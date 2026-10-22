"""Arayüzü sunan küçük yerel sunucu (sadece bu bilgisayardan erişilebilir)."""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional

from . import APP_NAME, __version__, i18n, links, report
from .i18n import msg
from .engine import MODE_COOKIE, MODE_LOGIN, MODE_PROFILE, MODE_SINGLE, Engine, Job, load_session_state
from .media import author_folder
from .paths import app_data_dir, clean_settings, load_settings, save_settings


def resource_dir() -> Path:
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base) / "instagram_indirici"
    return Path(__file__).resolve().parent


class AppState:
    def __init__(self) -> None:
        self.engine = Engine()
        self.settings = load_settings()
        i18n.set_language(self.settings["lang"])
        self.last_poll = 0.0
        self.port = 0


def choose_folder_windows(initial: str) -> Optional[str]:
    script = (
        "[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
        "Add-Type -AssemblyName System.Windows.Forms;"
        "$o=New-Object System.Windows.Forms.Form -Property @{TopMost=$true;ShowInTaskbar=$false};"
        "$f=New-Object System.Windows.Forms.FolderBrowserDialog;"
        f"$f.Description={json.dumps(i18n.translate('server.folder_dialog_title'))};"
        "$f.ShowNewFolderButton=$true;"
        f"$f.SelectedPath={json.dumps(initial)};"
        "if($f.ShowDialog($o) -eq 'OK'){Write-Output $f.SelectedPath}"
    )
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass", "-Command", script],
            capture_output=True, timeout=600,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        value = out.stdout.decode("utf-8", "replace").strip()
        return value or None
    except Exception:
        return None


def open_folder(path: str) -> bool:
    target = Path(path)
    while not target.exists() and target != target.parent:
        target = target.parent
    try:
        if sys.platform == "win32":
            os.startfile(str(target))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(target)])
        else:
            subprocess.Popen(["xdg-open", str(target)])
        return True
    except Exception:
        return False


# ---------------------------------------------------------------- bakım işleri (Gelişmiş panel)
_CACHE_DIRS = ("Cache", "Code Cache", "GPUCache", "DawnCache", "GrShaderCache", "ShaderCache")


def browser_profiles() -> list:
    """Programın Instagram için kullandığı tarayıcı profilleri (arayüz penceresinin profiline dokunulmaz)."""
    base = app_data_dir()
    try:
        return [p for p in base.iterdir() if p.is_dir() and p.name.startswith("tarayici-")]
    except OSError:
        return []


def clear_cache() -> int:
    """Tarayıcı önbelleğini siler (oturum ve çerezlere dokunmaz). Silinen klasör sayısını döndürür."""
    n = 0
    for profile in browser_profiles():
        for root in (profile, profile / "Default"):
            for name in _CACHE_DIRS:
                target = root / name
                if target.is_dir():
                    shutil.rmtree(target, ignore_errors=True)
                    n += 1
    return n


def clear_cookies() -> int:
    """Instagram oturumunu ve site verisini siler (profil klasörleri). Çıkış yapılmış olur."""
    n = 0
    for profile in browser_profiles():
        shutil.rmtree(profile, ignore_errors=True)
        n += 1
    return n


def _no_open() -> bool:
    """Testlerde pencere/pano açılmasın (INSTAGRAM_INDIRICI_NO_OPEN=1)."""
    return os.environ.get("INSTAGRAM_INDIRICI_NO_OPEN") == "1"


def copy_to_clipboard(text: str) -> bool:
    if sys.platform != "win32" or _no_open():
        return False
    script = ("[Console]::InputEncoding=[Text.Encoding]::UTF8;"
              "$t=[Console]::In.ReadToEnd(); Set-Clipboard -Value $t")
    try:
        done = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
                              input=text.encode("utf-8"), capture_output=True, timeout=30,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return done.returncode == 0
    except Exception:
        return False


def reveal_file(path: Path) -> bool:
    if _no_open():
        return True
    try:
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", str(path)])
            return True
    except Exception:
        return False
    return open_folder(str(path.parent))


def open_network_settings() -> bool:
    if _no_open():
        return sys.platform == "win32"
    try:
        if sys.platform == "win32":
            os.startfile("ms-settings:network")  # type: ignore[attr-defined]
            return True
    except Exception:
        pass
    return False


