# overlay_render

**A visualization pipeline for calcium imaging neuroscience data.**

Overlay_render creates annotated videos by overlaying calcium imaging recordings (GCaMP fluorescence) over structural reference images (RFP). It automatically aligns frames, annotates odor stimulus timing, and produces publication-ready videos with optional denoising.

**What this pipeline does:**
- 🎥 Creates overlay videos: recording frames + structure image + odor annotations
- 🔄 Automatic frame-by-frame registration (rigid or affine alignment)
- 📊 View scaling: percentile/minmax contrast, gamma correction, optional CLAHE
- 🧪 Odor annotation: "ODOR ON" text boxes during stimulus delivery
- 🖼️ Optional denoising: Classical (NLM, bilateral) or deep learning (Noise2Void)
- 📝 Comprehensive JSON reports with processing parameters and metrics

**What this pipeline does NOT do:**
- ❌ Signal extraction (ΔF/F, ROI analysis, etc.) - use other tools
- ❌ Spike detection or neural activity analysis
- ❌ Statistical analysis or comparisons

This is a **view-only presentation pipeline** for creating videos and thumbnails.

---

## Quick Start

### Installation

```bash
# From repository root
cd /path/to/RamanLab-Imaging/RamanLab-Imaging

# Install core dependencies
pip install numpy opencv-python imageio imageio-ffmpeg tifffile pyyaml pytest

# Optional: Install denoising dependencies (for Noise2Void deep learning)
pip install torch torchvision tqdm
```

### Basic Usage

```bash
# Process a single trial with auto-discovery (easiest)
python -m overlay_render --folder /path/to/Trial_001_OFM_E_123456/

# Process entire experiment folder (all trials)
python -m overlay_render --folder /path/to/experiment_folder/

# Recording-only mode (no structure overlay)
python -m overlay_render --folder /path/to/trial/ --recording-only

# With denoising (fast classical method)
python -m overlay_render --folder /path/to/trial/ \
  --denoise.enabled true \
  --denoise.method bilateral \
  --denoise.strength 5

# Interactive preview GUI (tune parameters, then save settings)
python -m overlay_render.preview --folder /path/to/trial/
```

### Typical Workflow

```bash
# 1. Preview and tune parameters interactively
python -m overlay_render.preview --folder trial_folder/
# → Adjust gamma, contrast, CLAHE, etc. in GUI
# → Press 's' to save tuned_settings.yaml

# 2. Render final video with tuned settings
python -m overlay_render --folder trial_folder/ \
  --settings tuned_settings.yaml

# 3. Output files created:
# trial_folder_overlay.mp4
# trial_folder_report.json  
# trial_folder_thumbnail.png
```

---

## Features

### Core Features
- ✅ **View-only processing**: Brightness/contrast adjustments for visualization (no analysis)
- ✅ **Multiple input formats**: TIFF stacks, MP4/AVI videos, PNG structure images
- ✅ **Automatic registration**: Rigid (Euclidean) or affine alignment using OpenCV ECC
- ✅ **Odor annotation**: "ODOR ON" text overlay during stimulus delivery frames
- ✅ **Flexible timing**: Parse odor timing from CSV per-frame data or JSON intervals
- ✅ **Comprehensive reports**: JSON reports with parameters, hashes, and processing metrics

### Optional Denoising (New!)
- 🔹 **Classical methods**: Fast CPU-based (bilateral filter ~2ms/frame, NLM ~32ms/frame)
- 🔹 **Deep learning**: Noise2Void self-supervised training + GPU inference
- 🔹 **Recording-only**: Denoising applied only to recording frames, never structure
- 🔹 **Flexible config**: Enable/disable, choose method, tune strength via YAML or CLI

---

## Installation

```bash
# Create virtual environment (recommended)
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install core dependencies
pip install numpy opencv-python imageio imageio-ffmpeg tifffile pyyaml pytest

# Optional: Install denoising dependencies (Noise2Void deep learning)
pip install torch torchvision tqdm
```

### Dependencies

**Core (required):**
| Package | Purpose |
|---------|---------|
| numpy | Array operations |
| opencv-python | Registration, annotation drawing |
| imageio | Video reading/writing |
| imageio-ffmpeg | FFmpeg backend for MP4 |
| tifffile | TIFF stack reading |
| pyyaml | Configuration parsing |
| pytest | Running tests |

**Optional (for N2V denoising):**
| Package | Purpose |
|---------|---------|
| torch | PyTorch for deep learning inference |
| torchvision | Image transforms |
| tqdm | Progress bars for training |

---

## Command-Line Interface

### Main Pipeline

