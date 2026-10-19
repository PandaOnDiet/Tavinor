"""The running app's side of the agent hooks (phase 12).

A tiny HTTP endpoint on 127.0.0.1 (random port) that mcp_server.py calls.
Each start writes bridge.json beside tavinor.py with the port and a fresh
random token; requests without that token are refused. Anything that
changes your data shows a Do it / Cancel card in Tavinor and waits for you -
the same rule as the chat. If you don't answer in APPROVE_WAIT seconds,
the answer is no.
"""
import http.server
import json
import os
import secrets
import threading
from datetime import date, datetime, timedelta
from pathlib import Path

import tavinor

FILE = Path(os.environ.get("CANE_BRIDGE") or tavinor.DATA / "bridge.json")
APPROVE_WAIT = 120

READS = {"get_day", "search_notes"}
WRITES = {"add_task", "add_note", "start_focus"}
COSMETIC = {"animate_face"}


def get_day():
    import focus
    import tasks
    import gcal
    today = date.today().isoformat()
    items = tavinor.open_items()
    out = {"date": today,
           "tasks": [{"n": i, "text": t["text"], "due": t.get("due"),
                      "subtask": bool(t.get("parent"))}
                     for i, t in enumerate(items, 1)][:30],
           "focus": None, "habits": [], "calendar": None}
    f = focus.current()
    if f:
        out["focus"] = {"task": f["task"], "minutes_left": round(f["left"])}
    for h in tasks.load_habits():
        if not h["archived"]:
            out["habits"].append({"name": h["name"], "done_today": today in h["checkins"],
                                  "streak": tasks.streak(h)})
    if gcal.signed_in():
        try:
            out["calendar"] = [{"title": e["title"], "start": e["start"].isoformat(),
                                "end": e["end"].isoformat(), "all_day": e["all_day"]}
                               for e in gcal.events(date.today(),
                                                    date.today() + timedelta(days=1))]
        except RuntimeError as e:
            out["calendar"] = "unavailable: %s" % e
    return json.dumps(out)


def describe(tool, args):
    return {"add_task": "Add task: %s%s" % (args.get("text", "")[:50],
                                             " (due %s)" % args["due"] if args.get("due") else ""),
            "add_note": "Save note: %s" % args.get("text", "")[:50],
            "start_focus": "Start focus%s for %s min" % (
                " on " + args["task"][:30] if args.get("task") else "",
                args.get("minutes", 25))}[tool]


def run(tool, args):
    """Carry out an already-approved (or read-only) tool call."""
    if tool == "get_day":
        return get_day()
    if tool == "search_notes":
        import notes
        hits, how = notes.search(str(args.get("query", "")))
        return json.dumps({"matches": [{"score": round(s, 3), "text": r["text"]}
                                       for s, r in hits], "note": how})
    if tool == "add_task":
        return tavinor.dispatch({"intent": "task", "action": "add",
                              "arg": str(args.get("text", "")),
                              "when": str(args.get("due", "") or "")})
    if tool == "add_note":
        return tavinor.dispatch({"intent": "note", "action": "add",
                              "arg": str(args.get("text", ""))})
    if tool == "start_focus":
        return tavinor.dispatch({"intent": "focus", "action": "start",
                              "arg": str(args.get("task", "") or ""),
                              "minutes": str(args.get("minutes", 25) or 25)})
    raise ValueError("unknown tool %r" % tool)


def call(tool, args, approve, face=None):
    """(ok, text). `approve(lines) -> bool` blocks until the user decides."""
    if tool in COSMETIC:
        state = str(args.get("state", ""))
        if face:
            face(state)
        return True, "face: " + state
    if tool not in READS | WRITES:
        return False, "unknown tool %r" % tool
    if tool in WRITES and not approve(["An agent wants to:", describe(tool, args)]):
        return False, "declined by the user in Tavinor"
    try:
        out = run(tool, args)
    except Exception as e:
        return False, "failed: %s" % e
    # Live, Claude Code couldn't tell an approved-and-saved task from a
    # pending one when only the result came back. Say it.
    return True, ("approved by the user in Tavinor and done - " + out) \
        if tool in WRITES else out


