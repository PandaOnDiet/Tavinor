"""Checks for Cane.

  python test_cane.py          logic only, no model needed
  python test_cane.py --model  also checks the model routes sentences correctly

CANE_MODEL picks the model (default qwen3:1.7b).
"""
import json
import os
import subprocess
import sys
import tempfile
import pathlib
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cane

NOW = datetime(2026, 9, 7, 14, 0)  # a Monday, 2pm

WHEN_CASES = [
    ("in 20 minutes", "2026-09-07 14:20"),
    ("in 2 hours", "2026-09-07 16:00"),
    ("in 3 days", "2026-09-10 14:00"),
    ("tomorrow at 9am", "2026-09-08 09:00"),
    ("tomorrow", "2026-09-08 09:00"),
    ("at 3pm", "2026-09-07 15:00"),
    ("at 15:30", "2026-09-07 15:30"),
    ("at 3", "2026-09-07 15:00"),      # afternoon, not 3am
    ("at 9am", "2026-09-08 09:00"),    # already passed -> tomorrow
    ("tonight", "2026-09-07 20:00"),
    ("sometime whenever", None),
]

INTENT_CASES = [
    ("pause", "media"),
    ("turn the volume down", "media"),
    ("skip this one", "media"),
    ("play sapphire", "search_play"),
    ("open vs code", "launch"),
    ("remember the auth discussion is waiting on Ravi", "remember"),
    ("remind me in 20 minutes to check the build", "remind"),
    ("what's left", "recall"),
    ("what am I tracking", "recall"),
    ("mark 2 done", "done"),
    ("move all my pdfs from downloads into the paisa folder", "files"),
    ("write me a python script that renames files by date", "escalate"),
    ("how do I undo a git commit", "ask"),
    ("why does my wifi keep dropping", "ask"),
    ("who wrote the hobbit", "ask"),
    ("what's 15% of 2400", "ask"),          # was "none": a dead end
    ("how much is 340 divided by 7", "ask"),
    ("convert 5 miles to km", "ask"),
    # Unseen phrasings - not in the prompt, so these test generalisation.
    ("how many ounces in a pound", "ask"),
    ("define entropy", "ask"),
    ("12 times 13", "ask"),
    # Non-commands. These must never trigger an action: "hi" once classified
    # as the nearest action and started playing music.
    ("hey", "none"),
    ("hi", "none"),
    ("hello", "none"),
    ("yo", "none"),
    ("thanks", "none"),
    ("ok", "none"),
    ("asdfgh", "none"),
    ("what can you do", "none"),
]


def check_logic():
    cane.STORE = pathlib.Path(tempfile.mkdtemp()) / "t.json"

    for phrase, want in WHEN_CASES:
        got = cane.parse_when(phrase, now=NOW)
        got_s = got.strftime("%Y-%m-%d %H:%M") if got else None
        assert got_s == want, (phrase, got_s, want)

    cane.add_note("auth discussion waiting on Ravi")
    cane.add_note("check the build", cane.parse_when("in 20 minutes", now=NOW))
    cane.add_note("already passed", datetime(2020, 1, 1, 9, 0))
    assert len(cane.open_items()) == 3

    assert [f["text"] for f in cane.due_now(now=NOW)] == ["already passed"]
    assert cane.due_now(now=NOW) == [], "a reminder fired twice"

    assert cane.mark_done("2")["text"] == "check the build"
    assert cane.mark_done("ravi")["text"] == "auth discussion waiting on Ravi"
    assert cane.mark_done("nonsense") is None
    assert len(cane.open_items()) == 1
    print(f"logic: {len(WHEN_CASES) + 7} checks passed")


