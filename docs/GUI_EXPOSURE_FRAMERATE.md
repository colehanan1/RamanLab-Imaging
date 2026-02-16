# How to Set Exposure and Frame Rate Via the GUI

## Overview

Yes! The GUI already has controls for exposure time. Here's how to use them:

---

## GUI Sections

Your application has the following sections relevant to acquisition timing:

### 1. **Camera Section** (Top)
Where you control **EXPOSURE** and other camera settings

### 2. **Acquisition Section** (Middle)
Where you control **FPS** and other recording settings

---

## Setting Exposure Time (5 ms)

### Step 1: Find the Exposure Entry Field

In the **"📷 CAMERA"** section, look for:
```
Exposure: [____] ms  [Set] [⚡Auto]  → 200 FPS max
          (entry)  (button)(button)  (indicator)
```

### Step 2: Enter Your Desired Exposure Time

```
Default: Shows current exposure (usually 100 ms)

To set 5 ms:
1. Click in the exposure entry field
2. Clear the current value
3. Type: 5
4. Press ENTER or click [Set] button
```

### Step 3: Watch the FPS Indicator

The indicator on the right shows the **maximum achievable FPS** at your exposure time:

```
If you set exposure to:     Max FPS indicator will show:
1 ms                        → 1000 FPS max
2.5 ms                      → 400 FPS max
5.0 ms                      → 200 FPS max  ✓
10.0 ms                     → 100 FPS max
20.0 ms                     → 50 FPS max
100.0 ms                    → 10 FPS max
```

**The formula:** `max_fps = 1000 / exposure_ms`

### Step 4: Confirm It Was Set

After clicking [Set], you should see in the console:
```
[ACQUISITION] Exposure set to 5.00 ms
```

---

## Setting Frame Rate (20 fps)

### Step 1: Find the FPS Entry Field

In the **"⚙️ ACQUISITION"** section, look for:
```
FPS: [____]  Odor: [______]  ☐ Video
     (entry)        (combo)   (checkbox)
```

### Step 2: Enter Your Desired FPS

```
Current default: 9

To set 20 fps:
1. Click in the FPS entry field
2. Clear the current value
3. Type: 20
4. The value updates immediately
```

### Step 3: Constraints to Remember

Your target FPS cannot exceed the **exposure limit**:

```
If exposure is 5 ms:
  Max possible FPS = 200 fps
  You can set target FPS to any value ≤ 200
  → Setting 20 fps is fine ✓

If exposure is 10 ms:
  Max possible FPS = 100 fps
  You can set target FPS to any value ≤ 100
  → Setting 20 fps is fine ✓

If exposure is 100 ms:
  Max possible FPS = 10 fps
  You CANNOT set target FPS to 20
  → You must reduce exposure first
```

---

## Recommended Workflow

### For Bright Samples (Normal Imaging)

```
1. Set Exposure: 5 ms
   └─ FPS indicator shows: 200 FPS max
2. Set FPS: 20
   └─ Actual framerate will be 20 fps ✓
3. Click [Set] to apply exposure
4. Ready to record!
```

### For Dim Samples (Need More Light)

```
1. Set Exposure: 10 ms (or 15 ms if still dim)
   └─ FPS indicator shows: 100 FPS max (or 66 FPS)
2. Set FPS: 20
   └─ Still achievable ✓
3. Click [Set] to apply exposure
4. Ready to record!
```

### For Very Bright Samples (Prevent Saturation)

```
1. Set Exposure: 2.5 ms
   └─ FPS indicator shows: 400 FPS max
2. Set FPS: 20
   └─ Easily achievable ✓
3. Click [Set] to apply exposure
4. Ready to record!
```

---

## Auto-Exposure Button (Automatic Method)

If you want the system to **automatically find the best exposure**:

### Step 1: Click [⚡Auto] Button

Located in the Camera section next to the [Set] button:
```
Exposure: [100] ms  [Set] [⚡Auto]
```

### Step 2: What Happens

The system will:
1. Stop preview (briefly)
2. Take 10 test images
3. Analyze brightness (targeting 75% of dynamic range)
4. Adjust exposure until brightness is optimal
5. Show result in console: `Auto-exposure: 7.5ms`

### Step 3: Check the Result

After auto-exposure completes:
- The exposure entry field updates to the new value
- FPS indicator updates accordingly
- You can then proceed with recording

### Pro Tips

- **Auto-exposure works best** for initial setup
- **Manual adjustment works better** for fine-tuning
- **Use Auto when**: First time with a new sample preparation
- **Use Manual when**: You want precise control or testing different exposures

---

## Complete Setup Example: 5ms @ 20fps

### GUI Steps

1. **Open the GUI** (mm_odor_recorder_v9.py runs)

2. **Connect Camera**
   - MM section: Click "Connect" button
   - Wait for "Connected: PrimeCam" message

3. **Connect ESP32** (if using odor delivery)
   - Select COM port
   - Click "Connect"

4. **Preview (Optional)**
   - Click "Start Preview" button
   - Should see live camera feed

5. **Set Exposure to 5 ms**
   - Find: Exposure entry field (Camera section)
   - Current value: (shows whatever was last)
   - Action: Clear, type "5", press ENTER or click [Set]
   - Console shows: `[ACQUISITION] Exposure set to 5.00 ms`
   - FPS indicator shows: `→ 200 FPS max`

6. **Set FPS to 20**
   - Find: FPS entry field (Acquisition section)
   - Current value: (default 9)
   - Action: Clear, type "20"
   - No button needed, updates immediately

