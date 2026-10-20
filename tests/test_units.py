"""Tarayıcı gerektirmeyen hızlı testler."""

import json
import os
import sys
import tempfile
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from instagram_indirici import links, media  # noqa: E402
from instagram_indirici.browser import ApiResult, classify, parse_json_text  # noqa: E402
from instagram_indirici.engine import known_newest, needs_repair, read_marker, write_marker  # noqa: E402
from instagram_indirici.i18n import translate  # noqa: E402
from instagram_indirici.naming import sanitize  # noqa: E402
from tests import fake_instagram as fi  # noqa: E402


class LinkTests(unittest.TestCase):
    def check(self, text, kind, **expected):
        p = links.parse_link(text)
        self.assertTrue(p.ok, f"{text}: {p.error and p.error.key}")
        self.assertEqual(p.kind, kind, text)
        for key, value in expected.items():
            self.assertEqual(getattr(p, key), value, f"{text}: {key}")

    def fail(self, text, key):
        p = links.parse_link(text)
        self.assertFalse(p.ok, text)
        self.assertEqual(p.error.key, key, text)

    def test_profiles(self):
        self.check("nasa", links.PROFILE, username="nasa")
        self.check("@NASA", links.PROFILE, username="nasa")
        self.check("  @nat.geo_tr  ", links.PROFILE, username="nat.geo_tr")
        self.check("https://www.instagram.com/nasa/", links.PROFILE, username="nasa")
        self.check("instagram.com/nasa?igsh=MWQ1ZGUxMzBkMA==", links.PROFILE, username="nasa")
        self.check("https://instagram.com/nasa/reels/", links.PROFILE, username="nasa")
        self.check("https://m.instagram.com/nasa/tagged/", links.PROFILE, username="nasa")
        self.check("https://www.instagram.com/Nasa", links.PROFILE, username="nasa")

    def test_posts(self):
        self.check("https://www.instagram.com/p/C8abcDEF123/", links.POST, shortcode="C8abcDEF123")
        self.check("https://www.instagram.com/reel/DA_b-12xYz/?igsh=x", links.POST, shortcode="DA_b-12xYz")
        self.check("https://www.instagram.com/reels/DA_b-12xYz/", links.POST, shortcode="DA_b-12xYz")
        self.check("https://www.instagram.com/tv/B1abcdEFG/", links.POST, shortcode="B1abcdEFG")
        self.check("https://www.instagram.com/nasa/p/C8abcDEF123/", links.POST, shortcode="C8abcDEF123")
        self.check("Şuna bak: https://www.instagram.com/reel/DAbc_12-x/ çok iyi", links.POST, shortcode="DAbc_12-x")

    def test_stories(self):
        self.check("https://www.instagram.com/stories/nasa/", links.STORY, username="nasa", story_media_id="")
        self.check("https://www.instagram.com/stories/nasa/3456789012345678901/?utm=1", links.STORY,
                   story_media_id="3456789012345678901")
        self.check("https://www.instagram.com/stories/highlights/17912345678901234/", links.HIGHLIGHT,
                   highlight_id="17912345678901234")
        self.check("https://www.instagram.com/s/aGlnaGxpZ2h0OjE3OTEyMzQ1Njc4OTAxMjM0?story_media_id=1",
                   links.HIGHLIGHT, highlight_id="17912345678901234")
        self.check("https://www.instagram.com/share/BAabc123xyz/", links.SHARE)

    def test_errors(self):
        self.fail("", "link.empty")
        self.fail("https://www.tiktok.com/@kisi/video/1", "link.other_site")
        self.fail("https://www.threads.net/@kisi", "link.threads")
        self.fail("https://www.instagram.com/", "link.no_target")
        self.fail("https://www.instagram.com/explore/tags/yaz/", "link.explore")
        self.fail("https://www.instagram.com/direct/inbox/", "link.direct")
        self.fail("https://www.instagram.com/p/", "link.post_broken")
        self.fail("bu bir cümle, link yok", "link.not_found")

    def test_shortcode_roundtrip(self):
        for pk in (1, 63, 64, 3394145943868104119, 3400000000001001005):
            self.assertEqual(int(links.id_from_shortcode(links.shortcode_from_id(pk))), pk)
        self.assertEqual(links.shortcode_from_id("3400000000001001005_1001"), fi.shortcode(3400000000001001005))
        # Gizli hesap paylaşım kodu: sonundaki 28 karakter atılır
        code = fi.shortcode(3394145943868104119)
        self.assertEqual(links.id_from_shortcode(code + "x" * 28), "3394145943868104119")


class MediaTests(unittest.TestCase):
    def setUp(self):
        self.data = fi.Data("https://cdn.test")
        self.posts = self.data.posts[fi.PROFILE_OK]

    def test_find_media_in_nested_response(self):
        blob = {"data": {"xdt_api__v1__feed__user_timeline_graphql_connection": {
            "edges": [{"node": p} for p in self.posts[:5]], "page_info": {"has_next_page": True, "end_cursor": "x"}}}}
        found = media.find_media(blob)
        self.assertEqual([media.media_key(f) for f in found], [p["code"] for p in self.posts[:5]])
        self.assertEqual(media.find_page_info(blob)["end_cursor"], "x")

    def test_carousel_children_are_not_separate_posts(self):
        album = self.posts[3]
        self.assertTrue(album.get("carousel_media"))
        self.assertEqual(len(media.find_media({"items": [album]})), 1)

    def test_best_video_and_image(self):
        video = self.posts[2]
        self.assertTrue(media.video_urls(video)[0].split("?")[0].endswith("_1080.mp4"))
        self.assertTrue(media.image_urls(self.posts[1])[0].split("?")[0].endswith("_n.jpg"))
        node = self.data.graphql_node(self.posts[1])
        self.assertTrue(media.image_urls(node)[0].split("?")[0].endswith("_n.jpg"))

    def test_dash_picks_best_streams(self):
        item = dict(self.posts[2])
        item["video_dash_manifest"] = self.data.manifest(int(item["pk"]))
        choice = media.dash_choice(item)
        self.assertEqual((choice.video.width, choice.video.height), (1080, 1920))
        self.assertEqual(choice.video.bandwidth, 4200000)
        self.assertIn("_dashv1920.mp4", choice.video.url)
        self.assertIn("&oe=6A0B1C2D", choice.video.url)      # &amp; çözülmüş olmalı
        self.assertTrue(choice.audio.url.split("?")[0].endswith("_dasha.mp4"))
        silent = dict(item, video_dash_manifest=self.data.manifest(int(item["pk"]), audio=False))
        self.assertIsNone(media.dash_choice(silent).audio)

    def test_dash_ignores_segmented_and_broken_manifests(self):
        item = dict(self.posts[2])
        segmented = ('<MPD xmlns="urn:mpeg:dash:schema:mpd:2011"><Period><AdaptationSet contentType="video">'
                     '<SegmentTemplate media="s$Number$.m4s" initialization="i.mp4"/>'
                     '<Representation width="1080" height="1920" bandwidth="1"><BaseURL>https://x/y.mp4</BaseURL>'
                     '</Representation></AdaptationSet></Period></MPD>')
        self.assertIsNone(media.dash_choice(dict(item, video_dash_manifest=segmented)))
        self.assertIsNone(media.dash_choice(dict(item, video_dash_manifest="<MPD><broken")))
        self.assertIsNone(media.dash_choice(dict(item, video_dash_manifest="")))
        self.assertIsNone(media.dash_choice(item))
        nested = dict(item, dash_info={"video_dash_manifest": self.data.manifest(int(item["pk"]))})
        self.assertIsNotNone(media.dash_choice(nested))

    def test_dash_used_only_when_not_worse_than_plain(self):
        item = dict(self.posts[2])      # normal sürümlerin en büyüğü 1080x1920
        item["video_dash_manifest"] = self.data.manifest(int(item["pk"]), top=1920)
        file = media.build_post(item, content="all").files[0]
        self.assertIsNotNone(file.dash)
        self.assertEqual(file.plain_res, "1080x1920")
        item["video_dash_manifest"] = self.data.manifest(int(item["pk"]), top=1280)   # DASH en çok 720p
        self.assertIsNone(media.build_post(item, content="all").files[0].dash)
        # normal sürümler 720p ile sınırlıysa 1080p DASH tercih edilir
        low = dict(item, video_versions=[v for v in item["video_versions"] if v["width"] <= 720],
                   video_dash_manifest=self.data.manifest(int(item["pk"]), top=1920))
        built = media.build_post(low, content="all").files[0]
        self.assertEqual(built.dash.video.height, 1920)
        self.assertEqual(built.plain_res, "720x1280")

    def test_dash_alone_keeps_video_without_plain_urls(self):
        item = dict(self.posts[2])
        item["video_versions"] = []
        item["video_dash_manifest"] = self.data.manifest(int(item["pk"]))
        post = media.build_post(item, content="all")
        self.assertIsNone(post.skipped_reason)
        self.assertEqual(len(post.files), 1)
        self.assertEqual(post.files[0].urls, [])
        self.assertIsNotNone(post.files[0].dash)

    def test_build_post_v1_and_graphql_agree(self):
        for item in self.posts[:12]:
            a = media.build_post(item, content="all")
            b = media.build_post(self.data.graphql_node(item), content="all")
            self.assertEqual(a.stem, b.stem)
            self.assertEqual([f.rel_stem for f in a.files], [f.rel_stem for f in b.files])
            self.assertEqual([f.kind for f in a.files], [f.kind for f in b.files])

    def test_content_filters(self):
        album = self.posts[3]
        v = media.build_post(album, content="video")
        self.assertEqual([f.kind for f in v.files], ["video"])
        self.assertEqual(v.files[0].rel_stem.rsplit("_", 1)[-1], "02")
        image_only = media.build_post(self.posts[2], content="image")
        self.assertEqual(image_only.skipped_reason.key, "skip.image_only")
        broken = media.build_post(self.posts[9], content="all")   # i=8
        self.assertEqual(broken.skipped_reason.key, "skip.no_video")

    def test_story_and_highlight_names(self):
        story = self.data.stories(fi.PROFILE_OK)[0]
        post = media.build_post(story, content="all", kind=media.KIND_STORY, subdir=media.STORIES_DIR)
        self.assertTrue(post.files[0].rel_stem.startswith("Hikayeler/"))
        self.assertTrue(post.files[0].rel_stem.endswith("_3500000000000000000"))
        self.assertEqual(media.highlight_folder("İş/Proje", "1"), "Öne Çıkanlar/İş Proje")
        self.assertEqual(media.highlight_folder("", "179"), "Öne Çıkanlar/179")

    def test_needs_repair_only_for_old_format(self):
        item = dict(self.posts[2])
        node = self.data.graphql_node(item)
        node.pop("video_url")
        post = media.build_post(node, content="all")
        self.assertTrue(needs_repair(node, post))
        broken = self.posts[9]
        self.assertFalse(needs_repair(broken, media.build_post(broken, content="all")) and "video_versions" in broken)

    def test_avatar(self):
        user = {"username": "x", "profile_pic_id": "123_456",
                "hd_profile_pic_url_info": {"url": "https://c/a_hd.jpg"},
                "profile_pic_url_hd": "https://c/a_320.jpg", "profile_pic_url": "https://c/a_150.jpg"}
        urls = media.avatar_urls(user)
        self.assertEqual(urls, ["https://c/a_hd.jpg", "https://c/a_320.jpg", "https://c/a_150.jpg"])
        post = media.build_avatar(user, urls, content="all")
        self.assertEqual(post.files[0].rel_stem, "Profil Fotoğrafı/123")

    def test_find_user_prefers_richest(self):
        blob = [{"user": {"username": "nasa", "pk": "5"}},
                {"x": {"username": "NASA", "pk": "5", "full_name": "N", "is_private": False}}]
        self.assertEqual(media.find_user(blob, "nasa")["full_name"], "N")


