"""
Unit tests for activity summary projections.

Tests cover:
- Projection computations (mean, std, max)
- Epoch determination from odor intervals
- Image scaling to uint8
- ProjectionSettings validation
- Config loading with/without projections section
- Integration: pipeline produces projection PNGs with correct properties
"""

import json
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import pytest


# ---------------------------------------------------------------------------
# Unit tests for projection computations
# ---------------------------------------------------------------------------

class TestMeanProjection:
    def test_basic(self):
        from overlay_render.projections import mean_projection

        frames = np.arange(60, dtype=np.float32).reshape(3, 4, 5)
        idx = np.array([0, 1, 2])
        result = mean_projection(frames, idx)
        assert result.shape == (4, 5)
        assert result.dtype == np.float32
        np.testing.assert_allclose(result, frames.mean(axis=0))

    def test_subset(self):
        from overlay_render.projections import mean_projection

        frames = np.zeros((10, 4, 4), dtype=np.float32)
        frames[0] = 10.0
        frames[1] = 20.0
        idx = np.array([0, 1])
        result = mean_projection(frames, idx)
        np.testing.assert_allclose(result, 15.0)


class TestStdProjection:
    def test_constant_is_zero(self):
        from overlay_render.projections import std_projection

        frames = np.full((5, 4, 4), 42.0, dtype=np.float32)
        idx = np.arange(5)
        result = std_projection(frames, idx)
        np.testing.assert_allclose(result, 0.0)

    def test_known_std(self):
        from overlay_render.projections import std_projection

        frames = np.zeros((2, 4, 4), dtype=np.float32)
        frames[0] = 0.0
        frames[1] = 10.0
        idx = np.array([0, 1])
        result = std_projection(frames, idx)
        # std of [0, 10] = 5.0
        np.testing.assert_allclose(result, 5.0)


class TestMaxProjection:
    def test_basic(self):
        from overlay_render.projections import max_projection

        frames = np.zeros((5, 4, 4), dtype=np.float32)
        frames[3] = 100.0
        idx = np.arange(5)
        result = max_projection(frames, idx)
        np.testing.assert_allclose(result, 100.0)


# ---------------------------------------------------------------------------
# Tests for epoch determination
# ---------------------------------------------------------------------------

class TestDetermineEpochs:
    @dataclass
    class FakeInterval:
        start_frame: int
        end_frame: int

    def test_basic_epochs(self):
        from overlay_render.projections import determine_epochs

        intervals = [self.FakeInterval(start_frame=10, end_frame=20)]
        epochs = determine_epochs(intervals, n_frames=50)

        np.testing.assert_array_equal(epochs.baseline, np.arange(0, 10))
        # Odor frames 10..20 inclusive
        np.testing.assert_array_equal(epochs.odor, np.arange(10, 21))
        # Post frames 21..49
        np.testing.assert_array_equal(epochs.post, np.arange(21, 50))

    def test_post_frames_cap(self):
        from overlay_render.projections import determine_epochs

        intervals = [self.FakeInterval(start_frame=10, end_frame=20)]
        epochs = determine_epochs(intervals, n_frames=100, post_frames=5)

        np.testing.assert_array_equal(epochs.post, np.arange(21, 26))

    def test_multiple_intervals(self):
        from overlay_render.projections import determine_epochs

        intervals = [
            self.FakeInterval(start_frame=10, end_frame=20),
            self.FakeInterval(start_frame=30, end_frame=40),
        ]
        epochs = determine_epochs(intervals, n_frames=60)

        # Baseline: 0..9
        np.testing.assert_array_equal(epochs.baseline, np.arange(0, 10))
        # Odor: union of [10..20] and [30..40]
        expected_odor = np.concatenate([np.arange(10, 21), np.arange(30, 41)])
        np.testing.assert_array_equal(epochs.odor, expected_odor)
        # Post: 41..59
        np.testing.assert_array_equal(epochs.post, np.arange(41, 60))

    def test_no_intervals_raises(self):
        from overlay_render.projections import determine_epochs

        with pytest.raises(ValueError, match="require odor timing"):
            determine_epochs([], n_frames=50)

    def test_odor_at_start_warns(self):
        from overlay_render.projections import determine_epochs

        intervals = [self.FakeInterval(start_frame=0, end_frame=10)]
        epochs = determine_epochs(intervals, n_frames=50)
        # Baseline should be just frame 0 (warning issued)
        np.testing.assert_array_equal(epochs.baseline, np.array([0]))

    def test_odor_at_end_warns(self):
        from overlay_render.projections import determine_epochs

        intervals = [self.FakeInterval(start_frame=10, end_frame=49)]
        epochs = determine_epochs(intervals, n_frames=50)
        # Post should be just the last frame
        np.testing.assert_array_equal(epochs.post, np.array([49]))


