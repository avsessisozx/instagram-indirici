package io.github.avsessisozx.instagramindirici

import android.content.ContentValues
import android.content.Intent
import android.graphics.Bitmap
import android.provider.MediaStore
import android.webkit.CookieManager
import androidx.test.core.app.ActivityScenario
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import org.json.JSONObject
import org.json.JSONTokener
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.FixMethodOrder
import org.junit.Test
import org.junit.runner.RunWith
import org.junit.runners.MethodSorters
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicReference

/**
 * Gerçek uygulama, gerçek WebView, sahte Instagram sunucusu (tests/fake_instagram.py; CI'da bilgisayarda
 * çalışır, emülatör ona 10.0.2.2 adresinden ulaşır). Testler arayüzdeki düğmelere kullanıcı gibi basar.
 * Sıra önemli: önce giriş yapılmadan (a_), sonra giriş penceresi (b_), sonra giriş yapılmış (c_).
 */
@RunWith(AndroidJUnit4::class)
@FixMethodOrder(MethodSorters.NAME_ASCENDING)
class EndToEndTest {
    private val inst = InstrumentationRegistry.getInstrumentation()
    private val ctx = inst.targetContext
    private val base = InstrumentationRegistry.getArguments().getString("fakeBase") ?: "http://10.0.2.2:8765"
    private var scenario: ActivityScenario<MainActivity>? = null
    private lateinit var out: File
    private val profile = "sahte.kisi"

    @Before
    fun setUp() {
        TestHooks.baseUrl = base
        TestHooks.delayMs = 0L..50L
        TestHooks.pageDelayMs = 0L..10L
        TestHooks.backoffSeconds = listOf(1L, 2L)
        TestHooks.breakEvery = 0
        TestHooks.idleSeconds = 4.0
        TestHooks.resumeWaitSeconds = 6L
        TestHooks.resumeRounds = 3
        TestHooks.verifyCalmMs = 1500L
        out = File(ctx.getExternalFilesDir(null), "e2e/${System.nanoTime()}")
        TestHooks.sinkFactory = { FileSink(out) }
        TestHooks.historyFile = File(out, "gecmis.json")
        control("/__sifirla")
        Core.init(ctx)
        Core.saveSettings(SettingsValues(lang = "tr", onlyNew = false, history = false, autoContinue = false, setupDone = true))
        if (android.os.Build.VERSION.SDK_INT >= 33) {
            inst.uiAutomation.grantRuntimePermission(ctx.packageName, android.Manifest.permission.POST_NOTIFICATIONS)
        }
    }

    @After
    fun tearDown() {
        if (Core.engine.running) {
            Core.engine.stop()
            waitUntil(20_000) { !Core.engine.running }
        }
        scenario?.close()
        TestHooks.sinkFactory = null
    }

    // ---------------------------------------------------------------- yardımcılar
    private fun control(path: String) {
        val c = URL(base + path).openConnection() as HttpURLConnection
        try { c.responseCode } finally { c.disconnect() }
    }

    private fun set(vararg kv: Pair<String, Any>) = control("/__ayar?" + kv.joinToString("&") { "${it.first}=${it.second}" })

    private fun waitUntil(timeoutMs: Long, cond: () -> Boolean): Boolean {
        val end = System.currentTimeMillis() + timeoutMs
        while (System.currentTimeMillis() < end) {
            if (cond()) return true
            Thread.sleep(200)
        }
        return cond()
    }

    private fun launch(intent: Intent? = null): ActivityScenario<MainActivity> {
        val s = if (intent != null) ActivityScenario.launch<MainActivity>(intent) else ActivityScenario.launch(MainActivity::class.java)
        scenario = s
        assertTrue("arayüz açılmadı", waitUntil(30_000) { activity(s)?.uiReady == true })
        assertTrue("arayüz yüklenmedi", waitUntil(15_000) { js("document.body.classList.contains('loading') === false") == "true" })
        return s
    }

