"""
اختبارات سطح المكتب - hermetic: بلا صوت حقيقي، بلا شبكة، بلا لمس لإعدادات المستخدم
"""

import json
import struct
import sys
import tempfile
import types
import wave
from datetime import datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.context_aware_audio import EngineConfig
from src.context_aware_audio.audio_types import EngineState, PlaybackCommand
from src.context_aware_audio import prayer_provider, prayer_engine, settings
from src.context_aware_audio.mic_input import MicInput
from src.context_aware_audio.prayer_engine import PrayerEngine
from src.context_aware_audio.real_player import RealPlayer
from src.context_aware_audio.sound_synth import (
    BUILDERS,
    ensure_assets,
    missing_assets,
)
from src.context_aware_audio.paths import assets_dir
from src.context_aware_audio.vad import VadProcessor

PASSED = FAILED = 0


def check(name, cond, extra=""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"PASS {name}")
    else:
        FAILED += 1
        print(f"FAIL {name} {extra}")


# ============ 1) الأصول الصوتية موجودة وصالحة ============
ensure_assets(force=False)
for name in BUILDERS:
    path = assets_dir() / name
    check(f"asset exists {name}", path.exists())
    if path.exists():
        try:
            with wave.open(str(path), "rb") as w:
                check(
                    f"wav valid {name}", w.getnframes() > 8000 and w.getsampwidth() == 2
                )
        except Exception as e:
            check(f"wav valid {name}", False, str(e))


# ============ 2) المشغّل: حساب master/EQ بلا تشغيل صوت ============
# RealPlayer.__init__ يهيّئ mixer حقيقياً، لذلك نحقنه بواجهة pygame وهمية


class _FakeChannel:
    def __init__(self, sink):
        self._sink = sink

    def play(self, *args, **kwargs):
        self._sink["plays"] += 1

    def set_volume(self, v):
        self._sink["volumes"].append(v)

    def fadeout(self, ms):
        self._sink["fades"].append(ms)

    def stop(self):
        self._sink["stops"] += 1


class _FakeMusic:
    def load(self, *args, **kwargs):
        pass

    def set_volume(self, *args, **kwargs):
        pass

    def play(self, *args, **kwargs):
        pass

    def stop(self):
        pass


def _make_fake_pygame():
    """نسخة pygame وهمية تطابق ما يستهلكه RealPlayer فقط."""
    import types

    sink = {"plays": 0, "volumes": [], "fades": [], "stops": 0}
    pg = types.ModuleType("pygame")

    mixer = types.ModuleType("pygame.mixer")
    mixer.init = lambda **kw: None
    mixer.set_num_channels = lambda n: None
    mixer.Sound = lambda path: object()
    mixer.Channel = lambda i: _FakeChannel(sink)
    mixer.music = _FakeMusic()

    pg.mixer = mixer
    pg.Sound = mixer.Sound
    return pg, sink


fake_pg, fake_sink = _make_fake_pygame()
with mock.patch.dict(sys.modules, {"pygame": fake_pg}):
    pl = RealPlayer()
    check("player picks pygame backend", pl.backend == "pygame", pl.backend)
    pl.set_master_volume(0.5)
    pl.set_eq(0.8)
    cmd = PlaybackCommand(
        file="water_stream.wav",
        target_db=38,
        volume_ratio=1.0,
        fade_duration_sec=0.1,
        state=EngineState.DAILY_AMBIENT,
        reason="t",
    )
    line = pl.apply(cmd)
    check("player apply ducked by master+eq", abs(pl.current_volume - 0.4) < 0.01, line)
    check("player reached fake backend", fake_sink["plays"] > 0, fake_sink)
    check("player pushed effective volume", fake_sink["volumes"][-1] == 0.4, fake_sink)
    pl.set_master_muted(True)
    pl.apply(cmd)
    check("player master mute", pl.is_muted)
    check("player faded out on mute", bool(fake_sink["fades"]), fake_sink)
    pl.set_master_muted(False)
    check(
        "player play_once returns bool",
        isinstance(pl.play_once("athan_chime.wav"), bool),
    )
    check("player play_once reached backend", fake_sink["plays"] > 1, fake_sink)
    pl.stop()

