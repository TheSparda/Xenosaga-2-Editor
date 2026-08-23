// Xenosaga II Save Editor — web front-end. Runs the real Editor/x2save.py in
// Pyodide (CPython/WASM); the uploaded save is written to Pyodide's in-memory FS
// and the existing path-based decode_save()/write_save() are called unchanged, so
// every byte layout + container handling comes from one engine. Nothing is uploaded.

const SAVE_PATH = "/save.bin";
// character-sheet columns: [header, decoded-field key]
const SHEET = [["Lvl","Level"],["HP","HP"],["Cur HP","Current HP"],["EP","EP"],
  ["Str","Str"],["Vit","Vit"],["EAtk","Eatk"],["EDef","Edef"],["Dex","Dex"],["Eva","Eva"],["Agl","Agl"]];
const CAPS = {Level:99,HP:9999,"Current HP":9999,EP:99,Str:999,Vit:999,Eatk:999,Edef:999,Dex:99,Eva:99,Agl:99};

let pyReady = null, PY = null, REF = null, curSave = null, origName = "save.bin";
let fileHandle = null;
const SUPPORTS_FS = typeof window !== "undefined" && "showOpenFilePicker" in window;

const $ = (s,r=document)=>r.querySelector(s);
const esc = (s)=>String(s).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));

// ---- tiny IndexedDB kv (remember last opened) ----
const IDB="x2editor", STORE="kv";
function _idb(){return new Promise((res,rej)=>{const r=indexedDB.open(IDB,1);
  r.onupgradeneeded=()=>r.result.createObjectStore(STORE);r.onsuccess=()=>res(r.result);r.onerror=()=>rej(r.error);});}
async function idbSet(k,v){const db=await _idb();return new Promise((res,rej)=>{const t=db.transaction(STORE,"readwrite");t.objectStore(STORE).put(v,k);t.oncomplete=()=>res();t.onerror=()=>rej(t.error);});}
async function idbGet(k){const db=await _idb();return new Promise((res,rej)=>{const t=db.transaction(STORE,"readonly");const q=t.objectStore(STORE).get(k);q.onsuccess=()=>res(q.result);q.onerror=()=>rej(q.error);});}
async function idbDel(k){const db=await _idb();return new Promise((res,rej)=>{const t=db.transaction(STORE,"readwrite");t.objectStore(STORE).delete(k);t.oncomplete=()=>res();t.onerror=()=>rej(t.error);});}

let toastT;
function toast(msg,err){const t=$("#toast");if(!t)return;t.textContent=msg;t.className="show"+(err?" err":"");
  clearTimeout(toastT);toastT=setTimeout(()=>t.className=t.className.replace("show",""),2600);}

// ---- shared modal: review-changes + searchable picker ----
function _modalEls(){return{el:$("#modal"),title:$("#modalTitle"),body:$("#modalBody"),ok:$("#modalOk"),cancel:$("#modalCancel")};}
function openReview(title, bodyHtml, okLabel){
  return new Promise(res=>{const m=_modalEls();m.title.textContent=title;m.body.innerHTML=bodyHtml;
    m.ok.style.display="";m.ok.textContent=okLabel||"Confirm";m.cancel.textContent="Cancel";
    m.el.classList.remove("hidden");
    const done=v=>{m.el.classList.add("hidden");m.ok.onclick=m.cancel.onclick=m.el.onclick=null;res(v);};
    m.ok.onclick=()=>done(true);m.cancel.onclick=()=>done(false);
    m.el.onclick=e=>{if(e.target===m.el)done(false);};});
}
function openPicker(title, items, current){
  return new Promise(res=>{const m=_modalEls();m.title.textContent=title;m.ok.style.display="none";m.cancel.textContent="Close";
    m.body.innerHTML='<input class="picksearch" id="pkq" type="text" placeholder="Search by id or name…" autocomplete="off"><div id="pklist"></div>';
    m.el.classList.remove("hidden");
    const list=$("#pklist"),q=$("#pkq");
    const done=v=>{m.el.classList.add("hidden");m.cancel.onclick=m.el.onclick=null;res(v);};
    const draw=()=>{const s=(q.value||"").toLowerCase();
      list.innerHTML=items.filter(it=>!s||(it.id+" "+it.name+" "+(it.desc||"")).toLowerCase().includes(s)).slice(0,300)
        .map(it=>'<div class="pickrow'+(String(it.id)===String(current)?" sel":"")+'" data-id="'+it.id+'">'+
          '<span class="pid">'+it.id+'</span><span class="pn">'+esc(it.name)+'</span>'+
          (it.desc?'<span class="pd">'+esc(it.desc)+'</span>':'')+'</div>').join("")||'<div class="note">No matches.</div>';
      list.querySelectorAll(".pickrow").forEach(r=>r.onclick=()=>done(+r.dataset.id));};
    q.addEventListener("input",draw);draw();setTimeout(()=>q.focus(),50);
    m.cancel.onclick=()=>done(null);m.el.onclick=e=>{if(e.target===m.el)done(null);};});
}
window.openReview=openReview; window.openPicker=openPicker;

