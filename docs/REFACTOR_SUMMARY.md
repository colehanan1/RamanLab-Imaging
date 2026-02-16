# Acquisition Refactor Summary

## What Changed

### BEFORE: Implicit Timing

```python
# Old code (pre-refactor)
def acquire_multiphase(self, fps, base_sec, odor_sec, post_sec, ...):
    # Exposure was NOT explicitly configured before acquisition
    exposure_ms = self.core.get_exposure()  # Whatever it was before

    # FPS was enforced only in software (wall-clock pacing)
    target_interval_s = 1.0 / fps

    # Acquisition started without explicit timing setup
    self.core.start_continuous_sequence_acquisition(0)

    # Frames selected at target_interval_s via next_frame_wall
```

**Problems:**
- ❌ Exposure could be from a previous recording or preview
- ❌ Camera frame-rate property was never queried or set
- ❌ Timing relied entirely on software (vulnerable to system hiccups)
- ❌ No clear log of what timing was actually configured

---

### AFTER: Explicit Hardware + Software Timing

```python
# New code (post-refactor)
def acquire_multiphase(self, fps, base_sec, odor_sec, post_sec, ..., exposure_ms=5.0):
    # 1. EXPLICITLY configure camera timing
    ok_timing, msg_timing, exposure_actual = self.configure_acquisition_timing(
        exposure_ms=exposure_ms,      # 5.0 ms (HARDWARE LEVEL)
        target_fps=fps                # 20 fps (HARDWARE LEVEL if available)
    )

    # 2. Log what was actually set
    logger.log("ACQ_TIMING", "CONFIG", ...,
              f"fps_target={fps} exposure_ms={exposure_actual:.2f}")

    # 3. Start acquisition (camera already configured)
    self.core.start_continuous_sequence_acquisition(0)

    # 4. Frame pacing acts as a safety net (drains excess frames)
```

**Improvements:**
- ✅ Exposure ALWAYS set to 5.0 ms before acquisition starts
- ✅ Camera FrameRate property is queried and set if available
- ✅ Timing is hardware-controlled (more reliable)
- ✅ Software pacing is a fallback, not the primary mechanism
- ✅ Timing configuration is logged and verified

---

## Method: `configure_acquisition_timing()`

### Signature

```python
def configure_acquisition_timing(self, exposure_ms=5.0, target_fps=20.0):
    """
    Configure camera for high-speed acquisition.

    Returns: (success: bool, message: str, exposure_actual_ms: float)
    """
```

### What It Does (In Order)

1. **Sets exposure** (ALWAYS):
   ```python
   self.core.set_exposure(float(exposure_ms))  # e.g., 5.0
   ```
   - This is mandatory and enforced immediately
   - Camera cannot acquire faster than `1000 / exposure_ms` fps

2. **Queries for FrameRate property**:
   - Tries `"FrameRate"`, `"AcquisitionFrameRate"`, or `"Frame Rate"`
   - Checks for allowed values (constraints)
   - Sets the closest valid value to `target_fps`

3. **Handles three scenarios**:

   | Scenario | Outcome | Acquisition Mode |
   |----------|---------|------------------|
   | **Has FrameRate property** | Sets to 20 Hz (or allowed closest value) | Hardware-paced at 20 fps |
   | **No FrameRate property** | Logs warning | Free-running, software-paced |
   | **Error during setup** | Returns success=False | Fails before acquisition starts |

### Example Output (Console)

```
[ACQUISITION] Exposure set to 5.00 ms
[ACQUISITION] Frame rate set via 'FrameRate': 20.0 Hz
```

Or if fallback:

```
[ACQUISITION] Exposure set to 5.00 ms
[ACQUISITION] No FrameRate property found; camera free-running at max speed for 5.0ms exposure
[ACQUISITION] Target 20 fps will be enforced in software via frame pacing
```

---

## Parameter Changes

### `acquire_multiphase()` - New Parameter

| Parameter | Type | Default | Purpose |
|-----------|------|---------|---------|
| `exposure_ms` | float | 5.0 | Camera exposure in milliseconds |

**Usage:**

```python
# Default (5.0 ms):
controller.acquire_multiphase(fps=20.0, ..., exposure_ms=5.0)

# Custom (e.g., 10 ms for dimmer samples):
controller.acquire_multiphase(fps=20.0, ..., exposure_ms=10.0)

# Can be overridden per-recording while defaults are set globally:
controller.set_acquisition_defaults(exposure_ms=7.5, fps=15.0)
controller.acquire_multiphase(..., exposure_ms=7.5)  # Or use defaults
```

---

## Logging Output

### New Log Entries

**`configure_acquisition_timing()` success:**

```
ACQ_TIMING | CONFIG | 0 | OFM_A | 4.0 | fly_001 | WT | 0 | fps_target=20 exposure_ms=5.00 (camera hardware paced)
```

**`configure_acquisition_timing()` failure:**

```
ACQ_CONFIG_FAILED | ERROR | 0 | OFM_A | 4.0 | fly_001 | WT | 0 | Failed to set exposure: camera not connected
```

---

## Frame Pacing Logic (Unchanged But Now Better Documented)

The frame selection loop in `acquire_multiphase()` now has detailed comments:

