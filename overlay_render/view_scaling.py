"""
View scaling (brightness/contrast) for overlay_render.

Provides view-only adjustments:
- Percentile-based scaling
- Min/max scaling
- Gamma correction
- CLAHE (optional)

These are purely for visualization - no analysis operations.
"""

import logging
from dataclasses import dataclass
from typing import Optional, Tuple, Union

import numpy as np

from .config import ViewSettings

logger = logging.getLogger(__name__)


@dataclass
class ScalingParams:
    """
    Pre-computed scaling parameters for consistent frame processing.

    Attributes:
        vmin: Minimum value for scaling.
        vmax: Maximum value for scaling.
        gamma: Gamma correction factor.
        use_clahe: Whether to apply CLAHE.
        clahe_clip_limit: CLAHE clip limit.
        clahe_tile_grid: CLAHE tile grid size.
    """
    vmin: float
    vmax: float
    gamma: float
    use_clahe: bool
    clahe_clip_limit: float
    clahe_tile_grid: Tuple[int, int]


def compute_global_scaling_params(
    frames: Union[np.ndarray, "RecordingIterator"],
    settings: ViewSettings,
    sample_frames: int = 50,
    roi_center_fraction: Optional[float] = None
) -> ScalingParams:
    """
    Compute global scaling parameters from a sample of frames.

    Uses global scaling for consistent appearance across all frames.

    Args:
        frames: Either a 3D array (T, H, W) or a RecordingIterator.
        settings: View settings from config.
        sample_frames: Number of frames to sample for statistics.
        roi_center_fraction: If set (0.0-1.0), only use the center portion of the
            image for computing percentiles. E.g., 0.5 = center 50% of image.
            This helps when edges are bright but the signal is in the center.

    Returns:
        ScalingParams for use with scale_frame().
    """
    logger.info("Computing global scaling parameters...")

    # Collect sample frames
    if isinstance(frames, np.ndarray):
        n_frames = frames.shape[0]
        indices = np.linspace(0, n_frames - 1, min(sample_frames, n_frames), dtype=int)
        samples = [frames[i] for i in indices]
    else:
        # RecordingIterator
        n_frames = frames.n_frames
        indices = np.linspace(0, n_frames - 1, min(sample_frames, n_frames), dtype=int)
        samples = [frames.get_frame(int(i)) for i in indices]

    # Stack samples
    sample_stack = np.stack(samples, axis=0)

    # Apply ROI if specified (use only center portion for statistics)
    if roi_center_fraction is not None and 0 < roi_center_fraction < 1:
        h, w = sample_stack.shape[1], sample_stack.shape[2]
        margin_h = int(h * (1 - roi_center_fraction) / 2)
        margin_w = int(w * (1 - roi_center_fraction) / 2)
        sample_stack = sample_stack[:, margin_h:h-margin_h, margin_w:w-margin_w]
        logger.info(f"Using center ROI ({roi_center_fraction*100:.0f}%) for scaling statistics")

    # Compute scaling bounds
    if settings.method == "percentile":
        vmin = np.percentile(sample_stack, settings.p_lo)
        vmax = np.percentile(sample_stack, settings.p_hi)
        logger.info(
            f"Percentile scaling: p{settings.p_lo}={vmin:.2f}, "
            f"p{settings.p_hi}={vmax:.2f}"
        )
    elif settings.method == "minmax":
        vmin = sample_stack.min()
        vmax = sample_stack.max()
        logger.info(f"Min/max scaling: min={vmin:.2f}, max={vmax:.2f}")
    else:
        raise ValueError(f"Unknown scaling method: {settings.method}")

    # Ensure valid range
    if vmax <= vmin:
        logger.warning(f"vmax <= vmin ({vmax} <= {vmin}), using [0, 1]")
        vmin, vmax = 0, 1

    return ScalingParams(
        vmin=vmin,
        vmax=vmax,
        gamma=settings.gamma,
        use_clahe=settings.clahe,
        clahe_clip_limit=settings.clahe_clip_limit,
        clahe_tile_grid=settings.clahe_tile_grid,
    )


def scale_frame(
    frame: np.ndarray,
    params: ScalingParams
) -> np.ndarray:
    """
    Apply view scaling to a single frame.

    Args:
        frame: Input 2D grayscale frame.
        params: Pre-computed scaling parameters.

    Returns:
        Scaled uint8 frame [0, 255].
    """
    # Normalize to [0, 1]
    scaled = (frame.astype(np.float64) - params.vmin) / (params.vmax - params.vmin)
    scaled = np.clip(scaled, 0, 1)

    # Apply gamma correction
    if params.gamma != 1.0:
        scaled = np.power(scaled, 1.0 / params.gamma)

    # Convert to uint8
    result = (scaled * 255).astype(np.uint8)

    # Apply CLAHE if enabled
    if params.use_clahe:
        result = _apply_clahe(
            result,
            params.clahe_clip_limit,
            params.clahe_tile_grid
        )

    return result


def _apply_clahe(
    image: np.ndarray,
    clip_limit: float,
    tile_grid: Tuple[int, int]
) -> np.ndarray:
    """
    Apply Contrast Limited Adaptive Histogram Equalization.

    Args:
        image: uint8 grayscale image.
        clip_limit: CLAHE clip limit.
        tile_grid: Tile grid size (rows, cols).

    Returns:
        CLAHE-enhanced uint8 image.
    """
    try:
        import cv2
    except ImportError:
        raise ImportError(
            "OpenCV is required for CLAHE. "
            "Install with: pip install opencv-python"
        )

    clahe = cv2.createCLAHE(
        clipLimit=clip_limit,
        tileGridSize=tile_grid
    )

    return clahe.apply(image)


def scale_view(
    image: np.ndarray,
    settings: ViewSettings,
    params: Optional[ScalingParams] = None
) -> np.ndarray:
    """
    Apply view scaling to an image.

    Convenience function that computes params if not provided.

    Args:
        image: 2D grayscale image or 3D stack (T, H, W).
        settings: View settings from config.
        params: Optional pre-computed scaling params.

    Returns:
        Scaled uint8 image(s).
    """
    if params is None:
        if image.ndim == 2:
            # Single image - compute params from it
            params = _compute_single_image_params(image, settings)
        else:
            # Stack - compute global params
            params = compute_global_scaling_params(image, settings)

    if image.ndim == 2:
        return scale_frame(image, params)
    elif image.ndim == 3:
        return np.stack([scale_frame(f, params) for f in image], axis=0)
    else:
        raise ValueError(f"Expected 2D or 3D image, got {image.ndim}D")


def _compute_single_image_params(
    image: np.ndarray,
    settings: ViewSettings
) -> ScalingParams:
    """Compute scaling params for a single image."""
    if settings.method == "percentile":
        vmin = np.percentile(image, settings.p_lo)
        vmax = np.percentile(image, settings.p_hi)
    else:
        vmin = image.min()
        vmax = image.max()

    if vmax <= vmin:
        vmin, vmax = 0, 1

    return ScalingParams(
        vmin=vmin,
        vmax=vmax,
        gamma=settings.gamma,
        use_clahe=settings.clahe,
        clahe_clip_limit=settings.clahe_clip_limit,
        clahe_tile_grid=settings.clahe_tile_grid,
    )


def compute_histogram(
    image: np.ndarray,
    n_bins: int = 256
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Compute histogram of an image (for debugging/visualization).

    Args:
        image: Input image.
        n_bins: Number of histogram bins.

    Returns:
        Tuple of (counts, bin_edges).
    """
    return np.histogram(image.ravel(), bins=n_bins)
