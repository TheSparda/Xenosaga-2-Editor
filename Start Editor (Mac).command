#!/usr/bin/env bash
# Double-click this file to launch the Xenosaga II editor on macOS.
# (If it won't open: right-click -> Open the first time, or run
#  chmod +x "Start Editor (Mac).command" in Terminal.)
cd "$(dirname "$0")/Editor" || exit 1

PY=""
for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then
  echo "Python 3 is not installed."
  echo "Install it from https://www.python.org/downloads/ then double-click this again."
  read -r -p "Press Return to close..."
  exit 1
fi

echo "Starting Xenosaga II editor..."
echo "A browser tab will open. Pick your ISO/save there."
echo "Keep this window open while editing. Close it (or press Ctrl+C) to stop."
echo
exec "$PY" x2editor.py
