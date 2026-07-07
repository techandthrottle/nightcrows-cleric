"""
nightcrows_healer.py — Night Crows Auto-Healer (Dual Client Edition)
======================================================================
- Targets NIGHT CROWS(2) window specifically
- Sends input to background window (no focus stealing)
- Detects and wakes Power Saver Mode automatically
- Rotates heals through F1 → F2 → F3 → F4
- Monitors own HP via OCR, self-heals if below threshold

Requirements:
    pip install pynput pillow pytesseract pywin32

Tesseract OCR:
    https://github.com/UB-Mannheim/tesseract/wiki
    Install to default path: C:\\Program Files\\Tesseract-OCR\\tesseract.exe

Controls (global hotkeys, work even if game is in background):
    F6  — Start healer
    F7  — Toggle self-heal priority
    F8  — Stop healer

FIRST RUN:
    Set CALIBRATE_MODE = True to verify OCR is reading your HP correctly.
"""

"""
nightcrows_healer.py — Night Crows Auto-Healer (Final Perfect Edition v2)
======================================================================
"""

import time
import threading
import re
import pytesseract
import win32gui
import win32con
import win32api
from PIL import ImageGrab, ImageOps, ImageStat
from pynput import keyboard
from pynput.keyboard import Key
from pynput.keyboard import Listener as KeyListener

# ══════════════════════════════════════════════════════════════════════════════
#  CONFIGURATION
# ══════════════════════════════════════════════════════════════════════════════

TESSERACT_PATH = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
GAME_WINDOW_TITLE = "NIGHT CROWS(2)  "

# Perfected Coordinates!
HP_REGION_RELATIVE = (60, 945, 250, 970) 

# ── Self-Heal Settings ────────────────────────────────────────────────────────
SELF_HP_THRESHOLD = 60      
HEAL_COOLDOWN     = 5.0     
CAST_DELAY        = 0.8     

# ── Heal key (Hotbar slot 2) ──────────────────────────────────────────────────
HEAL_VK = ord("2")

# ── Party Settings ────────────────────────────────────────────────────────────
party_heals_enabled = False  # Set to True if you are in a party!
PARTY_VK    = [win32con.VK_F1, win32con.VK_F2, win32con.VK_F3, win32con.VK_F4]
PARTY_NAMES = ["F1", "F2", "F3", "F4"]

# ── Power Saver Detection ─────────────────────────────────────────────────────
POWER_SAVER_BRIGHTNESS_THRESHOLD = 30   
POWER_SAVER_SAMPLE_REGION = (400, 300, 900, 500)

CALIBRATE_MODE = False

# ══════════════════════════════════════════════════════════════════════════════
#  ENGINE
# ══════════════════════════════════════════════════════════════════════════════

pytesseract.pytesseract.tesseract_cmd = TESSERACT_PATH

stop_event    = threading.Event()
running       = False
self_priority = True
party_index   = 0

def find_game_window():
    return win32gui.FindWindow(None, GAME_WINDOW_TITLE)

def get_window_rect(hwnd):
    try:
        rect = win32gui.GetClientRect(hwnd)
        tl   = win32gui.ClientToScreen(hwnd, (rect[0], rect[1]))
        br   = win32gui.ClientToScreen(hwnd, (rect[2], rect[3]))
        return (tl[0], tl[1], br[0], br[1])
    except Exception:
        return None

def get_absolute_bbox(hwnd, relative_box):
    rect = get_window_rect(hwnd)
    if not rect: return None
    return (
        rect[0] + relative_box[0],
        rect[1] + relative_box[1],
        rect[0] + relative_box[2],
        rect[1] + relative_box[3]
    )

def is_power_saver_active(hwnd):
    try:
        bbox = get_absolute_bbox(hwnd, POWER_SAVER_SAMPLE_REGION)
        if not bbox: return False
        img  = ImageGrab.grab(bbox=bbox, all_screens=True)
        gray = img.convert("L")
        avg  = ImageStat.Stat(gray).mean[0]
        return avg < POWER_SAVER_BRIGHTNESS_THRESHOLD
    except Exception:
        return False

