// Pull a function out of the shipped web/iso.js so a Node test can drive the
// real source rather than a copy of it.
//
// The extraction is by text markers, which is fragile by nature — and it failed
// silently for three commits. `src.indexOf(marker)` returns -1 when a comment
// downstream is reworded, `slice(at, -1)` then happily returns everything to the
// end of the file, and the eval blows up with a syntax error pointing at a
// function the test never meant to touch. A missing marker has to be an error
// about the marker, so that is what this does.
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

export const WEB = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
export const isoSource = () => fs.readFileSync(path.join(WEB, "iso.js"), "utf8");

export function between(src, startMarker, endMarker) {
  const a = src.indexOf(startMarker);
  if (a < 0) throw new Error(`iso.js no longer contains ${JSON.stringify(startMarker)} — ` +
    `the test extracts source by text, so a rename or reworded comment breaks it here`);
  const b = src.indexOf(endMarker, a);
  if (b < 0) throw new Error(`iso.js has ${JSON.stringify(startMarker)} but no ` +
    `${JSON.stringify(endMarker)} after it — the end marker moved or was reworded`);
  return src.slice(a, b);
}