# ---------------------------------------------------------------------------
# Tests for image scaling
# ---------------------------------------------------------------------------

class TestScaleToUint8:
    def test_basic_scaling(self):
        from overlay_render.projections import _scale_to_uint8

        img = np.linspace(0, 100, 100).reshape(10, 10).astype(np.float32)
        result = _scale_to_uint8(img, p_lo=0, p_hi=100, gamma=1.0)
        assert result.dtype == np.uint8
        assert result.min() == 0
        assert result.max() == 255

    def test_constant_image(self):
        from overlay_render.projections import _scale_to_uint8

        img = np.full((10, 10), 42.0, dtype=np.float32)
        result = _scale_to_uint8(img)
        # Constant image: vmin == vmax → zeros
        assert result.dtype == np.uint8


# ---------------------------------------------------------------------------
# Tests for ProjectionSettings config
# ---------------------------------------------------------------------------

class TestProjectionSettings:
    def test_defaults(self):
        from overlay_render.config import ProjectionSettings

        s = ProjectionSettings()
        assert s.enabled is False
        assert s.save_png is True
        assert s.save_tiff is False
        assert s.make_rgb_epochs is True
        assert s.post_frames is None
        assert s.p_lo == 1.0
        assert s.p_hi == 99.0
        assert s.gamma == 1.0

    def test_invalid_percentiles(self):
        from overlay_render.config import ProjectionSettings

        with pytest.raises(ValueError, match="percentile bounds"):
            ProjectionSettings(p_lo=99.0, p_hi=1.0)

    def test_invalid_gamma(self):
        from overlay_render.config import ProjectionSettings

        with pytest.raises(ValueError, match="gamma"):
            ProjectionSettings(gamma=-1.0)

    def test_invalid_post_frames(self):
        from overlay_render.config import ProjectionSettings

        with pytest.raises(ValueError, match="post_frames"):
            ProjectionSettings(post_frames=0)


class TestProjectionConfigLoading:
    def test_load_with_projections_section(self):
        from overlay_render.config import load_config

        tmpdir = Path(tempfile.mkdtemp(prefix="proj_cfg_"))
        try:
            struct = tmpdir / "s.tif"
            rec = tmpdir / "r.tif"
            try:
                import tifffile
                tifffile.imwrite(str(struct), np.zeros((8, 8), dtype=np.uint16))
                tifffile.imwrite(str(rec), np.zeros((3, 8, 8), dtype=np.uint16))
            except ImportError:
                pytest.skip("tifffile required")

            cfg_path = tmpdir / "cfg.yaml"
            cfg_path.write_text(f"""
structure_path: "{struct}"
recording_path: "{rec}"
output_dir: "{tmpdir / 'out'}"
timing:
  fps: 10
projections:
  enabled: true
  save_png: true
  save_tiff: true
  make_rgb_epochs: false
  post_frames: 30
  p_lo: 2.0
  p_hi: 98.0
  gamma: 1.5
""")
            config = load_config(cfg_path)
            assert config.projections.enabled is True
            assert config.projections.save_tiff is True
            assert config.projections.make_rgb_epochs is False
            assert config.projections.post_frames == 30
            assert config.projections.p_lo == 2.0
            assert config.projections.gamma == 1.5
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_load_without_projections_section(self):
        from overlay_render.config import load_config

        tmpdir = Path(tempfile.mkdtemp(prefix="proj_cfg_"))
        try:
            struct = tmpdir / "s.tif"
            rec = tmpdir / "r.tif"
            try:
                import tifffile
                tifffile.imwrite(str(struct), np.zeros((8, 8), dtype=np.uint16))
                tifffile.imwrite(str(rec), np.zeros((3, 8, 8), dtype=np.uint16))
            except ImportError:
                pytest.skip("tifffile required")

            cfg_path = tmpdir / "cfg.yaml"
            cfg_path.write_text(f"""
structure_path: "{struct}"
recording_path: "{rec}"
output_dir: "{tmpdir / 'out'}"
timing:
  fps: 10
""")
            config = load_config(cfg_path)
            assert config.projections.enabled is False
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Integration test
# ---------------------------------------------------------------------------

