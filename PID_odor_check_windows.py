#!/usr/bin/env python3
"""
Windows PID odor verification experiment.

This script combines:
1. The odor-delivery command style from mm_odor_recorder_v11.py:
      SET_PIN:<odor_code>:<duration_sec>
   sent to both the odor valve and OFM_P (carrier).
2. The PID voltage logging flow from PID_odor_exp.py.

Default run structure:
- Warmup: 15 s at 5 Hz
- Stabilization: 15 s at 5 Hz
- For each odor in the imaging panel:
    - Send odor + OFM_P for 30 s
    - Record PID during odor-on and for 300 s after
    - Repeat once more

Outputs:
- One CSV per odor delivery
- One optional PNG plot per odor delivery
- One startup baseline CSV/plot
- One run_summary.csv with confirmation fields from the ESP32

Example:
    python PID_odor_check_windows.py --esp-port COM3 --pid-port COM4
"""

from __future__ import annotations

import argparse
import csv
import queue
import re
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Deque, Dict, List, Optional, Tuple

try:
    import serial
    import serial.tools.list_ports
except ImportError as exc:
    raise SystemExit(
        "pyserial is required for this script. Install it with:\n"
        "python -m pip install pyserial"
    ) from exc

try:
    import matplotlib.pyplot as plt
    MATPLOTLIB_AVAILABLE = True
except ImportError:
    MATPLOTLIB_AVAILABLE = False


DEFAULT_ESP32_PORT = "COM3"
DEFAULT_PID_PORT = "COM4"
DEFAULT_BAUD_RATE = 115200

ODOR_SEQUENCE = ["OFM_A", "OFM_B", "OFM_C", "OFM_H", "OFM_L", "OFM_O", "OFM_E"]
CARRIER_PIN = "OFM_P"

WARMUP_DURATION = 15.0
STABILIZATION_DURATION = 15.0
SAMPLE_HZ = 5.0
ODOR_ON_DURATION = 30.0
ODOR_OFF_DURATION = 300.0
REPEATS_PER_ODOR = 2
PING_TIMEOUT = 0.8

PID_LINE_PATTERN = re.compile(r"Averaged Voltage\s*:\s*([-+]?[0-9]*\.?[0-9]+)")


def iso_now() -> str:
    dt = datetime.now()
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}"


def safe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value).strip())
    return cleaned.strip("_") or "x"


