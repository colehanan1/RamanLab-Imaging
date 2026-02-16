# High-Speed Acquisition Tuning Guide

## Overview

Your `MicroManagerController` class has been refactored to support **robust, camera-controlled high-speed acquisition** at 5 ms exposure and 20 fps using Micro-Manager's continuous sequence acquisition.

## Key Changes

### 1. New Method: `configure_acquisition_timing(exposure_ms, target_fps)`

**Location:** `MicroManagerController` class

**Purpose:** Explicitly configure the camera for high-speed acquisition before starting frame collection.

**What it does:**

1. **Sets exposure time** (MANDATORY):
   ```python
   self.core.set_exposure(5.0)  # 5 ms exposure
   ```
   - This is enforced at the **camera hardware level**
   - Non-negotiable: camera cannot acquire faster than 1/(exposure_ms/1000) fps
   - For 5 ms exposure → max ~200 fps

2. **Attempts to set frame rate** (PREFERRED):
   - Queries for `FrameRate`, `AcquisitionFrameRate`, or `Frame Rate` properties
   - Sets the closest allowed value to 20 fps
   - If multiple allowed values exist, picks the one nearest to target
   - If successful, camera will acquire at exactly 20 Hz in **hardware**

3. **Fallback to free-running** (if no FrameRate property):
   - Camera runs at max speed (~200 fps for 5 ms exposure)
   - Software pacing via wall-clock timing (`next_frame_wall`) selects frames at 20 fps
   - Excess frames are drained from the circular buffer to prevent overflow

**Example usage:**

```python
# Before starting a recording:
success, message, exposure_actual = controller.configure_acquisition_timing(
    exposure_ms=5.0,
    target_fps=20.0
)
if not success:
    print(f"Timing config failed: {message}")
```

### 2. Updated: `acquire_multiphase()` Signature & Behavior

**New parameter:**
```python
def acquire_multiphase(self, fps, base_sec, odor_sec, post_sec, save_dir, logger,
                       odor, fly, geno, esp32, prog_cb, phase_cb,
                       save_video=False, frame_cb=None, exposure_ms=5.0):
```

**Breaking change:** `exposure_ms` is now an explicit parameter (default 5.0 ms).

**Key behavior:**

1. **Calls `configure_acquisition_timing(exposure_ms, fps)`** immediately:
   ```python
   ok_timing, msg_timing, exposure_actual = self.configure_acquisition_timing(
       exposure_ms=exposure_ms, target_fps=fps
   )
   ```

2. **Logs timing configuration** to the event log:
   ```
   ACQ_TIMING | CONFIG | fps_target=20 exposure_ms=5.00 (camera hardware paced)
   ```

3. **Uses continuous sequence acquisition:**
   ```python
   self.core.start_continuous_sequence_acquisition(0)  # 0 = max speed
   ```

4. **Frame pacing logic remains unchanged:**
   - Calculates `target_interval_s = 1.0 / fps` (50 ms for 20 fps)
   - Uses wall-clock time to select frames at the target interval
   - Prevents buffer overflow by draining excess frames

### 3. New Convenience Method: `set_acquisition_defaults(exposure_ms, fps)`

**Purpose:** Store default acquisition parameters at startup.

**Example:**

```python
controller.set_acquisition_defaults(exposure_ms=5.0, fps=20.0)
```

This centralizes timing configuration and makes it easy to change globally from the UI.

## Hardware Timing vs. Software Pacing

### Scenario A: Camera has FrameRate property ✅ (PREFERRED)

```
Camera exposure = 5.0 ms (set in hardware)
Camera frame rate = 20 Hz (set via FrameRate property)
                    ↓
          [Camera acquires at exactly 20 fps]
                    ↓
   [Micro-Manager circular buffer fills at 20 fps]
                    ↓
    [Software pops one frame every 50 ms]
                    ↓
         [Frames written to disk or RAM]
```

**Advantage:** Timing is controlled entirely by the camera. Python just needs to keep up with frame popping.

### Scenario B: Camera has NO FrameRate property 🆗 (FALLBACK)

```
Camera exposure = 5.0 ms (set in hardware)
Camera frame rate = Not available (camera free-runs at ~200 fps)
                    ↓
    [Camera acquires at max speed (~200 fps)]
                    ↓
   [Micro-Manager circular buffer fills rapidly]
                    ↓
  [Software checks next_frame_wall (wall-clock time)]
     If now >= next_frame_wall:
       - Pop frames from buffer (drain extras)
       - Keep one frame, discard the rest
     Else:
       - Sleep briefly, drain excess to prevent overflow
                    ↓
   [Frames selected at ~50 ms intervals (20 fps)]
                    ↓
         [Frames written to disk or RAM]
```

**Advantage:** No special camera support needed. Wall-clock pacing ensures consistent frame intervals despite free-running acquisition.

## How to Use This in Your UI / Recording Logic

### Example 1: Single Recording with Custom Exposure

