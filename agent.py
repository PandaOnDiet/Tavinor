"""Cane's own chat: tasks are done here, by the configured provider, through
Cane's own commands. Nothing is handed to an external CLI.

Every provider speaks this, including ones with no tool-calling support: the
model replies with one small JSON object, Cane runs the actions through the
same dispatch() the bar uses, and feeds the results back. Up to MAX_STEPS
rounds per message, so "set a timer and remind me after" finishes in one go.
"""
import json
import re
import threading
from datetime import datetime
from pathlib import Path

import cane

CHAT = Path(__file__).with_name("chat.json")
MAX_STEPS = 4          # model rounds per user message
CONTEXT = 24           # stored messages sent back to the model
KEEP = 400             # stored messages kept on disk

# Tools the model may call, in dispatch()'s own intent format. "ask",
# "escalate", "copilot" and "settings" are left out on purpose: the chat IS
# the place answers and tasks land, and it must never bounce to itself.
TOOLS = """\
{"intent":"volume","arg":"0-100"}                  set an exact volume
{"intent":"media","action":"play|pause|next|prev|volup|voldown|mute"}
{"intent":"brightness","arg":"0-100"}  or  {"intent":"brightness","action":"up|down"}
{"intent":"timer","arg":"<number>","action":"s|m|h"}
{"intent":"theme","arg":"dark|light"}             Windows dark or light mode
{"intent":"screenshot"}
{"intent":"lock"}                                  lock the laptop
{"intent":"launch","arg":"<app or site name>"}     open an app or website
{"intent":"google","arg":"<search terms>"}         open a web search
{"intent":"search_play","arg":"<song name>"}       play a song on YouTube
{"intent":"weather","arg":"<place or empty>"}      live weather
{"intent":"clock"}                                 current time and date
{"intent":"remember","arg":"<thing to track>"}     add an open item
{"intent":"remind","arg":"<thing>","when":"<time phrase as the user said it>"}
{"intent":"recall"}                                list open items
{"intent":"done","arg":"<item number or words>"}   mark an open item done
{"intent":"files","arg":"<the file request>"}      plan a file move (the user approves it)
{"intent":"undo"}                                  put the last moved files back"""

# Run straight away: cheap, reversible, or only shows something. Everything
# else shows an approval card first. "files" only builds a plan - the plan
# panel is its own approval step.
INSTANT = {"volume", "media", "brightness", "timer", "theme", "screenshot",
           "launch", "google", "search_play", "weather", "clock", "remember",
           "remind", "recall", "files"}
# Tools other modules add (focus mode, ...): {intent: (doc line, instant?)}.
EXTRA = {}

_LOCK = threading.Lock()


def tools_doc():
    return TOOLS + "".join("\n" + doc for doc, _ in EXTRA.values())


def allowed():
    return (set(INSTANT) | {"lock", "done", "undo"}
            | set(EXTRA))


def instant(kind):
    return kind in INSTANT or EXTRA.get(kind, ("", False))[1]


def system_prompt():
    name = soul_name()
    return (
        "You are %s, an assistant on the user's Windows laptop, working inside "
        "your own chat window. You act only through the tools below. You cannot "
        "run code, open a terminal or touch files except through these tools.\n\n"
        "Reply with ONE JSON object and nothing else:\n"
        '{"say": "<short message to the user>", "actions": [<tool calls>], '
        '"remember": "<optional>"}\n\n'
        "Tools:\n%s\n\n"
        "After your actions run you get a RESULTS message. Then either act "
        "again or finish with \"actions\": []. Never claim something happened "
        "before its result says so. If a tool reports a failure, say so plainly. "
        "If the request needs something no tool can do (writing code, a long "
        "answer), do what you can in \"say\" - code goes in backticks.\n"
        "\"remember\": only when the user reveals a lasting preference or fact "
        "about themselves worth keeping; one short sentence. They will be asked "
        "before it is saved. Omit it otherwise.\n"
        "The local time is %s.%s"
        % (name, tools_doc(), datetime.now().strftime("%A %d %B %Y, %H:%M"),
           soul_text()))


def soul_name():
    try:
        import soul
        return soul.name()
    except ImportError:
        return "Cane"


def soul_text():
    try:
        import soul
        p = soul.prompt()
        return "\n\n" + p if p else ""
    except ImportError:
        return ""


# ------------------------------------------------------------- history

def load():
    try:
        rows = json.loads(CHAT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(rows, list):
        return []
    return [r for r in rows if isinstance(r, dict)
            and r.get("role") in ("user", "assistant")
            and isinstance(r.get("content"), str)]


def save(rows):
    with _LOCK:
        CHAT.write_text(json.dumps(rows[-KEEP:], indent=1), encoding="utf-8")


def clear():
    with _LOCK:
        CHAT.unlink(missing_ok=True)


def turns(rows):
    """What the model sees: the recent rows, roles strictly alternating and
    starting with the user. Claude rejects two user turns in a row, which a
    failed call in the middle of a round would otherwise leave behind."""
    out = []
    for r in rows[-CONTEXT:]:
        if out and out[-1]["role"] == r["role"]:
            out[-1]["content"] += "\n\n" + r["content"]
        else:
            out.append({"role": r["role"], "content": r["content"]})
    while out and out[0]["role"] != "user":
        out.pop(0)
    return out


# ------------------------------------------------------------- one reply

def parse(text):
    """{"say", "actions", "remember"} from a model reply.

    A reply that isn't JSON is still a reply: it becomes the message, with no
    actions. Unknown tools are dropped here, before anything can run them.
    """
    start, end = text.find("{"), text.rfind("}")
    raw = None
    if 0 <= start < end:
        try:
            raw = json.loads(text[start:end + 1])
        except ValueError:
            raw = None
    if not isinstance(raw, dict):
        return {"say": text.strip(), "actions": [], "remember": "", "dropped": 0}
    acts, dropped = [], 0
    for a in raw.get("actions") or []:
        if isinstance(a, dict) and a.get("intent") in allowed():
            acts.append(clean(a))
        else:
            dropped += 1
    rem = raw.get("remember")
    return {"say": str(raw.get("say") or "").strip(), "actions": acts,
            "remember": rem.strip()[:200] if isinstance(rem, str) else "",
            "dropped": dropped}


def clean(a):
    """Model-written values into the shapes dispatch() expects."""
    kind = a["intent"]
    out = {"intent": kind}
    for k in ("arg", "action", "when", "minutes", "site", "from", "to", "days",
              "parent", "apps", "every", "at"):
        if a.get(k) not in (None, ""):
            out[k] = str(a[k]).strip()
    if kind in ("volume", "brightness") and "arg" in out:
        out["arg"] = re.sub(r"\D", "", out["arg"])
    if kind == "timer":
        out["arg"] = re.sub(r"\D", "", out.get("arg", ""))
        out["action"] = (out.get("action", "m").lower() or "m")[0]
    if "action" in out:
        out["action"] = out["action"].lower()
    if kind in ("brightness", "theme", "media") and "arg" in out:
        out["arg"] = out["arg"].lower()
    return out


def describe(a):
    try:
        return cane.describe(a)
    except (KeyError, TypeError):
        return a["intent"]


def run_action(a, ui):
    try:
        return str(cane.dispatch(a, ui))
    except Exception as e:           # one bad action must not end the chat
        return "error: %s" % e


def converse(text, ui=None, show=None, approve=None, propose=None,
             complete=None):
    """Handle one user message to the end. Returns the stored rows.

    show(kind, text): "say" | "result" | "error" | "note" - for the window.
    approve(actions) -> bool: blocks until the user decides. None = decline.
    propose(fact): a memory the model wants to keep (phase 3 asks the user).
    complete: injectable for tests; defaults to cane.complete.
    """
    show = show or (lambda kind, t: None)
    complete = complete or cane.complete
    rows = load()
    rows.append({"role": "user", "content": text})
    save(rows)
    for step in range(MAX_STEPS):
        try:
            reply, note = complete(system_prompt(), turns(rows), 45, "low")
        except RuntimeError as e:
            show("error", "Couldn't reach the model: %s" % e)
            if step == 0:
                # Unanswered, it must not ride along with the next message:
                # live, a failed "set a timer" was carried out a message later.
                rows.pop()
                save(rows)
            break
        if note:
            show("note", note.strip())
        p = parse(reply)
        rows.append({"role": "assistant", "content": reply.strip() or "{}",
                     "say": p["say"]})
        save(rows)
        # A reply that comes with actions is written before they run, and
        # live it stated their outcome anyway ("Current time: 23:03" - it was
        # 23:05). Only the reply after the RESULTS is shown as fact.
        if p["say"] and not p["actions"]:
            show("say", p["say"])
        if p["remember"] and propose:
            propose(p["remember"])
        if not p["actions"]:
            break
        risky = [a for a in p["actions"] if not instant(a["intent"])]
        ok = True
        if risky:
            ok = bool(approve and approve(p["actions"]))
        results = []
        for a in p["actions"]:
            if ok:
                r = run_action(a, ui)
            else:
                r = "not done - the user declined"
            results.append("%s -> %s" % (describe(a), r))
            show("result", results[-1])
        if p["dropped"]:
            results.append("%d unknown tool call(s) ignored" % p["dropped"])
        rows.append({"role": "user", "content": "RESULTS:\n" + "\n".join(results)})
        save(rows)
        if not ok:
            # The user said no. Stop here rather than ask the model again,
            # which tends to propose the same thing a second time.
            show("note", "Nothing was done.")
            break
    else:
        show("note", "Stopped after %d rounds of actions." % MAX_STEPS)
    return rows


# ------------------------------------------------------------- window

def open_window(bar, first=None, back=None):
    """`first`: a message to send at once. `back`: reopen Settings."""
    bar.root.after(0, lambda: _window(bar, first, back))


def _window(bar, first=None, back=None):
    import tkinter as tk
    from cane import PANEL, FG, DIM, ACCENT, FONT_BODY, FONT_SM, MARGIN

    if getattr(bar, "_chat", None):
        try:
            bar._chat["win"].lift()
            if first:
                bar._chat["send"](first)
            return
        except tk.TclError:
            bar._chat = None

    W, H = 560, 560
    w = tk.Toplevel(bar.root)
    cv = cane.make_panel(w, W, H)
    x0 = bar._x()
    y0 = MARGIN          # the bar hides as the chat opens; don't leave a gap
    w.geometry("+%d+%d" % (x0 + 40, y0))
    state = {"win": w, "busy": False}
    bar._chat = state

    def close(*_):
        bar._chat = None
        cane.slide(w, x0 + 40, y0, 0.0, steps=8, then=w.destroy)

    bar.page_header(w, cv, "CHAT · " + soul_name().upper(), close,
                    back and (lambda: (close(), back())))
    model = cv.create_text(W - 60, 28, text=cane.current_model(), fill=DIM,
                           anchor="e", font=FONT_SM)

    txt = tk.Text(w, bg=PANEL, fg=FG, font=FONT_BODY, wrap="word",
                  relief="flat", borderwidth=0, highlightthickness=0,
                  padx=0, pady=0, spacing1=3, spacing3=3, cursor="arrow")
    txt.tag_config("me", foreground=ACCENT)
    txt.tag_config("dim", foreground=DIM, font=FONT_SM)
    txt.tag_config("err", foreground="#ff8a7a")
    txt.tag_config("code", font=("Consolas", 10), foreground="#cfe3ff",
                   background="#1d2027")
    txt.bind("<MouseWheel>",
             lambda e: txt.yview_scroll(-e.delta // 120, "units"))
    cv.create_window(24, 48, window=txt, anchor="nw", width=W - 48,
                     height=H - 48 - 92)

    entry = tk.Entry(w, font=("Segoe UI", 12), bg="#22252c", fg=FG,
                     insertbackground=ACCENT, relief="flat",
                     highlightthickness=0, borderwidth=0)
    cv.create_window(24, H - 66, window=entry, anchor="w", width=W - 48,
                     height=30)
    status = cv.create_text(24, H - 28, text="", fill=DIM, anchor="w",
                            font=FONT_SM)

    def put(kind, t):
        """Append to the transcript. UI thread only."""
        txt.config(state="normal")
        if kind == "me":
            txt.insert("end", "You  ", "dim")
            txt.insert("end", t + "\n", "me")
        elif kind == "say":
            for k, piece in enumerate(re.split(r"`([^`]+)`", t)):
                if piece:
                    txt.insert("end", piece, "code" if k % 2 else "")
            txt.insert("end", "\n")
        elif kind == "error":
            txt.insert("end", t + "\n", "err")
        else:
            txt.insert("end", ("• " if kind == "result" else "") + t + "\n", "dim")
        txt.config(state="disabled")
        txt.see("end")

    def card(lines, buttons):
        """An inline card with buttons. Returns a function that removes the
        buttons and leaves the outcome in their place."""
        txt.config(state="normal")
        f = tk.Frame(txt, bg="#1e222a", padx=10, pady=8)
        for line in lines:
            tk.Label(f, text=line, bg="#1e222a", fg=FG, font=FONT_BODY,
                     anchor="w", justify="left", wraplength=W - 110).pack(
                         anchor="w")
        row = tk.Frame(f, bg="#1e222a")
        row.pack(anchor="e", pady=(6, 0))
        for label, cmd in buttons:
            b = bar._button(row, label, cmd)
            b.config(bg="#1e222a", activebackground="#1e222a")
            b.pack(side="right", padx=4)
        txt.window_create("end", window=f)
        txt.insert("end", "\n")
        txt.config(state="disabled")
        txt.see("end")

        def settle(outcome):
            for b in row.winfo_children():
                b.destroy()
            tk.Label(row, text=outcome, bg="#1e222a", fg=DIM,
                     font=FONT_SM).pack()
        return settle

    def ui_put(kind, t):
        w.after(0, lambda: put(kind, t))

    def approve(actions):
        """Blocks the worker until a button is pressed."""
        done, answer = threading.Event(), []

        def decide(ok):
            if not answer:
                answer.append(ok)
                settle("approved" if ok else "declined")
                done.set()
        settle = None

        def make():
            nonlocal settle
            settle = card(["Allow these?"] + ["  • " + describe(a)
                                              for a in actions],
                          [("Allow", lambda: decide(True)),
                           ("Decline", lambda: decide(False))])
        w.after(0, make)
        done.wait(300)                 # five minutes, then it counts as no
        return bool(answer and answer[0])

    def propose(fact):
        def make():
            holder = {}

            def yes():
                try:
                    import soul
                    soul.remember(fact)
                    holder["s"]("saved to memory")
                except Exception as e:
                    holder["s"]("couldn't save: %s" % e)

            holder["s"] = card(["Remember this?", "  " + fact],
                               [("Remember", yes),
                                ("No", lambda: holder["s"]("not saved"))])
        w.after(0, make)

    def send(text=None):
        text = (text or entry.get()).strip()
        if not text or state["busy"]:
            return
        entry.delete(0, "end")
        put("me", text)
        state["busy"] = True
        cv.itemconfig(status, text="thinking…")
        cv.itemconfig(model, text=cane.current_model())

        def go():
            try:
                converse(text, ui=bar, show=ui_put, approve=approve,
                         propose=propose)
            except Exception as e:
                ui_put("error", "error: %s" % e)
            finally:
                def idle():
                    state["busy"] = False
                    try:
                        cv.itemconfig(status, text="")
                    except tk.TclError:
                        pass
                w.after(0, idle)
        threading.Thread(target=go, daemon=True).start()
    state["send"] = send

    def wipe():
        clear()
        txt.config(state="normal")
        txt.delete("1.0", "end")
        txt.config(state="disabled")

    cv.create_window(W - 24, H - 28, anchor="e",
                     window=bar._button(w, "Clear", wipe))
    if cane.setting("claude", False) and cane.shutil.which("claude"):
        def hand_off():
            last = next((r["content"] for r in reversed(load())
                         if r["role"] == "user"
                         and not r["content"].startswith("RESULTS:")), "")
            if last:
                cane.subprocess.Popen(
                    [cane.shutil.which("claude"), last], cwd=cane.ESCALATE_CWD,
                    creationflags=cane.subprocess.CREATE_NEW_CONSOLE)
                put("note", "handed to Claude Code: " + last[:60])
        cv.create_window(W - 84, H - 28, anchor="e",
                         window=bar._button(w, "Hand to Claude Code", hand_off))

    # Earlier conversation, so the chat picks up where it left off.
    for r in load()[-30:]:
        if r["role"] == "user" and r["content"].startswith("RESULTS:"):
            for line in r["content"].splitlines()[1:]:
                put("result", line)
        elif r["role"] == "user":
            put("me", r["content"])
        elif r.get("say") and not parse(r["content"])["actions"]:
            put("say", r["say"])

    entry.bind("<Return>", lambda e: send())
    w.focus_force()
    entry.focus_set()
    cane.slide(w, x0, y0, 0.97, x_from=x0 + 40)
    if first:
        send(first)
