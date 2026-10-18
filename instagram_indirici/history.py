"""İndirme geçmişi: hangi profilden hangi gönderinin indiğini ve listede nerede kalındığını tutar.

Geçmiş Instagram hesabına değil, indirilen profile (kullanıcı kimliğine) bağlıdır. Böylece
başka bir hesapla giriş yapılsa da, aylar sonra bile, inenler tekrar inmez ve yarım kalan
liste kaldığı yerden devam eder. Dosya: <program verisi>/gecmis.sqlite3
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional, Set, Tuple

from .paths import CONTENT_ALL, app_data_dir

_SCHEMA = """
CREATE TABLE IF NOT EXISTS downloaded (
    user_id  TEXT NOT NULL,
    key      TEXT NOT NULL,
    content  TEXT NOT NULL,
    kind     TEXT NOT NULL,
    username TEXT,
    at       INTEGER NOT NULL,
    PRIMARY KEY (user_id, key, content)
);
CREATE TABLE IF NOT EXISTS progress (
    user_id     TEXT NOT NULL,
    content     TEXT NOT NULL,
    username    TEXT,
    newest      INTEGER NOT NULL DEFAULT 0,
    cursor_kind TEXT,
    cursor      TEXT,
    cursor_at   INTEGER,
    PRIMARY KEY (user_id, content)
);
"""


def history_path() -> Path:
    return app_data_dir() / "gecmis.sqlite3"


class History:
    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = path or history_path()
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(self.path), timeout=30, check_same_thread=False)
        self._db.executescript(_SCHEMA)
        self._db.commit()

    def close(self) -> None:
        with self._lock:
            try:
                self._db.close()
            except Exception:
                pass

    # ------------------------------------------------------------ indirilenler
    def known(self, user_id: str, content: str) -> Set[str]:
        """Bu içerik seçimi için daha önce tamamen inmiş gönderilerin anahtarları."""
        if not user_id:
            return set()
        with self._lock:
            rows = self._db.execute(
                "SELECT key FROM downloaded WHERE user_id=? AND content IN (?, ?)",
                (user_id, content, CONTENT_ALL)).fetchall()
        return {r[0] for r in rows}

    def mark(self, user_id: str, key: str, content: str, kind: str, username: str = "") -> None:
        if not user_id or not key:
            return
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO downloaded (user_id, key, content, kind, username, at) VALUES (?,?,?,?,?,?)",
                (user_id, key, content, kind, username, int(time.time())))
            self._db.commit()

    def count(self) -> int:
        with self._lock:
            return int(self._db.execute("SELECT COUNT(*) FROM downloaded").fetchone()[0])

    def clear(self) -> int:
        with self._lock:
            n = int(self._db.execute("SELECT COUNT(*) FROM downloaded").fetchone()[0])
            self._db.execute("DELETE FROM downloaded")
            self._db.execute("DELETE FROM progress")
            self._db.commit()
        return n

    # ------------------------------------------------------------ liste ilerlemesi
    def _progress(self, user_id: str, content: str):
        return self._db.execute(
            "SELECT newest, cursor_kind, cursor FROM progress WHERE user_id=? AND content=?",
            (user_id, content)).fetchone()

    def complete_newest(self, user_id: str, content: str) -> int:
        """Profil daha önce tamamen tarandıysa en yeni gönderinin zamanı, yoksa 0."""
        if not user_id:
            return 0
        with self._lock:
            best = 0
            for c in {content, CONTENT_ALL}:
                row = self._progress(user_id, c)
                if row and row[0]:
                    best = max(best, int(row[0]))
        return best

    def set_complete(self, user_id: str, content: str, newest: int, username: str = "") -> None:
        if not user_id:
            return
        with self._lock:
            self._db.execute(
                "INSERT INTO progress (user_id, content, username, newest, cursor_kind, cursor, cursor_at) "
                "VALUES (?,?,?,?,NULL,NULL,NULL) ON CONFLICT(user_id, content) DO UPDATE SET "
                "newest=MAX(newest, excluded.newest), username=excluded.username, cursor_kind=NULL, cursor=NULL, cursor_at=NULL",
                (user_id, content, username, int(newest or 0)))
            self._db.commit()

    def resume_point(self, user_id: str, content: str) -> Optional[Tuple[str, str]]:
        """Yarım kalan taramanın devam noktası: (yol türü, imleç)."""
        if not user_id:
            return None
        with self._lock:
            for c in (content, CONTENT_ALL):
                row = self._progress(user_id, c)
                if row and row[1] and row[2]:
                    return str(row[1]), str(row[2])
        return None

    def set_resume_point(self, user_id: str, content: str, cursor_kind: str, cursor: str, username: str = "") -> None:
        if not user_id or not cursor:
            return
        with self._lock:
            self._db.execute(
                "INSERT INTO progress (user_id, content, username, newest, cursor_kind, cursor, cursor_at) "
                "VALUES (?,?,?,0,?,?,?) ON CONFLICT(user_id, content) DO UPDATE SET "
                "cursor_kind=excluded.cursor_kind, cursor=excluded.cursor, cursor_at=excluded.cursor_at, "
                "username=excluded.username",
                (user_id, content, username, cursor_kind, cursor, int(time.time())))
            self._db.commit()
