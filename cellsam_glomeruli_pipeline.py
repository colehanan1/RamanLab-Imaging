#!/usr/bin/env python3
"""Minimal CellSAM glomeruli pipeline for TIFF frame trials.

Pipeline stages:
1. Probe runtime environment and sample TIFF metadata.
2. Build 5-frame max/std projections per window.
3. Run CellSAM on projections to generate candidate ROIs.
4. Save QC artifacts (projections, masks, overlays, review panels).
5. Attempt deterministic ROI tracking across windows with quality checks.
6. Fallback to fixed-ROI strategy when tracking is weak.
7. Extract per-ROI fluorescence traces over all frames.

This script intentionally treats CellSAM masks as exploratory candidate glomeruli.
"""

from __future__ import annotations

import argparse
import inspect
import json
import os
import platform
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import tifffile
from skimage import exposure
from skimage import measure
from skimage.filters import gaussian

try:
    import torch
except Exception:  # pragma: no cover - torch is optional for CPU-only fallback
    torch = None

from cellSAM import get_model, segment_cellular_image


@dataclass
class WindowResult:
    window_idx: int
    frame_indices: List[int]
    frame_names: List[str]
    proj_max: np.ndarray
    proj_std: np.ndarray
    raw_mask: np.ndarray
    filtered_mask: np.ndarray
    roi_ids: List[int]
    roi_areas: List[int]
    roi_centroids: List[Tuple[float, float]]


def log(msg: str) -> None:
    print(msg, flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="CellSAM glomeruli projection + trace pipeline")
    parser.add_argument("--input", required=True, help="Input folder containing frame_*.tif files")
    parser.add_argument(
        "--output-root",
        default="cellsam_glomeruli_outputs",
        help="Output folder root (default: ./cellsam_glomeruli_outputs)",
    )
    parser.add_argument("--window-size", type=int, default=5, help="Projection window size")
    parser.add_argument("--test-frames", type=int, default=0, help="Limit frames for dry-run testing")
    parser.add_argument("--device", default="auto", choices=["auto", "cpu", "cuda"], help="CellSAM device")
    parser.add_argument("--min-area", type=int, default=100, help="Min ROI area in pixels")
    parser.add_argument("--max-area", type=int, default=250000, help="Max ROI area in pixels")
    parser.add_argument(
        "--remove-border-touching",
        action="store_true",
        help="Remove ROIs that touch image border",
    )
    parser.add_argument("--tracking-iou-thresh", type=float, default=0.2, help="Min IoU for tracking match")
    parser.add_argument(
        "--tracking-max-centroid-dist",
        type=float,
        default=80.0,
        help="Max centroid distance (pixels) for tracking match",
    )
    parser.add_argument(
        "--std-weight",
        type=float,
        default=0.4,
        help="Blend weight for std projection in inference image",
    )
    return parser.parse_args()


def ensure_dirs(root: Path) -> Dict[str, Path]:
    paths = {
        "root": root,
        "projections": root / "projections",
        "projections_preprocessed": root / "projections_preprocessed",
        "masks_raw": root / "masks_raw",
        "masks_filtered": root / "masks_filtered",
        "qc": root / "qc",
        "comparisons": root / "comparisons",
        "traces": root / "traces",
        "reports": root / "reports",
    }
    for p in paths.values():
        p.mkdir(parents=True, exist_ok=True)
    return paths


def find_frames(input_dir: Path, test_frames: int) -> List[Path]:
    frames = sorted([p for p in input_dir.iterdir() if p.suffix.lower() in {".tif", ".tiff"}])
    if not frames:
        raise FileNotFoundError(f"No TIFF frames found in {input_dir}")
    if test_frames > 0:
        frames = frames[:test_frames]
    log(f"[INFO] Located {len(frames)} frame(s)")
    return frames


