"""Activity tracking and insights (phase 6). OFF by default.

When switched on, the window in front is sampled every few seconds and
added up per minute: which app, which site (for browsers, from the tab
title), and whether you were active or idle. Nothing else - no keystrokes,
no screenshots, no page contents. It stays in tavinor.db beside tavinor.py and is
deleted after KEEP_DAYS. Settings has a "delete everything" button.

It answers: what did I do today, when am I most productive, how long on
YouTube. An optional morning check-in summarises yesterday.
"""
import ctypes
import re
import sqlite3
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import tavinor
import winsys

DB = tavinor.DATA / "tavinor.db"
EVERY = 5               # seconds between samples
IDLE_AFTER = 120        # seconds without input before time counts as idle
KEEP_DAYS = 30
CHECKIN_AFTER = 8       # morning check-in from 08:00
NAMES = {"code": "VS Code", "chrome": "Chrome", "msedge": "Edge", "brave": "Brave",
         "firefox": "Firefox", "explorer": "File Explorer", "winword": "Word",
         "excel": "Excel", "powerpnt": "PowerPoint", "outlook": "Outlook",
         "olk": "Outlook", "teams": "Teams", "ms-teams": "Teams",
         "whatsapp": "WhatsApp", "windowsterminal": "Terminal", "slack": "Slack",
         "spotify": "Spotify", "notepad": "Notepad", "pythonw": "Tavinor"}
SKIP = {"lockapp.exe", "logonui.exe", ""}      # lock screen, sign-in

_LOCK = threading.Lock()


def db():
    c = sqlite3.connect(DB, timeout=5)
    c.execute("CREATE TABLE IF NOT EXISTS activity (minute TEXT, app TEXT, "
              "site TEXT, title TEXT, active INTEGER, idle INTEGER, "
              "PRIMARY KEY (minute, app, site))")
    return c


def on():
    return bool(tavinor.setting("activity", False))


