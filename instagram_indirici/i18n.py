"""Çeviri dosyalarını (ceviriler/*.json) okur ve mesajları seçili dile çevirir.

Program içindeki hiçbir kullanıcı metni koda gömülü değildir. Kod sadece
mesajın anahtarını ve değişkenlerini (Msg) üretir; metne çevirme işi
arayüzde ya da burada yapılır. Bir dilde eksik anahtar varsa İngilizcesi
kullanılır, o da yoksa anahtarın kendisi gösterilir.
"""

from __future__ import annotations

import json
import locale
import re
import sys
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List

FALLBACK = "en"
_PLACEHOLDER = re.compile(r"\{(\w+)\}")


@dataclass
class Msg:
    """Çevrilecek bir mesaj: anahtar + değişkenler (değişkenler de Msg olabilir)."""

    key: str
    params: Dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> Dict[str, Any]:
        return {"key": self.key, "params": {k: _jsonable(v) for k, v in self.params.items()}}

    def render(self, lang: str = "") -> str:
        return translate(self.key, lang or current_language(), **self.params)

    def __str__(self) -> str:
        return self.render()


def msg(key: str, **params: Any) -> Msg:
    return Msg(key, params)


def _jsonable(value: Any) -> Any:
    if isinstance(value, Msg):
        return value.to_json()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def translations_dir() -> Path:
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base) / "ceviriler"
    return Path(__file__).resolve().parents[1] / "ceviriler"


@lru_cache(maxsize=None)
def load(lang: str) -> Dict[str, str]:
    try:
        data = json.loads((translations_dir() / f"{lang}.json").read_text(encoding="utf-8"))
        return {k: v for k, v in data.items() if isinstance(v, str)}
    except (OSError, ValueError):
        return {}


def available_languages() -> List[Dict[str, str]]:
    out = []
    for path in sorted(translations_dir().glob("*.json")):
        code = path.stem
        out.append({"code": code, "name": load(code).get("_language_name", code)})
    return out


def system_language() -> str:
    codes = {item["code"] for item in available_languages()}
    candidates: List[str] = []
    if sys.platform == "win32":
        try:
            import ctypes

            lcid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            name = locale.windows_locale.get(lcid, "")
            candidates.append(name)
        except Exception:
            pass
    try:
        candidates.append(locale.getlocale()[0] or "")
    except Exception:
        pass
    for cand in candidates:
        code = (cand or "").split("_")[0].split("-")[0].lower()
        if code in codes:
            return code
    return FALLBACK


_current = {"lang": ""}


def set_language(setting: str) -> str:
    codes = {item["code"] for item in available_languages()}
    _current["lang"] = setting if setting in codes else system_language()
    return _current["lang"]


def current_language() -> str:
    return _current["lang"] or set_language("auto")


def strings_for(lang: str) -> Dict[str, str]:
    merged = dict(load(FALLBACK))
    merged.update(load(lang))
    return merged


def compose_summary(lang: str, done: int = 0, skipped: int = 0, failed: int = 0, unmatched: int = 0,
                    unavailable: int = 0, **_: Any) -> str:
    """"Bitti: 3 dosya indirildi, 2 dosya zaten vardı." gibi özet cümlesi kurar."""
    parts = [translate("summary.done", lang, n=done)]
    for key, value in (("summary.skipped", skipped), ("summary.failed", failed), ("summary.unmatched", unmatched),
                       ("summary.unavailable", unavailable)):
        if value:
            parts.append(translate(key, lang, n=value))
    return translate("summary.wrap", lang, parts=", ".join(parts))


def translate(key: str, lang: str = "", **params: Any) -> str:
    lang = lang or current_language()
    if key == "summary":
        return compose_summary(lang, **params)
    text = load(lang).get(key) or load(FALLBACK).get(key) or key
    rendered = {k: (v.render(lang) if isinstance(v, Msg) else v) for k, v in params.items()}
    return _PLACEHOLDER.sub(lambda m: str(rendered.get(m.group(1), m.group(0))), text)