def preflight_probe(frames: List[Path], device_arg: str, reports_dir: Path) -> Tuple[dict, str]:
    log("[INFO] Running preflight probe")
    cuda_available = bool(torch and torch.cuda.is_available())
    resolved_device = "cpu"
    if device_arg == "cuda":
        resolved_device = "cuda"
    elif device_arg == "auto":
        resolved_device = "cuda" if cuda_available else "cpu"

    sample = frames[: min(5, len(frames))]
    sample_stats = []
    for f in sample:
        arr = tifffile.imread(f)
        stat = {
            "file": f.name,
            "shape": tuple(int(x) for x in arr.shape),
            "dtype": str(arr.dtype),
            "min": float(np.min(arr)),
            "max": float(np.max(arr)),
            "channels": "grayscale" if arr.ndim == 2 else "multichannel" if arr.ndim == 3 else f"ndim={arr.ndim}",
        }
        sample_stats.append(stat)

    sig = str(inspect.signature(segment_cellular_image))

    manifest = {
        "python_version": sys.version,
        "platform": platform.platform(),
        "torch_version": getattr(torch, "__version__", None),
        "cuda_available": cuda_available,
        "cellSAM_signature": sig,
        "cellSAM_module": str(segment_cellular_image.__module__),
        "resolved_device": resolved_device,
        "sample_tiff_stats": sample_stats,
        "projection_strategy": {
            "window_size": None,
            "max_projection": True,
            "std_projection": True,
            "inference_image": "normalized(max) + std_weight * normalized(std)",
        },
    }

    out = reports_dir / "preflight_manifest.json"
    out.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    log(f"[INFO] Wrote preflight manifest: {out}")
    return manifest, resolved_device


def normalize01(img: np.ndarray, p_lo: float = 1.0, p_hi: float = 99.5) -> np.ndarray:
    lo = np.percentile(img, p_lo)
    hi = np.percentile(img, p_hi)
    if hi <= lo:
        return np.zeros_like(img, dtype=np.float32)
    out = (img.astype(np.float32) - float(lo)) / float(hi - lo)
    return np.clip(out, 0.0, 1.0)


def preprocess_projection(img: np.ndarray) -> np.ndarray:
    # Deterministic preprocessing sequence required before CellSAM segmentation.
    img = img.astype(np.float32)

    p1, p99 = np.percentile(img, (1, 99))
    img = np.clip((img - p1) / (p99 - p1 + 1e-8), 0, 1)

    img = gaussian(img, sigma=1)

    background = gaussian(img, sigma=10)
    img = img - background
    img = np.clip(img, 0, None)

    img = exposure.equalize_adapthist(img, clip_limit=0.03)
    return img.astype(np.float32)


def to_uint8(img01: np.ndarray) -> np.ndarray:
    return (np.clip(img01, 0.0, 1.0) * 255.0).astype(np.uint8)


def window_slices(n: int, w: int) -> List[List[int]]:
    return [list(range(i, min(i + w, n))) for i in range(0, n, w)]


def deterministic_relabel(mask: np.ndarray) -> np.ndarray:
    lab = measure.label(mask > 0, connectivity=1)
    props = measure.regionprops(lab)
    order = sorted(props, key=lambda p: (p.centroid[0], p.centroid[1], p.area))
    out = np.zeros_like(lab, dtype=np.int32)
    for idx, p in enumerate(order, start=1):
        out[lab == p.label] = idx
    return out


def filter_rois(mask: np.ndarray, min_area: int, max_area: int, remove_border: bool) -> np.ndarray:
    lab = deterministic_relabel(mask)
    keep = np.zeros_like(lab, dtype=np.int32)
    h, w = lab.shape
    next_id = 1
    for p in measure.regionprops(lab):
        area = int(p.area)
        if area < min_area or area > max_area:
            continue
        if remove_border:
            rr, cc = p.coords[:, 0], p.coords[:, 1]
            touches = np.any(rr == 0) or np.any(rr == h - 1) or np.any(cc == 0) or np.any(cc == w - 1)
            if touches:
                continue
        keep[lab == p.label] = next_id
        next_id += 1
    return keep


def projection_for_window(frame_paths: List[Path], idxs: List[int]) -> Tuple[np.ndarray, np.ndarray]:
    stack = []
    for i in idxs:
        arr = tifffile.imread(frame_paths[i])
        if arr.ndim != 2:
            raise ValueError(f"Expected 2D grayscale TIFF, got shape={arr.shape} for {frame_paths[i]}")
        stack.append(arr.astype(np.float32))
    data = np.stack(stack, axis=0)
    return np.max(data, axis=0), np.std(data, axis=0)


