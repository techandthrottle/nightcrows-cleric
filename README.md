# Night Crows Cleric — Auto Healer

A Tkinter-based helper for the Cleric (support) playstyle in Night Crows Global. It
automates a healer rotation, timed buffs, and anti-AFK activity against a selected
game window, with an in-app debug log panel.

> **Note:** This is personal-use tooling. Automating input may conflict with the
> game's Terms of Service — use at your own risk.

## Features

- **Game window selection** — auto-detects `NIGHT CROWS` windows and lets you pick one.
- **Rotational healing** — cycles heals across selected party members (F1–F4) and
  optionally self, on a configurable cooldown.
- **Timed buffs** — presses configured hotbar keys on an interval.
- **Anti-AFK** — random WASD movement, safe-key presses, or disabled.
- **Power-saver detection** — wakes a dimmed game window before casting.
- **In-app debug log** — all output is shown inside the GUI (no separate terminal).

## Requirements

- Windows
- Python 3.11+
- [Tesseract-OCR](https://github.com/tesseract-ocr/tesseract) installed at
  `C:\Program Files\Tesseract-OCR\tesseract.exe` (path configurable in the script)
- Python packages: `pytesseract`, `pywin32`, `pillow`, `pynput`

```bash
pip install pytesseract pywin32 pillow pynput
```

## Running from source

```bash
python nc_macro_gui.py
```

## Building the executable

```bash
pyinstaller nc_macro_gui.spec
```

The built app is written to `dist\nc_macro_gui.exe` (windowed — no console).

## Files

| File | Purpose |
| --- | --- |
| `nc_macro_gui.py` | Main GUI application |
| `nc_macro.py` | Original CLI macro (pre-GUI) |
| `nc_macro_gui.spec` | PyInstaller build spec |
| `calibrate_hp.py`, `find_hp_region.py`, `diagnose_hp.py` | OCR calibration / diagnostic helpers |
| `find_windows.py` | Lists visible window titles |
| `read_hp_memory.py` | Experimental memory-read prototype |
