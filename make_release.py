#!/usr/bin/env python3
"""Build the single-file editor: dist/Xenosaga2EditorPackage.pyz (stdlib zipapp).

Bundles Editor/*.py + any x2_*.json reference tables + the *_ids.txt id lists into
one executable Python archive. Run it with `python3 dist/Xenosaga2EditorPackage.pyz`
(double-click on Windows with the Python launcher installed); Mac/Linux and Windows
double-click wrappers are emitted alongside.

Usage:  python3 make_release.py [version]   # version tags the release zip, e.g. v1
"""
import os, sys, shutil, stat, tempfile, zipapp, zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(ROOT, "Editor")
DIST = os.path.join(ROOT, "dist")
OUT = os.path.join(DIST, "Xenosaga2EditorPackage.pyz")

MAIN = "import x2editor\nx2editor.main()\n"

# Extra (non-.py, non-x2_*.json) data files the editor loads at runtime.
EXTRA_DATA = ()

def _include(name):
    if name.endswith(".py"):
        return True
    if name.startswith("x2_") and name.endswith(".json"):
        return True
    return name in EXTRA_DATA

SH_LAUNCHER = '''#!/bin/sh
# Xenosaga II Editor — macOS / Linux launcher (double-click on macOS).
DIR="$(cd "$(dirname "$0")" && pwd)"
PY="$(command -v python3 || command -v python)"
if [ -z "$PY" ]; then
  echo "Python 3 is required. Install it from https://www.python.org/downloads/"
  read -r _; exit 1
fi
exec "$PY" "$DIR/Xenosaga2EditorPackage.pyz" "$@"
'''
BAT_LAUNCHER = '''@echo off
rem Xenosaga II Editor - Windows launcher (double-click).
where py >nul 2>nul && ( py -3 "%~dp0Xenosaga2EditorPackage.pyz" %* & goto :eof )
where python >nul 2>nul && ( python "%~dp0Xenosaga2EditorPackage.pyz" %* & goto :eof )
echo Python 3 is required. Install from https://www.python.org/downloads/ and tick "Add to PATH".
pause
'''

def main():
    os.makedirs(DIST, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        n = 0
        for name in sorted(os.listdir(SRC)):
            if _include(name):
                shutil.copy2(os.path.join(SRC, name), os.path.join(tmp, name)); n += 1
        with open(os.path.join(tmp, "__main__.py"), "w") as f:
            f.write(MAIN)
        zipapp.create_archive(tmp, OUT, interpreter="/usr/bin/env python3", compressed=True)
    os.chmod(OUT, os.stat(OUT).st_mode | stat.S_IEXEC)
    launchers = []
    mac = os.path.join(DIST, "Xenosaga2EditorLauncher (Mac, Linux).command")
    with open(mac, "w", newline="\n") as f: f.write(SH_LAUNCHER)
    os.chmod(mac, os.stat(mac).st_mode | stat.S_IEXEC); launchers.append(mac)
    win = os.path.join(DIST, "Xenosaga2EditorLauncher (Windows).bat")
    with open(win, "w", newline="\r\n") as f: f.write(BAT_LAUNCHER)
    launchers.append(win)
    print(f"bundled {n} files -> {OUT} ({os.path.getsize(OUT)//1024} KB)")
    for p in launchers: print(f"launcher -> {p}")

    ver = sys.argv[1] if len(sys.argv) > 1 else ""
    zname = f"Xenosaga2Editor-{ver}.zip" if ver else "Xenosaga2Editor.zip"
    zpath = os.path.join(DIST, zname)
    payload = [OUT] + launchers
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for p in payload:
            zi = zipfile.ZipInfo(os.path.basename(p))
            zi.external_attr = (os.stat(p).st_mode & 0xFFFF) << 16
            zi.compress_type = zipfile.ZIP_DEFLATED
            with open(p, "rb") as f: z.writestr(zi, f.read())
    print(f"release zip -> {zpath} ({os.path.getsize(zpath)//1024} KB, {len(payload)} files)")

if __name__ == "__main__":
    main()
