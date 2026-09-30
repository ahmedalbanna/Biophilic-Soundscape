"""
simulate.py - محاكاة سيناريوهات اليوم الكامل والحالات الخاصة
تشغيل: python -m src.context_aware_audio.simulate

السيناريوهات 7 و8 تحتاج مكتبة محتوى، فتُبنى في مجلد مؤقت وتُمحى
معه. لا تمسّ المحاكاة ملفات المستخدم ولا القرص خارج المجلد المؤقت.
"""

import sys
from datetime import datetime, timedelta
from datetime import time as dtime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.context_aware_audio import ContextAwareAudioEngine, EngineConfig
from src.context_aware_audio.audio_types import AudioFrame
from src.context_aware_audio.simulated_player import SimulatedPlayer


def feed(engine, player, db, speech, now, overlapping=False, greeting=False, label=""):
    """يزحم إطاراً صوتياً واحداً في المحرك ويطبع القرار الناتج."""
    f = AudioFrame(
        timestamp=now.timestamp(),
        db_level=db,
        is_speech=speech,
        is_overlapping=overlapping,
        is_greeting_tone=greeting,
    )
    cmd = engine.process_frame(f, now)
    line = player.apply(cmd)
    print(
        f"{now.strftime('%H:%M:%S')} | dB={db:4.0f} speech={int(speech)} | {line} {label}"
    )
    return cmd


# ---------- مسار المحتوى ----------
_last_content = None


def build_content(engine, tmp, window="maqil_story", titles=("قصة يونس", "قصة موسى")):
    """
    يجهّز مكتبة مؤقتة ومخزناً ويربطهما بمحرك المحتوى.

    يعيد المخزن. المقاطع صمتٌ لا صوت: نرى قرارات ولا نسمع سرداً،
    فلا داعي لملفات ثقيلة. والمجلد مؤقت حتى لا يمسّ المحاكاة
    ملفات المستخدم.
    """
    import wave

    from src.context_aware_audio.content_engine import ContentEngine
    from src.context_aware_audio.content_library import ContentLibrary
    from src.context_aware_audio.content_store import ContentStore

    lib_dir = Path(tmp) / "library"
    lib_dir.mkdir(parents=True, exist_ok=True)
    for i, title in enumerate(titles, start=1):
        path = lib_dir / f"{window}__{i:03d}__{title}.wav"
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(b"\x00\x00" * 16000 * 300)  # خمس دقائق
    store = ContentStore(Path(tmp) / "content.db")
    ContentLibrary(lib_dir, store, engine.config).scan()
    engine.content = ContentEngine(engine.config, store)
    return store


def feed_c(engine, player, db, speech, now, moment=""):
    """
    إطار واحد، وسطر إضافي عند تغيّر قرار المحتوى فقط.

    لا يُطبع سطر المحتوى كل نبضة: القرار بلا تغيير يغرق المحاكاة
    في خمسمئة سطر متطابرة، والمقروء هو الذي تغيّر.
    """
    global _last_content
    f = AudioFrame(
        timestamp=now.timestamp(),
        db_level=db,
        is_speech=speech,
        is_overlapping=db >= 65.0 and speech,
    )
    cmd = engine.process_frame(f, now)
    player.apply(cmd)
    action = cmd.content_action
    if action is not None and action is not _last_content:
        _last_content = action
        eng = engine.content
        pos = cmd.content_position_sec
        pct = 0.0
        if eng.duration_sec > 0:
            pct = max(0.0, min(100.0, pos * 100.0 / eng.duration_sec))
        why = f" | {moment}" if moment else ""
        print(
            f"      >>> {action.value:8s} | {eng.state:17s} | "
            f"{pos:5.1f}ث ({pct:3.0f}%) | {cmd.content_file or '-'}{why}"
        )
    return cmd


