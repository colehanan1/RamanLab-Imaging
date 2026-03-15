"""
CaImAn integration for calcium imaging analysis.

CaImAn's CNMF is better than Suite2p for densely packed overlapping ROIs
like antennal lobe glomeruli.

CaImAn reference: https://github.com/flatironinstitute/CaImAn
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Optional

import numpy as np


def _load_fps_from_trial(trial_dir: Path) -> float:
    """Read fps from trial.json, falling back to 9.0."""
    trial_json = trial_dir / "trial.json"
    if trial_json.exists():
        with open(trial_json, "r") as f:
            meta = json.load(f)
        return float(meta.get("fps", 9.0))
    return 9.0


def _load_frame_stack(trial_dir: Path) -> np.ndarray:
    """Load all frames from trial_dir/images/images/ into a (T, H, W) array."""
    try:
        import tifffile
    except ImportError:
        raise ImportError("tifffile is required. Install with: pip install tifffile")

    frames_dir = trial_dir / "images" / "images"
    if not frames_dir.exists():
        raise FileNotFoundError(
            f"Frames directory not found: {frames_dir}\n"
            "Expected nested 'images/images/' structure."
        )
    frame_files = sorted(frames_dir.glob("frame_*.tif"))
    if not frame_files:
        raise FileNotFoundError(f"No frame_*.tif files found in {frames_dir}")

    frames = [tifffile.imread(str(f)) for f in frame_files]
    return np.stack(frames, axis=0)  # (T, H, W)


def run_caiman(
    trial_dir: Path,
    output_dir: Path,
    params_overrides: Optional[dict] = None,
) -> Path:
    """Run CaImAn CNMF on a single trial directory.

    Loads frames from ``trial_dir/images/images/``, saves as a
    memory-mapped file, configures CNMF parameters, and runs the full
    CaImAn pipeline.

    Parameters
    ----------
    trial_dir:
        Trial directory containing ``trial.json`` and ``images/images/``.
    output_dir:
        Directory where CaImAn output will be saved.
    params_overrides:
        Optional dict of CNMFParams to override defaults.  Keys should match
        CaImAn parameter group names, e.g.
        ``{"data": {"fr": 10}, "patch": {"rf": 30}}``.

    Returns
    -------
    Path
        Path to the output directory where results are saved.

    Raises
    ------
    ImportError
        If caiman is not installed.
    """
    try:
        import caiman as cm
        from caiman.source_extraction import cnmf
        from caiman.source_extraction.cnmf import params as caiman_params
        from caiman.motion_correction import MotionCorrect
    except ImportError:
        raise ImportError(
            "CaImAn is required for run_caiman(). "
            "Install with: pip install caiman\n"
            "See also: https://github.com/flatironinstitute/CaImAn"
        )

    trial_dir = Path(trial_dir)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    fps = _load_fps_from_trial(trial_dir)

    print(f"Loading frames from {trial_dir / 'images' / 'images'} ...")
    stack = _load_frame_stack(trial_dir)  # (T, H, W)
    T, H, W = stack.shape
    print(f"  Loaded {T} frames, shape ({H}, {W})")

    # Save as memory-mapped TIFF for CaImAn
    tmp_dir = Path(tempfile.mkdtemp(prefix="caiman_input_"))
    try:
        import tifffile
        tmp_tiff = tmp_dir / "frames.tif"
        tifffile.imwrite(str(tmp_tiff), stack)
        fnames = [str(tmp_tiff)]

        # Build CaImAn parameters
        params_dict = {
            "data": {
                "fnames": fnames,
                "fr": fps,
                "decay_time": 1.5,
            },
            "init": {
                "K": 30,
                "gSig": [5, 5],
                "method_init": "greedy_roi",
                "ssub": 1,
                "tsub": 1,
            },
            "patch": {
                "rf": 25,
                "stride": 10,
                "n_processes": 1,
            },
            "preprocess": {
                "p": 1,
            },
            "merge": {
                "merge_thr": 0.85,
            },
            "spatial": {},
            "temporal": {
                "p": 1,
            },
            "quality": {
                "min_SNR": 2.0,
                "rval_thr": 0.85,
            },
        }

        # Apply overrides (by group key)
        if params_overrides:
            for group, vals in params_overrides.items():
                if group in params_dict:
                    params_dict[group].update(vals)
                else:
                    params_dict[group] = vals

        # Flatten gnb into init group
        if "gnb" not in params_dict.get("init", {}):
            params_dict["init"]["nb"] = 2  # CaImAn calls it nb (background components)

        opts = caiman_params.CNMFParams(params_dict=params_dict)

        # Set up cluster
        c, dview, n_processes = cm.cluster.setup_cluster(
            backend="local", n_processes=1, single_thread=True
        )

        try:
            # Motion correction
            print("Running motion correction ...")
            mc = MotionCorrect(fnames, dview=dview, **opts.get_group("motion"))
            mc.motion_correct(save_movie=True)
            fname_mc = mc.fname_tot_els if hasattr(mc, "fname_tot_els") else mc.mmap_file

            # Convert to memmap
            fname_new = cm.save_memmap(
                fname_mc, base_name="memmap_", order="C", border_to_0=0, dview=dview
            )
            Yr, dims, T_val = cm.load_memmap(fname_new)
            images = Yr.T.reshape((T_val,) + dims, order="F")

            # Run CNMF
            print("Running CNMF ...")
            cnmf_obj = cnmf.CNMF(n_processes=1, params=opts, dview=dview)
            cnmf_obj = cnmf_obj.fit(images)

            # Evaluate components
            print("Evaluating components ...")
            cnmf_obj.estimates.evaluate_components(images, opts, dview=dview)

        finally:
            cm.stop_server(dview=dview)

        # Save results
        output_hdf5 = output_dir / "caiman_results.hdf5"
        cnmf_obj.save(str(output_hdf5))
        print(f"CaImAn complete. Results saved to {output_dir}")

    finally:
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)

    return output_dir


def load_caiman_results(output_dir: Path) -> dict:
    """Load saved CaImAn CNMF results.

    Parameters
    ----------
    output_dir:
        Directory containing ``caiman_results.hdf5`` (as saved by
        :func:`run_caiman`).

    Returns
    -------
    dict
        Keys: ``A`` (spatial components, sparse matrix), ``C`` (temporal
        traces), ``dff`` (ΔF/F traces), ``coords`` (component centroids).

    Raises
    ------
    ImportError
        If caiman is not installed.
    FileNotFoundError
        If the HDF5 results file is not found.
    """
    try:
        from caiman.source_extraction.cnmf import cnmf
        from caiman.utils.visualization import get_contours
    except ImportError:
        raise ImportError(
            "CaImAn is required for load_caiman_results(). "
            "Install with: pip install caiman"
        )

    output_dir = Path(output_dir)
    hdf5_path = output_dir / "caiman_results.hdf5"

    if not hdf5_path.exists():
        raise FileNotFoundError(
            f"CaImAn results not found: {hdf5_path}\n"
            "Run run_caiman() first."
        )

    print(f"Loading CaImAn results from {hdf5_path} ...")
    cnmf_obj = cnmf.CNMF.load(str(hdf5_path))

    estimates = cnmf_obj.estimates
    A = estimates.A           # spatial components (H*W x K sparse)
    C = estimates.C           # temporal traces (K x T)
    F_dff = getattr(estimates, "F_dff", None)

    # Compute ΔF/F if not already stored
    if F_dff is None and C is not None:
        F_dff = _compute_dff_caiman(C)

    # Component centroids
    dims = cnmf_obj.dims
    coords = _get_component_centroids(A, dims)

    return {
        "A": A,
        "C": C,
        "dff": F_dff,
        "coords": coords,
        "dims": dims,
        "cnmf": cnmf_obj,
    }


def _compute_dff_caiman(C: np.ndarray, percentile: float = 10.0) -> np.ndarray:
    """Compute ΔF/F from CaImAn temporal traces using percentile baseline."""
    from scipy.ndimage import percentile_filter

    dff = np.zeros_like(C, dtype=np.float32)
    window = max(30, C.shape[1] // 10)

    for i in range(C.shape[0]):
        trace = C[i].astype(np.float32)
        f0 = percentile_filter(trace, percentile=percentile, size=window)
        f0 = np.maximum(f0, 1e-6)
        dff[i] = (trace - f0) / f0

    return dff


def _get_component_centroids(A, dims: tuple) -> list[dict]:
    """Compute (y, x) centroids of spatial components."""
    H, W = dims
    centroids = []
    A_dense = A.toarray() if hasattr(A, "toarray") else np.array(A)

    for k in range(A_dense.shape[1]):
        spatial = A_dense[:, k].reshape(H, W, order="F")
        ys, xs = np.where(spatial > 0)
        if len(ys) == 0:
            centroids.append({"y": 0, "x": 0})
        else:
            weights = spatial[ys, xs]
            cy = float(np.average(ys, weights=weights))
            cx = float(np.average(xs, weights=weights))
            centroids.append({"y": cy, "x": cx})

    return centroids


def match_caiman_rois_to_glomeruli(
    caiman_results: dict,
    manual_rois: dict,
    image_shape: tuple,
) -> dict:
    """Match CaImAn spatial components to manually labeled glomeruli by IoU.

    For each manually labeled glomerulus mask, finds the CaImAn component
    with the highest intersection-over-union (IoU).

    Parameters
    ----------
    caiman_results:
        Output from :func:`load_caiman_results`.
    manual_rois:
        Dict of ``{name: {"mask": np.ndarray bool, ...}, ...}``.
    image_shape:
        ``(height, width)`` of the imaging plane.

    Returns
    -------
    dict
        ``{"DA1": caiman_component_index, ...}``
        Value is -1 if no matching component is found.
    """
    A = caiman_results["A"]
    dims = caiman_results.get("dims")
    H, W = image_shape[:2]

    if dims is None:
        dims = (H, W)

    A_dense = A.toarray() if hasattr(A, "toarray") else np.array(A)
    n_components = A_dense.shape[1]

    # Build binary masks for CaImAn spatial components
    caiman_masks = []
    for k in range(n_components):
        spatial = A_dense[:, k].reshape(dims[0], dims[1], order="F")
        # Resize if shapes differ
        if (dims[0], dims[1]) != (H, W):
            from skimage.transform import resize
            spatial = resize(spatial, (H, W), anti_aliasing=False)
        mask = spatial > 0
        caiman_masks.append(mask)

    mapping: dict = {}
    for name, roi_data in manual_rois.items():
        manual_mask = roi_data.get("mask")
        if manual_mask is None or not manual_mask.any():
            mapping[name] = -1
            continue

        best_iou = 0.0
        best_idx = -1
        for k, caiman_mask in enumerate(caiman_masks):
            intersection = (manual_mask & caiman_mask).sum()
            union = (manual_mask | caiman_mask).sum()
            if union == 0:
                continue
            iou = intersection / union
            if iou > best_iou:
                best_iou = iou
                best_idx = k

        mapping[name] = best_idx
        if best_idx >= 0:
            print(f"  {name} → CaImAn component #{best_idx} (IoU={best_iou:.3f})")
        else:
            print(f"  {name} → no match found")

    return mapping