    private fun activity(s: ActivityScenario<MainActivity>? = scenario): MainActivity? {
        val ref = AtomicReference<MainActivity?>()
        try { s?.onActivity { ref.set(it) } } catch (e: Exception) {}
        return ref.get()
    }

    private fun js(code: String): String? {
        val a = activity() ?: return null
        val latch = CountDownLatch(1)
        val result = AtomicReference<String?>()
        inst.runOnMainSync { a.runUiScript(code) { result.set(it); latch.countDown() } }
        latch.await(10, TimeUnit.SECONDS)
        return result.get()
    }

    private fun jsText(code: String): String =
        js(code)?.let { try { JSONTokener(it).nextValue() as? String } catch (e: Exception) { null } } ?: ""

    private fun setLink(link: String) {
        js("(function(){var l=document.getElementById('link'); l.value=${JSONObject.quote(link)}; l.dispatchEvent(new Event('input')); return true;})()")
        assertTrue("link algılanmadı", waitUntil(5_000) { js("document.getElementById('step1').classList.contains('done')") == "true" })
    }

    private fun clickId(id: String) = js("document.getElementById('$id').click(), true")

    private fun setChecked(id: String, v: Boolean) =
        js("(function(){var s=document.getElementById('$id'); s.checked=$v; s.dispatchEvent(new Event('change')); return true;})()")

    private fun setSelect(id: String, value: String) =
        js("(function(){var s=document.getElementById('$id'); s.value='$value'; s.dispatchEvent(new Event('change')); return true;})()")

    private fun startAndWait(timeoutMs: Long = 180_000, during: (() -> Unit)? = null): JSONObject {
        // Önceki iş (ör. giriş) bitmiş ve arayüz bunu görmüş olsun; yoksa düğme "Durdur" gibi davranır.
        assertTrue(waitUntil(15_000) { !Core.engine.running })
        assertTrue("arayüz hâlâ iş sürüyor sanıyor",
            waitUntil(10_000) { jsText("document.getElementById('start').textContent") == "İndirmeyi başlat" })
        val before = Core.engine.snapshot(0).optInt("log_next")
        clickId("start")
        assertTrue("indirme başlamadı: " + jsText("document.getElementById('form-error').textContent"),
            waitUntil(10_000) { Core.engine.running || Core.engine.snapshot(0).optInt("log_next") != before })
        val end = System.currentTimeMillis() + timeoutMs
        while (Core.engine.running && System.currentTimeMillis() < end) {
            during?.invoke()
            Thread.sleep(300)
        }
        assertFalse("iş zamanında bitmedi", Core.engine.running)
        Thread.sleep(1500)  // arayüz son durumu çizsin
        return Core.engine.snapshot(0)
    }

    private fun files(): List<String> =
        out.walkTopDown().filter { it.isFile && it.name != "gecmis.json" }.map { it.relativeTo(out).path }.sorted().toList()

