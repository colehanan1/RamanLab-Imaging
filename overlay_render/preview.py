"""
Real-time preview GUI for tuning overlay parameters.

Usage:
    python -m overlay_render.preview --folder /path/to/experiment
    python -m overlay_render.preview --structure img.tif --recording dir/

Controls:
    - Trackbars for all parameters
    - 's' to save current settings to YAML
    - 'q' to quit
    - '[' / ']' to change frame
    - 'r' to toggle registration
    - 'c' to toggle CLAHE
    - 'm' to toggle overlay mode
"""

import argparse
import logging
import sys
from pathlib import Path
from typing import Optional, Tuple

import cv2
import numpy as np

from .denoise import apply_denoise
from .config import DenoiseSettings

logger = logging.getLogger(__name__)


class PreviewGUI:
    """Real-time preview GUI for tuning overlay parameters."""

    def __init__(
        self,
        structure: np.ndarray,
        recording_frames: list,
        window_name: str = "Overlay Preview - Press 'q' to quit, 's' to save settings",
        recording_only: bool = False,
        start_frame: int = None
    ):
        """
        Initialize preview GUI.

        Args:
            structure: Structure image (2D array). Can be None if recording_only.
            recording_frames: List of recording frames to preview.
            window_name: OpenCV window name.
            recording_only: If True, show only recording (grayscale, no overlay).
            start_frame: Initial frame index to display.
        """
        self.recording_only = recording_only
        self.recording_frames = [f.astype(np.float32) for f in recording_frames]

        if recording_only:
            self.structure = None
            self.window_name = "Recording Preview - Press 'q' to quit, 's' to save settings"
        else:
            self.structure = structure.astype(np.float32) if structure is not None else None
            self.window_name = window_name

        # Set initial frame
        if start_frame is not None and 0 <= start_frame < len(recording_frames):
            self.current_frame_idx = start_frame
        else:
            self.current_frame_idx = len(recording_frames) // 2

        # Parameters (will be controlled by trackbars)
        self.p_lo = 1
        self.p_hi = 99
        self.gamma = 10  # Stored as gamma * 10 for trackbar (0.1 to 3.0)
        self.alpha = 50  # Stored as alpha * 100 for trackbar (0 to 100)
        self.use_clahe = False
        self.use_registration = False
        self.overlay_mode = 0  # 0 = falsecolor, 1 = blend
        self.roi_center = 100  # 100 = full image, 50 = center 50%

        # Precompute structure stats (if not recording_only)
        if self.structure is not None:
            self.struct_min = float(np.percentile(self.structure, 1))
            self.struct_max = float(np.percentile(self.structure, 99))
        else:
            self.struct_min = 0
            self.struct_max = 1

        # Registration matrix (computed once if enabled)
        self._registration_matrix = None

        # Create window and trackbars
        self._setup_window()

    def _setup_window(self):
        """Create OpenCV window with trackbars."""
        cv2.namedWindow(self.window_name, cv2.WINDOW_NORMAL)

        # Resize window to reasonable size
        cv2.resizeWindow(self.window_name, 1200, 900)

        # Create trackbars - full 0-100 range for percentiles
        cv2.createTrackbar("p_lo (MIN)", self.window_name, self.p_lo, 100, self._on_trackbar)
        cv2.createTrackbar("p_hi (MAX)", self.window_name, self.p_hi, 100, self._on_trackbar)
        cv2.createTrackbar("gamma x10", self.window_name, self.gamma, 30, self._on_trackbar)

        # Only show overlay controls if not recording_only
        if not self.recording_only:
            cv2.createTrackbar("alpha %", self.window_name, self.alpha, 100, self._on_trackbar)
            cv2.createTrackbar("Registration", self.window_name, 0, 1, self._on_trackbar)
            cv2.createTrackbar("Mode (0=false,1=blend)", self.window_name, 0, 1, self._on_trackbar)

        cv2.createTrackbar("CLAHE", self.window_name, 0, 1, self._on_trackbar)
        cv2.createTrackbar("ROI center %", self.window_name, self.roi_center, 100, self._on_trackbar)
        cv2.createTrackbar("Frame", self.window_name, self.current_frame_idx,
                          len(self.recording_frames) - 1, self._on_trackbar)

    def _on_trackbar(self, val):
        """Trackbar callback (triggers redraw)."""
        pass  # Values read directly in render loop

    def _read_trackbars(self):
        """Read current trackbar values."""
        self.p_lo = cv2.getTrackbarPos("p_lo (MIN)", self.window_name)
        self.p_hi = cv2.getTrackbarPos("p_hi (MAX)", self.window_name)
        self.gamma = cv2.getTrackbarPos("gamma x10", self.window_name)
        self.use_clahe = cv2.getTrackbarPos("CLAHE", self.window_name) == 1
        self.roi_center = cv2.getTrackbarPos("ROI center %", self.window_name)
        self.current_frame_idx = cv2.getTrackbarPos("Frame", self.window_name)

        # Only read overlay controls if not recording_only
        if not self.recording_only:
            self.alpha = cv2.getTrackbarPos("alpha %", self.window_name)
            self.use_registration = cv2.getTrackbarPos("Registration", self.window_name) == 1
            self.overlay_mode = cv2.getTrackbarPos("Mode (0=false,1=blend)", self.window_name)

        # Only ensure p_lo < p_hi (allow full 0-100 range)
        if self.p_lo >= self.p_hi:
            self.p_lo = max(0, self.p_hi - 1)
        # Allow gamma down to 0.1 (trackbar value 1)
        self.gamma = max(1, self.gamma)
        # Allow ROI down to 1% (but not 0 to avoid division issues)
        self.roi_center = max(1, self.roi_center)

    def _scale_image(self, img: np.ndarray, p_lo: float, p_hi: float, roi_fraction: float = 1.0) -> np.ndarray:
        """Apply percentile scaling to image, optionally using center ROI for stats."""
        # Use center ROI for computing percentiles if roi_fraction < 1
        if roi_fraction < 1.0:
            h, w = img.shape[:2]
            margin_h = int(h * (1 - roi_fraction) / 2)
            margin_w = int(w * (1 - roi_fraction) / 2)
            roi = img[margin_h:h-margin_h, margin_w:w-margin_w]
        else:
            roi = img

        vmin = np.percentile(roi, p_lo)
        vmax = np.percentile(roi, p_hi)

        if vmax <= vmin:
            vmax = vmin + 1

        # Scale the FULL image using ROI-derived stats
        scaled = (img - vmin) / (vmax - vmin)
        scaled = np.clip(scaled, 0, 1)
        return scaled

    def _apply_gamma(self, img: np.ndarray, gamma: float) -> np.ndarray:
        """Apply gamma correction."""
        if gamma == 1.0:
            return img
        return np.power(img, gamma)

    def _apply_clahe(self, img: np.ndarray) -> np.ndarray:
        """Apply CLAHE contrast enhancement."""
        img_u8 = (img * 255).astype(np.uint8)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        enhanced = clahe.apply(img_u8)
        return enhanced.astype(np.float32) / 255.0

    def _compute_registration(self, frame: np.ndarray) -> Optional[np.ndarray]:
        """Compute registration matrix using ECC."""
        if self._registration_matrix is not None:
            return self._registration_matrix

        try:
            # Prepare images
            struct_8u = ((self.structure - self.struct_min) /
                        (self.struct_max - self.struct_min) * 255).astype(np.uint8)
            struct_8u = np.clip(struct_8u, 0, 255)

            frame_min = np.percentile(frame, 1)
            frame_max = np.percentile(frame, 99)
            frame_8u = ((frame - frame_min) / (frame_max - frame_min) * 255).astype(np.uint8)
            frame_8u = np.clip(frame_8u, 0, 255)

            # Downscale for speed
            scale = 0.25
            h, w = struct_8u.shape[:2]
            small_struct = cv2.resize(struct_8u, (int(w * scale), int(h * scale)))
            small_frame = cv2.resize(frame_8u, (int(w * scale), int(h * scale)))

            # ECC registration
            warp_matrix = np.eye(2, 3, dtype=np.float32)
            criteria = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 100, 1e-5)

            _, warp_matrix = cv2.findTransformECC(
                small_struct, small_frame, warp_matrix,
                cv2.MOTION_EUCLIDEAN, criteria
            )

            # Scale translation back
            warp_matrix[0, 2] /= scale
            warp_matrix[1, 2] /= scale

            self._registration_matrix = warp_matrix
            logger.info("Registration computed successfully")
            return warp_matrix

        except cv2.error as e:
            logger.warning(f"Registration failed: {e}")
            return None

    def _apply_registration(self, frame: np.ndarray) -> np.ndarray:
        """Apply registration to frame."""
        matrix = self._compute_registration(frame)
        if matrix is None:
            return frame

        h, w = self.structure.shape[:2]
        return cv2.warpAffine(frame, matrix, (w, h), flags=cv2.INTER_LINEAR)

    def _create_overlay(
        self,
        structure_scaled: np.ndarray,
        recording_scaled: np.ndarray,
        alpha: float,
        mode: int
    ) -> np.ndarray:
        """Create overlay composite."""
        if mode == 0:  # Falsecolor
            # Structure -> magenta (R+B), Recording -> green
            composite = np.zeros((*structure_scaled.shape, 3), dtype=np.float32)
            composite[:, :, 0] = structure_scaled * (1 - alpha)  # Red from structure
            composite[:, :, 1] = recording_scaled * alpha        # Green from recording
            composite[:, :, 2] = structure_scaled * (1 - alpha)  # Blue from structure
        else:  # Blend
            blended = structure_scaled * (1 - alpha) + recording_scaled * alpha
            composite = np.stack([blended, blended, blended], axis=-1)

        return composite

    def _add_info_text(self, img: np.ndarray) -> np.ndarray:
        """Add parameter info text to image."""
        img_display = (img * 255).astype(np.uint8)

        # Settings text
        gamma_val = self.gamma / 10.0
        roi_pct = self.roi_center

        if self.recording_only:
            lines = [
                "=== BRIGHTNESS/CONTRAST (like Fiji) ===",
                f"p_lo: {self.p_lo}   <- MIN: slide RIGHT to darken background",
                f"p_hi: {self.p_hi}  <- MAX: slide LEFT to brighten signal",
                f"gamma: {gamma_val:.1f}  <- midtone adjust (1.0 = off)",
                f"ROI: {roi_pct}%   <- area used for min/max calc (lower = center only)",
                f"CLAHE: {'ON' if self.use_clahe else 'OFF'}    <- local contrast enhancement",
                "",
                f"Frame: {self.current_frame_idx + 1}/{len(self.recording_frames)}  |  MODE: RECORDING ONLY",
                "",
                "Keys: 's'=SAVE  'q'=quit  '['/']=frame  'c'=clahe"
            ]
        else:
            alpha_val = self.alpha / 100.0
            mode_str = "falsecolor" if self.overlay_mode == 0 else "blend"
            lines = [
                "=== BRIGHTNESS/CONTRAST ===",
                f"p_lo: {self.p_lo}   <- MIN: slide RIGHT to darken background",
                f"p_hi: {self.p_hi}  <- MAX: slide LEFT to brighten signal",
                f"gamma: {gamma_val:.1f}  <- midtone adjust (1.0 = off)",
                f"ROI: {roi_pct}%   <- area for min/max calc",
                "",
                "=== OVERLAY ===",
                f"alpha: {alpha_val:.2f}  <- recording blend (1=full green, 0=full magenta)",
                f"mode: {mode_str}  <- falsecolor=pink+green, blend=grayscale",
                f"CLAHE: {'ON' if self.use_clahe else 'OFF'}  Reg: {'ON' if self.use_registration else 'OFF'}",
                "",
                f"Frame: {self.current_frame_idx + 1}/{len(self.recording_frames)}",
                "",
                "Keys: 's'=SAVE  'q'=quit  '['/']=frame  'r'=reg  'c'=clahe  'm'=mode"
            ]

        y = 30
        for line in lines:
            cv2.putText(img_display, line, (10, y),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
            cv2.putText(img_display, line, (10, y),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 1)
            y += 22

        return img_display

    def render(self) -> np.ndarray:
        """Render current preview frame."""
        self._read_trackbars()

        # Get current recording frame
        frame = self.recording_frames[self.current_frame_idx]

        # Apply registration if enabled (only for overlay mode)
        if not self.recording_only and self.use_registration:
            frame = self._apply_registration(frame)

        # Apply denoising (placeholder - denoise not yet in preview GUI controls)
        # When denoise controls are added to GUI, apply here before scaling
        # For now, denoise is disabled in preview (no trackbars yet)
        denoise_settings = DenoiseSettings(enabled=False)
        if denoise_settings.enabled:
            frame = apply_denoise(frame, denoise_settings)

        # Scale images
        gamma_val = self.gamma / 10.0
        alpha_val = self.alpha / 100.0
        roi_fraction = self.roi_center / 100.0

        frame_scaled = self._scale_image(frame, self.p_lo, self.p_hi, roi_fraction)

        # Apply gamma
        frame_scaled = self._apply_gamma(frame_scaled, gamma_val)

        # Apply CLAHE if enabled
        if self.use_clahe:
            frame_scaled = self._apply_clahe(frame_scaled)

        if self.recording_only:
            # Grayscale output - stack to 3 channels
            composite = np.stack([frame_scaled, frame_scaled, frame_scaled], axis=-1)
        else:
            # Overlay mode
            struct_scaled = self._scale_image(self.structure, self.p_lo, self.p_hi, roi_fraction)
            struct_scaled = self._apply_gamma(struct_scaled, gamma_val)
            if self.use_clahe:
                struct_scaled = self._apply_clahe(struct_scaled)
            composite = self._create_overlay(struct_scaled, frame_scaled, alpha_val, self.overlay_mode)

        # Add info text
        display = self._add_info_text(composite)

        return display

    def get_settings(self) -> dict:
        """Get current settings as config dict."""
        gamma_val = self.gamma / 10.0
        roi_fraction = self.roi_center / 100.0

        settings = {
            "view": {
                "method": "percentile",
                "p_lo": self.p_lo,
                "p_hi": self.p_hi,
                "gamma": gamma_val,
                "clahe": self.use_clahe,
            },
        }

        # Only include overlay/registration settings if not recording_only
        if not self.recording_only:
            alpha_val = self.alpha / 100.0
            mode_str = "falsecolor" if self.overlay_mode == 0 else "blend"
            settings["overlay"] = {
                "alpha": alpha_val,
                "mode": mode_str,
            }
            settings["registration"] = {
                "enabled": self.use_registration,
                "model": "euclidean",
            }

        # Only include roi_center_fraction if not using full image
        if roi_fraction < 1.0:
            settings["view"]["roi_center_fraction"] = roi_fraction

        # Include denoise settings only if non-default (placeholder for future GUI controls)
        # Currently denoise is not exposed in preview GUI, so always disabled
        # When denoise controls are added, include here if enabled
        
        return settings

    def save_settings(self, path: Path):
        """Save current settings to YAML file."""
        import yaml

        settings = self.get_settings()

        # Add comment header
        if self.recording_only:
            yaml_str = """# Recording-only render settings - tuned via preview GUI
# Use with: python -m overlay_render --folder <path> --settings this_file.yaml --recording-only

"""
        else:
            yaml_str = """# Overlay render settings - tuned via preview GUI
# Use with: python -m overlay_render --folder <path> --settings this_file.yaml

"""
        yaml_str += yaml.dump(settings, default_flow_style=False, sort_keys=False)

        path.write_text(yaml_str)
        logger.info(f"Settings saved to {path}")

    def run(self) -> dict:
        """Run the preview GUI. Returns final settings."""
        logger.info("Starting preview GUI...")
        logger.info("Controls: 's'=save, 'q'=quit, '['/']=frame, 'r'=reg, 'c'=clahe, 'm'=mode")

        while True:
            # Render current view
            display = self.render()

            # Show image
            cv2.imshow(self.window_name, display)

            # Handle keyboard
            key = cv2.waitKey(30) & 0xFF

            if key == ord('q'):
                break
            elif key == ord('s'):
                save_path = Path("tuned_settings.yaml")
                self.save_settings(save_path)
                print(f"\nSettings saved to: {save_path.absolute()}")
            elif key == ord('['):
                self.current_frame_idx = max(0, self.current_frame_idx - 1)
                cv2.setTrackbarPos("Frame", self.window_name, self.current_frame_idx)
            elif key == ord(']'):
                self.current_frame_idx = min(len(self.recording_frames) - 1,
                                             self.current_frame_idx + 1)
                cv2.setTrackbarPos("Frame", self.window_name, self.current_frame_idx)
            elif key == ord('r'):
                current = cv2.getTrackbarPos("Registration", self.window_name)
                cv2.setTrackbarPos("Registration", self.window_name, 1 - current)
            elif key == ord('c'):
                current = cv2.getTrackbarPos("CLAHE", self.window_name)
                cv2.setTrackbarPos("CLAHE", self.window_name, 1 - current)
            elif key == ord('m'):
                current = cv2.getTrackbarPos("Mode (0=false,1=blend)", self.window_name)
                cv2.setTrackbarPos("Mode (0=false,1=blend)", self.window_name, 1 - current)

        cv2.destroyAllWindows()
        return self.get_settings()


def load_preview_data(
    folder: Optional[Path] = None,
    structure_path: Optional[Path] = None,
    recording_path: Optional[Path] = None,
    n_frames: int = 10,
    trial_index: int = 0,
) -> Tuple[np.ndarray, list]:
    """
    Load data for preview.

    Args:
        folder: Experiment folder (auto-discovery mode).
        structure_path: Direct path to structure image.
        recording_path: Direct path to recording.
        n_frames: Number of frames to sample for preview.
        trial_index: Which trial to use (folder mode).

    Returns:
        Tuple of (structure, list of recording frames).
    """
    from .loaders import load_structure, load_recording

    if folder:
        from .discovery import discover_experiment

        experiment = discover_experiment(folder)
        structure = load_structure(experiment.structure_files[0])

        if trial_index >= len(experiment.trials):
            trial_index = 0
        trial = experiment.trials[trial_index]

        recording = load_recording(trial.images_dir)
    else:
        structure = load_structure(structure_path)
        recording = load_recording(recording_path)

    # Sample frames evenly across recording
    total_frames = recording.n_frames
    if total_frames <= n_frames:
        indices = list(range(total_frames))
    else:
        indices = np.linspace(0, total_frames - 1, n_frames, dtype=int).tolist()

    frames = [recording.get_frame(i) for i in indices]
    recording.close()

    return structure, frames


def main(argv=None):
    """Main entry point for preview CLI."""
    parser = argparse.ArgumentParser(
        description="Real-time preview GUI for tuning overlay parameters"
    )

    mode_group = parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument("--folder", "-f", type=Path,
                           help="Experiment folder (auto-discovery)")
    mode_group.add_argument("--structure", "-s", type=Path,
                           help="Structure image path")

    parser.add_argument("--recording", "-r", type=Path,
                       help="Recording path (required with --structure)")
    parser.add_argument("--trial", "-t", type=int, default=0,
                       help="Trial index to preview (folder mode)")
    parser.add_argument("--frames", "-n", type=int, default=15,
                       help="Number of frames to sample (default: 15)")
    parser.add_argument("--verbose", "-v", action="store_true")

    args = parser.parse_args(argv)

    # Setup logging
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s"
    )

    # Validate args
    if args.structure and not args.recording:
        parser.error("--recording is required when using --structure")

    try:
        # Load data
        print("Loading data for preview...")
        structure, frames = load_preview_data(
            folder=args.folder,
            structure_path=args.structure,
            recording_path=args.recording,
            n_frames=args.frames,
            trial_index=args.trial,
        )
        print(f"Loaded structure: {structure.shape}, {len(frames)} preview frames")

        # Run GUI
        gui = PreviewGUI(structure, frames)
        final_settings = gui.run()

        print("\nFinal settings:")
        import yaml
        print(yaml.dump(final_settings, default_flow_style=False))

        return 0

    except Exception as e:
        logger.exception(f"Error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
