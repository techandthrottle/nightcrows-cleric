import random
import tkinter as tk
from tkinter import ttk, messagebox
from tkinter.scrolledtext import ScrolledText
import threading
import time
import re
import pytesseract
import win32gui
import win32con
import win32api
from PIL import ImageGrab, ImageOps, ImageStat, Image, ImageDraw, ImageTk
import numpy as np
from pynput import keyboard
from pynput.keyboard import Key
from pynput.keyboard import Listener as KeyListener
import logging
import sys
import queue
import os
import json

# ──────────────────────────────────────────────────────────────────────────────
#  Console output → in-app log panel
#  All print()/logging output is teed into this queue, which the GUI drains on a
#  timer and renders inside the window. This removes the need for a separate
#  terminal window for debugging.
# ──────────────────────────────────────────────────────────────────────────────
log_queue = queue.Queue()


class _QueueStream:
    """File-like object that routes writes to the GUI log queue only.
    Output is not mirrored to any terminal."""

    def write(self, text):
        if text:
            log_queue.put(text)
        return len(text) if text else 0

    def flush(self):
        pass


# Redirect stdout/stderr so every print() lands in the GUI log panel only.
sys.stdout = _QueueStream()
sys.stderr = _QueueStream()

# Configure logging to the same stream.
log_formatter = logging.Formatter('%(levelname)s: %(message)s')
root_logger = logging.getLogger()
root_logger.setLevel(logging.DEBUG)

# Clear existing handlers to prevent duplicate output if basicConfig was called elsewhere
if root_logger.hasHandlers():
    root_logger.handlers.clear()

console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(log_formatter)
root_logger.addHandler(console_handler)

# Import the core logic from nc_macro.py (will need refactoring)
# For now, I'll copy relevant parts and adapt them.

# ══════════════════════════════════════════════════════════════════════════════
#  CONFIGURATION (from nc_macro.py, will be dynamic in GUI)
# ══════════════════════════════════════════════════════════════════════════════

TESSERACT_PATH = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
GAME_WINDOW_TITLE = "NIGHT CROWS(2)  "

HP_REGION_RELATIVE = (60, 945, 250, 970)

# ── Reactive (HP-based) self-healing ──────────────────────────────────────────
# Band framing the HP bar, as fractions (0-1) of the window client area:
# (left, top, right, bottom). Left edge ≈ bar start, right edge ≈ bar end. Because
# it is relative, calibration survives window resizing (the HUD scales with it).
# Calibrate with the "Test HP Read" button + debug_hp_fill.png.
HP_SEARCH_BAND_FRAC = (0.045, 0.950, 0.130, 0.963)
# HP fill is vivid RED: red channel bright and clearly dominant over green/blue.
# Detected by RGB (not hue). White digits (R≈G≈B), the brown background/dark track
# (low red), and the blue mana bar all fail this, so only the fill matches. 0-255.
HP_RED_MIN    = 120   # a "filled" pixel needs at least this much red
HP_RED_MARGIN = 70    # ...and red must exceed green AND blue by at least this much

# ── Reactive party healing (auto-detects party members' HP bars) ──────────────
# Party HP bars form a horizontal, center-anchored row at the bottom-center. Bars
# are found directly in the pixels each cycle (no manual party size): each bar's
# left edge is snapped to a fixed centered grid to recover its slot (F1..F4), and
# the party size N is inferred from the outermost visible bars. Geometry is in
# fractions of the window client area, so it scales with the window.
PARTY_BAR_CENTER_X_FRAC = 0.5075   # horizontal center of the whole row (x~574 @1131)
PARTY_BAR_SPACING_FRAC  = 0.0964   # center-to-center spacing between slots (~109px)
PARTY_BAR_WIDTH_FRAC    = 0.0619   # width of one member's bar (~70px)
PARTY_BAR_Y_FRAC        = 0.8838   # vertical center of the bars
PARTY_BAR_HALFH_FRAC    = 0.0090   # half-height of the sampling band
# Center-to-center spacing is a fixed proportion of the bar width in the UI layout,
# so calibrating one bar's width also gives the spacing (spacing = width * ratio).
PARTY_SPACING_WIDTH_RATIO = PARTY_BAR_SPACING_FRAC / PARTY_BAR_WIDTH_FRAC
PARTY_ROW_XL_FRAC       = 0.30     # search span for the whole party row (left)
PARTY_ROW_XR_FRAC       = 0.70     # search span for the whole party row (right)
# Near members show bright red bars (R~200+); far members show dim red (R~125).
# The DIM thresholds detect a bar's presence (near or far) to locate/count it;
# the BRIGHT thresholds decide whether it is near enough to heal.
PARTY_DIM_RED_MIN    = 100
PARTY_DIM_MARGIN     = 50
PARTY_RED_MIN        = 160
PARTY_RED_MARGIN     = 70
PARTY_BAR_MIN_RED_PX = 3       # fewer bright-red px than this => far => skip healing
# Each real party member also has a teal MP bar just below the red HP bar. Requiring
# it rejects stray game-world red (damage numbers, effects) that would otherwise be
# mistaken for a bar. Teal = blue-dominant.
PARTY_TEAL_B_MIN     = 100
PARTY_TEAL_MARGIN    = 40      # blue AND green must exceed red by at least this much
PARTY_HEAL_THRESHOLD  = 70.0   # heal a member at/below this percent
PARTY_PANIC_THRESHOLD = 35.0   # emergency: heal a member at/below this immediately,
                               # ignoring cooldown, above self and normal party heals
# Heal self when HP drops to/below this percent (respects the heal cooldown).
SELF_HEAL_THRESHOLD  = 70.0
# Emergency: heal self immediately, jumping the party rotation, at/below this percent.
SELF_PANIC_THRESHOLD = 35.0


HEAL_COOLDOWN     = 5.0
CAST_DELAY        = 0.8

HEAL_VK = ord("2")

BUFF_HOTBAR_KEYS = [ord('5'), ord('6'), ord('7'), ord('8')]

POWER_SAVER_BRIGHTNESS_THRESHOLD = 30
POWER_SAVER_SAMPLE_REGION = (400, 300, 900, 500)

# Settings are persisted next to the executable (frozen) or this script (source),
# so a calibrated setup survives restarts.
if getattr(sys, "frozen", False):
    _CONFIG_DIR = os.path.dirname(sys.executable)
else:
    _CONFIG_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(_CONFIG_DIR, "nc_macro_config.json")

def find_crow_windows():
    results = []

    def callback(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd)
            # Debug print: show the exact title captured
            print(f"DEBUG: Detected window title: '{title}' (Length: {len(title)})") # ADDED DEBUG PRINT
            # Check if "CROW" is in the title (case-insensitive) and title is not empty
            if "CROW" in title.upper() and title:
                results.append(title) # Append the original title, including trailing spaces

    win32gui.EnumWindows(callback, None)
    # Use a set to get unique titles, then sort.
    # We need to preserve trailing spaces, so we don't strip here.
    return sorted(list(set(results)))

# ══════════════════════════════════════════════════════════════════════════════
#  ENGINE (adapted from nc_macro.py)
# ══════════════════════════════════════════════════════════════════════════════

pytesseract.pytesseract.tesseract_cmd = TESSERACT_PATH

stop_event    = threading.Event()
running       = False
last_buff_press_time = time.time() # Initialize timer for buff presses
BUFF_INTERVAL = 5 * 60 # 5 minutes in seconds


def find_game_window(game_window_title_param):
    return win32gui.FindWindow(None, game_window_title_param)

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

def _send_vk(hwnd, vk, delay=0.08):
    scan = win32api.MapVirtualKey(vk, 0)
    win32api.PostMessage(hwnd, win32con.WM_KEYDOWN, vk, (scan << 16) | 1)
    time.sleep(delay)
    win32api.PostMessage(hwnd, win32con.WM_KEYUP,   vk, (scan << 16) | 0xC0000001)
    time.sleep(0.05)

# Movement keys for anti-AFK
MOVEMENT_KEYS = [ord('W'), ord('A'), ord('S'), ord('D')]

