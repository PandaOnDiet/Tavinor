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
from datetime import datetime, timedelta

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

    # Tasks are done in Cane's own chat (decision D5, 2026-10-16). Before,
    # every task opened the Claude CLI once it was switched on - Panda's API
    # key was never used for work. Now nothing is spawned, switch on or off;
    # Claude Code is only a button in the chat.
    cane.SETTINGS = pathlib.Path(tempfile.mkdtemp()) / "s.json"
    seen = []
    real = cane.subprocess.Popen
    cane.subprocess.Popen = lambda *a, **k: seen.append((a, k))
    try:
        for on in (False, True):
            cane.set_setting("claude", on)
            out = cane.dispatch({"intent": "escalate", "arg": 'x" & calc & "y'})
            assert out == "that's a job for the chat", out
    finally:
        cane.subprocess.Popen = real
    assert seen == [], "a task spawned a process: %r" % seen

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


def check_agent():
    """Phase 1: tasks run in Cane's own chat through dispatch()."""
    import agent
    agent.CHAT = pathlib.Path(tempfile.mkdtemp()) / "chat.json"
    n = 0

    # Replies: JSON inside chatter, plain prose, unknown tools dropped.
    p = agent.parse('Sure!\n```json\n{"say": "On it", "actions": [{"intent": '
                    '"timer", "arg": "5 min", "action": "Minutes"}, {"intent": '
                    '"rm_rf"}, "junk"]}\n```')
    assert p["say"] == "On it" and p["dropped"] == 2, p
    assert p["actions"] == [{"intent": "timer", "arg": "5", "action": "m"}], p
    p = agent.parse("Just words, no JSON")
    assert p == {"say": "Just words, no JSON", "actions": [], "remember": "",
                 "dropped": 0}, p
    p = agent.parse('{"say": "x", "actions": [{"intent": "escalate"}, '
                    '{"intent": "ask"}, {"intent": "copilot"}]}')
    assert p["actions"] == [], "the chat must never bounce to itself or out"
    assert agent.clean({"intent": "volume", "arg": "40%"})["arg"] == "40"
    n += 6

    # What the model sees: alternating roles, starting with the user.
    rows = [{"role": "assistant", "content": "a"}, {"role": "user", "content": "1"},
            {"role": "user", "content": "2"}, {"role": "assistant", "content": "b"}]
    assert agent.turns(rows) == [{"role": "user", "content": "1\n\n2"},
                                 {"role": "assistant", "content": "b"}]
    n += 1

    def scripted(*replies):
        calls = []

        def complete(system, turns, timeout, effort="low"):
            calls.append(turns)
            r = replies[min(len(calls), len(replies)) - 1]
            if isinstance(r, Exception):
                raise r
            return r, ""
        return complete, calls

    shown = []
    show = lambda kind, t: shown.append((kind, t))

    # Instant actions run without asking; the results go back to the model.
    agent.clear()
    complete, calls = scripted(
        '{"say": "Setting it", "actions": [{"intent": "timer", "arg": "5", '
        '"action": "m"}]}', '{"say": "Done - 5 min timer", "actions": []}')
    asked = []
    agent.converse("set a 5 min timer", show=show, complete=complete,
                   approve=lambda a: asked.append(a) or True)
    assert len(calls) == 2 and asked == [], (len(calls), asked)
    assert calls[1][-1]["content"].startswith("RESULTS:\nstart a timer for 5 min"
                                              " -> timer: 5 min"), calls[1][-1]
    assert ("say", "Done - 5 min timer") in shown
    # Written before the action ran, so it is never shown as fact.
    assert ("say", "Setting it") not in shown, shown
    n += 4

    # A risky action waits for approval; declined means it never runs.
    agent.clear()
    ran = []
    real_dispatch = cane.dispatch
    cane.dispatch = lambda a, ui=None: ran.append(a) or "locked"
    try:
        complete, calls = scripted('{"say": "Locking", "actions": [{"intent": '
                                   '"lock"}]}')
        agent.converse("lock it", show=show, complete=complete,
                       approve=lambda a: False)
        assert ran == [] and len(calls) == 1, ran
        agent.converse("lock it", show=show, complete=complete, approve=None)
        assert ran == [], "ran a risky action with no way to approve it"
        complete, calls = scripted('{"say": "Locking", "actions": [{"intent": '
                                   '"lock"}]}', '{"say": "Locked", "actions": []}')
        agent.converse("lock it", show=show, complete=complete,
                       approve=lambda a: True)
        # Approved by the click, and marked so - screen actions refuse
        # to run without that mark.
        assert ran == [{"intent": "lock", "approved": True}], ran
        n += 4

        # A model that never stops acting is cut off.
        complete, calls = scripted('{"say": "", "actions": [{"intent": "clock"}]}')
        agent.converse("loop", show=show, complete=complete)
        assert len(calls) == agent.MAX_STEPS, len(calls)
        n += 1
    finally:
        cane.dispatch = real_dispatch

    # A dead provider is reported, not raised.
    shown.clear()
    complete, _ = scripted(RuntimeError("NVIDIA rejected the key"))
    agent.converse("hi", show=show, complete=complete)
    assert shown == [("error", "Couldn't reach the model: NVIDIA rejected the key")]
    # ...and the unanswered message is not carried into the next one.
    assert all(r["content"] != "hi" for r in agent.load()), agent.load()
    n += 2

    # A memory is only proposed - never written by the chat itself.
    proposed = []
    complete, _ = scripted('{"say": "Noted", "actions": [], "remember": '
                           '"Prefers mornings for deep work"}')
    agent.converse("I work best in the mornings", show=show, complete=complete,
                   propose=proposed.append)
    assert proposed == ["Prefers mornings for deep work"], proposed
    n += 1

    # History survives a restart and a junk file reads as empty.
    assert agent.load()[-1]["say"] == "Noted"
    agent.CHAT.write_text("not json")
    assert agent.load() == []
    n += 2
    print("agent: %d checks passed" % n)


