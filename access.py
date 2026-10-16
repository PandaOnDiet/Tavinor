"""Eyes and hands, behind one access switch (phase 7).

Levels (Settings -> Screen access, or the tray):
  off   no reading, typing, clicking or running. Commands and chat only.
  ask   (default) may read the window in front when you ask; every hands
        action shows an approval card first.
  full  reads and acts on its own for safe actions.

ALWAYS ASK, even on full - this list can be made stricter, never looser:
running commands, Enter (it sends messages), Delete, closing windows or
tabs, typing a line break, and clicking anything named like send / delete /
pay / buy / install / submit / sign in / discard / close.

Approval can't be forged: dispatch() refuses a gated action unless it
carries approved=True, which only the chat sets after your click - the
model's own JSON never gets that field through (agent.clean whitelists).

Ctrl+Alt+Esc is the panic key: access drops to off and any typing in
progress stops at the next character. Every action is logged in cane.db.
Full drops back to ask on restart unless "keep" is switched on.

Eyes read text through Windows UI Automation (built in, nothing installed):
a document's text where the app exposes it, otherwise the names of what is
on screen. Nothing is captured as an image.
"""
import base64
import ctypes
import os
import re
import sqlite3
import subprocess
import threading
import time
from ctypes import wintypes
from datetime import datetime

import cane
import winsys

LEVELS = ("off", "ask", "full")
EYES = {"read", "windows"}
HANDS = {"switch", "type", "keys", "click", "run"}
RISKY_KEYS = {"enter", "ctrl+enter", "shift+enter", "delete", "shift+delete",
              "alt+f4", "ctrl+w", "ctrl+shift+w", "ctrl+q", "ctrl+f4"}
RISKY_WORDS = (r"send|delete|remove|erase|pay|buy|purchase|order|checkout|"
               r"install|uninstall|submit|post|publish|reply|confirm|transfer|"
               r"sign ?in|log ?in|password|discard|don'?t save|close|format|reset")
TYPE_DELAY = 0.008       # seconds between typed characters

stop = threading.Event()  # set by the panic key
_last_other = [None]      # last foreground window that wasn't Cane's own
_LOCK = threading.Lock()
DB = cane.Path(cane.__file__).with_name("cane.db")


# ------------------------------------------------------------- level

def level():
    v = cane.setting("access", "ask")
    return v if v in LEVELS else "ask"


def set_level(v):
    if v in LEVELS:
        cane.set_setting("access", v)
        if v != "off":
            stop.clear()


def on_start():
    """Full drops to ask on restart unless kept. Never raises a level: off
    stays off, so a restart can't hand out access you took away."""
    if level() == "full" and not cane.setting("access_keep", False):
        set_level("ask")


def risky(a):
    """Why this action must always be approved, or "" if it needn't be."""
    act, arg = a.get("action"), str(a.get("arg") or "")
    if act == "run":
        return "it runs a command"
    if act == "type" and ("\n" in arg or "\r" in arg):
        return "it presses Enter"
    if act == "keys" and norm_keys(arg) in RISKY_KEYS:
        return "%s can send, delete or close" % norm_keys(arg)
    if act == "click" and re.search(r"\b(%s)\b" % RISKY_WORDS, arg.lower()):
        return "the button sounds like it sends, pays, deletes or installs"
    return ""


def needs_approval(a):
    """Does this action need a click from the user at the current level?"""
    if a.get("action") in EYES:
        return False
    return level() != "full" or bool(risky(a))


# ------------------------------------------------------------- windows

def _is_cane(hwnd):
    pid = wintypes.DWORD()
    winsys.u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value == os.getpid()


def track():
    """Remember the last window in front that isn't Cane's: while you chat,
    the chat has focus, and "read my screen" means the window behind it."""
    while True:
        try:
            h = winsys.u32.GetForegroundWindow()
            if h and not _is_cane(h):
                _last_other[0] = h
        except Exception:
            pass
        time.sleep(0.5)


def target():
    h = winsys.u32.GetForegroundWindow()
    if h and not _is_cane(h):
        return h
    return _last_other[0]


