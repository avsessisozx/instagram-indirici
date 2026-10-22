"""Instagram yanıtlarındaki gönderileri bulur ve indirilecek dosyalara çevirir.

Instagram aynı gönderiyi iki ayrı biçimde verebiliyor:
  * "v1" biçimi (uygulama ve web sitesinin yeni sorguları): pk, code, image_versions2,
    video_versions, carousel_media, caption.text, taken_at, user
  * eski GraphQL biçimi (giriş yapmadan açılan profil): id, shortcode, display_url,
    display_resources, video_url, edge_sidecar_to_children, taken_at_timestamp, owner
Bu dosya ikisini de tanır; yanıtın içinde nerede durduklarını bilmesi gerekmez.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlsplit

from . import links
from .i18n import Msg, msg
from .naming import date_from_timestamp, post_stem, sanitize
from .paths import CONTENT_IMAGE, CONTENT_VIDEO

KIND_POST = "post"
KIND_REEL = "reel"
KIND_REPOST = "repost"
KIND_TAGGED = "tagged"
KIND_STORY = "story"
KIND_HIGHLIGHT = "highlight"
KIND_AVATAR = "avatar"

# Gönderi gibi adlandırılan (kod + açıklama + tarih) türler
FEED_KINDS = (KIND_POST, KIND_REEL, KIND_REPOST, KIND_TAGGED)
# Arayüzdeki sekme sayaçları için: tür -> sekme
SECTION_OF_KIND = {KIND_POST: "posts", KIND_REEL: "reels", KIND_REPOST: "reposts", KIND_TAGGED: "tagged",
                   KIND_STORY: "stories", KIND_HIGHLIGHT: "highlights", KIND_AVATAR: "avatar"}

STORIES_DIR = "Hikayeler"
HIGHLIGHTS_DIR = "Öne Çıkanlar"
AVATAR_DIR = "Profil Fotoğrafı"
POSTS_DIR = "Gönderiler"
PHOTOS_DIR = "Fotoğraflar"
VIDEOS_DIR = "Videolar"
ALBUMS_DIR = "Albümler"
REELS_DIR = "Reels"
REPOSTS_DIR = "Yeniden Paylaşımlar"
TAGGED_DIR = "Etiketlenenler"


@dataclass
class DashStream:
    """DASH bildirimindeki (video_dash_manifest) tek bir akış: ses ya da görüntü."""
    url: str
    width: int = 0
    height: int = 0
    bandwidth: int = 0        # bit/sn

    @property
    def area(self) -> int:
        return self.width * self.height


@dataclass
class DashChoice:
    video: DashStream
    audio: Optional[DashStream] = None


@dataclass
class MediaFile:
    kind: str                 # "video" | "image"
    urls: List[str]
    rel_stem: str             # klasöre göre yol, uzantısız (ör. "Hikayeler/2026-10-01_123")
    ext: str = ""             # boşsa sunucunun bildirdiği türe göre seçilir
    expected_size: int = 0
    legacy_stems: List[str] = field(default_factory=list)   # eski sürümlerin aynı dosyayı koyduğu yerler (varsa buradan taşınır)
    dash: Optional[DashChoice] = None   # varsa: normal sürümden daha iyi ses+görüntü akışı (ffmpeg ile birleştirilir)
    plain_res: str = ""       # normal (tek parça) sürümün çözünürlüğü, ör. "720x1280" (kayıt için)


@dataclass
class Post:
    key: str = ""
    code: str = ""
    owner_id: str = ""
    kind: str = KIND_POST
    username: str = ""
    author_name: str = ""
    taken_at: int = 0
    pinned: bool = False
    stem: str = ""
    section: str = ""               # arayüz sekmesi: posts / reels / reposts / tagged / stories / highlights / avatar
    dest_user: str = ""             # dosyaların hangi profil klasörüne gideceği (boşsa username)
    caption: str = ""
    caption_stem: str = ""          # uzun açıklamanın .txt dosyası (uzantısız yol); boşsa açıklama ayrıca kaydedilmez
    likes: int = 0
    comments: int = 0
    permalink: str = ""
    attempts: int = 0               # kaç kez indirilmeye çalışıldı (yeniden deneme için)
    failed_files: int = 0           # bu gönderide inemeyen dosya sayısı
    last_error: str = ""            # son indirme hatası (arayüzdeki "inemeyenler" listesi için)
    files: List[MediaFile] = field(default_factory=list)
    skipped_reason: Optional[Msg] = None
    history_content: str = ""       # geçmişe hangi içerik seçimiyle yazılacağı
    page: Optional[Dict[str, Any]] = field(default=None, repr=False)   # motorun sayfa takibi

    @property
    def is_gallery(self) -> bool:
        return len(self.files) > 1


# ------------------------------------------------------------ tanıma
def is_v1_media(d: Any) -> bool:
    if not isinstance(d, dict):
        return False
    if not ("pk" in d or "id" in d):
        return False
    return any(k in d for k in ("image_versions2", "video_versions", "carousel_media"))


def is_graphql_media(d: Any) -> bool:
    if not isinstance(d, dict) or not d.get("shortcode"):
        return False
    return any(k in d for k in ("display_url", "display_resources", "edge_sidecar_to_children", "video_url"))


def is_media(d: Any) -> bool:
    return is_v1_media(d) or is_graphql_media(d)


def find_media(obj: Any, limit: int = 100000) -> List[Dict[str, Any]]:
    """Herhangi bir JSON'un içindeki gönderileri sırayla bulur (albüm parçalarına inmez)."""
    out: List[Dict[str, Any]] = []
    stack = [obj]
    # Belge sırasını korumak için derinlik öncelikli, ters sırayla yığına koy.
    while stack and len(out) < limit:
        cur = stack.pop()
        if isinstance(cur, dict):
            if is_media(cur):
                out.append(cur)
                continue
            stack.extend(reversed(list(cur.values())))
        elif isinstance(cur, list):
            stack.extend(reversed(cur))
    return out


