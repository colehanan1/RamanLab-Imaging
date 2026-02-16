# Code Locations Reference

## Quick Index to Refactored Code

### 1. New Timing Configuration Method

**File:** `mm_odor_recorder_v9.py`
**Class:** `MicroManagerController`
**Method:** `configure_acquisition_timing()`
**Lines:** ~880-947

**What it does:**
- Sets camera exposure to 5 ms (or specified value)
- Attempts to set FrameRate property to 20 fps (or specified value)
- Provides fallback message if no FrameRate property available

**Key code:**
```python
# Lines 902-903: SET EXPOSURE
self.core.set_exposure(float(exposure_ms))
exposure_set = self.core.get_exposure()

# Lines 910-933: ATTEMPT TO SET FRAME RATE
for frame_rate_prop in ["FrameRate", "AcquisitionFrameRate", "Frame Rate"]:
    try:
        if self.core.has_property(self.camera, frame_rate_prop):
            # ... set frame rate ...
```

---

### 2. Updated Main Acquisition Method

**File:** `mm_odor_recorder_v9.py`
**Class:** `MicroManagerController`
**Method:** `acquire_multiphase()`
**Lines:** ~1241-1505

**New signature:**
```python
def acquire_multiphase(self, fps, base_sec, odor_sec, post_sec, save_dir, logger,
                      odor, fly, geno, esp32, prog_cb, phase_cb, save_video=False,
                      frame_cb=None, exposure_ms=5.0):  # ← NEW PARAMETER
```

**Key sections:**

| Section | Lines | Purpose |
|---------|-------|---------|
| Docstring | ~1244-1267 | Explains new timing control approach |
| Metrics initialization | ~1297-1312 | Now includes `exposure_ms_set` |
| Call `configure_acquisition_timing()` | ~1318-1329 | **CRITICAL**: Sets exposure and frame rate before acquisition |
| Log timing config | ~1331-1333 | Logs what was actually set |
| Comment block: Hardware vs. Software | ~1394-1407 | Explains frame pacing logic |
| Continuous acquisition startup | ~1411 | `self.core.start_continuous_sequence_acquisition(0)` |
| Frame pacing loop | ~1421-1490 | Uses `next_frame_wall` for timing |

---

### 3. Acquisition Defaults Storage

**File:** `mm_odor_recorder_v9.py`
**Class:** `MicroManagerController`

#### Initialization (in `__init__`)
**Lines:** ~795-800

```python
# Acquisition timing defaults (5 ms exposure, 20 fps target)
self._default_exposure_ms = 5.0
self._default_fps = 20.0
```

#### Setter Method
**Method:** `set_acquisition_defaults()`
**Lines:** ~1620-1635

```python
def set_acquisition_defaults(self, exposure_ms=5.0, fps=20.0):
    """
    Convenience method to store default acquisition parameters.
    """
    self._default_exposure_ms = float(exposure_ms)
    self._default_fps = float(fps)
    print(f"[ACQUISITION] Defaults set: {exposure_ms} ms exposure, {fps} fps target")
```

---

## Key Code Snippets

### Setting Exposure (Mandatory)

**Location:** `configure_acquisition_timing()`, line 902-903

```python
# THIS IS MANDATORY - must happen before any acquisition
self.core.set_exposure(float(exposure_ms))  # e.g., 5.0 ms
exposure_set = self.core.get_exposure()     # Verify it was set
print(f"[ACQUISITION] Exposure set to {exposure_set:.2f} ms")
```

**Purpose:** Tells the camera to use 5 ms exposure time. This is the foundational limit for frame rate:
- At 5 ms exposure → max ~200 fps
- At 10 ms exposure → max ~100 fps
- At 1 ms exposure → max ~1000 fps

---

### Setting Frame Rate (Optional but Preferred)

**Location:** `configure_acquisition_timing()`, line 910-933

