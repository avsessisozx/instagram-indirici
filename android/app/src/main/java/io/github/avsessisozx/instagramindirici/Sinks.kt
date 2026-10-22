package io.github.avsessisozx.instagramindirici

import android.content.ContentValues
import android.content.Context
import android.net.Uri
import android.provider.MediaStore
import java.io.BufferedOutputStream
import java.io.File
import java.io.FileOutputStream
import java.io.OutputStream

val KNOWN_EXTS = listOf(".mp4", ".jpg", ".jpeg", ".png", ".webp", ".heic", ".avif", ".gif")

/**
 * Galeriye kaydeder: DCIM/Instagram/<kullanıcı>/Fotoğraflar/..., Videolar/..., Hikayeler/...,
 * Öne Çıkanlar/<başlık>/..., Profil Fotoğrafı/... (Windows sürümündeki klasör düzeninin aynısı).
 */
class MediaStoreSink(private val ctx: Context) : MediaSink {
    private val resolver = ctx.contentResolver

    private fun split(relStem: String): Pair<String, String> {
        val dir = relStem.substringBeforeLast('/', "")
        val name = relStem.substringAfterLast('/')
        return (if (dir.isEmpty()) BASE else "$BASE$dir/") to name
    }

    override fun exists(relStem: String, ext: String): Boolean {
        val (dir, name) = split(relStem)
        val names = if (ext.isNotEmpty()) listOf(name + ext) else KNOWN_EXTS.map { name + it }
        val placeholders = names.joinToString(",") { "?" }
        val args = arrayOf(dir, *names.toTypedArray())
        return try {
            resolver.query(
                MediaStore.Files.getContentUri(MediaStore.VOLUME_EXTERNAL),
                arrayOf(MediaStore.MediaColumns._ID, MediaStore.MediaColumns.SIZE),
                "${MediaStore.MediaColumns.RELATIVE_PATH}=? AND ${MediaStore.MediaColumns.DISPLAY_NAME} IN ($placeholders) " +
                    "AND ${MediaStore.MediaColumns.IS_PENDING}=0",
                args, null,
            )?.use { c ->
                while (c.moveToNext()) if (c.getLong(1) > 0) return true
                false
            } ?: false
        } catch (e: Exception) {
            false
        }
    }

    override fun begin(relStem: String, ext: String, mime: String): PendingFile {
        val (dir, name) = split(relStem)
        val collection = if (mime.startsWith("video/")) MediaStore.Video.Media.getContentUri(MediaStore.VOLUME_EXTERNAL_PRIMARY)
        else MediaStore.Images.Media.getContentUri(MediaStore.VOLUME_EXTERNAL_PRIMARY)
        val values = ContentValues().apply {
            put(MediaStore.MediaColumns.DISPLAY_NAME, name + ext)
            put(MediaStore.MediaColumns.MIME_TYPE, mime)
            put(MediaStore.MediaColumns.RELATIVE_PATH, dir)
            put(MediaStore.MediaColumns.IS_PENDING, 1)
        }
        val uri: Uri = resolver.insert(collection, values) ?: throw java.io.IOException("MediaStore insert failed")
        val out = try {
            resolver.openOutputStream(uri, "w") ?: throw java.io.IOException("openOutputStream failed")
        } catch (e: Exception) {
            resolver.delete(uri, null, null)
            throw e
        }
        val stream = BufferedOutputStream(out, 256 * 1024)
        return object : PendingFile {
            override val stream: OutputStream = stream
            override fun commit(): String {
                stream.close()
                resolver.update(uri, ContentValues().apply { put(MediaStore.MediaColumns.IS_PENDING, 0) }, null, null)
                return uri.toString()
            }
            override fun abort() {
                try { stream.close() } catch (e: Exception) {}
                try { resolver.delete(uri, null, null) } catch (e: Exception) {}
            }
        }
    }

    // ---- eski düzenden taşıma: aynı dosyanın satırında klasör ve ad güncellenir (kopyalamadan)
    override fun moveLegacy(oldRelStem: String, newRelStem: String, ext: String): Boolean {
        val (oldDir, oldName) = split(oldRelStem)
        val names = if (ext.isNotEmpty()) listOf(oldName + ext) else KNOWN_EXTS.map { oldName + it }
        val placeholders = names.joinToString(",") { "?" }
        return try {
            val files = MediaStore.Files.getContentUri(MediaStore.VOLUME_EXTERNAL_PRIMARY)
            var id = -1L
            var display = ""
            resolver.query(
                files, arrayOf(MediaStore.MediaColumns._ID, MediaStore.MediaColumns.DISPLAY_NAME, MediaStore.MediaColumns.SIZE),
                "${MediaStore.MediaColumns.RELATIVE_PATH}=? AND ${MediaStore.MediaColumns.DISPLAY_NAME} IN ($placeholders) " +
                    "AND ${MediaStore.MediaColumns.IS_PENDING}=0",
                arrayOf(oldDir, *names.toTypedArray()), null,
            )?.use { c ->
                while (c.moveToNext()) if (c.getLong(2) > 0) { id = c.getLong(0); display = c.getString(1); break }
            }
            if (id < 0) return false
            val (newDir, newName) = split(newRelStem)
            val dot = display.lastIndexOf('.')
            val values = ContentValues().apply {
                put(MediaStore.MediaColumns.RELATIVE_PATH, newDir)
                put(MediaStore.MediaColumns.DISPLAY_NAME, newName + if (dot >= 0) display.substring(dot) else ext)
            }
            resolver.update(android.content.ContentUris.withAppendedId(files, id), values, null, null) > 0
        } catch (e: Exception) {
            false
        }
    }

