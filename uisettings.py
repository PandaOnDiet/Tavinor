"""Settings for the Electron UI (phase E4, done early).

Each page is described here as plain data - sections of rows (toggle, link,
radio, select, combo, text, button, list, info) - and the UI draws any page
from that. Every change goes through act(), which calls the same functions
the Tk settings window called, so behaviour is unchanged; only the look moved.

Slow actions (Test, Test microphone, Google sign-in) return at once and
report later with a bridge "note" event: {"page", "id", "text", "ok"}.
"""
import json
import threading
import time

import tavinor


def _note(page, rid, text, ok=True):
    import bridge
    bridge.publish("note", {"page": page, "id": rid, "text": text, "ok": ok})


def _later(page, rid, fn):
    """Run fn() off the request thread; its (text, ok) becomes a note."""
    def go():
        try:
            text, ok = fn()
        except Exception as e:                 # a failed test must still answer
            text, ok = str(e), False
        _note(page, rid, text, ok)
    threading.Thread(target=go, daemon=True).start()


def T(rid, label, desc, value):
    return {"type": "toggle", "id": rid, "label": label, "desc": desc, "value": bool(value)}


def L(page, label, desc):
    return {"type": "link", "id": page, "label": label, "desc": desc}


def I(text, dim=True):
    return {"type": "info", "text": text, "dim": dim}


def B(rid, label, confirm=None, note=""):
    return {"type": "button", "id": rid, "label": label, "confirm": confirm, "note": note}


# ------------------------------------------------------------------ pages

def N(page, label, value, icon):
    """A section on the first screen: icon, name, its current value, arrow."""
    return {"type": "nav", "id": page, "label": label, "value": value, "icon": icon}


def page_main():
    """The first screen: three quick switches, then one row per section.
    No descriptions here - they live inside each section."""
    import access, activity, focus, gcal, soul, voice
    started = (tavinor.STARTUP / "Tavinor.vbs").exists() or (tavinor.STARTUP / tavinor.OLD_VBS).exists()
    s = soul.load()
    preset = next((k for k, v in soul.PRESETS.items() if v == s["personality"].strip()), "custom")
    p = tavinor.provider()
    model = tavinor.current_model(p).split("/")[-1]
    fs = focus.load()
    return {"title": "Settings", "sections": [
        {"items": [
            T("startup", "Start with Windows", "", started),
            T("hover", "Hover to open", "", tavinor.setting("hover", True)),
            T("wake", "\"Hey %s\"" % s["name"], "", tavinor.setting("wake", False)),
        ]},
        {"items": [
            dict(N("chat", "Chats", "", "chat"), action=True),
            N("brain", "Brain", "%s · %s" % (tavinor.PROVIDERS[p][0], model), "brain"),
            N("personality", "Personality", "%s · %s" % (s["name"], preset.lower()), "person"),
            N("access", "Permissions", access.level().title(), "shield"),
        ]},
        {"items": [
            N("sites", "Focus & blocked sites", "%d site%s" % (
                len(fs["sites"]), "" if len(fs["sites"]) == 1 else "s"), "focus"),
            N("voice", "Voice", "Whisper" if voice.engine() == "whisper" else "Windows", "mic"),
            N("calendar", "Calendar", "Connected" if gcal.signed_in() else "Not connected", "calendar"),
            N("activity", "Activity", "On" if activity.on() else "Off", "chart"),
            N("display", "Display", display_value(), "display"),
            N("widget", "Widget", (tavinor.setting("widget_place") or {}).get("name", "No city"), "widget"),
            N("general", "General", "", "sliders"),
        ]},
    ]}


