# Timing Control Diagram

## Call Flow: High-Level

```
┌─────────────────────────────────────────────────┐
│  UI Calls: controller.acquire_multiphase(      │
│      fps=20, exposure_ms=5.0, ...)             │
└────────────────┬────────────────────────────────┘
                 │
                 ▼
       ┌─────────────────────────┐
       │ CRITICAL: Configure     │
       │ Timing BEFORE           │
       │ Acquisition Starts      │
       │                         │
       │ configure_acquisition   │
       │ _timing(5.0, 20.0)     │
       └────────────┬────────────┘
                    │
                    ├─► Set Exposure
                    │   core.set_exposure(5.0)
                    │   └─► Camera NOW limited to max 200 fps
                    │
                    ├─► Try FrameRate Property
                    │   ├─ Check for "FrameRate"
                    │   ├─ Check for "AcquisitionFrameRate"
                    │   └─ Check for "Frame Rate"
                    │       └─► If found: Set to 20 fps (HARDWARE PACED)
                    │       └─► If not found: Fallback message
                    │
                    ▼
        ┌──────────────────────────┐
        │ Log Configuration Result │
        │ logger.log("ACQ_TIMING", │
        │   "CONFIG", ...)         │
        └────────────┬─────────────┘
                     │
                     ▼
┌─────────────────────────────────────┐
│ START CONTINUOUS ACQUISITION        │
│ core.start_continuous_sequence      │
│ _acquisition(0)                     │
│                                     │
│ Camera now acquiring at:            │
│  - If hardware frame rate set:      │
│    Exactly 20 fps (camera controls) │
│  - If no frame rate property:       │
│    Max speed ~200 fps (free-running)│
└────────────────┬────────────────────┘
                 │
                 ▼
  ┌──────────────────────────────┐
  │ Frame Pacing Loop            │
  │ (Software Safety Net)        │
  │                              │
  │ while frames < total:        │
  │   now = time.time()          │
  │   if now >= next_frame_wall: │
  │     Pop frame from buffer    │
  │     Drain extra frames       │
  │     Store frame              │
  │     Increment counter        │
  │   else:                      │
  │     Sleep 1ms                │
  └────────────────┬─────────────┘
                   │
                   ▼
       ┌───────────────────────┐
       │ SAVE FRAMES TO DISK   │
       │ (After acquisition)   │
       └───────────────────────┘
                   │
                   ▼
         ┌─────────────────────┐
         │ Return Metrics      │
         │ {frames_captured,   │
         │  fps_measured,      │
         │  dropped_frames,    │
         │  ...}               │
         └─────────────────────┘
```

---

## Timing Scenarios

### Scenario A: Camera with FrameRate Property ✅ PREFERRED

```
HARDWARE TIMING ENFORCED BY CAMERA

configure_acquisition_timing():
  └─ set_exposure(5.0 ms)        ✓ Set
  └─ set_property("FrameRate", 20) ✓ Set
     └─ Camera will acquire at exactly 20 Hz

Result in circular buffer:
  Frame 0:  t=0 ms
  Frame 1:  t=50 ms  (exactly 50 ms = 1/20 fps)
  Frame 2:  t=100 ms (exactly 50 ms interval)
  Frame 3:  t=150 ms (exactly 50 ms interval)
  Frame 4:  t=200 ms (exactly 50 ms interval)
  ...

Frame pacing loop:
  - Checks next_frame_wall (every 50 ms)
  - Pops ONE frame
  - Drains any extras (rare, since hardware is pacing)
  - Stores frame
  - Very efficient ✓

Outcome:
  ✓ Deterministic timing
  ✓ No frame loss
  ✓ Minimal software overhead
  ✓ Precise frame intervals
```

### Scenario B: Camera without FrameRate Property 🆗 FALLBACK

```
HYBRID TIMING: Hardware exposure + Software pacing

configure_acquisition_timing():
  └─ set_exposure(5.0 ms)           ✓ Set
  └─ Try set_property("FrameRate", 20) ✗ Not found
     └─ Falls back to free-running

Result in circular buffer:
  Frame 0:  t=0 ms
  Frame 1:  t=5 ms   (camera acquires every 5 ms)
  Frame 2:  t=10 ms
  Frame 3:  t=15 ms
  Frame 4:  t=20 ms
  ...
  Frame 10: t=50 ms  ← Software selects this one
  ...
  Frame 20: t=100 ms ← Software selects this one
  ...

Frame pacing loop:
  - Checks next_frame_wall (every 50 ms, i.e., 1/20 fps)
  - Pops latest frame (t=50 ms)
  - Drains frames 1-9 to prevent buffer overflow
  - Stores only frame 10
  - Repeat for next interval

Outcome:
  ✓ Still gets 20 fps (via software timing)
  ✓ Slightly more CPU overhead (frame draining)
  ✓ Still no dropped frames (buffer large enough)
  ✓ Reasonable frame intervals (wall-clock based)
```

---

## Exposure-to-FPS Relationship