class TestProjectionIntegration:
    @pytest.fixture
    def test_dir(self):
        tmpdir = Path(tempfile.mkdtemp(prefix="proj_integ_"))
        yield tmpdir
        shutil.rmtree(tmpdir, ignore_errors=True)

    def test_pipeline_produces_projections(self, test_dir):
        try:
            import tifffile
        except ImportError:
            pytest.skip("tifffile required")

        n_frames = 50
        shape = (64, 64)

        # Synthetic data with spatial gradient + distinct epochs
        rng = np.random.RandomState(42)
        base_gradient = np.linspace(50, 150, shape[1], dtype=np.float32)[np.newaxis, :]
        frames = np.empty((n_frames, *shape), dtype=np.uint16)
        for i in range(n_frames):
            frames[i] = (base_gradient + rng.normal(0, 5, shape)).clip(0, 65535).astype(np.uint16)
        # Add large stimulus bump during odor epoch
        frames[20:35] = (frames[20:35].astype(np.float32) + 500).clip(0, 65535).astype(np.uint16)
        # Slightly elevated post
        frames[35:] = (frames[35:].astype(np.float32) + 50).clip(0, 65535).astype(np.uint16)

        struct = np.full(shape, 5000, dtype=np.uint16)

        struct_path = test_dir / "structure.tif"
        rec_path = test_dir / "recording.tif"
        tifffile.imwrite(str(struct_path), struct)
        tifffile.imwrite(str(rec_path), frames)

        meta = {
            "fps": 10.0,
            "odor_intervals": [
                {"start_frame": 20, "end_frame": 34, "odor_name": "test"}
            ],
        }
        meta_path = test_dir / "metadata.json"
        with open(meta_path, "w") as f:
            json.dump(meta, f)

        output_dir = test_dir / "output"
        cfg_path = test_dir / "config.yaml"
        cfg_path.write_text(f"""
structure_path: "{struct_path}"
recording_path: "{rec_path}"
output_dir: "{output_dir}"
metadata_json_path: "{meta_path}"
overlay:
  alpha: 0.5
  mode: "blend"
view:
  method: "percentile"
  p_lo: 1
  p_hi: 99
  gamma: 1.0
registration:
  enabled: false
timing:
  fps: 10.0
projections:
  enabled: true
  save_png: true
  save_tiff: false
  make_rgb_epochs: true
""")

        from overlay_render.config import load_config
        from overlay_render.cli import run_pipeline

        config = load_config(cfg_path)
        report = run_pipeline(config)

        proj_dir = output_dir / "projections"
        assert proj_dir.exists()

        # Check all expected PNGs exist
        for name in ["baseline_mean", "odor_mean", "post_mean", "odor_std", "epochs_rgb"]:
            png = proj_dir / f"{name}.png"
            assert png.exists(), f"Missing projection: {png}"

        # Check image properties
        baseline_img = cv2.imread(str(proj_dir / "baseline_mean.png"), cv2.IMREAD_GRAYSCALE)
        odor_img = cv2.imread(str(proj_dir / "odor_mean.png"), cv2.IMREAD_GRAYSCALE)
        assert baseline_img.shape == shape
        assert odor_img.shape == shape

        # Odor mean should be brighter than baseline mean (stimulus = 200 vs 100)
        assert odor_img.mean() > baseline_img.mean(), (
            f"Expected odor mean ({odor_img.mean():.1f}) > baseline mean ({baseline_img.mean():.1f})"
        )

        # RGB image should have 3 channels
        rgb_img = cv2.imread(str(proj_dir / "epochs_rgb.png"))
        assert rgb_img.ndim == 3
        assert rgb_img.shape[2] == 3
        assert rgb_img.dtype == np.uint8

        # Check report includes projections
        assert "additional" in report
        assert "projections" in report["additional"]
        proj_report = report["additional"]["projections"]
        assert "epochs" in proj_report
        assert "images" in proj_report
        assert "baseline_mean" in proj_report["images"]
        assert "rgb_epochs" in proj_report["images"]

    def test_pipeline_with_dff_and_projections(self, test_dir):
        """Projections + DFF: should also produce odor_dff_mean."""
        try:
            import tifffile
        except ImportError:
            pytest.skip("tifffile required")

        n_frames = 50
        shape = (64, 64)

        frames = np.full((n_frames, *shape), 100.0, dtype=np.uint16)
        frames[20:35] = 200
        frames[35:] = 120

        struct = np.full(shape, 5000, dtype=np.uint16)

        struct_path = test_dir / "structure.tif"
        rec_path = test_dir / "recording.tif"
        tifffile.imwrite(str(struct_path), struct)
        tifffile.imwrite(str(rec_path), frames)

        meta = {
            "fps": 10.0,
            "odor_intervals": [
                {"start_frame": 20, "end_frame": 34, "odor_name": "test"}
            ],
        }
        meta_path = test_dir / "metadata.json"
        with open(meta_path, "w") as f:
            json.dump(meta, f)

        output_dir = test_dir / "output"
        cfg_path = test_dir / "config.yaml"
        cfg_path.write_text(f"""
structure_path: "{struct_path}"
recording_path: "{rec_path}"
output_dir: "{output_dir}"
metadata_json_path: "{meta_path}"
overlay:
  alpha: 0.5
  mode: "blend"
view:
  method: "percentile"
  p_lo: 1
  p_hi: 99
  gamma: 1.0
registration:
  enabled: false
timing:
  fps: 10.0
dff:
  enabled: true
  baseline_source: "odor"
  f0_method: "mean"
  save_tiff: false
projections:
  enabled: true
  save_png: true
  make_rgb_epochs: true
""")

        from overlay_render.config import load_config
        from overlay_render.cli import run_pipeline

        config = load_config(cfg_path)
        report = run_pipeline(config)

        proj_dir = output_dir / "projections"
        # Should have the DFF odor mean projection too
        dff_mean_png = proj_dir / "odor_dff_mean.png"
        assert dff_mean_png.exists(), "Missing odor_dff_mean.png"

        proj_report = report["additional"]["projections"]
        assert "odor_dff_mean" in proj_report["images"]

    def test_projections_disabled_no_output(self, test_dir):
        """When projections disabled, no projections dir should be created."""
        try:
            import tifffile
        except ImportError:
            pytest.skip("tifffile required")

        n_frames = 30
        shape = (64, 64)

        frames = np.full((n_frames, *shape), 100.0, dtype=np.uint16)
        struct = np.full(shape, 5000, dtype=np.uint16)

        struct_path = test_dir / "structure.tif"
        rec_path = test_dir / "recording.tif"
        tifffile.imwrite(str(struct_path), struct)
        tifffile.imwrite(str(rec_path), frames)

        meta = {
            "fps": 10.0,
            "odor_intervals": [
                {"start_frame": 10, "end_frame": 20, "odor_name": "test"}
            ],
        }
        meta_path = test_dir / "metadata.json"
        with open(meta_path, "w") as f:
            json.dump(meta, f)

        output_dir = test_dir / "output"
        cfg_path = test_dir / "config.yaml"
        cfg_path.write_text(f"""
structure_path: "{struct_path}"
recording_path: "{rec_path}"
output_dir: "{output_dir}"
metadata_json_path: "{meta_path}"
overlay:
  alpha: 0.5
  mode: "blend"
registration:
  enabled: false
timing:
  fps: 10.0
""")

        from overlay_render.config import load_config
        from overlay_render.cli import run_pipeline

        config = load_config(cfg_path)
        report = run_pipeline(config)

        proj_dir = output_dir / "projections"
        assert not proj_dir.exists()
