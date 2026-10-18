"""Windows'ta sorunsuz çalışan dosya ve klasör adları üretir."""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from typing import Any

_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')
_SPACES = re.compile(r"\s+")
_HASHTAG = re.compile(r"#\S+")
_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def sanitize(name: Any, max_len: int = 60, fallback: str = "adsiz") -> str:
    text = unicodedata.normalize("NFC", str(name or ""))
    text = re.sub(r"[\r\n\t\v\f]+", " ", text)
    # Görünmez yön/biçim karakterlerini at (ör. U+200B, U+202E).
    text = "".join(ch for ch in text if unicodedata.category(ch) not in ("Cf", "Cc", "Cs", "Co"))
    text = _INVALID.sub(" ", text)
    text = _SPACES.sub(" ", text).strip(" .")
    if len(text) > max_len:
        text = text[:max_len].rstrip(" .")
    if not text:
        text = fallback
    if text.split(".")[0].upper() in _RESERVED:
        text = f"{text}_"
    return text


def title_from_desc(desc: Any, max_len: int = 40) -> str:
    raw = str(desc or "")
    without_tags = _HASHTAG.sub(" ", raw)
    title = sanitize(without_tags, max_len=max_len, fallback="")
    if not title:
        tags = [t.lstrip("#") for t in _HASHTAG.findall(raw)]
        title = sanitize(" ".join(tags), max_len=max_len, fallback="")
    return title or "gonderi"


def date_from_timestamp(ts: Any) -> str:
    try:
        value = int(ts)
        if value <= 0:
            return "tarihsiz"
        return datetime.fromtimestamp(value).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError, OverflowError):
        return "tarihsiz"


def post_stem(aweme_id: str, desc: Any, create_time: Any) -> str:
    return f"{date_from_timestamp(create_time)}_{title_from_desc(desc)}_{aweme_id}"
