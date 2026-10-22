// Instagram İndirici – Giriş Köprüsü (arka plan)
// Yalnızca kullanıcı programda "Chrome'dan giriş yap"a bastığında çalışır: programın verdiği tek kullanımlık
// kodla, bu bilgisayardaki programa (127.0.0.1) Instagram oturum çerezlerini gönderir. Başka hiçbir yere göndermez.
const COOKIE_DOMAIN = "instagram.com";
const WANT = ["sessionid", "csrftoken", "ds_user_id", "mid", "ig_did", "datr", "ig_nrcb"];
const PENDING = "ii_bekleyen";
const TTL_MS = 10 * 60 * 1000;

async function getPending() {
  const stored = await chrome.storage.session.get(PENDING);
  const p = stored[PENDING];
  if (!p) return null;
  if (Date.now() > p.expires) {
    await chrome.storage.session.remove(PENDING);
    return null;
  }
  return p;
}

async function collect() {
  const all = await chrome.cookies.getAll({ domain: COOKIE_DOMAIN });
  const out = {};
  for (const c of all) {
    if (WANT.includes(c.name) && c.value) out[c.name] = c.value;
  }
  return out;
}

function tell(tabId, ok, text) {
  if (tabId == null) return;
  chrome.tabs.sendMessage(tabId, { type: "ii-durum", ok, text }).catch(() => {});
}

async function trySend() {
  const p = await getPending();
  if (!p) return;
  const cookies = await collect();
  if (!cookies.sessionid) {
    tell(p.tabId, false, "Önce bu sayfada Instagram'a giriş yap; giriş bitince oturum otomatik programa gönderilecek.");
    return;
  }
  try {
    const res = await fetch(`http://127.0.0.1:${p.port}/api/extension-session`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token: p.token, cookies }),
    });
    await chrome.storage.session.remove(PENDING);
    if (res.ok) tell(p.tabId, true, "Oturum Instagram İndirici'ye gönderildi. Programa dönebilirsin.");
    else tell(p.tabId, false, "Program oturumu kabul etmedi. Programda 'Chrome'dan giriş yap'a tekrar bas.");
  } catch (e) {
    await chrome.storage.session.remove(PENDING);
    tell(p.tabId, false, "Instagram İndirici programına ulaşılamadı. Program açık mı?");
  }
}

chrome.runtime.onMessage.addListener((message, sender) => {
  if (!message || message.type !== "ii-basla") return;
  const port = Number(message.port);
  if (!(port >= 1024 && port <= 65535) || !/^[A-Za-z0-9_-]{8,64}$/.test(String(message.token))) return;
  chrome.storage.session
    .set({ [PENDING]: { port, token: String(message.token), tabId: sender.tab ? sender.tab.id : null, expires: Date.now() + TTL_MS } })
    .then(trySend);
});

// Kullanıcı o sayfada giriş yapınca (oturum çerezi yeni yazılınca) otomatik gönder.
chrome.cookies.onChanged.addListener((info) => {
  const c = info.cookie;
  if (!info.removed && c && c.name === "sessionid" && c.domain.replace(/^\./, "").endsWith(COOKIE_DOMAIN)) trySend();
});
