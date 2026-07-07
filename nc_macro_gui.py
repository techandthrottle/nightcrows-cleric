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
from PIL import ImageGrab, ImageOps, ImageStat, Image
import numpy as np
from pynput import keyboard
from pynput.keyboard import Key
from pynput.keyboard import Listener as KeyListener
import logging
import sys
import queue

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
# The player's own HP bar region relative to the window client area:
# (left, top, right, bottom). Calibrate with the "Test HP Read" button.
HP_BAR_REGION_RELATIVE = (60, 945, 250, 970)
# Health-fill color range on PIL's 0-255 HSV Hue scale. Defaults target a red bar.
# Red sits near hue 0, so if HUE_LO > HUE_HI the range wraps past 255→0.
HP_FILL_HUE_LO  = 240
HP_FILL_HUE_HI  = 12
HP_FILL_SAT_MIN = 80
HP_FILL_VAL_MIN = 60
# Heal self when HP drops to/below this percent (respects the heal cooldown).
SELF_HEAL_THRESHOLD  = 70.0
# Emergency: heal self immediately, jumping the party rotation, at/below this percent.
SELF_PANIC_THRESHOLD = 35.0


HEAL_COOLDOWN     = 5.0
CAST_DELAY        = 0.8

HEAL_VK = ord("2")

# Special VK for self-heal in rotation
VK_SELF_IN_ROTATION = win32con.VK_ESCAPE # Using ESC to target self

# These will be dynamic based on GUI checkboxes
PARTY_VK_DEFAULT    = [win32con.VK_F1, win32con.VK_F2, win32con.VK_F3, win32con.VK_F4]
PARTY_NAMES_DEFAULT = ["F1", "F2", "F3", "F4"]

BUFF_HOTBAR_KEYS = [ord('5'), ord('6'), ord('7'), ord('8')]

POWER_SAVER_BRIGHTNESS_THRESHOLD = 30   
POWER_SAVER_SAMPLE_REGION = (400, 300, 900, 500)

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


party_index   = 0
party_heals_enabled = False # Default to False for GUI
selected_party_members_vk = [] # To be populated by GUI

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


