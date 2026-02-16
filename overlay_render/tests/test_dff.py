"""
Unit tests for ΔF/F (delta-F over F0) computation.

Tests cover:
- compute_f0 with mean and percentile methods
- apply_dff correctness (baseline ~0, stimulus positive)
- determine_baseline_frames for "odor" and "explicit" modes
- DffSettings validation
- Integration: pipeline produces DFF TIFF with correct dtype/shape
"""

import json
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest


class TestComputeF0:
    """Tests for compute_f0()."""

    def test_mean_method(self):
        from overlay_render.dff import compute_f0

        # 10 frames, 4x4 image; baseline frames all have value 100
        frames = np.full((10, 4, 4), 100.0, dtype=np.float32)
        # Make non-baseline frames different (should not affect F0)
        frames[5:] = 500.0
        baseline_idx = np.arange(0, 5)

        f0 = compute_f0(frames, baseline_idx, method="mean")

        assert f0.shape == (4, 4)
        assert f0.dtype == np.float32
        np.testing.assert_allclose(f0, 100.0)

    def test_percentile_method(self):
        from overlay_render.dff import compute_f0

        # Create baseline with known distribution: values 0..9 per pixel
        frames = np.zeros((10, 2, 2), dtype=np.float32)
        for i in range(10):
            frames[i] = float(i * 10)  # 0, 10, 20, ..., 90
        baseline_idx = np.arange(10)

        f0 = compute_f0(frames, baseline_idx, method="percentile", percentile=50.0)

        # numpy percentile of [0,10,20,...,90] at 50th → 45.0
        assert f0.shape == (2, 2)
        np.testing.assert_allclose(f0, 45.0)

    def test_empty_baseline_raises(self):
        from overlay_render.dff import compute_f0

        frames = np.zeros((10, 4, 4), dtype=np.float32)
        with pytest.raises(ValueError, match="at least one"):
            compute_f0(frames, np.array([], dtype=int))

    def test_out_of_range_baseline_raises(self):
        from overlay_render.dff import compute_f0

        frames = np.zeros((10, 4, 4), dtype=np.float32)
        with pytest.raises(ValueError, match="must be in"):
            compute_f0(frames, np.array([10]))

    def test_invalid_method_raises(self):
        from overlay_render.dff import compute_f0

        frames = np.zeros((5, 4, 4), dtype=np.float32)
        with pytest.raises(ValueError, match="must be 'mean' or 'percentile'"):
            compute_f0(frames, np.arange(3), method="median")


class TestApplyDff:
    """Tests for apply_dff()."""

    def test_baseline_near_zero(self):
        from overlay_render.dff import apply_dff

        # F0 = 100 everywhere.  Baseline frames (value 100) should yield ~0.
        f0 = np.full((4, 4), 100.0, dtype=np.float32)
        frames = np.full((5, 4, 4), 100.0, dtype=np.float32)

        dff = apply_dff(frames, f0)

        assert dff.shape == frames.shape
        assert dff.dtype == np.float32
        np.testing.assert_allclose(dff, 0.0, atol=1e-5)

    def test_stimulus_positive(self):
        from overlay_render.dff import apply_dff

        f0 = np.full((4, 4), 100.0, dtype=np.float32)
        # Stimulus frames at 200 → DFF = (200-100)/(100+eps) ≈ 1.0
        frames = np.full((3, 4, 4), 200.0, dtype=np.float32)

        dff = apply_dff(frames, f0)

        np.testing.assert_allclose(dff, 1.0, atol=1e-4)

    def test_negative_dff(self):
        from overlay_render.dff import apply_dff

        f0 = np.full((4, 4), 100.0, dtype=np.float32)
        # Frames at 50 → DFF = (50-100)/(100+eps) ≈ -0.5
        frames = np.full((2, 4, 4), 50.0, dtype=np.float32)

        dff = apply_dff(frames, f0)

        np.testing.assert_allclose(dff, -0.5, atol=1e-4)

    def test_clip(self):
        from overlay_render.dff import apply_dff

        f0 = np.full((4, 4), 100.0, dtype=np.float32)
        frames = np.zeros((3, 4, 4), dtype=np.float32)
        frames[0] = 50.0   # DFF ≈ -0.5
        frames[1] = 100.0  # DFF ≈ 0.0
        frames[2] = 300.0  # DFF ≈ 2.0

        dff = apply_dff(frames, f0, clip=(-0.2, 1.5))

        # Frame 0: -0.5 clipped to -0.2
        np.testing.assert_allclose(dff[0], -0.2, atol=1e-4)
        # Frame 1: 0.0 unchanged
        np.testing.assert_allclose(dff[1], 0.0, atol=1e-4)
        # Frame 2: 2.0 clipped to 1.5
        np.testing.assert_allclose(dff[2], 1.5, atol=1e-4)

    def test_eps_prevents_division_by_zero(self):
        from overlay_render.dff import apply_dff

        f0 = np.zeros((4, 4), dtype=np.float32)
        frames = np.full((2, 4, 4), 1.0, dtype=np.float32)

        dff = apply_dff(frames, f0, eps=1.0)

        # DFF = (1 - 0) / (0 + 1) = 1.0
        np.testing.assert_allclose(dff, 1.0, atol=1e-5)
        assert np.all(np.isfinite(dff))


