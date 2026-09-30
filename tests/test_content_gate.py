"""
تكامل المحتوى مع محرك الخلفية: البوابة والأولويات.

الاختبارات هنا عن العلاقة بين المحرّكين لا عن آلة المحتوى نفسها:
أن الصلاة تتجاوز المحتوى، وأن النقاش يوقفه لا ينهيه، وأن الخلفية
تخفت استبدالاً لا ضرباً.
"""

import shutil
import sys
import tempfile
import wave
from datetime import datetime, time as dtime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.context_aware_audio import ContextAwareAudioEngine, EngineConfig
from src.context_aware_audio.audio_types import (
    AudioFrame,
    ContentAction,
    DayPeriod,
    EngineState,
)
from src.context_aware_audio.content_engine import ContentEngine, ContentState
from src.context_aware_audio.content_library import ContentLibrary
from src.context_aware_audio.content_store import ContentStore

PASSED = FAILED = 0
STEP = 0.2

BASE_TIME = datetime(2026, 9, 30, 8, 0, 0)  # الضحى: نافذة 8ث قبل عتبة التأمل
MAGHRIB = dtime(18, 10)
FAJR = dtime(5, 10)
DHUHR = dtime(12, 5)


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


def build(window="duha_wisdom", name="wisdom", seconds=60.0):
    """محرك خلفية فيه محرك محتوى بمخزن حقيقي ومقطع واحد."""
    tmp = Path(tempfile.mkdtemp())
    lib = tmp / "lib"
    wav(lib / f"{window}__001__{name}.wav", seconds)
    cfg = EngineConfig()
    store = ContentStore(tmp / "c.db")
    ContentLibrary(lib, store, cfg).scan()
    engine = ContextAwareAudioEngine(cfg)
    engine.content = ContentEngine(cfg, store)
    return tmp, engine, store, cfg


def step(engine, clock, seconds, db=20.0, speech=False, now=None):
    """
    يمرّر نبضات عبر محرك الخلفية نفسه، لا عبر المحتوى وحده.

    يعيد (آخر أمر، الإجراءات التي ظهرت، أول أمر لكل إجراء). حقول
    المحتوى تُملأ عند الانتقال فقط - المواصفة تجعل _with_content
    تعود مبكراً على NONE - فالفحص الذي يقرأ آخر أمر يرى خرائط فارغة
    فيُفترض ما لم يحدث.
    """
    last = None
    seen = []
    at = {}
    for _ in range(int(seconds / STEP)):
        clock["ts"] += STEP
        clock["now"] = datetime.fromtimestamp(clock["ts"])
        when = now or clock["now"]
        frame = AudioFrame(timestamp=clock["ts"], db_level=db, is_speech=speech)
        last = engine.process_frame(frame, when)
        if not seen or seen[-1] is not last.content_action:
            seen.append(last.content_action)
        at.setdefault(last.content_action, last)
    return last, seen, at


def new_clock(base=None):
    t = (base or BASE_TIME).timestamp()
    return {"ts": t, "now": datetime.fromtimestamp(t)}


def done(tmp, store):
    store.close()
    shutil.rmtree(tmp, ignore_errors=True)


# ===== 1) المحتوى يبدأ من فرع الخلفية اليومي =====
# نافذة duha_wisdom تحتاج 8ث هدوء، وعتبة التأمل 10ث. فالمحتوى
# يبدأ أولاً والفرع اليومي هو صاحب القرار - لا فرع التأمل.
tmp, engine, store, cfg = build()
clock = new_clock()
cmd, seen, at = step(engine, clock, 10.0)
check(
    "gate: the daily branch starts the content",
    ContentAction.START in seen,
    seen,
)
check(
    "gate: state is still the background state",
    cmd.state == EngineState.DAILY_AMBIENT,
    cmd.state.value,
)
check(
    "gate: the file is the background file",
    cmd.file == "water_stream.wav",
    cmd.file,
)
# حقول المحتوى تُملأ عند الانتقال: المواصفة تجعل _with_content
# تعود مبكراً على NONE، فآخر أمر في تشغيل مستمر يحمل خرائط فارغة.
# نقرأ أمر START نفسه. الحالة المستمرة تُقرأ من محرك المحتوى.
# .get لا []: غياب الإجراء يجب أن يُفشل الفحص لا أن يهدم الملف
_start_cmd = at.get(ContentAction.START, cmd)
check(
    "gate: the content file is carried on the start",
    _start_cmd.content_file and "wisdom" in _start_cmd.content_file,
    _start_cmd.content_file,
)
check(
    "gate: the content volume is carried on the start",
    _start_cmd.content_volume == cfg.content_volume,
    _start_cmd.content_volume,
)
check(
    "gate: the start is at position zero",
    _start_cmd.content_position_sec == 0.0,
    _start_cmd.content_position_sec,
)
check(
    "gate: the steady state keeps no content action",
    cmd.content_action is ContentAction.NONE,
    cmd.content_action.value,
)
check(
    "gate: the content engine holds the live position",
    engine.content.position_sec > 0.0,
    engine.content.position_sec,
)
check(
    "gate: the reason names the window on the start",
    "بدء" in _start_cmd.reason,
    _start_cmd.reason,
)
done(tmp, store)

