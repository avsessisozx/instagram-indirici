"""Instagram'a istek gönderme hızı ("hız modu"). Android'deki Pacing.kt ile aynı mantık.

Normal mod Windows'ta eskiden beri kullanılan değerlerdir (liste sayfaları arasında ek bekleme yok);
dikkatli modlar Instagram'ı daha az yorar ama daha yavaştır. Testler ortam değişkenleriyle
(INSTAGRAM_INDIRICI_DELAY / _BREAK) bu değerlerin üstüne yazabilir.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Tuple

Range = Tuple[float, float]


@dataclass(frozen=True)
class Pacing:
    call_delay: Range      # her istek arası (sn)
    page_delay: Range      # gönderi listesinin sayfaları arası ek bekleme (sn)
    break_every: int       # her kaç liste sayfasında bir mola (0 = yok)
    break_seconds: Range   # molanın süresi (sn)

    @staticmethod
    def for_speed(speed: str) -> "Pacing":
        if speed == "careful":
            return Pacing((4.0, 8.0), (15.0, 30.0), 3, (90.0, 180.0))
        if speed == "very_careful":
            return Pacing((6.0, 12.0), (30.0, 60.0), 2, (120.0, 240.0))
        return Pacing((2.5, 5.0), (0.0, 0.0), 5, (60.0, 120.0))

    def with_env(self) -> "Pacing":
        """Test ortamı değişkenleri varsa onları uygular."""
        call, brk = self.call_delay, (self.break_every, self.break_seconds)
        raw = os.environ.get("INSTAGRAM_INDIRICI_DELAY")
        if raw:
            try:
                lo, _, hi = raw.partition("-")
                low = float(lo)
                call = (max(0.0, low), max(low, float(hi or lo)))
            except ValueError:
                pass
        raw = os.environ.get("INSTAGRAM_INDIRICI_BREAK")
        if raw:
            try:
                every_s, _, span = raw.partition(":")
                lo, _, hi = span.partition("-")
                low, high = float(lo or 0), float(hi or lo or 0)
                brk = (int(every_s), (low, max(low, high)))
            except ValueError:
                pass
        page = self.page_delay
        raw = os.environ.get("INSTAGRAM_INDIRICI_PAGE_DELAY")
        if raw:
            try:
                lo, _, hi = raw.partition("-")
                low = float(lo)
                page = (max(0.0, low), max(low, float(hi or lo)))
            except ValueError:
                pass
        return Pacing(call, page, brk[0], brk[1])


def for_settings(settings: dict) -> Pacing:
    return Pacing.for_speed(str(settings.get("speed") or "normal")).with_env()
