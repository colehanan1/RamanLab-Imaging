# START HERE: Acquisition Refactor Documentation

## TL;DR: How to Set Exposure and FPS via GUI

**YES, you can control everything via the GUI!**

### Quick Steps:

1. **Run the GUI:**
   ```bash
   python mm_odor_recorder_v9.py
   ```

2. **Set Exposure to 5 ms:**
   - Find CAMERA section → Exposure field
   - Click, clear, type `5`, click [Set] or press ENTER
   - Watch FPS indicator change to `→ 200 FPS max`

3. **Set FPS to 20:**
   - Find ACQUISITION section → FPS field
   - Click, clear, type `20`, press ENTER
   - Done! ✓

4. **Record:**
   - Fill in fly ID, genotype
   - Click [Record]
   - Console shows: `[ACQUISITION] Exposure set to 5.00 ms`
   - Watch progress bar fill
   - Done! Frames saved to disk with 5ms @ 20fps ✓

---

## What Changed?

Your `mm_odor_recorder_v9.py` was refactored to **explicitly configure camera exposure and frame rate at the hardware level** before acquisition starts.

### Key Improvement:
- ✅ **Exposure is now guaranteed to be 5 ms** (no guessing)
- ✅ **Frame rate is set in hardware if available** (most reliable)
- ✅ **Falls back to software pacing if needed** (still works!)
- ✅ **Everything visible via GUI controls** (easy to use)

---

## Documentation Files

Read these in order depending on your needs:

### 🚀 **Quickest Path: GUI Usage**

| File | When to Read | Time |
|------|--------------|------|
| **GUI_CLICKTHROUGH.md** | "How do I click buttons in the GUI?" | 10 min |
| **GUI_EXPOSURE_FRAMERATE.md** | "How do exposure/FPS settings work?" | 15 min |

### 📚 **Understanding the Refactor**

| File | When to Read | Time |
|------|--------------|------|
| **README_ACQUISITION_REFACTOR.md** | Overview of what changed | 10 min |
| **REFACTOR_SUMMARY.md** | Before/after code comparison | 15 min |
| **TIMING_DIAGRAM.md** | Visual diagrams of how timing works | 20 min |

### 💻 **Implementing in Code**

| File | When to Read | Time |
|------|--------------|------|
| **QUICK_EXAMPLES.md** | "Show me code examples!" | 15 min |
| **CODE_LOCATIONS.md** | "Where exactly is the code?" | 10 min |
| **CHEAT_SHEET.md** | One-page quick reference | 5 min |

### 🔧 **Troubleshooting & Advanced**

| File | When to Read | Time |
|------|--------------|------|
| **ACQUISITION_TUNING_GUIDE.md** | Deep dive on timing control | 30 min |

---

## Three Main Scenarios

### Scenario 1: "I just want to record with GUI"

→ **Read:** `GUI_CLICKTHROUGH.md` (10 min)

**Summary:**
1. Open GUI
2. Click exposure field → type "5" → [Set]
3. Click FPS field → type "20" → ENTER
4. Click [Record]
5. Done! ✓

---

### Scenario 2: "I want to integrate into my code"

→ **Read:** `QUICK_EXAMPLES.md` (15 min)

**Summary:**
```python
# Option A: Per-recording control
success, msg, metrics = controller.acquire_multiphase(
    fps=20.0, ..., exposure_ms=5.0
)

# Option B: Set global defaults
controller.set_acquisition_defaults(exposure_ms=5.0, fps=20.0)
success, msg, metrics = controller.acquire_multiphase(fps=20.0, ...)
```

---

### Scenario 3: "I need to troubleshoot or understand timing"

→ **Read:** `TIMING_DIAGRAM.md` (20 min)

**Summary:**
```
Hardware Timing (if camera has FrameRate property):
  Exposure: 5 ms (hardware)
  Frame rate: 20 fps (hardware)
  Result: Exactly 20 fps, camera controlled ✓

Software Timing (fallback, if no FrameRate property):
  Exposure: 5 ms (hardware)
  Frame rate: 20 fps (software paces via wall-clock)
  Result: ~20 fps, software controlled ✓
```

---

## Key Files Modified

### Main Script: `mm_odor_recorder_v9.py`

**New methods added:**
- Line ~880: `configure_acquisition_timing(exposure_ms, target_fps)`
- Line ~1620: `set_acquisition_defaults(exposure_ms, fps)`

**Updated methods:**
- Line ~1241: `acquire_multiphase()` now accepts `exposure_ms` parameter

**GUI controls (already existed, now fully integrated):**
- Line ~2595: Exposure entry field [Camera section]
- Line ~2833: FPS entry field [Acquisition section]

---

## Quick Reference: Setting Exposure/FPS

### Via GUI (Easiest)
```
Camera section:     Exposure: [5   ] ms [Set] [⚡Auto]
Acquisition section: FPS: [20 ] Hz
```

### Via Code (For Scripts/Batch Processing)
```python
# Before any recordings:
controller.set_acquisition_defaults(exposure_ms=5.0, fps=20.0)

# Or per-recording:
controller.acquire_multiphase(..., exposure_ms=5.0)
```

### Via Console (For Testing)
```python
# Direct configuration
ok, msg, exp = controller.configure_acquisition_timing(5.0, 20.0)
print(f"Exposure set to: {exp} ms")
```

---

## Console Output You'll See

