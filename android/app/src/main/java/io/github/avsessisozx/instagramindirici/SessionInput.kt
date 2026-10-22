package io.github.avsessisozx.instagramindirici

/**
 * Kullanıcının yapıştırdığı metinden Instagram oturum çerezlerini çıkarır (browser.parse_session_input'ın karşılığı).
 * Kabul: yalın sessionid değeri, `sessionid=...`, tüm `Cookie:` satırı, geliştirici araçlarından kopyalanan satır.
 * Geçersizse boş harita döner. Değer asla günlüğe yazılmaz.
 */
object SessionInput {
    private val VALUE = Regex("^[A-Za-z0-9%:_.\\-]{6,}$")
    private val NAMES = listOf("sessionid", "csrftoken", "ds_user_id")

    fun parse(text: String?): Map<String, String> {
        val raw = (text ?: "").trim().trim('\'', '"').trim()
        if (raw.isEmpty()) return emptyMap()
        val found = linkedMapOf<String, String>()
        val body = raw.replace(Regex("^\\s*cookie:\\s*", RegexOption.IGNORE_CASE), "")
        if ('=' in body) {
            for (part in body.split(';', '\n')) {
                val i = part.indexOf('=')
                if (i < 0) continue
                val key = part.substring(0, i).trim().lowercase()
                val value = part.substring(i + 1).trim().trim('\'', '"')
                if (key in NAMES && value.isNotEmpty()) found[key] = value
            }
        }
        if ("sessionid" !in found) {
            Regex("^\\s*sessionid\\s+(\\S+)", RegexOption.IGNORE_CASE).find(raw)?.let { found["sessionid"] = it.groupValues[1] }
        }
        if ("sessionid" !in found && Regex("[^\\s;=]+").matches(raw)) found["sessionid"] = raw
        val sid = (found["sessionid"] ?: "").replace(":", "%3A")
        if (!VALUE.matches(sid)) return emptyMap()
        found["sessionid"] = sid
        for (key in listOf("csrftoken", "ds_user_id")) {
            val v = found[key]
            if (v != null && !VALUE.matches(v)) found.remove(key)
        }
        if ("ds_user_id" !in found) {
            Regex("^(\\d{5,})%3A").find(sid)?.let { found["ds_user_id"] = it.groupValues[1] }
        }
        return NAMES.filter { it in found }.associateWith { found.getValue(it) }
    }
}
