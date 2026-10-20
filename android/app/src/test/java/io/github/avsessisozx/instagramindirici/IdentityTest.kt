package io.github.avsessisozx.instagramindirici

import org.junit.Test
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue

class IdentityTest {
    @Test fun desktopUserAgentLooksLikeWindows() {
        assertTrue(Identity.userAgentFor(Identity.DESKTOP, "dev").contains("Windows NT 10.0"))
    }

    @Test fun phoneDropsWebViewMarkers() {
        val wv = "Mozilla/5.0 (Linux; Android 13; M2007J3SY; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/126.0.0.0 Mobile Safari/537.36"
        val ua = Identity.userAgentFor(Identity.PHONE, wv)
        assertTrue(!ua.contains("wv") && !ua.contains("Version/4.0") && ua.contains("Chrome/126"))
    }

    @Test fun tabletHasNoMobileMarker() {
        val ua = Identity.userAgentFor(Identity.TABLET, "Chrome/126.0.0.0")
        assertTrue(ua.contains("Android") && !ua.contains("Mobile") && ua.contains("Chrome/126"))
    }

    @Test fun unknownIdentityFallsBackToDesktop() {
        assertTrue(Identity.userAgentFor("bogus", "dev").contains("Windows NT"))
    }

    @Test fun carefulModesAreSlowerThanNormal() {
        val n = Pacing.forSpeed(Pacing.NORMAL)
        val c = Pacing.forSpeed(Pacing.CAREFUL)
        val v = Pacing.forSpeed(Pacing.VERY_CAREFUL)
        assertTrue(c.pageDelayMs.first > n.pageDelayMs.first && v.pageDelayMs.first > c.pageDelayMs.first)
        assertTrue(v.breakEvery < c.breakEvery && c.breakEvery < n.breakEvery)
    }

    @Test fun unknownSpeedIsNormal() {
        assertEquals(Pacing.forSpeed(Pacing.NORMAL), Pacing.forSpeed("???"))
    }
}