def check_edges():
    """Malformed and hostile input. Every case here was a real defect."""
    cane.STORE = pathlib.Path(tempfile.mkdtemp()) / "e.json"

    # An empty ref used to substring-match every row and silently close the
    # first open item.
    cane.add_note("first important thing")
    cane.add_note("second thing")
    for ref in ["", "   ", None]:
        assert cane.mark_done(ref) is None, f"empty ref {ref!r} matched a row"
    assert len(cane.open_items()) == 2, "an empty ref closed something"

    # Out-of-range clock values used to raise straight out of dispatch.
    for bad in ["at 25pm", "at 99", "at 13:70", "in 999999999 days"]:
        assert cane.parse_when(bad, now=NOW) is None, bad
    assert cane.parse_when("at 3pm", now=NOW) is not None, "valid time broke"

    # A hand-edited or truncated store must not take the app down.
    for bad in ['{"not":"a list"}', "[1,2,3]", "not json at all", "[]"]:
        cane.STORE.write_text(bad)
        assert cane.open_items() == [], bad
    cane.STORE.write_text('[{"text":"sparse row"}]')
    assert [i["text"] for i in cane.open_items()] == ["sparse row"]

    # One unparseable date used to kill the whole sweep, so every other
    # reminder stopped firing.
    cane.STORE.write_text(
        '[{"text":"bad","due":"not-a-date","done":false,"fired":false},'
        '{"text":"good","due":"2020-01-01T09:00","done":false,"fired":false}]')
    assert [r["text"] for r in cane.due_now(now=NOW)] == ["good"]
    assert cane.due_now(now=NOW) == [], "re-fired after a bad row"

    # Escalation must not go through a shell. Via cmd.exe, a sentence
    # containing a quote and & was reparsed and the tail executed.
    cane.SETTINGS = pathlib.Path(tempfile.mkdtemp()) / "s.json"
    seen = []
    real = cane.subprocess.Popen
    cane.subprocess.Popen = lambda *a, **k: seen.append((a, k))
    try:
        # Claude Code is opt-in: off by default, nothing may be spawned.
        cane.dispatch({"intent": "escalate", "arg": "move my pdfs"})
        assert seen == [], "escalated while Claude Code was switched off"
        cane.set_setting("claude", True)
        cane.dispatch({"intent": "escalate", "arg": 'x" & calc & "y'})
    finally:
        cane.subprocess.Popen = real
    argv = seen[0][0][0]
    assert "cmd" not in [os.path.basename(str(x)).lower() for x in argv], argv
    assert argv[1] == 'x" & calc & "y', "argument was mangled"
    assert seen[0][1].get("creationflags"), "no CREATE_NEW_CONSOLE"

    # A garbage settings file must read as defaults, not crash.
    cane.SETTINGS.write_text("[not a dict]")
    assert cane.setting("claude", False) is False

    # Timers outside 1s..24h are refused, not scheduled.
    for bad in ("0", "999", ""):
        out = cane.dispatch({"intent": "timer", "arg": bad, "action": "h"})
        assert out.startswith("a timer has to be"), (bad, out)
    print("edges: 26 checks passed")


# text -> (intent, arg or action). Every row came from a real probe: 24
# everyday commands, of which Cane handled 5 before this tier existed.
QUICK_CASES = [
    ("open whatsapp", "launch", "whatsapp"),
    ("Open Settings", "launch", "settings"),
    ("launch the terminal", "launch", "the terminal"),
    ("volume 50", "volume", "50"),
    ("set the volume to 30%", "volume", "30"),
    ("volume at 80 percent", "volume", "80"),
    ("brightness 70", "brightness", "70"),
    ("set screen brightness to 40", "brightness", "40"),
    ("make it brighter", "brightness", "up"),
    ("dim the screen", "brightness", "down"),
    ("lower the brightness", "brightness", "down"),
    ("set a timer for 10 minutes", "timer", "10"),
    ("10 min timer", "timer", "10"),
    ("start a timer for 30 seconds", "timer", "30"),   # not "open a timer"
    ("lock my laptop", "lock", None),
    ("lock", "lock", None),
    ("take a screenshot", "screenshot", None),
    ("turn on dark mode", "theme", "dark"),            # pressed volume-down
    ("turn off dark mode", "theme", "light"),
    ("light theme please", "theme", "light"),
    ("google best laptops 2026", "google", "best laptops 2026"),
    ("search for flights to goa", "google", "flights to goa"),
    ("ask copilot why is the sky blue", "copilot", "why is the sky blue"),
    # Weather must never reach a language model: it has no live data. The
    # router sent this one to "none"; routed to "ask" it would be a guess.
    ("what's the weather like in delhi", "weather", "delhi"),
    ("weather", "weather", ""),
    ("what's the temperature", "weather", ""),
    ("what time is it", "clock", None),
    ("what's the date today", "clock", None),
    ("Can you open youtube?", "launch", "youtube"),
    ("pause", "media", "pause"),
    ("skip the song", "media", "next"),
    ("unmute", "media", "mute"),
    # Cane's own settings - but "open settings" is still Windows Settings.
    ("settings", "settings", None),
    ("cane settings", "settings", None),
    ("open cane settings", "settings", None),
    ("open settings", "launch", "settings"),
    # Level words. "Volume max" once reached the model and turned it DOWN.
    ("Volume max", "volume", "100"),
    ("max volume", "volume", "100"),
    ("set the volume to half", "volume", "50"),
    ("volume all the way up", "volume", "100"),
    ("volume min", "volume", "0"),
    ("full brightness", "brightness", "100"),
    ("brightness half", "brightness", "50"),
    # Track moves as sentences. "play next" reached the cloud as a question.
    ("play next", "media", "next"),
    ("play the next song", "media", "next"),
    ("skip this one", "media", "next"),
    ("play the previous track", "media", "prev"),
    ("go back a song", "media", "prev"),
    ("undo", "undo", None),
    ("undo that", "undo", None),
]

