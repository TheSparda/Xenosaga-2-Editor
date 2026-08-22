// Xenosaga II ISO Editor (web) — enemy table only, so far. Never loads the 4.6 GB disc:
// reads a ~9 KB ranged slice around the enemy table, edits in memory, and writes only the
// changed byte-runs back in place via the File System Access API (desktop Chromium).
// Mirrors Editor/x2fields.ENEMY_* + Editor/x2patch enemy read/write.
(function(){
  const FS = "showOpenFilePicker" in window;
  // enemy table (disc 1) — keep in sync with x2fields
  const BASE = 0x2000000, STRIDE = 0x5C, COUNT = 97;
  const FIELDS = [["HP",0x36,4],["Atk",0x3E,2],["Def",0x42,2],["Cash",0x4E,2],["EXP",0x50,2]];
  const END = BASE + COUNT*STRIDE + 0x40;

  let handle=null, BUF=null, ORIG=null, dv=null, names=null, backedUp=false;
  const $=(s,r=document)=>r.querySelector(s);
  const esc=(s)=>String(s).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const toastFn=(m,e)=>{try{window.toast&&window.toast(m,e);}catch(_){}};

  function rd(i,off,w){const a=i*STRIDE+off;return w===4?dv.getUint32(a,true):dv.getUint16(a,true);}
  function wr(i,off,w,v){const a=i*STRIDE+off;const max=(w===4?0xFFFFFFFF:0xFFFF);
    v=Math.max(0,Math.min(v|0,max));if(w===4)dv.setUint32(a,v,true);else dv.setUint16(a,v,true);}

  async function loadNames(){ if(names)return names;
    try{names=await (await fetch("../Editor/x2_enemies.json")).json();}catch(e){names={};} return names; }

  window.initISO = async function(){
    const root=$("#isoRoot"); if(root.dataset.init) return; root.dataset.init="1";
    if(!FS){ root.innerHTML='<div class="card blocked"><b>ISO editing needs desktop Chrome / Edge / Brave / Opera</b>'+
      ' (File System Access API). The Save editor works everywhere, including mobile.</div>'; return; }
    await loadNames();
    root.innerHTML='<div class="card"><h2>1 · Open disc 1 ISO</h2>'+
      '<button id="isoPick" class="btn primary">Choose ISO…</button> '+
      '<span id="isoStatus" class="status"></span>'+
      '<p class="note">Xenosaga II (USA) <b>Disc 1</b> — SLUS-20892. Enemy stats apply to a new game. '+
      'Edits write in place; work on a copy or tick backup.</p></div>'+
      '<div id="isoEdit"></div>';
    $("#isoPick").onclick=openISO;
  };

  async function openISO(){
    try{ [handle]=await window.showOpenFilePicker(); }catch(e){ return; }
    const f=await handle.getFile();
    const st=$("#isoStatus"); st.textContent="checking disc…"; st.className="status";
    // version/region gate: confirm XENOSAGA_II volume + disc-1 serial
    const head=new Uint8Array(await f.slice(0,0x200000).arrayBuffer());
    const asc=s=>{let o=-1;const t=[...s].map(c=>c.charCodeAt(0));
      for(let i=0;i<head.length-t.length;i++){let m=true;for(let j=0;j<t.length;j++)if(head[i+j]!==t[j]){m=false;break;}if(m){o=i;break;}}return o;};
    const vol=new TextDecoder().decode(head.slice(0x8028,0x8028+11));
    if(vol!=="XENOSAGA_II"){ st.textContent="✗ Not a Xenosaga II disc (volume "+esc(vol)+")"; st.className="status err"; return; }
    if(asc("SLUS_208.92")<0){
      st.textContent = asc("SLUS_211.33")>=0 ? "✗ This is Disc 2 — the enemy table is on Disc 1."
                                             : "✗ Unrecognized Xenosaga II serial."; st.className="status err"; return; }
    // ranged read of the enemy table
    BUF=new Uint8Array(await f.slice(BASE,END).arrayBuffer());
    ORIG=BUF.slice(); dv=new DataView(BUF.buffer); backedUp=false;
    st.textContent="✓ Disc 1 loaded ("+f.name+")"; st.className="status ok";
    renderEnemy();
  }

  function renderEnemy(){
    const opts=Object.keys(names).map(i=>'<option value="'+i+'">'+String(i).padStart(2,"0")+' · '+esc(names[i])+'</option>').join("")
      || Array.from({length:COUNT},(_,i)=>'<option value="'+i+'">'+i+'</option>').join("");
    $("#isoEdit").innerHTML='<div class="card"><h2>2 · Enemy</h2>'+
      '<div class="toolbar"><label>Enemy</label> <select id="esel">'+opts+'</select>'+
      '<label style="margin-left:8px"><input type="checkbox" id="ebak"> back up ISO first</label>'+
      '<span style="flex:1"></span>'+
      '<button id="erev" class="btn" disabled>Revert</button>'+
      '<button id="esave" class="btn primary" disabled>Save to ISO <span id="ebadge" class="badge"></span></button>'+
      '<span id="estat" class="status"></span></div>'+
      '<table id="etbl"><tbody><tr id="erow"></tr></tbody></table>'+
      '<p class="note">HP is verified; Atk/Def/Cash/EXP are inferred. Writes only the changed bytes '+
      'back into the disc at their exact offsets.</p></div>';
    $("#esel").onchange=loadEnemy;
    $("#erev").onclick=()=>{const i=+$("#esel").value;
      document.querySelectorAll("#erow input").forEach(inp=>{const f=inp.dataset.f,w=+inp.dataset.w,off=+inp.dataset.o;
        const v=fieldFromOrig(i,off,w);inp.value=v;inp.setAttribute("data-def",v);wr(i,off,w,v);
        inp.classList.remove("changed");const b=inp.nextElementSibling;if(b&&b.classList.contains("restore"))b.classList.remove("show");});
      epending();};
    $("#esave").onclick=saveISO;
    loadEnemy();
  }
  function fieldFromOrig(i,off,w){const a=i*STRIDE+off;const d=new DataView(ORIG.buffer);
    return w===4?d.getUint32(a,true):d.getUint16(a,true);}

  function loadEnemy(){
    const i=+$("#esel").value;
    $("#erow").innerHTML=FIELDS.map(([lbl,off,w])=>'<td><div class="fl">'+lbl+'</div><span><input type="number" min="0" '+
      'autocomplete="off" data-f="'+lbl+'" data-o="'+off+'" data-w="'+w+'" data-def="'+rd(i,off,w)+'" value="'+rd(i,off,w)+'"></span></td>').join("");
    document.querySelectorAll("#erow input").forEach(inp=>{
      let btn=inp.nextElementSibling;
      if(!btn||!btn.classList.contains("restore")){btn=document.createElement("button");btn.type="button";
        btn.className="restore";btn.textContent="↺";inp.after(btn);}
      const refresh=()=>{const off=+inp.dataset.o,w=+inp.dataset.w;wr(i,off,w,+inp.value);
        const ch=String(inp.value)!==String(inp.getAttribute("data-def"));
        inp.classList.toggle("changed",ch);btn.classList.toggle("show",ch);epending();};
      inp.addEventListener("input",refresh);btn.onclick=()=>{inp.value=inp.getAttribute("data-def");refresh();};refresh();
    });
    epending();
  }
  function diffCount(){let n=0;for(let i=0;i<BUF.length;i++)if(BUF[i]!==ORIG[i]){n++;while(i<BUF.length&&BUF[i]!==ORIG[i])i++;}return n;}
  function epending(){const n=diffCount();const b=$("#ebadge");if(b)b.textContent=n?"("+n+")":"";
    const s=$("#esave"),r=$("#erev");if(s)s.disabled=!n;if(r)r.disabled=!n;}
  function diffRuns(){const runs=[];let i=0;while(i<BUF.length){if(BUF[i]!==ORIG[i]){let j=i;
    while(j<BUF.length&&BUF[j]!==ORIG[j])j++;runs.push([i,j]);i=j;}else i++;}return runs;}

  async function saveISO(){
    const st=$("#estat");st.textContent="writing…";st.className="status";$("#esave").disabled=true;
    try{
      if((await handle.queryPermission({mode:"readwrite"}))!=="granted")
        await handle.requestPermission({mode:"readwrite"});
      if($("#ebak").checked && !backedUp){
        st.textContent="backing up (this copies the whole disc)…";
        const src=await handle.getFile();
        // best-effort .bak next to a chosen destination
        const bh=await window.showSaveFilePicker({suggestedName:src.name+".bak"});
        const bw=await bh.createWritable();await bw.write(src);await bw.close();backedUp=true;
      }
      const runs=diffRuns();
      const w=await handle.createWritable({keepExistingData:true});
      for(const [s,e] of runs) await w.write({type:"write",position:BASE+s,data:BUF.slice(s,e)});
      await w.close();
      ORIG=BUF.slice();
      document.querySelectorAll("#erow input").forEach(inp=>{inp.setAttribute("data-def",inp.value);inp.classList.remove("changed");
        const b=inp.nextElementSibling;if(b&&b.classList.contains("restore"))b.classList.remove("show");});
      st.textContent="✓ wrote "+runs.length+" run(s) to ISO";st.className="status ok";toastFn("✓ Enemy saved to ISO");
    }catch(e){st.textContent="✗ "+e;st.className="status err";toastFn("✗ "+e,true);}
    epending();
  }
})();
