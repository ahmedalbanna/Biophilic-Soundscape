"""
prayer_provider.py - جلب مواقيت الصلاة الحقيقية من Aladhan API مع كاش محلي
الموقع والطريقة يُقرأان من EngineConfig (افتراضياً صنعاء + رابطة العالم الإسلامي).
"""

import json
from datetime import date
from datetime import time as dtime
from typing import Dict, NamedTuple, Optional

from .config import EngineConfig
from .paths import writable_path

CACHE_FILENAME = "prayer_cache.json"

PRAYER_KEYS = ("fajr", "dhuhr", "asr", "maghrib", "isha")
PRAYER_KEYS_AR = {
    "fajr": "الفجر",
    "dhuhr": "الظهر",
    "asr": "العصر",
    "maghrib": "المغرب",
    "isha": "العشاء",
}

# مصادر المواقيت - يخبرها الواجهة للمستخدم
SOURCE_API = "api"
SOURCE_CACHE_TODAY = "cache"
SOURCE_CACHE_STALE = "cache-stale"
SOURCE_DEFAULT = "default"


class PrayerTimes(NamedTuple):
    """نتيجة تحميل المواقيت مع مصدرها."""

    times: Dict[str, dtime]
    source: str
    cached_date: Optional[date] = None


def _parse_hhmm(value: str) -> dtime:
    """'04:42 (EET)' -> time(4, 42)."""
    token = value.strip().split()[0]
    h, m = token.split(":")[:2]
    return dtime(int(h), int(m))


def _read_cache() -> Optional[PrayerTimes]:
    """يقرأ الكاش مرة واحدة ويعيد المواقيت وتاريخها، أو None."""
    try:
        path = writable_path(CACHE_FILENAME)
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        raw = data.get("times") or {}
        times: Dict[str, dtime] = {}
        for key, value in raw.items():
            h, m = value.split(":")
            times[key] = dtime(int(h), int(m))
        if not times:
            return None
        cached = date.fromisoformat(data["date"]) if data.get("date") else None
        return PrayerTimes(times, SOURCE_CACHE_TODAY, cached)
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _write_cache(day: date, times: Dict[str, dtime]) -> None:
    try:
        writable_path(CACHE_FILENAME).write_text(
            json.dumps(
                {
                    "date": day.isoformat(),
                    "times": {k: v.strftime("%H:%M") for k, v in times.items()},
                }
            ),
            encoding="utf-8",
        )
    except OSError:
        pass


def fetch_times(config: EngineConfig, day: date, timeout: int = 8) -> Dict[str, dtime]:
    """يجلب المواقيت من Aladhan حسب config (city/country/prayer_method)."""
    import requests

    response = requests.get(
        "https://api.aladhan.com/v1/timingsByCity",
        params={
            "city": config.city,
            "country": config.country,
            "method": config.prayer_method,
            "date": f"{day.day:02d}-{day.month:02d}-{day.year:04d}",
        },
        timeout=timeout,
    )
    response.raise_for_status()
    raw = response.json()["data"]["timings"]
    times = {
        "fajr": _parse_hhmm(raw["Fajr"]),
        "dhuhr": _parse_hhmm(raw["Dhuhr"]),
        "asr": _parse_hhmm(raw["Asr"]),
        "maghrib": _parse_hhmm(raw["Maghrib"]),
        "isha": _parse_hhmm(raw["Isha"]),
    }
    _write_cache(day, times)
    return times


def describe_times(times: Dict[str, dtime]) -> str:
    """وصف عربي مختصر: «الفجر 04:42 | الظهر 11:53 | ...»."""
    return " | ".join(
        f"{PRAYER_KEYS_AR.get(k, k)} {v.strftime('%H:%M')}" for k, v in times.items()
    )


def describe_source(source: str, cached_date: Optional[date] = None) -> str:
    """وصف عربي لمصدر المواقيت، مع تاريخ الكاش إذا كان قديماً."""
    if source == SOURCE_API:
        return "من Aladhan مباشرة"
    if source == SOURCE_CACHE_TODAY:
        return "من كاش اليوم"
    if source == SOURCE_CACHE_STALE:
        return f"كاش قديم ({cached_date}) - تعذر التحديث"
    return "أوقات تقريبية احتياطية"


def load_today_times(config: Optional[EngineConfig] = None) -> PrayerTimes:
    """
    يحمّل مواقيت اليوم بترتيب الأولوية:
    كاش اليوم -> API -> كاش قديم -> أوقات تقريبية.
    """
    config = config or EngineConfig()
    today = date.today()

    cached = _read_cache()
    if cached is not None and cached.cached_date == today:
        return cached
    try:
        return PrayerTimes(fetch_times(config, today), SOURCE_API, today)
    except Exception:
        pass
    if cached is not None:
        return PrayerTimes(cached.times, SOURCE_CACHE_STALE, cached.cached_date)

    from .prayer_engine import PrayerEngine

    return PrayerTimes(PrayerEngine(config).default_times(), SOURCE_DEFAULT, None)
