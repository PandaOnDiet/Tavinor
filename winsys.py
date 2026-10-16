"""What is in front of the user right now: the foreground window's app,
title, and whether it fills the screen. Shared by hover-to-open (stay out of
full-screen video and games) and focus mode (spot a blocked site).

Every Win32 call declares its types. Undeclared, ctypes assumes int and
truncates 64-bit handles - that already crashed CreateWindowExW once.
"""
import ctypes
import re
from ctypes import wintypes

u32 = ctypes.windll.user32
k32 = ctypes.windll.kernel32

u32.GetForegroundWindow.restype = wintypes.HWND
u32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
u32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
u32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
u32.GetWindowThreadProcessId.argtypes = [wintypes.HWND,
                                         ctypes.POINTER(wintypes.DWORD)]
u32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
u32.MonitorFromWindow.restype = wintypes.HMONITOR
u32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
u32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
u32.IsZoomed.argtypes = [wintypes.HWND]
u32.GetAsyncKeyState.restype = ctypes.c_short
u32.GetAsyncKeyState.argtypes = [ctypes.c_int]
k32.OpenProcess.restype = wintypes.HANDLE
k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
k32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
    ctypes.POINTER(wintypes.DWORD)]
k32.CloseHandle.argtypes = [wintypes.HANDLE]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


u32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MONITORINFO)]

BROWSERS = {"chrome.exe", "msedge.exe", "brave.exe", "firefox.exe",
            "opera.exe", "vivaldi.exe", "arc.exe"}
# The desktop itself reports a screen-sized window; it is not "full screen".
DESKTOP_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd"}


def _exe(pid):
    h = k32.OpenProcess(0x1000, False, pid)        # QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        buf, n = ctypes.create_unicode_buffer(520), wintypes.DWORD(520)
        if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
            return buf.value.rsplit("\\", 1)[-1].lower()
        return ""
    finally:
        k32.CloseHandle(h)


def foreground():
    """{hwnd, exe, title, cls, fullscreen, browser} for the window in front."""
    hwnd = u32.GetForegroundWindow()
    if not hwnd:
        return {"hwnd": None, "exe": "", "title": "", "cls": "",
                "fullscreen": False, "browser": False}
    n = u32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    u32.GetWindowTextW(hwnd, buf, n + 1)
    cls = ctypes.create_unicode_buffer(256)
    u32.GetClassNameW(hwnd, cls, 256)
    pid = wintypes.DWORD()
    u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    exe = _exe(pid.value)

    full = False
    r, mi = wintypes.RECT(), MONITORINFO()
    mi.cbSize = ctypes.sizeof(MONITORINFO)
    if (u32.GetWindowRect(hwnd, ctypes.byref(r)) and u32.GetMonitorInfoW(
            u32.MonitorFromWindow(hwnd, 2), ctypes.byref(mi))):
        m = mi.rcMonitor
        full = is_fullscreen((r.left, r.top, r.right, r.bottom),
                             (m.left, m.top, m.right, m.bottom),
                             bool(u32.IsZoomed(hwnd)), cls.value)
    return {"hwnd": hwnd, "exe": exe, "title": buf.value, "cls": cls.value,
            "fullscreen": full, "browser": exe in BROWSERS}


def is_fullscreen(rect, monitor, zoomed, cls):
    """True full screen (video, games, F11) - not merely maximised.

    Covering the monitor is not enough: with the taskbar on auto-hide a
    maximised window covers it too (its frame overhangs by ~7 px), and
    hover-to-open stopped working over every maximised window. Windows flags
    a maximised window as zoomed; true full screen is not flagged.
    """
    if zoomed or cls in DESKTOP_CLASSES:
        return False
    return (rect[0] <= monitor[0] and rect[1] <= monitor[1]
            and rect[2] >= monitor[2] and rect[3] >= monitor[3])


def cursor():
    pt = wintypes.POINT()
    u32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


def mouse_down():
    """Left or right button held - dragging a window, not hovering."""
    return bool(u32.GetAsyncKeyState(0x01) & 0x8000
                or u32.GetAsyncKeyState(0x02) & 0x8000)


# Browsers append their own name (and Edge a profile and a zero-width space):
# "Lofi - YouTube - Google Chrome", "x and 3 more pages - Work - Microsoft​ Edge".
# Only the browser name is stripped. A profile can't be told apart from a
# site name, so callers match whole segments instead of "the last one".
_BROWSER_TAIL = re.compile(r" [-—] (?:Google Chrome|Microsoft Edge|Brave|"
                           r"Mozilla Firefox|Firefox|Opera|Vivaldi|Arc)$")


def segments(title):
    """A browser title split into its parts, lowercased:
    "(3) Home / X - Google Chrome" -> ["home", "x"]."""
    t = _BROWSER_TAIL.sub("", title.replace("​", "")).strip()
    t = re.sub(r"^\(\d+\+?\)\s*", "", t)
    t = re.sub(r" and \d+ more pages?\b", "", t)
    return [s.strip().lower() for s in re.split(r" [-—|/·] ", t) if s.strip()]
