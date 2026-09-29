"""
simulate.py - محاكاة سيناريوهات اليوم الكامل والحالات الخاصة
تشغيل: python -m src.context_aware_audio.simulate
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

    print("\n✅ انتهت المحاكاة")


if __name__ == "__main__":
    main()
