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
from src.context_aware_audio.real_player import HISTORY_LIMIT, RealPlayer
from src.context_aware_audio.sound_synth import (
    BUILDERS,
    ensure_assets,
    missing_assets,
)
from src.context_aware_audio.paths import assets_dir
from src.context_aware_audio.vad import VadProcessor

PASSED = FAILED = 0


# حجم settings.json الحقيقي قبل أي اختبار: يقارنه الحارس في النهاية
_real_settings_path = assets_dir() / "settings.json"
_real_size = _real_settings_path.stat().st_size if _real_settings_path.exists() else 0


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
check("vad floor blocks 35dB", not v.analyze_frame(35).is_speech_raw)
_f45 = v.analyze_frame(45, timestamp=300.0)
check("vad floor passes 45dB (raw)", _f45.is_speech_raw, str(_f45))
_f45b = v.analyze_frame(45, timestamp=300.2)
check("vad floor passes 45dB (confirmed)", _f45b.is_speech, str(_f45b))
silence = struct.pack("<1600h", *([0] * 1600))
check("vad pcm silence", not v.analyze_pcm(silence).is_speech_raw)
check(
    "vad pcm silence on a clean processor",
    not VadProcessor(EngineConfig()).analyze_pcm(silence).is_speech,
)


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

# اختيار الجهاز يصل إلى pyaudio أيضاً
with mock.patch.dict(sys.modules, {"pyaudio": _fake_pyaudio()}):
    _opened = {}

    def _capture(**kw):
        _opened.update(kw)
        return _FakeStream()

    _reset_pa_counters()
    m2e = MicInput()
    m2e._sd = None
    m2e._pyaudio = sys.modules["pyaudio"]
    m2e.backend = "pyaudio"
    m2e.available = True
    with mock.patch.object(_FakePA, "open", side_effect=_capture):
        m2e.start()
    check(
        "pyaudio: device selection honoured",
        _opened.get("input_device_index") is None,
        _opened,
    )
    m2e.stop()

    _opened.clear()
    m2e.device = 3
    with mock.patch.object(_FakePA, "open", side_effect=_capture):
        m2e.start()
    check(
        "pyaudio: chosen device passed to open()",
        _opened.get("input_device_index") == 3,
        _opened,
    )
    m2e.stop()

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
# نحتاج تأكيد الكلام أولاً (0.2ث) قبل أن تُفتح نافذة الترحيب
_v.analyze_frame(75.0, timestamp=100.1)
_v.analyze_frame(75.0, timestamp=100.2)
_v.analyze_frame(75.0, timestamp=100.3)  # يرفع النافذة دون إكمال الشرط
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
        # القيمة المثمَّنة تُقرأ من الحقل لا من _scenario_db(): الأخيرة
        # تفحص مهلة زمنية، فعلى تشغيل بارد (ترجمة الشيفرة أول مرة) قد
        # تنقضي الثواني الثلاث قبل السطر التالي ويفشل اختبار سليم.
        check(
            f"scenario {kind}: pinned db == {expected:.0f}",
            _app._scenario_db_value == expected,
            _app._scenario_db_value,
        )
        # ثم نجعل التثبيت سارياً قطعاً لاختبار مسار القراءة
        _app._scenario_until = _time.time() + 60
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

# أصل تالف (لا ناقص): pygame.error يرث RuntimeError لا OSError، وwave.Error
# ليس فرعاً منه. هذا هو ما فوّته تضييق except السابق.
_corrupt = assets_dir() / "corrupt_probe.wav"
_corrupt.write_bytes(b"RIFFnotreallyawavefile" * 30)
try:
    import pygame as _real_pg  # noqa: F401  (اختياري)

    try:
        _real_pg.mixer.Sound(str(_corrupt))
        _sound_raises = None
    except Exception as _e:
        _sound_raises = _e
    check(
        "corrupt asset: pygame raises a non-OSError",
        _sound_raises is not None
        and not isinstance(_sound_raises, (OSError, ValueError)),
        f"{type(_sound_raises).__name__}",
    )

    # عبر المشغّل الحقيقي: يجب PLAY-FAILED لا استثناء هارب
    with mock.patch.dict(sys.modules, {"pygame": _real_pg}):
        pl5 = RealPlayer()
        bad5 = PlaybackCommand(
            file=_corrupt.name,
            target_db=38,
            volume_ratio=1.0,
            fade_duration_sec=0.1,
            state=EngineState.DAILY_AMBIENT,
            reason="t",
        )
        out5 = pl5.apply(bad5)
        check(
            "corrupt asset: reports PLAY-FAILED, does not raise",
            "PLAY-FAILED" in out5,
            out5,
        )
        check(
            "corrupt asset: current_file not advanced (no retry loop)",
            pl5.current_file != _corrupt.name,
            pl5.current_file,
        )
        # لا إغراق في السجل: يُبلَّغ مرة ثم يُحاول مرة واحدة
        again5 = pl5.apply(bad5)
        check(
            "corrupt asset: second apply does not re-log failure",
            "PLAY-FAILED" not in again5,
            again5,
        )
        check(
            "corrupt asset: second apply says skipped",
            "PLAY-SKIPPED" in again5,
            again5,
        )
        check(
            "corrupt asset: still quarantined",
            _corrupt.name in pl5._broken_assets,
            pl5._broken_assets,
        )
        # تغيّر الملف على القرص يُسمح بإعادة المحاولة
        _corrupt.write_bytes(b"RIFFstillbrokenfile" * 40)
        third5 = pl5.apply(bad5)
        check(
            "corrupt asset: changed file is retried",
            "PLAY-FAILED" in third5,
            third5,
        )
        check(
            "corrupt asset: state never advances to a dead file",
            pl5.current_file != _corrupt.name,
            pl5.current_file,
        )
        pl5.stop()
except Exception as e:  # pygame غير متاح في بيئة الاختبار
    check("corrupt asset path exercised", False, repr(e))
finally:
    _corrupt.unlink(missing_ok=True)

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

# ============ 19) محاكاة الوقت ============
from src.context_aware_audio.sim_clock import SimClock, MAX_OFFSET_SEC

_c1 = SimClock()
check("sim: disabled at construction", _c1.enabled is False)
check(
    "sim: now() follows real when disabled",
    abs((_c1.now() - _c1.real_now()).total_seconds()) < 1.0,
)
_c1._enabled = True
_c1.set_hhmmss(2, 0)
check("sim: now() is faked when enabled", _c1.now().hour == 2, _c1.now())
check(
    "sim: real_now() unaffected by enable",
    abs((_c1.real_now() - datetime.now()).total_seconds()) < 1.0,
)
_before = _c1.offset_sec
_c1.nudge(-1)
check(
    "sim: nudge(-1) shifts -3600s",
    abs((_c1.offset_sec - _before) + 3600) < 2,
    _c1.offset_sec - _before,
)
_c1.nudge(1)
_c1.reset()
check("sim: reset clears enabled", _c1.enabled is False)
check("sim: reset clears offset", _c1.offset_sec == 0.0)
_c2 = SimClock(offset_sec=999_999)
check("sim: offset clamped to a day", _c2.offset_sec == MAX_OFFSET_SEC, _c2.offset_sec)
_c2.offset_sec = -999_999
check("sim: negative offset clamped", _c2.offset_sec == -MAX_OFFSET_SEC, _c2.offset_sec)

