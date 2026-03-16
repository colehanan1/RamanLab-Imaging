"""
Prototype activity-based glomerulus ROI suggestion tool.

Option 1 workflow:
1. Compute per-trial ΔF/F stacks using pre-odor baseline frames.
2. Average ΔF/F during odor-on frames to get an odor response map.
3. Optionally aggregate response maps across trials with the same odor.
4. Smooth + threshold the map.
5. Extract contiguous connected components as candidate ROIs.
6. Save preview images plus ROI polygons in the same JSON format used by
   glomerulus_id.save_rois()/load_rois().

This is intended as a *candidate ROI generator*, not a final segmentation
solution.  The output should be reviewed/edited in Napari/Fiji.
"""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import cv2
import numpy as np
import tifffile

from .dff import apply_dff, compute_f0
from .discovery import discover_experiment
from .projections import determine_epochs
from .timing import TimingSettings, extract_odor_timing

logger = logging.getLogger(__name__)


@dataclass
class CandidateRoi:
    name: str
    polygon: list[list[float]]
    area_px: int
    centroid_yx: tuple[float, float]
    mean_response: float
    peak_response: float
    circularity: float
    bbox_yxhw: tuple[int, int, int, int]

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "polygon": self.polygon,
            "area_px": self.area_px,
            "centroid_yx": [float(self.centroid_yx[0]), float(self.centroid_yx[1])],
            "mean_response": float(self.mean_response),
            "peak_response": float(self.peak_response),
            "circularity": float(self.circularity),
            "bbox_yxhw": [int(v) for v in self.bbox_yxhw],
        }


@dataclass
class TrialResponse:
    trial_name: str
    odor_name: str
    odor_code: str
    response_map: np.ndarray
    baseline_mean: np.ndarray
    odor_mean: np.ndarray
    dff_odor_mean: np.ndarray
    epochs: dict


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_trial_frames(images_dir: Path) -> np.ndarray:
    files = sorted(images_dir.glob("frame_*.tif"))
    if not files:
        raise FileNotFoundError(f"No frame_*.tif files found in {images_dir}")
    return tifffile.imread([str(f) for f in files]).astype(np.float32)


def _scale_to_u8(img: np.ndarray, p_lo: float = 1.0, p_hi: float = 99.0) -> np.ndarray:
    img = img.astype(np.float32)
    vmin = float(np.percentile(img, p_lo))
    vmax = float(np.percentile(img, p_hi))
    if vmax <= vmin:
        vmin = float(np.min(img))
        vmax = float(np.max(img))
    if vmax <= vmin:
        return np.zeros_like(img, dtype=np.uint8)
    out = (img - vmin) / (vmax - vmin)
    out = np.clip(out, 0, 1)
    return (out * 255).astype(np.uint8)


def _overlay_mask_outlines(base_gray: np.ndarray, mask: np.ndarray) -> np.ndarray:
    base_u8 = _scale_to_u8(base_gray)
    rgb = cv2.cvtColor(base_u8, cv2.COLOR_GRAY2BGR)
    contours, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cv2.drawContours(rgb, contours, -1, (0, 255, 255), 1)
    return rgb