class TestDetermineBaselineFrames:
    """Tests for determine_baseline_frames()."""

    @dataclass
    class FakeInterval:
        start_frame: int
        end_frame: int

    def test_explicit_source(self):
        from overlay_render.dff import determine_baseline_frames

        idx = determine_baseline_frames(
            baseline_source="explicit",
            baseline_frames_range=(5, 15),
            odor_intervals=[],
            n_frames=100,
        )

        np.testing.assert_array_equal(idx, np.arange(5, 16))

    def test_explicit_missing_range_raises(self):
        from overlay_render.dff import determine_baseline_frames

        with pytest.raises(ValueError, match="baseline_frames must be set"):
            determine_baseline_frames("explicit", None, [], 100)

    def test_explicit_out_of_range_raises(self):
        from overlay_render.dff import determine_baseline_frames

        with pytest.raises(ValueError, match="out of valid range"):
            determine_baseline_frames("explicit", (0, 200), [], 100)

    def test_odor_source(self):
        from overlay_render.dff import determine_baseline_frames

        intervals = [
            self.FakeInterval(start_frame=20, end_frame=40),
            self.FakeInterval(start_frame=60, end_frame=80),
        ]
        idx = determine_baseline_frames("odor", None, intervals, 100)

        # Should be frames 0..19 (before first odor at 20)
        np.testing.assert_array_equal(idx, np.arange(0, 20))

    def test_odor_source_no_intervals_raises(self):
        from overlay_render.dff import determine_baseline_frames

        with pytest.raises(ValueError, match="no odor intervals found"):
            determine_baseline_frames("odor", None, [], 100)

    def test_odor_source_starts_at_zero_raises(self):
        from overlay_render.dff import determine_baseline_frames

        intervals = [self.FakeInterval(start_frame=0, end_frame=10)]
        with pytest.raises(ValueError, match="no pre-odor baseline"):
            determine_baseline_frames("odor", None, intervals, 100)

    def test_invalid_source_raises(self):
        from overlay_render.dff import determine_baseline_frames

        with pytest.raises(ValueError, match="must be 'odor' or 'explicit'"):
            determine_baseline_frames("auto", None, [], 100)


class TestDffSettings:
    """Tests for DffSettings config dataclass."""

    def test_defaults(self):
        from overlay_render.config import DffSettings

        s = DffSettings()
        assert s.enabled is False
        assert s.baseline_source == "odor"
        assert s.baseline_frames is None
        assert s.f0_method == "mean"
        assert s.eps == 1e-6
        assert s.clip is None
        assert s.save_tiff is True
        assert s.output_name == "dff.tif"

    def test_list_to_tuple_coercion(self):
        from overlay_render.config import DffSettings

        s = DffSettings(baseline_frames=[10, 30], clip=[-0.2, 2.0])
        assert isinstance(s.baseline_frames, tuple)
        assert isinstance(s.clip, tuple)
        assert s.baseline_frames == (10, 30)
        assert s.clip == (-0.2, 2.0)

    def test_invalid_baseline_source(self):
        from overlay_render.config import DffSettings

        with pytest.raises(ValueError, match="baseline_source"):
            DffSettings(baseline_source="auto")

    def test_invalid_f0_method(self):
        from overlay_render.config import DffSettings

        with pytest.raises(ValueError, match="f0_method"):
            DffSettings(f0_method="median")

    def test_invalid_eps(self):
        from overlay_render.config import DffSettings

        with pytest.raises(ValueError, match="eps"):
            DffSettings(eps=-1.0)

    def test_invalid_clip_order(self):
        from overlay_render.config import DffSettings

        with pytest.raises(ValueError, match="clip min must be < max"):
            DffSettings(clip=(2.0, -0.2))


