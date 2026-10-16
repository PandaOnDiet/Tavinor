"""The running app's side of the agent hooks (phase 12).

A tiny HTTP endpoint on 127.0.0.1 (random port) that mcp_server.py calls.
Each start writes bridge.json beside cane.py with the port and a fresh
random token; requests without that token are refused. Anything that
changes your data shows a Do it / Cancel card in Cane and waits for you -
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

import cane

FILE = Path(os.environ.get("CANE_BRIDGE") or Path(__file__).with_name("bridge.json"))
APPROVE_WAIT = 120

READS = {"get_day", "search_notes"}
WRITES = {"add_task", "add_note", "start_focus"}
COSMETIC = {"animate_face"}


def get_day():
    import focus
    import tasks
    import gcal
    today = date.today().isoformat()
    items = cane.open_items()
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
        return cane.dispatch({"intent": "task", "action": "add",
                              "arg": str(args.get("text", "")),
                              "when": str(args.get("due", "") or "")})
    if tool == "add_note":
        return cane.dispatch({"intent": "note", "action": "add",
                              "arg": str(args.get("text", ""))})
    if tool == "start_focus":
        return cane.dispatch({"intent": "focus", "action": "start",
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
        return False, "declined by the user in Cane"
    try:
        out = run(tool, args)
    except Exception as e:
        return False, "failed: %s" % e
    # Live, Claude Code couldn't tell an approved-and-saved task from a
    # pending one when only the result came back. Say it.
    return True, ("approved by the user in Cane and done - " + out) \
        if tool in WRITES else out


def serve(approve, face=None):
    """Start the endpoint; returns the server. `approve` as in call()."""
    token = secrets.token_urlsafe(24)

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            if self.headers.get("X-Cane-Token") != token:
                self.send_response(403)
                self.end_headers()
                return
            try:
                n = int(self.headers.get("Content-Length", 0))
                req = json.loads(self.rfile.read(n) or b"{}")
                ok, text = call(str(req.get("tool")), req.get("args") or {}, approve, face)
            except Exception as e:
                ok, text = False, "bad request: %s" % e
            body = json.dumps({"ok": ok, "text": text}).encode()
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
