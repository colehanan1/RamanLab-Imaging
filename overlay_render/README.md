# overlay_render

A robust, view-only visualization pipeline for neuroscience imaging data. Creates overlay videos of calcium imaging recordings over structural reference images with odor/stimulus annotation.

## Features

- **View-only processing**: Brightness/contrast adjustments for visualization (no analysis operations)
- **Multiple input formats**: TIFF stacks, MP4/AVI videos, PNG structure images
- **Automatic registration**: Rigid (Euclidean) or affine alignment of recording to structure
- **Odor annotation**: "ODOR ON" text overlay during stimulus delivery frames
- **Flexible timing**: Parse odor timing from CSV per-frame data or JSON intervals
- **Comprehensive reports**: JSON reports with parameters, hashes, and processing metrics

## Installation

```bash
# Create virtual environment (recommended)
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install numpy opencv-python imageio imageio-ffmpeg tifffile pyyaml

# For running tests
pip install pytest
```

### Dependencies

| Package | Purpose |
|---------|---------|
| numpy | Array operations |
| opencv-python | Registration, annotation drawing |
| imageio | Video reading/writing |
| imageio-ffmpeg | FFmpeg backend for MP4 |
| tifffile | TIFF stack reading |
| pyyaml | Configuration parsing |

## Quick Start

### 1. Create a configuration file

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

### 2. Run the pipeline

```bash
python -m overlay_render --config my_config.yaml
```

### 3. Check outputs

- `<recording_stem>_overlay.mp4` - Rendered overlay video
- `<recording_stem>_report.json` - Processing report
- `<recording_stem>_thumbnail.png` - Representative frame

## Usage

### Basic Command

```bash
python -m overlay_render --config path/to/config.yaml
```

### CLI Options

```bash
python -m overlay_render --help

# Verbose output
python -m overlay_render --config config.yaml -v

# Dry run (validate config only)
python -m overlay_render --config config.yaml --dry-run

# Override config values
python -m overlay_render --config config.yaml --view.gamma 1.5 --overlay.alpha 0.7
```

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

## License

MIT License - See LICENSE file for details.

## Contributing

1. Fork the repository
2. Create a feature branch
3. Add tests for new functionality
4. Submit a pull request

## Acknowledgments

Built for the Raman Lab neuroscience imaging workflow.
