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


# Character-sheet columns shown in the save viewer: (header, decoded-field key).
SHEET_COLS = [("Lvl", "Level"), ("HP", "HP"), ("Cur HP", "Current HP"), ("EP", "EP"),
              ("Str", "Str"), ("Vit", "Vit"), ("EAtk", "Eatk"), ("EDef", "Edef"),
              ("Dex", "Dex"), ("Eva", "Eva"), ("Agl", "Agl")]

PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>Xenosaga II Editor</title>
<style>
 :root { color-scheme: light dark; }
 body { font: 15px/1.5 system-ui, sans-serif; margin: 0; }
 header { background:#1a1a2e; color:#e6e6fa; padding:18px 24px; }
 header h1 { margin:0; font-size:20px; }
 header .sub { opacity:.7; font-size:13px; }
 main { padding:24px; max-width:960px; }
 section { margin-bottom:28px; }
 h2 { font-size:16px; border-bottom:1px solid #8884; padding-bottom:6px; }
 table { border-collapse:collapse; width:100%; font-size:14px; }
 td, th { text-align:left; padding:5px 9px; border-bottom:1px solid #8883; }
 #sheet td, #sheet th { text-align:right; }
 #sheet td.name, #sheet th.name { text-align:left; }
 .ok { color:#2e9e4f; font-weight:600; }
 .bad { color:#c0392b; }
 .pill { display:inline-block; padding:1px 8px; border-radius:10px; background:#8883; font-size:12px; }
 .todo li { margin:2px 0; }
 code { background:#8882; padding:1px 4px; border-radius:3px; }
 select { font-size:14px; padding:4px 8px; max-width:100%; }
 .es { opacity:.7; }
 #sheet input { width:6ch; text-align:right; font:inherit; padding:2px 4px;
   border:1px solid #8886; border-radius:5px; background:transparent; color:inherit; }
 #gold { font:inherit; padding:3px 6px; border:1px solid #8886; border-radius:5px;
   background:transparent; color:inherit; }
 input.changed { color:#b8860b; border-color:#d9a520; background:#f6edcf40; font-weight:600; }
 .restore { display:none; margin-left:3px; background:transparent; border:1px solid #8886;
   color:#888; border-radius:5px; padding:1px 5px; cursor:pointer; font:inherit; line-height:1; }
 .restore:hover { border-color:#d9a520; color:#b8860b; }
 .restore.show { display:inline-block; }
 button#savebtn, button#revertbtn { font:inherit; padding:5px 12px; border-radius:6px;
   border:1px solid #8886; background:#8881; color:inherit; cursor:pointer; }
 button#savebtn:disabled, button#revertbtn:disabled { opacity:.45; cursor:default; }
 #badge { color:#b8860b; font-weight:600; }
 #status { font-size:13px; }
 #status.ok { color:#2e9e4f; } #status.err { color:#c0392b; }
 .cell { white-space:nowrap; }
</style></head><body>
<header>
 <h1>Xenosaga Episode II — ISO &amp; Save Editor</h1>
 <div class="sub">%%GAME%% · discs %%SERIALS%%</div>
</header>
<main>
 <section><h2>Discs detected</h2>%%ISOS%%</section>
 <section><h2>Character sheet</h2>
  <p>Save: <select id="savesel">%%SAVEOPTS%%</select>
     &nbsp; Gold: <span class="cell"><input id="gold" type="number" min="0"
       max="4294967295" style="width:11ch"></span>
     &nbsp; <button id="savebtn" disabled>Save changes <span id="badge"></span></button>
     <button id="revertbtn" disabled>Revert all</button>
     &nbsp; <span id="status"></span></p>
  <table id="sheet"><thead>%%SHEETHEAD%%</thead><tbody id="sheetbody"></tbody></table>
  <p style="opacity:.6;font-size:13px">Edits write to the selected file (a
   <code>.bak</code> is made first) and are re-read to verify. Note: the in-game
   save checksum isn't cracked yet, so an edited save <b>may be rejected by the
   game</b> until it is — test one in your emulator.</p>
 </section>
 <section><h2>Saves detected</h2>%%SAVES%%</section>
 <section><h2>Reverse-engineering roadmap</h2>
  <ul class="todo">
   <li><b>ISO tables</b> — locate character/tech/gear/enemy/shop records.</li>
   <li><b>Checksum</b> — crack the save checksum before enabling writes.</li>
   <li><b>Party / inventory</b> — need a known-content reference save to confirm.</li>
  </ul>
  <p style="opacity:.6">Nothing is uploaded. Supply your own ISO/saves. See
   <code>Editor/Xenosaga2_ISO_offsets.md</code> for progress.</p>
 </section>
</main>
<script>
const COLS = %%COLS%%;
const $ = s => document.querySelector(s);
let CUR = -1;   // index of the loaded save

// wire a single input for staging: amber when value != data-def, with a ↺ restore btn
function decorate(inp){
  const def = inp.getAttribute('data-def');
  let btn = inp.nextElementSibling;
  if(!btn || !btn.classList.contains('restore')){
    btn = document.createElement('button'); btn.type='button'; btn.className='restore';
    btn.textContent='↺'; btn.title='Restore to '+def; inp.after(btn);
  }
  const refresh = () => {
    const ch = String(inp.value) !== String(def);
    inp.classList.toggle('changed', ch); btn.classList.toggle('show', ch); updatePending();
  };
  inp.addEventListener('input', refresh);
  btn.onclick = () => { inp.value = def; refresh(); };
  refresh();
}

function changedInputs(){ return [...document.querySelectorAll('#sheet input.changed, #gold.changed')]; }
function updatePending(){
  const n = changedInputs().length;
  $('#badge').textContent = n ? '('+n+')' : '';
  $('#savebtn').disabled = !n; $('#revertbtn').disabled = !n;
  if(!n && $('#status').dataset.sticky!=='1') $('#status').textContent='';
}

async function loadSave(){
  CUR = $('#savesel').value;
  $('#status').textContent=''; $('#status').className='';
  const r = await fetch('/api/save?i='+CUR);
  if(!r.ok){ $('#sheetbody').innerHTML='<tr><td colspan="99">decode failed</td></tr>'; return; }
  const d = await r.json();
  const g = $('#gold'); g.value = d.gold; g.setAttribute('data-def', d.gold); decorate(g);
  const rows = d.characters.filter(c=>c.active).map((c,ri)=>{
    const idx = d.characters.indexOf(c);
    const tds = COLS.map(k =>
      '<td class="cell"><input type="number" min="0" data-idx="'+idx+'" data-field="'+k[1]+
      '" data-def="'+(c[k[1]]??0)+'" value="'+(c[k[1]]??0)+'"></td>').join('');
    const cls = c.name.startsWith('E.S.') ? ' class="es"' : '';
    return '<tr'+cls+'><td class="name">'+c.name+'</td>'+tds+'</tr>';
  }).join('');
  $('#sheetbody').innerHTML = rows;
  document.querySelectorAll('#sheet input').forEach(decorate);
  updatePending();
}

function collectEdits(){
  const edits = { characters:{} };
  const g = $('#gold');
  if(g.classList.contains('changed')) edits.gold = +g.value;
  changedInputs().forEach(inp=>{
    if(inp.id==='gold') return;
    const idx = inp.dataset.idx, f = inp.dataset.field;
    (edits.characters[idx] = edits.characters[idx] || {})[f] = +inp.value;
  });
  return edits;
}

$('#savebtn').onclick = async () => {
  const edits = collectEdits();
  $('#savebtn').disabled = true; $('#status').textContent='saving…'; $('#status').className='';
  const r = await fetch('/api/write', { method:'POST',
    headers:{'Content-Type':'application/json'}, body: JSON.stringify({i:+CUR, edits}) });
  const res = await r.json().catch(()=>({ok:false,error:'bad response'}));
  const s = $('#status');
  if(res.ok){
    // commit: new saved values become the baseline (clears amber)
    document.querySelectorAll('#sheet input, #gold').forEach(inp=>{
      inp.setAttribute('data-def', inp.value);
      inp.classList.remove('changed');
      if(inp.nextElementSibling&&inp.nextElementSibling.classList.contains('restore'))
        inp.nextElementSibling.classList.remove('show');
    });
    s.textContent='✓ saved '+res.count+' field(s) (.bak kept, round-trip verified)';
    s.className='ok';
  } else { s.textContent='✗ '+(res.error||'write failed'); s.className='err'; }
  s.dataset.sticky='1'; setTimeout(()=>{s.dataset.sticky='0';},50);
  updatePending();
};

$('#revertbtn').onclick = () => {
  document.querySelectorAll('#sheet input, #gold').forEach(inp=>{
    inp.value = inp.getAttribute('data-def'); inp.classList.remove('changed');
    if(inp.nextElementSibling&&inp.nextElementSibling.classList.contains('restore'))
      inp.nextElementSibling.classList.remove('show');
  });
  updatePending();
};

$('#savesel').addEventListener('change', loadSave);
loadSave();
</script>
</body></html>"""


def decodable_saves():
    """Saves under Saves/ that currently decode, in scan order."""
    out = []
    for s in SV.scan_saves(os.path.join(SCAN_ROOT, "Saves")):
        try:
            SV.extract_gamedata(s["path"], s["format"])
        except Exception:
            continue
        out.append(s)
    return out


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

    opts = "".join(f"<option value='{i}'>{html.escape(s['name'])} ({s['format']})</option>"
                   for i, s in enumerate(decodable_saves()))
    head = "<tr><th class='name'>character</th>" + \
           "".join(f"<th>{h}</th>" for h, _ in SHEET_COLS) + "</tr>"

    return (PAGE
            .replace("%%GAME%%", html.escape(F.GAME_NAME))
            .replace("%%SERIALS%%", ", ".join(sorted(F.SERIALS)))
            .replace("%%ISOS%%", iso_table)
            .replace("%%SAVES%%", save_table)
            .replace("%%SAVEOPTS%%", opts or "<option>none</option>")
            .replace("%%SHEETHEAD%%", head)
            .replace("%%COLS%%", json.dumps(SHEET_COLS)))


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
        elif self.path.startswith("/api/save?"):
            from urllib.parse import urlparse, parse_qs
            q = parse_qs(urlparse(self.path).query)
            try:
                i = int(q.get("i", ["0"])[0])
                s = decodable_saves()[i]
                data = SV.decode_save(s["path"], s["format"])
            except Exception as e:
                self.send_error(500, str(e)); return
            body = json.dumps(data).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
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

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path != "/api/write":
            self.send_error(404); return
        try:
            n = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(n))
            s = decodable_saves()[int(req["i"])]
            edits = req.get("edits", {}) or {}
            # normalize character keys to ints
            chars = {int(k): v for k, v in (edits.get("characters") or {}).items()}
            norm = {"characters": chars}
            if "gold" in edits:
                norm["gold"] = edits["gold"]
            count = (1 if "gold" in edits else 0) + sum(len(v) for v in chars.values())
            SV.write_save(s["path"], norm, fmt=s["format"])
            self._json({"ok": True, "count": count})
        except Exception as e:
            self._json({"ok": False, "error": str(e)}, code=200)


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