def wake_window(hwnd):
    try:
        wr = get_window_rect(hwnd)
        if not wr: return
        cx, cy = (wr[2] - wr[0]) // 2, (wr[3] - wr[1]) // 2
        lparam = win32api.MAKELONG(cx, cy)
        win32api.PostMessage(hwnd, win32con.WM_MOUSEMOVE, 0, lparam)
        time.sleep(0.3)
        _send_vk(hwnd, win32con.VK_SHIFT)
        time.sleep(0.5)
        print("[WAKE] Window woken from Power Saver.")
    except Exception as e:
        print(f"[WARN] Wake failed: {e}")

# Add debugs to _send_vk as well
def _send_vk(hwnd, vk, delay=0.08):
    # Map VK to a readable key name for debugging
    key_name = {
        win32con.VK_F1: "F1", win32con.VK_F2: "F2", win32con.VK_F3: "F3", win32con.VK_F4: "F4",
        ord("2"): "2", win32con.VK_SHIFT: "SHIFT"
    }.get(vk, str(vk))
    
    # print(f"[SEND_VK] Sending KEYDOWN {key_name} to {hwnd}") # Optional: very noisy
    scan = win32api.MapVirtualKey(vk, 0)
    win32api.PostMessage(hwnd, win32con.WM_KEYDOWN, vk, (scan << 16) | 1)
    time.sleep(delay)
    # print(f"[SEND_VK] Sending KEYUP {key_name} to {hwnd}") # Optional: very noisy
    win32api.PostMessage(hwnd, win32con.WM_KEYUP,   vk, (scan << 16) | 0xC0000001)
    time.sleep(0.05)

def cast_heal(hwnd):
    if is_power_saver_active(hwnd):
        print("[CAST_HEAL] Power Saver Active. Waking window...")
        wake_window(hwnd)
    print(f"[CAST_HEAL] Sending heal key (VK: {HEAL_VK}).")
    _send_vk(hwnd, HEAL_VK)
    time.sleep(CAST_DELAY)
    print(f"[CAST_HEAL] Heal cast animation delay finished ({CAST_DELAY}s).")

def target_party(hwnd, index):
    _send_vk(hwnd, PARTY_VK[index])
    time.sleep(0.15)

def read_hp_percent(hwnd, save_debug=False):
    try:
        hp_bbox = get_absolute_bbox(hwnd, HP_REGION_RELATIVE)
        if not hp_bbox: return None
        
        img = ImageGrab.grab(bbox=hp_bbox, all_screens=True)
        img = img.resize((img.width * 5, img.height * 5))
        img = img.convert("L")
        
        img = img.point(lambda p: 255 if p > 120 else 0) 
        img = ImageOps.invert(img)
        
        if save_debug:
            img.save("debug_hp_crop.png")
            
        text  = pytesseract.image_to_string(
                    img,
                    config="--psm 7 -c tessedit_char_whitelist=0123456789/ "
                ).strip()
        
        matches = re.findall(r"(\d+)", text)
        if len(matches) >= 2:
            cur = int(matches[0])
            mx  = int(matches[1])
            if mx > 0:
                return (cur / mx) * 100.0
    except Exception:
        pass
    return None

def self_hp_low(hwnd):
    hp = read_hp_percent(hwnd)
    if hp is None:
        return False
    if hp < SELF_HP_THRESHOLD:
        print(f"[HP] {hp:.1f}% — below {SELF_HP_THRESHOLD}% threshold!")
        return True
    return False

def heal_self(hwnd):
    print("[HEAL] → SELF (priority)")
    # ESC key removed here to prevent Auto-Combat from cancelling!
    cast_heal(hwnd)

def heal_party(hwnd, index):
    print(f"[HEAL_PARTY] Targeting Party {PARTY_NAMES[index]} (VK: {PARTY_VK[index]})")
    _send_vk(hwnd, PARTY_VK[index])
    time.sleep(0.2) # Increased delay after target - experiment with 0.2, 0.3, 0.5
    print(f"[HEAL_PARTY] Casting heal (VK: {HEAL_VK}).")
    cast_heal(hwnd)

def interruptible_sleep(seconds):
    end = time.time() + seconds
    while time.time() < end:
        if stop_event.is_set():
            return False
        time.sleep(0.05)
    return True

