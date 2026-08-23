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
  let TABLES=null;

  async function loadTables(){
    if(TABLES) return TABLES;
    const r=await fetch("tables.json",{cache:"no-cache"});
    if(!r.ok) throw new Error("tables.json ("+r.status+")");
    const t=await r.json();
    SBASE=t.enemy.base; STRIDE=t.enemy.stride; COUNT=t.enemy.count;
    SFIELDS=t.enemy.fields; ID_OFF=t.enemy.idOff;
    RBASE=t.reward.base; RSTRIDE=t.reward.stride; RFIELDS=t.reward.fields;
    SEND=SBASE+COUNT*STRIDE; REND=RBASE+COUNT*RSTRIDE;
    BOSS_ID_MIN=t.bossIdMin;
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
  const tableOf=(f)=>SFIELDS.some(x=>x[0]===f)?S:R;
  const specOf=(f)=>SFIELDS.concat(RFIELDS).find(x=>x[0]===f);

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
      '<p class="note">Xenosaga II (USA) <b>Disc 1</b> — SLUS-20892. Enemy edits apply to a new game. '+
      'Edits write in place; work on a copy or tick backup.</p></div>'+
      '<div id="isoEdit"></div>';
    $("#isoPick").onclick=openISO;
  };

  async function openISO(){
    try{ [handle]=await window.showOpenFilePicker(); }catch(e){ return; }
    const f=await handle.getFile();
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
    renderEnemy();
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
      '<label style="margin-left:10px"><input type="checkbox" id="sclBoss"> bosses too (IDs '+BOSS_ID_MIN+'+)</label>'+
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
    $("#erow").innerHTML=SFIELDS.map(([l,o,w])=>cellHtml(l,o,w,get(S,i,o,w),getOrig(S,i,o,w))).join("");
    $("#erow2").innerHTML='<td><div class="fl">rewards</div></td>'+
      RFIELDS.map(([l,o,w])=>cellHtml(l,o,w,get(R,i,o,w),getOrig(R,i,o,w))).join("")+
      '<td colspan="4"><div class="fl">enemy id</div><span class="muted small">'+get(S,i,ID_OFF,2)+'</span></td>';
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
