"""The assistant's personality ("soul"): who it is, who you are, the rules it
works by, and what it has learned about you - like custom instructions in
ChatGPT, Claude or Copilot.

One hand-editable file, soul.md, read fresh on every use, so an edit in
Notepad applies to the very next answer. It goes to answers and the chat,
never to the small command router: that model is at its limit, and a page of
personality would cost it accuracy on "volume 40".

Memory is never written by the model on its own. The chat proposes a fact,
and it is saved only when you press Remember.
"""
import re
import threading
from pathlib import Path

import cane

SOUL = Path(__file__).with_name("soul.md")
SECTIONS = ("Identity", "About you", "Rules", "Memory")
DEFAULT_NAME = "Cane"

PRESETS = {
    "Concise": "Direct and brief. Answer first, no filler, no small talk.",
    "Warm": "Friendly and encouraging, but still gets to the point.",
    "Blunt": "Plain-spoken. Says when something is a bad idea and why.",
    "Butler": "Polite, formal and unflappable. Anticipates what is needed next.",
    "Coach": "Keeps you on track: nudges toward the next step, celebrates finishing.",
}

TEMPLATE = """# Soul
Read before every answer and chat. Edit freely - changes apply to the next
message. Lines in (brackets) are hints and are ignored.

## Identity
Name: {name}
{personality}

## About you
{about}

## Rules
{rules}

## Memory
{memory}
"""

_LOCK = threading.Lock()


def _blank():
    return {"name": DEFAULT_NAME, "personality": PRESETS["Concise"],
            "about": "(what to call you, your work, your hours, how you like "
                     "to work)",
            "rules": "- Reply in English.\n- Ask before doing anything that "
                     "deletes, sends or pays.",
            "memory": []}


def parse(text):
    """soul.md text -> {name, personality, about, rules, memory[]}.

    Forgiving on purpose: a missing section keeps its default, unknown
    sections are ignored, and a file that is just prose becomes the
    personality rather than being thrown away.
    """
    out = _blank()
    parts = re.split(r"^##\s+(.+?)\s*$", text, flags=re.M)
    if len(parts) == 1:
        if text.strip():
            out["personality"] = text.strip()
        return out
    found = {}
    for head, body in zip(parts[1::2], parts[2::2]):
        found[head.strip().lower()] = body.strip()
    ident = found.get("identity")
    if ident is not None:
        m = re.search(r"^name:\s*(.+)$", ident, re.M | re.I)
        if m and m.group(1).strip():
            out["name"] = m.group(1).strip()[:40]
        out["personality"] = re.sub(r"^name:.*$\n?", "", ident,
                                    flags=re.M | re.I).strip()
    if "about you" in found:
        out["about"] = found["about you"]
    if "rules" in found:
        out["rules"] = found["rules"]
    if "memory" in found:
        out["memory"] = [re.sub(r"^[-*•]\s*", "", l).strip()
                         for l in found["memory"].splitlines()
                         if l.strip() and not _hint(l)]
    return out


def render(s):
    return TEMPLATE.format(
        name=s["name"] or DEFAULT_NAME, personality=s["personality"].strip(),
        about=s["about"].strip(), rules=s["rules"].strip(),
        memory="\n".join("- " + m for m in s["memory"]) or "(nothing yet)")


def load():
    try:
        return parse(SOUL.read_text(encoding="utf-8"))
    except OSError:
        return _blank()


def save(s):
    with _LOCK:
        SOUL.write_text(render(s), encoding="utf-8")


def ensure():
    """Create soul.md on first use so there is something to open and edit."""
    if not SOUL.exists():
        save(_blank())


def name():
    return load()["name"]


def _hint(line):
    return bool(re.fullmatch(r"\s*\(.*\)\s*", line))


def _body(text):
    return "\n".join(l for l in text.splitlines() if not _hint(l)).strip()


def prompt():
    """The part of the system prompt that carries the soul, or "" when it is
    switched off in settings."""
    if not cane.setting("soul", True):
        return ""
    s = load()
    parts = []
    if _body(s["personality"]):
        parts.append("Your personality: " + _body(s["personality"]))
    if _body(s["about"]):
        parts.append("About the user:\n" + _body(s["about"]))
    if _body(s["rules"]):
        parts.append("The user's rules - always follow them:\n" + _body(s["rules"]))
    if s["memory"]:
        parts.append("What you know about the user:\n"
                     + "\n".join("- " + m for m in s["memory"]))
    return "\n\n".join(parts)


def remember(fact):
    """Add an approved fact. Returns False if it was already there."""
    fact = " ".join(str(fact).split())[:200]
    if not fact:
        return False
    with _LOCK:
        s = load()
        if any(m.lower() == fact.lower() for m in s["memory"]):
            return False
        s["memory"].append(fact)
        SOUL.write_text(render(s), encoding="utf-8")
    return True


def forget(index):
    with _LOCK:
        s = load()
        if 0 <= index < len(s["memory"]):
            gone = s["memory"].pop(index)
            SOUL.write_text(render(s), encoding="utf-8")
            return gone
    return None


# ------------------------------------------------------------- window

def open_window(bar):
    bar.root.after(0, lambda: _window(bar))


