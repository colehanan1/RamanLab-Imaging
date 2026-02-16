# GUI Clickthrough: Setting Exposure and FPS

## Visual Layout

```
┌─────────────────────────────────────────────────────────────────┐
│  SNNE Lab                                               🌙 Theme  │
│  🪰 v10.0 | Space=Next Trial | Esc=Stop | F5=Repeat             │
└─────────────────────────────────────────────────────────────────┘

┌─ SECTION 1: CONNECTION ──────────────────────────────────────────┐
│ ESP32: ● [Connected/Disconnected]  |  MM: ● [Connected/...]      │
│ Port: [COM3▼] [↻] [Connect]                                      │
└──────────────────────────────────────────────────────────────────┘

┌─ SECTION 2: CAMERA 📷 ───────────────────────────────────────────┐
│                                                                   │
│  Exposure: [100    ] ms  [Set] [⚡Auto]  → 200 FPS max           │
│            ^click here                                            │
│            ^type "5" here, then click [Set] or press ENTER       │
│                                                                   │
│  Gain:     [======●===] 0                                        │
│                                                                   │
│  Binning:  [1▼]                                                  │
│                                                                   │
│  Cam ROI:  [Apply ROI] [Clear ROI]                              │
│                                                                   │
└──────────────────────────────────────────────────────────────────┘

┌─ SECTION 3: EXPERIMENT 🧪 ───────────────────────────────────────┐
│ Fly ID:    [___________________]                                  │
│ Genotype:  [___________________]                                  │
│ Location:  [Browse...] /path/to/save/                            │
└──────────────────────────────────────────────────────────────────┘

┌─ SECTION 4: PHASES ⏱ ────────────────────────────────────────────┐
│ [■ BASE] [■ ODOR] [■ POST]                                       │
│                                                                   │
│ Base: [15  ] s    Odor: [4   ] s    Post: [15  ] s = 34s       │
└──────────────────────────────────────────────────────────────────┘

┌─ SECTION 5: ACQUISITION ⚙️ ──────────────────────────────────────┐
│ FPS: [20  ]  Odor: [OFM_A▼]  ☐ Video                            │
│       ^click here, type "20"                                     │
│       (or whatever FPS you want)                                 │
└──────────────────────────────────────────────────────────────────┘

┌─ SECTION 6: CONTROLS ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┐
│ [Start Preview] [Stop Preview] [Record] [Stop]                   │
└──────────────────────────────────────────────────────────────────┘

┌─ SECTION 7: PROGRESS ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┐
│ Phase: [BASELINE ───────────────]                                │
│ Progress: [████████░░░░░░░░░░░░░] 35%                           │
│ Status: Acquiring frames...                                      │
└──────────────────────────────────────────────────────────────────┘

┌─ SECTION 8: LOG ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┐
│ [2025-02-11 14:32:10] [CONNECTION] Successfully connected       │
│ [2025-02-11 14:32:15] [ACQ_TIMING] Exposure set to 5.00 ms     │
│ [2025-02-11 14:32:20] [COMPLETE] Recorded 680 frames @ 19.95   │
│                                                                  │
│ [Scroll ↑↓] [Clear] [Save Log]                                  │
└──────────────────────────────────────────────────────────────────┘
```

---

## Step-by-Step Clickthrough

### GOAL: Set Exposure to 5 ms and FPS to 20

---

### STEP 1: Launch the GUI

```bash
python mm_odor_recorder_v9.py
```

**Expected:** Application window opens with all sections visible

---

### STEP 2: Connect Camera

**Location:** CONNECTION section, MM row

```
MM: ● --  [Connect]
          ↑ CLICK HERE
```

**Action:** Click [Connect] button

**Expected:** Status changes to:
```
MM: ● Connected: PrimeCam
```

**Console shows:** Something like:
```
Connected: PrimeCam
Exposure min: 0.10 ms
Exposure max: 5000.0 ms
```

---

### STEP 3: Check Current Exposure

**Location:** CAMERA section, Exposure row

```
Exposure: [100    ] ms  [Set] [⚡Auto]  → 100 FPS max
          ^^^^^^^^
          Shows current exposure (100 ms in this example)
```

**What you see:**
- Default is probably 100 ms (or whatever was last set)
- FPS indicator shows: `→ 100 FPS max` (because 1000/100 = 10 max)

---

### STEP 4: Change Exposure to 5 ms

**Location:** CAMERA section, Exposure row

```
Exposure: [100    ] ms  [Set] [⚡Auto]  → 100 FPS max
          ^^^^^^^^
          CLICK HERE (in the text field)
```

