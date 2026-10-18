"""Gerçek bir tarayıcı (Edge/Chrome) penceresi üzerinden Instagram verisini toplar.

Instagram, programların doğrudan isteklerini çabuk kısıtlıyor ve kullanıcı adı/şifreyle
program içinden girişi artık kabul etmiyor. Bu yüzden instagram.com'u gerçek bir
tarayıcıda açıyoruz: giriş, doğrulama ve çerezler tamamen tarayıcıda kalıyor. Veriyi de
sitenin kendi kullandığı adreslerden, sayfanın içinden istiyoruz.

Bir yol çalışmazsa sıradakine geçilir:
  1. Instagram'ın web API'si (giriş yapıldıysa en hızlı ve eksiksiz yol)
  2. Sitenin GraphQL sorgusu
  3. Profil sayfasını kaydırıp sayfanın kendi yüklediği gönderileri yakalamak
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlencode, urlsplit

from . import links, media, winbrowsers
from .i18n import Msg, msg

APP_ID = "936619743392459"
ASBD_ID = "129477"
# Sitenin profil gönderileri için kullandığı GraphQL sorguları (Instaloader 2026-10'da bunları kullanıyor).
DOC_FEED_LOGGED_IN = "28975909992013618"
DOC_FEED_LOGGED_OUT = "7950326061742207"

API_JS = r"""async ([url, method, form, appId, asbd, timeoutMs]) => {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), timeoutMs);
  const m = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
  let claim = '0';
  try { claim = sessionStorage.getItem('www-claim-v2') || '0'; } catch (e) {}
  const headers = {
    'Accept': '*/*', 'X-IG-App-ID': appId, 'X-ASBD-ID': asbd, 'X-IG-WWW-Claim': claim,
    'X-Requested-With': 'XMLHttpRequest', 'X-CSRFToken': m ? decodeURIComponent(m[1]) : ''
  };
  const opts = { method, headers, credentials: 'include', signal: ctl.signal, redirect: 'follow' };
  if (form) {
    headers['Content-Type'] = 'application/x-www-form-urlencoded';
    opts.body = new URLSearchParams(form).toString();
  }
  try {
    const r = await fetch(url, opts);
    const newClaim = r.headers.get('x-ig-set-www-claim');
    if (newClaim) { try { sessionStorage.setItem('www-claim-v2', newClaim); } catch (e) {} }
    const text = await r.text();
    return { status: r.status, url: r.url, redirected: r.redirected, ctype: r.headers.get('content-type') || '', text };
  } catch (e) {
    return { status: 0, url, redirected: false, ctype: '', text: '', error: String(e && e.name === 'AbortError' ? 'timeout' : e) };
  } finally {
    clearTimeout(timer);
  }
}"""

PAGE_DATA_JS = r"""() => {
  const out = [];
  for (const s of document.querySelectorAll('script[type="application/json"]')) {
    const t = s.textContent || '';
    if (t.length > 50 && (t.indexOf('username') >= 0 || t.indexOf('image_versions2') >= 0 || t.indexOf('display_url') >= 0)) out.push(t);
  }
  return out;
}"""

_CHALLENGE_PATHS = ("/challenge", "/checkpoint", "/auth_platform", "/accounts/suspended", "/accounts/disabled")
_LOGIN_PATHS = ("/accounts/login", "/accounts/signup")


class BrowserError(Exception):
    """Kullanıcıya gösterilecek, çevrilebilir açıklamalı hata."""

    def __init__(self, message: Msg) -> None:
        super().__init__(message.key)
        self.message = message

    def __str__(self) -> str:
        return self.message.render()


class LoginRequired(BrowserError):
    def __init__(self, message: Optional[Msg] = None) -> None:
        super().__init__(message or msg("browser.login_required"))


class RateLimited(BrowserError):
    def __init__(self) -> None:
        super().__init__(msg("browser.rate_limited"))


class NotFound(BrowserError):
    def __init__(self, message: Optional[Msg] = None) -> None:
        super().__init__(message or msg("browser.not_found"))


class ApiError(BrowserError):
    """Beklenmeyen yanıt: bu yol işe yaramıyor, başka yol denenmeli."""

    def __init__(self, detail: str) -> None:
        super().__init__(msg("browser.api_error", detail=detail))
        self.detail = detail


def base_url() -> str:
    return os.environ.get("INSTAGRAM_INDIRICI_BASE", "https://www.instagram.com").rstrip("/")


def rebase(url: str) -> str:
    """Testlerde gerçek Instagram yerine sahte sunucuya yönlendirmek için."""
    base = base_url()
    if base == "https://www.instagram.com":
        return url
    return re.sub(r"^https?://(www\.)?instagram\.com", base, url)


def _delay_range() -> Tuple[float, float]:
    raw = os.environ.get("INSTAGRAM_INDIRICI_DELAY", "2.5-5")
    try:
        lo, _, hi = raw.partition("-")
        low = float(lo)
        high = float(hi or lo)
        return max(0.0, low), max(low, high)
    except ValueError:
        return 2.5, 5.0


def _backoff_steps() -> List[float]:
    raw = os.environ.get("INSTAGRAM_INDIRICI_BACKOFF", "45,90,180,300")
    try:
        return [float(x) for x in raw.split(",") if x.strip()]
    except ValueError:
        return [45, 90, 180, 300]


def parse_json_text(text: str) -> Any:
    text = (text or "").strip()
    if text.startswith("for (;;);"):
        text = text[len("for (;;);"):]
    if not text or text[0] not in "{[":
        return None
    try:
        return json.loads(text)
    except ValueError:
        # Bazı GraphQL yanıtları birden çok JSON satırı döner; ilkini al.
        first = text.split("\n", 1)[0]
        try:
            return json.loads(first)
        except ValueError:
            return None


@dataclass
class ApiResult:
    status: int
    url: str
    redirected: bool
    text: str
    data: Any = None
    error: str = ""


@dataclass
class ProfileInfo:
    username: str
    user_id: str = ""
    full_name: str = ""
    is_private: bool = False
    followed: bool = False
    media_count: int = -1
    user: Dict[str, Any] = field(default_factory=dict)
    first_items: List[Dict[str, Any]] = field(default_factory=list)
    end_cursor: str = ""
    has_next: Optional[bool] = None
    sources: List[str] = field(default_factory=list)

    @property
    def locked(self) -> bool:
        return self.is_private and not self.followed


@dataclass
class FeedResult:
    collected: int = 0
    reason: str = "end"     # end | limit | cancel | login_wall | rate_limited | up_to_date | stalled
    via: str = ""


def classify(res: ApiResult) -> str:
    """Bir API yanıtının ne anlama geldiğini söyler: ok | login | challenge | rate | notfound | error."""
    path = urlsplit(res.url or "").path.lower()
    data = res.data if isinstance(res.data, dict) else {}
    message = str(data.get("message") or "").lower()
    if any(p in path for p in _CHALLENGE_PATHS) or message in ("challenge_required", "checkpoint_required") \
            or data.get("checkpoint_url") or (isinstance(data.get("challenge"), dict) and not data.get("items")):
        return "challenge"
    if res.status == 429 or "wait a few minutes" in message or "please wait" in message \
            or message in ("feedback_required", "rate_limit_error") or data.get("spam") is True:
        return "rate"
    if any(p in path for p in _LOGIN_PATHS) or message in ("login_required", "user_has_logged_out") \
            or data.get("require_login") is True:
        return "login"
    if res.status == 404:
        return "notfound"
    if res.status in (401, 403):
        return "login"
    if res.status == 200 and isinstance(res.data, (dict, list)):
        if isinstance(res.data, dict) and res.data.get("status") == "fail":
            return "error"
        return "ok"
    return "error"


class InstagramBrowser:
    def __init__(
        self,
        data_dir: Path,
        *,
        log: Callable[[str, Any], None],
        cancel: threading.Event,
        on_verify: Callable[[bool], None],
    ) -> None:
        self.data_dir = data_dir
        self.log = log
        self.cancel = cancel
        self.on_verify = on_verify
        self._pw = None
        self.context = None
        self.page = None
        self.user_agent = ""
        self.channel_name = ""
        self._last_call = 0.0
        self.api_calls = 0

    # ------------------------------------------------------------ açılış
    async def __aenter__(self) -> "InstagramBrowser":
        winbrowsers.prepare_playwright_env()
        try:
            from playwright.async_api import async_playwright
        except Exception as exc:  # pragma: no cover
            raise BrowserError(msg("browser.load_failed", error=str(exc)))

        self._pw = await async_playwright().start()
        errors: List[str] = []
        for channel in self._channel_order():
            if self.cancel.is_set():
                break
            if await self._try_launch(channel, errors):
                break
            if channel == "chromium" and winbrowsers.bundled_chromium_exe() is None:
                ok = await asyncio.to_thread(winbrowsers.install_bundled_chromium, self.log)
                if ok and await self._try_launch(channel, errors):
                    break
        if self.context is None:
            await self._close_pw()
            raise BrowserError(msg("browser.none", detail=" | ".join(errors)))

        await self.context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
        pages = self.context.pages
        self.page = pages[0] if pages else await self.context.new_page()
        for extra in pages[1:]:
            try:
                await extra.close()
            except Exception:
                pass
        try:
            self.user_agent = await self.page.evaluate("navigator.userAgent")
        except Exception:
            self.user_agent = ""
        self.log("info", msg("browser.opened", name=self.channel_name))
        return self

    def _channel_order(self) -> List[str]:
        wanted = os.environ.get("INSTAGRAM_INDIRICI_BROWSER", "")
        if wanted:
            return [wanted]
        order: List[str] = []
        if winbrowsers.healthy_edge():
            order.append("msedge")
        elif winbrowsers.find_edge():
            self.log("warn", msg("browser.edge_broken"))
        if winbrowsers.healthy_chrome():
            order.append("chrome")
        order.append("chromium")
        return order

    async def _try_launch(self, channel: str, errors: List[str]) -> bool:
        headless = os.environ.get("INSTAGRAM_INDIRICI_HEADLESS") == "1"
        profile_dir = self.data_dir / f"tarayici-{channel or 'chromium'}"
        profile_dir.mkdir(parents=True, exist_ok=True)
        kwargs: Dict[str, Any] = dict(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled", "--no-first-run",
                  "--no-default-browser-check", "--window-size=1100,860"],
            ignore_default_args=["--enable-automation"],
            no_viewport=True,
            locale="tr-TR",
            accept_downloads=False,
            timeout=60000,
        )
        if channel and channel != "chromium":
            kwargs["channel"] = channel
        elif not os.environ.get("INSTAGRAM_INDIRICI_BROWSER"):
            exe = winbrowsers.bundled_chromium_exe()
            if not exe:
                errors.append("chromium: not downloaded")
                return False
            kwargs["executable_path"] = exe
        try:
            self.context = await self._pw.chromium.launch_persistent_context(str(profile_dir), **kwargs)
        except Exception as exc:
            text = str(exc)
            errors.append(f"{channel}: {text.splitlines()[0] if text else exc}")
            if "ProcessSingleton" in text or "already in use" in text:
                await self._close_pw()
                raise BrowserError(msg("browser.profile_in_use"))
            return False
        self.channel_name = {"msedge": "Microsoft Edge", "chrome": "Google Chrome"}.get(channel, "Chromium")
        return True

    async def __aexit__(self, *exc) -> None:
        await self.close()

    async def close(self) -> None:
        if self.context is not None:
            try:
                await self.context.close()
            except Exception:
                pass
            self.context = None
        await self._close_pw()

    async def _close_pw(self) -> None:
        if self._pw is not None:
            try:
                await self._pw.stop()
            except Exception:
                pass
            self._pw = None

    @property
    def closed(self) -> bool:
        return self.page is None or self.page.is_closed()

    def _check_alive(self) -> None:
        if self.closed:
            raise BrowserError(msg("browser.closed"))

    def _check_cancel(self) -> None:
        if self.cancel.is_set():
            raise asyncio.CancelledError()

    async def _sleep(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        while True:
            self._check_cancel()
            self._check_alive()
            left = end - time.monotonic()
            if left <= 0:
                return
            await asyncio.sleep(min(0.5, left))

    # ------------------------------------------------------------ oturum
    async def logged_in(self) -> bool:
        if self.context is None:
            return False
        host = urlsplit(base_url()).hostname or ""
        try:
            cookies = await self.context.cookies([base_url() + "/"])
        except Exception:
            return False
        for c in cookies:
            if c.get("name") == "sessionid" and c.get("value") and host.endswith(str(c.get("domain") or "").lstrip(".")):
                return True
        return False

    # ------------------------------------------------------------ sayfa gezinme
    async def goto(self, url: str) -> int:
        """Sayfayı açar; HTTP durum kodunu döner (bilinmiyorsa 0)."""
        self._check_alive()
        try:
            response = await self.page.goto(rebase(url), wait_until="domcontentloaded", timeout=60000)
            return response.status if response is not None else 0
        except Exception as exc:
            if self.cancel.is_set():
                raise asyncio.CancelledError()
            if self.page.is_closed():
                raise BrowserError(msg("browser.closed"))
            message = str(exc).splitlines()[0]
            if "ERR_INTERNET_DISCONNECTED" in message or "ERR_NAME_NOT_RESOLVED" in message:
                raise BrowserError(msg("browser.no_internet"))
            if "Timeout" not in message:
                raise BrowserError(msg("browser.page_failed", error=message))
            self.log("warn", msg("browser.slow"))
            return 0

    def _page_path(self) -> str:
        try:
            return urlsplit(self.page.url).path.lower()
        except Exception:
            return ""

    async def _ensure_on_site(self) -> None:
        self._check_alive()
        if not self.page.url.startswith(base_url()):
            await self.goto(base_url() + "/")

    async def _page_redirect_state(self) -> str:
        path = self._page_path()
        if any(p in path for p in _CHALLENGE_PATHS):
            return "challenge"
        if any(p in path for p in _LOGIN_PATHS):
            return "login"
        return ""

    async def wait_for_verification(self, target_url: str = "", timeout: float = 900) -> None:
        """Instagram doğrulama (challenge) istediğinde kullanıcının tamamlamasını bekler."""
        self.on_verify(True)
        self.log("warn", msg("browser.verify"))
        try:
            if target_url:
                if target_url.startswith("/"):
                    target_url = base_url() + target_url
                await self.goto(target_url)
            try:
                await self.page.bring_to_front()
            except Exception:
                pass
            start = time.monotonic()
            calm_since = 0.0
            while time.monotonic() - start < timeout:
                await self._sleep(1.5)
                if await self._page_redirect_state():
                    calm_since = 0.0
                    continue
                if not calm_since:
                    calm_since = time.monotonic()
                elif time.monotonic() - calm_since > 3:
                    self.log("ok", msg("browser.verify_done"))
                    return
            raise BrowserError(msg("browser.verify_timeout"))
        finally:
            self.on_verify(False)

    # ------------------------------------------------------------ API
    async def _raw_api(self, path: str, *, params: Optional[List[Tuple[str, str]]] = None,
                       form: Optional[Dict[str, str]] = None, timeout: float = 45) -> ApiResult:
        await self._ensure_on_site()
        url = path if path.startswith("http") else base_url() + path
        if params:
            url += ("&" if "?" in url else "?") + urlencode(params)
        # İstekleri insan hızında tut: Instagram art arda gelen istekleri kısıtlıyor.
        low, high = _delay_range()
        wait = self._last_call + random.uniform(low, high) - time.monotonic()
        if self._last_call and wait > 0:
            await self._sleep(wait)
        self._last_call = time.monotonic()
        self.api_calls += 1
        try:
            raw = await asyncio.wait_for(
                self.page.evaluate(API_JS, [url, "POST" if form else "GET", form, APP_ID, ASBD_ID, int(timeout * 1000)]),
                timeout + 15,
            )
        except asyncio.TimeoutError:
            raw = {"status": 0, "url": url, "redirected": False, "text": "", "error": "timeout"}
        except Exception as exc:
            if self.cancel.is_set():
                raise asyncio.CancelledError()
            self._check_alive()
            # Sayfa tam o sırada başka adrese geçtiyse (ör. doğrulama sayfası) bir kez daha dene.
            raw = {"status": 0, "url": url, "redirected": False, "text": "", "error": str(exc).splitlines()[0]}
        text = str(raw.get("text") or "")
        return ApiResult(int(raw.get("status") or 0), str(raw.get("url") or url), bool(raw.get("redirected")),
                         text, parse_json_text(text), str(raw.get("error") or ""))

    async def call(self, path: str, *, params: Optional[List[Tuple[str, str]]] = None,
                   form: Optional[Dict[str, str]] = None, what: str = "") -> Any:
        """API çağrısı: hız sınırında bekleyip tekrar dener, doğrulama isterse kullanıcıyı bekler."""
        backoff = _backoff_steps()
        attempt = 0
        connection_errors = 0
        verified = False
        while True:
            self._check_cancel()
            res = await self._raw_api(path, params=params, form=form)
            kind = classify(res)
            if os.environ.get("INSTAGRAM_INDIRICI_DEBUG"):
                self.log("debug", f"API {path} -> {res.status} {kind} {res.text[:200]!r}")
            if kind == "ok":
                return res.data
            if res.status == 0 and kind == "error":
                connection_errors += 1
                if connection_errors <= 2:
                    self.log("warn", msg("browser.retrying", detail=res.error or "?"))
                    await self._sleep(5 * connection_errors)
                    continue
                raise ApiError(f"{what or path}: {res.error or 'no response'}")
            if kind == "rate":
                if attempt >= len(backoff):
                    raise RateLimited()
                seconds = backoff[attempt]
                attempt += 1
                self.log("warn", msg("browser.slow_down", s=int(seconds)))
                await self._sleep(seconds)
                continue
            if kind == "challenge":
                if verified:
                    raise BrowserError(msg("browser.verify_failed"))
                data = res.data if isinstance(res.data, dict) else {}
                target = str(data.get("checkpoint_url") or ((data.get("challenge") or {}).get("url") if isinstance(data.get("challenge"), dict) else "") or "")
                if not target and any(p in urlsplit(res.url).path.lower() for p in _CHALLENGE_PATHS):
                    target = res.url
                await self.wait_for_verification(target or base_url() + "/")
                verified = True
                continue
            if kind == "login":
                raise LoginRequired()
            if kind == "notfound":
                raise NotFound()
            snippet = res.text[:160].replace("\n", " ")
            raise ApiError(f"{what or path}: HTTP {res.status} {snippet}")

    # ------------------------------------------------------------ profil
    async def _embedded_data(self) -> List[Any]:
        try:
            blobs = await self.page.evaluate(PAGE_DATA_JS)
        except Exception:
            return []
        out = []
        for text in blobs or []:
            data = parse_json_text(text)
            if data is not None:
                out.append(data)
        return out

    async def _page_html(self) -> str:
        try:
            return await self.page.content()
        except Exception:
            return ""

    async def open_profile(self, username: str, *, logged_in: bool) -> ProfileInfo:
        info = ProfileInfo(username=username)
        self.log("info", msg("browser.profile_opening", name=username))
        status = await self.goto(f"https://www.instagram.com/{username}/")
        await self._sleep(1.5)
        state = await self._page_redirect_state()
        if state == "challenge":
            await self.wait_for_verification()
            status = await self.goto(f"https://www.instagram.com/{username}/")
            await self._sleep(1.5)
            state = await self._page_redirect_state()
        if state == "login" and not logged_in:
            # Instagram bazen giriş yapmayanlara profili hiç göstermiyor.
            raise LoginRequired(msg("browser.profile_login_required"))
        if state == "login":
            raise LoginRequired(msg("browser.session_expired"))

        # 1) Sayfaya gömülü veri
        for data in await self._embedded_data():
            user = media.find_user(data, username)
            if user:
                self._merge_user(info, user)
                if "page" not in info.sources:
                    info.sources.append("page")
            self._add_first_items(info, data)
        html = ""
        if not info.user_id:
            html = await self._page_html()
            m = re.search(r'"profile_id"\s*:\s*"(\d+)"', html) or re.search(r'"user_id"\s*:\s*"(\d+)"', html)
            if m:
                info.user_id = m.group(1)
                info.sources.append("html")

        # 2) Profil bilgisi isteği (sayılar, gizlilik, ilk gönderiler)
        try:
            data = await self.call("/api/v1/users/web_profile_info/", params=[("username", username)], what="profile")
            user = ((data or {}).get("data") or {}).get("user") if isinstance(data, dict) else None
            if isinstance(user, dict):
                self._merge_user(info, user)
                self._add_first_items(info, user)
                info.sources.append("web_profile_info")
            elif isinstance(data, dict) and isinstance(data.get("data"), dict) and data["data"].get("user") is None and not info.user_id:
                raise NotFound(msg("browser.user_not_found", name=username))
        except NotFound:
            if not info.user_id:
                raise NotFound(msg("browser.user_not_found", name=username))
        except (ApiError, LoginRequired) as exc:
            self.log("debug", f"web_profile_info: {exc.message.key}")

        # 3) Arama (kimlik hâlâ yoksa)
        if not info.user_id:
            try:
                data = await self.call("/web/search/topsearch/", params=[("query", username)], what="search")
                user = media.find_user(data, username)
                if user:
                    self._merge_user(info, user)
                    info.sources.append("search")
            except (ApiError, LoginRequired, NotFound) as exc:
                self.log("debug", f"topsearch: {exc.message.key}")

        if not info.user_id:
            if status == 404 or "page not found" in (html or "").lower()[:4000]:
                raise NotFound(msg("browser.user_not_found", name=username))
            raise BrowserError(msg("browser.profile_failed", name=username))
        return info

    def _merge_user(self, info: ProfileInfo, user: Dict[str, Any]) -> None:
        merged = dict(user)
        merged.update({k: v for k, v in info.user.items() if v not in (None, "", [], {})})
        merged.update({k: v for k, v in user.items() if v not in (None, "", [], {})})
        info.user = merged
        info.user_id = info.user_id or media.user_id_of(user)
        info.full_name = info.full_name or str(user.get("full_name") or "")
        if "is_private" in user:
            info.is_private = bool(user.get("is_private"))
        friendship = user.get("friendship_status") if isinstance(user.get("friendship_status"), dict) else {}
        if user.get("followed_by_viewer") or friendship.get("following"):
            info.followed = True
        count = user.get("media_count")
        if count is None and isinstance(user.get("edge_owner_to_timeline_media"), dict):
            count = user["edge_owner_to_timeline_media"].get("count")
        try:
            if count is not None:
                info.media_count = int(count)
        except (TypeError, ValueError):
            pass

    def _add_first_items(self, info: ProfileInfo, data: Any) -> None:
        seen = {media.media_key(i) for i in info.first_items}
        for item in media.find_media(data):
            owner = media.owner_of(item)
            name = str(owner.get("username") or "").lower()
            if name and name != info.username:
                continue
            key = media.media_key(item)
            if key and key not in seen:
                seen.add(key)
                info.first_items.append(item)
        edge = data.get("edge_owner_to_timeline_media") if isinstance(data, dict) else None
        page_info = edge.get("page_info") if isinstance(edge, dict) else None
        if isinstance(page_info, dict):
            info.has_next = bool(page_info.get("has_next_page"))
            info.end_cursor = str(page_info.get("end_cursor") or "")

    async def avatar_urls(self, info: ProfileInfo, *, logged_in: bool) -> List[str]:
        urls: List[str] = []
        if logged_in and info.user_id:
            try:
                data = await self.call(f"/api/v1/users/{info.user_id}/info/", what="avatar")
                user = (data or {}).get("user") if isinstance(data, dict) else None
                if isinstance(user, dict):
                    urls.extend(media.avatar_urls(user))
                    if user.get("profile_pic_id") and not info.user.get("profile_pic_id"):
                        info.user["profile_pic_id"] = user["profile_pic_id"]
            except (ApiError, LoginRequired, NotFound) as exc:
                self.log("debug", f"user info: {exc.message.key}")
        urls.extend(media.avatar_urls(info.user))
        out: List[str] = []
        for u in urls:
            if u not in out:
                out.append(u)
        return out

    # ------------------------------------------------------------ gönderiler
    async def collect_posts(
        self,
        info: ProfileInfo,
        *,
        logged_in: bool,
        limit: int,
        on_page: Callable[[List[Dict[str, Any]]], bool],
    ) -> FeedResult:
        """Profilin gönderilerini sayfa sayfa toplar. on_page True dönerse (hepsi zaten var) durur."""
        state = _FeedState(limit=limit, on_page=on_page)
        result = FeedResult()

        def finish(reason: str, via: str) -> FeedResult:
            result.reason = "limit" if state.limit_hit else reason
            result.collected = len(state.seen)
            result.via = via
            return result

        # İlk sayfa elimizde varsa (sayfaya gömülü ya da profil bilgisiyle gelen) önce onu ver.
        if not logged_in and info.first_items:
            if state.accept(info.first_items):
                return finish("up_to_date", "page")
            if state.done:
                return finish("end", "page")

        strategies: List[Tuple[str, Callable[[], Any]]] = []
        if logged_in:
            strategies.append(("rest", lambda: self._feed_rest(info, state)))
            strategies.append(("graphql", lambda: self._feed_graphql(info, state, logged_in=True)))
        else:
            strategies.append(("graphql", lambda: self._feed_graphql(info, state, logged_in=False)))
        strategies.append(("scroll", lambda: self._feed_scroll(info, state)))

        last_reason = "stalled"
        for name, run in strategies:
            self._check_cancel()
            try:
                reason = await run()
            except ApiError as exc:
                self.log("info", msg("browser.strategy_failed", detail=exc.detail))
                continue
            except LoginRequired:
                if logged_in:
                    raise LoginRequired(msg("browser.session_expired"))
                last_reason = "login_wall"
                continue
            if reason in ("end", "up_to_date", "limit"):
                return finish(reason, name)
            last_reason = reason
            if reason == "login_wall" and name != "graphql":
                break
        if not logged_in and (info.media_count < 0 or len(state.seen) < info.media_count):
            return finish("login_wall", "")
        return finish(last_reason if state.seen else "stalled", "")

    async def _feed_rest(self, info: ProfileInfo, state: "_FeedState") -> str:
        max_id = ""
        pages = 0
        while True:
            self._check_cancel()
            params = [("count", "30")]
            if max_id:
                params.append(("max_id", max_id))
            data = await self.call(f"/api/v1/feed/user/{info.user_id}/", params=params, what="feed")
            if not isinstance(data, dict) or "items" not in data:
                raise ApiError("feed: no items")
            pages += 1
            items = [i for i in data.get("items") or [] if media.is_media(i)]
            if state.accept(items):
                return "up_to_date"
            if state.done:
                return "limit"
            next_id = str(data.get("next_max_id") or "")
            if not data.get("more_available") or not next_id or next_id == max_id:
                return "end"
            max_id = next_id

    async def _feed_graphql(self, info: ProfileInfo, state: "_FeedState", *, logged_in: bool) -> str:
        cursor = info.end_cursor if not logged_in else ""
        if not logged_in and info.has_next is False and state.seen:
            return "end"
        pages = 0
        while True:
            self._check_cancel()
            if logged_in:
                variables: Dict[str, Any] = {
                    "data": {"count": 12, "include_reel_media_seen_timestamp": True,
                             "include_relationship_info": True, "latest_besties_reel_media": True,
                             "latest_reel_media": True},
                    "username": info.username,
                    "__relay_internal__pv__PolarisFeedShareMenurelayprovider": False,
                }
                doc_id = DOC_FEED_LOGGED_IN
            else:
                variables = {
                    "data": {"count": 12, "include_relationship_info": True,
                             "latest_besties_reel_media": True, "latest_reel_media": True},
                    "id": info.user_id,
                    "__relay_internal__pv__PolarisFeedShareMenurelayprovider": False,
                }
                doc_id = DOC_FEED_LOGGED_OUT
            if cursor:
                variables.update({"after": cursor, "before": None, "first": 12, "last": None})
            else:
                variables["first"] = 12
            form = {"doc_id": doc_id, "variables": json.dumps(variables, separators=(",", ":")),
                    "server_timestamps": "true"}
            data = await self.call("/graphql/query", form=form, what="graphql")
            if isinstance(data, dict) and data.get("errors") and not data.get("data"):
                raise ApiError("graphql: " + str(data.get("errors"))[:120])
            items = media.find_media(data)
            page_info = media.find_page_info(data)
            if page_info is None and not items:
                raise ApiError("graphql: no posts in response")
            pages += 1
            if state.accept(items):
                return "up_to_date"
            if state.done:
                return "limit"
            if not page_info or not page_info.get("has_next_page"):
                return "end"
            next_cursor = str(page_info.get("end_cursor") or "")
            if not next_cursor or next_cursor == cursor:
                return "end" if items else "login_wall"
            if not items and pages > 1:
                return "login_wall"
            cursor = next_cursor

    async def _feed_scroll(self, info: ProfileInfo, state: "_FeedState") -> str:
        """Son çare: profil sayfasını kaydırıp sayfanın kendi yüklediği gönderileri yakalar."""
        idle_limit = float(os.environ.get("INSTAGRAM_INDIRICI_IDLE", "25"))
        page = self.page
        tasks: List[asyncio.Task] = []
        box: Dict[str, Any] = {"up_to_date": False, "last": time.monotonic(), "responses": 0}
        base = base_url()

        async def handle(response) -> None:
            url = response.url
            if not url.startswith(base) or ("/graphql" not in url and "/api/v1/" not in url):
                return
            try:
                text = await response.text()
            except Exception:
                return
            data = parse_json_text(text)
            if data is None:
                return
            box["responses"] += 1
            items = [i for i in media.find_media(data)
                     if str(media.owner_of(i).get("username") or info.username).lower() == info.username]
            before = len(state.seen)
            if state.accept(items):
                box["up_to_date"] = True
            if len(state.seen) > before:
                box["last"] = time.monotonic()

        def on_response(response) -> None:
            tasks.append(asyncio.ensure_future(handle(response)))

        self.log("info", msg("browser.scrolling"))
        page.on("response", on_response)
        try:
            await self.goto(f"https://www.instagram.com/{info.username}/")
            await self._sleep(2)
            for data in await self._embedded_data():
                items = [i for i in media.find_media(data)
                         if str(media.owner_of(i).get("username") or info.username).lower() == info.username]
                if state.accept(items):
                    return "up_to_date"
            box["last"] = time.monotonic()
            while True:
                self._check_cancel()
                self._check_alive()
                if box["up_to_date"]:
                    return "up_to_date"
                if state.done:
                    return "limit"
                if await self._page_redirect_state() == "challenge":
                    await self.wait_for_verification()
                    box["last"] = time.monotonic()
                    continue
                try:
                    await page.mouse.wheel(0, 2400)
                    await page.evaluate("window.scrollBy(0, 2400)")
                except Exception:
                    pass
                await self._sleep(1.5)
                if time.monotonic() - box["last"] > idle_limit:
                    if info.media_count >= 0 and len(state.seen) >= info.media_count:
                        return "end"
                    return "login_wall" if state.seen else "stalled"
        finally:
            page.remove_listener("response", on_response)
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

    # ------------------------------------------------------------ hikayeler ve öne çıkanlar
    @staticmethod
    def _reels_from(data: Any) -> List[Dict[str, Any]]:
        if not isinstance(data, dict):
            return []
        reels = data.get("reels_media")
        if isinstance(reels, list):
            return [r for r in reels if isinstance(r, dict)]
        reels = data.get("reels")
        if isinstance(reels, dict):
            return [r for r in reels.values() if isinstance(r, dict)]
        return []

    async def stories(self, user_id: str) -> List[Dict[str, Any]]:
        data = await self.call("/api/v1/feed/reels_media/", params=[("reel_ids", user_id)], what="stories")
        items: List[Dict[str, Any]] = []
        for reel in self._reels_from(data):
            items.extend(i for i in reel.get("items") or [] if media.is_media(i))
        return items

    async def highlights_tray(self, user_id: str) -> List[Dict[str, Any]]:
        data = await self.call(f"/api/v1/highlights/{user_id}/highlights_tray/", what="highlights")
        tray = data.get("tray") if isinstance(data, dict) else None
        if not isinstance(tray, list):
            raise ApiError("highlights: no tray")
        out = []
        for entry in tray:
            if isinstance(entry, dict) and entry.get("id"):
                hid = str(entry["id"])
                out.append({"id": hid if hid.startswith("highlight:") else f"highlight:{hid}",
                            "title": str(entry.get("title") or "")})
        return out

    async def highlight_reels(self, reel_ids: List[str]) -> List[Dict[str, Any]]:
        """Öne çıkanların içeriği: [{"id", "title", "items", "user"}]."""
        out: List[Dict[str, Any]] = []
        for start in range(0, len(reel_ids), 5):
            self._check_cancel()
            chunk = reel_ids[start:start + 5]
            data = await self.call("/api/v1/feed/reels_media/", params=[("reel_ids", r) for r in chunk],
                                   what="highlights")
            for reel in self._reels_from(data):
                out.append({
                    "id": str(reel.get("id") or ""),
                    "title": str(reel.get("title") or ""),
                    "user": reel.get("user") if isinstance(reel.get("user"), dict) else {},
                    "items": [i for i in reel.get("items") or [] if media.is_media(i)],
                })
        return out

    # ------------------------------------------------------------ tek gönderi
    async def get_post(self, shortcode: str, *, logged_in: bool) -> Dict[str, Any]:
        media_id = links.id_from_shortcode(shortcode)
        if logged_in and media_id:
            try:
                data = await self.call(f"/api/v1/media/{media_id}/info/", what="post")
                items = [i for i in (data or {}).get("items") or [] if media.is_media(i)] if isinstance(data, dict) else []
                if items:
                    return items[0]
            except NotFound:
                raise NotFound(msg("browser.post_not_found"))
            except (ApiError, LoginRequired) as exc:
                self.log("debug", f"media info: {exc.message.key}")
        return await self._post_from_page(shortcode)

    async def _post_from_page(self, shortcode: str) -> Dict[str, Any]:
        found: Dict[str, Any] = {}
        tasks: List[asyncio.Task] = []
        base = base_url()

        def match(data: Any) -> None:
            for item in media.find_media(data):
                if media.media_key(item) == shortcode or str(item.get("code") or item.get("shortcode") or "") == shortcode:
                    found.setdefault("item", item)

        async def handle(response) -> None:
            if not response.url.startswith(base):
                return
            try:
                data = parse_json_text(await response.text())
            except Exception:
                return
            if data is not None:
                match(data)

        def on_response(response) -> None:
            tasks.append(asyncio.ensure_future(handle(response)))

        page = self.page
        page.on("response", on_response)
        try:
            self.log("info", msg("browser.post_opening"))
            status = await self.goto(f"https://www.instagram.com/p/{shortcode}/")
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                self._check_cancel()
                state = await self._page_redirect_state()
                if state == "challenge":
                    await self.wait_for_verification()
                    await self.goto(f"https://www.instagram.com/p/{shortcode}/")
                    deadline = time.monotonic() + 20
                    continue
                if state == "login":
                    raise LoginRequired(msg("browser.post_login_required"))
                for data in await self._embedded_data():
                    match(data)
                if "item" in found:
                    return found["item"]
                if status == 404:
                    break
                await self._sleep(1)
        finally:
            page.remove_listener("response", on_response)
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
        if "item" in found:
            return found["item"]
        raise NotFound(msg("browser.post_not_found"))

    async def resolve_share(self, url: str) -> links.ParsedLink:
        self.log("info", msg("browser.share_opening"))
        await self.goto(url)
        for _ in range(30):
            current = self.page.url
            if base_url() != "https://www.instagram.com":
                current = re.sub("^" + re.escape(base_url()), "https://www.instagram.com", current)
            parsed = links.parse_link(current)
            if parsed.ok and parsed.kind != links.SHARE:
                return parsed
            if await self._page_redirect_state() == "login":
                raise LoginRequired(msg("browser.post_login_required"))
            await self._sleep(0.5)
        raise BrowserError(msg("browser.share_failed"))

    # ------------------------------------------------------------ indirme yedeği
    async def download_via_browser(self, url: str, headers: Dict[str, str]) -> Optional[tuple]:
        """Normal indirme reddedilirse tarayıcının kendi bağlantısıyla dener."""
        if self.context is None:
            return None
        try:
            response = await self.context.request.get(url, headers=headers, timeout=120000)
            if response.ok:
                body = await response.body()
                content_type = response.headers.get("content-type", "")
                if body and not content_type.startswith(("text/", "application/json")):
                    return body, content_type
        except Exception:
            return None
        return None

    # ------------------------------------------------------------ giriş
    async def login_session(self) -> bool:
        """Kullanıcının Instagram'a giriş yapması için pencereyi açar. Giriş tamamlanınca True döner."""
        page = self.page
        already = await self.logged_in()
        await self.goto("https://www.instagram.com/accounts/login/")
        if already:
            self.log("ok", msg("browser.login_already"))
        else:
            self.log("info", msg("browser.login_open"))
        try:
            await page.bring_to_front()
        except Exception:
            pass
        seen_logged_in = 0.0
        while not page.is_closed() and not self.cancel.is_set():
            await asyncio.sleep(1)
            if page.is_closed():
                break
            if await self.logged_in() and not await self._page_redirect_state() and \
                    "two_factor" not in self._page_path():
                if not seen_logged_in:
                    seen_logged_in = time.monotonic()
                elif time.monotonic() - seen_logged_in > 3:
                    self.log("ok", msg("browser.login_success"))
                    return True
            else:
                seen_logged_in = 0.0
        return await self.logged_in() if self.context is not None and not self.closed else already


@dataclass
class _FeedState:
    limit: int
    on_page: Callable[[List[Dict[str, Any]]], bool]
    seen: Dict[str, bool] = field(default_factory=dict)
    limit_hit: bool = False

    @property
    def done(self) -> bool:
        return self.limit_hit

    def accept(self, items: List[Dict[str, Any]]) -> bool:
        """Yeni gönderileri motora verir. Motor "hepsi zaten indirilmiş" derse True döner."""
        new: List[Dict[str, Any]] = []
        for item in items:
            key = media.media_key(item)
            if not key or key in self.seen:
                continue
            if self.limit and len(self.seen) >= self.limit:
                self.limit_hit = True
                break
            self.seen[key] = True
            new.append(item)
        if self.limit and len(self.seen) >= self.limit:
            self.limit_hit = True
        if not new:
            return False
        return bool(self.on_page(new))
