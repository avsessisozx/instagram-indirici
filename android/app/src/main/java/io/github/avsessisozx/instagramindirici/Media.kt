package io.github.avsessisozx.instagramindirici

import org.json.JSONArray
import org.json.JSONObject
import org.w3c.dom.Element
import org.xml.sax.InputSource
import java.io.StringReader
import javax.xml.parsers.DocumentBuilderFactory

/**
 * Instagram yanıtlarındaki gönderileri bulur ve indirilecek dosyalara çevirir.
 * Windows sürümündeki media.py'nin birebir karşılığı (tests/vectors ile denetlenir).
 *
 * Instagram aynı gönderiyi iki biçimde verebiliyor: "v1" (pk, code, image_versions2,
 * video_versions, carousel_media) ve eski GraphQL (shortcode, display_url, video_url,
 * edge_sidecar_to_children). İkisi de tanınır; yanıtın içinde nerede durdukları önemli değil.
 */
object Media {
    const val CONTENT_ALL = "all"
    const val CONTENT_VIDEO = "video"
    const val CONTENT_IMAGE = "image"
    val CONTENT_CHOICES = listOf(CONTENT_ALL, CONTENT_VIDEO, CONTENT_IMAGE)

    const val KIND_POST = "post"
    const val KIND_STORY = "story"
    const val KIND_HIGHLIGHT = "highlight"
    const val KIND_AVATAR = "avatar"

    const val STORIES_DIR = "Hikayeler"
    const val HIGHLIGHTS_DIR = "Öne Çıkanlar"
    const val AVATAR_DIR = "Profil Fotoğrafı"
    const val PHOTOS_DIR = "Fotoğraflar"
    const val VIDEOS_DIR = "Videolar"

    /** DASH bildirimindeki (video_dash_manifest) tek bir akış: ses ya da görüntü. */
    data class DashStream(
        val url: String, val width: Long = 0, val height: Long = 0, val bandwidth: Long = 0,
        val codecs: String = "",
    ) {
        val area: Long get() = width * height

        /**
         * Telefonun MediaMuxer'ı mp4'e koyabilsin: H.264 / H.265 görüntü, AAC ses. VP9, AV1, Opus gibileri
         * reddedilir ("Failed to add the track"). Codec bilinmiyorsa (boş) denenir.
         */
        val muxable: Boolean get() {
            val c = codecs.lowercase()
            if (c.isEmpty()) return true
            return c.startsWith("avc") || c.startsWith("hvc") || c.startsWith("hev") || c.startsWith("mp4a")
        }
    }

    data class DashChoice(val video: DashStream, val audio: DashStream? = null)

    data class MediaFile(
        val kind: String,               // "video" | "image"
        val urls: List<String>,
        var relStem: String,            // kişinin klasörüne göre yol, uzantısız
        val ext: String = "",           // boşsa sunucunun bildirdiği türe göre
        val expectedSize: Long = 0,
        var legacyStem: String = "",
        val dash: DashChoice? = null,   // varsa: normal sürümden daha iyi ses+görüntü akışı (birleştirilerek indirilir)
        val plainRes: String = "",      // normal (tek parça) sürümün çözünürlüğü, ör. "720x1280" (kayıt için)
    )

    class Post(val kind: String) {
        var key = ""
        var code = ""
        var ownerId = ""
        var username = ""
        var authorName = ""
        var takenAt = 0L
        var pinned = false
        var stem = ""
        val files = mutableListOf<MediaFile>()
        var skippedReason: Msg? = null
        var historyContent = ""
        var page: FeedPage? = null
        val isGallery: Boolean get() = files.size > 1
    }

    // ------------------------------------------------------------ Python davranışını taklit eden yardımcılar
    /** Python doğruluğu: None, 0, "", [], {} ve False yanlıştır. */
    fun truthy(v: Any?): Boolean = when (v) {
        null, JSONObject.NULL -> false
        is Boolean -> v
        is Number -> v.toDouble() != 0.0
        is String -> v.isNotEmpty()
        is JSONArray -> v.length() > 0
        is JSONObject -> v.length() > 0
        else -> true
    }

