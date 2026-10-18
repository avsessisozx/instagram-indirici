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
        TestHooks.backoffSeconds = listOf(1L, 2L)
        TestHooks.breakEvery = 0
        TestHooks.idleSeconds = 3.0
        TestHooks.resumeWaitSeconds = 6L
        TestHooks.resumeRounds = 3
        TestHooks.verifyCalmMs = 1000L
    }

    @After
    fun tearDown() {
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
    ) = SettingsValues(content = content, limit = limit, stories = stories, highlights = highlights, avatar = avatar,
        onlyNew = onlyNew, history = history, autoContinue = autoContinue)

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
        assertTrue(f.filter { it.startsWith("$profile/Videolar/") }.all { it.endsWith(".mp4") })
        assertTrue(f.any { it.startsWith("$profile/Fotoğraflar/") && it.endsWith(".webp") })
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
        assertTrue(f.any { it.startsWith("$profile/Videolar/") && it.endsWith("_02.mp4") })
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
        set("rate_limit_once" to 1, "challenge_once" to 1)
        val e = engine()
        val snap = run(e, profile)
        assertEquals(snap.toString(), "done", snap.getString("phase"))
        assertTrue(snap.getBoolean("logged_in"))
        val keys = e.logKeys()
        assertTrue("browser.slow_down" in keys)
        assertTrue("browser.verify_done" in keys)
        assertTrue(web.verifyShown > 0)
        assertEquals(totalPosts + 1, snap.getInt("found_posts"))
        val f = files()
        val p = "$profile/"
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

    // ---------------------------------------------------------------- sahte sunucunun gönderi kodları
    /** tests/fake_instagram.py: i. gönderinin kimliği 3400000000000000000 + 1001*1000 + i (i=1 video, i=2 albüm). */
    private fun code(i: Int): String = Links.shortcodeFromId((3400000000000000000L + 1001L * 1000L + i).toString())
}