def find_page_info(obj: Any) -> Optional[Dict[str, Any]]:
    """GraphQL yanıtındaki sayfalama bilgisini (has_next_page / end_cursor) bulur."""
    stack = [obj]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            info = cur.get("page_info")
            if isinstance(info, dict) and "has_next_page" in info:
                return info
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)
    return None


def find_user(obj: Any, username: str) -> Optional[Dict[str, Any]]:
    """Yanıtın içinde bu kullanıcı adına ait profil bilgisini bulur (en çok alanı olanı)."""
    best: Optional[Dict[str, Any]] = None
    stack = [obj]
    name = username.lower()
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            if str(cur.get("username") or "").lower() == name and (cur.get("pk") or cur.get("id") or cur.get("pk_id")):
                if best is None or len(cur) > len(best):
                    best = cur
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)
    return best


def find_highlights(obj: Any) -> List[Dict[str, Any]]:
    """Yanıtın içindeki öne çıkanları bulur: [{"id": "highlight:…", "title", "media_count"}]."""
    out: List[Dict[str, Any]] = []
    seen = set()

    def add(hid: Any, entry: Dict[str, Any]) -> None:
        hid = str(hid or "")
        if not hid:
            return
        if not hid.startswith("highlight:"):
            if not hid.isdigit():
                return
            hid = f"highlight:{hid}"
        if hid in seen:
            return
        seen.add(hid)
        out.append({"id": hid, "title": str(entry.get("title") or ""),
                    "media_count": _int(entry.get("media_count") or entry.get("highlight_reel_count"))})

    stack = [obj]
    while stack:
        cur = stack.pop(0)
        if isinstance(cur, dict):
            if str(cur.get("id") or "").startswith("highlight:") and "title" in cur:
                add(cur["id"], cur)
                continue
            edge = cur.get("edge_highlight_reels")
            if isinstance(edge, dict):
                for e in edge.get("edges") or []:
                    node = e.get("node") if isinstance(e, dict) else None
                    if isinstance(node, dict):
                        add(node.get("id"), node)
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)
    return out


def user_id_of(user: Dict[str, Any]) -> str:
    for key in ("pk", "pk_id", "id"):
        value = user.get(key)
        if value and str(value).isdigit():
            return str(value)
    return ""


def media_key(item: Dict[str, Any]) -> str:
    """Bir gönderinin değişmeyen kimliği (aynı gönderiyi iki kez saymamak için)."""
    code = item.get("code") or item.get("shortcode")
    if code:
        return str(code)
    pk = item.get("pk") or str(item.get("id") or "").split("_")[0]
    return str(pk or "")


# ------------------------------------------------------------ alanlar
def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _area(entry: Dict[str, Any], w: str = "width", h: str = "height") -> int:
    return _int(entry.get(w)) * _int(entry.get(h))


def _dedupe(urls: Iterable[str]) -> List[str]:
    out: List[str] = []
    for url in urls:
        if isinstance(url, str) and url.startswith("http") and url not in out:
            out.append(url)
    return out