# ============ 3) VAD: عتبة ديناميكية + PCM ============
v = VadProcessor(EngineConfig())
check("vad default thr", v.speech_threshold() == 40.0)
v.set_noise_floor(30)
check("vad floor thr", v.speech_threshold() == 42.0)
check("vad floor blocks 35dB", not v.analyze_frame(35).is_speech)
check("vad floor passes 45dB", v.analyze_frame(45).is_speech)
silence = struct.pack("<1600h", *([0] * 1600))
check("vad pcm silence", not v.analyze_pcm(silence).is_speech)


# ============ 4) نبرة الترحيب: قفزة + استمرار ============
def greeting_hits(sequence, step=0.1, start=100.0):
    proc = VadProcessor(EngineConfig())
    ts = start
    hits = 0
    for db in sequence:
        if proc.analyze_frame(db, timestamp=ts).is_greeting_tone:
            hits += 1
        ts += step
    return hits


check("greeting fires on onset+hold", greeting_hits([30.0] * 3 + [75.0] * 10) == 1)
check("greeting no fire on steady speech", greeting_hits([60.0] * 30) == 0)
check("greeting no fire in quiet room", greeting_hits([5.0] * 30) == 0)
check(
    "greeting honours cooldown",
    greeting_hits([30.0] * 3 + [75.0] * 6 + [30.0] * 5 + [75.0] * 6) == 1,
)

# ============ 5) الميكروفون: مسارات بلا عتاد ============
m = MicInput()
check("mic has backend attr", hasattr(m, "backend"))
check("mic list_devices type", isinstance(MicInput.list_devices(), list))
m2 = MicInput(device=None)
check(
    "mic stopped read None or float",
    m2.read_db() is None or isinstance(m2.read_db(), float),
)
check("mic calibrate without stream None", m2.calibrate(seconds=0.5) is None)

# ============ 6) الصلاة: لحظة الأذان + تحويل التوقيت ============
pe = PrayerEngine(EngineConfig())
check("prayer duration from config", pe.prayer_duration_min == 20)
check(
    "prayer duration override",
    PrayerEngine(EngineConfig(), prayer_duration_min=30).prayer_duration_min == 30,
)
at = datetime.now().replace(hour=18, minute=10, second=5, microsecond=0)
check("athan_moment maghrib", pe.athan_moment(at) == "maghrib")
idle = datetime.now().replace(hour=10, minute=0, second=0, microsecond=0)
check("athan_moment idle None", pe.athan_moment(idle) is None)

# نافذة الأذان تتحوّل إلى توقيت المضيف
import datetime as _dt

with mock.patch.object(prayer_engine, "host_utc_offset_hours", lambda: 3.0):
    same = [w for w in pe._build_windows(_dt.date(2026, 9, 29)) if w.name == "maghrib"][
        0
    ]
check(
    "window unchanged when host matches site",
    same.mute_start.time().isoformat() == "18:07:00",
)
with mock.patch.object(prayer_engine, "host_utc_offset_hours", lambda: 0.0):
    shifted = [
        w for w in pe._build_windows(_dt.date(2026, 9, 29)) if w.name == "maghrib"
    ][0]
check(
    "window shifts on other host tz",
    shifted.mute_start.time().isoformat() == "15:07:00",
)

# ============ 7) الإعدادات: في مجلد مؤقت لا يُمسّ إعدادات المستخدم ============
with tempfile.TemporaryDirectory() as tmp:
    with mock.patch.object(settings, "writable_path", lambda name: Path(tmp) / name):
        s = settings.load_settings()
        check(
            "settings keys",
            all(k in s for k in ("master_vol", "mic_device", "athan_enabled")),
        )
        s["master_vol"] = 77.0
        check("settings save", settings.save_settings(s))
        check("settings reload", settings.load_settings()["master_vol"] == 77.0)
        check(
            "settings unknown keys dropped", settings.load_settings().keys() == s.keys()
        )
        check("no settings written to user dir", not (Path(tmp) / "leftover").exists())

