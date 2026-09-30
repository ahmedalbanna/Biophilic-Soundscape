"""
engine.py - محرك القرار المركزي (قلب النظام)
القرار = f(VAD, dB, السياق الزمني, حالة الصلاة)
الأولوية: الصلاة > نقاش حامي > ترحيب > هدوء مفاجئ > Ducking/يومي > نوم

كل العتبات والأزمنة والمستويات تأتي من EngineConfig - لا أرقام مكتوبة هنا.
"""

from datetime import datetime
from typing import Optional
from .config import EngineConfig
from .audio_types import (
    AudioFrame,
    ContentAction,
    DayPeriod,
    EngineState,
    PlaybackCommand,
)
from .cultural_calendar import CulturalCalendar
from .prayer_engine import PrayerEngine
from .content_engine import ContentEngine


# أدنى نسبة صوت يبقّيها الخفض: لا تنزل تحت 2% فتصمت الخلفية فعلياً،
# وهدوء ناعم هو كل ما نريده. الكتم التام حكر على نافذة الصلاة والنقاش.
MIN_DUCK_RATIO = 0.02
class ContextAwareAudioEngine:
    """محرك الصوت التكيفي - النسخة اليمنية"""

    def __init__(self, config: Optional[EngineConfig] = None):
        self.config = config or EngineConfig()
        self.calendar = CulturalCalendar(self.config)
        self.prayer = PrayerEngine(self.config)
        # مسار المحتوى: متعاون لا منافس. المخزن يُحقن لاحقاً من الواجهة
        # (content_store) لأنه يحتاج مسار بيانات المستخدم لا الإعدادات.
        self.content = ContentEngine(self.config)

        # --- حالة داخلية (timers) ---
        self._last_speech_time: Optional[float] = None  # آخر مرة سُمع فيها كلام
        self._silence_since: Optional[float] = None  # بداية الصمت الحالي
        self._last_loud_time: Optional[float] = None  # آخر صوت > 65dB
        self._in_debate_mute: bool = False
        self._welcome_until: float = 0.0
        # نافذة الخفض: آخر لحظة خُفض فيها الصوت، وآخر نسبة خُفض إليها
        self._last_duck_time: Optional[float] = None
        self._last_duck_ratio: float = 1.0
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
            # الصلاة فوق كل شيء: ينتهي السرد ويُصفَّف قبل الإرجاع،
            # فلا يبقى مؤقّت معلّق يعيد التشغيل بعد دقائق.
            _cc = self.content.force_stop(f"🕌 {prayer_reason}")
            return self._attach(self._cmd(
                None,
                0,
                0.0,
                0.0,
                EngineState.PRAYER_MUTED,
                f"🕌 {prayer_reason}",
                muted=True,
            ), _cc)

        # === 2) نقاش حامي > 65dB ===
        if frame.db_level >= cfg.loud_debate_threshold_db and (
            frame.is_speech or frame.is_overlapping
        ):
            self._in_debate_mute = True
            self._last_loud_time = ts
            _cc = self.content.force_pause(f"نقاش حامي {frame.db_level:.0f}dB")
            return self._attach(self._cmd(
                None,
                0,
                0.0,
                cfg.fade_out_duration_sec,
                EngineState.DEBATE_MUTED,
                f"نقاش حامي {frame.db_level:.0f}dB - كتم تلقائي",
                muted=True,
            ), _cc)

        if self._in_debate_mute:
            # انتبه: لا تستخدم `or` هنا - الطابع 0.0 قيمة شرعية وليس غياباً
            since_loud = (
                self._last_loud_time if self._last_loud_time is not None else ts
            )
            calm_sec = ts - since_loud
            if calm_sec >= cfg.debate_cooldown_sec:
                self._in_debate_mute = False  # هدأ المجلس 60 ثانية -> عودة
            else:
                # المجلس ما زال صاخباً: السرد يبقى متوقفاً
                _cc = self.content.force_pause("المجلس ما زال صاخباً")
                return self._attach(self._cmd(
                    None,
                    0,
                    0.0,
                    0.0,
                    EngineState.DEBATE_MUTED,
                    f"انتظار هدوء المجلس ({calm_sec:.0f}/{cfg.debate_cooldown_sec:.0f} ث)",
                    muted=True,
                ), _cc)

        # === 3) ترحيب ضيوف ===
        if frame.is_greeting_tone:
            self._welcome_until = ts + cfg.welcome_hold_sec
        if ts < self._welcome_until:
            w = cfg.special_sounds["welcome"]
            return self._with_content(self._cmd(
                w["file"],
                w["db"],
                cfg.welcome_duck_ratio,
                cfg.fade_out_duration_sec,
                EngineState.WELCOME,
                "ترحيب ضيوف - شلال بعيد",
            ), frame, ts, now_dt, period)

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
                    return self._with_content(self._cmd(
                        None,
                        0,
                        0.0,
                        cfg.fade_out_duration_sec,
                        EngineState.SLEEP_SILENCE,
                        "ليل + سكون 5د - إيقاف تام",
                        muted=True,
                    ), frame, ts, now_dt, period)
                return self._with_content(self._cmd(
                    night["file"],
                    night["db"],
                    1.0,
                    cfg.fade_in_duration_sec,
                    EngineState.SLEEP_SILENCE,
                    "ليل + سكون 5د - صرار خفيف 20dB",
                ), frame, ts, now_dt, period)

        # === 5) هدوء مفاجئ > 10 ثوانٍ -> Fade-In ===
        if not frame.is_speech and self._silence_since is not None:
            silence_sec = ts - self._silence_since
            if silence_sec >= cfg.silence_for_fade_in_sec and base.get("file"):
                # في المقيل: ريح البن، وإلا الصوت اليومي المعتاد
                if period == DayPeriod.MAQIL:
                    c = cfg.special_sounds["contemplation"]
                    return self._with_content(self._cmd(
                        c["file"],
                        c["db"],
                        1.0,
                        cfg.fade_in_duration_sec,
                        EngineState.CONTEMPLATION_FADE,
                        f"هدوء {silence_sec:.0f}ث - Fade-In ريح البن",
                    ), frame, ts, now_dt, period)
                return self._with_content(self._cmd(
                    base["file"],
                    base["db"],
                    1.0,
                    cfg.fade_in_duration_sec,
                    EngineState.CONTEMPLATION_FADE,
                    f"هدوء {silence_sec:.0f}ث - عودة تدريجية",
                ), frame, ts, now_dt, period)

        # === 6) Ducking أثناء الكلام العادي ===
        if frame.is_speech and base.get("file"):
            ratio = self._duck_ratio(frame.db_level, frame.threshold_db)
            self._last_duck_time = ts
            self._last_duck_ratio = ratio
            return self._with_content(self._cmd(
                base["file"],
                base["db"],
                ratio,
                cfg.fade_out_duration_sec,
                EngineState.DUCKED,
                f"كلام {frame.db_level:.0f}dB - خفض {(1 - ratio) * 100:.0f}%",
            ), frame, ts, now_dt, period)

        # === 6b) نافذة الخفض بعد الكلام ===
        # زفير 0.8ث كان يرسل الخلفية إلى 100% فوراً: يحرر الكلام بعد
        # 0.4ث، ثم ترفض التهدئة (3ث) إعادة التأكيد، فتنقلب الحالة بلا
        # سبب مرئي. نُبقي الخفض duck_hold_sec قبل الصعود.
        if (
            base.get("file")
            and self._last_duck_time is not None
            and ts - self._last_duck_time < cfg.duck_hold_sec
        ):
            return self._with_content(self._cmd(
                base["file"],
                base["db"],
                self._last_duck_ratio,
                cfg.fade_in_duration_sec,
                EngineState.DUCKED,
                f"استمرار الخفض {cfg.duck_hold_sec:.0f}ث",
            ), frame, ts, now_dt, period)

        # === 7) الوضع اليومي الافتراضي ===
        if base.get("file") is None:
            # فترة كتم مجدولة (المغرب/العشاء خارج الصلاة)
            return self._with_content(self._cmd(
                None,
                0,
                0.0,
                cfg.fade_out_duration_sec,
                EngineState.DAILY_AMBIENT,
                "فترة عبادة - خلفية متوقفة",
                muted=True,
            ), frame, ts, now_dt, period)
        return self._with_content(self._cmd(
            base["file"],
            base["db"],
            1.0,
            cfg.fade_in_duration_sec,
            EngineState.DAILY_AMBIENT,
            f"وضع يومي: {base.get('label', '')}",
        ), frame, ts, now_dt, period)

    @staticmethod
    def _attach(cmd, decision):
        """
        يضع قرار المحتوى على أمر الخلفية بلا تغيير مستوى الخلفية.

        فرعا الصلاة والنقاش الحامي يخرجان قبل البوابة، فيستدعيان
        محرك المحتوى بأنفسهما. لولا هذا المساعد لابتلع إيقاف الصلاة
        نتيجته: ينتهي السرد في الذاكرة ويظل المشغّل يبثّه.
        """
        cmd.content_file = decision.file
        cmd.content_action = decision.action
        cmd.content_volume = decision.volume
        cmd.content_position_sec = decision.position_sec
        if decision.action is not ContentAction.NONE:
            cmd.reason = f"{cmd.reason} | {decision.reason}"
        return cmd

    def _with_content(self, cmd, frame, ts, now, period):
        """
        البوابة الوحيدة لمسار المحتوى.

        تُستدعى من كل فرع يُخرج خلفية. فرعا الكتم (الصلاة والنقاش
        الحامي) لا يمران بها: الصلاة تُوقف السرد صراحةً، والنقاش
        يوقفه مؤقتاً. المحتوى متعاون لا منافس، فلا يدخل سلسلة
        الأولويات.

        نسبة الخلفية تُستبدل لا تُضرب. الضرب مع منحنى الخفض يجعل
        0.20 × 0.10 = 2% أي صمت تام، والخلفية تخفت تحت السرد ولا
        تختفي.
        """
        decision = self.content.update(
            frame, ts, now, period, enabled=self.config.content_enabled
        )
        # حقول المحتوى تُملأ في كل نبضة: المشغّل يحتاج الملف والموقع
        # في كل نبضة لا في نبضة الانتقال وحدها، وقراءة الواجهة كذلك.
        cmd.content_file = decision.file
        cmd.content_volume = decision.volume
        cmd.content_position_sec = decision.position_sec

        # أرضية الخلفية تُطبَّق ما دام المسار مسموعاً، لا عند الانتقال
        # فقط. الرجوع المبكر على NONE كان يرفع الخلفية إلى 100% في
        # النبضة التالية للبدء، فيسمع السرد فوق خلفية كاملة ثم
        # تخفت بعده.
        if decision.is_audible and not cmd.is_muted:
            cmd.volume_ratio = self.config.content_ambient_ratio

        if decision.action is not ContentAction.NONE:
            cmd.content_action = decision.action
            cmd.reason = f"{cmd.reason} | {decision.reason}"
        elif decision.is_audible and decision.reason:
            cmd.reason = f"{cmd.reason} | {decision.reason}"
        return cmd

    # ---------- أدوات ----------
    def _duck_ratio(self, db: float, threshold_db: float) -> float:
        """
        كلما ارتفع الصوت عن العتبة السارية زاد الخفض.

        المحور السفلي هو العتبة التي هبط عنها الصوت، لا قيمة ثابتة
        من الإعدادات. كان شكل المنحنى يقوده حقل إعدادات لا علاقة له
        بالقرار الذي يطلقه: في غرفة أرضيتها 30dB تكون العتبة 42،
        ومع ذلك يبدأ المنحنى عند 40 - أي قبل حدّ الكلام، فلا يقع
        أقصى خفض إلا في نطاق ضيق بلا أثر مسموع.

        التثبيت على العتبة يجعل أقصى خفض يقع عند حدّ الكلام تماماً،
        ويبقى 90% محجوزة لآخر 0.5dB قبل كتم النقاش. الحدّ الأعلى
        لا يتغير: 65dB قرار صريح لا منحنى.
        """
        lo, hi = threshold_db, self.config.loud_debate_threshold_db
        top = self.duck_max_ratio()
        bottom = self.duck_min_ratio()
        if db <= lo:
            return top
        if db >= hi:
            return bottom
        t = (db - lo) / max(hi - lo, 1e-6)
        return top - t * (top - bottom)

    def duck_max_ratio(self) -> float:
        """أقصى خفض: ما تبقّى من الصوت عند حدّ الكلام."""
        return max(0.0, 1.0 - self.config.duck_depth / 100.0)

    def duck_min_ratio(self) -> float:
        """
        أقل نسبة يبقاها الخفض، أي أقصى شدة.

        تُشتق من العمق بنسبة ثابتة (ثلث) لا قيمة مستقلة: عمق 90%
        عند 65dB غير قابل للوصول أصلاً لأن كتم النقاش يبدأ عندها
        بالضبط، فلا معنى لحقل يعد بعمق لا يستطيع بلوغه. «عميق» هنا
        تعني متحدثاً واحداً بصوت عالٍ أو تلفازاً، لا حشداً من الناس.
        """
        return max(MIN_DUCK_RATIO, self.duck_max_ratio() / 3.0)

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
        self._last_duck_time = None
        self._last_duck_ratio = 1.0
        self._last_activity_time = None
        self._last_command = None
        self.content.reset()
