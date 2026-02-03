"""
Video writing for overlay_render.

Uses imageio-ffmpeg for MP4 output with configurable quality.
"""

import logging
from pathlib import Path
from typing import Optional, Tuple, Union

import numpy as np

logger = logging.getLogger(__name__)


class VideoWriter:
    """
    Video writer using imageio-ffmpeg backend.

    Supports MP4 output with H.264 encoding.
    """

    def __init__(
        self,
        output_path: Union[str, Path],
        fps: float,
        frame_size: Optional[Tuple[int, int]] = None,
        quality: int = 8,
        codec: str = "libx264",
        pixel_format: str = "yuv420p"
    ):
        """
        Initialize video writer.

        Args:
            output_path: Path for output video file.
            fps: Frames per second.
            frame_size: (width, height) of frames. If None, inferred from first frame.
            quality: Quality level (1-10, higher is better, 8 is recommended).
            codec: Video codec (default: libx264 for H.264).
            pixel_format: Pixel format for compatibility (yuv420p for most players).
        """
        try:
            import imageio.v3 as iio
            from imageio import get_writer
        except ImportError:
            raise ImportError(
                "imageio and imageio-ffmpeg are required for video writing. "
                "Install with: pip install imageio imageio-ffmpeg"
            )

        self.output_path = Path(output_path)
        self.fps = fps
        self.frame_size = frame_size
        self.quality = quality
        self.codec = codec
        self.pixel_format = pixel_format

        self._writer = None
        self._frame_count = 0
        self._initialized = False

        # Ensure output directory exists
        self.output_path.parent.mkdir(parents=True, exist_ok=True)

        logger.info(f"VideoWriter initialized: {self.output_path}, fps={fps}")

    def _init_writer(self, first_frame: np.ndarray) -> None:
        """Initialize writer with first frame to get dimensions."""
        from imageio import get_writer

        if self.frame_size is None:
            h, w = first_frame.shape[:2]
            self.frame_size = (w, h)

        # FFmpeg output parameters - use codec and pixelformat kwargs instead of
        # output_params to avoid duplicate option warnings from imageio
        self._writer = get_writer(
            str(self.output_path),
            fps=self.fps,
            mode="I",
            format="FFMPEG",
            codec=self.codec,
            pixelformat=self.pixel_format,
            output_params=[
                "-crf", str(max(1, 51 - self.quality * 5)),  # Quality setting
                "-preset", "medium",  # Balance speed/quality
            ],
        )

        self._initialized = True
        logger.info(f"Video writer started: {self.frame_size[0]}x{self.frame_size[1]}")

    def write_frame(self, frame: np.ndarray) -> None:
        """
        Write a single frame to the video.

        Args:
            frame: RGB frame as uint8 array (H, W, 3).

        Raises:
            ValueError: If frame dimensions don't match.
        """
        if not self._initialized:
            self._init_writer(frame)

        # Validate frame
        if frame.dtype != np.uint8:
            frame = frame.astype(np.uint8)

        if frame.ndim == 2:
            # Convert grayscale to RGB
            frame = np.stack([frame, frame, frame], axis=-1)

        if frame.ndim != 3 or frame.shape[2] != 3:
            raise ValueError(
                f"Expected RGB frame (H, W, 3), got shape {frame.shape}"
            )

        h, w = frame.shape[:2]
        expected_w, expected_h = self.frame_size

        if w != expected_w or h != expected_h:
            raise ValueError(
                f"Frame size {w}x{h} doesn't match expected {expected_w}x{expected_h}"
            )

        self._writer.append_data(frame)
        self._frame_count += 1

    def close(self) -> None:
        """Close the video writer and finalize the file."""
        if self._writer is not None:
            self._writer.close()
            self._writer = None
            logger.info(
                f"Video saved: {self.output_path} "
                f"({self._frame_count} frames)"
            )

    def __enter__(self) -> "VideoWriter":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    @property
    def frame_count(self) -> int:
        """Number of frames written."""
        return self._frame_count


def save_thumbnail(
    frame: np.ndarray,
    output_path: Union[str, Path],
    format: str = "png"
) -> None:
    """
    Save a single frame as a thumbnail image.

    Args:
        frame: RGB frame as uint8 array.
        output_path: Output path for thumbnail.
        format: Image format (png, jpg, etc.).
    """
    try:
        import imageio.v3 as iio
    except ImportError:
        raise ImportError(
            "imageio is required for thumbnail saving. "
            "Install with: pip install imageio"
        )

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if frame.dtype != np.uint8:
        frame = frame.astype(np.uint8)

    iio.imwrite(output_path, frame)
    logger.info(f"Thumbnail saved: {output_path}")


def get_video_info(path: Union[str, Path]) -> dict:
    """
    Get information about a video file.

    Args:
        path: Path to video file.

    Returns:
        Dict with video metadata.
    """
    try:
        from imageio import get_reader
    except ImportError:
        raise ImportError(
            "imageio is required. Install with: pip install imageio"
        )

    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Video not found: {path}")

    reader = get_reader(str(path))
    meta = reader.get_meta_data()
    reader.close()

    return {
        "path": str(path),
        "size_mb": path.stat().st_size / (1024 * 1024),
        "fps": meta.get("fps"),
        "duration": meta.get("duration"),
        "nframes": meta.get("nframes"),
        "size": meta.get("size"),
        "codec": meta.get("codec"),
    }