class ESP32Controller:
    """Minimal Windows serial controller matching the recorder script protocol."""

    def __init__(self) -> None:
        self.serial_port: Optional[serial.Serial] = None
        self.connected = False
        self.port_name = ""
        self._rx_thread: Optional[threading.Thread] = None
        self._rx_stop = threading.Event()
        self._rx_lock = threading.Lock()
        self._rx_queue: "queue.Queue[Tuple[str, str]]" = queue.Queue(maxsize=2000)
        self._recent_lines: Deque[Tuple[str, str]] = deque(maxlen=100)
        self._last_state: Dict[str, Dict[str, str]] = {}

    def connect(self, port: str, baudrate: int = DEFAULT_BAUD_RATE) -> Tuple[bool, str]:
        try:
            self.disconnect()
            self.serial_port = serial.Serial(port, baudrate, timeout=0.2)
            time.sleep(0.5)
            try:
                self.serial_port.reset_input_buffer()
            except Exception:
                pass
            self.port_name = port
            self.connected = True
            self._start_rx_thread()
            return True, f"Connected to {port}"
        except Exception as exc:
            return False, str(exc)

    def disconnect(self) -> None:
        self._stop_rx_thread()
        if self.serial_port and getattr(self.serial_port, "is_open", False):
            try:
                self.serial_port.close()
            except Exception:
                pass
        self.serial_port = None
        self.connected = False
        self.port_name = ""

    def _start_rx_thread(self) -> None:
        self._stop_rx_thread()
        if not (self.serial_port and self.connected):
            return

        self._rx_stop.clear()

        def _rx_loop() -> None:
            while not self._rx_stop.is_set():
                try:
                    if not (self.serial_port and self.serial_port.is_open):
                        time.sleep(0.05)
                        continue

                    line = self.serial_port.readline()
                    if not line:
                        continue

                    decoded = line.decode("utf-8", errors="ignore").strip()
                    if not decoded:
                        continue

                    ts = iso_now()
                    self._recent_lines.append((ts, decoded))

                    try:
                        self._rx_queue.put_nowait((ts, decoded))
                    except queue.Full:
                        try:
                            _ = self._rx_queue.get_nowait()
                        except Exception:
                            pass
                        try:
                            self._rx_queue.put_nowait((ts, decoded))
                        except Exception:
                            pass

                    parsed = self._parse_state_line(decoded)
                    if parsed:
                        odor, state = parsed
                        with self._rx_lock:
                            self._last_state.setdefault(odor, {})
                            self._last_state[odor][state] = ts
                            self._last_state[odor][f"RAW_{state}"] = decoded
                except Exception:
                    time.sleep(0.05)

        self._rx_thread = threading.Thread(target=_rx_loop, daemon=True)
        self._rx_thread.start()

    def _stop_rx_thread(self) -> None:
        self._rx_stop.set()
        thread = self._rx_thread
        self._rx_thread = None
        if thread and thread.is_alive():
            try:
                thread.join(timeout=0.5)
            except Exception:
                pass

    @staticmethod
    def _parse_state_line(line: str) -> Optional[Tuple[str, str]]:
        upper = line.strip().upper()
        upper = upper.replace(",", " ").replace(";", " ").replace("\t", " ").replace("|", " ")

        match = re.search(r"\b(ON|OFF)\b\s*[:= ]\s*(OFM_[A-Z])\b", upper)
        if match:
            return match.group(2), match.group(1)

        match = re.search(r"\bOFM_(ON|OFF)\b\s*[:= ]\s*(OFM_[A-Z])\b", upper)
        if match:
            return match.group(2), match.group(1)

        match = re.search(r"\bODOR_(ON|OFF)\b\s+(OFM_[A-Z])\b", upper)
        if match:
            return match.group(2), match.group(1)

        return None

    def _write_line(self, message: str) -> Tuple[bool, str]:
        if not (self.connected and self.serial_port):
            return False, "Not connected"
        try:
            payload = (message.strip() + "\n").encode("utf-8")
            self.serial_port.write(payload)
            self.serial_port.flush()
            return True, ""
        except Exception as exc:
            return False, str(exc)

    def ping(self, timeout: float = PING_TIMEOUT) -> Tuple[bool, str]:
        ok, err = self._write_line("PING")
        if not ok:
            return False, err

        start = time.time()
        seen: List[str] = []
        while (time.time() - start) < float(timeout):
            try:
                _ts, line = self._rx_queue.get_nowait()
                seen.append(line)
                if "PONG" in line.upper():
                    return True, "PONG"
            except queue.Empty:
                time.sleep(0.02)

        if seen:
            return True, "; ".join(seen[:5])
        return True, "no response"

    def send_odor(self, odor_code: str, duration_sec: float) -> Tuple[bool, str]:
        return self._write_line(f"SET_PIN:{odor_code}:{int(round(duration_sec))}")

    def send_stop(self) -> Tuple[bool, str]:
        return self._write_line("STOP")

    def get_last_state_ts(self, odor_code: str, state: str) -> str:
        with self._rx_lock:
            return self._last_state.get(str(odor_code).upper(), {}).get(str(state).upper(), "")

    def get_last_state_raw(self, odor_code: str, state: str) -> str:
        with self._rx_lock:
            return self._last_state.get(str(odor_code).upper(), {}).get(f"RAW_{str(state).upper()}", "")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Windows PID odor verification runner")
    parser.add_argument("--esp-port", default=DEFAULT_ESP32_PORT, help="ESP32 odor controller serial port")
    parser.add_argument("--pid-port", default=DEFAULT_PID_PORT, help="PID controller serial port")
    parser.add_argument("--esp-baud", type=int, default=DEFAULT_BAUD_RATE, help="ESP32 baud rate")
    parser.add_argument("--pid-baud", type=int, default=DEFAULT_BAUD_RATE, help="PID controller baud rate")
    parser.add_argument("--sample-hz", type=float, default=SAMPLE_HZ, help="PID sampling frequency")
    parser.add_argument("--warmup-sec", type=float, default=WARMUP_DURATION, help="Warmup duration")
    parser.add_argument("--stabilization-sec", type=float, default=STABILIZATION_DURATION, help="Stabilization duration")
    parser.add_argument("--odor-on-sec", type=float, default=ODOR_ON_DURATION, help="Seconds each odor stays on")
    parser.add_argument("--odor-off-sec", type=float, default=ODOR_OFF_DURATION, help="Seconds to keep recording after odor turns off")
    parser.add_argument("--repeats", type=int, default=REPEATS_PER_ODOR, help="Number of sends per odor")
    parser.add_argument("--odors", nargs="+", default=ODOR_SEQUENCE, help="Odor codes to run")
    parser.add_argument("--out-dir", default="", help="Optional output directory")
    parser.add_argument("--no-plots", action="store_true", help="Skip PNG plot output")
    parser.add_argument("--list-ports", action="store_true", help="List detected serial ports and exit")
    parser.add_argument("--check-only", action="store_true", help="Only verify ESP32 and PID serial communication, then exit")
    parser.add_argument("--pid-check-timeout", type=float, default=10.0, help="Seconds to wait for a PID voltage line in --check-only mode")
    return parser