```bash
# Process a single trial folder
python -m overlay_render --folder /path/to/Trial_001_OFM_E_123456/

# Process entire experiment (all trials)
python -m overlay_render --folder /path/to/experiment_folder/

# Use custom config file
python -m overlay_render --config my_config.yaml

# Recording-only (no structure overlay)
python -m overlay_render --folder /path/to/trial/ --recording-only

# Filter specific odor types
python -m overlay_render --folder /path/to/experiment/ --filter OFM_E

# Also create one synchronized all-trials comparison video
python -m overlay_render --folder /path/to/experiment/ \
  --combined-video \
  --combined-sync odor_on

# Override config parameters via CLI
python -m overlay_render --folder /path/to/trial/ \
  --view.gamma 1.5 \
  --overlay.alpha 0.7 \
  --annotation.font_scale 1.2
```

### Preview GUI

```bash
# Launch interactive preview
python -m overlay_render.preview --folder /path/to/trial/

# Preview with saved settings
python -m overlay_render.preview --folder /path/to/trial/ \
  --settings tuned_settings.yaml

# Recording-only preview
python -m overlay_render.preview --folder /path/to/trial/ --recording-only
```

**Preview Keyboard Controls:**
- `Space` - Play/pause
- `→/←` - Next/previous frame
- `s` - Save current settings to `tuned_settings.yaml`
- `r` - Toggle registration on/off
- `q` - Quit

### Output Files

Running the pipeline creates three files per trial:

```
Trial_001_OFM_E_123456_overlay.mp4       # Annotated video
Trial_001_OFM_E_123456_report.json       # Processing parameters and metrics
Trial_001_OFM_E_123456_thumbnail.png     # Representative frame thumbnail
```

If `--combined-video` is used in folder mode, one additional file is written:

```
all_trials_overlay_synced.mp4            # Grid video aligned by odor onset (or start)
```

---

## Configuration

### Example Configuration File

Create a YAML file with your experiment parameters:

```yaml
# my_config.yaml
structure_path: "/path/to/structure.tif"
recording_path: "/path/to/recording.tif"
output_dir: "/path/to/output"
metadata_json_path: "/path/to/metadata.json"

overlay:
  alpha: 0.5
  mode: "falsecolor"

timing:
  fps: 30.0
```

Then run:
```bash
python -m overlay_render --config my_config.yaml
```

**OR use folder auto-discovery mode (recommended):**
```bash
python -m overlay_render --folder /path/to/trial_folder/
# Auto-discovers structure, recording, and metadata files
```

### Configuration Sections

## Configuration Reference

See [examples/example_config.yaml](examples/example_config.yaml) for a fully documented configuration file.

### Required Fields

| Field | Description |
|-------|-------------|
| `structure_path` | Path to structure image (.tif, .tiff, .png) |
| `recording_path` | Path to recording (.tif, .tiff, .mp4, .avi) |
| `output_dir` | Output directory for rendered files |

### Optional Fields

| Field | Default | Description |
|-------|---------|-------------|
| `frames_csv_path` | null | Per-frame timing CSV |
| `metadata_json_path` | null | Experiment metadata JSON |

### Overlay Settings

```yaml
overlay:
  alpha: 0.5         # 0-1, blend factor (0=structure, 1=recording)
  mode: "falsecolor" # "blend" or "falsecolor"
```

- **blend**: Simple alpha blending (grayscale)
- **falsecolor**: Structure in red/magenta, recording in green (common for RFP+GCaMP)

### View Scaling

```yaml
view:
  method: "percentile"  # "percentile" or "minmax"
  p_lo: 1               # Lower percentile
  p_hi: 99              # Upper percentile
  gamma: 1.0            # Gamma correction (1.0 = linear)
  clahe: false          # Enable CLAHE
```

### Denoise (Optional)

Denoising is applied to recording frames only, **before** view scaling/gamma/CLAHE.

```yaml
denoise:
  enabled: false        # Enable denoising (default: false)
  method: "none"        # "none", "nlm", "bilateral", "n2v"
  strength: 0           # Method-dependent strength parameter (ignored for n2v)
  device: "auto"        # "auto", "cpu", or "cuda" (for n2v)
  model_path: null      # Path to N2V model directory (required for method: n2v)
```

**Methods:**
- `none`: Disabled (default)
- `nlm`: Non-local means (CPU, ~32ms/frame, edge-preserving)
- `bilateral`: Bilateral filter (CPU, ~2ms/frame, fast edge-preserving)
- `n2v`: Noise2Void deep learning (GPU/CPU, requires trained model)

**CLI Override Examples:**
```bash
# Classical denoising (fast, no training needed)
--denoise.enabled true --denoise.method bilateral --denoise.strength 5

# N2V deep learning denoising (requires trained model)
--denoise.enabled true --denoise.method n2v --denoise.model_path models/my_model --denoise.device cuda
```

