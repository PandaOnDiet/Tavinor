"""Build Tavinor-<VERSION>.msix: the app plus its Windows widget, signed.

    python packaging/build_msix.py

Needs the app folder from packaging/build.py (built here if missing), the
.NET 8 SDK and the Windows SDK (makeappx, signtool). Signing: a self-signed
certificate, made once and kept in packaging/out/cert/ (never committed).
A computer installing this package must trust that certificate; the
Microsoft Store would sign it instead.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT = HERE / "out"
LAYOUT = OUT / "msix" / "layout"
CERT = OUT / "cert"
PUBLISHER = "CN=PandaOnDiet"
VERSION = (HERE / "VERSION").read_text(encoding="utf-8").strip()
DOTNET = shutil.which("dotnet") or r"C:\Program Files\dotnet\dotnet.exe"


def sdk_tool(name):
    """Newest x64 copy of a Windows SDK tool (makeappx, signtool)."""
    base = Path(r"C:\Program Files (x86)\Windows Kits\10\bin")
    found = sorted(base.glob("10.*/x64/%s.exe" % name))
    if not found:
        sys.exit("%s.exe not found: install the Windows SDK" % name)
    return str(found[-1])


def certificate():
    """A self-signed code-signing certificate for PUBLISHER, made once."""
    pfx, cer = CERT / "tavinor-dev.pfx", CERT / "tavinor-dev.cer"
    password = "tavinor-dev"                  # protects a local test certificate only
    if not pfx.exists():
        CERT.mkdir(parents=True, exist_ok=True)
        ps = (
            "$c = New-SelfSignedCertificate -Type Custom -Subject '%s' -KeyUsage DigitalSignature "
            "-FriendlyName 'Tavinor test signing' -CertStoreLocation Cert:\\CurrentUser\\My "
            "-TextExtension @('2.5.29.37={text}1.3.6.1.5.5.7.3.3', '2.5.29.19={text}'); "
            "Export-PfxCertificate -Cert $c -FilePath '%s' -Password (ConvertTo-SecureString '%s' -Force -AsPlainText) | Out-Null; "
            "Export-Certificate -Cert $c -FilePath '%s' | Out-Null"
        ) % (PUBLISHER, pfx, password, cer)
        subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True)
    return pfx, cer, password


def layout():
    app = OUT / "dist" / "Tavinor"
    if not (app / "Tavinor.exe").exists():
        subprocess.run([sys.executable, str(HERE / "build.py"), "--no-setup"], check=True)
    if LAYOUT.exists():
        shutil.rmtree(LAYOUT)
    LAYOUT.mkdir(parents=True)
    shutil.copytree(app, LAYOUT / "Tavinor")
    subprocess.run([DOTNET, "publish", str(ROOT / "widget" / "TavinorWidget.csproj"), "-c", "Release",
                    "-r", "win-x64", "--self-contained", "-o", str(LAYOUT / "Widget"), "-nologo", "-v", "q"],
                   check=True)
    shutil.copytree(HERE / "assets", LAYOUT / "Assets")
    (LAYOUT / "Public").mkdir()               # the widget extension's public folder
    manifest = (HERE / "AppxManifest.xml").read_text(encoding="utf-8")
    (LAYOUT / "AppxManifest.xml").write_text(
        manifest.replace("{VERSION}", VERSION + ".0").replace("{PUBLISHER}", PUBLISHER), encoding="utf-8")


def pack_and_sign():
    msix = OUT / ("Tavinor-%s.msix" % VERSION)
    msix.unlink(missing_ok=True)
    subprocess.run([sdk_tool("makeappx"), "pack", "/o", "/d", str(LAYOUT), "/p", str(msix)], check=True,
                   stdout=subprocess.DEVNULL)
    pfx, cer, password = certificate()
    subprocess.run([sdk_tool("signtool"), "sign", "/fd", "SHA256", "/f", str(pfx), "/p", password, str(msix)],
                   check=True, stdout=subprocess.DEVNULL)
    shutil.copy2(cer, OUT / "Tavinor-test-certificate.cer")
    return msix


if __name__ == "__main__":
    layout()
    print("package:", pack_and_sign())