def check_hover():
    """Phase 2: top-edge trigger zone and its accident guards."""
    import hover
    z = hover.zone(1920, 560, 24)
    assert z == (1336, 1760), z
    cases = [((1400, 0), True), ((1400, 1), True), ((1400, 2), False),
             ((1800, 0), False),       # over a maximised window's close button
             ((100, 0), False), ((1400, -5), False)]   # monitor above
    for (x, y), want in cases:
        assert hover.in_zone(x, y, z) is want, (x, y)
    full = {"fullscreen": True, "browser": False}
    assert hover.delay_for(full) is None, "opened over a full-screen app"
    assert hover.delay_for({"fullscreen": False, "browser": True}) == hover.DELAY_BROWSER
    assert hover.delay_for({"fullscreen": False, "browser": False}) == hover.DELAY
    assert hover.near_bar(1500, 100, 1920, 560, 104, 24)
    assert not hover.near_bar(900, 400, 1920, 560, 104, 24)

    # Full screen vs maximised. Real rects from Panda's laptop (taskbar on
    # auto-hide, so the work area is the whole monitor): a maximised window
    # covered the monitor and hover never opened over it.
    import winsys
    mon = (0, 0, 1536, 960)
    full_cases = [((-7, -7, 1543, 967), True, "Chrome_WidgetWin_1", False),
                  ((0, 0, 1536, 960), False, "Chrome_WidgetWin_1", True),  # F11
                  ((0, 0, 1536, 960), False, "UnityWndClass", True),       # game
                  ((952, 24, 1512, 298), False, "TkTopLevel", False),
                  ((0, 0, 1536, 960), False, "WorkerW", False)]            # desktop
    for rect, zoomed, cls, want in full_cases:
        assert winsys.is_fullscreen(rect, mon, zoomed, cls) is want, (rect, cls)
    print("hover: %d checks passed" % (len(cases) + 6 + len(full_cases)))


def check_soul():
    """Phase 3: the personality file, its memory, and where it is used."""
    import soul
    soul.SOUL = pathlib.Path(tempfile.mkdtemp()) / "soul.md"
    cane.SETTINGS = pathlib.Path(tempfile.mkdtemp()) / "s.json"
    n = 0
    assert soul.name() == "Cane" and soul.load()["memory"] == []
    soul.ensure()
    assert soul.SOUL.exists()
    n += 2

    s = soul.load()
    s.update(name="Wren", about="Panda, QA lead.\n(hint line)", rules="- Be brief.")
    soul.save(s)
    assert soul.parse(soul.SOUL.read_text(encoding="utf-8")) == soul.load()
    assert soul.name() == "Wren"
    p = soul.prompt()
    assert "Panda, QA lead." in p and "hint line" not in p and "Be brief" in p, p
    n += 3

    assert soul.remember("Prefers mornings") and not soul.remember("prefers MORNINGS")
    assert soul.remember("Uses Brave")
    assert soul.load()["memory"] == ["Prefers mornings", "Uses Brave"]
    assert soul.forget(0) == "Prefers mornings" and soul.forget(9) is None
    assert "Uses Brave" in soul.prompt()
    n += 5

    # Hand-edited oddities: plain prose, a missing name, a blank file.
    assert soul.parse("Just be nice.")["personality"] == "Just be nice."
    assert soul.parse("## Identity\nCalm.\n")["name"] == "Cane"
    assert soul.parse("")["name"] == "Cane"
    n += 3

    # Off switch: nothing of the soul goes to the provider.
    cane.set_setting("soul", False)
    assert soul.prompt() == ""
    cane.set_setting("soul", True)
    n += 1

    # Answers carry it; the command router never does.
    seen = []
    real = cane.complete
    cane.complete = lambda system, turns, timeout, effort="low": (
        seen.append(system) or ("ok", ""))
    try:
        cane.answer("hi")
    finally:
        cane.complete = real
    assert "You are Wren" in seen[0] and "Uses Brave" in seen[0], seen[0]
    assert "Wren" not in cane.SYSTEM and "Uses Brave" not in cane.SYSTEM
    n += 2
    print("soul: %d checks passed" % n)


FOCUS_QUICK = [
    ("focus", ("focus", "start", "", "25")),
    ("focus mode", ("focus", "start", "", "25")),
    ("help me focus", ("focus", "start", "", "25")),
    ("focus on the tax report for 45 min", ("focus", "start", "the tax report", "45")),
    ("focus for 50 minutes", ("focus", "start", "", "50")),
    ("focus 30", ("focus", "start", "", "30")),
    ("focus for half an hour", ("focus", "start", "", "30")),
    ("start a pomodoro", ("focus", "start", "", "25")),
    ("focus on writing for 2 hours", ("focus", "start", "writing", "120")),
    ("stop focus", ("focus", "stop", None, None)),
    ("how long left", ("focus", "status", None, None)),
    ("focus history", ("focus", "history", None, None)),
    ("block reddit", ("block", "add", None, None)),
    ("unblock youtube", ("block", "remove", None, None)),
    ("what's blocked", ("block", "list", None, None)),
]


