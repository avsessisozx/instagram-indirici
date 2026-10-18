"""Testleri çalıştırır; GitHub Actions'ta hata çıkarsa ayrıntıyı özet ekranına (annotation) yazar."""

import io
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)


def esc(text: str) -> str:
    return text.replace("%", "%25").replace("\r", "").replace("\n", "%0A")


def main() -> int:
    for stream in (sys.stdout, sys.stderr, sys.__stderr__):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    names = sys.argv[1:] or ["tests.test_units"]
    buf = io.StringIO()

    class Tee(io.TextIOBase):
        def write(self, s):
            sys.__stderr__.write(s)
            buf.write(s)
            return len(s)

    suite = unittest.defaultTestLoader.loadTestsFromNames(names)
    result = unittest.TextTestRunner(stream=Tee(), verbosity=2).run(suite)
    if result.wasSuccessful():
        return 0
    if os.environ.get("GITHUB_ACTIONS"):
        problems = result.failures + result.errors
        for test, trace in problems[:8]:
            print(f"::error title={esc(str(test))}::{esc(trace[-3500:])}", flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(main())