def run_cellsam_on_projection(infer_img01: np.ndarray, alt_img01: np.ndarray, model, device: str):
    attempts = [
        {"img": infer_img01, "normalize": True, "postprocess": False, "bbox_threshold": 0.4, "tag": "infer_norm_t040"},
        {"img": infer_img01, "normalize": True, "postprocess": True, "bbox_threshold": 0.2, "tag": "infer_post_t020"},
        {"img": alt_img01, "normalize": False, "postprocess": False, "bbox_threshold": 0.05, "tag": "alt_pre_nonorm_t005"},
    ]
    for spec in attempts:
        try:
            mask, flows, styles = segment_cellular_image(
                spec["img"],
                model=model,
                normalize=spec["normalize"],
                postprocess=spec["postprocess"],
                remove_boundaries=False,
                bbox_threshold=spec["bbox_threshold"],
                device=device,
            )
            _ = (flows, styles)
            if mask is None:
                log(f"[WARN] CellSAM attempt {spec['tag']} returned None mask")
                continue
            if mask.ndim != 2:
                raise RuntimeError(f"CellSAM returned unexpected mask shape: {mask.shape}")
            nz = int((mask > 0).sum())
            log(f"[INFO] CellSAM attempt {spec['tag']} nonzero pixels: {nz}")
            if nz > 0:
                return mask
        except AttributeError as exc:
            if "NoneType" in str(exc) and "ndim" in str(exc):
                log(f"[WARN] CellSAM attempt {spec['tag']} produced empty internal predictions")
                continue
            raise
        except ValueError as exc:
            if "zero-size array to reduction operation maximum" in str(exc):
                log(f"[WARN] CellSAM attempt {spec['tag']} had zero-size postprocess result")
                continue
            raise
        except ModuleNotFoundError as exc:
            if "cv2" in str(exc):
                log(f"[WARN] CellSAM attempt {spec['tag']} requires cv2; skipping this attempt")
                continue
            raise
    log("[WARN] All CellSAM attempts empty for this window; using empty mask")
    return np.zeros(infer_img01.shape[:2], dtype=np.int32)


def save_window_artifacts(
    res: WindowResult,
    paths: Dict[str, Path],
    raw_infer_img01: np.ndarray,
    preprocessed_infer_img01: np.ndarray,
) -> None:
    w = res.window_idx
    stem = f"window_{w:04d}"

    np.save(paths["projections"] / f"{stem}_max.npy", res.proj_max)
    np.save(paths["projections"] / f"{stem}_std.npy", res.proj_std)
    tifffile.imwrite(paths["projections"] / f"{stem}_max.tif", res.proj_max.astype(np.float32))
    tifffile.imwrite(paths["projections"] / f"{stem}_std.tif", res.proj_std.astype(np.float32))
    np.save(paths["projections_preprocessed"] / f"{stem}_inference_preprocessed.npy", preprocessed_infer_img01.astype(np.float32))
    tifffile.imwrite(
        paths["projections_preprocessed"] / f"{stem}_inference_preprocessed.tif",
        preprocessed_infer_img01.astype(np.float32),
    )

    comp_fig = plt.figure(figsize=(10, 4))
    comp_ax1 = comp_fig.add_subplot(1, 2, 1)
    comp_ax2 = comp_fig.add_subplot(1, 2, 2)
    comp_ax1.imshow(raw_infer_img01, cmap="gray")
    comp_ax1.set_title("Raw Inference Projection")
    comp_ax1.axis("off")
    comp_ax2.imshow(preprocessed_infer_img01, cmap="gray")
    comp_ax2.set_title("Preprocessed Projection")
    comp_ax2.axis("off")
    comp_fig.tight_layout()
    comp_fig.savefig(paths["comparisons"] / f"{stem}_raw_vs_preprocessed.png", dpi=150)
    plt.close(comp_fig)

    np.save(paths["masks_raw"] / f"{stem}_mask_raw.npy", res.raw_mask.astype(np.int32))
    np.save(paths["masks_filtered"] / f"{stem}_mask_filtered.npy", res.filtered_mask.astype(np.int32))
    tifffile.imwrite(paths["masks_raw"] / f"{stem}_mask_raw.tif", res.raw_mask.astype(np.int32))
    tifffile.imwrite(paths["masks_filtered"] / f"{stem}_mask_filtered.tif", res.filtered_mask.astype(np.int32))

    fig = plt.figure(figsize=(16, 5))
    ax1 = fig.add_subplot(1, 3, 1)
    ax2 = fig.add_subplot(1, 3, 2)
    ax3 = fig.add_subplot(1, 3, 3)

    ax1.imshow(preprocessed_infer_img01, cmap="gray")
    ax1.set_title("Inference Projection (Preprocessed)")
    ax1.axis("off")

    ax2.imshow(res.filtered_mask, cmap="nipy_spectral")
    ax2.set_title("Filtered ROI Labels")
    ax2.axis("off")

    ax3.imshow(preprocessed_infer_img01, cmap="gray")
    ax3.imshow(np.ma.masked_where(res.filtered_mask == 0, res.filtered_mask), cmap="nipy_spectral", alpha=0.5)
    ax3.set_title("Overlay + ROI IDs")
    ax3.axis("off")

    for rid, (cy, cx) in zip(res.roi_ids, res.roi_centroids):
        ax3.text(cx, cy, str(rid), color="white", fontsize=8, ha="center", va="center")

    fig.tight_layout()
    panel_path = paths["qc"] / f"{stem}_review_panel.png"
    fig.savefig(panel_path, dpi=150)
    plt.close(fig)

    overlay = plt.figure(figsize=(6, 6))
    ax = overlay.add_subplot(1, 1, 1)
    ax.imshow(preprocessed_infer_img01, cmap="gray")
    ax.imshow(np.ma.masked_where(res.filtered_mask == 0, res.filtered_mask), cmap="nipy_spectral", alpha=0.5)
    ax.axis("off")
    overlay_path = paths["qc"] / f"{stem}_overlay.png"
    overlay.savefig(overlay_path, dpi=150, bbox_inches="tight")
    plt.close(overlay)


