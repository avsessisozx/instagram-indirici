"""Python ve Kotlin (Android) sürümlerinin birlikte kullandığı test vakalarını üretir.

Çıktı: tests/vectors/*.json. Python testleri bu dosyaların Python koduyla birebir uyduğunu,
Android testleri de Kotlin koduyla uyduğunu doğrular. Böylece iki sürüm aynı linki aynı
şekilde tanır, aynı dosyayı seçer ve dosyaları aynı adla aynı klasöre kaydeder.

Kullanım: python tools/make_vectors.py          (dosyaları yazar)
          python tools/make_vectors.py --check  (güncel mi diye bakar)
"""

import json
import os
import sys
import time
from pathlib import Path

os.environ["TZ"] = "UTC"
if hasattr(time, "tzset"):
    time.tzset()

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from instagram_indirici import links, media, naming  # noqa: E402
from instagram_indirici.browser import ApiResult, classify, parse_session_input  # noqa: E402
from tests import fake_instagram as fi  # noqa: E402

OUT = ROOT / "tests" / "vectors"

LINK_INPUTS = [
    "nasa", "@NASA", "  @nat.geo_tr  ", "x.y", "instagram.com", "nasa.com",
    "https://www.instagram.com/nasa/", "instagram.com/nasa?igsh=MWQ1ZGUxMzBkMA==",
    "https://instagram.com/nasa/reels/", "https://m.instagram.com/nasa/tagged/", "https://www.instagram.com/Nasa",
    "https://www.instagram.com/p/C8abcDEF123/", "https://www.instagram.com/reel/DA_b-12xYz/?igsh=x",
    "https://www.instagram.com/reels/DA_b-12xYz/", "https://www.instagram.com/tv/B1abcdEFG/",
    "https://www.instagram.com/nasa/p/C8abcDEF123/", "Şuna bak: https://www.instagram.com/reel/DAbc_12-x/ çok iyi",
    "https://www.instagram.com/stories/nasa/", "https://www.instagram.com/stories/nasa/3456789012345678901/?utm=1",
    "https://www.instagram.com/stories/highlights/17912345678901234/",
    "https://www.instagram.com/s/aGlnaGxpZ2h0OjE3OTEyMzQ1Njc4OTAxMjM0?story_media_id=1",
    "https://www.instagram.com/share/BAabc123xyz/", "https://www.instagram.com/share/p/BAabc123/",
    "", "   ", "https://www.tiktok.com/@kisi/video/1", "https://www.threads.net/@kisi", "https://www.instagram.com/",
    "https://www.instagram.com/explore/tags/yaz/", "https://www.instagram.com/direct/inbox/", "https://www.instagram.com/p/",
    "https://www.instagram.com/reels/", "https://www.instagram.com/accounts/login/", "bu bir cümle, link yok",
    "instagr.am/p/C8abcDEF123", "https://www.instagram.com/nasa/?hl=tr", "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "https://www.instagram.com/stories/me/", "https://www.instagram.com/s/bm90aGluZw==",
]

NAMING = [
    "Gönderi 1 #tatil #yaz", "#sadece #etiket", "", None, "a<b>c:\"d\"/e\\f|g?h*", "CON", "   ...  ",
    "Çok uzun bir açıklama " * 5, "satır\nsonu\tve\rboşluk", "emoji 😀 ve \u200b görünmez", 12345,
]


def link_vectors():
    out = []
    for text in LINK_INPUTS:
        p = links.parse_link(text)
        out.append({
            "input": text, "ok": p.ok, "kind": p.kind, "username": p.username, "shortcode": p.shortcode,
            "highlight_id": p.highlight_id, "story_media_id": p.story_media_id, "url": p.url,
            "error": p.error.key if p.error else None, "describe": p.describe().key,
        })
    codes = []
    for pk in (1, 63, 64, 3394145943868104119, 3400000000001001005, "3400000000001001005_1001"):
        code = links.shortcode_from_id(pk)
        codes.append({"id": str(pk), "code": code, "back": links.id_from_shortcode(code)})
    codes.append({"id": "", "code": "", "back": links.id_from_shortcode(fi.shortcode(3394145943868104119) + "x" * 28)})
    return {"parse": out, "codes": codes}


