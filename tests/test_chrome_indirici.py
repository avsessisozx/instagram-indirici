"""Chrome eklentisi (chrome_indirici): gerçek Chromium'a yüklenir, sahte Instagram'dan gerçekten dosya indirir."""

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

EXT_SRC = Path(__file__).resolve().parents[1] / "chrome_indirici"


@unittest.skipUnless(os.environ.get("INSTAGRAM_INDIRICI_BROWSER", "chromium") == "chromium",
                     "eklenti testi yalnızca Playwright Chromium ile çalışır")
class ChromeIndirici(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fake = fi.FakeInstagram().start()
        cls.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        cls.ext = Path(cls.tmp.name) / "ext"
        shutil.copytree(EXT_SRC, cls.ext)
        manifest = json.loads((cls.ext / "manifest.json").read_text(encoding="utf-8"))
        for script in manifest["content_scripts"]:
            script["matches"] = ["http://127.0.0.1/*"]
        manifest["host_permissions"] = ["http://127.0.0.1/*"]
        (cls.ext / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.fake.stop()
        cls.tmp.cleanup()

    def setUp(self):
        self.fake.reset()
        self.fake.autologin = False

    def launch(self, name):
        from playwright.sync_api import sync_playwright

        profile = Path(self.tmp.name) / name
        downloads = Path(self.tmp.name) / (name + "_dl")
        (profile / "Default").mkdir(parents=True)
        downloads.mkdir()
        (profile / "Default" / "Preferences").write_text(json.dumps({"download": {
            "default_directory": str(downloads), "prompt_for_download": False, "directory_upgrade": True}}))
        pw = sync_playwright().start()
        ctx = pw.chromium.launch_persistent_context(
            str(profile), channel="chromium", headless=True,
            args=[f"--disable-extensions-except={self.ext}", f"--load-extension={self.ext}"])
        self.addCleanup(pw.stop)
        self.addCleanup(ctx.close)
        ctx.add_cookies([{"name": "sessionid", "value": "gecerli", "url": self.fake.base + "/", "httpOnly": True}])
        worker = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event("serviceworker", timeout=15000)
        ext_id = worker.url.split("/")[2]
        panel = ctx.new_page()
        panel.goto(f"chrome-extension://{ext_id}/panel.html")
        return panel, downloads

    def run_engine(self, panel, target, **settings):
        settings.update(base=self.fake.base, speed="fast", folder="IIT")
        panel.evaluate("""([t, s]) => { window.__e = new IIEngine.Engine(() => {}); window.__p = window.__e.run(t, Object.assign({}, IIEngine.DEFAULTS, s)); }""",
                       [target, settings])
        end = time.monotonic() + 240
        while time.monotonic() < end:
            if not panel.evaluate("window.__e.running"):
                break
            time.sleep(1)
        else:
            self.fail("bitmedi:\n" + panel.evaluate("window.__e.report()"))
        return panel.evaluate("window.__e.report()")

    def test_profile_downloads_everything(self):
        panel, _dl = self.launch("a")
        report = self.run_engine(panel, fi.PROFILE_OK)
        files = panel.evaluate("window.__e.saved")
        text = "\n".join(files)
        self.assertTrue(any("/Hikayeler/" in f for f in files), report + "\n" + text)
        self.assertTrue(any("/Öne Çıkanlar/" in f for f in files), report + "\n" + text)
        self.assertTrue(any("/Gönderiler/" in f for f in files), report + "\n" + text)
        self.assertTrue(any("/Reels/" in f for f in files), report + "\n" + text)
        self.assertTrue(any("Profil Fotoğrafı" in f for f in files), report + "\n" + text)
        self.assertTrue(any(f.endswith("/bilgi.csv") for f in files), report + "\n" + text)
        posts = [f for f in files if "/Gönderiler/" in f and not f.endswith(".txt")]
        self.assertGreaterEqual(len(posts), fi.TOTAL_POSTS - 5, report + "\n" + text)
        self.assertTrue(all(f.startswith("IIT/" + fi.PROFILE_OK + "/") for f in files), text)
        self.assertIn("files_failed: 0", report)

        # İkinci çalıştırma (dosya denetimi kapalı): geçmişte olanlar tekrar inmez
        report2 = self.run_engine(panel, fi.PROFILE_OK, verify_files=False)
        self.assertRegex(report2, r"files_done: [01]\n", report2)   # yalnız profil fotoğrafı yeniden iner

    def test_single_post_link(self):
        panel, _dl = self.launch("b")
        code = fi.shortcode(fi.POST_PK_BASE) if hasattr(fi, "POST_PK_BASE") else None
        item = self.fake.data.posts[fi.PROFILE_OK][0]
        report = self.run_engine(panel, f"https://www.instagram.com/p/{item['code']}/", posts=True)
        files = panel.evaluate("window.__e.saved")
        self.assertTrue(files, report)
        self.assertIn("files_failed: 0", report)


if __name__ == "__main__":
    unittest.main()
