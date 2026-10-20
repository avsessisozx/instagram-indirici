"""Dosyaları parça parça indirir (sadece Python'un kendi kütüphaneleriyle)."""

from __future__ import annotations

import os
import shutil
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .i18n import Msg, msg

CHUNK = 256 * 1024
TIMEOUT = 30

_CONTENT_EXT = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/heic": ".heic",
    "image/heif": ".heic",
    "image/avif": ".avif",
    "image/gif": ".gif",
    "video/mp4": ".mp4",
}
KNOWN_EXTS = (".mp4", ".jpg", ".jpeg", ".png", ".webp", ".heic", ".avif", ".gif")


class Cancelled(Exception):
    pass


@dataclass
class FetchResult:
    ok: bool
    path: Optional[Path] = None
    size: int = 0
    error: Optional[Msg] = None
    status: int = 0          # son HTTP durum kodu (403 ise tarayıcıyla denenebilir)


def existing_file(stem_path: Path, ext: str = "") -> Optional[Path]:
    """Daha önce inmiş (boş olmayan) dosyayı bulur."""
    candidates = [stem_path.with_name(stem_path.name + ext)] if ext else [
        stem_path.with_name(stem_path.name + e) for e in KNOWN_EXTS
    ]
    for path in candidates:
        try:
            if path.is_file() and path.stat().st_size > 0:
                return path
        except OSError:
            continue
    return None


def ext_from_content_type(content_type: str, fallback: str) -> str:
    base = (content_type or "").split(";")[0].strip().lower()
    return _CONTENT_EXT.get(base, fallback)


def _ssl_context() -> ssl.SSLContext:
    return ssl.create_default_context()


def fetch(
    urls: List[str],
    stem_path: Path,
    *,
    ext: str,
    fallback_ext: str,
    headers: Dict[str, str],
    cancel: threading.Event,
    on_progress: Optional[Callable[[int, int], None]] = None,
    attempts_per_url: int = 2,
) -> FetchResult:
    """URL listesini sırayla dener; ilk başarılı indirmeyi kaydeder."""
    stem_path.parent.mkdir(parents=True, exist_ok=True)
    last = FetchResult(False, error=msg("fetch.no_url"))
    ctx = _ssl_context()
    for url in urls:
        for _attempt in range(attempts_per_url):
            if cancel.is_set():
                raise Cancelled()
            result = _fetch_one(url, stem_path, ext, fallback_ext, headers, cancel, on_progress, ctx)
            if result.ok:
                return result
            last = result
            if result.status in (403, 404, 410):
                break  # bu adres işe yaramıyor, sonrakine geç
    return last


def _fetch_one(url, stem_path, ext, fallback_ext, headers, cancel, on_progress, ctx) -> FetchResult:
    part = stem_path.with_name(stem_path.name + ".part")
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT, context=ctx) as response:
            status = getattr(response, "status", 200)
            content_type = response.headers.get("Content-Type", "")
            if content_type.lower().startswith(("text/html", "application/json", "text/plain")):
                return FetchResult(False, error=msg("fetch.html_page"), status=403)
            total = int(response.headers.get("Content-Length") or 0)
            final_ext = ext or ext_from_content_type(content_type, fallback_ext)
            done = 0
            with open(part, "wb") as fh:
                while True:
                    if cancel.is_set():
                        raise Cancelled()
                    chunk = response.read(CHUNK)
                    if not chunk:
                        break
                    fh.write(chunk)
                    done += len(chunk)
                    if on_progress:
                        on_progress(done, total)
            if done == 0:
                _remove(part)
                return FetchResult(False, error=msg("fetch.empty"), status=status)
            if total and done < total:
                _remove(part)
                return FetchResult(False, error=msg("fetch.incomplete"), status=status)
            final = stem_path.with_name(stem_path.name + final_ext)
            os.replace(part, final)
            return FetchResult(True, final, done, status=status)
    except Cancelled:
        _remove(part)
        raise
    except urllib.error.HTTPError as exc:
        _remove(part)
        return FetchResult(False, error=msg("fetch.http", code=exc.code), status=exc.code)
    except (urllib.error.URLError, TimeoutError, ConnectionError, ssl.SSLError) as exc:
        _remove(part)
        reason = getattr(exc, "reason", exc)
        return FetchResult(False, error=msg("fetch.connection", reason=str(reason)))
    except OSError as exc:
        _remove(part)
        return FetchResult(False, error=msg("fetch.write", error=str(exc)))


