"""اختبارات تبديل المحتوى في settings.py."""

import json
import shutil
import sys
import tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.context_aware_audio import settings

PASSED = FAILED = 0


def check(name, cond, extra=""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"PASS {name}")
    else:
        FAILED += 1
        print(f"FAIL {name}  {extra}")


tmp = Path(tempfile.mkdtemp())


def put(payload):
    (tmp / "settings.json").write_text(json.dumps(payload), encoding="utf-8")


try:
    with mock.patch.object(settings, "writable_path", lambda n: tmp / n):
        # --- المفاتيح موجودة في الافتراضي ---
        d = dict(settings.DEFAULTS)
        check("content_enabled defaults to on", d["content_enabled"] is True)
        check("content_muted defaults to off", d["content_muted"] is False)
        check("content_volume defaults to 0.85", d["content_volume"] == 0.85)
        check("content_library_dir defaults to empty", d["content_library_dir"] == "")

        # --- المستوى: مجال مغلق ---
        for raw, want in (
            (0.5, 0.5),
            (0.0, 0.0),
            (1.0, 1.0),
            (1.5, 1.0),
            (-0.5, 0.0),
            (True, 0.85),
            ("abc", 0.85),
            (None, 0.85),
            (float("nan"), 0.85),
        ):
            put({"content_volume": raw})
            got = settings.load_settings()["content_volume"]
            check(f"content_volume {raw!r} -> {want}", got == want, got)

        # --- الكتم: نص معناه صريح، لا bool("false") ---
        for raw, want in (
            (True, True),
            (False, False),
            ("true", True),
            ("false", False),
            ("yes", True),
            ("no", False),
            (1, True),
            (0, False),
            ("", False),
            ("غير معروف", False),  # الافتراضي content_muted=False
        ):
            put({"content_muted": raw})
            got = settings.load_settings()["content_muted"]
            check(f"content_muted {raw!r} -> {want}", got == want, got)

        # --- تفعيل المسار ---
        for raw, want in ((True, True), (False, False), ("true", True), ("no", False)):
            put({"content_enabled": raw})
            got = settings.load_settings()["content_enabled"]
            check(f"content_enabled {raw!r} -> {want}", got == want, got)

        # --- مجلد المكتبة: نص، بطول محدود ---
        for raw, want in (
            ("C:/music", "C:/music"),
            ("  C:/music  ", "C:/music"),
            (5, ""),
            (None, ""),
            ([], ""),
            (True, ""),
            ("z" * 500, "z" * 400),
        ):
            put({"content_library_dir": raw})
            got = settings.load_settings()["content_library_dir"]
            check(f"library_dir {raw!r:.20} -> {want!r:.20}", got == want, got)

        # --- رحلة ذهاب وإياب ---
        settings.save_settings(
            {
                "content_enabled": False,
                "content_muted": True,
                "content_volume": 0.42,
                "content_library_dir": "D:/my/content",
            }
        )
        back = settings.load_settings()
        check("round trip: enabled", back["content_enabled"] is False)
        check("round trip: muted", back["content_muted"] is True)
        check("round trip: volume", back["content_volume"] == 0.42)
        check("round trip: dir", back["content_library_dir"] == "D:/my/content")

        # --- مفتاح مجهول يُهمَل ---
        put({"content_volume": 0.5, "totally_unknown": 1})
        _got = settings.load_settings()
        check("an unknown key is not echoed back",
              "totally_unknown" not in _got, sorted(_got))
        check("a known key alongside it still applies",
              _got["content_volume"] == 0.5, _got["content_volume"])

        # --- ملف تالف: كل القيم افتراضية ---
        (tmp / "settings.json").write_text("{not json", encoding="utf-8")
        check(
            "a corrupt file falls back to defaults",
            settings.load_settings()["content_volume"] == 0.85,
        )
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print(f"\nRESULT: {PASSED} passed / {FAILED} failed")
sys.exit(1 if FAILED else 0)