# Map of named keys to VK codes for user input parsing
_KEY_NAME_TO_VK = {
    "space": win32con.VK_SPACE,
    "enter": win32con.VK_RETURN,
    "return": win32con.VK_RETURN,
    "tab": win32con.VK_TAB,
    "esc": win32con.VK_ESCAPE,
    "escape": win32con.VK_ESCAPE,
    "backspace": win32con.VK_BACK,
    ",": 0xBC,
    ".": 0xBE,
    "/": 0xBF,
    ";": 0xBA,
    "'": 0xDE,
    "`": 0xC0,
    "-": 0xBD,
    "=": 0xBB,
    "[": 0xDB,
    "]": 0xDD,
    "\\": 0xDC,
}

def parse_safe_afk_keys(text):
    """Parse a space-separated string of key names into a list of VK codes."""
    vk_list = []
    for token in text.split():
        token_lower = token.lower()
        if token_lower in _KEY_NAME_TO_VK:
            vk_list.append(_KEY_NAME_TO_VK[token_lower])
        elif len(token) == 1 and token.upper().isalpha():
            vk_list.append(ord(token.upper()))
        elif len(token) == 1 and token.isdigit():
            vk_list.append(ord(token))
    return vk_list

DEFAULT_SAFE_AFK_KEYS_TEXT = "Space B H , `"

_last_safe_afk_key = None

def send_random_movement(hwnd):
    if not MOVEMENT_KEYS: return
    num_keys_to_press = random.randint(3, 5)
    pressed_keys_str = []
    for _ in range(num_keys_to_press):
        key_to_press = random.choice(MOVEMENT_KEYS)
        _send_vk(hwnd, key_to_press, delay=0.05)
        pressed_keys_str.append(chr(key_to_press))
    print(f"[ANTI-AFK] Sent random movement sequence: {''.join(pressed_keys_str)}")

def send_random_keypress(hwnd, safe_keys):
    global _last_safe_afk_key
    if not safe_keys:
        print("[ANTI-AFK] No safe keys configured, skipping keypress.")
        return
    pool = [k for k in safe_keys if k != _last_safe_afk_key] if len(safe_keys) > 1 else safe_keys
    key = random.choice(pool)
    _last_safe_afk_key = key
    _send_vk(hwnd, key, delay=0.05)
    rev = {v: k for k, v in _KEY_NAME_TO_VK.items()}
    label = rev.get(key) or (chr(key) if 32 <= key < 127 else hex(key))
    print(f"[ANTI-AFK] Sent safe keypress: {label.upper()}")

def _do_anti_afk(hwnd, mode, safe_keys=None):
    if mode == "disabled":
        return
    if mode == "keypress":
        send_random_keypress(hwnd, safe_keys or [])
    else:
        send_random_movement(hwnd)

def cast_heal(hwnd, heal_vk_param, cast_delay_param):
    if is_power_saver_active(hwnd):
        print("[CAST_HEAL] Power Saver Active. Waking window...")
        wake_window(hwnd)
    print(f"[CAST_HEAL] Sending heal key (VK: {heal_vk_param}).")
    _send_vk(hwnd, heal_vk_param)
    time.sleep(cast_delay_param)
    print(f"[CAST_HEAL] Heal cast animation delay finished ({cast_delay_param}s).")

def target_party(hwnd, vk_key):
    _send_vk(hwnd, vk_key)
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


def _frac_to_bbox(hwnd, band_frac):
    """Convert a fractional (L, T, R, B) band into an absolute screen bbox using
    the live window client size, so it scales with the window."""
    rect = get_window_rect(hwnd)
    if not rect:
        return None
    w = rect[2] - rect[0]
    h = rect[3] - rect[1]
    return (
        rect[0] + int(band_frac[0] * w),
        rect[1] + int(band_frac[1] * h),
        rect[0] + int(band_frac[2] * w),
        rect[1] + int(band_frac[3] * h),
    )


def detect_hp_bar_fill(hwnd, band_frac, red_min=HP_RED_MIN, red_margin=HP_RED_MARGIN,
                       save_debug=False):
    """Return the HP bar's fill % by measuring the vivid-red fill within a band
    that frames the bar.

    The band (L,T,R,B, fractions of the window client area) must frame the HP bar:
    left edge ≈ bar start, right edge ≈ bar end. Because it is fractional it scales
    with the window. The empty/drained part of this game's bar is visually identical
    to the brown background, so it is NOT auto-detected; instead the bar's full
    width is taken from the band width, and only the bright-red fill is measured:

        HP% = (rightmost red column - leftmost red column) / (band width - leftmost)

    A FILLED pixel has the red channel bright (>= red_min) and clearly dominant over
    green and blue (>= red_margin). Nothing else in the UI matches: white digits have
    R≈G≈B, the brown background and dark track have low red, the mana bar is blue.
    Returns 0-100, or None on failure.
    """
    try:
        bbox = _frac_to_bbox(hwnd, band_frac)
        if not bbox:
            return None
        img = ImageGrab.grab(bbox=bbox, all_screens=True).convert("RGB")
        arr = np.asarray(img).astype(np.int16)
        R, G, B = arr[..., 0], arr[..., 1], arr[..., 2]

        red = (R >= red_min) & ((R - G) >= red_margin) & ((R - B) >= red_margin)
        h, w = red.shape
        if w == 0 or h == 0:
            return None

        # Columns containing any vivid red. The fill is contiguous from the bar's
        # left; to its right there is no red (empty is dark, digits are white), so
        # the rightmost red column is the fill edge even with digits over the fill.
        col_has_red = red.any(axis=0)
        idx = np.flatnonzero(col_has_red)
        if idx.size == 0:
            hp = 0.0
            bar_left = fill_right = 0
        else:
            bar_left = int(idx[0])
            fill_right = int(idx[-1])
            denom = w - bar_left
            hp = 100.0 * (fill_right - bar_left + 1) / float(denom) if denom > 0 else 0.0
        hp = max(0.0, min(100.0, hp))

        if save_debug:
            vis = np.zeros((h, w, 3), dtype=np.uint8)
            vis[red] = (220, 30, 30)
            vis[:, bar_left] = (0, 255, 0)      # bar left edge (band left ≈ this)
            vis[:, fill_right] = (255, 255, 0)  # fill edge (current HP level)
            vis[:, w - 1] = (0, 255, 0)         # band right edge (≈ bar end)
            Image.fromarray(vis, "RGB").save("debug_hp_fill.png")

        return hp
    except Exception as e:
        print(f"[HP_READ] Detection failed: {e}")
        return None


_PARTY_VKS = [win32con.VK_F1, win32con.VK_F2, win32con.VK_F3, win32con.VK_F4]


def _column_runs(flags, gap, min_w):
    """Group True columns of a 1-D boolean array into (start, end) runs, splitting
    where the gap between True columns exceeds `gap`; drop runs narrower than min_w."""
    xs = np.flatnonzero(flags)
    runs = []
    if xs.size:
        s = p = int(xs[0])
        for x in xs[1:]:
            x = int(x)
            if x - p > gap:
                runs.append((s, p))
                s = x
            p = x
        runs.append((s, p))
    return [(a, b) for a, b in runs if b - a + 1 >= min_w]


def _resolve_party_geom(geom):
    """Merge a partial geometry override with the module defaults and derive the
    spacing (from width) and search x-range (to cover up to 4 centered slots)."""
    g = {
        "center": PARTY_BAR_CENTER_X_FRAC,
        "width": PARTY_BAR_WIDTH_FRAC,
        "y": PARTY_BAR_Y_FRAC,
        "halfh": PARTY_BAR_HALFH_FRAC,
    }
    if geom:
        g.update({k: v for k, v in geom.items() if v is not None})
    g["spacing"] = g.get("spacing") or g["width"] * PARTY_SPACING_WIDTH_RATIO
    span = 2.0 * g["spacing"] + g["width"]
    g["xl"] = max(0.0, g["center"] - span)
    g["xr"] = min(1.0, g["center"] + span)
    return g


