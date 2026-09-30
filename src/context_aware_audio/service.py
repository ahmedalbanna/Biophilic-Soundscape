"""
service.py - المتحكّم بلا واجهة

كل ما كان في DesktopApp من منطق، بلا Tk. الواجهة (كانت Tk، والآن
صفحة ويب) طبقة عرض فوق هذا: تقرأ `state()` وتستدعي الأوامر.

لماذا الوحدة: المنطق كان موزّعاً بين 1691 سطراً فيها متغيّرات
Tk，随时 «تُقرَأ» من想吃 وسم، وسمٌ لا وجود له قبل أن تبنيه الواجهة.
فالفحص كان يحتاج Tk ليختبر منطقاً لا علاقة له بـ Tk، وستة ملفات
اختبار كانت تُنشئ نافذة.

قاعدة صارمة: هذا الملف لا يستورد tkinter ولا يعرف существова.
"""

from __future__ import annotations

import os
import queue
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .audio_types import AudioFrame
from .config import EngineConfig
from .content_library import ContentLibrary
from .content_store import ContentStore
from .audio_types import ContentAction
from .content_sync import list_windows_why, sync_window
from .cultural_calendar import PERIOD_LABELS_AR
from .engine import ContextAwareAudioEngine
from .mic_input import MicInput
from .paths import content_db_path, content_library_dir
from .prayer_provider import load_today_times
from .real_content import RealContentPlayer
from .real_player import RealPlayer
from .settings import load_settings, save_settings, sync_token
from .sim_clock import SimClock
from .vad import VadProcessor

STEP_SEC = 0.2
SAVE_EVERY_TICKS = 25  # نبضة واحدة كل نحو 5 ثوانٍ
ATHAN_VOLUME = 0.5
LOG_TAIL = 300

# معادل كل فترة. تخفيضٌ لا رفع: ليل أخفض.
PERIOD_EQ = {
    "fajr_sabah": 1.0,
    "duha_work": 1.0,
    "lunch": 0.8,
    "maqil": 1.0,
    "maghrib_isha": 1.0,
    "samra": 1.0,
    "night_sleep": 0.6,
}

DUCK_PRESETS = (60.0, 70.0, 80.0)

# سيناريوهات الفحص السريع. قيم dB تُزرع مباشرةً.
SCENARIOS = {
    "debate": (70.0, True, True, 3.0, "نقاش حامي 70dB"),
    "welcome": (62.0, True, True, 3.0, "نبرة ترحيب"),
    "silence": (20.0, False, False, 12.0, "هدوء 12ث"),
    "talk": (45.0, True, False, 3.0, "كلام عادي"),
}


