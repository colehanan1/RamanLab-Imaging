"""
Overlay compositing for overlay_render.

Combines structure (RFP) and recording (GCaMP/functional) images into
a single visualization.

Modes:
- blend: Simple alpha blending
- falsecolor: Structure in red, recording in green (magenta-green)
"""

import logging
from typing import Literal, Tuple

import numpy as np

from .config import OverlaySettings

logger = logging.getLogger(__name__)


def composite_frame(
    structure: np.ndarray,
    recording: np.ndarray,
    settings: OverlaySettings
) -> np.ndarray:
    """
    Composite structure and recording frame into overlay.

    Args:
        structure: Structure image (uint8, grayscale or RGB).
        recording: Recording frame (uint8, grayscale).
        settings: Overlay settings.

    Returns:
        RGB composite frame (uint8, H, W, 3).
    """
    # Ensure both are uint8
    if structure.dtype != np.uint8:
        structure = structure.astype(np.uint8)
    if recording.dtype != np.uint8:
        recording = recording.astype(np.uint8)

    # Ensure same spatial dimensions
    if structure.shape[:2] != recording.shape[:2]:
        raise ValueError(
            f"Structure and recording dimensions don't match: "
            f"{structure.shape[:2]} vs {recording.shape[:2]}"
        )

    if settings.mode == "blend":
        return _blend_overlay(structure, recording, settings.alpha)
    elif settings.mode == "falsecolor":
        return _falsecolor_overlay(structure, recording, settings.alpha)
    else:
        raise ValueError(f"Unknown overlay mode: {settings.mode}")


def _blend_overlay(
    structure: np.ndarray,
    recording: np.ndarray,
    alpha: float
) -> np.ndarray:
    """
    Simple alpha blending of structure and recording.

    Structure is shown as grayscale base, recording is overlaid with alpha.

    Args:
        structure: Structure image (grayscale or RGB).
        recording: Recording frame (grayscale).
        alpha: Blend factor for recording (0=structure only, 1=recording only).

    Returns:
        RGB blended frame.
    """
    from .utils import ensure_rgb

    # Convert to RGB
    struct_rgb = ensure_rgb(structure)
    rec_rgb = ensure_rgb(recording)

    # Blend: result = (1-alpha)*structure + alpha*recording
    blended = (
        (1 - alpha) * struct_rgb.astype(np.float32) +
        alpha * rec_rgb.astype(np.float32)
    )

    return np.clip(blended, 0, 255).astype(np.uint8)


def _falsecolor_overlay(
    structure: np.ndarray,
    recording: np.ndarray,
    alpha: float
) -> np.ndarray:
    """
    False color overlay: structure in red/magenta, recording in green.

    Common visualization for RFP (structure) + GCaMP (recording).

    Args:
        structure: Structure image (grayscale).
        recording: Recording frame (grayscale).
        alpha: Controls recording channel intensity.

    Returns:
        RGB false color frame.
    """
    from .utils import ensure_grayscale

    # Ensure grayscale
    if structure.ndim == 3:
        structure = ensure_grayscale(structure)
    if recording.ndim == 3:
        recording = ensure_grayscale(recording)

    h, w = structure.shape

    # Create RGB output
    result = np.zeros((h, w, 3), dtype=np.uint8)

    # Red channel: structure
    result[:, :, 0] = structure

    # Green channel: recording (scaled by alpha)
    result[:, :, 1] = (recording.astype(np.float32) * alpha).astype(np.uint8)

    # Blue channel: structure (for magenta) scaled down
    result[:, :, 2] = (structure.astype(np.float32) * 0.5).astype(np.uint8)

    return result


def create_side_by_side(
    structure: np.ndarray,
    recording: np.ndarray,
    composite: np.ndarray,
    gap: int = 10
) -> np.ndarray:
    """
    Create side-by-side comparison of structure, recording, and composite.

    Args:
        structure: Structure image (grayscale or RGB).
        recording: Recording frame (grayscale or RGB).
        composite: Composite frame (RGB).
        gap: Gap between panels in pixels.

    Returns:
        Combined RGB image.
    """
    from .utils import ensure_rgb

    struct_rgb = ensure_rgb(structure)
    rec_rgb = ensure_rgb(recording)

    h, w, _ = struct_rgb.shape
    total_width = w * 3 + gap * 2

    result = np.zeros((h, total_width, 3), dtype=np.uint8)

    # Structure
    result[:, :w, :] = struct_rgb

    # Recording
    result[:, w + gap:2*w + gap, :] = rec_rgb

    # Composite
    result[:, 2*w + 2*gap:, :] = composite

    return result


def compute_overlay_stats(
    structure: np.ndarray,
    recording: np.ndarray
) -> dict:
    """
    Compute statistics about the overlay inputs.

    Useful for debugging and report generation.

    Args:
        structure: Structure image.
        recording: Recording frame.

    Returns:
        Dict with statistics.
    """
    return {
        "structure": {
            "shape": structure.shape,
            "dtype": str(structure.dtype),
            "min": float(structure.min()),
            "max": float(structure.max()),
            "mean": float(structure.mean()),
            "std": float(structure.std()),
        },
        "recording": {
            "shape": recording.shape,
            "dtype": str(recording.dtype),
            "min": float(recording.min()),
            "max": float(recording.max()),
            "mean": float(recording.mean()),
            "std": float(recording.std()),
        },
    }


class OverlayRenderer:
    """
    Helper class for rendering overlays consistently.

    Caches structure processing for efficiency.
    """

    def __init__(
        self,
        structure: np.ndarray,
        settings: OverlaySettings
    ):
        """
        Initialize renderer with structure image.

        Args:
            structure: Structure (reference) image.
            settings: Overlay settings.
        """
        self.settings = settings
        self._structure_uint8 = self._prepare_structure(structure)

        logger.info(
            f"OverlayRenderer initialized: mode={settings.mode}, "
            f"alpha={settings.alpha}"
        )

    def _prepare_structure(self, structure: np.ndarray) -> np.ndarray:
        """Prepare structure image for overlay."""
        # Normalize to uint8 if needed
        if structure.dtype != np.uint8:
            from .utils import normalize_to_uint8
            return normalize_to_uint8(structure)
        return structure

    def render(self, recording_frame: np.ndarray) -> np.ndarray:
        """
        Render overlay for a single recording frame.

        Args:
            recording_frame: Recording frame (uint8 grayscale).

        Returns:
            RGB composite frame.
        """
        # Ensure frame is uint8
        if recording_frame.dtype != np.uint8:
            from .utils import normalize_to_uint8
            recording_frame = normalize_to_uint8(recording_frame)

        return composite_frame(
            self._structure_uint8,
            recording_frame,
            self.settings
        )

    @property
    def output_shape(self) -> Tuple[int, int, int]:
        """Shape of output frames (H, W, 3)."""
        h, w = self._structure_uint8.shape[:2]
        return (h, w, 3)