# الإزاحة عبر الإعدادات
_ts3 = tempfile.mkdtemp()
try:
    with mock.patch.object(settings, "writable_path", lambda n: Path(_ts3) / n):
        (Path(_ts3) / "settings.json").write_text(
            json.dumps({"sim_offset_sec": -7200.5}), encoding="utf-8"
        )
        check(
            "sim: offset loaded from settings",
            settings.load_settings()["sim_offset_sec"] == -7200.5,
        )
        (Path(_ts3) / "settings.json").write_text(
            json.dumps({"sim_offset_sec": 10**9}), encoding="utf-8"
        )
        check(
            "sim: offset clamped on load",
            settings.load_settings()["sim_offset_sec"] == MAX_OFFSET_SEC,
        )
        (Path(_ts3) / "settings.json").write_text(
            json.dumps({"sim_offset_sec": True}), encoding="utf-8"
        )
        check(
            "sim: bool offset rejected",
            settings.load_settings()["sim_offset_sec"] == 0.0,
        )
        (Path(_ts3) / "settings.json").write_text(
            json.dumps({"sim_offset_sec": "abc"}), encoding="utf-8"
        )
        check(
            "sim: junk offset rejected",
            settings.load_settings()["sim_offset_sec"] == 0.0,
        )
        check(
            "sim: offset round-trips",
            settings.save_settings({"sim_offset_sec": 3600.0})
            and settings.load_settings()["sim_offset_sec"] == 3600.0,
        )
finally:
    import shutil

    shutil.rmtree(_ts3, ignore_errors=True)

# --- انحدار: قفزة الساعة لا تُطلق كتم النقاش ---
from datetime import time as dtime
from src.context_aware_audio import ContextAwareAudioEngine
from src.context_aware_audio.audio_types import AudioFrame

_eng = ContextAwareAudioEngine(EngineConfig())
_eng.prayer.set_times(
    {
        "fajr": dtime(5, 10),
        "dhuhr": dtime(12, 5),
        "asr": dtime(15, 25),
        "maghrib": dtime(18, 10),
        "isha": dtime(19, 30),
    }
)
_day = datetime.now().replace(hour=15, minute=0, second=0, microsecond=0)
_real_t0 = 1_000_000.0
_eng.process_frame(
    AudioFrame(timestamp=_real_t0, db_level=70, is_speech=True, is_overlapping=True),
    _day,
)
_eng.process_frame(
    AudioFrame(timestamp=_real_t0 + 20, db_level=30, is_speech=False), _day
)
_still = _eng.process_frame(
    AudioFrame(timestamp=_real_t0 + 20, db_level=30, is_speech=False),
    _day.replace(hour=2),
)
check(
    "sim: 13h clock jump does not release the debate mute",
    _still.state == EngineState.DEBATE_MUTED,
    _still,
)


# --- انحدار: القفزة لا تمسّ حالة المحرك أو مؤقّت الخمول ---
import tkinter as tk
import time as _time