    /** Python str(): sayılar ondalıksız, None "None" değil boş (çağıran yerler zaten "or ''" kullanıyor). */
    fun pyStr(v: Any?): String = when (v) {
        null, JSONObject.NULL -> ""
        is Boolean -> if (v) "True" else "False"
        is Double -> if (v == Math.floor(v) && !v.isInfinite() && Math.abs(v) < 1e15) v.toLong().toString() + ".0" else v.toString()
        is Float -> pyStr(v.toDouble())
        is Number -> v.toString()
        else -> v.toString()
    }

    /** Python: a or b */
    fun or(vararg values: Any?): Any? {
        for (v in values) if (truthy(v)) return v
        return values.lastOrNull()
    }

    fun opt(d: Any?, key: String): Any? = if (d is JSONObject && d.has(key)) d.opt(key).let { if (it == JSONObject.NULL) null else it } else null

    /** Python _int(): int(değer), olmazsa 0. */
    fun toInt(v: Any?): Long = when (v) {
        null, JSONObject.NULL -> 0
        is Boolean -> if (v) 1 else 0
        is Int -> v.toLong()
        is Long -> v
        is Double -> if (v.isNaN() || v.isInfinite()) 0 else v.toLong()
        is Number -> v.toLong()
        is String -> v.trim().toLongOrNull() ?: 0
        else -> 0
    }

    private fun values(o: JSONObject): List<Any?> = o.keys().asSequence().map { o.opt(it) }.toList()

    // ------------------------------------------------------------ tanıma
    fun isV1Media(d: Any?): Boolean {
        if (d !is JSONObject) return false
        if (!(d.has("pk") || d.has("id"))) return false
        return d.has("image_versions2") || d.has("video_versions") || d.has("carousel_media")
    }

    fun isGraphqlMedia(d: Any?): Boolean {
        if (d !is JSONObject || !truthy(opt(d, "shortcode"))) return false
        return d.has("display_url") || d.has("display_resources") || d.has("edge_sidecar_to_children") || d.has("video_url")
    }

    fun isMedia(d: Any?): Boolean = isV1Media(d) || isGraphqlMedia(d)

    /** Herhangi bir JSON'un içindeki gönderileri sırayla bulur (albüm parçalarına inmez). */
    fun findMedia(obj: Any?, limit: Int = 100_000): List<JSONObject> {
        val out = mutableListOf<JSONObject>()
        val stack = ArrayDeque<Any?>()
        stack.addLast(obj)
        while (stack.isNotEmpty() && out.size < limit) {
            when (val cur = stack.removeLast()) {
                is JSONObject -> {
                    if (isMedia(cur)) { out += cur; continue }
                    for (v in values(cur).asReversed()) stack.addLast(v)
                }
                is JSONArray -> for (i in cur.length() - 1 downTo 0) stack.addLast(cur.opt(i))
            }
        }
        return out
    }

    fun findPageInfo(obj: Any?): JSONObject? {
        val stack = ArrayDeque<Any?>()
        stack.addLast(obj)
        while (stack.isNotEmpty()) {
            when (val cur = stack.removeLast()) {
                is JSONObject -> {
                    val info = cur.opt("page_info")
                    if (info is JSONObject && info.has("has_next_page")) return info
                    for (v in values(cur)) stack.addLast(v)
                }
                is JSONArray -> for (i in 0 until cur.length()) stack.addLast(cur.opt(i))
            }
        }
        return null
    }

    fun findUser(obj: Any?, username: String): JSONObject? {
        var best: JSONObject? = null
        val name = username.lowercase()
        val stack = ArrayDeque<Any?>()
        stack.addLast(obj)
        while (stack.isNotEmpty()) {
            when (val cur = stack.removeLast()) {
                is JSONObject -> {
                    if (pyStr(or(opt(cur, "username"), "")).lowercase() == name &&
                        truthy(or(opt(cur, "pk"), opt(cur, "id"), opt(cur, "pk_id")))) {
                        if (best == null || cur.length() > best.length()) best = cur
                    }
                    for (v in values(cur)) stack.addLast(v)
                }
                is JSONArray -> for (i in 0 until cur.length()) stack.addLast(cur.opt(i))
            }
        }
        return best
    }