class ClassifyTests(unittest.TestCase):
    def res(self, status, data, url="https://www.instagram.com/api/v1/x/"):
        return ApiResult(status, url, False, json.dumps(data), data)

    def test_classify(self):
        self.assertEqual(classify(self.res(200, {"items": [], "status": "ok"})), "ok")
        self.assertEqual(classify(self.res(401, {"message": "Please wait a few minutes before you try again.",
                                                 "require_login": True, "status": "fail"})), "rate")
        self.assertEqual(classify(self.res(429, None)), "rate")
        self.assertEqual(classify(self.res(401, {"message": "login_required", "status": "fail"})), "login")
        self.assertEqual(classify(self.res(400, {"message": "challenge_required", "challenge": {"url": "/c/"}})), "challenge")
        self.assertEqual(classify(self.res(400, {"message": "checkpoint_required", "checkpoint_url": "/c/"})), "challenge")
        self.assertEqual(classify(ApiResult(200, "https://www.instagram.com/accounts/login/?next=x", True, "<html>")), "login")
        self.assertEqual(classify(ApiResult(200, "https://www.instagram.com/challenge/x/", True, "<html>")), "challenge")
        self.assertEqual(classify(self.res(404, {"status": "fail"})), "notfound")
        self.assertEqual(classify(ApiResult(500, "https://www.instagram.com/api/v1/x/", False, "<html>")), "error")
        self.assertEqual(classify(self.res(200, {"message": "feedback_required", "spam": True, "status": "fail"})), "rate")

    def test_parse_json_text(self):
        self.assertEqual(parse_json_text('for (;;);{"a":1}'), {"a": 1})
        self.assertEqual(parse_json_text('{"a":1}\n{"b":2}'), {"a": 1})
        self.assertIsNone(parse_json_text("<html>"))