    // ---- metin dosyaları: Documents/Instagram/<yol>/
    private fun textSplit(relPath: String): Pair<String, String> {
        val dir = relPath.substringBeforeLast('/', "")
        return (if (dir.isEmpty()) TEXT_BASE else "$TEXT_BASE$dir/") to relPath.substringAfterLast('/')
    }

    private fun textUri(relPath: String): Uri? {
        val (dir, name) = textSplit(relPath)
        return try {
            val files = MediaStore.Files.getContentUri(MediaStore.VOLUME_EXTERNAL_PRIMARY)
            resolver.query(
                files, arrayOf(MediaStore.MediaColumns._ID, MediaStore.MediaColumns.SIZE),
                "${MediaStore.MediaColumns.RELATIVE_PATH}=? AND ${MediaStore.MediaColumns.DISPLAY_NAME}=? " +
                    "AND ${MediaStore.MediaColumns.IS_PENDING}=0",
                arrayOf(dir, name), null,
            )?.use { c -> if (c.moveToFirst()) android.content.ContentUris.withAppendedId(files, c.getLong(0)) else null }
        } catch (e: Exception) {
            null
        }
    }

    override fun textExists(relPath: String): Boolean {
        val uri = textUri(relPath) ?: return false
        return try {
            resolver.openInputStream(uri)?.use { it.read() >= 0 } ?: false
        } catch (e: Exception) {
            false
        }
    }

    override fun readText(relPath: String): String? {
        val uri = textUri(relPath) ?: return null
        return try { resolver.openInputStream(uri)?.use { String(it.readBytes(), Charsets.UTF_8) } } catch (e: Exception) { null }
    }

    override fun writeText(relPath: String, text: String): Boolean {
        return try {
            val existing = textUri(relPath)
            val uri = existing ?: run {
                val (dir, name) = textSplit(relPath)
                val values = ContentValues().apply {
                    put(MediaStore.MediaColumns.DISPLAY_NAME, name)
                    put(MediaStore.MediaColumns.MIME_TYPE, if (name.endsWith(".csv")) "text/csv" else "text/plain")
                    put(MediaStore.MediaColumns.RELATIVE_PATH, dir)
                }
                resolver.insert(MediaStore.Files.getContentUri(MediaStore.VOLUME_EXTERNAL_PRIMARY), values)
            } ?: return false
            resolver.openOutputStream(uri, "wt")?.use { it.write(text.toByteArray(Charsets.UTF_8)) } ?: return false
            true
        } catch (e: Exception) {
            false
        }
    }

    companion object {
        const val BASE = "DCIM/Instagram/"
        const val TEXT_BASE = "Documents/Instagram/"
    }
}

/** Düz klasöre kaydeder (testler için). Windows sürümüyle aynı klasör yapısı. */
class FileSink(private val base: File) : MediaSink {
    private fun find(relStem: String, ext: String): File? {
        val stem = File(base, relStem)
        val candidates = if (ext.isNotEmpty()) listOf(ext) else KNOWN_EXTS
        return candidates.map { File(stem.parentFile, stem.name + it) }.firstOrNull { it.isFile && it.length() > 0 }
    }

    override fun exists(relStem: String, ext: String): Boolean = find(relStem, ext) != null

    override fun moveLegacy(oldRelStem: String, newRelStem: String, ext: String): Boolean {
        val old = find(oldRelStem, ext) ?: return false
        val stem = File(base, newRelStem)
        stem.parentFile?.mkdirs()
        val target = File(stem.parentFile, stem.name + "." + old.extension)
        return old.renameTo(target)
    }

    override fun textExists(relPath: String): Boolean = File(base, relPath).let { it.isFile && it.length() > 0 }

    override fun readText(relPath: String): String? =
        File(base, relPath).takeIf { it.isFile }?.let { try { it.readText(Charsets.UTF_8) } catch (e: Exception) { null } }

    override fun writeText(relPath: String, text: String): Boolean = try {
        val f = File(base, relPath)
        f.parentFile?.mkdirs()
        val tmp = File(f.parentFile, f.name + ".part")
        tmp.writeText(text, Charsets.UTF_8)
        if (!tmp.renameTo(f)) { tmp.copyTo(f, overwrite = true); tmp.delete() }
        true
    } catch (e: Exception) {
        false
    }

    override fun begin(relStem: String, ext: String, mime: String): PendingFile {
        val stem = File(base, relStem)
        stem.parentFile?.mkdirs()
        val part = File(stem.parentFile, stem.name + ".part")
        val final = File(stem.parentFile, stem.name + ext)
        val stream = BufferedOutputStream(FileOutputStream(part))
        return object : PendingFile {
            override val stream: OutputStream = stream
            override fun commit(): String {
                stream.close()
                if (!part.renameTo(final)) {
                    part.copyTo(final, overwrite = true); part.delete()
                }
                return final.absolutePath
            }
            override fun abort() {
                try { stream.close() } catch (e: Exception) {}
                part.delete()
            }
        }
    }
}
