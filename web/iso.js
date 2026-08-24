// Xenosaga II ISO Editor (web) — enemy stats + rewards, VERIFIED tables.
// Never loads the 4.6 GB disc: reads two small ranged slices (stat table ~7 KB,
// rewards table ~2 KB), edits in memory, and writes only the changed byte-runs
// back in place via the File System Access API (desktop Chromium).
// Table derivation: Editor/Xenosaga2_ISO_offsets.md (74/76 exact guide matches).
(function(){
  const FS = "showOpenFilePicker" in window;
  // Verified disc-1 tables. These are byte offsets we write into a 4.6 GB image,
  // so they are NOT duplicated here: tables.json is generated from
  // Editor/x2fields.py (and CI fails if it drifts). If the fetch fails we refuse
  // to open a disc rather than fall back to a possibly-stale copy.
  let SBASE, STRIDE, COUNT, RBASE, RSTRIDE, SFIELDS, RFIELDS, SEND, REND, BOSS_ID_MIN, ID_OFF;
  let AFIELDS, AFF_NORMAL, CATKEYS, SERIAL;
  let TABLES=null;
  // Patch files are interchangeable with `x2patch.py export-patch/apply-patch`.
  const PATCH_FORMAT="x2-enemy-patch", PATCH_VERSION=1;

  async function loadTables(){
    if(TABLES) return TABLES;
    const r=await fetch("tables.json",{cache:"no-cache"});
    if(!r.ok) throw new Error("tables.json ("+r.status+")");
    const t=await r.json();
    SBASE=t.enemy.base; STRIDE=t.enemy.stride; COUNT=t.enemy.count;
    SFIELDS=t.enemy.fields; ID_OFF=t.enemy.idOff;
    AFIELDS=t.enemy.affinityFields||[]; AFF_NORMAL=t.enemy.affinityNormal;
    RBASE=t.reward.base; RSTRIDE=t.reward.stride; RFIELDS=t.reward.fields;
    SEND=SBASE+COUNT*STRIDE; REND=RBASE+COUNT*RSTRIDE;
    BOSS_ID_MIN=t.bossIdMin; CATKEYS=t.catalogKeys||{};
    SERIAL=Object.keys(t.serials||{}).find(k=>t.serials[k]===1)||"SLUS-20892";
    return (TABLES=t);
  }

  let handle=null, cat=null, backedUp=false;
  // two independent slices: {buf, orig, dv, base}
  let S=null, R=null;
  const $=(s,r=document)=>r.querySelector(s);
  const esc=(s)=>String(s).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const toastFn=(m,e)=>{try{window.toast&&window.toast(m,e);}catch(_){}};

  function get(T,i,off,w){const a=i*(T===S?STRIDE:RSTRIDE)+off;
    return w===4?T.dv.getUint32(a,true):w===2?T.dv.getUint16(a,true):T.buf[a];}
  function put(T,i,off,w,v){const a=i*(T===S?STRIDE:RSTRIDE)+off;
    const max=w===4?0xFFFFFFFF:w===2?0xFFFF:0xFF;
    v=Math.max(0,Math.min(Math.round(+v||0),max));
    if(w===4)T.dv.setUint32(a,v,true);else if(w===2)T.dv.setUint16(a,v,true);else T.buf[a]=v;}
  function getOrig(T,i,off,w){const a=i*(T===S?STRIDE:RSTRIDE)+off;
    const d=new DataView(T.orig.buffer);
    return w===4?d.getUint32(a,true):w===2?d.getUint16(a,true):T.orig[a];}
  // stats and affinities live in the stat record; rewards are their own table
  const tableOf=(f)=>RFIELDS.some(x=>x[0]===f)?R:S;
  const allFields=()=>SFIELDS.concat(AFIELDS,RFIELDS);
  const specOf=(f)=>allFields().find(x=>x[0]===f);
  // retail value of one field, from the verified bestiary (affinities have none)
  const retail=(i,label)=>{
    const key=CATKEYS[label];
    return (key && cat[i] && cat[i][key]!==undefined) ? cat[i][key] : undefined;
  };

  async function loadCat(){ if(cat)return cat;
    try{cat=await (await fetch("../Editor/x2_enemies.json")).json();}catch(e){cat={};} return cat; }

  window.initISO = async function(){
    const root=$("#isoRoot"); if(root.dataset.init) return; root.dataset.init="1";
    if(!FS){ root.innerHTML='<div class="card blocked"><b>ISO editing needs desktop Chrome / Edge / Brave / Opera</b>'+
      ' (File System Access API). The Save editor works everywhere, including mobile.</div>'; return; }
    try{ await loadTables(); }
    catch(e){ root.innerHTML='<div class="card blocked"><b>Could not load the disc table definitions</b>'+
      ' — '+esc(String(e))+'. Try ↻ Force refresh in the footer.</div>'; root.dataset.init=""; return; }
    await loadCat();
    root.innerHTML='<div class="card"><h2>1 · Open disc 1 ISO</h2>'+
      '<button id="isoPick" class="btn primary">Choose ISO…</button> '+
      '<span id="isoStatus" class="status"></span>'+
      '<div id="isoRecent"></div>'+
      '<p class="note">Xenosaga II (USA) <b>Disc 1</b> — SLUS-20892. Enemy edits apply to a new game. '+
      'Edits write in place; work on a copy or tick backup. Your last disc is remembered for '+
      'one-tap reopening (only the file reference is stored — never the disc itself).</p></div>'+
      '<div id="isoEdit"></div>';
    $("#isoPick").onclick=openISO;
    showLastIso();
  };

  // ---- remember last opened ISO (stores the file HANDLE only — the 4.6 GB is never copied) ----
  const IDB=()=>window.x2idb;
  function rememberIso(name,h){ const k=IDB(); if(k) k.set("lastIso",{name,handle:h,at:Date.now()}).catch(()=>{}); }
  async function showLastIso(){
    const el=$("#isoRecent"), k=IDB(); if(!el||!k) return;
    let rec; try{ rec=await k.get("lastIso"); }catch(e){ return; }
    if(!rec||!rec.handle){ el.innerHTML=""; return; }
    const when=k.fmtWhen?k.fmtWhen(rec.at):"";
    el.innerHTML='<div class="recent"><span class="muted small">Last opened:</span>'+
      '<button class="chip" id="isoReopen" title="Reopen this disc">↻ '+esc(rec.name)+
      (when?' <span class="muted">('+esc(when)+')</span>':'')+'</button>'+
      '<button class="chip mini" id="isoForget" title="Forget this disc" aria-label="Forget this disc">✕</button></div>';
    $("#isoReopen").onclick=()=>reopenLastIso(rec);
    $("#isoForget").onclick=async()=>{await k.del("lastIso").catch(()=>{});showLastIso();};
  }
  async function reopenLastIso(rec){
    const st=$("#isoStatus"), k=IDB();
    try{
      if(!(await k.ensureWritable(rec.handle))){
        st.textContent="✗ Reopen cancelled — write permission denied."; st.className="status err"; return; }
      handle=rec.handle;
      await commitISO(await handle.getFile());
    }catch(e){
      st.textContent="✗ Could not reopen — the file may have moved. Pick it again."; st.className="status err";
    }
  }

  async function openISO(){
    try{ [handle]=await window.showOpenFilePicker(); }catch(e){ return; }
    return commitISO(await handle.getFile());
  }

  // Validate + commit an ISO from a File (handle is already set by the caller).
  async function commitISO(f){
    const st=$("#isoStatus"); st.textContent="checking disc…"; st.className="status";
    const head=new Uint8Array(await f.slice(0,0x200000).arrayBuffer());
    const asc=s=>{let o=-1;const t=[...s].map(c=>c.charCodeAt(0));
      for(let i=0;i<head.length-t.length;i++){let m=true;for(let j=0;j<t.length;j++)if(head[i+j]!==t[j]){m=false;break;}if(m){o=i;break;}}return o;};
    const vol=new TextDecoder().decode(head.slice(0x8028,0x8028+11));
    if(vol!=="XENOSAGA_II"){ st.textContent="✗ Not a Xenosaga II disc (volume "+esc(vol)+")"; st.className="status err"; return; }
    if(asc("SLUS_208.92")<0){
      st.textContent = asc("SLUS_211.33")>=0 ? "✗ This is Disc 2 — the enemy tables are on Disc 1."
                                             : "✗ Unrecognized Xenosaga II serial."; st.className="status err"; return; }
    const sb=new Uint8Array(await f.slice(SBASE,SEND).arrayBuffer());
    const rb=new Uint8Array(await f.slice(RBASE,REND).arrayBuffer());
    S={buf:sb,orig:sb.slice(),dv:new DataView(sb.buffer),base:SBASE};
    R={buf:rb,orig:rb.slice(),dv:new DataView(rb.buffer),base:RBASE};
    backedUp=false;
    // sanity anchor: Perun (rec 6) HP must match the bestiary on an unmodified disc
    const [,hpO,hpW]=SFIELDS.find(f=>f[0]==="HP");
    const perun=get(S,6,hpO,hpW), want=retail(6,"HP");
    st.textContent="✓ Disc 1 loaded ("+f.name+")"+
      (want!==undefined&&perun!==want?" — note: "+esc(cat[6]?cat[6].name:"record 6")+" HP reads "+
        perun.toLocaleString()+" not "+want.toLocaleString()+" (modified disc?)":"");
    st.className="status ok";
    rememberIso(f.name||"disc1.iso",handle);   // one-tap reopen next visit
    renderEnemy();
    showLastIso();
  }

  function renderEnemy(){
    const opts=Object.keys(cat).sort((a,b)=>+a-+b).map(i=>'<option value="'+i+'">'+
      String(i).padStart(3,"0")+' · '+esc(cat[i].name)+'</option>').join("");
    $("#isoEdit").innerHTML=
      '<div class="card"><h2>2 · Enemy</h2>'+
      '<div class="toolbar"><label>Enemy</label> <select id="esel">'+opts+'</select>'+
      '<label style="margin-left:8px"><input type="checkbox" id="ebak"> back up ISO first</label>'+
      '<span style="flex:1"></span>'+
      '<button id="erev" class="btn" disabled>Revert all</button>'+
      '<button id="esave" class="btn primary" disabled>Save to ISO <span id="ebadge" class="badge"></span></button>'+
      '<span id="estat" class="status"></span></div>'+
      '<table id="etbl"><tbody><tr id="erow"></tr><tr id="erow2" class="gearrow"></tr></tbody></table>'+
      '<div id="eretail" class="note"></div>'+
      '<details class="unverified"><summary>⚠ Damage affinities — unverified, opt in</summary>'+
        '<p class="note">Eight percentages in the record ('+AFF_NORMAL+' = normal damage, '+
        'lower resists, higher takes extra, 0 is immune). That there are eight and that they '+
        'hold '+AFF_NORMAL+' in ordinary records is solid — <b>which element each slot is has '+
        'not been confirmed</b>, so they are numbered rather than named. Editing them is an '+
        'experiment; the retail comparison and patch export handle them separately because '+
        'the bestiary has no baseline for them.</p>'+
        '<table><tbody><tr id="erow3"></tr></tbody></table></details>'+
      '<p class="note">Stats + battle rewards, verified against guide data (74/76 exact matches). '+
      'Writes only the changed bytes back at their exact offsets.</p></div>'+
      '<div class="card"><h2>3 · Rebalance (all '+COUNT+' enemies)</h2>'+
      '<p class="sub" style="margin:0 0 10px">The community\'s #1 complaint is bloated enemy HP. '+
      'Scale it globally — 50% halves every enemy\'s HP; rewards can be scaled up to keep pace.</p>'+
      '<div class="toolbar"><label>Presets</label>'+
      PRESETS.map((p,i)=>'<button class="btn preset" data-p="'+i+'">'+esc(p.label)+'</button>').join("")+
      '</div>'+
      '<div class="toolbar">'+
      '<label>HP</label> <input type="number" id="sclHP" value="100" min="1" max="1000" style="width:8ch">%'+
      '<label style="margin-left:10px">EXP/SP/CP</label> <input type="number" id="sclRW" value="100" min="1" max="1000" style="width:8ch">%'+
      '<label style="margin-left:10px"><input type="checkbox" id="sclBoss"> bosses too (IDs '+BOSS_ID_MIN+'+)</label>'+
      '<span style="flex:1"></span>'+
      '<button id="sclApply" class="btn primary">Stage rebalance</button></div>'+
      '<p class="note">Staged into the same pending-changes set above — review everything before writing. '+
      'Values round to whole numbers; HP floors at 1. Rebalancing always scales from the values '+
      'on the disc, so nudging a preset twice will not compound.</p></div>'+
      '<div class="card"><h2>4 · Patch files &amp; retail values</h2>'+
      '<div class="toolbar">'+
      '<button id="pExport" class="btn">⬇ Export patch…</button>'+
      '<button id="pImport" class="btn">⬆ Import patch…</button>'+
      '<input type="file" id="pFile" accept=".json,application/json" hidden>'+
      '<span style="flex:1"></span>'+
      '<button id="pDiff" class="btn">Compare to retail</button>'+
      '<button id="pRestore" class="btn">Stage restore to retail</button>'+
      '</div>'+
      '<p class="note">A patch is a small JSON file listing only the fields you changed, so you can '+
      'share a rebalance instead of a 4.6 GB disc. Importing <i>stages</i> the changes for review '+
      'rather than writing them. The command line reads and writes the same file: '+
      '<code>x2patch.py export-patch</code> / <code>apply-patch</code>. Because the bestiary shipped '+
      'with this editor holds the verified retail numbers, the editor can also tell you exactly how '+
      'your disc differs from an unmodified one — and put it back.</p></div>';
    $("#esel").onchange=loadEnemy;
    $("#erev").onclick=()=>{S.buf.set(S.orig);R.buf.set(R.orig);loadEnemy();epending();};
    $("#esave").onclick=saveISO;
    $("#sclApply").onclick=()=>stageRebalance();
    document.querySelectorAll(".preset").forEach(b=>b.onclick=()=>{
      const p=PRESETS[+b.dataset.p];
      $("#sclHP").value=p.hp; $("#sclRW").value=p.rw; $("#sclBoss").checked=!!p.bosses;
      stageRebalance();
    });
    $("#pExport").onclick=exportPatch;
    $("#pImport").onclick=()=>$("#pFile").click();
    $("#pFile").onchange=e=>{const f=e.target.files[0]; e.target.value=""; if(f)importPatch(f);};
    $("#pDiff").onclick=showRetailDiff;
    $("#pRestore").onclick=stageRestore;
    loadEnemy();
  }

  const PRESETS=[
    {label:"Halve HP",              hp:50,  rw:100},
    {label:"Halve HP · +50% rewards", hp:50,  rw:150},
    {label:"Gentle (75% HP)",       hp:75,  rw:120},
    {label:"Double rewards",        hp:100, rw:200},
    {label:"Harder (+50% HP)",      hp:150, rw:100},
    {label:"Halve everything, bosses too", hp:50, rw:100, bosses:true},
  ];

  // `val` is the staged (current) value shown in the box; `def` is the value on
  // disc. They differ after a staged rebalance or when revisiting an edited enemy —
  // keeping them separate is what makes the amber highlight and per-field ↺ mean
  // "differs from the disc" rather than "differs from whatever was last rendered".
  function cellHtml(lbl,off,w,val,def){
    return '<td><div class="fl">'+lbl+'</div><span><input type="number" min="0" autocomplete="off" '+
      'data-f="'+lbl+'" data-o="'+off+'" data-w="'+w+'" data-def="'+def+'" value="'+val+'"></span></td>';
  }
  function loadEnemy(){
    const i=+$("#esel").value;
    const eid=get(S,i,ID_OFF,2);
    $("#erow").innerHTML=SFIELDS.map(([l,o,w])=>cellHtml(l,o,w,get(S,i,o,w),getOrig(S,i,o,w))).join("");
    $("#erow2").innerHTML='<td><div class="fl">rewards</div></td>'+
      RFIELDS.map(([l,o,w])=>cellHtml(l,o,w,get(R,i,o,w),getOrig(R,i,o,w))).join("")+
      '<td colspan="4"><div class="fl">enemy id</div><span class="muted small">'+eid+
      (eid>=BOSS_ID_MIN?" · boss":"")+'</span></td>';
    $("#erow3").innerHTML=AFIELDS.map(([l,o,w])=>
      cellHtml(l,o,w,get(S,i,o,w),getOrig(S,i,o,w))).join("");
    // how this record compares with an unmodified disc
    const off=SFIELDS.concat(RFIELDS).filter(([l,o,w])=>{
      const v=retail(i,l); return v!==undefined && get(tableOf(l),i,o,w)!==v; });
    $("#eretail").innerHTML = off.length
      ? "Differs from retail: "+off.map(([l,o,w])=>esc(l)+" "+get(tableOf(l),i,o,w).toLocaleString()+
          " (retail "+retail(i,l).toLocaleString()+")").join(", ")
      : "Matches the retail values for this enemy.";
    document.querySelectorAll("#erow input, #erow2 input, #erow3 input").forEach(inp=>{
      let btn=inp.nextElementSibling;
      if(!btn||!btn.classList.contains("restore")){btn=document.createElement("button");btn.type="button";
        btn.className="restore";btn.textContent="↺";inp.after(btn);}
      const refresh=()=>{const f=inp.dataset.f,off=+inp.dataset.o,w=+inp.dataset.w;
        put(tableOf(f),i,off,w,+inp.value);
        const ch=String(inp.value)!==String(inp.getAttribute("data-def"));
        inp.classList.toggle("changed",ch);btn.classList.toggle("show",ch);epending();};
      inp.addEventListener("input",refresh);
      btn.onclick=()=>{inp.value=inp.getAttribute("data-def");refresh();};refresh();
    });
    epending();
  }

  function stageRebalance(){
    const hpP=+$("#sclHP").value/100, rwP=+$("#sclRW").value/100;
    const bosses=$("#sclBoss").checked;
    if(!(hpP>0)||!(rwP>0)) return;
    const hpf=SFIELDS.find(f=>f[0]==="HP");
    let n=0;
    for(let i=0;i<COUNT;i++){
      // read the id off the disc, not the vanilla catalog, so a partly-modified
      // disc still classifies correctly
      if(!bosses && get(S,i,ID_OFF,2)>=BOSS_ID_MIN) continue;
      if(hpP!==1&&hpf){const [,o,w]=hpf;
        put(S,i,o,w,Math.max(1,Math.round(getOrig(S,i,o,w)*hpP)));n++;}
      if(rwP!==1){
        RFIELDS.forEach(([,o,w])=>put(R,i,o,w,Math.round(getOrig(R,i,o,w)*rwP)));n++;
      }
    }
    loadEnemy();epending();
    toastFn(n?("✓ Rebalance staged for "+n+" record(s) — review & Save to ISO"):"No changes to stage");
  }

  function diffCount(){let n=0;
    for(const T of [S,R]){for(let i=0;i<T.buf.length;i++)if(T.buf[i]!==T.orig[i]){n++;while(i<T.buf.length&&T.buf[i]!==T.orig[i])i++;}}
    return n;}
  function epending(){const n=diffCount();const b=$("#ebadge");if(b)b.textContent=n?"("+n+")":"";
    const s=$("#esave"),r=$("#erev");if(s)s.disabled=!n;if(r)r.disabled=!n;}
  function diffRuns(T){const runs=[];let i=0;while(i<T.buf.length){if(T.buf[i]!==T.orig[i]){let j=i;
    while(j<T.buf.length&&T.buf[j]!==T.orig[j])j++;runs.push([i,j]);i=j;}else i++;}return runs;}

  function reviewRows(){
    // summarize per-enemy field diffs across both tables
    let rows="",count=0;
    for(let i=0;i<COUNT && count<400;i++){
      let cells="";
      for(const [T,FL] of [[S,SFIELDS],[S,AFIELDS],[R,RFIELDS]]){
        for(const [l,o,w] of FL){
          const a=getOrig(T,i,o,w),b=get(T,i,o,w);
          if(a!==b) cells+='<div class="revrow"><span class="rl">'+l+'</span><span class="ro">'+
            a.toLocaleString()+'</span>→ <span class="rn">'+b.toLocaleString()+'</span></div>';
        }
      }
      if(cells){rows+='<div class="revgrp">'+String(i).padStart(3,"0")+' · '+esc(cat[i]?cat[i].name:i)+'</div>'+cells;count++;}
    }
    if(count>=400) rows+='<div class="note">…truncated…</div>';
    return rows;
  }

  // ---- patch files, retail comparison, restore ----------------------------
  // Verified fields are exported against the retail bestiary so a patch describes
  // a complete mod, not just this session's edits. Affinities have no retail
  // baseline, so those are exported only where they differ from the opened disc.
  function buildPatch(note){
    const edits={};
    for(let i=0;i<COUNT;i++){
      const f={};
      for(const [l,o,w] of SFIELDS.concat(RFIELDS)){
        const cur=get(tableOf(l),i,o,w), van=retail(i,l);
        if(van!==undefined && cur!==van) f[l]=cur;
      }
      for(const [l,o,w] of AFIELDS){
        const cur=get(S,i,o,w);
        if(cur!==getOrig(S,i,o,w)) f[l]=cur;
      }
      if(Object.keys(f).length) edits[String(i)]=f;
    }
    return {format:PATCH_FORMAT,version:PATCH_VERSION,game:SERIAL,note:note||"",edits};
  }

  function patchStats(doc){
    const recs=Object.keys(doc.edits||{}).length;
    let fields=0; for(const v of Object.values(doc.edits||{})) fields+=Object.keys(v).length;
    return {recs,fields};
  }

  async function exportPatch(){
    const doc=buildPatch();
    const {recs,fields}=patchStats(doc);
    if(!recs){ toastFn("Nothing to export — the disc matches retail",true); return; }
    const text=JSON.stringify(doc,null,1)+"\n";
    const name="xenosaga2-enemy-patch.json";
    try{
      if("showSaveFilePicker" in window){
        const h=await window.showSaveFilePicker({suggestedName:name,
          types:[{description:"Xenosaga II enemy patch",accept:{"application/json":[".json"]}}]});
        const w=await h.createWritable(); await w.write(text); await w.close();
      }else{
        const a=document.createElement("a");
        a.href=URL.createObjectURL(new Blob([text],{type:"application/json"}));
        a.download=name; document.body.appendChild(a); a.click(); a.remove();
        setTimeout(()=>URL.revokeObjectURL(a.href),4000);
      }
      toastFn("✓ Exported "+fields+" field(s) across "+recs+" enemy record(s)");
    }catch(e){ if(e&&e.name!=="AbortError") toastFn("✗ "+e,true); }
  }

  // Strict on purpose: this stages writes into a disc image, so an unknown field
  // or an out-of-range record is an error rather than something to skip quietly.
  function applyPatchDoc(doc){
    if(!doc||typeof doc!=="object"||doc.format!==PATCH_FORMAT)
      throw new Error("not a "+PATCH_FORMAT+" file");
    if(doc.version!==PATCH_VERSION)
      throw new Error("patch version "+doc.version+" is not supported (this build reads "+PATCH_VERSION+")");
    const known=new Map(allFields().map(f=>[f[0],f]));
    const entries=Object.entries(doc.edits||{});
    if(!entries.length) throw new Error("patch contains no edits");
    let n=0;
    for(const [key,fields] of entries){
      const i=Number(key);
      if(!Number.isInteger(i)||i<0||i>=COUNT) throw new Error("record "+key+" is out of range");
      if(!fields||typeof fields!=="object") throw new Error("record "+i+": expected a field map");
      for(const [l,v] of Object.entries(fields)){
        const spec=known.get(l);
        if(!spec) throw new Error("record "+i+": unknown field "+l);
        if(!Number.isInteger(v)) throw new Error("record "+i+"."+l+": expected a whole number");
        put(tableOf(l),i,spec[1],spec[2],v); n++;
      }
    }
    return n;
  }

  async function importPatch(file){
    let doc;
    try{ doc=JSON.parse(await file.text()); }
    catch(e){ toastFn("✗ "+file.name+" is not valid JSON",true); return; }
    // apply to a scratch copy first so a bad patch cannot half-stage
    const keepS=S.buf.slice(), keepR=R.buf.slice();
    let n;
    try{ n=applyPatchDoc(doc); }
    catch(e){ S.buf.set(keepS); R.buf.set(keepR); toastFn("✗ "+e.message,true); return; }
    const {recs}=patchStats(doc);
    loadEnemy(); epending();
    toastFn("✓ Staged "+n+" field(s) across "+recs+" record(s)"+
      (doc.note?" — "+doc.note:"")+" · review, then Save to ISO");
  }

  function stageRestore(){
    let n=0;
    for(let i=0;i<COUNT;i++)
      for(const [l,o,w] of SFIELDS.concat(RFIELDS)){
        const van=retail(i,l), T=tableOf(l);
        if(van!==undefined && get(T,i,o,w)!==van){ put(T,i,o,w,van); n++; }
      }
    loadEnemy(); epending();
    toastFn(n?("✓ Staged a restore of "+n+" field(s) to retail — review, then Save to ISO")
             :"Already matches the retail values");
  }

  async function showRetailDiff(){
    let rows="", recs=0, fields=0;
    for(let i=0;i<COUNT;i++){
      let cells="";
      for(const [l,o,w] of SFIELDS.concat(RFIELDS)){
        const van=retail(i,l); if(van===undefined) continue;
        const cur=get(tableOf(l),i,o,w);
        if(cur===van) continue;
        fields++;
        cells+='<div class="revrow"><span class="rl">'+esc(l)+'</span><span class="ro">'+
          van.toLocaleString()+'</span>→ <span class="rn">'+cur.toLocaleString()+'</span></div>';
      }
      if(cells){recs++;
        if(recs<=200) rows+='<div class="revgrp">'+String(i).padStart(3,"0")+' · '+
          esc(cat[i]?cat[i].name:i)+'</div>'+cells;}
    }
    const head=recs
      ? '<div class="note">'+recs+' record(s), '+fields+' field(s) differ from an unmodified disc '+
        '(retail → yours). Affinity slots are not listed — the bestiary has no retail baseline '+
        'for them.</div>'+(recs>200?'<div class="note">…first 200 shown…</div>':'')
      : '<div class="note">Every enemy stat and reward matches the retail values.</div>';
    if(window.openInfo) await window.openInfo("Compared to retail", head+rows);
  }

  async function saveISO(){
    const rows=reviewRows();
    if(window.openReview && !(await window.openReview("Write to ISO — enemy tables", rows, "Apply & write to disc"))) return;
    const st=$("#estat");st.textContent="writing…";st.className="status";$("#esave").disabled=true;
    try{
      if((await handle.queryPermission({mode:"readwrite"}))!=="granted")
        await handle.requestPermission({mode:"readwrite"});
      if($("#ebak").checked && !backedUp){
        st.textContent="backing up (this copies the whole disc)…";
        const src=await handle.getFile();
        const bh=await window.showSaveFilePicker({suggestedName:src.name+".bak"});
        const bw=await bh.createWritable();await bw.write(src);await bw.close();backedUp=true;
      }
      let total=0;
      const w=await handle.createWritable({keepExistingData:true});
      for(const T of [S,R]){
        for(const [s,e] of diffRuns(T)){await w.write({type:"write",position:T.base+s,data:T.buf.slice(s,e)});total++;}
      }
      await w.close();
      S.orig=S.buf.slice();R.orig=R.buf.slice();
      loadEnemy();
      st.textContent="✓ wrote "+total+" run(s) to ISO";st.className="status ok";toastFn("✓ Enemy tables saved to ISO");
    }catch(e){st.textContent="✗ "+e;st.className="status err";toastFn("✗ "+e,true);}
    epending();
  }
})();
