package io.github.avsessisozx.instagramindirici

import android.annotation.SuppressLint
import android.graphics.Bitmap
import android.webkit.CookieManager
import android.webkit.JavascriptInterface
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.webkit.WebViewCompat
import androidx.webkit.WebViewFeature
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import org.json.JSONArray
import org.json.JSONObject
import org.json.JSONTokener
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger
import kotlin.coroutines.resume

/**
 * Gizli WebView ile instagram.com'u açar. İstekler sayfanın içinden yapılır (çerezler, site
 * başlıkları tarayıcıda kalır). Windows sürümünde Playwright'ın yaptığı işin karşılığı.
 *
 * Sayfa başlarken eklenen küçük betik iki iş yapar:
 *  - window.__igFetch: uygulamanın isteğini sayfanın içinden yapıp sonucu geri yollar
 *  - sayfanın kendi JSON yanıtlarının bir kopyasını iletir (kaydırarak toplama için)
 */
@SuppressLint("SetJavaScriptEnabled")
class WebInstagram(private val webView: WebView, private val overlay: OverlayHost) : IgWeb {

    override var listener: WebListener? = null
    override var cancel: AtomicBoolean = AtomicBoolean(false)
    override val userAgent: String
    override val baseUrl: String get() = (TestHooks.baseUrl ?: "https://www.instagram.com").trimEnd('/')

    private val pending = ConcurrentHashMap<Int, CompletableDeferred<RawResponse>>()
    private val nextId = AtomicInteger(1)
    @Volatile private var captureHandler: ((String, Any) -> Unit)? = null
    @Volatile private var pageWaiter: CompletableDeferred<Unit>? = null
    @Volatile private var mainFrameError: Int? = null
    @Volatile private var mainFrameStatus: Int = 0
    @Volatile private var lastUrl: String = ""
    private val modern: Boolean

    init {
        val s = webView.settings
        s.javaScriptEnabled = true
        s.domStorageEnabled = true
        s.databaseEnabled = true
        s.mediaPlaybackRequiresUserGesture = true   // gizli sayfada video sesli oynamasın
        s.useWideViewPort = true
        s.loadWithOverviewMode = true
        s.setSupportZoom(true)
        s.builtInZoomControls = true
        s.displayZoomControls = false
        s.allowFileAccess = false
        s.allowContentAccess = false
        s.javaScriptCanOpenWindowsAutomatically = false
        s.setSupportMultipleWindows(false)
        userAgent = desktopUserAgent(s.userAgentString)
        s.userAgentString = userAgent
        CookieManager.getInstance().setAcceptCookie(true)
        CookieManager.getInstance().setAcceptThirdPartyCookies(webView, true)

        val origins = if (TestHooks.baseUrl != null) setOf("*") else ORIGINS
        modern = WebViewFeature.isFeatureSupported(WebViewFeature.WEB_MESSAGE_LISTENER) &&
            WebViewFeature.isFeatureSupported(WebViewFeature.DOCUMENT_START_SCRIPT)
        if (modern) {
            WebViewCompat.addWebMessageListener(webView, "IgBridge", origins) { _, message, _, _, _ ->
                message.data?.let { onMessage(it) }
            }
            WebViewCompat.addDocumentStartJavaScript(webView, pageScript("IgBridge.postMessage(s)"), origins)
        } else {
            webView.addJavascriptInterface(object {
                @JavascriptInterface fun post(s: String) { onMessage(s) }
            }, "IgBridgeLegacy")
        }

        webView.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                val scheme = request.url.scheme?.lowercase() ?: return true
                return scheme != "http" && scheme != "https"   // Instagram uygulamasını açmaya çalışan linkleri engelle
            }

            override fun onPageStarted(view: WebView, url: String?, favicon: Bitmap?) {
                if (url != null) lastUrl = url
                if (!modern) view.evaluateJavascript(pageScript("IgBridgeLegacy.post(s)"), null)
            }

            override fun doUpdateVisitedHistory(view: WebView, url: String?, isReload: Boolean) {
                if (url != null) lastUrl = url
            }

