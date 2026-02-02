"""
overlay_render - View-only visualization pipeline for neuroscience imaging data.

This package provides tools for:
- Loading structure images and time-series recordings
- Optional rigid/affine registration
- View-only brightness/contrast adjustments
- Overlay video rendering with odor annotation
"""

__version__ = "0.1.0"
__author__ = "RamanLab"

from .config import OverlayConfig, load_config
from .loaders import load_structure, load_recording, RecordingIterator
from .registration import compute_registration, apply_registration
from .view_scaling import scale_view
from .overlay import composite_frame
from .annotation import draw_odor_annotation
from .timing import extract_odor_timing
from .writer import VideoWriter
from .report import generate_report
from .discovery import discover_experiment, TrialInfo, ExperimentInfo

__all__ = [
    "OverlayConfig",
    "load_config",
    "load_structure",
    "load_recording",
    "RecordingIterator",
    "compute_registration",
    "apply_registration",
    "scale_view",
    "composite_frame",
    "draw_odor_annotation",
    "extract_odor_timing",
    "VideoWriter",
    "generate_report",
    "discover_experiment",
    "TrialInfo",
    "ExperimentInfo",
]
