"""
cultural_calendar.py - التقويم الثقافي والجدول الزمني اليمني (24 ساعة)
"""

from datetime import datetime
from .audio_types import DayPeriod
from .config import EngineConfig


# حدود الفترات (ساعة البداية شاملة، النهاية غير شاملة)
PERIOD_BOUNDS = [
    (5, 8, DayPeriod.FAJR_SABAH),
    (8, 13, DayPeriod.DUHA_WORK),
    (13, 14, DayPeriod.LUNCH),
    (14, 18, DayPeriod.MAQIL),
    (18, 20, DayPeriod.MAGHRIB_ISHA),
    (20, 23, DayPeriod.SAMRA),
    # 23-24 و 0-5 -> NIGHT_SLEEP
]

PERIOD_LABELS_AR = {
    DayPeriod.FAJR_SABAH: "الباكر والصباح",
    DayPeriod.DUHA_WORK: "الضحى والعمل",
    DayPeriod.LUNCH: "الظهر والغداء",
    DayPeriod.MAQIL: "المقيل والديوان",
    DayPeriod.MAGHRIB_ISHA: "المغرب والعشاء",
    DayPeriod.SAMRA: "السمرة المسائية",
    DayPeriod.NIGHT_SLEEP: "الليل والسكون",
}


class CulturalCalendar:
    """يحدد السياق الاجتماعي حسب الوقت ويعيد الصوت المناسب"""

    def __init__(self, config: EngineConfig):
        self.config = config

    @staticmethod
    def period_for_hour(hour: int) -> DayPeriod:
        for start, end, period in PERIOD_BOUNDS:
            if start <= hour < end:
                return period
        return DayPeriod.NIGHT_SLEEP

    def period_for_datetime(self, dt: datetime) -> DayPeriod:
        return self.period_for_hour(dt.hour)

    def sound_for_period(self, period: DayPeriod) -> dict:
        return self.config.period_sounds.get(
            period.value, {"file": None, "db": 0, "label": ""}
        )

    def describe(self, period: DayPeriod) -> str:
        """وصف نصي للفترة: مثال «المقيل والديوان -> نسيم دافئ (32 dB)»."""
        info = self.sound_for_period(period)
        label = PERIOD_LABELS_AR.get(period, period.value)
        return f"{label} -> {info.get('label', '')} ({info.get('db', 0)} dB)"
