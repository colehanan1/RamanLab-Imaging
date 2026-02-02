"""
Utility functions for overlay_render.

Provides logging setup, file hashing, and common helpers.
"""

import hashlib
import logging
import sys
from pathlib import Path
from typing import Optional, Union

import numpy as np


def setup_logging(
    level: int = logging.INFO,
    log_file: Optional[Path] = None
) -> logging.Logger:
    """
    Set up structured logging for the package.

    Args:
        level: Logging level (e.g., logging.INFO, logging.DEBUG).
        log_file: Optional path to write logs to file.

    Returns:
        Configured root logger for the package.
    """
    logger = logging.getLogger("overlay_render")
    logger.setLevel(level)

    # Clear existing handlers
    logger.handlers.clear()

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_format = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    console_handler.setFormatter(console_format)
    logger.addHandler(console_handler)

    # File handler if requested
    if log_file is not None:
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(level)
        file_format = logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(name)s:%(lineno)d | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        file_handler.setFormatter(file_format)
        logger.addHandler(file_handler)

    return logger


def compute_file_hash(
    filepath: Union[str, Path],
    algorithm: str = "sha256",
    chunk_size: int = 65536
) -> str:
    """
    Compute hash of a file for reproducibility tracking.

    Args:
        filepath: Path to file.
        algorithm: Hash algorithm ('sha256', 'md5', etc.).
        chunk_size: Size of chunks to read.

    Returns:
        Hexadecimal hash string.

    Raises:
        FileNotFoundError: If file doesn't exist.
        ValueError: If algorithm is not supported.
    """
    filepath = Path(filepath)
    if not filepath.exists():
        raise FileNotFoundError(f"Cannot hash non-existent file: {filepath}")

    try:
        hasher = hashlib.new(algorithm)
    except ValueError:
        raise ValueError(f"Unsupported hash algorithm: {algorithm}")

    with open(filepath, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            hasher.update(chunk)

    return hasher.hexdigest()


def normalize_to_uint8(
    image: np.ndarray,
    vmin: Optional[float] = None,
    vmax: Optional[float] = None
) -> np.ndarray:
    """
    Normalize image to uint8 range [0, 255].

    Args:
        image: Input image array.
        vmin: Minimum value for scaling. If None, uses image minimum.
        vmax: Maximum value for scaling. If None, uses image maximum.

    Returns:
        Normalized uint8 image.
    """
    img = image.astype(np.float64)

    if vmin is None:
        vmin = img.min()
    if vmax is None:
        vmax = img.max()

    if vmax <= vmin:
        # Handle flat images
        return np.zeros(img.shape, dtype=np.uint8)

    img = (img - vmin) / (vmax - vmin)
    img = np.clip(img * 255, 0, 255)

    return img.astype(np.uint8)


def ensure_grayscale(image: np.ndarray) -> np.ndarray:
    """
    Convert image to grayscale if needed.

    Args:
        image: Input image (grayscale, RGB, or RGBA).

    Returns:
        2D grayscale image.
    """
    if image.ndim == 2:
        return image

    if image.ndim == 3:
        if image.shape[2] == 1:
            return image[:, :, 0]
        elif image.shape[2] == 3:
            # Standard luminosity formula
            return (
                0.299 * image[:, :, 0] +
                0.587 * image[:, :, 1] +
                0.114 * image[:, :, 2]
            ).astype(image.dtype)
        elif image.shape[2] == 4:
            # RGBA - ignore alpha, use RGB
            return (
                0.299 * image[:, :, 0] +
                0.587 * image[:, :, 1] +
                0.114 * image[:, :, 2]
            ).astype(image.dtype)

    raise ValueError(f"Cannot convert {image.ndim}D image with shape {image.shape} to grayscale")


def ensure_rgb(image: np.ndarray) -> np.ndarray:
    """
    Convert image to RGB if needed.

    Args:
        image: Input image (grayscale, RGB, or RGBA).

    Returns:
        3D RGB image with shape (H, W, 3).
    """
    if image.ndim == 2:
        # Grayscale to RGB
        return np.stack([image, image, image], axis=-1)

    if image.ndim == 3:
        if image.shape[2] == 1:
            # Single channel to RGB
            return np.concatenate([image, image, image], axis=-1)
        elif image.shape[2] == 3:
            return image
        elif image.shape[2] == 4:
            # RGBA to RGB
            return image[:, :, :3]

    raise ValueError(f"Cannot convert {image.ndim}D image with shape {image.shape} to RGB")


def get_representative_frame_index(n_frames: int, method: str = "median") -> int:
    """
    Get index of a representative frame for registration reference.

    Args:
        n_frames: Total number of frames.
        method: Selection method ('median', 'first', 'quarter').

    Returns:
        Frame index.
    """
    if n_frames <= 0:
        raise ValueError(f"n_frames must be positive, got {n_frames}")

    if method == "median":
        return n_frames // 2
    elif method == "first":
        return 0
    elif method == "quarter":
        return n_frames // 4
    else:
        raise ValueError(f"Unknown method: {method}")


def validate_frame_dimensions(
    frame: np.ndarray,
    reference: np.ndarray,
    name: str = "frame"
) -> None:
    """
    Validate that frame dimensions match reference.

    Args:
        frame: Frame to validate.
        reference: Reference frame.
        name: Name for error messages.

    Raises:
        ValueError: If dimensions don't match.
    """
    if frame.shape[:2] != reference.shape[:2]:
        raise ValueError(
            f"{name} dimensions {frame.shape[:2]} do not match "
            f"reference {reference.shape[:2]}"
        )
