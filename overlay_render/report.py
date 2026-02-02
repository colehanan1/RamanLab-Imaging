"""
Report generation for overlay_render.

Creates JSON reports capturing parameters, versions, and processing results.
"""

import json
import logging
import platform
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Union

from .config import OverlayConfig, config_to_dict
from .registration import RegistrationResult
from .timing import TimingResult
from .utils import compute_file_hash

logger = logging.getLogger(__name__)


def generate_report(
    config: OverlayConfig,
    timing_result: TimingResult,
    registration_result: Optional[RegistrationResult],
    output_video_path: Path,
    n_frames_processed: int,
    fps_used: float,
    processing_time_seconds: float,
    additional_info: Optional[Dict[str, Any]] = None
) -> dict:
    """
    Generate a run report capturing all processing parameters and results.

    Args:
        config: Configuration used for processing.
        timing_result: Odor timing extraction result.
        registration_result: Registration result (if registration was done).
        output_video_path: Path to output video.
        n_frames_processed: Number of frames processed.
        fps_used: FPS used for video.
        processing_time_seconds: Total processing time.
        additional_info: Any additional info to include.

    Returns:
        Report dictionary.
    """
    report = {
        "meta": {
            "tool": "overlay_render",
            "version": _get_version(),
            "timestamp": datetime.now().isoformat(),
            "python_version": sys.version,
            "platform": platform.platform(),
        },
        "inputs": {
            "structure_path": str(config.structure_path),
            "structure_hash": _safe_hash(config.structure_path),
            "recording_path": str(config.recording_path),
            "recording_hash": _safe_hash(config.recording_path),
        },
        "config": config_to_dict(config),
        "processing": {
            "n_frames_processed": n_frames_processed,
            "fps_used": fps_used,
            "processing_time_seconds": round(processing_time_seconds, 2),
            "frames_per_second_processing": round(
                n_frames_processed / max(0.01, processing_time_seconds), 2
            ),
        },
        "timing": timing_result.to_dict(),
        "registration": None,
        "output": {
            "video_path": str(output_video_path),
            "video_hash": _safe_hash(output_video_path) if output_video_path.exists() else None,
        },
    }

    # Add registration info if available
    if registration_result is not None:
        report["registration"] = registration_result.to_dict()

    # Add optional CSV/JSON paths and hashes
    if config.frames_csv_path is not None:
        report["inputs"]["frames_csv_path"] = str(config.frames_csv_path)
        report["inputs"]["frames_csv_hash"] = _safe_hash(config.frames_csv_path)

    if config.metadata_json_path is not None:
        report["inputs"]["metadata_json_path"] = str(config.metadata_json_path)
        report["inputs"]["metadata_json_hash"] = _safe_hash(config.metadata_json_path)

    # Add any additional info
    if additional_info:
        report["additional"] = additional_info

    # Add dependency versions
    report["dependencies"] = _get_dependency_versions()

    return report


def save_report(
    report: dict,
    output_path: Union[str, Path]
) -> None:
    """
    Save report to JSON file.

    Args:
        report: Report dictionary.
        output_path: Path to save JSON.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w") as f:
        json.dump(report, f, indent=2)

    logger.info(f"Report saved: {output_path}")


def _get_version() -> str:
    """Get overlay_render version."""
    try:
        from . import __version__
        return __version__
    except ImportError:
        return "unknown"


def _safe_hash(path: Union[str, Path]) -> Optional[str]:
    """Compute file hash, returning None on error."""
    try:
        return compute_file_hash(path)
    except Exception as e:
        logger.warning(f"Failed to hash {path}: {e}")
        return None


def _get_dependency_versions() -> dict:
    """Get versions of key dependencies."""
    versions = {}

    # NumPy
    try:
        import numpy as np
        versions["numpy"] = np.__version__
    except ImportError:
        versions["numpy"] = None

    # OpenCV
    try:
        import cv2
        versions["opencv"] = cv2.__version__
    except ImportError:
        versions["opencv"] = None

    # imageio
    try:
        import imageio
        versions["imageio"] = imageio.__version__
    except ImportError:
        versions["imageio"] = None

    # tifffile
    try:
        import tifffile
        versions["tifffile"] = tifffile.__version__
    except ImportError:
        versions["tifffile"] = None

    # PyYAML
    try:
        import yaml
        versions["pyyaml"] = yaml.__version__
    except ImportError:
        versions["pyyaml"] = None

    return versions


def format_report_summary(report: dict) -> str:
    """
    Format report as human-readable summary string.

    Args:
        report: Report dictionary.

    Returns:
        Formatted string summary.
    """
    lines = [
        "=" * 60,
        "OVERLAY RENDER REPORT",
        "=" * 60,
        "",
        f"Timestamp: {report['meta']['timestamp']}",
        f"Version: {report['meta']['version']}",
        "",
        "INPUTS:",
        f"  Structure: {report['inputs']['structure_path']}",
        f"  Recording: {report['inputs']['recording_path']}",
        "",
        "PROCESSING:",
        f"  Frames: {report['processing']['n_frames_processed']}",
        f"  FPS: {report['processing']['fps_used']}",
        f"  Time: {report['processing']['processing_time_seconds']:.1f}s",
        f"  Speed: {report['processing']['frames_per_second_processing']:.1f} fps",
        "",
        "TIMING:",
        f"  Source: {report['timing']['source']}",
        f"  Intervals: {len(report['timing']['intervals'])}",
    ]

    # Add odor intervals
    for interval in report['timing']['intervals'][:5]:  # Show first 5
        name = interval.get('odor_name', 'unnamed')
        lines.append(
            f"    [{interval['start_frame']}-{interval['end_frame']}] {name}"
        )
    if len(report['timing']['intervals']) > 5:
        lines.append(f"    ... and {len(report['timing']['intervals']) - 5} more")

    # Registration
    if report['registration']:
        reg = report['registration']
        lines.extend([
            "",
            "REGISTRATION:",
            f"  Model: {reg['model']}",
            f"  Converged: {reg['converged']}",
            f"  Correlation: {reg['correlation']:.4f}",
        ])

    # Output
    lines.extend([
        "",
        "OUTPUT:",
        f"  Video: {report['output']['video_path']}",
        "",
        "=" * 60,
    ])

    return "\n".join(lines)
