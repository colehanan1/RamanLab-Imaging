"""
Smoke tests for overlay_render.

Tests the full pipeline with synthetic data to verify:
1. Video renders without errors
2. Odor annotation box toggles on correct frames
3. Report is generated correctly
"""

import json
import shutil
import tempfile
from pathlib import Path

import numpy as np
import pytest


def create_synthetic_structure(shape=(256, 256), dtype=np.uint16) -> np.ndarray:
    """Create a synthetic structure image with some features."""
    image = np.zeros(shape, dtype=dtype)

    # Add some circular features (simulating neurons)
    y, x = np.ogrid[:shape[0], :shape[1]]

    # Central feature
    center_y, center_x = shape[0] // 2, shape[1] // 2
    mask = (x - center_x) ** 2 + (y - center_y) ** 2 < 30 ** 2
    image[mask] = 40000

    # Smaller features around
    for offset_y, offset_x in [(50, 50), (-50, 50), (50, -50), (-50, -50)]:
        cy, cx = center_y + offset_y, center_x + offset_x
        mask = (x - cx) ** 2 + (y - cy) ** 2 < 15 ** 2
        image[mask] = 30000

    # Add noise
    noise = np.random.randint(0, 5000, shape, dtype=dtype)
    image = np.clip(image + noise, 0, 65535).astype(dtype)

    return image


def create_synthetic_recording(
    n_frames: int = 100,
    shape: tuple = (256, 256),
    dtype=np.uint16,
    blob_movement: bool = True
) -> np.ndarray:
    """
    Create a synthetic recording with a moving blob.

    Args:
        n_frames: Number of frames.
        shape: Frame shape (H, W).
        dtype: Data type.
        blob_movement: Whether blob should move.

    Returns:
        3D array (T, H, W).
    """
    frames = []
    y, x = np.ogrid[:shape[0], :shape[1]]

    center_y, center_x = shape[0] // 2, shape[1] // 2

    for i in range(n_frames):
        frame = np.zeros(shape, dtype=np.float32)

        # Moving blob position (sinusoidal movement)
        if blob_movement:
            offset_y = int(20 * np.sin(2 * np.pi * i / n_frames))
            offset_x = int(20 * np.cos(2 * np.pi * i / n_frames))
        else:
            offset_y, offset_x = 0, 0

        cy, cx = center_y + offset_y, center_x + offset_x
        mask = (x - cx) ** 2 + (y - cy) ** 2 < 25 ** 2

        # Varying intensity
        intensity = 30000 + 10000 * np.sin(2 * np.pi * i / 50)
        frame[mask] = intensity

        # Background features
        frame += np.random.randint(0, 3000, shape)

        frames.append(np.clip(frame, 0, 65535).astype(dtype))

    return np.stack(frames, axis=0)


def create_synthetic_odor_intervals(n_frames: int) -> list:
    """
    Create synthetic odor intervals.

    Odor is ON for frames 20-40 and 60-80.
    """
    return [
        {"start_frame": 20, "end_frame": 40, "odor_name": "test_odor_A"},
        {"start_frame": 60, "end_frame": 80, "odor_name": "test_odor_B"},
    ]


def create_test_files(tmpdir: Path, n_frames: int = 100):
    """
    Create all test files in a temporary directory.

    Returns:
        Tuple of paths: (structure_path, recording_path, metadata_path, config_path)
    """
    # Create structure image
    structure = create_synthetic_structure()
    structure_path = tmpdir / "structure.tif"

    try:
        import tifffile
        tifffile.imwrite(structure_path, structure)
    except ImportError:
        pytest.skip("tifffile required for this test")

    # Create recording
    recording = create_synthetic_recording(n_frames=n_frames)
    recording_path = tmpdir / "recording.tif"
    tifffile.imwrite(recording_path, recording)

    # Create metadata JSON
    metadata = {
        "fps": 10.0,
        "odor_intervals": create_synthetic_odor_intervals(n_frames),
        "experiment": "smoke_test",
    }
    metadata_path = tmpdir / "metadata.json"
    with open(metadata_path, "w") as f:
        json.dump(metadata, f)

    # Create config YAML
    config_content = f"""
structure_path: "{structure_path}"
recording_path: "{recording_path}"
output_dir: "{tmpdir / 'output'}"
metadata_json_path: "{metadata_path}"

overlay:
  alpha: 0.5
  mode: "falsecolor"

view:
  method: "percentile"
  p_lo: 1
  p_hi: 99
  gamma: 1.0
  clahe: false

registration:
  enabled: true
  model: "euclidean"
  ecc_iters: 50
  ecc_eps: 1e-5
  downscale: 2

annotation:
  show_box_when_off: false
  box:
    anchor: "bottom_left"
    width_px: 200
    height_px: 60
    margin_px: 10
  text:
    "on": "ODOR ON"
    "off": "ODOR OFF"
    font_scale: 1.0
    thickness: 2

timing:
  fps: 10.0
  odor_source: "auto"
"""
    config_path = tmpdir / "config.yaml"
    with open(config_path, "w") as f:
        f.write(config_content)

    return structure_path, recording_path, metadata_path, config_path


