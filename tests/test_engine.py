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
f = vad.analyze_frame(50)
check("VAD يصنف 50dB كلاماً", f.is_speech, str(f))
f2 = vad.analyze_frame(75)
check("VAD يصنف 75dB صخباً", f2.is_overlapping, str(f2))
import struct

silence = struct.pack("<1600h", *([0] * 1600))
f3 = vad.analyze_pcm(silence)
check("PCM صامت -> ليس كلاماً", not f3.is_speech, str(f3))


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

print(f"\nالنتيجة: {PASSED} ناجح / {FAILED} فاشل")
sys.exit(1 if FAILED else 0)