class MarkerTests(unittest.TestCase):
    def test_marker_roundtrip_and_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "kisi"
            write_marker(folder, {"user_id": "1", "complete": {"video": 100}})
            marker = read_marker(folder)
            self.assertEqual(known_newest(marker, "video"), 100)
            self.assertEqual(known_newest(marker, "all"), 0)      # sadece video tarandıysa resimler eksik
            self.assertEqual(known_newest({"complete": {"all": 50}}, "image"), 50)
            write_marker(folder, {"user_id": "1", "complete": {"video": 200}})
            self.assertEqual(known_newest(read_marker(folder), "video"), 200)


class HistoryTests(unittest.TestCase):
    def test_history(self):
        from instagram_indirici.history import History
        with tempfile.TemporaryDirectory() as tmp:
            h = History(Path(tmp) / "g.sqlite3")
            h.mark("1", "A", "all", "post", "x")
            h.mark("1", "B", "video", "post", "x")
            self.assertEqual(h.known("1", "video"), {"A", "B"})
            self.assertEqual(h.known("1", "image"), {"A"})     # video seçimiyle inenler resimleri kapsamaz
            self.assertEqual(h.known("2", "all"), set())
            h.set_resume_point("1", "all", "rest", "24", "x")
            self.assertEqual(h.resume_point("1", "all"), ("rest", "24"))
            self.assertEqual(h.resume_point("1", "video"), ("rest", "24"))
            h.set_complete("1", "all", 500, "x")
            self.assertIsNone(h.resume_point("1", "all"))
            self.assertEqual(h.complete_newest("1", "image"), 500)
            h.set_complete("1", "all", 300, "x")
            self.assertEqual(h.complete_newest("1", "all"), 500)
            self.assertEqual(h.clear(), 2)
            self.assertEqual(h.known("1", "all"), set())
            h.close()

    def test_post_folders(self):
        data = fi.Data("https://cdn.test")
        album = data.posts[fi.PROFILE_OK][3]
        post = media.build_post(album, content="all")
        self.assertEqual([f.rel_stem.split("/")[0] for f in post.files], ["Fotoğraflar", "Videolar", "Fotoğraflar"])
        self.assertTrue(all(f.legacy_stem and "/" not in f.legacy_stem for f in post.files))
        story = media.build_post(data.stories(fi.PROFILE_OK)[2], content="all", kind=media.KIND_STORY,
                                 subdir=media.STORIES_DIR)
        self.assertTrue(story.files[0].rel_stem.startswith("Hikayeler/"))
        self.assertEqual(story.files[0].legacy_stem, "")


class NamingAndTextTests(unittest.TestCase):
    def test_sanitize(self):
        self.assertEqual(sanitize('a<b>c:"d"/e\\f|g?h*'), "a b c d e f g h")
        self.assertEqual(sanitize("CON"), "CON_")
        self.assertEqual(sanitize("   "), "adsiz")

    def test_translations_complete(self):
        self.assertEqual(translate("link.profile_detected", "tr", name="nasa"), "Profil algılandı: @nasa")
        self.assertEqual(translate("summary", "tr", done=2, skipped=1), "Bitti: 2 dosya indirildi, 1 dosya zaten vardı.")


if __name__ == "__main__":
    unittest.main()


