"""
Timing and odor state extraction for overlay_render.

Parses CSV and JSON metadata to determine per-frame odor on/off state.

Priority:
1. CSV per-frame odor state (if matches frame count)
2. JSON intervals with start/end frame indices
3. JSON times + fps -> frame indices (validated)
"""

import csv
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple, Union

import numpy as np

from .config import TimingSettings

logger = logging.getLogger(__name__)


@dataclass
class OdorInterval:
    """
    Represents an interval when odor is ON.

    Attributes:
        start_frame: First frame with odor ON (inclusive).
        end_frame: Last frame with odor ON (inclusive).
        odor_name: Optional odor identifier.
    """
    start_frame: int
    end_frame: int
    odor_name: Optional[str] = None

    def contains(self, frame_idx: int) -> bool:
        """Check if frame is within this interval."""
        return self.start_frame <= frame_idx <= self.end_frame

    def to_dict(self) -> dict:
        """Convert to serializable dictionary."""
        return {
            "start_frame": self.start_frame,
            "end_frame": self.end_frame,
            "odor_name": self.odor_name,
        }


@dataclass
class TimingResult:
    """
    Result of timing extraction.

    Attributes:
        intervals: List of odor ON intervals.
        source: Source of timing info ('csv', 'json', or 'none').
        fps_used: FPS used for time->frame conversion (if applicable).
        n_frames: Total number of frames.
        warnings: Any warnings generated during parsing.
    """
    intervals: List[OdorInterval]
    source: str
    fps_used: Optional[float]
    n_frames: int
    warnings: List[str]

    def is_odor_on(self, frame_idx: int) -> bool:
        """Check if odor is ON at given frame."""
        return any(interval.contains(frame_idx) for interval in self.intervals)

    def get_odor_mask(self) -> np.ndarray:
        """Get boolean array of odor state for all frames."""
        mask = np.zeros(self.n_frames, dtype=bool)
        for interval in self.intervals:
            start = max(0, interval.start_frame)
            end = min(self.n_frames - 1, interval.end_frame)
            mask[start:end + 1] = True
        return mask

    def to_dict(self) -> dict:
        """Convert to serializable dictionary."""
        return {
            "intervals": [i.to_dict() for i in self.intervals],
            "source": self.source,
            "fps_used": self.fps_used,
            "n_frames": self.n_frames,
            "warnings": self.warnings,
        }


def extract_odor_timing(
    n_frames: int,
    csv_path: Optional[Path] = None,
    json_path: Optional[Path] = None,
    settings: Optional[TimingSettings] = None
) -> TimingResult:
    """
    Extract odor timing from CSV and/or JSON metadata.

    Priority:
    1. CSV per-frame odor state if present and matches frame count
    2. JSON intervals with start/end frame indices
    3. JSON times + fps -> frame indices

    Args:
        n_frames: Total number of recording frames.
        csv_path: Path to frames CSV (optional).
        json_path: Path to metadata JSON (optional).
        settings: Timing settings from config.

    Returns:
        TimingResult with odor intervals.

    Raises:
        ValueError: If timing cannot be determined and is required.
    """
    if settings is None:
        settings = TimingSettings()

    warnings = []

    # Determine source preference
    source_pref = settings.odor_source
    logger.info(f"Extracting odor timing (preference: {source_pref})")

    # Try CSV first (if auto or csv)
    csv_result = None
    if csv_path is not None and source_pref in ("auto", "csv"):
        csv_result = _try_parse_csv(
            csv_path,
            n_frames,
            settings.frame_index_column_candidates,
            settings.odor_on_column_candidates
        )
        if csv_result is not None:
            intervals, csv_warnings = csv_result
            warnings.extend(csv_warnings)
            logger.info(f"Using CSV timing: {len(intervals)} odor intervals")
            return TimingResult(
                intervals=intervals,
                source="csv",
                fps_used=None,
                n_frames=n_frames,
                warnings=warnings,
            )
        else:
            warnings.append("CSV parsing failed or frame count mismatch")

    # Try JSON (if auto or json)
    json_result = None
    if json_path is not None and source_pref in ("auto", "json"):
        json_result = _try_parse_json(
            json_path,
            n_frames,
            settings.fps
        )
        if json_result is not None:
            intervals, fps_used, json_warnings = json_result
            warnings.extend(json_warnings)
            logger.info(f"Using JSON timing: {len(intervals)} odor intervals")
            return TimingResult(
                intervals=intervals,
                source="json",
                fps_used=fps_used,
                n_frames=n_frames,
                warnings=warnings,
            )
        else:
            warnings.append("JSON parsing failed")

    # No timing found
    logger.warning("No odor timing found, defaulting to always OFF")
    warnings.append("No valid timing source found")

    return TimingResult(
        intervals=[],
        source="none",
        fps_used=None,
        n_frames=n_frames,
        warnings=warnings,
    )


