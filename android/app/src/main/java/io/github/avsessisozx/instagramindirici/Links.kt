package io.github.avsessisozx.instagramindirici

import java.io.ByteArrayOutputStream
import java.util.Base64

/**
 * Kullanıcının yapıştırdığı metinden Instagram profilini, gönderisini ya da hikayesini anlar.
 * Windows sürümündeki links.py'nin birebir karşılığı (tests/vectors/links.json ile denetlenir).
 */
object Links {
    const val PROFILE = "profile"
    const val POST = "post"
    const val STORY = "story"
    const val HIGHLIGHT = "highlight"
    const val SHARE = "share"

    private val USERNAME = Regex("^[A-Za-z0-9._]{1,30}$")
    private val URL_RX = Rx.unicode("(?:https?://)?(?:[a-z0-9-]+\\.)*(?:instagram\\.com|instagr\\.am)(?:/[^\\s<>\"']*)?", ignoreCase = true)
    private val SHORTCODE = Regex("^[A-Za-z0-9_-]{5,60}$")
    private val DOMAIN_END = Regex("\\.(com|net|org|am|tr|io|me|ly|co|app|tv)$", RegexOption.IGNORE_CASE)
    private val OTHER_SITES = listOf(
        "tiktok.com" to "link.other_site", "douyin.com" to "link.other_site", "facebook.com" to "link.other_site",
        "fb.watch" to "link.other_site", "threads.net" to "link.threads", "threads.com" to "link.threads",
        "youtube.com" to "link.other_site", "youtu.be" to "link.other_site", "x.com" to "link.other_site",
        "twitter.com" to "link.other_site",
    )
    private val RESERVED = setOf(
        "p", "reel", "reels", "tv", "stories", "explore", "accounts", "direct", "about", "legal",
        "developer", "web", "api", "s", "share", "challenge", "emails", "session", "privacy", "terms",
        "graphql", "static", "directory", "nametag", "oauth", "topics", "lite", "ar", "invites",
        "your_activity", "notifications", "create", "settings", "help", "press", "jobs", "blog",
        "popular", "locations", "tags", "audio", "threads", "meta",
    )
    private val TRAIL = ".,;:!?)»”’"

    data class Parsed(
        val ok: Boolean,
        val kind: String? = null,
        val username: String = "",
        val shortcode: String = "",
        val highlightId: String = "",
        val storyMediaId: String = "",
        val url: String = "",
        val error: Msg? = null,
    ) {
        fun describe(): Msg {
            if (!ok) return error ?: msg("link.not_found")
            return when (kind) {
                PROFILE -> msg("link.profile_detected", "name" to username)
                POST -> msg("link.post_detected")
                STORY -> msg("link.story_detected", "name" to username)
                HIGHLIGHT -> msg("link.highlight_detected")
                else -> msg("link.share_detected")
            }
        }
    }

    private fun fail(key: String) = Parsed(false, error = msg(key))

    private fun isDigits(s: String) = s.isNotEmpty() && s.all { Character.isDigit(it) }

    private fun findUrl(text: String): String {
        val m = URL_RX.find(text) ?: return ""
        var url = m.value.trimEnd { it in TRAIL }
        if (!url.lowercase().startsWith("http")) url = "https://$url"
        return url
    }

    /** Python urllib.parse.unquote: %XX dizilerini UTF-8 olarak çözer, "+" işaretine dokunmaz. */
    private fun unquote(s: String): String {
        if ('%' !in s) return s
        val out = StringBuilder()
        var i = 0
        fun escapeAt(j: Int) = j + 2 < s.length && s[j] == '%' && isHex(s[j + 1]) && isHex(s[j + 2])
        while (i < s.length) {
            if (escapeAt(i)) {
                val bytes = ByteArrayOutputStream()
                while (escapeAt(i)) {
                    bytes.write(s.substring(i + 1, i + 3).toInt(16))
                    i += 3
                }
                out.append(String(bytes.toByteArray(), Charsets.UTF_8))
            } else {
                out.append(s[i]); i++
            }
        }
        return out.toString()
    }

    private fun isHex(c: Char) = c in '0'..'9' || c in 'a'..'f' || c in 'A'..'F'

    /** urlsplit'in ihtiyacımız olan kısmı: (küçük harfli host, path). */
    private fun split(url: String): Pair<String, String> {
        var rest = url.substringAfter("://", url)
        rest = rest.substringBefore('#')
        val hostEnd = rest.indexOfFirst { it == '/' || it == '?' }
        val hostPort = if (hostEnd < 0) rest else rest.substring(0, hostEnd)
        val host = hostPort.substringAfterLast('@').substringBefore(':').lowercase()
        val path = if (hostEnd < 0 || rest[hostEnd] == '?') "" else rest.substring(hostEnd).substringBefore('?')
        return host to path
    }

