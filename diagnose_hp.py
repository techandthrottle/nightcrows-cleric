"""
diagnose_hp.py — HP Region Diagnostic
======================================
Captures the window with PrintWindow, measures the title bar offset,
saves the full capture and a precise HP crop so we can verify coordinates.

Requirements:
    pip install pillow pywin32 pytesseract

Usage:
    python diagnose_hp.py
"""

import ctypes
import time
import re
import win32gui
import win32ui
import win32con
import win32api
import pytesseract
from PIL import Image, ImageOps

TESSERACT_PATH    = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
GAME_WINDOW_TITLE = "NIGHT CROWS(2)  "

pytesseract.pytesseract.tesseract_cmd = TESSERACT_PATH


def find_window():
    hwnd = win32gui.FindWindow(None, GAME_WINDOW_TITLE)
    if not hwnd:
        print(f"[ERROR] Window not found: '{GAME_WINDOW_TITLE}'")
        return None
    return hwnd


def capture_window_full(hwnd):
    """Capture entire window INCLUDING title bar."""
    # GetWindowRect includes title bar, borders
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    width  = right  - left
    height = bottom - top

    hwnd_dc = win32gui.GetWindowDC(hwnd)
    mfc_dc  = win32ui.CreateDCFromHandle(hwnd_dc)
    save_dc = mfc_dc.CreateCompatibleDC()
    bmp     = win32ui.CreateBitmap()
    bmp.CreateCompatibleBitmap(mfc_dc, width, height)
    save_dc.SelectObject(bmp)

    PW_RENDERFULLCONTENT = 0x00000002
    result = ctypes.windll.user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), PW_RENDERFULLCONTENT)
    if result == 0:
        ctypes.windll.user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), 0)

    bmp_info = bmp.GetInfo()
    bmp_bits = bmp.GetBitmapBits(True)
    img = Image.frombuffer(
        "RGB",
        (bmp_info["bmWidth"], bmp_info["bmHeight"]),
        bmp_bits, "raw", "BGRX", 0, 1
    )

    win32gui.DeleteObject(bmp.GetHandle())
    save_dc.DeleteDC()
    mfc_dc.DeleteDC()
    win32gui.ReleaseDC(hwnd, hwnd_dc)

    return img, (left, top, right, bottom)


def capture_client_only(hwnd):
    """Capture ONLY the client area (no title bar)."""
    rect   = win32gui.GetClientRect(hwnd)
    width  = rect[2]
    height = rect[3]

    # Get the DC of just the client area
    client_dc = win32gui.GetDC(hwnd)
    mfc_dc    = win32ui.CreateDCFromHandle(client_dc)
    save_dc   = mfc_dc.CreateCompatibleDC()
    bmp       = win32ui.CreateBitmap()
    bmp.CreateCompatibleBitmap(mfc_dc, width, height)
    save_dc.SelectObject(bmp)

    PW_CLIENTONLY        = 0x00000001
    PW_RENDERFULLCONTENT = 0x00000002
    result = ctypes.windll.user32.PrintWindow(
        hwnd, save_dc.GetSafeHdc(),
        PW_CLIENTONLY | PW_RENDERFULLCONTENT
    )
    if result == 0:
        ctypes.windll.user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), PW_CLIENTONLY)

    bmp_info = bmp.GetInfo()
    bmp_bits = bmp.GetBitmapBits(True)
    img = Image.frombuffer(
        "RGB",
        (bmp_info["bmWidth"], bmp_info["bmHeight"]),
        bmp_bits, "raw", "BGRX", 0, 1
    )

    win32gui.DeleteObject(bmp.GetHandle())
    save_dc.DeleteDC()
    mfc_dc.DeleteDC()
    win32gui.ReleaseDC(hwnd, client_dc)

    return img


def scan_for_hp_text(img, label=""):
    """Scan bottom 40% of image row by row to find HP text."""
    w, h   = img.size
    start_y = int(h * 0.6)
    found  = []

    for y in range(start_y, h - 10, 2):
        crop   = img.crop((0, y, min(300, w), y + 16))
        crop_u = crop.resize((crop.width * 5, crop.height * 5))
        gray   = crop_u.convert("L")
        thresh = gray.point(lambda p: 255 if p > 100 else 0)
        inv    = ImageOps.invert(thresh)
        text   = pytesseract.image_to_string(
                     inv,
                     config="--psm 7 -c tessedit_char_whitelist=0123456789/"
                 ).strip()
        match  = re.search(r"(\d{4,})/(\d{4,})", text)
        if match:
            found.append((y, y + 16, match.group(0)))
            print(f"  [{label}] ✅ HP FOUND at y={y}-{y+16}: {match.group(0)}")

    if not found:
        print(f"  [{label}] ❌ No HP text found in bottom 40% of image")
    return found


def main():
    print("=" * 58)
    print("  Night Crows — HP Diagnostic")
    print("=" * 58)

    hwnd = find_window()
    if not hwnd:
        input("\nPress Enter to exit...")
        return

    # Measure window dimensions
    win_rect              = win32gui.GetWindowRect(hwnd)
    client_rect           = win32gui.GetClientRect(hwnd)
    client_screen_tl      = win32gui.ClientToScreen(hwnd, (0, 0))

    win_w   = win_rect[2]  - win_rect[0]
    win_h   = win_rect[3]  - win_rect[1]
    cli_w   = client_rect[2]
    cli_h   = client_rect[3]
    title_h = client_screen_tl[1] - win_rect[1]
    border  = client_screen_tl[0] - win_rect[0]

    print(f"\n  Window rect (screen) : {win_rect}")
    print(f"  Client size          : {cli_w} x {cli_h}")
    print(f"  Client screen TL     : {client_screen_tl}")
    print(f"  Title bar height     : {title_h} px")
    print(f"  Left border          : {border} px")

    print(f"\n  Capturing in 3 seconds — make sure game is visible...")
    time.sleep(3)

    # ── Capture 1: Full window ─────────────────────────────────────────────────
    print("\n  [1] Capturing FULL window (with title bar)...")
    full_img, full_rect = capture_window_full(hwnd)
    full_img.save("diag_full_window.png")
    print(f"      Size: {full_img.size} — saved: diag_full_window.png")
    scan_for_hp_text(full_img, "FULL")

    # ── Capture 2: Client area only ────────────────────────────────────────────
    print("\n  [2] Capturing CLIENT AREA only (no title bar)...")
    client_img = capture_client_only(hwnd)
    client_img.save("diag_client_area.png")
    print(f"      Size: {client_img.size} — saved: diag_client_area.png")
    scan_for_hp_text(client_img, "CLIENT")

    # ── Save bottom strips of both for visual inspection ──────────────────────
    for name, img in [("full", full_img), ("client", client_img)]:
        w, h  = img.size
        strip = img.crop((0, int(h * 0.85), min(350, w), h))
        strip.save(f"diag_bottom_{name}.png")
        print(f"\n  Bottom strip saved: diag_bottom_{name}.png")

    print("\n  DONE. Share diag_client_area.png and the console output here.")
    input("\nPress Enter to exit...")


if __name__ == "__main__":
    main()