"""Focus mode: one task, a countdown, and the distracting sites closed until
it ends. Plus scheduled blocks ("block youtube 9-12 on weekdays") and a
"let me through" override.

State lives in focus.json beside cane.py, hand-editable like notes.json.

Closing a tab is the one destructive thing here, so it is fenced in: the
window must be a browser, must still be the foreground window, and its title
must still name the blocked site at the moment Ctrl+W is sent. After a close
there is a cooldown, because the title of a closing tab lingers for a beat
and a second Ctrl+W would land on an innocent tab.
"""
import json
import re
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import cane
import winsys

STATE = Path(__file__).with_name("focus.json")
DEFAULT_SITES = ["youtube", "twitch", "x"]
ALIASES = {"x": {"x", "twitter"}, "twitter": {"x", "twitter"}}
DEFAULT_MIN = 25
POLL = 1.5                 # seconds between foreground checks
COOLDOWN = 3.0             # seconds after a close before closing again
LET_THROUGH = 5            # minutes "let me through" lasts
DAYS = {"weekdays": [0, 1, 2, 3, 4], "weekends": [5, 6]}
ALL_DAYS = list(range(7))

_LOCK = threading.Lock()
_allow = {}                # site -> datetime until which it is let through


# ------------------------------------------------------------- state

def load():
    try:
        s = json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        s = {}
    if not isinstance(s, dict):
        s = {}
    sites = s.get("sites")
    s["sites"] = ([str(x).lower() for x in sites if str(x).strip()]
                  if isinstance(sites, list) else list(DEFAULT_SITES))
    s["blocks"] = [b for b in s.get("blocks") or [] if _valid_block(b)]
    s["history"] = [h for h in s.get("history") or [] if isinstance(h, dict)]
    sess = s.get("session")
    s["session"] = sess if isinstance(sess, dict) and sess.get("ends") else None
    return s


def save(s):
    s["history"] = s["history"][-200:]
    STATE.write_text(json.dumps(s, indent=1), encoding="utf-8")


def _valid_block(b):
    return (isinstance(b, dict) and isinstance(b.get("site"), str)
            and _hm(b.get("start")) is not None and _hm(b.get("end")) is not None
            and isinstance(b.get("days"), list))


def _hm(v):
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", str(v or ""))
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        return None
    return int(m.group(1)) * 60 + int(m.group(2))


def _iso(dt):
    return dt.isoformat(timespec="seconds")


# ------------------------------------------------------------- sessions

def start(task="", minutes=DEFAULT_MIN, now=None):
    now = now or datetime.now()
    minutes = int(minutes)
    if not 1 <= minutes <= 480:
        raise ValueError("a focus session has to be 1 to 480 minutes")
    with _LOCK:
        s = load()
        if s["session"]:
            _end(s, "stopped", now)          # a new session replaces the old
        s["session"] = {"task": task.strip()[:80], "minutes": minutes,
                        "started": _iso(now),
                        "ends": _iso(now + timedelta(minutes=minutes))}
        # Linked to a real open task when the words name one, so finishing
        # can offer to tick it off.
        hit = link(task)
        if hit:
            s["session"]["task"] = hit["text"][:80]
            s["session"]["task_id"] = hit.get("id") or ""
            s["session"]["task_created"] = hit["created"]
        save(s)
        return s["session"]


def _end(s, how, now):
    sess = s["session"]
    started = datetime.fromisoformat(sess["started"])
    ends = datetime.fromisoformat(sess["ends"])
    stop_at = min(now, ends)
    s["history"].append({"task": sess.get("task", ""),
                         "task_id": sess.get("task_id", ""),
                         "task_created": sess.get("task_created", ""),
                         "planned": sess.get("minutes"),
                         "minutes": max(0, round((stop_at - started)
                                                 .total_seconds() / 60)),
                         "started": sess["started"], "ended": _iso(stop_at),
                         "state": how})
    s["session"] = None
    return s["history"][-1]


def stop(now=None):
    now = now or datetime.now()
    with _LOCK:
        s = load()
        if not s["session"]:
            return None
        h = _end(s, "stopped", now)
        save(s)
        return h


def current(now=None):
    """The running session with minutes left, or None."""
    now = now or datetime.now()
    sess = load()["session"]
    if not sess:
        return None
    left = (datetime.fromisoformat(sess["ends"]) - now).total_seconds() / 60
    return dict(sess, left=max(0, left)) if left > 0 else None