def check_focus():
    """Phase 4: sessions, title matching, schedules, and the close guard."""
    import focus
    import winsys
    focus.STATE = pathlib.Path(tempfile.mkdtemp()) / "focus.json"
    focus._allow.clear()
    n = 0

    for text, (intent, action, arg, minutes) in FOCUS_QUICK:
        hit = cane.quick(text)
        assert hit and (hit["intent"], hit["action"]) == (intent, action), (text, hit)
        if arg is not None:
            assert hit["arg"] == arg and hit["minutes"] == minutes, (text, hit)
        n += 1
    # These belong to other features and must stay theirs.
    for text, want in [("what's left", None), ("set a timer for 25 minutes", "timer"),
                       ("block", None)]:
        hit = cane.quick(text)
        assert (hit and hit["intent"]) == want, (text, hit)
        n += 1
    m = cane.quick("block youtube 9-12 on weekdays")
    assert (m["site"], m["from"], m["to"], m["days"]) == ("youtube", "9", "12",
                                                          "weekdays"), m
    n += 1

    # Titles: whole segments only.
    titles = [("Lofi beats - YouTube - Google Chrome", "youtube", True),
              ("(3) YouTube - Brave", "youtube", True),
              ("Song - YouTube Music - Google Chrome", "youtube", False),
              ("Notes about YouTube - Google Docs - Google Chrome", "youtube", False),
              ("Lofi - YouTube and 4 more pages - Work - Microsoft​ Edge",
               "youtube", True),
              ("(2) Home / X - Google Chrome", "x", True),
              ("Home / Twitter - Google Chrome", "x", True),
              ("SpaceX launches - Google Search - Google Chrome", "x", False),
              ("shroud - Twitch — Mozilla Firefox", "twitch", True),
              ("Twitch - Google Chrome", "twitch", True)]
    for title, site, want in titles:
        assert focus.matches(title, site) is want, (title, site)
        n += 1

    # A session: start, count down, finish on time.
    t0 = datetime(2026, 10, 16, 10, 0)
    focus.start("report", 25, now=t0)
    assert round(focus.current(now=t0)["left"]) == 25
    assert focus.blocked_now(now=t0) == {"youtube", "twitch", "x"}
    assert focus.tick(now=t0.replace(minute=24)) is None
    done = focus.tick(now=t0.replace(minute=25))
    assert done["state"] == "finished" and done["minutes"] == 25, done
    assert focus.current(now=t0.replace(minute=26)) is None
    assert focus.blocked_now(now=t0.replace(minute=26)) == set()
    n += 6

    # Stopped early; replaced by a new one; abandoned while Cane was off.
    focus.start("a", 25, now=t0)
    assert focus.stop(now=t0.replace(minute=10))["minutes"] == 10
    assert focus.stop(now=t0) is None
    focus.start("b", 25, now=t0)
    focus.start("c", 25, now=t0.replace(minute=5))
    assert focus.load()["history"][-1]["task"] == "b"
    focus.recover(now=t0.replace(hour=12))
    assert focus.load()["history"][-1]["state"] == "abandoned"
    for bad in (0, 481):
        try:
            focus.start("x", bad, now=t0)
            raise AssertionError("accepted %d minutes" % bad)
        except ValueError:
            pass
    n += 6

    # Schedules, including weekdays only and one that runs past midnight.
    focus.save(dict(focus.load(), blocks=[
        {"site": "youtube", "start": "09:00", "end": "12:00", "days": [0, 1, 2, 3, 4]},
        {"site": "reddit", "start": "23:00", "end": "01:00", "days": [4]}]))
    fri = datetime(2026, 10, 16, 10, 0)            # a Friday
    assert focus.scheduled(fri) == {"youtube"}
    assert focus.scheduled(fri.replace(hour=12)) == set()
    assert focus.scheduled(datetime(2026, 10, 17, 10, 0)) == set()      # Saturday
    assert focus.scheduled(fri.replace(hour=23, minute=30)) == {"reddit"}
    assert focus.scheduled(datetime(2026, 10, 17, 0, 30)) == {"reddit"}  # Sat early
    assert focus.scheduled(datetime(2026, 10, 18, 0, 30)) == set()      # Sun early
    n += 6

    # "Let me through" lifts one site for a while.
    focus.let_through("youtube", now=fri)
    assert "youtube" not in focus.blocked_now(now=fri.replace(minute=3))
    assert "youtube" in focus.blocked_now(now=fri.replace(minute=6))
    n += 2

    # Commands through dispatch, and schedules that make no sense.
    assert cane.dispatch({"intent": "block", "action": "add", "site": "reddit.com"}) \
        == "reddit is blocked during focus"
    assert "reddit" in focus.load()["sites"]
    out = cane.dispatch(cane.quick("block twitch 9pm-11pm on weekends"))
    assert out == "twitch blocked 21:00-23:00 on weekends", out
    for bad in ("block twitch 9-9", "block twitch 25-26"):
        out = cane.dispatch(cane.quick(bad))
        assert out.startswith("couldn't read that schedule"), (bad, out)
    assert cane.dispatch(cane.quick("unblock reddit")) == "reddit unblocked"
    assert cane.dispatch(cane.quick("unblock nothing")) == "nothing wasn't blocked"
    assert cane.dispatch(cane.quick("stop focus")) == "no focus session running"
    n += 7

    # The close guard: Ctrl+W only if the same browser tab is still in front.
    sent = []
    real_fg, real_key = winsys.foreground, winsys.u32.keybd_event
    winsys.u32.keybd_event = lambda *a: sent.append(a)
    try:
        seen = {"hwnd": 7, "browser": True, "title": "Lofi - YouTube - Google Chrome"}
        for now_fg in ({"hwnd": 8, "browser": True, "title": seen["title"]},
                       {"hwnd": 7, "browser": True, "title": "Docs - Google Chrome"},
                       {"hwnd": 7, "browser": False, "title": seen["title"]}):
            winsys.foreground = lambda f=now_fg: f
            assert not focus.close_tab(seen, "youtube") and not sent, now_fg
        winsys.foreground = lambda: seen
        assert focus.close_tab(seen, "youtube") and len(sent) == 4
    finally:
        winsys.foreground, winsys.u32.keybd_event = real_fg, real_key
    n += 4

    # A broken focus.json reads as defaults.
    focus.STATE.write_text("[]")
    assert focus.load()["sites"] == focus.DEFAULT_SITES and focus.current() is None
    n += 1
    print("focus: %d checks passed" % n)


def check_render():
    """Answer panel text. A Java answer showed with every line flush left."""
    class Txt:
        def __init__(self):
            self.parts = []

        def insert(self, _, s, tag=""):
            self.parts.append((s, tag))

    t = Txt()
    cane.fill_text(t, "Here:\n```\nclass A {\n    void f() {\n        x();\n"
                      "    }\n}\n```\n    indented block\nuse `ls` now")
    code = [s for s, tag in t.parts if tag == "code"]
    assert code[:5] == ["class A {\n", "    void f() {\n", "        x();\n",
                        "    }\n", "}\n"], code
    assert code[5] == "indented block\n", code
    assert ("ls", "code") in t.parts
    print("render: 3 checks passed")


