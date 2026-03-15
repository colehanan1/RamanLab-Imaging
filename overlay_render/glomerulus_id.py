"""
Glomerulus identification and ROI management for antennal lobe calcium imaging.

Workflow:
1. generate_mean_image() - compute mean/std projection from structural TIFs
2. launch_roi_editor() - open Napari GUI for drawing + labeling glomerulus ROIs
3. save_rois() / load_rois() - persist ROI masks as JSON
4. overlay_labels_on_frame() - draw glomerulus name labels on a video frame
5. extract_dff_traces() - compute ΔF/F per glomerulus ROI from trial frames
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

import numpy as np


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------

class HeadlessError(RuntimeError):
    """Raised when a GUI operation is attempted without a display."""
    pass


# ---------------------------------------------------------------------------
# Color palette for glomeruli
# ---------------------------------------------------------------------------

# Named colors for well-known glomeruli (BGR for OpenCV)
GLOMERULUS_COLORS: dict[str, tuple[int, int, int]] = {
    "DA1":  (0,   0,   255),   # red
    "DM2":  (255, 0,   0),     # blue
    "DM4":  (0,   200, 0),     # green
    "VA1":  (0,   140, 255),   # orange
    "DC3":  (255, 0,   255),   # magenta
    "VA2":  (0,   255, 255),   # yellow
    "DL5":  (255, 128, 0),     # cyan-ish
    "VM2":  (128, 0,   255),   # purple
}

# Fallback cycling palette (BGR)
_CYCLE_PALETTE = [
    (255, 100, 100),
    (100, 255, 100),
    (100, 100, 255),
    (255, 255, 100),
    (255, 100, 255),
    (100, 255, 255),
    (200, 150, 50),
    (50,  200, 150),
    (150, 50,  200),
    (200, 200, 100),
]


def _get_color(name: str, index: int) -> tuple[int, int, int]:
    """Return a BGR color for a glomerulus name."""
    if name in GLOMERULUS_COLORS:
        return GLOMERULUS_COLORS[name]
    return _CYCLE_PALETTE[index % len(_CYCLE_PALETTE)]


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

def generate_mean_image(structure_dir: Path) -> np.ndarray:
    """Compute mean projection from structural TIF files.

    Loads all ``structure_*.tif`` files in *structure_dir* and returns their
    mean as a float32 array normalized to [0, 1].

    Parameters
    ----------
    structure_dir:
        Directory containing structural reference TIF files.

    Returns
    -------
    np.ndarray
        float32 mean projection, shape (H, W), normalized to [0, 1].
    """
    try:
        import tifffile
    except ImportError:
        raise ImportError(
            "tifffile is required for generate_mean_image(). "
            "Install it with: pip install tifffile"
        )

    structure_dir = Path(structure_dir)
    tif_files = sorted(structure_dir.glob("structure_*.tif"))

    if not tif_files:
        raise FileNotFoundError(
            f"No structure_*.tif files found in {structure_dir}"
        )

    stack = []
    for f in tif_files:
        img = tifffile.imread(str(f)).astype(np.float32)
        stack.append(img)

    mean_img = np.mean(stack, axis=0).astype(np.float32)

    # Normalize to [0, 1]
    min_val = mean_img.min()
    max_val = mean_img.max()
    if max_val > min_val:
        mean_img = (mean_img - min_val) / (max_val - min_val)
    else:
        mean_img = np.zeros_like(mean_img)

    return mean_img


def launch_roi_editor(
    mean_image: np.ndarray,
    existing_rois: Optional[dict] = None,
) -> dict:
    """Open a Napari GUI for drawing and labeling glomerulus ROIs.

    Opens a Napari viewer showing *mean_image* as background.  The user draws
    polygon ROIs around each glomerulus.  After closing the window, the user
    names each ROI via ``input()``.

    Parameters
    ----------
    mean_image:
        2-D float32 array (normalized 0–1) used as background reference.
    existing_rois:
        Optional dict of pre-existing ROIs (as returned by :func:`load_rois`).
        Polygons will be loaded into the Shapes layer for editing.

    Returns
    -------
    dict
        ``{"DA1": {"polygon": [[y, x], ...], "mask": None}, ...}``

    Raises
    ------
    HeadlessError
        If no display is available (``DISPLAY`` not set on Linux).
    ImportError
        If napari is not installed.
    """
    # Check for display
    if os.name != "nt" and not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        raise HeadlessError(
            "No display detected (DISPLAY/WAYLAND_DISPLAY not set). "
            "launch_roi_editor() requires a graphical display. "
            "Either:\n"
            "  1. Run on a machine with a display, or\n"
            "  2. Use X forwarding: ssh -X user@host\n"
            "  3. Manually construct ROI JSON and use load_rois() instead."
        )

    try:
        import napari
    except ImportError:
        raise ImportError(
            "napari is required for launch_roi_editor(). "
            "Install it with: pip install 'napari[all]'"
        )

    print("Draw polygon ROIs around each glomerulus. Double-click to close polygon. When done, close the window.")

    viewer = napari.Viewer(title="Glomerulus ROI Editor")
    viewer.add_image(mean_image, name="structural_reference", colormap="gray")

    # Pre-load existing ROIs as shapes
    initial_shapes = []
    if existing_rois:
        for name, roi_data in existing_rois.items():
            poly = roi_data.get("polygon")
            if poly is not None and len(poly) > 0:
                initial_shapes.append(np.array(poly))

    shapes_layer = viewer.add_shapes(
        initial_shapes if initial_shapes else None,
        shape_type="polygon",
        edge_color="yellow",
        face_color="transparent",
        name="glomerulus_rois",
    )

    napari.run()

    # Collect shapes after window closes
    shapes_data = shapes_layer.data
    shape_types = shapes_layer.shape_type

    if not shapes_data:
        print("No ROIs drawn.")
        return {}

    rois: dict = {}
    for i, shape in enumerate(shapes_data):
        print(f"\nROI #{i+1} — polygon with {len(shape)} vertices")
        while True:
            name = input(f"  Enter glomerulus name for ROI #{i+1} (e.g. DA1): ").strip()
            if name:
                break
            print("  Name cannot be empty.")
        rois[name] = {
            "polygon": shape.tolist(),
            "mask": None,
        }

    return rois


def save_rois(rois: dict, output_path: Path) -> None:
    """Save glomerulus ROIs to a JSON file.

    Only polygons are saved (masks are numpy arrays and not JSON-serializable).

    Parameters
    ----------
    rois:
        Dict of ``{name: {"polygon": [[y, x], ...], "mask": ...}, ...}``.
    output_path:
        Path to write the JSON file.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    serializable: dict = {}
    for name, roi_data in rois.items():
        poly = roi_data.get("polygon")
        if poly is None:
            serializable[name] = {"polygon": None}
        elif isinstance(poly, np.ndarray):
            serializable[name] = {"polygon": poly.tolist()}
        else:
            serializable[name] = {"polygon": list(poly)}

    with open(output_path, "w") as f:
        json.dump(serializable, f, indent=2)

    print(f"Saved {len(serializable)} ROIs to {output_path}")


