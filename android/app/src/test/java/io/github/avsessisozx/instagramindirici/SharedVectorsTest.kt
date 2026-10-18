package io.github.avsessisozx.instagramindirici

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * tests/vectors klasöründeki .json dosyaları: Windows (Python) sürümünün ürettiği beklenen sonuçlar.
 * Android sürümü aynı linki aynı şekilde tanımalı, aynı dosyayı seçmeli, dosyaları aynı adla
 * aynı klasöre kaydetmeli ve Instagram yanıtlarını aynı şekilde yorumlamalı.
 */
class SharedVectorsTest {
    private val dir = File(System.getProperty("vectorsDir") ?: "../../tests/vectors")
    private fun load(name: String) = File(dir, name).readText(Charsets.UTF_8)
    private fun nul(o: JSONObject, k: String): String? = if (o.isNull(k)) null else o.get(k).toString()
    private fun strings(a: JSONArray?): List<String> = if (a == null) emptyList() else (0 until a.length()).map { a.get(it).toString() }

    @Test
    fun links() {
        val data = JSONObject(load("links.json"))
        val cases = data.getJSONArray("parse")
        for (i in 0 until cases.length()) {
            val c = cases.getJSONObject(i)
            val label = c.getString("input")
            val p = Links.parse(label)
            assertEquals(label, c.getBoolean("ok"), p.ok)
            assertEquals(label, nul(c, "kind"), p.kind)
            assertEquals(label, c.getString("username"), p.username)
            assertEquals(label, c.getString("shortcode"), p.shortcode)
            assertEquals(label, c.getString("highlight_id"), p.highlightId)
            assertEquals(label, c.getString("story_media_id"), p.storyMediaId)
            assertEquals(label, c.getString("url"), p.url)
            assertEquals(label, nul(c, "error"), p.error?.key)
            assertEquals(label, c.getString("describe"), p.describe().key)
        }
        assertTrue(cases.length() > 30)
        val codes = data.getJSONArray("codes")
        for (i in 0 until codes.length()) {
            val c = codes.getJSONObject(i)
            if (c.getString("id").isNotEmpty()) {
                assertEquals(c.getString("id"), c.getString("code"), Links.shortcodeFromId(c.getString("id")))
                assertEquals(c.getString("code"), c.getString("back"), Links.idFromShortcode(c.getString("code")))
            } else {
                assertEquals("3394145943868104119", c.getString("back"))
            }
        }
    }

    @Test
    fun naming() {
        val data = JSONObject(load("naming.json"))
        val cases = data.getJSONArray("cases")
        for (i in 0 until cases.length()) {
            val c = cases.getJSONObject(i)
            val input: Any? = if (c.isNull("input")) null else c.get("input")
            assertEquals("$input", c.getString("sanitize"), Naming.sanitize(input))
            assertEquals("$input", c.getString("sanitize20"), Naming.sanitize(input, 20, ""))
            assertEquals("$input", c.getString("title"), Naming.titleFromDesc(input))
        }
        val dates = data.getJSONArray("dates")
        for (i in 0 until dates.length()) {
            val c = dates.getJSONObject(i)
            val ts: Any? = if (c.isNull("ts")) null else c.get("ts")
            assertEquals("$ts", c.getString("date"), Naming.dateFromTimestamp(ts))
        }
    }

    private fun checkPost(label: String, r: JSONObject, post: Media.Post) {
        assertEquals(label, r.getString("key"), post.key)
        assertEquals(label, r.getString("code"), post.code)
        assertEquals(label, r.getString("owner_id"), post.ownerId)
        assertEquals(label, r.getLong("taken_at"), post.takenAt)
        assertEquals(label, r.getBoolean("pinned"), post.pinned)
        assertEquals(label, r.getString("stem"), post.stem)
        assertEquals(label, nul(r, "skipped"), post.skippedReason?.key)
        val files = r.getJSONArray("files")
        assertEquals(label, files.length(), post.files.size)
        for (k in 0 until files.length()) {
            val f = files.getJSONObject(k)
            val m = post.files[k]
            assertEquals(label, f.getString("kind"), m.kind)
            assertEquals(label, f.getString("rel_stem"), m.relStem)
            assertEquals(label, f.getString("legacy_stem"), m.legacyStem)
            assertEquals(label, f.getString("ext"), m.ext)
            assertEquals(label, strings(f.getJSONArray("urls")), m.urls)
        }
    }

    @Test
    fun posts() {
        val cases = JSONArray(load("posts.json"))
        var checked = 0
        for (i in 0 until cases.length()) {
            val case = cases.getJSONObject(i)
            val results = case.getJSONArray("results")
            for (j in 0 until results.length()) {
                val r = results.getJSONObject(j)
                val label = "${case.getString("name")} ${r.getString("content")}"
                val post = Media.buildPost(case.getJSONObject("item"), r.getString("content"), case.getString("kind"),
                    case.getString("subdir"), "sahte.kisi")
                checkPost(label, r, post)
                checked++
            }
        }
        assertTrue(checked > 60)
    }

    @Test
    fun finders() {
        val data = JSONObject(load("find.json"))
        val blobs = data.getJSONArray("blobs")
        for (i in 0 until blobs.length()) {
            val c = blobs.getJSONObject(i)
            val blob = c.get("blob")
            val label = "blob $i"
            assertEquals(label, strings(c.getJSONArray("media_keys")), Media.findMedia(blob).map { Media.mediaKey(it) })
            val pi = Media.findPageInfo(blob)
            if (c.isNull("page_info")) assertEquals(label, null, pi)
            else {
                val want = c.getJSONObject("page_info")
                assertEquals(label, want.getBoolean("has_next_page"), pi!!.optBoolean("has_next_page"))
                assertEquals(label, nul(want, "end_cursor"), nul(pi, "end_cursor"))
            }
            val user = Media.findUser(blob, "sahte.kisi")
            assertEquals(label, nul(c, "user_full_name"), user?.let { if (it.has("full_name")) it.getString("full_name") else null })
            assertEquals(label, c.getString("user_id"), user?.let { Media.userIdOf(it) } ?: "")
            val hl = Media.findHighlights(blob)
            val want = c.getJSONArray("highlights")
            assertEquals(label, want.length(), hl.size)
            for (k in 0 until want.length()) {
                assertEquals(label, want.getJSONObject(k).getString("id"), hl[k].getString("id"))
                assertEquals(label, want.getJSONObject(k).optString("title"), hl[k].optString("title"))
            }
        }
        val avatars = data.getJSONArray("avatars")
        for (i in 0 until avatars.length()) {
            val c = avatars.getJSONObject(i)
            val user = c.getJSONObject("user")
            val urls = Media.avatarUrls(user)
            assertEquals("avatar $i", strings(c.getJSONArray("urls")), urls)
            checkPost("avatar $i", c, Media.buildAvatar(user, urls, c.getString("content")))
        }
    }

    @Test
    fun classify() {
        val cases = JSONArray(load("classify.json"))
        for (i in 0 until cases.length()) {
            val c = cases.getJSONObject(i)
            val data = Insta.parseJsonText(c.getString("text"))
            assertEquals("case $i", c.getString("kind"), Insta.classify(c.getInt("status"), c.getString("url"), data))
        }
    }
}