def windows():
    """[(hwnd, title, exe)] of ordinary visible windows, front first."""
    u = winsys.u32
    u.IsWindowVisible.argtypes = [wintypes.HWND]
    u.GetWindow.restype = wintypes.HWND
    u.GetWindow.argtypes = [wintypes.HWND, ctypes.c_uint]
    out, seen = [], set()
    P = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def cb(h, _):
        if not u.IsWindowVisible(h) or u.GetWindow(h, 4) or _is_cane(h):
            return True                       # hidden, owned (dialog bits), ours
        n = u.GetWindowTextLengthW(h)
        if not n:
            return True
        b = ctypes.create_unicode_buffer(n + 1)
        u.GetWindowTextW(h, b, n + 1)
        if b.value == "Program Manager":       # the desktop
            return True
        pid = wintypes.DWORD()
        u.GetWindowThreadProcessId(h, ctypes.byref(pid))
        if h not in seen:
            seen.add(h)
            out.append((h, b.value, winsys._exe(pid.value)))
        return True
    u.EnumWindows(P(cb), 0)
    return out


def bring(h):
    """Bring a window to the front.

    Windows refuses SetForegroundWindow to a background process, so attach
    to the foreground thread's input for the moment of the switch. Not the
    usual Alt-tap trick: live, the form took that Alt as "open the menu"
    and swallowed the next typed letter - in a real app it could fire an
    Alt shortcut instead.
    """
    u, k = winsys.u32, winsys.k32
    u.SetForegroundWindow.argtypes = [wintypes.HWND]
    u.BringWindowToTop.argtypes = [wintypes.HWND]
    u.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    u.IsIconic.argtypes = [wintypes.HWND]
    u.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
    if u.IsIconic(h):
        u.ShowWindow(h, 9)                   # SW_RESTORE
    fg = u.GetForegroundWindow()
    them = u.GetWindowThreadProcessId(fg, None) if fg else 0
    me = k.GetCurrentThreadId()
    attached = bool(them and them != me and u.AttachThreadInput(me, them, True))
    try:
        u.BringWindowToTop(h)
        u.SetForegroundWindow(h)
    finally:
        if attached:
            u.AttachThreadInput(me, them, False)
    time.sleep(0.25)
    return u.GetForegroundWindow() == h


def find_window(words):
    words = words.lower().strip()
    rows = windows()
    return (next((r for r in rows if words in r[1].lower()), None)
            or next((r for r in rows if words in r[2].lower()), None))


# ------------------------------------------------------------- hands

VK = {"ctrl": 0x11, "control": 0x11, "alt": 0x12, "shift": 0x10, "win": 0x5B,
      "enter": 0x0D, "return": 0x0D, "tab": 0x09, "esc": 0x1B, "escape": 0x1B,
      "space": 0x20, "backspace": 0x08, "delete": 0x2E, "del": 0x2E,
      "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
      "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27}
VK.update({"f%d" % i: 0x6F + i for i in range(1, 13)})
MODS = ("ctrl", "shift", "alt", "win")


def norm_keys(combo):
    """"Control + S" -> "ctrl+s", modifiers first in a fixed order."""
    parts = [p.strip().lower() for p in re.split(r"\s*\+\s*", combo or "") if p.strip()]
    parts = ["ctrl" if p == "control" else "delete" if p == "del" else
             "enter" if p == "return" else "esc" if p == "escape" else p for p in parts]
    mods = [m for m in MODS if m in parts]
    rest = [p for p in parts if p not in MODS]
    return "+".join(mods + rest)


def press(combo):
    keys = norm_keys(combo).split("+")
    vks = []
    for k in keys:
        if k in VK:
            vks.append(VK[k])
        elif len(k) == 1 and k.isalnum():
            vks.append(ord(k.upper()))
        else:
            raise ValueError("unknown key %r" % k)
    u = winsys.u32
    for v in vks:
        u.keybd_event(v, 0, 0, 0)
    for v in reversed(vks):
        u.keybd_event(v, 0, 2, 0)


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


class INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("pad", ctypes.c_byte * 32)]
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


