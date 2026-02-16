# Quick Examples: Using the Refactored Acquisition

## Example 1: Simple Recording (5 ms, 20 fps)

```python
# In your main window or recording function
from pathlib import Path

controller = self.mm  # Your MicroManagerController instance
logger = self.logger  # Your TimestampLogger

# Define parameters
session_dir = Path("C:/Data/Recordings-20250211/fly_001")
session_dir.mkdir(parents=True, exist_ok=True)

# Record: 15s baseline + 4s odor + 15s post-odor
success, message, metrics = controller.acquire_multiphase(
    fps=20.0,                      # Target 20 fps
    base_sec=15.0,                 # Baseline phase
    odor_sec=4.0,                  # Odor delivery phase
    post_sec=15.0,                 # Post-odor phase
    save_dir=session_dir,          # Where to save frames
    logger=logger,                 # Event logger
    odor="OFM_A",                  # Odor code
    fly="fly_001",                 # Fly identifier
    geno="WT",                     # Genotype
    esp32=self.esp32,              # Odor controller
    prog_cb=self.update_progress,  # Progress callback
    phase_cb=self.update_phase,    # Phase callback
    exposure_ms=5.0,               # 5 ms exposure (EXPLICIT)
    frame_cb=self.update_preview   # Live preview
)

if success:
    print(f"✓ Recorded {metrics['frames_captured']} frames @ {metrics['fps_measured']:.1f} fps")
else:
    print(f"✗ Recording failed: {message}")
```

**Console output:**
```
[ACQUISITION] Exposure set to 5.00 ms
[ACQUISITION] Frame rate set via 'FrameRate': 20.0 Hz
✓ Recorded 680 frames @ 20.0 fps
```

---

## Example 2: Custom Exposure (10 ms for dim samples)

```python
# For a dimmer sample, use longer exposure
success, message, metrics = controller.acquire_multiphase(
    fps=20.0,
    base_sec=10.0,
    odor_sec=3.0,
    post_sec=10.0,
    save_dir=session_dir,
    logger=logger,
    odor="OFM_B",
    fly="fly_002_dim",
    geno="WT",
    esp32=self.esp32,
    prog_cb=self.update_progress,
    phase_cb=self.update_phase,
    exposure_ms=10.0,  # LONGER exposure for dim sample
    frame_cb=self.update_preview
)

print(f"Exposure set to: {metrics['exposure_ms_set']} ms")
```

**Console output:**
```
[ACQUISITION] Exposure set to 10.00 ms
[ACQUISITION] Frame rate set via 'FrameRate': 20.0 Hz
Exposure set to: 10.0 ms
```

---

## Example 3: Set Defaults, Then Use Them

```python
# At the start of your recording session
controller.set_acquisition_defaults(exposure_ms=5.0, fps=20.0)

# Now all recordings use these defaults (unless explicitly overridden)
for fly_id in ["fly_001", "fly_002", "fly_003"]:
    # No need to pass exposure_ms unless you want to change it
    success, message, metrics = controller.acquire_multiphase(
        fps=20.0,
        base_sec=15.0,
        odor_sec=4.0,
        post_sec=15.0,
        save_dir=Path(f"C:/Data/{fly_id}"),
        logger=logger,
        odor="OFM_A",
        fly=fly_id,
        geno="WT",
        esp32=self.esp32,
        prog_cb=self.update_progress,
        phase_cb=self.update_phase
        # exposure_ms NOT specified → uses 5.0 from defaults
    )

    if success:
        print(f"{fly_id}: {metrics['frames_captured']} frames @ {metrics['fps_measured']:.1f} fps")
```

---

## Example 4: Protocol Runner (Multiple Odors, Multiple Trials)