# ------------------------------------------------------------ the UI (E1)
# The Electron front end uses this same server with its OWN token, handed to
# it at launch in an environment variable and never written to disk. It can
# run any command (it is the bar), so an agent holding bridge.json's token
# must not be able to use it - that would skip the Do it / Cancel approval.
UI_TOKEN = secrets.token_urlsafe(24)
_EV, _EV_N, _FACE = [], 0, "idle"
DISPLAYS = []                # [{"id", "label", "size"}], reported by Electron
_EV_COND = threading.Condition()


def publish(kind, data=None):
    """Queue an event for the UI (status text, an answer, a face change)."""
    global _EV_N, _FACE
    with _EV_COND:
        _EV_N += 1
        if kind == "face":
            _FACE = data
        _EV.append((_EV_N, kind, data))
        del _EV[:-200]
        _EV_COND.notify_all()


def events(after, wait=20.0):
    """Events newer than `after`; waits up to `wait` s for the first one."""
    with _EV_COND:
        if not any(i > after for i, _, _ in _EV):
            _EV_COND.wait(wait)
        return [{"id": i, "kind": k, "data": d} for i, k, d in _EV if i > after]


ERROR_RE = r"(error|couldn'?t|could not|failed|model offline)"


class Remote:
    """What run() and push_to_talk() talk to for a request from the UI:
    results become events; anything else (Settings, the chat window) is
    still the Tk bar's until phases E2-E4."""

    def __init__(self, bar):
        self.bar = bar

    def __getattr__(self, name):
        return getattr(self.bar, name)

    def status(self, msg, keep=False):
        import re
        publish("status", {"text": msg})
        self.bar.set_face("error" if re.match(ERROR_RE, msg) else "done")

    def show_answer(self, question, text):
        publish("answer", {"question": question, "text": text})
        import voice
        voice.maybe_speak(text)

    def hide(self):
        publish("hide")

    def unfocus(self):
        publish("hide")


_TOASTS = {}                 # toast id -> {button id: callable or None}


def toast(title, text, sub="", buttons=(("OK", None, False),), rows=None,
          kind="info", sound=False):
    """Show a toast in the notch. `buttons`: (label, fn or None, primary);
    fn runs in a thread when pressed, None just dismisses. `rows`: optional
    [(left, right)] lines. Returns the toast id."""
    tid = secrets.token_hex(4)
    _TOASTS[tid] = {str(i): b[1] for i, b in enumerate(buttons)}
    while len(_TOASTS) > 50:                      # never-clicked ones
        _TOASTS.pop(next(iter(_TOASTS)))
    if sound:
        import winsound
        winsound.PlaySound("SystemNotification", winsound.SND_ALIAS | winsound.SND_ASYNC)
    publish("toast", {"id": tid, "title": title, "text": str(text)[:300], "sub": str(sub or "")[:200],
                      "kind": kind, "rows": [list(map(str, r)) for r in (rows or [])][:12],
                      "buttons": [{"id": str(i), "label": b[0], "primary": bool(b[2])}
                                  for i, b in enumerate(buttons)]})
    return tid


def toast_click(tid, button):
    fn = _TOASTS.pop(tid, {}).get(str(button))
    if fn:
        threading.Thread(target=fn, daemon=True).start()
    return {"ok": True}


def ui_day():
    """The Today card: open tasks, the next meeting, focus, a habit."""
    import focus
    import gcal
    import tasks
    today = date.today().isoformat()
    out = {"tasks": [{"key": i["created"], "text": i["text"], "due": i.get("due"),
                      "sub": bool(i.get("parent"))} for i in tavinor.open_items()][:6],
           "open": len(tavinor.open_items()), "next": None, "focus": None, "habit": None}
    f = focus.current()
    if f:
        out["focus"] = {"task": f["task"], "left": round(f["left"])}
    for h in tasks.load_habits():
        if not h["archived"] and today not in h["checkins"]:
            out["habit"] = h["name"]
            break
    if gcal.signed_in():
        try:
            now = datetime.now()
            for e in gcal.events(date.today(), date.today() + timedelta(days=1)):
                if not e["all_day"] and e["end"].replace(tzinfo=None) > now:
                    out["next"] = {"title": e["title"], "start": e["start"].strftime("%H:%M"),
                                   "end": e["end"].strftime("%H:%M")}
                    break
        except Exception:
            pass
    return out


