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
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Xenosaga II Editor</title>
<style>
 /* Xenosaga sci-fi theme; all colors via CSS vars. Default = deep-space dark;
    body.light overrides to a clean steel/white variant. */
 :root{
  --bg:#0a0f1e; --panel:#111a30; --panel2:#1a2440; --headbg:linear-gradient(180deg,#15203c,#101a30);
  --ink:#e8eefb; --mut:#8695b8; --line:#293350;
  --acc:#46a6ff; --acc2:#7cc3ff; --accink:#04101f;
  --warn:#f0b429; --warnbd:#a97b1e; --changed-bg:#33290f;
  --input:#0d1526; --ring:#46a6ff40; --shadow:0 3px 16px #0007; --ok:#3ddc84; --err:#ff7a7a;
 }
 body.light{
  --bg:#e7ecf4; --panel:#f8fafd; --panel2:#eaf0f9; --headbg:linear-gradient(180deg,#f3f8ff,#e8f0fb);
  --ink:#15203a; --mut:#5c6c8e; --line:#ccd6e6;
  --acc:#1668d4; --acc2:#2f86e6; --accink:#f4f9ff;
  --warn:#8a5c10; --warnbd:#c79a2e; --changed-bg:#fbf0cd;
  --input:#fff; --ring:#1668d433; --shadow:0 2px 12px #0002; --ok:#1a8f4a; --err:#c0392b;
 }
 * { box-sizing:border-box; }
 body { font:15px/1.55 system-ui,-apple-system,Segoe UI,Roboto,sans-serif; margin:0;
   background:var(--bg); color:var(--ink); -webkit-font-smoothing:antialiased; }
 header { position:sticky; top:0; z-index:20; display:flex; align-items:center; gap:14px;
   padding:13px 22px; background:var(--headbg); border-bottom:1px solid var(--acc);
   box-shadow:var(--shadow); }
 header h1 { margin:0; font-size:18px; letter-spacing:.02em; color:var(--acc2);
   font-weight:700; }
 header .sub { color:var(--mut); font-size:12px; }
 header .spacer { flex:1; }
 main { padding:22px; max-width:1040px; margin:0 auto; }
 .card { background:var(--panel); border:1px solid var(--line); border-radius:14px;
   padding:16px 18px; margin-bottom:16px; box-shadow:var(--shadow); }
 .card h2 { margin:0 0 12px; font-size:14px; text-transform:uppercase; letter-spacing:.08em;
   color:var(--acc2); }
 table { border-collapse:collapse; width:100%; font-size:14px; }
 th { text-align:left; padding:6px 10px; font-size:11px; text-transform:uppercase;
   letter-spacing:.06em; color:var(--mut); border-bottom:1px solid var(--line); }
 td { text-align:left; padding:5px 10px; border-bottom:1px solid var(--line); }
 tbody tr:hover td { background:var(--panel2); }
 /* character sheet: each unit spans two rows so all 11 fields fit without scroll */
 #sheet td { text-align:center; border-bottom:0; padding:4px 6px; }
 #sheet td.name { text-align:left; font-weight:600; font-size:15px; vertical-align:middle;
   white-space:nowrap; padding-left:2px; border-right:1px solid var(--line); }
 #sheet tr.u2 td { padding-bottom:11px; border-bottom:1px solid var(--line); }
 #sheet .fl { font-size:9.5px; text-transform:uppercase; letter-spacing:.05em;
   color:var(--mut); margin-bottom:2px; }
 .es td.name { color:var(--mut); }
 #sheet tr.gearrow td { background:var(--panel2); }
 #sheet tr.gearrow .fl { color:var(--acc2); }
 #sheet tr.gearrow .fl::before { content:"⚙ "; }
 #sheet .gname { font-size:9px; color:var(--acc2); margin-top:2px; max-width:8.5ch;
   white-space:nowrap; overflow:hidden; text-overflow:ellipsis; margin-left:auto; margin-right:auto; }
 .pill { display:inline-block; padding:1px 9px; border-radius:20px; background:var(--panel2);
   border:1px solid var(--line); font-size:11px; letter-spacing:.03em; }
 code { background:var(--panel2); padding:1px 5px; border-radius:4px; font-size:12px; }
 select,input { background:var(--input); color:var(--ink); border:1px solid var(--line);
   border-radius:7px; font:inherit; padding:4px 8px; }
 select:focus,input:focus { outline:0; border-color:var(--acc); box-shadow:0 0 0 3px var(--ring); }
 select:hover,input:hover { border-color:var(--acc); }
 #sheet input { width:7ch; text-align:center; padding:3px 5px; }
 #gold { width:12ch; }
 input.changed { color:var(--warn); border-color:var(--warnbd); background:var(--changed-bg);
   font-weight:600; }
 .restore { display:none; margin-left:3px; background:transparent; border:1px solid var(--line);
   color:var(--mut); border-radius:6px; padding:2px 6px; cursor:pointer; font:inherit; line-height:1; }
 .restore:hover { border-color:var(--warnbd); color:var(--warn); }
 .restore.show { display:inline-block; }
 .btn { font:inherit; padding:6px 13px; border-radius:8px; cursor:pointer; border:1px solid var(--line);
   background:var(--panel2); color:var(--ink); transition:.12s; }
 .btn:hover:not(:disabled) { border-color:var(--acc); }
 .btn.primary { background:var(--acc); color:var(--accink); border:0; font-weight:600; }
 .btn.primary:hover:not(:disabled) { background:var(--acc2); }
 .btn:disabled { opacity:.4; cursor:default; }
 .toolbar { display:flex; flex-wrap:wrap; gap:10px; align-items:center; margin-bottom:14px; }
 .toolbar label { color:var(--mut); font-size:13px; }
 #badge { font-weight:700; }
 #status { font-size:13px; margin-left:2px; }
 #status.ok { color:var(--ok); } #status.err { color:var(--err); }
 .cell { white-space:nowrap; }
 .ok { color:var(--ok); font-weight:600; } .bad { color:var(--err); }
 .note { color:var(--mut); font-size:12.5px; margin:12px 0 0; }
 .todo { margin:0; padding-left:18px; } .todo li { margin:3px 0; }
 #toast { position:fixed; left:50%; bottom:26px; transform:translateX(-50%) translateY(20px);
   background:var(--panel); color:var(--ink); border:1px solid var(--acc); border-radius:10px;
   padding:10px 18px; box-shadow:var(--shadow); opacity:0; pointer-events:none; transition:.22s;
   font-size:14px; z-index:50; }
 #toast.show { opacity:1; transform:translateX(-50%) translateY(0); }
 #toast.err { border-color:var(--err); }