# ============ 8) مزود المواقيت: بلا شبكة ============
cfg = EngineConfig()
check(
    "provider honours config location",
    cfg.city == "Sanaa" and cfg.country == "Yemen" and cfg.prayer_method == 3,
)
with tempfile.TemporaryDirectory() as tmp:
    cache = Path(tmp) / "prayer_cache.json"

    def fake_path(name):
        return Path(tmp) / name

    # a) لا كاش + فشل الشبكة -> الافتراضي
    with mock.patch.object(prayer_provider, "writable_path", fake_path):
        with mock.patch.object(
            prayer_provider, "fetch_times", side_effect=RuntimeError("offline")
        ):
            result = prayer_provider.load_today_times(cfg)
    check(
        "offline falls back to default", result.source == prayer_provider.SOURCE_DEFAULT
    )
    check(
        "default has 5 prayers", set(result.times) == set(prayer_provider.PRAYER_KEYS)
    )

    # b) كاش اليوم -> لا نداء للشبكة إطلاقاً

    today = _dt.date.today()
    cache.write_text(
        json.dumps(
            {
                "date": today.isoformat(),
                "times": {
                    "fajr": "04:42",
                    "dhuhr": "11:53",
                    "asr": "15:15",
                    "maghrib": "17:54",
                    "isha": "19:01",
                },
            }
        ),
        encoding="utf-8",
    )
    with mock.patch.object(prayer_provider, "writable_path", fake_path):
        with mock.patch.object(prayer_provider, "fetch_times") as fetch:
            result = prayer_provider.load_today_times(cfg)
    check("today cache avoids network", not fetch.called)
    check("today cache source", result.source == prayer_provider.SOURCE_CACHE_TODAY)
    check(
        "cached maghrib parsed",
        result.times["maghrib"].strftime("%H:%M") == "17:54",
    )

    # c) كاش قديم + فشل الشبكة -> مصدر مختلف وصريح
    cache.write_text(
        json.dumps(
            {
                "date": (_dt.date.today() - _dt.timedelta(days=3)).isoformat(),
                "times": {
                    "fajr": "04:40",
                    "dhuhr": "11:50",
                    "asr": "15:10",
                    "maghrib": "17:50",
                    "isha": "19:00",
                },
            }
        ),
        encoding="utf-8",
    )
    with mock.patch.object(prayer_provider, "writable_path", fake_path):
        with mock.patch.object(
            prayer_provider, "fetch_times", side_effect=RuntimeError("offline")
        ):
            result = prayer_provider.load_today_times(cfg)
    check(
        "stale cache marked distinctly",
        result.source == prayer_provider.SOURCE_CACHE_STALE,
    )
    check("stale cache keeps date", result.cached_date is not None)
    check(
        "stale described to user",
        "قديم" in prayer_provider.describe_source(result.source, result.cached_date),
    )

    # d) كاش تالف -> لا انهيار
    cache.write_text("{ not json", encoding="utf-8")
    with mock.patch.object(prayer_provider, "writable_path", fake_path):
        with mock.patch.object(
            prayer_provider, "fetch_times", side_effect=RuntimeError("offline")
        ):
            result = prayer_provider.load_today_times(cfg)
    check("corrupt cache falls back", result.source == prayer_provider.SOURCE_DEFAULT)

# ============ 9) قاعدة الإعدادات: لا أرقام مكتوبة في المحرك ============
from src.context_aware_audio import engine as engine_module

source = Path(engine_module.__file__).read_text(encoding="utf-8")
body = "\n".join(
    line for line in source.splitlines() if not line.strip().startswith("#")
)
check("engine has no inlined activity threshold", "db_level > 5" not in body)
check("engine reads activity threshold from config", "activity_threshold_db" in body)
check("engine uses special_sounds db not literal", 'w["db"]' in body)

