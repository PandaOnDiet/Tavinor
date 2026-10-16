"""Cane - text-first command router with a memory.

Hotkey (Ctrl+Alt+Space) -> input bar -> the sentence is matched in three
tiers, cheapest first:

  1. fixed-shape commands (open X, volume 40, timer 10 min) - regex, instant
  2. loose phrasing (skip this one, remind me at 5) - tiny local model
  3. questions - a cloud model answers into a panel; Copilot one click away

Tasks go to Cane's own chat (agent.py), done by the answer provider through
Cane's own commands; Claude Code is only ever an explicit button there. Also
keeps open items and reminders in notes.json, a personality in soul.md
(soul.py), focus sessions and blocks in focus.json (focus.py), and opens
when the cursor touches the top edge (hover.py).

Stdlib only. Needs ollama running locally.
"""
import ctypes
import ctypes.wintypes
from ctypes import wintypes
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import webbrowser
import winreg
import winsound
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk
from datetime import datetime, timedelta
from pathlib import Path

# The feature modules import cane. Run as a script this file is __main__,
# and without this alias each of them would load a second, separate copy -
# its own settings cache, app index and Bar - and nothing would line up.
sys.modules.setdefault("cane", sys.modules[__name__])
import agent  # noqa: E402  (after the alias, on purpose)
import focus  # noqa: E402
import hover  # noqa: E402
import soul  # noqa: E402

MODEL =os.environ.get("CANE_MODEL", "qwen3:1.7b")
# Runs on Ollama's servers through the same local API. Measured 1.5s and
# correct, where the 1.7B router gave a plausible-looking wrong git answer.
ANSWER_MODEL = os.environ.get("CANE_ANSWER_MODEL", "nemotron-3-super:cloud")
OLLAMA = "http://127.0.0.1:11434/api/chat"
ESCALATE_CWD = os.environ.get("CANE_CWD", os.path.expanduser("~"))
STORE = Path(__file__).parent / "notes.json"
SETTINGS = Path(__file__).parent / "settings.json"
CHECK_EVERY = 30  # seconds between reminder sweeps

MEDIA_KEYS = {
    "play": 0xB3, "pause": 0xB3, "next": 0xB0, "prev": 0xB1,
    "volup": 0xAF, "voldown": 0xAE, "mute": 0xAD,
}

EXTENDED, KEYUP = 0x0001, 0x0002


def send_media(vk):
    """Press and release a media key.

    Media keys are EXTENDED keys - without KEYEVENTF_EXTENDEDKEY the player
    never sees them. The caller must also drop focus first, or the keypress
    lands on our own borderless window instead of the player.
    """
    u32 = ctypes.windll.user32
    u32.keybd_event(vk, 0, EXTENDED, 0)
    u32.keybd_event(vk, 0, EXTENDED | KEYUP, 0)

# Every intent dispatch() handles must be here AND in SYSTEM. The schema
# below restricts the model to this list: brightness, lock and weather were
# missing, so "it's too bright" could only ever come back as a volume change.
INTENTS = ["media", "volume", "brightness", "timer", "lock", "screenshot",
           "theme", "google", "weather", "clock", "settings", "search_play",
           "launch", "files", "undo", "remember", "remind", "recall", "done",
           "ask", "escalate", "none"]

SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": INTENTS},
        "action": {"type": "string"},
        "arg": {"type": "string"},
        "when": {"type": "string"},
    },
    "required": ["intent"],
}

SYSTEM = """You convert a laptop command into JSON. Reply with JSON only.

media       transport control. action = play|pause|next|prev|volup|voldown|mute
volume      set an exact volume. arg = 0-100 as digits (max/full=100,
            half=50, min/zero=0)
brightness  screen brightness. arg = 0-100 as digits, OR action = up|down
timer       a countdown. arg = the number, action = s|m|h
lock        lock the screen
screenshot  take a screenshot
theme       Windows dark mode or light mode, only when those words appear.
            A darker or dimmer screen is brightness. arg = dark|light
google      search the web. arg = the search terms
weather     rain, temperature or the forecast. arg = the place, or "" for
            here. Other questions about places or space are "ask"
clock       the current time or date
settings    open Cane's own settings
files       organise, sort, tidy or move the user's files or folders.
            arg = the request as written
undo        put back the files Cane last moved. A question about undoing
            anything else (git, Word, a setting) is "ask"
search_play play a NAMED song. arg = song name only
launch      open an app. arg = app name only
remember    note something to track, no time. arg = the thing to track
remind      note something with a time. arg = the thing, when = the time phrase
recall      asking what is open/pending/left/tracked
done        finish an item. arg = which one (a number or a few words)
ask         wants information: a question, fact, explanation, advice, maths,
            a unit conversion or a definition. omit arg
none        ONLY a greeting, thanks, gibberish, or asking what you do
escalate    a real task that fits nothing above: writing code, multi-step
            work that is not about organising files. Must be work to DO, not a question. omit arg

A greeting, an acknowledgement, or gibberish is never a command -> "none".
But anything that asks for work to be done IS a command -> "escalate".

Copy the time phrase into "when" exactly as written. Never compute a date.

hey -> {"intent":"none"}
hi -> {"intent":"none"}
thanks -> {"intent":"none"}
ok -> {"intent":"none"}
asdfgh -> {"intent":"none"}
what can you do -> {"intent":"none"}
pause -> {"intent":"media","action":"pause"}
skip this one -> {"intent":"media","action":"next"}
turn it down -> {"intent":"media","action":"voldown"}
crank the sound to max -> {"intent":"volume","arg":"100"}
put the volume at half -> {"intent":"volume","arg":"50"}
my screen is blinding me -> {"intent":"brightness","action":"down"}
screen's too dim, i can't see -> {"intent":"brightness","action":"up"}
go dark theme -> {"intent":"theme","arg":"dark"}
back to light mode -> {"intent":"theme","arg":"light"}
lock the laptop -> {"intent":"lock"}
countdown 3 minutes -> {"intent":"timer","arg":"3","action":"m"}
capture my screen -> {"intent":"screenshot"}
search the web for pasta recipes -> {"intent":"google","arg":"pasta recipes"}
do i need an umbrella in mumbai today -> {"intent":"weather","arg":"mumbai"}
what day is it -> {"intent":"clock"}
dim the display a little -> {"intent":"brightness","action":"down"}
how big is jupiter -> {"intent":"ask"}
how do i revert a change in excel -> {"intent":"ask"}
open cane settings -> {"intent":"settings"}
play sapphire -> {"intent":"search_play","arg":"sapphire"}
put on some jazz -> {"intent":"search_play","arg":"jazz"}
open vs code -> {"intent":"launch","arg":"code"}
remember the auth discussion is waiting on Ravi -> {"intent":"remember","arg":"auth discussion waiting on Ravi"}
remind me in 20 minutes to check the build -> {"intent":"remind","arg":"check the build","when":"in 20 minutes"}
what is left -> {"intent":"recall"}
mark 2 done -> {"intent":"done","arg":"2"}
how do I undo a git commit -> {"intent":"ask"}
what is the capital of peru -> {"intent":"ask"}
why is my laptop slow -> {"intent":"ask"}
what's 15% of 2400 -> {"intent":"ask"}
move my pdfs into paisa -> {"intent":"files","arg":"move my pdfs into paisa"}
my desktop is cluttered, sort it -> {"intent":"files","arg":"my desktop is cluttered, sort it"}
put those files back -> {"intent":"undo"}
write me a python script that renames files by date -> {"intent":"escalate"}
"""


# ---------------------------------------------------------------- store

_LOCK = threading.Lock()  # the reminder sweep and the bar both write


def load():
    """Read the store, dropping anything malformed.

    A hand-edited or truncated notes.json must never take the app down, so
    every row is normalised here and consumers can trust the shape.
    """
    try:
        raw = json.loads(STORE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(raw, list):
        return []
    items = []
    for r in raw:
        if not isinstance(r, dict) or not r.get("text"):
            continue
        items.append({
            "text": str(r["text"]),
            "due": r.get("due") if isinstance(r.get("due"), str) else None,
            "done": bool(r.get("done")),
            "fired": bool(r.get("fired")),
            "created": str(r.get("created", "")),
        })
    return items


def save(items):
    STORE.write_text(json.dumps(items, indent=2), encoding="utf-8")


def add_note(text, due=None):
    with _LOCK:
        items = load()
        items.append({
            "text": text,
            "due": due.isoformat(timespec="minutes") if due else None,
            "done": False,
            "fired": False,
            "created": datetime.now().isoformat(timespec="minutes"),
        })
        save(items)
        return items[-1]


def open_items():
    return [i for i in load() if not i["done"]]


def mark_done(ref):
    """ref is a 1-based index from the recall list, or words to match.

    An empty ref matches NOTHING. Substring matching on "" is true for every
    row, which silently closed whatever happened to be first in the list.
    """
    ref = (ref or "").strip()
    if not ref:
        return None
    with _LOCK:
        items = load()
        live = [i for i in items if not i["done"]]
        hit = None
        if ref.isdigit():
            n = int(ref) - 1
            if 0 <= n < len(live):
                hit = live[n]
        else:
            needle = ref.lower()
            hit = next((i for i in live if needle in i["text"].lower()), None)
        if hit is None:
            return None
        hit["done"] = True
        save(items)
        return hit


# ---------------------------------------------------------------- time

UNITS = {"min": 1, "hour": 60, "day": 1440, "week": 10080}


def parse_when(phrase, now=None):
    """'in 20 minutes' / 'tomorrow at 3pm' / 'at 15:30' -> datetime, else None.

    Deliberately a regex and not the model: small models fumble date
    arithmetic, and a wrong reminder time is worse than no reminder.
    """
    if not phrase:
        return None
    now = now or datetime.now()
    p = phrase.lower().strip()

    # Nonsense times return None, which saves an untimed item instead of
    # crashing the command. "at 25pm" used to raise straight out of dispatch.
    m = re.search(r"in\s+(\d+)\s*(min|hour|day|week)", p)
    if m:
        minutes = int(m.group(1)) * UNITS[m.group(2)]
        if minutes > 525600 * 10:  # beyond ten years is a typo, not a plan
            return None
        return now + timedelta(minutes=minutes)

    # A clock time, optionally on tomorrow.
    m = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", p)
    if m and ("at" in p or "tomorrow" in p or "tonight" in p):
        base = now + timedelta(days=1) if "tomorrow" in p else now
        hour, minute = int(m.group(1)), int(m.group(2) or 0)
        if m.group(3) == "pm" and hour < 12:
            hour += 12
        elif m.group(3) == "am" and hour == 12:
            hour = 0
        elif not m.group(3) and hour < 8:
            hour += 12  # "at 3" means afternoon, not 3am
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            return None
        due = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
        return due + timedelta(days=1) if due <= now else due

    if "tonight" in p:
        return now.replace(hour=20, minute=0, second=0, microsecond=0)
    if "tomorrow" in p:
        return (now + timedelta(days=1)).replace(
            hour=9, minute=0, second=0, microsecond=0)
    return None


def due_now(now=None):
    """Un-fired reminders whose time has passed. Marks them fired."""
    now = now or datetime.now()
    with _LOCK:
        items, ready, dirty = load(), [], False
        for i in items:
            if i["done"] or i["fired"] or not i["due"]:
                continue
            try:
                due = datetime.fromisoformat(i["due"])
            except ValueError:
                i["fired"] = True  # unparseable: skip it, don't retry forever
                dirty = True
                continue
            if due <= now:
                i["fired"] = True
                dirty = True
                ready.append(i)
        if dirty:
            save(items)
        return ready


# ---------------------------------------------------------------- settings

def _settings():
    try:
        s = json.loads(SETTINGS.read_text(encoding="utf-8"))
        return s if isinstance(s, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def setting(key, default=None):
    return _settings().get(key, default)


def set_setting(key, value):
    s = _settings()
    s[key] = value
    SETTINGS.write_text(json.dumps(s, indent=2), encoding="utf-8")


# Switchable features: (key, label, intents it governs). All on by default.
# Saved reminders still fire when "reminders" is off - the switch covers the
# commands, and silently dropping a reminder you already set would be worse.
FEATURES = [
    ("apps", "Apps & sites", ("launch",)),
    ("media", "Volume & media", ("volume", "media")),
    ("brightness", "Brightness", ("brightness",)),
    ("timers", "Timers", ("timer",)),
    ("lock", "Lock screen", ("lock",)),
    ("screenshot", "Screenshot", ("screenshot",)),
    ("theme", "Dark mode", ("theme",)),
    ("search", "Web search", ("google",)),
    ("weather", "Weather", ("weather",)),
    ("music", "Play songs", ("search_play",)),
    ("reminders", "Reminders", ("remember", "remind", "recall", "done")),
    ("files", "File tasks", ("files", "undo")),
    ("focus", "Focus & blocking", ("focus", "block")),
]


def feature_on(key):
    return bool(setting("feature:" + key, True))


def installed_models():
    """Chat-capable ollama models, local and cloud, for the answer picker."""
    try:
        with urllib.request.urlopen(OLLAMA.replace("/api/chat", "/api/tags"),
                                    timeout=3) as r:
            names = [m["name"] for m in json.load(r).get("models", [])]
    except (urllib.error.URLError, OSError, TimeoutError, ValueError, KeyError):
        names = []
    return [n for n in names if "embed" not in n]   # embedders can't chat


# ---------------------------------------------------------------- system

class GUID(ctypes.Structure):
    _fields_ = [("d1", ctypes.c_ulong), ("d2", ctypes.c_ushort),
                ("d3", ctypes.c_ushort), ("d4", ctypes.c_ubyte * 8)]


def _guid(s):
    g = GUID()
    ctypes.oledll.ole32.CLSIDFromString(s, ctypes.byref(g))
    return g


def _vcall(obj, index, argtypes, *args, res=ctypes.HRESULT):
    """Call slot `index` of a COM object's vtable. HRESULT failures raise."""
    vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    return ctypes.WINFUNCTYPE(res, ctypes.c_void_p, *argtypes)(vtbl[index])(obj, *args)


def set_volume(level=None, delta=None):
    """Master volume 0-100: set it, nudge it, or just read it.

    Media keys only step 2% and cannot set a level, so "volume 50" needs the
    endpoint volume COM interface. Returns the resulting level.
    """
    ole32 = ctypes.oledll.ole32
    ole32.CoInitialize(None)          # per thread; a repeat call is harmless
    held = []
    try:
        enum = ctypes.c_void_p()
        ole32.CoCreateInstance(
            ctypes.byref(_guid("{BCDE0395-E52F-467C-8E3D-C4579291692E}")),
            None, 23,
            ctypes.byref(_guid("{A95664D2-9614-4F35-A746-DE8DB63617E6}")),
            ctypes.byref(enum))
        held.append(enum)
        dev = ctypes.c_void_p()       # GetDefaultAudioEndpoint(eRender, eConsole)
        _vcall(enum, 4, (ctypes.c_int, ctypes.c_int,
                         ctypes.POINTER(ctypes.c_void_p)), 0, 0, ctypes.byref(dev))
        held.append(dev)
        vol = ctypes.c_void_p()       # Activate(IAudioEndpointVolume)
        _vcall(dev, 3, (ctypes.POINTER(GUID), ctypes.c_uint, ctypes.c_void_p,
                        ctypes.POINTER(ctypes.c_void_p)),
               ctypes.byref(_guid("{5CDF2C82-841E-4546-9722-0CF74078229A}")),
               23, None, ctypes.byref(vol))
        held.append(vol)
        cur = ctypes.c_float()
        _vcall(vol, 9, (ctypes.POINTER(ctypes.c_float),), ctypes.byref(cur))
        if delta is not None:
            level = cur.value * 100 + delta
        if level is None:
            return round(cur.value * 100)
        level = max(0, min(100, round(level)))
        _vcall(vol, 7, (ctypes.c_float, ctypes.c_void_p), level / 100, None)
        if level:                     # "volume 40" while muted should be heard
            _vcall(vol, 14, (wintypes.BOOL, ctypes.c_void_p), False, None)
        return level
    finally:
        for obj in reversed(held):
            _vcall(obj, 2, (), res=ctypes.c_ulong)          # Release


def brightness(level=None, delta=0):
    """Set the built-in panel's brightness. Returns the new level, or None.

    WMI is the only route without a dependency, and external monitors don't
    expose it - those return None rather than pretending it worked.
    """
    target = "%d" % int(level) if level is not None else "$c + (%d)" % int(delta)
    ps = ("$c = (Get-CimInstance -Namespace root/WMI -ClassName "
          "WmiMonitorBrightness -ErrorAction Stop).CurrentBrightness; "
          "$n = [Math]::Max(0, [Math]::Min(100, %s)); "
          "Get-CimInstance -Namespace root/WMI -ClassName "
          "WmiMonitorBrightnessMethods -ErrorAction Stop | Invoke-CimMethod "
          "-MethodName WmiSetBrightness -Arguments "
          "@{Timeout=[uint32]1; Brightness=[byte]$n} | Out-Null; $n") % target
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                             capture_output=True, text=True, timeout=20,
                             creationflags=subprocess.CREATE_NO_WINDOW)
        return int(out.stdout.strip().splitlines()[-1])
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return None


def screenshot():
    """Win+PrtScn - Windows itself saves the screen to Pictures\\Screenshots."""
    u32 = ctypes.windll.user32
    u32.keybd_event(0x5B, 0, 0, 0)
    u32.keybd_event(0x2C, 0, 0, 0)
    u32.keybd_event(0x2C, 0, KEYUP, 0)
    u32.keybd_event(0x5B, 0, KEYUP, 0)


def set_theme(dark):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                        r"Software\Microsoft\Windows\CurrentVersion\Themes"
                        r"\Personalize", 0, winreg.KEY_SET_VALUE) as key:
        for name in ("AppsUseLightTheme", "SystemUsesLightTheme"):
            winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, 0 if dark else 1)
    # Running apps only repaint once told the colour set changed.
    ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0,
                                             "ImmersiveColorSet", 2, 3000, None)