```python
# === CONTINUOUS ACQUISITION WITH OPTIONAL SOFTWARE PACING ===
# HARDWARE TIMING:
#   - Camera exposure is fixed at exposure_ms (e.g., 5.0 ms)
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

## Metrics Tracking

### New Metric

`metrics["exposure_ms_set"]` — Records the exposure that was actually set before acquisition

```python
metrics = {
    ...,
    "exposure_ms_set": 5.00,  # NEW
    "fps_target": 20.0,
    "fps_measured": 19.8,
    ...
}
```

---

## Backward Compatibility

### ✅ No Breaking Changes (Mostly)

**If you call without `exposure_ms`:**
```python
# Old code still works (uses default 5.0 ms):
success, msg, metrics = controller.acquire_multiphase(
    fps=20.0,
    base_sec=15.0,
    odor_sec=4.0,
    ...
)
```

**If you want different exposure:**
```python
# New parameter makes it explicit:
success, msg, metrics = controller.acquire_multiphase(
    fps=20.0,
    base_sec=15.0,
    odor_sec=4.0,
    ...,
    exposure_ms=10.0  # NOW EXPLICIT (was implicit before)
)
```

### ⚠️ Watch Out For

If your UI creates `MicroManagerController` instances, they now have two new instance variables:

```python
self._default_exposure_ms = 5.0
self._default_fps = 20.0
```

These are set in `__init__()` and don't affect old code, but you can now use them:

```python
controller.set_acquisition_defaults(exposure_ms=7.5, fps=15.0)
```

---

## Testing Recommendations

### 1. Verify Exposure is Set

```python
# Check camera exposure before and after acquisition
before = controller.core.get_exposure()
print(f"Exposure before: {before} ms")

controller.acquire_multiphase(fps=20.0, ..., exposure_ms=5.0)

after = controller.core.get_exposure()
print(f"Exposure after: {after} ms")  # Should be 5.0
```

### 2. Monitor Frame Rate

```python
metrics = controller.acquire_multiphase(
    fps=20.0,
    base_sec=10.0,  # 200 frames at 20 fps
    ...,
    exposure_ms=5.0
)

# Should see ~20 fps measured (±5%)
measured_fps = metrics["fps_measured"]
print(f"Target FPS: {metrics['fps_target']}")
print(f"Measured FPS: {measured_fps:.1f}")
print(f"Error: {abs(measured_fps - 20.0) / 20.0 * 100:.1f}%")
```

### 3. Check for Dropped Frames

```python
dropped = metrics["dropped_frames"]
captured = metrics["frames_captured"]

if dropped > 0:
    print(f"WARNING: {dropped} dropped frames out of {captured}")
else:
    print("✓ No dropped frames")
```

### 4. Verify Logs

Check the timestamp log (CSV) for:
- `ACQ_TIMING | CONFIG` entry showing what was set
- `FRAME_*` entries evenly spaced (~50 ms apart for 20 fps)
- No `ACQ_CONFIG_FAILED` entries

---

## For The UI Team

### In "Run" Tab

When starting a recording, add these lines BEFORE calling `acquire_multiphase()`:

```python
# Set acquisition defaults once
self.controller.set_acquisition_defaults(exposure_ms=5.0, fps=20.0)

# Or pass explicit values
success, msg, metrics = self.controller.acquire_multiphase(
    fps=self.fps_spinbox.get(),
    base_sec=self.baseline_spinbox.get(),
    odor_sec=self.odor_spinbox.get(),
    post_sec=self.post_spinbox.get(),
    save_dir=self.session_dir,
    logger=self.logger,
    odor=self.odor_dropdown.get(),
    fly=self.fly_entry.get(),
    geno=self.geno_entry.get(),
    esp32=self.esp32,
    prog_cb=self.update_progress,
    phase_cb=self.update_phase,
    exposure_ms=self.exposure_spinbox.get(),  # NEW: Allow UI control
    frame_cb=self.update_preview
)
```

### In "Protocol Runner" Tab

Set defaults once at protocol start:

```python
def start_protocol(self):
    # Configure timing for all trials in this protocol
    self.controller.set_acquisition_defaults(
        exposure_ms=self.protocol_exposure.get(),
        fps=self.protocol_fps.get()
    )

    # Run all trials with these settings
    for trial in range(num_trials):
        self.controller.acquire_multiphase(
            fps=self.protocol_fps.get(),
            ...,
            # exposure_ms not needed here—uses defaults
        )
```

---

## Summary of Code Locations

| What | Where | Line Range |
|------|-------|-----------|
| New method `configure_acquisition_timing()` | `MicroManagerController` | ~875-935 |
| Updated `acquire_multiphase()` signature | `MicroManagerController` | ~1230 |
| Timing config call | `acquire_multiphase()` | ~1280-1290 |
| Hardware vs. software timing comments | `acquire_multiphase()` | ~1340-1360 |
| New method `set_acquisition_defaults()` | `MicroManagerController` | ~1620-1635 |
| Initialize defaults in `__init__()` | `MicroManagerController.__init__()` | ~795-800 |

---

## FAQ

**Q: What if my camera doesn't have a FrameRate property?**
A: It will free-run at max speed, and software pacing will select frames at the target FPS. You'll see a warning in the console but acquisition will proceed.

**Q: Can I change exposure between trials?**
A: Yes! Each call to `acquire_multiphase(exposure_ms=X)` reconfigures the camera. You can vary exposure per-odor or per-fly.

**Q: What's the maximum FPS I can achieve?**
A: `max_fps = 1000 / exposure_ms`. At 5 ms exposure, max is 200 fps. To get higher FPS, reduce exposure or use binning.

**Q: Do I need to call `set_acquisition_defaults()`?**
A: No, but it's convenient if you want all recordings to use the same settings. You can always pass `exposure_ms` explicitly per-acquisition.

**Q: Will this work with my camera?**
A: Yes! The fallback to software pacing ensures it works with any camera. If your camera has FrameRate support, great! If not, the software pacing takes over.

---
