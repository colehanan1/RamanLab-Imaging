"""
Noise2Void Training Utility for Fluorescence Microscopy

Self-supervised denoising model training without requiring clean/paired data.
Uses blind-spot masking strategy from Krull et al., CVPR 2019.

Usage:
    python -m overlay_render.train_n2v \\
        --input recording.tif \\
        --output_model_dir models/n2v_trial1 \\
        --epochs 50 \\
        --device cuda

Outputs:
    - model_best.pth: Best model checkpoint
    - config.json: Training configuration
    - training_log.txt: Loss history
"""

import argparse
import json
import logging
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


def check_torch_available():
    """Check if PyTorch is installed."""
    try:
        import torch
        return True
    except ImportError:
        print("\nERROR: PyTorch not installed.")
        print("Install with: pip install -e .[n2v]")
        print("Or manually: pip install torch>=2.0.0 torchvision>=0.15.0 tqdm>=4.60.0")
        return False


if not check_torch_available():
    sys.exit(1)

import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import Dataset, DataLoader
from tqdm import tqdm


@dataclass
class TrainingConfig:
    """Configuration for N2V training."""
    input_path: str
    output_model_dir: str
    patch_size: int = 64
    epochs: int = 50
    batch_size: int = 16
    steps_per_epoch: int = 200
    learning_rate: float = 0.0004
    device: str = "auto"
    seed: int = 42
    num_channels: int = 1  # Grayscale
    normalize_percentile_lo: float = 1.0
    normalize_percentile_hi: float = 99.5


class UNet(nn.Module):
    """
    Simple U-Net architecture for Noise2Void.
    
    Architecture:
        - Encoder: 4 blocks, each with 2 conv + maxpool
        - Bottleneck: 2 conv
        - Decoder: 4 blocks, each with upconv + 2 conv + skip connection
        - Output: 1x1 conv to single channel
    """
    
    def __init__(self, in_channels=1, out_channels=1):
        super(UNet, self).__init__()
        
        # Encoder
        self.enc1 = self._double_conv(in_channels, 48)
        self.pool1 = nn.MaxPool2d(2)
        
        self.enc2 = self._double_conv(48, 48)
        self.pool2 = nn.MaxPool2d(2)
        
        self.enc3 = self._double_conv(48, 48)
        self.pool3 = nn.MaxPool2d(2)
        
        self.enc4 = self._double_conv(48, 48)
        self.pool4 = nn.MaxPool2d(2)
        
        # Bottleneck
        self.bottleneck = self._double_conv(48, 48)
        
        # Decoder
        self.upconv4 = nn.ConvTranspose2d(48, 48, kernel_size=2, stride=2)
        self.dec4 = self._double_conv(96, 48)  # 96 = 48 + 48 from skip
        
        self.upconv3 = nn.ConvTranspose2d(48, 48, kernel_size=2, stride=2)
        self.dec3 = self._double_conv(96, 48)
        
        self.upconv2 = nn.ConvTranspose2d(48, 48, kernel_size=2, stride=2)
        self.dec2 = self._double_conv(96, 48)
        
        self.upconv1 = nn.ConvTranspose2d(48, 48, kernel_size=2, stride=2)
        self.dec1 = self._double_conv(96, 48)
        
        # Output
        self.out = nn.Conv2d(48, out_channels, kernel_size=1)
    
    def _double_conv(self, in_ch, out_ch):
        """Double convolution block: Conv-ReLU-Conv-ReLU."""
        return nn.Sequential(
            nn.Conv2d(in_ch, out_ch, kernel_size=3, padding=1),
            nn.ReLU(inplace=True),
            nn.Conv2d(out_ch, out_ch, kernel_size=3, padding=1),
            nn.ReLU(inplace=True)
        )
    
    def forward(self, x):
        # Encoder
        enc1 = self.enc1(x)
        enc2 = self.enc2(self.pool1(enc1))
        enc3 = self.enc3(self.pool2(enc2))
        enc4 = self.enc4(self.pool3(enc3))
        
        # Bottleneck
        bottleneck = self.bottleneck(self.pool4(enc4))
        
        # Decoder with skip connections
        dec4 = self.dec4(torch.cat([self.upconv4(bottleneck), enc4], dim=1))
        dec3 = self.dec3(torch.cat([self.upconv3(dec4), enc3], dim=1))
        dec2 = self.dec2(torch.cat([self.upconv2(dec3), enc2], dim=1))
        dec1 = self.dec1(torch.cat([self.upconv1(dec2), enc1], dim=1))
        
        # Output
        return self.out(dec1)


