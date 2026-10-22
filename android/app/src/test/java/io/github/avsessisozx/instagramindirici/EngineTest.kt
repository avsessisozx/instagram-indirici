package io.github.avsessisozx.instagramindirici

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Before
import org.junit.FixMethodOrder
import org.junit.Test
import org.junit.runners.MethodSorters
import java.io.File
import java.net.URL
import java.nio.file.Files

/**
 * Android sürümünün bütün Instagram mantığını (Insta + Engine) telefonsuz dener: Windows sürümünün
 * testlerindeki aynı sahte Instagram sunucusuna (tests/fake_instagram.py) gerçek HTTP ile bağlanır,
 * dosyaları düz klasöre yazar. Sunucu adresi FAKE_INSTAGRAM ortam değişkeninden gelir; yoksa atlanır.
 */
@FixMethodOrder(MethodSorters.NAME_ASCENDING)
class EngineTest {
    private val base = System.getProperty("fakeBase") ?: ""
    private lateinit var out: File
    private lateinit var historyFile: File
    private lateinit var web: JvmWeb

    private val profile = "sahte.kisi"
    private val totalPosts = 40
    private val page = 12

    @Before
    fun setUp() {
        assumeTrue("FAKE_INSTAGRAM yok, atlandı", base.isNotEmpty())
        control("/__sifirla")
        out = Files.createTempDirectory("ig-out").toFile()
        historyFile = File(Files.createTempDirectory("ig-hist").toFile(), "gecmis.json")
        web = JvmWeb(base)
        TestHooks.baseUrl = base
        TestHooks.delayMs = 0L..20L
        TestHooks.pageDelayMs = 0L..10L
        TestHooks.muxer = null
        TestHooks.backoffSeconds = listOf(1L, 2L)
        TestHooks.breakEvery = 0
        TestHooks.idleSeconds = 3.0
        TestHooks.resumeWaitSeconds = 6L
        TestHooks.resumeRounds = 3
        TestHooks.verifyCalmMs = 1000L
        TestHooks.serverErrorWaitSeconds = 0.0
        TestHooks.retryWaitMs = 300L
        TestHooks.otherRounds = 1
    }

    @After
    fun tearDown() {
        TestHooks.muxer = null
        if (::out.isInitialized) out.deleteRecursively()
        if (::historyFile.isInitialized) historyFile.parentFile?.deleteRecursively()
    }

    // ---------------------------------------------------------------- yardımcılar
    private fun control(path: String): JSONObject =
        JSONObject(URL(base + path).openStream().bufferedReader().use { it.readText() })

    private fun set(vararg kv: Pair<String, Any>) =
        control("/__ayar?" + kv.joinToString("&") { "${it.first}=${it.second}" })

    private fun engine(history: History? = History(historyFile)) =
        Engine({ web }, { FileSink(out) }, { history }, CoroutineScope(SupervisorJob() + Dispatchers.Default))

    private fun settings(
        content: String = "all", limit: Int = 0, stories: Boolean = true, highlights: Boolean = true,
        avatar: Boolean = true, onlyNew: Boolean = false, history: Boolean = false, autoContinue: Boolean = false,
        reels: Boolean = false, reposts: Boolean = false, tagged: Boolean = false, verifyFiles: Boolean = false,
        infoCsv: Boolean = false, captionMin: Int = 0,
    ) = SettingsValues(content = content, limit = limit, stories = stories, highlights = highlights, avatar = avatar,
        onlyNew = onlyNew, history = history, autoContinue = autoContinue, reels = reels, reposts = reposts,
        tagged = tagged, verifyFiles = verifyFiles, infoCsv = infoCsv, captionMin = captionMin)

    private fun run(e: Engine, link: String, mode: String = Engine.MODE_PROFILE, s: SettingsValues = settings(),
                    timeoutMs: Long = 120_000): JSONObject {
        assertNull(e.start(Engine.Job(link, mode, s)))
        val end = System.currentTimeMillis() + timeoutMs
        while (e.running && System.currentTimeMillis() < end) Thread.sleep(50)
        assertFalse("iş zamanında bitmedi", e.running)
        val snap = e.snapshot(0)
        if (System.getenv("TEST_VERBOSE") != null) println("${snap.optString("phase")} ${snap.opt("summary")}\n${e.logKeys()}")
        return snap
    }