```python
def run_full_odor_panel(self):
    """Record responses to all odors, 3 trials each."""

    # Configure timing once for the entire protocol
    self.controller.set_acquisition_defaults(exposure_ms=5.0, fps=20.0)

    odors = ["OFM_A", "OFM_B", "OFM_C", "OFM_H"]
    fly_id = "fly_123"

    for odor in odors:
        for trial in range(3):
            trial_dir = Path(f"C:/Data/{fly_id}/{odor}_trial{trial+1}")
            trial_dir.mkdir(parents=True, exist_ok=True)

            # All trials use 5 ms, 20 fps (configured above)
            success, msg, metrics = self.controller.acquire_multiphase(
                fps=20.0,
                base_sec=15.0,
                odor_sec=4.0,
                post_sec=15.0,
                save_dir=trial_dir,
                logger=self.logger,
                odor=odor,
                fly=fly_id,
                geno="WT",
                esp32=self.esp32,
                prog_cb=self.update_progress,
                phase_cb=self.update_phase,
                frame_cb=self.update_preview
            )

            if success:
                print(f"✓ {odor} Trial {trial+1}: {metrics['fps_measured']:.1f} fps")
            else:
                print(f"✗ {odor} Trial {trial+1} FAILED")

            # Inter-trial interval (light pause)
            self._show_pause_popup(seconds=60, message="Turn OFF shutter. Press SPACE when ready.")
```

---

## Example 5: Test Mode (Verify Frame Rate Before Full Recording)

```python
def verify_acquisition_timing(self):
    """Quick test to verify 5ms / 20fps is working correctly."""

    test_dir = Path("C:/Data/timing_test")
    test_dir.mkdir(parents=True, exist_ok=True)

    # Short 5-second test
    success, msg, metrics = self.controller.acquire_multiphase(
        fps=20.0,
        base_sec=1.0,
        odor_sec=1.0,
        post_sec=1.0,
        save_dir=test_dir,
        logger=self.logger,
        odor="TEST",
        fly="test",
        geno="test",
        esp32=self.esp32,
        prog_cb=lambda f, t, p: None,  # No UI update needed
        phase_cb=lambda p: None,
        exposure_ms=5.0
    )

    if success:
        fps_measured = metrics['fps_measured']
        fps_target = metrics['fps_target']
        error_pct = abs(fps_measured - fps_target) / fps_target * 100

        print(f"Target FPS: {fps_target}")
        print(f"Measured FPS: {fps_measured:.1f}")
        print(f"Error: {error_pct:.1f}%")

        if error_pct < 5.0:
            print("✓ Timing is within 5% tolerance")
            return True
        else:
            print(f"✗ Timing error exceeds 5% - check camera configuration")
            return False
    else:
        print(f"✗ Acquisition failed: {msg}")
        return False
```

**Console output:**
```
[ACQUISITION] Exposure set to 5.00 ms
[ACQUISITION] Frame rate set via 'FrameRate': 20.0 Hz
Target FPS: 20.0
Measured FPS: 19.8
Error: 1.0%
✓ Timing is within 5% tolerance
```

---

## Example 6: Handle Different Camera Properties

### If camera has `FrameRate`:
```python
# No special code needed—configure_acquisition_timing() will find it
success, msg, metrics = controller.acquire_multiphase(
    fps=20.0,
    ...,
    exposure_ms=5.0
)
# Console will show:
# [ACQUISITION] Frame rate set via 'FrameRate': 20.0 Hz
```

### If camera has `AcquisitionFrameRate`:
```python
# Still no special code—configure_acquisition_timing() tries multiple names
success, msg, metrics = controller.acquire_multiphase(
    fps=20.0,
    ...,
    exposure_ms=5.0
)
# Console will show:
# [ACQUISITION] Frame rate set via 'AcquisitionFrameRate': 20.0 Hz
```

### If camera has NO frame rate property (fallback):
```python
# Still works—software pacing kicks in
success, msg, metrics = controller.acquire_multiphase(
    fps=20.0,
    ...,
    exposure_ms=5.0
)
# Console will show:
# [ACQUISITION] Exposure set to 5.00 ms
# [ACQUISITION] No FrameRate property found; camera free-running at max speed for 5.0ms exposure
# [ACQUISITION] Target 20 fps will be enforced in software via frame pacing
#
# Result: Still gets 20 fps via software pacing, just less efficient
```

