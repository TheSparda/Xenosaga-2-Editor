// Xenosaga II ISO Editor (web) — enemy stats + rewards, VERIFIED tables.
// Never loads the 4.6 GB disc: reads two small ranged slices (stat table ~7 KB,
// rewards table ~2 KB), edits in memory, and writes only the changed byte-runs
// back in place via the File System Access API (desktop Chromium).
// Table derivation: Editor/Xenosaga2_ISO_offsets.md (74/76 exact guide matches).
(function(){
  const FS = "showOpenFilePicker" in window;
  // Everything below comes from tables.json, generated from Editor/x2fields.py —
  // byte offsets we write into a 4.6 GB image, the per-field caps, and the
  // battle-pacing profiles. None of it is duplicated here: a second copy is a
  // data-loss bug (or a silently-diverging profile) waiting to happen, and CI
  // fails if the generated file drifts. If the fetch fails we refuse to open a
  // disc rather than fall back to a possibly-stale copy.
  let SBASE, STRIDE, COUNT, RBASE, RSTRIDE, SFIELDS, RFIELDS, SEND, REND, BOSS_ID_MIN, ID_OFF;
  let AFIELDS, AFF_NORMAL, CATKEYS, SERIAL, CAPS, PROFILES, MAJOR_HP;
  // break/zone data: a hittable-zone mask and BRK_SLOTS one-hot sequence slots
  let ZMASK_OFF, BRK_OFF, BRK_SLOTS, ZBITS, ZSYM;
  let TABLES=null;
  // Both retail discs carry the enemy tables and disc 2's copy sits 0x800 lower,
  // so the bases can't be fixed at load time — they're resolved from the serial
  // once a disc is open (setDisc). Editing only disc 1 leaves the second half of
  // the game on retail values, so the UI has to say so.
  let ETABLES=null, SERIALMAP=null, DISC=1;
  // the dotted form as it appears in SYSTEM.CNF, for the byte scan
  const SERIAL_ASCII={1:"SLUS_208.92", 2:"SLUS_211.33"};
  // Patch files are interchangeable with `x2patch.py export-patch/apply-patch`.
  const PATCH_FORMAT="x2-enemy-patch", PATCH_VERSION=1;

  async function loadTables(){
    if(TABLES) return TABLES;
    const r=await fetch("tables.json",{cache:"no-cache"});
    if(!r.ok) throw new Error("tables.json ("+r.status+")");
    const t=await r.json();
    STRIDE=t.enemy.stride; COUNT=t.enemy.count;
    SFIELDS=t.enemy.fields; ID_OFF=t.enemy.idOff;
    AFIELDS=t.enemy.affinityFields||[]; AFF_NORMAL=t.enemy.affinityNormal;
    ZMASK_OFF=t.enemy.zoneMaskOff; BRK_OFF=t.enemy.breakSeqOff;
    BRK_SLOTS=t.enemy.breakSeqSlots||4; ZBITS=t.enemy.zoneBits||{A:1,B:2,C:4};
    ZSYM={}; for(const k in ZBITS) ZSYM[ZBITS[k]]=k;
    RSTRIDE=t.reward.stride; RFIELDS=t.reward.fields;
    BOSS_ID_MIN=t.bossIdMin; CATKEYS=t.catalogKeys||{};
    CAPS=t.fieldCaps||{}; PROFILES=t.profiles||{}; MAJOR_HP=t.majorHpThreshold;
    ETABLES=t.enemyTables||{"1":{stats:t.enemy.base,rewards:t.reward.base}};
    SERIALMAP=t.serials||{};
    setDisc(1);
    return (TABLES=t);
  }

  // Point every offset at one disc's tables. Called once per opened disc, before
  // any slice is read, so S/R can never be filled from the wrong base.
  function setDisc(n){
    const e=ETABLES[String(n)]; if(!e) throw new Error("no tables for disc "+n);
    DISC=n; SBASE=e.stats; RBASE=e.rewards;
    SEND=SBASE+COUNT*STRIDE; REND=RBASE+COUNT*RSTRIDE;
    SERIAL=Object.keys(SERIALMAP||{}).find(k=>SERIALMAP[k]===n)||"SLUS-20892";
  }

  // Placeholder/debug rows (13 of them: GNO013, CRE006/018, UMA013, MON001-4,
  // BOS026-29, and unused rows carrying a token EXP with no SP/CP) are never
  // scaled — mirrors x2fields.is_dummy_record().
  const isDummy=(r)=>!!r&&(/^[A-Z]{3}\d{3}$/.test(String(r.name||"").trim())||
                           (r.exp>0&&r.exp<100&&!r.sp&&!r.cp));

  let handle=null, cat=null, backedUp=false;
  // two independent slices: {buf, orig, dv, base}
  let S=null, R=null;
  const $=(s,r=document)=>r.querySelector(s);
  const esc=(s)=>String(s).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const toastFn=(m,e)=>{try{window.toast&&window.toast(m,e);}catch(_){}};

  // ---- break sequence: BRK_SLOTS one-hot bytes, 0 = end of sequence ----
  // Mirrors x2fields.decode_break_seq / encode_break_seq.
  function breakSeq(i){
    let s="";
    for(let n=0;n<BRK_SLOTS;n++){
      const sym=ZSYM[get(S,i,BRK_OFF+n,1)];
      if(!sym) break;
      s+=sym;
    }
    return s;
  }
  function setBreakSeq(i,text){
    const syms=String(text).toUpperCase().split("").filter(c=>c in ZBITS).slice(0,BRK_SLOTS);
    for(let n=0;n<BRK_SLOTS;n++) put(S,i,BRK_OFF+n,1, n<syms.length?ZBITS[syms[n]]:0);
    return syms.join("");
  }
  const zoneMaskText=(m)=>Object.keys(ZBITS).filter(k=>m&ZBITS[k]).join("");

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
    root.innerHTML='<div class="card"><h2>1 · Open a disc ISO</h2>'+
      '<button id="isoPick" class="btn primary">Choose ISO…</button> '+
      '<span id="isoStatus" class="status"></span>'+
      '<div id="isoRecent"></div><div id="isoPair"></div>'+
      '<p class="note">Xenosaga II (USA) — <b>either disc</b> (SLUS-20892 or SLUS-21133). '+
      'Both discs carry the same enemy tables, so <b>apply the same edits to both</b> or '+
      'enemies revert to retail values after the disc swap. Enemy edits apply to a new game. '+
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
    // Both discs are editable, each from its own bases. Resolve which one this
    // is BEFORE slicing, or the edits land at the other disc's offsets.
    const disc = asc(SERIAL_ASCII[1])>=0 ? 1 : asc(SERIAL_ASCII[2])>=0 ? 2 : 0;
    if(!disc){ st.textContent="✗ Unrecognized Xenosaga II serial."; st.className="status err"; return; }
    try{ setDisc(disc); }
    catch(e){ st.textContent="✗ No table offsets known for disc "+disc+"."; st.className="status err"; return; }
    const sb=new Uint8Array(await f.slice(SBASE,SEND).arrayBuffer());
    const rb=new Uint8Array(await f.slice(RBASE,REND).arrayBuffer());
    S={buf:sb,orig:sb.slice(),dv:new DataView(sb.buffer),base:SBASE};
    R={buf:rb,orig:rb.slice(),dv:new DataView(rb.buffer),base:RBASE};
    backedUp=false;
    // sanity anchor: Perun (rec 6) HP must match the bestiary on an unmodified disc
    const [,hpO,hpW]=SFIELDS.find(f=>f[0]==="HP");
    const perun=get(S,6,hpO,hpW), want=retail(6,"HP");
    st.textContent="✓ Disc "+DISC+" loaded ("+f.name+")"+
      (want!==undefined&&perun!==want?" — note: "+esc(cat[6]?cat[6].name:"record 6")+" HP reads "+
        perun.toLocaleString()+" not "+want.toLocaleString()+" (modified disc?)":"");
    st.className="status ok";
    showPairNote();
    rememberIso(f.name||("disc"+DISC+".iso"),handle);   // one-tap reopen next visit
    renderEnemy();
    showLastIso();
  }

  // A rebalance applied to one disc only is the easiest way to get a half-modded
  // playthrough, and nothing in the game warns you. Say it on the disc that's open.
  function showPairNote(){
    const el=$("#isoPair"); if(!el) return;
    const other=DISC===1?2:1;
    el.innerHTML='<p class="note warn">Editing <b>disc '+DISC+'</b>. Disc '+other+
      ' holds its own copy of these tables — apply the same changes there too, or the '+
      'second half of the game keeps retail values. A patch file (below) replays the '+
      'exact same edits onto the other disc.</p>';
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
      '<div class="brkbox"><div class="fl">Break sequence</div>'+
        '<input id="ebrk" type="text" maxlength="'+BRK_SLOTS+'" spellcheck="false" '+
          'autocapitalize="characters" placeholder="e.g. CBB" style="width:8ch;text-transform:uppercase">'+
        '<button type="button" class="restore" id="ebrkrev" title="Restore">↺</button>'+
        '<span id="ebrkinfo" class="muted small"></span>'+
        '<p class="note">The zones you must hit, <b>in order</b>, to Break this enemy — the combo '+
        'loop’s actual gate. Zones are attack heights: <b>A</b> above 3&nbsp;m (○), '+
        '<b>B</b> 1–3&nbsp;m (□), <b>C</b> below 1&nbsp;m (△). Up to '+BRK_SLOTS+' hits; '+
        'clear it to make the enemy unbreakable. Shortening a boss’s 4-hit sequence is the '+
        'single biggest cut to how long its fight drags.</p></div>'+
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
      'previous plan instead of compounding it. Values round to whole numbers; HP floors at 1.</p></div>'+
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
    $("#sclApply").onclick=()=>stageRebalance(readScales());
    document.querySelectorAll("#profRow .prof").forEach(b=>b.onclick=()=>applyProfile(b.dataset.p));
    $("#pExport").onclick=exportPatch;
    $("#pImport").onclick=()=>$("#pFile").click();
    $("#pFile").onchange=e=>{const f=e.target.files[0]; e.target.value=""; if(f)importPatch(f);};
    $("#pDiff").onclick=showRetailDiff;
    $("#pRestore").onclick=stageRestore;
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
      if(!cat[i]) continue;
      for(const [l,o,wd] of SFIELDS.concat(RFIELDS)){
        const want=retail(i,l);
        if(want!==undefined && getOrig(tableOf(l),i,o,wd)!==want){ clean=false; break; }
      }
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
    wireBreak(i);
    epending();
  }

  // The break sequence is one text box over BRK_SLOTS bytes, so it can't use the
  // generic per-field cell wiring above.
  function wireBreak(i){
    const inp=$("#ebrk"), rev=$("#ebrkrev"), info=$("#ebrkinfo");
    if(!inp) return;
    // "as opened" value, so ↺ and the amber marker mean the same as elsewhere
    let def="";
    for(let n=0;n<BRK_SLOTS;n++){
      const sym=ZSYM[getOrig(S,i,BRK_OFF+n,1)];
      if(!sym) break;
      def+=sym;
    }
    inp.value=breakSeq(i);
    const paint=()=>{
      const cur=breakSeq(i), mask=get(S,i,ZMASK_OFF,1);
      const zones=zoneMaskText(mask);
      // a sequence can only use zones the enemy actually has
      const bad=cur.split("").filter(c=>!(mask&ZBITS[c]));
      info.textContent = (cur?cur.split("").join("→"):"cannot be broken")+
        "   ·   zones on this enemy: "+(zones||"none")+
        (bad.length?"   ⚠ uses zone "+[...new Set(bad)].join("/")+" which this enemy doesn't have":"");
      info.className="muted small"+(bad.length?" warntext":"");
      const ch=cur!==def;
      inp.classList.toggle("changed",ch); rev.classList.toggle("show",ch);
    };
    inp.oninput=()=>{
      const cleaned=setBreakSeq(i,inp.value);
      if(inp.value!==cleaned) inp.value=cleaned;   // drop anything that isn't a zone
      paint(); epending();
    };
    rev.onclick=()=>{ inp.value=setBreakSeq(i,def); paint(); epending(); };
    paint();
  }

  // Scale every record per its group. Always reads from `orig` (the disc as
  // opened) so re-staging replaces the plan rather than compounding it.
  function stageRebalance(scales){
    const hpSpec=specOf("HP");
    let n=0, skipped=0;
    for(let i=0;i<COUNT;i++){
      const rec=cat[i];
      if(isDummy(rec)){ skipped++; continue; }
      // group on retail HP where we have it, else the disc's own value
      const hp=rec&&rec.hp!=null?rec.hp:getOrig(S,i,hpSpec[1],hpSpec[2]);
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
