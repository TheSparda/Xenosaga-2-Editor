// Xenosaga II — Reference browser (read-only). Surfaces the data extracted from the
// disc: items + key items + E.S. equipment (name + description) and the full bestiary
// (verified stats + rewards; 74/76 exact guide matches), plus the damage formula read
// out of the battle overlay. Pure client-side; fetches the committed JSON catalogs.
(function(){
  const $=(s,r=document)=>r.querySelector(s);
  const esc=(s)=>String(s).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  // `code` and **bold** only — the doc section's copy is written here, not fetched,
  // so the markup stays deliberately tiny and everything is escaped first.
  const fmt=(s)=>esc(s).replace(/`([^`]+)`/g,'<code>$1</code>').replace(/\*\*([^*]+)\*\*/g,'<b>$1</b>');
  const SECTIONS=[
    {key:"consumables", label:"Items", file:"x2_consumables.json", kind:"item"},
    {key:"keyitems",    label:"Key Items", file:"x2_keyitems.json", kind:"item"},
    {key:"es_equip",    label:"E.S. Gear", file:"x2_es_equip.json", kind:"item"},
    {key:"enemies",     label:"Bestiary", file:"x2_enemies.json", kind:"enemy"},
    {key:"damage",      label:"Damage", kind:"doc"},
  ];
  const DOC_URL="https://github.com/TheSparda/Xenosaga-2-Editor/blob/main/Research/DAMAGE.md";
  // every bestiary column, so nothing extracted from the disc is hidden here
  const STATS=[["HP","hp"],["STR","str"],["VIT","vit"],["EATK","eatk"],["EDEF","edef"],
               ["DEX","dex"],["EVA","eva"],["AGL","agl"],["EXP","exp"],["SP","sp"],["CP","cp"]];
  // Two ways to slice the bestiary. The ID bands are what the disc records, but
  // the 561+ band mixes late-game field Gnosis in with real bosses — so "major"
  // (retail HP at/above the rebalance threshold) is the more meaningful cut, and
  // the band filters are labelled as bands rather than as boss-ness.
  const MAJOR_HP=20000;
  const GROUPS=[
    {key:"all",   label:"All",           test:()=>true},
    {key:"major", label:"Major fights",  test:v=>v.hp>=MAJOR_HP},
    {key:"field", label:"ID 501+",       test:v=>v.id<561},
    {key:"boss",  label:"ID 561+",       test:v=>v.id>=561&&v.id<701},
    {key:"es",    label:"ID 701+ (E.S.)",test:v=>v.id>=701},
  ];
  const SORTS=[["idx","Index"],["name","Name"],["hp","HP"],["exp","EXP"],["sp","SP"]];

  // The damage formula, read out of the disc-1 battle overlay (OV01.OVL, routine
  // 0xA8C778) with no emulator. Every claim carries the same verified / inferred /
  // open label the write-up uses, and where the code contradicts the community
  // guide that is said here too rather than smoothed over. Full derivation — how the
  // routine was located, and what is still unresolved — in Research/DAMAGE.md.
  const TAGS={verified:"verified", inferred:"inferred", open:"unresolved", source:"how this was derived"};
  const DAMAGE=[
    {t:"Where this comes from", tag:"source", note:[
      "Read statically out of `OV01.OVL` on disc 1 (USA, SLUS-20892) — **no emulator**. The damage routine is `0xA8C778`, found by following the only two `0x270F` (9999) immediates in the overlay, the divides by a literal `100` (51 divides in 446 KB), and the fact that it is the only one of 1419 functions that calls any of the four stat getters.",
      "What follows is **attack category 1** — the ordinary physical/ether attack path. The action descriptor's `+0x07` picks one of seven categories via a jump table at `0xABA3B0`; `+0x06` picks physical (`0`) or ether (`1`).",
      "The stat-block offsets the getters read are exactly the disc-1 pnach's per-character stat addresses, 4 of 4 — an independent source that knew nothing about this overlay."]},

    {t:"Attack and defence", tag:"verified", note:[
      "Each side's term is one stat times a fixed coefficient:"],
      tab:[["","attacker term","target term"],
           ["physical","STR × 4","VIT × 3"],
           ["ether","EATK × 5","EDEF × 4"]],
      code:"v = stat × coefficient\n"+
           "if  has_status(unit, class 2, mask):   v -= v/2    # -50%\n"+
           "elif has_status(unit, class 1, mask):  v -= v/4    # -25%\n"+
           "if  has_status(unit, class 4, up_mask):   v += v/4 # +25%\n"+
           "elif has_status(unit, class 4, down_mask): v -= v/4 # -25%",
      after:["The status adjustment is identical in all four getters."]},

    {t:"Base damage", tag:"verified", note:[
      "`power` is a **u8** at `+0x0E` of the action descriptor."],
      code:"base = ATK × power / 20  -  DEF\n"+
           "if base < 0:\n"+
           "    base = 0\n"+
           "else:\n"+
           "    base += rand(0 .. base/10 + 2)   # 0% to +10%, inclusive",
      after:["The variance is **one-sided and upward only** — there is no downward roll.",
             "Practical reading of `power / 20`: power 20 is a flat 1× of ATK, and Erde Kaiser Fury's 250 is 12.5×. That is the runtime descriptor's byte; the **Power** the ISO editor's Skills tab writes lives at `+0x0A` of the 32-byte disc record, and the copy from one to the other has not been traced (**inferred**)."]},

    {t:"Multipliers, in source order", tag:"verified", note:[
      "`D` is the base-damage snapshot from the step above. Additive percentage bonuses are computed from `D`, **not** compounded on the running total; only the `×` steps compound."],
      tab:[["#","condition","effect","note"],
           ["1","target state `+0x70 == 1`","×1.5","Break (guide agrees)"],
           ["2","target state `+0x70` in `2..3`","×2","Air / Down (guide agrees)"],
           ["3","attacker flag `0x2E`","+= D × f / 4","`f` from `0xA880F8`, unresolved"],
           ["4","target flag `0x2F`","−= D × f / 4","same"],
           ["5","element coat matches attack","×0.5 or ×0.75","×0.5 on status `4/0x100`"],
           ["6","attacker flag `0x31`","+= D","unresolved"],
           ["7","target flag `0x3F`","+= D × rand(10..15) / 100",""],
           ["8","ether **and** event slot `4` (ETR)","×1.5","matches the pnach slot list: ether damage & recovery +50%"],
           ["9","critical (`0xA8CF88`)","×1.5, or ×2 hi-critical","see Criticals"],
           ["10","chain counter `+0xF4 ≥ 2`","+= D × (10×chain − 10) / 100","+10% per chain step above 1"],
           ["11","elemental affinity","× affinity / 20","see Elemental affinity"],
           ["12","zone bit set for the zone hit","×0.5","see Zone hits"],
           ["13","target `+0x19 == 2` (Guarding)","−= d × (base + bonus) / 100","base 50 physical / 25 ether — the guide's \"took only half damage\""],
           ["14","target flag `0x28`","×0.9","unresolved"],
           ["15","always","clamp ≥ 0, then 9999 / 99999","99999 when the unit id at `+0x62` is `11..15` — the E.S. units"]]},

    {t:"Zone hits", tag:"verified", note:[
      "The target's current zone is `stats +0x1C & 3` (0/1/2). The action descriptor carries one flag byte per zone at `+0x0F`, `+0x10`, `+0x11`. For the zone actually being hit:",
      "• **bit 1** set → damage is **halved**.",
      "• **bit 2** set → **+50 critical rate**.",
      "The same three bytes drive both the zone damage penalty and the zone crit bonus, which is why hitting the right zone matters twice over."]},

    {t:"Criticals", tag:"verified", note:["Routine `0xA8CF88`:"],
      code:"rate = 10                    # 50 when the event slot is 0\n"+
           "rate += 50   per matching attack zone   (descriptor +0x0F/+0x10/+0x11, bit 2)\n"+
           "rate += back-attack bonus    (0xA8ED88)\n"+
           "rate += 50   if target flag +0x10 == 1\n"+
           "rate  = min(rate, 100)\n"+
           "if rand_pct(rate):\n"+
           "    if event_slot != 0:   damage = damage × 3 / 2   # ×1.5\n"+
           "    elif rand_pct(10):    damage = damage × 4 / 2   # ×2.0  hi-critical\n"+
           "    else:                 damage = damage × 3 / 2",
      after:["Matches the guide's critical ×1.5 / hi-critical ×2 exactly. The 10% hi-critical roll only happens when the event slot is `0` — very likely CRTC, which would also explain the base rate jumping 10 → 50 on the same condition (**inferred**, not proven)."]},

    {t:"Elemental affinity", tag:"verified", code:
      "mult = 20\n"+
      "for i in 0..7:\n"+
      "    if attack_elements & (1 << i):\n"+
      "        if rand_pct(stats[0x2C + i]):        # per-element proc chance\n"+
      "            a = stats[0x24 + i]              # signed affinity\n"+
      "            if a < 0:  mult = a; break       # absorb\n"+
      "            else:      mult = mult + a - 20  # stacks across elements\n"+
      "if matched:\n"+
      "    if mult == 0:  damage = 0                # null, flag 0x2000\n"+
      "    else:          damage = damage × |mult| / 20   (negative -> absorb, flag 0x1002)",
      after:["The multiplier is **affinity / 20** — and that confirms the affinity bytes this editor writes. The enemy-table work derived \"percent = byte × 5\" by comparison against the strategy guide, and `byte × 5 / 100` **is** `byte / 20`. Guide comparison and instruction stream, from directions that know nothing about each other, agreeing exactly.",
             "The per-element **proc chance** at `+0x2C + i` is new: nothing in the guides mentions affinities being probabilistic."]},

    {t:"The runtime stat block", tag:"verified", note:[
      "Every unit has a stat block at `unit + 0x144`. Block base is `0x61B590` for Shion, and the four stat offsets land in exactly the pnach's slots — 4 of 4."],
      tab:[["offset","field"],
           ["`+0x02`","HP, u32, **unaligned** (read via `lwl 5`/`lwr 2`)"],
           ["`+0x08`","**STR**"],
           ["`+0x0A`","**VIT**"],
           ["`+0x0C`","**EATK**"],
           ["`+0x0E`","**EDEF**"],
           ["`+0x10/11/12`","DEX / EVA / AGL (u8)"],
           ["`+0x24..0x2B`","8 element affinities (signed)"],
           ["`+0x2C..0x33`","8 per-element proc chances"],
           ["`+0x62`","unit id (`11..15` = E.S. → 99999 cap)"],
           ["`+0x70`","battle state (`1` = Break, `2..3` = Air/Down)"],
           ["`+0xF4`","elemental chain counter (**inferred**)"]]},

    {t:"Where this contradicts the community guide", tag:"inferred", note:[
      "The Battle Mechanics Guide says of elemental chains: \"I'm not sure of the exact formula… a chain of 10 does around 10% more damage\", and guesses `chain × 1%`. The code computes `(10 × chain − 10)%`, so a chain of 10 is **+90%**, not +10%.",
      "Either the guide's estimate is wrong or `+0xF4` is not the chain counter. That offset label is inferred from context and is the **weakest claim** on this page."]},

    {t:"Not yet resolved", tag:"open", note:[
      "• Attack categories 2/3 (`0xA8CCA8`, percentage/fixed damage), 6 (`0xA8CDD8`) and 7 (`0xA8CE58`). Categories 4 and 5 branch straight to the exit — no damage.",
      "• Flags `0x2E` / `0x2F` / `0x31` / `0x3F`, and `0xA880F8`.",
      "• `0xA8C278` — builds the effective element mask from the attacker and the descriptor's `+0x0C`.",
      "• Post-damage passes `0xA8D110`, `0xA8D240`, `0xA8D950`, `0xA8DB90`.",
      "• Whether E.S. units use this routine or the `0xA8E9B0` path."]},
  ];

  const cache={}; let active="consumables", query="", group="all", sort="idx", desc=false;

  async function load(sec){ if(cache[sec.key])return cache[sec.key];
    try{ cache[sec.key]=await (await fetch("../Editor/"+sec.file)).json(); }catch(e){ cache[sec.key]={}; }
    return cache[sec.key]; }

  window.initRef=async function(){
    const root=$("#refRoot"); if(root.dataset.init)return; root.dataset.init="1";
    root.innerHTML='<div class="card">'+
      '<div class="toolbar" id="refTabs">'+SECTIONS.map((s,i)=>
        '<button class="mtab'+(i===0?" on":"")+'" data-k="'+s.key+'">'+s.label+'</button>').join("")+'</div>'+
      '<input id="refSearch" type="text" placeholder="Search by name…" autocomplete="off" style="width:100%;margin-bottom:10px">'+
      '<div class="toolbar hidden" id="refBestiaryBar">'+
        '<label>Show</label> <select id="refGroup">'+GROUPS.map(g=>
          '<option value="'+g.key+'">'+g.label+'</option>').join("")+'</select>'+
        '<label style="margin-left:8px">Sort</label> <select id="refSort">'+SORTS.map(s=>
          '<option value="'+s[0]+'">'+s[1]+'</option>').join("")+'</select>'+
        '<button id="refDir" class="btn" title="Reverse order">↑</button>'+
        '<span style="flex:1"></span>'+
        '<button id="refCsv" class="btn">⬇ CSV</button>'+
      '</div>'+
      '<div id="refCount" class="note" style="margin:0 0 8px"></div>'+
      '<div id="refList"></div></div>';
    root.querySelectorAll("#refTabs .mtab").forEach(b=>b.onclick=()=>{
      root.querySelectorAll("#refTabs .mtab").forEach(x=>x.classList.toggle("on",x===b));
      active=b.dataset.k; render();
    });
    $("#refSearch").addEventListener("input",e=>{query=e.target.value.toLowerCase();render();});
    $("#refGroup").onchange=e=>{group=e.target.value;render();};
    $("#refSort").onchange=e=>{sort=e.target.value;render();};
    $("#refDir").onclick=()=>{desc=!desc;$("#refDir").textContent=desc?"↓":"↑";render();};
    $("#refCsv").onclick=exportCsv;
    render();
  };

  function matching(data){
    const g=GROUPS.find(x=>x.key===group)||GROUPS[0];
    return Object.keys(data).map(id=>[id,data[id]])
      .filter(([id,v])=>{
        const name=v.name||v;
        if(query && !((name+" "+(v.desc||"")).toLowerCase().includes(query))) return false;
        return v.id===undefined || g.test(v);
      });
  }

  function sortRows(rows){
    const key=([id,v])=>{
      if(sort==="idx") return +id;
      if(sort==="name") return String(v.name||v).toLowerCase();
      return Number(v[sort]||0);
    };
    rows.sort((a,b)=>{const x=key(a),y=key(b);return x<y?-1:x>y?1:0;});
    if(desc) rows.reverse();
    return rows;
  }

  async function render(){
    const sec=SECTIONS.find(s=>s.key===active);
    $("#refSearch").placeholder = sec.kind==="doc" ? "Search the formula…" : "Search by name…";
    if(sec.kind==="doc") return renderDoc();
    const data=await load(sec);
    const bestiary=sec.kind==="enemy";
    $("#refBestiaryBar").classList.toggle("hidden",!bestiary);
    let rows=matching(data);
    if(bestiary) rows=sortRows(rows); else rows.sort((a,b)=>+a[0]-+b[0]);
    let html="";
    for(const [id,v] of rows){
      const name=v.name||v;
      if(bestiary){
        html+='<div class="refrow"><span class="rid">'+id+'</span>'+
          '<span class="rname">'+esc(name)+'</span>'+
          '<span class="stpill">id '+v.id+(v.hp>=MAJOR_HP?" · major":"")+'</span>'+
          '<span class="rpills">'+
            STATS.map(([l,k])=>pill(l,v[k])).join("")+
          '</span></div>';
      }else{
        html+='<div class="refrow"><span class="rid">'+id+'</span>'+
          '<span class="rname">'+esc(name)+'</span>'+
          (v.desc?'<span class="rdesc">'+esc(v.desc)+'</span>':'')+'</div>';
      }
    }
    const total=Object.keys(data).length;
    $("#refCount").textContent = rows.length+" of "+total+
      (query||(bestiary&&group!=="all")?" (filtered)":"")+" · "+sec.label;
    $("#refList").innerHTML = html || '<div class="note">No matches.</div>';
  }

  // The Damage section is prose, not rows, so search filters whole blocks — a
  // half-shown formula would be worse than none.
  function blockText(b){
    return [b.t, TAGS[b.tag]||"", (b.note||[]).join(" "), (b.after||[]).join(" "),
            (b.tab||[]).map(r=>r.join(" ")).join(" "), b.code||""].join(" ").toLowerCase();
  }

  function blockHtml(b){
    let h='<div class="dmgblk"><div class="dmghead"><span class="rname">'+esc(b.t)+'</span>'+
          '<span class="stpill tag-'+b.tag+'">'+esc(TAGS[b.tag]||b.tag)+'</span></div>';
    for(const p of b.note||[]) h+='<p class="note">'+fmt(p)+'</p>';
    if(b.tab) h+='<div class="dmgwrap"><table class="dmgtab">'+b.tab.map((r,i)=>'<tr>'+
      r.map(c=>'<'+(i?"td":"th")+'>'+fmt(c)+'</'+(i?"td":"th")+'>').join("")+'</tr>').join("")+'</table></div>';
    if(b.code) h+='<pre class="dmgcode">'+esc(b.code)+'</pre>';
    for(const p of b.after||[]) h+='<p class="note">'+fmt(p)+'</p>';
    return h+'</div>';
  }

  function renderDoc(){
    $("#refBestiaryBar").classList.add("hidden");
    const blocks=DAMAGE.filter(b=>!query||blockText(b).includes(query));
    $("#refCount").innerHTML = blocks.length+" of "+DAMAGE.length+(query?" (filtered)":"")+
      " · Damage — attack category 1, read out of the disc-1 battle overlay. "+
      'Full derivation, including what is still open: <a href="'+DOC_URL+
      '" target="_blank" rel="noopener noreferrer">Research/DAMAGE.md</a>.';
    $("#refList").innerHTML = blocks.map(blockHtml).join("") ||
      '<div class="note">No matches.</div>';
  }

  function exportCsv(){
    const data=cache.enemies||{};
    const rows=sortRows(matching(data));
    const cols=["idx","name","id"].concat(STATS.map(s=>s[1]));
    const q=s=>'"'+String(s).replace(/"/g,'""')+'"';
    const text=[cols.join(",")].concat(rows.map(([id,v])=>
      [id,q(v.name),v.id].concat(STATS.map(([,k])=>v[k]??"")).join(","))).join("\n")+"\n";
    const a=document.createElement("a");
    a.href=URL.createObjectURL(new Blob([text],{type:"text/csv"}));
    a.download="xenosaga2-bestiary.csv";
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(()=>URL.revokeObjectURL(a.href),4000);
    try{window.toast&&window.toast("✓ Exported "+rows.length+" enemy row(s)");}catch(e){}
  }

  function pill(l,v){return v===undefined?"":'<span class="stpill">'+l+' '+Number(v).toLocaleString()+'</span>';}
})();
