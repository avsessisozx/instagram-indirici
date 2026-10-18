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
    "only_new": True,
    "lang": "auto",
}


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
    for key in ("posts", "stories", "highlights", "avatar", "only_new"):
        out[key] = bool(settings.get(key, DEFAULT_SETTINGS[key]))
    lang = str(settings.get("lang") or "auto")
    from .i18n import available_languages
    out["lang"] = lang if lang in {item["code"] for item in available_languages()} else "auto"
    return out


def save_settings(settings: Dict[str, Any]) -> None:
    try:
        settings_path().write_text(json.dumps(clean_settings(settings), ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass
