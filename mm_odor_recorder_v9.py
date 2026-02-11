#!/usr/bin/env python3
"""
Micro-Manager + ESP32 Odor Delivery Recorder v10.0
=================================================
Systems Neuroscience & Neuromorphic Engineering Lab
Drosophila Olfactory Behavior Recording System

New in v10.0:
  - RAW frame saving: acquired frames are written as true raw pixel values (uint8/uint16 TIFF) with no contrast/bit-depth scaling.
  - Robust dtype handling: pixel dtype is inferred from the Micro-Manager buffer to avoid accidental uint16 casting of uint8 data.
  - Protocol Runner tab: save/load JSON protocols and run multi-odor, multi-trial sequences with automatic folder structuring.
  - Improved acquisition pacing: frame interval respects both FPS target and exposure time (actual FPS can be exposure-limited).
  - Protocol replay preview: shows last trial playback between recordings (no live preview during protocol).

Previous fixes (v6.0-6.2 (legacy)):
- REMOVED auto brightness/contrast from preview & recording
- Fixed scaling based on camera bit depth
- Exposure entry field with max FPS indicator
- Preview uses continuous acquisition
- ROI changes safely stop preview first
- Dark mode works
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import threading
import queue
import csv
import json
import subprocess
import shutil
import os
from datetime import datetime
from pathlib import Path
import time
import re
import random

try:
    import serial
    import serial.tools.list_ports
    SERIAL_AVAILABLE = True
except ImportError:
    SERIAL_AVAILABLE = False

try:
    from pycromanager import Core
    PYCROMANAGER_AVAILABLE = True
except ImportError:
    PYCROMANAGER_AVAILABLE = False

try:
    import numpy as np
    from PIL import Image, ImageTk, ImageDraw
    IMAGING_AVAILABLE = True
except ImportError:
    IMAGING_AVAILABLE = False

try:
    import cv2
    VIDEO_AVAILABLE = True
except ImportError:
    VIDEO_AVAILABLE = False

# =============================================================================
# CONFIGURATION
# =============================================================================
DEFAULT_CONFIG_FILE = r"C:/Program Files/Micro-Manager-2.0/Basic_config_Primecam.cfg"
# Default save location: Documents/Cole/Data/Recordings-YYYYMMDD
_user_docs = Path.home() / "Documents"
_default_date_str = datetime.now().strftime("%Y%m%d")
DEFAULT_SAVE_DIR = str(_user_docs / "Cole" / "Data" / f"Recordings-{_default_date_str}")
DEFAULT_COM_PORT = "COM3"
BAUD_RATE = 115200
ODOR_OPTIONS = ["OFM_A", "OFM_B", "OFM_C", "OFM_H", "OFM_L", "OFM_O", "OFM_E"]
OFM_P_PIN = "OFM_P"  # Carrier/dilution pin that turns on with every odor
SOFTWARE_VERSION = '10.0'

# Remote server sync settings
REMOTE_SYNC_ENABLED = False  # Will be configurable in UI
REMOTE_SERVER_HOST = ""  # e.g., "user@server.university.edu:/data/flies/"
REMOTE_SYNC_METHOD = "rclone"  # Options: "rsync" or "rclone"


# Human-friendly odor names (used for overlays / metadata)
ODOR_HUMAN_NAMES = {
    'OFM_A': 'apple cider vinegar',
    'OFM_B': 'benzaldehyde',
    'OFM_C': 'citral',
    'OFM_H': 'hexanol',
    'OFM_E': 'ethyl butyrate',
    'OFM_O': '3-octanol',
    'OFM_L': 'linalool',
}


def get_git_hash():
    """Return current git commit hash if available, else empty string."""
    try:
        out = subprocess.check_output(["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL)
        return out.decode("utf-8", errors="ignore").strip()
    except Exception:
        return ""

def safe_slug(s: str, max_len: int = 64) -> str:
    """Filesystem-safe slug (ASCII-ish)."""
    try:
        s = str(s)
    except Exception:
        s = "x"
    s = s.strip().replace(" ", "_")
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    if not s:
        s = "x"
    return s[:max_len]

DEFAULT_FPS = 9
DEFAULT_EXPOSURE_MS = 100
DEFAULT_BASELINE = 15
DEFAULT_ODOR = 4
DEFAULT_POST = 15
DEFAULT_ITI = 60
PREVIEW_FPS = 30  # Target FPS for preview (will be limited by camera/system)
STATS_EVERY_N_FRAMES = 10  # Only calculate stats every N frames
MIN_DISK_GB = 20

# Protocol runner defaults
DEFAULT_PROTOCOL_NAME = "full-odor-pannel"
DEFAULT_PROTOCOL_REPEATS_PER_ODOR = 1
DEFAULT_PROTOCOL_INTER_ODOR_SEC = 60
DEFAULT_PROTOCOL_FLY_IDS = ""  # blank = use Fly ID from Run tab
DEFAULT_SAVE_PROTOCOL_VIDEO = False

# Protocol replay preview (embedded tuned settings; no external file required)
REPLAY_VIEW_SETTINGS = {
    "method": "percentile",
    "p_lo": 40,
    "p_hi": 100,
    "gamma": 1.3,
    "clahe": False,  # CLACHE/CLAHE explicitly disabled for v10
}

# =============================================================================
# THEMES
# =============================================================================
class ThemeColors:
    """Base theme class with color definitions"""
    pass

class LightTheme(ThemeColors):
    name = "light"
    NAVY_DARK = "#1a2744"
    NAVY = "#2c3e50"
    BLUE_ACCENT = "#3498db"
    BLUE_LIGHT = "#5dade2"
    BLUE_PALE = "#85c1e9"
    GRAY_MID = "#636e72"
    GRAY_LIGHT = "#b2bec3"
    GRAY_PALE = "#dfe6e9"
    BG_MAIN = "#ecf0f1"
    BG_SECTION = "#ffffff"
    BG_INPUT = "#ffffff"
    SUCCESS = "#27ae60"
    ERROR = "#e74c3c"
    WARNING = "#f39c12"
    TEXT_LIGHT = "#ffffff"
    TEXT_DARK = "#2c3e50"
    TEXT_MED = "#636e72"
    TEXT_MUTED = "#7f8c8d"
    PHASE_BASELINE = "#9b59b6"
    PHASE_ODOR = "#e74c3c"
    PHASE_POST = "#3498db"
    PREVIEW_BG = "#000000"
    LOG_BG = "#1a2744"
    LOG_FG = "#85c1e9"

class DarkTheme(ThemeColors):
    name = "dark"
    NAVY_DARK = "#0d1117"
    NAVY = "#161b22"
    BLUE_ACCENT = "#58a6ff"
    BLUE_LIGHT = "#79c0ff"
    BLUE_PALE = "#a5d6ff"
    GRAY_MID = "#8b949e"
    GRAY_LIGHT = "#30363d"
    GRAY_PALE = "#21262d"
    BG_MAIN = "#0d1117"
    BG_SECTION = "#161b22"
    BG_INPUT = "#21262d"
    SUCCESS = "#3fb950"
    ERROR = "#f85149"
    WARNING = "#d29922"
    TEXT_LIGHT = "#c9d1d9"
    TEXT_DARK = "#c9d1d9"
    TEXT_MED = "#8b949e"
    TEXT_MUTED = "#8b949e"
    PHASE_BASELINE = "#a371f7"
    PHASE_ODOR = "#f85149"
    PHASE_POST = "#58a6ff"
    PREVIEW_BG = "#010409"
    LOG_BG = "#010409"
    LOG_FG = "#7ee787"

# Global theme - will be updated by ThemeManager
Theme = DarkTheme
# =============================================================================
# THEME MANAGER - Handles dynamic theme switching
# =============================================================================
class ThemeManager:
    """Manages theme switching and widget color updates"""
    
    def __init__(self):
        self.registered_widgets = []
        self.current_theme = LightTheme
    
    def register(self, widget, bg_attr=None, fg_attr=None, extra_config=None):
        """Register a widget for theme updates"""
        self.registered_widgets.append({
            'widget': widget,
            'bg_attr': bg_attr,
            'fg_attr': fg_attr,
            'extra': extra_config or {}
        })
    
    def set_theme(self, theme_class):
        """Switch to a new theme and update all registered widgets"""
        global Theme
        Theme = theme_class
        self.current_theme = theme_class
        
        for item in self.registered_widgets:
            try:
                widget = item['widget']
                if not widget.winfo_exists():
                    continue
                
                config = {}
                if item['bg_attr']:
                    config['bg'] = getattr(theme_class, item['bg_attr'])
                if item['fg_attr']:
                    config['fg'] = getattr(theme_class, item['fg_attr'])
                
                # Handle extra config (like activebackground, selectcolor, etc.)
                for key, attr in item['extra'].items():
                    config[key] = getattr(theme_class, attr)
                
                if config:
                    widget.config(**config)
            except Exception:
                pass  # Widget may have been destroyed
        
        # Clean up destroyed widgets
        self.registered_widgets = [
            item for item in self.registered_widgets 
            if item['widget'].winfo_exists()
        ]

# Global theme manager
theme_manager = ThemeManager()

# =============================================================================
# TIMESTAMP LOGGER
# =============================================================================
class TimestampLogger:
    def __init__(self, log_path):
        self.log_path = log_path
        self.lock = threading.Lock()
        self.frame_times = []
        self._buffer = []
        self._flush_every = 200
        with open(log_path, 'w', newline='') as f:
            csv.writer(f).writerow(['timestamp', 'event', 'phase', 'frame', 'odor',
                                    'duration', 'fly', 'geno', 'interval_ms', 'notes'])

    @staticmethod
    def _format_ts(ts):
        if ts is None:
            dt = datetime.now()
        elif isinstance(ts, datetime):
            dt = ts
        elif isinstance(ts, (int, float)):
            dt = datetime.fromtimestamp(ts)
        elif isinstance(ts, str):
            return ts
        else:
            dt = datetime.now()
        return dt.strftime('%Y-%m-%dT%H:%M:%S.') + f'{dt.microsecond // 1000:03d}'

    def log(self, event, phase="", frame=0, odor="", dur=0, fly="", geno="", interval=0, notes="", ts=None):
        ts_str = self._format_ts(ts)
        row = [ts_str, event, phase, frame, odor, dur, fly, geno,
               f"{interval:.2f}" if interval else "", notes]
        with self.lock:
            self._buffer.append(row)
            if interval > 0:
                self.frame_times.append(interval)
            if (not str(event).startswith("FRAME_")) or len(self._buffer) >= self._flush_every:
                self._flush_locked()

    def _flush_locked(self):
        if not self._buffer:
            return
        with open(self.log_path, 'a', newline='') as f:
            csv.writer(f).writerows(self._buffer)
        self._buffer.clear()

    def flush(self):
        with self.lock:
            self._flush_locked()
    
    def get_stats(self):
        if not self.frame_times or not IMAGING_AVAILABLE:
            return {}
        t = np.array(self.frame_times)
        return {'mean': float(np.mean(t)), 'dropped': sum(1 for x in t if x > 150)}

# =============================================================================
# SESSION MANAGER
# =============================================================================
class SessionManager:
    @staticmethod
    def save_metadata(trial_dir, params, stats=None, roi=None):
        meta = {'trial': params, 'stats': stats, 'roi': roi, 'timestamp': datetime.now().isoformat()}
        with open(trial_dir / 'metadata.json', 'w') as f:
            json.dump(meta, f, indent=2)
    
    @staticmethod
    def generate_script(trial_dir):
        script = f'''#!/usr/bin/env python3
"""Analysis script for {trial_dir.name}"""
import json, pandas as pd, numpy as np
from pathlib import Path
from PIL import Image

trial = Path(r"{trial_dir}")
meta = json.load(open(trial / "metadata.json"))
log = pd.read_csv(trial / "log.csv")
images = sorted((trial / "images").glob("*.tif"))
print(f"Loaded {{len(images)}} frames")
'''
        with open(trial_dir / 'analyze.py', 'w') as f:
            f.write(script)

# =============================================================================
# ESP32 CONTROLLER
# =============================================================================
class ESP32Controller:
    """Serial link to ESP32 odor controller.

    Goal: timestamp ODOR ON/OFF based on *ESP32-reported* state changes, not just host-side command times.

    This assumes the ESP32 firmware prints line-oriented status messages when valves change state.
    Supported patterns (case-insensitive):
      - ON:OFM_A / OFF:OFM_A
      - OFM_ON:OFM_A / OFM_OFF:OFM_A
      - ODOR_ON OFM_A / ODOR_OFF OFM_A
    Anything else is still logged as ESP_RX.
    """
    def __init__(self):
        self.serial_port = None
        self.connected = False
        self.port_name = ""
        self._rx_thread = None
        self._rx_stop = threading.Event()
        self._rx_lock = threading.Lock()
        self._rx_queue = queue.Queue(maxsize=2000)  # (ts_iso, line)
        self._last_state = {}  # {odor: {"ON": ts_iso, "OFF": ts_iso, "RAW_ON": line, "RAW_OFF": line}}
        self._logger = None  # optional TimestampLogger

    def attach_logger(self, logger):
        self._logger = logger

    def get_ports(self):
        return [p.device for p in serial.tools.list_ports.comports()] if SERIAL_AVAILABLE else []

    def connect(self, port):
        if not SERIAL_AVAILABLE:
            return False, "pyserial not installed"
        try:
            self.disconnect()
            self.serial_port = serial.Serial(port, BAUD_RATE, timeout=0.2)
            time.sleep(0.5)
            try:
                self.serial_port.reset_input_buffer()
            except Exception:
                pass
            self.port_name = port
            self.connected = True
            self._start_rx_thread()
            return True, f"Connected to {port}"
        except Exception as e:
            return False, str(e)

    def disconnect(self):
        self._stop_rx_thread()
        if self.serial_port and getattr(self.serial_port, "is_open", False):
            try:
                self.serial_port.close()
            except Exception:
                pass
        self.serial_port = None
        self.connected = False
        self.port_name = ""

    def _start_rx_thread(self):
        self._stop_rx_thread()
        if not (self.serial_port and self.connected):
            return

        self._rx_stop.clear()

        def _rx_loop():
            while not self._rx_stop.is_set():
                try:
                    if not (self.serial_port and self.serial_port.is_open):
                        time.sleep(0.05)
                        continue

                    line = self.serial_port.readline()
                    if not line:
                        continue
                    try:
                        s = line.decode("utf-8", errors="ignore").strip()
                    except Exception:
                        s = str(line).strip()

                    if not s:
                        continue

                    ts = datetime.now().strftime('%Y-%m-%dT%H:%M:%S.') + f'{datetime.now().microsecond // 1000:03d}'

                    # Push into queue (drop oldest if full)
                    try:
                        self._rx_queue.put_nowait((ts, s))
                    except queue.Full:
                        try:
                            _ = self._rx_queue.get_nowait()
                        except Exception:
                            pass
                        try:
                            self._rx_queue.put_nowait((ts, s))
                        except Exception:
                            pass

                    # Parse ON/OFF state
                    parsed = self._parse_state_line(s)
                    if parsed:
                        odor, state = parsed
                        with self._rx_lock:
                            self._last_state.setdefault(odor, {})
                            self._last_state[odor][state] = ts
                            self._last_state[odor][f"RAW_{state}"] = s

                        if self._logger:
                            self._logger.log(f"ODOR_{state}_ESP", "ESP32", 0, odor, 0, "", "", 0, s)
                    else:
                        if self._logger:
                            self._logger.log("ESP_RX", "ESP32", 0, "", 0, "", "", 0, s)

                except Exception:
                    time.sleep(0.05)

        self._rx_thread = threading.Thread(target=_rx_loop, daemon=True)
        self._rx_thread.start()

    def _stop_rx_thread(self):
        try:
            self._rx_stop.set()
        except Exception:
            pass
        t = self._rx_thread
        self._rx_thread = None
        if t and t.is_alive():
            try:
                t.join(timeout=0.5)
            except Exception:
                pass

    @staticmethod
    def _parse_state_line(s):
        u = s.strip().upper()
        # Normalize separators
        u = u.replace(",", " ").replace(";", " ").replace("\t", " ").replace("|", " ")
        # Common patterns
        # ON:OFM_A
        m = re.search(r"\b(ON|OFF)\b\s*[:= ]\s*(OFM_[A-Z])\b", u)
        if m:
            return m.group(2), m.group(1)
        # OFM_ON:OFM_A
        m = re.search(r"\bOFM_(ON|OFF)\b\s*[:= ]\s*(OFM_[A-Z])\b", u)
        if m:
            return m.group(2), m.group(1)
        # ODOR_ON OFM_A
        m = re.search(r"\bODOR_(ON|OFF)\b\s+(OFM_[A-Z])\b", u)
        if m:
            return m.group(2), m.group(1)
        return None

    def _write_line(self, s):
        if not (self.connected and self.serial_port):
            return False, "Not connected", {}, {}
        try:
            b = (s.strip() + "\n").encode("utf-8")
            self.serial_port.write(b)
            self.serial_port.flush()
            return True, ""
        except Exception as e:
            return False, str(e)

    def ping(self, timeout=0.8):
        """Best-effort connection sanity check without actuating valves.

        Tries 'PING' then observes whether *any* line comes back (ideally 'PONG').
        If firmware does not implement ping, this still validates that serial writes work.
        """
        ok, err = self._write_line("PING")
        if not ok:
            return False, err

        t0 = time.time()
        seen = []
        while (time.time() - t0) < float(timeout):
            try:
                ts, line = self._rx_queue.get_nowait()
                seen.append(line)
                if "PONG" in line.upper():
                    return True, "PONG"
            except queue.Empty:
                time.sleep(0.02)

        # No response is not necessarily fatal (firmware might be silent)
        return True, ("; ".join(seen)[:200] if seen else "no response")

    def send_odor(self, odor_code, duration_sec):
        if not self.connected or not self.serial_port:
            return False, "Not connected", {}
        # Host-side timestamp for when we *requested* odor
        if self._logger:
            self._logger.log("ODOR_CMD_SENT", "HOST", 0, odor_code, duration_sec, "", "", 0, f"duration={duration_sec}")
        return self._write_line(f"SET_PIN:{odor_code}:{int(duration_sec)}")

    def get_last_state_ts(self, odor_code, state):
        with self._rx_lock:
            d = self._last_state.get(str(odor_code).upper(), {})
            return d.get(str(state).upper())

    def wait_for_state(self, odor_code, state, timeout=1.0, poll=0.02):
        odor_code = str(odor_code).upper()
        state = str(state).upper()
        t0 = time.time()
        while (time.time() - t0) < float(timeout):
            ts = self.get_last_state_ts(odor_code, state)
            if ts:
                return True, ts
            time.sleep(float(poll))
        return False, None

    def send_stop(self):
        # optional; only if firmware supports it
        self._write_line("STOP")
        return True, ""

# =============================================================================
# REMOTE SYNC HANDLER (rsync/rclone)
# =============================================================================
class RemoteSyncHandler:
    """Handle secure syncing to remote server using rsync or rclone."""
    
    def __init__(self):
        self.method = "rclone"  # "rsync" or "rclone"
        self.enabled = True  # Always enabled if rclone is configured
        
    def check_availability(self):
        """Check if rsync or rclone is available on the system."""
        try:
            if self.method == "rclone":
                # Try to run rclone
                result = subprocess.run(
                    ["rclone", "version"], 
                    capture_output=True, 
                    timeout=5,
                    creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
                )
                if result.returncode == 0:
                    # Check if ramanlab remote is configured
                    try:
                        config_check = subprocess.run(
                            ["rclone", "listremotes"],
                            capture_output=True, 
                            timeout=5, 
                            text=True,
                            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
                        )
                        if "ramanlab:" in config_check.stdout:
                            return True, "rclone (ramanlab configured)"
                        else:
                            return False, "rclone installed (needs configuration)"
                    except Exception:
                        return False, "rclone installed (cannot check config)"
                return False, "rclone command failed"
            elif self.method == "rsync":
                result = subprocess.run(
                    ["rsync", "--version"], 
                    capture_output=True, 
                    timeout=5,
                    creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
                )
                return result.returncode == 0, "rsync"
        except FileNotFoundError:
            return False, "rclone not installed or not in PATH"
        except Exception:
            return False, "sync not available"
        return False, "sync method unknown"
    
    def sync_directory(self, local_path, progress_callback=None):
        """
        Sync local directory to remote server (only upload new/changed files).
        
        Args:
            local_path: Path to local directory to sync
            progress_callback: Optional callback(message) for status updates
            
        Returns:
            (success: bool, message: str, stats: dict)
        """
        if not self.enabled:
            return False, "Remote sync not configured", {}
        
        local_path = Path(local_path)
        if not local_path.exists():
            return False, f"Local path does not exist: {local_path}", {}
        
        # Check if tool is available
        available, tool_info = self.check_availability()
        if not available:
            return False, f"Sync tool not available: {tool_info}", {}
        
        try:
            if self.method == "rclone":
                return self._sync_with_rclone(local_path, progress_callback)
            elif self.method == "rsync":
                return self._sync_with_rsync(local_path, progress_callback)
            else:
                return False, f"Unknown sync method: {self.method}", {}
        except Exception as e:
            return False, f"Sync failed: {e}", {}
    
    def _sync_with_rclone(self, local_path, progress_callback=None):
        """Sync using rclone (Windows-friendly, supports many backends)."""
        if progress_callback:
            progress_callback("Using pre-configured rclone remote: ramanlab")
        
        if progress_callback:
            progress_callback(f"Starting rclone sync: {local_path.name}")
        
        # Use pre-configured ramanlab remote pointing to \\10.229.137.184\RamanFiles
        # Files will go to: RamanFiles/cole/imaging/<experiment_folder>
        remote_target = f"ramanlab:RamanFiles/cole/imaging/{local_path.name}"
        
        # Run rclone copy (only uploads new/changed files)
        cmd = [
            "rclone", "copy",
            str(local_path),
            remote_target,
            "--progress",
            "--verbose",
            "--transfers", "4",
            "--checkers", "8"
        ]
        
        if progress_callback:
            progress_callback(f"Running: rclone copy {local_path.name} -> {remote_target}")
        
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )
        
        output_lines = []
        for line in process.stdout:
            output_lines.append(line.strip())
            if progress_callback and line.strip():
                progress_callback(line.strip()[:100])  # Limit line length
        
        process.wait()
        
        if process.returncode == 0:
            stats = {"method": "rclone", "files_synced": len(output_lines)}
            return True, "Sync completed successfully", stats
        else:
            return False, f"rclone failed (code {process.returncode})", {}
    
    def _sync_with_rsync(self, local_path, progress_callback=None):
        """Sync using rsync (requires rsync on Windows, e.g., via Cygwin/WSL)."""
        if progress_callback:
            progress_callback(f"Starting rsync: {local_path.name}")
        
        # Build rsync command
        # -a = archive mode (recursive, preserve permissions, etc.)
        # -v = verbose
        # -z = compress during transfer
        # --progress = show progress
        # --update = skip files that are newer on receiver
        
        remote_target = f"{self.username}@{self.remote_host}/{local_path.name}"
        
        cmd = [
            "rsync",
            "-avz",
            "--progress",
            "--update",  # Only update if source is newer
            str(local_path) + "/",  # Trailing slash = sync contents
            remote_target
        ]
        
        # Set password via environment variable (if supported)
        env = os.environ.copy()
        env["RSYNC_PASSWORD"] = self.password
        
        if progress_callback:
            progress_callback(f"Running: rsync {local_path.name} -> {remote_target}")
        
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env
        )
        
        output_lines = []
        for line in process.stdout:
            output_lines.append(line.strip())
            if progress_callback and line.strip():
                progress_callback(line.strip()[:100])
        
        process.wait()
        
        if process.returncode == 0:
            stats = {"method": "rsync", "output_lines": len(output_lines)}
            return True, "Sync completed successfully", stats
        else:
            return False, f"rsync failed (code {process.returncode})", {}
    
    def _obscure_password(self, password):
        """Obscure password for rclone config (rclone has built-in obscure command)."""
        try:
            result = subprocess.run(
                ["rclone", "obscure", password],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception:
            pass
        # Fallback: return unobscured (less secure but functional)
        return password

# =============================================================================
# MICRO-MANAGER CONTROLLER
# =============================================================================
class MicroManagerController:
    def __init__(self, config_path):
        self.config_path = Path(config_path)
        self.core = None
        self.connected = False
        self.acquisition_running = False
        self.abort_flag = threading.Event()
        self.preview_running = False
        self.preview_callback = None
        self._preview_lock = threading.Lock()
        
        # Camera properties (cached after connect)
        self.camera = ""
        self.exp_min, self.exp_max = 0.1, 5000
        self.gain_min, self.gain_max = 0, 100
        self.has_gain = False
        self.gain_prop = "Gain"
        self.binning_opts = ["1", "2", "4"]
        self.bit_depth = 16
        self.img_w, self.img_h = 512, 512
    
    def connect(self):
        if not PYCROMANAGER_AVAILABLE:
            return False, "pycromanager not installed"
        if not self.config_path.exists():
            return False, f"Config not found: {self.config_path}"
        try:
            self.core = Core()
            self.core.load_system_configuration(str(self.config_path))
            self.connected = True
            self.camera = self.core.get_camera_device()
            self._cache_properties()
            return True, f"Connected: {self.camera}"
        except Exception as e:
            return False, str(e)
    
    def _cache_properties(self):
        """Cache camera property limits for UI controls."""
        try:
            # Exposure limits
            if self.core.has_property_limits(self.camera, "Exposure"):
                self.exp_min = max(0.1, self.core.get_property_lower_limit(self.camera, "Exposure"))
                self.exp_max = min(5000, self.core.get_property_upper_limit(self.camera, "Exposure"))
            
            # Find gain property (varies by camera)
            for prop in ["Gain", "EMGain", "EM Gain", "Analog Gain"]:
                try:
                    if self.core.has_property(self.camera, prop):
                        self.has_gain = True
                        self.gain_prop = prop
                        if self.core.has_property_limits(self.camera, prop):
                            self.gain_min = self.core.get_property_lower_limit(self.camera, prop)
                            self.gain_max = self.core.get_property_upper_limit(self.camera, prop)
                        break
                except:
                    continue
            
            # Binning options
            try:
                allowed = self.core.get_allowed_property_values(self.camera, "Binning")
                if allowed.size() > 0:
                    self.binning_opts = [allowed.get(i) for i in range(allowed.size())]
            except:
                pass
            
            # Image dimensions
            self.img_w = self.core.get_image_width()
            self.img_h = self.core.get_image_height()
            self.bit_depth = self.core.get_image_bit_depth()
        except Exception as e:
            print(f"Property cache error: {e}")
    
    def disconnect(self):
        self.stop_preview()
        self.abort_flag.set()
        self.core = None
        self.connected = False
    
    def get_exposure(self):
        return self.core.get_exposure() if self.connected else DEFAULT_EXPOSURE_MS
    
    def set_exposure(self, ms):
        if self.connected:
            try:
                self.core.set_exposure(float(ms))
                return True
            except:
                pass
        return False
    
    def get_gain(self):
        if self.connected and self.has_gain:
            try:
                return float(self.core.get_property(self.camera, self.gain_prop))
            except:
                pass
        return 0
    
    def set_gain(self, val):
        if self.connected and self.has_gain:
            try:
                self.core.set_property(self.camera, self.gain_prop, str(int(val)))
                return True
            except:
                pass
        return False
    
    def get_binning(self):
        if self.connected:
            try:
                return self.core.get_property(self.camera, "Binning")
            except:
                pass
        return "1"
    
    def set_binning(self, val):
        """Set binning - SAFELY stops preview first"""
        if not self.connected:
            return False
        
        was_previewing = self.preview_running
        if was_previewing:
            self.stop_preview()
            time.sleep(0.1)
        
        try:
            self.core.set_property(self.camera, "Binning", val)
            time.sleep(0.05)  # Small delay for camera to settle
            self.img_w = self.core.get_image_width()
            self.img_h = self.core.get_image_height()
            success = True
        except Exception as e:
            print(f"Binning error: {e}")
            success = False
        
        if was_previewing:
            self.start_preview(self.preview_callback)
        
        return success
    
    def set_camera_roi(self, x, y, w, h):
        """Set camera ROI - SAFELY stops preview first"""
        if not self.connected:
            return False, "Not connected", {}
        
        was_previewing = self.preview_running
        if was_previewing:
            self.stop_preview()
            time.sleep(0.1)
        
        try:
            self.core.set_roi(int(x), int(y), int(w), int(h))
            time.sleep(0.05)
            self.img_w = self.core.get_image_width()
            self.img_h = self.core.get_image_height()
            result = (True, f"ROI: {self.img_w}x{self.img_h}")
        except Exception as e:
            result = (False, str(e))
        
        if was_previewing:
            self.start_preview(self.preview_callback)
        
        return result
    
    def clear_camera_roi(self):
        """Clear camera ROI - SAFELY stops preview first"""
        if not self.connected:
            return False, "Not connected", {}
        
        was_previewing = self.preview_running
        if was_previewing:
            self.stop_preview()
            time.sleep(0.1)
        
        try:
            self.core.clear_roi()
            time.sleep(0.05)
            self.img_w = self.core.get_image_width()
            self.img_h = self.core.get_image_height()
            result = (True, f"Full sensor: {self.img_w}x{self.img_h}")
        except Exception as e:
            result = (False, str(e))
        
        if was_previewing:
            self.start_preview(self.preview_callback)
        
        return result
    
    def auto_exposure(self, callback=None):
        """Percentile-based auto-exposure for fluorescence imaging."""
        if not self.connected:
            return False, "Not connected", {}
        
        # Stop preview during auto-exposure
        was_previewing = self.preview_running
        if was_previewing:
            self.stop_preview()
            time.sleep(0.1)
        
        max_val = (2 ** self.bit_depth) - 1
        target = 0.75 * max_val  # Target 75% of dynamic range
        
        try:
            for i in range(10):
                self.core.snap_image()
                tagged = self.core.get_tagged_image()
                
                arr = self._tagged_to_raw(tagged)
                
                exp = self.core.get_exposure()
                sat_frac = np.sum(arr >= max_val * 0.99) / arr.size
                
                if sat_frac > 0.001:  # >0.1% saturated
                    new_exp = exp * 0.5
                    if callback:
                        callback(f"Iter {i+1}: {sat_frac*100:.2f}% saturated, reducing")
                else:
                    p99 = np.percentile(arr, 99.5)
                    error = abs(p99 - target) / target
                    if error < 0.1:  # Converged
                        if callback:
                            callback(f"Converged at {exp:.1f}ms")
                        if was_previewing:
                            self.start_preview(self.preview_callback)
                        return True, f"Auto-exposure: {exp:.1f}ms"
                    new_exp = exp * (target / max(p99, 1))
                    if callback:
                        callback(f"Iter {i+1}: P99.5={p99:.0f}, target={target:.0f}")
                
                new_exp = float(np.clip(new_exp, self.exp_min, self.exp_max))
                self.core.set_exposure(new_exp)
            
            final = self.core.get_exposure()
            if was_previewing:
                self.start_preview(self.preview_callback)
            return True, f"Auto-exposure: {final:.1f}ms (max iterations)"
            
        except Exception as e:
            if was_previewing:
                self.start_preview(self.preview_callback)
            return False, str(e)
    
    def get_image_stats(self, arr):
        """Calculate image statistics for display."""
        if not IMAGING_AVAILABLE or arr is None:
            return {}
        max_val = (2 ** self.bit_depth) - 1
        return {
            'mean': float(np.mean(arr)),
            'max': float(np.max(arr)),
            'p99': float(np.percentile(arr, 99)),
            'sat': float(np.sum(arr >= max_val * 0.99) / arr.size * 100)
        }
    
    def start_preview(self, callback):
        """Start preview using CONTINUOUS ACQUISITION (much faster than snap)"""
        if not self.connected:
            return False
        
        with self._preview_lock:
            if self.preview_running:
                return True  # Already running
            
            self.preview_callback = callback
            self.preview_running = True
        
        threading.Thread(target=self._preview_loop_continuous, daemon=True).start()
        return True
    
    def stop_preview(self):
        """Stop preview and wait for thread to finish"""
        with self._preview_lock:
            if not self.preview_running:
                return
            self.preview_running = False
        
        # Wait a bit for the thread to stop and camera to be ready
        time.sleep(0.15)
        
        # Make sure sequence acquisition is stopped
        try:
            if self.core and self.core.is_sequence_running():
                self.core.stop_sequence_acquisition()
        except:
            pass
    
    def _preview_loop_continuous(self):
        """Preview loop using continuous sequence acquisition (FASTER)"""
        frame_count = 0
        last_stats = {}
        
        try:
            # Start continuous acquisition
            self.core.start_continuous_sequence_acquisition(0)  # 0 = max speed
            
            while self.preview_running and self.connected and not self.acquisition_running:
                try:
                    # Check if there's an image available
                    if self.core.get_remaining_image_count() > 0:
                        # Use get_last_image for preview (we don't need every frame)
                        tagged = self.core.get_last_tagged_image()
                        
                        img, raw = self._process_image(tagged)
                        
                        if self.preview_callback and img:
                            # Only calculate stats every N frames to reduce overhead
                            if frame_count % STATS_EVERY_N_FRAMES == 0:
                                last_stats = self.get_image_stats(raw)
                            
                            self.preview_callback(img, raw, last_stats)
                        
                        frame_count += 1
                    else:
                        # No image yet, brief sleep
                        time.sleep(0.001)
                        
                except Exception as e:
                    if self.preview_running:
                        time.sleep(0.01)
            
        except Exception as e:
            print(f"Preview error: {e}")
        finally:
            # Always stop sequence acquisition when done
            try:
                if self.core and self.core.is_sequence_running():
                    self.core.stop_sequence_acquisition()
            except:
                pass
    

    def _tagged_to_raw(self, tagged):
        """Return raw image data as a NumPy array WITHOUT modifying pixel values.

        Micro-Manager can return pixel buffers as either a NumPy array or a bytes-like buffer.
        We infer dtype from buffer length when needed to avoid accidental uint16 casting of uint8 data.
        """
        if not IMAGING_AVAILABLE:
            return None
        pix = tagged.pix
        w = int(tagged.tags.get('Width'))
        h = int(tagged.tags.get('Height'))

        if isinstance(pix, np.ndarray):
            arr = pix
            if arr.ndim == 1:
                return arr.reshape(h, w)
            if arr.shape != (h, w):
                return arr.reshape(h, w)
            return arr

        buf = pix  # bytes-like
        try:
            nbytes = len(buf)
        except TypeError:
            # Some implementations provide a buffer interface without len(); fall back to uint16
            return np.frombuffer(buf, dtype=np.uint16)[: w * h].reshape(h, w)

        if nbytes == w * h:
            return np.frombuffer(buf, dtype=np.uint8).reshape(h, w)
        if nbytes == 2 * w * h:
            return np.frombuffer(buf, dtype=np.uint16).reshape(h, w)

        # Fallback: assume 16-bit and crop
        return np.frombuffer(buf, dtype=np.uint16)[: w * h].reshape(h, w)

    def _process_image(self, tagged):
        """Convert raw camera image to 8-bit for DISPLAY ONLY.

        Raw saving uses _tagged_to_raw and preserves pixel values. This method applies a fixed,
        deterministic scale based on the configured bit depth for UI preview.
        """
        if not IMAGING_AVAILABLE:
            return None, None
        try:
            raw = self._tagged_to_raw(tagged)
            if raw is None:
                return None, None

            max_val = (2 ** self.bit_depth) - 1
            scale = 255.0 / max_val if max_val > 0 else 1.0
            img8 = (raw.astype(np.float32) * scale).clip(0, 255).astype(np.uint8)
            img = Image.fromarray(img8)
            return img, raw
        except Exception:
            return None, None

    @staticmethod
    def _extract_elapsed_ms(tags):
        if not tags:
            return None
        for key in ("ElapsedTime-ms", "ElapsedTime_ms", "ElapsedTime", "ElapsedTimeMs"):
            try:
                if key in tags:
                    return float(tags[key])
            except Exception:
                continue
        return None

    def acquire_multiphase(self, fps, base_sec, odor_sec, post_sec, save_dir, logger,
                          odor, fly, geno, esp32, prog_cb, phase_cb, save_video=False, frame_cb=None):
        """
        High-speed acquisition using Micro-Manager's sequence acquisition.
        
        Key optimization: Let the CAMERA handle timing internally via sequence acquisition.
        We grab frames in batches, not one-at-a-time with Python timing.
        """
        if not self.connected:
            return False, "Not connected", {}
        
        was_previewing = self.preview_running
        self.stop_preview()
        self.abort_flag.clear()
        self.acquisition_running = True
        
        b_frames = int(base_sec * fps)
        o_frames = int(odor_sec * fps)
        p_frames = int(post_sec * fps)
        total = b_frames + o_frames + p_frames
        
        # All frames stored here
        all_frames = []
        all_times = []
        dropped = 0

        # Metrics / metadata returned to caller
        odor_frame_start = None
        odor_frame_end = None
        esp_odor_on_ts = None
        esp_odor_off_ts = None
        camera_pixel_type = ""
        try:
            camera_pixel_type = self.core.get_property(self.camera, "PixelType")
        except Exception:
            pass

        metrics = {
            "fps_target": float(fps),
            "fps_measured": None,
            "frames_captured": 0,
            "frames_saved": 0,
            "dropped_frames": 0,
            "saturation_pct": None,
            "mean_intensity": None,
            "dtype": "",
            "pixel_type": camera_pixel_type,
            "odor_frame_start": None,
            "odor_frame_end": None,
            "odor_on_ts_esp": None,
            "odor_off_ts_esp": None,
        }
        
        try:
            img_dir = save_dir / "images"
            img_dir.mkdir(parents=True, exist_ok=True)
            
            # Get actual exposure time - camera will pace based on this
            exposure_ms = self.core.get_exposure()
            # Use the FPS setting to determine frame interval (camera will be limited by exposure if it's longer)
            acq_interval_ms = int(round(1000.0 / max(float(fps), 0.001)))
            
            # Debug logging
            logger.log("ACQ_TIMING", "CONFIG", 0, odor, odor_sec, fly, geno, 0, 
                      f"fps={fps} exp={exposure_ms}ms acq_interval={acq_interval_ms}ms")
            
            if total <= 0:
                self.acquisition_running = False
                return False, "No frames to acquire", {}

            phases = []
            start = 0
            for name, count in [("BASELINE", b_frames), ("ODOR", o_frames), ("POST-ODOR", p_frames)]:
                if count > 0:
                    phases.append({"name": name, "start": start, "end": start + count})
                start += count

            odor_frame_start = b_frames if o_frames > 0 else None
            odor_frame_end = b_frames + o_frames if o_frames > 0 else None
            metrics["odor_frame_start"] = odor_frame_start
            metrics["odor_frame_end"] = odor_frame_end

            global_frame = 0
            last_capture_time = None
            odor_cmd_sent = False

            def send_odor_now(frame_idx):
                nonlocal odor_cmd_sent, esp_odor_on_ts
                if odor_cmd_sent:
                    return
                if esp32 and esp32.connected:
                    try:
                        esp32.attach_logger(logger)
                    except Exception:
                        pass

                    ok_cmd, err = esp32.send_odor(odor, int(round(odor_sec)))
                    logger.log("ODOR_CMD", "ODOR", frame_idx, odor, odor_sec, fly, geno, 0,
                               "" if ok_cmd else f"ESP_ERR:{err}")
                    
                    # Also turn on OFM_P (carrier) for the same duration
                    ok_p, err_p = esp32.send_odor(OFM_P_PIN, int(round(odor_sec)))
                    logger.log("ODOR_CMD", "CARRIER", frame_idx, OFM_P_PIN, odor_sec, fly, geno, 0,
                               "" if ok_p else f"ESP_ERR:{err_p}")

                    ts_on = None
                    try:
                        if hasattr(esp32, "get_last_state_ts"):
                            ts_on = esp32.get_last_state_ts(odor, "ON")
                    except Exception:
                        ts_on = None

                    if ts_on:
                        esp_odor_on_ts = ts_on
                        metrics["odor_on_ts_esp"] = ts_on
                        logger.log("ODOR_ON_TS", "ODOR", frame_idx, odor, odor_sec, fly, geno, 0, ts_on)
                    else:
                        ts_host = datetime.now().strftime('%Y-%m-%dT%H:%M:%S.') + f'{datetime.now().microsecond // 1000:03d}'
                        metrics["odor_on_ts_esp"] = None
                        logger.log("ODOR_ON_NOESP", "ODOR", frame_idx, odor, odor_sec, fly, geno, 0, ts_host)
                else:
                    logger.log("ODOR_CMD_SKIPPED", "ODOR", frame_idx, odor, odor_sec, fly, geno)

                odor_cmd_sent = True

            # === CONTINUOUS ACQUISITION WITH SOFTWARE FRAME PACING ===
            # Many camera drivers ignore the interval parameter in
            # start_sequence_acquisition and run at max hardware speed.
            # We use continuous acquisition and only keep frames at the
            # target FPS, draining excess frames to prevent buffer overflow.
            try:
                self.core.clear_circular_buffer()
            except Exception:
                pass

            target_interval_s = 1.0 / max(float(fps), 0.001)
            total_duration_s = float(base_sec + odor_sec + post_sec)

            self.core.start_continuous_sequence_acquisition(0)
            acq_start_wall = time.time()
            last_gui_update = time.time()
            next_frame_wall = acq_start_wall  # wall-clock time for next kept frame

            phase_idx = 0
            current_phase = phases[0]["name"] if phases else ""
            if current_phase:
                if phase_cb:
                    phase_cb(current_phase)
                logger.log("PHASE_START", current_phase, global_frame, odor, odor_sec, fly, geno)

            if odor_frame_start == 0:
                send_odor_now(0)

            # Collect frames with software pacing
            while global_frame < total:
                if self.abort_flag.is_set():
                    self.core.stop_sequence_acquisition()
                    raise InterruptedError("Aborted")

                now = time.time()
                available = self.core.get_remaining_image_count()

                if available > 0 and now >= next_frame_wall:
                    try:
                        # Grab the most recent frame, drain extras
                        tagged = self.core.pop_next_tagged_image()
                        while self.core.get_remaining_image_count() > 0:
                            tagged = self.core.pop_next_tagged_image()

                        raw = self._tagged_to_raw(tagged).copy()
                        capture_time = now
                        frame_interval_ms = (capture_time - last_capture_time) * 1000 if last_capture_time else 0
                        last_capture_time = capture_time
                        frame_idx = global_frame

                        # Phase transitions
                        while phase_idx + 1 < len(phases) and frame_idx >= phases[phase_idx]["end"]:
                            phase_idx += 1
                            current_phase = phases[phase_idx]["name"]
                            if phase_cb:
                                phase_cb(current_phase)
                            logger.log("PHASE_START", current_phase, frame_idx, odor, odor_sec, fly, geno)

                        # Odor command
                        if (not odor_cmd_sent) and (odor_frame_start is not None) and odor_frame_start > 0:
                            if frame_idx >= (odor_frame_start - 1):
                                send_odor_now(frame_idx)

                        all_frames.append(raw)
                        all_times.append(capture_time)
                        global_frame += 1

                        # Schedule next frame based on ideal wall-clock time (prevents drift)
                        next_frame_wall = acq_start_wall + (global_frame * target_interval_s)

                        logger.log(f"FRAME_{frame_idx}", current_phase, frame_idx, odor, odor_sec, fly, geno,
                                   frame_interval_ms, ts=capture_time)
                    except Exception:
                        dropped += 1

                    # GUI update (throttled)
                    if now - last_gui_update > 0.2:
                        last_gui_update = now
                        if prog_cb:
                            prog_cb(global_frame, total, current_phase)
                        if frame_cb and len(all_frames) > 0:
                            display_raw = all_frames[-1]
                            max_val = (2 ** self.bit_depth) - 1
                            img8 = (display_raw * (255.0 / max_val)).astype(np.uint8)
                            frame_cb(Image.fromarray(img8), display_raw)

                elif available > 1:
                    # Camera running ahead of target FPS — drain excess to prevent buffer overflow
                    while self.core.get_remaining_image_count() > 1:
                        try:
                            self.core.pop_next_tagged_image()
                        except Exception:
                            break
                    time.sleep(0.001)
                else:
                    time.sleep(0.001)

                # Safety timeout (wall-clock based)
                if now - acq_start_wall > total_duration_s + 10:
                    print(f"Acquisition timeout: got {global_frame}/{total}")
                    break

            try:
                self.core.stop_sequence_acquisition()
            except Exception:
                pass
            
            # Calculate actual FPS from wall-clock timestamps
            if len(all_times) > 1:
                total_acq_time = all_times[-1] - all_times[0]
                actual_fps = (len(all_frames) - 1) / total_acq_time if total_acq_time > 0 else 0
            else:
                actual_fps = 0
            
            # === SAVE PHASE ===
            if phase_cb:
                phase_cb("SAVING")
            
            for idx, raw_frame in enumerate(all_frames):
                if self.abort_flag.is_set():
                    break

                # Save RAW frame (no scaling / no contrast changes)
                frame_path = img_dir / f"frame_{idx:05d}.tif"
                if raw_frame.dtype == np.uint8:
                    Image.fromarray(raw_frame, mode='L').save(frame_path)
                else:
                    # Save as 16-bit TIFF (fixed deprecation warning)
                    Image.fromarray(raw_frame.astype(np.uint16)).save(frame_path, format='TIFF')

                
                if idx % 20 == 0 and prog_cb:
                    prog_cb(idx, len(all_frames), "SAVING")
            
            # Optional video
            if save_video and VIDEO_AVAILABLE and len(all_frames) > 0:
                h, w = all_frames[0].shape
                fourcc = cv2.VideoWriter_fourcc(*'XVID')
                vw = cv2.VideoWriter(str(save_dir / "recording.avi"), fourcc, fps, (w, h), False)
                for rf in all_frames:
                    vw.write((rf.astype(np.float32) * (255.0 / max(1, ((2 ** self.bit_depth) - 1)))).clip(0, 255).astype(np.uint8))
                vw.release()
            
            logger.log("COMPLETE", "", global_frame, odor, odor_sec, fly, geno,
                      notes=f"Dropped: {dropped}, FPS: {actual_fps:.1f}")
            
            self.acquisition_running = False
            if was_previewing and self.preview_callback:
                self.start_preview(self.preview_callback)
            

            # Final metrics
            metrics["frames_captured"] = int(global_frame)
            metrics["dropped_frames"] = int(dropped)
            metrics["dtype"] = str(all_frames[0].dtype) if all_frames else ""
            metrics["fps_measured"] = float(actual_fps) if actual_fps else None

            # Saturation / mean intensity (quick)
            try:
                if all_frames:
                    sample = all_frames[min(len(all_frames)-1, max(0, int(len(all_frames)*0.5)))]
                    metrics["mean_intensity"] = float(np.mean(sample))
                    maxv = float(np.iinfo(sample.dtype).max) if np.issubdtype(sample.dtype, np.integer) else float(np.max(sample))
                    sat = np.mean(sample >= (0.98 * maxv)) if maxv > 0 else 0.0
                    metrics["saturation_pct"] = float(sat * 100.0)
            except Exception:
                pass

            # ESP32-reported OFF timestamp (if present)
            try:
                if esp32 and esp32.connected and hasattr(esp32, "get_last_state_ts"):
                    ts_off = esp32.get_last_state_ts(odor, "OFF")
                    if ts_off:
                        esp_odor_off_ts = ts_off
                        metrics["odor_off_ts_esp"] = ts_off
                        logger.log("ODOR_OFF_TS", "ESP32", global_frame, odor, odor_sec, fly, geno, 0, ts_off)
            except Exception:
                pass

            metrics["frames_saved"] = int(len(all_frames))

            all_frames.clear()
            all_times.clear()
            try:
                logger.flush()
            except Exception:
                pass

            return True, f"Acquired {global_frame} frames @ {actual_fps:.1f} FPS (dropped: {dropped})", metrics
            
        except InterruptedError as e:
            self.acquisition_running = False
            try:
                self.core.stop_sequence_acquisition()
            except:
                pass
            try:
                logger.flush()
            except Exception:
                pass
            return False, str(e), {}
        except Exception as e:
            self.acquisition_running = False
            try:
                self.core.stop_sequence_acquisition()
            except:
                pass
            try:
                logger.flush()
            except Exception:
                pass
            return False, str(e), {}
    
    def warmup_capture(self, n_frames=50, fps=10.0):
        """Capture a short burst to estimate achieved FPS and basic signal health.
        Does not save frames or actuate odors.
        """
        if not self.connected:
            return False, {"error": "Micro-Manager not connected"}

        n_frames = int(max(1, n_frames))
        fps = float(max(0.1, fps))
        interval_ms = int(round(1000.0 / fps))

        frames = []
        dropped = 0
        t0 = None
        t1 = None

        try:
            self.core.clear_circular_buffer()
            self.core.start_sequence_acquisition(n_frames, interval_ms, True)

            for _ in range(n_frames):
                t_wait0 = time.time()
                while self.core.get_remaining_image_count() == 0:
                    if (time.time() - t_wait0) > 2.0:
                        dropped += 1
                        break
                    time.sleep(0.001)

                if self.core.get_remaining_image_count() > 0:
                    tagged = self.core.pop_next_tagged_image()
                    arr = np.reshape(tagged.pix, (tagged.tags['Height'], tagged.tags['Width']))
                    if t0 is None:
                        t0 = time.time()
                    t1 = time.time()
                    frames.append(arr)

            try:
                self.core.stop_sequence_acquisition()
            except Exception:
                pass

            elapsed = (t1 - t0) if (t0 is not None and t1 is not None and t1 > t0) else None
            fps_measured = (len(frames) / elapsed) if (elapsed and elapsed > 0) else None

            sat_pct = None
            mean_int = None
            if frames:
                sample = frames[-1]
                mean_int = float(np.mean(sample))
                try:
                    maxv = float(np.iinfo(sample.dtype).max)
                    sat_pct = float(np.mean(sample >= (0.98 * maxv)) * 100.0)
                except Exception:
                    pass

            return True, {
                "frames": len(frames),
                "dropped_frames": dropped,
                "fps_target": fps,
                "fps_measured": fps_measured,
                "mean_intensity": mean_int,
                "saturation_pct": sat_pct,
                "dtype": str(frames[-1].dtype) if frames else "",
            }

        except Exception as e:
            try:
                self.core.stop_sequence_acquisition()
            except Exception:
                pass
            return False, {"error": str(e)}

    def _snap_single(self):
        """Internal snap without preview management"""
        try:
            self.core.snap_image()
            return self._process_image(self.core.get_tagged_image())
        except:
            return None, None
    
    def abort(self):
        self.abort_flag.set()

# =============================================================================
# LIVE PREVIEW PANEL
# =============================================================================
class LivePreviewPanel(tk.Frame):
    def __init__(self, parent, on_roi_change=None):
        super().__init__(parent, bg=Theme.NAVY_DARK)
        
        self.on_roi_change = on_roi_change
        self.photo = None
        self.current_roi = None
        self.drawing_roi = False
        self.roi_start = None
        self.roi_enabled = False
        self.show_crosshair = False
        self.display_scale = 1.0
        self.img_offset = (0, 0)
        self.img_width, self.img_height = 512, 512
        
        self._build_ui()
        self.frame_times = []
    
    def _build_ui(self):
        # Header
        self.header = tk.Frame(self, bg=Theme.NAVY_DARK)
        self.header.pack(fill=tk.X, padx=10, pady=5)
        
        self.title_label = tk.Label(self.header, text="📷 LIVE PREVIEW", font=('Segoe UI', 11, 'bold'),
                                    bg=Theme.NAVY_DARK, fg=Theme.TEXT_LIGHT)
        self.title_label.pack(side=tk.LEFT)
        
        self.status_label = tk.Label(self.header, text="Stopped", font=('Segoe UI', 9),
                                     bg=Theme.NAVY_DARK, fg=Theme.TEXT_MUTED)
        self.status_label.pack(side=tk.RIGHT)
        
        # Canvas - fills available space
        self.canvas_frame = tk.Frame(self, bg=Theme.PREVIEW_BG)
        self.canvas_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        self.canvas = tk.Canvas(self.canvas_frame, bg=Theme.PREVIEW_BG,
                               highlightthickness=1, highlightbackground=Theme.NAVY)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        
        self.canvas.bind('<Button-1>', self._on_mouse_down)
        self.canvas.bind('<B1-Motion>', self._on_mouse_drag)
        self.canvas.bind('<ButtonRelease-1>', self._on_mouse_up)
        
        # Phase indicator
        self.phase_label = tk.Label(self, text="", font=('Segoe UI', 12, 'bold'),
                                    bg=Theme.NAVY_DARK, fg=Theme.TEXT_LIGHT)
        self.phase_label.pack(fill=tk.X, padx=10)
        
        # Stats bar
        self.stats_frame = tk.Frame(self, bg=Theme.NAVY_DARK)
        self.stats_frame.pack(fill=tk.X, padx=10, pady=5)
        
        self.stats_label = tk.Label(self.stats_frame, text="Mean: -- | Max: -- | P99: -- | Sat: --%",
                                    font=('Consolas', 9), bg=Theme.NAVY_DARK, fg=Theme.BLUE_PALE)
        self.stats_label.pack(side=tk.LEFT)
        
        self.fps_label = tk.Label(self.stats_frame, text="-- FPS", font=('Segoe UI', 9),
                                  bg=Theme.NAVY_DARK, fg=Theme.TEXT_MUTED)
        self.fps_label.pack(side=tk.RIGHT)
        
        # Histogram
        self.hist_frame = tk.Frame(self, bg=Theme.NAVY_DARK)
        self.hist_frame.pack(fill=tk.X, padx=10, pady=(0, 10))
        
        self.hist_title = tk.Label(self.hist_frame, text="Histogram", font=('Segoe UI', 8),
                                   bg=Theme.NAVY_DARK, fg=Theme.TEXT_MUTED)
        self.hist_title.pack(anchor='w')
        
        self.hist_canvas = tk.Canvas(self.hist_frame, height=50, bg=Theme.PREVIEW_BG, highlightthickness=0)
        self.hist_canvas.pack(fill=tk.X)
        
        # Register for theme updates
        theme_manager.register(self, 'NAVY_DARK')
        theme_manager.register(self.header, 'NAVY_DARK')
        theme_manager.register(self.title_label, 'NAVY_DARK', 'TEXT_LIGHT')
        theme_manager.register(self.status_label, 'NAVY_DARK', 'TEXT_MUTED')
        theme_manager.register(self.canvas_frame, 'PREVIEW_BG')
        theme_manager.register(self.canvas, 'PREVIEW_BG')
        theme_manager.register(self.phase_label, 'NAVY_DARK', 'TEXT_LIGHT')
        theme_manager.register(self.stats_frame, 'NAVY_DARK')
        theme_manager.register(self.stats_label, 'NAVY_DARK', 'BLUE_PALE')
        theme_manager.register(self.fps_label, 'NAVY_DARK', 'TEXT_MUTED')
        theme_manager.register(self.hist_frame, 'NAVY_DARK')
        theme_manager.register(self.hist_title, 'NAVY_DARK', 'TEXT_MUTED')
        theme_manager.register(self.hist_canvas, 'PREVIEW_BG')
    
    def update_image(self, pil_image, raw_image=None, stats=None):
        if pil_image is None:
            return
        
        try:
            canvas_w = self.canvas.winfo_width()
            canvas_h = self.canvas.winfo_height()
            
            if canvas_w < 10 or canvas_h < 10:
                return
            
            self.img_width = pil_image.width
            self.img_height = pil_image.height
            
            # Scale to fit canvas
            scale_w = canvas_w / pil_image.width
            scale_h = canvas_h / pil_image.height
            self.display_scale = min(scale_w, scale_h)
            
            new_w = int(pil_image.width * self.display_scale)
            new_h = int(pil_image.height * self.display_scale)
            
            # Use BILINEAR for speed (LANCZOS is too slow for live preview)
            img_resized = pil_image.resize((new_w, new_h), Image.Resampling.BILINEAR)
            
            # Draw overlays
            if self.current_roi or self.show_crosshair:
                img_resized = img_resized.convert('RGB')
                draw = ImageDraw.Draw(img_resized)
                
                if self.current_roi:
                    x, y, w, h = self.current_roi
                    rx = int(x * self.display_scale)
                    ry = int(y * self.display_scale)
                    rw = int(w * self.display_scale)
                    rh = int(h * self.display_scale)
                    draw.rectangle([rx, ry, rx + rw, ry + rh], outline='#00ff00', width=2)
                
                if self.show_crosshair:
                    cx, cy = new_w // 2, new_h // 2
                    draw.line([(cx - 20, cy), (cx + 20, cy)], fill='#ff0000', width=1)
                    draw.line([(cx, cy - 20), (cx, cy + 20)], fill='#ff0000', width=1)
            
            self.photo = ImageTk.PhotoImage(img_resized)
            
            x_offset = (canvas_w - new_w) // 2
            y_offset = (canvas_h - new_h) // 2
            self.img_offset = (x_offset, y_offset)
            
            self.canvas.delete("all")
            self.canvas.create_image(x_offset, y_offset, anchor=tk.NW, image=self.photo)
            
            # Update histogram (only if we have raw image)
            if raw_image is not None and IMAGING_AVAILABLE:
                self._update_histogram(raw_image)
            
            # Update stats display
            if stats:
                sat_color = Theme.ERROR if stats.get('sat', 0) > 0.1 else Theme.BLUE_PALE
                self.stats_label.config(
                    text=f"Mean: {stats.get('mean', 0):.0f} | Max: {stats.get('max', 0):.0f} | "
                         f"P99: {stats.get('p99', 0):.0f} | Sat: {stats.get('sat', 0):.2f}%",
                    fg=sat_color
                )
            
            # Update FPS counter
            now = time.time()
            self.frame_times.append(now)
            self.frame_times = [t for t in self.frame_times if now - t < 1.0]
            self.fps_label.config(text=f"{len(self.frame_times)} FPS")
            
        except Exception as e:
            pass  # Silently ignore preview errors

    def clear_image(self):
        try:
            self.canvas.delete("all")
            self.hist_canvas.delete("all")
            self.stats_label.config(text="Mean: -- | Max: -- | P99: -- | Sat: --%", fg=Theme.BLUE_PALE)
            self.fps_label.config(text="-- FPS")
        except Exception:
            pass
    
    def _update_histogram(self, raw_image):
        try:
            hist_w = self.hist_canvas.winfo_width()
            hist_h = 50
            if hist_w < 10:
                return
            
            # Subsample for speed
            subsample = raw_image[::4, ::4].flatten()
            hist, _ = np.histogram(subsample, bins=256)
            hist = np.log1p(hist.astype(float))  # Log scale
            hist = hist / hist.max() if hist.max() > 0 else hist
            
            self.hist_canvas.delete("all")
            bin_width = hist_w / 256
            for i, h_val in enumerate(hist):
                x = i * bin_width
                bar_h = int(h_val * (hist_h - 5))
                if bar_h > 0:
                    self.hist_canvas.create_line(x, hist_h, x, hist_h - bar_h,
                                                 fill=Theme.BLUE_ACCENT, width=max(1, bin_width))
        except:
            pass
    
    def _on_mouse_down(self, event):
        if self.roi_enabled:
            self.drawing_roi = True
            self.roi_start = (event.x, event.y)
    
    def _on_mouse_drag(self, event):
        if self.drawing_roi and self.roi_start:
            self.canvas.delete("roi_temp")
            self.canvas.create_rectangle(self.roi_start[0], self.roi_start[1], event.x, event.y,
                                         outline='#00ff00', width=2, tags="roi_temp")
    
    def _on_mouse_up(self, event):
        if not self.drawing_roi or not self.roi_start:
            return
        
        self.drawing_roi = False
        self.canvas.delete("roi_temp")
        
        ox, oy = self.img_offset
        x1 = (min(self.roi_start[0], event.x) - ox) / self.display_scale
        y1 = (min(self.roi_start[1], event.y) - oy) / self.display_scale
        x2 = (max(self.roi_start[0], event.x) - ox) / self.display_scale
        y2 = (max(self.roi_start[1], event.y) - oy) / self.display_scale
        
        x1 = max(0, min(x1, self.img_width))
        y1 = max(0, min(y1, self.img_height))
        x2 = max(0, min(x2, self.img_width))
        y2 = max(0, min(y2, self.img_height))
        
        w, h = x2 - x1, y2 - y1
        if w > 5 and h > 5:
            self.current_roi = (int(x1), int(y1), int(w), int(h))
            if self.on_roi_change:
                self.on_roi_change(self.current_roi)
    
    def clear_roi(self):
        self.current_roi = None
        if self.on_roi_change:
            self.on_roi_change(None)
    
    def set_phase(self, phase):
        colors = {
            "BASELINE": (Theme.PHASE_BASELINE, "⏱ BASELINE"),
            "ODOR": (Theme.PHASE_ODOR, "🧪 ODOR DELIVERY"),
            "POST-ODOR": (Theme.PHASE_POST, "📊 POST-ODOR"),
            "SAVING": (Theme.WARNING, "💾 SAVING TO DISK..."),
            "": (Theme.NAVY_DARK, "")
        }
        color, text = colors.get(phase, (Theme.NAVY_DARK, phase))
        self.phase_label.config(text=text, bg=color)
    
    def set_status(self, text, color=None):
        self.status_label.config(text=text, fg=color or Theme.TEXT_MUTED)

# =============================================================================
# ITI TIMER
# =============================================================================
class ITITimer:
    def __init__(self, duration, on_tick=None, on_complete=None):
        self.duration = duration
        self.remaining = duration
        self.on_tick = on_tick
        self.on_complete = on_complete
        self.running = False
    
    def start(self):
        self.remaining = self.duration
        self.running = True
        threading.Thread(target=self._run, daemon=True).start()
    
    def stop(self):
        self.running = False
    
    def _run(self):
        while self.running and self.remaining > 0:
            if self.on_tick:
                self.on_tick(self.remaining)
            time.sleep(1)
            self.remaining -= 1
        if self.running and self.on_complete:
            self.on_complete()
        self.running = False

# =============================================================================
# MAIN GUI
# =============================================================================
class OdorRecorderGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("SNNE Lab - Drosophila Olfactory Recording v10.0")
        self.root.state('zoomed')
        self.root.minsize(1200, 700)
        
        self.esp32 = ESP32Controller()
        self.mm = None
        self.remote_sync = RemoteSyncHandler()  # Remote sync handler
        
        self.recording = False
        self.session_dir = None
        self.logger = None
        self.preview_active = False
        self.current_roi = None
        self.iti_timer = None
        self.last_params = None

        # Protocol runner state
        self.protocol_running = False
        self.protocol_abort = threading.Event()
        self.protocol_abort_flag = False
        self._proto_replay_stop = threading.Event()
        self._proto_replay_thread = None
        self._proto_replay_running = False
        
        # Track widgets for theme updates
        self.themed_widgets = []
        
        self._create_ui()
        
        # Create default save directory and log info
        try:
            default_save_path = Path(DEFAULT_SAVE_DIR)
            default_save_path.mkdir(parents=True, exist_ok=True)
            self._log(f"Default save location: {DEFAULT_SAVE_DIR}", "SUCCESS")
        except Exception as e:
            self._log(f"Could not create default save dir: {e}", "WARNING")
        
        # Default to dark mode
        try:
            self._toggle_dark(silent=True)
        except TypeError:
            # Backward compatibility if silent arg is not present
            self._toggle_dark()
        self._bind_shortcuts()
        self._update_status()
        
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
    
    def _bind_shortcuts(self):
        # Space no longer starts recording. It is reserved for protocol progression gating.
        self.root.bind('<space>', lambda e: self._on_space_pressed())

        # Escape stops recording OR aborts an active protocol run
        self.root.bind('<Escape>', lambda e: self._stop_recording() if (self.recording or self.protocol_running) else None)

        self.root.bind('<F11>', lambda e: self._toggle_fullscreen())
        self.root.bind('<F5>', lambda e: self._quick_repeat())

    
    def _on_space_pressed(self):
        """SPACE is used ONLY to advance a protocol when the protocol runner is waiting for user confirmation."""
        try:
            if not self.protocol_running:
                return
            ev = getattr(self, "_proto_continue_event", None)
            if ev is None:
                return
            if getattr(self, "_proto_space_allowed", False):
                ev.set()
                self._close_proto_popup()
        except Exception:
            pass

    def _close_proto_popup(self):
        """Close the protocol inter-trial popup if it exists."""
        try:
            popup = getattr(self, "_proto_popup", None)
            if popup is not None and popup.winfo_exists():
                try:
                    popup.grab_release()
                except Exception:
                    pass
                popup.destroy()
        except Exception:
            pass
        finally:
            self._proto_popup = None
            self._proto_space_allowed = False
            self._proto_continue_event = None

    def _protocol_pause_popup(self, seconds, context_text="Next trial", on_ready=None):
        """Show a modal countdown popup instructing the user to turn OFF shutter, then require SPACE to continue."""
        try:
            seconds = int(round(max(0, float(seconds))))
        except Exception:
            seconds = 0

        ev = threading.Event()
        self._proto_continue_event = ev
        self._proto_space_allowed = (seconds == 0)
        self._proto_popup = None

        def _show():
            if self.protocol_abort.is_set():
                ev.set()
                return

            popup = tk.Toplevel(self.root)
            self._proto_popup = popup
            popup.title("Protocol: shutter / light")
            popup.configure(bg=Theme.BG_MAIN)
            try:
                popup.transient(self.root)
                popup.grab_set()
            except Exception:
                pass

            popup.protocol("WM_DELETE_WINDOW", lambda: None)
            
            # Bind space key to popup so it works even when popup has focus/grab
            popup.bind('<space>', lambda e: self._on_space_pressed())

            frm = tk.Frame(popup, bg=Theme.BG_MAIN, padx=16, pady=14)
            frm.pack(fill=tk.BOTH, expand=True)
            theme_manager.register(frm, 'BG_MAIN')

            header = tk.Label(frm, text="Turn OFF shutter now", font=("Segoe UI", 12, "bold"),
                              bg=Theme.BG_MAIN, fg=Theme.NAVY_DARK)
            header.pack(anchor="w")
            theme_manager.register(header, 'BG_MAIN', 'NAVY_DARK')

            if seconds <= 0:
                header.config(text="Turn ON shutter and check focus")

            msg = tk.Label(frm, text="", font=("Segoe UI", 10),
                           bg=Theme.BG_MAIN, fg=Theme.TEXT_DARK, justify=tk.LEFT)
            msg.pack(anchor="w", pady=(8, 0))
            theme_manager.register(msg, 'BG_MAIN', 'TEXT_DARK')

            countdown = tk.Label(frm, text="", font=("Segoe UI", 18, "bold"),
                                 bg=Theme.BG_MAIN, fg=Theme.NAVY_DARK)
            countdown.pack(anchor="w", pady=(10, 0))
            theme_manager.register(countdown, 'BG_MAIN', 'NAVY_DARK')

            hint = tk.Label(frm, text="After the countdown, turn the light back ON and press SPACE to continue.",
                            font=("Segoe UI", 9), bg=Theme.BG_MAIN, fg=Theme.TEXT_DARK)
            hint.pack(anchor="w", pady=(10, 0))
            theme_manager.register(hint, 'BG_MAIN', 'TEXT_DARK')

            remaining = {"t": seconds}

            def tick():
                if self.protocol_abort.is_set():
                    try:
                        popup.destroy()
                    except Exception:
                        pass
                    ev.set()
                    return

                t = remaining["t"]
                if t > 0:
                    msg.config(text=f"{context_text} begins in:")
                    countdown.config(text=f"{t:02d} s")
                    remaining["t"] = t - 1
                    popup.after(1000, tick)
                else:
                    msg.config(text=f"{context_text} is ready. Press SPACE to continue.")
                    countdown.config(text="00 s")
                    if callable(on_ready):
                        try:
                            on_ready()
                        except Exception:
                            pass
                    self._proto_space_allowed = True
                    header.config(text="Turn ON shutter and check focus, then press SPACE")

            tick()

        self.root.after(0, _show)

        # Wait for event (this is called from protocol worker thread, not main thread)
        # The event will be set by space key handler running on main thread
        while not ev.is_set():
            if self.protocol_abort.is_set():
                self.root.after(0, self._close_proto_popup)
                return False
            time.sleep(0.05)

        # Close popup from main thread
        self.root.after(0, self._close_proto_popup)
        return not self.protocol_abort.is_set()

    def _apply_replay_view(self, raw):
        if raw is None or not IMAGING_AVAILABLE:
            return None
        settings = REPLAY_VIEW_SETTINGS
        try:
            arr = raw.astype(np.float32)
        except Exception:
            return None

        method = str(settings.get("method", "percentile")).lower()
        if method == "percentile":
            try:
                p_lo = float(settings.get("p_lo", 1))
                p_hi = float(settings.get("p_hi", 99))
                lo = np.percentile(arr, p_lo)
                hi = np.percentile(arr, p_hi)
            except Exception:
                lo = float(np.min(arr))
                hi = float(np.max(arr))
        else:
            lo = float(np.min(arr))
            hi = float(np.max(arr))

        if not np.isfinite(lo):
            lo = 0.0
        if not np.isfinite(hi) or hi <= lo:
            hi = lo + 1.0

        arr = (arr - lo) / (hi - lo)
        arr = np.clip(arr, 0.0, 1.0)

        gamma = settings.get("gamma", 1.0)
        try:
            gamma = float(gamma)
        except Exception:
            gamma = 1.0
        if gamma > 0 and gamma != 1.0:
            arr = np.power(arr, 1.0 / gamma)

        # CLAHE intentionally disabled for v10
        img8 = (arr * 255.0).clip(0, 255).astype(np.uint8)
        return Image.fromarray(img8)

    def _find_replay_frames_dir(self, trial_dir):
        try:
            trial_dir = Path(trial_dir)
        except Exception:
            return None
        candidates = [
            trial_dir / "images" / "images",
            trial_dir / "images",
            trial_dir,
        ]
        for c in candidates:
            if c.exists():
                if any(c.glob("frame_*.tif")) or any(c.glob("*.tif")):
                    return c
        return None

    def _protocol_replay_loop(self, frames_dir, fps, stop_event):
        if not IMAGING_AVAILABLE:
            return
        try:
            frames_dir = Path(frames_dir)
        except Exception:
            return

        files = sorted(frames_dir.glob("frame_*.tif"))
        if not files:
            files = sorted(frames_dir.glob("*.tif"))
        if not files:
            return

        try:
            fps = float(fps) if fps else float(DEFAULT_FPS)
        except Exception:
            fps = float(DEFAULT_FPS)
        fps = max(0.1, fps)
        delay = 1.0 / fps

        while not stop_event.is_set() and self.protocol_running:
            for fp in files:
                if stop_event.is_set() or not self.protocol_running:
                    break
                try:
                    img = Image.open(fp)
                    raw = np.array(img)
                    img.close()
                except Exception:
                    continue

                disp = self._apply_replay_view(raw)
                if disp is None:
                    continue
                stats = self.mm.get_image_stats(raw) if self.mm else {}
                if stop_event.is_set() or not self.protocol_running:
                    break
                self.root.after(0, lambda d=disp, r=raw, s=stats: self.preview_panel.update_image(d, r, s))

                t0 = time.time()
                while (time.time() - t0) < delay:
                    if stop_event.is_set() or not self.protocol_running:
                        break
                    time.sleep(0.01)

    def _start_protocol_replay(self, trial_dir, fps):
        if not IMAGING_AVAILABLE:
            return
        frames_dir = self._find_replay_frames_dir(trial_dir)
        if not frames_dir:
            return
        self._stop_protocol_replay(clear=False)
        stop_event = threading.Event()
        self._proto_replay_stop = stop_event
        self._proto_replay_running = True
        try:
            self.root.after(0, lambda: self.preview_panel.set_status("Replay (protocol)", Theme.BLUE_ACCENT))
        except Exception:
            pass
        t = threading.Thread(target=self._protocol_replay_loop, args=(frames_dir, fps, stop_event), daemon=True)
        self._proto_replay_thread = t
        t.start()

    def _stop_protocol_replay(self, clear=False):
        try:
            if self._proto_replay_stop:
                self._proto_replay_stop.set()
        except Exception:
            pass
        self._proto_replay_running = False
        if clear:
            try:
                self.root.after(0, lambda: self.preview_panel.clear_image())
                self.root.after(0, lambda: self.preview_panel.set_status("Stopped", Theme.TEXT_MUTED))
            except Exception:
                pass

    def _toggle_fullscreen(self):
        self.root.state('normal' if self.root.state() == 'zoomed' else 'zoomed')
    
    def _create_ui(self):
        self.root.configure(bg=Theme.BG_MAIN)
        theme_manager.register(self.root, 'BG_MAIN')
        
        # PanedWindow for proper fullscreen resize
        self.paned = tk.PanedWindow(self.root, orient=tk.HORIZONTAL, bg=Theme.BG_MAIN,
                                     sashwidth=6, sashrelief=tk.RAISED)
        self.paned.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        theme_manager.register(self.paned, 'BG_MAIN')
        
        # LEFT: Preview panel (expands to fill)
        self.left_panel = tk.Frame(self.paned, bg=Theme.NAVY_DARK)
        self.paned.add(self.left_panel, stretch="always", minsize=400)
        theme_manager.register(self.left_panel, 'NAVY_DARK')
        
        self.preview_panel = LivePreviewPanel(self.left_panel, on_roi_change=self._on_roi_change)
        self.preview_panel.pack(fill=tk.BOTH, expand=True)
        
        # Preview controls
        self.prev_ctrl = tk.Frame(self.left_panel, bg=Theme.NAVY_DARK, pady=8)
        self.prev_ctrl.pack(fill=tk.X)
        theme_manager.register(self.prev_ctrl, 'NAVY_DARK')
        
        self.preview_btn = self._btn(self.prev_ctrl, "▶ Start Preview", self._toggle_preview)
        self.preview_btn.pack(side=tk.LEFT, padx=8)
        self._btn(self.prev_ctrl, "📸", self._take_snapshot, "secondary").pack(side=tk.LEFT, padx=2)
        self.roi_btn = self._btn(self.prev_ctrl, "🔲 ROI", self._toggle_roi, "secondary")
        self.roi_btn.pack(side=tk.LEFT, padx=2)
        self._btn(self.prev_ctrl, "✕", self._clear_roi, "secondary").pack(side=tk.LEFT, padx=2)
        
        self.crosshair_var = tk.BooleanVar(value=False)
        self.crosshair_cb = tk.Checkbutton(self.prev_ctrl, text="⊕", variable=self.crosshair_var,
                                           command=self._toggle_crosshair, bg=Theme.NAVY_DARK,
                                           fg=Theme.TEXT_LIGHT, selectcolor=Theme.NAVY,
                                           activebackground=Theme.NAVY_DARK,
                                           font=('Segoe UI', 10))
        self.crosshair_cb.pack(side=tk.LEFT, padx=5)
        theme_manager.register(self.crosshair_cb, 'NAVY_DARK', 'TEXT_LIGHT', 
                              {'selectcolor': 'NAVY', 'activebackground': 'NAVY_DARK'})
        
        # RIGHT: Controls panel (fixed width)
        # RIGHT: Controls panel (fixed width)
        self.right_panel = tk.Frame(self.paned, bg=Theme.BG_MAIN)
        self.paned.add(self.right_panel, stretch="never", minsize=420, width=480)
        theme_manager.register(self.right_panel, 'BG_MAIN')

        # Tabs: Run / Protocols
        self.tabs = ttk.Notebook(self.right_panel)
        self.tabs.pack(fill=tk.BOTH, expand=True)

        self.run_tab = tk.Frame(self.tabs, bg=Theme.BG_MAIN)
        self.protocol_tab = tk.Frame(self.tabs, bg=Theme.BG_MAIN)
        theme_manager.register(self.run_tab, 'BG_MAIN')
        theme_manager.register(self.protocol_tab, 'BG_MAIN')

        self.tabs.add(self.run_tab, text="Run")
        self.tabs.add(self.protocol_tab, text="Protocols")

        # --- Run tab: scrollable controls ---
        self.scroll_canvas = tk.Canvas(self.run_tab, bg=Theme.BG_MAIN, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self.run_tab, orient="vertical", command=self.scroll_canvas.yview)
        self.scrollable = tk.Frame(self.scroll_canvas, bg=Theme.BG_MAIN)
        theme_manager.register(self.scroll_canvas, 'BG_MAIN')
        theme_manager.register(self.scrollable, 'BG_MAIN')

        self.scrollable.bind(
            "<Configure>",
            lambda e: self.scroll_canvas.configure(scrollregion=self.scroll_canvas.bbox("all"))
        )
        self.scroll_canvas.create_window((0, 0), window=self.scrollable, anchor="nw", tags="win")
        self.scroll_canvas.configure(yscrollcommand=scrollbar.set)

        def resize_scroll(event):
            self.scroll_canvas.itemconfig("win", width=event.width)
        self.scroll_canvas.bind("<Configure>", resize_scroll)

        scrollbar.pack(side="right", fill="y")
        self.scroll_canvas.pack(side="left", fill="both", expand=True)

        # Wheel scroll (Windows/macOS)
        self.scroll_canvas.bind_all(
            "<MouseWheel>",
            lambda e: self.scroll_canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")
        )

        # Build Run tab sections
        self._create_header()
        self._create_connection_section()
        self._create_camera_section()
        self._create_experiment_section()
        self._create_timing_section()
        self._create_acquisition_section()
        self._create_controls_section()
        self._create_progress_section()
        self._create_log_section()

        # Build Protocols tab
        self._create_protocols_tab()

        self._refresh_ports()

    def _btn(self, parent, text, cmd, style="primary"):
        colors = {
            "primary": (Theme.BLUE_ACCENT, Theme.BLUE_LIGHT, 'BLUE_ACCENT', 'BLUE_LIGHT'),
            "success": (Theme.SUCCESS, "#2ecc71", 'SUCCESS', 'SUCCESS'),
            "danger": (Theme.ERROR, "#c0392b", 'ERROR', 'ERROR'),
            "secondary": (Theme.GRAY_MID, "#4a5568", 'GRAY_MID', 'GRAY_MID'),
        }
        bg, hover, bg_attr, hover_attr = colors.get(style, colors["primary"])
        btn = tk.Button(parent, text=text, command=cmd, font=('Segoe UI', 9, 'bold'),
                       bg=bg, fg=Theme.TEXT_LIGHT, activebackground=hover,
                       relief=tk.FLAT, cursor='hand2', padx=8, pady=4)
        
        # Store style info for theme updates
        btn._style_info = (bg_attr, hover_attr)
        
        def on_enter(e):
            btn.config(bg=getattr(Theme, btn._style_info[1]))
        def on_leave(e):
            btn.config(bg=getattr(Theme, btn._style_info[0]))
        
        btn.bind('<Enter>', on_enter)
        btn.bind('<Leave>', on_leave)
        
        theme_manager.register(btn, bg_attr, 'TEXT_LIGHT')
        return btn
    
    def _section(self, title):
        container = tk.Frame(self.scrollable, bg=Theme.BG_MAIN)
        container.pack(fill=tk.X, pady=(0, 5), padx=3)
        theme_manager.register(container, 'BG_MAIN')
        
        header = tk.Label(container, text=title, font=('Segoe UI', 10, 'bold'),
                         bg=Theme.NAVY_DARK, fg=Theme.TEXT_LIGHT, pady=4, padx=8, anchor='w')
        header.pack(fill=tk.X)
        theme_manager.register(header, 'NAVY_DARK', 'TEXT_LIGHT')
        
        content = tk.Frame(container, bg=Theme.BG_SECTION, padx=8, pady=6)
        content.pack(fill=tk.X)
        theme_manager.register(content, 'BG_SECTION')
        
        return content
    
    def _create_header(self):
        header = tk.Frame(self.scrollable, bg=Theme.NAVY_DARK, pady=6, padx=8)
        header.pack(fill=tk.X, pady=(0, 5), padx=3)
        theme_manager.register(header, 'NAVY_DARK')
        
        row = tk.Frame(header, bg=Theme.NAVY_DARK)
        row.pack(fill=tk.X)
        theme_manager.register(row, 'NAVY_DARK')
        
        title = tk.Label(row, text="SNNE Lab", font=('Segoe UI', 12, 'bold'),
                        bg=Theme.NAVY_DARK, fg=Theme.TEXT_LIGHT)
        title.pack(side=tk.LEFT)
        theme_manager.register(title, 'NAVY_DARK', 'TEXT_LIGHT')
        
        self.dark_var = tk.BooleanVar(value=True)
        self.dark_cb = tk.Checkbutton(row, text="🌙", variable=self.dark_var, command=self._toggle_dark,
                                      bg=Theme.NAVY_DARK, fg=Theme.TEXT_LIGHT, selectcolor=Theme.NAVY,
                                      activebackground=Theme.NAVY_DARK, font=('Segoe UI', 10))
        self.dark_cb.pack(side=tk.RIGHT)
        theme_manager.register(self.dark_cb, 'NAVY_DARK', 'TEXT_LIGHT',
                              {'selectcolor': 'NAVY', 'activebackground': 'NAVY_DARK'})
        
        subtitle = tk.Label(header, text="🪰 v10.0 | Space=Next Trial | Esc=Stop | F5=Repeat",
                           font=('Segoe UI', 8), bg=Theme.NAVY_DARK, fg=Theme.BLUE_PALE)
        subtitle.pack(anchor='w')
        theme_manager.register(subtitle, 'NAVY_DARK', 'BLUE_PALE')
    
    def _create_connection_section(self):
        sec = self._section("📡 CONNECTION")
        
        # ESP32
        r1 = tk.Frame(sec, bg=Theme.BG_SECTION)
        r1.pack(fill=tk.X, pady=2)
        theme_manager.register(r1, 'BG_SECTION')
        
        lbl1 = tk.Label(r1, text="ESP32:", font=('Segoe UI', 9, 'bold'), width=9, anchor='w',
                       bg=Theme.BG_SECTION, fg=Theme.TEXT_DARK)
        lbl1.pack(side=tk.LEFT)
        theme_manager.register(lbl1, 'BG_SECTION', 'TEXT_DARK')
        
        self.esp32_ind = tk.Canvas(r1, width=12, height=12, bg=Theme.BG_SECTION, highlightthickness=0)
        self.esp32_ind.pack(side=tk.LEFT, padx=(0, 5))
        self.esp32_ind.create_oval(1, 1, 11, 11, fill=Theme.ERROR, tags="ind")
        theme_manager.register(self.esp32_ind, 'BG_SECTION')
        
        self.esp32_lbl = tk.Label(r1, text="--", font=('Segoe UI', 8),
                                  bg=Theme.BG_SECTION, fg=Theme.TEXT_MUTED)
        self.esp32_lbl.pack(side=tk.LEFT)
        theme_manager.register(self.esp32_lbl, 'BG_SECTION', 'TEXT_MUTED')
        
        r2 = tk.Frame(sec, bg=Theme.BG_SECTION)
        r2.pack(fill=tk.X, pady=2)
        theme_manager.register(r2, 'BG_SECTION')
        
        self.port_var = tk.StringVar(value=DEFAULT_COM_PORT)
        self.port_combo = ttk.Combobox(r2, textvariable=self.port_var, width=8)
        self.port_combo.pack(side=tk.LEFT, padx=(0, 3))
        
        self._btn(r2, "↻", self._refresh_ports, "secondary").pack(side=tk.LEFT, padx=1)
        self.esp32_btn = self._btn(r2, "Connect", self._connect_esp32)
        self.esp32_btn.pack(side=tk.LEFT, padx=1)
        
        # MM
        r3 = tk.Frame(sec, bg=Theme.BG_SECTION)
        r3.pack(fill=tk.X, pady=(6, 2))
        theme_manager.register(r3, 'BG_SECTION')
        
        lbl2 = tk.Label(r3, text="Camera:", font=('Segoe UI', 9, 'bold'), width=9, anchor='w',
                       bg=Theme.BG_SECTION, fg=Theme.TEXT_DARK)
        lbl2.pack(side=tk.LEFT)
        theme_manager.register(lbl2, 'BG_SECTION', 'TEXT_DARK')
        
        self.mm_ind = tk.Canvas(r3, width=12, height=12, bg=Theme.BG_SECTION, highlightthickness=0)
        self.mm_ind.pack(side=tk.LEFT, padx=(0, 5))
        self.mm_ind.create_oval(1, 1, 11, 11, fill=Theme.ERROR, tags="ind")
        theme_manager.register(self.mm_ind, 'BG_SECTION')
        
        self.mm_lbl = tk.Label(r3, text="--", font=('Segoe UI', 8),
                               bg=Theme.BG_SECTION, fg=Theme.TEXT_MUTED)
        self.mm_lbl.pack(side=tk.LEFT)
        theme_manager.register(self.mm_lbl, 'BG_SECTION', 'TEXT_MUTED')
        
        r4 = tk.Frame(sec, bg=Theme.BG_SECTION)
        r4.pack(fill=tk.X, pady=2)
        theme_manager.register(r4, 'BG_SECTION')
        
        self.config_var = tk.StringVar(value=DEFAULT_CONFIG_FILE)
        self.config_entry = tk.Entry(r4, textvariable=self.config_var, width=22, font=('Segoe UI', 8),
                                     relief=tk.SOLID, bd=1, bg=Theme.BG_INPUT, fg=Theme.TEXT_DARK)
        self.config_entry.pack(side=tk.LEFT, padx=(0, 3))
        theme_manager.register(self.config_entry, 'BG_INPUT', 'TEXT_DARK')
        
        self._btn(r4, "...", self._browse_config, "secondary").pack(side=tk.LEFT, padx=1)
        self.mm_btn = self._btn(r4, "Connect", self._connect_mm)
        self.mm_btn.pack(side=tk.LEFT, padx=1)
        
        # Disk
        self.disk_lbl = tk.Label(sec, text="💾 --", font=('Segoe UI', 8),
                                 bg=Theme.BG_SECTION, fg=Theme.TEXT_MUTED)
        self.disk_lbl.pack(anchor='w', pady=(3, 0))
        theme_manager.register(self.disk_lbl, 'BG_SECTION', 'TEXT_MUTED')
    
    def _create_camera_section(self):
        sec = self._section("📷 CAMERA SETUP")
        
        # Exposure entry with FPS display
        r1 = tk.Frame(sec, bg=Theme.BG_SECTION)
        r1.pack(fill=tk.X, pady=2)
        theme_manager.register(r1, 'BG_SECTION')
        
        lbl1 = tk.Label(r1, text="Exposure:", font=('Segoe UI', 9), width=8, anchor='w',
                       bg=Theme.BG_SECTION, fg=Theme.TEXT_DARK)
        lbl1.pack(side=tk.LEFT)
        theme_manager.register(lbl1, 'BG_SECTION', 'TEXT_DARK')
        
        # Exposure entry field
        self.exp_var = tk.StringVar(value=str(DEFAULT_EXPOSURE_MS))
        self.exp_entry = tk.Entry(r1, textvariable=self.exp_var, width=7, font=('Segoe UI', 9, 'bold'),
                                  relief=tk.SOLID, bd=1, bg=Theme.BG_INPUT, fg=Theme.TEXT_DARK)
        self.exp_entry.pack(side=tk.LEFT, padx=(0, 2))
        theme_manager.register(self.exp_entry, 'BG_INPUT', 'TEXT_DARK')
        
        lbl_ms = tk.Label(r1, text="ms", font=('Segoe UI', 9), bg=Theme.BG_SECTION, fg=Theme.TEXT_MUTED)
        lbl_ms.pack(side=tk.LEFT, padx=(0, 8))
        theme_manager.register(lbl_ms, 'BG_SECTION', 'TEXT_MUTED')
        
        # Set button to apply exposure
        self.set_exp_btn = self._btn(r1, "Set", self._apply_exposure, "primary")
        self.set_exp_btn.pack(side=tk.LEFT, padx=2)
        
        # Auto-exposure button (one-click)
        self.auto_btn = self._btn(r1, "⚡Auto", self._run_auto_exp, "success")
        self.auto_btn.pack(side=tk.LEFT, padx=2)
        
        # FPS indicator
        self.fps_indicator = tk.Label(r1, text="→ 100 FPS max", font=('Segoe UI', 9, 'bold'),
                                      bg=Theme.BG_SECTION, fg=Theme.BLUE_ACCENT)
        self.fps_indicator.pack(side=tk.LEFT, padx=(8, 0))
        theme_manager.register(self.fps_indicator, 'BG_SECTION', 'BLUE_ACCENT')
        
        # Bind Enter key to apply exposure
        self.exp_entry.bind('<Return>', lambda e: self._apply_exposure())
        self.exp_var.trace_add('write', self._update_fps_indicator)
        
        # Gain slider
        r2 = tk.Frame(sec, bg=Theme.BG_SECTION)
        r2.pack(fill=tk.X, pady=2)
        theme_manager.register(r2, 'BG_SECTION')
        
        lbl2 = tk.Label(r2, text="Gain:", font=('Segoe UI', 9), width=8, anchor='w',
                       bg=Theme.BG_SECTION, fg=Theme.TEXT_DARK)
        lbl2.pack(side=tk.LEFT)
        theme_manager.register(lbl2, 'BG_SECTION', 'TEXT_DARK')
        
        self.gain_slider = tk.Scale(r2, from_=0, to=100, orient=tk.HORIZONTAL,
                                    resolution=1, length=140, bg=Theme.BG_SECTION,
                                    highlightthickness=0, troughcolor=Theme.GRAY_LIGHT,
                                    fg=Theme.TEXT_DARK, command=self._on_gain_change)
        self.gain_slider.set(0)
        self.gain_slider.pack(side=tk.LEFT, padx=3)
        theme_manager.register(self.gain_slider, 'BG_SECTION', 'TEXT_DARK', {'troughcolor': 'GRAY_LIGHT'})
        
        self.gain_lbl = tk.Label(r2, text="0", font=('Segoe UI', 9, 'bold'),
                                 width=9, bg=Theme.BG_SECTION, fg=Theme.NAVY)
        self.gain_lbl.pack(side=tk.LEFT)
        theme_manager.register(self.gain_lbl, 'BG_SECTION', 'NAVY')
        
        # Binning
        r3 = tk.Frame(sec, bg=Theme.BG_SECTION)
        r3.pack(fill=tk.X, pady=2)
        theme_manager.register(r3, 'BG_SECTION')
        
        lbl3 = tk.Label(r3, text="Binning:", font=('Segoe UI', 9), width=8, anchor='w',
                       bg=Theme.BG_SECTION, fg=Theme.TEXT_DARK)
        lbl3.pack(side=tk.LEFT)
        theme_manager.register(lbl3, 'BG_SECTION', 'TEXT_DARK')
        
        self.bin_var = tk.StringVar(value="1")
        self.bin_combo = ttk.Combobox(r3, textvariable=self.bin_var, values=["1", "2", "4"],
                                      width=4, state='readonly')
        self.bin_combo.pack(side=tk.LEFT, padx=3)
        self.bin_combo.bind('<<ComboboxSelected>>', self._on_bin_change)
        
        self.res_lbl = tk.Label(r3, text="", font=('Segoe UI', 8),
                                bg=Theme.BG_SECTION, fg=Theme.TEXT_MUTED)
        self.res_lbl.pack(side=tk.LEFT, padx=8)
        theme_manager.register(self.res_lbl, 'BG_SECTION', 'TEXT_MUTED')
        
        # Camera ROI
        r4 = tk.Frame(sec, bg=Theme.BG_SECTION)
        r4.pack(fill=tk.X, pady=2)
        theme_manager.register(r4, 'BG_SECTION')
        
        lbl4 = tk.Label(r4, text="Cam ROI:", font=('Segoe UI', 9), width=8, anchor='w',
                       bg=Theme.BG_SECTION, fg=Theme.TEXT_DARK)
        lbl4.pack(side=tk.LEFT)
        theme_manager.register(lbl4, 'BG_SECTION', 'TEXT_DARK')
        
        self._btn(r4, "Apply ROI", self._apply_cam_roi, "secondary").pack(side=tk.LEFT, padx=2)
        self._btn(r4, "Full", self._clear_cam_roi, "secondary").pack(side=tk.LEFT, padx=2)
        
        self.cam_roi_lbl = tk.Label(r4, text="", font=('Segoe UI', 8),
                                    bg=Theme.BG_SECTION, fg=Theme.TEXT_MUTED)
        self.cam_roi_lbl.pack(side=tk.LEFT, padx=8)
        theme_manager.register(self.cam_roi_lbl, 'BG_SECTION', 'TEXT_MUTED')
        
        # Auto-exp status
        self.auto_status = tk.Label(sec, text="", font=('Segoe UI', 8),
                                    bg=Theme.BG_SECTION, fg=Theme.TEXT_MUTED)
        self.auto_status.pack(anchor='w', pady=(3, 0))
        theme_manager.register(self.auto_status, 'BG_SECTION', 'TEXT_MUTED')
    
    def _create_experiment_section(self):
        sec = self._section("🧬 EXPERIMENT")
        
        r1 = tk.Frame(sec, bg=Theme.BG_SECTION)
        r1.pack(fill=tk.X, pady=2)
        theme_manager.register(r1, 'BG_SECTION')
        
        lbl1 = tk.Label(r1, text="Fly:", font=('Segoe UI', 9), bg=Theme.BG_SECTION, fg=Theme.TEXT_DARK)
        lbl1.pack(side=tk.LEFT)
        theme_manager.register(lbl1, 'BG_SECTION', 'TEXT_DARK')
        
        self.fly_var = tk.StringVar()
        self.fly_entry = tk.Entry(r1, textvariable=self.fly_var, width=7, font=('Segoe UI', 9),
                                  relief=tk.SOLID, bd=1, bg=Theme.BG_INPUT, fg=Theme.TEXT_DARK)
        self.fly_entry.pack(side=tk.LEFT, padx=(2, 8))
        theme_manager.register(self.fly_entry, 'BG_INPUT', 'TEXT_DARK')
        
        lbl2 = tk.Label(r1, text="Geno:", font=('Segoe UI', 9), bg=Theme.BG_SECTION, fg=Theme.TEXT_DARK)
        lbl2.pack(side=tk.LEFT)
        theme_manager.register(lbl2, 'BG_SECTION', 'TEXT_DARK')
        
        self.geno_var = tk.StringVar(value="w1118")
        self.geno_entry = tk.Entry(r1, textvariable=self.geno_var, width=8, font=('Segoe UI', 9),
                                   relief=tk.SOLID, bd=1, bg=Theme.BG_INPUT, fg=Theme.TEXT_DARK)
        self.geno_entry.pack(side=tk.LEFT, padx=(2, 8))
        theme_manager.register(self.geno_entry, 'BG_INPUT', 'TEXT_DARK')
        
        lbl3 = tk.Label(r1, text="Trial:", font=('Segoe UI', 9), bg=Theme.BG_SECTION, fg=Theme.TEXT_DARK)
        lbl3.pack(side=tk.LEFT)
        theme_manager.register(lbl3, 'BG_SECTION', 'TEXT_DARK')
        
        self.trial_var = tk.StringVar(value="1")
        self.trial_entry = tk.Entry(r1, textvariable=self.trial_var, width=3, font=('Segoe UI', 9, 'bold'),
                                    relief=tk.SOLID, bd=1, bg=Theme.BG_INPUT, fg=Theme.TEXT_DARK)
        self.trial_entry.pack(side=tk.LEFT, padx=2)
        theme_manager.register(self.trial_entry, 'BG_INPUT', 'TEXT_DARK')
        
        r2 = tk.Frame(sec, bg=Theme.BG_SECTION)
        r2.pack(fill=tk.X, pady=2)
        theme_manager.register(r2, 'BG_SECTION')
        
        lbl4 = tk.Label(r2, text="Notes:", font=('Segoe UI', 9), bg=Theme.BG_SECTION, fg=Theme.TEXT_DARK)
        lbl4.pack(side=tk.LEFT)
        theme_manager.register(lbl4, 'BG_SECTION', 'TEXT_DARK')
        
        self.notes_var = tk.StringVar()
        self.notes_entry = tk.Entry(r2, textvariable=self.notes_var, font=('Segoe UI', 9),
                                    relief=tk.SOLID, bd=1, bg=Theme.BG_INPUT, fg=Theme.TEXT_DARK)
        self.notes_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)
        theme_manager.register(self.notes_entry, 'BG_INPUT', 'TEXT_DARK')
    
    def _create_timing_section(self):
        sec = self._section("⏱ PHASES")
        
        # Timeline
        timeline = tk.Frame(sec, bg=Theme.BG_SECTION)
        timeline.pack(fill=tk.X, pady=(0, 5))
        theme_manager.register(timeline, 'BG_SECTION')
        
        self.phase_boxes = []
        for color_attr, txt in [('PHASE_BASELINE', "BASE"), ('PHASE_ODOR', "ODOR"), ('PHASE_POST', "POST")]:
            color = getattr(Theme, color_attr)
            box = tk.Frame(timeline, bg=color, padx=5, pady=2)
            box.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=1)
            lbl = tk.Label(box, text=txt, font=('Segoe UI', 8, 'bold'), bg=color, fg=Theme.TEXT_LIGHT)
            lbl.pack()
            self.phase_boxes.append((box, lbl, color_attr))
            theme_manager.register(box, color_attr)
            theme_manager.register(lbl, color_attr, 'TEXT_LIGHT')
        
        # Timing inputs
        r1 = tk.Frame(sec, bg=Theme.BG_SECTION)
        r1.pack(fill=tk.X, pady=2)
        theme_manager.register(r1, 'BG_SECTION')
        
        for lbl_text, attr, default, color_attr in [("Base:", "base_var", DEFAULT_BASELINE, 'PHASE_BASELINE'),
                                                     ("Odor:", "odor_dur_var", DEFAULT_ODOR, 'PHASE_ODOR'),
                                                     ("Post:", "post_var", DEFAULT_POST, 'PHASE_POST')]:
            color = getattr(Theme, color_attr)
            lbl = tk.Label(r1, text=lbl_text, font=('Segoe UI', 9), bg=Theme.BG_SECTION, fg=color)
            lbl.pack(side=tk.LEFT)
            theme_manager.register(lbl, 'BG_SECTION', color_attr)
            
            var = tk.StringVar(value=str(default))
            setattr(self, attr, var)
            entry = tk.Entry(r1, textvariable=var, width=3, font=('Segoe UI', 9),
                            relief=tk.SOLID, bd=1, bg=Theme.BG_INPUT, fg=Theme.TEXT_DARK)
            entry.pack(side=tk.LEFT, padx=(2, 6))
            theme_manager.register(entry, 'BG_INPUT', 'TEXT_DARK')
            var.trace_add('write', self._update_total)
        
        lbl_s = tk.Label(r1, text="s", font=('Segoe UI', 8), bg=Theme.BG_SECTION, fg=Theme.TEXT_MUTED)
        lbl_s.pack(side=tk.LEFT)
        theme_manager.register(lbl_s, 'BG_SECTION', 'TEXT_MUTED')
        
        self.total_lbl = tk.Label(r1, text="= 35s", font=('Segoe UI', 9, 'bold'),
                                  bg=Theme.BG_SECTION, fg=Theme.NAVY)
        self.total_lbl.pack(side=tk.RIGHT)
        theme_manager.register(self.total_lbl, 'BG_SECTION', 'NAVY')
        
        # ITI
        r2 = tk.Frame(sec, bg=Theme.BG_SECTION)
        r2.pack(fill=tk.X, pady=2)
        theme_manager.register(r2, 'BG_SECTION')
        
        lbl_iti = tk.Label(r2, text="ITI:", font=('Segoe UI', 9), bg=Theme.BG_SECTION, fg=Theme.TEXT_DARK)
        lbl_iti.pack(side=tk.LEFT)
        theme_manager.register(lbl_iti, 'BG_SECTION', 'TEXT_DARK')
        
        self.iti_var = tk.StringVar(value=str(DEFAULT_ITI))
        self.iti_entry = tk.Entry(r2, textvariable=self.iti_var, width=3, font=('Segoe UI', 9),
                                  relief=tk.SOLID, bd=1, bg=Theme.BG_INPUT, fg=Theme.TEXT_DARK)
        self.iti_entry.pack(side=tk.LEFT, padx=(2, 2))
        theme_manager.register(self.iti_entry, 'BG_INPUT', 'TEXT_DARK')
        
        lbl_s2 = tk.Label(r2, text="s", font=('Segoe UI', 8), bg=Theme.BG_SECTION, fg=Theme.TEXT_MUTED)
        lbl_s2.pack(side=tk.LEFT)
        theme_manager.register(lbl_s2, 'BG_SECTION', 'TEXT_MUTED')
        
        self.iti_enabled = tk.BooleanVar(value=True)
        self.iti_cb = tk.Checkbutton(r2, text="enabled", variable=self.iti_enabled, font=('Segoe UI', 8),
                                     bg=Theme.BG_SECTION, fg=Theme.TEXT_DARK, selectcolor=Theme.BG_INPUT,
                                     activebackground=Theme.BG_SECTION)
        self.iti_cb.pack(side=tk.LEFT, padx=5)
        theme_manager.register(self.iti_cb, 'BG_SECTION', 'TEXT_DARK',
                              {'selectcolor': 'BG_INPUT', 'activebackground': 'BG_SECTION'})
    
    def _update_total(self, *args):
        try:
            total = float(self.base_var.get() or 0) + float(self.odor_dur_var.get() or 0) + float(self.post_var.get() or 0)
            self.total_lbl.config(text=f"= {total:.0f}s")
        except:
            pass
    
    def _create_acquisition_section(self):
        sec = self._section("⚙️ ACQUISITION")
        
        r1 = tk.Frame(sec, bg=Theme.BG_SECTION)
        r1.pack(fill=tk.X, pady=2)
        theme_manager.register(r1, 'BG_SECTION')
        
        lbl1 = tk.Label(r1, text="FPS:", font=('Segoe UI', 9), bg=Theme.BG_SECTION, fg=Theme.TEXT_DARK)
        lbl1.pack(side=tk.LEFT)
        theme_manager.register(lbl1, 'BG_SECTION', 'TEXT_DARK')
        
        self.fps_var = tk.StringVar(value=str(DEFAULT_FPS))
        self.fps_entry = tk.Entry(r1, textvariable=self.fps_var, width=4, font=('Segoe UI', 9),
                                  relief=tk.SOLID, bd=1, bg=Theme.BG_INPUT, fg=Theme.TEXT_DARK)
        self.fps_entry.pack(side=tk.LEFT, padx=(2, 10))
        theme_manager.register(self.fps_entry, 'BG_INPUT', 'TEXT_DARK')
        
        lbl2 = tk.Label(r1, text="Odor:", font=('Segoe UI', 9), bg=Theme.BG_SECTION, fg=Theme.TEXT_DARK)
        lbl2.pack(side=tk.LEFT)
        theme_manager.register(lbl2, 'BG_SECTION', 'TEXT_DARK')
        
        self.odor_var = tk.StringVar(value=ODOR_OPTIONS[0])
        self.odor_combo = ttk.Combobox(r1, textvariable=self.odor_var, values=ODOR_OPTIONS, width=7,
                                       state='readonly')
        self.odor_combo.pack(side=tk.LEFT, padx=2)
        
        self.video_var = tk.BooleanVar(value=False)
        self.video_cb = tk.Checkbutton(r1, text="Video", variable=self.video_var, font=('Segoe UI', 8),
                                       bg=Theme.BG_SECTION, fg=Theme.TEXT_DARK, selectcolor=Theme.BG_INPUT,
                                       activebackground=Theme.BG_SECTION)
        self.video_cb.pack(side=tk.LEFT, padx=8)
        theme_manager.register(self.video_cb, 'BG_SECTION', 'TEXT_DARK',
                              {'selectcolor': 'BG_INPUT', 'activebackground': 'BG_SECTION'})
        
        r2 = tk.Frame(sec, bg=Theme.BG_SECTION)
        r2.pack(fill=tk.X, pady=2)
        theme_manager.register(r2, 'BG_SECTION')
        
        lbl3 = tk.Label(r2, text="Save:", font=('Segoe UI', 9), bg=Theme.BG_SECTION, fg=Theme.TEXT_DARK)
        lbl3.pack(side=tk.LEFT)
        theme_manager.register(lbl3, 'BG_SECTION', 'TEXT_DARK')
        
        self.save_var = tk.StringVar(value=DEFAULT_SAVE_DIR)
        self.save_entry = tk.Entry(r2, textvariable=self.save_var, width=22, font=('Segoe UI', 8),
                                   relief=tk.SOLID, bd=1, bg=Theme.BG_INPUT, fg=Theme.TEXT_DARK)
        self.save_entry.pack(side=tk.LEFT, padx=(2, 2))
        theme_manager.register(self.save_entry, 'BG_INPUT', 'TEXT_DARK')
        
        self._btn(r2, "...", self._browse_save, "secondary").pack(side=tk.LEFT)
    
    def _create_controls_section(self):
        sec = self._section("🎬 CONTROLS")
        
        row = tk.Frame(sec, bg=Theme.BG_SECTION)
        row.pack(fill=tk.X, pady=2)
        theme_manager.register(row, 'BG_SECTION')
        
        self.rec_btn = tk.Button(row, text="▶ RECORD", command=self._start_recording,
                                font=('Segoe UI', 11, 'bold'), bg=Theme.SUCCESS, fg=Theme.TEXT_LIGHT,
                                activebackground="#2ecc71", relief=tk.FLAT, cursor='hand2', padx=12, pady=5)
        self.rec_btn.pack(side=tk.LEFT, padx=(0, 4))
        self.rec_btn._style_info = ('SUCCESS', 'SUCCESS')
        theme_manager.register(self.rec_btn, 'SUCCESS', 'TEXT_LIGHT')
        
        self.stop_btn = tk.Button(row, text="⏹ STOP", command=self._stop_recording,
                                 font=('Segoe UI', 10, 'bold'), bg=Theme.ERROR, fg=Theme.TEXT_LIGHT,
                                 relief=tk.FLAT, padx=8, pady=5, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=(0, 4))
        theme_manager.register(self.stop_btn, 'ERROR', 'TEXT_LIGHT')
        
        self.repeat_btn = tk.Button(row, text="🔄 F5", command=self._quick_repeat,
                                   font=('Segoe UI', 9, 'bold'), bg=Theme.BLUE_ACCENT, fg=Theme.TEXT_LIGHT,
                                   relief=tk.FLAT, padx=6, pady=5, state=tk.DISABLED)
        self.repeat_btn.pack(side=tk.LEFT, padx=(0, 4))
        theme_manager.register(self.repeat_btn, 'BLUE_ACCENT', 'TEXT_LIGHT')
        
        self._btn(row, "📁", self._open_folder, "secondary").pack(side=tk.RIGHT)
    
    def _create_progress_section(self):
        sec = self._section("📊 PROGRESS")
        
        self.prog_lbl = tk.Label(sec, text="Ready", font=('Segoe UI', 10),
                                 bg=Theme.BG_SECTION, fg=Theme.NAVY)
        self.prog_lbl.pack(pady=2)
        theme_manager.register(self.prog_lbl, 'BG_SECTION', 'NAVY')
        
        self.iti_lbl = tk.Label(sec, text="", font=('Segoe UI', 9, 'bold'),
                                bg=Theme.BG_SECTION, fg=Theme.WARNING)
        self.iti_lbl.pack(pady=1)
        theme_manager.register(self.iti_lbl, 'BG_SECTION', 'WARNING')
        
        self.prog_container = tk.Frame(sec, bg=Theme.GRAY_LIGHT, height=16)
        self.prog_container.pack(fill=tk.X, pady=2)
        self.prog_container.pack_propagate(False)
        theme_manager.register(self.prog_container, 'GRAY_LIGHT')
        
        self.prog_base = tk.Frame(self.prog_container, bg=Theme.GRAY_PALE, width=0)
        self.prog_base.pack(side=tk.LEFT, fill=tk.Y)
        self.prog_odor = tk.Frame(self.prog_container, bg=Theme.GRAY_PALE, width=0)
        self.prog_odor.pack(side=tk.LEFT, fill=tk.Y)
        self.prog_post = tk.Frame(self.prog_container, bg=Theme.GRAY_PALE, width=0)
        self.prog_post.pack(side=tk.LEFT, fill=tk.Y)
        
        self.timing_lbl = tk.Label(sec, text="", font=('Segoe UI', 8),
                                   bg=Theme.BG_SECTION, fg=Theme.TEXT_MUTED)
        self.timing_lbl.pack(pady=1)
        theme_manager.register(self.timing_lbl, 'BG_SECTION', 'TEXT_MUTED')
    
    def _create_log_section(self):
        sec = self._section("📝 LOG")
        
        self.log_text = tk.Text(sec, height=4, wrap=tk.WORD, font=('Consolas', 8),
                               bg=Theme.LOG_BG, fg=Theme.LOG_FG, relief=tk.FLAT, padx=5, pady=3)
        self.log_text.pack(fill=tk.BOTH, expand=True)
        self.log_text.tag_configure('SUCCESS', foreground=Theme.SUCCESS)
        self.log_text.tag_configure('ERROR', foreground=Theme.ERROR)
        self.log_text.tag_configure('WARNING', foreground=Theme.WARNING)
        theme_manager.register(self.log_text, 'LOG_BG', 'LOG_FG')
    

    # =========================================================================
    # PROTOCOL RUNNER (v10.0)
    # =========================================================================
    def _create_protocols_tab(self):
        """Protocols tab: define, save, load, and execute multi-odor runs."""
        outer = tk.Frame(self.protocol_tab, bg=Theme.BG_MAIN)
        outer.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        theme_manager.register(outer, 'BG_MAIN')

        title = tk.Label(outer, text="Protocol Runner", font=('Segoe UI', 14, 'bold'),
                         bg=Theme.BG_MAIN, fg=Theme.TEXT_DARK)
        title.pack(anchor="w", pady=(0, 10))
        theme_manager.register(title, 'BG_MAIN', 'TEXT_DARK')

        # Left: saved protocols list
        top = tk.Frame(outer, bg=Theme.BG_MAIN)
        top.pack(fill=tk.BOTH, expand=True)
        theme_manager.register(top, 'BG_MAIN')

        left = tk.Frame(top, bg=Theme.BG_MAIN)
        left.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 12))
        theme_manager.register(left, 'BG_MAIN')

        tk.Label(left, text="Saved protocols", bg=Theme.BG_MAIN, fg=Theme.TEXT_DARK,
                 font=('Segoe UI', 10, 'bold')).pack(anchor="w")
        self.proto_list = tk.Listbox(left, height=18, exportselection=False, bg=Theme.BG_INPUT, fg=Theme.TEXT_DARK, relief=tk.FLAT)
        self.proto_list.pack(fill=tk.Y, expand=True, pady=(4, 8))
        theme_manager.register(self.proto_list, 'BG_INPUT', 'TEXT_DARK')

        btn_row = tk.Frame(left, bg=Theme.BG_MAIN)
        btn_row.pack(fill=tk.X)
        theme_manager.register(btn_row, 'BG_MAIN')
        self._btn(btn_row, "↻ Refresh", self._refresh_protocol_list, "secondary").pack(side=tk.LEFT, padx=(0, 6))
        self._btn(btn_row, "Load", self._load_selected_protocol, "secondary").pack(side=tk.LEFT)

        # Right: editor
        right = tk.Frame(top, bg=Theme.BG_MAIN)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        theme_manager.register(right, 'BG_MAIN')

        form = tk.LabelFrame(right, text="Protocol settings", bg=Theme.BG_MAIN, fg=Theme.TEXT_DARK,
                             font=('Segoe UI', 10, 'bold'), padx=10, pady=10)
        form.pack(fill=tk.BOTH, expand=True)
        theme_manager.register(form, 'BG_MAIN', 'TEXT_DARK')

        self.proto_name_var = tk.StringVar(value=DEFAULT_PROTOCOL_NAME)
        self.proto_fps_var = tk.StringVar(value=str(DEFAULT_FPS))
        self.proto_base_var = tk.StringVar(value=str(DEFAULT_BASELINE))
        self.proto_odor_var = tk.StringVar(value=str(DEFAULT_ODOR))
        self.proto_post_var = tk.StringVar(value=str(DEFAULT_POST))
        self.proto_repeats_var = tk.StringVar(value=str(DEFAULT_PROTOCOL_REPEATS_PER_ODOR))
        self.proto_iti_var = tk.StringVar(value=str(DEFAULT_ITI))
        self.proto_interodor_var = tk.StringVar(value=str(DEFAULT_PROTOCOL_INTER_ODOR_SEC))
        self.proto_flyids_var = tk.StringVar(value=DEFAULT_PROTOCOL_FLY_IDS)
        self.proto_video_var = tk.BooleanVar(value=DEFAULT_SAVE_PROTOCOL_VIDEO)
        self.proto_genotype_meta_var = tk.StringVar(value="")
        self.proto_gender_var = tk.StringVar(value="")
        self.proto_age_var = tk.StringVar(value="")
        self.proto_post_surg_var = tk.StringVar(value="")
        self.proto_notes_var = tk.StringVar(value="")

        def row(label, var, width=10):
            r = tk.Frame(form, bg=Theme.BG_MAIN)
            r.pack(fill=tk.X, pady=2)
            theme_manager.register(r, 'BG_MAIN')
            tk.Label(r, text=label, width=24, anchor="w",
                     bg=Theme.BG_MAIN, fg=Theme.TEXT_DARK).pack(side=tk.LEFT)
            e = tk.Entry(r, textvariable=var, width=width,
                         bg=Theme.BG_INPUT, fg=Theme.TEXT_DARK, relief=tk.FLAT)
            e.pack(side=tk.LEFT)
            theme_manager.register(e, 'BG_INPUT', 'TEXT_DARK')
            return e

        row("Protocol name", self.proto_name_var, width=24)
        row("FPS (target)", self.proto_fps_var, width=10)
        row("Pre (s)", self.proto_base_var, width=10)
        row("Odor (s)", self.proto_odor_var, width=10)
        row("Post (s)", self.proto_post_var, width=10)
        row("Repeats per odor", self.proto_repeats_var, width=10)
        row("Inter-trial total (s)", self.proto_iti_var, width=10)
        row("Inter-odor total (s)", self.proto_interodor_var, width=10)
        row("Fly IDs (e.g., 1-5 or 1,3,7)", self.proto_flyids_var, width=24)
        row("Genotype", self.proto_genotype_meta_var, width=24)
        row("Gender", self.proto_gender_var, width=10)
        row("Age (days)", self.proto_age_var, width=10)
        row("Post-surgery (h)", self.proto_post_surg_var, width=10)
        row("Notes (optional)", self.proto_notes_var, width=40)

        r = tk.Frame(form, bg=Theme.BG_MAIN)
        r.pack(fill=tk.X, pady=(6, 2))
        theme_manager.register(r, 'BG_MAIN')
        tk.Checkbutton(r, text="Save .avi video (8-bit preview)", variable=self.proto_video_var,
                       bg=Theme.BG_MAIN, fg=Theme.TEXT_DARK, selectcolor=Theme.BG_MAIN,
                       activebackground=Theme.BG_MAIN).pack(anchor="w")
        theme_manager.register(r, 'BG_MAIN', 'TEXT_DARK', {'selectcolor': 'BG_MAIN', 'activebackground': 'BG_MAIN'})

        # Odor selection
        tk.Label(form, text="Odors to run (default: all)", bg=Theme.BG_MAIN, fg=Theme.TEXT_DARK,
                 font=('Segoe UI', 10, 'bold')).pack(anchor="w", pady=(10, 4))
        self.proto_odors = tk.Listbox(form, selectmode=tk.MULTIPLE, height=6, exportselection=False, bg=Theme.BG_INPUT, fg=Theme.TEXT_DARK, relief=tk.FLAT)
        for o in ODOR_OPTIONS:
            self.proto_odors.insert(tk.END, o)
        self.proto_odors.selection_set(0, tk.END)
        self.proto_odors.pack(fill=tk.X)
        theme_manager.register(self.proto_odors, 'BG_INPUT', 'TEXT_DARK')

        # Real-time protocol status monitor
        status_frame = tk.LabelFrame(form, text="⚡ Live Protocol Status", bg=Theme.BG_MAIN, fg=Theme.NAVY_DARK,
                                     font=('Segoe UI', 10, 'bold'), padx=12, pady=10)
        status_frame.pack(fill=tk.X, pady=(10, 0))
        theme_manager.register(status_frame, 'BG_MAIN', 'NAVY_DARK')

        # Trial progress
        trial_row = tk.Frame(status_frame, bg=Theme.BG_MAIN)
        trial_row.pack(fill=tk.X, pady=2)
        theme_manager.register(trial_row, 'BG_MAIN')
        tk.Label(trial_row, text="Trial:", width=12, anchor="w", font=('Segoe UI', 9, 'bold'),
                 bg=Theme.BG_MAIN, fg=Theme.TEXT_DARK).pack(side=tk.LEFT)
        self.proto_trial_lbl = tk.Label(trial_row, text="Not running", anchor="w", font=('Segoe UI', 9),
                                        bg=Theme.BG_MAIN, fg=Theme.TEXT_DARK)
        self.proto_trial_lbl.pack(side=tk.LEFT, fill=tk.X, expand=True)
        theme_manager.register(self.proto_trial_lbl, 'BG_MAIN', 'TEXT_DARK')

        # Current phase
        phase_row = tk.Frame(status_frame, bg=Theme.BG_MAIN)
        phase_row.pack(fill=tk.X, pady=2)
        theme_manager.register(phase_row, 'BG_MAIN')
        tk.Label(phase_row, text="Phase:", width=12, anchor="w", font=('Segoe UI', 9, 'bold'),
                 bg=Theme.BG_MAIN, fg=Theme.TEXT_DARK).pack(side=tk.LEFT)
        self.proto_phase_lbl = tk.Label(phase_row, text="—", anchor="w", font=('Segoe UI', 9),
                                        bg=Theme.BG_MAIN, fg=Theme.TEXT_DARK)
        self.proto_phase_lbl.pack(side=tk.LEFT, fill=tk.X, expand=True)
        theme_manager.register(self.proto_phase_lbl, 'BG_MAIN', 'TEXT_DARK')

        # Current odor
        odor_row = tk.Frame(status_frame, bg=Theme.BG_MAIN)
        odor_row.pack(fill=tk.X, pady=2)
        theme_manager.register(odor_row, 'BG_MAIN')
        tk.Label(odor_row, text="Odor:", width=12, anchor="w", font=('Segoe UI', 9, 'bold'),
                 bg=Theme.BG_MAIN, fg=Theme.TEXT_DARK).pack(side=tk.LEFT)
        self.proto_odor_lbl = tk.Label(odor_row, text="—", anchor="w", font=('Segoe UI', 9),
                                       bg=Theme.BG_MAIN, fg=Theme.TEXT_DARK)
        self.proto_odor_lbl.pack(side=tk.LEFT, fill=tk.X, expand=True)
        theme_manager.register(self.proto_odor_lbl, 'BG_MAIN', 'TEXT_DARK')

        # Timing
        timing_row = tk.Frame(status_frame, bg=Theme.BG_MAIN)
        timing_row.pack(fill=tk.X, pady=2)
        theme_manager.register(timing_row, 'BG_MAIN')
        tk.Label(timing_row, text="Timing:", width=12, anchor="w", font=('Segoe UI', 9, 'bold'),
                 bg=Theme.BG_MAIN, fg=Theme.TEXT_DARK).pack(side=tk.LEFT)
        self.proto_timing_lbl = tk.Label(timing_row, text="—", anchor="w", font=('Segoe UI', 9),
                                         bg=Theme.BG_MAIN, fg=Theme.TEXT_DARK)
        self.proto_timing_lbl.pack(side=tk.LEFT, fill=tk.X, expand=True)
        theme_manager.register(self.proto_timing_lbl, 'BG_MAIN', 'TEXT_DARK')

        # Camera status
        camera_row = tk.Frame(status_frame, bg=Theme.BG_MAIN)
        camera_row.pack(fill=tk.X, pady=2)
        theme_manager.register(camera_row, 'BG_MAIN')
        tk.Label(camera_row, text="Camera:", width=12, anchor="w", font=('Segoe UI', 9, 'bold'),
                 bg=Theme.BG_MAIN, fg=Theme.TEXT_DARK).pack(side=tk.LEFT)
        self.proto_camera_lbl = tk.Label(camera_row, text="—", anchor="w", font=('Segoe UI', 9),
                                         bg=Theme.BG_MAIN, fg=Theme.TEXT_DARK)
        self.proto_camera_lbl.pack(side=tk.LEFT, fill=tk.X, expand=True)
        theme_manager.register(self.proto_camera_lbl, 'BG_MAIN', 'TEXT_DARK')

        # Completed trials
        completed_row = tk.Frame(status_frame, bg=Theme.BG_MAIN)
        completed_row.pack(fill=tk.X, pady=2)
        theme_manager.register(completed_row, 'BG_MAIN')
        tk.Label(completed_row, text="Completed:", width=12, anchor="w", font=('Segoe UI', 9, 'bold'),
                 bg=Theme.BG_MAIN, fg=Theme.TEXT_DARK).pack(side=tk.LEFT)
        self.proto_completed_lbl = tk.Label(completed_row, text="—", anchor="w", font=('Segoe UI', 9),
                                            bg=Theme.BG_MAIN, fg=Theme.TEXT_DARK)
        self.proto_completed_lbl.pack(side=tk.LEFT, fill=tk.X, expand=True)
        theme_manager.register(self.proto_completed_lbl, 'BG_MAIN', 'TEXT_DARK')

        # Action buttons
        actions = tk.Frame(outer, bg=Theme.BG_MAIN)
        actions.pack(fill=tk.X, pady=(10, 0))
        theme_manager.register(actions, 'BG_MAIN')

        self.save_proto_btn = self._btn(actions, "💾 Save Protocol", self._save_protocol, "secondary")
        self.save_proto_btn.pack(side=tk.LEFT, padx=(0, 8))
        self.load_proto_file_btn = self._btn(actions, "📂 Load Protocol File", self._load_protocol_file, "secondary")
        self.load_proto_file_btn.pack(side=tk.LEFT, padx=(0, 8))
        self.run_proto_btn = self._btn(actions, "▶ Run Protocol", self._run_protocol)
        self.run_proto_btn.pack(side=tk.LEFT, padx=(0, 8))
        self.stop_proto_btn = self._btn(actions, "■ Stop", self._stop_recording, "danger")
        self.stop_proto_btn.pack(side=tk.RIGHT)
        
        # Remote sync button
        self.sync_btn = self._btn(actions, "☁️ Sync to Server", self._show_sync_dialog, "secondary")
        self.sync_btn.pack(side=tk.RIGHT, padx=(0, 8))

        # Status line
        self.proto_status = tk.Label(outer, text="", bg=Theme.BG_MAIN, fg=Theme.TEXT_DARK, anchor="w")
        self.proto_status.pack(fill=tk.X, pady=(8, 0))
        theme_manager.register(self.proto_status, 'BG_MAIN', 'TEXT_DARK')
        
        # Remote sync status
        self.sync_status = tk.Label(outer, text="", bg=Theme.BG_MAIN, fg=Theme.TEXT_DARK, anchor="w", font=("Segoe UI", 8))
        self.sync_status.pack(fill=tk.X, pady=(2, 0))
        theme_manager.register(self.sync_status, 'BG_MAIN', 'TEXT_DARK')

        self._refresh_protocol_list()
        self._update_sync_status()

    def _protocol_store_dir(self):
        try:
            base = Path(self.save_var.get())
        except Exception:
            base = Path.cwd()
        pdir = base / "protocols"
        pdir.mkdir(parents=True, exist_ok=True)
        return pdir

    def _refresh_protocol_list(self):
        if not hasattr(self, "proto_list"):
            return
        self.proto_list.delete(0, tk.END)
        pdir = self._protocol_store_dir()
        for fp in sorted(pdir.glob("*.json"))[:999]:
            self.proto_list.insert(tk.END, fp.stem)

    def _selected_protocol_name(self):
        if not hasattr(self, "proto_list"):
            return None
        sel = self.proto_list.curselection()
        if not sel:
            return None
        return self.proto_list.get(sel[0])

    def _load_selected_protocol(self):
        name = self._selected_protocol_name()
        if not name:
            self._log("No protocol selected", "WARNING")
            return
        fp = self._protocol_store_dir() / f"{name}.json"
        self._load_protocol_from_path(fp)

    def _load_protocol_file(self):
        fp = filedialog.askopenfilename(
            title="Load Protocol JSON",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")]
        )
        if not fp:
            return
        self._load_protocol_from_path(Path(fp))

    def _load_protocol_from_path(self, path: Path):
        try:
            cfg = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load protocol:\n{e}")
            return

        self.proto_name_var.set(str(cfg.get("name", DEFAULT_PROTOCOL_NAME)))
        self.proto_fps_var.set(str(cfg.get("fps", DEFAULT_FPS)))
        self.proto_base_var.set(str(cfg.get("baseline_sec", DEFAULT_BASELINE)))
        self.proto_odor_var.set(str(cfg.get("odor_sec", DEFAULT_ODOR)))
        self.proto_post_var.set(str(cfg.get("post_sec", DEFAULT_POST)))
        self.proto_repeats_var.set(str(cfg.get("repeats_per_odor", DEFAULT_PROTOCOL_REPEATS_PER_ODOR)))
        self.proto_iti_var.set(str(cfg.get("iti_between_trials_sec", DEFAULT_ITI)))
        self.proto_interodor_var.set(str(cfg.get("inter_odor_sec", DEFAULT_PROTOCOL_INTER_ODOR_SEC)))
        self.proto_flyids_var.set(str(cfg.get("fly_ids", DEFAULT_PROTOCOL_FLY_IDS)))
        self.proto_video_var.set(bool(cfg.get("save_video", DEFAULT_SAVE_PROTOCOL_VIDEO)))

        # Odor selection
        odors = cfg.get("odors", ODOR_OPTIONS)
        self.proto_odors.selection_clear(0, tk.END)
        for i, o in enumerate(ODOR_OPTIONS):
            if o in odors:
                self.proto_odors.selection_set(i)

        self._log(f"Loaded protocol: {path.name}", "SUCCESS")

    def _save_protocol(self):
        try:
            cfg = self._collect_protocol_cfg()
        except ValueError as e:
            messagebox.showerror("Error", str(e))
            return

        pdir = self._protocol_store_dir()
        fp = pdir / f"{cfg['name']}.json"
        fp.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        self._log(f"Saved protocol: {fp}", "SUCCESS")
        self._refresh_protocol_list()

    def _collect_protocol_cfg(self):
        name = self.proto_name_var.get().strip()
        if not name:
            raise ValueError("Protocol name is required.")

        def fnum(var, label, minv=None):
            try:
                v = float(var.get())
            except Exception:
                raise ValueError(f"Invalid {label}.")
            if minv is not None and v < minv:
                raise ValueError(f"{label} must be >= {minv}.")
            return v

        def inum(var, label, minv=None):
            try:
                v = int(float(var.get()))
            except Exception:
                raise ValueError(f"Invalid {label}.")
            if minv is not None and v < minv:
                raise ValueError(f"{label} must be >= {minv}.")
            return v

        odors = self._get_selected_protocol_odors()
        # odors will always have values - defaults to all ODOR_OPTIONS

        cfg = {
            "name": name,
            "fps": fnum(self.proto_fps_var, "FPS", 0.1),
            "baseline_sec": fnum(self.proto_base_var, "Pre (s)", 0.0),
            "odor_sec": fnum(self.proto_odor_var, "Odor (s)", 0.0),
            "post_sec": fnum(self.proto_post_var, "Post (s)", 0.0),
            "repeats_per_odor": inum(self.proto_repeats_var, "Repeats per odor", 1),
            "iti_between_trials_sec": fnum(self.proto_iti_var, "Inter-trial total (s)", 0.0),
            "inter_odor_sec": fnum(self.proto_interodor_var, "Inter-odor total (s)", 0.0),
            "fly_ids": self.proto_flyids_var.get().strip(),
            "odors": odors,
            "fly_gender": self.proto_gender_var.get().strip(),
            "fly_age_days": self.proto_age_var.get().strip(),
            "post_surgery_hours": self.proto_post_surg_var.get().strip(),
            "genotype": self.proto_genotype_meta_var.get().strip(),
            "notes": self.proto_notes_var.get().strip(),
            "save_video": bool(self.proto_video_var.get()),
            "save_dir": self.save_var.get().strip(),
        }
        return cfg

    def _get_selected_protocol_odors(self):
        try:
            sel = list(self.proto_odors.curselection())
            if sel:
                return [ODOR_OPTIONS[i] for i in sel if i < len(ODOR_OPTIONS)]
        except Exception:
            pass
        # Default to all odors if none selected or on any error
        return list(ODOR_OPTIONS)

    def _parse_fly_ids(self, s: str):
        s = (s or "").strip()
        if not s:
            return []
        out = []
        parts = [p.strip() for p in s.split(",") if p.strip()]
        for p in parts:
            if "-" in p:
                a, b = p.split("-", 1)
                a = int(a.strip()); b = int(b.strip())
                if b < a:
                    a, b = b, a
                out.extend(list(range(a, b + 1)))
            else:
                out.append(int(p))
        seen = set()
        final = []
        for x in out:
            if x not in seen:
                seen.add(x)
                final.append(x)
        return final

    def _run_protocol(self):
        if self.recording or self.protocol_running:
            self._log("Already running", "WARNING")
            return

        if not self.mm or not self.mm.connected:
            messagebox.showerror("Error", "Camera not connected.")
            return

        try:
            cfg = self._collect_protocol_cfg()
        except ValueError as e:
            messagebox.showerror("Error", str(e))
            return

        # Use genotype + camera settings from Run tab
        valid, params = self._get_params()
        if not valid:
            messagebox.showerror("Error", params)
            return

        # Build run plan
        fly_ids = self._parse_fly_ids(cfg.get("fly_ids", "")) or ([int(params["fly_id"])] if str(params.get("fly_id", "")).strip().isdigit() else [])
        if not fly_ids:
            fly_ids = ["unknown"]

        plan = {"cfg": cfg, "params": params, "fly_ids": fly_ids}

        self.protocol_abort.clear()
        self.protocol_running = True

        if self.preview_active:
            self.mm.stop_preview()
            self.preview_active = False
            self.preview_btn.config(text="▶ Start Preview")
            self.preview_panel.set_status("Stopped", Theme.TEXT_MUTED)

        try:
            self.preview_btn.config(state=tk.DISABLED)
        except Exception:
            pass

        self.proto_status.config(text="Running protocol…")
        self._log(f"Protocol started: {cfg['name']} | Odors={len(cfg['odors'])} | Repeats={cfg['repeats_per_odor']}", "SUCCESS")

        try:
            self.run_proto_btn.config(state=tk.DISABLED)
            self.save_proto_btn.config(state=tk.DISABLED)
            self.load_proto_file_btn.config(state=tk.DISABLED)
        except Exception:
            pass

        threading.Thread(target=self._protocol_worker, args=(plan,), daemon=True).start()

    def _update_proto_status(self, trial_text="", phase_text="", odor_text="", timing_text="", camera_text="", completed_text=""):
        """Thread-safe update of protocol status display."""
        def _update():
            try:
                if trial_text and hasattr(self, 'proto_trial_lbl'):
                    self.proto_trial_lbl.config(text=trial_text)
                if phase_text and hasattr(self, 'proto_phase_lbl'):
                    self.proto_phase_lbl.config(text=phase_text)
                if odor_text and hasattr(self, 'proto_odor_lbl'):
                    self.proto_odor_lbl.config(text=odor_text)
                if timing_text and hasattr(self, 'proto_timing_lbl'):
                    self.proto_timing_lbl.config(text=timing_text)
                if camera_text and hasattr(self, 'proto_camera_lbl'):
                    self.proto_camera_lbl.config(text=camera_text)
                if completed_text and hasattr(self, 'proto_completed_lbl'):
                    self.proto_completed_lbl.config(text=completed_text)
            except Exception:
                pass
        self.root.after(0, _update)

    def _clear_proto_status(self):
        """Clear protocol status display."""
        self._update_proto_status(
            trial_text="Not running",
            phase_text="—",
            odor_text="—",
            timing_text="—",
            camera_text="—",
            completed_text="—"
        )

    def _capture_structure_images(self, save_dir, fps):
        """Capture structure/anatomy images with RFP light before protocol starts.
        
        Args:
            save_dir: Directory to save structure images
            fps: FPS to use (ignored, we use fixed 50ms exposure)
            
        Returns:
            bool: True if successful, False if skipped/failed
        """
        # Create popup instructing user to prepare RFP light
        result = {"proceed": False, "popup": None, "exposure_ms": 50.0}
        
        def _show_structure_popup():
            popup = tk.Toplevel(self.root)
            result["popup"] = popup
            popup.title("Structure Recording")
            popup.configure(bg=Theme.BG_MAIN)
            try:
                popup.transient(self.root)
                popup.grab_set()
            except Exception:
                pass
            
            popup.protocol("WM_DELETE_WINDOW", lambda: None)
            
            frm = tk.Frame(popup, bg=Theme.BG_MAIN, padx=20, pady=18)
            frm.pack(fill=tk.BOTH, expand=True)
            theme_manager.register(frm, 'BG_MAIN')
            
            header = tk.Label(frm, text="📸 Structure Recording", font=("Segoe UI", 13, "bold"),
                              bg=Theme.BG_MAIN, fg=Theme.NAVY_DARK)
            header.pack(anchor="w", pady=(0, 10))
            theme_manager.register(header, 'BG_MAIN', 'NAVY_DARK')
            
            instructions = [
                "Before starting odor trials, we'll capture reference images",
                "of the brain structure using RFP light.",
                "",
                "Please:",
                "  1. Turn OFF the shutter (no green GCaMP excitation)",
                "  2. Turn ON the RFP illumination light",
                "  3. Check focus and adjust if needed",
                "  4. Set exposure time and press 'Capture' when ready"
            ]
            
            for line in instructions:
                lbl = tk.Label(frm, text=line, font=("Segoe UI", 9), 
                              bg=Theme.BG_MAIN, fg=Theme.TEXT_DARK, anchor="w", justify=tk.LEFT)
                lbl.pack(anchor="w", pady=1)
                theme_manager.register(lbl, 'BG_MAIN', 'TEXT_DARK')
            
            # Exposure time input
            exp_frame = tk.Frame(frm, bg=Theme.BG_MAIN)
            exp_frame.pack(pady=(12, 0), fill=tk.X)
            theme_manager.register(exp_frame, 'BG_MAIN')
            
            exp_lbl = tk.Label(exp_frame, text="Exposure Time (ms):", font=("Segoe UI", 9, "bold"),
                              bg=Theme.BG_MAIN, fg=Theme.TEXT_DARK)
            exp_lbl.pack(side=tk.LEFT, padx=(0, 8))
            theme_manager.register(exp_lbl, 'BG_MAIN', 'TEXT_DARK')
            
            exp_entry = tk.Entry(exp_frame, font=("Segoe UI", 10), width=10)
            exp_entry.insert(0, "50.0")
            exp_entry.pack(side=tk.LEFT)
            
            exp_info = tk.Label(exp_frame, text="(5 images will be captured)", font=("Segoe UI", 8),
                               bg=Theme.BG_MAIN, fg=Theme.TEXT_MED)
            exp_info.pack(side=tk.LEFT, padx=(8, 0))
            theme_manager.register(exp_info, 'BG_MAIN', 'TEXT_MED')
            
            btn_frame = tk.Frame(frm, bg=Theme.BG_MAIN)
            btn_frame.pack(pady=(15, 0), fill=tk.X)
            theme_manager.register(btn_frame, 'BG_MAIN')
            
            def on_capture():
                try:
                    exposure_val = float(exp_entry.get())
                    if exposure_val <= 0:
                        raise ValueError("Exposure must be positive")
                    result["exposure_ms"] = exposure_val
                    result["proceed"] = True
                except ValueError:
                    # Show error and return without closing
                    exp_entry.configure(bg="#ffcccc")
                    self.root.after(500, lambda: exp_entry.configure(bg="white"))
                    return
                try:
                    popup.grab_release()
                except Exception:
                    pass
                popup.destroy()
            
            def on_skip():
                result["proceed"] = False
                try:
                    popup.grab_release()
                except Exception:
                    pass
                popup.destroy()
            
            capture_btn = self._btn(btn_frame, "📸 Capture Structure", on_capture)
            capture_btn.pack(side=tk.LEFT, padx=(0, 10))
            
            skip_btn = self._btn(btn_frame, "Skip", on_skip, "secondary")
            skip_btn.pack(side=tk.LEFT)
        
        # Show popup on main thread
        self.root.after(0, _show_structure_popup)
        
        # Wait for user decision
        while result["popup"] is None or result["popup"].winfo_exists():
            time.sleep(0.05)
        
        if not result["proceed"]:
            self._log("Structure recording skipped by user", "WARNING")
            return False
        
        # Get user-specified exposure time
        exposure_ms = result["exposure_ms"]
        
        # Update status
        self._update_proto_status(
            trial_text="Structure Recording",
            phase_text="📸 Capturing RFP images",
            odor_text="—",
            timing_text=f"5 images @ {exposure_ms}ms exposure",
            camera_text="Recording structure..."
        )
        
        try:
            # Save current exposure
            original_exp = self.mm.core.get_exposure() if self.mm and self.mm.connected else exposure_ms
            
            # Set to user-specified exposure for structure imaging
            if self.mm and self.mm.connected:
                self.mm.set_exposure(exposure_ms)
                time.sleep(0.1)  # Let camera settle
            
            self._log(f"Capturing structure images (5 frames @ {exposure_ms}ms)...", "SUCCESS")
            
            # Capture 5 images
            n_images = 5
            for i in range(n_images):
                if self.protocol_abort.is_set():
                    return False
                
                # Snap single image
                self.mm.core.snap_image()
                tagged_img = self.mm.core.get_tagged_image()
                pixels = np.reshape(tagged_img.pix, [tagged_img.tags['Height'], tagged_img.tags['Width']])
                
                # Save as 16-bit TIFF (fixed deprecation warning)
                frame_path = save_dir / f"structure_{i+1:03d}.tif"
                Image.fromarray(pixels.astype(np.uint16)).save(frame_path, format='TIFF')
                
                self._update_proto_status(
                    camera_text=f"Captured {i+1}/{n_images} structure images"
                )
                
                time.sleep(0.1)  # Small delay between captures
            
            # Restore original exposure
            if self.mm and self.mm.connected:
                self.mm.set_exposure(original_exp)
            
            self._log(f"Structure images saved to: {save_dir}", "SUCCESS")
            self._update_proto_status(
                phase_text="✅ Structure captured",
                camera_text=f"Saved {n_images} images"
            )
            
            # Remind user to switch back to GCaMP light
            reminder_done = threading.Event()
            
            def _show_reminder():
                popup = tk.Toplevel(self.root)
                popup.title("Ready for Protocol")
                popup.configure(bg=Theme.BG_MAIN)
                try:
                    popup.transient(self.root)
                    popup.grab_set()
                except Exception:
                    pass
                
                frm = tk.Frame(popup, bg=Theme.BG_MAIN, padx=20, pady=18)
                frm.pack(fill=tk.BOTH, expand=True)
                theme_manager.register(frm, 'BG_MAIN')
                
                tk.Label(frm, text="✅ Structure Images Captured", font=("Segoe UI", 12, "bold"),
                        bg=Theme.BG_MAIN, fg=Theme.SUCCESS).pack(pady=(0, 10))
                
                tk.Label(frm, text="Now prepare for odor trials:\n\n"
                               "  1. Turn OFF RFP light\n"
                               "  2. Turn ON GCaMP excitation light\n"
                               "  3. Check shutter is OFF\n"
                               "  4. Press OK to begin trials",
                        font=("Segoe UI", 9), bg=Theme.BG_MAIN, fg=Theme.TEXT_DARK,
                        justify=tk.LEFT).pack(pady=(0, 15))
                
                def on_ok():
                    try:
                        popup.grab_release()
                    except Exception:
                        pass
                    popup.destroy()
                    reminder_done.set()
                
                self._btn(frm, "OK - Begin Trials", on_ok).pack()
            
            self.root.after(0, _show_reminder)
            reminder_done.wait()  # Wait for user to acknowledge
            
            return True
            
        except Exception as e:
            self._log(f"Structure capture failed: {e}", "ERROR")
            self._update_proto_status(
                phase_text="❌ Structure capture failed",
                camera_text=str(e)[:50]
            )
            # Restore exposure on error
            try:
                if self.mm and self.mm.connected:
                    self.mm.set_exposure(original_exp)
            except Exception:
                pass
            return False

    def _protocol_worker(self, plan):
        """Run protocol (randomized block design) in a background thread."""
        self.protocol_running = True
        self.protocol_abort_flag = False

        def ui_info(title, message):
            done_evt = threading.Event()
            def _do():
                try:
                    messagebox.showinfo(title, message)
                finally:
                    done_evt.set()
            self.root.after(0, _do)
            done_evt.wait()

        def ui_error(title, message):
            done_evt = threading.Event()
            def _do():
                try:
                    messagebox.showerror(title, message)
                finally:
                    done_evt.set()
            self.root.after(0, _do)
            done_evt.wait()

        def ui_confirm(title, message):
            done_evt = threading.Event()
            out = {"ok": False}
            def _do():
                try:
                    out["ok"] = bool(messagebox.askyesno(title, message))
                finally:
                    done_evt.set()
            self.root.after(0, _do)
            done_evt.wait()
            return out["ok"]

        def build_block_sequence(odors, blocks):
            rng = random.SystemRandom()
            odors = list(odors)
            seq = []
            last = None
            for _b in range(int(blocks)):
                block = list(odors)
                # shuffle until boundary constraint satisfied
                for _try in range(20):
                    rng.shuffle(block)
                    if (last is None) or (not block) or (block[0] != last):
                        break
                # hard fallback: rotate if still violates
                if last is not None and block and block[0] == last and len(block) > 1:
                    block = block[1:] + block[:1]
                seq.extend(block)
                last = block[-1] if block else last
            return seq

        try:
            cfg = plan.get("cfg", {})
            params = plan.get("params", {})

            # Defaults per your workflow
            base_sec = float(cfg.get("baseline_sec", 15.0))
            odor_sec = float(cfg.get("odor_sec", 4.0))
            post_sec = float(cfg.get("post_sec", 15.0))

            # Interpret "inter-trial total" as total time between trial starts INCLUDING baseline+post
            iti_total = float(cfg.get("iti_between_trials_sec", 60.0))
            inter_total = float(cfg.get("inter_odor_sec", 60.0))
            iti_wait = max(0.0, iti_total - (base_sec + post_sec))
            inter_wait = max(0.0, inter_total - (base_sec + post_sec))

            repeats = int(cfg.get("repeats_per_odor", 1))
            fps = float(cfg.get("fps", 10.0))
            
            # Handle odors - could be list of strings
            raw_odors = cfg.get("odors", [])
            if isinstance(raw_odors, list):
                odors = [str(o).strip().upper() for o in raw_odors if str(o).strip()]
            else:
                odors = [str(raw_odors).strip().upper()] if str(raw_odors).strip() else []
            if not odors:
                # Default to all odors if somehow none were passed
                odors = [o.upper() for o in ODOR_OPTIONS]

            # Handle fly_ids - could be string "1,2,3" or list [1,2,3] or already parsed
            raw_fly_ids = cfg.get("fly_ids", "1")
            if isinstance(raw_fly_ids, list):
                fly_ids = [int(x) if str(x).strip().isdigit() else str(x) for x in raw_fly_ids]
            else:
                fly_ids = self._parse_fly_ids(str(raw_fly_ids))
            if not fly_ids:
                fly_ids = [1]
            
            # Handle string fields safely
            def safe_str(val):
                if val is None:
                    return ""
                if isinstance(val, list):
                    return ", ".join(str(v) for v in val)
                return str(val).strip()
            
            genotype = safe_str(cfg.get("genotype", ""))
            gender = safe_str(cfg.get("fly_gender", ""))
            age_days = safe_str(cfg.get("fly_age_days", ""))
            post_surg_h = safe_str(cfg.get("post_surgery_hours", ""))
            notes = safe_str(cfg.get("notes", ""))

            # Build Experiment ID once; propagate everywhere
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            experiment_id = safe_slug(f"{genotype}_{gender}_{age_days}d_post{post_surg_h}h_{ts}", 96)

            save_dir = Path(cfg.get("save_dir") or DEFAULT_SAVE_DIR)
            root_dir = save_dir / f"{safe_slug(cfg.get('name','protocol'))}_{experiment_id}"
            root_dir.mkdir(parents=True, exist_ok=True)

            # Save protocol settings + experiment metadata
            proto_used = dict(cfg)
            proto_used.update({
                "experiment_id": experiment_id,
                "timestamp": ts,
                "baseline_sec": base_sec,
                "odor_sec": odor_sec,
                "post_sec": post_sec,
                "iti_wait_sec": iti_wait,
                "inter_wait_sec": inter_wait,
            })
            with open(root_dir / "protocol_used.json", "w", encoding="utf-8") as f:
                json.dump(proto_used, f, indent=2)

            # Pre-run hardware sanity checks (no odors sent)
            warm_ok, warm = self.mm.warmup_capture(n_frames=50, fps=fps)
            esp_ok, esp_msg = (True, "not connected")
            if self.esp32 and self.esp32.connected:
                esp_ok, esp_msg = self.esp32.ping(timeout=0.8)

            msg = [
                f"Experiment ID: {experiment_id}",
                "",
                "Pre-run sanity check:",
                f"- Camera warm-up: {'OK' if warm_ok else 'FAIL'}",
            ]
            if warm_ok:
                msg += [
                    f"  • frames={warm.get('frames')}  dropped={warm.get('dropped_frames')}",
                    f"  • fps target={warm.get('fps_target'):.2f}  measured={warm.get('fps_measured') if warm.get('fps_measured') else 'n/a'}",
                    f"  • mean={warm.get('mean_intensity')}  saturation%={warm.get('saturation_pct')}",
                ]
            else:
                msg += [f"  • error={warm.get('error')}"]

            msg += [
                f"- ESP32 link: {'OK' if esp_ok else 'FAIL'} ({esp_msg})",
                "",
                "Proceed to start the protocol?"
            ]

            if not ui_confirm("Pre-run check", "\n".join(str(x) for x in msg)):
                return

            # Ensure shutter/light ready before starting
            self._protocol_pause_popup(0, context_text="Start experiment")

            summary_rows = []
            git_hash = get_git_hash()

            # Structure recording: Capture reference images with RFP light before starting trials
            structure_dir = root_dir / "structure"
            structure_dir.mkdir(parents=True, exist_ok=True)
            
            if not self._capture_structure_images(structure_dir, fps):
                if not ui_confirm("Structure capture", "Structure recording failed or was skipped.\n\nContinue with protocol anyway?"):
                    return

            for fly in fly_ids:
                fly_dir = root_dir / f"fly_{safe_slug(fly)}"
                if genotype:
                    fly_dir = root_dir / f"fly_{safe_slug(fly)}_geno_{safe_slug(genotype)}"
                fly_dir.mkdir(parents=True, exist_ok=True)

                # Randomized block schedule: each odor appears once per block (repeat)
                odor_sequence = build_block_sequence(odors, repeats)
                last_trial_dir = None

                for t_idx, odor in enumerate(odor_sequence, start=1):
                    if self.protocol_abort_flag or self.protocol_abort.is_set():
                        raise RuntimeError("Protocol aborted.")

                    # Ensure any replay from the previous trial is stopped before recording
                    self._stop_protocol_replay(clear=True)

                    odor_name = ODOR_HUMAN_NAMES.get(odor, odor)
                    trial_dir = fly_dir / f"trial_{t_idx:03d}_{safe_slug(odor)}"
                    trial_dir.mkdir(parents=True, exist_ok=True)
                    (trial_dir / "images").mkdir(parents=True, exist_ok=True)

                    # Update status: Starting trial
                    completed_odors = [build_block_sequence(odors, repeats)[i] for i in range(t_idx - 1)]
                    completed_str = ", ".join([ODOR_HUMAN_NAMES.get(o, o) for o in completed_odors[-3:]]) if completed_odors else "None"
                    if len(completed_odors) > 3:
                        completed_str = f"...{completed_str}"
                    
                    self._update_proto_status(
                        trial_text=f"Fly {fly} | Trial {t_idx}/{len(odor_sequence)}",
                        phase_text="Preparing...",
                        odor_text=f"{odor_name} ({odor})",
                        timing_text="Setting up acquisition",
                        camera_text="Initializing",
                        completed_text=f"{t_idx-1}/{len(odor_sequence)} | Last: {completed_str}"
                    )

                    logger = TimestampLogger(trial_dir / "timestamps.csv")
                    if self.esp32 and self.esp32.connected:
                        try:
                            self.esp32.attach_logger(logger)
                        except Exception:
                            pass

                    logger.log("TRIAL_START", "PROTOCOL", 0, odor, odor_sec, fly, genotype, 0, f"odor_name={odor_name}")

                    # Track acquisition state for status updates
                    acq_state = {"phase": "BASELINE", "start_time": time.time(), "frame_count": 0}
                    
                    # Progress/phase callbacks for protocol mode with status updates
                    def proto_prog_cb(current, total, phase):
                        acq_state["frame_count"] = current
                        elapsed = time.time() - acq_state["start_time"]
                        fps_actual = current / elapsed if elapsed > 0 else 0
                        self._update_proto_status(
                            camera_text=f"Recording: {current}/{total} frames ({fps_actual:.1f} fps)"
                        )
                    
                    def proto_phase_cb(phase):
                        acq_state["phase"] = phase
                        acq_state["start_time"] = time.time()
                        
                        phase_emoji = {"BASELINE": "📊", "ODOR": "💨", "POST": "📉"}
                        odor_status = "OFF" if phase != "ODOR" else "ON"
                        
                        phase_durations = {"BASELINE": base_sec, "ODOR": odor_sec, "POST": post_sec}
                        duration = phase_durations.get(phase, 0)
                        
                        self._update_proto_status(
                            phase_text=f"{phase_emoji.get(phase, '⚡')} {phase} (Odor: {odor_status})",
                            timing_text=f"{phase} phase: 0/{duration}s"
                        )
                    
                    phases = [("BASELINE", base_sec), ("ODOR", odor_sec), ("POST", post_sec)]
                    success, msg, metrics = self.mm.acquire_multiphase(
                        fps=fps,
                        base_sec=base_sec,
                        odor_sec=odor_sec,
                        post_sec=post_sec,
                        save_dir=trial_dir / "images",
                        logger=logger,
                        odor=odor,
                        fly=fly,
                        geno=genotype,
                        esp32=self.esp32,
                        prog_cb=proto_prog_cb,
                        phase_cb=proto_phase_cb,
                        save_video=bool(cfg.get("save_video")),
                        frame_cb=None
                    )

                    # *** IMMEDIATELY turn off shutter after recording completes ***
                    if t_idx < len(odor_sequence):
                        self._start_protocol_replay(trial_dir, fps)
                        self._update_proto_status(
                            phase_text="⏸️ Recording complete - TURN OFF SHUTTER",
                            timing_text=f"Inter-trial pause: {inter_wait}s countdown"
                        )
                        self._protocol_pause_popup(
                            inter_wait,
                            context_text="Next trial",
                            on_ready=lambda: self._stop_protocol_replay(clear=True)
                        )
                        self._stop_protocol_replay(clear=True)

                    # Trial metadata JSON (one per trial)
                    trial_meta = {
                        "experiment_id": experiment_id,
                        "protocol_name": cfg.get("name", ""),
                        "fly_id": str(fly),
                        "genotype": genotype,
                        "gender": gender,
                        "age_days": age_days,
                        "post_surgery_hours": post_surg_h,
                        "notes": notes,
                        "odor_code": odor,
                        "odor_name": odor_name,
                        "phases": phases,
                        "timestamps_csv": str((trial_dir / "timestamps.csv").resolve()),
                        "save_dir": str(trial_dir.resolve()),
                        "software_version": SOFTWARE_VERSION,
                        "git_hash": git_hash,
                        "camera": {
                            "exposure_ms": float(self.mm.core.get_exposure()) if self.mm and self.mm.connected else None,
                            "binning": None,
                            "pixel_type": metrics.get("pixel_type") if isinstance(metrics, dict) else "",
                        },
                        "acquisition": metrics if isinstance(metrics, dict) else {},
                        "success": bool(success),
                        "message": str(msg),
                    }
                    try:
                        # best-effort read binning
                        trial_meta["camera"]["binning"] = self.mm.core.get_property(self.mm.camera, "Binning")
                    except Exception:
                        pass

                    with open(trial_dir / "trial.json", "w", encoding="utf-8") as f:
                        json.dump(trial_meta, f, indent=2)

                    summary_rows.append({
                        "experiment_id": experiment_id,
                        "protocol": cfg.get("name", ""),
                        "fly_id": str(fly),
                        "genotype": genotype,
                        "odor_code": odor,
                        "odor_name": odor_name,
                        "trial_index": t_idx,
                        "fps_target": metrics.get("fps_target") if isinstance(metrics, dict) else "",
                        "fps_measured": metrics.get("fps_measured") if isinstance(metrics, dict) else "",
                        "exposure_ms": trial_meta["camera"]["exposure_ms"],
                        "pixel_type": trial_meta["camera"]["pixel_type"],
                        "frames_captured": metrics.get("frames_captured") if isinstance(metrics, dict) else "",
                        "dropped_frames": metrics.get("dropped_frames") if isinstance(metrics, dict) else "",
                        "odor_on_ts_esp": metrics.get("odor_on_ts_esp") if isinstance(metrics, dict) else "",
                        "odor_off_ts_esp": metrics.get("odor_off_ts_esp") if isinstance(metrics, dict) else "",
                        "timestamps_csv": str((trial_dir / "timestamps.csv").name),
                        "trial_dir": str(trial_dir.name),
                        "success": bool(success),
                        "message": str(msg),
                    })

                    if not success:
                        ui_error("Trial failed", f"Fly {fly} / {odor}:\n{msg}")
                        raise RuntimeError(f"Trial failed: {fly} {odor}")

                    # Update status: Trial completed successfully
                    self._update_proto_status(
                        phase_text="✅ Trial complete",
                        timing_text=f"Saved {metrics.get('frames_captured', '?')} frames",
                        camera_text=f"Success - {metrics.get('fps_measured', '?'):.1f} fps avg" if isinstance(metrics, dict) and metrics.get('fps_measured') else "Success"
                    )
                    last_trial_dir = trial_dir

                # Optional: between flies, force a short pause for re-focus
                if fly != fly_ids[-1]:
                    if last_trial_dir is not None:
                        self._start_protocol_replay(last_trial_dir, fps)
                    self._protocol_pause_popup(0, context_text="Next fly", on_ready=lambda: self._stop_protocol_replay(clear=True))
                    self._stop_protocol_replay(clear=True)

            # Structure recording: Capture reference images with RFP light after completing all trials
            structure_post_dir = root_dir / "structure_post"
            structure_post_dir.mkdir(parents=True, exist_ok=True)
            
            if not self._capture_structure_images(structure_post_dir, fps):
                if not ui_confirm("Post-Structure capture", "Post-protocol structure recording failed or was skipped.\n\nContinue with saving anyway?"):
                    raise RuntimeError("Post-structure capture aborted by user")

            # Write protocol_summary.csv (one row per trial)
            try:
                if summary_rows:
                    fieldnames = list(summary_rows[0].keys())
                    with open(root_dir / "protocol_summary.csv", "w", newline="", encoding="utf-8") as f:
                        w = csv.DictWriter(f, fieldnames=fieldnames)
                        w.writeheader()
                        for r in summary_rows:
                            w.writerow(r)
            except Exception as e:
                ui_error("Summary write failed", f"Could not write protocol_summary.csv:\n{e}")

            ui_info("Protocol complete", f"Saved to:\n{root_dir}")
            self._update_proto_status(
                trial_text="Protocol Complete! ✅",
                phase_text="Finished",
                odor_text="All trials done",
                timing_text=f"Total trials: {len(summary_rows)}",
                camera_text="Idle",
                completed_text=f"{len(summary_rows)}/{len(summary_rows)}"
            )
            
            # Auto-sync to remote server if configured
            if self.remote_sync.enabled:
                if ui_confirm("Sync to Server?", f"Protocol complete!\n\nSync data to remote server?\n\nFolder: {root_dir.name}"):
                    self._sync_directory_to_server(root_dir)

        except Exception as e:
            ui_error("Protocol stopped", str(e))
            self._update_proto_status(
                trial_text="Protocol Stopped ⚠️",
                phase_text="Error/Aborted",
                odor_text="—",
                timing_text=str(e)[:50],
                camera_text="Idle",
                completed_text="Incomplete"
            )
        finally:
            self.protocol_running = False
            self.protocol_abort_flag = False
            self._stop_protocol_replay(clear=True)
            # Reset UI state on main thread
            def _reset_ui():
                try:
                    self.proto_status.config(text="")
                    self.run_proto_btn.config(state=tk.NORMAL)
                    self.save_proto_btn.config(state=tk.NORMAL)
                    self.load_proto_file_btn.config(state=tk.NORMAL)
                    self.preview_btn.config(state=tk.NORMAL)
                    # Clear status after a delay
                    self.root.after(5000, self._clear_proto_status)
                except Exception:
                    pass
            self.root.after(0, _reset_ui)
    
    def _update_sync_status(self):
        """Update remote sync status label."""
        if hasattr(self, 'sync_status'):
            try:
                # Check if rclone is configured (don't crash if it fails)
                available, info = self.remote_sync.check_availability()
                if available:
                    status = f"☁️ Sync enabled: {info} → \\\\10.229.137.184\\RamanFiles\\cole\\imaging"
                    self.sync_status.config(text=status, fg=Theme.SUCCESS)
                else:
                    self.sync_status.config(text=f"☁️ {info}", fg=Theme.WARNING)
            except Exception as e:
                # Silently fail - don't crash the app
                self.sync_status.config(text="☁️ Sync not configured (optional)", fg=Theme.TEXT_DARK)
    
    def _show_sync_dialog(self):
        """Show dialog to test sync and view status."""
        dialog = tk.Toplevel(self.root)
        dialog.title("Remote Server Sync Status")
        dialog.configure(bg=Theme.BG_MAIN)
        dialog.geometry("550x350")
        try:
            dialog.transient(self.root)
            dialog.grab_set()
        except Exception:
            pass
        
        frm = tk.Frame(dialog, bg=Theme.BG_MAIN, padx=20, pady=20)
        frm.pack(fill=tk.BOTH, expand=True)
        theme_manager.register(frm, 'BG_MAIN')
        
        tk.Label(frm, text="☁️ Remote Server Sync", font=("Segoe UI", 13, "bold"),
                bg=Theme.BG_MAIN, fg=Theme.NAVY_DARK).pack(anchor="w", pady=(0, 15))
        
        # Check status
        available, info = self.remote_sync.check_availability()
        
        if available:
            tk.Label(frm, text="✅ Sync is configured and ready!", font=("Segoe UI", 11, "bold"),
                    bg=Theme.BG_MAIN, fg=Theme.SUCCESS).pack(anchor="w", pady=(0, 10))
            
            tk.Label(frm, text=f"Status: {info}", font=("Segoe UI", 9),
                    bg=Theme.BG_MAIN, fg=Theme.TEXT_DARK).pack(anchor="w", pady=2)
            
            tk.Label(frm, text="Destination: \\\\10.229.137.184\\RamanFiles\\cole\\imaging", 
                    font=("Segoe UI", 9),
                    bg=Theme.BG_MAIN, fg=Theme.TEXT_DARK).pack(anchor="w", pady=2)
            
            tk.Label(frm, text="\nData will automatically sync after each protocol completes.",
                    font=("Segoe UI", 9), bg=Theme.BG_MAIN, fg=Theme.TEXT_DARK,
                    justify=tk.LEFT).pack(anchor="w", pady=(10, 0))
            
            tk.Label(frm, text="Only new/changed files are uploaded (saves time & bandwidth).",
                    font=("Segoe UI", 9), bg=Theme.BG_MAIN, fg=Theme.TEXT_DARK).pack(anchor="w", pady=2)
            
        else:
            tk.Label(frm, text="⚠️ Sync not configured", font=("Segoe UI", 11, "bold"),
                    bg=Theme.BG_MAIN, fg=Theme.WARNING).pack(anchor="w", pady=(0, 10))
            
            tk.Label(frm, text=f"Status: {info}", font=("Segoe UI", 9),
                    bg=Theme.BG_MAIN, fg=Theme.TEXT_DARK).pack(anchor="w", pady=2)
            
            tk.Label(frm, text="\nTo set up sync:", font=("Segoe UI", 9, "bold"),
                    bg=Theme.BG_MAIN, fg=Theme.TEXT_DARK).pack(anchor="w", pady=(10, 5))
            
            steps = [
                "1. Close this application",
                "2. Run: UPDATE_SYNC_PATH.bat",
                "3. Enter your password when prompted",
                "4. Restart this application"
            ]
            for step in steps:
                tk.Label(frm, text=step, font=("Segoe UI", 9),
                        bg=Theme.BG_MAIN, fg=Theme.TEXT_DARK).pack(anchor="w", pady=1, padx=(10, 0))
        
        # Test connection button
        test_status = tk.Label(frm, text="", font=("Segoe UI", 8),
                              bg=Theme.BG_MAIN, fg=Theme.TEXT_DARK, anchor="w")
        test_status.pack(fill=tk.X, pady=(15, 5))
        theme_manager.register(test_status, 'BG_MAIN', 'TEXT_DARK')
        
        def test_connection():
            test_status.config(text="Testing connection...", fg=Theme.TEXT_DARK)
            dialog.update()
            
            try:
                result = subprocess.run(
                    ["rclone", "lsd", "ramanlab:RamanFiles/cole/imaging"],
                    capture_output=True, timeout=10, text=True
                )
                if result.returncode == 0:
                    test_status.config(text="✅ Connection successful!", fg=Theme.SUCCESS)
                else:
                    test_status.config(text=f"❌ Connection failed: {result.stderr[:100]}", fg=Theme.ERROR)
            except Exception as e:
                test_status.config(text=f"❌ Error: {str(e)}", fg=Theme.ERROR)
        
        # Buttons
        btn_frame = tk.Frame(frm, bg=Theme.BG_MAIN)
        btn_frame.pack(side=tk.BOTTOM, fill=tk.X, pady=(15, 0))
        theme_manager.register(btn_frame, 'BG_MAIN')
        
        if available:
            self._btn(btn_frame, "Test Connection", test_connection, "secondary").pack(side=tk.LEFT)
        
        self._btn(btn_frame, "Close", lambda: dialog.destroy()).pack(side=tk.RIGHT)
        
        dialog.wait_window()
    
    def _sync_directory_to_server(self, local_dir):
        """Sync a directory to remote server in background thread."""
        if not self.remote_sync.enabled:
            messagebox.showwarning("Sync Not Configured", 
                                  "Please configure remote sync settings first.\n\n"
                                  "Click 'Sync to Server' button to set up.")
            return
        
        local_dir = Path(local_dir)
        
        # Create progress dialog
        progress_dialog = tk.Toplevel(self.root)
        progress_dialog.title("Syncing to Server")
        progress_dialog.configure(bg=Theme.BG_MAIN)
        progress_dialog.geometry("600x300")
        try:
            progress_dialog.transient(self.root)
            progress_dialog.grab_set()
        except Exception:
            pass
        
        frm = tk.Frame(progress_dialog, bg=Theme.BG_MAIN, padx=20, pady=20)
        frm.pack(fill=tk.BOTH, expand=True)
        theme_manager.register(frm, 'BG_MAIN')
        
        tk.Label(frm, text="☁️ Syncing to Remote Server", font=("Segoe UI", 12, "bold"),
                bg=Theme.BG_MAIN, fg=Theme.NAVY_DARK).pack(pady=(0, 10))
        
        status_label = tk.Label(frm, text=f"Preparing to sync: {local_dir.name}",
                               font=("Segoe UI", 9), bg=Theme.BG_MAIN, fg=Theme.TEXT_DARK,
                               justify=tk.LEFT, anchor="w")
        status_label.pack(fill=tk.X, pady=(0, 10))
        theme_manager.register(status_label, 'BG_MAIN', 'TEXT_DARK')
        
        # Progress text area
        progress_text = tk.Text(frm, height=10, width=70, bg=Theme.BG_INPUT, 
                               fg=Theme.TEXT_DARK, relief=tk.FLAT, font=("Consolas", 8))
        progress_text.pack(fill=tk.BOTH, expand=True, pady=(0, 10))
        theme_manager.register(progress_text, 'BG_INPUT', 'TEXT_DARK')
        
        result_label = tk.Label(frm, text="", font=("Segoe UI", 9, "bold"),
                               bg=Theme.BG_MAIN, fg=Theme.TEXT_DARK)
        result_label.pack(pady=(5, 0))
        theme_manager.register(result_label, 'BG_MAIN', 'TEXT_DARK')
        
        close_btn = self._btn(frm, "Close", lambda: progress_dialog.destroy(), "secondary")
        close_btn.config(state=tk.DISABLED)
        close_btn.pack(pady=(10, 0))
        
        def progress_callback(message):
            """Thread-safe progress updates."""
            def _update():
                try:
                    progress_text.insert(tk.END, message + "\n")
                    progress_text.see(tk.END)
                    progress_dialog.update_idletasks()  # Safe: only updates display, doesn't process events
                except Exception:
                    pass
            progress_dialog.after(0, _update)
        
        def do_sync():
            try:
                success, message, stats = self.remote_sync.sync_directory(
                    local_dir, 
                    progress_callback
                )
                
                def _finish():
                    if success:
                        result_label.config(text=f"✅ Sync Complete! {message}", fg=Theme.SUCCESS)
                        self._log(f"Synced to server: {local_dir.name}", "SUCCESS")
                    else:
                        result_label.config(text=f"❌ Sync Failed: {message}", fg=Theme.ERROR)
                        self._log(f"Sync failed: {message}", "ERROR")
                    close_btn.config(state=tk.NORMAL)
                
                progress_dialog.after(0, _finish)
                
            except Exception as e:
                def _error():
                    result_label.config(text=f"❌ Error: {str(e)}", fg=Theme.ERROR)
                    close_btn.config(state=tk.NORMAL)
                progress_dialog.after(0, _error)
        
        # Start sync in background thread
        threading.Thread(target=do_sync, daemon=True).start()
            
    def _toggle_dark(self, silent=False):
        new_theme = DarkTheme if self.dark_var.get() else LightTheme
        theme_manager.set_theme(new_theme)
        
        # Update log text tags
        self.log_text.tag_configure('SUCCESS', foreground=Theme.SUCCESS)
        self.log_text.tag_configure('ERROR', foreground=Theme.ERROR)
        self.log_text.tag_configure('WARNING', foreground=Theme.WARNING)
        
        # Update status indicators
        self._update_status()
        if not silent:
            self._log(f"Theme: {Theme.name}", "SUCCESS")
    
    # =========================================================================
    # CAMERA CALLBACKS
    # =========================================================================
    def _apply_exposure(self):
        """Apply the exposure time from the entry field to the camera"""
        try:
            exp = float(self.exp_var.get())
            if exp < 0.1:
                exp = 0.1
                self.exp_var.set("0.1")
            elif exp > 10000:
                exp = 10000
                self.exp_var.set("10000")
            
            if self.mm and self.mm.connected:
                self.mm.set_exposure(exp)
                self._log(f"Exposure set: {exp:.1f}ms → {1000/exp:.1f} FPS max", "SUCCESS")
        except ValueError:
            self._log("Invalid exposure value", "ERROR")
    
    def _update_fps_indicator(self, *args):
        """Update the FPS indicator based on current exposure value"""
        try:
            exp = float(self.exp_var.get())
            if exp > 0:
                max_fps = 1000 / exp
                if max_fps >= 1:
                    self.fps_indicator.config(text=f"→ {max_fps:.1f} FPS max")
                else:
                    self.fps_indicator.config(text=f"→ {max_fps:.2f} FPS max")
        except (ValueError, ZeroDivisionError):
            self.fps_indicator.config(text="→ -- FPS")
    
    def _on_gain_change(self, val):
        gain = int(float(val))
        self.gain_lbl.config(text=str(gain))
        if self.mm and self.mm.connected:
            self.mm.set_gain(gain)
    
    def _on_bin_change(self, event=None):
        if self.mm and self.mm.connected:
            self.mm.set_binning(self.bin_var.get())
            self._update_res_label()
    
    def _update_res_label(self):
        if self.mm and self.mm.connected:
            self.res_lbl.config(text=f"{self.mm.img_w} x {self.mm.img_h}")
    
    def _run_auto_exp(self):
        if not self.mm or not self.mm.connected:
            messagebox.showwarning("Not Connected", "Connect to camera first")
            return
        
        self.auto_status.config(text="Running auto-exposure...", fg=Theme.WARNING)
        self.auto_btn.config(state=tk.DISABLED)
        self.root.update()
        
        def update_cb(msg):
            self.root.after(0, lambda: self.auto_status.config(text=msg))
        
        def run():
            success, msg = self.mm.auto_exposure(callback=update_cb)
            
            def finish():
                self.auto_btn.config(state=tk.NORMAL)
                if success:
                    new_exp = self.mm.get_exposure()
                    self.exp_var.set(f"{new_exp:.1f}")
                    self._update_fps_indicator()
                    self.auto_status.config(text=f"✓ {msg}", fg=Theme.SUCCESS)
                    self._log(msg, "SUCCESS")
                else:
                    self.auto_status.config(text=f"✗ {msg}", fg=Theme.ERROR)
                    self._log(msg, "ERROR")
            
            self.root.after(0, finish)
        
        threading.Thread(target=run, daemon=True).start()
    
    def _apply_cam_roi(self):
        if not self.mm or not self.mm.connected:
            messagebox.showwarning("Not Connected", "Connect to camera first")
            return
        if not self.current_roi:
            messagebox.showinfo("No ROI", "Draw an ROI on the preview first")
            return
        
        x, y, w, h = self.current_roi
        success, msg = self.mm.set_camera_roi(x, y, w, h)
        if success:
            self.cam_roi_lbl.config(text=f"{self.mm.img_w}x{self.mm.img_h}")
            self._update_res_label()
            self._log(msg, "SUCCESS")
        else:
            self._log(msg, "ERROR")
            messagebox.showerror("ROI Error", msg)
    
    def _clear_cam_roi(self):
        if not self.mm or not self.mm.connected:
            return
        success, msg = self.mm.clear_camera_roi()
        if success:
            self.cam_roi_lbl.config(text="Full")
            self._update_res_label()
            self._log(msg, "SUCCESS")
        else:
            self._log(msg, "ERROR")
    
    def _update_camera_controls(self):
        if not self.mm or not self.mm.connected:
            return
        
        # Update exposure entry with current camera value
        current_exp = self.mm.get_exposure()
        self.exp_var.set(f"{current_exp:.1f}")
        self._update_fps_indicator()
        
        if self.mm.has_gain:
            self.gain_slider.config(from_=self.mm.gain_min, to=self.mm.gain_max, state=tk.NORMAL)
            self.gain_slider.set(self.mm.get_gain())
        else:
            self.gain_slider.config(state=tk.DISABLED)
        
        self.bin_combo['values'] = self.mm.binning_opts
        self.bin_var.set(self.mm.get_binning())
        self._update_res_label()
    
    # =========================================================================
    # OTHER CALLBACKS
    # =========================================================================
    def _log(self, msg, level="INFO"):
        ts = datetime.now().strftime('%H:%M:%S')
        self.log_text.insert(tk.END, f"[{ts}] {msg}\n", level)
        self.log_text.see(tk.END)
    
    def _refresh_ports(self):
        ports = self.esp32.get_ports()
        self.port_combo['values'] = ports if ports else ['--']
        if ports and self.port_var.get() not in ports:
            self.port_var.set(ports[0])
    
    def _connect_esp32(self):
        if self.esp32.connected:
            self.esp32.disconnect()
            self._log("ESP32 disconnected")
        else:
            success, msg = self.esp32.connect(self.port_var.get())
            self._log(msg, "SUCCESS" if success else "ERROR")
        self._update_status()
    
    def _connect_mm(self):
        config = self.config_var.get()
        if not Path(config).exists():
            messagebox.showerror("Error", "Config file not found")
            return
        
        self._log("Connecting to Micro-Manager...")
        self.mm = MicroManagerController(config)
        success, msg = self.mm.connect()
        
        if success:
            self._log(msg, "SUCCESS")
            self._update_camera_controls()
        else:
            self._log(msg, "ERROR")
            messagebox.showerror("Error", msg)
        
        self._update_status()
        self._update_disk()
    
    def _browse_config(self):
        f = filedialog.askopenfilename(filetypes=[("Config", "*.cfg")])
        if f:
            self.config_var.set(f)
    
    def _browse_save(self):
        d = filedialog.askdirectory()
        if d:
            self.save_var.set(d)
            self._update_disk()
    
    def _update_disk(self):
        try:
            path = Path(self.save_var.get())
            if path.exists():
                _, _, free = shutil.disk_usage(path)
                free_gb = free / (1024 ** 3)
                color = Theme.ERROR if free_gb < MIN_DISK_GB else Theme.SUCCESS
                self.disk_lbl.config(text=f"💾 {free_gb:.1f} GB free", fg=color)
        except:
            pass
    
    def _toggle_preview(self):
        if not self.mm or not self.mm.connected:
            messagebox.showwarning("Not Connected", "Connect to camera first")
            return
        if self.protocol_running:
            self._log("Preview disabled during protocol (replay shown between trials).", "WARNING")
            return
        
        if self.preview_active:
            self.mm.stop_preview()
            self.preview_active = False
            self.preview_btn.config(text="▶ Start Preview")
            self.preview_panel.set_status("Stopped", Theme.TEXT_MUTED)
        else:
            self.mm.start_preview(self._on_preview_frame)
            self.preview_active = True
            self.preview_btn.config(text="⏸ Stop Preview")
            self.preview_panel.set_status("Running", Theme.SUCCESS)
    
    def _on_preview_frame(self, img, raw, stats):
        self.root.after(0, lambda: self.preview_panel.update_image(img, raw, stats))
    
    def _take_snapshot(self):
        if not self.mm or not self.mm.connected:
            return
        img, raw = self.mm.snap_frame()
        if img:
            stats = self.mm.get_image_stats(raw)
            self.preview_panel.update_image(img, raw, stats)
            ts = datetime.now().strftime('%Y%m%d_%H%M%S')
            path = Path(self.save_var.get()) / f"snapshot_{ts}.tif"
            path.parent.mkdir(parents=True, exist_ok=True)
            img.save(path)
            self._log(f"Snapshot: {path.name}", "SUCCESS")
    
    def _toggle_roi(self):
        self.preview_panel.roi_enabled = not self.preview_panel.roi_enabled
        self.roi_btn.config(text="🔲 ..." if self.preview_panel.roi_enabled else "🔲 ROI")
    
    def _clear_roi(self):
        self.preview_panel.clear_roi()
        self.current_roi = None
    
    def _on_roi_change(self, roi):
        self.current_roi = roi
        self.preview_panel.roi_enabled = False
        self.roi_btn.config(text="🔲 ROI")
        if roi:
            self._log(f"ROI: {roi[2]}x{roi[3]} @ ({roi[0]},{roi[1]})", "SUCCESS")
    
    def _toggle_crosshair(self):
        self.preview_panel.show_crosshair = self.crosshair_var.get()
    
    def _update_status(self):
        if self.esp32.connected:
            self.esp32_ind.itemconfig("ind", fill=Theme.SUCCESS)
            self.esp32_lbl.config(text=self.esp32.port_name, fg=Theme.SUCCESS)
            self.esp32_btn.config(text="Disconnect")
        else:
            self.esp32_ind.itemconfig("ind", fill=Theme.ERROR)
            self.esp32_lbl.config(text="--", fg=Theme.TEXT_MUTED)
            self.esp32_btn.config(text="Connect")
        
        if self.mm and self.mm.connected:
            self.mm_ind.itemconfig("ind", fill=Theme.SUCCESS)
            self.mm_lbl.config(text="Connected", fg=Theme.SUCCESS)
            self.mm_btn.config(text="Disconnect")
        else:
            self.mm_ind.itemconfig("ind", fill=Theme.ERROR)
            self.mm_lbl.config(text="--", fg=Theme.TEXT_MUTED)
            self.mm_btn.config(text="Connect")
        
        both = self.esp32.connected and self.mm and self.mm.connected
        self.rec_btn.config(state=tk.NORMAL if both and not self.recording else tk.DISABLED)
    
    def _get_params(self):
        try:
            return True, {
                'fps': int(self.fps_var.get()),
                'exposure_ms': float(self.exp_var.get()),
                'baseline_sec': float(self.base_var.get()),
                'odor_duration_sec': float(self.odor_dur_var.get()),
                'post_odor_sec': float(self.post_var.get()),
                'odor': self.odor_var.get(),
                'fly_id': self.fly_var.get(),
                'genotype': self.geno_var.get(),
                'trial': self.trial_var.get(),
                'notes': self.notes_var.get(),
                'save_video': self.video_var.get()
            }
        except Exception as e:
            return False, str(e)
    
    def _start_recording(self):
        valid, result = self._get_params()
        if not valid:
            messagebox.showerror("Error", result)
            return
        
        # Check disk
        try:
            path = Path(self.save_var.get())
            if path.exists():
                _, _, free = shutil.disk_usage(path)
                if free / (1024 ** 3) < MIN_DISK_GB:
                    if not messagebox.askyesno("Low Disk", f"Only {free / (1024 ** 3):.1f} GB free. Continue?"):
                        return
        except:
            pass
        
        params = result
        self.last_params = params
        
        if self.preview_active:
            self.mm.stop_preview()
            self.preview_active = False
            self.preview_btn.config(text="▶ Start Preview")
        
        ts = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
        fly = params['fly_id'] or 'unknown'
        folder = f"{ts}_fly-{fly}_trial-{params['trial']}"
        self.session_dir = Path(self.save_var.get()) / folder
        self.session_dir.mkdir(parents=True, exist_ok=True)
        
        self.logger = TimestampLogger(self.session_dir / "log.csv")
        
        self.recording = True
        self.rec_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        self.iti_lbl.config(text="")
        self.prog_base.config(width=0)
        self.prog_odor.config(width=0)
        self.prog_post.config(width=0)
        
        self.mm.set_exposure(params['exposure_ms'])
        
        self._log(f"Recording: {folder}", "SUCCESS")
        
        threading.Thread(target=self._recording_thread, args=(params,), daemon=True).start()
    
    def _recording_thread(self, params):
        def prog_cb(current, total, phase):
            self.root.after(0, lambda: self._update_progress(current, total, phase, params))
        
        def phase_cb(phase):
            self.root.after(0, lambda: self.preview_panel.set_phase(phase))
        
        def frame_cb(img, raw):
            stats = self.mm.get_image_stats(raw) if raw is not None else {}
            self.root.after(0, lambda: self.preview_panel.update_image(img, raw, stats))
        
        success, msg, metrics = self.mm.acquire_multiphase(
            fps=params['fps'],
            base_sec=params['baseline_sec'],
            odor_sec=params['odor_duration_sec'],
            post_sec=params['post_odor_sec'],
            save_dir=self.session_dir,
            logger=self.logger,
            odor=params['odor'],
            fly=params['fly_id'],
            geno=params['genotype'],
            esp32=self.esp32,
            prog_cb=prog_cb,
            phase_cb=phase_cb,
            save_video=params['save_video'],
            frame_cb=frame_cb
        )
        
        stats = self.logger.get_stats()
        SessionManager.save_metadata(self.session_dir, params, stats, self.current_roi)
        SessionManager.generate_script(self.session_dir)
        
        if success:
            self.root.after(0, lambda: self._recording_complete(stats))
        else:
            self.root.after(0, lambda: self._recording_failed(msg))
    
    def _update_progress(self, current, total, phase, params):
        self.prog_lbl.config(text=f"{phase}: {current}/{total}")
        
        try:
            fps = params['fps']
            b = int(params['baseline_sec'] * fps)
            o = int(params['odor_duration_sec'] * fps)
            w = self.prog_container.winfo_width()
            
            if phase == "BASELINE":
                self.prog_base.config(width=int(current / total * w), bg=Theme.PHASE_BASELINE)
            elif phase == "ODOR":
                self.prog_base.config(width=int(b / total * w), bg=Theme.PHASE_BASELINE)
                self.prog_odor.config(width=int((current - b) / total * w), bg=Theme.PHASE_ODOR)
            elif phase == "POST-ODOR":
                self.prog_base.config(width=int(b / total * w), bg=Theme.PHASE_BASELINE)
                self.prog_odor.config(width=int(o / total * w), bg=Theme.PHASE_ODOR)
                self.prog_post.config(width=int((current - b - o) / total * w), bg=Theme.PHASE_POST)
        except:
            pass
    
    def _recording_complete(self, stats):
        self.recording = False
        self.rec_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)
        self.repeat_btn.config(state=tk.NORMAL)
        self.preview_panel.set_phase("")
        
        self.prog_lbl.config(text="✓ Complete!")
        self._log(f"Done: {self.session_dir.name}", "SUCCESS")
        
        if stats:
            dropped = stats.get('dropped', 0)
            self.timing_lbl.config(
                text=f"⚠️ {dropped} dropped" if dropped else "✓ No drops",
                fg=Theme.WARNING if dropped else Theme.SUCCESS
            )
        
        try:
            self.trial_var.set(str(int(self.trial_var.get()) + 1))
        except:
            pass
        
        try:
            import winsound
            winsound.MessageBeep()
        except:
            pass
        
        if self.iti_enabled.get():
            try:
                self._start_iti(int(self.iti_var.get()))
            except:
                pass
        
        self._update_status()
        self._update_disk()
    
    def _recording_failed(self, msg):
        self.recording = False
        self.rec_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)
        self.preview_panel.set_phase("")
        self.prog_lbl.config(text="✗ Failed")
        self._log(msg, "ERROR")
        messagebox.showerror("Error", msg)
        self._update_status()
    
    def _stop_recording(self):
        self._log("Stop requested", "WARNING")
        # Abort protocol runner (if active)
        try:
            self.protocol_abort.set()
            self.protocol_abort_flag = True  # Also set the flag checked in the loop
            self.protocol_running = False
        except Exception:
            pass
        self._stop_protocol_replay(clear=True)
        if self.mm:
            self.mm.abort()
        if self.esp32.connected:
            self.esp32.send_stop()
        if self.iti_timer:
            self.iti_timer.stop()
            self.iti_lbl.config(text="")
    
    def _start_iti(self, duration):
        def on_tick(remaining):
            self.root.after(0, lambda: self.iti_lbl.config(text=f"⏳ {remaining}s"))
        
        def on_complete():
            self.root.after(0, lambda: self.iti_lbl.config(text="✓ Ready"))
            try:
                import winsound
                winsound.Beep(1000, 200)
            except:
                pass
        
        self.iti_timer = ITITimer(duration, on_tick, on_complete)
        self.iti_timer.start()
    
    def _quick_repeat(self):
        if self.last_params and not self.recording:
            self._start_recording()
    
    def _open_folder(self):
        folder = self.session_dir or Path(self.save_var.get())
        if folder.exists():
            subprocess.run(['explorer', str(folder)])
    
    def _on_close(self):
        if self.recording:
            if not messagebox.askyesno("Recording", "Stop and exit?"):
                return
            self._stop_recording()
        
        if self.iti_timer:
            self.iti_timer.stop()
        if self.mm:
            self.mm.disconnect()
        if self.esp32.connected:
            self.esp32.disconnect()
        
        self.root.destroy()

# =============================================================================
# MAIN
# =============================================================================
def main():
    print("=" * 60)
    print("SNNE Lab - Drosophila Olfactory Recording v10.0")
    print("=" * 60)
    print("\nNew in v10.0:")
    print("  - Continuous acquisition across phases (no gaps)")
    print("  - Protocol replay preview between trials")
    print("  - Default 9 FPS at 100ms exposure")
    print("\nShortcuts: Space=Next Trial (protocol) | Escape=Stop | F5=Repeat | F11=Fullscreen\n")
    
    root = tk.Tk()
    app = OdorRecorderGUI(root)
    root.mainloop()

if __name__ == "__main__":
    main()
