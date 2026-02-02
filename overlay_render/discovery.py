"""
Auto-discovery of files from RamanLab folder structure.

Expected structure:
    <main_folder>/
        structure/
            structure_001.tif
            ...
        fly_1_geno_<genotype>/
            trial_001_OFM_E/
                trial.json
                timestamps.csv
                images/images/
                    frame_00000.tif
                    frame_00001.tif
                    ...
            trial_002_OFM_O/
                ...
        protocol_summary.csv
"""

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Odor code to name mapping
ODOR_NAMES = {
    "OFM_A": "apple cider vinegar",
    "OFM_B": "benzaldehyde",
    "OFM_C": "citral",
    "OFM_E": "ethyl butyrate",
    "OFM_H": "hexanol",
    "OFM_L": "linalool",
    "OFM_O": "3-octanol",
}


@dataclass
class TrialInfo:
    """Information about a single trial."""
    trial_dir: Path
    trial_name: str
    trial_number: int
    odor_code: str
    odor_name: str
    images_dir: Path
    json_path: Path
    csv_path: Optional[Path]
    n_frames: int
    fps: float
    odor_frame_start: int
    odor_frame_end: int

    def to_dict(self) -> dict:
        """Convert to serializable dict."""
        return {
            "trial_dir": str(self.trial_dir),
            "trial_name": self.trial_name,
            "trial_number": self.trial_number,
            "odor_code": self.odor_code,
            "odor_name": self.odor_name,
            "images_dir": str(self.images_dir),
            "json_path": str(self.json_path),
            "csv_path": str(self.csv_path) if self.csv_path else None,
            "n_frames": self.n_frames,
            "fps": self.fps,
            "odor_frame_start": self.odor_frame_start,
            "odor_frame_end": self.odor_frame_end,
        }


@dataclass
class ExperimentInfo:
    """Information about an entire experiment folder."""
    main_dir: Path
    experiment_name: str
    structure_dir: Path
    structure_files: List[Path]
    fly_dirs: List[Path]
    trials: List[TrialInfo]

    def to_dict(self) -> dict:
        """Convert to serializable dict."""
        return {
            "main_dir": str(self.main_dir),
            "experiment_name": self.experiment_name,
            "structure_dir": str(self.structure_dir),
            "structure_files": [str(p) for p in self.structure_files],
            "fly_dirs": [str(p) for p in self.fly_dirs],
            "trials": [t.to_dict() for t in self.trials],
        }


def discover_experiment(main_folder: Path) -> ExperimentInfo:
    """
    Discover all files in an experiment folder.

    Args:
        main_folder: Path to main experiment folder.

    Returns:
        ExperimentInfo with all discovered files.

    Raises:
        FileNotFoundError: If folder doesn't exist or is missing required files.
        ValueError: If folder structure is unexpected.
    """
    main_folder = Path(main_folder)
    if not main_folder.exists():
        raise FileNotFoundError(f"Experiment folder not found: {main_folder}")
    if not main_folder.is_dir():
        raise ValueError(f"Expected directory, got file: {main_folder}")

    logger.info(f"Discovering experiment: {main_folder.name}")

    # Find structure folder
    structure_dir = main_folder / "structure"
    if not structure_dir.exists():
        raise FileNotFoundError(f"Structure folder not found: {structure_dir}")

    structure_files = sorted(structure_dir.glob("*.tif")) + sorted(structure_dir.glob("*.tiff"))
    if not structure_files:
        raise FileNotFoundError(f"No structure images found in {structure_dir}")
    logger.info(f"Found {len(structure_files)} structure images")

    # Find fly folders
    fly_dirs = sorted([
        d for d in main_folder.iterdir()
        if d.is_dir() and d.name.startswith("fly_")
    ])
    if not fly_dirs:
        raise FileNotFoundError(f"No fly folders found in {main_folder}")
    logger.info(f"Found {len(fly_dirs)} fly folders")

    # Discover trials in each fly folder
    trials = []
    for fly_dir in fly_dirs:
        fly_trials = _discover_trials(fly_dir)
        trials.extend(fly_trials)
        logger.info(f"  {fly_dir.name}: {len(fly_trials)} trials")

    logger.info(f"Total trials discovered: {len(trials)}")

    return ExperimentInfo(
        main_dir=main_folder,
        experiment_name=main_folder.name,
        structure_dir=structure_dir,
        structure_files=structure_files,
        fly_dirs=fly_dirs,
        trials=trials,
    )


def _discover_trials(fly_dir: Path) -> List[TrialInfo]:
    """Discover all trials in a fly folder."""
    trials = []

    trial_dirs = sorted([
        d for d in fly_dir.iterdir()
        if d.is_dir() and d.name.startswith("trial_")
    ])

    for trial_dir in trial_dirs:
        try:
            trial_info = _parse_trial(trial_dir)
            if trial_info:
                trials.append(trial_info)
        except Exception as e:
            logger.warning(f"Failed to parse trial {trial_dir.name}: {e}")

    return trials


