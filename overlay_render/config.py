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
class BleachCorrectionSettings:
    """Settings for bleach/drift correction (recording frames only).

    Global correction fits a smooth trend to the per-frame median intensity
    and normalises each frame to remove photobleaching drift.  Designed for
    visualisation (manual labelling), not quantitative analysis.
    """
    enabled: bool = False
    method: Literal["none", "exp_global", "poly_global"] = "none"
    baseline_frames: Union[int, Tuple[int, int]] = 30
    poly_order: int = 2
    epsilon: float = 1e-6
    apply_mode: Literal["divide", "subtract"] = "divide"

    def __post_init__(self) -> None:
        if self.method not in ("none", "exp_global", "poly_global"):
            raise ValueError(
                f"bleach_correction.method must be 'none', 'exp_global', or 'poly_global', "
                f"got {self.method}"
            )
        # Coerce list → tuple for baseline_frames from YAML
        if isinstance(self.baseline_frames, list):
            self.baseline_frames = tuple(self.baseline_frames)
        if isinstance(self.baseline_frames, tuple):
            if len(self.baseline_frames) != 2:
                raise ValueError(
                    f"bleach_correction.baseline_frames tuple must have 2 elements "
                    f"(start, end), got {self.baseline_frames}"
                )
            if self.baseline_frames[0] >= self.baseline_frames[1]:
                raise ValueError(
                    f"bleach_correction.baseline_frames start must be < end, "
                    f"got {self.baseline_frames}"
                )
        elif isinstance(self.baseline_frames, int):
            if self.baseline_frames < 1:
                raise ValueError(
                    f"bleach_correction.baseline_frames must be >= 1, "
                    f"got {self.baseline_frames}"
                )
        else:
            raise ValueError(
                f"bleach_correction.baseline_frames must be int or tuple, "
                f"got {type(self.baseline_frames)}"
            )
        if self.poly_order < 1:
            raise ValueError(
                f"bleach_correction.poly_order must be >= 1, got {self.poly_order}"
            )
        if self.epsilon <= 0:
            raise ValueError(
                f"bleach_correction.epsilon must be positive, got {self.epsilon}"
            )
        if self.apply_mode not in ("divide", "subtract"):
            raise ValueError(
                f"bleach_correction.apply_mode must be 'divide' or 'subtract', "
                f"got {self.apply_mode}"
            )


@dataclass
class DenoiseSettings:
    """Settings for denoising (recording frames only)."""
    enabled: bool = False
    method: Literal["none", "nlm", "bilateral", "n2v"] = "none"
    strength: float = 0.0
    device: Literal["auto", "cpu", "cuda"] = "auto"
    model_path: Optional[str] = None

    def __post_init__(self) -> None:
        if self.method not in ("none", "nlm", "bilateral", "n2v"):
            raise ValueError(f"denoise.method must be 'none', 'nlm', 'bilateral', or 'n2v', got {self.method}")
        if self.strength < 0:
            raise ValueError(f"denoise.strength must be non-negative, got {self.strength}")
        if self.device not in ("auto", "cpu", "cuda"):
            raise ValueError(f"denoise.device must be 'auto', 'cpu', or 'cuda', got {self.device}")


