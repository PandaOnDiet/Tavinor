"""What the Windows widget (lock screen and widget board) shows.

Tavinor writes widget.json in its data folder every few minutes and after
any widget setting changes; the widget provider (widget/, C#) only reads
it. Its buttons come back through act(), over the bridge with the widget's
own token (bridge.WIDGET_TOKEN): add a task, tick one off, start or stop
focus, set the city. Nothing else.

    write()     # build the snapshot and write it now
    loop()      # every 5 minutes, in a thread
    act(tool, args)   # a widget button
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


# ------------------------------------------------- "Yours": a widget you describe
#
# You describe it in words ("meeting in red when it's under 10 min, the focus
# timer while focusing, habits after 9 pm"); the model turns that into rules,
# and clean() keeps only rules built from the words below. The first rule
# whose conditions all hold, and whose thing has something to show, is shown.

# Conditions: kind -> what its value is ("" = none).
CONDS = {"meeting_within": "minutes", "focus_on": "", "after": "HH:MM", "before": "HH:MM",
         "days": "days", "tasks_over": "count", "habits_left": "", "rain_over": "percent",
         "aqi_over": "count", "temp_over": "degrees", "temp_under": "degrees"}
THINGS = ("meeting", "focus", "tasks", "habits", "weather", "air", "sun", "text")
COLORS = ("default", "accent", "good", "warning", "attention")   # what Windows allows
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
MAX_RULES = 6

RULES_SYSTEM = """You turn a description of a small lock-screen widget into JSON rules.
Reply with only JSON: {"rules": [rule, ...]}, at most 6 rules, most specific first.
A rule: {"when": [condition, ...], "show": thing, "color": color, "big": true|false, "text": "..."}
The widget shows the first rule whose conditions all hold. A rule with "when": [] always holds,
so put it last as the fallback.
Conditions (all must hold):
  {"if": "meeting_within", "n": minutes}   next meeting starts within n minutes
  {"if": "focus_on"}                        a focus session is running
  {"if": "after", "time": "HH:MM"}          now is at or after this time (24h)
  {"if": "before", "time": "HH:MM"}         now is before this time (24h)
  {"if": "days", "days": ["mon", ...]}      today is one of these (mon tue wed thu fri sat sun)
  {"if": "tasks_over", "n": count}          more than n tasks are open
  {"if": "habits_left"}                     some habits are not done today
  {"if": "rain_over", "n": percent}         chance of rain today is over n
  {"if": "aqi_over", "n": aqi}              air quality index is over n
  {"if": "temp_over", "n": degrees}         temperature is over n
  {"if": "temp_under", "n": degrees}        temperature is under n
Things to show: meeting, focus, tasks, habits, weather, air, sun, text.
"text" shows the rule's own "text" (at most 40 characters); other things ignore "text".
Colors: default, accent (blue), good (green), warning (yellow/orange), attention (red).
"big": true for a large first line.
Each part of the description is its own rule with only its own conditions: never copy a
condition from one part into another.
Use only these words. If part of the description can't be expressed with them, leave it out."""


def rules():
    r = tavinor.setting("widget_rules")
    return r if isinstance(r, list) else []


def _hhmm(v):
    v = str(v).strip()
    try:
        return datetime.strptime(v, "%H:%M").strftime("%H:%M")
    except ValueError:
        raise ValueError("time %r is not HH:MM" % v[:10]) from None


def _cond(c):
    """One condition, checked and normalised. Raises ValueError."""
    if not isinstance(c, dict) or c.get("if") not in CONDS:
        raise ValueError("unknown condition %r" % (c.get("if") if isinstance(c, dict) else c,))
    kind, want = c["if"], CONDS[c["if"]]
    if want == "":
        return {"if": kind}
    if want == "HH:MM":
        return {"if": kind, "time": _hhmm(c.get("time"))}
    if want == "days":
        days = [str(d).strip().lower()[:3] for d in (c.get("days") or [])]
        if not days or any(d not in DAYS for d in days):
            raise ValueError("days must be some of %s" % ", ".join(DAYS))
        return {"if": kind, "days": sorted(set(days), key=DAYS.index)}
    try:
        n = float(c.get("n"))
    except (TypeError, ValueError):
        raise ValueError("%s needs a number" % kind) from None
    lo, hi = {"minutes": (1, 720), "count": (0, 999), "percent": (0, 100),
              "degrees": (-60, 60)}[want]
    if not lo <= n <= hi:
        raise ValueError("%s must be %g to %g" % (kind, lo, hi))
    return {"if": kind, "n": int(n) if n == int(n) else n}


