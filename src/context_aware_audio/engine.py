"""
engine.py - محرك القرار المركزي (قلب النظام)
القرار = f(VAD, dB, السياق الزمني, حالة الصلاة)
الأولوية: الصلاة > نقاش حامي > ترحيب > هدوء مفاجئ > Ducking/يومي > نوم

كل العتبات والأزمنة والمستويات تأتي من EngineConfig - لا أرقام مكتوبة هنا.
"""

from datetime import datetime
from typing import Optional
from .config import EngineConfig
from .audio_types import AudioFrame, PlaybackCommand, EngineState, DayPeriod
from .cultural_calendar import CulturalCalendar
from .prayer_engine import PrayerEngine


class ContextAwareAudioEngine:
    """محرك الصوت التكيفي - النسخة اليمنية"""

    def __init__(self, config: Optional[EngineConfig] = None):
        self.config = config or EngineConfig()
        self.calendar = CulturalCalendar(self.config)
        self.prayer = PrayerEngine(self.config)

        # --- حالة داخلية (timers) ---
        self._last_speech_time: Optional[float] = None  # آخر مرة سُمع فيها كلام
        self._silence_since: Optional[float] = None  # بداية الصمت الحالي
        self._last_loud_time: Optional[float] = None  # آخر صوت > 65dB
        self._in_debate_mute: bool = False
        self._welcome_until: float = 0.0
        self._last_activity_time: Optional[float] = None  # أي صوت فوق العتبة الدنيا
        self._last_command: Optional[PlaybackCommand] = None

    # ---------- API رئيسي ----------
    def process_frame(
        self, frame: AudioFrame, now: Optional[datetime] = None
    ) -> PlaybackCommand:
        now_dt = now or datetime.now()
        ts = frame.timestamp
        cfg = self.config

        # تحديث المؤقتات: الصمت يُقاس من آخر كلام مسموع
        if frame.is_speech:
            self._last_speech_time = ts
            self._silence_since = None
        else:
            if self._silence_since is None:
                # إذا كان هناك كلام سابق، الصمت بدأ لحظة انتهائه
                self._silence_since = (
                    self._last_speech_time if self._last_speech_time is not None else ts
                )

        if (
            frame.db_level > cfg.activity_threshold_db
            or self._last_activity_time is None
        ):
            self._last_activity_time = ts

        if frame.db_level >= cfg.loud_debate_threshold_db and frame.is_speech:
            self._last_loud_time = ts

        period = self.calendar.period_for_datetime(now_dt)
        base = self.calendar.sound_for_period(period)

        # === 1) الصلاة (أعلى أولوية) ===
        prayer_muted, prayer_reason = self.prayer.check(now_dt)
        if prayer_muted:
            self._in_debate_mute = False  # الصلاة تلغي أي حالة أخرى
            return self._cmd(
                None,
                0,
                0.0,
                0.0,
                EngineState.PRAYER_MUTED,
                f"🕌 {prayer_reason}",
                muted=True,
            )

        # === 2) نقاش حامي > 65dB ===
        if frame.db_level >= cfg.loud_debate_threshold_db and (
            frame.is_speech or frame.is_overlapping
        ):
            self._in_debate_mute = True
            self._last_loud_time = ts
            return self._cmd(
                None,
                0,
                0.0,
                cfg.fade_out_duration_sec,
                EngineState.DEBATE_MUTED,
                f"نقاش حامي {frame.db_level:.0f}dB - كتم تلقائي",
                muted=True,
            )

        if self._in_debate_mute:
            # انتبه: لا تستخدم `or` هنا - الطابع 0.0 قيمة شرعية وليس غياباً
            since_loud = (
                self._last_loud_time if self._last_loud_time is not None else ts
            )
            calm_sec = ts - since_loud
            if calm_sec >= cfg.debate_cooldown_sec:
                self._in_debate_mute = False  # هدأ المجلس 60 ثانية -> عودة
            else:
                return self._cmd(
                    None,
                    0,
                    0.0,
                    0.0,
                    EngineState.DEBATE_MUTED,
                    f"انتظار هدوء المجلس ({calm_sec:.0f}/{cfg.debate_cooldown_sec:.0f} ث)",
                    muted=True,
                )

        # === 3) ترحيب ضيوف ===
        if frame.is_greeting_tone:
            self._welcome_until = ts + cfg.welcome_hold_sec
        if ts < self._welcome_until:
            w = cfg.special_sounds["welcome"]
            return self._cmd(
                w["file"],
                w["db"],
                cfg.welcome_duck_ratio,
                cfg.fade_out_duration_sec,
                EngineState.WELCOME,
                "ترحيب ضيوف - شلال بعيد",
            )

        # === 4) وضع النوم: ليل + 5 دقائق بلا نشاط ===
        if period == DayPeriod.NIGHT_SLEEP:
            since_activity = (
                self._last_activity_time if self._last_activity_time is not None else ts
            )
            inactive_sec = ts - since_activity
            if inactive_sec >= cfg.sleep_no_activity_sec:
                night = cfg.period_sounds["night_sleep"]
                # إيقاف أو صرار خفيف 20dB حسب الإعداد
                if night["file"] is None:
                    return self._cmd(
                        None,
                        0,
                        0.0,
                        cfg.fade_out_duration_sec,
                        EngineState.SLEEP_SILENCE,
                        "ليل + سكون 5د - إيقاف تام",
                        muted=True,
                    )
                return self._cmd(
                    night["file"],
                    night["db"],
                    1.0,
                    cfg.fade_in_duration_sec,
                    EngineState.SLEEP_SILENCE,
                    "ليل + سكون 5د - صرار خفيف 20dB",
                )

        # === 5) هدوء مفاجئ > 10 ثوانٍ -> Fade-In ===
        if not frame.is_speech and self._silence_since is not None:
            silence_sec = ts - self._silence_since
            if silence_sec >= cfg.silence_for_fade_in_sec and base.get("file"):
                # في المقيل: ريح البن، وإلا الصوت اليومي المعتاد
                if period == DayPeriod.MAQIL:
                    c = cfg.special_sounds["contemplation"]
                    return self._cmd(
                        c["file"],
                        c["db"],
                        1.0,
                        cfg.fade_in_duration_sec,
                        EngineState.CONTEMPLATION_FADE,
                        f"هدوء {silence_sec:.0f}ث - Fade-In ريح البن",
                    )
                return self._cmd(
                    base["file"],
                    base["db"],
                    1.0,
                    cfg.fade_in_duration_sec,
                    EngineState.CONTEMPLATION_FADE,
                    f"هدوء {silence_sec:.0f}ث - عودة تدريجية",
                )

        # === 6) Ducking أثناء الكلام العادي ===
        if frame.is_speech and base.get("file"):
            ratio = self._duck_ratio(frame.db_level)
            return self._cmd(
                base["file"],
                base["db"],
                ratio,
                cfg.fade_out_duration_sec,
                EngineState.DUCKED,
                f"كلام {frame.db_level:.0f}dB - خفض {(1 - ratio) * 100:.0f}%",
            )

        # === 7) الوضع اليومي الافتراضي ===
        if base.get("file") is None:
            # فترة كتم مجدولة (المغرب/العشاء خارج الصلاة)
            return self._cmd(
                None,
                0,
                0.0,
                cfg.fade_out_duration_sec,
                EngineState.DAILY_AMBIENT,
                "فترة عبادة - خلفية متوقفة",
                muted=True,
            )
        return self._cmd(
            base["file"],
            base["db"],
            1.0,
            cfg.fade_in_duration_sec,
            EngineState.DAILY_AMBIENT,
            f"وضع يومي: {base.get('label', '')}",
        )

    # ---------- أدوات ----------
    def _duck_ratio(self, db: float) -> float:
        """كلما ارتفع الصوت زاد الخفض: 40dB->30% ، 65dB->10%"""
        cfg = self.config
        lo, hi = cfg.speech_threshold_db, cfg.loud_debate_threshold_db
        if db <= lo:
            return cfg.ducking_max_ratio
        if db >= hi:
            return cfg.ducking_min_ratio
        t = (db - lo) / max(hi - lo, 1e-6)
        return cfg.ducking_max_ratio - t * (
            cfg.ducking_max_ratio - cfg.ducking_min_ratio
        )

    def _cmd(
        self, file, db, ratio, fade, state, reason, muted=False
    ) -> PlaybackCommand:
        cmd = PlaybackCommand(
            file=file,
            target_db=db,
            volume_ratio=ratio,
            fade_duration_sec=fade,
            state=state,
            reason=reason,
            is_muted=muted or file is None,
        )
        self._last_command = cmd
        return cmd

    @property
    def last_command(self) -> Optional[PlaybackCommand]:
        return self._last_command

    def reset(self) -> None:
        self._last_speech_time = None
        self._silence_since = None
        self._last_loud_time = None
        self._in_debate_mute = False
        self._welcome_until = 0.0
        self._last_activity_time = None
        self._last_command = None
