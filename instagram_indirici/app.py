"""Programın giriş noktası: sunucuyu başlatır ve arayüz penceresini açar."""

from __future__ import annotations

import argparse
import json
import os
import threading
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from . import __version__
from . import i18n, winbrowsers
from .paths import app_data_dir
from .server import AppState, start_server

HEARTBEAT_TIMEOUT = 45
WINDOW_WAIT = 20


def say(text: str) -> None:
    """Konsol yoksa ya da Türkçe karakter yazamıyorsa sessizce geç."""
    try:
        print(text, flush=True)
    except Exception:
        pass


def _instance_file() -> Path:
    return app_data_dir() / "calisan.json"


def _existing_instance() -> Optional[int]:
    try:
        port = int(json.loads(_instance_file().read_text(encoding="utf-8"))["port"])
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ping", timeout=2) as resp:
            if json.loads(resp.read()).get("app") == "instagram-indirici":
                return port
    except Exception:
        return None
    return None


def _app_window(exe: str, url: str, name: str) -> Optional[subprocess.Popen]:
    profile = app_data_dir() / f"arayuz-penceresi-{name}"
    cmd = [exe, f"--app={url}", f"--user-data-dir={profile}", "--window-size=1000,900",
           "--no-first-run", "--no-default-browser-check", "--disable-features=Translate"]
    if sys.platform.startswith("linux") and hasattr(os, "geteuid") and os.geteuid() == 0:
        cmd.append("--no-sandbox")  # sadece geliştirme ortamı (Linux, root)
    try:
        return subprocess.Popen(cmd)
    except OSError:
        return None


def _message_box(text: str) -> None:
    """Pencere açılamazken kullanıcıyı bilgilendirmek için Windows bilgi kutusu."""
    if sys.platform != "win32":
        say(text)
        return

    def show() -> None:
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, text, i18n.translate("ui.app_title"), 0x40 | 0x40000)
        except Exception:
            pass

    threading.Thread(target=show, daemon=True).start()


def window_launchers(url: str) -> List[Tuple[str, Callable[[], Tuple[bool, Optional[subprocess.Popen]]]]]:
    """Arayüz penceresini açmanın yolları, en iyiden en son çareye."""

    def with_exe(finder: Callable[[], Optional[str]], name: str):
        def launch():
            exe = finder()
            if not exe:
                return False, None
            proc = _app_window(exe, url, name)
            return proc is not None, proc
        return launch

    def default_browser():
        if os.environ.get("INSTAGRAM_INDIRICI_FORCE_BUNDLED"):
            return False, None
        try:
            return bool(webbrowser.open(url)), None
        except Exception:
            return False, None

    def download_then_open():
        _message_box(i18n.translate("app.edge_broken_box"))
        if not winbrowsers.install_bundled_chromium(lambda level, text: say(str(text))):
            _message_box(i18n.translate("app.download_failed_box"))
            return False, None
        return with_exe(winbrowsers.bundled_chromium_exe, "chromium")()

    return [
        ("edge", with_exe(winbrowsers.healthy_edge, "edge")),
        ("chrome", with_exe(winbrowsers.healthy_chrome, "chrome")),
        ("chromium", with_exe(winbrowsers.bundled_chromium_exe, "chromium")),
        ("default", default_browser),
        ("download", download_then_open),
    ]


def open_window(url: str) -> Optional[subprocess.Popen]:
    """Var olan bir kopyanın penceresini yeniden açmak için basit yol."""
    for _name, launch in window_launchers(url)[:4]:
        ok, proc = launch()
        if ok:
            return proc
    return None


def self_test() -> str:
    """Derlenmiş programın çalıştığını doğrular (GitHub derlemesinde kullanılır)."""
    import asyncio
    import threading

    from .browser import InstagramBrowser

    state = AppState()
    server = start_server(state)
    with urllib.request.urlopen(f"http://127.0.0.1:{state.port}/", timeout=5) as resp:
        html = resp.read().decode("utf-8")
    assert "data-i18n" in html, "UI did not load"
    with urllib.request.urlopen(f"http://127.0.0.1:{state.port}/api/strings", timeout=5) as resp:
        strings = json.loads(resp.read().decode("utf-8"))["strings"]
    assert strings.get("ui.start"), "translations did not load"

    async def check_browser() -> str:
        os.environ.setdefault("INSTAGRAM_INDIRICI_HEADLESS", "1")
        async with InstagramBrowser(app_data_dir(), log=lambda *a: None, cancel=threading.Event(),
                                    on_verify=lambda v: None) as browser:
            return browser.channel_name

    name = asyncio.run(check_browser())
    server.shutdown()
    say(f"SELF-TEST OK: ui + browser ({name})")
    return name