```python
# In your Run tab or recording function:
def start_recording(self):
    # Optionally set custom exposure
    self.controller.set_acquisition_defaults(exposure_ms=5.0, fps=20.0)

    # Proceed with acquisition
    success, msg, metrics = self.controller.acquire_multiphase(
        fps=20.0,
        base_sec=15.0,
        odor_sec=4.0,
        post_sec=15.0,
        save_dir=self.session_dir,
        logger=self.logger,
        odor="OFM_A",
        fly="fly_001",
        geno="wild-type",
        esp32=self.esp32,
        prog_cb=self.on_progress,
        phase_cb=self.on_phase,
        exposure_ms=5.0,  # Explicitly pass if different from defaults
        frame_cb=self.on_live_frame
    )
```

### Example 2: Protocol Runner with Consistent Timing

```python
# In protocol runner initialization:
def setup_protocol(self):
    # Set once at the start of protocol
    self.controller.set_acquisition_defaults(exposure_ms=5.0, fps=20.0)

    # All subsequent acquisitions will use these defaults
    for odor in ["OFM_A", "OFM_B", "OFM_C"]:
        for trial in range(3):
            self.controller.acquire_multiphase(
                fps=20.0,
                base_sec=15.0,
                odor_sec=4.0,
                post_sec=15.0,
                save_dir=trial_dir,
                logger=logger,
                odor=odor,
                fly=fly_id,
                geno=geno,
                esp32=self.esp32,
                prog_cb=self.on_progress,
                phase_cb=self.on_phase,
                # exposure_ms defaults to 5.0 if not specified
            )
```

## Timing Verification

After acquisition completes, check the metrics and log:

```python
success, msg, metrics = controller.acquire_multiphase(...)

# Print metrics
print(f"Frames captured: {metrics['frames_captured']}")
print(f"FPS measured: {metrics['fps_measured']:.1f}")
print(f"FPS target: {metrics['fps_target']}")
print(f"Exposure set: {metrics['exposure_ms_set']:.2f} ms")
print(f"Dropped frames: {metrics['dropped_frames']}")
```

**What to expect:**
- `fps_measured` should be close to `fps_target` (20 Hz)
- `dropped_frames` should be 0 or very small
- `exposure_ms_set` should match your configuration (5.0 ms)

## Troubleshooting

### Issue: `fps_measured` is much lower than 20 Hz

**Possible causes:**
1. Disk I/O bottleneck (frames saved sequentially)
   - **Fix:** Save to SSD or RAM disk; reduce resolution via ROI
2. Camera can't acquire at 20 fps for the configured exposure
   - **Fix:** Increase exposure time or reduce target fps
3. Frame pacing loop is blocked (e.g., heavy logging)
   - **Fix:** Reduce logging frequency or move to background thread

### Issue: Dropped frames

**Possible causes:**
1. Circular buffer too small
   - **Fix:** Reduce frame rate or increase buffer size (camera property)
2. Software pacing not keeping up
   - **Fix:** Ensure no heavy operations in frame pacing loop

### Issue: Exposure time not set correctly

**Possible causes:**
1. Camera doesn't expose exposure as milliseconds
   - **Fix:** Check your camera manual; may need unit conversion
2. Exposure limits prevent the value
   - **Check:** Print `controller.exp_min` and `controller.exp_max`

## Code Comments Summary

Key lines in `acquire_multiphase()`:

| Line Range | Purpose |
|-----------|---------|
| ~1250 | Call `configure_acquisition_timing()` (EXPOSURE + FRAME RATE) |
| ~1300-1340 | Comment block explaining hardware vs. software timing |
| ~1350-1380 | Continuous acquisition startup and frame loop |
| ~1410-1430 | Frame pacing logic (next_frame_wall, buffer drain) |
| ~1500-1520 | FPS calculation from timestamps |

## For Long Acquisitions (1000+ frames)

Your current implementation is **already optimized** for long acquisitions:

1. ✅ Uses continuous sequence acquisition (not snap loops)
2. ✅ Frames held in RAM until save (not streamed to disk during acquisition)
3. ✅ Circular buffer prevents excessive memory allocation
4. ✅ UI remains responsive (frame callbacks throttled every 0.2 s)
5. ✅ Logging is buffered (flushed every 200 frames)

**For even longer acquisitions (10,000+ frames):**
- Consider saving frames to disk during acquisition (modify save loop)
- Or use memory-mapped arrays (NumPy `memmap`)

## Summary

Your acquisition now:
1. **Explicitly sets 5 ms exposure** at the camera hardware level
2. **Attempts to set 20 fps** via camera FrameRate property (preferred)
3. **Falls back to software pacing** if hardware frame-rate unavailable
4. **Uses continuous sequence acquisition** for efficiency
5. **Drains excess frames** to prevent buffer overflow
6. **Calculates actual FPS** from wall-clock timestamps

All while preserving your existing phase logic, odor control, and ESP32 integration.

---

## Quick Reference: Changing Acquisition Parameters

To change exposure and FPS:

```python
# Option 1: Per-acquisition override
controller.acquire_multiphase(
    ...,
    exposure_ms=10.0,  # 10 ms instead of 5 ms
    fps=10.0            # 10 fps instead of 20 fps
)

# Option 2: Set defaults once
controller.set_acquisition_defaults(exposure_ms=10.0, fps=10.0)
# Then all subsequent acquisitions use these defaults

# Option 3: Direct configuration (before acquisition)
success, msg, exposure_actual = controller.configure_acquisition_timing(
    exposure_ms=10.0,
    target_fps=10.0
)
```

---