class PacingAndIdentityTests(unittest.TestCase):
    def test_careful_modes_are_slower(self):
        from instagram_indirici.pacing import Pacing
        n, c, v = (Pacing.for_speed(x) for x in ("normal", "careful", "very_careful"))
        self.assertEqual(n.call_delay, (2.5, 5.0))        # Windows'ta eskiden beri kullanılan değer
        self.assertEqual(n.page_delay, (0.0, 0.0))
        self.assertGreater(c.call_delay[0], n.call_delay[0])
        self.assertGreater(v.call_delay[0], c.call_delay[0])
        self.assertGreater(v.page_delay[1], c.page_delay[1])
        self.assertLess(v.break_every, c.break_every)
        self.assertEqual(Pacing.for_speed("bogus"), n)

    def test_env_overrides(self):
        from instagram_indirici.pacing import Pacing
        with mock.patch.dict(os.environ, {"INSTAGRAM_INDIRICI_DELAY": "0-0.05", "INSTAGRAM_INDIRICI_BREAK": "0",
                                          "INSTAGRAM_INDIRICI_PAGE_DELAY": "0"}):
            p = Pacing.for_speed("very_careful").with_env()
        self.assertEqual(p.call_delay, (0.0, 0.05))
        self.assertEqual(p.break_every, 0)
        self.assertEqual(p.page_delay, (0.0, 0.0))

    def test_identity_user_agents(self):
        from instagram_indirici import identity
        cur = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36 Edg/126.0.0.0"
        self.assertIn("Windows NT 10.0", identity.user_agent_for("desktop", cur))
        phone = identity.user_agent_for("android", cur)
        self.assertTrue("Android" in phone and "Mobile" in phone and "Chrome/126" in phone)
        tablet = identity.user_agent_for("tablet", cur)
        self.assertTrue("Android" in tablet and "Mobile" not in tablet)
        self.assertIn("Windows NT", identity.user_agent_for("bogus", cur))


class PaceCounterTests(unittest.TestCase):
    def test_highlights_do_not_count_as_list_pages(self):
        import asyncio
        import threading

        from instagram_indirici.browser import InstagramBrowser
        from instagram_indirici.pacing import Pacing

        pc = Pacing((0.0, 0.0), (0.0, 0.0), 2, (0.0, 0.0))
        with tempfile.TemporaryDirectory() as tmp:
            b = InstagramBrowser(Path(tmp), log=lambda *a: None, cancel=threading.Event(), on_verify=lambda v: None,
                                 pacing=pc)
            asyncio.run(b._pace(counts_as_page=False))
            asyncio.run(b._pace(counts_as_page=False))
            self.assertEqual(b.list_pages, 0)
            asyncio.run(b._pace())
            asyncio.run(b._pace())
            self.assertEqual(b.list_pages, 2)


class SettingsTests(unittest.TestCase):
    def test_new_settings_are_cleaned(self):
        from instagram_indirici.paths import clean_settings
        s = clean_settings({})
        self.assertEqual((s["identity"], s["speed"], s["setup_done"], s["no_confirm"]), ("desktop", "normal", False, False))
        s = clean_settings({"identity": "tablet", "speed": "careful", "setup_done": True, "no_confirm": 1})
        self.assertEqual((s["identity"], s["speed"], s["setup_done"], s["no_confirm"]), ("tablet", "careful", True, True))
        s = clean_settings({"identity": "toaster", "speed": "ludicrous"})
        self.assertEqual((s["identity"], s["speed"]), ("desktop", "normal"))


class ReportTests(unittest.TestCase):
    def test_report_has_log_and_no_secrets(self):
        from instagram_indirici import report
        snap = {"running": False, "phase": "done", "found_posts": 3, "files_done": 2,
                "summary": {"key": "summary", "params": {"done": 2}},
                "logs": [{"t": "10:00:01", "level": "info", "msg": {"key": "browser.opened", "params": {"name": "Edge"}}},
                         {"t": "10:00:02", "level": "debug", "text": "cookie sessionid=abc123SECRET; csrftoken: tok999"}]}
        text = report.build([("Sürüm", "1.6.0")], {"out_dir": "C:/gizli", "speed": "careful"}, snap, "tr")
        self.assertIn("Sürüm: 1.6.0", text)
        self.assertIn("Edge", text)
        self.assertIn("careful", text)
        self.assertNotIn("C:/gizli", text)
        self.assertNotIn("abc123SECRET", text)
        self.assertNotIn("tok999", text)
        self.assertIn("[silindi]", text)

    def test_report_is_capped(self):
        from instagram_indirici import report
        logs = [{"t": "00:00:00", "level": "info", "text": "x" * 200 + str(i)} for i in range(2000)]
        text = report.build([], {}, {"logs": logs}, "en")
        self.assertLessEqual(len(text), report.MAX_CHARS + 200)
        self.assertIn("1999", text)                  # en yeni satırlar korunur
        self.assertNotIn("x" * 200 + "0\n", text)    # en eski satırlar atılır


