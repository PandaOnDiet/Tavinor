"""Voice (phase 8): talk to Tavinor, have it talk back, and an opt-in
"Hey <name>" wake word.

Built on Windows' own speech engine (System.Speech, en-US) driven through
PowerShell - nothing installed. Speech becomes the same text the bar takes,
so every command works by voice.

  push to talk   Ctrl+Alt+V, or the mic on the bar
  wake word      off by default; "hey" + the personality name, so renaming
                 the assistant renames the wake word - nothing to retrain
  read aloud     off by default; answers and chat replies, code left out

The microphone is "ears" under Screen access: at Off, nothing listens.
While listening for the wake word, audio is matched on this laptop and
nothing is sent anywhere; only what you say after it becomes a command.
"""
import os
import re
import tempfile
import subprocess
import threading
import time

import cane

CONFIDENCE = 0.75          # wake word: below this it is ignored
CULTURE = "en-US"
WHISPER_MODEL = "small"    # cached in ~/.cache/huggingface

LISTEN_PS = r"""
Add-Type -AssemblyName System.Speech
[Console]::OutputEncoding=[Text.Encoding]::UTF8
$r=New-Object System.Speech.Recognition.SpeechRecognitionEngine([Globalization.CultureInfo]$env:CANE_CULTURE)
$r.LoadGrammar((New-Object System.Speech.Recognition.DictationGrammar))
$wav=$env:CANE_WAV
if($wav){ $r.SetInputToWaveFile($wav) } else { $r.SetInputToDefaultAudioDevice() }
$r.InitialSilenceTimeout=[TimeSpan]::FromSeconds(6)
$r.EndSilenceTimeout=[TimeSpan]::FromSeconds(1)
# A rejected utterance still carries its audio - keep it for Whisper.
$global:rej=$null
$r.add_SpeechRecognitionRejected({ param($s,$e) $global:rej=$e.Result })
$res=$r.Recognize([TimeSpan]::FromSeconds(15))
$heard=$res; if(-not $heard){ $heard=$global:rej }
if($heard -and $heard.Audio -and $env:CANE_OUT){
  $fs=[IO.File]::Create($env:CANE_OUT); $heard.Audio.WriteToWaveStream($fs); $fs.Close() }
if($res){ "TEXT:{0}|{1}" -f $res.Text,$res.Confidence } elseif($heard){ "AUDIO" } else { "NONE" }
"""

# The wake phrase competes with free dictation: with only one grammar loaded
# the engine forces every sound onto it, and anything said would "wake".
WAKE_PS = r"""
Add-Type -AssemblyName System.Speech
[Console]::OutputEncoding=[Text.Encoding]::UTF8
$r=New-Object System.Speech.Recognition.SpeechRecognitionEngine([Globalization.CultureInfo]$env:CANE_CULTURE)
$gb=New-Object System.Speech.Recognition.GrammarBuilder($env:CANE_PHRASE)
$gb.AppendWildcard()      # "hey cane open chrome" - words may follow
$g=New-Object System.Speech.Recognition.Grammar($gb); $g.Name='wake'
$r.LoadGrammar($g)
$d=New-Object System.Speech.Recognition.DictationGrammar; $d.Name='other'
$r.LoadGrammar($d)
$wav=$env:CANE_WAV
if($wav){ $r.SetInputToWaveFile($wav) } else { $r.SetInputToDefaultAudioDevice() }
"READY"
$n=0; $k=0
while($true){
  # From a file, an empty result or an error means the file is used up.
  try { $res=$r.Recognize([TimeSpan]::FromSeconds(4)) } catch { "END"; break }
  if($wav -and -not $res){ $n++; if($n -ge 2){ "END"; break } }
  if($res -and $res.Grammar.Name -eq 'wake'){ "WAKE:{0}" -f $res.Confidence }
  # "hey cane open chrome" lands in dictation, misheard beyond any word
  # filter ("I can open Croat", "Peking what time is it"), so every heard
  # phrase goes to Whisper with its audio. Costs ~2 s of CPU per phrase,
  # only while the wake word is switched on; it all stays on the laptop.
  elseif($res -and $res.Audio){
    $k++; $f=Join-Path $env:CANE_OUT ("cand{0}.wav" -f $k)
    $fs=[IO.File]::Create($f); $res.Audio.WriteToWaveStream($fs); $fs.Close()
    "CAND:$f" }
  [Console]::Out.Flush()
}
"""

SPEAK_PS = r"""
Add-Type -AssemblyName System.Speech
$s=New-Object System.Speech.Synthesis.SpeechSynthesizer
if($env:CANE_WAV){ $s.SetOutputToWaveFile($env:CANE_WAV) }
$s.Speak($env:CANE_SAY)
"""


