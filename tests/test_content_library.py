"""content_library.py: الفحص وقياس المدة."""

import shutil
import sys
import tempfile
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.context_aware_audio.config import EngineConfig
from src.context_aware_audio.content_library import (
    ContentLibrary,
    content_id_for,
    measure_duration,
    parse_name,
)
from src.context_aware_audio.content_store import ContentStore

PASSED = FAILED = 0


def check(name, cond, extra=""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"PASS {name}")
    else:
        FAILED += 1
        print(f"FAIL {name}  {extra}")


def write_wav(path, seconds=2.0, rate=8000):
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = int(seconds * rate)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * frames)
    return path


cfg = EngineConfig()
keys = list(cfg.content_windows.keys())

# ---------- تفسير الاسم ----------
w, s, t = parse_name("maqil_story__001__قصة_آدم.mp3", keys)
check("name: window parsed", w == "maqil_story", w)
check("name: sequence parsed", s == 1, s)
check("name: title parsed", t == "قصة_آدم", t)

w, s, t = parse_name("duha_wisdom__حكمة_الرفق.mp3", keys)
check("name: no sequence is None", s is None, s)
check("name: title without sequence", t == "حكمة_الرفق", t)

w, s, t = parse_name("random_song.mp3", keys)
check("name: unknown window falls back", w == "duha_wisdom", w)
check("name: fallback has no sequence", s is None, s)
check("name: fallback keeps the stem", t == "random_song", t)

# ---------- المعرّف ----------
check(
    "id: stable for the same path",
    content_id_for("a/b.mp3") == content_id_for("a/b.mp3"),
)
check(
    "id: separator-independent", content_id_for("a\\b.mp3") == content_id_for("a/b.mp3")
)
check("id: case-independent", content_id_for("a/B.mp3") == content_id_for("a/b.mp3"))
check("id: different paths differ", content_id_for("a.mp3") != content_id_for("b.mp3"))

# ---------- قياس المدة ----------
tmp = Path(tempfile.mkdtemp())
wav = write_wav(tmp / "tone.wav", seconds=3.0, rate=8000)
dur = measure_duration(wav)
check("measure: wav duration is 3s", dur is not None and abs(dur - 3.0) < 0.01, dur)
check("measure: unknown suffix is None", measure_duration(tmp / "x.xyz") is None)

bad = tmp / "broken.wav"
bad.write_bytes(b"RIFFnotawaveatall" * 20)
check("measure: corrupt wav is None, not an exception", measure_duration(bad) is None)

# ---------- الفحص ----------
lib_dir = tmp / "library"
write_wav(lib_dir / "maqil_story__001__first.wav", 4.0)
write_wav(lib_dir / "maqil_story__002__second.wav", 5.0)
write_wav(lib_dir / "evening_ethic__hadith_one.wav", 6.0)
(lib_dir / "notes.txt").write_text("ignored", encoding="utf-8")
(lib_dir / "sub").mkdir()
write_wav(lib_dir / "sub" / "duha_wisdom__nested.wav", 7.0)

db = tmp / "content.db"
with ContentStore(db) as store:
    lib = ContentLibrary(lib_dir, store, cfg)
    rep = lib.scan()
    check("scan: three files entered", rep.added == 4, rep.added)
    check(
        "scan: unsupported skipped",
        any("notes.txt" in x for x in rep.skipped),
        rep.skipped,
    )
    check("scan: store agrees", store.count() == 4, store.count())

    rows = {r["file_path"]: r for r in store.all_content()}
    first = rows["maqil_story__001__first.wav"]
    check(
        "scan: duration stored",
        abs(first["duration_sec"] - 4.0) < 0.01,
        first["duration_sec"],
    )
    check("scan: sequence stored", first["sequence_index"] == 1)
    check("scan: window stored", first["window_key"] == "maqil_story")
    check("scan: kind from the window spec", first["kind"] == "story", first["kind"])
    nested = rows["sub/duha_wisdom__nested.wav"]
    check("scan: subfolders included", nested is not None)
    check("scan: unsequenced stays None", nested["sequence_index"] is None)
    check(
        "scan: relative path stored",
        first["file_path"] == "maqil_story__001__first.wav",
    )

    # إعادة الفحص بلا تغيير: لا صفوف جديدة، ولا إعادة قياس
    rep2 = lib.scan()
    check("rescan: nothing new", rep2.added == 0, rep2.added)
    check("rescan: still four files", store.count() == 4, store.count())

    # تغيير مدة الملف: يُعاد القياس
    write_wav(lib_dir / "maqil_story__001__first.wav", 9.0)
    rep3 = lib.scan()
    check(
        "rescan: changed file is re-measured",
        abs(
            store.get(content_id_for("maqil_story__001__first.wav"))["duration_sec"]
            - 9.0
        )
        < 0.01,
        store.get(content_id_for("maqil_story__001__first.wav"))["duration_sec"],
    )

    # الترجمة تتبع المواصفات
    story = store.get(content_id_for("maqil_story__001__first.wav"))
    check(
        "scan: silence window from the spec",
        story["min_room_silence_sec"]
        == cfg.content_windows["maqil_story"]["min_room_silence_sec"],
        story["min_room_silence_sec"],
    )
    check(
        "scan: ducking ratio from config",
        abs(story["ducking_ratio"] - cfg.content_ambient_ratio) < 1e-9,
        story["ducking_ratio"],
    )

