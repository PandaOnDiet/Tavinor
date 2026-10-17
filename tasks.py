"""Tasks, workspaces and habits (Taby parity, phase 5).

Tasks are Tavinor's existing open items in notes.json, with optional extras:
a due date (which also fires as a reminder, as before), subtasks, and a
workspace - the apps and links "start task X" opens. Old rows need no
migration; they simply have none of the extras.

Habits live in habits.json: a name, which days (daily / weekdays), a nudge
time, and the days you checked in. A streak counts scheduled days only, so
a weekday habit isn't broken by the weekend.
"""
import json
import re
import threading
import time
import webbrowser
from datetime import date, datetime, timedelta
from pathlib import Path

import cane

HABITS = Path(__file__).with_name("habits.json")
NUDGE_AT = "20:00"              # default habit nudge time
EVERY = {"daily": list(range(7)), "weekdays": [0, 1, 2, 3, 4]}
_LOCK = threading.Lock()


# ------------------------------------------------------------- habits store

def load_habits():
    try:
        raw = json.loads(HABITS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    rows = raw.get("habits") if isinstance(raw, dict) else None
    out = []
    for h in rows or []:
        if not isinstance(h, dict) or not str(h.get("name", "")).strip():
            continue
        out.append({
            "name": str(h["name"]).strip()[:60],
            "every": h.get("every") if h.get("every") in EVERY else "daily",
            "at": h.get("at") if re.fullmatch(r"\d\d:\d\d", str(h.get("at"))) else NUDGE_AT,
            "created": str(h.get("created", "")),
            "archived": bool(h.get("archived")),
            "checkins": sorted({d for d in h.get("checkins") or []
                                if isinstance(d, str) and _day(d)}),
            "nudged": str(h.get("nudged", "")),
        })
    return out


def save_habits(rows):
    HABITS.write_text(json.dumps({"habits": rows}, indent=1), encoding="utf-8")


def _day(s):
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def find_habit(rows, ref):
    ref = " ".join((ref or "").lower().split())
    if not ref:
        return None
    live = [h for h in rows if not h["archived"]]
    return (next((h for h in live if h["name"].lower() == ref), None)
            or next((h for h in live if ref in h["name"].lower()), None)
            or next((h for h in live if h["name"].lower() in ref), None))


def add_habit(name, every="daily", at=NUDGE_AT, now=None):
    now = now or datetime.now()
    name = " ".join(name.split())[:60]
    with _LOCK:
        rows = load_habits()
        old = next((h for h in rows if h["name"].lower() == name.lower()), None)
        if old:
            old.update(archived=False, every=every, at=at)
        else:
            rows.append({"name": name, "every": every, "at": at,
                         "created": now.isoformat(timespec="minutes"),
                         "archived": False, "checkins": [], "nudged": ""})
        save_habits(rows)
        return old or rows[-1]


def checkin(ref, today=None):
    """(habit, was_new) - checking in twice on one day counts once."""
    today = (today or date.today()).isoformat()
    with _LOCK:
        rows = load_habits()
        h = find_habit(rows, ref)
        if not h:
            return None, False
        new = today not in h["checkins"]
        if new:
            h["checkins"] = sorted(h["checkins"] + [today])
            save_habits(rows)
        return h, new


def drop_habit(ref):
    """Archive, not delete: the check-in history is kept."""
    with _LOCK:
        rows = load_habits()
        h = find_habit(rows, ref)
        if h:
            h["archived"] = True
            save_habits(rows)
        return h


def streak(h, today=None):
    """Consecutive scheduled days checked in, ending today - or yesterday's
    scheduled day if today isn't done yet (the day isn't over)."""
    today = today or date.today()
    days, done = EVERY[h["every"]], set(h["checkins"])
    d, n = today, 0
    if d.isoformat() not in done:
        d -= timedelta(days=1)
    for _ in range(3660):
        if d.weekday() not in days:
            d -= timedelta(days=1)
            continue
        if d.isoformat() not in done:
            break
        n += 1
        d -= timedelta(days=1)
    return n


def nudges(now=None):
    """Habits due a nudge now: scheduled today, past their time, not done,
    not already nudged today. Marks them nudged."""
    now = now or datetime.now()
    today = now.date().isoformat()
    with _LOCK:
        rows, out = load_habits(), []
        for h in rows:
            if (h["archived"] or now.weekday() not in EVERY[h["every"]]
                    or today in h["checkins"] or h["nudged"] == today
                    or now.strftime("%H:%M") < h["at"]):
                continue
            h["nudged"] = today
            out.append(h)
        if out:
            save_habits(rows)
        return out


def watch(bar):
    while True:
        try:
            for h in nudges():
                bar.root.after(0, lambda h=h: nudge(bar, h))
        except Exception:
            pass
        time.sleep(60)


def nudge(bar, h):
    if getattr(bar, "electron", False):
        import bridge
        s = streak(h)
        bridge.toast("Habit", h["name"], kind="habit", sound=True,
                     sub=("%d-day streak - keep it going" % s) if s else "not done yet today",
                     buttons=(("Later", None, False), ("Done", lambda: checkin(h["name"]), True)))
        return
    from cane import FG, DIM, ACCENT, FONT_SM
    import winsound
    winsound.PlaySound("SystemNotification", winsound.SND_ALIAS | winsound.SND_ASYNC)
    w, cv, close = bar._toast(112)
    cv.create_text(24, 26, text="HABIT", fill=ACCENT, anchor="w",
                   font=("Segoe UI", 8, "bold"))
    s = streak(h)
    cv.create_text(24, 56, text=h["name"][:50], fill=FG, anchor="w",
                   font=("Segoe UI", 13))
    cv.create_text(24, 92, text=("%d-day streak - keep it going" % s) if s
                   else "not done yet today", fill=DIM, anchor="w", font=FONT_SM)
    cv.create_window(bar.W - 24, 92, anchor="e", window=bar._button(
        w, "Done", lambda: (checkin(h["name"]), close())))
    cv.create_window(bar.W - 74, 92, anchor="e",
                     window=bar._button(w, "Later", close))


# ------------------------------------------------------------- workspaces

def split_targets(text):
    """"excel, chrome and https://x.com" -> (["excel", "chrome"], ["https://x.com"])"""
    parts = [p.strip(" .") for p in re.split(r",|\band\b|\+", text) if p.strip(" .")]
    links = [p for p in parts if re.match(r"(https?://|www\.)|[\w-]+\.[a-z]{2,}(/|$)", p)]
    return [p for p in parts if p not in links][:8], links[:8]


def set_workspace(ref, text):
    apps, links = split_targets(text)
    with cane._LOCK:
        items = cane.load()
        t = cane.pick_item([i for i in items if not i["done"]], ref)
        if not t:
            return None
        t["apps"] = sorted(set(t.get("apps", [])) | set(apps), key=str.lower)
        t["links"] = sorted(set(t.get("links", [])) | set(links))
        cane.save(items)
        return t


def start(ref, ui=None):
    t = cane.pick_item(cane.open_items(), ref)
    if not t:
        return "no open task matching %r" % ref
    opened, failed = [], []
    for a in t.get("apps", []):
        r = cane.dispatch({"intent": "launch", "arg": a}, ui)
        (opened if r.startswith("opened") else failed).append(a)
    for link in t.get("links", []):
        webbrowser.open(link if link.startswith("http") else "https://" + link)
        opened.append(link)
    if not opened and not failed:
        return "%s has no workspace yet - try: workspace for %s: excel, chrome" % (
            t["text"][:30], t["text"][:20])
    msg = "opened %d for %s" % (len(opened), t["text"][:30])
    return msg + (" (couldn't open %s)" % ", ".join(failed) if failed else "")


# ------------------------------------------------------------- commands

_T = r"\d{1,2}(?::\d\d)?\s?(?:am|pm)?"


def quick(t):
    """Fixed-shape task and habit commands. `t` is already lowercased."""
    if re.fullmatch(r"(?:show |list )?(?:my )?(?:tasks|to-?dos?)|what are my tasks", t):
        return {"intent": "recall"}
    m = re.fullmatch(r"(?:add (?:a )?)?sub-?task:? (?P<text>.+?) (?:to|under|for|in) "
                     r"(?P<parent>.+)", t)
    if m:
        return {"intent": "task", "action": "subtask", "arg": m["text"],
                "parent": m["parent"]}
    # "task: X" / "add task X" / "new task X" - never a bare "task X", or
    # "task manager" would become a task called "manager".
    m = re.fullmatch(r"(?:(?:add (?:a )?|new )task:?|task:) (?P<text>.+?)(?:,? (?P<when>(?:due|by|on) "
                     r".+|(?:today|tonight|tomorrow)(?: at .+)?))?", t)
    if m:
        when = re.sub(r"^(due|by) ", "", m["when"] or "")
        return {"intent": "task", "action": "add", "arg": m["text"], "when": when}
    m = (re.fullmatch(r"(?:workspace|apps) for (?P<task>.+?):\s*(?P<apps>.+)", t)
         or re.fullmatch(r"add (?P<apps>.+?) to (?:the )?(?P<task>.+?) workspace", t))
    if m:
        return {"intent": "task", "action": "workspace", "arg": m["task"],
                "apps": m["apps"]}
    # Not "open task X": "open task manager" must still launch Task Manager.
    m = re.fullmatch(r"(?:start|begin|resume) (?:task|working on|work on) (?P<task>.+)"
                     r"|open (?:the )?workspace (?:for )?(?P<task2>.+)|work on (?P<task3>.+)", t)
    if m:
        return {"intent": "task", "action": "start",
                "arg": m["task"] or m["task2"] or m["task3"]}

    m = re.fullmatch(r"(?:(?:add (?:a )?|new |start (?:a )?)habit:?|habit:) (?P<name>.+?)"
                     r"(?: (?P<every>daily|every ?day|each day|on weekdays|weekdays))?"
                     r"(?: at (?P<at>" + _T + r"))?", t)
    if m:
        return {"intent": "habit", "action": "add", "arg": m["name"],
                "every": "weekdays" if "weekday" in (m["every"] or "") else "daily",
                "at": m["at"] or ""}
    if re.fullmatch(r"(?:show |list )?(?:my )?habits|(?:my )?(?:habit )?streaks?|"
                    r"how are my habits(?: going)?", t):
        return {"intent": "habit", "action": "list"}
    m = re.fullmatch(r"(?:drop|stop|remove|delete|archive) (?:the |my )?habit (?P<name>.+)", t)
    if m:
        return {"intent": "habit", "action": "drop", "arg": m["name"]}
    # "did my run" / "done meditating" - only when it names a habit, so
    # "done 2" and "done ravi" still close open items as before.
    m = re.fullmatch(r"(?:i )?(?:did|done|finished|checked? in|check in|completed?) "
                     r"(?:my |the )?(?:habit )?(?P<name>.+)", t)
    if m and not m["name"].isdigit() and find_habit(load_habits(), m["name"]):
        return {"intent": "habit", "action": "checkin", "arg": m["name"]}
    return None


def dispatch(intent, ui=None):
    kind, action = intent.get("intent"), intent.get("action") or ""
    arg = str(intent.get("arg") or "").strip()
    if kind == "task":
        if action == "add":
            if not arg:
                return "what's the task?"
            when = str(intent.get("when") or "")
            due = cane.parse_when(when) if when else None
            item = cane.add_note(arg, due)
            if when and not due:
                return "task saved (couldn't read %r as a date): %s" % (when, arg[:40])
            return "task: %s%s" % (arg[:40], due and due.strftime(", due %a %d %b %H:%M") or "")
        if action == "subtask":
            parent = cane.pick_item(cane.open_items(), str(intent.get("parent") or ""))
            if not parent:
                return "no open task matching %r" % intent.get("parent")
            try:
                cane.add_note(arg, parent=parent)
            except ValueError as e:
                return str(e)
            return "subtask of %s: %s" % (parent["text"][:30], arg[:30])
        if action == "workspace":
            t = set_workspace(arg, str(intent.get("apps") or ""))
            if not t:
                return "no open task matching %r" % arg
            return "%s opens: %s" % (t["text"][:30],
                                     ", ".join(t["apps"] + t["links"]) or "nothing")
        if action == "start":
            return start(arg, ui)
        return "unknown task action"

    if kind == "habit":
        if action == "add":
            if not arg:
                return "which habit?"
            at = intent.get("at") or ""
            at = (_clock(at) if at else NUDGE_AT)
            if at is None:
                return "couldn't read the time %r" % intent.get("at")
            every = intent.get("every") if intent.get("every") in EVERY else "daily"
            h = add_habit(arg, every, at)
            return "habit: %s, %s, nudge at %s" % (h["name"], every, at)
        if action == "checkin":
            h, new = checkin(arg)
            if not h:
                return "no habit matching %r" % arg
            s = streak(h)
            return ("%s done - %d-day streak" if new else
                    "%s was already done today - %d-day streak") % (h["name"], s)
        if action == "drop":
            h = drop_habit(arg)
            return ("stopped %s (history kept)" % h["name"]) if h else \
                "no habit matching %r" % arg
        if action == "list":
            live = [h for h in load_habits() if not h["archived"]]
            if not live:
                return "no habits yet - try: habit: drink water daily at 6pm"
            today = date.today().isoformat()
            text = "\n".join("%s %s  ·  %d-day streak  ·  %s at %s" % (
                "✓" if today in h["checkins"] else "·", h["name"], streak(h),
                h["every"], h["at"]) for h in live)
            if ui:
                ui.show_answer("habits", text)
            done = sum(today in h["checkins"] for h in live)
            return "%d of %d habits done today" % (done, len(live))
        return "unknown habit action"
    return "unknown"


def _clock(v):
    import focus
    return focus._clock(v)


def describe(it):
    kind, act, arg = it["intent"], it.get("action") or "", it.get("arg") or ""
    if kind == "task":
        return {"add": "add task: %s" % arg[:30],
                "subtask": "add subtask: %s" % arg[:28],
                "workspace": "set what %s opens" % arg[:26],
                "start": "open the workspace for %s" % arg[:26]}.get(act, "task")
    return {"add": "add habit: %s" % arg[:30], "checkin": "check in: %s" % arg[:30],
            "drop": "stop habit: %s" % arg[:30],
            "list": "show habits and streaks"}.get(act, "habit")


try:
    import agent
    agent.EXTRA.update({
        "task": ('{"intent":"task","action":"add","arg":"<task>","when":"<due, e.g. friday '
                 'at 5pm>"}  add a task; action "subtask" with "parent":"<task words>"; '
                 'action "workspace" with "apps":"excel, chrome, https://..." sets what it '
                 'opens; action "start" opens that workspace', True),
        "habit": ('{"intent":"habit","action":"add","arg":"<habit>","every":"daily|'
                  'weekdays","at":"20:00"}  track a habit; action checkin|drop with arg, '
                  'or list', True),
    })
except ImportError:
    pass