    private fun login() {
        set("autologin" to 1)
        val e = engine()
        val snap = run(e, "", Engine.MODE_LOGIN)
        assertEquals("engine.login_done_ok", snap.getJSONObject("summary").getString("key"))
        set("autologin" to 0)
    }

    private fun files(): List<String> = out.walkTopDown().filter { it.isFile }
        .map { it.relativeTo(out).path.replace('\\', '/') }.sorted().toList()

    private fun feedCalls(): Int {
        val calls = control("/__durum").getJSONArray("api_calls")
        return (0 until calls.length()).count { calls.getJSONArray(it).getString(0).startsWith("/api/v1/feed/user/") }
    }

    // ---------------------------------------------------------------- giriş yapılmadan
    @Test
    fun a1_loggedOutProfileStopsAtLoginWall() {
        val e = engine()
        val snap = run(e, "https://www.instagram.com/$profile/")
        assertEquals(snap.toString(), "done", snap.getString("phase"))
        assertFalse(snap.getBoolean("logged_in"))
        assertTrue(snap.getBoolean("login_wall"))
        assertEquals(2 * page, snap.getInt("found_posts"))
        val f = files()
        assertTrue(f.all { it.startsWith("$profile/") })
        assertTrue(f.any { it.startsWith("$profile/Profil Fotoğrafı/") })
        // Izgarada görünen klipler (product_type = clips) Reels/ klasörüne iner.
        assertTrue(f.any { it.startsWith("$profile/Reels/") })
        assertTrue(f.filter { it.startsWith("$profile/Reels/") }.all { it.endsWith(".mp4") })
        assertTrue(f.any { it.startsWith("$profile/Gönderiler/Fotoğraflar/") && it.endsWith(".webp") })
        assertTrue(f.none { it.endsWith(".part") })
        assertTrue("engine.stories_skipped_login" in e.logKeys())
        assertEquals(1, snap.getInt("posts_unavailable"))
        // En iyi video seçildi
        val video = File(out, f.first { it.endsWith(".mp4") }).readText(Charsets.ISO_8859_1)
        assertTrue("_1080.mp4" in video)
    }

    @Test
    fun a2_notFound() {
        val snap = run(engine(), "@yok.boyle.biri")
        assertEquals("error", snap.getString("phase"))
        assertEquals("browser.user_not_found", snap.getJSONObject("summary").getString("key"))
    }

    @Test
    fun a3_singlePostLoggedOut() {
        val snap = run(engine(), "https://www.instagram.com/p/${code(2)}/?igsh=x", Engine.MODE_SINGLE)
        assertEquals(snap.toString(), "done", snap.getString("phase"))
        val f = files()
        assertEquals(f.toString(), 3, f.size)
        assertTrue(f.any { it.startsWith("$profile/Gönderiler/Albümler/") && it.endsWith("_02.mp4") })
    }

    @Test
    fun a4_pageRateLimitedIsRetried() {
        set("page_429_once" to 1)
        val e = engine()
        val snap = run(e, profile, s = settings(limit = 3, stories = false, highlights = false, avatar = false))
        assertEquals(snap.toString(), "done", snap.getString("phase"))
        assertTrue("browser.page_refused" in e.logKeys())
        assertEquals(3, snap.getInt("found_posts"))
    }

