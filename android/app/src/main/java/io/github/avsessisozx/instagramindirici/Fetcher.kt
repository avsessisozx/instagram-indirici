package io.github.avsessisozx.instagramindirici

import java.io.File
import java.io.IOException
import java.io.OutputStream
import java.net.HttpURLConnection
import java.net.SocketTimeoutException
import java.net.URL
import java.net.UnknownHostException
import java.util.concurrent.atomic.AtomicBoolean

/** İndirilen dosyanın yazılacağı yer. Android'de galeri (MediaStore), testlerde düz klasör. */
interface MediaSink {
    /** relStem: "kullanici/Fotoğraflar/2024-06-01_baslik_KOD" gibi, uzantısız. */
    fun exists(relStem: String, ext: String): Boolean
    fun begin(relStem: String, ext: String, mime: String): PendingFile
    /** Eski düzendeki dosyayı yeni yerine taşır (varsa). Taşındıysa true. */
    fun moveLegacy(oldRelStem: String, newRelStem: String, ext: String): Boolean = false
}

interface PendingFile {
    val stream: OutputStream
    fun commit(): String?   // kaydedilen dosyanın adresi (galeride açmak için)
    fun abort()
}

class CancelledException : Exception("cancelled")

/** Ayrı görüntü ve ses akışlarını yeniden kodlamadan tek mp4'e birleştirir. Hata olursa açıklamasını döndürür. */
interface Muxer {
    fun mux(video: File, audio: File?, out: File, cancel: AtomicBoolean): String?
}

/** Python fetcher.py'nin karşılığı: adresleri sırayla dener, ilk başarılı indirmeyi kaydeder. */
object Fetcher {
    data class Result(val ok: Boolean, val location: String? = null, val size: Long = 0, val error: Msg? = null, val status: Int = 0)

    private const val CHUNK = 256 * 1024
    private const val TIMEOUT_MS = 30_000

    private val CONTENT_EXT = mapOf(
        "image/jpeg" to ".jpg", "image/jpg" to ".jpg", "image/png" to ".png", "image/webp" to ".webp",
        "image/heic" to ".heic", "image/heif" to ".heic", "image/avif" to ".avif", "image/gif" to ".gif",
        "video/mp4" to ".mp4",
    )

    fun extFromContentType(contentType: String?, fallback: String): String =
        CONTENT_EXT[(contentType ?: "").substringBefore(';').trim().lowercase()] ?: fallback

    fun mimeForExt(ext: String): String = when (ext) {
        ".mp4" -> "video/mp4"
        ".png" -> "image/png"
        ".webp" -> "image/webp"
        ".heic" -> "image/heic"
        ".avif" -> "image/avif"
        ".gif" -> "image/gif"
        else -> "image/jpeg"
    }

    fun fetch(
        urls: List<String>,
        relStem: String,
        ext: String,
        fallbackExt: String,
        headers: Map<String, String>,
        sink: MediaSink,
        cancel: AtomicBoolean,
        onProgress: ((Long, Long) -> Unit)? = null,
        attemptsPerUrl: Int = 2,
    ): Result {
        var last = Result(false, error = msg("fetch.no_url"))
        for (url in urls) {
            for (attempt in 0 until attemptsPerUrl) {
                if (cancel.get()) throw CancelledException()
                val r = fetchOne(url, relStem, ext, fallbackExt, headers, sink, cancel, onProgress)
                if (r.ok) return r
                last = r
                if (r.status == 403 || r.status == 404 || r.status == 410) break
            }
        }
        return last
    }

