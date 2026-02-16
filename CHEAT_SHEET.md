# Acquisition Refactor Cheat Sheet

## The One Command (Copy-Paste Ready)

```python
# 5 ms exposure, 20 fps, standard odor recording
success, msg, metrics = controller.acquire_multiphase(
    fps=20.0,
    base_sec=15.0,
    odor_sec=4.0,
    post_sec=15.0,
    save_dir=Path("C:/Data/fly_001"),
    logger=logger,
    odor="OFM_A",
    fly="fly_001",
    geno="WT",
    esp32=self.esp32,
    prog_cb=self.update_progress,
    phase_cb=self.update_phase,
    exposure_ms=5.0,  # ← KEY: Explicit 5 ms exposure
    frame_cb=self.update_preview
)

print(f"✓ {metrics['frames_captured']} frames @ {metrics['fps_measured']:.1f} fps")
```

---

## What Changed

| Before | After |
|--------|-------|
| ❌ Exposure undefined (whatever was last set) | ✅ Exposure **explicitly set to 5.0 ms** |
| ❌ FPS enforced only in software | ✅ FPS set in hardware if possible, software pacing as fallback |
| ❌ No timing configuration before acquisition | ✅ **`configure_acquisition_timing()` called first** |
| ❌ Timing uncertainty | ✅ Console/logs show exactly what was configured |

---

## Three Ways to Control Exposure

### Method 1: Per-Recording (Recommended for flexibility)
```python
success, msg, metrics = controller.acquire_multiphase(
    ...,
    exposure_ms=5.0  # Set per-recording
)
```

### Method 2: Global Default (Recommended for consistency)
```python
controller.set_acquisition_defaults(exposure_ms=5.0, fps=20.0)  # ← Call once

# Then all acquisitions use this exposure
success, msg, metrics = controller.acquire_multiphase(...)
```

### Method 3: Direct Configuration (For testing)
```python
ok, msg, exp = controller.configure_acquisition_timing(
    exposure_ms=5.0,
    target_fps=20.0
)
print(f"Exposure set to: {exp} ms")
```

---

## Console Output

### Success ✓
```
[ACQUISITION] Exposure set to 5.00 ms
[ACQUISITION] Frame rate set via 'FrameRate': 20.0 Hz
```

### Success (Fallback) ✓
```
[ACQUISITION] Exposure set to 5.00 ms
[ACQUISITION] No FrameRate property found; camera free-running at max speed for 5.0ms exposure
[ACQUISITION] Target 20 fps will be enforced in software via frame pacing
```

### Failure ✗
```
[ACQUISITION] ERROR: Failed to configure acquisition timing: Not connected
```

---

## Key Metrics to Check

```python
success, msg, metrics = controller.acquire_multiphase(...)

if success:
    # CRITICAL:
    measured_fps = metrics['fps_measured']          # Should be ~20.0
    dropped = metrics['dropped_frames']             # Should be 0-few
    exposure = metrics['exposure_ms_set']           # Should be 5.0

    # GOOD TO CHECK:
    frames_captured = metrics['frames_captured']    # Total frames
    mean_intensity = metrics['mean_intensity']      # Signal strength
    saturation = metrics['saturation_pct']          # Clipping %

    # OPTIONAL:
    odor_on_time = metrics['odor_on_ts_esp']      # ESP32-reported ON
    odor_off_time = metrics['odor_off_ts_esp']    # ESP32-reported OFF
```

---

## Quick Verification (One-Liner Tests)

```python
# Test 1: Check exposure
print(f"Exposure: {controller.core.get_exposure()} ms")

# Test 2: Quick 5-second test
ok, msg, m = controller.acquire_multiphase(
    fps=20.0, base_sec=2.0, odor_sec=1.0, post_sec=2.0,
    save_dir=Path("C:/Data/test"), logger=logger,
    odor="TEST", fly="test", geno="test", esp32=self.esp32,
    prog_cb=lambda *_: None, phase_cb=lambda *_: None
)
print(f"Test: {m['fps_measured']:.1f} fps (target 20)")

# Test 3: Check log file
with open(logger.log_path) as f:
    for line in f:
        if "ACQ_TIMING" in line:
            print(line.strip())
```

---

## Common Scenarios

### Dim Sample (Need More Light)
```python
success, msg, metrics = controller.acquire_multiphase(
    ...,
    exposure_ms=10.0  # ← Longer exposure (10 ms)
)
```

### Bright Sample (Prevent Saturation)
```python
success, msg, metrics = controller.acquire_multiphase(
    ...,
    exposure_ms=2.5  # ← Shorter exposure (2.5 ms)
)
```

### Slow Motion (10 fps instead of 20)
```python
success, msg, metrics = controller.acquire_multiphase(
    fps=10.0,        # ← Lower FPS
    ...,
    exposure_ms=5.0
)
```

### High Speed (40 fps, if camera supports it)
```python
success, msg, metrics = controller.acquire_multiphase(
    fps=40.0,        # ← Higher FPS (may need shorter exposure)
    ...,
    exposure_ms=2.5  # ← Shorter exposure to allow higher FPS
)
```

