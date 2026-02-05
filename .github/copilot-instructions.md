# RamanLab-Imaging - Copilot Instructions

## Project Overview

This repository contains two main components for neuroscience imaging workflows:

1. **mm_odor_recorder_v9.py** - Desktop GUI for live data acquisition
   - Integrates Micro-Manager microscopy control with ESP32-based odor delivery
   - Records calcium imaging (GCaMP) with synchronized odor stimulus timing
   - Protocol runner for multi-odor, multi-trial experiments with auto folder structuring

2. **overlay_render/** - Post-acquisition visualization pipeline (Python package)
   - View-only processing: creates overlay videos of recordings over structural reference images
   - No analysis/signal processing—purely presentation pipeline for alignment + visualization
   - Automatic registration (rigid/affine), odor annotation overlays, configurable rendering
   - **Optional denoising**: Classical (NLM, bilateral) and deep learning (Noise2Void) methods

## CRITICAL: Module Import Patterns ⚠️

**overlay_render modules use RELATIVE IMPORTS** - This is a common mistake:

```python
# ❌ WRONG - Will fail with "attempted relative import with no known parent package"
from denoise import apply_denoise
from config import DenoiseSettings

# ✅ CORRECT - Always import through the package
from overlay_render.denoise import apply_denoise
from overlay_render.config import DenoiseSettings

# ✅ CORRECT - When testing in overlay_render/ directory
python -m overlay_render.cli --folder trial/
pytest tests/test_smoke.py

# ❌ WRONG - Running scripts directly breaks relative imports
python denoise.py
python train_n2v.py
```

**Testing Module Imports:**
```bash
# From repo root:
cd /path/to/RamanLab-Imaging
python -c "from overlay_render.denoise import apply_denoise; print('✓')"

# From overlay_render/ subdirectory:
cd overlay_render
python -c "from denoise import apply_denoise; print('✓')"  # This WILL fail
python -m denoise  # This will also fail
```

## DOCUMENTATION POLICY 📝

**ALWAYS update README.md after every major codebase edit:**

- ✅ Add new features to README usage section
- ✅ Update CLI examples when arguments change
- ✅ Document new configuration options
- ✅ Add performance notes for new features
- ✅ Update architecture diagrams if data flow changes
- ✅ Keep "Quick Start" section current

**When to update README:**
- After implementing new features (denoising, registration modes, etc.)
- After changing CLI interface or config structure
- After adding new dependencies or optional features
- After fixing major bugs that affect usage patterns

**README sections to maintain:**
- Quick Start (must work copy-paste)
- Installation instructions (including optional dependencies)
- Configuration examples (YAML + CLI)
- Architecture/data flow diagrams
- Troubleshooting common issues

## Running Tests

```bash
# overlay_render tests only
pytest overlay_render/tests/ -v

# Run specific test file
pytest overlay_render/tests/test_smoke.py -v

# With coverage
pytest overlay_render/tests/ -v --cov=overlay_render
```

There are no tests for mm_odor_recorder_v9.py (GUI application).

## overlay_render Usage

### Basic Commands

```bash
# Process entire experiment folder (auto-discovery mode - recommended)
python -m overlay_render --folder /path/to/experiment_folder

# Filter specific odor trials
python -m overlay_render --folder /path/to/folder --filter OFM_E

# Process single trial with config file
python -m overlay_render --config experiment.yaml

# Interactive GUI for tuning view parameters
python -m overlay_render.preview --folder /path/to/experiment

# Recording-only render (no structure overlay)
python -m overlay_render --folder /path/to/folder --settings tuned_settings.yaml --recording-only
```

### CLI Entry Points

- **overlay_render.cli:main** - Main command-line interface
- **overlay_render.preview** - Interactive GUI for parameter tuning

## Architecture

### Data Flow (overlay_render)

1. **Discovery** (`discovery.py`) - Scans experiment folders, identifies structure/recording/metadata files per trial
2. **Loading** (`loaders.py`) - Reads TIFF stacks, MP4/AVI videos, PNG structure images
3. **Timing** (`timing.py`) - Parses odor intervals from JSON metadata or per-frame CSV
4. **Registration** (`registration.py`) - Aligns recording to structure using OpenCV's ECC (Euclidean or affine)
5. **View Scaling** (`view_scaling.py`) - Applies percentile/minmax scaling, gamma correction, optional CLAHE
6. **Overlay** (`overlay.py`) - Composites recording over structure (blend or falsecolor mode)
7. **Annotation** (`annotation.py`) - Draws "ODOR ON" text boxes on stimulus frames
8. **Writer** (`writer.py`) - Outputs MP4 video + thumbnail + JSON report

### mm_odor_recorder_v9.py Structure

- **Single-file GUI application** (~6000+ lines) with Tkinter interface
- **Threading model**: Separate threads for camera acquisition, serial communication, protocol execution
- **Three tabs**: Run (single trial), Protocol Runner (multi-trial sequences), Replay Preview
- **Frame saving**: RAW pixel values (uint8/uint16 TIFF) with no contrast/bit-depth scaling
- **Output structure**: `<Folder>/<ExperimentID>/<TrialID>/` with frames/, metadata.json, frames.csv

### Configuration System (overlay_render)

- **YAML-based config** with dataclass validation (`config.py`)
- **Nested settings**: `overlay`, `view`, `registration`, `annotation`, `timing`
- **CLI overrides**: `--view.gamma 1.5 --overlay.alpha 0.7` syntax supported
- **Auto-discovery mode**: Uses `discovery.py` to find files; minimal config needed

## Key Conventions

### File Naming Patterns

**Experiment folders** (mm_odor_recorder output):
```
2xOdor_Pannel_GH146xOr7a_Female_d_post0h_20260202_112312/
├── structure.tif                    # RFP/structural channel
├── Trial_001_OFM_E_123456/         # Individual trial folders
│   ├── frames/                      # Raw TIFF frames
│   ├── metadata.json                # Timing, odor info, camera settings
│   └── frames.csv                   # Per-frame timestamps
└── Trial_002_OFM_B_123457/
```

**overlay_render outputs** (placed alongside trial folders):
```
Trial_001_OFM_E_123456_overlay.mp4
Trial_001_OFM_E_123456_report.json
Trial_001_OFM_E_123456_thumbnail.png
```

### Overlay Modes

- **blend**: Simple alpha blending (grayscale)
- **falsecolor**: Structure in red/magenta, recording in green (common for RFP + GCaMP)

### View Scaling Methods

- **percentile** (default): Use p_lo/p_hi percentiles for contrast (e.g., 1st-99th)
- **minmax**: Use absolute min/max pixel values across entire recording

### Registration Models

- **euclidean**: Translation + rotation (2 DOF)
- **affine**: Translation + rotation + scale + shear (6 DOF)

Both use OpenCV's `findTransformECC` with downscaling for efficiency.

### Odor Timing Sources

- **JSON metadata**: `odor_intervals` array with `start_frame`, `end_frame`, `odor_name`
- **CSV frames file**: Column named `odor_on`, `odor`, or `valve_open` (per-frame boolean)
- **Auto-detection**: Tries JSON first, falls back to CSV

### Odor Code Mappings (mm_odor_recorder)

```python
ODOR_OPTIONS = ["OFM_A", "OFM_B", "OFM_C", "OFM_H", "OFM_L", "OFM_O", "OFM_E"]
ODOR_HUMAN_NAMES = {
    'OFM_A': 'apple cider vinegar',
    'OFM_B': 'benzaldehyde',
    'OFM_C': 'citral',
    'OFM_H': 'hexanol',
    'OFM_E': 'ethyl butyrate',
    'OFM_O': '3-octanol',
    'OFM_L': 'linalool',
}
```

### Python Environment

- **Conda environment**: `imaging` (see .claude/settings.local.json for auto-approved commands)
- **Key dependencies**: numpy, opencv-python, imageio, tifffile, pyyaml, pytest
- **Live acquisition dependencies**: pycromanager, pyserial, PIL, tkinter

### Version Tracking

- **mm_odor_recorder**: Version number in `SOFTWARE_VERSION` constant + git hash in metadata
- **overlay_render**: Version in pyproject.toml (0.1.0)

## Common Patterns

### Adding a New View Parameter

1. Add field to `ViewSettings` dataclass in `config.py`
2. Update `scale_frame()` in `view_scaling.py` to use the parameter
3. Add trackbar to `PreviewGUI` in `preview.py` if applicable
4. Update example config files in `examples/`

### Adding a New Overlay Mode

1. Add mode literal to `OverlaySettings.mode` in `config.py`
2. Implement rendering logic in `OverlayRenderer.render_frame()` in `overlay.py`
3. Update README.md documentation

### Debugging Registration Failures

- Check `_report.json` for `registration.converged` and `registration.correlation` fields
- Use `preview.py` GUI with 'r' key to toggle registration on/off in real-time
- Increase `registration.downscale` for coarser/faster alignment
- Reduce `registration.ecc_iters` to fail faster when images are too different

## Dependencies

**overlay_render core**:
- numpy (array operations)
- opencv-python (registration, annotation drawing)
- imageio + imageio-ffmpeg (video I/O)
- tifffile (TIFF stack reading)
- pyyaml (config parsing)

**mm_odor_recorder** (acquisition GUI):
- pycromanager (Micro-Manager Python API)
- pyserial (ESP32 communication)
- tkinter (GUI - included with Python)
- PIL/Pillow (image display)
- opencv-python (optional, for MP4 preview video)

## Notes

- **No analysis operations**: overlay_render is strictly view-only processing for presentation
- **RAW frame saving**: mm_odor_recorder saves true raw pixel values (uint8/uint16) without contrast scaling
- **Thread safety**: mm_odor_recorder uses queues for cross-thread communication (camera → GUI, serial → GUI)
- **CLAHE disabled by default**: Explicitly disabled in v10.0 replay preview settings
- **FFmpeg required**: For MP4 reading/writing in overlay_render (via imageio-ffmpeg)