def default_output_root() -> Path:
    return Path.home() / "Documents" / "Cole" / "Data" / "PID_Odor_Checks"


def normalize_odors(raw_odors: List[str]) -> List[str]:
    odors = [str(odor).strip().upper() for odor in raw_odors if str(odor).strip()]
    if not odors:
        raise ValueError("No odors provided.")
    if CARRIER_PIN in odors:
        raise ValueError(
            f"{CARRIER_PIN} is the shared carrier line and cannot be tested by itself. "
            "Use only odor valves such as OFM_A, OFM_B, OFM_C, OFM_E, OFM_H, OFM_O, OFM_L."
        )
    return odors


def print_serial_ports() -> None:
    ports = list(serial.tools.list_ports.comports())
    if not ports:
        print("No serial ports detected.")
        return
    print("Detected serial ports:")
    for port in ports:
        desc = port.description or "Unknown device"
        hwid = port.hwid or ""
        print(f"  {port.device} | {desc} | {hwid}")


def read_voltage(
    pid_serial: serial.Serial,
    overall_start: float,
    phase: str,
    odor: str,
    repeat_idx: int,
    trial_index: int,
) -> Dict[str, object]:
    while True:
        raw = pid_serial.readline().decode("utf-8", errors="ignore").strip()
        if not raw:
            continue

        rel_ts = time.monotonic() - overall_start
        match = PID_LINE_PATTERN.search(raw)
        if match:
            voltage = float(match.group(1))
            print(
                f"[{phase}] Odor={odor} r={repeat_idx} trial={trial_index} "
                f"t={rel_ts:.2f}s V={voltage}",
                flush=True,
            )
            return {
                "Timestamp": round(rel_ts, 3),
                "Voltage": voltage,
                "Phase": phase,
                "Odor": odor,
                "Repeat": repeat_idx,
            }

        print(f"[WARN] Non-voltage PID line: {raw}")


def wait_for_first_voltage(pid_serial: serial.Serial, timeout_sec: Optional[float] = None) -> float:
    print("Waiting for first PID voltage reading...")
    try:
        pid_serial.reset_input_buffer()
    except Exception:
        pass

    start = time.monotonic()
    while True:
        if timeout_sec is not None and (time.monotonic() - start) > float(timeout_sec):
            raise TimeoutError(f"No PID voltage line received within {timeout_sec} seconds")
        raw = pid_serial.readline().decode("utf-8", errors="ignore").strip()
        if not raw:
            continue
        match = PID_LINE_PATTERN.search(raw)
        if match:
            first_value = float(match.group(1))
            print(f"Handshake voltage: {first_value}")
            return first_value
        print(f"[BOOT] {raw}")


