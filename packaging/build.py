"""Build Tavinor for Windows: a folder app (PyInstaller), then Setup.exe
(Inno Setup) from it.

    python packaging/build.py            # app folder + Setup.exe
    python packaging/build.py --no-setup # app folder only

Output in packaging/out/. Only files tracked in the repo go in: your
settings, history, notes and keys stay out because they are never read.
Models are not bundled either; the Whisper and Piper ones download on
first use, Ollama models from Settings > Brain.
"""
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HERE = ROOT / "packaging"
OUT = HERE / "out"
STAGE = OUT / "stage"
VERSION = (HERE / "VERSION").read_text(encoding="utf-8").strip()

# The UI files Electron needs, and Electron itself without its 50 interface
# languages (English only: the notch has its own text).
UI_FILES = ["main.js", "preload.js", "dizzy.js", "package.json", "index.html", "app.js",
            "style.css", "settings.html", "settings.js", "settings.css", "chat.html",
            "chat.js", "chat.css"]
ELECTRON = ROOT / "ui" / "node_modules" / "electron" / "dist"

# Imported inside functions, so PyInstaller can't see them on its own.
HIDDEN = ["access", "activity", "agent", "bridge", "face", "focus", "gcal", "hover",
          "notes", "soul", "tasks", "uisettings", "voice", "winsys", "anthropic"]
# Packages that carry DLLs or data files (Whisper's engine, Piper's
# phoneme data, ONNX runtime).
COLLECT = ["faster_whisper", "ctranslate2", "onnxruntime", "piper", "tokenizers", "av"]


def stage_ui():
    ui = STAGE / "ui"
    if ui.exists():
        shutil.rmtree(ui)
    ui.mkdir(parents=True)
    for f in UI_FILES:
        shutil.copy2(ROOT / "ui" / f, ui / f)
    dist = ui / "node_modules" / "electron" / "dist"
    shutil.copytree(ELECTRON, dist, ignore=shutil.ignore_patterns("locales"))
    (dist / "locales").mkdir()
    shutil.copy2(ELECTRON / "locales" / "en-US.pak", dist / "locales" / "en-US.pak")
    return ui


def venv():
    """A clean environment with only requirements.txt, so nothing else
    installed on the build machine ends up in the app."""
    py = OUT / "venv" / "Scripts" / "python.exe"
    if not py.exists():
        subprocess.run([sys.executable, "-m", "venv", str(OUT / "venv")], check=True)
        subprocess.run([str(py), "-m", "pip", "install", "-q", "-r", str(HERE / "requirements.txt")], check=True)
    return py


def pyinstaller(ui):
    sep = ";"
    args = [str(venv()), "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed",
            "--name", "Tavinor", "--icon", str(ROOT / "tavinor.ico"),
            "--distpath", str(OUT / "dist"), "--workpath", str(OUT / "build"),
            "--specpath", str(OUT),
            "--add-data", "%s%sui" % (ui, sep)]
    for ico in ("tavinor.ico", "tavinor-tray-dark.ico", "tavinor-tray-light.ico"):
        args += ["--add-data", "%s%s." % (ROOT / ico, sep)]
    for m in HIDDEN:
        args += ["--hidden-import", m]
    for m in COLLECT:
        args += ["--collect-all", m]
    args += ["--paths", str(ROOT), str(ROOT / "tavinor.py")]
    subprocess.run(args, check=True, cwd=ROOT)
    return OUT / "dist" / "Tavinor"


def inno(app):
    iscc = shutil.which("ISCC") or next(
        (str(p) for p in (Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
                          Path.home() / r"AppData\Local\Programs\Inno Setup 6\ISCC.exe") if p.exists()), None)
    if not iscc:
        sys.exit("Inno Setup's ISCC.exe not found: install Inno Setup 6, or use --no-setup")
    subprocess.run([iscc, "/Q", "/DAppVersion=" + VERSION, "/DAppDir=" + str(app),
                    "/DOutDir=" + str(OUT), "/DRoot=" + str(ROOT), str(HERE / "tavinor.iss")], check=True)
    return OUT / ("Tavinor-Setup-%s.exe" % VERSION)


if __name__ == "__main__":
    app = pyinstaller(stage_ui())
    print("app folder:", app)
    if "--no-setup" not in sys.argv:
        print("installer:", inno(app))
