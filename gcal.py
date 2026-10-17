"""Google Calendar, two-way, and "plan my day" (phase 10).

Sign-in is Google's installed-app flow: your browser opens Google's own page,
Google sends the code back to a one-off local address (127.0.0.1, random
port, PKCE), and the refresh token is kept in Windows Credential Manager as
"Cane/google_refresh" - never in a file. Your Google Cloud client ID and
secret (Settings > Calendar) are stored the same way.

Scope is calendar events only. Reading and creating run straight away;
moving and deleting always ask first, from the bar or the chat.

  what's on my calendar today / tomorrow
  add meeting design review tomorrow at 3pm for 1 hour
  move design review to friday at 11am      (asks first)
  cancel meeting design review              (asks first)
  plan my day

"Plan my day" works without a calendar too: it fits your open tasks into
the free time between meetings and puts the hardest one in your best hours
(from Activity, when that's on).
"""
import base64
import hashlib
import http.server
import json
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from datetime import date, datetime, timedelta

import cane

AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN = "https://oauth2.googleapis.com/token"
API = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
SCOPE = "https://www.googleapis.com/auth/calendar.events"
REMIND_BEFORE = 10            # minutes before a meeting
DAY_START, DAY_END = 9, 18    # working hours for "plan my day"
DEFAULT_MIN = 30
_token = {"access": None, "expires": 0.0}
_reminded = set()
_LOCK = threading.Lock()


# ------------------------------------------------------------- credentials

def client():
    raw = cane.key_get("google_client")
    try:
        c = json.loads(raw) if raw else {}
    except ValueError:
        c = {}
    return c.get("id", ""), c.get("secret", "")


def set_client(cid, secret):
    cane.key_set("google_client", json.dumps({"id": cid.strip(),
                                              "secret": secret.strip()}))


def signed_in():
    return bool(cane.key_get("google_refresh"))


def sign_out():
    cane.key_set("google_refresh", "")
    _token.update(access=None, expires=0.0)


