"""Ayrıntılı rapor: sorunu uzaktan incelemek için sürüm, bilgisayar, ayarlar, durum ve günlük tek metinde.

Çerez, oturum anahtarı, şifre ve erişim bilgisi içermez (varsa silinir). Android'deki Report.kt'nin karşılığı.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Tuple

from .i18n import Msg, translate

MAX_CHARS = 150_000

_SECRET = re.compile(
    r"(?i)(sessionid|csrftoken|ds_user_id|ig_did|datr|mid|rur|ig_nrcb|authorization|password|passwd|token)"
    r"([\"']?\s*[:=]\s*[\"']?)[^;\s&,\"'}]+")


def redact(text: str) -> str:
    return _SECRET.sub(lambda m: f"{m.group(1)}{m.group(2)}[silindi]", text)


def render_json(m: Any, lang: str) -> str:
    """{"key":..., "params":{...}} biçimindeki mesajı metne çevirir."""
    if not isinstance(m, dict):
        return str(m or "")
    params = {k: (render_json(v, lang) if isinstance(v, dict) and "key" in v else v)
              for k, v in (m.get("params") or {}).items()}
    return translate(str(m.get("key") or ""), lang, **params)


def _log_line(entry: Dict[str, Any], lang: str) -> str:
    m = entry.get("msg")
    text = render_json(m, lang) if m else str(entry.get("text") or "")
    return f"{entry.get('t', '')} [{entry.get('level', '')}] {text.replace(chr(10), ' ')}"


def build(info: List[Tuple[str, str]], settings: Dict[str, Any], snapshot: Dict[str, Any], lang: str) -> str:
    out: List[str] = ["Instagram İndirici raporu"]
    for k, v in info:
        out.append(f"{k}: {v}")
    out.append("\n--- Ayarlar ---")
    s = dict(settings)
    s.pop("out_dir", None)
    out.append(json.dumps(s, ensure_ascii=False))

    out.append("\n--- Durum ---")
    for k in ("running", "phase", "mode", "logged_in", "login_wall", "rate_limited", "author", "found_posts",
              "posts_done", "posts_skipped", "posts_unavailable", "files_total", "files_done", "files_skipped",
              "files_failed"):
        out.append(f"{k}: {snapshot.get(k)}")
    if snapshot.get("summary"):
        out.append("summary: " + render_json(snapshot["summary"], lang))

    lines = [_log_line(e, lang) for e in (snapshot.get("logs") or [])]
    out.append(f"\n--- Günlük ({len(lines)} satır) ---")
    head = sum(len(x) + 1 for x in out)
    body: List[str] = []
    size = 0
    truncated = False
    for line in reversed(lines):          # sığmazsa en eski satırlar atılır
        if head + size + len(line) + 1 > MAX_CHARS:
            truncated = True
            break
        body.insert(0, line)
        size += len(line) + 1
    if truncated:
        out.append("(başı kısaltıldı)")
    out.extend(body)
    return redact("\n".join(out) + "\n")