def type_text(text):
    """Type as Unicode keystrokes. Checks the panic flag between characters.
    Returns how many characters went in."""
    send = winsys.u32.SendInput
    send.argtypes = [ctypes.c_uint, ctypes.POINTER(INPUT), ctypes.c_int]
    n = 0
    for ch in text:
        if stop.is_set():
            break
        for flags in (0x0004, 0x0004 | 0x0002):          # UNICODE, then KEYUP
            i = INPUT(type=1)
            i.ki = KEYBDINPUT(0, ord(ch), flags, 0, 0)
            send(1, ctypes.byref(i), ctypes.sizeof(INPUT))
        n += 1
        time.sleep(TYPE_DELAY)
    return n


# ------------------------------------------------------------- eyes

READ_PS = r"""
Add-Type -AssemblyName UIAutomationClient,UIAutomationTypes
[Console]::OutputEncoding=[Text.Encoding]::UTF8
$A=[System.Windows.Automation.AutomationElement]
$T=[System.Windows.Automation.TreeScope]::Descendants
$root=$A::FromHandle([IntPtr]%d)
$sb=New-Object System.Text.StringBuilder
$tp=[System.Windows.Automation.TextPattern]::Pattern
$doc=New-Object System.Windows.Automation.PropertyCondition($A::ControlTypeProperty,[System.Windows.Automation.ControlType]::Document)
foreach($d in $root.FindAll($T,$doc)){ $p=$null
  if($d.TryGetCurrentPattern($tp,[ref]$p)){ [void]$sb.AppendLine($p.DocumentRange.GetText(6000)) } }
if($sb.Length -lt 200){ $n=0
  foreach($e in $root.FindAll($T,[System.Windows.Automation.Condition]::TrueCondition)){
    $nm=$e.Current.Name; if($nm -and $nm.Length -gt 1){ [void]$sb.AppendLine($nm); $n++; if($n -gt 400){break} } } }
$s=$sb.ToString(); if($s.Length -gt 8000){$s=$s.Substring(0,8000)}; $s
"""

CLICK_PS = r"""
Add-Type -AssemblyName UIAutomationClient,UIAutomationTypes
[Console]::OutputEncoding=[Text.Encoding]::UTF8
$A=[System.Windows.Automation.AutomationElement]
$root=$A::FromHandle([IntPtr]%d)
$want='%s'; $risky='%s'; $ok=%s
$types='button','hyperlink','menu item','list item','tab item','check box','radio button','split button'
# Exact name on a clickable type, then exact name on anything (some apps
# report every control as "pane"), then a partial name.
$typed=$null; $any=$null; $loose=$null
foreach($e in $root.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.Condition]::TrueCondition)){
  $c=$e.Current; if(-not $c.Name){continue}
  if($c.Name -ieq $want){ if($types -contains $c.LocalizedControlType){$typed=$e;break}; if(-not $any){$any=$e} }
  elseif(-not $loose -and $c.Name -like "*$want*"){$loose=$e} }
$hit=$typed; if(-not $hit){$hit=$any}; if(-not $hit){$hit=$loose}
if(-not $hit){"NOTFOUND"; exit}
$name=$hit.Current.Name
if(-not $ok -and $name -imatch "\b($risky)\b"){"RISKY:$name"; exit}
$p=$null
if($hit.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern,[ref]$p)){$p.Invoke(); "CLICKED:$name"; exit}
if($hit.TryGetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern,[ref]$p)){$p.Select(); "CLICKED:$name"; exit}
if($hit.TryGetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern,[ref]$p)){$p.Toggle(); "CLICKED:$name"; exit}
# No click action exposed: click its centre with the mouse, then put the
# cursor back. DPI-aware, because UIA reports physical pixels.
$r=$hit.Current.BoundingRectangle
if($r.IsEmpty -or $r.Width -lt 1){"NOPATTERN:$name"; exit}
Add-Type -Name M -Namespace W -MemberDefinition '[DllImport("user32.dll")] public static extern bool SetProcessDPIAware(); [DllImport("user32.dll")] public static extern bool SetCursorPos(int x,int y); [DllImport("user32.dll")] public static extern bool GetCursorPos(out System.Drawing.Point p); [DllImport("user32.dll")] public static extern void mouse_event(int f,int x,int y,int d,int e);' -ReferencedAssemblies System.Drawing
[W.M]::SetProcessDPIAware()|Out-Null
$old=New-Object System.Drawing.Point; [W.M]::GetCursorPos([ref]$old)|Out-Null
[W.M]::SetCursorPos([int]($r.X+$r.Width/2),[int]($r.Y+$r.Height/2))|Out-Null
[W.M]::mouse_event(2,0,0,0,0); [W.M]::mouse_event(4,0,0,0,0)
Start-Sleep -Milliseconds 80; [W.M]::SetCursorPos($old.X,$old.Y)|Out-Null
"CLICKED:$name (mouse)"
"""


