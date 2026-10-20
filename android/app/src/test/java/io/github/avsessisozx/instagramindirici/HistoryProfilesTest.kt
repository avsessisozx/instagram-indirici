package io.github.avsessisozx.instagramindirici

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/** Geçmiş sekmesinin verisi: profil listesi, "geçmişi sil" ve yeni ayar alanları. */
class HistoryProfilesTest {
    private fun tmp(): File = File.createTempFile("gecmis", ".json").also { it.delete(); it.deleteOnExit() }

    @Test fun profilesAreListedNewestFirstWithCounts() {
        val h = History(tmp())
        h.mark("u1", "a", "all", "post", "natgeo")
        h.mark("u1", "b", "all", "post", "natgeo")
        h.mark("u1", "c", "video", "post", "natgeo")
        h.mark("u2", "a", "all", "post", "sahte.kisi")
        val list = h.profiles()
        assertEquals(setOf("natgeo", "sahte.kisi"), list.map { it.username }.toSet())
        assertEquals(3, list.first { it.userId == "u1" }.count)
        assertEquals(1, list.first { it.userId == "u2" }.count)
        assertTrue(list.all { it.last > 0 })
    }

    @Test fun forgetRemovesOnlyThatProfile() {
        val f = tmp()
        val h = History(f)
        h.mark("u1", "a", "all", "post", "natgeo")
        h.mark("u1", "b", "all", "post", "natgeo")
        h.mark("u2", "a", "all", "post", "sahte.kisi")
        h.setResumePoint("u1", "all", "rest", "CURSOR", "natgeo")
        assertEquals(2, h.forget("u1"))
        assertEquals(listOf("u2"), h.profiles().map { it.userId })
        assertTrue(h.known("u1", "all").isEmpty())
        assertEquals(null, h.resumePoint("u1", "all"))
        assertEquals(0, h.forget(""))
        // diske yazıldı ve yeniden okununca da böyle
        assertEquals(listOf("u2"), History(f).profiles().map { it.userId })
    }

    @Test fun oldFilesWithoutMetaStillList() {
        val f = tmp()
        f.writeText("""{"downloaded":{"u9":{"all":["x","y"]}},"progress":{"u9":{"all":{"newest":5,"username":"eski.kisi"}}}}""")
        val list = History(f).profiles()
        assertEquals(1, list.size)
        assertEquals("eski.kisi", list[0].username)       // kullanıcı adı ilerleme kaydından alınır
        assertEquals(2, list[0].count)
    }

    @Test fun clearAlsoDropsMeta() {
        val h = History(tmp())
        h.mark("u1", "a", "all", "post", "natgeo")
        assertEquals(1, h.clear())
        assertTrue(h.profiles().isEmpty())
    }

    @Test fun settingsKeepSetupAndConfirmFlags() {
        val s = SettingsValues().merge(JSONObject().put("setup_done", true).put("no_confirm", true), listOf("tr", "en"))
        assertTrue(s.setupDone && s.noConfirm)
        val back = SettingsValues.fromJson(s.toJson().toString(), listOf("tr", "en"))
        assertTrue(back.setupDone && back.noConfirm)
        // alan gelmezse mevcut değer korunur
        val kept = s.merge(JSONObject().put("speed", "careful"), listOf("tr", "en"))
        assertTrue(kept.setupDone && kept.noConfirm)
        assertEquals("careful", kept.speed)
        assertFalse(SettingsValues().setupDone || SettingsValues().noConfirm)
    }
}
