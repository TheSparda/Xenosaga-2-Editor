#!/usr/bin/env python3
"""
Xenosaga Episode II ISO & Save editor — cross-platform local web app.

Stdlib only (http.server + json). Counterpart to Suikoden 3's s3editor.py.
Run:  python3 x2editor.py ["ISO/....(Disc 1).iso"] [port]
Then open the printed http://127.0.0.1:PORT URL in any browser.

This is the scaffold: it identifies discs, inventories saves, and shows the
reverse-engineering roadmap. Editable tables land here as x2patch / x2save grow.
"""
import json, os, sys, webbrowser, html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import x2patch as X
import x2save as SV
import x2fields as F

DEFAULT_PORT = 8748          # S3 uses 8747; keep X2 on its own port
SCAN_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ISO_PATH = None


def scan_isos(root):
    found = []
    for dirpath, _dirs, files in os.walk(root):
        if os.sep + ".git" in dirpath:
            continue
        for name in files:
            if name.lower().endswith((".iso", ".bin", ".img")):
                p = os.path.join(dirpath, name)
                try:
                    with X.Iso(p) as iso:
                        ok, serial, disc, vol = X.check_version(iso)
                    found.append({"path": p, "name": name, "ok": ok,
                                  "serial": serial, "disc": disc, "volume": vol})
                except OSError:
                    pass
    found.sort(key=lambda d: (d["disc"] or 9, d["name"]))
    return found


PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>Xenosaga II Editor</title>
<style>
 :root {{ color-scheme: light dark; }}
 body {{ font: 15px/1.5 system-ui, sans-serif; margin: 0; }}
 header {{ background:#1a1a2e; color:#e6e6fa; padding:18px 24px; }}
 header h1 {{ margin:0; font-size:20px; }}
 header .sub {{ opacity:.7; font-size:13px; }}
 main {{ padding:24px; max-width:900px; }}
 section {{ margin-bottom:28px; }}
 h2 {{ font-size:16px; border-bottom:1px solid #8884; padding-bottom:6px; }}
 table {{ border-collapse:collapse; width:100%; font-size:14px; }}
 td, th {{ text-align:left; padding:6px 10px; border-bottom:1px solid #8883; }}
 .ok {{ color:#2e9e4f; font-weight:600; }}
 .bad {{ color:#c0392b; }}
 .pill {{ display:inline-block; padding:1px 8px; border-radius:10px; background:#8883; font-size:12px; }}
 .todo li {{ margin:2px 0; }}
 code {{ background:#8882; padding:1px 4px; border-radius:3px; }}
</style></head><body>
<header>
 <h1>Xenosaga Episode II — ISO &amp; Save Editor</h1>
 <div class="sub">{game} · discs {serials}</div>
</header>
<main>
 <section><h2>Discs detected</h2>{iso_table}</section>
 <section><h2>Saves detected</h2>{save_table}</section>
 <section><h2>Decoded saves (PSV)</h2>{decoded_table}</section>
 <section><h2>Reverse-engineering roadmap</h2>
  <ul class="todo">
   <li><b>ISO tables</b> — locate character/tech/gear/enemy/shop records (start from the
       boot ELF; use the <code>.pnach</code> EE addresses as anchors).</li>
   <li><b>Save decode</b> — extract <code>gamedata</code> from each container
       (.psv/.max/.sps/.cbs), then map fields against the sample saves.</li>
   <li><b>Checksum</b> — crack the save checksum before enabling writes.</li>
   <li><b>Editor tabs</b> — port the S3 UI patterns once tables are verified.</li>
  </ul>
  <p style="opacity:.6">Nothing is uploaded. Supply your own ISO/saves. See
   <code>Editor/Xenosaga2_ISO_offsets.md</code> for progress.</p>
 </section>
</main></body></html>"""


def render():
    isos = scan_isos(SCAN_ROOT)
    if isos:
        rows = "".join(
            f"<tr><td>{'<span class=ok>OK</span>' if d['ok'] else '<span class=bad>?</span>'}</td>"
            f"<td>Disc {d['disc'] or '?'}</td><td>{html.escape(d['serial'] or '?')}</td>"
            f"<td>{html.escape(d['name'])}</td></tr>" for d in isos)
        iso_table = f"<table><tr><th></th><th></th><th>serial</th><th>file</th></tr>{rows}</table>"
    else:
        iso_table = "<p class='bad'>No disc images found under the project folder.</p>"

    saves = SV.scan_saves(os.path.join(SCAN_ROOT, "Saves"))
    if saves:
        rows = "".join(
            f"<tr><td><span class=pill>{s['format']}</span></td>"
            f"<td>{s['region'] or '?'}</td><td>{s['size']:,}</td>"
            f"<td>{html.escape(s['name'])}</td></tr>" for s in saves)
        save_table = (f"<table><tr><th>format</th><th>region</th><th>bytes</th>"
                      f"<th>file</th></tr>{rows}</table>")
    else:
        save_table = "<p>No saves found under <code>Saves/</code>.</p>"

    # Decode the PSV saves (the format we've reverse-engineered so far).
    drows = []
    for s in saves:
        if s["format"] != "psv":
            continue
        try:
            d = SV.decode_save(s["path"])
        except Exception:
            continue
        party = ", ".join(f"{c['name']} L{c['Level']}"
                           for c in d["characters"] if c["active"])
        drows.append(f"<tr><td>{html.escape(s['name'])}</td>"
                     f"<td>{d['gold']:,}</td><td>{html.escape(party)}</td></tr>")
    if drows:
        decoded_table = ("<table><tr><th>file</th><th>gold</th><th>party</th></tr>"
                         + "".join(drows) + "</table>")
    else:
        decoded_table = ("<p>No decodable PSV saves found. (.max/.sps/.cbs decode "
                         "is still on the roadmap.)</p>")

    return PAGE.format(
        game=html.escape(F.GAME_NAME),
        serials=", ".join(sorted(F.SERIALS)),
        iso_table=iso_table, save_table=save_table, decoded_table=decoded_table)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass  # quiet

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            body = render().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/api/status":
            data = {
                "game": F.GAME_NAME,
                "serials": F.SERIALS,
                "isos": scan_isos(SCAN_ROOT),
                "saves": SV.scan_saves(os.path.join(SCAN_ROOT, "Saves")),
            }
            body = json.dumps(data).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_error(404)


def main():
    global ISO_PATH
    args = sys.argv[1:]
    port = DEFAULT_PORT
    if args and args[-1].isdigit():
        port = int(args.pop())
    if args:
        ISO_PATH = args[0]

    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"Xenosaga II editor running at {url}")
    print("Pick your disc/save there. Ctrl+C to stop.")
    if not os.environ.get("X2_NO_BROWSER"):
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
