package io.github.avsessisozx.instagramindirici

import java.io.ByteArrayOutputStream
import java.io.File
import java.io.RandomAccessFile
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Parçalı (fragmented, DASH) mp4 görüntü + ses dosyasını yeniden kodlamadan tek, düz bir mp4'e dizer.
 *
 * Telefonun MediaMuxer'ı VP9'u mp4'e koyamıyor; Instagram'ın en yüksek kalite akışı çoğu zaman VP9.
 * Bu sınıf tools/mp4mux_ref.py'nin satır satır karşılığıdır ve aynı girdiden BİREBİR aynı baytları üretmelidir
 * (test: Mp4MuxTest, android/app/src/test/resources/mp4mux). Saf JVM: Android'e bağımlı değil.
 */
object Mp4Mux : Muxer {
    private const val CHUNK_SECONDS = 1.0
    private const val MAX_BOX = 64L * 1024 * 1024   // moov / moof için makul üst sınır

    private class Box(val kind: String, val start: Int, val content: Int, val end: Int)

    private class Sample(val offset: Long, val size: Long, val duration: Long, val sync: Boolean, val cto: Long)

    private class Track(
        val track_id: Long, val timescale: Long, val lang: ByteArray, val tkhdTail: ByteArray,
        val hdlr: ByteArray, val mediaHeader: ByteArray, val dinf: ByteArray, val stsd: ByteArray,
        val trexDuration: Long, val trexSize: Long, val trexFlags: Long,
    ) {
        val samples = ArrayList<Sample>()
        val total: Long get() = samples.sumOf { it.duration }
    }

    // ---- ikili okuma / yazma yardımcıları
    private fun u32(b: ByteArray, o: Int): Long =
        ((b[o].toLong() and 0xFF) shl 24) or ((b[o + 1].toLong() and 0xFF) shl 16) or
            ((b[o + 2].toLong() and 0xFF) shl 8) or (b[o + 3].toLong() and 0xFF)

    private fun u64(b: ByteArray, o: Int): Long = (u32(b, o) shl 32) or u32(b, o + 4)

    private fun be32(v: Long): ByteArray = byteArrayOf((v shr 24).toByte(), (v shr 16).toByte(), (v shr 8).toByte(), v.toByte())
    private fun be16(v: Int): ByteArray = byteArrayOf((v shr 8).toByte(), v.toByte())

    private fun cat(vararg parts: ByteArray): ByteArray {
        val out = ByteArrayOutputStream()
        for (p in parts) out.write(p)
        return out.toByteArray()
    }

    private fun box(kind: String, vararg payload: ByteArray): ByteArray {
        val body = cat(*payload)
        return cat(be32(8L + body.size), kind.toByteArray(Charsets.ISO_8859_1), body)
    }

    private fun fullBox(kind: String, version: Int, flags: Int, vararg payload: ByteArray): ByteArray =
        box(kind, be32(((version.toLong() shl 24) or flags.toLong())), *payload)

    private fun children(data: ByteArray, start: Int, end: Int): List<Box> {
        val out = ArrayList<Box>()
        var pos = start
        while (pos + 8 <= end) {
            var size = u32(data, pos)
            val kind = String(data, pos + 4, 4, Charsets.ISO_8859_1)
            var head = 8
            if (size == 1L) {
                size = u64(data, pos + 8); head = 16
            } else if (size == 0L) {
                size = (end - pos).toLong()
            }
            if (size < head || pos + size > end) break
            out.add(Box(kind, pos, pos + head, (pos + size).toInt()))
            pos += size.toInt()
        }
        return out
    }

    private fun find(data: ByteArray, start: Int, end: Int, kind: String): Box? = children(data, start, end).firstOrNull { it.kind == kind }