# هذا التطبيق ينشئ كائنات حقيقية تحفظ إعداداتها. بدون عزل المسار
# يكتب الاختبار إلى settings.json الحقيقي، وهو ملف مُتجاهَل في git
# فلا يظهر التغيير في git status بينما يفسد إعدادات المستخدم فعلياً.
_sim_home = tempfile.mkdtemp()
try:
    with mock.patch.object(settings, "writable_path", lambda n: Path(_sim_home) / n):
        _root2 = tk.Tk()
        _root2.withdraw()
        try:
            from src.context_aware_audio.app import DesktopApp

            _app2 = DesktopApp(_root2, log_path=None)
            _app2.manual_db.set(2.0)
            _app2.start()
            for _ in range(6):
                _root2.update()
                _time.sleep(0.1)
            _act_before = _app2.engine._last_activity_time
            _stamp_before = _app2.engine._last_speech_time

            _app2.sim_on.set(True)
            _app2._on_sim_toggle()
            _app2.clock.set_hhmmss(2, 0)
            for _ in range(6):
                _root2.update()
                _time.sleep(0.1)

            check(
                "sim: activity timer untouched by the jump",
                _app2.engine._last_activity_time == _act_before,
                f"{_act_before} -> {_app2.engine._last_activity_time}",
            )
            check(
                "sim: speech timer untouched by the jump",
                _app2.engine._last_speech_time == _stamp_before,
            )

            # سيناريو تحت ساعة محاكاة لا يضع طابعاً في المستقبل
            _app2._scenario("talk")
            _now_real = _app2.clock.real_now().timestamp()
            _drift = abs(_app2.engine._last_activity_time - _now_real)
            check(
                "sim: scenario stamps stay on the real timeline",
                _drift < 30,
                f"drift={_drift:.1f}s",
            )

            # الانتقال بين الفترات
            for hour, expect in (
                (2, "\u0627\u0644\u0644\u064a\u0644"),
                (15, "\u0627\u0644\u0645\u0642\u064a\u0644"),
            ):
                _app2.clock.set_hhmmss(hour, 0)
                for _ in range(6):
                    _root2.update()
                    _time.sleep(0.1)
                check(
                    f"sim: {hour:02d}:00 shows {expect}",
                    expect in _app2.period_text.get(),
                    _app2.period_text.get(),
                )

            # دقيقة الأذان الحقيقية بعد التحويل
            _app2.clock.set_hhmmss(18, 10)
            for _ in range(8):
                _root2.update()
                _time.sleep(0.1)
            check(
                "sim: 18:10 mutes for maghrib",
                "prayer_muted" in _app2.state_text.get(),
                _app2.state_text.get(),
            )

            # عناصر اللوحة موجودة
            check(
                "sim: panel widgets exist",
                all(
                    hasattr(_app2, n)
                    for n in ("sim_on", "sim_readout", "sim_hour", "sim_minute")
                ),
            )
            _app2._on_sim_reset()
            check("sim: reset disables the box", _app2.clock.enabled is False)
            check("sim: reset clears the offset", _app2.clock.offset_sec == 0.0)
            check("sim: reset clears the checkbox", _app2.sim_on.get() is False)

            # الحقول تكتب الساعة
            _app2.sim_on.set(True)
            _app2._on_sim_toggle()
            _app2.sim_hour.set(3)
            for _ in range(3):
                _root2.update()
                _time.sleep(0.05)
            check(
                "sim: hour field drives the clock",
                _app2.clock.now().hour == 3,
                _app2.clock.now(),
            )
            check(
                "sim: readout shows both clocks",
                "\u0627\u0644\u0645\u062d\u0627\u0643\u0649" in _app2.sim_readout.get()
                and "\u0627\u0644\u062d\u0642\u064a\u0642\u064a"
                in _app2.sim_readout.get(),
                _app2.sim_readout.get(),
            )

            # الخيار يُحفظ ويعود
            _app2._save_settings()
            check(
                "sim: offset persisted in settings snapshot",
                "sim_offset_sec" in _app2._settings_snapshot(),
                _app2._settings_snapshot().keys(),
            )
            _app2.stop()
        finally:
            try:
                _root2.destroy()
            except Exception:
                pass
finally:
    import shutil

    shutil.rmtree(_sim_home, ignore_errors=True)


# ============ 20) عزل أعطال التشغيل + واجهة الساعة ============
from src.context_aware_audio.sim_clock import SimClock

# واجهة عامة بدل الكتابة على _enabled من الخارج
_cx = SimClock()
_cx.set_enabled(True)
check("sim: set_enabled is public and works", _cx.enabled is True)
_cx.set_enabled(False)
check("sim: set_enabled(False) disables", _cx.enabled is False)
check(
    "sim: clamp uses finite check", SimClock(offset_sec=float("nan")).offset_sec == 0.0
)
check("sim: clamp rejects inf", SimClock(offset_sec=float("inf")).offset_sec == 0.0)

# حالة التفعيل تُحفظ: إزاحة بلا علم بها لا تقفز عند أول ضغطة
_t20 = tempfile.mkdtemp()
try:
    with mock.patch.object(settings, "writable_path", lambda n: Path(_t20) / n):
        (Path(_t20) / "settings.json").write_text(
            json.dumps({"sim_offset_sec": -7200.0, "sim_enabled": True}),
            encoding="utf-8",
        )
        _s20 = settings.load_settings()
        check("sim: enabled flag persisted", _s20["sim_enabled"] is True, _s20)
        check(
            "sim: offset persisted alongside", _s20["sim_offset_sec"] == -7200.0, _s20
        )
        (Path(_t20) / "settings.json").write_text(
            json.dumps({"sim_enabled": "false"}), encoding="utf-8"
        )
        check(
            "sim: string 'false' means off",
            settings.load_settings()["sim_enabled"] is False,
        )
        (Path(_t20) / "settings.json").write_text(
            json.dumps({"sim_enabled": "true"}), encoding="utf-8"
        )
        check(
            "sim: string 'true' means on",
            settings.load_settings()["sim_enabled"] is True,
        )