def image_urls(item: Dict[str, Any]) -> List[str]:
    """En büyükten küçüğe resim adresleri."""
    urls: List[str] = []
    candidates = ((item.get("image_versions2") or {}).get("candidates")) if isinstance(item.get("image_versions2"), dict) else None
    if isinstance(candidates, list):
        good = [c for c in candidates if isinstance(c, dict) and c.get("url")]
        # Aynı alanlıysa listedeki sırayı koru (Instagram en iyisini başa koyuyor).
        ordered = sorted(enumerate(good), key=lambda pair: (-_area(pair[1]), pair[0]))
        urls.extend(c["url"] for _i, c in ordered)
    resources = item.get("display_resources")
    if isinstance(resources, list):
        good = [r for r in resources if isinstance(r, dict) and r.get("src")]
        good.sort(key=lambda r: -_area(r, "config_width", "config_height"))
        urls.extend(r["src"] for r in good)
    for key in ("display_url", "display_src", "thumbnail_src"):
        if item.get(key):
            urls.append(item[key])
    return _dedupe(urls)


def video_urls(item: Dict[str, Any]) -> List[str]:
    urls: List[str] = []
    versions = item.get("video_versions")
    if isinstance(versions, list):
        good = [v for v in versions if isinstance(v, dict) and v.get("url")]
        ordered = sorted(enumerate(good), key=lambda pair: (-_area(pair[1]), pair[0]))
        urls.extend(v["url"] for _i, v in ordered)
    if item.get("video_url"):
        urls.append(item["video_url"])
    return _dedupe(urls)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def dash_streams(item: Dict[str, Any]) -> Tuple[List[DashStream], List[DashStream]]:
    """video_dash_manifest (MPD) içindeki tek dosyalı video ve ses akışları.

    Instagram uygulama içinde oynatırken bu bildirimi kullanır; en yüksek kalite çoğu zaman sadece burada olur,
    video_versions listesi daha düşük çözünürlükte durur. Parçalı (SegmentTemplate/SegmentList) akışlar atlanır.
    """
    manifest = item.get("video_dash_manifest")
    if not manifest and isinstance(item.get("dash_info"), dict):
        manifest = item["dash_info"].get("video_dash_manifest")
    if not isinstance(manifest, str) or "<" not in manifest:
        return [], []
    try:
        root = ET.fromstring(manifest.strip())
    except ET.ParseError:
        return [], []
    videos: List[DashStream] = []
    audios: List[DashStream] = []
    for aset in root.iter():
        if _local(aset.tag) != "AdaptationSet":
            continue
        set_type = (aset.get("contentType") or aset.get("mimeType") or "").lower()
        set_segmented = any(_local(c.tag) in ("SegmentTemplate", "SegmentList") for c in aset)
        for rep in aset:
            if _local(rep.tag) != "Representation":
                continue
            url = ""
            segmented = set_segmented
            for child in rep:
                name = _local(child.tag)
                if name == "BaseURL" and child.text and child.text.strip().startswith("http"):
                    url = child.text.strip()
                elif name in ("SegmentTemplate", "SegmentList"):
                    segmented = True
            if not url or segmented:
                continue
            kind = (rep.get("mimeType") or set_type).lower()
            stream = DashStream(url, _int(rep.get("width")), _int(rep.get("height")), _int(rep.get("bandwidth")))
            if "audio" in kind:
                audios.append(stream)
            elif "video" in kind or stream.width:
                videos.append(stream)
    return videos, audios


def dash_choice(item: Dict[str, Any]) -> Optional[DashChoice]:
    videos, audios = dash_streams(item)
    if not videos:
        return None
    best = max(videos, key=lambda s: (s.area, s.bandwidth))
    audio = max(audios, key=lambda s: s.bandwidth) if audios else None
    return DashChoice(best, audio)


def plain_size(item: Dict[str, Any]) -> Tuple[int, int]:
    """Normal (tek parça) sürümlerin en büyüğünün genişlik ve yüksekliği; bilinmiyorsa (0, 0)."""
    best = (0, 0)
    versions = item.get("video_versions")
    if isinstance(versions, list):
        for v in versions:
            if isinstance(v, dict) and v.get("url"):
                size = (_int(v.get("width")), _int(v.get("height")))
                if size[0] * size[1] > best[0] * best[1]:
                    best = size
    if best == (0, 0) and isinstance(item.get("dimensions"), dict):
        best = (_int(item["dimensions"].get("width")), _int(item["dimensions"].get("height")))
    return best


def is_video(item: Dict[str, Any]) -> bool:
    if item.get("video_versions") or item.get("video_url"):
        return True
    if item.get("is_video") is True:
        return True
    return _int(item.get("media_type")) == 2