class AudioService:
    """
    المالك الوحيد للمحرّك والمشغّلات والميكروفون ومكتبة المحتوى.

    خيط نبضٍ واحد كل 200ms. `tick()` عامّة ومحقونة الساعتين، فالفحص
    تقودها بإطار واحد بدل انتظارِ حائطِ الساعة.
    """

    def __init__(self, log_path: Optional[Path] = None):
        self.log_path = log_path
        self._log_lines: List[str] = []
        self._log_lock = threading.Lock()

        # --- الإعدادات: نص واحد، وأنواعُها فيsettings.py ---
        self._settings: Dict[str, Any] = load_settings()

        self.config = EngineConfig()
        self.engine = ContextAwareAudioEngine(self.config)
        self.content = self.engine.content
        self.player = RealPlayer()
        self.mic = MicInput(device=self._settings.get("mic_device"))
        self.vad = VadProcessor(self.config)
        self.clock = SimClock(
            offset_sec=float(self._settings.get("sim_offset_sec", 0.0))
        )
        self.clock.set_enabled(bool(self._settings.get("sim_enabled", False)))

        # --- حقول تُقرأ وتُكتب من الواجهة ---
        self.master_vol = float(self._settings.get("master_vol", 100.0))
        self.master_muted = bool(self._settings.get("master_mute", False))
        self.duck_depth = float(self._settings.get("duck_depth", 70.0))
        self.athan_enabled = bool(self._settings.get("athan_enabled", True))
        self.use_mic = bool(self._settings.get("use_mic", False))
        self.content_enabled = bool(self._settings.get("content_enabled", True))
        self.content_muted = bool(self._settings.get("content_muted", False))
        self.content_volume = float(self._settings.get("content_volume", 0.85))
        self.content_sync_url = str(self._settings.get("content_sync_url", ""))
        self.content_sync_token = str(self._settings.get("content_sync_token", ""))
        self.content_sync_parts = int(self._settings.get("content_sync_max_parts", 20))
        self.library_override = str(self._settings.get("content_library_dir", ""))
        self.content_library_path = content_library_dir(self.library_override)

        # --- حالة حيّة ---
        self.running = False
        self.db_level = 0.0
        self.is_speech = False
        self.is_speech_raw = False
        self.state_text = ""
        self.period_text = ""
        self.next_prayer_text = ""
        self.content_text = "لا يوجد مقطع جارٍ"
        self.content_state = "—"
        self.content_position = "—"
        self.content_error = ""
        self.sync_text = ""
        self.sync_note = ""
        self._player_error = ""
        self._player_error_logged = False
        self._last_mute = False
        self._last_vol = -1.0
        self._last_eq = -1.0
        self._last_player = self.player
        self._save_counter = 0
        self._athan_played: set = set()
        self._prayer_date: Optional[datetime] = None
        self._prayer_error = ""
        self._calibrating = False
        self._calibrate_until = 0.0
        self._calibrate_db = 0.0
        self._calibrate_n = 0

        # --- طوابير الخيوط العاملة ---
        self._prayer_queue: "queue.Queue" = queue.Queue()
        self._sync_queue: "queue.Queue" = queue.Queue()
        self._sync_running = False
        self._sync_windows = [
            "fajr_dhikr",
            "duha_wisdom",
            "maqil_story",
            "evening_ethic",
        ]
        self._sync_window = self._sync_windows[0]
        self._sync_button_locked = False

        # --- سيناريو مثبَّت ---
        self._scenario_until = 0.0
        self._scenario_db_value = 0.0
        self._pcm_seq_used = -1
        self._pcm_error_logged = False

        self.config.duck_depth = self.duck_depth
        self.config.content_enabled = self.content_enabled
        self.config.content_volume = self.content_volume
        self._init_content()
        self._load_prayer_async()
        self._startup_banner()

    # ================================================================
    # السجل
    # ================================================================
    def log(self, msg: str) -> str:
        """سطر سجلّ واحد. اللبس في عدّه، فيقتطع الذيل."""
        with self._log_lock:
            self._log_lines.append(str(msg))
            if len(self._log_lines) > LOG_TAIL:
                del self._log_lines[: len(self._log_lines) - LOG_TAIL]
        return str(msg)

    def log_tail(self, n: int = 60) -> List[str]:
        with self._log_lock:
            return list(self._log_lines[-n:])

    def _startup_banner(self):
        from .sound_synth import missing_assets

        self.log(
            f"بدء التشغيل | مشغّل={self.player.backend} "
            f"ميكروفون={self.mic.backend if self.mic.available else 'غير متاح'}"
        )
        if self.log_path is not None:
            self.log(f"ملف السجل: {self.log_path}")
        absent = missing_assets()
        if absent:
            self.log(f"تحذير: {len(absent)} ملف صوتي ناقص: {', '.join(absent)}")

    # ================================================================
    # المحتوى
    # ================================================================
    def _init_content(self):
        self.content_store: Optional[ContentStore] = None
        self.content_player: Optional[RealContentPlayer] = None
        self.content_library: Optional[ContentLibrary] = None
        self._set_content_error("")
        try:
            self.content_store = ContentStore(content_db_path())
        except Exception as exc:
            self._set_content_error(f"تعذّر فتح قاعدة المحتوى: {exc}")
            return
        self.engine.content.store = self.content_store
        self.content_library = ContentLibrary(
            self.content_library_path, self.content_store, self.config
        )
        try:
            self.content_player = RealContentPlayer(
                self.content_library_path, background=self.player
            )
        except Exception as exc:
            self._set_content_error(f"تعذّر تهيئة مشغّل المحتوى: {exc}")
            return
        if not self.content_player.available:
            self._set_content_error(self.content_player.unavailable.reason)

    def _set_content_error(self, reason: str = "") -> None:
        self.content_error = reason

    def content_ready(self) -> bool:
        return self.content_library is not None and self.content is not None

    def _content_window_now(self) -> str:
        period = self.engine.calendar.period_for_datetime(self.clock.now())
        for key, spec in self.config.content_windows.items():
            if period in spec["periods"]:
                return key
        return ""

    def _content_tick(self, cmd):
        """
        ينفّذ قرار المحتوى على المشغّل. القرار يبقى في المحرك.

        cmd قد يكون None حين يكون المحرك متوقفاً، فنُبقي المسار
        كلّه. والكتم يعطّل المؤشر نفسه لا المشغّل وحده: تركه يعمل
        يعني أن الموضع يتقدّم بلا صوت، فلا يبقى عند رفع الكتم ما
        يُستأنف.
        """
        if self.content_player is None or cmd is None:
            return
        eng = self.content
        if self.content_muted or not self.content_enabled:
            if eng is not None and eng.is_running:
                eng.force_pause("كتم المحتوى")
            self.content_player.stop()
            return
        if eng is not None and eng.is_running and not self.content_player.is_playing:
            # رفع الكتم: المشغّل فقد الملف عند الإيقاف، فاستئناف بلا
            # ملف لا يفعل شيئاً. نعيد التشغيل عند موضع المحرك.
            self.content_player.apply(
                ContentAction.RESUME,
                eng.current_file,
                self.config.content_volume,
                eng.position_sec,
            )
        self.content_player.apply(
            cmd.content_action,
            cmd.content_file,
            cmd.content_volume or self.config.content_volume,
            cmd.content_position_sec,
        )

    def content_play_now(self) -> str:
        if not self.content_ready():
            return self.log("تشغيل فوري: المكتبة غير جاهزة")
        clip = self.content_store.next_for_window(self._content_window_now())
        if clip is None:
            return self.log("تشغيل فوري: لا مقطع في النافذة الحالية")
        if self.content.request_now(clip, self.clock.now()):
            return self.log(f"تشغيل فوري: {clip['title']}")
        return self.log("تشغيل فوري: تعذّر البدء")

    def content_skip(self) -> str:
        if not self.content_ready():
            return "المكتبة غير جاهزة"
        was = self.content.current_title
        self.content.abandon("تخطٍّ يدوي")
        return self.log(f"تخطّيت: {was}") if was else "لا مقطع جارٍ"

    def content_rescan(self) -> str:
        if self.content_library is None:
            return self.log("فحص المكتبة: غير مهيّأة")
        report = self.content_library.scan()
        if report.total:
            line = (
                f"فحص المكتبة: {report.total} مقطع "
                f"({report.added} جديد، {report.updated} محدَّث)"
            )
        elif report.is_empty:
            line = "فحص المكتبة: المجلد فارغ"
        else:
            line = f"فحص المكتبة: {report.total} مقطع"
        for err in report.errors[:3]:
            self.log(f"فحص المكتبة: {err}")
        return self.log(line)

    def content_open_folder(self) -> str:
        if self.content_library is None:
            return "المكتبة غير مهيّأة"
        path = self.content_library.ensure_root()
        try:
            os.startfile(str(path))
            return self.log(f"فُتح: {path}")
        except (OSError, AttributeError) as exc:
            return self.log(f"تعذّر فتح المجلد: {exc}")

    # ================================================================
    # المزامنة
    # ================================================================
    def sync_base(self) -> str:
        from .settings import _as_url

        typed = self.content_sync_url.strip()
        clean = _as_url(typed)
        if typed and clean != typed.rstrip("/"):
            self.sync_note = (
                "العنوان مرفوض: لا يبدأ بـ http:// أو https://"
                if not clean
                else f"استُخدم: {clean}"
            )
        elif clean:
            self.sync_note = ""
        return clean

    def sync_parts(self) -> int:
        try:
            value = int(float(self.content_sync_parts))
        except (TypeError, ValueError):
            value = 20
        return max(1, min(500, value))

    def sync_token_now(self) -> str:
        from .settings import TOKEN_ENV_VAR

        if os.environ.get(TOKEN_ENV_VAR, "").strip() and self.content_sync_token:
            self.sync_note = f"الرمز من متغيّر البيئة {TOKEN_ENV_VAR}، لا من الحقل"
        return sync_token(self.content_sync_token)

    def content_sync(self, window: Optional[str] = None) -> str:
        if self._sync_running:
            return self.log("المزامنة: جارية بالفعل")
        base = self.sync_base()
        if not base:
            self.sync_text = "لا يوجد خادم صالح"
            return self.log("المزامنة: لا يوجد خادم صالح")
        target = window or self._sync_window
        token = self.sync_token_now()
        dest = self.content_library_path
        cap = self.sync_parts()
        self._sync_running = True
        self._sync_button_locked = True
        self.sync_text = "جارٍ السحب..."

        def work():
            def on_progress(done_n, total_n):
                if total_n:
                    self._sync_queue.put(("progress", (done_n, total_n)))

            try:
                result = sync_window(
                    dest,
                    base,
                    target,
                    token,
                    max_parts=cap,
                    on_progress=on_progress,
                )
                self._sync_queue.put(("result", result))
            except Exception as exc:
                self._sync_queue.put(("error", str(exc)))

        threading.Thread(target=work, daemon=True).start()
        return "بدأ السحب"

    def sync_scan(self) -> str:
        base = self.sync_base()
        if not base:
            return "لا يوجد خادم صالح"
        token = self.sync_token_now()

        def work():
            found, why = list_windows_why(base, token)
            self._sync_queue.put(
                ("windows", ([w.get("window", "") for w in found], why))
            )

        threading.Thread(target=work, daemon=True).start()
        return "يبحث عن النوافذ"

    def _drain_sync_queue(self):
        while True:
            try:
                kind, payload = self._sync_queue.get_nowait()
            except queue.Empty:
                return
            try:
                if kind == "error":
                    self._sync_idle()
                    is_disk = isinstance(payload, OSError) or (
                        "content-sync-state" in str(payload)
                    )
                    self.sync_text = "فشل حفظ الحالة" if is_disk else "فشل الاتصال"
                    self.log(f"المزامنة: {payload}")
                elif kind == "progress":
                    done_n, total_n = payload
                    if total_n:
                        self.sync_text = f"جارٍ السحب... {done_n}/{total_n}"
                elif kind == "windows":
                    names, why = payload
                    if names:
                        self._sync_windows = names
                        self._sync_window = names[0]
                        self.sync_text = f"{len(names)} نافذة على الخادم"
                        self.log(f"المزامنة: {len(names)} نافذة على الخادم")
                    else:
                        self.sync_note = f"الفهرس: {why}"
                        self.log(f"المزامنة: تعذّر جلب الفهرس - {why}")
                else:
                    r = payload
                    self._sync_idle()
                    if r.cancelled:
                        self.sync_text = "أُلغيت"
                    elif r.ok:
                        if self.content_library is not None:
                            self.content_library.scan()
                        self.sync_text = (
                            f"{r.window}: {r.transferred} جديد، {r.skipped} موجود"
                        )
                        self.log(
                            f"المزامنة {r.window}: {r.transferred} منزَّل "
                            f"({r.verified} متحقَّق)، {r.skipped} موجود مسبقاً"
                        )
                    else:
                        self.sync_text = f"{r.window}: {r.failed} فشل"
                        self.log(f"المزامنة {r.window}: {r.failed} فشل")
                    for e in r.errors[:3]:
                        self.log(f"المزامنة: {e}")
            except Exception as exc:
                self.log(f"تعذّرت معالجة نتيجة المزامنة ({kind}): {exc}")
            finally:
                self._sync_idle()

    def _sync_idle(self):
        self._sync_running = False
        self._sync_button_locked = False

    # ================================================================
    # الإعدادات
    # ================================================================
    def settings_snapshot(self) -> Dict[str, Any]:
        return {
            "master_vol": self.master_vol,
            "master_mute": self.master_muted,
            "use_mic": self.use_mic,
            "mic_device": self.mic.device,
            "athan_enabled": self.athan_enabled,
            "sim_offset_sec": self.clock.offset_sec,
            "sim_enabled": bool(self.clock.enabled),
            "duck_depth": self.duck_depth,
            "content_enabled": self.content_enabled,
            "content_muted": self.content_muted,
            "content_volume": self.config.content_volume,
            "content_library_dir": self.library_override,
            "content_sync_url": self.content_sync_url,
            "content_sync_token": self.content_sync_token,
            "content_sync_max_parts": self.content_sync_parts,
        }

    def save_settings(self) -> bool:
        self._settings.update(self.settings_snapshot())
        return save_settings(self._settings)

    def update_settings(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        """تعديل جماعي. كل مفتاح يُنقّى إلى نوعه في settings.py."""
        from .settings import _COERCERS

        applied = {}
        for key, value in (patch or {}).items():
            if key not in _COERCERS:
                continue
            clean = _COERCERS[key](value)
            applied[key] = clean
        if not applied:
            return self.settings_snapshot()

        if "master_vol" in applied:
            self.master_vol = applied["master_vol"]
        if "master_mute" in applied:
            self.master_muted = applied["master_mute"]
        if "duck_depth" in applied:
            self.duck_depth = applied["duck_depth"]
            self.config.duck_depth = self.duck_depth
        if "athan_enabled" in applied:
            self.athan_enabled = applied["athan_enabled"]
        if "use_mic" in applied:
            self.use_mic = applied["use_mic"]
            if self.use_mic:
                self._start_mic()
            else:
                self.mic.stop()
        if "mic_device" in applied:
            self.mic.device = applied["mic_device"]
        if "content_enabled" in applied:
            self.content_enabled = applied["content_enabled"]
            self.config.content_enabled = self.content_enabled
        if "content_muted" in applied:
            self.content_muted = applied["content_muted"]
        if "content_volume" in applied:
            self.config.content_volume = applied["content_volume"]
        if "content_sync_url" in applied:
            self.content_sync_url = applied["content_sync_url"]
        if "content_sync_token" in applied:
            self.content_sync_token = applied["content_sync_token"]
        if "content_sync_max_parts" in applied:
            self.content_sync_parts = int(applied["content_sync_max_parts"])
        if "content_library_dir" in applied:
            new_dir = applied["content_library_dir"]
            if new_dir != self.library_override:
                self.library_override = new_dir
                self.content_library_path = content_library_dir(new_dir)
                self._init_content()
                self.log(f"تغيّر مجلد المكتبة: {self.content_library_path}")
        if "sim_enabled" in applied:
            self.clock.set_enabled(applied["sim_enabled"])
        if "sim_offset_sec" in applied:
            self.clock.offset_sec = applied["sim_offset_sec"]

        self.save_settings()
        return self.settings_snapshot()

    # ================================================================
    # الميكروفون
    # ================================================================
    def _start_mic(self) -> bool:
        if not self.mic.available:
            self.log("الميكروفون غير متاح")
            return False
        if self.mic.start():
            self.mic.stop_streaming = False
            self.log(f"الميكروفون: {self.mic.backend}")
            return True
        self.log("تعذّر فتح الميكروفون")
        return False

    def mic_calibrate(self) -> str:
        """معايرة 2ث صمت ترفع أرضية الضجيج فوق الغرفة."""
        if not (self.use_mic and self.mic.available):
            return self.log("المعايرة: الميكروفون غير مُفعَّل")
        self._calibrating = True
        self._calibrate_until = time.time() + 2.0
        self._calibrate_db = 0.0
        self._calibrate_n = 0
        return self.log("معايرة: اصمت ثانيتين")

    def mic_devices(self) -> list:
        try:
            return MicInput.list_devices()
        except Exception as exc:
            self.log(f"تعذّر سرد الأجهزة: {exc}")
            return []

    # ================================================================
    # المواقيت
    # ================================================================
    def _load_prayer_async(self):
        def work():
            try:
                self._prayer_queue.put(("ok", load_today_times(self.config)))
            except Exception as exc:
                self._prayer_queue.put(("error", str(exc)))

        threading.Thread(target=work, daemon=True).start()

    def prayer_reload(self) -> str:
        self._prayer_date = None
        self._athan_played.clear()
        self._load_prayer_async()
        return "يحدّث المواقيت"

    def _drain_prayer_queue(self):
        while True:
            try:
                kind, payload = self._prayer_queue.get_nowait()
            except queue.Empty:
                return
            if kind == "ok":
                # PrayerTimes NamedTuple فيه times و source. تمرير
                # الكائن كله إلى set_times يجعله يتوقّع قاموساً.
                self.engine.prayer.set_times(payload.times)
                self._prayer_date = datetime.now().date()
                self._prayer_error = ""
                self.log(f"مواقيت الصلاة: من {payload.source}")
                if payload.times:
                    self.log(
                        "  "
                        + " · ".join(
                            f"{k}={v.strftime('%H:%M')}"
                            for k, v in payload.times.items()
                        )
                    )
            else:
                self._prayer_error = str(payload)
                self.log(f"تعذّر جلب المواقيت: {payload} — استُخدمت التقديرية")

    # ================================================================
    # المحاكاة والسيناريو
    # ================================================================
    def sim_enable(self, on: bool) -> str:
        self.clock.set_enabled(bool(on))
        return f"محاكاة الوقت: {'مفعّلة' if on else 'معطّلة'}"

    def sim_set_time(self, hour: int, minute: int) -> str:
        self.clock.set_hhmmss(int(hour), int(minute))
        return f"الوقت المحاكى: {self.clock.now().strftime('%H:%M')}"

    def sim_nudge(self, hours: float) -> str:
        self.clock.nudge(hours)
        return f"إزاحة {hours:+.1f}س"

    def sim_reset(self) -> str:
        self.clock.reset()
        self.clock.set_enabled(False)
        return "عاد الزمن الحقيقي"

    def scenario(self, kind: str):
        """
        يزرع إطاراً لثوانٍ قليلة.

        بلا التثبيت كانت النبضة التالية تستبدله بإطار حقيقي، فلا يرى
        المستخدم شيئاً. والميكروفون يعمل بالخلفية، فمن ينظر إلى
        العدّاد لا إلى ما زرعه الزر.
        """
        if kind not in SCENARIOS:
            return f"سيناريو مجهول: {kind}"
        db, speech, greeting, hold, label = SCENARIOS[kind]
        self._scenario_until = 0.0
        self._scenario_db_value = 0.0
        now = self.clock.now()
        frame = self.vad.analyze_frame(db, timestamp=self.clock.real_now().timestamp())
        frame.is_speech = speech
        frame.is_overlapping = greeting and speech
        if greeting:
            frame.is_greeting_tone = True
        cmd = self.engine.process_frame(frame, now)
        self.player.apply(cmd)
        self.log(f"سيناريو: {label} (مثبَّت {hold:.0f}ث)")
        # الطابع زمن حقيقي: طابع من الساعة المحاكاة يجعل الإطار
        # المزروع في المستقبل باثنتي عشرة ساعة عند الضبط على 02:00.
        self._scenario_until = time.time() + hold
        self._scenario_db_value = frame.db_level
        return f"سيناريو: {label}"

    def _scenario_db(self) -> Optional[float]:
        if self._scenario_until and time.time() < self._scenario_until:
            return self._scenario_db_value
        self._scenario_until = 0.0
        return None

    def _current_db(self) -> float:
        if self._calibrating:
            return self._calibrate_db
        pinned = self._scenario_db()
        if pinned is not None:
            return pinned
        if self.use_mic and self.mic.available:
            db = self.mic.read_db()
            if db is not None:
                return db
        return float(self._manual_db())

    def _manual_db(self) -> float:
        """المستوى اليدوي حين لا ميكروفون. متغيّر، لا Tk."""
        return getattr(self, "_manual_db_value", 25.0)

    def set_manual_db(self, value: float) -> float:
        try:
            self._manual_db_value = max(0.0, min(100.0, float(value)))
        except (TypeError, ValueError):
            pass
        return self._manual_db_value

    def _analyze_tick_frame(self, db: float, real_now: datetime) -> AudioFrame:
        """
        يبني إطار النبضة، عبر PCM الحقيقي إن أمكن.

        ثلاثة مسارات بالترتيب:

        1. سيناريو مثبَّت ← `analyze_frame` حتماً. المزروع ليس صوتاً،
           فتمريره إلى محلّل PCM يجعله ينسب قراره لغرفة صامتة ويبطل
           إبطالَ webrtcvad الذي وُضع أصلاً لصدّ الكلام الزائف.
        2. كتلة PCM جديدة ← `analyze_pcm`.
        3. غير ذلك ← `analyze_frame`.
        """
        if self._scenario_db() is not None:
            return self.vad.analyze_frame(db, timestamp=real_now.timestamp())
        if not (self.use_mic and self.mic.available):
            return self.vad.analyze_frame(db, timestamp=real_now.timestamp())
        pcm, seq = self.mic.read_pcm()
        if not pcm or seq == self._pcm_seq_used:
            # لا بيانات، أو نفس الكتلة التي حُلّلت في النبضة السابقة:
            # إعادة تحليلها تجعل عدّاد التوقيت يظنّ أن الصوت يتقدّم
            # وهو واقف.
            self._pcm_seq_used = seq
            return self.vad.analyze_frame(db, timestamp=real_now.timestamp())
        self._pcm_seq_used = seq
        try:
            return self.vad.analyze_pcm(
                pcm,
                sample_rate=self.mic.sample_rate,
                timestamp=real_now.timestamp(),
            )
        except Exception as exc:
            if not self._pcm_error_logged:
                self._pcm_error_logged = True
                self.log(f"تحليل PCM تعذّر، عُدنا للطاقة: {exc}")
            return self.vad.analyze_frame(db, timestamp=real_now.timestamp())

    # ================================================================
    # النبضة
    # ================================================================
    def _maybe_play_athan(self, now: datetime):
        if not self.athan_enabled:
            return
        hit = self.engine.prayer.athan_moment(now)
        if hit is None:
            return
        key = f"{now.date().isoformat()}:{hit}"
        if key in self._athan_played:
            return
        self._athan_played.add(key)
        self.player.play_once("athan_chime.wav", volume=ATHAN_VOLUME)
        self.log(f"نغمة الأذان: {hit}")

    def _apply_output_settings(self, now: datetime):
        """
        يرفع الصوت الرئيسي والكتم ومعادل الفترة إلى المشغّل.

        لا نعيد دفع ما لم يتغيّر: كل دفعة تستهلك قفلاً في المشغّل،
        والتحريك المتكرر يُولّد خيوط تدرّج بلا فائدة.
        """
        if self.player is not self._last_player:
            self._last_player = self.player
            self._last_vol = -1.0
            self._last_eq = -1.0
        vol = float(self.master_vol) / 100.0
        if abs(vol - self._last_vol) >= 0.005:
            self.player.set_master_volume(vol)
            self._last_vol = vol
        if self.master_muted != self._last_mute:
            self.player.set_master_muted(self.master_muted)
            self._last_mute = self.master_muted
        period = self.engine.calendar.period_for_datetime(now)
        eq = PERIOD_EQ.get(period.value, 1.0)
        if eq != self._last_eq:
            self.player.set_eq(eq)
            self._last_eq = eq

    def _update_readouts(self, now, cmd, db: float):
        period = self.engine.calendar.period_for_datetime(now)
        self.period_text = (
            f"{PERIOD_LABELS_AR.get(period, period.value)} ({period.value})"
        )
        self.state_text = f"{cmd.state.value} - {cmd.reason}" if cmd else ""
        if self._player_error:
            self.state_text = f"بلا صوت: {self._player_error[:40]} | {self.state_text}"
        self.db_level = db
        nxt = self.engine.prayer.next_prayer(now)
        if nxt is not None:
            mins = max(0, int((nxt.adhan - now).total_seconds() // 60))
            name = "الجمعة" if nxt.name == "dhuhr" and now.weekday() == 4 else nxt.name
            self.next_prayer_text = (
                f"{name} {nxt.adhan.strftime('%H:%M')} (بعد {mins} د)"
            )
        # محتوى
        eng = self.content
        pct = 0.0
        if eng is not None and eng.is_running:
            if eng.duration_sec > 0:
                pct = max(0.0, min(100.0, eng.position_sec * 100.0 / eng.duration_sec))
            self.content_text = f"جارٍ: {eng.current_title}"
            self.content_state = {
                "playing": "يُبثّ",
                "paused_interrupt": "معلّق بالمقاطعة",
                "waiting_quiet": "بانتظار هدوء الغرفة",
                "postponed": "مؤجَّلة لليوم",
            }.get(eng.state, eng.state)
            self.content_position = (
                f"{eng.position_sec:.0f} / {eng.duration_sec:.0f} ث ({pct:.0f}%)"
            )
        else:
            self.content_text = "لا يوجد مقطع جارٍ"
            self.content_state = "—"
            self.content_position = "—"

    def _maybe_save_settings(self):
        self._save_counter += 1
        if self._save_counter < SAVE_EVERY_TICKS:
            return
        self._save_counter = 0
        self.save_settings()

    def tick(
        self, now: Optional[datetime] = None, real_now: Optional[datetime] = None
    ) -> Optional[Any]:
        """
        نبضة واحدة.

        محقونة الساعتين: `now` قد يكون مزيفاً ليراه المحرك فيقرر
        (الفترة، نافذة الصلاة)، و`real_now` حقيقي لطوابع الإطارات.
        لو استُعملت الساعة المحاكاة للطوابع لأمكن فكّ مؤقّتات المحرك
        كلها بقفزة واحدة.
        """
        try:
            self._drain_prayer_queue()
            now = now or self.clock.now()
            real_now = real_now or self.clock.real_now()
            if self._prayer_date is not None and real_now.date() != self._prayer_date:
                self._prayer_date = None
                self._athan_played.clear()
                self._load_prayer_async()
            if self.running:
                self._maybe_play_athan(now)
            db = self._current_db()
            self._apply_output_settings(now)
            self._finish_calibration()
            auto = self._analyze_tick_frame(db, real_now)
            self.is_speech = auto.is_speech
            self.is_speech_raw = auto.is_speech_raw
            cmd = None
            if self.running:
                cmd = self.engine.process_frame(auto, now)
                try:
                    self.player.apply(cmd)
                except Exception as e:
                    self._player_error = str(e)
                    if not self._player_error_logged:
                        self.log(f"عطل في التشغيل: {e}")
                        self._player_error_logged = True
                else:
                    self._player_error_logged = False
                    self._player_error = ""
                self._update_readouts(now, cmd, auto.db_level)
            self._content_tick(cmd)
            self._drain_sync_queue()
            self._maybe_save_settings()
            return cmd
        except Exception as e:
            self.log(f"خطأ في النبضة: {e}")
            return None

    def _finish_calibration(self):
        if not self._calibrating:
            return
        if time.time() < self._calibrate_until:
            db = self.mic.read_db() or 0.0
            self._calibrate_db += db
            self._calibrate_n += 1
            return
        if self._calibrate_n:
            floor = self._calibrate_db / self._calibrate_n
            self.mic.noise_floor_db = floor
            self.log(f"معايرة: أرضية {floor:.1f}dB")
        self._calibrating = False
        self._calibrate_db = 0.0
        self._calibrate_n = 0

    # ================================================================
    # الحياة
    # ================================================================
    def start_engine(self) -> str:
        if self.running:
            return "يعمل"
        self.running = True
        if self.use_mic and self.mic.available:
            self._start_mic()
        self.log(f"بدأ التشغيل (مشغّل={self.player.backend})")
        return "بدأ"

    def stop_engine(self) -> str:
        self.running = False
        self.mic.stop()
        self.player.stop()
        self.log("متوقف")
        return "توقف"

    def shutdown(self):
        try:
            self.save_settings()
        except OSError:
            pass
        try:
            self.stop_engine()
        except Exception as exc:
            print(f"shutdown error: {exc}")
        if self.content_store is not None:
            try:
                self.content_store.close()
            except Exception:
                pass

    # خيط النبضة
    def _run_forever(self, stop_event: threading.Event):
        while not stop_event.is_set():
            self.tick()
            time.sleep(STEP_SEC)

    def start_background(self) -> threading.Thread:
        stop_event = threading.Event()
        thread = threading.Thread(
            target=self._run_forever, args=(stop_event,), daemon=True
        )
        thread.start()
        self._stop_event = stop_event
        return thread

    def stop_background(self):
        stop_event = getattr(self, "_stop_event", None)
        if stop_event is not None:
            stop_event.set()

    # ================================================================
    # اللقطة
    # ================================================================
    def state(self) -> Dict[str, Any]:
        """
        لقطة كاملة يرسم منها أي واجهة.

        مصدر واحد: لا تحسب الواجهة شيئاً ولا تختلق قيمة. هذا ما يجعل
        استبدال الواجهة تغييراً في طبقة العرض لا في المنطق.
        """
        now = self.clock.now()
        period = self.engine.calendar.period_for_datetime(now)
        return {
            "now": {
                "sim": self.clock.now().strftime("%H:%M:%S"),
                "real": self.clock.real_now().strftime("%H:%M:%S"),
                "sim_enabled": bool(self.clock.enabled),
                "offset_sec": round(self.clock.offset_sec, 1),
            },
            "engine": {
                "running": self.running,
                "state": self.state_text,
                "period": self.period_text,
                "period_key": period.value,
                "db": round(self.db_level, 1),
                "is_speech": bool(self.is_speech),
                "is_speech_raw": bool(self.is_speech_raw),
                "next_prayer": self.next_prayer_text,
                "player_backend": self.player.backend,
                "player_error": self._player_error,
                "athan_enabled": self.athan_enabled,
            },
            "audio": {
                "master_vol": self.master_vol,
                "master_mute": self.master_muted,
                "duck_depth": self.duck_depth,
                "duck_presets": list(DUCK_PRESETS),
                "athan_enabled": self.athan_enabled,
            },
            "mic": {
                "use_mic": self.use_mic,
                "available": self.mic.available,
                "backend": self.mic.backend,
                "device": self.mic.device,
                "noise_floor_db": self.mic.noise_floor_db,
                "calibrating": self._calibrating,
                "manual_db": round(self._manual_db(), 1),
            },
            "content": {
                "enabled": self.content_enabled,
                "muted": self.content_muted,
                "volume": self.config.content_volume,
                "title": self.content_text,
                "state": self.content_state,
                "position": self.content_position,
                "error": self.content_error,
                "library": str(self.content_library_path),
                "count": (
                    self.content_store.count() if self.content_store is not None else 0
                ),
                "window_now": self._content_window_now(),
            },
            "sync": {
                "url": self.content_sync_url,
                "token_set": bool(self.sync_token_now()),
                "max_parts": self.content_sync_parts,
                "window": self._sync_window,
                "windows": list(self._sync_windows),
                "text": self.sync_text,
                "note": self.sync_note,
                "running": self._sync_running,
            },
            "prayer": {
                "error": self._prayer_error,
            },
        }
