"""İndirme işini yürüten motor. Arayüz bu motorun durumunu okur."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import links
from .browser import (ApiError, BrowserError, FeedResult, InstagramBrowser, LoginRequired, NotFound,
                      ProfileInfo, RateLimited, parse_session_input)
from .fetcher import Cancelled, existing_file, ext_from_content_type, fetch, fetch_dash, write_bytes
from .history import History
from .i18n import Msg, msg
from .media import (KIND_AVATAR, KIND_HIGHLIGHT, KIND_POST, KIND_REEL, KIND_REPOST, KIND_STORY, KIND_TAGGED,
                    STORIES_DIR, MediaFile, Post, author_folder, build_avatar, build_post, highlight_folder,
                    is_pinned, owner_of, taken_at_of)
from .pacing import for_settings as pacing_for
from .paths import CONTENT_ALL, app_data_dir, clean_settings, date_end_ts, date_start_ts

MODE_SINGLE = "single"
MODE_PROFILE = "profile"
MODE_COOKIE = "cookie"
WORKERS = 3
UNMATCHED_REASONS = {"skip.video_only", "skip.image_only", "skip.image_only_avatar"}
MARKER_NAME = ".instagram-indirici.json"
INFO_NAME = "bilgi.csv"
INFO_HEADER = ["kod", "tür", "tarih", "bağlantı", "beğeni", "yorum", "dosyalar", "açıklama"]
SECTIONS = ("posts", "reels", "reposts", "tagged", "stories", "highlights", "avatar")
MAX_FAILURES = 60


@dataclass
class Job:
    link: str
    mode: str
    settings: Dict[str, Any]


def session_file() -> Path:
    return app_data_dir() / "oturum.json"


def load_session_state() -> Dict[str, Any]:
    try:
        data = json.loads(session_file().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_session_state(logged_in: bool) -> None:
    try:
        session_file().write_text(json.dumps({"logged_in": bool(logged_in), "checked": int(time.time())}),
                                  encoding="utf-8")
    except OSError:
        pass


# ------------------------------------------------------------ profil durum dosyası
def read_marker(folder: Path) -> Dict[str, Any]:
    try:
        data = json.loads((folder / MARKER_NAME).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_marker(folder: Path, data: Dict[str, Any]) -> None:
    path = folder / MARKER_NAME
    try:
        folder.mkdir(parents=True, exist_ok=True)
        if path.exists() and sys.platform == "win32":
            _set_hidden(path, False)   # gizli dosyanın üzerine yazmak için
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        if sys.platform == "win32":
            _set_hidden(path, True)
    except OSError:
        pass


def _set_hidden(path: Path, hidden: bool) -> None:
    try:
        import ctypes

        ctypes.windll.kernel32.SetFileAttributesW(str(path), 0x2 if hidden else 0x80)
    except Exception:
        pass


def known_newest(marker: Dict[str, Any], content: str) -> int:
    """Daha önce tamamı taranmış profilde en yeni gönderinin zamanı (yoksa 0)."""
    complete = marker.get("complete") if isinstance(marker.get("complete"), dict) else {}
    values = [complete.get(content), complete.get(CONTENT_ALL)]
    best = 0
    for v in values:
        try:
            best = max(best, int(v or 0))
        except (TypeError, ValueError):
            continue
    return best


def needs_repair(item: Dict[str, Any], post: Post) -> bool:
    """Eski biçimdeki listelerde video adresi bazen gelmiyor; gönderi tek başına açılınca geliyor."""
    return bool(post.skipped_reason and post.skipped_reason.key == "skip.no_video"
                and "video_versions" not in item and post.code)


def other_rounds() -> int:
    """Hikaye / öne çıkan / Reels gibi listelerde hız sınırında kaç kez bekleyip yeniden denenir."""
    try:
        return int(os.environ.get("INSTAGRAM_INDIRICI_OTHER_ROUNDS", "2"))
    except ValueError:
        return 2


def resume_wait() -> float:
    try:
        return float(os.environ.get("INSTAGRAM_INDIRICI_RESUME_WAIT", "900"))
    except ValueError:
        return 900.0


def max_rounds() -> int:
    try:
        return int(os.environ.get("INSTAGRAM_INDIRICI_RESUME_ROUNDS", "4"))
    except ValueError:
        return 4


class Engine:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._cancel = threading.Event()
        self.log_offset = 0
        self.logs: List[Dict[str, Any]] = []
        self.browser_name = ""          # son kullanılan tarayıcı (rapor için); durum sıfırlanınca silinmez
        self._reset()

    # ------------------------------------------------------------ durum
    def _reset(self) -> None:
        self.running = False
        self.phase = "idle"            # idle | starting | collecting | downloading | done | error | stopped
        self.mode = ""
        self.verify = False
        self.login_wall = False
        self.rate_limited = False
        self.logged_in: Optional[bool] = None
        self.author = ""
        self.folder = ""
        self.found_posts = 0
        self.posts_done = 0
        self.files_total = 0
        self.files_done = 0
        self.files_skipped = 0
        self.files_failed = 0
        self.posts_skipped = 0
        self.posts_unavailable = 0
        self.current = ""
        self.current_bytes = 0
        self.current_total = 0
        self.collecting = False
        self.summary: Optional[Msg] = None
        self.notes: List[Msg] = []
        self._pending_complete: Optional[tuple] = None
        self.posts_known = 0
        self.wait_until = 0.0
        self.wait_kind = ""
        self.history: Optional[History] = None
        self._use_history = True
        self._known: set = set()
        self._enqueued: set = set()
        self._pages: List[Dict[str, Any]] = []
        self._progress_ctx: Optional[tuple] = None   # (user_id, content, username) için imleç kaydı
        self._jumped = False
        self.started_at = 0.0
        self.paused = False
        self.posts_repaired = 0
        self.tabs: Dict[str, Dict[str, int]] = {}
        self.failures: List[Dict[str, str]] = []
        self._verify = True
        self.history_auto = True
        self._caption_min = 0
        self._out_root: Optional[Path] = None
        self._date_from = 0
        self._date_to = 0
        self._info_csv = True
        self._info_rows: Dict[str, Dict[str, List[str]]] = {}   # kişi klasörü -> kod -> satır
        self._failed_posts: List[Post] = []
        self.logs = []

    def log(self, level: str, message: Any) -> None:
        """Kayda bir satır ekler. message bir Msg (çevrilir) ya da ham metin (hata ayrıntısı) olabilir."""
        entry: Dict[str, Any] = {"t": time.strftime("%H:%M:%S"), "level": level}
        if isinstance(message, Msg):
            entry["msg"] = message.to_json()
        else:
            entry["text"] = str(message)
        with self._lock:
            self.logs.append(entry)
            if len(self.logs) > 3000:
                drop = len(self.logs) - 2000
                self.logs = self.logs[drop:]
                self.log_offset += drop

    def snapshot(self, since: int = 0) -> Dict[str, Any]:
        with self._lock:
            start = max(0, since - self.log_offset)
            return {
                "running": self.running,
                "phase": self.phase,
                "mode": self.mode,
                "verify": self.verify,
                "login_wall": self.login_wall,
                "rate_limited": self.rate_limited,
                "logged_in": self.logged_in,
                "author": self.author,
                "folder": self.folder,
                "found_posts": self.found_posts,
                "posts_done": self.posts_done,
                "posts_skipped": self.posts_skipped,
                "posts_unavailable": self.posts_unavailable,
                "posts_known": self.posts_known,
                "posts_repaired": self.posts_repaired,
                "paused": self.paused,
                "tabs": {k: dict(v) for k, v in self.tabs.items() if v.get("found") or v.get("failed")},
                "failures": list(self.failures),
                "waiting": max(0, int(self.wait_until - time.time())) if self.wait_until else 0,
                "wait_kind": self.wait_kind,
                "collecting": self.collecting,
                "files_total": self.files_total,
                "files_done": self.files_done,
                "files_skipped": self.files_skipped,
                "files_failed": self.files_failed,
                "current": self.current,
                "current_bytes": self.current_bytes,
                "current_total": self.current_total,
                "summary": self.summary.to_json() if self.summary else None,
                "notes": [n.to_json() for n in self.notes],
                "logs": self.logs[start:],
                "log_next": self.log_offset + len(self.logs),
            }

    # ------------------------------------------------------------ kontrol
    def start(self, job: Job) -> Optional[Msg]:
        with self._lock:
            if self.running:
                return msg("engine.already_running")
        if job.mode == MODE_COOKIE:
            if not parse_session_input(job.link):
                return msg("engine.cookie_invalid")
        else:
            parsed = links.parse_link(job.link)
            if not parsed.ok:
                return parsed.error
            if job.mode == MODE_SINGLE and parsed.kind == links.PROFILE:
                return msg("engine.single_needs_post")
        logs_offset = self.log_offset + len(self.logs)
        self._reset()
        self.log_offset = logs_offset
        self._cancel = threading.Event()
        self.running = True
        self.phase = "starting"
        self.mode = job.mode
        self.started_at = time.monotonic()
        self._thread = threading.Thread(target=self._thread_main, args=(job,), daemon=True)
        self._thread.start()
        return None

    def stop(self) -> None:
        if self.running:
            self.log("warn", msg("engine.stopping"))
            self.paused = False
            self._cancel.set()

    def pause(self) -> None:
        if self.running and not self.paused:
            self.paused = True
            self.log("info", msg("engine.paused"))

    def resume(self) -> None:
        if self.paused:
            self.paused = False
            self.log("info", msg("engine.resumed"))

    async def pause_gate(self) -> None:
        """Duraklatıldıysa devam edilene (ya da durdurulana) kadar bekler."""
        while self.paused and not self._cancel.is_set():
            await asyncio.sleep(0.3)

    def _tab(self, section: str) -> Dict[str, int]:
        return self.tabs.setdefault(section or "posts", {"found": 0, "done": 0, "failed": 0, "skipped": 0})

    def wait(self, timeout: float = 10) -> None:
        thread = self._thread
        if thread is not None:
            thread.join(timeout)

    def _thread_main(self, job: Job) -> None:
        try:
            asyncio.run(self._run(job))
        except Exception as exc:  # beklenmeyen hata — kullanıcıya göster
            self.log("error", msg("engine.unexpected", error=str(exc)))
            self.log("debug", traceback.format_exc())
            self.phase = "error"
            self.summary = msg("engine.unexpected_summary")
        finally:
            self.running = False
            self.verify = False
            self.collecting = False
            self.current = ""

    # ------------------------------------------------------------ iş akışı
    async def _run(self, job: Job) -> None:
        settings = clean_settings(job.settings)
        self._use_history = bool(settings.get("history", True))
        self.history_auto = bool(settings.get("auto_continue", True))
        try:
            self.history = History()
        except Exception as exc:  # geçmiş açılamazsa yine de çalış
            self.history = None
            self.log("warn", msg("engine.history_failed", error=str(exc)))
        try:
            await self._run_browser(job, settings)
        finally:
            if self.history is not None:
                self.history.close()
                self.history = None

    async def _run_browser(self, job: Job, settings: Dict[str, Any]) -> None:
        try:
            async with InstagramBrowser(app_data_dir(), log=self.log, cancel=self._cancel,
                                        on_verify=self._set_verify, pacing=pacing_for(settings),
                                        identity=str(settings.get("identity") or "desktop")) as browser:
                browser.on_wait = self._set_wait
                self.browser_name = browser.channel_name
                if job.mode == MODE_COOKIE:
                    result = await browser.import_session(job.link)
                    ok = result == "ok"
                    if ok:
                        self.logged_in = True
                        save_session_state(True)
                    self.phase = "done" if ok else "error"
                    if ok:
                        self.summary = msg("engine.cookie_ok")
                    elif result == "invalid":
                        self.summary = msg("engine.cookie_invalid")
                    elif result == "expired":
                        self.summary = msg("engine.cookie_expired")
                    elif result == "challenge":
                        self.summary = msg("engine.cookie_challenge")
                    else:
                        self.summary = msg("engine.cookie_error")
                    self.log("ok" if ok else "error", self.summary)
                    return
                await self._download_job(browser, job, settings)
        except BrowserError as exc:
            if self._cancel.is_set():
                self._finish_stopped()
            else:
                if isinstance(exc, LoginRequired):
                    self.login_wall = True
                if isinstance(exc, RateLimited):
                    self.rate_limited = True
                self.phase = "error"
                self.summary = exc.message
                self.log("error", exc.message)
        except (asyncio.CancelledError, Cancelled):
            self._finish_stopped()

    def _finish_stopped(self) -> None:
        self.phase = "stopped"
        self.summary = msg("engine.stopped", n=self.files_done)
        self.log("warn", self.summary)

    def _set_verify(self, value: bool) -> None:
        self.verify = value

    def _set_wait(self, until: float) -> None:
        self.wait_until = until
        self.wait_kind = "pause" if until else ""

    async def _download_job(self, browser: InstagramBrowser, job: Job, settings: Dict[str, Any]) -> None:
        parsed = links.parse_link(job.link)
        out_root = Path(settings["out_dir"]).expanduser()
        try:
            out_root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise BrowserError(msg("engine.folder_failed", path=str(out_root), error=str(exc)))

        logged_in = await browser.logged_in()
        self.logged_in = logged_in
        save_session_state(logged_in)
        self.log("info", msg("engine.account_yes" if logged_in else "engine.account_no"))
        self._out_root = out_root
        self._verify = bool(settings.get("verify_files", True)) and self._use_history
        self._caption_min = int(settings.get("caption_min") or 0)
        self._info_csv = bool(settings.get("info_csv", True))
        self._date_from = date_start_ts(settings.get("date_from", ""))
        self._date_to = date_end_ts(settings.get("date_to", ""))
        browser.pause_gate = self.pause_gate

        queue: asyncio.Queue = asyncio.Queue()
        downloads = DownloadPool(self, browser, out_root, queue)
        workers = [asyncio.ensure_future(downloads.worker()) for _ in range(WORKERS)]
        collect_error: Optional[BrowserError] = None
        try:
            self.phase = "collecting"
            if parsed.kind == links.SHARE:
                parsed = await browser.resolve_share(parsed.url)
                if job.mode == MODE_SINGLE and parsed.kind == links.PROFILE:
                    raise BrowserError(msg("engine.share_to_profile"))
            try:
                if job.mode == MODE_SINGLE:
                    await self._single(browser, parsed, settings, queue, logged_in)
                else:
                    username = await self._owner_of(browser, parsed, logged_in)
                    await self._profile(browser, username, settings, queue, logged_in, out_root)
            except RateLimited as exc:
                # Toplanan kadarını yine de indir.
                self.rate_limited = True
                collect_error = exc
                self.log("error", exc.message)
            except LoginRequired as exc:
                if not self.files_total and not self.posts_known:
                    raise
                # Instagram oturumu iş ortasında kapattı: bulunanları indir, kalanı sonra devam eder.
                self.login_wall = True
                collect_error = exc
                self.log("error", exc.message)
            self.collecting = False
            for _ in workers:
                queue.put_nowait(None)
            self.phase = "downloading"
            await asyncio.gather(*workers)
            await self._retry_failed(downloads)
        except BaseException:
            for w in workers:
                w.cancel()
            await asyncio.gather(*workers, return_exceptions=True)
            self._write_info()
            raise
        self._write_info()

        if self._cancel.is_set():
            self._finish_stopped()
            return
        if self._pending_complete and self.files_failed == 0 and collect_error is None and self.history:
            self.history.set_complete(*self._pending_complete)
        if collect_error is not None and self.files_done == 0 and self.files_skipped == 0 and self.posts_known == 0:
            self.phase = "error"
            self.summary = collect_error.message
            return
        self.phase = "done"
        self.summary = msg("summary", done=self.files_done, skipped=self.files_skipped,
                           failed=self.files_failed, unmatched=self.posts_skipped,
                           unavailable=self.posts_unavailable, known=self.posts_known)
        if self.files_done == 0 and self.files_skipped == 0 and self.posts_known == 0:
            self.phase = "error" if self.files_failed or self.found_posts == 0 else "done"
            if self.found_posts == 0 and self.notes:
                self.summary = self.notes[0]
        self.log("ok" if self.phase == "done" else "error", self.summary)

    # ------------------------------------------------------------ yeniden deneme ve bilgi dosyası
    async def _retry_failed(self, downloads: "DownloadPool") -> None:
        """İnemeyen gönderileri bir kez daha dener (geçici bağlantı sorunları için)."""
        posts = [p for p in self._failed_posts if p.attempts < 2]
        if not posts or self._cancel.is_set():
            return
        wait = float(os.environ.get("INSTAGRAM_INDIRICI_RETRY_WAIT", "15"))
        self.log("warn", msg("engine.retrying", n=len(posts)))
        end = time.monotonic() + wait
        while time.monotonic() < end and not self._cancel.is_set():
            await asyncio.sleep(0.2)
        for post in posts:
            self._failed_posts.remove(post)
            self.failures = [f for f in self.failures if f.get("key") != f"{post.kind}:{post.key}"]
            self.files_failed = max(0, self.files_failed - post.failed_files)
            self._tab(post.section)["failed"] = max(0, self._tab(post.section)["failed"] - 1)
            post.failed_files = 0
        queue: asyncio.Queue = asyncio.Queue()
        pool = DownloadPool(self, downloads.browser, downloads.out_root, queue)
        workers = [asyncio.ensure_future(pool.worker()) for _ in range(min(WORKERS, len(posts)))]
        for post in posts:
            queue.put_nowait(post)
        for _ in workers:
            queue.put_nowait(None)
        try:
            await asyncio.gather(*workers)
        except BaseException:
            for w in workers:
                w.cancel()
            await asyncio.gather(*workers, return_exceptions=True)
            raise

    def note_info(self, post: Post, results: List[str]) -> None:
        """Başarıyla inen gönderiyi profilin bilgi.csv dosyasına eklenecekler arasına alır."""
        if not self._info_csv or post.kind not in (KIND_POST, KIND_REEL, KIND_REPOST, KIND_TAGGED) or not post.code:
            return
        user = author_folder(post.dest_user or post.username or self.author)
        names = ";".join(Path(f.rel_stem).name for f in post.files)
        when = time.strftime("%Y-%m-%d %H:%M", time.gmtime(post.taken_at)) if post.taken_at else ""
        kind = {KIND_POST: "gönderi", KIND_REEL: "reels", KIND_REPOST: "yeniden paylaşım",
                KIND_TAGGED: "etiketlenen"}[post.kind]
        caption = " ".join(post.caption.split())[:140]
        row = [post.code, kind, when, post.permalink, str(post.likes), str(post.comments), names, caption]
        self._info_rows.setdefault(user, {})[post.code] = row

    def _write_info(self) -> None:
        """Her profil klasöründeki bilgi.csv'yi yeni satırlarla günceller (aynı kod bir kez yazılır)."""
        if not self._info_rows or self._out_root is None:
            return
        import csv

        for user, rows in self._info_rows.items():
            path = self._out_root / user / INFO_NAME
            old: Dict[str, List[str]] = {}
            try:
                with open(path, "r", encoding="utf-8-sig", newline="") as fh:
                    for line in csv.reader(fh, delimiter=";"):
                        if line and line[0] != INFO_HEADER[0]:
                            old[line[0]] = line
            except OSError:
                pass
            old.update(rows)
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp = path.with_name(path.name + ".part")
                with open(tmp, "w", encoding="utf-8-sig", newline="") as fh:
                    writer = csv.writer(fh, delimiter=";")
                    writer.writerow(INFO_HEADER)
                    writer.writerows(old.values())
                os.replace(tmp, path)
            except OSError as exc:
                self.log("debug", f"bilgi.csv: {exc}")
        self._info_rows = {}

    # ------------------------------------------------------------ tek öğe
    async def _single(self, browser: InstagramBrowser, parsed: links.ParsedLink, settings: Dict[str, Any],
                      queue: asyncio.Queue, logged_in: bool) -> None:
        content = CONTENT_ALL  # tek gönderide ne varsa onu indir
        if parsed.kind == links.POST:
            item = await browser.get_post(parsed.shortcode, logged_in=logged_in)
            owner = owner_of(item)
            self.author = str(owner.get("username") or "")
            self._enqueue(queue, build_post(item, content=content, username=self.author), content=content)
            return
        if parsed.kind == links.STORY:
            if not logged_in:
                raise LoginRequired(msg("engine.stories_need_login"))
            info = await browser.open_profile(parsed.username, logged_in=logged_in)
            self.author = info.username
            items = await browser.stories(info.user_id)
            if parsed.story_media_id:
                items = [i for i in items if str(i.get("pk") or str(i.get("id") or "").split("_")[0]) == parsed.story_media_id]
            if not items:
                raise NotFound(msg("engine.no_stories"))
            for item in items:
                self._enqueue(queue, build_post(item, content=content, kind=KIND_STORY,
                                                subdir=STORIES_DIR, username=info.username),
                              content=content, user_id=info.user_id)
            return
        if parsed.kind == links.HIGHLIGHT:
            if not logged_in:
                raise LoginRequired(msg("engine.highlights_need_login"))
            reels = await browser.highlight_reels([f"highlight:{parsed.highlight_id}"])
            if not reels or not reels[0]["items"]:
                raise NotFound(msg("engine.highlight_not_found"))
            for reel in reels:
                self.author = self.author or str(reel["user"].get("username") or "")
                folder = highlight_folder(reel["title"], parsed.highlight_id)
                for item in reel["items"]:
                    self._enqueue(queue, build_post(item, content=content, kind=KIND_HIGHLIGHT,
                                                    subdir=folder, username=self.author), content=content)
            return
        raise BrowserError(msg("engine.single_needs_post"))

    async def _owner_of(self, browser: InstagramBrowser, parsed: links.ParsedLink, logged_in: bool) -> str:
        if parsed.kind in (links.PROFILE, links.STORY):
            return parsed.username
        self.log("info", msg("engine.finding_owner"))
        if parsed.kind == links.POST:
            item = await browser.get_post(parsed.shortcode, logged_in=logged_in)
            name = str(owner_of(item).get("username") or "")
        elif parsed.kind == links.HIGHLIGHT:
            if not logged_in:
                raise LoginRequired(msg("engine.highlights_need_login"))
            reels = await browser.highlight_reels([f"highlight:{parsed.highlight_id}"])
            name = str((reels[0]["user"] if reels else {}).get("username") or "")
        else:
            name = ""
        if not name:
            raise BrowserError(msg("engine.owner_not_found"))
        return name.lower()

    # ------------------------------------------------------------ profil
    async def _profile(self, browser: InstagramBrowser, username: str, settings: Dict[str, Any],
                       queue: asyncio.Queue, logged_in: bool, out_root: Path) -> None:
        content = settings["content"]
        info = await browser.open_profile(username, logged_in=logged_in)
        self.author = info.username
        folder = out_root / author_folder(info.username)
        self.folder = str(folder)
        label = f"{info.full_name} (@{info.username})" if info.full_name else f"@{info.username}"
        if info.media_count >= 0:
            self.log("info", msg("engine.profile_found_count", name=label, n=info.media_count))
        else:
            self.log("info", msg("engine.profile_found", name=label))
        self.log("debug", f"user_id={info.user_id} sources={','.join(info.sources)}")
        if self.history and self._use_history:
            self._known = self.history.known(info.user_id, content)
            if self._known:
                self.log("info", msg("engine.history_known", n=len(self._known)))

        if settings["avatar"]:
            try:
                urls = await browser.avatar_urls(info, logged_in=logged_in)
                avatar = build_avatar(info.user, urls, content=content)
                avatar.dest_user = info.username
                self._enqueue(queue, avatar, quiet_skip=True)
            except (ApiError, NotFound, RateLimited) as exc:
                self.log("warn", msg("engine.avatar_failed", error=exc.message))

        if info.locked:
            note = msg("engine.private_logged_in" if logged_in else "engine.private")
            self.notes.append(note)
            self.log("warn", note)
            return

        if settings["stories"]:
            await self._stories(browser, info, content, queue, logged_in)
        if settings["posts"]:
            await self._posts(browser, info, settings, queue, logged_in, folder)
        for tab, kind in (("reels", KIND_REEL), ("reposts", KIND_REPOST), ("tagged", KIND_TAGGED)):
            if settings[tab]:
                await self._tab_pass(browser, info, settings, queue, logged_in, tab, kind)
        # Öne çıkanlar en yavaş bölüm (listeden alınamazsa her biri ayrı sayfa açar): ana içerikten sonra.
        if settings["highlights"]:
            await self._highlights(browser, info, content, queue, logged_in)
        if not any(settings[k] for k in ("posts", "stories", "highlights", "avatar", "reels", "reposts", "tagged")):
            self.notes.append(msg("engine.nothing_selected"))

    async def _stories(self, browser: InstagramBrowser, info: ProfileInfo, content: str,
                       queue: asyncio.Queue, logged_in: bool) -> None:
        if not logged_in:
            self.log("info", msg("engine.stories_skipped_login"))
            return
        try:
            items = await self._with_rate_wait(lambda: browser.stories(info.user_id, info.username))
        except (ApiError, NotFound, RateLimited) as exc:
            self.log("warn", msg("engine.stories_failed", error=exc.message))
            return
        except LoginRequired:
            self.log("warn", msg("browser.session_expired"))
            return
        if not items:
            self.log("info", msg("engine.no_stories_now"))
            return
        self.log("info", msg("engine.stories_found", n=len(items)))
        for item in items:
            post = build_post(item, content=content, kind=KIND_STORY, subdir=STORIES_DIR, username=info.username)
            post.dest_user = info.username
            self._enqueue(queue, post, content=content, user_id=info.user_id)

    async def _highlights(self, browser: InstagramBrowser, info: ProfileInfo, content: str,
                          queue: asyncio.Queue, logged_in: bool) -> None:
        if not logged_in:
            self.log("info", msg("engine.highlights_skipped_login"))
            return
        failed: List[str] = []
        try:
            tray = await self._with_rate_wait(lambda: browser.highlights_tray(info.user_id, info.username))
            if not tray:
                self.log("info", msg("engine.no_highlights"))
                return
            self.log("info", msg("engine.highlights_found", n=len(tray)))
            titles = {h["id"]: h["title"] for h in tray}
            expected = {h["id"]: h.get("media_count") or 0 for h in tray}

            async def fetch_reels():
                failed.clear()
                return await browser.highlight_reels([h["id"] for h in tray], expected=expected, failed=failed,
                                                     titles=titles)
            reels = await self._with_rate_wait(fetch_reels)
        except LoginRequired:
            self.log("warn", msg("browser.session_expired"))
            return
        except (ApiError, NotFound, RateLimited) as exc:
            self.log("warn", msg("engine.highlights_failed", error=exc.message))
            return
        for reel in reels:
            hid = reel["id"]
            title = reel["title"] or titles.get(hid, "")
            folder = highlight_folder(title, hid.split(":")[-1])
            self.log("info", msg("engine.highlight_items", title=title or hid.split(":")[-1], n=len(reel["items"])))
            for item in reel["items"]:
                post = build_post(item, content=content, kind=KIND_HIGHLIGHT, subdir=folder, username=info.username)
                post.dest_user = info.username
                self._enqueue(queue, post, content=content, user_id=info.user_id)
        if failed:
            names = ", ".join(titles.get(h) or h.split(":")[-1] for h in failed)
            note = msg("engine.highlights_partial", n=len(failed), names=names)
            self.notes.append(note)
            self.log("warn", note)

    async def _posts(self, browser: InstagramBrowser, info: ProfileInfo, settings: Dict[str, Any],
                     queue: asyncio.Queue, logged_in: bool, folder: Path) -> None:
        content = settings["content"]
        limit = settings["limit"]
        uid = info.user_id
        newest_known = 0
        # Dosya kontrolü açıkken profilin tamamı taranır: geçmişte "indi" görünen ama dosyası olmayanlar bulunur.
        if settings.get("only_new", True) and not self._verify:
            newest_known = self.history.complete_newest(uid, content) if self.history else 0
            marker = read_marker(folder)   # 1.0.x sürümünün klasöre yazdığı not
            if not newest_known and str(marker.get("user_id") or "") == uid:
                newest_known = known_newest(marker, content)
        if newest_known:
            self.log("info", msg("engine.only_new"))
        resume = None
        if self.history and self._use_history and not newest_known and not limit and not self._verify:
            resume = self.history.resume_point(uid, content)
            if resume:
                self.log("info", msg("engine.resume_found"))
        if not limit:
            self._progress_ctx = (uid, content, info.username)
        newest_seen = {"ts": 0}
        repairs: List[Post] = []
        repair_keys: set = set()

        def on_page(items: List[Dict[str, Any]], cursor_kind: str = "", next_cursor: str = "") -> str:
            page = {"cursor_kind": cursor_kind, "cursor": next_cursor, "pending": set(), "failed": False}
            self._pages.append(page)
            reached_old = False
            regular = 0
            regular_known = 0
            for item in items:
                ts = taken_at_of(item)
                post = build_post(item, content=content, username=info.username, caption_min=self._caption_min)
                post.dest_user = info.username
                if not is_pinned(item):
                    newest_seen["ts"] = max(newest_seen["ts"], ts)
                    if newest_known and ts and ts <= newest_known:
                        reached_old = True
                    if self._date_from and ts and ts < self._date_from:
                        reached_old = True       # liste yeniden eskiye doğru; bundan sonrası aralık dışı
                    regular += 1
                    if post.key in self._known or post.skipped_reason:
                        regular_known += 1
                if not self._in_range(ts):
                    continue
                if needs_repair(item, post) and post.key not in self._known:
                    if post.key not in repair_keys:
                        repair_keys.add(post.key)
                        repairs.append(post)
                    continue
                self._enqueue(queue, post, content=content, user_id=uid, page=page)
            self._advance_pages()
            if reached_old:
                return "stop"
            if (resume and not self._jumped and next_cursor and resume[0] == cursor_kind
                    and regular and regular_known == regular):
                # Bu bölge önceden inmiş: yarım kalan taramanın devam noktasına atla.
                self._jumped = True
                page["cursor"] = resume[1]
                return "jump"
            return ""

        rounds = 0
        while True:
            self.collecting = True
            try:
                result: FeedResult = await browser.collect_posts(
                    info, logged_in=logged_in, limit=limit, on_page=on_page, resume=resume)
            except RateLimited:
                result = FeedResult(collected=0, reason="rate_limited")
            self.log("debug", f"posts via={result.via} reason={result.reason} n={result.collected}")
            if result.reason == "rate_limited" and settings.get("auto_continue", True) and rounds < max_rounds():
                rounds += 1
                await self._wait_and_continue(rounds)
                # Bekleme sırasında inenler geçmişe yazıldı; kaldığı yerden devam edilir.
                if self.history and self._use_history:
                    self._known |= self.history.known(uid, content)
                resume = self.history.resume_point(uid, content) if self.history and not limit else None
                self._jumped = False
                continue
            break
        if result.reason == "rate_limited" and result.collected == 0 and not self.files_total and not self.posts_known:
            self.collecting = False
            raise RateLimited("feed")
        self._report_posts(result, info)
        if repairs:
            await self._repair(browser, repairs, content, queue, logged_in, info.username, uid)
        self.collecting = False

        if result.reason in ("end", "up_to_date") and not limit and not self._date_from and not self._date_to:
            newest = newest_seen["ts"]
            if result.reason == "up_to_date":
                newest = max(newest, newest_known)
            # İndirmeler hatasız biterse yazılır (bkz. _download_job).
            self._pending_complete = (uid, content, newest, info.username)

    def _files_state(self, post: Post) -> str:
        """Gönderinin dosyaları diskte mi? "ok" (hepsi yeni yerinde), "legacy" (eski klasörde var) ya da "missing"."""
        if self._out_root is None:
            return "ok"
        folder = self._out_root / author_folder(post.dest_user or post.username or self.author)
        state = "ok"
        for media_file in post.files:
            if existing_file(folder / Path(media_file.rel_stem), media_file.ext):
                continue
            if any(existing_file(folder / old, media_file.ext) for old in media_file.legacy_stems):
                state = "legacy"
                continue
            return "missing"
        if post.caption_stem and not (folder / (post.caption_stem + ".txt")).is_file():
            return "missing" if state == "ok" else state
        return state

    def _in_range(self, ts: int) -> bool:
        if not ts:
            return True
        if self._date_from and ts < self._date_from:
            return False
        if self._date_to and ts > self._date_to:
            return False
        return True

    async def _tab_pass(self, browser: InstagramBrowser, info: ProfileInfo, settings: Dict[str, Any],
                   queue: asyncio.Queue, logged_in: bool, tab: str, kind: str) -> None:
        """Profil sayfasındaki ayrı bir sekmeyi (Reels, Yeniden paylaşımlar, Etiketlenenler) toplar."""
        content = settings["content"]
        limit = settings["limit"]
        uid = info.user_id
        label = msg(f"engine.tab_{tab}")
        if not logged_in and tab != "reels":
            self.log("info", msg("engine.tab_needs_login", tab=label))
            return
        self.log("info", msg("engine.tab_scanning", tab=label))
        stop_known = bool(settings.get("only_new", True)) and not self._verify

        def on_page(items: List[Dict[str, Any]], cursor_kind: str = "", next_cursor: str = "") -> str:
            fresh = 0
            reached_old = False
            for item in items:
                ts = taken_at_of(item)
                post = build_post(item, content=content, kind=kind, username=info.username,
                                  caption_min=self._caption_min)
                post.dest_user = info.username
                post.owner_id = uid          # geçmiş, indirilen profile bağlı (gönderi başka kişinin olsa bile)
                if self._date_from and ts and ts < self._date_from and not is_pinned(item):
                    reached_old = True
                if not self._in_range(ts):
                    continue
                if post.key not in self._known and not post.skipped_reason:
                    fresh += 1
                self._enqueue(queue, post, content=content, user_id=uid)
            if reached_old or (stop_known and items and fresh == 0):
                return "stop"
            return ""

        rounds = 0
        while True:
            self.collecting = True
            try:
                result: FeedResult = await browser.collect_tab(info, tab, logged_in=logged_in, limit=limit,
                                                               on_page=on_page)
            except RateLimited:
                result = FeedResult(collected=0, reason="rate_limited")
            self.log("debug", f"{tab} via={result.via} reason={result.reason} n={result.collected}")
            if (result.reason == "rate_limited" and settings.get("auto_continue", True)
                    and rounds < other_rounds() and result.collected == 0):
                rounds += 1
                await self._wait_and_continue(rounds, total=other_rounds())
                if self.history and self._use_history:
                    self._known |= self.history.known(uid, content)
                continue
            break
        self.collecting = False
        if result.reason == "rate_limited":
            self.rate_limited = True
            note = msg("engine.tab_rate_limited", tab=label, n=result.collected)
            self.notes.append(note)
            self.log("warn", note)
        elif result.reason in ("login_wall", "session_expired"):
            self.login_wall = True
            note = msg("engine.tab_login_wall", tab=label, n=result.collected)
            self.notes.append(note)
            self.log("warn", note)
        elif result.collected == 0:
            self.log("info", msg("engine.tab_empty", tab=label))
        else:
            self.log("ok", msg("engine.tab_scanned", tab=label, n=result.collected))

    async def _with_rate_wait(self, fn):
        """Hız sınırına takılan listeyi (hikaye, öne çıkan, Reels…) bekleyip birkaç kez yeniden dener."""
        rounds = 0
        while True:
            try:
                return await fn()
            except RateLimited:
                if not self.history_auto or rounds >= other_rounds():
                    raise
                rounds += 1
                self.rate_limited = True
                await self._wait_and_continue(rounds, total=other_rounds())
                self.rate_limited = False

    async def _wait_and_continue(self, round_no: int, total: Optional[int] = None) -> None:
        seconds = resume_wait()
        self.log("warn", msg("engine.auto_wait", m=max(1, round(seconds / 60)), n=round_no,
                             total=total if total is not None else max_rounds()))
        self.wait_until = time.time() + seconds
        self.wait_kind = "rate"
        try:
            end = time.monotonic() + seconds
            while time.monotonic() < end:
                if self._cancel.is_set():
                    raise asyncio.CancelledError()
                await asyncio.sleep(min(1.0, end - time.monotonic()))
        finally:
            self.wait_until = 0.0
            self.wait_kind = ""
        self.log("info", msg("engine.auto_resume"))

    def _advance_pages(self) -> None:
        """Tamamen inen sayfaların imlecini kaydeder (yarıda kalırsa oradan devam etmek için)."""
        while self._pages and not self._pages[0]["pending"] and not self._pages[0]["failed"]:
            page = self._pages.pop(0)
            if self._progress_ctx and self.history and page["cursor"] and page["cursor_kind"]:
                uid, content, username = self._progress_ctx
                self.history.set_resume_point(uid, content, page["cursor_kind"], page["cursor"], username)

    def post_finished(self, post: Post, ok: bool, results: Optional[List[str]] = None) -> None:
        """İndirme havuzu bir gönderiyi bitirince çağırır."""
        tab = self._tab(post.section)
        if ok:
            tab["done"] += 1
            self.note_info(post, results or [])
        else:
            tab["failed"] += 1
            if post not in self._failed_posts:
                self._failed_posts.append(post)
            if len(self.failures) < MAX_FAILURES:
                self.failures.append({"key": f"{post.kind}:{post.key}", "name": post.stem,
                                      "section": post.section, "error": post.last_error})
        if ok and self.history and post.kind != KIND_AVATAR and post.owner_id:
            self.history.mark(post.owner_id, post.key, post.history_content or CONTENT_ALL, post.kind, post.username)
            if post.history_content:
                self._known.add(post.key)
        page = post.page
        if page is not None:
            page["pending"].discard(post.key)
            if not ok:
                page["failed"] = True
            self._advance_pages()

    async def _repair(self, browser: InstagramBrowser, repairs: List[Post], content: str,
                      queue: asyncio.Queue, logged_in: bool, username: str, user_id: str) -> None:
        """Listede video adresi olmadan gelen gönderileri tek tek açıp tamamlar."""
        self.log("info", msg("engine.repairing", n=len(repairs)))
        for post in repairs:
            if self._cancel.is_set():
                return
            try:
                item = await browser.get_post(post.code, logged_in=logged_in)
                fixed = build_post(item, content=content, username=username, caption_min=self._caption_min)
                fixed.dest_user = username
                if not fixed.skipped_reason:
                    self._enqueue(queue, fixed, content=content, user_id=user_id)
                    continue
            except (ApiError, NotFound, LoginRequired, RateLimited) as exc:
                self.log("debug", f"repair {post.code}: {exc.message.key}")
            self._enqueue(queue, post, content=content, user_id=user_id)

    def _report_posts(self, result: FeedResult, info: ProfileInfo) -> None:
        n, total = result.collected, info.media_count
        if result.reason == "session_expired":
            self.login_wall = True
            note = msg("engine.session_expired_partial", n=n)
            self.notes.append(note)
            self.log("warn", note)
        elif result.reason == "rate_limited":
            self.rate_limited = True
            note = msg("engine.rate_limited_partial", n=n)
            self.notes.append(note)
            self.log("warn", note)
        elif result.reason == "login_wall":
            self.login_wall = True
            key = "engine.login_wall_total" if total > 0 else "engine.login_wall"
            note = msg(key, n=n, total=total)
            self.notes.append(note)
            self.log("warn", note)
        elif n == 0 and result.reason in ("end", "stalled"):
            empty = total == 0 or (total < 0 and result.reason == "end")
            note = msg("engine.empty" if empty else "engine.no_list")
            self.notes.append(note)
            self.log("info" if empty else "error", note)
        elif result.reason == "limit":
            self.log("info", msg("engine.limit_reached", n=n))
        elif result.reason == "up_to_date":
            self.log("ok", msg("engine.up_to_date", n=n))
        else:
            if total > 0:
                self.log("ok", msg("engine.profile_scanned_total", n=n, total=total))
            else:
                self.log("ok", msg("engine.profile_scanned", n=n))

    # ------------------------------------------------------------ kuyruk
    def _enqueue(self, queue: asyncio.Queue, post: Post, quiet_skip: bool = False, *, content: str = "",
                 user_id: str = "", page: Optional[Dict[str, Any]] = None) -> None:
        if not post.key:
            return
        if user_id and not post.owner_id:
            post.owner_id = user_id
        dedupe = f"{post.kind}:{post.key}"
        if dedupe in self._enqueued:
            return   # aynı iş içinde ikinci kez listelendi (ör. bekleyip devam ederken)
        self._enqueued.add(dedupe)
        if post.kind in (KIND_POST, KIND_REEL, KIND_REPOST, KIND_TAGGED):
            self.found_posts += 1
        if post.kind != KIND_AVATAR:
            self._tab(post.section)["found"] += 1
        if not self.author and post.username:
            self.author = post.username
        if post.skipped_reason:
            if post.skipped_reason.key in UNMATCHED_REASONS:
                if post.kind != KIND_AVATAR:
                    self.posts_skipped += 1
            else:
                self.posts_unavailable += 1
            if post.kind != KIND_AVATAR:
                self._tab(post.section)["skipped"] += 1
            if not quiet_skip:
                self.log("skip", msg("engine.skipped_post", reason=post.skipped_reason, name=post.stem))
            return
        if post.kind != KIND_AVATAR and post.key in self._known:
            # Geçmişe göre daha önce (belki başka hesapla, başka klasöre) inmiş.
            state = self._files_state(post) if self._verify else "ok"
            if state == "ok":
                self.posts_known += 1
                self._tab(post.section)["done"] += 1
                return
            # Dosyası yok ya da eski klasörde: indirme havuzu eksikleri indirir / eski dosyaları yeni yere taşır.
            if state == "missing":
                self.posts_repaired += 1
                self.log("info", msg("engine.missing_file", name=post.stem))
        post.history_content = content
        if page is not None:
            post.page = page
            page["pending"].add(post.key)
        self.files_total += len(post.files)
        queue.put_nowait(post)


