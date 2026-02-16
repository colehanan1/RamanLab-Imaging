"""
ΔF/F (delta-F over F0) computation for calcium imaging data.

Computes baseline fluorescence (F0) and normalised change (ΔF/F)
frame-by-frame.  Designed for TIFF stack recordings.

Typical usage:
    f0 = compute_f0(frames, baseline_idx, method="mean")
    dff = apply_dff(frames, f0)
"""

import logging
from typing import Literal, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


def compute_f0(
    frames: np.ndarray,
    baseline_idx: np.ndarray,
    method: Literal["mean", "percentile"] = "mean",
    percentile: float = 20.0,
) -> np.ndarray:
    """
    Compute baseline fluorescence image F0 from selected frames.

    Args:
        frames: Recording array with shape (T, H, W), any numeric dtype.
        baseline_idx: 1-D array of frame indices to use as baseline.
        method: Aggregation method – "mean" or "percentile".
        percentile: Percentile value when *method* is ``"percentile"``.

    Returns:
        F0 image with shape (H, W) as float32.

    Raises:
        ValueError: If *baseline_idx* is empty or out of range.
    """
    if baseline_idx.size == 0:
        raise ValueError("baseline_idx must contain at least one frame index")

    if baseline_idx.max() >= frames.shape[0] or baseline_idx.min() < 0:
        raise ValueError(
            f"baseline_idx values must be in [0, {frames.shape[0] - 1}], "
            f"got range [{baseline_idx.min()}, {baseline_idx.max()}]"
        )

    baseline_frames = frames[baseline_idx].astype(np.float32)

    if method == "mean":
        f0 = np.mean(baseline_frames, axis=0)
    elif method == "percentile":
        f0 = np.percentile(baseline_frames, percentile, axis=0).astype(np.float32)
    else:
        raise ValueError(f"f0 method must be 'mean' or 'percentile', got '{method}'")

    logger.info(
        "F0 computed (%s): shape=%s, min=%.2f, max=%.2f, mean=%.2f",
        method, f0.shape, f0.min(), f0.max(), f0.mean(),
    )
    return f0


def apply_dff(
    frames: np.ndarray,
    f0: np.ndarray,
    eps: float = 1e-6,
    clip: Optional[Tuple[float, float]] = None,
) -> np.ndarray:
    """
    Compute ΔF/F = (F - F0) / (F0 + eps) for every frame.

    Args:
        frames: Recording array (T, H, W), any numeric dtype.
        f0: Baseline image (H, W) as float32.
        eps: Small constant added to F0 to avoid division by zero.
        clip: Optional (min, max) to clip ΔF/F values.  ``None`` means no
              clipping.

    Returns:
        ΔF/F array (T, H, W) as float32.
    """
    frames_f = frames.astype(np.float32)
    dff = (frames_f - f0[np.newaxis, ...]) / (f0[np.newaxis, ...] + eps)

    if clip is not None:
        dff = np.clip(dff, clip[0], clip[1])

    logger.info(
        "ΔF/F computed: shape=%s, min=%.4f, max=%.4f, mean=%.4f",
        dff.shape, dff.min(), dff.max(), dff.mean(),
    )
    return dff


def determine_baseline_frames(
    baseline_source: str,
    baseline_frames_range: Optional[Tuple[int, int]],
    odor_intervals: list,
    n_frames: int,
) -> np.ndarray:
    """
    Determine which frame indices to use as baseline.

    Args:
        baseline_source: ``"odor"`` or ``"explicit"``.
        baseline_frames_range: Inclusive (start, end) when *baseline_source*
            is ``"explicit"``.
        odor_intervals: List of objects with ``.start_frame`` attribute
            (from timing module).
        n_frames: Total number of recording frames.

    Returns:
        1-D numpy array of baseline frame indices.

    Raises:
        ValueError: If baseline cannot be determined.
    """
    if baseline_source == "explicit":
        if baseline_frames_range is None:
            raise ValueError(
                "dff.baseline_frames must be set when baseline_source='explicit'"
            )
        start, end = baseline_frames_range
        if start < 0 or end >= n_frames or start > end:
            raise ValueError(
                f"dff.baseline_frames [{start}, {end}] out of valid range "
                f"[0, {n_frames - 1}]"
            )
        return np.arange(start, end + 1)

    elif baseline_source == "odor":
        if not odor_intervals:
            raise ValueError(
                "dff.baseline_source='odor' but no odor intervals found. "
                "Provide odor timing data (CSV/JSON) or use "
                "baseline_source='explicit' with an explicit frame range."
            )
        first_odor_start = min(iv.start_frame for iv in odor_intervals)
        if first_odor_start <= 0:
            raise ValueError(
                "dff.baseline_source='odor' but the first odor interval starts "
                f"at frame {first_odor_start}; no pre-odor baseline frames are "
                "available. Use baseline_source='explicit' instead."
            )
        return np.arange(0, first_odor_start)

    else:
        raise ValueError(
            f"dff.baseline_source must be 'odor' or 'explicit', got '{baseline_source}'"
        )