# Real typos. Before: dead ends, a Claude escalation, and "brigtness up"
# turned the VOLUME up.
TYPO_CASES = [
    ("brigtness 50", "brightness", "50"),
    ("set brigtness to 40", "brightness", "40"),
    ("brigtness up", "brightness", "up"),
    ("volum 30", "volume", "30"),
    ("set the volme to 20", "volume", "20"),
    ("take a scrensht", "screenshot", None),
    ("wether", "weather", ""),
    ("wheather in goa", "weather", "goa"),
    ("timr 5 min", "timer", "5"),
    ("set a timer for 5 minuts", "timer", "5"),
    ("settigns", "settings", None),
    ("pasue", "media", "pause"),
    ("gogle cheap flights", "google", "cheap flights"),
]

# Ordinary sentences one letter from a keyword. Correction must never turn
# these into actions - "clock" must not lock the screen.
TYPO_SAFE = ["whether to buy a mac", "the build timed out after 5 minutes",
             "what time is it in london", "research quantum computing",
             "the cause of high cpu", "clock", "lunch with ravi",
             "spend time on it for 10 minutes", "pilot training tips"]

# Sentences the quick tier must NOT claim - they belong to the model.
NOT_QUICK = ["play sapphire", "remind me at 5pm to call mom", "go back",
             "play the last of us", "what's the max volume on a mac",
             "what's left", "how do I undo a git commit", "hey",
             "turn the volume up", "what time does the store open",
             "remind me to check the weather at 5pm",
             "remember to open the parcel",
             "remind me to take a screenshot at 5",
             "remind me in 10 minutes to turn on dark mode"]


def check_quick():
    for text, want, val in QUICK_CASES:
        got = cane.quick(text)
        assert got and got["intent"] == want, (text, got)
        if val is not None:
            assert val in (got.get("arg"), got.get("action")), (text, got)
    for text in NOT_QUICK:
        assert cane.quick(text) is None, (text, cane.quick(text))
    for text, want, val in TYPO_CASES:
        assert cane.quick(text) is None, ("typo matched exactly?", text)
        got = cane.quick(cane.fix_typos(text))
        assert got and got["intent"] == want, (text, got)
        if val is not None:
            assert val in (got.get("arg"), got.get("action")), (text, got)
    for text in TYPO_SAFE:
        assert cane.quick(text) is None and cane.quick(cane.fix_typos(text)) is None, \
            (text, cane.fix_typos(text), cane.quick(cane.fix_typos(text)))

    # A model media intent without a media word is a misfire: "turn on
    # bluetooth" must never press a key.
    real = cane._ask
    cane._ask = lambda t, timeout: {"intent": "media", "action": "voldown"}
    try:
        assert cane.parse("enable bluetooth")["intent"] == "none"
        assert cane.parse("turn it down")["intent"] == "media"
    finally:
        cane._ask = real
    n = len(QUICK_CASES) + len(NOT_QUICK) + len(TYPO_CASES) + len(TYPO_SAFE) + 2
    print(f"quick: {n} checks passed")


