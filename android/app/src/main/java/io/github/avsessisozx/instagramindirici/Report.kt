package io.github.avsessisozx.instagramindirici

import org.json.JSONArray
import org.json.JSONObject

/**
 * "Ayrıntılı rapor": sorunu uzaktan incelemek için sürüm, cihaz, ayarlar, durum ve tam günlük tek metinde.
 * Çerez, oturum anahtarı, şifre ve erişim bilgisi içermez (varsa silinir).
 */
object Report {
    const val MAX_CHARS = 150_000

    private val SECRET = Regex(
        "(?i)(sessionid|csrftoken|ds_user_id|ig_did|datr|mid|rur|ig_nrcb|authorization|password|passwd|token)" +
            "([\"']?\\s*[:=]\\s*[\"']?)[^;\\s&,\"'}]+",
    )

    fun redact(text: String): String = SECRET.replace(text) { "${it.groupValues[1]}${it.groupValues[2]}[silindi]" }

    fun msgFromJson(o: JSONObject): Msg {
        val params = LinkedHashMap<String, Any?>()
        val p = o.optJSONObject("params")
        if (p != null) for (k in p.keys()) {
            val v = p.opt(k)
            params[k] = if (v is JSONObject && v.has("key")) msgFromJson(v) else if (v == JSONObject.NULL) null else v
        }
        return Msg(o.optString("key"), params)
    }

    private fun logLine(entry: JSONObject, tr: Tr, lang: String): String {
        val m = entry.optJSONObject("msg")
        val text = if (m != null) tr.render(msgFromJson(m), lang) else entry.optString("text")
        return "${entry.optString("t")} [${entry.optString("level")}] ${text.replace("\n", " ")}"
    }

    /** info: cihaz/ağ gibi başlık satırları (Android tarafı toplar), snapshot: Engine.snapshot(0). */
    fun build(info: List<Pair<String, String>>, settings: JSONObject, snapshot: JSONObject, tr: Tr, lang: String): String {
        val sb = StringBuilder()
        sb.append("Instagram İndirici raporu\n")
        for ((k, v) in info) sb.append(k).append(": ").append(v).append('\n')
        sb.append("\n--- Ayarlar ---\n")
        val s = JSONObject(settings.toString())
        s.remove("out_dir")
        sb.append(s.toString()).append('\n')

        sb.append("\n--- Durum ---\n")
        for (k in listOf("running", "phase", "mode", "logged_in", "login_wall", "rate_limited", "author", "found_posts",
            "posts_done", "posts_skipped", "posts_unavailable", "files_total", "files_done", "files_skipped", "files_failed")) {
            sb.append(k).append(": ").append(snapshot.opt(k)).append('\n')
        }
        val summary = snapshot.optJSONObject("summary")
        if (summary != null) sb.append("summary: ").append(tr.render(msgFromJson(summary), lang)).append('\n')

        val logs: JSONArray = snapshot.optJSONArray("logs") ?: JSONArray()
        val lines = (0 until logs.length()).mapNotNull { logs.optJSONObject(it) }.map { logLine(it, tr, lang) }
        sb.append("\n--- Günlük (").append(lines.size).append(" satır) ---\n")
        val head = sb.length
        val body = StringBuilder()
        var truncated = false
        for (line in lines.asReversed()) {          // sığmazsa en eski satırlar atılır
            if (head + body.length + line.length + 1 > MAX_CHARS) { truncated = true; break }
            body.insert(0, line + "\n")
        }
        if (truncated) sb.append("(başı kısaltıldı)\n")
        sb.append(body)
        return redact(sb.toString())
    }
}