    // ---- girdiyi okuma
    private fun readTrack(file: File): Track {
        RandomAccessFile(file, "r").use { raf ->
            val length = raf.length()
            var pos = 0L
            var track: Track? = null
            var fragments = 0
            val head = ByteArray(16)
            while (pos + 8 <= length) {
                raf.seek(pos)
                raf.readFully(head, 0, 8)
                var size = u32(head, 0)
                val kind = String(head, 4, 4, Charsets.ISO_8859_1)
                if (size == 1L) {
                    raf.readFully(head, 8, 8); size = u64(head, 8)
                } else if (size == 0L) {
                    size = length - pos
                }
                if (size < 8 || pos + size > length) break
                if (kind == "moov") {
                    if (size > MAX_BOX) throw IllegalArgumentException("moov too large")
                    val data = ByteArray(size.toInt()); raf.seek(pos); raf.readFully(data)
                    track = parseMoov(data)
                } else if (kind == "moof") {
                    if (size > MAX_BOX) throw IllegalArgumentException("moof too large")
                    val t = track ?: throw IllegalArgumentException("moof before moov")
                    val data = ByteArray(size.toInt()); raf.seek(pos); raf.readFully(data)
                    fragments++
                    val top = children(data, 0, data.size)[0]
                    for (b in children(data, top.content, top.end)) if (b.kind == "traf") readTraf(data, t, pos, b.content, b.end)
                }
                pos += size
            }
            val t = track ?: throw IllegalArgumentException("no moov")
            if (fragments == 0) throw IllegalArgumentException("not fragmented")
            if (t.samples.isEmpty()) throw IllegalArgumentException("no samples")
            return t
        }
    }

    private fun parseMoov(data: ByteArray): Track {
        val moov = children(data, 0, data.size)[0]
        val trak = find(data, moov.content, moov.end, "trak") ?: throw IllegalArgumentException("no trak")
        val tkhd = find(data, trak.content, trak.end, "tkhd")
        val mdia = find(data, trak.content, trak.end, "mdia")
        if (tkhd == null || mdia == null) throw IllegalArgumentException("broken trak")
        val ver = data[tkhd.content].toInt()
        val trackId = u32(data, tkhd.content + if (ver == 1) 20 else 12)
        val tail = data.copyOfRange(tkhd.content + if (ver == 1) 36 else 24, tkhd.end)
        val mdhd = find(data, mdia.content, mdia.end, "mdhd")
        val hdlr = find(data, mdia.content, mdia.end, "hdlr")
        val minf = find(data, mdia.content, mdia.end, "minf")
        if (mdhd == null || hdlr == null || minf == null) throw IllegalArgumentException("broken mdia")
        val mver = data[mdhd.content].toInt()
        val timescale = u32(data, mdhd.content + if (mver == 1) 20 else 12)
        val langAt = mdhd.content + if (mver == 1) 32 else 20
        val lang = data.copyOfRange(langAt, langAt + 2)
        val stbl = find(data, minf.content, minf.end, "stbl") ?: throw IllegalArgumentException("no stbl")
        val stsd = find(data, stbl.content, stbl.end, "stsd")
        val dinf = find(data, minf.content, minf.end, "dinf")
        val header = find(data, minf.content, minf.end, "vmhd") ?: find(data, minf.content, minf.end, "smhd")
        if (stsd == null || dinf == null || header == null) throw IllegalArgumentException("broken minf")
        var trexDur = 0L; var trexSize = 0L; var trexFlags = 0L
        val mvex = find(data, moov.content, moov.end, "mvex")
        if (mvex != null) {
            val trex = find(data, mvex.content, mvex.end, "trex")
            if (trex != null) {
                trexDur = u32(data, trex.content + 12); trexSize = u32(data, trex.content + 16); trexFlags = u32(data, trex.content + 20)
            }
        }
        return Track(
            trackId, timescale, lang, tail,
            data.copyOfRange(hdlr.start, hdlr.end), data.copyOfRange(header.start, header.end),
            data.copyOfRange(dinf.start, dinf.end), data.copyOfRange(stsd.start, stsd.end),
            trexDur, trexSize, trexFlags,
        )
    }