    // ---------------------------------------------------------------- giriş yapılmış
    @Test
    fun b1_fullProfileWithRateLimitAndChallenge() {
        login()
        // İlk liste isteğindeki ret b6'da denenir; burada doğrulama (challenge) akışı.
        set("challenge_once" to 1)
        val e = engine()
        val snap = run(e, profile)
        assertEquals(snap.toString(), "done", snap.getString("phase"))
        assertTrue(snap.getBoolean("logged_in"))
        val keys = e.logKeys()
        assertTrue("browser.verify_done" in keys)
        assertTrue(web.verifyShown > 0)
        assertEquals(totalPosts + 1, snap.getInt("found_posts"))
        val f = files()
        val p = "$profile/"
        assertTrue(f.any { it.startsWith(p + "Gönderiler/Albümler/") })
        assertTrue(f.any { it.startsWith(p + "Reels/") })
        assertEquals(3, f.count { it.startsWith(p + "Hikayeler/") })
        assertEquals(2, f.count { it.startsWith(p + "Öne Çıkanlar/Tatil/") })
        assertEquals(3, f.count { it.startsWith(p + "Öne Çıkanlar/İş Proje/") })
        assertTrue(p + "Profil Fotoğrafı/9988776655.jpg" in f)
        assertTrue(f.none { it.count { c -> c == '/' } == 1 })
        assertEquals(0, snap.getInt("files_failed"))
    }

    @Test
    fun b2_restBrokenUsesGraphqlThenScroll() {
        login()
        set("break_rest" to 1)
        val e = engine()
        var snap = run(e, profile, s = settings(stories = false, highlights = false, avatar = false))
        assertEquals("done", snap.getString("phase"))
        assertEquals(totalPosts + 1, snap.getInt("found_posts"))
        assertTrue("browser.strategy_failed" in e.logKeys())

        out.deleteRecursively(); out.mkdirs()
        set("break_graphql" to 1, "break_profile_api" to 1)
        val e2 = engine()
        snap = run(e2, profile, s = settings(stories = false, highlights = false, avatar = false))
        assertEquals("done", snap.getString("phase"))
        assertTrue("browser.scrolling" in e2.logKeys())
        assertEquals(totalPosts + 1, snap.getInt("found_posts"))
    }

    @Test
    fun b3_privateAndEmpty() {
        login()
        var e = engine()
        var snap = run(e, "gizli.hesap")
        assertEquals("done", snap.getString("phase"))
        assertTrue("engine.private_logged_in" in e.noteKeys())
        assertEquals(1, files().size)
        e = engine()
        snap = run(e, "bos.hesap", s = settings(avatar = false))
        assertEquals("engine.empty", snap.getJSONObject("summary").getString("key"))
    }

    @Test
    fun b4_singleItems() {
        login()
        var snap = run(engine(), "https://www.instagram.com/reel/${code(1)}/", Engine.MODE_SINGLE)
        assertEquals("done", snap.getString("phase"))
        assertEquals(1, files().size)
        out.deleteRecursively(); out.mkdirs()
        snap = run(engine(), "https://www.instagram.com/stories/highlights/17900000000000002/", Engine.MODE_SINGLE)
        assertEquals("done", snap.getString("phase"))
        assertEquals(3, files().count { "/Öne Çıkanlar/İş Proje/" in it })
        out.deleteRecursively(); out.mkdirs()
        snap = run(engine(), "https://www.instagram.com/stories/$profile/", Engine.MODE_SINGLE)
        assertEquals("done", snap.getString("phase"))
        assertEquals(3, files().count { "/Hikayeler/" in it })
    }

    @Test
    fun b5_videoOnlyWithLimit() {
        login()
        val e = engine()
        val snap = run(e, profile, s = settings(content = "video", limit = 12, stories = false, highlights = false, avatar = false))
        assertEquals("done", snap.getString("phase"))
        assertEquals(12, snap.getInt("found_posts"))
        assertTrue(files().isNotEmpty() && files().all { it.endsWith(".mp4") })
    }

    /** Telefonda görülen durum: REST liste ve öne çıkan listesi hep reddediliyor. Beklemeden diğer yollar. */
    @Test
    fun b6_refusedListsDoNotWait() {
        login()
        set("rest_refused" to 1, "highlights_tray_refused" to 1)
        TestHooks.backoffSeconds = listOf(30L, 90L)
        try {
            val e = engine()
            val started = System.currentTimeMillis()
            val snap = run(e, profile, s = settings(autoContinue = true), timeoutMs = 60_000)
            val took = System.currentTimeMillis() - started
            assertEquals(snap.toString(), "done", snap.getString("phase"))
            val keys = e.logKeys()
            assertFalse(keys.toString(), "browser.slow_down" in keys)
            assertFalse(keys.toString(), "engine.auto_wait" in keys)
            assertTrue(keys.toString(), "browser.highlights_from_page" in keys)
            assertEquals(1, feedCalls())
            assertEquals(totalPosts + 1, snap.getInt("found_posts"))
            // (Öne çıkanların sayfadan okunması gerçek tarayıcı ister; uçtan uca testte denenir.)
            assertEquals(3, files().count { it.startsWith("$profile/Hikayeler/") })
            assertTrue("sürdü: $took ms", took < 30_000)
        } finally {
            TestHooks.backoffSeconds = listOf(1L, 2L)
        }
    }