def clean(raw):
    """The model's reply -> (rules, skipped). A rule with anything unknown is
    dropped whole: dropping just the bad condition would make it fire more
    often than you asked. Raises ValueError when nothing usable is left."""
    if isinstance(raw, dict):
        raw = raw.get("rules")
    if not isinstance(raw, list):
        raise ValueError("no rules in the reply")
    out, skipped = [], 0
    for r in raw[:MAX_RULES * 2]:
        try:
            if not isinstance(r, dict) or r.get("show") not in THINGS:
                raise ValueError("unknown thing to show")
            when = r.get("when") or []
            if not isinstance(when, list) or len(when) > 4:
                raise ValueError("bad conditions")
            rule = {"when": [_cond(c) for c in when], "show": r["show"],
                    "color": r.get("color") if r.get("color") in COLORS else "default",
                    "big": bool(r.get("big"))}
            if r["show"] == "text":
                rule["text"] = " ".join(str(r.get("text") or "").split())[:40]
                if not rule["text"]:
                    raise ValueError("text rule with no text")
            out.append(rule)
        except ValueError:
            skipped += 1
    if not out:
        raise ValueError("couldn't make a rule from that, try naming what to show and when")
    return out[:MAX_RULES], skipped + max(0, len(out) - MAX_RULES)


def describe(text):
    """Words -> saved rules. Returns the read-back; raises RuntimeError
    (model unreachable) or ValueError (nothing usable)."""
    text = str(text).strip()[:600]
    reply, _ = tavinor.complete(RULES_SYSTEM, [{"role": "user", "content": text}], 60)
    start, end = reply.find("{"), reply.rfind("}")
    try:
        raw = json.loads(reply[start:end + 1]) if 0 <= start < end else None
    except ValueError:
        raw = None
    got, skipped = clean(raw)
    tavinor.set_setting("widget_rules", got)
    tavinor.set_setting("widget_rules_text", text)
    refresh()
    return got, skipped


def explain(rule):
    """One rule in plain words, so you can check what the model understood."""
    say = {"meeting_within": "a meeting starts within {n} min", "focus_on": "focus is running",
           "after": "it's {time} or later", "before": "it's before {time}",
           "tasks_over": "more than {n} tasks are open", "habits_left": "habits are left today",
           "rain_over": "rain chance is over {n}%", "aqi_over": "air quality is over {n}",
           "temp_over": "it's over {n}°", "temp_under": "it's under {n}°"}
    parts = [", ".join(d.title() for d in c["days"]) if c["if"] == "days"
             else say[c["if"]].format(**c) for c in rule["when"]]
    what = "“%s”" % rule["text"] if rule["show"] == "text" else "the " + rule["show"]
    style = [w for w in ("big" if rule["big"] else "", {"default": "", "accent": "blue", "good": "green",
             "warning": "orange", "attention": "red"}[rule["color"]]) if w]
    return ("When %s: " % " and ".join(parts) if parts else "Otherwise: ") \
        + "show " + what + (" (%s)" % ", ".join(style) if style else "")


def _holds(c, now, f):
    k = c["if"]
    if k == "meeting_within":
        return f["meeting_in"] is not None and f["meeting_in"] <= c["n"]
    if k == "focus_on":
        return f["focus"] is not None
    if k == "after":
        return now.strftime("%H:%M") >= c["time"]
    if k == "before":
        return now.strftime("%H:%M") < c["time"]
    if k == "days":
        return DAYS[now.weekday()] in c["days"]
    if k == "tasks_over":
        return f["open"] > c["n"]
    if k == "habits_left":
        return f["habits_left"] > 0
    w = f["weather"] or {}
    v = w.get({"rain_over": "rain", "aqi_over": "aqi"}.get(k, "temp"))
    if not isinstance(v, (int, float)):
        return False                               # no weather: the rule can't fire
    return v > c["n"] if k != "temp_under" else v < c["n"]


def _lines(thing, rule, f, hide):
    """(first line, second line) for a thing, or None when it has nothing."""
    w = f["weather"] or {}
    if thing == "meeting" and f["meeting"]:
        m, mins = f["meeting"], f["meeting_in"]
        when = "now" if mins <= 0 else "in %d min" % mins if mins < 60 else m["time"]
        return when, "Meeting at " + m["time"] if hide else m["title"] or "Meeting"
    if thing == "focus" and f["focus"]:
        return "%d min" % f["focus"]["minutes_left"], "Focus" if hide else f["focus"]["task"] or "Focus"
    if thing == "tasks":
        return ("%d open" % f["open"] if f["open"] else "All done"), \
            ("tasks" if hide or not f["top"] else f["top"])
    if thing == "habits" and f["habits_of"]:
        return "%d of %d" % (f["habits_of"] - f["habits_left"], f["habits_of"]), "habits today"
    if thing == "weather" and "temp" in w:
        return "%s°" % w["temp"], "%s · %s%% rain" % (w.get("summary", ""), w.get("rain", 0))
    if thing == "air" and isinstance(w.get("aqi"), (int, float)):
        return "AQI %s" % w["aqi"], w.get("aqi_label", "")
    if thing == "sun" and w.get("sunset"):
        return "Sunset %s" % w["sunset"], "Sunrise %s" % w["sunrise"]
    if thing == "text":
        return rule["text"], ""
    return None