@dataclass
class DffSettings:
    """Settings for ΔF/F (delta-F over F0) computation.

    When enabled, computes (F - F0) / (F0 + eps) for each frame and
    optionally saves the result as a float32 TIFF stack.  The DFF frames
    can also be routed through the existing view-scaling / overlay pipeline
    to produce an MP4 rendered from ΔF/F values instead of raw intensities.
    """
    enabled: bool = False
    baseline_source: Literal["odor", "explicit"] = "odor"
    baseline_frames: Optional[Tuple[int, int]] = None  # inclusive [start, end]
    f0_method: Literal["mean", "percentile"] = "mean"
    f0_percentile: float = 20.0
    eps: float = 1e-6
    clip: Optional[Tuple[float, float]] = None  # e.g. [-0.2, 2.0]
    save_tiff: bool = True
    output_name: str = "dff.tif"

    def __post_init__(self) -> None:
        if self.baseline_source not in ("odor", "explicit"):
            raise ValueError(
                f"dff.baseline_source must be 'odor' or 'explicit', "
                f"got '{self.baseline_source}'"
            )
        if self.f0_method not in ("mean", "percentile"):
            raise ValueError(
                f"dff.f0_method must be 'mean' or 'percentile', "
                f"got '{self.f0_method}'"
            )
        if self.f0_percentile < 0 or self.f0_percentile > 100:
            raise ValueError(
                f"dff.f0_percentile must be in [0, 100], got {self.f0_percentile}"
            )
        if self.eps <= 0:
            raise ValueError(f"dff.eps must be positive, got {self.eps}")
        # Coerce list → tuple for fields from YAML
        if isinstance(self.baseline_frames, list):
            self.baseline_frames = tuple(self.baseline_frames)
        if isinstance(self.clip, list):
            self.clip = tuple(self.clip)
        if self.baseline_frames is not None:
            if len(self.baseline_frames) != 2:
                raise ValueError(
                    f"dff.baseline_frames must have 2 elements (start, end), "
                    f"got {self.baseline_frames}"
                )
            if self.baseline_frames[0] > self.baseline_frames[1]:
                raise ValueError(
                    f"dff.baseline_frames start must be <= end, "
                    f"got {self.baseline_frames}"
                )
        if self.clip is not None:
            if len(self.clip) != 2:
                raise ValueError(
                    f"dff.clip must have 2 elements (min, max), got {self.clip}"
                )
            if self.clip[0] >= self.clip[1]:
                raise ValueError(
                    f"dff.clip min must be < max, got {self.clip}"
                )


@dataclass
class ProjectionSettings:
    """Settings for activity summary projection images.

    Generates epoch-based projections (baseline mean, odor mean, post mean,
    odor std, optional RGB composite) to help distinguish active glomeruli.
    """
    enabled: bool = False
    save_png: bool = True
    save_tiff: bool = False
    make_rgb_epochs: bool = True
    post_frames: Optional[int] = None  # cap post-odor epoch length; None = all
    p_lo: float = 1.0
    p_hi: float = 99.0
    gamma: float = 1.0

    def __post_init__(self) -> None:
        if not 0.0 <= self.p_lo < self.p_hi <= 100.0:
            raise ValueError(
                f"projections percentile bounds invalid: p_lo={self.p_lo}, p_hi={self.p_hi}"
            )
        if self.gamma <= 0:
            raise ValueError(f"projections.gamma must be positive, got {self.gamma}")
        if self.post_frames is not None and self.post_frames < 1:
            raise ValueError(
                f"projections.post_frames must be >= 1 or null, got {self.post_frames}"
            )


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
    bleach_correction: BleachCorrectionSettings = field(default_factory=BleachCorrectionSettings)
    denoise: DenoiseSettings = field(default_factory=DenoiseSettings)
    registration: RegistrationSettings = field(default_factory=RegistrationSettings)
    annotation: AnnotationSettings = field(default_factory=AnnotationSettings)
    timing: TimingSettings = field(default_factory=TimingSettings)
    dff: DffSettings = field(default_factory=DffSettings)
    projections: ProjectionSettings = field(default_factory=ProjectionSettings)

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

    if cls == BleachCorrectionSettings and "baseline_frames" in section:
        bf = section["baseline_frames"]
        if isinstance(bf, list):
            section["baseline_frames"] = tuple(bf)

    if cls == DffSettings:
        if "baseline_frames" in section:
            bf = section["baseline_frames"]
            if isinstance(bf, list):
                section["baseline_frames"] = tuple(bf)
        if "clip" in section:
            c = section["clip"]
            if isinstance(c, list):
                section["clip"] = tuple(c)

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
        bleach_correction=_parse_nested_config(data, "bleach_correction", BleachCorrectionSettings),
        denoise=_parse_nested_config(data, "denoise", DenoiseSettings),
        registration=_parse_nested_config(data, "registration", RegistrationSettings),
        annotation=_parse_annotation_config(data),
        timing=_parse_nested_config(data, "timing", TimingSettings),
        dff=_parse_nested_config(data, "dff", DffSettings),
        projections=_parse_nested_config(data, "projections", ProjectionSettings),
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