def self_test_window() -> str:
    """Arayüz penceresinin gerçekten açılıp sunucuya bağlandığını dener."""
    state = AppState()
    server = start_server(state)
    url = f"http://127.0.0.1:{state.port}/"
    try:
        for name, launch in window_launchers(url):
            started = time.monotonic()
            ok, proc = launch()
            if not ok:
                continue
            while time.monotonic() - started < 40:
                if state.last_poll > started:
                    if proc is not None and proc.poll() is None:
                        proc.kill()
                    say(f"SELF-TEST WINDOW OK: {name}")
                    return name
                time.sleep(0.5)
            if proc is not None and proc.poll() is None:
                proc.kill()
        raise RuntimeError("UI window could not be opened in any way")
    finally:
        server.shutdown()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="InstagramIndirici")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--self-test-window", action="store_true")
    parser.add_argument("--no-window", action="store_true")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args(argv)
    winbrowsers.suppress_windows_error_dialogs()
    winbrowsers.prepare_playwright_env()

    if args.self_test or args.self_test_window:
        # Pencereli derlemede hata kutusu açılıp beklemesin: sonucu dosyaya yaz.
        report = Path(os.environ.get("INSTAGRAM_INDIRICI_SELFTEST_OUT", "selftest.txt"))
        try:
            detail = self_test_window() if args.self_test_window else self_test()
            report.write_text(f"OK {detail}", encoding="utf-8")
            return 0
        except BaseException:
            import traceback
            report.write_text("FAIL\n" + traceback.format_exc(), encoding="utf-8")
            return 1

    existing = _existing_instance()
    if existing and not args.no_window:
        open_window(f"http://127.0.0.1:{existing}/")
        return 0

    state = AppState()
    server = start_server(state, args.port)
    url = f"http://127.0.0.1:{state.port}/"
    try:
        _instance_file().write_text(json.dumps({"port": state.port, "pid": os.getpid()}), encoding="utf-8")
    except OSError:
        pass
    say(f"Instagram Indirici: {url}")

    launchers = [] if args.no_window else window_launchers(url)
    window: Optional[subprocess.Popen] = None
    launched_at = 0.0
    ui_seen = False
    watch_process = False

    def next_window() -> bool:
        nonlocal window, launched_at
        while launchers:
            _name, launch = launchers.pop(0)
            ok, proc = launch()
            if ok:
                window, launched_at = proc, time.monotonic()
                return True
        return False

    if not args.no_window and not next_window():
        _message_box(i18n.translate("app.window_failed_box", url=url))
        launched_at = time.monotonic()
    try:
        while True:
            time.sleep(1)
            if args.no_window:
                continue
            now = time.monotonic()
            if not ui_seen:
                if state.last_poll > launched_at:
                    ui_seen = True
                    # Pencere kendi sürecinde açıldıysa o süreç kapanınca programı kapat.
                    watch_process = window is not None and window.poll() is None
                    continue
                failed_fast = window is not None and window.poll() not in (None, 0)
                if failed_fast or now - launched_at > WINDOW_WAIT:
                    if not next_window():
                        if now - launched_at > HEARTBEAT_TIMEOUT * 4:
                            break  # hiçbir yol işe yaramadı
                continue
            if watch_process:
                if window.poll() is not None:
                    break  # pencere kapatıldı
            elif now - (state.last_poll or now) > HEARTBEAT_TIMEOUT and not state.engine.running:
                break
    except KeyboardInterrupt:
        pass
    finally:
        state.engine.stop()
        state.engine.wait(15)
        server.shutdown()
        try:
            _instance_file().unlink()
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