            override fun onPageCommitVisible(view: WebView, url: String?) { pageWaiter?.complete(Unit) }
            override fun onPageFinished(view: WebView, url: String?) {
                if (url != null) lastUrl = url
                pageWaiter?.complete(Unit)
            }

            override fun onReceivedHttpError(view: WebView, request: WebResourceRequest, errorResponse: WebResourceResponse) {
                if (request.isForMainFrame) mainFrameStatus = errorResponse.statusCode
            }

            override fun onReceivedError(view: WebView, request: WebResourceRequest, error: WebResourceError) {
                if (request.isForMainFrame) {
                    mainFrameError = error.errorCode
                    pageWaiter?.complete(Unit)
                }
            }
        }
    }

    /** Sayfadan gelen mesaj: "R\n<no>\n<json>" (isteğin sonucu) ya da "C\n<adres>\n<metin>" (yakalanan yanıt). */
    private fun onMessage(raw: String) {
        if (raw.startsWith("R\n")) {
            val rest = raw.substring(2)
            val nl = rest.indexOf('\n')
            if (nl <= 0) return
            val id = rest.substring(0, nl).toIntOrNull() ?: return
            val o = try { JSONObject(rest.substring(nl + 1)) } catch (e: Exception) { JSONObject() }
            pending.remove(id)?.complete(RawResponse(o.optInt("status", 0), o.optString("url", ""), o.optString("text", ""), o.optString("error", "")))
        } else if (raw.startsWith("C\n")) {
            val handler = captureHandler ?: return
            val rest = raw.substring(2)
            val nl = rest.indexOf('\n')
            if (nl <= 0) return
            val url = rest.substring(0, nl)
            val data = Insta.parseJsonText(rest.substring(nl + 1)) ?: return
            try { handler(url, data) } catch (e: Exception) {}
        }
    }

    private fun checkCancel() { if (cancel.get()) throw CancelledException() }

    private suspend fun eval(js: String, timeoutMs: Long = 15_000): String? = withContext(Dispatchers.Main) {
        withTimeoutOrNull(timeoutMs) {
            suspendCancellableCoroutine<String?> { cont ->
                webView.evaluateJavascript(js) { r -> if (cont.isActive) cont.resume(r) }
            }
        }
    }

    private suspend fun evalString(js: String, timeoutMs: Long = 15_000): String? {
        val r = eval(js, timeoutMs) ?: return null
        if (r == "null") return null
        return try { JSONTokener(r).nextValue() as? String } catch (e: Exception) { null }
    }

    // ------------------------------------------------------------ IgWeb
    override suspend fun prepare() {
        listener?.log("info", msg("browser.opened_mobile"))
    }

    override suspend fun loggedIn(): Boolean = withContext(Dispatchers.Main) {
        val cookies = try { CookieManager.getInstance().getCookie("$baseUrl/") } catch (e: Exception) { null } ?: ""
        cookies.split(';').any { val p = it.trim(); p.startsWith("sessionid=") && p.length > "sessionid=".length }
    }

    override suspend fun navigate(url: String): Int = withContext(Dispatchers.Main) {
        checkCancel()
        mainFrameError = null
        mainFrameStatus = 0
        val waiter = CompletableDeferred<Unit>()
        pageWaiter = waiter
        webView.loadUrl(url)
        val ok = withTimeoutOrNull(60_000) { waiter.await() }
        pageWaiter = null
        val err = mainFrameError
        if (err != null && !cancel.get()) {
            when (err) {
                WebViewClient.ERROR_HOST_LOOKUP, WebViewClient.ERROR_CONNECT, WebViewClient.ERROR_TIMEOUT,
                WebViewClient.ERROR_PROXY_AUTHENTICATION -> throw IgError(msg("browser.no_internet"))
            }
        }
        if (ok == null) listener?.log("warn", msg("browser.slow"))
        // Sayfa başlayınca betik hazır olsun diye çok kısa bekle (eski WebView'lerde betik onPageStarted'da eklenir).
        delay(300)
        lastUrl = webView.url ?: lastUrl
        if (mainFrameStatus != 0) mainFrameStatus else 200
    }

    override fun currentUrl(): String = lastUrl

    override suspend fun fetch(url: String, form: Map<String, String>?, timeoutMs: Long): RawResponse {
        val id = nextId.getAndIncrement()
        val deferred = CompletableDeferred<RawResponse>()
        pending[id] = deferred
        val formJs = if (form == null) "null" else JSONObject(form as Map<*, *>).toString()
        val js = "(function(){ if (!window.__igFetch) { ${pageScript(if (modern) "IgBridge.postMessage(s)" else "IgBridgeLegacy.post(s)")} }" +
            " window.__igFetch($id, ${JSONObject.quote(url)}, $formJs, '${Insta.APP_ID}', '${Insta.ASBD_ID}', $timeoutMs); return true; })()"
        eval(js)
        val r = withTimeoutOrNull(timeoutMs + 15_000) { deferred.await() }
        pending.remove(id)
        return r ?: RawResponse(0, url, "", "timeout")
    }

    override suspend fun embeddedJson(): List<Any> {
        val raw = evalString(EMBEDDED_JS, 20_000) ?: return emptyList()
        val arr = try { JSONArray(raw) } catch (e: Exception) { return emptyList() }
        return (0 until arr.length()).mapNotNull { Insta.parseJsonText(arr.optString(it, "")) }
    }

    override suspend fun pageHtml(): String = evalString("document.documentElement.outerHTML", 20_000) ?: ""

    override fun setCapture(handler: ((String, Any) -> Unit)?) { captureHandler = handler }

    override suspend fun scrollDown() { eval(SCROLL_JS) }

    override suspend fun showVerify(show: Boolean) = withContext(Dispatchers.Main) {
        if (show) overlay.showOverlay("verify", null) else overlay.hideOverlay()
    }

    override suspend fun loginSession(): Boolean {
        val already = loggedIn()
        navigate("$baseUrl/accounts/login/")
        listener?.log(if (already) "ok" else "info", msg(if (already) "browser.login_already" else "browser.login_open_mobile"))
        val done = CompletableDeferred<Unit>()
        withContext(Dispatchers.Main) { overlay.showOverlay("login") { done.complete(Unit) } }
        try {
            var seen = 0L
            while (!done.isCompleted && !cancel.get()) {
                delay(1000)
                val path = Insta.pathOf(lastUrl)
                val busy = Insta.CHALLENGE_PATHS.any { it in path } || Insta.LOGIN_PATHS.any { it in path } || "two_factor" in path
                if (loggedIn() && !busy) {
                    if (seen == 0L) seen = System.currentTimeMillis()
                    else if (System.currentTimeMillis() - seen > 3000) {
                        listener?.log("ok", msg("browser.login_success"))
                        break
                    }
                } else seen = 0L
            }
        } finally {
            withContext(Dispatchers.Main) {
                overlay.hideOverlay()
                CookieManager.getInstance().flush()
            }
        }
        return loggedIn()
    }

    companion object {
        private val ORIGINS = setOf("https://www.instagram.com", "https://instagram.com", "https://*.instagram.com")

        /** WebView kimliğinden ("; wv", "Version/4.0") arındırılmış Chrome kimliği. */
        fun chromeUserAgent(default: String): String =
            default.replace("; wv)", ")").replace(Regex("\\s*Version/\\d+(\\.\\d+)*"), "")

        /**
         * Windows sürümüyle aynı kimlik: masaüstü Chrome. İstekler masaüstü sitenin uygulama kimliğiyle (X-IG-App-ID)
         * gittiği için mobil kimlikle birlikte tutarsız görünüyordu.
         */
        fun desktopUserAgent(default: String): String {
            val major = Regex("Chrome/(\\d+)").find(default)?.groupValues?.get(1) ?: "130"
            return "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) " +
                "Chrome/$major.0.0.0 Safari/537.36"
        }

        fun pageScript(send: String): String = """
(function(){
  if (window.__igHooked) return; window.__igHooked = true;
  var post = function(s){ try { $send; } catch(e){} };
  try {
    var mp = HTMLMediaElement.prototype.play;
    HTMLMediaElement.prototype.play = function(){ try { this.muted = true; } catch(e){} return mp.apply(this, arguments); };
  } catch(e){}
  var of = window.fetch;
  var origin = location.origin;
  var want = function(u){ u = String(u || ''); if (u.indexOf('/') === 0) u = origin + u;
    return u.indexOf(origin) === 0 && (u.indexOf('/graphql') >= 0 || u.indexOf('/api/v1/') >= 0); };
  var cap = function(url, text){ if (text && text.length) post('C\n' + String(url) + '\n' + text); };
  window.__igFetch = function(id, url, form, appId, asbd, timeoutMs){
    var ctl = new AbortController();
    var timer = setTimeout(function(){ ctl.abort(); }, timeoutMs);
    var m = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    var claim = '0';
    try { claim = sessionStorage.getItem('www-claim-v2') || '0'; } catch (e) {}
    var headers = { 'Accept': '*/*', 'X-IG-App-ID': appId, 'X-ASBD-ID': asbd, 'X-IG-WWW-Claim': claim,
      'X-Requested-With': 'XMLHttpRequest', 'X-CSRFToken': m ? decodeURIComponent(m[1]) : '' };
    var opts = { method: form ? 'POST' : 'GET', headers: headers, credentials: 'include', signal: ctl.signal, redirect: 'follow' };
    if (form) { headers['Content-Type'] = 'application/x-www-form-urlencoded'; opts.body = new URLSearchParams(form).toString(); }
    var done = function(o){ clearTimeout(timer); post('R\n' + id + '\n' + JSON.stringify(o)); };
    (of || fetch).call(window, url, opts).then(function(r){
      var nc = r.headers.get('x-ig-set-www-claim');
      if (nc) { try { sessionStorage.setItem('www-claim-v2', nc); } catch (e) {} }
      return r.text().then(function(t){ done({ status: r.status, url: r.url || url, text: t }); });
    }).catch(function(e){ done({ status: 0, url: url, text: '', error: String(e && e.name === 'AbortError' ? 'timeout' : e) }); });
  };
  if (of) {
    window.fetch = function(input, init){
      var url = (typeof input === 'string') ? input : ((input && input.url) || '');
      var p = of.apply(this, arguments);
      if (want(url)) p.then(function(r){ r.clone().text().then(function(t){ cap(r.url || url, t); }); }).catch(function(){});
      return p;
    };
  }
  var X = window.XMLHttpRequest && XMLHttpRequest.prototype;
  if (X) {
    var oo = X.open, os = X.send;
    X.open = function(m, u){ this.__igUrl = u; return oo.apply(this, arguments); };
    X.send = function(){
      if (want(this.__igUrl)) this.addEventListener('load', function(){
        try {
          var t = (this.responseType === '' || this.responseType === 'text') ? this.responseText
                : (this.responseType === 'json' ? JSON.stringify(this.response) : null);
          if (t) cap(this.responseURL || this.__igUrl, t);
        } catch(e){}
      });
      return os.apply(this, arguments);
    };
  }
})();
"""

        private const val EMBEDDED_JS = """
(function(){
  var out = [];
  var s = document.querySelectorAll('script[type="application/json"]');
  for (var i = 0; i < s.length; i++) {
    var t = s[i].textContent || '';
    if (t.length > 50 && (t.indexOf('username') >= 0 || t.indexOf('image_versions2') >= 0 || t.indexOf('display_url') >= 0)) out.push(t);
  }
  return JSON.stringify(out);
})()
"""

        private const val SCROLL_JS = """
(function(){
  window.scrollBy(0, 2400);
  var els = document.querySelectorAll('main, [role="main"]');
  for (var i = 0; i < els.length; i++) { var el = els[i]; if (el.scrollHeight > el.clientHeight + 50) el.scrollTop += 2400; }
  window.dispatchEvent(new Event('scroll'));
  return true;
})()
"""
    }
}
