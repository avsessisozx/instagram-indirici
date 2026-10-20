package io.github.avsessisozx.instagramindirici

import org.json.JSONArray
import org.json.JSONObject
import java.io.File

/**
 * İndirme geçmişi: hangi profilden hangi gönderinin indiğini ve listede nerede kalındığını tutar.
 * Windows sürümündeki history.py ile aynı kurallar. Geçmiş Instagram hesabına değil indirilen
 * profile bağlıdır; başka hesapla girilse de inenler tekrar inmez, yarım kalan liste devam eder.
 *
 * Dosya: uygulama klasöründe gecmis.json (her değişiklikte en geç 2 sn içinde yazılır).
 */
class History(private val file: File) {
    // user_id -> içerik seçimi -> anahtarlar
    private val downloaded = HashMap<String, HashMap<String, LinkedHashSet<String>>>()
    // user_id -> içerik seçimi -> {newest, cursor_kind, cursor, username}
    private val progress = HashMap<String, HashMap<String, JSONObject>>()
    // user_id -> {username, last}: Geçmiş sekmesinde kimin ne zaman indirildiğini göstermek için
    private val meta = HashMap<String, JSONObject>()
    private var dirty = false
    private var lastSave = 0L

    init { load() }

    fun fileMatches(f: File): Boolean = f.absolutePath == file.absolutePath

    @Synchronized
    private fun load() {
        val raw = try { if (file.isFile) file.readText(Charsets.UTF_8) else null } catch (e: Exception) { null } ?: return
        val root = try { JSONObject(raw) } catch (e: Exception) { return }
        root.optJSONObject("downloaded")?.let { d ->
            for (uid in d.keys()) {
                val byContent = d.optJSONObject(uid) ?: continue
                val m = HashMap<String, LinkedHashSet<String>>()
                for (c in byContent.keys()) {
                    val arr = byContent.optJSONArray(c) ?: continue
                    m[c] = LinkedHashSet<String>().apply { for (i in 0 until arr.length()) add(arr.optString(i)) }
                }
                downloaded[uid] = m
            }
        }
        root.optJSONObject("meta")?.let { m ->
            for (uid in m.keys()) m.optJSONObject(uid)?.let { meta[uid] = it }
        }
        root.optJSONObject("progress")?.let { p ->
            for (uid in p.keys()) {
                val byContent = p.optJSONObject(uid) ?: continue
                val m = HashMap<String, JSONObject>()
                for (c in byContent.keys()) byContent.optJSONObject(c)?.let { m[c] = it }
                progress[uid] = m
            }
        }
    }

    @Synchronized
    fun flush() {
        if (!dirty) return
        val d = JSONObject()
        for ((uid, m) in downloaded) {
            val byContent = JSONObject()
            for ((c, keys) in m) byContent.put(c, JSONArray(keys.toList()))
            d.put(uid, byContent)
        }
        val p = JSONObject()
        for ((uid, m) in progress) {
            val byContent = JSONObject()
            for ((c, o) in m) byContent.put(c, o)
            p.put(uid, byContent)
        }
        val m = JSONObject()
        for ((uid, o) in meta) m.put(uid, o)
        val text = JSONObject().put("downloaded", d).put("progress", p).put("meta", m).toString()
        try {
            file.parentFile?.mkdirs()
            val tmp = File(file.parentFile, file.name + ".tmp")
            tmp.writeText(text, Charsets.UTF_8)
            if (!tmp.renameTo(file)) { file.delete(); tmp.renameTo(file) }
            dirty = false
            lastSave = System.currentTimeMillis()
        } catch (e: Exception) {
            // Yazılamazsa bir sonraki değişiklikte tekrar denenir.
        }
    }

    private fun changed() {
        dirty = true
        if (System.currentTimeMillis() - lastSave > 2000) flush()
    }

    // ------------------------------------------------------------ indirilenler
    @Synchronized
    fun known(userId: String, content: String): Set<String> {
        if (userId.isEmpty()) return emptySet()
        val m = downloaded[userId] ?: return emptySet()
        val out = HashSet<String>()
        m[content]?.let { out += it }
        m[Media.CONTENT_ALL]?.let { out += it }
        return out
    }