# ============ 10) تسجيل الأخطاء (مهم في وضع exe بلا stdout) ============
import threading
import time as _time

from src.context_aware_audio import log_setup

_tmp = tempfile.mkdtemp()
try:
    with mock.patch.object(log_setup, "writable_path", lambda name: Path(_tmp) / name):
        _log = log_setup.install()
        check("log_setup returns path", _log is not None and _log.exists())
        check("log_setup wraps stdout", type(sys.stdout).__name__ == "_TeeWriter")

        print("اختبار stdout")
        print("اختبار stderr", file=sys.stderr)
        sys.stdout.flush()
        sys.stderr.flush()

        try:
            raise ValueError("انهيار محاكى في الخيط الرئيسي")
        except ValueError:
            sys.excepthook(*sys.exc_info())

        def _boom():
            raise RuntimeError("انهيار محاكى في خيط عامل")

        _t = threading.Thread(target=_boom)
        _t.start()
        _t.join()
        _time.sleep(0.2)
        sys.stdout.flush()
        sys.stderr.flush()
        log_setup.close()

    _content = _log.read_text(encoding="utf-8")
    check("log captures stdout", "اختبار stdout" in _content)
    check("log captures stderr", "اختبار stderr" in _content)
    check("log captures main-thread crash", "انهيار محاكى في الخيط الرئيسي" in _content)
    check("log captures worker-thread crash", "انهيار محاكى في خيط عامل" in _content)
    check("log_setup restores stdout", type(sys.stdout).__name__ != "_TeeWriter")
    check("log file released after close", _log.exists() and not _log.is_symlink())
finally:
    import shutil

    shutil.rmtree(_tmp, ignore_errors=True)

# تدوير السجل الكبير
_big = Path(_tmp) / "big.log"
_big.mkdir(parents=True, exist_ok=True)
(_big / "context_audio.log").write_text("x" * (log_setup.MAX_LOG_BYTES + 10))
with mock.patch.object(log_setup, "writable_path", lambda name: Path(_big) / name):
    _rotated = log_setup.install()
    check("oversized log rotated", _rotated is not None)
    log_setup.close()
shutil.rmtree(_big, ignore_errors=True)

# الاستيراد بلا انزلاق: تسجيل الأخطاء لا يجرّ مكتبات ثقيلة
import subprocess

_heavy = subprocess.run(
    [
        sys.executable,
        "-c",
        "import sys; sys.path.insert(0, r'C:\\Users\\ahmed\\work');"
        "import src.context_aware_audio.log_setup;"
        "print([m for m in ('numpy','pygame','sounddevice') if m in sys.modules] or 'NONE')",
    ],
    capture_output=True,
    text=True,
)
check(
    "log_setup imports no audio libs",
    "NONE" in _heavy.stdout,
    _heavy.stdout + _heavy.stderr,
)

# ============ 11) وضع --windowed: stdout ليس مجرى بل None ============
# في الـ exe المجمّع لا يوجد stdout أصلاً. أي كود يفترض وجوده ينهار بصمت،
# وهذا بالضبط ما كان يمنع حلقة التحديث من العمل. الاختبار يحاكي الوضع.
_win = tempfile.mkdtemp()
_real_out, _real_err = sys.stdout, sys.stderr
try:
    sys.stdout = None
    sys.stderr = None
    with mock.patch.object(log_setup, "writable_path", lambda name: Path(_win) / name):
        _wlog = log_setup.install()
        check("windowed: install works with stdout None", _wlog is not None)
        _raised = ""
        try:
            print("وضع النافذة")
        except Exception as _e:
            _raised = repr(_e)
        check("windowed: print() does not raise", not _raised, _raised)
        log_setup.close()
    _wc = _wlog.read_text(encoding="utf-8")
    check("windowed: output still reaches file", "وضع النافذة" in _wc, _wc[:120])
