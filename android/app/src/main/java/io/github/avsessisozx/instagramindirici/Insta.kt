package io.github.avsessisozx.instagramindirici

import kotlinx.coroutines.delay
import org.json.JSONArray
import org.json.JSONObject
import org.json.JSONTokener
import java.net.URLEncoder
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.random.Random

// ------------------------------------------------------------ hatalar
/** Kullanıcıya gösterilecek, çevrilebilir açıklamalı hata. */
open class IgError(val m: Msg) : Exception(m.key)
class LoginRequired(m: Msg = msg("browser.login_required")) : IgError(m)
class RateLimited(val what: String = "", val status: Int = 0) : IgError(msg("browser.rate_limited"))
class NotFound(m: Msg = msg("browser.not_found")) : IgError(m)
class ApiError(val detail: String) : IgError(msg("browser.api_error", "detail" to detail))

interface WebListener {
    fun log(level: String, m: Msg)
    fun logText(level: String, text: String)
    fun verify(active: Boolean)
    fun waitUntil(epochMs: Long, kind: String)
}

data class RawResponse(val status: Int, val url: String, val text: String, val error: String = "")

/**
 * Instagram sayfasını açan ve sayfanın içinden istek yapan taraf.
 * Android'de gizli WebView (WebInstagram), birim testlerinde düz HTTP istemcisi.
 * Windows sürümündeki Playwright tarayıcısının karşılığı.
 */
interface IgWeb {
    var listener: WebListener?
    var cancel: AtomicBoolean
    val userAgent: String
    val baseUrl: String
    suspend fun prepare()
    suspend fun loggedIn(): Boolean
    /** Sayfayı açar; HTTP durum kodunu döner (bilinmiyorsa 0). İnternet yoksa IgError fırlatır. */
    suspend fun navigate(url: String): Int
    fun currentUrl(): String
    /** Sayfanın içinden (aynı site) istek: çerezler ve site başlıkları otomatik. */
    suspend fun fetch(url: String, form: Map<String, String>?, timeoutMs: Long): RawResponse
    /** Sayfaya gömülü JSON verileri (çözülmüş). */
    suspend fun embeddedJson(): List<Any>
    suspend fun pageHtml(): String
    /** Sayfanın kendi yaptığı JSON isteklerinin yanıtlarını yakalar (null: kapat). */
    fun setCapture(handler: ((String, Any) -> Unit)?)
    suspend fun scrollDown()
    /** Doğrulama ekranını kullanıcıya göster / gizle. */
    suspend fun showVerify(show: Boolean)
    /** Giriş ekranını açar; giriş tamamlanınca ya da kullanıcı bitirince döner. Giriş yapıldıysa true. */
    suspend fun loginSession(): Boolean
}

data class ProfileInfo(val username: String) {
    var userId = ""
    var fullName = ""
    var isPrivate = false
    var followed = false
    var mediaCount = -1
    var user = JSONObject()
    val firstItems = mutableListOf<JSONObject>()
    var endCursor = ""
    var hasNext: Boolean? = null
    val sources = mutableListOf<String>()
    val locked: Boolean get() = isPrivate && !followed
}

data class FeedResult(var collected: Int = 0, var reason: String = "end", var via: String = "")

/** Gönderi listesinin ortak durumu (hangi yol kullanılırsa kullanılsın). */
class FeedState(
    val limit: Int,
    val onPage: (List<JSONObject>, String, String) -> String,
    val resume: Pair<String, String>? = null,
) {
    val seen = LinkedHashSet<String>()
    var limitHit = false
    var jumped = false

    /** "" (devam), "stop" (geri kalan zaten inmiş) ya da "jump" (yarım kalan yere atla). */
    fun accept(items: List<JSONObject>, cursorKind: String = "", nextCursor: String = ""): String {
        val fresh = mutableListOf<JSONObject>()
        for (item in items) {
            val key = Media.mediaKey(item)
            if (key.isEmpty() || key in seen) continue
            if (limit > 0 && seen.size >= limit) { limitHit = true; break }
            seen += key
            fresh += item
        }
        if (limit > 0 && seen.size >= limit) limitHit = true
        if (fresh.isEmpty()) return ""
        return onPage(fresh, cursorKind, if (limitHit) "" else nextCursor)
    }

    fun jumpTarget(kind: String): String {
        if (jumped || resume == null || resume.first != kind) return ""
        jumped = true
        return resume.second
    }
}

/** Windows sürümündeki browser.py'nin karşılığı: Instagram'dan veriyi toplayan kurallar. */
class Insta(private val web: IgWeb, private val pacing: Pacing = Pacing.forSpeed(Pacing.NORMAL)) {
    private val log: (String, Msg) -> Unit = { level, m -> web.listener?.log(level, m) }
    private fun debug(text: String) { web.listener?.logText("debug", text) }
    private var lastCall = 0L
    var apiCalls = 0; private set
    private var listPages = 0
    private val base: String get() = web.baseUrl.trimEnd('/')

    /** Duraklatıldıysa devam edilene kadar bekler (motor atar). */
    var pauseGate: suspend () -> Unit = {}

    // ------------------------------------------------------------ zaman
    private fun checkCancel() { if (web.cancel.get()) throw CancelledException() }

    private suspend fun sleep(ms: Long) {
        val end = System.currentTimeMillis() + ms
        while (true) {
            checkCancel()
            pauseGate()
            val left = end - System.currentTimeMillis()
            if (left <= 0) return
            delay(minOf(500L, left))
        }
    }

    private fun backoff(): List<Long> = TestHooks.backoffSeconds ?: listOf(30L, 90L)

    fun rebase(url: String): String {
        if (base == "https://www.instagram.com") return url
        return url.replace(Regex("^https?://(www\\.)?instagram\\.com"), base)
    }

    // ------------------------------------------------------------ sayfa gezinme
    /** Sayfayı açar; Instagram yoğunlukta 429/5xx döndürürse bekleyip tekrar dener. */
    suspend fun goto(url: String): Int {
        val target = rebase(url)
        val steps = backoff()
        var attempt = 0
        while (true) {
            checkCancel()
            val status = web.navigate(target)
            if (status !in setOf(429, 500, 502, 503, 504)) return status
            if (attempt >= steps.size) throw RateLimited()
            val seconds = steps[attempt++]
            log("warn", msg("browser.page_refused", "code" to status, "s" to seconds))
            sleep(seconds * 1000)
        }
    }