class HistoryProfilesTests(unittest.TestCase):
    def test_profiles_and_forget(self):
        from instagram_indirici.history import History
        with tempfile.TemporaryDirectory() as tmp:
            h = History(Path(tmp) / "h.sqlite3")
            for i in range(3):
                h.mark("u1", f"k{i}", "all", "post", "natgeo")
            h.mark("u2", "k0", "all", "post", "sahte.kisi")
            h._db.execute("UPDATE downloaded SET at=at-100000 WHERE user_id='u2'")
            h._db.commit()
            profiles = h.profiles()
            self.assertEqual([p["username"] for p in profiles], ["natgeo", "sahte.kisi"])   # en son indirilen başta
            self.assertEqual([p["count"] for p in profiles], [3, 1])
            self.assertEqual(h.forget("u1"), 3)
            self.assertEqual([p["user_id"] for p in h.profiles()], ["u2"])
            self.assertEqual(h.forget(""), 0)
            h.close()


class ServerFeatureTests(unittest.TestCase):
    """Windows sunucusunun yeni uç noktaları (tarayıcı gerektirmez)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.env = mock.patch.dict(os.environ, {"INSTAGRAM_INDIRICI_DATA": str(Path(self.tmp.name) / "data"),
                                                "INSTAGRAM_INDIRICI_NO_OPEN": "1"})
        self.env.start()
        from instagram_indirici.server import AppState, start_server
        self.state = AppState()
        self.server = start_server(self.state)
        self.base = f"http://127.0.0.1:{self.state.port}"

    def tearDown(self):
        self.server.shutdown()
        self.env.stop()
        self.tmp.cleanup()

    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=10) as r:
            return json.loads(r.read())

    def post(self, path, body=None):
        req = urllib.request.Request(self.base + path, data=json.dumps(body or {}).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_history_list_and_forget(self):
        from instagram_indirici.history import History
        h = History()
        h.mark("u1", "a", "all", "post", "natgeo")
        h.mark("u1", "b", "all", "post", "natgeo")
        h.close()
        data = self.get("/api/history-list")
        self.assertEqual(data["total"], 2)
        self.assertEqual(data["profiles"][0]["username"], "natgeo")
        self.assertFalse(data["profiles"][0]["exists"])
        code, res = self.post("/api/history-forget", {"user_id": "u1"})
        self.assertEqual(code, 200)
        self.assertEqual(self.get("/api/history-list")["total"], 0)

    def test_clear_cache_keeps_login_and_clear_cookies_removes_it(self):
        from instagram_indirici.paths import app_data_dir
        prof = app_data_dir() / "tarayici-msedge"
        (prof / "Default" / "Cache").mkdir(parents=True)
        (prof / "Default" / "Cache" / "f").write_text("x")
        (prof / "Default" / "Cookies").write_text("c")
        (app_data_dir() / "arayuz-penceresi-edge").mkdir()
        code, res = self.post("/api/clear-cache")
        self.assertEqual(code, 200)
        self.assertFalse((prof / "Default" / "Cache").exists())
        self.assertTrue((prof / "Default" / "Cookies").exists())        # oturum duruyor
        code, res = self.post("/api/clear-cookies")
        self.assertEqual(code, 200)
        self.assertFalse(prof.exists())
        self.assertTrue((app_data_dir() / "arayuz-penceresi-edge").exists())   # arayüz penceresine dokunulmaz
        self.assertFalse(self.get("/api/state")["logged_in"])

    def test_maintenance_is_refused_while_running(self):
        self.state.engine.running = True
        for path in ("/api/clear-cache", "/api/clear-cookies", "/api/history-forget"):
            self.assertEqual(self.post(path, {"user_id": "x"})[0], 400, path)

    def test_report_is_saved_as_file(self):
        from instagram_indirici.paths import app_data_dir
        code, res = self.post("/api/report-share")
        self.assertEqual(code, 200)
        self.assertEqual(res["message"]["key"], "server.report_saved")
        path = Path(res["message"]["params"]["path"])
        self.assertTrue(path.is_file() and path.parent == app_data_dir())
        self.assertIn("Instagram İndirici raporu", path.read_text(encoding="utf-8"))

    def test_settings_roundtrip_new_fields(self):
        code, res = self.post("/api/settings", {"identity": "android", "speed": "very_careful", "setup_done": True})
        self.assertEqual(res["settings"]["identity"], "android")
        self.assertEqual(res["settings"]["speed"], "very_careful")
        self.assertTrue(res["settings"]["setup_done"])
        self.assertEqual(self.get("/api/state")["settings"]["speed"], "very_careful")
