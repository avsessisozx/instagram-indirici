"""Testler için sahte Instagram sunucusu.

Gerçek Instagram'ın web sitesinin kullandığı adresleri ve yanıt biçimlerini taklit eder
(Instaloader ve gallery-dl'in 2026-10 sürümlerinin beklediği alanlarla):
  * /{kullanıcı}/                    profil sayfası, içine gömülü JSON ile
  * /api/v1/users/web_profile_info/   eski GraphQL biçiminde profil + ilk 12 gönderi
  * /api/v1/feed/user/{id}/           v1 biçiminde sayfalı gönderi listesi (giriş gerekir)
  * /graphql/query                    doc_id sorguları (giriş yapılmış / yapılmamış)
  * /api/v1/feed/reels_media/         hikayeler ve öne çıkanlar (giriş gerekir)
  * /api/v1/highlights/{id}/highlights_tray/
  * /api/v1/users/{id}/info/          HD profil fotoğrafı
  * /api/v1/media/{id}/info/          tek gönderi
  * /cdn/...                          resim ve video dosyaları

Çalıştırmak için: python -m tests.fake_instagram --port 8765
"""

from __future__ import annotations

import argparse
import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlsplit

APP_ID = "936619743392459"
_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"

PROFILE_OK = "sahte.kisi"
PROFILE_PRIVATE = "gizli.hesap"
PROFILE_EMPTY = "bos.hesap"
PROFILE_FORCE_LOGIN = "giris.isteyen"
USER_IDS = {PROFILE_OK: "1001", PROFILE_PRIVATE: "1002", PROFILE_EMPTY: "1003", PROFILE_FORCE_LOGIN: "1004"}
TOTAL_POSTS = 40
PAGE = 12
LOGGED_OUT_PAGES = 2          # giriş yapmadan en fazla bu kadar sayfa görünür (24 gönderi)
BASE_TS = 1760000000          # 2025-10-09 civarı

JPEG = b"\xff\xd8\xff\xe0" + b"\x00\x10JFIF\x00" + b"x" * 2000 + b"\xff\xd9"
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 3000
WEBP = b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"w" * 1500


def shortcode(pk: int) -> str:
    out = ""
    while pk:
        pk, rem = divmod(pk, 64)
        out = _ALPHABET[rem] + out
    return out


def id_from_shortcode(code: str) -> int:
    v = 0
    for ch in code:
        v = v * 64 + _ALPHABET.index(ch)
    return v


