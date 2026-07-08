# Revolt — Night Crows Cleric BOT

A Tkinter-based bot for the Cleric (support) playstyle in Night Crows Global.
It reads HP bars off the screen and reactively heals you and your party, plus timed
buffs and anti-AFK — all against a selected game window, with an in-app debug log.

> **Note:** This is personal-use tooling. Automating input may conflict with the
> game's Terms of Service — use at your own risk.

## Features

- **Game window selection** — auto-detects `NIGHT CROWS` windows and lets you pick one.
- **Reactive self-healing** — reads your own HP bar and heals when it drops below a
  threshold, with a separate **panic** threshold that ignores the cooldown.
- **Reactive party healing** — auto-detects the party HP bars (bottom-center row),
  including the party size, and heals the lowest member below the threshold, with
  its own **panic** threshold. Far/out-of-range members (dimmed bars) are skipped.
  No manual party-size setting — bars are located from the pixels each cycle, so
  adding/removing members is handled automatically.
- **HP detection by color-fill** — instead of fragile OCR, HP is measured from the
  vivid-red fill of the bar within a resolution-independent (fractional) band, so
  calibration survives window resizing.
- **Heal priority** — self-panic → party-panic → self-heal → party-heal.
- **Timed buffs** — presses configured hotbar keys on an interval.
- **Anti-AFK** — random WASD movement, safe-key presses, or disabled.
- **Power-saver detection** — wakes a dimmed game window before casting.
- **Visual calibrator** — a `Calibrate` window where you drag boxes over your HP bar
  and a party member's HP bar; built-in `Test HP Read` / `Test Party Read` show a
  live annotated preview so you can verify before saving.
- **Settings persistence** — all options are saved to `nc_macro_config.json` and
  restored on the next launch.
- **In-app debug log** — all output is shown inside the GUI (no separate terminal).

## Requirements

- Windows
- Python 3.11+
- [Tesseract-OCR](https://github.com/tesseract-ocr/tesseract) installed at
  `C:\Program Files\Tesseract-OCR\tesseract.exe` (only used by the legacy OCR helpers)
- Python packages: `pytesseract`, `pywin32`, `pillow`, `numpy`, `pynput`

```bash
pip install pytesseract pywin32 pillow numpy pynput
```

## Running as administrator (important)

Night Crows usually runs elevated, and Windows blocks input from a non-elevated
process to an elevated window. So **run the bot elevated too**, or its key presses
won't reach the game.

- **From source:** open PowerShell **as administrator**, then:
  ```powershell
  cd D:\script
  python nc_macro_gui.py
  ```
  If `python` isn't found in the admin context (per-user installs often aren't on
  the admin PATH), use its full path, e.g.
  `& "C:\Users\<you>\AppData\Local\Programs\Python\Python313\python.exe" nc_macro_gui.py`.
- **Built exe:** right-click `dist\Revolt.exe` → **Run as administrator**.

The GUI has no console output — everything goes to the in-app **Debug Log** panel,
so the launching terminal just idles while the window is open.

## Building the executable

```bash
pyinstaller nc_macro_gui.spec
```

The built app is written to `dist\Revolt.exe` (windowed — no console).

## Calibrating HP detection

Positions are stored as fractions of the window, so calibration transfers to
different resolutions as long as the game HUD scales with the window. On a new PC,
recalibrate once with the visual calibrator:

1. Select your game window and click **Calibrate**. A screenshot of your window opens.
2. With **My HP bar** selected, **drag a box around your own HP bar** (left edge to
   right edge). Then choose **A party member's HP bar** and drag a box around *any
   one* party member's HP bar — only its width and height are read (not its
   position), so it doesn't matter which member you mark or how many are in your
   party; the count and positions are detected automatically at runtime. Click
   **Save & Close**.
3. Verify (buttons in the Calibrate window): **Test HP Read** pops up the detected
   self fill (a full bar should read ~100%); **Test Party Read** pops up the party
   row with each member boxed (green = near/healable, yellow = far) and its percent.

Fine-tuning: the Red min / margin colors rarely need changing (they're the same on
every resolution). To restrict which party slots are healed, tick specific F-keys
(leftmost bar = F1); leave all unchecked to heal everyone.

## Usage

1. Select the game window (**Refresh** if it's not listed).
2. Set the **Heal Hotbar Key**, **Heal Cooldown**, and **Cast Delay** in General Settings.
3. **Reactive Self-Heal:** enable it, calibrate the search band + Red values, and set
   **Self heal below %** and **Self panic below %**.
4. **Reactive Party Heal:** enable it and set **Party heal below %** / **Party panic
   below %**. Party size is auto-detected. Optionally tick specific F-keys to limit
   which slots to heal (leftmost bar = F1); leave all unchecked to heal everyone.
5. Click **Start Script**; watch the **Debug Log** for `[REACTIVE]` / `[PARTY]` lines.

Thresholds are "heal at/below this percent." A very high heal threshold (e.g. 95%)
tops members off constantly; ~75–85% heal with ~35–45% panic is a saner starting
point. Heal priority is: self-panic → party-panic → self-heal → party-heal.

## Files

| File | Purpose |
| --- | --- |
| `nc_macro_gui.py` | Main GUI application |
| `nc_macro.py` | Original CLI macro (pre-GUI) |
| `nc_macro_gui.spec` | PyInstaller build spec |
| `calibrate_hp.py`, `find_hp_region.py`, `diagnose_hp.py` | Legacy OCR calibration / diagnostic helpers |
| `find_windows.py` | Lists visible window titles |
| `read_hp_memory.py` | Experimental memory-read prototype (unused) |

Runtime-generated files (git-ignored): `nc_macro_config.json` (settings),
`debug_*.png` (calibration images), `game_capture_*.png` (captures).