def _try_parse_csv(
    csv_path: Path,
    n_frames: int,
    frame_idx_candidates: List[str],
    odor_on_candidates: List[str]
) -> Optional[Tuple[List[OdorInterval], List[str]]]:
    """
    Try to parse odor timing from CSV.

    Supports two formats:
    1. RamanLab timestamps.csv format with 'event', 'phase', 'frame' columns
    2. Simple per-frame CSV with odor_on column

    Returns None if parsing fails or frame count doesn't match.
    """
    warnings = []

    try:
        with open(csv_path, "r", newline="") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
    except Exception as e:
        logger.warning(f"Failed to read CSV: {e}")
        return None

    if not rows:
        logger.warning("CSV is empty")
        return None

    # Get column names
    columns = set(rows[0].keys())

    # Check for RamanLab timestamps.csv format (has 'event', 'phase', 'frame' columns)
    if "event" in columns and "phase" in columns and "frame" in columns:
        result = _parse_ramanlab_timestamps_csv(rows, n_frames)
        if result is not None:
            return result
        # Fall through to try other formats

    # Find odor column (for simple per-frame format)
    odor_col = None
    for candidate in odor_on_candidates:
        if candidate in columns:
            odor_col = candidate
            break

    if odor_col is None:
        logger.warning(f"No odor column found (tried: {odor_on_candidates})")
        return None

    # Check if frame index column exists or assume row order
    frame_col = None
    for candidate in frame_idx_candidates:
        if candidate in columns:
            frame_col = candidate
            break

    # Parse odor states
    odor_states = []
    for i, row in enumerate(rows):
        # Get frame index
        if frame_col:
            try:
                frame_idx = int(row[frame_col])
            except (ValueError, KeyError):
                frame_idx = i
        else:
            frame_idx = i

        # Get odor state
        try:
            value = row[odor_col]
            # Handle various true/false representations
            if isinstance(value, str):
                value = value.lower().strip()
                is_on = value in ("1", "true", "yes", "on")
            else:
                is_on = bool(int(value))
        except (ValueError, KeyError):
            is_on = False

        odor_states.append((frame_idx, is_on))

    # Check frame count
    if len(odor_states) != n_frames:
        warnings.append(
            f"CSV has {len(odor_states)} rows but recording has {n_frames} frames"
        )
        # Still try to use it if close
        if abs(len(odor_states) - n_frames) > n_frames * 0.1:
            logger.warning(f"Frame count mismatch too large, skipping CSV @{csv_path}")
            return None

    # Convert to intervals
    intervals = _states_to_intervals(odor_states)

    logger.info(f"Parsed {len(intervals)} odor intervals from CSV")
    return intervals, warnings