def scenario_content_interrupt(engine, player, now, tmp):
    """مقاطعة في منتصف السرد، ثم استئناف، ثم نقاش حامي."""
    print("\n=== 7) مقاطعة في منتصف السرد (مقيل 14:00) ===")
    store = build_content(engine, tmp)
    engine.reset()

    print("  غرفة هادئة 25dB — النافذة تحتاج 10ث هدوء:")
    for _ in range(70):
        now += timedelta(seconds=0.2)
        feed_c(engine, player, 25.0, False, now)

    print("  ضيف يتكلم فوق السرد 52dB (تجاوز بوابة الهدوء 50dB):")
    for _ in range(15):
        now += timedelta(seconds=0.2)
        feed_c(engine, player, 52.0, True, now, moment="كلام الضيف")

    print("  الغرفة هدأت 7ث (فوق عتبة الاستئناف 5ث):")
    for _ in range(35):
        now += timedelta(seconds=0.2)
        feed_c(engine, player, 22.0, False, now)

    print("  نقاش حامي 68dB — كتم وتوقّف، لا انتهاء:")
    for _ in range(10):
        now += timedelta(seconds=0.2)
        feed_c(engine, player, 68.0, True, now, moment="نقاش")

    print("  بعد 65ث هدوء يعود الاثنان معاً:")
    for _ in range(325):
        now += timedelta(seconds=0.2)
        feed_c(engine, player, 20.0, False, now)

    engine.content.force_stop("إنهاء المحاكاة")
    _print_log(store)


def scenario_content_postponed(engine, player, now, tmp):
    """مجلس صاخب: تأجيل ثم إقصاء، وحدود ما يُستأنف."""
    print("\n=== 8) مجلس صاخب يؤجّل السرد ثم يُقصيه (مقيل) ===")
    store = build_content(engine, tmp)
    engine.reset()

    for attempt in range(1, 4):
        print(f"  الجلسة الصاخبة {attempt} — 4ث فوق 50dB:")
        for _ in range(20):
            now += timedelta(seconds=0.2)
            feed_c(engine, player, 60.0, True, now)
        # التأجيل لا يُصدر إجراءً، فحالته تُقرأ من المحرك لا من
        # الإجراء. بلا هذا السطر يبدو المجلس الصاخب صمتاً لا قراراً.
        # ولا نطبع "السبب" من cmd.reason: ذاك سبب الخلفية، وسبب
        # المحتوى لا يلتصق بالأمر إلا عند الانتقال.
        eng = engine.content
        print(
            f"    الحالة: {eng.state} | المؤجَّلات: "
            f"{', '.join(sorted(eng._postponed_windows)) or '—'}"
        )
        print("  ثم 7ث هدوء (فوق عتبة الاستئناف):")
        for _ in range(35):
            now += timedelta(seconds=0.2)
            feed_c(engine, player, 22.0, False, now)

    print("  الغرفة هادئة الآن 40ث — والمفترض ألّا يبدأ شيء:")
    started = False
    for _ in range(200):
        now += timedelta(seconds=0.2)
        cmd = feed_c(engine, player, 22.0, False, now)
        if cmd.content_action is not None and cmd.content_action.value == "start":
            started = True
    print(f"  بدأ شيء؟ {'نعم — وهذا خطأ' if started else 'لا. الإقصاء نجح'}")

    print("  «شغّل الآن» يتجاوز البوابة، ولا يحتاجها:")
    clip = engine.content.store.next_for_window("maqil_story")
    if clip is not None:
        if engine.content.request_now(clip, now):
            eng = engine.content
            # الطلب يبدأ بلا إجراء: المحرك يقرأ الطلب فيبدأ، فالنبضة
            # التالية حالة مستقرة لا تطبع شيئاً. فنطبع الحالة صراحةً.
            print(
                f"    طلب صريح: {clip['title']} | الحالة: {eng.state}"
                f" | المقطع: {eng.current_title}"
            )

    engine.content.force_stop("إنهاء المحاكاة")
    _print_log(store)


