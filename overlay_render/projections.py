"""
Activity summary projections for overlay_render.

Generates epoch-based projection images (mean, std, RGB composite) from
calcium imaging recordings to help distinguish active glomeruli.

Epoch definitions:
  - baseline: frames before the first odor interval
  - odor: union of all odor-ON frames
  - post: frames after the last odor interval (optionally capped)
"""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Projection computations
# ---------------------------------------------------------------------------

def mean_projection(frames: np.ndarray, idx: np.ndarray) -> np.ndarray:
    """Mean intensity projection over selected frame indices.

    Args:
        frames: (T, H, W) array (any numeric dtype).
        idx: 1-D array of frame indices.

    Returns:
        (H, W) float32 image.
    """
    return frames[idx].astype(np.float32).mean(axis=0)


def std_projection(frames: np.ndarray, idx: np.ndarray) -> np.ndarray:
    """Standard-deviation projection over selected frame indices.

    Args:
        frames: (T, H, W) array.
        idx: 1-D array of frame indices.

    Returns:
        (H, W) float32 image.
    """
    return frames[idx].astype(np.float32).std(axis=0)


def max_projection(frames: np.ndarray, idx: np.ndarray) -> np.ndarray:
    """Maximum intensity projection over selected frame indices.

    Args:
        frames: (T, H, W) array.
        idx: 1-D array of frame indices.

    Returns:
        (H, W) float32 image.
    """
    return frames[idx].astype(np.float32).max(axis=0)


# ---------------------------------------------------------------------------
# Epoch determination
# ---------------------------------------------------------------------------

@dataclass
class EpochIndices:
    """Frame indices for each epoch."""
    baseline: np.ndarray
    odor: np.ndarray
    post: np.ndarray


def determine_epochs(
    odor_intervals: list,
    n_frames: int,
    post_frames: Optional[int] = None,
) -> EpochIndices:
    """Compute frame indices for baseline / odor / post epochs.

    Args:
        odor_intervals: List of objects with ``.start_frame`` and
            ``.end_frame`` (inclusive) attributes.
        n_frames: Total number of recording frames.
        post_frames: If set, cap the post-odor epoch to this many frames.

    Returns:
        ``EpochIndices`` with arrays for each epoch.

    Raises:
        ValueError: If no odor intervals are provided.
    """
    if not odor_intervals:
        raise ValueError(
            "projections require odor timing data (CSV odor_on column or "
            "JSON odor_intervals) to define epochs.  Provide timing metadata "
            "or disable projections."
        )

    first_start = min(iv.start_frame for iv in odor_intervals)
    last_end = max(iv.end_frame for iv in odor_intervals)

    # Baseline: all frames before first odor start
    if first_start > 0:
        baseline_idx = np.arange(0, first_start)
    else:
        # No pre-odor frames; use frame 0 alone as a minimal baseline
        baseline_idx = np.array([0])
        logger.warning(
            "First odor interval starts at frame 0; baseline will use "
            "only frame 0.  Consider providing an explicit baseline."
        )

    # Odor: union of all interval frames
    odor_mask = np.zeros(n_frames, dtype=bool)
    for iv in odor_intervals:
        start = max(0, iv.start_frame)
        end = min(n_frames - 1, iv.end_frame)
        odor_mask[start:end + 1] = True
    odor_idx = np.where(odor_mask)[0]

    # Post: frames after last odor end
    post_start = last_end + 1
    if post_start < n_frames:
        post_end = n_frames
        if post_frames is not None:
            post_end = min(post_start + post_frames, n_frames)
        post_idx = np.arange(post_start, post_end)
    else:
        # No post-odor frames; use the last frame
        post_idx = np.array([n_frames - 1])
        logger.warning(
            "No post-odor frames available; post epoch will use the "
            "last frame only."
        )

    return EpochIndices(baseline=baseline_idx, odor=odor_idx, post=post_idx)


# ---------------------------------------------------------------------------
# Image scaling / saving
# ---------------------------------------------------------------------------

def _scale_to_uint8(
    img: np.ndarray,
    p_lo: float = 1.0,
    p_hi: float = 99.0,
    gamma: float = 1.0,
) -> np.ndarray:
    """Percentile-scale a float image to uint8.

    Args:
        img: (H, W) float32 image.
        p_lo: Lower percentile for black point.
        p_hi: Upper percentile for white point.
        gamma: Gamma correction (>1 brightens mid-tones).

    Returns:
        (H, W) uint8 image.
    """
    vmin = float(np.percentile(img, p_lo))
    vmax = float(np.percentile(img, p_hi))
    if vmax <= vmin:
        vmin, vmax = float(img.min()), float(img.max())
    if vmax <= vmin:
        return np.zeros_like(img, dtype=np.uint8)

    scaled = (img.astype(np.float64) - vmin) / (vmax - vmin)
    scaled = np.clip(scaled, 0.0, 1.0)
    if gamma != 1.0:
        scaled = np.power(scaled, 1.0 / gamma)
    return (scaled * 255).astype(np.uint8)


