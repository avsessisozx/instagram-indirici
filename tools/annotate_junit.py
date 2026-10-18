"""JUnit XML sonuçlarındaki hataları GitHub özet ekranına (annotation) yazar.

Kullanım: python tools/annotate_junit.py "<başlık>" <xml dosyası ya da klasörü>...
Günlük dosyalarına erişilemediğinde hataların görülebilmesi için.
"""

import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def esc(text: str) -> str:
    return text.replace("%", "%25").replace("\r", "").replace("\n", "%0A")


def main() -> int:
    title = sys.argv[1]
    files = []
    for arg in sys.argv[2:]:
        p = Path(arg)
        files += sorted(p.rglob("*.xml")) if p.is_dir() else [p]
    total = failed = 0
    shown = 0
    for f in files:
        try:
            root = ET.parse(f).getroot()
        except Exception:
            continue
        for case in root.iter("testcase"):
            total += 1
            for tag in ("failure", "error"):
                node = case.find(tag)
                if node is None:
                    continue
                failed += 1
                if shown < 10:
                    shown += 1
                    text = (node.get("message") or "") + "\n" + (node.text or "")
                    name = f"{case.get('classname', '').split('.')[-1]}.{case.get('name')}"
                    print(f"::error title={esc(title + ': ' + name)}::{esc(text[:3500])}")
    print(f"::notice title={esc(title)}::{total} test çalıştı, {failed} başarısız")
    return 0


if __name__ == "__main__":
    sys.exit(main())