def tick(now=None):
    """Finish a session whose time is up. Returns its history row, or None."""
    now = now or datetime.now()
    with _LOCK:
        s = load()
        sess = s["session"]
        if not sess or datetime.fromisoformat(sess["ends"]) > now:
            return None
        h = _end(s, "finished", now)
        save(s)
        return h


def recover(now=None):
    """At startup: a session that ended while Tavinor wasn't running can't be
    called finished - nobody saw it through - so it is "abandoned"."""
    now = now or datetime.now()
    with _LOCK:
        s = load()
        sess = s["session"]
        if sess and datetime.fromisoformat(sess["ends"]) < now - timedelta(minutes=2):
            _end(s, "abandoned", now)
            save(s)


# ------------------------------------------------------------- blocking

def site_key(site):
    site = site.lower().strip()
    site = re.sub(r"^(https?://)?(www\.)?", "", site)
    return re.sub(r"\.(com|tv|org|net|in)$", "", site)


def matches(title, site):
    """Does this browser title show `site`? Whole title segments only:
    "Lofi - YouTube" is YouTube, "Notes about YouTube - Google Docs" is not,
    and "Song - YouTube Music" is not YouTube."""
    names = ALIASES.get(site, {site})
    for seg in winsys.segments(title):
        seg = re.sub(r"\.(com|tv)$", "", seg)
        if seg in names:
            return True
    return False


def scheduled(now=None):
    """Sites a schedule blocks right now."""
    now = now or datetime.now()
    m = now.hour * 60 + now.minute
    out = set()
    for b in load()["blocks"]:
        a, z = _hm(b["start"]), _hm(b["end"])
        day = now.weekday()
        if a < z:
            on = day in b["days"] and a <= m < z
        else:                                   # runs past midnight
            on = ((day in b["days"] and m >= a)
                  or ((day - 1) % 7 in b["days"] and m < z))
        if on:
            out.add(site_key(b["site"]))
    return out


def blocked_now(now=None):
    now = now or datetime.now()
    sites = set(scheduled(now))
    if current(now):
        sites |= {site_key(x) for x in load()["sites"]}
    return {x for x in sites if _allow.get(x, now) <= now}


def let_through(site, now=None):
    _allow[site] = (now or datetime.now()) + timedelta(minutes=LET_THROUGH)


def which_site(title, sites):
    return next((x for x in sorted(sites) if matches(title, x)), None)


def close_tab(fg, site):
    """Ctrl+W to the browser, only if nothing changed since we looked."""
    now = winsys.foreground()
    if (now["hwnd"] != fg["hwnd"] or not now["browser"]
            or not matches(now["title"], site)):
        return False
    u32 = winsys.u32
    u32.keybd_event(0x11, 0, 0, 0)          # Ctrl down
    u32.keybd_event(0x57, 0, 0, 0)          # W
    u32.keybd_event(0x57, 0, 2, 0)
    u32.keybd_event(0x11, 0, 2, 0)          # Ctrl up
    return True


def watch(bar):
    """Background loop: finish sessions, close blocked tabs, nudge."""
    recover()
    last_close = 0.0
    while True:
        try:
            done = tick()
            if done:
                bar.root.after(0, lambda d=done: finished(bar, d))
            sites = blocked_now()
            if sites and time.monotonic() - last_close > COOLDOWN:
                fg = winsys.foreground()
                site = fg["browser"] and which_site(fg["title"], sites)
                if site and close_tab(fg, site):
                    last_close = time.monotonic()
                    bar.root.after(0, lambda x=site: nudge(bar, x))
        except Exception:
            pass                        # a bad read must never stop the loop
        time.sleep(POLL)


def link(task):
    """The open item a focus task names: the same text, or a phrase of 4+
    characters inside it. Stricter than "done X" matching - "focus on b"
    must not grab whatever task contains the letter b."""
    words = " ".join(task.lower().split())
    if not words:
        return None
    items = cane.open_items()
    return (next((i for i in items if i["text"].lower() == words), None)
            or (next((i for i in items if words in i["text"].lower()), None)
                if len(words) >= 4 else None))


