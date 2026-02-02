"""
Annotation rendering for overlay_render.

Draws "ODOR ON" / "ODOR OFF" annotation box on frames.
"""

import logging
from typing import Literal, Tuple

import numpy as np

from .config import AnnotationSettings

logger = logging.getLogger(__name__)


def draw_odor_annotation(
    frame: np.ndarray,
    is_odor_on: bool,
    settings: AnnotationSettings,
    frame_idx: int = 0
) -> np.ndarray:
    """
    Draw odor state annotation on a frame.

    Args:
        frame: RGB frame (H, W, 3) as uint8.
        is_odor_on: Whether odor is currently ON.
        settings: Annotation settings.
        frame_idx: Current frame index (for debugging).

    Returns:
        Annotated frame (modified in-place).
    """
    try:
        import cv2
    except ImportError:
        raise ImportError(
            "OpenCV is required for annotation. "
            "Install with: pip install opencv-python"
        )

    # Check if we should draw anything
    if not is_odor_on and not settings.show_box_when_off:
        return frame

    # Get text and colors
    if is_odor_on:
        text = settings.text.on
        box_color = (255, 255, 255)  # White box
        text_color = (0, 0, 0)       # Black text
    else:
        text = settings.text.off
        box_color = (80, 80, 80)     # Gray box
        text_color = (200, 200, 200) # Light gray text

    # Calculate box position
    h, w = frame.shape[:2]
    box = settings.box

    x1, y1, x2, y2 = _compute_box_coords(
        frame_height=h,
        frame_width=w,
        box_width=box.width_px,
        box_height=box.height_px,
        margin=box.margin_px,
        anchor=box.anchor
    )

    # Draw filled rectangle
    cv2.rectangle(frame, (x1, y1), (x2, y2), box_color, thickness=-1)

    # Draw border
    border_color = (0, 0, 0) if is_odor_on else (100, 100, 100)
    cv2.rectangle(frame, (x1, y1), (x2, y2), border_color, thickness=2)

    # Calculate text position (centered in box)
    font = cv2.FONT_HERSHEY_SIMPLEX
    font_scale = settings.text.font_scale
    thickness = settings.text.thickness

    (text_width, text_height), baseline = cv2.getTextSize(
        text, font, font_scale, thickness
    )

    text_x = x1 + (box.width_px - text_width) // 2
    text_y = y1 + (box.height_px + text_height) // 2

    # Draw text
    cv2.putText(
        frame,
        text,
        (text_x, text_y),
        font,
        font_scale,
        text_color,
        thickness,
        cv2.LINE_AA
    )

    return frame


def _compute_box_coords(
    frame_height: int,
    frame_width: int,
    box_width: int,
    box_height: int,
    margin: int,
    anchor: Literal["bottom_left", "bottom_right", "top_left", "top_right"]
) -> Tuple[int, int, int, int]:
    """
    Compute box coordinates based on anchor position.

    Returns:
        (x1, y1, x2, y2) - top-left and bottom-right corners.
    """
    if anchor == "bottom_left":
        x1 = margin
        y1 = frame_height - box_height - margin
    elif anchor == "bottom_right":
        x1 = frame_width - box_width - margin
        y1 = frame_height - box_height - margin
    elif anchor == "top_left":
        x1 = margin
        y1 = margin
    elif anchor == "top_right":
        x1 = frame_width - box_width - margin
        y1 = margin
    else:
        raise ValueError(f"Unknown anchor: {anchor}")

    x2 = x1 + box_width
    y2 = y1 + box_height

    # Clamp to frame bounds
    x1 = max(0, x1)
    y1 = max(0, y1)
    x2 = min(frame_width, x2)
    y2 = min(frame_height, y2)

    return x1, y1, x2, y2


def draw_frame_counter(
    frame: np.ndarray,
    frame_idx: int,
    total_frames: int,
    position: str = "top_right",
    margin: int = 10
) -> np.ndarray:
    """
    Draw frame counter on a frame (optional utility).

    Args:
        frame: RGB frame as uint8.
        frame_idx: Current frame index (0-based).
        total_frames: Total number of frames.
        position: Position of counter.
        margin: Margin from edge in pixels.

    Returns:
        Annotated frame.
    """
    try:
        import cv2
    except ImportError:
        raise ImportError(
            "OpenCV is required for annotation. "
            "Install with: pip install opencv-python"
        )

    text = f"{frame_idx + 1}/{total_frames}"

    font = cv2.FONT_HERSHEY_SIMPLEX
    font_scale = 0.7
    thickness = 2

    (text_width, text_height), baseline = cv2.getTextSize(
        text, font, font_scale, thickness
    )

    h, w = frame.shape[:2]

    if position == "top_right":
        x = w - text_width - margin
        y = text_height + margin
    elif position == "top_left":
        x = margin
        y = text_height + margin
    elif position == "bottom_right":
        x = w - text_width - margin
        y = h - margin
    elif position == "bottom_left":
        x = margin
        y = h - margin
    else:
        x = margin
        y = text_height + margin

    # Draw background
    cv2.rectangle(
        frame,
        (x - 5, y - text_height - 5),
        (x + text_width + 5, y + 5),
        (0, 0, 0),
        thickness=-1
    )

    # Draw text
    cv2.putText(
        frame,
        text,
        (x, y),
        font,
        font_scale,
        (255, 255, 255),
        thickness,
        cv2.LINE_AA
    )

    return frame


def draw_odor_name(
    frame: np.ndarray,
    odor_name: str,
    position: str = "top_center",
    margin: int = 20
) -> np.ndarray:
    """
    Draw odor name on a frame (optional utility).

    Args:
        frame: RGB frame as uint8.
        odor_name: Name of the odor.
        position: Position of text.
        margin: Margin from edge in pixels.

    Returns:
        Annotated frame.
    """
    try:
        import cv2
    except ImportError:
        raise ImportError(
            "OpenCV is required for annotation. "
            "Install with: pip install opencv-python"
        )

    font = cv2.FONT_HERSHEY_SIMPLEX
    font_scale = 1.0
    thickness = 2

    (text_width, text_height), baseline = cv2.getTextSize(
        odor_name, font, font_scale, thickness
    )

    h, w = frame.shape[:2]

    if position == "top_center":
        x = (w - text_width) // 2
        y = text_height + margin
    else:
        x = margin
        y = text_height + margin

    # Draw background
    cv2.rectangle(
        frame,
        (x - 10, y - text_height - 10),
        (x + text_width + 10, y + 10),
        (0, 0, 0),
        thickness=-1
    )

    # Draw text
    cv2.putText(
        frame,
        odor_name,
        (x, y),
        font,
        font_scale,
        (255, 255, 0),  # Yellow
        thickness,
        cv2.LINE_AA
    )

    return frame