    @Test
    fun c1_sessionKilledThenContinueFromHistory() {
        login()
        set("session_kill_after" to 2)
        val hist = History(historyFile)
        var e = engine(hist)
        var snap = run(e, profile, s = settings(stories = false, highlights = false, avatar = false, history = true))
        assertEquals(snap.toString(), "done", snap.getString("phase"))
        assertTrue(snap.getBoolean("login_wall"))
        assertTrue("engine.session_expired_partial" in e.noteKeys())
        assertEquals(2 * page, snap.getInt("found_posts"))
        hist.flush()
        val firstNames = files().map { it.substringAfterLast('/') }.toSet()

        // Başka hesapla (burada aynı sahte hesap) tekrar giriş, başka klasöre: kaldığı yerden devam.
        control("/__sifirla")
        web = JvmWeb(base)
        login()
        val out1 = out
        out = Files.createTempDirectory("ig-out2").toFile()
        e = engine(History(historyFile))
        snap = run(e, profile, s = settings(stories = false, highlights = false, avatar = false, history = true))
        assertEquals(snap.toString(), "done", snap.getString("phase"))
        val keys = e.logKeys()
        assertTrue(keys.toString(), "engine.resume_found" in keys)
        assertTrue(keys.toString(), "browser.resuming" in keys)
        assertEquals(3, feedCalls())
        assertEquals(page - 1, snap.getInt("posts_known"))
        assertTrue(files().map { it.substringAfterLast('/') }.none { it in firstNames })
        out1.deleteRecursively()
    }

    @Test
    fun c2_rateLimitedWaitsAndContinues() {
        login()
        set("rate_after" to 2, "rate_window" to 9.0)
        val e = engine()
        val snap = run(e, profile, s = settings(stories = false, highlights = false, avatar = false, history = true,
            autoContinue = true), timeoutMs = 180_000)
        assertEquals(snap.toString(), "done", snap.getString("phase"))
        val keys = e.logKeys()
        assertTrue(keys.toString(), "engine.auto_wait" in keys)
        assertTrue("engine.auto_resume" in keys)
        assertEquals(totalPosts + 1, snap.getInt("found_posts"))
        assertTrue(snap.getInt("files_done") >= 50)
        assertFalse(snap.getBoolean("rate_limited"))
    }

    @Test
    fun c3_onlyNewAfterCompleteScan() {
        login()
        val hist = History(historyFile)
        var snap = run(engine(hist), profile, s = settings(stories = false, highlights = false, avatar = false))
        assertEquals("done", snap.getString("phase"))
        assertTrue(hist.completeNewest("1001", "all") > 0)
        control("/__sifirla")
        val e = engine(hist)
        snap = run(e, profile, s = settings(stories = false, highlights = false, avatar = false, onlyNew = true))
        assertEquals("done", snap.getString("phase"))
        assertTrue("engine.up_to_date" in e.logKeys())
        assertEquals(0, snap.getInt("files_done"))
        assertEquals(1, feedCalls())
    }

    @Test
    fun c4_stop() {
        login()
        set("slow_cdn" to 0.5)
        val e = engine()
        assertNull(e.start(Engine.Job(profile, Engine.MODE_PROFILE, settings(stories = false, highlights = false))))
        val end = System.currentTimeMillis() + 60_000
        while (e.filesDone.get() < 2 && System.currentTimeMillis() < end) Thread.sleep(50)
        e.stop()
        val end2 = System.currentTimeMillis() + 30_000
        while (e.running && System.currentTimeMillis() < end2) Thread.sleep(50)
        assertFalse(e.running)
        assertEquals("stopped", e.snapshot(0).getString("phase"))
        assertTrue(files().none { it.endsWith(".part") })
    }

