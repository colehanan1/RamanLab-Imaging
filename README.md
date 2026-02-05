# RamanLab-Imaging

**Neuroscience imaging acquisition and visualization tools for the Raman Lab.**

This repository contains two main tools for calcium imaging (GCaMP) experiments with odor stimulation:

## 🔬 Components

### 1. `mm_odor_recorder_v9.py` - Live Acquisition GUI
Desktop application for synchronized data acquisition:
- **Microscopy control**: Integrates with Micro-Manager for live imaging
- **Odor delivery**: ESP32-based valve control with precise timing
- **Protocol runner**: Multi-trial, multi-odor experiment automation
- **Output**: RAW TIFF frames + synchronized metadata (JSON + CSV)

**Use case:** Run experiments, acquire data

### 2. `overlay_render/` - Post-Processing Pipeline
Python package for creating annotated visualization videos:
- **Automatic alignment**: Register recording frames to structural reference
- **Odor annotation**: Overlay "ODOR ON" text during stimulus delivery
- **View scaling**: Percentile contrast, gamma correction, optional CLAHE
- **Optional denoising**: Classical (bilateral, NLM) or deep learning (Noise2Void)
- **Output**: MP4 videos + thumbnails + processing reports

**Use case:** Create publication-ready videos from acquired data

---

## 🚀 Quick Start

### Acquisition (mm_odor_recorder)
```bash
# Install dependencies
pip install pycromanager pyserial pillow opencv-python

# Run GUI
python mm_odor_recorder_v9.py
```

### Visualization (overlay_render)
```bash
# Install core dependencies
pip install numpy opencv-python imageio imageio-ffmpeg tifffile pyyaml pytest

# Process experiment data
cd overlay_render/
python -m overlay_render --folder /path/to/trial_folder/

# Interactive preview
python -m overlay_render.preview --folder /path/to/trial_folder/
```

See [`overlay_render/README.md`](overlay_render/README.md) for detailed documentation.

---

## 📂 Data Structure

Typical experiment folder structure created by `mm_odor_recorder`:

```
2xOdor_Panel_GH146xOr7a_Female_d_post0h_20260202_112312/
├── structure.tif                    # Structural reference (RFP)
├── Trial_001_OFM_E_123456/          # First trial
│   ├── frames/                      # RAW TIFF frames (uint8/uint16)
│   │   ├── frame_00000.tif
│   │   ├── frame_00001.tif
│   │   └── ...
│   ├── metadata.json                # Camera settings, timing, odor info
│   └── frames.csv                   # Per-frame timestamps
├── Trial_002_OFM_B_123457/          # Second trial
│   └── ...
└── ... (more trials)
```

After processing with `overlay_render`:
```
Trial_001_OFM_E_123456_overlay.mp4       # Annotated video
Trial_001_OFM_E_123456_report.json       # Processing parameters
Trial_001_OFM_E_123456_thumbnail.png     # Representative frame
```

---

## 🧪 What This Pipeline Does (and Doesn't Do)

**✅ This pipeline provides:**
- Video rendering and visualization
- Frame alignment and registration
- Contrast/brightness adjustments for viewing
- Odor stimulus annotation
- Optional denoising for cleaner visuals

**❌ This pipeline does NOT provide:**
- Signal extraction (ΔF/F, ROI analysis)
- Spike detection or neural activity quantification
- Statistical analysis or comparisons
- Cell segmentation or tracking

This is a **presentation-only pipeline** for creating videos. Use other tools (ImageJ, Suite2p, CaImAn, etc.) for scientific analysis.

---

## 📖 Documentation

- **Acquisition GUI**: See inline documentation in `mm_odor_recorder_v9.py`
- **Visualization Pipeline**: See [`overlay_render/README.md`](overlay_render/README.md)
- **Development Guidelines**: See [`.github/copilot-instructions.md`](.github/copilot-instructions.md)

---

## 🛠️ Development

### Testing
```bash
# Test overlay_render pipeline
pytest overlay_render/tests/ -v

# Quick test
pytest overlay_render/tests/ -q
```

### Environment
- Python 3.11+ recommended
- Conda environment: `imaging` (see `.github/copilot-instructions.md`)
- Core dependencies: numpy, opencv-python, imageio, tifffile, pyyaml

---

## 📄 License

MIT License - See LICENSE file for details.

---

## 🙏 Acknowledgments

Built for the Raman Lab neuroscience imaging workflow.
