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
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlencode, urlsplit

from . import links, media, winbrowsers
from .pacing import Pacing
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
    def __init__(self, what: str = "", status: int = 0) -> None:
        super().__init__(msg("browser.rate_limited"))
        self.what = what
        self.status = status


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


def _backoff_steps() -> List[float]:
    raw = os.environ.get("INSTAGRAM_INDIRICI_BACKOFF", "30,90")
    try:
        return [float(x) for x in raw.split(",") if x.strip()]
    except ValueError:
        return [30, 90]


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


_SESSION_VALUE = re.compile(r"^[A-Za-z0-9%:_.\-]{6,}$")
_SESSION_NAMES = ("sessionid", "csrftoken", "ds_user_id", "mid", "ig_did", "datr", "ig_nrcb")
_DEVICE_VALUE = re.compile(r"^[A-Za-z0-9%:_.\-]{1,200}$")   # mid / ig_did / datr: Instagram'ın cihazı tanıdığı çerezler


def parse_session_input(text: str) -> Dict[str, str]:
    """Kullanıcının yapıştırdığı metinden Instagram oturum çerezlerini çıkarır.

    Kabul edilen biçimler: yalın sessionid değeri, ``sessionid=...``, tüm ``Cookie:`` satırı ya da
    tarayıcı geliştirici araçlarından kopyalanan satır (``sessionid<TAB>değer<TAB>...``).
    Geçersizse boş sözlük döner. Değer asla günlüğe yazılmaz.
    """
    raw = (text or "").strip().strip("'\"").strip()
    if not raw:
        return {}
    found: Dict[str, str] = {}
    body = re.sub(r"^\s*cookie:\s*", "", raw, flags=re.I)
    if "=" in body:
        for part in re.split(r"[;\n]", body):
            if "=" not in part:
                continue
            key, value = part.split("=", 1)
            key = key.strip().lower()
            value = value.strip().strip("'\"")
            if key in _SESSION_NAMES and value:
                found[key] = value
    if "sessionid" not in found:
        m = re.match(r"^\s*sessionid\s+(\S+)", raw, flags=re.I)
        if m:
            found["sessionid"] = m.group(1)
    if "sessionid" not in found and re.fullmatch(r"[^\s;=]+", raw):
        found["sessionid"] = raw
    sid = found.get("sessionid", "").replace(":", "%3A")
    if not _SESSION_VALUE.match(sid):
        return {}
    found["sessionid"] = sid
    for key in _SESSION_NAMES[1:]:
        if key in found and not _DEVICE_VALUE.match(found[key]):
            del found[key]
    if "ds_user_id" not in found:
        m = re.match(r"^(\d{5,})%3A", sid)
        if m:
            found["ds_user_id"] = m.group(1)
    return {k: v for k, v in found.items() if k in _SESSION_NAMES}


def use_chromium_sandbox() -> bool:
    """Windows'ta tarayıcı normal (korumalı) başlar; Playwright'ın varsayılan `--no-sandbox` bayrağı
    Chrome'da "desteklenmeyen komut satırı işareti" uyarısı çıkarıp tarayıcıyı alışılmadık gösteriyordu.
    Linux'ta (geliştirme ortamı, root) kapalı kalır; `INSTAGRAM_INDIRICI_SANDBOX=0/1` ile değiştirilir."""
    forced = os.environ.get("INSTAGRAM_INDIRICI_SANDBOX", "")
    if forced in ("0", "1"):
        return forced == "1"
    return sys.platform == "win32"