def save_zero_roi_debug_image(raw_img01: np.ndarray, pre_img01: np.ndarray, out_path: Path, title: str) -> None:
    fig = plt.figure(figsize=(12, 4))
    ax1 = fig.add_subplot(1, 3, 1)
    ax2 = fig.add_subplot(1, 3, 2)
    ax3 = fig.add_subplot(1, 3, 3)

    ax1.imshow(raw_img01, cmap="gray")
    ax1.set_title("Raw Projection")
    ax1.axis("off")

    ax2.imshow(pre_img01, cmap="gray")
    ax2.set_title("Preprocessed Projection")
    ax2.axis("off")

    diff = np.clip(pre_img01 - raw_img01, 0.0, 1.0)
    ax3.imshow(diff, cmap="magma")
    ax3.set_title("Preprocessed - Raw")
    ax3.axis("off")

    fig.suptitle(title)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)



def iou(mask_a: np.ndarray, mask_b: np.ndarray) -> float:
    inter = np.logical_and(mask_a, mask_b).sum()
    union = np.logical_or(mask_a, mask_b).sum()
    return float(inter) / float(union) if union > 0 else 0.0


def build_tracking(results: List[WindowResult], iou_thr: float, max_dist: float) -> Tuple[dict, dict]:
    log("[INFO] Attempting deterministic ROI tracking across windows")
    global_masks: Dict[int, np.ndarray] = {}
    global_last_centroid: Dict[int, Tuple[float, float]] = {}
    window_global_map: Dict[int, Dict[int, int]] = {}
    next_gid = 1
    total_local = 0
    matched = 0

    for res in results:
        local_map: Dict[int, int] = {}
        used_global: set[int] = set()
        props = {p.label: p for p in measure.regionprops(res.filtered_mask)}
        ordered_locals = sorted(props.values(), key=lambda p: (p.centroid[0], p.centroid[1]))
        for p in ordered_locals:
            lid = int(p.label)
            total_local += 1
            lmask = res.filtered_mask == lid
            cy, cx = p.centroid
            candidates = []
            for gid, gmask in global_masks.items():
                if gid in used_global:
                    continue
                gcy, gcx = global_last_centroid[gid]
                dist = float(np.hypot(cy - gcy, cx - gcx))
                ov = iou(lmask, gmask)
                if ov >= iou_thr and dist <= max_dist:
                    candidates.append((ov, -dist, gid))
            if candidates:
                candidates.sort(reverse=True)
                gid = candidates[0][2]
                matched += 1
            else:
                gid = next_gid
                next_gid += 1
            local_map[lid] = gid
            used_global.add(gid)
            if gid not in global_masks:
                global_masks[gid] = lmask.copy()
            else:
                # Conservative update: keep union to improve coverage across windows.
                global_masks[gid] = np.logical_or(global_masks[gid], lmask)
            global_last_centroid[gid] = (cy, cx)
        window_global_map[res.window_idx] = local_map

    metrics = {
        "total_local_rois": total_local,
        "matched_rois": matched,
        "match_rate": float(matched) / float(total_local) if total_local else 0.0,
        "global_roi_count": len(global_masks),
        "window_count": len(results),
    }
    log(f"[INFO] Tracking metrics: {metrics}")
    return {"global_masks": global_masks, "window_global_map": window_global_map}, metrics