class DownloadPool:
    def __init__(self, engine: Engine, browser: InstagramBrowser, out_root: Path, queue: asyncio.Queue) -> None:
        self.engine = engine
        self.browser = browser
        self.out_root = out_root
        self.queue = queue
        self.headers = {
            "User-Agent": browser.user_agent or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                                "(KHTML, like Gecko) Chrome/141.0 Safari/537.36 Edg/141.0",
            "Referer": "https://www.instagram.com/",
            "Accept": "*/*",
        }

    async def worker(self) -> None:
        while True:
            post: Optional[Post] = await self.queue.get()
            if post is None:
                return
            if self.engine._cancel.is_set():
                continue
            await self.engine.pause_gate()
            await self._download_post(post)

    async def _download_post(self, post: Post) -> None:
        eng = self.engine
        folder = self.out_root / author_folder(post.dest_user or post.username or eng.author)
        if not eng.folder:
            eng.folder = str(folder)
        post.attempts += 1
        post.last_error = ""
        results = []
        failed_before = eng.files_failed
        for media_file in post.files:
            if eng._cancel.is_set():
                return
            await eng.pause_gate()
            results.append(await self._download_file(folder, media_file, post))
        post.failed_files = max(0, eng.files_failed - failed_before)
        ok = bool(results) and all(r != "failed" for r in results)
        if post.attempts == 1:
            eng.posts_done += 1
        if ok and post.caption_stem:
            self._write_caption(folder, post)
        eng.post_finished(post, ok, results)
        if ok:
            if any(r == "downloaded" for r in results):
                kinds = {f.kind for f in post.files}
                key = {
                    "story": "engine.downloaded_story",
                    "highlight": "engine.downloaded_highlight",
                    "avatar": "engine.downloaded_avatar",
                    "reel": "engine.downloaded_reel",
                }.get(post.kind) or ("engine.downloaded_gallery" if post.is_gallery
                                     else "engine.downloaded_video" if "video" in kinds else "engine.downloaded_image")
            else:
                key = "engine.already_had"
            eng.log("ok", msg(key, name=post.stem))

    def _write_caption(self, folder: Path, post: Post) -> None:
        """Uzun açıklamayı gönderinin dosyasıyla aynı adlı .txt olarak yanına kaydeder."""
        path = folder / (post.caption_stem + ".txt")
        try:
            if path.is_file() and path.stat().st_size > 0:
                return
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".part")
            tmp.write_text(post.caption + "\n", encoding="utf-8")
            os.replace(tmp, path)
            self.engine.log("info", msg("engine.caption_saved", name=path.name))
        except OSError as exc:
            self.engine.log("warn", msg("engine.caption_failed", name=path.name, error=str(exc)))

    async def _download_file(self, folder: Path, media_file: MediaFile, post: Optional[Post] = None) -> str:
        """Sonuç: "downloaded", "skipped" (zaten vardı) ya da "failed"."""
        eng = self.engine
        stem_path = folder / Path(media_file.rel_stem)
        if existing_file(stem_path, media_file.ext):
            eng.files_skipped += 1
            return "skipped"
        for legacy in media_file.legacy_stems:
            # Eski sürümler dosyayı başka klasöre koyuyordu (kök, Fotoğraflar, Videolar): yeni yerine taşı.
            old = existing_file(folder / legacy, media_file.ext)
            if old is not None:
                try:
                    stem_path.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(old, stem_path.with_name(stem_path.name + old.suffix))
                    eng.files_skipped += 1
                    return "skipped"
                except OSError:
                    pass
        eng.current = stem_path.name
        eng.current_bytes = 0
        eng.current_total = media_file.expected_size

        def progress(done: int, total: int) -> None:
            eng.current_bytes = done
            eng.current_total = total or media_file.expected_size

        fallback_ext = ".mp4" if media_file.kind == "video" else ".jpg"
        if media_file.kind == "video" and media_file.dash is not None:
            # En yüksek kalite: Instagram'ın uygulama içinde oynattığı ayrı ses + görüntü akışı.
            dash = media_file.dash
            got = await asyncio.to_thread(
                fetch_dash, dash.video.url, dash.audio.url if dash.audio else "", stem_path,
                headers=self.headers, cancel=eng._cancel, on_progress=progress,
            )
            if got.ok:
                eng.files_done += 1
                eng.log("info", msg("engine.quality_dash", res=f"{dash.video.width}x{dash.video.height}",
                                    mbps=f"{dash.video.bandwidth / 1_000_000:.1f}"))
                return "downloaded"
            if eng._cancel.is_set():
                return "failed"
            eng.log("warn", msg("engine.quality_fallback", res=media_file.plain_res or "?", error=got.error or ""))
            if not media_file.urls:
                eng.files_failed += 1
                if post is not None:
                    post.last_error = got.error or ""
                eng.log("error", msg("engine.download_failed", name=stem_path.name, error=got.error or ""))
                return "failed"
        result = await asyncio.to_thread(
            fetch, media_file.urls, stem_path, ext=media_file.ext, fallback_ext=fallback_ext,
            headers=self.headers, cancel=eng._cancel, on_progress=progress,
        )
        if not result.ok and not eng._cancel.is_set() and result.status in (403, 0, 401):
            for url in media_file.urls[:3]:
                got = await self.browser.download_via_browser(url, self.headers)
                if got:
                    body, content_type = got
                    ext = media_file.ext or ext_from_content_type(content_type, fallback_ext)
                    result = await asyncio.to_thread(write_bytes, body, stem_path, ext)
                    break
        if result.ok:
            eng.files_done += 1
            if media_file.kind == "video" and media_file.dash is None:
                eng.log("info", msg("engine.quality_plain", res=media_file.plain_res or "?"))
            return "downloaded"
        if eng._cancel.is_set():
            return "failed"
        eng.files_failed += 1
        if post is not None:
            post.last_error = result.error or ""
        eng.log("error", msg("engine.download_failed", name=stem_path.name, error=result.error or ""))
        return "failed"