    private fun readTraf(data: ByteArray, trk: Track, moofStart: Long, start: Int, end: Int) {
        val tfhd = find(data, start, end, "tfhd") ?: return
        val flags = u32(data, tfhd.content) and 0xFFFFFF
        var p = tfhd.content + 4
        if (u32(data, p) != trk.track_id) return
        p += 4
        var base = moofStart
        if (flags and 0x1L != 0L) { base = u64(data, p); p += 8 }
        if (flags and 0x2L != 0L) p += 4
        var dur = trk.trexDuration; var size = trk.trexSize; var sflags = trk.trexFlags
        if (flags and 0x8L != 0L) { dur = u32(data, p); p += 4 }
        if (flags and 0x10L != 0L) { size = u32(data, p); p += 4 }
        if (flags and 0x20L != 0L) { sflags = u32(data, p); p += 4 }
        var cursor = -1L
        for (b in children(data, start, end)) {
            if (b.kind != "trun") continue
            val version = data[b.content].toInt()
            val tf = u32(data, b.content) and 0xFFFFFF
            val count = u32(data, b.content + 4)
            var q = b.content + 8
            var dataOffset = 0L; var hasOffset = false
            if (tf and 0x1L != 0L) { dataOffset = u32(data, q).toInt().toLong(); q += 4; hasOffset = true }
            var firstFlags = 0L; var hasFirst = false
            if (tf and 0x4L != 0L) { firstFlags = u32(data, q); q += 4; hasFirst = true }
            if (hasOffset) cursor = base + dataOffset else if (cursor < 0) cursor = base
            for (i in 0 until count.toInt()) {
                var sDur = dur; var sSize = size; var sFlags = sflags; var cto = 0L
                if (tf and 0x100L != 0L) { sDur = u32(data, q); q += 4 }
                if (tf and 0x200L != 0L) { sSize = u32(data, q); q += 4 }
                if (tf and 0x400L != 0L) { sFlags = u32(data, q); q += 4 } else if (i == 0 && hasFirst) sFlags = firstFlags
                if (tf and 0x800L != 0L) {
                    cto = if (version == 1) u32(data, q).toInt().toLong() else u32(data, q)
                    q += 4
                }
                trk.samples.add(Sample(cursor, sSize, sDur, sFlags and 0x10000L == 0L, cto))
                cursor += sSize
            }
        }
    }

    // ---- çıktıyı kurma
    private fun <T> runs(values: List<T>): List<Pair<T, Int>> {
        val out = ArrayList<Pair<T, Int>>()
        for (v in values) {
            if (out.isNotEmpty() && out.last().first == v) out[out.size - 1] = v to (out.last().second + 1) else out.add(v to 1)
        }
        return out
    }

    private fun chunking(trk: Track): List<List<Int>> {
        val limit = (CHUNK_SECONDS * trk.timescale).toLong()
        val chunks = ArrayList<List<Int>>()
        var current = ArrayList<Int>()
        var elapsed = 0L
        for ((i, s) in trk.samples.withIndex()) {
            current.add(i)
            elapsed += s.duration
            if (elapsed >= limit) { chunks.add(current); current = ArrayList(); elapsed = 0 }
        }
        if (current.isNotEmpty()) chunks.add(current)
        return chunks
    }

    private fun stblOf(trk: Track, chunkOffsets: List<Long>, chunks: List<List<Int>>): ByteArray {
        val s = trk.samples
        val durRuns = runs(s.map { it.duration })
        val stts = fullBox("stts", 0, 0, be32(durRuns.size.toLong()), *durRuns.map { cat(be32(it.second.toLong()), be32(it.first)) }.toTypedArray())
        val parts = ArrayList<ByteArray>()
        parts.add(trk.stsd); parts.add(stts)
        if (!s.all { it.sync }) {
            val sync = s.withIndex().filter { it.value.sync }.map { (it.index + 1).toLong() }
            parts.add(fullBox("stss", 0, 0, be32(sync.size.toLong()), *sync.map { be32(it) }.toTypedArray()))
        }
        if (s.any { it.cto != 0L }) {
            val negative = s.any { it.cto < 0 }
            val ctoRuns = runs(s.map { it.cto })
            parts.add(fullBox("ctts", if (negative) 1 else 0, 0, be32(ctoRuns.size.toLong()),
                *ctoRuns.map { cat(be32(it.second.toLong()), be32(it.first and 0xFFFFFFFFL)) }.toTypedArray()))
        }
        val stscRows = ArrayList<Triple<Long, Long, Long>>()
        var prev = -1
        for ((idx, ch) in chunks.withIndex()) {
            if (ch.size != prev) { stscRows.add(Triple((idx + 1).toLong(), ch.size.toLong(), 1L)); prev = ch.size }
        }
        parts.add(fullBox("stsc", 0, 0, be32(stscRows.size.toLong()), *stscRows.map { cat(be32(it.first), be32(it.second), be32(it.third)) }.toTypedArray()))
        parts.add(fullBox("stsz", 0, 0, be32(0), be32(s.size.toLong()), *s.map { be32(it.size) }.toTypedArray()))
        parts.add(fullBox("stco", 0, 0, be32(chunkOffsets.size.toLong()), *chunkOffsets.map { be32(it) }.toTypedArray()))
        return box("stbl", *parts.toTypedArray())
    }