finally:
    import shutil

    shutil.rmtree(_t20, ignore_errors=True)

# خطأ التشغيل لا يوقف قراءة الواجهة
_root3 = tk.Tk()
_root3.withdraw()
try:
    from src.context_aware_audio.app import DesktopApp

    _app3 = DesktopApp(_root3, log_path=None)
    _app3.manual_db.set(30.0)
    _app3.start()
    for _ in range(8):
        _root3.update()
        _time.sleep(0.1)
    _before3 = _app3.period_text.get()
    with mock.patch.object(_app3.player, "apply", side_effect=RuntimeError("boom")):
        for _ in range(8):
            _root3.update()
            _time.sleep(0.1)
    _after3 = _app3.period_text.get()
    check(
        "player fault does not freeze the readouts",
        _after3 != "-" and bool(_after3),
        _after3,
    )
    check("player fault logged once, not per tick", _app3._player_error_logged is True)
    _app3.stop()
finally:
    try:
        _root3.destroy()
    except Exception:
        pass

# الملف الناقص لا يغرق السجل على واجهة winsound أيضاً
_missing_cmd = PlaybackCommand(
    file="no_such_asset_probe.wav",
    target_db=38,
    volume_ratio=1.0,
    fade_duration_sec=0.1,
    state=EngineState.DAILY_AMBIENT,
    reason="t",
)
_pl6 = RealPlayer()
_pl6._pg = None
_pl6._winsound = types.SimpleNamespace(PlaySound=lambda *a, **k: None)
_pl6.backend = "winsound"
_first6 = _pl6.apply(_missing_cmd)
_second6 = _pl6.apply(_missing_cmd)
_third6 = _pl6.apply(_missing_cmd)
check("winsound: first attempt reports the failure", "PLAY-FAILED" in _first6, _first6)
check("winsound: no repeat failure", "PLAY-FAILED" not in _second6, _second6)
check("winsound: later attempts skip", "PLAY-SKIPPED" in _third6, _third6)
check("winsound: quarantined", "no_such_asset_probe.wav" in _pl6._broken_assets)
_pl6.stop()

# السجل محدود الطول
_pl7 = RealPlayer()
for _ in range(HISTORY_LIMIT + 250):
    _pl7.apply(
        PlaybackCommand(
            file="water_stream.wav",
            target_db=38,
            volume_ratio=1.0,
            fade_duration_sec=0.0,
            state=EngineState.DAILY_AMBIENT,
            reason="t",
        )
    )
check(
    "player: history is bounded", len(_pl7.history) <= HISTORY_LIMIT, len(_pl7.history)
)
_pl7.stop()


# ============ 21) قراءة خام/مؤكد في الواجهة ============
# أثناء فلتر الثبات يختلف المؤشران، وهذا ما يفسر تأخر 200ms.
# إن أخفيناه بدا الكتم غير مبرَّر عند ضبط أي عتبة.
_r4 = tk.Tk()
_r4.withdraw()
try:
    from src.context_aware_audio.app import DesktopApp

    _a4 = DesktopApp(_r4, log_path=None)
    _cmd4 = PlaybackCommand(
        file=None,
        target_db=0,
        volume_ratio=0.0,
        fade_duration_sec=0.0,
        state=EngineState.DUCKED,
        reason="t",
    )
    _now4 = _a4.clock.now()
    for _raw, _conf, _want in (
        (True, True, False),
        (True, False, True),
        (False, True, True),
        (False, False, False),
    ):
        _a4._update_readouts(_now4, _cmd4, 50.0, _conf, _raw)
        _txt4 = _a4.db_label.cget("text")
        check(
            f"readout raw={int(_raw)} confirmed={int(_conf)} shows raw flag",
            ("\u062e\u0627\u0645=" in _txt4) == _want,
            _txt4,
        )
    # المؤشران متفقان فلا فائدة من عرض الخام
    _a4._update_readouts(_now4, _cmd4, 50.0, True, True)
    check(
        "readout omits the flag when both agree",
        "\u062e\u0627\u0645=" not in _a4.db_label.cget("text"),
        _a4.db_label.cget("text"),
    )
    _a4.stop()