    private fun pagePath(): String = pathOf(web.currentUrl())

    private fun pageRedirectState(): String {
        val path = pagePath()
        if (CHALLENGE_PATHS.any { it in path }) return "challenge"
        if (LOGIN_PATHS.any { it in path }) return "login"
        return ""
    }

    private suspend fun ensureOnSite() {
        if (!web.currentUrl().startsWith(base)) goto("$base/")
    }

    suspend fun waitForVerification(targetUrl: String = "", timeoutMs: Long = 900_000) {
        web.listener?.verify(true)
        log("warn", msg("browser.verify_mobile"))
        web.showVerify(true)
        try {
            if (targetUrl.isNotEmpty()) goto(if (targetUrl.startsWith("/")) base + targetUrl else targetUrl)
            val start = System.currentTimeMillis()
            var calmSince = 0L
            val calmMs = TestHooks.verifyCalmMs ?: 3000L
            while (System.currentTimeMillis() - start < timeoutMs) {
                sleep(1500)
                if (pageRedirectState().isNotEmpty()) { calmSince = 0; continue }
                if (calmSince == 0L) calmSince = System.currentTimeMillis()
                else if (System.currentTimeMillis() - calmSince > calmMs) {
                    log("ok", msg("browser.verify_done"))
                    return
                }
            }
            throw IgError(msg("browser.verify_timeout"))
        } finally {
            web.listener?.verify(false)
            web.showVerify(false)
        }
    }

    // ------------------------------------------------------------ API
    private suspend fun rawApi(path: String, params: List<Pair<String, String>>?, form: Map<String, String>?): Triple<RawResponse, Any?, String> {
        ensureOnSite()
        var url = if (path.startsWith("http")) path else base + path
        if (!params.isNullOrEmpty()) {
            url += (if ('?' in url) "&" else "?") + params.joinToString("&") { (k, v) ->
                URLEncoder.encode(k, "UTF-8") + "=" + URLEncoder.encode(v, "UTF-8")
            }
        }
        val range = TestHooks.delayMs ?: pacing.callDelayMs
        val wait = lastCall + Random.nextLong(range.first, range.last + 1) - System.currentTimeMillis()
        if (lastCall > 0 && wait > 0) sleep(wait)
        lastCall = System.currentTimeMillis()
        apiCalls++
        val raw = web.fetch(url, form, 45_000)
        return Triple(raw, parseJsonText(raw.text), url)
    }

    /**
     * API çağrısı: doğrulama isterse kullanıcıyı bekler. Sadece vazgeçilmez çağrılarda (gönderi listesi)
     * hız sınırında bekleyip tekrar dener; diğerlerinde hemen vazgeçip başka yoldan devam edilir.
     */
    suspend fun call(path: String, params: List<Pair<String, String>>? = null, form: Map<String, String>? = null,
                     what: String = "", essential: Boolean = false): Any? {
        val steps = if (essential) backoff() else emptyList()
        if (essential) pace(countsAsPage = what == "feed" || what == "graphql")
        var attempt = 0
        var connectionErrors = 0
        var serverErrors = 0
        var verified = false
        while (true) {
            checkCancel()
            pauseGate()
            val (res, data, _) = rawApi(path, params, form)
            val kind = classify(res.status, res.url, data)
            if (kind == "ok") return data
            if (res.status == 0 && kind == "error") {
                connectionErrors++
                if (connectionErrors <= 2) {
                    log("warn", msg("browser.retrying", "detail" to res.error.ifEmpty { "?" }))
                    sleep(5000L * connectionErrors)
                    continue
                }
                throw ApiError("${what.ifEmpty { path }}: ${res.error.ifEmpty { "no response" }}")
            }
            if (kind == "error" && res.status in listOf(500, 502, 503, 504)) {
                // "Üzgünüz, bir sorun oluştu. Lütfen tekrar dene." — Instagram'ın geçici sunucu hatası.
                serverErrors++
                if (serverErrors <= 2) {
                    val wait = ((TestHooks.serverErrorWaitSeconds ?: 10.0) * 1000 * serverErrors).toLong()
                    debug("${what.ifEmpty { path }}: HTTP ${res.status}, ${wait / 1000.0} sn sonra yeniden denenecek")
                    sleep(wait)
                    continue
                }
            }
            if (kind == "rate") {
                val label = msg("what.${what.ifEmpty { "other" }}")
                debug("${what.ifEmpty { path }} HTTP ${res.status}: ${res.text.take(200).replace("\n", " ")}")
                if (attempt >= steps.size) {
                    log("info", msg("browser.refused", "what" to label, "code" to (if (res.status != 0) res.status else "?")))
                    throw RateLimited(what, res.status)
                }
                val seconds = steps[attempt++]
                log("warn", msg("browser.slow_down", "what" to label, "code" to (if (res.status != 0) res.status else "?"), "s" to seconds))
                web.listener?.waitUntil(System.currentTimeMillis() + seconds * 1000, "rate")
                try { sleep(seconds * 1000) } finally { web.listener?.waitUntil(0, "") }
                continue
            }
            if (kind == "challenge") {
                if (verified) throw IgError(msg("browser.verify_failed"))
                val d = data as? JSONObject
                var target = Media.pyStr(Media.or(Media.opt(d, "checkpoint_url"),
                    (Media.opt(d, "challenge") as? JSONObject)?.let { Media.opt(it, "url") }, ""))
                if (target.isEmpty() && CHALLENGE_PATHS.any { it in pathOf(res.url) }) target = res.url
                waitForVerification(target.ifEmpty { "$base/" })
                verified = true
                continue
            }
            if (kind == "login") throw LoginRequired()
            if (kind == "notfound") throw NotFound()
            throw ApiError("${what.ifEmpty { path }}: HTTP ${res.status} ${res.text.take(160).replace("\n", " ")}")
        }
    }