def snapshot_esp_states(esp32: ESP32Controller, odor: str) -> Dict[str, str]:
    return {
        "odor_on_prev": esp32.get_last_state_ts(odor, "ON"),
        "odor_off_prev": esp32.get_last_state_ts(odor, "OFF"),
        "carrier_on_prev": esp32.get_last_state_ts(CARRIER_PIN, "ON"),
        "carrier_off_prev": esp32.get_last_state_ts(CARRIER_PIN, "OFF"),
    }


def update_confirmations(
    esp32: ESP32Controller,
    odor: str,
    previous: Dict[str, str],
    meta: Dict[str, object],
) -> None:
    checks = [
        ("odor_on_prev", odor, "ON", "esp_odor_on_ts", "esp_odor_on_raw", "odor_on_confirmed"),
        ("odor_off_prev", odor, "OFF", "esp_odor_off_ts", "esp_odor_off_raw", "odor_off_confirmed"),
        ("carrier_on_prev", CARRIER_PIN, "ON", "esp_carrier_on_ts", "esp_carrier_on_raw", "carrier_on_confirmed"),
        ("carrier_off_prev", CARRIER_PIN, "OFF", "esp_carrier_off_ts", "esp_carrier_off_raw", "carrier_off_confirmed"),
    ]

    for prev_key, odor_code, state, ts_key, raw_key, flag_key in checks:
        current_ts = esp32.get_last_state_ts(odor_code, state)
        if current_ts and current_ts != previous[prev_key] and not meta[flag_key]:
            meta[flag_key] = True
            meta[ts_key] = current_ts
            meta[raw_key] = esp32.get_last_state_raw(odor_code, state)
            print(f"[ESP32] {odor_code} {state} confirmed at {current_ts}")


def sample_phase(
    pid_serial: serial.Serial,
    overall_start: float,
    duration_sec: float,
    sample_period_sec: float,
    phase: str,
    odor: str,
    repeat_idx: int,
    trial_index: int,
    esp32: Optional[ESP32Controller] = None,
    esp_previous: Optional[Dict[str, str]] = None,
    trial_meta: Optional[Dict[str, object]] = None,
) -> List[Dict[str, object]]:
    records: List[Dict[str, object]] = []
    end = time.monotonic() + float(duration_sec)
    while time.monotonic() < end:
        if esp32 and esp_previous and trial_meta:
            update_confirmations(esp32, odor, esp_previous, trial_meta)
        records.append(read_voltage(pid_serial, overall_start, phase, odor, repeat_idx, trial_index))
        if esp32 and esp_previous and trial_meta:
            update_confirmations(esp32, odor, esp_previous, trial_meta)
        time.sleep(sample_period_sec)
    return records


def write_csv(path: Path, rows: List[Dict[str, object]], extra_columns: Optional[Dict[str, object]] = None) -> None:
    if not rows:
        return

    merged_rows: List[Dict[str, object]] = []
    for row in rows:
        combined = dict(row)
        if extra_columns:
            combined.update(extra_columns)
        merged_rows.append(combined)

    fieldnames = list(merged_rows[0].keys())
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(merged_rows)


def save_plot(path: Path, rows: List[Dict[str, object]], title: str) -> None:
    if not MATPLOTLIB_AVAILABLE or not rows:
        return

    x = [float(row["Timestamp"]) for row in rows]
    y = [float(row["Voltage"]) for row in rows]

    plt.figure(figsize=(12, 5))
    plt.plot(x, y, linewidth=1.2, label="PID Voltage")

    odor_on_points = [float(row["Timestamp"]) for row in rows if row["Phase"] == "odor_on"]
    if odor_on_points:
        plt.axvspan(min(odor_on_points), max(odor_on_points), color="red", alpha=0.2, label="Odor ON")

    plt.xlabel("Elapsed Time (s)")
    plt.ylabel("Voltage (V)")
    plt.title(title)
    plt.grid(True, alpha=0.3)
    plt.legend()
    plt.tight_layout()
    plt.savefig(path)
    plt.close()


def compute_summary(rows: List[Dict[str, object]]) -> Dict[str, object]:
    voltages = [float(row["Voltage"]) for row in rows]
    return {
        "sample_count": len(rows),
        "voltage_min": min(voltages) if voltages else "",
        "voltage_max": max(voltages) if voltages else "",
        "voltage_mean": (sum(voltages) / len(voltages)) if voltages else "",
    }