def load_rois(roi_path: Path, image_shape: tuple) -> dict:
    """Load glomerulus ROIs from a JSON file and rasterize to masks.

    Parameters
    ----------
    roi_path:
        Path to the JSON file saved by :func:`save_rois`.
    image_shape:
        ``(height, width)`` of the image space for mask rasterization.

    Returns
    -------
    dict
        ``{"DA1": {"polygon": [[y, x], ...], "mask": np.ndarray bool}, ...}``
    """
    roi_path = Path(roi_path)
    with open(roi_path, "r") as f:
        data = json.load(f)

    rois: dict = {}
    for name, roi_data in data.items():
        poly = roi_data.get("polygon")
        rois[name] = {
            "polygon": poly,
            "mask": None,
        }

    # Rasterize all polygons to masks
    rois = rasterize_rois(rois, image_shape)
    return rois


def rasterize_rois(rois: dict, image_shape: tuple) -> dict:
    """Convert polygon coordinates to boolean masks for all ROIs.

    Parameters
    ----------
    rois:
        Dict of ``{name: {"polygon": [[y, x], ...], ...}, ...}``.
    image_shape:
        ``(height, width)`` of the target image.

    Returns
    -------
    dict
        Updated rois dict with ``"mask"`` field filled in as boolean np.ndarray.
    """
    try:
        from skimage.draw import polygon as skimage_polygon
    except ImportError:
        raise ImportError(
            "scikit-image is required for rasterize_rois(). "
            "Install it with: pip install scikit-image"
        )

    H, W = image_shape[:2]
    updated = {}
    for name, roi_data in rois.items():
        poly = roi_data.get("polygon")
        updated[name] = dict(roi_data)  # shallow copy

        if poly is None or len(poly) < 3:
            updated[name]["mask"] = np.zeros((H, W), dtype=bool)
            continue

        poly_arr = np.array(poly)
        # polygon coords are [y, x] (row, col)
        rows = poly_arr[:, 0]
        cols = poly_arr[:, 1]

        rr, cc = skimage_polygon(rows, cols, shape=(H, W))
        mask = np.zeros((H, W), dtype=bool)
        mask[rr, cc] = True
        updated[name]["mask"] = mask

    return updated