def _ps(script, timeout=15):
    enc = base64.b64encode(script.encode("utf-16-le")).decode()
    r = subprocess.run(["powershell", "-NoProfile", "-EncodedCommand", enc],
                       capture_output=True, timeout=timeout,
                       creationflags=subprocess.CREATE_NO_WINDOW)
    return r.stdout.decode("utf-8", "ignore").strip()


def read(h):
    text = _ps(READ_PS % h)
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    out, seen = [], set()
    for l in lines:                        # UI trees repeat names a lot
        if l not in seen:
            seen.add(l)
            out.append(l)
    return "\n".join(out)[:6000]


# ------------------------------------------------------------- log

def log(a, approved, result):
    try:
        with _LOCK, sqlite3.connect(DB, timeout=5) as c:
            c.execute("CREATE TABLE IF NOT EXISTS actions (at TEXT, level TEXT, "
                      "action TEXT, arg TEXT, approved INTEGER, result TEXT)")
            c.execute("INSERT INTO actions VALUES (?,?,?,?,?,?)", (
                datetime.now().isoformat(timespec="seconds"), level(),
                a.get("action", ""), str(a.get("arg") or "")[:200],
                int(bool(approved)), str(result)[:200]))
    except sqlite3.Error:
        pass                              # a log failure must not stop the action


def recent(n=30):
    try:
        with _LOCK, sqlite3.connect(DB, timeout=5) as c:
            return c.execute("SELECT at, level, action, arg, approved, result FROM "
                             "actions ORDER BY rowid DESC LIMIT ?", (n,)).fetchall()
    except sqlite3.Error:
        return []


# ------------------------------------------------------------- dispatch

def dispatch(intent, ui=None):
    a = intent
    act = a.get("action") or ""
    arg = str(a.get("arg") or "")
    if act not in EYES | HANDS:
        return "unknown screen action"
    if level() == "off":
        return "screen access is off - switch it on in Settings > Screen access"
    if needs_approval(a) and not a.get("approved"):
        why = risky(a) or "screen access is on Ask"
        log(a, False, "refused: needs approval")
        return "not done - needs your approval (%s)" % why
    if stop.is_set():
        return "stopped by the panic key"
    show = indicator(ui, "reading" if act in EYES else "acting")
    try:
        result = _do(act, arg, a.get("approved"))
    except Exception as e:
        result = "failed: %s" % e
    finally:
        show()
    log(a, a.get("approved"), result)
    return result


