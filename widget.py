"""What the Windows widget (lock screen and widget board) shows.

Tavinor writes widget.json in its data folder every few minutes and after
any widget setting changes; the widget provider (widget/, C#) only reads
it. No port, no token: the widget can show, never act.

    write()     # build the snapshot and write it now
    loop()      # every 5 minutes, in a thread
"""
import json
import os
import threading
import time
from datetime import datetime

import tavinor

FILE = tavinor.DATA / "widget.json"
EVERY = 5 * 60
# What can be shown, in order, with its default.
SHOW = [("weather", "Weather now", True), ("details", "Feels like, humidity, wind", True),
        ("air", "Air quality", True), ("sun", "Sunrise and sunset", False),
        ("meeting", "Next meeting", True), ("tasks", "Open tasks", True),
        ("focus", "Focus timer", True), ("habits", "Habits today", False)]
_wake = threading.Event()


def shown(key):
    return bool(tavinor.setting("widget_show:" + key, dict((k, d) for k, _, d in SHOW)[key]))


def private():
    """Lock screen: anyone at the desk can read it, so names are hidden by default."""
    return bool(tavinor.setting("widget_private", True))


def snapshot(now=None):
    import bridge
    import weather
    now = now or datetime.now()
    day = json.loads(bridge.get_day())
    hide = private()
    out = {"updated": now.strftime("%H:%M"), "date": now.strftime("%a %d %b"),
           "face": getattr(bridge, "_FACE", "idle"), "private": hide}

    place = tavinor.setting("widget_place")
    if shown("weather") and place:
        try:
            w = weather.get(place, tavinor.setting("widget_units", "c"))
            if not shown("details"):
                w = {k: v for k, v in w.items() if k not in ("feels", "humidity", "wind", "wind_unit")}
            if not shown("air"):
                w = {k: v for k, v in w.items() if k not in ("aqi", "aqi_label")}
            if not shown("sun"):
                w = {k: v for k, v in w.items() if k not in ("sunrise", "sunset")}
            out["weather"] = w
        except (RuntimeError, OSError, ValueError, KeyError):
            out["weather"] = {"error": "Weather unavailable (offline?)"}
    elif shown("weather"):
        out["weather"] = {"error": "Set a city in Tavinor: Settings > Widget"}

    cal = day.get("calendar")
    if shown("meeting") and isinstance(cal, list):
        upcoming = [e for e in cal if not e["all_day"] and e["start"] >= now.isoformat()[:16]]
        if upcoming:
            e = upcoming[0]
            out["meeting"] = {"time": e["start"][11:16], "title": "" if hide else e["title"]}
    if shown("tasks"):
        top = [t["text"] for t in day["tasks"] if not t["subtask"]]
        out["tasks"] = {"open": len(top), "top": [] if hide else top[:3]}
    if shown("focus") and day.get("focus"):
        f = day["focus"]
        out["focus"] = {"minutes_left": f["minutes_left"], "task": "" if hide else f["task"]}
    if shown("habits") and day.get("habits"):
        hs = day["habits"]
        out["habits"] = {"done": sum(h["done_today"] for h in hs), "of": len(hs)}
    return out


def write():
    """Write atomically, so the widget never reads half a file."""
    data = json.dumps(snapshot(), ensure_ascii=False)
    tmp = FILE.with_suffix(".tmp")
    tmp.write_text(data, encoding="utf-8")
    os.replace(tmp, FILE)


def refresh():
    """Write again now (a setting changed), from the loop's thread."""
    _wake.set()


def loop():
    while True:
        try:
            write()
        except Exception:                      # never take Tavinor down over a widget
            pass
        _wake.wait(EVERY)
        _wake.clear()


def start():
    threading.Thread(target=loop, daemon=True).start()
