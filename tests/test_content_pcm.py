"""
توصيل PCM بالميكروفون: read_pcm، وبوابة التحليل في النبضة.

الفحوص هنا على الواجهة لا على المحلّل: من يمرّر الكتلة، ومتى تُحلل،
وماذا يحدث حين تتكرّر. تحليل PCM نفسه له فحوصه في test_desktop.
"""

import struct
import sys
import tempfile
import tkinter as tk
from datetime import datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.context_aware_audio import settings
from src.context_aware_audio.mic_input import MicInput
from src.context_aware_audio.vad import VadProcessor

PASSED = FAILED = 0


def check(name, cond, extra=""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"PASS {name}")
    else:
        FAILED += 1
        print(f"FAIL {name}  {extra}")


def pcm_of(peak: int, frames: int = 1600) -> bytes:
    """كتلة PCM16 أحادية من قيمة ثابتة: dB محسوبة وسهلة التوقّع."""
    return struct.pack("<" + "h" * frames, *([peak] * frames))


# ===== 1) read_pcm: لا بيانات قبل التشغيل =====
mic = MicInput()
check("pcm: nothing before start", mic.read_pcm() == (None, 0), mic.read_pcm())
check("pcm: read_db is also nothing", mic.read_db() is None)

# ===== 2) الالتقاط: الخيطان اللذان يخزّان الكتلة =====
# لا نفتح ميكروفوناً في فحص، لكن الالتقاط هو ما يملأ _pcm، وبلا
# فحصه كان الفحص كله على حارسٍ يمرّر فارغاً. نمرّر الكتلة إلى
# الاستدعاءين كما يفعل الجهاز، فنمسح الأثر مباشرةً.
peak_db = 42.0

# --- أ) استدعاء sounddevice ---
captured = {}


class _FakeStream:
    def __init__(self, **kw):
        captured["cb"] = kw.get("callback")

    def start(self):
        captured["started"] = True


class _FakeSD:
    def InputStream(self, **kw):  # noqa: N802 - اسم واجهة sounddevice
        return _FakeStream(**kw)


m2 = MicInput()
m2._sd = _FakeSD()
m2.backend = "sounddevice"
m2.available = True
with mock.patch.object(MicInput, "_pcm_to_db", staticmethod(lambda b: peak_db)):
    ok = m2._start_sounddevice()
check("capture: sounddevice starts", ok is True and captured.get("started") is True)
raw_block = pcm_of(1000)
# الاستدعاء داخل سياق الحجب: هو الذي يحسب dB، ولو استُدعي خارجه
# لحسبه بالطريقة الحقيقية فبدا كأن الحفظ هو ما نُختبر.
with mock.patch.object(MicInput, "_pcm_to_db", staticmethod(lambda b: peak_db)):
    captured["cb"](raw_block, 1600, None, None)
check(
    "capture: the sounddevice callback stores the block",
    m2.read_pcm()[0] == raw_block,
    len(m2.read_pcm()[0] or b""),
)
check("capture: and numbers it", m2.read_pcm()[1] == 1, m2.read_pcm()[1])
check("capture: and keeps the dB as before", m2.read_db() == peak_db, m2.read_db())

# استدعاء ثانٍ: الرقم يتقدّم، والكتلة تتبدّل
with mock.patch.object(MicInput, "_pcm_to_db", staticmethod(lambda b: peak_db)):
    captured["cb"](pcm_of(2000), 1600, None, None)
pcm_a, seq_a = m2.read_pcm()
check(
    "capture: a second block replaces the first",
    pcm_a == pcm_of(2000) and seq_a == 2,
    (len(pcm_a or b""), seq_a),
)


# --- ب) خيط pyaudio ---
class _FakePaStream:
    def __init__(self):
        self.blocks = [pcm_of(3000), pcm_of(4000)]
        self.done = False

    def read(self, n, exception_on_overflow=False):
        if not self.blocks:
            self.done = True
            raise OSError("انتهى التيار")
        return self.blocks.pop(0)

    def stop_stream(self):
        pass

    def close(self):
        pass


class _FakePa:
    paInt16 = 8  # قيمة وهمية: القيمة الحقيقية رقمية لا تُقارن هنا

    def __init__(self):
        self.stream = _FakePaStream()
        self.terminated = False

    def open(self, **kw):
        return self.stream

    def terminate(self):
        self.terminated = True