def check_tasks():
    """Phase 5: due dates, subtasks, workspaces, habits."""
    import tasks
    import webbrowser
    from datetime import date
    tmp = pathlib.Path(tempfile.mkdtemp())
    cane.STORE, tasks.HABITS = tmp / "notes.json", tmp / "habits.json"
    cane.SETTINGS = tmp / "s.json"
    n = 0

    # Weekdays. NOW is Monday 14:00.
    for phrase, want in [("friday", "2026-09-11 09:00"),
                         ("friday at 3pm", "2026-09-11 15:00"),
                         ("monday", "2026-09-14 09:00"),         # today's 9am passed
                         ("monday at 5pm", "2026-09-07 17:00"),   # later today
                         ("on saturday at 10am", "2026-09-12 10:00"),
                         ("tomorrow at 9am", "2026-09-08 09:00")]:
        got = cane.parse_when(phrase, now=NOW).strftime("%Y-%m-%d %H:%M")
        assert got == want, (phrase, got, want)
        n += 1

    # An item saved before tasks existed keeps working and gains subtasks.
    cane.STORE.write_text(json.dumps([
        {"text": "send invoice", "due": None, "done": False, "fired": False,
         "created": "2026-09-01T10:00"},
        {"text": "call ravi", "due": None, "done": False, "fired": False,
         "created": "2026-09-01T11:00"},
        {"text": "bad extras", "apps": "notalist", "parent": 7, "created": "x"}]))
    assert [i["text"] for i in cane.open_items()] == ["send invoice", "call ravi",
                                                      "bad extras"]
    assert "apps" not in cane.load()[2] and "parent" not in cane.load()[2]
    out = cane.dispatch(cane.quick("subtask draft the email to send invoice"))
    assert out == "subtask of send invoice: draft the email", out
    cane.dispatch(cane.quick("subtask attach pdf under invoice"))
    rows = cane.open_items()
    assert [i["text"] for i in rows] == ["send invoice", "draft the email",
                                         "attach pdf", "call ravi", "bad extras"]
    assert rows[1]["parent"] == rows[0]["id"] == rows[2]["parent"]
    assert cane.mark_done("3")["text"] == "attach pdf", "numbering drifted"
    n += 5

    # Tasks with due dates; a date it can't read is said, not guessed.
    out = cane.dispatch(cane.quick("task: file taxes due friday at 5pm"))
    assert out.startswith("task: file taxes, due Fri"), out
    out = cane.dispatch(cane.quick("add task water plants by someday"))
    assert out.startswith("task saved (couldn't read"), out
    n += 2

    # Workspaces: set, then open through the normal launch path.
    out = cane.dispatch(cane.quick("workspace for file taxes: excel, chrome and "
                                   "https://incometax.gov.in"))
    assert out == "file taxes opens: chrome, excel, https://incometax.gov.in", out
    launched, browsed = [], []
    real_dispatch, real_open = tasks.cane.dispatch, webbrowser.open
    try:
        tasks.cane.dispatch = lambda it, ui=None: launched.append(it["arg"]) or (
            "opened: x" if it["arg"] == "excel" else "nothing called 'chrome'")
        webbrowser.open = browsed.append
        out = tasks.start("taxes")
    finally:
        tasks.cane.dispatch, webbrowser.open = real_dispatch, real_open
    assert launched == ["chrome", "excel"] and browsed == ["https://incometax.gov.in"]
    assert out == "opened 2 for file taxes (couldn't open chrome)", out
    assert cane.dispatch(cane.quick("start task call ravi")).startswith(
        "call ravi has no workspace yet")
    n += 4

    # Habits: add, check in once a day, streaks over scheduled days only.
    out = cane.dispatch(cane.quick("habit: drink water daily at 6pm"))
    assert out == "habit: drink water, daily, nudge at 18:00", out
    cane.dispatch(cane.quick("new habit stretch on weekdays"))
    out = cane.dispatch(cane.quick("did drink water"))
    assert out.startswith("drink water done - 1-day streak"), out
    assert "already done today" in cane.dispatch(cane.quick("done drink water"))
    h = {"every": "weekdays", "checkins": ["2026-10-15", "2026-10-16"]}  # Thu, Fri
    assert tasks.streak(h, today=date(2026, 10, 19)) == 2   # Mon: weekend skipped
    h = {"every": "daily", "checkins": ["2026-10-15", "2026-10-16"]}
    assert tasks.streak(h, today=date(2026, 10, 17)) == 2   # today not done yet
    assert tasks.streak(h, today=date(2026, 10, 18)) == 0   # a day was missed
    n += 7

    # Nudges: after the time, once a day, never once done or archived.
    tasks.save_habits([{"name": "read", "every": "daily", "at": "20:00",
                        "checkins": [], "archived": False, "nudged": "",
                        "created": ""}])
    eve = datetime(2026, 10, 16, 19, 59)
    assert tasks.nudges(eve) == []
    assert [h["name"] for h in tasks.nudges(eve.replace(hour=20, minute=1))] == ["read"]
    assert tasks.nudges(eve.replace(hour=21)) == [], "nudged twice in a day"
    assert cane.dispatch(cane.quick("drop habit read")) == "stopped read (history kept)"
    assert tasks.load_habits()[0]["archived"] is True
    n += 5

    # Words that must keep their old meaning.
    for text, want in [("open task manager", "launch"), ("task manager", None),
                       ("done 2", None), ("did i lock the door", None),
                       ("tasks", "recall"), ("what's left", None)]:
        hit = cane.quick(text)
        assert (hit and hit["intent"]) == want, (text, hit)
        n += 1
    print("tasks: %d checks passed" % n)


