package io.github.avsessisozx.instagramindirici

import org.json.JSONObject

/**
 * Çevrilecek bir mesaj: anahtar + değişkenler (değişkenler de Msg olabilir).
 * Metne çevirme işi arayüzde (index.html) ya da Tr.text ile yapılır;
 * kodun içinde hiçbir kullanıcı metni yoktur. Windows sürümündeki i18n.Msg ile aynı biçim.
 */
data class Msg(val key: String, val params: Map<String, Any?> = emptyMap()) {
    fun toJson(): JSONObject {
        val p = JSONObject()
        for ((k, v) in params) {
            p.put(k, when (v) {
                is Msg -> v.toJson()
                null -> JSONObject.NULL
                is Number, is Boolean -> v
                else -> v.toString()
            })
        }
        return JSONObject().put("key", key).put("params", p)
    }
}

fun msg(key: String, vararg params: Pair<String, Any?>): Msg = Msg(key, mapOf(*params))

/** Dil dosyalarını (ceviriler klasöründeki .json dosyaları) okur. Eksik anahtar İngilizceye, o da yoksa anahtarın kendisine düşer. */
class Tr(private val loader: (String) -> String?, private val languages: List<String>) {

    private val cache = HashMap<String, Map<String, String>>()

    @Synchronized
    fun load(lang: String): Map<String, String> = cache.getOrPut(lang) {
        val raw = loader(lang) ?: return@getOrPut emptyMap()
        val obj = try { JSONObject(raw) } catch (e: Exception) { return@getOrPut emptyMap() }
        val out = HashMap<String, String>()
        for (k in obj.keys()) {
            val v = obj.opt(k)
            if (v is String) out[k] = v
        }
        out
    }

    fun available(): List<Pair<String, String>> =
        languages.map { code -> code to (load(code)["_language_name"] ?: code) }

    fun resolve(setting: String, systemLanguage: String): String {
        val codes = languages.toSet()
        if (setting in codes) return setting
        val sys = systemLanguage.lowercase().substringBefore('-').substringBefore('_')
        return if (sys in codes) sys else FALLBACK
    }

    fun merged(lang: String): Map<String, String> {
        val m = LinkedHashMap(load(FALLBACK))
        m.putAll(load(lang))
        return m
    }

    fun text(key: String, lang: String, params: Map<String, Any?> = emptyMap()): String {
        if (key == "summary") return summary(lang, params)
        val template = load(lang)[key] ?: load(FALLBACK)[key] ?: key
        return PLACEHOLDER.replace(template) { m ->
            val name = m.groupValues[1]
            if (!params.containsKey(name)) m.value
            else when (val v = params[name]) {
                is Msg -> text(v.key, lang, v.params)
                null -> ""
                else -> v.toString()
            }
        }
    }

    fun render(m: Msg, lang: String): String = text(m.key, lang, m.params)

    private fun summary(lang: String, p: Map<String, Any?>): String {
        fun n(name: String) = (p[name] as? Number)?.toInt() ?: 0
        val parts = mutableListOf(text("summary.done", lang, mapOf("n" to n("done"))))
        for ((key, name) in listOf("summary.skipped" to "skipped", "summary.failed" to "failed", "summary.unmatched" to "unmatched",
                "summary.unavailable" to "unavailable", "summary.known" to "known")) {
            if (n(name) != 0) parts += text(key, lang, mapOf("n" to n(name)))
        }
        return text("summary.wrap", lang, mapOf("parts" to parts.joinToString(", ")))
    }

    companion object {
        const val FALLBACK = "en"
        private val PLACEHOLDER = Regex("\\{(\\w+)\\}")
    }
}