class FakeText:
    """Stands in for tk.Text so the answer formatting can be tested headless."""

    def __init__(self):
        self.parts = []

    def insert(self, where, text, tag=""):
        self.parts.append((text, tag))


class FakeUI:
    def __init__(self):
        self.msg = self.keep = None
        self.thread = []

    def status(self, msg, keep=False):
        self.msg, self.keep = msg, keep

    def show_answer(self, q, t):
        pass

    def timer_done(self, label):
        pass

    def remember_turn(self, q, a):
        self.thread.append((q, a))


def check_suggest():
    cane._APPS.clear()
    cane._APPS.update({"whatsapp": ("WhatsApp", "id1"),
                       "whatsapp business": ("WhatsApp Business", "id2"),
                       "notepad": ("Notepad", "id3")})
    # Apps first, web/model fallbacks last - the order every launcher uses.
    rows = cane.suggest("whats")
    assert [r[0] for r in rows[:2]] == ["open WhatsApp",
                                        "open WhatsApp Business"], rows
    assert [r[2] and r[2]["intent"] for r in rows[2:]] == [None, "ask", "google"], rows
    assert rows[0][2] == {"intent": "launch", "arg": "WhatsApp"}
    assert cane.suggest("open note")[0][0] == "open Notepad"
    # A typo shows the corrected action rather than a dead end.
    assert cane.suggest("brigtness 50")[0][0] == "set brightness to 50%"
    assert cane.suggest("volum 30")[0][0] == "set volume to 30%"
    assert cane.suggest("timr 5 min")[0][0] == "start a timer for 5 min"
    # Anything unmatched: the top row is Cane's normal routing (None = parse
    # on Enter), never "ask" - that sent "play next" to the cloud as a question.
    for text in ("how do i undo a commit", "turn the volume up",
                 "put on some jazz"):
        rows = cane.suggest(text)
        assert [r[2] and r[2]["intent"] for r in rows] == [None, "ask", "google"], \
            (text, rows)
    # Commands that used to fall through now show what they will do.
    assert cane.suggest("Volume max")[0][0] == "set volume to 100%"
    assert cane.suggest("play next")[0][0] == "next track"
    assert cane.suggest("") == [] and cane.suggest("   ") == []
    assert len(cane.suggest("a" * 40)) <= 5
    # No raw intent name may reach the list - every one says what it does.
    for text, _, _ in QUICK_CASES + TYPO_CASES:
        it = cane.quick(text) or cane.quick(cane.fix_typos(text))
        said = cane.describe(it)
        assert said and said != it["intent"], (text, it, said)

    # The bar stays open for commands that leave the screen alone.
    ui = FakeUI()
    for intent, want in [({"intent": "clock"}, True),
                         ({"intent": "timer", "arg": "1", "action": "s"}, True),
                         ({"intent": "none"}, False),
                         ({"intent": "launch", "arg": "no-such-app-here"}, False)]:
        cane.run("", ui, item=intent)
        assert ui.keep is want, (intent, ui.keep, ui.msg)

    t = FakeText()
    cane.fill_text(t, "Use `git reset` now\n```\ngit reset --hard\n```\n# Heading")
    assert ("git reset", "code") in t.parts, t.parts
    assert ("git reset --hard\n", "code") in t.parts, t.parts
    assert ("Heading\n", "head") in t.parts, t.parts
    assert not any("`" in p[0] for p in t.parts), "backticks left in the panel"
    print("suggest: %d checks passed" % (14 + len(QUICK_CASES) + len(TYPO_CASES)))