finally:
    sys.stdout, sys.stderr = _real_out, _real_err
    import shutil

    shutil.rmtree(_win, ignore_errors=True)

# ============ 12) دورة حياة الميكروفون على pyaudio ============
# Bug كان يمنع أي إعادة تشغيل بعد إيقاف واحد، ويُبطل الميكروفون نهائياً.
import types as _types


class _FakeStream:
    def read(self, n, **kw):
        return b"\x00" * (n * 2)

    def stop_stream(self):
        pass

    def close(self):
        pass


class _FakePA:
    def __init__(self):
        type(self).created += 1

    def open(self, **kw):
        return _FakeStream()

    def terminate(self):
        type(self).terminated += 1

    def get_device_count(self):
        return 2

    def get_device_info_by_index(self, i):
        return {"maxInputChannels": 1, "name": f"Mic {i}"}


class _FakePyAudio(_FakePA):
    paInt16 = 8
    created = 0
    terminated = 0


def _fake_pyaudio():
    mod = _types.ModuleType("pyaudio")
    mod.PyAudio = _FakePyAudio
    mod.paInt16 = 8  # ثابت على مستوى الوحدة كما في المكتبة الحقيقية
    # الوحدة تحمل terminate أيضاً - هذا ما أخفى الخطأ سابقاً
    mod.terminate = lambda: None
    return mod


def _reset_pa_counters():
    _FakePyAudio.created = 0
    _FakePyAudio.terminated = 0


with mock.patch.dict(sys.modules, {"pyaudio": _fake_pyaudio()}):
    _reset_pa_counters()
    m2 = MicInput()
    m2._sd = None
    m2._pyaudio = sys.modules["pyaudio"]
    m2.backend = "pyaudio"
    m2.available = True
    first = m2.start()
    check("pyaudio: first start succeeds", first, m2.backend)
    m2.stop()
    check("pyaudio: available survives stop", m2.available)
    second = m2.start()
    check("pyaudio: restart after stop succeeds", second)
    check("pyaudio: still available after restart", m2.available, m2.backend)
    m2.stop()
    devs = MicInput._devices_via_pyaudio()
    check("pyaudio: device listing works", len(devs) == 2, devs)
    check("pyaudio: device entries well formed", all(len(d) == 2 for d in devs), devs)
    # تسريب مقابض PortAudio: كل PyAudio() يجب أن يُنهى عند stop
    check(
        "pyaudio: every handle terminated on stop",
        _FakePyAudio.created == _FakePyAudio.terminated,
        f"created={_FakePyAudio.created} terminated={_FakePyAudio.terminated}",
    )
    check("pyaudio: handles were actually created", _FakePyAudio.created >= 2)

# فشل open بعد إنشاء الكائن يجب أن يُنهيه (لا تسرّب)
_reset_pa_counters()
with mock.patch.dict(sys.modules, {"pyaudio": _fake_pyaudio()}):
    m2b = MicInput()
    m2b._sd = None
    m2b._pyaudio = sys.modules["pyaudio"]
    m2b.backend = "pyaudio"
    m2b.available = True
    with mock.patch.object(_FakePA, "open", side_effect=OSError("busy")):
        r2b = m2b.start()
    check("pyaudio: open failure returns False", r2b is False)
    check(
        "pyaudio: failed open still terminates handle",
        _FakePyAudio.created == 1 and _FakePyAudio.terminated == 1,
        f"created={_FakePyAudio.created} terminated={_FakePyAudio.terminated}",
    )

# خيط القراءة: يُنظَّف ولا يتنافس مع خيط جديد
with mock.patch.dict(sys.modules, {"pyaudio": _fake_pyaudio()}):
    m2c = MicInput()
    m2c._sd = None
    m2c._pyaudio = sys.modules["pyaudio"]
    m2c.backend = "pyaudio"
    m2c.available = True
    check("pyaudio: _thread initialised to None", m2c._thread is None)
    m2c.start()
    check("pyaudio: reader thread created", m2c._thread is not None)
    m2c.stop()
    check("pyaudio: reader thread cleared on stop", m2c._thread is None)

