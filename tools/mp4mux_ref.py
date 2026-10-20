"""MP4 birleştirici — REFERANS gerçekleştirme (Python).

Android'in MediaMuxer'ı VP9'u mp4'e koyamadığı için görüntü ve ses (parçalı / fragmented mp4, DASH)
burada elle tek, düz bir mp4'e dizilir; yeniden kodlama yok. Kotlin sürümü (Mp4Mux.kt) bunun satır satır
karşılığıdır ve aynı girdiden BİREBİR aynı baytları üretmelidir (android/app/src/test/resources/mp4mux/).

Kullanım:  python3 tools/mp4mux_ref.py görüntü.mp4 ses.mp4 çıkış.mp4
"""

from __future__ import annotations

import struct
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

CHUNK_SECONDS = 1.0


def u32(b: bytes, o: int) -> int:
    return struct.unpack_from(">I", b, o)[0]


def u64(b: bytes, o: int) -> int:
    return struct.unpack_from(">Q", b, o)[0]


def box(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I4s", 8 + len(payload), kind) + payload


def full_box(kind: bytes, version: int, flags: int, payload: bytes) -> bytes:
    return box(kind, struct.pack(">I", (version << 24) | flags) + payload)


def children(data: bytes, start: int, end: int) -> List[Tuple[bytes, int, int, int]]:
    """(tür, kutu başı, içerik başı, kutu sonu)"""
    out = []
    pos = start
    while pos + 8 <= end:
        size = u32(data, pos)
        kind = data[pos + 4:pos + 8]
        head = 8
        if size == 1:
            size = u64(data, pos + 8)
            head = 16
        elif size == 0:
            size = end - pos
        if size < head or pos + size > end:
            break
        out.append((kind, pos, pos + head, pos + size))
        pos += size
    return out


def find(data: bytes, start: int, end: int, kind: bytes) -> Optional[Tuple[int, int, int]]:
    for k, b, c, e in children(data, start, end):
        if k == kind:
            return b, c, e
    return None


@dataclass
class Sample:
    offset: int
    size: int
    duration: int
    sync: bool
    cto: int


@dataclass
class Track:
    kind: str                  # "vide" / "soun"
    track_id: int
    timescale: int
    lang: bytes
    tkhd_tail: bytes
    hdlr: bytes
    media_header: bytes        # vmhd / smhd
    dinf: bytes
    stsd: bytes
    trex: Tuple[int, int, int] = (0, 0, 0)  # varsayılan süre, boyut, bayrak
    samples: List[Sample] = field(default_factory=list)


def parse(data: bytes) -> Track:
    top = children(data, 0, len(data))
    moov = next(((c, e) for k, b, c, e in top if k == b"moov"), None)
    if moov is None:
        raise ValueError("no moov")
    trak = find(data, moov[0], moov[1], b"trak")
    if trak is None:
        raise ValueError("no trak")
    tkhd = find(data, trak[1], trak[2], b"tkhd")
    mdia = find(data, trak[1], trak[2], b"mdia")
    if tkhd is None or mdia is None:
        raise ValueError("broken trak")
    ver = data[tkhd[1]]
    track_id = u32(data, tkhd[1] + (20 if ver == 1 else 12))
    tail = data[tkhd[1] + (36 if ver == 1 else 24):tkhd[2]]
    mdhd = find(data, mdia[1], mdia[2], b"mdhd")
    hdlr = find(data, mdia[1], mdia[2], b"hdlr")
    minf = find(data, mdia[1], mdia[2], b"minf")
    if mdhd is None or hdlr is None or minf is None:
        raise ValueError("broken mdia")
    mver = data[mdhd[1]]
    timescale = u32(data, mdhd[1] + (20 if mver == 1 else 12))
    lang = data[mdhd[1] + (32 if mver == 1 else 20):mdhd[1] + (34 if mver == 1 else 22)]
    handler = data[hdlr[1] + 8:hdlr[1] + 12].decode("latin1")
    stbl = find(data, minf[1], minf[2], b"stbl")
    if stbl is None:
        raise ValueError("no stbl")
    stsd = find(data, stbl[1], stbl[2], b"stsd")
    dinf = find(data, minf[1], minf[2], b"dinf")
    header = find(data, minf[1], minf[2], b"vmhd") or find(data, minf[1], minf[2], b"smhd")
    if stsd is None or dinf is None or header is None:
        raise ValueError("broken minf")
    trk = Track(handler, track_id, timescale, lang, tail, data[hdlr[0]:hdlr[2]], data[header[0]:header[2]],
                data[dinf[0]:dinf[2]], data[stsd[0]:stsd[2]])
    mvex = find(data, moov[0], moov[1], b"mvex")
    if mvex is not None:
        trex = find(data, mvex[1], mvex[2], b"trex")
        if trex is not None:
            trk.trex = (u32(data, trex[1] + 12), u32(data, trex[1] + 16), u32(data, trex[1] + 20))

    fragments = 0
    for k, b, c, e in top:
        if k != b"moof":
            continue
        fragments += 1
        for tk, tb, tc, te in children(data, c, e):
            if tk != b"traf":
                continue
            _read_traf(data, trk, b, tc, te)
    if fragments == 0:
        raise ValueError("not fragmented")
    if not trk.samples:
        raise ValueError("no samples")
    return trk


def _read_traf(data: bytes, trk: Track, moof_start: int, start: int, end: int) -> None:
    tfhd = find(data, start, end, b"tfhd")
    if tfhd is None:
        return
    flags = u32(data, tfhd[1]) & 0xFFFFFF
    p = tfhd[1] + 4
    if u32(data, p) != trk.track_id:
        return
    p += 4
    base = moof_start
    if flags & 0x1:
        base = u64(data, p)
        p += 8
    if flags & 0x2:
        p += 4
    dur, size, sflags = trk.trex
    if flags & 0x8:
        dur = u32(data, p); p += 4
    if flags & 0x10:
        size = u32(data, p); p += 4
    if flags & 0x20:
        sflags = u32(data, p); p += 4
    cursor = None
    for k, b, c, e in children(data, start, end):
        if k != b"trun":
            continue
        version = data[c]
        tf = u32(data, c) & 0xFFFFFF
        count = u32(data, c + 4)
        q = c + 8
        data_offset = None
        if tf & 0x1:
            data_offset = struct.unpack_from(">i", data, q)[0]; q += 4
        first_flags = None
        if tf & 0x4:
            first_flags = u32(data, q); q += 4
        if data_offset is not None:
            cursor = base + data_offset
        elif cursor is None:
            cursor = base
        for i in range(count):
            s_dur, s_size, s_flags, cto = dur, size, sflags, 0
            if tf & 0x100:
                s_dur = u32(data, q); q += 4
            if tf & 0x200:
                s_size = u32(data, q); q += 4
            if tf & 0x400:
                s_flags = u32(data, q); q += 4
            elif i == 0 and first_flags is not None:
                s_flags = first_flags
            if tf & 0x800:
                cto = struct.unpack_from(">i" if version == 1 else ">I", data, q)[0]; q += 4
            trk.samples.append(Sample(cursor, s_size, s_dur, not (s_flags & 0x10000), cto))
            cursor += s_size


def runs(values: List[int]) -> List[Tuple[int, int]]:
    out: List[Tuple[int, int]] = []
    for v in values:
        if out and out[-1][0] == v:
            out[-1] = (v, out[-1][1] + 1)
        else:
            out.append((v, 1))
    return out


def chunking(trk: Track) -> List[List[int]]:
    """Örnek sıra numaralarını yaklaşık CHUNK_SECONDS'lik öbeklere böler."""
    limit = int(CHUNK_SECONDS * trk.timescale)
    chunks: List[List[int]] = []
    current: List[int] = []
    elapsed = 0
    for i, s in enumerate(trk.samples):
        current.append(i)
        elapsed += s.duration
        if elapsed >= limit:
            chunks.append(current)
            current, elapsed = [], 0
    if current:
        chunks.append(current)
    return chunks


def stbl_of(trk: Track, chunk_offsets: List[int], chunks: List[List[int]]) -> bytes:
    s = trk.samples
    stts = full_box(b"stts", 0, 0, struct.pack(">I", len(runs([x.duration for x in s]))) +
                    b"".join(struct.pack(">II", n, v) for v, n in runs([x.duration for x in s])))
    parts = [trk.stsd, stts]
    if not all(x.sync for x in s):
        sync = [i + 1 for i, x in enumerate(s) if x.sync]
        parts.append(full_box(b"stss", 0, 0, struct.pack(">I", len(sync)) + b"".join(struct.pack(">I", i) for i in sync)))
    if any(x.cto != 0 for x in s):
        negative = any(x.cto < 0 for x in s)
        body = struct.pack(">I", len(runs([x.cto for x in s])))
        fmt = ">Ii" if negative else ">II"
        body += b"".join(struct.pack(fmt, n, v) for v, n in runs([x.cto for x in s]))
        parts.append(full_box(b"ctts", 1 if negative else 0, 0, body))
    # stsc: aynı örnek sayısına sahip ardışık öbekler tek satır
    stsc_rows = []
    prev = None
    for idx, ch in enumerate(chunks):
        if len(ch) != prev:
            stsc_rows.append((idx + 1, len(ch), 1))
            prev = len(ch)
    parts.append(full_box(b"stsc", 0, 0, struct.pack(">I", len(stsc_rows)) + b"".join(struct.pack(">III", *r) for r in stsc_rows)))
    parts.append(full_box(b"stsz", 0, 0, struct.pack(">II", 0, len(s)) + b"".join(struct.pack(">I", x.size) for x in s)))
    parts.append(full_box(b"stco", 0, 0, struct.pack(">I", len(chunk_offsets)) + b"".join(struct.pack(">I", o) for o in chunk_offsets)))
    return box(b"stbl", b"".join(parts))


def trak_of(trk: Track, track_id: int, chunk_offsets: List[int], chunks: List[List[int]]) -> bytes:
    total = sum(x.duration for x in trk.samples)
    movie_dur = total * 1000 // trk.timescale
    tkhd = full_box(b"tkhd", 0, 3, struct.pack(">IIIII", 0, 0, track_id, 0, movie_dur) + trk.tkhd_tail)
    mdhd = full_box(b"mdhd", 0, 0, struct.pack(">IIII", 0, 0, trk.timescale, total) + trk.lang + b"\x00\x00")
    minf = box(b"minf", trk.media_header + trk.dinf + stbl_of(trk, chunk_offsets, chunks))
    return box(b"trak", tkhd + box(b"mdia", mdhd + trk.hdlr + minf))


def moov_of(tracks: List[Track], offsets: List[List[int]], chunked: List[List[List[int]]]) -> bytes:
    movie_dur = max(sum(x.duration for x in t.samples) * 1000 // t.timescale for t in tracks)
    matrix = struct.pack(">9I", 0x10000, 0, 0, 0, 0x10000, 0, 0, 0, 0x40000000)
    mvhd = full_box(b"mvhd", 0, 0, struct.pack(">IIII", 0, 0, 1000, movie_dur) + struct.pack(">IH", 0x10000, 0x100) +
                    b"\x00" * 10 + matrix + b"\x00" * 24 + struct.pack(">I", len(tracks) + 1))
    traks = b"".join(trak_of(t, i + 1, offsets[i], chunked[i]) for i, t in enumerate(tracks))
    return box(b"moov", mvhd + traks)


def mux(video: bytes, audio: Optional[bytes]) -> bytes:
    sources = [video] + ([audio] if audio is not None else [])
    tracks = [parse(d) for d in sources]
    chunked = [chunking(t) for t in tracks]
    ftyp = box(b"ftyp", b"isom" + struct.pack(">I", 512) + b"isomiso2mp41")

    # Öbekleri zamana göre sırala (görüntü/ses iç içe): (başlangıç saniyesi, iz sırası)
    order: List[Tuple[float, int, int]] = []
    for ti, (t, chs) in enumerate(zip(tracks, chunked)):
        elapsed = 0
        for ci, ch in enumerate(chs):
            order.append((elapsed / t.timescale, ti, ci))
            elapsed += sum(t.samples[i].duration for i in ch)
    order.sort(key=lambda x: (x[0], x[1], x[2]))

    def layout(base: int) -> Tuple[List[List[int]], List[Tuple[int, int, int]]]:
        pos = base
        offs = [[0] * len(c) for c in chunked]
        plan = []
        for _, ti, ci in order:
            offs[ti][ci] = pos
            size = sum(tracks[ti].samples[i].size for i in chunked[ti][ci])
            plan.append((ti, ci, size))
            pos += size
        return offs, plan

    # moov önde (hızlı başlangıç): uzunluğu için önce sıfır konumla kur, sonra gerçeğiyle yeniden kur.
    first_offsets, _ = layout(0)
    moov_size = len(moov_of(tracks, first_offsets, chunked))
    data_start = len(ftyp) + moov_size + 8
    offsets, plan = layout(data_start)
    moov = moov_of(tracks, offsets, chunked)
    assert len(moov) == moov_size

    payload = bytearray()
    for ti, ci, _ in plan:
        for i in chunked[ti][ci]:
            s = tracks[ti].samples[i]
            payload += sources[ti][s.offset:s.offset + s.size]
    return ftyp + moov + struct.pack(">I4s", 8 + len(payload), b"mdat") + bytes(payload)


def main() -> int:
    if len(sys.argv) not in (3, 4):
        print(__doc__)
        return 2
    video = open(sys.argv[1], "rb").read()
    audio = open(sys.argv[2], "rb").read() if len(sys.argv) == 4 else None
    out = sys.argv[-1]
    if len(sys.argv) == 3:
        out = sys.argv[2]
    open(out, "wb").write(mux(video, audio))
    return 0


if __name__ == "__main__":
    sys.exit(main())
