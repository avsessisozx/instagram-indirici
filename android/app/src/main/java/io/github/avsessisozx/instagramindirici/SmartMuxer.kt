package io.github.avsessisozx.instagramindirici

import java.io.File
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Önce telefonun kendi MediaMuxer'ı (H.264/H.265 + AAC için kanıtlanmış yol); olmazsa (VP9, AV1 ...) kendi
 * mp4 birleştiricimiz. İkisi de yeniden kodlamaz.
 */
object SmartMuxer : Muxer {
    override fun mux(video: File, audio: File?, out: File, cancel: AtomicBoolean): String? {
        val first = AndroidMuxer.mux(video, audio, out, cancel)
        if (first == null) return null
        out.delete()
        val second = Mp4Mux.mux(video, audio, out, cancel)
        if (second == null) return null
        out.delete()
        return "$first | $second"
    }
}
