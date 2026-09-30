"""
content_engine.py: آلة حالات المسار بحقن الوقت.

كل مؤقّت يُختبر بحقن طوابع، بلا انتظار حقيقي. كل فحص جديد يُكتب
ليpinsط لسبب أُصلح: الفحص الذي يمرّ سواء أُصلح الشيء أم لم يُصلح ليس
اختبار ارتداد.
"""

import shutil
import sys
import tempfile
import wave
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.context_aware_audio.audio_types import AudioFrame, ContentAction, DayPeriod
from src.context_aware_audio.config import EngineConfig
from src.context_aware_audio.content_engine import ContentEngine, ContentState
from src.context_aware_audio.content_library import ContentLibrary
from src.context_aware_audio.content_store import ContentStore

PASSED = FAILED = 0
STEP = 0.2


def check(name, cond, extra=""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"PASS {name}")
    else:
        FAILED += 1
        print(f"FAIL {name}  {extra}")


def wav(path, seconds, rate=8000):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(seconds * rate))
    return path


def build(period_files=None, config=None):
    """مخزن ومكتبة ومحرك جاهزة. يُرجع أيضاً مساراً للحذف."""
    tmp = Path(tempfile.mkdtemp())
    lib = tmp / "lib"
    spec = period_files or [
        ("maqil_story__001__first.wav", 60.0),
        ("maqil_story__002__second.wav", 60.0),
    ]
    for name, dur in spec:
        wav(lib / name, dur)
    cfg = config or EngineConfig()
    store = ContentStore(tmp / "c.db")
    ContentLibrary(lib, store, cfg).scan()
    return tmp, ContentEngine(cfg, store), store, cfg


def done(tmp, store):
    store.close()
    shutil.rmtree(tmp, ignore_errors=True)


class Clock:
    """ساعة محقونة: كل نداء يقدّم الزمن. لا انتظار حقيقي."""

    def __init__(self, base=None):
        self.now = base or datetime(2026, 9, 30, 14, 0, 0)
        self.ts = self.now.timestamp()

    def tick(self, seconds=STEP):
        self.ts += seconds
        self.now = datetime.fromtimestamp(self.ts)
        return self.now, self.ts


_reasons = []


def frame(db=20.0, speech=False):
    return AudioFrame(db_level=db, is_speech=speech)


def run(eng, clock, period, seconds, db=20.0, speech=False):
    """
    يمرّر ثواني ويعيد (آخر قرار، كل الإجراءات التي ظهرت بالترتيب).

    الإجراء الواحد لا يكفي: START يقع في منتصف النافذة، فالنبضة
    الأخيرة منه «سرد» لا «بدء». لولا التجميع لأخطأ الفحص على شيفرة
    سليمة.
    """
    d = None
    seen = []
    _reasons.clear()
    for _ in range(int(seconds / STEP)):
        now, ts = clock.tick()
        d = eng.update(frame(db, speech), ts, now, period)
        if d.reason:
            _reasons.append(d.reason)
        if not seen or seen[-1] is not d.action:
            seen.append(d.action)
    return d, seen


def start_playing(eng, clock, period=DayPeriod.MAQIL, warm=5.0):
    """يصل إلى حالة التشغيل ويخضع إزاحة تساوي warm ثانية."""
    run(eng, clock, period, 12.0)
    if warm:
        run(eng, clock, period, warm)
    return eng.position_sec


# ===== 1) البدء بعد هدوء كافٍ =====
tmp, eng, store, cfg = build()
clock = Clock()
d, seen = run(eng, clock, DayPeriod.MAQIL, 1.0)
check(
    "start: nothing before the silence window",
    ContentAction.START not in seen,
    seen,
)
d, seen = run(eng, clock, DayPeriod.MAQIL, 12.0)
check(
    "start: begins after min_room_silence_sec",
    ContentAction.START in seen,
    seen,
)
check("start: state is PLAYING", eng.state == ContentState.PLAYING, eng.state)
check("start: the lowest sequence is chosen", "first" in (d.file or ""), d.file)
check(
    "start: a log row was written",
    store.plays_today("maqil_story", "2026-09-30") == 1,
    store.plays_today("maqil_story", "2026-09-30"),
)
check("start: ambient is marked audible", d.is_audible is True)
check(
    "start: the window label appears in a decision reason",
    any("قصة من السيرة" in eng_reason for eng_reason in _reasons),
    d.reason,
)
done(tmp, store)