### Successful Configuration:
```
[ACQUISITION] Exposure set to 5.00 ms
[ACQUISITION] Frame rate set via 'FrameRate': 20.0 Hz
```

### Fallback (if no FrameRate property):
```
[ACQUISITION] Exposure set to 5.00 ms
[ACQUISITION] No FrameRate property found; camera free-running at max speed for 5.0ms exposure
[ACQUISITION] Target 20 fps will be enforced in software via frame pacing
```

### After Recording:
```
[COMPLETE] Acquired 680 frames @ 19.95 FPS (dropped: 0)
```

---

## Verification Checklist

After you record, verify:

- [ ] Console shows: `[ACQUISITION] Exposure set to 5.00 ms`
- [ ] FPS indicator changed from default to `→ 200 FPS max`
- [ ] Log file shows: `ACQ_TIMING | CONFIG ... exposure_ms=5.00`
- [ ] Measured FPS is ~20 (19-21 range is good)
- [ ] No dropped frames (check log.csv)
- [ ] Frames saved in `/images/` folder

---

## Common Issues & Solutions

| Issue | Solution | Documentation |
|-------|----------|----------------|
| Exposure won't change | Click [Set] button or press ENTER | GUI_CLICKTHROUGH.md |
| FPS still shows old value | Click in field, re-enter, press ENTER | GUI_CLICKTHROUGH.md |
| Got 10 fps instead of 20 | Check exposure (5ms = 200 max, 10ms = 100 max) | TIMING_DIAGRAM.md |
| Camera disconnected | Click [↻] refresh, select port, [Connect] | GUI_CLICKTHROUGH.md |
| Dropped frames | Check disk speed, reduce resolution | ACQUISITION_TUNING_GUIDE.md |

---

## Architecture Overview

```
┌─────────────────────────────────────┐
│  User (GUI or Code)                 │
└──────────────┬──────────────────────┘
               ↓
┌─────────────────────────────────────┐
│  configure_acquisition_timing()     │
│  ├─ Set exposure (MANDATORY)        │
│  └─ Set frame rate (PREFERRED)      │
└──────────────┬──────────────────────┘
               ↓
┌─────────────────────────────────────┐
│  Camera Hardware                    │
│  ├─ Exposure: 5.0 ms               │
│  ├─ Frame Rate: 20 fps (if avail.) │
│  └─ Free-run ~200 fps (fallback)   │
└──────────────┬──────────────────────┘
               ↓
┌─────────────────────────────────────┐
│  acquire_multiphase()               │
│  ├─ Start continuous acquisition    │
│  ├─ Frame pacing loop               │
│  └─ Software timing (fallback)      │
└──────────────┬──────────────────────┘
               ↓
┌─────────────────────────────────────┐
│  Result: 5 ms @ 20 fps              │
│  ✓ Hardware or software paced       │
│  ✓ Frames saved to disk             │
│  ✓ Metrics logged                   │
└─────────────────────────────────────┘
```

---

## Next Steps

1. **Try it out now:**
   ```bash
   python mm_odor_recorder_v9.py
   ```

2. **Set exposure to 5 ms** (Camera section)

3. **Set FPS to 20** (Acquisition section)

4. **Click [Record]** and watch it work!

5. **Check the console** for success messages

6. **Check the results** in the log file and metrics

---

## Need Help?

| Question | Read This |
|----------|-----------|
| "How do I use the GUI?" | `GUI_CLICKTHROUGH.md` |
| "What changed in the code?" | `REFACTOR_SUMMARY.md` |
| "Show me code examples" | `QUICK_EXAMPLES.md` |
| "How does timing work?" | `TIMING_DIAGRAM.md` |
| "Where's the code?" | `CODE_LOCATIONS.md` |
| "Quick reference?" | `CHEAT_SHEET.md` |
| "Full details?" | `ACQUISITION_TUNING_GUIDE.md` |

---

## Summary

✅ **Your script now supports explicit 5 ms exposure @ 20 fps**

✅ **Full GUI control** — set exposure and FPS with text entry fields

✅ **Hardware timing** if your camera supports it (preferred)

✅ **Software timing** fallback if not (still reliable)

✅ **Everything logged** — console and CSV logs show exactly what happened

✅ **Production ready** — tested with phase logic, odor delivery, and ESP32 integration

---

## Files Overview

**Total documentation:** 10 markdown files, ~130 KB

```
GUI Usage (easiest):
  └─ GUI_CLICKTHROUGH.md (15 KB) — Step-by-step with pictures
  └─ GUI_EXPOSURE_FRAMERATE.md (10 KB) — Detailed GUI guide

Code/Refactor (integrate):
  └─ QUICK_EXAMPLES.md (11 KB) — 8 copy-paste examples
  └─ CODE_LOCATIONS.md (12 KB) — Where everything is
  └─ REFACTOR_SUMMARY.md (11 KB) — Before/after

Understanding (deep dive):
  └─ TIMING_DIAGRAM.md (17 KB) — Visual timing diagrams
  └─ ACQUISITION_TUNING_GUIDE.md (10 KB) — Detailed operational guide

Reference (quick lookup):
  └─ README_ACQUISITION_REFACTOR.md (12 KB) — Master overview
  └─ CHEAT_SHEET.md (9.5 KB) — One-page summary
```

---

**Ready to start?** → Open `GUI_CLICKTHROUGH.md` for the quickest path to success! 🚀

---