    // ---------------------------------------------------------------- 1.7: sekmeler, açıklama, eksik dosya, yedek yollar
    @Test
    fun e1_reelsTabIsCollectedSeparately() {
        login()
        val e = engine()
        val snap = run(e, profile, s = settings(stories = false, highlights = false, avatar = false, reels = true))
        assertEquals(snap.toString(), "done", snap.getString("phase"))
        val f = files()
        val reels = f.filter { it.startsWith("$profile/Reels/") }
        // ızgaradaki 8 klip + yalnız Reels sekmesinde olan 5 klip (videosu olmayan 1 klip indirilemez)
        assertEquals(reels.toString(), 13, reels.size)
        assertEquals(13, reels.map { it.substringAfterLast('/') }.toSet().size)
        assertEquals(14, snap.getJSONObject("tabs").getJSONObject("reels").getInt("found"))
    }

    @Test
    fun e2_reelsFallBackToTabPageScroll() {
        login()
        set("clips_refused" to 1)
        val e = engine()
        val snap = run(e, profile, s = settings(posts = false, stories = false, highlights = false, avatar = false, reels = true))
        assertEquals(snap.toString(), "done", snap.getString("phase"))
        assertTrue("browser.scrolling" in e.logKeys())
        assertTrue(files().count { it.startsWith("$profile/Reels/") } >= 5)
    }

    @Test
    fun e3_longCaptionIsSavedNextToMedia() {
        login()
        val e = engine()
        run(e, profile, s = settings(stories = false, highlights = false, avatar = false, reels = true, captionMin = 100))
        val f = files()
        val txt = f.filter { it.endsWith(".txt") }
        assertEquals(txt.toString(), 1, txt.size)
        assertTrue(txt[0].startsWith("$profile/Reels/"))
        assertTrue(f.contains(txt[0].removeSuffix(".txt") + ".mp4"))
        assertTrue(File(out, txt[0]).readText().contains("uzun açıklama"))
    }

    @Test
    fun e4_missingFilesAreRedownloadedEvenIfHistoryKnowsThem() {
        login()
        val hist = History(historyFile)
        val first = run(engine(hist), profile, s = settings(stories = false, highlights = false, avatar = false, history = true))
        assertEquals("done", first.getString("phase"))
        hist.flush()
        val before = files()
        val victims = before.filter { it.startsWith("$profile/Reels/") }.take(2)
        victims.forEach { File(out, it).delete() }
        val e = engine(hist)
        val snap = run(e, profile, s = settings(stories = false, highlights = false, avatar = false, history = true,
            onlyNew = true, verifyFiles = true))
        assertEquals(snap.toString(), "done", snap.getString("phase"))
        assertEquals(2, snap.getInt("posts_repaired"))
        assertEquals(before, files())
    }

    @Test
    fun e5_highlightsFallBackToTheirOwnPageWhen500() {
        login()
        TestHooks.serverErrorWaitSeconds = 0.0
        set("highlights_500" to 1)
        val e = engine()
        val snap = run(e, profile, s = settings(posts = false, stories = false, avatar = false))
        assertEquals(snap.toString(), "done", snap.getString("phase"))
        assertTrue("browser.highlight_from_page" in e.logKeys())
        assertEquals(2, files().count { it.startsWith("$profile/Öne Çıkanlar/Tatil/") })
    }

    @Test
    fun e6_infoCsvIsWrittenOncePerPost() {
        login()
        val e = engine()
        run(e, profile, s = settings(stories = false, highlights = false, avatar = false, limit = 5, infoCsv = true))
        val csv = File(out, "$profile/bilgi.csv").readText()
        assertTrue(csv.startsWith("\uFEFFkod;tür;tarih"))
        assertEquals(6, csv.trim().lines().size)
    }

