package io.github.avsessisozx.instagramindirici

import android.content.Context
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.os.Build
import android.webkit.WebView

/** Rapor için cihaz ve ağ bilgisi. */
object DeviceInfo {
    fun networkType(ctx: Context): String = try {
        val cm = ctx.getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
        val caps = cm.getNetworkCapabilities(cm.activeNetwork)
        when {
            caps == null -> "yok"
            caps.hasTransport(NetworkCapabilities.TRANSPORT_VPN) -> "VPN"
            caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) -> "Wi-Fi"
            caps.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR) -> "Mobil veri"
            caps.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET) -> "Ethernet"
            else -> "diğer"
        }
    } catch (e: Exception) {
        "bilinmiyor"
    }

    fun collect(ctx: Context, identity: String, speed: String, userAgent: String): List<Pair<String, String>> {
        val webView = try { WebView.getCurrentWebViewPackage() } catch (e: Exception) { null }
        return listOf(
            "Sürüm" to Core.version(),
            "Zaman" to java.text.SimpleDateFormat("yyyy-MM-dd HH:mm:ss Z", java.util.Locale.US).format(java.util.Date()),
            "Telefon" to "${Build.MANUFACTURER} ${Build.MODEL}",
            "Android" to "${Build.VERSION.RELEASE} (SDK ${Build.VERSION.SDK_INT})",
            "WebView" to (webView?.let { "${it.packageName} ${it.versionName}" } ?: "bilinmiyor"),
            "Ağ" to networkType(ctx),
            "Kimlik" to identity,
            "Hız modu" to speed,
            "Kullanılan user agent" to userAgent,
            "Dil" to Core.lang(),
        )
    }
}
