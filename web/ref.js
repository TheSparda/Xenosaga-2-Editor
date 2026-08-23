// Xenosaga II — Reference browser (read-only). Surfaces the data extracted from the
// disc: items + key items + E.S. equipment (name + description) and the enemy name list.
// (Enemy battle stats are not shown — the on-disc stat table is packed/unverified.)
// Pure client-side; fetches the committed JSON catalogs.
(function(){
  const $=(s,r=document)=>r.querySelector(s);
  const esc=(s)=>String(s).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const SECTIONS=[
    {key:"consumables", label:"Items", file:"x2_consumables.json", kind:"item"},
    {key:"keyitems",    label:"Key Items", file:"x2_keyitems.json", kind:"item"},
    {key:"es_equip",    label:"E.S. Gear", file:"x2_es_equip.json", kind:"item"},
    {key:"enemies",     label:"Enemies", file:"x2_enemies.json", kind:"enemy"},
  ];
  const cache={}; let active="consumables", query="";

  async function load(sec){ if(cache[sec.key])return cache[sec.key];
    try{ cache[sec.key]=await (await fetch("../Editor/"+sec.file)).json(); }catch(e){ cache[sec.key]={}; }
    return cache[sec.key]; }

  window.initRef=async function(){
    const root=$("#refRoot"); if(root.dataset.init)return; root.dataset.init="1";
    root.innerHTML='<div class="card">'+
      '<div class="toolbar" id="refTabs">'+SECTIONS.map((s,i)=>
        '<button class="mtab'+(i===0?" on":"")+'" data-k="'+s.key+'">'+s.label+'</button>').join("")+'</div>'+
      '<input id="refSearch" type="text" placeholder="Search by name…" autocomplete="off" style="width:100%;margin-bottom:10px">'+
      '<div id="refCount" class="note" style="margin:0 0 8px"></div>'+
      '<div id="refList"></div></div>';
    root.querySelectorAll("#refTabs .mtab").forEach(b=>b.onclick=()=>{
      root.querySelectorAll("#refTabs .mtab").forEach(x=>x.classList.toggle("on",x===b));
      active=b.dataset.k; render();
    });
    $("#refSearch").addEventListener("input",e=>{query=e.target.value.toLowerCase();render();});
    render();
  };

  async function render(){
    const sec=SECTIONS.find(s=>s.key===active);
    const data=await load(sec);
    const ids=Object.keys(data).sort((a,b)=>+a-+b);
    let shown=0, html="";
    for(const id of ids){
      const v=data[id]; const name=(v.name||v);
      const desc=v.desc||"";
      const hay=(name+" "+desc).toLowerCase();
      if(query && !hay.includes(query)) continue;
      shown++;
      if(sec.kind==="enemy"){
        html+='<div class="refrow"><span class="rid">'+id+'</span>'+
          '<span class="rname">'+esc(name)+'</span></div>';
      } else {
        html+='<div class="refrow"><span class="rid">'+id+'</span>'+
          '<span class="rname">'+esc(name)+'</span>'+
          (desc?'<span class="rdesc">'+esc(desc)+'</span>':'')+'</div>';
      }
    }
    $("#refCount").textContent = shown+" of "+ids.length+(query?" (filtered)":"")+" · "+sec.label;
    $("#refList").innerHTML = html || '<div class="note">No matches.</div>';
  }
})();