def detect_party_members(hwnd, geom=None):
    """Auto-detect the party HP bars in the bottom-center row.

    Returns a list of dicts {slot, vk, hp} sorted left→right, where `slot` is the
    0-based party slot (0 = F1), `vk` its target key, and `hp` the fill percent, or
    None if the bar is dim (member far / out of heal range). No manual party size:
    each bar's left edge is snapped to the fixed centered grid to recover its slot,
    and the party size is inferred from the outermost visible bars. `geom` overrides
    the bar geometry (from calibration); defaults to the module constants.
    """
    try:
        rect = get_window_rect(hwnd)
        if not rect:
            return []
        W = rect[2] - rect[0]
        H = rect[3] - rect[1]
        g = _resolve_party_geom(geom)
        x_off = int(g["xl"] * W)
        bbox = (rect[0] + x_off,
                rect[1] + int((g["y"] - g["halfh"]) * H),
                rect[0] + int(g["xr"] * W),
                rect[1] + int((g["y"] + g["halfh"]) * H))
        img = ImageGrab.grab(bbox=bbox, all_screens=True).convert("RGB")
        arr = np.asarray(img).astype(np.int16)
        R, G, B = arr[..., 0], arr[..., 1], arr[..., 2]
        dim = (R >= PARTY_DIM_RED_MIN) & ((R - G) >= PARTY_DIM_MARGIN) & ((R - B) >= PARTY_DIM_MARGIN)
        bright = (R >= PARTY_RED_MIN) & ((R - G) >= PARTY_RED_MARGIN) & ((R - B) >= PARTY_RED_MARGIN)
        teal = (B >= PARTY_TEAL_B_MIN) & ((B - R) >= PARTY_TEAL_MARGIN) & ((G - R) >= PARTY_TEAL_MARGIN)
        teal_col = teal.any(axis=0)

        barw_px = max(1, int(g["width"] * W))
        runs = _column_runs(dim.any(axis=0), gap=max(6, int(0.013 * W)),
                             min_w=max(3, int(0.004 * W)))
        # Keep only runs that have a teal MP bar under them — a real party member is
        # red HP over teal MP; stray game-world red has no teal and is discarded.
        runs = [(a, b) for a, b in runs if teal_col[a:a + barw_px].any()]
        if not runs:
            return []

        half_spacing = (g["spacing"] / 2.0) or 1e-6
        entries = []  # (a, b, j)
        for a, b in runs:
            bar_left = x_off + a  # window x of the bar's left edge
            slot_center_frac = (bar_left + barw_px / 2.0) / W
            j = round((slot_center_frac - g["center"]) / half_spacing)
            entries.append((a, b, j))

        n = max(abs(j) for _, _, j in entries) + 1  # inferred party size
        members = []
        for a, b, j in entries:
            slot = (j + (n - 1)) // 2
            if slot < 0 or slot >= len(_PARTY_VKS):
                continue  # only F1-F4 supported
            near = int(bright[:, a:a + barw_px].sum()) >= PARTY_BAR_MIN_RED_PX
            hp = max(0.0, min(100.0, 100.0 * min(b - a + 1, barw_px) / barw_px)) if near else None
            members.append({"slot": slot, "vk": _PARTY_VKS[slot], "hp": hp})
        members.sort(key=lambda m: m["slot"])
        return members
    except Exception as e:
        print(f"[PARTY] Detection failed: {e}")
        return []


