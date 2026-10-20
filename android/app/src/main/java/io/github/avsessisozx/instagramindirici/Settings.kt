package io.github.avsessisozx.instagramindirici

import org.json.JSONObject

/** Kullanıcı ayarları. Windows sürümündeki paths.clean_settings ile aynı kurallar. */
data class SettingsValues(
    val content: String = Media.CONTENT_ALL,
    val limit: Int = 0,
    val posts: Boolean = true,
    val stories: Boolean = true,
    val highlights: Boolean = true,
    val avatar: Boolean = true,
    val onlyNew: Boolean = true,
    val history: Boolean = true,
    val autoContinue: Boolean = true,
    val lang: String = "auto",
) {
    fun toJson(): JSONObject = JSONObject()
        .put("out_dir", "")
        .put("content", content)
        .put("limit", limit)
        .put("posts", posts)
        .put("stories", stories)
        .put("highlights", highlights)
        .put("avatar", avatar)
        .put("only_new", onlyNew)
        .put("history", history)
        .put("auto_continue", autoContinue)
        .put("lang", lang)

    /** Gelen JSON'daki geçerli alanları üzerine yazar, geçersizleri yok sayar. */
    fun merge(o: JSONObject?, languages: Collection<String>): SettingsValues {
        if (o == null) return this
        var v = this
        val c = o.opt("content")
        if (c is String && c in Media.CONTENT_CHOICES) v = v.copy(content = c)
        if (o.has("limit")) v = v.copy(limit = Media.toInt(o.opt("limit")).coerceIn(0, 100_000).toInt())
        fun bool(key: String, cur: Boolean): Boolean {
            if (!o.has(key)) return cur
            return when (val b = o.opt(key)) {
                is Boolean -> b
                is Number -> b.toInt() != 0
                is String -> b.isNotEmpty() && b != "false" && b != "0"
                else -> cur
            }
        }
        v = v.copy(
            posts = bool("posts", v.posts), stories = bool("stories", v.stories),
            highlights = bool("highlights", v.highlights), avatar = bool("avatar", v.avatar),
            onlyNew = bool("only_new", v.onlyNew), history = bool("history", v.history),
            autoContinue = bool("auto_continue", v.autoContinue),
        )
        val lang = o.opt("lang")
        if (lang is String) v = v.copy(lang = if (lang in languages) lang else "auto")
        return v
    }

    companion object {
        fun fromJson(raw: String?, languages: Collection<String>): SettingsValues = try {
            SettingsValues().merge(if (raw.isNullOrEmpty()) null else JSONObject(raw), languages)
        } catch (e: Exception) {
            SettingsValues()
        }
    }
}

/** Testlerin uygulamayı sahte Instagram sunucusuna yönlendirmesi ve beklemeleri kısaltması için. */
object TestHooks {
    @Volatile var baseUrl: String? = null
    @Volatile var sinkFactory: (() -> MediaSink)? = null
    @Volatile var historyFile: java.io.File? = null
    /** İstekler arası bekleme (ms): alt-üst sınır. */
    @Volatile var delayMs: LongRange? = null
    @Volatile var backoffSeconds: List<Long>? = null
    /** Liste sayfaları arası ek bekleme (ms): alt-üst sınır. */
    @Volatile var pageDelayMs: LongRange? = null
    /** Mola: her kaç liste sayfasında, kaç ms (0 = kapalı). */
    @Volatile var breakEvery: Int? = null
    @Volatile var idleSeconds: Double? = null
    @Volatile var resumeWaitSeconds: Long? = null
    @Volatile var resumeRounds: Int? = null
    @Volatile var verifyCalmMs: Long? = null
    /** Testlerde gerçek ses/görüntü birleştirici yerine kullanılır. */
    @Volatile var muxer: Muxer? = null
}
