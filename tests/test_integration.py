"""Uçtan uca testler: gerçek tarayıcı + sahte Instagram sunucusu.

Çalıştırmak için Playwright'ın bir tarayıcıya erişebilmesi gerekir:
  * Linux/geliştirme: INSTAGRAM_INDIRICI_BROWSER=chromium
  * Windows (GitHub derlemesi): INSTAGRAM_INDIRICI_BROWSER=msedge

Testler alfabetik sırayla çalışır: önce giriş yapılmadan (a_), sonra giriş (b_), sonra
giriş yapılmış hesapla (c_) senaryolar.
"""

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests import fake_instagram as fi  # noqa: E402


class IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fake = fi.FakeInstagram().start()
        cls.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        os.environ["INSTAGRAM_INDIRICI_BASE"] = cls.fake.base
        os.environ["INSTAGRAM_INDIRICI_DATA"] = str(Path(cls.tmp.name) / "data")
        os.environ.setdefault("INSTAGRAM_INDIRICI_BROWSER", "chromium")
        os.environ["INSTAGRAM_INDIRICI_HEADLESS"] = "1"
        os.environ["INSTAGRAM_INDIRICI_IDLE"] = "4"
        os.environ["INSTAGRAM_INDIRICI_DELAY"] = "0-0.05"
        os.environ["INSTAGRAM_INDIRICI_BACKOFF"] = "1,2"
        os.environ["INSTAGRAM_INDIRICI_BREAK"] = "0"
        os.environ["INSTAGRAM_INDIRICI_RESUME_WAIT"] = "8"
        os.environ["INSTAGRAM_INDIRICI_RESUME_ROUNDS"] = "3"

    @classmethod
    def tearDownClass(cls):
        cls.fake.stop()
        cls.tmp.cleanup()

    def setUp(self):
        self.fake.reset()
        from instagram_indirici.history import History
        h = History()   # her test temiz geçmişle başlasın
        h.clear()
        h.close()

    # ------------------------------------------------------------ yardımcılar
    def run_job(self, link, mode, timeout=180, out=None, **settings):
        from instagram_indirici.engine import Engine, Job

        out = out or Path(self.tmp.name) / f"out_{time.monotonic_ns()}"
        engine = Engine()
        # Geçmiş ve "sadece yeni" testler arasında birbirini etkilemesin diye varsayılan kapalı;
        # bu özellikleri deneyen testler açıkça açar.
        base = {"out_dir": str(out), "content": "all", "limit": 0, "posts": True, "stories": True,
                "highlights": True, "avatar": True, "only_new": False, "history": False, "auto_continue": False,
                # 1.7 özellikleri eski testleri etkilemesin diye kapalı; yeni testler açıkça açar.
                "reels": False, "reposts": False, "tagged": False, "verify_files": False, "info_csv": False,
                "caption_min": 0}
        base.update(settings)
        error = engine.start(Job(link, mode, base))
        self.assertIsNone(error, error and error.key)
        deadline = time.monotonic() + timeout
        verify_seen = False
        while engine.running and time.monotonic() < deadline:
            verify_seen = verify_seen or engine.verify
            time.sleep(0.2)
        self.assertFalse(engine.running, "iş zamanında bitmedi")
        snap = engine.snapshot()
        snap["verify_seen"] = verify_seen
        if os.environ.get("TEST_VERBOSE"):
            print(f"\n--- {self._testMethodName}: {snap['phase']} {snap['summary']}")
            for line in snap["logs"]:
                text = line.get("text") or json.dumps(line.get("msg"), ensure_ascii=False)
                print(f"  [{line['level']}] {text[:300]}")
        return snap, out

    def keys(self, snap):
        return [(line.get("msg") or {}).get("key") for line in snap["logs"]]

    def files(self, out):
        return sorted(str(p.relative_to(out)).replace("\\", "/") for p in out.rglob("*")
                      if p.is_file() and not p.name.startswith(".instagram-indirici"))

    def assert_clean(self, out):
        files = self.files(out)
        self.assertFalse(any(f.endswith(".part") for f in files), files)

    def ensure_login(self):
        from instagram_indirici.engine import MODE_LOGIN

        self.fake.autologin = True
        snap, _ = self.run_job("", MODE_LOGIN, timeout=60)
        self.fake.autologin = False
        self.assertEqual(snap["summary"]["key"], "engine.login_done_ok", snap["logs"])
        return snap

    # ------------------------------------------------------------ yeni ayarlar: kimlik ve hız modu
    def test_a0_identity_android_changes_user_agent(self):
        snap, out = self.run_job(f"https://www.instagram.com/{fi.PROFILE_OK}/", "profile", identity="android")
        self.assertEqual(snap["phase"], "done", snap["summary"])
        self.assertTrue(self.fake.user_agents)
        self.assertTrue(all("Android" in ua and "Mobile" in ua for ua in self.fake.user_agents), self.fake.user_agents)

    def test_a0_identity_desktop_keeps_browser_agent(self):
        snap, out = self.run_job(f"https://www.instagram.com/{fi.PROFILE_OK}/", "profile", identity="desktop")
        self.assertEqual(snap["phase"], "done", snap["summary"])
        self.assertTrue(self.fake.user_agents)
        self.assertFalse(any("Android" in ua for ua in self.fake.user_agents), self.fake.user_agents)

    # ------------------------------------------------------------ giriş yapılmadan
    def test_a1_logged_out_profile_login_wall(self):
        snap, out = self.run_job(f"https://www.instagram.com/{fi.PROFILE_OK}/", "profile")
        self.assertEqual(snap["phase"], "done", snap["summary"])
        self.assertIs(snap["logged_in"], False)
        self.assertTrue(snap["login_wall"])
        keys = self.keys(snap)
        self.assertIn("engine.stories_skipped_login", keys)
        self.assertIn("engine.highlights_skipped_login", keys)
        # Giriş yapmadan 2 sayfa (24 gönderi) görünüyor
        self.assertEqual(snap["found_posts"], 2 * fi.PAGE)
        files = self.files(out)
        self.assertTrue(all(f.startswith(f"{fi.PROFILE_OK}/") for f in files), files)
        # Profil fotoğrafı (giriş yapmadan: en büyük açık adres)
        self.assertTrue(any(f.startswith(f"{fi.PROFILE_OK}/Profil Fotoğrafı/") for f in files), files)
        # Kısıtlanmış video atlandı (i=8)
        self.assertEqual(snap["posts_unavailable"], 1)
        self.assert_clean(out)
        # Albüm parçaları ve en yüksek kalite video
        albums = [f for f in files if "_01." in f or "_02." in f or "_03." in f]
        self.assertTrue(albums, files)
        videos = [out / f for f in files if f.endswith(".mp4")]
        self.assertTrue(videos)
        for v in videos:
            self.assertIn(b"_1080.mp4", v.read_bytes(), v.name)
        # Uzantı içerik türünden: webp gelen resim .webp kaydedilir
        self.assertTrue(any(f.endswith(".webp") for f in files), files)
        # Dosya adında tarih ve başlık
        self.assertTrue(any("2025-10-0" in f and "Gönderi 1" in f for f in files), files)

    def test_a1b_page_429_is_retried(self):
        self.fake.page_429_once = True
        snap, out = self.run_job(fi.PROFILE_OK, "profile", limit=3, stories=False, highlights=False, avatar=False)
        self.assertEqual(snap["phase"], "done", snap["summary"])
        self.assertIn("browser.page_refused", self.keys(snap))
        self.assertEqual(snap["found_posts"], 3)

    def test_a1c_profile_api_refused_logged_out(self):
        self.fake.profile_api_refused = True
        snap, out = self.run_job(fi.PROFILE_OK, "profile", stories=False, highlights=False)
        self.assertEqual(snap["phase"], "done", snap["summary"])
        self.assertIn("browser.refused", self.keys(snap))
        self.assertNotIn("browser.slow_down", self.keys(snap))
        self.assertEqual(snap["found_posts"], 2 * fi.PAGE)
        self.assertTrue(snap["login_wall"])

    def test_a2_not_found(self):
        snap, out = self.run_job("@yok.boyle.biri", "profile", timeout=90)
        self.assertEqual(snap["phase"], "error")
        self.assertEqual(snap["summary"]["key"], "browser.user_not_found", snap["summary"])

    def test_a3_profile_needs_login(self):
        snap, out = self.run_job(fi.PROFILE_FORCE_LOGIN, "profile", timeout=90)
        self.assertEqual(snap["phase"], "error")
        self.assertEqual(snap["summary"]["key"], "browser.profile_login_required")
        self.assertTrue(snap["login_wall"])

    def test_a4_single_post_logged_out(self):
        item = self.fake.data.posts[fi.PROFILE_OK][3]   # i=2: albüm
        snap, out = self.run_job(f"https://www.instagram.com/p/{item['code']}/?igsh=abc", "single", timeout=90)
        self.assertEqual(snap["phase"], "done", snap["summary"])
        files = self.files(out)
        self.assertEqual(len(files), 3, files)
        self.assertTrue(any(f.endswith("_02.mp4") for f in files), files)

    def test_a5_stories_need_login(self):
        snap, out = self.run_job(f"https://www.instagram.com/stories/{fi.PROFILE_OK}/", "single", timeout=60)
        self.assertEqual(snap["phase"], "error")
        self.assertEqual(snap["summary"]["key"], "engine.stories_need_login")

    def test_a6_graphql_without_video_urls_is_repaired(self):
        self.fake.embed_posts = False
        self.fake.graphql_video_urls = False
        snap, out = self.run_job(fi.PROFILE_OK, "profile", limit=12, stories=False, highlights=False, avatar=False)
        self.assertEqual(snap["phase"], "done", snap["summary"])
        self.assertIn("engine.repairing", self.keys(snap))
        files = self.files(out)
        videos = [f for f in files if f.endswith(".mp4")]
        # İlk 12 gönderide (sabitlenmiş + 0..10): 2 video (1, 6) + 2 albümde 1'er video (2, 7); 8 videosuz
        self.assertEqual(len(videos), 4, files)
        self.assertEqual(snap["posts_unavailable"], 1)

    def test_a7_stop(self):
        from instagram_indirici.engine import Engine, Job

        self.fake.slow_cdn = 0.5
        out = Path(self.tmp.name) / "stop"
        engine = Engine()
        engine.start(Job(fi.PROFILE_OK, "profile", {"out_dir": str(out), "stories": False, "highlights": False}))
        deadline = time.monotonic() + 60
        while engine.files_done < 2 and time.monotonic() < deadline:
            time.sleep(0.1)
        engine.stop()
        engine.wait(30)
        snap = engine.snapshot()
        self.assertFalse(snap["running"])
        self.assertEqual(snap["phase"], "stopped")
        self.assertLess(snap["files_done"], 20)
        self.assert_clean(out)

    # ------------------------------------------------------------ oturum kodu yapıştırma
    def test_b0_cookie_1_rejected_code(self):
        from instagram_indirici.engine import MODE_COOKIE

        snap, _ = self.run_job("yanlis-kod-123", MODE_COOKIE, timeout=90)
        self.assertEqual(snap["phase"], "error", snap["logs"])
        self.assertEqual(snap["summary"]["key"], "engine.cookie_expired", snap["logs"])
        self.assertNotEqual(snap["logged_in"], True)
        self.assertNotIn("yanlis-kod-123", json.dumps(snap, ensure_ascii=False))

    def test_b0_cookie_2_accepted_code_logs_in(self):
        from instagram_indirici.engine import MODE_COOKIE

        snap, _ = self.run_job("Cookie: csrftoken=sahteCSRF123; sessionid=gecerli", MODE_COOKIE, timeout=90)
        self.assertEqual(snap["phase"], "done", snap["logs"])
        self.assertEqual(snap["summary"]["key"], "engine.cookie_ok", snap["logs"])
        self.assertIs(snap["logged_in"], True)
        self.assertNotIn("gecerli", json.dumps(snap["logs"], ensure_ascii=False))
        # Oturum tarayıcı profilinde kalıcı: sonraki iş girişli çalışır (giriş penceresi açılmadan).
        snap2, _ = self.run_job(fi.PROFILE_OK, "profile", timeout=240)
        self.assertEqual(snap2["phase"], "done", snap2["summary"])
        self.assertIs(snap2["logged_in"], True)
        self.assertFalse(snap2["login_wall"])

    def test_b0_cookie_3_rejected_code_keeps_old_session(self):
        from instagram_indirici.engine import MODE_COOKIE

        self.ensure_login()
        snap, _ = self.run_job("baska-yanlis-kod", MODE_COOKIE, timeout=90)
        self.assertEqual(snap["summary"]["key"], "engine.cookie_expired", snap["logs"])
        snap2, _ = self.run_job(fi.PROFILE_OK, "profile", timeout=240)
        self.assertEqual(snap2["phase"], "done", snap2["summary"])
        self.assertIs(snap2["logged_in"], True)
        self.assertFalse(snap2["login_wall"])

    # ------------------------------------------------------------ giriş
    def test_b1_login(self):
        snap = self.ensure_login()
        self.assertIs(snap["logged_in"], True)

    # ------------------------------------------------------------ giriş yapılmış
    def test_c1_full_profile_with_rate_limit_and_challenge(self):
        self.ensure_login()
        self.fake.rate_limit_once = True
        self.fake.challenge_once = True
        snap, out = self.run_job(fi.PROFILE_OK, "profile", timeout=240)
        self.assertEqual(snap["phase"], "done", snap["summary"])
        self.assertIs(snap["logged_in"], True)
        self.assertFalse(snap["login_wall"])
        keys = self.keys(snap)
        self.assertIn("browser.slow_down", keys)
        self.assertIn("browser.verify", keys)
        self.assertIn("browser.verify_done", keys)
        self.assertTrue(snap["verify_seen"])
        self.assertEqual(snap["found_posts"], fi.TOTAL_POSTS + 1)
        files = self.files(out)
        prefix = f"{fi.PROFILE_OK}/"
        stories = [f for f in files if f.startswith(prefix + "Hikayeler/")]
        self.assertEqual(len(stories), 3, files)
        self.assertEqual(len([f for f in stories if f.endswith(".mp4")]), 1)
        tatil = [f for f in files if f.startswith(prefix + "Öne Çıkanlar/Tatil/")]
        is_proje = [f for f in files if f.startswith(prefix + "Öne Çıkanlar/İş Proje/")]
        self.assertEqual((len(tatil), len(is_proje)), (2, 3), files)
        self.assertIn(prefix + "Profil Fotoğrafı/9988776655.jpg", files)
        posts = [f for f in files if f.startswith((prefix + "Gönderiler/", prefix + "Reels/"))]
        self.assertTrue(all(f.endswith(".mp4") for f in files if f.startswith(prefix + "Reels/")))
        self.assertFalse(any(f.endswith(".mp4") for f in files if f.startswith(prefix + "Gönderiler/Fotoğraflar/")))
        self.assertTrue(any(f.startswith(prefix + "Gönderiler/Albümler/") for f in files))
        self.assertEqual([f for f in files if f.count("/") == 1], [], "kişinin klasöründe başıboş dosya kalmamalı")
        # 41 gönderi: 8 albüm (3 dosya), 8 video, 1 videosuz (atlanır), geri kalanı tek resim
        albums = sum(1 for i in range(fi.TOTAL_POSTS) if i % 5 == 2)
        videos = sum(1 for i in range(fi.TOTAL_POSTS) if i % 5 == 1)
        broken = 1
        singles = fi.TOTAL_POSTS - albums - videos - broken + 1  # +1 sabitlenmiş
        self.assertEqual(len(posts), albums * 3 + videos + singles, len(posts))
        self.assertEqual(snap["files_failed"], 0)
        self.assert_clean(out)
        from instagram_indirici.history import History
        h = History()
        self.assertEqual(h.complete_newest("1001", "all"), fi.BASE_TS)
        h.close()
        type(self).full_out = out

        # Aynı klasöre tekrar: sadece yeni gönderilere bakar, hiçbir şey tekrar inmez
        self.fake.reset()
        snap2, _ = self.run_job(fi.PROFILE_OK, "profile", out=out, stories=False, highlights=False, only_new=True)
        self.assertEqual(snap2["phase"], "done", snap2["summary"])
        self.assertIn("engine.only_new", self.keys(snap2))
        self.assertIn("engine.up_to_date", self.keys(snap2))
        self.assertEqual(snap2["files_done"], 0)
        feed_calls = [p for p, _ in self.fake.api_calls if p.startswith("/api/v1/feed/user/")]
        self.assertEqual(len(feed_calls), 1, feed_calls)

    def test_c2_rest_broken_uses_graphql(self):
        self.ensure_login()
        self.fake.break_rest = True
        snap, out = self.run_job(fi.PROFILE_OK, "profile", stories=False, highlights=False, avatar=False)
        self.assertEqual(snap["phase"], "done", snap["summary"])
        self.assertEqual(snap["found_posts"], fi.TOTAL_POSTS + 1)
        self.assertIn("browser.strategy_failed", self.keys(snap))
        self.assertTrue(any(p.startswith("/graphql/query") and li for p, li in self.fake.api_calls))

    def test_c3_everything_broken_scrolls(self):
        self.ensure_login()
        self.fake.break_rest = True
        self.fake.break_graphql = True
        self.fake.break_profile_api = True
        snap, out = self.run_job(fi.PROFILE_OK, "profile", stories=False, highlights=False, avatar=False)
        self.assertEqual(snap["phase"], "done", snap["summary"])
        self.assertIn("browser.scrolling", self.keys(snap))
        self.assertEqual(snap["found_posts"], fi.TOTAL_POSTS + 1)

    def test_c3b_profile_api_and_feed_refused(self):
        """Kullanıcının ilk denemesinde görülen durum: Instagram profil bilgisini hiç vermiyor."""
        self.ensure_login()
        self.fake.profile_api_refused = True
        self.fake.feed_rate_always = True
        snap, out = self.run_job(fi.PROFILE_OK, "profile", timeout=120)
        self.assertEqual(snap["phase"], "done", snap["summary"])
        self.assertFalse(snap["rate_limited"])
        keys = self.keys(snap)
        self.assertIn("browser.refused", keys)
        self.assertEqual(snap["found_posts"], fi.TOTAL_POSTS + 1)
        files = self.files(out)
        self.assertEqual(len([f for f in files if "/Hikayeler/" in f]), 3)

    def test_c4_private_profile(self):
        self.ensure_login()
        snap, out = self.run_job(fi.PROFILE_PRIVATE, "profile")
        self.assertEqual(snap["phase"], "done", snap["summary"])
        self.assertEqual(snap["notes"][0]["key"], "engine.private_logged_in")
        files = self.files(out)
        self.assertEqual(len(files), 1, files)
        self.assertIn("Profil Fotoğrafı", files[0])

    def test_c5_empty_profile(self):
        self.ensure_login()
        snap, out = self.run_job(fi.PROFILE_EMPTY, "profile", avatar=False)
        self.assertEqual(snap["found_posts"], 0)
        self.assertEqual(snap["summary"]["key"], "engine.empty", snap["summary"])

    def test_c6_video_only_with_limit(self):
        self.ensure_login()
        snap, out = self.run_job(fi.PROFILE_OK, "profile", content="video", limit=12,
                                 stories=False, highlights=False, avatar=False)
        self.assertEqual(snap["phase"], "done", snap["summary"])
        self.assertEqual(snap["found_posts"], 12)
        self.assertIn("engine.limit_reached", self.keys(snap))
        files = self.files(out)
        self.assertTrue(files)
        self.assertTrue(all(f.endswith(".mp4") for f in files), files)

    # ---- yüksek kalite (DASH): ayrı görüntü + ses akışları ffmpeg ile birleştirilir
    def _dash_streams(self):
        """ffmpeg ile küçük, gerçek bir görüntü ve ses dosyası üretir (yoksa test atlanır)."""
        import subprocess
        from instagram_indirici.fetcher import find_ffmpeg

        exe = find_ffmpeg()
        if not exe:
            self.skipTest("ffmpeg yok")
        d = Path(self.tmp.name) / "dashsrc"
        d.mkdir(exist_ok=True)
        v, a = d / "v.mp4", d / "a.mp4"
        run = lambda *args: subprocess.run([exe, "-y", "-v", "error", *args], check=True)
        run("-f", "lavfi", "-i", "testsrc=size=160x284:rate=10", "-t", "2", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", str(v))
        run("-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-c:a", "aac", str(a))
        return exe, v.read_bytes(), a.read_bytes()

    def _streams_of(self, exe, path):
        import subprocess
        info = subprocess.run([exe, "-hide_banner", "-i", str(path)], capture_output=True, text=True).stderr
        return ("Video:" in info, "Audio:" in info)

    def test_c8_video_uses_dash_streams(self):
        self.ensure_login()
        exe, vbytes, abytes = self._dash_streams()
        self.fake.dash_video, self.fake.dash_audio = vbytes, abytes
        self.fake.data.enable_dash()
        try:
            video = self.fake.data.posts[fi.PROFILE_OK][2]     # i=1: video
            snap, out = self.run_job(f"https://www.instagram.com/reel/{video['code']}/", "single")
        finally:
            self.fake.data.disable_dash()
        self.assertEqual(snap["phase"], "done", snap["summary"])
        files = self.files(out)
        self.assertEqual(len(files), 1, files)
        self.assert_clean(out)
        self.assertEqual(self._streams_of(exe, out / files[0]), (True, True))   # hem görüntü hem ses var
        self.assertIn("engine.quality_dash", self.keys(snap))
        line = next(l for l in snap["logs"] if (l.get("msg") or {}).get("key") == "engine.quality_dash")
        self.assertEqual(line["msg"]["params"]["res"], "1080x1920")

    def test_c9_dash_failure_falls_back_to_plain_version(self):
        self.ensure_login()
        exe, vbytes, abytes = self._dash_streams()
        self.fake.dash_video, self.fake.dash_audio = vbytes, abytes
        self.fake.data.enable_dash()
        video = self.fake.data.posts[fi.PROFILE_OK][2]
        self.fake.cdn_403 = {f"{video['pk']}_dashv1920.mp4"}
        try:
            snap, out = self.run_job(f"https://www.instagram.com/reel/{video['code']}/", "single")
        finally:
            self.fake.data.disable_dash()
        self.assertEqual(snap["phase"], "done", snap["summary"])
        files = self.files(out)
        self.assertEqual(len(files), 1, files)
        self.assert_clean(out)
        self.assertIn(b"_1080.mp4", (out / files[0]).read_bytes())      # normal sürüm indi
        self.assertIn("engine.quality_fallback", self.keys(snap))

    def test_c7_single_items_logged_in(self):
        self.ensure_login()
        video = self.fake.data.posts[fi.PROFILE_OK][2]   # i=1: video
        self.fake.cdn_403 = {video["video_versions"][1]["url"].split("/cdn/")[1].split("?")[0]}
        snap, out = self.run_job(f"https://www.instagram.com/reel/{video['code']}/", "single")
        self.assertEqual(snap["phase"], "done", snap["summary"])
        files = self.files(out)
        self.assertEqual(len(files), 1, files)
        # En iyi adres 403 verdi; sıradaki (720p) indi
        self.assertIn(b"_720.mp4", (out / files[0]).read_bytes())
        self.assertTrue(any(p.startswith("/api/v1/media/") for p, _ in self.fake.api_calls))

        snap, out = self.run_job(f"https://www.instagram.com/stories/{fi.PROFILE_OK}/3500000000000000001/", "single")
        self.assertEqual(snap["phase"], "done", snap["summary"])
        self.assertEqual(self.files(out), [f"{fi.PROFILE_OK}/Hikayeler/2025-10-09_3500000000000000001.jpg"]
                         if time.localtime(fi.BASE_TS + 3600).tm_mday == 9 else self.files(out))

        snap, out = self.run_job("https://www.instagram.com/stories/highlights/17900000000000002/", "single")
        self.assertEqual(snap["phase"], "done", snap["summary"])
        files = self.files(out)
        self.assertEqual(len(files), 3, files)
        self.assertTrue(all("/Öne Çıkanlar/İş Proje/" in f for f in files), files)

        # Gönderi linkinden sahibinin profilinin tamamı
        snap, out = self.run_job(f"https://www.instagram.com/p/{video['code']}/", "profile",
                                 stories=False, highlights=False, avatar=False)
        self.assertEqual(snap["phase"], "done", snap["summary"])
        self.assertEqual(snap["found_posts"], fi.TOTAL_POSTS + 1)


    # ------------------------------------------------------------ 1.1: klasör düzeni, geçmiş, devam
    def test_c8_highlights_robust(self):
        """Öne çıkan listesi reddediliyor ve çoklu istekte sadece ilkinin öğeleri geliyor."""
        self.ensure_login()
        self.fake.highlights_tray_refused = True
        self.fake.highlights_batch_truncate = True
        snap, out = self.run_job(fi.PROFILE_OK, "profile", posts=False, stories=False, avatar=False, timeout=120)
        self.assertEqual(snap["phase"], "done", snap["summary"])
        keys = self.keys(snap)
        self.assertIn("browser.highlights_from_page", keys)
        self.assertEqual(keys.count("engine.highlight_items"), len(self.fake.data.HIGHLIGHTS))
        files = self.files(out)
        for hid, title in self.fake.data.HIGHLIGHTS.items():
            folder = f"{fi.PROFILE_OK}/Öne Çıkanlar/{title.replace('/', ' ')}/"
            n = len([f for f in files if f.startswith(folder)])
            self.assertEqual(n, len(self.fake.data.highlight_items(hid)), (title, files))

    def test_d1_old_layout_files_are_moved(self):
        self.ensure_login()
        out = Path(self.tmp.name) / "eski_duzen"
        first = self.fake.data.posts[fi.PROFILE_OK][1]   # i=0, tek resim
        from instagram_indirici.media import build_post
        stem = build_post(first, content="all").stem
        old = out / fi.PROFILE_OK / f"{stem}.jpg"
        old.parent.mkdir(parents=True)
        old.write_bytes(b"eski dosya")
        snap, out = self.run_job(fi.PROFILE_OK, "profile", out=out, stories=False, highlights=False, avatar=False,
                                 limit=3)
        self.assertEqual(snap["phase"], "done", snap["summary"])
        self.assertFalse(old.exists())
        moved = list((out / fi.PROFILE_OK / "Gönderiler" / "Fotoğraflar").glob(f"{stem}.*"))
        self.assertEqual(len(moved), 1, moved)
        self.assertEqual(moved[0].read_bytes(), b"eski dosya")

    def test_d2_session_killed_then_continue_with_other_account(self):
        """Kullanıcının yaşadığı: ~150 gönderiden sonra Instagram oturumu kapatıyor."""
        self.ensure_login()
        self.fake.session_kill_after = 2
        snap, out1 = self.run_job(fi.PROFILE_OK, "profile", stories=False, highlights=False, avatar=False,
                                  history=True)
        self.assertEqual(snap["phase"], "done", snap["summary"])
        self.assertTrue(snap["login_wall"])
        self.assertIn("engine.session_expired_partial", [n["key"] for n in snap["notes"]])
        self.assertEqual(snap["found_posts"], 2 * fi.PAGE)
        first_files = self.files(out1)
        self.assertTrue(first_files)

        # Başka hesapla (burada aynı sahte hesap) giriş, başka bir klasöre: kaldığı yerden devam.
        self.fake.reset()
        self.ensure_login()
        snap, out2 = self.run_job(fi.PROFILE_OK, "profile", stories=False, highlights=False, avatar=False,
                                  history=True)
        self.assertEqual(snap["phase"], "done", snap["summary"])
        keys = self.keys(snap)
        self.assertIn("engine.history_known", keys)
        self.assertIn("engine.resume_found", keys)
        self.assertIn("browser.resuming", keys)
        feed_calls = [p for p, _ in self.fake.api_calls if p.startswith("/api/v1/feed/user/")]
        self.assertEqual(len(feed_calls), 3, feed_calls)       # baştan 1 sayfa + kalan 2 sayfa
        second_files = self.files(out2)
        names1 = {Path(f).name for f in first_files}
        self.assertFalse(names1 & {Path(f).name for f in second_files}, "önceden inenler tekrar indi")
        # Baştaki sayfa (sabitlenmiş + 11 gönderi, 1'i videosuz) geçmişte bulundu, sonra atlandı
        self.assertEqual(snap["posts_known"], fi.PAGE - 1)
        self.assertEqual(snap["found_posts"], fi.PAGE + fi.TOTAL_POSTS + 1 - 2 * fi.PAGE)
        # Liste bitti: artık tamamlandı sayılır, devam noktası silinir
        from instagram_indirici.history import History
        h = History()
        self.assertIsNone(h.resume_point("1001", "all"))
        self.assertEqual(h.complete_newest("1001", "all"), fi.BASE_TS)
        h.clear()
        h.close()

    def test_d3_rate_limited_waits_and_continues(self):
        self.ensure_login()
        self.fake.rate_after = 2
        self.fake.rate_window = 12
        snap, out = self.run_job(fi.PROFILE_OK, "profile", stories=False, highlights=False, avatar=False,
                                 history=True, auto_continue=True, timeout=240)
        self.assertEqual(snap["phase"], "done", snap["summary"])
        keys = self.keys(snap)
        self.assertIn("engine.auto_wait", keys)
        self.assertIn("engine.auto_resume", keys)
        self.assertEqual(snap["found_posts"], fi.TOTAL_POSTS + 1)
        self.assertEqual(snap["files_failed"], 0)
        self.assertFalse(snap["rate_limited"])
        self.assertGreaterEqual(snap["files_done"], 50)
        from instagram_indirici.history import History
        h = History()
        h.clear()
        h.close()


    # ------------------------------------------------------------ 1.7: sekmeler, açıklama, eksik dosya, yedek yollar
    def test_e1_reels_tab_is_collected_separately(self):
        self.ensure_login()
        snap, out = self.run_job(fi.PROFILE_OK, "profile", reels=True, stories=False, highlights=False, avatar=False)
        self.assertEqual(snap["phase"], "done", snap["summary"])
        files = self.files(out)
        reels = [f for f in files if f.startswith(f"{fi.PROFILE_OK}/Reels/")]
        # ızgaradaki 8 klip + yalnız Reels sekmesinde olan 5 klip (videosu olmayan 1 klip indirilemez)
        self.assertEqual(len(reels), 13, reels)
        self.assertEqual(len({Path(f).name for f in reels}), 13)
        self.assertEqual(snap["tabs"]["reels"]["found"], 14)
        self.assertEqual(snap["tabs"]["reels"]["failed"], 0)
        self.assertFalse(any("/Videolar/" in f and f.endswith(".mp4") for f in files))

    def test_e2_reels_fall_back_to_tab_page_scroll(self):
        self.ensure_login()
        self.fake.clips_refused = True
        snap, out = self.run_job(fi.PROFILE_OK, "profile", reels=True, posts=False, stories=False,
                                 highlights=False, avatar=False)
        self.assertEqual(snap["phase"], "done", snap["summary"])
        self.assertIn("browser.scrolling", self.keys(snap))
        reels = [f for f in self.files(out) if f.startswith(f"{fi.PROFILE_OK}/Reels/")]
        self.assertGreaterEqual(len(reels), 5)

    def test_e3_long_caption_saved_next_to_media(self):
        self.ensure_login()
        snap, out = self.run_job(fi.PROFILE_OK, "profile", reels=True, caption_min=100, posts=False,
                                 stories=False, highlights=False, avatar=False)
        files = self.files(out)
        txt = [f for f in files if f.endswith(".txt")]
        self.assertEqual(len(txt), 1, txt)
        self.assertIn(txt[0][:-4] + ".mp4", files)
        self.assertIn("uzun açıklama", (out / txt[0]).read_text(encoding="utf-8"))

    def test_e4_missing_files_are_redownloaded_even_if_history_knows_them(self):
        self.ensure_login()
        snap, out = self.run_job(fi.PROFILE_OK, "profile", history=True, stories=False, highlights=False, avatar=False)
        self.assertEqual(snap["phase"], "done", snap["summary"])
        before = self.files(out)
        victims = [f for f in before if f.startswith(f"{fi.PROFILE_OK}/Reels/")][:2]
        for f in victims:
            (out / f).unlink()
        snap2, _ = self.run_job(fi.PROFILE_OK, "profile", out=out, history=True, only_new=True, verify_files=True,
                                stories=False, highlights=False, avatar=False)
        self.assertEqual(snap2["phase"], "done", snap2["summary"])
        self.assertEqual(snap2["posts_repaired"], 2)
        self.assertEqual(self.files(out), before)

    def test_e5_highlights_fall_back_to_their_own_page_on_http_500(self):
        self.ensure_login()
        os.environ["INSTAGRAM_INDIRICI_5XX_WAIT"] = "0"
        try:
            self.fake.highlights_500 = True
            snap, out = self.run_job(fi.PROFILE_OK, "profile", posts=False, stories=False, avatar=False)
        finally:
            os.environ.pop("INSTAGRAM_INDIRICI_5XX_WAIT", None)
        self.assertEqual(snap["phase"], "done", snap["summary"])
        self.assertIn("browser.highlight_from_page", self.keys(snap))
        files = self.files(out)
        self.assertEqual(len([f for f in files if f.startswith(f"{fi.PROFILE_OK}/Öne Çıkanlar/Tatil/")]), 2, files)

    def test_e5b_unanswered_highlight_list_goes_straight_to_pages(self):
        """Raporda görülen: liste isteğine yanıt gelmiyor; her öne çıkan için dakikalarca beklenmemeli."""
        self.ensure_login()
        os.environ["INSTAGRAM_INDIRICI_QUICK_TIMEOUT"] = "1"
        try:
            self.fake.highlights_hang = 4.0
            snap, out = self.run_job(fi.PROFILE_OK, "profile", posts=False, stories=False, avatar=False)
        finally:
            os.environ.pop("INSTAGRAM_INDIRICI_QUICK_TIMEOUT", None)
            self.fake.highlights_hang = 0.0
        self.assertEqual(snap["phase"], "done", snap["summary"])
        self.assertIn("browser.highlight_from_page", self.keys(snap))
        files = self.files(out)
        self.assertEqual(len([f for f in files if f.startswith(f"{fi.PROFILE_OK}/Öne Çıkanlar/Tatil/")]), 2, files)

    def test_e6_short_http_500_is_retried(self):
        self.ensure_login()
        os.environ["INSTAGRAM_INDIRICI_5XX_WAIT"] = "0"
        try:
            self.fake.reels_media_fail_n = 2
            snap, out = self.run_job(fi.PROFILE_OK, "profile", posts=False, highlights=False, avatar=False)
        finally:
            os.environ.pop("INSTAGRAM_INDIRICI_5XX_WAIT", None)
        self.assertEqual(snap["phase"], "done", snap["summary"])
        self.assertEqual(len([f for f in self.files(out) if "/Hikayeler/" in f]), 3)

    def test_e7_info_csv_is_written(self):
        self.ensure_login()
        snap, out = self.run_job(fi.PROFILE_OK, "profile", info_csv=True, limit=5, stories=False, highlights=False,
                                 avatar=False)
        csv_text = (out / fi.PROFILE_OK / "bilgi.csv").read_text(encoding="utf-8-sig")
        lines = [l for l in csv_text.splitlines() if l]
        self.assertTrue(lines[0].startswith("kod;tür;tarih"))
        self.assertEqual(len(lines), 6, lines)

    def test_e8_date_range_limits_posts(self):
        self.ensure_login()
        # BASE_TS ≈ 2025-10-09; her gönderi bir gün arayla eskiye gider
        snap, out = self.run_job(fi.PROFILE_OK, "profile", date_from="2025-09-25", date_to="2025-09-30",
                                 stories=False, highlights=False, avatar=False)
        self.assertEqual(snap["phase"], "done", snap["summary"])
        self.assertLessEqual(snap["found_posts"], 7)
        self.assertGreaterEqual(snap["found_posts"], 5)

    def test_e9_tagged_and_reposts_are_optional_tabs(self):
        self.ensure_login()
        snap, out = self.run_job(fi.PROFILE_OK, "profile", tagged=True, reposts=True, posts=False, stories=False,
                                 highlights=False, avatar=False)
        self.assertEqual(snap["phase"], "done", snap["summary"])
        files = self.files(out)
        self.assertEqual(len([f for f in files if "/Etiketlenenler/" in f]), 5, files)
        self.assertEqual(len([f for f in files if "/Yeniden Paylaşımlar/" in f]), 2, files)


if __name__ == "__main__":
    unittest.main()