def check_activity():
    """Phase 6: per-minute recording, idle, insights, retention, off switch."""
    import activity
    import focus
    tmp = pathlib.Path(tempfile.mkdtemp())
    activity.DB, cane.SETTINGS = tmp / "cane.db", tmp / "s.json"
    focus.STATE = tmp / "f.json"
    n = 0
    chrome = lambda title: {"exe": "chrome.exe", "title": title, "browser": True}
    code = {"exe": "Code.exe", "title": "x.py - VS Code", "browser": False}
    t0 = datetime(2026, 10, 16, 9, 0)

    # Off by default: nothing answers, nothing is claimed.
    assert cane.dispatch(cane.quick("what did i do today")).startswith(
        "activity tracking is off")
    cane.set_setting("activity", True)
    n += 1

    # Two hours of VS Code at 9-11, YouTube at 15:00, idle time apart.
    for m in range(120):
        activity.record(code, 0, 60, now=t0 + timedelta(minutes=m))
    for m in range(30):
        activity.record(chrome("Lofi - YouTube - Google Chrome"), 0, 60,
                        now=t0.replace(hour=15) + timedelta(minutes=m))
    activity.record(code, 500, 60, now=t0.replace(hour=16))      # idle
    activity.record({"exe": "LockApp.exe", "title": "", "browser": False}, 0, 60,
                    now=t0.replace(hour=17))                      # locked: skipped
    rows = activity.day_summary(t0.date())
    assert rows == [("VS Code", 120), ("Youtube (Chrome)", 30)], rows
    assert activity.active_minutes(t0.date()) == 150
    assert activity.site_minutes("youtube", t0.date()) == 30
    assert activity.site_minutes("YouTube", t0.date()) == 30
    n += 4

    # Best hours ignore distracting sites; too little data says so.
    assert activity.best_hours(today=t0.date()) == (9, 120), activity.best_hours(
        today=t0.date())
    assert activity.best_hours(today=t0.date() + timedelta(days=40)) is None
    n += 2

    # Retention and wipe.
    activity.prune(now=t0 + timedelta(days=31))
    assert activity.day_summary(t0.date()) == []
    activity.record(code, 0, 60, now=t0)
    activity.wipe()
    assert activity.active_minutes(t0.date()) == 0
    n += 2

    for text, want in [("what did i do today", "today"),
                       ("screen time", "today"), ("when am i most productive", "productive"),
                       ("how long on youtube this week", "site"),
                       ("how much time have i spent on twitch today", "site")]:
        hit = cane.quick(text)
        assert hit and hit["intent"] == "activity" and hit["action"] == want, (text, hit)
        n += 1
    assert cane.quick("how long on youtube this week")["when"] == "this week"
    n += 1
    print("activity: %d checks passed" % n)


def check_access():
    """Phase 7: levels, the always-ask list, unforgeable approval, panic."""
    import access
    import agent
    tmp = pathlib.Path(tempfile.mkdtemp())
    cane.SETTINGS, access.DB = tmp / "s.json", tmp / "cane.db"
    access.stop.clear()
    n = 0
    ran = []
    real_do = access._do
    access._do = lambda act, arg, approved: ran.append((act, arg, approved)) or "ok"
    try:
        # Off: nothing at all.
        access.set_level("off")
        for act in ("read", "type", "run"):
            out = access.dispatch({"intent": "screen", "action": act, "arg": "x"})
            assert out.startswith("screen access is off"), out
        assert ran == []
        n += 3

        # Ask (default): reading is fine, hands need the click.
        cane.SETTINGS.write_text("{}")
        assert access.level() == "ask"
        assert access.dispatch({"intent": "screen", "action": "read"}) == "ok"
        out = access.dispatch({"intent": "screen", "action": "type", "arg": "hi"})
        assert out.startswith("not done - needs your approval"), out
        assert access.dispatch({"intent": "screen", "action": "type", "arg": "hi",
                                "approved": True}) == "ok"
        n += 4

        # Full: safe actions run; the always-ask list still asks.
        access.set_level("full")
        assert access.dispatch({"intent": "screen", "action": "type", "arg": "hi"}) == "ok"
        assert access.dispatch({"intent": "screen", "action": "keys",
                                "arg": "Control + S"}) == "ok"
        for a in [{"action": "run", "arg": "dir"},
                  {"action": "keys", "arg": "Enter"},
                  {"action": "keys", "arg": "alt + F4"},
                  {"action": "keys", "arg": "ctrl+w"},
                  {"action": "type", "arg": "hello\n"},
                  {"action": "click", "arg": "Send"},
                  {"action": "click", "arg": "Pay now"},
                  {"action": "click", "arg": "Delete account"},
                  {"action": "click", "arg": "Install"}]:
            a["intent"] = "screen"
            assert access.risky(a), a
            out = access.dispatch(dict(a))
            assert out.startswith("not done - needs your approval"), (a, out)
            n += 1
        assert not access.risky({"action": "click", "arg": "Bold"})
        # Whole words: "Sender details" is not "Send".
        assert not access.risky({"action": "click", "arg": "Sender details"})
        n += 2

        # The model can't approve itself: clean() drops the field.
        p = agent.parse('{"say": "", "actions": [{"intent": "screen", "action": '
                        '"run", "arg": "del *", "approved": true}]}')
        assert "approved" not in p["actions"][0], p
        assert not agent.instant(p["actions"][0])
        assert agent.instant({"intent": "screen", "action": "type", "arg": "x"})
        access.set_level("ask")
        assert not agent.instant({"intent": "screen", "action": "type", "arg": "x"})
        n += 4

        # Panic: off, and typing stops mid-word.
        class Bar:
            class root:
                after = staticmethod(lambda ms, f=None: None)
        access.panic(Bar)
        assert access.level() == "off" and access.stop.is_set()
        sent = []
        import winsys as ws
        real_send = ws.u32.SendInput
        ws.u32.SendInput = lambda *a: sent.append(a)
        try:
            assert access.type_text("hello") == 0, "typed after the panic key"
        finally:
            ws.u32.SendInput = real_send
        access.set_level("ask")
        assert not access.stop.is_set()
        n += 3

        # Restart: full drops to ask unless kept; off is never raised.
        access.set_level("full")
        access.on_start()
        assert access.level() == "ask"
        access.set_level("full")
        cane.set_setting("access_keep", True)
        access.on_start()
        assert access.level() == "full"
        access.set_level("off")
        access.on_start()
        assert access.level() == "off"
        n += 3

        # Every action is logged, refusals included.
        assert len(access.recent()) >= 10
        n += 1
    finally:
        access._do = real_do
        access.stop.clear()
    assert access.norm_keys("Shift + ctrl + S") == "ctrl+shift+s"

    # The click script must be valid PowerShell: an unescaped ' in the risky
    # list ("don't save") broke it, and every click failed silently.
    seen = []
    real_ps, real_target = access._ps, access.target
    access._ps, access.target = (lambda s, timeout=15: seen.append(s) or "NOTFOUND"), \
        (lambda: 1)
    try:
        access._do("click", "it's here", True)
    finally:
        access._ps, access.target = real_ps, real_target
    line = next(l for l in seen[0].splitlines() if l.startswith("$want="))
    assert "'it''s here'" in line and "don''?t" in line, line
    print("access: %d checks passed" % (n + 2))


