// Instagram İndirici – Deneme eklentisi (v0.2.0)
// Amaç: programın kullandığı liste isteklerini, programın açtığı ayrı tarayıcı yerine senin gerçek
// Chrome oturumunda dener; hangisi hangi kodu döndürdü kopyalanabilir rapor olarak gösterir.
// Hiçbir şey indirmez. Rapor yalnız bu sayfada gösterilir; hiçbir yere gönderilmez. Çerez/oturum değeri rapora yazılmaz.
(() => {
  "use strict";
  const VERSION = "0.2.0";
  const APP_ID = "936619743392459";
  const ASBD_ID = "129477";
  const RESERVED = new Set(["", "p", "reel", "reels", "tv", "stories", "explore", "direct", "accounts", "about", "web", "s", "share", "tags", "locations", "challenge", "emails", "legal", "directory"]);
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  // Sayfanın kendi istekleri (hook.js yakalar): { path, status, bytes, users: [["kullanıcı|anahtar", "kimlik"], ...] }
  const captured = [];
  window.addEventListener("message", (ev) => {
    if (ev.source !== window || !ev.data || ev.data.__ii !== 1) return;
    if (captured.length < 300) captured.push(ev.data.entry);
  });
  const pageUser = () => {
    const seg = location.pathname.split("/").filter(Boolean)[0] || "";
    return RESERVED.has(seg.toLowerCase()) ? "" : seg;
  };

  async function api(path, params) {
    const url = new URL(path, location.origin);
    for (const [k, v] of params || []) url.searchParams.append(k, v);
    const m = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    let claim = "0";
    try { claim = sessionStorage.getItem("www-claim-v2") || "0"; } catch (e) {}
    const headers = {
      "Accept": "*/*", "X-IG-App-ID": APP_ID, "X-ASBD-ID": ASBD_ID, "X-IG-WWW-Claim": claim,
      "X-Requested-With": "XMLHttpRequest", "X-CSRFToken": m ? decodeURIComponent(m[1]) : "",
    };
    const t0 = performance.now();
    try {
      const r = await fetch(url.toString(), { method: "GET", headers, credentials: "include", redirect: "follow" });
      const nc = r.headers.get("x-ig-set-www-claim");
      if (nc) { try { sessionStorage.setItem("www-claim-v2", nc); } catch (e) {} }
      const text = await r.text();
      let data = null;
      try { data = JSON.parse(text); } catch (e) {}
      return { status: r.status, ms: Math.round(performance.now() - t0), text, data };
    } catch (e) {
      return { status: 0, ms: Math.round(performance.now() - t0), text: "", data: null, error: String(e) };
    }
  }

  const snippet = (res) => {
    if (res.error) return " hata=" + res.error;
    if (res.status === 200 && res.data) return "";
    return " yanıt=" + (res.text || "").replace(/\s+/g, " ").slice(0, 120);
  };
  const line = (label, res, extra) =>
    `${label}: HTTP ${res.status} (${res.ms} ms)${extra ? " " + extra : ""}${snippet(res)}`;

  function idsFromHtml(username) {
    const html = document.documentElement.innerHTML;
    const first = (re) => { const m = html.match(re); return m ? m[1] : ""; };
    const esc = username.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const anchored =
      first(new RegExp(`"username"\\s*:\\s*"${esc}"[^{}]{0,400}?"(?:id|pk|pk_id|profile_id)"\\s*:\\s*"(\\d+)"`, "i")) ||
      first(new RegExp(`"(?:id|pk|pk_id|profile_id)"\\s*:\\s*"(\\d+)"[^{}]{0,400}?"username"\\s*:\\s*"${esc}"`, "i"));
    return { profile_id: first(/"profile_id"\s*:\s*"(\d+)"/), user_id: first(/"user_id"\s*:\s*"(\d+)"/), anchored };
  }


  const REDACT = /("[^"]*(?:token|csrf|lsd|dtsg|claim|session|cookie|auth)[^"]*"\s*:\s*")[^"]*/gi;

  function htmlContext(username) {
    const html = document.documentElement.innerHTML;
    const out = [];
    const needle = `"username":"${username}"`;
    let from = 0;
    for (let i = 0; i < 3; i++) {
      const at = html.toLowerCase().indexOf(needle.toLowerCase(), from);
      if (at < 0) break;
      from = at + needle.length;
      out.push("   ..." + html.slice(Math.max(0, at - 140), at + needle.length + 140).replace(REDACT, "$1<gizli>").replace(/\s+/g, " ") + "...");
    }
    return out;
  }

  function idKeyStats() {
    const html = document.documentElement.innerHTML;
    const re = /"([A-Za-z_]*(?:id|pk))"\s*:\s*"(\d{5,})"/g;
    const counts = new Map();
    let m;
    let n = 0;
    while ((m = re.exec(html)) && n++ < 5000) {
      const key = m[1];
      const cur = counts.get(key) || { count: 0, first: m[2] };
      cur.count++;
      counts.set(key, cur);
    }
    return [...counts.entries()].sort((a, b) => b[1].count - a[1].count).slice(0, 12)
      .map(([k, v]) => `${k}=${v.first} (x${v.count})`);
  }

  function capturedFor(username) {
    const u = username.toLowerCase();
    const ids = new Set();
    for (const e of captured) {
      for (const [key, id] of e.users || []) {
        if (key.split("|")[0] === u) ids.add(id);
      }
    }
    return [...ids];
  }

  async function runTests(log) {
    const out = [];
    const add = (s) => { out.push(s); log(out.join("\n")); };
    const username = pageUser();
    add(`Instagram İndirici – Deneme ${VERSION}`);
    add(`Zaman: ${new Date().toString()}`);
    add(`Tarayıcı: ${navigator.userAgent}`);
    add(`webdriver: ${String(navigator.webdriver)}`);
    if (!username) { add("Bir profil sayfasını aç (ör. instagram.com/kullaniciadi) ve tekrar dene."); return out.join("\n"); }
    add(`Profil: @${username}`);
    const wait = () => sleep(2500);

    const ids = idsFromHtml(username);
    add(`Sayfadan okunan kimlikler: profile_id=${ids.profile_id || "-"} user_id=${ids.user_id || "-"} kullanıcı adıyla eşleşen=${ids.anchored || "-"}`);
    for (const l of htmlContext(username)) add(l);
    add("Sayfadaki kimlik alanları: " + (idKeyStats().join(", ") || "-"));
    const pageIds = capturedFor(username);
    add(`Sayfanın kendi isteklerinden bu kullanıcı için gelen kimlikler: ${pageIds.join(", ") || "-"}` +
        (captured.length ? ` (${captured.length} istek yakalandı)` : " (hiç istek yakalanmadı: sayfayı F5 ile yenileyip tekrar dene)"));
    await wait();

    const prof = await api("/api/v1/users/web_profile_info/", [["username", username]]);
    const user = prof.data && prof.data.data && prof.data.data.user;
    const apiId = user ? String(user.id || user.pk || "") : "";
    add(line("1) profil bilgisi", prof, user ? `kimlik=${apiId} gönderi=${user.edge_owner_to_timeline_media ? user.edge_owner_to_timeline_media.count : "?"} gizli=${user.is_private}` : ""));
    const id = apiId || ids.anchored || ids.profile_id || ids.user_id;
    if (apiId) {
      for (const k of ["profile_id", "user_id", "anchored"]) {
        if (ids[k] && ids[k] !== apiId) add(`   UYARI: sayfadan okunan ${k}=${ids[k]} gerçek kimlikten (${apiId}) farklı`);
      }
    }
    if (!id) { add("Kimlik bulunamadı, devam edilemiyor."); return out.join("\n"); }
    await wait();

    const feed = await api(`/api/v1/feed/user/${id}/`, [["count", "12"]]);
    add(line("2) gönderi listesi (REST)", feed, feed.data && feed.data.items ? `öğe=${feed.data.items.length} devam=${feed.data.more_available}` : ""));
    await wait();

    for (const k of ["profile_id", "user_id"]) {
      if (ids[k] && ids[k] !== id) {
        const bad = await api(`/api/v1/feed/user/${ids[k]}/`, [["count", "12"]]);
        add(line(`2b) gönderi listesi, sayfadan okunan ${k} (${ids[k]}) ile`, bad));
        await wait();
      }
    }

    const stories = await api("/api/v1/feed/reels_media/", [["reel_ids", id]]);
    const sReel = stories.data && stories.data.reels_media && stories.data.reels_media[0];
    add(line("3) hikayeler", stories, stories.data ? `hikaye=${sReel && sReel.items ? sReel.items.length : 0}` : ""));
    await wait();

    const tray = await api(`/api/v1/highlights/${id}/highlights_tray/`);
    const trayList = (tray.data && tray.data.tray) || [];
    add(line("4) öne çıkan listesi", tray, tray.data ? `öne çıkan=${trayList.length}` : ""));
    await wait();

    const hlIds = trayList.slice(0, 3).map((t) => t.id).filter(Boolean);
    if (hlIds.length) {
      const hl = await api("/api/v1/feed/reels_media/", hlIds.map((h) => ["reel_ids", h]));
      const reels = (hl.data && hl.data.reels_media) || [];
      add(line("5) öne çıkan içeriği (ilk 3)", hl, hl.data ? `gelen=${reels.length} öğe=${reels.reduce((n, r) => n + ((r.items || []).length), 0)}` : ""));
    } else {
      add("5) öne çıkan içeriği: liste boş/alınamadığı için denenmedi");
    }
    await wait();

    const info = await api(`/api/v1/users/${id}/info/`);
    add(line("6) kullanıcı bilgisi", info, info.data && info.data.user ? `kullanıcı=${info.data.user.username}` : ""));
    await wait();

    const tried = new Set([id, ids.profile_id, ids.user_id].filter(Boolean));
    let extraTested = 0;
    for (const cand of pageIds) {
      if (tried.has(cand) || extraTested >= 2) continue;
      extraTested++;
      const res = await api(`/api/v1/feed/user/${cand}/`, [["count", "12"]]);
      add(line(`7) gönderi listesi, sayfanın kendi isteğinden gelen kimlik (${cand}) ile`, res,
               res.data && res.data.items ? `öğe=${res.data.items.length}` : ""));
      await wait();
    }

    const seen = new Map();
    for (const e of captured) {
      const k = `${e.path} -> ${e.status}`;
      seen.set(k, (seen.get(k) || 0) + 1);
    }
    add("Sayfanın kendi istekleri (adres -> kod, adet):");
    for (const [k, c] of [...seen.entries()].slice(0, 30)) add(`   ${k} x${c}`);
    add("Bitti. Raporu kopyalayıp Claude'a yapıştır.");
    return out.join("\n");
  }

  function mount() {
    if (document.getElementById("ii-deneme-root")) return;
    const root = document.createElement("div");
    root.id = "ii-deneme-root";
    root.style.cssText = "position:fixed;left:12px;bottom:12px;z-index:2147483647;font:13px/1.4 system-ui,sans-serif;";
    const btn = document.createElement("button");
    btn.id = "ii-deneme-btn";
    btn.textContent = "İİ Deneme";
    btn.style.cssText = "padding:8px 12px;border:0;border-radius:18px;background:#f60;color:#fff;font-weight:600;cursor:pointer;box-shadow:0 2px 8px #0006;";
    const panel = document.createElement("div");
    panel.style.cssText = "display:none;margin-bottom:8px;width:min(560px,90vw);background:#111;color:#eee;border-radius:10px;padding:10px;box-shadow:0 4px 18px #000a;";
    const area = document.createElement("textarea");
    area.readOnly = true;
    area.style.cssText = "width:100%;height:260px;background:#000;color:#9f9;border:0;border-radius:6px;padding:6px;font:12px/1.35 ui-monospace,Consolas,monospace;resize:vertical;box-sizing:border-box;";
    const bar = document.createElement("div");
    bar.style.cssText = "display:flex;gap:8px;margin-top:8px;";
    const copy = document.createElement("button");
    copy.textContent = "Raporu kopyala";
    const close = document.createElement("button");
    close.textContent = "Kapat";
    for (const b of [copy, close]) b.style.cssText = "padding:6px 10px;border:0;border-radius:6px;background:#333;color:#fff;cursor:pointer;";
    copy.onclick = async () => {
      try { await navigator.clipboard.writeText(area.value); copy.textContent = "Kopyalandı"; }
      catch (e) { area.select(); document.execCommand("copy"); copy.textContent = "Kopyalandı"; }
      setTimeout(() => (copy.textContent = "Raporu kopyala"), 1500);
    };
    close.onclick = () => { panel.style.display = "none"; };
    bar.append(copy, close);
    panel.append(area, bar);
    root.append(panel, btn);
    let running = false;
    btn.onclick = async () => {
      panel.style.display = "block";
      if (running) return;
      running = true;
      btn.disabled = true;
      btn.textContent = "Deneniyor…";
      area.value = "Başlıyor…";
      try {
        const text = await runTests((t) => { area.value = t; area.scrollTop = area.scrollHeight; });
        area.value = text;
      } catch (e) {
        area.value += "\nBeklenmeyen hata: " + e;
      } finally {
        running = false;
        btn.disabled = false;
        btn.textContent = "İİ Deneme";
      }
    };
    document.documentElement.appendChild(root);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mount);
  else mount();
  window.__iiDeneme = { runTests, version: VERSION };
})();
