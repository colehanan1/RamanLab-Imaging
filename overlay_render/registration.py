"""
Image registration for overlay_render.

Provides rigid (Euclidean) and affine registration using OpenCV's ECC algorithm.
Registration is computed from a representative frame and applied to all frames.
"""

import logging
from dataclasses import dataclass
from typing import Literal, Optional, Tuple

import numpy as np

from .config import RegistrationSettings

logger = logging.getLogger(__name__)


@dataclass
class RegistrationResult:
    """
    Result of registration computation.

    Attributes:
        warp_matrix: 2x3 or 3x3 transformation matrix.
        correlation: Final ECC correlation coefficient.
        converged: Whether optimization converged.
        model: Registration model used.
        iterations: Number of iterations performed.
    """
    warp_matrix: np.ndarray
    correlation: float
    converged: bool
    model: str
    iterations: int

    def to_dict(self) -> dict:
        """Convert to serializable dictionary."""
        return {
            "warp_matrix": self.warp_matrix.tolist(),
            "correlation": self.correlation,
            "converged": self.converged,
            "model": self.model,
            "iterations": self.iterations,
        }


def compute_registration(
    reference: np.ndarray,
    target: np.ndarray,
    settings: RegistrationSettings
) -> RegistrationResult:
    """
    Compute registration transform from target to reference.

    Uses OpenCV's Enhanced Correlation Coefficient (ECC) algorithm.

    Args:
        reference: Reference image (structure).
        target: Target image (representative recording frame).
        settings: Registration settings.

    Returns:
        RegistrationResult with transformation matrix and metrics.

    Raises:
        ImportError: If OpenCV is not available.
        RuntimeError: If registration fails completely.
    """
    try:
        import cv2
    except ImportError:
        raise ImportError(
            "OpenCV is required for registration. "
            "Install with: pip install opencv-python"
        )

    logger.info(f"Computing {settings.model} registration...")

    # Ensure images are grayscale uint8
    ref = _prepare_for_registration(reference)
    tgt = _prepare_for_registration(target)

    # Verify dimensions match
    if ref.shape != tgt.shape:
        raise ValueError(
            f"Image dimensions don't match: reference={ref.shape}, "
            f"target={tgt.shape}"
        )

    # Downscale for faster computation
    if settings.downscale > 1:
        ref_small = cv2.resize(
            ref,
            (ref.shape[1] // settings.downscale, ref.shape[0] // settings.downscale),
            interpolation=cv2.INTER_AREA
        )
        tgt_small = cv2.resize(
            tgt,
            (tgt.shape[1] // settings.downscale, tgt.shape[0] // settings.downscale),
            interpolation=cv2.INTER_AREA
        )
    else:
        ref_small = ref
        tgt_small = tgt

    # Set motion model
    if settings.model == "euclidean":
        motion_type = cv2.MOTION_EUCLIDEAN
        warp_matrix = np.eye(2, 3, dtype=np.float32)
    elif settings.model == "affine":
        motion_type = cv2.MOTION_AFFINE
        warp_matrix = np.eye(2, 3, dtype=np.float32)
    else:
        raise ValueError(f"Unknown registration model: {settings.model}")

    # ECC criteria
    criteria = (
        cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT,
        settings.ecc_iters,
        settings.ecc_eps
    )

    try:
        # Run ECC optimization
        correlation, warp_matrix = cv2.findTransformECC(
            ref_small.astype(np.float32),
            tgt_small.astype(np.float32),
            warp_matrix,
            motion_type,
            criteria,
            inputMask=None,
            gaussFiltSize=5
        )
        converged = True
        logger.info(f"Registration converged with correlation={correlation:.4f}")

    except cv2.error as e:
        logger.warning(f"ECC registration failed: {e}")
        # Return identity transform
        warp_matrix = np.eye(2, 3, dtype=np.float32)
        correlation = 0.0
        converged = False

    # Scale warp matrix back to original resolution
    if settings.downscale > 1:
        warp_matrix[0, 2] *= settings.downscale
        warp_matrix[1, 2] *= settings.downscale

    return RegistrationResult(
        warp_matrix=warp_matrix,
        correlation=correlation,
        converged=converged,
        model=settings.model,
        iterations=settings.ecc_iters,
    )


def apply_registration(
    image: np.ndarray,
    result: RegistrationResult,
    output_shape: Optional[Tuple[int, int]] = None
) -> np.ndarray:
    """
    Apply registration transform to an image.

    Args:
        image: Input image (2D or 3D with channels last).
        result: Registration result from compute_registration().
        output_shape: Output (height, width). If None, uses input shape.

    Returns:
        Registered image with same dtype as input.
    """
    try:
        import cv2
    except ImportError:
        raise ImportError(
            "OpenCV is required for registration. "
            "Install with: pip install opencv-python"
        )

    if output_shape is None:
        output_shape = image.shape[:2]

    # Apply warp
    registered = cv2.warpAffine(
        image,
        result.warp_matrix,
        (output_shape[1], output_shape[0]),  # OpenCV uses (width, height)
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=0
    )

    return registered


def _prepare_for_registration(image: np.ndarray) -> np.ndarray:
    """
    Prepare image for ECC registration.

    Converts to uint8 grayscale if needed.
    """
    # Handle multi-channel
    if image.ndim == 3:
        from .utils import ensure_grayscale
        image = ensure_grayscale(image)

    # Normalize to uint8
    if image.dtype != np.uint8:
        from .utils import normalize_to_uint8
        image = normalize_to_uint8(image)

    return image


def compute_phase_correlation_shift(
    reference: np.ndarray,
    target: np.ndarray
) -> Tuple[float, float]:
    """
    Compute translation shift using phase correlation.

    Useful for fast drift correction when rotation/scale is not needed.

    Args:
        reference: Reference image.
        target: Target image.

    Returns:
        Tuple of (dy, dx) pixel shifts.
    """
    try:
        import cv2
    except ImportError:
        raise ImportError(
            "OpenCV is required for phase correlation. "
            "Install with: pip install opencv-python"
        )

    ref = _prepare_for_registration(reference).astype(np.float32)
    tgt = _prepare_for_registration(target).astype(np.float32)

    shift, response = cv2.phaseCorrelate(ref, tgt)

    return (shift[1], shift[0])  # Return as (dy, dx)


class FrameRegistration:
    """
    Helper class for registering multiple frames.

    Computes transform once from representative frame, applies to all.
    """

    def __init__(
        self,
        reference: np.ndarray,
        settings: RegistrationSettings
    ):
        """
        Initialize with reference image.

        Args:
            reference: Reference (structure) image.
            settings: Registration settings.
        """
        self.reference = reference
        self.settings = settings
        self._result: Optional[RegistrationResult] = None
        self._initialized = False

    def initialize(self, representative_frame: np.ndarray) -> RegistrationResult:
        """
        Compute registration from representative recording frame.

        Args:
            representative_frame: A frame from the recording to align.

        Returns:
            RegistrationResult.
        """
        if not self.settings.enabled:
            logger.info("Registration disabled, using identity transform")
            self._result = RegistrationResult(
                warp_matrix=np.eye(2, 3, dtype=np.float32),
                correlation=1.0,
                converged=True,
                model="identity",
                iterations=0,
            )
        else:
            self._result = compute_registration(
                self.reference,
                representative_frame,
                self.settings
            )

        self._initialized = True
        return self._result

    def apply(self, frame: np.ndarray) -> np.ndarray:
        """
        Apply computed registration to a frame.

        Args:
            frame: Input frame.

        Returns:
            Registered frame.

        Raises:
            RuntimeError: If initialize() not called first.
        """
        if not self._initialized:
            raise RuntimeError("Must call initialize() before apply()")

        if not self.settings.enabled:
            return frame

        return apply_registration(
            frame,
            self._result,
            output_shape=self.reference.shape[:2]
        )

    @property
    def result(self) -> Optional[RegistrationResult]:
        """Get registration result (None if not initialized)."""
        return self._result