    @Test
    fun e7_pauseAndResume() {
        login()
        set("slow_cdn" to 0.3)
        val e = engine()
        assertNull(e.start(Engine.Job(profile, Engine.MODE_PROFILE, settings(stories = false, highlights = false, limit = 6))))
        val end = System.currentTimeMillis() + 60_000
        while (e.filesDone.get() < 1 && System.currentTimeMillis() < end) Thread.sleep(50)
        e.pause()
        Thread.sleep(1500)
        val frozen = e.filesDone.get()
        Thread.sleep(1500)
        assertTrue(e.snapshot(0).getBoolean("paused"))
        assertTrue(e.filesDone.get() <= frozen + 3)
        e.resume()
        val end2 = System.currentTimeMillis() + 90_000
        while (e.running && System.currentTimeMillis() < end2) Thread.sleep(50)
        assertEquals("done", e.snapshot(0).getString("phase"))
    }

    // ---------------------------------------------------------------- sahte sunucunun gönderi kodları
    /** tests/fake_instagram.py: i. gönderinin kimliği 3400000000000000000 + 1001*1000 + i (i=1 video, i=2 albüm). */
    private fun code(i: Int): String = Links.shortcodeFromId((3400000000000000000L + 1001L * 1000L + i).toString())

    // ---------------------------------------------------------------- yüksek kalite (DASH)
    /** Gerçek MediaMuxer telefonda çalışır; burada görüntü ve ses dosyalarını art arda yazan sahte birleştirici kullanılır. */
    private val fakeMuxer = object : Muxer {
        override fun mux(video: File, audio: File?, out: File, cancel: java.util.concurrent.atomic.AtomicBoolean): String? {
            out.writeBytes("MUX".toByteArray() + video.readBytes() + (audio?.readBytes() ?: ByteArray(0)))
            return null
        }
    }

    @Test
    fun d1_videoUsesDashStreams() {
        login()
        set("dash" to 1)
        TestHooks.muxer = fakeMuxer
        val e = engine()
        val snap = run(e, "https://www.instagram.com/reel/${code(1)}/", Engine.MODE_SINGLE)
        assertEquals(snap.toString(), "done", snap.getString("phase"))
        val f = files()
        assertEquals(f.toString(), 1, f.size)
        val text = File(out, f[0]).readText(Charsets.ISO_8859_1)
        assertTrue(text.startsWith("MUX"))
        assertTrue("_dashv1920.mp4" in text)      // en yüksek çözünürlüklü görüntü akışı
        assertTrue("_dasha.mp4" in text)          // en yüksek bit hızlı ses akışı
        assertTrue("_dasha_low.mp4" !in text)
        assertTrue("engine.quality_dash" in e.logKeys())
        assertTrue(f.none { it.endsWith(".part") })
        assertTrue(File(System.getProperty("java.io.tmpdir"), "ii-dash").listFiles().orEmpty().isEmpty())
    }

    @Test
    fun d2_dashFailureFallsBackToPlainVersion() {
        login()
        set("dash" to 1)
        TestHooks.muxer = object : Muxer {
            override fun mux(video: File, audio: File?, out: File, cancel: java.util.concurrent.atomic.AtomicBoolean) = "boom"
        }
        val e = engine()
        val snap = run(e, "https://www.instagram.com/reel/${code(1)}/", Engine.MODE_SINGLE)
        assertEquals(snap.toString(), "done", snap.getString("phase"))
        val f = files()
        assertEquals(f.toString(), 1, f.size)
        assertTrue("_1080.mp4" in File(out, f[0]).readText(Charsets.ISO_8859_1))   // normal sürüm indi
        assertTrue("engine.quality_fallback" in e.logKeys())
    }

    @Test
    fun d4_dashPrefersCodecsThePhoneCanMux() {
        val mpd = """<MPD><Period>
            <AdaptationSet contentType="video">
              <Representation mimeType="video/mp4" codecs="vp09.00.40.08" width="1080" height="1920" bandwidth="5000000"><BaseURL>https://x/vp9.mp4</BaseURL></Representation>
              <Representation mimeType="video/mp4" codecs="avc1.640028" width="1080" height="1920" bandwidth="4200000"><BaseURL>https://x/avc.mp4</BaseURL></Representation>
            </AdaptationSet>
            <AdaptationSet contentType="audio">
              <Representation mimeType="audio/mp4" codecs="opus" bandwidth="128000"><BaseURL>https://x/opus.mp4</BaseURL></Representation>
              <Representation mimeType="audio/mp4" codecs="mp4a.40.2" bandwidth="72000"><BaseURL>https://x/aac.mp4</BaseURL></Representation>
            </AdaptationSet></Period></MPD>"""
        val choice = Media.dashChoice(JSONObject().put("video_dash_manifest", mpd))!!
        assertEquals("https://x/avc.mp4", choice.video.url)
        assertEquals("https://x/aac.mp4", choice.audio!!.url)
    }