# ------------------------------------------------------------ veri üretimi
class Data:
    def __init__(self, base: str, fake: "FakeInstagram" = None) -> None:
        self.base = base
        self.fake = fake
        self.posts: Dict[str, List[Dict[str, Any]]] = {PROFILE_OK: [], PROFILE_EMPTY: [], PROFILE_PRIVATE: [],
                                                        PROFILE_FORCE_LOGIN: []}
        for i in range(TOTAL_POSTS):
            self.posts[PROFILE_OK].append(self._post(PROFILE_OK, i))
        # En üstte sabitlenmiş eski bir gönderi (Instagram'daki "sabitlenmiş" gönderiler gibi)
        pinned = self._post(PROFILE_OK, 100)
        pinned["taken_at"] = BASE_TS - 400 * 86400
        pinned["timeline_pinned_user_ids"] = [int(USER_IDS[PROFILE_OK])]
        self.posts[PROFILE_OK].insert(0, pinned)
        for i in range(5):
            self.posts[PROFILE_FORCE_LOGIN].append(self._post(PROFILE_FORCE_LOGIN, i))
        for i in range(3):
            self.posts[PROFILE_PRIVATE].append(self._post(PROFILE_PRIVATE, i))

    def user(self, username: str) -> Dict[str, Any]:
        return {"pk": USER_IDS[username], "pk_id": USER_IDS[username], "id": USER_IDS[username],
                "username": username, "full_name": "Sahte Kişi" if username == PROFILE_OK else username.title(),
                "profile_pic_url": f"{self.base}/cdn/avatar_{username}_s150.jpg?stp=dst-jpg_s150x150",
                "is_private": username == PROFILE_PRIVATE, "is_verified": False}

    def cdn(self, name: str) -> str:
        return f"{self.base}/cdn/{name}?oh=00_AbC&oe=6A0B1C2D&_nc_ht=scontent"

    def _image(self, pk: int, w: int = 1080, h: int = 1350) -> Dict[str, Any]:
        return {"candidates": [
            {"width": w, "height": h, "url": self.cdn(f"{pk}_n.jpg")},
            {"width": 640, "height": 800, "url": self.cdn(f"{pk}_640.jpg")},
            {"width": 150, "height": 150, "url": self.cdn(f"{pk}_150.jpg")},
        ]}

    def _videos(self, pk: int) -> List[Dict[str, Any]]:
        # Gerçekte de sıra her zaman en iyiden kötüye değil; programın büyüğü seçmesi gerekir.
        return [
            {"type": 101, "width": 720, "height": 1280, "url": self.cdn(f"{pk}_720.mp4")},
            {"type": 103, "width": 1080, "height": 1920, "url": self.cdn(f"{pk}_1080.mp4")},
            {"type": 102, "width": 480, "height": 854, "url": self.cdn(f"{pk}_480.mp4")},
        ]

    def manifest(self, pk: int, top: int = 1920, audio: bool = True) -> str:
        """Gerçek Instagram'ınkine benzer DASH bildirimi: tek dosyalı görüntü ve ses akışları."""
        widths = [(360, 640, 309000), (720, 1280, 1360000), (round(top * 9 / 16), top, 4200000)]
        reps = "".join(
            f'<Representation id="v{n}" mimeType="video/mp4" codecs="avc1.640028" width="{w}" height="{h}" '
            f'bandwidth="{bw}"><BaseURL>{self.cdn(f"{pk}_dashv{h}.mp4").replace("&", "&amp;")}</BaseURL>'
            f'<SegmentBase indexRange="800-1200"><Initialization range="0-799"/></SegmentBase></Representation>'
            for n, (w, h, bw) in enumerate(widths))
        sound = ""
        if audio:
            sound = ('<AdaptationSet contentType="audio" segmentAlignment="true">'
                     '<Representation id="a0" mimeType="audio/mp4" codecs="mp4a.40.2" bandwidth="72000">'
                     f'<BaseURL>{self.cdn(f"{pk}_dasha.mp4").replace("&", "&amp;")}</BaseURL></Representation>'
                     '<Representation id="a1" mimeType="audio/mp4" codecs="mp4a.40.2" bandwidth="48000">'
                     f'<BaseURL>{self.cdn(f"{pk}_dasha_low.mp4").replace("&", "&amp;")}</BaseURL></Representation>'
                     '</AdaptationSet>')
        return ('<?xml version="1.0"?><MPD xmlns="urn:mpeg:dash:schema:mpd:2011" type="static"><Period>'
                f'<AdaptationSet contentType="video" segmentAlignment="true">{reps}</AdaptationSet>{sound}'
                '</Period></MPD>')

    def enable_dash(self, top: int = 1920, audio: bool = True) -> None:
        """Bütün videolu gönderilere (ve albüm parçalarına) video_dash_manifest ekler."""
        for items in self.posts.values():
            for item in items:
                for node in [item] + list(item.get("carousel_media") or []):
                    if node.get("video_versions"):
                        node["video_dash_manifest"] = self.manifest(int(node["pk"]), top, audio)

    def disable_dash(self) -> None:
        for items in self.posts.values():
            for item in items:
                for node in [item] + list(item.get("carousel_media") or []):
                    node.pop("video_dash_manifest", None)

    def _post(self, username: str, i: int) -> Dict[str, Any]:
        pk = 3400000000000000000 + USER_IDS_INT[username] * 1000 + i
        item: Dict[str, Any] = {
            "pk": str(pk), "id": f"{pk}_{USER_IDS[username]}", "code": shortcode(pk),
            "taken_at": BASE_TS - i * 86400, "media_type": 1,
            "caption": {"text": f"Gönderi {i} #tatil #yaz"} if i % 4 else None,
            "user": {"pk": USER_IDS[username], "username": username, "full_name": "Sahte Kişi"},
            "image_versions2": self._image(pk), "original_width": 1080, "original_height": 1350,
            "like_count": 10 + i, "product_type": "feed",
        }
        if i % 5 == 1:          # video (reels)
            item["media_type"] = 2
            item["product_type"] = "clips"
            item["video_versions"] = self._videos(pk)
        elif i % 5 == 2:        # albüm: resim + video + resim
            item["media_type"] = 8
            item["product_type"] = "carousel_container"
            kids = []
            for n in range(3):
                cpk = pk * 10 + n
                child = {"pk": str(cpk), "id": f"{cpk}_{USER_IDS[username]}", "media_type": 1,
                         "image_versions2": self._image(cpk)}
                if n == 1:
                    child["media_type"] = 2
                    child["video_versions"] = self._videos(cpk)
                kids.append(child)
            item["carousel_media"] = kids
            item["carousel_media_count"] = 3
        elif i == 8:            # videosu olmayan (kısıtlanmış) video
            item["media_type"] = 2
            item["product_type"] = "clips"
        return item

    # Eski GraphQL biçimi (giriş yapmadan profil bilgisinde gelen)
    def graphql_node(self, item: Dict[str, Any]) -> Dict[str, Any]:
        node: Dict[str, Any] = {
            "__typename": "GraphImage", "id": item["pk"], "shortcode": item["code"],
            "taken_at_timestamp": item["taken_at"], "dimensions": {"width": 1080, "height": 1350},
            "display_url": item["image_versions2"]["candidates"][0]["url"],
            "display_resources": [
                {"src": item["image_versions2"]["candidates"][2]["url"], "config_width": 150, "config_height": 150},
                {"src": item["image_versions2"]["candidates"][0]["url"], "config_width": 1080, "config_height": 1350},
            ],
            "is_video": item["media_type"] == 2,
            "edge_media_to_caption": {"edges": [{"node": {"text": (item.get("caption") or {}).get("text", "")}}] if item.get("caption") else []},
            "owner": {"id": item["user"]["pk"], "username": item["user"]["username"]},
            "edge_media_preview_like": {"count": item["like_count"]},
        }
        if item.get("product_type"):
            node["product_type"] = item["product_type"]
        if item.get("video_versions"):
            node["__typename"] = "GraphVideo"
            if self.fake is None or self.fake.graphql_video_urls:
                node["video_url"] = item["video_versions"][1]["url"]
        if item.get("timeline_pinned_user_ids"):
            node["pinned_for_users"] = [{"id": item["user"]["pk"], "username": item["user"]["username"]}]
        if item.get("carousel_media"):
            node["__typename"] = "GraphSidecar"
            node["edge_sidecar_to_children"] = {"edges": [{"node": {
                "__typename": "GraphVideo" if c.get("video_versions") else "GraphImage",
                "id": c["pk"], "shortcode": shortcode(int(c["pk"])), "dimensions": {"width": 1080, "height": 1350},
                "display_url": c["image_versions2"]["candidates"][0]["url"],
                "is_video": bool(c.get("video_versions")),
                **({"video_url": c["video_versions"][1]["url"]} if c.get("video_versions") else {}),
            }} for c in item["carousel_media"]]}
        return node

    def stories(self, username: str) -> List[Dict[str, Any]]:
        out = []
        for n in range(3):
            pk = 3500000000000000000 + n
            item = {"pk": str(pk), "id": f"{pk}_{USER_IDS[username]}", "taken_at": BASE_TS + 3600 * n,
                    "media_type": 1, "image_versions2": self._image(pk, 1080, 1920),
                    "expiring_at": BASE_TS + 86400 + 3600 * n,
                    "user": {"pk": USER_IDS[username], "username": username}}
            if n == 2:
                item["media_type"] = 2
                item["video_versions"] = self._videos(pk)
            out.append(item)
        return out

    HIGHLIGHTS = {"17900000000000001": "Tatil", "17900000000000002": "İş/Proje", "17900000000000003": "Yemek",
                  "17900000000000004": "Kedi", "17900000000000005": "Konser", "17900000000000006": "Yol",
                  "17900000000000007": "Eski"}

    def highlight_items(self, hid: str) -> List[Dict[str, Any]]:
        out = []
        count = 2 if hid.endswith("1") else 3 if hid.endswith("2") else 1 + int(hid[-1])
        for n in range(count):
            pk = 3600000000000000000 + int(hid[-1]) * 100 + n
            out.append({"pk": str(pk), "id": f"{pk}_1001", "taken_at": BASE_TS - 50 * 86400 + n,
                        "media_type": 1, "image_versions2": self._image(pk, 1080, 1920),
                        "user": {"pk": "1001", "username": PROFILE_OK}})
        return out

    def extra_reels(self, username: str) -> List[Dict[str, Any]]:
        """Yalnız Reels sekmesinde görünen (profil ızgarasında olmayan) klipler."""
        out = []
        for n in range(5):
            item = self._post(username, 150 + n)
            item["media_type"] = 2
            item["product_type"] = "clips"
            item["video_versions"] = self._videos(int(item["pk"]))
            item.pop("carousel_media", None)
            item.pop("carousel_media_count", None)
            item["caption"] = {"text": f"Reels {n} " + "uzun açıklama " * 30} if n == 0 else {"text": f"Reels {n}"}
            out.append(item)
        return out

    def reels(self, username: str) -> List[Dict[str, Any]]:
        """Reels sekmesi: ızgaradaki klipler + yalnız burada olanlar."""
        grid = [p for p in self.posts.get(username, []) if p.get("product_type") == "clips"]
        return grid + (self.extra_reels(username) if username == PROFILE_OK else [])

    def tagged(self, username: str) -> List[Dict[str, Any]]:
        """Etiketlenenler: başkasının gönderileri."""
        if username != PROFILE_OK:
            return []
        return [self._post(PROFILE_EMPTY, 202 + n - 2) for n in range(3)]

    def reposts(self, username: str) -> List[Dict[str, Any]]:
        if username != PROFILE_OK:
            return []
        return [self._post(PROFILE_EMPTY, 300 + n) for n in range(2)]

    def find_post(self, code: str) -> Optional[Dict[str, Any]]:
        for items in self.posts.values():
            for item in items:
                if item["code"] == code:
                    return item
        return None