### Registration

```yaml
registration:
  enabled: true
  model: "affine"      # "affine" or "euclidean"
  ecc_iters: 200       # Max iterations
  ecc_eps: 1e-6        # Convergence threshold
  downscale: 2         # Compute at reduced resolution
```

### Annotation

```yaml
annotation:
  show_box_when_off: false  # Show gray box when odor OFF
  box:
    anchor: "bottom_left"   # Position
    width_px: 520
    height_px: 140
    margin_px: 20
  text:
    on: "ODOR ON"
    off: "ODOR OFF"
    font_scale: 1.6
    thickness: 3
```

### Timing

```yaml
timing:
  fps: 30.0           # Required if not in metadata
  odor_source: "auto" # "auto", "csv", or "json"
```

## Input Format Details

### Structure Image

Single-frame image representing the anatomical reference (e.g., RFP channel).

- **TIFF**: 8-bit or 16-bit grayscale/RGB
- **PNG**: 8-bit grayscale/RGB

### Recording

Time-series movie of functional activity (e.g., GCaMP signal).

- **TIFF stack**: Multi-page TIFF with one frame per page
- **MP4/AVI**: Standard video formats (FPS from metadata)

### Metadata JSON

```json
{
  "fps": 30.0,
  "odor_intervals": [
    {"start_frame": 100, "end_frame": 200, "odor_name": "ethyl_butyrate"},
    {"start_frame": 400, "end_frame": 500, "odor_name": "benzaldehyde"}
  ]
}
```

### Frames CSV

```csv
frame,odor_on,timestamp
0,0,0.0
1,0,0.033
...
100,1,3.333
101,1,3.367
...
```

## Examples

### TIFF Stack Recording

```yaml
structure_path: "/data/experiment/structure.tif"
recording_path: "/data/experiment/recording_stack.tif"
output_dir: "/data/experiment/output"

overlay:
  alpha: 0.6
  mode: "falsecolor"

timing:
  fps: 30.0
```

### MP4 Video Recording

```yaml
structure_path: "/data/experiment/structure.png"
recording_path: "/data/experiment/recording.mp4"
output_dir: "/data/experiment/output"

overlay:
  alpha: 0.4
  mode: "blend"

registration:
  enabled: false  # Often not needed for behavioral videos

timing:
  fps: null  # Read from MP4 metadata
```

## Output Report

The JSON report includes:

```json
{
  "meta": {
    "tool": "overlay_render",
    "version": "0.1.0",
    "timestamp": "2024-01-15T10:30:00"
  },
  "inputs": {
    "structure_path": "/path/to/structure.tif",
    "structure_hash": "sha256:...",
    "recording_path": "/path/to/recording.tif",
    "recording_hash": "sha256:..."
  },
  "config": { ... },
  "processing": {
    "n_frames_processed": 1000,
    "fps_used": 30.0,
    "processing_time_seconds": 45.2
  },
  "timing": {
    "source": "json",
    "intervals": [...]
  },
  "registration": {
    "model": "affine",
    "converged": true,
    "correlation": 0.95
  }
}
```

## Running Tests

```bash
# Run all tests
pytest overlay_render/tests/ -v

# Run smoke tests only
pytest overlay_render/tests/test_smoke.py -v

# Run with coverage
pytest overlay_render/tests/ -v --cov=overlay_render
```

## Troubleshooting

### "FPS must be specified"

The recording format doesn't contain FPS metadata. Add to your config:

```yaml
timing:
  fps: 30.0  # Set your actual recording FPS
```

### "Registration failed to converge"

The images may be too different for ECC registration. Try:

1. Reduce `ecc_iters` to fail faster
2. Increase `downscale` for coarser alignment
3. Switch to `model: "euclidean"` for simpler transform
4. Set `registration.enabled: false` to skip

### "No odor timing found"

Ensure your metadata file has the correct format:

- JSON: `odor_intervals` with `start_frame`/`end_frame`
- CSV: Column named `odor_on`, `odor`, or `valve_open`

### Video playback issues

The default output uses `yuv420p` pixel format for compatibility. If colors look wrong:

1. Try a different video player (VLC recommended)
2. Check if your system has FFmpeg installed correctly

---

## Testing

Run the test suite to verify installation:

```bash
# From repository root
cd /path/to/RamanLab-Imaging/RamanLab-Imaging

# Run all tests
pytest overlay_render/tests/ -v

# Run specific test file
pytest overlay_render/tests/test_smoke.py -v

# Quick test (quiet mode)
pytest overlay_render/tests/ -q

# With coverage report
pytest overlay_render/tests/ -v --cov=overlay_render
```