def check_providers():
    cane.SETTINGS = pathlib.Path(tempfile.mkdtemp()) / "p.json"
    real = (cane.key_get, cane._ask_ollama, cane._ask_openai_style)
    try:
        # Keys round-trip through the real Credential Manager, under a
        # throwaway name that is deleted again.
        cane.key_set("_selftest", "sk-test-é-1234")
        assert cane.key_get("_selftest") == "sk-test-é-1234"
        cane.key_set("_selftest", "")
        assert cane.key_get("_selftest") is None, "empty save must delete"
        assert "sk-test" not in cane.SETTINGS.read_text() if \
            cane.SETTINGS.exists() else True, "key leaked into settings.json"

        cane._ask_ollama = lambda s, t, to: "Use `git reset --soft HEAD~1`."
        # Ollama: backticks survive to the panel, which styles them as code.
        assert "`git reset" in cane.answer("undo?"), "backticks were stripped"

        # A keyed provider that fails falls back to ollama and says why.
        cane.set_setting("provider", "openai")
        cane.key_get = lambda p: "sk-fake"
        def rejected(*a):
            raise RuntimeError("OpenAI rejected the key")
        cane._ask_openai_style = rejected
        out = cane.answer("undo?")
        assert out.startswith("(OpenAI rejected the key - answered by "), out
        assert "`git reset" in out, out

        # No key saved: same fallback, with the reason.
        cane.key_get = lambda p: None
        assert cane.answer("undo?").startswith("(no OpenAI key saved"), \
            cane.answer("undo?")

        # The model shown and sent follows the provider.
        assert cane.current_model("anthropic") == "claude-opus-5"
        assert cane.current_model("openai") == "", "no guessed OpenAI model"
        cane.set_setting("model:openai", "some-gpt")
        assert cane.current_model() == "some-gpt"
        cane.set_setting("provider", "nonsense")
        assert cane.provider() == "ollama", "unknown provider must not break"
    finally:
        cane.key_get, cane._ask_ollama, cane._ask_openai_style = real
        cane.key_set("_selftest", "")
    print("providers: 11 checks passed")


def check_settings():
    cane.SETTINGS = pathlib.Path(tempfile.mkdtemp()) / "s.json"
    real_open, real_answer = cane.os.startfile, cane.answer
    opened = []
    cane.os.startfile = lambda p: opened.append(p)
    try:
        # Every feature defaults on; switching one off blocks exactly it.
        assert all(cane.feature_on(k) for k, _, _ in cane.FEATURES)
        cane.set_setting("feature:weather", False)
        assert cane.dispatch({"intent": "weather"}) == \
            "Weather is switched off in settings"
        assert cane.dispatch({"intent": "clock"}) != \
            "Weather is switched off in settings", "blocked the wrong feature"
        cane.set_setting("feature:reminders", False)
        assert "switched off" in cane.dispatch({"intent": "remember", "arg": "x"})
        assert "switched off" in cane.dispatch({"intent": "recall"})

        # Cloud answers off: the question must not reach the cloud model.
        def no_cloud(q):
            raise AssertionError("question was sent to the cloud")
        cane.answer = no_cloud
        cane.set_setting("cloud_answers", False)
        out = cane.dispatch({"intent": "ask", "arg": "why is the sky blue"})
        assert out.startswith("Copilot opened"), out
        assert opened == ["ms-copilot:"], opened

        # The picked answer model is the one actually requested.
        cane.answer = real_answer
        cane.set_setting("answer_model", "some-model:cloud")
        sent = []

        class R:
            def __enter__(s): return s
            def __exit__(s, *a): pass
            def read(s): return b'{"message":{"content":"ok"}}'
        real_urlopen = cane.urllib.request.urlopen
        cane.urllib.request.urlopen = lambda req, timeout=0: (sent.append(req.data), R())[1]
        try:
            assert cane.answer("q") == "ok"
        finally:
            cane.urllib.request.urlopen = real_urlopen
        assert b'"some-model:cloud"' in sent[0], sent

        # A follow-up carries the previous turn, so "undo that" resolves.
        cane.urllib.request.urlopen = lambda req, timeout=0: (sent.append(req.data), R())[1]
        try:
            cane.answer("and undo that?", [("how do i commit", "use git commit")])
        finally:
            cane.urllib.request.urlopen = real_urlopen
        assert b"use git commit" in sent[1], sent[1]
        assert sent[1].index(b"how do i commit") < sent[1].index(b"undo that"), \
            "history must come before the new question"
    finally:
        cane.os.startfile, cane.answer = real_open, real_answer
    print("settings: 9 checks passed")