```python
frame_rate_set = False
for frame_rate_prop in ["FrameRate", "AcquisitionFrameRate", "Frame Rate"]:
    try:
        if self.core.has_property(self.camera, frame_rate_prop):
            # Query allowed values (constraints)
            allowed = self.core.get_allowed_property_values(self.camera, frame_rate_prop)
            if allowed.size() > 0:
                # Use closest allowed value
                allowed_list = [float(allowed.get(i)) for i in range(allowed.size())]
                closest_fps = min(allowed_list, key=lambda x: abs(x - target_fps))
                self.core.set_property(self.camera, frame_rate_prop, str(closest_fps))
            else:
                # No constraints; set directly
                self.core.set_property(self.camera, frame_rate_prop, str(float(target_fps)))

        fps_actual = self.core.get_property(self.camera, frame_rate_prop)
        print(f"[ACQUISITION] Frame rate set via '{frame_rate_prop}': {fps_actual} Hz")
        frame_rate_set = True
        break
    except Exception:
        continue
```

**Purpose:** If your camera has a FrameRate property, this locks the acquisition to exactly 20 fps in hardware. If not, it falls back gracefully.

---

### Frame Pacing Loop (Software Timing Fallback)

**Location:** `acquire_multiphase()`, line 1421-1490

```python
target_interval_s = 1.0 / max(float(fps), 0.001)  # e.g., 0.05 s for 20 fps
next_frame_wall = acq_start_wall  # Wall-clock time for next kept frame

while global_frame < total:
    if self.abort_flag.is_set():
        self.core.stop_sequence_acquisition()
        raise InterruptedError("Aborted")

    now = time.time()
    available = self.core.get_remaining_image_count()

    if available > 0 and now >= next_frame_wall:  # ← KEY TIMING CHECK
        try:
            # Pop frames from buffer, keep only one
            tagged = self.core.pop_next_tagged_image()
            while self.core.get_remaining_image_count() > 0:
                tagged = self.core.pop_next_tagged_image()  # Drain extras

            raw = self._tagged_to_raw(tagged).copy()
            capture_time = now

            # ... log frame, check phases, send odor commands ...

            all_frames.append(raw)
            all_times.append(capture_time)
            global_frame += 1

            # Schedule next frame time (prevents drift)
            next_frame_wall = acq_start_wall + (global_frame * target_interval_s)

        except Exception:
            dropped += 1

    elif available > 1:
        # Camera running ahead—drain excess frames
        while self.core.get_remaining_image_count() > 1:
            try:
                self.core.pop_next_tagged_image()
            except Exception:
                break
        time.sleep(0.001)
    else:
        time.sleep(0.001)
```

**Purpose:**
- If camera has hardware frame rate control → This loop just drains excess frames at a safe pace
- If camera free-running → This loop selects frames at target_fps by wall-clock timing

---

### Log Entries Generated

**Location:** `acquire_multiphase()`, line 1323-1327 and 1331-1333

**On success:**
```python
ok_timing, msg_timing, exposure_actual = self.configure_acquisition_timing(
    exposure_ms=exposure_ms, target_fps=fps
)
if not ok_timing:
    logger.log("ACQ_CONFIG_FAILED", "ERROR", 0, odor, odor_sec, fly, geno, 0, msg_timing)
```

**Logged as:**
```
ACQ_TIMING | CONFIG | 0 | OFM_A | 4.0 | fly_001 | WT | 0 | fps_target=20 exposure_ms=5.00 (camera hardware paced)
```

---

## Comments in Code

### Configure Timing (Mandatory Comment)
**Location:** `acquire_multiphase()`, line 1318-1322

```python
# ===== CONFIGURE CAMERA TIMING (CRITICAL) =====
# This sets:
#   1. Exposure time to 5.0 ms (hardware-level, mandatory)
#   2. Frame rate to 20 fps via FrameRate property if available (hardware-level, preferred)
#   3. Falls back to free-running with software pacing if no FrameRate property
```

### Hardware vs. Software Timing
**Location:** `acquire_multiphase()`, line 1394-1407