# ---------------------------------------------------------------- open

_APPS = {}   # lowercase Start-menu name -> (display name, AppID)


def index_apps():
    """Every app on the Start menu, the way Windows itself lists them.

    Launching used to look on PATH, which holds a handful of command-line
    tools: WhatsApp, Settings and Brave were installed and unlaunchable.
    Get-StartApps covers desktop and Store apps alike. ~5s, so it runs once
    in the background at launch.
    """
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
             "Get-StartApps | ConvertTo-Json -Compress"],
            capture_output=True, encoding="utf-8", timeout=60,
            creationflags=subprocess.CREATE_NO_WINDOW).stdout
        rows = json.loads(out)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return
    for r in [rows] if isinstance(rows, dict) else rows:
        if isinstance(r, dict) and r.get("Name") and r.get("AppID"):
            _APPS.setdefault(r["Name"].lower(), (r["Name"], r["AppID"]))


ALIASES = {"code": "visual studio code", "vscode": "visual studio code",
           "vs code": "visual studio code", "explorer": "file explorer",
           "files": "file explorer", "cmd": "command prompt"}

# Settings first: two apps are literally named "Settings", one of them a
# notification tool, so the index alone would pick the wrong one.
PAGES = {"settings": "ms-settings:", "bluetooth": "ms-settings:bluetooth",
         "wifi": "ms-settings:network-wifi", "wi-fi": "ms-settings:network-wifi",
         "network": "ms-settings:network", "display": "ms-settings:display",
         "sound": "ms-settings:sound", "battery": "ms-settings:batterysaver",
         "update": "ms-settings:windowsupdate",
         "windows update": "ms-settings:windowsupdate"}

# shell: names, not home-relative paths, so redirected folders still work.
FOLDERS = {"downloads": "Downloads", "documents": "Personal",
           "desktop": "Desktop", "pictures": "My Pictures",
           "photos": "My Pictures", "music": "My Music", "videos": "My Video",
           "screenshots": "Screenshots"}

SITES = {"youtube": "https://www.youtube.com",
         "youtube music": "https://music.youtube.com",
         "gmail": "https://mail.google.com", "google": "https://www.google.com",
         "browser": "https://www.google.com",   # the default browser, whichever
         "github": "https://github.com", "drive": "https://drive.google.com",
         "maps": "https://maps.google.com",
         "calendar": "https://calendar.google.com",
         "chatgpt": "https://chatgpt.com", "linkedin": "https://www.linkedin.com"}


def find_app(name):
    """Best Start-menu match: exact, then prefix, then whole word, then typo."""
    if name in _APPS:
        return _APPS[name]
    for pick in ([k for k in _APPS if k.startswith(name)],
                 [k for k in _APPS if name in k.split()]):
        if pick:
            return _APPS[min(pick, key=len)]
    close = difflib.get_close_matches(name, list(_APPS), n=1, cutoff=0.8)
    return _APPS[close[0]] if close else None


def resolve(name):
    """(label, target) for os.startfile, or None if nothing matches."""
    n = re.sub(r"^(the|my) ", "", name.lower().strip())
    page = PAGES.get(re.sub(r" settings$", "", n))
    if page:
        return n, page
    folder = FOLDERS.get(re.sub(r" folder$", "", n))
    if folder:
        return n, "shell:" + folder
    app = find_app(ALIASES.get(n, n)) if len(n) > 1 else None
    if app:
        return app[0], "shell:AppsFolder\\" + app[1]
    if n in SITES:
        return n, SITES[n]
    if re.fullmatch(r"[\w-]+(\.[\w-]+)+(/\S*)?", n):
        return n, "https://" + n
    exe = shutil.which(n)               # still honour anything on PATH
    return (n, exe) if exe else None


# ---------------------------------------------------------------- quick

MEDIA_WORDS = re.compile(
    r"\b(play|pause|resume|stop|skip|next|prev|previous|back|volume|louder|"
    r"quieter|softer|up|down|mute|unmute|song|track|music|sound|audio|loud|"
    r"hear|quiet)\b")

# A model-chosen action with side effects must be backed by the user's own
# words. A small model forced to pick lands on the nearest verb, and locking
# the screen or flipping the theme on a misread is worse than doing nothing.
MODEL_GUARDS = {
    "lock": re.compile(r"\block\b"),                     # not "unlock"/"clock"
    "theme": re.compile(r"\b(dark|light)\b"),
    "screenshot": re.compile(r"screen|shot|capture|snip"),
    "brightness": re.compile(r"bright|dim|screen|dark|blind|glare"),
    "volume": re.compile(r"volume|sound|loud|quiet|audio|max|mute"),
    "undo": re.compile(r"undo|back|revert|restore"),
}
SIDE_EFFECTS = set(MODEL_GUARDS) | {"media"}
# Question openers. Not "can"/"could": "can you lock my laptop" is a request.
QUESTION = re.compile(r"(how|what|why|when|where|which|who|should|is|are|"
                      r"does|did|do i|do you)\b")

UNIT_SECS = {"s": 1, "m": 60, "h": 3600}

# Words a typo may be snapped onto. Deliberately NOT here: "lock" (a typo must
# never lock the screen, and "clock" is one letter away), "launch" ("lunch"),
# "copilot" ("pilot"), "search" ("research").
# Only words that appear in a quick() pattern - snapping onto anything else
# could never produce a match.
VOCAB = ["brightness", "brighter", "dimmer", "volume", "screenshot", "screen",
         "weather", "temperature", "forecast", "settings", "preferences",
         "timer", "minutes", "minute", "seconds", "second", "hours", "percent",
         "google", "pause", "resume", "previous", "unmute", "music", "track"]
# Real words one letter from a keyword. Each was a false positive: "the build
# timed out after 5 minutes" became a 5 minute timer.
KEEP = {"time", "timed", "times", "whether", "research", "cause",
        "leather", "feather", "bright"}   # "how bright..." != "brighter"


def fix_typos(text):
    """Snap near-miss words onto command words: "brigtness 50" -> brightness.

    Only ever tried after an exact match fails, and parse() only accepts the
    result if it fully matches a fixed pattern - so a correction can never
    turn an ordinary sentence into an action on its own.
    """
    def snap(m):
        w = m.group(0)
        if len(w) < 4 or w in VOCAB or w in KEEP:
            return w
        hit = difflib.get_close_matches(w, VOCAB, n=1, cutoff=0.8)
        return hit[0] if hit else w
    return re.sub(r"[a-z]+", snap, text.lower())


LEVEL_WORDS = {"max": 100, "maximum": 100, "full": 100, "highest": 100,
               "half": 50, "min": 0, "minimum": 0, "lowest": 0, "zero": 0,
               "up": 100, "down": 0}     # up/down only after "all the way"

MEDIA_ALIASES = {"pause": "pause", "stop": "pause", "play": "play",
                 "resume": "play", "next": "next", "skip": "next",
                 "previous": "prev", "prev": "prev", "mute": "mute",
                 "unmute": "mute"}


def quick(text):
    """Fixed-shape commands, matched before the model.

    Instant, work offline, and can't be misrouted: through the model, "turn
    on dark mode" pressed volume-down and "volume 50" nudged it up 2%.
    A question never gets a side-effect action: several patterns search
    inside the sentence, so "how do I take a screenshot on a mac" took one.
    """
    hit = _quick(text)
    if (hit and hit["intent"] in SIDE_EFFECTS
            and QUESTION.match(text.lower().strip())):
        return None                   # the model / answer path takes it
    return hit


