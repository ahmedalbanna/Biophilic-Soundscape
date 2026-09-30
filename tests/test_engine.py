"""اختبارات المحرك - تغطي الجدول اليومي والحالات الأربع + الأولويات"""

import sys
from datetime import datetime, timedelta
from datetime import time as dtime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.context_aware_audio import ContextAwareAudioEngine, EngineConfig
from src.context_aware_audio.audio_types import AudioFrame, EngineState, DayPeriod


def make_engine():
    cfg = EngineConfig()
    e = ContextAwareAudioEngine(cfg)
    e.prayer.set_times(
        {
            "fajr": dtime(5, 10),
            "dhuhr": dtime(12, 5),
            "asr": dtime(15, 25),
            "maghrib": dtime(18, 10),
            "isha": dtime(19, 30),
        }
    )
    return e


def frame(ts, db, speech=False, overlapping=False, greeting=False):
    return AudioFrame(
        timestamp=ts,
        db_level=db,
        is_speech=speech,
        is_overlapping=overlapping,
        is_greeting_tone=greeting,
    )


def day_at(h, m=0):
    return datetime.now().replace(hour=h, minute=m, second=0, microsecond=0)


PASSED = FAILED = 0


def check(name, cond, extra=""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"✅ {name}")
    else:
        FAILED += 1
        print(f"❌ {name} {extra}")


# 1) الجدول اليومي
e = make_engine()
period_cases = [
    (6, DayPeriod.FAJR_SABAH, "mountain_breeze_birds.wav"),
    (10, DayPeriod.DUHA_WORK, "water_stream.wav"),
    (13, DayPeriod.LUNCH, "light_rain_leaves.wav"),
    (15, DayPeriod.MAQIL, "warm_breeze.wav"),
    (21, DayPeriod.SAMRA, "sea_waves_fireplace.wav"),
    (2, DayPeriod.NIGHT_SLEEP, "crickets_light.wav"),
]
for h, expected_period, expected_file in period_cases:
    e.reset()
    p = e.calendar.period_for_datetime(day_at(h))
    check(f"فترة {h}:00 = {expected_period.value}", p == expected_period, f"got {p}")
    cmd = e.process_frame(frame(day_at(h).timestamp(), 10, False), day_at(h))
    check(f"ملف {h}:00 = {expected_file}", cmd.file == expected_file, f"got {cmd.file}")

# 2) Ducking
e.reset()
cmd = e.process_frame(frame(day_at(10).timestamp(), 50, True), day_at(10))
check(
    "خفض أثناء الكلام",
    cmd.state == EngineState.DUCKED and cmd.volume_ratio < 0.3,
    str(cmd),
)

# 3) نقاش حامي
e.reset()
t0 = day_at(15).timestamp()
cmd = e.process_frame(frame(t0, 70, True, overlapping=True), day_at(15))
check("كتم نقاش حامي", cmd.state == EngineState.DEBATE_MUTED and cmd.is_muted, str(cmd))
cmd2 = e.process_frame(frame(t0 + 30, 30, False), day_at(15) + timedelta(seconds=30))
check("استمرار الكتم قبل 60ث", cmd2.is_muted, str(cmd2))
cmd3 = e.process_frame(frame(t0 + 95, 30, False), day_at(15) + timedelta(seconds=95))
check(
    "العودة بعد 60ث هدوء",
    not cmd3.is_muted or cmd3.state != EngineState.DEBATE_MUTED,
    str(cmd3),
)

# 4) ترحيب (16:30 لتجنب قفل العصر 15:22-16:00)
e.reset()
t0 = day_at(16, 30).timestamp()
cmd = e.process_frame(frame(t0, 62, True, greeting=True), day_at(16, 30))
check(
    "ترحيب -> شلال + 15%",
    cmd.state == EngineState.WELCOME and cmd.volume_ratio == 0.15,
    str(cmd),
)

# 5) هدوء مفاجئ (16:40)
e.reset()
base = day_at(16, 40)
e.process_frame(frame(base.timestamp(), 45, True), base)
cmd = e.process_frame(
    frame((base + timedelta(seconds=11)).timestamp(), 20, False),
    base + timedelta(seconds=11),
)
check("هدوء 11ث -> Fade-In", cmd.state == EngineState.CONTEMPLATION_FADE, str(cmd))
check("زمن Fade-In ناعم 3ث", cmd.fade_duration_sec == 3.0, str(cmd))

# 6) الصلاة
e.reset()
cmd = e.process_frame(frame(day_at(18, 8).timestamp(), 20, False), day_at(18, 8))
check("كتم قبل الأذان بـ3د", cmd.state == EngineState.PRAYER_MUTED, str(cmd))
cmd = e.process_frame(frame(day_at(18, 20).timestamp(), 20, False), day_at(18, 20))
check("حظر أثناء/بعد الصلاة", cmd.state == EngineState.PRAYER_MUTED, str(cmd))
cmd = e.process_frame(frame(day_at(19, 0).timestamp(), 20, False), day_at(19, 0))
check("انتهاء الحظر بعد 15د", cmd.state != EngineState.PRAYER_MUTED, str(cmd))