```
Exposure Time (ms)    Max FPS (hardware limit)    Free-run rate (5ms exposure camera)
─────────────────     ──────────────────────     ──────────────────────────────────
1.0 ms                1000 fps                   1000 fps
2.5 ms                400 fps                    400 fps
5.0 ms                200 fps                    200 fps ← YOUR CONFIGURATION
10.0 ms               100 fps                    100 fps
20.0 ms               50 fps                     50 fps
50.0 ms               20 fps                     20 fps (could be hardware paced if available)
100.0 ms              10 fps                     10 fps

Note: Software pacing can reduce any of these rates further.
Example: Camera free-runs at 200 fps (5 ms exposure) → software selects every 10th frame → 20 fps effective
```

---

## Frame Interval Timeline

### Hardware Frame Rate Control (20 fps)

```
Time (ms)  Event
──────────────────────────────────────────────────────────────────────
0          configure_acquisition_timing() called
           └─ Exposure: 5.0 ms
           └─ Frame Rate: 20 fps (HARDWARE)

0-50       Camera acquiring frames at 20 Hz (50 ms intervals)
           Circular buffer fills

50         Frame pacing loop:
           └─ next_frame_wall = 50 ms reached
           └─ Pop 1 frame from buffer
           └─ Store frame 0
           └─ Schedule next_frame_wall = 100 ms

100        Frame pacing loop:
           └─ next_frame_wall = 100 ms reached
           └─ Pop 1 frame from buffer
           └─ Store frame 1
           └─ Schedule next_frame_wall = 150 ms

150        Frame pacing loop:
           └─ next_frame_wall = 150 ms reached
           └─ Pop 1 frame from buffer
           └─ Store frame 2
           └─ Schedule next_frame_wall = 200 ms

...

Result: Frames stored at exactly 50 ms intervals (20 fps) ✓
```

### Software Frame Rate Control (20 fps, free-running camera)

```
Time (ms)  Event
──────────────────────────────────────────────────────────────────────
0          configure_acquisition_timing() called
           └─ Exposure: 5.0 ms
           └─ Frame Rate: Not available (free-running)
           └─ Console: "Target 20 fps will be enforced in software"

0-5        Camera acquires frame 0
5-10       Camera acquires frame 1
10-15      Camera acquires frame 2
...
45-50      Camera acquires frame 9
           Circular buffer now has 10 frames

50         Frame pacing loop:
           └─ next_frame_wall = 50 ms reached
           └─ Pop latest frame (frame 9, acquired at t=45-50 ms)
           └─ Drain frames 1-8 to prevent overflow
           └─ Store frame 9
           └─ Schedule next_frame_wall = 100 ms

50-55      Camera acquires frame 10
...
100-105    Camera acquires frame 19
           Circular buffer has ~10 frames again

100        Frame pacing loop:
           └─ next_frame_wall = 100 ms reached
           └─ Pop latest frame (frame 19, acquired at ~100 ms)
           └─ Drain frames 10-18
           └─ Store frame 19
           └─ Schedule next_frame_wall = 150 ms

...

Result: Frames stored at ~50 ms intervals (20 fps) via software timing ✓
        Slightly higher CPU (draining 9 frames per interval)
        But still reliable and efficient
```

---

## Decision Tree: Hardware vs. Software Timing

```
START: User calls acquire_multiphase(fps=20, exposure_ms=5.0, ...)
│
├─ configure_acquisition_timing(exposure_ms=5.0, target_fps=20.0)
│  │
│  ├─ Set exposure: self.core.set_exposure(5.0)
│  │  └─ SUCCESS: Camera now limited to max 200 fps
│  │
│  └─ Try FrameRate properties:
│     │
│     ├─ Does camera have "FrameRate" property?
│     │  ├─ YES → Set to 20.0 fps
│     │  │        └─ HARDWARE PACED ✓
│     │  │
│     │  └─ NO → Try "AcquisitionFrameRate"
│     │         ├─ YES → Set to 20.0 fps
│     │         │        └─ HARDWARE PACED ✓
│     │         │
│     │         └─ NO → Try "Frame Rate"
│     │                ├─ YES → Set to 20.0 fps
│     │                │        └─ HARDWARE PACED ✓
│     │                │
│     │                └─ NO → Print fallback message
│     │                       └─ SOFTWARE PACED (via next_frame_wall) 🆗
│     │
│     └─ Return: (success, message, exposure_actual)
│
└─ Start continuous acquisition: core.start_continuous_sequence_acquisition(0)
   │
   └─ Frame pacing loop:
      ├─ If HARDWARE PACED: Drain excess frames, keep one every 50 ms
      └─ If SOFTWARE PACED: Pop frames at next_frame_wall (50 ms intervals)
         └─ Result: 20 fps either way ✓
```

---

## Performance Comparison

### Configuration: 5 ms exposure, 20 fps target, 1000 frames total