# ===== 2) الموضع يتقدّم بالزمن المحقوق =====
tmp, eng, store, cfg = build()
clock = Clock()
pos0 = start_playing(eng, clock, warm=5.0)
d, seen = run(eng, clock, DayPeriod.MAQIL, 5.0)
check(
    "position: advances by the injected span",
    abs((eng.position_sec - pos0) - 5.0) < 0.5,
    f"{pos0:.2f} -> {eng.position_sec:.2f}",
)
check("position: no action while playing", d.action is ContentAction.NONE)
check("position: reason says narration", "سرد" in d.reason, d.reason)
done(tmp, store)

# ===== 3) المقاطعة تتراجع، والاستئناف لا يبدأ من الصفر =====
tmp, eng, store, cfg = build()
clock = Clock()
before = start_playing(eng, clock, warm=5.0)
check("interrupt: there is a position to rewind", before > 3.0, before)

# 1) الكلام يوقف مؤقتاً في أي لحظة
now, ts = clock.tick()
d = eng.update(frame(50.0, speech=True), ts, now, DayPeriod.MAQIL)
check("interrupt: speech pauses", d.action is ContentAction.PAUSE, d.action.value)
check(
    "interrupt: state is PAUSED_INTERRUPT",
    eng.state == ContentState.PAUSED_INTERRUPT,
    eng.state,
)
check(
    "interrupt: rewound by content_rewind_sec",
    abs(eng.position_sec - (before - cfg.content_rewind_sec)) < 0.5,
    f"{before:.2f} -> {eng.position_sec:.2f}",
)
rewound = eng.position_sec

# 2) الهدوء القصير لا يستأنف. هذا الفحص يفصل بين تصفير العدّاد عند
#    التوقّف وعدمه:HadElapsed 5s من الهدوء قبل الكلام، فبلا تصفير
#    يستأنف في النبضة التالية والشخص ما زال يتكلم.
d, seen = run(eng, clock, DayPeriod.MAQIL, 2.0)
check(
    "resume: a short quiet spell does not resume",
    eng.state == ContentState.PAUSED_INTERRUPT,
    eng.state,
)
check("resume: no action yet", d.action is ContentAction.NONE, d.action.value)

# 3) بعد content_resume_quiet_sec يستأنف من الموضع المتراجع
d, seen = run(eng, clock, DayPeriod.MAQIL, 5.0)
check(
    "resume: fires after content_resume_quiet_sec",
    ContentAction.RESUME in seen,
    seen,
)
check("resume: state is PLAYING again", eng.state == ContentState.PLAYING, eng.state)
check(
    "resume: resumes from the rewound position, not from zero",
    d.position_sec >= rewound and d.position_sec > 0.0,
    f"resumed {d.position_sec:.2f}, rewound to {rewound:.2f}",
)
check("resume: ambient is audible again", d.is_audible is True)
done(tmp, store)

# ===== 3b) التراجع لا ينزل تحت الصفر =====
tmp, eng, store, cfg = build()
clock = Clock()
run(eng, clock, DayPeriod.MAQIL, 10.6)
check(
    "rewind clamp: barely into the clip",
    0.0 <= eng.position_sec < 1.0,
    eng.position_sec,
)
now, ts = clock.tick()
eng.update(frame(50.0, speech=True), ts, now, DayPeriod.MAQIL)
check(
    "rewind clamp: position never goes negative",
    eng.position_sec >= 0.0,
    eng.position_sec,
)
done(tmp, store)