# بديل عند تعذّر sounddevice
with mock.patch.dict(sys.modules, {"pyaudio": _fake_pyaudio()}):
    m2d = MicInput()
    m2d.backend = "sounddevice"
    m2d.available = True
    m2d._sd = _types.SimpleNamespace(InputStream=_types.SimpleNamespace())
    with mock.patch.object(m2d, "_start_sounddevice", return_value=False):
        r2d = m2d.start()
    check("mic: falls back to pyaudio when sounddevice fails", r2d is True, m2d.backend)
    check("mic: backend switched to pyaudio", m2d.backend == "pyaudio", m2d.backend)
    m2d.stop()

# فشل التقاط عابر يجب ألّا يُعطّل الجهاز نهائياً
with mock.patch.dict(sys.modules, {"pyaudio": _fake_pyaudio()}):
    m3 = MicInput()
    m3._sd = None
    m3._pyaudio = sys.modules["pyaudio"]
    m3.backend = "pyaudio"
    m3.available = True
    with mock.patch.object(_FakePyAudio, "open", side_effect=OSError("busy")):
        r = m3.start()
    check("pyaudio: transient failure returns False", r is False)
    check("pyaudio: transient failure keeps device available", m3.available)

# ============ 13) الإعدادات: أنواع خاطئة ومجال خارجي ============
_t = tempfile.mkdtemp()
try:
    sp = Path(_t) / "settings.json"
    with mock.patch.object(settings, "writable_path", lambda n: Path(_t) / n):
        sp.write_text(
            json.dumps(
                {
                    "master_vol": "loud",
                    "use_mic": "false",
                    "mic_device": 7,
                    "master_mute": 0,
                    "athan_enabled": None,
                    "injected": "evil",
                }
            ),
            encoding="utf-8",
        )
        s2 = settings.load_settings()
        check("settings: bad float falls back", s2["master_vol"] == 100.0, s2)
        check("settings: string 'false' is False", s2["use_mic"] is False, s2)
        check("settings: int 0 is False", s2["master_mute"] is False, s2)
        check("settings: None falls back", s2["athan_enabled"] is True, s2)
        check("settings: valid int device kept", s2["mic_device"] == 7, s2)
        check("settings: unknown key dropped", "injected" not in s2, s2)

        # المجال المقبول: القيم السابعة تُقصّ إلى المجال (أفصلً أحسن من المتعبّر ضعيفاً)
        for bad, expected in (
            (9999, 100.0),
            (-5, 0.0),
            (float("nan"), 100.0),
            (float("inf"), 100.0),
        ):
            sp.write_text(json.dumps({"master_vol": bad}), encoding="utf-8")
            v = settings.load_settings()["master_vol"]
            check(f"settings: master_vol {bad!r} -> {expected}", v == expected, v)
        for bad in ([], {}, "loud", None, True):
            sp.write_text(json.dumps({"master_vol": bad}), encoding="utf-8")
            v = settings.load_settings()["master_vol"]
            check(f"settings: master_vol {bad!r} -> 100.0", v == 100.0, v)

        sp.write_text(json.dumps({"mic_device": -3}), encoding="utf-8")
        check(
            "settings: negative device -> None",
            settings.load_settings()["mic_device"] is None,
        )
        sp.write_text(json.dumps({"mic_device": "abc"}), encoding="utf-8")
        check(
            "settings: junk device -> None",
            settings.load_settings()["mic_device"] is None,
        )

        check(
            "settings: save coerces too",
            settings.save_settings({"master_vol": "x", "use_mic": "true"}),
        )
        written = json.loads(sp.read_text(encoding="utf-8"))
        check("settings: saved value coerced", written["master_vol"] == 100.0, written)
        check(
            "settings: no temp file left", not (Path(_t) / "settings.json.tmp").exists()
        )