def check_resolve():
    saved = dict(cane._APPS)
    cane._APPS.clear()
    cane._APPS.update({
        "whatsapp": ("WhatsApp", "wa!App"),
        "visual studio code": ("Visual Studio Code", "VSCode"),
        "file explorer": ("File Explorer", "Explorer"),
        "settings": ("Settings", "Elevoc Smart Notification"),  # the impostor
        "windows powershell": ("Windows PowerShell", "PS"),
        "brave": ("Brave", "Brave"),
    })
    try:
        cases = [
            ("whatsapp", "shell:AppsFolder\\wa!App"),
            ("whatsap", "shell:AppsFolder\\wa!App"),         # typo
            ("vs code", "shell:AppsFolder\\VSCode"),         # alias
            ("the explorer", "shell:AppsFolder\\Explorer"),  # alias + article
            ("powershell", "shell:AppsFolder\\PS"),          # whole word
            ("settings", "ms-settings:"),                    # not the impostor
            ("bluetooth settings", "ms-settings:bluetooth"),
            ("downloads", "shell:Downloads"),
            ("my documents folder", "shell:Personal"),
            ("youtube", "https://www.youtube.com"),
            ("github.com/anthropics", "https://github.com/anthropics"),
            ("browser", "https://www.google.com"),
        ]
        for name, want in cases:
            got = cane.resolve(name)
            assert got and got[1] == want, (name, got, want)
        for name in ("zzqx", "b", "cane folder"):
            assert cane.resolve(name) is None, (name, cane.resolve(name))
    finally:
        cane._APPS.clear()
        cane._APPS.update(saved)
    print(f"resolve: {len(cases) + 3} checks passed")


def check_model():
    print(f"\nmodel: {cane.MODEL}")
    fails, times = [], []
    for text, want in INTENT_CASES:
        t0 = time.time()
        got = cane.parse(text)
        dt = time.time() - t0
        times.append(dt)
        ok = got.get("intent") == want
        if not ok:
            fails.append((text, want, got))
        print(f"{'ok  ' if ok else 'FAIL'} {dt:5.2f}s  {text!r:56} -> {got}")
    print(f"\nmedian {sorted(times)[len(times) // 2]:.2f}s   max {max(times):.2f}s")
    assert not fails, f"{len(fails)} wrong: {fails}"
    print(f"model: {len(INTENT_CASES)} checks passed")


# Held out: none of these phrasings is an example in SYSTEM, so passing means
# the model understood, not that it recognised a copy. Before the prompt
# listed brightness/theme/lock/weather, 1.7B got 39/47 and the cloud model
# 40/47 - both missed the same ones, because neither had been told they exist.
ROUTE_CASES = [
    ("Volume max", {"volume"}, None),
    ("play next", {"media"}, "next"),
    ("turn it all the way up", {"volume", "media"}, None),
    ("it's too loud", {"volume", "media"}, None),
    ("i can't hear anything", {"volume", "media"}, None),
    ("it's too bright", {"brightness"}, None),
    ("make the screen darker", {"brightness"}, None),
    ("switch to dark mode", {"theme"}, None),
    ("skip this song", {"media"}, "next"),
    ("go back one song", {"media"}, "prev"),
    ("put some lofi on", {"search_play"}, None),
    ("thanks cane", {"none"}, None),
    ("how far is the moon", {"ask"}, None),
    ("remind me to call mom at 6", {"remind"}, None),
    ("open spotify", {"launch"}, None),
    ("lock it", {"lock"}, None),
    ("what's the weather like", {"weather"}, None),
    ("my downloads folder is messy can you fix it", {"files"}, None),
    ("group my screenshots on the desktop by month", {"files"}, None),
    ("can you revert what you just moved", {"undo"}, None),
]


def check_route():
    """The model alone, patterns bypassed: check_model goes through parse(),
    which answers most sentences from the fixed patterns before the model
    ever sees them, so it cannot tell whether the model understands."""
    print(f"\nroute (model only): {cane.MODEL}")
    fails = []
    for text, want, action in [(t, {w}, None) for t, w in INTENT_CASES] + ROUTE_CASES:
        got = cane._ask(text, 60)
        if got.get("intent") not in want or (action and got.get("action") != action):
            fails.append((text, want, got))
    assert not fails, f"{len(fails)} misrouted: {fails}"
    print(f"route: {len(INTENT_CASES) + len(ROUTE_CASES)} checks passed")