**Actions:**
1. **Click** in the exposure entry field (where "100" is)
2. **Select all** text (Ctrl+A or triple-click)
3. **Type:** `5`
4. **Press** ENTER or click [Set] button

**What you should see:**

```
Exposure: [5      ] ms  [Set] [⚡Auto]  → 200 FPS max
          ^^^^^^^^                       ^^^^^^^^^^^^
          Now shows 5                    Updated! (1000/5 = 200)
```

**Console output:**
```
[ACQUISITION] Exposure set to 5.00 ms
```

---

### STEP 5: Verify Exposure Was Applied

**Check:**
1. FPS indicator changed from `100 FPS max` to `200 FPS max` ✓
2. Console shows `[ACQUISITION] Exposure set to 5.00 ms` ✓
3. Entry field shows `5` ✓

---

### STEP 6: Set FPS to 20

**Location:** ACQUISITION section, FPS row

```
FPS: [20  ]  Odor: [OFM_A▼]  ☐ Video
     ^^^^^
     CLICK HERE
```

**Actions:**
1. **Click** in the FPS entry field
2. **Select all** (Ctrl+A or triple-click)
3. **Type:** `20`
4. **Press** ENTER (no button needed, value updates immediately)

**What you should see:**

```
FPS: [20  ]  Odor: [OFM_A▼]  ☐ Video
     ^^^^^
     Now shows 20
```

**Check:** 20 ≤ 200 (the max FPS from exposure)? **YES** ✓

---

### STEP 7: Verify FPS Was Set

**Check:**
1. Entry field shows `20` ✓
2. This is less than max FPS (200)? **YES** ✓

---

### STEP 8: Set Experiment Details

**Location:** EXPERIMENT section

```
Fly ID:    [___________________]
           ↑ CLICK and enter "fly_001"

Genotype:  [___________________]
           ↑ CLICK and enter "WT"
```

**Actions:**
1. Click in Fly ID field
2. Type: `fly_001`
3. Click in Genotype field
4. Type: `WT`

---

### STEP 9: Set Phase Durations

**Location:** PHASES section

```
Base: [15  ] s    Odor: [4   ] s    Post: [15  ] s
      ↑              ↑                    ↑
      Already set    Already set        Already set
      (or change)    (or change)        (or change)
```

**Default values are usually fine:**
- Base: 15 s (pre-stimulus baseline)
- Odor: 4 s (stimulus duration)
- Post: 15 s (post-stimulus observation)
- Total: 34 s

**To change:**
1. Click in the field
2. Select all
3. Type new value
4. Total updates automatically

---

### STEP 10: Check Acquisition Settings

**Location:** ACQUISITION section

```
FPS: [20  ]  Odor: [OFM_A▼]  ☐ Video
             ↑                 ↑
             Already set      Checkbox for AVI
             to 20            (leave unchecked if just want TIFFs)
```

**Verify:**
- FPS: 20 ✓
- Odor: OFM_A (or your choice) ✓
- Video: ☐ (unchecked is fine) ✓

---

### STEP 11: Start Preview (Optional)

**Location:** CONTROLS section

```
[Start Preview] [Stop Preview] [Record] [Stop]
 ↑ CLICK HERE
```

**Action:** Click [Start Preview]

**Expected:**
- Live camera feed appears in left panel
- Phase indicator and stats update
- You can see what the camera sees

**Console:**
```
[CONNECTION] Preview started
```

---

### STEP 12: Record!

**Location:** CONTROLS section

```
[Start Preview] [Stop Preview] [Record] [Stop]
                               ↑
                               CLICK HERE TO RECORD
```

**Action:** Click [Record]