def mark_done_key(key):
    """Tick off one open item by its `created` stamp. Returns its text."""
    with tavinor._LOCK:
        items = tavinor.load()
        for i in items:
            if not i["done"] and i["created"] == key:
                i["done"] = True
                tavinor.save(items)
                return i["text"]
    return None


def ui_call(bar, tool, args):
    if tool == "hello":
        import soul
        with _EV_COND:
            import uisettings
            return {"last": _EV_N, "face": _FACE, "name": soul.name(),
                    "side": tavinor.setting("notch_side", "center"),
                    "screen": tavinor.setting("notch_screen", "primary"),
                    "config": uisettings.ui_config()}
    if tool == "events":
        return events(int(args.get("after", 0)), float(args.get("wait", 20)))
    if tool == "ask":
        text = str(args.get("text", "")).strip()[:2000]
        if text:
            threading.Thread(target=tavinor.run, args=(text, Remote(bar)),
                             daemon=True).start()
        return {"ok": bool(text)}
    if tool == "mic":
        import voice
        r = Remote(bar)
        voice.push_to_talk(r, heard=lambda t: (publish("heard", {"text": t}),
                                               tavinor.run(t, r)))
        return {"ok": True}
    if tool == "day":
        return ui_day()
    if tool == "displays":
        global DISPLAYS
        DISPLAYS = [{"id": str(d.get("id", ""))[:40], "label": str(d.get("label", ""))[:60],
                     "size": str(d.get("size", ""))[:40]}
                    for d in (args.get("list") or [])[:8] if isinstance(d, dict)]
        return {"ok": True}
    if tool == "speak":                       # the speaker buttons: always, on demand
        import voice
        if args.get("stop"):
            voice.stop_speaking()
        else:
            voice.speak(str(args.get("text", ""))[:4000])
        return {"ok": True}
    if tool.startswith("chat_"):
        import agent
        return agent.ui_call(bar, tool, args)
    if tool == "toast_click":
        return toast_click(str(args.get("id", "")), args.get("button"))
    if tool == "done":
        return {"text": mark_done_key(str(args.get("key", "")))}
    if tool == "settings":
        bar.show_settings()
        return {"ok": True}
    if tool == "settings_page":
        import uisettings
        return uisettings.page(str(args.get("name", "main")))
    if tool == "settings_set":
        import uisettings
        return uisettings.act(bar, str(args.get("page", "main")), str(args.get("id", "")),
                              args.get("value"))
    raise ValueError("unknown ui tool %r" % tool)


def serve(approve, face=None, ui=None):
    """Start the endpoint; returns the server. `approve` as in call();
    `ui` is the Bar, for the Electron front end's calls."""
    token = secrets.token_urlsafe(24)

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            try:
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            except (ValueError, OSError):
                body = b""
            # The body is read first even when refused: replying with it
            # unread made Windows reset the connection ~1 in 3 times, so a
            # wrong token looked like "Tavinor isn't running".
            if ui is not None and self.headers.get("X-Tavinor-UI") == UI_TOKEN:
                try:
                    req = json.loads(body or b"{}")
                    out = {"ok": True, "data": ui_call(ui, str(req.get("tool")),
                                                       req.get("args") or {})}
                except Exception as e:
                    out = {"ok": False, "error": str(e)}
                return self._reply(json.dumps(out).encode())
            if self.headers.get("X-Cane-Token") != token:
                self.send_response(403)
                self.end_headers()
                return
            try:
                req = json.loads(body or b"{}")
                ok, text = call(str(req.get("tool")), req.get("args") or {}, approve, face)
            except Exception as e:
                ok, text = False, "bad request: %s" % e
            self._reply(json.dumps({"ok": ok, "text": text}).encode())

        def _reply(self, body):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    FILE.write_text(json.dumps({"port": srv.server_port, "token": token,
                                "pid": os.getpid(),
                                "started": datetime.now().isoformat(timespec="seconds")}),
                    encoding="utf-8")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def approver(bar):
    """approve() for the real app: a Do it / Cancel card, waited on."""
    def approve(lines):
        done, answer = threading.Event(), []

        def yes():
            answer.append(True)
            done.set()
        bar.confirm("AGENT", lines, yes)
        done.wait(APPROVE_WAIT)
        return bool(answer)
    return approve