def overlay_labels_on_frame(
    frame: np.ndarray,
    rois: dict,
    alpha: float = 0.3,
) -> np.ndarray:
    """Draw glomerulus ROI outlines and name labels on a frame.

    Parameters
    ----------
    frame:
        2-D grayscale array (uint8 or uint16).
    rois:
        Dict from :func:`load_rois` / :func:`rasterize_rois`, with masks.
    alpha:
        Blending alpha for the filled ROI overlay (0 = no fill, 1 = solid).

    Returns
    -------
    np.ndarray
        RGB uint8 image with overlays drawn.
    """
    try:
        import cv2
    except ImportError:
        raise ImportError(
            "opencv-python is required for overlay_labels_on_frame(). "
            "Install it with: pip install opencv-python"
        )

    # Convert frame to uint8 RGB
    if frame.dtype == np.uint16:
        display = (frame / 256).astype(np.uint8)
    elif frame.dtype != np.uint8:
        # normalize float-ish
        mn, mx = frame.min(), frame.max()
        if mx > mn:
            display = ((frame - mn) / (mx - mn) * 255).astype(np.uint8)
        else:
            display = np.zeros(frame.shape[:2], dtype=np.uint8)
    else:
        display = frame.copy()

    # Convert to BGR (OpenCV native)
    if display.ndim == 2:
        bgr = cv2.cvtColor(display, cv2.COLOR_GRAY2BGR)
    elif display.shape[2] == 3:
        bgr = display.copy()
    else:
        bgr = display[:, :, :3].copy()

    overlay = bgr.copy()

    for idx, (name, roi_data) in enumerate(rois.items()):
        color = _get_color(name, idx)
        mask = roi_data.get("mask")
        poly = roi_data.get("polygon")

        # Draw filled area on overlay
        if mask is not None and mask.any():
            overlay[mask] = color

        # Draw polygon outline
        if poly is not None and len(poly) >= 2:
            pts = np.array(poly, dtype=np.int32)
            # pts are [y, x], cv2 wants [x, y]
            pts_xy = pts[:, ::-1].reshape(-1, 1, 2)
            cv2.polylines(bgr, [pts_xy], isClosed=True, color=color, thickness=2)

            # Compute centroid for label
            cy = int(np.mean(pts[:, 0]))
            cx = int(np.mean(pts[:, 1]))
            cv2.putText(
                bgr,
                name,
                (cx, cy),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                color,
                2,
                cv2.LINE_AA,
            )
        elif mask is not None and mask.any():
            # Compute centroid from mask
            ys, xs = np.where(mask)
            cy, cx = int(ys.mean()), int(xs.mean())
            cv2.putText(
                bgr,
                name,
                (cx, cy),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                color,
                2,
                cv2.LINE_AA,
            )

    # Blend overlay (filled regions) with original
    result = cv2.addWeighted(overlay, alpha, bgr, 1 - alpha, 0)

    # Convert BGR -> RGB for return
    return cv2.cvtColor(result, cv2.COLOR_BGR2RGB)


