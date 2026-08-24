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
  let STRIDE, COUNT, TAIL, RSTRIDE, SFIELDS, RFIELDS, BOSS_ID_MIN, ID_OFF;
  let AFIELDS, AFF_NORMAL, AFF_SCALE, AFF_ELEMENTS, CATKEYS, CAPS, PROFILES, MAJOR_HP;
  // break/zone data: a hittable-zone mask and BRK_SLOTS one-hot sequence slots
  let ZMASK_OFF, BRK_OFF, BRK_SLOTS, ZBITS, ZSYM, ZFIELDS;
  // verified battle flags: enemy type (+0x50 bits 0-1) and zone targeting off
  // (+0x51 bit 3). The second decides breakability along with the sequence.
  let GFIELDS, TYPE_OFF, TYPE_MASK, TYPE_NAMES, NOZONE_OFF, NOZONE_BIT;
  // item drops share the rewards row: rate, category and 1-based id per slot
  let DFIELDS, DROPCATS, DROP_CONSUMABLE, DROPBASE, RFIELDS_RES, ITEMS=null;
  // Ether + Double skill numeric records: two disjoint blocks per disc, read as
  // one span so a single buffer covers both (x2fields.skill_span()).
  let KFIELDS, KSTRIDE, KBLOCKS, KSPAN, KELEM, SKILLS=null;
  let TABLES=null;
  // Both retail discs carry the enemy tables, disc 2's copy 0x800 lower, so no
  // base can be fixed at load time — each opened disc carries its own (see DISCS).
  let ETABLES=null, SERIALMAP=null;
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
    // The affinity (+0x58) and status-resistance (+0x6C) blocks overhang the
    // nominal record, so the LAST record's fields live past count*stride. Read
    // the extra bytes or record 124 comes back undefined and renders as
    // blank-and-modified. x2fields.enemy_record_tail() is the source of truth.
    TAIL=t.enemy.recordTail||0;
    SFIELDS=t.enemy.fields; ID_OFF=t.enemy.idOff;
    AFIELDS=t.enemy.affinityFields||[]; AFF_NORMAL=t.enemy.affinityNormal;
    AFF_SCALE=t.enemy.affinityScale||5; AFF_ELEMENTS=t.enemy.affinityElements||[];
    ZFIELDS=t.enemy.zoneFields||[];
    GFIELDS=t.enemy.flagFields||[]; TYPE_OFF=t.enemy.typeOff; TYPE_MASK=t.enemy.typeMask;
    TYPE_NAMES=t.enemy.typeNames||{}; NOZONE_OFF=t.enemy.noZoneOff; NOZONE_BIT=t.enemy.noZoneBit;
    ZMASK_OFF=t.enemy.zoneMaskOff; BRK_OFF=t.enemy.breakSeqOff;
    BRK_SLOTS=t.enemy.breakSeqSlots||4; ZBITS=t.enemy.zoneBits||{A:1,B:2,C:4};
    ZSYM={}; for(const k in ZBITS) ZSYM[ZBITS[k]]=k;
    RSTRIDE=t.reward.stride; RFIELDS=t.reward.fields;
    DFIELDS=t.reward.dropFields||[]; DROPCATS=t.reward.dropCatNames||{};
    DROP_CONSUMABLE=t.reward.dropCatConsumable; DROPBASE=t.reward.dropCatBase||{};
    RFIELDS_RES=t.enemy.statusResFields||[];
    KFIELDS=(t.skill||{}).fields||[]; KSTRIDE=(t.skill||{}).stride||32;
    KBLOCKS=(t.skill||{}).blocks||{}; KSPAN=(t.skill||{}).span||0;
    KELEM=(t.skill||{}).elementBits||{};
    BOSS_ID_MIN=t.bossIdMin; CATKEYS=t.catalogKeys||{};
    CAPS=t.fieldCaps||{}; PROFILES=t.profiles||{}; MAJOR_HP=t.majorHpThreshold;
    ETABLES=t.enemyTables||{"1":{stats:t.enemy.base,rewards:t.reward.base}};
    SERIALMAP=t.serials||{};
    return (TABLES=t);
  }

  // The serial stamped into an exported patch — the disc the values came from.
  const serialOf=(n)=>Object.keys(SERIALMAP||{}).find(k=>SERIALMAP[k]===n)||"SLUS-20892";

  // Placeholder/debug rows (13 of them: GNO013, CRE006/018, UMA013, MON001-4,
  // BOS026-29, and unused rows carrying a token EXP with no SP/CP) are never
  // scaled — mirrors x2fields.is_dummy_record().
  const isDummy=(r)=>!!r&&(/^[A-Z]{3}\d{3}$/.test(String(r.name||"").trim())||
                           (r.exp>0&&r.exp<100&&!r.sp&&!r.cp));

  let cat=null;
  // ---- multi-disc model -------------------------------------------------
  // Both retail discs hold byte-identical enemy tables at different bases, so
  // there is exactly ONE set of values to edit. Rather than keep two buffers in
  // step, we keep one edit buffer (S/R) and mirror it into each targeted disc at
  // that disc's own bases when saving. Sync is then true by construction — there
  // is no code path that can write different values to the two discs.
  //
  // DISCS[n] = {handle, name, sBase, rBase, backedUp}
  // PRIMARY  = the disc whose values were loaded into S/R
  // TARGET   = 'both' | 1 | 2 — which discs a Save actually writes
  const DISCS={};
  let PRIMARY=null, TARGET='both';
  // three independent slices of the disc, each {buf, orig, dv}: enemy stats,
  // enemy rewards, and the skill numeric blocks
  let S=null, R=null, K=null;
  const loadedDiscs=()=>Object.keys(DISCS).map(Number).sort();
  const targetDiscs=()=>loadedDiscs().filter(n=>TARGET==='both'||TARGET===n);
  const $=(s,r=document)=>r.querySelector(s);
  const esc=(s)=>String(s).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const toastFn=(m,e)=>{try{window.toast&&window.toast(m,e);}catch(_){}};

  // ---- break sequence: BRK_SLOTS one-hot bytes, 0 = end of sequence ----
  // Mirrors x2fields.decode_break_seq / encode_break_seq.
  // Verified 57/57 against the guide on both discs: an enemy is unbreakable when
  // zone targeting is off OR it has no sequence. 15 records carry a hittable
  // `BB` whose bytes are inert because the bit is set — trimming those changes
  // nothing the game reads.
  const noZone=(i)=>!!(get(S,i,NOZONE_OFF,1) & NOZONE_BIT);
  const enemyType=(i)=>TYPE_NAMES[String(get(S,i,TYPE_OFF,1) & TYPE_MASK)] ||
                       ("type "+(get(S,i,TYPE_OFF,1)&TYPE_MASK));
  const canBreak=(i)=>!noZone(i) && !!breakSeq(i);

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
  // Skills are addressed by absolute offset inside their buffer, not index*stride:
  // the two blocks are disjoint with a gap between them, so index arithmetic off a
  // single base would silently address the gap.
  function getAt(T,a,w){
    return w===4?T.dv.getUint32(a,true):w===2?T.dv.getUint16(a,true):T.buf[a];}
  function putAt(T,a,w,v){
    const max=w===4?0xFFFFFFFF:w===2?0xFFFF:0xFF;
    v=Math.max(0,Math.min(Math.round(+v||0),max));
    if(w===4)T.dv.setUint32(a,v,true);else if(w===2)T.dv.setUint16(a,v,true);else T.buf[a]=v;}
  function getOrigAt(T,a,w){
    const d=new DataView(T.orig.buffer);
    return w===4?d.getUint32(a,true):w===2?d.getUint16(a,true):T.orig[a];}

  // stats and affinities live in the stat record; rewards are their own table
  const tableOf=(f)=>(RFIELDS.some(x=>x[0]===f)||DFIELDS.some(x=>x[0]===f))?R:S;
  // ZFIELDS (Zones, Brk1..Brk4) must be in here or a JSON import silently drops
  // break-sequence edits — specOf() would return undefined and the write is skipped
  const allFields=()=>SFIELDS.concat(AFIELDS,RFIELDS_RES,ZFIELDS,GFIELDS,RFIELDS,DFIELDS);
  const specOf=(f)=>allFields().find(x=>x[0]===f);
  // retail value of one field, from the verified bestiary (affinities have none)
  const retail=(i,label)=>{
    const key=CATKEYS[label];
    return (key && cat[i] && cat[i][key]!==undefined) ? cat[i][key] : undefined;
  };

  // Every field the editor can write, compared against the retail baseline —
  // one helper, because the per-enemy line and the Compare dialog kept drifting
  // apart. It used to be SFIELDS+RFIELDS only, so break sequences, zones,
  // affinities, resistances and drops were silently exempt: shorten every boss's
  // break and the editor still reported the disc matched retail.
  //
  // Break slots are collapsed into one "Break" row and shown as zone letters,
  // because "Brk3 2 → 0" is not a thing anyone can read.
  const BRK_LABELS=()=>Array.from({length:BRK_SLOTS},(_,n)=>"Brk"+(n+1));
  function retailBreak(i){
    let s="";
    for(const l of BRK_LABELS()){
      const v=retail(i,l); if(v===undefined) return undefined;
      const sym=ZSYM[v]; if(!sym) break;
      s+=sym;
    }
    return s;
  }
  const dash=(s)=>s===""?"—":s;
  function retailDiffs(i){
    const out=[];
    const van=retailBreak(i), cur=breakSeq(i);
    if(van!==undefined && van!==cur)
      out.push({label:"Break", van:dash(van), cur:dash(cur)});
    const zs=ZFIELDS.find(x=>x[0]==="Zones");
    if(zs){
      const v=retail(i,"Zones");
      if(v!==undefined && v!==get(S,i,zs[1],zs[2]))
        out.push({label:"Zones", van:dash(zoneMaskText(v)),
                  cur:dash(zoneMaskText(get(S,i,zs[1],zs[2])))});
    }
    for(const [l,o,w] of SFIELDS.concat(AFIELDS,RFIELDS_RES,RFIELDS,DFIELDS)){
      const v=retail(i,l); if(v===undefined) continue;
      const c=get(tableOf(l),i,o,w); if(c===v) continue;
      const aff=AFIELDS.some(x=>x[0]===l);
      out.push({label:l, van:(aff?affPct(v):v).toLocaleString()+(aff?"%":""),
                cur:(aff?affPct(c):c).toLocaleString()+(aff?"%":"")});
    }
    return out;
  }

  async function loadCat(){ if(cat)return cat;
    try{cat=await (await fetch("../Editor/x2_enemies.json")).json();}catch(e){cat={};}
    // the disc's unified item table — both drop categories index it, each from
    // its own base (see x2fields.DROP_CAT_BASE)
    try{ITEMS=await (await fetch("../Editor/x2_items.json")).json();}catch(e){ITEMS={};}
    // names, descriptions and the retail numerics for the skill panel
    try{SKILLS=await (await fetch("../Editor/x2_skills.json")).json();}catch(e){SKILLS={};}
    return cat; }

  // ---- skills -------------------------------------------------------------
  // A skill is addressed by its TEXT index, which is what the catalog is keyed
  // by; only indices inside a verified block have a numeric record. The tech and
  // combo blocks use a different layout, so they are deliberately not addressable
  // (mirrors x2fields.skill_record_off returning None).
  const kBlocks=()=>KBLOCKS[String(PRIMARY||1)]||[];
  const kBase=(disc)=>Math.min.apply(null,(KBLOCKS[String(disc)]||[[0,0,0,0]])
                                          .map(b=>b[1]));
  function skillKeys(){
    const out=[];
    for(const [,,count,text0] of kBlocks())
      for(let n=0;n<count;n++) out.push(text0+n);
    return out;
  }
  // byte offset of a skill's record WITHIN the K buffer
  function skillOff(i){
    const base=kBase(PRIMARY||1);
    for(const [,b,count,text0] of kBlocks())
      if(i>=text0 && i<text0+count) return b-base+(i-text0)*KSTRIDE;
    return -1;
  }
  const skillBlockName=(i)=>{
    for(const [n,,count,text0] of kBlocks()) if(i>=text0 && i<text0+count) return n;
    return "";
  };
  const skillInfo=(i)=>(SKILLS&&SKILLS[String(i)])||null;
  const skillName=(i)=>{const v=skillInfo(i); return v&&v.name?v.name:("skill "+i);};
  // retail value of one numeric field, from the shipped catalog
  const KCATKEY={EP:"ep",Element:"element",Power:"power",EffPct:"effPct",EffMask:"effMask"};
  function skillRetail(i,label){
    const v=skillInfo(i), k=KCATKEY[label];
    return (v&&v.numeric&&k&&v.numeric[k]!==undefined)?v.numeric[k]:undefined;
  }
  // mirrors x2fields.skill_element_text()
  function elementText(mask){
    const names=[]; let rest=mask;
    for(const n of Object.keys(KELEM).sort((a,b)=>KELEM[a]-KELEM[b]))
      if(mask&KELEM[n]){ names.push(n); rest&=~KELEM[n]; }
    if(rest) names.push("0x"+rest.toString(16).toUpperCase());
    return names.length?names.join("+"):"—";
  }

  // mirrors x2fields.drop_label()
  function dropLabel(catByte,id,rate){
    if(!catByte||!id) return "nothing";
    let name=null;
    const base=DROPBASE[String(catByte)];
    if(base!==undefined && ITEMS){
      const e=ITEMS[String(base+id-1)];
      if(e && !e.placeholder) name=e.name;
    }
    if(!name) name=(DROPCATS[String(catByte)]||("category "+catByte))+" #"+id;
    return name+" "+rate+"%";
  }

  window.initISO = async function(){
    const root=$("#isoRoot"); if(root.dataset.init) return; root.dataset.init="1";
    if(!FS){ root.innerHTML='<div class="card blocked"><b>ISO editing needs desktop Chrome / Edge / Brave / Opera</b>'+
      ' (File System Access API). The Save editor works everywhere, including mobile.</div>'; return; }
    try{ await loadTables(); }
    catch(e){ root.innerHTML='<div class="card blocked"><b>Could not load the disc table definitions</b>'+
      ' — '+esc(String(e))+'. Try ↻ Force refresh in the footer.</div>'; root.dataset.init=""; return; }
    await loadCat();
    // ids are written out literally rather than built in a loop, so every #id
    // the script queries can be grepped straight out of the source
    root.innerHTML='<div class="card"><h2>1 · Open your discs</h2>'+
      '<div class="discrow"><button id="isoPick1" class="btn primary">Choose disc 1…</button> '+
        '<span id="isoStatus1" class="status"></span><div id="isoRecent1"></div></div>'+
      '<div class="discrow"><button id="isoPick2" class="btn">Choose disc 2…</button> '+
        '<span id="isoStatus2" class="status"></span><div id="isoRecent2"></div></div>'+
      '<div id="isoPair"></div>'+
      '<p class="note">Xenosaga II (USA) — disc 1 is <code>SLUS-20892</code>, disc 2 is '+
      '<code>SLUS-21133</code>. It doesn’t matter which button you use: each file is '+
      'identified by its own serial and slotted automatically. <b>Open both</b> and every '+
      'edit is written to both discs, which is what you want — they carry identical enemy '+
      'tables, so editing one alone reverts at the disc swap. Enemy edits apply to a new '+
      'game. Edits write in place; work on a copy or tick backup. Your discs are remembered '+
      'for one-tap reopening (only the file reference is stored — never the disc itself).</p></div>'+
      '<div id="isoEdit"></div>';
    $("#isoPick1").onclick=()=>openISO(1);
    $("#isoPick2").onclick=()=>openISO(2);
    showLastIso();
  };

  // ---- remember opened discs (stores the file HANDLE only — the 4.6 GB is never copied) ----
  const IDB=()=>window.x2idb;
  const isoKey=(n)=>"lastIso"+n;
  // Per-disc element selectors, written out rather than concatenated — building
  // "#isoStatus"+n hides the id from anything that greps the source for it
  // (tests/test_web.py checks every queried #id is really emitted).
  const EL={1:{status:"#isoStatus1",recent:"#isoRecent1"},
            2:{status:"#isoStatus2",recent:"#isoRecent2"}};
  function rememberIso(n,name,h){ const k=IDB(); if(k) k.set(isoKey(n),{name,handle:h,at:Date.now()}).catch(()=>{}); }
  async function showLastIso(){
    const k=IDB(); if(!k) return;
    for(const n of [1,2]){
      const el=$(EL[n].recent); if(!el) continue;
      let rec; try{ rec=await k.get(isoKey(n)); }catch(e){ continue; }
      if(!rec||!rec.handle||DISCS[n]){ el.innerHTML=""; continue; }
      const when=k.fmtWhen?k.fmtWhen(rec.at):"";
      el.innerHTML='<div class="recent"><span class="muted small">Last:</span>'+
        '<button class="chip" data-reopen="'+n+'" title="Reopen this disc">↻ '+esc(rec.name)+
        (when?' <span class="muted">('+esc(when)+')</span>':'')+'</button>'+
        '<button class="chip mini" data-forget="'+n+'" title="Forget" aria-label="Forget this disc">✕</button></div>';
      el.querySelector("[data-reopen]").onclick=()=>reopenLastIso(n,rec);
      el.querySelector("[data-forget]").onclick=async()=>{await k.del(isoKey(n)).catch(()=>{});showLastIso();};
    }
  }
  async function reopenLastIso(n,rec){
    const st=$(EL[n].status), k=IDB();
    try{
      if(!(await k.ensureWritable(rec.handle))){
        st.textContent="✗ Reopen cancelled — write permission denied."; st.className="status err"; return; }
      await commitISO(await rec.handle.getFile(), rec.handle, n);
    }catch(e){
      st.textContent="✗ Could not reopen — the file may have moved. Pick it again."; st.className="status err";
    }
  }

  // `hint` is only which button was pressed — used for the transient "checking…"
  // message, since the real disc number isn't known until the header is read.
  async function openISO(hint){
    let h; try{ [h]=await window.showOpenFilePicker(); }catch(e){ return; }
    return commitISO(await h.getFile(), h, hint);
  }

  // Validate an ISO, work out WHICH disc it is, and slot it in. The disc decides
  // its own bases, so this must resolve the serial before slicing anything.
  async function commitISO(f,h,hint){
    const say=(n,msg,cls)=>{const e=$(EL[n||1].status); if(e){e.textContent=msg;e.className="status"+(cls?" "+cls:"");}};
    const probe=(hint===1||hint===2)?hint:1;
    say(probe,"checking disc…");
    const head=new Uint8Array(await f.slice(0,0x200000).arrayBuffer());
    const asc=s=>{let o=-1;const t=[...s].map(c=>c.charCodeAt(0));
      for(let i=0;i<head.length-t.length;i++){let m=true;for(let j=0;j<t.length;j++)if(head[i+j]!==t[j]){m=false;break;}if(m){o=i;break;}}return o;};
    const vol=new TextDecoder().decode(head.slice(0x8028,0x8028+11));
    if(vol!=="XENOSAGA_II"){ say(probe,"✗ Not a Xenosaga II disc (volume "+esc(vol)+")","err"); return; }
    const disc = asc(SERIAL_ASCII[1])>=0 ? 1 : asc(SERIAL_ASCII[2])>=0 ? 2 : 0;
    if(!disc){ say(probe,"✗ Unrecognized Xenosaga II serial.","err"); return; }
    if(probe!==disc) say(probe,"");        // the guess was wrong — don't leave it hanging
    const t=ETABLES[String(disc)];
    if(!t){ say(probe,"✗ No table offsets known for disc "+disc+".","err"); return; }
    const sBase=t.stats, rBase=t.rewards, kb=kBase(disc);
    const sb=new Uint8Array(await f.slice(sBase,sBase+COUNT*STRIDE+TAIL).arrayBuffer());
    const rb=new Uint8Array(await f.slice(rBase,rBase+COUNT*RSTRIDE).arrayBuffer());
    const kbuf=new Uint8Array(await f.slice(kb,kb+KSPAN).arrayBuffer());

    DISCS[disc]={handle:h,name:f.name||("disc"+disc+".iso"),sBase,rBase,kBase:kb,
                 size:f.size,backedUp:false};
    rememberIso(disc,DISCS[disc].name,h);

    if(PRIMARY===null || PRIMARY===disc){
      // first disc in, or a reload of the one we're already editing
      PRIMARY=disc;
      S={buf:sb,orig:sb.slice(),dv:new DataView(sb.buffer)};
      R={buf:rb,orig:rb.slice(),dv:new DataView(rb.buffer)};
      K={buf:kbuf,orig:kbuf.slice(),dv:new DataView(kbuf.buffer)};
      const [,hpO,hpW]=SFIELDS.find(x=>x[0]==="HP");
      const perun=get(S,6,hpO,hpW), want=retail(6,"HP");
      say(disc,"✓ Disc "+disc+" loaded ("+esc(DISCS[disc].name)+")"+
        (want!==undefined&&perun!==want?" — "+esc(cat[6]?cat[6].name:"record 6")+" HP reads "+
          perun.toLocaleString()+" not "+want.toLocaleString()+" (modified disc?)":""),"ok");
      renderEditor();
    } else {
      // second disc: it must agree with what we're editing, or the user has to
      // decide which disc's values win. Compare against `orig`, not `buf`, so
      // staged edits aren't mistaken for a difference between the discs.
      const dS=countDiff(sb,S.orig), dR=countDiff(rb,R.orig), dK=countDiff(kbuf,K.orig);
      if(dS+dR+dK===0){
        say(disc,"✓ Disc "+disc+" loaded ("+esc(DISCS[disc].name)+") — matches disc "+PRIMARY,"ok");
      } else {
        say(disc,"⚠ Disc "+disc+" loaded, but its enemy/skill tables differ from disc "+PRIMARY+
               " in "+(dS+dR+dK)+" byte run(s) — pick which disc's values to keep below","err");
        pendingDiverge={disc,sb,rb,kbuf,runs:dS+dR+dK};
      }
    }
    renderDiscBar();
    showLastIso();
  }

  // number of differing byte RUNS between two equal-length arrays
  function countDiff(a,b){
    let n=0;
    for(let i=0;i<a.length;i++){ if(a[i]!==b[i]){ n++; while(i<a.length&&a[i]!==b[i]) i++; } }
    return n;
  }
  let pendingDiverge=null;

  // Adopt one disc's on-disc values as the thing being edited, discarding
  // whatever was staged (that's the point — the user chose a source of truth).
  function adoptDisc(n){
    const d=DISCS[n]; if(!d) return;
    if(pendingDiverge && pendingDiverge.disc===n){
      S={buf:pendingDiverge.sb,orig:pendingDiverge.sb.slice(),dv:new DataView(pendingDiverge.sb.buffer)};
      R={buf:pendingDiverge.rb,orig:pendingDiverge.rb.slice(),dv:new DataView(pendingDiverge.rb.buffer)};
      K={buf:pendingDiverge.kbuf,orig:pendingDiverge.kbuf.slice(),
         dv:new DataView(pendingDiverge.kbuf.buffer)};
      PRIMARY=n; pendingDiverge=null;
      renderEditor(); renderDiscBar();
      toastFn("Now editing disc "+n+"'s values — they will be written to every targeted disc");
    }
  }

  // ---- the disc bar: what's loaded, and where a Save goes ----
  function renderDiscBar(){
    const el=$("#isoPair"); if(!el) return;
    const loaded=loadedDiscs();
    if(!loaded.length){ el.innerHTML=""; return; }
    let html="";
    if(pendingDiverge){
      const other=PRIMARY;
      html+='<p class="note warn"><b>These discs don\'t match.</b> Disc '+pendingDiverge.disc+
        ' differs from disc '+other+' in '+pendingDiverge.runs+' byte run(s) — one of them was '+
        'probably edited on its own already. Choose which disc\'s enemy values to keep; the other '+
        'disc gets overwritten with them when you save.'+
        ' <button class="btn mini" id="keepA">Keep disc '+other+'</button>'+
        ' <button class="btn mini" id="keepB">Keep disc '+pendingDiverge.disc+'</button></p>';
    }
    if(loaded.length===1){
      const n=loaded[0], other=n===1?2:1;
      html+='<p class="note warn">Only <b>disc '+n+'</b> is open. Disc '+other+' holds its own copy of '+
        'these tables, so anything you write here reverts to retail values at the disc swap. '+
        '<b>Open disc '+other+' too</b> and edits go to both at once.</p>';
    } else {
      html+='<div class="discbar"><span class="fl">Write edits to</span>'+
        ['both',1,2].map(v=>'<label class="pill'+(TARGET===v?' on':'')+'">'+
          '<input type="radio" name="tgt" value="'+v+'"'+(TARGET===v?' checked':'')+'>'+
          (v==='both'?'Both discs':'Disc '+v+' only')+'</label>').join("")+
        '</div><p class="note">Both discs carry identical enemy tables, so <b>Both discs</b> is '+
        'almost always what you want — the values you edit are written to each disc at its own '+
        'offsets. Pick a single disc only if you deliberately want them to differ.</p>';
    }
    el.innerHTML=html;
    if(pendingDiverge){
      $("#keepA").onclick=()=>{ const d=pendingDiverge.disc; pendingDiverge=null; renderDiscBar();
        toastFn("Keeping disc "+PRIMARY+"'s values; disc "+d+" will be overwritten on save"); };
      $("#keepB").onclick=()=>adoptDisc(pendingDiverge.disc);
    }
    el.querySelectorAll('input[name=tgt]').forEach(r=>{
      r.onchange=()=>{ TARGET = r.value==='both'?'both':+r.value; renderDiscBar(); epending(); };
    });
    const lbl=$("#esaveLabel");
    if(lbl){
      const tg=targetDiscs();
      lbl.textContent = tg.length>1 ? "Save to both discs" : "Save to disc "+(tg[0]||PRIMARY);
      epending();
    }
  }

  // ---- enemy picker + filter ----
  // 125 records is too many to scroll, so the dropdown is filtered rather than
  // replaced: the selection survives typing as long as it still matches, and an
  // empty result leaves the loaded enemy alone instead of blanking the sheet.
  const enemyKeys=()=>Object.keys(cat).sort((a,b)=>+a-+b);
  function enemyMatches(q){
    q=String(q||"").trim().toLowerCase();
    if(!q) return enemyKeys();
    return enemyKeys().filter(i=>{
      const r=cat[i]||{};
      return String(i)===q
          || String(i).padStart(3,"0").indexOf(q)>=0
          || String(r.id||"").indexOf(q)>=0
          || String(r.name||"").toLowerCase().indexOf(q)>=0;
    });
  }
  function optionHtml(i){
    return '<option value="'+i+'">'+String(i).padStart(3,"0")+' · '+esc(cat[i].name)+'</option>';
  }
  function paintEnemyList(){
    const sel=$("#esel"), box=$("#esearch"), cnt=$("#ecount");
    if(!sel||!box) return;
    const q=box.value, keys=enemyMatches(q), prev=sel.value;
    if(cnt) cnt.textContent = !q.trim() ? "" :
      (keys.length ? keys.length+" of "+enemyKeys().length : "no match");
    if(cnt) cnt.className = "muted small"+(keys.length?"":" warntext");
    if(!keys.length) return;             // keep the current enemy loaded
    sel.innerHTML=keys.map(optionHtml).join("");
    if(keys.indexOf(prev)>=0){ sel.value=prev; }
    else { sel.value=keys[0]; loadEnemy(); }
  }

  // Which pane the editor is showing. Both write to the same disc and share one
  // Save, so this is presentation only — switching tabs never discards staged
  // work, and the Save button's count covers every pane.
  let PANE="enemy";
  // ids written out literally, not built from the pane name — a concatenated id
  // is invisible to anything that greps the source (tests/test_web.py checks it)
  function showPane(which){
    PANE=which;
    const on=(el,yes)=>{ if(el) el.className="mtab"+(yes?" on":""); };
    const show=(el,yes)=>{ if(el) el.hidden=!yes; };
    show($("#pane-enemy"), which==="enemy");
    show($("#pane-skill"), which==="skill");
    on($("#ptab-enemy"), which==="enemy");
    on($("#ptab-skill"), which==="skill");
    // the patch/JSON/retail actions describe the enemy tables specifically
    show($("#enemyActions"), which==="enemy");
    if(which==="skill") loadSkill();
  }

  function renderEditor(){
    const opts=enemyKeys().map(optionHtml).join("");
    $("#isoEdit").innerHTML=
      '<nav class="modebar panetabs">'+
      '<button id="ptab-enemy" class="mtab on">Enemies</button>'+
      '<button id="ptab-skill" class="mtab">Skills</button>'+
      '</nav>'+
      '<div id="pane-enemy">'+
      '<div class="card"><h2>2 · Enemy</h2>'+
      '<div class="toolbar"><label>Enemy</label> <select id="esel">'+opts+'</select>'+
      '<span class="findbox"><input type="search" id="esearch" placeholder="find by name, index or id" '+
        'autocomplete="off" spellcheck="false"><button type="button" id="eclear" '+
        'class="chip mini" title="Clear search" aria-label="Clear search">✕</button></span>'+
      '<span id="ecount" class="muted small"></span>'+
      '<label style="margin-left:8px"><input type="checkbox" id="ebak"> back up ISO first</label>'+
      '</div>'+
      '<table id="etbl"><tbody><tr id="erow"></tr><tr id="erow2" class="gearrow"></tr></tbody></table>'+
      '<div id="eflags" class="note"></div>'+
      '<div id="eretail" class="note"></div>'+
      '<div class="affbox"><div class="fl">Item drops</div>'+
        '<table><tbody><tr id="erow4"></tr></tbody></table>'+
        '<div id="edrops" class="muted small"></div>'+
        '<p class="note">Two slots per enemy: a common drop and a rare one, each a '+
        'percentage plus a category (0 nothing, 1 consumable, 2 E.S. gear) and a '+
        '<b>1-based</b> item id within that category. Consumable ids are verified against '+
        'the item catalog; E.S. gear ids are shown as bare numbers because that id space '+
        'has not been pinned down yet.</p></div>'+
      '<div class="affbox"><div class="fl">Status resistance (%)</div>'+
        '<table><tbody><tr id="erow5"></tr></tbody></table>'+
        '<p class="note">Higher resists the status more. Eight of the ten statuses a '+
        'strategy guide publishes map to these bytes at 98–100% agreement; the block has '+
        'three more bytes we have not identified, so they are not shown.</p></div>'+
      '<div class="affbox"><div class="fl">Damage taken, by element (%)</div>'+
        '<table><tbody><tr id="erow3"></tr></tbody></table>'+
        '<p class="note">'+AFF_NORMAL+'% is normal, below resists, above takes extra, '+
        '<b>0 is immune</b> and <b>negative absorbs</b> (Svarozic takes -200% Fire, i.e. it '+
        'heals for double). Stored as a signed byte &times;'+AFF_SCALE+', so values snap to '+
        AFF_SCALE+'% steps and the usable range is about -640% to +635%. Verified against '+
        '71 guide entries, exact on every one.</p></div>'+
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
      '<div class="brkbox"><div class="fl">Shorten every Break sequence</div>'+
        '<div class="toolbar" style="margin:0">'+
        '<button id="brkS1" class="btn">−1 hit</button>'+
        '<button id="brkS2" class="btn">−2 hits</button>'+
        '<button id="brkS3" class="btn">−3 hits</button>'+
        '<span id="brkInfo" class="muted small"></span></div>'+
        '<label class="brkguard"><input type="checkbox" id="brkKeep" checked> '+
        'Keep every enemy breakable <span class="muted small">— never trim a sequence '+
        'to nothing</span></label>'+
        '<div id="brkOpts"></div>'+
        '<div id="brkPreview"></div>'+
        '<p class="note">This is the loop\'s actual gate, not a stat: a 4-hit boss costs four correct '+
        'zone hits <i>per break, all fight</i>. Trimming takes hits off the <b>end</b>, so the opening '+
        'zone you already know stays right. An enemy that already can\'t be broken is never touched.</p>'+
        '<p class="note">The shield above is why a 1-hit sequence is left alone. Emptying a sequence '+
        'doesn\'t shorten the break, it <b>removes</b> it: 16 retail enemies ship that way and 15 of '+
        'them still have weak zones, so there are places to hit but no break to reach. Turn it off '+
        'only if that is what you actually want.</p>'+
      '</div>'+
      '<p class="note">Staged into the same pending-changes set above — review everything before writing. '+
      'Scaling always starts from the values the disc had when it was opened, so re-staging replaces the '+
      'previous plan instead of compounding it. Values round to whole numbers; HP floors at 1.</p></div>'+
      '<div class="card"><h2>4 · Patch files, bulk JSON &amp; retail values</h2>'+
      '<input type="file" id="pFile" accept=".json,application/json" hidden>'+
      '<input type="file" id="tFile" accept=".json,application/json" hidden>'+
      '<p class="note">A patch is a small JSON file listing only the fields you changed, so you can '+
      'share a rebalance instead of a 4.6 GB disc. Importing <i>stages</i> the changes for review '+
      'rather than writing them. The command line reads and writes the same file: '+
      '<code>x2patch.py export-patch</code> / <code>apply-patch</code>. Because the bestiary shipped '+
      'with this editor holds the verified retail numbers, the editor can also tell you exactly how '+
      'your disc differs from an unmodified one — and put it back.</p></div>'+
      '</div>'+                              // /pane-enemy
      '<div id="pane-skill" hidden>'+
      '<div class="card"><h2>2 · Skill</h2>'+
      '<div class="toolbar"><label>Skill</label> <select id="ksel"></select>'+
      '<span class="findbox"><input type="search" id="ksearch" placeholder="find by name, text or index" '+
        'autocomplete="off" spellcheck="false"><button type="button" id="kclear" '+
        'class="chip mini" title="Clear search" aria-label="Clear search">✕</button></span>'+
      '<span id="kcount" class="muted small"></span></div>'+
      '<div id="kdesc" class="note"></div>'+
      '<div id="krow"></div>'+
      '<div id="kelem" class="note"></div>'+
      '<div id="kretail" class="note"></div>'+
      '<p class="note"><b>EP</b> is what the skill costs to cast and <b>Power</b> scales its '+
      'damage or healing — between them they decide whether a skill is worth a turn. '+
      '<b>Element</b> is a bitmask (' + Object.keys(KELEM).sort((a,b)=>KELEM[a]-KELEM[b]).map(n=>esc(n)+" = 0x"+KELEM[n].toString(16).toUpperCase()).join(", ") + '), '+
      'so it pairs with the enemy damage affinities on the Enemies tab. <b>EffPct</b> and '+
      '<b>EffMask</b> drive the status effect a skill applies; they are verified as fields but '+
      'their encoding is only partly decoded, so treat them as advanced.</p>'+
      '<p class="note">Only the '+skillKeys().length+' Ether and Double skills have verified numeric '+
      'records. The tech and combo blocks use a different layout and are deliberately not '+
      'editable here rather than written blind — the Reference tab lists all 174 by name.</p>'+
      '</div>'+
      '</div>'+                              // /pane-skill
      // Sticky bottom action bar — same shape and ordering as the Suikoden 3
      // editor: primary Save first, then the dirty pill, then revert, then the
      // export/import pairs. Opaque so content scrolls cleanly underneath.
      '<div class="actionbar">'+
      '<button id="esave" class="btn primary" disabled><span id="esaveLabel">Save to ISO</span> '+
        '<span id="ebadge" class="badge"></span></button>'+
      '<span id="edirty" class="pill dirty" hidden></span>'+
      '<button id="erev" class="btn" disabled>↺ Revert all</button>'+
      '<span class="sep"></span>'+
      '<button id="pXdelta" class="btn">⬇ Export .xdelta…</button>'+
      '<span id="enemyActions" class="barGroup">'+
      '<button id="pExport" class="btn">⬇ Export patch…</button>'+
      '<button id="pImport" class="btn">⬆ Import patch…</button>'+
      '<button id="tExport" class="btn">⬇ Export JSON…</button>'+
      '<button id="tImport" class="btn">⬆ Import JSON…</button>'+
      '<span class="sep"></span>'+
      '<button id="pDiff" class="btn">Compare to retail</button>'+
      '<button id="pRestore" class="btn">Stage restore</button>'+
      '</span>'+
      '<span style="flex:1"></span>'+
      '<span id="estat" class="status"></span></div>';
    $("#esel").onchange=loadEnemy;
    $("#esearch").addEventListener("input",paintEnemyList);
    $("#esearch").addEventListener("keydown",e=>{
      if(e.key==="Escape"){ $("#esearch").value=""; paintEnemyList(); }
      if(e.key==="Enter"){ e.preventDefault(); loadEnemy(); }
    });
    $("#eclear").onclick=()=>{ $("#esearch").value=""; paintEnemyList(); $("#esearch").focus(); };
    $("#ptab-enemy").onclick=()=>showPane("enemy");
    $("#ptab-skill").onclick=()=>showPane("skill");
    $("#ksel").onchange=loadSkill;
    $("#ksearch").addEventListener("input",paintSkillList);
    $("#ksearch").addEventListener("keydown",e=>{
      if(e.key==="Escape"){ $("#ksearch").value=""; paintSkillList(); }
      if(e.key==="Enter"){ e.preventDefault(); loadSkill(); }
    });
    $("#kclear").onclick=()=>{ $("#ksearch").value=""; paintSkillList(); $("#ksearch").focus(); };
    paintSkillList();
    // Revert covers every pane: one Save writes them all, so one Revert has to
    // undo them all or the button would lie about its scope.
    $("#erev").onclick=()=>{S.buf.set(S.orig);R.buf.set(R.orig);K.buf.set(K.orig);
      loadEnemy();loadSkill();epending();};
    $("#esave").onclick=saveISO;
    $("#sclApply").onclick=()=>stageRebalance(readScales());
    document.querySelectorAll("#profRow .prof").forEach(b=>b.onclick=()=>applyProfile(b.dataset.p));
    // explicit selectors, not "#brkS"+n — a concatenated id is invisible to
    // anything that greps the source for it (tests/test_web.py checks that)
    $("#brkKeep").onchange=paintBrkOpts;
    $("#brkS1").onclick=()=>stageShorten(1);
    $("#brkS2").onclick=()=>stageShorten(2);
    $("#brkS3").onclick=()=>stageShorten(3);
    $("#tExport").onclick=exportTable;
    $("#tImport").onclick=()=>$("#tFile").click();
    $("#tFile").onchange=importTable;
    $("#pExport").onclick=exportPatch;
    $("#pXdelta").onclick=exportXdelta;
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
  // Deliberately narrower than the retail comparison: this warns about
  // double-SCALING, so it looks only at fields a profile scales. A disc whose
  // break sequences were shortened is not "already rebalanced" in that sense.
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
  function cellHtml(lbl,off,w,val,def,pct){
    // pct: the field is a signed byte shown as a percentage (damage affinities),
    // so it needs a sign, a wider range, and byte<->percent conversion on write.
    return '<td><div class="fl">'+lbl+'</div><span><input type="number" '+
      (pct?'step="'+AFF_SCALE+'" ':'min="0" ')+'autocomplete="off" '+
      'data-f="'+lbl+'" data-o="'+off+'" data-w="'+w+'" data-def="'+def+'"'+
      (pct?' data-pct="1"':'')+' value="'+val+'"></span></td>';
  }
  // signed byte <-> percent, mirroring x2fields.affinity_pct / affinity_byte
  const affPct=(b)=>((b>127?b-256:b)*AFF_SCALE);
  const affByte=(p)=>{let st=Math.round((+p||0)/AFF_SCALE);
    st=Math.max(-128,Math.min(st,127)); return st<0?st+256:st;};
  function loadEnemy(){
    const i=+$("#esel").value;
    const eid=get(S,i,ID_OFF,2);
    $("#erow").innerHTML=SFIELDS.map(([l,o,w])=>cellHtml(l,o,w,get(S,i,o,w),getOrig(S,i,o,w))).join("");
    $("#erow2").innerHTML='<td><div class="fl">rewards</div></td>'+
      RFIELDS.map(([l,o,w])=>cellHtml(l,o,w,get(R,i,o,w),getOrig(R,i,o,w))).join("")+
      '<td colspan="4"><div class="fl">enemy id</div><span class="muted small">'+eid+
      (eid>=BOSS_ID_MIN?" · boss":"")+'</span></td>';
    $("#erow5").innerHTML=RFIELDS_RES.map(([l,o,w])=>
      cellHtml(l,o,w,get(S,i,o,w),getOrig(S,i,o,w))).join("");
    $("#erow4").innerHTML=DFIELDS.map(([l,o,w])=>
      cellHtml(l,o,w,get(R,i,o,w),getOrig(R,i,o,w))).join("");
    $("#erow3").innerHTML=AFIELDS.map(([l,o,w])=>
      cellHtml(l,o,w,affPct(get(S,i,o,w)),affPct(getOrig(S,i,o,w)),true)).join("");
    // type, and whether the game will honour a break sequence at all
    const fl=$("#eflags");
    if(fl) fl.innerHTML="Type: <b>"+esc(enemyType(i))+"</b> · "+
      (canBreak(i)
        ? "breakable"
        : noZone(i)
          ? "<b>cannot be broken</b> — zone targeting is off for this enemy"+
            (breakSeq(i)?" , so its <code>"+esc(breakSeq(i))+"</code> sequence is inert":"")
          : "<b>cannot be broken</b> — it has no Break sequence");

    // how this record compares with an unmodified disc
    const off=retailDiffs(i);
    $("#eretail").innerHTML = off.length
      ? "Differs from retail: "+off.map(d=>esc(d.label)+" "+esc(d.cur)+
          " (retail "+esc(d.van)+")").join(", ")
      : "Matches the retail values for this enemy.";
    const paintDrops=()=>{
      const el=$("#edrops"); if(!el) return;
      const v=(f)=>{const sp=DFIELDS.find(x=>x[0]===f); return sp?get(R,i,sp[1],sp[2]):0;};
      el.textContent="common: "+dropLabel(v("DropCat"),v("DropItem"),v("DropRate"))+
                     "   ·   rare: "+dropLabel(v("RareCat"),v("RareItem"),v("RareRate"));
    };
    document.querySelectorAll("#erow input, #erow2 input, #erow3 input, #erow4 input, #erow5 input").forEach(inp=>{
      let btn=inp.nextElementSibling;
      if(!btn||!btn.classList.contains("restore")){btn=document.createElement("button");btn.type="button";
        btn.className="restore";btn.textContent="↺";inp.after(btn);}
      const refresh=()=>{const f=inp.dataset.f,off=+inp.dataset.o,w=+inp.dataset.w;
        put(tableOf(f),i,off,w, inp.dataset.pct ? affByte(inp.value) : +inp.value);
        const ch=String(inp.value)!==String(inp.getAttribute("data-def"));
        inp.classList.toggle("changed",ch);btn.classList.toggle("show",ch);paintDrops();epending();};
      inp.addEventListener("input",refresh);
      btn.onclick=()=>{inp.value=inp.getAttribute("data-def");refresh();};refresh();
    });
    wireBreak(i);
    paintDrops();
    epending();
    checkBrkPlan(); paintBrkOpts();
  }

  // ---- skills pane --------------------------------------------------------
  function skillMatches(i,q){
    if(!q) return true;
    const v=skillInfo(i)||{};
    const hay=[String(i),v.name||"",v.desc||"",v.target||"",skillBlockName(i)]
      .join(" ").toLowerCase();
    return q.split(/\s+/).filter(Boolean).every(t=>hay.indexOf(t)>=0);
  }
  function paintSkillList(){
    const sel=$("#ksel"); if(!sel) return;
    const q=($("#ksearch").value||"").trim().toLowerCase();
    const prev=sel.value;
    const keys=skillKeys().filter(i=>skillMatches(i,q));
    const all=skillKeys().length;
    $("#kcount").textContent=q?(keys.length+" of "+all):(all+" editable");
    if(!keys.length){
      sel.innerHTML='<option value="">no match</option>'; sel.disabled=true;
      $("#kdesc").textContent="No skill matches “"+q+"”."; $("#krow").innerHTML="";
      $("#kelem").textContent=""; $("#kretail").textContent=""; return;
    }
    sel.disabled=false;
    sel.innerHTML=keys.map(i=>'<option value="'+i+'">'+String(i).padStart(3,"0")+
      " · "+esc(skillName(i))+'</option>').join("");
    if(keys.indexOf(+prev)>=0) sel.value=prev; else loadSkill();
  }
  function loadSkill(){
    const sel=$("#ksel"); if(!sel||sel.disabled||sel.value==="") return;
    const i=+sel.value, base=skillOff(i);
    if(base<0){ $("#kdesc").textContent="No verified numeric record for this skill."; return; }
    const v=skillInfo(i)||{};
    $("#kdesc").innerHTML='<b>'+esc(v.name||("skill "+i))+'</b>'+
      (v.target?' <span class="muted">· '+esc(v.target)+'</span>':'')+
      ' <span class="muted">· '+esc(skillBlockName(i))+' block</span>'+
      (v.desc?'<br>'+esc(v.desc):'');
    $("#krow").innerHTML='<table><tbody><tr>'+KFIELDS.map(([l,o,w])=>
      cellHtml(l,base+o,w,getAt(K,base+o,w),getOrigAt(K,base+o,w))).join("")+'</tr></tbody></table>';
    document.querySelectorAll("#krow input").forEach(inp=>{
      let btn=inp.nextElementSibling;
      if(!btn||!btn.classList.contains("restore")){btn=document.createElement("button");
        btn.type="button";btn.className="restore";btn.textContent="↺";inp.after(btn);}
      const refresh=()=>{
        putAt(K,+inp.dataset.o,+inp.dataset.w,+inp.value);
        const ch=String(inp.value)!==String(inp.getAttribute("data-def"));
        inp.classList.toggle("changed",ch);btn.classList.toggle("show",ch);
        paintSkillDerived(i); epending();};
      inp.addEventListener("input",refresh);
      btn.onclick=()=>{inp.value=inp.getAttribute("data-def");refresh();};refresh();
    });
    paintSkillDerived(i);
  }
  // element mask in words, and how the record compares with an unmodified disc
  function paintSkillDerived(i){
    const base=skillOff(i); if(base<0) return;
    const em=KFIELDS.find(x=>x[0]==="Element");
    if(em) $("#kelem").textContent="Element: "+elementText(getAt(K,base+em[1],em[2]));
    const off=[];
    for(const [l,o,w] of KFIELDS){
      const van=skillRetail(i,l); if(van===undefined) continue;
      const cur=getAt(K,base+o,w); if(cur===van) continue;
      off.push(esc(l)+" "+cur.toLocaleString()+" (retail "+van.toLocaleString()+")");
    }
    $("#kretail").innerHTML = off.length ? "Differs from retail: "+off.join(", ")
                                         : "Matches the retail values for this skill.";
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

  // ---- bulk break shortening (mirrors x2fields.shorten_break_seq) ----
  // Emptying a sequence does not speed a fight up — it removes the break. 16
  // retail enemies ship with no sequence and 15 of those still have a live zone
  // mask, so "no sequence" is not "no weak zones": there is simply no break to
  // reach, and the fight gets longer. The floor is on by default for that
  // reason, but it is a balance choice, so it is a control rather than a
  // constant. Mirrors x2fields.BREAK_MIN_LEN / BREAK_FLOOR_NONE.
  const BREAK_MIN_LEN=1, BREAK_FLOOR_NONE=0;
  const breakFloor=()=>{const c=$("#brkKeep"); return (!c||c.checked)?BREAK_MIN_LEN:BREAK_FLOOR_NONE;};
  function shortenSeq(seq,steps,floor){
    if(!seq) return seq;
    if(floor===undefined) floor=breakFloor();
    return seq.slice(0, Math.max(floor, seq.length-Math.max(0,steps|0)));
  }
  // What each option would actually do, from the CURRENT staged state — so the
  // three buttons can be compared before pressing one, and the numbers update
  // after each press. "Break hits" is the honest measure of the tax: the total
  // number of correct zone hits a full clear costs, once per break per enemy.
  function breakStats(steps){
    const plan=planShorten(steps);
    let before=0, after=0, longest=0, emptied=0;
    const from={};
    for(let i=0;i<COUNT;i++){
      const old=breakSeq(i);
      if(!old || noZone(i)) continue;          // unbreakable: not part of the tax
      const nw=shortenSeq(old,steps);
      before+=old.length; after+=nw.length;
      longest=Math.max(longest,nw.length);
      if(nw.length!==old.length) from[old.length]=nw.length;
      if(!nw.length) emptied++;
    }
    return {plan,affected:plan.length,before,after,longest,emptied,
            cut: before?Math.round(100*(before-after)/before):0, from};
  }
  // The impact list is a claim about staged state: "these 108 enemies are set to
  // change." Revert all restores the buffer, so the claim stops being true — but
  // the list stayed on screen, which reads as the section not having reverted.
  // Re-validating against the buffer catches that for every path that can replace
  // the staged set, not just the ones remembered here.
  let BRK_PLAN=null;
  function checkBrkPlan(){
    if(!BRK_PLAN) return;
    if(BRK_PLAN.every(([i,,nw])=>breakSeq(i)===nw)) return;
    BRK_PLAN=null;
    const info=$("#brkInfo"), prev=$("#brkPreview");
    if(info) info.textContent="";
    if(prev) prev.innerHTML="";
  }

  function paintBrkOpts(){
    const el=$("#brkOpts"); if(!el) return;
    const rows=[1,2,3].map(n=>[n,breakStats(n)]);
    const base=rows[0][1].before;
    if(!base){ el.innerHTML='<p class="note">No enemy on this disc has a Break '+
      'sequence left to shorten.</p>'; return; }
    el.innerHTML='<table class="brkopts"><tbody>'+
      '<tr><th></th><th>enemies</th><th>becomes</th><th>break hits to clear</th></tr>'+
      rows.map(([n,st])=>{
        const map=Object.keys(st.from).sort((a,b)=>b-a)
          .map(k=>k+"→"+st.from[k]).join("  ") || "—";
        return '<tr><th>−'+n+(n===1?' hit':' hits')+'</th>'+
          '<td>'+(st.affected||"—")+'</td>'+
          '<td class="map">'+map+'</td>'+
          '<td>'+st.before+' → '+st.after+
            (st.cut?' <span class="cut">−'+st.cut+'%</span>':'')+'</td></tr>';
      }).join("")+'</tbody></table>'+
      '<p class="note" style="margin-top:6px">“Break hits to clear” is every enemy’s '+
      'sequence added up — one full pass through the bestiary. It is the size of the '+
      'ritual, not of any one fight. Unbreakable enemies aren’t counted — including the '+
      'ones whose sequence bytes are <i>inert</i> because zone targeting is off for them '+
      '(15 on a retail disc). Trimming those would change bytes the game never reads.</p>'+
      (rows.some(([,st])=>st.emptied)
        ? '<p class="note warnnote">⚠ With the shield off, '+
          rows.filter(([,st])=>st.emptied).map(([n,st])=>st.emptied+' enemy'+
            (st.emptied===1?'':'s')+' at −'+n).join(", ")+
          ' would lose their sequence entirely and become <b>unbreakable</b> — those '+
          'fights get longer, not shorter.</p>'
        : '');
  }

  function planShorten(steps){
    const plan=[];
    for(let i=0;i<COUNT;i++){
      if(noZone(i)) continue;              // cannot be broken whatever the bytes say
      const old=breakSeq(i), nw=shortenSeq(old,steps);
      if(nw!==old) plan.push([i,old,nw]);
    }
    return plan;
  }
  function stageShorten(steps){
    const plan=planShorten(steps);
    const info=$("#brkInfo"), prev=$("#brkPreview");
    if(!plan.length){
      BRK_PLAN=null;
      info.textContent="nothing to shorten — every sequence is already at the minimum";
      prev.innerHTML=""; return;
    }
    for(const [i,,nw] of plan) setBreakSeq(i,nw);
    BRK_PLAN=plan;
    const byLen={};
    for(const [,old] of plan) byLen[old.length]=(byLen[old.length]||0)+1;
    info.textContent="staged for "+plan.length+" of "+COUNT+" enemies";
    prev.innerHTML='<p class="note"><b>'+plan.length+' affected</b> — '+
      Object.keys(byLen).sort((a,b)=>b-a).map(k=>byLen[k]+" x "+k+"-hit").join(", ")+
      '</p><div class="brklist">'+plan.map(([i,old,nw])=>
        '<div><span class="bi">'+String(i).padStart(3,"0")+'</span>'+
        '<span class="bn">'+esc(cat[i]?cat[i].name:String(i))+'</span>'+
        '<span class="bs">'+old+' → '+nw+'</span></div>'
      ).join("")+'</div>';
    loadEnemy(); epending(); paintBrkOpts();
    const gone=plan.filter(([,,nw])=>!nw).length;
    toastFn("✓ Break sequences shortened for "+plan.length+" enemies"+
      (gone?" — ⚠ "+gone+" now UNBREAKABLE":"")+" — review & Save", !!gone);
  }

  // ---- full-table JSON (mirrors x2patch.enemy_json / parse_enemy_json) ----
  const TABLE_FORMAT="x2-enemy-table", TABLE_VERSION=1;
  function tableDoc(){
    const rows=[];
    for(let i=0;i<COUNT;i++){
      const row={index:i,name:(cat[i]&&cat[i].name)||"?"};
      SFIELDS.concat(RFIELDS).forEach(([l,o,w])=>{ row[l]=get(tableOf(l),i,o,w); });
      row["break"]=breakSeq(i);
      row.zones=zoneMaskText(get(S,i,ZMASK_OFF,1));
      row.affinity={}; AFIELDS.forEach(([l,o,w])=>{ row.affinity[l]=affPct(get(S,i,o,w)); });
      row.resist={};   RFIELDS_RES.forEach(([l,o,w])=>{ row.resist[l]=get(S,i,o,w); });
      const dv=(f)=>{const sp=DFIELDS.find(x=>x[0]===f); return sp?get(R,i,sp[1],sp[2]):0;};
      row.drop={rate:dv("DropRate"),category:dv("DropCat"),item:dv("DropItem"),
                _name:dropLabel(dv("DropCat"),dv("DropItem"),dv("DropRate"))};
      row.rare={rate:dv("RareRate"),category:dv("RareCat"),item:dv("RareItem"),
                _name:dropLabel(dv("RareCat"),dv("RareItem"),dv("RareRate"))};
      rows.push(row);
    }
    return {format:TABLE_FORMAT,version:TABLE_VERSION,game:serialOf(PRIMARY),note:"",
      count:COUNT,
      _help:"Edit values in place. 'break' is zone letters (A/B/C, max "+BRK_SLOTS+
            ", empty = cannot be broken). Affinities are percentages in "+AFF_SCALE+
            "% steps; negative absorbs. '_name' fields are read-only hints, ignored on import.",
      enemies:rows};
  }
  // strict on purpose: this writes into a 4.6 GB disc image
  function parseTable(doc){
    const cap=(w)=>w===4?0xFFFFFFFF:w===2?0xFFFF:0xFF;
    if(!doc||typeof doc!=="object"||doc.format!==TABLE_FORMAT)
      throw new Error("not a "+TABLE_FORMAT+" file");
    if(doc.version!==TABLE_VERSION)
      throw new Error("table version "+doc.version+" is not supported (this build reads "+TABLE_VERSION+")");
    if(!Array.isArray(doc.enemies)||!doc.enemies.length) throw new Error("no 'enemies' array");
    const plain={}; SFIELDS.concat(RFIELDS).forEach(f=>plain[f[0]]=f);
    const out={};
    doc.enemies.forEach((row,n)=>{
      if(!row||typeof row!=="object") throw new Error("row "+n+": expected an object");
      const i=row.index;
      if(!Number.isInteger(i)||i<0||i>=COUNT)
        throw new Error("row "+n+": 'index' must be 0.."+(COUNT-1)+", got "+JSON.stringify(i));
      const where="enemy "+i+" ("+(row.name||"?")+")";
      const num=(label,v,limit)=>{
        if(!Number.isInteger(v)) throw new Error(where+": "+label+" must be a whole number, got "+JSON.stringify(v));
        if(v<0||v>limit) throw new Error(where+": "+label+" must be 0.."+limit+", got "+v);
        return v;
      };
      const edits={};
      for(const l in plain) if(l in row) edits[l]=num(l,row[l],cap(plain[l][2]));
      if("break" in row){
        const t=String(row["break"]||"");
        const syms=t.toUpperCase().split("").filter(c=>c!=="-"&&c.trim()!=="");
        const bad=syms.filter(c=>!(c in ZBITS));
        if(bad.length) throw new Error(where+": break — not a zone letter: "+bad.join("")+" (use A, B or C)");
        if(syms.length>BRK_SLOTS) throw new Error(where+": break — at most "+BRK_SLOTS+" hits");
        for(let k=0;k<BRK_SLOTS;k++) edits["Brk"+(k+1)]=k<syms.length?ZBITS[syms[k]]:0;
      }
      if(row.affinity!==undefined){
        if(typeof row.affinity!=="object"||!row.affinity) throw new Error(where+": 'affinity' must be an object");
        for(const el in row.affinity){
          if(!AFIELDS.some(f=>f[0]===el))
            throw new Error(where+": unknown element "+JSON.stringify(el)+"; expected one of "+AFF_ELEMENTS.join(", "));
          const pct=row.affinity[el];
          if(!Number.isInteger(pct)) throw new Error(where+": affinity "+el+" must be a whole number of percent, got "+JSON.stringify(pct));
          if(pct%AFF_SCALE) throw new Error(where+": affinity "+el+" must be a multiple of "+AFF_SCALE+"%, got "+pct);
          if(pct< -128*AFF_SCALE || pct> 127*AFF_SCALE)
            throw new Error(where+": affinity "+el+" is out of range, got "+pct);
          edits[el]=affByte(pct);
        }
      }
      if(row.resist!==undefined){
        if(typeof row.resist!=="object"||!row.resist) throw new Error(where+": 'resist' must be an object");
        for(const st in row.resist){
          if(!RFIELDS_RES.some(f=>f[0]===st))
            throw new Error(where+": unknown status "+JSON.stringify(st)+"; expected one of "+RFIELDS_RES.map(f=>f[0]).join(", "));
          edits[st]=num("resist "+st,row.resist[st],0xFF);
        }
      }
      [["drop","Drop"],["rare","Rare"]].forEach(([key,pre])=>{
        const d=row[key];
        if(d===undefined) return;
        if(typeof d!=="object"||!d) throw new Error(where+": '"+key+"' must be an object");
        [["rate",pre+"Rate"],["category",pre+"Cat"],["item",pre+"Item"]].forEach(([k,l])=>{
          if(k in d) edits[l]=num(key+"."+k,d[k],0xFF);
        });
      });
      if(Object.keys(edits).length) out[i]=edits;
    });
    if(!Object.keys(out).length) throw new Error("document contains no editable values");
    return out;
  }
  function exportTable(){
    const doc=tableDoc();
    const blob=new Blob([JSON.stringify(doc,null,1)+"\n"],{type:"application/json"});
    const a=document.createElement("a");
    a.href=URL.createObjectURL(blob); a.download="xenosaga2-enemies.json";
    a.click(); setTimeout(()=>URL.revokeObjectURL(a.href),1000);
    toastFn("✓ Exported "+doc.count+" enemies");
  }
  async function importTable(e){
    const f=e.target.files&&e.target.files[0];
    e.target.value="";
    if(!f) return;
    let edits;
    try{ edits=parseTable(JSON.parse(await f.text())); }
    catch(err){ toastFn("✗ "+err.message,true);
      $("#estat").textContent="✗ "+err.message; $("#estat").className="status err"; return; }
    // stage rather than write, and report what actually differs
    let recs=0, fields=0;
    try{
    for(const k in edits){
      const i=+k; let touched=false;
      for(const lbl in edits[k]){
        const sp=specOf(lbl);
        if(!sp) throw new Error("internal: no field spec for "+lbl);
        const T=tableOf(lbl);
        if(get(T,i,sp[1],sp[2])!==edits[k][lbl]){ put(T,i,sp[1],sp[2],edits[k][lbl]); fields++; touched=true; }
      }
      if(touched) recs++;
    }
    }catch(err){ toastFn("✗ "+err.message,true);
      $("#estat").textContent="✗ "+err.message; $("#estat").className="status err"; return; }
    loadEnemy(); epending();
    const msg = fields ? ("✓ Staged "+fields+" field(s) across "+recs+" enemies — review & Save")
                       : "Imported: every value already matches";
    $("#estat").textContent=msg; $("#estat").className="status ok";
    toastFn(msg);
  }

  function diffCount(){let n=0;
    for(const T of [S,R,K]){for(let i=0;i<T.buf.length;i++)if(T.buf[i]!==T.orig[i]){n++;while(i<T.buf.length&&T.buf[i]!==T.orig[i])i++;}}
    return n;}
  function epending(){const n=diffCount();const b=$("#ebadge");if(b)b.textContent=n?"("+n+")":"";
    const s=$("#esave"),r=$("#erev");if(s)s.disabled=!n;if(r)r.disabled=!n;
    const d=$("#edirty");
    if(d){ d.hidden=!n; d.textContent=n?("● "+n+" unsaved change"+(n===1?"":"s")):""; }}
  function diffRuns(T){const runs=[];let i=0;while(i<T.buf.length){if(T.buf[i]!==T.orig[i]){let j=i;
    while(j<T.buf.length&&T.buf[j]!==T.orig[j])j++;runs.push([i,j]);i=j;}else i++;}return runs;}

  // What the confirm dialog lists before anything is written. It must cover
  // every field a Save actually writes — it used to show stats, affinities and
  // rewards only, so a break-sequence or drop change went to disc without ever
  // appearing in the review it was supposedly reviewed in.
  function reviewRows(){
    const row=(l,a,b)=>'<div class="revrow"><span class="rl">'+esc(l)+'</span><span class="ro">'+
      esc(a)+'</span>→ <span class="rn">'+esc(b)+'</span></div>';
    let rows="",count=0;
    for(let i=0;i<COUNT && count<400;i++){
      let cells="";
      // break slots collapse into one readable row, as in the retail comparison
      let ob=""; for(let n=0;n<BRK_SLOTS;n++){const y=ZSYM[getOrig(S,i,BRK_OFF+n,1)];if(!y)break;ob+=y;}
      const nb=breakSeq(i);
      if(ob!==nb) cells+=row("Break",ob||"—",nb||"—");
      for(const [T,FL] of [[S,SFIELDS],[S,AFIELDS],[S,RFIELDS_RES],[R,RFIELDS],[R,DFIELDS]]){
        for(const [l,o,w] of FL){
          const a=getOrig(T,i,o,w),b=get(T,i,o,w);
          if(a!==b) cells+=row(l,a.toLocaleString(),b.toLocaleString());
        }
      }
      const zs=ZFIELDS.find(x=>x[0]==="Zones");
      if(zs){
        const a=getOrig(S,i,zs[1],zs[2]), b=get(S,i,zs[1],zs[2]);
        if(a!==b) cells+=row("Zones",zoneMaskText(a)||"—",zoneMaskText(b)||"—");
      }
      if(cells){rows+='<div class="revgrp">'+String(i).padStart(3,"0")+' · '+esc(cat[i]?cat[i].name:i)+'</div>'+cells;count++;}
    }
    for(const i of skillKeys()){
      const base=skillOff(i); if(base<0) continue;
      let cells="";
      for(const [l,o,w] of KFIELDS){
        const a=getOrigAt(K,base+o,w), b=getAt(K,base+o,w);
        if(a!==b) cells+=row(l,a.toLocaleString(),b.toLocaleString());
      }
      if(cells) rows+='<div class="revgrp">skill '+String(i).padStart(3,"0")+' · '+
        esc(skillName(i))+'</div>'+cells;
    }
    if(count>=400) rows+='<div class="note">…truncated…</div>';
    return rows;
  }

  // ---- patch files, retail comparison, restore ----------------------------
  // Exported against the retail bestiary so a patch describes a complete mod,
  // not just this session's edits — every writable field, now that every
  // writable field has a baseline. Affinities used to need a session-relative
  // fallback here; break sequences, zones, resistances and drops fell through
  // both paths and were exported by neither, so a patch made after shortening
  // every boss's break didn't carry the shortening.
  function buildPatch(note){
    const edits={};
    for(let i=0;i<COUNT;i++){
      const f={};
      for(const [l,o,w] of allFields()){
        const cur=get(tableOf(l),i,o,w), van=retail(i,l);
        if(van!==undefined && cur!==van) f[l]=cur;
      }
      if(Object.keys(f).length) edits[String(i)]=f;
    }
    return {format:PATCH_FORMAT,version:PATCH_VERSION,game:serialOf(PRIMARY),note:note||"",edits};
  }

  // A standard .xdelta (VCDIFF) patch, synthesized from the staged edits rather
  // than diffed — we already know every changed byte range, so nothing has to
  // read the 4.6 GB image. Apply with:
  //     xdelta3 -d -s <pristine ISO> file.xdelta out.iso
  //
  // One patch PER DISC, because the same edit buffer lands at different bases on
  // each (disc 2's tables are 0x800 lower): a single file could only ever be
  // right for one of them, and applying disc 1's patch to disc 2 would write the
  // enemy tables 0x800 into the wrong place.
  function xdeltaEdits(d){
    const runs=[];
    for(const [T,base] of [[S,d.sBase],[R,d.rBase],[K,d.kBase]])
      for(const [s0,e0] of diffRuns(T)) runs.push({off:base+s0,data:T.buf.slice(s0,e0)});
    return runs.sort((a,b)=>a.off-b.off);
  }
  async function exportXdelta(){
    if(!diffCount()) return toastFn("Nothing staged to export",true);
    if(typeof Vcdiff==="undefined") return toastFn("✗ VCDIFF module didn't load — force refresh",true);
    const targets=targetDiscs();
    if(!targets.length) return toastFn("No disc targeted",true);
    const made=[];
    for(const n of targets){
      const d=DISCS[n]; if(!d||!d.size) continue;
      const edits=xdeltaEdits(d);
      const bytes=edits.reduce((a,e)=>a+e.data.length,0);
      let patch;
      try{ patch=Vcdiff.buildXdelta(d.size,edits); }
      catch(e){ toastFn("✗ disc "+n+": "+e.message,true); return; }
      const a=document.createElement("a");
      a.href=URL.createObjectURL(new Blob([patch],{type:"application/octet-stream"}));
      a.download=(d.name.replace(/\.[^.]+$/,"")||("xenosaga2-disc"+n))+".xdelta";
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(()=>URL.revokeObjectURL(a.href),4000);
      made.push("disc "+n+" ("+bytes+" byte"+(bytes===1?"":"s")+")");
      // give the browser a beat between downloads, or the second is dropped
      if(targets.length>1) await new Promise(r=>setTimeout(r,400));
    }
    const msg="✓ Exported "+made.length+" .xdelta patch(es): "+made.join(", ");
    $("#estat").textContent=msg; $("#estat").className="status ok";
    toastFn(msg);
    // No integrity check in the patch (that would mean hashing 4.6 GB), so say
    // plainly what it must be applied to.
    if(window.openInfo) await window.openInfo("Apply your .xdelta patch",
      '<div class="note">Apply each patch to a <b>pristine</b> disc image with:</div>'+
      '<pre class="cmd">xdelta3 -d -s "&lt;pristine ISO&gt;" patch.xdelta out.iso</pre>'+
      '<div class="note">⚠ These patches carry <b>no integrity check</b> — applying one to an '+
      'already-modified or wrong image corrupts it silently. Each patch is for the disc it was '+
      'exported from: disc 2 keeps the same tables 0x800 lower, so the two are not '+
      'interchangeable. If you want a source-verified, human-readable alternative, export a '+
      '<b>patch file</b> instead — that one names fields rather than byte offsets, and the '+
      'editor validates it on import.</div>');
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

  // allFields(), not SFIELDS+RFIELDS: a restore that quietly leaves break
  // sequences, zones, affinities, resistances and drops modified is worse than
  // no restore, because it reports success.
  function stageRestore(){
    let n=0;
    for(let i=0;i<COUNT;i++)
      for(const [l,o,w] of allFields()){
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
      for(const d of retailDiffs(i)){
        fields++;
        cells+='<div class="revrow"><span class="rl">'+esc(d.label)+'</span><span class="ro">'+
          esc(d.van)+'</span>→ <span class="rn">'+esc(d.cur)+'</span></div>';
      }
      if(cells){recs++;
        if(recs<=200) rows+='<div class="revgrp">'+String(i).padStart(3,"0")+' · '+
          esc(cat[i]?cat[i].name:i)+'</div>'+cells;}
    }
    const head=recs
      ? '<div class="note">'+recs+' record(s), '+fields+' field(s) differ from an unmodified disc '+
        '(retail → yours). Covers everything this editor can write — stats, rewards, drops, '+
        'break sequences, zones, affinities and status resistances.</div>'+
        (recs>200?'<div class="note">…first 200 shown…</div>':'')
      : '<div class="note">Every editable field matches the retail values.</div>';
    if(window.openInfo) await window.openInfo("Compared to retail", head+rows);
  }

  async function saveISO(){
    const rows=reviewRows();
    if(window.openReview && !(await window.openReview("Write to ISO — enemy & skill tables", rows, "Apply & write to disc"))) return;
    const st=$("#estat");st.textContent="writing…";st.className="status";$("#esave").disabled=true;
    const targets=targetDiscs();
    // The SAME edit buffer goes to every target, each at its own bases — that is
    // what keeps the discs in sync. A per-disc failure is reported rather than
    // silently leaving one disc written and the other not.
    const done=[], failed=[];
    for(const n of targets){
      const d=DISCS[n];
      try{
        if((await d.handle.queryPermission({mode:"readwrite"}))!=="granted")
          await d.handle.requestPermission({mode:"readwrite"});
        if($("#ebak").checked && !d.backedUp){
          st.textContent="backing up disc "+n+" (this copies the whole disc)…";
          const src=await d.handle.getFile();
          const bh=await window.showSaveFilePicker({suggestedName:src.name+".bak"});
          const bw=await bh.createWritable();await bw.write(src);await bw.close();d.backedUp=true;
        }
        st.textContent="writing disc "+n+"…";
        let runs=0;
        const w=await d.handle.createWritable({keepExistingData:true});
        for(const [T,base] of [[S,d.sBase],[R,d.rBase],[K,d.kBase]]){
          for(const [s,e] of diffRuns(T)){
            await w.write({type:"write",position:base+s,data:T.buf.slice(s,e)}); runs++;
          }
        }
        await w.close();
        done.push(n+" ("+runs+" run"+(runs===1?"":"s")+")");
      }catch(e){ failed.push("disc "+n+": "+e); }
    }
    if(failed.length){
      st.textContent="✗ "+failed.join("; ")+(done.length?"  — wrote disc "+done.join(", "):"");
      st.className="status err"; toastFn("✗ "+failed[0],true);
    } else {
      // only clear the pending state once every target actually took the write
      S.orig=S.buf.slice();R.orig=R.buf.slice();K.orig=K.buf.slice();
      loadEnemy();loadSkill();
      st.textContent="✓ wrote disc "+done.join(", disc ");st.className="status ok";
      toastFn("✓ Saved to "+(done.length>1?"both discs":"disc "+targets[0]));
    }
    epending();
  }
})();