def write_bytes(data: bytes, stem_path: Path, ext: str) -> FetchResult:
    stem_path.parent.mkdir(parents=True, exist_ok=True)
    final = stem_path.with_name(stem_path.name + ext)
    part = stem_path.with_name(stem_path.name + ".part")
    try:
        with open(part, "wb") as fh:
            fh.write(data)
        os.replace(part, final)
    except OSError as exc:
        _remove(part)
        return FetchResult(False, error=msg("fetch.write", error=str(exc)))
    return FetchResult(True, final, len(data))


def _remove(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


# ------------------------------------------------------------ yüksek kalite (DASH): ayrı ses + görüntü
def find_ffmpeg() -> Optional[str]:
    """ffmpeg'in yolu: ortam değişkeni, programla gelen kopya, sonra sistemdeki."""
    env = os.environ.get("INSTAGRAM_INDIRICI_FFMPEG")
    if env and Path(env).is_file():
        return env
    try:
        import imageio_ffmpeg  # type: ignore
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and Path(exe).is_file():
            return exe
    except Exception:
        pass
    return shutil.which("ffmpeg")


def _mux(ffmpeg: str, video: Path, audio: Optional[Path], out: Path, cancel: threading.Event) -> Optional[str]:
    """Akışları yeniden kodlamadan tek mp4'e birleştirir. Hata olursa açıklamasını döndürür."""
    cmd = [ffmpeg, "-y", "-v", "error", "-i", str(video)]
    if audio is not None:
        cmd += ["-i", str(audio), "-map", "0:v:0", "-map", "1:a:0"]
    cmd += ["-c", "copy", "-movflags", "+faststart", "-f", "mp4", str(out)]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, creationflags=flags)
    except OSError as exc:
        return str(exc)
    deadline = time.monotonic() + 300
    while True:
        try:
            _out, err = proc.communicate(timeout=0.5)
            break
        except subprocess.TimeoutExpired:
            if cancel.is_set() or time.monotonic() > deadline:
                proc.kill()
                proc.communicate()
                if cancel.is_set():
                    raise Cancelled()
                return "timeout"
    if proc.returncode != 0:
        return (err or b"").decode("utf-8", "replace").strip()[-300:] or f"ffmpeg {proc.returncode}"
    try:
        if not out.is_file() or out.stat().st_size == 0:
            return "empty output"
    except OSError as exc:
        return str(exc)
    return None


def fetch_dash(
    video_url: str,
    audio_url: str,
    stem_path: Path,
    *,
    headers: Dict[str, str],
    cancel: threading.Event,
    on_progress: Optional[Callable[[int, int], None]] = None,
    ffmpeg: Optional[str] = None,
) -> FetchResult:
    """Görüntü ve sesi ayrı indirip tek .mp4'e birleştirir. Başarısızsa geçici dosyaları siler."""
    exe = ffmpeg or find_ffmpeg()
    if not exe:
        return FetchResult(False, error=msg("fetch.no_ffmpeg"))
    stem_path.parent.mkdir(parents=True, exist_ok=True)
    temps: List[Path] = []
    out = stem_path.with_name(stem_path.name + ".mux")
    try:
        got_video = fetch([video_url], stem_path.with_name(stem_path.name + ".dv"), ext=".tmp", fallback_ext=".tmp",
                          headers=headers, cancel=cancel, on_progress=on_progress)
        if not got_video.ok or got_video.path is None:
            return got_video
        temps.append(got_video.path)
        audio_path: Optional[Path] = None
        if audio_url:
            got_audio = fetch([audio_url], stem_path.with_name(stem_path.name + ".da"), ext=".tmp", fallback_ext=".tmp",
                              headers=headers, cancel=cancel, on_progress=on_progress)
            if not got_audio.ok or got_audio.path is None:
                return got_audio
            temps.append(got_audio.path)
            audio_path = got_audio.path
        problem = _mux(exe, got_video.path, audio_path, out, cancel)
        if problem:
            return FetchResult(False, error=msg("fetch.mux_failed", error=problem))
        final = stem_path.with_name(stem_path.name + ".mp4")
        os.replace(out, final)
        return FetchResult(True, final, final.stat().st_size)
    finally:
        for path in temps + [out]:
            _remove(path)
