"""Programın kendi verisini sıfırlar: yeni sürümde ilk açılışta ve "Her şeyi sıfırla" düğmesinde.

Dokunulmayanlar: kullanıcının indirdiği dosyalar (bu klasörde değildir), indirme klasörü ayarı, Chrome'a kurulan
eklentinin klasörü (Chrome eklentiyi oradan okur), indirilmiş tarayıcı bileşeni ve çalışan örneğin kilit dosyası.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Set

from . import __version__
from .paths import app_data_dir, settings_path

KEEP: Set[str] = {"chrome-eklenti", "tarayici-bileseni", "calisan.json", ".surum"}
IN_USE_PREFIX = "arayuz-penceresi-"   # açık arayüz penceresinin profili: çalışırken silinemez


def _marker() -> Path:
    return app_data_dir() / ".surum"


def reset_app_data(*, running: bool = False) -> int:
    """Veri klasöründeki her şeyi siler (yukarıdaki istisnalar hariç). Silinen öğe sayısını döndürür.

    Yalnızca indirme klasörü ayarı korunur: silinirse önceki indirilenler "zaten var" diye tanınmaz.
    """
    base = app_data_dir()
    out_dir = ""
    try:
        stored = json.loads(settings_path().read_text(encoding="utf-8"))
        if isinstance(stored, dict):
            out_dir = str(stored.get("out_dir") or "")
    except (OSError, ValueError):
        pass
    n = 0
    try:
        items = list(base.iterdir())
    except OSError:
        items = []
    for item in items:
        if item.name in KEEP or (running and item.name.startswith(IN_USE_PREFIX)):
            continue
        try:
            if item.is_dir() and not item.is_symlink():
                shutil.rmtree(item, ignore_errors=True)
            else:
                item.unlink()
            n += 1
        except OSError:
            pass
    if out_dir:
        try:
            settings_path().write_text(json.dumps({"out_dir": out_dir}, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
    return n


def reset_if_new_version() -> bool:
    """Program yeni bir sürümle ilk kez açılıyorsa eski veriyi siler. Sıfırlama yapıldıysa True döner."""
    base = app_data_dir()
    marker = _marker()
    try:
        previous = marker.read_text(encoding="utf-8").strip()
    except OSError:
        previous = ""
    did = False
    if previous != __version__:
        had_data = any(p.name not in KEEP for p in base.iterdir()) if base.exists() else False
        if had_data:
            reset_app_data()
            did = True
    try:
        marker.write_text(__version__, encoding="utf-8")
    except OSError:
        pass
    return did
