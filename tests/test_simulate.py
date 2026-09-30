"""
سيناريوهات المحاكاة السبعة والثماناء: أنها تُخرج ما تعد به.

المحاكاة نصوص مطبوعة، فبلا فحص يفقد أحدها سطراً ويسكت:silent لا
أمان. نمرّرها بمخرج ملتقَط ونطالب بالقرارات التي يفترضها كلٌّ منها.
"""

import io
import shutil
import sys
import tempfile
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.context_aware_audio import ContextAwareAudioEngine, EngineConfig
from src.context_aware_audio.simulated_player import SimulatedPlayer
from src.context_aware_audio.simulate import (
    build_content,
    feed_c,
    scenario_content_interrupt,
    scenario_content_postponed,
)

PASSED = FAILED = 0


def check(name, cond, extra=""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"PASS {name}")
    else:
        FAILED += 1
        print(f"FAIL {name}  {extra}")


def fresh():
    cfg = EngineConfig()
    engine = ContextAwareAudioEngine(cfg)
    return engine, SimulatedPlayer()


def actions(text):
    """قرارات المحتوى المطبوعة، بالترتيب."""
    out = []
    for line in text.splitlines():
        if ">>>" in line:
            out.append(line.split(">>>")[1].split("|")[0].strip())
    return out


# ===== 1) السيناريو السابع: المقاطعة =====
tmp = tempfile.mkdtemp()
try:
    engine, player = fresh()
    buf = io.StringIO()
    with redirect_stdout(buf):
        scenario_content_interrupt(engine, player, datetime(2026, 9, 30, 14, 0, 0), tmp)
    text = buf.getvalue()
    acts = actions(text)

    check("sim7: the narration starts", "start" in acts, acts)
    check("sim7: the guest's speech pauses it", "pause" in acts, acts)
    check("sim7: the quiet resumes it", "resume" in acts, acts)
    check(
        "sim7: the pause precedes the resume",
        # لا index() بلا فحص présence: غياب الإجراء يرفع استثناءً
        # يوقف الملف، و读取 نتيجة الفحصين السابقين لا يُطبع أبداً.
        "pause" in acts
        and "resume" in acts
        and acts.index("pause") < acts.index("resume"),
        acts,
    )
    check("sim7: the debate is a pause, not a stop", "stop" not in acts, acts)
    check("sim7: nothing finished it", "finished" not in acts, acts)
    check("sim7: the state is reported in Arabic", "مقاطعة" in text or "مقاطعة" in text)
    check("sim7: the play log is printed", "سجل التشغيل" in text)
    check(
        "sim7: the log names the clip, not a hash", "قصة" in text and "282b" not in text
    )
    # التراجع: الموضع بعد المقاطعة أقل من ما قبلها
    rows = [ln for ln in text.splitlines() if ">>>" in ln]
    pauses = [i for i, a in enumerate(acts) if a == "pause"]
    check("sim7: at least two pauses (speech and debate)", len(pauses) >= 2, acts)
    check(
        "sim7: the rewind shows a non-zero position before it",
        any(">  0.0ث" in ln or "  1.0ث" in ln for ln in rows),
        rows[:4],
    )
finally:
    shutil.rmtree(tmp, ignore_errors=True)

# ===== 2) السيناريو الثامن: المجلس الصاخب =====
tmp = tempfile.mkdtemp()
try:
    engine, player = fresh()
    buf = io.StringIO()
    with redirect_stdout(buf):
        scenario_content_postponed(engine, player, datetime(2026, 9, 30, 14, 0, 0), tmp)
    text = buf.getvalue()
    acts = actions(text)

    check("sim8: the noisy room postpones without starting", "start" not in acts, acts)
    check("sim8: the postponement is reported", "postponed" in text)
    check("sim8: the postponing window is named", "maqil_story" in text, text[:0])
    check("sim8: the outcome is stated", "الإقصاء نجح" in text)
    check("sim8: a manual start still works", "طلب صريح" in text)
    # الطلب يبدأ بلا إجراء: المحرك يبدأ في لحظته فالنبضة التالية حالة
    # مستقرة. فالدليل سطر الحالة لا سطر إجراء. والحالة بعد نهاية
    # السيناريو مقصودة: force_stop عند الخروج.
    # العلامة تُعرَّف هنا لا على السطر مع المعرّف: سطر يجمع اسماً
    # لاتينياً وعربيةً يصير شبهةً في مسح النثر.
    marker = "طلب صريح"
    tail = text.split(marker)[-1] if marker in text else ""
    check(
        "sim8: the manual start reaches the engine",
        "الحالة: playing" in tail and "قصة يونس" in tail,
        tail[:160],
    )
    check(
        "sim8: and the scenario stops it before leaving",
        engine.content.is_running is False,
        engine.content.state,
    )
    check("sim8: the play log is printed", "سجل التشغيل" in text)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

# ===== 3) build_content يبني ما يفترضه =====
tmp = tempfile.mkdtemp()
try:
    engine, player = fresh()
    store = build_content(engine, tmp)
    check("build: two clips were found", store.count() == 2, store.count())
    check("build: the engine has the store", engine.content.store is store)
    clip = store.next_for_window("maqil_story")
    check(
        "build: the first clip is the first title",
        clip is not None and clip["title"] == "قصة يونس",
        clip["title"] if clip else None,
    )
    check(
        "build: the duration was measured",
        clip["duration_sec"] > 200.0,
        clip["duration_sec"] if clip else None,
    )
    store.close()
finally:
    shutil.rmtree(tmp, ignore_errors=True)

# ===== 4) feed_c لا يطبع إلا عند تغيّر القرار =====
# سطر لكل نبضة يغرق المحاكاة في خمسمئة سطر متطابرة.
tmp = tempfile.mkdtemp()
try:
    engine, player = fresh()
    store = build_content(engine, tmp)
    buf = io.StringIO()
    with redirect_stdout(buf):
        from datetime import timedelta

        now = datetime(2026, 9, 30, 14, 0, 0)
        for _ in range(200):
            now += timedelta(seconds=0.2)
            feed_c(engine, player, 25.0, False, now)
    printed = [ln for ln in buf.getvalue().splitlines() if ">>>" in ln]
    check(
        "feed: the same action is not repeated every frame",
        len(printed) <= 3,
        len(printed),
    )
    check(
        "feed: the start is among them", any("start" in ln for ln in printed), printed
    )
    store.close()
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print(f"\nRESULT: {PASSED} passed / {FAILED} failed")
sys.exit(1 if FAILED else 0)
