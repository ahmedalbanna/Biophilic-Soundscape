"""
vad.py - كشف النشاط الصوتي ومعالجة الإشارة
- وضع محاكاة: يستقبل AudioFrame جاهزة
- وضع حقيقي: يحلل مصفوفة PCM عبر طاقة الإشارة + webrtcvad (اختياري)
- معايرة: set_noise_floor() يرفع عتبة الكلام فوق ضجيج الغرفة
"""

import importlib.util
import math
import time
from typing import Optional

from .audio_types import AudioFrame
from .config import EngineConfig


class VadProcessor:
    """
    معالج VAD خفيف:
    - is_speech من تجاوز عتبة الكلام (ديناميكية بعد المعايرة)
    - is_overlapping عند تجاوز عتبة النقاش الحامي
    - نبرة الترحيب: قفزة بداية + استمرار، مع تهدئة
    - analyze_pcm يستخدم webrtcvad عند توفره، وإلا الطاقة فقط
    """

    def __init__(self, config: EngineConfig):
        self.config = config
        self._prev_db: float = 0.0
        self._has_prev: bool = False
        self.noise_floor_db: Optional[float] = None
        # نافذة الترحيب: نحتفظ بأقصى قفزة خلال فترة الاستمرار
        self._elevated_since: Optional[float] = None
        self._peak_jump_db: float = 0.0
        self._last_greeting_time: float = 0.0
        self.webrtc_available: bool = importlib.util.find_spec("webrtcvad") is not None
        self._webrtc_vad = None
        if self.webrtc_available:
            try:
                import webrtcvad

                self._webrtc_vad = webrtcvad.Vad(2)
            except Exception:
                self.webrtc_available = False
                self._webrtc_vad = None

    def set_noise_floor(self, floor_db: float) -> None:
        """يضبط أرضية ضجيج الغرفة ليرفع عتبة الكلام فوقها."""
        self.noise_floor_db = max(0.0, min(80.0, floor_db))

    def reset(self) -> None:
        """
        يمسح كل حالة الكشف.

        الواجهة تستدعي analyze_frame حتى أثناء التوقف، فيتراكم نصف نافذة نبرة
        الترحيب ثم يُستهلك بعد التشغيل - نغمة ترحيب تتأخر ثوانٍ
        بعد ضغط المستخدم على "تشغيل". يُستدعى مع engine.reset().
        """
        self._prev_db = 0.0
        self._has_prev = False
        self._elevated_since = None
        self._peak_jump_db = 0.0
        self._last_greeting_time = 0.0

    def speech_threshold(self) -> float:
        """عتبة الكلام: لا تقل عن speech_threshold_db، وترتفع فوق ضجيج الغرفة."""
        base = self.config.speech_threshold_db
        if self.noise_floor_db is None:
            return base
        return max(base, self.noise_floor_db + self.config.noise_floor_margin_db)

    def _detect_greeting(
        self, db_level: float, jump: float, is_speech: bool, ts: float
    ) -> bool:
        """
        نبرة الترحيب = قفزة بداية + بقاء فوق العتبة.

        القفزة تحدث في إطار البداية فقط، والاستمرار يتحقق بعد 0.3ث،
        فلا يمكن أن يتحقق الشرطان في الإطار نفسه. لذلك نحتفظ
        بأكبر قفزة رصدناها خلال نافذة الاستمرار.
        """
        cfg = self.config
        elevated = db_level > cfg.greeting_min_db and is_speech
        if not elevated or not self._has_prev:
            # بلا إطار سابق لا توجد "قفزة بداية" - أول عينة ليست قفزة
            self._elevated_since = None
            self._peak_jump_db = 0.0
            return False

        if self._elevated_since is None:
            self._elevated_since = ts
            self._peak_jump_db = jump
        else:
            self._peak_jump_db = max(self._peak_jump_db, jump)

        sustained = ts - self._elevated_since >= cfg.greeting_sustain_sec
        cooled = ts - self._last_greeting_time >= cfg.greeting_cooldown_sec
        if not (sustained and cooled):
            return False
        if self._peak_jump_db < cfg.greeting_min_jump_db:
            return False

        self._last_greeting_time = ts
        self._elevated_since = None
        self._peak_jump_db = 0.0
        return True

    def analyze_frame(
        self, db_level: float, timestamp: Optional[float] = None
    ) -> AudioFrame:
        """يحلل مستوى dB خام ويعيد AudioFrame مصنفاً."""
        cfg = self.config
        ts = timestamp if timestamp is not None else time.time()
        is_speech = db_level >= self.speech_threshold()
        is_overlapping = is_speech and db_level >= cfg.loud_debate_threshold_db
        jump = db_level - self._prev_db
        is_greeting_tone = self._detect_greeting(db_level, jump, is_speech, ts)

        frame = AudioFrame(
            timestamp=ts,
            db_level=db_level,
            is_speech=is_speech,
            is_overlapping=is_overlapping,
            is_greeting_tone=is_greeting_tone,
            raw_energy=self.db_to_energy(db_level),
        )
        self._prev_db = db_level
        self._has_prev = True
        return frame

    def analyze_pcm(self, pcm_bytes: bytes, sample_rate: int = 16000) -> AudioFrame:
        """تحليل PCM حقيقي (16-bit mono): RMS -> dB، مع دمج قرار webrtcvad إن وُجد."""
        db = self.pcm_to_db(pcm_bytes)
        frame = self.analyze_frame(db)
        if self._webrtc_vad is not None and sample_rate in (8000, 16000, 32000, 48000):
            voiced_ratio = self._voiced_ratio(pcm_bytes, sample_rate)
            if voiced_ratio is not None:
                frame.is_speech = bool(frame.is_speech and voiced_ratio > 0.3)
                frame.is_overlapping = bool(
                    frame.is_speech and db >= self.config.loud_debate_threshold_db
                )
        return frame

    def _voiced_ratio(self, pcm_bytes: bytes, sample_rate: int) -> Optional[float]:
        """نسبة المقاطع الصوتية من الإجمالي، أو None إن تعذر التحليل."""
        try:
            step = sample_rate // 50  # 20ms لكل مقطع
            needed = step * 2
            total = voiced = 0
            for i in range(0, len(pcm_bytes) - needed + 1, needed):
                total += 1
                if self._webrtc_vad.is_speech(pcm_bytes[i : i + needed], sample_rate):
                    voiced += 1
            return voiced / total if total else None
        except Exception:
            return None

    @staticmethod
    def pcm_to_db(pcm_bytes: bytes) -> float:
        """
        PCM16 little-endian -> dB (0..100) عبر RMS بطابع numpy.

        numpy يُستورد هنا لا في الأعلى: استيراد الحزمة يجب ألا يجرّ مكتبات ثقيلة.
        """
        if not pcm_bytes:
            return 0.0
        import numpy as np

        samples = np.frombuffer(
            pcm_bytes[: len(pcm_bytes) - (len(pcm_bytes) % 2)], dtype="<i2"
        )
        if samples.size == 0:
            return 0.0
        rms = float(np.sqrt(np.mean(np.square(samples.astype(np.float64)))))
        if rms <= 0.0:
            return 0.0
        db = 20 * math.log10(rms / 32768) + 100
        return max(0.0, min(100.0, db))

    @staticmethod
    def db_to_energy(db: float) -> float:
        return math.pow(10, db / 20) if db > 0 else 0.0