# ---------- مكتبة فارغة ----------
empty_dir = tmp / "empty"
empty_dir.mkdir()
with ContentStore(tmp / "e.db") as store2:
    rep4 = ContentLibrary(empty_dir, store2, cfg).scan()
    check("empty: is_empty is True", rep4.is_empty)
    check(
        "empty: reported as such", any("فارغ" in x for x in rep4.skipped), rep4.skipped
    )
    check("empty: no rows", store2.count() == 0)

# ---------- مجلد غير موجود ----------
with ContentStore(tmp / "m.db") as store3:
    rep5 = ContentLibrary(tmp / "does_not_exist", store3, cfg).scan()
    check("missing dir: is_empty, no crash", rep5.is_empty, rep5.skipped)
    lib3 = ContentLibrary(tmp / "does_not_exist", store3, cfg)
    lib3.ensure_root()
    check("missing dir: ensure_root creates it", (tmp / "does_not_exist").is_dir())

# ---------- ملف تالف: خطأ معلن لا صمت ----------
broken_dir = tmp / "broken"
broken = broken_dir / "maqil_story__001__bad.wav"
broken.parent.mkdir(parents=True)
broken.write_bytes(b"RIFFnope" * 30)
with ContentStore(tmp / "b.db") as store4:
    rep6 = ContentLibrary(broken_dir, store4, cfg).scan()
    check("broken: reported as an error", rep6.errors, rep6.errors)
    check("broken: no row written", store4.count() == 0, store4.count())
    check(
        "broken: is_empty is False (a real problem, not an empty library)",
        rep6.is_empty is False,
    )

# ---------- قياس المدة مرة واحدة، لا عند كل فحص ----------
# فكّ مقطع عشرين دقيقة يستهلك ~211 ميغابايت لحظياً، فإعادة القياس عند
# كل فحص تجعل تفتّح التطبيقпен ding. البصمة (زمن التعديل + الحجم) هي
# الضمان: الملف كما هو لا يُقاس ثانية.
from src.context_aware_audio import content_library as _cl

_cache_dir = tmp / "cache"
write_wav(_cache_dir / "maqil_story__001__a.wav", 1.0)
_measured = {"n": 0}
_orig_measure = _cl.measure_duration


def _counting(path):
    _measured["n"] += 1
    return _orig_measure(path)


_cl.measure_duration = _counting
try:
    with ContentStore(tmp / "cache.db") as _cs:
        _lib = ContentLibrary(_cache_dir, _cs, cfg)
        _lib.scan()
        check("cache: measured once on the first scan", _measured["n"] == 1, _measured)
        _lib.scan()
        _lib.scan()
        check("cache: unchanged files are not re-measured", _measured["n"] == 1,
              _measured)
        write_wav(_cache_dir / "maqil_story__001__a.wav", 2.0)
        _lib.scan()
        check("cache: a changed file is re-measured", _measured["n"] == 2, _measured)
finally:
    _cl.measure_duration = _orig_measure


shutil.rmtree(tmp, ignore_errors=True)
print(f"\nRESULT: {PASSED} passed / {FAILED} failed")
sys.exit(1 if FAILED else 0)
