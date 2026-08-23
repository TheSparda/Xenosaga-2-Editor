// Xenosaga II ISO Editor (web) — enemy stats + rewards, VERIFIED tables.
// Never loads the 4.6 GB disc: reads two small ranged slices (stat table ~7 KB,
// rewards table ~2 KB), edits in memory, and writes only the changed byte-runs
// back in place via the File System Access API (desktop Chromium).
// Table derivation: Editor/Xenosaga2_ISO_offsets.md (74/76 exact guide matches).
(function(){
  const FS = "showOpenFilePicker" in window;
  // verified tables (disc 1) — keep in sync with x2fields
  const SBASE=0x1FFF5F0, STRIDE=0x5C, COUNT=125;          // stat records
  const RBASE=0x201094C, RSTRIDE=0x10;                    // rewards rows
  const SFIELDS=[["HP",0x36,4],["STR",0x3C,2],["VIT",0x3E,2],["EATK",0x40,2],
                 ["EDEF",0x42,2],["DEX",0x44,1],["EVA",0x45,1],["AGL",0x46,1]];
  const RFIELDS=[["EXP",0x00,4],["SP",0x04,2],["CP",0x06,2]];
  const SEND=SBASE+COUNT*STRIDE, REND=RBASE+COUNT*RSTRIDE;

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
  const tableOf=(f)=>SFIELDS.some(x=>x[0]===f)?S:R;
  const specOf=(f)=>SFIELDS.concat(RFIELDS).find(x=>x[0]===f);

  async function loadCat(){ if(cat)return cat;
    try{cat=await (await fetch("../Editor/x2_enemies.json")).json();}catch(e){cat={};} return cat; }

  window.initISO = async function(){
    const root=$("#isoRoot"); if(root.dataset.init) return; root.dataset.init="1";
    if(!FS){ root.innerHTML='<div class="card blocked"><b>ISO editing needs desktop Chrome / Edge / Brave / Opera</b>'+
      ' (File System Access API). The Save editor works everywhere, including mobile.</div>'; return; }
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
    // sanity anchor: Perun (rec 6) HP must be 22400 in an unmodified disc — warn if not
    const perun=get(S,6,0x36,4);
    st.textContent="✓ Disc 1 loaded ("+f.name+")"+(perun!==22400?" — note: Perun HP reads "+perun+" (modified disc?)":"");
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
      '<p class="note">Stats + battle rewards, verified against guide data (74/76 exact matches). '+
      'Writes only the changed bytes back at their exact offsets.</p></div>'+
      '<div class="card"><h2>3 · Rebalance (all '+COUNT+' enemies)</h2>'+
      '<p class="sub" style="margin:0 0 10px">The community\'s #1 complaint is bloated enemy HP. '+
      'Scale it globally — 50% halves every enemy\'s HP; rewards can be scaled up to keep pace.</p>'+
      '<div class="toolbar">'+
      '<label>HP</label> <input type="number" id="sclHP" value="100" min="1" max="1000" style="width:8ch">%'+
      '<label style="margin-left:10px">EXP/SP/CP</label> <input type="number" id="sclRW" value="100" min="1" max="1000" style="width:8ch">%'+
      '<label style="margin-left:10px"><input type="checkbox" id="sclBoss"> bosses too (IDs 561+)</label>'+
      '<span style="flex:1"></span>'+
      '<button id="sclApply" class="btn primary">Stage rebalance</button></div>'+
      '<p class="note">Staged into the same pending-changes set above — review everything before writing. '+
      'Values round to whole numbers; HP floors at 1.</p></div>';
    $("#esel").onchange=loadEnemy;
    $("#erev").onclick=()=>{S.buf.set(S.orig);R.buf.set(R.orig);loadEnemy();epending();};
    $("#esave").onclick=saveISO;
    $("#sclApply").onclick=stageRebalance;
    loadEnemy();
  }

  function cellHtml(lbl,off,w,val){
    return '<td><div class="fl">'+lbl+'</div><span><input type="number" min="0" autocomplete="off" '+
      'data-f="'+lbl+'" data-o="'+off+'" data-w="'+w+'" data-def="'+val+'" value="'+val+'"></span></td>';
  }
  function loadEnemy(){
    const i=+$("#esel").value;
    $("#erow").innerHTML=SFIELDS.map(([l,o,w])=>cellHtml(l,o,w,get(S,i,o,w))).join("");
    $("#erow2").innerHTML='<td><div class="fl">rewards</div></td>'+
      RFIELDS.map(([l,o,w])=>cellHtml(l,o,w,get(R,i,o,w))).join("")+
      '<td colspan="4"><div class="fl">enemy id</div><span class="muted small">'+(cat[i]?cat[i].id:"?")+'</span></td>';
    document.querySelectorAll("#erow input, #erow2 input").forEach(inp=>{
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
    let n=0;
    for(let i=0;i<COUNT;i++){
      const id=cat[i]?cat[i].id:0;
      if(!bosses && id>=561) continue;          // fields+grunts only unless opted in
      if(hpP!==1){const hp=getOrig(S,i,0x36,4);put(S,i,0x36,4,Math.max(1,Math.round(hp*hpP)));n++;}
      if(rwP!==1){
        put(R,i,0x00,4,Math.round(getOrig(R,i,0x00,4)*rwP));
        put(R,i,0x04,2,Math.round(getOrig(R,i,0x04,2)*rwP));
        put(R,i,0x06,2,Math.round(getOrig(R,i,0x06,2)*rwP));n++;
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
      for(const [T,FL] of [[S,SFIELDS],[R,RFIELDS]]){
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