finally:
    import shutil

    shutil.rmtree(_t, ignore_errors=True)

# ============ 14) VAD.reset() يمسح نافذة الترحيب ============
_v = VadProcessor(EngineConfig())
_v.analyze_frame(30.0, timestamp=100.0)
_v.analyze_frame(75.0, timestamp=100.1)  # يرفع النافذة دون إكمال الشرط
check("vad: greeting pending before reset", _v._elevated_since is not None)
_v.reset()
check("vad: reset clears elevation window", _v._elevated_since is None)
check("vad: reset clears peak jump", _v._peak_jump_db == 0.0)
check("vad: reset clears has_prev", _v._has_prev is False)
_fired = _v.analyze_frame(75.0, timestamp=100.5)
check("vad: no greeting right after reset", not _fired.is_greeting_tone)

# أضف noise_floor إلى reset؟ (يُحفظ عمداً - إعداد وليس حالة)
_v2 = VadProcessor(EngineConfig())
_v2.set_noise_floor(25.0)
_v2.reset()
check("vad: reset keeps calibrated floor", _v2.noise_floor_db == 25.0)

# ============ 15) play_once يستخدم المخبأ (لا قراءة قرص) ============
_calls = {"n": 0}


def _counting_sound(path):
    _calls["n"] += 1
    return object()


_fake_pg2, _sink2 = _make_fake_pygame()
_fake_pg2.mixer.Sound = _counting_sound
with mock.patch.dict(sys.modules, {"pygame": _fake_pg2}):
    pl2 = RealPlayer()
    pl2.play_once("athan_chime.wav")
    pl2.play_once("athan_chime.wav")
    check("player: play_once uses sound cache", _calls["n"] == 1, _calls)
    pl2.stop()

# ============ 16) أصول الصوت: كشف الناقص لا ابتلاعه ============
check("assets: none reported missing", missing_assets() == [], missing_assets())

# مجلد للقراءة فقط: التوليد يجب أن يفشل بهدءة لا أن يرمي
_ro = tempfile.mkdtemp()
try:
    with mock.patch(
        "src.context_aware_audio.sound_synth.assets_dir", return_value=Path(_ro)
    ):
        check(
            "assets: empty dir reports all missing",
            len(missing_assets()) == len(BUILDERS),
        )
        # على Windows لا يُمنع chmod للمجلدات، فنسبب الكتابة مباشرة
        with mock.patch(
            "src.context_aware_audio.sound_synth._write_wav",
            side_effect=PermissionError("read-only"),
        ):
            made = ensure_assets(force=False)
        check("assets: read-only dir yields no files", made == [], made[:2])
        check(
            "assets: still reports missing after failure",
            len(missing_assets()) == len(BUILDERS),
        )
finally:
    import shutil

    shutil.rmtree(_ro, ignore_errors=True)

# ============ 17) سيناريوهات: القيمة المعروضة = قيمة الإطار المزروع ============
# كان _scenario_db() يرجع 75.0 ثابتة بينما الإطارات المزروعة 70/62/20/50
import tkinter as _tk

_root = _tk.Tk()
_root.withdraw()
try:
    from src.context_aware_audio.app import DesktopApp

    _app = DesktopApp(_root, log_path=None)
    for kind, expected in (
        ("debate", 70.0),
        ("welcome", 62.0),
        ("silence", 20.0),
        ("talk", 50.0),
    ):
        _app._scenario(kind)
        check(
            f"scenario {kind}: pinned db == {expected:.0f}",
            _app._scenario_db() == expected,
            _app._scenario_db(),
        )
        check(
            f"scenario {kind}: shown db == {expected:.0f}",
            _app._current_db() == expected,
            _app._current_db(),
        )
        _app._scenario_until = 0.0
    check("scenario expires", _app._scenario_db() is None)
    _app.stop()
finally:
    try:
        _root.destroy()
    except Exception:
        pass

# ============ 18) المشغّل: PLAY-FAILED بدل ادّعاء التشغيل ============