def task_of(h):
    """The open item a finished session was for, if it's still open."""
    for i in cane.open_items():
        if (h.get("task_id") and i.get("id") == h["task_id"]) or (
                h.get("task_created") and i["created"] == h["task_created"]
                and i["text"] == h.get("task")):
            return i
    return None


def finish_task(h):
    """Mark the session's task done. Returns its text, or None."""
    with cane._LOCK:
        items = cane.load()
        for i in items:
            if not i["done"] and ((h.get("task_id") and i.get("id") == h["task_id"])
                                  or (h.get("task_created") and
                                      i["created"] == h["task_created"] and
                                      i["text"] == h.get("task"))):
                i["done"] = True
                cane.save(items)
                return i["text"]
    return None


def finished(bar, h):
    """End of a session: a toast, and "Mark task done" when it was for one."""
    if getattr(bar, "electron", False):
        import bridge
        text = "Focus done%s · %d min" % ((": " + h["task"]) if h["task"] else "", h["minutes"])
        buttons = (("OK", None, not task_of(h)),)
        if task_of(h):
            buttons = (("OK", None, False), ("Mark task done", lambda: finish_task(h), True))
        bridge.toast("Focus", text, buttons=buttons, kind="focus", sound=True)
        return
    from cane import FG, ACCENT
    import winsound
    winsound.PlaySound("SystemNotification", winsound.SND_ALIAS | winsound.SND_ASYNC)
    w, cv, close = bar._toast(112)
    cv.create_text(24, 26, text="FOCUS", fill=ACCENT, anchor="w",
                   font=("Segoe UI", 8, "bold"))
    cv.create_text(24, 58, text=("Focus done%s - %d min" % (
        (": " + h["task"]) if h["task"] else "", h["minutes"]))[:60],
        fill=FG, anchor="w", font=("Segoe UI", 13))
    cv.create_window(bar.W - 24, 92, anchor="e", window=bar._button(w, "OK", close))
    if task_of(h):
        go = bar._button(w, "Mark task done", lambda: (finish_task(h), close()))
        go.config(fg=ACCENT)
        cv.create_window(bar.W - 64, 92, anchor="e", window=go)


def nudge(bar, site):
    """The toast after a block, with a way through."""
    sess = current()
    if getattr(bar, "electron", False):
        import bridge
        msg = "%s is blocked" % site.title()
        if sess:
            msg += " · %d min left%s" % (round(sess["left"]),
                                         (" on " + sess["task"]) if sess["task"] else "")
        bridge.toast("Blocked", msg, sub="the tab was closed", kind="alert",
                     buttons=(("Let me through %d min" % LET_THROUGH,
                               lambda: let_through(site), False), ("OK", None, True)))
        return
    from cane import FG, DIM, ACCENT, FONT_SM
    w, cv, close = bar._toast(112)
    cv.create_text(24, 26, text="BLOCKED", fill=ACCENT, anchor="w",
                   font=("Segoe UI", 8, "bold"))
    msg = "%s is blocked" % site.title()
    if sess:
        msg += " - %d min left%s" % (round(sess["left"]),
                                     (" on " + sess["task"]) if sess["task"] else "")
    cv.create_text(24, 56, text=msg[:70], fill=FG, anchor="w",
                   font=("Segoe UI", 13))
    cv.create_window(bar.W - 24, 92, anchor="e",
                     window=bar._button(w, "OK", close))
    cv.create_window(bar.W - 70, 92, anchor="e", window=bar._button(
        w, "Let me through %d min" % LET_THROUGH,
        lambda: (let_through(site), close())))
    cv.create_text(24, 92, text="the tab was closed", fill=DIM, anchor="w",
                   font=FONT_SM)


# ------------------------------------------------------------- page

def sites_window(bar, back=None):
    """The Blocked sites page: what focus mode closes, and the schedules.
    Everything here goes through dispatch(), the same path as typed
    commands, so the page and "block reddit" can never disagree."""
    bar.root.after(0, lambda: _sites_window(bar, back))


