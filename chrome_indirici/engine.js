// Instagram İndirici (Chrome eklentisi) – indirme motoru. Denetim sayfasında (panel.html) çalışır.
(function (root) {
  "use strict";
  const C = root.IICore;
  const VERSION = chrome.runtime.getManifest().version;
  const DEFAULT_IG = "https://www.instagram.com";
  let IG = DEFAULT_IG;

  const DEFAULTS = {
    folder: "Instagram Indirici", content: "all", limit: 0,
    posts: true, stories: true, highlights: true, avatar: true, reels: true, reposts: false, tagged: false,
    only_new: true, history: true, verify_files: true, caption_min: 300, info_csv: true,
    date_from: "", date_to: "", speed: "normal",
  };

  class Cancelled extends Error {}
  class Problem extends Error {}   // kullanıcıya olduğu gibi gösterilecek hata

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  // ------------------------------------------------------------ geçmiş (IndexedDB)
  const DB = {
    db: null,
    open() {
      if (this.db) return Promise.resolve(this.db);
      return new Promise((resolve, reject) => {
        const req = indexedDB.open("instagram-indirici", 1);
        req.onupgradeneeded = () => {
          const db = req.result;
          db.createObjectStore("downloaded", { keyPath: "id" });     // id = uid|key
          db.createObjectStore("progress", { keyPath: "id" });       // id = uid|content
          db.createObjectStore("info", { keyPath: "id" });           // id = user|code
        };
        req.onsuccess = () => { this.db = req.result; resolve(this.db); };
        req.onerror = () => reject(req.error);
      });
    },
    async tx(store, mode, fn) {
      const db = await this.open();
      return new Promise((resolve, reject) => {
        const t = db.transaction(store, mode);
        const s = t.objectStore(store);
        let out;
        try { out = fn(s); } catch (e) { reject(e); return; }
        t.oncomplete = () => resolve(out && out.result !== undefined ? out.result : out);
        t.onerror = () => reject(t.error);
      });
    },
    get(store, id) { return this.tx(store, "readonly", (s) => s.get(id)); },
    put(store, value) { return this.tx(store, "readwrite", (s) => s.put(value)); },
    del(store, id) { return this.tx(store, "readwrite", (s) => s.delete(id)); },
    all(store) { return this.tx(store, "readonly", (s) => s.getAll()); },
    clear(store) { return this.tx(store, "readwrite", (s) => s.clear()); },
  };

  const History = {
    async known(uid) {
      if (!uid) return new Set();
      const rows = await DB.all("downloaded");
      return new Set(rows.filter((r) => r.uid === uid).map((r) => r.key));
    },
    mark(uid, key, kind, username) {
      if (!uid || !key) return Promise.resolve();
      return DB.put("downloaded", { id: uid + "|" + key, uid, key, kind, username, at: Date.now() });
    },
    async profiles() {
      const rows = await DB.all("downloaded");
      const map = new Map();
      for (const r of rows) {
        const e = map.get(r.uid) || { uid: r.uid, username: r.username || "", count: 0, last: 0 };
        e.count++; e.last = Math.max(e.last, r.at || 0); e.username = r.username || e.username;
        map.set(r.uid, e);
      }
      return [...map.values()].sort((a, b) => b.last - a.last);
    },
    async forget(uid) {
      const rows = await DB.all("downloaded");
      for (const r of rows) if (r.uid === uid) await DB.del("downloaded", r.id);
      const prog = await DB.all("progress");
      for (const r of prog) if (r.uid === uid) await DB.del("progress", r.id);
    },
    async clear() { await DB.clear("downloaded"); await DB.clear("progress"); },
    async newest(uid) { const r = await DB.get("progress", uid + "|posts"); return r ? r.newest || 0 : 0; },
    setComplete(uid, newest, username) {
      return DB.get("progress", uid + "|posts").then((old) => DB.put("progress", {
        id: uid + "|posts", uid, username, newest: Math.max(newest || 0, old ? old.newest || 0 : 0) }));
    },
  };

  // ------------------------------------------------------------ indirme (chrome.downloads)
  const waiters = new Map();
  chrome.downloads.onChanged.addListener((delta) => {
    const w = waiters.get(delta.id);
    if (!w) return;
    if (delta.state && delta.state.current === "complete") { waiters.delete(delta.id); w.resolve({ ok: true }); }
    else if (delta.state && delta.state.current === "interrupted") {
      waiters.delete(delta.id);
      w.resolve({ ok: false, error: (delta.error && delta.error.current) || "interrupted" });
    }
  });

  function startDownload(url, filename) {
    return new Promise((resolve) => {
      chrome.downloads.download({ url, filename, conflictAction: "overwrite", saveAs: false }, (id) => {
        if (chrome.runtime.lastError || id === undefined) {
          resolve({ ok: false, error: (chrome.runtime.lastError && chrome.runtime.lastError.message) || "başlatılamadı" });
          return;
        }
        // Zaten bitmiş olabilir
        waiters.set(id, { resolve });
        chrome.downloads.search({ id }, (items) => {
          const it = items && items[0];
          if (it && it.state === "complete" && waiters.has(id)) { waiters.delete(id); resolve({ ok: true }); }
          else if (it && it.state === "interrupted" && waiters.has(id)) { waiters.delete(id); resolve({ ok: false, error: it.error || "interrupted" }); }
        });
      });
    });
  }

  function existsOnDisk(relPath) {
    const pattern = relPath.replace(/[.*+?^${}()|[\]\\]/g, "\\$&").replace(/\//g, "[\\\\/]") + "$";
    return new Promise((resolve) => {
      chrome.downloads.search({ filenameRegex: pattern, state: "complete", exists: true, limit: 1 }, (items) => {
        resolve(!!(items && items.length && items[0].exists));
      });
    });
  }

  function extFromUrl(url, kind) {
    if (kind === "video") return ".mp4";
    try {
      const m = new URL(url).pathname.match(/\.(jpe?g|png|webp|heic|gif)$/i);
      if (m) return "." + m[1].toLowerCase().replace("jpeg", "jpg");
    } catch (e) {}
    return ".jpg";
  }

  // ------------------------------------------------------------ çalışma sekmesi (Instagram penceresi)
  const Work = {
    tabId: null, windowId: null,
    async ensure() {
      if (this.tabId !== null) {
        try { const t = await chrome.tabs.get(this.tabId); if (t) return this.tabId; } catch (e) {}
        this.tabId = null;
      }
      const win = await chrome.windows.create({ url: IG + "/", type: "normal", focused: false, width: 1100, height: 900 });
      this.windowId = win.id;
      this.tabId = win.tabs[0].id;
      return this.tabId;
    },
    async close() {
      if (this.windowId !== null) { try { await chrome.windows.remove(this.windowId); } catch (e) {} }
      this.tabId = null; this.windowId = null;
    },
    send(cmd, extra) {
      return new Promise((resolve) => {
        if (this.tabId === null) { resolve(null); return; }
        chrome.tabs.sendMessage(this.tabId, { ii: 1, cmd, ...(extra || {}) }, (res) => {
          if (chrome.runtime.lastError) resolve(null); else resolve(res || null);
        });
      });
    },
    async go(url, cancelled) {
      await this.ensure();
      await chrome.tabs.update(this.tabId, { url, active: false });
      // Yükleme bitene kadar bekle
      const start = Date.now();
      await new Promise((resolve) => {
        const done = () => { chrome.tabs.onUpdated.removeListener(on); clearTimeout(to); resolve(); };
        const on = (id, info) => { if (id === this.tabId && info.status === "complete") done(); };
        const to = setTimeout(done, 45000);
        chrome.tabs.onUpdated.addListener(on);
      });
      // Yardımcı betik hazır olana kadar
      for (let i = 0; i < 30; i++) {
        if (cancelled && cancelled()) throw new Cancelled();
        const r = await this.send("ping");
        if (r && r.ready) return r;
        await sleep(500);
      }
      throw new Problem("Instagram sayfası açılamadı (yardımcı betik yanıt vermedi). Sayfayı kapatıp tekrar dene.");
    },
  };

  // ------------------------------------------------------------ motor
  class Engine {
    constructor(onUpdate) {
      this.onUpdate = onUpdate || (() => {});
      this.reset();
    }

    reset() {
      this.running = false; this.cancel = false;
      this.log = []; this.notes = [];
      this.stats = { found: 0, postsDone: 0, postsSkipped: 0, unavailable: 0, filesTotal: 0, filesDone: 0,
        filesSkipped: 0, filesFailed: 0 };
      this.phase = "idle"; this.author = ""; this.summary = "";
      this.failures = [];
      this.saved = [];       // başarıyla kaydedilen göreli yollar (sınama ve rapor için)
    }

    say(level, text) {
      const d = new Date();
      const p = (n) => String(n).padStart(2, "0");
      this.log.push({ t: `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`, level, text });
      if (this.log.length > 3000) this.log.splice(0, 500);
      this.onUpdate();
    }
    setPhase(p) { this.phase = p; this.onUpdate(); }
    check() { if (this.cancel) throw new Cancelled(); }
    async nap(ms) {
      const end = Date.now() + ms;
      while (Date.now() < end) { this.check(); await sleep(Math.min(250, end - Date.now())); }
    }
    pace() {
      const f = { fast: 0.6, normal: 1, slow: 2 }[this.settings.speed] || 1;
      return this.nap((1200 + Math.random() * 1400) * f);
    }

    // ---------------------------------------------------------- giriş noktası
    async run(rawInput, settings) {
      if (this.running) return;
      this.reset();
      this.running = true;
      this.settings = { ...DEFAULTS, ...settings };
      IG = (settings && settings.base) || DEFAULT_IG;     // base yalnız sınama içindir
      this.setPhase("starting");
      const started = Date.now();
      try {
        try { chrome.downloads.setUiOptions && chrome.downloads.setUiOptions({ enabled: false }); } catch (e) {}
        const targets = [];
        for (const line of String(rawInput || "").split(/\r?\n|,|\s{2,}/).map((s) => s.trim()).filter(Boolean)) {
          const p = C.parseLink(line);
          if (!p.ok) { this.say("warn", `${line}: ${p.error}`); continue; }
          if (!targets.some((t) => JSON.stringify(t) === JSON.stringify(p))) targets.push(p);
        }
        if (!targets.length) throw new Problem("İndirilecek profil ya da bağlantı yazılmadı.");
        await this.ensureLogin();
        this.queue = []; this.inRun = new Set(); this.active = 0; this.producing = true; this.infoRows = {};
        const workers = Array.from({ length: 3 }, () => this.worker());
        try {
          for (const t of targets) {
            this.check();
            try { await this.target(t); }
            catch (e) { if (e instanceof Cancelled) throw e; this.say("error", e instanceof Problem ? e.message : `Hata: ${e && e.message || e}`); }
          }
        } finally {
          this.producing = false;
        }
        await Promise.all(workers);
        await this.writeInfo();
        const s = this.stats;
        this.summary = `Bitti. ${s.filesDone} dosya indirildi` + (s.filesSkipped ? `, ${s.filesSkipped} zaten vardı` : "") +
          (s.filesFailed ? `, ${s.filesFailed} dosya inemedi` : "") + ".";
        this.say("ok", this.summary);
      } catch (e) {
        if (e instanceof Cancelled) {
          this.cancel = true;
          this.producing = false;
          this.summary = `Durduruldu. ${this.stats.filesDone} dosya indirildi.`;
          this.say("warn", this.summary);
        } else {
          this.summary = e instanceof Problem ? e.message : `Hata: ${e && e.message || e}`;
          this.say("error", this.summary);
        }
      } finally {
        this.running = false;
        this.setPhase("stopped");
        this.say("debug", `süre ${Math.round((Date.now() - started) / 1000)} sn`);
        try { chrome.downloads.setUiOptions && chrome.downloads.setUiOptions({ enabled: true }); } catch (e) {}
      }
    }

    stop() { this.cancel = true; this.say("warn", "Durduruluyor…"); }

    async ensureLogin() {
      this.say("info", "Instagram penceresi açılıyor…");
      const r = await Work.go(IG + "/", () => this.cancel);
      await this.nap(2500);
      const p = (await Work.send("ping")) || r;
      if (p.loginWall) {
        throw new Problem("Instagram'a giriş yapılmamış görünüyor. Açılan Instagram penceresinde giriş yap, sonra tekrar Başlat'a bas.");
      }
      this.say("info", "Instagram penceresi hazır. İş bitene kadar bu pencereyi kapatma ve küçültme.");
    }

    // ---------------------------------------------------------- hedef türleri
    async target(t) {
      if (t.kind === "profile") return this.profile(t.username);
      if (t.kind === "story") return this.profile(t.username, { only: "stories" });
      if (t.kind === "post") return this.singlePost(t.shortcode);
      if (t.kind === "highlight") return this.singleHighlight(t.highlightId);
    }

    // Sayfayı açıp veri toplar. onItems(yeni kayıtlar) "stop" dönerse biter.
    async collect(url, o) {
      const p = await Work.go(url, () => this.cancel);
      if (p.loginWall) throw new Problem("Instagram giriş istedi. Penceredeki oturum kapanmış olabilir; yeniden giriş yapıp tekrar dene.");
      if (p.notFound && !o.allowNotFound) throw new Problem("Sayfa bulunamadı (profil yok ya da gizli).");
      const t0 = Date.now();
      let m = 0, h = 0, total = 0, last429 = 0;
      const maxMs = (o.maxSec || 20) * 1000;
      const idleMs = (o.idleSec || 3) * 1000;
      let waitedChallenge = false;
      let result = { reason: "", user: null, state: p, hlTotal: 0 };
      for (;;) {
        this.check();
        const r = await Work.send("take", { m, h, username: o.username, links: o.links, html: false });
        if (!r) { await this.nap(800); if (Date.now() - t0 > maxMs + 10000) { result.reason = "no-bridge"; break; } continue; }
        result.state = r;
        if (r.user) result.user = r.user;
        m = r.mediaTotal; h = r.hlTotal;
        result.hlTotal = h;
        if (r.challenge) {
          if (!waitedChallenge) { this.say("warn", "Instagram doğrulama istiyor. Açılan pencerede doğrulamayı tamamla; bekliyorum…"); waitedChallenge = true; }
          await this.nap(3000);
          if (Date.now() - t0 > 15 * 60 * 1000) { result.reason = "challenge"; break; }
          continue;
        }
        if (r.loginWall) throw new Problem("Instagram giriş istedi. Yeniden giriş yapıp tekrar dene.");
        if (r.status429 > last429) { last429 = r.status429; this.say("debug", `Instagram 429 (çok fazla istek) yanıtı verdi (${r.status429})`); }
        if (r.media.length || r.highlights.length) {
          if (o.onItems) {
            const res = await o.onItems(r.media, r.highlights);
            if (res === "stop") { result.reason = "stop"; break; }
          }
        }
        total = r.mediaTotal;
        const elapsed = Date.now() - t0;
        if (o.scroll) {
          const idle = r.idleMs;
          if (o.expected && total >= o.expected && elapsed > 4000) { result.reason = "end"; break; }
          if (idle > (o.idleSec || 20) * 1000 && elapsed > 4000) { result.reason = total ? "end" : "stalled"; break; }
          if (o.maxTotalSec && elapsed > o.maxTotalSec * 1000) { result.reason = "timeout"; break; }
          await Work.send("scroll");
          await this.nap(1500);
        } else {
          const enough = (total > 0 || (o.wantHl && h > 0)) && r.idleMs >= idleMs && elapsed > 2000;
          if (enough || elapsed > maxMs) { result.reason = enough ? "settled" : "timeout"; break; }
          await this.nap(700);
        }
      }
      result.total = total;
      result.status429 = (result.state && result.state.status429) || 0;
      return result;
    }

    // ---------------------------------------------------------- profil
    async profile(username, opts) {
      const S = this.settings;
      const only = opts && opts.only;
      this.author = username;
      this.setPhase("profile");
      this.say("info", `@${username} profili açılıyor…`);
      const prof = await this.collect(`${IG}/${username}/`, {
        username, links: true, wantHl: !only && S.highlights, maxSec: 9, idleSec: 4, allowNotFound: true,
        onItems: async () => {},
      });
      if (prof.state.notFound) throw new Problem(`@${username} bulunamadı.`);
      let user = prof.user;
      let uid = user ? C.userIdOf(user) : "";
      if (!uid) {
        const r = await Work.send("take", { m: 0, h: 0, username, html: true });
        if (r && r.html) uid = C.userIdFromHtml(r.html, username);
        if (r && r.user) user = r.user;
      }
      if (!uid) this.say("warn", "Hesabın sayısal kimliği okunamadı; geçmiş/devam bu profil için çalışmayabilir.");
      const hlList = [];
      const addHl = (list) => { for (const x of list || []) if (!hlList.some((y) => y.id === x.id)) hlList.push(x); };
      const first = await Work.send("take", { m: 0, h: 0, username, links: true });
      if (first) addHl(first.highlights);
      const fullName = (user && user.full_name) || "";
      const label = fullName ? `${fullName} (@${username})` : `@${username}`;
      const count = user ? C.toInt((user.edge_owner_to_timeline_media || {}).count || user.media_count) : 0;
      this.say("info", `Profil: ${label}` + (count ? ` — ${count} gönderi` : ""));
      if (user && (user.is_private === true) && user.followed_by_viewer === false && user.friendship_status === undefined) {
        this.say("warn", "Bu hesap gizli görünüyor; takip etmiyorsan gönderiler indirilemez.");
      }
      const ctx = { username, uid, user, count, known: S.history ? await History.known(uid) : new Set(), folder: C.authorFolder(username) };
      this.ctx = ctx;
      if (ctx.known.size) this.say("info", `Geçmişte ${ctx.known.size} içerik kayıtlı; bunlar tekrar indirilmeyecek.`);

      if (!only && S.avatar && user) {
        const av = C.buildAvatar(user, C.avatarUrls(user), S.content);
        av.destUser = username;
        this.enqueue(av, { uid, quiet: true });
      }
      if (S.stories) await this.stories(ctx);
      if (!only && S.posts) await this.feed(ctx, "posts", `${IG}/${username}/`, hlList, addHl);
      if (!only) {
        for (const tab of ["reels", "reposts", "tagged"]) if (S[tab]) await this.feed(ctx, tab, `${IG}/${username}/${tab}/`);
      }
      if (!only && S.highlights) await this.highlights(ctx, hlList);
      this.say("debug", `bitti: @${username}`);
    }

    async stories(ctx) {
      this.setPhase("stories");
      this.say("info", "Hikayeler okunuyor…");
      await this.pace();
      const items = new Map();
      const res = await this.collect(`${IG}/stories/${ctx.username}/`, {
        username: ctx.username, maxSec: 14, idleSec: 3.5, allowNotFound: true,
        onItems: async (media) => {
          for (const s of media) {
            const owner = (s.user.username || "").toLowerCase();
            if (!owner || owner === ctx.username.toLowerCase()) items.set(s.key, s);
          }
        },
      });
      if (!items.size) { this.say("info", "Şu an hikaye yok" + (res.status429 ? " (Instagram çok fazla istek uyarısı verdi, biraz sonra tekrar dene)." : ".")); return; }
      this.say("info", `${items.size} hikaye bulundu.`);
      for (const s of items.values()) {
        const post = C.buildPost(s, { content: this.settings.content, kind: C.KIND_STORY, subdir: C.STORIES_DIR, username: ctx.username });
        post.destUser = ctx.username;
        this.enqueue(post, { uid: ctx.uid, key: s.key });
      }
    }

    async feed(ctx, tab, url, hlList, addHl) {
      const S = this.settings;
      const kind = { posts: C.KIND_POST, reels: C.KIND_REEL, reposts: C.KIND_REPOST, tagged: C.KIND_TAGGED }[tab];
      const names = { posts: "Gönderiler", reels: "Reels", reposts: "Yeniden paylaşımlar", tagged: "Etiketlenenler" };
      this.setPhase(tab);
      this.say("info", `${names[tab]} okunuyor (sayfa kaydırılıyor)…`);
      await this.pace();
      const from = S.date_from ? Math.floor(Date.parse(S.date_from + "T00:00:00") / 1000) : 0;
      const to = S.date_to ? Math.floor(Date.parse(S.date_to + "T23:59:59") / 1000) : 0;
      const newest = S.only_new && S.history && tab === "posts" ? await History.newest(ctx.uid) : 0;
      const seen = new Set();
      let accepted = 0, consecKnown = 0, newestSeen = 0, stopReason = "";
      const me = ctx.username.toLowerCase();
      const res = await this.collect(url, {
        username: ctx.username, scroll: true, idleSec: 25, maxTotalSec: 60 * 90, links: tab === "posts",
        expected: tab === "posts" && ctx.count ? ctx.count : 0,
        onItems: async (media, hls) => {
          if (hls && addHl) addHl(hls);
          for (const s of media) {
            if (seen.has(s.key)) continue;
            seen.add(s.key);
            const owner = (s.user.username || "").toLowerCase();
            if (tab === "tagged" || tab === "reposts") { if (owner && owner === me) continue; }
            else { if (owner && owner !== me) continue; }
            if (tab === "reels" && s.product && s.product !== "clips") continue;
            if (!s.pinned && s.taken) {
              if (from && s.taken < from) { stopReason = "date"; return "stop"; }
              if (to && s.taken > to) continue;
              newestSeen = Math.max(newestSeen, s.taken);
            }
            const isKnown = ctx.known.has(s.key);
            if (!s.pinned) {
              consecKnown = isKnown ? consecKnown + 1 : 0;
              if (newest && consecKnown >= 3) { stopReason = "up_to_date"; return "stop"; }
            }
            accepted++;
            this.stats.found++;
            const post = C.buildPost(s, { content: S.content, kind, username: ctx.username, captionMin: S.caption_min });
            post.destUser = ctx.username;
            this.enqueue(post, { uid: ctx.uid, key: s.key });
            if (S.limit > 0 && accepted >= S.limit) { stopReason = "limit"; return "stop"; }
          }
          this.onUpdate();
        },
      });
      const why = stopReason || res.reason;
      this.say("info", `${names[tab]}: ${accepted} gönderi bulundu` + ({ up_to_date: " (öncekilerle aynı yere gelindi).", date: " (tarih sınırına gelindi).", limit: " (sayı sınırı).", end: ".", stalled: " (liste yüklenmedi).", timeout: " (zaman aşımı)." }[why] || "."));
      if (!accepted && res.status429) this.say("warn", "Instagram 'çok fazla istek' (429) dedi; birkaç dakika bekleyip tekrar dene.");
      if (!accepted && why === "stalled") this.say("warn", "Liste hiç yüklenmedi. Pencerede sayfa görünüyor mu? Giriş yapılı ve küçültülmemiş olmalı.");
      if (tab === "posts" && (why === "end" || why === "up_to_date") && ctx.uid && S.history) {
        await History.setComplete(ctx.uid, newestSeen, ctx.username).catch(() => {});
      }
    }

    async highlights(ctx, hlList) {
      this.setPhase("highlights");
      if (!hlList.length) { this.say("info", "Öne çıkan bulunamadı."); return; }
      this.say("info", `${hlList.length} öne çıkan bulundu.`);
      let n = 0;
      for (const hl of hlList) {
        this.check();
        n++;
        const num = hl.id.split(":").pop();
        const title = hl.title || num;
        this.say("info", `Öne çıkan ${n}/${hlList.length}: ${title}`);
        await this.pace();
        const items = new Map();
        try {
          await this.collect(`${IG}/stories/highlights/${num}/`, {
            maxSec: 12, idleSec: 3, allowNotFound: true,
            onItems: async (media) => { for (const s of media) items.set(s.key, s); },
          });
        } catch (e) { if (e instanceof Cancelled) throw e; this.say("warn", `Öne çıkan '${title}' okunamadı: ${e.message}`); continue; }
        if (!items.size) { this.say("warn", `Öne çıkan '${title}' boş geldi.`); continue; }
        const sub = C.highlightFolder(hl.title, num);
        for (const s of items.values()) {
          const post = C.buildPost(s, { content: this.settings.content, kind: C.KIND_HIGHLIGHT, subdir: sub, username: ctx.username });
          post.destUser = ctx.username;
          this.enqueue(post, { uid: ctx.uid, key: `h${num}:${s.key}` });
        }
      }
    }

    async singlePost(code) {
      const S = this.settings;
      this.setPhase("posts");
      this.say("info", `Gönderi ${code} açılıyor…`);
      let found = null;
      await this.collect(`${IG}/p/${code}/`, {
        maxSec: 12, idleSec: 3, allowNotFound: true,
        onItems: async (media) => { for (const s of media) if (s.code === code || s.key === code) found = found || s; },
      });
      if (!found) throw new Problem(`Gönderi ${code} okunamadı (silinmiş, gizli ya da Instagram yanıt vermedi).`);
      const post = C.buildPost(found, { content: S.content, kind: C.KIND_POST, captionMin: S.caption_min });
      post.destUser = post.username || "instagram";
      this.stats.found++;
      this.enqueue(post, { uid: found.user.id || "", key: found.key });
    }

    async singleHighlight(num) {
      this.setPhase("highlights");
      this.say("info", `Öne çıkan ${num} açılıyor…`);
      const items = new Map();
      await this.collect(`${IG}/stories/highlights/${num}/`, {
        maxSec: 12, idleSec: 3, allowNotFound: true,
        onItems: async (media) => { for (const s of media) items.set(s.key, s); },
      });
      if (!items.size) throw new Problem("Öne çıkan okunamadı.");
      for (const s of items.values()) {
        const owner = s.user.username || "instagram";
        const post = C.buildPost(s, { content: this.settings.content, kind: C.KIND_HIGHLIGHT, subdir: C.highlightFolder("", num), username: owner });
        post.destUser = owner;
        this.enqueue(post, { uid: s.user.id || "", key: `h${num}:${s.key}` });
      }
    }

    // ---------------------------------------------------------- indirme kuyruğu
    enqueue(post, meta) {
      if (post.kind === C.KIND_AVATAR) { /* sessiz atlama */ }
      if (post.skipReason) {
        if (!meta.quiet) { this.stats.postsSkipped++; this.say("debug", `Atlandı (${post.key || post.stem}): ${post.skipReason}`); }
        return;
      }
      post.uid = meta.uid || ""; post.histKey = meta.key || post.key;
      post.quiet = !!meta.quiet;
      post.isKnown = !!(this.ctx && this.ctx.known && this.ctx.uid === post.uid && this.ctx.known.has(post.histKey));
      this.queue.push(post);
      this.stats.filesTotal += post.files.length;
      this.onUpdate();
    }

    async worker() {
      for (;;) {
        if (this.cancel) return;
        const post = this.queue.shift();
        if (!post) {
          if (!this.producing && !this.queue.length) return;
          await sleep(300);
          continue;
        }
        try { await this.download(post); }
        catch (e) {
          if (e instanceof Cancelled) return;
          this.stats.filesFailed += post.files.length;
          this.say("error", `${post.stem}: ${e && e.message || e}`);
        }
      }
    }

    async download(post) {
      const S = this.settings;
      const runKey = post.uid + "|" + post.histKey;
      if (post.uid && post.kind !== C.KIND_AVATAR) {
        if (this.inRun.has(runKey)) return;          // aynı gönderi başka sekmede de çıktı (ör. Reels): bir kez indir
        this.inRun.add(runKey);
      }
      const known = S.history && S.only_new && post.uid && post.isKnown && post.kind !== C.KIND_AVATAR;
      const base = (S.folder ? C.sanitize(S.folder, 60, "Instagram Indirici") + "/" : "") + C.authorFolder(post.destUser || post.username) + "/";
      let okFiles = 0, failed = 0, skipped = 0;
      for (const f of post.files) {
        if (this.cancel) throw new Cancelled();
        const firstUrl = (f.urls && f.urls[0]) || (f.dash && f.dash.video.url) || "";
        const rel = base + f.relStem + extFromUrl(firstUrl, f.kind);
        if (S.verify_files || known) {
          if (await existsOnDisk(rel)) { skipped++; this.stats.filesSkipped++; continue; }
        }
        if (known && !S.verify_files) { skipped++; this.stats.filesSkipped++; continue; }
        const res = await this.fetchFile(f, rel);
        if (res.ok) { okFiles++; this.stats.filesDone++; this.say("ok", `İndirildi (${this.kindName(post.kind)}): ${f.relStem.split("/").pop()}`); }
        else { failed++; this.stats.filesFailed++; this.failures.push(`${f.relStem}: ${res.error}`); this.say("error", `İnemedi: ${f.relStem.split("/").pop()} – ${res.error}`); }
      }
      if (post.captionStem && !failed && okFiles) {
        try { await this.saveText(base + post.captionStem + ".txt", post.caption); } catch (e) {}
      }
      if (!failed) {
        if (okFiles || skipped) this.stats.postsDone++;
        if (post.uid && S.history && post.kind !== C.KIND_AVATAR) await History.mark(post.uid, post.histKey, post.kind, post.destUser || post.username).catch(() => {});
        if (S.info_csv && C.FEED_KINDS.includes(post.kind) && post.code) this.noteInfo(post);
      }
    }

    kindName(k) {
      return { post: "gönderi", reel: "reels", repost: "yeniden paylaşım", tagged: "etiketlenen", story: "hikaye", highlight: "öne çıkan", avatar: "profil fotoğrafı" }[k] || k;
    }

    async fetchFile(f, rel) {
      const r = await this.fetchFile2(f, rel);
      if (r.ok && this.saved.length < 20000) this.saved.push(rel);
      return r;
    }

    async fetchFile2(f, rel) {
      if (f.dash) {
        try {
          const r = await this.fetchDash(f, rel);
          if (r.ok) return r;
          this.say("debug", `Yüksek kalite akış inemedi (${r.error}); normal sürüme geçiliyor.`);
        } catch (e) {
          if (e instanceof Cancelled) throw e;
          this.say("debug", `Birleştirme olmadı (${e.message}); normal sürüme geçiliyor.`);
        }
      }
      if (!f.urls.length) return { ok: false, error: "adres yok" };
      let last = "";
      for (const url of f.urls) {
        const r = await startDownload(url, rel);
        if (r.ok) {
          if (f.kind === "video" && f.plainRes) this.say("info", `Video kalitesi: ${f.plainRes}`);
          return r;
        }
        last = r.error;
      }
      return { ok: false, error: last || "indirilemedi" };
    }

    async fetchBytes(url) {
      const res = await fetch(url, { credentials: "omit" });
      if (!res.ok) throw new Error("HTTP " + res.status);
      return new Uint8Array(await res.arrayBuffer());
    }

    async fetchDash(f, rel) {
      const d = f.dash;
      const video = await this.fetchBytes(d.video.url);
      let audio = null;
      if (d.audio) audio = await this.fetchBytes(d.audio.url);
      this.check();
      let out;
      try { out = root.IIMp4Mux.mux(video, audio); } catch (e) { return { ok: false, error: "birleştirilemedi: " + e.message }; }
      const blob = new Blob([out], { type: "video/mp4" });
      const url = URL.createObjectURL(blob);
      try {
        const r = await startDownload(url, rel);
        if (r.ok) this.say("info", `Video kalitesi: ${d.video.width}x${d.video.height}, ${(d.video.bandwidth / 1e6).toFixed(1)} Mb/sn (en yüksek kalite akışı)`);
        return r;
      } finally { setTimeout(() => URL.revokeObjectURL(url), 60000); }
    }

    async saveText(rel, text, bom) {
      const blob = new Blob([bom ? "\ufeff" + text : text], { type: "text/plain;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      try {
        const r = await startDownload(url, rel);
        if (r.ok && this.saved.length < 20000) this.saved.push(rel);
        return r;
      } finally { setTimeout(() => URL.revokeObjectURL(url), 60000); }
    }

    // ---------------------------------------------------------- bilgi.csv
    noteInfo(post) {
      const user = C.authorFolder(post.destUser || post.username);
      const names = post.files.map((f) => f.relStem.split("/").pop()).join(";");
      const d = post.takenAt ? new Date(post.takenAt * 1000) : null;
      const p = (n) => String(n).padStart(2, "0");
      const when = d ? `${d.getUTCFullYear()}-${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())} ${p(d.getUTCHours())}:${p(d.getUTCMinutes())}` : "";
      const kind = { post: "gönderi", reel: "reels", repost: "yeniden paylaşım", tagged: "etiketlenen" }[post.kind];
      const caption = post.caption.split(/\s+/).join(" ").slice(0, 140);
      const row = [post.code, kind, when, post.permalink, String(post.likes), String(post.comments), names, caption];
      (this.infoRows[user] = this.infoRows[user] || {})[post.code] = row;
    }

    async writeInfo() {
      if (!this.infoRows) return;
      const HEADER = ["kod", "tür", "tarih", "bağlantı", "beğeni", "yorum", "dosyalar", "açıklama"];
      const q = (v) => { v = String(v == null ? "" : v); return /[;"\r\n]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v; };
      for (const [user, rows] of Object.entries(this.infoRows)) {
        for (const [code, row] of Object.entries(rows)) await DB.put("info", { id: user + "|" + code, user, code, row });
        const all = (await DB.all("info")).filter((r) => r.user === user).map((r) => r.row);
        const csv = [HEADER, ...all].map((r) => r.map(q).join(";")).join("\r\n") + "\r\n";
        const base = (this.settings.folder ? C.sanitize(this.settings.folder, 60, "Instagram Indirici") + "/" : "") + user + "/bilgi.csv";
        try { await this.saveText(base, csv, true); } catch (e) { this.say("debug", "bilgi.csv: " + e.message); }
      }
      this.infoRows = {};
    }

    // ---------------------------------------------------------- rapor
    report() {
      const s = this.stats;
      const lines = [
        "Instagram İndirici (Chrome eklentisi) raporu",
        `Sürüm: ${VERSION}`,
        `Zaman: ${new Date().toString()}`,
        `Tarayıcı: ${navigator.userAgent}`,
        "", "--- Ayarlar ---", JSON.stringify(this.settings || {}),
        "", "--- Durum ---",
        `running: ${this.running}`, `phase: ${this.phase}`, `author: ${this.author}`,
        `found_posts: ${s.found}`, `posts_done: ${s.postsDone}`, `posts_skipped: ${s.postsSkipped}`,
        `files_total: ${s.filesTotal}`, `files_done: ${s.filesDone}`, `files_skipped: ${s.filesSkipped}`, `files_failed: ${s.filesFailed}`,
        `summary: ${this.summary}`,
        "", `--- Günlük (${this.log.length} satır) ---`,
        ...this.log.map((l) => `${l.t} [${l.level}] ${l.text}`),
      ];
      return lines.join("\n");
    }
  }

  root.IIEngine = { Engine, History, Work, DEFAULTS, VERSION };
})(typeof globalThis !== "undefined" ? globalThis : this);
