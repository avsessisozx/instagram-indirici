package io.github.avsessisozx.instagramindirici

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

class TrTest {
    private val dir = File(System.getProperty("translationsDir") ?: "../../ceviriler")
    private val langs = dir.listFiles { f -> f.name.endsWith(".json") }!!.map { it.name.removeSuffix(".json") }.sorted()
    private val tr = Tr({ code -> File(dir, "$code.json").takeIf { it.exists() }?.readText(Charsets.UTF_8) }, langs)

    @Test
    fun translateAndFallback() {
        assertEquals("İndirmeyi başlat", tr.text("ui.start", "tr"))
        assertEquals("Start download", tr.text("ui.start", "en"))
        assertEquals("Start download", tr.text("ui.start", "xx"))
        assertEquals("yok.boyle", tr.text("yok.boyle", "tr"))
        val m = msg("engine.skipped_post", "reason" to msg("skip.no_media"), "name" to "a")
        assertEquals("Atlandı (indirilecek dosya bulunamadı): a", tr.render(m, "tr"))
        assertEquals("Bitti: 3 dosya indirildi, 2 dosya zaten vardı.",
            tr.text("summary", "tr", mapOf("done" to 3, "skipped" to 2)))
        assertEquals("Done: 1 files downloaded.", tr.text("summary", "en", mapOf("done" to 1)))
    }

    @Test
    fun languageResolution() {
        assertEquals("tr", tr.resolve("auto", "tr"))
        assertEquals("tr", tr.resolve("auto", "tr-TR"))
        assertEquals("en", tr.resolve("auto", "de"))
        assertEquals("en", tr.resolve("en", "tr"))
        assertEquals("tr", tr.resolve("xx", "tr"))
        assertTrue(tr.available().any { it.first == "tr" && it.second == "Türkçe" })
    }

    @Test
    fun msgJson() {
        val j = msg("engine.download_failed", "name" to "x", "error" to msg("fetch.http", "code" to 403)).toJson()
        assertEquals("engine.download_failed", j.getString("key"))
        assertEquals(403, j.getJSONObject("params").getJSONObject("error").getJSONObject("params").getInt("code"))
    }
}