USER_IDS_INT = {k: int(v) for k, v in USER_IDS.items()}


# ------------------------------------------------------------ sunucu
class FakeInstagram:
    def __init__(self, port: int = 0, public: str = "") -> None:
        self.port = port
        self.public = public
        self.server: Optional[ThreadingHTTPServer] = None
        self.lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        self.user_agents = set()       # API isteklerinde görülen tarayıcı kimlikleri
        self.autologin = False
        self.rate_limit_once = False
        self.challenge_once = False
        self.break_rest = False        # /api/v1/feed/user bozuk (HTML döner)
        self.break_graphql = False     # /graphql/query POST bozuk
        self.break_profile_api = False # web_profile_info bozuk
        self.cdn_403 = set()           # bu adlar 403 döner
        self.dash = False              # /__ayar?dash=1: videolu gönderilere video_dash_manifest eklenir
        self.dash_video = b""          # DASH görüntü/ses akışı olarak sunulacak gerçek mp4 baytları
        self.dash_audio = b""
        self.embed_posts = True        # profil sayfasına ilk gönderiler gömülsün mü
        self.graphql_video_urls = True # eski biçimde video adresi gelsin mi (gerçekte bazen gelmiyor)
        self.slow_cdn = 0.0            # her dosyada bekleme (durdurma testi için)
        self.page_429_once = False     # profil sayfası bir kez boş 429 döner (yoğunluk)
        self.profile_api_refused = False  # web_profile_info ve arama hep "biraz bekle" der (gerçekte bazı oturumlarda böyle)
        self.feed_rate_always = False  # REST gönderi listesi hep 429
        self.session_kill_after = 0    # bu kadar liste sayfasından sonra oturumu kapatır (Instagram'ın yaptığı gibi)
        self.rate_after = 0            # bu kadar liste sayfasından sonra her liste isteği 429 …
        self.rate_window = 0.0         # … bu kadar saniye boyunca
        self.feed_pages = 0
        self.rate_started = 0.0
        self.highlights_batch_truncate = False  # çoklu istekte sadece ilk öne çıkanın öğeleri gelir
        self.highlights_tray_refused = False    # öne çıkan listesi isteği hep "biraz bekle"
        self.rest_refused = False      # REST gönderi listesi hep HTTP 400 "biraz bekle" (gerçek telefonda görüldü)
        self.stories_500 = False       # hikaye listesi isteği hep HTTP 500 (gerçek telefonda görüldü)
        self.highlights_500 = False    # öne çıkan içerik isteği hep HTTP 500
        self.highlights_hang = 0.0     # öne çıkan içerik isteğine bu kadar saniye geç yanıt verir (zaman aşımı)
        self.reels_media_fail_n = 0    # reels_media ilk bu kadar istekte HTTP 500 döner, sonra düzelir
        self.clips_refused = False     # Reels liste isteği reddedilir (sayfa kaydırma yedeği denenir)
        self.usertags_refused = False  # Etiketlenenler liste isteği reddedilir
        self.requests: List[str] = []
        self.api_calls: List[Tuple[str, bool]] = []   # (yol, giriş var mı)

    @property
    def base(self) -> str:
        return self.public or f"http://127.0.0.1:{self.port}"

    def start(self) -> "FakeInstagram":
        handler = make_handler(self)
        self.server = ThreadingHTTPServer(("0.0.0.0" if self.public else "127.0.0.1", self.port), handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.data = Data(self.base, self)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def stop(self) -> None:
        if self.server:
            self.server.shutdown()
            self.server.server_close()


def _relay(data: Dict[str, Any]) -> str:
    """Instagram'ın sayfaya gömdüğü JSON'un yapısını taklit eder."""
    blob = {"require": [["ScheduledServerJS", "handle", None, [{"__bbox": {"require": [[
        "RelayPrefetchedStreamCache", "next", [],
        ["adp_PolarisProfilePageContentQueryRelayPreloader_1", {"__bbox": {"complete": True, "result": {"data": data}}}],
    ]]}}]]]}
    return '<script type="application/json" data-content-len="1" data-sjs>' + json.dumps(blob, ensure_ascii=False).replace("</", "<\\/") + "</script>"


def make_handler(fake: FakeInstagram):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args) -> None:
            pass

        # ---- yardımcılar
        def _cookies(self) -> Dict[str, str]:
            out = {}
            for part in (self.headers.get("Cookie") or "").split(";"):
                if "=" in part:
                    k, v = part.strip().split("=", 1)
                    out[k] = v
            return out

        def logged_in(self) -> bool:
            return self._cookies().get("sessionid") == "gecerli"

        def send(self, code: int, body: bytes, ctype: str, headers: Optional[Dict[str, str]] = None) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            if "csrftoken" not in self._cookies():
                self.send_header("Set-Cookie", "csrftoken=sahteCSRF123; Path=/")
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def json(self, data: Any, code: int = 200, headers=None) -> None:
            self.send(code, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8", headers)

        def html(self, text: str, code: int = 200, headers=None) -> None:
            self.send(code, text.encode("utf-8"), "text/html; charset=utf-8", headers)

        def api_guard(self) -> bool:
            """Gerçek Instagram gibi: uygulama kimliği yoksa API reddedilir."""
            fake.user_agents.add(self.headers.get("User-Agent") or "")
            if self.headers.get("X-IG-App-ID") != APP_ID:
                self.json({"message": "useragent mismatch", "status": "fail"}, 400)
                return False
            if fake.rate_limit_once and self.path.startswith("/api/v1/feed/user/"):
                fake.rate_limit_once = False
                self.json({"message": "Please wait a few minutes before you try again.", "require_login": True,
                           "status": "fail"}, 401)
                return False
            return True

        def login_required(self) -> None:
            self.json({"message": "login_required", "require_login": True, "status": "fail"}, 401)

        # ---- GET
        def do_GET(self) -> None:
            parts = urlsplit(self.path)
            path, query = parts.path, parse_qs(parts.query)
            fake.requests.append(self.path)
            if path.startswith("/cdn/"):
                return self.cdn(path[5:])
            if path.startswith("/api/v1/") or path.startswith("/web/search"):
                fake.api_calls.append((path, self.logged_in()))
                if not self.api_guard():
                    return
                return self.api_get(path, query)
            if path.startswith("/graphql/query"):
                return self.scroll_page(query)
            return self.page(path, query)

        def do_POST(self) -> None:
            parts = urlsplit(self.path)
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length).decode("utf-8") if length else ""
            fake.requests.append("POST " + self.path)
            if parts.path.startswith("/graphql/query"):
                fake.api_calls.append((parts.path, self.logged_in()))
                if fake.break_graphql:
                    return self.html("<html>hata</html>", 500)
                if not self.api_guard():
                    return
                if self.headers.get("X-CSRFToken") != "sahteCSRF123":
                    return self.json({"message": "CSRF token missing or incorrect", "status": "fail"}, 403)
                return self.graphql(parse_qs(body))
            if parts.path == "/api/v1/clips/user/":
                fake.api_calls.append((parts.path, self.logged_in()))
                if not self.logged_in():
                    return self.login_required()
                if fake.clips_refused:
                    return self.json({"message": "not found", "status": "fail"}, 404)
                form = parse_qs(body)
                name = next((n for n, i in USER_IDS.items() if i == (form.get("target_user_id") or [""])[0]), "")
                items = fake.data.reels(name)
                size = int((form.get("page_size") or ["12"])[0] or 12)
                start = int((form.get("max_id") or ["0"])[0] or 0)
                chunk = items[start:start + size]
                more = start + size < len(items)
                return self.json({"items": [{"media": it} for it in chunk],
                                  "paging_info": {"max_id": str(start + size) if more else "", "more_available": more},
                                  "status": "ok"})
            self.json({"status": "fail"}, 404)

        # ---- sayfalar
        def control(self, path: str, query) -> bool:
            """Testlerin (Android) sahte sunucuyu ayarlaması: /__ayar?ad=değer, /__sifirla, /__durum."""
            if path == "/__sifirla":
                fake.reset()
                fake.data.disable_dash()
                self.json({"ok": True})
                return True
            if path == "/__durum":
                self.json({"feed_pages": fake.feed_pages,
                           "api_calls": [[p, li] for p, li in fake.api_calls],
                           "requests": len(fake.requests)})
                return True
            if path == "/__ayar":
                for key, values in query.items():
                    if not hasattr(fake, key) or key.startswith("_"):
                        continue
                    raw = values[0]
                    cur = getattr(fake, key)
                    if isinstance(cur, bool):
                        val = raw in ("1", "true", "True")
                    elif isinstance(cur, int):
                        val = int(raw)
                    elif isinstance(cur, float):
                        val = float(raw)
                    elif isinstance(cur, set):
                        val = {x for x in raw.split(",") if x}
                    else:
                        val = raw
                    setattr(fake, key, val)
                    if key == "dash":
                        fake.data.enable_dash() if val else fake.data.disable_dash()
                self.json({"ok": True})
                return True
            return False

        def page(self, path: str, query) -> None:
            d = fake.data
            if self.control(path, query):
                return
            if path == "/accounts/login/":
                if fake.autologin:
                    return self.html("<html><body>Giriş yapılıyor…<script>setTimeout(()=>location.href='/', 800)</script></body></html>",
                                     headers={"Set-Cookie": "sessionid=gecerli; Path=/; Max-Age=31536000"})
                return self.html("<html><head><title>Giriş • Instagram</title></head><body>Giriş formu</body></html>")
            if path.startswith("/challenge/"):
                # Kullanıcı doğrulamayı tamamlamış gibi birkaç saniye sonra ana sayfaya döner.
                return self.html("<html><body>Doğrulama<script>setTimeout(()=>location.href='/', 2500)</script></body></html>")
            if path == "/":
                return self.html("<html><head><title>Instagram</title></head><body>Ana sayfa</body></html>")
            m = re.match(r"^/stories/highlights/(\d+)/?$", path)
            if m:
                hid = m.group(1)
                if not self.logged_in() or hid not in d.HIGHLIGHTS:
                    return self.html("<html><body>Sorry, this page isn't available.</body></html>", 404)
                return self.html("<html><body>" + _relay({"xdt_api__v1__feed__reels_media": {"reels_media": [{
                    "id": f"highlight:{hid}", "title": d.HIGHLIGHTS[hid], "items": d.highlight_items(hid)}]}}) + "</body></html>")
            m = re.match(r"^/stories/([A-Za-z0-9._]+)/?$", path)
            if m:
                name = m.group(1).lower()
                if not self.logged_in() or name != PROFILE_OK:
                    return self.html("<html><body>Sorry, this page isn't available.</body></html>", 404)
                return self.html("<html><body>" + _relay({"xdt_api__v1__feed__reels_media": {"reels_media": [{
                    "id": USER_IDS[name], "items": d.stories(name)}]}}) + "</body></html>")
            m = re.match(r"^/([A-Za-z0-9._]+)/(reels|tagged|reposts)/?$", path)
            if m and m.group(1).lower() in USER_IDS:
                name, tab = m.group(1).lower(), m.group(2)
                if tab != "reels" and not self.logged_in():
                    return self.html("<html><body>Giriş yap</body></html>")
                items = {"reels": d.reels, "tagged": d.tagged, "reposts": d.reposts}[tab](name)
                return self.html("<html><body>" + _relay({"xdt_api__v1__clips__user": {"items": [{"media": i} for i in items]}}) + "</body></html>")
            m = re.match(r"^/(?:p|reel)/([A-Za-z0-9_-]+)/?$", path)
            if m:
                item = d.find_post(m.group(1))
                if not item:
                    return self.html("<html><body>Sorry, this page isn't available.</body></html>", 404)
                return self.html("<html><body>" + _relay({"xdt_api__v1__media__shortcode__web_info": {"items": [item]}}) + "</body></html>")
            m = re.match(r"^/([A-Za-z0-9._]+)/?$", path)
            if m and fake.page_429_once:
                fake.page_429_once = False
                return self.send(429, b"", "text/html; charset=utf-8")
            if m:
                name = m.group(1).lower()
                if name not in USER_IDS:
                    return self.html("<html><head><title>Page not found • Instagram</title></head><body>Sorry, this page isn't available.</body></html>", 404)
                if name == PROFILE_FORCE_LOGIN and not self.logged_in():
                    self.send_response(302)
                    self.send_header("Location", f"/accounts/login/?next=/{name}/")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                user = d.user(name)
                first = d.posts[name][:PAGE] if name != PROFILE_PRIVATE and fake.embed_posts else []
                data = {"user": dict(user, media_count=len(d.posts[name])),
                        "xdt_api__v1__feed__user_timeline_graphql_connection": {
                            "edges": [{"node": p, "cursor": p["pk"]} for p in first],
                            "page_info": {"has_next_page": len(d.posts[name]) > PAGE, "end_cursor": "c1"}}}
                return self.html(
                    "<html><head><title>" + user["full_name"] + "</title></head><body style='margin:0'>"
                    + _relay(data) + SCROLL_SCRIPT + "<div id='grid'></div></body></html>")
            self.html("<html><body>404</body></html>", 404)

        def scroll_page(self, query) -> None:
            """Profil sayfasının kaydırınca kendi yüklediği gönderiler (son çare yolunu denemek için)."""
            d = fake.data
            if query.get("hl"):
                # Profil sayfasının öne çıkanları kendi yüklemesi (sitenin GraphQL yanıtına benzer)
                name = (query.get("u") or [""])[0]
                edges = [{"node": {"id": f"highlight:{h}", "title": t, "cover_media": {}}}
                         for h, t in d.HIGHLIGHTS.items()] if name == PROFILE_OK and self.logged_in() else []
                return self.json({"data": {"highlights": {"edges": edges}}, "status": "ok"})
            blocked = self.list_blocked()
            if blocked:
                return self.blocked_response(blocked)
            try:
                page = int((query.get("scroll") or ["1"])[0])
            except ValueError:
                page = 1
            name = (query.get("u") or [PROFILE_OK])[0]
            items = d.posts.get(name, [])
            if not self.logged_in() and page >= LOGGED_OUT_PAGES:
                return self.json({"data": {"xdt_api__v1__feed__user_timeline_graphql_connection": {
                    "edges": [], "page_info": {"has_next_page": True, "end_cursor": f"c{page}"}}}, "status": "ok"})
            chunk = items[page * PAGE:(page + 1) * PAGE]
            self.json({"data": {"xdt_api__v1__feed__user_timeline_graphql_connection": {
                "edges": [{"node": p} for p in chunk],
                "page_info": {"has_next_page": (page + 1) * PAGE < len(items), "end_cursor": f"c{page + 1}"}}},
                "status": "ok"})

        def cdn(self, name: str) -> None:
            base = name.split("?")[0]
            if fake.slow_cdn:
                time.sleep(fake.slow_cdn)
            if base in fake.cdn_403:
                return self.send(403, b"denied", "text/plain")
            if "_dashv" in base and fake.dash_video:
                return self.send(200, fake.dash_video, "video/mp4")
            if "_dasha" in base and fake.dash_audio:
                return self.send(200, fake.dash_audio, "video/mp4")
            if base.endswith(".mp4"):
                return self.send(200, MP4 + base.encode(), "video/mp4")
            if "avatar" in base:
                return self.send(200, JPEG + base.encode(), "image/jpeg")
            if base.endswith("_640.jpg") or base.endswith("_150.jpg"):
                return self.send(200, JPEG + base.encode(), "image/jpeg")
            # Instagram bazen .jpg adresinden webp verir; uzantı içerik türüne göre seçilmeli.
            if base.startswith("34") and base.endswith("_n.jpg") and base[-7] in "05":
                return self.send(200, WEBP + base.encode(), "image/webp")
            return self.send(200, JPEG + base.encode(), "image/jpeg")

        # ---- API
        def list_blocked(self) -> Optional[str]:
            """Liste isteği (REST, GraphQL, kaydırma) için oturum kapatma / kısıtlama durumu."""
            if fake.session_kill_after and fake.feed_pages >= fake.session_kill_after and self.logged_in():
                return "kill"
            if fake.rate_after and fake.feed_pages >= fake.rate_after:
                if not fake.rate_started:
                    fake.rate_started = time.monotonic()
                if time.monotonic() - fake.rate_started < fake.rate_window:
                    return "rate"
            return None

        def blocked_response(self, kind: str) -> None:
            if kind == "kill":
                return self.json({"message": "login_required", "require_login": True, "status": "fail"}, 401,
                                 headers={"Set-Cookie": "sessionid=; Path=/; Max-Age=0"})
            return self.json({"message": "Please wait a few minutes before you try again.", "require_login": True,
                              "status": "fail"}, 401)

        def api_get(self, path: str, query) -> None:
            d = fake.data
            if path == "/api/v1/accounts/current_user/":
                if not self.logged_in():
                    return self.login_required()
                return self.json({"user": {"pk": "1234567890", "username": "nowoserhi"}, "status": "ok"})
            if fake.profile_api_refused and path in ("/api/v1/users/web_profile_info/", "/web/search/topsearch/"):
                return self.json({"message": "Please wait a few minutes before you try again.",
                                  "require_login": True, "status": "fail"}, 401)
            if fake.rest_refused and path.startswith("/api/v1/feed/user/"):
                return self.json({"message": "Please wait a few minutes before you try again.", "spam": True,
                                  "status": "fail"}, 400)
            if fake.feed_rate_always and path.startswith("/api/v1/feed/user/"):
                return self.json({"message": "rate limited", "status": "fail"}, 429)
            if path == "/api/v1/users/web_profile_info/":
                if fake.break_profile_api:
                    return self.json({"message": "", "status": "fail"}, 400)
                name = (query.get("username") or [""])[0].lower()
                if name not in USER_IDS:
                    return self.json({"data": {"user": None}, "status": "ok"}, 404)
                if name == PROFILE_FORCE_LOGIN and not self.logged_in():
                    return self.login_required()
                user = d.user(name)
                posts = d.posts[name]
                visible = [] if name == PROFILE_PRIVATE else posts[:PAGE]
                return self.json({"data": {"user": {
                    "id": user["pk"], "username": name, "full_name": user["full_name"],
                    "is_private": user["is_private"], "followed_by_viewer": False,
                    "profile_pic_url": user["profile_pic_url"],
                    "profile_pic_url_hd": f"{d.base}/cdn/avatar_{name}_s320.jpg?stp=dst-jpg_s320x320",
                    "edge_owner_to_timeline_media": {
                        "count": len(posts),
                        "page_info": {"has_next_page": len(posts) > PAGE, "end_cursor": "QVFE_1"},
                        "edges": [{"node": d.graphql_node(p)} for p in visible]},
                }}, "status": "ok"})
            if path == "/web/search/topsearch/":
                name = (query.get("query") or [""])[0].lower()
                users = [{"position": 0, "user": d.user(name)}] if name in USER_IDS else []
                return self.json({"users": users, "status": "ok"})
            m = re.match(r"^/api/v1/feed/user/(\d+)/$", path)
            if m:
                if fake.break_rest:
                    return self.html("<html><body>Bir hata oluştu</body></html>", 500)
                blocked = self.list_blocked()
                if blocked:
                    return self.blocked_response(blocked)
                if not self.logged_in():
                    return self.login_required()
                fake.feed_pages += 1
                if fake.challenge_once:
                    fake.challenge_once = False
                    return self.json({"message": "challenge_required", "challenge": {"url": f"{d.base}/challenge/AbC123/", "lock": False},
                                      "status": "fail"}, 400)
                name = next((n for n, i in USER_IDS.items() if i == m.group(1)), "")
                items = d.posts.get(name, [])
                start = int((query.get("max_id") or ["0"])[0] or 0)
                chunk = items[start:start + PAGE]
                more = start + PAGE < len(items)
                return self.json({"items": chunk, "num_results": len(chunk), "more_available": more,
                                  "next_max_id": str(start + PAGE) if more else None,
                                  "user": d.user(name), "status": "ok"})
            if path == "/api/v1/feed/reels_media/":
                if not self.logged_in():
                    return self.login_required()
                ids = query.get("reel_ids") or []
                is_hl = any(r.startswith("highlight:") for r in ids)
                if fake.reels_media_fail_n > 0:
                    fake.reels_media_fail_n -= 1
                    return self.json({"message": "Sorry, there was a problem. Please try again.", "status": "fail"}, 500)
                if fake.highlights_hang and is_hl:
                    time.sleep(fake.highlights_hang)
                if (fake.highlights_500 and is_hl) or (fake.stories_500 and not is_hl):
                    return self.json({"message": "Sorry, there was a problem. Please try again.", "status": "fail"}, 500)
                reels = []
                for rid in ids:
                    if rid.startswith("highlight:"):
                        hid = rid.split(":")[1]
                        if hid in d.HIGHLIGHTS:
                            items = d.highlight_items(hid)
                            reel = {"id": rid, "title": d.HIGHLIGHTS[hid], "media_count": len(items),
                                    "user": {"pk": "1001", "username": PROFILE_OK}, "items": items}
                            if fake.highlights_batch_truncate and len(ids) > 1 and reels:
                                reel["items"] = []
                                reel["media_ids"] = [int(i["pk"]) for i in items]
                            reels.append(reel)
                    else:
                        name = next((n for n, i in USER_IDS.items() if i == rid), "")
                        if name == PROFILE_OK:
                            reels.append({"id": rid, "expiring_at": BASE_TS + 86400, "user": d.user(name),
                                          "items": d.stories(name)})
                return self.json({"reels_media": reels, "reels": {r["id"]: r for r in reels}, "status": "ok"})
            m = re.match(r"^/api/v1/usertags/(\d+)/feed/$", path)
            if m:
                if not self.logged_in():
                    return self.login_required()
                if fake.usertags_refused:
                    return self.json({"message": "not found", "status": "fail"}, 404)
                name = next((n for n, i in USER_IDS.items() if i == m.group(1)), "")
                items = d.tagged(name)
                return self.json({"items": items, "num_results": len(items), "more_available": False, "status": "ok"})
            m = re.match(r"^/api/v1/highlights/(\d+)/highlights_tray/$", path)
            if m:
                if not self.logged_in():
                    return self.login_required()
                if fake.highlights_tray_refused:
                    return self.json({"message": "Please wait a few minutes before you try again.",
                                      "require_login": True, "status": "fail"}, 401)
                tray = []
                if m.group(1) == "1001":
                    tray = [{"id": f"highlight:{h}", "title": t, "media_count": len(d.highlight_items(h))}
                            for h, t in d.HIGHLIGHTS.items()]
                return self.json({"tray": tray, "status": "ok"})
            m = re.match(r"^/api/v1/users/(\d+)/info/$", path)
            if m:
                if not self.logged_in():
                    return self.login_required()
                name = next((n for n, i in USER_IDS.items() if i == m.group(1)), "")
                user = dict(d.user(name), profile_pic_id=f"9988776655_{m.group(1)}",
                            hd_profile_pic_url_info={"url": f"{d.base}/cdn/avatar_{name}_hd.jpg", "width": 1080, "height": 1080},
                            hd_profile_pic_versions=[{"url": f"{d.base}/cdn/avatar_{name}_320.jpg", "width": 320, "height": 320}])
                return self.json({"user": user, "status": "ok"})
            m = re.match(r"^/api/v1/media/(\d+)/info/$", path)
            if m:
                if not self.logged_in():
                    return self.login_required()
                code = shortcode(int(m.group(1)))
                item = d.find_post(code)
                if not item:
                    return self.json({"message": "Media not found or unavailable", "status": "fail"}, 404)
                return self.json({"items": [item], "num_results": 1, "more_available": False, "status": "ok"})
            self.json({"message": "not found", "status": "fail"}, 404)

        def graphql(self, form) -> None:
            d = fake.data
            blocked = self.list_blocked()
            if blocked:
                return self.blocked_response(blocked)
            doc_id = (form.get("doc_id") or [""])[0]
            try:
                variables = json.loads((form.get("variables") or ["{}"])[0])
            except ValueError:
                return self.json({"errors": [{"message": "bad variables"}]}, 400)
            after = variables.get("after")
            if doc_id == "7950326061742207":   # giriş yapılmamış: eski biçim
                uid = str(variables.get("id") or "")
                name = next((n for n, i in USER_IDS.items() if i == uid), "")
                items = d.posts.get(name, [])
                page = 1 if after == "QVFE_1" else int(str(after or "c0").lstrip("c") or 0)
                if not self.logged_in() and page >= LOGGED_OUT_PAGES:
                    return self.login_required()
                chunk = items[page * PAGE:(page + 1) * PAGE]
                return self.json({"data": {"user": {"edge_owner_to_timeline_media": {
                    "count": len(items),
                    "page_info": {"has_next_page": (page + 1) * PAGE < len(items), "end_cursor": f"c{page + 1}"},
                    "edges": [{"node": d.graphql_node(p)} for p in chunk]}}}, "status": "ok"})
            if doc_id == "28975909992013618":  # giriş yapılmış: v1 biçimi
                if not self.logged_in():
                    return self.login_required()
                name = str(variables.get("username") or "")
                items = d.posts.get(name, [])
                page = int(str(after or "c0").lstrip("c") or 0)
                chunk = items[page * PAGE:(page + 1) * PAGE]
                return self.json({"data": {"xdt_api__v1__feed__user_timeline_graphql_connection": {
                    "edges": [{"node": p, "cursor": p["pk"]} for p in chunk],
                    "page_info": {"has_next_page": (page + 1) * PAGE < len(items), "end_cursor": f"c{page + 1}"}}},
                    "extensions": {"is_final": True}, "status": "ok"})
            self.json({"errors": [{"message": "execution error"}], "status": "fail"})

    return Handler