    @Synchronized
    fun mark(userId: String, key: String, content: String, kind: String, username: String = "") {
        if (userId.isEmpty() || key.isEmpty()) return
        val added = downloaded.getOrPut(userId) { HashMap() }.getOrPut(content) { LinkedHashSet() }.add(key)
        val o = meta.getOrPut(userId) { JSONObject() }
        if (username.isNotEmpty()) o.put("username", username)
        o.put("last", System.currentTimeMillis() / 1000)
        if (added || username.isNotEmpty()) changed()
    }

    /** Geçmiş sekmesi için bir profilin özeti. */
    data class Profile(val userId: String, val username: String, val count: Int, val last: Long)

    /** İndirilen profiller, en son indirilen başta. Eski kayıtlarda (zaman yok) dosyanın değişme zamanı kullanılır. */
    @Synchronized
    fun profiles(): List<Profile> {
        val fallback = try { file.lastModified() / 1000 } catch (e: Exception) { 0L }
        return downloaded.map { (uid, byContent) ->
            val o = meta[uid]
            val name = o?.optString("username", "").orEmpty().ifEmpty {
                progress[uid]?.values?.firstNotNullOfOrNull { it.optString("username", "").ifEmpty { null } } ?: ""
            }
            Profile(uid, name, byContent.values.sumOf { it.size }, o?.optLong("last", 0)?.takeIf { it > 0 } ?: fallback)
        }.filter { it.count > 0 }.sortedByDescending { it.last }
    }

    /** Bir profilin geçmişini siler (dosyalara dokunmaz). Silinen kayıt sayısını döndürür. */
    @Synchronized
    fun forget(userId: String): Int {
        if (userId.isEmpty()) return 0
        val n = downloaded.remove(userId)?.values?.sumOf { it.size } ?: 0
        progress.remove(userId); meta.remove(userId)
        dirty = true
        flush()
        return n
    }

    @Synchronized
    fun count(): Int = downloaded.values.sumOf { m -> m.values.sumOf { it.size } }

    @Synchronized
    fun clear(): Int {
        val n = count()
        downloaded.clear()
        progress.clear()
        meta.clear()
        dirty = true
        flush()
        return n
    }

    // ------------------------------------------------------------ liste ilerlemesi
    private fun prog(userId: String, content: String): JSONObject? = progress[userId]?.get(content)

    @Synchronized
    fun completeNewest(userId: String, content: String): Long {
        if (userId.isEmpty()) return 0
        var best = 0L
        for (c in setOf(content, Media.CONTENT_ALL)) best = maxOf(best, prog(userId, c)?.optLong("newest", 0) ?: 0)
        return best
    }

    @Synchronized
    fun setComplete(userId: String, content: String, newest: Long, username: String = "") {
        if (userId.isEmpty()) return
        val o = progress.getOrPut(userId) { HashMap() }.getOrPut(content) { JSONObject() }
        o.put("newest", maxOf(o.optLong("newest", 0), newest))
        o.put("username", username)
        o.remove("cursor_kind"); o.remove("cursor")
        changed()
    }

    @Synchronized
    fun resumePoint(userId: String, content: String): Pair<String, String>? {
        if (userId.isEmpty()) return null
        for (c in listOf(content, Media.CONTENT_ALL)) {
            val o = prog(userId, c) ?: continue
            val kind = o.optString("cursor_kind", ""); val cursor = o.optString("cursor", "")
            if (kind.isNotEmpty() && cursor.isNotEmpty()) return kind to cursor
        }
        return null
    }

    @Synchronized
    fun setResumePoint(userId: String, content: String, cursorKind: String, cursor: String, username: String = "") {
        if (userId.isEmpty() || cursor.isEmpty()) return
        val o = progress.getOrPut(userId) { HashMap() }.getOrPut(content) { JSONObject().put("newest", 0) }
        o.put("cursor_kind", cursorKind).put("cursor", cursor).put("username", username)
        changed()
    }
}
