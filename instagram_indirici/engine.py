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
                      ProfileInfo, RateLimited)
from .fetcher import Cancelled, existing_file, ext_from_content_type, fetch, fetch_dash, write_bytes
from .history import History
from .i18n import Msg, msg
from .media import (KIND_AVATAR, KIND_HIGHLIGHT, KIND_POST, KIND_STORY, STORIES_DIR, MediaFile, Post, author_folder,
                    build_avatar, build_post, highlight_folder, is_pinned, owner_of, taken_at_of)
from .paths import CONTENT_ALL, app_data_dir, clean_settings

MODE_SINGLE = "single"
MODE_PROFILE = "profile"
MODE_LOGIN = "login"
WORKERS = 3
UNMATCHED_REASONS = {"skip.video_only", "skip.image_only", "skip.image_only_avatar"}
MARKER_NAME = ".instagram-indirici.json"


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
        if job.mode != MODE_LOGIN:
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
            self._cancel.set()

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
                                        on_verify=self._set_verify) as browser:
                browser.on_wait = self._set_wait
                if job.mode == MODE_LOGIN:
                    ok = await browser.login_session()
                    self.logged_in = ok
                    save_session_state(ok)
                    self.phase = "done"
                    self.summary = msg("engine.login_done_ok" if ok else "engine.login_done")
                    self.log("ok", self.summary)
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
        except BaseException:
            for w in workers:
                w.cancel()
            await asyncio.gather(*workers, return_exceptions=True)
            raise

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
                self._enqueue(queue, build_avatar(info.user, urls, content=content), quiet_skip=True)
            except (ApiError, NotFound, RateLimited) as exc:
                self.log("warn", msg("engine.avatar_failed", error=exc.message))

        if info.locked:
            note = msg("engine.private_logged_in" if logged_in else "engine.private")
            self.notes.append(note)
            self.log("warn", note)
            return

        if settings["stories"]:
            await self._stories(browser, info, content, queue, logged_in)
        if settings["highlights"]:
            await self._highlights(browser, info, content, queue, logged_in)
        if settings["posts"]:
            await self._posts(browser, info, settings, queue, logged_in, folder)
        if not (settings["posts"] or settings["stories"] or settings["highlights"] or settings["avatar"]):
            self.notes.append(msg("engine.nothing_selected"))

    async def _stories(self, browser: InstagramBrowser, info: ProfileInfo, content: str,
                       queue: asyncio.Queue, logged_in: bool) -> None:
        if not logged_in:
            self.log("info", msg("engine.stories_skipped_login"))
            return
        try:
            items = await browser.stories(info.user_id)
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
            self._enqueue(queue, build_post(item, content=content, kind=KIND_STORY,
                                            subdir=STORIES_DIR, username=info.username),
                          content=content, user_id=info.user_id)

    async def _highlights(self, browser: InstagramBrowser, info: ProfileInfo, content: str,
                          queue: asyncio.Queue, logged_in: bool) -> None:
        if not logged_in:
            self.log("info", msg("engine.highlights_skipped_login"))
            return
        failed: List[str] = []
        try:
            tray = await browser.highlights_tray(info.user_id, info.username)
            if not tray:
                self.log("info", msg("engine.no_highlights"))
                return
            self.log("info", msg("engine.highlights_found", n=len(tray)))
            titles = {h["id"]: h["title"] for h in tray}
            expected = {h["id"]: h.get("media_count") or 0 for h in tray}
            reels = await browser.highlight_reels([h["id"] for h in tray], expected=expected, failed=failed)
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
                self._enqueue(queue, build_post(item, content=content, kind=KIND_HIGHLIGHT,
                                                subdir=folder, username=info.username),
                              content=content, user_id=info.user_id)
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
        if settings.get("only_new", True):
            newest_known = self.history.complete_newest(uid, content) if self.history else 0
            marker = read_marker(folder)   # 1.0.x sürümünün klasöre yazdığı not
            if not newest_known and str(marker.get("user_id") or "") == uid:
                newest_known = known_newest(marker, content)
        if newest_known:
            self.log("info", msg("engine.only_new"))
        resume = None
        if self.history and self._use_history and not newest_known and not limit:
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
                post = build_post(item, content=content, username=info.username)
                if not is_pinned(item):
                    newest_seen["ts"] = max(newest_seen["ts"], ts)
                    if newest_known and ts and ts <= newest_known:
                        reached_old = True
                    regular += 1
                    if post.key in self._known or post.skipped_reason:
                        regular_known += 1
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

        if result.reason in ("end", "up_to_date") and not limit:
            newest = newest_seen["ts"]
            if result.reason == "up_to_date":
                newest = max(newest, newest_known)
            # İndirmeler hatasız biterse yazılır (bkz. _download_job).
            self._pending_complete = (uid, content, newest, info.username)

    async def _wait_and_continue(self, round_no: int) -> None:
        seconds = resume_wait()
        self.log("warn", msg("engine.auto_wait", m=max(1, round(seconds / 60)), n=round_no, total=max_rounds()))
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

    def post_finished(self, post: Post, ok: bool) -> None:
        """İndirme havuzu bir gönderiyi bitirince çağırır."""
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
                fixed = build_post(item, content=content, username=username)
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
        if post.kind == KIND_POST:
            self.found_posts += 1
        if not self.author and post.username:
            self.author = post.username
        if post.skipped_reason:
            if post.skipped_reason.key in UNMATCHED_REASONS:
                if post.kind != KIND_AVATAR:
                    self.posts_skipped += 1
            else:
                self.posts_unavailable += 1
            if not quiet_skip:
                self.log("skip", msg("engine.skipped_post", reason=post.skipped_reason, name=post.stem))
            return
        if post.kind != KIND_AVATAR and post.key in self._known:
            # Geçmişe göre daha önce (belki başka hesapla, başka klasöre) inmiş.
            self.posts_known += 1
            return
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
            await self._download_post(post)

    async def _download_post(self, post: Post) -> None:
        eng = self.engine
        folder = self.out_root / author_folder(post.username or eng.author)
        if not eng.folder:
            eng.folder = str(folder)
        results = []
        for media_file in post.files:
            if eng._cancel.is_set():
                return
            results.append(await self._download_file(folder, media_file))
        eng.posts_done += 1
        eng.post_finished(post, bool(results) and all(r != "failed" for r in results))
        if results and all(r != "failed" for r in results):
            if any(r == "downloaded" for r in results):
                kinds = {f.kind for f in post.files}
                key = {
                    "story": "engine.downloaded_story",
                    "highlight": "engine.downloaded_highlight",
                    "avatar": "engine.downloaded_avatar",
                }.get(post.kind) or ("engine.downloaded_gallery" if post.is_gallery
                                     else "engine.downloaded_video" if "video" in kinds else "engine.downloaded_image")
            else:
                key = "engine.already_had"
            eng.log("ok", msg(key, name=post.stem))

    async def _download_file(self, folder: Path, media_file: MediaFile) -> str:
        """Sonuç: "downloaded", "skipped" (zaten vardı) ya da "failed"."""
        eng = self.engine
        stem_path = folder / Path(media_file.rel_stem)
        if existing_file(stem_path, media_file.ext):
            eng.files_skipped += 1
            return "skipped"
        if media_file.legacy_stem:
            # 1.0.x sürümü gönderileri doğrudan kişinin klasörüne koyuyordu: yeni yerine taşı.
            old = existing_file(folder / media_file.legacy_stem, media_file.ext)
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
        eng.log("error", msg("engine.download_failed", name=stem_path.name, error=result.error or ""))
        return "failed"