7. **Select Odor**
   - Odor dropdown: Select "OFM_A" (or your preferred odor)

8. **Set Phase Durations** (in Phases section)
   - Baseline: 15 (seconds)
   - Odor: 4 (seconds)
   - Post: 15 (seconds)
   - Total shown: = 34s

9. **Enter Fly Information** (Experiment section)
   - Fly ID: "fly_001"
   - Genotype: "WT"

10. **Ready to Record**
    - Click "Record" button
    - Console should show:
      ```
      [ACQUISITION] Exposure set to 5.00 ms
      [ACQUISITION] Frame rate set via 'FrameRate': 20.0 Hz
      ```

11. **Monitor Progress**
    - Progress bar fills (acquisition progress)
    - Phase indicator updates (BASELINE → ODOR → POST-ODOR)
    - Live preview shows frames

12. **Recording Complete**
    - Frames saved to disk
    - Log file updated
    - Metrics displayed:
      ```
      Acquired 680 frames @ 19.95 FPS
      ```

---

## Settings Persistence

### How Exposure/FPS Are Applied

The exposure and FPS settings **currently** are handled as follows:

**Exposure:**
- Set via: `Camera.Set` button (line 2603)
- Applied immediately to camera
- Used during acquisition via `exposure_ms` parameter

**FPS:**
- Set via: `Acquisition.FPS` entry field (line 2833)
- Used during acquisition to pace frame selection
- Not pre-applied to camera (unless camera has FrameRate property)

### Making Them "Sticky" (Optional Enhancement)

To save exposure/FPS values so they persist between runs:

**File:** `mm_odor_recorder_v9.py`

**Add to `__init__`:**
```python
def __init__(self, root):
    ...
    # Load defaults from a config file
    self.config = self._load_config()
    ...

def _load_config(self):
    """Load saved settings."""
    try:
        with open("acquisition_settings.json") as f:
            return json.load(f)
    except:
        return {
            "exposure_ms": 5.0,
            "fps": 20.0,
            "odor": "OFM_A"
        }

def _save_config(self):
    """Save current settings."""
    with open("acquisition_settings.json", "w") as f:
        json.dump({
            "exposure_ms": float(self.exp_var.get()),
            "fps": float(self.fps_var.get()),
            "odor": self.odor_var.get()
        }, f)
```

Then call `_save_config()` after recording completes.

---

## Troubleshooting via GUI

### Problem: Exposure Won't Change

**Check:**
1. Is camera connected? (Connection status should show green)
2. Did you click [Set] button? (Or press ENTER?)
3. Check console for error: `Failed to set exposure: ...`

**Fix:**
1. Reconnect camera (click Disconnect, then Connect)
2. Try clicking [Set] again
3. If still failing, check camera is in the right mode (preview must be stopped)

### Problem: FPS Still Shows Wrong Value

**Check:**
1. Is the FPS entry field showing 20? (Not some other number?)
2. Is it less than the max FPS from exposure? (20 ≤ 200 for 5ms exposure)
3. Check console: Does it say `Frame rate set via...`?

**Fix:**
1. Stop preview (if running)
2. Clear FPS field and re-enter: 20
3. In console, look for `[ACQUISITION] Frame rate set via...` message
4. If missing, camera may not support FrameRate property (still OK, will use software pacing)

### Problem: Auto-Exposure Doesn't Work

**Check:**
1. Is camera connected?
2. Is preview running? (Auto-exposure will stop it first, that's normal)
3. Any error messages in console?

**Fix:**
1. Make sure nothing is blocking the camera (lens cap off, not too dark)
2. Try [Set] button manually instead
3. If that fails, reconnect camera

---

## Advanced: Command-Line Defaults

If you want to set exposure/FPS **before GUI launches**:

**Create a file: `acquisition_defaults.json`**
```json
{
  "exposure_ms": 5.0,
  "fps": 20.0,
  "odor": "OFM_A"
}
```

**Add to GUI startup (in `_create_ui()`):**
```python
def _load_defaults(self):
    """Load from JSON file if it exists."""
    try:
        with open("acquisition_defaults.json") as f:
            defaults = json.load(f)
            self.exp_var.set(str(defaults.get("exposure_ms", 5.0)))
            self.fps_var.set(str(defaults.get("fps", 20.0)))
            self.odor_var.set(defaults.get("odor", "OFM_A"))
            self._update_fps_indicator()
    except:
        pass
```

Then call `self._load_defaults()` in `__init__`.

---

## Summary

| Setting | Where in GUI | How to Set | Default |
|---------|--------------|-----------|---------|
| **Exposure** | Camera section | Entry field + [Set] button | 100 ms |
| **Max FPS** | (Indicator) | Auto-calculated from exposure | Varies |
| **Target FPS** | Acquisition section | Entry field (press ENTER) | 9 fps |
| **Odor** | Acquisition section | Dropdown menu | OFM_A |
| **Gain** | Camera section | Slider | 0 |
| **Binning** | Camera section | Dropdown (1/2/4) | 1 |

---

## Next Steps

1. **Run the GUI**: `python mm_odor_recorder_v9.py`
2. **Connect camera**: Click [Connect] in MM section
3. **Set exposure**: Enter "5" in Exposure field, click [Set]
4. **Set FPS**: Enter "20" in FPS field
5. **Start preview**: Click [Start Preview] to verify
6. **Record**: Fill in experiment details and click [Record]

Done! 🎉

---
