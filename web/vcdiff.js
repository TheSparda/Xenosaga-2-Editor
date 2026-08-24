// Minimal VCDIFF (RFC 3284) encoder — shared by the ISO editor (iso.js) and the Node tests.
// Ported from the Suikoden 3 editor, unchanged: it is game-agnostic (edits + source size in,
// patch out), and two copies that drift would be two different patch formats.
// It turns a set of known byte edits against a source file into a standard .xdelta patch that
// `xdelta3 -d -s <pristine> <patch> <out>` (or any VCDIFF decoder) applies to reproduce the
// edited file. We don't diff the 4 GB disc: the web editor already tracks every changed byte
// range, so we synthesize the patch directly as COPY-from-source + ADD-literal windows.
//
// Format choices (kept deliberately simple, all standard / decoder-required-to-support):
//   • header: magic D6 C3 C4 00, Hdr_Indicator 0x00 (no secondary compressor, no app header)
//   • per window: VCD_SOURCE, source segment = the same byte range this window outputs
//   • instructions use only the RFC default code table opcodes 1 (ADD, explicit size) and
//     19 (COPY, explicit size, mode 0 = VCD_SELF); no caches, no secondary compression.
// The whole target is tiled into windows so the decoder reproduces the entire file; unchanged
// windows are a single COPY, so the patch stays tiny even for a multi-GB disc.
(function (root) {
  // VCDIFF integer: base-128, big-endian, 0x80 continuation bit on all but the last byte.
  function pushInt(arr, n) {
    if (n < 0) throw new Error("vcdiff int < 0");
    const bytes = [n & 0x7f]; n = Math.floor(n / 128);
    while (n > 0) { bytes.push((n & 0x7f) | 0x80); n = Math.floor(n / 128); }
    for (let i = bytes.length - 1; i >= 0; i--) arr.push(bytes[i]);   // most-significant first
  }

  // edits: [{off, data:Uint8Array}] (data = the NEW bytes). sourceSize: total file length.
  // opts.window: target window size (default 8 MiB). Returns a Uint8Array patch.
  function buildXdelta(sourceSize, edits, opts) {
    const WIN = (opts && opts.window) || (8 * 1024 * 1024);
    edits = (edits || []).filter((e) => e.data && e.data.length)
      .map((e) => ({ off: e.off, data: e.data })).sort((a, b) => a.off - b.off);
    for (let i = 1; i < edits.length; i++)
      if (edits[i].off < edits[i - 1].off + edits[i - 1].data.length)
        throw new Error("vcdiff: overlapping edits");
    // Split any edit that straddles a window boundary so each piece lives in one window.
    const split = [];
    for (const e of edits) {
      let o = e.off, d = e.data;
      while (o + d.length > (Math.floor(o / WIN) + 1) * WIN) {
        const cut = (Math.floor(o / WIN) + 1) * WIN;
        split.push({ off: o, data: d.subarray(0, cut - o) });
        d = d.subarray(cut - o); o = cut;
      }
      split.push({ off: o, data: d });
    }
    edits = split;

    const out = [0xd6, 0xc3, 0xc4, 0x00, 0x00];   // magic + Hdr_Indicator (no app header)
    let ei = 0;                                    // index into edits, advanced across windows

    for (let ws = 0; ws < sourceSize; ws += WIN) {
      const we = Math.min(ws + WIN, sourceSize);
      const tlen = we - ws;
      const data = [], inst = [], addr = [];       // three window sections

      const emitCopy = (srcRel, len) => {          // COPY len bytes from source-segment offset srcRel
        if (len <= 0) return;
        inst.push(19); pushInt(inst, len);         // opcode 19 = COPY size0 mode0; size follows
        pushInt(addr, srcRel);                     // mode 0 (SELF): absolute addr = segment offset
      };
      const emitAdd = (bytes) => {
        if (!bytes.length) return;
        inst.push(1); pushInt(inst, bytes.length); // opcode 1 = ADD size0; size follows
        for (let i = 0; i < bytes.length; i++) data.push(bytes[i]);
      };

      let p = ws;
      while (ei < edits.length && edits[ei].off < we) {
        const e = edits[ei];
        if (e.off + e.data.length > we) throw new Error("vcdiff: edit spans a window boundary");
        emitCopy(p - ws, e.off - p);               // unchanged run before the edit
        emitAdd(e.data);                           // the edited bytes
        p = e.off + e.data.length;
        ei++;
      }
      emitCopy(p - ws, we - p);                     // trailing unchanged run

      // Delta encoding block: [target size][Delta_Indicator][|data|][|inst|][|addr|] data inst addr
      const block = [];
      pushInt(block, tlen);
      block.push(0x00);                             // Delta_Indicator: no section compression
      pushInt(block, data.length);
      pushInt(block, inst.length);
      pushInt(block, addr.length);
      for (const b of data) block.push(b);
      for (const b of inst) block.push(b);
      for (const b of addr) block.push(b);

      out.push(0x01);                              // Win_Indicator = VCD_SOURCE
      pushInt(out, tlen);                          // source segment size (= this window's span)
      pushInt(out, ws);                            // source segment position
      pushInt(out, block.length);                  // length of the delta encoding
      for (const b of block) out.push(b);
    }
    return Uint8Array.from(out);
  }

  const api = { buildXdelta, pushInt };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.Vcdiff = api;
})(typeof self !== "undefined" ? self : globalThis);