---

## Integration Steps

1. **Add to UI: Exposure spinbox**
   ```python
   exposure_var = tk.DoubleVar(value=5.0)
   ttk.Spinbox(frame, from_=0.1, to=100, textvariable=exposure_var)
   ```

2. **Pass to acquisition**
   ```python
   controller.acquire_multiphase(
       ...,
       exposure_ms=exposure_var.get()
   )
   ```

3. **Test**
   ```python
   # Run a 10-second test
   # Verify exposed time in console
   # Check fps_measured in metrics
   ```

4. **Deploy**
   - Update all places that call `acquire_multiphase()`
   - Update protocol runner
   - Test with full protocol run

---

## Troubleshooting Tree

```
FPS is 10 instead of 20?
├─ Is exposure 5 ms? (check console output)
│  └─ If not: pass exposure_ms=5.0 to acquire_multiphase()
├─ Are there dropped frames? (check metrics['dropped_frames'])
│  └─ If yes: reduce fps or increase buffer size
└─ Is disk I/O blocking? (move saving to after acquisition)
   └─ Save frames after acquisition completes

Exposure not 5 ms?
├─ Did you pass exposure_ms=5.0?
│  └─ Yes? Then check console: [ACQUISITION] Exposure set to...
│  └─ No? Then pass it now
├─ Is camera connected?
│  └─ Check: controller.connected
└─ Any error messages?
   └─ Check log file for ACQ_CONFIG_FAILED

No frames captured?
├─ Did acquire_multiphase() return success=True?
│  └─ Check metrics['frames_captured']
├─ Is save_dir valid?
│  └─ Verify path exists
└─ Is camera connected?
   └─ Call controller.connect() first
```

---

## Code Changes Summary

| Component | Change | Location |
|-----------|--------|----------|
| New method | `configure_acquisition_timing()` | Line ~880 |
| Updated signature | `acquire_multiphase(..., exposure_ms=5.0)` | Line ~1243 |
| New call | `self.configure_acquisition_timing(exposure_ms, fps)` | Line ~1323 |
| New metric | `metrics['exposure_ms_set']` | Line ~1311 |
| New method | `set_acquisition_defaults(exposure_ms, fps)` | Line ~1620 |
| New initialization | `_default_exposure_ms`, `_default_fps` | Line ~798 |

---

## Documentation Files (Quick Links)

| File | Purpose | Read When |
|------|---------|-----------|
| **README_ACQUISITION_REFACTOR.md** | Overview of all changes | Starting out |
| **REFACTOR_SUMMARY.md** | Before/after comparison | Understanding what changed |
| **QUICK_EXAMPLES.md** | Copy-paste code examples | Implementing in your code |
| **ACQUISITION_TUNING_GUIDE.md** | Detailed operational guide | Troubleshooting or optimizing |
| **TIMING_DIAGRAM.md** | Visual diagrams and timelines | Visualizing how it works |
| **CODE_LOCATIONS.md** | Where everything is in the file | Debugging or reading code |
| **CHEAT_SHEET.md** | This file | Quick reference |

---

## One-Page Summary

```
┌─────────────────────────────────────────────────────────┐
│ YOUR ACQUISITION: 5 ms EXPOSURE @ 20 FPS                │
├─────────────────────────────────────────────────────────┤
│                                                          │
│ BEFORE REFACTOR:                                        │
│  ❌ Exposure undefined                                  │
│  ❌ FPS only in software                                │
│  ❌ No explicit configuration                           │
│                                                          │
│ AFTER REFACTOR:                                         │
│  ✅ Exposure EXPLICITLY set to 5.0 ms (hardware)       │
│  ✅ Frame rate set in hardware if available             │
│  ✅ Software pacing as fallback (still works!)          │
│  ✅ Clear console/log output of configuration           │
│                                                          │
│ NEW CODE:                                               │
│  → configure_acquisition_timing(5.0, 20.0)             │
│  → acquire_multiphase(..., exposure_ms=5.0)            │
│  → set_acquisition_defaults(5.0, 20.0)                 │
│                                                          │
│ RESULT:                                                 │
│  ✓ Robust high-speed acquisition                       │
│  ✓ No timing surprises                                 │
│  ✓ Backward compatible                                 │
│  ✓ Ready for production                                │
│                                                          │
└─────────────────────────────────────────────────────────┘
```

---

## The Magic Lines

```python
# LINE 1: Set defaults once
controller.set_acquisition_defaults(exposure_ms=5.0, fps=20.0)

# LINE 2: Use them everywhere
success, msg, metrics = controller.acquire_multiphase(
    fps=20.0,
    ...,
    exposure_ms=5.0  # ← KEY LINE
)

# LINE 3: Check it worked
print(f"✓ {metrics['frames_captured']} frames @ {metrics['fps_measured']:.1f} fps")
```

That's all you need! 🎯

---
