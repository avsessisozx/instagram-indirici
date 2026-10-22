// Instagram İndirici – Giriş Köprüsü (sayfa tarafı)
// Program, Instagram'ı şu adresle açar:  https://www.instagram.com/#ii=<port>.<kod>
// (# işaretinden sonrası Instagram'a hiç gönderilmez.) Burada o bilgi okunur, arka plana verilir
// ve adres çubuğundan silinir. Sayfadaki hiçbir şey okunmaz ya da değiştirilmez.
(function () {
  const WHERE = /[#&]ii=(\d{2,5})\.([A-Za-z0-9_-]{8,64})/;

  function banner(text, ok) {
    try {
      let box = document.getElementById("ii-kopru-bilgi");
      if (!box) {
        box = document.createElement("div");
        box.id = "ii-kopru-bilgi";
        box.style.cssText =
          "position:fixed;z-index:2147483647;left:50%;top:14px;transform:translateX(-50%);" +
          "max-width:min(92vw,520px);padding:12px 18px;border-radius:12px;font:600 15px/1.4 system-ui,sans-serif;" +
          "color:#fff;box-shadow:0 6px 24px rgba(0,0,0,.35);text-align:center";
        (document.body || document.documentElement).appendChild(box);
      }
      box.style.background = ok ? "#1e8e3e" : "#c5221f";
      box.textContent = text;
    } catch (e) { /* sayfa hazır değilse sessiz geç */ }
  }

  chrome.runtime.onMessage.addListener((message) => {
    if (message && message.type === "ii-durum") banner(message.text, message.ok);
  });

  const found = WHERE.exec(location.hash || "");
  if (!found) return;
  try { history.replaceState(null, "", location.pathname + location.search); } catch (e) { /* önemsiz */ }
  chrome.runtime.sendMessage({ type: "ii-basla", port: Number(found[1]), token: found[2] });
})();