def idle_seconds():
    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]
    li = LASTINPUTINFO(cbSize=ctypes.sizeof(LASTINPUTINFO))
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(li)):
        return 0
    return max(0, (ctypes.windll.kernel32.GetTickCount() - li.dwTime) // 1000)



def app_name(exe):
    stem = re.sub(r"\.exe$", "", exe.lower())
    return NAMES.get(stem, stem[:1].upper() + stem[1:] if stem else "")


def site_of(fg):
    """The site a browser is showing: the title's last part ("Lofi - YouTube"
    -> "youtube"). "" for anything that isn't a browser."""
    if not fg["browser"]:
        return ""
    segs = winsys.segments(fg["title"])
    return segs[-1][:60] if segs else ""


def record(fg, idle, seconds=EVERY, now=None):
    """Add one sample. Pure enough to test: pass fg and idle in."""
    if fg["exe"].lower() in SKIP:       # the lock screen is not screen time
        return
    now = now or datetime.now()
    minute = now.strftime("%Y-%m-%dT%H:%M")
    is_idle = idle >= IDLE_AFTER
    app = app_name(fg["exe"])
    site = "" if is_idle else site_of(fg)
    with _LOCK, db() as c:
        c.execute("INSERT INTO activity VALUES (?,?,?,?,?,?) ON CONFLICT(minute, "
                  "app, site) DO UPDATE SET active = active + excluded.active, "
                  "idle = idle + excluded.idle, title = excluded.title",
                  (minute, app, site, fg["title"][:120],
                   0 if is_idle else seconds, seconds if is_idle else 0))


def prune(now=None):
    cut = ((now or datetime.now()) - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
    with _LOCK, db() as c:
        c.execute("DELETE FROM activity WHERE minute < ?", (cut,))


def wipe():
    with _LOCK, db() as c:
        c.execute("DELETE FROM activity")


def watch(bar):
    last_prune = None
    while True:
        try:
            if on():
                fg = winsys.foreground()
                record(fg, idle_seconds())
                today = date.today()
                if last_prune != today:
                    prune()
                    last_prune = today
                maybe_checkin(bar)
        except Exception:
            pass                       # a bad sample must never stop the loop
        time.sleep(EVERY)


# ------------------------------------------------------------- insights

def _rows(sql, args=()):
    with _LOCK, db() as c:
        return c.execute(sql, args).fetchall()


def day_summary(day):
    """[(label, minutes)] for one day, browsers split by site, biggest first."""
    rows = _rows("SELECT app, site, SUM(active) FROM activity WHERE minute LIKE ? "
                 "AND active > 0 GROUP BY app, site", (day.isoformat() + "%",))
    out = [(("%s (%s)" % (site.title(), app)) if site else app, round(s / 60))
           for app, site, s in rows]
    return sorted([r for r in out if r[1] > 0], key=lambda r: -r[1])


def active_minutes(day):
    r = _rows("SELECT SUM(active) FROM activity WHERE minute LIKE ?",
              (day.isoformat() + "%",))
    return round((r[0][0] or 0) / 60)


def site_minutes(site, since):
    """Minutes on a site (or app) since a date. "youtube" matches the site
    YouTube in any browser, and also an app called YouTube."""
    key = site.lower().strip()
    r = _rows("SELECT SUM(active) FROM activity WHERE minute >= ? AND "
              "(lower(site) = ? OR lower(app) = ?)",
              (since.isoformat(), key, key))
    return round((r[0][0] or 0) / 60)


def distracting():
    import focus
    return {focus.site_key(s) for s in focus.load()["sites"]} | {"twitter"}


def best_hours(days=14, today=None):
    """(start_hour, minutes) of the best two-hour window for focused work
    over the last `days`, or None without enough data. Time on distracting
    sites doesn't count as productive."""
    today = today or date.today()
    since = (today - timedelta(days=days)).isoformat()
    bad = distracting()
    rows = _rows("SELECT substr(minute, 12, 2), site, SUM(active) FROM activity "
                 "WHERE minute >= ? GROUP BY 1, 2", (since,))
    hours = [0] * 24
    for h, site, s in rows:
        if site.lower() not in bad:
            hours[int(h)] += s
    if sum(hours) < 3600:            # under an hour of data says nothing
        return None
    best = max(range(23), key=lambda h: hours[h] + hours[h + 1])
    return best, round((hours[best] + hours[best + 1]) / 60)


def _hour(h):
    return "%d %s" % (h % 12 or 12, "AM" if h < 12 else "PM")


def maybe_checkin(bar, now=None):
    """Once a day after 08:00: yesterday in one toast."""
    now = now or datetime.now()
    if (not tavinor.setting("checkin", True) or now.hour < CHECKIN_AFTER
            or tavinor.setting("checkin_day") == now.date().isoformat()):
        return
    y = now.date() - timedelta(days=1)
    mins = active_minutes(y)
    tavinor.set_setting("checkin_day", now.date().isoformat())
    if mins < 10:
        return                          # nothing worth saying about yesterday
    top = ", ".join("%s %dm" % (a, m) for a, m in day_summary(y)[:3])
    text = "Yesterday: %dh %02dm active - %s" % (mins // 60, mins % 60, top)
    bar.root.after(0, lambda: bar.alert({"text": text[:120]}, title="CHECK-IN"))


# ------------------------------------------------------------- commands

def quick(t):
    if re.fullmatch(r"what (?:did|have) i (?:do|done|been doing)(?: today)?|"
                    r"(?:my )?(?:screen ?time|activity)(?: today)?|"
                    r"how (?:did|was) my day", t):
        return {"intent": "activity", "action": "today"}
    if re.fullmatch(r"what did i do yesterday|(?:my )?(?:screen ?time|activity) "
                    r"yesterday", t):
        return {"intent": "activity", "action": "yesterday"}
    if re.fullmatch(r"when am i (?:most )?productive|what are my best hours|"
                    r"when (?:should|do) i (?:do|work on) (?:hard|deep|focused) "
                    r"(?:work|tasks?)", t):
        return {"intent": "activity", "action": "productive"}
    m = re.fullmatch(r"how (?:long|much time|many (?:minutes|hours))(?:(?: have| did)? "
                     r"i(?: spent?| been| was)?)? (?:on|in|using) (?P<site>[a-z0-9 .]+?)"
                     r"(?P<when> today| this week| yesterday)?", t)
    if m:
        return {"intent": "activity", "action": "site", "site": m["site"].strip(),
                "when": (m["when"] or " today").strip()}
    return None


def dispatch(intent, ui=None):
    action = intent.get("action") or ""
    if not on():
        return ("activity tracking is off - switch it on in Settings "
                "(it stays on this laptop)")
    if action in ("today", "yesterday"):
        day = date.today() - timedelta(days=action == "yesterday")
        rows = day_summary(day)
        mins = active_minutes(day)
        if not rows:
            return "nothing recorded for %s yet" % action
        text = "\n".join("%3d min  %s" % (m, label) for label, m in rows[:15])
        if ui:
            ui.show_answer("activity " + action, "%dh %02dm active\n%s" % (
                mins // 60, mins % 60, text))
        return "%dh %02dm active %s - most on %s" % (
            mins // 60, mins % 60, action,
            ", ".join("%s %dm" % r for r in rows[:3]))
    if action == "productive":
        b = best_hours()
        if not b:
            return "not enough activity yet - give it a few days"
        h, m = b
        return "you're most productive %s to %s (%d focused min there, last 14 days)" % (
            _hour(h), _hour(h + 2), m)
    if action == "site":
        site = str(intent.get("site") or intent.get("arg") or "")
        when = str(intent.get("when") or "today")
        since = {"today": date.today(), "yesterday": date.today() - timedelta(days=1),
                 "this week": date.today() - timedelta(days=date.today().weekday())
                 }.get(when, date.today())
        mins = site_minutes(site, since)
        if when == "yesterday":
            mins -= site_minutes(site, date.today())
        return "%d min on %s %s" % (mins, site, when)
    return "unknown activity question"


try:
    import agent
    agent.EXTRA["activity"] = (
        '{"intent":"activity","action":"today|yesterday|productive"}  what the user '
        'did / their best hours; or "action":"site","site":"youtube","when":"today|'
        'yesterday|this week"', True)
except ImportError:
    pass