def check_voice_face():
    """Phases 8 and 11: wake parsing, speech text, mic gating, face states."""
    import voice
    import access
    import face
    import tkinter as tk
    tmp = pathlib.Path(tempfile.mkdtemp())
    cane.SETTINGS = tmp / "s.json"
    n = 0
    # Wake word: real transcripts from testing, including near misses.
    for text, want in [("Hey Cane, open Chrome.", (True, "open chrome")),
                       ("Hey Kane what time is it", (True, "what time is it")),
                       ("Hi Cain", (True, "")),
                       ("Okay, cancel that.", (False, "")),        # woke it once
                       ("Hey can you help me", (False, "")),
                       ("hey candle shop", (False, "")),
                       ("A cane.", (False, "")),
                       ("I can open Croat", (False, "")),
                       ("", (False, ""))]:
        assert voice.parse_wake(text, "cane") == want, (text, voice.parse_wake(text, "cane"))
        n += 1
    assert voice.parse_wake("Hey Wren, lock it", "wren") == (True, "lock it")
    n += 1

    # What gets said aloud: no code, no fallback note.
    said = voice.clean_for_speech("(could not reach NVIDIA - answered by x)\nRun "
                                  "`ls` then:\n```\nrm -rf /\n```\ndone")
    assert said == "Run ls then: (code shown on screen) done", said
    n += 1

    # Ears follow Screen access; the engine follows its setting.
    access.set_level("off")
    assert not voice.ears_allowed()
    access.set_level("ask")
    assert voice.ears_allowed()
    cane.set_setting("stt", "windows")
    assert voice.engine() == "windows"
    n += 3

    # Push to talk at Off says so instead of listening.
    access.set_level("off")
    seen = []

    class Bar:
        class root:
            after = staticmethod(lambda ms, f=None: f and f())
        show = staticmethod(lambda: None)
        status = staticmethod(lambda m, keep=False: seen.append(m))
    voice.push_to_talk(Bar)
    assert seen and "microphone is off" in seen[0], seen
    access.set_level("ask")
    n += 1

    # Every face state draws, and done/error settle back.
    root = tk.Tk()
    root.withdraw()
    try:
        cv = tk.Canvas(root, width=100, height=100)
        f = face.Face(cv)
        for s in face.STATES:
            f.set(s)
            assert cv.find_withtag("face"), s
            n += 1
        f.draw("idle", blink=True)
        cane.set_setting("face", False)
        f.draw("idle")
        assert cv.itemcget(cv.find_withtag("face")[0], "text") == "›"
        n += 1
    finally:
        root.destroy()
    print("voice/face: %d checks passed" % n)


def check_notes():
    """Phase 9: notes, per-model vectors, and the fallback chain."""
    import notes
    tmp = pathlib.Path(tempfile.mkdtemp())
    cane.SETTINGS, notes.BOOK = tmp / "s.json", tmp / "book.json"
    n = 0
    real = (notes._provider, notes._ollama, notes._provider_model, cane.provider,
            cane.key_get)
    vec = lambda t: [1.0, 0.0] if "wifi" in t or "internet" in t else [0.0, 1.0]
    calls = []
    try:
        cane.provider = lambda: "nvidia"
        cane.key_get = lambda p: "k"
        notes._provider_model = lambda p: "emb-1"
        notes._provider = lambda p, m, texts, kind: calls.append("prov") or [
            vec(t) for t in texts]
        notes._ollama = lambda texts, kind: calls.append("olla") or [
            vec(t) for t in texts]
        notes.add("wifi password is on the fridge")
        notes.add("spare key under the pot")
        time.sleep(0.3)

        hits, how = notes.search("internet login")
        assert hits[0][1]["text"].startswith("wifi") and how == "", (hits, how)
        assert "nvidia:emb-1" in notes.load()[0]["vec"]
        n += 2

        # Provider down: Ollama answers, and says so; vectors kept apart.
        def down(*a):
            raise OSError("timed out")
        notes._provider = down
        hits, how = notes.search("internet login")
        assert hits[0][1]["text"].startswith("wifi") and "used ollama" in how, how
        assert set(notes.load()[0]["vec"]) == {"nvidia:emb-1", "ollama:nomic-embed-text"}
        n += 2

        # Both down: plain words, and it says so.
        notes._ollama = down
        hits, how = notes.search("where is the spare key")
        assert hits[0][1]["text"] == "spare key under the pot" and "words only" in how
        n += 2

        # "Only on this laptop" never calls the provider.
        notes._ollama = lambda texts, kind: [vec(t) for t in texts]
        calls.clear()
        notes._provider = lambda *a: calls.append("prov") or [[0, 0]]
        cane.set_setting("embed", "ollama")
        notes.search("internet")
        assert "prov" not in calls, calls
        n += 1
    finally:
        (notes._provider, notes._ollama, notes._provider_model, cane.provider,
         cane.key_get) = real

    for text, want in [("note: wifi is on the fridge", ("add", "wifi is on the fridge")),
                       ("jot down call ravi back", ("add", "call ravi back")),
                       ("find note about the wifi", ("search", "the wifi")),
                       ("what did i note about passwords", ("search", "passwords")),
                       ("notes", ("list", None)), ("delete note 2", ("delete", "2"))]:
        hit = cane.quick(text)
        assert hit and hit["intent"] == "note" and hit["action"] == want[0], (text, hit)
        if want[1]:
            assert hit["arg"] == want[1], (text, hit)
        n += 1
    # Numbers follow the newest-first list.
    assert notes.recent()[0]["text"] == "spare key under the pot"
    assert notes.delete(1)["text"] == "spare key under the pot"
    assert notes.delete(9) is None and len(notes.load()) == 1
    n += 3
    notes.BOOK.write_text("{bad json")
    assert notes.load() == []
    print("notes: %d checks passed" % (n + 1))