def page_brain():
    """What answers: provider, key, model; whether answers go to the cloud;
    how notes are searched."""
    import notes
    p = tavinor.provider()
    keyed = p != "ollama"
    k = tavinor.key_get(p) if keyed else None
    rows = notes.load()
    return {"title": "Brain", "sections": [
        {"title": "Answers", "items": [
            {"type": "select", "id": "provider", "label": "Provider", "value": p,
             "options": [{"value": key, "label": v[0]} for key, v in tavinor.PROVIDERS.items()]},
            {"type": "text", "id": "key", "label": "API key", "secret": True, "button": "Save",
             "disabled": not keyed, "value": "",
             "placeholder": "paste a key" if keyed else "not needed for Ollama",
             "desc": ("saved in Windows Credential Manager ····" + k[-4:]) if k else
                     ("no key saved" if keyed else "")},
            {"type": "combo", "id": "model", "label": "Model", "value": tavinor.current_model(p),
             "options_from": "models"},
            B("test", "Test", note="answers, and can do tasks"),
        ]},
        local_models_section(),
        {"items": [
            T("cloud_answers", "Cloud answers",
              "Off: questions open Copilot instead", tavinor.setting("cloud_answers", True)),
            T("embed", "Search notes on this laptop only",
              "%d notes · off: your provider's embedding model first" % len(rows),
              notes.source() == "ollama"),
        ]},
    ]}


# Local models, light to heavy: (ollama name, label, tier, download GB).
# Sizes read from registry.ollama.ai on 2026-10-19.
LOCAL_MODELS = [
    ("qwen3:0.6b", "Qwen3 0.6B", "Tiny", 0.5),
    ("qwen3:1.7b", "Qwen3 1.7B", "Light", 1.4),
    ("qwen3:4b", "Qwen3 4B", "Small", 2.5),
    ("qwen3:8b", "Qwen3 8B", "Medium", 5.2),
    ("qwen3:14b", "Qwen3 14B", "Large", 9.3),
    ("gpt-oss:20b", "gpt-oss 20B", "Heavy", 13.8),
    ("qwen3:30b", "Qwen3 30B", "Very heavy", 18.6),
]
_pulls = {}                    # name -> {"cancel": bool} while downloading


def _ram_gb():
    import ctypes

    class M(ctypes.Structure):
        _fields_ = [("len", ctypes.c_ulong), ("load", ctypes.c_ulong)] +                    [(n, ctypes.c_ulonglong) for n in ("total", "avail", "a", "b", "c", "d", "e")]
    m = M()
    m.len = ctypes.sizeof(M)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    return m.total / 2 ** 30


def _ollama(path, payload=None, method="POST", timeout=10):
    import json
    import urllib.request
    url = tavinor.OLLAMA.replace("/api/chat", path)
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data, {"Content-Type": "application/json"}, method=method)
    return urllib.request.urlopen(req, timeout=timeout)


def _installed():
    import json
    try:
        with _ollama("/api/tags", method="GET", timeout=2) as r:
            return {m["name"] for m in json.load(r).get("models", [])}
    except OSError:
        return None            # Ollama not running or not installed


def local_models_section():
    """Download a local model from light to heavy, then use it for answers."""
    import shutil
    if not shutil.which("ollama") and _installed() is None:
        return {"title": "Local models", "items": [
            I("Local models run through Ollama, a free app. Install it, then come back here."),
            B("lm_get", "Get Ollama", note="ollama.com/download")]}
    have = _installed() or set()
    ram = _ram_gb()
    using = tavinor.current_model("ollama") if tavinor.provider() == "ollama" else ""
    rows = []
    for name, label, tier, gb in LOCAL_MODELS:
        need = gb * 1.2 + 1                     # rough: weights plus working memory
        state = ("using" if name == using else "pulling" if name in _pulls
                 else "installed" if name in have else "absent")
        rows.append({"type": "model", "id": "lm:" + name, "label": label, "state": state,
                     "desc": "%s · %.1f GB download · needs about %d GB memory%s"
                             % (tier, gb, round(need), "" if need <= ram else " (more than this laptop has)")})
    return {"title": "Local models · on this laptop, offline", "items": rows}