class _FakePyaudio:
    """ما يعيده _ensure_pyaudio: كائن يحمل PyAudio لا الوحدة نفسها."""

    # paInt16 تُقرأ من الوحدة لا من كائن PyAudio، فبدونها يفشل open
    paInt16 = 8

    def __init__(self, pa):
        self._pa = pa

    def PyAudio(self):  # noqa: N802 - اسم واجهة pyaudio
        return self._pa


m3 = MicInput()
m3.backend = "pyaudio"
m3.available = True
fake_pa = _FakePa()
with (
    mock.patch.object(MicInput, "_ensure_pyaudio", lambda self: _FakePyaudio(fake_pa)),
    mock.patch.object(MicInput, "_pcm_to_db", staticmethod(lambda b: peak_db)),
):
    ok = m3._start_pyaudio()
check("capture: pyaudio starts", ok is True)
t = m3._thread
if t is not None:
    t.join(timeout=3.0)
pcm_b, seq_b = m3.read_pcm()
check("capture: the pyaudio thread stored a block", bool(pcm_b), len(pcm_b or b""))
check("capture: and numbered it", seq_b >= 1, seq_b)
check(
    "capture: the second block is the last one read",
    pcm_b == pcm_of(4000),
    len(pcm_b or b""),
)
check("capture: the thread ended when the stream did", t is None or not t.is_alive())

# ===== 3) stop() يمسح الكتلة =====
# تحليل PCM فوق بيانات تشغيل سابق ينسب الصوتَ لغرفةٍ لم تعد تُسمع.
# stop() ينتهي منفذاً حقيقياً، فنقطع طرفَي الخيط والمنفذ ونقيس
# الأثر الذي نقيسه: محو الكتلة.
with mock.patch.object(MicInput, "_join_reader", lambda self, timeout=0.5: None):
    m2.stop()
check("pcm: stop clears the block", m2.read_pcm() == (None, 0), m2.read_pcm())
# حارس _stream وحده كان يخفي الفحص كله: بعد الإيقاف يصير _stream
# None فيردّ read_pcm فارغاً ولو بقيت البايتات في الحقل. نفحص
# الحقل نفسه، فنقرأ ما يُفترض محوه لا ما يردّه المسار.
check(
    "pcm: stop releases the bytes, not just the handle",
    m2._pcm == b"" and m2._pcm_seq == 0,
    (len(m2._pcm), m2._pcm_seq),
)
with mock.patch.object(MicInput, "_join_reader", lambda self, timeout=0.5: None):
    m3.stop()
check(
    "pcm: stop clears the pyaudio block too", m3.read_pcm() == (None, 0), m3.read_pcm()
)

