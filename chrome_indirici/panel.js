// Instagram İndirici – denetim sayfası (arayüz).
(() => {
  "use strict";
  const { Engine, History, Work, DEFAULTS, VERSION } = globalThis.IIEngine;
  const $ = (id) => document.getElementById(id);
  const BOOL = ["posts", "stories", "highlights", "avatar", "reels", "reposts", "tagged", "only_new", "history", "verify_files", "info_csv"];
  const TEXT = ["content", "date_from", "date_to", "speed", "folder"];
  const NUM = ["limit", "caption_min"];
  let engine = null;
  const keep = [];

  $("ver").textContent = "v" + VERSION;

  // Pencere arka planda kalsa da zamanlayıcıların kısıtlanmaması için sessiz bir WebRTC bağlantısı
  function keepAlive() {
    try {
      const a = new RTCPeerConnection(), b = new RTCPeerConnection();
      a.onicecandidate = (e) => e.candidate && b.addIceCandidate(e.candidate).catch(() => {});
      b.onicecandidate = (e) => e.candidate && a.addIceCandidate(e.candidate).catch(() => {});
      const dc = a.createDataChannel("ii");
      b.ondatachannel = (e) => { e.channel.onmessage = () => {}; };
      a.createOffer().then((o) => a.setLocalDescription(o).then(() => b.setRemoteDescription(o)))
        .then(() => b.createAnswer()).then((an) => b.setLocalDescription(an).then(() => a.setRemoteDescription(an)))
        .catch(() => {});
      setInterval(() => { try { if (dc.readyState === "open") dc.send("."); } catch (e) {} }, 20000);
      keep.push(a, b, dc);
    } catch (e) {}
  }

  function readForm() {
    const s = {};
    for (const k of BOOL) s[k] = $(k).checked;
    for (const k of TEXT) s[k] = $(k).value;
    for (const k of NUM) s[k] = Math.max(0, parseInt($(k).value, 10) || 0);
    return s;
  }
  function fillForm(s) {
    for (const k of BOOL) $(k).checked = !!s[k];
    for (const k of TEXT) $(k).value = s[k] == null ? "" : s[k];
    for (const k of NUM) $(k).value = s[k] == null ? 0 : s[k];
    $("folderName").textContent = s.folder || "";
  }
  const store = {
    get(k) { return new Promise((r) => { try { chrome.storage.local.get(k, (v) => r(v || {})); } catch (e) { r({}); } }); },
    set(o) { try { chrome.storage.local.set(o); } catch (e) {} },
  };

  function render() {
    if (!engine) return;
    const s = engine.stats;
    $("c-found").textContent = s.found;
    $("c-done").textContent = s.filesDone;
    $("c-skip").textContent = s.filesSkipped;
    $("c-fail").textContent = s.filesFailed;
    $("c-total").textContent = s.filesTotal;
    const finished = s.filesDone + s.filesSkipped + s.filesFailed;
    $("bar-fill").style.width = (s.filesTotal ? Math.min(100, Math.round(finished * 100 / s.filesTotal)) : 0) + "%";
    $("start").disabled = engine.running;
    $("stop").disabled = !engine.running;
    const names = { starting: "başlıyor", profile: "profil okunuyor", stories: "hikayeler", posts: "gönderiler", reels: "reels",
      reposts: "yeniden paylaşımlar", tagged: "etiketlenenler", highlights: "öne çıkanlar", stopped: "durdu", idle: "" };
    $("state").textContent = engine.running ? `Çalışıyor: ${names[engine.phase] || engine.phase}${engine.author ? " — @" + engine.author : ""}` : (engine.summary || "");
    const debug = $("debug").checked;
    const box = $("log");
    const stick = box.scrollTop + box.clientHeight >= box.scrollHeight - 30;
    box.textContent = "";
    for (const l of engine.log.slice(-600)) {
      if (l.level === "debug" && !debug) continue;
      const span = document.createElement("span");
      span.className = "l-" + l.level;
      span.textContent = `${l.t} ${l.text}\n`;
      box.appendChild(span);
    }
    if (stick) box.scrollTop = box.scrollHeight;
  }

  let pending = false;
  function schedule() { if (pending) return; pending = true; setTimeout(() => { pending = false; render(); }, 250); }

  async function renderHistory() {
    const box = $("hist");
    box.textContent = "";
    let rows = [];
    try { rows = await History.profiles(); } catch (e) {}
    if (!rows.length) { box.innerHTML = '<p class="hint">Henüz kayıt yok.</p>'; return; }
    for (const r of rows) {
      const div = document.createElement("div");
      div.className = "h";
      const left = document.createElement("div");
      left.innerHTML = "<b></b> <small></small>";
      left.querySelector("b").textContent = "@" + (r.username || r.uid);
      left.querySelector("small").textContent = `${r.count} içerik · ${new Date(r.last).toLocaleDateString("tr-TR")}`;
      const btn = document.createElement("button");
      btn.textContent = "Geçmişi sil";
      btn.addEventListener("click", async () => { await History.forget(r.uid); renderHistory(); });
      div.append(left, btn);
      box.appendChild(div);
    }
  }

  async function start() {
    const settings = readForm();
    store.set({ settings });
    $("folderName").textContent = settings.folder;
    engine = new Engine(schedule);
    render();
    await engine.run($("targets").value, settings);
    render();
    renderHistory();
  }

  async function init() {
    const saved = (await store.get(["settings", "targets"]));
    fillForm({ ...DEFAULTS, ...(saved.settings || {}) });
    $("targets").value = saved.targets || "";
    const u = new URLSearchParams(location.search).get("u");
    if (u) $("targets").value = u;
    $("targets").addEventListener("input", () => store.set({ targets: $("targets").value }));
    $("folder").addEventListener("input", () => { $("folderName").textContent = $("folder").value; });
    $("start").addEventListener("click", start);
    $("stop").addEventListener("click", () => engine && engine.stop());
    $("debug").addEventListener("change", render);
    $("copy").addEventListener("click", async () => {
      const text = engine ? engine.report() : "Henüz bir çalışma yok.";
      try { await navigator.clipboard.writeText(text); $("copy").textContent = "Kopyalandı ✓"; }
      catch (e) {
        const ta = document.createElement("textarea"); ta.value = text; document.body.appendChild(ta); ta.select();
        document.execCommand("copy"); ta.remove(); $("copy").textContent = "Kopyalandı ✓";
      }
      setTimeout(() => { $("copy").textContent = "Raporu kopyala"; }, 2000);
    });
    $("clear-all").addEventListener("click", async () => {
      if (confirm("Tüm indirme geçmişi silinsin mi? (Dosyalara dokunulmaz.)")) { await History.clear(); renderHistory(); }
    });
    window.addEventListener("beforeunload", (e) => { if (engine && engine.running) { e.preventDefault(); e.returnValue = ""; } });
    keepAlive();
    renderHistory();
    engine = new Engine(schedule);
    render();
  }
  init();
})();