class TestSmokeTests:
    """Smoke tests for the overlay_render pipeline."""

    @pytest.fixture
    def test_dir(self):
        """Create and cleanup temporary test directory."""
        tmpdir = Path(tempfile.mkdtemp(prefix="overlay_render_test_"))
        yield tmpdir
        shutil.rmtree(tmpdir, ignore_errors=True)

    def test_config_loading(self, test_dir):
        """Test configuration loading and validation."""
        from overlay_render.config import load_config

        _, _, _, config_path = create_test_files(test_dir)
        config = load_config(config_path)

        assert config.structure_path.exists()
        assert config.recording_path.exists()
        assert config.overlay.alpha == 0.5
        assert config.overlay.mode == "falsecolor"
        assert config.registration.enabled is True
        assert config.timing.fps == 10.0

    def test_structure_loading(self, test_dir):
        """Test structure image loading."""
        from overlay_render.loaders import load_structure

        structure_path, _, _, _ = create_test_files(test_dir)
        structure = load_structure(structure_path)

        assert structure.ndim == 2
        assert structure.shape == (256, 256)
        assert structure.dtype in (np.uint8, np.uint16)

    def test_recording_loading(self, test_dir):
        """Test recording loading."""
        from overlay_render.loaders import load_recording

        _, recording_path, _, _ = create_test_files(test_dir)

        with load_recording(recording_path) as rec:
            assert rec.n_frames == 100
            assert rec.frame_shape == (256, 256)

            # Test frame access
            frame = rec.get_frame(0)
            assert frame.shape == (256, 256)

    def test_timing_extraction(self, test_dir):
        """Test odor timing extraction from JSON."""
        from overlay_render.timing import extract_odor_timing
        from overlay_render.config import TimingSettings

        _, _, metadata_path, _ = create_test_files(test_dir)

        result = extract_odor_timing(
            n_frames=100,
            json_path=metadata_path,
            settings=TimingSettings(fps=10.0)
        )

        assert result.source == "json"
        assert len(result.intervals) == 2
        assert result.intervals[0].start_frame == 20
        assert result.intervals[0].end_frame == 40

        # Test odor mask
        assert result.is_odor_on(30) is True
        assert result.is_odor_on(50) is False
        assert result.is_odor_on(70) is True

    def test_view_scaling(self, test_dir):
        """Test view scaling."""
        from overlay_render.view_scaling import scale_view
        from overlay_render.config import ViewSettings

        structure = create_synthetic_structure()
        settings = ViewSettings(method="percentile", p_lo=1, p_hi=99, gamma=1.0)

        scaled = scale_view(structure, settings)

        assert scaled.dtype == np.uint8
        assert scaled.min() >= 0
        assert scaled.max() <= 255

    def test_overlay_compositing(self, test_dir):
        """Test overlay compositing."""
        from overlay_render.overlay import composite_frame
        from overlay_render.config import OverlaySettings

        structure = np.random.randint(0, 256, (100, 100), dtype=np.uint8)
        recording = np.random.randint(0, 256, (100, 100), dtype=np.uint8)

        # Test blend mode
        settings = OverlaySettings(alpha=0.5, mode="blend")
        result = composite_frame(structure, recording, settings)
        assert result.shape == (100, 100, 3)
        assert result.dtype == np.uint8

        # Test falsecolor mode
        settings = OverlaySettings(alpha=0.5, mode="falsecolor")
        result = composite_frame(structure, recording, settings)
        assert result.shape == (100, 100, 3)

    def test_annotation_drawing(self, test_dir):
        """Test annotation drawing."""
        from overlay_render.annotation import draw_odor_annotation
        from overlay_render.config import AnnotationSettings

        frame = np.zeros((256, 256, 3), dtype=np.uint8)
        settings = AnnotationSettings()

        # Test odor ON
        result = draw_odor_annotation(frame.copy(), is_odor_on=True, settings=settings)
        # Should have white box (check bottom-left region is not all black)
        bottom_left = result[200:, :100, :]
        assert bottom_left.sum() > 0

        # Test odor OFF (show_box_when_off=False by default)
        result = draw_odor_annotation(frame.copy(), is_odor_on=False, settings=settings)
        # Should be unchanged
        assert np.array_equal(result, frame)

    def test_registration(self, test_dir):
        """Test image registration."""
        from overlay_render.registration import compute_registration
        from overlay_render.config import RegistrationSettings

        # Create reference and slightly shifted target
        reference = create_synthetic_structure()
        target = np.roll(reference, 5, axis=0)  # Shift by 5 pixels

        settings = RegistrationSettings(
            enabled=True,
            model="euclidean",
            ecc_iters=50,
            downscale=2
        )

        result = compute_registration(reference, target, settings)

        assert result.warp_matrix.shape == (2, 3)
        # Registration may or may not converge on synthetic data
        # Just check it doesn't crash

    def test_full_pipeline(self, test_dir):
        """Test the full rendering pipeline."""
        from overlay_render.cli import run_pipeline
        from overlay_render.config import load_config

        _, _, _, config_path = create_test_files(test_dir, n_frames=50)
        config = load_config(config_path)

        # Run pipeline
        report = run_pipeline(config, save_thumbnail_flag=True)

        # Check outputs exist
        output_dir = config.output_dir
        assert (output_dir / "recording_overlay.mp4").exists()
        assert (output_dir / "recording_report.json").exists()
        assert (output_dir / "recording_thumbnail.png").exists()

        # Check report contents
        assert report["processing"]["n_frames_processed"] == 50
        assert report["processing"]["fps_used"] == 10.0
        assert report["timing"]["source"] == "json"
        assert len(report["timing"]["intervals"]) == 2

    def test_odor_box_frame_accuracy(self, test_dir):
        """
        Test that odor annotation appears on exactly the correct frames.

        This is the key acceptance test for the annotation feature.
        """
        from overlay_render.timing import extract_odor_timing
        from overlay_render.config import TimingSettings

        _, _, metadata_path, _ = create_test_files(test_dir, n_frames=100)

        result = extract_odor_timing(
            n_frames=100,
            json_path=metadata_path,
            settings=TimingSettings(fps=10.0)
        )

        # Verify exact frame accuracy
        # Odor ON: frames 20-40 and 60-80
        odor_frames = set()
        for i in range(20, 41):
            odor_frames.add(i)
        for i in range(60, 81):
            odor_frames.add(i)

        for frame_idx in range(100):
            expected_on = frame_idx in odor_frames
            actual_on = result.is_odor_on(frame_idx)
            assert actual_on == expected_on, \
                f"Frame {frame_idx}: expected {expected_on}, got {actual_on}"