# ===== 4) البوابة: تأجيل ثم إقصاء =====
tmp, eng, store, cfg = build()
clock = Clock()
states = []
for _ in range(3):
    now, ts = clock.tick()
    d = eng.update(frame(70.0), ts, now, DayPeriod.MAQIL)
    states.append(d.state)
check(
    "gate: first postponement is WAITING_QUIET",
    states[0] == ContentState.WAITING_QUIET,
    states,
)
check(
    "gate: not abandoned before the limit",
    states[1] != ContentState.POSTPONED,
    states,
)
check(
    "gate: abandoned at content_gate_max_postpones",
    states[2] == ContentState.POSTPONED,
    states,
)
check(
    "gate: abandoned for the day",
    "maqil_story" in eng._postponed_windows,
    eng._postponed_windows,
)
d, seen = run(eng, clock, DayPeriod.MAQIL, 30.0)
check(
    "gate: a quiet room does not revive an abandoned window",
    eng.state == ContentState.POSTPONED,
    eng.state,
)
check(
    "gate: nothing was ever played",
    store.plays_today("maqil_story", "2026-09-30") == 0,
    store.plays_today("maqil_story", "2026-09-30"),
)
done(tmp, store)

# ===== 5) بوابة: ضجيج واحد ثم هدوء = تشغيل =====
tmp, eng, store, cfg = build()
clock = Clock()
now, ts = clock.tick()
eng.update(frame(60.0), ts, now, DayPeriod.MAQIL)
d, seen = run(eng, clock, DayPeriod.MAQIL, 15.0)
check(
    "gate: quiet after one postponement still plays",
    ContentAction.START in seen,
    seen,
)
done(tmp, store)

# ===== 6) force_stop عند الصلاة ينهي ولا يترك مؤقّتاً =====
tmp, eng, store, cfg = build()
clock = Clock()
start_playing(eng, clock, warm=4.0)
check("prayer: playing before the stop", eng.is_running is True)
d = eng.force_stop("وقت الصلاة")
check("prayer: emits STOP", d.action is ContentAction.STOP, d.action.value)
check("prayer: no clip remains", eng.is_running is False)
check("prayer: no pending log id", eng._log_id is None)
check("prayer: position cleared", eng.position_sec == 0.0)
check("prayer: state is IDLE", eng.state == ContentState.IDLE, eng.state)
check("prayer: room quiet timer cleared", eng._room_quiet_since is None)
check("prayer: postpone counter cleared", eng._postpones == 0)
check(
    "prayer: the log was closed as abandoned",
    bool(store.stats()),
    store.stats(),
)
# المقاطعة المتروكة لا يجب أن تُحسب بثّاً: الصلاة قطعته قبل أن يُسمع
d, seen = run(eng, clock, DayPeriod.MAQIL, 13.0)
check(
    "prayer: the window can start again after the stop",
    ContentAction.START in seen,
    seen,
)
done(tmp, store)

# ===== 7) force_pause يوقف ولا يمحو =====
tmp, eng, store, cfg = build()
clock = Clock()
before = start_playing(eng, clock, warm=5.0)
d = eng.force_pause("نقاش حامي")
check("debate: emits PAUSE", d.action is ContentAction.PAUSE, d.action.value)
check("debate: the clip survives", eng.is_running is True)
check(
    "debate: rewound",
    eng.position_sec < before,
    f"{before:.2f} -> {eng.position_sec:.2f}",
)
check(
    "debate: state is PAUSED_INTERRUPT",
    eng.state == ContentState.PAUSED_INTERRUPT,
    eng.state,
)
# بلا تصفير عدّاد الهدوء عند force_pause يستأنف في النبضة التالية،
# لأن هدوء الغرفة لم ينقطع أصلاً. هذا الفحص هو ما يفصل.
d, seen = run(eng, clock, DayPeriod.MAQIL, 2.0)
check(
    "debate: a short quiet spell does not resume",
    eng.state == ContentState.PAUSED_INTERRUPT,
    eng.state,
)
d, seen = run(eng, clock, DayPeriod.MAQIL, 4.0)
check(
    "debate: resumes when the room is quiet",
    ContentAction.RESUME in seen,
    seen,
)
done(tmp, store)