---

## Example 7: Check Metrics After Recording

```python
success, msg, metrics = controller.acquire_multiphase(...)

if success:
    print("=== Acquisition Metrics ===")
    print(f"Frames captured: {metrics['frames_captured']}")
    print(f"Frames saved: {metrics['frames_saved']}")
    print(f"Dropped frames: {metrics['dropped_frames']}")
    print(f"FPS target: {metrics['fps_target']}")
    print(f"FPS measured: {metrics['fps_measured']:.2f}")
    print(f"Exposure set: {metrics['exposure_ms_set']:.2f} ms")
    print(f"Mean intensity: {metrics['mean_intensity']:.0f}")
    print(f"Saturation: {metrics['saturation_pct']:.2f}%")
    print(f"Data type: {metrics['dtype']}")
    print(f"Odor ON (ESP32): {metrics['odor_on_ts_esp']}")
    print(f"Odor OFF (ESP32): {metrics['odor_off_ts_esp']}")
else:
    print(f"Failed: {msg}")
```

**Example output:**
```
=== Acquisition Metrics ===
Frames captured: 680
Frames saved: 680
Dropped frames: 0
FPS target: 20.0
FPS measured: 19.95
Exposure set: 5.00 ms
Mean intensity: 1200.5
Saturation: 0.00%
Data type: uint16
Odor ON (ESP32): 2025-02-11T14:32:15.042
Odor OFF (ESP32): 2025-02-11T14:32:19.050
```

---

## Example 8: Error Handling

```python
def safe_record(self, odor, fly_id):
    """Record with proper error handling."""

    try:
        # Verify camera is connected
        if not self.controller.connected:
            raise RuntimeError("Camera not connected")

        # Verify ESP32 is available for odor control
        if not self.esp32.connected:
            print("WARNING: ESP32 not connected—recording without odor delivery")

        # Perform acquisition
        success, msg, metrics = self.controller.acquire_multiphase(
            fps=20.0,
            base_sec=15.0,
            odor_sec=4.0,
            post_sec=15.0,
            save_dir=Path(f"C:/Data/{fly_id}"),
            logger=self.logger,
            odor=odor,
            fly=fly_id,
            geno="WT",
            esp32=self.esp32,
            prog_cb=self.update_progress,
            phase_cb=self.update_phase,
            exposure_ms=5.0
        )

        if not success:
            raise RuntimeError(f"Acquisition failed: {msg}")

        # Verify we got frames
        if metrics['frames_captured'] == 0:
            raise RuntimeError("No frames captured")

        # Warn if too many dropped frames
        if metrics['dropped_frames'] > 10:
            print(f"WARNING: {metrics['dropped_frames']} dropped frames")

        # Warn if saturation is high
        if metrics['saturation_pct'] > 5.0:
            print(f"WARNING: High saturation ({metrics['saturation_pct']:.1f}%)")

        print(f"✓ Recording successful: {metrics['frames_captured']} frames @ {metrics['fps_measured']:.1f} fps")
        return True

    except Exception as e:
        print(f"✗ Recording failed: {e}")
        return False
```

---

## Summary

| Use Case | Example | Key Points |
|----------|---------|-----------|
| Simple recording | Example 1 | Pass `exposure_ms` explicitly |
| Dim sample | Example 2 | Use longer exposure (e.g., 10 ms) |
| Multiple recordings | Example 3 | Call `set_acquisition_defaults()` once |
| Protocol runner | Example 4 | Set defaults at start, then record all trials |
| Timing verification | Example 5 | Use short test to verify before full recording |
| Different cameras | Example 6 | Code handles all camera types automatically |
| Metrics check | Example 7 | Inspect metrics after acquisition |
| Error handling | Example 8 | Always check success and metrics |

---
