"""
Configuration schema and loading for overlay_render.

Provides dataclass-based configuration with validation and YAML loading.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Literal, Optional, Tuple, Union
import logging
import yaml

logger = logging.getLogger(__name__)


@dataclass
class OverlaySettings:
    """Settings for overlay compositing."""
    alpha: float = 0.5
    mode: Literal["blend", "falsecolor"] = "blend"

    def __post_init__(self) -> None:
        if not 0.0 <= self.alpha <= 1.0:
            raise ValueError(f"alpha must be in [0, 1], got {self.alpha}")
        if self.mode not in ("blend", "falsecolor"):
            raise ValueError(f"mode must be 'blend' or 'falsecolor', got {self.mode}")


@dataclass
class ViewSettings:
    """Settings for view scaling (brightness/contrast)."""
    method: Literal["percentile", "minmax"] = "percentile"
    p_lo: float = 1.0
    p_hi: float = 99.0
    gamma: float = 1.0
    clahe: bool = False
    clahe_clip_limit: float = 2.0
    clahe_tile_grid: Tuple[int, int] = (8, 8)
    roi_center_fraction: Optional[float] = None  # Use center ROI for scaling stats (0.0-1.0)

    def __post_init__(self) -> None:
        if self.method not in ("percentile", "minmax"):
            raise ValueError(f"method must be 'percentile' or 'minmax', got {self.method}")
        if not 0.0 <= self.p_lo < self.p_hi <= 100.0:
            raise ValueError(f"percentile bounds invalid: p_lo={self.p_lo}, p_hi={self.p_hi}")
        if self.gamma <= 0:
            raise ValueError(f"gamma must be positive, got {self.gamma}")
        if self.clahe_clip_limit <= 0:
            raise ValueError(f"clahe_clip_limit must be positive, got {self.clahe_clip_limit}")
        if self.roi_center_fraction is not None and not 0.0 < self.roi_center_fraction <= 1.0:
            raise ValueError(f"roi_center_fraction must be in (0, 1], got {self.roi_center_fraction}")


@dataclass
class RegistrationSettings:
    """Settings for image registration."""
    enabled: bool = True
    model: Literal["affine", "euclidean"] = "affine"
    ecc_iters: int = 200
    ecc_eps: float = 1e-6
    downscale: int = 2

    def __post_init__(self) -> None:
        if self.model not in ("affine", "euclidean"):
            raise ValueError(f"model must be 'affine' or 'euclidean', got {self.model}")
        if self.ecc_iters <= 0:
            raise ValueError(f"ecc_iters must be positive, got {self.ecc_iters}")
        if self.ecc_eps <= 0:
            raise ValueError(f"ecc_eps must be positive, got {self.ecc_eps}")
        if self.downscale < 1:
            raise ValueError(f"downscale must be >= 1, got {self.downscale}")


@dataclass
class BoxSettings:
    """Settings for annotation box."""
    anchor: Literal["bottom_left", "bottom_right", "top_left", "top_right"] = "bottom_left"
    width_px: int = 520
    height_px: int = 140
    margin_px: int = 20

    def __post_init__(self) -> None:
        if self.width_px <= 0 or self.height_px <= 0:
            raise ValueError(f"box dimensions must be positive: {self.width_px}x{self.height_px}")
        if self.margin_px < 0:
            raise ValueError(f"margin_px must be non-negative, got {self.margin_px}")


@dataclass
class TextSettings:
    """Settings for annotation text."""
    on: str = "ODOR ON"
    off: str = "ODOR OFF"
    font_scale: float = 1.6
    thickness: int = 3

    def __post_init__(self) -> None:
        if self.font_scale <= 0:
            raise ValueError(f"font_scale must be positive, got {self.font_scale}")
        if self.thickness < 1:
            raise ValueError(f"thickness must be >= 1, got {self.thickness}")


@dataclass
class AnnotationSettings:
    """Settings for odor annotation."""
    show_box_when_off: bool = False
    box: BoxSettings = field(default_factory=BoxSettings)
    text: TextSettings = field(default_factory=TextSettings)


@dataclass
class TimingSettings:
    """Settings for timing/odor extraction."""
    fps: Optional[float] = None
    odor_source: Literal["auto", "csv", "json"] = "auto"
    frame_index_column_candidates: List[str] = field(
        default_factory=lambda: ["frame", "frame_idx", "index"]
    )
    odor_on_column_candidates: List[str] = field(
        default_factory=lambda: ["odor_on", "odor", "valve_open", "odor_state"]
    )

    def __post_init__(self) -> None:
        if self.fps is not None and self.fps <= 0:
            raise ValueError(f"fps must be positive or None, got {self.fps}")
        if self.odor_source not in ("auto", "csv", "json"):
            raise ValueError(f"odor_source must be 'auto', 'csv', or 'json', got {self.odor_source}")


@dataclass
class OverlayConfig:
    """Main configuration for overlay rendering."""
    structure_path: Path
    recording_path: Path
    output_dir: Path
    frames_csv_path: Optional[Path] = None
    metadata_json_path: Optional[Path] = None
    overlay: OverlaySettings = field(default_factory=OverlaySettings)
    view: ViewSettings = field(default_factory=ViewSettings)
    registration: RegistrationSettings = field(default_factory=RegistrationSettings)
    annotation: AnnotationSettings = field(default_factory=AnnotationSettings)
    timing: TimingSettings = field(default_factory=TimingSettings)

    def __post_init__(self) -> None:
        # Convert strings to Path objects
        if isinstance(self.structure_path, str):
            self.structure_path = Path(self.structure_path)
        if isinstance(self.recording_path, str):
            self.recording_path = Path(self.recording_path)
        if isinstance(self.output_dir, str):
            self.output_dir = Path(self.output_dir)
        if isinstance(self.frames_csv_path, str):
            self.frames_csv_path = Path(self.frames_csv_path)
        if isinstance(self.metadata_json_path, str):
            self.metadata_json_path = Path(self.metadata_json_path)

        # Validate required paths exist
        if not self.structure_path.exists():
            raise FileNotFoundError(f"Structure image not found: {self.structure_path}")
        if not self.recording_path.exists():
            raise FileNotFoundError(f"Recording not found: {self.recording_path}")

        # Validate optional paths if provided
        if self.frames_csv_path is not None and not self.frames_csv_path.exists():
            raise FileNotFoundError(f"Frames CSV not found: {self.frames_csv_path}")
        if self.metadata_json_path is not None and not self.metadata_json_path.exists():
            raise FileNotFoundError(f"Metadata JSON not found: {self.metadata_json_path}")

        # Create output directory if needed
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def validate(self) -> None:
        """Run full validation of configuration."""
        # All validation happens in __post_init__ of sub-configs
        logger.info("Configuration validated successfully")


def _parse_nested_config(data: dict, key: str, cls: type) -> Any:
    """Parse a nested configuration section."""
    section = data.get(key, {})
    if not isinstance(section, dict):
        raise ValueError(f"Config section '{key}' must be a dict, got {type(section)}")

    # Handle special cases for tuple fields
    if cls == ViewSettings and "clahe_tile_grid" in section:
        grid = section["clahe_tile_grid"]
        if isinstance(grid, list):
            section["clahe_tile_grid"] = tuple(grid)

    # Convert string values to appropriate numeric types
    # YAML sometimes parses scientific notation (1e-5) as strings
    section = _convert_numeric_fields(section, cls)

    return cls(**section)


def _convert_numeric_fields(section: dict, cls: type) -> dict:
    """Convert string values to appropriate numeric types based on dataclass fields."""
    import dataclasses

    if not dataclasses.is_dataclass(cls):
        return section

    result = section.copy()
    field_types = {f.name: f.type for f in dataclasses.fields(cls)}

    for field_name, value in result.items():
        if field_name not in field_types:
            continue

        expected_type = field_types[field_name]

        # Handle string values that should be numeric
        if isinstance(value, str):
            try:
                if expected_type == float or expected_type == Optional[float]:
                    result[field_name] = float(value)
                elif expected_type == int:
                    result[field_name] = int(float(value))  # Handle "1e-5" -> 0
            except (ValueError, TypeError):
                pass  # Leave as-is, let validation catch it

    return result


def _parse_annotation_config(data: dict) -> AnnotationSettings:
    """Parse annotation configuration with nested box and text settings."""
    section = data.get("annotation", {})
    if not isinstance(section, dict):
        raise ValueError(f"Config section 'annotation' must be a dict, got {type(section)}")

    show_box_when_off = section.get("show_box_when_off", False)
    box = BoxSettings(**section.get("box", {}))
    text = TextSettings(**section.get("text", {}))

    return AnnotationSettings(
        show_box_when_off=show_box_when_off,
        box=box,
        text=text
    )


def load_config(
    config_path: Union[str, Path],
    overrides: Optional[dict] = None
) -> OverlayConfig:
    """
    Load configuration from YAML file with optional overrides.

    Args:
        config_path: Path to YAML configuration file.
        overrides: Optional dict of overrides (flat keys like 'view.gamma').

    Returns:
        Validated OverlayConfig instance.

    Raises:
        FileNotFoundError: If config file doesn't exist.
        ValueError: If configuration is invalid.
        yaml.YAMLError: If YAML parsing fails.
    """
    config_path = Path(config_path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    logger.info(f"Loading configuration from {config_path}")

    with open(config_path, "r") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        raise ValueError(f"Config file must contain a YAML dict, got {type(data)}")

    # Apply overrides
    if overrides:
        data = _apply_overrides(data, overrides)

    # Extract required fields
    required_fields = ["structure_path", "recording_path", "output_dir"]
    for field_name in required_fields:
        if field_name not in data:
            raise ValueError(f"Missing required config field: {field_name}")

    # Build config object
    config = OverlayConfig(
        structure_path=data["structure_path"],
        recording_path=data["recording_path"],
        output_dir=data["output_dir"],
        frames_csv_path=data.get("frames_csv_path"),
        metadata_json_path=data.get("metadata_json_path"),
        overlay=_parse_nested_config(data, "overlay", OverlaySettings),
        view=_parse_nested_config(data, "view", ViewSettings),
        registration=_parse_nested_config(data, "registration", RegistrationSettings),
        annotation=_parse_annotation_config(data),
        timing=_parse_nested_config(data, "timing", TimingSettings),
    )

    config.validate()
    return config


def _apply_overrides(data: dict, overrides: dict) -> dict:
    """
    Apply flat key overrides to nested config dict.

    Supports keys like 'view.gamma', 'registration.enabled', etc.
    """
    for key, value in overrides.items():
        parts = key.split(".")
        target = data
        for part in parts[:-1]:
            if part not in target:
                target[part] = {}
            target = target[part]
        target[parts[-1]] = value
        logger.debug(f"Override applied: {key} = {value}")

    return data


def config_to_dict(config: OverlayConfig) -> dict:
    """Convert config to a serializable dictionary for reports."""
    from dataclasses import asdict

    def convert(obj: Any) -> Any:
        if isinstance(obj, Path):
            return str(obj)
        elif isinstance(obj, dict):
            return {k: convert(v) for k, v in obj.items()}
        elif isinstance(obj, (list, tuple)):
            return [convert(v) for v in obj]
        return obj

    return convert(asdict(config))
