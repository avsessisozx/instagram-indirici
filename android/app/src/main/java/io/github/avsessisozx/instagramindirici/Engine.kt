package io.github.avsessisozx.instagramindirici

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.joinAll
import kotlinx.coroutines.launch
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.PrintWriter
import java.io.StringWriter
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.atomic.AtomicLong

/** Windows sürümündeki engine.py'nin karşılığı. Arayüz snapshot() ile durumu okur. */
class Engine(
    private val webProvider: () -> IgWeb?,
    private val sinkFactory: () -> MediaSink,
    private val historyProvider: () -> History?,
    private val scope: CoroutineScope,
    private val workers: Int = 3,
    private val onLoginState: (Boolean) -> Unit = {},
) {
    data class Job(val link: String, val mode: String, val settings: SettingsValues)

    private val lock = Any()
    private val progressLock = Any()
    private var cancel = AtomicBoolean(false)

    @Volatile var running = false; private set
    @Volatile var phase = "idle"; private set
    @Volatile var mode = ""; private set
    @Volatile var verify = false
    @Volatile var loginWall = false; private set
    @Volatile var rateLimited = false; private set
    @Volatile var loggedIn: Boolean? = null; private set
    @Volatile var author = ""; private set
    @Volatile var folder = ""; private set
    @Volatile var collecting = false; private set
    @Volatile var summary: Msg? = null; private set
    @Volatile var current = ""; private set
    @Volatile var lastLocation: String? = null; private set
    @Volatile var waitUntil = 0L; private set
    @Volatile var waitKind = ""; private set
    val foundPosts = AtomicInteger()
    val postsDone = AtomicInteger()
    val postsSkipped = AtomicInteger()
    val postsUnavailable = AtomicInteger()
    val postsKnown = AtomicInteger()
    val filesTotal = AtomicInteger()
    val filesDone = AtomicInteger()
    val filesSkipped = AtomicInteger()
    val filesFailed = AtomicInteger()
    val currentBytes = AtomicLong()
    val currentTotal = AtomicLong()
    private val notes = mutableListOf<Msg>()

    private val logs = ArrayList<JSONObject>()
    private var logOffset = 0

    // iş başına durum
    private var history: History? = null
    private var useHistory = true
    private val known = HashSet<String>()
    private val enqueued = HashSet<String>()
    private val pages = ArrayDeque<FeedPage>()
    private var progressCtx: Triple<String, String, String>? = null
    private var jumped = false
    private var pendingComplete: (() -> Unit)? = null

    private fun reset() {
        phase = "idle"; mode = ""; verify = false; loginWall = false; rateLimited = false; loggedIn = null
        author = ""; folder = ""; collecting = false; summary = null; current = ""; lastLocation = null
        waitUntil = 0; waitKind = ""
        for (a in listOf(foundPosts, postsDone, postsSkipped, postsUnavailable, postsKnown, filesTotal, filesDone, filesSkipped, filesFailed)) a.set(0)
        currentBytes.set(0); currentTotal.set(0)
        synchronized(progressLock) {
            known.clear(); enqueued.clear(); pages.clear()
            progressCtx = null; jumped = false; pendingComplete = null
        }
        synchronized(lock) {
            notes.clear()
            logOffset += logs.size
            logs.clear()
        }
    }

    // ------------------------------------------------------------ kayıt
    fun log(level: String, m: Msg) = addLog(level, m.toJson(), null)
    fun logText(level: String, text: String) = addLog(level, null, text)

    private fun addLog(level: String, m: JSONObject?, text: String?) {
        val entry = JSONObject()
            .put("t", SimpleDateFormat("HH:mm:ss", Locale.ROOT).format(Date()))
            .put("level", level)
        if (m != null) entry.put("msg", m) else entry.put("text", text ?: "")
        synchronized(lock) {
            logs.add(entry)
            if (logs.size > 3000) {
                val drop = logs.size - 2000
                logs.subList(0, drop).clear()
                logOffset += drop
            }
        }
    }

    private fun note(m: Msg, level: String) {
        synchronized(lock) { notes += m }
        log(level, m)
    }

    fun logKeys(): List<String> = synchronized(lock) { logs.mapNotNull { it.optJSONObject("msg")?.optString("key") } }
    fun noteKeys(): List<String> = synchronized(lock) { notes.map { it.key } }

    fun snapshot(since: Int = 0): JSONObject {
        val arr = JSONArray()
        val noteArr = JSONArray()
        val next: Int
        synchronized(lock) {
            val start = (since - logOffset).coerceAtLeast(0)
            for (i in start until logs.size) arr.put(logs[i])
            next = logOffset + logs.size
            for (n in notes) noteArr.put(n.toJson())
        }
        val waiting = if (waitUntil > 0) ((waitUntil - System.currentTimeMillis()) / 1000).coerceAtLeast(0) else 0
        return JSONObject()
            .put("running", running)
            .put("phase", phase)
            .put("mode", mode)
            .put("verify", verify)
            .put("login_wall", loginWall)
            .put("rate_limited", rateLimited)
            .put("logged_in", loggedIn ?: JSONObject.NULL)
            .put("author", author)
            .put("folder", folder)
            .put("found_posts", foundPosts.get())
            .put("posts_done", postsDone.get())
            .put("posts_skipped", postsSkipped.get())
            .put("posts_unavailable", postsUnavailable.get())
            .put("posts_known", postsKnown.get())
            .put("waiting", waiting)
            .put("wait_kind", waitKind)
            .put("collecting", collecting)
            .put("files_total", filesTotal.get())
            .put("files_done", filesDone.get())
            .put("files_skipped", filesSkipped.get())
            .put("files_failed", filesFailed.get())
            .put("current", current)
            .put("current_bytes", currentBytes.get())
            .put("current_total", currentTotal.get())
            .put("summary", summary?.toJson() ?: JSONObject.NULL)
            .put("notes", noteArr)
            .put("logs", arr)
            .put("log_next", next)
    }

    // ------------------------------------------------------------ kontrol
    fun start(job: Job): Msg? {
        synchronized(lock) { if (running) return msg("engine.already_running") }
        if (job.mode != MODE_LOGIN) {
            val p = Links.parse(job.link)
            if (!p.ok) return p.error
            if (job.mode == MODE_SINGLE && p.kind == Links.PROFILE) return msg("engine.single_needs_post")
        }
        synchronized(lock) {
            if (running) return msg("engine.already_running")
            running = true
        }
        reset()
        cancel = AtomicBoolean(false)
        phase = "starting"
        mode = job.mode
        val c = cancel
        scope.launch { runJob(job, c) }
        return null
    }

    fun stop() {
        if (running && !cancel.get()) {
            log("warn", msg("engine.stopping"))
            cancel.set(true)
        }
    }

    private suspend fun runJob(job: Job, cancel: AtomicBoolean) {
        val settings = job.settings
        useHistory = settings.history
        history = try { historyProvider() } catch (e: Exception) {
            log("warn", msg("engine.history_failed", "error" to (e.message ?: e.javaClass.simpleName))); null
        }
        var web: IgWeb? = null
        try {
            web = webProvider() ?: throw IgError(msg("android.no_window"))
            web.cancel = cancel
            web.listener = object : WebListener {
                override fun log(level: String, m: Msg) = this@Engine.log(level, m)
                override fun logText(level: String, text: String) = this@Engine.logText(level, text)
                override fun verify(active: Boolean) { this@Engine.verify = active }
                override fun waitUntil(epochMs: Long, kind: String) { this@Engine.waitUntil = epochMs; this@Engine.waitKind = kind }
            }
            web.prepare()
            if (job.mode == MODE_LOGIN) {
                val ok = web.loginSession()
                loggedIn = ok
                onLoginState(ok)
                phase = "done"
                summary = msg(if (ok) "engine.login_done_ok" else "engine.login_done")
                log("ok", summary!!)
                return
            }
            downloadJob(web, job, settings, cancel)
        } catch (e: IgError) {
            if (cancel.get()) finishStopped() else {
                if (e is LoginRequired) loginWall = true
                if (e is RateLimited) rateLimited = true
                phase = "error"; summary = e.m; log("error", e.m)
            }
        } catch (e: CancelledException) {
            finishStopped()
        } catch (e: CancellationException) {
            finishStopped()
        } catch (e: Throwable) {
            if (cancel.get()) finishStopped() else {
                log("error", msg("engine.unexpected", "error" to (e.message ?: e.javaClass.simpleName)))
                val sw = StringWriter(); e.printStackTrace(PrintWriter(sw)); logText("debug", sw.toString())
                phase = "error"
                summary = msg("engine.unexpected_summary")
            }
        } finally {
            web?.listener = null
            try { history?.flush() } catch (e: Exception) {}
            verify = false
            collecting = false
            current = ""
            waitUntil = 0
            running = false
        }
    }

    private fun finishStopped() {
        phase = "stopped"
        summary = msg("engine.stopped", "n" to filesDone.get())
        log("warn", summary!!)
    }

    private suspend fun downloadJob(web: IgWeb, job: Job, s: SettingsValues, cancel: AtomicBoolean) {
        val insta = Insta(web, Pacing.forSpeed(s.speed))
        var parsed = Links.parse(job.link)
        val li = web.loggedIn()
        loggedIn = li
        onLoginState(li)
        log("info", msg(if (li) "engine.account_yes" else "engine.account_no"))
        val identityName = msg(when (s.identity) {
            Identity.PHONE -> "ui.identity_android"
            Identity.TABLET -> "ui.identity_tablet"
            else -> "ui.identity_desktop"
        })
        val speedName = msg(when (s.speed) {
            Pacing.CAREFUL -> "ui.speed_careful"
            Pacing.VERY_CAREFUL -> "ui.speed_very_careful"
            else -> "ui.speed_normal"
        })
        log("info", msg("engine.identity", "identity" to identityName, "speed" to speedName, "ua" to web.userAgent))

        val sink = sinkFactory()
        val queue = Channel<Media.Post?>(Channel.UNLIMITED)
        val pool = DownloadPool(this, web, sink, cancel)
        var collectError: IgError? = null

        coroutineScope {
            val ws = List(workers) { launch(Dispatchers.IO) { pool.worker(queue) } }
            try {
                phase = "collecting"
                if (parsed.kind == Links.SHARE) {
                    parsed = insta.resolveShare(parsed.url)
                    if (job.mode == MODE_SINGLE && parsed.kind == Links.PROFILE) throw IgError(msg("engine.share_to_profile"))
                }
                try {
                    if (job.mode == MODE_SINGLE) single(insta, parsed, queue, li)
                    else profile(insta, ownerOf(insta, parsed, li), s, queue, li)
                } catch (e: RateLimited) {
                    rateLimited = true
                    collectError = e
                    log("error", e.m)
                } catch (e: LoginRequired) {
                    if (filesTotal.get() == 0 && postsKnown.get() == 0) throw e
                    loginWall = true
                    collectError = e
                    log("error", e.m)
                }
                collecting = false
                repeat(workers) { queue.send(null) }
                phase = "downloading"
                ws.joinAll()
            } catch (e: Throwable) {
                queue.close()
                ws.forEach { it.cancel() }
                throw e
            }
        }

        if (cancel.get()) { finishStopped(); return }
        val ce = collectError
        if (filesFailed.get() == 0 && ce == null) pendingComplete?.invoke()
        if (ce != null && filesDone.get() == 0 && filesSkipped.get() == 0 && postsKnown.get() == 0) {
            phase = "error"; summary = ce.m; return
        }
        phase = "done"
        summary = msg("summary", "done" to filesDone.get(), "skipped" to filesSkipped.get(),
            "failed" to filesFailed.get(), "unmatched" to postsSkipped.get(),
            "unavailable" to postsUnavailable.get(), "known" to postsKnown.get())
        if (filesDone.get() == 0 && filesSkipped.get() == 0 && postsKnown.get() == 0) {
            phase = if (filesFailed.get() > 0 || foundPosts.get() == 0) "error" else "done"
            val first = synchronized(lock) { notes.firstOrNull() }
            if (foundPosts.get() == 0 && first != null) summary = first
        }
        log(if (phase == "done") "ok" else "error", summary!!)
    }

    // ------------------------------------------------------------ tek öğe
    private suspend fun single(insta: Insta, p: Links.Parsed, queue: Channel<Media.Post?>, li: Boolean) {
        val content = Media.CONTENT_ALL
        when (p.kind) {
            Links.POST -> {
                val item = insta.getPost(p.shortcode, li)
                author = Media.pyStr(Media.or(Media.opt(Media.ownerOf(item), "username"), ""))
                enqueue(queue, Media.buildPost(item, content, username = author), content = content)
            }
            Links.STORY -> {
                if (!li) throw LoginRequired(msg("engine.stories_need_login"))
                val info = insta.openProfile(p.username, li)
                author = info.username
                var items = insta.stories(info.userId)
                if (p.storyMediaId.isNotEmpty()) {
                    items = items.filter { Media.pyStr(Media.or(Media.opt(it, "pk"), Media.pyStr(Media.or(Media.opt(it, "id"), "")).substringBefore('_'))) == p.storyMediaId }
                }
                if (items.isEmpty()) throw NotFound(msg("engine.no_stories"))
                for (item in items) enqueue(queue, Media.buildPost(item, content, Media.KIND_STORY, Media.STORIES_DIR, info.username),
                    content = content, userId = info.userId)
            }
            Links.HIGHLIGHT -> {
                if (!li) throw LoginRequired(msg("engine.highlights_need_login"))
                val reels = insta.highlightReels(listOf("highlight:${p.highlightId}"))
                if (reels.isEmpty() || reels[0].items.isEmpty()) throw NotFound(msg("engine.highlight_not_found"))
                for (reel in reels) {
                    if (author.isEmpty()) author = Media.pyStr(Media.or(Media.opt(reel.user, "username"), ""))
                    val folder = Media.highlightFolder(reel.title, p.highlightId)
                    for (item in reel.items) enqueue(queue, Media.buildPost(item, content, Media.KIND_HIGHLIGHT, folder, author), content = content)
                }
            }
            else -> throw IgError(msg("engine.single_needs_post"))
        }
    }

    private suspend fun ownerOf(insta: Insta, p: Links.Parsed, li: Boolean): String {
        if (p.kind == Links.PROFILE || p.kind == Links.STORY) return p.username
        log("info", msg("engine.finding_owner"))
        val name = when (p.kind) {
            Links.POST -> Media.pyStr(Media.or(Media.opt(Media.ownerOf(insta.getPost(p.shortcode, li)), "username"), ""))
            Links.HIGHLIGHT -> {
                if (!li) throw LoginRequired(msg("engine.highlights_need_login"))
                val reels = insta.highlightReels(listOf("highlight:${p.highlightId}"))
                Media.pyStr(Media.or(Media.opt(reels.firstOrNull()?.user, "username"), ""))
            }
            else -> ""
        }
        if (name.isEmpty()) throw IgError(msg("engine.owner_not_found"))
        return name.lowercase()
    }

    // ------------------------------------------------------------ profil
    private suspend fun profile(insta: Insta, username: String, s: SettingsValues, queue: Channel<Media.Post?>, li: Boolean) {
        val content = s.content
        val info = insta.openProfile(username, li)
        author = info.username
        folder = MediaStoreSink.BASE + Media.authorFolder(info.username)
        val label = if (info.fullName.isNotEmpty()) "${info.fullName} (@${info.username})" else "@${info.username}"
        if (info.mediaCount >= 0) log("info", msg("engine.profile_found_count", "name" to label, "n" to info.mediaCount))
        else log("info", msg("engine.profile_found", "name" to label))
        logText("debug", "user_id=${info.userId} sources=${info.sources.joinToString(",")}")
        val h = history
        if (h != null && useHistory) {
            val k = h.known(info.userId, content)
            synchronized(progressLock) { known.addAll(k) }
            if (k.isNotEmpty()) log("info", msg("engine.history_known", "n" to k.size))
        }

        if (s.avatar) {
            try {
                val urls = insta.avatarUrls(info, li)
                enqueue(queue, Media.buildAvatar(info.user, urls, content), quietSkip = true)
            } catch (e: IgError) {
                if (e is LoginRequired) throw e
                log("warn", msg("engine.avatar_failed", "error" to e.m))
            }
        }
        if (info.locked) {
            note(msg(if (li) "engine.private_logged_in" else "engine.private"), "warn")
            return
        }
        if (s.stories) stories(insta, info, content, queue, li)
        if (s.highlights) highlights(insta, info, content, queue, li)
        if (s.posts) posts(insta, info, s, queue, li)
        if (!(s.posts || s.stories || s.highlights || s.avatar)) synchronized(lock) { notes += msg("engine.nothing_selected") }
    }

    private suspend fun stories(insta: Insta, info: ProfileInfo, content: String, queue: Channel<Media.Post?>, li: Boolean) {
        if (!li) { log("info", msg("engine.stories_skipped_login")); return }
        val items = try {
            insta.stories(info.userId)
        } catch (e: LoginRequired) {
            log("warn", msg("browser.session_expired")); return
        } catch (e: IgError) {
            log("warn", msg("engine.stories_failed", "error" to e.m)); return
        }
        if (items.isEmpty()) { log("info", msg("engine.no_stories_now")); return }
        log("info", msg("engine.stories_found", "n" to items.size))
        for (item in items) enqueue(queue, Media.buildPost(item, content, Media.KIND_STORY, Media.STORIES_DIR, info.username),
            content = content, userId = info.userId)
    }

    private suspend fun highlights(insta: Insta, info: ProfileInfo, content: String, queue: Channel<Media.Post?>, li: Boolean) {
        if (!li) { log("info", msg("engine.highlights_skipped_login")); return }
        val failed = mutableListOf<String>()
        val titles = HashMap<String, String>()
        val reels = try {
            val tray = insta.highlightsTray(info.userId, info.username)
            if (tray.isEmpty()) { log("info", msg("engine.no_highlights")); return }
            log("info", msg("engine.highlights_found", "n" to tray.size))
            for (h in tray) titles[h.optString("id")] = h.optString("title")
            val expected = tray.associate { it.optString("id") to it.optInt("media_count", 0) }
            insta.highlightReels(tray.map { it.optString("id") }, expected, failed)
        } catch (e: LoginRequired) {
            log("warn", msg("browser.session_expired")); return
        } catch (e: IgError) {
            log("warn", msg("engine.highlights_failed", "error" to e.m)); return
        }
        for (reel in reels) {
            val title = reel.title.ifEmpty { titles[reel.id] ?: "" }
            val folder = Media.highlightFolder(title, reel.id.substringAfterLast(':'))
            log("info", msg("engine.highlight_items", "title" to title.ifEmpty { reel.id.substringAfterLast(':') }, "n" to reel.items.size))
            for (item in reel.items) enqueue(queue, Media.buildPost(item, content, Media.KIND_HIGHLIGHT, folder, info.username),
                content = content, userId = info.userId)
        }
        if (failed.isNotEmpty()) {
            val names = failed.joinToString(", ") { (titles[it] ?: "").ifEmpty { it.substringAfterLast(':') } }
            note(msg("engine.highlights_partial", "n" to failed.size, "names" to names), "warn")
        }
    }

    private suspend fun posts(insta: Insta, info: ProfileInfo, s: SettingsValues, queue: Channel<Media.Post?>, li: Boolean) {
        val content = s.content
        val limit = s.limit
        val uid = info.userId
        val h = history
        val newestKnown = if (s.onlyNew && h != null) h.completeNewest(uid, content) else 0L
        if (newestKnown > 0) log("info", msg("engine.only_new"))
        var resume: Pair<String, String>? = null
        if (h != null && useHistory && newestKnown == 0L && limit == 0) {
            resume = h.resumePoint(uid, content)
            if (resume != null) log("info", msg("engine.resume_found"))
        }
        if (limit == 0) synchronized(progressLock) { progressCtx = Triple(uid, content, info.username) }
        var newestSeen = 0L
        val repairs = mutableListOf<Media.Post>()
        val repairKeys = HashSet<String>()

        val onPage: (List<JSONObject>, String, String) -> String = { items, cursorKind, nextCursor ->
            val page = FeedPage(cursorKind, nextCursor)
            synchronized(progressLock) { pages.addLast(page) }
            var reachedOld = false
            var regular = 0
            var regularKnown = 0
            for (item in items) {
                val ts = Media.takenAtOf(item)
                val post = Media.buildPost(item, content, username = info.username)
                val isKnown = synchronized(progressLock) { post.key in known }
                if (!Media.isPinned(item)) {
                    newestSeen = maxOf(newestSeen, ts)
                    if (newestKnown > 0 && ts > 0 && ts <= newestKnown) reachedOld = true
                    regular++
                    if (isKnown || post.skippedReason != null) regularKnown++
                }
                if (needsRepair(item, post) && !isKnown) {
                    if (repairKeys.add(post.key)) repairs += post
                    continue
                }
                enqueue(queue, post, content = content, userId = uid, page = page)
            }
            advancePages()
            val r = resume
            when {
                reachedOld -> "stop"
                r != null && !jumped && nextCursor.isNotEmpty() && r.first == cursorKind && regular > 0 && regularKnown == regular -> {
                    jumped = true
                    page.cursor = r.second
                    "jump"
                }
                else -> ""
            }
        }

        var rounds = 0
        var result: FeedResult
        while (true) {
            collecting = true
            result = try {
                insta.collectPosts(info, li, limit, onPage, resume)
            } catch (e: RateLimited) {
                FeedResult(0, "rate_limited")
            }
            logText("debug", "posts via=${result.via} reason=${result.reason} n=${result.collected}")
            if (result.reason == "rate_limited" && s.autoContinue && rounds < maxRounds()) {
                rounds++
                waitAndContinue(rounds)
                if (h != null && useHistory) {
                    val k = h.known(uid, content)
                    synchronized(progressLock) { known.addAll(k) }
                }
                resume = if (h != null && limit == 0) h.resumePoint(uid, content) else null
                jumped = false
                continue
            }
            break
        }
        if (result.reason == "rate_limited" && result.collected == 0 && filesTotal.get() == 0 && postsKnown.get() == 0) {
            collecting = false
            throw RateLimited("feed")
        }
        reportPosts(result, info)
        if (repairs.isNotEmpty()) repair(insta, repairs, content, queue, li, info.username, uid)
        collecting = false

        if ((result.reason == "end" || result.reason == "up_to_date") && limit == 0) {
            var newest = newestSeen
            if (result.reason == "up_to_date") newest = maxOf(newest, newestKnown)
            pendingComplete = { h?.setComplete(uid, content, newest, info.username) }
        }
    }

    private suspend fun waitAndContinue(round: Int) {
        val seconds = resumeWait()
        log("warn", msg("engine.auto_wait", "m" to maxOf(1L, Math.round(seconds / 60.0)), "n" to round, "total" to maxRounds()))
        waitUntil = System.currentTimeMillis() + seconds * 1000
        waitKind = "rate"
        try {
            val end = System.currentTimeMillis() + seconds * 1000
            while (System.currentTimeMillis() < end) {
                if (cancel.get()) throw CancelledException()
                delay(minOf(1000L, end - System.currentTimeMillis()).coerceAtLeast(1))
            }
        } finally {
            waitUntil = 0; waitKind = ""
        }
        log("info", msg("engine.auto_resume"))
    }

    private suspend fun repair(insta: Insta, repairs: List<Media.Post>, content: String, queue: Channel<Media.Post?>,
                               li: Boolean, username: String, userId: String) {
        log("info", msg("engine.repairing", "n" to repairs.size))
        for (post in repairs) {
            if (cancel.get()) return
            try {
                val item = insta.getPost(post.code, li)
                val fixed = Media.buildPost(item, content, username = username)
                if (fixed.skippedReason == null) { enqueue(queue, fixed, content = content, userId = userId); continue }
            } catch (e: IgError) {
                logText("debug", "repair ${post.code}: ${e.m.key}")
            }
            enqueue(queue, post, content = content, userId = userId)
        }
    }

    private fun reportPosts(r: FeedResult, info: ProfileInfo) {
        val n = r.collected; val total = info.mediaCount
        when {
            r.reason == "session_expired" -> { loginWall = true; note(msg("engine.session_expired_partial", "n" to n), "warn") }
            r.reason == "rate_limited" -> { rateLimited = true; note(msg("engine.rate_limited_partial", "n" to n), "warn") }
            r.reason == "login_wall" -> {
                loginWall = true
                note(msg(if (total > 0) "engine.login_wall_total" else "engine.login_wall", "n" to n, "total" to total), "warn")
            }
            n == 0 && (r.reason == "end" || r.reason == "stalled") -> {
                val empty = total == 0 || (total < 0 && r.reason == "end")
                note(msg(if (empty) "engine.empty" else "engine.no_list"), if (empty) "info" else "error")
            }
            r.reason == "limit" -> log("info", msg("engine.limit_reached", "n" to n))
            r.reason == "up_to_date" -> log("ok", msg("engine.up_to_date", "n" to n))
            total > 0 -> log("ok", msg("engine.profile_scanned_total", "n" to n, "total" to total))
            else -> log("ok", msg("engine.profile_scanned", "n" to n))
        }
    }

    // ------------------------------------------------------------ kuyruk
    private fun enqueue(queue: Channel<Media.Post?>, post: Media.Post, quietSkip: Boolean = false, content: String = "",
                        userId: String = "", page: FeedPage? = null) {
        if (post.key.isEmpty()) return
        if (userId.isNotEmpty() && post.ownerId.isEmpty()) post.ownerId = userId
        synchronized(progressLock) { if (!enqueued.add("${post.kind}:${post.key}")) return }
        if (post.kind == Media.KIND_POST) foundPosts.incrementAndGet()
        if (author.isEmpty() && post.username.isNotEmpty()) author = post.username
        val reason = post.skippedReason
        if (reason != null) {
            if (reason.key in UNMATCHED_REASONS) { if (post.kind != Media.KIND_AVATAR) postsSkipped.incrementAndGet() }
            else postsUnavailable.incrementAndGet()
            if (!quietSkip) log("skip", msg("engine.skipped_post", "reason" to reason, "name" to post.stem))
            return
        }
        val isKnown = synchronized(progressLock) { post.kind != Media.KIND_AVATAR && post.key in known }
        if (isKnown) { postsKnown.incrementAndGet(); return }
        post.historyContent = content
        if (page != null) {
            post.page = page
            synchronized(progressLock) { page.pending += post.key }
        }
        filesTotal.addAndGet(post.files.size)
        queue.trySend(post)
    }

    /** Tamamen inen sayfaların imlecini kaydeder (yarıda kalırsa oradan devam etmek için). */
    private fun advancePages() {
        synchronized(progressLock) {
            while (pages.isNotEmpty() && pages.first().pending.isEmpty() && !pages.first().failed) {
                val page = pages.removeFirst()
                val ctx = progressCtx
                if (ctx != null && page.cursor.isNotEmpty() && page.cursorKind.isNotEmpty()) {
                    history?.setResumePoint(ctx.first, ctx.second, page.cursorKind, page.cursor, ctx.third)
                }
            }
        }
    }

    internal fun postFinished(post: Media.Post, ok: Boolean) {
        if (ok && post.kind != Media.KIND_AVATAR && post.ownerId.isNotEmpty()) {
            history?.mark(post.ownerId, post.key, post.historyContent.ifEmpty { Media.CONTENT_ALL }, post.kind, post.username)
            if (post.historyContent.isNotEmpty()) synchronized(progressLock) { known += post.key }
        }
        val page = post.page ?: return
        synchronized(progressLock) {
            page.pending.remove(post.key)
            if (!ok) page.failed = true
        }
        advancePages()
    }

    internal fun setCurrent(name: String) { current = name }
    internal fun setLastLocation(l: String?) { if (l != null) lastLocation = l }
    internal fun isCancelled() = cancel.get()

    companion object {
        const val MODE_SINGLE = "single"
        const val MODE_PROFILE = "profile"
        const val MODE_LOGIN = "login"
        val UNMATCHED_REASONS = setOf("skip.video_only", "skip.image_only", "skip.image_only_avatar")

        fun resumeWait(): Long = TestHooks.resumeWaitSeconds ?: 900L
        fun maxRounds(): Int = TestHooks.resumeRounds ?: 4

        /** Eski biçimdeki listelerde video adresi bazen gelmiyor; gönderi tek başına açılınca geliyor. */
        fun needsRepair(item: JSONObject, post: Media.Post): Boolean =
            post.skippedReason?.key == "skip.no_video" && !item.has("video_versions") && post.code.isNotEmpty()
    }
}