// ---- theme ----
(function(){try{if(localStorage.getItem("x2theme")==="light")document.body.classList.add("light");}catch(e){}
  const b=$("#themeBtn");if(b)b.onclick=()=>{document.body.classList.toggle("light");
    try{localStorage.setItem("x2theme",document.body.classList.contains("light")?"light":"dark");}catch(e){}};})();

// ---- mode tabs ----
document.querySelectorAll(".mtab").forEach(t=>t.onclick=()=>{
  document.querySelectorAll(".mtab").forEach(x=>x.classList.toggle("on",x===t));
  const m=t.dataset.mode;
  document.querySelectorAll(".mode").forEach(s=>s.classList.add("hidden"));
  const sec=document.getElementById("mode-"+m); if(sec)sec.classList.remove("hidden");
  if(m==="iso"&&window.initISO)window.initISO();
  if(m==="ref"&&window.initRef)window.initRef();
});

function bootProgress(pct,msg){const s=$("#engineStatus");if(!s)return;
  s.innerHTML=(pct<100?'<span class="spinner"></span>':"✓ ")+esc(msg)+
    '<div class="bar"><i style="width:'+pct+'%"></i></div>';}

// ---- Pyodide bootstrap ----
async function bootPyodide(){
  bootProgress(10,"Downloading Python runtime…");
  const py = await loadPyodide();
  bootProgress(55,"Loading save engine…");
  const grab = async u=>{const r=await fetch(u);if(!r.ok)throw new Error("fetch "+u+" ("+r.status+")");return r.text();};
  for(const f of ["x2fields.py","x2save.py","x2_consumables.json","x2_keyitems.json","x2_es_equip.json"])
    py.FS.writeFile(f, await grab("../Editor/"+f));
  bootProgress(80,"Wiring adapters…");
  py.runPython(`
import json, x2save, x2fields as F
def load_reference():
    cat = F.es_equip_catalog()
    return json.dumps({
      "roster": F.ROSTER,
      "esEquip": {str(i): v["name"] for i, v in cat.items()},
      "esEquipList": [{"id": i, "name": v["name"], "desc": v.get("desc","")} for i, v in sorted(cat.items())],
    })
def load_save(path):
    fmt = x2save.sniff_format(path)
    if not fmt: return json.dumps({"error":"Unrecognized save format."})
    try:
        d = x2save.decode_save(path, fmt); d["format"]=fmt; return json.dumps(d)
    except Exception as e:
        return json.dumps({"error": str(e)})
def apply_edits(path, payload):
    p = json.loads(payload)
    edits = {"characters": {int(k): v for k, v in (p.get("characters") or {}).items()}}
    if p.get("gold") is not None: edits["gold"] = p["gold"]
    return json.dumps(x2save.write_save(path, edits, make_backup=False))
`);
  REF = JSON.parse(py.runPython("load_reference()"));
  PY = py; bootProgress(100,"Ready — load a save");
  $("#pickBtn").disabled = false;
  return py;
}