def _pull(pg, rid, name):
    """Stream Ollama's pull and report progress as the row's note."""
    import json
    import time
    _pulls[name] = {"cancel": False}
    last = 0.0
    try:
        with _ollama("/api/pull", {"model": name, "stream": True}, timeout=60) as r:
            for line in r:
                if _pulls[name]["cancel"]:
                    _note(pg, rid, "cancelled - Download again resumes where it stopped", False)
                    return
                d = json.loads(line or b"{}")
                if d.get("error"):
                    raise RuntimeError(d["error"])
                if d.get("total") and time.monotonic() - last > 0.7:
                    last = time.monotonic()
                    _note(pg, rid, "downloading %d%% · %.1f of %.1f GB" % (
                        100 * d.get("completed", 0) / d["total"], d.get("completed", 0) / 1e9, d["total"] / 1e9))
                elif d.get("status") and not d.get("total"):
                    _note(pg, rid, d["status"])
        _note(pg, rid, "downloaded - press Use to answer with it")
    except Exception as e:
        _note(pg, rid, "download failed: %s" % e, False)
    finally:
        _pulls.pop(name, None)
        import bridge
        bridge.publish("settings_reload", {"page": pg})


SIDES = [("left", "Left"), ("center", "Center"), ("right", "Right")]


def display_value():
    import bridge
    where = tavinor.setting("notch_screen", "primary")
    name = ("Follows the mouse" if where == "mouse" else "Main screen" if where == "primary"
            else next((d["label"] for d in bridge.DISPLAYS if d["id"] == where), "Main screen"))
    return "%s · %s" % (name, dict(SIDES).get(tavinor.setting("notch_side", "center"), "Center"))


def page_display():
    """Which screen the notch lives on, and where along its top edge.
    The screens come from Electron, which reports them as it starts."""
    import bridge
    screens = [{"value": "mouse", "label": "Follow the mouse",
                "desc": "Moves to whichever screen the pointer is on, while closed"},
               {"value": "primary", "label": "Main screen", "desc": "The one Windows calls main"}]
    screens += [{"value": d["id"], "label": d["label"], "desc": d["size"]} for d in bridge.DISPLAYS]
    return {"title": "Display", "sections": [
        {"title": "Screen", "items": [
            {"type": "radio", "id": "notch_screen", "value": tavinor.setting("notch_screen", "primary"),
             "options": screens}]},
        {"title": "Position", "items": [
            {"type": "radio", "id": "notch_side", "value": tavinor.setting("notch_side", "center"),
             "options": [{"value": k, "label": v} for k, v in SIDES]}]},
    ], "footer": "Moves straight away"}


def page_widget():
    """The Windows widget: lock screen and widget board."""
    import widget
    place = tavinor.setting("widget_place") or {}
    where = ", ".join(x for x in (place.get("name"), place.get("region"), place.get("country")) if x)
    try:
        status = "Last updated %s" % json.loads(widget.FILE.read_text(encoding="utf-8"))["updated"]
    except (OSError, ValueError, KeyError):
        status = "Not written yet"
    return {"title": "Widget", "sections": [
        {"title": "Weather", "items": [
            {"type": "text", "id": "widget_place", "label": "City", "value": place.get("name", ""),
             "button": "Save", "placeholder": "e.g. Noida",
             "desc": where or "Weather from Open-Meteo: only this city's location is sent"},
            {"type": "radio", "id": "widget_units", "value": tavinor.setting("widget_units", "c"),
             "options": [{"value": "c", "label": "Celsius, km/h"}, {"value": "f", "label": "Fahrenheit, mph"}]},
        ]},
        {"title": "Show", "items": [T("widget_show:" + k, label, "", widget.shown(k))
                                    for k, label, _ in widget.SHOW]},
        {"title": "Privacy", "items": [
            T("widget_private", "Hide names on the widget",
              "Shows “Meeting at 15:00” and “3 tasks open” instead of titles. "
              "The lock screen is visible to anyone at your desk.", widget.private()),
        ]},
        {"title": "Add it", "items": [
            I("Lock screen: Windows Settings > Personalization > Lock screen > Widgets", dim=False),
            I("Widget board: Win+W, then + and pick Tavinor", dim=False),
            I("Needs the Tavinor package from the Microsoft Store or the .msix download. "
              "The Setup.exe version has no widget."),
        ]},
    ], "footer": status}


