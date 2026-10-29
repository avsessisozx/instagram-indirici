// Instagram İndirici – Instagram sayfası içindeki yardımcı. Sayfanın yüklediği gönderi/hikaye/öne çıkan verisini
// toplar ve denetim sayfasına (panel.js) verir. Instagram'a kendi başına hiçbir istek yapmaz.
(() => {
  "use strict";
  if (window.__iiBridge) return;
  window.__iiBridge = true;
  const C = globalThis.IICore;

  const state = {
    media: [], mediaKeys: new Set(),     // hafif kayıtlar (C.slim)
    highlights: [], hlKeys: new Set(),
    users: {},                           // küçük harfli kullanıcı adı -> kullanıcı nesnesi (en çok alanlı)
    lastNew: Date.now(), responses: 0, status429: 0, statusLast: 0, scanned: 0,
  };

  function takeData(data) {
    if (!data || typeof data !== "object") return;
    for (const item of C.findMedia(data)) {
      const key = C.mediaKey(item);
      if (!key || state.mediaKeys.has(key)) continue;
      state.mediaKeys.add(key);
      state.media.push(C.slim(item));
      state.lastNew = Date.now();
    }
    for (const h of C.findHighlights(data)) {
      if (state.hlKeys.has(h.id)) continue;
      state.hlKeys.add(h.id);
      state.highlights.push(h);
      state.lastNew = Date.now();
    }
    collectUsers(data);
  }

  function collectUsers(data) {
    const stack = [data];
    let n = 0;
    while (stack.length && n++ < 60000) {
      const v = stack.pop();
      if (Array.isArray(v)) { for (const x of v) if (x && typeof x === "object") stack.push(x); continue; }
      if (!v || typeof v !== "object") continue;
      if (typeof v.username === "string" && (v.pk || v.id || v.pk_id)) {
        const name = v.username.toLowerCase();
        const old = state.users[name];
        if (!old || Object.keys(v).length > Object.keys(old).length) state.users[name] = v;
      }
      for (const k in v) { const x = v[k]; if (x && typeof x === "object") stack.push(x); }
    }
  }

  window.addEventListener("message", (ev) => {
    if (ev.source !== window || !ev.data || ev.data.__ii !== 1) return;
    const e = ev.data.entry || {};
    state.responses++;
    state.statusLast = e.status || 0;
    if (e.status === 429) state.status429++;
    if (e.text) takeData(C.parseJsonText(e.text));
  });

  // Sayfaya gömülü ilk veriler (ilk gönderiler, profil bilgisi, öne çıkanlar)
  function scanEmbedded() {
    let n = 0;
    for (const s of document.querySelectorAll('script[type="application/json"]')) {
      const t = s.textContent || "";
      if (t.length < 50 || s.__iiDone === t.length) continue;
      if (t.indexOf("username") < 0 && t.indexOf("image_versions2") < 0 && t.indexOf("display_url") < 0) continue;
      s.__iiDone = t.length;
      takeData(C.parseJsonText(t));
      n++;
    }
    state.scanned += n;
    return n;
  }
  const timer = setInterval(scanEmbedded, 1500);
  document.addEventListener("DOMContentLoaded", scanEmbedded);

  function pageState() {
    const path = location.pathname;
    return {
      url: location.href, path, title: document.title,
      loginWall: /^\/accounts\/(login|signup)/.test(path) || !!document.querySelector('input[name="password"]'),
      challenge: /^\/(challenge|checkpoint|auth_platform|accounts\/suspended|accounts\/disabled)/.test(path),
      notFound: /Sayfa mevcut değil|Sorry, this page isn't available|Page Not Found/i.test(document.title) ||
        /Sayfa mevcut değil|Sorry, this page isn't available/i.test((document.body && document.body.innerText || "").slice(0, 600)),
      privateAccount: /Bu Hesap Gizli|This account is private/i.test((document.body && document.body.innerText) || ""),
      responses: state.responses, status429: state.status429, statusLast: state.statusLast,
      mediaCount: state.media.length, hlCount: state.highlights.length,
      idleMs: Date.now() - state.lastNew,
    };
  }

  // Sayfa bağlantılarından öne çıkan kimlikleri de oku (yanıttan alınamazsa)
  function linkHighlights() {
    const out = [];
    for (const a of document.querySelectorAll('a[href*="/stories/highlights/"]')) {
      const m = (a.getAttribute("href") || "").match(/\/stories\/highlights\/(\d+)/);
      if (m && !state.hlKeys.has("highlight:" + m[1])) {
        state.hlKeys.add("highlight:" + m[1]);
        const label = (a.getAttribute("aria-label") || a.innerText || "").trim().split("\n")[0].slice(0, 60);
        state.highlights.push({ id: "highlight:" + m[1], title: label, media_count: 0 });
      }
    }
  }

  chrome.runtime.onMessage.addListener((msg, sender, reply) => {
    if (!msg || msg.ii !== 1) return;
    try {
      switch (msg.cmd) {
        case "ping": reply({ ready: true, ...pageState() }); break;
        case "reset":
          state.media = []; state.mediaKeys = new Set(); state.highlights = []; state.hlKeys = new Set();
          state.users = {}; state.lastNew = Date.now(); state.responses = 0; state.status429 = 0;
          document.querySelectorAll('script[type="application/json"]').forEach((s) => { s.__iiDone = 0; });
          reply({ ok: true }); break;
        case "confirm": {
          // Öne çıkan sayfaları içeriği ancak "Hikayeyi Gör" onayından sonra yükler
          const re = /^(Hikayeyi Gör|Hikayeyi Görüntüle|View story|View Story)$/i;
          let clicked = false;
          for (const el of document.querySelectorAll('button,[role="button"]')) {
            const t = (el.innerText || el.textContent || "").trim();
            if (re.test(t)) { el.click(); clicked = true; break; }
          }
          reply({ clicked }); break;
        }
        case "scan": scanEmbedded(); linkHighlights(); reply({ ...pageState() }); break;
        case "scroll":
          window.scrollBy(0, Math.max(800, window.innerHeight * 1.5));
          window.scrollTo(0, document.documentElement.scrollHeight);
          window.dispatchEvent(new Event("scroll"));
          scanEmbedded(); reply({ ...pageState() }); break;
        case "take": {
          scanEmbedded();
          if (msg.links) linkHighlights();
          const m0 = msg.m || 0, h0 = msg.h || 0;
          const user = msg.username ? state.users[String(msg.username).toLowerCase()] : null;
          reply({ ...pageState(), media: state.media.slice(m0), mediaTotal: state.media.length,
            highlights: state.highlights.slice(h0), hlTotal: state.highlights.length,
            user: user || null, html: msg.html ? document.documentElement.outerHTML.slice(0, 3000000) : undefined });
          break;
        }
        default: reply({ error: "bilinmeyen komut" });
      }
    } catch (e) { reply({ error: String(e && e.message || e) }); }
    return true;
  });

  // Sayfada küçük bir düğme: eklentinin denetim sayfasını bu profille açar
  function addButton() {
    if (document.getElementById("ii-open-btn") || !document.body) return;
    const b = document.createElement("button");
    b.id = "ii-open-btn";
    b.textContent = "İİ İndir";
    b.title = "Instagram İndirici'yi bu sayfa için aç";
    b.style.cssText = "position:fixed;right:14px;bottom:14px;z-index:2147483646;background:#e1306c;color:#fff;border:0;" +
      "border-radius:999px;padding:9px 14px;font:600 13px system-ui,sans-serif;cursor:pointer;box-shadow:0 2px 10px rgba(0,0,0,.35);opacity:.9";
    b.addEventListener("click", () => chrome.runtime.sendMessage({ ii: 1, cmd: "open-panel", url: location.href }));
    document.body.appendChild(b);
  }
  setInterval(addButton, 2500);
  void timer;
})();