def children(item: Dict[str, Any]) -> List[Dict[str, Any]]:
    carousel = item.get("carousel_media")
    if isinstance(carousel, list) and carousel:
        return [c for c in carousel if isinstance(c, dict)]
    sidecar = item.get("edge_sidecar_to_children")
    if isinstance(sidecar, dict):
        nodes = [e.get("node") for e in sidecar.get("edges") or [] if isinstance(e, dict)]
        return [n for n in nodes if isinstance(n, dict)]
    return []


def caption_of(item: Dict[str, Any]) -> str:
    caption = item.get("caption")
    if isinstance(caption, dict):
        return str(caption.get("text") or "")
    if isinstance(caption, str):
        return caption
    edges = ((item.get("edge_media_to_caption") or {}).get("edges")) if isinstance(item.get("edge_media_to_caption"), dict) else None
    if isinstance(edges, list):
        texts = [str((e.get("node") or {}).get("text") or "") for e in edges if isinstance(e, dict)]
        return "\n".join(t for t in texts if t)
    return ""


def taken_at_of(item: Dict[str, Any]) -> int:
    for key in ("taken_at", "taken_at_timestamp", "device_timestamp", "created_at"):
        value = _int(item.get(key))
        if value > 10_000_000_000:      # milisaniye/mikrosaniye gelmişse
            while value > 10_000_000_000:
                value //= 1000
        if value > 0:
            return value
    return 0


def owner_of(item: Dict[str, Any]) -> Dict[str, Any]:
    for key in ("user", "owner"):
        if isinstance(item.get(key), dict):
            return item[key]
    return {}


def is_pinned(item: Dict[str, Any]) -> bool:
    return bool(item.get("timeline_pinned_user_ids") or item.get("pinned_for_users")
                or item.get("clips_tab_pinned_user_ids"))


def _url_stem(url: str) -> str:
    name = PurePosixPath(urlsplit(url).path).name
    return name.rsplit(".", 1)[0] if "." in name else name


# ------------------------------------------------------------ dosya listesi
def _wanted(kind: str, content: str) -> bool:
    if content == CONTENT_VIDEO:
        return kind == "video"
    if content == CONTENT_IMAGE:
        return kind == "image"
    return True


def _file_for(node: Dict[str, Any], rel_stem: str) -> Optional[MediaFile]:
    if is_video(node):
        urls = video_urls(node)
        width, height = plain_size(node)
        media = MediaFile("video", urls, rel_stem, ".mp4", plain_res=f"{width}x{height}" if width and height else "")
        dash = dash_choice(node)
        # Daha büyük (ya da aynı) çözünürlükte DASH akışı varsa onu kullan; indirilemezse normal sürüme dönülür.
        if dash is not None and dash.video.area >= width * height:
            media.dash = dash
        return media
    urls = image_urls(node)
    return MediaFile("image", urls, rel_stem, "")


def is_reel(item: Dict[str, Any]) -> bool:
    """Reels olarak paylaşılmış mı? (product_type == "clips")"""
    return str(item.get("product_type") or "").lower() == "clips"


def count_of(item: Dict[str, Any], v1_key: str, edge_key: str) -> int:
    """Beğeni / yorum sayısı: v1 biçiminde düz alan, eski GraphQL biçiminde edge_*.count."""
    value = _int(item.get(v1_key))
    if value:
        return value
    edge = item.get(edge_key)
    return _int(edge.get("count")) if isinstance(edge, dict) else 0


def feed_folder(kind: str, media_kind: str, album: bool, stem: str) -> str:
    """Gönderi türüne göre klasör: Gönderiler/Fotoğraflar|Videolar|Albümler/<ad>, Reels, ..."""
    if kind == KIND_POST:
        if album:
            return f"{POSTS_DIR}/{ALBUMS_DIR}/{stem}"
        return f"{POSTS_DIR}/{VIDEOS_DIR if media_kind == 'video' else PHOTOS_DIR}"
    base = {KIND_REEL: REELS_DIR, KIND_REPOST: REPOSTS_DIR, KIND_TAGGED: TAGGED_DIR}[kind]
    return f"{base}/{stem}" if album else base


def feed_legacy(kind: str, media_kind: str, name: str) -> List[str]:
    """Eski sürümlerin (1.0.x kök klasör, 1.1+ Fotoğraflar/Videolar) aynı dosyayı koyduğu yerler."""
    old_dir = VIDEOS_DIR if media_kind == "video" else PHOTOS_DIR
    if kind == KIND_POST:
        return [f"{old_dir}/{name}", name]
    if kind == KIND_REEL:
        return [f"{POSTS_DIR}/{VIDEOS_DIR}/{name}", f"{VIDEOS_DIR}/{name}", name]
    return []