def _quick(text):
    t = re.sub(r"\s+", " ", text.lower()).strip(" ?.!")
    t = re.sub(r"^(please |can you |could you |cane,? )+", "", t)
    # A reminder ABOUT a command is not the command: "remind me to take a
    # screenshot at 5" must save a note, not take one now.
    if re.match(r"(remind|remember|note|don't forget|dont forget)\b", t):
        return None

    # Cane's own settings. "open settings" alone still means Windows Settings,
    # like every other "open X".
    if re.fullmatch(r"(?:(?:open|show) )?cane(?:'s)? (?:settings|preferences|"
                    r"options)|settings|preferences|options|show settings", t):
        return {"intent": "settings"}
    if re.fullmatch(r"undo(?: that| it| the (?:last )?(?:move|moves|changes?))?", t):
        return {"intent": "undo"}
    if re.fullmatch(r"(?:open |show )?(?:the |cane )?chat|let'?s chat|chat with (?:me|cane)", t):
        return {"intent": "chat"}
    if re.fullmatch(r"(?:open |edit |show )?(?:cane'?s |my |your )?(?:personality|soul)"
                    r"(?: settings)?", t):
        return {"intent": "soul"}
    hit = focus.quick(t)
    if hit:
        return hit

    # Bare transport words. Instant, and lets "pasue" be corrected to pause.
    m = re.fullmatch(r"(pause|stop|play|resume|next|skip|previous|prev|mute|"
                     r"unmute)(?: the)?(?: music| song| track| video)?", t)
    if m:
        return {"intent": "media", "action": MEDIA_ALIASES[m.group(1)]}
    # Track moves phrased as sentences. "play next" reached the cloud model
    # as a question and came back asking what "next" meant.
    if re.fullmatch(r"(?:play |go to |skip to )(?:the )?next(?: one| song| track|"
                    r" video)?|skip (?:this|that)(?: one| song| track)?|"
                    r"next (?:one|song|track|video) please", t):
        return {"intent": "media", "action": "next"}
    if re.fullmatch(r"(?:play |go to )(?:the )?(?:previous|last)(?: one| song|"
                    r" track| video)?|go back (?:a |one )?(?:song|track)|"
                    r"(?:previous|last) (?:song|track) please", t):
        return {"intent": "media", "action": "prev"}

    if re.search(r"\btimer\b", t):
        m = re.search(r"(\d+) ?(s|secs?|seconds?|m|mins?|minutes?|h|hrs?|hours?)\b", t)
        if m:
            return {"intent": "timer", "arg": m.group(1), "action": m.group(2)[0]}
    m = re.fullmatch(r"(?:open|launch) (.+)", t)
    if m:
        return {"intent": "launch", "arg": m.group(1)}
    m = re.fullmatch(r"(?:set |change |put |make |turn )?(?:the )?(?:screen )?"
                     r"(volume|brightness)(?: level)?(?: to| at)? (\d{1,3}) ?"
                     r"(?:%|percent)?", t)
    if m:
        return {"intent": m.group(1), "arg": m.group(2)}
    # Level words, either order: "volume max", "max brightness", "set the
    # volume to half". Through the model, "Volume max" turned it DOWN.
    # Not up/down: "volume up" is a relative nudge, not a jump to 100.
    words = "|".join(w for w in LEVEL_WORDS if w not in ("up", "down"))
    m = (re.fullmatch(r"(?:set |change |put |make |turn )?(?:the )?(?:screen )?"
                      r"(?P<what>volume|brightness)(?: level)?(?: to| at)? "
                      r"(?P<lvl>%s)" % words, t)
         or re.fullmatch(r"(?:set |turn )?(?:the )?(?P<lvl>%s) (?:screen )?"
                         r"(?P<what>volume|brightness)" % words, t)
         or re.fullmatch(r"(?:turn )?(?:the )?(?:screen )?(?P<what>volume|"
                         r"brightness) all the way (?P<lvl>up|down)", t))
    if m:
        return {"intent": m["what"], "arg": str(LEVEL_WORDS[m["lvl"]])}
    if re.search(r"\b(brighter|(increase|raise|turn up) (the )?brightness|"
                 r"brightness up)\b", t):
        return {"intent": "brightness", "action": "up"}
    if re.search(r"\b(dimmer|dim (the )?screen|(decrease|lower|reduce|turn down) "
                 r"(the )?brightness|brightness down)\b", t):
        return {"intent": "brightness", "action": "down"}
    if re.fullmatch(r"lock(?: my| the)?(?: laptop| pc| computer| screen)?", t):
        return {"intent": "lock"}
    if re.search(r"\bscreen ?shot\b", t):
        return {"intent": "screenshot"}
    m = re.search(r"\b(dark|light) (?:mode|theme)\b", t)
    if m:
        dark = m.group(1) == "dark"
        if re.search(r"\b(off|disable|stop)\b", t):   # "turn off dark mode"
            dark = not dark
        return {"intent": "theme", "arg": "dark" if dark else "light"}
    m = re.fullmatch(r"(?:google|search(?: for)?|look up) (.+)", t)
    if m:
        return {"intent": "google", "arg": m.group(1)}
    m = re.fullmatch(r"(?:ask )?copilot,? (.+)", t)
    if m:
        return {"intent": "copilot", "arg": m.group(1)}
    # Before "ask": a language model has no live data and would guess.
    # Anchored: "remind me to check the weather at 5" is a reminder.
    m = re.match(r"(?:(?:what|how)(?:'s| is) )?(?:the )?(?:weather|temperature|"
                 r"forecast)\b(?:.*?\bin ([a-z][a-z .'-]+))?", t)
    if m:
        return {"intent": "weather", "arg": (m.group(1) or "").strip()}
    if re.fullmatch(r"(?:what(?:'s| is) )?(?:the )?(?:time|date)(?: is it)?"
                    r"(?: now| today)?|what time is it|what day is (?:it|today)", t):
        return {"intent": "clock"}
    return None


# ---------------------------------------------------------------- model

def ollama_up(timeout=0.6):
    # Short timeout on purpose: this is polled while the server boots, and a
    # 3s timeout per attempt turned a 25s budget into a 93s wait.
    try:
        urllib.request.urlopen(OLLAMA.replace("/api/chat", "/api/tags"),
                               timeout=timeout)
        return True
    except (urllib.error.URLError, OSError, TimeoutError):
        return False