    /** Liste sayfaları arasında insan gibi bekler (telefonda Instagram daha çabuk "yavaşla" diyor). */
    private suspend fun pageDelay() {
        val range = TestHooks.pageDelayMs ?: pacing.pageDelayMs
        sleep(Random.nextLong(range.first, range.last + 1))
    }

    /** Belli sayıda liste sayfasından sonra kısa mola verir (Instagram'ı yormamak için). */
    private suspend fun pace(countsAsPage: Boolean) {
        // Öne çıkanlar gibi gönderi olmayan listeler mola sayacını doldurmasın.
        if (!countsAsPage) return
        val every = TestHooks.breakEvery ?: pacing.breakEvery
        listPages++
        if (every > 0 && listPages > 1 && (listPages - 1) % every == 0) {
            val range = TestHooks.breakSeconds ?: pacing.breakSeconds
            val seconds = Random.nextLong(range.first, range.last + 1)
            log("info", msg("browser.pause", "s" to seconds))
            web.listener?.waitUntil(System.currentTimeMillis() + seconds * 1000, "pause")
            try { sleep(seconds * 1000) } finally { web.listener?.waitUntil(0, "") }
        }
    }

    // ------------------------------------------------------------ profil
    suspend fun openProfile(username: String, loggedIn: Boolean): ProfileInfo {
        val info = ProfileInfo(username)
        log("info", msg("browser.profile_opening", "name" to username))
        var status = goto("https://www.instagram.com/$username/")
        sleep(1500)
        var state = pageRedirectState()
        if (state == "challenge") {
            waitForVerification()
            status = goto("https://www.instagram.com/$username/")
            sleep(1500)
            state = pageRedirectState()
        }
        if (state == "login" && !loggedIn) throw LoginRequired(msg("browser.profile_login_required"))
        if (state == "login") throw LoginRequired(msg("browser.session_expired"))

        for (data in web.embeddedJson()) {
            val user = Media.findUser(data, username)
            if (user != null) {
                mergeUser(info, user)
                if ("page" !in info.sources) info.sources += "page"
            }
            addFirstItems(info, data)
        }
        var html = ""
        if (info.userId.isEmpty()) {
            html = web.pageHtml()
            val m = Regex("\"profile_id\"\\s*:\\s*\"(\\d+)\"").find(html) ?: Regex("\"user_id\"\\s*:\\s*\"(\\d+)\"").find(html)
            if (m != null) { info.userId = m.groupValues[1]; info.sources += "html" }
        }

        var rateHit = false
        try {
            val data = call("/api/v1/users/web_profile_info/", listOf("username" to username), what = "profile")
            val user = ((data as? JSONObject)?.optJSONObject("data"))?.opt("user")
            if (user is JSONObject) {
                mergeUser(info, user)
                addFirstItems(info, user)
                info.sources += "web_profile_info"
            } else if (data is JSONObject && data.optJSONObject("data") != null &&
                (data.optJSONObject("data")!!.isNull("user")) && info.userId.isEmpty()) {
                throw NotFound(msg("browser.user_not_found", "name" to username))
            }
        } catch (e: NotFound) {
            if (info.userId.isEmpty()) throw NotFound(msg("browser.user_not_found", "name" to username))
        } catch (e: RateLimited) {
            rateHit = true; debug("web_profile_info: ${e.m.key}")
        } catch (e: ApiError) {
            debug("web_profile_info: ${e.m.key}")
        } catch (e: LoginRequired) {
            debug("web_profile_info: ${e.m.key}")
        }

        if (info.userId.isEmpty()) {
            try {
                val data = call("/web/search/topsearch/", listOf("query" to username), what = "search")
                Media.findUser(data, username)?.let { mergeUser(info, it); info.sources += "search" }
            } catch (e: RateLimited) {
                rateHit = true; debug("topsearch: ${e.m.key}")
            } catch (e: IgError) {
                debug("topsearch: ${e.m.key}")
            }
        }

        if (info.userId.isEmpty()) {
            if (rateHit) throw RateLimited("profile")
            if (status == 404 || "page not found" in html.lowercase().take(4000)) {
                throw NotFound(msg("browser.user_not_found", "name" to username))
            }
            throw IgError(msg("browser.profile_failed", "name" to username))
        }
        return info
    }

    private fun mergeUser(info: ProfileInfo, user: JSONObject) {
        val merged = JSONObject()
        fun putAll(src: JSONObject, skipEmpty: Boolean) {
            for (k in src.keys()) {
                val v = src.opt(k)
                if (skipEmpty && !Media.truthy(v) && v !is Boolean && v !is Number) continue
                merged.put(k, v)
            }
        }
        putAll(user, false)
        putAll(info.user, true)
        putAll(user, true)
        info.user = merged
        if (info.userId.isEmpty()) info.userId = Media.userIdOf(user)
        if (info.fullName.isEmpty()) info.fullName = Media.pyStr(Media.or(Media.opt(user, "full_name"), ""))
        if (user.has("is_private")) info.isPrivate = Media.truthy(user.opt("is_private"))
        val friendship = user.optJSONObject("friendship_status")
        if (Media.truthy(Media.opt(user, "followed_by_viewer")) || Media.truthy(Media.opt(friendship, "following"))) info.followed = true
        var count: Any? = Media.opt(user, "media_count")
        if (count == null) count = (Media.opt(user, "edge_owner_to_timeline_media") as? JSONObject)?.let { Media.opt(it, "count") }
        if (count != null) {
            val n = when (count) {
                is Number -> count.toLong()
                is String -> count.trim().toLongOrNull()
                else -> null
            }
            if (n != null) info.mediaCount = n.toInt()
        }
    }

