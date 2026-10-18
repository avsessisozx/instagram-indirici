"""Kullanıcının yapıştırdığı metinden Instagram profilini, gönderisini ya da hikayesini anlar."""

from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import unquote, urlsplit

from .i18n import Msg, msg

PROFILE = "profile"
POST = "post"
STORY = "story"
HIGHLIGHT = "highlight"
SHARE = "share"

_USERNAME = re.compile(r"^[A-Za-z0-9._]{1,30}$")
_URL = re.compile(r"(?:https?://)?(?:[a-z0-9-]+\.)*(?:instagram\.com|instagr\.am)(?:/[^\s<>\"']*)?", re.I)
_SHORTCODE = re.compile(r"^[A-Za-z0-9_-]{5,60}$")
_OTHER_SITES = {
    "tiktok.com": "link.other_site",
    "douyin.com": "link.other_site",
    "facebook.com": "link.other_site",
    "fb.watch": "link.other_site",
    "threads.net": "link.threads",
    "threads.com": "link.threads",
    "youtube.com": "link.other_site",
    "youtu.be": "link.other_site",
    "x.com": "link.other_site",
    "twitter.com": "link.other_site",
}
# Profil adı olamayacak ilk yol parçaları (Instagram'ın kendi sayfaları)
_RESERVED = {
    "p", "reel", "reels", "tv", "stories", "explore", "accounts", "direct", "about", "legal",
    "developer", "web", "api", "s", "share", "challenge", "emails", "session", "privacy", "terms",
    "graphql", "static", "directory", "nametag", "oauth", "topics", "lite", "ar", "invites",
    "your_activity", "notifications", "create", "settings", "help", "press", "jobs", "blog",
    "popular", "locations", "tags", "audio", "threads", "meta",
}
_PROFILE_TABS = {"", "reels", "tagged", "highlights", "posts", "saved", "feed", "followers", "following", "channel"}


@dataclass
class ParsedLink:
    ok: bool
    kind: str = ""
    username: str = ""
    shortcode: str = ""
    highlight_id: str = ""
    story_media_id: str = ""
    url: str = ""
    error: Optional[Msg] = None

    def describe(self) -> Msg:
        if not self.ok:
            return self.error or msg("link.not_found")
        if self.kind == PROFILE:
            return msg("link.profile_detected", name=self.username)
        if self.kind == POST:
            return msg("link.post_detected")
        if self.kind == STORY:
            return msg("link.story_detected", name=self.username)
        if self.kind == HIGHLIGHT:
            return msg("link.highlight_detected")
        return msg("link.share_detected")


def _fail(key: str, **params) -> ParsedLink:
    return ParsedLink(False, error=msg(key, **params))


def _find_url(text: str) -> str:
    match = _URL.search(text)
    if not match:
        return ""
    url = match.group(0).rstrip(".,;:!?)»”’")
    if not url.lower().startswith("http"):
        url = "https://" + url
    return url


def _highlight_from_base64(token: str) -> str:
    """/s/aGlnaGxpZ2h0OjE4MDAwMDAwMDAwMDAwMDA= biçimindeki öne çıkan linkini çözer."""
    token = token.strip()
    padded = token + "=" * (-len(token) % 4)
    for decoder in (base64.b64decode, base64.urlsafe_b64decode):
        try:
            text = decoder(padded).decode("utf-8", "replace")
        except (binascii.Error, ValueError):
            continue
        m = re.match(r"^highlight:(\d+)$", text.strip())
        if m:
            return m.group(1)
    return ""


