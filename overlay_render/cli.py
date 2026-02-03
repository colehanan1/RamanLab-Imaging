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
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

from .config import OverlayConfig, load_config
from .loaders import load_structure, load_recording
from .registration import FrameRegistration
from .view_scaling import compute_global_scaling_params, scale_frame
from .timing import extract_odor_timing
from .annotation import draw_odor_annotation
from .overlay import OverlayRenderer
from .writer import VideoWriter, save_thumbnail
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


def run_pipeline(config: OverlayConfig, save_thumbnail_flag: bool = True, recording_only: bool = False) -> dict:
    """
    Run the full overlay rendering pipeline.

    Args:
        config: Validated configuration.
        save_thumbnail_flag: Whether to save thumbnail image.
        recording_only: If True, render only the recording (grayscale, no overlay).

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

    # Step 5: Compute global scaling parameters
    logger.info("=" * 60)
    logger.info("STEP 5: Computing view scaling parameters")
    logger.info("=" * 60)
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
    else:
        output_video = config.output_dir / f"{stem}_overlay.mp4"
        output_thumbnail = config.output_dir / f"{stem}_thumbnail.png"
    output_report = config.output_dir / f"{stem}_report.json"

    thumbnail_frame = None
    thumbnail_idx = recording.n_frames // 2

    with VideoWriter(output_video, fps=fps) as writer:
        for frame_idx in range(recording.n_frames):
            # Progress logging
            if frame_idx % 100 == 0:
                pct = 100 * frame_idx / recording.n_frames
                logger.info(f"Processing frame {frame_idx}/{recording.n_frames} ({pct:.1f}%)")

            # Get frame
            raw_frame = recording.get_frame(frame_idx)

            # Apply registration (if enabled and not recording_only)
            if registration is not None:
                registered_frame = registration.apply(raw_frame)
            else:
                registered_frame = raw_frame

            # Apply view scaling
            scaled_frame = scale_frame(registered_frame, scaling_params)

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

    report = generate_report(
        config=config,
        timing_result=timing_result,
        registration_result=registration_result,
        output_video_path=output_video,
        n_frames_processed=recording.n_frames,
        fps_used=fps,
        processing_time_seconds=processing_time,
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
    recording_only: bool = False
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

            # Run pipeline
            run_pipeline(config, save_thumbnail_flag=save_thumbnail_flag, recording_only=recording_only)
            success_count += 1

        except Exception as e:
            logger.error(f"Failed to process {trial.trial_name}: {e}")
            fail_count += 1

    # Summary
    logger.info("\n" + "=" * 60)
    logger.info("SUMMARY")
    logger.info("=" * 60)
    logger.info(f"Processed: {success_count}/{len(trials)} trials")
    if fail_count > 0:
        logger.warning(f"Failed: {fail_count} trials")

    return 0 if fail_count == 0 else 1


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
                structure, frames = load_preview_data(
                    folder=args.folder,
                    n_frames=args.preview_frames,
                    trial_index=args.trial_index,
                )
            elif args.config:
                config = load_config(args.config)
                structure, frames = load_preview_data(
                    structure_path=Path(config.structure_path),
                    recording_path=Path(config.recording_path),
                    n_frames=args.preview_frames,
                )
            else:
                logger.error("Preview requires --folder or --config")
                return 1

            logger.info(f"Loaded {len(frames)} preview frames")

            # Determine start frame for preview
            start_frame = args.start_frame
            if start_frame is not None:
                # Map actual frame index to preview frame index
                # Preview samples evenly, so map to closest
                total_frames_approx = len(frames) * (340 // len(frames))  # Rough estimate
                preview_idx = int(start_frame / total_frames_approx * len(frames))
                start_frame = min(max(0, preview_idx), len(frames) - 1)
                logger.info(f"Starting at preview frame {start_frame} (requested ~frame {args.start_frame})")

            gui = PreviewGUI(
                structure,
                frames,
                recording_only=args.recording_only,
                start_frame=start_frame
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
                recording_only=args.recording_only
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
            report = run_pipeline(config, save_thumbnail_flag=save_thumb)

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
