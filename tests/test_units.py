"""Tarayıcı gerektirmeyen hızlı testler."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

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