# 7) أولوية الصلاة على الترحيب
e.reset()
cmd = e.process_frame(
    frame(day_at(18, 9).timestamp(), 65, True, greeting=True), day_at(18, 9)
)
check("الصلاة تتغلب على الترحيب", cmd.state == EngineState.PRAYER_MUTED, str(cmd))

# 8) وضع النوم: ليل + 5 دقائق بلا نشاط (الحالة الوحيدة بلا تغطية سابقاً)
e = make_engine()
t0 = day_at(2, 30).timestamp()
cmd = e.process_frame(frame(t0, 2, False), day_at(2, 30))
check("ليل + نشاط خفيف -> ليس نوماً", cmd.state != EngineState.SLEEP_SILENCE, str(cmd))
cmd = e.process_frame(frame(t0 + 301, 2, False), day_at(2, 31))
check(
    "ليل + 5د سكون -> صرار 20dB",
    cmd.state == EngineState.SLEEP_SILENCE and cmd.target_db == 20,
    str(cmd),
)
cmd = e.process_frame(frame(t0 + 320, 45, True), day_at(2, 35))
check("كلام يستيقظ الصوت", cmd.state != EngineState.SLEEP_SILENCE, str(cmd))

# نوم تام: بلا ملف صوتي في فترة الليل
e2 = make_engine()
e2.config.period_sounds["night_sleep"] = {"file": None, "db": 0, "label": "إيقاف"}
e2.reset()
# إطار تهيئة: بدونه لا يبدأ قياس السكون (لا نشاط سابق = لا مرجع زمني)
e2.process_frame(frame(t0, 1, False), day_at(2, 30))
cmd = e2.process_frame(frame(t0 + 400, 1, False), day_at(2, 36))
check(
    "ليل + إيقاف تام", cmd.state == EngineState.SLEEP_SILENCE and cmd.is_muted, str(cmd)
)

# الطابع 0.0 قيمة شرعية: لا يجوز أن يعلق الكتم على الأبد
e3 = make_engine()
cmd = e3.process_frame(frame(0.0, 70, True, overlapping=True), day_at(15))
check("نقاش عند الطابع 0 يبدأ الكتم", cmd.state == EngineState.DEBATE_MUTED, str(cmd))
cmd = e3.process_frame(frame(100.0, 30, False), day_at(15))
check("الطابع 0 لا يعلّق الكتم", cmd.state != EngineState.DEBATE_MUTED, str(cmd))

# 9) VAD من PCM
from src.context_aware_audio.vad import VadProcessor

vad = VadProcessor(EngineConfig())
f = vad.analyze_frame(50, timestamp=1000.0)
# عتبة و استمرارية: إطار واحد يتجاوز العتبة لكنه لا يؤكد بعد
check("VAD: 50dB يتجاوز العتبة خاماً", f.is_speech_raw, str(f))
check("VAD: إطار واحد ليس كلاماً بعد", f.is_speech is False, str(f))
f = vad.analyze_frame(50, timestamp=1000.2)
check("VAD يصنف 50dB كلاماً بعد الثبات", f.is_speech, str(f))
f2 = vad.analyze_frame(75, timestamp=1000.4)
check("VAD يصنف 75dB صخباً", f2.is_overlapping, str(f2))
import struct

silence = struct.pack("<1600h", *([0] * 1600))
f3 = vad.analyze_pcm(silence)
# إطار صامت واحد لا يُنهي الكلام: الإنهاء يحتاج 0.4ث هبوطاً
check("PCM صامت -> لا يتجاوز العتبة", not f3.is_speech_raw, str(f3))
_vad_sil = VadProcessor(EngineConfig())
check(
    "PCM صامت على معالج نظيف -> ليس كلاماً",
    not _vad_sil.analyze_pcm(silence).is_speech,
)


# 10) نبرة الترحيب: قفزة بداية + استمرار (كانت غير قابلة للوصول)
def greeting_hits(sequence, step=0.1, start=100.0):
    proc = VadProcessor(EngineConfig())
    ts = start
    hits = 0
    for db in sequence:
        if proc.analyze_frame(db, timestamp=ts).is_greeting_tone:
            hits += 1
        ts += step
    return hits


check("ترحيب: قفزة ثم استمرار -> يُكتشف", greeting_hits([30.0] * 3 + [75.0] * 10) == 1)
check("ترحيب: كلام مستمر بلا قفزة -> لا يُكتشف", greeting_hits([60.0] * 30) == 0)
check("ترحيب: هدوء -> لا يُكتشف", greeting_hits([5.0] * 30) == 0)