    /**
     * En yüksek kalite (DASH): görüntü ve sesi ayrı indirip birleştirir. Başarısızsa geçici dosyalar silinir
     * ve çağıran normal sürüme döner.
     */
    fun fetchDash(
        choice: Media.DashChoice,
        relStem: String,
        headers: Map<String, String>,
        sink: MediaSink,
        cancel: AtomicBoolean,
        onProgress: ((Long, Long) -> Unit)?,
        tempDir: File,
        muxer: Muxer,
    ): Result {
        tempDir.mkdirs()
        val id = java.lang.Long.toString(System.nanoTime(), 36)
        val tmp = FileSink(tempDir)
        val files = mutableListOf<File>()
        try {
            val v = fetch(listOf(choice.video.url), "dv_$id", ".tmp", ".tmp", headers, tmp, cancel, onProgress)
            if (!v.ok || v.location == null) return v
            val videoFile = File(v.location); files += videoFile
            var audioFile: File? = null
            val audio = choice.audio
            if (audio != null) {
                val a = fetch(listOf(audio.url), "da_$id", ".tmp", ".tmp", headers, tmp, cancel, onProgress)
                if (!a.ok || a.location == null) return a
                audioFile = File(a.location); files += audioFile
            }
            val out = File(tempDir, "dm_$id.mp4"); files += out
            val problem = muxer.mux(videoFile, audioFile, out, cancel)
            if (problem != null) return Result(false, error = msg("fetch.mux_failed", "error" to problem))
            if (!out.isFile || out.length() == 0L) return Result(false, error = msg("fetch.mux_failed", "error" to "empty output"))
            val pending = sink.begin(relStem, ".mp4", "video/mp4")
            try {
                out.inputStream().use { it.copyTo(pending.stream, CHUNK) }
            } catch (e: Exception) {
                pending.abort()
                return Result(false, error = msg("fetch.write", "error" to (e.message ?: e.javaClass.simpleName)))
            }
            return Result(true, pending.commit(), out.length())
        } finally {
            files.forEach { it.delete() }
        }
    }

    private fun fetchOne(
        url: String, relStem: String, ext: String, fallbackExt: String, headers: Map<String, String>,
        sink: MediaSink, cancel: AtomicBoolean, onProgress: ((Long, Long) -> Unit)?,
    ): Result {
        var conn: HttpURLConnection? = null
        var pending: PendingFile? = null
        try {
            conn = (URL(url).openConnection() as HttpURLConnection).apply {
                connectTimeout = TIMEOUT_MS
                readTimeout = TIMEOUT_MS
                instanceFollowRedirects = true
                for ((k, v) in headers) setRequestProperty(k, v)
            }
            val status = conn.responseCode
            if (status !in 200..299) {
                return Result(false, error = msg("fetch.http", "code" to status), status = status)
            }
            val contentType = conn.contentType ?: ""
            val lowType = contentType.lowercase()
            if (lowType.startsWith("text/html") || lowType.startsWith("application/json") || lowType.startsWith("text/plain")) {
                return Result(false, error = msg("fetch.html_page"), status = 403)
            }
            val total = conn.contentLengthLong.coerceAtLeast(0)
            val finalExt = ext.ifEmpty { extFromContentType(contentType, fallbackExt) }
            pending = sink.begin(relStem, finalExt, mimeForExt(finalExt))
            var done = 0L
            conn.inputStream.use { input ->
                val buf = ByteArray(CHUNK)
                while (true) {
                    if (cancel.get()) throw CancelledException()
                    val n = input.read(buf)
                    if (n < 0) break
                    pending.stream.write(buf, 0, n)
                    done += n
                    onProgress?.invoke(done, total)
                }
            }
            if (done == 0L) {
                pending.abort(); pending = null
                return Result(false, error = msg("fetch.empty"), status = status)
            }
            if (total > 0 && done < total) {
                pending.abort(); pending = null
                return Result(false, error = msg("fetch.incomplete"), status = status)
            }
            val location = pending.commit()
            pending = null
            return Result(true, location, done, status = status)
        } catch (e: CancelledException) {
            pending?.abort()
            throw e
        } catch (e: UnknownHostException) {
            pending?.abort()
            return Result(false, error = msg("fetch.connection", "reason" to (e.message ?: "DNS")))
        } catch (e: SocketTimeoutException) {
            pending?.abort()
            return Result(false, error = msg("fetch.connection", "reason" to "timeout"))
        } catch (e: IOException) {
            pending?.abort()
            return Result(false, error = msg("fetch.connection", "reason" to (e.message ?: e.javaClass.simpleName)))
        } catch (e: Exception) {
            pending?.abort()
            return Result(false, error = msg("fetch.write", "error" to (e.message ?: e.javaClass.simpleName)))
        } finally {
            conn?.disconnect()
        }
    }
}
