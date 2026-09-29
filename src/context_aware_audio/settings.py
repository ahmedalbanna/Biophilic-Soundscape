"""
settings.py - حفظ إعدادات سطح المكتب محلياً (JSON)
"""

import json
from pathlib import Path

from .paths import writable_path

SETTINGS_FILENAME = "settings.json"

DEFAULTS = {
    "master_vol": 100.0,
    "master_mute": False,
    "use_mic": False,
    "mic_device": None,
    "athan_enabled": True,
}


def settings_path() -> Path:
    """مسار ملف الإعدادات (يُحسب عند الاستدعاء لا عند الاستيراد)."""
    return writable_path(SETTINGS_FILENAME)


def load_settings() -> dict:
    """يقرأ الإعدادات المحفوظة، أو القيم الافتراضية عند غيابها أو تلفها."""
    try:
        path = settings_path()
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            out = dict(DEFAULTS)
            out.update({k: data[k] for k in DEFAULTS if k in data})
            return out
    except (OSError, ValueError, TypeError):
        pass
    return dict(DEFAULTS)


def save_settings(data: dict) -> bool:
    """يحفظ الإعدادات بعد تنقيتها إلى المفاتيح المعروفة فقط."""
    try:
        clean = {k: data.get(k, DEFAULTS[k]) for k in DEFAULTS}
        settings_path().write_text(
            json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return True
    except OSError:
        return False