# 11) مستويات الترحيب من special_sounds لا من فترة اليوم
e4 = make_engine()
e4.reset()
cmd = e4.process_frame(
    frame(day_at(6, 30).timestamp(), 62, True, greeting=True), day_at(6, 30)
)
check("مستوى الترحيب ثابت لا يتبع الفترة", cmd.target_db == 30, str(cmd))
cmd = e4.process_frame(frame(day_at(16, 40).timestamp(), 45, True), day_at(16, 40))
check("مستوى ريح المقيل من special_sounds", cmd.target_db == 32, str(cmd))

# 12) فلتر ثبات الكلام: القفزة الواحدة لا تقرّر
from src.context_aware_audio.vad import VadProcessor, elapsed

STEP = 0.2  # 200ms = نبضة الواجهة


def make_pair():
    """محرك ومعالج VAD يشاركان الإعدادات نفسها."""
    cfg = EngineConfig()
    e = ContextAwareAudioEngine(cfg)
    e.prayer.set_times(
        {
            "fajr": dtime(5, 10),
            "dhuhr": dtime(12, 5),
            "asr": dtime(15, 25),
            "maghrib": dtime(18, 10),
            "isha": dtime(19, 30),
        }
    )
    return e, VadProcessor(cfg)


# قفزة واحدة كانت تُسكِت الغرفة 60 ثانية
for _n, _expect in ((1, False), (2, True), (3, True)):
    _e, _v = make_pair()
    _now = day_at(15, 0)
    _t = 3000.0  # أساس مستدير عمداً: يكشف حساسية الأعداد العشرية
    _last = None
    for _ in range(_n):
        _last = _e.process_frame(_v.analyze_frame(75.0, timestamp=_t), _now)
        _t += STEP
    for _ in range(5):  # ثانية هدوء
        _last = _e.process_frame(_v.analyze_frame(30.0, timestamp=_t), _now)
        _t += STEP
    check(
        f"قفزة {_n} إطار: الكتم {'نعم' if _expect else 'لا'}",
        (_last.state == EngineState.DEBATE_MUTED) == _expect,
        _last.state.value,
    )

# السعال فوق العتبة لا يعيد ضبط عدّاد السكون
_e, _v = make_pair()
_now = day_at(15, 0)
_t = 3000.0
_e.process_frame(_v.analyze_frame(45.0, timestamp=_t), _now)
_t += STEP
_e.process_frame(_v.analyze_frame(20.0, timestamp=_t), _now)
_t += STEP
# سعال حقيقي = إطار واحد عند 200ms. خمس إطارات تعني ثانية كاملة فوق
# العتبة، وهذه كلام مشروع لا سعال، والتأكيد فيها هو السلوك الصحيح.
_e.process_frame(_v.analyze_frame(55.0, timestamp=_t), _now)
_t += STEP
_t += 11
_last = _e.process_frame(_v.analyze_frame(20.0, timestamp=_t), _now)
check(
    "سعال بإطار واحد لا يوقف Fade-In",
    _last.state == EngineState.CONTEMPLATION_FADE,
    _last.state.value,
)
check(
    "سعال بإطار واحد: خام بلا تأكيد",
    _v._speech_confirmed is False,
    f"confirmed={_v._speech_confirmed}",
)

# النبضات المتقطعة لا تُشغّل الخفض، والصوت المستمر يُشغّله
_e, _v = make_pair()
_t = 3000.0
for _i in range(30):
    _last = _e.process_frame(
        _v.analyze_frame(50.0 if _i % 4 == 0 else 20.0, timestamp=_t), _now
    )
    _t += STEP
check(
    "الخفض لا يهتز مع نبضات متقطعة",
    _last.state == EngineState.DAILY_AMBIENT,
    _last.state.value,
)

_e, _v = make_pair()
_t = 3000.0
for _ in range(6):
    _last = _e.process_frame(_v.analyze_frame(50.0, timestamp=_t), _now)
    _t += STEP
check("صوت مستمر يُخفض الصوت", _last.state == EngineState.DUCKED, _last.state.value)

# توقيت الفلتر
_v = VadProcessor(EngineConfig())
_t = 3000.0
_f = _v.analyze_frame(50.0, timestamp=_t)
check("الإطار الأول خام فقط", _f.is_speech_raw and not _f.is_speech, str(_f))
_f = _v.analyze_frame(50.0, timestamp=_t + STEP)
check("الإطار الثاني مؤكد", _f.is_speech, str(_f))

_leave = _v.speech_threshold() - EngineConfig().speech_hysteresis_db
_v2 = VadProcessor(EngineConfig())
_t2 = 4000.0
_v2.analyze_frame(50.0, timestamp=_t2)
_v2.analyze_frame(50.0, timestamp=_t2 + STEP)
_f = _v2.analyze_frame(_leave + 0.5, timestamp=_t2 + 2 * STEP)
check("داخل نطاق التخلّف يبقى كلاماً", _f.is_speech, str(_f))
_f = _v2.analyze_frame(10.0, timestamp=_t2 + 3 * STEP)
check("الهبوط الأول لا يُنهي", _f.is_speech, str(_f))
_f = _v2.analyze_frame(10.0, timestamp=_t2 + 4 * STEP)
check("الهبوط الثاني لا يُنهي (0.2ث < 0.4ث)", _f.is_speech, str(_f))
_f = _v2.analyze_frame(10.0, timestamp=_t2 + 5 * STEP)
check("الهبوط الثالث يُنهي (0.4ث)", not _f.is_speech, str(_f))