def _post_form(url, fields, timeout=20):
    req = urllib.request.Request(url, urllib.parse.urlencode(fields).encode(),
                                 {"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            msg = json.load(e).get("error_description") or str(e)
        except ValueError:
            msg = str(e)
        raise RuntimeError("Google: %s" % msg) from None
    except (urllib.error.URLError, OSError):
        raise RuntimeError("couldn't reach Google") from None


def auth_url(cid, redirect, challenge, state):
    return AUTH + "?" + urllib.parse.urlencode({
        "client_id": cid, "redirect_uri": redirect, "response_type": "code",
        "scope": SCOPE, "access_type": "offline", "prompt": "consent",
        "code_challenge": challenge, "code_challenge_method": "S256",
        "state": state})


def sign_in(timeout=180, open_browser=webbrowser.open):
    """Run the browser sign-in. Returns a message; raises RuntimeError."""
    cid, secret = client()
    if not cid:
        raise RuntimeError("add your Google client ID in Settings > Calendar first")
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    got = {}

    class Back(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if q.get("state", [""])[0] != state:       # not our request
                self.send_response(400)
                self.end_headers()
                return
            got["code"] = q.get("code", [""])[0]
            got["error"] = q.get("error", [""])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(("<p style='font-family:Segoe UI'>%s You can close "
                              "this tab.</p>" % ("Signed in to Tavinor." if got["code"]
                                                 else "Sign-in was cancelled.")).encode())

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Back)
    redirect = "http://127.0.0.1:%d" % srv.server_port
    srv.timeout = 1
    open_browser(auth_url(cid, redirect, challenge, state))
    end = time.monotonic() + timeout
    try:
        while not got and time.monotonic() < end:
            srv.handle_request()
    finally:
        srv.server_close()
    if not got.get("code"):
        raise RuntimeError("sign-in %s" % ("was cancelled" if got else "timed out"))
    tok = _post_form(TOKEN, {"code": got["code"], "client_id": cid,
                             "client_secret": secret, "redirect_uri": redirect,
                             "grant_type": "authorization_code",
                             "code_verifier": verifier})
    if not tok.get("refresh_token"):
        raise RuntimeError("Google sent no refresh token - try signing in again")
    cane.key_set("google_refresh", tok["refresh_token"])
    _token.update(access=tok.get("access_token"),
                  expires=time.time() + int(tok.get("expires_in", 0)) - 60)
    return "signed in to Google Calendar"


def access_token():
    with _LOCK:
        if _token["access"] and time.time() < _token["expires"]:
            return _token["access"]
        refresh = cane.key_get("google_refresh")
        if not refresh:
            raise RuntimeError("not signed in to Google - Settings > Calendar")
        cid, secret = client()
        tok = _post_form(TOKEN, {"client_id": cid, "client_secret": secret,
                                 "refresh_token": refresh,
                                 "grant_type": "refresh_token"})
        _token.update(access=tok["access_token"],
                      expires=time.time() + int(tok.get("expires_in", 3600)) - 60)
        return _token["access"]


def _api(method, url, body=None, timeout=20):
    req = urllib.request.Request(url, json.dumps(body).encode() if body else None,
                                 {"Authorization": "Bearer " + access_token(),
                                  "Content-Type": "application/json"}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        if e.code == 401:
            _token.update(access=None, expires=0.0)
        raise RuntimeError("Google Calendar said %d" % e.code) from None
    except (urllib.error.URLError, OSError):
        raise RuntimeError("couldn't reach Google Calendar") from None


# ------------------------------------------------------------- events

def _local_iso(dt):
    return dt.astimezone().isoformat(timespec="seconds")


def _parse(ev):
    """An API event -> {id, title, start, end, all_day} with local datetimes."""
    s, e = ev.get("start", {}), ev.get("end", {})
    if "dateTime" in s:
        start = datetime.fromisoformat(s["dateTime"]).astimezone().replace(tzinfo=None)
        end = datetime.fromisoformat(e.get("dateTime", s["dateTime"])).astimezone() \
            .replace(tzinfo=None)
        all_day = False
    else:
        start = datetime.fromisoformat(s.get("date", "1970-01-01"))
        end = datetime.fromisoformat(e.get("date", s.get("date", "1970-01-01")))
        all_day = True
    return {"id": ev.get("id", ""), "title": ev.get("summary") or "(no title)",
            "start": start, "end": end, "all_day": all_day}


def events(day_from, day_to):
    q = urllib.parse.urlencode({
        "timeMin": _local_iso(datetime.combine(day_from, datetime.min.time())),
        "timeMax": _local_iso(datetime.combine(day_to, datetime.min.time())),
        "singleEvents": "true", "orderBy": "startTime", "maxResults": 100})
    data = _api("GET", API + "?" + q)
    return [_parse(ev) for ev in data.get("items", []) if ev.get("status") != "cancelled"]


def create(title, start, minutes):
    ev = _api("POST", API, {"summary": title,
                            "start": {"dateTime": _local_iso(start)},
                            "end": {"dateTime": _local_iso(start + timedelta(minutes=minutes))}})
    return _parse(ev)


def move(ev, start):
    length = ev["end"] - ev["start"]
    out = _api("PATCH", API + "/" + urllib.parse.quote(ev["id"]),
               {"start": {"dateTime": _local_iso(start)},
                "end": {"dateTime": _local_iso(start + length)}})
    return _parse(out)


def delete(ev):
    _api("DELETE", API + "/" + urllib.parse.quote(ev["id"]))


def find(words, around=None):
    """The next event in two weeks whose title contains the words."""
    words = words.lower().strip()
    today = (around or date.today())
    for ev in events(today, today + timedelta(days=14)):
        if words and words in ev["title"].lower():
            return ev
    return None


def reminders(bar):
    """Toast REMIND_BEFORE minutes before each meeting."""
    cache, fetched = [], 0.0
    while True:
        try:
            if signed_in() and cane.setting("meeting_reminders", True):
                if time.monotonic() - fetched > 300:
                    cache = events(date.today(), date.today() + timedelta(days=1))
                    fetched = time.monotonic()
                now = datetime.now()
                for ev in cache:
                    lead = (ev["start"] - now).total_seconds() / 60
                    if not ev["all_day"] and 0 <= lead <= REMIND_BEFORE \
                            and ev["id"] not in _reminded:
                        _reminded.add(ev["id"])
                        bar.root.after(0, lambda e=ev, m=round(lead): bar.alert(
                            {"text": "%s in %d min (%s)" % (e["title"][:60], m,
                                                            e["start"].strftime("%H:%M"))},
                            title="MEETING"))
        except Exception:
            pass
        time.sleep(60)


# ------------------------------------------------------------- plan my day

def free_slots(busy, start, end):
    """[(from, to)] gaps of at least 15 min between start and end."""
    slots, t = [], start
    for b0, b1 in sorted(busy):
        if b1 <= t or b0 >= end:
            continue
        if b0 - t >= timedelta(minutes=15):
            slots.append((t, min(b0, end)))
        t = max(t, b1)
    if end - t >= timedelta(minutes=15):
        slots.append((t, end))
    return slots


def _take(slots, k, s, need):
    """Use [s, s+need) out of slot k, keeping the pieces left either side."""
    a, b = slots[k]
    keep = [(x, y) for x, y in ((a, s), (s + need, b))
            if y - x >= timedelta(minutes=15)]
    slots[k:k + 1] = keep


def plan(tasks, busy, now, best=None, day_end=None):
    """[(from, to, task)] and [tasks that didn't fit].

    tasks: [{"text", "minutes", "hard"}] - placed hardest first.
    busy:  [(from, to)] meetings - the plan never overlaps one.
    best:  the hour the user works best (Activity), or None. The hardest
           task starts inside best..best+2 when any free time there fits it.
    """
    day_end = day_end or now.replace(hour=DAY_END, minute=0, second=0, microsecond=0)
    start = max(now, now.replace(hour=DAY_START, minute=0, second=0, microsecond=0))
    start = start.replace(second=0, microsecond=0)
    start += timedelta(minutes=(-start.minute) % 15)             # round up to :15
    slots = free_slots(busy, start, day_end)
    placed, left = [], []
    for i, t in enumerate(sorted(tasks, key=lambda t: -t.get("hard", 2))):
        need = timedelta(minutes=t.get("minutes", DEFAULT_MIN))
        choice = None
        if i == 0 and best is not None:
            for k, (a, b) in enumerate(slots):
                s = max(a, a.replace(hour=best, minute=0))
                if best <= s.hour < best + 2 and s + need <= b:
                    choice = (k, s)
                    break
        if choice is None:
            choice = next(((k, a) for k, (a, b) in enumerate(slots)
                           if b - a >= need), None)
        if choice is None:
            left.append(t)
            continue
        k, s = choice
        _take(slots, k, s, need)
        placed.append((s, s + need, t))
    placed.sort(key=lambda p: p[0])
    return placed, left


ESTIMATE = """Estimate each task. Reply with JSON only:
{"tasks": [{"n": <number>, "minutes": <15-240>, "hard": <1 easy | 2 | 3 hard>}]}"""


def estimate(texts):
    """Minutes and difficulty per task from the answer model; 30 min / 2 if
    it can't say. Never blocks the plan on the model."""
    out = [{"text": t, "minutes": DEFAULT_MIN, "hard": 2} for t in texts]
    if not texts:
        return out
    try:
        reply, _ = cane.complete(ESTIMATE, [{"role": "user", "content": "\n".join(
            "%d. %s" % (i, t) for i, t in enumerate(texts, 1))}], 15)  # live: 32.7 s at 30
        data = json.loads(reply[reply.find("{"):reply.rfind("}") + 1])
        for row in data.get("tasks", []):
            i = int(row.get("n", 0)) - 1
            if 0 <= i < len(out):
                out[i]["minutes"] = max(15, min(240, int(row.get("minutes", DEFAULT_MIN))))
                out[i]["hard"] = max(1, min(3, int(row.get("hard", 2))))
    except Exception:
        pass
    return out


def plan_my_day(now=None):
    """(text, placed, left, note) for today."""
    now = now or datetime.now()
    note = []
    busy = []
    if signed_in():
        try:
            busy = [(e["start"], e["end"]) for e in events(now.date(), now.date() + timedelta(days=1))
                    if not e["all_day"]]
        except RuntimeError as e:
            note.append("calendar not read: %s" % e)
    else:
        note.append("no calendar connected - meetings not included")
    items = [i for i in cane.open_items()
             if not i.get("due") or i["due"][:10] <= now.date().isoformat()][:12]
    tasks = estimate([i["text"] for i in items])
    best = None
    try:
        import activity
        if activity.on():
            b = activity.best_hours(today=now.date())
            best = b[0] if b else None
    except Exception:
        best = None
    placed, left = plan(tasks, busy, now, best)
    lines = []
    for a, b, t in placed:
        lines.append("%s-%s  %s%s" % (a.strftime("%H:%M"), b.strftime("%H:%M"),
                                      t["text"][:60], "  (hard)" if t["hard"] == 3 else ""))
    for a, b in busy:
        if a.date() == now.date() and b > now:
            lines.append("%s-%s  [meeting]" % (a.strftime("%H:%M"), b.strftime("%H:%M")))
    lines.sort()
    if left:
        lines.append("didn't fit today: " + ", ".join(t["text"][:30] for t in left))
    if best is not None:
        note.append("best hours from Activity: %d-%d" % (best, best + 2))
    return "\n".join(lines) or "nothing to plan - no open tasks", placed, left, "; ".join(note)


# ------------------------------------------------------------- commands

def quick(t):
    m = re.fullmatch(r"(?:what'?s|what is) on my (?:calendar|schedule|agenda)"
                     r"(?: for)?(?: (?P<d>today|tomorrow))?|(?:my )?(?:meetings|agenda|"
                     r"calendar|schedule)(?: for)?(?: (?P<d2>today|tomorrow))?", t)
    if m:
        return {"intent": "calendar", "action": "list",
                "arg": m["d"] or m["d2"] or "today"}
    # "add meeting design review tomorrow at 3pm for 1 hour" - a time is
    # required, so "add meeting notes to the doc" stays a sentence.
    m = re.fullmatch(r"(?:(?:add|create|book|schedule)(?: a| an)? (?P<kind>meeting|event|call)"
                     r"(?P<with> with)?:?|schedule|book) (?P<title>.+?) "
                     r"(?P<when>(?:(?:today|tomorrow|on \w+day|\w+day) )?at \d.*?)"
                     r"(?: for (?P<dur>\d+) ?(?P<u>m|mins?|minutes?|h|hrs?|hours?))?", t)
    if m:
        mins = int(m["dur"] or DEFAULT_MIN) * (60 if (m["u"] or "m").startswith("h") else 1)
        title = m["title"]
        if m["with"]:                              # "call with ravi", not "ravi"
            title = "%s with %s" % (m["kind"].title(), title)
        return {"intent": "calendar", "action": "create", "arg": title,
                "when": m["when"].strip(), "minutes": str(mins)}
    m = re.fullmatch(r"(?:move|reschedule|push) (?:the |my )?(?:meeting )?(?P<what>.+?) "
                     r"to (?P<when>.+)", t)
    if m and re.search(r"\d|today|tomorrow|day\b", m["when"]):
        return {"intent": "calendar", "action": "move", "arg": m["what"],
                "when": m["when"]}
    m = re.fullmatch(r"(?:cancel|delete|remove) (?:the |my )?(?:meeting|event) (?P<what>.+)", t)
    if m:
        return {"intent": "calendar", "action": "delete", "arg": m["what"]}
    if re.fullmatch(r"plan my day|plan (?:the|my) day|what should i do (?:today|now)", t):
        return {"intent": "calendar", "action": "plan"}
    return None


def needs_ok(intent):
    return intent.get("action") in ("move", "delete")


def dispatch(intent, ui=None):
    act, arg = intent.get("action"), str(intent.get("arg") or "").strip()
    if act == "plan":
        text, placed, left, note = plan_my_day()
        if ui:
            ui.show_answer("plan my day", (note + "\n" if note else "") + text)
        return "planned %d task%s%s" % (len(placed), "" if len(placed) == 1 else "s",
                                        ", %d didn't fit" % len(left) if left else "")
    if not signed_in():
        return "Google Calendar isn't connected - Settings > Calendar"
    try:
        if act == "list":
            day = date.today() + timedelta(days=arg == "tomorrow")
            evs = events(day, day + timedelta(days=1))
            if not evs:
                return "nothing on your calendar %s" % arg
            text = "\n".join("%s  %s" % ("all day" if e["all_day"] else
                                         e["start"].strftime("%H:%M") + "-" +
                                         e["end"].strftime("%H:%M"), e["title"])
                             for e in evs)
            if ui:
                ui.show_answer("calendar " + arg, text)
            return "%d event%s %s" % (len(evs), "" if len(evs) == 1 else "s", arg)
        if act == "create":
            start = cane.parse_when(str(intent.get("when") or ""))
            if not start:
                return "couldn't read the time %r" % intent.get("when")
            ev = create(arg, start, int(str(intent.get("minutes") or DEFAULT_MIN)))
            return "added %s, %s" % (ev["title"][:40], ev["start"].strftime("%a %d %b %H:%M"))
        if act in ("move", "delete"):
            ev = find(arg)
            if not ev:
                return "no event matching %r in the next two weeks" % arg
            start = cane.parse_when(str(intent.get("when") or "")) if act == "move" else None
            if act == "move" and not start:
                return "couldn't read the new time %r" % intent.get("when")
            what = ("move %s from %s to %s" % (ev["title"][:40],
                                               ev["start"].strftime("%a %H:%M"),
                                               start.strftime("%a %d %b %H:%M"))
                    if act == "move" else "delete %s on %s" % (
                        ev["title"][:40], ev["start"].strftime("%a %d %b %H:%M")))
            if not intent.get("approved"):
                if ui and hasattr(ui, "confirm"):
                    ui.confirm("CALENDAR", [what[0].upper() + what[1:] + "?"],
                               lambda: ui.status(dispatch(dict(intent, approved=True), ui),
                                                 keep=True))
                    return "waiting for your OK"
                return "not done - needs your approval (%s)" % what
            if act == "move":
                ev2 = move(ev, start)
                return "moved %s to %s" % (ev2["title"][:40], ev2["start"].strftime("%a %H:%M"))
            delete(ev)
            return "deleted %s" % ev["title"][:40]
    except RuntimeError as e:
        return str(e)
    return "unknown calendar action"


try:
    import agent
    agent.EXTRA["calendar"] = (
        '{"intent":"calendar","action":"list","arg":"today|tomorrow"}  read the '
        'calendar; "action":"create","arg":"<title>","when":"tomorrow at 3pm",'
        '"minutes":"60"; "action":"move","arg":"<event title words>","when":"<new '
        'time>"; "action":"delete","arg":"<event title words>"; "action":"plan" '
        'plans the day', lambda a: not needs_ok(a))
except ImportError:
    pass