finally:
    try:
        _r4.destroy()
    except Exception:
        pass


# ============ 22) حارس: الاختبارات لا تكتب إلى إعدادات المستخدم ============
# هذا الملف مُتجاهَل في git، فالكتابة إليه لا تُظهر شيئاً في git status
# لكنها تُفسد إعدادات المستخدم فعلياً. أوضحها: محاكاة الساعة مفعّلة تلقائياً.
# أي اختبار ينشئ DesktopApp ويحفظ يجب أن يغلّف settings.writable_path.
_real_settings = assets_dir() / "settings.json"
check(
    "guard: real settings.json untouched by the suite",
    not _real_settings.exists() or _real_settings.stat().st_size == _real_size,
    f"size={_real_settings.stat().st_size if _real_settings.exists() else 0}"
    f" expected={_real_size}",
)

# ============ 23) تعديل حقل الساعة يفعّل المحاكاة ============
# تركها معطّلة كان يعني تخزين إزاحة صامتة تُطبَّق عند أول ضغطة على
# المربع لاحقاً: مخاطرة الظهور فجأة بلا سبب ظاهر للمستخدم.
_sim_home2 = tempfile.mkdtemp()
try:
    with mock.patch.object(settings, "writable_path", lambda n: Path(_sim_home2) / n):
        _r5 = tk.Tk()
        _r5.withdraw()
        try:
            from src.context_aware_audio.app import DesktopApp

            _a5 = DesktopApp(_r5, log_path=None)
            check("sim: starts disabled", _a5.clock.enabled is False)
            _a5.sim_hour.set(2)
            for _ in range(4):
                _r5.update()
                _time.sleep(0.05)
            check(
                "sim: editing the field enables simulation", _a5.clock.enabled is True
            )
            check("sim: checkbox follows", _a5.sim_on.get() is True)
            check(
                "sim: the edit took effect", _a5.clock.now().hour == 2, _a5.clock.now()
            )

            _a5._on_sim_reset()
            check("sim: reset disables", _a5.clock.enabled is False)
            check("sim: reset zeroes offset", _a5.clock.offset_sec == 0.0)
            _a5.sim_hour.set(5)
            for _ in range(4):
                _r5.update()
                _time.sleep(0.05)
            check("sim: a second edit re-enables", _a5.clock.enabled is True)
            check(
                "sim: second edit applied", _a5.clock.now().hour == 5, _a5.clock.now()
            )
            _a5.stop()
        finally:
            try:
                _r5.destroy()
            except Exception:
                pass
finally:
    import shutil

    shutil.rmtree(_sim_home2, ignore_errors=True)

# ============ 24) عطل التشغيل يظهر في القراءة لا في السجل وحده ============
_fault_home = tempfile.mkdtemp()
try:
    with mock.patch.object(settings, "writable_path", lambda n: Path(_fault_home) / n):
        _r6 = tk.Tk()
        _r6.withdraw()
        try:
            _a6 = DesktopApp(_r6, log_path=None)
            _a6.manual_db.set(30.0)
            _a6.start()
            for _ in range(6):
                _r6.update()
                _time.sleep(0.1)
            _cmd6 = PlaybackCommand(
                file=None,
                target_db=0,
                volume_ratio=0.0,
                fade_duration_sec=0.0,
                state=EngineState.DAILY_AMBIENT,
                reason="t",
            )
            _a6._update_readouts(_a6.clock.now(), _cmd6, 30.0, False, False)
            check(
                "readout clean before the fault",
                "بلا صوت" not in _a6.state_text.get(),
                _a6.state_text.get()[:50],
            )
            _a6._player_error = "boom"
            _a6._update_readouts(_a6.clock.now(), _cmd6, 30.0, False, False)
            check(
                "readout warns when nothing is playing",
                "بلا صوت" in _a6.state_text.get(),
                _a6.state_text.get()[:50],
            )
            check(
                "readout keeps the engine state visible",
                "daily_ambient" in _a6.state_text.get(),
                _a6.state_text.get()[:70],
            )
            _a6._player_error = ""
            _a6.stop()
        finally:
            try:
                _r6.destroy()
            except Exception:
                pass