# التهدئة تحجب التأكيد المبكر وتسمح بعد 3 ثوان
_v3 = VadProcessor(EngineConfig())
_t3 = 3000.0
_v3.analyze_frame(50.0, timestamp=_t3)
_v3.analyze_frame(50.0, timestamp=_t3 + STEP)
_confirm_at = _t3 + STEP
_ts = _confirm_at
for _ in range(17):  # 3.4ث، أي تجاوز تهدئة 3.0ث
    _v3.analyze_frame(10.0, timestamp=_ts)
    _ts += STEP
_f = _v3.analyze_frame(50.0, timestamp=_ts)
check(
    "التهدئة تحجب التأكيد المبكر",
    _f.is_speech_raw and not _f.is_speech,
    f"elapsed={_ts - _confirm_at:.1f}s",
)
_ts += STEP
_f = _v3.analyze_frame(50.0, timestamp=_ts)
check("بعد 3ث يعود التأكيد", _f.is_speech, f"elapsed={_ts - _confirm_at:.1f}s")

# حدود النافذة مع الأعداد العشرية
check("elapsed عند حدّ مستدير", elapsed(3000.2, 3000.0, 0.2), "3000.2-3000.0")
check("elapsed عند 100.8", elapsed(100.8, 100.5, 0.3), "100.8-100.5")
check("elapsed لبداية غائبة", not elapsed(1.0, None, 0.0))
_v4 = VadProcessor(EngineConfig())
_v4.analyze_frame(50.0, timestamp=3000.0)
_f = _v4.analyze_frame(50.0, timestamp=3000.2)
check("التأكيد لا يعتمد على مقدار الطابع", _f.is_speech, str(_f))

# التصفير لا يترك حالة
_v5 = VadProcessor(EngineConfig())
_v5.analyze_frame(50.0, timestamp=3000.0)
_v5.analyze_frame(50.0, timestamp=3000.2)
check("قبل التصفير: مؤكد", _v5._speech_confirmed)
_v5.reset()
check("التصفير يلغي التأكيد", _v5._speech_confirmed is False)
check("التصفير يمسح البداية", _v5._above_since is None)
check("التصفير يمسح الهبوط", _v5._below_since is None)
check("التصفير يمسح التاريخ", len(_v5._history) == 0)
_f = _v5.analyze_frame(50.0, timestamp=6000.0)
check("بعد التصفير بداية نظيفة", _f.is_speech_raw and not _f.is_speech, str(_f))

# نافذة بداية الترحيب تُقرأ من 1.5ث لا من إطار واحد
_v6 = VadProcessor(EngineConfig())
_t6 = 6000.0
for _ in range(10):
    _v6.analyze_frame(30.0, timestamp=_t6)
    _t6 += STEP
_hits = 0
for _ in range(6):
    _f = _v6.analyze_frame(75.0, timestamp=_t6)
    _hits += int(_f.is_greeting_tone)
    _t6 += STEP
check("قفزة حادة تكشف الترحيب", _hits == 1, f"hits={_hits}")

_v7 = VadProcessor(EngineConfig())
_t7 = 6000.0
for _ in range(10):
    _v7.analyze_frame(30.0, timestamp=_t7)
    _t7 += STEP
for _ in range(20):  # صوت مرتفع مستمر: ليس قفزة
    _f = _v7.analyze_frame(75.0, timestamp=_t7)
    _t7 += STEP
check("صوت مرتفع مستمر ليس ترحيباً", not _f.is_greeting_tone, str(_f))



# 13) مراجعة: قفزة النبرة، veto تحليل PCM، نافذة التاريخ
import inspect

from src.context_aware_audio.vad import VadProcessor, elapsed

STEP = 0.2


cfg = EngineConfig()


def greeting_after(ramp_frames, step_db, hold=10, quiet=10):
    """هدوء، ثم صعود تدريجي، ثم ثبات. كم مرة أُطلقت نبرة الترحيب؟"""
    v = VadProcessor(cfg)
    t = 6000.0
    for _ in range(quiet):
        v.analyze_frame(30.0, timestamp=t)
        t += STEP
    db = 30.0
    fired = 0
    for _ in range(ramp_frames):
        db += step_db
        fired += int(v.analyze_frame(db, timestamp=t).is_greeting_tone)
        t += STEP
    for _ in range(hold):
        fired += int(v.analyze_frame(db, timestamp=t).is_greeting_tone)
        t += STEP
    return fired


