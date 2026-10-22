package io.github.avsessisozx.instagramindirici

import android.Manifest
import android.annotation.SuppressLint
import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager
import android.content.res.Configuration
import android.graphics.Color
import android.graphics.drawable.GradientDrawable
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.MediaStore
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.webkit.CookieManager
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebStorage
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.core.content.ContextCompat
import androidx.webkit.WebViewAssetLoader
import org.json.JSONObject

/**
 * Tek ekran: üstte Türkçe arayüz (index.html), arkasında gizli Instagram tarayıcısı.
 * Instagram'a giriş yapılırken ya da doğrulama istendiğinde arkadaki tarayıcı öne, bir pencere olarak gelir.
 * Pencerenin üstünde "Gizle" (pencere küçülür, iş beklemeye devam eder; arayüzdeki düğmeyle tekrar açılır)
 * ve giriş ekranında "Kapat" (giriş ekranını tamamen kapatır) düğmeleri vardır.
 */
@SuppressLint("SetJavaScriptEnabled")
class MainActivity : Activity(), ApiHost {

    lateinit var uiWeb: WebView; private set
    private lateinit var holder: IgHolder
    private lateinit var popup: FrameLayout
    private lateinit var igPane: LinearLayout
    private lateinit var headerTitle: TextView
    private lateinit var hideButton: Button
    private lateinit var doneButton: Button

    @Volatile var uiReady = false; private set
    @Volatile var overlayMode: String? = null; private set
    private var overlayDone: (() -> Unit)? = null
    private var pendingLink: String? = null

    private val isDark: Boolean
        get() = (resources.configuration.uiMode and Configuration.UI_MODE_NIGHT_MASK) == Configuration.UI_MODE_NIGHT_YES

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        Core.init(this)
        val bg = if (isDark) Color.parseColor("#12161e") else Color.parseColor("#e9edf2")
        val surface = if (isDark) Color.parseColor("#1b212c") else Color.WHITE
        val ink = if (isDark) Color.parseColor("#e6eaf1") else Color.parseColor("#182033")

        val root = FrameLayout(this).apply {
            fitsSystemWindows = true
            setBackgroundColor(bg)
        }

