"""
Bleach/drift correction for calcium imaging recordings.

Provides global bleach correction by fitting a smooth trend to the per-frame
median intensity and normalising each frame to remove photobleaching drift.

Applied to recording frames only, before denoising and view scaling.
Designed for visualisation (manual labelling), not quantitative analysis.
"""

import logging
from typing import Optional, Tuple, Union

import numpy as np

from .config import BleachCorrectionSettings

logger = logging.getLogger(__name__)


def compute_intensity_trace(
    frames: Union[np.ndarray, list],
    recording_iterator: Optional[object] = None,
    n_frames: Optional[int] = None,
) -> np.ndarray:
    """
    Compute per-frame median intensity trace.

    Accepts either a list/array of frames (preview mode) or a
    RecordingIterator (pipeline mode).

    Args:
        frames: List or 3D array of frames (T, H, W), or None if using iterator.
        recording_iterator: RecordingIterator with .get_frame(i) and .n_frames.
        n_frames: Number of frames (required with recording_iterator).

    Returns:
        1D float64 array of per-frame median intensities.
    """
    if frames is not None:
        if isinstance(frames, np.ndarray) and frames.ndim == 3:
            trace = np.array(
                [np.median(frames[t]) for t in range(frames.shape[0])],
                dtype=np.float64,
            )
        else:
            # list of 2D frames
            trace = np.array(
                [np.median(f) for f in frames], dtype=np.float64
            )
    elif recording_iterator is not None and n_frames is not None:
        trace = np.empty(n_frames, dtype=np.float64)
        for t in range(n_frames):
            frame = recording_iterator.get_frame(t)
            trace[t] = np.median(frame)
    else:
        raise ValueError(
            "compute_intensity_trace requires either frames or "
            "(recording_iterator, n_frames)"
        )

    return trace


def _get_baseline_slice(
    n_frames: int,
    baseline_frames: Union[int, Tuple[int, int]],
) -> slice:
    """
    Determine the baseline frame range for fitting.

    Args:
        n_frames: Total number of frames.
        baseline_frames: Either an int (use first N) or tuple (start, end).

    Returns:
        slice for indexing the baseline window.
    """
    if isinstance(baseline_frames, tuple):
        start, end = baseline_frames
        end = min(end, n_frames)
        return slice(start, end)
    else:
        return slice(0, min(baseline_frames, n_frames))


def fit_bleach_trend(
    trace: np.ndarray,
    settings: BleachCorrectionSettings,
) -> np.ndarray:
    """
    Fit a bleach trend to the intensity trace.

    Args:
        trace: 1D per-frame median intensity array.
        settings: Bleach correction settings.

    Returns:
        1D float64 trend array (same length as trace).

    Raises:
        ValueError: If method is unknown.
    """
    n = len(trace)
    t_all = np.arange(n, dtype=np.float64)

    baseline_sl = _get_baseline_slice(n, settings.baseline_frames)
    t_base = t_all[baseline_sl]
    s_base = trace[baseline_sl]

    if len(s_base) < 2:
        logger.warning(
            "Baseline window has < 2 frames; returning flat trend"
        )
        return np.full(n, trace[0], dtype=np.float64)

    if settings.method == "poly_global":
        return _fit_poly(t_all, t_base, s_base, settings.poly_order)

    elif settings.method == "exp_global":
        return _fit_exp(t_all, t_base, s_base, settings.poly_order)

    else:
        raise ValueError(
            f"bleach_correction.method must be 'exp_global' or 'poly_global', "
            f"got {settings.method!r}"
        )


def _fit_poly(
    t_all: np.ndarray,
    t_base: np.ndarray,
    s_base: np.ndarray,
    order: int,
) -> np.ndarray:
    """Fit polynomial trend of given order."""
    coeffs = np.polyfit(t_base, s_base, order)
    trend = np.polyval(coeffs, t_all)
    return trend


def _fit_exp(
    t_all: np.ndarray,
    t_base: np.ndarray,
    s_base: np.ndarray,
    poly_fallback_order: int,
) -> np.ndarray:
    """
    Fit a * exp(-k * t) + c to the baseline trace.

    Strategy:
    1. Try scipy.optimize.curve_fit if available.
    2. Fall back to log-linear fit (clipped) + constant offset.
    3. If all else fails, fall back to polynomial.
    """
    # Try scipy curve_fit first
    try:
        from scipy.optimize import curve_fit

        def exp_model(t, a, k, c):
            return a * np.exp(-k * t) + c

        # Initial guesses from data
        s0, s_end = s_base[0], s_base[-1]
        a0 = s0 - s_end if s0 > s_end else 1.0
        c0 = s_end
        k0 = 0.01

        popt, _ = curve_fit(
            exp_model,
            t_base,
            s_base,
            p0=[a0, k0, c0],
            maxfev=5000,
            bounds=([0, 0, 0], [np.inf, np.inf, np.inf]),
        )

        trend = exp_model(t_all, *popt)
        logger.debug(
            f"Exponential fit: a={popt[0]:.2f}, k={popt[1]:.6f}, c={popt[2]:.2f}"
        )
        return trend

    except Exception as e:
        logger.debug(f"scipy curve_fit failed ({e}), trying log-linear fallback")

    # Fallback: log-linear fit
    # For a*exp(-k*t)+c, if c ≈ min(trace), then log(s-c) ≈ log(a) - k*t
    try:
        c_est = max(s_base.min() * 0.9, 0)
        shifted = s_base - c_est
        # Clip to positive for log
        shifted = np.clip(shifted, 1e-10, None)
        log_s = np.log(shifted)

        # Linear fit: log(s-c) = log(a) - k*t
        coeffs = np.polyfit(t_base, log_s, 1)
        k_est = -coeffs[0]
        a_est = np.exp(coeffs[1])

        if k_est < 0:
            # Increasing signal; doesn't look like bleach, use polynomial
            raise ValueError("Negative k in log-linear fit")

        trend = a_est * np.exp(-k_est * t_all) + c_est
        logger.debug(
            f"Log-linear exp fit: a={a_est:.2f}, k={k_est:.6f}, c={c_est:.2f}"
        )
        return trend

    except Exception as e:
        logger.debug(
            f"Log-linear fallback failed ({e}), using polynomial fallback"
        )

    # Final fallback: polynomial
    return _fit_poly(t_all, t_base, s_base, poly_fallback_order)