def parse_link(raw: str) -> ParsedLink:
    text = (raw or "").strip()
    if not text:
        return _fail("link.empty")

    # Sadece kullanıcı adı: "nasa" ya da "@nasa"
    if text.startswith("@") and _USERNAME.match(text[1:]):
        return _profile(text[1:])
    if _USERNAME.match(text) and not re.search(r"\.(com|net|org|am|tr|io|me|ly|co|app|tv)$", text, re.I):
        return _profile(text)
    bare = text.lstrip("@")

    url = _find_url(text)
    if not url:
        lowered = text.lower()
        for site, key in _OTHER_SITES.items():
            if site in lowered:
                return _fail(key)
        if " " not in text and len(bare) > 30 and _USERNAME.match(bare[:30]):
            return _fail("link.username_too_long")
        return _fail("link.not_found")

    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    segments = [unquote(s) for s in parts.path.split("/") if s]

    if not segments:
        return _fail("link.no_target")

    first = segments[0].lower()

    # Paylaşım linkleri (instagram.com/share/...) tarayıcıda açılınca gerçek adrese gider.
    if first == "share":
        return ParsedLink(True, SHARE, url=url)

    # /p/KOD, /reel/KOD, /tv/KOD (önünde kullanıcı adı da olabilir: /nasa/p/KOD)
    for i, seg in enumerate(segments[:2]):
        if seg.lower() in ("p", "reel", "reels", "tv") and len(segments) > i + 1:
            code = segments[i + 1]
            if i == 1 and segments[0].lower() in _RESERVED:
                break
            if _SHORTCODE.match(code):
                return ParsedLink(True, POST, shortcode=code, url=url)
            return _fail("link.post_broken")
        if i == 0 and seg.lower() in ("p", "tv", "reel") and len(segments) == 1:
            return _fail("link.post_broken")

    if first == "reels" and len(segments) == 1:
        return _fail("link.no_target")

    if first == "stories":
        if len(segments) >= 3 and segments[1].lower() == "highlights" and segments[2].isdigit():
            return ParsedLink(True, HIGHLIGHT, highlight_id=segments[2], url=url)
        if len(segments) >= 2 and segments[1].lower() not in ("highlights", "me") and _USERNAME.match(segments[1]):
            media_id = segments[2] if len(segments) >= 3 and segments[2].isdigit() else ""
            return ParsedLink(True, STORY, username=segments[1].lower(), story_media_id=media_id, url=url)
        return _fail("link.no_target")

    if first == "s" and len(segments) >= 2:
        hid = _highlight_from_base64(segments[1])
        if hid:
            return ParsedLink(True, HIGHLIGHT, highlight_id=hid, url=url)
        return _fail("link.no_target")

    if first in ("explore",):
        return _fail("link.explore")
    if first in ("direct",):
        return _fail("link.direct")
    if first in _RESERVED:
        return _fail("link.no_target")

    if _USERNAME.match(segments[0]):
        tab = segments[1].lower() if len(segments) > 1 else ""
        if tab in _PROFILE_TABS or tab.startswith("?"):
            return _profile(segments[0], url)
        return _profile(segments[0], url)
    if host:
        return _fail("link.no_target")
    return _fail("link.not_found")


def _profile(name: str, url: str = "") -> ParsedLink:
    name = name.strip().lstrip("@").lower()
    if not _USERNAME.match(name):
        return _fail("link.not_found")
    return ParsedLink(True, PROFILE, username=name, url=url or f"https://www.instagram.com/{name}/")


# ------------------------------------------------------------ gönderi kodu <-> kimlik
_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"


def id_from_shortcode(code: str) -> str:
    # Gizli hesap paylaşım linklerinde kodun sonuna 28 karakter daha eklenir.
    if len(code) > 28:
        code = code[:-28]
    value = 0
    for ch in code:
        idx = _ALPHABET.find(ch)
        if idx < 0:
            return ""
        value = value * 64 + idx
    return str(value)


def shortcode_from_id(media_id) -> str:
    try:
        value = int(str(media_id).split("_")[0])
    except (TypeError, ValueError):
        return ""
    if value <= 0:
        return ""
    out = ""
    while value:
        value, rem = divmod(value, 64)
        out = _ALPHABET[rem] + out
    return out