def run_trial(
    pid_serial: serial.Serial,
    esp32: ESP32Controller,
    overall_start: float,
    run_started_at: str,
    odor: str,
    repeat_idx: int,
    trial_index: int,
    args: argparse.Namespace,
    run_dir: Path,
) -> Dict[str, object]:
    if odor == CARRIER_PIN:
        raise ValueError(f"{CARRIER_PIN} cannot be run as a standalone odor.")

    sample_period_sec = 1.0 / max(float(args.sample_hz), 0.001)
    esp_previous = snapshot_esp_states(esp32, odor)
    command_ts = iso_now()

    trial_meta: Dict[str, object] = {
        "run_started_at": run_started_at,
        "command_sent_ts": command_ts,
        "command_elapsed_sec": round(time.monotonic() - overall_start, 3),
        "odor_command_ok": False,
        "odor_command_error": "",
        "carrier_command_ok": False,
        "carrier_command_error": "",
        "odor_on_confirmed": False,
        "odor_off_confirmed": False,
        "carrier_on_confirmed": False,
        "carrier_off_confirmed": False,
        "esp_odor_on_ts": "",
        "esp_odor_off_ts": "",
        "esp_carrier_on_ts": "",
        "esp_carrier_off_ts": "",
        "esp_odor_on_raw": "",
        "esp_odor_off_raw": "",
        "esp_carrier_on_raw": "",
        "esp_carrier_off_raw": "",
    }

    print(f"[TRIAL {trial_index:03d}] {odor} repeat {repeat_idx}/{args.repeats}: ON for {args.odor_on_sec}s")
    odor_ok, odor_err = esp32.send_odor(odor, args.odor_on_sec)
    carrier_ok, carrier_err = esp32.send_odor(CARRIER_PIN, args.odor_on_sec)
    trial_meta["odor_command_ok"] = odor_ok
    trial_meta["odor_command_error"] = odor_err
    trial_meta["carrier_command_ok"] = carrier_ok
    trial_meta["carrier_command_error"] = carrier_err

    if not odor_ok:
        print(f"[WARN] Failed to send {odor}: {odor_err}")
    if not carrier_ok:
        print(f"[WARN] Failed to send {CARRIER_PIN}: {carrier_err}")

    rows = []
    rows.extend(
        sample_phase(
            pid_serial=pid_serial,
            overall_start=overall_start,
            duration_sec=args.odor_on_sec,
            sample_period_sec=sample_period_sec,
            phase="odor_on",
            odor=odor,
            repeat_idx=repeat_idx,
            trial_index=trial_index,
            esp32=esp32,
            esp_previous=esp_previous,
            trial_meta=trial_meta,
        )
    )

    print(f"[TRIAL {trial_index:03d}] {odor} repeat {repeat_idx}/{args.repeats}: OFF for {args.odor_off_sec}s")
    rows.extend(
        sample_phase(
            pid_serial=pid_serial,
            overall_start=overall_start,
            duration_sec=args.odor_off_sec,
            sample_period_sec=sample_period_sec,
            phase="odor_off",
            odor=odor,
            repeat_idx=repeat_idx,
            trial_index=trial_index,
            esp32=esp32,
            esp_previous=esp_previous,
            trial_meta=trial_meta,
        )
    )

    update_confirmations(esp32, odor, esp_previous, trial_meta)

    trial_stem = f"trial_{trial_index:03d}_{safe_name(odor)}_rep{repeat_idx}"
    csv_path = run_dir / f"{trial_stem}.csv"
    png_path = run_dir / f"{trial_stem}.png"

    summary = compute_summary(rows)
    write_csv(csv_path, rows)

    if not args.no_plots and MATPLOTLIB_AVAILABLE:
        save_plot(png_path, rows, f"{odor} repeat {repeat_idx} | PID odor check")
        plot_name = png_path.name
    else:
        plot_name = ""

    summary_row = {
        "trial_index": trial_index,
        "odor": odor,
        "repeat": repeat_idx,
        "csv_file": csv_path.name,
        "plot_file": plot_name,
        **summary,
        **trial_meta,
    }
    print(
        f"[TRIAL {trial_index:03d}] saved {csv_path.name} | "
        f"odor_on={trial_meta['odor_on_confirmed']} carrier_on={trial_meta['carrier_on_confirmed']} "
        f"odor_off={trial_meta['odor_off_confirmed']} carrier_off={trial_meta['carrier_off_confirmed']}"
    )
    return summary_row