# 1) صعود بطيء ليس بداية نبرة
check("ramp 5 dB/frame does not fire", greeting_after(9, 5.0) == 0)
check("ramp 2.5 dB/frame does not fire", greeting_after(18, 2.5) == 0)
check("ramp 1 dB/frame does not fire", greeting_after(36, 1.0) == 0)
# الصعود الحاد يبقى مقبولاً
check("abrupt 45 dB jump fires", greeting_after(1, 45.0) > 0)
check("sharp 15 dB/frame fires", greeting_after(3, 15.0) > 0)

# 2) نافذة البداية مرئية: القفزة الحادة محدودة بزمن
_slow = EngineConfig()
_slow.greeting_onset_window_sec = 0.05  # لا تتسع إلا لإطار واحد


def greeting_with(cfg_override, ramp_frames, step_db):
    v = VadProcessor(cfg_override)
    t = 6000.0
    for _ in range(10):
        v.analyze_frame(30.0, timestamp=t)
        t += STEP
    db = 30.0
    fired = 0
    for _ in range(ramp_frames):
        db += step_db
        fired += int(v.analyze_frame(db, timestamp=t).is_greeting_tone)
        t += STEP
    for _ in range(10):
        fired += int(v.analyze_frame(db, timestamp=t).is_greeting_tone)
        t += STEP
    return fired


check(
    "onset window is load-bearing: 0.05s rejects a 2-frame rise",
    greeting_with(_slow, 2, 22.5) == 0,
    greeting_with(_slow, 2, 22.5),
)
check(
    "onset window is load-bearing: 1.5s accepts a 2-frame rise",
    greeting_with(cfg, 2, 22.5) > 0,
    greeting_with(cfg, 2, 22.5),
)


# 3) تاريخ المستويات يُقصّ بالزمن لا بالعدد
_fast = VadProcessor(cfg)
_t = 6000.0
for i in range(200):  # 20ms: أسرع بكثير من نبضة الواجهة
    _fast.analyze_frame(30.0, timestamp=_t)
    _t += 0.02
check(
    "history trimmed by time at 20ms sampling",
    len(_fast._history) <= 1.5 / 0.02 + 2,
    len(_fast._history),
)
_grew = False
# عند 20ms يحتاج onset (0.2ث = 10 إطارات) + sustain (0.3ث = 15) = 25+
for _ in range(40):
    _t += 0.02
    _f = _fast.analyze_frame(75.0, timestamp=_t)
    _grew = _grew or _f.is_greeting_tone
check("sharp onset still detected at 20ms sampling", _grew)


# 4) analyze_pcm: veto على البدء فقط
class _FakeVad:
    def __init__(self, ratio):
        self.ratio = ratio
        self.n = 0

    def is_speech(self, chunk, rate):
        self.n += 1
        return self.n <= int(self.ratio * 1000)


def pcm(db=50.0, samples=1600):
    import math

    amp = int(32767 * (10 ** ((db - 100) / 20)))
    return struct.pack(
        f"<{samples}h", *[int(amp * math.sin(i / 20)) for i in range(samples)]
    )



check(
    "analyze_pcm accepts timestamp",
    "timestamp" in inspect.signature(VadProcessor.analyze_pcm).parameters,
)

# نداءات سريعة متتابعة: نافذة 0.2ث قابلة للإشباع
_v = VadProcessor(cfg)
_speech = 0
for i in range(6):
    _f = _v.analyze_pcm(pcm(50.0), timestamp=1000.0 + i * STEP)
    _speech += int(_f.is_speech)
check("rapid analyze_pcm can confirm speech", _speech >= 4, _speech)

# veto يمنع البدء
_v2 = VadProcessor(cfg)
_v2._webrtc_vad = _FakeVad(0.0)
_f = None
for i in range(5):
    _f = _v2.analyze_pcm(pcm(50.0), timestamp=2000.0 + i * STEP)
check("voiced_ratio=0 vetoes the onset", not _f.is_speech, str(_f))
check("veto also clears is_speech_raw", not _f.is_speech_raw, str(_f))

# veto لا يلمس قراراً مؤكّداً
_v3 = VadProcessor(cfg)
for i in range(4):
    _f = _v3.analyze_pcm(pcm(50.0), timestamp=3000.0 + i * STEP)
check("speech confirmed before the veto", _f.is_speech, str(_f))
_v3._webrtc_vad = _FakeVad(0.0)
_f = _v3.analyze_pcm(pcm(50.0), timestamp=3000.8)
check("one unvoiced frame does not drop confirmed speech", _f.is_speech, str(_f))


# 5) elapsed لا يتجمّد على طابع متأخر
check("elapsed: backwards stamp is clamped to zero", not elapsed(10.0, 20.0, 0.2))
check("elapsed: normal span still passes", elapsed(20.2, 20.0, 0.2))

# 14) نافذة الخفض: زفير قصير لا يرسل الخلفية إلى 100%
from src.context_aware_audio.vad import VadProcessor

STEP = 0.2


