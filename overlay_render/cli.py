"""
Command-line interface for overlay_render.

Usage:
    # Process entire experiment folder (auto-discovery)
    python -m overlay_render --folder /path/to/experiment_folder

    # Process single trial with config
    python -m overlay_render --config path/to/config.yaml

    # Filter specific trials
    python -m overlay_render --folder /path/to/folder --filter OFM_E
"""

import argparse
import logging
import math
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

from .config import OverlayConfig, load_config
from .loaders import load_structure, load_recording
from .registration import FrameRegistration
from .bleach import precompute_bleach_corrector, format_preproc_banner
from .denoise import apply_denoise
from .dff import compute_f0, apply_dff, determine_baseline_frames
from .projections import determine_epochs, generate_projections
from .view_scaling import compute_global_scaling_params, scale_frame
from .timing import extract_odor_timing
from .annotation import draw_odor_annotation
from .overlay import OverlayRenderer
from .writer import VideoWriter, save_thumbnail, save_tiff_frame
from .report import generate_report, save_report, format_report_summary
from .utils import setup_logging, get_representative_frame_index

logger = logging.getLogger(__name__)


def parse_args(argv: Optional[List[str]] = None) -> Tuple[argparse.Namespace, Dict]:
    """
    Parse command-line arguments.

    Returns:
        Tuple of (parsed args, config overrides dict).
    """
    parser = argparse.ArgumentParser(
        prog="overlay_render",
        description="Render overlay video of recording over structure image.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Process entire experiment folder (recommended)
    python -m overlay_render --folder 2xOdor_Pannel_GH146xOr7a_Female_d_post0h_20260202_112312

    # Process with trial filter
    python -m overlay_render --folder /path/to/folder --filter OFM_E

    # Process single trial with config file
    python -m overlay_render --config experiment.yaml

    # With overrides
    python -m overlay_render --config experiment.yaml --view.gamma 1.2
        """
    )

    # Mode selection (mutually exclusive)
    mode_group = parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument(
        "--folder", "-f",
        type=Path,
        help="Path to experiment folder (auto-discovery mode)"
    )
    mode_group.add_argument(
        "--config", "-c",
        type=Path,
        help="Path to YAML configuration file"
    )

    # Folder mode options
    parser.add_argument(
        "--filter",
        type=str,
        default=None,
        help="Filter trials by name (e.g., 'OFM_E' for ethyl butyrate)"
    )
    parser.add_argument(
        "--structure-index",
        type=int,
        default=0,
        help="Which structure image to use (0-indexed, default: 0)"
    )
    parser.add_argument(
        "--output-dir", "-o",
        type=Path,
        default=None,
        help="Output directory (default: <folder>/output)"
    )
    parser.add_argument(
        "--reuse-output",
        action="store_true",
        help="Write into existing output directory (default: create run subfolder if output exists)"
    )
    parser.add_argument(
        "--combined-video",
        action="store_true",
        help="In folder mode, also render one synchronized grid video across all processed trials"
    )
    parser.add_argument(
        "--combined-sync",
        choices=["odor_on", "start"],
        default="odor_on",
        help="Combined video alignment: odor_on (default) or start"
    )
    parser.add_argument(
        "--combined-max-tile",
        type=int,
        default=512,
        help="Maximum tile size (pixels) for each trial in the combined grid video (default: 512)"
    )
    parser.add_argument(
        "--combined-cols",
        type=int,
        default=None,
        help="Fixed number of columns for combined grid (default: auto-square)"
    )

    # Common options
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose (debug) logging"
    )
    parser.add_argument(
        "--quiet", "-q",
        action="store_true",
        help="Suppress all output except errors"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be processed without running"
    )
    parser.add_argument(
        "--thumbnail",
        action="store_true",
        default=True,
        help="Save a representative frame thumbnail (default: True)"
    )
    parser.add_argument(
        "--no-thumbnail",
        action="store_true",
        help="Disable thumbnail saving"
    )
    parser.add_argument(
        "--preview",
        action="store_true",
        help="Launch interactive preview GUI for tuning parameters"
    )
    parser.add_argument(
        "--preview-frames",
        type=int,
        default=15,
        help="Number of frames to load for preview (default: 15)"
    )
    parser.add_argument(
        "--trial-index",
        type=int,
        default=0,
        help="Which trial to preview (0-indexed, default: 0)"
    )
    parser.add_argument(
        "--settings",
        type=Path,
        default=None,
        help="YAML file with view/overlay/registration settings (use with --folder)"
    )
    parser.add_argument(
        "--recording-only",
        action="store_true",
        help="Render recording only (no structure overlay, grayscale output)"
    )
    parser.add_argument(
        "--start-frame",
        type=int,
        default=None,
        help="Start preview at this frame index (useful for finding bright frames)"
    )
    parser.add_argument(
        "--save-tiff",
        action="store_true",
        help="Save processed frames as TIFF files alongside video"
    )
    parser.add_argument(
        "--tiff-format",
        choices=["uint8", "float32"],
        default="uint8",
        help="TIFF bit depth: uint8 (smaller files) or float32 (preserves dynamic range)"
    )

    # Parse known args to allow for override args
    args, unknown = parser.parse_known_args(argv)

    # Parse override arguments (--section.key value)
    overrides = {}
    i = 0
    while i < len(unknown):
        if unknown[i].startswith("--") and "." in unknown[i]:
            key = unknown[i][2:]  # Remove --
            if i + 1 < len(unknown) and not unknown[i + 1].startswith("--"):
                value = unknown[i + 1]
                # Try to parse as number or bool
                value = _parse_value(value)
                overrides[key] = value
                i += 2
            else:
                logger.warning(f"Override {unknown[i]} has no value, skipping")
                i += 1
        else:
            logger.warning(f"Unknown argument: {unknown[i]}")
            i += 1

    return args, overrides


def _parse_value(value: str):
    """Parse a string value to appropriate type."""
    # Boolean
    if value.lower() in ("true", "yes", "1"):
        return True
    if value.lower() in ("false", "no", "0"):
        return False

    # Integer
    try:
        return int(value)
    except ValueError:
        pass

    # Float
    try:
        return float(value)
    except ValueError:
        pass

    # String
    return value


def _resolve_output_dir(
    folder: Path,
    output_dir: Optional[Path],
    reuse_output: bool,
) -> Path:
    """
    Resolve output directory for folder mode.

    If output_dir already exists and has content, create a run subfolder
    unless reuse_output is True.
    """
    if output_dir is None:
        output_dir = folder / "output"
    output_dir = Path(output_dir)

    if not reuse_output and output_dir.exists():
        try:
            has_contents = any(output_dir.iterdir())
        except PermissionError:
            has_contents = True
        if has_contents:
            run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_dir = output_dir / f"run_{run_id}"
            logger.warning(
                "Existing output found; writing to new run folder: %s "
                "(use --reuse-output to write into existing folder)",
                output_dir
            )

    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def _get_first_odor_onset(intervals: List[Dict]) -> int:
    """Return earliest odor onset frame from timing intervals (or 0)."""
    starts = []
    for interval in intervals:
        try:
            starts.append(int(interval["start_frame"]))
        except (KeyError, TypeError, ValueError):
            continue
    return min(starts) if starts else 0


def _is_odor_on_at_frame(intervals: List[Dict], frame_idx: int) -> bool:
    """Check if frame index falls within any odor interval."""
    for interval in intervals:
        try:
            start = int(interval["start_frame"])
            end = int(interval["end_frame"])
        except (KeyError, TypeError, ValueError):
            continue
        if start <= frame_idx <= end:
            return True
    return False


def _fit_frame_to_tile(frame_rgb: np.ndarray, target_w: int, target_h: int) -> np.ndarray:
    """
    Resize frame to fit inside tile while preserving aspect ratio, with padding.
    """
    import cv2

    h, w = frame_rgb.shape[:2]
    if h <= 0 or w <= 0:
        return np.zeros((target_h, target_w, 3), dtype=np.uint8)

    scale = min(target_w / w, target_h / h)
    new_w = max(1, int(round(w * scale)))
    new_h = max(1, int(round(h * scale)))

    resized = cv2.resize(frame_rgb, (new_w, new_h), interpolation=cv2.INTER_AREA)
    tile = np.zeros((target_h, target_w, 3), dtype=np.uint8)
    x0 = (target_w - new_w) // 2
    y0 = (target_h - new_h) // 2
    tile[y0:y0 + new_h, x0:x0 + new_w] = resized
    return tile


def _read_capture_frame_rgb(state: Dict, target_idx: int) -> Optional[np.ndarray]:
    """
    Read one frame from cv2.VideoCapture at target index (forward-only).
    """
    import cv2

    cap = state["cap"]
    if state["eof"] or target_idx < 0:
        return None

    # Combined video timeline should be monotonic; guard anyway.
    if target_idx < state["next_idx"]:
        return None

    while state["next_idx"] < target_idx:
        if not cap.grab():
            state["eof"] = True
            return None
        state["next_idx"] += 1

    ok, frame_bgr = cap.read()
    if not ok:
        state["eof"] = True
        return None

    state["next_idx"] += 1
    return cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)


def _render_combined_trial_video(
    trials_data: List[Dict],
    output_path: Path,
    sync_mode: str = "odor_on",
    max_tile_size: int = 512,
    grid_cols: Optional[int] = None,
) -> Path:
    """
    Render one synchronized grid video across multiple trial videos.

    Args:
        trials_data: List of trial metadata dicts with:
            trial_name, odor_name, video_path, n_frames, fps, timing_intervals.
        output_path: Output MP4 path.
        sync_mode: "odor_on" to align earliest odor onset, or "start".
        max_tile_size: Maximum tile edge length for each trial panel.
        grid_cols: Optional fixed number of grid columns.
    """
    import cv2

    if len(trials_data) < 2:
        raise ValueError("Need at least 2 trials for combined video")

    max_tile_size = max(64, int(max_tile_size))
    caps = []

    try:
        # Open all trial videos and collect source dimensions.
        widths = []
        heights = []
        fps_values = []

        for trial in trials_data:
            video_path = Path(trial["video_path"])
            if not video_path.exists():
                raise FileNotFoundError(f"Trial video not found: {video_path}")

            cap = cv2.VideoCapture(str(video_path))
            if not cap.isOpened():
                raise ValueError(f"Failed to open trial video: {video_path}")

            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 1
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 1

            caps.append({"cap": cap, "next_idx": 0, "eof": False})
            widths.append(width)
            heights.append(height)
            fps_values.append(float(trial["fps"]))

        base_w = max(widths)
        base_h = max(heights)
        scale = min(1.0, max_tile_size / max(base_w, base_h))
        tile_w = max(2, int(round(base_w * scale)))
        tile_h = max(2, int(round(base_h * scale)))
        # yuv420p compatibility
        if tile_w % 2:
            tile_w += 1
        if tile_h % 2:
            tile_h += 1

        n_trials = len(trials_data)
        if grid_cols is not None and grid_cols > 0:
            cols = int(grid_cols)
        else:
            cols = int(math.ceil(math.sqrt(n_trials)))
        rows = int(math.ceil(n_trials / cols))

        banner_h = 36
        label_h = 44
        canvas_w = cols * tile_w
        canvas_h = banner_h + rows * (tile_h + label_h)
        if canvas_w % 2:
            canvas_w += 1
        if canvas_h % 2:
            canvas_h += 1

        # Build synchronization timeline.
        if sync_mode == "odor_on":
            anchors = [int(trial["anchor_frame"]) for trial in trials_data]
            common_anchor = max(anchors)
            post_lengths = [max(0, int(trial["n_frames"]) - int(trial["anchor_frame"]))
                            for trial in trials_data]
            n_out_frames = common_anchor + max(post_lengths)
        else:
            common_anchor = 0
            n_out_frames = max(int(trial["n_frames"]) for trial in trials_data)

        if n_out_frames <= 0:
            raise ValueError("No frames available for combined video")

        fps_out = fps_values[0]
        if any(abs(f - fps_out) > 1e-3 for f in fps_values[1:]):
            logger.warning(
                "Trials have mismatched FPS values (%s); combined video will use %.3f",
                [round(f, 3) for f in fps_values],
                fps_out,
            )

        logger.info(
            "Rendering combined video: %d trials, grid=%dx%d, tile=%dx%d, frames=%d",
            n_trials, cols, rows, tile_w, tile_h, n_out_frames
        )

        output_path = Path(output_path)
        with VideoWriter(output_path, fps=fps_out, frame_size=(canvas_w, canvas_h)) as writer:
            for out_idx in range(n_out_frames):
                if out_idx % 100 == 0:
                    pct = 100.0 * out_idx / n_out_frames
                    logger.info(
                        "Combined video frame %d/%d (%.1f%%)",
                        out_idx, n_out_frames, pct
                    )

                canvas = np.zeros((canvas_h, canvas_w, 3), dtype=np.uint8)

                if sync_mode == "odor_on":
                    rel_frame = out_idx - common_anchor
                    rel_sec = rel_frame / max(1e-6, fps_out)
                    banner_text = f"Synchronized by odor onset | rel_frame={rel_frame:+d} | rel_time={rel_sec:+.2f}s"
                else:
                    banner_text = f"Synchronized by recording start | frame={out_idx}"
                cv2.putText(
                    canvas,
                    banner_text,
                    (12, 24),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.58,
                    (230, 230, 230),
                    1,
                    cv2.LINE_AA,
                )

                for i, trial in enumerate(trials_data):
                    row = i // cols
                    col = i % cols
                    x0 = col * tile_w
                    y0 = banner_h + row * (tile_h + label_h)

                    if sync_mode == "odor_on":
                        src_idx = out_idx - common_anchor + int(trial["anchor_frame"])
                    else:
                        src_idx = out_idx

                    frame_tile = np.zeros((tile_h, tile_w, 3), dtype=np.uint8)
                    valid_idx = 0 <= src_idx < int(trial["n_frames"])
                    if valid_idx:
                        frame_rgb = _read_capture_frame_rgb(caps[i], src_idx)
                        if frame_rgb is not None:
                            frame_tile = _fit_frame_to_tile(frame_rgb, tile_w, tile_h)

                    canvas[y0:y0 + tile_h, x0:x0 + tile_w] = frame_tile

                    odor_on = valid_idx and _is_odor_on_at_frame(trial["timing_intervals"], src_idx)
                    border_color = (30, 220, 70) if odor_on else (70, 70, 70)
                    cv2.rectangle(
                        canvas,
                        (x0, y0),
                        (x0 + tile_w - 1, y0 + tile_h - 1),
                        border_color,
                        2,
                    )

                    y_label = y0 + tile_h
                    canvas[y_label:y_label + label_h, x0:x0 + tile_w] = (20, 20, 20)

                    trial_name = str(trial["trial_name"])
                    odor_name = str(trial["odor_name"])
                    status = "ODOR ON" if odor_on else "odor off"
                    frame_text = f"f={src_idx}" if valid_idx else "f=--"

                    cv2.putText(
                        canvas,
                        trial_name[:42],
                        (x0 + 8, y_label + 17),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.45,
                        (240, 240, 240),
                        1,
                        cv2.LINE_AA,
                    )
                    cv2.putText(
                        canvas,
                        f"{odor_name[:26]} | {status} | {frame_text}",
                        (x0 + 8, y_label + 35),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.43,
                        (200, 200, 200),
                        1,
                        cv2.LINE_AA,
                    )

                writer.write_frame(canvas)

        return output_path

    finally:
        for state in caps:
            try:
                state["cap"].release()
            except Exception:
                pass


def run_pipeline(config: OverlayConfig, save_thumbnail_flag: bool = True, recording_only: bool = False, save_tiff: bool = False, tiff_format: str = "uint8") -> dict:
    """
    Run the full overlay rendering pipeline.

    Args:
        config: Validated configuration.
        save_thumbnail_flag: Whether to save thumbnail image.
        recording_only: If True, render only the recording (grayscale, no overlay).
        save_tiff: If True, save processed frames as TIFF files.
        tiff_format: TIFF bit depth: "uint8" or "float32".

    Returns:
        Report dictionary.
    """
    start_time = time.time()

    if recording_only:
        logger.info("=" * 60)
        logger.info("RECORDING-ONLY MODE (no structure overlay)")
        logger.info("=" * 60)

    # Step 1: Load structure image (skip if recording_only)
    structure = None
    if not recording_only:
        logger.info("=" * 60)
        logger.info("STEP 1: Loading structure image")
        logger.info("=" * 60)
        structure = load_structure(config.structure_path)
        logger.info(f"Structure shape: {structure.shape}, dtype: {structure.dtype}")
    else:
        logger.info("Skipping structure loading (recording-only mode)")

    # Step 2: Load recording
    logger.info("=" * 60)
    logger.info("STEP 2: Loading recording")
    logger.info("=" * 60)
    recording = load_recording(config.recording_path)
    logger.info(f"Recording: {recording.n_frames} frames, shape: {recording.frame_shape}")

    # Step 2b: Precompute bleach correction (if enabled)
    bleach_corrector = None
    if config.bleach_correction.enabled and config.bleach_correction.method != "none":
        logger.info("Computing bleach correction trend from recording...")
        bleach_corrector = precompute_bleach_corrector(
            settings=config.bleach_correction,
            recording_iterator=recording,
            n_frames=recording.n_frames,
        )

    # Log preprocessing banner
    banner = format_preproc_banner(
        config.bleach_correction, config.denoise, config.view
    )
    logger.info(banner)

    # Determine FPS
    fps = config.timing.fps
    if fps is None:
        fps = recording.fps
        if fps is None:
            logger.error("FPS not specified in config and not available from recording")
            raise ValueError(
                "FPS must be specified in config.timing.fps or available from recording metadata"
            )
    logger.info(f"Using FPS: {fps}")

    # Step 3: Extract timing
    logger.info("=" * 60)
    logger.info("STEP 3: Extracting odor timing")
    logger.info("=" * 60)
    timing_result = extract_odor_timing(
        n_frames=recording.n_frames,
        csv_path=config.frames_csv_path,
        json_path=config.metadata_json_path,
        settings=config.timing
    )
    logger.info(f"Timing source: {timing_result.source}")
    logger.info(f"Odor intervals: {len(timing_result.intervals)}")
    for interval in timing_result.intervals[:5]:
        logger.info(f"  Frames {interval.start_frame}-{interval.end_frame}: {interval.odor_name or 'odor'}")
    if len(timing_result.intervals) > 5:
        logger.info(f"  ... and {len(timing_result.intervals) - 5} more")

    # Step 4: Registration (skip if recording_only)
    registration = None
    registration_result = None
    if not recording_only and structure is not None:
        logger.info("=" * 60)
        logger.info("STEP 4: Computing registration")
        logger.info("=" * 60)
        registration = FrameRegistration(structure, config.registration)

        # Get representative frame for registration
        rep_idx = get_representative_frame_index(recording.n_frames, method="median")
        rep_frame = recording.get_frame(rep_idx)
        logger.info(f"Using frame {rep_idx} as registration reference")

        registration_result = registration.initialize(rep_frame)
        logger.info(f"Registration converged: {registration_result.converged}")
        logger.info(f"Registration correlation: {registration_result.correlation:.4f}")
    else:
        logger.info("Skipping registration (recording-only mode)")

    # Step 4b: Collect preprocessed frames (if DFF or projections need them)
    need_all_frames = config.dff.enabled or config.projections.enabled
    dff_frames = None  # Will hold (T,H,W) float32 array when DFF is active
    dff_baseline_idx = None
    all_frames = None  # Preprocessed raw frames; kept alive for projections
    projection_report = None

    if need_all_frames:
        logger.info("=" * 60)
        logger.info("STEP 4b: Collecting preprocessed frames")
        logger.info("=" * 60)

        all_frames = np.empty(
            (recording.n_frames, *recording.frame_shape), dtype=np.float32
        )
        for fi in range(recording.n_frames):
            if fi % 200 == 0:
                logger.info(f"  Preprocessing frame {fi}/{recording.n_frames}")
            frame = recording.get_frame(fi)
            if registration is not None:
                frame = registration.apply(frame)
            if bleach_corrector is not None:
                frame = bleach_corrector.correct_frame(frame, fi)
            if config.denoise.enabled:
                frame = apply_denoise(frame, config.denoise)
            all_frames[fi] = frame.astype(np.float32)

    # ΔF/F computation (if enabled)
    if config.dff.enabled:
        logger.info("Computing ΔF/F...")

        dff_baseline_idx = determine_baseline_frames(
            baseline_source=config.dff.baseline_source,
            baseline_frames_range=config.dff.baseline_frames,
            odor_intervals=timing_result.intervals,
            n_frames=recording.n_frames,
        )
        logger.info(
            f"Baseline: frames {dff_baseline_idx[0]}–{dff_baseline_idx[-1]} "
            f"({len(dff_baseline_idx)} frames)"
        )

        f0 = compute_f0(
            all_frames, dff_baseline_idx,
            method=config.dff.f0_method,
            percentile=config.dff.f0_percentile,
        )
        dff_frames = apply_dff(
            all_frames, f0,
            eps=config.dff.eps,
            clip=config.dff.clip,
        )

        # Save DFF TIFF stack
        if config.dff.save_tiff:
            import tifffile
            dff_tiff_path = config.output_dir / config.dff.output_name
            config.output_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"Saving ΔF/F TIFF: {dff_tiff_path}")
            tifffile.imwrite(str(dff_tiff_path), dff_frames)
            logger.info(
                f"Saved: dtype={dff_frames.dtype}, shape={dff_frames.shape}, "
                f"min={dff_frames.min():.4f}, max={dff_frames.max():.4f}"
            )

    # Activity summary projections (if enabled)
    if config.projections.enabled:
        logger.info("=" * 60)
        logger.info("STEP 4c: Generating activity summary projections")
        logger.info("=" * 60)

        proj_epochs = determine_epochs(
            odor_intervals=timing_result.intervals,
            n_frames=recording.n_frames,
            post_frames=config.projections.post_frames,
        )
        proj_dir = config.output_dir / "projections"
        projection_report = generate_projections(
            frames=all_frames,
            epochs=proj_epochs,
            output_dir=proj_dir,
            save_png=config.projections.save_png,
            save_tiff=config.projections.save_tiff,
            make_rgb=config.projections.make_rgb_epochs,
            dff_frames=dff_frames,
            p_lo=config.projections.p_lo,
            p_hi=config.projections.p_hi,
            gamma=config.projections.gamma,
        )

    # Free preprocessed frames if no longer needed for rendering
    if all_frames is not None and dff_frames is None:
        del all_frames

    # Step 5: Compute global scaling parameters
    logger.info("=" * 60)
    logger.info("STEP 5: Computing view scaling parameters")
    logger.info("=" * 60)
    # When DFF is active, compute scaling params from DFF frames (not raw).
    # We create a thin wrapper that yields DFF frames so the existing
    # compute_global_scaling_params() can sample them.
    if dff_frames is not None:
        from .view_scaling import ScalingParams
        # Compute percentiles directly from the DFF array
        sample_indices = np.linspace(0, dff_frames.shape[0] - 1, min(50, dff_frames.shape[0]), dtype=int)
        sample = dff_frames[sample_indices]
        if config.view.method == "percentile":
            vmin = float(np.percentile(sample, config.view.p_lo))
            vmax = float(np.percentile(sample, config.view.p_hi))
        else:
            vmin = float(sample.min())
            vmax = float(sample.max())
        scaling_params = ScalingParams(
            vmin=vmin, vmax=vmax,
            gamma=config.view.gamma,
            use_clahe=config.view.clahe,
            clahe_clip_limit=config.view.clahe_clip_limit,
            clahe_tile_grid=config.view.clahe_tile_grid,
        )
    else:
        scaling_params = compute_global_scaling_params(
            recording, config.view,
            roi_center_fraction=config.view.roi_center_fraction
        )
    logger.info(f"Scaling: vmin={scaling_params.vmin:.2f}, vmax={scaling_params.vmax:.2f}")
    logger.info(f"Gamma: {scaling_params.gamma}, CLAHE: {scaling_params.use_clahe}")

    # Step 6: Initialize overlay renderer (skip if recording_only)
    overlay_renderer = None
    if not recording_only and structure is not None:
        logger.info("=" * 60)
        logger.info("STEP 6: Initializing overlay renderer")
        logger.info("=" * 60)

        # Scale structure for display
        from .view_scaling import _compute_single_image_params, scale_frame as scale_single
        struct_params = _compute_single_image_params(structure, config.view)
        structure_scaled = scale_single(structure, struct_params)

        overlay_renderer = OverlayRenderer(structure_scaled, config.overlay)
    else:
        logger.info("Skipping overlay renderer (recording-only mode)")

    # Step 7: Render video
    logger.info("=" * 60)
    logger.info("STEP 7: Rendering video")
    logger.info("=" * 60)

    # Output paths
    stem = Path(config.recording_path).stem
    if Path(config.recording_path).is_dir():
        stem = Path(config.recording_path).parent.parent.name  # Use trial name

    # Different output name for recording-only mode
    if recording_only:
        output_video = config.output_dir / f"{stem}_recording.mp4"
        output_thumbnail = config.output_dir / f"{stem}_recording_thumbnail.png"
        output_tiff_dir = config.output_dir / f"{stem}_recording_tiff" if save_tiff else None
    else:
        output_video = config.output_dir / f"{stem}_overlay.mp4"
        output_thumbnail = config.output_dir / f"{stem}_thumbnail.png"
        output_tiff_dir = config.output_dir / f"{stem}_overlay_tiff" if save_tiff else None
    output_report = config.output_dir / f"{stem}_report.json"

    # Create TIFF output directory if needed
    if save_tiff:
        output_tiff_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"TIFF output directory: {output_tiff_dir}")
        logger.info(f"TIFF format: {tiff_format}")

    thumbnail_frame = None
    thumbnail_idx = recording.n_frames // 2

    with VideoWriter(output_video, fps=fps) as writer:
        for frame_idx in range(recording.n_frames):
            # Progress logging
            if frame_idx % 100 == 0:
                pct = 100 * frame_idx / recording.n_frames
                logger.info(f"Processing frame {frame_idx}/{recording.n_frames} ({pct:.1f}%)")

            if dff_frames is not None:
                # DFF path: frames already preprocessed, use DFF values directly
                denoised_frame = dff_frames[frame_idx]
            else:
                # Standard path: preprocess on the fly
                # Get frame
                raw_frame = recording.get_frame(frame_idx)

                # Apply registration (if enabled and not recording_only)
                if registration is not None:
                    registered_frame = registration.apply(raw_frame)
                else:
                    registered_frame = raw_frame

                # Apply bleach correction (before denoise, before view scaling)
                if bleach_corrector is not None:
                    corrected_frame = bleach_corrector.correct_frame(
                        registered_frame, frame_idx
                    )
                else:
                    corrected_frame = registered_frame

                # Apply denoising to recording frame (before view scaling)
                if config.denoise.enabled:
                    denoised_frame = apply_denoise(corrected_frame, config.denoise)
                else:
                    denoised_frame = corrected_frame

            # Apply view scaling
            scaled_frame = scale_frame(denoised_frame, scaling_params)

            # Create output frame
            if recording_only:
                # Grayscale: convert to 3-channel for video writer
                composite = np.stack([scaled_frame, scaled_frame, scaled_frame], axis=-1)
            else:
                # Create overlay
                composite = overlay_renderer.render(scaled_frame)

            # Add annotation
            is_odor_on = timing_result.is_odor_on(frame_idx)
            annotated = draw_odor_annotation(
                composite.copy(),
                is_odor_on=is_odor_on,
                settings=config.annotation,
                frame_idx=frame_idx
            )

            # Write frame
            writer.write_frame(annotated)

            # Save TIFF frame if enabled
            if save_tiff:
                save_tiff_frame(
                    annotated,
                    output_tiff_dir,
                    frame_idx,
                    as_float32=(tiff_format == "float32")
                )

            # Save thumbnail frame
            if frame_idx == thumbnail_idx:
                thumbnail_frame = annotated.copy()

    # Save thumbnail
    if save_thumbnail_flag and thumbnail_frame is not None:
        save_thumbnail(thumbnail_frame, output_thumbnail)

    # Step 8: Generate report
    logger.info("=" * 60)
    logger.info("STEP 8: Generating report")
    logger.info("=" * 60)

    processing_time = time.time() - start_time

    # Build additional info for report (DFF + projections metadata)
    additional_info = {}
    if config.dff.enabled and dff_baseline_idx is not None:
        additional_info["dff"] = {
            "enabled": True,
            "baseline_source": config.dff.baseline_source,
            "f0_method": config.dff.f0_method,
            "f0_percentile": config.dff.f0_percentile,
            "eps": config.dff.eps,
            "clip": list(config.dff.clip) if config.dff.clip else None,
            "baseline_frames_used": [
                int(dff_baseline_idx[0]), int(dff_baseline_idx[-1])
            ],
            "n_baseline_frames": len(dff_baseline_idx),
            "save_tiff": config.dff.save_tiff,
            "output_name": config.dff.output_name,
        }
    if projection_report is not None:
        additional_info["projections"] = projection_report
    if not additional_info:
        additional_info = None

    report = generate_report(
        config=config,
        timing_result=timing_result,
        registration_result=registration_result,
        output_video_path=output_video,
        n_frames_processed=recording.n_frames,
        fps_used=fps,
        processing_time_seconds=processing_time,
        additional_info=additional_info,
    )

    save_report(report, output_report)

    # Print summary
    logger.info("\n" + format_report_summary(report))

    # Cleanup
    recording.close()

    return report


def run_folder_mode(
    folder: Path,
    output_dir: Optional[Path],
    trial_filter: Optional[str],
    structure_index: int,
    save_thumbnail_flag: bool,
    dry_run: bool,
    reuse_output: bool,
    settings_overrides: Optional[dict] = None,
    recording_only: bool = False,
    save_tiff: bool = False,
    tiff_format: str = "uint8",
    combined_video: bool = False,
    combined_sync: str = "odor_on",
    combined_max_tile: int = 512,
    combined_cols: Optional[int] = None,
) -> int:
    """
    Run in folder auto-discovery mode.

    Args:
        folder: Path to experiment folder.
        output_dir: Output directory (or None for default).
        trial_filter: Optional trial name filter.
        structure_index: Which structure image to use.
        save_thumbnail_flag: Whether to save thumbnails.
        dry_run: If True, show what would be processed without running.
        save_tiff: If True, save processed frames as TIFF files.
        tiff_format: TIFF bit depth: "uint8" or "float32".
        combined_video: If True, render combined multi-trial comparison video.
        combined_sync: Combined video sync mode ("odor_on" or "start").
        combined_max_tile: Maximum tile size in combined video grid.
        combined_cols: Optional fixed number of columns in combined grid.

    Returns:
        Exit code (0 for success).
    """
    from .discovery import discover_experiment, generate_trial_config

    # Discover experiment
    logger.info("=" * 60)
    logger.info("DISCOVERING EXPERIMENT")
    logger.info("=" * 60)

    experiment = discover_experiment(folder)

    # Set output directory
    output_dir = _resolve_output_dir(folder, output_dir, reuse_output=reuse_output)

    # Select structure image
    if structure_index >= len(experiment.structure_files):
        logger.warning(f"Structure index {structure_index} out of range, using 0")
        structure_index = 0
    structure_path = experiment.structure_files[structure_index]

    logger.info(f"Experiment: {experiment.experiment_name}")
    logger.info(f"Structure: {structure_path.name}")
    logger.info(f"Output: {output_dir}")

    # Filter trials
    trials = experiment.trials
    if trial_filter:
        trials = [t for t in trials if trial_filter.upper() in t.trial_name.upper()]
        logger.info(f"Filter '{trial_filter}': {len(trials)} trials match")

    if not trials:
        logger.error("No trials to process!")
        return 1

    logger.info(f"\nTrials to process ({len(trials)}):")
    for trial in trials:
        logger.info(f"  {trial.trial_name}: {trial.odor_name} ({trial.n_frames} frames)")

    if dry_run:
        logger.info("\nDRY RUN - No processing performed")
        return 0

    # Process each trial
    logger.info("\n" + "=" * 60)
    logger.info("PROCESSING TRIALS")
    logger.info("=" * 60)

    success_count = 0
    fail_count = 0
    processed_trials = []

    for i, trial in enumerate(trials):
        logger.info(f"\n[{i+1}/{len(trials)}] Processing {trial.trial_name}...")

        try:
            # Generate config for this trial
            trial_output = output_dir / trial.trial_name
            config_dict = generate_trial_config(trial, structure_path, trial_output)

            # Create config object
            config = OverlayConfig(
                structure_path=config_dict["structure_path"],
                recording_path=config_dict["recording_path"],
                output_dir=config_dict["output_dir"],
                metadata_json_path=config_dict["metadata_json_path"],
                frames_csv_path=config_dict.get("frames_csv_path"),
            )

            # Override with specific settings from config_dict
            config.overlay.alpha = config_dict["overlay"]["alpha"]
            config.overlay.mode = config_dict["overlay"]["mode"]
            config.timing.fps = config_dict["timing"]["fps"]

            # Apply user settings overrides (from --settings file)
            if settings_overrides:
                if "view" in settings_overrides:
                    view = settings_overrides["view"]
                    if "p_lo" in view:
                        config.view.p_lo = view["p_lo"]
                    if "p_hi" in view:
                        config.view.p_hi = view["p_hi"]
                    if "gamma" in view:
                        config.view.gamma = view["gamma"]
                    if "clahe" in view:
                        config.view.clahe = view["clahe"]
                    if "method" in view:
                        config.view.method = view["method"]
                    if "roi_center_fraction" in view:
                        config.view.roi_center_fraction = view["roi_center_fraction"]

                if "overlay" in settings_overrides:
                    overlay = settings_overrides["overlay"]
                    if "alpha" in overlay:
                        config.overlay.alpha = overlay["alpha"]
                    if "mode" in overlay:
                        config.overlay.mode = overlay["mode"]

                if "registration" in settings_overrides:
                    reg = settings_overrides["registration"]
                    if "enabled" in reg:
                        config.registration.enabled = reg["enabled"]
                    if "model" in reg:
                        config.registration.model = reg["model"]

                if "bleach_correction" in settings_overrides:
                    bc = settings_overrides["bleach_correction"]
                    if "enabled" in bc:
                        config.bleach_correction.enabled = bc["enabled"]
                    if "method" in bc:
                        config.bleach_correction.method = bc["method"]
                    if "baseline_frames" in bc:
                        bf = bc["baseline_frames"]
                        config.bleach_correction.baseline_frames = tuple(bf) if isinstance(bf, list) else bf
                    if "poly_order" in bc:
                        config.bleach_correction.poly_order = bc["poly_order"]
                    if "epsilon" in bc:
                        config.bleach_correction.epsilon = bc["epsilon"]
                    if "apply_mode" in bc:
                        config.bleach_correction.apply_mode = bc["apply_mode"]

                if "denoise" in settings_overrides:
                    denoise = settings_overrides["denoise"]
                    if "enabled" in denoise:
                        config.denoise.enabled = denoise["enabled"]
                    if "method" in denoise:
                        config.denoise.method = denoise["method"]
                    if "strength" in denoise:
                        config.denoise.strength = denoise["strength"]
                    if "device" in denoise:
                        config.denoise.device = denoise["device"]
                    if "model_path" in denoise:
                        config.denoise.model_path = denoise["model_path"]

                if "dff" in settings_overrides:
                    dff = settings_overrides["dff"]
                    if "enabled" in dff:
                        config.dff.enabled = dff["enabled"]
                    if "baseline_source" in dff:
                        config.dff.baseline_source = dff["baseline_source"]
                    if "baseline_frames" in dff:
                        bf = dff["baseline_frames"]
                        config.dff.baseline_frames = tuple(bf) if isinstance(bf, list) else bf
                    if "f0_method" in dff:
                        config.dff.f0_method = dff["f0_method"]
                    if "f0_percentile" in dff:
                        config.dff.f0_percentile = dff["f0_percentile"]
                    if "eps" in dff:
                        config.dff.eps = dff["eps"]
                    if "clip" in dff:
                        c = dff["clip"]
                        config.dff.clip = tuple(c) if isinstance(c, list) else c
                    if "save_tiff" in dff:
                        config.dff.save_tiff = dff["save_tiff"]
                    if "output_name" in dff:
                        config.dff.output_name = dff["output_name"]

                if "projections" in settings_overrides:
                    proj = settings_overrides["projections"]
                    if "enabled" in proj:
                        config.projections.enabled = proj["enabled"]
                    if "save_png" in proj:
                        config.projections.save_png = proj["save_png"]
                    if "save_tiff" in proj:
                        config.projections.save_tiff = proj["save_tiff"]
                    if "make_rgb_epochs" in proj:
                        config.projections.make_rgb_epochs = proj["make_rgb_epochs"]
                    if "post_frames" in proj:
                        config.projections.post_frames = proj["post_frames"]
                    if "p_lo" in proj:
                        config.projections.p_lo = proj["p_lo"]
                    if "p_hi" in proj:
                        config.projections.p_hi = proj["p_hi"]
                    if "gamma" in proj:
                        config.projections.gamma = proj["gamma"]

            # Run pipeline
            report = run_pipeline(
                config,
                save_thumbnail_flag=save_thumbnail_flag,
                recording_only=recording_only,
                save_tiff=save_tiff,
                tiff_format=tiff_format
            )
            processed_trials.append({
                "trial_name": trial.trial_name,
                "odor_name": trial.odor_name,
                "report": report,
            })
            success_count += 1

        except Exception as e:
            logger.error(f"Failed to process {trial.trial_name}: {e}")
            fail_count += 1

    combined_failed = False
    if combined_video:
        if len(processed_trials) < 2:
            logger.warning("Skipping combined video: need at least 2 successfully rendered trials")
        else:
            logger.info("\n" + "=" * 60)
            logger.info("RENDERING COMBINED MULTI-TRIAL VIDEO")
            logger.info("=" * 60)
            try:
                combined_trials_data = []
                for item in processed_trials:
                    report = item["report"]
                    intervals = report.get("timing", {}).get("intervals", [])
                    combined_trials_data.append({
                        "trial_name": item["trial_name"],
                        "odor_name": item["odor_name"],
                        "video_path": report["output"]["video_path"],
                        "n_frames": report["processing"]["n_frames_processed"],
                        "fps": report["processing"]["fps_used"],
                        "timing_intervals": intervals,
                        "anchor_frame": _get_first_odor_onset(intervals),
                    })

                combined_name = (
                    "all_trials_recording_synced.mp4"
                    if recording_only
                    else "all_trials_overlay_synced.mp4"
                )
                combined_path = _render_combined_trial_video(
                    trials_data=combined_trials_data,
                    output_path=output_dir / combined_name,
                    sync_mode=combined_sync,
                    max_tile_size=combined_max_tile,
                    grid_cols=combined_cols,
                )
                logger.info(f"Combined video saved: {combined_path}")
            except Exception as e:
                combined_failed = True
                logger.error(f"Failed to render combined video: {e}")

    # Summary
    logger.info("\n" + "=" * 60)
    logger.info("SUMMARY")
    logger.info("=" * 60)
    logger.info(f"Processed: {success_count}/{len(trials)} trials")
    if fail_count > 0:
        logger.warning(f"Failed: {fail_count} trials")
    if combined_video and not combined_failed and len(processed_trials) >= 2:
        logger.info("Combined video: completed")
    if combined_failed:
        logger.warning("Combined video: failed")

    return 0 if fail_count == 0 and not combined_failed else 1


def main(argv: Optional[List[str]] = None) -> int:
    """
    Main entry point for CLI.

    Args:
        argv: Command-line arguments (defaults to sys.argv).

    Returns:
        Exit code (0 for success, non-zero for error).
    """
    args, overrides = parse_args(argv)

    # Setup logging
    if args.quiet:
        log_level = logging.ERROR
    elif args.verbose:
        log_level = logging.DEBUG
    else:
        log_level = logging.INFO

    setup_logging(level=log_level)

    try:
        # Preview mode
        if args.preview:
            from .preview import load_preview_data, PreviewGUI

            if args.folder:
                logger.info(f"Loading preview from folder: {args.folder}")
                structure, frames, total_frames = load_preview_data(
                    folder=args.folder,
                    n_frames=args.preview_frames,
                    trial_index=args.trial_index,
                )
            elif args.config:
                config = load_config(args.config)
                structure, frames, total_frames = load_preview_data(
                    structure_path=Path(config.structure_path),
                    recording_path=Path(config.recording_path),
                    n_frames=args.preview_frames,
                )
            else:
                logger.error("Preview requires --folder or --config")
                return 1

            logger.info(f"Loaded {len(frames)} preview frames ({total_frames} total in recording)")

            # Determine start frame for preview
            start_frame = args.start_frame
            if start_frame is not None:
                # Map actual recording frame index to preview-sampled frame index.
                denom = max(1, total_frames - 1)
                preview_idx = int(start_frame / denom * max(0, len(frames) - 1))
                start_frame = min(max(0, preview_idx), len(frames) - 1)
                logger.info(f"Starting at preview frame {start_frame} (requested frame {args.start_frame})")

            gui = PreviewGUI(
                structure,
                frames,
                recording_only=args.recording_only,
                start_frame=start_frame,
                total_recording_frames=total_frames,
            )
            settings = gui.run()

            if args.recording_only:
                logger.info("Preview closed. Use saved settings with --settings tuned_settings.yaml --recording-only")
            else:
                logger.info("Preview closed. Use saved settings with --settings tuned_settings.yaml")
            return 0

        # Load settings file if provided
        settings_overrides = None
        if args.settings:
            import yaml
            logger.info(f"Loading settings from: {args.settings}")
            with open(args.settings, 'r') as f:
                settings_overrides = yaml.safe_load(f)
            logger.info(f"Loaded settings: {list(settings_overrides.keys())}")

        # Determine mode
        if args.folder:
            # Folder auto-discovery mode
            save_thumb = args.thumbnail and not args.no_thumbnail
            return run_folder_mode(
                folder=args.folder,
                output_dir=args.output_dir,
                trial_filter=args.filter,
                structure_index=args.structure_index,
                save_thumbnail_flag=save_thumb,
                dry_run=args.dry_run,
                reuse_output=args.reuse_output,
                settings_overrides=settings_overrides,
                recording_only=args.recording_only,
                save_tiff=args.save_tiff,
                tiff_format=args.tiff_format,
                combined_video=args.combined_video,
                combined_sync=args.combined_sync,
                combined_max_tile=args.combined_max_tile,
                combined_cols=args.combined_cols,
            )

        else:
            # Config file mode
            logger.info(f"Loading config: {args.config}")
            config = load_config(args.config, overrides=overrides if overrides else None)

            if args.dry_run:
                logger.info("DRY RUN - Configuration validated successfully")
                logger.info(f"Structure: {config.structure_path}")
                logger.info(f"Recording: {config.recording_path}")
                logger.info(f"Output dir: {config.output_dir}")
                logger.info(f"Overlay mode: {config.overlay.mode}")
                logger.info(f"Registration: {config.registration.model if config.registration.enabled else 'disabled'}")
                return 0

            # Run pipeline
            save_thumb = args.thumbnail and not args.no_thumbnail
            report = run_pipeline(
                config,
                save_thumbnail_flag=save_thumb,
                save_tiff=args.save_tiff,
                tiff_format=args.tiff_format
            )

            logger.info("Processing completed successfully!")
            return 0

    except FileNotFoundError as e:
        logger.error(f"File not found: {e}")
        return 1

    except ValueError as e:
        logger.error(f"Configuration error: {e}")
        return 1

    except ImportError as e:
        logger.error(f"Missing dependency: {e}")
        return 1

    except KeyboardInterrupt:
        logger.warning("Processing interrupted by user")
        return 130

    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