# ===== 2) الاستبدال لا الضرب في نسبة الخلفية =====
# نبني محرّكين: واحد بمحتوى معطّل ليُقرأ منحنى الخفض منه، وآخر
# بالمحتوى. البناء الواحد لا ينفع: once_per_day يمنع بثّاً ثانياً
# في اليوم نفسه، فيبدو المحتوى معطّلاً وهو ليس كذلك.
tmp_off, eng_off, store_off, cfg_off = build()
cfg_off.content_enabled = False
clock = new_clock()
_, _, _ = step(eng_off, clock, 1.0, db=50.0, speech=True)
duck_cmd, _, _ = step(eng_off, clock, 2.0, db=50.0, speech=True)
check("replace: with the content off the ducking branch is active",
      duck_cmd.state == EngineState.DUCKED, duck_cmd.state.value)
duck_ratio = duck_cmd.volume_ratio
check("replace: the duck curve is not the ambient floor",
      abs(duck_ratio - cfg_off.content_ambient_ratio) > 1e-6, duck_ratio)
check("replace: a product would have been near silence",
      duck_ratio * cfg_off.content_ambient_ratio < 0.1,
      duck_ratio * cfg_off.content_ambient_ratio)
done(tmp_off, store_off)

tmp, engine, store, cfg = build()
clock = new_clock()
step(engine, clock, 10.0)
check("replace: the content is running before the talk",
      engine.content.is_running is True, engine.content.state)
cmd, seen, at = step(engine, clock, 2.0, db=50.0, speech=True)
check("replace: the ducking branch is still the background state",
      cmd.state == EngineState.DUCKED, cmd.state.value)
check("replace: with content the ratio is the ambient floor",
      cmd.volume_ratio == cfg.content_ambient_ratio, cmd.volume_ratio)
check("replace: the floor is NOT the product of the two",
      abs(cmd.volume_ratio - duck_ratio * cfg.content_ambient_ratio) > 1e-6,
      cmd.volume_ratio)
check("replace: the ratio is the floor on the pause frame too",
      at.get(ContentAction.PAUSE, cmd).volume_ratio == cfg.content_ambient_ratio,
      at.get(ContentAction.PAUSE, cmd).volume_ratio)
done(tmp, store)

# ===== 3) الصلاة تتجاوز المحتوى تماماً =====
tmp, engine, store, cfg = build()
clock = new_clock()
step(engine, clock, 13.0)
check("prayer: content is running first", engine.content.is_running is True)
# ندخل نافذة الصلاة: المحرك يعرض وقتاً في بالمغرب
cmd, seen, at = step(engine, clock, 1.0, now=datetime(2026, 9, 30, 18, 10, 0))
check(
    "prayer: the background is muted",
    cmd.state == EngineState.PRAYER_MUTED,
    cmd.state.value,
)
check(
    "prayer: the content is stopped",
    ContentAction.STOP in seen,
    seen,
)
check(
# سبب الإيقاف يُمرَّر من فرع الصلاة نفسه فيتكرر فيطرفين. المهم
# أن السطرين انضما، أي أن المشغّل عرف أنه يجب أن يوقف.
    "prayer: the stop reaches the command",
    " | " in at.get(ContentAction.STOP, cmd).reason,
    at.get(ContentAction.STOP, cmd).reason,
)
check("prayer: no content file survives", not engine.content.is_running)
check("prayer: no position remains", engine.content.position_sec == 0.0)
check("prayer: no log row is left open", engine.content._log_id is None)
check("prayer: the log was closed", bool(store.stats()), store.stats())
done(tmp, store)