def page_general():
    """How Tavinor opens and a few odds and ends; the commands it runs have
    their own page."""
    on = sum(tavinor.feature_on(k) for k, _, _ in tavinor.FEATURES)
    return {"title": "General", "sections": [
        {"title": "Opening", "items": [
            T("hover_slow", "Hover needs a longer rest", "Fewer accidental opens when the pointer passes",
              tavinor.setting("hover_slow", False)),
        ]},
        {"title": "Shortcuts", "items": [
            I("Ctrl+Alt+Space  ·  open the bar", dim=False),
            I("Ctrl+Alt+V  ·  push to talk", dim=False),
            I("Ctrl+Alt+Esc  ·  panic key: stops typing and clicking, sets access to Off", dim=False),
        ]},
        {"items": [
            N("commands", "Commands", "%d of %d on" % (on, len(tavinor.FEATURES)), "sliders"),
        ]},
        {"title": "Chat", "items": [
            T("claude", "Claude Code button", "Hands the chat to Claude Code - only when you press it",
              tavinor.setting("claude", False)),
        ]},
    ]}


# What each command does, with real phrases (README "What it handles").
COMMAND_GROUPS = [
    ("System", [
        ("apps", "“open whatsapp”, “open downloads”, “open github.com”"),
        ("media", "“volume 40”, “quieter”, “pause”, “skip this one”"),
        ("brightness", "“brightness 70”, “dim the screen” - built-in screen only"),
        ("lock", "“lock my laptop”"),
        ("screenshot", "“take a screenshot” - saved to Pictures\\Screenshots"),
        ("theme", "“dark mode”, “turn off dark mode”"),
    ]),
    ("Everyday", [
        ("timers", "“timer 10 min” - rings when it ends"),
        ("reminders", "“remind me in 20 minutes to…”, “what’s left”, “mark 2 done”"),
        ("weather", "“weather”, “weather in goa”"),
        ("search", "“google best laptops” - in your browser"),
        ("music", "“play sapphire” - plays the top YouTube result"),
        ("files", "“clean up my downloads” - shows a plan; nothing moves until you approve"),
    ]),
    ("Productivity", [
        ("focus", "Focus sessions, and blocking your distracting sites during them"),
        ("tasks", "Tasks, subtasks and daily habits"),
        ("notes", "Save notes and find them by meaning"),
        ("calendar", "Today’s meetings, reminders before them, “plan my day”"),
    ]),
]


def page_commands():
    labels = {k: label for k, label, _ in tavinor.FEATURES}
    return {"title": "Commands", "back": "general", "sections": [
        {"title": group, "items": [T("feature:" + k, labels[k], desc, tavinor.feature_on(k))
                                   for k, desc in rows]}
        for group, rows in COMMAND_GROUPS
    ], "footer": "Off: Tavinor won’t run that kind of command. Everything else keeps working."}


def page_personality():
    import soul
    soul.ensure()
    s = soul.load()
    return {"title": "Personality", "sections": [
        {"items": [
            {"type": "text", "id": "soul_name", "label": "Name", "value": s["name"],
             "desc": "also the wake word: \"Hey %s\"" % s["name"]},
            {"type": "select", "id": "soul_preset", "label": "Start from",
             "value": "", "options": [{"value": "", "label": "a preset…"}] +
             [{"value": k, "label": k} for k in soul.PRESETS]},
            {"type": "text", "id": "soul_personality", "label": "How it talks", "multiline": True,
             "value": s["personality"]},
            {"type": "text", "id": "soul_about", "label": "About you", "multiline": True,
             "value": s["about"], "placeholder": "name, work, hours, habits"},
            {"type": "text", "id": "soul_rules", "label": "Rules", "multiline": True,
             "value": s["rules"]},
        ]},
        {"title": "Memory", "items": [
            {"type": "list", "id": "memory", "empty": "nothing remembered yet - say "
             "\"remember that …\"", "remove": "forget",
             "items": [{"key": str(i), "text": m} for i, m in enumerate(s["memory"])]},
        ]},
    ], "footer": "Text saves when you leave the box"}


