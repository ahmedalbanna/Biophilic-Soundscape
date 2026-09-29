"""
prayer_engine.py - محرك مواقيت الصلاة والقفل الذكي (Interlock)
حسب thinking.md الحالة الرابعة:
- كتم قبل الأذان بـ 3 دقائق
- حظر التشغيل طوال فترة الصلاة
- منع الأصوات العشوائية حتى 15 دقيقة بعد الصلاة
"""

from dataclasses import dataclass
from datetime import date, datetime, time as dtime, timedelta
from typing import Dict, List, Optional, Tuple

from .config import EngineConfig


def host_utc_offset_hours() -> float:
    """إزاحة توقيت المضيف عن UTC بالساعات (تراعي التوقيت الصيفي)."""
    offset = datetime.now().astimezone().utcoffset()
    return offset.total_seconds() / 3600.0 if offset else 0.0


@dataclass
class PrayerWindow:
    name: str
    adhan: datetime  # وقت الأذان بتوقيت الموقع
    mute_start: datetime  # الأذان - 3 دقائق (بتوقيت المضيف)
    lock_end: datetime  # نهاية الحظر (بتوقيت المضيف)


class PrayerEngine:
    """يحسب نوافذ الكتم اليومية ويتحقق من حالة القفل"""

    PRAYER_NAMES = ["fajr", "dhuhr", "asr", "maghrib", "isha"]
    PRAYER_NAMES_AR = {
        "fajr": "الفجر",
        "dhuhr": "الظهر",
        "asr": "العصر",
        "maghrib": "المغرب",
        "isha": "العشاء",
    }

    def __init__(self, config: EngineConfig, prayer_duration_min: Optional[int] = None):
        self.config = config
        # مدة الصلاة التقديرية - من الإعدادات ما لم تُمرَّر صراحةً
        self.prayer_duration_min = (
            prayer_duration_min
            if prayer_duration_min is not None
            else config.prayer_duration_min
        )
        # أوقات افتراضية تقريبية (تُستبدل بـ Aladhan عبر prayer_provider)
        self._times: Dict[str, dtime] = self.default_times()
        self._cache_date: Optional[date] = None
        self._cache_windows: List[PrayerWindow] = []

    @staticmethod
    def default_times() -> Dict[str, dtime]:
        """مواقيت تقريبية للتجربة (لا تصلح للتشغيل الفعلي)."""
        return {
            "fajr": dtime(5, 10),
            "dhuhr": dtime(12, 5),
            "asr": dtime(15, 25),
            "maghrib": dtime(18, 10),
            "isha": dtime(19, 30),
        }

    def set_times(self, times: Dict[str, dtime]) -> None:
        """
        حقن أوقات مخصصة (للمحاكاة والاختبار أو API خارجي مثل Aladhan).

        تُرشّح إلى أسماء الصلوات المعروفة فقط: كاش أو ملف معدَّل يدوياً قد
        يحمل مفاتيح غريبة لا يجب أن تصل إلى حسابات النوافذ.
        """
        for name, value in times.items():
            if name in self.PRAYER_NAMES and isinstance(value, dtime):
                self._times[name] = value
        self._cache_date = None  # إبطال الكاش

    def _shift_to_host_time(self, day: date, t: dtime) -> datetime:
        """
        يحوّل وقت الأذان (بتوقيت الموقع) إلى توقيت المضيف.

        Aladhan يعيد المواقيت بتوقيت المدينة (صنعاء = UTC+3). لو كان المضيف
        على منطقة زمنية أخرى، ستقع كل نافذة كتم في غير وقتها. لذلك نطرح
        الفرق بين توقيت الموقع وتوقيت المضيف.
        """
        local = datetime.combine(day, t)
        # وقت المضيف = وقت الموقع - إزاحة الموقع + إزاحة المضيف
        delta_hours = self.config.timezone_offset - host_utc_offset_hours()
        if delta_hours:
            local -= timedelta(hours=delta_hours)
        return local

    def _build_windows(self, day: date) -> List[PrayerWindow]:
        windows = []
        for name in self.PRAYER_NAMES:
            adhan = self._shift_to_host_time(day, self._times[name])
            mute_start = adhan - timedelta(minutes=self.config.pre_adhan_mute_min)
            lock_end = adhan + timedelta(
                minutes=self.prayer_duration_min + self.config.post_prayer_lock_min
            )
            windows.append(PrayerWindow(name, adhan, mute_start, lock_end))
        return windows

    def _windows_for(self, day: date) -> List[PrayerWindow]:
        # كاش يومي + نافذة اليوم السابق (لصلاة العشاء الممتدة بعد منتصف الليل)
        if self._cache_date != day:
            self._cache_date = day
            self._cache_windows = self._build_windows(day)
        return self._cache_windows

    def check(self, now: datetime) -> Tuple[bool, str]:
        """
        هل نحن داخل نافذة كتم/قفل؟
        يعيد (محظور؟, السبب)
        يفحص اليوم الحالي واليوم السابق (لتغطية امتداد العشاء بعد 00:00)
        """
        for day in (now.date(), now.date() - timedelta(days=1)):
            # اليوم السابق: نحسب نوافذه مباشرة دون كاش
            windows = (
                self._windows_for(day)
                if day == now.date()
                else self._build_windows(day)
            )
            for w in windows:
                if w.mute_start <= now < w.lock_end:
                    ar = self.PRAYER_NAMES_AR.get(w.name, w.name)
                    if now < w.adhan:
                        return (
                            True,
                            f"كتم استباقي قبل أذان {ar} (متبقي {(w.adhan - now).seconds // 60} د)",
                        )
                    elif now < w.adhan + timedelta(minutes=self.prayer_duration_min):
                        return True, f"حظر Interlock - وقت صلاة {ar}"
                    else:
                        return (
                            True,
                            f"حظر ما بعد صلاة {ar} (+{self.config.post_prayer_lock_min} د)",
                        )
        return False, ""

    def athan_moment(self, now: datetime, window_sec: int = 60) -> Optional[str]:
        """اسم الصلاة إذا كنا ضمن نافذة لحظة الأذان، وإلا None."""
        for day in (now.date(), now.date() - timedelta(days=1)):
            windows = (
                self._windows_for(day)
                if day == now.date()
                else self._build_windows(day)
            )
            for w in windows:
                delta = (now - w.adhan).total_seconds()
                if 0 <= delta < window_sec:
                    return w.name
        return None

    def next_prayer(self, now: datetime) -> Optional[PrayerWindow]:
        windows = sorted(self._windows_for(now.date()), key=lambda w: w.adhan)
        for w in windows:
            if w.adhan > now:
                return w
        # صلاة فجر الغد
        tomorrow = self._build_windows(now.date() + timedelta(days=1))
        return min(tomorrow, key=lambda w: w.adhan)