SCROLL_SCRIPT = """<script>
(() => {
  const name = location.pathname.split('/').filter(Boolean)[0];
  let page = 1, busy = false, done = false;
  const grid = () => document.getElementById('grid');
  function grow() { const d = document.createElement('div'); d.style.height = '1600px'; d.textContent = 'gönderiler'; grid().appendChild(d); }
  addEventListener('DOMContentLoaded', () => { grow(); grow();
    fetch('/graphql/query/?hl=1&u=' + encodeURIComponent(name)).catch(() => {}); });
  setInterval(async () => {
    if (busy || done || !grid()) return;
    if (innerHeight + scrollY < document.body.scrollHeight - 400) return;
    busy = true;
    try {
      const r = await fetch('/graphql/query/?scroll=' + page + '&u=' + encodeURIComponent(name));
      const j = await r.json();
      const c = j.data.xdt_api__v1__feed__user_timeline_graphql_connection;
      if (!c.edges.length || !c.page_info.has_next_page) done = !c.page_info.has_next_page;
      page += 1; grow();
    } catch (e) {} finally { busy = false; }
  }, 300);
})();
</script>"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--public", default="")
    args = parser.parse_args()
    fake = FakeInstagram(args.port, args.public).start()
    print(f"Sahte Instagram: {fake.base}", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        fake.stop()


if __name__ == "__main__":
    main()