def apply_bleach_correction_frame(
    frame: np.ndarray,
    trend_t: float,
    trend_0: float,
    settings: BleachCorrectionSettings,
) -> np.ndarray:
    """
    Apply bleach correction to a single frame.

    Args:
        frame: 2D grayscale frame (any float dtype).
        trend_t: Trend value at this frame's time index.
        trend_0: Trend value at t=0 (reference level).
        settings: Bleach correction settings.

    Returns:
        Corrected frame (float32).

    Raises:
        ValueError: If divide mode and trend is non-positive.
    """
    frame = np.nan_to_num(frame.astype(np.float32), nan=0.0, posinf=0.0, neginf=0.0)

    if settings.apply_mode == "divide":
        denom = trend_t + settings.epsilon
        if denom <= 0:
            raise ValueError(
                f"bleach_correction: non-positive trend value ({trend_t:.4f}) "
                f"with divide mode. Use subtract mode or check your data."
            )
        corrected = frame / denom * trend_0
    else:
        # subtract
        corrected = frame - (trend_t - trend_0)

    return corrected.astype(np.float32)


class BleachCorrector:
    """
    Stateful bleach corrector that pre-computes the trend once,
    then applies per-frame correction efficiently.
    """

    def __init__(
        self,
        trace: np.ndarray,
        settings: BleachCorrectionSettings,
    ):
        """
        Args:
            trace: Per-frame median intensity trace.
            settings: Bleach correction configuration.
        """
        self.settings = settings
        self.trace = trace
        self.trend = fit_bleach_trend(trace, settings)
        self.trend_0 = self.trend[0]
        self.n_frames = len(trace)

        # Validate trend for divide mode
        if settings.apply_mode == "divide":
            min_trend = self.trend.min()
            if min_trend + settings.epsilon <= 0:
                raise ValueError(
                    f"bleach_correction: trend has non-positive values "
                    f"(min={min_trend:.4f}) which is invalid for divide mode. "
                    f"Use apply_mode='subtract' or check your data."
                )

        trace_std_before = np.std(trace)
        corrected_trace = trace / (self.trend + settings.epsilon) * self.trend_0 \
            if settings.apply_mode == "divide" \
            else trace - (self.trend - self.trend_0)
        trace_std_after = np.std(corrected_trace)

        logger.info(
            f"Bleach correction fitted: method={settings.method}, "
            f"trace std {trace_std_before:.2f} -> {trace_std_after:.2f}"
        )

    def correct_frame(self, frame: np.ndarray, frame_idx: int) -> np.ndarray:
        """
        Apply correction to a single frame.

        Args:
            frame: 2D grayscale frame.
            frame_idx: Frame index (0-based).

        Returns:
            Corrected float32 frame.
        """
        # Clamp index if out of range (e.g. preview with sampled indices)
        idx = min(frame_idx, self.n_frames - 1)
        return apply_bleach_correction_frame(
            frame, self.trend[idx], self.trend_0, self.settings
        )


def precompute_bleach_corrector(
    settings: BleachCorrectionSettings,
    frames: Optional[list] = None,
    recording_iterator: Optional[object] = None,
    n_frames: Optional[int] = None,
) -> Optional[BleachCorrector]:
    """
    Build a BleachCorrector if enabled, or return None.

    Args:
        settings: Bleach correction configuration.
        frames: List of frames (preview mode).
        recording_iterator: RecordingIterator (pipeline mode).
        n_frames: Number of frames (pipeline mode).

    Returns:
        BleachCorrector instance or None if disabled.
    """
    if not settings.enabled or settings.method == "none":
        return None

    logger.info("Computing bleach correction intensity trace...")
    trace = compute_intensity_trace(
        frames=frames,
        recording_iterator=recording_iterator,
        n_frames=n_frames,
    )

    return BleachCorrector(trace, settings)


def format_preproc_banner(
    bleach_settings: BleachCorrectionSettings,
    denoise_settings: object,
    view_settings: object,
) -> str:
    """
    Format a one-line preprocessing configuration banner.

    Args:
        bleach_settings: BleachCorrectionSettings instance.
        denoise_settings: DenoiseSettings instance.
        view_settings: ViewSettings instance.

    Returns:
        Human-readable banner string.
    """
    # Bleach part
    if bleach_settings.enabled and bleach_settings.method != "none":
        bf = bleach_settings.baseline_frames
        bleach_str = (
            f"bleach(enabled=True, method={bleach_settings.method}, "
            f"baseline={bf}, mode={bleach_settings.apply_mode})"
        )
    else:
        bleach_str = "bleach(enabled=False)"

    # Denoise part
    if hasattr(denoise_settings, 'enabled') and denoise_settings.enabled:
        denoise_str = (
            f"denoise(method={denoise_settings.method}, "
            f"strength={denoise_settings.strength})"
        )
    else:
        denoise_str = "denoise(off)"

    # View part
    view_str = (
        f"view(p_lo={view_settings.p_lo}, p_hi={view_settings.p_hi}, "
        f"gamma={view_settings.gamma}, clahe={view_settings.clahe})"
    )

    return f"Preproc: {bleach_str} {denoise_str} {view_str}"