# ===== 4) البوابة: ما الذي يمرّ إلى analyze_pcm =====
tmp = Path(tempfile.mkdtemp())
root = tk.Tk()
root.withdraw()
try:
    from src.context_aware_audio.app import DesktopApp

    with mock.patch.object(settings, "writable_path", lambda n: tmp / n):
        app = DesktopApp(root, log_path=None)
        stamp = datetime(2026, 9, 30, 8, 0, 0)
        ts = stamp.timestamp()
        calls = []

        # نمسح أي استدعاء حقيقي
        real_frame = app.vad.analyze_frame
        real_pcm = app.vad.analyze_pcm

        def spy_frame(db, timestamp=None):
            calls.append(("frame", db, timestamp))
            return real_frame(db, timestamp=timestamp)

        def spy_pcm(pcm, sample_rate=16000, timestamp=None):
            calls.append(("pcm", len(pcm), sample_rate, timestamp))
            # analyze_pcm ينادي analyze_frame في داخله. لو سجّلته
            # لأقنعتنا الفحوص أن البوابة اختارته، وهي لم تختره. فنخفي
            # الدالة الحقيقية أثناء النداء، ثم نعيدها.
            spy = app.vad.analyze_frame
            app.vad.analyze_frame = real_frame
            try:
                return real_pcm(pcm, sample_rate=sample_rate, timestamp=timestamp)
            finally:
                app.vad.analyze_frame = spy

        app.vad.analyze_frame = spy_frame
        app.vad.analyze_pcm = spy_pcm

        # --- أ) بلا ميكروفون: analyze_frame ---
        app.use_mic.set(False)
        app.mic.available = False
        app._pcm_seq_used = -1
        calls.clear()
        # نتتبّع استدعاء read_pcm نفسه لا النتيجة فقط. النتيجة
        # متطابقة في الحالتين - بلا تيار يردّ (None, 0) ويمرّ إلى
        # analyze_frame - فالفحص على النتيجة لا يرى الفرق، والحارس
        # موجود تحديداً ليقفل على الميكروفون وهو مقفل.
        read_calls = []
        real_read_pcm = app.mic.read_pcm

        def spy_read_pcm():
            read_calls.append(1)
            return real_read_pcm()

        app.mic.read_pcm = spy_read_pcm
        fr = app._analyze_tick_frame(20.0, stamp)
        check(
            "gate: with the mic off the PCM block is never read",
            read_calls == [],
            read_calls,
        )
        check(
            "gate: the frame carries the timestamp",
            calls and calls[0][2] == ts,
            calls[:1],
        )
        check("gate: a frame comes back", fr.db_level == 20.0, fr.db_level)

        # --- ب) ميكروفون بلا كتلة: analyze_frame ---
        app.use_mic.set(True)
        app.mic.available = True
        app._pcm_seq_used = -1
        calls.clear()
        app._analyze_tick_frame(20.0, stamp)
        check(
            "gate: with no PCM block it falls back to the energy path",
            [c[0] for c in calls] == ["frame"],
            calls,
        )

        # --- ج) كتلة جديدة: analyze_pcm ---
        # لا يمرّ شيء بلا تيار مفتوح: read_pcm يحرس على _stream، فبلا
        # فتح حقيقي في الفحص نضع كائناً مكانه. هذا لا يختبر الالتقاط،
        # بل التوجيه - فالالتقاط له فحوصه في test_desktop.
        app.mic._stream = object()
        block = pcm_of(3000)
        with app.mic._lock:
            app.mic._pcm = block
            app.mic._pcm_seq = 11
        app._pcm_seq_used = -1
        calls.clear()
        fr = app._analyze_tick_frame(20.0, stamp)
        check(
            "gate: a new PCM block goes to analyze_pcm",
            [c[0] for c in calls] == ["pcm"],
            calls,
        )
        check(
            "gate: the block is passed whole",
            calls and calls[0][1] == len(block),
            calls[:1],
        )
        check(
            "gate: the mic sample rate is used",
            calls and calls[0][2] == 16000,
            calls[:1],
        )
        check(
            "gate: the PCM frame reports its own dB, not the passed one",
            abs(fr.db_level - real_pcm(block, timestamp=ts).db_level) < 1e-9,
            f"got {fr.db_level:.2f}",
        )
        check(
            "gate: the used sequence is recorded",
            app._pcm_seq_used == 11,
            app._pcm_seq_used,
        )

        # --- د) الكتلة نفسها مرة ثانية: لا تُحلَّل ---
        # هذا هو السبب في وجود الرقم أصلاً. الطاقة تُحسب من الكتلة
        # نفسها مرتين فتحلّ نافذة البدء 0.2ث مرتين، فيُؤكَّد كلامٌ بعد
        # 0.1ث من أوله - تباطؤٌ في القرار لا خطأ في الكلام.
        calls.clear()
        app._analyze_tick_frame(20.0, stamp)
        check(
            "gate: the same block is not analysed twice",
            [c[0] for c in calls] == ["frame"],
            calls,
        )

        # --- هـ) سيناريو مثبَّت: analyze_frame دائماً ---
        # المزروع ليس صوتاً. تمريره إلى محلّل PCM يجعله يبطل إبطالَ
        # webrtcvad على أساس صمتٍ مصطنع، فيسكت الكلام الحقيقي.
        app._scenario("talk")
        app._pcm_seq_used = -1
        with app.mic._lock:
            app.mic._pcm = block
            app.mic._pcm_seq = 12
        calls.clear()
        app._analyze_tick_frame(20.0, stamp)
        check(
            "gate: a pinned scenario never reaches the PCM analyser",
            [c[0] for c in calls] == ["frame"],
            calls,
        )
        check(
            "gate: the pinned dB is what is analysed",
            calls and calls[0][1] == 20.0,
            calls[:1],
        )

        # --- و) معطّل محلّل PCM: يُعزل، لا يوقف النبضة ---
        def boom(*a, **k):
            raise RuntimeError("webrtcvad انهار")

        app.vad.analyze_pcm = boom
        app._scenario_until = 0.0
        app._pcm_seq_used = -1
        with app.mic._lock:
            app.mic._pcm = block
            app.mic._pcm_seq = 13
        app._pcm_error_logged = False
        # نحفظ الاستثناء بدل letting it fly. لو لم يُعزل العطل لخرج
        # من _analyze_tick_frame وكسر الملف كله - وهو خطأ يُقرأ في
        # الفحص كمرور: والملف يموت قبل أن يُطبع حاصل.
        escaped = None
        fr = None
        try:
            fr = app._analyze_tick_frame(20.0, stamp)
        except Exception as exc:  # noqa: BLE001 - هذا ما نختبره
            escaped = exc
        check(
            "gate: a broken PCM analyser does not escape the tick",
            escaped is None,
            f"{type(escaped).__name__}: {escaped}" if escaped else "",
        )
        check(
            "gate: a broken PCM analyser falls back to energy",
            fr is not None and fr.db_level == 20.0,
            fr.db_level if fr is not None else "no frame",
        )
        check("gate: the fallback is logged once", app._pcm_error_logged is True)
        app._pcm_error_logged = True
        app.vad.analyze_pcm = spy_pcm

        # ===== 5) العدّاد يقرأ dB الإطار، عبر النبضة نفسها =====
        # على مسار PCM المستوى يُحسب داخل المحلّل، فقد يخالف القراءة
        # الأولى بنفس الكتلة. العدّاد الذي يعرض رقماً غير ما قرّره
        # المحرك يعرض كذباً.
        #
        # الفحص يمرّ بالنبضة (_tick) لا باستدعاء _update_readouts
        # باليد. النداء اليدوي يجعل الفحص يمرّر نفسه ما يريد التحقق
        # منه: نضع fr.db_level فنقارن fr.db_level، فيمرّ ولو كانت
        # النبضة تمرّر قيمة أخرى. هذا الفحص كان يمرّ بلا أثر.
        app.vad.analyze_pcm = real_pcm
        app.vad.analyze_frame = real_frame
        app.use_mic.set(True)
        app.mic.available = True
        app.mic._stream = object()
        app._scenario_until = 0.0
        app._pcm_seq_used = -1
        app.running = True
        seen = []
        logs = []
        app._update_readouts = lambda now, cmd, db, sp, raw: seen.append(db)
        real_log = app._log
        app._log = lambda msg: (logs.append(str(msg)), real_log(msg))[1]

        loud = pcm_of(9000)
        with app.mic._lock:
            app.mic._pcm = loud
            app.mic._pcm_seq = 20
        expect_db = VadProcessor.pcm_to_db(loud)
        seen.clear()
        logs.clear()
        app._tick()
        check(
            "readout: the tick ran without an error",
            not any("خطأ في النبضة" in m for m in logs),
            logs[:2],
        )
        check(
            "readout: the meter shows the frame's own dB",
            seen and abs(seen[-1] - expect_db) < 1e-6,
            f"shown {seen[-1] if seen else None} expected {expect_db:.2f}",
        )
        check(
            "readout: and that dB really is the PCM's, not the manual one",
            abs(expect_db - 20.0) > 1.0,
            expect_db,
        )

        # ===== 6) الحالة: dB متوافق بين الإطارين =====
        # على مسار الطاقة لا فرق، فلا بدّ أن يبقى السلوك السابق: الرقم
        # اليدوي يصل إلى العدّاد كما هو. `_current_db` يقرأ manual_db
        # حين لا ميكروفون، فنقارن بما تعطيه لا برقم نختاره.
        app.mic.read_pcm = spy_read_pcm
        app.use_mic.set(False)
        app.mic.available = False
        app.manual_db.set(37.0)
        app._pcm_seq_used = -1
        seen.clear()
        logs.clear()
        app._tick()
        check(
            "readout: the energy path is unchanged",
            seen and abs(seen[-1] - app.manual_db.get()) < 1e-9,
            f"shown {seen[-1] if seen else None} manual {app.manual_db.get()}",
        )
        app._log = real_log

        app.stop()
        app._on_close()
finally:
    try:
        root.destroy()
    except Exception:
        pass

import shutil

shutil.rmtree(tmp, ignore_errors=True)
print(f"\nRESULT: {PASSED} passed / {FAILED} failed")
sys.exit(1 if FAILED else 0)