# ===== 4) النقاش الحامي يوقف ولا ينهي =====
tmp, engine, store, cfg = build()
clock = new_clock()
step(engine, clock, 13.0)
step(engine, clock, 2.0)
check("debate: content is running", engine.content.is_running is True)
before = engine.content.position_sec
cmd, seen, at = step(engine, clock, 1.0, db=70.0, speech=True)
check(
    "debate: the background is muted",
    cmd.state == EngineState.DEBATE_MUTED,
    cmd.state.value,
)
check(
    "debate: content is paused, not stopped",
    cmd.content_action is ContentAction.PAUSE,
    cmd.content_action.value,
)
check("debate: the clip survives", engine.content.is_running is True)
check(
    "debate: the position is rewound",
    engine.content.position_sec < before,
    f"{before:.2f} -> {engine.content.position_sec:.2f}",
)
# بعد 60 ثانية هدوء يعود الاثنان
cmd, seen, at = step(engine, clock, 62.0, db=20.0)
check(
    "debate: the background returns",
    cmd.state != EngineState.DEBATE_MUTED,
    cmd.state.value,
)
check("debate: the content resumes", engine.content.is_running is True)
done(tmp, store)

# ===== 5) فترة بلا نافذة محتوى =====
tmp, engine, store, cfg = build()
clock = new_clock()
cmd, seen, at = step(engine, clock, 20.0, now=datetime(2026, 9, 30, 19, 0, 0))
check(
    "no window: maghrib_isha mutes the background",
    cmd.state == EngineState.DAILY_AMBIENT and cmd.is_muted,
    f"{cmd.state.value} muted={cmd.is_muted}",
)
check(
    "no window: no content action",
    cmd.content_action is ContentAction.NONE,
    cmd.content_action.value,
)
# سبب «لا نافذة» يخصّ المحتوى وحده ولا يُحقن في سبب الخلفية: حقن
# سطر كامل في كل نبضة يغرق سجلّ الحالة التي يخدمه.
check(
    "no window: the content engine reports no window",
    engine.content._window_for(DayPeriod.MAGHRIB_ISHA) is None,
    engine.content._window_for(DayPeriod.MAGHRIB_ISHA),
)
done(tmp, store)

# ===== 6) تعطيل content_enabled من المحرك =====
tmp, engine, store, cfg = build()
cfg.content_enabled = False
clock = new_clock()
# 9 ثوانٍ: نافذة المحتوى تحتاج 8ث، وعتبة التأمل 10ث. فلا نصل
# إلى أي منهما، والفحص يبقى عن التعطيل وحده.
cmd, seen, at = step(engine, clock, 9.0)
check("disabled: nothing starts", ContentAction.START not in seen, seen)
check(
    "disabled: the background still plays normally",
    cmd.state == EngineState.DAILY_AMBIENT and not cmd.is_muted,
    cmd.state.value,
)
check(
    "disabled: the background is not pushed to the ambient floor",
    cmd.volume_ratio == 1.0,
    cmd.volume_ratio,
)
done(tmp, store)

# ===== 7) engine.reset يمرّر إلى المحتوى =====
tmp, engine, store, cfg = build()
clock = new_clock()
step(engine, clock, 13.0)
check("reset: content is running before", engine.content.is_running is True)
engine.reset()
check("reset: the content engine was reset", engine.content.is_running is False)
check(
    "reset: its state is IDLE",
    engine.content.state == ContentState.IDLE,
    engine.content.state,
)
check("reset: no position", engine.content.position_sec == 0.0)
check("reset: no postponed windows", engine.content._postponed_windows == set())
done(tmp, store)

# ===== 8) بوابة الضجيج عبر محرك الخلفية =====
tmp, engine, store, cfg = build()
clock = new_clock()
for _ in range(3):
    step(engine, clock, 1.0, db=60.0)
check(
    "gate: a loud room postpones the content",
    "duha_wisdom" in engine.content._postponed_windows,
    engine.content._postponed_windows,
)
cmd, seen, at = step(engine, clock, 30.0, db=20.0)
check(
    "gate: nothing starts while abandoned",
    ContentAction.START not in seen,
    seen,
)
check(
    "gate: the content gate never mutes the background",
    cmd.is_muted is False,
    cmd.is_muted,
)
done(tmp, store)

# ===== 9) الخلفية تخفت تحت المحتوى حتى أثناء المقاطعة =====
tmp, engine, store, cfg = build()
clock = new_clock()
step(engine, clock, 13.0)
step(engine, clock, 2.0)
cmd, seen, at = step(engine, clock, 2.0, db=50.0, speech=True)
check(
    "interrupt: content is paused exactly once",
    ContentAction.PAUSE in seen and seen.count(ContentAction.PAUSE) == 1,
    seen,
)
_pause = at.get(ContentAction.PAUSE, cmd)
check(
    "interrupt: the background stays faint on the pause frame",
    _pause.volume_ratio == cfg.content_ambient_ratio,
    _pause.volume_ratio,
)
check(
    "interrupt: it stays faint on the frames that follow",
    cmd.volume_ratio == cfg.content_ambient_ratio,
    cmd.volume_ratio,
)
check(
    "interrupt: the reason shows the interruption",
    "مقاطعة" in _pause.reason,
    _pause.reason,
)
check(
    "interrupt: later frames report the hold, not a second pause",
    "معلّق" in cmd.reason,
    cmd.reason,
)
done(tmp, store)