def _save_json(data: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


# ---------------------------------------------------------------------------
# Core computations
# ---------------------------------------------------------------------------

def compute_trial_response(trial_dir: Path) -> TrialResponse:
    """Compute odor-evoked response map for one trial."""
    trial_dir = Path(trial_dir)
    images_dir = trial_dir / "images" / "images"
    json_path = trial_dir / "trial.json"
    csv_path = trial_dir / "timestamps.csv"

    frames = _load_trial_frames(images_dir)
    n_frames = frames.shape[0]

    timing = extract_odor_timing(
        n_frames=n_frames,
        csv_path=csv_path if csv_path.exists() else None,
        json_path=json_path if json_path.exists() else None,
        settings=TimingSettings(),
    )
    epochs = determine_epochs(timing.intervals, n_frames)

    f0 = compute_f0(frames, epochs.baseline, method="mean")
    dff = apply_dff(frames, f0, eps=1e-6, clip=None)

    baseline_mean = frames[epochs.baseline].mean(axis=0).astype(np.float32)
    odor_mean = frames[epochs.odor].mean(axis=0).astype(np.float32)
    dff_odor_mean = dff[epochs.odor].mean(axis=0).astype(np.float32)

    with open(json_path, "r") as f:
        meta = json.load(f)

    odor_name = meta.get("odor_name") or trial_dir.name
    odor_code = meta.get("odor_code") or trial_dir.name

    return TrialResponse(
        trial_name=trial_dir.name,
        odor_name=odor_name,
        odor_code=odor_code,
        response_map=dff_odor_mean,
        baseline_mean=baseline_mean,
        odor_mean=odor_mean,
        dff_odor_mean=dff_odor_mean,
        epochs={
            "baseline": [int(epochs.baseline[0]), int(epochs.baseline[-1])],
            "odor": [int(epochs.odor[0]), int(epochs.odor[-1])],
            "post": [int(epochs.post[0]), int(epochs.post[-1])],
        },
    )



def segment_response_map(
    response_map: np.ndarray,
    *,
    blur_sigma: float = 1.5,
    threshold_percentile: float = 97.0,
    min_area_px: int = 25,
    max_area_px: Optional[int] = None,
    min_circularity: float = 0.0,
) -> tuple[np.ndarray, list[CandidateRoi], dict]:
    """Threshold contiguous hotspots from a response map."""
    resp = response_map.astype(np.float32)
    blurred = cv2.GaussianBlur(resp, ksize=(0, 0), sigmaX=blur_sigma, sigmaY=blur_sigma)

    positive = blurred[np.isfinite(blurred)]
    positive = positive[positive > 0]
    if positive.size == 0:
        threshold = float(np.max(blurred)) + 1.0
    else:
        threshold = float(np.percentile(positive, threshold_percentile))

    binary = blurred >= threshold
    binary_u8 = binary.astype(np.uint8)

    # Clean tiny speckles and tiny holes.
    kernel = np.ones((3, 3), np.uint8)
    binary_u8 = cv2.morphologyEx(binary_u8, cv2.MORPH_OPEN, kernel)
    binary_u8 = cv2.morphologyEx(binary_u8, cv2.MORPH_CLOSE, kernel)

    n_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(binary_u8, connectivity=8)

    candidates: list[CandidateRoi] = []
    kept_mask = np.zeros_like(binary_u8)

    for label_idx in range(1, n_labels):
        x = int(stats[label_idx, cv2.CC_STAT_LEFT])
        y = int(stats[label_idx, cv2.CC_STAT_TOP])
        w = int(stats[label_idx, cv2.CC_STAT_WIDTH])
        h = int(stats[label_idx, cv2.CC_STAT_HEIGHT])
        area = int(stats[label_idx, cv2.CC_STAT_AREA])

        if area < min_area_px:
            continue
        if max_area_px is not None and area > max_area_px:
            continue

        component = (labels == label_idx).astype(np.uint8)
        contours, _ = cv2.findContours(component, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            continue
        contour = max(contours, key=cv2.contourArea)
        perimeter = float(cv2.arcLength(contour, True))
        contour_area = float(cv2.contourArea(contour))
        circularity = 0.0 if perimeter <= 0 else float(4.0 * np.pi * contour_area / (perimeter * perimeter))
        if circularity < min_circularity:
            continue

        if len(contour) < 3:
            continue

        polygon_xy = contour[:, 0, :].astype(float)
        polygon_yx = [[float(yx[1]), float(yx[0])] for yx in polygon_xy]

        response_vals = response_map[component.astype(bool)]
        cy, cx = centroids[label_idx][1], centroids[label_idx][0]
        kept_mask[component.astype(bool)] = 1

        candidates.append(
            CandidateRoi(
                name=f"auto_{len(candidates)+1:03d}",
                polygon=polygon_yx,
                area_px=area,
                centroid_yx=(float(cy), float(cx)),
                mean_response=float(np.mean(response_vals)),
                peak_response=float(np.max(response_vals)),
                circularity=circularity,
                bbox_yxhw=(y, x, h, w),
            )
        )

    meta = {
        "blur_sigma": float(blur_sigma),
        "threshold_percentile": float(threshold_percentile),
        "threshold_value": float(threshold),
        "min_area_px": int(min_area_px),
        "max_area_px": None if max_area_px is None else int(max_area_px),
        "min_circularity": float(min_circularity),
        "n_candidates": len(candidates),
    }
    return kept_mask.astype(bool), candidates, meta


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def _save_trial_outputs(trial_result: TrialResponse, out_dir: Path, mask: np.ndarray, candidates: list[CandidateRoi], seg_meta: dict) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    tifffile.imwrite(str(out_dir / "response_map_dff_mean.tif"), trial_result.response_map.astype(np.float32))
    tifffile.imwrite(str(out_dir / "baseline_mean.tif"), trial_result.baseline_mean.astype(np.float32))
    tifffile.imwrite(str(out_dir / "odor_mean.tif"), trial_result.odor_mean.astype(np.float32))
    tifffile.imwrite(str(out_dir / "candidate_mask.tif"), mask.astype(np.uint8))

    cv2.imwrite(str(out_dir / "response_map_dff_mean.png"), _scale_to_u8(trial_result.response_map))
    cv2.imwrite(str(out_dir / "baseline_mean.png"), _scale_to_u8(trial_result.baseline_mean))
    cv2.imwrite(str(out_dir / "odor_mean.png"), _scale_to_u8(trial_result.odor_mean))
    cv2.imwrite(str(out_dir / "candidate_overlay.png"), _overlay_mask_outlines(trial_result.baseline_mean, mask))

    rois_json = {
        c.name: {"polygon": c.polygon}
        for c in candidates
    }
    _save_json(rois_json, out_dir / "candidate_rois.json")
    _save_json(
        {
            "trial_name": trial_result.trial_name,
            "odor_name": trial_result.odor_name,
            "odor_code": trial_result.odor_code,
            "epochs": trial_result.epochs,
            "segmentation": seg_meta,
            "candidates": [c.to_dict() for c in candidates],
        },
        out_dir / "candidate_rois_report.json",
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def run_activity_roi_suggester(
    experiment_dir: Path,
    output_dir: Path,
    *,
    per_odor: bool = False,
    blur_sigma: float = 1.5,
    threshold_percentile: float = 97.0,
    min_area_px: int = 25,
    max_area_px: Optional[int] = None,
    min_circularity: float = 0.0,
) -> None:
    experiment = discover_experiment(Path(experiment_dir))
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    trial_results: list[TrialResponse] = []
    for trial in experiment.trials:
        logger.info("Computing response map for %s", trial.trial_dir)
        trial_results.append(compute_trial_response(trial.trial_dir))

    summary = {
        "experiment_dir": str(experiment_dir),
        "output_dir": str(output_dir),
        "per_odor": bool(per_odor),
        "n_trials": len(trial_results),
        "segmentation_params": {
            "blur_sigma": blur_sigma,
            "threshold_percentile": threshold_percentile,
            "min_area_px": min_area_px,
            "max_area_px": max_area_px,
            "min_circularity": min_circularity,
        },
        "outputs": [],
    }

    if per_odor:
        groups: Dict[str, list[TrialResponse]] = {}
        for tr in trial_results:
            groups.setdefault(tr.odor_code, []).append(tr)

        for odor_code, rows in groups.items():
            mean_resp = np.mean([r.response_map for r in rows], axis=0).astype(np.float32)
            mean_base = np.mean([r.baseline_mean for r in rows], axis=0).astype(np.float32)
            mean_odor = np.mean([r.odor_mean for r in rows], axis=0).astype(np.float32)
            representative = rows[0]
            merged = TrialResponse(
                trial_name=f"odor_{odor_code}",
                odor_name=representative.odor_name,
                odor_code=odor_code,
                response_map=mean_resp,
                baseline_mean=mean_base,
                odor_mean=mean_odor,
                dff_odor_mean=mean_resp,
                epochs={"aggregated_from_trials": [r.trial_name for r in rows]},
            )
            mask, candidates, seg_meta = segment_response_map(
                merged.response_map,
                blur_sigma=blur_sigma,
                threshold_percentile=threshold_percentile,
                min_area_px=min_area_px,
                max_area_px=max_area_px,
                min_circularity=min_circularity,
            )
            out_dir = output_dir / f"odor_{odor_code}"
            _save_trial_outputs(merged, out_dir, mask, candidates, seg_meta)
            summary["outputs"].append({
                "kind": "odor_group",
                "odor_code": odor_code,
                "odor_name": representative.odor_name,
                "n_trials": len(rows),
                "output_dir": str(out_dir),
                "n_candidates": len(candidates),
            })
    else:
        for tr in trial_results:
            mask, candidates, seg_meta = segment_response_map(
                tr.response_map,
                blur_sigma=blur_sigma,
                threshold_percentile=threshold_percentile,
                min_area_px=min_area_px,
                max_area_px=max_area_px,
                min_circularity=min_circularity,
            )
            out_dir = output_dir / tr.trial_name
            _save_trial_outputs(tr, out_dir, mask, candidates, seg_meta)
            summary["outputs"].append({
                "kind": "trial",
                "trial_name": tr.trial_name,
                "odor_code": tr.odor_code,
                "odor_name": tr.odor_name,
                "output_dir": str(out_dir),
                "n_candidates": len(candidates),
            })

    _save_json(summary, output_dir / "activity_roi_summary.json")
    logger.info("Saved summary to %s", output_dir / "activity_roi_summary.json")



def main() -> None:
    parser = argparse.ArgumentParser(description="Suggest glomerulus ROIs from odor-evoked ΔF/F hotspots")
    parser.add_argument("--experiment-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument(
        "--per-odor",
        action="store_true",
        help="Average trials with the same odor code before segmentation",
    )
    parser.add_argument("--blur-sigma", type=float, default=1.5)
    parser.add_argument("--threshold-percentile", type=float, default=97.0)
    parser.add_argument("--min-area-px", type=int, default=25)
    parser.add_argument("--max-area-px", type=int, default=None)
    parser.add_argument("--min-circularity", type=float, default=0.0)
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args()

    logging.basicConfig(level=getattr(logging, args.log_level), format="%(levelname)s: %(message)s")

    run_activity_roi_suggester(
        experiment_dir=args.experiment_dir,
        output_dir=args.output_dir,
        per_odor=args.per_odor,
        blur_sigma=args.blur_sigma,
        threshold_percentile=args.threshold_percentile,
        min_area_px=args.min_area_px,
        max_area_px=args.max_area_px,
        min_circularity=args.min_circularity,
    )


if __name__ == "__main__":
    main()
