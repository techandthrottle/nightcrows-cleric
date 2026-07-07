"""
find_windows.py — Lists all open window titles containing 'CROW'
Run this to find the exact window title of your Night Crows instances.
"""

import win32gui

def find_crow_windows():
    results = []

    def callback(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd)
            if "CROW" in title.upper() and title.strip():
                results.append((hwnd, repr(title)))

    win32gui.EnumWindows(callback, None)

    if results:
        print(f"Found {len(results)} Night Crows window(s):\n")
        for hwnd, title in results:
            print(f"  HWND : {hwnd}")
            print(f"  Title: {title}")
            print()
    else:
        print("No Night Crows windows found. Make sure the game is running.")

if __name__ == "__main__":
    find_crow_windows()
    input("\nPress Enter to exit...")