def start_ollama(wait=30):
    """Start the ollama server and wait for it to answer.

    Without this a stopped server made every command fall back to escalation,
    so the app looked alive while doing the wrong thing for every input.
    """
    if ollama_up():
        return True
    exe = shutil.which("ollama")
    if not exe:
        return False
    try:
        subprocess.Popen([exe, "serve"],
                         creationflags=subprocess.CREATE_NO_WINDOW,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        return False
    for _ in range(wait):
        if ollama_up():
            return True
        time.sleep(0.4)
    return False


def warmup():
    """Start the server and load the router model before it is needed.

    Runs at launch and again each time the bar opens: after 8h idle the model
    is unloaded and the first command took 19s. Loading while you type hides
    most of that. An empty generate only loads - no inference is spent.
    """
    if not start_ollama():
        return
    body = json.dumps({"model": MODEL, "keep_alive": "8h"}).encode()
    try:
        urllib.request.urlopen(urllib.request.Request(
            OLLAMA.replace("/api/chat", "/api/generate"), body,
            {"Content-Type": "application/json"}), timeout=90).read()
    except (urllib.error.URLError, OSError, TimeoutError):
        pass


def _ask(text, timeout):
    """One request to the model. Raises URLError if the server is unreachable."""
    body = json.dumps({
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": text},
        ],
        "stream": False,
        "keep_alive": "8h",
        "think": False,  # qwen3 is a hybrid reasoner; thinking blows the budget
        "format": SCHEMA,
        "options": {"temperature": 0},
    }).encode()
    req = urllib.request.Request(OLLAMA, body, {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:   # URLError escapes
        content = json.load(r)["message"]["content"]
    try:
        intent = json.loads(content)
    except json.JSONDecodeError:
        return {"intent": "escalate", "arg": text}
    if intent.get("intent") in INTENTS:
        kind = intent["intent"]
        if kind in ("escalate", "ask", "files"):
            intent["arg"] = text  # reuse the original, never the model's copy
        # Model-written values are loose: "100%", "max", "minutes", "Dark".
        # Dispatch wants digits, s|m|h and lowercase dark|light.
        if kind in ("volume", "brightness", "timer"):
            a = str(intent.get("arg") or "").strip().lower()
            if kind != "timer" and a in LEVEL_WORDS and a not in ("up", "down"):
                a = str(LEVEL_WORDS[a])
            intent["arg"] = re.sub(r"\D", "", a)
        # A level with no number is really a nudge: "turn the volume down"
        # came back as volume "" from the cloud model, which dispatch rejects.
        low = text.lower()
        # "too X" first: "too dim" wants it brighter, "dim the screen" darker.
        nudge = ("up" if re.search(r"too (dim|dark|quiet|soft)|can'?t (hear|see)"
                                   r"|cannot (hear|see)", low) else
                 "down" if re.search(r"too (loud|bright)|\b(down|lower|quieter|"
                                     r"softer|dim|darker)\b", low) else
                 "up" if re.search(r"\b(up|raise|louder|brighter)\b", low)
                 else "")
        if kind == "volume" and not intent["arg"]:
            return ({"intent": "media", "action": "vol" + nudge} if nudge
                    else {"intent": "none"})
        if (kind == "brightness" and not intent["arg"]
                and str(intent.get("action") or "").lower() not in ("up", "down")):
            return ({"intent": "brightness", "action": nudge} if nudge
                    else {"intent": "none"})
        if kind == "timer":
            intent["action"] = (str(intent.get("action") or "m").strip().lower()
                                or "m")[0]
        if kind == "brightness" and intent.get("action"):
            intent["action"] = str(intent["action"]).lower()
        if kind == "theme":
            intent["arg"] = str(intent.get("arg") or "").strip().lower()
        return intent
    return {"intent": "escalate", "arg": text}


def parse(text, timeout=20):
    """Intent for a sentence: fixed shapes first, then the model.

    A dead server returns "offline" rather than "escalate". Escalating on a
    failed dependency turned every keystroke into a new terminal window while
    making the app look like it was working.
    """
    # Exact first; a typo-corrected version only if it fully hits a pattern.
    # Otherwise the model gets the original words - a misspelt keyword used
    # to reach it and be guessed at: "brigtness up" turned the volume up.
    hit = quick(text) or quick(fix_typos(text))
    if hit:
        return hit
    for attempt in (1, 2):
        try:
            intent = _ask(text, timeout)
        except (urllib.error.URLError, OSError, TimeoutError, KeyError):
            if attempt == 1 and start_ollama():
                continue
            return {"intent": "offline"}
        # A small model forced to choose picks the nearest verb. With no media
        # word in the sentence, a media intent is a misfire, not a command.
        low = text.lower()
        # A question never triggers a side effect: "how do I undo a git
        # commit" came back as undo, which would have put files back.
        if (intent.get("intent") in SIDE_EFFECTS
                and QUESTION.match(low.strip())):
            return {"intent": "ask", "arg": text}
        if intent.get("intent") == "media" and not MEDIA_WORDS.search(low):
            return {"intent": "none"}
        guard = MODEL_GUARDS.get(intent.get("intent"))
        if guard and not guard.search(low):
            return {"intent": "none"}
        return intent


# ---------------------------------------------------------------- answers

# Everyone but Anthropic speaks the OpenAI chat format, so one code path
# covers five providers. Anthropic goes through its own SDK.
PROVIDERS = {                       # id: (label, OpenAI-style base URL)
    "ollama": ("Ollama", None),
    "anthropic": ("Anthropic (Claude)", None),
    "openai": ("OpenAI", "https://api.openai.com/v1"),
    "gemini": ("Google Gemini",
               "https://generativelanguage.googleapis.com/v1beta/openai"),
    "groq": ("Groq", "https://api.groq.com/openai/v1"),
    "openrouter": ("OpenRouter", "https://openrouter.ai/api/v1"),
    "nvidia": ("NVIDIA", "https://integrate.api.nvidia.com/v1"),
}
# Only Claude has a default: its IDs are stable and documented. The others
# change monthly, so their model comes from the provider's live list.
DEFAULT_MODEL = {"ollama": ANSWER_MODEL, "anthropic": "claude-opus-5"}


# -- keys live in Windows Credential Manager, never in settings.json

class CREDENTIAL(ctypes.Structure):
    _fields_ = [("Flags", wintypes.DWORD), ("Type", wintypes.DWORD),
                ("TargetName", wintypes.LPWSTR), ("Comment", wintypes.LPWSTR),
                ("LastWritten", wintypes.FILETIME),
                ("CredentialBlobSize", wintypes.DWORD),
                ("CredentialBlob", ctypes.POINTER(ctypes.c_char)),
                ("Persist", wintypes.DWORD), ("AttributeCount", wintypes.DWORD),
                ("Attributes", ctypes.c_void_p), ("TargetAlias", wintypes.LPWSTR),
                ("UserName", wintypes.LPWSTR)]


_adv = ctypes.windll.advapi32
_adv.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                           ctypes.POINTER(ctypes.POINTER(CREDENTIAL))]
_adv.CredWriteW.argtypes = [ctypes.POINTER(CREDENTIAL), wintypes.DWORD]
_adv.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
CRED_GENERIC, CRED_PERSIST_LOCAL = 1, 2
KEY_PREFIX = "Cane/"


def key_set(provider, key):
    """Store a key, or remove it when key is empty."""
    target = KEY_PREFIX + provider
    if not key:
        _adv.CredDeleteW(target, CRED_GENERIC, 0)
        return
    blob = key.encode("utf-16-le")
    buf = ctypes.create_string_buffer(blob, len(blob))   # alive until written
    c = CREDENTIAL(Type=CRED_GENERIC, TargetName=target, UserName="Cane",
                   Persist=CRED_PERSIST_LOCAL, CredentialBlobSize=len(blob),
                   CredentialBlob=ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    if not _adv.CredWriteW(ctypes.byref(c), 0):
        raise OSError("Credential Manager refused the key")


def key_get(provider):
    p = ctypes.POINTER(CREDENTIAL)()
    if not _adv.CredReadW(KEY_PREFIX + provider, CRED_GENERIC, 0,
                          ctypes.byref(p)):
        return None
    try:
        c = p.contents
        return ctypes.string_at(c.CredentialBlob,
                                c.CredentialBlobSize).decode("utf-16-le")
    finally:
        _adv.CredFree(p)


def provider():
    prov = setting("provider", "ollama")
    return prov if prov in PROVIDERS else "ollama"


def current_model(prov=None):
    prov = prov or provider()
    if prov == "ollama":
        return setting("answer_model") or ANSWER_MODEL
    return setting("model:" + prov) or DEFAULT_MODEL.get(prov) or ""


def _post(url, payload, headers, timeout, name):
    """POST JSON, return the parsed reply. Every failure becomes a readable
    RuntimeError - a panel saying "key rejected" beats one saying nothing."""
    req = urllib.request.Request(url, json.dumps(payload).encode(),
                                 {"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            data = json.load(e)
        except ValueError:
            data = {}
        if isinstance(data, list) and data:       # Gemini wraps errors in a list
            data = data[0]
        err = data.get("error") if isinstance(data, dict) else None
        msg = err.get("message") if isinstance(err, dict) else err
        if e.code in (401, 403):
            raise RuntimeError("%s rejected the key" % name) from None
        if e.code == 429:
            raise RuntimeError("%s rate limit or quota reached" % name) from None
        if e.code == 404 and not msg:   # NVIDIA: unknown model, bare 404
            raise RuntimeError("%s has no such model - pick one in settings"
                               % name) from None
        raise RuntimeError(str(msg or "%s HTTP %d" % (name, e.code))[:110]) \
            from None
    except (urllib.error.URLError, OSError, TimeoutError):
        raise RuntimeError("could not reach " + name) from None


def _ask_ollama(system, turns, timeout):
    data = _post(OLLAMA, {"model": current_model("ollama"), "stream": False,
                          "think": False,
                          "messages": [{"role": "system", "content": system}]
                          + turns}, {}, timeout, "ollama")
    if not isinstance(data, dict):
        raise RuntimeError("unexpected reply")
    if data.get("error"):
        raise RuntimeError(str(data["error"])[:110])
    return str((data.get("message") or {}).get("content") or "")


def _ask_openai_style(prov, key, system, turns, timeout):
    label, base = PROVIDERS[prov]
    model = current_model(prov)
    if not model:
        raise RuntimeError("pick a %s model in settings" % label)
    data = _post(base + "/chat/completions",
                 {"model": model,
                  "messages": [{"role": "system", "content": system}] + turns},
                 {"Authorization": "Bearer " + key}, timeout, label)
    try:
        return str(data["choices"][0]["message"]["content"] or "")
    except (KeyError, IndexError, TypeError):
        raise RuntimeError("unexpected reply from " + label) from None


def _ask_claude(key, system, turns, timeout, effort="low"):
    try:
        import anthropic
    except ImportError:
        raise RuntimeError("Anthropic SDK missing - pip install anthropic") \
            from None
    model = current_model("anthropic")
    extra = {}
    # Opus 5 / Fable 5.1 safety classifiers can decline; "default" fallbacks
    # rerun a declined request server-side instead of returning nothing.
    if model in ("claude-opus-5", "claude-fable-5-1"):
        extra = {"betas": ["server-side-fallback-2026-07-01"],
                 "fallbacks": "default"}
    client = anthropic.Anthropic(api_key=key, timeout=timeout, max_retries=1)
    try:
        r = client.beta.messages.create(
            model=model, max_tokens=16000, system=system, messages=turns,
            output_config={"effort": effort},    # low for bar answers
            **extra)
    except anthropic.AuthenticationError:
        raise RuntimeError("Anthropic rejected the key") from None
    except anthropic.RateLimitError:
        raise RuntimeError("Anthropic rate limit or quota reached") from None
    except anthropic.NotFoundError:
        raise RuntimeError("Anthropic has no model %r" % model) from None
    except anthropic.APIConnectionError:
        raise RuntimeError("could not reach Anthropic") from None
    except anthropic.APIStatusError as e:
        raise RuntimeError(str(e.message)[:110]) from None
    if r.stop_reason == "refusal":
        raise RuntimeError("Claude declined to answer this one")
    return "".join(b.text for b in r.content if b.type == "text")


def provider_models(prov):
    """Models the provider offers right now, for the settings picker."""
    if prov == "ollama":
        return installed_models()
    key = key_get(prov)
    if not key:
        return []
    try:
        if prov == "anthropic":
            import anthropic
            return [m.id for m in
                    anthropic.Anthropic(api_key=key, timeout=10).models.list()]
        req = urllib.request.Request(PROVIDERS[prov][1] + "/models",
                                     headers={"Authorization": "Bearer " + key})
        with urllib.request.urlopen(req, timeout=10) as r:
            rows = json.load(r).get("data", [])
        # Gemini lists "models/gemini-x"; its chat endpoint wants "gemini-x".
        return sorted(str(m["id"]).removeprefix("models/") for m in rows)
    except Exception:
        return []            # the picker stays empty; Test says why


def complete(system, turns, timeout, effort="low"):
    """(text, note) from the chosen provider. If a keyed provider fails,
    ollama takes over and `note` says why, so answers and file plans share
    one fallback instead of two that drift apart."""
    prov = provider()
    if prov == "ollama":
        return _ask_ollama(system, turns, timeout), ""
    try:
        key = key_get(prov)
        if not key:
            raise RuntimeError("no %s key saved" % PROVIDERS[prov][0])
        text = (_ask_claude(key, system, turns, timeout, effort)
                if prov == "anthropic" else
                _ask_openai_style(prov, key, system, turns, timeout))
        return text, ""
    except RuntimeError as e:
        note = "(%s - answered by %s)\n" % (e, current_model("ollama"))
        return _ask_ollama(system, turns, timeout), note


def answer(question, history=None, timeout=45):
    """A short answer from the chosen provider, in the thread so far.

    `history` is [(question, answer)] from this bar session, so "and how do I
    undo that?" resolves. If a keyed provider fails, ollama answers instead
    and the reply says so. Raises RuntimeError with a readable reason when
    nothing could answer.
    """
    system = ("Answer in at most four short sentences. Put commands and code "
              "in backticks; no other markdown. The local time is "
              + datetime.now().strftime("%A %d %B %Y, %H:%M") + ".")
    personality = soul.prompt()
    if personality:
        system += ("\n\nYou are %s. Keep to the length limit above whatever "
                   "the personality says.\n\n" % soul.name()) + personality
    turns = []
    for prev_q, prev_a in (history or []):
        turns += [{"role": "user", "content": prev_q},
                  {"role": "assistant", "content": prev_a}]
    turns.append({"role": "user", "content": question})

    text, note = complete(system, turns, timeout)

    text = text.strip()
    if not text:
        raise RuntimeError("empty answer")
    # Bold markers and blank lines only make the panel taller. Backticks stay:
    # the panel renders them as code.
    text = text.replace("**", "")
    return note + "\n".join(l.rstrip() for l in text.splitlines() if l.strip())


# ---------------------------------------------------------------- file tasks

# The planner may only MOVE files into folders. No delete, no rename, no
# overwrite, no shell: it returns groups of file names and a destination,
# and Cane checks every one before you see it. A file named "delete
# everything.pdf" is just a name - there is no command for it to become.

# Windows known-folder IDs. Desktop and Documents are often redirected into
# OneDrive, so Path.home() / "Documents" can point at the wrong place.
KNOWN_FOLDERS = {
    "downloads": "{374DE290-123F-4565-9164-39C4925E467B}",
    "desktop": "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}",
    "documents": "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}",
    "pictures": "{33E28130-4E1E-4676-835A-98395C3BC3BB}",
    "videos": "{18989B1D-99B5-455B-841C-AB7C74E4DDFC}",
    "music": "{4BD8D571-6D19-48D3-BE97-422220080E43}",
}
FOLDER_WORDS = {"downloads": "downloads", "download": "downloads",
                "desktop": "desktop", "documents": "documents",
                "docs": "documents", "pictures": "pictures",
                "photos": "pictures", "videos": "videos", "music": "music"}
PLAN_MAX = 400          # files shown to the planner, newest first
UNDO = Path(__file__).with_name("undo.json")
# Half-finished downloads: moving one breaks the download in progress.
IN_PROGRESS = (".crdownload", ".part", ".partial", ".download", ".tmp")


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]


def known_folders():
    """{name: Path} for the six user folders, as Windows reports them."""
    out = {}
    for name, fid in KNOWN_FOLDERS.items():
        path = ctypes.c_wchar_p()
        guid = GUID.from_buffer_copy(uuid.UUID(fid).bytes_le)
        if ctypes.windll.shell32.SHGetKnownFolderPath(
                ctypes.byref(guid), 0, None, ctypes.byref(path)) == 0:
            out[name] = Path(path.value)
            ctypes.windll.ole32.CoTaskMemFree(path)
    return out


def source_folder(text):
    """Which folder the request is about. "from X" wins; a folder named
    after into/to is the destination, not the source. Default: Downloads."""
    low = text.lower()
    m = re.search(r"\bfrom (?:my |the )?(\w+)", low)
    if m and m.group(1) in FOLDER_WORDS:
        return FOLDER_WORDS[m.group(1)]
    for m in re.finditer(r"(\w+)?\s*(?:my |the )?\b(%s)\b" % "|".join(FOLDER_WORDS),
                         low):
        if m.group(1) not in ("into", "to"):
            return FOLDER_WORDS[m.group(2)]
    return "downloads"


def scan(folder):
    """Top-level files only, newest first. Skips hidden and system files and
    downloads still in progress."""
    files = []
    try:
        entries = list(os.scandir(folder))
    except OSError:
        return []
    for e in entries:
        try:
            if not e.is_file(follow_symlinks=False):
                continue
            st = e.stat(follow_symlinks=False)
        except OSError:
            continue
        if (getattr(st, "st_file_attributes", 0) & 0x6        # hidden|system
                or e.name.startswith(".")
                or e.name.lower().endswith(IN_PROGRESS)):
            continue
        files.append({"name": e.name, "size": st.st_size, "mtime": st.st_mtime})
    files.sort(key=lambda f: -f["mtime"])
    return files[:PLAN_MAX]


def _size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return ("%d %s" if unit == "B" else "%.1f %s") % (n, unit)
        n /= 1024


def resolve_dest(text, roots):
    """ "Documents/paisa" -> Path inside the Documents root, or None.

    Only the six user folders plus up to three plain subfolder names. Anything
    that could leave them - "..", a drive letter, a junction pointing elsewhere
    - is refused, as are names Windows can't create.
    """
    parts = [p.strip() for p in re.split(r"[\\/]+", str(text)) if p.strip()]
    if not parts or parts[0].lower() not in roots:
        return None
    sub = parts[1:]
    for p in sub:
        if (p in (".", "..") or p.startswith(".") or p.rstrip(" .") != p
                or re.search(r'[<>:"|?*\x00-\x1f]', p)
                or re.fullmatch(r"(con|prn|aux|nul|com\d|lpt\d)(\..*)?", p, re.I)):
            return None
    if len(sub) > 3:
        return None
    root = roots[parts[0].lower()]
    dest = root.joinpath(*sub)
    try:
        dest.resolve().relative_to(root.resolve())
    except (ValueError, OSError):
        return None
    return dest


PLAN_SYSTEM = """You organise files on a Windows laptop. You never delete and
never rename. You only group files and say which folder each group goes to.

Reply with JSON only, in this shape:
{"summary": "<one short line>",
 "groups": [{"label": "<what these files are>", "to": "<destination>",
             "files": ["<exact file name from the list>", ...]}]}

"to" is one of the user's folders (Downloads, Desktop, Documents, Pictures,
Videos, Music) plus optional subfolders, e.g. Downloads/Installers or
Documents/paisa. Prefer existing subfolders; new ones are fine.
Copy file names exactly. Leave out files the request doesn't cover.
Don't make a folder for a single file unless the request asks for it."""


def ask_plan(request, source, files, roots, timeout=120):
    """The planner's raw JSON (a dict), plus the provider fallback note."""
    subs = []
    for name, root in roots.items():
        try:
            kids = sorted(e.name for e in os.scandir(root)
                          if e.is_dir() and not e.name.startswith("."))[:40]
        except OSError:
            kids = []
        subs.append("%s: %s" % (name.title(), ", ".join(kids) or "(none)"))
    listing = "\n".join("%s | %s | %s" % (
        f["name"], _size(f["size"]),
        datetime.fromtimestamp(f["mtime"]).strftime("%Y-%m-%d")) for f in files)
    user = ("Request: %s\n\nSOURCE: %s (%d files, newest first)\n%s\n\n"
            "Existing subfolders:\n%s" % (request, source.title(), len(files),
                                          listing, "\n".join(subs)))
    text, note = complete(PLAN_SYSTEM, [{"role": "user", "content": user}],
                          timeout, effort="medium")
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise RuntimeError("the planner didn't return a plan")
    try:
        raw = json.loads(text[start:end + 1])
    except ValueError:
        raise RuntimeError("the planner's plan wasn't valid JSON") from None
    if not isinstance(raw, dict):
        raise RuntimeError("the planner's plan had the wrong shape")
    return raw, note


def _where(dest, roots):
    """ "Downloads\\Installers" rather than the full path, for the panel."""
    for name, root in roots.items():
        try:
            rel = dest.relative_to(root)
        except ValueError:
            continue
        return "\\".join([name.title()] + list(rel.parts))
    return str(dest)


def build_plan(request, source, files, roots, raw):
    """Check every move the planner proposed; keep only the safe ones.

    A move survives only if its file was in the listing, it isn't already
    claimed by another group, its destination resolves inside the user
    folders, it isn't already there, and nothing sits at the target name.
    """
    src_root = roots[source]
    by_name = {f["name"]: f for f in files}
    claimed, groups, dropped = set(), [], 0
    for g in raw.get("groups") or []:
        if not isinstance(g, dict):
            continue
        dest = resolve_dest(g.get("to", ""), roots)
        names = [n for n in (g.get("files") or []) if isinstance(n, str)]
        moves = []
        for n in names:
            if (dest is None or n not in by_name or n in claimed
                    or dest == src_root or (dest / n).exists()):
                dropped += 1
                continue
            claimed.add(n)
            moves.append((src_root / n, dest / n))
        if moves:
            groups.append({"label": str(g.get("label") or "files")[:40],
                           "dest": dest, "where": _where(dest, roots),
                           "moves": moves})
    return {"request": request, "source": source, "total": len(files),
            "summary": str(raw.get("summary") or "")[:120],
            "groups": groups, "dropped": dropped,
            "stat": {src_root / f["name"]: (f["size"], f["mtime"]) for f in files}}


def execute_plan(plan):
    """Do the approved moves. Returns (moved, skipped).

    Each file is re-checked first - anything changed or gone since the plan
    was shown is skipped, never guessed at. os.rename refuses to overwrite on
    Windows, so a file that appears at the target in between is safe too.
    """
    moved, skipped, made = [], [], []
    for g in plan["groups"]:
        for src, dst in g["moves"]:
            try:
                st = src.stat()
            except OSError:
                skipped.append((src.name, "gone"))
                continue
            if (st.st_size, st.st_mtime) != plan["stat"].get(src):
                skipped.append((src.name, "changed since the plan"))
                continue
            missing = []
            d = dst.parent
            while not d.exists():
                missing.append(d)
                d = d.parent
            try:
                for d in reversed(missing):
                    d.mkdir()
                    made.append(str(d))
                os.rename(src, dst)
            except FileExistsError:
                skipped.append((src.name, "something is already at the target"))
                continue
            except OSError as e:
                # ponytail: cross-drive moves are skipped, not copied - a copy
                # plus delete is a delete, and this feature never deletes.
                skipped.append((src.name, e.strerror or "could not move"))
                continue
            moved.append((str(src), str(dst)))
    if moved:
        UNDO.write_text(json.dumps({"when": datetime.now().isoformat(
            timespec="seconds"), "moves": moved, "made": made}, indent=1),
            encoding="utf-8")
    return moved, skipped


def undo_last():
    """Put the last batch back. Returns (restored, missed)."""
    try:
        batch = json.loads(UNDO.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0, []
    restored, missed = 0, []
    for src, dst in reversed(batch.get("moves", [])):
        if Path(dst).exists() and not Path(src).exists():
            try:
                os.rename(dst, src)
                restored += 1
                continue
            except OSError:
                pass
        missed.append(Path(src).name)   # moved again, or a file took its place
    for d in reversed(batch.get("made", [])):
        try:
            os.rmdir(d)                 # only succeeds while it is empty
        except OSError:
            pass
    UNDO.unlink(missing_ok=True)
    return restored, missed


HINT = ("try: open whatsapp / volume 40 / timer 10 min / play sapphire / "
        "how do I ... / settings")

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"


def first_video(query):
    """(video id, title) of the top YouTube hit, or (None, None).

    Opening a search page was never "play the song" - it just showed results
    and waited for a click. Resolving the id here means the song actually
    starts. No API key: the id is in the search HTML.
    """
    url = ("https://www.youtube.com/results?search_query="
           + urllib.parse.quote(query))
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=10) as r:
            html = r.read().decode("utf-8", "ignore")
    except (urllib.error.URLError, OSError, TimeoutError):
        return None, None
    m = re.search(r'"videoId":"([\w-]{11})"', html)
    if not m:
        return None, None
    t = re.search(r'"title":\{"runs":\[\{"text":"(.*?)"\}', html)
    return m.group(1), (t.group(1) if t else None)


def dispatch(intent, ui=None):
    """Run the parsed intent. Returns a one-line status string.

    `ui` is the Bar (or None in tests): show_list, unfocus, show_answer,
    copy, timer_done.
    """
    kind = intent.get("intent")
    arg = str(intent.get("arg") or "").strip()
    action = intent.get("action")

    def unfocus():
        if ui:
            ui.unfocus()      # hand focus back to whatever is underneath...
            time.sleep(0.3)   # ...and let Windows actually do it

    # A greeting, thanks, or gibberish must never trigger an action. Without
    # this, "hi" was classified as the nearest action and started the music.
    if kind == "none":
        return HINT

    if kind == "offline":
        return "model offline - could not start ollama"

    if kind == "settings":
        if ui:
            ui.show_settings()
        return "settings"

    for key, label, kinds in FEATURES:
        if kind in kinds and not feature_on(key):
            return label + " is switched off in settings"

    if kind in ("focus", "block"):
        return focus.dispatch(intent, ui)

    if kind == "soul":
        if ui:
            soul.open_window(ui)
        return "personality"

    # Tasks are done in Cane's own chat, by the answer provider, through
    # Cane's own commands. Nothing is handed to an external CLI here; Claude
    # Code is a button in the chat that only you can press.
    if kind in ("chat", "escalate"):
        if not ui:
            return "that's a job for the chat"
        ui.root.after(0, ui.hide)
        agent.open_window(ui, arg if kind == "escalate" and arg else None)
        return "working on it in the chat" if kind == "escalate" else "chat"

    if kind == "media":
        if action in ("volup", "voldown"):
            try:   # one media key is a 2% step - too small to notice
                return "volume %d" % set_volume(delta=10 if action == "volup" else -10)
            except OSError:
                pass
        vk = MEDIA_KEYS.get(action or "play")
        if not vk:
            return "unknown media action"
        unfocus()
        send_media(vk)
        return "media: " + str(action)

    if kind == "volume":
        try:
            return "volume %d" % set_volume(int(arg))
        except (OSError, ValueError):
            return "could not set the volume"

    if kind == "brightness":
        n = (brightness(delta=20 if action == "up" else -20)
             if action in ("up", "down") else
             brightness(int(arg)) if arg.isdigit() else None)
        return ("brightness %d" % n if n is not None
                else "brightness control isn't available on this screen")

    if kind == "timer":
        secs = int(arg) * UNIT_SECS.get(action, 60) if arg.isdigit() else 0
        if not 0 < secs <= 86400:
            return "a timer has to be between 1 second and 24 hours"
        label = arg + {"s": " sec", "m": " min", "h": " hr"}.get(action, " min")
        if ui:
            t = threading.Timer(secs, ui.timer_done, args=(label,))
            t.daemon = True   # must not keep the process alive after Quit
            t.start()
        ends = (datetime.now() + timedelta(seconds=secs)).strftime("%H:%M:%S")
        return "timer: %s, ends %s" % (label, ends)

    if kind == "lock":
        unfocus()
        ctypes.windll.user32.LockWorkStation()
        return "locked"

    if kind == "screenshot":
        unfocus()             # or the bar is in the picture
        screenshot()
        return "screenshot saved to Pictures\\Screenshots"

    if kind == "theme":
        set_theme(arg == "dark")
        return arg + " mode on"

    if kind == "google":
        webbrowser.open("https://www.google.com/search?q=" + urllib.parse.quote(arg))
        return "searching: " + arg[:40]

    if kind == "clock":
        return datetime.now().strftime("%H:%M  ·  %A %d %B")

    if kind == "weather":
        # wttr.in: no key, ~0.7s, locates by IP when no place is given.
        url = ("https://wttr.in/" + urllib.parse.quote(arg)
               + "?format=%l:+%C+%t+(feels+%f)")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "curl"})
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.read().decode("utf-8", "ignore").strip()[:90]
        except (urllib.error.URLError, OSError, TimeoutError):
            return "couldn't reach the weather service"

    if kind == "copilot":
        # Neither the app nor the website accepts a question in the link
        # (both tested: the box came up empty), and typing it for you would
        # land in whatever window has focus. So: copy it, open Copilot.
        if ui:
            ui.copy(arg)
        try:
            os.startfile("ms-copilot:")
        except OSError:
            webbrowser.open("https://copilot.microsoft.com")
        return "Copilot opened - question copied, paste with Ctrl+V"

    if kind == "ask":
        # Cloud answers off: nothing leaves the laptop, Copilot takes it.
        if not setting("cloud_answers", True):
            return dispatch({"intent": "copilot", "arg": arg}, ui)
        try:
            text = answer(arg, getattr(ui, "thread", None))
        except RuntimeError as e:
            text = "Couldn't get an answer: %s" % e
        if ui:
            ui.remember_turn(arg, text)
            ui.show_answer(arg, text)
            return "answered" if not text.startswith("Couldn't") else text[:60]
        return text

    if kind == "search_play":
        vid, title = first_video(arg)
        if vid:
            webbrowser.open("https://www.youtube.com/watch?v=" + vid)
            return "playing: " + (title or arg)[:44]
        # Could not resolve one - show the search rather than nothing.
        webbrowser.open("https://www.youtube.com/results?search_query="
                        + urllib.parse.quote(arg))
        return "searching: " + arg

    if kind == "launch":
        hit = resolve(arg)
        if not hit:
            if not _APPS:
                return "still reading your apps - try again in a few seconds"
            # Too far off to open on a guess, close enough to suggest.
            near = difflib.get_close_matches(arg.lower(), list(_APPS), n=1,
                                             cutoff=0.6)
            if near:
                return "no %r - did you mean %s?" % (arg[:24], _APPS[near[0]][0])
            return "nothing called " + repr(arg[:30])
        # os.startfile, not a shell - it hands the target to ShellExecute and
        # never reparses it, whether an app id, a folder, a page or a URL.
        os.startfile(hit[1])
        return "opened: " + hit[0]

    if kind in ("remember", "remind"):
        due = parse_when(intent.get("when")) if kind == "remind" else None
        add_note(arg, due)
        if kind == "remind" and not due:
            return "saved (no time understood): " + arg[:40]
        when = " for " + due.strftime("%a %H:%M") if due else ""
        return "saved" + when + ": " + arg[:40]

    if kind == "recall":
        items = open_items()
        if ui:
            ui.show_list(items)
        return (str(len(items)) + " open") if items else "nothing open"

    if kind == "done":
        hit = mark_done(arg)
        return ("done: " + hit["text"][:40]) if hit else ("no match for " + repr(arg))

    if kind == "files":
        # Nothing moves here. This only builds a plan; files move when you
        # press "Do it" on the panel, after every move is checked again.
        roots = known_folders()
        source = source_folder(arg)
        if source not in roots:
            return "can't find your %s folder" % source
        files = scan(roots[source])
        if not files:
            return "nothing to sort in " + source.title()
        try:
            raw, note = ask_plan(arg, source, files, roots)
        except RuntimeError as e:
            return "couldn't plan it: %s" % e
        plan = build_plan(arg, source, files, roots, raw)
        plan["note"] = note
        n = sum(len(g["moves"]) for g in plan["groups"])
        if not n:
            return "nothing to move - " + (plan["summary"] or "it already looks tidy")
        if ui:
            ui.show_plan(plan)
            return "plan ready: %d files - nothing moves until you say so" % n
        return "plan: %d files (no screen to approve it on)" % n

    if kind == "undo":
        restored, missed = undo_last()
        if not restored and not missed:
            return "nothing to undo"
        return "put back %d file%s%s" % (restored, "" if restored == 1 else "s",
                                         " (%d had moved again)" % len(missed)
                                         if missed else "")

    return "nothing I can do with that"


# ------------------------------------------------------------- suggestions

# These put another window in front, so the bar gets out of the way. Anything
# else (volume, a timer, an answer) leaves it open for the next command.
HIDE_AFTER = {"launch", "google", "search_play", "copilot", "escalate",
              "lock", "screenshot", "media", "none", "offline", "chat", "soul"}

SAYS = {"weather": "show the weather", "clock": "show the time",
        "lock": "lock the screen", "screenshot": "take a screenshot",
        "settings": "open Cane's settings", "recall": "show what's open",
        "remember": "save a note", "done": "mark it done",
        "escalate": "do it in the chat", "chat": "open the chat",
        "soul": "open the personality settings",
        "files": "plan it and show you first",
        "undo": "put the last moved files back"}
FOCUS_SAYS = {"stop": "stop focus", "status": "show focus time left",
              "history": "show focus history"}
MEDIA_SAYS = {"pause": "pause", "play": "play", "next": "next track",
              "prev": "previous track", "mute": "mute", "volup": "volume up",
              "voldown": "volume down"}


def describe(it):
    """One line saying what Cane would do with this intent."""
    kind, arg, act = it["intent"], it.get("arg") or "", it.get("action") or ""
    if kind == "launch":
        hit = resolve(arg)
        return "open " + (hit[0] if hit else arg)
    if kind == "volume":
        return "set volume to %s%%" % arg
    if kind == "brightness":
        return "set brightness to %s%%" % arg if arg else "brightness " + act
    if kind == "timer":
        return "start a timer for %s%s" % (arg, {"s": "s", "m": " min",
                                                 "h": " hr"}[act])
    if kind == "media":
        return MEDIA_SAYS.get(act, act)
    if kind == "theme":
        return "switch to %s mode" % arg
    if kind == "google":
        return "search the web for “%s”" % arg[:32]
    if kind == "search_play":
        return "play %s" % arg[:32]
    if kind == "copilot":
        return "ask Copilot “%s”" % arg[:28]
    if kind == "remind":
        return "remind you: %s" % arg[:32]
    if kind == "focus":
        if act == "start":
            return "focus %s min%s" % (it.get("minutes"), " on " + arg[:28]
                                       if arg else "")
        return FOCUS_SAYS.get(act, "focus")
    if kind == "block":
        site = it.get("site", "")
        if act == "add":
            return "block %s %s" % (site, "%s-%s" % (it["from"], it["to"])
                                    if it.get("from") else "during focus")
        return "unblock " + site if act == "remove" else "show blocked sites"
    return SAYS.get(kind, kind)


def suggest(text, limit=5):
    """Rows for the bar's live list: (label, note, intent).

    Pure and instant on purpose - no model, no network - because it runs on
    every keystroke. The model still decides anything this cannot name.
    """
    t = " ".join(text.split())
    if not t:
        return []
    rows = []
    hit = quick(t) or quick(fix_typos(t))
    if hit:
        rows.append((describe(hit), "", hit))
    name = re.sub(r"^(open|launch|start|go to) ", "", t.lower()).strip()
    if name and (not hit or hit["intent"] == "launch"):
        near = ([k for k in _APPS if k.startswith(name)]
                or [k for k in _APPS if name in k])
        for key in sorted(near, key=len)[:3]:
            row = ("open " + _APPS[key][0], "app",
                   {"intent": "launch", "arg": _APPS[key][0]})
            if all(row[0] != r[0] for r in rows):
                rows.append(row)
    if not hit and len(t) > 2:
        # First fallback is Cane's normal routing (intent None = parse it on
        # Enter), not "ask": that sent "play next" to the cloud as a question.
        rows.append(("run “%s”" % t[:34], "Cane decides", None))
        rows.append(("ask " + current_model(),
                     "", {"intent": "ask", "arg": t}))
        rows.append(("search the web for “%s”" % t[:30], "",
                     {"intent": "google", "arg": t}))
    return rows[:limit]


def run(text, ui, item=None):
    """Parse and dispatch off the UI thread so the bar never freezes.

    `item` is already-parsed - a row picked from the live list, which needs
    no parsing and must not be re-guessed.
    """
    try:
        item = item or parse(text)
        ui.status(dispatch(item, ui),
                  keep=item.get("intent") not in HIDE_AFTER)
    except Exception as e:  # a bad command must not kill the daemon
        ui.status("error: " + str(e))


# ---------------------------------------------------------------- ui

# Near-black panel, one blue accent, nothing else competing for attention.
PANEL, FG, DIM, ACCENT = "#15171c", "#f0f2f5", "#7d838f", "#4c8dff"
KEY = "#010203"          # keyed out to fake rounded corners
MARGIN, RADIUS = 24, 16
FONT_IN = ("Segoe UI", 17)
FONT_SM = ("Segoe UI", 9)
FONT_BODY = ("Segoe UI", 11)


def round_rect(cv, x1, y1, x2, y2, r, **kw):
    """A rounded rectangle. Tk has no such primitive, so smooth a polygon."""
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
           x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return cv.create_polygon(pts, smooth=True, **kw)


def make_panel(win, w, h, rect_h=None):
    """Borderless rounded panel. Falls back to square if keying is refused.

    `rect_h` paints the panel shorter than the canvas: the bar keeps a canvas
    tall enough for its result list, and the unpainted part is keyed out, so
    it is both invisible and click-through until there are rows to show.
    """
    win.overrideredirect(True)
    win.attributes("-topmost", True)
    try:
        win.attributes("-alpha", 0.0)
        win.configure(bg=KEY)
        win.attributes("-transparentcolor", KEY)
        bg = KEY
    except tk.TclError:      # keying unsupported - square corners, still fine
        win.configure(bg=PANEL)
        bg = PANEL
    cv = tk.Canvas(win, width=w, height=h, bg=bg, highlightthickness=0)
    cv.pack()
    round_rect(cv, 0, 0, w - 1, (rect_h or h) - 1, RADIUS, fill=PANEL,
               outline="", tags="panelbg")
    return cv


def slide(win, x_to, y, a_to, step=0, steps=12, then=None, x_from=None):
    """Ease-out slide along x with a matching fade. ~100ms end to end."""
    if x_from is None:
        x_from = win.winfo_x() or x_to
    a_from = float(win.attributes("-alpha"))
    t = step / steps
    e = 1 - (1 - t) ** 3
    try:
        win.geometry("+%d+%d" % (int(x_from + (x_to - x_from) * e), y))
        win.attributes("-alpha", a_from + (a_to - a_from) * e)
    except tk.TclError:
        return                                    # window died mid-animation
    if step < steps:
        win.after(8, lambda: slide(win, x_to, y, a_to, step + 1, steps,
                                   then, x_from))
    elif then:
        then()


def drop(win, x, y_to, a_to, step=0, steps=10, y_from=None):
    """slide(), but down from the top edge - for the hover-opened bar."""
    t = step / steps
    e = 1 - (1 - t) ** 3
    try:
        win.geometry("+%d+%d" % (x, int(y_from + (y_to - y_from) * e)))
        win.attributes("-alpha", a_to * e)
    except tk.TclError:
        return
    if step < steps:
        win.after(8, lambda: drop(win, x, y_to, a_to, step + 1, steps, y_from))


def fill_text(txt, body):
    """Insert an answer, styling the little markdown answers actually use:
    fenced or indented code, `inline code`, and # headings."""
    fenced = False
    for line in body.split("\n"):
        if line.strip().startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            # Keep the indentation: stripping it flattened every code answer
            # (a Java calculator came out with no nesting at all).
            txt.insert("end", line.rstrip() + "\n", "code")
        elif line.startswith("    "):
            txt.insert("end", line[4:].rstrip() + "\n", "code")
        elif line.startswith("#"):
            txt.insert("end", line.lstrip("# ").rstrip() + "\n", "head")
        else:
            # Odd pieces of the split are what sat between the backticks.
            for k, piece in enumerate(re.split(r"`([^`]+)`", line)):
                if piece:
                    txt.insert("end", piece, "code" if k % 2 else "")
            txt.insert("end", "\n")


class Bar:
    """A single always-alive hidden window, shown on hotkey."""

    W, H = 560, 104
    ROW, ROWS = 32, 5                  # result row height, and how many

    def __init__(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.rows, self.sel, self.thread, self._idle_id = [], 0, [], None
        # Read by hover.loop on its own thread - plain attributes, no Tk calls.
        self.visible = self.hover_opened = self.engaged = False
        self.bar_height = self.H
        cv = make_panel(self.root, self.W, self.H + 10 + self.ROW * self.ROWS,
                        rect_h=self.H)

        cv.create_text(30, 42, text="\u203a", fill=ACCENT, anchor="w",
                       font=("Segoe UI", 24, "bold"))
        self.entry = tk.Entry(self.root, font=FONT_IN, bg=PANEL, fg=FG,
                              insertbackground=ACCENT, relief="flat",
                              highlightthickness=0, borderwidth=0)
        cv.create_window(52, 42, window=self.entry, anchor="w",
                         width=self.W - 110, height=32)
        gear = cv.create_text(self.W - 34, 42, text="⚙", fill=DIM,
                              font=("Segoe UI Symbol", 16), tags="gear")
        cv.tag_bind("gear", "<Enter>", lambda e: (cv.itemconfig(gear, fill=ACCENT),
                                                  cv.config(cursor="hand2")))
        cv.tag_bind("gear", "<Leave>", lambda e: (cv.itemconfig(gear, fill=DIM),
                                                  cv.config(cursor="")))
        cv.tag_bind("gear", "<Button-1>", lambda e: (self.hide(),
                                                     self.show_settings()))
        self.cv = cv
        self.label = tk.Label(self.root, text="", font=FONT_SM, bg=PANEL,
                              fg=DIM, anchor="w")
        cv.create_window(53, 76, window=self.label, anchor="w",
                         width=self.W - 80)

        self.entry.bind("<Return>", self.submit)
        self.entry.bind("<Escape>", lambda e: self.hide())
        self.entry.bind("<KeyRelease>", self._live)
        self.entry.bind("<Down>", lambda e: self._move(1))
        self.entry.bind("<Up>", lambda e: self._move(-1))
        # A hover-opened bar has no keyboard focus (it must not steal it from
        # what you were typing in). Clicking it takes focus and keeps it open.
        self.entry.bind("<Button-1>", self._engage)
        cv.bind("<Button-1>", self._engage)
        self.entry.bind("<KeyPress>", lambda e: setattr(self, "engaged", True),
                        add="+")
        self._toasts = []

    def _engage(self, _=None):
        self.engaged = True
        self.root.focus_force()
        self.entry.focus_set()

    # -- live results

    def _live(self, event=None):
        if event and event.keysym in ("Up", "Down", "Return", "Escape"):
            return
        self._draw_rows(suggest(self.entry.get()))
        self._touch()

    def _bar_h(self):
        """Painted height right now - the list makes the bar taller, and the
        panels stacked underneath have to start below it."""
        return self.H + (10 + self.ROW * len(self.rows) if self.rows else 0)

    def _draw_rows(self, rows):
        """Redraw the result list under the input, growing the panel."""
        cv = self.cv
        cv.delete("rows")
        self.rows = rows
        self.sel = min(self.sel, max(0, len(rows) - 1))
        h = self.bar_height = self._bar_h()
        round_rect(cv, 0, 0, self.W - 1, h - 1, RADIUS, fill=PANEL,
                   outline="", tags="rows")
        cv.tag_lower("rows")             # under the input, chevron and gear
        for k, (label, note, _) in enumerate(rows):
            y = self.H + self.ROW * k
            tag = "row%d" % k
            if k == self.sel:
                round_rect(cv, 12, y, self.W - 12, y + self.ROW - 4, 8,
                           fill="#1e222a", outline="", tags=("rows", tag))
            cv.create_text(52, y + 14, text=label[:56], anchor="w",
                           fill=FG if k == self.sel else DIM, font=FONT_BODY,
                           tags=("rows", tag))
            if note:
                cv.create_text(self.W - 24, y + 14, text=note, anchor="e",
                               fill=DIM, font=FONT_SM, tags=("rows", tag))
            cv.tag_bind(tag, "<Button-1>", lambda e, i=k: self._pick(i))
            cv.tag_bind(tag, "<Enter>", lambda e, i=k: self._hover(i))

    def _hover(self, i):
        if i != self.sel:
            self.sel = i
            self._draw_rows(self.rows)

    def _move(self, d):
        if self.rows:
            self.sel = (self.sel + d) % len(self.rows)
            self._draw_rows(self.rows)
        return "break"                   # don't let Entry move the caret

    def _pick(self, i):
        if not 0 <= i < len(self.rows):
            return
        item = self.rows[i][2]         # None: route the typed text normally
        text = self.entry.get().strip()
        self.label.config(text="…")
        self.entry.delete(0, "end")
        self._draw_rows([])
        threading.Thread(target=run, daemon=True, args=(text, self),
                         kwargs={"item": item}).start()

    # -- geometry

    def _x(self):
        return self.root.winfo_screenwidth() - self.W - MARGIN

    # -- visibility

    def show(self, hover=False):
        """`hover`: opened by touching the top edge. It drops down from the
        top instead of sliding in, and leaves keyboard focus where it was."""
        x = self._x()
        # Mid-fade-out counts as hidden, so a quick re-open still animates in.
        already = (self.root.winfo_viewable()
                   and float(self.root.attributes("-alpha")) > 0.9)
        self.visible, self.hover_opened = True, hover
        self.engaged = not hover
        line = focus.bar_line()
        if line and not self.entry.get():
            self.label.config(text=line)
        if already:
            if not hover:
                self._engage()
            return
        self.root.attributes("-alpha", 0.0)
        self.root.deiconify()
        self.root.lift()
        if hover:
            self.root.geometry("+%d+%d" % (x, MARGIN - 30))
            drop(self.root, x, MARGIN, 0.97, y_from=MARGIN - 30)
        else:
            self.root.geometry("+%d+%d" % (x + 40, MARGIN))
            self.entry.focus_force()
            slide(self.root, x, MARGIN, 0.97, x_from=x + 40)
        threading.Thread(target=warmup, daemon=True).start()

    def hide(self):
        def done():
            self.root.withdraw()
            self.entry.delete(0, "end")
            self.label.config(text="")
            self._draw_rows([])
            self.thread = []            # closing ends the question thread
            self.visible = self.hover_opened = self.engaged = False
        slide(self.root, self._x() + 40, MARGIN, 0.0, steps=8, then=done)

    def _touch(self):
        """Restart the idle close. The bar stays open between commands, but
        not forever - it is always-on-top, and a forgotten one is in the way.
        """
        if self._idle_id:
            self.root.after_cancel(self._idle_id)
        self._idle_id = self.root.after(30000, self.hide)

    def remember_turn(self, question, reply):
        self.thread = (self.thread + [(question, reply)])[-2:]

    def unfocus(self):
        """Withdraw instantly - a media key must reach the app underneath.

        No animation here on purpose: the keypress is sent 250ms later, and an
        animating window would still own focus when it lands.
        """
        self.visible = self.hover_opened = False
        self.root.after(0, self.root.withdraw)

    def status(self, msg, keep=False):
        """Report the result. `keep` leaves the bar up, cleared and focused,
        so the next command needs no second trip through the hotkey."""
        def put():
            self.label.config(text=msg)
            if keep:
                self.entry.delete(0, "end")
                self._draw_rows([])
                self._touch()
                # No focus_force here: if you moved to another window while
                # the command ran, taking focus back would be rude. If you
                # stayed, the entry never lost it.
        self.root.after(0, put)
        if not keep:
            self.root.after(2500, self.hide)   # long enough to read the hint

    def submit(self, _):
        if self.rows:                    # Enter takes the highlighted row
            return self._pick(self.sel)
        text = self.entry.get().strip()
        if not text:
            return
        self.label.config(text="\u2026")
        threading.Thread(target=run, daemon=True, args=(text, self)).start()

    def copy(self, text):
        def put():
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
        self.root.after(0, put)

    def timer_done(self, label):
        self.root.after(0, lambda: self.alert({"text": "Time's up - " + label},
                                              title="TIMER"))

    # -- secondary windows, stacked down the right edge

    def _toast(self, h):
        w = tk.Toplevel(self.root)
        y = MARGIN + self._bar_h() + 12 + sum(t + 12 for t in self._toasts)
        self._toasts.append(h)
        cv = make_panel(w, self.W, h)
        x = self._x()
        w.geometry("+%d+%d" % (x + 40, y))
        closed = []

        def close(*_):
            if closed:
                return                      # a click during the fade-out
            closed.append(True)
            if h in self._toasts:
                self._toasts.remove(h)
            slide(w, x + 40, y, 0.0, steps=8, then=w.destroy)

        # Click anywhere to dismiss. Escape alone is not enough: a borderless
        # Toplevel may never take focus, which left these unclosable.
        cv.bind("<Button-1>", close)
        w.bind("<Escape>", close)
        slide(w, x, y, 0.97, x_from=x + 40)
        return w, cv, close

    def show_list(self, items):
        self.root.after(0, lambda: self._list_window(items))

    def _list_window(self, items):
        # Only ever one list open, else repeated "what's left" walks new
        # windows down and off the bottom of the screen.
        if getattr(self, "_close_list", None):
            self._close_list()
        rows = []
        for n, i in enumerate(items, 1):
            due = ""
            if i["due"]:
                due = "   " + datetime.fromisoformat(i["due"]).strftime("%a %H:%M")
            rows.append((str(n), i["text"], due))
        h = max(64, 46 + 24 * len(rows))
        w, cv, close = self._toast(h)
        self._close_list = close
        cv.create_text(24, 24, text="OPEN", fill=DIM, anchor="w",
                       font=("Segoe UI", 8, "bold"))
        if not rows:
            cv.create_text(24, 46, text="nothing open", fill=DIM, anchor="w",
                           font=FONT_BODY)
        for k, (n, text, due) in enumerate(rows):
            y = 46 + 24 * k
            cv.create_text(24, y, text=n, fill=ACCENT, anchor="w", font=FONT_BODY)
            cv.create_text(44, y, text=text[:52], fill=FG, anchor="w",
                           font=FONT_BODY)
            if due:
                cv.create_text(self.W - 24, y, text=due, fill=DIM, anchor="e",
                               font=FONT_SM)

    def _button(self, parent, text, command):
        return tk.Button(parent, text=text, font=FONT_SM, bg=PANEL, fg=DIM,
                         relief="flat", borderwidth=0, highlightthickness=0,
                         activebackground=PANEL, activeforeground=ACCENT,
                         cursor="hand2", command=command)

    def alert(self, item, title="REMINDER"):
        """Pop a fired reminder or timer. Stays until dismissed - it is the
        point - and makes a sound, since you may be looking elsewhere."""
        winsound.PlaySound("SystemNotification",
                           winsound.SND_ALIAS | winsound.SND_ASYNC)
        w, cv, close = self._toast(112)
        cv.create_text(24, 26, text=title, fill=ACCENT, anchor="w",
                       font=("Segoe UI", 8, "bold"))
        cv.create_text(24, 58, text=item["text"][:120], fill=FG, anchor="w",
                       font=("Segoe UI", 13), width=self.W - 48)
        done = ((lambda: (mark_done(item["text"]), close()))
                if title == "REMINDER" else close)
        cv.create_window(self.W - 24, 92, window=self._button(w, "Done", done),
                         anchor="e")

    # -- file plans

    def show_plan(self, plan):
        self.root.after(0, lambda: self._plan_window(plan))

    def _plan_window(self, plan):
        """The approval step. Nothing has moved yet; "Do it" re-checks every
        file before moving it, and Cancel or a click elsewhere drops the plan."""
        if getattr(self, "_close_plan", None):
            self._close_plan()
        groups, shown = plan["groups"], plan["groups"][:8]
        n = sum(len(g["moves"]) for g in groups)
        h = 88 + 26 * len(shown) + (22 if len(groups) > 8 else 0) + 96
        w, cv, close = self._toast(h)
        self._close_plan = close
        cv.create_text(24, 24, text="PLAN", fill=ACCENT, anchor="w",
                       font=("Segoe UI", 8, "bold"))
        cv.create_text(70, 24, text=plan["request"][:56], fill=DIM, anchor="w",
                       font=FONT_SM)
        cv.create_text(24, 54, text="%d files in %s" % (
            plan["total"], plan["source"].title()), fill=FG, anchor="w",
            font=FONT_BODY)
        y = 86
        for g in shown:
            cv.create_text(56, y, text=str(len(g["moves"])), fill=ACCENT,
                           anchor="e", font=FONT_BODY)
            cv.create_text(68, y, text=g["label"][:24], fill=FG, anchor="w",
                           font=FONT_BODY)
            cv.create_text(self.W - 24, y, text="→ " + g["where"][:36],
                           fill=DIM, anchor="e", font=FONT_SM)
            y += 26
        if len(groups) > 8:
            cv.create_text(68, y, text="+ %d more groups - see Details" % (
                len(groups) - 8), fill=DIM, anchor="w", font=FONT_SM)
            y += 22
        cv.create_text(68, y, text="%d left where they are" % (plan["total"] - n),
                       fill=DIM, anchor="w", font=FONT_BODY)
        safety = "nothing is deleted · Undo afterwards"
        if plan["dropped"]:
            safety += " · %d unsafe suggestion%s dropped" % (
                plan["dropped"], "" if plan["dropped"] == 1 else "s")
        cv.create_text(24, y + 34, text=safety, fill=DIM, anchor="w",
                       font=FONT_SM)
        if plan.get("note"):
            cv.create_text(24, y + 52, text=plan["note"].strip()[:80],
                           fill=DIM, anchor="w", font=FONT_SM)

        def details():
            lines = []
            for g in groups:
                lines.append("# %s → %s" % (g["label"], g["where"]))
                lines += ["    " + src.name for src, _ in g["moves"]]
            self._answer_window(plan["request"], "\n".join(lines),
                                title="DETAILS", limit=60000)

        def do_it():
            close()
            self.status("moving…", keep=True)

            def go():
                moved, skipped = execute_plan(plan)
                self.root.after(0, lambda: self._moved_window(moved, skipped))
            threading.Thread(target=go, daemon=True).start()

        go_btn = self._button(w, "Do it", do_it)
        go_btn.config(fg=ACCENT, font=("Segoe UI", 9, "bold"))
        cv.create_window(self.W - 24, h - 22, anchor="e", window=go_btn)
        cv.create_window(self.W - 84, h - 22, anchor="e",
                         window=self._button(w, "Cancel", close))
        cv.create_window(self.W - 150, h - 22, anchor="e",
                         window=self._button(w, "Details", details))

    def _moved_window(self, moved, skipped):
        # Replace the "moving..." the bar has shown since Do it.
        self.label.config(text="moved %d file%s%s" % (
            len(moved), "" if len(moved) == 1 else "s",
            ", %d skipped" % len(skipped) if skipped else ""))
        h = 112 + (22 if skipped else 0)
        w, cv, close = self._toast(h)
        cv.create_text(24, 26, text="DONE", fill=ACCENT, anchor="w",
                       font=("Segoe UI", 8, "bold"))
        cv.create_text(24, 56, text="Moved %d file%s" % (
            len(moved), "" if len(moved) == 1 else "s"), fill=FG, anchor="w",
            font=("Segoe UI", 13))
        if skipped:
            cv.create_text(24, 82, text="%d skipped - %s" % (
                len(skipped), skipped[0][1]), fill=DIM, anchor="w",
                font=FONT_SM)

        def undo():
            close()
            threading.Thread(target=lambda: self.status(
                dispatch({"intent": "undo"}, self), keep=True),
                daemon=True).start()
        if moved:
            cv.create_window(self.W - 84, h - 22, anchor="e",
                             window=self._button(w, "Undo", undo))
        cv.create_window(self.W - 24, h - 22, anchor="e",
                         window=self._button(w, "OK", close))

    def show_answer(self, question, text):
        self.root.after(0, lambda: self._answer_window(question, text))

    # -- settings

    def show_settings(self):
        self.root.after(0, self._settings_window)

    def page_header(self, w, cv, title, close, back=None):
        """Top line of a settings-style page. With `back`, the left shows
        "‹ SETTINGS" and both it and Escape return there; ✕ always closes.

        Pages used to open with no way back - Personality, Chat and Blocked
        sites each closed Settings and left you on your own.
        """
        x = 24
        if back:
            link = cv.create_text(x, 28, text="‹  SETTINGS", fill=DIM,
                                  anchor="w", font=("Segoe UI", 8, "bold"),
                                  tags="back")
            cv.tag_bind("back", "<Button-1>", lambda e: back())
            cv.tag_bind("back", "<Enter>", lambda e: (
                cv.itemconfig(link, fill=ACCENT), cv.config(cursor="hand2")))
            cv.tag_bind("back", "<Leave>", lambda e: (
                cv.itemconfig(link, fill=DIM), cv.config(cursor="")))
            x = 24 + tkfont.Font(font=("Segoe UI", 8, "bold")).measure(
                "‹  SETTINGS") + 14
        cv.create_text(x, 28, text=title, fill=ACCENT, anchor="w",
                       font=("Segoe UI", 8, "bold"))
        x_btn = cv.create_text(self.W - 26, 28, text="✕", fill=DIM,
                               font=("Segoe UI", 11), tags="close")
        cv.tag_bind("close", "<Button-1>", close)
        cv.tag_bind("close", "<Enter>", lambda e: cv.itemconfig(x_btn, fill=FG))
        cv.tag_bind("close", "<Leave>", lambda e: cv.itemconfig(x_btn, fill=DIM))
        w.bind("<Escape>", lambda e: back() if back else close())

    def _toggle(self, cv, x, y, on, command, hit_tags=()):
        """A pill switch drawn on the canvas. Clicking it, or anything tagged
        in hit_tags (its label), flips it and calls command(new_state)."""
        pill = "pill%d_%d" % (x, y)
        hit = "hit%d_%d" % (x, y)
        state = [on]

        def draw():
            cv.delete(pill)
            round_rect(cv, x, y, x + 36, y + 20, 10, outline="",
                       fill=ACCENT if state[0] else "#2b2f38", tags=(pill, hit))
            kx = x + 26 if state[0] else x + 10
            cv.create_oval(kx - 7, y + 3, kx + 7, y + 17, outline="",
                           fill=FG if state[0] else DIM, tags=(pill, hit))

        def flip(_):
            state[0] = not state[0]
            draw()
            command(state[0])

        for t in hit_tags:
            cv.addtag_withtag(hit, t)
        cv.tag_bind(hit, "<Button-1>", flip)
        cv.tag_bind(hit, "<Enter>", lambda e: cv.config(cursor="hand2"))
        cv.tag_bind(hit, "<Leave>", lambda e: cv.config(cursor=""))
        draw()

    def _answers_section(self, w, cv, y):
        """Provider, key, model and a Test button. Returns the next free y.

        The key box is write-only: a saved key is never shown again, only its
        last four characters, so the window can be screenshotted safely.
        """
        field = dict(bg="#22252c", fg=FG, relief="flat", borderwidth=0,
                     highlightthickness=0, font=FONT_SM)
        cv.create_text(24, y, text="ANSWERS", fill=DIM, anchor="w",
                       font=("Segoe UI", 8, "bold"))
        y += 30
        by_label = {v[0]: k for k, v in PROVIDERS.items()}
        pvar, kvar, mvar = tk.StringVar(), tk.StringVar(), tk.StringVar()
        pvar.set(PROVIDERS[provider()][0])

        cv.create_text(24, y, text="Provider", fill=FG, anchor="w",
                       font=FONT_BODY)
        pick = tk.OptionMenu(w, pvar, *by_label)
        pick.config(activebackground="#2b2f38", activeforeground=FG,
                    cursor="hand2", **field)
        pick["menu"].config(bg=PANEL, fg=FG, activebackground=ACCENT,
                            activeforeground=FG, font=FONT_SM)
        cv.create_window(self.W - 24, y, window=pick, anchor="e")

        cv.create_text(24, y + 38, text="API key", fill=FG, anchor="w",
                       font=FONT_BODY)
        entry = tk.Entry(w, textvariable=kvar, show="•",
                         insertbackground=ACCENT, **field)
        cv.create_window(self.W - 84, y + 38, window=entry, anchor="e",
                         width=230, height=24)
        save = self._button(w, "Save", lambda: save_key())
        cv.create_window(self.W - 24, y + 38, window=save, anchor="e")
        key_note = cv.create_text(24, y + 60, text="", fill=DIM, anchor="w",
                                  font=FONT_SM)

        cv.create_text(24, y + 94, text="Model", fill=FG, anchor="w",
                       font=FONT_BODY)
        style = ttk.Style(w)
        style.theme_use("clam")        # the only theme that takes dark colours
        style.configure("Cane.TCombobox", fieldbackground="#22252c",
                        background="#22252c", foreground=FG, arrowcolor=DIM,
                        bordercolor="#22252c", lightcolor="#22252c",
                        darkcolor="#22252c", selectbackground="#22252c",
                        selectforeground=FG)
        combo = ttk.Combobox(w, textvariable=mvar, style="Cane.TCombobox",
                             font=FONT_SM, height=14)
        cv.create_window(self.W - 24, y + 94, window=combo, anchor="e",
                         width=290, height=24)

        test = self._button(w, "Test", lambda: run_test())
        cv.create_window(self.W - 24, y + 132, window=test, anchor="e")
        test_note = cv.create_text(24, y + 132, text="", fill=DIM, anchor="w",
                                   font=FONT_SM, width=self.W - 110)

        def prov():
            return by_label[pvar.get()]

        def save_model(*_):
            p = prov()
            set_setting("answer_model" if p == "ollama" else "model:" + p,
                        mvar.get().strip())

        def refresh(*_):
            p = prov()
            set_setting("provider", p)
            keyed = p != "ollama"
            for widget in (entry, save):
                widget.config(state="normal" if keyed else "disabled")
            k = key_get(p) if keyed else None
            cv.itemconfig(key_note, text=(
                "not needed for Ollama" if not keyed else
                "saved in Windows Credential Manager ····"
                + k[-4:] if k else "no key saved - paste one and press Save"))
            cv.itemconfig(test_note, text="")
            mvar.set(current_model(p))
            combo.config(values=())

            def load():             # network: off the UI thread
                names = provider_models(p)
                self.root.after(0, lambda: prov() == p and combo.config(
                    values=names))
            threading.Thread(target=load, daemon=True).start()

        def save_key():
            try:
                key_set(prov(), kvar.get().strip())
            except OSError as e:
                cv.itemconfig(key_note, text=str(e))
                return
            kvar.set("")
            refresh()

        def run_test():
            save_model()             # a typed model, not yet saved
            cv.itemconfig(test_note, text="asking…", fill=DIM)

            def go():
                try:
                    reply = answer("Reply with the single word OK.",
                                   timeout=30).splitlines()[0]
                    ok = not reply.startswith("(")   # "(" = fell back
                except RuntimeError as e:
                    reply, ok = str(e), False
                self.root.after(0, lambda: cv.itemconfig(
                    test_note, text=reply[:90], fill=FG if ok else "#ff8a7a"))
            threading.Thread(target=go, daemon=True).start()

        pvar.trace_add("write", refresh)
        combo.bind("<<ComboboxSelected>>", save_model)
        combo.bind("<Return>", save_model)
        combo.bind("<FocusOut>", save_model)
        entry.bind("<Return>", lambda e: save_key())
        refresh()
        return y + 178

    def _settings_window(self):
        if getattr(self, "_settings_win", None):
            try:                              # already open: bring it forward
                self._settings_win.lift()
                return
            except tk.TclError:
                self._settings_win = None

        general = [
            ("Start with Windows", "Cane opens in the tray when you log in",
             (STARTUP / "Cane.vbs").exists(),
             lambda on: install_startup() if on else remove_startup()),
            # Not "nothing leaves the laptop": off hands the question to
            # Copilot, which is Microsoft's cloud. Cane itself sends nothing.
            ("Cloud answers", "Questions go to the provider below. Off: "
             "Cane opens Copilot instead",
             setting("cloud_answers", True),
             lambda on: set_setting("cloud_answers", on)),
            ("Hover to open", "Touch the top edge above the bar to open it. "
             "Never over full-screen apps",
             setting("hover", True),
             lambda on: set_setting("hover", on)),
            # Tasks are always done in Cane's chat. This only adds a button
            # there - nothing is ever handed to Claude Code automatically.
            ("Claude Code button in chat", "Adds \"Hand to Claude Code\" to the "
             "chat. Nothing goes to it unless you press it",
             setting("claude", False),
             lambda on: set_setting("claude", on)),
        ]
        rows = -(-len(FEATURES) // 2)         # two columns
        pages = [
            ("Personality", "name, how it talks, your rules, its memory",
             lambda back: soul.open_window(self, back=back)),
            ("Blocked sites", "what focus mode closes, and when",
             lambda back: focus.sites_window(self, back=back)),
            ("Chat", "tasks done by your answer provider",
             lambda back: agent.open_window(self, back=back)),
        ]
        h = 72 + 52 * len(general) + 34 * len(pages) + 16 + 208 + 38 + 32 * rows + 44
        w = tk.Toplevel(self.root)
        self._settings_win = w
        cv = make_panel(w, self.W, h)
        x0 = self._x()
        w.geometry("+%d+%d" % (x0 + 40, MARGIN))

        def close(*_):
            self._settings_win = None
            slide(w, x0 + 40, MARGIN, 0.0, steps=8, then=w.destroy)

        self.page_header(w, cv, "SETTINGS", close)

        y = 72
        for i, (label, desc, on, command) in enumerate(general):
            tag = "g%d" % i
            cv.create_text(24, y, text=label, fill=FG, anchor="w",
                           font=FONT_BODY, tags=tag)
            # "nw", not "w": a west anchor centres wrapped text on the line,
            # so a two-line description grew upward over its own label.
            cv.create_text(24, y + 11, text=desc, fill=DIM, anchor="nw",
                           font=FONT_SM, width=self.W - 120, tags=tag)
            self._toggle(cv, self.W - 60, y - 8, on, command, (tag,))
            y += 52

        # Pages: a row with an arrow, and each page has "‹ SETTINGS" back.
        y -= 8
        for i, (label, desc, opener) in enumerate(pages):
            tag = "p%d" % i
            bg = round_rect(cv, 14, y, self.W - 14, y + 30, 8, fill=PANEL,
                            outline="", tags=tag)
            cv.create_text(24, y + 15, text=label, fill=FG, anchor="w",
                           font=FONT_BODY, tags=tag)
            cv.create_text(150, y + 15, text=desc, fill=DIM, anchor="w",
                           font=FONT_SM, tags=tag)
            cv.create_text(self.W - 28, y + 15, text="›", fill=DIM, anchor="e",
                           font=("Segoe UI", 14), tags=tag)

            def go(_, opener=opener):
                close()
                opener(lambda: self.root.after(0, self._settings_window))
            cv.tag_bind(tag, "<Button-1>", go)
            cv.tag_bind(tag, "<Enter>", lambda e, b=bg: (
                cv.itemconfig(b, fill="#1e222a"), cv.config(cursor="hand2")))
            cv.tag_bind(tag, "<Leave>", lambda e, b=bg: (
                cv.itemconfig(b, fill=PANEL), cv.config(cursor="")))
            y += 34
        y += 24

        y = self._answers_section(w, cv, y)

        cv.create_text(24, y, text="FEATURES", fill=DIM, anchor="w",
                       font=("Segoe UI", 8, "bold"))
        y += 30
        for i, (key, label, _) in enumerate(FEATURES):
            col, row = i // rows, i % rows
            fx, fy = 24 + col * (self.W // 2), y + row * 32
            tag = "f_" + key
            cv.create_text(fx + 48, fy + 10, text=label, fill=FG, anchor="w",
                           font=FONT_BODY, tags=tag)
            self._toggle(cv, fx, fy, feature_on(key),
                         lambda on, k=key: set_setting("feature:" + k, on),
                         (tag,))
        cv.create_text(24, h - 22, text="Changes apply straight away",
                       fill=DIM, anchor="w", font=FONT_SM)
        w.focus_force()
        slide(w, x0, MARGIN, 0.97, x_from=x0 + 40)

    def _answer_window(self, question, text, title="ANSWER", limit=4000):
        """A scrollable text panel. As ANSWER it names the model and offers
        Ask Copilot; with another title (a plan's DETAILS) it just shows."""
        if getattr(self, "_close_answer", None):
            self._close_answer()            # one answer at a time
        text = text[:limit]
        is_answer = title == "ANSWER"
        body, wrap = tkfont.Font(font=FONT_BODY), self.W - 48
        # Word wrap breaks early, so pad the estimate 15% rather than clip.
        lines = sum(max(1, -(-int(body.measure(p) * 1.15) // wrap))
                    for p in text.split("\n"))
        full = lines * (body.metrics("linespace") + 2) + 6
        tall = min(full, 420)             # longer than this scrolls instead
        h = 44 + tall + 42
        w, cv, close = self._toast(h)
        self._close_answer = close
        cv.create_text(24, 24, text=title, fill=ACCENT, anchor="w",
                       font=("Segoe UI", 8, "bold"))
        cv.create_text(84, 24, text=question[:44], fill=DIM, anchor="w",
                       font=FONT_SM)
        if is_answer:
            # The model that actually answered: after a fallback the header
            # used to name the provider's model that had just failed.
            fell = re.match(r"\(.* - answered by (.+?)\)\n", text)
            cv.create_text(self.W - 24, 24, anchor="e", fill=DIM,
                           font=FONT_SM,
                           text=fell.group(1) if fell else current_model())

        txt = tk.Text(w, bg=PANEL, fg=FG, font=FONT_BODY, wrap="word",
                      relief="flat", borderwidth=0, highlightthickness=0,
                      padx=0, pady=0, spacing1=2, spacing3=2, cursor="arrow")
        txt.tag_config("code", font=("Consolas", 10), foreground="#cfe3ff",
                       background="#1d2027")
        txt.tag_config("head", font=("Segoe UI", 11, "bold"))
        fill_text(txt, text)
        txt.config(state="disabled")     # read-only, but still scrollable
        # A borderless panel may never take focus, so bind the wheel to the
        # widget itself rather than relying on Tk's focus-follows scrolling.
        txt.bind("<MouseWheel>", lambda e: txt.yview_scroll(-e.delta // 120,
                                                            "units"))
        cv.create_window(24, 44, window=txt, anchor="nw", width=wrap,
                         height=tall)
        if full > tall:
            cv.create_text(24, h - 22, text="scroll for more", fill=DIM,
                           anchor="w", font=FONT_SM)
        if not is_answer:
            cv.create_window(self.W - 24, h - 22, anchor="e",
                             window=self._button(w, "Close", close))
            return
        ask_copilot = lambda: (dispatch({"intent": "copilot", "arg": question},
                                        self), close())
        cv.create_window(self.W - 24, h - 22, anchor="e",
                         window=self._button(w, "Ask Copilot", ask_copilot))
        cv.create_window(self.W - 124, h - 22, anchor="e",
                         window=self._button(w, "Copy", lambda: self.copy(text)))


def reminder_loop(bar):
    """Sweep for due reminders and pop them."""
    while True:
        try:
            for item in due_now():
                bar.root.after(0, lambda i=item: bar.alert(i))
        except Exception:
            pass  # a bad row must never kill the sweep
        time.sleep(CHECK_EVERY)


# ---------------------------------------------------------------- tray

WM_HOTKEY, WM_COMMAND, WM_DESTROY = 0x0312, 0x0111, 0x0002
WM_TRAY, WM_LBUTTONUP, WM_RBUTTONUP = 0x0401, 0x0202, 0x0205
NIM_ADD, NIM_DELETE, NIF_ALL = 0, 2, 0x01 | 0x02 | 0x04
ID_OPEN, ID_ITEMS, ID_QUIT, ID_SETTINGS, ID_CHAT, ID_SOUL = 1, 2, 3, 4, 5, 6
MF_SEPARATOR = 0x0800

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT,
                             wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASS(ctypes.Structure):
    _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR)]


class NOTIFYICONDATA(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND),
                ("uID", wintypes.UINT), ("uFlags", wintypes.UINT),
                ("uCallbackMessage", wintypes.UINT), ("hIcon", wintypes.HICON),
                ("szTip", wintypes.WCHAR * 128), ("dwState", wintypes.DWORD),
                ("dwStateMask", wintypes.DWORD), ("szInfo", wintypes.WCHAR * 256),
                ("uTimeout", wintypes.UINT), ("szInfoTitle", wintypes.WCHAR * 64),
                ("dwInfoFlags", wintypes.DWORD), ("guidItem", ctypes.c_byte * 16),
                ("hBalloonIcon", wintypes.HICON)]


def _tray_icon(u32):
    """cane.ico beside the script if present, else the stock app icon."""
    ico = Path(__file__).parent / "cane.ico"
    if ico.exists():
        h = u32.LoadImageW(None, str(ico), 1, 0, 0, 0x00000010 | 0x00000040)
        if h:
            return h
    # IDI_APPLICATION is MAKEINTRESOURCE(32512): an int cast to a string
    # pointer, not a string and not a plain int.
    return u32.LoadIconW(None, ctypes.cast(32512, wintypes.LPCWSTR))


def tray_loop(bar):
    """Tray icon + global hotkey, sharing one Win32 message loop.

    The hotkey needs a message loop and the tray needs a window; running both
    on one thread keeps it to a single loop and a single shutdown path.
    """
    u32, k32 = ctypes.windll.user32, ctypes.windll.kernel32
    # Handles are 64-bit. Without explicit prototypes ctypes assumes int and
    # truncates them, and CreateWindowExW fails with an OverflowError.
    k32.GetModuleHandleW.restype = wintypes.HMODULE
    k32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    u32.DefWindowProcW.restype = LRESULT
    u32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT,
                                   wintypes.WPARAM, wintypes.LPARAM]
    u32.CreateWindowExW.restype = wintypes.HWND
    u32.CreateWindowExW.argtypes = [
        wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
    u32.CreatePopupMenu.restype = wintypes.HMENU
    u32.LoadIconW.restype = wintypes.HICON
    u32.LoadIconW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR]
    u32.LoadImageW.restype = wintypes.HANDLE
    u32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR,
                               wintypes.UINT, ctypes.c_int, ctypes.c_int,
                               wintypes.UINT]

    def on_message(hwnd, msg, wp, lp):
        if msg == WM_HOTKEY:
            bar.root.after(0, bar.show)
        elif msg == WM_TRAY and lp in (WM_LBUTTONUP, WM_RBUTTONUP):
            if lp == WM_LBUTTONUP:
                bar.root.after(0, bar.show)
            else:
                menu = u32.CreatePopupMenu()
                u32.AppendMenuW(menu, 0, ID_OPEN, "Open Cane\tCtrl+Alt+Space")
                u32.AppendMenuW(menu, 0, ID_CHAT, "Chat")
                u32.AppendMenuW(menu, 0, ID_ITEMS, "Open items")
                u32.AppendMenuW(menu, 0, ID_SOUL, "Personality…")
                u32.AppendMenuW(menu, 0, ID_SETTINGS, "Settings…")
                u32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
                u32.AppendMenuW(menu, 0, ID_QUIT, "Quit Cane")
                pt = wintypes.POINT()
                u32.GetCursorPos(ctypes.byref(pt))
                u32.SetForegroundWindow(hwnd)   # else the menu never dismisses
                u32.TrackPopupMenu(menu, 0x0002, pt.x, pt.y, 0, hwnd, None)
                u32.DestroyMenu(menu)
        elif msg == WM_COMMAND:
            cmd = wp & 0xFFFF
            if cmd == ID_OPEN:
                bar.root.after(0, bar.show)
            elif cmd == ID_ITEMS:
                bar.root.after(0, lambda: bar._list_window(open_items()))
            elif cmd == ID_SETTINGS:
                bar.show_settings()
            elif cmd == ID_CHAT:
                agent.open_window(bar)
            elif cmd == ID_SOUL:
                soul.open_window(bar)
            elif cmd == ID_QUIT:
                u32.DestroyWindow(hwnd)
        elif msg == WM_DESTROY:
            u32.PostQuitMessage(0)
            return 0
        return u32.DefWindowProcW(hwnd, msg, wp, lp)

    proc = WNDPROC(on_message)          # must outlive the loop or Windows crashes
    cls = WNDCLASS()
    cls.lpfnWndProc = proc
    cls.hInstance = k32.GetModuleHandleW(None)
    cls.lpszClassName = "CaneTray"
    if not u32.RegisterClassW(ctypes.byref(cls)):
        return
    hwnd = u32.CreateWindowExW(0, "CaneTray", "Cane", 0, 0, 0, 0, 0,
                               None, None, cls.hInstance, None)
    if not hwnd:
        return

    nid = NOTIFYICONDATA()
    nid.cbSize = ctypes.sizeof(NOTIFYICONDATA)
    nid.hWnd = hwnd
    nid.uID = 1
    nid.uFlags = NIF_ALL
    nid.uCallbackMessage = WM_TRAY
    nid.hIcon = _tray_icon(u32)
    nid.szTip = "Cane - Ctrl+Alt+Space"
    ctypes.windll.shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid))

    if not u32.RegisterHotKey(hwnd, 1, 0x0002 | 0x0001, 0x20):  # CTRL|ALT SPACE
        bar.root.after(0, lambda: bar.status("Ctrl+Alt+Space is taken"))

    msg = wintypes.MSG()
    while u32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        u32.TranslateMessage(ctypes.byref(msg))
        u32.DispatchMessageW(ctypes.byref(msg))

    ctypes.windll.shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
    bar.root.after(0, bar.root.quit)        # Quit chosen: bring the app down


