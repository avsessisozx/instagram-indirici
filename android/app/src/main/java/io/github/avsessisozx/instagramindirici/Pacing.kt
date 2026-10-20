package io.github.avsessisozx.instagramindirici

/** Instagram'a istek gönderme hızı. Kullanıcı "hız modu" ile seçer. */
data class Pacing(
    val callDelayMs: LongRange,     // her istek arası
    val pageDelayMs: LongRange,     // gönderi listesinin sayfaları arası (ek)
    val breakEvery: Int,            // her kaç liste sayfasında bir mola
    val breakSeconds: LongRange,    // molanın süresi
) {
    companion object {
        const val NORMAL = "normal"
        const val CAREFUL = "careful"
        const val VERY_CAREFUL = "very_careful"
        val ALL = listOf(NORMAL, CAREFUL, VERY_CAREFUL)

        fun forSpeed(speed: String): Pacing = when (speed) {
            CAREFUL -> Pacing(4_000L..8_000L, 15_000L..30_000L, 3, 90L..180L)
            VERY_CAREFUL -> Pacing(6_000L..12_000L, 30_000L..60_000L, 2, 120L..240L)
            else -> Pacing(2_500L..5_000L, 8_000L..15_000L, 5, 60L..120L)
        }
    }
}
