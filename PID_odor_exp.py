#!/usr/bin/env python3
import time
import re
import pandas as pd
import lgpio
from datetime import datetime
import matplotlib.pyplot as plt
import serial
from pathlib import Path

# ─── CONFIG ─────────────────────────────────────────────
EXP_TIME = int(time.time())
print(f"ID: {EXP_TIME}")

OUT_DIR = Path("data_odor")
OUT_DIR.mkdir(parents=True, exist_ok=True)
CSV_FILE = OUT_DIR / f"odor_test_{EXP_TIME}.csv"
FIG_LOC  = OUT_DIR / f"gas_exp_{EXP_TIME}.png"

# Odor pins
OFM_A = 23
OFM_B = 22
OFM_H = 27
OFM_P = 17  # pump/common line
OFM_C = 12
OFM_L = 26
OFM_O = 16
OFM_E = 13

# Mapping odor labels → pins
ODORS = {
    "O": OFM_O,
}
PUMP_PIN = OFM_P

# Serial port settings for PID controller
tio_cmd = {
    'port': '/dev/ttyUSB0',
    'baudrate': 115200,
    'bytesize': serial.EIGHTBITS,
    'parity': serial.PARITY_NONE,
    'stopbits': serial.STOPBITS_ONE,
    'timeout': None  # block until data arrives
}

# Timing parameters
WARMUP_DURATION = 15
WARMUP_HERTZ = 5
WARMUP_T = 1.0 / WARMUP_HERTZ
STABILIZATION_DURATION = 15
HERTZ = 5
T = 1.0 / HERTZ
ODOR_ON_DURATION = 30.0
ODOR_OFF_DURATION = 300.0
NOT_RECORDING_DURATION = 0.0
REPEATS = 5
# ────────────────────────────────────────────────────────

# Regex to match 'Averaged Voltage: <value>'
voltage_pattern = re.compile(r"Averaged Voltage\s*:\s*([-+]?[0-9]*\.?[0-9]+)")

# 1) GPIO setup
chip = lgpio.gpiochip_open(0)
for pin in ODORS.values():
    lgpio.gpio_claim_output(chip, pin)
lgpio.gpio_claim_output(chip, PUMP_PIN)

def set_odor(odor: str, state: bool):
    """Turn one odor valve ON/OFF and sync pump."""
    for od, pin in ODORS.items():
        lgpio.gpio_write(chip, pin, 1 if (od == odor and state) else 0)
    lgpio.gpio_write(chip, PUMP_PIN, 1 if state else 0)

def all_valves_off():
    for pin in ODORS.values():
        lgpio.gpio_write(chip, pin, 0)
    lgpio.gpio_write(chip, PUMP_PIN, 0)

# 2) Serial handshake
ser = serial.Serial(**tio_cmd)
print("Reset ESP32 now, waiting for first 'Averaged Voltage' reading...")
ser.reset_input_buffer()

# Handshake loop
while True:
    raw = ser.readline().decode('utf-8', errors='ignore').strip()
    if not raw:
        continue
    match = voltage_pattern.search(raw)
    if match:
        first_val = float(match.group(1))
        print(f"Handshake voltage: {first_val}")
        break
    else:
        print(f"BOOT MSG: {raw}")

# 3) Prepare for data acquisition
records = []
overall_start_time = time.time()
print(f"Process start: {datetime.now():%H:%M:%S}")

# Function: block until a valid voltage line is read
def read_voltage(phase, odor, r):
    time_stamp = time.time() - overall_start_time
    while True:
        raw = ser.readline().decode('utf-8', errors='ignore').strip()
        if not raw:
            continue
        match = voltage_pattern.search(raw)
        if match:
            voltage = float(match.group(1))
            print(f"[{phase}] Odor={odor} r={r} t={time_stamp:.2f}s V={voltage}")
            return {
                'Timestamp': time_stamp,
                'Voltage': voltage,
                'Phase': phase,
                'Odor': odor,
                'Repeat': r
            }
        else:
            print(f"[WARN] Non-voltage line: {raw}")

# 4) Experiment: warmup, stabilization, odor cycles
print(f"Warming up ({WARMUP_DURATION}s at {WARMUP_HERTZ}Hz)")
phase, r = 'warmup', -1
end = time.time() + WARMUP_DURATION
while time.time() < end:
    records.append(read_voltage(phase, "none", r))
    time.sleep(WARMUP_T)

print(f"Stabilizing ({STABILIZATION_DURATION}s at {HERTZ}Hz)")
phase, r = 'stabilization', -1
end = time.time() + STABILIZATION_DURATION
while time.time() < end:
    records.append(read_voltage(phase, "none", r))
    time.sleep(T)

for odor in ODORS.keys():
    for r in range(REPEATS):
        print(f"[{odor}] Repeat {r}: ON for {ODOR_ON_DURATION}s")
        set_odor(odor, True)
        phase = 'odor_on'
        end = time.time() + ODOR_ON_DURATION
        while time.time() < end:
            records.append(read_voltage(phase, odor, r))
            time.sleep(T)

        print(f"[{odor}] Repeat {r}: OFF for {ODOR_OFF_DURATION}s")
        set_odor(odor, False)
        phase = 'odor_off'
        end = time.time() + ODOR_OFF_DURATION
        while time.time() < end:
            records.append(read_voltage(phase, odor, r))
            time.sleep(T)

        print(f"Pause ({NOT_RECORDING_DURATION}s)")
        time.sleep(NOT_RECORDING_DURATION)

# 5) Save to CSV
pd.DataFrame(records).to_csv(CSV_FILE, index=False)
print(f"Saved {len(records)} records to {CSV_FILE}")

# 6) Cleanup
ser.close()
all_valves_off()
for pin in ODORS.values():
    lgpio.gpio_free(chip, pin)
lgpio.gpio_free(chip, PUMP_PIN)
lgpio.gpiochip_close(chip)

# 7) Plot
df = pd.read_csv(CSV_FILE)
plt.figure(figsize=(12, 6))
plt.plot(df['Timestamp'], df['Voltage'], label='Voltage')
for odor in df['Odor'].unique():
    if odor == "none":
        continue
    sub = df[df['Odor'] == odor]
    for r in sub['Repeat'].unique():
        on = sub[(sub['Repeat'] == r) & (sub['Phase'] == 'odor_on')]
        if not on.empty:
            plt.axvspan(on['Timestamp'].min(), on['Timestamp'].max(), color='red', alpha=0.2, label=f"{odor} r{r}")
plt.xlabel('Time (s)')
plt.ylabel('Voltage (V)')
plt.title(f"PID Sampling {HERTZ} Hz (all odors, pump controlled)")
plt.legend()
plt.grid(True)
plt.tight_layout()
plt.savefig(FIG_LOC)
print(f"Plot saved to {FIG_LOC}")