/** İndirme işçileri. Windows sürümündeki DownloadPool'un karşılığı. */
class DownloadPool(
    private val eng: Engine,
    private val web: IgWeb,
    private val sink: MediaSink,
    private val cancel: AtomicBoolean,
) {
    private val headers: Map<String, String> get() = linkedMapOf(
        "User-Agent" to web.userAgent,
        "Referer" to "https://www.instagram.com/",
        "Accept" to "*/*",
    )

    suspend fun worker(queue: Channel<Media.Post?>) {
        for (post in queue) {
            if (post == null) return
            if (cancel.get()) continue
            try {
                downloadPost(post)
            } catch (e: CancelledException) {
                return
            }
        }
    }

    private fun downloadPost(post: Media.Post) {
        val folder = Media.authorFolder(post.username.ifEmpty { eng.author })
        val results = mutableListOf<String>()
        for (media in post.files) {
            if (cancel.get()) return
            results += downloadFile(folder, media)
        }
        eng.postsDone.incrementAndGet()
        val ok = results.isNotEmpty() && results.none { it == "failed" }
        eng.postFinished(post, ok)
        if (ok) {
            val key = when {
                results.any { it == "downloaded" } -> when (post.kind) {
                    Media.KIND_STORY -> "engine.downloaded_story"
                    Media.KIND_HIGHLIGHT -> "engine.downloaded_highlight"
                    Media.KIND_AVATAR -> "engine.downloaded_avatar"
                    else -> if (post.isGallery) "engine.downloaded_gallery"
                    else if (post.files.any { it.kind == "video" }) "engine.downloaded_video" else "engine.downloaded_image"
                }
                else -> "engine.already_had"
            }
            eng.log("ok", msg(key, "name" to post.stem))
        }
    }

    private fun downloadFile(folder: String, media: Media.MediaFile): String {
        val rel = "$folder/${media.relStem}"
        if (sink.exists(rel, media.ext)) { eng.filesSkipped.incrementAndGet(); return "skipped" }
        if (media.legacyStem.isNotEmpty() && sink.moveLegacy("$folder/${media.legacyStem}", rel, media.ext)) {
            eng.filesSkipped.incrementAndGet(); return "skipped"
        }
        eng.setCurrent(rel.substringAfterLast('/'))
        eng.currentBytes.set(0)
        eng.currentTotal.set(media.expectedSize)
        val fallbackExt = if (media.kind == "video") ".mp4" else ".jpg"
        val progress = { done: Long, total: Long ->
            eng.currentBytes.set(done)
            eng.currentTotal.set(if (total > 0) total else media.expectedSize)
        }
        val dash = media.dash
        if (media.kind == "video" && dash != null) {
            // En yüksek kalite: Instagram'ın uygulama içinde oynattığı ayrı ses + görüntü akışı.
            eng.log("info", msg("engine.quality_offered", "list" to dash.offered))
            val tempDir = File(System.getProperty("java.io.tmpdir") ?: ".", "ii-dash")
            // Telefon bu görüntü biçimini mp4'e koyamıyorsa (VP9/AV1) boşuna indirme.
            val got = if (!dash.video.muxable && media.urls.isNotEmpty() && TestHooks.muxer == null)
                Fetcher.Result(false, error = msg("fetch.mux_failed", "error" to "${dash.video.codecs} (telefon birleştiremiyor)"))
            else Fetcher.fetchDash(dash, rel, headers, sink, cancel, progress, tempDir, TestHooks.muxer ?: AndroidMuxer)
            if (got.ok) {
                eng.filesDone.incrementAndGet()
                eng.setLastLocation(got.location)
                eng.log("info", msg("engine.quality_dash", "res" to "${dash.video.width}x${dash.video.height}",
                    "mbps" to String.format(Locale.US, "%.1f", dash.video.bandwidth / 1_000_000.0)))
                return "downloaded"
            }
            if (cancel.get()) return "failed"
            eng.log("warn", msg("engine.quality_fallback", "res" to media.plainRes.ifEmpty { "?" }, "error" to (got.error ?: "")))
            if (media.urls.isEmpty()) {
                eng.filesFailed.incrementAndGet()
                eng.log("error", msg("engine.download_failed", "name" to rel.substringAfterLast('/'), "error" to (got.error ?: "")))
                return "failed"
            }
        }
        val result = Fetcher.fetch(media.urls, rel, media.ext, fallbackExt, headers, sink, cancel, progress)
        if (result.ok) {
            eng.filesDone.incrementAndGet()
            eng.setLastLocation(result.location)
            if (media.kind == "video" && dash == null) {
                eng.log("info", msg("engine.quality_plain", "res" to media.plainRes.ifEmpty { "?" }))
            }
            return "downloaded"
        }
        if (cancel.get()) return "failed"
        eng.filesFailed.incrementAndGet()
        eng.log("error", msg("engine.download_failed", "name" to rel.substringAfterLast('/'), "error" to (result.error ?: "")))
        return "failed"
    }
}