class TestEdgeCases:
    """Test edge cases and error handling."""

    @pytest.fixture
    def test_dir(self):
        """Create and cleanup temporary test directory."""
        tmpdir = Path(tempfile.mkdtemp(prefix="overlay_render_test_"))
        yield tmpdir
        shutil.rmtree(tmpdir, ignore_errors=True)

    def test_missing_structure_file(self, test_dir):
        """Test error when structure file doesn't exist."""
        from overlay_render.config import OverlayConfig

        with pytest.raises(FileNotFoundError):
            OverlayConfig(
                structure_path=test_dir / "nonexistent.tif",
                recording_path=test_dir / "nonexistent.tif",
                output_dir=test_dir / "output"
            )

    def test_invalid_alpha(self, test_dir):
        """Test error for invalid alpha value."""
        from overlay_render.config import OverlaySettings

        with pytest.raises(ValueError):
            OverlaySettings(alpha=1.5)

        with pytest.raises(ValueError):
            OverlaySettings(alpha=-0.1)

    def test_empty_odor_intervals(self, test_dir):
        """Test handling of no odor intervals."""
        from overlay_render.timing import extract_odor_timing
        from overlay_render.config import TimingSettings

        # No CSV or JSON provided
        result = extract_odor_timing(
            n_frames=100,
            csv_path=None,
            json_path=None,
            settings=TimingSettings()
        )

        assert result.source == "none"
        assert len(result.intervals) == 0
        assert result.is_odor_on(50) is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