def _parse_ramanlab_timestamps_csv(
    rows: List[dict],
    n_frames: int
) -> Optional[Tuple[List[OdorInterval], List[str]]]:
    """
    Parse RamanLab timestamps.csv format.

    This format has:
    - event: "FRAME_0", "FRAME_1", ..., "TRIAL_START", "ODOR_CMD", "ESP_RX", etc.
    - phase: "BASELINE", "ODOR", "POST-ODOR", "PROTOCOL", "HOST", "ESP32"
    - frame: frame number (int)
    - odor: odor code like "OFM_H"

    Odor is ON when phase == "ODOR" for frame rows.
    """
    warnings = []

    # Filter to only FRAME_* rows
    frame_rows = []
    for row in rows:
        event = row.get("event", "")
        if event.startswith("FRAME_"):
            frame_rows.append(row)

    if not frame_rows:
        logger.warning("No FRAME_* rows found in timestamps CSV")
        return None

    # Check frame count
    csv_frame_count = len(frame_rows)
    if csv_frame_count != n_frames:
        warnings.append(
            f"CSV has {csv_frame_count} frame rows but recording has {n_frames} frames"
        )
        # Allow small mismatch (within 5%)
        if abs(csv_frame_count - n_frames) > n_frames * 0.05:
            logger.warning(
                f"Frame count mismatch: CSV has {csv_frame_count} frames, "
                f"recording has {n_frames} frames"
            )
            # Don't return None - still try to use it

    # Get odor name from first row
    odor_name = frame_rows[0].get("odor") if frame_rows else None

    # Parse odor states based on 'phase' column
    odor_states = []
    for row in frame_rows:
        try:
            frame_idx = int(row["frame"])
        except (ValueError, KeyError):
            continue

        phase = row.get("phase", "").upper()
        is_on = phase == "ODOR"
        odor_states.append((frame_idx, is_on))

    if not odor_states:
        logger.warning("No valid frame states parsed from timestamps CSV")
        return None

    # Convert to intervals
    intervals = _states_to_intervals(odor_states)

    # Add odor name to intervals
    if odor_name:
        for interval in intervals:
            interval.odor_name = odor_name

    logger.info(
        f"Parsed RamanLab timestamps.csv: {len(frame_rows)} frames, "
        f"{len(intervals)} odor intervals"
    )
    return intervals, warnings


def _try_parse_json(
    json_path: Path,
    n_frames: int,
    config_fps: Optional[float]
) -> Optional[Tuple[List[OdorInterval], Optional[float], List[str]]]:
    """
    Try to parse odor timing from JSON metadata.

    Returns None if parsing fails.
    """
    warnings = []

    try:
        with open(json_path, "r") as f:
            data = json.load(f)
    except Exception as e:
        logger.warning(f"Failed to read JSON: {e}")
        return None

    if not isinstance(data, dict):
        logger.warning("JSON is not a dictionary")
        return None

    # Try to find intervals with frame indices
    intervals = _parse_json_frame_intervals(data)
    if intervals:
        logger.info(f"Found {len(intervals)} frame-based intervals in JSON")
        return intervals, None, warnings

    # Try to find intervals with times (requires FPS)
    fps = config_fps
    if fps is None:
        # Try to get FPS from JSON (check multiple locations)
        fps = data.get("fps") or data.get("frame_rate") or data.get("framerate")
        # Check acquisition section (RamanLab format)
        if fps is None:
            acq = data.get("acquisition", {})
            if isinstance(acq, dict):
                fps = acq.get("fps_measured") or acq.get("fps_target") or acq.get("fps")
        if fps is not None:
            fps = float(fps)

    if fps is not None:
        intervals = _parse_json_time_intervals(data, fps, n_frames)
        if intervals:
            logger.info(f"Found {len(intervals)} time-based intervals in JSON (fps={fps})")
            return intervals, fps, warnings

    # Try odor_on/odor_off simple format
    intervals = _parse_json_simple_intervals(data, fps, n_frames)
    if intervals:
        return intervals, fps, warnings

    return None


def _parse_json_frame_intervals(data: dict) -> Optional[List[OdorInterval]]:
    """Parse JSON with frame-based odor intervals."""
    # Look for common patterns
    interval_keys = ["odor_intervals", "intervals", "odor_frames", "stimulus_intervals"]

    for key in interval_keys:
        if key in data and isinstance(data[key], list):
            intervals = []
            for item in data[key]:
                if isinstance(item, dict):
                    start = item.get("start_frame") or item.get("start")
                    end = item.get("end_frame") or item.get("end")
                    name = item.get("odor") or item.get("name") or item.get("odor_name")

                    if start is not None and end is not None:
                        try:
                            intervals.append(OdorInterval(
                                start_frame=int(start),
                                end_frame=int(end),
                                odor_name=name
                            ))
                        except (ValueError, TypeError):
                            continue

            if intervals:
                return intervals

    return None