def make_talking(floor=50.0, frames=6):
    """محرك يتكلم الآن: المحرك والمعالج بطوابع محقونة."""
    cfg = EngineConfig()
    e = ContextAwareAudioEngine(cfg)
    e.prayer.set_times(
        {
            "fajr": dtime(5, 10),
            "dhuhr": dtime(12, 5),
            "asr": dtime(15, 25),
            "maghrib": dtime(18, 10),
            "isha": dtime(19, 30),
        }
    )
    v = VadProcessor(cfg)
    t = 3000.0
    for _ in range(frames):
        e.process_frame(v.analyze_frame(floor, timestamp=t), day_at(15, 0))
        t += STEP
    return e, v, t


# زفير 0.8ث: كان يقلب الحالة إلى daily_ambientratio=1.0
_e, _v, _t = make_talking()
check("duck: talking is ducked", _e._last_command.state == EngineState.DUCKED)
_breath = []
for _ in range(4):  # 0.8ث
    _c = _e.process_frame(_v.analyze_frame(20.0, timestamp=_t), day_at(15, 0))
    _breath.append(_c)
    _t += STEP
check(
    "duck: a 0.8s breath stays ducked",
    all(c.state == EngineState.DUCKED for c in _breath),
    [c.state.value for c in _breath],
)
check(
    "duck: the breath never returns full volume",
    all(c.volume_ratio < 1.0 for c in _breath),
    [round(c.volume_ratio, 2) for c in _breath],
)
check(
    "duck: the hold keeps the last ratio, not a jump",
    len({round(c.volume_ratio, 3) for c in _breath}) == 1,
    [round(c.volume_ratio, 3) for c in _breath],
)

# انتهاء النافذة: الموسيقى تعود
for _ in range(20):
    _c = _e.process_frame(_v.analyze_frame(20.0, timestamp=_t), day_at(15, 0))
    _t += STEP
check("duck: after the hold the music returns", _c.state == EngineState.DAILY_AMBIENT,
      _c.state.value)
check("duck: and at full volume", _c.volume_ratio == 1.0, _c.volume_ratio)

# النافذة أقصر من صمت التأمل فلا تطغى عليه
check("duck_hold_sec is below the silence window",
      EngineConfig().duck_hold_sec < EngineConfig().silence_for_fade_in_sec)
_e2, _v2, _t2 = make_talking()
for _ in range(60):  # 12ث
    _c2 = _e2.process_frame(_v2.analyze_frame(20.0, timestamp=_t2), day_at(15, 0))
    _t2 += STEP
check("duck: the 10s contemplation fade still wins",
      _c2.state == EngineState.CONTEMPLATION_FADE, _c2.state.value)

# الصلاة تعلو النافذة
_e3, _v3, _t3 = make_talking()
_c3 = _e3.process_frame(_v3.analyze_frame(20.0, timestamp=_t3), day_at(18, 10))
check("duck: prayer outranks the hold",
      _c3.state == EngineState.PRAYER_MUTED, _c3.state.value)

# reset يصفّر النافذة
_e4, _v4, _t4 = make_talking()
check("duck: hold is set before reset", _e4._last_duck_time is not None)
_e4.reset()
check("duck: reset clears _last_duck_time", _e4._last_duck_time is None)
check("duck: reset clears the stored ratio", _e4._last_duck_ratio == 1.0)
_v4.reset()
_c4 = _e4.process_frame(_v4.analyze_frame(20.0, timestamp=_t4), day_at(15, 0))
check("duck: after reset a silent frame is not a hold",
      _c4.state == EngineState.DAILY_AMBIENT, _c4.state.value)

# نافذة صفرية = السلوك القديم: إثبات أن الاختبار يلتقط العطل
_holdless = EngineConfig()
_holdless.duck_hold_sec = 0.0
_e5 = ContextAwareAudioEngine(_holdless)
_e5.prayer.set_times(
    {
        "fajr": dtime(5, 10),
        "dhuhr": dtime(12, 5),
        "asr": dtime(15, 25),
        "maghrib": dtime(18, 10),
        "isha": dtime(19, 30),
    }
)
_v5 = VadProcessor(_holdless)
_t5 = 3000.0
for _ in range(6):
    _e5.process_frame(_v5.analyze_frame(50.0, timestamp=_t5), day_at(15, 0))
    _t5 += STEP
_states = []
for _ in range(4):
    _states.append(
        _e5.process_frame(_v5.analyze_frame(20.0, timestamp=_t5), day_at(15, 0)).state
    )
    _t5 += STEP
check(
    "duck: with the hold disabled the breath does snap to ambient (regression proof)",
    EngineState.DAILY_AMBIENT in _states,
    [s.value for s in _states],
)



# 15) تتبّع أرضية الضجيج + منحنى مربوط بالعتبة + عمق الخفض


STEP = 0.2


def day_at(h, m=0):
    """وقت بعيد عن كل نافذة صلاة."""
    return datetime.now().replace(hour=h, minute=m, second=0, microsecond=0)



def feed(v, db, n, t):
    for _ in range(n):
        v.analyze_frame(db, timestamp=t)
        t += STEP
    return t


