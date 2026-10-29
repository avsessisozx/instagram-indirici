// Instagram İndirici (Chrome eklentisi) – ortak mantık.
// Uygulamadaki media.py / naming.py / links.py dosyalarının birebir JavaScript karşılığıdır.
// Hem sayfa içindeki betikte (bridge.js) hem de denetim sayfasında (panel.js) kullanılır.
(function (root) {
  "use strict";

  const KIND_POST = "post", KIND_REEL = "reel", KIND_REPOST = "repost", KIND_TAGGED = "tagged";
  const KIND_STORY = "story", KIND_HIGHLIGHT = "highlight", KIND_AVATAR = "avatar";
  const FEED_KINDS = [KIND_POST, KIND_REEL, KIND_REPOST, KIND_TAGGED];
  const SECTION_OF_KIND = { post: "posts", reel: "reels", repost: "reposts", tagged: "tagged",
    story: "stories", highlight: "highlights", avatar: "avatar" };
  const STORIES_DIR = "Hikayeler", HIGHLIGHTS_DIR = "Öne Çıkanlar", AVATAR_DIR = "Profil Fotoğrafı";
  const POSTS_DIR = "Gönderiler", PHOTOS_DIR = "Fotoğraflar", VIDEOS_DIR = "Videolar", ALBUMS_DIR = "Albümler";
  const REELS_DIR = "Reels", REPOSTS_DIR = "Yeniden Paylaşımlar", TAGGED_DIR = "Etiketlenenler";

  // ------------------------------------------------------------ adlandırma
  const INVALID = /[<>:"/\\|?*\u0000-\u001f\u007f]/g;
  const RESERVED = new Set(["CON", "PRN", "AUX", "NUL",
    ...Array.from({ length: 9 }, (_, i) => "COM" + (i + 1)), ...Array.from({ length: 9 }, (_, i) => "LPT" + (i + 1))]);

  function sanitize(name, maxLen = 60, fallback = "adsiz") {
    let text = String(name == null ? "" : name).normalize("NFC");
    text = text.replace(/[\r\n\t\v\f]+/g, " ");
    // Görünmez yön/biçim karakterlerini at
    text = text.replace(/[​-‏‪-‮⁠-⁤﻿-]/g, "");
    text = text.replace(INVALID, " ").replace(/\s+/g, " ").replace(/^[ .]+|[ .]+$/g, "");
    if ([...text].length > maxLen) text = [...text].slice(0, maxLen).join("").replace(/[ .]+$/g, "");
    if (!text) text = fallback;
    if (RESERVED.has(text.split(".")[0].toUpperCase())) text += "_";
    return text;
  }

  function titleFromDesc(desc, maxLen = 40) {
    const raw = String(desc || "");
    let title = sanitize(raw.replace(/#\S+/g, " "), maxLen, "");
    if (!title) {
      const tags = (raw.match(/#\S+/g) || []).map((t) => t.replace(/^#+/, ""));
      title = sanitize(tags.join(" "), maxLen, "");
    }
    return title || "gonderi";
  }

  function dateFromTimestamp(ts) {
    const v = parseInt(ts, 10);
    if (!(v > 0)) return "tarihsiz";
    const d = new Date(v * 1000);
    if (isNaN(d.getTime())) return "tarihsiz";
    const p = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
  }

  function postStem(id, desc, createTime) {
    return `${dateFromTimestamp(createTime)}_${titleFromDesc(desc)}_${id}`;
  }

  // ------------------------------------------------------------ kısa kod <-> kimlik
  const ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
  function shortcodeFromId(mediaId) {
    let value;
    try { value = BigInt(String(mediaId).split("_")[0]); } catch (e) { return ""; }
    if (value <= 0n) return "";
    let out = "";
    while (value > 0n) { out = ALPHABET[Number(value % 64n)] + out; value /= 64n; }
    return out;
  }

  // ------------------------------------------------------------ JSON
  function parseJsonText(text) {
    text = String(text || "").trim();
    if (text.startsWith("for (;;);")) text = text.slice(9);
    if (!text || (text[0] !== "{" && text[0] !== "[")) return null;
    try { return JSON.parse(text); } catch (e) {
      try { return JSON.parse(text.split("\n", 1)[0]); } catch (e2) { return null; }
    }
  }

  // ------------------------------------------------------------ tanıma
  const isObj = (d) => d !== null && typeof d === "object" && !Array.isArray(d);
  function isV1Media(d) {
    if (!isObj(d)) return false;
    if (!("pk" in d || "id" in d)) return false;
    return ["image_versions2", "video_versions", "carousel_media"].some((k) => k in d);
  }
  function isGraphqlMedia(d) {
    if (!isObj(d) || !d.shortcode) return false;
    return ["display_url", "display_resources", "edge_sidecar_to_children", "video_url"].some((k) => k in d);
  }
  const isMedia = (d) => isV1Media(d) || isGraphqlMedia(d);

  function findMedia(obj, limit = 100000) {
    const out = [];
    const stack = [obj];
    while (stack.length && out.length < limit) {
      const cur = stack.pop();
      if (Array.isArray(cur)) {
        for (let i = cur.length - 1; i >= 0; i--) stack.push(cur[i]);
      } else if (isObj(cur)) {
        if (isMedia(cur)) { out.push(cur); continue; }
        const keys = Object.keys(cur);
        for (let i = keys.length - 1; i >= 0; i--) {
          const v = cur[keys[i]];
          if (v && typeof v === "object") stack.push(v);
        }
      }
    }
    return out;
  }

  function findUser(obj, username) {
    let best = null;
    const name = String(username || "").toLowerCase();
    const stack = [obj];
    while (stack.length) {
      const cur = stack.pop();
      if (Array.isArray(cur)) { for (const x of cur) if (x && typeof x === "object") stack.push(x); }
      else if (isObj(cur)) {
        if (String(cur.username || "").toLowerCase() === name && (cur.pk || cur.id || cur.pk_id)) {
          if (!best || Object.keys(cur).length > Object.keys(best).length) best = cur;
        }
        for (const k in cur) { const v = cur[k]; if (v && typeof v === "object") stack.push(v); }
      }
    }
    return best;
  }

  function findHighlights(obj) {
    const out = [];
    const seen = new Set();
    const add = (hid, entry) => {
      hid = String(hid || "");
      if (!hid) return;
      if (!hid.startsWith("highlight:")) {
        if (!/^\d+$/.test(hid)) return;
        hid = "highlight:" + hid;
      }
      if (seen.has(hid)) return;
      seen.add(hid);
      out.push({ id: hid, title: String(entry.title || ""),
        media_count: toInt(entry.media_count || entry.highlight_reel_count) });
    };
    const queue = [obj];
    let guard = 0;
    while (queue.length && guard++ < 200000) {
      const cur = queue.shift();
      if (Array.isArray(cur)) { for (const x of cur) if (x && typeof x === "object") queue.push(x); }
      else if (isObj(cur)) {
        if (String(cur.id || "").startsWith("highlight:") && "title" in cur) { add(cur.id, cur); continue; }
        const edge = cur.edge_highlight_reels;
        if (isObj(edge)) {
          for (const e of edge.edges || []) if (isObj(e) && isObj(e.node)) add(e.node.id, e.node);
        }
        for (const k in cur) { const v = cur[k]; if (v && typeof v === "object") queue.push(v); }
      }
    }
    return out;
  }

  function userIdOf(user) {
    for (const key of ["pk", "pk_id", "id"]) {
      const v = user && user[key];
      if (v && /^\d+$/.test(String(v))) return String(v);
    }
    return "";
  }

  function mediaKey(item) {
    const code = item.code || item.shortcode;
    if (code) return String(code);
    const pk = item.pk || String(item.id || "").split("_")[0];
    return String(pk || "");
  }

  function userIdFromHtml(html, username) {
    if (username) {
      const name = username.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      const pats = [
        new RegExp(`"username"\\s*:\\s*"${name}"[^{}]{0,400}?"(?:id|pk|pk_id|profile_id)"\\s*:\\s*"(\\d+)"`, "i"),
        new RegExp(`"(?:id|pk|pk_id|profile_id)"\\s*:\\s*"(\\d+)"[^{}]{0,400}?"username"\\s*:\\s*"${name}"`, "i"),
      ];
      for (const p of pats) { const m = html.match(p); if (m) return m[1]; }
    }
    const m = html.match(/"profile_id"\s*:\s*"(\d+)"/) || html.match(/"user_id"\s*:\s*"(\d+)"/);
    return m ? m[1] : "";
  }

  // ------------------------------------------------------------ alanlar
  function toInt(v) { const n = parseInt(v, 10); return isNaN(n) ? 0 : n; }
  const area = (e, w = "width", h = "height") => toInt(e[w]) * toInt(e[h]);
  function dedupe(urls) {
    const out = [];
    for (const u of urls) if (typeof u === "string" && u.startsWith("http") && !out.includes(u)) out.push(u);
    return out;
  }

  function imageUrls(item) {
    const urls = [];
    const iv = item.image_versions2;
    const cands = isObj(iv) ? iv.candidates : null;
    if (Array.isArray(cands)) {
      const good = cands.map((c, i) => [i, c]).filter(([, c]) => isObj(c) && c.url);
      good.sort((a, b) => (area(b[1]) - area(a[1])) || (a[0] - b[0]));
      urls.push(...good.map(([, c]) => c.url));
    }
    if (Array.isArray(item.display_resources)) {
      const good = item.display_resources.filter((r) => isObj(r) && r.src);
      good.sort((a, b) => area(b, "config_width", "config_height") - area(a, "config_width", "config_height"));
      urls.push(...good.map((r) => r.src));
    }
    for (const k of ["display_url", "display_src", "thumbnail_src"]) if (item[k]) urls.push(item[k]);
    return dedupe(urls);
  }

  function videoUrls(item) {
    const urls = [];
    if (Array.isArray(item.video_versions)) {
      const good = item.video_versions.map((v, i) => [i, v]).filter(([, v]) => isObj(v) && v.url);
      good.sort((a, b) => (area(b[1]) - area(a[1])) || (a[0] - b[0]));
      urls.push(...good.map(([, v]) => v.url));
    }
    if (item.video_url) urls.push(item.video_url);
    return dedupe(urls);
  }

  const localName = (el) => (el.localName || el.tagName || "").split(":").pop();

  // video_dash_manifest (MPD) içindeki tek dosyalı video ve ses akışları
  function dashStreams(item) {
    let manifest = item.video_dash_manifest;
    if (!manifest && isObj(item.dash_info)) manifest = item.dash_info.video_dash_manifest;
    if (typeof manifest !== "string" || !manifest.includes("<")) return [[], []];
    if (typeof DOMParser === "undefined") return [[], []];
    let doc;
    try {
      doc = new DOMParser().parseFromString(manifest.trim(), "application/xml");
      if (doc.getElementsByTagName("parsererror").length) return [[], []];
    } catch (e) { return [[], []]; }
    const videos = [], audios = [];
    for (const aset of Array.from(doc.getElementsByTagName("*"))) {
      if (localName(aset) !== "AdaptationSet") continue;
      const setType = (aset.getAttribute("contentType") || aset.getAttribute("mimeType") || "").toLowerCase();
      const setSegmented = Array.from(aset.children).some((c) => ["SegmentTemplate", "SegmentList"].includes(localName(c)));
      for (const rep of Array.from(aset.children)) {
        if (localName(rep) !== "Representation") continue;
        let url = "", segmented = setSegmented;
        for (const child of Array.from(rep.children)) {
          const n = localName(child);
          if (n === "BaseURL" && child.textContent && child.textContent.trim().startsWith("http")) url = child.textContent.trim();
          else if (n === "SegmentTemplate" || n === "SegmentList") segmented = true;
        }
        if (!url || segmented) continue;
        const kind = (rep.getAttribute("mimeType") || setType).toLowerCase();
        const s = { url, width: toInt(rep.getAttribute("width")), height: toInt(rep.getAttribute("height")),
          bandwidth: toInt(rep.getAttribute("bandwidth")) };
        if (kind.includes("audio")) audios.push(s);
        else if (kind.includes("video") || s.width) videos.push(s);
      }
    }
    return [videos, audios];
  }

  function dashChoice(item) {
    const [videos, audios] = dashStreams(item);
    if (!videos.length) return null;
    const best = videos.reduce((a, b) => ((b.width * b.height - a.width * a.height) || (b.bandwidth - a.bandwidth)) > 0 ? b : a);
    const audio = audios.length ? audios.reduce((a, b) => (b.bandwidth > a.bandwidth ? b : a)) : null;
    return { video: best, audio };
  }

  function plainSize(item) {
    let best = [0, 0];
    if (Array.isArray(item.video_versions)) {
      for (const v of item.video_versions) {
        if (isObj(v) && v.url) {
          const s = [toInt(v.width), toInt(v.height)];
          if (s[0] * s[1] > best[0] * best[1]) best = s;
        }
      }
    }
    if (best[0] === 0 && best[1] === 0 && isObj(item.dimensions)) best = [toInt(item.dimensions.width), toInt(item.dimensions.height)];
    return best;
  }

  function isVideo(item) {
    if ((item.video_versions && item.video_versions.length) || item.video_url) return true;
    if (item.is_video === true) return true;
    return toInt(item.media_type) === 2;
  }

  function childrenOf(item) {
    if (Array.isArray(item.carousel_media) && item.carousel_media.length) return item.carousel_media.filter(isObj);
    const sc = item.edge_sidecar_to_children;
    if (isObj(sc)) return (sc.edges || []).map((e) => (isObj(e) ? e.node : null)).filter(isObj);
    return [];
  }

  function captionOf(item) {
    const c = item.caption;
    if (isObj(c)) return String(c.text || "");
    if (typeof c === "string") return c;
    const em = item.edge_media_to_caption;
    if (isObj(em) && Array.isArray(em.edges)) {
      return em.edges.map((e) => String((e && e.node && e.node.text) || "")).filter(Boolean).join("\n");
    }
    return "";
  }

  function takenAtOf(item) {
    for (const key of ["taken_at", "taken_at_timestamp", "device_timestamp", "created_at"]) {
      let v = toInt(item[key]);
      while (v > 10000000000) v = Math.floor(v / 1000);
      if (v > 0) return v;
    }
    return 0;
  }

  function ownerOf(item) {
    for (const k of ["user", "owner"]) if (isObj(item[k])) return item[k];
    return {};
  }
  const isPinned = (item) => !!(item.timeline_pinned_user_ids || item.pinned_for_users || item.clips_tab_pinned_user_ids);
  const isReelItem = (item) => String(item.product_type || "").toLowerCase() === "clips";
  function countOf(item, v1, edge) {
    const v = toInt(item[v1]);
    if (v) return v;
    const e = item[edge];
    return isObj(e) ? toInt(e.count) : 0;
  }

  // ------------------------------------------------------------ hafif kayıt (sayfa -> panel)
  // Büyük yanıt nesnesi yerine, indirmek için gereken alanları taşıyan küçük bir kayıt.
  function slim(item) {
    const owner = ownerOf(item);
    const nodes = (childrenOf(item).length ? childrenOf(item) : [item]).map((node) => {
      const v = isVideo(node);
      const out = { v, iu: v ? [] : imageUrls(node), vu: v ? videoUrls(node) : [] };
      if (v) {
        const [w, h] = plainSize(node);
        out.pw = w; out.ph = h;
        const d = dashChoice(node);
        if (d) out.dash = d;
      }
      return out;
    });
    const pk = String(item.pk || String(item.id || "").split("_")[0] || "");
    return {
      key: mediaKey(item), pk,
      code: String(item.code || item.shortcode || shortcodeFromId(item.pk || item.id) || ""),
      reel: isReelItem(item), taken: takenAtOf(item), pinned: isPinned(item),
      caption: captionOf(item).trim(),
      likes: countOf(item, "like_count", "edge_media_preview_like"),
      comments: countOf(item, "comment_count", "edge_media_to_comment"),
      user: { username: String(owner.username || ""), full_name: String(owner.full_name || ""),
        id: String(owner.pk || owner.id || owner.pk_id || "") },
      product: item.product_type == null ? null : String(item.product_type),
      nodes,
    };
  }

  // ------------------------------------------------------------ gönderi -> indirilecek dosyalar
  function wanted(kind, content) {
    if (content === "video") return kind === "video";
    if (content === "image") return kind === "image";
    return true;
  }

  function feedFolder(kind, mediaKind, album, stem) {
    if (kind === KIND_POST) {
      if (album) return `${POSTS_DIR}/${ALBUMS_DIR}/${stem}`;
      return `${POSTS_DIR}/${mediaKind === "video" ? VIDEOS_DIR : PHOTOS_DIR}`;
    }
    const base = { reel: REELS_DIR, repost: REPOSTS_DIR, tagged: TAGGED_DIR }[kind];
    return album ? `${base}/${stem}` : base;
  }

  // s: slim kayıt. Dönen: { key, code, kind, username, ... files: [{kind, urls, relStem, dash, plainRes}] }
  function buildPost(s, opts) {
    const content = opts.content || "all";
    let kind = opts.kind || KIND_POST;
    const subdir = opts.subdir || "";
    const captionMin = opts.captionMin || 0;
    if (kind === KIND_POST && s.reel) kind = KIND_REEL;
    const post = { kind, section: SECTION_OF_KIND[kind] || "", key: s.key, code: s.code,
      username: s.user.username || opts.username || "", authorName: s.user.full_name, ownerId: s.user.id,
      takenAt: s.taken, pinned: s.pinned, caption: s.caption, likes: s.likes, comments: s.comments,
      permalink: "", stem: "", captionStem: "", files: [], skipReason: "" };
    if (post.code && FEED_KINDS.includes(kind)) {
      post.permalink = `https://www.instagram.com/${kind === KIND_REEL ? "reel" : "p"}/${post.code}/`;
    }
    const feed = FEED_KINDS.includes(kind);
    if (feed) post.stem = postStem(post.code || post.key, s.caption, s.taken);
    else post.stem = `${dateFromTimestamp(s.taken)}_${s.pk || post.key}`;
    const prefix = subdir ? subdir + "/" : "";
    const album = s.nodes.length > 1;
    let unmatched = 0, missingVideo = 0, captionFolder = "";
    s.nodes.forEach((node, idx) => {
      const name = album ? `${post.stem}_${String(idx + 1).padStart(2, "0")}` : post.stem;
      const mk = node.v ? "video" : "image";
      const file = { kind: mk, urls: node.v ? node.vu : node.iu, relStem: prefix + name, dash: null,
        plainRes: node.v && node.pw && node.ph ? `${node.pw}x${node.ph}` : "" };
      if (node.v && node.dash && node.dash.video.width * node.dash.video.height >= (node.pw || 0) * (node.ph || 0)) {
        file.dash = node.dash;
      }
      if (feed) {
        const folder = feedFolder(kind, mk, album, post.stem);
        file.relStem = `${folder}/${name}`;
        captionFolder = captionFolder || folder;
      }
      if (!wanted(mk, content)) { unmatched++; return; }
      if (!file.urls.length && !file.dash) { if (mk === "video") missingVideo++; return; }
      post.files.push(file);
    });
    if (feed && captionMin > 0 && post.caption.length >= captionMin && captionFolder) {
      post.captionStem = `${captionFolder}/${post.stem}`;
    }
    if (!post.files.length) {
      if (unmatched) post.skipReason = content === "video" ? "Yalnız video seçili" : "Yalnız fotoğraf seçili";
      else if (missingVideo) post.skipReason = "Video adresi bulunamadı";
      else post.skipReason = "İndirilecek dosya bulunamadı";
    }
    return post;
  }

  function avatarUrls(user) {
    const urls = [];
    const info = user.hd_profile_pic_url_info;
    if (isObj(info) && info.url) urls.push(info.url);
    if (Array.isArray(user.hd_profile_pic_versions)) {
      urls.push(...user.hd_profile_pic_versions.filter((v) => isObj(v) && v.url).sort((a, b) => area(b) - area(a)).map((v) => v.url));
    }
    for (const k of ["profile_pic_url_hd", "profile_pic_url"]) if (user[k]) urls.push(user[k]);
    return dedupe(urls);
  }

  function buildAvatar(user, urls, content) {
    const post = { kind: KIND_AVATAR, section: "avatar", username: String(user.username || ""), files: [], skipReason: "" };
    urls = dedupe(urls);
    const picId = String(user.profile_pic_id || "").split("_")[0];
    let name = picId;
    if (!name) {
      let stem = "profil";
      if (urls.length) {
        try { const n = new URL(urls[0]).pathname.split("/").pop() || ""; stem = n.includes(".") ? n.replace(/\.[^.]*$/, "") : n; } catch (e) {}
      }
      name = sanitize(stem, 80, "profil");
    }
    post.key = "avatar:" + name;
    post.stem = name;
    if (content === "video") post.skipReason = "Yalnız video seçili";
    else if (!urls.length) post.skipReason = "Profil fotoğrafı bulunamadı";
    else post.files = [{ kind: "image", urls, relStem: `${AVATAR_DIR}/${name}`, dash: null, plainRes: "" }];
    return post;
  }

  const highlightFolder = (title, id) => `${HIGHLIGHTS_DIR}/${sanitize(title, 50, "") || id}`;
  const authorFolder = (username) => sanitize(username || "instagram", 60, "instagram");

  // ------------------------------------------------------------ bağlantı çözme
  const USERNAME = /^[A-Za-z0-9._]{1,30}$/;
  const SHORTCODE = /^[A-Za-z0-9_-]{5,60}$/;
  const RESERVED_FIRST = new Set(["p", "reel", "reels", "tv", "stories", "explore", "accounts", "direct", "about", "legal",
    "developer", "web", "api", "s", "share", "challenge", "emails", "session", "privacy", "terms", "graphql", "static",
    "directory", "nametag", "oauth", "topics", "lite", "ar", "invites", "your_activity", "notifications", "create",
    "settings", "help", "press", "jobs", "blog", "popular", "locations", "tags", "audio", "threads", "meta"]);

  // Dönen: {ok, kind: profile|post|story|highlight, username, shortcode, highlightId, error}
  function parseLink(raw) {
    const text = String(raw || "").trim();
    const fail = (error) => ({ ok: false, error });
    if (!text) return fail("Boş satır");
    if (text.startsWith("@") && USERNAME.test(text.slice(1))) return { ok: true, kind: "profile", username: text.slice(1).toLowerCase() };
    if (USERNAME.test(text) && !/\.(com|net|org|am|tr|io|me|ly|co|app|tv)$/i.test(text)) return { ok: true, kind: "profile", username: text.toLowerCase() };
    const m = text.match(/(?:https?:\/\/)?(?:[a-z0-9-]+\.)*(?:instagram\.com|instagr\.am)(\/[^\s<>"']*)?/i);
    if (!m) return fail("Instagram bağlantısı ya da kullanıcı adı değil");
    const path = (m[1] || "/").split(/[?#]/)[0];
    const seg = path.split("/").filter(Boolean).map((x) => decodeURIComponent(x));
    if (!seg.length) return fail("Bağlantıda profil ya da gönderi yok");
    const first = seg[0].toLowerCase();
    if (first === "share") return fail("Paylaşım bağlantıları desteklenmiyor; gönderiyi açıp adres çubuğundaki bağlantıyı kopyala");
    for (let i = 0; i < Math.min(2, seg.length); i++) {
      if (["p", "reel", "reels", "tv"].includes(seg[i].toLowerCase()) && seg.length > i + 1) {
        if (i === 1 && RESERVED_FIRST.has(seg[0].toLowerCase())) break;
        if (SHORTCODE.test(seg[i + 1])) return { ok: true, kind: "post", shortcode: seg[i + 1] };
        return fail("Gönderi bağlantısı bozuk");
      }
    }
    if (first === "stories") {
      if (seg.length >= 3 && seg[1].toLowerCase() === "highlights" && /^\d+$/.test(seg[2])) return { ok: true, kind: "highlight", highlightId: seg[2] };
      if (seg.length >= 2 && !["highlights", "me"].includes(seg[1].toLowerCase()) && USERNAME.test(seg[1])) return { ok: true, kind: "story", username: seg[1].toLowerCase() };
      return fail("Hikaye bağlantısında kullanıcı yok");
    }
    if (first === "explore" || first === "direct" || RESERVED_FIRST.has(first)) return fail("Bu bağlantı bir profil değil");
    if (USERNAME.test(seg[0])) return { ok: true, kind: "profile", username: seg[0].toLowerCase() };
    return fail("Bağlantı anlaşılamadı");
  }

  root.IICore = {
    KIND_POST, KIND_REEL, KIND_REPOST, KIND_TAGGED, KIND_STORY, KIND_HIGHLIGHT, KIND_AVATAR, FEED_KINDS,
    STORIES_DIR, HIGHLIGHTS_DIR, sanitize, titleFromDesc, dateFromTimestamp, postStem, shortcodeFromId,
    parseJsonText, isMedia, findMedia, findUser, findHighlights, userIdOf, mediaKey, userIdFromHtml,
    imageUrls, videoUrls, dashStreams, dashChoice, plainSize, isVideo, childrenOf, captionOf, takenAtOf, ownerOf,
    isPinned, isReelItem, slim, buildPost, avatarUrls, buildAvatar, highlightFolder, authorFolder, parseLink, toInt,
  };
})(typeof globalThis !== "undefined" ? globalThis : this);
