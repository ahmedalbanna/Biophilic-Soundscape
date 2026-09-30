"""
content_engine.py - آلة حالات مسار المحتوى

تقرر ولا تلمس صوتاً. محرّك الخلفية يستدعيها في فرع واحد بعد كل فرع
يُخرج خلفية، فلا يصبح المحتوى منافساً للأولويات: الصلاة والنقاش
الحامي يتجاوزانها لأنهما يخرجان قبل البوابة.

الموضع يملكه هذا المحرك لا المشغّل: يُحسب من فرق طوابع الإطارات
المحقونة، فيبقى كل مؤقّت قابلاً للاختبار بلا انتظار، ويعمل المسار
الوهمي والحقيقي بالتطابق. وكل طابع زمني يدخل من update: بما فيه
وقت إغلاق السجل، وإلا صار الفحص معتمداً على ساعة الحائط.

قاعدة الضجيج: فوق content_gate_max_db نؤجّل ولا نقطع. مجلس فيه
حديث لا يُقطع، لكنه لا يُفرض على من يحديث أصلاً.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from .audio_types import AudioFrame, ContentAction, DayPeriod
from .config import EngineConfig
from .content_store import (
    OUTCOME_ABANDONED,
    OUTCOME_COMPLETED,
    OUTCOME_SKIPPED,
    ContentStore,
)


class ContentState:
    """حالات السرد. الأسماء تظهر في الواجهة وفي السجل."""

    IDLE = "idle"
    WAITING_QUIET = "waiting_quiet"  # الغرفة لم تهدأ بعد
    POSTPONED = "postponed"  # أُهجرت النافذة لهذا اليوم
    PLAYING = "playing"
    PAUSED_INTERRUPT = "paused_interrupt"  # مقاطعة كلام


@dataclass
class ContentDecision:
    """ما يقوله هذا المحرك هذه النبضة، وما يجب على المشغّل فعله."""

    action: ContentAction = ContentAction.NONE
    file: Optional[str] = None
    volume: float = 0.0
    position_sec: float = 0.0
    is_audible: bool = False  # الخلفية تخفت تحت المحتوى
    reason: str = ""
    state: str = ContentState.IDLE


class ContentEngine:
    """آلة حالات المحتوى. مدخلها إطار صوتي، ومخرجها أمر للواجهة."""

    def __init__(self, config: EngineConfig, store: Optional[ContentStore] = None):
        self.config = config
        self.store = store
        self.state = ContentState.IDLE

        # --- المؤقّتات ---
        # آخر لحظة كانت الغرفة هادئة: منها يُقاس كل انتظار
        self._room_quiet_since: Optional[float] = None
        # آخر طابع استُخدم: منه يُشتق تقدّم الموضع
        self._last_ts: Optional[float] = None
        self._postpones: int = 0  # تأجيلات النافذة الجارية
        self._postponed_windows: set = set()  # نوافذ أُهجرت اليوم

        # --- المقطع الجاري ---
        self._clip = None  # صف من audio_content
        self._log_id: Optional[int] = None
        self._position_sec: float = 0.0
        self._window_key: Optional[str] = None

    # ---------- واجهة القراءة ----------
    @property
    def current_file(self) -> Optional[str]:
        return self._clip["file_path"] if self._clip is not None else None

    @property
    def current_title(self) -> str:
        return self._clip["title"] if self._clip is not None else ""

    @property
    def position_sec(self) -> float:
        return self._position_sec

    @property
    def duration_sec(self) -> float:
        return float(self._clip["duration_sec"]) if self._clip is not None else 0.0

    @property
    def is_audible(self) -> bool:
        """الخلفية تخفت أثناء السرد والمقاطعة معاً، نصاً للمواصفة."""
        return self.state in (ContentState.PLAYING, ContentState.PAUSED_INTERRUPT)

    @property
    def is_running(self) -> bool:
        return self._clip is not None

    # ---------- قوى عليا من محرك الخلفية ----------
    def force_stop(self, reason: str = "") -> ContentDecision:
        """
        الصلاة: ينهي السرد ويصفّر كل شيء.

        يمرّ عبر reset() فلا يبقى مؤقّت معلّق، لأن الصلاة قد تأتي بعد
        انتقال ولا بد أن تبدأ النافذة التالية من صفر.
        """
        was_running = self.is_running
        title = self.current_title
        self._close_log(OUTCOME_ABANDONED, now=datetime.now())
        self.reset()
        if was_running:
            return ContentDecision(
                action=ContentAction.STOP,
                reason=reason or f"توقّف للصلاة: {title}",
                state=ContentState.IDLE,
            )
        return ContentDecision(reason=reason, state=ContentState.IDLE)

    def force_pause(self, reason: str = "") -> ContentDecision:
        """
        النقاش الحامي: يوقف مؤقتاً ولا يمحو التقدّم.

        تراجع هنا أيضاً: الكلام فوق 65dB مقاطعة لا بداية نبرة.
        """
        if self.is_running:
            self._rewind()
            self.state = ContentState.PAUSED_INTERRUPT
            self._room_quiet_since = None
            return ContentDecision(
                action=ContentAction.PAUSE,
                file=self.current_file,
                volume=self.config.content_volume,
                position_sec=self._position_sec,
                is_audible=True,
                reason=reason or "توقّف مؤقت للنقاش الحامي",
                state=self.state,
            )
        return ContentDecision(reason=reason, state=self.state)

    def abandon(self, reason: str = "تخطٍّ يدوي") -> None:
        """
        يختم المقطع كتخطٍّ لا كقطع.

        الفرق مقصود: التخطّي اختيار من المستخدم، فالتالي هو المقصود.
        أما القطع بالصلاة فلا يعني أنه سمع شيئاً، ولا يجوز أن يُحسب
        مرة واحدة في اليوم. وabandon لا يحسم التقدّم، وskipped
        يحسمه.
        """
        self._close_log(OUTCOME_SKIPPED, now=datetime.now())
        self.reset()

    def request_now(self, clip, now: datetime) -> bool:
        """
        تشغيل مقطع بطلب صريح، متجاوزاً البوابة.

        البوابة تحرس الانتباه: غرفة فيها ضجيج فوق 50dB ولا معنى
        لسردٍ فوق مجلس. أما من ضغط الزر فالمجلس يريد السمع الآن،
        والضغط طلب لا حارس.

        يسجّل المقطع كـ completed عند انتهائه كأي غيره، فلا يبقى بلا
        أثر. يعيد True إن بدأ.
        """
        if clip is None or self.store is None:
            return False
        self._close_log(OUTCOME_ABANDONED, now=now)
        self.reset()
        self._start(clip, clip["window_key"], now)
        self.state = ContentState.PLAYING
        self._room_quiet_since = None  # البوابة لن تعيد التحقق لهذا المقطع
        return True

    # ---------- المسار الرئيسي ----------
    def update(
        self,
        frame: AudioFrame,
        ts: float,
        now: datetime,
        period: DayPeriod,
        enabled: bool = True,
    ) -> ContentDecision:
        """نبضة واحدة. تُستدعى من فرع البوابة في محرك الخلفية."""
        if not enabled:
            return self.force_stop("المحتوى معطّل")

        # حصيلة الموضع قبل أي انتقال: نبضة واحدة تكفي لحساب الزمن.
        # أثناء PLAYING فقط. لو تقدّم والمقطع متوقف لبلغ نهاية مدته
        # والسرد لم يُسمع منه حرف: المقاطعة كانت تُنهيه بصمت.
        if (
            self._clip is not None
            and self._last_ts is not None
            and self.state == ContentState.PLAYING
        ):
            self._position_sec = max(
                0.0, self._position_sec + max(0.0, ts - self._last_ts)
            )
        self._last_ts = ts

        # هدوء الغرفة: يُعاد حسابه من الصفر عند أي إطار عالٍ
        loud = frame.db_level > self.config.content_gate_max_db
        if loud:
            self._room_quiet_since = None
        elif self._room_quiet_since is None:
            self._room_quiet_since = ts
        quiet_for = (
            0.0 if self._room_quiet_since is None else ts - self._room_quiet_since
        )

        # نافذة الفترة الجارية: إن انتهت صلاحية الحالية يتوقف السرد،
        # وإلا امتدّ ذكر Magdal إلى فترة عبادة مكتومة.
        window_key = self._window_for(period)

        if self.is_running:
            if window_key is None or window_key != self._window_key:
                return self._stop_for_window_change(now, window_key)
            return self._update_playing(frame, ts, now, quiet_for)

        return self._update_idle(frame, ts, now, period, window_key, quiet_for, loud)

    # ---------- أثناء السرد ----------
    def _stop_for_window_change(
        self, now: datetime, window_key: Optional[str]
    ) -> ContentDecision:
        title = self.current_title
        self._close_log(OUTCOME_ABANDONED, now=now)
        self._clip = None
        self._log_id = None
        self._position_sec = 0.0
        self._window_key = None
        self._postpones = 0
        self.state = ContentState.IDLE
        return ContentDecision(
            action=ContentAction.STOP,
            file=window_key,
            reason=f"انتهت نافذة المحتوى: {title}",
            state=self.state,
        )

    def _update_playing(
        self, frame: AudioFrame, ts: float, now: datetime, quiet_for: float
    ) -> ContentDecision:
        # --- الانتهاء: يُفحص قبل المقاطعة ---
        # لو انتهى المقطع في نفس إطار الكلام، الأولوية للإنهاء: المقطع
        # Formats ق completed وسجله صحيح، لا "مقاطعة" لشيء انتهى.
        if self._position_sec >= self.duration_sec:
            title = self.current_title
            finished_at = self._position_sec
            self._close_log(OUTCOME_COMPLETED, now=now, position=finished_at)
            self._clip = None
            self._log_id = None
            self._position_sec = 0.0
            self._window_key = None
            self.state = ContentState.IDLE
            return ContentDecision(
                action=ContentAction.FINISHED,
                file=title,
                position_sec=finished_at,
                reason=f"انتهى: {title}",
                state=self.state,
            )

        # --- المقاطعة ---
        if frame.is_speech and self.state == ContentState.PLAYING:
            # التراجع مرّة واحدة عند الانتقال. كان يُعاد كل إطار أثناء
            # الكلام، فسحب 3 ثوانٍ كل 200ms حتى بدا السرد قد بدأ من
            # الصفر كلما دام الكلام.
            self._rewind()
            self.state = ContentState.PAUSED_INTERRUPT
            # نبدأ عدّ الهدوء من لحظة التوقّف لا من آخر هدوء عام في
            # الغرفة: متحدث عند 45dB لا يتجاوز بوابة 50dB، فهدوء
            # الغرفة لم ينقطع، وبلاء هذا البدء كان السرد يستأنف في
            # النبضة التالية والشخص ما زال يتكلم.
            self._room_quiet_since = ts
            return ContentDecision(
                action=ContentAction.PAUSE,
                file=self.current_file,
                volume=self.config.content_volume,
                position_sec=self._position_sec,
                # الخلفية تبقى خافتة أثناء التوقّف أيضاً، نصاً للمواصفة:
                # السرد معلّق لا ملغى، فارتفاع الخلفية الآن يوحي
                # بأنه غادر المطهر.
                is_audible=True,
                reason=(f"مقاطعة كلام -> تراجع {self.config.content_rewind_sec:.0f}ث"),
                state=self.state,
            )

        # --- الاستئناف بعد الهدوء ---
        if self.state == ContentState.PAUSED_INTERRUPT:
            if quiet_for >= self.config.content_resume_quiet_sec:
                self.state = ContentState.PLAYING
                return ContentDecision(
                    action=ContentAction.RESUME,
                    file=self.current_file,
                    volume=self.config.content_volume,
                    position_sec=self._position_sec,
                    is_audible=True,
                    reason=(
                        f"استئناف بعد {self.config.content_resume_quiet_sec:.0f}ث هدوء"
                    ),
                    state=self.state,
                )
            return ContentDecision(
                action=ContentAction.NONE,
                file=self.current_file,
                position_sec=self._position_sec,
                is_audible=True,
                reason="معلّق بانتظار الهدوء",
                state=self.state,
            )

        # --- تشغيل مستمر: لا إجراء، والخلفية تخفت ---
        return ContentDecision(
            action=ContentAction.NONE,
            file=self.current_file,
            volume=self.config.content_volume,
            position_sec=self._position_sec,
            is_audible=True,
            reason=f"سرد: {self.current_title}",
            state=self.state,
        )

    # ---------- قبل السرد ----------
    def _update_idle(
        self,
        frame: AudioFrame,
        ts: float,
        now: datetime,
        period: DayPeriod,
        window_key: Optional[str],
        quiet_for: float,
        loud: bool,
    ) -> ContentDecision:
        if window_key is None:
            self.state = ContentState.IDLE
            return ContentDecision(
                reason="لا نافذة محتوى لهذه الفترة", state=self.state
            )

        spec = self.config.content_windows[window_key]
        label = spec["label"]

        # نافذة جديدة: عدّاد التأجيل يخصّ الجارية لا السابقة
        if window_key != self._window_key:
            self._window_key = window_key
            self._postpones = 0

        if window_key in self._postponed_windows:
            self.state = ContentState.POSTPONED
            return ContentDecision(reason=f"{label}: مؤجَّلة حتى الغد", state=self.state)

        # --- بوابة الانتباه ---
        if loud:
            self._postpones += 1
            if self._postpones >= self.config.content_gate_max_postpones:
                self._postponed_windows.add(window_key)
                self.state = ContentState.POSTPONED
                return ContentDecision(
                    reason=(
                        f"{label}: ضجيج مستمر "
                        f"({self._postpones}/{self.config.content_gate_max_postpones})"
                        " -> تأجيل لليوم"
                    ),
                    state=self.state,
                )
            self.state = ContentState.WAITING_QUIET
            self._room_quiet_since = None
            return ContentDecision(
                reason=(
                    f"{label}: مؤجَّل "
                    f"({self._postpones}/"
                    f"{self.config.content_gate_max_postpones})"
                    f" - أرضية {frame.db_level:.0f}dB"
                ),
                state=self.state,
            )

        # --- فجوة بين المقاطع ---
        if not self._gap_elapsed(window_key, now):
            self.state = ContentState.IDLE
            return ContentDecision(
                reason=f"{label}: فجوة قصيرة بين المقاطع", state=self.state
            )

        # --- مرة في اليوم ---
        if spec.get("once_per_day") and self.store is not None:
            played = self.store.plays_today(
                window_key,
                now.date().isoformat(),
                exclude_outcome=OUTCOME_ABANDONED,
            )
            if played > 0:
                self.state = ContentState.IDLE
                return ContentDecision(reason=f"{label}: بُثّ اليوم", state=self.state)

        # --- صمت الغرفة المطلوب قبل البدء ---
        needed = float(spec.get("min_room_silence_sec", 5.0))
        if quiet_for < needed:
            self.state = ContentState.WAITING_QUIET
            return ContentDecision(
                reason=f"{label}: بانتظار هدوء ({quiet_for:.0f}/{needed:.0f}ث)",
                state=self.state,
            )

        # --- الاختيار والبدء ---
        if self.store is None:
            return ContentDecision(reason="لا مكتبة محتوى", state=self.state)
        clip = self.store.next_for_window(window_key)
        if clip is None:
            self.state = ContentState.IDLE
            return ContentDecision(reason=f"{label}: لا مقطع متاح", state=self.state)

        self._start(clip, window_key, now)
        self.state = ContentState.PLAYING
        return ContentDecision(
            action=ContentAction.START,
            file=self.current_file,
            volume=self.config.content_volume,
            position_sec=0.0,
            is_audible=True,
            reason=f"بدء {label}: {self.current_title}",
            state=self.state,
        )

    # ---------- أدوات ----------
    def _window_for(self, period: DayPeriod) -> Optional[str]:
        """أول نافذة تسمح بهذه الفترة، بترتيب الإعدادات."""
        for key, spec in self.config.content_windows.items():
            if period in spec["periods"]:
                return key
        return None

    def _gap_elapsed(self, window_key: str, now: datetime) -> bool:
        """هل مضت فجوة content_min_gap_min منذ آخر بث في هذه النافذة؟"""
        if self.store is None:
            return True
        last = self.store.last_started(window_key, exclude_outcome=OUTCOME_ABANDONED)
        if not last:
            return True
        try:
            prev = datetime.fromisoformat(last)
        except ValueError:
            return True
        minutes = (now - prev).total_seconds() / 60.0
        return minutes >= self.config.content_min_gap_min

    def _start(self, clip, window_key: str, now: datetime) -> None:
        self._clip = clip
        self._window_key = window_key
        self._position_sec = 0.0
        self._postpones = 0
        # نبدأ العدّ من هذه اللحظة: البدء يحتاج نافذة هدوء من الآن
        self._room_quiet_since = now.timestamp()
        self._log_id = self.store.log_start(
            clip["id"], window_key, now.isoformat(timespec="seconds")
        )

    def _rewind(self) -> None:
        """تراجع بمقدار الإعداد، ولا ينزل تحت الصفر."""
        self._position_sec = max(
            0.0, self._position_sec - self.config.content_rewind_sec
        )

    def _close_log(
        self, outcome: str, now: Optional[datetime] = None, position: float = None
    ) -> None:
        """
        يغلق صف السجل. `now` يُحقن لا يُقرأ من الحائط.

        قراءة datetime.now() هنا كانت ستجعل النتيجة تعتمد على ساعة
        الجهاز، والاختبار لا يستطيع حقنها. الموضع الافتراضي هو الحالي
        إلا إذا مرّرنا وقت الانتهاء الحقيقي.
        """
        if self._clip is None or self._log_id is None or self.store is None:
            return
        stamp = (now or datetime.now()).isoformat(timespec="seconds")
        self.store.log_finish(
            self._log_id,
            outcome,
            self._position_sec if position is None else position,
            stamp,
        )

    def reset(self) -> None:
        """
        يمسح كل حالة المحتوى.

        يُستدعى مع engine.reset(). أي حقل يُنسى يتسرّب بين السيناريوهات:
        عدّاد تأجيلات متبقٍّ يجعل النافذة مهجورة بلا سبب ظاهر.
        """
        self.state = ContentState.IDLE
        self._clip = None
        self._log_id = None
        self._position_sec = 0.0
        self._window_key = None
        self._postpones = 0
        self._postponed_windows = set()
        self._room_quiet_since = None
        self._last_ts = None
