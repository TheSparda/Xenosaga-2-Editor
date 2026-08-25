// Xenosaga II — Reference browser (read-only). Surfaces the data extracted from the
// disc: items + key items + E.S. equipment (name + description) and the full bestiary
// (verified stats + rewards; 74/76 exact guide matches). Pure client-side; fetches
// the committed JSON catalogs.
(function(){
  const $=(s,r=document)=>r.querySelector(s);
  const esc=(s)=>String(s).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const SECTIONS=[
    {key:"consumables", label:"Items", file:"x2_consumables.json", kind:"item"},
    {key:"keyitems",    label:"Key Items", file:"x2_keyitems.json", kind:"item"},
    {key:"es_equip",    label:"E.S. Gear", file:"x2_es_equip.json", kind:"item"},
    {key:"enemies",     label:"Bestiary", file:"x2_enemies.json", kind:"enemy"},
  ];
  // every bestiary column, so nothing extracted from the disc is hidden here
  const STATS=[["HP","hp"],["STR","str"],["VIT","vit"],["EATK","eatk"],["EDEF","edef"],
               ["DEX","dex"],["EVA","eva"],["AGL","agl"],["EXP","exp"],["SP","sp"],["CP","cp"]];
  // Two ways to slice the bestiary. The encounter class is the audited answer to
  // "what is this fight" — a per-record table (x2fields.BOSS_RECORDS /
  // SUPERBOSS_RECORDS, shipped in tables.json) cross-checked against the game's
  // own boss listings. The ID bands are kept alongside it because they are what
  // the disc records, but they are labelled as bands, not as boss-ness: the 561+
  // band really does mix optional-dungeon field Gnosis in with real bosses.
  const GROUPS=[
    {key:"all",   label:"All",              test:()=>true},
    {key:"random",label:"Random encounters",cls:"random"},
    {key:"boss",  label:"Boss battles",     cls:"boss"},
    {key:"super", label:"Super bosses",     cls:"superboss"},
    {key:"field", label:"ID 501+",          test:v=>v.id<561},
    {key:"bossid",label:"ID 561+",          test:v=>v.id>=561&&v.id<701},
    {key:"es",    label:"ID 701+ (E.S.)",   test:v=>v.id>=701},
  ];
  // {index: "boss"|"superboss"} plus labels, from the generated tables. Anything
  // not listed is a random encounter (or a debug row) — mirrors x2fields.
  let ENC={}, ECLASS={}, ELABELS={}, EWHERE={};
  const isDummy=(r)=>!!r&&(/^[A-Z]{3}\d{3}$/.test(String(r.name||"").trim())||
                           (r.exp>0&&r.exp<100&&!r.sp&&!r.cp));
  const eclass=(id,v)=>isDummy(v)?"dummy":(ECLASS[id]||"random");
  const CLASS_PILL={boss:"boss",superboss:"super boss",dummy:"unused"};
  const SORTS=[["idx","Index"],["name","Name"],["hp","HP"],["exp","EXP"],["sp","SP"]];

  const cache={}; let active="consumables", query="", group="all", sort="idx", desc=false;

  async function load(sec){ if(cache[sec.key])return cache[sec.key];
    try{ cache[sec.key]=await (await fetch("../Editor/"+sec.file)).json(); }catch(e){ cache[sec.key]={}; }
    return cache[sec.key]; }

  // the same generated file the ISO editor reads, so both tabs classify a record
  // identically instead of each carrying its own idea of what a boss is
  async function loadClasses(){
    try{
      const t=await (await fetch("tables.json",{cache:"no-cache"})).json();
      ENC=t.encounter||{};
      ECLASS=ENC.byIndex||{}; ELABELS=ENC.labels||{}; EWHERE=ENC.where||{};
    }catch(e){ /* filters fall back to "everything is a random encounter" */ }
  }

  window.initRef=async function(){
    const root=$("#refRoot"); if(root.dataset.init)return; root.dataset.init="1";
    await loadClasses();
    root.innerHTML='<div class="card">'+
      '<div class="toolbar" id="refTabs">'+SECTIONS.map((s,i)=>
        '<button class="mtab'+(i===0?" on":"")+'" data-k="'+s.key+'">'+s.label+'</button>').join("")+'</div>'+
      '<input id="refSearch" type="text" placeholder="Search by name…" autocomplete="off" style="width:100%;margin-bottom:10px">'+
      '<div class="toolbar hidden" id="refBestiaryBar">'+
        '<label>Show</label> <select id="refGroup">'+GROUPS.map(g=>
          '<option value="'+g.key+'">'+esc((g.cls&&ELABELS[g.cls])||g.label)+
          '</option>').join("")+'</select>'+
        '<button type="button" class="helpq" id="refClsHelp" title="How the three '+
          'encounter classes were decided, and which enemies are in each" '+
          'aria-label="About the encounter classes">?</button>'+
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
    $("#refClsHelp").onclick=async()=>{
      const cat=await load(SECTIONS.find(s=>s.kind==="enemy"));
      if(window.openInfo) window.openInfo("What counts as a boss",
        window.encounterHelpHtml(ENC, cat, (i)=>eclass(i,cat[i])));
    };
    $("#refSort").onchange=e=>{sort=e.target.value;render();};
    $("#refDir").onclick=()=>{desc=!desc;$("#refDir").textContent=desc?"↓":"↑";render();};
    $("#refCsv").onclick=exportCsv;
    render();
  };

  function matching(data){
    const g=GROUPS.find(x=>x.key===group)||GROUPS[0];
    const test=g.cls?((id,v)=>eclass(id,v)===g.cls):((id,v)=>g.test(v));
    return Object.keys(data).map(id=>[id,data[id]])
      .filter(([id,v])=>{
        const name=v.name||v;
        if(query && !((name+" "+(v.desc||"")).toLowerCase().includes(query))) return false;
        return v.id===undefined || test(id,v);
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
    const data=await load(sec);
    const bestiary=sec.kind==="enemy";
    $("#refBestiaryBar").classList.toggle("hidden",!bestiary);
    let rows=matching(data);
    if(bestiary) rows=sortRows(rows); else rows.sort((a,b)=>+a[0]-+b[0]);
    let html="";
    for(const [id,v] of rows){
      const name=v.name||v;
      if(bestiary){
        const cls=eclass(id,v), tag=CLASS_PILL[cls];
        html+='<div class="refrow"><span class="rid">'+id+'</span>'+
          '<span class="rname">'+esc(name)+'</span>'+
          '<span class="stpill"'+(EWHERE[id]?' title="'+esc(EWHERE[id])+'"':'')+'>id '+
            v.id+(tag?" · "+tag:"")+'</span>'+
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

  function exportCsv(){
    const data=cache.enemies||{};
    const rows=sortRows(matching(data));
    const cols=["idx","name","id","class","where"].concat(STATS.map(s=>s[1]));
    const q=s=>'"'+String(s).replace(/"/g,'""')+'"';
    const text=[cols.join(",")].concat(rows.map(([id,v])=>
      [id,q(v.name),v.id,eclass(id,v),q(EWHERE[id]||"")]
        .concat(STATS.map(([,k])=>v[k]??"")).join(","))).join("\n")+"\n";
    const a=document.createElement("a");
    a.href=URL.createObjectURL(new Blob([text],{type:"text/csv"}));
    a.download="xenosaga2-bestiary.csv";
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(()=>URL.revokeObjectURL(a.href),4000);
    try{window.toast&&window.toast("✓ Exported "+rows.length+" enemy row(s)");}catch(e){}
  }

  function pill(l,v){return v===undefined?"":'<span class="stpill">'+l+' '+Number(v).toLocaleString()+'</span>';}
})();