def smart(now, day, hide):
    """What the "Yours" widget shows now: the first rule that holds and has
    something to show. None when no rules are set or none applies."""
    rs = rules()
    if not rs:
        return None
    import weather
    w, place = None, tavinor.setting("widget_place")
    if place and any(r["show"] in ("weather", "air", "sun") or
                     any(c["if"] in ("rain_over", "aqi_over", "temp_over", "temp_under") for c in r["when"])
                     for r in rs):
        try:
            w = weather.get(place, tavinor.setting("widget_units", "c"))   # cached 15 min
        except (RuntimeError, OSError, ValueError, KeyError):
            w = None
    cal = day.get("calendar") if isinstance(day.get("calendar"), list) else []
    up = [e for e in cal if not e["all_day"] and e["start"][:16] >= now.isoformat()[:16]]
    meeting, meeting_in = None, None
    if up:
        e = up[0]
        meeting = {"time": e["start"][11:16], "title": e["title"]}
        meeting_in = int((datetime.fromisoformat(e["start"][:16]) - now.replace(second=0, microsecond=0))
                         .total_seconds() // 60)
    top = [t["text"] for t in day.get("tasks", []) if not t["subtask"]]
    hs = day.get("habits") or []
    f = {"meeting": meeting, "meeting_in": meeting_in, "focus": day.get("focus"), "open": len(top),
         "top": top[0] if top else "", "habits_of": len(hs),
         "habits_left": sum(not h["done_today"] for h in hs), "weather": w}
    for i, r in enumerate(rs):
        if all(_holds(c, now, f) for c in r["when"]):
            lines = _lines(r["show"], r, f, hide)
            if lines:
                return {"line1": lines[0], "line2": lines[1], "color": r["color"], "big": r["big"], "rule": i}
    return None


def snapshot(now=None):
    import bridge
    import weather
    now = now or datetime.now()
    day = json.loads(bridge.get_day())
    hide = private()
    out = {"updated": now.strftime("%H:%M"), "date": now.strftime("%a %d %b"),
           "face": getattr(bridge, "_FACE", "idle"), "private": hide}
    try:
        out["smart"] = smart(now, day, hide)
    except (KeyError, TypeError, ValueError):       # a bad rule never blanks the other widgets
        out["smart"] = None

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
        # `list` is the large size's to-do list: names always, because the
        # lock screen doesn't show the large size; `top` follows Hide names.
        out["tasks"] = {"open": len(top), "top": [] if hide else top[:3],
                        "list": [{"key": i["created"], "text": i["text"]}
                                 for i in tavinor.open_items() if not i.get("parent")][:6]}
    if shown("focus") and day.get("focus"):
        f = day["focus"]
        out["focus"] = {"minutes_left": f["minutes_left"], "ends": f.get("ends", ""),
                        "task": "" if hide else f["task"]}
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


def act(tool, args):
    """A widget button. {"ok": True} or {"ok": False, "error": shown on the card}."""
    import bridge
    if tool == "add":
        text = str(args.get("text", "")).strip()[:200]
        if not text:
            return {"ok": False, "error": "Type a task first"}
        out = bridge.run("add_task", {"text": text})
    elif tool == "done":
        out = bridge.mark_done_key(str(args.get("key", "")))
        if out is None:
            return {"ok": False, "error": "That task is already done"}
    elif tool == "focus":
        minutes = int(args.get("minutes", 25))
        if not 1 <= minutes <= 480:
            return {"ok": False, "error": "Focus is 1 to 480 minutes"}
        out = bridge.run("start_focus", {"minutes": minutes})
    elif tool == "stop":
        import focus
        out = focus.stop() and "stopped"
    elif tool == "place":
        import uisettings
        r = uisettings.act(None, "widget", "widget_place", str(args.get("value", ""))[:80])
        if r.get("ok") is False:
            return {"ok": False, "error": r["note"].capitalize()}
        out = "city set"
    else:
        return {"ok": False, "error": "Unknown widget action"}
    refresh()
    return {"ok": True, "text": str(out or "")}


def refresh():
    """Write again now (a setting changed), from the loop's thread."""
    _wake.set()


def loop():
    while True:
        try:
            write()
        except Exception:                      # never take Tavinor down over a widget
            pass
        # "Yours" counts down to meetings and switches at set times: every minute.
        _wake.wait(60 if rules() else EVERY)
        _wake.clear()


def start():
    threading.Thread(target=loop, daemon=True).start()