def check_gcal():
    """Phase 10: sign-in round trip, tokens, events, planning, approvals."""
    import gcal
    import random
    import threading as th
    import urllib.request as ur
    tmp = pathlib.Path(tempfile.mkdtemp())
    cane.SETTINGS = tmp / "s.json"
    store = {}
    real = (cane.key_get, cane.key_set, gcal._post_form, gcal._api)
    cane.key_get = lambda k: store.get(k)
    cane.key_set = lambda k, v: store.__setitem__(k, v) if v else store.pop(k, None)
    n = 0
    try:
        # The sign-in URL asks for offline access, events only, with PKCE.
        u = gcal.auth_url("cid", "http://127.0.0.1:5", "chal", "st")
        for part in ("access_type=offline", "code_challenge_method=S256",
                     "calendar.events", "state=st", "prompt=consent"):
            assert part in u, part
        n += 1

        # Round trip: a fake browser calls the local server - first with a
        # wrong state (ignored), then the right one.
        gcal.set_client("cid", "sec")
        posted = []
        gcal._post_form = lambda url, f, timeout=20: posted.append(f) or {
            "refresh_token": "R1", "access_token": "A1", "expires_in": 3600}

        def browser(url):
            q = dict(x.split("=", 1) for x in url.split("?", 1)[1].split("&"))
            redirect = ur.unquote(q["redirect_uri"])

            def hit():
                time.sleep(0.2)
                try:
                    ur.urlopen(redirect + "/?code=BAD&state=wrong", timeout=5)
                except Exception:
                    pass
                ur.urlopen(redirect + "/?code=C1&state=" + q["state"], timeout=5).read()
            th.Thread(target=hit, daemon=True).start()
        assert gcal.sign_in(timeout=10, open_browser=browser) == "signed in to Google Calendar"
        assert store["google_refresh"] == "R1" and posted[0]["code"] == "C1"
        assert posted[0]["code_verifier"] and gcal.signed_in()
        n += 3

        # Tokens: cached until expiry, then refreshed with the refresh token.
        assert gcal.access_token() == "A1"
        gcal._token["expires"] = 0
        gcal._post_form = lambda url, f, timeout=20: {"access_token": "A2",
                                                       "expires_in": 3600}
        assert gcal.access_token() == "A2"
        gcal.sign_out()
        try:
            gcal.access_token()
            raise AssertionError("token without sign-in")
        except RuntimeError:
            pass
        n += 3

        # Events: timed and all-day.
        e = gcal._parse({"id": "1", "summary": "Standup",
                         "start": {"dateTime": "2026-10-19T10:00:00+05:30"},
                         "end": {"dateTime": "2026-10-19T10:15:00+05:30"}})
        assert e["title"] == "Standup" and not e["all_day"] and \
            (e["end"] - e["start"]).seconds == 900
        assert gcal._parse({"start": {"date": "2026-10-20"},
                            "end": {"date": "2026-10-21"}})["all_day"]
        n += 2

        # Planning: never overlaps a meeting, hardest in the best hours,
        # what doesn't fit is listed.
        now = datetime(2026, 10, 19, 8, 50)
        busy = [(now.replace(hour=10), now.replace(hour=11)),
                (now.replace(hour=13), now.replace(hour=14, minute=30))]
        tasks = [{"text": "email", "minutes": 15, "hard": 1},
                 {"text": "tax report", "minutes": 90, "hard": 3},
                 {"text": "review", "minutes": 45, "hard": 2},
                 {"text": "huge", "minutes": 600, "hard": 2}]
        placed, left = gcal.plan(tasks, busy, now, best=15)
        for a, b, t in placed:
            for x, y in busy:
                assert b <= x or a >= y, ("overlap", t["text"], a, b)
        hard = next(p for p in placed if p[2]["text"] == "tax report")
        assert hard[0].hour == 15, hard
        assert [t["text"] for t in left] == ["huge"]
        assert all(a >= now for a, _, _ in placed)
        n += 4
        # Property check: random days never overlap either.
        rnd = random.Random(7)
        for _ in range(200):
            busy = []
            for _ in range(rnd.randint(0, 5)):
                h = rnd.randint(9, 17)
                s = now.replace(hour=h, minute=rnd.choice([0, 15, 30, 45]))
                busy.append((s, s + timedelta(minutes=rnd.choice([15, 30, 60, 90]))))
            ts = [{"text": str(i), "minutes": rnd.choice([15, 30, 45, 90]),
                   "hard": rnd.randint(1, 3)} for i in range(rnd.randint(0, 8))]
            placed, left = gcal.plan(ts, busy, now, best=rnd.choice([None, 9, 14]))
            spans = [(a, b) for a, b, _ in placed]
            for a, b in spans:
                assert all(b <= x or a >= y for x, y in busy)
            spans.sort()
            assert all(spans[i][1] <= spans[i + 1][0] for i in range(len(spans) - 1))
            assert len(placed) + len(left) == len(ts)
        n += 1

        # Approval: moves and deletes never run unasked.
        store["google_refresh"] = "R1"
        ev = {"id": "e1", "title": "Design review", "all_day": False,
              "start": datetime(2026, 10, 20, 15), "end": datetime(2026, 10, 20, 16)}
        did = []
        real_find, real_move, real_delete = gcal.find, gcal.move, gcal.delete
        gcal.find = lambda w, around=None: ev
        gcal.move = lambda e, s: did.append(("move", s)) or dict(e, start=s,
                                                                  end=s + timedelta(hours=1))
        gcal.delete = lambda e: did.append(("delete",))
        try:
            out = cane.dispatch(cane.quick("move design review to friday at 11am"))
            assert out.startswith("not done - needs your approval"), out
            out = cane.dispatch(cane.quick("cancel meeting design review"))
            assert out.startswith("not done") and did == [], (out, did)
            asked = []

            class UI:
                confirm = staticmethod(lambda t, lines, yes: asked.append(lines))
            assert cane.dispatch(cane.quick("cancel meeting design review"), UI) \
                == "waiting for your OK" and asked and did == []
            out = cane.dispatch(dict(cane.quick("cancel meeting design review"),
                                     approved=True))
            assert out == "deleted Design review" and did == [("delete",)]
        finally:
            gcal.find, gcal.move, gcal.delete = real_find, real_move, real_delete
        n += 4

        # Not connected: says so, plan still works without meetings.
        gcal.sign_out()
        assert cane.dispatch(cane.quick("my meetings")).startswith(
            "Google Calendar isn't connected")
        n += 1
    finally:
        cane.key_get, cane.key_set, gcal._post_form, gcal._api = real
    print("calendar: %d checks passed" % n)