```python
# === CONTINUOUS ACQUISITION WITH OPTIONAL SOFTWARE PACING ===
# HARDWARE TIMING:
#   - Camera exposure is fixed at exposure_ms (e.g., 5.0 ms) via configure_acquisition_timing()
#   - If FrameRate property was set, camera will acquire at target_fps (20 Hz) in hardware
#
# SOFTWARE TIMING:
#   - If no FrameRate property, camera runs at max speed (~200 fps for 5 ms exposure)
#   - We use next_frame_wall (wall-clock based) to select only frames at target_fps
#   - This prevents buffer overflow and ensures consistent frame intervals
#
# FRAME BUFFER MANAGEMENT:
#   - Micro-Manager's circular buffer holds camera frames
#   - We pop frames when available, drain extras to prevent memory buildup
#   - Frame interval is measured from wall-clock time, not camera timestamps
```

---

## File Structure (Complete)

```
mm_odor_recorder_v9.py
├── Imports & Configuration (lines 1-134)
├── Themes (lines 145-205)
├── ThemeManager (lines 210-260)
├── TimestampLogger (lines 265-317)
├── SessionManager (lines 321-343)
├── ESP32Controller (lines 348-563)
├── RemoteSyncHandler (lines 567-764)
├── MicroManagerController (lines 769-1635)
│   ├── __init__() (line ~770) ← NEW: _default_exposure_ms, _default_fps
│   ├── connect() (line ~790)
│   ├── _cache_properties() (line ~805)
│   ├── disconnect() (line ~841)
│   ├── get_exposure() (line ~847)
│   ├── set_exposure() (line ~850)
│   ├── get_gain() (line ~859)
│   ├── set_gain() (line ~867)
│   ├── configure_acquisition_timing() (line ~880) ← NEW METHOD
│   ├── get_binning() (line ~949)
│   ├── set_binning() (line ~884)
│   ├── set_camera_roi() (line ~909)
│   ├── clear_camera_roi() (line ~933)
│   ├── auto_exposure() (line ~957)
│   ├── get_image_stats() (line ~1011)
│   ├── start_preview() (line ~1023)
│   ├── stop_preview() (line ~1038)
│   ├── _preview_loop_continuous() (line ~1055)
│   ├── _tagged_to_raw() (line ~1100)
│   ├── _process_image() (line ~1135)
│   ├── _extract_elapsed_ms() (line ~1230)
│   ├── acquire_multiphase() (line ~1241) ← UPDATED: now calls configure_acquisition_timing()
│   ├── warmup_capture() (line ~1506)
│   ├── _snap_single() (line ~1578)
│   ├── abort() (line ~1586)
│   └── set_acquisition_defaults() (line ~1620) ← NEW METHOD
├── LivePreviewPanel (lines 1592-1838)
├── ITITimer (lines 1842-1867)
├── OdorRecorderGUI (lines 1871-2000+)
└── ... rest of UI code ...
```

---

## How to Find Things

### "I want to set exposure"
→ See line ~902 in `configure_acquisition_timing()`

### "I want to set frame rate"
→ See lines ~910-933 in `configure_acquisition_timing()`

### "I want to understand frame pacing"
→ See lines ~1394-1407 (comments) and ~1421-1490 (code) in `acquire_multiphase()`

### "I want to see what gets logged"
→ See line ~1331-1333 in `acquire_multiphase()`

### "I want to customize timing per-recording"
→ See `acquire_multiphase()` parameter `exposure_ms` (line ~1243)

### "I want to set global defaults"
→ See `set_acquisition_defaults()` method (line ~1620)

### "I want to verify timing was set"
→ Check `metrics['exposure_ms_set']` (returned from `acquire_multiphase()`)

---

## Testing Checklist

- [ ] Call `configure_acquisition_timing(5.0, 20.0)` and verify console output
- [ ] Check that `self.core.get_exposure()` returns 5.0
- [ ] Run `acquire_multiphase()` with default exposure and verify metrics
- [ ] Run `acquire_multiphase()` with custom exposure (e.g., 10.0) and verify metrics
- [ ] Check timestamp log for "ACQ_TIMING | CONFIG" entry
- [ ] Verify measured FPS is within 5% of target (19-21 fps for 20 fps target)
- [ ] Confirm no "ACQ_CONFIG_FAILED" entries in log
- [ ] Test with different odors and verify timing is consistent
- [ ] Verify frame interval is ~50 ms (1/20 fps) in timestamp log

---
