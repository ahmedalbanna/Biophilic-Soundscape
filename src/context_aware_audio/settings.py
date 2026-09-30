"""
settings.py - حفظ إعدادات سطح المكتب محلياً (JSON)

الملف قابل للتحرير يدوياً أو قد يُقتطع كتابةً، لذا كل قيمة تُنقّى إلى
النوع المتوقع مع الرجوع للقيمة الافتراضية عند الخطأ. لا يُقبل أي مفتاح
خارج المُسجَّل.
"""

import json
import math
from pathlib import Path
from typing import Any, Callable, Optional

from .paths import atomic_write, writable_path

SETTINGS_FILENAME = "settings.json"

DEFAULTS = {
    "master_vol": 100.0,
    "master_mute": False,
    "use_mic": False,
    "mic_device": None,
    "athan_enabled": True,
    "sim_offset_sec": 0.0,
    "sim_enabled": False,
    "duck_depth": 70.0,
    # مسار المحتوى التعليمي
    "content_enabled": True,
    "content_muted": False,
    "content_volume": 0.85,
    "content_library_dir": "",
}


def settings_path() -> Path:
    """مسار ملف الإعدادات (يُحسب عند الاستدعاء لا عند الاستيراد)."""
    return writable_path(SETTINGS_FILENAME)


def _as_float(value: Any, default: float, low: float, high: float) -> float:
    """
    يحوّل إلى float ضمن مجال، ويعيد الافتراضي عند الفشل أو الخروج عن المجال.

    `bool` مستثنى عمداً: `float(True)` = 1.0، فحقل `true` في ملف الإعدادات
    كان سيعني "صوت 1%" بلا أي خطأ ظاهر.
    """
    if isinstance(value, bool):
        return default
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(out):  # NaN أو ±inf
        return default
    return low if out < low else high if out > high else out


def _as_bool(value: Any, default: bool) -> bool:
    """
    يحوّل إلى bool بمعنى صريح.

    `bool("false")` = True في بايثون، فأي نص غير فارغ كان يشغّل الميكروفون
    من ملف مكتوب يدوياً. لذلك نتعامل مع النصوص صراحةً.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        low = value.strip().lower()
        if low in ("true", "1", "yes", "on"):
            return True
        if low in ("false", "0", "no", "off", ""):
            return False
    return default


def _as_device(value: Any) -> Optional[int]:
    """معرّف جهاز إدخال: int سالب غير مقبول، وأي شيء آخر = None."""
    if value is None or isinstance(value, bool):
        return None
    try:
        idx = int(value)
    except (TypeError, ValueError):
        return None
    return idx if idx >= 0 else None


def _as_dir(value: Any) -> str:
    """
    مسار مجلد نصّي، أو نص فارغ يعني الافتراضي.

    يُقصّ على 400 محرفاً: حقل في ملف JSON قابل للتحرير يدوياً، بلا
    سقف كان يعني مساراً من أي طول. والقصّ يمنع أيضاً مفتاحاً عملاقاً
    في واجهة الإعدادات.
    """
    if not isinstance(value, str):
        return ""
    return value.strip()[:400]


_COERCERS: dict[str, Callable[[Any], Any]] = {
    "master_vol": lambda v: _as_float(v, DEFAULTS["master_vol"], 0.0, 100.0),
    "master_mute": lambda v: _as_bool(v, DEFAULTS["master_mute"]),
    "use_mic": lambda v: _as_bool(v, DEFAULTS["use_mic"]),
    "mic_device": _as_device,
    "athan_enabled": lambda v: _as_bool(v, DEFAULTS["athan_enabled"]),
    # محاكاة الساعة: إزاحة يوم واحد قبل وبعد + حالة التفعيل
    "sim_offset_sec": lambda v: _as_float(
        v, DEFAULTS["sim_offset_sec"], -86_400.0, 86_400.0
    ),
    "sim_enabled": lambda v: _as_bool(v, DEFAULTS["sim_enabled"]),
    # عمق الخفض: نسبة مئوية بين 0 و100، ما عدا 0 فهو بلا خفض
    "duck_depth": lambda v: _as_float(v, DEFAULTS["duck_depth"], 0.0, 95.0),
    # مسار المحتوى: 0.85 افتراضياً، والقصّ يمنع مستوىً يتجاوز 100%
    "content_enabled": lambda v: _as_bool(v, DEFAULTS["content_enabled"]),
    "content_muted": lambda v: _as_bool(v, DEFAULTS["content_muted"]),
    "content_volume": lambda v: _as_float(
        v, DEFAULTS["content_volume"], 0.0, 1.0
    ),
    "content_library_dir": _as_dir,
}


def load_settings() -> dict:
    """يقرأ الإعدادات منقّاةً، أو القيم الافتراضية عند غيابها أو تلفها."""
    try:
        path = settings_path()
        if not path.exists():
            return dict(DEFAULTS)
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return dict(DEFAULTS)
    except (OSError, ValueError):
        return dict(DEFAULTS)

    out = dict(DEFAULTS)
    for key, coerce in _COERCERS.items():
        if key in data:
            out[key] = coerce(data[key])
    return out


def save_settings(data: dict) -> bool:
    """يحفظ الإعدادات بعد تنقيتها إلى المفاتيح المعروفة وكتابتها ذرّياً."""
    if not isinstance(data, dict):
        return False
    try:
        clean = {
            key: coerce(data.get(key, DEFAULTS[key]))
            for key, coerce in _COERCERS.items()
        }
        atomic_write(settings_path(), json.dumps(clean, ensure_ascii=False, indent=2))
        return True
    except (OSError, TypeError, ValueError):
        return False