# ===== P2: تتبّع أرضية الضجيج =====
# في هذه الغرفة الأرضية 3.5dB والعتبة 40، فالهامش 12dB لا أثر له.
# defence الحقيقية أنه يصيرLever إذا تجاوزت الأرضية 28dB.
v = VadProcessor(EngineConfig())
t = feed(v, 3.5, 200, 6000.0)
check("floor: tracks a quiet room", abs(v._live_floor_db - 3.5) < 0.1, v._live_floor_db)
check(
    "floor: a 3.5dB room leaves the threshold at 40",
    v.speech_threshold() == 40.0,
    v.speech_threshold(),
)

v2 = VadProcessor(EngineConfig())
t2 = feed(v2, 30.0, 200, 6000.0)
check(
    "floor: tracks a loud room", abs(v2._live_floor_db - 30.0) < 0.1, v2._live_floor_db
)
check(
    "floor: a 30dB room raises the threshold to 42",
    v2.speech_threshold() == 42.0,
    v2.speech_threshold(),
)

# الكلام في غرفة صاخبة يبقى مكتشفاً، والعتبة لا تهرب
t3 = t2
for _ in range(4):
    f = v2.analyze_frame(45.0, timestamp=t3)
    t3 += STEP
check("floor: speech detected above a raised threshold", f.is_speech, str(f))
check(
    "floor: the threshold did not run away",
    v2.speech_threshold() == 42.0,
    v2.speech_threshold(),
)

# عشر دقائق من الكلام: لا حلقة ارتداد
v3 = VadProcessor(EngineConfig())
t3b = feed(v3, 3.5, 200, 6000.0)
_before = v3.speech_threshold()
for _ in range(3000):
    f3 = v3.analyze_frame(45.0, timestamp=t3b)
    t3b += STEP
check(
    "floor: no runaway after 10 minutes of speech",
    v3.speech_threshold() == _before,
    v3.speech_threshold(),
)
check("floor: speech still detected at the end", f3.is_speech)

# باب يُغلق مرة واحدة: الوسيط يتجاهله
v4 = VadProcessor(EngineConfig())
t4 = feed(v4, 3.5, 200, 6000.0)
_floor_before = v4._live_floor_db
t4 = feed(v4, 95.0, 1, t4)
t4 = feed(v4, 3.5, 100, t4)
check(
    "floor: one door slam does not move the median",
    v4._live_floor_db == _floor_before,
    f"{_floor_before} -> {v4._live_floor_db}",
)
check(
    "floor: the sample window is bounded",
    len(v4._floor_samples) <= v4._max_floor_samples(),
    f"{len(v4._floor_samples)} vs {v4._max_floor_samples()}",
)

# عينة واحدة قد تكون قفزة باب فلا تصلح أساساً
v5 = VadProcessor(EngineConfig())
v5.analyze_frame(90.0, timestamp=6000.0)
check("floor: one sample is not enough", v5._live_floor_db is None, v5._live_floor_db)
check("floor: threshold stays at base", v5.speech_threshold() == 40.0)

# البذر اليدوي يعمل ثم يتجاوزه التتبّع
v6 = VadProcessor(EngineConfig())
v6.set_noise_floor(35.0)
check(
    "floor: manual seed raises the threshold",
    v6.speech_threshold() == 47.0,
    v6.speech_threshold(),
)
t6 = feed(v6, 3.5, 200, 6000.0)
check(
    "floor: tracking takes over from the seed",
    v6.speech_threshold() == 40.0,
    v6.speech_threshold(),
)
check("floor: the manual seed is kept on record", v6._manual_floor_db == 35.0)

# reset يصفّر التتبّع ويبقي البذر
v7 = VadProcessor(EngineConfig())
v7.set_noise_floor(20.0)
feed(v7, 3.5, 200, 6000.0)
v7.reset()
check("floor: reset clears the live floor", v7._live_floor_db is None)
check("floor: reset clears the samples", len(v7._floor_samples) == 0)
check("floor: reset keeps the manual seed", v7._manual_floor_db == 20.0)


# ===== P3: منحنى الخفض مربوط بالعتبة السارية =====
def noisy_engine(floor_db=30.0):
    cfg = EngineConfig()
    e = ContextAwareAudioEngine(cfg)
    e.prayer.set_times(
        {
            "fajr": dtime(5, 10),
            "dhuhr": dtime(12, 5),
            "asr": dtime(15, 25),
            "maghrib": dtime(18, 10),
            "isha": dtime(19, 30),
        }
    )
    v = VadProcessor(cfg)
    t = feed(v, floor_db, 200, 6000.0)
    return e, v, cfg, t


