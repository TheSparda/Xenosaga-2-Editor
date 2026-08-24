#!/usr/bin/env python3
"""Generate web/tables.json from x2fields.py.

The web ISO editor writes byte offsets into a multi-GB disc image without a
Python runtime on that tab, so it needs these constants in JSON. Keeping them
generated means x2fields.py stays the single source of truth; tests/test_tables.py
fails if the committed file drifts from it.

Usage:  python3 Editor/gen_web_tables.py [--check]
        --check  exit non-zero if the committed file is stale (used by CI)
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import x2fields as F  # noqa: E402

OUT = os.path.join(HERE, "..", "web", "tables.json")

# One field per line beats twelve — collapse arrays that hold no nested arrays
# or objects, so ["HP", 54, 4] stays readable as a row.
_LEAF_ARRAY = re.compile(r"\[\s*([^\[\]{}]*?)\s*\]")


def render():
    tables = F.web_tables()
    text = json.dumps(tables, indent=2, sort_keys=True)
    text = _LEAF_ARRAY.sub(
        lambda m: "[" + ", ".join(p.strip() for p in m.group(1).split(",") if p.strip()) + "]",
        text)
    # the collapse is textual, so prove it round-trips before anyone depends on it
    if json.loads(text) != json.loads(json.dumps(tables)):
        raise SystemExit("compacting changed the data — refusing to write")
    return text + "\n"


def main():
    text = render()
    path = os.path.normpath(OUT)
    if "--check" in sys.argv[1:]:
        try:
            with open(path) as f:
                current = f.read()
        except OSError:
            print(f"{path} is missing — run: python3 Editor/gen_web_tables.py")
            return 1
        if current != text:
            print(f"{path} is out of date with x2fields.py — "
                  f"run: python3 Editor/gen_web_tables.py")
            return 1
        print(f"{path} is up to date")
        return 0
    with open(path, "w") as f:
        f.write(text)
    print(f"wrote {path} ({len(text):,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