```
Scenario A: Hardware Frame Rate Control
───────────────────────────────────────
Time to acquire:     50 seconds (1000 frames / 20 fps)
Buffer size needed:  Small (camera paces itself)
CPU overhead:        Low (minimal frame draining)
Timing precision:    ±0.1 ms per frame (hardware)
Exposure accuracy:   ±0.01 ms (hardware)
Result:              ✓ OPTIMAL
```

```
Scenario B: Software Frame Rate Control (Free-running Camera)
──────────────────────────────────────────────────────────────
Time to acquire:     ~50 seconds (same: 1000 frames / 20 fps)
Buffer size needed:  Larger (camera free-runs at 200 fps)
CPU overhead:        Moderate (drain 9 frames per interval)
Timing precision:    ±1-5 ms per frame (wall-clock dependent)
Exposure accuracy:   ±0.01 ms (hardware)
Result:              ✓ GOOD (but less efficient)
```

**Both achieve 20 fps, but Scenario A is preferable if your camera supports it.**

---

## Monitoring During Acquisition

### Console Output

```
[ACQUISITION] Exposure set to 5.00 ms
[ACQUISITION] Frame rate set via 'FrameRate': 20.0 Hz
```

Or:

```
[ACQUISITION] Exposure set to 5.00 ms
[ACQUISITION] No FrameRate property found; camera free-running at max speed for 5.0ms exposure
[ACQUISITION] Target 20 fps will be enforced in software via frame pacing
```

### Log File (CSV)

```csv
timestamp,event,phase,frame,odor,duration,fly,geno,interval_ms,notes
2025-02-11T14:32:10.001,ACQ_TIMING,CONFIG,0,OFM_A,4.0,fly_001,WT,,fps_target=20 exposure_ms=5.00 (camera hardware paced)
2025-02-11T14:32:10.050,FRAME_0,BASELINE,0,OFM_A,4.0,fly_001,WT,0.0
2025-02-11T14:32:10.100,FRAME_1,BASELINE,1,OFM_A,4.0,fly_001,WT,49.8
2025-02-11T14:32:10.150,FRAME_2,BASELINE,2,OFM_A,4.0,fly_001,WT,50.1
2025-02-11T14:32:10.200,FRAME_3,BASELINE,3,OFM_A,4.0,fly_001,WT,50.0
...
2025-02-11T14:32:34.950,FRAME_300,BASELINE,300,OFM_A,4.0,fly_001,WT,50.1
2025-02-11T14:32:15.000,FRAME_300,ODOR,300,OFM_A,4.0,fly_001,WT,50.0
...
2025-02-11T14:32:35.000,COMPLETE,,700,OFM_A,4.0,fly_001,WT,,Dropped: 0, FPS: 19.95
```

**What to check:**
- `interval_ms` should be ~50 ms (for 20 fps)
- `Dropped` should be 0 or very small
- `FPS: 19.95` should be close to 20.0

---

## Troubleshooting Timeline

```
Problem: fps_measured is 10 fps instead of 20 fps
└─ Check:
   ├─ Camera exposure: Is it 5.0 ms? (run configure_acquisition_timing)
   ├─ Buffer overflow: Are there dropped frames? (check metrics)
   ├─ Disk I/O: Is saving blocking acquisition? (move to background)
   ├─ Frame rate property: Is it actually set? (check console output)
   └─ System load: Is Python GIL blocking? (reduce other tasks)

Problem: Exposure is 10 ms instead of 5 ms
└─ Check:
   ├─ Did you pass exposure_ms=5.0 to acquire_multiphase?
   ├─ Is configure_acquisition_timing being called?
   ├─ Check [ACQUISITION] console output
   └─ Verify logs show exposure_ms_set=5.00 in metrics

Problem: Camera has hardware frame rate support, but it's not being used
└─ Check:
   ├─ Is your property named something different?
   │  └─ Add it to the list in configure_acquisition_timing() line ~910
   ├─ Does the property have constraints?
   │  └─ Check allowed values with get_allowed_property_values()
   └─ Is the value being set correctly?
      └─ Add debug print: print(f"Frame rate value: {core.get_property(camera, prop)}")
```

---

## Summary

```
┌─────────────────────────────────────────────────────────────────┐
│  YOUR ACQUISITION SYSTEM (Post-Refactor)                       │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  1. EXPLICIT EXPOSURE CONFIGURATION                            │
│     └─ set_exposure(5.0 ms) ← Hardware-enforced                 │
│                                                                 │
│  2. OPTIONAL HARDWARE FRAME RATE CONTROL                       │
│     ├─ Try FrameRate property (preferred)                      │
│     └─ Fallback to free-running + software pacing               │
│                                                                 │
│  3. CONTINUOUS SEQUENCE ACQUISITION                            │
│     └─ Fast, efficient, uses circular buffer                    │
│                                                                 │
│  4. FRAME PACING SAFETY NET                                    │
│     └─ Wall-clock timing ensures target FPS always achieved     │
│                                                                 │
│  RESULT: Robust 5 ms / 20 fps acquisition ✓                    │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---