    @Test
    fun d5_dashPicksHigherResolutionEvenIfVp9() {
        val mpd = """<MPD><Period>
            <AdaptationSet contentType="video">
              <Representation mimeType="video/mp4" codecs="vp09.00.40.08" width="1080" height="1920" bandwidth="2400000"><BaseURL>https://x/vp9_1080.mp4</BaseURL></Representation>
              <Representation mimeType="video/mp4" codecs="avc1.64001f" width="720" height="1280" bandwidth="1400000"><BaseURL>https://x/avc_720.mp4</BaseURL></Representation>
            </AdaptationSet></Period></MPD>"""
        assertEquals("https://x/vp9_1080.mp4", Media.dashChoice(JSONObject().put("video_dash_manifest", mpd))!!.video.url)
    }

    @Test
    fun d3_dashParsingPicksBestStreams() {
        val mpd = """<?xml version="1.0"?><MPD xmlns="urn:mpeg:dash:schema:mpd:2011"><Period>
            <AdaptationSet contentType="video">
              <Representation mimeType="video/mp4" width="720" height="1280" bandwidth="1360000"><BaseURL>https://x/v720.mp4?a=1&amp;b=2</BaseURL></Representation>
              <Representation mimeType="video/mp4" width="1080" height="1920" bandwidth="4200000"><BaseURL>https://x/v1080.mp4</BaseURL></Representation>
            </AdaptationSet>
            <AdaptationSet contentType="audio">
              <Representation mimeType="audio/mp4" bandwidth="48000"><BaseURL>https://x/a48.mp4</BaseURL></Representation>
              <Representation mimeType="audio/mp4" bandwidth="72000"><BaseURL>https://x/a72.mp4</BaseURL></Representation>
            </AdaptationSet></Period></MPD>"""
        val item = JSONObject().put("video_dash_manifest", mpd)
        val choice = Media.dashChoice(item)!!
        assertEquals(1920L, choice.video.height)
        assertEquals("https://x/v1080.mp4", choice.video.url)
        assertEquals("https://x/a72.mp4", choice.audio!!.url)
        assertEquals("https://x/v720.mp4?a=1&b=2", Media.dashStreams(item).first.first { it.height == 1280L }.url)

        val segmented = JSONObject().put("video_dash_manifest",
            """<MPD><Period><AdaptationSet contentType="video"><SegmentTemplate media="s.m4s"/>
               <Representation width="1080" height="1920" bandwidth="1"><BaseURL>https://x/y.mp4</BaseURL></Representation>
               </AdaptationSet></Period></MPD>""")
        assertNull(Media.dashChoice(segmented))
        assertNull(Media.dashChoice(JSONObject().put("video_dash_manifest", "<MPD><broken")))
        assertNull(Media.dashChoice(JSONObject()))

        // normal sürümler 720p ile sınırlıysa 1080p akış seçilir; tersi olursa normal sürüm kalır
        fun videoItem(plainHeight: Int) = JSONObject().put("pk", "1").put("code", "AbC").put("media_type", 2)
            .put("taken_at", 1760000000)
            .put("video_versions", org.json.JSONArray().put(JSONObject().put("width", plainHeight * 9 / 16).put("height", plainHeight)
                .put("url", "https://x/plain.mp4")))
            .put("video_dash_manifest", mpd)
        assertEquals(1920L, Media.buildPost(videoItem(1280), "all").files[0].dash!!.video.height)
        assertNull(Media.buildPost(videoItem(2160), "all").files[0].dash)
    }
}
