"""
اختبار يدوي لوحدة content_sync مقابل خادم content يعمل فعلاً.

ليس جزءاً من مجموعة اختبارات المشروع - هذا فحص اندماجي Needs خادماً
حيّاً، بخلاف اختبارات الوحدة هنا. الاستعمال:

    node content-server.mjs --port 8787 &
    python3 tools/check_sync.py http://127.0.0.1:8787 dev-token
"""

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.context_aware_audio.content_sync import (  # noqa: E402
    list_windows,
    manifest_url,
    sync_window,
)


def main() -> int:
    base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8787"
    token = sys.argv[2] if len(sys.argv) > 2 else "dev-token"

    print(f"base: {base}")
    windows = list_windows(base, token)
    print(f"index: {len(windows)} window(s)")
    for w in windows:
        print(f"   {w.get('window'):16} {w.get('parts')} parts")

    if not windows:
        print("FAIL: index empty or unreachable")
        return 1

    window = "duha_wisdom"
    print(f"\nmanifest url: {manifest_url(base, window, token)}")

    tmp = Path(tempfile.mkdtemp(prefix="synctest-"))
    try:
        # ---- first pull ------------------------------------------------
        r1 = sync_window(tmp, base, window, token)
        print(
            f"\n[1] first pull : transferred={r1.transferred} "
            f"verified={r1.verified} failed={r1.failed} total={r1.total}"
        )
        for e in r1.errors[:3]:
            print("    err:", e)
        if not r1.ok or r1.transferred != r1.total:
            print("FAIL: first pull incomplete")
            return 1

        # ---- second pull must be a no-op -------------------------------
        r2 = sync_window(tmp, base, window, token)
        print(
            f"[2] second pull: transferred={r2.transferred} "
            f"skipped={r2.skipped} failed={r2.failed}"
        )
        if r2.transferred != 0:
            print("FAIL: second pull was not a no-op")
            return 1

        # ---- corrupt one file, delete another --------------------------
        files = sorted(p for p in tmp.glob("*.mp3"))
        if len(files) < 2:
            print("FAIL: not enough files for the damage test")
            return 1
        files[0].write_bytes(b"CORRUPT")
        files[1].unlink()
        r3 = sync_window(tmp, base, window, token)
        print(
            f"[3] repair      : transferred={r3.transferred} "
            f"failed={r3.failed} verified={r3.verified}"
        )
        if r3.transferred != 2:
            print(f"FAIL: expected exactly 2 repairs, got {r3.transferred}")
            return 1

        # ---- truncation must also be caught -----------------------------
        files[2].write_bytes(files[2].read_bytes()[:1000])
        r4 = sync_window(tmp, base, window, token)
        print(f"[4] truncation  : transferred={r4.transferred} failed={r4.failed}")
        if r4.transferred != 1:
            print(f"FAIL: truncated file not repaired ({r4.transferred})")
            return 1

        # ---- max_parts --------------------------------------------------
        r5 = sync_window(tmp, base, window, token, max_parts=2)
        print(f"[5] max_parts=2 : transferred={r5.transferred} (nothing to do)")
        if r5.transferred != 0:
            print("FAIL: max_parts fetched when nothing was missing")
            return 1

        print("\nALL SYNC CHECKS PASSED")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
