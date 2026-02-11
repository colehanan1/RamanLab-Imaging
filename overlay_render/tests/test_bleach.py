"""
Tests for bleach/drift correction.

All tests use synthetic data with seeded RNG for determinism.
"""

import numpy as np
import pytest

from overlay_render.bleach import (
    BleachCorrector,
    apply_bleach_correction_frame,
    compute_intensity_trace,
    fit_bleach_trend,
    format_preproc_banner,
    precompute_bleach_corrector,
)
from overlay_render.config import (
    BleachCorrectionSettings,
    DenoiseSettings,
    ViewSettings,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_clean_blob_image(shape=(128, 128), seed=42):
    """Create a clean image with Gaussian blobs and edges (deterministic)."""
    rng = np.random.RandomState(seed)
    y, x = np.ogrid[:shape[0], :shape[1]]
    image = np.zeros(shape, dtype=np.float32)

    centers = [
        (shape[0] // 4, shape[1] // 4, 20, 0.8),
        (3 * shape[0] // 4, shape[1] // 4, 15, 0.6),
        (shape[0] // 2, 3 * shape[1] // 4, 25, 0.9),
    ]
    for cy, cx, radius, intensity in centers:
        mask = (x - cx) ** 2 + (y - cy) ** 2 < radius ** 2
        image[mask] = intensity

    # Smooth background
    image += 0.05
    return image


def make_bleaching_stack(
    n_frames=100,
    shape=(128, 128),
    a=1.0,
    k=0.02,
    c=0.3,
    noise_std=0.02,
    seed=42,
):
    """
    Create a time series with exponential bleach + noise.

    I[t] = I_clean * (a * exp(-k * t) + c) + gaussian_noise
    """
    rng = np.random.RandomState(seed)
    I_clean = make_clean_blob_image(shape, seed=seed)

    frames = []
    for t in range(n_frames):
        bleach_factor = a * np.exp(-k * t) + c
        frame = I_clean * bleach_factor
        frame += rng.normal(0, noise_std, shape).astype(np.float32)
        frames.append(np.clip(frame, 0, None).astype(np.float32))

    return np.stack(frames, axis=0), I_clean


# ---------------------------------------------------------------------------
# Synthetic test: correction flattens intensity trace
# ---------------------------------------------------------------------------

class TestBleachCorrectionSynthetic:
    """Quantitative tests on synthetic bleaching data."""

    def test_exp_global_flattens_trace(self):
        """Exponential correction should flatten median intensity over time."""
        stack, _ = make_bleaching_stack(n_frames=100, seed=123)

        settings = BleachCorrectionSettings(
            enabled=True,
            method="exp_global",
            baseline_frames=100,
            apply_mode="divide",
        )

        corrector = BleachCorrector(
            trace=compute_intensity_trace(frames=stack),
            settings=settings,
        )

        # Compute trace before and after
        trace_raw = np.array([np.median(stack[t]) for t in range(100)])
        corrected = np.stack(
            [corrector.correct_frame(stack[t], t) for t in range(100)]
        )
        trace_corrected = np.array(
            [np.median(corrected[t]) for t in range(100)]
        )

        std_raw = np.std(trace_raw)
        std_corrected = np.std(trace_corrected)

        assert std_corrected <= 0.3 * std_raw, (
            f"Corrected trace std ({std_corrected:.4f}) should be <= 30% of "
            f"raw trace std ({std_raw:.4f})"
        )

    def test_poly_global_flattens_trace(self):
        """Polynomial correction should also flatten the trace."""
        stack, _ = make_bleaching_stack(n_frames=100, seed=456)

        settings = BleachCorrectionSettings(
            enabled=True,
            method="poly_global",
            baseline_frames=100,
            poly_order=2,
            apply_mode="divide",
        )

        corrector = BleachCorrector(
            trace=compute_intensity_trace(frames=stack),
            settings=settings,
        )

        trace_raw = np.array([np.median(stack[t]) for t in range(100)])
        corrected = np.stack(
            [corrector.correct_frame(stack[t], t) for t in range(100)]
        )
        trace_corrected = np.array(
            [np.median(corrected[t]) for t in range(100)]
        )

        std_raw = np.std(trace_raw)
        std_corrected = np.std(trace_corrected)

        assert std_corrected <= 0.3 * std_raw, (
            f"Corrected trace std ({std_corrected:.4f}) should be <= 30% of "
            f"raw trace std ({std_raw:.4f})"
        )

    def test_subtract_mode_flattens_trace(self):
        """Subtract mode should also flatten the trace."""
        stack, _ = make_bleaching_stack(n_frames=80, seed=789)

        settings = BleachCorrectionSettings(
            enabled=True,
            method="exp_global",
            baseline_frames=80,
            apply_mode="subtract",
        )

        corrector = BleachCorrector(
            trace=compute_intensity_trace(frames=stack),
            settings=settings,
        )

        trace_raw = np.array([np.median(stack[t]) for t in range(80)])
        corrected = np.stack(
            [corrector.correct_frame(stack[t], t) for t in range(80)]
        )
        trace_corrected = np.array(
            [np.median(corrected[t]) for t in range(80)]
        )

        std_raw = np.std(trace_raw)
        std_corrected = np.std(trace_corrected)

        assert std_corrected <= 0.3 * std_raw, (
            f"Subtract mode: corrected trace std ({std_corrected:.4f}) should "
            f"be <= 30% of raw ({std_raw:.4f})"
        )

    def test_mse_to_clean_improves(self):
        """After correction, MSE to clean reference should decrease on average."""
        a, k, c_offset = 1.0, 0.02, 0.3
        stack, I_clean = make_bleaching_stack(
            n_frames=60, noise_std=0.01, seed=321, a=a, k=k, c=c_offset,
        )

        settings = BleachCorrectionSettings(
            enabled=True,
            method="exp_global",
            baseline_frames=60,
            apply_mode="divide",
        )

        corrector = BleachCorrector(
            trace=compute_intensity_trace(frames=stack),
            settings=settings,
        )

        # After divide-mode correction, all frames are normalised to the
        # t=0 intensity level: I_clean * (a + c).
        target = I_clean * (a + c_offset)

        mse_raw = np.mean([(np.mean((stack[t] - target) ** 2)) for t in range(60)])
        corrected = [corrector.correct_frame(stack[t], t) for t in range(60)]
        mse_corr = np.mean([(np.mean((c - target) ** 2)) for c in corrected])

        assert mse_corr < mse_raw, (
            f"MSE should decrease after correction "
            f"(raw={mse_raw:.6f}, corrected={mse_corr:.6f})"
        )

    def test_baseline_frames_tuple(self):
        """Baseline specified as (start, end) tuple should work."""
        stack, _ = make_bleaching_stack(n_frames=100, seed=111)

        settings = BleachCorrectionSettings(
            enabled=True,
            method="poly_global",
            baseline_frames=(10, 50),
            poly_order=2,
            apply_mode="divide",
        )

        corrector = BleachCorrector(
            trace=compute_intensity_trace(frames=stack),
            settings=settings,
        )

        # Should produce valid corrector without error
        corrected = corrector.correct_frame(stack[0], 0)
        assert corrected.shape == stack[0].shape
        assert np.isfinite(corrected).all()


# ---------------------------------------------------------------------------
# Validation tests: error handling
# ---------------------------------------------------------------------------

class TestBleachCorrectionValidation:
    """Test error handling and edge cases."""

    def test_invalid_method_raises(self):
        """Invalid method should raise ValueError with field path."""
        with pytest.raises(ValueError, match="bleach_correction.method"):
            BleachCorrectionSettings(method="invalid_method")

    def test_invalid_apply_mode_raises(self):
        """Invalid apply_mode should raise ValueError with field path."""
        with pytest.raises(ValueError, match="bleach_correction.apply_mode"):
            BleachCorrectionSettings(apply_mode="multiply")

    def test_invalid_baseline_frames_negative(self):
        """Negative baseline_frames should raise ValueError."""
        with pytest.raises(ValueError, match="bleach_correction.baseline_frames"):
            BleachCorrectionSettings(baseline_frames=0)

    def test_invalid_baseline_frames_tuple_order(self):
        """Reversed tuple baseline_frames should raise ValueError."""
        with pytest.raises(ValueError, match="bleach_correction.baseline_frames"):
            BleachCorrectionSettings(baseline_frames=(50, 10))

    def test_invalid_baseline_frames_tuple_length(self):
        """Tuple with != 2 elements should raise ValueError."""
        with pytest.raises(ValueError, match="bleach_correction.baseline_frames"):
            BleachCorrectionSettings(baseline_frames=(1, 2, 3))

    def test_invalid_poly_order(self):
        """poly_order < 1 should raise ValueError."""
        with pytest.raises(ValueError, match="bleach_correction.poly_order"):
            BleachCorrectionSettings(poly_order=0)

    def test_invalid_epsilon(self):
        """Non-positive epsilon should raise ValueError."""
        with pytest.raises(ValueError, match="bleach_correction.epsilon"):
            BleachCorrectionSettings(epsilon=0.0)

    def test_divide_nonpositive_trend_raises(self):
        """Divide mode with non-positive trend should raise ValueError."""
        # Create a frame and force a negative trend value
        frame = np.ones((10, 10), dtype=np.float32)

        settings = BleachCorrectionSettings(
            enabled=True,
            method="exp_global",
            apply_mode="divide",
            epsilon=1e-6,
        )

        with pytest.raises(ValueError, match="non-positive trend"):
            apply_bleach_correction_frame(frame, -1.0, 1.0, settings)

    def test_disabled_is_strict_noop(self):
        """Disabled bleach correction must be a strict no-op (exact equality)."""
        stack, _ = make_bleaching_stack(n_frames=20, seed=999)

        settings = BleachCorrectionSettings(enabled=False)

        corrector = precompute_bleach_corrector(
            settings=settings,
            frames=[stack[t] for t in range(20)],
        )

        assert corrector is None, "Disabled bleach should return None corrector"

    def test_method_none_is_strict_noop(self):
        """method='none' must also return None corrector."""
        stack, _ = make_bleaching_stack(n_frames=20, seed=888)

        settings = BleachCorrectionSettings(enabled=True, method="none")

        corrector = precompute_bleach_corrector(
            settings=settings,
            frames=[stack[t] for t in range(20)],
        )

        assert corrector is None, "method='none' should return None corrector"

    def test_handles_nan_inf_in_frames(self):
        """Frames with NaN/inf should not crash; NaN/inf replaced with 0."""
        frame = np.ones((10, 10), dtype=np.float32)
        frame[3, 3] = np.nan
        frame[5, 5] = np.inf

        settings = BleachCorrectionSettings(
            enabled=True, method="exp_global", apply_mode="divide"
        )

        result = apply_bleach_correction_frame(frame, 1.0, 1.0, settings)
        assert np.isfinite(result).all(), "Result should not contain NaN or inf"

    def test_output_is_float32(self):
        """Corrected frames must be float32."""
        frame = np.ones((10, 10), dtype=np.float64) * 100
        settings = BleachCorrectionSettings(
            enabled=True, method="exp_global", apply_mode="divide"
        )

        result = apply_bleach_correction_frame(frame, 100.0, 100.0, settings)
        assert result.dtype == np.float32

    def test_list_coerced_to_tuple(self):
        """baseline_frames passed as list (from YAML) should be coerced to tuple."""
        settings = BleachCorrectionSettings(baseline_frames=[10, 50])
        assert isinstance(settings.baseline_frames, tuple)
        assert settings.baseline_frames == (10, 50)


# ---------------------------------------------------------------------------
# Config integration tests
# ---------------------------------------------------------------------------

class TestBleachCorrectionConfig:
    """Test config loading and overrides for bleach_correction."""

    def test_default_disabled(self):
        """Default BleachCorrectionSettings should be disabled."""
        settings = BleachCorrectionSettings()
        assert settings.enabled is False
        assert settings.method == "none"

    def test_all_valid_methods(self):
        """All valid methods should be accepted."""
        for method in ("none", "exp_global", "poly_global"):
            s = BleachCorrectionSettings(method=method)
            assert s.method == method

    def test_all_valid_apply_modes(self):
        """All valid apply modes should be accepted."""
        for mode in ("divide", "subtract"):
            s = BleachCorrectionSettings(apply_mode=mode)
            assert s.apply_mode == mode


# ---------------------------------------------------------------------------
# Preprocessing banner test
# ---------------------------------------------------------------------------

class TestPreprocBanner:
    """Test the preprocessing banner formatting."""

    def test_banner_disabled(self):
        """Banner with everything disabled."""
        banner = format_preproc_banner(
            BleachCorrectionSettings(),
            DenoiseSettings(),
            ViewSettings(),
        )
        assert "bleach(enabled=False)" in banner
        assert "denoise(off)" in banner
        assert "view(" in banner

    def test_banner_enabled(self):
        """Banner with bleach enabled."""
        banner = format_preproc_banner(
            BleachCorrectionSettings(
                enabled=True, method="exp_global", baseline_frames=30
            ),
            DenoiseSettings(enabled=True, method="bilateral", strength=10),
            ViewSettings(p_lo=5, p_hi=95, gamma=1.2, clahe=True),
        )
        assert "bleach(enabled=True" in banner
        assert "method=exp_global" in banner
        assert "baseline=30" in banner
        assert "mode=divide" in banner
        assert "denoise(method=bilateral" in banner
        assert "view(p_lo=5" in banner


# ---------------------------------------------------------------------------
# Intensity trace computation
# ---------------------------------------------------------------------------

class TestIntensityTrace:
    """Test compute_intensity_trace."""

    def test_trace_from_array(self):
        """Trace from 3D numpy array."""
        rng = np.random.RandomState(42)
        stack = rng.rand(10, 32, 32).astype(np.float32)
        trace = compute_intensity_trace(frames=stack)

        assert trace.shape == (10,)
        assert trace.dtype == np.float64
        # Median of uniform [0,1] should be ~0.5
        assert np.all(trace > 0.3)
        assert np.all(trace < 0.7)

    def test_trace_from_list(self):
        """Trace from list of 2D frames."""
        rng = np.random.RandomState(42)
        frames = [rng.rand(32, 32).astype(np.float32) for _ in range(5)]
        trace = compute_intensity_trace(frames=frames)

        assert trace.shape == (5,)
        assert trace.dtype == np.float64