def check_mcp():
    """Phase 12: MCP protocol, approval gate, token check."""
    import mcp_server
    import bridge
    import urllib.request as ur
    n = 0
    r = mcp_server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                           "params": {"protocolVersion": "2025-06-18"}})
    assert r["result"]["protocolVersion"] == "2025-06-18"
    assert r["result"]["capabilities"] == {"tools": {}}
    r = mcp_server.handle({"jsonrpc": "2.0", "id": 2, "method": "initialize",
                           "params": {"protocolVersion": "1999-01-01"}})
    assert r["result"]["protocolVersion"] == mcp_server.VERSIONS[0]
    assert mcp_server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    names = {t["name"] for t in mcp_server.handle(
        {"jsonrpc": "2.0", "id": 3, "method": "tools/list"})["result"]["tools"]}
    assert names == {"get_day", "search_notes", "add_task", "add_note",
                     "start_focus", "animate_face"}
    assert mcp_server.handle({"jsonrpc": "2.0", "id": 4, "method": "nope"})["error"]
    assert mcp_server.handle({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                              "params": {"name": "rm_rf"}})["error"]
    n += 6

    # No Cane running: tools say so instead of acting.
    tmp = pathlib.Path(tempfile.mkdtemp())
    mcp_server.BRIDGE = tmp / "missing.json"
    r = mcp_server.handle({"jsonrpc": "2.0", "id": 6, "method": "tools/call",
                           "params": {"name": "get_day", "arguments": {}}})
    assert r["result"]["isError"] and "isn't running" in r["result"]["content"][0]["text"]
    n += 1

    # Writes need approval; reads and the face don't.
    ran = []
    real_run = bridge.run
    bridge.run = lambda tool, args: ran.append(tool) or "ok"
    try:
        assert bridge.call("add_task", {"text": "x"}, lambda l: False) == (
            False, "declined by the user in Cane") and ran == []
        ok, text = bridge.call("add_task", {"text": "x"}, lambda l: True)
        assert ok and text.startswith("approved by the user in Cane") and ran == ["add_task"]
        assert bridge.call("get_day", {}, lambda l: False) == (True, "ok")
        faces = []
        assert bridge.call("animate_face", {"state": "done"}, lambda l: False,
                           face=faces.append)[0] and faces == ["done"]
        assert not bridge.call("delete_everything", {}, lambda l: True)[0]
        n += 5

        # Over HTTP: the right token works, a wrong one is refused.
        bridge.FILE = tmp / "bridge.json"
        srv = bridge.serve(lambda l: True)
        info = json.loads(bridge.FILE.read_text())
        try:
            for token, want in ((info["token"], 200), ("guess", 403)):
                req = ur.Request("http://127.0.0.1:%d/" % info["port"],
                                 json.dumps({"tool": "get_day"}).encode(),
                                 {"X-Cane-Token": token})
                try:
                    code = ur.urlopen(req, timeout=5).status
                except Exception as e:
                    code = getattr(e, "code", 0)
                assert code == want, (token, code)
                n += 1
            mcp_server.BRIDGE = bridge.FILE
            assert mcp_server.forward("get_day", {}) == (True, "ok")
            n += 1
        finally:
            srv.shutdown()
    finally:
        bridge.run = real_run
    print("mcp: %d checks passed" % n)


def check_focus_link():
    """Gap: a focus session on a real task can tick it off at the end."""
    import focus
    tmp = pathlib.Path(tempfile.mkdtemp())
    cane.STORE, focus.STATE = tmp / "n.json", tmp / "f.json"
    cane.SETTINGS = tmp / "s.json"
    cane.add_note("write the quarterly tax report")
    cane.add_note("buy bread")
    t0 = datetime(2026, 10, 19, 10, 0)
    s = focus.start("tax report", 25, now=t0)
    assert s["task"] == "write the quarterly tax report" and s["task_id"]
    h = focus.tick(now=t0 + timedelta(minutes=25))
    assert focus.task_of(h)["text"] == "write the quarterly tax report"
    assert focus.finish_task(h) == "write the quarterly tax report"
    assert [i["text"] for i in cane.open_items()] == ["buy bread"]
    assert focus.task_of(h) is None and focus.finish_task(h) is None
    # Too short to be a link: "b" must not grab "buy bread".
    s = focus.start("b", 5, now=t0)
    assert s["task"] == "b" and "task_id" not in s
    focus.stop(now=t0)
    print("focus link: 7 checks passed")


if __name__ == "__main__":
    check_focus_link()
    check_mcp()
    check_gcal()
    check_notes()
    check_voice_face()
    check_activity()
    check_access()
    check_tasks()
    check_render()
    check_agent()
    check_hover()
    check_soul()
    check_focus()
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
