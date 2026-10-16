"""Cane as an MCP server (phase 12), for Claude Code, Codex or any agent.

    claude mcp add cane -- python "D:\\VS Code\\Cane\\mcp_server.py"

Speaks MCP over stdio (newline-delimited JSON-RPC 2.0) and forwards every
tool call to the running Cane through bridge.py, so changes appear as a
Do it / Cancel card in Cane and wait for you. If Cane isn't running, tools
say so instead of acting. Standard library only; it never imports cane.py,
so starting it can't start a second Cane.
"""
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

BRIDGE = Path(os.environ.get("CANE_BRIDGE") or Path(__file__).with_name("bridge.json"))
VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
WAIT = 150                  # a write waits for your click in Cane

TOOLS = [
    {"name": "get_day",
     "description": "The user's day from Cane: open tasks (numbered), focus "
                    "session, habits done today, and today's calendar.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "search_notes",
     "description": "Find the user's notes by meaning.",
     "inputSchema": {"type": "object", "required": ["query"],
                     "properties": {"query": {"type": "string"}}}},
    {"name": "add_task",
     "description": "Add a task to the user's list. The user approves it in "
                    "Cane first.",
     "inputSchema": {"type": "object", "required": ["text"],
                     "properties": {"text": {"type": "string"},
                                    "due": {"type": "string", "description":
                                            "optional, e.g. 'friday at 5pm'"}}}},
    {"name": "add_note",
     "description": "Save a note. The user approves it in Cane first.",
     "inputSchema": {"type": "object", "required": ["text"],
                     "properties": {"text": {"type": "string"}}}},
    {"name": "start_focus",
     "description": "Start a focus session (closes distracting sites until it "
                    "ends). The user approves it in Cane first.",
     "inputSchema": {"type": "object",
                     "properties": {"task": {"type": "string"},
                                    "minutes": {"type": "integer", "minimum": 1,
                                                "maximum": 480}}}},
    {"name": "animate_face",
     "description": "Change the face on Cane's bar for a moment.",
     "inputSchema": {"type": "object", "required": ["state"],
                     "properties": {"state": {"type": "string", "enum": [
                         "idle", "listening", "thinking", "done", "error",
                         "focus", "eyes"]}}}},
]


def forward(tool, args):
    """(ok, text) from the running Cane."""
    try:
        info = json.loads(BRIDGE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False, "Cane isn't running (no bridge.json) - start Cane first"
    req = urllib.request.Request(
        "http://127.0.0.1:%d/" % info["port"],
        json.dumps({"tool": tool, "args": args}).encode(),
        {"Content-Type": "application/json", "X-Cane-Token": info["token"]})
    try:
        with urllib.request.urlopen(req, timeout=WAIT) as r:
            out = json.load(r)
        return bool(out.get("ok")), str(out.get("text", ""))
    except urllib.error.HTTPError as e:
        return False, "Cane refused the call (%d)" % e.code
    except (urllib.error.URLError, OSError):
        return False, "Cane isn't running - start Cane first"


def handle(msg):
    """One JSON-RPC message -> a reply dict, or None for notifications."""
    mid, method = msg.get("id"), msg.get("method")
    if mid is None:
        return None                                  # notifications/initialized etc.
    if method == "initialize":
        want = (msg.get("params") or {}).get("protocolVersion")
        result = {"protocolVersion": want if want in VERSIONS else VERSIONS[0],
                  "capabilities": {"tools": {}},
                  "serverInfo": {"name": "cane", "version": "1.0"}}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        p = msg.get("params") or {}
        name = p.get("name")
        if name not in {t["name"] for t in TOOLS}:
            return {"jsonrpc": "2.0", "id": mid,
                    "error": {"code": -32602, "message": "unknown tool %r" % name}}
        ok, text = forward(name, p.get("arguments") or {})
        result = {"content": [{"type": "text", "text": text}], "isError": not ok}
    else:
        return {"jsonrpc": "2.0", "id": mid,
                "error": {"code": -32601, "message": "unknown method %r" % method}}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            reply = {"jsonrpc": "2.0", "id": None,
                     "error": {"code": -32700, "message": "parse error"}}
        else:
            reply = handle(msg) if isinstance(msg, dict) else None
        if reply is not None:
            sys.stdout.write(json.dumps(reply) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