    private fun addFirstItems(info: ProfileInfo, data: Any) {
        val seen = info.firstItems.map { Media.mediaKey(it) }.toHashSet()
        for (item in Media.findMedia(data)) {
            val name = Media.pyStr(Media.or(Media.opt(Media.ownerOf(item), "username"), "")).lowercase()
            if (name.isNotEmpty() && name != info.username) continue
            val key = Media.mediaKey(item)
            if (key.isNotEmpty() && seen.add(key)) info.firstItems += item
        }
        val edge = (data as? JSONObject)?.opt("edge_owner_to_timeline_media") as? JSONObject
        val pageInfo = edge?.opt("page_info") as? JSONObject
        if (pageInfo != null) {
            info.hasNext = Media.truthy(pageInfo.opt("has_next_page"))
            info.endCursor = Media.pyStr(Media.or(Media.opt(pageInfo, "end_cursor"), ""))
        }
    }

    suspend fun avatarUrls(info: ProfileInfo, loggedIn: Boolean): List<String> {
        val urls = mutableListOf<String>()
        if (loggedIn && info.userId.isNotEmpty()) {
            try {
                val data = call("/api/v1/users/${info.userId}/info/", what = "avatar")
                val user = (data as? JSONObject)?.opt("user") as? JSONObject
                if (user != null) {
                    urls += Media.avatarUrls(user)
                    if (Media.truthy(Media.opt(user, "profile_pic_id")) && !Media.truthy(Media.opt(info.user, "profile_pic_id"))) {
                        info.user.put("profile_pic_id", user.opt("profile_pic_id"))
                    }
                }
            } catch (e: IgError) {
                debug("user info: ${e.m.key}")
            }
        }
        urls += Media.avatarUrls(info.user)
        return urls.distinct()
    }

    // ------------------------------------------------------------ gönderiler
    suspend fun collectPosts(
        info: ProfileInfo, loggedIn: Boolean, limit: Int,
        onPage: (List<JSONObject>, String, String) -> String,
        resume: Pair<String, String>? = null,
    ): FeedResult {
        val state = FeedState(limit, onPage, resume)
        val result = FeedResult()
        fun finish(reason: String, via: String): FeedResult {
            result.reason = if (state.limitHit) "limit" else reason
            result.collected = state.seen.size
            result.via = via
            return result
        }

        if (!loggedIn && info.firstItems.isNotEmpty()) {
            if (state.accept(info.firstItems, "graphql_out", info.endCursor) == "stop") return finish("up_to_date", "page")
            if (state.limitHit) return finish("end", "page")
        }

        val strategies = if (loggedIn) listOf("rest", "graphql", "scroll") else listOf("graphql", "scroll")

        var lastReason = "stalled"
        var rateHits = 0
        for (name in strategies) {
            checkCancel()
            val reason = try {
                when (name) {
                    "rest" -> feedRest(info, state)
                    "graphql" -> feedGraphql(info, state, loggedIn)
                    else -> feedScroll(info, state)
                }
            } catch (e: ApiError) {
                log("info", msg("browser.strategy_failed", "detail" to e.detail)); continue
            } catch (e: RateLimited) {
                lastReason = "rate_limited"; rateHits++; continue
            } catch (e: LoginRequired) {
                if (loggedIn) {
                    if (state.seen.isEmpty()) throw LoginRequired(msg("browser.session_expired"))
                    return finish("session_expired", name)
                }
                lastReason = "login_wall"; continue
            }
            if (reason in setOf("end", "up_to_date", "limit")) return finish(reason, name)
            lastReason = reason
            if (reason == "login_wall" && name != "graphql") break
        }
        if (rateHits > 0 && (info.mediaCount < 0 || state.seen.size < info.mediaCount)) {
            if (state.seen.isEmpty()) throw RateLimited("feed")
            return finish("rate_limited", "")
        }
        if (!loggedIn && (info.mediaCount < 0 || state.seen.size < info.mediaCount)) return finish("login_wall", "")
        return finish(if (state.seen.isNotEmpty()) lastReason else "stalled", "")
    }

    private suspend fun feedRest(info: ProfileInfo, state: FeedState): String {
        var maxId = ""
        var fallback: String? = null
        var pages = 0
        while (true) {
            checkCancel()
            if (pages > 0) pageDelay()
            // Sitenin kendi sayfa boyutu 12; telefonda Instagram daha dikkatli olduğu için büyük sayfa istemiyoruz.
            val params = mutableListOf("count" to "12")
            if (maxId.isNotEmpty()) params += "max_id" to maxId
            val data: JSONObject
            try {
                // İlk istek reddedilirse beklemeden sıradaki yola geçilir (Instagram bazı oturumlarda bu
                // listeyi hep reddediyor; beklemek düzeltmiyor). Liste başladıktan sonraki retler beklenir.
                val d = call("/api/v1/feed/user/${info.userId}/", params, what = "feed", essential = pages > 0)
                if (d !is JSONObject || !d.has("items")) throw ApiError("feed: no items")
                data = d
            } catch (e: IgError) {
                if (e !is ApiError && e !is NotFound) throw e
                val fb = fallback ?: throw e
                log("info", msg("browser.resume_failed"))
                maxId = fb; fallback = null
                continue
            }
            fallback = null
            pages++
            val arr = data.optJSONArray("items")
            val items = if (arr == null) emptyList() else (0 until arr.length()).mapNotNull { arr.opt(it) as? JSONObject }.filter { Media.isMedia(it) }
            val nextId = Media.pyStr(Media.or(Media.opt(data, "next_max_id"), ""))
            val more = Media.truthy(Media.opt(data, "more_available")) && nextId.isNotEmpty() && nextId != maxId
            val decision = state.accept(items, "rest", if (more) nextId else "")
            if (decision == "stop") return "up_to_date"
            if (state.limitHit) return "limit"
            if (decision == "jump" && more) {
                val target = state.jumpTarget("rest")
                if (target.isNotEmpty() && target != nextId) {
                    log("info", msg("browser.resuming"))
                    fallback = nextId
                    maxId = target
                    continue
                }
            }
            if (!more) return "end"
            maxId = nextId
        }
    }

    private suspend fun feedGraphql(info: ProfileInfo, state: FeedState, loggedIn: Boolean): String {
        var cursor = if (!loggedIn) info.endCursor else ""
        if (!loggedIn && info.hasNext == false && state.seen.isNotEmpty()) return "end"
        var pages = 0
        while (true) {
            checkCancel()
            if (pages > 0) pageDelay()
            val dataVars = JSONObject().put("count", 12).put("include_relationship_info", true)
                .put("latest_besties_reel_media", true).put("latest_reel_media", true)
            val variables = JSONObject()
            val docId: String
            if (loggedIn) {
                dataVars.put("include_reel_media_seen_timestamp", true)
                variables.put("data", dataVars).put("username", info.username)
                docId = DOC_FEED_LOGGED_IN
            } else {
                variables.put("data", dataVars).put("id", info.userId)
                docId = DOC_FEED_LOGGED_OUT
            }
            variables.put("__relay_internal__pv__PolarisFeedShareMenurelayprovider", false)
            if (cursor.isNotEmpty()) {
                variables.put("after", cursor).put("before", JSONObject.NULL).put("first", 12).put("last", JSONObject.NULL)
            } else {
                variables.put("first", 12)
            }
            val form = linkedMapOf("doc_id" to docId, "variables" to variables.toString(), "server_timestamps" to "true")
            val data = call("/graphql/query", form = form, what = "graphql", essential = pages > 0)
            if (data is JSONObject && Media.truthy(Media.opt(data, "errors")) && !Media.truthy(Media.opt(data, "data"))) {
                throw ApiError("graphql: " + data.opt("errors").toString().take(120))
            }
            val items = Media.findMedia(data)
            val pageInfo = Media.findPageInfo(data)
            if (pageInfo == null && items.isEmpty()) throw ApiError("graphql: no posts in response")
            pages++
            val kind = if (loggedIn) "graphql_in" else "graphql_out"
            val hasNext = pageInfo != null && Media.truthy(pageInfo.opt("has_next_page"))
            val nextCursor = Media.pyStr(Media.or(Media.opt(pageInfo, "end_cursor"), ""))
            val decision = state.accept(items, kind, if (hasNext) nextCursor else "")
            if (decision == "stop") return "up_to_date"
            if (state.limitHit) return "limit"
            if (decision == "jump" && hasNext) {
                val target = state.jumpTarget(kind)
                if (target.isNotEmpty() && target != nextCursor) {
                    log("info", msg("browser.resuming"))
                    cursor = target
                    continue
                }
            }
            if (!hasNext) return "end"
            if (nextCursor.isEmpty() || nextCursor == cursor) return if (items.isNotEmpty()) "end" else "login_wall"
            if (items.isEmpty() && pages > 1) return "login_wall"
            cursor = nextCursor
        }
    }

    /** Son çare: profil sayfasını kaydırıp sayfanın kendi yüklediği gönderileri yakalar. */
    private suspend fun feedScroll(info: ProfileInfo, state: FeedState): String {
        val idleMs = ((TestHooks.idleSeconds ?: 25.0) * 1000).toLong()
        var upToDate = false
        var last = System.currentTimeMillis()
        val lock = Any()
        fun mine(items: List<JSONObject>) = items.filter {
            Media.pyStr(Media.or(Media.opt(Media.ownerOf(it), "username"), info.username)).lowercase() == info.username
        }
        log("info", msg("browser.scrolling"))
        web.setCapture { url, data ->
            if ("/graphql" !in url && "/api/v1/" !in url) return@setCapture
            synchronized(lock) {
                val before = state.seen.size
                if (state.accept(mine(Media.findMedia(data))) == "stop") upToDate = true
                if (state.seen.size > before) last = System.currentTimeMillis()
            }
        }
        try {
            goto("https://www.instagram.com/${info.username}/")
            sleep(2000)
            for (data in web.embeddedJson()) {
                synchronized(lock) { if (state.accept(mine(Media.findMedia(data))) == "stop") upToDate = true }
                if (upToDate) return "up_to_date"
            }
            last = System.currentTimeMillis()
            while (true) {
                checkCancel()
                if (upToDate) return "up_to_date"
                if (state.limitHit) return "limit"
                if (pageRedirectState() == "challenge") {
                    waitForVerification()
                    last = System.currentTimeMillis()
                    continue
                }
                web.scrollDown()
                sleep(1500)
                if (System.currentTimeMillis() - last > idleMs) {
                    if (info.mediaCount >= 0 && state.seen.size >= info.mediaCount) return "end"
                    return if (state.seen.isNotEmpty()) "login_wall" else "stalled"
                }
            }
        } finally {
            web.setCapture(null)
        }
    }

    // ------------------------------------------------------------ hikayeler ve öne çıkanlar
    private fun reelsFrom(data: Any?): List<JSONObject> {
        if (data !is JSONObject) return emptyList()
        val list = data.opt("reels_media")
        if (list is JSONArray) return (0 until list.length()).mapNotNull { list.opt(it) as? JSONObject }
        val map = data.opt("reels")
        if (map is JSONObject) return map.keys().asSequence().mapNotNull { map.opt(it) as? JSONObject }.toList()
        return emptyList()
    }

    private fun itemsOf(reel: JSONObject): List<JSONObject> {
        val arr = reel.optJSONArray("items") ?: return emptyList()
        return (0 until arr.length()).mapNotNull { arr.opt(it) as? JSONObject }.filter { Media.isMedia(it) }
    }

    suspend fun stories(userId: String, username: String = ""): List<JSONObject> {
        val data = try {
            call("/api/v1/feed/reels_media/", listOf("reel_ids" to userId), what = "stories")
        } catch (e: ApiError) {
            if (username.isEmpty()) throw e
            // Liste isteği çalışmadı: hikâye sayfası açılıp sayfanın kendi yüklediği öğeler yakalanır.
            debug("stories: ${e.detail}")
            log("info", msg("browser.stories_from_page"))
            val found = captureMediaFromPage("$base/stories/$username/", 10_000)
            if (found.isNotEmpty()) return found
            throw e
        }
        return reelsFrom(data).flatMap { itemsOf(it) }
    }

    /** Sayfayı açar; sayfaya gömülü ve sayfanın kendi yüklediği yanıtlardaki gönderileri toplar. */
    private suspend fun captureMediaFromPage(url: String, waitMs: Long = 8000, scroll: Boolean = false,
                                             keep: ((JSONObject) -> Boolean)? = null): List<JSONObject> {
        val found = LinkedHashMap<String, JSONObject>()
        val lock = Any()
        fun take(data: Any) = synchronized(lock) {
            for (item in Media.findMedia(data)) {
                val key = Media.mediaKey(item)
                if (key.isNotEmpty() && key !in found && (keep == null || keep(item))) found[key] = item
            }
        }
        web.setCapture { u, data -> if ("/graphql" in u || "/api/v1/" in u) take(data) }
        try {
            goto(url)
            val end = System.currentTimeMillis() + waitMs
            while (System.currentTimeMillis() < end) {
                for (d in web.embeddedJson()) take(d)
                if (scroll) web.scrollDown()
                sleep(1000)
            }
        } finally {
            web.setCapture(null)
        }
        return synchronized(lock) { found.values.toList() }
    }

    suspend fun highlightsTray(userId: String, username: String = ""): List<JSONObject> {
        var error: IgError = ApiError("highlights: no tray")
        try {
            // Reddedilirse beklemeden profil sayfasından okunur.
            val data = call("/api/v1/highlights/$userId/highlights_tray/", what = "highlights")
            val tray = (data as? JSONObject)?.opt("tray")
            if (tray is JSONArray) return if (tray.length() > 0) Media.findHighlights(tray) else emptyList()
            error = ApiError("highlights: no tray")
        } catch (e: IgError) {
            if (e !is ApiError && e !is RateLimited && e !is NotFound) throw e
            error = e
        }
        if (username.isEmpty()) throw error
        log("info", msg("browser.highlights_from_page"))
        val found = captureFromProfilePage(username) { Media.findHighlights(it) }
        if (found.isNotEmpty()) return found
        throw error
    }

    private suspend fun captureFromProfilePage(username: String, waitMs: Long = 8000, finder: (Any) -> List<JSONObject>): List<JSONObject> {
        val found = mutableListOf<JSONObject>()
        val keys = HashSet<String>()
        val lock = Any()
        fun take(data: Any) = synchronized(lock) {
            for (e in finder(data)) if (keys.add(e.optString("id"))) found += e
        }
        web.setCapture { _, data -> take(data) }
        try {
            goto("https://www.instagram.com/$username/")
            val end = System.currentTimeMillis() + waitMs
            while (System.currentTimeMillis() < end) {
                for (d in web.embeddedJson()) take(d)
                sleep(1000)
            }
        } finally {
            web.setCapture(null)
        }
        return synchronized(lock) { found.toList() }
    }

    class Reel(val id: String, val title: String, val user: JSONObject, val items: List<JSONObject>, val mediaCount: Int)

    private fun reelEntry(reel: JSONObject): Reel {
        val ids = reel.optJSONArray("media_ids")
        return Reel(
            Media.pyStr(Media.or(Media.opt(reel, "id"), "")),
            Media.pyStr(Media.or(Media.opt(reel, "title"), "")),
            reel.optJSONObject("user") ?: JSONObject(),
            itemsOf(reel),
            Media.toInt(Media.or(Media.opt(reel, "media_count"), ids?.length() ?: 0, 0)).toInt(),
        )
    }

    /**
     * Öne çıkanların içeriği. Üçerli gruplar halinde istenir; eksik ya da boş gelen her biri tek
     * başına tekrar istenir. Alınamayanlar failed listesine yazılır.
     */
    suspend fun highlightReels(reelIds: List<String>, expected: Map<String, Int> = emptyMap(),
                               failed: MutableList<String>? = null,
                               titles: Map<String, String> = emptyMap()): List<Reel> {
        val got = LinkedHashMap<String, Reel>()
        var limited = false
        for (chunk in reelIds.chunked(3)) {
            checkCancel()
            val data = try {
                call("/api/v1/feed/reels_media/", chunk.map { "reel_ids" to it }, what = "highlights", essential = true)
            } catch (e: RateLimited) {
                limited = true; break
            } catch (e: IgError) {
                if (e !is ApiError && e !is NotFound) throw e
                debug("highlights chunk: ${(e as? ApiError)?.detail ?: e.m.key}"); null
            }
            for (reel in reelsFrom(data)) {
                val r = reelEntry(reel)
                if (r.id.isNotEmpty()) got[r.id] = r
            }
        }
        for (rid in reelIds) {
            checkCancel()
            var entry = got[rid]
            val want = maxOf(expected[rid] ?: 0, entry?.mediaCount ?: 0)
            if (entry != null && entry.items.isNotEmpty() && entry.items.size >= want) continue
            if (!limited) {
                try {
                    val data = call("/api/v1/feed/reels_media/", listOf("reel_ids" to rid), what = "highlights", essential = true)
                    for (reel in reelsFrom(data)) {
                        val single = reelEntry(reel)
                        if (single.id == rid && (entry == null || single.items.size > entry.items.size)) {
                            got[rid] = single; entry = single
                        }
                    }
                } catch (e: RateLimited) {
                    limited = true
                } catch (e: IgError) {
                    if (e !is ApiError && e !is NotFound) throw e
                    debug("highlight $rid: ${(e as? ApiError)?.detail ?: e.m.key}")
                }
            }
            if ((entry == null || entry.items.isEmpty()) && !limited) {
                // Liste isteği çalışmadı: öne çıkanın kendi sayfası açılıp sayfanın yüklediği öğeler yakalanır.
                val pageEntry = highlightFromPage(rid, titles[rid] ?: "")
                if (pageEntry != null) { got[rid] = pageEntry; entry = pageEntry }
            }
            if ((entry == null || entry.items.isEmpty())) failed?.add(rid)
        }
        return reelIds.mapNotNull { got[it] }.filter { it.items.isNotEmpty() }
    }

    private suspend fun highlightFromPage(rid: String, title: String): Reel? {
        val number = rid.substringAfterLast(':')
        log("info", msg("browser.highlight_from_page", "name" to title.ifEmpty { number }))
        val items = captureMediaFromPage("$base/stories/highlights/$number/", 10_000)
        if (items.isEmpty()) return null
        return Reel(rid, title, JSONObject(), items, items.size)
    }