def diagnose_hp_band(hwnd, band_frac):
    """Save the raw search-band crop and print a left→right HSV profile, so the
    fill/empty colors can be tuned against the real UI instead of guessed."""
    bbox = _frac_to_bbox(hwnd, band_frac)
    if not bbox:
        print("[DIAG] Could not resolve window rect.")
        return
    img = ImageGrab.grab(bbox=bbox, all_screens=True).convert("RGB")
    img.save("debug_hp_raw.png")
    hsv = np.asarray(img.convert("HSV"))
    H, S, V = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    h, w = H.shape
    if h == 0 or w == 0:
        print("[DIAG] Empty crop.")
        return
    # Profile the vertical middle half, where a bottom-anchored bar usually sits.
    r0, r1 = h // 4, max(h // 4 + 1, 3 * h // 4)
    buckets = min(20, w)
    print(f"[DIAG] band {w}x{h}px; profiling rows {r0}-{r1}, left→right (avg H/S/V, 0-255):")
    for b in range(buckets):
        c0 = b * w // buckets
        c1 = max(c0 + 1, (b + 1) * w // buckets)
        hh = int(H[r0:r1, c0:c1].mean())
        ss = int(S[r0:r1, c0:c1].mean())
        vv = int(V[r0:r1, c0:c1].mean())
        print(f"   {int(100*c0/w):3d}%  H={hh:3d} S={ss:3d} V={vv:3d}")
    print("[DIAG] Saved raw crop to debug_hp_raw.png")


def heal_self(hwnd, heal_vk_param, cast_delay_param):
    print("[HEAL] → SELF (priority)")
    cast_heal(hwnd, heal_vk_param, cast_delay_param)

def heal_party(hwnd, vk_key, heal_vk_param, cast_delay_param):
    print(f"[HEAL_PARTY] Targeting Party (VK: {vk_key})")
    _send_vk(hwnd, vk_key)
    time.sleep(0.2)
    print(f"[HEAL_PARTY] Casting heal (VK: {heal_vk_param}).")
    cast_heal(hwnd, heal_vk_param, cast_delay_param)

def interruptible_sleep(seconds):
    end = time.time() + seconds
    while time.time() < end:
        if stop_event.is_set():
            return False
        time.sleep(0.05)
    return True

def healer_loop(
    heal_cooldown_param,
    cast_delay_param,
    heal_vk_param,
    game_window_title_param,
    anti_afk_mode_param="movement",
    safe_afk_keys_param=None,
    buff_enabled_param=False,
    buff_keys_param=None,
    buff_interval_param=300,
    reactive_enabled_param=False,
    hp_band_frac_param=None,
    hp_red_min_param=HP_RED_MIN,
    hp_red_margin_param=HP_RED_MARGIN,
    self_heal_threshold_param=SELF_HEAL_THRESHOLD,
    self_panic_threshold_param=SELF_PANIC_THRESHOLD,
    party_reactive_enabled_param=False,
    party_heal_slots_param=None,     # set of slot indices to heal (0=F1..3=F4); None/empty = all detected
    party_heal_threshold_param=PARTY_HEAL_THRESHOLD,
    party_panic_threshold_param=PARTY_PANIC_THRESHOLD,
    party_geom_param=None,           # calibrated party bar geometry override
):
    global running
    previous_target_vk = None
    target_missing_cycles = 0  # consecutive cycles our party target wasn't seen near
    last_buff_press_time = time.time()
    last_self_heal_time = 0.0

    print(f"DEBUG: Game window title used in healer_loop: '{game_window_title_param}' (Length: {len(game_window_title_param)})")
    hwnd = find_game_window(game_window_title_param)
    if not hwnd:
        running = False
        print("[HEALER_LOOP] Game window not found at start. Stopping.")
        return

    print(f"        Buffs: {'ON' if buff_enabled_param else 'OFF'}  Keys: {buff_keys_param}  Interval: {buff_interval_param}s")
    if reactive_enabled_param:
        print(f"        Reactive Self-Heal: ON  Heal<={self_heal_threshold_param:.0f}%  Panic<={self_panic_threshold_param:.0f}%")
    else:
        print("        Reactive Self-Heal: OFF")
    if party_reactive_enabled_param:
        slots = sorted((party_heal_slots_param or set()))
        who = "F" + str([s + 1 for s in slots]) if slots else "all detected"
        print(f"        Reactive Party Heal: ON (auto-detect size)  Healing {who}  Heal<={party_heal_threshold_param:.0f}%  Panic<={party_panic_threshold_param:.0f}%")
    else:
        print("        Reactive Party Heal: OFF")
    print("        Press F8 to stop.\n")

    while not stop_event.is_set():
        hwnd = find_game_window(game_window_title_param)
        if not hwnd:
            print("[HEALER_LOOP] Game window lost during loop, attempting to re-find...")
            if not interruptible_sleep(3): break
            continue

        # Timed buff hotbar key presses
        if buff_enabled_param and buff_keys_param and time.time() - last_buff_press_time > buff_interval_param:
            print("[HEALER_LOOP] Sending buff hotbar keys.")
            for buff_key in buff_keys_param:
                _send_vk(hwnd, buff_key)
                time.sleep(0.1)
            print("[HEALER_LOOP] Buff hotbar keys sent.")
            last_buff_press_time = time.time()

        # ── Reactive self-heal: highest priority ──────────────────────────────
        # Read HP once per cycle: auto-detected party members (dim/far → skipped)
        # and self. Party size/positions are detected from the pixels, so nothing
        # needs to be set when members join or leave.
        party_readings = []  # list of (hp, slot, vk)
        if party_reactive_enabled_param:
            for m in detect_party_members(hwnd, party_geom_param):
                if m["hp"] is None:
                    continue  # far / out of range
                if party_heal_slots_param and m["slot"] not in party_heal_slots_param:
                    continue  # not a slot we were asked to heal
                party_readings.append((m["hp"], m["slot"], m["vk"]))

        # Forget a party target that is no longer near: when a member moves out of
        # range the game drops the target, but our "already selected" tracking would
        # stay stuck on it and self-cast on return. Clearing it forces a re-select
        # (press the key again) next time we heal that member. Debounced by a couple
        # of cycles so a one-frame detection flicker doesn't trigger a needless
        # re-press (which would toggle a still-valid target off).
        if previous_target_vk is not None:
            if previous_target_vk in {r[2] for r in party_readings}:
                target_missing_cycles = 0
            else:
                target_missing_cycles += 1
                if target_missing_cycles >= 2:
                    previous_target_vk = None
                    target_missing_cycles = 0

        self_hp = None
        if reactive_enabled_param and hp_band_frac_param:
            self_hp = detect_hp_bar_fill(hwnd, hp_band_frac_param, hp_red_min_param, hp_red_margin_param)

        now = time.time()

        def _self_heal(tag):
            # Ensure the heal lands on self, not a lingering party target: pressing
            # the party key again toggles (deselects) it, so the heal self-casts.
            if previous_target_vk is not None:
                print(f"[REACTIVE] Deselecting party target (VK {previous_target_vk}) before self-heal.")
                _send_vk(hwnd, previous_target_vk)
                time.sleep(0.1)
            print(f"[REACTIVE] {tag}: self HP ~{self_hp:.0f}%. Healing self.")
            heal_self(hwnd, heal_vk_param, cast_delay_param)

        # Priority 1 — SELF PANIC: keep the healer alive first. Ignores cooldown.
        if self_hp is not None and self_hp <= self_panic_threshold_param:
            _self_heal("PANIC")
            previous_target_vk = None
            last_self_heal_time = now
            continue

        # Priority 2 — PARTY PANIC: a critically low member, healed immediately
        # (ignores cooldown), ahead of normal self and party heals.
        if party_readings:
            critical = [r for r in party_readings if r[0] <= party_panic_threshold_param]
            if critical:
                hp_val, i, vk = min(critical)
                print(f"[PARTY] PANIC F{i + 1} (VK {vk}) at ~{hp_val:.0f}% "
                      f"(<= {party_panic_threshold_param:.0f}%). Emergency heal.")
                # Target keys TOGGLE in-game: pressing F# when it is already the
                # target cancels it (then the heal self-casts). So only press when
                # switching to a new target; otherwise it is already selected.
                if vk != previous_target_vk:
                    _send_vk(hwnd, vk)
                    time.sleep(0.2)
                previous_target_vk = vk
                cast_heal(hwnd, heal_vk_param, cast_delay_param)
                continue  # re-check immediately, ignore cooldown

        # Priority 3 — SELF normal heal (respects cooldown).
        if (self_hp is not None and self_hp <= self_heal_threshold_param
                and now - last_self_heal_time >= heal_cooldown_param):
            _self_heal("self-heal")
            previous_target_vk = None
            last_self_heal_time = now
            continue

        # Priority 4 — normal party heal: lowest member at/below the heal threshold.
        if party_reactive_enabled_param:
            below = [r for r in party_readings if r[0] <= party_heal_threshold_param]
            if below:
                hp_val, i, vk = min(below)
                print(f"[PARTY] Lowest F{i + 1} (VK {vk}) at ~{hp_val:.0f}% "
                      f"(<= {party_heal_threshold_param:.0f}%). Targeting and healing.")
                # Target keys TOGGLE in-game: pressing F# when it is already the
                # target cancels it (then the heal self-casts). So only press when
                # switching to a new target; otherwise it is already selected.
                if vk != previous_target_vk:
                    _send_vk(hwnd, vk)
                    time.sleep(0.2)
                previous_target_vk = vk
                cast_heal(hwnd, heal_vk_param, cast_delay_param)
                cooldown_end = time.time() + heal_cooldown_param
                while time.time() < cooldown_end:
                    if stop_event.is_set():
                        break
                    _do_anti_afk(hwnd, anti_afk_mode_param, safe_afk_keys_param)
                    time.sleep(0.5)
            else:
                _do_anti_afk(hwnd, anti_afk_mode_param, safe_afk_keys_param)
                time.sleep(0.5)
        else:
            _do_anti_afk(hwnd, anti_afk_mode_param, safe_afk_keys_param)
            time.sleep(0.5)

    running = False
    print("[STOP] Healer stopped.")

# ══════════════════════════════════════════════════════════════════════════════
#  GUI
# ══════════════════════════════════════════════════════════════════════════════

class MacroGUI:
    def __init__(self, master):
        self.master = master
        master.title("Revolt - Night Crows Cleric BOT")

        self.running_thread = None

        # Variables for GUI elements
        self.game_window_title_var = tk.StringVar() # New variable for selected game window
        self.f_keys_vars = {
            "F1": tk.BooleanVar(value=False),
            "F2": tk.BooleanVar(value=False),
            "F3": tk.BooleanVar(value=False),
            "F4": tk.BooleanVar(value=False)
        }
        self.party_reactive_enabled_var = tk.BooleanVar(value=False) # Reactive (HP-based) party healing
        self.party_heal_threshold_var = tk.StringVar(value=str(PARTY_HEAL_THRESHOLD))
        self.party_panic_threshold_var = tk.StringVar(value=str(PARTY_PANIC_THRESHOLD))
        # Party bar geometry (fractions of the window) — set via the calibrator.
        self.party_center_x_var = tk.StringVar(value=str(PARTY_BAR_CENTER_X_FRAC))
        self.party_bar_width_var = tk.StringVar(value=str(PARTY_BAR_WIDTH_FRAC))
        self.party_bar_y_var = tk.StringVar(value=str(PARTY_BAR_Y_FRAC))
        self.party_bar_halfh_var = tk.StringVar(value=str(PARTY_BAR_HALFH_FRAC))
        self.cast_delay_var = tk.StringVar(value=str(CAST_DELAY))
        self.heal_key_var = tk.StringVar(value=chr(HEAL_VK))
        
        self.heal_cooldown_var = tk.StringVar(value=str(HEAL_COOLDOWN))
        self.anti_afk_mode_var = tk.StringVar(value="movement")
        self.safe_afk_keys_var = tk.StringVar(value=DEFAULT_SAFE_AFK_KEYS_TEXT)
        self.buff_enabled_var = tk.BooleanVar(value=False)
        self.buff_keys_var = tk.StringVar(value="5 6 7 8")
        self.buff_interval_var = tk.StringVar(value="5")

        # Reactive (HP-based) self-heal settings
        self.reactive_enabled_var = tk.BooleanVar(value=False)
        self.self_heal_threshold_var = tk.StringVar(value=str(SELF_HEAL_THRESHOLD))
        self.self_panic_threshold_var = tk.StringVar(value=str(SELF_PANIC_THRESHOLD))
        self.hp_band_l_var = tk.StringVar(value=str(HP_SEARCH_BAND_FRAC[0]))
        self.hp_band_t_var = tk.StringVar(value=str(HP_SEARCH_BAND_FRAC[1]))
        self.hp_band_r_var = tk.StringVar(value=str(HP_SEARCH_BAND_FRAC[2]))
        self.hp_band_b_var = tk.StringVar(value=str(HP_SEARCH_BAND_FRAC[3]))
        self.hp_red_min_var = tk.StringVar(value=str(HP_RED_MIN))
        self.hp_red_margin_var = tk.StringVar(value=str(HP_RED_MARGIN))

        # Create GUI elements
        self.create_widgets()
        self.load_config()  # Restore saved settings before populating the dropdown
        self._on_afk_mode_change()  # Sync widget enable-state to loaded settings
        self.populate_game_windows() # Populate dropdown on startup
        self.poll_log_queue() # Start draining console output into the log panel
        # Persist settings automatically when the window is closed.
        master.protocol("WM_DELETE_WINDOW", self.on_close)

    def create_widgets(self):
        # Frame for Script Control
        script_frame = ttk.LabelFrame(self.master, text="Script Control")
        script_frame.pack(padx=10, pady=5, fill="x")

        self.start_button = ttk.Button(script_frame, text="Start Script", command=self.start_script)
        self.start_button.pack(side="left", padx=5, pady=5)

        self.stop_button = ttk.Button(script_frame, text="Stop Script", command=self.stop_script, state="disabled")
        self.stop_button.pack(side="left", padx=5, pady=5)

        ttk.Button(script_frame, text="Save Settings", command=self.save_config).pack(
            side="right", padx=5, pady=5)

        # Frame for Game Window Selection
        game_window_frame = ttk.LabelFrame(self.master, text="Game Window Selection")
        game_window_frame.pack(padx=10, pady=5, fill="x")

        ttk.Label(game_window_frame, text="Select Game Window:").pack(side="left", padx=5, pady=2)
        self.game_window_dropdown = ttk.Combobox(game_window_frame, textvariable=self.game_window_title_var, state="readonly")
        self.game_window_dropdown.pack(side="left", padx=5, pady=2, fill="x", expand=True)
        self.refresh_windows_button = ttk.Button(game_window_frame, text="Refresh", command=self.populate_game_windows)
        self.refresh_windows_button.pack(side="left", padx=5, pady=2)
        self.capture_button = ttk.Button(game_window_frame, text="Capture", command=self.capture_screenshot)
        self.capture_button.pack(side="left", padx=5, pady=2)
        self.calibrate_button = ttk.Button(game_window_frame, text="Calibrate", command=self.open_calibrator)
        self.calibrate_button.pack(side="left", padx=5, pady=2)

        # Frame for Party Heal Settings
        party_frame = ttk.LabelFrame(self.master, text="Party Heal Settings")
        party_frame.pack(padx=10, pady=5, fill="x")

        f_keys_frame = ttk.Frame(party_frame)
        f_keys_frame.pack(anchor="w", padx=5, pady=2)
        ttk.Label(f_keys_frame, text="Slots to heal (leave all unchecked = heal everyone):").pack(side="left")
        for text, var in self.f_keys_vars.items():
            ttk.Checkbutton(f_keys_frame, text=text, variable=var).pack(side="left", padx=2)

        # Reactive party heal: auto-detects the party bars (size + positions) each
        # cycle and heals the lowest below threshold.
        reactive_party_frame = ttk.Frame(party_frame)
        reactive_party_frame.pack(anchor="w", padx=5, pady=2)
        ttk.Checkbutton(
            reactive_party_frame,
            text="Reactive Party Heal (auto-detects party size)",
            variable=self.party_reactive_enabled_var
        ).pack(side="left")

        reactive_party_frame2 = ttk.Frame(party_frame)
        reactive_party_frame2.pack(anchor="w", padx=5, pady=2)
        ttk.Label(reactive_party_frame2, text="Party heal below (%):").pack(side="left", padx=(0, 2))
        ttk.Entry(reactive_party_frame2, textvariable=self.party_heal_threshold_var, width=6).pack(side="left")
        ttk.Label(reactive_party_frame2, text="Party panic below (%):").pack(side="left", padx=(10, 2))
        ttk.Entry(reactive_party_frame2, textvariable=self.party_panic_threshold_var, width=6).pack(side="left")
        ttk.Button(reactive_party_frame2, text="Test Party Read", command=self.test_party_read).pack(side="left", padx=(10, 0))

        # Frame for Buff Settings
        buff_frame = ttk.LabelFrame(self.master, text="Buff Settings")
        buff_frame.pack(padx=10, pady=5, fill="x")

        ttk.Checkbutton(buff_frame, text="Enable Buffs", variable=self.buff_enabled_var).grid(
            row=0, column=0, columnspan=3, sticky="w", padx=5, pady=2)

        ttk.Label(buff_frame, text="Buff Keys:").grid(row=1, column=0, sticky="w", padx=5, pady=2)
        ttk.Entry(buff_frame, textvariable=self.buff_keys_var, width=20).grid(row=1, column=1, sticky="ew", padx=5, pady=2)
        ttk.Label(buff_frame, text="(space-separated, e.g. 5 6 7 8)").grid(row=1, column=2, sticky="w", padx=2, pady=2)

        ttk.Label(buff_frame, text="Interval (min):").grid(row=2, column=0, sticky="w", padx=5, pady=2)
        ttk.Entry(buff_frame, textvariable=self.buff_interval_var, width=10).grid(row=2, column=1, sticky="ew", padx=5, pady=2)

        # Frame for General Settings
        general_frame = ttk.LabelFrame(self.master, text="General Settings")
        general_frame.pack(padx=10, pady=5, fill="x")

        

        ttk.Label(general_frame, text="Heal Cooldown (s):").grid(row=1, column=0, sticky="w", padx=5, pady=2)
        ttk.Entry(general_frame, textvariable=self.heal_cooldown_var, width=10).grid(row=1, column=1, sticky="ew", padx=5, pady=2)

        ttk.Label(general_frame, text="Cast Delay (s):").grid(row=2, column=0, sticky="w", padx=5, pady=2)
        ttk.Entry(general_frame, textvariable=self.cast_delay_var, width=10).grid(row=2, column=1, sticky="ew", padx=5, pady=2)

        ttk.Label(general_frame, text="Heal Hotbar Key:").grid(row=3, column=0, sticky="w", padx=5, pady=2)
        ttk.Entry(general_frame, textvariable=self.heal_key_var, width=10).grid(row=3, column=1, sticky="ew", padx=5, pady=2)

        ttk.Label(general_frame, text="Anti-AFK Method:").grid(row=4, column=0, sticky="w", padx=5, pady=2)
        afk_frame = ttk.Frame(general_frame)
        afk_frame.grid(row=4, column=1, columnspan=2, sticky="w", padx=5, pady=2)
        ttk.Radiobutton(afk_frame, text="Movement (WASD)", variable=self.anti_afk_mode_var, value="movement",
                        command=self._on_afk_mode_change).pack(side="left")
        ttk.Radiobutton(afk_frame, text="Safe Keys:", variable=self.anti_afk_mode_var, value="keypress",
                        command=self._on_afk_mode_change).pack(side="left", padx=(8, 2))
        self.safe_keys_entry = ttk.Entry(afk_frame, textvariable=self.safe_afk_keys_var, width=22, state="disabled")
        self.safe_keys_entry.pack(side="left")
        ttk.Label(afk_frame, text="(space-separated, e.g. Space B H , `)").pack(side="left", padx=(4, 0))
        ttk.Radiobutton(afk_frame, text="Disabled", variable=self.anti_afk_mode_var, value="disabled",
                        command=self._on_afk_mode_change).pack(side="left", padx=(8, 0))

        # Frame for Reactive (HP-based) Self-Heal
        reactive_frame = ttk.LabelFrame(self.master, text="Reactive Self-Heal (reads own HP bar)")
        reactive_frame.pack(padx=10, pady=5, fill="x")

        ttk.Checkbutton(reactive_frame, text="Enable Reactive Self-Heal",
                        variable=self.reactive_enabled_var).grid(
            row=0, column=0, columnspan=4, sticky="w", padx=5, pady=2)

        ttk.Label(reactive_frame, text="Self heal below (%):").grid(row=1, column=0, sticky="w", padx=5, pady=2)
        ttk.Entry(reactive_frame, textvariable=self.self_heal_threshold_var, width=6).grid(row=1, column=1, sticky="w", padx=5, pady=2)
        ttk.Label(reactive_frame, text="Self panic below (%):").grid(row=1, column=2, sticky="w", padx=5, pady=2)
        ttk.Entry(reactive_frame, textvariable=self.self_panic_threshold_var, width=6).grid(row=1, column=3, sticky="w", padx=5, pady=2)

        ttk.Label(reactive_frame, text="Search band (L T R B, 0-1):").grid(row=2, column=0, sticky="w", padx=5, pady=2)
        region_frame = ttk.Frame(reactive_frame)
        region_frame.grid(row=2, column=1, columnspan=3, sticky="w", padx=5, pady=2)
        for var in (self.hp_band_l_var, self.hp_band_t_var, self.hp_band_r_var, self.hp_band_b_var):
            ttk.Entry(region_frame, textvariable=var, width=6).pack(side="left", padx=2)

        ttk.Label(reactive_frame, text="Red min / Red margin (0-255):").grid(row=3, column=0, sticky="w", padx=5, pady=2)
        color_frame = ttk.Frame(reactive_frame)
        color_frame.grid(row=3, column=1, columnspan=2, sticky="w", padx=5, pady=2)
        ttk.Entry(color_frame, textvariable=self.hp_red_min_var, width=6).pack(side="left", padx=2)
        ttk.Entry(color_frame, textvariable=self.hp_red_margin_var, width=6).pack(side="left", padx=2)
        ttk.Button(reactive_frame, text="Test HP Read", command=self.test_hp_read).grid(
            row=3, column=3, sticky="e", padx=5, pady=2)

        # Frame for the in-app debug log (replaces the separate terminal window)
        log_frame = ttk.LabelFrame(self.master, text="Debug Log")
        log_frame.pack(padx=10, pady=5, fill="both", expand=True)

        self.log_text = ScrolledText(log_frame, height=12, wrap="word", state="disabled")
        self.log_text.pack(side="top", fill="both", expand=True, padx=5, pady=(5, 2))

        ttk.Button(log_frame, text="Clear Log", command=self.clear_log).pack(
            side="right", padx=5, pady=(0, 5))

    def append_log(self, text):
        self.log_text.config(state="normal")
        self.log_text.insert("end", text)
        self.log_text.see("end")
        self.log_text.config(state="disabled")

    def clear_log(self):
        self.log_text.config(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.config(state="disabled")

    def capture_screenshot(self):
        """Save a full screenshot of the selected game window's client area, for
        analysis/calibration (e.g. locating party HP bars)."""
        title = self.game_window_title_var.get()
        if not title:
            messagebox.showerror("Error", "Please select a game window first.")
            return
        hwnd = find_game_window(title)
        if not hwnd:
            messagebox.showerror("Error", "Game window not found.")
            return
        rect = get_window_rect(hwnd)
        if not rect:
            messagebox.showerror("Error", "Could not get window bounds.")
            return
        try:
            img = ImageGrab.grab(bbox=rect, all_screens=True)
            fname = time.strftime("game_capture_%Y%m%d_%H%M%S.png")
            path = os.path.join(_CONFIG_DIR, fname)
            img.save(path)
            print(f"[CAPTURE] Saved {img.width}x{img.height} screenshot to {path}")
        except Exception as e:
            print(f"[CAPTURE] Failed: {e}")

    def _show_image_popup(self, image, title, note=""):
        """Show a PIL image (or path) in a preview window, scaled to fit; tiny
        images are upscaled so they're readable."""
        try:
            if isinstance(image, str):
                image = Image.open(image).convert("RGB")
            w, h = image.size
            scale = min(1100.0 / w, 720.0 / h)
            if w * scale < 480:  # upscale tiny crops (e.g. the HP-fill mask)
                scale = 480.0 / w
            disp = image.resize((max(1, int(w * scale)), max(1, int(h * scale))),
                                 Image.NEAREST if scale > 1 else Image.LANCZOS)
            top = tk.Toplevel(self.master)
            top.title(title)
            if note:
                ttk.Label(top, text=note, wraplength=disp.width).pack(padx=8, pady=(8, 0))
            top._preview_photo = ImageTk.PhotoImage(disp)  # keep ref alive on the window
            ttk.Label(top, image=top._preview_photo).pack(padx=8, pady=8)
            ttk.Button(top, text="Close", command=top.destroy).pack(pady=(0, 8))
        except Exception as e:
            print(f"[PREVIEW] Could not show image: {e}")

    def _party_geom(self):
        """Build the party bar geometry override from the GUI fields, or None."""
        try:
            return {
                "center": float(self.party_center_x_var.get()),
                "width": float(self.party_bar_width_var.get()),
                "y": float(self.party_bar_y_var.get()),
                "halfh": float(self.party_bar_halfh_var.get()),
            }
        except ValueError:
            return None

    def open_calibrator(self):
        """Show a screenshot of the game window and let the user drag a box over
        their own HP bar and one party member's HP bar to set the fractions."""
        title = self.game_window_title_var.get()
        if not title:
            messagebox.showerror("Error", "Please select a game window first.")
            return
        hwnd = find_game_window(title)
        if not hwnd:
            messagebox.showerror("Error", "Game window not found.")
            return
        rect = get_window_rect(hwnd)
        if not rect:
            messagebox.showerror("Error", "Could not get window bounds.")
            return
        img = ImageGrab.grab(bbox=rect, all_screens=True).convert("RGB")
        win_w, win_h = img.size
        scale = min(1.0, 1100.0 / win_w, 750.0 / win_h)
        disp = img.resize((max(1, int(win_w * scale)), max(1, int(win_h * scale))))

        top = tk.Toplevel(self.master)
        top.title("Calibrate — drag a box over the bar")
        mode = tk.StringVar(value="self")
        bar = ttk.Frame(top)
        bar.pack(fill="x", padx=6, pady=4)
        ttk.Label(bar, text="Marking:").pack(side="left")
        ttk.Radiobutton(bar, text="My HP bar", variable=mode, value="self").pack(side="left", padx=4)
        ttk.Radiobutton(bar, text="A party member's HP bar", variable=mode, value="party").pack(side="left", padx=4)
        status = ttk.Label(bar, text="Drag a box around the whole bar (left edge → right edge).")
        status.pack(side="left", padx=10)

        self._cal_photo = ImageTk.PhotoImage(disp)  # keep a reference
        canvas = tk.Canvas(top, width=disp.width, height=disp.height, cursor="cross")
        canvas.pack(padx=6, pady=6)
        canvas.create_image(0, 0, anchor="nw", image=self._cal_photo)
        st = {"x0": 0, "y0": 0, "rect": None}

        def on_down(e):
            st["x0"], st["y0"] = e.x, e.y
            if st["rect"]:
                canvas.delete(st["rect"])
            st["rect"] = canvas.create_rectangle(e.x, e.y, e.x, e.y, outline="#00ff00", width=2)

        def on_drag(e):
            if st["rect"]:
                canvas.coords(st["rect"], st["x0"], st["y0"], e.x, e.y)

        def on_up(e):
            x0, x1 = sorted((st["x0"], e.x))
            y0, y1 = sorted((st["y0"], e.y))
            if x1 - x0 < 3 or y1 - y0 < 3:
                return
            # display px -> window fractions
            fl, fr = x0 / scale / win_w, x1 / scale / win_w
            ft, fb = y0 / scale / win_h, y1 / scale / win_h
            if mode.get() == "self":
                self.hp_band_l_var.set(round(fl, 4))
                self.hp_band_t_var.set(round(ft, 4))
                self.hp_band_r_var.set(round(fr, 4))
                self.hp_band_b_var.set(round(fb, 4))
                status.config(text=f"Self HP band set: L{fl:.3f} T{ft:.3f} R{fr:.3f} B{fb:.3f}")
                print(f"[CALIBRATE] Self HP band set to ({fl:.4f}, {ft:.4f}, {fr:.4f}, {fb:.4f}).")
            else:
                # Only the bar's WIDTH and vertical position matter — every member's
                # bar is identical in size/height, and the row is centered — so it
                # does not matter which member you mark or how many you have. The
                # horizontal center is left at its (screen-centered) default.
                self.party_bar_width_var.set(round(fr - fl, 4))
                self.party_bar_y_var.set(round((ft + fb) / 2, 4))
                self.party_bar_halfh_var.set(round(max((fb - ft) / 2, 0.004), 4))
                status.config(text=f"Party bar set: width {fr - fl:.3f}, y {(ft + fb) / 2:.3f} "
                                   f"(mark any member — count/position handled automatically).")
                print(f"[CALIBRATE] Party bar: width={fr - fl:.4f} y={(ft + fb) / 2:.4f} "
                      f"(center kept at default; any member/size is fine).")

        canvas.bind("<Button-1>", on_down)
        canvas.bind("<B1-Motion>", on_drag)
        canvas.bind("<ButtonRelease-1>", on_up)

        btns = ttk.Frame(top)
        btns.pack(fill="x", padx=6, pady=(0, 6))
        ttk.Button(btns, text="Save & Close", command=lambda: (self.save_config(), top.destroy())).pack(side="right")

    def test_party_read(self):
        """Auto-detect party members once and save an overlay of the detected
        bars (slot + HP), for calibration/diagnosis."""
        title = self.game_window_title_var.get()
        if not title:
            messagebox.showerror("Error", "Please select a game window first.")
            return
        hwnd = find_game_window(title)
        if not hwnd:
            messagebox.showerror("Error", "Game window not found.")
            return
        geom = self._party_geom()
        members = detect_party_members(hwnd, geom)
        if not members:
            print("[PARTY TEST] No party bars detected in the bottom-center row.")
        for m in members:
            label = "far/skip" if m["hp"] is None else f"{m['hp']:.0f}%"
            print(f"[PARTY TEST] F{m['slot'] + 1}: {label}")
        # Overlay: mark each detected bar's slot band on a full-window capture.
        rect = get_window_rect(hwnd)
        if not rect:
            return
        g = _resolve_party_geom(geom)
        img = ImageGrab.grab(bbox=rect, all_screens=True).convert("RGB")
        W, H = img.size
        draw = ImageDraw.Draw(img)
        half_w = g["width"] / 2.0
        for m in members:
            n = max((mm["slot"] for mm in members), default=0) + 1
            cx = g["center"] + (m["slot"] - (n - 1) / 2.0) * g["spacing"]
            box = (int((cx - half_w) * W), int((g["y"] - g["halfh"]) * H),
                   int((cx + half_w) * W), int((g["y"] + g["halfh"]) * H))
            color = (0, 255, 0) if m["hp"] is not None else (255, 255, 0)
            draw.rectangle(box, outline=color, width=2)
            draw.text((box[0], max(0, box[1] - 12)), f"F{m['slot'] + 1}", fill=color)
        img.save("debug_party.png")
        print("[PARTY TEST] Saved overlay to debug_party.png "
              "(green=near/healable, yellow=far).")
        # Preview: crop to the party row (with margin) so the boxes are visible.
        cx0 = int(max(0.0, g["xl"] - 0.03) * W)
        cx1 = int(min(1.0, g["xr"] + 0.03) * W)
        cy0 = int(max(0.0, g["y"] - 0.06) * H)
        cy1 = int(min(1.0, g["y"] + 0.06) * H)
        crop = img.crop((cx0, cy0, cx1, cy1))
        if not members:
            summ = "No party bars detected."
        else:
            summ = "  ".join(f"F{m['slot']+1}=" + ("far" if m["hp"] is None else f"{m['hp']:.0f}%")
                             for m in members)
        self._show_image_popup(crop, "Test Party Read",
                               note=f"{summ}\ngreen = near/healable,  yellow = far")

    def _get_search_band(self):
        """Parse the four search-band entries into an (L, T, R, B) fraction tuple.
        Returns None if not four numbers in 0-1 with left<right and top<bottom."""
        try:
            band = (
                float(self.hp_band_l_var.get()),
                float(self.hp_band_t_var.get()),
                float(self.hp_band_r_var.get()),
                float(self.hp_band_b_var.get()),
            )
        except ValueError:
            return None
        if not all(0.0 <= f <= 1.0 for f in band):
            return None
        if band[0] >= band[2] or band[1] >= band[3]:
            return None
        return band

    def _get_color_params(self):
        """Parse the (red_min, red_margin) entries into ints in 0-255, or None."""
        try:
            red_min = int(self.hp_red_min_var.get())
            red_margin = int(self.hp_red_margin_var.get())
        except ValueError:
            return None
        if not (0 <= red_min <= 255 and 0 <= red_margin <= 255):
            return None
        return red_min, red_margin

    def test_hp_read(self):
        """Read the HP bar once with the current settings and report the result.
        Saves debug_hp_fill.png so the region/color can be calibrated."""
        title = self.game_window_title_var.get()
        if not title:
            messagebox.showerror("Error", "Please select a game window first.")
            return
        hwnd = find_game_window(title)
        if not hwnd:
            messagebox.showerror("Error", "Game window not found.")
            return
        band = self._get_search_band()
        if not band:
            messagebox.showerror("Error", "Search band must be four numbers in 0-1 (L<R, T<B).")
            return
        color = self._get_color_params()
        if not color:
            messagebox.showerror("Error", "Red min / Dark max must be integers (0-255).")
            return
        hp = detect_hp_bar_fill(hwnd, band, color[0], color[1], save_debug=True)
        legend = "red = detected fill,  green = band ends,  yellow = HP level"
        summary = "No HP bar found — check band/colors." if hp is None else f"Self HP ~ {hp:.1f}%"
        print(f"[TEST] {summary}  ({legend})")
        # Always capture raw pixels + color profile to guide calibration.
        diagnose_hp_band(hwnd, band)
        self._show_image_popup("debug_hp_fill.png", "Test HP Read", note=f"{summary}\n{legend}")

    def poll_log_queue(self):
        """Drain queued console output into the log panel. Runs on the Tk main
        thread so it is safe to update the widget even though logs originate from
        the background healer thread."""
        try:
            while True:
                self.append_log(log_queue.get_nowait())
        except queue.Empty:
            pass
        self.master.after(100, self.poll_log_queue)

    def _on_afk_mode_change(self):
        if self.anti_afk_mode_var.get() == "keypress":
            self.safe_keys_entry.config(state="normal")
        else:
            self.safe_keys_entry.config(state="disabled")

    def _gather_settings(self):
        """Collect all GUI settings into a plain dict for JSON persistence."""
        return {
            "game_window_title": self.game_window_title_var.get(),
            "f_keys": {k: v.get() for k, v in self.f_keys_vars.items()},
            "party_reactive_enabled": self.party_reactive_enabled_var.get(),
            "party_heal_threshold": self.party_heal_threshold_var.get(),
            "party_panic_threshold": self.party_panic_threshold_var.get(),
            "party_center_x": self.party_center_x_var.get(),
            "party_bar_width": self.party_bar_width_var.get(),
            "party_bar_y": self.party_bar_y_var.get(),
            "party_bar_halfh": self.party_bar_halfh_var.get(),
            "cast_delay": self.cast_delay_var.get(),
            "heal_key": self.heal_key_var.get(),
            "heal_cooldown": self.heal_cooldown_var.get(),
            "anti_afk_mode": self.anti_afk_mode_var.get(),
            "safe_afk_keys": self.safe_afk_keys_var.get(),
            "buff_enabled": self.buff_enabled_var.get(),
            "buff_keys": self.buff_keys_var.get(),
            "buff_interval": self.buff_interval_var.get(),
            "reactive_enabled": self.reactive_enabled_var.get(),
            "self_heal_threshold": self.self_heal_threshold_var.get(),
            "self_panic_threshold": self.self_panic_threshold_var.get(),
            "hp_band": [self.hp_band_l_var.get(), self.hp_band_t_var.get(),
                        self.hp_band_r_var.get(), self.hp_band_b_var.get()],
            "hp_red_min": self.hp_red_min_var.get(),
            "hp_red_margin": self.hp_red_margin_var.get(),
        }

    def _apply_settings(self, d):
        """Apply a settings dict (from JSON) to the GUI variables, tolerating
        missing/extra keys so old config files keep working."""
        def setvar(var, key):
            if key in d and d[key] is not None:
                var.set(d[key])
        setvar(self.game_window_title_var, "game_window_title")
        for k, v in (d.get("f_keys") or {}).items():
            if k in self.f_keys_vars:
                self.f_keys_vars[k].set(v)
        setvar(self.party_reactive_enabled_var, "party_reactive_enabled")
        setvar(self.party_heal_threshold_var, "party_heal_threshold")
        setvar(self.party_panic_threshold_var, "party_panic_threshold")
        setvar(self.party_center_x_var, "party_center_x")
        setvar(self.party_bar_width_var, "party_bar_width")
        setvar(self.party_bar_y_var, "party_bar_y")
        setvar(self.party_bar_halfh_var, "party_bar_halfh")
        setvar(self.cast_delay_var, "cast_delay")
        setvar(self.heal_key_var, "heal_key")
        setvar(self.heal_cooldown_var, "heal_cooldown")
        setvar(self.anti_afk_mode_var, "anti_afk_mode")
        setvar(self.safe_afk_keys_var, "safe_afk_keys")
        setvar(self.buff_enabled_var, "buff_enabled")
        setvar(self.buff_keys_var, "buff_keys")
        setvar(self.buff_interval_var, "buff_interval")
        setvar(self.reactive_enabled_var, "reactive_enabled")
        setvar(self.self_heal_threshold_var, "self_heal_threshold")
        setvar(self.self_panic_threshold_var, "self_panic_threshold")
        band = d.get("hp_band")
        if isinstance(band, list) and len(band) == 4:
            self.hp_band_l_var.set(band[0])
            self.hp_band_t_var.set(band[1])
            self.hp_band_r_var.set(band[2])
            self.hp_band_b_var.set(band[3])
        setvar(self.hp_red_min_var, "hp_red_min")
        setvar(self.hp_red_margin_var, "hp_red_margin")

    def save_config(self, silent=False):
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(self._gather_settings(), f, indent=2)
            if not silent:
                print(f"[CONFIG] Saved to {CONFIG_PATH}")
        except Exception as e:
            print(f"[CONFIG] Save failed: {e}")

    def load_config(self):
        if not os.path.exists(CONFIG_PATH):
            return
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            print(f"[CONFIG] Load failed: {e}")
            return
        self._apply_settings(data)
        print(f"[CONFIG] Loaded settings from {CONFIG_PATH}")

    def on_close(self):
        self.save_config(silent=True)
        self.master.destroy()

    def populate_game_windows(self):
        windows = find_crow_windows()
        # Debug print: show the exact list of windows populating the dropdown
        print(f"DEBUG: Windows found for dropdown: {windows}") # ADDED DEBUG PRINT
        self.game_window_dropdown['values'] = windows
        if windows:
            # Try to select the previously selected window, or the first one
            current_selection = self.game_window_title_var.get()
            if current_selection in windows:
                self.game_window_title_var.set(current_selection)
            else:
                self.game_window_title_var.set(windows[0])
        else:
            self.game_window_title_var.set("")
            messagebox.showwarning("No Windows Found", "No 'NIGHT CROWS' windows detected. Please ensure the game is running.")

    def start_script(self):
        global running, stop_event

        if running:
            messagebox.showinfo("Info", "Script is already running.")
            return

        game_window_title = self.game_window_title_var.get()
        if not game_window_title:
            messagebox.showerror("Error", "Please select a game window.")
            return

        # Validate inputs
        try:
            
            heal_cooldown = float(self.heal_cooldown_var.get())
            cast_delay = float(self.cast_delay_var.get())
            heal_key_char = self.heal_key_var.get().upper()
            if not heal_key_char:
                raise ValueError("Heal Hotbar Key cannot be empty.")
            heal_vk = ord(heal_key_char)
        except ValueError as e:
            messagebox.showerror("Input Error", f"Invalid input: {e}")
            return

        # Slots to heal (0=F1..3=F4). Empty set => heal all auto-detected members.
        party_heal_slots = {i for i in range(4) if self.f_keys_vars[f"F{i + 1}"].get()}

        safe_afk_keys = parse_safe_afk_keys(self.safe_afk_keys_var.get())
        if self.anti_afk_mode_var.get() == "keypress" and not safe_afk_keys:
            messagebox.showwarning("Warning", "Safe Keys mode selected but no valid keys entered. Please enter at least one key.")
            return

        buff_enabled = self.buff_enabled_var.get()
        buff_keys = parse_safe_afk_keys(self.buff_keys_var.get())
        try:
            buff_interval = float(self.buff_interval_var.get()) * 60
            if buff_interval <= 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("Input Error", "Buff interval must be a positive number (in minutes).")
            return
        if buff_enabled and not buff_keys:
            messagebox.showwarning("Warning", "Buffs enabled but no valid buff keys entered.")
            return

        # Reactive self-heal settings
        reactive_enabled = self.reactive_enabled_var.get()
        hp_band = self._get_search_band()
        color_params = self._get_color_params()
        self_heal_threshold = SELF_HEAL_THRESHOLD
        self_panic_threshold = SELF_PANIC_THRESHOLD
        if reactive_enabled:
            if not hp_band:
                messagebox.showerror("Input Error", "Search band must be four numbers in 0-1 (L<R, T<B).")
                return
            if not color_params:
                messagebox.showerror("Input Error", "Red min / Dark max must be integers (0-255).")
                return
            try:
                self_heal_threshold = float(self.self_heal_threshold_var.get())
                self_panic_threshold = float(self.self_panic_threshold_var.get())
                if not (0 < self_panic_threshold <= self_heal_threshold <= 100):
                    raise ValueError
            except ValueError:
                messagebox.showerror(
                    "Input Error",
                    "Thresholds must satisfy 0 < panic <= heal <= 100.")
                return

        # Reactive party-heal settings (party size/positions are auto-detected)
        party_reactive_enabled = self.party_reactive_enabled_var.get()
        party_heal_threshold = PARTY_HEAL_THRESHOLD
        party_panic_threshold = PARTY_PANIC_THRESHOLD
        if party_reactive_enabled:
            try:
                party_heal_threshold = float(self.party_heal_threshold_var.get())
                party_panic_threshold = float(self.party_panic_threshold_var.get())
                if not (0 < party_panic_threshold <= party_heal_threshold <= 100):
                    raise ValueError
            except ValueError:
                messagebox.showerror(
                    "Input Error",
                    "Party thresholds must satisfy 0 < panic <= heal <= 100.")
                return

        stop_event.clear()
        running = True

        self.start_button.config(state="disabled")
        self.stop_button.config(state="normal")

        # Start the healer loop in a separate thread
        self.running_thread = threading.Thread(
            target=healer_loop,
            args=(
                heal_cooldown,
                cast_delay,
                heal_vk,
                game_window_title,
                self.anti_afk_mode_var.get(),
                safe_afk_keys,
                buff_enabled,
                buff_keys,
                buff_interval,
                reactive_enabled,
                hp_band if reactive_enabled else None,
                color_params[0] if color_params else HP_RED_MIN,
                color_params[1] if color_params else HP_RED_MARGIN,
                self_heal_threshold,
                self_panic_threshold,
                party_reactive_enabled,
                party_heal_slots if party_reactive_enabled else None,
                party_heal_threshold,
                party_panic_threshold,
                self._party_geom(),
            ),
            daemon=True
        )
        self.running_thread.start()
        print("Script started.")

    def stop_script(self):
        global running, stop_event
        if not running:
            messagebox.showinfo("Info", "Script is not running.")
            return

        stop_event.set()
        if self.running_thread and self.running_thread.is_alive():
            self.running_thread.join(timeout=1.0) # Wait for thread to finish
        running = False
        self.start_button.config(state="normal")
        self.stop_button.config(state="disabled")
        print("Script stopped.")

def main_gui():
    root = tk.Tk()
    app = MacroGUI(root)
    root.mainloop()

if __name__ == "__main__":
    main_gui()