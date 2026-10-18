"""Gerçek Instagram'a karşı küçük bir deneme (GitHub derlemesinde bilgi amaçlı çalışır).

Giriş yapmadan herkese açık bir profili açar, birkaç gönderi toplar ve hangi yolların
çalıştığını yazar. Instagram sunucu adreslerini sık engellediği için başarısız olması
derlemeyi durdurmaz; sadece Instagram'ın o günkü davranışını görmek içindir.

Kullanım: python tools/canli_deneme.py [kullanıcı_adı]
"""

import asyncio
import json
import os
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("INSTAGRAM_INDIRICI_DEBUG", "1")
os.environ.setdefault("INSTAGRAM_INDIRICI_IDLE", "12")
os.environ.setdefault("INSTAGRAM_INDIRICI_BACKOFF", "20")

from instagram_indirici import media  # noqa: E402
from instagram_indirici.browser import BrowserError, InstagramBrowser  # noqa: E402

lines = []


def out(text: str) -> None:
    lines.append(text)
    print(text, flush=True)


def log(level, message) -> None:
    text = message.render("en") if hasattr(message, "render") else str(message)
    out(f"  [{level}] {text[:400]}")


async def main(username: str) -> None:
    data_dir = Path(tempfile.mkdtemp())
    async with InstagramBrowser(data_dir, log=log, cancel=threading.Event(), on_verify=lambda v: None) as b:
        out(f"Tarayıcı: {b.channel_name}")
        try:
            info = await b.open_profile(username, logged_in=False)
        except BrowserError as exc:
            out(f"PROFİL AÇILAMADI: {exc.message.key} {exc.message.params}")
            return
        out(f"Profil: id={info.user_id} ad={info.full_name!r} gizli={info.is_private} "
            f"gönderi={info.media_count} kaynak={info.sources} ilk={len(info.first_items)}")
        if info.first_items:
            item = info.first_items[0]
            post = media.build_post(item, content="all")
            out(f"İlk gönderi alanları: {sorted(item.keys())[:40]}")
            out(f"İlk gönderi: {post.stem} dosya={[(f.kind, f.urls[0][:80] if f.urls else '') for f in post.files]}")
        got = []
        result = await b.collect_posts(info, logged_in=False, limit=30, on_page=lambda items: got.extend(items) or False)
        out(f"Toplama: {result.reason} yol={result.via!r} n={result.collected}")
        if got:
            code = media.media_key(got[0])
            try:
                item = await b.get_post(code, logged_in=False)
                out(f"Tek gönderi ({code}): {len(media.build_post(item, content='all').files)} dosya")
            except BrowserError as exc:
                out(f"Tek gönderi ({code}) alınamadı: {exc.message.key}")
        out(f"API çağrısı: {b.api_calls}")


if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else "instagram"
    try:
        asyncio.run(asyncio.wait_for(main(name), 600))
    except Exception as exc:  # bilgi amaçlı; derlemeyi durdurma
        out(f"HATA: {type(exc).__name__}: {exc}")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write("### Gerçek Instagram denemesi (giriş yapmadan)\n```\n" + "\n".join(lines)[-60000:] + "\n```\n")
    Path("canli-deneme.txt").write_text("\n".join(lines), encoding="utf-8")