def naming_vectors():
    out = []
    for text in NAMING:
        out.append({"input": text, "sanitize": naming.sanitize(text), "sanitize20": naming.sanitize(text, 20, ""),
                    "title": naming.title_from_desc(text)})
    dates = [{"ts": ts, "date": naming.date_from_timestamp(ts)} for ts in (0, -5, 1760000000, "1760000000", "x", None)]
    return {"cases": out, "dates": dates}


CAPTION_MIN = 20   # vektörlerde kısa tutulur ki uzun açıklama yolu da denensin


def post_dict(post):
    return {
        "key": post.key, "code": post.code, "owner_id": post.owner_id, "username": post.username,
        "taken_at": post.taken_at, "pinned": post.pinned, "stem": post.stem,
        "skipped": post.skipped_reason.key if post.skipped_reason else None,
        "section": post.section, "caption_stem": post.caption_stem, "likes": post.likes,
        "comments": post.comments, "permalink": post.permalink,
        "files": [{"kind": f.kind, "rel_stem": f.rel_stem, "legacy_stems": f.legacy_stems, "ext": f.ext,
                   "urls": f.urls} for f in post.files],
    }


def post_vectors():
    data = fi.Data("https://cdn.test")
    posts = data.posts[fi.PROFILE_OK]
    cases = []
    items = [("pinned", posts[0])] + [(f"i{i}", posts[i + 1]) for i in range(10)]
    for name, item in items:
        cases.append({"name": name, "kind": "post", "subdir": "", "item": item})
        cases.append({"name": name + "_graphql", "kind": "post", "subdir": "", "item": data.graphql_node(item)})
    for n, story in enumerate(data.stories(fi.PROFILE_OK)):
        cases.append({"name": f"story{n}", "kind": "story", "subdir": media.STORIES_DIR, "item": story})
    for item in data.highlight_items("17900000000000002"):
        cases.append({"name": "hl" + item["pk"][-2:], "kind": "highlight",
                      "subdir": media.highlight_folder("İş/Proje", "17900000000000002"), "item": item})
    for case in cases:
        case["results"] = []
        for content in ("all", "video", "image"):
            post = media.build_post(case["item"], content=content, kind=case["kind"], subdir=case["subdir"],
                                    username=fi.PROFILE_OK, caption_min=CAPTION_MIN)
            case["results"].append({"content": content, **post_dict(post)})
    return cases


def find_vectors():
    data = fi.Data("https://cdn.test")
    posts = data.posts[fi.PROFILE_OK]
    blobs = [
        {"data": {"xdt_api__v1__feed__user_timeline_graphql_connection": {
            "edges": [{"node": p} for p in posts[:4]], "page_info": {"has_next_page": True, "end_cursor": "c1"}}}},
        {"data": {"user": {"edge_owner_to_timeline_media": {
            "count": 41, "page_info": {"has_next_page": False, "end_cursor": None},
            "edges": [{"node": data.graphql_node(p)} for p in posts[2:5]]}}}},
        {"items": [posts[3]], "more_available": False},
        [{"user": {"username": "sahte.kisi", "pk": "1001"}},
         {"x": {"username": "SAHTE.KISI", "pk": "1001", "full_name": "Sahte Kişi", "is_private": False}}],
        {"tray": [{"id": "highlight:17900000000000001", "title": "Tatil", "media_count": 2},
                  {"id": 17900000000000002, "title": "Sayı"}]},
        {"data": {"user": {"edge_highlight_reels": {"edges": [{"node": {"id": "17900000000000003", "title": "Eski"}}]}}}},
        {"data": {"highlights": {"edges": [{"node": {"id": "highlight:17900000000000004", "title": "Kedi"}}]}}},
        {"nothing": [1, 2, {"deep": None}]},
    ]
    out = []
    for blob in blobs:
        user = media.find_user(blob, "sahte.kisi")
        out.append({
            "blob": blob,
            "media_keys": [media.media_key(m) for m in media.find_media(blob)],
            "page_info": media.find_page_info(blob),
            "user_full_name": (user or {}).get("full_name"),
            "user_id": media.user_id_of(user) if user else "",
            "highlights": media.find_highlights(blob),
        })
    avatar_users = [
        {"username": "x", "profile_pic_id": "123_456", "hd_profile_pic_url_info": {"url": "https://c/a_hd.jpg"},
         "profile_pic_url_hd": "https://c/a_320.jpg", "profile_pic_url": "https://c/a_150.jpg"},
        {"username": "y", "profile_pic_url": "https://c/p/9988_s150.jpg?stp=x",
         "hd_profile_pic_versions": [{"url": "https://c/v320.jpg", "width": 320, "height": 320},
                                     {"url": "https://c/v640.jpg", "width": 640, "height": 640}]},
        {"username": "z"},
    ]
    avatars = []
    for u in avatar_users:
        urls = media.avatar_urls(u)
        for content in ("all", "video"):
            avatars.append({"user": u, "content": content, "urls": urls,
                            **post_dict(media.build_avatar(u, urls, content=content))})
    return {"blobs": out, "avatars": avatars}