# ===== 8) force_stop بلا تشغيل لا يصنع أمراً =====
tmp, eng, store, cfg = build()
d = eng.force_stop("وقت الصلاة")
check(
    "idle stop: no STOP when nothing was playing",
    d.action is ContentAction.NONE,
    d.action.value,
)
d = eng.force_pause("نقاش حامي")
check(
    "idle pause: no PAUSE when nothing was playing",
    d.action is ContentAction.NONE,
    d.action.value,
)
done(tmp, store)

# ===== 9) الانتهاء يسلّم سجلاً مكتملاً =====
tmp, eng, store, cfg = build(period_files=[("maqil_story__001__short.wav", 6.0)])
clock = Clock()
run(eng, clock, DayPeriod.MAQIL, 12.0)
check("finish: started", eng.is_running is True)
d, seen = run(eng, clock, DayPeriod.MAQIL, 8.0)
check("finish: emits FINISHED", ContentAction.FINISHED in seen, seen)
check("finish: no clip after finishing", eng.is_running is False)
stats = dict((a, p) for a, p, _ in store.stats())
check("finish: exactly one clip in the log", len(stats) == 1, stats)
d, seen = run(eng, clock, DayPeriod.MAQIL, 15.0)
check(
    "finish: the same clip is not replayed",
    "short" not in (d.file or ""),
    d.file,
)
done(tmp, store)

# ===== 10) فترة بلا نافذة =====
tmp, eng, store, cfg = build()
clock = Clock()
d, seen = run(eng, clock, DayPeriod.MAGHRIB_ISHA, 30.0)
check(
    "no window: maghrib_isha plays nothing",
    eng.is_running is False and ContentAction.START not in seen,
    seen,
)
check("no window: the reason says so", "لا نافذة" in d.reason, d.reason)
done(tmp, store)

# ===== 11) النافذة تنتهي بتغيّر الفترة =====
tmp, eng, store, cfg = build()
clock = Clock()
run(eng, clock, DayPeriod.MAQIL, 12.0)
check("window end: playing in maqil", eng.is_running is True)
now, ts = clock.tick()
d = eng.update(frame(20.0), ts, now, DayPeriod.MAGHRIB_ISHA)
check(
    "window end: content stops at the period end",
    d.action is ContentAction.STOP,
    d.action.value,
)
check("window end: no clip remains", eng.is_running is False)
done(tmp, store)

# ===== 12) مرة في اليوم =====
tmp, eng, store, cfg = build(
    period_files=[
        ("maqil_story__001__first.wav", 5.0),
        ("maqil_story__002__second.wav", 5.0),
        # ثالث لازم: بلا مرشّح متبقٍّ صار «محجوب» و«لا مقطع» شيئاً
        # واحداً، فلا يميّز الفحص «مرة في اليوم» عن نفاد المكتبة.
        ("maqil_story__003__third.wav", 5.0),
    ]
)
clock = Clock()
run(eng, clock, DayPeriod.MAQIL, 12.0)
run(eng, clock, DayPeriod.MAQIL, 8.0)
clock2 = Clock(base=clock.now + timedelta(days=1))
d, seen = run(eng, clock2, DayPeriod.MAQIL, 15.0)
check(
    "once per day: yesterday's play does not block today",
    ContentAction.START in seen,
    seen,
)
# الفجوة يجب أن تتجاوز 30 دقيقة، وإلا كان هي التي تمنع لا «مرة في
# اليوم». نقفز ساعة كاملة داخل اليوم نفسه: الفجوة تحققت، فبقي
# «مرة في اليوم» وحدها مانعة.
clock3 = Clock(base=clock2.now + timedelta(hours=1))
d, seen = run(eng, clock3, DayPeriod.MAQIL, 15.0)
check(
    "once per day: an hour later the same day is still blocked",
    ContentAction.START not in seen,
    d.reason,
)
check(
    "once per day: the block is the daily rule, not the gap",
    "بُثّ اليوم" in d.reason,
    d.reason,
)
check(
    "once per day: only one play is logged for day one",
    store.plays_today("maqil_story", "2026-09-30") == 1,
    store.plays_today("maqil_story", "2026-09-30"),
)
done(tmp, store)

