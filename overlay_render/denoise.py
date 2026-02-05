"""
Denoising for calcium imaging recordings.

Provides classical edge-preserving denoising methods:
- Non-local means (NLM)
- Bilateral filter
- Noise2Void (N2V) deep learning denoising

Applied to recording frames only, before view scaling.
"""

import logging
from pathlib import Path
from typing import Optional

import numpy as np

from .config import DenoiseSettings

logger = logging.getLogger(__name__)

# Global cache for N2V model to avoid reloading per frame
_n2v_denoiser_cache = None


class N2VDenoiser:
    """
    Noise2Void deep learning denoiser.
    
    Loads trained N2V U-Net model and applies to frames.
    Caches model to avoid reloading per frame.
    """
    
    def __init__(self, model_path: str, device: str = "auto"):
        """
        Initialize N2V denoiser.
        
        Args:
            model_path: Path to model directory or checkpoint file.
            device: Device to use ("auto", "cpu", or "cuda").
            
        Raises:
            ImportError: If PyTorch not installed.
            FileNotFoundError: If model file not found.
            ValueError: If model checkpoint is invalid.
        """
        # Check PyTorch availability
        try:
            import torch
        except ImportError:
            raise ImportError(
                "PyTorch not installed. N2V denoising requires PyTorch.\n"
                "Install with: pip install torch torchvision\n"
                "Or: pip install -e .[n2v]"
            )
        
        # Resolve model path
        model_path = Path(model_path)
        if model_path.is_dir():
            checkpoint_path = model_path / "model_best.pth"
            if not checkpoint_path.exists():
                checkpoint_path = model_path / "model_latest.pth"
        else:
            checkpoint_path = model_path
        
        if not checkpoint_path.exists():
            raise FileNotFoundError(
                f"N2V model not found: {checkpoint_path}\n"
                f"Train a model first with: python -m overlay_render.train_n2v"
            )
        
        # Select device
        if device == "auto":
            self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        else:
            self.device = torch.device(device)
        
        logger.info(f"Loading N2V model from: {checkpoint_path}")
        logger.info(f"Using device: {self.device}")
        
        # Load checkpoint
        try:
            checkpoint = torch.load(checkpoint_path, map_location=self.device)
        except Exception as e:
            raise ValueError(f"Failed to load N2V checkpoint: {e}")
        
        # Extract config and normalization params
        if "config" not in checkpoint:
            raise ValueError("Checkpoint missing 'config' field")
        
        config = checkpoint["config"]
        self.normalize_lo = config.get("normalize_percentile_lo", 1.0)
        self.normalize_hi = config.get("normalize_percentile_hi", 99.5)
        
        # Create model architecture
        try:
            from .train_n2v import UNet
        except ImportError:
            raise ImportError("Failed to import UNet from train_n2v module")
        
        num_channels = config.get("num_channels", 1)
        self.model = UNet(in_channels=num_channels, out_channels=num_channels)
        
        # Load weights
        try:
            self.model.load_state_dict(checkpoint["model_state_dict"])
        except Exception as e:
            raise ValueError(f"Failed to load model weights: {e}")
        
        self.model.to(self.device)
        self.model.eval()
        
        logger.info(f"✓ N2V model loaded (epoch {checkpoint.get('epoch', '?')}, "
                   f"loss {checkpoint.get('loss', '?'):.6f})")
    
    def denoise(self, frame: np.ndarray) -> np.ndarray:
        """
        Apply N2V denoising to a single frame.
        
        Args:
            frame: 2D grayscale frame (float32/float64, any range).
            
        Returns:
            Denoised frame (same dtype and range as input).
        """
        import torch
        
        # Store input properties
        input_dtype = frame.dtype
        vmin, vmax = float(frame.min()), float(frame.max())
        
        # Normalize using training percentiles
        if vmax > vmin:
            # Use percentile normalization as in training
            p_lo = np.percentile(frame, self.normalize_lo)
            p_hi = np.percentile(frame, self.normalize_hi)
            
            if p_hi > p_lo:
                frame_normalized = (frame - p_lo) / (p_hi - p_lo)
                frame_normalized = np.clip(frame_normalized, 0, 1)
            else:
                frame_normalized = np.zeros_like(frame, dtype=np.float32)
        else:
            frame_normalized = np.zeros_like(frame, dtype=np.float32)
        
        # Convert to tensor [1, 1, H, W]
        frame_tensor = torch.from_numpy(frame_normalized.astype(np.float32))
        frame_tensor = frame_tensor.unsqueeze(0).unsqueeze(0).to(self.device)
        
        # Apply model
        with torch.no_grad():
            denoised_tensor = self.model(frame_tensor)
        
        # Convert back to numpy
        denoised = denoised_tensor.squeeze().cpu().numpy()
        
        # Restore original range
        if vmax > vmin:
            p_lo = np.percentile(frame, self.normalize_lo)
            p_hi = np.percentile(frame, self.normalize_hi)
            if p_hi > p_lo:
                denoised = denoised * (p_hi - p_lo) + p_lo
        
        # Clip to original range
        denoised = np.clip(denoised, vmin, vmax)
        
        return denoised.astype(input_dtype)


