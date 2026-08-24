#!/usr/bin/env python3
"""
LZARI — the compression inside AR Max / MAX Drive `.max` saves.

Haruhiko Okumura's 1989 LZARI: LZSS sliding-window matching with an adaptive
arithmetic coder over both the literal/length alphabet and the match positions.
Public-domain algorithm; this is an independent stdlib-only implementation (the
engines here take no third-party dependencies).

Two things make it fiddly to get right, and both are pinned by the round-trip
test in x2selftest.py:

* The bit layer is MSB-first and the decoder primes itself with M+2 bits before
  the first symbol. Get that off by one bit and the first symbol still decodes,
  so the failure shows up hundreds of bytes later.
* The arithmetic coder carries "pending" opposite bits (`shifts`) through the
  Q1..Q3 straddle case. Dropping them produces a stream that decodes correctly
  for a while and then diverges.

The position model is Okumura's fixed empirical curve (10000 / (i + 200)), not
something derived from the data — it has to match bit-for-bit or nothing decodes.
"""

# --- LZSS window -----------------------------------------------------------
N = 4096                      # ring buffer size
F = 60                        # longest match
THRESHOLD = 2                 # shortest match worth encoding as a match
NIL = N

# --- symbol alphabet: 256 literals + (F - THRESHOLD + 1) match lengths ------
N_CHAR = 256 - THRESHOLD + F  # 314
MAX_FREQ = 0x8000

# --- arithmetic coder ------------------------------------------------------
M = 15
Q1 = 1 << M                   # 0x8000
Q2 = 2 * Q1
Q3 = 3 * Q1
Q4 = 4 * Q1
MAX_CUM = Q1 - 1


class _Model:
    """Adaptive frequency model, shared by the encoder and the decoder.

    Symbols are kept ordered by frequency so the cumulative table stays cheap to
    search; char_to_sym / sym_to_char track the permutation."""

    def __init__(self):
        self.sym_freq = [0] * (N_CHAR + 1)
        self.sym_cum = [0] * (N_CHAR + 1)
        self.char_to_sym = [0] * N_CHAR
        self.sym_to_char = [0] * (N_CHAR + 1)
        self.sym_cum[N_CHAR] = 0
        for sym in range(N_CHAR, 0, -1):
            ch = sym - 1
            self.char_to_sym[ch] = sym
            self.sym_to_char[sym] = ch
            self.sym_freq[sym] = 1
            self.sym_cum[sym - 1] = self.sym_cum[sym] + self.sym_freq[sym]
        self.sym_freq[0] = 0          # sentinel: must differ from sym_freq[1]
        # Fixed position model. Okumura's "quite tentative" empirical curve —
        # it is part of the format, so it cannot be improved without breaking
        # compatibility with every existing .max file.
        self.position_cum = [0] * (N + 1)
        for i in range(N, 0, -1):
            self.position_cum[i - 1] = self.position_cum[i] + 10000 // (i + 200)

    def update(self, sym):
        if self.sym_cum[0] >= MAX_CUM:          # rescale before overflow
            c = 0
            for i in range(N_CHAR, 0, -1):
                self.sym_cum[i] = c
                self.sym_freq[i] = (self.sym_freq[i] + 1) >> 1
                c += self.sym_freq[i]
            self.sym_cum[0] = c
        # keep the alphabet sorted by frequency: swap this symbol up past every
        # symbol it has just drawn level with
        i = sym
        while self.sym_freq[i] == self.sym_freq[i - 1]:
            i -= 1
        if i < sym:
            ch_i, ch_sym = self.sym_to_char[i], self.sym_to_char[sym]
            self.sym_to_char[i], self.sym_to_char[sym] = ch_sym, ch_i
            self.char_to_sym[ch_i], self.char_to_sym[ch_sym] = sym, i
        self.sym_freq[i] += 1
        while i > 0:
            i -= 1
            self.sym_cum[i] += 1

    def search_sym(self, x):
        lo, hi = 1, N_CHAR
        while lo < hi:
            mid = (lo + hi) // 2
            if self.sym_cum[mid] > x:
                lo = mid + 1
            else:
                hi = mid
        return lo

    def search_pos(self, x):
        lo, hi = 1, N
        while lo < hi:
            mid = (lo + hi) // 2
            if self.position_cum[mid] > x:
                lo = mid + 1
            else:
                hi = mid
        return lo - 1


class _BitReader:
    """MSB-first, and reads zeroes forever past the end (the coder needs a few
    bits beyond the last byte to finish the final symbol)."""

    def __init__(self, data):
        self.data = data
        self.pos = 0
        self.buf = 0
        self.len = 0

    def bit(self):
        while self.len <= 8:
            b = self.data[self.pos] if self.pos < len(self.data) else 0
            self.pos += 1
            self.buf |= b << (8 - self.len)
            self.len += 8
        v = self.buf
        self.buf = (self.buf << 1) & 0xFFFF
        self.len -= 1
        return (v >> 15) & 1


class _BitWriter:
    def __init__(self):
        self.out = bytearray()
        self.buf = 0
        self.len = 0

    def bit(self, b):
        self.buf = ((self.buf << 1) | (b & 1)) & 0xFF
        self.len += 1
        if self.len == 8:
            self.out.append(self.buf)
            self.buf = 0
            self.len = 0

    def flush(self):
        while self.len:
            self.bit(0)
        return bytes(self.out)


