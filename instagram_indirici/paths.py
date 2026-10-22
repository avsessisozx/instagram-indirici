"""Program verisinin ve ayarların nerede tutulduğu."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict

CONTENT_ALL = "all"
CONTENT_VIDEO = "video"
CONTENT_IMAGE = "image"
CONTENT_CHOICES = (CONTENT_ALL, CONTENT_VIDEO, CONTENT_IMAGE)
LIMIT_CHOICES = (0, 12, 30, 50, 100, 300)
IDENTITY_CHOICES = ("desktop", "android", "tablet")   # Instagram'a hangi cihazmış gibi görünülür
SPEED_CHOICES = ("normal", "careful", "very_careful")  # istek hızı


def app_data_dir() -> Path:
    override = os.environ.get("INSTAGRAM_INDIRICI_DATA")
    if override:
        base = Path(override)
    elif sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "InstagramIndirici"
    else:
        base = Path.home() / ".instagram-indirici"
    base.mkdir(parents=True, exist_ok=True)
    return base


def default_download_dir() -> Path:
    if sys.platform == "win32":
        # Kullanıcının gerçek "İndirilenler" klasörü (başka sürücüye taşınmış olsa bile).
        try:
            import ctypes

            path_ptr = ctypes.c_wchar_p()
            fid = _GUID_DOWNLOADS()
            if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(fid), 0, None, ctypes.byref(path_ptr)) == 0:
                value = path_ptr.value
                ctypes.windll.ole32.CoTaskMemFree(path_ptr)
                if value:
                    return Path(value) / "Instagram"
        except Exception:
            pass
    return Path.home() / "Downloads" / "Instagram"


def _GUID_DOWNLOADS():
    import ctypes

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16),
                    ("Data3", ctypes.c_uint16), ("Data4", ctypes.c_ubyte * 8)]

    # FOLDERID_Downloads = {374DE290-123F-4565-9164-39C4925E467B}
    return GUID(0x374DE290, 0x123F, 0x4565, (ctypes.c_ubyte * 8)(0x91, 0x64, 0x39, 0xC4, 0x92, 0x5E, 0x46, 0x7B))


DEFAULT_SETTINGS: Dict[str, Any] = {
    "out_dir": "",
    "content": CONTENT_ALL,
    "limit": 0,
    "posts": True,
    "stories": True,
    "highlights": True,
    "avatar": True,
    "reels": True,          # Reels sekmesi (ayrı liste; Reels/ klasörüne iner)
    "reposts": False,       # Yeniden paylaşımlar sekmesi
    "tagged": False,        # Etiketlenenler sekmesi
    "only_new": True,
    "history": True,
    "auto_continue": True,
    "verify_files": True,   # geçmişte "indi" görünen ama dosyası olmayanları yeniden indir
    "caption_min": 300,     # bu kadar karakterden uzun açıklamalar ayrı .txt olarak kaydedilir (0 = kapalı)
    "info_csv": True,       # her profil klasörüne bilgi.csv (tarih, bağlantı, beğeni…) yaz
    "date_from": "",        # YYYY-MM-DD: bundan eski gönderiler indirilmez (boş = sınırsız)
    "date_to": "",          # YYYY-MM-DD: bundan yeni gönderiler indirilmez
    "lang": "auto",
    "identity": "desktop",
    "speed": "normal",
    "setup_done": False,    # ilk açılış sihirbazı bitti mi
    "no_confirm": False,    # silmeden önce onay sorma
}

CAPTION_MIN_MAX = 20000


def clean_date(value: Any) -> str:
    """YYYY-MM-DD biçimindeki geçerli tarihi olduğu gibi, geçersizi boş metin olarak döndürür."""
    import datetime

    text = str(value or "").strip()
    try:
        return datetime.date.fromisoformat(text).isoformat() if len(text) == 10 else ""
    except ValueError:
        return ""


def date_start_ts(value: str) -> int:
    """Günün başlangıcı (UTC) saniye olarak; boşsa 0."""
    import calendar
    import datetime

    text = clean_date(value)
    if not text:
        return 0
    return calendar.timegm(datetime.date.fromisoformat(text).timetuple())


def date_end_ts(value: str) -> int:
    """Günün sonu (UTC) saniye olarak; boşsa 0 (sınır yok)."""
    start = date_start_ts(value)
    return start + 86400 - 1 if start else 0


def settings_path() -> Path:
    return app_data_dir() / "ayarlar.json"


def load_settings() -> Dict[str, Any]:
    settings = dict(DEFAULT_SETTINGS)
    try:
        stored = json.loads(settings_path().read_text(encoding="utf-8"))
        if isinstance(stored, dict):
            settings.update({k: stored[k] for k in DEFAULT_SETTINGS if k in stored})
    except (OSError, ValueError):
        pass
    return clean_settings(settings)


def clean_settings(settings: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(DEFAULT_SETTINGS)
    out_dir = str(settings.get("out_dir") or "").strip()
    out["out_dir"] = out_dir or str(default_download_dir())
    if settings.get("content") in CONTENT_CHOICES:
        out["content"] = settings["content"]
    try:
        limit = int(settings.get("limit") or 0)
    except (TypeError, ValueError):
        limit = 0
    out["limit"] = max(0, min(limit, 100000))
    for key in ("posts", "stories", "highlights", "avatar", "reels", "reposts", "tagged", "only_new", "history",
                "auto_continue", "verify_files", "info_csv", "setup_done", "no_confirm"):
        out[key] = bool(settings.get(key, DEFAULT_SETTINGS[key]))
    try:
        caption_min = int(settings.get("caption_min", DEFAULT_SETTINGS["caption_min"]))
    except (TypeError, ValueError):
        caption_min = DEFAULT_SETTINGS["caption_min"]
    out["caption_min"] = max(0, min(caption_min, CAPTION_MIN_MAX))
    out["date_from"] = clean_date(settings.get("date_from"))
    out["date_to"] = clean_date(settings.get("date_to"))
    out["identity"] = settings.get("identity") if settings.get("identity") in IDENTITY_CHOICES else "desktop"
    out["speed"] = settings.get("speed") if settings.get("speed") in SPEED_CHOICES else "normal"
    lang = str(settings.get("lang") or "auto")
    from .i18n import available_languages
    out["lang"] = lang if lang in {item["code"] for item in available_languages()} else "auto"
    return out


def save_settings(settings: Dict[str, Any]) -> None:
    try:
        settings_path().write_text(json.dumps(clean_settings(settings), ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass
