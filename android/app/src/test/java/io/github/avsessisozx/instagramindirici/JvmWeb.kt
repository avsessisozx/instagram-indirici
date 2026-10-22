package io.github.avsessisozx.instagramindirici

import java.io.ByteArrayOutputStream
import java.net.CookieManager
import java.net.CookiePolicy
import java.net.HttpCookie
import java.net.HttpURLConnection
import java.net.URI
import java.net.URL
import java.net.URLEncoder
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Birim testlerinde gizli WebView'in yerine geçen düz HTTP istemcisi.
 *
 * Sahte Instagram sunucusuna (tests/fake_instagram.py) gerçek HTTP istekleri yapar; WebView'deki
 * sayfa betiğinin (__igFetch) gönderdiği başlıkları aynen gönderir. Böylece Kotlin'deki bütün
 * Instagram mantığı (Insta, Engine) telefon olmadan, Windows sürümünün testlerindeki aynı sahte
 * sunucuya karşı denenir. Sayfadaki JavaScript'in yaptıkları (kaydırınca gönderi yükleme,
 * doğrulamadan sonra ana sayfaya dönme) burada elle taklit edilir.
 */
class JvmWeb(override val baseUrl: String) : IgWeb {
    override var listener: WebListener? = null
    override var cancel: AtomicBoolean = AtomicBoolean(false)
    override val userAgent = "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Mobile Safari/537.36"

    val cookies = CookieManager(null, CookiePolicy.ACCEPT_ALL)
    @Volatile private var lastUrl = ""
    @Volatile private var html = ""
    @Volatile private var capture: ((String, Any) -> Unit)? = null
    private var scrollPage = 1
    @Volatile var verifyShown = 0; private set
    /** Giriş ekranı açılınca sahte sunucu girişi kendisi yapsın mı? */
    var autoLogin = true

    private data class Resp(val status: Int, val url: String, val body: String)

    private fun cookieHeader(url: String): String =
        cookies.cookieStore.get(URI(url)).filter { !it.hasExpired() && it.value.isNotEmpty() }
            .joinToString("; ") { "${it.name}=${it.value}" }

    private fun storeCookies(url: String, conn: HttpURLConnection) {
        val uri = URI(url)
        for ((k, values) in conn.headerFields) {
            if (k == null || !k.equals("Set-Cookie", ignoreCase = true)) continue
            for (v in values) {
                for (c in HttpCookie.parse(v)) {
                    if (c.maxAge == 0L) {
                        cookies.cookieStore.get(uri).filter { it.name == c.name }.forEach { cookies.cookieStore.remove(uri, it) }
                    } else {
                        cookies.cookieStore.add(uri, c)
                    }
                }
            }
        }
    }

    /** Yönlendirmeleri ve çerezleri tarayıcı gibi izleyen istek. */
    private fun request(urlIn: String, method: String, headers: Map<String, String>, body: String?,
                        readTimeoutMs: Int = 45_000): Resp {
        var url = urlIn
        var m = method
        var b = body
        repeat(8) {
            val conn = URL(url).openConnection() as HttpURLConnection
            conn.instanceFollowRedirects = false
            conn.connectTimeout = 15_000
            conn.readTimeout = readTimeoutMs
            conn.requestMethod = m
            conn.setRequestProperty("User-Agent", userAgent)
            for ((k, v) in headers) conn.setRequestProperty(k, v)
            val ck = cookieHeader(url)
            if (ck.isNotEmpty()) conn.setRequestProperty("Cookie", ck)
            if (b != null) {
                conn.doOutput = true
                conn.outputStream.use { it.write(b!!.toByteArray(Charsets.UTF_8)) }
            }
            val status = try { conn.responseCode } catch (e: Exception) { return Resp(0, url, "") }
            storeCookies(url, conn)
            if (status in 300..399) {
                val loc = conn.getHeaderField("Location") ?: return Resp(status, url, "")
                url = URL(URL(url), loc).toString()
                m = "GET"; b = null
                conn.disconnect()
                return@repeat
            }
            val stream = if (status >= 400) conn.errorStream else conn.inputStream
            val out = ByteArrayOutputStream()
            stream?.use { it.copyTo(out) }
            conn.disconnect()
            return Resp(status, url, out.toString("UTF-8"))
        }
        return Resp(0, url, "")
    }

    override suspend fun prepare() {}

    override suspend fun loggedIn(): Boolean =
        cookies.cookieStore.get(URI("$baseUrl/")).any { it.name == "sessionid" && it.value.isNotEmpty() && !it.hasExpired() }

    override suspend fun navigate(url: String): Int {
        if (cancel.get()) throw CancelledException()
        val r = request(url, "GET", mapOf("Accept" to "text/html"), null)
        if (r.status == 0) throw IgError(msg("browser.no_internet"))
        lastUrl = r.url
        html = r.body
        scrollPage = 1
        // Sahte doğrulama sayfası birkaç saniye sonra ana sayfaya döner (kullanıcı doğrulamayı yapmış gibi).
        if ("/challenge/" in Insta.pathOf(r.url)) {
            Thread {
                Thread.sleep(1500)
                if ("/challenge/" in Insta.pathOf(lastUrl)) lastUrl = "$baseUrl/"
            }.start()
        }
        return r.status
    }

    override fun currentUrl(): String = lastUrl

    override suspend fun fetch(url: String, form: Map<String, String>?, timeoutMs: Long): RawResponse {
        val csrf = cookies.cookieStore.get(URI(url)).firstOrNull { it.name == "csrftoken" }?.value ?: ""
        val headers = linkedMapOf(
            "Accept" to "*/*", "X-IG-App-ID" to Insta.APP_ID, "X-ASBD-ID" to Insta.ASBD_ID,
            "X-IG-WWW-Claim" to "0", "X-Requested-With" to "XMLHttpRequest", "X-CSRFToken" to csrf,
        )
        var body: String? = null
        if (form != null) {
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            body = form.entries.joinToString("&") { (k, v) -> URLEncoder.encode(k, "UTF-8") + "=" + URLEncoder.encode(v, "UTF-8") }
        }
        val r = request(url, if (form != null) "POST" else "GET", headers, body, timeoutMs.toInt().coerceAtLeast(500))
        return RawResponse(r.status, r.url, r.body, if (r.status == 0) "timeout" else "")
    }

    override suspend fun embeddedJson(): List<Any> =
        Regex("<script type=\"application/json\"[^>]*>(.*?)</script>", RegexOption.DOT_MATCHES_ALL)
            .findAll(html).map { it.groupValues[1] }
            .filter { it.length > 50 && ("username" in it || "image_versions2" in it || "display_url" in it) }
            .mapNotNull { Insta.parseJsonText(it.replace("<\\/", "</")) }.toList()

    override suspend fun pageHtml(): String = html

    override fun setCapture(handler: ((String, Any) -> Unit)?) { capture = handler }

    /** Sahte profil sayfasındaki betiğin yaptığı gibi: aşağı kayınca bir sonraki gönderi sayfasını yükler. */
    override suspend fun scrollDown() {
        val path = Insta.pathOf(lastUrl).trim('/')
        if (path.isEmpty() || '/' in path) return
        val url = "$baseUrl/graphql/query/?scroll=$scrollPage&u=$path"
        scrollPage++
        val r = request(url, "GET", mapOf("Accept" to "*/*"), null)
        val data = Insta.parseJsonText(r.body) ?: return
        capture?.invoke(url, data)
    }

    override suspend fun showVerify(show: Boolean) { if (show) verifyShown++ }

    override suspend fun loginSession(): Boolean {
        navigate("$baseUrl/accounts/login/")
        if (autoLogin) navigate("$baseUrl/")   // sahte sunucu girişi yaptı; sayfa ana sayfaya döner
        return loggedIn()
    }
}
