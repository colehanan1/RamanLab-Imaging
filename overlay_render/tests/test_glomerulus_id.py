"""Unit tests for glomerulus_id."""
from __future__ import annotations

import json

import numpy as np
import pytest
import tifffile


@pytest.fixture
def fake_structure_dir(tmp_path):
    d = tmp_path / "structure"
    d.mkdir()
    for i in range(3):
        img = (np.random.rand(64, 64) * 1000).astype(np.uint16)
        tifffile.imwrite(str(d / f"structure_00{i+1}.tif"), img)
    return d


@pytest.fixture
def fake_trial_dir(tmp_path):
    trial_dir = tmp_path / "trial_001_OFM_H"
    frames_dir = trial_dir / "images" / "images"
    frames_dir.mkdir(parents=True)
    for i in range(30):
        img = (np.random.rand(64, 64) * 1000).astype(np.uint16)
        tifffile.imwrite(str(frames_dir / f"frame_{i:05d}.tif"), img)
    meta = {
        "acquisition": {
            "fps_measured": 9.0,
            "odor_frame_start": 10,
            "odor_frame_end": 20,
            "frames_captured": 30,
        },
        "odor_name": "hexanol",
    }
    with open(trial_dir / "trial.json", "w") as f:
        json.dump(meta, f)
    return trial_dir


@pytest.fixture
def simple_rois():
    return {
        "DA1": {"polygon": [[10, 10], [10, 20], [20, 20], [20, 10]], "mask": None},
        "DM2": {"polygon": [[30, 30], [30, 45], [45, 45], [45, 30]], "mask": None},
    }


def test_generate_mean_image(fake_structure_dir):
    from overlay_render.glomerulus_id import generate_mean_image
    img = generate_mean_image(fake_structure_dir)
    assert img.dtype == np.float32
    assert img.shape == (64, 64)
    assert 0.0 <= img.min() <= img.max() <= 1.0


def test_save_load_rois_roundtrip(tmp_path, simple_rois):
    from overlay_render.glomerulus_id import load_rois, save_rois
    roi_path = tmp_path / "rois.json"
    save_rois(simple_rois, roi_path)
    loaded = load_rois(roi_path, (64, 64))
    assert set(loaded.keys()) == {"DA1", "DM2"}
    assert loaded["DA1"]["mask"].dtype == bool


def test_extract_dff_traces(fake_trial_dir, simple_rois):
    from overlay_render.glomerulus_id import extract_dff_traces, rasterize_rois
    rois = rasterize_rois(simple_rois, (64, 64))
    res = extract_dff_traces(fake_trial_dir, rois)
    assert set(res.keys()) == {"DA1", "DM2"}
    assert len(res["DA1"]["raw_f"]) == 30
    assert len(res["DA1"]["dff"]) == 30


def test_overlay_labels_smoke(simple_rois):
    from overlay_render.glomerulus_id import overlay_labels_on_frame, rasterize_rois
    rois = rasterize_rois(simple_rois, (64, 64))
    frame = (np.random.rand(64, 64) * 1000).astype(np.uint16)
    out = overlay_labels_on_frame(frame, rois)
    assert out.dtype == np.uint8
    assert out.shape == (64, 64, 3)
