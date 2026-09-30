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
    # سحب المحتوى من الخادم. عنوان مُثبَّت حتى يعمل heartbeat بلا
    # المزامنة اختيارية: عنوان فارغ تعني معطّلة. كان الافتراضي عنواناً
    # عاماً برقم «dev-token» مكتوباً في كل نسخة، فيرسل كل مستخدم الرمز
    # نفسه صريحاً على HTTP بلا TLS إلى عنوان لا يملكه. الافتراضي الفارغ
    # يجعل التشغيل صامتاً وآمناً، والتفعيل اختيار صريح.
    "content_sync_url": "",
    "content_sync_token": "",
    "content_sync_max_parts": 20,
}

# اسم متغيّر البيئة الذي يُقرأ منه الرمز. لـ URI عام أو خادم مشترك
# هو الموضع الصحيح للرمز: خارج الإعدادات التي تُرفع وتُنسخ.
TOKEN_ENV_VAR = "CONTENT_SYNC_TOKEN"


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


def _as_url(value: Any) -> str:
    """
    عنوان خادم المزامنة، أو نص فارغ يعني «المزامنة معطّلة».

    يُقصّ على 300 محرفاً مثل `_as_dir`.

    المخطط محصور بـ http/https. لم يكن محصوراً، و`urlopen` يحترم
    `file://`: فملف إعدادات معدَّل يحوّل مسار الـmanifest إلى قارئ
    ملف محلي. عنوان بلا مخطط يُرفض أيضاً — الإعداد الصامت أسهل على
    من يقصده من رسالة خطأ غامضة.
    """
    if not isinstance(value, str):
        return ""
    url = value.strip().rstrip("/")[:300]
    if not url:
        return ""
    low = url.lower()
    if not (low.startswith("http://") or low.startswith("https://")):
        return ""
    return url


def _as_token(value: Any) -> str:
    """رمز وصول نصّي؛ يُقصّ على 200 محرفاً - الرمز المولَّد 40 حرفاً."""
    if not isinstance(value, str):
        return ""
    return value.strip()[:200]


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
    "content_sync_url": _as_url,
    "content_sync_token": _as_token,
    "content_sync_max_parts": lambda v: _as_float(
        v, DEFAULTS["content_sync_max_parts"], 1.0, 500.0
    ),
}


def sync_token(stored: str = "") -> str:
    """
    رمز المزامنة الفعّال: من متغيّر البيئة أولاً، ثم من الإعدادات.

    البيئة أولاً لأنها خارج الملف: من يضع الرمز في ملف
    الإعدادات يوزّعه مع كل نسخة. وحقل الملف يبقى محترَماً لمن يعمل
    خلف NAT على جهازه وحده.
    """
    from os import environ

    env = environ.get(TOKEN_ENV_VAR, "").strip()
    if env:
        return env[:200]
    return str(stored or "").strip()[:200]


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