def _sites_window(bar, back=None):
    import tkinter as tk
    from cane import PANEL, FG, DIM, ACCENT, FONT_BODY, FONT_SM, MARGIN

    if getattr(bar, "_sites_win", None):
        try:
            bar._sites_win.lift()
            return
        except tk.TclError:
            bar._sites_win = None
    W, H = bar.W, 520
    w = tk.Toplevel(bar.root)
    bar._sites_win = w
    cv = cane.make_panel(w, W, H)
    x0 = bar._x()
    w.geometry("+%d+%d" % (x0 + 40, MARGIN))

    def close(*_):
        bar._sites_win = None
        cane.slide(w, x0 + 40, MARGIN, 0.0, steps=8, then=w.destroy)

    bar.page_header(w, cv, "BLOCKED SITES", close,
                    back and (lambda: (close(), back())))
    field = dict(bg="#22252c", fg=FG, relief="flat", borderwidth=0,
                 highlightthickness=0, insertbackground=ACCENT, font=FONT_BODY)
    body = tk.Frame(w, bg=PANEL)
    cv.create_window(24, 52, anchor="nw", width=W - 48, height=H - 52 - 44,
                     window=body)
    note = cv.create_text(24, H - 24, text="", fill=DIM, anchor="w",
                          font=FONT_SM)

    def run(intent):
        cv.itemconfig(note, text=cane.dispatch(intent))
        draw()

    def head(text, hint):
        row = tk.Frame(body, bg=PANEL)
        row.pack(fill="x", pady=(10, 4))
        tk.Label(row, text=text, bg=PANEL, fg=DIM,
                 font=("Segoe UI", 8, "bold")).pack(side="left")
        tk.Label(row, text=hint, bg=PANEL, fg=DIM, font=FONT_SM).pack(side="right")

    def line(text, sub, on_remove):
        row = tk.Frame(body, bg=PANEL)
        row.pack(fill="x", pady=1)
        tk.Label(row, text=text, bg=PANEL, fg=FG, font=FONT_BODY).pack(side="left")
        if sub:
            tk.Label(row, text="  " + sub, bg=PANEL, fg=DIM,
                     font=FONT_SM).pack(side="left")
        bar._button(row, "remove", on_remove).pack(side="right")

    def adder(hint, make):
        row = tk.Frame(body, bg=PANEL)
        row.pack(fill="x", pady=(6, 0))
        var = tk.StringVar()
        e = tk.Entry(row, textvariable=var, **field)
        e.pack(side="left", fill="x", expand=True, ipady=3)
        tk.Label(row, text="", bg=PANEL, width=1).pack(side="left")
        ok = lambda *_: var.get().strip() and run(make(var.get().strip().lower()))
        b = bar._button(row, "Add", ok)
        b.config(fg=ACCENT)
        b.pack(side="right")
        e.bind("<Return>", ok)
        tk.Label(body, text=hint, bg=PANEL, fg=DIM, font=FONT_SM,
                 anchor="w").pack(fill="x")

    def draw():
        for c in body.winfo_children():
            c.destroy()
        s = load()
        head("DURING FOCUS", "closed while a focus session runs")
        if not s["sites"]:
            tk.Label(body, text="nothing", bg=PANEL, fg=DIM,
                     font=FONT_SM).pack(anchor="w")
        for x in s["sites"]:
            line(x, "", lambda x=x: run({"intent": "block", "action": "remove",
                                         "site": x}))
        adder("a site name, e.g. reddit or instagram.com",
              lambda v: {"intent": "block", "action": "add", "site": v})

        head("SCHEDULES", "closed at these times, focus or not")
        if not s["blocks"]:
            tk.Label(body, text="none", bg=PANEL, fg=DIM,
                     font=FONT_SM).pack(anchor="w")
        for b in s["blocks"]:
            line(b["site"], "%s-%s %s" % (b["start"], b["end"],
                                          _day_words(b["days"])),
                 lambda b=b: remove_schedule(b))
        adder("site and time, e.g. youtube 9-12 weekdays  ·  x 9pm-11pm daily",
              lambda v: quick("block " + re.sub(r"\s+(weekdays|weekends|daily)$",
                                                r" on \1", v))
              or {"intent": "block", "action": "add", "site": "?",
                  "from": "x", "to": "x"})

    def remove_schedule(b):
        with _LOCK:
            s = load()
            s["blocks"] = [x for x in s["blocks"] if x != b]
            save(s)
        cv.itemconfig(note, text="removed %s %s-%s" % (b["site"], b["start"],
                                                       b["end"]))
        draw()

    draw()
    w.focus_force()
    cane.slide(w, x0, MARGIN, 0.97, x_from=x0 + 40)


# ------------------------------------------------------------- commands