    /** Yanıtın içindeki öne çıkanlar: id ("highlight:…"), title, media_count. */
    fun findHighlights(obj: Any?): List<JSONObject> {
        val out = mutableListOf<JSONObject>()
        val seen = HashSet<String>()
        fun add(hidRaw: Any?, entry: JSONObject) {
            var hid = pyStr(or(hidRaw, ""))
            if (hid.isEmpty()) return
            if (!hid.startsWith("highlight:")) {
                if (!hid.all { it.isDigit() }) return
                hid = "highlight:$hid"
            }
            if (!seen.add(hid)) return
            out += JSONObject().put("id", hid).put("title", pyStr(or(opt(entry, "title"), "")))
                .put("media_count", toInt(or(opt(entry, "media_count"), opt(entry, "highlight_reel_count"))))
        }
        val queue = ArrayDeque<Any?>()
        queue.addLast(obj)
        while (queue.isNotEmpty()) {
            when (val cur = queue.removeFirst()) {
                is JSONObject -> {
                    val id = opt(cur, "id")
                    if (id is String && id.startsWith("highlight:") && cur.has("title")) { add(id, cur); continue }
                    val edge = cur.opt("edge_highlight_reels")
                    if (edge is JSONObject) {
                        val edges = edge.optJSONArray("edges")
                        if (edges != null) for (i in 0 until edges.length()) {
                            val node = (edges.opt(i) as? JSONObject)?.opt("node")
                            if (node is JSONObject) add(opt(node, "id"), node)
                        }
                    }
                    for (v in values(cur)) queue.addLast(v)
                }
                is JSONArray -> for (i in 0 until cur.length()) queue.addLast(cur.opt(i))
            }
        }
        return out
    }

    fun userIdOf(user: JSONObject): String {
        for (k in listOf("pk", "pk_id", "id")) {
            val v = opt(user, k)
            val s = pyStr(v)
            if (truthy(v) && s.isNotEmpty() && s.all { it.isDigit() }) return s
        }
        return ""
    }

    fun mediaKey(item: JSONObject): String {
        val code = or(opt(item, "code"), opt(item, "shortcode"))
        if (truthy(code)) return pyStr(code)
        val pk = or(opt(item, "pk"), pyStr(or(opt(item, "id"), "")).substringBefore('_'))
        return pyStr(or(pk, ""))
    }

    // ------------------------------------------------------------ alanlar
    private fun area(e: JSONObject, w: String = "width", h: String = "height") = toInt(opt(e, w)) * toInt(opt(e, h))

    private fun dedupe(urls: List<Any?>): List<String> {
        val out = mutableListOf<String>()
        for (u in urls) if (u is String && u.startsWith("http") && u !in out) out += u
        return out
    }

    private fun objects(arr: Any?): List<JSONObject> =
        if (arr is JSONArray) (0 until arr.length()).mapNotNull { arr.opt(it) as? JSONObject } else emptyList()

    fun imageUrls(item: JSONObject): List<String> {
        val urls = mutableListOf<Any?>()
        val iv = opt(item, "image_versions2")
        if (iv is JSONObject) {
            val cands = iv.opt("candidates")
            if (cands is JSONArray) {
                val good = objects(cands).filter { truthy(opt(it, "url")) }
                good.withIndex().sortedWith(compareBy({ -area(it.value) }, { it.index })).forEach { urls += opt(it.value, "url") }
            }
        }
        val res = opt(item, "display_resources")
        if (res is JSONArray) {
            val good = objects(res).filter { truthy(opt(it, "src")) }
            good.withIndex().sortedWith(compareBy({ -area(it.value, "config_width", "config_height") }, { it.index }))
                .forEach { urls += opt(it.value, "src") }
        }
        for (k in listOf("display_url", "display_src", "thumbnail_src")) if (truthy(opt(item, k))) urls += opt(item, k)
        return dedupe(urls)
    }

