// Xenosaga II Editor — service worker. Offline app shell (network-first so deploys
// update on next online launch), cache-first for the large immutable Pyodide CDN, and
// a Web Share target hand-off.
const CACHE = "x2editor-v1.2.0";
const SHARE_CACHE = "x2editor-share";
const SHELL = ["./","index.html","style.css","app.js","iso.js","ref.js","manifest.webmanifest",
  "../Editor/x2save.py","../Editor/x2fields.py","../Editor/x2_consumables.json",
  "../Editor/x2_keyitems.json","../Editor/x2_es_equip.json","../Editor/x2_enemies.json"];

self.addEventListener("install",e=>{self.skipWaiting();
  e.waitUntil(caches.open(CACHE).then(c=>Promise.allSettled(SHELL.map(u=>c.add(u)))));});

self.addEventListener("activate",e=>{e.waitUntil((async()=>{
  for(const k of await caches.keys()) if(k!==CACHE&&k!==SHARE_CACHE) await caches.delete(k);
  await self.clients.claim();})());});

self.addEventListener("fetch",e=>{
  const url=new URL(e.request.url);
  // Web Share target: stash the shared file, redirect app to ?shared=1
  if(e.request.method==="POST"&&url.pathname.endsWith("share-target")){
    e.respondWith((async()=>{const form=await e.request.formData();const f=form.get("save");
      if(f){const c=await caches.open(SHARE_CACHE);await c.put("shared",new Response(f,{headers:{"X-Name":f.name||"shared.bin"}}));}
      return Response.redirect("./?shared=1",303);})());return;}
  if(e.request.method!=="GET")return;
  // cross-origin (Pyodide CDN): cache-first (immutable, version-pinned)
  if(url.origin!==location.origin){
    e.respondWith(caches.open(CACHE).then(async c=>{const hit=await c.match(e.request);
      if(hit)return hit;const res=await fetch(e.request);if(res.ok)c.put(e.request,res.clone());return res;}));return;}
  // same-origin: network-first, fall back to cache
  e.respondWith((async()=>{try{const res=await fetch(e.request);
    if(res.ok){const c=await caches.open(CACHE);c.put(e.request,res.clone());}return res;}
    catch(err){const hit=await caches.match(e.request);if(hit)return hit;throw err;}})());
});
