// PPF import tests. Builds synthetic PPF3.0 patches in-test (no game data) and
// drives the parsePPF function extracted from the shipped web/iso.js source.
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";
const WEB = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const src = fs.readFileSync(path.join(WEB, "iso.js"), "utf8");
const at = src.indexOf("function parsePPF");
const parsePPF = eval("(" + src.slice(at, src.indexOf("\n  // the edit buffers", at)) + ")");

let fail = 0;
const ok = (m) => console.log("  ✓ " + m);
const bad = (m) => { console.log("  ✗ " + m); fail++; };

function buildPPF(records, { blockcheck = 0, undo = 0 } = {}) {
  const head = new Uint8Array(60 + (blockcheck ? 1024 : 0));
  head.set([0x50, 0x50, 0x46, 0x33, 0x30, 0x02]);          // "PPF30", enc 2
  head[57] = blockcheck; head[58] = undo;
  const parts = [head];
  for (const { off, data } of records) {
    const r = new Uint8Array(9 + data.length * (undo ? 2 : 1));
    new DataView(r.buffer).setBigUint64(0, BigInt(off), true);
    r[8] = data.length;
    r.set(data, 9);
    if (undo) r.set(data.map(() => 0xEE), 9 + data.length); // fake undo bytes
    parts.push(r);
  }
  const total = parts.reduce((n, p) => n + p.length, 0);
  const out = new Uint8Array(total);
  let p = 0; for (const part of parts) { out.set(part, p); p += part.length; }
  return out.buffer;
}

const RECS = [{ off: 0x1FFF84E, data: Uint8Array.from([1, 2, 3]) },
              { off: 0x2003000, data: Uint8Array.from([9]) }];

// plain
let got = parsePPF(buildPPF(RECS));
if (got.length === 2 && got[0].off === 0x1FFF84E && [...got[0].data].join() === "1,2,3" &&
    got[1].off === 0x2003000 && got[1].data[0] === 9) ok("plain patch parses");
else bad("plain patch: " + JSON.stringify(got));

// blockcheck header (1024 extra bytes) must be skipped
got = parsePPF(buildPPF(RECS, { blockcheck: 1 }));
if (got.length === 2 && got[0].off === 0x1FFF84E) ok("blockcheck header skipped");
else bad("blockcheck: " + JSON.stringify(got.map(r => r.off.toString(16))));

// undo data must be skipped, not read as the next record
got = parsePPF(buildPPF(RECS, { undo: 1 }));
if (got.length === 2 && got[1].data[0] === 9 && got[1].data.length === 1) ok("undo bytes skipped");
else bad("undo: " + JSON.stringify(got));

// offsets above 4 GiB survive the BigInt round-trip (real disc is 4.6 GB)
got = parsePPF(buildPPF([{ off: 0x113399E00, data: Uint8Array.from([7]) }]));
if (got.length === 1 && got[0].off === 0x113399E00) ok("offsets above 4 GiB survive");
else bad(">4GiB offset: " + got[0].off.toString(16));

// wrong magic is rejected loudly
try { parsePPF(new Uint8Array([0x50, 0x50, 0x46, 0x32, 0x30]).buffer); bad("PPF20 accepted"); }
catch (e) { ok("non-PPF3.0 rejected: " + e.message); }

if (fail) { console.log(`\n${fail} PPF test(s) failed.`); process.exit(1); }
console.log("\nAll PPF tests passed.");
