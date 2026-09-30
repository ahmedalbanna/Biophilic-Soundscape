"""
vad.py - كشف النشاط الصوتي ومعالجة الإشارة

- analyze_frame: المسار الحيّ. يقارن dB بعتبة ديناميكية ثم يمرّ
  بفلتر ثبات وتخلّف. الواجهة تستدعيه بإطار كل 200ms.
- analyze_pcm: مسار مستقبلي لـ webrtcvad عند توفره. غير موصول
  بالميكروفون بعد، فوجوده في الشيفرة لا يعني أنه يعمل.
- معايرة: set_noise_floor() يرفع عتبة الكلام فوق ضجيج الغرفة.
"""

import importlib.util
import math
import time
from collections import deque
from typing import Deque, Optional, Tuple

from .audio_types import AudioFrame
from .config import EngineConfig

# فرق طوابع الزمن العائمة: (3000.2 - 3000.0) = 0.1999999999998181
# فالمقارنة الحرفية بـ 0.2 تفشل عند أرقام مستديرة. التسامح أصغر
# بكثير من أي فاصل إطارات حقيقي، فيصلح الحساب ولا يوسّع النافذة فعلياً.
EPS = 1e-6


def elapsed(ts: float, start: Optional[float], span: float) -> bool:
    """
    هل مضى من `start` ما يفي بـ `span`؟

    존재ت هذه المقارنة موزّعة في أربعة مواضع، وأخطأ واحد منها
    (استمرار نبرة الترحيب) كان يفشل عند حدّ النافذة بالضبط بسبب
    فرق الأعداد العشرية: 100.8 - 100.5 = 0.2999999999999545.
    توحيدها هنا يمنع تكرار الخطأ عند كل مقارنة جديدة.
    """
    if start is None:
        return False
    return ts - start >= span - EPS


class VadProcessor:
    """
    معالج VAD خفيف:
    - القرار يمرّ بفلتر ثبات: إطار واحد لا يكفي
    - is_speech_raw يكشف تجاوز العتبة لحظياً، وis_speech يقرّر بعد الثبات
    - is_overlapping يمرّ بالفلتر نفسه: كتم النقاش أغلى قرار بالنظام
    - نبرة الترحيب: ارتفاع خلال نافذة محددة + بقاء فوق العتبة
    - analyze_pcm يستخدم webrtcvad عند توفره، وإلا الطاقة فقط
    """

    def __init__(self, config: EngineConfig):
        self.config = config
        self._prev_db: float = 0.0
        self._has_prev: bool = False
        self.noise_floor_db: Optional[float] = None
        # فلتر الثبات
        self._above_since: Optional[float] = None
        self._below_since: Optional[float] = None
        self._speech_confirmed: bool = False
        # نافذة الترحيب: نحتفظ بأقصى قفزة خلال فترة الاستمرار
        self._elevated_since: Optional[float] = None
        self._peak_jump_db: float = 0.0
        self._last_greeting_time: float = 0.0
        # تاريخ المستويات لقياس القفزة على نافذة حقيقية لا إطار واحد
        self._history: Deque[Tuple[float, float]] = deque(maxlen=64)
        # -inf حتى لا تحجب أول عملية تأكيد cooldown عند أي طابع زمني
        self._last_confirmed_speech: float = float("-inf")
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
        # لا يُنسى أي حقل: المتبقي يتسرب بين السيناريوهات والاختبارات
        self._above_since = None
        self._below_since = None
        self._speech_confirmed = False
        self._history.clear()
        self._last_confirmed_speech = float("-inf")

    def speech_threshold(self) -> float:
        """عتبة الكلام: لا تقل عن speech_threshold_db، وترتفع فوق ضجيج الغرفة."""
        base = self.config.speech_threshold_db
        if self.noise_floor_db is None:
            return base
        return max(base, self.noise_floor_db + self.config.noise_floor_margin_db)

    def _window_jump(self, ts: float, db_level: float) -> float:
        """
        أقصى ارتفاع خلال نافذة بداية النبرة، لا منذ الإطار السابق.

        القياس من الإطار الأخير يجعل أي ارتفاع 200ms qualify كبداية،
        فيستحق greeting_onset_window_sec صامتاً. نقيس من أقدم عينة
        ما زالت داخل النافذة.
        """
        window = self.config.greeting_onset_window_sec
        baseline: Optional[float] = None
        # من الأحدث للأقدم: القطع عند أول عينة خارج النافذة.
        for sample_ts, sample_db in reversed(self._history):
            if ts - sample_ts > window:
                break
            baseline = sample_db if baseline is None else min(baseline, sample_db)
        if baseline is None:
            return 0.0
        return db_level - baseline

    def _update_speech(self, db_level: float, ts: float) -> Tuple[bool, bool]:
        """
        يحدّث فلتر الثبات ويعيد (is_speech_raw, is_speech).

        الدخول عند speech_threshold، والخروج عند threshold - hysteresis،
        فلا يطنّش قرارٌ على حدّ العتبة. والتأكيد يحتاج بقاءً
        speech_onset_sec، والإبطال يحتاج هبوطاً speech_release_sec.
        """
        cfg = self.config
        enter = self.speech_threshold()
        leave = enter - cfg.speech_hysteresis_db
        raw = db_level >= enter

        if not self._speech_confirmed:
            if raw:
                if self._above_since is None:
                    self._above_since = ts
                held = elapsed(ts, self._above_since, cfg.speech_onset_sec)
                cooled = elapsed(
                    ts,
                    self._last_confirmed_speech,
                    cfg.speech_retrigger_cooldown_sec,
                )
                if held and cooled:
                    self._speech_confirmed = True
                    self._last_confirmed_speech = ts
                    self._below_since = None
            else:
                self._above_since = None
        else:
            if db_level < leave:
                if self._below_since is None:
                    self._below_since = ts
                if elapsed(ts, self._below_since, cfg.speech_release_sec):
                    self._speech_confirmed = False
                    self._above_since = None
                    self._below_since = None
            else:
                # داخل نطاق التخلفي: نبقى متكلّفين ولا نعيد حساب الإبطال
                self._below_since = None

        return raw, self._speech_confirmed

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

        sustained = elapsed(ts, self._elevated_since, cfg.greeting_sustain_sec)
        cooled = elapsed(ts, self._last_greeting_time, cfg.greeting_cooldown_sec)
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

        is_speech_raw, is_speech = self._update_speech(db_level, ts)
        # الكتم أغلى قرار: يمرّ بالفلتر نفسه لا بمقارنة العتبة وحدها
        is_overlapping = is_speech and db_level >= cfg.loud_debate_threshold_db
        jump = self._window_jump(ts, db_level)
        is_greeting_tone = self._detect_greeting(db_level, jump, is_speech, ts)

        frame = AudioFrame(
            timestamp=ts,
            db_level=db_level,
            is_speech=is_speech,
            is_speech_raw=is_speech_raw,
            is_overlapping=is_overlapping,
            is_greeting_tone=is_greeting_tone,
            raw_energy=self.db_to_energy(db_level),
        )
        self._prev_db = db_level
        self._has_prev = True
        self._history.append((ts, db_level))
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

        usable = len(pcm_bytes) - (len(pcm_bytes) % 2)
        samples = np.frombuffer(pcm_bytes[:usable], dtype="<i2")
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
