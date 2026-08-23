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
  const CAPS={HP:999999,STR:999,VIT:999,EATK:999,EDEF:999,DEX:255,EVA:255,AGL:255,
              EXP:999999,SP:9999,CP:9999};

  // Battle-pacing profiles — keep in sync with x2fields.PROFILES.
  // Ep. II's stock→break→boost loop is the only efficient way to fight, and the
  // ritual costs turns before it pays out. The mechanics live in code we haven't
  // located, but what makes the loop feel like a tax is tuning we can write:
  // HP = stocked chains per kill, VIT/EDEF = whether off-loop attacks matter,
  // STR/EATK = enemy pressure, SP/CP = how fast the skill system opens up.
  const MAJOR_HP=20000;                       // catalog HP at/above this = "major"
  const PROFILES={
    faster:{label:"Faster fights",
      note:"Keeps the combo loop, cuts the tax: fewer stocked chains per kill and quicker skill unlocks. The safe default.",
      regular:{HP:45,EXP:150,SP:150,CP:150}, major:{HP:70,EXP:150,SP:150,CP:150}},
    freer:{label:"Freer play",
      note:"Makes off-combo attacks viable — softer defenses so unbroken damage lands, on top of a lighter HP cut.",
      regular:{HP:55,VIT:70,EDEF:70,EXP:150,SP:150,CP:150},
      major:{HP:75,VIT:80,EDEF:80,EXP:150,SP:150,CP:150}},
    deeper:{label:"Deeper challenge",
      note:"For players who like the loop: enemies hit harder and last longer, but pay out much more.",
      regular:{HP:110,STR:115,EATK:115,EXP:200,SP:200,CP:200},
      major:{HP:130,STR:115,EATK:115,EXP:200,SP:200,CP:200}},
    grindcut:{label:"Reward-only",
      note:"Leaves every fight exactly as designed and only removes the grind between them.",
      regular:{EXP:250,SP:250,CP:250}, major:{EXP:250,SP:250,CP:250}},
  };
  // Placeholder/debug rows (13 of them: GNO013, CRE006/018, UMA013, MON001-4,
  // BOS026-29, and unused rows
  // carrying a token EXP with no SP/CP) are never scaled — mirrors is_dummy_record().
  const isDummy=(r)=>!!r&&(/^[A-Z]{3}\d{3}$/.test(String(r.name||"").trim())||
                           (r.exp>0&&r.exp<100&&!r.sp&&!r.cp));

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
      '<div class="card"><h2>3 · Battle pacing (all '+COUNT+' enemies)</h2>'+
      '<p class="sub" style="margin:0 0 10px">The stock→break→boost loop is the only efficient way to '+
      'fight, and bloated HP makes you run the whole ritual for every enemy. These profiles retune what '+
      'the loop <i>costs</i>: HP sets how many stocked chains a kill takes, VIT/EDEF whether off-loop '+
      'attacks land at all, and SP/CP how fast the skill system opens up.</p>'+
      '<div class="toolbar" id="profRow">'+
      Object.keys(PROFILES).map(k=>'<button class="btn prof" data-p="'+k+'" title="'+
        esc(PROFILES[k].note)+'">'+esc(PROFILES[k].label)+'</button>').join(" ")+'</div>'+
      '<p class="note" id="profNote">Pick a profile to load its numbers below, then stage it. '+
      '“Major” means a record whose retail HP is '+MAJOR_HP.toLocaleString()+'+ — the only boss signal '+
      'the disc actually gives us. Debug/unused records are never touched.</p>'+
      '<table class="scl"><tbody>'+
      '<tr><td></td><th>HP</th><th>VIT/EDEF</th><th>STR/EATK</th><th>EXP/SP/CP</th></tr>'+
      '<tr><th>regular</th>'+
      '<td><input type="number" id="rHP" value="100" min="1" max="1000" style="width:7ch">%</td>'+
      '<td><input type="number" id="rDEF" value="100" min="1" max="1000" style="width:7ch">%</td>'+
      '<td><input type="number" id="rATK" value="100" min="1" max="1000" style="width:7ch">%</td>'+
      '<td><input type="number" id="rRW" value="100" min="1" max="1000" style="width:7ch">%</td></tr>'+
      '<tr><th>major</th>'+
      '<td><input type="number" id="mHP" value="100" min="1" max="1000" style="width:7ch">%</td>'+
      '<td><input type="number" id="mDEF" value="100" min="1" max="1000" style="width:7ch">%</td>'+
      '<td><input type="number" id="mATK" value="100" min="1" max="1000" style="width:7ch">%</td>'+
      '<td><input type="number" id="mRW" value="100" min="1" max="1000" style="width:7ch">%</td></tr>'+
      '</tbody></table>'+
      '<div class="toolbar"><span id="sclWarn" class="status"></span><span style="flex:1"></span>'+
      '<button id="sclApply" class="btn primary">Stage rebalance</button></div>'+
      '<p class="note">Staged into the same pending-changes set above — review everything before writing. '+
      'Scaling always starts from the values the disc had when it was opened, so re-staging replaces the '+
      'previous plan instead of compounding it. Values round to whole numbers; HP floors at 1.</p></div>';
    $("#esel").onchange=loadEnemy;
    $("#erev").onclick=()=>{S.buf.set(S.orig);R.buf.set(R.orig);loadEnemy();epending();};
    $("#esave").onclick=saveISO;
    $("#sclApply").onclick=()=>stageRebalance(readScales());
    document.querySelectorAll("#profRow .prof").forEach(b=>b.onclick=()=>applyProfile(b.dataset.p));
    checkPristine();
    loadEnemy();
  }

  // Warn if the disc no longer matches the verified retail tables. Stats *and*
  // rewards, both: a reward-only profile leaves every stat byte untouched, so a
  // stats-only check would miss it and the multipliers would quietly stack.
  function checkPristine(){
    const w=$("#sclWarn"); if(!w) return;
    let clean=true;
    for(let i=0;i<COUNT&&clean;i++){
      const r=cat[i]; if(!r) continue;
      clean = getOrig(S,i,0x36,4)===r.hp && getOrig(S,i,0x3C,2)===r.str &&
              getOrig(R,i,0x00,4)===r.exp && getOrig(R,i,0x04,2)===r.sp &&
              getOrig(R,i,0x06,2)===r.cp;
    }
    w.textContent=clean?"":"! this disc was already rebalanced — staging again scales the "+
      "already-scaled values";
    w.className=clean?"status":"status err";
  }

  const PCT=(id)=>Math.max(1,+$("#"+id).value||100);
  function readScales(){
    const g=(hp,def,atk,rw)=>({HP:PCT(hp),VIT:PCT(def),EDEF:PCT(def),STR:PCT(atk),
                               EATK:PCT(atk),EXP:PCT(rw),SP:PCT(rw),CP:PCT(rw)});
    return {regular:g("rHP","rDEF","rATK","rRW"), major:g("mHP","mDEF","mATK","mRW")};
  }
  function applyProfile(key){
    const p=PROFILES[key]; if(!p) return;
    const set=(id,v)=>{$("#"+id).value=v==null?100:v;};
    for(const [grp,pre] of [["regular","r"],["major","m"]]){
      const s=p[grp]||{};
      set(pre+"HP",s.HP); set(pre+"DEF",s.VIT); set(pre+"ATK",s.STR); set(pre+"RW",s.EXP);
    }
    document.querySelectorAll("#profRow .prof").forEach(b=>b.classList.toggle("on",b.dataset.p===key));
    $("#profNote").textContent=p.label+" — "+p.note;
    toastFn("Loaded “"+p.label+"” — review the numbers, then Stage rebalance");
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

  // Scale every record per its group. Always reads from `orig` (the disc as
  // opened) so re-staging replaces the plan rather than compounding it.
  function stageRebalance(scales){
    let n=0, skipped=0;
    for(let i=0;i<COUNT;i++){
      const rec=cat[i];
      if(isDummy(rec)){ skipped++; continue; }
      const hp=rec&&rec.hp!=null?rec.hp:getOrig(S,i,0x36,4);
      const s=scales[hp>=MAJOR_HP?"major":"regular"]||{};
      let touched=false;
      for(const [lbl,pct] of Object.entries(s)){
        const spec=specOf(lbl); if(!spec) continue;
        const [,off,w]=spec, T=tableOf(lbl), old=getOrig(T,i,off,w);
        if(old===0) continue;                    // 0 means "none" — never scale it up
        const val=Math.min(Math.max(1,Math.round(old*pct/100)),CAPS[lbl]||0xFFFFFFFF);
        put(T,i,off,w,val);                      // write even at 100% — that restores
        if(val!==old) touched=true;              // the disc value, replacing a prior stage
      }
      if(touched) n++;
    }
    loadEnemy();epending();
    toastFn(n?("✓ Staged for "+n+" record(s)"+(skipped?" ("+skipped+" debug records skipped)":"")+
               " — review & Save to ISO"):"No changes to stage");
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