def _get_n2v_denoiser(settings: DenoiseSettings) -> N2VDenoiser:
    """
    Get or create cached N2V denoiser.
    
    Args:
        settings: Denoise settings with model_path and device.
        
    Returns:
        Cached N2V denoiser instance.
        
    Raises:
        ValueError: If model_path not specified.
    """
    global _n2v_denoiser_cache
    
    if settings.model_path is None:
        raise ValueError(
            "N2V denoising requires model_path to be specified.\n"
            "Set denoise.model_path in config or use --denoise.model_path on CLI."
        )
    
    # Check if we need to create or recreate the denoiser
    cache_key = (settings.model_path, settings.device)
    if _n2v_denoiser_cache is None or _n2v_denoiser_cache[0] != cache_key:
        logger.info("Creating N2V denoiser (will be cached for subsequent frames)")
        _n2v_denoiser_cache = (
            cache_key,
            N2VDenoiser(settings.model_path, settings.device)
        )
    
    return _n2v_denoiser_cache[1]


def apply_denoise(
    frame: np.ndarray,
    settings: DenoiseSettings
) -> np.ndarray:
    """
    Apply denoising to a recording frame.

    Args:
        frame: Input 2D grayscale frame (float32 or float64, any range).
        settings: Denoise configuration.

    Returns:
        Denoised frame (same dtype and range as input).

    Notes:
        - If disabled or method is "none", returns frame unchanged.
        - Handles NaN/inf by replacing with 0.
        - Preserves dtype and value range of input.
    """
    if not settings.enabled or settings.method == "none":
        return frame

    # Handle edge cases
    if frame.size == 0:
        return frame

    # Replace NaN/inf with 0
    if not np.isfinite(frame).all():
        logger.warning("Frame contains NaN or inf values, replacing with 0")
        frame = np.nan_to_num(frame, nan=0.0, posinf=0.0, neginf=0.0)

    # Handle constant frames (no variance)
    if frame.min() == frame.max():
        logger.debug("Frame is constant, skipping denoise")
        return frame

    # Apply denoising method
    if settings.method == "nlm":
        # Store input dtype and range for restoration
        input_dtype = frame.dtype
        vmin, vmax = float(frame.min()), float(frame.max())
        
        # Normalize to [0, 1] float32 for processing
        if vmax > vmin:
            frame_normalized = ((frame - vmin) / (vmax - vmin)).astype(np.float32)
        else:
            frame_normalized = frame.astype(np.float32)
        
        denoised = _denoise_nlm(frame_normalized, settings.strength)
        
        # Restore original range and dtype
        if vmax > vmin:
            denoised = denoised * (vmax - vmin) + vmin
        denoised = np.clip(denoised, vmin, vmax)
        return denoised.astype(input_dtype)
        
    elif settings.method == "bilateral":
        # Store input dtype and range for restoration
        input_dtype = frame.dtype
        vmin, vmax = float(frame.min()), float(frame.max())
        
        # Normalize to [0, 1] float32 for processing
        if vmax > vmin:
            frame_normalized = ((frame - vmin) / (vmax - vmin)).astype(np.float32)
        else:
            frame_normalized = frame.astype(np.float32)
        
        denoised = _denoise_bilateral(frame_normalized, settings.strength)
        
        # Restore original range and dtype
        if vmax > vmin:
            denoised = denoised * (vmax - vmin) + vmin
        denoised = np.clip(denoised, vmin, vmax)
        return denoised.astype(input_dtype)
        
    elif settings.method == "n2v":
        # N2V uses its own normalization internally
        denoiser = _get_n2v_denoiser(settings)
        return denoiser.denoise(frame)
        
    else:
        logger.warning(f"Unknown denoise method: {settings.method}, returning unchanged")
        return frame