    fun videoUrls(item: JSONObject): List<String> {
        val urls = mutableListOf<Any?>()
        val vv = opt(item, "video_versions")
        if (vv is JSONArray) {
            val good = objects(vv).filter { truthy(opt(it, "url")) }
            good.withIndex().sortedWith(compareBy({ -area(it.value) }, { it.index })).forEach { urls += opt(it.value, "url") }
        }
        if (truthy(opt(item, "video_url"))) urls += opt(item, "video_url")
        return dedupe(urls)
    }

    private fun localName(e: Element): String = e.tagName.substringAfter(':')

    private fun childElements(e: Element): List<Element> {
        val out = ArrayList<Element>()
        val nodes = e.childNodes
        for (i in 0 until nodes.length) (nodes.item(i) as? Element)?.let { out += it }
        return out
    }

    /**
     * video_dash_manifest (MPD) içindeki tek dosyalı video ve ses akışları. Instagram uygulama içinde bunu
     * oynatır; en yüksek kalite çoğu zaman sadece burada olur. Parçalı (SegmentTemplate/SegmentList) akışlar atlanır.
     */
    fun dashStreams(item: JSONObject): Pair<List<DashStream>, List<DashStream>> {
        var manifest = opt(item, "video_dash_manifest")
        if (!truthy(manifest)) (opt(item, "dash_info") as? JSONObject)?.let { manifest = opt(it, "video_dash_manifest") }
        val text = (manifest as? String)?.trim() ?: return emptyList<DashStream>() to emptyList()
        if ('<' !in text) return emptyList<DashStream>() to emptyList()
        val doc = try {
            val factory = DocumentBuilderFactory.newInstance()
            factory.isNamespaceAware = false
            try { factory.setFeature("http://apache.org/xml/features/disallow-doctype-decl", true) } catch (e: Exception) {}
            factory.newDocumentBuilder().parse(InputSource(StringReader(text)))
        } catch (e: Exception) {
            return emptyList<DashStream>() to emptyList()
        }
        val videos = ArrayList<DashStream>()
        val audios = ArrayList<DashStream>()
        val sets = doc.getElementsByTagName("AdaptationSet")
        for (i in 0 until sets.length) {
            val set = sets.item(i) as? Element ?: continue
            val setType = (set.getAttribute("contentType").ifEmpty { set.getAttribute("mimeType") }).lowercase()
            val setSegmented = childElements(set).any { localName(it) == "SegmentTemplate" || localName(it) == "SegmentList" }
            for (rep in childElements(set)) {
                if (localName(rep) != "Representation") continue
                var url = ""
                var segmented = setSegmented
                for (child in childElements(rep)) {
                    when (localName(child)) {
                        "BaseURL" -> { val t = child.textContent.trim(); if (t.startsWith("http")) url = t }
                        "SegmentTemplate", "SegmentList" -> segmented = true
                    }
                }
                if (url.isEmpty() || segmented) continue
                val kind = (rep.getAttribute("mimeType").ifEmpty { setType }).lowercase()
                val width = rep.getAttribute("width").trim().toLongOrNull() ?: 0L
                val stream = DashStream(
                    url, width, rep.getAttribute("height").trim().toLongOrNull() ?: 0L,
                    rep.getAttribute("bandwidth").trim().toLongOrNull() ?: 0L,
                    rep.getAttribute("codecs").ifEmpty { set.getAttribute("codecs") }.trim(),
                )
                if ("audio" in kind) audios += stream else if ("video" in kind || width > 0) videos += stream
            }
        }
        return videos to audios
    }

    fun dashChoice(item: JSONObject): DashChoice? {
        val (videos, audios) = dashStreams(item)
        if (videos.isEmpty()) return null
        // Telefonda birleştirilebilen akışlar öncelikli (VP9/AV1 en büyük olsa bile H.264 seçilir).
        val best = videos.sortedWith(compareByDescending<DashStream> { it.muxable }.thenByDescending { it.area }
            .thenByDescending { it.bandwidth }).first()
        val audio = audios.sortedWith(compareByDescending<DashStream> { it.muxable }.thenByDescending { it.bandwidth }).firstOrNull()
        return DashChoice(best, audio)
    }