def main() -> None:
    args = build_parser().parse_args()

    if args.list_ports:
        print_serial_ports()
        return

    run_started_at = iso_now()
    esp32 = ESP32Controller()
    pid_serial: Optional[serial.Serial] = None

    try:
        print(f"Connecting to ESP32 on {args.esp_port}...")
        ok, msg = esp32.connect(args.esp_port, args.esp_baud)
        if not ok:
            raise RuntimeError(f"ESP32 connect failed: {msg}")
        print(msg)

        ping_ok, ping_msg = esp32.ping()
        print(f"ESP32 ping: {'OK' if ping_ok else 'FAIL'} ({ping_msg})")

        print(f"Connecting to PID on {args.pid_port}...")
        pid_serial = serial.Serial(
            port=args.pid_port,
            baudrate=args.pid_baud,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=1.0,
        )
        wait_for_first_voltage(pid_serial, timeout_sec=args.pid_check_timeout if args.check_only else None)

        if args.check_only:
            print("")
            print("Serial check passed.")
            print(f"ESP32 odor controller responded on {args.esp_port}.")
            print(f"PID stream produced 'Averaged Voltage' lines on {args.pid_port}.")
            print("Use these same COM ports for the full experiment run.")
            return

        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_root = Path(args.out_dir) if args.out_dir else default_output_root()
        run_dir = output_root / f"pid_odor_check_{run_id}"
        run_dir.mkdir(parents=True, exist_ok=True)
        print(f"Output directory: {run_dir}")

        summary_rows: List[Dict[str, object]] = []
        startup_rows: List[Dict[str, object]] = []
        overall_start = time.monotonic()
        sample_period_sec = 1.0 / max(float(args.sample_hz), 0.001)

        print(f"Warmup for {args.warmup_sec}s at {args.sample_hz} Hz")
        startup_rows.extend(
            sample_phase(
                pid_serial=pid_serial,
                overall_start=overall_start,
                duration_sec=args.warmup_sec,
                sample_period_sec=sample_period_sec,
                phase="warmup",
                odor="none",
                repeat_idx=-1,
                trial_index=0,
            )
        )

        print(f"Stabilization for {args.stabilization_sec}s at {args.sample_hz} Hz")
        startup_rows.extend(
            sample_phase(
                pid_serial=pid_serial,
                overall_start=overall_start,
                duration_sec=args.stabilization_sec,
                sample_period_sec=sample_period_sec,
                phase="stabilization",
                odor="none",
                repeat_idx=-1,
                trial_index=0,
            )
        )

        startup_csv = run_dir / "startup_baseline.csv"
        write_csv(startup_csv, startup_rows)
        if not args.no_plots and MATPLOTLIB_AVAILABLE:
            save_plot(run_dir / "startup_baseline.png", startup_rows, "Startup baseline")

        trial_index = 1
        odors = normalize_odors(args.odors)
        for odor in odors:
            for repeat_idx in range(1, int(args.repeats) + 1):
                summary_rows.append(
                    run_trial(
                        pid_serial=pid_serial,
                        esp32=esp32,
                        overall_start=overall_start,
                        run_started_at=run_started_at,
                        odor=odor,
                        repeat_idx=repeat_idx,
                        trial_index=trial_index,
                        args=args,
                        run_dir=run_dir,
                    )
                )
                trial_index += 1

        if summary_rows:
            summary_path = run_dir / "run_summary.csv"
            write_csv(summary_path, summary_rows)
            print(f"Run summary saved to {summary_path}")

        print("Experiment complete.")
    except KeyboardInterrupt:
        print("Interrupted by user.")
    finally:
        try:
            esp32.send_stop()
        except Exception:
            pass
        esp32.disconnect()
        if pid_serial and getattr(pid_serial, "is_open", False):
            try:
                pid_serial.close()
            except Exception:
                pass


if __name__ == "__main__":
    main()
