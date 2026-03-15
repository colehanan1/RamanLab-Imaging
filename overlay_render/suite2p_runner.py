"""
Suite2p integration for automated ROI detection and signal extraction.

Wraps suite2p for use with the RamanLab imaging data format.
Provides motion correction, ROI detection, neuropil subtraction, and ΔF/F.

Suite2p reference: https://github.com/MouseLand/suite2p
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from typing import Optional

import numpy as np


def _load_fps_from_trial(trial_dir: Path) -> float:
    """Read fps from trial.json, falling back to 9.0."""
    trial_json = trial_dir / "trial.json"
    if trial_json.exists():
        with open(trial_json, "r") as f:
            meta = json.load(f)
        return float(meta.get("fps", 9.0))
    return 9.0


def _load_frame_stack(trial_dir: Path) -> np.ndarray:
    """Load all frames from trial_dir/images/images/ into a (T, H, W) uint16 array."""
    try:
        import tifffile
    except ImportError:
        raise ImportError(
            "tifffile is required. Install with: pip install tifffile"
        )

    frames_dir = trial_dir / "images" / "images"
    if not frames_dir.exists():
        raise FileNotFoundError(
            f"Frames directory not found: {frames_dir}\n"
            "Expected nested 'images/images/' structure."
        )

    frame_files = sorted(frames_dir.glob("frame_*.tif"))
    if not frame_files:
        raise FileNotFoundError(f"No frame_*.tif files found in {frames_dir}")

    frames = [tifffile.imread(str(f)) for f in frame_files]
    return np.stack(frames, axis=0)  # (T, H, W)


def run_suite2p(
    trial_dir: Path,
    output_dir: Path,
    ops_overrides: Optional[dict] = None,
) -> Path:
    """Run suite2p on a single trial directory.

    Loads frames from ``trial_dir/images/images/``, stacks them into a
    temporary TIFF, configures suite2p ops, and runs the full pipeline
    (motion correction → ROI detection → neuropil subtraction → ΔF/F).

    Parameters
    ----------
    trial_dir:
        Trial directory containing ``trial.json`` and ``images/images/``.
    output_dir:
        Directory where suite2p output will be written.
    ops_overrides:
        Optional dict of suite2p ops to override defaults.

    Returns
    -------
    Path
        Path to the suite2p output directory (``output_dir/suite2p/plane0/``).

    Raises
    ------
    ImportError
        If suite2p is not installed.
    """
    try:
        import suite2p
        from suite2p import run_s2p, default_ops
    except ImportError:
        raise ImportError(
            "suite2p is required for run_suite2p(). "
            "Install with: pip install suite2p"
        )
    try:
        import tifffile
    except ImportError:
        raise ImportError(
            "tifffile is required. Install with: pip install tifffile"
        )

    trial_dir = Path(trial_dir)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    fps = _load_fps_from_trial(trial_dir)

    # Load frame stack and write to a single TIFF in a temp dir
    print(f"Loading frames from {trial_dir / 'images' / 'images'} ...")
    stack = _load_frame_stack(trial_dir)  # (T, H, W)
    print(f"  Loaded {stack.shape[0]} frames, shape {stack.shape[1:]}")

    tmp_dir = Path(tempfile.mkdtemp(prefix="suite2p_input_"))
    try:
        tmp_tiff = tmp_dir / "frames.tif"
        tifffile.imwrite(str(tmp_tiff), stack)
        print(f"  Wrote temporary TIFF: {tmp_tiff}")

        # Build ops
        ops = default_ops()
        ops.update({
            "fs": fps,
            "nplanes": 1,
            "nchannels": 1,
            "diameter": 20,
            "tau": 1.5,
            "threshold_scaling": 1.0,
            "connected": True,
            "max_overlap": 0.75,
            "high_pass": 100,
            "save_path0": str(output_dir),
            "fast_disk": str(output_dir),
        })

        if ops_overrides:
            ops.update(ops_overrides)

        db = {
            "h5py": [],
            "h5py_key": "data",
            "look_one_level_down": False,
            "data_path": [str(tmp_dir)],
            "subfolders": [],
            "tiff_list": [str(tmp_tiff)],
        }

        print("Running suite2p pipeline ...")
        run_s2p(ops=ops, db=db)

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    suite2p_out = output_dir / "suite2p" / "plane0"
    print(f"Suite2p complete. Results at: {suite2p_out}")
    return suite2p_out


def load_suite2p_results(suite2p_dir: Path) -> dict:
    """Load suite2p output files and compute ΔF/F.

    Loads ``F.npy``, ``Fneu.npy``, ``iscell.npy``, ``stat.npy``,
    ``ops.npy``.  Computes neuropil-corrected fluorescence and ΔF/F via
    rolling-percentile baseline.

    Parameters
    ----------
    suite2p_dir:
        Path to ``suite2p/plane0/`` output directory.

    Returns
    -------
    dict
        Keys: ``F``, ``Fneu``, ``F_corr``, ``dff``, ``iscell``, ``stat``,
        ``ops``.
    """
    suite2p_dir = Path(suite2p_dir)

    F = np.load(suite2p_dir / "F.npy", allow_pickle=False)           # (N, T)
    Fneu = np.load(suite2p_dir / "Fneu.npy", allow_pickle=False)     # (N, T)
    iscell = np.load(suite2p_dir / "iscell.npy", allow_pickle=False) # (N, 2)
    stat = np.load(suite2p_dir / "stat.npy", allow_pickle=True)      # (N,)
    ops = np.load(suite2p_dir / "ops.npy", allow_pickle=True).item() # dict

    # Neuropil correction
    F_corr = F - 0.7 * Fneu  # (N, T)

    # ΔF/F via rolling percentile baseline
    dff = _compute_dff_percentile(F_corr)

    return {
        "F": F,
        "Fneu": Fneu,
        "F_corr": F_corr,
        "dff": dff,
        "iscell": iscell,
        "stat": stat,
        "ops": ops,
    }


def _compute_dff_percentile(
    F: np.ndarray,
    window: int = 300,
    percentile: float = 10.0,
) -> np.ndarray:
    """Compute ΔF/F using a rolling percentile baseline.

    Parameters
    ----------
    F:
        Fluorescence array, shape (N_rois, T_frames).
    window:
        Rolling window size in frames.
    percentile:
        Percentile used for baseline estimation (default 10th).

    Returns
    -------
    np.ndarray
        ΔF/F array, same shape as F.
    """
    from scipy.ndimage import percentile_filter

    dff = np.zeros_like(F, dtype=np.float32)
    for i in range(F.shape[0]):
        trace = F[i].astype(np.float32)
        # Estimate rolling baseline
        f0 = percentile_filter(trace, percentile=percentile, size=window)
        f0 = np.maximum(f0, 1e-6)  # avoid division by zero
        dff[i] = (trace - f0) / f0

    return dff


def match_suite2p_rois_to_glomeruli(
    suite2p_results: dict,
    manual_rois: dict,
    image_shape: tuple,
) -> dict:
    """Match suite2p detected ROIs to manually labeled glomeruli by IoU.

    For each manually labeled glomerulus mask, find the suite2p ROI with
    the highest intersection-over-union (IoU).

    Parameters
    ----------
    suite2p_results:
        Output from :func:`load_suite2p_results`.
    manual_rois:
        Dict of ``{name: {"mask": np.ndarray bool, ...}, ...}``.
    image_shape:
        ``(height, width)`` of the imaging plane.

    Returns
    -------
    dict
        ``{"DA1": suite2p_roi_index, ...}``
        Value is -1 if no matching ROI is found.
    """
    stat = suite2p_results["stat"]
    H, W = image_shape[:2]

    # Build binary masks for each suite2p ROI
    s2p_masks = []
    for roi_stat in stat:
        mask = np.zeros((H, W), dtype=bool)
        ypix = roi_stat.get("ypix", np.array([], dtype=int))
        xpix = roi_stat.get("xpix", np.array([], dtype=int))
        valid_y = (ypix >= 0) & (ypix < H)
        valid_x = (xpix >= 0) & (xpix < W)
        valid = valid_y & valid_x
        if valid.any():
            mask[ypix[valid], xpix[valid]] = True
        s2p_masks.append(mask)

    mapping: dict = {}
    for name, roi_data in manual_rois.items():
        manual_mask = roi_data.get("mask")
        if manual_mask is None or not manual_mask.any():
            mapping[name] = -1
            continue

        best_iou = 0.0
        best_idx = -1
        for j, s2p_mask in enumerate(s2p_masks):
            intersection = (manual_mask & s2p_mask).sum()
            union = (manual_mask | s2p_mask).sum()
            if union == 0:
                continue
            iou = intersection / union
            if iou > best_iou:
                best_iou = iou
                best_idx = j

        mapping[name] = best_idx
        if best_idx >= 0:
            print(f"  {name} → suite2p ROI #{best_idx} (IoU={best_iou:.3f})")
        else:
            print(f"  {name} → no match found")

    return mapping
