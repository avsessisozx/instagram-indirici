"""Instagram'a hangi cihazmış gibi görüneceğimiz (tarayıcı kimliği). Android'deki Identity.kt'nin karşılığı.

Windows'ta "Bilgisayar" seçiliyken tarayıcının kendi kimliği kullanılır; bu modül yalnız
"Android telefon" ve "Tablet" için kimlik metni üretir.
"""

from __future__ import annotations

import re

DESKTOP = "desktop"
PHONE = "android"
TABLET = "tablet"
ALL = (DESKTOP, PHONE, TABLET)


def chrome_major(user_agent: str) -> str:
    m = re.search(r"Chrome/(\d+)", user_agent or "")
    return m.group(1) if m else "130"


def phone(user_agent: str) -> str:
    return (f"Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) "
            f"Chrome/{chrome_major(user_agent)}.0.0.0 Mobile Safari/537.36")


def tablet(user_agent: str) -> str:
    return (f"Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) "
            f"Chrome/{chrome_major(user_agent)}.0.0.0 Safari/537.36")


def desktop(user_agent: str) -> str:
    return (f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
            f"Chrome/{chrome_major(user_agent)}.0.0.0 Safari/537.36")


def user_agent_for(identity: str, current: str) -> str:
    if identity == PHONE:
        return phone(current)
    if identity == TABLET:
        return tablet(current)
    return desktop(current)