def _parse_json_time_intervals(
    data: dict,
    fps: float,
    n_frames: int
) -> Optional[List[OdorInterval]]:
    """Parse JSON with time-based odor intervals."""
    interval_keys = ["odor_intervals", "intervals", "stimulus_times"]

    for key in interval_keys:
        if key in data and isinstance(data[key], list):
            intervals = []
            for item in data[key]:
                if isinstance(item, dict):
                    start_time = item.get("start_time") or item.get("onset")
                    end_time = item.get("end_time") or item.get("offset")
                    name = item.get("odor") or item.get("name")

                    if start_time is not None and end_time is not None:
                        try:
                            start_frame = int(float(start_time) * fps)
                            end_frame = int(float(end_time) * fps)

                            # Validate
                            if 0 <= start_frame < n_frames and start_frame <= end_frame:
                                end_frame = min(end_frame, n_frames - 1)
                                intervals.append(OdorInterval(
                                    start_frame=start_frame,
                                    end_frame=end_frame,
                                    odor_name=name
                                ))
                        except (ValueError, TypeError):
                            continue

            if intervals:
                return intervals

    return None


def _parse_json_simple_intervals(
    data: dict,
    fps: Optional[float],
    n_frames: int
) -> Optional[List[OdorInterval]]:
    """Parse simple on/off format from JSON."""
    # Get odor name if available
    odor_name = data.get("odor_name") or data.get("odor_code")

    # Check for simple on/off frame format at top level
    if "odor_on_frame" in data and "odor_off_frame" in data:
        try:
            start = int(data["odor_on_frame"])
            end = int(data["odor_off_frame"])
            if 0 <= start <= end < n_frames:
                return [OdorInterval(start, end, odor_name)]
        except (ValueError, TypeError):
            pass

    # Check in acquisition section (RamanLab format)
    acq = data.get("acquisition", {})
    if isinstance(acq, dict):
        start = acq.get("odor_frame_start")
        end = acq.get("odor_frame_end")
        if start is not None and end is not None:
            try:
                start = int(start)
                end = int(end)
                if 0 <= start <= end:
                    end = min(end, n_frames - 1) if n_frames > 0 else end
                    return [OdorInterval(start, end, odor_name)]
            except (ValueError, TypeError):
                pass

    # Check for odor_frame_start/end at top level
    if "odor_frame_start" in data and "odor_frame_end" in data:
        try:
            start = int(data["odor_frame_start"])
            end = int(data["odor_frame_end"])
            if 0 <= start <= end:
                end = min(end, n_frames - 1) if n_frames > 0 else end
                return [OdorInterval(start, end, odor_name)]
        except (ValueError, TypeError):
            pass

    # Check for simple on/off time format
    if fps is not None:
        if "odor_on_time" in data and "odor_off_time" in data:
            try:
                start = int(float(data["odor_on_time"]) * fps)
                end = int(float(data["odor_off_time"]) * fps)
                if 0 <= start <= end:
                    end = min(end, n_frames - 1)
                    return [OdorInterval(start, end)]
            except (ValueError, TypeError):
                pass

    return None


def _states_to_intervals(states: List[Tuple[int, bool]]) -> List[OdorInterval]:
    """
    Convert per-frame odor states to intervals.

    Args:
        states: List of (frame_idx, is_on) tuples.

    Returns:
        List of OdorInterval.
    """
    if not states:
        return []

    # Sort by frame index
    states = sorted(states, key=lambda x: x[0])

    intervals = []
    in_interval = False
    interval_start = 0

    for frame_idx, is_on in states:
        if is_on and not in_interval:
            # Start new interval
            interval_start = frame_idx
            in_interval = True
        elif not is_on and in_interval:
            # End interval
            intervals.append(OdorInterval(interval_start, frame_idx - 1))
            in_interval = False

    # Close any open interval
    if in_interval:
        intervals.append(OdorInterval(interval_start, states[-1][0]))

    return intervals


def create_manual_intervals(
    intervals: List[Tuple[int, int]],
    n_frames: int,
    odor_name: Optional[str] = None
) -> TimingResult:
    """
    Create timing result from manual interval specification.

    Args:
        intervals: List of (start_frame, end_frame) tuples.
        n_frames: Total frame count.
        odor_name: Optional odor identifier.

    Returns:
        TimingResult.
    """
    odor_intervals = [
        OdorInterval(start, end, odor_name)
        for start, end in intervals
    ]

    return TimingResult(
        intervals=odor_intervals,
        source="manual",
        fps_used=None,
        n_frames=n_frames,
        warnings=[],
    )