    // ------------------------------------------------------------ Reels / etiketlenenler / yeniden paylaşımlar
    /**
     * Profilin ayrı sekmelerini (reels | tagged | reposts) toplar. Reels ve etiketlenenler için önce kendi
     * liste isteği denenir; olmazsa (ya da yeniden paylaşımlar için doğrudan) sekme sayfası kaydırılır.
     */
    suspend fun collectTab(info: ProfileInfo, tab: String, loggedIn: Boolean, limit: Int,
                           onPage: (List<JSONObject>, String, String) -> String): FeedResult {
        val state = FeedState(limit, onPage, null)
        val result = FeedResult()
        fun finish(reason: String, via: String): FeedResult {
            result.reason = if (state.limitHit) "limit" else reason
            result.collected = state.seen.size
            result.via = via
            return result
        }
        val strategies = mutableListOf<String>()
        if (loggedIn && tab == "reels") strategies += "clips"
        if (loggedIn && tab == "tagged") strategies += "usertags"
        strategies += "scroll"
        var lastReason = "stalled"
        var rateHits = 0
        for (name in strategies) {
            checkCancel()
            val reason = try {
                when (name) {
                    "clips" -> tabClips(info, state)
                    "usertags" -> tabUsertags(info, state)
                    else -> tabScroll(info, tab, state)
                }
            } catch (e: ApiError) {
                log("info", msg("browser.strategy_failed", "detail" to e.detail)); continue
            } catch (e: NotFound) {
                continue
            } catch (e: RateLimited) {
                lastReason = "rate_limited"; rateHits++; continue
            } catch (e: LoginRequired) {
                if (state.seen.isNotEmpty()) return finish("session_expired", name)
                lastReason = "login_wall"; continue
            }
            if (reason in setOf("end", "up_to_date", "limit")) return finish(reason, name)
            lastReason = reason
        }
        if (rateHits > 0 && state.seen.isEmpty()) throw RateLimited(tab)
        if (rateHits > 0) return finish("rate_limited", "")
        return finish(if (state.seen.isNotEmpty()) lastReason else if (!loggedIn) "login_wall" else "stalled", "")
    }

    /** Etiketlenenler ve yeniden paylaşımlarda profilin kendi gönderileri hariç tutulur; reels'te yalnız klipler. */
    private fun tabFilter(info: ProfileInfo, tab: String, items: List<JSONObject>): List<JSONObject> = items.filter {
        val owner = Media.pyStr(Media.or(Media.opt(Media.ownerOf(it), "username"), "")).lowercase()
        when (tab) {
            "tagged", "reposts" -> !(owner.isNotEmpty() && owner == info.username.lowercase())
            "reels" -> {
                if (owner.isNotEmpty() && owner != info.username.lowercase()) false
                else if (!Media.isReel(it) && Media.isMedia(it) &&
                    Media.pyStr(Media.or(Media.opt(it, "product_type"), "")).isNotEmpty()) false
                else true
            }
            else -> true
        }
    }

    private suspend fun tabClips(info: ProfileInfo, state: FeedState): String {
        var maxId = ""
        var pages = 0
        while (true) {
            checkCancel()
            val form = linkedMapOf("target_user_id" to info.userId, "page_size" to "12", "include_feed_video" to "true")
            if (maxId.isNotEmpty()) form["max_id"] = maxId
            val data = call("/api/v1/clips/user/", form = form, what = "reels", essential = pages > 0) as? JSONObject
            val arr = data?.optJSONArray("items") ?: throw ApiError("clips: no items")
            val raw = mutableListOf<JSONObject>()
            for (i in 0 until arr.length()) {
                val entry = arr.opt(i) as? JSONObject ?: continue
                val inner = entry.optJSONObject("media") ?: entry
                if (Media.isMedia(inner)) raw += inner
            }
            pages++
            val paging = data.optJSONObject("paging_info")
            val nextId = Media.pyStr(Media.or(Media.opt(paging, "max_id"), ""))
            val more = Media.truthy(Media.opt(paging, "more_available")) && nextId.isNotEmpty() && nextId != maxId
            val decision = state.accept(tabFilter(info, "reels", raw), "clips", if (more) nextId else "")
            if (decision == "stop") return "up_to_date"
            if (state.limitHit) return "limit"
            if (!more) return "end"
            maxId = nextId
        }
    }

    private suspend fun tabUsertags(info: ProfileInfo, state: FeedState): String {
        var maxId = ""
        var pages = 0
        while (true) {
            checkCancel()
            val params = mutableListOf("count" to "30")
            if (maxId.isNotEmpty()) params += "max_id" to maxId
            val data = call("/api/v1/usertags/${info.userId}/feed/", params, what = "tagged", essential = pages > 0) as? JSONObject
            val arr = data?.optJSONArray("items") ?: throw ApiError("usertags: no items")
            val items = (0 until arr.length()).mapNotNull { arr.opt(it) as? JSONObject }.filter { Media.isMedia(it) }
            pages++
            val nextId = Media.pyStr(Media.or(Media.opt(data, "next_max_id"), ""))
            val more = Media.truthy(Media.opt(data, "more_available")) && nextId.isNotEmpty() && nextId != maxId
            val decision = state.accept(tabFilter(info, "tagged", items), "usertags", if (more) nextId else "")
            if (decision == "stop") return "up_to_date"
            if (state.limitHit) return "limit"
            if (!more) return "end"
            maxId = nextId
        }
    }