def choose_best_window(results: List[WindowResult]) -> WindowResult:
    scored = []
    for res in results:
        n = len(res.roi_ids)
        total_area = float(sum(res.roi_areas))
        score = n * 1000000.0 + total_area
        scored.append((score, -res.window_idx, res))
    scored.sort(reverse=True)
    return scored[0][2]


def global_projection(frames: List[Path]) -> Tuple[np.ndarray, np.ndarray]:
    stack = []
    for fp in frames:
        arr = tifffile.imread(fp)
        if arr.ndim != 2:
            raise ValueError(f"Expected 2D grayscale TIFF, got shape={arr.shape} for {fp}")
        stack.append(arr.astype(np.float32))
    data = np.stack(stack, axis=0)
    return np.max(data, axis=0), np.std(data, axis=0)


def build_final_roi_mask(results: List[WindowResult], tracking: dict, metrics: dict) -> Tuple[np.ndarray, str]:
    if not results:
        raise RuntimeError("No window results available")

    use_tracking = metrics["global_roi_count"] > 0 and metrics["match_rate"] >= 0.35 and metrics["global_roi_count"] <= 4 * max(1, len(results))

    h, w = results[0].filtered_mask.shape
    final = np.zeros((h, w), dtype=np.int32)

    if use_tracking:
        log("[INFO] Tracking quality acceptable; using tracked ROI union mask")
        gids = sorted(tracking["global_masks"].keys())
        claimed = np.zeros((h, w), dtype=bool)
        new_id = 1
        for gid in gids:
            m = tracking["global_masks"][gid].astype(bool)
            m = np.logical_and(m, ~claimed)
            if m.sum() == 0:
                continue
            final[m] = new_id
            claimed[m] = True
            new_id += 1
        strategy = "tracking_union"
    else:
        best = choose_best_window(results)
        final = deterministic_relabel(best.filtered_mask)
        strategy = f"fallback_fixed_window_{best.window_idx:04d}"
        log(
            "[WARN] Tracking quality weak. Falling back to fixed ROI set from best projection window "
            f"{best.window_idx:04d}"
        )

    if int((final > 0).sum()) == 0:
        raise RuntimeError("Final ROI mask is empty after strategy selection")
    return final, strategy


def extract_traces(frame_paths: List[Path], roi_mask: np.ndarray) -> Tuple[pd.DataFrame, pd.DataFrame]:
    roi_ids = [int(x) for x in np.unique(roi_mask) if x > 0]
    if not roi_ids:
        raise RuntimeError("No ROI labels available for trace extraction")

    records = []
    bg_mask = roi_mask == 0

    for fi, fp in enumerate(frame_paths):
        arr = tifffile.imread(fp).astype(np.float32)
        if arr.ndim != 2:
            raise ValueError(f"Expected 2D frame at extraction, got {arr.shape} for {fp}")
        bg_mean = float(arr[bg_mask].mean()) if bg_mask.any() else np.nan
        for rid in roi_ids:
            m = roi_mask == rid
            mean_int = float(arr[m].mean())
            records.append(
                {
                    "frame_idx": fi,
                    "frame_name": fp.name,
                    "roi_id": rid,
                    "mean_intensity": mean_int,
                    "bg_mean": bg_mean,
                    "bg_subtracted_mean": mean_int - bg_mean if np.isfinite(bg_mean) else np.nan,
                }
            )

    df = pd.DataFrame.from_records(records)
    summary = (
        df.groupby("roi_id", as_index=False)
        .agg(
            n_frames=("frame_idx", "count"),
            mean_intensity_mean=("mean_intensity", "mean"),
            mean_intensity_std=("mean_intensity", "std"),
            bg_subtracted_mean=("bg_subtracted_mean", "mean"),
        )
        .sort_values("roi_id")
    )
    return df, summary