def check_model_output():
    """What the model writes is loose. Canned replies, no model needed."""
    real = cane.urllib.request.urlopen

    def reply(payload):
        class R:
            def __enter__(s): return s
            def __exit__(s, *a): pass
            def read(s):
                return json.dumps({"message": {"content": json.dumps(payload)}}).encode()
        return lambda req, timeout=0: R()

    cases = [
        ("go dark", {"intent": "theme", "arg": "Dark"}, {"intent": "theme", "arg": "dark"}),
        ("crank it", {"intent": "volume", "arg": "100%"}, {"intent": "volume", "arg": "100"}),
        ("sound max", {"intent": "volume", "arg": "max"}, {"intent": "volume", "arg": "100"}),
        ("turn the volume down", {"intent": "volume", "arg": ""},
         {"intent": "media", "action": "voldown"}),
        ("it's too loud", {"intent": "volume"}, {"intent": "media", "action": "voldown"}),
        ("screen too dim", {"intent": "brightness"}, {"intent": "brightness", "action": "up"}),
        ("count down 30 seconds", {"intent": "timer", "arg": "30", "action": "seconds"},
         {"intent": "timer", "arg": "30", "action": "s"}),
        ("volume thing", {"intent": "volume", "arg": ""}, {"intent": "none"}),
    ]
    try:
        for text, raw, want in cases:
            cane.urllib.request.urlopen = reply(raw)
            got = cane._ask(text, 5)
            assert got == want, (text, raw, got)
        # Side-effect actions need the user's own words behind them.
        for text, raw in [("unlock my phone", {"intent": "lock"}),
                          ("what's the plan for today", {"intent": "theme", "arg": "dark"}),
                          ("tell me a joke", {"intent": "screenshot"})]:
            cane.urllib.request.urlopen = reply(raw)
            # What matters is that the action never runs; a question is
            # passed on as one ("what's the plan..." -> ask), the rest -> none.
            assert cane.parse(text)["intent"] in ("none", "ask"), (text, cane.parse(text))
        # A question never triggers a side effect, whatever the model says.
        for text, raw in [("how do I undo a git commit", {"intent": "undo"}),
                          ("how bright should my screen be",
                           {"intent": "brightness", "action": "up"}),
                          ("should I lock my screen at night", {"intent": "lock"})]:
            cane.urllib.request.urlopen = reply(raw)
            assert cane.parse(text) == {"intent": "ask", "arg": text}, \
                (text, cane.parse(text))
        # A request phrased politely is still a request.
        cane.urllib.request.urlopen = reply({"intent": "lock"})
        assert cane.parse("can you lock it for me")["intent"] == "lock"
    finally:
        cane.urllib.request.urlopen = real
    print("model output: 15 checks passed")


