"""Chrome eklentisi (Giriş Köprüsü) uçtan uca: gerçek Chromium'a eklenti yüklenir, sahte Instagram + gerçek program sunucusu.

Çalıştırmak için Playwright'ın kendi Chromium'u gerekir (Edge/Chrome kanalı eklenti yüklemeyi desteklemeyebilir):
  INSTAGRAM_INDIRICI_BROWSER=chromium python tools/run_tests.py tests.test_extension
Başka bir tarayıcı seçiliyse testler atlanır.
"""

import json
import os
import shutil
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests import fake_instagram as fi  # noqa: E402

EXT_SRC = Path(__file__).resolve().parents[1] / "instagram_indirici" / "chrome_eklenti"


def make_test_extension(dst: Path) -> Path:
    """Gerçek eklentinin kopyası; yalnızca adresler sahte Instagram'ın (127.0.0.1) adresine çevrilir."""
    shutil.copytree(EXT_SRC, dst)
    manifest = json.loads((dst / "manifest.json").read_text(encoding="utf-8"))
    manifest["host_permissions"] = ["http://127.0.0.1/*"]
    manifest["content_scripts"][0]["matches"] = ["http://127.0.0.1/*"]
    (dst / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    bg = (dst / "background.js").read_text(encoding="utf-8")
    assert 'const COOKIE_DOMAIN = "instagram.com";' in bg
    (dst / "background.js").write_text(bg.replace('const COOKIE_DOMAIN = "instagram.com";',
                                                   'const COOKIE_DOMAIN = "127.0.0.1";'), encoding="utf-8")
    return dst


@unittest.skipUnless(os.environ.get("INSTAGRAM_INDIRICI_BROWSER", "chromium") == "chromium",
                     "eklenti testi yalnızca Playwright Chromium ile çalışır")
class ExtensionE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fake = fi.FakeInstagram().start()
        cls.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        cls.env = {
            "INSTAGRAM_INDIRICI_BASE": cls.fake.base,
            "INSTAGRAM_INDIRICI_DATA": str(Path(cls.tmp.name) / "data"),
            "INSTAGRAM_INDIRICI_BROWSER": "chromium",
            "INSTAGRAM_INDIRICI_HEADLESS": "1",
            "INSTAGRAM_INDIRICI_NO_OPEN": "1",
            "INSTAGRAM_INDIRICI_DELAY": "0-0.05",
            "INSTAGRAM_INDIRICI_BACKOFF": "1,2",
        }
        cls.saved = {k: os.environ.get(k) for k in cls.env}
        os.environ.update(cls.env)
        from instagram_indirici.server import AppState, start_server
        cls.state = AppState()
        cls.server = start_server(cls.state)
        cls.ext = make_test_extension(Path(cls.tmp.name) / "ext")

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.fake.stop()
        for k, v in cls.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        cls.tmp.cleanup()

    def setUp(self):
        self.fake.reset()
        self.fake.autologin = False

    # ------------------------------------------------------------ yardımcılar
    def start_chrome_login(self) -> str:
        req = urllib.request.Request(f"http://127.0.0.1:{self.state.port}/api/login-chrome", data=b"{}",
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=10) as r:
            self.assertTrue(json.loads(r.read())["ok"])
        self.assertTrue(self.state.chrome_waiting())
        return self.state.ext_token

    def open_chrome(self, profile: str):
        from playwright.sync_api import sync_playwright

        pw = sync_playwright().start()
        ctx = pw.chromium.launch_persistent_context(
            str(Path(self.tmp.name) / profile), channel="chromium", headless=True,
            args=[f"--disable-extensions-except={self.ext}", f"--load-extension={self.ext}"])
        self.addCleanup(pw.stop)
        self.addCleanup(ctx.close)
        return ctx

    def wait_job(self, timeout=90):
        end = time.monotonic() + timeout
        engine = self.state.engine
        while time.monotonic() < end:
            if engine.mode == "cookie" and not engine.running and engine.phase in ("done", "error"):
                return engine.snapshot()
            time.sleep(0.3)
        self.fail("oturum içe aktarma işi zamanında bitmedi")

    def banner(self, page, timeout=15):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            text = page.evaluate("document.getElementById('ii-kopru-bilgi') && document.getElementById('ii-kopru-bilgi').textContent")
            if text:
                return text
            time.sleep(0.3)
        return ""

    # ------------------------------------------------------------ testler
    def test_1_already_logged_in_chrome_sends_session(self):
        token = self.start_chrome_login()
        ctx = self.open_chrome("p1")
        ctx.add_cookies([
            {"name": "sessionid", "value": "gecerli", "url": self.fake.base + "/", "httpOnly": True},
            {"name": "mid", "value": "MIDDEGERI", "url": self.fake.base + "/"},
        ])
        page = ctx.new_page()
        page.goto(f"{self.fake.base}/#ii={self.state.port}.{token}")
        snap = self.wait_job()
        self.assertEqual(snap["phase"], "done", snap["logs"])
        self.assertEqual(snap["summary"]["key"], "engine.cookie_ok")
        self.assertIs(snap["logged_in"], True)
        self.assertIn("gönderildi", self.banner(page))
        self.assertNotIn("#ii=", page.url, "kod adres çubuğundan silinmeli")
        self.assertFalse(self.state.chrome_waiting(), "kod tek kullanımlık olmalı")
        self.assertNotIn("gecerli", json.dumps(snap["logs"], ensure_ascii=False))

    def test_2_login_after_opening_sends_session_automatically(self):
        token = self.start_chrome_login()
        ctx = self.open_chrome("p2")
        page = ctx.new_page()
        page.goto(f"{self.fake.base}/#ii={self.state.port}.{token}")
        self.assertIn("giriş yap", self.banner(page))          # girişli değil: bekliyor
        self.assertTrue(self.state.chrome_waiting())
        self.fake.autologin = True
        page.goto(f"{self.fake.base}/accounts/login/")           # sahte sunucu oturum çerezini verir
        snap = self.wait_job()
        self.assertEqual(snap["summary"]["key"], "engine.cookie_ok", snap["logs"])
        self.assertIs(snap["logged_in"], True)

    def test_3_wrong_token_is_rejected(self):
        self.start_chrome_login()
        ctx = self.open_chrome("p3")
        ctx.add_cookies([{"name": "sessionid", "value": "gecerli", "url": self.fake.base + "/", "httpOnly": True}])
        before = self.state.engine.mode
        page = ctx.new_page()
        page.goto(f"{self.fake.base}/#ii={self.state.port}.YANLISKOD123456")
        self.assertIn("kabul etmedi", self.banner(page))
        self.assertFalse(self.state.engine.running)
        self.assertEqual(self.state.engine.mode, before)
        self.assertTrue(self.state.chrome_waiting(), "yanlış kod gerçek kodu bozmamalı")


if __name__ == "__main__":
    unittest.main()