    /** Ekran görüntüsünü İndirilenler/ii-screens klasörüne kaydeder (CI oradan alır). */
    private fun screenshot(name: String) {
        inst.waitForIdleSync()
        Thread.sleep(1500)
        val bmp: Bitmap = inst.uiAutomation.takeScreenshot() ?: return
        val values = ContentValues().apply {
            put(MediaStore.MediaColumns.DISPLAY_NAME, "$name.png")
            put(MediaStore.MediaColumns.MIME_TYPE, "image/png")
            put(MediaStore.MediaColumns.RELATIVE_PATH, "Download/ii-screens/")
        }
        val uri = ctx.contentResolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values) ?: return
        ctx.contentResolver.openOutputStream(uri)?.use { bmp.compress(Bitmap.CompressFormat.PNG, 100, it) }
    }

    private fun logs(snap: JSONObject): String {
        val arr = snap.getJSONArray("logs")
        return (0 until arr.length()).joinToString("\n") { i ->
            val l = arr.getJSONObject(i)
            l.optJSONObject("msg")?.let { Core.tr.render(Msg(it.getString("key"), toMap(it.optJSONObject("params"))), "tr") } ?: l.optString("text")
        }
    }

    private fun toMap(o: JSONObject?): Map<String, Any?> {
        if (o == null) return emptyMap()
        val m = HashMap<String, Any?>()
        for (k in o.keys()) {
            val v = o.opt(k)
            m[k] = if (v is JSONObject && v.has("key")) Msg(v.getString("key"), toMap(v.optJSONObject("params"))) else v
        }
        return m
    }

    private fun clearCookies() {
        val latch = CountDownLatch(1)
        inst.runOnMainSync { CookieManager.getInstance().removeAllCookies { latch.countDown() } }
        latch.await(10, TimeUnit.SECONDS)
        Core.saveLoginState(false)
    }

    // ---------------------------------------------------------------- giriş yapılmadan
    @Test
    fun a1_profileLoggedOutThroughUi() {
        clearCookies()
        launch()
        screenshot("01_bos_ekran")
        assertEquals("İndirmeyi başlat", jsText("document.getElementById('start').textContent"))
        setLink("https://www.instagram.com/$profile/")
        assertEquals("Profil algılandı: @$profile", jsText("document.getElementById('detect').textContent"))
        assertEquals("true", js("document.getElementById('mode-profile').checked"))
        screenshot("02_link_yapistirildi")
        var shot = false
        val snap = startAndWait {
            if (!shot && Core.engine.filesDone.get() > 2) { screenshot("03_indiriliyor"); shot = true }
        }
        screenshot("04_giris_uyarisi")
        val l = logs(snap)
        assertEquals(l, "done", snap.getString("phase"))
        assertTrue(l, snap.getBoolean("login_wall"))
        assertEquals(l, 24, snap.getInt("found_posts"))
        val f = files()
        assertTrue(f.toString(), f.all { it.startsWith("$profile/") })
        assertTrue(f.toString(), f.any { it.startsWith("$profile/Fotoğraflar/") })
        assertTrue(f.toString(), f.any { it.startsWith("$profile/Videolar/") && it.endsWith(".mp4") })
        assertTrue(f.toString(), f.any { it.startsWith("$profile/Profil Fotoğrafı/") })
        assertTrue(jsText("document.getElementById('notes').textContent").contains("giriş yapılmadan"))
        assertEquals("Giriş yapılmadı", jsText("document.getElementById('account-text').textContent"))
    }

    @Test
    fun a2_shareMenuFillsLink() {
        val intent = Intent(ctx, MainActivity::class.java).setAction(Intent.ACTION_SEND).setType("text/plain")
            .putExtra(Intent.EXTRA_TEXT, "Şu gönderiye bak https://www.instagram.com/reel/DAbc_12-x/?igsh=MWQ1 ")
        launch(intent)
        assertTrue(waitUntil(5_000) { jsText("document.getElementById('link').value").contains("instagram.com/reel/DAbc_12-x") })
        assertTrue(waitUntil(5_000) { jsText("document.getElementById('detect').textContent") == "Tek gönderi linki algılandı." })
        screenshot("05_paylas_menusu")
    }

    @Test
    fun a3_englishInterface() {
        launch()
        setSelect("lang", "en")
        assertTrue(waitUntil(5_000) { jsText("document.getElementById('start').textContent") == "Start download" })
        screenshot("06_ingilizce")
        setSelect("lang", "tr")
    }

    // ---------------------------------------------------------------- giriş penceresi
    @Test
    fun b1_loginWindowCanBeHiddenReopenedAndClosed() {
        set("autologin" to 0)
        val s = launch()
        clickId("login")
        assertTrue("giriş penceresi açılmadı", waitUntil(20_000) { activity(s)?.overlayMode == "login" })
        val a = activity(s)!!
        assertTrue("'Gizle' düğmesi yok", a.hideButtonVisible())
        assertTrue("'Kapat' düğmesi yok", a.closeButtonVisible())
        screenshot("07_giris_penceresi")

        // Gizle: pencere küçülür, giriş beklemeye devam eder; arayüzde tekrar açma düğmesi çıkar
        inst.runOnMainSync { a.pressHideForTest() }
        assertTrue(waitUntil(3_000) { activity(s)?.overlayMode == null })
        assertTrue("giriş işi durmamalı", Core.engine.running)
        assertTrue("'Giriş penceresini aç' düğmesi yok",
            waitUntil(5_000) { js("!!document.querySelector('#notes button[data-login]')") == "true" })
        screenshot("08_giris_gizlendi")
        js("document.querySelector('#notes button[data-login]').click(), true")
        assertTrue("pencere tekrar açılmadı", waitUntil(5_000) { activity(s)?.overlayMode == "login" })

        // Geri tuşu da küçültür
        inst.runOnMainSync { @Suppress("DEPRECATION") activity(s)!!.onBackPressed() }
        assertTrue(waitUntil(3_000) { activity(s)?.overlayMode == null })
        assertTrue(Core.engine.running)
        js("document.querySelector('#notes button[data-login]').click(), true")
        assertTrue(waitUntil(5_000) { activity(s)?.overlayMode == "login" })

        // Kapat: giriş ekranı tamamen kapanır
        inst.runOnMainSync { activity(s)!!.pressCloseForTest() }
        assertTrue(waitUntil(10_000) { !Core.engine.running })
        assertEquals(null, activity(s)?.overlayMode)
        assertEquals("engine.login_done", Core.engine.snapshot(0).getJSONObject("summary").getString("key"))
    }

    @Test
    fun b2_loginCompletesByItself() {
        set("autologin" to 1)
        val s = launch()
        clickId("login")
        assertTrue(waitUntil(20_000) { activity(s)?.overlayMode == "login" })
        assertTrue("giriş kendiliğinden bitmedi", waitUntil(30_000) { !Core.engine.running })
        assertEquals("engine.login_done_ok", Core.engine.snapshot(0).getJSONObject("summary").getString("key"))
        assertEquals(null, activity(s)?.overlayMode)
        assertTrue(waitUntil(5_000) { jsText("document.getElementById('account-text').textContent") == "Instagram'a giriş yapıldı" })
        screenshot("09_giris_yapildi")
    }

    // ---------------------------------------------------------------- giriş yapılmış
    @Test
    fun c1_fullProfileWithVerification() {
        set("challenge_once" to 1)
        val s = launch()
        setLink(profile)
        var sawVerify = false
        var shot = false
        val snap = startAndWait {
            if (activity(s)?.overlayMode == "verify") {
                sawVerify = true
                if (!shot) { screenshot("10_dogrulama"); shot = true }
            }
        }
        val l = logs(snap)
        assertEquals(l, "done", snap.getString("phase"))
        assertTrue("doğrulama penceresi açılmadı\n$l", sawVerify)
        assertEquals(l, 41, snap.getInt("found_posts"))
        val f = files()
        assertEquals(f.toString(), 3, f.count { it.startsWith("$profile/Hikayeler/") })
        assertEquals(f.toString(), 2, f.count { it.startsWith("$profile/Öne Çıkanlar/Tatil/") })
        assertEquals(f.toString(), 3, f.count { it.startsWith("$profile/Öne Çıkanlar/İş Proje/") })
        assertTrue(f.toString(), f.none { it.count { c -> c == '/' } == 1 })
        assertEquals(null, activity(s)?.overlayMode)
        screenshot("11_profil_bitti")
    }

    @Test
    fun c2_singleVideoToGallery() {
        TestHooks.sinkFactory = null   // gerçek galeri (MediaStore)
        val resolver = ctx.contentResolver
        val where = "${MediaStore.MediaColumns.RELATIVE_PATH}=? AND ${MediaStore.MediaColumns.DISPLAY_NAME} LIKE ?"
        val code = Links.shortcodeFromId((3400000000000000000L + 1001L * 1000L + 1).toString())
        val args = arrayOf("DCIM/Instagram/$profile/Videolar/", "%_$code.mp4")
        resolver.delete(MediaStore.Video.Media.EXTERNAL_CONTENT_URI, where, args)
        launch()
        setLink("https://www.instagram.com/reel/$code/")
        assertEquals("true", js("document.getElementById('mode-single').checked"))
        val snap = startAndWait()
        assertEquals(logs(snap), "done", snap.getString("phase"))
        assertEquals(1, snap.getInt("files_done"))
        var count = 0
        resolver.query(MediaStore.Video.Media.EXTERNAL_CONTENT_URI,
            arrayOf(MediaStore.MediaColumns.DISPLAY_NAME, MediaStore.MediaColumns.SIZE), where, args, null)?.use { c ->
            while (c.moveToNext()) { count++; assertTrue(c.getLong(1) > 0) }
        }
        assertEquals("galeride bulunamadı", 1, count)
        // İkinci kez: zaten var
        clickId("start")
        waitUntil(10_000) { Core.engine.running }
        waitUntil(60_000) { !Core.engine.running }
        assertEquals(1, Core.engine.filesSkipped.get())
        resolver.delete(MediaStore.Video.Media.EXTERNAL_CONTENT_URI, where, args)
    }

    @Test
    fun c3_sessionEndsThenContinuesFromHistory() {
        set("session_kill_after" to 2)
        launch()
        setLink(profile)
        setChecked("history", true)
        setChecked("w-stories", false); setChecked("w-highlights", false); setChecked("w-avatar", false)
        var snap = startAndWait()
        assertEquals(logs(snap), "done", snap.getString("phase"))
        assertTrue(logs(snap), snap.getBoolean("login_wall"))
        assertEquals(24, snap.getInt("found_posts"))
        assertTrue(jsText("document.getElementById('notes').textContent").contains("oturumu kapattı"))
        screenshot("12_oturum_kapandi")

        // Tekrar giriş ve aynı linkle devam
        control("/__sifirla")
        set("autologin" to 1)
        clickId("login")
        assertTrue(waitUntil(30_000) { Core.engine.mode == Engine.MODE_LOGIN && !Core.engine.running })
        set("autologin" to 0)
        val first = files().size
        snap = startAndWait()
        val l = logs(snap)
        assertEquals(l, "done", snap.getString("phase"))
        assertTrue(l, "browser.resuming" in Core.engine.logKeys())
        assertEquals(l, 11, snap.getInt("posts_known"))
        assertTrue(files().size > first)
        setChecked("history", false)
        setChecked("w-stories", true); setChecked("w-highlights", true); setChecked("w-avatar", true)
    }

    @Test
    fun c4_stopButton() {
        set("slow_cdn" to 0.5)
        launch()
        setLink(profile)
        clickId("start")
        assertTrue(waitUntil(30_000) { Core.engine.running && Core.engine.filesDone.get() > 0 })
        clickId("start")   // aynı düğme "Durdur" olur
        assertTrue(waitUntil(30_000) { !Core.engine.running })
        assertEquals("stopped", Core.engine.phase)
        Thread.sleep(1200)
        assertEquals("Durduruldu", jsText("document.getElementById('status').textContent"))
    }

    @Test
    fun c5_screenRebuiltDuringCollection() {
        set("slow_cdn" to 0.1)
        val s = launch()
        setLink(profile)
        clickId("start")
        assertTrue(waitUntil(30_000) { Core.engine.running && Core.engine.foundPosts.get() > 0 })
        s.recreate()
        assertTrue("ekran geri gelmedi", waitUntil(30_000) { activity(s)?.uiReady == true })
        assertTrue(waitUntil(180_000) { !Core.engine.running })
        val snap = Core.engine.snapshot(0)
        assertEquals(logs(snap), "done", snap.getString("phase"))
        assertEquals(logs(snap), 41, snap.getInt("found_posts"))
        Thread.sleep(1500)
        assertEquals("İndirme tamamlandı", jsText("document.getElementById('status').textContent"))
        screenshot("13_ekran_yeniden_kuruldu")
    }

    // ---------------------------------------------------------------- yeni arayüz: sekmeler, geçmiş, sihirbaz
    @Test
    fun d1_tabsAndHistoryList() {
        Core.history().clear()
        launch()
        assertEquals("true", js("document.getElementById('view-download').hidden === false"))
        clickId("tab-history")
        assertTrue("geçmiş sekmesi açılmadı", waitUntil(5_000) { js("document.getElementById('view-history').hidden === false") == "true" })
        assertTrue("boş geçmiş mesajı yok", waitUntil(5_000) { js("document.getElementById('hist-empty').hidden === false") == "true" })
        Core.history().mark("u1", "k1", "all", "post", "natgeo")
        Core.history().mark("u1", "k2", "all", "post", "natgeo")
        Core.history().mark("u2", "k1", "all", "post", "sahte.kisi")
        clickId("tab-download"); clickId("tab-history")
        assertTrue("geçmiş listelenmedi", waitUntil(5_000) { js("document.querySelectorAll('#hist-list .person').length") == "2" })
        assertEquals("2", jsText("document.getElementById('hist-profiles').textContent"))
        assertEquals("3", jsText("document.getElementById('hist-posts').textContent"))
        assertTrue(jsText("document.getElementById('hist-list').textContent").contains("@natgeo"))
        screenshot("20_gecmis_sekmesi")
        // "Yenileri indir" linki indir sekmesine taşır
        js("(function(){var b=[].slice.call(document.querySelectorAll('#hist-list .person .btn.primary'))[0]; b.click(); return true;})()")
        assertTrue(waitUntil(5_000) { js("document.getElementById('view-download').hidden === false") == "true" })
        assertTrue(waitUntil(5_000) { jsText("document.getElementById('link').value").contains("instagram.com/") })
        clickId("tab-settings")
        assertEquals("true", js("document.getElementById('view-settings').hidden === false"))
        assertEquals("true", js("!!document.getElementById('identity') && !!document.getElementById('speed')"))
        screenshot("21_ayarlar_sekmesi")
        Core.history().clear()
    }

    @Test
    fun d2_wizardOnFreshInstallThenSkips() {
        clearCookies()
        Core.history().clear()
        Core.saveSettings(SettingsValues(lang = "tr", setupDone = false))
        launch()
        assertTrue("sihirbaz açılmadı", waitUntil(10_000) { js("document.getElementById('wizard').hidden === false") == "true" })
        assertEquals("Adım 1/3", jsText("document.getElementById('wz-step').textContent"))
        screenshot("22_sihirbaz")
        clickId("wz-next")                                   // karşılama -> giriş
        assertEquals("Adım 2/3", jsText("document.getElementById('wz-step').textContent"))
        clickId("wz-skip")                                   // girişi atla -> hazır
        assertEquals("Adım 3/3", jsText("document.getElementById('wz-step').textContent"))
        clickId("wz-next")                                   // bitir
        assertTrue(waitUntil(5_000) { js("document.getElementById('wizard').hidden === true") == "true" })
        assertTrue("sihirbaz bitti ayarı kaydedilmedi", waitUntil(5_000) { Core.settings.setupDone })
    }

    @Test
    fun d3_wizardSkippedForExistingUser() {
        Core.history().clear()
        Core.history().mark("u1", "k1", "all", "post", "natgeo")
        Core.saveSettings(SettingsValues(lang = "tr", setupDone = false))
        launch()
        assertTrue("eski kullanıcıda ayar kaydedilmedi", waitUntil(10_000) { Core.settings.setupDone })
        assertEquals("true", js("document.getElementById('wizard').hidden === true"))
        Core.history().clear()
    }
}