def save_trace_qc(df: pd.DataFrame, out_png: Path) -> None:
    pivot = df.pivot(index="frame_idx", columns="roi_id", values="mean_intensity")
    plt.figure(figsize=(12, 5))
    for rid in pivot.columns:
        plt.plot(pivot.index, pivot[rid], lw=1.0, label=f"ROI {rid}")
    plt.xlabel("Frame Index")
    plt.ylabel("Mean Intensity")
    plt.title("Per-ROI Fluorescence Traces")
    if len(pivot.columns) <= 20:
        plt.legend(loc="upper right", fontsize=7, ncol=2)
    plt.tight_layout()
    plt.savefig(out_png, dpi=150)
    plt.close()



def main() -> int:
    args = parse_args()
    input_dir = Path(args.input).resolve()
    output_root = Path(args.output_root).resolve()

    if not input_dir.exists():
        raise FileNotFoundError(f"Input folder does not exist: {input_dir}")

    paths = ensure_dirs(output_root)
    frames = find_frames(input_dir, args.test_frames)

    manifest, device = preflight_probe(frames, args.device, paths["reports"])
    manifest["projection_strategy"]["window_size"] = args.window_size

    windows = window_slices(len(frames), args.window_size)
    log(f"[INFO] Processing {len(windows)} projection window(s), window_size={args.window_size}")

    log("[INFO] Loading CellSAM model")
    model = get_model(model="cellsam_general")

    results: List[WindowResult] = []
    per_window_rows = []

    for wi, idxs in enumerate(windows):
        log(f"[INFO] Window {wi:04d} frame indices: {idxs}")
        pmax, pstd = projection_for_window(frames, idxs)
        nmax = normalize01(pmax)
        nstd = normalize01(pstd)
        infer_raw = np.clip(nmax + args.std_weight * nstd, 0.0, 1.0)

        pmax_pre = preprocess_projection(pmax)
        pstd_pre = preprocess_projection(pstd)
        infer = np.clip(pmax_pre + args.std_weight * pstd_pre, 0.0, 1.0).astype(np.float32)

        raw_mask = run_cellsam_on_projection(infer, alt_img01=pmax_pre, model=model, device=device)
        raw_mask = deterministic_relabel(raw_mask)
        filt = filter_rois(
            raw_mask,
            min_area=args.min_area,
            max_area=args.max_area,
            remove_border=args.remove_border_touching,
        )

        props = measure.regionprops(filt)
        roi_ids = [int(p.label) for p in props]
        roi_areas = [int(p.area) for p in props]
        roi_centroids = [(float(p.centroid[0]), float(p.centroid[1])) for p in props]

        res = WindowResult(
            window_idx=wi,
            frame_indices=idxs,
            frame_names=[frames[i].name for i in idxs],
            proj_max=pmax,
            proj_std=pstd,
            raw_mask=raw_mask,
            filtered_mask=filt,
            roi_ids=roi_ids,
            roi_areas=roi_areas,
            roi_centroids=roi_centroids,
        )
        save_window_artifacts(res, paths, infer_raw, infer)
        results.append(res)

        per_window_rows.append(
            {
                "window_idx": wi,
                "start_frame_idx": idxs[0],
                "end_frame_idx": idxs[-1],
                "n_raw_roi": int(np.max(raw_mask)),
                "n_filtered_roi": len(roi_ids),
                "roi_area_sum": int(sum(roi_areas)),
            }
        )

    win_df = pd.DataFrame(per_window_rows)
    win_df.to_csv(paths["reports"] / "window_roi_stats.csv", index=False)

    non_empty_windows = int((win_df["n_filtered_roi"] > 0).sum())
    if non_empty_windows == 0:
        log("[WARN] No non-empty per-window masks. Trying global projection CellSAM fallback.")
        gmax, gstd = global_projection(frames)
        gnmax = normalize01(gmax)
        gnstd = normalize01(gstd)
        ginfer_raw = np.clip(gnmax + args.std_weight * gnstd, 0.0, 1.0)
        gmax_pre = preprocess_projection(gmax)
        gstd_pre = preprocess_projection(gstd)
        ginfer = np.clip(gmax_pre + args.std_weight * gstd_pre, 0.0, 1.0).astype(np.float32)
        graw = run_cellsam_on_projection(ginfer, alt_img01=gmax_pre, model=model, device=device)
        gfilt = filter_rois(
            deterministic_relabel(graw),
            min_area=args.min_area,
            max_area=args.max_area,
            remove_border=args.remove_border_touching,
        )
        gprops = measure.regionprops(gfilt)
        gres = WindowResult(
            window_idx=9999,
            frame_indices=list(range(len(frames))),
            frame_names=[f.name for f in frames],
            proj_max=gmax,
            proj_std=gstd,
            raw_mask=deterministic_relabel(graw),
            filtered_mask=gfilt,
            roi_ids=[int(p.label) for p in gprops],
            roi_areas=[int(p.area) for p in gprops],
            roi_centroids=[(float(p.centroid[0]), float(p.centroid[1])) for p in gprops],
        )
        save_window_artifacts(gres, paths, ginfer_raw, ginfer)
        results.append(gres)
        per_window_rows.append(
            {
                "window_idx": 9999,
                "start_frame_idx": 0,
                "end_frame_idx": len(frames) - 1,
                "n_raw_roi": int(np.max(gres.raw_mask)),
                "n_filtered_roi": int(np.max(gres.filtered_mask)),
                "roi_area_sum": int(sum(gres.roi_areas)),
            }
        )
        win_df = pd.DataFrame(per_window_rows)
        win_df.to_csv(paths["reports"] / "window_roi_stats.csv", index=False)
        non_empty_windows = int((win_df["n_filtered_roi"] > 0).sum())
        if non_empty_windows == 0:
            debug_path = paths["qc"] / "zero_roi_debug_global.png"
            save_zero_roi_debug_image(ginfer_raw, ginfer, debug_path, "Zero ROI debug: global projection")
            raise RuntimeError(
                "No non-empty ROI masks found in window or global projection CellSAM runs. "
                f"Diagnostics saved under: {paths['root']} (debug image: {debug_path})"
            )

    tracking, tracking_metrics = build_tracking(
        results,
        iou_thr=args.tracking_iou_thresh,
        max_dist=args.tracking_max_centroid_dist,
    )
    final_roi_mask, strategy = build_final_roi_mask(results, tracking, tracking_metrics)
    final_roi_mask = deterministic_relabel(final_roi_mask)

    np.save(paths["masks_filtered"] / "final_roi_mask.npy", final_roi_mask)
    tifffile.imwrite(paths["masks_filtered"] / "final_roi_mask.tif", final_roi_mask.astype(np.int32))

    traces_df, summary_df = extract_traces(frames, final_roi_mask)
    traces_csv = paths["traces"] / "roi_traces.csv"
    summary_csv = paths["traces"] / "roi_summary.csv"
    traces_df.to_csv(traces_csv, index=False)
    summary_df.to_csv(summary_csv, index=False)
    save_trace_qc(traces_df, paths["traces"] / "roi_traces_qc.png")

    report = {
        "input_dir": str(input_dir),
        "output_root": str(output_root),
        "n_frames": len(frames),
        "window_size": args.window_size,
        "n_windows": len(windows),
        "non_empty_windows": non_empty_windows,
        "device": device,
        "tracking_metrics": tracking_metrics,
        "final_strategy": strategy,
        "n_final_rois": int(np.max(final_roi_mask)),
        "trace_rows": int(len(traces_df)),
        "trace_columns": list(traces_df.columns),
        "artifacts": {
            "window_stats_csv": str(paths["reports"] / "window_roi_stats.csv"),
            "final_roi_mask_npy": str(paths["masks_filtered"] / "final_roi_mask.npy"),
            "final_roi_mask_tif": str(paths["masks_filtered"] / "final_roi_mask.tif"),
            "roi_traces_csv": str(traces_csv),
            "roi_summary_csv": str(summary_csv),
            "trace_qc_png": str(paths["traces"] / "roi_traces_qc.png"),
        },
    }
    report_path = paths["reports"] / "run_report.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    log("[INFO] Pipeline complete")
    log(f"[INFO] Output root: {output_root}")
    log(f"[INFO] Final ROI count: {int(np.max(final_roi_mask))}")
    log(f"[INFO] Frame count: {len(frames)}")
    log(f"[INFO] roi_traces.csv rows: {len(traces_df)}")
    log(f"[INFO] Report: {report_path}")

    if traces_df["mean_intensity"].isna().any():
        raise RuntimeError("Trace extraction produced NaN mean_intensity values")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr, flush=True)
        raise