def _print_log(store):
    """
    يطبع سجل التشغيل كما رآه: العنوان وعدد المرات والدقائق.

    التجميع على معرّف المقطع لا على النافذة، فنقرأ العنوان من
    المخزن بدل طباعة بصمةٍ ستَغني عن معنى السطر كله.
    """
    rows = store.stats()
    if not rows:
        print("  السجل فارغ")
        return
    print("  سجل التشغيل (مقطع / مرات / دقائق):")
    for audio_id, times, minutes in rows:
        row = store.get(audio_id)
        title = row["title"] if row is not None else audio_id[:8]
        print(f"    {title:20s} {times:3d} مرة  {minutes:6.1f} دقيقة")


def main():
    cfg = EngineConfig()
    engine = ContextAwareAudioEngine(cfg)
    player = SimulatedPlayer()
    # أوقات صلاة ثابتة حتى لا تعتمد المحاكاة على API
    engine.prayer.set_times(
        {
            "fajr": dtime(5, 10),
            "dhuhr": dtime(12, 5),
            "asr": dtime(15, 25),
            "maghrib": dtime(18, 10),
            "isha": dtime(19, 30),
        }
    )
    day = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    print("=== 1) جدول 24 ساعة (لقطات) ===")
    for h, db, sp in [
        (6, 25, False),
        (10, 45, True),
        (13, 50, True),
        (15, 48, True),
        (19, 10, False),
        (21, 35, False),
        (2, 5, False),
    ]:
        now = day.replace(hour=h)
        feed(engine, player, db, sp, now, label=f"[فترة {h}:00]")
        engine.reset()

    print("\n=== 2) نقاش حامي >65dB ===")
    now = day.replace(hour=15, minute=0)
    feed(engine, player, 70, True, now, overlapping=True, label="صراخ")
    for i in range(1, 4):
        now += timedelta(seconds=20)
        feed(engine, player, 30, False, now, label=f"هدوء {i * 20}ث")
    now += timedelta(seconds=50)
    feed(engine, player, 30, False, now, label="بعد 60+ ث هدوء -> عودة")

    print("\n=== 3) ترحيب ضيوف (16:30 بعد انتهاء قفل العصر) ===")
    engine.reset()
    now = day.replace(hour=16, minute=30)
    feed(engine, player, 62, True, now, greeting=True, label="أرحبوا!")
    feed(engine, player, 45, True, now + timedelta(seconds=2), label="حديث")

    print("\n=== 4) هدوء مفاجئ 10ث (16:40) ===")
    engine.reset()
    now = day.replace(hour=16, minute=40)
    feed(engine, player, 45, True, now, label="كلام")
    feed(
        engine,
        player,
        20,
        False,
        now + timedelta(seconds=11),
        label="صمت 11ث -> Fade-In",
    )
    feed(
        engine,
        player,
        50,
        True,
        now + timedelta(seconds=12),
        label="عاد الكلام -> اختفاء 1ث",
    )

    print("\n=== 5) وقت الأذان (المغرب 18:10) ===")
    engine.reset()
    for m in [(18, 5), (18, 8), (18, 10), (18, 20), (18, 50)]:
        now = day.replace(hour=m[0], minute=m[1])
        feed(engine, player, 40, False, now, label="اختبار قفل الصلاة")

    print("\n=== 6) تعارض: ترحيب + أذان (الأولوية للصلاة) ===")
    engine.reset()
    now = day.replace(hour=18, minute=9)
    feed(
        engine, player, 65, True, now, greeting=True, label="ضيف وقت الأذان -> كتم صلاة"
    )

    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        scenario_content_interrupt(engine, player, day.replace(hour=14, minute=0), tmp)
        scenario_content_postponed(engine, player, day.replace(hour=14, minute=0), tmp)

    print("\n✅ انتهت المحاكاة")


if __name__ == "__main__":
    main()