def _boom(path):
    raise OSError("missing wav")


_miss_pg, _miss_sink = _make_fake_pygame()
_miss_pg.mixer.Sound = _boom
with mock.patch.dict(sys.modules, {"pygame": _miss_pg}):
    pl3 = RealPlayer()
    bad = PlaybackCommand(
        file="does_not_exist.wav",
        target_db=38,
        volume_ratio=1.0,
        fade_duration_sec=0.1,
        state=EngineState.DAILY_AMBIENT,
        reason="t",
    )
    out = pl3.apply(bad)
    check("player reports PLAY-FAILED for missing asset", "PLAY-FAILED" in out, out)
    pl3.stop()

# مسار winsound: أصل مفقود يجب ألا يُعلن PLAY
pl4 = RealPlayer()
pl4._pg = None
pl4._winsound = types.SimpleNamespace(PlaySound=lambda *a, **k: None)
pl4.backend = "winsound"
out4 = pl4.apply(bad)
check("winsound reports PLAY-FAILED for missing asset", "PLAY-FAILED" in out4, out4)
pl4.stop()

# ============ 19) هروب مسار bat والتحقق من الملكية ============
from src.context_aware_audio import app as app_mod

check("bat escapes percent", "%%TEMP%%" in app_mod._escape_bat_text("C:\a%TEMP%b"))
_esc = app_mod._escape_bat_text("C:\a&b^d|e<f>g!h")
for _ch in ("&", "|", "<", ">", "!"):
    check(f"bat escapes {_ch!r}", "^" + _ch in _esc, _esc)
check(
    "bat arg wraps in real quotes",
    app_mod._bat_arg(r"C:\x") == '"' + r"C:\x" + '"',
    app_mod._bat_arg(r"C:\x"),
)
check(
    "bat arg does not escape its own quotes", "^" + '"' not in app_mod._bat_arg(r"C:\x")
)
check("bat marker present", "context-aware-audio-autostart" in app_mod.AUTOSTART_MARKER)
check(
    "launch command uses sys.executable",
    app_mod._launch_command()[0] == sys.executable,
    app_mod._launch_command(),
)
check(
    "launch command is raw (no pre-quoted args)",
    all(not a.startswith('"') for a in app_mod._launch_command()),
    app_mod._launch_command(),
)

_bt = Path(tempfile.mkdtemp()) / "x.bat"
_bt.write_text("@echo off\nsomething else\n", encoding="utf-8")
check("foreign bat not claimed", not app_mod._is_our_bat(_bt))
_bt.write_text(f"@echo off\n{app_mod.AUTOSTART_MARKER}\n", encoding="utf-8")
check("our bat recognised", app_mod._is_our_bat(_bt))
import shutil

shutil.rmtree(_bt.parent, ignore_errors=True)

# ============ 20) مسارات: ملف مؤقت فريد ============
from src.context_aware_audio.paths import atomic_write

_t2 = tempfile.mkdtemp()
try:
    tgt = Path(_t2) / "f.json"
    atomic_write(tgt, '{"a":1}')
    check("atomic write creates target", tgt.read_text(encoding="utf-8") == '{"a":1}')
    leftovers = [p.name for p in Path(_t2).iterdir() if p.name != "f.json"]
    check("atomic write leaves no temp", leftovers == [], leftovers)
    atomic_write(tgt, '{"a":2}')
    check(
        "atomic write overwrites", json.loads(tgt.read_text(encoding="utf-8"))["a"] == 2
    )
    leftovers = [p.name for p in Path(_t2).iterdir() if p.name != "f.json"]
    check("no temp after overwrite", leftovers == [], leftovers)
finally:
    import shutil

    shutil.rmtree(_t2, ignore_errors=True)

check("settings rejects non-dict", settings.save_settings("nope") is False)
check("settings rejects None", settings.save_settings(None) is False)

print(f"\nRESULT: {PASSED} passed / {FAILED} failed")
sys.exit(1 if FAILED else 0)