def _ps(script, **params):
    """argv and env for a PowerShell script. Values travel in environment
    variables, never formatted into the script: no quoting to get wrong, and
    no -EncodedCommand - Windows refused to start the encoded speech scripts
    at all ("Access is denied"), while the same script in plain form ran."""
    env = dict(os.environ, CANE_CULTURE=CULTURE)
    env.update({"CANE_" + k.upper(): str(v) for k, v in params.items()})
    return ["powershell", "-NoProfile", "-Command", script], env


def ears_allowed():
    import access
    return access.level() != "off"


def wake_phrase():
    import soul
    return "hey " + soul.name().lower()


def listen_once(wav=""):
    """(text, confidence) for one utterance, or ("", 0.0).

    Windows' engine finds where speech starts and stops and keeps that
    audio; Whisper (faster-whisper, already installed, model cached) turns
    it into text. Measured on clean synthetic speech, Windows' own
    dictation got 2 of 4 commands right ("open whatsapp" -> "Open one
    separate"). Without Whisper, Windows' text is used.
    """
    clip = os.path.join(tempfile.gettempdir(), "cane_heard.wav")
    if os.path.exists(clip):
        os.remove(clip)
    argv, env = _ps(LISTEN_PS, wav=wav, out=clip)
    r = subprocess.run(argv, env=env, capture_output=True, timeout=40,
                       creationflags=subprocess.CREATE_NO_WINDOW)
    out = r.stdout.decode("utf-8", "ignore").strip().splitlines()
    line = next((l for l in out if l.startswith("TEXT:")), "")
    text, conf = "", 0.0
    if line:
        text, _, c = line[5:].rpartition("|")
        try:
            conf = float(c.replace(",", "."))
        except ValueError:
            conf = 0.0
    if os.path.exists(clip) and engine() == "whisper":
        better = transcribe(clip)
        if better:
            return better, 1.0
    return text.strip(), conf


_model = [None]


def engine():
    """"whisper" when faster-whisper and its model are on this laptop."""
    if cane.setting("stt", "whisper") == "windows":
        return "windows"
    try:
        import faster_whisper  # noqa: F401
        return "whisper"
    except ImportError:
        return "windows"


def transcribe(path):
    """Text from a WAV with Whisper, or "" if it can't. local_files_only: the
    model is already cached, and nothing should download behind your back."""
    try:
        if _model[0] is None:
            from faster_whisper import WhisperModel
            _model[0] = WhisperModel(WHISPER_MODEL, device="cpu",
                                     compute_type="int8", local_files_only=True)
        segs, _ = _model[0].transcribe(path, language="en", beam_size=1,
                                       vad_filter=True)
        return " ".join(s.text.strip() for s in segs).strip()
    except Exception:
        return ""


# ------------------------------------------------------------- speaking

_speaker = [None]


def clean_for_speech(text):
    """Code isn't read out: fenced blocks and `inline code` become a word."""
    text = re.sub(r"```.*?```", " (code shown on screen) ", text, flags=re.S)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"^\(.*? - answered by .*?\)\s*", "", text)   # fallback note
    return " ".join(text.split())[:1200]


def speak(text, wav=""):
    """Say it (or write it to a WAV for tests). Stops anything still playing."""
    stop_speaking()
    text = clean_for_speech(text)
    if not text:
        return None
    argv, env = _ps(SPEAK_PS, wav=wav, say=text)
    p = subprocess.Popen(argv, env=env, creationflags=subprocess.CREATE_NO_WINDOW)
    _speaker[0] = p
    return p


def stop_speaking():
    p = _speaker[0]
    if p and p.poll() is None:
        p.kill()
    _speaker[0] = None


def maybe_speak(text):
    if cane.setting("speak", False):
        speak(text)


# ------------------------------------------------------------- push to talk

_busy = threading.Lock()


def push_to_talk(bar):
    """Listen once and run what was heard as if typed into the bar."""
    if not ears_allowed():
        bar.root.after(0, lambda: (bar.show(), bar.status(
            "the microphone is off - Screen access is set to Off", keep=True)))
        return
    if not _busy.acquire(blocking=False):
        return                               # already listening
    stop_speaking()

    def go():
        try:
            bar.root.after(0, lambda: (bar.show(), bar.label.config(
                text="listening… speak now"), bar.set_face("listening")))
            text, conf = listen_once()
            if not text:
                bar.status("didn't catch that", keep=True)
                bar.set_face("idle")
                return

            def submit():
                bar.entry.delete(0, "end")
                bar.entry.insert(0, text)
                bar.set_face("thinking")
                bar.submit(None)
            bar.root.after(0, submit)
        finally:
            _busy.release()
    threading.Thread(target=go, daemon=True).start()