</style></head><body>
<header>
 <h1>XENOSAGA II</h1>
 <span class="sub">ISO &amp; Save Editor · discs %%SERIALS%%</span>
 <span class="spacer"></span>
 <button id="themebtn" class="btn" title="Toggle light/dark">◐ Theme</button>
</header>
<main>
 <div class="card"><h2>Character sheet</h2>
  <div class="toolbar">
    <label>Save</label> <select id="savesel">%%SAVEOPTS%%</select>
    <label>Gold</label> <span class="cell"><input id="gold" type="number" min="0" max="4294967295" autocomplete="off"></span>
    <span class="spacer" style="flex:1"></span>
    <button id="maxbtn" class="btn" disabled>Max all stats</button>
    <button id="revertbtn" class="btn" disabled>Revert all</button>
    <button id="savebtn" class="btn primary" disabled>Save changes <span id="badge"></span></button>
    <span id="status"></span>
  </div>
  <table id="sheet"><thead>%%SHEETHEAD%%</thead><tbody id="sheetbody"></tbody></table>
  <p class="note">Edits stage in memory and highlight amber (each has a ↺ restore); <b>Save
   changes</b> writes them to the selected file in one pass — a <code>.bak</code> is made
   first and the result is re-read to verify. The in-game save checksum isn't cracked yet,
   so an edited save <b>may be rejected by the game</b> until it is — test one in an emulator.</p>
 </div>
 <div class="card"><h2>Discs detected</h2>%%ISOS%%</div>
 <div class="card"><h2>Saves detected</h2>%%SAVES%%</div>
 <div class="card"><h2>Reverse-engineering roadmap</h2>
  <ul class="todo">
   <li><b>ISO tables</b> — characters/enemies/items/shops in the disc (new-game edits).</li>
   <li><b>Save checksum</b> — crack gamedata +0x08 so edited saves load guaranteed.</li>
   <li><b>Party / inventory / EXP</b> — need a known-content reference save to confirm.</li>
  </ul>
  <p class="note">Nothing is uploaded. Supply your own ISO/saves. See
   <code>Editor/Xenosaga2_ISO_offsets.md</code> for progress.</p>
 </div>
</main>
<div id="toast"></div>
<script>
const COLS = %%COLS%%;
const ES_COLS = %%ESCOLS%%;   // E.S. mech gear slots (experimental, raw ids)
const ES_EQUIP = %%ESEQUIP%%; // id -> E.S. accessory name (ISO catalog, ids 0-30)
// per-field caps for the "Max all stats" convenience button
const CAPS = {Level:99, HP:9999, "Current HP":9999, EP:99, Str:999, Vit:999,
  Eatk:999, Edef:999, Dex:99, Eva:99, Agl:99};
