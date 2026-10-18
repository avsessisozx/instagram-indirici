package io.github.avsessisozx.instagramindirici

import java.text.Normalizer
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/** Her yerde sorunsuz dosya/klasör adları. Windows sürümündeki naming.py'nin karşılığı. */
object Naming {
    private val INVALID = Regex("[<>:\"/\\\\|?*\\x00-\\x1f\\x7f]")
    private val LINE_BREAKS = Regex("[\\r\\n\\t\\u000B\\f]+")
    private val SPACES = Rx.unicode("\\s+")
    private val HASHTAG = Rx.unicode("#\\S+")
    private val RESERVED: Set<String> = buildSet {
        addAll(listOf("CON", "PRN", "AUX", "NUL"))
        for (i in 1..9) { add("COM$i"); add("LPT$i") }
    }

    private fun stripSpaceDot(s: String): String = s.trim { it == ' ' || it == '.' }

    private fun truncateCodePoints(s: String, max: Int): String {
        val count = s.codePointCount(0, s.length)
        if (count <= max) return s
        return s.substring(0, s.offsetByCodePoints(0, max))
    }

    fun sanitize(name: Any?, maxLen: Int = 60, fallback: String = "adsiz"): String {
        val raw = when (name) {
            null -> ""
            is String -> name
            is Number -> if (name.toDouble() == 0.0) "" else name.toString()
            is Boolean -> if (name) "True" else ""
            else -> name.toString()
        }
        var text = Normalizer.normalize(raw, Normalizer.Form.NFC)
        text = LINE_BREAKS.replace(text, " ")
        val sb = StringBuilder()
        var i = 0
        while (i < text.length) {
            val cp = text.codePointAt(i)
            when (Character.getType(cp)) {
                Character.FORMAT.toInt(), Character.CONTROL.toInt(),
                Character.SURROGATE.toInt(), Character.PRIVATE_USE.toInt() -> {}
                else -> sb.appendCodePoint(cp)
            }
            i += Character.charCount(cp)
        }
        text = INVALID.replace(sb.toString(), " ")
        text = stripSpaceDot(SPACES.replace(text, " "))
        if (text.codePointCount(0, text.length) > maxLen) {
            text = truncateCodePoints(text, maxLen).trimEnd { it == ' ' || it == '.' }
        }
        if (text.isEmpty()) text = fallback
        if (text.split(".")[0].uppercase(Locale.ROOT) in RESERVED) text = "${text}_"
        return text
    }

    fun titleFromDesc(desc: Any?, maxLen: Int = 40): String {
        val raw = desc?.toString() ?: ""
        val withoutTags = HASHTAG.replace(raw, " ")
        var title = sanitize(withoutTags, maxLen, fallback = "")
        if (title.isEmpty()) {
            val tags = HASHTAG.findAll(raw).map { it.value.trimStart('#') }.toList()
            title = sanitize(tags.joinToString(" "), maxLen, fallback = "")
        }
        return title.ifEmpty { "gonderi" }
    }

    fun dateFromTimestamp(ts: Any?): String {
        val value = when (ts) {
            is Number -> ts.toLong()
            is String -> ts.trim().toLongOrNull() ?: return "tarihsiz"
            else -> return "tarihsiz"
        }
        if (value <= 0) return "tarihsiz"
        return try {
            SimpleDateFormat("yyyy-MM-dd", Locale.ROOT).format(Date(value * 1000))
        } catch (e: Exception) {
            "tarihsiz"
        }
    }

    fun postStem(code: String, desc: Any?, createTime: Any?): String =
        "${dateFromTimestamp(createTime)}_${titleFromDesc(desc)}_$code"
}
