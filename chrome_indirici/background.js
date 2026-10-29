// Instagram İndirici – arka plan: simgeye ya da sayfadaki düğmeye basınca denetim sayfasını açar.
const PANEL = chrome.runtime.getURL("panel.html");

async function openPanel(link) {
  const tabs = await chrome.tabs.query({ url: PANEL + "*" });
  const url = PANEL + (link ? "?u=" + encodeURIComponent(link) : "");
  if (tabs.length) {
    await chrome.tabs.update(tabs[0].id, { active: true, url: link ? url : undefined });
    if (tabs[0].windowId) chrome.windows.update(tabs[0].windowId, { focused: true });
  } else {
    await chrome.tabs.create({ url });
  }
}

chrome.action.onClicked.addListener(() => openPanel(""));
chrome.runtime.onMessage.addListener((msg) => {
  if (msg && msg.ii === 1 && msg.cmd === "open-panel") openPanel(msg.url);
});