**Test coverage:** 42 comprehensive tests including:
- 11 original pipeline tests (loading, registration, rendering)
- 3 edge case tests (missing files, invalid parameters)
- 8 denoise configuration tests
- 11 classical denoising implementation tests
- 4 N2V training tests (skipped if PyTorch not installed)
- 5 N2V inference tests (skipped if PyTorch not installed)
- 1 full pipeline integration test with N2V

All tests use synthetic data and are deterministic (no external file dependencies).

---

## License

MIT License - See LICENSE file for details.

## Contributing

1. Fork the repository
2. Create a feature branch
3. Add tests for new functionality
4. Submit a pull request

## Acknowledgments

Built for the Raman Lab neuroscience imaging workflow.

## Noise2Void Training (Optional)

Train a self-supervised deep learning denoiser without requiring clean/paired data.

### Installation

```bash
# Install N2V dependencies (PyTorch + CUDA)
pip install -e .[n2v]

# This installs: torch>=2.0.0, torchvision>=0.15.0, tqdm>=4.60.0
```

### Training a Model

```bash
# Train on a TIFF stack
python -m overlay_render.train_n2v \
    --input recording.tif \
    --output_model_dir models/n2v_trial1 \
    --epochs 50 \
    --device cuda

# Train on a recording folder
python -m overlay_render.train_n2v \
    --input /path/to/trial_folder \
    --output_model_dir models/n2v_trial1 \
    --epochs 50 \
    --device cuda
```

### Training Options

| Option | Default | Description |
|--------|---------|-------------|
| `--input` | Required | Input TIFF stack or recording folder |
| `--output_model_dir` | Required | Output directory for model and logs |
| `--epochs` | 50 | Number of training epochs |
| `--batch_size` | 16 | Batch size for training |
| `--steps_per_epoch` | 200 | Number of batches per epoch |
| `--patch_size` | 64 | Training patch size (must be divisible by 16) |
| `--learning_rate` | 0.0004 | Learning rate |
| `--device` | auto | Device: auto, cpu, or cuda |
| `--seed` | 42 | Random seed |

### Expected Runtime

Hardware: NVIDIA 4080 GPU, 340-frame recording (256×256 pixels)

- **50 epochs**: ~10-15 minutes
- **100 epochs**: ~20-30 minutes

CPU training is significantly slower (~10x) and not recommended for production.

### Outputs

Training produces:

```
models/n2v_trial1/
├── model_best.pth       # Best model checkpoint
├── model_latest.pth     # Latest model checkpoint
├── config.json          # Training configuration
└── training_log.txt     # Loss history and training log
```

### Using Trained Model

After training, use the model for denoising in your rendering pipeline:

**In config YAML:**
```yaml
denoise:
  enabled: true
  method: "n2v"
  model_path: "models/n2v_trial1"  # Can be directory or .pth file
  device: "cuda"  # "auto", "cpu", or "cuda"
```

**Via CLI:**
```bash
# Apply N2V denoising during render
python -m overlay_render --folder trial_folder/ \
    --recording-only \
    --denoise.enabled true \
    --denoise.method n2v \
    --denoise.model_path models/n2v_trial1 \
    --denoise.device cuda

# Use in preview mode
python -m overlay_render --preview --folder trial_folder/ \
    --recording-only \
    --denoise.enabled true \
    --denoise.method n2v \
    --denoise.model_path models/n2v_trial1
```

**Notes:**
- Model is loaded once and cached for all frames (efficient)
- Inference uses same normalization as training (percentile-based)
- GPU inference is ~10-50x faster than CPU depending on frame size
- Model path can be directory (loads `model_best.pth`) or direct `.pth` file

**Note**: N2V inference is not yet implemented. The training utility prepares the model artifact for future use.

### Training Tips

1. **Data Requirements**: Minimum 50-100 frames recommended. More frames = better denoising.

2. **Patch Size**: 64x64 works well for most calcium imaging data. Use 128x128 for larger FOV.

3. **Epochs**: Start with 50 epochs. Monitor loss - if still decreasing, train longer.

4. **Device**: Always use CUDA for practical training times. CPU training is for testing only.

5. **Validation**: After training, test on held-out frames to ensure no overfitting.

### Troubleshooting

**Out of Memory (CUDA)**:
- Reduce `--batch_size` (e.g., 8 or 4)
- Reduce `--patch_size` (e.g., 48)

**Slow Training (GPU not utilized)**:
- Check `device: cuda` in log
- Verify CUDA is available: `python -c "import torch; print(torch.cuda.is_available())"`
- Update PyTorch/CUDA drivers

**Loss not decreasing**:
- Train longer (100+ epochs)
- Check data quality (sufficient frames, good SNR)
- Try different `--learning_rate` (e.g., 0.0002 or 0.0008)