// ---- load a save ----
async function handleFile(file, handle){
  await pyReady;
  origName = file.name || "save.bin"; fileHandle = handle || null;
  const buf = new Uint8Array(await file.arrayBuffer());
  PY.FS.writeFile(SAVE_PATH, buf);
  const d = JSON.parse(PY.runPython(`load_save(${JSON.stringify(SAVE_PATH)})`));
  if(d.error){ $("#editor").innerHTML = '<div class="card blocked">Could not open '+esc(origName)+' — '+esc(d.error)+'</div>'; return; }
  curSave = d;
  try{ await idbSet("last",{bytes:buf,name:origName}); refreshRecent(); }catch(e){}
  renderSheet(d);
}

function decorate(inp,onchange){
  const def=inp.getAttribute("data-def");
  let btn=inp.nextElementSibling;
  if(!btn||!btn.classList.contains("restore")){btn=document.createElement("button");btn.type="button";
    btn.className="restore";btn.textContent="↺";btn.title="Restore to "+def;inp.after(btn);}
  const refresh=()=>{const ch=String(inp.value)!==String(def);
    inp.classList.toggle("changed",ch);btn.classList.toggle("show",ch);(onchange||(()=>{}))();};
  inp.addEventListener("input",refresh);btn.onclick=()=>{inp.value=def;refresh();};refresh();
}

function renderSheet(d){
  const GA=SHEET.slice(0,6), GB=SHEET.slice(6);
  const cell=(c,idx,k)=>'<td><div class="fl">'+k[0]+'</div><span><input type="number" min="0" '+
    'autocomplete="off" data-idx="'+idx+'" data-field="'+k[1]+'" data-def="'+(c[k[1]]??0)+'" value="'+(c[k[1]]??0)+'"></span></td>';
  let rows="";
  d.characters.forEach((c,idx)=>{
    if(!c.active) return;
    const es=c.is_es?" es":"";
    rows+='<tr class="u1'+es+'"><td class="name" rowspan="'+(c.is_es?3:2)+'">'+esc(c.name)+'</td>'+GA.map(k=>cell(c,idx,k)).join("")+'</tr>';
    rows+='<tr class="u'+(c.is_es?"2m":"2")+es+'">'+GB.map(k=>cell(c,idx,k)).join("")+'<td></td>'+'</tr>';
    if(c.is_es){
      const gear=[["Gear 1","Gear 1"],["Gear 2","Gear 2"],["Gear 3","Gear 3"],["Gear 4","Gear 4"]];
      rows+='<tr class="u2 es gearrow">'+gear.map(k=>cell(c,idx,k)).join("")+'<td></td><td></td>'+'</tr>';
    }
  });
  $("#editor").innerHTML =
    '<div class="card"><h2>2 · Edit ('+esc(origName)+' · '+esc((d.format||"").toUpperCase())+')</h2>'+
    '<div class="toolbar"><label>Gold</label> <input id="gold" type="number" min="0" max="4294967295" '+
      'autocomplete="off" data-def="'+d.gold+'" value="'+d.gold+'">'+
      '<span style="flex:1"></span>'+
      '<button id="maxBtn" class="btn">Max stats</button>'+
      '<button id="revBtn" class="btn" disabled>Revert all</button>'+
      '<button id="saveBtn" class="btn primary" disabled>Save <span id="badge" class="badge"></span></button>'+
      '<span id="sstatus" class="status"></span></div>'+
    '<table id="sheet"><tbody>'+rows+'</tbody></table>'+
    '<p class="note">Edits stage in memory (amber, with ↺). <b>Save</b> writes them and downloads the '+
      'edited file'+(SUPPORTS_FS?' (or overwrites in place if you opened via the picker)':'')+' — a .bak is not '+
      'made here, so keep your original. The in-game save checksum isn\'t cracked yet, so an edited save '+
      '<b>may be rejected by the game</b> until it is — test one in your emulator.</p></div>';

  const gearNames = REF.esEquip || {};
  document.querySelectorAll("#sheet input").forEach(i=>decorate(i,updatePending));
  decorate($("#gold"),updatePending);
  const gearList = REF.esEquipList || [];
  document.querySelectorAll("#sheet tr.gearrow input").forEach(inp=>{
    const lab=document.createElement("div");lab.className="gname gpick";lab.title="Pick from list";
    inp.parentElement.appendChild(lab);
    const upd=()=>{const n=gearNames[inp.value];lab.textContent=(n||("id "+inp.value))+" ▾";
      inp.title=n||("unknown id "+inp.value);};
    inp.addEventListener("input",upd);upd();
    lab.onclick=async()=>{ if(!gearList.length)return;
      const id=await openPicker("E.S. gear", gearList, inp.value);
      if(id!==null){inp.value=id;inp.dispatchEvent(new Event("input",{bubbles:true}));} };
  });
  $("#maxBtn").onclick=()=>{document.querySelectorAll("#sheet input").forEach(i=>{const c=CAPS[i.dataset.field];
    if(c!==undefined){i.value=c;i.dispatchEvent(new Event("input",{bubbles:true}));}});toast("Maxed stats — review, then Save");};
  $("#revBtn").onclick=()=>{document.querySelectorAll("#sheet input, #gold").forEach(i=>{i.value=i.getAttribute("data-def");
    i.classList.remove("changed");const b=i.nextElementSibling;if(b&&b.classList.contains("restore"))b.classList.remove("show");});updatePending();};
  $("#saveBtn").onclick=applyAndSave;
  updatePending();
}

