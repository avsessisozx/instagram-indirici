// Instagram İndirici – sayfanın KENDİ isteklerinin (fetch/XHR) yanıtlarını dinler (sayfa dünyasında çalışır).
// Yanıt metnini yalnız bu tarayıcı sekmesindeki bridge.js'e iletir; hiçbir yere göndermez.
(() => {
  "use strict";
  if (window.__iiHook) return;
  window.__iiHook = true;
  const MARK = /image_versions2|video_versions|display_url|"highlight:|"username"|carousel_media|edge_highlight_reels/;
  const send = (entry) => { try { window.postMessage({ __ii: 1, entry }, "*"); } catch (e) {} };

  function handle(url, status, getText) {
    let path = "";
    try { path = new URL(url, location.origin).pathname; } catch (e) { return; }
    if (!/\/api\/|\/graphql/.test(path)) return;
    Promise.resolve().then(getText).then((text) => {
      text = text || "";
      if (text.length > 30000000) text = "";
      send({ path, status, text: MARK.test(text) ? text : "" });
    }).catch(() => send({ path, status, text: "" }));
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
