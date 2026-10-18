"""Bilgisayardaki tarayıcıları bulur, sağlam olup olmadıklarını kontrol eder ve
gerekirse programın kendi tarayıcı bileşenini (Chromium) bir kereye mahsus indirir.

Neden gerekli: Edge'in güncellemesi yarıda kalınca klasöründen dosya eksilebiliyor
(ör. "msedge_elf.dll bulunamadı" hatası). Böyle bir durumda program Edge'e
takılıp kalmamalı; Chrome'u ya da kendi indirdiği tarayıcıyı kullanmalı.
"""

from __future__ import annotations

import glob
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable, List, Optional

from .i18n import msg
from .paths import app_data_dir

_VERSION_DIR = re.compile(r"^\d+\.\d+\.\d+\.\d+$")


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def suppress_windows_error_dialogs() -> None:
    """Eksik DLL gibi sistem hatalarında Windows'un açtığı kutuları kapatır.

    Alt süreçler (Edge vb.) bu ayarı devralır; bozuk bir tarayıcı kullanıcıya
    hata kutusu göstermek yerine sessizce başarısız olur ve program
    yedek tarayıcıya geçer.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        SEM_FAILCRITICALERRORS, SEM_NOGPFAULTERRORBOX, SEM_NOOPENFILEERRORBOX = 0x0001, 0x0002, 0x8000
        ctypes.windll.kernel32.SetErrorMode(SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX | SEM_NOOPENFILEERRORBOX)
    except Exception:
        pass


def browser_healthy(exe: Optional[str]) -> bool:
    """Chromium tabanlı bir tarayıcının kurulumu tam mı?

    Edge/Chrome, exe'nin yanındaki sürüm klasöründen <ad>_elf.dll yükler.
    Bu dosya eksikse tarayıcı hiç açılmaz.
    """
    if not exe:
        return False
    path = Path(exe)
    if not path.is_file():
        return False
    if sys.platform != "win32" and not os.environ.get("INSTAGRAM_INDIRICI_CHECK_ELF"):
        return True
    elf_name = f"{path.stem}_elf.dll"
    version_dirs = [d for d in path.parent.iterdir() if d.is_dir() and _VERSION_DIR.match(d.name)]
    if not version_dirs:
        return False
    return all((d / elf_name).is_file() for d in version_dirs)


def _program_dirs() -> List[Path]:
    out = []
    for env in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA"):
        base = os.environ.get(env)
        if base:
            out.append(Path(base))
    return out


def find_edge() -> Optional[str]:
    if sys.platform != "win32" or os.environ.get("INSTAGRAM_INDIRICI_FORCE_BUNDLED"):
        return None
    for base in _program_dirs():
        exe = base / "Microsoft" / "Edge" / "Application" / "msedge.exe"
        if exe.is_file():
            return str(exe)
    return None


def find_chrome() -> Optional[str]:
    if sys.platform != "win32" or os.environ.get("INSTAGRAM_INDIRICI_FORCE_BUNDLED"):
        return None
    for base in _program_dirs():
        exe = base / "Google" / "Chrome" / "Application" / "chrome.exe"
        if exe.is_file():
            return str(exe)
    return None


def healthy_edge() -> Optional[str]:
    exe = find_edge()
    return exe if browser_healthy(exe) else None


def healthy_chrome() -> Optional[str]:
    exe = find_chrome()
    return exe if browser_healthy(exe) else None


# ------------------------------------------------------------ kendi tarayıcımız

def bundled_browsers_dir() -> Path:
    return app_data_dir() / "tarayici-bileseni"


def prepare_playwright_env() -> None:
    """Playwright'ın tarayıcıyı nereye indireceğini/nerede arayacağını ayarlar.

    Derlenmiş programda Playwright varsayılan olarak geçici klasörü kullanır
    (her açılışta silinir); bu yüzden kalıcı bir klasör veriyoruz.
    """
    if is_frozen() or not os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(bundled_browsers_dir())


def bundled_chromium_exe() -> Optional[str]:
    base = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or bundled_browsers_dir())
    patterns = [
        "chromium-*/chrome-win*/chrome.exe",
        "chromium-*/chrome-linux*/chrome",
        "chromium-*/chrome-mac*/Chromium.app/Contents/MacOS/Chromium",
    ]
    for pattern in patterns:
        hits = sorted(glob.glob(str(base / pattern)))
        if hits:
            return hits[-1]
    return None


def install_bundled_chromium(log: Callable[[str, str], None]) -> bool:
    """Playwright'ın Chromium'unu programın klasörüne indirir (bir kereye mahsus, ~150 MB)."""
    prepare_playwright_env()
    try:
        from playwright._impl._driver import compute_driver_executable, get_driver_env
    except Exception as exc:
        log("error", msg("chromium.failed", detail=str(exc)))
        return False
    node, cli = compute_driver_executable()
    env = get_driver_env()
    env["PLAYWRIGHT_BROWSERS_PATH"] = os.environ["PLAYWRIGHT_BROWSERS_PATH"]
    log("info", msg("chromium.downloading"))
    try:
        result = subprocess.run(
            [node, cli, "install", "chromium"],
            env=env, capture_output=True, timeout=1800,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as exc:
        log("error", msg("chromium.failed", detail=str(exc)))
        return False
    if result.returncode != 0:
        tail = (result.stderr or result.stdout or b"").decode("utf-8", "replace").strip().splitlines()[-3:]
        log("error", msg("chromium.failed", detail=" ".join(tail)))
        return False
    log("ok", msg("chromium.done"))
    return True