def _window(bar):
    import tkinter as tk
    from cane import PANEL, FG, DIM, ACCENT, FONT_BODY, FONT_SM, MARGIN

    if getattr(bar, "_soul_win", None):
        try:
            bar._soul_win.lift()
            return
        except tk.TclError:
            bar._soul_win = None
    ensure()
    s = load()
    W, H = 560, 690
    w = tk.Toplevel(bar.root)
    bar._soul_win = w
    cv = cane.make_panel(w, W, H)
    x0 = bar._x()
    w.geometry("+%d+%d" % (x0 + 40, MARGIN))

    def close(*_):
        bar._soul_win = None
        cane.slide(w, x0 + 40, MARGIN, 0.0, steps=8, then=w.destroy)

    cv.create_text(24, 28, text="PERSONALITY", fill=ACCENT, anchor="w",
                   font=("Segoe UI", 8, "bold"))
    cv.create_text(W - 26, 28, text="✕", fill=DIM, font=("Segoe UI", 11),
                   tags="close")
    cv.tag_bind("close", "<Button-1>", close)
    w.bind("<Escape>", close)

    field = dict(bg="#22252c", fg=FG, relief="flat", borderwidth=0,
                 highlightthickness=0, insertbackground=ACCENT)

    cv.create_text(24, 64, text="Name", fill=FG, anchor="w", font=FONT_BODY)
    name_var = tk.StringVar(value=s["name"])
    cv.create_window(W - 24, 64, anchor="e", width=300, height=24,
                     window=tk.Entry(w, textvariable=name_var, font=FONT_BODY,
                                     **field))

    def box(y, label, hint, text, h):
        cv.create_text(24, y, text=label, fill=FG, anchor="w", font=FONT_BODY)
        cv.create_text(W - 24, y, text=hint, fill=DIM, anchor="e", font=FONT_SM)
        t = tk.Text(w, font=FONT_SM, wrap="word", padx=6, pady=4, **field)
        t.insert("1.0", text)
        cv.create_window(24, y + 14, anchor="nw", width=W - 48, height=h,
                         window=t)
        return t

    cv.create_text(24, 100, text="Preset", fill=FG, anchor="w", font=FONT_BODY)
    preset = tk.StringVar(value="choose…")
    pick = tk.OptionMenu(w, preset, *PRESETS)
    pick.config(bg="#22252c", fg=FG, relief="flat", borderwidth=0,
                highlightthickness=0, font=FONT_SM, cursor="hand2",
                activebackground="#2b2f38", activeforeground=FG)
    pick["menu"].config(bg=PANEL, fg=FG, activebackground=ACCENT, font=FONT_SM)
    cv.create_window(W - 24, 100, anchor="e", window=pick)

    pers = box(132, "Personality", "how it talks and behaves", s["personality"], 56)
    about = box(222, "About you", "name, work, hours, habits", s["about"], 70)
    rules = box(326, "Rules", "how work must be done", s["rules"], 70)
    preset.trace_add("write", lambda *_: (pers.delete("1.0", "end"),
                                          pers.insert("1.0", PRESETS[preset.get()])))

    cv.create_text(24, 430, text="Memory", fill=FG, anchor="w", font=FONT_BODY)
    cv.create_text(W - 24, 430, text="saved only when you approve them",
                   fill=DIM, anchor="e", font=FONT_SM)
    mem = tk.Frame(w, bg=PANEL)
    cv.create_window(24, 444, anchor="nw", width=W - 48, height=124, window=mem)

    def draw_memory():
        for c in mem.winfo_children():
            c.destroy()
        facts = load()["memory"]
        if not facts:
            tk.Label(mem, text="nothing yet - the chat will ask before "
                     "remembering anything", bg=PANEL, fg=DIM,
                     font=FONT_SM).pack(anchor="w")
        for i, f in enumerate(facts[:6]):
            row = tk.Frame(mem, bg=PANEL)
            row.pack(fill="x")
            tk.Label(row, text="• " + f[:70], bg=PANEL, fg=FG, font=FONT_SM,
                     anchor="w").pack(side="left")
            bar._button(row, "forget", lambda i=i: (forget(i), draw_memory())
                        ).pack(side="right")
        if len(facts) > 6:
            tk.Label(mem, text="+ %d more in soul.md" % (len(facts) - 6),
                     bg=PANEL, fg=DIM, font=FONT_SM).pack(anchor="w")
    draw_memory()

    tag = "soul_on"
    cv.create_text(24, 594, text="Use it for answers and chat", fill=FG,
                   anchor="w", font=FONT_BODY, tags=tag)
    cv.create_text(24, 606, text="it goes to your answer provider with each "
                   "message", fill=DIM, anchor="nw", font=FONT_SM, tags=tag)
    bar._toggle(cv, W - 60, 586, cane.setting("soul", True),
                lambda on: cane.set_setting("soul", on), (tag,))

    note = cv.create_text(24, H - 24, text="", fill=DIM, anchor="w",
                          font=FONT_SM)

    def save_all():
        cur = load()               # keep memory added while this was open
        cur.update(name=" ".join(name_var.get().split())[:40] or DEFAULT_NAME,
                   personality=pers.get("1.0", "end").strip(),
                   about=about.get("1.0", "end").strip(),
                   rules=rules.get("1.0", "end").strip())
        save(cur)
        cv.itemconfig(note, text="saved - applies to the next message")

    go = bar._button(w, "Save", save_all)
    go.config(fg=ACCENT, font=("Segoe UI", 9, "bold"))
    cv.create_window(W - 24, H - 24, anchor="e", window=go)
    cv.create_window(W - 80, H - 24, anchor="e", window=bar._button(
        w, "Open soul.md", lambda: cane.os.startfile(str(SOUL))))
    w.focus_force()
    cane.slide(w, x0, MARGIN, 0.97, x_from=x0 + 40)
