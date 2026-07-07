"""
find_hp_region_pw.py — HP Region Finder (PrintWindow API)
==========================================================
Uses Windows PrintWindow API to capture the game window directly.
Works regardless of which monitor the window is on, including
negative coordinate monitors (left-side monitors).

Requirements:
    pip install pillow pywin32

Usage:
    python find_hp_region_pw.py
"""

import time
import os
import ctypes
import win32gui
import win32ui
import win32con
from PIL import Image

# ── Update if your window title differs ───────────────────────────────────────
GAME_WINDOW_TITLE = "NIGHT CROWS(2)  "

# ── Scan settings ─────────────────────────────────────────────────────────────
STRIP_HEIGHT    = 20
SCAN_FROM_PCT   = 60    # Start from 60% down the window
STRIP_WIDTH_PCT = 30    # Left 30% where HP bar lives

OUTPUT_DIR = "hp_strips"

# ─────────────────────────────────────────────────────────────────────────────

def find_window():
    hwnd = win32gui.FindWindow(None, GAME_WINDOW_TITLE)
    if not hwnd:
        print(f"[ERROR] Window not found: '{GAME_WINDOW_TITLE}'")
        return None
    return hwnd


def capture_window(hwnd):
    """
    Capture a window using PrintWindow — works in background,
    on any monitor, including negative coordinate screens.
    """
    # Get client area size
    rect   = win32gui.GetClientRect(hwnd)
    width  = rect[2] - rect[0]
    height = rect[3] - rect[1]

    if width == 0 or height == 0:
        print("[ERROR] Window has zero size. Is it minimized?")
        return None

    # Create device contexts and bitmap
    hwnd_dc  = win32gui.GetWindowDC(hwnd)
    mfc_dc   = win32ui.CreateDCFromHandle(hwnd_dc)
    save_dc  = mfc_dc.CreateCompatibleDC()
    bmp      = win32ui.CreateBitmap()
    bmp.CreateCompatibleBitmap(mfc_dc, width, height)
    save_dc.SelectObject(bmp)

    # PrintWindow flags: PW_CLIENTONLY = 1, PW_RENDERFULLCONTENT = 2
    # Use PW_RENDERFULLCONTENT to capture GPU-rendered content
    PW_RENDERFULLCONTENT = 0x00000002
    result = ctypes.windll.user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), PW_RENDERFULLCONTENT)

    if result == 0:
        print("[WARN] PrintWindow returned 0 — trying fallback flag...")
        result = ctypes.windll.user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), 1)

    # Convert to PIL Image
    bmp_info = bmp.GetInfo()
    bmp_bits = bmp.GetBitmapBits(True)
    img = Image.frombuffer(
        "RGB",
        (bmp_info["bmWidth"], bmp_info["bmHeight"]),
        bmp_bits, "raw", "BGRX", 0, 1
    )

    # Cleanup
    win32gui.DeleteObject(bmp.GetHandle())
    save_dc.DeleteDC()
    mfc_dc.DeleteDC()
    win32gui.ReleaseDC(hwnd, hwnd_dc)

    return img


def main():
    print("=" * 58)
    print("  Night Crows — HP Finder (PrintWindow API)")
    print("=" * 58)

    hwnd = find_window()
    if not hwnd:
        input("\nPress Enter to exit...")
        return

    rect   = win32gui.GetClientRect(hwnd)
    win_w  = rect[2] - rect[0]
    win_h  = rect[3] - rect[1]
    tl     = win32gui.ClientToScreen(hwnd, (0, 0))

    print(f"\n  Window handle   : {hwnd}")
    print(f"  Screen position : {tl}")
    print(f"  Client size     : {win_w} x {win_h} px")

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print(f"\n  Capturing window in 3 seconds...")
    time.sleep(3)

    img = capture_window(hwnd)
    if img is None:
        print("[ERROR] Capture failed.")
        input("\nPress Enter to exit...")
        return

    img_w, img_h = img.size
    print(f"  Captured image  : {img_w} x {img_h} px")

    # Check if image is all black
    gray    = img.convert("L")
    pixels  = list(gray.getdata())
    avg_brightness = sum(pixels) / len(pixels)
    print(f"  Avg brightness  : {avg_brightness:.1f} (0=black, 255=white)")

    if avg_brightness < 5:
        print("\n  [WARN] Image appears to be all black.")
        print("  The game may use anti-capture protection.")
        print("  Try: Run script as Administrator, or set game to Windowed mode.")

    # Save full window
    full_path = os.path.join(OUTPUT_DIR, "full_window_capture.png")
    img.save(full_path)
    print(f"\n  ✅ Full window saved: {full_path}")
    print("  ★  Share this image here — I'll find the exact HP coordinates!")

    # Save strips of the bottom portion
    strip_w     = int(img_w * STRIP_WIDTH_PCT / 100)
    scan_top    = int(img_h * SCAN_FROM_PCT   / 100)
    scan_bottom = img_h

    count = 0
    y = scan_top
    while y + STRIP_HEIGHT <= scan_bottom:
        strip    = img.crop((0, y, strip_w, y + STRIP_HEIGHT))
        upscaled = strip.resize((strip.width * 3, strip.height * 3))
        fname    = os.path.join(OUTPUT_DIR, f"strip_{count:02d}_y{y}.png")
        upscaled.save(fname)
        y += STRIP_HEIGHT
        count += 1

    print(f"  Saved {count} strips to '{OUTPUT_DIR}/'")
    print()
    print("  Open full_window_capture.png and share it here.")
    print("  I will read the HP bar position and update the script for you.")

    input("\nPress Enter to exit...")


if __name__ == "__main__":
    main()