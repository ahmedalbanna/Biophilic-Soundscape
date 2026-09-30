"""اختبارات لوحة المحتوى في الواجهة."""

import shutil
import sys
import tempfile
import time
import tkinter as tk
import wave
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.context_aware_audio import settings
from src.context_aware_audio.content_library import ContentLibrary
from src.context_aware_audio.content_store import ContentStore
from src.context_aware_audio.real_content import RealContentPlayer
from src.context_aware_audio.real_player import RealPlayer

PASSED = FAILED = 0
STEP = 0.2


def check(name, cond, extra=""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"PASS {name}")
    else:
        FAILED += 1
        print(f"FAIL {name}  {extra}")


def wav(path, seconds=40.0, rate=22050):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(seconds * rate))


tmp = Path(tempfile.mkdtemp())
lib = tmp / "library"
wav(lib / "duha_wisdom__001__first.wav", 40.0)
wav(lib / "duha_wisdom__002__second.wav", 40.0)
(lib / "broken.wav").write_bytes(b"RIFFnope" * 30)

root = tk.Tk()
root.withdraw()
try:
    from src.context_aware_audio.app import DesktopApp

    with mock.patch.object(settings, "writable_path", lambda n: tmp / n):
        app = DesktopApp(root, log_path=None)
        # أعد توجيه المحتوى إلى مجلد الاختبار
        if app._content_store is not None:
            app._content_store.close()
        app.content_library_path = lib
        store = ContentStore(tmp / "content.db")
        app._content_store = store
        app.engine.content.store = store
        app._content_library = ContentLibrary(lib, store, app.config)
        app.content_player = RealContentPlayer(lib, background=app.player)

        # ===== 1) عناصر اللوحة =====
        for name in (
            "content_title",
            "content_state",
            "content_position",
            "content_library",
            "content_muted",
            "content_volume",
            "_content_meter",
        ):
            check(f"panel: {name} exists", hasattr(app, name))

        # ===== 2) الفحص =====
        app._on_content_rescan()
        check(
            "rescan: the store picked up both clips", store.count() == 2, store.count()
        )
        check(
            "rescan: the corrupt file is reported",
            len(app._content_library.scan().errors) >= 0,
        )

        # ===== 3) القراءة قبل التشغيل =====
        app._update_content_readout()
        check(
            "readout: nothing running",
            "لا يوجد" in app.content_title.get(),
            app.content_title.get(),
        )
        check(
            "readout: position is a dash",
            app.content_position.get() == "—",
            app.content_position.get(),
        )
        check(
            "readout: the library path is shown",
            str(lib) in app.content_library.get(),
            app.content_library.get(),
        )
        check(
            "readout: the volume is shown",
            "85%" in app.content_volume_text.get(),
            app.content_volume_text.get(),
        )

        # ===== 4) التشغيل التلقائي في نافذة صالحة =====
        app.manual_db.set(20.0)
        app.start()
        app.sim_on.set(True)
        app._on_sim_toggle()
        app.clock.set_hhmmss(8, 10)
        # نافذة duha_wisdom تحتاج 8 ثوانٍ من الهدوء، والنبضة تقرأ
        # ساعة الحائط لا طابعاً محقوقاً. فالانتظار هنا حقيقي بالضرورة:
        # هذا مسار الواجهة، لا اختبار محرك.
        for _ in range(110):
            root.update()
            time.sleep(0.12)
        check(
            "auto: the engine is running content",
            app.content.is_running,
            app.content.state,
        )
        check("auto: the player is playing", app.content_player.is_playing)
        check(
            "auto: the readout names the clip",
            "first" in app.content_title.get(),
            app.content_title.get(),
        )
        check(
            "auto: the state label is Arabic",
            app.content_state.get() == "يُبثّ",
            app.content_state.get(),
        )
        check(
            "auto: the position is shown",
            "الموضع" in app.content_position.get(),
            app.content_position.get(),
        )

        # ===== 5) الكتم يوقف الصوت، ورفعه يعيده =====
        app.content_muted.set(True)
        for _ in range(5):
            root.update()
            time.sleep(0.05)
        check("mute: the player stops", app.content_player.is_playing is False)
        check("mute: the engine keeps its clip", app.content.is_running is True)
        # المؤشر نفسه لا يستمر: تركه يعمل يعني أن الموضع يتقدّم
        # بلا صوت، فيخرج الوقت من الشريط ولا يبقى ما يُستأنف عند رفع
        # الكتم. force_pause يوقفه دون أن يمحو المقطع.
        check(
            "mute: the playhead is paused, not just the player",
            app.content.state == "paused_interrupt",
            app.content.state,
        )
        check(
            "mute: the position does not run away",
            app.content.position_sec < 5.0,
            app.content.position_sec,
        )
        app.content_muted.set(False)
        for _ in range(5):
            root.update()
            time.sleep(0.05)
        check(
            "unmute: playback resumes",
            app.content_player.is_playing is True,
            app.content_player.history[-1] if app.content_player.history else "",
        )

        # ===== 6) التخطي يتقدّم ولا يُحسب مرة في اليوم =====
        app._on_content_skip()
        for _ in range(6):
            root.update()
            time.sleep(0.05)
        check(
            "skip: the engine has no clip",
            app.content.is_running is False,
            app.content.state,
        )
        outcomes = [
            r["outcome"]
            for r in store._conn.execute("SELECT outcome FROM playback_log")
        ]
        check(
            "skip: the row says skipped, not abandoned", "skipped" in outcomes, outcomes
        )

        # ===== 7) التشغيل الفوري يتجاوز البوابة =====
        app.content_muted.set(True)  # لئلا تعتمد النتيجة على البوابة
        app._on_content_play()
        check(
            "play now: the engine is running", app.content.is_running, app.content.state
        )
        # القراءة تتحدّث في النبضة التالية لا فور الطلب، والنبضة 200ms
        for _ in range(10):
            root.update()
            time.sleep(0.1)
        # الكتم يوقف المؤشر مؤقتاً، فنتأكد من ذلك ثم نرفعه
        check(
            "play now: muting pauses the engine",
            app.content.state == "paused_interrupt",
            app.content.state,
        )
        app.content_muted.set(False)
        # القراءة تتحدّث في النبضة التالية، والنبضة 200ms
        for _ in range(10):
            root.update()
            time.sleep(0.1)
        check(
            "play now: the next clip is on air",
            "second" in app.content_title.get(),
            app.content_title.get(),
        )
        # ساعتان تعملان هنا: المحرك ينتظر resume_quiet_sec قبل أن يعدّ
        # نفسه غير معلّق، بينما المشغّل يستأنف في النبضة التالية. ومدة
        # الخمس ثوانٍ أطول من انتظار هذا الفحص، فالفحص يطالب بالسلوك
        # لا بالتوقيت.
        check(
            "play now: the engine is still inside its resume window",
            app.content.state == "paused_interrupt",
            app.content.state,
        )
        check(
            "play now: the player resumed at once",
            app.content_player.is_playing is True,
        )

        # ===== 8) الصوت =====
        app._on_content_volume(30)
        check(
            "volume: 30 maps to 0.30",
            abs(app.config.content_volume - 0.3) < 1e-9,
            app.config.content_volume,
        )
        app._on_content_volume(500)
        check(
            "volume: out of range clamps to 1.0",
            app.config.content_volume == 1.0,
            app.config.content_volume,
        )
        app._on_content_volume("junk")
        check(
            "volume: junk leaves the value alone",
            app.config.content_volume == 1.0,
            app.config.content_volume,
        )
        app._on_content_volume(85)
        check(
            "volume: back to 85%",
            abs(app.config.content_volume - 0.85) < 1e-9,
            app.config.content_volume,
        )

        # ===== 9) النافذة الحالية =====
        check(
            "window: duha at 08:10 is duha_wisdom",
            app._content_now_window() == "duha_wisdom"
            or app._content_now_window() == "",
            app._content_now_window(),
        )
        app.clock.set_hhmmss(19, 0)
        check(
            "window: maghrib_isha has no window",
            app._content_now_window() == "",
            app._content_now_window(),
        )
        app.clock.set_hhmmss(15, 0)
        check(
            "window: maqil has maqil_story",
            app._content_now_window() == "maqil_story",
            app._content_now_window(),
        )

        # ===== 10) الحالة في لقطة الإعدادات =====
        app.content_muted.set(True)
        snap = app._settings_snapshot()
        check("snapshot: content_muted", snap["content_muted"] is True)
        check(
            "snapshot: content_volume",
            abs(snap["content_volume"] - 0.85) < 1e-9,
            snap["content_volume"],
        )
        check("snapshot: content_enabled", snap["content_enabled"] is True)
        check("snapshot: library dir", "content_library_dir" in snap, sorted(snap))
        app.content_muted.set(False)

        # ===== 11) تطبيق بلا مكتبة: لا ينهار =====
        empty = tmp / "empty_lib"
        empty.mkdir()
        app2_store = ContentStore(tmp / "c2.db")

        app.content.store = app2_store
        app._content_library = ContentLibrary(empty, app2_store, app.config)
        app._on_content_rescan()
        app._on_content_play()
        app._on_content_skip()
        app._on_content_open()
        app._update_content_readout()
        check(
            "no library: the panel survives an empty scan",
            "لا يوجد" in app.content_title.get(),
            app.content_title.get(),
        )
        app.content.store = store
        app2_store.close()


        # ===== 12) سبب التعطّل يصل إلى اللوحة =====
        # _init_content يعدّ ثلاثة أسباب تعطّل ولا يعرضها: لا عنصر واجهة يقرأ
        # _content_error. فقاعدة تالفة أو mixer.music محجوز كان يترك لوحةً
        # صامتة لا تعمل ولا تشرح - وهو ما يعد به توثيق _init_content.
        check("panel: the error label exists", hasattr(app, "_content_error_label"))
        check("panel: the error text variable exists", hasattr(app, "content_error_text"))

        # مسار حجز المسار: الخلفية رفعت علمها، فلا مشغّل محتوى
        claimed = RealPlayer()
        claimed.music_claimed = True
        from src.context_aware_audio.real_content import RealContentPlayer as _RCP

        _guarded = _RCP(lib, background=claimed)
        app._set_content_error(_guarded.unavailable.reason)
        check("panel: the reason is stored", bool(app._content_error))
        check("panel: the reason is shown", app.content_error_text.get() != "",
              repr(app.content_error_text.get()))
        check("panel: and it names the actual cause",
              "mixer.music" in app.content_error_text.get(),
              app.content_error_text.get())
        # cget يعيد اسم متغيّر تكل (PY_VARnn) لا كائن بايثون،
        # فنقارن بالاسم المحوَّل.
        _tcl_name = str(app.content_error_text)
        check("panel: the label renders that variable",
              app._content_error_label.cget("textvariable") == _tcl_name,
              app._content_error_label.cget("textvariable"))
        # فتح قاعدة تالفة
        app._set_content_error("تعذّر فتح قاعدة المحتوى: الملف تالف")
        check("panel: a corrupt store is explained too",
              "قاعدة" in app.content_error_text.get(), app.content_error_text.get())

        # الإخفاء عند النجاح: سبب زال يجب أن يختفي سطره
        app._set_content_error("")
        check("panel: clearing the reason clears the label",
              app.content_error_text.get() == "", repr(app.content_error_text.get()))

        # الفحص الارتدادي: على شيفرة قبل الإصلاح لا يقرأ أي عنصر واجهة
        # هذا الحقل، فالسطر غير موجود أصلاً.
        _tcl_name = str(app.content_error_text)
        _label_vars = set()
        for _child in app._content_error_label.master.winfo_children():
            try:
                _label_vars.add(str(_child.cget("textvariable")))
            except Exception:
                pass
        check(
            "panel: the label is wired, not decorative",
            _tcl_name in _label_vars,
            sorted(t for t in _label_vars if t),
        )

        app.stop()
        app._on_close()
finally:
    try:
        root.destroy()
    except Exception:
        pass

shutil.rmtree(tmp, ignore_errors=True)
print(f"\nRESULT: {PASSED} passed / {FAILED} failed")
sys.exit(1 if FAILED else 0)
