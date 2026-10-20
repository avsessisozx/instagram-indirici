package io.github.avsessisozx.instagramindirici

import android.media.MediaCodec
import android.media.MediaExtractor
import android.media.MediaFormat
import android.media.MediaMuxer
import java.io.File
import java.nio.ByteBuffer
import java.util.concurrent.atomic.AtomicBoolean

/** Telefonun kendi MediaExtractor/MediaMuxer'ı ile görüntü ve sesi yeniden kodlamadan tek mp4'e koyar. */
object AndroidMuxer : Muxer {
    private const val BUFFER = 4 * 1024 * 1024

    private fun trackOf(ex: MediaExtractor, prefix: String): Int =
        (0 until ex.trackCount).firstOrNull { ex.getTrackFormat(it).getString(MediaFormat.KEY_MIME)?.startsWith(prefix) == true } ?: -1

    override fun mux(video: File, audio: File?, out: File, cancel: AtomicBoolean): String? {
        var videoEx: MediaExtractor? = null
        var audioEx: MediaExtractor? = null
        var muxer: MediaMuxer? = null
        var started = false
        try {
            val vEx = MediaExtractor().also { videoEx = it; it.setDataSource(video.absolutePath) }
            val vTrack = trackOf(vEx, "video/")
            if (vTrack < 0) return "no video track"
            vEx.selectTrack(vTrack)
            val vFormat = vEx.getTrackFormat(vTrack)

            var aEx: MediaExtractor? = null
            var aFormat: MediaFormat? = null
            if (audio != null) {
                val ex = MediaExtractor().also { audioEx = it; it.setDataSource(audio.absolutePath) }
                val aTrack = trackOf(ex, "audio/")
                if (aTrack < 0) return "no audio track"
                ex.selectTrack(aTrack)
                aFormat = ex.getTrackFormat(aTrack)
                aEx = ex
            }

            val mux = MediaMuxer(out.absolutePath, MediaMuxer.OutputFormat.MUXER_OUTPUT_MPEG_4).also { muxer = it }
            if (vFormat.containsKey(MediaFormat.KEY_ROTATION)) mux.setOrientationHint(vFormat.getInteger(MediaFormat.KEY_ROTATION))
            val vOut = try { mux.addTrack(vFormat) } catch (e: Exception) {
                return "video track ${vFormat.getString(MediaFormat.KEY_MIME)}: ${e.message}"
            }
            val aOut = if (aFormat != null) try { mux.addTrack(aFormat) } catch (e: Exception) {
                return "audio track ${aFormat.getString(MediaFormat.KEY_MIME)}: ${e.message}"
            } else -1
            mux.start(); started = true

            val buf = ByteBuffer.allocate(BUFFER)
            val info = MediaCodec.BufferInfo()
            var vDone = false
            var aDone = aEx == null
            while (!vDone || !aDone) {
                if (cancel.get()) throw CancelledException()
                val vt = if (vDone) Long.MAX_VALUE else vEx.sampleTime
                val at = if (aDone) Long.MAX_VALUE else aEx!!.sampleTime
                if (!vDone && vt < 0) { vDone = true; continue }
                if (!aDone && at < 0) { aDone = true; continue }
                val useVideo = vt <= at
                val ex = if (useVideo) vEx else aEx!!
                buf.clear()
                val size = ex.readSampleData(buf, 0)
                if (size < 0) { if (useVideo) vDone = true else aDone = true; continue }
                val flags = if (ex.sampleFlags and MediaExtractor.SAMPLE_FLAG_SYNC != 0) MediaCodec.BUFFER_FLAG_KEY_FRAME else 0
                info.set(0, size, ex.sampleTime, flags)
                mux.writeSampleData(if (useVideo) vOut else aOut, buf, info)
                ex.advance()
            }
            return null
        } catch (e: CancelledException) {
            throw e
        } catch (e: Exception) {
            return e.message ?: e.javaClass.simpleName
        } finally {
            try { if (started) muxer?.stop() } catch (e: Exception) {}
            try { muxer?.release() } catch (e: Exception) {}
            try { videoEx?.release() } catch (e: Exception) {}
            try { audioEx?.release() } catch (e: Exception) {}
        }
    }
}
