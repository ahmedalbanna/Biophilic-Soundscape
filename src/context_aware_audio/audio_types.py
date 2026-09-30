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

    def __str__(self):
        if self.is_muted or self.file is None:
            return f"🔇 MUTE [{self.state.value}] - {self.reason}"
        return f"🔊 {self.file} @ {self.target_db}dB vol={self.volume_ratio:.0%} fade={self.fade_duration_sec}s [{self.state.value}] {self.reason}"