    /** Normal (tek parça) sürümlerin en büyüğünün genişlik ve yüksekliği; bilinmiyorsa (0, 0). */
    fun plainSize(item: JSONObject): Pair<Long, Long> {
        var best = 0L to 0L
        val vv = opt(item, "video_versions")
        if (vv is JSONArray) {
            for (v in objects(vv)) {
                if (!truthy(opt(v, "url"))) continue
                val size = toInt(opt(v, "width")) to toInt(opt(v, "height"))
                if (size.first * size.second > best.first * best.second) best = size
            }
        }
        if (best == 0L to 0L) (opt(item, "dimensions") as? JSONObject)?.let { best = toInt(opt(it, "width")) to toInt(opt(it, "height")) }
        return best
    }

    fun isVideo(item: JSONObject): Boolean {
        if (truthy(opt(item, "video_versions")) || truthy(opt(item, "video_url"))) return true
        if (opt(item, "is_video") == true) return true
        return toInt(opt(item, "media_type")) == 2L
    }

    fun children(item: JSONObject): List<JSONObject> {
        val carousel = opt(item, "carousel_media")
        if (carousel is JSONArray && carousel.length() > 0) return objects(carousel)
        val sidecar = opt(item, "edge_sidecar_to_children")
        if (sidecar is JSONObject) {
            return objects(sidecar.opt("edges")).mapNotNull { e -> e.opt("node") as? JSONObject }
        }
        return emptyList()
    }

    fun captionOf(item: JSONObject): String {
        val c = opt(item, "caption")
        if (c is JSONObject) return pyStr(or(opt(c, "text"), ""))
        if (c is String) return c
        val e = opt(item, "edge_media_to_caption")
        if (e is JSONObject) {
            val edges = e.opt("edges")
            if (edges is JSONArray) {
                return objects(edges).map { pyStr(or(opt(it.opt("node"), "text"), "")) }.filter { it.isNotEmpty() }.joinToString("\n")
            }
        }
        return ""
    }

    fun takenAtOf(item: JSONObject): Long {
        for (k in listOf("taken_at", "taken_at_timestamp", "device_timestamp", "created_at")) {
            var v = toInt(opt(item, k))
            while (v > 10_000_000_000L) v /= 1000
            if (v > 0) return v
        }
        return 0
    }

    fun ownerOf(item: JSONObject): JSONObject {
        for (k in listOf("user", "owner")) { val o = opt(item, k); if (o is JSONObject) return o }
        return JSONObject()
    }

    fun isPinned(item: JSONObject): Boolean =
        truthy(opt(item, "timeline_pinned_user_ids")) || truthy(opt(item, "pinned_for_users")) ||
            truthy(opt(item, "clips_tab_pinned_user_ids"))

    private fun urlStem(url: String): String {
        var path = url.substringAfter("://", url)
        path = path.substringBefore('#').substringBefore('?')
        path = if ('/' in path) path.substring(path.indexOf('/')) else ""
        val name = path.substringAfterLast('/')
        return if ('.' in name) name.substringBeforeLast('.') else name
    }

    // ------------------------------------------------------------ dosya listesi
    private fun wanted(kind: String, content: String) = when (content) {
        CONTENT_VIDEO -> kind == "video"
        CONTENT_IMAGE -> kind == "image"
        else -> true
    }

    private fun fileFor(node: JSONObject, rel: String): MediaFile {
        if (!isVideo(node)) return MediaFile("image", imageUrls(node), rel, "")
        val (width, height) = plainSize(node)
        val dash = dashChoice(node)
        // Daha büyük (ya da aynı) çözünürlükte DASH akışı varsa onu kullan; indirilemezse normal sürüme dönülür.
        val use = if (dash != null && dash.video.area >= width * height) dash else null
        return MediaFile("video", videoUrls(node), rel, ".mp4", dash = use,
            plainRes = if (width > 0 && height > 0) "${width}x$height" else "")
    }

