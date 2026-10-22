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
    val reels: Boolean = true,                 // Reels sekmesi (ayrı liste; Reels/ klasörüne iner)
    val reposts: Boolean = false,              // Yeniden paylaşımlar sekmesi
    val tagged: Boolean = false,               // Etiketlenenler sekmesi
    val verifyFiles: Boolean = true,           // geçmişte "indi" görünen ama dosyası olmayanları yeniden indir
    val captionMin: Int = 300,                 // bu kadar karakterden uzun açıklamalar ayrı .txt olur (0 = kapalı)
    val infoCsv: Boolean = true,               // her profile bilgi.csv yaz
    val dateFrom: String = "",                 // YYYY-MM-DD: bundan eski gönderiler indirilmez
    val dateTo: String = "",                   // YYYY-MM-DD: bundan yeni gönderiler indirilmez
    val onlyNew: Boolean = true,
    val history: Boolean = true,
    val autoContinue: Boolean = true,
    val lang: String = "auto",
    val identity: String = Identity.DESKTOP,   // Instagram'a hangi cihazmış gibi görünülür
    val speed: String = Pacing.NORMAL,         // istek hızı: normal / dikkatli / çok dikkatli
    val setupDone: Boolean = false,            // ilk açılış sihirbazı bitti mi
    val noConfirm: Boolean = false,            // silmeden önce onay sorma
) {
    fun toJson(): JSONObject = JSONObject()
        .put("out_dir", "")
        .put("content", content)
        .put("limit", limit)
        .put("posts", posts)
        .put("stories", stories)
        .put("highlights", highlights)
        .put("avatar", avatar)
        .put("reels", reels)
        .put("reposts", reposts)
        .put("tagged", tagged)
        .put("verify_files", verifyFiles)
        .put("caption_min", captionMin)
        .put("info_csv", infoCsv)
        .put("date_from", dateFrom)
        .put("date_to", dateTo)
        .put("only_new", onlyNew)
        .put("history", history)
        .put("auto_continue", autoContinue)
        .put("lang", lang)
        .put("identity", identity)
        .put("speed", speed)
        .put("setup_done", setupDone)
        .put("no_confirm", noConfirm)

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
            reels = bool("reels", v.reels), reposts = bool("reposts", v.reposts), tagged = bool("tagged", v.tagged),
            verifyFiles = bool("verify_files", v.verifyFiles), infoCsv = bool("info_csv", v.infoCsv),
            onlyNew = bool("only_new", v.onlyNew), history = bool("history", v.history),
            autoContinue = bool("auto_continue", v.autoContinue),
            setupDone = bool("setup_done", v.setupDone), noConfirm = bool("no_confirm", v.noConfirm),
        )
        if (o.has("caption_min")) {
            v = v.copy(captionMin = Media.toInt(o.opt("caption_min")).coerceIn(0, CAPTION_MIN_MAX.toLong()).toInt())
        }
        if (o.has("date_from")) v = v.copy(dateFrom = cleanDate(o.opt("date_from")))
        if (o.has("date_to")) v = v.copy(dateTo = cleanDate(o.opt("date_to")))
        val identity = o.opt("identity")
        if (identity is String && identity in Identity.ALL) v = v.copy(identity = identity)
        val speed = o.opt("speed")
        if (speed is String && speed in Pacing.ALL) v = v.copy(speed = speed)
        val lang = o.opt("lang")
        if (lang is String) v = v.copy(lang = if (lang in languages) lang else "auto")
        return v
    }

    companion object {
        const val CAPTION_MIN_MAX = 20000

        /** YYYY-MM-DD biçimindeki geçerli tarihi olduğu gibi, geçersizi boş metin olarak döndürür. */
        fun cleanDate(value: Any?): String {
            val text = (value as? String)?.trim() ?: return ""
            if (text.length != 10) return ""
            return try { java.time.LocalDate.parse(text).toString() } catch (e: Exception) { "" }
        }

        /** Günün başlangıcı (UTC) saniye olarak; boşsa 0. */
        fun dateStartTs(value: String): Long {
            val text = cleanDate(value)
            if (text.isEmpty()) return 0
            return java.time.LocalDate.parse(text).atStartOfDay(java.time.ZoneOffset.UTC).toEpochSecond()
        }

        /** Günün sonu (UTC) saniye olarak; boşsa 0 (sınır yok). */
        fun dateEndTs(value: String): Long {
            val start = dateStartTs(value)
            return if (start != 0L) start + 86400 - 1 else 0L
        }

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
    /** Hızlı (tek denemelik) isteklerin zaman aşımı (ms). */
    @Volatile var quickTimeoutMs: Long? = null
    /** Liste sayfaları arası ek bekleme (ms): alt-üst sınır. */
    @Volatile var pageDelayMs: LongRange? = null
    /** Mola: her kaç liste sayfasında, kaç ms (0 = kapalı). */
    @Volatile var breakEvery: Int? = null
    /** Molanın süresi (saniye): alt-üst sınır. */
    @Volatile var breakSeconds: LongRange? = null
    @Volatile var idleSeconds: Double? = null
    @Volatile var resumeWaitSeconds: Long? = null
    @Volatile var resumeRounds: Int? = null
    @Volatile var verifyCalmMs: Long? = null
    /** HTTP 5xx sonrası ilk bekleme (saniye); ikinci denemede iki katı. */
    @Volatile var serverErrorWaitSeconds: Double? = null
    /** Eksik dosyaları yeniden denemeden önceki bekleme (ms). */
    @Volatile var retryWaitMs: Long? = null
    /** Hikaye / öne çıkan / Reels listelerinde hız sınırında kaç kez bekleyip yeniden denenir. */
    @Volatile var otherRounds: Int? = null
    /** Testlerde gerçek ses/görüntü birleştirici yerine kullanılır. */
    @Volatile var muxer: Muxer? = null
}
