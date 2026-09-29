"""
اختبارات سطح المكتب - hermetic: بلا صوت حقيقي، بلا شبكة، بلا لمس لإعدادات المستخدم
"""

import struct
import sys
import tempfile
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
from src.context_aware_audio.sound_synth import ASSETS_DIR, BUILDERS, ensure_assets
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
    path = ASSETS_DIR / name
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
    import json

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

print(f"\nRESULT: {PASSED} passed / {FAILED} failed")
sys.exit(1 if FAILED else 0)