def check_files():
    """File tasks against throwaway fixture files in a temp folder. The
    "model" reply is canned and deliberately hostile."""
    base = pathlib.Path(tempfile.mkdtemp())
    roots = {n: base / n for n in ("downloads", "documents", "desktop")}
    for r in roots.values():
        r.mkdir()
    (roots["documents"] / "paisa").mkdir()
    for name in ("a.pdf", "b.pdf", "invoice.pdf", "setup.exe", "pic.png",
                 "movie.mkv.crdownload", "secret.txt"):
        (roots["downloads"] / name).write_text("fixture " + name)
    (roots["documents"] / "paisa" / "invoice.pdf").write_text("already here")
    cane.ctypes.windll.kernel32.SetFileAttributesW(
        str(roots["downloads"] / "secret.txt"), 0x2)             # hidden
    real_known, real_undo, real_complete = cane.known_folders, cane.UNDO, cane.complete
    cane.known_folders = lambda: roots
    cane.UNDO = base / "undo.json"

    def everything():
        return sorted(str(p.relative_to(base)) for p in base.rglob("*")
                      if p.is_file() and p.name != "undo.json")
    try:
        files = cane.scan(roots["downloads"])
        names = {f["name"] for f in files}
        assert "movie.mkv.crdownload" not in names, "moved a download in progress"
        assert "secret.txt" not in names, "hidden file offered to the planner"
        assert names == {"a.pdf", "b.pdf", "invoice.pdf", "setup.exe", "pic.png"}

        # Where things may go.
        assert cane.resolve_dest("documents\\paisa", roots) == roots["documents"] / "paisa"
        for bad in ("../../Windows", "C:/Windows", "Documents/paisa/../../x",
                    "Downloads/CON", "Downloads/a/b/c/d", "Downloads/.ssh",
                    "Downloads/bad:name", "Nowhere/x", ""):
            assert cane.resolve_dest(bad, roots) is None, bad

        # A hostile plan: escapes, an unknown file, a double claim, a collision.
        raw = {"summary": "tidy", "groups": [
            {"label": "PDFs", "to": "Documents/paisa",
             "files": ["a.pdf", "b.pdf", "invoice.pdf"]},        # invoice collides
            {"label": "x", "to": "../../Windows", "files": ["setup.exe"]},
            {"label": "x", "to": "C:/Windows", "files": ["pic.png"]},
            {"label": "Images", "to": "Downloads/Images",
             "files": ["pic.png", "ghost.png", "a.pdf"]},        # ghost, a.pdf taken
        ]}
        plan = cane.build_plan("clean up", "downloads", files, roots, raw)
        got = {g["where"]: sorted(s.name for s, _ in g["moves"]) for g in plan["groups"]}
        assert got == {"Documents\\paisa": ["a.pdf", "b.pdf"],
                       "Downloads\\Images": ["pic.png"]}, got
        assert plan["dropped"] == 5, plan["dropped"]

        # Planned, then b.pdf is edited before "Do it": it must be skipped.
        before = everything()
        (roots["downloads"] / "b.pdf").write_text("edited after the plan")
        moved, skipped = cane.execute_plan(plan)
        assert sorted(pathlib.Path(s).name for s, _ in moved) == ["a.pdf", "pic.png"]
        assert [n for n, _ in skipped] == ["b.pdf"], skipped
        assert (roots["downloads"] / "Images" / "pic.png").exists()
        assert len(everything()) == len(before), "a file disappeared"
        assert cane.UNDO.exists()

        # Undo puts everything back and removes the folder it created.
        assert cane.undo_last() == (2, [])
        assert everything() == before, everything()
        assert not (roots["downloads"] / "Images").exists(), "left an empty folder"
        assert not cane.UNDO.exists() and cane.undo_last() == (0, [])
        assert cane.dispatch({"intent": "undo"}) == "nothing to undo"

        # A junction inside a user folder that points outside is refused.
        outside = pathlib.Path(tempfile.mkdtemp())
        link = roots["documents"] / "jump"
        made = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)],
                              capture_output=True).returncode == 0
        if made:
            assert cane.resolve_dest("Documents/jump", roots) is None, "junction escape"
            assert cane.resolve_dest("Documents/jump/deeper", roots) is None
            os.rmdir(link)                                 # removes the link only

        # The planner's JSON is found inside chatter and code fences.
        cane.complete = lambda *a, **k: ('Sure!\n```json\n{"summary": "s", '
                                         '"groups": []}\n```', "")
        raw, note = cane.ask_plan("tidy", "downloads", files, roots)
        assert raw == {"summary": "s", "groups": []}
        cane.complete = lambda *a, **k: ("I can't do that.", "")
        assert "couldn't plan" in cane.dispatch({"intent": "files", "arg": "tidy"})

        # Which folder a request is about.
        for text, want in [("clean up my downloads", "downloads"),
                           ("move my pdfs into paisa", "downloads"),
                           ("move pdfs from desktop into documents", "desktop"),
                           ("put these into documents", "downloads"),
                           ("sort my photos", "pictures")]:
            assert cane.source_folder(text) == want, (text, cane.source_folder(text))
    finally:
        cane.known_folders, cane.UNDO, cane.complete = real_known, real_undo, real_complete
    print("files: 34 checks passed%s" % ("" if made else " (junction check skipped)"))


if __name__ == "__main__":
    check_logic()
    check_edges()
    check_quick()
    check_resolve()
    check_suggest()
    check_providers()
    check_settings()
    check_model_output()
    check_files()
    if "--model" in sys.argv:
        check_model()
        check_route()