def page_sites():
    import focus
    s = focus.load()
    return {"title": "Focus & blocked sites", "sections": [
        {"title": "During focus", "items": [
            {"type": "list", "id": "sites", "empty": "nothing", "remove": "remove",
             "items": [{"key": x, "text": x} for x in s["sites"]],
             "add": "a site, e.g. reddit or instagram.com"},
        ]},
        {"title": "Schedules", "items": [
            {"type": "list", "id": "blocks", "empty": "none", "remove": "remove",
             "items": [{"key": str(i), "text": b["site"], "sub": "%s–%s %s" % (
                 b["start"], b["end"], focus._day_words(b["days"]))}
                 for i, b in enumerate(s["blocks"])],
             "add": "site and time, e.g. youtube 9-12 weekdays"},
        ]},
    ]}


def page_voice():
    import soul
    import voice
    return {"title": "Voice", "sections": [
        {"items": [
            I("Push to talk: Ctrl+Alt+V, or the mic in the pill", dim=False),
            T("wake", "Wake word: \"Hey %s\"" % soul.name(),
              "Off by default. Listens on this laptop only and sends nothing. "
              "About 2 s of CPU per phrase heard while on", tavinor.setting("wake", False)),
            T("speak", "Read answers aloud", "Answers and chat replies; code is left out",
              tavinor.setting("speak", False)),
            T("stt", "Use Whisper for speech to text",
              "Accurate, ~2 s. Off: Windows' own, instant but it got 4 of 6 test commands wrong",
              voice.engine() == "whisper"),
            I("The microphone follows Screen access: at Off nothing listens"),
            B("mic_test", "Test microphone"),
        ]},
    ]}


def page_access():
    import access
    return {"title": "Permissions", "sections": [
        {"items": [
            {"type": "radio", "id": "level", "value": access.level(), "options": [
                {"value": "off", "label": "Off", "desc": "No reading, typing, clicking or running."},
                {"value": "ask", "label": "Ask", "desc": "Reads the window in front when you ask. "
                 "Asks before every typing, clicking or command."},
                {"value": "full", "label": "Full", "desc": "Reads and acts on its own for safe actions."}]},
        ]},
        {"title": "Always asks, even on Full", "items": [
            I("Running commands · Enter (it sends messages) · Delete · closing windows or tabs · "
              "typing a line break · buttons like Send, Delete, Pay, Buy, Install, Submit, "
              "Sign in, Discard, Close", dim=False),
        ]},
        {"items": [
            T("access_keep", "Keep Full after a restart", "Off: a restart drops Full back to Ask",
              tavinor.setting("access_keep", False)),
            T("screenshots", "Screenshots to the vision model",
              "When reading text isn't enough, a screenshot of the window in front goes to "
              "your provider's vision model", tavinor.setting("screenshots", True)),
            I("Panic key Ctrl+Alt+Esc drops to Off and stops what it is doing. The eyes turn "
              "orange whenever it reads or acts."),
            B("log", "Action log"),
        ]},
    ]}