class InstagramBrowser:
    def __init__(
        self,
        data_dir: Path,
        *,
        log: Callable[[str, Any], None],
        cancel: threading.Event,
        on_verify: Callable[[bool], None],
        pacing: Optional[Pacing] = None,
        identity: str = "desktop",
    ) -> None:
        self.pacing = pacing or Pacing.for_speed("normal").with_env()
        self.identity = identity
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
        self.list_pages = 0
        self.on_wait: Callable[[float], None] = lambda until: None
        self.pause_gate: Callable[[], Awaitable[None]] = self._no_pause

    @staticmethod
    async def _no_pause() -> None:
        return None

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
        await self._apply_identity()
        self.log("info", msg("browser.opened", name=self.channel_name))
        return self

    async def _apply_identity(self) -> None:
        """"Android telefon" / "Tablet" seçiliyse Instagram'a o cihazmış gibi görün (tarayıcı kimliği).

        "Bilgisayar" seçiliyken hiçbir şey değişmez: tarayıcının kendi kimliği kullanılır.
        """
        if self.identity not in ("android", "tablet") or not self.user_agent:
            return
        from .identity import user_agent_for
        ua = user_agent_for(self.identity, self.user_agent)
        try:
            session = await self.context.new_cdp_session(self.page)
            await session.send("Network.setUserAgentOverride", {
                "userAgent": ua,
                "platform": "Linux armv8l",
                "userAgentMetadata": {
                    "brands": [{"brand": "Chromium", "version": "130"}, {"brand": "Not?A_Brand", "version": "99"}],
                    "fullVersion": "130.0.0.0",
                    "platform": "Android",
                    "platformVersion": "10.0.0",
                    "architecture": "",
                    "model": "K",
                    "mobile": self.identity == "android",
                },
            })
            self.user_agent = ua
        except Exception as exc:
            self.log("warn", f"identity: {exc}")

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
            chromium_sandbox=use_chromium_sandbox(),
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
            await self.pause_gate()
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
        """Sayfayı açar; HTTP durum kodunu döner (bilinmiyorsa 0).

        Instagram yoğun istekte sayfayı boş bir hata koduyla (429/5xx) döndürebiliyor; tarayıcı bunu
        ERR_HTTP_RESPONSE_CODE_FAILURE olarak bildirir. O zaman bekleyip tekrar denenir.
        """
        target = rebase(url)
        backoff = _backoff_steps()
        attempt = 0
        while True:
            self._check_alive()
            seen: Dict[str, int] = {}

            def on_response(response) -> None:
                try:
                    if response.request.is_navigation_request() and response.request.frame == self.page.main_frame:
                        seen["status"] = response.status
                except Exception:
                    pass

            self.page.on("response", on_response)
            try:
                response = await self.page.goto(target, wait_until="domcontentloaded", timeout=60000)
                status = response.status if response is not None else 0
                if status not in (429, 500, 502, 503, 504):
                    return status
                refused = status
            except Exception as exc:
                if self.cancel.is_set():
                    raise asyncio.CancelledError()
                if self.page.is_closed():
                    raise BrowserError(msg("browser.closed"))
                message = str(exc).splitlines()[0]
                if "ERR_INTERNET_DISCONNECTED" in message or "ERR_NAME_NOT_RESOLVED" in message:
                    raise BrowserError(msg("browser.no_internet"))
                if "ERR_HTTP_RESPONSE_CODE_FAILURE" not in message:
                    if "Timeout" not in message:
                        raise BrowserError(msg("browser.page_failed", error=message))
                    self.log("warn", msg("browser.slow"))
                    return 0
                refused = seen.get("status", 0)
                self.log("debug", f"page {target} -> HTTP {refused or '?'} (boş hata sayfası)")
                if refused in (404, 410):
                    return refused
            finally:
                self.page.remove_listener("response", on_response)
            if attempt >= len(backoff):
                raise RateLimited()
            seconds = backoff[attempt]
            attempt += 1
            self.log("warn", msg("browser.page_refused", code=refused or "?", s=int(seconds)))
            await self._sleep(seconds)

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
        low, high = self.pacing.call_delay
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
                   form: Optional[Dict[str, str]] = None, what: str = "", essential: bool = False,
                   quick: bool = False) -> Any:
        """API çağrısı: doğrulama isterse kullanıcıyı bekler.

        quick=True: yanıt gelmezse 20 sn sonra vazgeçer ve aynı isteği tekrar denemez (başka yol varsa).

        Instagram bazı adresleri (ör. profil bilgisi) bazı oturumlara hiç vermiyor ve bunu "biraz bekle"
        diye söylüyor. Bu yüzden sadece vazgeçilmez çağrılarda (gönderi listesi) beklenip tekrar denenir;
        diğerlerinde hemen vazgeçilip başka yoldan devam edilir.
        """
        backoff = _backoff_steps() if essential else []
        if essential:
            # Öne çıkanlar gönderi listesi sayfası değildir: mola sayacına ve sayfa beklemesine girmez.
            await self._pace(counts_as_page=what != "highlights")
        attempt = 0
        connection_errors = 0
        server_errors = 0
        verified = False
        while True:
            self._check_cancel()
            await self.pause_gate()
            res = await self._raw_api(path, params=params, form=form, timeout=float(os.environ.get("INSTAGRAM_INDIRICI_QUICK_TIMEOUT", "20")) if quick else 45)
            kind = classify(res)
            if os.environ.get("INSTAGRAM_INDIRICI_DEBUG"):
                self.log("debug", f"API {path} -> {res.status} {kind} {res.text[:200]!r}")
            if kind == "ok":
                return res.data
            if res.status == 0 and kind == "error":
                connection_errors += 1
                if connection_errors <= 2 and not quick:
                    self.log("warn", msg("browser.retrying", detail=res.error or "?"))
                    await self._sleep(5 * connection_errors)
                    continue
                raise ApiError(f"{what or path}: {res.error or 'no response'}")
            if kind == "error" and res.status in (500, 502, 503, 504):
                # "Üzgünüz, bir sorun oluştu. Lütfen tekrar dene." — Instagram'ın geçici sunucu hatası.
                server_errors += 1
                if server_errors <= 2:
                    wait = float(os.environ.get("INSTAGRAM_INDIRICI_5XX_WAIT", "10")) * server_errors
                    self.log("debug", f"{what or path}: HTTP {res.status}, {wait:g} sn sonra yeniden denenecek")
                    await self._sleep(wait)
                    continue
            if kind == "rate":
                if attempt >= len(backoff):
                    self.log("info", msg("browser.refused", what=msg(f"what.{what or 'other'}"), code=res.status or "?"))
                    raise RateLimited(what, res.status)
                seconds = backoff[attempt]
                attempt += 1
                self.log("warn", msg("browser.slow_down", what=msg(f"what.{what or 'other'}"),
                                     code=res.status or "?", s=int(seconds)))
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

    async def _pace(self, counts_as_page: bool = True) -> None:
        """Liste sayfaları arasında bekler; belli sayıda sayfadan sonra uzun mola verir (Instagram'ı yormamak için)."""
        if not counts_as_page:
            return
        pc = self.pacing
        self.list_pages += 1
        if self.list_pages > 1 and pc.page_delay[1] > 0:
            await self._sleep(random.uniform(pc.page_delay[0], max(pc.page_delay[0], pc.page_delay[1])))
        low, high = pc.break_seconds
        every = pc.break_every
        if every > 0 and self.list_pages > 1 and (self.list_pages - 1) % every == 0 and high > 0:
            seconds = random.uniform(low, max(low, high))
            self.log("info", msg("browser.pause", s=int(seconds)))
            self.on_wait(time.time() + seconds)
            try:
                await self._sleep(seconds)
            finally:
                self.on_wait(0)

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
        rate_hit = False
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
        except (ApiError, LoginRequired, RateLimited) as exc:
            if isinstance(exc, RateLimited):
                rate_hit = True
            self.log("debug", f"web_profile_info: {exc.message.key}")

        # 3) Arama (kimlik hâlâ yoksa)
        if not info.user_id:
            try:
                data = await self.call("/web/search/topsearch/", params=[("query", username)], what="search")
                user = media.find_user(data, username)
                if user:
                    self._merge_user(info, user)
                    info.sources.append("search")
            except (ApiError, LoginRequired, NotFound, RateLimited) as exc:
                if isinstance(exc, RateLimited):
                    rate_hit = True
                self.log("debug", f"topsearch: {exc.message.key}")

        if not info.user_id:
            if rate_hit:
                raise RateLimited("profile")
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
            except (ApiError, LoginRequired, NotFound, RateLimited) as exc:
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
        on_page: Callable[..., str],
        resume: Optional[Tuple[str, str]] = None,
    ) -> FeedResult:
        """Profilin gönderilerini sayfa sayfa toplar.

        on_page(gönderiler, yol türü, sonraki imleç) "stop" dönerse durur, "jump" dönerse yarım kalan
        önceki taramanın devam noktasına (resume) atlar.
        """
        state = _FeedState(limit=limit, on_page=on_page, resume=resume)
        result = FeedResult()

        def finish(reason: str, via: str) -> FeedResult:
            result.reason = "limit" if state.limit_hit else reason
            result.collected = len(state.seen)
            result.via = via
            return result

        # İlk sayfa elimizde varsa (sayfaya gömülü ya da profil bilgisiyle gelen) önce onu ver.
        if not logged_in and info.first_items:
            if state.accept(info.first_items, "graphql_out", info.end_cursor) == "stop":
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
        rate_hits = 0
        for name, run in strategies:
            self._check_cancel()
            try:
                reason = await run()
            except ApiError as exc:
                self.log("info", msg("browser.strategy_failed", detail=exc.detail))
                continue
            except RateLimited:
                # Bu yol kısıtlandı; sıradaki yol (en son sayfanın kendi istekleri) denenir.
                last_reason = "rate_limited"
                rate_hits += 1
                continue
            except LoginRequired:
                if logged_in:
                    # Instagram oturumu kapattı (çok gezinince oluyor). Bulunanlar yine iner.
                    if not state.seen:
                        raise LoginRequired(msg("browser.session_expired"))
                    return finish("session_expired", name)
                last_reason = "login_wall"
                continue
            if reason in ("end", "up_to_date", "limit"):
                return finish(reason, name)
            last_reason = reason
            if reason == "login_wall" and name != "graphql":
                break
        if rate_hits and (info.media_count < 0 or len(state.seen) < info.media_count):
            if not state.seen:
                raise RateLimited("feed")
            return finish("rate_limited", "")
        if not logged_in and (info.media_count < 0 or len(state.seen) < info.media_count):
            return finish("login_wall", "")
        return finish(last_reason if state.seen else "stalled", "")

    async def _feed_rest(self, info: ProfileInfo, state: "_FeedState") -> str:
        max_id = ""
        fallback = None     # atlama başarısız olursa dönülecek imleç
        while True:
            self._check_cancel()
            params = [("count", "30")]
            if max_id:
                params.append(("max_id", max_id))
            try:
                data = await self.call(f"/api/v1/feed/user/{info.user_id}/", params=params, what="feed", essential=True)
                if not isinstance(data, dict) or "items" not in data:
                    raise ApiError("feed: no items")
            except (ApiError, NotFound):
                if fallback is None:
                    raise
                self.log("info", msg("browser.resume_failed"))
                max_id, fallback = fallback, None
                continue
            fallback = None
            items = [i for i in data.get("items") or [] if media.is_media(i)]
            next_id = str(data.get("next_max_id") or "")
            more = bool(data.get("more_available")) and bool(next_id) and next_id != max_id
            decision = state.accept(items, "rest", next_id if more else "")
            if decision == "stop":
                return "up_to_date"
            if state.done:
                return "limit"
            if decision == "jump" and more:
                target = state.jump_target("rest")
                if target and target != next_id:
                    self.log("info", msg("browser.resuming"))
                    fallback = next_id
                    max_id = target
                    continue
            if not more:
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
            data = await self.call("/graphql/query", form=form, what="graphql", essential=pages > 0)
            if isinstance(data, dict) and data.get("errors") and not data.get("data"):
                raise ApiError("graphql: " + str(data.get("errors"))[:120])
            items = media.find_media(data)
            page_info = media.find_page_info(data)
            if page_info is None and not items:
                raise ApiError("graphql: no posts in response")
            pages += 1
            kind = "graphql_in" if logged_in else "graphql_out"
            has_next = bool(page_info and page_info.get("has_next_page"))
            next_cursor = str((page_info or {}).get("end_cursor") or "")
            decision = state.accept(items, kind, next_cursor if has_next else "")
            if decision == "stop":
                return "up_to_date"
            if state.done:
                return "limit"
            if decision == "jump" and has_next:
                target = state.jump_target(kind)
                if target and target != next_cursor:
                    self.log("info", msg("browser.resuming"))
                    cursor = target
                    continue
            if not has_next:
                return "end"
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
            if state.accept(items) == "stop":
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
                if state.accept(items) == "stop":
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

    # ------------------------------------------------------------ Reels / etiketlenenler / yeniden paylaşımlar
    async def collect_tab(self, info: ProfileInfo, tab: str, *, logged_in: bool, limit: int,
                          on_page: Callable[..., str]) -> FeedResult:
        """Profilin ayrı sekmelerini (reels | tagged | reposts) toplar.

        Reels ve etiketlenenler için önce kendi liste isteği denenir; olmazsa (ya da yeniden paylaşımlar
        için doğrudan) sekme sayfası kaydırılıp sayfanın kendi yüklediği veri yakalanır.
        """
        state = _FeedState(limit=limit, on_page=on_page)
        result = FeedResult()

        def finish(reason: str, via: str) -> FeedResult:
            result.reason = "limit" if state.limit_hit else reason
            result.collected = len(state.seen)
            result.via = via
            return result

        strategies: List[Tuple[str, Callable[[], Any]]] = []
        if logged_in and tab == "reels":
            strategies.append(("clips", lambda: self._tab_clips(info, state)))
        if logged_in and tab == "tagged":
            strategies.append(("usertags", lambda: self._tab_usertags(info, state)))
        strategies.append(("scroll", lambda: self._tab_scroll(info, tab, state)))

        last_reason = "stalled"
        rate_hits = 0
        for name, run in strategies:
            self._check_cancel()
            try:
                reason = await run()
            except ApiError as exc:
                self.log("info", msg("browser.strategy_failed", detail=exc.detail))
                continue
            except NotFound:
                continue
            except RateLimited:
                last_reason = "rate_limited"
                rate_hits += 1
                continue
            except LoginRequired:
                if state.seen:
                    return finish("session_expired", name)
                last_reason = "login_wall"
                continue
            if reason in ("end", "up_to_date", "limit"):
                return finish(reason, name)
            last_reason = reason
        if rate_hits and not state.seen:
            raise RateLimited(tab)
        if rate_hits:
            return finish("rate_limited", "")
        return finish(last_reason if state.seen else ("login_wall" if not logged_in else "stalled"), "")

    def _tab_filter(self, info: ProfileInfo, tab: str, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Etiketlenenler ve yeniden paylaşımlarda profilin kendi gönderileri hariç tutulur; reels'te yalnız klipler."""
        out = []
        for item in items:
            owner = str(media.owner_of(item).get("username") or "").lower()
            if tab in ("tagged", "reposts"):
                if owner and owner == info.username.lower():
                    continue
            elif tab == "reels":
                if owner and owner != info.username.lower():
                    continue
                if not media.is_reel(item) and media.is_media(item) and item.get("product_type") not in (None, ""):
                    continue
            out.append(item)
        return out

    async def _tab_clips(self, info: ProfileInfo, state: "_FeedState") -> str:
        max_id = ""
        pages = 0
        while True:
            self._check_cancel()
            form = {"target_user_id": info.user_id, "page_size": "12", "include_feed_video": "true"}
            if max_id:
                form["max_id"] = max_id
            data = await self.call("/api/v1/clips/user/", form=form, what="reels", essential=pages > 0)
            if not isinstance(data, dict) or not isinstance(data.get("items"), list):
                raise ApiError("clips: no items")
            raw = []
            for entry in data["items"]:
                inner = entry.get("media") if isinstance(entry, dict) and isinstance(entry.get("media"), dict) else entry
                if isinstance(inner, dict) and media.is_media(inner):
                    raw.append(inner)
            pages += 1
            paging = data.get("paging_info") if isinstance(data.get("paging_info"), dict) else {}
            next_id = str(paging.get("max_id") or "")
            more = bool(paging.get("more_available")) and bool(next_id) and next_id != max_id
            decision = state.accept(self._tab_filter(info, "reels", raw), "clips", next_id if more else "")
            if decision == "stop":
                return "up_to_date"
            if state.done:
                return "limit"
            if not more:
                return "end"
            max_id = next_id

    async def _tab_usertags(self, info: ProfileInfo, state: "_FeedState") -> str:
        max_id = ""
        pages = 0
        while True:
            self._check_cancel()
            params = [("count", "30")]
            if max_id:
                params.append(("max_id", max_id))
            data = await self.call(f"/api/v1/usertags/{info.user_id}/feed/", params=params, what="tagged",
                                   essential=pages > 0)
            if not isinstance(data, dict) or not isinstance(data.get("items"), list):
                raise ApiError("usertags: no items")
            items = [i for i in data["items"] if media.is_media(i)]
            pages += 1
            next_id = str(data.get("next_max_id") or "")
            more = bool(data.get("more_available")) and bool(next_id) and next_id != max_id
            decision = state.accept(self._tab_filter(info, "tagged", items), "usertags", next_id if more else "")
            if decision == "stop":
                return "up_to_date"
            if state.done:
                return "limit"
            if not more:
                return "end"
            max_id = next_id

    async def _tab_scroll(self, info: ProfileInfo, tab: str, state: "_FeedState") -> str:
        """Sekme sayfasını kaydırıp sayfanın kendi yüklediği gönderileri yakalar."""
        idle_limit = float(os.environ.get("INSTAGRAM_INDIRICI_IDLE", "25"))
        page = self.page
        tasks: List[asyncio.Task] = []
        box: Dict[str, Any] = {"up_to_date": False, "last": time.monotonic()}
        base = base_url()
        kind = f"scroll_{tab}"

        def feed(data: Any) -> None:
            items = self._tab_filter(info, tab, media.find_media(data))
            before = len(state.seen)
            if state.accept(items, kind) == "stop":
                box["up_to_date"] = True
            if len(state.seen) > before:
                box["last"] = time.monotonic()

        async def handle(response) -> None:
            url = response.url
            if not url.startswith(base) or ("/graphql" not in url and "/api/v1/" not in url):
                return
            try:
                data = parse_json_text(await response.text())
            except Exception:
                return
            if data is not None:
                feed(data)

        def on_response(response) -> None:
            tasks.append(asyncio.ensure_future(handle(response)))

        self.log("info", msg("browser.scrolling"))
        page.on("response", on_response)
        try:
            await self.goto(f"https://www.instagram.com/{info.username}/{tab}/")
            await self._sleep(2)
            for data in await self._embedded_data():
                feed(data)
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
                    return "end" if state.seen else "stalled"
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

    async def stories(self, user_id: str, username: str = "") -> List[Dict[str, Any]]:
        try:
            data = await self.call("/api/v1/feed/reels_media/", params=[("reel_ids", user_id)], what="stories")
        except ApiError as exc:
            if not username:
                raise
            # Liste isteği çalışmadı: hikâye sayfası açılıp sayfanın kendi yüklediği öğeler yakalanır.
            self.log("debug", f"stories: {getattr(exc, 'detail', '') or exc.message.key}")
            self.log("info", msg("browser.stories_from_page"))
            found = await self._capture_media_from_page(f"https://www.instagram.com/stories/{username}/", wait=10)
            if found:
                return found
            raise
        items: List[Dict[str, Any]] = []
        for reel in self._reels_from(data):
            items.extend(i for i in reel.get("items") or [] if media.is_media(i))
        return items

    async def highlights_tray(self, user_id: str, username: str = "") -> List[Dict[str, Any]]:
        """Profilin öne çıkan listesi. Liste isteği reddedilirse profil sayfasının kendi verisinden alınır."""
        error: Optional[BrowserError] = None
        try:
            data = await self.call(f"/api/v1/highlights/{user_id}/highlights_tray/", what="highlights",
                                   essential=True)
            tray = data.get("tray") if isinstance(data, dict) else None
            if isinstance(tray, list):
                return media.find_highlights(tray) if tray else []
            error = ApiError("highlights: no tray")
        except (ApiError, RateLimited, NotFound) as exc:
            error = exc
        if not username:
            raise error
        self.log("info", msg("browser.highlights_from_page"))
        found = await self._capture_from_profile_page(username, media.find_highlights)
        if found:
            return found
        raise error

    async def _capture_from_profile_page(self, username: str, finder: Callable[[Any], List[Dict[str, Any]]],
                                         wait: float = 8) -> List[Dict[str, Any]]:
        """Profil sayfasını açıp sayfaya gömülü ve sayfanın kendi yüklediği veride finder ile arar."""
        found: List[Dict[str, Any]] = []
        keys = set()
        tasks: List[asyncio.Task] = []
        base = base_url()

        def take(data: Any) -> None:
            for entry in finder(data):
                k = entry.get("id")
                if k not in keys:
                    keys.add(k)
                    found.append(entry)

        async def handle(response) -> None:
            if not response.url.startswith(base):
                return
            try:
                data = parse_json_text(await response.text())
            except Exception:
                return
            if data is not None:
                take(data)

        def on_response(response) -> None:
            tasks.append(asyncio.ensure_future(handle(response)))

        page = self.page
        page.on("response", on_response)
        try:
            await self.goto(f"https://www.instagram.com/{username}/")
            end = time.monotonic() + wait
            while time.monotonic() < end:
                for data in await self._embedded_data():
                    take(data)
                await self._sleep(1)
        finally:
            page.remove_listener("response", on_response)
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
        return found

    def _reel_entry(self, reel: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": str(reel.get("id") or ""),
            "title": str(reel.get("title") or ""),
            "user": reel.get("user") if isinstance(reel.get("user"), dict) else {},
            "items": [i for i in reel.get("items") or [] if media.is_media(i)],
            "media_count": int(reel.get("media_count") or len(reel.get("media_ids") or []) or 0),
        }

    async def highlight_reels(self, reel_ids: List[str], expected: Optional[Dict[str, int]] = None,
                              failed: Optional[List[str]] = None,
                              titles: Optional[Dict[str, str]] = None) -> List[Dict[str, Any]]:
        """Öne çıkanların içeriği: [{"id", "title", "items", "user"}].

        Üçerli gruplar halinde istenir. Bir grubun içinde eksik gelen (öğe sayısı listedekinden az ya da
        hiç öğesi olmayan) öne çıkan tek başına tekrar istenir. Alınamayanlar failed listesine yazılır.
        """
        expected = expected or {}
        got: Dict[str, Dict[str, Any]] = {}
        order = list(reel_ids)
        limited = False
        api_down = False   # Liste isteğine yanıt gelmiyor: bir daha denenmez, doğrudan öne çıkanın sayfasına geçilir
        for start in range(0, len(order), 3):
            self._check_cancel()
            if api_down:
                break
            chunk = order[start:start + 3]
            try:
                data = await self.call("/api/v1/feed/reels_media/", params=[("reel_ids", r) for r in chunk],
                                       what="highlights", essential=True, quick=True)
            except (ApiError, NotFound) as exc:
                detail = getattr(exc, 'detail', '') or exc.message.key
                self.log("debug", f"highlights chunk: {detail}")
                if str(detail).endswith("timeout"):
                    api_down = True
                data = None
            except RateLimited:
                limited = True   # Instagram durdurdu: kalanları zorlamadan bırak
                break
            for reel in self._reels_from(data):
                entry = self._reel_entry(reel)
                if entry["id"]:
                    got[entry["id"]] = entry
        # Eksik ya da hiç gelmeyenleri tek tek iste.
        for rid in order:
            self._check_cancel()
            entry = got.get(rid)
            want = max(int(expected.get(rid) or 0), int((entry or {}).get("media_count") or 0))
            if entry is not None and entry["items"] and len(entry["items"]) >= want:
                continue
            if limited:
                if (entry is None or not entry["items"]) and failed is not None:
                    failed.append(rid)
                continue
            try:
                if api_down:
                    raise NotFound()
                data = await self.call("/api/v1/feed/reels_media/", params=[("reel_ids", rid)],
                                       what="highlights", essential=True, quick=True)
                for reel in self._reels_from(data):
                    single = self._reel_entry(reel)
                    if single["id"] == rid and (entry is None or len(single["items"]) > len(entry["items"])):
                        got[rid] = entry = single
            except (ApiError, NotFound) as exc:
                detail = getattr(exc, 'detail', '') or exc.message.key
                if not api_down:
                    self.log("debug", f"highlight {rid}: {detail}")
                if str(detail).endswith("timeout"):
                    api_down = True
            except RateLimited:
                limited = True
            if (entry is None or not entry["items"]) and not limited:
                # Liste isteği çalışmadı: öne çıkanın kendi sayfası açılır, sayfanın yaptığı istekler yakalanır.
                page_entry = await self._highlight_from_page(rid, (titles or {}).get(rid, ""))
                if page_entry is not None:
                    got[rid] = entry = page_entry
            if (entry is None or not entry["items"]) and failed is not None:
                failed.append(rid)
        return [got[r] for r in order if r in got and got[r]["items"]]

    async def _highlight_from_page(self, rid: str, title: str) -> Optional[Dict[str, Any]]:
        """Öne çıkanın sayfasını (/stories/highlights/<id>/) açıp sayfanın kendi yüklediği öğeleri yakalar."""
        number = rid.split(":")[-1]
        self.log("info", msg("browser.highlight_from_page", name=title or number))
        items = await self._capture_media_from_page(f"https://www.instagram.com/stories/highlights/{number}/", wait=10)
        if not items:
            return None
        return {"id": rid, "title": title, "user": {}, "items": items, "media_count": len(items)}

    async def _capture_media_from_page(self, url: str, wait: float = 8, scroll: bool = False,
                                       keep: Optional[Callable[[Dict[str, Any]], bool]] = None) -> List[Dict[str, Any]]:
        """Sayfayı açar; sayfaya gömülü ve sayfanın kendi yüklediği yanıtlardaki gönderileri toplar."""
        found: Dict[str, Dict[str, Any]] = {}
        tasks: List[asyncio.Task] = []
        base = base_url()

        def take(data: Any) -> None:
            for item in media.find_media(data):
                key = media.media_key(item)
                if key and key not in found and (keep is None or keep(item)):
                    found[key] = item

        async def handle(response) -> None:
            if not response.url.startswith(base) or ("/graphql" not in response.url and "/api/v1/" not in response.url):
                return
            try:
                data = parse_json_text(await response.text())
            except Exception:
                return
            if data is not None:
                take(data)

        def on_response(response) -> None:
            tasks.append(asyncio.ensure_future(handle(response)))

        page = self.page
        page.on("response", on_response)
        try:
            await self.goto(url)
            end = time.monotonic() + wait
            while time.monotonic() < end:
                for data in await self._embedded_data():
                    take(data)
                if scroll:
                    try:
                        await page.mouse.wheel(0, 2400)
                    except Exception:
                        pass
                await self._sleep(1)
        finally:
            page.remove_listener("response", on_response)
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
        return list(found.values())

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
            except (ApiError, LoginRequired, RateLimited) as exc:
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
    async def import_session(self, text: str) -> str:
        """Kullanıcının tanıdığı tarayıcıdan kopyaladığı oturum kodunu (sessionid) bu tarayıcıya yerleştirir.

        Dönüş: ok | invalid (biçim bozuk) | expired (Instagram kabul etmedi) | challenge | error.
        Başarısızsa önceki oturum geri yüklenir.
        """
        cookies = parse_session_input(text)
        if not cookies.get("sessionid"):
            return "invalid"
        self._check_alive()
        host = urlsplit(base_url()).hostname or "www.instagram.com"
        domain = ".instagram.com" if host.endswith("instagram.com") else host
        secure = base_url().startswith("https")
        previous: Dict[str, Any] = {}
        try:
            for c in await self.context.cookies([base_url() + "/"]):
                if c.get("name") in _SESSION_NAMES and c.get("value"):
                    previous[c["name"]] = c
        except Exception:
            previous = {}
        expires = time.time() + 365 * 24 * 3600
        items = []
        for name, value in cookies.items():
            items.append({"name": name, "value": value, "domain": domain, "path": "/", "expires": expires,
                          "httpOnly": name == "sessionid", "secure": secure})
        try:
            await self.context.add_cookies(items)
        except Exception as exc:
            self.log("debug", f"import_session: {str(exc).splitlines()[0] if str(exc) else exc}")
            return "error"
        await self.goto(base_url() + "/")
        result = "error"
        try:
            res = await self._raw_api("/api/v1/accounts/current_user/", params=[("edit", "true")])
            kind = classify(res)
            if kind == "ok":
                result = "ok"
            elif kind == "login":
                result = "expired"
            elif kind == "challenge":
                result = "challenge"
            elif kind == "rate" and await self.logged_in() and not await self._page_redirect_state():
                result = "ok"   # Instagram sıkıştı ama oturum çerezi duruyor: kabul et
        except (asyncio.CancelledError, BrowserError):
            raise
        except Exception as exc:
            self.log("debug", f"import_session: {str(exc).splitlines()[0] if str(exc) else exc}")
        if result != "ok":
            await self._restore_cookies(previous, domain, secure)
        return result

    async def _restore_cookies(self, previous: Dict[str, Any], domain: str, secure: bool) -> None:
        """Başarısız içe aktarmada eski oturumu geri koyar (yoksa yeni, geçersiz çerezi siler)."""
        try:
            await self.context.clear_cookies(name="sessionid")
            if previous.get("sessionid"):
                keep = []
                for c in previous.values():
                    d = {k: c[k] for k in ("name", "value", "domain", "path", "expires", "httpOnly", "secure", "sameSite") if k in c}
                    keep.append(d)
                await self.context.add_cookies(keep)
        except Exception as exc:
            self.log("debug", f"import_session restore: {str(exc).splitlines()[0] if str(exc) else exc}")

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
        last_state = already
        while not page.is_closed() and not self.cancel.is_set():
            await asyncio.sleep(1)
            if page.is_closed():
                break
            now_in = await self.logged_in()
            last_state = now_in
            if now_in and not await self._page_redirect_state() and \
                    "two_factor" not in self._page_path():
                if not seen_logged_in:
                    seen_logged_in = time.monotonic()
                elif time.monotonic() - seen_logged_in > 3:
                    self.log("ok", msg("browser.login_success"))
                    return True
            else:
                seen_logged_in = 0.0
        if self.context is not None and not self.closed:
            return await self.logged_in()
        return last_state


@dataclass
class _FeedState:
    limit: int
    on_page: Callable[..., str]
    resume: Optional[Tuple[str, str]] = None   # (yol türü, imleç): önceki yarım taramanın devam noktası
    jumped: bool = False
    seen: Dict[str, bool] = field(default_factory=dict)
    limit_hit: bool = False

    @property
    def done(self) -> bool:
        return self.limit_hit

    def accept(self, items: List[Dict[str, Any]], cursor_kind: str = "", next_cursor: str = "") -> str:
        """Yeni gönderileri motora verir.

        Dönüş: "" (devam), "stop" (geri kalan zaten indirilmiş) ya da "jump" (bu bölge önceden
        indirilmiş; yarım kalan taramanın devam noktasına atlanabilir).
        """
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
            return ""
        return str(self.on_page(new, cursor_kind, next_cursor if not self.limit_hit else "") or "")

    def jump_target(self, kind: str) -> str:
        """Bu yol için atlanacak imleç (bir kez)."""
        if self.jumped or not self.resume or self.resume[0] != kind:
            return ""
        self.jumped = True
        return self.resume[1]
