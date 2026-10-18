"""Çeviri dosyalarını denetler. Hata varsa çıkış kodu 1 olur (GitHub derlemesi durur).

Denetlenenler:
  * Her dil dosyası geçerli JSON mu?
  * Kodda (Python, Kotlin, HTML) kullanılan her anahtar İngilizce dosyasında var mı?
    (İngilizce, eksik çevirilerin düşeceği dil olduğu için tam olmak zorunda.)
  * Diğer dillerde eksik ya da fazla anahtar var mı? (Eksikler uyarıdır; program İngilizceye düşer.)
  * Bir çeviride, İngilizcedeki {değişken}lerden biri unutulmuş ya da yanlış yazılmış mı?
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIR = ROOT / "ceviriler"
PLACEHOLDER = re.compile(r"\{(\w+)\}")

# Kodda anahtar kullanımı: msg("x.y"), t('x.y'), data-i18n="x.y", Tr.get("x.y"), tr(ctx, "x.y") ...
USE_PATTERNS = [
    re.compile(r"""\bmsg\(\s*["']([a-z_]+\.[a-z0-9_.]+)["']"""),
    re.compile(r"""\b(?:t|pick|translate|tr|text|Msg)\(\s*(?:\w+\s*,\s*)?["']([a-z_]+\.[a-z0-9_.]+)["']"""),
    re.compile(r"""data-i18n(?:-placeholder)?=["']([a-z_]+\.[a-z0-9_.]+)["']"""),
    re.compile(r"""["']((?:ui|engine|browser|link|fetch|skip|summary|chromium|server|app|android|what)\.[a-z0-9_]+)["']"""),
]
# Platforma göre sonuna _desktop / _mobile eklenen anahtarlar (pick() ile kullanılır)
PICKED = re.compile(r"""\bpick\(\s*["']([a-z_]+\.[a-z0-9_.]+)["']""")


def load(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        print(f"HATA {path.name}: geçersiz JSON: {exc}")
        sys.exit(1)


def used_keys():
    keys = set()
    files = list((ROOT / "instagram_indirici").rglob("*.py")) + list((ROOT / "instagram_indirici").rglob("*.html"))
    files += list((ROOT / "android" / "app" / "src" / "main").rglob("*.kt"))
    for f in files:
        text = f.read_text(encoding="utf-8")
        for pat in USE_PATTERNS:
            keys.update(pat.findall(text))
        for base in PICKED.findall(text):
            keys.discard(base)
            keys.update({base + "_desktop", base + "_mobile"})
    return {k for k in keys if not k.endswith(".")}


def main() -> int:
    en = load(DIR / "en.json")
    errors, warnings = [], []
    for key in sorted(used_keys()):
        if key not in en and key != "summary":
            errors.append(f"en.json içinde yok: {key}")
    for path in sorted(DIR.glob("*.json")):
        if path.name == "en.json":
            continue
        data = load(path)
        for key in sorted(set(en) - set(data)):
            warnings.append(f"{path.name}: çevrilmemiş (İngilizcesi gösterilecek): {key}")
        for key in sorted(set(data) - set(en)):
            warnings.append(f"{path.name}: fazladan anahtar (kullanılmıyor): {key}")
        for key, value in data.items():
            if key in en and set(PLACEHOLDER.findall(value)) != set(PLACEHOLDER.findall(en[key])):
                errors.append(f"{path.name}: {key} değişkenleri İngilizceyle uyuşmuyor: {value!r}")
    for w in warnings:
        print("UYARI", w)
    for e in errors:
        print("HATA", e)
    if errors:
        return 1
    print(f"Çeviriler tamam: {len(en)} anahtar, {len(list(DIR.glob('*.json')))} dil.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