def _clock(v):
    """"9" / "9:30" / "2pm" -> "HH:MM". No am/pm and before 8 means
    afternoon, the same rule reminders use ("at 3" is 3pm)."""
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", v.strip())
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2) or 0)
    if m.group(3) == "pm" and h < 12:
        h += 12
    elif m.group(3) == "am" and h == 12:
        h = 0
    elif not m.group(3) and h < 8:
        h += 12
    if not (0 <= h <= 23 and 0 <= mi <= 59):
        return None
    return "%02d:%02d" % (h, mi)


def _days(v):
    v = (v or "").strip()
    if not v or re.fullmatch(r"every ?day|daily", v):
        return ALL_DAYS
    if v in DAYS:
        return DAYS[v]
    if re.fullmatch(r"mon(?:day)?s?(?: ?(?:-|to) ?)fri(?:day)?s?", v):
        return DAYS["weekdays"]
    return None


_N = r"(?P<n>\d+|half an hour|an hour|a half hour)"
_U = r"(?: ?(?P<u>m|mins?|minutes?|h|hrs?|hours?))?"
_T = r"\d{1,2}(?::\d\d)?\s?(?:am|pm)?"


def quick(t):
    """Fixed-shape focus and block commands. `t` is already lowercased."""
    m = re.fullmatch(
        r"(?:help me |let'?s |start (?:a )?|begin (?:a )?)?(?:focus|pomodoro)"
        r"(?: mode| session)?(?: on (?P<task>.+?))?(?: for " + _N + _U
        + r")?", t)
    if m:
        return {"intent": "focus", "action": "start", "arg": m["task"] or "",
                "minutes": _minutes(m["n"], m["u"])}
    m = re.fullmatch(r"(?:start )?(?:focus|pomodoro)(?: for)? (?P<n>\d+)" + _U, t)
    if m:
        return {"intent": "focus", "action": "start", "arg": "",
                "minutes": _minutes(m["n"], m["u"])}
    if re.fullmatch(r"(?:stop|end|cancel|quit|finish) (?:the |my )?(?:focus|pomodoro)"
                    r"(?: mode| session)?|i'?m done focusing|unfocus", t):
        return {"intent": "focus", "action": "stop"}
    if re.fullmatch(r"focus status|how (?:much|long)(?: time)? (?:is )?left"
                    r"(?: in (?:my |the )?focus)?", t):
        return {"intent": "focus", "action": "status"}
    if re.fullmatch(r"focus history|show (?:my )?focus(?: history| sessions)?|"
                    r"how (?:did|have) i focus(?:ed)?(?: today| this week)?", t):
        return {"intent": "focus", "action": "history"}
    if re.fullmatch(r"(?:show |list )?(?:my )?blocks|what(?:'s| is) blocked|"
                    r"blocked sites|show blocked sites", t):
        return {"intent": "block", "action": "list"}
    m = re.fullmatch(r"(?:unblock|stop blocking) (?P<site>[a-z0-9.]+)", t)
    if m:
        return {"intent": "block", "action": "remove", "site": m["site"]}
    m = re.fullmatch(r"block (?P<site>[a-z0-9.]+)(?: from)? (?P<a>" + _T
                     + r") ?(?:-|to|till|until) ?(?P<b>" + _T
                     + r")(?: (?:on |every )?(?P<days>weekdays|weekends|every ?day|"
                       r"daily|mon(?:day)?s?(?: ?(?:-|to) ?)fri(?:day)?s?))?", t)
    if m:
        return {"intent": "block", "action": "add", "site": m["site"],
                "from": m["a"], "to": m["b"], "days": m["days"] or "daily"}
    m = re.fullmatch(r"block (?P<site>[a-z0-9.]+)(?: (?:during|while|in) "
                     r"focus(?:ing)?)?", t)
    if m:
        return {"intent": "block", "action": "add", "site": m["site"]}
    return None


def _minutes(n, u):
    if not n:
        return str(DEFAULT_MIN)
    if not n.isdigit():
        return "60" if n == "an hour" else "30"
    return str(int(n) * (60 if (u or "m").startswith("h") else 1))


