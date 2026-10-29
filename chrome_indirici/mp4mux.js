// MP4 birleştirici – tools/mp4mux_ref.py (Python) ve Mp4Mux.kt ile aynı algoritma.
// Parçalı (fragmented) görüntü ve ses mp4 dosyalarını yeniden kodlamadan tek, düz bir mp4'e dizer.
(function (root) {
  "use strict";
  const CHUNK_SECONDS = 1.0;
  const enc = (s) => Uint8Array.from(s, (c) => c.charCodeAt(0));
  const dv = (b) => new DataView(b.buffer, b.byteOffset, b.byteLength);
  const u32 = (b, o) => dv(b).getUint32(o);
  const u64 = (b, o) => { const d = dv(b); return d.getUint32(o) * 4294967296 + d.getUint32(o + 4); };
  const tag = (b, o) => String.fromCharCode(b[o], b[o + 1], b[o + 2], b[o + 3]);

  function concat(parts) {
    let n = 0;
    for (const p of parts) n += p.length;
    const out = new Uint8Array(n);
    let o = 0;
    for (const p of parts) { out.set(p, o); o += p.length; }
    return out;
  }
  function pack(spec, ...vals) {            // spec: dizi, "I" (u32) | "i" (i32) | "H" (u16)
    let n = 0;
    for (const t of spec) n += t === "H" ? 2 : 4;
    const out = new Uint8Array(n), d = dv(out);
    let o = 0;
    spec.forEach((t, i) => {
      if (t === "I") d.setUint32(o, vals[i] >>> 0); else if (t === "i") d.setInt32(o, vals[i]); else d.setUint16(o, vals[i]);
      o += t === "H" ? 2 : 4;
    });
    return out;
  }
  const I = (n) => "I".repeat(n).split("");
  function box(kind, payload) { return concat([pack(["I"], 8 + payload.length), enc(kind), payload]); }
  function fullBox(kind, version, flags, payload) { return box(kind, concat([pack(["I"], ((version << 24) | flags) >>> 0), payload])); }

  function children(data, start, end) {
    const out = [];
    let pos = start;
    while (pos + 8 <= end) {
      let size = u32(data, pos);
      const kind = tag(data, pos + 4);
      let head = 8;
      if (size === 1) { size = u64(data, pos + 8); head = 16; }
      else if (size === 0) size = end - pos;
      if (size < head || pos + size > end) break;
      out.push([kind, pos, pos + head, pos + size]);
      pos += size;
    }
    return out;
  }
  function find(data, start, end, kind) {
    for (const [k, b, c, e] of children(data, start, end)) if (k === kind) return [b, c, e];
    return null;
  }

  function parse(data) {
    const top = children(data, 0, data.length);
    const moovE = top.find((x) => x[0] === "moov");
    if (!moovE) throw new Error("no moov");
    const moov = [moovE[2], moovE[3]];
    const trak = find(data, moov[0], moov[1], "trak");
    if (!trak) throw new Error("no trak");
    const tkhd = find(data, trak[1], trak[2], "tkhd");
    const mdia = find(data, trak[1], trak[2], "mdia");
    if (!tkhd || !mdia) throw new Error("broken trak");
    const ver = data[tkhd[1]];
    const trackId = u32(data, tkhd[1] + (ver === 1 ? 20 : 12));
    const tail = data.slice(tkhd[1] + (ver === 1 ? 36 : 24), tkhd[2]);
    const mdhd = find(data, mdia[1], mdia[2], "mdhd");
    const hdlr = find(data, mdia[1], mdia[2], "hdlr");
    const minf = find(data, mdia[1], mdia[2], "minf");
    if (!mdhd || !hdlr || !minf) throw new Error("broken mdia");
    const mver = data[mdhd[1]];
    const timescale = u32(data, mdhd[1] + (mver === 1 ? 20 : 12));
    const lang = data.slice(mdhd[1] + (mver === 1 ? 32 : 20), mdhd[1] + (mver === 1 ? 34 : 22));
    const handler = tag(data, hdlr[1] + 8);
    const stbl = find(data, minf[1], minf[2], "stbl");
    if (!stbl) throw new Error("no stbl");
    const stsd = find(data, stbl[1], stbl[2], "stsd");
    const dinf = find(data, minf[1], minf[2], "dinf");
    const header = find(data, minf[1], minf[2], "vmhd") || find(data, minf[1], minf[2], "smhd");
    if (!stsd || !dinf || !header) throw new Error("broken minf");
    const trk = { kind: handler, trackId, timescale, lang, tail, hdlr: data.slice(hdlr[0], hdlr[2]),
      header: data.slice(header[0], header[2]), dinf: data.slice(dinf[0], dinf[2]), stsd: data.slice(stsd[0], stsd[2]),
      trex: [0, 0, 0], samples: [] };
    const mvex = find(data, moov[0], moov[1], "mvex");
    if (mvex) {
      const trex = find(data, mvex[1], mvex[2], "trex");
      if (trex) trk.trex = [u32(data, trex[1] + 12), u32(data, trex[1] + 16), u32(data, trex[1] + 20)];
    }
    let fragments = 0;
    for (const [k, b, c, e] of top) {
      if (k !== "moof") continue;
      fragments++;
      for (const [tk, , tc, te] of children(data, c, e)) if (tk === "traf") readTraf(data, trk, b, tc, te);
    }
    if (!fragments) throw new Error("not fragmented");
    if (!trk.samples.length) throw new Error("no samples");
    return trk;
  }

  function readTraf(data, trk, moofStart, start, end) {
    const tfhd = find(data, start, end, "tfhd");
    if (!tfhd) return;
    const flags = u32(data, tfhd[1]) & 0xFFFFFF;
    let p = tfhd[1] + 4;
    if (u32(data, p) !== trk.trackId) return;
    p += 4;
    let base = moofStart;
    if (flags & 0x1) { base = u64(data, p); p += 8; }
    if (flags & 0x2) p += 4;
    let [dur, size, sflags] = trk.trex;
    if (flags & 0x8) { dur = u32(data, p); p += 4; }
    if (flags & 0x10) { size = u32(data, p); p += 4; }
    if (flags & 0x20) { sflags = u32(data, p); p += 4; }
    let cursor = null;
    for (const [k, , c] of children(data, start, end)) {
      if (k !== "trun") continue;
      const version = data[c];
      const tf = u32(data, c) & 0xFFFFFF;
      const count = u32(data, c + 4);
      let q = c + 8;
      let dataOffset = null;
      if (tf & 0x1) { dataOffset = dv(data).getInt32(q); q += 4; }
      let firstFlags = null;
      if (tf & 0x4) { firstFlags = u32(data, q); q += 4; }
      if (dataOffset !== null) cursor = base + dataOffset; else if (cursor === null) cursor = base;
      for (let i = 0; i < count; i++) {
        let sDur = dur, sSize = size, sFlags = sflags, cto = 0;
        if (tf & 0x100) { sDur = u32(data, q); q += 4; }
        if (tf & 0x200) { sSize = u32(data, q); q += 4; }
        if (tf & 0x400) { sFlags = u32(data, q); q += 4; } else if (i === 0 && firstFlags !== null) sFlags = firstFlags;
        if (tf & 0x800) { cto = version === 1 ? dv(data).getInt32(q) : u32(data, q); q += 4; }
        trk.samples.push({ offset: cursor, size: sSize, duration: sDur, sync: !(sFlags & 0x10000), cto });
        cursor += sSize;
      }
    }
  }

  function runs(values) {
    const out = [];
    for (const v of values) {
      if (out.length && out[out.length - 1][0] === v) out[out.length - 1][1]++; else out.push([v, 1]);
    }
    return out;
  }

  function chunking(trk) {
    const limit = Math.floor(CHUNK_SECONDS * trk.timescale);
    const chunks = [];
    let cur = [], elapsed = 0;
    trk.samples.forEach((s, i) => {
      cur.push(i);
      elapsed += s.duration;
      if (elapsed >= limit) { chunks.push(cur); cur = []; elapsed = 0; }
    });
    if (cur.length) chunks.push(cur);
    return chunks;
  }

  function stblOf(trk, chunkOffsets, chunks) {
    const s = trk.samples;
    const dr = runs(s.map((x) => x.duration));
    const stts = fullBox("stts", 0, 0, concat([pack(["I"], dr.length), ...dr.map(([v, n]) => pack(["I", "I"], n, v))]));
    const parts = [trk.stsd, stts];
    if (!s.every((x) => x.sync)) {
      const sync = [];
      s.forEach((x, i) => { if (x.sync) sync.push(i + 1); });
      parts.push(fullBox("stss", 0, 0, concat([pack(["I"], sync.length), ...sync.map((i) => pack(["I"], i))])));
    }
    if (s.some((x) => x.cto !== 0)) {
      const negative = s.some((x) => x.cto < 0);
      const cr = runs(s.map((x) => x.cto));
      const body = concat([pack(["I"], cr.length), ...cr.map(([v, n]) => pack(negative ? ["I", "i"] : ["I", "I"], n, v))]);
      parts.push(fullBox("ctts", negative ? 1 : 0, 0, body));
    }
    const rows = [];
    let prev = null;
    chunks.forEach((ch, idx) => { if (ch.length !== prev) { rows.push([idx + 1, ch.length, 1]); prev = ch.length; } });
    parts.push(fullBox("stsc", 0, 0, concat([pack(["I"], rows.length), ...rows.map((r) => pack(I(3), ...r))])));
    parts.push(fullBox("stsz", 0, 0, concat([pack(["I", "I"], 0, s.length), ...s.map((x) => pack(["I"], x.size))])));
    parts.push(fullBox("stco", 0, 0, concat([pack(["I"], chunkOffsets.length), ...chunkOffsets.map((o) => pack(["I"], o))])));
    return box("stbl", concat(parts));
  }

  function trakOf(trk, trackId, chunkOffsets, chunks) {
    let total = 0;
    for (const x of trk.samples) total += x.duration;
    const movieDur = Math.floor(total * 1000 / trk.timescale);
    const tkhd = fullBox("tkhd", 0, 3, concat([pack(I(5), 0, 0, trackId, 0, movieDur), trk.tail]));
    const mdhd = fullBox("mdhd", 0, 0, concat([pack(I(4), 0, 0, trk.timescale, total), trk.lang, new Uint8Array(2)]));
    const minf = box("minf", concat([trk.header, trk.dinf, stblOf(trk, chunkOffsets, chunks)]));
    return box("trak", concat([tkhd, box("mdia", concat([mdhd, trk.hdlr, minf]))]));
  }

  function moovOf(tracks, offsets, chunked) {
    let movieDur = 0;
    for (const t of tracks) {
      let total = 0;
      for (const x of t.samples) total += x.duration;
      movieDur = Math.max(movieDur, Math.floor(total * 1000 / t.timescale));
    }
    const matrix = pack(I(9), 0x10000, 0, 0, 0, 0x10000, 0, 0, 0, 0x40000000);
    const mvhd = fullBox("mvhd", 0, 0, concat([pack(I(4), 0, 0, 1000, movieDur), pack(["I", "H"], 0x10000, 0x100),
      new Uint8Array(10), matrix, new Uint8Array(24), pack(["I"], tracks.length + 1)]));
    const traks = concat(tracks.map((t, i) => trakOf(t, i + 1, offsets[i], chunked[i])));
    return box("moov", concat([mvhd, traks]));
  }

  // video, audio: Uint8Array (audio boş olabilir). Dönen: Uint8Array (düz mp4)
  function mux(video, audio) {
    const sources = audio ? [video, audio] : [video];
    const tracks = sources.map(parse);
    const chunked = tracks.map(chunking);
    const ftyp = box("ftyp", concat([enc("isom"), pack(["I"], 512), enc("isomiso2mp41")]));
    const order = [];
    tracks.forEach((t, ti) => {
      let elapsed = 0;
      chunked[ti].forEach((ch, ci) => {
        order.push([elapsed / t.timescale, ti, ci]);
        for (const i of ch) elapsed += t.samples[i].duration;
      });
    });
    order.sort((a, b) => (a[0] - b[0]) || (a[1] - b[1]) || (a[2] - b[2]));
    const layout = (base) => {
      let pos = base;
      const offs = chunked.map((c) => new Array(c.length).fill(0));
      const plan = [];
      for (const [, ti, ci] of order) {
        offs[ti][ci] = pos;
        let size = 0;
        for (const i of chunked[ti][ci]) size += tracks[ti].samples[i].size;
        plan.push([ti, ci, size]);
        pos += size;
      }
      return [offs, plan];
    };
    const [first] = layout(0);
    const moovSize = moovOf(tracks, first, chunked).length;
    const dataStart = ftyp.length + moovSize + 8;
    const [offsets, plan] = layout(dataStart);
    const moov = moovOf(tracks, offsets, chunked);
    if (moov.length !== moovSize) throw new Error("moov size mismatch");
    let payloadLen = 0;
    for (const [, , size] of plan) payloadLen += size;
    const out = new Uint8Array(ftyp.length + moov.length + 8 + payloadLen);
    out.set(ftyp, 0);
    out.set(moov, ftyp.length);
    let o = ftyp.length + moov.length;
    out.set(pack(["I"], 8 + payloadLen), o); out.set(enc("mdat"), o + 4); o += 8;
    for (const [ti, ci] of plan) {
      for (const i of chunked[ti][ci]) {
        const s = tracks[ti].samples[i];
        out.set(sources[ti].subarray(s.offset, s.offset + s.size), o);
        o += s.size;
      }
    }
    return out;
  }

  root.IIMp4Mux = { mux };
})(typeof globalThis !== "undefined" ? globalThis : this);
