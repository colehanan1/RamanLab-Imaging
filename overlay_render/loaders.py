"""
Image and video loaders for overlay_render.

Supports:
- Structure images: TIFF, PNG
- Recordings: Multi-page TIFF stacks, MP4, AVI
- Extensible design for future ND2/HDF5 support
"""

import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Iterator, Optional, Tuple, Union

import numpy as np

logger = logging.getLogger(__name__)


# =============================================================================
# Structure Image Loading
# =============================================================================

def load_structure(
    path: Union[str, Path],
    channel: Optional[int] = None
) -> np.ndarray:
    """
    Load structure image (RFP reference) as grayscale.

    Args:
        path: Path to structure image (.tif, .tiff, .png).
        channel: If multi-channel, which channel to use (0-indexed).
                 If None, converts to grayscale.

    Returns:
        2D numpy array (grayscale) with original dtype.

    Raises:
        FileNotFoundError: If file doesn't exist.
        ValueError: If format is not supported.
        RuntimeError: If loading fails.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Structure image not found: {path}")

    suffix = path.suffix.lower()
    logger.info(f"Loading structure image: {path}")

    if suffix in (".tif", ".tiff"):
        image = _load_tiff_single(path)
    elif suffix == ".png":
        image = _load_png(path)
    else:
        raise ValueError(
            f"Unsupported structure format: {suffix}. "
            f"Supported: .tif, .tiff, .png"
        )

    # Handle multi-channel images
    if image.ndim == 3:
        if channel is not None:
            if channel >= image.shape[2]:
                raise ValueError(
                    f"Channel {channel} requested but image only has "
                    f"{image.shape[2]} channels"
                )
            image = image[:, :, channel]
            logger.info(f"Using channel {channel}")
        else:
            # Convert to grayscale
            from .utils import ensure_grayscale
            image = ensure_grayscale(image)
            logger.info("Converted multi-channel to grayscale")

    logger.info(f"Structure loaded: shape={image.shape}, dtype={image.dtype}")
    return image


def _load_tiff_single(path: Path) -> np.ndarray:
    """Load a single TIFF image (first page if multi-page)."""
    try:
        import tifffile
    except ImportError:
        raise ImportError(
            "tifffile is required for TIFF loading. "
            "Install with: pip install tifffile"
        )

    with tifffile.TiffFile(path) as tif:
        # Get first page
        image = tif.pages[0].asarray()

    return image


def _load_png(path: Path) -> np.ndarray:
    """Load a PNG image."""
    try:
        import imageio.v3 as iio
    except ImportError:
        raise ImportError(
            "imageio is required for PNG loading. "
            "Install with: pip install imageio"
        )

    return iio.imread(path)


# =============================================================================
# Recording Loading (Abstract Interface)
# =============================================================================

class RecordingIterator(ABC):
    """
    Abstract base class for recording frame iterators.

    Provides a consistent interface for iterating over frames from
    different source formats (TIFF stack, MP4, etc.).
    """

    @property
    @abstractmethod
    def n_frames(self) -> int:
        """Total number of frames."""
        pass

    @property
    @abstractmethod
    def frame_shape(self) -> Tuple[int, int]:
        """Shape of each frame (height, width)."""
        pass

    @property
    @abstractmethod
    def dtype(self) -> np.dtype:
        """Data type of frames."""
        pass

    @property
    def fps(self) -> Optional[float]:
        """Frames per second if available from metadata."""
        return None

    @abstractmethod
    def get_frame(self, index: int) -> np.ndarray:
        """
        Get a specific frame by index.

        Args:
            index: Frame index (0-based).

        Returns:
            2D grayscale frame.

        Raises:
            IndexError: If index out of range.
        """
        pass

    @abstractmethod
    def __iter__(self) -> Iterator[np.ndarray]:
        """Iterate over all frames."""
        pass

    def __len__(self) -> int:
        return self.n_frames

    def close(self) -> None:
        """Release resources. Override in subclasses if needed."""
        pass

    def __enter__(self) -> "RecordingIterator":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()


# =============================================================================
# TIFF Stack Recording
# =============================================================================

class TiffStackIterator(RecordingIterator):
    """Iterator for multi-page TIFF stacks."""

    def __init__(
        self,
        path: Union[str, Path],
        channel: int = 0
    ):
        """
        Initialize TIFF stack iterator.

        Args:
            path: Path to TIFF stack file.
            channel: Channel to use if multi-channel (0-indexed).
        """
        try:
            import tifffile
        except ImportError:
            raise ImportError(
                "tifffile is required for TIFF loading. "
                "Install with: pip install tifffile"
            )

        self.path = Path(path)
        self.channel = channel
        self._tif = tifffile.TiffFile(self.path)

        # Determine frame structure
        self._pages = self._tif.pages
        self._n_frames = len(self._pages)

        if self._n_frames == 0:
            raise ValueError(f"TIFF file has no pages: {path}")

        # Get frame info from first page
        first_page = self._pages[0].asarray()
        self._original_shape = first_page.shape
        self._dtype = first_page.dtype

        # Handle multi-channel
        if first_page.ndim == 2:
            self._frame_shape = first_page.shape
            self._is_multichannel = False
        elif first_page.ndim == 3:
            self._frame_shape = first_page.shape[:2]
            self._is_multichannel = True
            if channel >= first_page.shape[2]:
                raise ValueError(
                    f"Channel {channel} requested but frames only have "
                    f"{first_page.shape[2]} channels"
                )
        else:
            raise ValueError(f"Unexpected frame dimensions: {first_page.shape}")

        logger.info(
            f"TIFF stack loaded: {self._n_frames} frames, "
            f"shape={self._frame_shape}, dtype={self._dtype}"
        )

    @property
    def n_frames(self) -> int:
        return self._n_frames

    @property
    def frame_shape(self) -> Tuple[int, int]:
        return self._frame_shape

    @property
    def dtype(self) -> np.dtype:
        return self._dtype

    def get_frame(self, index: int) -> np.ndarray:
        if index < 0 or index >= self._n_frames:
            raise IndexError(
                f"Frame index {index} out of range [0, {self._n_frames})"
            )

        frame = self._pages[index].asarray()

        if self._is_multichannel:
            frame = frame[:, :, self.channel]

        return frame

    def __iter__(self) -> Iterator[np.ndarray]:
        for i in range(self._n_frames):
            yield self.get_frame(i)

    def close(self) -> None:
        if hasattr(self, "_tif") and self._tif is not None:
            self._tif.close()
            self._tif = None


# =============================================================================
# TIFF Sequence (Individual Frame Files)
# =============================================================================

class TiffSequenceIterator(RecordingIterator):
    """Iterator for a directory of individual TIFF frame files."""

    def __init__(
        self,
        path: Union[str, Path],
        channel: int = 0,
        pattern: str = "frame_*.tif"
    ):
        """
        Initialize TIFF sequence iterator.

        Args:
            path: Path to directory containing TIFF frames.
            channel: Channel to use if multi-channel (0-indexed).
            pattern: Glob pattern for frame files (default: frame_*.tif).
        """
        try:
            import tifffile
        except ImportError:
            raise ImportError(
                "tifffile is required for TIFF loading. "
                "Install with: pip install tifffile"
            )

        self.path = Path(path)
        self.channel = channel
        self._tifffile = tifffile

        if not self.path.is_dir():
            raise ValueError(f"Expected directory, got file: {path}")

        # Find all frame files and sort numerically
        self._frame_files = sorted(
            self.path.glob(pattern),
            key=lambda p: self._extract_frame_number(p)
        )

        # Try alternate patterns if no files found
        if not self._frame_files:
            for alt_pattern in ["*.tif", "*.tiff", "img_*.tif", "image_*.tif"]:
                self._frame_files = sorted(
                    self.path.glob(alt_pattern),
                    key=lambda p: self._extract_frame_number(p)
                )
                if self._frame_files:
                    logger.info(f"Found frames with pattern: {alt_pattern}")
                    break

        if not self._frame_files:
            raise ValueError(f"No TIFF frames found in {path}")

        self._n_frames = len(self._frame_files)

        # Get frame info from first frame
        first_frame = tifffile.imread(self._frame_files[0])
        self._original_shape = first_frame.shape
        self._dtype = first_frame.dtype

        # Handle multi-channel
        if first_frame.ndim == 2:
            self._frame_shape = first_frame.shape
            self._is_multichannel = False
        elif first_frame.ndim == 3:
            self._frame_shape = first_frame.shape[:2]
            self._is_multichannel = True
            if channel >= first_frame.shape[2]:
                raise ValueError(
                    f"Channel {channel} requested but frames only have "
                    f"{first_frame.shape[2]} channels"
                )
        else:
            raise ValueError(f"Unexpected frame dimensions: {first_frame.shape}")

        logger.info(
            f"TIFF sequence loaded: {self._n_frames} frames from {path}, "
            f"shape={self._frame_shape}, dtype={self._dtype}"
        )

    @staticmethod
    def _extract_frame_number(path: Path) -> int:
        """Extract numeric frame number from filename for sorting."""
        import re
        numbers = re.findall(r'\d+', path.stem)
        if numbers:
            return int(numbers[-1])  # Use last number in filename
        return 0

    @property
    def n_frames(self) -> int:
        return self._n_frames

    @property
    def frame_shape(self) -> Tuple[int, int]:
        return self._frame_shape

    @property
    def dtype(self) -> np.dtype:
        return self._dtype

    def get_frame(self, index: int) -> np.ndarray:
        if index < 0 or index >= self._n_frames:
            raise IndexError(
                f"Frame index {index} out of range [0, {self._n_frames})"
            )

        frame = self._tifffile.imread(self._frame_files[index])

        if self._is_multichannel:
            frame = frame[:, :, self.channel]

        return frame

    def __iter__(self) -> Iterator[np.ndarray]:
        for i in range(self._n_frames):
            yield self.get_frame(i)

    def close(self) -> None:
        pass  # No resources to release for sequence


# =============================================================================
# Video Recording (MP4/AVI)
# =============================================================================

class VideoIterator(RecordingIterator):
    """Iterator for video files (MP4, AVI, etc.)."""

    def __init__(
        self,
        path: Union[str, Path],
        channel: int = 0
    ):
        """
        Initialize video iterator.

        Args:
            path: Path to video file.
            channel: Channel to use if color video (0=R, 1=G, 2=B for RGB).
        """
        try:
            import imageio.v3 as iio
            from imageio import get_reader
        except ImportError:
            raise ImportError(
                "imageio and imageio-ffmpeg are required for video loading. "
                "Install with: pip install imageio imageio-ffmpeg"
            )

        self.path = Path(path)
        self.channel = channel

        # Open video reader
        self._reader = get_reader(str(self.path))
        meta = self._reader.get_meta_data()

        self._n_frames = meta.get("nframes", meta.get("duration", 0))
        self._fps = meta.get("fps")

        # Handle infinite/unknown frame count
        if self._n_frames == float("inf") or self._n_frames <= 0:
            # Count frames manually (slow but necessary)
            logger.warning("Frame count unknown, counting frames...")
            self._reader.close()
            self._reader = get_reader(str(self.path))
            self._n_frames = sum(1 for _ in self._reader)
            self._reader.close()
            self._reader = get_reader(str(self.path))

        self._n_frames = int(self._n_frames)

        # Get frame info from first frame
        first_frame = self._reader.get_data(0)
        self._original_shape = first_frame.shape
        self._dtype = first_frame.dtype

        if first_frame.ndim == 2:
            self._frame_shape = first_frame.shape
            self._is_color = False
        elif first_frame.ndim == 3:
            self._frame_shape = first_frame.shape[:2]
            self._is_color = True
            if channel >= first_frame.shape[2]:
                raise ValueError(
                    f"Channel {channel} requested but frames only have "
                    f"{first_frame.shape[2]} channels"
                )
        else:
            raise ValueError(f"Unexpected frame dimensions: {first_frame.shape}")

        logger.info(
            f"Video loaded: {self._n_frames} frames, "
            f"shape={self._frame_shape}, dtype={self._dtype}, "
            f"fps={self._fps}"
        )

    @property
    def n_frames(self) -> int:
        return self._n_frames

    @property
    def frame_shape(self) -> Tuple[int, int]:
        return self._frame_shape

    @property
    def dtype(self) -> np.dtype:
        return self._dtype

    @property
    def fps(self) -> Optional[float]:
        return self._fps

    def get_frame(self, index: int) -> np.ndarray:
        if index < 0 or index >= self._n_frames:
            raise IndexError(
                f"Frame index {index} out of range [0, {self._n_frames})"
            )

        frame = self._reader.get_data(index)

        if self._is_color:
            # Convert to grayscale
            from .utils import ensure_grayscale
            frame = ensure_grayscale(frame)

        return frame

    def __iter__(self) -> Iterator[np.ndarray]:
        # Reset reader
        self._reader.close()
        from imageio import get_reader
        self._reader = get_reader(str(self.path))

        for i, frame in enumerate(self._reader):
            if i >= self._n_frames:
                break
            if self._is_color:
                from .utils import ensure_grayscale
                frame = ensure_grayscale(frame)
            yield frame

    def close(self) -> None:
        if hasattr(self, "_reader") and self._reader is not None:
            self._reader.close()
            self._reader = None


# =============================================================================
# Factory Function
# =============================================================================

def load_recording(
    path: Union[str, Path],
    channel: int = 0
) -> RecordingIterator:
    """
    Load a recording and return an appropriate iterator.

    Automatically detects format based on file extension.

    Args:
        path: Path to recording file.
        channel: Channel to use if multi-channel (0-indexed).

    Returns:
        RecordingIterator instance.

    Raises:
        FileNotFoundError: If file doesn't exist.
        ValueError: If format is not supported.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Recording not found: {path}")

    logger.info(f"Loading recording: {path}")

    # Check if path is a directory (sequence of individual frames)
    if path.is_dir():
        logger.info("Detected directory - loading as TIFF sequence")
        return TiffSequenceIterator(path, channel=channel)

    suffix = path.suffix.lower()

    if suffix in (".tif", ".tiff"):
        return TiffStackIterator(path, channel=channel)
    elif suffix in (".mp4", ".avi", ".mov", ".mkv"):
        return VideoIterator(path, channel=channel)
    else:
        raise ValueError(
            f"Unsupported recording format: {suffix}. "
            f"Supported: .tif, .tiff, .mp4, .avi, .mov, .mkv, or directory of TIFFs. "
            f"Future: .nd2, .h5"
        )


def get_recording_info(path: Union[str, Path]) -> dict:
    """
    Get information about a recording without loading all frames.

    Args:
        path: Path to recording file.

    Returns:
        Dict with keys: n_frames, frame_shape, dtype, fps (if available).
    """
    with load_recording(path) as rec:
        return {
            "n_frames": rec.n_frames,
            "frame_shape": rec.frame_shape,
            "dtype": str(rec.dtype),
            "fps": rec.fps,
        }
