package io.github.avsessisozx.instagramindirici

/** Instagram'a hangi cihazmış gibi görüneceğimiz (tarayıcı kimliği / user agent). Saf Kotlin: JVM'de denenir. */
object Identity {
    const val DESKTOP = "desktop"
    const val PHONE = "android"
    const val TABLET = "tablet"
    val ALL = listOf(DESKTOP, PHONE, TABLET)

    private fun chromeMajor(deviceDefault: String): String =
        Regex("Chrome/(\\d+)").find(deviceDefault)?.groupValues?.get(1) ?: "130"

    /** WebView kimliğinden ("; wv", "Version/4.0") arındırılmış, telefonun kendi Chrome kimliği. */
    fun phone(deviceDefault: String): String =
        deviceDefault.replace("; wv)", ")").replace(Regex("\\s*Version/\\d+(\\.\\d+)*"), "")

    /** Windows sürümüyle aynı kimlik: masaüstü Chrome. */
    fun desktop(deviceDefault: String): String =
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) " +
            "Chrome/${chromeMajor(deviceDefault)}.0.0.0 Safari/537.36"

    /** Android tablet Chrome kimliği (telefondan farkı: "Mobile" ibaresi yok). */
    fun tablet(deviceDefault: String): String =
        "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) " +
            "Chrome/${chromeMajor(deviceDefault)}.0.0.0 Safari/537.36"

    fun userAgentFor(identity: String, deviceDefault: String): String = when (identity) {
        PHONE -> phone(deviceDefault)
        TABLET -> tablet(deviceDefault)
        else -> desktop(deviceDefault)
    }
}