**What happens:**
1. Preview stops (automatically)
2. Exposure configured (you'll see console output)
3. Acquisition begins
4. Progress bar fills
5. Phase indicator changes: BASELINE → ODOR → POST-ODOR
6. Live preview shows acquisition frames
7. When complete, frames saved to disk

**Console output:**
```
[ACQUISITION] Exposure set to 5.00 ms
[ACQUISITION] Frame rate set via 'FrameRate': 20.0 Hz
[PROGRESS] Baseline: 0/150 frames
[PROGRESS] Odor: 0/80 frames
[PROGRESS] Post: 0/150 frames
[COMPLETE] Acquired 680 frames @ 19.95 FPS (dropped: 0)
```

---

### STEP 13: Verify Results

**Location:** LOG section (bottom)

```
[2025-02-11 14:32:10] [ACQ_TIMING] CONFIG ... fps_target=20 exposure_ms=5.00
[2025-02-11 14:32:15] [COMPLETE] ... Dropped: 0, FPS: 19.95
```

**Check:**
- ✓ Exposure set to 5.00 ms
- ✓ FPS target: 20 fps
- ✓ FPS measured: ~20 fps (19.95 is good!)
- ✓ Dropped frames: 0
- ✓ Frames saved to your specified location

---

## Common Mistakes

### Mistake 1: Not Clicking [Set] After Entering Exposure

```
❌ WRONG:
1. Click exposure field
2. Type "5"
3. Immediately record
→ Old exposure value still used!

✅ CORRECT:
1. Click exposure field
2. Type "5"
3. Click [Set] or press ENTER
4. Wait for "[ACQUISITION] Exposure set..." message
5. Now record
```

### Mistake 2: Setting FPS Higher Than Max

```
❌ WRONG:
1. Set exposure to 10 ms
   → Max FPS = 100
2. Set FPS to 50
3. Camera can only deliver 50 fps, software paces to it
   → Works but you're getting 50 fps, not from hardware frame rate control

✅ CORRECT:
1. Set exposure to 5 ms
   → Max FPS = 200
2. Set FPS to 20
3. FPS ≤ Max FPS ✓
4. Camera will deliver exactly 20 fps (hardware paced)
```

### Mistake 3: Forgetting to Connect Camera First

```
❌ WRONG:
1. Open GUI
2. Try to record immediately
→ Error: "Not connected"

✅ CORRECT:
1. Open GUI
2. Click [Connect] in MM section
3. Wait for "Connected: PrimeCam" message
4. Now record
```

### Mistake 4: Leaving Preview Running During Record

```
❌ WRONG:
1. Start Preview
2. Click Record
3. Preview still running?
→ Camera blocked, recording fails

✅ CORRECT:
1. Start Preview (to verify camera is working)
2. Click Record
3. Preview automatically stops
4. Recording proceeds
```

---

## Quick Reference: Where to Click

| What | Where | How | Default |
|------|-------|-----|---------|
| **Set Exposure** | CAMERA section | Click field, enter "5", [Set] or ENTER | 100 ms |
| **Set FPS** | ACQUISITION section | Click field, enter "20", ENTER | 9 fps |
| **Select Odor** | ACQUISITION section | Click dropdown, select "OFM_A" | OFM_A |
| **Connect Camera** | CONNECTION section, MM | Click [Connect] button | Disconnected |
| **Start Preview** | CONTROLS section | Click [Start Preview] | Not running |
| **Record** | CONTROLS section | Click [Record] | Inactive |
| **View Results** | LOG section | Scroll down | (empty) |

---

## Keyboard Shortcuts

```
ENTER        In any text field: Apply value (exposure, fps, fly id, etc.)
F5           Repeat last recording (same settings, new timestamp)
ESC          Stop recording or preview
SPACE        (during protocol) Advance to next trial
Mouse Wheel  Scroll through log section
```

---

## After Recording Completes

### Frames Saved Where?

Location is shown in the EXPERIMENT section:
```
Location: [Browse...] C:\Users\Cole\Documents\Cole\Data\Recordings-20250211\
                      ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
                      Your frames are here!
```

### Check the Results

```
Recordings-20250211/
├── fly_001/
│   ├── images/
│   │   ├── frame_00000.tif
│   │   ├── frame_00001.tif
│   │   ├── ...
│   │   └── frame_00679.tif
│   ├── log.csv          ← Event timestamps
│   ├── metadata.json    ← Recording parameters
│   └── analyze.py       ← Pre-generated analysis template
```

### Verify Timing in Log

Open `log.csv` and check:
- `interval_ms` column should show ~50 ms entries
- `event` column shows `FRAME_*` entries evenly spaced
- If you see "ACQ_TIMING | CONFIG", check the notes column

---

## Troubleshooting: What If...?

### "Exposure won't change"
→ Is preview running? Stop it first ([Stop Preview])
→ Camera disconnected? Click [Connect] again
→ Camera property not writable? Try [⚡Auto] instead

### "FPS is still 9 instead of 20"
→ Did you click in the FPS field and change it?
→ Did you press ENTER to apply?
→ Check if FPS value stuck at 9?

### "Got 10 fps instead of 20"
→ Check exposure: is it 5 ms? If 10 ms, max is 100 fps
→ Check for dropped frames in log: if many, disk I/O issue
→ Check FPS input: is it really 20?

### "Camera says disconnected"
→ Click [↻] to refresh port list
→ Select correct COM port from dropdown
→ Click [Connect]
→ If still fails: check Micro-Manager is running separately

---

That's it! You're ready to start recording with 5 ms exposure and 20 fps! 🎉

---
