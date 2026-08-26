// Templates-tab internals, driven from the shipped web/iso.js source the same
// way ppf.mjs drives parsePPF — no game data, no DOM.
//
// withScratch() is the one worth testing hardest. It hands the preview a set of
// throwaway buffers so a template can be applied and measured without staging
// anything; if it ever leaked, the user's pending edits would be silently
// overwritten by merely LOOKING at a template. That failure is invisible until
// someone saves, so it gets covered here rather than by eye.
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";
const WEB = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const src = fs.readFileSync(path.join(WEB, "iso.js"), "utf8");

function slice(startMarker, endMarker) {
  const a = src.indexOf(startMarker);
  if (a < 0) throw new Error("not found in iso.js: " + startMarker);
  const b = src.indexOf(endMarker, a);
  if (b < 0) throw new Error("no end marker after " + startMarker);
  return src.slice(a, b);
}

const runsAgainst = eval("(" + slice("function runsAgainst(T,base){", "\n  // Run `fn`") + ")");
// withScratch closes over the six edit buffers; hand it a scope that has them
const makeWithScratch = eval(
  "(function(S,R,K,U,TX,C){" +
  slice("function withScratch(fn){", "\n  // ---- Templates") +
  " return withScratch; })");

let fail = 0;
const ok = (m) => console.log("  ✓ " + m);
const bad = (m) => { console.log("  ✗ " + m); fail++; };
const eq = (got, want, m) =>
  JSON.stringify(got) === JSON.stringify(want) ? ok(m)
    : bad(m + ": got " + JSON.stringify(got) + ", want " + JSON.stringify(want));

const buf = (bytes) => {
  const b = new Uint8Array(bytes);
  return { buf: b, orig: b.slice(), dv: new DataView(b.buffer) };
};

// --- runsAgainst -----------------------------------------------------------
{
  const T = buf([1, 2, 3, 4, 5, 6]);
  eq(runsAgainst(T, T.orig), [], "an unchanged buffer has no runs");
  T.buf[1] = 9; T.buf[2] = 9; T.buf[5] = 9;
  eq(runsAgainst(T, T.orig), [[1, 3], [5, 6]],
     "adjacent changes coalesce into one run, separated ones do not");
  eq(runsAgainst(T, T.buf), [], "compared against itself, nothing differs");
}

// --- withScratch -----------------------------------------------------------
{
  const S = buf([1, 1, 1]), R = buf([2, 2]), K = buf([3]), U = buf([4]),
        TX = buf([5, 5]), C = buf([6]);
  const withScratch = makeWithScratch(S, R, K, U, TX, C);
  S.buf[0] = 99;                                   // a pending edit of the user's
  const realBufs = [S, R, K, U, TX, C].map(T => T.buf);

  const seen = withScratch((saved) => {
    S.buf[1] = 77; TX.buf[0] = 88;                 // what a template would write
    eq([...S.buf], [99, 77, 1], "writes inside the scratch land on the copy");
    eq([...saved.find(x => x.T === S).buf], [99, 1, 1],
       "the saved baseline still holds the user's staged bytes, not the template's");
    return [...S.buf];
  });
  eq(seen, [99, 77, 1], "the callback's view is returned to the caller");
  eq([...S.buf], [99, 1, 1], "the real buffer is restored afterwards");
  eq([...TX.buf], [5, 5], "every buffer is restored, not just the first");
  eq([S, R, K, U, TX, C].map((T, i) => T.buf === realBufs[i]), Array(6).fill(true),
     "the ORIGINAL arrays come back, not equal-looking copies");
  eq([S, R, K, U, TX, C].every(T => T.dv.buffer === T.buf.buffer), true,
     "each DataView is put back in step with its buffer");
}

// a preview that throws must not strand the editor on scratch buffers
{
  const S = buf([1, 2]), R = buf([0]), K = buf([0]), U = buf([0]),
        TX = buf([0]), C = buf([0]);
  const withScratch = makeWithScratch(S, R, K, U, TX, C);
  const real = S.buf;
  let threw = false;
  try { withScratch(() => { S.buf[0] = 42; throw new Error("boom"); }); }
  catch (e) { threw = e.message === "boom"; }
  eq(threw, true, "the error reaches the caller");
  eq(S.buf === real && S.buf[0] === 1, true,
     "a throw mid-preview still restores the real buffers");
}

// --- the registry the pane renders ----------------------------------------
{
  const ids = [...src.matchAll(/\{id:"([a-z0-9-]+)", variant:"(\w+)"/g)].map(m => m.slice(1));
  eq(ids, [["hardtype-normal", "normal"], ["hardtype-hard", "hard"]],
     "both HardType variants are registered as templates");
  const ht = JSON.parse(fs.readFileSync(path.join(WEB, "hardtype.json"), "utf8"));
  eq(ids.every(([, v]) => !!(ht.variants || {})[v]), true,
     "every registered template names a variant hardtype.json actually carries");
}

console.log(fail ? `\n${fail} check(s) failed` : "\nall checks passed");
process.exit(fail ? 1 : 0);