def dispatch(intent, ui=None):
    kind, action = intent.get("intent"), intent.get("action") or ""
    if kind == "focus":
        if action == "start":
            try:
                sess = start(str(intent.get("arg") or ""),
                             int(str(intent.get("minutes") or DEFAULT_MIN)))
            except ValueError as e:
                return str(e)
            sites = ", ".join(x.title() for x in load()["sites"]) or "nothing"
            return "focus%s: %d min, ends %s · blocking %s" % (
                (" on " + sess["task"]) if sess["task"] else "", sess["minutes"],
                sess["ends"][11:16], sites)
        if action == "stop":
            h = stop()
            return ("focus stopped after %d min" % h["minutes"]) if h \
                else "no focus session running"
        if action == "status":
            sess = current()
            return ("%d min left%s" % (round(sess["left"]), (" on " + sess["task"])
                                       if sess["task"] else "")) if sess \
                else "no focus session running"
        if action == "history":
            rows = load()["history"][-8:]
            if not rows:
                return "no focus sessions yet"
            text = "\n".join("%s  %s  %d/%s min  %s" % (
                h["started"][5:16].replace("T", " "), h["state"], h["minutes"],
                h.get("planned", "?"), h.get("task", "")) for h in reversed(rows))
            total = sum(h["minutes"] for h in load()["history"]
                        if h["started"][:10] == datetime.now().date().isoformat())
            if ui:
                ui.show_answer("focus history", text)
            return "%d min focused today" % total
        return "unknown focus action"

    if kind == "block":
        site = site_key(str(intent.get("site") or intent.get("arg") or ""))
        if action == "list":
            s = load()
            lines = ["During focus: " + (", ".join(s["sites"]) or "nothing")]
            for b in s["blocks"]:
                lines.append("%s %s-%s %s" % (b["site"], b["start"], b["end"],
                                              _day_words(b["days"])))
            if ui:
                ui.show_answer("blocks", "\n".join(lines))
            return "%d site%s during focus, %d schedule%s" % (
                len(s["sites"]), "" if len(s["sites"]) == 1 else "s",
                len(s["blocks"]), "" if len(s["blocks"]) == 1 else "s")
        if not re.fullmatch(r"[a-z0-9.-]{1,40}", site):
            return "which site?"
        with _LOCK:
            s = load()
            if action == "remove":
                had = site in s["sites"] or any(site_key(b["site"]) == site
                                                for b in s["blocks"])
                s["sites"] = [x for x in s["sites"] if x != site]
                s["blocks"] = [b for b in s["blocks"] if site_key(b["site"]) != site]
                save(s)
                return ("%s unblocked" % site) if had else ("%s wasn't blocked" % site)
            if action == "add":
                if intent.get("from") or intent.get("to"):
                    a, z = _clock(str(intent.get("from") or "")), \
                        _clock(str(intent.get("to") or ""))
                    days = _days(str(intent.get("days") or ""))
                    if not a or not z or a == z or days is None:
                        return "couldn't read that schedule - try: block youtube 9-12 on weekdays"
                    s["blocks"].append({"site": site, "start": a, "end": z,
                                        "days": days})
                    save(s)
                    return "%s blocked %s-%s %s" % (site, a, z, _day_words(days))
                if site not in s["sites"]:
                    s["sites"].append(site)
                    save(s)
                return "%s is blocked during focus" % site
        return "unknown block action"
    return "unknown"


def _day_words(days):
    days = sorted(days)
    if days == ALL_DAYS:
        return "daily"
    if days == DAYS["weekdays"]:
        return "on weekdays"
    if days == DAYS["weekends"]:
        return "on weekends"
    return "on " + ",".join("Mon Tue Wed Thu Fri Sat Sun".split()[d] for d in days)


def bar_line():
    """What the bar shows while a session runs."""
    sess = current()
    if not sess:
        return ""
    return "focus · %d min left%s" % (round(sess["left"]),
                                      (" · " + sess["task"]) if sess["task"] else "")


# The chat can use focus mode too.
try:
    import agent
    agent.EXTRA.update({
        "focus": ('{"intent":"focus","action":"start","arg":"<task>","minutes":"25"}'
                  '  start focus (closes distracting sites until it ends); '
                  'action stop|status|history need no other fields', True),
        "block": ('{"intent":"block","action":"add","site":"youtube"}  block a site '
                  'during focus; add "from":"9:00","to":"12:00","days":"weekdays|'
                  'weekends|daily" for a schedule; action remove|list', True),
    })
except ImportError:
    pass