def report_text(state: "AppState") -> str:
    st = state.settings
    info = [
        ("Sürüm", __version__),
        ("Zaman", datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z")),
        ("Bilgisayar", platform.platform()),
        ("Tarayıcı", state.engine.browser_name or "bilinmiyor"),
        ("Kimlik", str(st.get("identity"))),
        ("Hız modu", str(st.get("speed"))),
        ("Dil", i18n.current_language()),
    ]
    return report.build(info, st, state.engine.snapshot(0), i18n.current_language())


def save_report(text: str) -> Dict[str, Any]:
    """Raporu program klasörüne dosya olarak kaydeder ve gösterir (panoya kopyalanamadığında ya da "paylaş" için)."""
    path = app_data_dir() / f"rapor-{datetime.now().strftime('%Y%m%d-%H%M%S')}.txt"
    try:
        path.write_text(text, encoding="utf-8")
    except OSError as exc:
        return {"error": msg("server.report_failed", error=str(exc)).to_json()}
    reveal_file(path)
    return {"ok": True, "message": msg("server.report_saved", path=str(path)).to_json()}


def history_overview(state: "AppState") -> Dict[str, Any]:
    """Geçmiş sekmesi: indirilen profiller ve klasörleri."""
    out_dir = Path(str(state.settings.get("out_dir") or ""))
    try:
        from .history import History
        h = History()
        rows = h.profiles()
        h.close()
    except Exception:
        rows = []
    profiles = []
    for r in rows:
        folder = out_dir / author_folder(r["username"]) if r["username"] else None
        profiles.append({**r, "folder": str(folder) if folder else "", "exists": bool(folder and folder.exists())})
    return {"profiles": profiles, "total": sum(p["count"] for p in profiles)}


def make_handler(state: AppState):
    index_path = resource_dir() / "ui" / "index.html"

    class Handler(BaseHTTPRequestHandler):
        server_version = "InstagramIndirici"

        def log_message(self, *args) -> None:  # sessiz
            pass

        # ---- güvenlik: sadece bu bilgisayardan, sadece bizim arayüzden
        def _host_ok(self) -> bool:
            host = (self.headers.get("Host") or "").split(":")[0]
            return host in ("127.0.0.1", "localhost")

        def _origin_ok(self) -> bool:
            origin = self.headers.get("Origin")
            if not origin:
                return True
            return origin.split("://", 1)[-1].split(":")[0] in ("127.0.0.1", "localhost")

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, data: Any, code: int = 200) -> None:
            self._send(code, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def _read_json(self) -> Dict[str, Any]:
            if "application/json" not in (self.headers.get("Content-Type") or ""):
                raise ValueError("json bekleniyordu")
            length = int(self.headers.get("Content-Length") or 0)
            if length > 100_000:
                raise ValueError("too large")
            data = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(data, dict):
                raise ValueError("invalid")
            return data

        def do_GET(self) -> None:
            if not self._host_ok():
                return self._send(403, b"forbidden", "text/plain")
            path = self.path.split("?", 1)[0]
            if path in ("/", "/index.html"):
                try:
                    body = index_path.read_bytes()
                except OSError:
                    return self._send(500, b"index.html missing", "text/plain; charset=utf-8")
                return self._send(200, body, "text/html; charset=utf-8")
            if path == "/api/strings":
                lang = i18n.current_language()
                return self._json({"lang": lang, "setting": state.settings.get("lang", "auto"),
                                   "languages": i18n.available_languages(),
                                   "platform": "desktop", "strings": i18n.strings_for(lang)})
            if path == "/api/ping":
                return self._json({"app": "instagram-indirici", "version": __version__})
            if path == "/api/state":
                state.last_poll = time.monotonic()
                since = 0
                if "since=" in self.path:
                    try:
                        since = int(self.path.split("since=", 1)[1].split("&")[0])
                    except ValueError:
                        since = 0
                snap = state.engine.snapshot(since)
                snap["settings"] = state.settings
                if snap.get("logged_in") is None:
                    snap["logged_in"] = load_session_state().get("logged_in")
                snap["version"] = __version__
                snap["app_name"] = APP_NAME
                return self._json(snap)
            if path == "/api/history-list":
                return self._json(history_overview(state))
            return self._send(404, b"not found", "text/plain")

        def do_POST(self) -> None:
            if not self._host_ok() or not self._origin_ok():
                return self._send(403, b"forbidden", "text/plain")
            try:
                data = self._read_json()
            except Exception:
                return self._json({"error": msg("server.bad_request").to_json()}, 400)
            path = self.path.split("?", 1)[0]

            if path == "/api/parse":
                parsed = links.parse_link(str(data.get("link") or ""))
                return self._json({"ok": parsed.ok, "kind": parsed.kind, "message": parsed.describe().to_json(),
                                   "username": parsed.username})

            if path == "/api/settings":
                state.settings = clean_settings({**state.settings, **data})
                save_settings(state.settings)
                i18n.set_language(state.settings["lang"])
                return self._json({"settings": state.settings})

            if path == "/api/start":
                mode = data.get("mode")
                if mode not in (MODE_SINGLE, MODE_PROFILE):
                    return self._json({"error": msg("server.choose_mode").to_json()}, 400)
                state.settings = clean_settings({**state.settings, **(data.get("settings") or {})})
                save_settings(state.settings)
                error = state.engine.start(Job(str(data.get("link") or ""), mode, state.settings))
                if error:
                    return self._json({"error": error.to_json()}, 400)
                return self._json({"ok": True})

            if path == "/api/login":
                error = state.engine.start(Job("", MODE_LOGIN, state.settings))
                if error:
                    return self._json({"error": error.to_json()}, 400)
                return self._json({"ok": True})

            if path == "/api/login-cookie":
                error = state.engine.start(Job(str(data.get("cookie") or ""), MODE_COOKIE, state.settings))
                if error:
                    return self._json({"error": error.to_json()}, 400)
                return self._json({"ok": True})

            if path == "/api/clear-history":
                if state.engine.running:
                    return self._json({"error": msg("engine.already_running").to_json()}, 400)
                try:
                    from .history import History
                    h = History()
                    n = h.clear()
                    h.close()
                except Exception as exc:
                    return self._json({"error": msg("engine.history_failed", error=str(exc)).to_json()}, 500)
                return self._json({"ok": True, "message": msg("server.history_cleared", n=n).to_json()})

            if path == "/api/history-forget":
                if state.engine.running:
                    return self._json({"error": msg("engine.already_running").to_json()}, 400)
                try:
                    from .history import History
                    h = History()
                    n = h.forget(str(data.get("user_id") or ""))
                    h.close()
                except Exception as exc:
                    return self._json({"error": msg("engine.history_failed", error=str(exc)).to_json()}, 500)
                return self._json({"ok": True, "message": msg("server.history_cleared", n=n).to_json()})

            if path == "/api/report-copy":
                text = report_text(state)
                if copy_to_clipboard(text):
                    return self._json({"ok": True, "message": msg("server.report_copied", n=text.count("\n")).to_json()})
                return self._json(save_report(text))

            if path == "/api/report-share":
                return self._json(save_report(report_text(state)))

            if path == "/api/clear-cache":
                if state.engine.running:
                    return self._json({"error": msg("engine.already_running").to_json()}, 400)
                clear_cache()
                return self._json({"ok": True, "message": msg("server.cache_cleared").to_json()})

            if path == "/api/clear-cookies":
                if state.engine.running:
                    return self._json({"error": msg("engine.already_running").to_json()}, 400)
                clear_cookies()
                from .engine import save_session_state
                save_session_state(False)
                state.engine.logged_in = False
                return self._json({"ok": True, "message": msg("server.cookies_cleared").to_json()})

            if path == "/api/network-settings":
                ok = open_network_settings()
                if not ok:
                    return self._json({"error": msg("server.no_network_settings").to_json()}, 400)
                return self._json({"ok": True})

            if path == "/api/stop":
                state.engine.stop()
                return self._json({"ok": True})

            if path == "/api/pause":
                state.engine.pause()
                return self._json({"ok": True})

            if path == "/api/resume":
                state.engine.resume()
                return self._json({"ok": True})

            if path == "/api/choose-folder":
                if sys.platform != "win32":
                    return self._json({"error": msg("server.no_folder_dialog").to_json()}, 400)
                chosen = choose_folder_windows(state.settings.get("out_dir", ""))
                if chosen:
                    state.settings = clean_settings({**state.settings, "out_dir": chosen})
                    save_settings(state.settings)
                return self._json({"settings": state.settings, "changed": bool(chosen)})

            if path == "/api/open-folder":
                target = str(data.get("path") or state.settings.get("out_dir") or "")
                if not target:
                    return self._json({"error": msg("server.no_folder").to_json()}, 400)
                if target == state.settings.get("out_dir"):
                    try:
                        Path(target).mkdir(parents=True, exist_ok=True)
                    except OSError:
                        pass
                return self._json({"ok": open_folder(target)})

            return self._json({"error": msg("server.unknown").to_json()}, 404)

    return Handler


def start_server(state: AppState, port: int = 0) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(state))
    server.daemon_threads = True
    state.port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