        // --- Arayüz
        uiWeb = WebView(this).apply { setBackgroundColor(bg); translationZ = 1f }
        root.addView(uiWeb, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))

        // --- Instagram penceresi: normalde arayüzün arkasında (görünmez ama çalışır),
        //     doğrulama/giriş gerektiğinde arayüzün üstünde ortada açılır pencere olur.
        popup = FrameLayout(this).apply {
            setBackgroundColor(Color.TRANSPARENT)
            isClickable = true   // açıkken arkadaki arayüze dokunulmasın
            translationZ = 0f
        }
        igPane = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            background = GradientDrawable().apply { setColor(surface); cornerRadius = dp(16).toFloat() }
            clipToOutline = true
            elevation = dp(12).toFloat()
        }
        val header = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(dp(16), dp(10), dp(8), dp(10))
        }
        headerTitle = TextView(this).apply {
            setTextColor(ink)
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 15f)
        }
        hideButton = Button(this).apply { setOnClickListener { userHideOverlay() } }
        doneButton = Button(this).apply {
            setOnClickListener { overlayDone?.invoke() }   // giriş ekranını tamamen kapatır
            visibility = View.GONE
        }
        header.addView(headerTitle, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        header.addView(hideButton, LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT))
        header.addView(doneButton, LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT))
        igPane.addView(header, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))
        popup.addView(igPane, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT).apply {
            setMargins(dp(12), dp(24), dp(12), dp(24))
        })
        root.addView(popup, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
        setContentView(root)

        // Gizli Instagram tarayıcısı uygulamaya ait; ekran yeniden kurulsa da yaşar.
        holder = IgHolder.get(this)
        holder.attach(this, igPane, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f))
        setupUi()
        if (Core.webResetPending) { Core.webResetPending = false; clearCookies() }   // yeni sürüm: Instagram çerezleri de sıfırlansın
        handleIntent(intent)
        uiWeb.loadUrl(UI_URL)
        askNotificationPermissionOnce()
    }

    /** Bildirim iznini indirme sırasında değil, ilk açılışta bir kez sor. */
    private fun askNotificationPermissionOnce() {
        if (Build.VERSION.SDK_INT < 33) return
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED) return
        val prefs = getSharedPreferences("durum", MODE_PRIVATE)
        if (prefs.getBoolean("bildirim_soruldu", false)) return
        prefs.edit().putBoolean("bildirim_soruldu", true).apply()
        requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1)
    }

    private fun setupUi() {
        val loader = WebViewAssetLoader.Builder()
            .addPathHandler("/assets/", WebViewAssetLoader.AssetsPathHandler(this))
            .build()
        uiWeb.settings.javaScriptEnabled = true
        uiWeb.settings.domStorageEnabled = true
        uiWeb.settings.allowFileAccess = false
        uiWeb.settings.allowContentAccess = false
        uiWeb.addJavascriptInterface(Api(this), "InstagramApp")
        uiWeb.webViewClient = object : WebViewClient() {
            override fun shouldInterceptRequest(view: WebView, request: WebResourceRequest): WebResourceResponse? =
                loader.shouldInterceptRequest(request.url)

            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean =
                request.url.host != Uri.parse(UI_URL).host   // arayüz dışına çıkılmaz

            override fun onPageFinished(view: WebView, url: String?) {
                uiReady = true
                pendingLink?.let { deliverLink(it) }
                pendingLink = null
            }
        }
    }

    // ------------------------------------------------------------ paylaş menüsü
    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        handleIntent(intent)
    }

    private fun handleIntent(intent: Intent?) {
        if (intent?.action != Intent.ACTION_SEND) return
        val text = intent.getStringExtra(Intent.EXTRA_TEXT) ?: return
        if (uiReady) deliverLink(text) else pendingLink = text
    }

    private fun deliverLink(text: String) {
        if (Core.engine.running) return   // sürmekte olan işi bozma
        uiWeb.evaluateJavascript("window.igSetLink && window.igSetLink(${JSONObject.quote(text.trim())})", null)
    }

    /** Testler ve paylaş menüsü için: arayüzde JavaScript çalıştırır. */
    fun runUiScript(js: String, callback: ((String?) -> Unit)? = null) {
        uiWeb.evaluateJavascript(js) { callback?.invoke(it) }
    }

    // ------------------------------------------------------------ Instagram ekranını öne alma
    fun applyOverlay(mode: String, onDone: (() -> Unit)?) {
        overlayMode = mode
        overlayDone = onDone
        headerTitle.text = Core.text(if (mode == "login") "ui.overlay_login_title" else "ui.overlay_verify_title")
        hideButton.text = Core.text("ui.overlay_hide")
        doneButton.text = Core.text("ui.overlay_close")
        hideButton.visibility = View.VISIBLE                                   // her zaman küçültülebilir
        doneButton.visibility = if (onDone != null) View.VISIBLE else View.GONE // giriş ekranı tamamen kapatılabilir
        popup.setBackgroundColor(Color.argb(150, 0, 0, 0))   // arkadaki arayüz karartılır
        popup.translationZ = 10f
        holder.webView.requestFocus()
    }

    fun clearOverlay() {
        overlayMode = null
        overlayDone = null
        popup.setBackgroundColor(Color.TRANSPARENT)
        popup.translationZ = 0f
        uiWeb.requestFocus()
        uiWeb.evaluateJavascript("window.igRefresh && window.igRefresh()", null)
    }

    /** Kullanıcı pencereyi gizledi: iş beklemeye devam eder, arayüzdeki düğmeyle tekrar açılır. */
    private fun userHideOverlay() {
        holder.userHide()
    }

    /** Testler için: pencerenin "Gizle" düğmesine basar. */
    fun pressHideForTest() { hideButton.performClick() }

    /** Testler için: pencerenin "Kapat" düğmesine basar. */
    fun pressCloseForTest() { doneButton.performClick() }

    fun hideButtonVisible() = hideButton.visibility == View.VISIBLE
    fun closeButtonVisible() = doneButton.visibility == View.VISIBLE

    override fun showVerification() {
        runOnUiThread { holder.reshow() }
    }

    // ------------------------------------------------------------ ApiHost
    override fun startDownloadService() {
        runOnUiThread {
            try {
                ContextCompat.startForegroundService(this, Intent(this, DownloadService::class.java))
            } catch (e: Exception) {
                // Bildirim servisi başlamasa da indirme uygulama açıkken sürer.
            }
        }
    }

    override fun applyIdentity(identity: String) {
        runOnUiThread { holder.web.setIdentity(identity) }
    }

    override fun userAgent(): String = holder.web.userAgent

    override fun shareText(text: String) {
        runOnUiThread {
            val send = Intent(Intent.ACTION_SEND).setType("text/plain").putExtra(Intent.EXTRA_TEXT, text)
            try { startActivity(Intent.createChooser(send, null)) } catch (e: Exception) {
                Toast.makeText(this, Core.text("android.no_share_app"), Toast.LENGTH_LONG).show()
            }
        }
    }

    override fun clearCache() {
        runOnUiThread { try { holder.webView.clearCache(true) } catch (e: Exception) {} }
    }

    override fun clearCookies() {
        runOnUiThread {
            try {
                CookieManager.getInstance().removeAllCookies(null)
                CookieManager.getInstance().flush()
                // Sadece Instagram'ın verisi: arayüzün kendi kayıtları (ör. "bir daha gösterme") kalır.
                val storage = WebStorage.getInstance()
                for (origin in listOf("https://www.instagram.com", "https://instagram.com", "https://i.instagram.com")) {
                    storage.deleteOrigin(origin)
                }
                holder.webView.clearCache(true)
                holder.webView.clearHistory()
            } catch (e: Exception) {}
            uiWeb.evaluateJavascript("window.igRefresh && window.igRefresh()", null)
        }
    }

    override fun openNetworkSettings() {
        runOnUiThread {
            try { startActivity(Intent(android.provider.Settings.ACTION_WIRELESS_SETTINGS)) } catch (e: Exception) {
                Toast.makeText(this, Core.text("android.no_settings_app"), Toast.LENGTH_LONG).show()
            }
        }
    }

    override fun requestNotificationPermission() {
        // İzin ilk açılışta sorulur; indirme sırasında pencere açıp işi bölmeyiz.
    }

    override fun openResult(location: String?) {
        runOnUiThread {
            val intents = mutableListOf<Intent>()
            if (location != null && location.startsWith("content://")) {
                val uri = Uri.parse(location)
                intents += Intent(Intent.ACTION_VIEW).setDataAndType(uri, contentResolver.getType(uri))
                    .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
            }
            intents += Intent(Intent.ACTION_VIEW, MediaStore.Images.Media.EXTERNAL_CONTENT_URI)
            intents += Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_APP_GALLERY)
            for (i in intents) {
                try { startActivity(i); return@runOnUiThread } catch (e: Exception) {}
            }
            Toast.makeText(this, Core.text("android.no_gallery_app"), Toast.LENGTH_LONG).show()
        }
    }

    // ------------------------------------------------------------ yaşam döngüsü
    @Deprecated("Deprecated in Java")
    override fun onBackPressed() {
        when {
            overlayMode != null -> userHideOverlay()               // pencere küçülür, iş bekler
            Core.engine.running -> moveTaskToBack(true)             // indirme sürsün
            else -> @Suppress("DEPRECATION") super.onBackPressed()
        }
    }

    override fun onDestroy() {
        holder.detach(this)   // tarayıcı yaşamaya devam eder; iş sürer
        uiWeb.destroy()
        super.onDestroy()
    }

    private fun dp(v: Int): Int = (v * resources.displayMetrics.density).toInt()

    companion object {
        const val UI_URL = "https://appassets.androidplatform.net/assets/index.html"
    }
}