def _parse_trial(trial_dir: Path) -> Optional[TrialInfo]:
    """Parse a single trial directory."""
    # Extract trial info from name (e.g., "trial_001_OFM_E")
    name_parts = trial_dir.name.split("_")
    if len(name_parts) < 3:
        logger.warning(f"Unexpected trial name format: {trial_dir.name}")
        return None

    try:
        trial_number = int(name_parts[1])
    except ValueError:
        trial_number = 0

    # Extract odor code (last part or last two parts)
    odor_code = "_".join(name_parts[2:])
    odor_name = ODOR_NAMES.get(odor_code, odor_code)

    # Find images directory
    images_dir = trial_dir / "images" / "images"
    if not images_dir.exists():
        # Try alternate paths
        for alt in ["images", "frames", "."]:
            alt_dir = trial_dir / alt
            if alt_dir.exists() and list(alt_dir.glob("frame_*.tif")):
                images_dir = alt_dir
                break
        else:
            logger.warning(f"No images directory found in {trial_dir}")
            return None

    # Count frames
    frame_files = list(images_dir.glob("frame_*.tif"))
    if not frame_files:
        frame_files = list(images_dir.glob("*.tif"))
    n_frames = len(frame_files)

    if n_frames == 0:
        logger.warning(f"No frames found in {images_dir}")
        return None

    # Find JSON metadata
    json_path = trial_dir / "trial.json"
    if not json_path.exists():
        json_path = trial_dir / "metadata.json"
    if not json_path.exists():
        logger.warning(f"No JSON metadata found in {trial_dir}")
        return None

    # Parse JSON
    with open(json_path, "r") as f:
        data = json.load(f)

    acq = data.get("acquisition", {})
    fps = acq.get("fps_measured") or acq.get("fps_target") or data.get("fps", 10.0)
    odor_frame_start = acq.get("odor_frame_start", 0)
    odor_frame_end = acq.get("odor_frame_end", 0)

    # Override odor name from JSON if available
    if data.get("odor_name"):
        odor_name = data["odor_name"]

    # Find CSV (optional)
    csv_path = trial_dir / "timestamps.csv"
    if not csv_path.exists():
        csv_path = None

    return TrialInfo(
        trial_dir=trial_dir,
        trial_name=trial_dir.name,
        trial_number=trial_number,
        odor_code=odor_code,
        odor_name=odor_name,
        images_dir=images_dir,
        json_path=json_path,
        csv_path=csv_path,
        n_frames=n_frames,
        fps=fps,
        odor_frame_start=odor_frame_start,
        odor_frame_end=odor_frame_end,
    )


def generate_trial_config(
    trial: TrialInfo,
    structure_path: Path,
    output_dir: Path,
) -> dict:
    """
    Generate configuration dict for a single trial.

    Args:
        trial: Trial information.
        structure_path: Path to structure image to use.
        output_dir: Output directory for this trial.

    Returns:
        Configuration dict ready for OverlayConfig.
    """
    return {
        "structure_path": str(structure_path),
        "recording_path": str(trial.images_dir),
        "output_dir": str(output_dir),
        "metadata_json_path": str(trial.json_path),
        "frames_csv_path": str(trial.csv_path) if trial.csv_path else None,
        "overlay": {
            "alpha": 0.5,
            "mode": "falsecolor",
        },
        "view": {
            "method": "percentile",
            "p_lo": 1,
            "p_hi": 99,
            "gamma": 1.0,
            "clahe": False,
        },
        "registration": {
            "enabled": True,
            "model": "euclidean",
            "ecc_iters": 200,
            "ecc_eps": 1e-6,
            "downscale": 2,
        },
        "annotation": {
            "show_box_when_off": False,
            "box": {
                "anchor": "bottom_left",
                "width_px": 520,
                "height_px": 140,
                "margin_px": 20,
            },
            "text": {
                "on": "ODOR ON",
                "off": "ODOR OFF",
                "font_scale": 1.6,
                "thickness": 3,
            },
        },
        "timing": {
            "fps": trial.fps,
            "odor_source": "json",
        },
    }


def process_experiment(
    main_folder: Path,
    output_base: Optional[Path] = None,
    structure_index: int = 0,
    trial_filter: Optional[str] = None,
) -> List[Tuple[TrialInfo, Path]]:
    """
    Discover experiment and generate configs for all trials.

    Args:
        main_folder: Path to main experiment folder.
        output_base: Base output directory. If None, uses main_folder/output.
        structure_index: Which structure image to use (0-indexed).
        trial_filter: Optional filter string for trial names (e.g., "OFM_E").

    Returns:
        List of (TrialInfo, output_dir) tuples.
    """
    main_folder = Path(main_folder)
    experiment = discover_experiment(main_folder)

    if output_base is None:
        output_base = main_folder / "output"
    output_base = Path(output_base)
    output_base.mkdir(parents=True, exist_ok=True)

    # Select structure image
    if structure_index >= len(experiment.structure_files):
        logger.warning(f"Structure index {structure_index} out of range, using 0")
        structure_index = 0
    structure_path = experiment.structure_files[structure_index]
    logger.info(f"Using structure: {structure_path.name}")

    # Filter trials if requested
    trials = experiment.trials
    if trial_filter:
        trials = [t for t in trials if trial_filter.upper() in t.trial_name.upper()]
        logger.info(f"Filtered to {len(trials)} trials matching '{trial_filter}'")

    # Generate output paths
    result = []
    for trial in trials:
        trial_output = output_base / trial.trial_name
        result.append((trial, trial_output))

    return result