_e, _v, _cfg, _t = noisy_engine()
_thr = _v.speech_threshold()
_max_r = _e.duck_max_ratio()
_min_r = _e.duck_min_ratio()
check("curve: the noisy-room threshold is 42", _thr == 42.0, _thr)
check(
    "curve: max reduction sits exactly at the threshold",
    abs(_e._duck_ratio(_thr, _thr) - _max_r) < 1e-9,
    f"{_e._duck_ratio(_thr, _thr)} مقابل {_max_r}",
)
check(
    "curve: the curve does not start below speech",
    abs(_e._duck_ratio(_thr - 2, _thr) - _max_r) < 1e-9,
    f"{_e._duck_ratio(_thr - 2, _thr)} مقابل {_max_r}",
)
check(
    "curve: 64.9dB is essentially the minimum",
    abs(_e._duck_ratio(64.9, _thr) - _min_r) < 0.002,
    f"{_e._duck_ratio(64.9, _thr)} مقابل {_min_r}",
)

# النسب تُشتقّ من duck_depth لا من حقل مستقل. الفحص يبني محرّكين
# بعمقين مختلفين ويطالب بأن تتغيّر النسبة بينهما — وهو ما لا يراه
# فحصٌ يقارن رقمين ثابتين يبقيان متطابقين عند الافتراضي وحده.
_m60, _v60, _c60, _t60 = noisy_engine()
_c60.duck_depth = 60.0
_m60 = ContextAwareAudioEngine(_c60)
_m80, _v80, _c80, _t80 = noisy_engine()
_c80.duck_depth = 80.0
_m80 = ContextAwareAudioEngine(_c80)
check(
    "curve: ducking_max_ratio() follows duck_depth",
    _m60.duck_max_ratio() > _m80.duck_max_ratio(),
    f"عمق 60: {_m60.duck_max_ratio():.3f} | عمق 80: {_m80.duck_max_ratio():.3f}",
)
check(
    "curve: ducking_min_ratio() follows duck_depth too",
    _m60.duck_min_ratio() > _m80.duck_min_ratio(),
    f"عمق 60: {_m60.duck_min_ratio():.3f} | عمق 80: {_m80.duck_min_ratio():.3f}",
)
check(
    "curve: the depth field is the only knob",
    abs(_m60.duck_max_ratio() - (1.0 - 60.0 / 100.0)) < 1e-9
    and abs(_m80.duck_max_ratio() - (1.0 - 80.0 / 100.0)) < 1e-9,
    f"{_m60.duck_max_ratio():.3f} / {_m80.duck_max_ratio():.3f}",
)

# المحرك يمرّر العتبة الحيّة لا قيمة الإعدادات الثابتة
# عند 42dB بالضبط (عتبة الغرفة الصاخبة) يجب أن يعطي أقصى خفض
_e2, _v2, _cfg2, _t2b = noisy_engine()
_thr2 = _v2.speech_threshold()
for _ in range(4):
    _c = _e2.process_frame(
        _v2.analyze_frame(_thr2, timestamp=_t2b), day_at(15, 0)
    )
    _t2b += STEP
check(
    "curve: the engine uses the live threshold, not the config constant",
    abs(_c.volume_ratio - _e2.duck_max_ratio()) < 1e-6,
    f"{_c.volume_ratio} vs {_e2.duck_max_ratio()}",
)
check(
    "curve: speaking at the threshold is ducked",
    _c.state == EngineState.DUCKED,
    _c.state.value,
)

# ===== P5: عمق الخفض =====
for depth, expect_max_cut in ((60.0, 60.0), (70.0, 70.0), (80.0, 80.0)):
    _c3 = EngineConfig()
    _c3.duck_depth = depth
    _e3 = ContextAwareAudioEngine(_c3)
    _got = (1.0 - _e3.duck_max_ratio()) * 100
    check(
        f"depth: {depth:.0f}% preset gives {expect_max_cut:.0f}% max cut",
        abs(_got - expect_max_cut) < 0.01,
        _got,
    )

_c4 = EngineConfig()
_c4.duck_depth = 80.0
_e4 = ContextAwareAudioEngine(_c4)
_min_cut = (1.0 - _e4.duck_min_ratio()) * 100
check("depth: 80% preset peaks below 95% (no promise of 90)", _min_cut < 95.0, _min_cut)
check("depth: min ratio is floored", _e4.duck_min_ratio() >= 0.02, _e4.duck_min_ratio())

# 90% لا يمكن بلوغها عند 65dB: قرار صريح لا منحنى
_c5 = EngineConfig()
_c5.duck_depth = 80.0
_e5 = ContextAwareAudioEngine(_c5)
_v5 = VadProcessor(_c5)
_t5 = feed(_v5, 3.5, 200, 6000.0)
for _ in range(4):
    _c6 = _e5.process_frame(_v5.analyze_frame(65.0, timestamp=_t5), day_at(15, 0))
    _t5 += STEP
check(
    "depth: 65dB is a hard mute, never a 90% duck",
    _c6.state == EngineState.DEBATE_MUTED,
    _c6.state.value,
)

print(f"\nالنتيجة: {PASSED} ناجح / {FAILED} فاشل")
sys.exit(1 if FAILED else 0)