    /** Sekme sayfasını kaydırıp sayfanın kendi yüklediği gönderileri yakalar. */
    private suspend fun tabScroll(info: ProfileInfo, tab: String, state: FeedState): String {
        val idleMs = ((TestHooks.idleSeconds ?: 25.0) * 1000).toLong()
        var upToDate = false
        var last = System.currentTimeMillis()
        val lock = Any()
        val kind = "scroll_$tab"
        fun feed(data: Any) = synchronized(lock) {
            val before = state.seen.size
            if (state.accept(tabFilter(info, tab, Media.findMedia(data)), kind) == "stop") upToDate = true
            if (state.seen.size > before) last = System.currentTimeMillis()
        }
        log("info", msg("browser.scrolling"))
        web.setCapture { url, data -> if ("/graphql" in url || "/api/v1/" in url) feed(data) }
        try {
            goto("https://www.instagram.com/${info.username}/$tab/")
            sleep(2000)
            for (d in web.embeddedJson()) feed(d)
            last = System.currentTimeMillis()
            while (true) {
                checkCancel()
                if (upToDate) return "up_to_date"
                if (state.limitHit) return "limit"
                if (pageRedirectState() == "challenge") {
                    waitForVerification()
                    last = System.currentTimeMillis()
                    continue
                }
                web.scrollDown()
                sleep(1500)
                if (System.currentTimeMillis() - last > idleMs) return if (state.seen.isNotEmpty()) "end" else "stalled"
            }
        } finally {
            web.setCapture(null)
        }
    }

    // ------------------------------------------------------------ tek gönderi
    suspend fun getPost(shortcode: String, loggedIn: Boolean): JSONObject {
        val mediaId = Links.idFromShortcode(shortcode)
        if (loggedIn && mediaId.isNotEmpty()) {
            try {
                val data = call("/api/v1/media/$mediaId/info/", what = "post")
                val arr = (data as? JSONObject)?.optJSONArray("items")
                val items = if (arr == null) emptyList() else (0 until arr.length()).mapNotNull { arr.opt(it) as? JSONObject }.filter { Media.isMedia(it) }
                if (items.isNotEmpty()) return items[0]
            } catch (e: NotFound) {
                throw NotFound(msg("browser.post_not_found"))
            } catch (e: IgError) {
                if (e !is ApiError && e !is LoginRequired && e !is RateLimited) throw e
                debug("media info: ${e.m.key}")
            }
        }
        return postFromPage(shortcode)
    }

    private suspend fun postFromPage(shortcode: String): JSONObject {
        var found: JSONObject? = null
        val lock = Any()
        fun match(data: Any) = synchronized(lock) {
            for (item in Media.findMedia(data)) {
                if (found == null && (Media.mediaKey(item) == shortcode ||
                        Media.pyStr(Media.or(Media.opt(item, "code"), Media.opt(item, "shortcode"), "")) == shortcode)) found = item
            }
        }
        web.setCapture { _, data -> match(data) }
        try {
            log("info", msg("browser.post_opening"))
            val status = goto("https://www.instagram.com/p/$shortcode/")
            var deadline = System.currentTimeMillis() + 20_000
            while (System.currentTimeMillis() < deadline) {
                checkCancel()
                when (pageRedirectState()) {
                    "challenge" -> {
                        waitForVerification()
                        goto("https://www.instagram.com/p/$shortcode/")
                        deadline = System.currentTimeMillis() + 20_000
                        continue
                    }
                    "login" -> throw LoginRequired(msg("browser.post_login_required"))
                }
                for (d in web.embeddedJson()) match(d)
                synchronized(lock) { found }?.let { return it }
                if (status == 404) break
                sleep(1000)
            }
        } finally {
            web.setCapture(null)
        }
        return synchronized(lock) { found } ?: throw NotFound(msg("browser.post_not_found"))
    }

    suspend fun resolveShare(url: String): Links.Parsed {
        log("info", msg("browser.share_opening"))
        goto(url)
        repeat(30) {
            var current = web.currentUrl()
            if (base != "https://www.instagram.com") current = current.replaceFirst(base, "https://www.instagram.com")
            val p = Links.parse(current)
            if (p.ok && p.kind != Links.SHARE) return p
            if (pageRedirectState() == "login") throw LoginRequired(msg("browser.post_login_required"))
            sleep(500)
        }
        throw IgError(msg("browser.share_failed"))
    }

    companion object {
        const val APP_ID = "936619743392459"
        const val ASBD_ID = "129477"
        const val DOC_FEED_LOGGED_IN = "28975909992013618"
        const val DOC_FEED_LOGGED_OUT = "7950326061742207"
        val CHALLENGE_PATHS = listOf("/challenge", "/checkpoint", "/auth_platform", "/accounts/suspended", "/accounts/disabled")
        val LOGIN_PATHS = listOf("/accounts/login", "/accounts/signup")

        fun pathOf(url: String): String {
            val rest = url.substringAfter("://", url)
            val i = rest.indexOf('/')
            return if (i < 0) "" else rest.substring(i).substringBefore('?').substringBefore('#').lowercase()
        }

        fun parseJsonText(textIn: String?): Any? {
            var text = (textIn ?: "").trim()
            if (text.startsWith("for (;;);")) text = text.removePrefix("for (;;);")
            if (text.isEmpty() || (text[0] != '{' && text[0] != '[')) return null
            return try {
                JSONTokener(text).nextValue()
            } catch (e: Exception) {
                try { JSONTokener(text.substringBefore('\n')).nextValue() } catch (e2: Exception) { null }
            }
        }

        /** Bir API yanıtının anlamı: ok | login | challenge | rate | notfound | error. (browser.classify) */
        fun classify(status: Int, url: String, data: Any?): String {
            val path = pathOf(url)
            val d = data as? JSONObject ?: JSONObject()
            val message = Media.pyStr(Media.or(Media.opt(d, "message"), "")).lowercase()
            val challenge = d.opt("challenge")
            if (CHALLENGE_PATHS.any { it in path } || message in setOf("challenge_required", "checkpoint_required") ||
                Media.truthy(Media.opt(d, "checkpoint_url")) || (challenge is JSONObject && !Media.truthy(Media.opt(d, "items")))) {
                return "challenge"
            }
            if (status == 429 || "wait a few minutes" in message || "please wait" in message ||
                message in setOf("feedback_required", "rate_limit_error") || d.opt("spam") == true) return "rate"
            if (LOGIN_PATHS.any { it in path } || message in setOf("login_required", "user_has_logged_out") ||
                d.opt("require_login") == true) return "login"
            if (status == 404) return "notfound"
            if (status == 401 || status == 403) return "login"
            if (status == 200 && (data is JSONObject || data is JSONArray)) {
                if (data is JSONObject && data.opt("status") == "fail") return "error"
                return "ok"
            }
            return "error"
        }
    }
}