def build_post(item: Dict[str, Any], *, content: str, kind: str = KIND_POST,
               subdir: str = "", username: str = "", caption_min: int = 0) -> Post:
    owner = owner_of(item)
    if kind == KIND_POST and is_reel(item):
        kind = KIND_REEL      # ızgarada görünen Reels de Reels/ klasörüne iner
    post = Post(kind=kind)
    post.section = SECTION_OF_KIND.get(kind, "")
    post.key = media_key(item)
    post.code = str(item.get("code") or item.get("shortcode") or links.shortcode_from_id(item.get("pk") or item.get("id")) or "")
    post.username = str(owner.get("username") or username or "")
    post.author_name = str(owner.get("full_name") or "")
    post.taken_at = taken_at_of(item)
    post.pinned = is_pinned(item)
    post.caption = caption_of(item).strip()
    post.likes = count_of(item, "like_count", "edge_media_preview_like")
    post.comments = count_of(item, "comment_count", "edge_media_to_comment")
    if post.code and kind in FEED_KINDS:
        post.permalink = f"https://www.instagram.com/{'reel' if kind == KIND_REEL else 'p'}/{post.code}/"

    feed = kind in FEED_KINDS
    if feed:
        post.stem = post_stem(post.code or post.key, caption_of(item), post.taken_at)
    else:
        pk = str(item.get("pk") or str(item.get("id") or "").split("_")[0] or post.key)
        post.stem = f"{date_from_timestamp(post.taken_at)}_{pk}"
    post.owner_id = str(owner.get("pk") or owner.get("id") or owner.get("pk_id") or "")
    prefix = f"{subdir}/" if subdir else ""

    nodes = children(item) or [item]
    album = len(nodes) > 1
    files: List[MediaFile] = []
    unmatched = 0
    missing_video = 0
    caption_folder = ""
    for index, node in enumerate(nodes, 1):
        name = f"{post.stem}_{index:02d}" if album else post.stem
        media = _file_for(node, f"{prefix}{name}")
        if media is None:
            continue
        if feed:
            folder = feed_folder(kind, media.kind, album, post.stem)
            media.rel_stem = f"{folder}/{name}"
            media.legacy_stems = feed_legacy(kind, media.kind, name)
            caption_folder = caption_folder or folder
        if not _wanted(media.kind, content):
            unmatched += 1
            continue
        if not media.urls and media.dash is None:
            if media.kind == "video":
                missing_video += 1
            continue
        files.append(media)
    post.files = files
    if feed and caption_min > 0 and len(post.caption) >= caption_min and caption_folder:
        post.caption_stem = f"{caption_folder}/{post.stem}"
    if not files:
        if unmatched:
            post.skipped_reason = msg("skip.video_only" if content == CONTENT_VIDEO else "skip.image_only")
        elif missing_video:
            post.skipped_reason = msg("skip.no_video")
        else:
            post.skipped_reason = msg("skip.no_media")
    return post


def build_avatar(user: Dict[str, Any], urls: List[str], *, content: str) -> Post:
    post = Post(kind=KIND_AVATAR, username=str(user.get("username") or ""),
                author_name=str(user.get("full_name") or ""), section="avatar")
    urls = _dedupe(urls)
    pic_id = str(user.get("profile_pic_id") or "").split("_")[0]
    name = pic_id or (sanitize(_url_stem(urls[0]), 80, "profil") if urls else "profil")
    post.key = f"avatar:{name}"
    post.stem = name
    if content == CONTENT_VIDEO:
        post.skipped_reason = msg("skip.image_only_avatar")
    elif not urls:
        post.skipped_reason = msg("skip.no_media")
    else:
        post.files = [MediaFile("image", urls, f"{AVATAR_DIR}/{name}", "")]
    return post


def avatar_urls(user: Dict[str, Any]) -> List[str]:
    urls: List[str] = []
    info = user.get("hd_profile_pic_url_info")
    if isinstance(info, dict) and info.get("url"):
        urls.append(info["url"])
    versions = user.get("hd_profile_pic_versions")
    if isinstance(versions, list):
        good = sorted((v for v in versions if isinstance(v, dict) and v.get("url")), key=lambda v: -_area(v))
        urls.extend(v["url"] for v in good)
    for key in ("profile_pic_url_hd", "profile_pic_url"):
        if user.get(key):
            urls.append(user[key])
    return _dedupe(urls)


def author_folder(username: str) -> str:
    return sanitize(username or "instagram", max_len=60, fallback="instagram")


def highlight_folder(title: Any, highlight_id: str) -> str:
    name = sanitize(title, max_len=50, fallback="")
    return f"{HIGHLIGHTS_DIR}/{name or highlight_id}"
