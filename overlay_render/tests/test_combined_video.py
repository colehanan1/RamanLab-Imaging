"""
Tests for combined multi-trial comparison video rendering.
"""

import shutil
import tempfile
from pathlib import Path

import numpy as np
import pytest

from overlay_render.cli import _render_combined_trial_video
from overlay_render.writer import VideoWriter


def _make_test_video(path: Path, n_frames: int, base_rgb: tuple, fps: float = 10.0):
    """Create a tiny synthetic RGB test video."""
    with VideoWriter(path, fps=fps, frame_size=(64, 64)) as writer:
        for i in range(n_frames):
            frame = np.zeros((64, 64, 3), dtype=np.uint8)
            frame[:, :, 0] = min(255, base_rgb[0] + i * 3)
            frame[:, :, 1] = min(255, base_rgb[1] + i * 2)
            frame[:, :, 2] = min(255, base_rgb[2] + i * 1)
            writer.write_frame(frame)


def _count_video_frames(path: Path) -> int:
    """Count frames by decoding via cv2."""
    cv2 = pytest.importorskip("cv2")
    cap = cv2.VideoCapture(str(path))
    count = 0
    while True:
        ok, _ = cap.read()
        if not ok:
            break
        count += 1
    cap.release()
    return count


class TestCombinedVideo:
    """Tests for synchronized multi-trial combined video output."""

    @pytest.fixture
    def test_dir(self):
        tmpdir = Path(tempfile.mkdtemp(prefix="overlay_render_combined_"))
        yield tmpdir
        shutil.rmtree(tmpdir, ignore_errors=True)

    def test_render_combined_video_odor_sync(self, test_dir):
        video_a = test_dir / "trial_a.mp4"
        video_b = test_dir / "trial_b.mp4"
        output = test_dir / "combined.mp4"

        _make_test_video(video_a, n_frames=10, base_rgb=(20, 40, 60), fps=10.0)
        _make_test_video(video_b, n_frames=8, base_rgb=(80, 30, 10), fps=10.0)

        trials_data = [
            {
                "trial_name": "trial_001_OFM_A",
                "odor_name": "apple cider vinegar",
                "video_path": str(video_a),
                "n_frames": 10,
                "fps": 10.0,
                "timing_intervals": [{"start_frame": 2, "end_frame": 4, "odor_name": "A"}],
                "anchor_frame": 2,
            },
            {
                "trial_name": "trial_002_OFM_B",
                "odor_name": "benzaldehyde",
                "video_path": str(video_b),
                "n_frames": 8,
                "fps": 10.0,
                "timing_intervals": [{"start_frame": 4, "end_frame": 6, "odor_name": "B"}],
                "anchor_frame": 4,
            },
        ]

        saved_path = _render_combined_trial_video(
            trials_data=trials_data,
            output_path=output,
            sync_mode="odor_on",
            max_tile_size=64,
            grid_cols=2,
        )

        assert saved_path.exists()
        assert _count_video_frames(saved_path) == 12

