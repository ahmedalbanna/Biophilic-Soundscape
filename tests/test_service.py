"""
test_service.py - الخدمة بلا واجهة

ما كان يُفحص عبر نافذة Tk يُفحص هنا على الخدمة نفسها. المنطق واحد
وقبل كان يحتاج Tk ليُفحص؛ الآن يُفحص بما يُفحص فعلاً.

`tick()` محقونة الساعتين، فكل فحص هنا يحسم إطاراً واحداً بدل
انتظار حائط الساعة — إلا النافذة الحقيقية التي تحتاج نبضاتٍ متتالية
من خيطٍ واحد.
"""

import shutil
import sys
import tempfile
import wave
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.context_aware_audio import settings
from src.context_aware_audio.service import AudioService

PASSED = FAILED = 0
STEP = 0.2
BASE = datetime(2026, 9, 30, 8, 0, 0)


def check(name, cond, extra=""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"PASS {name}")
    else:
        FAILED += 1
        print(f"FAIL {name}  {extra}")


def wav(path, seconds=40.0, rate=16000):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(seconds * rate))
    return path


class Clock:
    """ساعتان محقونتان: now و real_now، كلاهما من ts."""

    def __init__(self, base=None, offset=0.0):
        self.ts = (base or BASE).timestamp()
        self.offset = timedelta(seconds=offset)

    def step(self, seconds=STEP):
        self.ts += seconds
        return self

    def pair(self):
        stamp = datetime.fromtimestamp(self.ts)
        return stamp + self.offset, stamp


def build(tmp, clips=2, window="duha_wisdom"):
    lib = tmp / "library"
    for i in range(1, clips + 1):
        wav(lib / f"{window}__{i:03d}__clip{i}.wav", 40.0)
    store_dir = tmp / "db"
    with (
        mock.patch.object(settings, "writable_path", lambda n: Path(tmp) / n),
        mock.patch(
            "src.context_aware_audio.service.content_db_path",
            lambda: store_dir / "content.db",
        ),
        mock.patch(
            "src.context_aware_audio.service.content_library_dir",
            lambda override="": lib,
        ),
    ):
        svc = AudioService(log_path=None)
    return svc, lib