# ------------------------------------------------------------- wake word

# Real words a misheard name must not be: "hey can you..." is not "hey cane".
COMMON = {"can", "came", "cake", "case", "cave", "care", "cain't", "cant",
          "kind", "cone", "come", "keen", "game", "gain", "there", "then",
          "the", "they", "you", "guys", "man", "what", "how", "why", "so",
          "tavern"}


def parse_wake(text, name):
    """(woke, rest) for a transcript: "Hey Tavinor, open Chrome." -> (True,
    "open chrome"). The greeting must come first and the name second, said
    as the name or something close to it - never a common word."""
    import difflib
    words = re.findall(r"[a-z0-9']+", text.lower())
    name = name.lower()
    if len(words) < 2 or words[0] not in ("hey", "hi", "okay", "ok"):
        return False, ""
    # Same length give or take one letter: Kane and Cain are the name,
    # "okay cancel that" is not (it woke it in testing). Longer names can
    # arrive split ("tavi nor"), so 1 to 3 words are tried joined.
    for k in (1, 2, 3):
        w = "".join(words[1:1 + k])
        if k > 1 and len(words) < 1 + k:
            break
        if w == name or (w not in COMMON and abs(len(w) - len(name)) <= 1 and
                         difflib.SequenceMatcher(None, w, name).ratio() >= 0.75):
            return True, " ".join(words[1 + k:])
    return False, ""


def handle(line, name):
    """One line from the wake listener -> (woke, rest) or None."""
    if line.startswith("WAKE:"):
        try:
            conf = float(line[5:].replace(",", "."))
        except ValueError:
            conf = 0.0
        return (True, "") if conf >= CONFIDENCE else None
    if line.startswith("CAND:"):
        path = line[5:].strip()
        text = transcribe(path) if engine() == "whisper" else ""
        try:
            os.remove(path)
        except OSError:
            pass
        woke, rest = parse_wake(text, name)
        return (True, rest) if woke else None
    return None


def run_text(bar, text):
    """Run words as if typed into the bar."""
    def submit():
        bar.show()
        bar.entry.delete(0, "end")
        bar.entry.insert(0, text)
        bar.set_face("thinking")
        bar.submit(None)
    bar.root.after(0, submit)


class Wake:
    """Keeps a wake-word listener running while it's switched on, ears are
    allowed and the phrase is current; restarts it when any of that changes."""

    def __init__(self, bar):
        self.bar, self.proc, self.phrase = bar, None, ""
        self.dir = tempfile.mkdtemp(prefix="cane_wake_")

    def wanted(self):
        return bool(cane.setting("wake", False)) and ears_allowed()

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.kill()
        self.proc = None

    def start(self):
        self.phrase = wake_phrase()
        argv, env = _ps(WAKE_PS, phrase=self.phrase, wav="", out=self.dir)
        self.proc = subprocess.Popen(argv, env=env, stdout=subprocess.PIPE,
                                     creationflags=subprocess.CREATE_NO_WINDOW)
        threading.Thread(target=self.read, args=(self.proc,), daemon=True).start()

    def read(self, proc):
        name = self.phrase.split(" ", 1)[1]
        for raw in proc.stdout:
            hit = handle(raw.decode("utf-8", "ignore").strip(), name)
            if not hit or _busy.locked():
                continue
            woke, rest = hit
            if rest:                       # "hey cane open chrome": just do it
                run_text(self.bar, rest)
            else:
                push_to_talk(self.bar)

    def loop(self):
        while True:
            try:
                want = self.wanted()
                running = self.proc and self.proc.poll() is None
                if want and (not running or self.phrase != wake_phrase()):
                    self.stop()
                    self.start()
                elif not want and running:
                    self.stop()
            except Exception:
                pass
            time.sleep(3)


def wake_events(wav, phrase):
    """[(woke, rest)] the wake listener produces for a WAV file - for tests."""
    out = tempfile.mkdtemp(prefix="cane_wake_")
    argv, env = _ps(WAKE_PS, phrase=phrase, wav=wav, out=out)
    r = subprocess.run(argv, env=env, capture_output=True, timeout=90,
                       creationflags=subprocess.CREATE_NO_WINDOW)
    name = phrase.split(" ", 1)[1]
    hits = [handle(l.strip(), name)
            for l in r.stdout.decode("utf-8", "ignore").splitlines()]
    return [h for h in hits if h]
