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
  let KFIELDS, KSTRIDE, KBLOCKS, KBASE, KSPAN, KELEM, KTARGETS, KTGT_ALL, SKILLS=null;
  // the skill/tech name pool, so names can be renamed in place
  let KTEXT=null;
  let PFIELDS, PSTRIDE, PTABLES, PCOUNT, PTEXT0, PKINDOFF, PKINDS, PSTATBITS;
  let GFIELDS_G, GTABLES, GCOUNT, GESIDS, GSTATBITS, ESGEAR=null, COSTS=null;
  let CFIELDS, CTABLES, CCOUNT, CSTRIDE, CTYPEOFF, CIDOFF, CSLOTOFF, CTYPES, CDELTA;
  // player units: 15 records before the enemy table, same 0x5C layout
  let UFIELDS, UAFIELDS, USTRIDE, UCOUNT, UTAIL, UTABLES, UNITS=null;
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
    KBASE=(t.skill||{}).base||null;
    KELEM=(t.skill||{}).elementBits||{};
    KTARGETS=(t.skill||{}).targetNames||{}; KTGT_ALL=(t.skill||{}).targetAll||8;
    KTEXT=(t.skill||{}).textSpan||null;
    PFIELDS=(t.passive||{}).fields||[]; PSTRIDE=(t.passive||{}).stride||12;
    PTABLES=(t.passive||{}).tables||{}; PCOUNT=(t.passive||{}).count||0;
    PTEXT0=(t.passive||{}).text0||0; PKINDOFF=(t.passive||{}).kindOff;
    PKINDS=(t.passive||{}).kindNames||{}; PSTATBITS=(t.passive||{}).statBits||{};
    GFIELDS_G=(t.gear||{}).fields||[]; GTABLES=(t.gear||{}).tables||{};
    GCOUNT=(t.gear||{}).count||0; GESIDS=(t.gear||{}).esIds||{};
    GSTATBITS=(t.gear||{}).statBits||{};
    CFIELDS=(t.skillCost||{}).fields||[]; CTABLES=(t.skillCost||{}).tables||{};
    CCOUNT=(t.skillCost||{}).count||0; CSTRIDE=(t.skillCost||{}).stride||6;
    CTYPEOFF=(t.skillCost||{}).typeOff||0; CIDOFF=(t.skillCost||{}).idOff||1;
    CSLOTOFF=(t.skillCost||{}).slotOff||4; CTYPES=(t.skillCost||{}).typeNames||{};
    CDELTA=(t.skillCost||{}).passiveDelta||109;
    UFIELDS=(t.unit||{}).fields||[]; USTRIDE=(t.unit||{}).stride||92;
    UAFIELDS=(t.unit||{}).affinityFields||[];
    // the affinity block overhangs the record, so the LAST unit's Ice/Pierce/
    // Slash/Hit live past count*stride — read the tail or they come back
    // undefined and render as blank-and-changed (the Dark Erde Kaiser bug)
    UTAIL=(t.unit||{}).recordTail||0;
    UCOUNT=(t.unit||{}).count||15; UTABLES=(t.unit||{}).tables||{};
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
  // five independent slices of the disc, each {buf, orig, dv}: enemy stats,
  // enemy rewards, the skill numeric blocks, the player-unit table, and the
  // skill/tech name text pool
  let S=null, R=null, K=null, U=null, TX=null, C=null;
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
    // names + retail values for the player units
    try{UNITS=await (await fetch("../Editor/x2_units.json")).json();}catch(e){UNITS={};}
    try{ESGEAR=await (await fetch("../Editor/x2_es_equip.json")).json();}catch(e){ESGEAR={};}
    // the retail purchase costs, so the Costs pane can be compared like the rest
    try{COSTS=await (await fetch("../Editor/x2_costs.json")).json();}catch(e){COSTS={};}
    return cat; }

  // ---- skills -------------------------------------------------------------
  // A skill is addressed by its TEXT index, which is what the catalog is keyed
  // by; only indices inside a verified block have a numeric record. The tech and
  // combo blocks use a different layout, so they are deliberately not addressable
  // (mirrors x2fields.skill_record_off returning None).
  const kBlocks=()=>KBLOCKS[String(PRIMARY||1)]||[];
  // From tables.json, NOT min(block bases): Target sits at base-0x04, so the
  // buffer has to start four bytes below the first block or the first record's
  // Target byte falls outside it. Deriving it here is what put `undefined` in
  // that slot — invisible while nothing compared Target against a baseline,
  // and a thrown TypeError the moment something did.
  const kBase=(disc)=>{
    const v=KBASE&&KBASE[String(disc)];
    return v!==undefined ? v
      : Math.min.apply(null,(KBLOCKS[String(disc)]||[[0,0,0,0]]).map(b=>b[1]));
  };
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
  // ---- passive / equip skills ----------------------------------------------
  // 12-byte records that live INSIDE the skill text span, so they are read and
  // written through the TX buffer rather than a buffer of their own.
  const passiveKeys=()=>{
    const out=[]; for(let n=0;n<PCOUNT;n++) out.push(PTEXT0+n); return out;
  };
  // byte offset of a passive's record WITHIN the TX buffer
  function passiveOff(i){
    if(!TX||!PCOUNT) return -1;
    const base=PTABLES[String(PRIMARY||1)];
    if(base===undefined) return -1;
    if(i<PTEXT0||i>=PTEXT0+PCOUNT) return -1;
    const at=base-TX.base+(i-PTEXT0)*PSTRIDE;
    return (at<0||at+PSTRIDE>TX.buf.length)?-1:at;
  }
  const passiveKind=(i)=>{const o=passiveOff(i); return o<0?0:getAt(TX,o+PKINDOFF,1);};
  const passiveKindText=(k)=>PKINDS[String(k)]||("0x"+k.toString(16).toUpperCase());
  // What the Param byte MEANS depends on the kind — a magnitude for the scalar
  // kinds, a bitmask for the typed ones. Showing a mask as a number is how a
  // user ends up typing 25 into a field where 25 means Ice|Thunder|Beam.
  const PARAM_IS_MASK={0x40:"element",0x20:"status"};

  // ---- E.S. accessories (the passive table's tail) --------------------------
  // Same 12-byte layout, also inside the text span. Names are read-only: these
  // records' pointers do not resolve into the skill name pool, which matches the
  // standing finding that equipment names come from menu code. So the record ->
  // catalog id map is shipped instead, with nulls for the 予備 placeholders.
  const gearEsId=(k)=>{const v=GESIDS[String(k)]; return (v===null||v===undefined)?null:v;};
  const gearKeys=()=>{
    const out=[];
    for(let k=0;k<GCOUNT;k++) if(gearEsId(k)!==null) out.push(k);
    return out;
  };
  const gearInfo=(k)=>{
    const id=gearEsId(k);
    return (id!==null&&ESGEAR&&ESGEAR[String(id)])||null;
  };
  const gearName=(k)=>{const v=gearInfo(k); return v&&v.name?v.name:("gear "+k);};
  function gearOff(k){
    if(!TX||!GCOUNT) return -1;
    const base=GTABLES[String(PRIMARY||1)];
    if(base===undefined||k<0||k>=GCOUNT) return -1;
    const at=base-TX.base+k*PSTRIDE;
    return (at<0||at+PSTRIDE>TX.buf.length)?-1:at;
  }
  const skillInfo=(i)=>(SKILLS&&SKILLS[String(i)])||null;
  const skillName=(i)=>{const v=skillInfo(i); return v&&v.name?v.name:("skill "+i);};
  // retail value of one numeric field, from the shipped catalog
  // Target belongs here as much as the rest — the catalog has carried
  // numeric.target since the skill table was decoded, and leaving it out made
  // the one field that turns a skill into an AoE the one field "Compare to
  // retail" could not answer for.
  const KCATKEY={Target:"target",EP:"ep",Element:"element",Power:"power",
                 EffPct:"effPct",EffMask:"effMask"};
  function skillRetail(i,label){
    const v=skillInfo(i), k=KCATKEY[label];
    return (v&&v.numeric&&k&&v.numeric[k]!==undefined)?v.numeric[k]:undefined;
  }
  // mirrors x2fields.skill_target_text() — never invents a name for a value
  // the disc uses but we haven't verified
  const SIDE={1:"ally",2:"enemy",4:"self"};
  function targetText(v){
    if(KTARGETS[String(v)]) return KTARGETS[String(v)];
    const side=SIDE[v&7];
    return side ? ((v&KTGT_ALL?"all ":"one ")+side+" (0x"+v.toString(16).toUpperCase()+")")
                : "0x"+v.toString(16).toUpperCase();
  }

  // mirrors x2fields.skill_element_text()
  function elementText(mask){
    const names=[]; let rest=mask;
    for(const n of Object.keys(KELEM).sort((a,b)=>KELEM[a]-KELEM[b]))
      if(mask&KELEM[n]){ names.push(n); rest&=~KELEM[n]; }
    if(rest) names.push("0x"+rest.toString(16).toUpperCase());
    return names.length?names.join("+"):"—";
  }

  // Every id in a drop category, as [id, name]. The categories partition one
  // unified item table by their bases (x2fields.DROP_CAT_BASE): E.S. gear from
  // 0, consumables from 40, so each runs until the next base begins.
  function dropItems(catByte){
    const base=DROPBASE[String(catByte)];
    if(base===undefined||!ITEMS) return [];
    const bases=Object.values(DROPBASE).map(Number).sort((a,b)=>a-b);
    const next=bases.find(b=>b>base);
    const ids=Object.keys(ITEMS).map(Number).sort((a,b)=>a-b);
    const end=next!==undefined?next:(ids[ids.length-1]+1);
    const out=[];
    for(let k=base;k<end;k++){
      const e=ITEMS[String(k)];
      if(!e) continue;
      out.push([k-base+1, e.placeholder?("(unused #"+(k-base+1)+")"):e.name]);
    }
    return out;
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
    const uBase=UTABLES[String(disc)];
    const ubuf=new Uint8Array(await f.slice(uBase,uBase+UCOUNT*USTRIDE+UTAIL).arrayBuffer());
    const [tBase,tLen]=KTEXT[String(disc)];
    const tbuf=new Uint8Array(await f.slice(tBase,tBase+tLen).arrayBuffer());
    // The skill-cost table is the one region whose disc-2 base is NOT -0x800,
    // so it must be resolved per image rather than derived from disc 1's.
    const cBase=CTABLES[String(disc)];
    const cbuf=new Uint8Array(await f.slice(cBase,cBase+CCOUNT*CSTRIDE).arrayBuffer());

    DISCS[disc]={handle:h,name:f.name||("disc"+disc+".iso"),sBase,rBase,kBase:kb,uBase,tBase,
                 cBase,size:f.size,backedUp:false};
    rememberIso(disc,DISCS[disc].name,h);

    if(PRIMARY===null || PRIMARY===disc){
      // first disc in, or a reload of the one we're already editing
      PRIMARY=disc;
      S={buf:sb,orig:sb.slice(),dv:new DataView(sb.buffer)};
      R={buf:rb,orig:rb.slice(),dv:new DataView(rb.buffer)};
      K={buf:kbuf,orig:kbuf.slice(),dv:new DataView(kbuf.buffer)};
      U={buf:ubuf,orig:ubuf.slice(),dv:new DataView(ubuf.buffer)};
      TX={buf:tbuf,orig:tbuf.slice(),dv:new DataView(tbuf.buffer),base:tBase};
      C={buf:cbuf,orig:cbuf.slice(),dv:new DataView(cbuf.buffer),base:cBase};
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
      const dS=countDiff(sb,S.orig), dR=countDiff(rb,R.orig),
            dK=countDiff(kbuf,K.orig), dU=countDiff(ubuf,U.orig),
            dT=countDiff(tbuf,TX.orig), dC=countDiff(cbuf,C.orig);
      if(dS+dR+dK+dU+dT+dC===0){
        say(disc,"✓ Disc "+disc+" loaded ("+esc(DISCS[disc].name)+") — matches disc "+PRIMARY,"ok");
      } else {
        say(disc,"⚠ Disc "+disc+" loaded, but its enemy/skill tables differ from disc "+PRIMARY+
               " in "+(dS+dR+dK+dC)+" byte run(s) — pick which disc's values to keep below","err");
        pendingDiverge={disc,sb,rb,kbuf,ubuf,tbuf,cbuf,runs:dS+dR+dK+dU+dT+dC};
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
      U={buf:pendingDiverge.ubuf,orig:pendingDiverge.ubuf.slice(),
         dv:new DataView(pendingDiverge.ubuf.buffer)};
      TX={buf:pendingDiverge.tbuf,orig:pendingDiverge.tbuf.slice(),
          dv:new DataView(pendingDiverge.tbuf.buffer),base:DISCS[n].tBase};
      C={buf:pendingDiverge.cbuf,orig:pendingDiverge.cbuf.slice(),
         dv:new DataView(pendingDiverge.cbuf.buffer),base:DISCS[n].cBase};
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
  let PANE="tpl";
  // ids written out literally, not built from the pane name — a concatenated id
  // is invisible to anything that greps the source (tests/test_web.py checks it)
  function showPane(which){
    PANE=which;
    const on=(el,yes)=>{ if(el) el.className="mtab"+(yes?" on":""); };
    const show=(el,yes)=>{ if(el) el.hidden=!yes; };
    show($("#pane-enemy"), which==="enemy");
    show($("#pane-skill"), which==="skill");
    show($("#pane-passive"), which==="passive");
    show($("#pane-gear"), which==="gear");
    show($("#pane-cost"), which==="cost");
    show($("#pane-unit"),  which==="unit");
    show($("#pane-tpl"),   which==="tpl");
    on($("#ptab-enemy"), which==="enemy");
    on($("#ptab-skill"), which==="skill");
    on($("#ptab-passive"), which==="passive");
    on($("#ptab-gear"), which==="gear");
    on($("#ptab-cost"), which==="cost");
    on($("#ptab-unit"),  which==="unit");
    on($("#ptab-tpl"),   which==="tpl");
    // the patch/JSON/retail actions describe the enemy tables specifically
    show($("#enemyActions"), which==="enemy");
    if(which==="skill") loadSkill();
    if(which==="passive") loadPassive();
    if(which==="gear") loadGear();
    if(which==="cost") loadCost();
    if(which==="unit") loadUnit();
    // the preview is against what is staged RIGHT NOW, so it is recomputed on
    // entry rather than cached — edits made in another pane change the answer
    if(which==="tpl") paintTemplate();
  }

  // ---- collapsible sections -------------------------------------------------
  // The enemy card carries six blocks of controls and a page of prose that
  // explains them. Shown all at once it buries the thing most people opened the
  // tab to do — pick an enemy and change a number — under several screens of
  // scrolling. Each block is a <details> instead, so the card opens as a short
  // list of headings you expand when you need them.
  //
  // Open/closed is remembered per SECTION, not per enemy. loadEnemy() refills
  // the tables inside these elements rather than replacing the elements, so a
  // section stays open while you page through enemies, and localStorage carries
  // that across reloads. Without both, a disclosure control is worse than no
  // disclosure control: you would re-open the same section 125 times.
  //
  // No element ids here on purpose — data-sect only. An id built by
  // concatenation is invisible to anything that greps the source for it, which
  // is the rule the pane tabs already follow.
  const SECTKEY="x2sect";
  let SECTOPEN={};
  try{ SECTOPEN=JSON.parse(localStorage.getItem(SECTKEY)||"{}")||{}; }catch(e){ SECTOPEN={}; }
  function sect(key,title,body,hint){
    return '<details class="sect" data-sect="'+key+'"'+(SECTOPEN[key]?' open':'')+'>'+
      '<summary>'+esc(title)+(hint?'<span class="secthint">'+hint+'</span>':'')+'</summary>'+
      '<div class="sectbody">'+body+'</div></details>';
  }
  function wireSects(){
    document.querySelectorAll("details.sect").forEach(d=>{
      d.addEventListener("toggle",()=>{
        SECTOPEN[d.dataset.sect]=d.open;
        try{ localStorage.setItem(SECTKEY,JSON.stringify(SECTOPEN)); }catch(e){}
      });
    });
  }

  function renderEditor(){
    const opts=enemyKeys().map(optionHtml).join("");
    $("#isoEdit").innerHTML=
      '<nav class="modebar panetabs">'+
      '<button id="ptab-tpl" class="mtab on">Templates</button>'+
      '<button id="ptab-enemy" class="mtab">Enemies</button>'+
      '<button id="ptab-skill" class="mtab">Skills</button>'+
      '<button id="ptab-passive" class="mtab">Passives</button>'+
      '<button id="ptab-gear" class="mtab">Gear</button>'+
      '<button id="ptab-cost" class="mtab">Costs</button>'+
      '<button id="ptab-unit" class="mtab">Units</button>'+
      '</nav>'+
      '<div id="pane-tpl">'+
      '<div class="card"><h2>2 \u00b7 Template</h2>'+
      '<p class="sub" style="margin:0 0 10px">A template is a whole rebalance expressed as '+
      'staged table edits \u2014 pick one, read exactly what it would change, and accept it '+
      'only if you want it. Accepting <b>configures</b>: the edits join your pending changes '+
      'and nothing is written until you Save, so you can walk into any pane and tweak '+
      'individual values on top.</p>'+
      '<div class="toolbar"><label>Template</label> <select id="tplsel"></select>'+
      '<span id="tplcount" class="muted small"></span></div>'+
      '<div id="tpldesc"></div></div>'+
      '<div class="card"><h2>3 \u00b7 Preview</h2>'+
      '<div id="tplprev"></div>'+
      '<div class="toolbar">'+
      '<button id="tplAccept" class="btn primary">\u2713 Accept into pending changes</button>'+
      '<button id="tplReplace" class="btn" disabled>\u21ba Replace my staged changes</button>'+
      '<span id="tplstat" class="muted small"></span></div>'+
      '<p class="note">Nothing above is staged. <b>Accept</b> layers the template on top of '+
      'whatever you have already changed; <b>Replace</b> discards your pending changes first '+
      'and stages the template on its own. Either way the disc is untouched until you Save, '+
      'and Revert all undoes the lot.</p></div>'+
      '</div>'+                              // /pane-tpl
      '<div id="pane-enemy" hidden>'+
      '<div class="card"><h2>2 · Enemy</h2>'+
      '<div class="toolbar"><label>Enemy</label> <select id="esel">'+opts+'</select>'+
      '<span class="findbox"><input type="search" id="esearch" placeholder="find by name, index or id" '+
        'autocomplete="off" spellcheck="false"><button type="button" id="eclear" '+
        'class="chip mini" title="Clear search" aria-label="Clear search">✕</button></span>'+
      '<span id="ecount" class="muted small"></span>'+
      '<label style="margin-left:8px"><input type="checkbox" id="ebak"> back up ISO first</label>'+
      '</div>'+
      '<div id="eflags" class="note"></div>'+
      '<div id="eretail" class="note"></div>'+
      sect("stats","Stats",
        '<table id="etbl" class="fieldtable"><tbody><tr id="erow"></tr></tbody></table>'+
        '<p class="note">Verified against guide data (74/76 exact matches). Writes only the '+
        'changed bytes back at their exact offsets.</p>',
        "HP, STR, VIT, EATK, EDEF, DEX, EVA, AGL")+
      sect("rewards","Battle rewards",
        '<table class="fieldtable"><tbody><tr id="erow2"></tr></tbody></table>',
        "EXP, SP, CP")+
      sect("drops","Item drops",
        '<table class="fieldtable"><tbody><tr id="erow4"></tr></tbody></table>'+
        '<div id="edrops" class="muted small"></div>'+
        '<p class="note">Two slots per enemy: a common drop and a rare one, each a '+
        'percentage plus the item itself. Both are picked by <b>name</b> — the disc stores '+
        'a category and a 1-based id within it, and the two categories are windows onto one '+
        'unified item table (E.S. gear from id 1, consumables from id 1 of their own base), '+
        'so a bare number is meaningless without knowing which window you are in.</p>'+
        '<p class="note">Changing the category re-bases the id, so the item list reloads '+
        'with it. Drop rates are verified against a strategy guide on 138 of 144 '+
        'comparisons.</p>',
        "common and rare slot")+
      sect("resist","Status resistance",
        '<table class="fieldtable"><tbody><tr id="erow5"></tr></tbody></table>'+
        '<p class="note">Higher resists the status more. Eight of the ten statuses a '+
        'strategy guide publishes map to these bytes at 98–100% agreement; the block has '+
        'three more bytes we have not identified, so they are not shown.</p>',
        "%, higher resists more")+
      sect("affinity","Damage taken, by element",
        '<table class="fieldtable"><tbody><tr id="erow3"></tr></tbody></table>'+
        '<p class="note">'+AFF_NORMAL+'% is normal, below resists, above takes extra, '+
        '<b>0 is immune</b> and <b>negative absorbs</b> (Svarozic takes -200% Fire, i.e. it '+
        'heals for double). Stored as a signed byte &times;'+AFF_SCALE+', so values snap to '+
        AFF_SCALE+'% steps and the usable range is about -640% to +635%. Verified against '+
        '71 guide entries, exact on every one.</p>',
        "%, "+AFF_NORMAL+" is normal")+
      sect("break","Break sequence",
        '<input id="ebrk" type="text" maxlength="'+BRK_SLOTS+'" spellcheck="false" '+
          'autocapitalize="characters" placeholder="e.g. CBB" style="width:8ch;text-transform:uppercase">'+
        '<button type="button" class="restore" id="ebrkrev" title="Restore">↺</button>'+
        '<span id="ebrkinfo" class="muted small"></span>'+
        '<p class="note">The zones you must hit, <b>in order</b>, to Break this enemy — the combo '+
        'loop’s actual gate. Zones are attack heights: <b>A</b> above 3&nbsp;m (○), '+
        '<b>B</b> 1–3&nbsp;m (□), <b>C</b> below 1&nbsp;m (△). Up to '+BRK_SLOTS+' hits; '+
        'clear it to make the enemy unbreakable. Shortening a boss’s 4-hit sequence is the '+
        'single biggest cut to how long its fight drags.</p>',
        "the combo loop’s gate")+
      '</div>'+
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
      sect("brkall","Shorten every Break sequence",
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
        'only if that is what you actually want.</p>',
        "trims hits off the end")+
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
      '<div id="kname" class="kctl"></div>'+
      '<div id="ktarget" class="kctl"></div>'+
      '<div id="kelemBox" class="kctl"></div>'+
      '<div id="krow"></div>'+
      '<div id="kelem" class="note"></div>'+
      '<div id="kretail" class="note"></div>'+
      '<details class="help"><summary>About these fields</summary>'+
      '<p class="note"><b>EP</b> is what the skill costs to cast and <b>Power</b> scales its '+
      'damage or healing — between them they decide whether a skill is worth a turn. '+
      '<b>Element</b> is a bitmask (' + Object.keys(KELEM).sort((a,b)=>KELEM[a]-KELEM[b]).map(n=>esc(n)+" = 0x"+KELEM[n].toString(16).toUpperCase()).join(", ") + '), '+
      'so it pairs with the enemy damage affinities on the Enemies tab. <b>EffPct</b> and '+
      '<b>EffMask</b> drive the status effect a skill applies; they are verified as fields but '+
      'their encoding is only partly decoded, so treat them as advanced.</p>'+
      '<p class="note"><b>Name</b> is rewritten in place, so it must fit the retail name\'s '+
      'bytes — a packed string pool cannot grow. Prose elsewhere in the game that spells the '+
      'old name (menu and tutorial text) is deliberately left alone rather than blind-patched, '+
      'which is how a rename of "Miracle" ends up truncating "Miracle Star".</p>'+
      '<p class="note">All '+skillKeys().length+' verified records are editable: Ether and Double '+
      'skills, Dual techs, every character\'s single techs, E.S. attacks and Special attacks. '+
      'The tech blocks were unlocked by cross-checking a third-party mod\'s published numbers '+
      'against its patch bytes — 71 of 71 matched. Techs cost no EP (their EP field is genuinely '+
      '0); Power, Element and Target work exactly as for ethers.</p></details>'+
      '</div>'+
      '</div>'+                              // /pane-skill
      '<div id="pane-passive" hidden>'+
      '<div class="card"><h2>2 · Passive &amp; equip skills</h2>'+
      '<div class="toolbar"><label>Passive</label> <select id="qsel"></select>'+
      '<span class="findbox"><input type="search" id="qsearch" placeholder="find by name, text or index" '+
        'autocomplete="off" spellcheck="false"><button type="button" id="qclear" '+
        'class="chip mini" title="Clear search" aria-label="Clear search">✕</button></span>'+
      '<span id="qcount" class="muted small"></span></div>'+
      '<div id="qdesc" class="note"></div>'+
      '<div id="qname" class="kctl"></div>'+
      '<div id="qparam" class="kctl"></div>'+
      '<div id="qstat" class="kctl"></div>'+
      '<div id="qnote" class="note"></div>'+
      '<details class="help"><summary>About these fields</summary>'+
      '<p class="note">These are the skills you <b>equip</b> rather than cast — the Guards, the '+
      'Coats, HP/ST Mind, the +2 stat skills, Break B10/B15, Rare+10/+30. Each is a 12-byte '+
      'record, and the field that matters is <b>Param</b>. What it means depends on the record\'s '+
      'kind: for a scalar passive it is the magnitude (Break B10 really does hold 10), and for a '+
      'typed one — the Coats and Guards — it is a bitmask naming which element or status the '+
      'skill resists, shown here as checkboxes rather than a number.</p>'+
      '<p class="note">Roughly a quarter of the passives read <b>zero across the whole effect '+
      'field</b>: Inner Peace, Damage-10, Revenge Power, Combo Boost, Samurai Soul, Rebound and '+
      'others. Their behaviour lives in battle code, not in this table, so there is no number '+
      'here to change and the editor says so instead of offering one that does nothing.</p>'+
      '<p class="note">Verified two independent ways: the Param byte equals the number in the '+
      'skill\'s own description on 20 of the 20 scalar passives that publish one, and on the '+
      'eight Coats it matches the same element bit order the enemy damage affinities use, 8 of '+
      '8. Names are rewritten in place under the same length rule as the Skills tab.</p></details>'+
      '</div>'+
      '</div>'+                              // /pane-passive
      '<div id="pane-gear" hidden>'+
      '<div class="card"><h2>2 \u00b7 E.S. accessories</h2>'+
      '<div class="toolbar"><label>Accessory</label> <select id="gsel"></select>'+
      '<span class="findbox"><input type="search" id="gsearch" placeholder="find by name or text" '+
        'autocomplete="off" spellcheck="false"><button type="button" id="gclear" '+
        'class="chip mini" title="Clear search" aria-label="Clear search">\u2715</button></span>'+
      '<span id="gcount" class="muted small"></span></div>'+
      '<div id="gdesc" class="note"></div>'+
      '<div id="gparam" class="kctl"></div>'+
      '<div id="gstat" class="kctl"></div>'+
      '<div id="gnote" class="note"></div>'+
      '<details class="help"><summary>About these fields</summary>'+
      '<p class="note">The gear your E.S. units equip \u2014 Auxiliary Armor, the EF Circuits, the '+
      'four Anti-element Armors, the thirteen G-guards. Same 12-byte record as a passive, so '+
      '<b>Param</b> works the same way: a magnitude for the scalar ones, a bitmask for the '+
      'typed ones. Turn Auxiliary Armor A\u2019s +30 Arm into +90, or repoint an Anti-Fire Armor '+
      'at Ice.</p>'+
      '<p class="note"><b>Names are read-only here.</b> These records point into a numeric pool '+
      'rather than the skill name pool \u2014 E.S. equipment names resolve through menu code, which '+
      'is why the name shown comes from the shipped catalog. Change an effect and the item keeps '+
      'its old name in game; that is exactly what the HardType mod does when it turns Anti-Fire '+
      'Armor into a +20 POW accessory.</p>'+
      '<p class="note">Verified three ways: the retail effects match what the catalog\u2019s own '+
      'descriptions predict (Arm +30, Edef +20, Agility +1, the four element armors) on 9 of 9 '+
      'anchors; the thirteen G-guards carry the same status mask <i>and</i> kind byte as their '+
      'non-G passive twins, 10 of 10 checkable; and every one of the 11 records HardType patches '+
      'here matches the exact values its readme publishes for its rebalanced accessories.</p>'+
      '</details></div>'+
      '</div>'+                              // /pane-gear
      '<div id="pane-cost" hidden>'+
      '<div class="card"><h2>2 \u00b7 Skill purchase costs</h2>'+
      '<div class="toolbar"><label>Skill</label> <select id="csel"></select>'+
      '<span class="findbox"><input type="search" id="csearch" placeholder="find by name or text" '+
        'autocomplete="off" spellcheck="false"><button type="button" id="cclear" '+
        'class="chip mini" title="Clear search" aria-label="Clear search">\u2715</button></span>'+
      '<span id="ccount" class="muted small"></span></div>'+
      '<div id="cdesc" class="note"></div>'+
      '<div id="crow" class="kctl"></div>'+
      '<div id="cnote" class="note"></div>'+
      '<details class="help"><summary>About these fields</summary>'+
      '<p class="note">What each skill costs in <b>Skill Points</b> to learn from the class tree \u2014 '+
      'the other half of skill pacing from the SP the Enemies tab hands out. All '+CCOUNT+' '+
      'purchasable skills, grouped by the type the game itself sorts them into: <b>auto</b> skills '+
      '(always on once learned), <b>equip</b> skills (they take a slot), and <b>ether</b> skills.</p>'+
      '<p class="note">Not every skill appears. The Erde Kaiser family and Burst Veil are quest '+
      'rewards rather than purchases, and Swimsuit is not for sale \u2014 so 112 records cover the '+
      'catalog\u2019s purchasable skills exactly.</p>'+
      '<p class="note">Verified against the walkthrough\u2019s class tree: every record\u2019s cost equals '+
      'the SP that guide publishes for the skill this editor names, <b>112 of 112</b>, across all '+
      'three types. A record stores a <i>(type, id)</i> pair rather than a catalog index; ether ids '+
      'are the catalog index plus one, and the auto/equip band shares a single id space \u2014 which '+
      'the disc confirms independently with an auto-skill flag bit in each passive record.</p>'+
      '<p class="note">This is the one table whose disc-2 copy is <b>not</b> the usual 0x800 lower; '+
      'it sits at its own base. Both are written, so a cost change survives the disc swap.</p>'+
      '</details></div>'+
      '</div>'+                              // /pane-cost
      '<div id="pane-unit" hidden>'+
      '<div class="card"><h2>2 · Unit</h2>'+
      '<div class="toolbar"><label>Unit</label> <select id="usel"></select>'+
      '<span id="ucount" class="muted small"></span></div>'+
      '<div id="udesc" class="note"></div>'+
      '<div id="urow"></div>'+
      '<div class="affbox"><div class="fl">Damage affinities</div>'+
        '<div id="urowAff"></div>'+
        '<p class="note">100% is normal, lower resists, higher takes extra, <b>0% is immune</b> '+
        'and <b>negative absorbs</b>. Stored as a signed byte ×5, so values move in 5% steps — '+
        'the same block, at the same offset, as the enemy affinities.</p>'+
        '<p class="note">Retail leaves every unit flat at 100% on all eight, so nothing '+
        'cross-checks that the game reads this block for player characters the way it '+
        'demonstrably does for enemies. The offsets are verified; the behaviour is inferred '+
        'from the shared record structure. Worth knowing before you rely on it.</p>'+
      '</div>'+
      '<div id="uretail" class="note"></div>'+
      '<details class="help"><summary>About the unit table</summary>'+
      '<p class="note">These are the values a <b>new game</b> hands each character and E.S. '+
      'unit — the save format copies these values in, which is how the table was '+
      'verified: a just-joined character\'s save stats match this record exactly. Raising a '+
      'stat here raises where the character <i>starts</i>; an existing save keeps the values '+
      'it already copied (edit those in the Save Editor).</p>'+
      '<p class="note">The three spare slots between the humans and the E.S. units are real '+
      'records the game ships empty. They are shown for completeness and there is no reason '+
      'to touch them.</p></details>'+
      '</div>'+
      '</div>'+                              // /pane-unit
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
      '<button id="pPpf" class="btn">⬆ Import .ppf…</button>'+
      '<input type="file" id="ppfFile" accept=".ppf" hidden>'+
      '<span id="enemyActions" class="barGroup">'+
      '<button id="pExport" class="btn">⬇ Export patch…</button>'+
      '<button id="pImport" class="btn">⬆ Import patch…</button>'+
      '<button id="tExport" class="btn">⬇ Export JSON…</button>'+
      '<button id="tImport" class="btn">⬆ Import JSON…</button>'+
      '<button id="pRestore" class="btn">Stage restore</button>'+
      '</span>'+
      '<span class="sep"></span>'+
      '<button id="pDiff" class="btn">Compare to retail</button>'+
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
    $("#ptab-passive").onclick=()=>showPane("passive");
    $("#ptab-unit").onclick=()=>showPane("unit");
    $("#qsel").onchange=loadPassive;
    $("#qsearch").addEventListener("input",paintPassiveList);
    $("#qsearch").addEventListener("keydown",e=>{
      if(e.key==="Escape"){ $("#qsearch").value=""; paintPassiveList(); }
      if(e.key==="Enter"){ e.preventDefault(); loadPassive(); }
    });
    $("#qclear").onclick=()=>{ $("#qsearch").value=""; paintPassiveList(); $("#qsearch").focus(); };
    paintPassiveList();
    $("#ptab-gear").onclick=()=>showPane("gear");
    $("#gsel").onchange=loadGear;
    $("#gsearch").addEventListener("input",paintGearList);
    $("#gsearch").addEventListener("keydown",e=>{
      if(e.key==="Escape"){ $("#gsearch").value=""; paintGearList(); }
      if(e.key==="Enter"){ e.preventDefault(); loadGear(); }
    });
    $("#gclear").onclick=()=>{ $("#gsearch").value=""; paintGearList(); $("#gsearch").focus(); };
    paintGearList();
    $("#ptab-cost").onclick=()=>showPane("cost");
    $("#csel").onchange=loadCost;
    $("#csearch").addEventListener("input",paintCostList);
    $("#csearch").addEventListener("keydown",e=>{
      if(e.key==="Escape"){ $("#csearch").value=""; paintCostList(); }
      if(e.key==="Enter"){ e.preventDefault(); loadCost(); }
    });
    $("#cclear").onclick=()=>{ $("#csearch").value=""; paintCostList(); $("#csearch").focus(); };
    paintCostList();
    $("#usel").onchange=loadUnit;
    paintUnitList();
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
    $("#erev").onclick=()=>{S.buf.set(S.orig);R.buf.set(R.orig);K.buf.set(K.orig);C.buf.set(C.orig);
      U.buf.set(U.orig);TX.buf.set(TX.orig);
      loadEnemy();loadSkill();loadPassive();loadGear();loadCost();loadUnit();epending();};
    $("#esave").onclick=saveISO;
    $("#sclApply").onclick=()=>stageRebalance(readScales());
    document.querySelectorAll("#profRow .prof").forEach(b=>b.onclick=()=>applyProfile(b.dataset.p));
    $("#ptab-tpl").onclick=()=>showPane("tpl");
    const tsel=$("#tplsel");
    if(tsel){
      tsel.innerHTML=TEMPLATES.map(t=>'<option value="'+t.id+'">'+esc(t.name)+'</option>').join("");
      tsel.onchange=paintTemplate;
    }
    $("#tplAccept").onclick=()=>{const t=templateById($("#tplsel").value); if(t) acceptTemplate(t,false);};
    $("#tplReplace").onclick=()=>{const t=templateById($("#tplsel").value); if(t) acceptTemplate(t,true);};
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
    $("#pPpf").onclick=()=>$("#ppfFile").click();
    $("#ppfFile").onchange=e=>{const f=e.target.files[0]; e.target.value=""; if(f)importPPF(f);};
    $("#pImport").onclick=()=>$("#pFile").click();
    $("#pFile").onchange=e=>{const f=e.target.files[0]; e.target.value=""; if(f)importPatch(f);};
    $("#pDiff").onclick=showRetailDiff;
    $("#pRestore").onclick=stageRestore;
    checkPristine();
    loadEnemy();
    wireSects();
    // The markup above already renders with Templates selected; going through
    // showPane() as well is what hides #enemyActions and paints the preview, so
    // the opening state is produced by the same code path as every tab click
    // rather than by markup that has to agree with it.
    showPane("tpl");
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
    $("#erow2").innerHTML=RFIELDS.map(([l,o,w])=>
      cellHtml(l,o,w,get(R,i,o,w),getOrig(R,i,o,w))).join("");
    $("#erow5").innerHTML=RFIELDS_RES.map(([l,o,w])=>
      cellHtml(l,o,w,get(S,i,o,w),getOrig(S,i,o,w))).join("");
    // Rates stay numeric; category and item id become named dropdowns. A bare
    // "2" in an ITEM ID box is a number you have to go and look up, and looking
    // it up needs the base-40 offset — the exact thing the editor already knows.
    const dsel=(l,o,w,opts)=>{
      const cur=get(R,i,o,w), def=getOrig(R,i,o,w);
      const known=opts.some(([v])=>v===cur);
      return '<td><div class="fl">'+l+'</div><span><select data-f="'+l+'" data-o="'+o+
        '" data-w="'+w+'" data-def="'+def+'"'+(cur!==def?' class="changed"':'')+'>'+
        opts.map(([v,t])=>'<option value="'+v+'"'+(v===cur?' selected':'')+'>'+
          esc(t)+'</option>').join("")+
        (known?'':'<option value="'+cur+'" selected>#'+cur+' (unknown)</option>')+
        '</select></span></td>';
    };
    const catOpts=Object.keys(DROPCATS).map(Number).sort((a,b)=>a-b)
      .map(v=>[v,DROPCATS[String(v)]]);
    $("#erow4").innerHTML=DFIELDS.map(([l,o,w])=>{
      if(l==="DropCat"||l==="RareCat") return dsel(l,o,w,catOpts);
      if(l==="DropItem"||l==="RareItem"){
        const cf=DFIELDS.find(x=>x[0]===(l==="DropItem"?"DropCat":"RareCat"));
        const cv=cf?get(R,i,cf[1],cf[2]):0;
        const opts=dropItems(cv);
        return opts.length ? dsel(l,o,w,[[0,"— none —"]].concat(opts))
                           : cellHtml(l,o,w,get(R,i,o,w),getOrig(R,i,o,w));
      }
      return cellHtml(l,o,w,get(R,i,o,w),getOrig(R,i,o,w));
    }).join("");
    document.querySelectorAll("#erow4 select").forEach(sel=>{
      sel.onchange=()=>{
        put(R,i,+sel.dataset.o,+sel.dataset.w,+sel.value);
        // a category change re-bases the item list, so re-render the row
        loadEnemy(); epending();
      };
    });
    $("#erow3").innerHTML=AFIELDS.map(([l,o,w])=>
      cellHtml(l,o,w,affPct(get(S,i,o,w)),affPct(getOrig(S,i,o,w)),true)).join("");
    // type, and whether the game will honour a break sequence at all
    const fl=$("#eflags");
    if(fl) fl.innerHTML="id <b>"+eid+"</b>"+(eid>=BOSS_ID_MIN?" · boss":"")+
      " · type <b>"+esc(enemyType(i))+"</b> · "+
      (canBreak(i)
        ? "breakable"
        : noZone(i)
          ? "<b>cannot be broken</b> — zone targeting is off for this enemy"+
            (breakSeq(i)?" , so its <code>"+esc(breakSeq(i))+"</code> sequence is inert":"")
          : "<b>cannot be broken</b> — it has no Break sequence");

    // how this record compares with an unmodified disc
    const off=retailDiffs(i);
    $("#eretail").innerHTML = off.length
      ? '<span class="verdict off">● '+off.map(d=>esc(d.label)+" "+esc(d.cur)+
          " (retail "+esc(d.van)+")").join(" · ")+'</span>'
      : '<span class="verdict ok">✓ matches retail</span>';
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

  // ---- skill name text ------------------------------------------------------
  // Budget comes from the RETAIL name in the catalog, never from the buffer's
  // current bytes: reading it live looks right and is wrong the moment anyone
  // renames, because shortening the name moves the terminator and the field can
  // then never be restored to its original length.
  const nameOff=(i)=>{const v=skillInfo(i); return v&&v.nameOff;};
  const nameBudget=(i)=>{const v=skillInfo(i);
    return v&&v.name!==undefined ? v.name.length+1 : 0;};
  // One reader over an arbitrary byte array — the shape readPassiveName already
  // uses. Two near-identical copies is how the live and baseline readers drift,
  // and the Templates preview needs a third baseline (what is staged right now)
  // that neither of them could express.
  function readName(i,arr){
    const off=nameOff(i); if(!off||!TX) return null;
    const src=arr||TX.buf, at=off-TX.base, n=nameBudget(i);
    if(at<0||at+n>src.length) return null;
    let out="";
    for(let k=0;k<n-1;k++){ const c=src[at+k]; if(!c) break; out+=String.fromCharCode(c); }
    return out;
  }
  const readNameOrig=(i)=>readName(i,TX?TX.orig:null);
  function writeName(i,text){
    const off=nameOff(i); if(!off||!TX) return false;
    const at=off-TX.base, n=nameBudget(i);
    const bytes=[];
    for(const ch of String(text)){ const c=ch.charCodeAt(0); bytes.push(c>255?63:c); }
    if(bytes.length+1>n) return false;
    // pad the whole budget, so a shorter name leaves no fragment of the old one
    for(let k=0;k<n;k++) TX.buf[at+k] = k<bytes.length ? bytes[k] : 0;
    return true;
  }

  // A passive record carries BOTH its name and description pointers, so its
  // real budget is the gap between them — which is what the game itself uses,
  // and is usually more room than the retail name's length. That difference is
  // not academic: the HardType mod renames Damage-10 to "Prism Coat", one byte
  // longer than the retail name, and shortens the description to pay for it.
  // Under the catalog-length budget that name reads back as "Prism Coa".
  const passivePtrs=(i)=>{
    const at=passiveOff(i);
    if(at<0) return null;
    const nOff=getAt(TX,at,2), dOff=getAt(TX,at+2,2);
    const base=PTABLES[String(PRIMARY||1)];
    if(dOff<=nOff) return null;                 // never trust a crossed pair
    return {at:base+nOff-TX.base, budget:dOff-nOff};
  };
  function readPassiveName(i,src){
    const p=passivePtrs(i); if(!p) return null;
    const buf=src||TX.buf;
    if(p.at<0||p.at+p.budget>buf.length) return null;
    let out="";
    for(let k=0;k<p.budget-1;k++){ const c=buf[p.at+k]; if(!c) break; out+=String.fromCharCode(c); }
    return out;
  }
  function writePassiveName(i,text){
    const p=passivePtrs(i); if(!p) return false;
    const bytes=[];
    for(const ch of String(text)){ const c=ch.charCodeAt(0); bytes.push(c>255?63:c); }
    if(bytes.length+1>p.budget) return false;
    if(p.at<0||p.at+p.budget>TX.buf.length) return false;
    for(let k=0;k<p.budget;k++) TX.buf[p.at+k] = k<bytes.length ? bytes[k] : 0;
    return true;
  }

  // ---- skills pane --------------------------------------------------------
  function skillMatches(i,q){
    if(!q) return true;
    const v=skillInfo(i)||{};
    const hay=[String(i),v.name||"",v.desc||"",v.target||"",skillBlockName(i)]
      .join(" ").toLowerCase();
    return q.split(/\s+/).filter(Boolean).every(t=>hay.indexOf(t)>=0);
  }
  function paintPassiveList(){
    const sel=$("#qsel"); if(!sel) return;
    const q=($("#qsearch").value||"").trim().toLowerCase();
    const prev=sel.value;
    const keys=passiveKeys().filter(i=>skillMatches(i,q));
    $("#qcount").textContent=q?(keys.length+" of "+PCOUNT):(PCOUNT+" editable");
    if(!keys.length){
      sel.innerHTML='<option value="">no match</option>'; sel.disabled=true;
      $("#qdesc").textContent="No passive matches “"+q+"”.";
      $("#qname").innerHTML=$("#qparam").innerHTML=$("#qstat").innerHTML="";
      $("#qnote").textContent=""; return;
    }
    sel.disabled=false;
    sel.innerHTML=keys.map(i=>'<option value="'+i+'">'+String(i).padStart(3,"0")+
      " · "+esc(skillName(i))+'</option>').join("");
    if(keys.indexOf(+prev)>=0) sel.value=prev; else loadPassive();
  }
  function loadPassive(){
    const sel=$("#qsel"); if(!sel||sel.disabled||sel.value==="") return;
    const i=+sel.value, base=passiveOff(i);
    const v=skillInfo(i)||{};
    if(base<0){
      $("#qdesc").textContent="No passive record addressable — open a disc first.";
      $("#qname").innerHTML=$("#qparam").innerHTML=$("#qstat").innerHTML="";
      $("#qnote").textContent=""; return;
    }
    const kind=passiveKind(i);
    $("#qdesc").innerHTML='<div class="rechead"><b>'+esc(v.name||("skill "+i))+'</b>'+
      ' <span class="sub">'+esc(passiveKindText(kind))+'</span></div>'+
      (v.target?'<div class="note">'+esc(v.target)+'</div>':'');

    const ptrs=passivePtrs(i);
    const cur=readPassiveName(i), budget=ptrs?ptrs.budget-1:0;
    $("#qname").innerHTML = cur===null
      ? '<div class="fl">Name</div><span class="muted small">not renameable</span>'
      : '<div class="fl">Name</div><input type="text" id="qnameIn" maxlength="'+budget+
        '" value="'+esc(cur)+'"><span class="muted small" id="qnameHint"></span>';
    if(cur!==null){
      const inp=$("#qnameIn"), hint=$("#qnameHint"), was=readPassiveName(i,TX.orig);
      const paint=()=>{
        const left=budget-inp.value.length;
        hint.textContent=left+" of "+budget+" left"+
          (was!==null&&inp.value!==was?"  ·  on disc: "+was:"");
        inp.classList.toggle("changed", inp.value!==was);
      };
      inp.oninput=()=>{ if(writePassiveName(i,inp.value)){ paint(); epending(); } };
      paint();
    }

    renderEffect({base:base, kind:kind, prefix:"q", what:"passive",
                  renameable:true, reload:loadPassive});
  }

  // Shared by the Passives and Gear panes: both are the same 12-byte record, so
  // one renderer means the two panes cannot drift in how they read a mask.
  function renderEffect(o){
    const P=$("#"+o.prefix+"param"), S=$("#"+o.prefix+"stat"), N=$("#"+o.prefix+"note");
    // A zero effect field means the behaviour is in battle code, so offering a
    // Param box would be offering a number that changes nothing.
    if(o.kind===0){
      P.innerHTML=''; S.innerHTML='';
      N.innerHTML='<b>No numeric effect on this record.</b> This '+o.what+'\'s behaviour '+
        'lives in battle code rather than the table, so there is nothing here to tune.'+
        (o.renameable?' Its name is still editable above.':'');
      return;
    }
    const base=o.base;
    const PP=PFIELDS.find(f=>f[0]==="Param"), PS=PFIELDS.find(f=>f[0]==="StatMask");
    const maskKind=PARAM_IS_MASK[o.kind];
    const pv=getAt(TX,base+PP[1],1), pdef=getOrigAt(TX,base+PP[1],1);
    const statMap=o.statBits||PSTATBITS;
    const bits=(box,cur,map,off)=>{
      box.innerHTML='<div class="fl">'+(map===KELEM?'Resists':'Applies to')+'</div><div class="ebits">'+
        Object.keys(map).sort((a,b)=>map[a]-map[b]).map(n=>
          '<label class="ebit"><input type="checkbox" data-bit="'+map[n]+'"'+
          ((cur&map[n])?' checked':'')+'>'+esc(n)+'</label>').join("")+
        '<span class="muted small">0x'+cur.toString(16).toUpperCase()+'</span></div>'+
        (cur!==getOrigAt(TX,base+off,1)?' <span class="pill dirty">changed</span>':'');
      box.querySelectorAll("input").forEach(cb=>cb.onchange=()=>{
        let m=getAt(TX,base+off,1);
        m = cb.checked ? (m | +cb.dataset.bit) : (m & ~(+cb.dataset.bit));
        putAt(TX,base+off,1,m&0xFF); o.reload(); epending();
      });
    };
    if(maskKind==="element"){
      bits(P,pv,KELEM,PP[1]);
    }else{
      P.innerHTML='<div class="fl">'+(maskKind==="status"?'Status mask':'Param')+'</div>'+
        '<input type="number" id="'+o.prefix+'paramIn" min="0" max="255" value="'+pv+
        '" style="width:8ch">'+
        '<span class="muted small">'+(maskKind==="status"
          ? '0x'+pv.toString(16).toUpperCase()+' — a status bitmask, not a percentage'
          : 'retail '+pdef)+'</span>'+
        (pv!==pdef?' <span class="pill dirty">changed</span>':'');
      const inp=$("#"+o.prefix+"paramIn");
      inp.oninput=()=>{ putAt(TX,base+PP[1],1,Math.max(0,Math.min(255,+inp.value||0))); epending(); };
      inp.onchange=()=>o.reload();
    }
    if(o.kind===0x80 && PS) bits(S,getAt(TX,base+PS[1],1),statMap,PS[1]);
    else S.innerHTML='';
    N.textContent = maskKind
      ? "Param is a bitmask on this kind, so it is shown as checkboxes."
      : "Param is this "+o.what+"'s magnitude — the number its own description publishes.";
  }

  // ---- skill costs ---------------------------------------------------------
  // 6-byte records in their own buffer. The record does NOT store a catalog
  // index: it stores (type, id), and the catalog index is derived — ether
  // skills are id-1, and the auto/equip band is id+CDELTA. Auto and equip share
  // one id space, which is why one rule covers both.
  function costOff(k){
    if(!C||!CCOUNT) return -1;
    const at=k*CSTRIDE;
    return (k<0||k>=CCOUNT||at+CSTRIDE>C.buf.length)?-1:at;
  }
  const costType=(k)=>{const o=costOff(k); return o<0?-1:getAt(C,o+CTYPEOFF,1);};
  const costId=(k)=>{const o=costOff(k); return o<0?-1:getAt(C,o+CIDOFF,1);};
  function costSkillIndex(k){
    const t=costType(k), id=costId(k);
    if(id<1) return null;
    if(t===2) return id-1;
    if(t===0||t===1) return id+CDELTA;
    return null;                       // unknown type: name nothing rather than guess
  }
  const costTypeText=(t)=>CTYPES[String(t)]||("type "+t);
  function costMatches(k,q){
    if(!q) return true;
    const i=costSkillIndex(k), v=(i!==null&&skillInfo(i))||{};
    const hay=[String(costId(k)),v.name||"",v.desc||"",v.target||"",costTypeText(costType(k))]
      .join(" ").toLowerCase();
    return q.split(/\s+/).filter(Boolean).every(t=>hay.indexOf(t)>=0);
  }
  const costName=(k)=>{
    const i=costSkillIndex(k);
    const v=i!==null&&skillInfo(i);
    return (v&&v.name)?v.name:("record "+k);
  };
  function paintCostList(){
    const sel=$("#csel"); if(!sel) return;
    const q=($("#csearch").value||"").trim().toLowerCase();
    const prev=sel.value;
    const all=[]; for(let k=0;k<CCOUNT;k++) all.push(k);
    const keys=all.filter(k=>costMatches(k,q));
    $("#ccount").textContent=q?(keys.length+" of "+CCOUNT):(CCOUNT+" purchasable");
    if(!keys.length){
      sel.innerHTML='<option value="">no match</option>'; sel.disabled=true;
      $("#cdesc").textContent="No skill matches \u201c"+q+"\u201d.";
      $("#crow").innerHTML=""; $("#cnote").textContent=""; return;
    }
    sel.disabled=false;
    // grouped by type so the list reads like the in-game shop rather than raw records
    const byType={};
    for(const k of keys){ (byType[costType(k)]=byType[costType(k)]||[]).push(k); }
    sel.innerHTML=Object.keys(byType).sort().map(t=>
      '<optgroup label="'+esc(costTypeText(+t))+'">'+
      byType[t].sort((a,b)=>costId(a)-costId(b)).map(k=>
        '<option value="'+k+'">'+esc(costName(k))+'</option>').join("")+
      '</optgroup>').join("");
    if(keys.indexOf(+prev)>=0) sel.value=prev; else loadCost();
  }
  function loadCost(){
    const sel=$("#csel"); if(!sel||sel.disabled||sel.value==="") return;
    const k=+sel.value, base=costOff(k);
    if(base<0){
      $("#cdesc").textContent="No cost record addressable \u2014 open a disc first.";
      $("#crow").innerHTML=""; $("#cnote").textContent=""; return;
    }
    const i=costSkillIndex(k), v=(i!==null&&skillInfo(i))||{}, t=costType(k);
    const slot=getAt(C,base+CSLOTOFF,1);
    $("#cdesc").innerHTML='<div class="rechead"><b>'+esc(v.name||costName(k))+'</b>'+
      ' <span class="sub">'+esc(costTypeText(t))+
      (slot?' \u00b7 tier slot '+slot:'')+'</span></div>'+
      (v.target?'<div class="note">'+esc(v.target)+'</div>':'');
    const CF=CFIELDS.find(f=>f[0]==="Cost");
    const cv=getAt(C,base+CF[1],CF[2]), cdef=getOrigAt(C,base+CF[1],CF[2]);
    $("#crow").innerHTML='<div class="fl">Skill Points</div>'+
      '<input type="number" id="ccostIn" min="0" max="65535" value="'+cv+'" style="width:10ch">'+
      '<button type="button" class="restore'+(cv!==cdef?' show':'')+'" id="ccostRev" '+
      'title="Restore">\u21ba</button>'+
      '<span class="muted small">on disc '+cdef.toLocaleString()+'</span>'+
      (cv!==cdef?' <span class="pill dirty">changed</span>':'');
    const inp=$("#ccostIn");
    inp.oninput=()=>{ putAt(C,base+CF[1],CF[2],Math.max(0,Math.min(65535,+inp.value||0))); epending(); };
    inp.onchange=()=>loadCost();
    $("#ccostRev").onclick=()=>{ putAt(C,base+CF[1],CF[2],cdef); loadCost(); epending(); };
    $("#cnote").textContent = i===null
      ? "This record's (type, id) pair isn't one this editor can name, so no skill is shown."
      : "";
  }

  // ---- gear pane ----------------------------------------------------------
  function gearMatches(k,q){
    if(!q) return true;
    const v=gearInfo(k)||{};
    const hay=[String(k),v.name||"",v.desc||""].join(" ").toLowerCase();
    return q.split(/\s+/).filter(Boolean).every(t=>hay.indexOf(t)>=0);
  }
  function paintGearList(){
    const sel=$("#gsel"); if(!sel) return;
    const q=($("#gsearch").value||"").trim().toLowerCase();
    const prev=sel.value;
    const all=gearKeys(), keys=all.filter(k=>gearMatches(k,q));
    $("#gcount").textContent=q?(keys.length+" of "+all.length):(all.length+" editable");
    if(!keys.length){
      sel.innerHTML='<option value="">no match</option>'; sel.disabled=true;
      $("#gdesc").textContent="No accessory matches \u201c"+q+"\u201d.";
      $("#gparam").innerHTML=$("#gstat").innerHTML=""; $("#gnote").textContent=""; return;
    }
    sel.disabled=false;
    sel.innerHTML=keys.map(k=>'<option value="'+k+'">'+String(gearEsId(k)).padStart(2,"0")+
      " \u00b7 "+esc(gearName(k))+'</option>').join("");
    if(keys.indexOf(+prev)>=0) sel.value=prev; else loadGear();
  }
  function loadGear(){
    const sel=$("#gsel"); if(!sel||sel.disabled||sel.value==="") return;
    const k=+sel.value, base=gearOff(k);
    if(base<0){
      $("#gdesc").textContent="No gear record addressable — open a disc first.";
      $("#gparam").innerHTML=$("#gstat").innerHTML=""; $("#gnote").textContent=""; return;
    }
    const v=gearInfo(k)||{}, kind=getAt(TX,base+PKINDOFF,1);
    $("#gdesc").innerHTML='<div class="rechead"><b>'+esc(v.name||("gear "+k))+'</b>'+
      ' <span class="sub">'+esc(passiveKindText(kind))+'</span></div>'+
      (v.desc?'<div class="note">'+esc(v.desc)+'</div>':'');
    renderEffect({base:base, kind:kind, prefix:"g", what:"accessory",
                  renameable:false, reload:loadGear, statBits:GSTATBITS});
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
    $("#kdesc").innerHTML='<div class="rechead"><b>'+esc(v.name||("skill "+i))+'</b>'+
      ' <span class="sub">'+esc(skillBlockName(i))+
      (v.target?' · '+esc(v.target):'')+'</span></div>'+
      (v.desc?'<div class="note">'+esc(v.desc)+'</div>':'');
    // Ids are shown as what they mean, not as numbers: Target is a named
    // dropdown and Element is one checkbox per element. A raw byte here is a
    // number you have to go and look up, which is how you end up writing 0x2A
    // when you meant "all allies".
    const cur=readName(i), budget=nameBudget(i)-1;
    $("#kname").innerHTML = cur===null
      ? '<div class="fl">Name</div><span class="muted small">not renameable</span>'
      : '<div class="fl">Name</div><input type="text" id="knameIn" maxlength="'+budget+
        '" value="'+esc(cur)+'"><span class="muted small" id="knameHint"></span>';
    if(cur!==null){
      const inp=$("#knameIn"), hint=$("#knameHint");
      const paint=()=>{
        const left=budget-inp.value.length;
        hint.textContent=left+" of "+budget+" left"+
          (inp.value!==v.name?"  ·  retail: "+v.name:"");
        inp.classList.toggle("changed", inp.value!==readNameOrig(i));
      };
      inp.oninput=()=>{ if(writeName(i,inp.value)){ paint(); epending(); } };
      paint();
    }

    const KTGT=KFIELDS.find(f=>f[0]==="Target"), KELE=KFIELDS.find(f=>f[0]==="Element");
    const tv=getAt(K,base+KTGT[1],1), tdef=getOrigAt(K,base+KTGT[1],1);
    const known=Object.keys(KTARGETS).map(Number);
    if(known.indexOf(tv)<0) known.push(tv);          // keep an unverified value selectable
    $("#ktarget").innerHTML='<div class="fl">Target</div><select id="ktsel">'+
      known.sort((a,b)=>a-b).map(v=>'<option value="'+v+'"'+(v===tv?' selected':'')+'>'+
        esc(targetText(v))+'</option>').join("")+'</select>'+
      (tv!==tdef?' <span class="pill dirty">changed</span>':'');
    $("#ktsel").onchange=()=>{ putAt(K,base+KTGT[1],1,+$("#ktsel").value);
                               loadSkill(); epending(); };

    const ev=getAt(K,base+KELE[1],KELE[2]);
    $("#kelemBox").innerHTML='<div class="fl">Element</div><div class="ebits">'+
      Object.keys(KELEM).sort((a,b)=>KELEM[a]-KELEM[b]).map(n=>
        '<label class="ebit"><input type="checkbox" data-bit="'+KELEM[n]+'"'+
        ((ev&KELEM[n])?' checked':'')+'>'+esc(n)+'</label>').join("")+
      '<span class="muted small">0x'+ev.toString(16).toUpperCase()+'</span></div>';
    document.querySelectorAll("#kelemBox input").forEach(cb=>cb.onchange=()=>{
      let m=getAt(K,base+KELE[1],KELE[2]);
      m = cb.checked ? (m | +cb.dataset.bit) : (m & ~(+cb.dataset.bit));
      putAt(K,base+KELE[1],KELE[2], m>>>0); loadSkill(); epending();
    });

    const plain=KFIELDS.filter(f=>f[0]!=="Target" && f[0]!=="Element");
    $("#krow").innerHTML='<table class="fieldtable"><tbody><tr>'+plain.map(([l,o,w])=>
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
    const em=KFIELDS.find(x=>x[0]==="Element"), tg=KFIELDS.find(x=>x[0]==="Target");
    if(em&&tg) $("#kelem").textContent=
      targetText(getAt(K,base+tg[1],1))+" · "+elementText(getAt(K,base+em[1],em[2]));
    const off=[];
    for(const [l,o,w] of KFIELDS){
      const van=skillRetail(i,l); if(van===undefined) continue;
      const cur=getAt(K,base+o,w); if(cur===van) continue;
      const fmt = l==="Target" ? targetText : l==="Element" ? elementText
                                              : (v)=>v.toLocaleString();
      off.push(esc(l)+" "+esc(String(fmt(cur)))+" (retail "+esc(String(fmt(van)))+")");
    }
    $("#kretail").innerHTML = off.length
      ? '<span class="verdict off">● '+off.join(" · ")+'</span>'
      : '<span class="verdict ok">✓ matches retail</span>';
  }

  // ---- units pane ---------------------------------------------------------
  const unitInfo=(i)=>(UNITS&&UNITS[String(i)])||null;
  const unitName=(i)=>{const v=unitInfo(i); return v&&v.name?v.name:("unit "+i);};
  function paintUnitList(){
    const sel=$("#usel"); if(!sel) return;
    sel.innerHTML=Array.from({length:UCOUNT},(_,i)=>
      '<option value="'+i+'">'+String(i).padStart(2,"0")+' · '+esc(unitName(i))+'</option>'
    ).join("");
    $("#ucount").textContent=UCOUNT+" records (7 characters, 3 E.S., 5 spares)";
  }
  function loadUnit(){
    const sel=$("#usel"); if(!sel||!U) return;
    const i=+sel.value||0;
    const v=unitInfo(i)||{};
    const uid=getAt(U,i*USTRIDE+((TABLES.unit||{}).idOff||82), 2);
    $("#udesc").innerHTML='<div class="rechead"><b>'+esc(unitName(i))+'</b> '+
      '<span class="sub">'+(uid>=100?'E.S. unit':uid?'character':'spare — empty on a retail disc')+
      ' · id '+uid+'</span></div>';
    $("#urow").innerHTML='<table class="fieldtable"><tbody><tr>'+UFIELDS.map(([l,o,w])=>
      cellHtml(l,i*USTRIDE+o,w,getAt(U,i*USTRIDE+o,w),getOrigAt(U,i*USTRIDE+o,w)))
      .join("")+'</tr></tbody></table>';
    // signed-byte percentages, so these need the pct flag like enemy affinities
    $("#urowAff").innerHTML='<table class="fieldtable"><tbody><tr>'+UAFIELDS.map(([l,o,w])=>
      cellHtml(l,i*USTRIDE+o,w,affPct(getAt(U,i*USTRIDE+o,w)),
               affPct(getOrigAt(U,i*USTRIDE+o,w)),true))
      .join("")+'</tr></tbody></table>';
    document.querySelectorAll("#urow input, #urowAff input").forEach(inp=>{
      let btn=inp.nextElementSibling;
      if(!btn||!btn.classList.contains("restore")){btn=document.createElement("button");
        btn.type="button";btn.className="restore";btn.textContent="↺";inp.after(btn);}
      const refresh=()=>{
        putAt(U,+inp.dataset.o,+inp.dataset.w,
              inp.dataset.pct ? affByte(inp.value) : +inp.value);
        const ch=String(inp.value)!==String(inp.getAttribute("data-def"));
        inp.classList.toggle("changed",ch);btn.classList.toggle("show",ch);
        paintUnitRetail(i); epending();};
      inp.addEventListener("input",refresh);
      btn.onclick=()=>{inp.value=inp.getAttribute("data-def");refresh();};refresh();
    });
    paintUnitRetail(i);
  }
  function paintUnitRetail(i){
    const v=unitInfo(i); const el=$("#uretail"); if(!el) return;
    if(!v){ el.textContent=""; return; }
    const off=[];
    for(const [l,o,w] of UFIELDS.concat(UAFIELDS)){
      const van=v[l]; if(van===undefined) continue;
      const cur=getAt(U,i*USTRIDE+o,w); if(cur===van) continue;
      const aff=UAFIELDS.some(x=>x[0]===l);
      off.push(esc(l)+" "+(aff?affPct(cur)+"%":cur.toLocaleString())+
               " (retail "+(aff?affPct(van)+"%":van.toLocaleString())+")");
    }
    el.innerHTML=off.length
      ? '<span class="verdict off">● '+off.join(" · ")+'</span>'
      : '<span class="verdict ok">✓ matches retail</span>';
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
    for(const T of [S,R,K,U,TX,C]){for(let i=0;i<T.buf.length;i++)if(T.buf[i]!==T.orig[i]){n++;while(i<T.buf.length&&T.buf[i]!==T.orig[i])i++;}}
    return n;}
  function epending(){const n=diffCount();const b=$("#ebadge");if(b)b.textContent=n?"("+n+")":"";
    const s=$("#esave"),r=$("#erev");if(s)s.disabled=!n;if(r)r.disabled=!n;
    const d=$("#edirty");
    if(d){ d.hidden=!n; d.textContent=n?("● "+n+" unsaved change"+(n===1?"":"s")):""; }}
  function diffRuns(T){const runs=[];let i=0;while(i<T.buf.length){if(T.buf[i]!==T.orig[i]){let j=i;
    while(j<T.buf.length&&T.buf[j]!==T.orig[j])j++;runs.push([i,j]);i=j;}else i++;}return runs;}

  // One row renderer for both comparisons — "staged vs the disc" and "yours vs
  // retail" are the same shape and were drifting apart as two copies.
  // A name is only comparable when the RETAIL name is representable in the
  // single-byte encoding these buffers are read as. The placeholder records are
  // named 予備 ("spare") in the catalog and read back from the disc as latin1
  // mojibake — a difference in encoding, not in the disc. Reported as an edit it
  // put a permanent false positive at the top of every retail comparison.
  const latin1=(v)=>typeof v==="string" && [...v].every(c=>c.charCodeAt(0)<256);

  const revRow=(l,a,b)=>'<div class="revrow"><span class="rl">'+esc(l)+'</span><span class="ro">'+
    esc(a)+'</span>→ <span class="rn">'+esc(b)+'</span></div>';
  const revGrp=(t)=>'<div class="revgrp">'+esc(t)+'</div>';

  // What the confirm dialog lists before anything is written, and what the
  // Templates preview lists before anything is staged. It must cover every
  // field a Save actually writes — it used to show stats, affinities and
  // rewards only, so a break-sequence or drop change went to disc without ever
  // appearing in the review it was supposedly reviewed in.
  //
  // `pick(T)` chooses the BASELINE each buffer is compared against, which is the
  // only thing separating the two callers: Save compares against the disc as it
  // was opened (T.orig), the Templates preview against what is staged right now.
  // Same rows, same grouping, same rendering — one function, or the two drift.
  function changeRows(pick){
    pick = pick || ((T)=>T.orig);
    const views=new Map();
    const wasAt=(T,a,w)=>{
      let v=views.get(T);
      if(!v){ const arr=pick(T)||T.orig; v={arr,dv:new DataView(arr.buffer)}; views.set(T,v); }
      return w===4?v.dv.getUint32(a,true):w===2?v.dv.getUint16(a,true):v.arr[a];
    };
    const was=(T,i,off,w)=>wasAt(T,i*(T===S?STRIDE:RSTRIDE)+off,w);
    const wasArr=(T)=>{ wasAt(T,0,1); return views.get(T).arr; };
    const row=revRow, grp=revGrp;
    let rows="",count=0;
    for(let i=0;i<COUNT && count<400;i++){
      let cells="";
      // break slots collapse into one readable row, as in the retail comparison
      let ob=""; for(let n=0;n<BRK_SLOTS;n++){const y=ZSYM[was(S,i,BRK_OFF+n,1)];if(!y)break;ob+=y;}
      const nb=breakSeq(i);
      if(ob!==nb) cells+=row("Break",ob||"—",nb||"—");
      for(const [T,FL] of [[S,SFIELDS],[S,AFIELDS],[S,RFIELDS_RES],[R,RFIELDS],[R,DFIELDS]]){
        for(const [l,o,w] of FL){
          const a=was(T,i,o,w),b=get(T,i,o,w);
          if(a!==b) cells+=row(l,a.toLocaleString(),b.toLocaleString());
        }
      }
      const zs=ZFIELDS.find(x=>x[0]==="Zones");
      if(zs){
        const a=was(S,i,zs[1],zs[2]), b=get(S,i,zs[1],zs[2]);
        if(a!==b) cells+=row("Zones",zoneMaskText(a)||"—",zoneMaskText(b)||"—");
      }
      if(cells){rows+=grp(String(i).padStart(3,"0")+" · "+(cat[i]?cat[i].name:i))+cells;count++;}
    }
    for(let i=0;i<UCOUNT;i++){
      let cells="";
      for(const [l,o,w] of UFIELDS.concat(UAFIELDS)){
        const a=wasAt(U,i*USTRIDE+o,w), b=getAt(U,i*USTRIDE+o,w);
        if(a===b) continue;
        const aff=UAFIELDS.some(x=>x[0]===l);
        cells+=row(l, aff?affPct(a)+"%":a.toLocaleString(),
                      aff?affPct(b)+"%":b.toLocaleString());
      }
      if(cells) rows+=grp("unit "+String(i).padStart(2,"0")+" · "+unitName(i))+cells;
    }
    for(const i of skillKeys()){
      const base=skillOff(i); if(base<0) continue;
      let cells="";
      const nOld=readName(i,wasArr(TX)), nNew=readName(i);
      if(nOld!==null && nOld!==nNew) cells+=row("Name",nOld||"—",nNew||"—");
      for(const [l,o,w] of KFIELDS){
        const a=wasAt(K,base+o,w), b=getAt(K,base+o,w);
        if(a!==b) cells+=row(l,a.toLocaleString(),b.toLocaleString());
      }
      if(cells) rows+=grp("skill "+String(i).padStart(3,"0")+" · "+skillName(i))+cells;
    }
    // The three panes below were missing entirely: staging the HardType preset
    // changes passive effects, E.S. accessories and skill costs, and the write
    // review listed none of them. A review that omits a pane is worse than no
    // review — it reads as "that is everything".
    for(const i of passiveKeys()){
      const base=passiveOff(i); if(base<0) continue;
      let cells="";
      const nOld=readPassiveName(i,wasArr(TX)), nNew=readPassiveName(i);
      if(nOld!==null && nOld!==nNew) cells+=row("Name",nOld||"—",nNew||"—");
      for(const [l,o,w] of PFIELDS){
        const a=wasAt(TX,base+o,w), b=getAt(TX,base+o,w);
        if(a!==b) cells+=row(l,a.toLocaleString(),b.toLocaleString());
      }
      if(cells) rows+=grp("passive "+String(i).padStart(3,"0")+" · "+skillName(i))+cells;
    }
    for(const k of gearKeys()){
      const base=gearOff(k); if(base<0) continue;
      let cells="";
      for(const [l,o,w] of GFIELDS_G){
        const a=wasAt(TX,base+o,w), b=getAt(TX,base+o,w);
        if(a!==b) cells+=row(l,a.toLocaleString(),b.toLocaleString());
      }
      if(cells) rows+=grp("gear "+String(k).padStart(2,"0")+" · "+gearName(k))+cells;
    }
    for(let k=0;k<CCOUNT;k++){
      const base=costOff(k); if(base<0) continue;
      let cells="";
      // Type/Id/Slot are shown as well as Cost: the HardType preset re-prices
      // four ethers by SWAPPING id bytes, which a Cost-only view renders as
      // "nothing changed" on records whose cost is untouched.
      for(const [l,o,w] of CFIELDS.concat([["Type",CTYPEOFF,1],["Id",CIDOFF,1],
                                           ["Slot",CSLOTOFF,1]])){
        const a=wasAt(C,base+o,w), b=getAt(C,base+o,w);
        if(a!==b) cells+=row(l,a.toLocaleString(),b.toLocaleString());
      }
      if(cells) rows+=grp("cost "+String(k).padStart(3,"0")+" · "+costName(k))+cells;
    }
    if(count>=400) rows+='<div class="note">…truncated…</div>';
    return rows;
  }
  const reviewRows=()=>changeRows();

  // Byte runs that differ from a baseline, for the buffers as a whole. diffRuns
  // answers the same question against T.orig only; this one takes the baseline,
  // and it exists to say honestly how much a change touches that the field rows
  // above do NOT model — rewritten skill DESCRIPTIONS, most of all, which live
  // in the text region and belong to no field.
  function runsAgainst(T,base){
    const runs=[]; let i=0;
    while(i<T.buf.length){
      if(T.buf[i]!==base[i]){ let j=i; while(j<T.buf.length&&T.buf[j]!==base[j]) j++; runs.push([i,j]); i=j; }
      else i++;
    }
    return runs;
  }

  // Run `fn` with every edit buffer swapped for a throwaway copy, then put the
  // real ones back. This is what lets the Templates tab answer "what would this
  // template do?" without staging anything: the template is applied to the
  // copies, the preview is rendered off them, and the user's actual pending
  // changes are never touched. Restoring in `finally` matters — a thrown error
  // mid-preview must not leave the editor pointing at scratch buffers.
  function withScratch(fn){
    const bufs=[S,R,K,U,TX,C].filter(Boolean);
    const saved=bufs.map(T=>({T,buf:T.buf,dv:T.dv}));
    for(const T of bufs){ T.buf=T.buf.slice(); T.dv=new DataView(T.buf.buffer); }
    try{ return fn(saved); }
    finally{ for(const x of saved){ x.T.buf=x.buf; x.T.dv=x.dv; } }
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
    for(const [T,base] of [[S,d.sBase],[R,d.rBase],[K,d.kBase],[U,d.uBase],[TX,d.tBase],[C,d.cBase]])
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

  // ---- PPF import -----------------------------------------------------------
  // PPF3.0: "PPF30" magic, 50-byte description, then flags at +56..58
  // (imagetype, blockcheck, undo), records of u64le offset + u8 len + bytes
  // (+ undo bytes when that flag is set). This is the format the HardType mod
  // ships in, and parsing it turns "balance my game like that mod" into: stage
  // every byte the patch writes that lands in a table this editor maps, and
  // say plainly what does not.
  function parsePPF(bytes){
    const b=new Uint8Array(bytes);
    if(String.fromCharCode(...b.slice(0,5))!=="PPF30") throw new Error("not a PPF3.0 patch");
    const blockcheck=b[57], undo=b[58];
    let p=60+(blockcheck?1024:0);
    const dv=new DataView(b.buffer), recs=[];
    while(p+9<=b.length){
      const off=Number(dv.getBigUint64(p,true)); p+=8;
      const n=b[p]; p+=1;
      recs.push({off,data:b.slice(p,p+n)}); p+=n;
      if(undo) p+=n;
    }
    return recs;
  }
  // The edit buffers with their on-disc extents under disc `d`'s layout.
  // TX is LAST on purpose: the skill text span is a bounding box wide enough to
  // contain the enemy and skill tables, so a record may only be attributed to it
  // after every precise table has declined it. With it present, a patch's skill
  // renames, rewritten descriptions and passive-effect edits all stage — the
  // passive/equip table lives inside that span.
  function bufferMap(d){
    const t=ETABLES[String(d)];
    return [[S,t.stats],[R,t.rewards],[K,kBase(d)],[U,UTABLES[String(d)]],
            [C,CTABLES[String(d)]],[TX,TX?TX.base:0]];
  }
  // Stage raw byte records (PPF or embedded preset) into whichever edit
  // buffers they land in, and repaint. Shared by importPPF and acceptTemplate.
  function stageRecords(recs, layout){
    let staged=0, stagedBytes=0, skipped=0, skippedBytes=0;
    for(const {off,data} of recs){
      let hit=false;
      for(const [T,base] of bufferMap(layout)){
        if(off>=base && off+data.length<=base+T.buf.length){
          T.buf.set(data, off-base); hit=true; break;
        }
      }
      if(hit){ staged++; stagedBytes+=data.length; }
      else { skipped++; skippedBytes+=data.length; }
    }
    loadEnemy(); loadSkill(); loadPassive(); loadGear(); loadCost(); loadUnit(); checkBrkPlan(); paintBrkOpts(); epending();
    return {staged,stagedBytes,skipped,skippedBytes};
  }

  // ---- Templates ------------------------------------------------------------
  // A template is a named set of table edits that can be PREVIEWED as a whole
  // and then staged as a whole. The two HardType variants are the first two
  // entries, and the registry is a list rather than two buttons precisely so
  // that community patch files or curated rebalances can join it later without
  // the pane changing shape.
  //
  // web/hardtype.json (generated by Editor/gen_hardtype.py from the mod's own
  // PPFs) already carries the records as disc-1 offsets, the per-disc divergence
  // notes and the count of what was deliberately left out — so an entry here is
  // mostly prose. The buffers are disc-agnostic, so one layout serves both discs.
  let HARDTYPE=null;
  async function loadHardtype(){
    if(HARDTYPE) return HARDTYPE;
    const r=await fetch("hardtype.json",{cache:"no-cache"});
    if(!r.ok) throw new Error("hardtype.json ("+r.status+")");
    HARDTYPE=await r.json(); return HARDTYPE;
  }
  function hexBytes(h){
    const a=new Uint8Array(h.length>>1);
    for(let i=0;i<a.length;i++) a[i]=parseInt(h.substr(i*2,2),16);
    return a;
  }
  // What a template deliberately does NOT carry. This lived in the enemy pane's
  // preset card; it belongs with the template it describes, where someone is
  // actually deciding whether to accept it.
  const HT_EXCLUDED=
    'Nine of the mod\u2019s 661 records are not staged: duplicate copies of a renamed '+
    'skill\u2019s <i>battle caption</i>, which live outside every table and are found by '+
    'scanning the image rather than at a fixed offset. The command line applies '+
    'those (<code>x2patch.py apply-ppf</code> reaches all 661); a browser would have '+
    'to scan the whole 4.6\u00a0GB image to match. The practical effect is that a skill '+
    'renamed here keeps its retail name in battle captions.';
  const TEMPLATES=[
    {id:"hardtype-normal", variant:"normal", name:"HardType v3.9 \u2014 Normal",
     by:"Landon Ray (1945)",
     blurb:'The mod\u2019s standard difficulty. Retunes every enemy\u2019s stats and rewards, '+
       'the skill numbers, unit starting stats, its skill renames and rewritten '+
       'descriptions, its passive/equip effects (Ice Coat becomes STR+4), its E.S. '+
       'accessory rebalance (Anti-Fire Armor becomes a +20 POW part) and its skill '+
       'purchase costs.',
     excluded: HT_EXCLUDED},
    {id:"hardtype-hard", variant:"hard", name:"HardType v3.9 \u2014 Hard",
     by:"Landon Ray (1945)",
     blurb:'The same rebalance with the mod\u2019s harder enemy numbers. Everything the '+
       'Normal variant touches, at the higher difficulty the mod ships as a separate patch.',
     excluded: HT_EXCLUDED},
  ];
  const templateById=(id)=>TEMPLATES.find(t=>t.id===id)||null;

  // {recs, layout, meta} for a template, or throws with something a user can act on.
  async function templateData(t){
    const ht=await loadHardtype();
    const v=(ht.variants||{})[t.variant];
    if(!v||!Array.isArray(v.records)) throw new Error("hardtype.json has no '"+t.variant+"' variant");
    return {recs:v.records.map(([off,hex])=>({off,data:hexBytes(hex)})),
            layout:ht.layout||1, meta:v, source:ht.source||"HardType"};
  }

  // Stage a template's records into throwaway buffers and render what changed.
  // "Preview" here means exactly what the pane claims: nothing is staged, the
  // user's own pending edits are untouched, and the rows are produced by the
  // same changeRows() the write confirmation uses — so what you see before
  // accepting is what you will see before saving.
  function templatePreview(d){
    return withScratch((saved)=>{
      const base=new Map(saved.map(x=>[x.T,x.buf]));
      for(const {off,data} of d.recs){
        for(const [T,b] of bufferMap(d.layout)){
          if(off>=b && off+data.length<=b+T.buf.length){ T.buf.set(data,off-b); break; }
        }
      }
      const rows=changeRows(T=>base.get(T));
      // Byte runs the field rows above cannot name — overwhelmingly the mod's
      // rewritten skill DESCRIPTIONS, which live in the text region and belong
      // to no field. Counting them is the honest way to say "there is more here
      // than the list shows" without inventing rows for text we do not model.
      let runs=0, bytes=0;
      for(const x of saved){
        for(const [a,b2] of runsAgainst(x.T,base.get(x.T))){ runs++; bytes+=b2-a; }
      }
      // How much of the user's own staged work this template would overwrite:
      // a byte that already differed from the disc AND differs again after the
      // template is applied has been taken over by it.
      let clobber=0;
      for(const x of saved){
        const orig=x.T.orig, mine=x.buf, after=x.T.buf;
        for(let n=0;n<after.length;n++)
          if(mine[n]!==orig[n] && after[n]!==mine[n]) clobber++;
      }
      return {rows, runs, bytes, clobber};
    });
  }

  async function acceptTemplate(t, replace){
    let d;
    try{ d=await templateData(t); }
    catch(e){ toastFn("\u2717 "+e.message+" \u2014 the template data didn\u2019t load (offline before first use?)",true); return; }
    if(replace){
      S.buf.set(S.orig);R.buf.set(R.orig);K.buf.set(K.orig);C.buf.set(C.orig);
      U.buf.set(U.orig);TX.buf.set(TX.orig);
    }
    const {staged,stagedBytes}=stageRecords(d.recs, d.layout);
    const how=replace?"replacing everything staged before it":"on top of what was already staged";
    const msg="\u2713 Accepted "+t.name+": "+staged+" record(s), "+stagedBytes+" bytes "+how+
              " \u2014 review & Save";
    $("#estat").textContent=msg; $("#estat").className="status ok";
    const st=$("#tplstat"); if(st) st.textContent=t.name+" accepted \u2014 "+staged+" record(s) staged";
    toastFn(msg);
    paintTemplate();
    if(window.openInfo) await window.openInfo("Template accepted",
      '<div class="note"><b>'+esc(t.name)+'</b> is now part of your pending changes \u2014 '+
      staged+' record(s). <b>Nothing has been written to the disc.</b> Walk into any pane and '+
      'edit individual values on top of it, then Save when you are happy; Revert all undoes '+
      'the template and your edits together.</div>'+
      (d.meta.discNotes&&d.meta.discNotes.length
        ? '<div class="note">\u26a0 '+d.meta.discNotes.map(esc).join("<br>")+'</div>' : ''));
  }

  // Selecting a template renders its preview; it never stages anything. The two
  // buttons are the whole composability answer: a template is presented as a
  // coherent whole, so layering it onto edits you have already made is offered
  // explicitly rather than done silently, and replacing is one click away when
  // that is what you meant.
  let TPLBUSY=false;
  async function paintTemplate(){
    const sel=$("#tplsel"), box=$("#tplprev"), desc=$("#tpldesc");
    if(!sel||!box) return;
    const t=templateById(sel.value);
    if(!t){ box.innerHTML=''; return; }
    if(desc) desc.innerHTML='<div class="note"><b>'+esc(t.name)+'</b> \u2014 by '+esc(t.by)+
      '. '+t.blurb+'</div><div class="note">'+t.excluded+'</div>';
    if(!S||!S.buf){ box.innerHTML='<div class="note">Open a disc first \u2014 a preview compares '+
      'the template against the tables on your image.</div>'; return; }
    if(TPLBUSY) return;
    TPLBUSY=true;
    box.innerHTML='<div class="note"><span class="spinner"></span>Reading the template\u2026</div>';
    let d;
    try{ d=await templateData(t); }
    catch(e){ box.innerHTML='<div class="note">\u2717 '+esc(e.message)+'</div>'; TPLBUSY=false; return; }
    finally{ TPLBUSY=false; }
    const {rows,runs,bytes,clobber}=templatePreview(d);
    const cnt=$("#tplcount");
    if(cnt) cnt.textContent=d.meta.recordCount+" record(s), "+d.meta.byteCount+" bytes";
    const head=rows
      ? '<div class="note">This is what accepting <b>'+esc(t.name)+'</b> would change, '+
        'against what is staged right now (current \u2192 template). '+runs+' byte run(s), '+
        bytes+' byte(s) in total.</div>'
      : '<div class="note">Nothing would change \u2014 every value this template sets is '+
        'already staged or already on your disc.</div>';
    const warn=clobber
      ? '<div class="note">\u26a0 <b>'+clobber+' byte(s) you have already changed would be '+
        'overwritten</b> by this template. Accept layers it on top of your edits; '+
        '\u201cReplace\u201d discards your pending changes first and stages the template alone.</div>'
      : '';
    const notes=(d.meta.discNotes&&d.meta.discNotes.length)
      ? '<div class="note">\u26a0 Per-disc divergence: '+d.meta.discNotes.map(esc).join("<br>")+'</div>'
      : '';
    box.innerHTML=head+warn+notes+
      '<div class="note">Skill <b>descriptions</b> this template rewrites are staged but not '+
      'itemised below \u2014 they are text in the same region rather than named fields, which '+
      'is what the byte-run count above covers.</div>'+
      '<div class="tplrows">'+(rows||'')+'</div>';
    const rep=$("#tplReplace"); if(rep) rep.disabled=!diffCount();
  }

  function ppfCoverage(recs,d){
    let ok=0;
    for(const {off,data} of recs)
      for(const [T,base] of bufferMap(d))
        if(off>=base && off+data.length<=base+T.buf.length){ ok+=data.length; break; }
    return ok;
  }
  async function importPPF(file){
    let recs;
    try{ recs=parsePPF(await file.arrayBuffer()); }
    catch(e){ toastFn("✗ "+e.message,true); return; }
    if(!recs.length){ toastFn("✗ patch contains no records",true); return; }
    // A disc-1 and a disc-2 PPF differ only by the 0x800 table shift, and the
    // edit buffers are disc-agnostic — so interpret the offsets under whichever
    // disc's layout explains more of the patch, and say which one that was.
    const discs=Object.keys(ETABLES).map(Number);
    const best=discs.map(d=>[ppfCoverage(recs,d),d]).sort((a,b)=>b[0]-a[0])[0];
    const [covered,layout]=best;
    if(!covered){ toastFn("✗ nothing in this patch lands in a table this editor maps",true); return; }
    const {staged,stagedBytes,skipped,skippedBytes}=stageRecords(recs,layout);
    const msg="✓ Staged "+staged+" of "+recs.length+" patch records ("+stagedBytes+" bytes) — review & Save";
    $("#estat").textContent=msg; $("#estat").className="status ok";
    toastFn(msg);
    if(window.openInfo) await window.openInfo("PPF import",
      '<div class="note">Read as a disc-'+layout+' patch. <b>'+staged+' record(s) staged</b> into '+
      'the enemy, unit and skill tables — nothing is written until you review and Save.</div>'+
      (skipped?'<div class="note">⚠ <b>'+skipped+' record(s) ('+skippedBytes+' bytes) were NOT '+
        'staged</b>: they change parts of the disc this editor does not map — typically skill '+
        'description text and menu strings. The gameplay numbers are what was staged; to apply '+
        'the patch completely, use a PPF tool (or xdelta) on a pristine image instead.</div>':'')+
      '<div class="note">Staging replaces those bytes\' current staged values, exactly as if '+
      'you had typed them — Revert all undoes everything.</div>');
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
  // Puts the retail values back, across the SAME six panes the comparison covers.
  // It was enemies-only, which stopped being defensible the moment "Compare to
  // retail" could report 252 records across five panes and the button next to it
  // could only fix 125 of them. Fields with no baseline are left alone, and a
  // name that is not representable in the buffers' encoding is skipped for the
  // same reason the comparison declines to judge it.
  function stageRestore(){
    let n=0;
    for(let i=0;i<COUNT;i++)
      for(const [l,o,w] of allFields()){
        const van=retail(i,l), T=tableOf(l);
        if(van!==undefined && get(T,i,o,w)!==van){ put(T,i,o,w,van); n++; }
      }
    for(let i=0;i<UCOUNT;i++){
      const v=unitInfo(i); if(!v) continue;
      for(const [l,o,w] of UFIELDS.concat(UAFIELDS)){
        const van=v[l]; if(van===undefined) continue;
        if(getAt(U,i*USTRIDE+o,w)!==van){ putAt(U,i*USTRIDE+o,w,van); n++; }
      }
    }
    for(const i of skillKeys()){
      const base=skillOff(i); if(base<0) continue;
      const v=skillInfo(i)||{};
      if(latin1(v.name) && readName(i)!==null && readName(i)!==v.name && writeName(i,v.name)) n++;
      for(const [l,o,w] of KFIELDS){
        const van=skillRetail(i,l); if(van===undefined) continue;
        if(getAt(K,base+o,w)!==van){ putAt(K,base+o,w,van); n++; }
      }
    }
    const restoreEffect=(base,van)=>{
      if(!van) return 0;
      let m=0;
      const pairs=[[PKINDOFF,1,van.kind]].concat(
        PFIELDS.map(([l,o,w])=>[o,w, l==="Param"?van.param:van.statMask]));
      for(const [o,w,want] of pairs){
        if(want===undefined) continue;
        if(getAt(TX,base+o,w)!==want){ putAt(TX,base+o,w,want); m++; }
      }
      return m;
    };
    for(const i of passiveKeys()){
      const base=passiveOff(i); if(base<0) continue;
      const v=skillInfo(i)||{};
      if(latin1(v.name) && readPassiveName(i)!==null && readPassiveName(i)!==v.name
         && writePassiveName(i,v.name)) n++;
      n+=restoreEffect(base, v.numeric);
    }
    for(const k of gearKeys()){
      const base=gearOff(k); if(base<0) continue;
      n+=restoreEffect(base, (gearInfo(k)||{}).numeric);
    }
    for(let k=0;k<CCOUNT;k++){
      const base=costOff(k), v=COSTS&&COSTS[String(k)];
      if(base<0||!v) continue;
      for(const [o,w,want] of [[0x02,2,v.cost],[CTYPEOFF,1,v.type],
                               [CIDOFF,1,v.id],[CSLOTOFF,1,v.slot]]){
        if(want===undefined) continue;
        if(getAt(C,base+o,w)!==want){ putAt(C,base+o,w,want); n++; }
      }
    }
    loadEnemy(); loadSkill(); loadPassive(); loadGear(); loadCost(); loadUnit(); epending();
    toastFn(n?("✓ Staged a restore of "+n+" field(s) to retail across every pane — "+
               "review, then Save to ISO")
             :"Already matches the retail values");
  }

  // Every editable field compared against the RETAIL baseline, across every pane.
  //
  // Distinct from changeRows(), which compares against a baseline that is on the
  // disc — as opened, or as staged. This one compares against what the game
  // shipped with, which comes from the catalogs rather than the image.
  //
  // It used to cover the enemy tables and nothing else. Not "the other panes
  // match" — nothing at all about them, which reads as "there is nothing to
  // report" while a changed equip-skill magnitude sat there unmentioned. Three
  // of the six panes had no shipped baseline to compare against at all;
  // Editor/gen_effect_catalog.py generates them off the discs, gated on both
  // discs agreeing, so all six are answerable now.
  //
  // Fields with no baseline are COUNTED and reported rather than skipped
  // silently — an unanswerable field and a matching field must not look alike.
  function retailRows(){
    let rows="", recs=0, fields=0, unknown=0;
    const push=(title,cells)=>{ if(cells){ recs++; if(recs<=300) rows+=revGrp(title)+cells; } };

    for(let i=0;i<COUNT;i++){
      let cells="";
      for(const d of retailDiffs(i)){ fields++; cells+=revRow(d.label,d.van,d.cur); }
      push(String(i).padStart(3,"0")+" · "+(cat[i]?cat[i].name:i), cells);
    }

    for(let i=0;i<UCOUNT;i++){
      const v=unitInfo(i); let cells="";
      for(const [l,o,w] of UFIELDS.concat(UAFIELDS)){
        const van=v&&v[l];
        if(!v||van===undefined){ unknown++; continue; }
        const cur=getAt(U,i*USTRIDE+o,w); if(cur===van) continue;
        const aff=UAFIELDS.some(x=>x[0]===l), f=(n)=>aff?affPct(n)+"%":n.toLocaleString();
        fields++; cells+=revRow(l,f(van),f(cur));
      }
      push("unit "+String(i).padStart(2,"0")+" · "+unitName(i), cells);
    }

    for(const i of skillKeys()){
      const base=skillOff(i); if(base<0) continue;
      let cells="";
      const vn=(skillInfo(i)||{}).name, cn=readName(i);
      if(vn!==undefined && !latin1(vn)) unknown++;
      else if(vn!==undefined && cn!==null && cn!==vn){ fields++; cells+=revRow("Name",vn||"—",cn||"—"); }
      for(const [l,o,w] of KFIELDS){
        const van=skillRetail(i,l);
        if(van===undefined){ unknown++; continue; }
        const cur=getAt(K,base+o,w); if(cur===van) continue;
        const f = l==="Target"?targetText : l==="Element"?elementText : (n)=>n.toLocaleString();
        fields++; cells+=revRow(l,String(f(van)),String(f(cur)));
      }
      push("skill "+String(i).padStart(3,"0")+" · "+skillName(i), cells);
    }

    // Passives and gear share the 12-byte effect record, so they share a walker.
    // Kind is compared even though no pane offers it as a control: a patch or a
    // template can change it, and a comparison that only covers what the UI
    // exposes is not a comparison against retail.
    const effectCells=(baseOff,van,curName,vanName)=>{
      let cells="";
      if(vanName!==undefined && !latin1(vanName)) unknown++;
      else if(vanName!==undefined && curName!==null && curName!==vanName){
        fields++; cells+=revRow("Name",vanName||"—",curName||"—");
      }
      if(!van){ unknown+=PFIELDS.length+1; return cells; }
      const pairs=[["Kind",PKINDOFF,1,van.kind]].concat(
        PFIELDS.map(([l,o,w])=>[l,o,w, l==="Param"?van.param:van.statMask]));
      for(const [l,o,w,want] of pairs){
        if(want===undefined){ unknown++; continue; }
        const cur=getAt(TX,baseOff+o,w); if(cur===want) continue;
        const f = l==="Kind" ? passiveKindText : (n)=>n.toLocaleString();
        fields++; cells+=revRow(l,String(f(want)),String(f(cur)));
      }
      return cells;
    };

    for(const i of passiveKeys()){
      const base=passiveOff(i); if(base<0) continue;
      const v=skillInfo(i)||{};
      push("passive "+String(i).padStart(3,"0")+" · "+skillName(i),
           effectCells(base, v.numeric, readPassiveName(i), v.name));
    }

    for(const k of gearKeys()){
      const base=gearOff(k); if(base<0) continue;
      // E.S. accessory names are read-only (their pointers land in a numeric
      // pool, not the skill name pool), so there is no name row to compare
      push("gear "+String(k).padStart(2,"0")+" · "+gearName(k),
           effectCells(base, (gearInfo(k)||{}).numeric, null, undefined));
    }

    for(let k=0;k<CCOUNT;k++){
      const base=costOff(k); if(base<0) continue;
      const v=COSTS&&COSTS[String(k)]; let cells="";
      if(!v){ unknown+=4; }
      else for(const [l,o,w,want] of [["Cost",0x02,2,v.cost],["Type",CTYPEOFF,1,v.type],
                                      ["Id",CIDOFF,1,v.id],["Slot",CSLOTOFF,1,v.slot]]){
        if(want===undefined){ unknown++; continue; }
        const cur=getAt(C,base+o,w); if(cur===want) continue;
        const f = l==="Type" ? costTypeText : (n)=>n.toLocaleString();
        fields++; cells+=revRow(l,String(f(want)),String(f(cur)));
      }
      push("cost "+String(k).padStart(3,"0")+" · "+costName(k), cells);
    }
    return {rows,recs,fields,unknown};
  }

  async function showRetailDiff(){
    const {rows,recs,fields,unknown}=retailRows();
    const head = recs
      ? '<div class="note"><b>'+recs+' record(s), '+fields+' field(s)</b> differ from an '+
        'unmodified disc (retail → yours). Covers every pane this editor can write: '+
        'enemies, units, skills, passives, E.S. gear and skill costs.</div>'+
        (recs>300?'<div class="note">…first 300 shown…</div>':'')
      : '<div class="note">Every editable field across all six panes matches the retail '+
        'values.</div>';
    const gap = unknown
      ? '<div class="note">'+unknown+' field(s) have no shipped retail baseline and were '+
        'not compared — counted here rather than skipped quietly, so an unanswerable '+
        'field does not look like a matching one.</div>'
      : '';
    // Said out loud because "matches retail" would otherwise be read as covering
    // it: description TEXT is not a field and is not compared, so a patch that
    // rewrites descriptions can leave this dialog reporting a clean match while
    // the pending-changes count is not zero. That is not a contradiction — the
    // two answer different questions — but it looks like one if nobody says so.
    const text='<div class="note">This compares editable <b>fields</b>. Skill and passive '+
      '<i>description text</i> is not among them, so a patch that rewrites descriptions can '+
      'show as a clean match here while still leaving pending changes.</div>';
    if(window.openInfo) await window.openInfo("Compared to retail", head+gap+text+rows);
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
        for(const [T,base] of [[S,d.sBase],[R,d.rBase],[K,d.kBase],[U,d.uBase],[TX,d.tBase],[C,d.cBase]]){
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
      S.orig=S.buf.slice();R.orig=R.buf.slice();K.orig=K.buf.slice();U.orig=U.buf.slice();
      TX.orig=TX.buf.slice();C.orig=C.buf.slice();
      loadEnemy();loadSkill();loadPassive();loadGear();loadCost();loadUnit();
      st.textContent="✓ wrote disc "+done.join(", disc ");st.className="status ok";
      toastFn("✓ Saved to "+(done.length>1?"both discs":"disc "+targets[0]));
    }
    epending();
  }
})();
