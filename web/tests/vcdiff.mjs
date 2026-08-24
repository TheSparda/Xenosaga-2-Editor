// VCDIFF encoder tests. Round-trips web/vcdiff.js through a minimal in-test decoder (so CI
// needs no external tools), and — when xdelta3 is installed — also cross-checks against the
// real decoder. The encoder was validated end-to-end against a real 4 GB ISO + xdelta3 3.2.0.
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";
import { execFileSync } from "child_process";
import { createRequire } from "module";
const require = createRequire(import.meta.url);
const WEB = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const { buildXdelta } = require(path.join(WEB, "vcdiff.js"));

let fail = 0;
const ok = (m) => console.log("  ✓ " + m);
const bad = (m) => { console.log("  ✗ " + m); fail++; };

// Minimal VCDIFF decoder for exactly the subset vcdiff.js emits: Hdr_Indicator 0, VCD_SOURCE
// windows, no secondary compression, opcodes 1 (ADD) and 19 (COPY mode 0/SELF).
function readInt(buf, pos) { let n = 0, b; do { b = buf[pos.i++]; n = n * 128 + (b & 0x7f); } while (b & 0x80); return n; }
function decode(source, patch) {
  const pos = { i: 0 };
  if (!(patch[0] === 0xd6 && patch[1] === 0xc3 && patch[2] === 0xc4 && patch[3] === 0x00)) throw new Error("bad magic");
  pos.i = 4;
  if (patch[pos.i++] !== 0x00) throw new Error("unexpected Hdr_Indicator");
  const out = [];
  while (pos.i < patch.length) {
    if (patch[pos.i++] !== 0x01) throw new Error("expected VCD_SOURCE window");
    const slen = readInt(patch, pos), spos = readInt(patch, pos);
    readInt(patch, pos);                                  // delta length (unused here)
    const tlen = readInt(patch, pos);
    if (patch[pos.i++] !== 0x00) throw new Error("unexpected Delta_Indicator");
    const dlen = readInt(patch, pos), ilen = readInt(patch, pos), alen = readInt(patch, pos);
    const data = patch.subarray(pos.i, pos.i + dlen); pos.i += dlen;
    const inst = patch.subarray(pos.i, pos.i + ilen); pos.i += ilen;
    const addr = patch.subarray(pos.i, pos.i + alen); pos.i += alen;
    const seg = source.subarray(spos, spos + slen);      // source segment for this window
    const tgt = []; const ip = { i: 0 }, ap = { i: 0 }; let dp = 0;
    while (ip.i < inst.length) {
      const op = inst[ip.i++];
      if (op === 1) { const sz = readInt(inst, ip); for (let k = 0; k < sz; k++) tgt.push(data[dp++]); }
      else if (op === 19) { const sz = readInt(inst, ip); const a = readInt(addr, ap);
        for (let k = 0; k < sz; k++) tgt.push(a + k < slen ? seg[a + k] : tgt[a + k - slen]); }
      else throw new Error("unexpected opcode " + op);
    }
    if (tgt.length !== tlen) throw new Error("target window size mismatch");
    for (const b of tgt) out.push(b);
  }
  return Uint8Array.from(out);
}

function mk(size) { const b = new Uint8Array(size); for (let i = 0; i < size; i++) b[i] = (i * 37 + 11) & 0xFF; return b; }
function apply(src, edits) { const e = Uint8Array.from(src); for (const ed of edits) e.set(ed.data, ed.off); return e; }

const CASES = [
  ["single edit", 10240, [{ off: 100, data: [1, 2, 3] }], undefined],
  ["edit at 0", 10240, [{ off: 0, data: [7, 7, 7, 7] }], undefined],
  ["edit at end", 10240, [{ off: 10236, data: [5, 6, 7, 8] }], undefined],
  ["multi-window", 20000, [{ off: 50, data: [1, 2] }, { off: 8200, data: [3, 4, 5] }, { off: 16000, data: [6] }], 4096],
  ["straddle boundary", 12000, [{ off: 4094, data: [1, 2, 3, 4, 5, 6] }], 4096],
  ["identity (no edits)", 5000, [], undefined],
  ["many edits", 100000, Array.from({ length: 40 }, (_, k) => ({ off: k * 2400 + 13, data: [k & 0xFF] })), undefined],
];

console.log("VCDIFF self-decode round-trips:");
let xdeltaOK = true; try { execFileSync("xdelta3", ["-V"], { stdio: "ignore" }); } catch { xdeltaOK = false; }
for (const [name, size, edits, win] of CASES) {
  const src = mk(size);
  const eds = edits.map((e) => ({ off: e.off, data: Uint8Array.from(e.data) }));
  const want = apply(src, eds);
  let patch;
  try { patch = buildXdelta(size, eds, win ? { window: win } : undefined); }
  catch (e) { bad(`${name}: build threw ${e.message}`); continue; }
  let got;
  try { got = decode(src, patch); } catch (e) { bad(`${name}: decode threw ${e.message}`); continue; }
  (Buffer.compare(Buffer.from(got), Buffer.from(want)) === 0 ? ok : bad)(`${name} (patch ${patch.length}B)`);
}
if (xdeltaOK) {
  console.log("VCDIFF cross-check vs xdelta3:");
  const tmp = process.env.TMPDIR || "/tmp";
  for (const [name, size, edits, win] of CASES) {
    const src = mk(size), eds = edits.map((e) => ({ off: e.off, data: Uint8Array.from(e.data) }));
    const want = apply(src, eds);
    const patch = buildXdelta(size, eds, win ? { window: win } : undefined);
    const s = path.join(tmp, "vct_s.bin"), p = path.join(tmp, "vct_p.xd"), o = path.join(tmp, "vct_o.bin");
    fs.writeFileSync(s, Buffer.from(src)); fs.writeFileSync(p, Buffer.from(patch));
    try { execFileSync("xdelta3", ["-d", "-f", "-q", "-s", s, p, o]); } catch (e) { bad(`${name}: xdelta3 -d failed`); continue; }
    (Buffer.compare(fs.readFileSync(o), Buffer.from(want)) === 0 ? ok : bad)(`${name} via xdelta3`);
  }
} else {
  console.log("  (xdelta3 not installed — skipped real-decoder cross-check)");
}

console.log(fail ? `\nFAILED (${fail})` : "\nAll VCDIFF tests passed.");
process.exit(fail ? 1 : 0);