# ===== 1) البناء والنبضة =====
tmp = Path(tempfile.mkdtemp())
try:
    svc, lib = build(tmp)
    check("svc: built without a display", svc is not None)
    check("svc: starts stopped", svc.running is False)
    # الفحص عند الإقلاع: بلاه تظهر المكتبة فارغة على تثبيت جديد حتى
    # يضغط المستخدم زر إعادة الفحص. العدد لا الكلمة هو الفحص.
    check(
        "svc: the library is indexed at startup",
        svc.content_store.count() == 2,
        svc.content_store.count(),
    )
    check(
        "svc: the library root is the one we gave it",
        str(svc.content_library_path) == str(lib),
        svc.content_library_path,
    )
    check(
        "svc: no tick error while stopped",
        svc.tick() is None and not any("خطأ في النبضة" in l for l in svc.log_tail(40)),
        svc.log_tail(5),
    )

    svc.start_engine()
    check("svc: start flips the flag", svc.running is True)
    clk = Clock()
    last = None
    for _ in range(70):
        clk.step()
        last = svc.tick(*clk.pair())
    check("svc: the engine decides when running", last is not None)
    check(
        "svc: content started in its window", svc.content.is_running, svc.content.state
    )
    # النافذة تُقرأ من ساعة النبضة لا من ساعة الحائط. الساعة المحاكاة
    # معطّلة، فـ clock.now() هو الآن الحقيقي (~18:00) = مغرب/عشاء، وهي
    # فترة بلا نافذة محتوى. لو بحثت النافذة عن ساعةٍ أخرى لفكّر
    # المحتوى في مقيل بينما المحرّك يقرّر ضحى.
    check(
        "svc: the window follows the tick clock, not the wall clock",
        svc._content_window_now() == "duha_wisdom",
        f"{svc._content_window_now()!r} @ {svc._now():%H:%M}",
    )

    # ===== 2) الإعدادات: كل مفتاح يصل إلىلقطة والحقل =====
    snap = svc.settings_snapshot()
    for key in (
        "master_vol",
        "master_mute",
        "duck_depth",
        "athan_enabled",
        "use_mic",
        "content_enabled",
        "content_muted",
        "content_volume",
        "content_sync_url",
        "content_sync_token",
        "content_sync_max_parts",
        "sim_enabled",
        "sim_offset_sec",
    ):
        check(f"svc: the snapshot carries {key}", key in snap, sorted(snap))

    out = svc.update_settings({"master_vol": 55, "duck_depth": 80.0})
    check("svc: a setting is applied", out["master_vol"] == 55.0, out["master_vol"])
    check(
        "svc: and reaches the engine config",
        svc.config.duck_depth == 80.0,
        svc.config.duck_depth,
    )
    on_disk = settings.load_settings()
    check(
        "svc: and is written to disk",
        on_disk["master_vol"] == 55.0,
        on_disk.get("master_vol"),
    )

    # نوع خاطئ: المنقّي يرفضه
    out = svc.update_settings({"master_vol": "junk"})
    # لا تبقى القيمة السابقة: المنقّي يرجع إلى افتراضي الإعداد (100)،
    # وهو سلوك settings.py المقصود. المهم ألّا يدخل الحقل خاماً.
    check(
        "svc: a bad value falls back to the default",
        out["master_vol"] == 100.0,
        out["master_vol"],
    )
    svc.update_settings({"master_vol": 55})

    # مفتاح مجهول: لا أثر له ولا رحمة
    before = dict(svc.settings_snapshot())
    svc.update_settings({"totally_unknown": 1})
    check(
        "svc: an unknown key is ignored",
        svc.settings_snapshot() == before,
        set(svc.settings_snapshot()) ^ set(before),
    )

    # ===== 3) كتم المحتوى يوقف المؤشر لا المشغّل وحده =====
    clk = Clock()
    for _ in range(20):
        clk.step()
        svc.tick(*clk.pair())
    check(
        "mute: the engine is playing", svc.content.state == "playing", svc.content.state
    )
    pos_before = svc.content.position_sec
    svc.update_settings({"content_muted": True})
    for _ in range(6):
        clk.step()
        svc.tick(*clk.pair())
    check("mute: the player stops", svc.content_player.is_playing is False)
    check("mute: the engine keeps the clip", svc.content.is_running is True)
    check(
        "mute: the playhead is paused, not just the player",
        svc.content.state == "paused_interrupt",
        svc.content.state,
    )
    check(
        "mute: the position does not run away",
        svc.content.position_sec <= pos_before + 0.5,
        f"{pos_before:.1f} -> {svc.content.position_sec:.1f}",
    )
    svc.update_settings({"content_muted": False})
    for _ in range(6):
        clk.step()
        svc.tick(*clk.pair())
    check(
        "mute: unmuting brings the sound back",
        svc.content_player.is_playing is True,
        svc.content_player.history[-1] if svc.content_player.history else "",
    )

    # ===== 4) التخطي يقدّم ولا يُحسب مرة في اليوم =====
    svc.content_skip()
    for _ in range(6):
        clk.step()
        svc.tick(*clk.pair())
    outcomes = [
        r["outcome"]
        for r in svc.content_store._conn.execute(
            "SELECT outcome FROM playback_log ORDER BY id"
        )
    ]
    check("skip: the row says skipped", "skipped" in outcomes, outcomes)
    check("skip: the engine is idle", svc.content.state == "idle", svc.content.state)

    # ===== 5) التشغيل الفوري يتجاوز البوابة =====
    svc.content_play_now()
    check(
        "play: the engine is running again", svc.content.is_running, svc.content.state
    )
    # اللقبضة تتحدّث في النبضة التالية لا فور الطلب: القراءة تُبنى
    # من قرار المحرّك بعد tick.
    clk.step()
    svc.tick(*clk.pair())
    check(
        "play: and it is the next clip", "clip2" in svc.content_text, svc.content_text
    )

    # ===== 6) الفحص والعدد =====
    line = svc.content_rescan()
    # لا يكفي أن تذكر الكلمة: «فحص المكتبة: 0 مقطع» و«مقطعان» فيها
    # نفس الكلمة. الرقم هو الفحص. و«المجلد فارغ» خطأ صريح عن مكتبة
    # فيها مقطعان لا تتغيّر — وهو ما كان يُقال.
    check("rescan: it reports the number it holds", "2 مقطع" in line, line)
    check("rescan: and it does not call it empty", "فارغ" not in line, line)
    check(
        "rescan: the store has the clips",
        svc.content_store.count() == 2,
        svc.content_store.count(),
    )

    # ===== 7) سبب التعطّل يُقرأ من اللقبضة =====
    svc._set_content_error("تعذّر فتح قاعدة المحتوى: تالف")
    check("error: it is held", "قاعدة" in svc.content_error, svc.content_error)
    st = svc.state()
    check(
        "error: and it reaches the state snapshot",
        "قاعدة" in st["content"]["error"],
        st["content"]["error"],
    )
    svc._set_content_error("")
    check("error: clearing it clears the state", svc.state()["content"]["error"] == "")

    # ===== 8) القمّة قابلة للتسلسل وتشمل كل ما ترسمه الواجهة =====
    import json

    st = svc.state()
    json.dumps(st)  # يجب ألا يرمي
    for key in ("engine", "content", "sync", "mic", "audio", "now"):
        check(f"state: has {key}", key in st, sorted(st))
    for key in ("running", "state", "db", "is_speech", "period"):
        check(f"state.engine: has {key}", key in st["engine"], sorted(st["engine"]))
    check(
        "state: the dB is a number",
        isinstance(st["engine"]["db"], (int, float)),
        st["engine"]["db"],
    )
    check(
        "state: the content count is a number",
        isinstance(st["content"]["count"], int),
        st["content"]["count"],
    )
    check(
        "state: the sim offset is reported",
        isinstance(st["now"]["offset_sec"], (int, float)),
        st["now"]["offset_sec"],
    )

    # ===== 9) عنوان الخادم: يُقبل ويُرفض بصوت =====
    svc.content_sync_url = "http://example.test:8787"
    check(
        "url: http passes",
        svc.sync_base() == "http://example.test:8787",
        svc.sync_base(),
    )
    svc.content_sync_url = "https://example.test"
    check("url: https passes", svc.sync_base() == "https://example.test")
    svc.content_sync_url = "file:///C:/secret.json"
    check("url: file:// is refused", svc.sync_base() == "")
    check("url: and the reason is shown", "http" in svc.sync_note, svc.sync_note)
    svc.content_sync_url = "example.test"
    check("url: a bare host is refused", svc.sync_base() == "")
    check("url: the refusal is explained", "مرفوض" in svc.sync_note, svc.sync_note)
    svc.content_sync_url = ""

    # ===== 10) سقف الجلسات =====
    for raw, want in (("0", 1), ("-4", 1), ("9999", 500), ("junk", 20), ("7", 7)):
        svc.content_sync_parts = raw
        check(f"parts: {raw!r} -> {want}", svc.sync_parts() == want, svc.sync_parts())
    svc.content_sync_parts = 20

    # ===== 11) الرمز من البيئة يُعلَن =====
    import os

    os.environ["CONTENT_SYNC_TOKEN"] = "env-token"
    try:
        svc.content_sync_token = "typed"
        check(
            "token: the env var wins",
            svc.sync_token_now() == "env-token",
            svc.sync_token_now(),
        )
        check(
            "token: and the UI is told why",
            "CONTENT_SYNC_TOKEN" in svc.sync_note,
            svc.sync_note,
        )
    finally:
        del os.environ["CONTENT_SYNC_TOKEN"]
    check(
        "token: the typed value is used without it",
        svc.sync_token_now() == "typed",
        svc.sync_token_now(),
    )

    # ===== 12) السحب بلا خادم لا يكسر شيئاً =====
    svc.content_sync_url = ""
    line = svc.content_sync()
    check("sync: no server is a clean refusal", "خادم" in line, line)
    check("sync: and it is not marked running", svc._sync_running is False)
    check(
        "sync: scanning without a server is silent",
        "خادم" in svc.sync_scan(),
        svc.sync_scan(),
    )

    # ===== 13) الساعة =====
    # ضبط الوقت لا أثر به والمحاكاة معطّلة: clock.now() يعيد
    # الزمن الحقيقي فلا يُقرأ الإزاحة. فالتفعيل أولاً.
    svc.sim_set_time(15, 30)
    check(
        "sim: setting the time while disabled does nothing",
        svc.clock.now().hour != 15,
        svc.clock.now().strftime("%H:%M"),
    )
    svc.sim_enable(True)
    svc.sim_set_time(15, 30)
    svc.sim_nudge(2.0)
    check(
        "sim: the nudge moved it",
        svc.clock.now().hour == 17,
        svc.clock.now().strftime("%H:%M"),
    )
    svc.sim_reset()
    check(
        "sim: the reset returns to real time",
        abs(svc.clock.offset_sec) < 1.0,
        svc.clock.offset_sec,
    )

    # ===== 14) السيناريو يزرع ما يقول =====
    svc.start_engine()
    # ساعة واحدة تتشارك صاروخاً واحداً. ساعة جديدة من BASE كانت تُرجع
    # زمن المحرّك إلى الوراء بعد قسم المحتوى، فيصير حساب تهدئة النقاش
    # سالباً ولا ينتهي القفل أبداً. الخلل في الفحص لا في الشيفرة.
    clk2 = Clock(base=datetime.fromtimestamp(clk.ts))
    svc.scenario("debate")
    check(
        "scen: it says what it planted",
        "نقاش" in svc.scenario("debate"),
        svc.scenario("nonsense"),
    )
    svc.scenario("talk")
    clk2.step()
    svc.tick(*clk2.pair())
    check(
        "scen: the planted debate is still pinned for 3 seconds",
        svc.engine._in_debate_mute is True,
        svc.engine._in_debate_mute,
    )
    # إبطال التثبيت زمن حقيقي لا طابع. لا ننتظر ثلاث ثوانٍ في
    # فحص: نُبطله كما يفعل مرورُ الوقت.
    svc._scenario_until = 0.0
    # قفل النقاش 60ث من الهدوء، فنبضة واحدة لا تفكّه: نمرّ عليها كلّها
    # بحقن الطوابع، بلا انتظار.
    for _ in range(320):
        clk2.step()
        svc.tick(*clk2.pair())
    check(
        "scen: once the pin expires the latch clears",
        svc.engine._in_debate_mute is False,
        svc.engine._in_debate_mute,
    )
    line = svc.scenario("nonsense")
    check("scen: an unknown scenario is refused", "مجهول" in line, line)

    # ===== 15) المستوى اليدوي =====
    check("manual: it takes a number", svc.set_manual_db(42.0) == 42.0)
    check("manual: it clamps high", svc.set_manual_db(500.0) == 100.0)
    check("manual: it clamps low", svc.set_manual_db(-5.0) == 0.0)
    check("manual: junk leaves it alone", svc.set_manual_db("x") == 0.0)

    # ===== 16) إغلاق =====
    svc.shutdown()
    check("shutdown: the store is closed", svc.content_store.is_closed)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print(f"\nRESULT: {PASSED} passed / {FAILED} failed")
sys.exit(1 if FAILED else 0)
