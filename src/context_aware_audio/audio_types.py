"""
audio_types.py - أنواع البيانات المشتركة
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import time


class DayPeriod(str, Enum):
    FAJR_SABAH = "fajr_sabah"  # 05:00-08:00
    DUHA_WORK = "duha_work"  # 08:00-13:00
    LUNCH = "lunch"  # 13:00-14:00
    MAQIL = "maqil"  # 14:00-18:00
    MAGHRIB_ISHA = "maghrib_isha"  # 18:00-20:00
    SAMRA = "samra"  # 20:00-23:00
    NIGHT_SLEEP = "night_sleep"  # 23:00-05:00


class EngineState(str, Enum):
    """حالة المحرك الحالية (للعرض والتشخيص)"""

    DAILY_AMBIENT = "daily_ambient"
    DUCKED = "ducked"  # خفض أثناء الكلام العادي
    DEBATE_MUTED = "debate_muted"  # كتم بسبب نقاش حامي
    WELCOME = "welcome"  # ترحيب ضيوف
    CONTEMPLATION_FADE = "contemplation_fade"  # هدوء مفاجئ -> Fade-In
    PRAYER_MUTED = "prayer_muted"  # كتم وقت الصلاة
    SLEEP_SILENCE = "sleep_silence"  # وضع النوم



class ContentAction(str, Enum):
    """
    ما يجب أن يفعله مشغّل المحتوى هذه النبضة.

    NONE يعني «لا تغيير»: المسار يمرّ من غير لمس. كل قيمة أخرى أمر
    صريح، ليبقى أثر الإجراء في سجل التشغيل قابلاً للتتبع.
    """

    NONE = "none"  # لا تغيير
    START = "start"  # ابدأ التشغيل من الموضع
    PAUSE = "pause"  # أوقف مؤقتاً (مقاطعة كلام)
    RESUME = "resume"  # استأنف من الموضع المتراجع
    STOP = "stop"  # أنهِ وامسح (صلاة، أو إيقاف يدوي)
    FINISHED = "finished"  # انتهى المقطع طبيعياً

@dataclass
class AudioFrame:
    """إطار صوتي واحد من الميكروفون (محاكى أو حقيقي)"""

    timestamp: float = field(default_factory=time.time)
    db_level: float = 0.0  # شدة الصوت dB
    is_speech: bool = False  # نتيجة VAD بعد فلتر الثبات
    is_speech_raw: bool = False  # تجاوز العتبة في هذا الإطار وحده
    is_overlapping: bool = False  # هل هناك تداخل أصوات (صخب)؟
    threshold_db: float = 40.0  # عتبة الكلام السارية وقت هذا الإطار
    is_greeting_tone: bool = False  # نبرة ترحيب مرتفعة
    raw_energy: float = 0.0  # طاقة خام (اختياري)


@dataclass
class PlaybackCommand:
    """أمر إخراج صوتي يصدره المحرك"""

    file: Optional[str]  # None = كتم
    target_db: float  # المستوى المطلوب
    volume_ratio: float = 1.0  # نسبة الحجم 0.0-1.0
    fade_duration_sec: float = 0.0
    state: EngineState = EngineState.DAILY_AMBIENT
    reason: str = ""
    is_muted: bool = False

    # ---- مسار المحتوى ----
    # قيم افتراضية صريحة: كل موضع إنشاء للأمر في الشيفرة القائمة
    # والاختبارات يبقى بلا تغيير، والمحتوى غائب حتى يُملأ.
    content_file: Optional[str] = None  # اسم ملف المحتوى
    content_action: ContentAction = ContentAction.NONE  # الإجراء المطلوب
    content_volume: float = 0.0  # مستوى المحتوى 0.0-1.0
    content_position_sec: float = 0.0  # الموضع داخل المقطع

    def _content_suffix(self) -> str:
        """وصف مختصر لحالة المحتوى، أو نص فارغ إن لم يتغيّر شيء."""
        if self.content_action is ContentAction.NONE:
            return ""
        name = self.content_file or "-"
        at = f"@{self.content_position_sec:.0f}s" if self.content_position_sec else ""
        return f" | محتوى: {self.content_action.value} {name}{at}"

    def __str__(self):
        tail = self._content_suffix()
        if self.is_muted or self.file is None:
            return f"🔇 MUTE [{self.state.value}] - {self.reason}{tail}"
        return (
            f"🔊 {self.file} @ {self.target_db}dB vol={self.volume_ratio:.0%} "
            f"fade={self.fade_duration_sec}s [{self.state.value}] {self.reason}{tail}"
        )