def _image_stats(img: np.ndarray) -> Dict:
    """Compute summary statistics for a float image."""
    return {
        "min": float(np.min(img)),
        "max": float(np.max(img)),
        "mean": float(np.mean(img)),
        "std": float(np.std(img)),
    }


# ---------------------------------------------------------------------------
# Main projection generator
# ---------------------------------------------------------------------------

def generate_projections(
    frames: np.ndarray,
    epochs: EpochIndices,
    output_dir: Path,
    save_png: bool = True,
    save_tiff: bool = False,
    make_rgb: bool = True,
    dff_frames: Optional[np.ndarray] = None,
    p_lo: float = 1.0,
    p_hi: float = 99.0,
    gamma: float = 1.0,
    names: Optional[Dict[str, str]] = None,
) -> Dict:
    """Generate and save all projection images.

    Args:
        frames: Preprocessed raw recording (T, H, W).
        epochs: Frame index sets for each epoch.
        output_dir: Directory to write projection files.
        save_png: Save view-scaled PNG versions.
        save_tiff: Save float32 TIFF versions.
        make_rgb: Generate RGB epoch composite image.
        dff_frames: Optional ΔF/F array (T, H, W) float32.
        p_lo: Lower percentile for PNG scaling.
        p_hi: Upper percentile for PNG scaling.
        gamma: Gamma for PNG scaling.
        names: Optional dict overriding default file names.

    Returns:
        Dict of projection metadata for report.json.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    default_names = {
        "baseline_mean": "baseline_mean",
        "odor_mean": "odor_mean",
        "post_mean": "post_mean",
        "odor_std": "odor_std",
        "odor_dff_mean": "odor_dff_mean",
        "rgb_epochs": "epochs_rgb",
    }
    if names:
        default_names.update(names)
    nm = default_names

    report: Dict = {
        "epochs": {
            "baseline_frames": [int(epochs.baseline[0]), int(epochs.baseline[-1])],
            "n_baseline": len(epochs.baseline),
            "odor_frames": [int(epochs.odor[0]), int(epochs.odor[-1])],
            "n_odor": len(epochs.odor),
            "post_frames": [int(epochs.post[0]), int(epochs.post[-1])],
            "n_post": len(epochs.post),
        },
        "images": {},
    }

    def _save(name: str, img_float: np.ndarray) -> None:
        """Save a projection image (PNG and/or TIFF) and record stats."""
        report["images"][name] = _image_stats(img_float)

        if save_png:
            png_path = output_dir / f"{nm[name]}.png"
            uint8 = _scale_to_uint8(img_float, p_lo, p_hi, gamma)
            cv2.imwrite(str(png_path), uint8)
            report["images"][name]["png"] = str(png_path)
            logger.info(f"Saved projection: {png_path}")

        if save_tiff:
            import tifffile
            tiff_path = output_dir / f"{nm[name]}.tif"
            tifffile.imwrite(str(tiff_path), img_float)
            report["images"][name]["tiff"] = str(tiff_path)
            logger.info(f"Saved projection TIFF: {tiff_path}")

    # -- Raw projections --
    logger.info("Computing baseline mean projection...")
    _save("baseline_mean", mean_projection(frames, epochs.baseline))

    logger.info("Computing odor mean projection...")
    _save("odor_mean", mean_projection(frames, epochs.odor))

    logger.info("Computing post mean projection...")
    _save("post_mean", mean_projection(frames, epochs.post))

    logger.info("Computing odor std projection...")
    _save("odor_std", std_projection(frames, epochs.odor))

    # -- DFF odor mean (if available) --
    if dff_frames is not None:
        logger.info("Computing odor ΔF/F mean projection...")
        _save("odor_dff_mean", mean_projection(dff_frames, epochs.odor))

    # -- RGB epoch composite --
    if make_rgb:
        logger.info("Computing RGB epoch composite...")
        r = _scale_to_uint8(
            mean_projection(frames, epochs.baseline), p_lo, p_hi, gamma
        )
        g = _scale_to_uint8(
            mean_projection(frames, epochs.odor), p_lo, p_hi, gamma
        )
        b = _scale_to_uint8(
            mean_projection(frames, epochs.post), p_lo, p_hi, gamma
        )
        rgb = np.stack([b, g, r], axis=-1)  # OpenCV BGR order
        rgb_path = output_dir / f"{nm['rgb_epochs']}.png"
        cv2.imwrite(str(rgb_path), rgb)
        report["images"]["rgb_epochs"] = {
            "png": str(rgb_path),
            "channels": "R=baseline, G=odor, B=post",
        }
        logger.info(f"Saved RGB composite: {rgb_path}")

    return report