finally:
    import shutil

    shutil.rmtree(_fault_home, ignore_errors=True)


# ============ 25) presets عمق الخفض + تصفير حالة السيناريو ============
_pres_home = tempfile.mkdtemp()
try:
    with mock.patch.object(
        settings, "writable_path", lambda n: Path(_pres_home) / n
    ):
        _r7 = tk.Tk()
        _r7.withdraw()
        try:
            from src.context_aware_audio.app import (
                DUCK_PRESETS,
                DUCK_PRESET_LABELS,
                DesktopApp,
            )

            _a7 = DesktopApp(_r7, log_path=None)
            check("presets: three depths are offered", len(DUCK_PRESETS) == 3,
                  DUCK_PRESETS)
            check("presets: none promises 90%",
                  all(d < 90.0 for d in DUCK_PRESETS), DUCK_PRESETS)
            check("presets: every depth is labelled",
                  all(d in DUCK_PRESET_LABELS for d in DUCK_PRESETS),
                  DUCK_PRESET_LABELS)
            check("presets: the scale exists", hasattr(_a7, "duck_depth"))
            check("presets: the scale feeds the engine config",
                  _a7.config.duck_depth == _a7.duck_depth.get(),
                  (_a7.config.duck_depth, _a7.duck_depth.get()))

            _a7._on_duck_preset(80.0)
            check("presets: choosing a depth updates the engine",
                  abs(_a7.config.duck_depth - 80.0) < 1e-9, _a7.config.duck_depth)
            check("presets: the derived max cut follows",
                  abs((1 - _a7.engine.duck_max_ratio()) * 100 - 80.0) < 0.01,
                  _a7.engine.duck_max_ratio())
            check("presets: the depth is in the snapshot",
                  _a7._settings_snapshot()["duck_depth"] == 80.0)
            _a7._on_duck_preset(60.0)
            check("presets: switching back works",
                  abs(_a7.config.duck_depth - 60.0) < 1e-9, _a7.config.duck_depth)

            # زر السيناريو يصفّر الحالة المثبّتة
            _a7.manual_db.set(30.0)
            _a7.start()
            for _ in range(5):
                _r7.update()
                _time.sleep(0.1)
            _a7._scenario("debate")
            check("scenario: debate latches the mute",
                  _a7.engine._in_debate_mute is True)
            _a7._scenario("talk")
            check("scenario: the next button clears the latch",
                  _a7.engine._in_debate_mute is False,
                  _a7.engine._in_debate_mute)
            _a7._scenario("debate")
            _a7._scenario("silence")
            check("scenario: silence also clears the latch",
                  _a7.engine._in_debate_mute is False)
            _a7.stop()
        finally:
            try:
                _r7.destroy()
            except Exception:
                pass
finally:
    import shutil

    shutil.rmtree(_pres_home, ignore_errors=True)

# مفتاح عمق الخفض يُنقّى كما بقيّة المفاتيح
_co_home = tempfile.mkdtemp()
try:
    with mock.patch.object(settings, "writable_path", lambda n: Path(_co_home) / n):
        for raw, want in (
            (50.0, 50.0),
            (95.0, 95.0),
            (1e9, 95.0),
            (-5.0, 0.0),
            (True, 70.0),
            ("abc", 70.0),
            (None, 70.0),
        ):
            (Path(_co_home) / "settings.json").write_text(
                json.dumps({"duck_depth": raw}), encoding="utf-8"
            )
            got = settings.load_settings()["duck_depth"]
            check(f"settings: duck_depth {raw!r} -> {want}", got == want, got)
finally:
    import shutil

    shutil.rmtree(_co_home, ignore_errors=True)


print(f"\nRESULT: {PASSED} passed / {FAILED} failed")
sys.exit(1 if FAILED else 0)
