"""
sim_clock.py - محاكاة ساعة الحائط لتجربة المحرك في أي لحظة من اليوم

الوحدة لا تعرف شيئاً عن المحرك ولا عن الواجهة: تعطي وقتاً
مزيفاً عند الطلب، وتترك الوقت الحقيقي متاحً دائماً.

الفصل ضروري لأن مؤقتات المحرك تعمل على الزمن الحقيقي:
لو تحولت إلى الزمن المحاكى لأمكن فكّها بقفزة واحدة في الساعة.

الاستخدام المقبول في الواجهة:
- now(): كل ما يُمر للمحرك ليقرر (الساعة، نافذة الصلاة، الفترة)
- real_now(): كل ما يُقاس بالزمن (طوابع الإطارات، تواريخ السجل)
"""

from datetime import datetime, timedelta

MAX_OFFSET_SEC = 86_400  # يوم كامل - كافي لمحاكاة معقولة


class SimClock:
    """ساعة ذات إزاحة اختيارة، معطّلة دائماً عند الإنشاء."""

    def __init__(self, offset_sec: float = 0.0):
        self._enabled = False
        self._offset_sec = self._clamp(offset_sec)

    @staticmethod
    def _clamp(seconds: float) -> float:
        """يحد الإزاحة إلى يوم واحد أرقام، ويُقابض النص غير النطاع."""
        try:
            value = float(seconds)
        except (TypeError, ValueError):
            return 0.0
        if value != value or value in (float("inf"), float("-inf")):
            return 0.0
        return max(-MAX_OFFSET_SEC, min(MAX_OFFSET_SEC, value))

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def offset_sec(self) -> float:
        return self._offset_sec

    @offset_sec.setter
    def offset_sec(self, value: float) -> None:
        self._offset_sec = self._clamp(value)

    def real_now(self) -> datetime:
        """الوقت الحقيقي دائماً، حتى أثناء المحاكاة."""
        return datetime.now()

    def now(self) -> datetime:
        """الوقت المحاكى عند التفعيل، وإلا الحقيقي."""
        if not self._enabled or not self._offset_sec:
            return datetime.now()
        return datetime.now() + timedelta(seconds=self._offset_sec)

    def set_hhmmss(self, hour: int, minute: int, second: int = 0) -> None:
        """
        يضبط الإزاحة لتُرجع هذه اللحظة من يوم اليوم.

        الهدف يوم الحالي لأن يبقى التاريخ متسقاً
        مع جدول اليوم وأوقات الصلاة.
        """
        hour = max(0, min(23, int(hour)))
        minute = max(0, min(59, int(minute)))
        second = max(0, min(59, int(second)))
        target = datetime.now().replace(
            hour=hour, minute=minute, second=second, microsecond=0
        )
        self._offset_sec = self._clamp((target - datetime.now()).total_seconds())

    def nudge(self, hours: float) -> None:
        """يزيح الساعة بمقدار الساعات (سالب للتراجع)."""
        self._offset_sec = self._clamp(self._offset_sec + hours * 3600.0)

    def reset(self) -> None:
        """يعيد الساعة إلى الحالة الحقيقية: تعطيل وإزاحة صفر."""
        self._enabled = False
        self._offset_sec = 0.0

    def __repr__(self) -> str:
        mode = "محاكى" if self._enabled else "حقيقي"
        return f"<SimClock {mode} إزاحة={self._offset_sec:+.0f}ث>"