def _do(act, arg, approved):
    if act == "windows":
        rows = windows()[:15]
        return "open windows: " + "; ".join("%s (%s)" % (t[:50], e) for _, t, e in rows)
    if act == "run":
        r = subprocess.run(["powershell", "-NoProfile", "-Command", arg],
                           capture_output=True, timeout=60,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        out = (r.stdout + r.stderr).decode("utf-8", "ignore").strip()
        return "exit %d: %s" % (r.returncode, out[:1500] or "(no output)")
    if act == "switch":
        w = find_window(arg)
        if not w:
            return "no open window matching %r" % arg
        ok = bring(w[0])
        if ok:
            _last_other[0] = w[0]
        return ("switched to %s" if ok else "Windows wouldn't bring %s forward") % w[1][:50]

    h = target()
    if not h:
        return "no window to work with - open or switch to one first"
    if act == "read":
        text = read(h)
        return ("screen text:\n" + text) if text else "couldn't read any text there"
    if act == "click":
        # Single quotes doubled for PowerShell: "don't save" in the risky
        # list once broke the whole script, and every click failed silently.
        out = _ps(CLICK_PS % (h, arg.replace("'", "''"),
                              RISKY_WORDS.replace("'", "''"),
                              "$true" if approved else "$false"))
        if not out:
            return "the click failed - Windows UI Automation gave no answer"
        if out.startswith("CLICKED:"):
            return "clicked " + out[8:]
        if out.startswith("RISKY:"):
            return "not clicked - %r looks risky; it needs your approval" % out[6:]
        if out.startswith("NOPATTERN:"):
            return "found %r but it can't be clicked that way" % out[10:]
        return "nothing called %r in that window" % arg
    # type / keys go to the target window, never to Cane's own chat.
    if winsys.u32.GetForegroundWindow() != h and not bring(h):
        return "couldn't bring the window forward, so nothing was typed"
    if act == "keys":
        press(arg)
        return "pressed " + norm_keys(arg)
    if act == "type":
        n = type_text(arg)
        return ("typed %d characters" % n) + (" - stopped by the panic key"
                                              if n < len(arg) else "")
    return "unknown"


def indicator(ui, what):
    """A pill at the top of the screen while Cane is reading or acting.
    Returns a function that removes it (after at least 0.8 s, to be seen)."""
    if not ui:
        return lambda: None
    started, box = time.monotonic(), {}

    def make():
        import tkinter as tk
        w = tk.Toplevel(ui.root)
        w.overrideredirect(True)
        w.attributes("-topmost", True)
        w.configure(bg="#ff8a3d")
        tk.Label(w, text="●  Cane is %s your screen  ·  Ctrl+Alt+Esc stops it"
                 % ("reading" if what == "reading" else "using"),
                 bg="#ff8a3d", fg="#111", font=("Segoe UI", 9, "bold"),
                 padx=12, pady=3).pack()
        w.update_idletasks()
        x = (ui.root.winfo_screenwidth() - w.winfo_width()) // 2
        w.geometry("+%d+0" % x)
        box["w"] = w
    ui.root.after(0, make)
    ui.set_face("eyes")

    def done():
        wait = max(0, 0.8 - (time.monotonic() - started))
        ui.root.after(int(wait * 1000), lambda: box.get("w") and box["w"].destroy())
        import face
        ui.set_face(face.resting())
    return done


def panic(bar):
    """Ctrl+Alt+Esc: off, and stop whatever is running."""
    stop.set()
    cane.set_setting("access", "off")
    log({"action": "panic"}, True, "access set to off")
    bar.root.after(0, lambda: bar.alert(
        {"text": "Stopped. Screen access is now Off - turn it back on in Settings."},
        title="PANIC KEY"))


def describe(it):
    act, arg = it.get("action"), str(it.get("arg") or "")
    return {"read": "read the window in front", "windows": "list open windows",
            "switch": "switch to %s" % arg[:30], "type": "type “%s”" % arg[:30],
            "keys": "press %s" % norm_keys(arg), "click": "click “%s”" % arg[:30],
            "run": "run: %s" % arg[:40]}.get(act, "screen")


try:
    import agent
    agent.EXTRA["screen"] = (
        '{"intent":"screen","action":"read"}  read the text of the window in front; '
        '"action":"windows" lists open windows; "switch" + "arg":"<window title words>"; '
        '"type" + "arg":"<text>" types into the window in front; "keys" + "arg":'
        '"ctrl+s"; "click" + "arg":"<button name>"; "run" + "arg":"<PowerShell '
        'command>". Switch to the right window before typing, pressing or clicking.',
        lambda a: not needs_approval(a))
except ImportError:
    pass