class N2VDataset(Dataset):
    """
    Noise2Void dataset with blind-spot masking.
    
    Applies N2V masking strategy: randomly mask pixels and predict them
    from surrounding context (excluding the pixel itself - blind spot).
    """
    
    def __init__(
        self,
        data: np.ndarray,
        patch_size: int = 64,
        num_patches: int = 200,
        mask_fraction: float = 0.006,  # ~0.6% of pixels masked per patch
        seed: int = 42
    ):
        """
        Args:
            data: 3D array (T, H, W) or 2D array (H, W) of normalized float32 data.
            patch_size: Size of patches to extract.
            num_patches: Number of random patches per epoch.
            mask_fraction: Fraction of pixels to mask in each patch.
            seed: Random seed.
        """
        self.data = data
        if data.ndim == 2:
            self.data = data[np.newaxis, ...]  # Add T dimension
        
        self.patch_size = patch_size
        self.num_patches = num_patches
        self.mask_fraction = mask_fraction
        self.rng = np.random.RandomState(seed)
        
        logger.info(f"N2V Dataset: {self.data.shape[0]} frames, "
                   f"{num_patches} patches/epoch, patch_size={patch_size}")
    
    def __len__(self):
        return self.num_patches
    
    def __getitem__(self, idx):
        """
        Extract random patch and apply N2V masking.
        
        Returns:
            input: Patch with masked pixels replaced by random neighbors.
            target: Original patch values (at masked positions).
            mask: Binary mask indicating masked positions.
        """
        # Random frame (if 3D stack)
        t = self.rng.randint(0, self.data.shape[0])
        frame = self.data[t]
        
        # Random patch location
        h, w = frame.shape
        y = self.rng.randint(0, h - self.patch_size + 1)
        x = self.rng.randint(0, w - self.patch_size + 1)
        patch = frame[y:y+self.patch_size, x:x+self.patch_size].copy()
        
        # Apply N2V masking
        input_patch, target, mask = self._apply_n2v_mask(patch)
        
        # Convert to torch tensors with channel dimension
        input_patch = torch.from_numpy(input_patch[np.newaxis, ...]).float()
        target = torch.from_numpy(target[np.newaxis, ...]).float()
        mask = torch.from_numpy(mask[np.newaxis, ...]).float()
        
        return input_patch, target, mask
    
    def _apply_n2v_mask(self, patch: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        Apply N2V blind-spot masking to patch.
        
        Strategy:
        1. Randomly select ~0.6% of pixels to mask
        2. For each masked pixel, replace with random neighbor value
           (excluding center pixel - blind spot)
        3. Network learns to predict original value from neighbors
        
        Args:
            patch: 2D patch (H, W).
            
        Returns:
            input_patch: Patch with masked values replaced.
            target: Original values at masked positions (zeros elsewhere).
            mask: Binary mask (1 at masked positions).
        """
        h, w = patch.shape
        n_pixels = h * w
        n_masked = int(n_pixels * self.mask_fraction)
        
        # Random pixel positions to mask
        mask_indices = self.rng.choice(n_pixels, size=n_masked, replace=False)
        mask_y, mask_x = np.unravel_index(mask_indices, (h, w))
        
        # Create mask and target
        mask = np.zeros((h, w), dtype=np.float32)
        mask[mask_y, mask_x] = 1.0
        
        target = np.zeros((h, w), dtype=np.float32)
        target[mask_y, mask_x] = patch[mask_y, mask_x]
        
        # Replace masked pixels with random neighbor values (blind-spot)
        input_patch = patch.copy()
        for y, x in zip(mask_y, mask_x):
            # Get neighbors (8-connected, excluding center)
            y_min, y_max = max(0, y-1), min(h, y+2)
            x_min, x_max = max(0, x-1), min(w, x+2)
            neighbors = patch[y_min:y_max, x_min:x_max].ravel()
            
            # Exclude center pixel if it's in the neighborhood
            center_idx = ((y - y_min) * (x_max - x_min) + (x - x_min))
            if center_idx < len(neighbors):
                neighbors = np.delete(neighbors, center_idx)
            
            # Replace with random neighbor
            if len(neighbors) > 0:
                input_patch[y, x] = self.rng.choice(neighbors)
        
        return input_patch, target, mask


def load_data(input_path: Path) -> np.ndarray:
    """
    Load TIFF stack or recording from path.
    
    Args:
        input_path: Path to TIFF file or folder.
        
    Returns:
        3D numpy array (T, H, W) or 2D array (H, W).
    """
    logger.info(f"Loading data from: {input_path}")
    
    if input_path.is_file():
        # Single TIFF file
        try:
            import tifffile
            data = tifffile.imread(str(input_path))
            logger.info(f"Loaded TIFF: shape={data.shape}, dtype={data.dtype}")
        except Exception as e:
            raise ValueError(f"Failed to load TIFF file: {e}")
    else:
        # Use overlay_render's recording loader
        try:
            from .loaders import load_recording
            with load_recording(input_path) as rec:
                # Load all frames
                frames = [rec.get_frame(i) for i in range(rec.n_frames)]
                data = np.stack(frames, axis=0)
                logger.info(f"Loaded recording: {rec.n_frames} frames, shape={data.shape}")
        except Exception as e:
            raise ValueError(f"Failed to load recording: {e}")
    
    # Ensure 2D or 3D
    if data.ndim == 2:
        logger.info("Single frame detected")
    elif data.ndim == 3:
        logger.info(f"Stack: {data.shape[0]} frames")
    else:
        raise ValueError(f"Expected 2D or 3D data, got shape {data.shape}")
    
    return data


def normalize_data(data: np.ndarray, p_lo: float = 1.0, p_hi: float = 99.5) -> np.ndarray:
    """
    Normalize data to [0, 1] using percentile scaling.
    
    Args:
        data: Input array.
        p_lo: Lower percentile for normalization.
        p_hi: Upper percentile for normalization.
        
    Returns:
        Normalized float32 array in [0, 1].
    """
    logger.info(f"Normalizing data (p{p_lo}-p{p_hi})")
    
    vmin = np.percentile(data, p_lo)
    vmax = np.percentile(data, p_hi)
    
    if vmax <= vmin:
        logger.warning(f"vmax <= vmin ({vmax} <= {vmin}), using [min, max]")
        vmin, vmax = data.min(), data.max()
    
    logger.info(f"  Range: [{vmin:.2f}, {vmax:.2f}] -> [0, 1]")
    
    normalized = (data.astype(np.float32) - vmin) / (vmax - vmin)
    normalized = np.clip(normalized, 0, 1)
    
    return normalized


def train_epoch(
    model: nn.Module,
    dataloader: DataLoader,
    optimizer: optim.Optimizer,
    device: torch.device,
    epoch: int
) -> float:
    """
    Train for one epoch.
    
    Args:
        model: U-Net model.
        dataloader: Training data loader.
        optimizer: Optimizer.
        device: Device (cpu/cuda).
        epoch: Current epoch number.
        
    Returns:
        Average loss for epoch.
    """
    model.train()
    total_loss = 0.0
    
    pbar = tqdm(dataloader, desc=f"Epoch {epoch+1}", ncols=100)
    for batch_idx, (input_batch, target, mask) in enumerate(pbar):
        input_batch = input_batch.to(device)
        target = target.to(device)
        mask = mask.to(device)
        
        # Forward pass
        optimizer.zero_grad()
        output = model(input_batch)
        
        # MSE loss only on masked pixels
        loss = torch.sum((output - target) ** 2 * mask) / torch.sum(mask)
        
        # Backward pass
        loss.backward()
        optimizer.step()
        
        # Logging
        total_loss += loss.item()
        pbar.set_postfix({"loss": f"{loss.item():.6f}"})
    
    return total_loss / len(dataloader)


def save_checkpoint(
    model: nn.Module,
    config: TrainingConfig,
    output_dir: Path,
    epoch: int,
    loss: float,
    is_best: bool = False
):
    """Save model checkpoint and config."""
    output_dir.mkdir(parents=True, exist_ok=True)
    
    checkpoint = {
        "model_state_dict": model.state_dict(),
        "epoch": epoch,
        "loss": loss,
        "config": asdict(config)
    }
    
    # Save latest
    torch.save(checkpoint, output_dir / "model_latest.pth")
    
    # Save best
    if is_best:
        torch.save(checkpoint, output_dir / "model_best.pth")
        logger.info(f"✓ Saved best model (loss={loss:.6f})")


def main(argv: Optional[List[str]] = None):
    """Main training entry point."""
    parser = argparse.ArgumentParser(
        prog="overlay_render.train_n2v",
        description="Train Noise2Void denoising model for fluorescence microscopy",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    
    parser.add_argument(
        "--input", "-i",
        type=Path,
        required=True,
        help="Input TIFF stack or recording folder"
    )
    parser.add_argument(
        "--output_model_dir", "-o",
        type=Path,
        required=True,
        help="Output directory for model and logs"
    )
    parser.add_argument(
        "--patch_size",
        type=int,
        default=64,
        help="Training patch size (must be divisible by 16)"
    )
    parser.add_argument(
        "--epochs",
        type=int,
        default=50,
        help="Number of training epochs"
    )
    parser.add_argument(
        "--batch_size",
        type=int,
        default=16,
        help="Batch size for training"
    )
    parser.add_argument(
        "--steps_per_epoch",
        type=int,
        default=200,
        help="Number of batches per epoch"
    )
    parser.add_argument(
        "--learning_rate",
        type=float,
        default=0.0004,
        help="Learning rate"
    )
    parser.add_argument(
        "--device",
        type=str,
        default="auto",
        choices=["auto", "cpu", "cuda"],
        help="Device to use for training"
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for reproducibility"
    )
    
    args = parser.parse_args(argv)
    
    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(args.output_model_dir / "training_log.txt")
        ]
    )
    
    # Determine device
    if args.device == "auto":
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    else:
        device = torch.device(args.device)
    
    logger.info("=" * 60)
    logger.info("NOISE2VOID TRAINING")
    logger.info("=" * 60)
    logger.info(f"Device: {device}")
    if device.type == "cuda":
        logger.info(f"GPU: {torch.cuda.get_device_name(0)}")
    logger.info(f"Input: {args.input}")
    logger.info(f"Output: {args.output_model_dir}")
    
    # Create config
    config = TrainingConfig(
        input_path=str(args.input),
        output_model_dir=str(args.output_model_dir),
        patch_size=args.patch_size,
        epochs=args.epochs,
        batch_size=args.batch_size,
        steps_per_epoch=args.steps_per_epoch,
        learning_rate=args.learning_rate,
        device=str(device),
        seed=args.seed
    )
    
    # Set random seeds
    torch.manual_seed(config.seed)
    np.random.seed(config.seed)
    
    # Load and normalize data
    logger.info("\n" + "=" * 60)
    logger.info("DATA PREPARATION")
    logger.info("=" * 60)
    data = load_data(args.input)
    data_normalized = normalize_data(
        data,
        config.normalize_percentile_lo,
        config.normalize_percentile_hi
    )
    
    # Create dataset and dataloader
    dataset = N2VDataset(
        data_normalized,
        patch_size=config.patch_size,
        num_patches=config.steps_per_epoch * config.batch_size,
        seed=config.seed
    )
    dataloader = DataLoader(
        dataset,
        batch_size=config.batch_size,
        shuffle=True,
        num_workers=0  # Single-threaded for reproducibility
    )
    
    # Create model
    logger.info("\n" + "=" * 60)
    logger.info("MODEL")
    logger.info("=" * 60)
    model = UNet(in_channels=1, out_channels=1).to(device)
    n_params = sum(p.numel() for p in model.parameters())
    logger.info(f"U-Net parameters: {n_params:,}")
    
    # Optimizer
    optimizer = optim.Adam(model.parameters(), lr=config.learning_rate)
    
    # Training loop
    logger.info("\n" + "=" * 60)
    logger.info("TRAINING")
    logger.info("=" * 60)
    
    best_loss = float('inf')
    start_time = time.time()
    
    for epoch in range(config.epochs):
        epoch_loss = train_epoch(model, dataloader, optimizer, device, epoch)
        
        logger.info(f"Epoch {epoch+1}/{config.epochs} - Loss: {epoch_loss:.6f}")
        
        # Save checkpoint
        is_best = epoch_loss < best_loss
        if is_best:
            best_loss = epoch_loss
        
        save_checkpoint(model, config, args.output_model_dir, epoch, epoch_loss, is_best)
    
    # Final summary
    elapsed = time.time() - start_time
    logger.info("\n" + "=" * 60)
    logger.info("TRAINING COMPLETE")
    logger.info("=" * 60)
    logger.info(f"Total time: {elapsed/60:.1f} minutes")
    logger.info(f"Best loss: {best_loss:.6f}")
    logger.info(f"Model saved to: {args.output_model_dir / 'model_best.pth'}")
    
    # Save config as JSON
    config_path = args.output_model_dir / "config.json"
    with open(config_path, "w") as f:
        json.dump(asdict(config), f, indent=2)
    logger.info(f"Config saved to: {config_path}")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