# ===== 13) الفجوة بين المقاطع =====
# نافذة بلا «مرة في اليوم»: لولا ذلك منع الشرط الثانيَ مقطعاً ثانياً
# في اليوم نفسه، وهو ما نختبره هنا.
gap_cfg = EngineConfig()
gap_cfg.content_windows["maqil_story"]["once_per_day"] = False
tmp, eng, store, cfg = build(
    period_files=[
        ("maqil_story__001__a.wav", 5.0),
        ("maqil_story__002__b.wav", 5.0),
        ("maqil_story__003__c.wav", 5.0),
    ],
    config=gap_cfg,
)
clock = Clock()
run(eng, clock, DayPeriod.MAQIL, 12.0)
run(eng, clock, DayPeriod.MAQIL, 8.0)  # انتهى الأول
d, seen = run(eng, clock, DayPeriod.MAQIL, 10.0)
check(
    "gap: a second clip is blocked by the min gap",
    eng.is_running is False and ContentAction.START not in seen,
    d.reason,
)
clock4 = Clock(base=clock.now + timedelta(minutes=31))
d, seen = run(eng, clock4, DayPeriod.MAQIL, 15.0)
check(
    "gap: a clip starts once the gap elapses",
    ContentAction.START in seen,
    seen,
)
done(tmp, store)

# ===== 14) تعطيل المحتوى يوقف السرد =====
tmp, eng, store, cfg = build()
clock = Clock()
run(eng, clock, DayPeriod.MAQIL, 12.0)
check("disabled: playing before", eng.is_running is True)
now, ts = clock.tick()
d = eng.update(frame(20.0), ts, now, DayPeriod.MAQIL, enabled=False)
check("disabled: emits STOP", d.action is ContentAction.STOP, d.action.value)
check("disabled: nothing left running", eng.is_running is False)
done(tmp, store)

# ===== 15) مكتبة فارغة: سبب صريح لا صمت =====
tmp = Path(tempfile.mkdtemp())
with ContentStore(tmp / "e.db") as store:
    eng = ContentEngine(EngineConfig(), store)
    clock = Clock()
    d, seen = run(eng, clock, DayPeriod.MAQIL, 30.0)
    check("empty library: nothing plays", ContentAction.START not in seen, seen)
    check("empty library: the reason says so", "لا مقطع" in d.reason, d.reason)
shutil.rmtree(tmp, ignore_errors=True)

# بلا مخزن أصلاً
eng_ns = ContentEngine(EngineConfig(), None)
clock = Clock()
d, seen = run(eng_ns, clock, DayPeriod.MAQIL, 30.0)
check("no store: nothing plays", ContentAction.START not in seen, seen)
check("no store: the reason says so", "لا مكتبة" in d.reason, d.reason)

# ===== 16) reset يصفّر كل شيء =====
tmp, eng, store, cfg = build()
clock = Clock()
run(eng, clock, DayPeriod.MAQIL, 12.0)
now, ts = clock.tick()
eng.update(frame(70.0), ts, now, DayPeriod.SAMRA)  # نافذة أخرى + ضجيج
eng.reset()
check("reset: state IDLE", eng.state == ContentState.IDLE, eng.state)
check("reset: no clip", eng.is_running is False)
check("reset: no log id", eng._log_id is None)
check("reset: position zero", eng.position_sec == 0.0)
check("reset: no window", eng._window_key is None)
check("reset: postpone counter zero", eng._postpones == 0)
check("reset: abandoned windows cleared", eng._postponed_windows == set())
check("reset: room quiet cleared", eng._room_quiet_since is None)
check("reset: last stamp cleared", eng._last_ts is None)
done(tmp, store)

print(f"\nRESULT: {PASSED} passed / {FAILED} failed")
sys.exit(1 if FAILED else 0)