def healer_loop():
    global running, party_index

    hwnd = find_game_window()
    if not hwnd:
        running = False
        print("[HEALER_LOOP] Game window not found at start. Stopping.") # Added debug
        return

    print(f"[START] Healer running  |  threshold={SELF_HP_THRESHOLD}%")
    print(f"        Party Heals: {'ON' if party_heals_enabled else 'OFF'}")
    print(f"        Self-heal priority: {'ON' if self_priority else 'OFF'}") # Added debug
    print("        Press F8 to stop.\n")

    while not stop_event.is_set():
        hwnd = find_game_window()
        if not hwnd:
            print("[HEALER_LOOP] Game window lost during loop, attempting to re-find...") # Added debug
            if not interruptible_sleep(3): break
            continue

        # Check self HP
        if self_priority and self_hp_low(hwnd):
            print("[HEALER_LOOP] Self-HP low, prioritizing self-heal.") # Added debug
            heal_self(hwnd)
            if not interruptible_sleep(HEAL_COOLDOWN): break
            print(f"[HEALER_LOOP] Self-heal complete, waiting {HEAL_COOLDOWN}s.") # Added debug
            continue # Go back to the start of the loop to check HP again

        # Party rotation (if enabled)
        if party_heals_enabled:
            current_party_member_name = PARTY_NAMES[party_index] # Get name for debug print
            print(f"[HEALER_LOOP] Attempting to heal Party {current_party_member_name} (index {party_index}).") # Added debug
            
            heal_party(hwnd, party_index)
            party_index = (party_index + 1) % len(PARTY_VK)
            
            print(f"[HEALER_LOOP] Party heal sent. Next target: {PARTY_NAMES[party_index]}.") # Added debug
            
            # Wait for cooldown after party heal
            cooldown_end = time.time() + HEAL_COOLDOWN
            print(f"[HEALER_LOOP] Entering party heal cooldown for {HEAL_COOLDOWN}s.") # Added debug
            while time.time() < cooldown_end:
                if stop_event.is_set(): 
                    print("[HEALER_LOOP] Stop event set during cooldown.") # Added debug
                    break
                # Crucial check: if self_priority is on and HP drops during party heal cooldown, self-heal.
                if self_priority and self_hp_low(hwnd):
                    print("[HEALER_LOOP] Self-HP low during party cooldown, interrupting for self-heal.") # Added debug
                    heal_self(hwnd)
                    cooldown_end = time.time() + HEAL_COOLDOWN # Reset cooldown after self-heal
                    print(f"[HEALER_LOOP] Self-heal complete, cooldown reset to {HEAL_COOLDOWN}s.") # Added debug
                time.sleep(0.5) # Shorter sleep to allow quicker self-heal interruption check
        else:
            # If solo, just wait a moment before checking HP again
            # Only relevant if party_heals_enabled is False.
            # print("[HEALER_LOOP] Party heals disabled, short delay.") # Optional: too noisy perhaps
            time.sleep(0.5)

    running = False
    print("[STOP] Healer stopped.")
def calibration_loop():
    print("[CALIBRATE] Running — Ctrl+C to exit\n")
    hwnd = find_game_window()
    if not hwnd:
        print("Window not found!")
        return
    
    print(f"  HP region (relative) : {HP_REGION_RELATIVE}\n")

    while True:
        try:
            ps  = is_power_saver_active(hwnd)
            hp  = read_hp_percent(hwnd)
            print(f"  HP: {f'{hp:.1f}%' if hp else 'FAILED':<12}  Power Saver: {'YES' if ps else 'no'}")
            time.sleep(1.0)
        except KeyboardInterrupt:
            print("\n[CALIBRATE] Done.")
            break

def on_press(key):
    global running, self_priority, party_index

    if key == Key.f6:
        if running: return
        stop_event.clear()
        party_index = 0
        running = True
        threading.Thread(target=healer_loop, daemon=True).start()

    elif key == Key.f7:
        self_priority = not self_priority
        print(f"[CONFIG] Self-heal priority: {'ON' if self_priority else 'OFF'}")

    elif key == Key.f9: # New hotkey for toggling party heals
        global party_heals_enabled
        party_heals_enabled = not party_heals_enabled
        print(f"[CONFIG] Party Heals: {'ON' if party_heals_enabled else 'OFF'}")

    elif key == Key.f8:
        if running:
            stop_event.set()

def main():
    print("=" * 58)
    print("  Night Crows Auto Healer [Final Perfect Edition v2]")
    print("=" * 58)
    
    if CALIBRATE_MODE:
        calibration_loop()
        return

    with KeyListener(on_press=on_press) as listener:
        listener.join()

if __name__ == "__main__":
    main()