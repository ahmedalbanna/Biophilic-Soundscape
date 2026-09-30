"""
البوابة اليدوية الثانية: إزاحة كبيرة، ومقطع كامل، ثم الذي يليه.

هذه هي الحالة التي تقول المراجعة إنه لا يغطّيها أي فحص: محرّك المحتوى
مع ساعة المحاكاة مُزاحة، تشغيل مقطع حتى نهايته، والتأكد أن الذي يليه
يبدأ. فحص الانحدار للساعتين يحقّن الطوابع؛ هذا يشغّل نبضة الواجهة
الحقيقية وساعة المحاكاة الحقيقية.

المقيل من 14:00 إلى 18:00، فنثبّت الساعة على 17:00 ونقيس الإزاحة
الفعلية: هي تتبع الزمن الحقيقي، فتبقى كبيرة طوال الجولة. لو اختفت
الإزاحة أثناء التشغيل لقرأ الفحص نفسه على شيفرة مكسورة.
"""

import shutil
import sys
import tempfile
import time
import tkinter as tk
import wave
from pathlib import Path
from unittest import mock

sys.path.insert(0, r"C:\Users\ahmed\work")

from src.context_aware_audio import log_setup, settings
from src.context_aware_audio.app import DesktopApp
from src.context_aware_audio.content_library import ContentLibrary
from src.context_aware_audio.content_store import ContentStore
from src.context_aware_audio.real_content import RealContentPlayer

PASSED = FAILED = 0


def check(name, cond, extra=""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"PASS {name}")
    else:
        FAILED += 1
        print(f"FAIL {name}  {extra}")


WINDOW = "maqil_story"
CLIPS = 3
SECONDS = 4.0
HOLD_H, HOLD_M = 17, 0


def wav(path, seconds, rate=16000):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(seconds * rate))


def pump_until(root, app, want, budget, log_lines, holds):
    """يضغط النبضات حتى تُشغَّل المقاطع المطلوبة أو ينتهي الوقت."""
    seen = []
    last = None
    deadline = time.time() + budget
    while time.time() < deadline and len(seen) < want:
        # إعادة التثبيت كلّها: تجعل الإزاحة تتبع الزمن الحقيقي،
        # فتبقى كبيرة ما دام الفارق بين 17:00 والحائط كبيراً.
        app.clock.set_hhmmss(HOLD_H, HOLD_M)
        holds.append(app.clock.offset_sec)
        root.update()
        time.sleep(0.02)
        title = app.content.current_title
        if title and title != last:
            seen.append(title)
            last = title
    return seen


tmp = Path(tempfile.mkdtemp())
lib = tmp / "library"
for i in range(1, CLIPS + 1):
    wav(lib / f"{WINDOW}__{i:03d}__clip{i}.wav", SECONDS)

root = tk.Tk()
root.withdraw()
log_lines = []
holds = []
errs = []
seen = []
try:
    with (
        mock.patch.object(log_setup, "writable_path", lambda n: Path(tmp) / n),
        mock.patch.object(settings, "writable_path", lambda n: Path(tmp) / n),
    ):
        log_setup.install()
        app = DesktopApp(root, log_path=None)
        real_log = app._log
        app._log = lambda m: (log_lines.append(str(m)), real_log(m))[1]

        if app._content_store is not None:
            app._content_store.close()
        app.content_library_path = lib
        store = ContentStore(tmp / "content.db")
        app._content_store = store
        app.engine.content.store = store
        app._content_library = ContentLibrary(lib, store, app.config)
        app.content_player = RealContentPlayer(lib, background=app.player)
        app._on_content_rescan()
        print(f"  المقاطع المفحوصة: {store.count()}")

        # once_per_day على maqil_story يوقف المقطع الثاني بحكم التصميم،
        # فلا يختبر هذا البوابةَ إلا إن عُلِّقت. نعلّقها هنا صراحةً.
        print(f"  once_per_day = {app.config.content_windows[WINDOW]['once_per_day']}")
        app.config.content_windows[WINDOW]["once_per_day"] = False
        # الفجوة 30 دقيقة بين المقاطع. في 180 ثانية نبضة حقيقية لا
        # يمكن أن تمرّ، فالمقطة الثانية لن تبدأ بحكم الساعات لا
        # بحكم الساعة. نُلغيها ليبقى السؤال هو سؤال الساعة.
        print(f"  content_min_gap_min = {app.config.content_min_gap_min}")
        app.config.content_min_gap_min = 0

        app.sim_on.set(True)
        app._on_sim_toggle()
        app.manual_db.set(20.0)
        app.start()

        seen = pump_until(root, app, CLIPS, 180, log_lines, holds)
        # الحلقة تخرج عند بدء المقطع الأخير لا عند انتهائه، فيبقى
        # صفّه interrupted. نمضي قليلاً ليُختم قبل أن نقرأ السجل.
        _settle = time.time() + 12
        while time.time() < _settle:
            app.clock.set_hhmmss(HOLD_H, HOLD_M)
            holds.append(app.clock.offset_sec)
            root.update()
            time.sleep(0.02)

        offsets = [o for o in holds if o is not None]
        print(f"  الساعة المحاكاة الآن: {app.clock.now().strftime('%H:%M')}")
        if offsets:
            print(
                f"  الإزاحة الفعلية: {min(offsets) / 3600:+.2f} .. "
                f"{max(offsets) / 3600:+.2f} ساعة"
            )
        print(f"  مقاطع شُغّلت: {seen}")
        outcomes = [
            r["outcome"]
            for r in store._conn.execute("SELECT outcome FROM playback_log ORDER BY id")
        ]
        print(f"  سجل التشغيل: {outcomes}")
        errs = [m for m in log_lines if "خطأ في النبضة" in m]
        print(f"  أخطاء النبضة: {len(errs)}")
        for e in errs[:2]:
            print(f"    {e}")

        app.stop()
        app._on_close()
        store.close()
finally:
    try:
        root.destroy()
    except Exception:
        pass
    log_setup.close()
    shutil.rmtree(tmp, ignore_errors=True)

# الإزاحة يجب أن تكون كبيرة فعلاً، وإلا لم يختبر هذا شيئاً:
# فحصٌ ينجح بإزاحة صفر يحرس لا شيئاً.
_big = bool(offsets) and min(abs(o) for o in offsets) > 1800
check("offset: the offset really was large throughout", _big,
      f"{min((abs(o) for o in offsets), default=0) / 3600:.2f} ساعة"
      if offsets else "no offsets sampled")
check("offset: every clip played under the shifted clock",
      len(seen) == CLIPS, f"بدأ {len(seen)} من {CLIPS}")
check("offset: the log recorded each one",
      outcomes.count("completed") == CLIPS, outcomes)
check("offset: no tick errors along the way", not errs, errs[:1])

print()
print("RESULT: {} passed / {} failed".format(PASSED, FAILED))
sys.exit(1 if FAILED else 0)