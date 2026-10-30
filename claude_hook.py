"""Claude Code in Tavinor's notch: its permission prompts and finished turns.

Claude Code runs this for each hook event, with the event as JSON on stdin
(Settings > General > Connect Claude Code puts it in ~/.claude/settings.json).
A permission prompt becomes an Allow / Deny / Terminal card in the notch and
the click goes back as Claude Code's answer. Tavinor not running, no click in
time, or Terminal: no answer, so Claude Code asks in the terminal as usual -
this never blocks or denies anything by itself. Standard library only; it
never imports tavinor.py.
"""
import json
import os
import sys
import urllib.request
from pathlib import Path

# Connect copies this file beside Tavinor's bridge.json.
BRIDGE = Path(os.environ.get("CANE_BRIDGE") or Path(__file__).with_name("bridge.json"))
WAIT = 75                   # the notch waits 60 s for a click (bridge.CLAUDE_WAIT)
SETTINGS = Path.home() / ".claude" / "settings.json"
EVENTS = ("PermissionRequest", "Stop")
MARK = "claude_hook.py"     # how our entries are found again in settings.json


def send(event):
    """Tavinor's reply, or "" when it isn't running or didn't answer."""
    try:
        info = json.loads(BRIDGE.read_text(encoding="utf-8"))
        req = urllib.request.Request(
            "http://127.0.0.1:%d/" % info["port"],
            json.dumps({"tool": "claude_event", "args": event}).encode(),
            {"Content-Type": "application/json", "X-Cane-Token": info["token"]})
        with urllib.request.urlopen(req, timeout=WAIT) as r:
            out = json.load(r)
        return str(out.get("text", "")) if out.get("ok") else ""
    except Exception:
        return ""


def answer(event, reply):
    """The hook's output for Tavinor's reply; None leaves Claude Code's own prompt."""
    if event.get("hook_event_name") != "PermissionRequest" or reply not in ("allow", "deny"):
        return None
    decision = {"behavior": reply}
    if reply == "deny":
        decision["message"] = "Denied in Tavinor"
    return {"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": decision}}


def _without(hooks):
    """hooks with our entries taken out, and any group or event left empty."""
    out = {}
    for ev, groups in hooks.items():
        kept = []
        for g in groups:
            hs = [h for h in g.get("hooks", []) if MARK not in str(h.get("command", ""))]
            if hs:
                kept.append(dict(g, hooks=hs))
        if kept:
            out[ev] = kept
    return out


def _load(path):
    # Unreadable JSON raises: we never overwrite a settings file we can't read.
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _save(path, data):
    if path.exists():
        path.with_name(path.name + ".tavinor-backup").write_bytes(path.read_bytes())
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def install(script, path=SETTINGS):
    """Add our hooks to Claude Code's settings (backed up first); others stay."""
    data = _load(path)
    hooks = _without(data.get("hooks") or {})
    cmd = 'python "%s"' % str(script).replace("\\", "/")
    for ev in EVENTS:
        hooks.setdefault(ev, []).append(
            {"hooks": [{"type": "command", "command": cmd, "timeout": WAIT + 15}]})
    data["hooks"] = hooks
    path.parent.mkdir(parents=True, exist_ok=True)
    _save(path, data)


def remove(path=SETTINGS):
    data = _load(path)
    hooks = _without(data.get("hooks") or {})
    if hooks:
        data["hooks"] = hooks
    else:
        data.pop("hooks", None)
    _save(path, data)


def installed(path=SETTINGS):
    try:
        hooks = _load(path).get("hooks") or {}
    except (OSError, ValueError):
        return False
    return _without(hooks) != hooks


def main():
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return
    out = answer(event, send(event)) if isinstance(event, dict) else None
    if out:
        print(json.dumps(out))


if __name__ == "__main__":
    main()