def extract_dff_traces(
    trial_dir: Path,
    rois: dict,
    baseline_frames: Optional[int] = None,
) -> dict:
    """Compute ΔF/F traces for each glomerulus ROI from trial frames.

    Parameters
    ----------
    trial_dir:
        Trial directory containing ``trial.json`` and ``images/images/``.
    rois:
        Dict from :func:`load_rois` (must have ``"mask"`` filled in).
    baseline_frames:
        Number of frames before odor onset to use as baseline F0.
        If None, uses ``odor_frame_start`` from trial.json.

    Returns
    -------
    dict
        ``{"DA1": {"raw_f": np.ndarray, "dff": np.ndarray, "f0": float}, ...}``
    """
    try:
        import tifffile
    except ImportError:
        raise ImportError(
            "tifffile is required for extract_dff_traces(). "
            "Install it with: pip install tifffile"
        )

    trial_dir = Path(trial_dir)
    trial_json = trial_dir / "trial.json"

    # Load metadata
    odor_frame_start = None
    odor_frame_end = None
    fps = 9.0

    if trial_json.exists():
        with open(trial_json, "r") as f:
            meta = json.load(f)
        odor_frame_start = meta.get("odor_frame_start")
        odor_frame_end = meta.get("odor_frame_end")
        fps = meta.get("fps", fps)

    if baseline_frames is None:
        if odor_frame_start is not None:
            baseline_frames = int(odor_frame_start)
        else:
            baseline_frames = 30  # default fallback: ~3 s at 9 fps

    # Load frames from images/images/ (nested!)
    frames_dir = trial_dir / "images" / "images"
    if not frames_dir.exists():
        raise FileNotFoundError(
            f"Frames directory not found: {frames_dir}\n"
            "Expected nested 'images/images/' structure."
        )

    frame_files = sorted(frames_dir.glob("frame_*.tif"))
    if not frame_files:
        raise FileNotFoundError(f"No frame_*.tif files found in {frames_dir}")

    # Collect masks (only valid ones)
    valid_rois = {
        name: roi_data
        for name, roi_data in rois.items()
        if roi_data.get("mask") is not None and roi_data["mask"].any()
    }

    if not valid_rois:
        raise ValueError(
            "No valid ROI masks found. Run rasterize_rois() before extract_dff_traces()."
        )

    # Initialize raw_f arrays
    n_frames = len(frame_files)
    raw_f: dict[str, list[float]] = {name: [] for name in valid_rois}

    for frame_path in frame_files:
        img = tifffile.imread(str(frame_path)).astype(np.float32)
        for name, roi_data in valid_rois.items():
            mask = roi_data["mask"]
            raw_f[name].append(float(img[mask].mean()))

    # Compute ΔF/F
    results: dict = {}
    for name, f_list in raw_f.items():
        f_arr = np.array(f_list, dtype=np.float32)
        f0_frames = f_arr[:baseline_frames]
        f0 = float(f0_frames.mean())

        if f0 > 0:
            dff = (f_arr - f0) / f0
        else:
            dff = np.zeros_like(f_arr)

        results[name] = {
            "raw_f": f_arr,
            "dff": dff,
            "f0": f0,
        }

    return results


def plot_dff_traces(
    dff_results: dict,
    trial_json: Path,
    output_path: Optional[Path] = None,
) -> None:
    """Plot ΔF/F traces for all glomeruli with odor timing markers.

    Parameters
    ----------
    dff_results:
        Output from :func:`extract_dff_traces`.
    trial_json:
        Path to trial.json (used for odor ON/OFF frame timing).
    output_path:
        If provided, save figure here (PNG/PDF). If None, call plt.show().
    """
    try:
        import matplotlib.pyplot as plt
        import matplotlib
        if output_path is not None:
            matplotlib.use("Agg")
    except ImportError:
        raise ImportError(
            "matplotlib is required for plot_dff_traces(). "
            "Install it with: pip install matplotlib"
        )

    trial_json = Path(trial_json)

    # Load odor timing
    odor_frame_start = None
    odor_frame_end = None
    fps = 9.0

    if trial_json.exists():
        with open(trial_json, "r") as f:
            meta = json.load(f)
        odor_frame_start = meta.get("odor_frame_start")
        odor_frame_end = meta.get("odor_frame_end")
        fps = meta.get("fps", fps)

    fig, ax = plt.subplots(figsize=(12, 6))

    for idx, (name, data) in enumerate(dff_results.items()):
        dff = data["dff"]
        frames = np.arange(len(dff))
        time_s = frames / fps

        # Get a matplotlib-compatible color (convert BGR → RGB float)
        bgr = _get_color(name, idx)
        rgb = (bgr[2] / 255, bgr[1] / 255, bgr[0] / 255)

        ax.plot(time_s, dff, color=rgb, label=name, linewidth=1.5)

    # Odor timing markers
    if odor_frame_start is not None:
        ax.axvline(
            x=odor_frame_start / fps,
            color="gray",
            linestyle="--",
            linewidth=1.5,
            label="Odor ON",
        )
    if odor_frame_end is not None:
        ax.axvline(
            x=odor_frame_end / fps,
            color="gray",
            linestyle=":",
            linewidth=1.5,
            label="Odor OFF",
        )

    ax.set_xlabel("Time (s)")
    ax.set_ylabel("ΔF/F")
    ax.set_title("Glomerulus ΔF/F Traces")
    ax.legend(loc="upper right", fontsize=8)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()

    if output_path is not None:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(output_path, dpi=150)
        print(f"Saved ΔF/F trace plot to {output_path}")
        plt.close(fig)
    else:
        plt.show()