# ===== 10) التدرّج (fade) يبقى كما هو مع المحتوى =====
tmp, engine, store, cfg = build()
clock = new_clock()
step(engine, clock, 13.0)
cmd, seen, at = step(engine, clock, 2.0)
check(
    "fade: the background still fades in over its own duration",
    abs(cmd.fade_duration_sec - cfg.fade_in_duration_sec) < 1e-9,
    cmd.fade_duration_sec,
)
done(tmp, store)


# ===== 11) السقف: الأرضية لا ترفع منحنى الخفض ولا تكذب في السجل =====
# كان السطر `cmd.volume_ratio = content_ambient_ratio` بلا شرط: كل
# نسبةAway تُنسخ. عند 64.9dB يقرر المحرك 10% ويسمّي السبب «خفض 90%»
# بينما تستقبل الخلطة 20% — أي أعلى مماقرر، في أسوأ لحظة: كلام عالٍ
# على بُعد 0.1dB من كتم النقاش الحامي. وMIN_DUCK_RATIO يصبح غير قابل
# للوصول ما دام المحتوى يعمل.
#
# نقطة 40dB لا تكفي: المنحنى عندها 0.22 أو أقل، فالسقف والإسناد
# يتّفقان صدفةً. الفارق يظهر حيث المنحنى أدنى من السقف.
tmp, engine, store, cfg = build()
clock = new_clock()
step(engine, clock, 10.0)
check("ceiling: the content is running before we raise the voice",
      engine.content.is_running is True, engine.content.state)

# نقطة مرجعية: كلام هادئ، المنحنى فوق السقف
def ratio_at(db, frames=6):
    """يبثّ كلاماً بمستوى ثابت ويعيد آخر أمر."""
    for _ in range(frames):
        clock["ts"] += STEP
        clock["now"] = datetime.fromtimestamp(clock["ts"])
        engine.process_frame(
            AudioFrame(
                timestamp=clock["ts"],
                db_level=db,
                is_speech=True,
            ),
            clock["now"],
        )
    return engine.last_command


low = ratio_at(35.0)
# السقف يخفض لا يرفع: عند كلام هادئ يكون المنحنى فوق السقف
# فيُقصّ إلى السقف. وهذا هو الفارق عن الاستبدال في الاتجاه
# المعاكس: هناك صار المنحنى ميتاً، وهنا يبقى حيّاً.
low = ratio_at(35.0)
check(
    "ceiling: a quiet voice is capped at the floor",
    abs(low.volume_ratio - cfg.content_ambient_ratio) < 1e-9,
    f"{low.volume_ratio:.3f} مقابل {cfg.content_ambient_ratio}",
)
check(
    "ceiling: and the cap is what did it, not the curve",
    low.volume_ratio < engine.duck_max_ratio() + 1e-9,
    f"المنحنى الأقصى {engine.duck_max_ratio():.3f}",
)

# النقطة الحاسمة: كلام عالٍ جداً
high = ratio_at(64.9)
check("ceiling: the applied ratio never exceeds the floor",
      high.volume_ratio <= cfg.content_ambient_ratio + 1e-9,
      f"{high.volume_ratio:.3f} > {cfg.content_ambient_ratio}")
check("ceiling: and it is the duck curve, not the floor",
      high.volume_ratio < cfg.content_ambient_ratio - 0.01,
      f"{high.volume_ratio:.3f}")
check("ceiling: the deep duck is still reachable with content on",
      high.volume_ratio < engine.duck_min_ratio() + 0.02,
      f"{high.volume_ratio:.3f} مقابل أدنى {engine.duck_min_ratio():.3f}")

# ما يكتبه السجل يجب أن يصف ما استُعمل
import re as _re

_m = _re.search(r"خفض\s*(\d+)%", high.reason)
_claimed = int(_m.group(1)) if _m else -1
_applied_cut = round((1.0 - high.volume_ratio) * 100)
check("ceiling: the log states the reduction that was applied",
      _claimed == _applied_cut,
      f"السجل يقول {_claimed}% والاستُعمل {_applied_cut}%")
done(tmp, store)

print(f"\nRESULT: {PASSED} passed / {FAILED} failed")
sys.exit(1 if FAILED else 0)
