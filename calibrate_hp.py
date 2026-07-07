"""
calibrate_hp.py — Visual HP Region Calibrator
===============================================
Shows exactly what the script is capturing for OCR.
Saves a zoomed screenshot of the HP region so you can verify it looks correct.

Usage:
    python calibrate_hp.py

Then open 'hp_region_capture.png' to see what OCR is reading.
Adjust HP_REGION_RELATIVE until the HP numbers are clearly visible in the image.
"""

import win32gui
import win32con
from PIL import ImageGrab, ImageFilter, ImageEnhance
import pytesseract
import re
import time

# ── Match these to your healer script ─────────────────────────────────────────
TESSERACT_PATH    = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
GAME_WINDOW_TITLE = "NIGHT CROWS(2)  "   # Update if find_windows.py showed different title

# Adjust these until the capture looks correct
HP_REGION_RELATIVE = (55, 728, 210, 748)

# ──────────────────────────────────────────────────────────────────────────────

pytesseract.pytesseract.tesseract_cmd = TESSERACT_PATH


def find_window():
    hwnd = win32gui.FindWindow(None, GAME_WINDOW_TITLE)
    if not hwnd:
        print(f"[ERROR] Window not found: '{GAME_WINDOW_TITLE}'")
        return None
    return hwnd


def get_client_screen_pos(hwnd):
    rect = win32gui.GetClientRect(hwnd)
    tl   = win32gui.ClientToScreen(hwnd, (rect[0], rect[1]))
    br   = win32gui.ClientToScreen(hwnd, (rect[2], rect[3]))
    return tl[0], tl[1], br[0], br[1]


def capture_and_save(hwnd):
    wx, wy, wr, wb = get_client_screen_pos(hwnd)
    window_w = wr - wx
    window_h = wb - wy

    print(f"\n  Game window position : x={wx}, y={wy}")
    print(f"  Game window size     : {window_w} x {window_h}")

    # Compute absolute screen coords of HP region
    abs_region = (
        wx + HP_REGION_RELATIVE[0],
        wy + HP_REGION_RELATIVE[1],
        wx + HP_REGION_RELATIVE[2],
        wy + HP_REGION_RELATIVE[3],
    )
    print(f"  HP region (relative) : {HP_REGION_RELATIVE}")
    print(f"  HP region (screen)   : {abs_region}")

    # Capture raw
    raw = ImageGrab.grab(bbox=abs_region)
    raw.save("hp_region_raw.png")
    print(f"\n  Saved raw capture    : hp_region_raw.png  ({raw.size[0]}x{raw.size[1]}px)")

    # Process for OCR (same pipeline as healer)
    processed = raw.resize((raw.width * 3, raw.height * 3))
    processed = processed.convert("L")
    processed = ImageEnhance.Contrast(processed).enhance(3.0)
    processed = processed.filter(ImageFilter.SHARPEN)
    processed.save("hp_region_processed.png")
    print(f"  Saved processed      : hp_region_processed.png")

    # Run OCR
    text = pytesseract.image_to_string(
        processed,
        config="--psm 7 -c tessedit_char_whitelist=0123456789/"
    ).strip()
    print(f"\n  Raw OCR text         : '{text}'")

    match = re.search(r"(\d+)/(\d+)", text)
    if match:
        cur = int(match.group(1))
        mx  = int(match.group(2))
        pct = (cur / mx * 100) if mx > 0 else 0
        print(f"  HP reading           : {cur}/{mx}  ({pct:.1f}%)")
        print("\n  ✅ OCR is working correctly!")
    else:
        print("  ❌ OCR failed to read HP numbers.")
        print("\n  HOW TO FIX:")
        print("  1. Open hp_region_raw.png — does it show your HP bar/numbers?")
        print("     - If the image is black/wrong area: adjust HP_REGION_RELATIVE")
        print("     - If numbers are visible but OCR fails: the processed image may need tuning")
        print()
        print("  2. Your game window size may differ from the screenshot used.")
        print("     Try these adjustments to HP_REGION_RELATIVE:")
        print("     - Move UP    : decrease the Y values (index 1 and 3)")
        print("     - Move DOWN  : increase the Y values")
        print("     - Move LEFT  : decrease the X values (index 0 and 2)")
        print("     - Move RIGHT : increase the X values")
        print("     - Make TALLER: decrease index 1 or increase index 3")
        print("     - Make WIDER : decrease index 0 or increase index 2")
        print()
        print("  Current value: HP_REGION_RELATIVE =", HP_REGION_RELATIVE)
        print("  Example wider+taller: HP_REGION_RELATIVE = (40, 720, 220, 758)")


def main():
    print("=" * 55)
    print("  Night Crows — HP Region Calibrator")
    print("=" * 55)

    hwnd = find_window()
    if not hwnd:
        input("\nPress Enter to exit...")
        return

    print(f"\n  Found window: '{GAME_WINDOW_TITLE}'")
    print("  Make sure your HP is visible in-game (not in menus).")
    print("\n  Capturing in 3 seconds — switch to the game now if needed...")
    time.sleep(3)

    capture_and_save(hwnd)

    print("\n  Open the saved PNG files to see what is being captured.")
    input("\n  Press Enter to exit...")


if __name__ == "__main__":
    main()