def page_activity():
    import activity
    try:
        days = activity._rows("SELECT COUNT(DISTINCT substr(minute,1,10)), "
                              "SUM(active) FROM activity")[0]
    except Exception:
        days = (0, 0)
    return {"title": "Activity", "sections": [
        {"items": [
            T("activity", "Track activity",
              "Which app and site is in front, minute by minute, and active vs idle. No "
              "keystrokes, screenshots or page contents. Stays on this laptop; deleted after "
              "%d days." % activity.KEEP_DAYS, activity.on()),
            T("checkin", "Morning check-in", "Yesterday in one toast, the first time after 8 AM",
              tavinor.setting("checkin", True)),
            I("Recorded: %d day%s, %d h active" % (days[0] or 0, "" if days[0] == 1 else "s",
                                                  (days[1] or 0) // 3600), dim=False),
            I("Ask: what did I do today · when am I most productive · how long on youtube this week"),
            B("wipe", "Delete everything", confirm="Delete all recorded activity? This can't be undone."),
        ]},
    ]}


GCAL_STEPS = [
    ("gcal_step1", "1. Turn on the Calendar API",
     "https://console.cloud.google.com/apis/library/calendar-json.googleapis.com",
     "Create a project if Google asks (any name), then press Enable."),
    ("gcal_step2", "2. Name the app",
     "https://console.cloud.google.com/apis/credentials/consent",
     "App name Tavinor, your email, Audience: External. Then Publish app - "
     "while it says Testing, Google signs you out every 7 days."),
    ("gcal_step3", "3. Make the app keys",
     "https://console.cloud.google.com/apis/credentials/oauthclient",
     "Application type: Desktop app, name Tavinor, Create. Copy the two values below."),
]


def page_calendar():
    """Before setup: the steps and the two boxes. After: just sign in."""
    import gcal
    cid, secret = gcal.client()
    tail = T("meeting_reminders", "Meeting reminders",
             "A toast %d minutes before each meeting" % gcal.REMIND_BEFORE,
             tavinor.setting("meeting_reminders", True))
    if cid and secret:
        on = gcal.signed_in()
        return {"title": "Calendar", "sections": [
            {"title": "Google Calendar · " + ("connected" if on else "not connected"), "items": [
                B("gcal_signout", "Sign out") if on else B("gcal_signin", "Sign in with Google"),
                tail]},
            {"items": [B("gcal_reset", "Replace app keys",
                         confirm="Removes the app keys and signs you out.")]},
        ], "footer": "Only calendar events are asked for. The sign-in stays in "
                     "Windows Credential Manager."}
    return {"title": "Calendar", "sections": [
        {"title": "Set up once · about 5 minutes", "items": [
            I("Google wants every app registered. Do it once in your own Google "
              "account; after this, the page is just Sign in with Google.")] +
            [B(rid, label, note=note) for rid, label, _, note in GCAL_STEPS]},
        {"title": "4. Paste the keys", "items": [
            {"type": "text", "id": "gcal_id", "label": "Client ID", "value": cid},
            {"type": "text", "id": "gcal_secret", "label": "Client secret", "secret": True,
             "value": "", "button": "Save", "desc": "kept in Windows Credential Manager"},
        ]},
        {"items": [tail]},
    ]}


def page_notes():
    import notes
    rows = notes.load()
    used = sorted({k for r in rows for k in r["vec"]})
    return {"title": "Notes", "sections": [
        {"items": [
            T("embed", "Only on this laptop",
              "On: Ollama's nomic-embed-text, nothing leaves the laptop. Off: your answer "
              "provider's embedding model first (note text is sent to it), Ollama if that fails",
              notes.source() == "ollama"),
            I("%d notes. Indexed with: %s" % (len(rows), ", ".join(used) or "nothing yet"), dim=False),
            I("Say: note: …  ·  find note about …  ·  notes"),
        ]},
    ]}


def page_features():
    return {"title": "Features", "sections": [
        {"items": [T("feature:" + key, label, "", tavinor.feature_on(key))
                   for key, label, _ in tavinor.FEATURES]},
        {"items": [I("A switched-off command replies that it is off")]},
    ]}


PAGES = {"main": page_main, "brain": page_brain, "general": page_general, "commands": page_commands, "widget": page_widget, "display": page_display,
         "personality": page_personality, "sites": page_sites,
         "voice": page_voice, "access": page_access, "activity": page_activity,
         "calendar": page_calendar, "notes": page_notes, "features": page_features}


def page(name):
    return PAGES.get(name, page_main)()


# ------------------------------------------------------------------ changes

SIMPLE = {"cloud_answers", "hover", "hover_slow", "claude", "wake", "speak",
          "access_keep", "screenshots", "activity", "checkin", "meeting_reminders"}


def act(bar, pg, rid, value):
    """Apply one change. Returns {"note": str, "ok": bool, "reload": bool,
    "show": text to show in a sheet, "options": [...]}."""
    import bridge
    if rid in SIMPLE:
        tavinor.set_setting(rid, bool(value))
        if rid in ("hover", "hover_slow"):
            bridge.publish("config", ui_config())
        return {"reload": rid in ("activity",)}
    if rid.startswith("feature:"):
        tavinor.set_setting(rid, bool(value))
        return {}
    if rid == "startup":
        tavinor.install_startup() if value else tavinor.remove_startup()
        return {}
    if rid == "stt":
        tavinor.set_setting("stt", "whisper" if value else "windows")
        return {}
    if rid == "embed":
        tavinor.set_setting("embed", "ollama" if value else "provider")
        return {"reload": True}
    if rid == "widget_place":
        import weather
        import widget
        if not str(value).strip():
            tavinor.set_setting("widget_place", None)
            widget.refresh()
            return {"reload": True}
        place = weather.find(str(value))
        if not place:
            return {"note": "couldn't find %s" % str(value)[:40], "ok": False}
        tavinor.set_setting("widget_place", place)
        widget.refresh()
        return {"reload": True}
    if rid == "widget_units":
        import widget
        if value not in ("c", "f"):
            raise ValueError("unknown units")
        tavinor.set_setting("widget_units", value)
        widget.refresh()
        return {}
    if rid.startswith("widget_show:") or rid == "widget_private":
        import widget
        if rid != "widget_private" and rid[12:] not in {k for k, _, _ in widget.SHOW}:
            raise ValueError("unknown widget item")
        tavinor.set_setting(rid, bool(value))
        widget.refresh()
        return {}
    if rid in ("notch_screen", "notch_side"):
        import bridge
        ok = ({"mouse", "primary"} | {d["id"] for d in bridge.DISPLAYS}) if rid == "notch_screen"             else dict(SIDES)
        if str(value) not in ok:
            raise ValueError("unknown choice")
        tavinor.set_setting(rid, str(value))
        bridge.publish("place", place())
        return {}
    if rid == "chat":
        bar.root.after(0, lambda: __import__("agent").open_window(bar))
        return {}

    # answers
    if rid == "provider":
        if value not in tavinor.PROVIDERS:
            raise ValueError("unknown provider")
        tavinor.set_setting("provider", value)
        return {"reload": True}
    if rid == "key":
        tavinor.key_set(tavinor.provider(), str(value).strip())
        return {"reload": True}
    if rid == "models":
        return {"options": tavinor.provider_models(tavinor.provider())}
    if rid == "model":
        p = tavinor.provider()
        tavinor.set_setting("answer_model" if p == "ollama" else "model:" + p, str(value).strip())
        return {}
    if rid == "lm_get":
        __import__("webbrowser").open("https://ollama.com/download")
        return {"note": "opened in your browser"}
    if rid.startswith("lm:"):
        name = rid[3:]
        if name not in {m[0] for m in LOCAL_MODELS} or value not in ("pull", "cancel", "use", "remove"):
            raise ValueError("unknown model action")
        if value == "pull":
            if name not in _pulls:
                if not tavinor.start_ollama():
                    return {"note": "Ollama isn't running and couldn't be started", "ok": False}
                threading.Thread(target=_pull, args=(pg, rid, name), daemon=True).start()
            return {"note": "starting download…", "reload": True}
        if value == "cancel":
            if name in _pulls:
                _pulls[name]["cancel"] = True
            return {"note": "stopping…"}
        if value == "use":
            tavinor.set_setting("provider", "ollama")
            tavinor.set_setting("answer_model", name)
            return {"reload": True}
        with _ollama("/api/delete", {"model": name}, method="DELETE"):
            pass
        return {"reload": True}
    if rid == "test":
        _later(pg, rid, _test_answers)
        return {"note": "asking…"}

    # personality
    if rid.startswith("soul_"):
        import soul
        s = soul.load()
        field = rid[5:]
        if field == "preset":
            if value in soul.PRESETS:
                s["personality"] = soul.PRESETS[value]
                soul.save(s)
            return {"reload": True}
        if field in ("name", "personality", "about", "rules"):
            s[field] = str(value).strip()[:4000] or (soul.DEFAULT_NAME if field == "name" else "")
            soul.save(s)
            return {"reload": field == "name", "note": "saved"}
    if rid == "memory":                            # value = index to forget
        import soul
        soul.forget(int(value))
        return {"reload": True}

    # blocked sites
    if rid == "sites":
        site = str(value.get("add") or value.get("remove") or "").strip().lower()
        action = "add" if value.get("add") else "remove"
        msg = tavinor.dispatch({"intent": "block", "action": action, "site": site})
        return {"reload": True, "note": msg}
    if rid == "blocks":
        import focus
        import re
        if value.get("add"):
            v = str(value["add"]).strip().lower()
            item = tavinor.quick("block " + re.sub(r"\s+(weekdays|weekends|daily)$", r" on \1", v))
            if not item:
                return {"note": "didn't understand - try: youtube 9-12 weekdays", "ok": False}
            return {"reload": True, "note": tavinor.dispatch(item)}
        i = int(value["remove"])
        with focus._LOCK:
            s = focus.load()
            if 0 <= i < len(s["blocks"]):
                s["blocks"].pop(i)
                focus.save(s)
        return {"reload": True, "note": "removed"}

    # voice
    if rid == "mic_test":
        import voice
        if not voice.ears_allowed():
            return {"note": "Screen access is Off - the mic is off", "ok": False}
        _later(pg, rid, lambda: ((lambda t: ("heard: " + t, True) if t else ("heard nothing", False))(
            voice.listen_once()[0])))
        return {"note": "listening… say something"}

    # access
    if rid == "level":
        import access
        access.set_level(str(value))
        return {}
    if rid == "log":
        import access
        rows = access.recent()
        return {"show": "\n".join("%s  %-4s %-7s %s%s -> %s" % (
            r[0][5:16].replace("T", " "), r[1], r[2], r[3][:30],
            " (approved)" if r[4] else "", r[5][:40]) for r in rows) or "nothing yet"}

    # activity
    if rid == "wipe":
        import activity
        activity.wipe()
        return {"reload": True, "note": "deleted - nothing recorded"}

    # calendar
    if rid == "gcal_id":
        import gcal
        gcal.set_client(str(value), gcal.client()[1])
        return {"note": "saved"}
    if rid == "gcal_secret":
        import gcal
        if not gcal.client()[0]:
            return {"note": "paste the Client ID first", "ok": False}
        gcal.set_client(gcal.client()[0], str(value).strip() or gcal.client()[1])
        return {"reload": True}
    if rid == "gcal_signin":
        import gcal
        _later(pg, rid, lambda: (gcal.sign_in(), True))
        return {"note": "a browser tab opened - finish there"}
    if rid.startswith("gcal_step"):
        url = next((u for r, _, u, _ in GCAL_STEPS if r == rid), None)
        if not url:
            raise ValueError("unknown step")
        __import__("webbrowser").open(url)
        return {"note": "opened in your browser"}
    if rid == "gcal_reset":
        import gcal
        gcal.sign_out()
        tavinor.key_set("google_client", "")
        return {"reload": True}
    if rid == "gcal_signout":
        import gcal
        gcal.sign_out()
        return {"reload": True, "note": "signed out"}
    raise ValueError("unknown setting %r" % rid)


def _test_answers():
    """Same two checks as the Tk Test button: it answers, and it can drive
    the chat's tools."""
    import agent
    t0 = time.time()
    try:
        reply = tavinor.answer("Reply with the single word OK.", timeout=30).splitlines()[0]
    except RuntimeError as e:
        return str(e), False
    if reply.startswith("("):                      # "(" = fell back
        return reply, False
    try:
        raw, note = tavinor.complete(agent.system_prompt(), [{
            "role": "user", "content": "set a timer for 2 minutes"}], 45)
        acts = agent.parse(raw)["actions"]
        tasks_ok = not note and any(a["intent"] == "timer" and a.get("arg") == "2" for a in acts)
    except RuntimeError:
        tasks_ok = False
    return ("answers ✓ · tasks %s  (%.1f s)" % (
        "✓" if tasks_ok else "✗ - pick another model for the chat", time.time() - t0), tasks_ok)


def place():
    return {"screen": tavinor.setting("notch_screen", "primary"),
            "side": tavinor.setting("notch_side", "center")}


def ui_config():
    return {"hover": bool(tavinor.setting("hover", True)),
            "hover_slow": bool(tavinor.setting("hover_slow", False))}
