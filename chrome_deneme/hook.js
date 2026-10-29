// Instagram İndirici – Deneme (v0.2.0) – sayfanın KENDİ isteklerini (fetch/XHR) dinler.
// Yanıtların içinden yalnız "kullanıcı adı → sayısal kimlik" eşleşmelerini ve istek adresi/durum kodunu
// content.js'e iletir. Yanıt içeriği, çerez ya da oturum değeri saklanmaz ya da gönderilmez.
(() => {
  "use strict";
  if (window.__iiHook) return;
  window.__iiHook = true;
  const post = (entry) => { try { window.postMessage({ __ii: 1, entry }, "*"); } catch (e) {} };

  function collect(data) {
    const out = new Map();
    const stack = [data];
    let n = 0;
    while (stack.length && n++ < 40000) {
      const v = stack.pop();
      if (!v || typeof v !== "object") continue;
      if (Array.isArray(v)) { for (const x of v) stack.push(x); continue; }
      if (typeof v.username === "string") {
        for (const key of ["id", "pk", "pk_id", "strong_id__"]) {
          if (v[key] !== undefined && v[key] !== null && v[key] !== "") {
            out.set(v.username.toLowerCase() + "|" + key, String(v[key]));
            break;
          }
        }
      }
      for (const k in v) { const x = v[k]; if (x && typeof x === "object") stack.push(x); }
    }
    return [...out.entries()].slice(0, 80);
  }

  function handle(url, status, getText) {
    let path = "";
    try { path = new URL(url, location.origin).pathname; } catch (e) { return; }
    if (!/\/api\/|\/graphql/.test(path)) return;
    Promise.resolve().then(getText).then((text) => {
      let users = [];
      if (text && text.length < 4000000) {
        try { users = collect(JSON.parse(text)); } catch (e) {}
      }
      post({ path, status, bytes: text ? text.length : 0, users });
    }).catch(() => {});
  }

  const origFetch = window.fetch;
  if (origFetch) {
    window.fetch = async function (...args) {
      const res = await origFetch.apply(this, args);
      try {
        const url = typeof args[0] === "string" ? args[0] : (args[0] && args[0].url) || String(args[0] || "");
        const clone = res.clone();
        handle(url, res.status, () => clone.text());
      } catch (e) {}
      return res;
    };
  }

  const origOpen = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function (method, url, ...rest) {
    try {
      this.addEventListener("loadend", () => {
        try {
          const text = (this.responseType === "" || this.responseType === "text") ? this.responseText : "";
          handle(String(url), this.status, () => text);
        } catch (e) {}
      });
    } catch (e) {}
    return origOpen.call(this, method, url, ...rest);
  };
})();