def decompress(data, size):
    """Decompress `size` bytes out of an LZARI stream."""
    m = _Model()
    br = _BitReader(data)
    low, high, value = 0, Q4, 0
    for _ in range(M + 2):
        value = 2 * value + br.bit()

    def renorm(low, high, value):
        while True:
            if low >= Q2:
                value -= Q2; low -= Q2; high -= Q2
            elif low >= Q1 and high <= Q3:
                value -= Q1; low -= Q1; high -= Q1
            elif high > Q2:
                break
            low += low; high += high
            value = 2 * value + br.bit()
        return low, high, value

    def decode_char(low, high, value):
        rng = high - low
        x = ((value - low + 1) * m.sym_cum[0] - 1) // rng
        sym = m.search_sym(x)
        high = low + (rng * m.sym_cum[sym - 1]) // m.sym_cum[0]
        low += (rng * m.sym_cum[sym]) // m.sym_cum[0]
        low, high, value = renorm(low, high, value)
        ch = m.sym_to_char[sym]
        m.update(sym)
        return ch, low, high, value

    def decode_pos(low, high, value):
        rng = high - low
        x = ((value - low + 1) * m.position_cum[0] - 1) // rng
        pos = m.search_pos(x)
        high = low + (rng * m.position_cum[pos]) // m.position_cum[0]
        low += (rng * m.position_cum[pos + 1]) // m.position_cum[0]
        low, high, value = renorm(low, high, value)
        return pos, low, high, value

    text = bytearray(N)
    for i in range(N - F):
        text[i] = 0x20
    r = N - F
    out = bytearray()
    while len(out) < size:
        c, low, high, value = decode_char(low, high, value)
        if c < 256:
            out.append(c)
            text[r] = c
            r = (r + 1) & (N - 1)
        else:
            pos, low, high, value = decode_pos(low, high, value)
            i = (r - pos - 1) & (N - 1)
            for k in range(c - 255 + THRESHOLD):
                b = text[(i + k) & (N - 1)]
                out.append(b)
                text[r] = b
                r = (r + 1) & (N - 1)
                if len(out) >= size:
                    break
    return bytes(out[:size])


def compress(data):
    """Compress with LZARI. Round-trips through decompress()."""
    m = _Model()
    bw = _BitWriter()
    state = {"low": 0, "high": Q4, "shifts": 0}

    def output(bit):
        bw.bit(bit)
        while state["shifts"] > 0:
            bw.bit(1 - bit)
            state["shifts"] -= 1

    def renorm():
        while True:
            if state["high"] <= Q2:
                output(0)
            elif state["low"] >= Q2:
                output(1)
                state["low"] -= Q2; state["high"] -= Q2
            elif state["low"] >= Q1 and state["high"] <= Q3:
                state["shifts"] += 1
                state["low"] -= Q1; state["high"] -= Q1
            else:
                break
            state["low"] += state["low"]
            state["high"] += state["high"]

    def encode_char(ch):
        sym = m.char_to_sym[ch]
        rng = state["high"] - state["low"]
        state["high"] = state["low"] + (rng * m.sym_cum[sym - 1]) // m.sym_cum[0]
        state["low"] += (rng * m.sym_cum[sym]) // m.sym_cum[0]
        renorm()
        m.update(sym)

    def encode_pos(pos):
        rng = state["high"] - state["low"]
        state["high"] = state["low"] + (rng * m.position_cum[pos]) // m.position_cum[0]
        state["low"] += (rng * m.position_cum[pos + 1]) // m.position_cum[0]
        renorm()

    # Match finding runs over `data` itself rather than over a ring buffer. The
    # decoder's window position for input byte q is (N - F + q) mod N, so a match
    # at distance d = p - q is emitted as position d - 1 — and comparing
    # data[p+k] with data[q+k] reproduces the decoder's copy semantics exactly,
    # including overlapping matches (d < length), which a ring-buffer comparison
    # gets wrong because the overlapping bytes have not been written yet.
    #
    # Only q >= 0 is considered, so the space-primed head of the window is never
    # referenced. That costs a little ratio on the first few bytes and removes a
    # whole class of off-by-one.
    n = len(data)
    index = {}                       # 3-byte prefix -> recent input positions
    CANDIDATES = 64                  # newest-first cap: ratio for bounded time
    p = 0
    while p < n:
        best_len, best_q = 0, -1
        limit = min(F, n - p)
        if limit > THRESHOLD:
            key = bytes(data[p:p + 3])
            bucket = index.get(key)
            if bucket:
                for q in bucket[-CANDIDATES:][::-1]:
                    d = p - q
                    if d <= 0 or d > N - 1:
                        continue
                    ln = 0
                    while ln < limit and data[q + ln] == data[p + ln]:
                        ln += 1
                    if ln > best_len:
                        best_len, best_q = ln, q
                        if ln == limit:
                            break
        if best_len > THRESHOLD:
            encode_char(255 - THRESHOLD + best_len)
            encode_pos(p - best_q - 1)
            run = best_len
        else:
            encode_char(data[p])
            run = 1
        for k in range(run):
            i = p + k
            if i + 3 <= n:
                index.setdefault(bytes(data[i:i + 3]), []).append(i)
        p += run

    state["shifts"] += 1
    output(0 if state["low"] < Q1 else 1)
    return bw.flush()