# ---------------------------------------------------------------- autostart

STARTUP = Path(os.path.expandvars(
    r"%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"))


def install_startup():
    """Drop a launcher in the Startup folder. Runs windowless via pythonw."""
    pyw = Path(sys.executable).with_name("pythonw.exe")
    if not pyw.exists():
        pyw = Path(sys.executable)
    target = STARTUP / "Cane.vbs"
    target.write_text(
        "' Starts Cane at login with no console window. Delete to disable.\r\n"
        'CreateObject("WScript.Shell").Run """%s"" ""%s""", 0, False\r\n'
        % (pyw, Path(__file__).resolve()), encoding="utf-8")
    return target


def remove_startup():
    target = STARTUP / "Cane.vbs"
    if target.exists():
        target.unlink()
        return target
    return None


if __name__ == "__main__":
    # python cane.py --media next   -> send one media key and exit.
    # Isolates key injection from the router when a command "does nothing".
    if "--media" in sys.argv:
        name = sys.argv[sys.argv.index("--media") + 1]
        if name not in MEDIA_KEYS:
            sys.exit("unknown key. try: " + " ".join(MEDIA_KEYS))
        print("focus your player - sending " + name + " in 3s")
        time.sleep(3)
        send_media(MEDIA_KEYS[name])
        print("sent")
        sys.exit()

    if "--install-startup" in sys.argv:
        print("installed: " + str(install_startup()))
        sys.exit()
    if "--remove-startup" in sys.argv:
        gone = remove_startup()
        print("removed: " + str(gone) if gone else "was not installed")
        sys.exit()

    bar = Bar()
    threading.Thread(target=tray_loop, args=(bar,), daemon=True).start()
    threading.Thread(target=reminder_loop, args=(bar,), daemon=True).start()
    threading.Thread(target=warmup, daemon=True).start()
    threading.Thread(target=index_apps, daemon=True).start()
    threading.Thread(target=hover.loop, args=(bar,), daemon=True).start()
    threading.Thread(target=focus.watch, args=(bar,), daemon=True).start()
    soul.ensure()
    # Start hidden: it lives in the tray, and at login nothing should appear.
    if "--show" in sys.argv:
        bar.show()
    bar.root.mainloop()