def read_hp_percent_by_fill(hwnd, bar_region, hue_lo, hue_hi,
                            sat_min=HP_FILL_SAT_MIN, val_min=HP_FILL_VAL_MIN,
                            save_debug=False):
    """Estimate HP% by measuring how much of the bar is filled with the health
    color, instead of OCR-ing the HP number. This is far more robust against
    lighting changes, anti-aliasing, and small pixel drift.

    hue/sat/val are on PIL's 0-255 HSV scale. If hue_lo > hue_hi the hue range
    is treated as wrapping past 255→0 (needed for red bars near hue 0).
    Returns a float 0-100, or None on failure.
    """
    try:
        bbox = get_absolute_bbox(hwnd, bar_region)
        if not bbox:
            return None
        img = ImageGrab.grab(bbox=bbox, all_screens=True).convert("HSV")
        arr = np.asarray(img)
        h, s, v = arr[..., 0], arr[..., 1], arr[..., 2]

        if hue_lo <= hue_hi:
            hue_mask = (h >= hue_lo) & (h <= hue_hi)
        else:  # wrap-around (e.g. red: 240..255 or 0..12)
            hue_mask = (h >= hue_lo) | (h <= hue_hi)
        mask = hue_mask & (s >= sat_min) & (v >= val_min)

        # Collapse to columns: a column is "filled" if most of its pixels match.
        # Bars drain left-to-right, so the filled-column fraction is the HP%.
        col_filled = mask.mean(axis=0) > 0.5
        if col_filled.size == 0:
            return None

        if save_debug:
            Image.fromarray((mask * 255).astype("uint8")).save("debug_hp_fill.png")

        return 100.0 * float(col_filled.sum()) / float(col_filled.size)
    except Exception as e:
        print(f"[HP_READ] Fill read failed: {e}")
        return None


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
    party_heals_enabled_param,
    selected_party_members_vk_param,
    game_window_title_param,
    anti_afk_mode_param="movement",
    safe_afk_keys_param=None,
    buff_enabled_param=False,
    buff_keys_param=None,
    buff_interval_param=300,
    reactive_enabled_param=False,
    hp_bar_region_param=None,
    hp_fill_hue_lo_param=HP_FILL_HUE_LO,
    hp_fill_hue_hi_param=HP_FILL_HUE_HI,
    self_heal_threshold_param=SELF_HEAL_THRESHOLD,
    self_panic_threshold_param=SELF_PANIC_THRESHOLD,
):
    global running, party_index
    previous_target_vk = None
    last_buff_press_time = time.time()
    last_self_heal_time = 0.0

    print(f"DEBUG: Game window title used in healer_loop: '{game_window_title_param}' (Length: {len(game_window_title_param)})")
    hwnd = find_game_window(game_window_title_param)
    if not hwnd:
        running = False
        print("[HEALER_LOOP] Game window not found at start. Stopping.")
        return

    print(f"        Party Heals: {'ON' if party_heals_enabled_param else 'OFF'}")
    print(f"        Selected Party VKs: {selected_party_members_vk_param}")
    print(f"        Buffs: {'ON' if buff_enabled_param else 'OFF'}  Keys: {buff_keys_param}  Interval: {buff_interval_param}s")
    if reactive_enabled_param:
        print(f"        Reactive Self-Heal: ON  Heal<={self_heal_threshold_param:.0f}%  Panic<={self_panic_threshold_param:.0f}%")
    else:
        print("        Reactive Self-Heal: OFF")
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
        # Read our own HP from the bar fill and heal before running the rotation.
        if reactive_enabled_param and hp_bar_region_param:
            hp = read_hp_percent_by_fill(
                hwnd, hp_bar_region_param,
                hp_fill_hue_lo_param, hp_fill_hue_hi_param)
            if hp is not None:
                now = time.time()
                below_panic = hp <= self_panic_threshold_param
                below_heal = hp <= self_heal_threshold_param
                # Panic ignores the cooldown; normal self-heal respects it.
                if below_panic or (below_heal and now - last_self_heal_time >= heal_cooldown_param):
                    # Ensure the heal lands on self, not a lingering party target.
                    if previous_target_vk is not None and previous_target_vk != VK_SELF_IN_ROTATION:
                        print(f"[REACTIVE] Deselecting party target (VK {previous_target_vk}) before self-heal.")
                        _send_vk(hwnd, previous_target_vk)
                        time.sleep(0.1)
                        previous_target_vk = None
                    tag = "PANIC" if below_panic else "self-heal"
                    print(f"[REACTIVE] {tag}: self HP ~{hp:.0f}%. Healing self.")
                    heal_self(hwnd, heal_vk_param, cast_delay_param)
                    last_self_heal_time = now
                    # Re-check immediately; skip the party rotation this tick.
                    continue

        if party_heals_enabled_param and selected_party_members_vk_param:
            current_target_vk = selected_party_members_vk_param[party_index]
            
            if current_target_vk == VK_SELF_IN_ROTATION:
                # Re-press the previous party key to toggle (deselect) that target,
                # so the next heal lands on self. Safer than ESC which can open the menu.
                if previous_target_vk is not None and previous_target_vk != VK_SELF_IN_ROTATION:
                    print(f"[HEALER_LOOP] Deselecting party target by re-pressing VK {previous_target_vk}.")
                    _send_vk(hwnd, previous_target_vk)
                    time.sleep(0.1)
                else:
                    print("[HEALER_LOOP] No active party target — healing Self directly.")
                cast_heal(hwnd, heal_vk_param, cast_delay_param)
            else:
                # Only send the target key if it's a new target
                if current_target_vk != previous_target_vk:
                    print(f"[HEALER_LOOP] Targeting Party member (VK: {current_target_vk}).")
                    _send_vk(hwnd, current_target_vk)
                    time.sleep(0.2)
                else:
                    print(f"[HEALER_LOOP] Target (VK: {current_target_vk}) already selected. Skipping target key press.")
                cast_heal(hwnd, heal_vk_param, cast_delay_param)
            
            previous_target_vk = current_target_vk # Update previous_target_vk
            party_index = (party_index + 1) % len(selected_party_members_vk_param)
            
            print(f"[HEALER_LOOP] Party heal sent. Next target index: {party_index}.")
            
            cooldown_end = time.time() + heal_cooldown_param
            print(f"[HEALER_LOOP] Entering party heal cooldown for {heal_cooldown_param}s.")
            while time.time() < cooldown_end:
                if stop_event.is_set(): 
                    print("[HEALER_LOOP] Stop event set during cooldown.")
                    break
                
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
        master.title("Night Crows Auto Healer")

        self.running_thread = None

        # Variables for GUI elements
        self.game_window_title_var = tk.StringVar() # New variable for selected game window
        self.party_heals_enabled_var = tk.BooleanVar(value=False)
        self.f_keys_vars = {
            "F1": tk.BooleanVar(value=False),
            "F2": tk.BooleanVar(value=False),
            "F3": tk.BooleanVar(value=False),
            "F4": tk.BooleanVar(value=False)
        }
        self.self_heal_in_rotation_var = tk.BooleanVar(value=False) # New variable for self-heal in rotation
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
        self.hp_bar_left_var = tk.StringVar(value=str(HP_BAR_REGION_RELATIVE[0]))
        self.hp_bar_top_var = tk.StringVar(value=str(HP_BAR_REGION_RELATIVE[1]))
        self.hp_bar_right_var = tk.StringVar(value=str(HP_BAR_REGION_RELATIVE[2]))
        self.hp_bar_bottom_var = tk.StringVar(value=str(HP_BAR_REGION_RELATIVE[3]))
        self.hp_hue_lo_var = tk.StringVar(value=str(HP_FILL_HUE_LO))
        self.hp_hue_hi_var = tk.StringVar(value=str(HP_FILL_HUE_HI))

        # Create GUI elements
        self.create_widgets()
        self.populate_game_windows() # Populate dropdown on startup
        self.poll_log_queue() # Start draining console output into the log panel

    def create_widgets(self):
        # Frame for Script Control
        script_frame = ttk.LabelFrame(self.master, text="Script Control")
        script_frame.pack(padx=10, pady=5, fill="x")

        self.start_button = ttk.Button(script_frame, text="Start Script", command=self.start_script)
        self.start_button.pack(side="left", padx=5, pady=5)

        self.stop_button = ttk.Button(script_frame, text="Stop Script", command=self.stop_script, state="disabled")
        self.stop_button.pack(side="left", padx=5, pady=5)

        # Frame for Game Window Selection
        game_window_frame = ttk.LabelFrame(self.master, text="Game Window Selection")
        game_window_frame.pack(padx=10, pady=5, fill="x")

        ttk.Label(game_window_frame, text="Select Game Window:").pack(side="left", padx=5, pady=2)
        self.game_window_dropdown = ttk.Combobox(game_window_frame, textvariable=self.game_window_title_var, state="readonly")
        self.game_window_dropdown.pack(side="left", padx=5, pady=2, fill="x", expand=True)
        self.refresh_windows_button = ttk.Button(game_window_frame, text="Refresh", command=self.populate_game_windows)
        self.refresh_windows_button.pack(side="left", padx=5, pady=2)

        # Frame for Party Heal Settings
        party_frame = ttk.LabelFrame(self.master, text="Party Heal Settings")
        party_frame.pack(padx=10, pady=5, fill="x")

        ttk.Checkbutton(
            party_frame,
            text="Enable Party Heals",
            variable=self.party_heals_enabled_var,
            command=self.toggle_party_heals
        ).pack(anchor="w", padx=5, pady=2)

        f_keys_frame = ttk.Frame(party_frame)
        f_keys_frame.pack(anchor="w", padx=5, pady=2)
        ttk.Label(f_keys_frame, text="Party Members (F-keys):").pack(side="left")
        for text, var in self.f_keys_vars.items():
            ttk.Checkbutton(f_keys_frame, text=text, variable=var).pack(side="left", padx=2)
        
        # New checkbox for self-heal in rotation
        ttk.Checkbutton(f_keys_frame, text="Self", variable=self.self_heal_in_rotation_var).pack(side="left", padx=2)

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

        ttk.Label(reactive_frame, text="Heal below (%):").grid(row=1, column=0, sticky="w", padx=5, pady=2)
        ttk.Entry(reactive_frame, textvariable=self.self_heal_threshold_var, width=6).grid(row=1, column=1, sticky="w", padx=5, pady=2)
        ttk.Label(reactive_frame, text="Panic below (%):").grid(row=1, column=2, sticky="w", padx=5, pady=2)
        ttk.Entry(reactive_frame, textvariable=self.self_panic_threshold_var, width=6).grid(row=1, column=3, sticky="w", padx=5, pady=2)

        ttk.Label(reactive_frame, text="HP bar region (L T R B):").grid(row=2, column=0, sticky="w", padx=5, pady=2)
        region_frame = ttk.Frame(reactive_frame)
        region_frame.grid(row=2, column=1, columnspan=3, sticky="w", padx=5, pady=2)
        for var in (self.hp_bar_left_var, self.hp_bar_top_var, self.hp_bar_right_var, self.hp_bar_bottom_var):
            ttk.Entry(region_frame, textvariable=var, width=6).pack(side="left", padx=2)

        ttk.Label(reactive_frame, text="Health hue lo/hi (0-255):").grid(row=3, column=0, sticky="w", padx=5, pady=2)
        hue_frame = ttk.Frame(reactive_frame)
        hue_frame.grid(row=3, column=1, columnspan=2, sticky="w", padx=5, pady=2)
        ttk.Entry(hue_frame, textvariable=self.hp_hue_lo_var, width=6).pack(side="left", padx=2)
        ttk.Entry(hue_frame, textvariable=self.hp_hue_hi_var, width=6).pack(side="left", padx=2)
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

    def _get_hp_bar_region(self):
        """Parse the four HP-bar-region entries into an (L, T, R, B) tuple, or None."""
        try:
            return (
                int(self.hp_bar_left_var.get()),
                int(self.hp_bar_top_var.get()),
                int(self.hp_bar_right_var.get()),
                int(self.hp_bar_bottom_var.get()),
            )
        except ValueError:
            return None

    def _get_hue_range(self):
        """Parse the hue lo/hi entries into ints, or None."""
        try:
            return int(self.hp_hue_lo_var.get()), int(self.hp_hue_hi_var.get())
        except ValueError:
            return None

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
        region = self._get_hp_bar_region()
        if not region:
            messagebox.showerror("Error", "HP bar region must be four integers.")
            return
        hue = self._get_hue_range()
        if not hue:
            messagebox.showerror("Error", "Health hue lo/hi must be integers (0-255).")
            return
        hp = read_hp_percent_by_fill(hwnd, region, hue[0], hue[1], save_debug=True)
        if hp is None:
            print("[TEST] HP read returned None — check region/hue. Saved debug_hp_fill.png.")
        else:
            print(f"[TEST] Self HP ~ {hp:.1f}%  (mask saved to debug_hp_fill.png)")

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
        global running, stop_event, party_index

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

        selected_party_members_vk = []
        for key_name, var in self.f_keys_vars.items():
            if var.get():
                # Convert F-key string to VK code
                if key_name == "F1": selected_party_members_vk.append(win32con.VK_F1)
                elif key_name == "F2": selected_party_members_vk.append(win32con.VK_F2)
                elif key_name == "F3": selected_party_members_vk.append(win32con.VK_F3)
                elif key_name == "F4": selected_party_members_vk.append(win32con.VK_F4)
        
        print(f"DEBUG: self_heal_in_rotation_var (before building VK list): {self.self_heal_in_rotation_var.get()}")
        # Add self to rotation if checked
        if self.self_heal_in_rotation_var.get():
            selected_party_members_vk.append(VK_SELF_IN_ROTATION)

        # If party heals are disabled, clear the selected party members list
        if not self.party_heals_enabled_var.get():
            selected_party_members_vk = []

        if self.party_heals_enabled_var.get() and not selected_party_members_vk:
            messagebox.showwarning("Warning", "Party heals enabled but no party members or self selected for rotation.")
            # Optionally, disable party heals or force selection

        print(f"DEBUG: self_heal_in_rotation_var: {self.self_heal_in_rotation_var.get()}")
        print(f"DEBUG: selected_party_members_vk: {selected_party_members_vk}")

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
        hp_bar_region = self._get_hp_bar_region()
        hue_range = self._get_hue_range()
        self_heal_threshold = SELF_HEAL_THRESHOLD
        self_panic_threshold = SELF_PANIC_THRESHOLD
        if reactive_enabled:
            if not hp_bar_region:
                messagebox.showerror("Input Error", "HP bar region must be four integers.")
                return
            if not hue_range:
                messagebox.showerror("Input Error", "Health hue lo/hi must be integers (0-255).")
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

        stop_event.clear()
        running = True
        party_index = 0 # Reset party index on start

        self.start_button.config(state="disabled")
        self.stop_button.config(state="normal")

        # Start the healer loop in a separate thread
        self.running_thread = threading.Thread(
            target=healer_loop,
            args=(
                heal_cooldown,
                cast_delay,
                heal_vk,
                self.party_heals_enabled_var.get(),
                selected_party_members_vk,
                game_window_title,
                self.anti_afk_mode_var.get(),
                safe_afk_keys,
                buff_enabled,
                buff_keys,
                buff_interval,
                reactive_enabled,
                hp_bar_region if reactive_enabled else None,
                hue_range[0] if hue_range else HP_FILL_HUE_LO,
                hue_range[1] if hue_range else HP_FILL_HUE_HI,
                self_heal_threshold,
                self_panic_threshold,
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

    def toggle_party_heals(*args):
        # This function is called when the "Enable Party Heals" checkbox is toggled
        # The state is automatically updated in self.party_heals_enabled_var
        pass # No explicit action needed here, the variable holds the state

def main_gui():
    root = tk.Tk()
    app = MacroGUI(root)
    root.mainloop()

if __name__ == "__main__":
    main_gui()