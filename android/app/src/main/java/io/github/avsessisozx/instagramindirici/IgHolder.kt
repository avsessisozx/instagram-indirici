package io.github.avsessisozx.instagramindirici

import android.content.Context
import android.content.MutableContextWrapper
import android.view.ViewGroup
import android.webkit.WebView

/** Instagram ekranını kullanıcıya gösterme (giriş ya da doğrulama için). Ana iş parçacığında çağrılır. */
interface OverlayHost {
    fun showOverlay(mode: String, onDone: (() -> Unit)?)
    fun hideOverlay()
}

/**
 * Gizli Instagram tarayıcısı ekrana (Activity) değil uygulamaya aittir.
 *
 * Android ekranı birçok sebeple yeniden kurar (izin penceresi, tema değişimi, dil ...).
 * Tarayıcı ekranla birlikte yok olsaydı süren iş yarıda kalırdı. Burada tarayıcı bir kere
 * oluşturulur; ekran her kurulduğunda ona takılır, ekran kapanınca çıkarılır ama yaşamaya devam eder.
 */
class IgHolder private constructor(appContext: Context) : OverlayHost {
    private val ctx = MutableContextWrapper(appContext)
    val webView: WebView = WebView(ctx)
    val web: WebInstagram = WebInstagram(webView, this)

    private var ui: MainActivity? = null
    var overlayMode: String? = null; private set
    var overlayDone: (() -> Unit)? = null; private set
    /** Kullanıcı pencereyi "Gizle" ile kapattı mı? (iş hâlâ bekliyor olabilir) */
    var hiddenByUser = false; private set

    fun attach(activity: MainActivity, parent: ViewGroup, params: ViewGroup.LayoutParams) {
        ctx.baseContext = activity
        (webView.parent as? ViewGroup)?.removeView(webView)
        parent.addView(webView, params)
        ui = activity
        if (!hiddenByUser) overlayMode?.let { activity.applyOverlay(it, overlayDone) }
    }

    fun detach(activity: MainActivity) {
        if (ui !== activity) return
        ui = null
        (webView.parent as? ViewGroup)?.removeView(webView)
        ctx.baseContext = activity.applicationContext
    }

    override fun showOverlay(mode: String, onDone: (() -> Unit)?) {
        overlayMode = mode
        overlayDone = onDone
        hiddenByUser = false
        ui?.applyOverlay(mode, onDone)
    }

    override fun hideOverlay() {
        overlayMode = null
        overlayDone = null
        hiddenByUser = false
        ui?.clearOverlay()
    }

    /** Kullanıcı gizledi (simge durumuna küçülttü): pencere kapanır ama bekleyen iş sürer. */
    fun userHide() {
        if (overlayMode == null) return
        hiddenByUser = true
        ui?.clearOverlay()
    }

    /** Arayüzdeki "Doğrulamayı aç" / "Giriş ekranını aç" düğmesi. */
    fun reshow() {
        val mode = overlayMode ?: return
        hiddenByUser = false
        ui?.applyOverlay(mode, overlayDone)
    }

    companion object {
        @Volatile private var instance: IgHolder? = null

        /** Ana iş parçacığında çağrılır. */
        fun get(context: Context): IgHolder =
            instance ?: IgHolder(context.applicationContext).also {
                instance = it
                Core.web = it.web
            }
    }
}