    fun buildPost(item: JSONObject, content: String, kind: String = KIND_POST, subdir: String = "", username: String = ""): Post {
        val owner = ownerOf(item)
        val post = Post(kind)
        post.key = mediaKey(item)
        post.code = pyStr(or(opt(item, "code"), opt(item, "shortcode"),
            Links.shortcodeFromId(or(opt(item, "pk"), opt(item, "id"))), ""))
        post.username = pyStr(or(opt(owner, "username"), username, ""))
        post.authorName = pyStr(or(opt(owner, "full_name"), ""))
        post.takenAt = takenAtOf(item)
        post.pinned = isPinned(item)
        post.stem = if (kind == KIND_POST) {
            Naming.postStem(post.code.ifEmpty { post.key }, captionOf(item), post.takenAt)
        } else {
            val pk = pyStr(or(opt(item, "pk"), pyStr(or(opt(item, "id"), "")).substringBefore('_'), post.key))
            "${Naming.dateFromTimestamp(post.takenAt)}_$pk"
        }
        post.ownerId = pyStr(or(opt(owner, "pk"), opt(owner, "id"), opt(owner, "pk_id"), ""))
        val prefix = if (subdir.isNotEmpty()) "$subdir/" else ""
        val nodes = children(item).ifEmpty { listOf(item) }
        var unmatched = 0
        var missingVideo = 0
        for ((index0, node) in nodes.withIndex()) {
            val index = index0 + 1
            val name = if (nodes.size > 1) "${post.stem}_${index.toString().padStart(2, '0')}" else post.stem
            val media = fileFor(node, "$prefix$name")
            if (kind == KIND_POST) {
                media.relStem = "${if (media.kind == "video") VIDEOS_DIR else PHOTOS_DIR}/$name"
                media.legacyStem = name
            }
            if (!wanted(media.kind, content)) { unmatched++; continue }
            if (media.urls.isEmpty() && media.dash == null) { if (media.kind == "video") missingVideo++; continue }
            post.files += media
        }
        if (post.files.isEmpty()) {
            post.skippedReason = when {
                unmatched > 0 -> msg(if (content == CONTENT_VIDEO) "skip.video_only" else "skip.image_only")
                missingVideo > 0 -> msg("skip.no_video")
                else -> msg("skip.no_media")
            }
        }
        return post
    }

    fun avatarUrls(user: JSONObject): List<String> {
        val urls = mutableListOf<Any?>()
        val info = opt(user, "hd_profile_pic_url_info")
        if (info is JSONObject && truthy(opt(info, "url"))) urls += opt(info, "url")
        val versions = opt(user, "hd_profile_pic_versions")
        if (versions is JSONArray) {
            objects(versions).filter { truthy(opt(it, "url")) }.withIndex()
                .sortedWith(compareBy({ -area(it.value) }, { it.index })).forEach { urls += opt(it.value, "url") }
        }
        for (k in listOf("profile_pic_url_hd", "profile_pic_url")) if (truthy(opt(user, k))) urls += opt(user, k)
        return dedupe(urls)
    }

    fun buildAvatar(user: JSONObject, urlsIn: List<String>, content: String): Post {
        val post = Post(KIND_AVATAR)
        post.username = pyStr(or(opt(user, "username"), ""))
        post.authorName = pyStr(or(opt(user, "full_name"), ""))
        val urls = dedupe(urlsIn)
        val picId = pyStr(or(opt(user, "profile_pic_id"), "")).substringBefore('_')
        val name = picId.ifEmpty { if (urls.isNotEmpty()) Naming.sanitize(urlStem(urls[0]), 80, "profil") else "profil" }
        post.key = "avatar:$name"
        post.stem = name
        when {
            content == CONTENT_VIDEO -> post.skippedReason = msg("skip.image_only_avatar")
            urls.isEmpty() -> post.skippedReason = msg("skip.no_media")
            else -> post.files += MediaFile("image", urls, "$AVATAR_DIR/$name", "")
        }
        return post
    }

    fun authorFolder(username: String): String = Naming.sanitize(username.ifEmpty { "instagram" }, 60, "instagram")

    fun highlightFolder(title: Any?, highlightId: String): String {
        val name = Naming.sanitize(title, 50, "")
        return "$HIGHLIGHTS_DIR/${name.ifEmpty { highlightId }}"
    }
}

/** Liste sayfası takibi: sayfadaki gönderilerin hepsi inince imleç kaydedilir (kaldığı yerden devam için). */
class FeedPage(val cursorKind: String, var cursor: String) {
    val pending = HashSet<String>()
    var failed = false
}