    private fun highlightFromBase64(token: String): String {
        val t = token.trim()
        val padded = t + "=".repeat((4 - t.length % 4) % 4)
        for (decoder in listOf(Base64.getMimeDecoder(), Base64.getUrlDecoder())) {
            val text = try { String(decoder.decode(padded), Charsets.UTF_8) } catch (e: Exception) { continue }
            val m = Regex("^highlight:(\\d+)$").find(text.trim())
            if (m != null) return m.groupValues[1]
        }
        return ""
    }

    fun parse(raw: String?): Parsed {
        val text = (raw ?: "").trim()
        if (text.isEmpty()) return fail("link.empty")

        if (text.startsWith("@") && USERNAME.matches(text.substring(1))) return profile(text.substring(1))
        if (USERNAME.matches(text) && !DOMAIN_END.containsMatchIn(text)) return profile(text)
        val bare = text.trimStart('@')

        val url = findUrl(text)
        if (url.isEmpty()) {
            val lowered = text.lowercase()
            for ((site, key) in OTHER_SITES) if (site in lowered) return fail(key)
            if (' ' !in text && bare.length > 30 && USERNAME.matches(bare.substring(0, 30))) return fail("link.username_too_long")
            return fail("link.not_found")
        }

        val (host, path) = split(url)
        val segments = path.split("/").filter { it.isNotEmpty() }.map { unquote(it) }
        if (segments.isEmpty()) return fail("link.no_target")
        val first = segments[0].lowercase()

        if (first == "share") return Parsed(true, SHARE, url = url)

        for (i in 0 until minOf(2, segments.size)) {
            val seg = segments[i].lowercase()
            if (seg in setOf("p", "reel", "reels", "tv") && segments.size > i + 1) {
                val code = segments[i + 1]
                if (i == 1 && segments[0].lowercase() in RESERVED) break
                if (SHORTCODE.matches(code)) return Parsed(true, POST, shortcode = code, url = url)
                return fail("link.post_broken")
            }
            if (i == 0 && seg in setOf("p", "tv", "reel") && segments.size == 1) return fail("link.post_broken")
        }

        if (first == "reels" && segments.size == 1) return fail("link.no_target")

        if (first == "stories") {
            if (segments.size >= 3 && segments[1].lowercase() == "highlights" && isDigits(segments[2])) {
                return Parsed(true, HIGHLIGHT, highlightId = segments[2], url = url)
            }
            if (segments.size >= 2 && segments[1].lowercase() !in setOf("highlights", "me") && USERNAME.matches(segments[1])) {
                val mediaId = if (segments.size >= 3 && isDigits(segments[2])) segments[2] else ""
                return Parsed(true, STORY, username = segments[1].lowercase(), storyMediaId = mediaId, url = url)
            }
            return fail("link.no_target")
        }

        if (first == "s" && segments.size >= 2) {
            val hid = highlightFromBase64(segments[1])
            if (hid.isNotEmpty()) return Parsed(true, HIGHLIGHT, highlightId = hid, url = url)
            return fail("link.no_target")
        }

        if (first == "explore") return fail("link.explore")
        if (first == "direct") return fail("link.direct")
        if (first in RESERVED) return fail("link.no_target")

        if (USERNAME.matches(segments[0])) return profile(segments[0], url)
        if (host.isNotEmpty()) return fail("link.no_target")
        return fail("link.not_found")
    }

    private fun profile(name: String, url: String = ""): Parsed {
        val n = name.trim().trimStart('@').lowercase()
        if (!USERNAME.matches(n)) return fail("link.not_found")
        return Parsed(true, PROFILE, username = n, url = url.ifEmpty { "https://www.instagram.com/$n/" })
    }

    // ------------------------------------------------------------ gönderi kodu <-> kimlik
    private const val ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"

    fun idFromShortcode(codeIn: String): String {
        var code = codeIn
        if (code.length > 28) code = code.substring(0, code.length - 28)
        var value = java.math.BigInteger.ZERO
        val b64 = java.math.BigInteger.valueOf(64)
        for (ch in code) {
            val idx = ALPHABET.indexOf(ch)
            if (idx < 0) return ""
            value = value.multiply(b64).add(java.math.BigInteger.valueOf(idx.toLong()))
        }
        return value.toString()
    }

    fun shortcodeFromId(mediaId: Any?): String {
        val s = Media.pyStr(mediaId).substringBefore('_')
        val value = s.toBigIntegerOrNull() ?: return ""
        if (value.signum() <= 0) return ""
        var v = value
        val b64 = java.math.BigInteger.valueOf(64)
        val out = StringBuilder()
        while (v.signum() > 0) {
            val qr = v.divideAndRemainder(b64)
            out.insert(0, ALPHABET[qr[1].toInt()])
            v = qr[0]
        }
        return out.toString()
    }
}
