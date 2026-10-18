"""Instagram yanıtlarındaki gönderileri bulur ve indirilecek dosyalara çevirir.

Instagram aynı gönderiyi iki ayrı biçimde verebiliyor:
  * "v1" biçimi (uygulama ve web sitesinin yeni sorguları): pk, code, image_versions2,
    video_versions, carousel_media, caption.text, taken_at, user
  * eski GraphQL biçimi (giriş yapmadan açılan profil): id, shortcode, display_url,
    display_resources, video_url, edge_sidecar_to_children, taken_at_timestamp, owner
Bu dosya ikisini de tanır; yanıtın içinde nerede durduklarını bilmesi gerekmez.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlsplit

from . import links
from .i18n import Msg, msg
from .naming import date_from_timestamp, post_stem, sanitize
from .paths import CONTENT_IMAGE, CONTENT_VIDEO

KIND_POST = "post"
KIND_STORY = "story"
KIND_HIGHLIGHT = "highlight"
KIND_AVATAR = "avatar"

STORIES_DIR = "Hikayeler"
HIGHLIGHTS_DIR = "Öne Çıkanlar"
AVATAR_DIR = "Profil Fotoğrafı"


@dataclass
class MediaFile:
    kind: str                 # "video" | "image"
    urls: List[str]
    rel_stem: str             # klasöre göre yol, uzantısız (ör. "Hikayeler/2026-10-01_123")
    ext: str = ""             # boşsa sunucunun bildirdiği türe göre seçilir
    expected_size: int = 0


@dataclass
class Post:
    key: str = ""
    code: str = ""
    kind: str = KIND_POST
    username: str = ""
    author_name: str = ""
    taken_at: int = 0
    pinned: bool = False
    stem: str = ""
    files: List[MediaFile] = field(default_factory=list)
    skipped_reason: Optional[Msg] = None

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
        if not urls:
            return MediaFile("video", [], rel_stem, ".mp4")
        return MediaFile("video", urls, rel_stem, ".mp4")
    urls = image_urls(node)
    return MediaFile("image", urls, rel_stem, "")


def build_post(item: Dict[str, Any], *, content: str, kind: str = KIND_POST,
               subdir: str = "", username: str = "") -> Post:
    owner = owner_of(item)
    post = Post(kind=kind)
    post.key = media_key(item)
    post.code = str(item.get("code") or item.get("shortcode") or links.shortcode_from_id(item.get("pk") or item.get("id")) or "")
    post.username = str(owner.get("username") or username or "")
    post.author_name = str(owner.get("full_name") or "")
    post.taken_at = taken_at_of(item)
    post.pinned = is_pinned(item)

    if kind == KIND_POST:
        post.stem = post_stem(post.code or post.key, caption_of(item), post.taken_at)
    else:
        pk = str(item.get("pk") or str(item.get("id") or "").split("_")[0] or post.key)
        post.stem = f"{date_from_timestamp(post.taken_at)}_{pk}"
    prefix = f"{subdir}/" if subdir else ""

    nodes = children(item) or [item]
    files: List[MediaFile] = []
    unmatched = 0
    missing_video = 0
    for index, node in enumerate(nodes, 1):
        rel = f"{prefix}{post.stem}_{index:02d}" if len(nodes) > 1 else f"{prefix}{post.stem}"
        media = _file_for(node, rel)
        if media is None:
            continue
        if not _wanted(media.kind, content):
            unmatched += 1
            continue
        if not media.urls:
            if media.kind == "video":
                missing_video += 1
            continue
        files.append(media)
    post.files = files
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
                author_name=str(user.get("full_name") or ""))
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