class TestDffConfigLoading:
    """Test that DFF config section loads correctly from YAML."""

    def test_load_config_with_dff_section(self):
        from overlay_render.config import load_config

        tmpdir = Path(tempfile.mkdtemp(prefix="dff_cfg_test_"))
        try:
            # Create minimal required files
            struct = tmpdir / "s.tif"
            rec = tmpdir / "r.tif"
            try:
                import tifffile
                tifffile.imwrite(str(struct), np.zeros((8, 8), dtype=np.uint16))
                tifffile.imwrite(str(rec), np.zeros((3, 8, 8), dtype=np.uint16))
            except ImportError:
                pytest.skip("tifffile required")

            config_content = f"""
structure_path: "{struct}"
recording_path: "{rec}"
output_dir: "{tmpdir / 'out'}"
timing:
  fps: 10
dff:
  enabled: true
  baseline_source: "explicit"
  baseline_frames: [0, 1]
  f0_method: "percentile"
  f0_percentile: 20
  eps: 1e-5
  clip: [-0.2, 2.0]
  save_tiff: true
  output_name: "my_dff.tif"
"""
            cfg_path = tmpdir / "cfg.yaml"
            cfg_path.write_text(config_content)

            config = load_config(cfg_path)

            assert config.dff.enabled is True
            assert config.dff.baseline_source == "explicit"
            assert config.dff.baseline_frames == (0, 1)
            assert config.dff.f0_method == "percentile"
            assert config.dff.f0_percentile == 20
            assert config.dff.clip == (-0.2, 2.0)
            assert config.dff.output_name == "my_dff.tif"
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_load_config_without_dff_section(self):
        """Existing configs without dff section should get defaults."""
        from overlay_render.config import load_config

        tmpdir = Path(tempfile.mkdtemp(prefix="dff_cfg_test_"))
        try:
            struct = tmpdir / "s.tif"
            rec = tmpdir / "r.tif"
            try:
                import tifffile
                tifffile.imwrite(str(struct), np.zeros((8, 8), dtype=np.uint16))
                tifffile.imwrite(str(rec), np.zeros((3, 8, 8), dtype=np.uint16))
            except ImportError:
                pytest.skip("tifffile required")

            config_content = f"""
structure_path: "{struct}"
recording_path: "{rec}"
output_dir: "{tmpdir / 'out'}"
timing:
  fps: 10
"""
            cfg_path = tmpdir / "cfg.yaml"
            cfg_path.write_text(config_content)

            config = load_config(cfg_path)

            assert config.dff.enabled is False
            assert config.dff.baseline_source == "odor"
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


class TestDffIntegration:
    """Integration test: run pipeline with DFF enabled on synthetic data."""

    @pytest.fixture
    def test_dir(self):
        tmpdir = Path(tempfile.mkdtemp(prefix="dff_integ_"))
        yield tmpdir
        shutil.rmtree(tmpdir, ignore_errors=True)

    def test_pipeline_produces_dff_tiff(self, test_dir):
        try:
            import tifffile
        except ImportError:
            pytest.skip("tifffile required")

        n_frames = 30
        shape = (64, 64)

        # Create synthetic data: baseline=100, stimulus=200
        frames = np.full((n_frames, *shape), 100.0, dtype=np.uint16)
        # Stimulus in frames 15-25
        frames[15:25] = 200

        struct = np.full(shape, 5000, dtype=np.uint16)

        struct_path = test_dir / "structure.tif"
        rec_path = test_dir / "recording.tif"
        tifffile.imwrite(str(struct_path), struct)
        tifffile.imwrite(str(rec_path), frames)

        # Metadata with odor intervals
        meta = {
            "fps": 10.0,
            "odor_intervals": [
                {"start_frame": 15, "end_frame": 25, "odor_name": "test"}
            ],
        }
        meta_path = test_dir / "metadata.json"
        with open(meta_path, "w") as f:
            json.dump(meta, f)

        output_dir = test_dir / "output"

        config_content = f"""
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
  eps: 1e-6
  save_tiff: true
  output_name: "dff.tif"
"""
        config_path = test_dir / "config.yaml"
        config_path.write_text(config_content)

        from overlay_render.config import load_config
        from overlay_render.cli import run_pipeline

        config = load_config(config_path)
        report = run_pipeline(config)

        # Check DFF TIFF exists with correct properties
        dff_path = output_dir / "dff.tif"
        assert dff_path.exists(), f"DFF TIFF not found at {dff_path}"

        dff_data = tifffile.imread(str(dff_path))
        assert dff_data.dtype == np.float32
        assert dff_data.shape == (n_frames, *shape)

        # Baseline frames (0-14) should be near 0
        baseline_mean = np.mean(dff_data[:15])
        assert abs(baseline_mean) < 0.05, f"Baseline ΔF/F should be ~0, got {baseline_mean}"

        # Stimulus frames (15-24) should be ~1.0 (200-100)/100
        stim_mean = np.mean(dff_data[15:25])
        assert stim_mean > 0.8, f"Stimulus ΔF/F should be ~1.0, got {stim_mean}"

        # Check report contains DFF info
        assert "additional" in report
        assert "dff" in report["additional"]
        assert report["additional"]["dff"]["enabled"] is True
        assert report["additional"]["dff"]["n_baseline_frames"] == 15

        # Check video was also produced
        from pathlib import Path as P
        video_files = list(output_dir.glob("*.mp4"))
        assert len(video_files) >= 1, "Expected MP4 output"