function changed(){return[...document.querySelectorAll("#sheet input.changed, #gold.changed")];}
function updatePending(){const n=changed().length;const b=$("#badge");if(b)b.textContent=n?"("+n+")":"";
  const s=$("#saveBtn"),r=$("#revBtn");if(s)s.disabled=!n;if(r)r.disabled=!n;}

function reviewHtml(){
  const g=$("#gold"); let html="";
  if(g.classList.contains("changed"))
    html+='<div class="revrow"><span class="rl">Gold</span><span class="ro">'+g.getAttribute("data-def")+'</span>→ <span class="rn">'+g.value+'</span></div>';
  const byChar={};
  changed().forEach(i=>{if(i.id==="gold")return;(byChar[i.dataset.idx]=byChar[i.dataset.idx]||[]).push(i);});
  Object.keys(byChar).forEach(idx=>{
    const nm=(curSave.characters[idx]||{}).name||("rec"+idx);
    html+='<div class="revgrp">'+esc(nm)+'</div>';
    byChar[idx].forEach(i=>html+='<div class="revrow"><span class="rl">'+esc(i.dataset.field)+
      '</span><span class="ro">'+i.getAttribute("data-def")+'</span>→ <span class="rn">'+i.value+'</span></div>');
  });
  return html;
}
async function applyAndSave(){
  const edits={characters:{}};
  const g=$("#gold");if(g.classList.contains("changed"))edits.gold=+g.value;
  changed().forEach(i=>{if(i.id==="gold")return;const idx=i.dataset.idx,f=i.dataset.field;
    (edits.characters[idx]=edits.characters[idx]||{})[f]=+i.value;});
  const dest = (fileHandle&&SUPPORTS_FS)?("Apply & save to "+origName):("Apply & download");
  if(!(await openReview("Review changes — "+origName, reviewHtml(), dest))) return;
  const st=$("#sstatus");st.textContent="saving…";st.className="status";$("#saveBtn").disabled=true;
  let ok=false;
  try{
    PY.runPython(`apply_edits(${JSON.stringify(SAVE_PATH)}, ${JSON.stringify(JSON.stringify(edits))})`);
    const bytes=PY.FS.readFile(SAVE_PATH);
    ok=true;
    // commit baseline
    document.querySelectorAll("#sheet input, #gold").forEach(i=>{i.setAttribute("data-def",i.value);
      i.classList.remove("changed");const b=i.nextElementSibling;if(b&&b.classList.contains("restore"))b.classList.remove("show");});
    await writeOut(bytes);
  }catch(e){st.textContent="✗ "+e;st.className="status err";toast("✗ "+e,true);}
  if(ok){st.textContent="✓ saved";st.className="status ok";}
  updatePending();
}