def classify_vectors():
    def res(status, data, url="https://www.instagram.com/api/v1/x/", text=None):
        return {"status": status, "url": url, "data": data, "text": text if text is not None else json.dumps(data)}

    cases = [
        res(200, {"items": [], "status": "ok"}),
        res(401, {"message": "Please wait a few minutes before you try again.", "require_login": True, "status": "fail"}),
        res(429, None, text=""),
        res(401, {"message": "login_required", "status": "fail"}),
        res(400, {"message": "challenge_required", "challenge": {"url": "/c/"}}),
        res(400, {"message": "checkpoint_required", "checkpoint_url": "/c/"}),
        res(200, None, url="https://www.instagram.com/accounts/login/?next=x", text="<html>"),
        res(200, None, url="https://www.instagram.com/challenge/x/", text="<html>"),
        res(404, {"status": "fail"}),
        res(500, None, text="<html>"),
        res(200, {"message": "feedback_required", "spam": True, "status": "fail"}),
        res(403, {"message": "", "status": "fail"}),
        res(200, {"status": "fail", "message": "x"}),
        res(200, [1, 2]),
        res(0, None, text=""),
    ]
    for c in cases:
        c["kind"] = classify(ApiResult(c["status"], c["url"], False, c["text"], c["data"]))
    return cases


SID = "61234567890%3AAbCdEfGhIjKl%3A15%3AAYexampleVALUE_-"
SESSION_INPUTS = [
    SID, "  " + SID + "\n", SID.replace("%3A", ":"), "sessionid=" + SID, 'sessionid="' + SID + '"', "SessionID=" + SID,
    "Cookie: mid=abc; csrftoken=TOK123; sessionid=" + SID + "; ds_user_id=777777; ig_did=x",
    "cookie:csrftoken=TOK123;sessionid=" + SID, "mid=abc\nsessionid=" + SID + "\ncsrftoken=T0KEN9",
    "sessionid\t" + SID + "\t.instagram.com\t/\t2027-10-22T10:00:00.000Z\t120\t✓\t✓",
    "gecerli", "yanlis-kod-123", "", "   ", "abc", "merhaba dünya", "https://www.instagram.com/", "csrftoken=abcdef123",
    "a b c d e f g", "sessionid=kısa", "sessionid=" + SID + "; csrftoken=bad value", "'" + SID + "'",
    "sessionid=12345%3Aabcdef; ds_user_id=99999999", "sessionid=abcdef123456",
]


def session_vectors():
    return [{"input": text, "parsed": parse_session_input(text)} for text in SESSION_INPUTS]


def build():
    return {
        "session.json": session_vectors(),
        "links.json": link_vectors(),
        "naming.json": naming_vectors(),
        "posts.json": post_vectors(),
        "find.json": find_vectors(),
        "classify.json": classify_vectors(),
    }


def dump(data) -> str:
    return json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True) + "\n"


def main() -> int:
    check = "--check" in sys.argv
    OUT.mkdir(parents=True, exist_ok=True)
    stale = []
    for name, data in build().items():
        path = OUT / name
        text = dump(data)
        if check:
            if not path.exists() or path.read_text(encoding="utf-8") != text:
                stale.append(name)
        else:
            path.write_text(text, encoding="utf-8")
    if stale:
        print("Güncel değil (python tools/make_vectors.py çalıştır): " + ", ".join(stale))
        return 1
    print("Test vakaları " + ("güncel." if check else f"yazıldı: {OUT}"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