def _denoise_nlm(
    frame: np.ndarray,
    strength: float
) -> np.ndarray:
    """
    Apply Non-Local Means denoising.

    Args:
        frame: Normalized float32 frame in [0, 1].
        strength: Denoise strength parameter (0-100).
            Maps to OpenCV 'h' parameter via: h = strength / 10.0
            Typical values: 3-10 for light denoising, 10-20 for strong.

    Returns:
        Denoised float32 frame in [0, 1].

    Notes:
        - Converts to uint8 for OpenCV, then back to float32.
        - Uses fast version with fixed template/search window sizes.
        - h controls filter strength (higher = more smoothing).
    """
    try:
        import cv2
    except ImportError:
        logger.error("OpenCV required for NLM denoising")
        return frame

    if strength <= 0:
        return frame

    # Convert to uint8 for OpenCV NLM
    frame_u8 = (frame * 255).astype(np.uint8)

    # Map strength to h parameter
    # strength range: 0-100, typical h range: 0-20
    h = strength / 5.0  # strength=10 -> h=2, strength=50 -> h=10

    # Fast NLM with fixed windows (templateWindowSize=7, searchWindowSize=21)
    # Smaller windows for speed in preview
    denoised_u8 = cv2.fastNlMeansDenoising(
        frame_u8,
        None,
        h=h,
        templateWindowSize=7,
        searchWindowSize=21
    )

    # Convert back to float32 [0, 1]
    return denoised_u8.astype(np.float32) / 255.0


def _denoise_bilateral(
    frame: np.ndarray,
    strength: float
) -> np.ndarray:
    """
    Apply bilateral filtering.

    Args:
        frame: Normalized float32 frame in [0, 1].
        strength: Denoise strength parameter (0-100).
            Maps to sigmaColor and sigmaSpace.
            Typical values: 10-30 for light, 30-80 for strong.

    Returns:
        Denoised float32 frame in [0, 1].

    Notes:
        - Bilateral filter preserves edges while smoothing flat regions.
        - sigmaColor controls intensity similarity (range domain).
        - sigmaSpace controls spatial proximity (spatial domain).
        - Both set equal to strength for balanced denoising.
    """
    try:
        import cv2
    except ImportError:
        logger.error("OpenCV required for bilateral filtering")
        return frame

    if strength <= 0:
        return frame

    # Convert to uint8 for OpenCV bilateral filter
    frame_u8 = (frame * 255).astype(np.uint8)

    # Map strength to sigma parameters
    # strength range: 0-100, typical sigma range: 10-100
    # Use smaller diameter for speed in preview
    d = 5  # Neighborhood diameter (fixed for speed)
    sigma_color = strength  # Intensity similarity
    sigma_space = strength  # Spatial proximity

    # Apply bilateral filter
    denoised_u8 = cv2.bilateralFilter(
        frame_u8,
        d=d,
        sigmaColor=sigma_color,
        sigmaSpace=sigma_space
    )

    # Convert back to float32 [0, 1]
    return denoised_u8.astype(np.float32) / 255.0
