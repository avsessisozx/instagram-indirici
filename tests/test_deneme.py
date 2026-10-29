"""Chrome "Deneme" eklentisi: gerçek Chromium'a yüklenir, sahte Instagram'a karşı raporu üretir."""

import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests import fake_instagram as fi  # noqa: E402

EXT_SRC = Path(__file__).resolve().parents[1] / "chrome_deneme"


@unittest.skipUnless(os.environ.get("INSTAGRAM_INDIRICI_BROWSER", "chromium") == "chromium",
                     "eklenti testi yalnızca Playwright Chromium ile çalışır")
class DenemeExtension(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fake = fi.FakeInstagram().start()
        cls.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        cls.ext = Path(cls.tmp.name) / "ext"
        shutil.copytree(EXT_SRC, cls.ext)
        manifest = json.loads((cls.ext / "manifest.json").read_text(encoding="utf-8"))
        for script in manifest["content_scripts"]:
            script["matches"] = ["http://127.0.0.1/*"]
        (cls.ext / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.fake.stop()
        cls.tmp.cleanup()

    def setUp(self):
        self.fake.reset()
        self.fake.autologin = False

    def run_report(self, name="p", page_fetch=False):
        from playwright.sync_api import sync_playwright

        pw = sync_playwright().start()
        ctx = pw.chromium.launch_persistent_context(
            str(Path(self.tmp.name) / name), channel="chromium", headless=True,
            args=[f"--disable-extensions-except={self.ext}", f"--load-extension={self.ext}"])
        self.addCleanup(pw.stop)
        self.addCleanup(ctx.close)
        ctx.add_cookies([{"name": "sessionid", "value": "gecerli", "url": self.fake.base + "/", "httpOnly": True},
                         {"name": "csrftoken", "value": "sahteCSRF123", "url": self.fake.base + "/"}])
        page = ctx.new_page()
        page.goto(f"{self.fake.base}/{fi.PROFILE_OK}/")
        page.wait_for_selector("#ii-deneme-btn", timeout=15000)
        if page_fetch:
            # Sayfanın kendi isteği (hook.js yakalar): kullanıcı adı → kimlik eşleşmesi rapora girmeli
            page.evaluate(
                "fetch('/api/v1/users/web_profile_info/?username=" + fi.PROFILE_OK + "', "
                "{headers: {'X-IG-App-ID': '936619743392459', 'X-Requested-With': 'XMLHttpRequest'}, "
                "credentials: 'include'}).then(r => r.text())")
            time.sleep(1.0)
        page.click("#ii-deneme-btn")
        end = time.monotonic() + 60
        text = ""
        while time.monotonic() < end:
            text = page.evaluate("document.querySelector('#ii-deneme-root textarea').value")
            if "Bitti." in text:
                return text
            time.sleep(0.5)
        self.fail("rapor zamanında bitmedi:\n" + text)

    def test_report_lists_every_request(self):
        text = self.run_report()
        for label in ("1) profil bilgisi", "2) gönderi listesi", "3) hikayeler", "4) öne çıkan listesi",
                      "5) öne çıkan içeriği", "6) kullanıcı bilgisi"):
            self.assertIn(label, text)
        self.assertIn("HTTP 200", text)
        self.assertIn(f"@{fi.PROFILE_OK}", text)
        self.assertNotIn("gecerli", text)          # oturum değeri rapora girmez
        self.assertNotIn("sahteCSRF123", text)

    def test_report_includes_page_own_requests_and_ids(self):
        text = self.run_report("p3", page_fetch=True)
        self.assertIn("gelen kimlikler: 1001", text)
        self.assertIn("/api/v1/users/web_profile_info/ -> 200", text)
        self.assertIn("Sayfadaki kimlik alanları:", text)
        self.assertNotIn("gecerli", text)

    def test_report_shows_failing_codes(self):
        self.fake.feed_404 = True
        text = self.run_report("p2")
        self.assertRegex(text, r"2\) gönderi listesi \(REST\): HTTP 404")


if __name__ == "__main__":
    unittest.main()