async function writeOut(bytes){
  // overwrite-in-place if we hold a writable handle; else download a copy
  if(fileHandle&&SUPPORTS_FS){
    try{
      if((await fileHandle.queryPermission({mode:"readwrite"}))!=="granted")
        await fileHandle.requestPermission({mode:"readwrite"});
      const w=await fileHandle.createWritable();await w.write(bytes);await w.close();
      toast("✓ Saved in place to "+origName);return;
    }catch(e){/* fall through to download */}
  }
  const blob=new Blob([bytes],{type:"application/octet-stream"});
  const a=document.createElement("a");a.href=URL.createObjectURL(blob);
  a.download=origName;document.body.appendChild(a);a.click();a.remove();
  setTimeout(()=>URL.revokeObjectURL(a.href),4000);
  toast("✓ Downloaded "+origName);
}

// ---- ingest funnel ----
async function pickFile(){
  if(SUPPORTS_FS){
    try{const [h]=await window.showOpenFilePicker();const f=await h.getFile();return handleFile(f,h);}
    catch(e){if(e&&e.name==="AbortError")return;}
  }
  $("#file").click();
}
$("#pickBtn").onclick=pickFile;
$("#file").onchange=e=>{const f=e.target.files[0];if(f)handleFile(f);};
const drop=$("#drop");
["dragover","dragenter"].forEach(ev=>drop.addEventListener(ev,e=>{e.preventDefault();drop.classList.add("drag");}));
["dragleave","drop"].forEach(ev=>drop.addEventListener(ev,e=>{e.preventDefault();drop.classList.remove("drag");}));
drop.addEventListener("drop",e=>{const f=e.dataTransfer.files[0];if(f)handleFile(f);});

async function refreshRecent(){
  const last=await idbGet("last").catch(()=>null);const el=$("#recent");if(!el)return;
  el.innerHTML = last ? '<div class="recent"><button class="chip" id="reopen">↻ '+esc(last.name)+
    '</button><button class="chip" id="forget">✕</button></div>' : "";
  if(last){$("#reopen").onclick=()=>handleFile(new File([last.bytes],last.name));
    $("#forget").onclick=async()=>{await idbDel("last");refreshRecent();};}
}

// unsaved guard
window.addEventListener("beforeunload",e=>{if(changed().length){e.preventDefault();e.returnValue="";}});

// ---- PWA: service worker, install prompt, share-target pickup ----
if("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(()=>{});
let deferredPrompt=null;
window.addEventListener("beforeinstallprompt",e=>{e.preventDefault();deferredPrompt=e;
  const b=$("#installBtn");if(b){b.classList.remove("hidden");b.onclick=async()=>{b.classList.add("hidden");
    deferredPrompt.prompt();deferredPrompt=null;};}});
async function pickupShared(){
  if(!/[?&]shared=1/.test(location.search))return;
  try{const c=await caches.open("x2editor-share");const r=await c.match("shared");
    if(r){const name=r.headers.get("X-Name")||"shared.bin";const blob=await r.blob();
      await c.delete("shared");await pyReady;handleFile(new File([blob],name));
      history.replaceState(null,"",location.pathname);}}catch(e){}
}

// ---- PWA staleness self-heal (B17) ----
const APP_VERSION = "1.0.1";
$("#forceRefresh")?.addEventListener("click", async ()=>{
  try{ if("serviceWorker" in navigator)
    for(const r of await navigator.serviceWorker.getRegistrations()) await r.unregister();
    if(window.caches) for(const k of await caches.keys()) await caches.delete(k);
  }catch(e){}
  location.reload(true);
});
async function checkForUpdate(){
  try{
    const html=await (await fetch("index.html?cb="+Date.now(),{cache:"no-store"})).text();
    const m=html.match(/id="appver">([^<]+)</);
    if(m && m[1].trim() && m[1].trim()!==APP_VERSION){
      const b=$("#updateBanner"); if(b){b.classList.remove("hidden");
        $("#reloadNew").onclick=()=>$("#forceRefresh").click();}
    }
  }catch(e){}
}

// boot
pyReady = bootPyodide().catch(e=>{bootProgress(100,"Engine failed: "+e);});
refreshRecent();
pickupShared();
checkForUpdate();