const $ = s => document.querySelector(s);
let CUR = -1;   // index of the loaded save

// theme toggle (persisted)
(function(){
  const saved = localStorage.getItem('x2theme');
  if(saved==='light') document.body.classList.add('light');
  $('#themebtn').onclick = () => {
    document.body.classList.toggle('light');
    localStorage.setItem('x2theme', document.body.classList.contains('light')?'light':'dark');
  };
})();

let toastT;
function toast(msg, err){
  const t=$('#toast'); t.textContent=msg; t.className='show'+(err?' err':'');
  clearTimeout(toastT); toastT=setTimeout(()=>t.className=t.className.replace('show',''),2400);
}

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
  const GA = COLS.slice(0, 6), GB = COLS.slice(6);   // two rows: 6 fields + the rest
  const cell = (c, idx, k) =>
    '<td><div class="fl">'+k[0]+'</div><span class="cell"><input type="number" min="0" '+
    'autocomplete="off" data-idx="'+idx+'" data-field="'+k[1]+'" data-def="'+(c[k[1]]??0)+
    '" value="'+(c[k[1]]??0)+'"></span></td>';
  const cols = GA.length;
  const rows = d.characters.filter(c=>c.active).map(c=>{
    const idx = d.characters.indexOf(c);
    const es = c.is_es ? ' es' : '';
    const span = c.is_es ? 3 : 2;
    const rowA = '<tr class="u1'+es+'"><td class="name" rowspan="'+span+'">'+c.name+'</td>'
      + GA.map(k=>cell(c,idx,k)).join('') + '</tr>';
    const padB = cols - GB.length;
    const rowB = '<tr class="u'+(c.is_es?'2m':'2')+es+'">' + GB.map(k=>cell(c,idx,k)).join('')
      + '<td></td>'.repeat(padB>0?padB:0) + '</tr>';
    if(!c.is_es) return rowA + rowB;
    const padC = cols - ES_COLS.length;
    const rowC = '<tr class="u2 es gearrow">' + ES_COLS.map(k=>cell(c,idx,k)).join('')
      + '<td></td>'.repeat(padC>0?padC:0) + '</tr>';
    return rowA + rowB + rowC;
  }).join('');
  $('#sheetbody').innerHTML = rows;
  document.querySelectorAll('#sheet input').forEach(decorate);
  // resolve E.S. gear ids -> accessory names (experimental; ids 0-30 known)
  document.querySelectorAll('#sheet tr.gearrow input').forEach(inp=>{
    const lab=document.createElement('div'); lab.className='gname';
    inp.parentElement.appendChild(lab);
    const upd=()=>{ const n=ES_EQUIP[inp.value];
      lab.textContent = n||('id '+inp.value); inp.title = n||('unknown id '+inp.value); };
    inp.addEventListener('input',upd); upd();
  });
  $('#maxbtn').disabled = false;
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
    s.textContent='✓ saved '+res.count+' field(s)';
    s.className='ok'; toast('✓ Saved '+res.count+' field(s) · .bak kept · verified');
  } else { s.textContent='✗ '+(res.error||'write failed'); s.className='err';
    toast('✗ '+(res.error||'write failed'), true); }
  s.dataset.sticky='1'; setTimeout(()=>{s.dataset.sticky='0';},50);
  updatePending();
};

$('#maxbtn').onclick = () => {
  document.querySelectorAll('#sheet input').forEach(inp=>{
    const cap = CAPS[inp.dataset.field];
    if(cap!==undefined){ inp.value = cap; inp.dispatchEvent(new Event('input',{bubbles:true})); }
  });
  toast('Maxed all stats — review, then Save changes');
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
    head = ""   # fields are self-labeled per cell (2-row layout)

    return (PAGE
            .replace("%%GAME%%", html.escape(F.GAME_NAME))
            .replace("%%SERIALS%%", ", ".join(sorted(F.SERIALS)))
            .replace("%%ISOS%%", iso_table)
            .replace("%%SAVES%%", save_table)
            .replace("%%SAVEOPTS%%", opts or "<option>none</option>")
            .replace("%%SHEETHEAD%%", head)
            .replace("%%COLS%%", json.dumps(SHEET_COLS))
            .replace("%%ESCOLS%%", json.dumps([[l, l] for (l, _o, _w, _k) in F.ES_EQUIP_FIELDS]))
            .replace("%%ESEQUIP%%", json.dumps({str(i): v["name"]
                     for i, v in F.es_equip_catalog().items()})))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass  # quiet

    def do_GET(self):
        route = self.path.split("?", 1)[0]
        if route in ("/", "/index.html"):
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
