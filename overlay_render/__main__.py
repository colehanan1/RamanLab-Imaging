"""
Entry point for running overlay_render as a module.

Usage:
    python -m overlay_render --config path/to/config.yaml
    python -m overlay_render --folder /path/to/experiment
    python -m overlay_render.preview --folder /path/to/experiment  # GUI tuning
"""

import sys
from .cli import main

if __name__ == "__main__":
    sys.exit(main())