    private fun trakOf(trk: Track, trackId: Long, chunkOffsets: List<Long>, chunks: List<List<Int>>): ByteArray {
        val total = trk.total
        val movieDur = total * 1000 / trk.timescale
        val tkhd = fullBox("tkhd", 0, 3, be32(0), be32(0), be32(trackId), be32(0), be32(movieDur), trk.tkhdTail)
        val mdhd = fullBox("mdhd", 0, 0, be32(0), be32(0), be32(trk.timescale), be32(total), trk.lang, byteArrayOf(0, 0))
        val minf = box("minf", trk.mediaHeader, trk.dinf, stblOf(trk, chunkOffsets, chunks))
        return box("trak", tkhd, box("mdia", mdhd, trk.hdlr, minf))
    }

    private fun moovOf(tracks: List<Track>, offsets: List<List<Long>>, chunked: List<List<List<Int>>>): ByteArray {
        val movieDur = tracks.maxOf { it.total * 1000 / it.timescale }
        val matrix = cat(be32(0x10000), be32(0), be32(0), be32(0), be32(0x10000), be32(0), be32(0), be32(0), be32(0x40000000))
        val mvhd = fullBox("mvhd", 0, 0, be32(0), be32(0), be32(1000), be32(movieDur), be32(0x10000), be16(0x100),
            ByteArray(10), matrix, ByteArray(24), be32(tracks.size + 1L))
        val traks = tracks.indices.map { trakOf(tracks[it], it + 1L, offsets[it], chunked[it]) }
        return box("moov", mvhd, *traks.toTypedArray())
    }

    private class Planned(val t: Double, val track: Int, val chunk: Int)

    /** @return null = tamam, aksi halde sorunun kısa açıklaması. Çıktı dosyası başarısızlıkta silinir. */
    override fun mux(video: File, audio: File?, out: File, cancel: AtomicBoolean): String? {
        val sources = listOfNotNull(video, audio)
        var written = false
        try {
            val tracks = sources.map { readTrack(it) }
            val chunked = tracks.map { chunking(it) }
            val ftyp = box("ftyp", "isom".toByteArray(), be32(512), "isomiso2mp41".toByteArray())

            val order = ArrayList<Planned>()
            for ((ti, pair) in tracks.zip(chunked).withIndex()) {
                var elapsed = 0L
                for ((ci, ch) in pair.second.withIndex()) {
                    order.add(Planned(elapsed.toDouble() / pair.first.timescale, ti, ci))
                    elapsed += ch.sumOf { pair.first.samples[it].duration }
                }
            }
            order.sortWith(compareBy<Planned>({ it.t }, { it.track }, { it.chunk }))

            fun layout(base: Long): List<List<Long>> {
                var pos = base
                val offs = chunked.map { c -> LongArray(c.size) }
                for (p in order) {
                    offs[p.track][p.chunk] = pos
                    pos += chunked[p.track][p.chunk].sumOf { tracks[p.track].samples[it].size }
                }
                if (pos > 0xFFFFFFFFL - 8) throw IllegalArgumentException("file too large")
                return offs.map { it.toList() }
            }

            val moovSize = moovOf(tracks, layout(0), chunked).size
            val dataStart = ftyp.size + moovSize + 8L
            val offsets = layout(dataStart)
            val moov = moovOf(tracks, offsets, chunked)
            check(moov.size == moovSize)

            val payloadSize = order.sumOf { p -> chunked[p.track][p.chunk].sumOf { tracks[p.track].samples[it].size } }
            val files = sources.map { RandomAccessFile(it, "r") }
            try {
                out.outputStream().buffered(1 shl 16).use { os ->
                    written = true
                    os.write(ftyp); os.write(moov)
                    os.write(be32(8 + payloadSize)); os.write("mdat".toByteArray())
                    var buf = ByteArray(1 shl 16)
                    for (p in order) {
                        if (cancel.get()) throw CancelledException()
                        val raf = files[p.track]
                        for (i in chunked[p.track][p.chunk]) {
                            val s = tracks[p.track].samples[i]
                            if (s.size > buf.size) buf = ByteArray(s.size.toInt())
                            raf.seek(s.offset)
                            raf.readFully(buf, 0, s.size.toInt())
                            os.write(buf, 0, s.size.toInt())
                        }
                    }
                }
            } finally {
                files.forEach { try { it.close() } catch (e: Exception) {} }
            }
            return null
        } catch (e: CancelledException) {
            if (written) out.delete()
            throw e
        } catch (e: Exception) {
            if (written) out.delete()
            return "mp4mux: " + (e.message ?: e.javaClass.simpleName)
        }
    }
}
