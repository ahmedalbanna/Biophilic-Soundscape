"""
app.py - تطبيق سطح المكتب الحقيقي (Tkinter)
تشغيل: python -m src.context_aware_audio.app
يعمل على Windows بدون مكتبات جديدة: صوت حقيقي عبر winsound،
ميكروفون اختياري، مواقيت حقيقية عبر Aladhan.
"""

import os
import queue
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

try:
    import tkinter as tk
    from tkinter import ttk
except Exception:
    tk = None

from src.context_aware_audio import ContextAwareAudioEngine, EngineConfig
from src.context_aware_audio.audio_types import AudioFrame
from src.context_aware_audio.cultural_calendar import PERIOD_LABELS_AR
from src.context_aware_audio.mic_input import MicInput
from src.context_aware_audio.prayer_provider import (
    describe_source,
    describe_times,
    load_today_times,
)
from src.context_aware_audio.real_player import RealPlayer
from src.context_aware_audio.settings import load_settings, save_settings
from src.context_aware_audio.sim_clock import SimClock
from src.context_aware_audio.sound_synth import ensure_assets, missing_assets
from src.context_aware_audio.vad import VadProcessor


# ثوابت الواجهة - لا أرقام مبثوثة في الكود
TICK_MS = 200  # الفترة بين نبضات حلقة التحديث
SAVE_EVERY_TICKS = 25  # نبضة واحدة كل ~5 ثوانٍ
METER_H = 22  # ارتفاع شريط قياس الـ dB
CALIBRATE_SEC = 2.0  # مدة قياس ضجيج الغرفة
ATHAN_VOLUME = 0.8  # مستوى نغمة تنبيه الأذان
# عمق الخفض: نسبة أقصى خفض عند حدّ الكلام. ثلاثة مستويات
# مبنية على ما يفعله المستخدم فعلاً، لا على أرقام مثالية.
#   خفيف 60% - قراءة هادئة، جار يهمس
#   عادي 70% - محادثة عادية (الافتراضي، سلوك المنحنى الأصلي)
#   عميق 80% - متحدث واحد بصوت عالٍ أو تلفاز
# لا يوجد عمق 90%: عند 65dB يصبح الكتم تاماً (نقاش حامي)،
# فلا أحد ينطق 90% في هذا المشروع.
DUCK_PRESETS = (60.0, 70.0, 80.0)
DUCK_PRESET_LABELS = {60.0: "خفيف", 70.0: "عادي", 80.0: "عميق"}

SIM_HINT = (
    "الأزمنة تعمل زمناً حقيقياً (لا تقفز أسبوع): 10ث هدوء، 60س نقاش، 5د سكون."
    + chr(10)
    + "لعرض «الليل» اضبط dB على صفر وانتظر 5 دقائق."
)


AUTOSTART_MARKER = "rem context-aware-audio-autostart"


def startup_bat_path() -> Path:
    """مسار ملف bat في مجلد Startup الخاص بـ Windows."""
    base = os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))
    return (
        Path(base)
        / "Microsoft"
        / "Windows"
        / "Start Menu"
        / "Programs"
        / "Startup"
        / "context_audio_autostart.bat"
    )


def _is_our_bat(path: Path) -> bool:
    """
    هل الملف bat كتبناه نحن؟

    نتحقق من سطر علامة فريد خاص بنا. `@echo off` سطر شائع جداً فلا يصلح
    دليلاً على الملكية - يحذف التطبيق ملفات الآخرين أو يدّعيها.
    """
    try:
        return AUTOSTART_MARKER in path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False


def is_autostart() -> bool:
    try:
        path = startup_bat_path()
        return path.exists() and _is_our_bat(path)
    except OSError:
        return False


def _launch_command() -> list:
    """
    وسيطات تشغيل التطبيق (خام، بلا اقتباس).

    نستخدم sys.executable دائماً بدل "python": الأخير قد لا يكون على PATH
    وقت تسجيل الدخول، فيفشل التشغيل التلقائي بصمت. الاقتباس والتهريب
    يحدثان عند الكتابة فقط - التهريب هنا كان يُنتج `^"` فيكسر الأمر.
    """
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, "-m", "src.context_aware_audio.app"]


def _escape_bat_text(value: str) -> str:
    """
    يهرّب محارف ذات معنى في cmd.exe.

    `%` للتوسيع، و`^` و`&` و`|` و`<` و`>` للتحكم، و`!` للتأخير عند
    expansions مفعّلة. لا نهرّب `"`: المسارات على ويندوز لا تحويها أصلاً،
    وتهريبها يُنتج `^"` الذي يمرّر علامة اقتباس حرفية للبرنامج.
    """
    out = value.replace("%", "%%")
    for ch in ("^", "&", "|", "<", ">", "!"):
        out = out.replace(ch, "^" + ch)
    return out


def _bat_arg(value: str) -> str:
    """وسطة واحدة مقتبسة ومهزّبة، صالحة للسطر في cmd.exe."""
    return f'"{_escape_bat_text(value)}"'


def set_autostart(on: bool) -> bool:
    """
    يكتب (أو يحذف) ملف bat في مجلد Startup الخاص بويندوز.

    ملاحظة مهمة: عند التجميد مع PyInstaller يكون `parents[2]`
    مجلداً مؤقتاً يُحذف عند إغلاق البرنامج، فلا يصح الانتقال إليه.
    لذلك نستخدم مسار المجلد الصريح في الحالتين.
    """
    try:
        target = startup_bat_path()
        if not on:
            if not target.exists():
                return True
            if not _is_our_bat(target):
                # لا نحذف ملفاً لم نكتبه نحن، ولا ندّعي أننا أوقفنا شيئاً.
                # False تعني للمستخدم أن التشغيل التلقائي ما زال فعالاً.
                print(f"autostart: {target} ليس ملفنا (لا يحتوي العلامة) - لم يُحذف")
                return False
            target.unlink(missing_ok=True)
            return True
        target.parent.mkdir(parents=True, exist_ok=True)
        if getattr(sys, "frozen", False):
            work_dir = str(Path(sys.executable).parent)
        else:
            work_dir = str(Path(__file__).resolve().parents[2])
        command = " ".join(_bat_arg(part) for part in _launch_command())
        target.write_text(
            "@echo off\n"
            f"{AUTOSTART_MARKER}\n"
            "chcp 65001 >nul\nset PYTHONUTF8=1\n"
            f"cd /d {_bat_arg(work_dir)}\n{command}\n",
            encoding="utf-8",
        )
        return True
    except OSError:
        return False


class DesktopApp:
    def __init__(self, root, log_path: Optional[Path] = None):
        self.root = root
        self.root.title("Context-Aware Audio Engine - Sanaa Desktop")
        self.root.geometry("660x960")
        self.log_path = log_path

        self.config = EngineConfig()
        self.engine = ContextAwareAudioEngine(self.config)
        self.vad = VadProcessor(self.config)
        self.player = RealPlayer()
        self._settings = load_settings()
        self.clock = SimClock(offset_sec=self._settings.get("sim_offset_sec", 0.0))
        self.clock.set_enabled(bool(self._settings.get("sim_enabled", False)))
        self.mic = MicInput(device=self._settings.get("mic_device"))
        self._mic_devices = MicInput.list_devices()

        self.running = False
        self.use_mic = tk.BooleanVar(value=bool(self._settings.get("use_mic")))
        self.manual_db = tk.DoubleVar(value=25.0)
        self.greeting = tk.BooleanVar(value=False)
        # نحفظ حالة التفعيل أيضاً: إزاحة محفوظة بلا علم بها تقفز
        # فجأة عند أول ضغطة على المربع.
        self.sim_on = tk.BooleanVar(
            value=bool(self._settings.get("sim_enabled", False))
        )
        self.sim_readout = tk.StringVar(value="-")
        self.sim_hour = tk.IntVar(value=self.clock.now().hour)
        self.sim_minute = tk.IntVar(value=self.clock.now().minute)
        self._updating = False  # يمنع إعادة الدخول عند تحديث الحقول برمجياً
        self.prayer_source = tk.StringVar(value="...")
        self.status_text = tk.StringVar(value="متوقف")
        self.period_text = tk.StringVar(value="-")
        self.state_text = tk.StringVar(value="-")
        self.prayer_text = tk.StringVar(value="جاري تحميل المواقيت...")
        self.next_text = tk.StringVar(value="-")
        self._prayer_queue = queue.Queue()
        self._prayer_date = None
        self._athan_played: set = set()
        self._scenario_until = 0.0
        self._scenario_db_value = 0.0
        self.athan_enabled = tk.BooleanVar(
            value=bool(self._settings.get("athan_enabled", True))
        )
        self.master_vol = tk.DoubleVar(
            value=float(self._settings.get("master_vol", 100.0))
        )
        self.duck_depth = tk.DoubleVar(
            value=float(self._settings.get("duck_depth", 70.0))
        )
        # الإعداد يمرّ مرة واحدة: نحوّله إلى إعدادات المحرك، ومنها
        # engine.duck_max_ratio. مصدر واحد لا قيمتان متناقضتان.
        self.config.duck_depth = float(self.duck_depth.get())
        self.master_mute = tk.BooleanVar(
            value=bool(self._settings.get("master_mute", False))
        )
        self._last_mute = False
        self._last_vol = -1.0
        self._last_eq = -1.0
        self._last_player = self.player
        self._player_error_logged = False
        self._player_error = ""
        self.autostart = tk.BooleanVar(value=is_autostart())
        self._save_counter = 0
        self.period_eq = {
            "fajr_sabah": 1.0,
            "duha_work": 1.0,
            "lunch": 0.8,
            "maqil": 1.0,
            "maghrib_isha": 1.0,
            "samra": 1.0,
            "night_sleep": 0.6,
        }

        self._build_ui()
        self._load_prayer_async()
        try:
            ensure_assets(force=False)
        except OSError as e:
            self._log(f"تعذر تجهيز الأصوات: {e}")
        self.player.set_master_volume(float(self.master_vol.get()) / 100.0)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._log_startup_banner()
        self._tick()

    def _log_startup_banner(self):
        """يسجّل حالة بدء التشغيل - أول ما يُقرأ عند تشخيص أي خلل."""
        self._log(
            f"بدء التشغيل | مشغّل={self.player.backend} "
            f"ميكروفون={self.mic.backend if self.mic.available else 'غير متاح'} "
            f"أجهزة إدخال={len(self._mic_devices)}"
        )
        if self.log_path is not None:
            self._log(f"ملف السجل: {self.log_path}")
        # الأصول الناقصة = صمت غير مفسر. أبلغ هنا لأن هذه أول سجل يُقرأ.
        absent = missing_assets()
        if absent:
            self._log(f"تحذير: {len(absent)} ملف صوتي ناقص: {', '.join(absent)}")

    def _on_close(self):
        try:
            self._save_settings()
        except OSError:
            pass
        try:
            self.stop()
        except Exception as e:
            # إيقاف الأخطاء يجب ألّا يمنع إغلاق النافذة
            print(f"close error: {e}")
        self.root.destroy()

    def _settings_snapshot(self) -> dict:
        """يجمع الإعدادات الحالية من عناصر الواجهة."""
        return {
            "master_vol": float(self.master_vol.get()),
            "master_mute": bool(self.master_mute.get()),
            "use_mic": bool(self.use_mic.get()),
            "mic_device": self.mic.device,
            "athan_enabled": bool(self.athan_enabled.get()),
            "sim_offset_sec": self.clock.offset_sec,
            "sim_enabled": bool(self.clock.enabled),
            "duck_depth": float(self.duck_depth.get()),
        }

    def _save_settings(self) -> bool:
        """يحفظ الإعدادات إلى القرص (يحدّث النسخة المحلية أيضاً)."""
        self._settings.update(self._settings_snapshot())
        return save_settings(self._settings)

    def _build_ui(self):
        f = ttk.Frame(self.root, padding=10)
        f.pack(fill="both", expand=True)

        ttk.Label(
            f, text="محرك الصوت التكيفي - صنعاء", font=("Segoe UI", 14, "bold")
        ).pack()
        ttk.Label(f, textvariable=self.status_text, font=("Segoe UI", 10)).pack(pady=2)

        row = ttk.Frame(f)
        row.pack(fill="x", pady=4)
        self.btn_start = ttk.Button(row, text="تشغيل", command=self.start)
        self.btn_start.pack(side="left", padx=4)
        ttk.Button(row, text="إيقاف", command=self.stop).pack(side="left", padx=4)
        ttk.Button(row, text="تحديث المواقيت", command=self._load_prayer_async).pack(
            side="right", padx=4
        )

        info = ttk.LabelFrame(f, text="الحالة الحية", padding=8)
        info.pack(fill="x", pady=6)
        ttk.Label(info, textvariable=self.period_text, font=("Segoe UI", 11)).pack(
            anchor="w"
        )
        ttk.Label(info, textvariable=self.state_text, font=("Segoe UI", 10)).pack(
            anchor="w"
        )
        ttk.Label(info, textvariable=self.next_text, font=("Segoe UI", 10)).pack(
            anchor="w"
        )
        self.meter = tk.Canvas(info, height=METER_H, bg="#eee")
        self.meter.pack(fill="x", pady=4)
        self.db_label = ttk.Label(info, text="dB: 0")
        self.db_label.pack(anchor="w")

        self._build_sim_panel(f)

        src = ttk.LabelFrame(f, text="مصدر الصوت", padding=8)
        src.pack(fill="x", pady=6)
        mic_state = (
            "متاح" if self.mic.available else "غير متاح (ثبّت sounddevice للميكروفون)"
        )
        ttk.Checkbutton(
            src,
            text=f"ميكروفون حقيقي ({mic_state})",
            variable=self.use_mic,
            state="normal" if self.mic.available else "disabled",
        ).pack(anchor="w")
        if self._mic_devices:
            dev_names = [f"{i}: {n}" for i, n in self._mic_devices]
            self.dev_combo = ttk.Combobox(
                src, values=dev_names, state="readonly", width=60
            )
            cur = self.mic.device
            sel = 0
            for idx, (i, _n) in enumerate(self._mic_devices):
                if i == cur:
                    sel = idx
                    break
            self.dev_combo.current(sel)
            self.dev_combo.pack(anchor="w", pady=2)
            self.dev_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_device())
        else:
            self.dev_combo = None
        ttk.Label(src, text="مستوى يدوي (عند غياب الميكروفون):").pack(anchor="w")
        ttk.Scale(
            src, from_=0, to=90, variable=self.manual_db, orient="horizontal"
        ).pack(fill="x")
        ttk.Checkbutton(
            src, text="نبرة ترحيب (ضيوف: ارحبوا!)", variable=self.greeting
        ).pack(anchor="w")
        self.cal_label = ttk.Label(src, text="أرضية الضجيج: غير معايرة")
        self.cal_label.pack(anchor="w")
        ttk.Button(
            src, text="معايرة الميكروفون (2ث صمت)", command=self._calibrate
        ).pack(anchor="w", pady=2)

        scen = ttk.LabelFrame(f, text="سيناريوهات سريعة", padding=8)
        scen.pack(fill="x", pady=6)
        btns = ttk.Frame(scen)
        btns.pack(fill="x")
        ttk.Button(
            btns, text="نقاش حامي", command=lambda: self._scenario("debate")
        ).pack(side="left", padx=3)
        ttk.Button(btns, text="ترحيب", command=lambda: self._scenario("welcome")).pack(
            side="left", padx=3
        )
        ttk.Button(
            btns, text="هدوء 11ث", command=lambda: self._scenario("silence")
        ).pack(side="left", padx=3)
        ttk.Button(btns, text="كلام عادي", command=lambda: self._scenario("talk")).pack(
            side="left", padx=3
        )

        pray = ttk.LabelFrame(f, text="مواقيت صنعاء", padding=8)
        pray.pack(fill="x", pady=6)
        ttk.Label(
            pray, textvariable=self.prayer_text, wraplength=580, font=("Segoe UI", 9)
        ).pack(anchor="w")

        out = ttk.LabelFrame(f, text="الإخراج", padding=8)
        out.pack(fill="x", pady=6)
        ttk.Label(out, text="الصوت الرئيسي:").pack(anchor="w")
        ttk.Scale(
            out, from_=0, to=100, variable=self.master_vol, orient="horizontal"
        ).pack(fill="x")
        ttk.Checkbutton(out, text="كتم رئيسي", variable=self.master_mute).pack(
            anchor="w"
        )

        ttk.Label(out, text="عمق الخفض أثناء الكلام:").pack(anchor="w")
        ttk.Scale(
            out, from_=0, to=95, variable=self.duck_depth, orient="horizontal"
        ).pack(fill="x")
        self.duck_depth.trace_add("write", self._on_duck_depth)
        prow = ttk.Frame(out)
        prow.pack(fill="x", pady=(2, 0))
        for _d in DUCK_PRESETS:
            ttk.Button(
                prow,
                text=DUCK_PRESET_LABELS.get(_d, f"{_d:.0f}%"),
                command=lambda d=_d: self._on_duck_preset(d),
            ).pack(side="left", padx=2)
        ttk.Label(
            prow,
            text="90% لا تتحقق: عند 65dB يصبح الكتم تاماً",
            foreground="#666",
            font=("Segoe UI", 8),
        ).pack(side="left", padx=8)
        ttk.Checkbutton(
            out, text="تنبيه لحظة الأذان", variable=self.athan_enabled
        ).pack(anchor="w")
        ttk.Checkbutton(
            out,
            text="بدء مع Windows",
            variable=self.autostart,
            command=self._on_autostart,
        ).pack(anchor="w")

        logf = ttk.LabelFrame(f, text="السجل", padding=8)
        logf.pack(fill="both", expand=True, pady=6)
        self.log = tk.Text(logf, height=8, font=("Consolas", 9))
        self.log.pack(fill="both", expand=True)

    def _on_device(self):
        try:
            sel = self.dev_combo.current()
            dev_id = self._mic_devices[sel][0]
            was_running = self.mic.is_running
            self.mic.stop()
            self.mic.device = dev_id
            if was_running:
                self.mic.start()
            self._save_settings()
            self._log(f"mic device -> {dev_id}")
        except (IndexError, OSError) as e:
            self._log(f"mic device error: {e}")

    # ---------- presets عمق الخفض ----------
    def _on_duck_depth(self, *_args):
        """عمق الخفض يُقرأ من إعداد المحرك، لا من حقل منفصل."""
        try:
            self.config.duck_depth = float(self.duck_depth.get())
        except (TypeError, ValueError, tk.TclError):
            self.duck_depth.set(self.config.duck_depth)

    def _on_duck_preset(self, depth: float):
        """يختار عمقاً جاهزاً ويشرح ما يعنيه: متحدث واحد أم حشد."""
        self.duck_depth.set(depth)
        self._on_duck_depth()
        label = DUCK_PRESET_LABELS.get(depth, f"{depth:.0f}%")
        self._log(f"عمق الخفض: {label}")
        self._log(f"  أقصى خفض {depth:.0f}% عند حدّ الكلام، "
                  f"و{100 - (1 - self.engine.duck_min_ratio()) * 100:.0f}% "
                  f"عند 64dB")

    def _on_autostart(self):
        wanted = bool(self.autostart.get())
        ok = set_autostart(wanted)
        if not ok:
            # الفشل يجب أن يُعاد إلى الواجهة: وإلا بقي المؤشر خاطئاً
            self.autostart.set(is_autostart())
        state = "on" if is_autostart() else "off"
        self._log(f"autostart={state} (طلب={'on' if wanted else 'off'}، نجح={ok})")

    def _build_sim_panel(self, parent):
        """
        لوحة محاكاة الوقت.

        الحقول مدخلات لا مخرجات: قيمها تُكتب عند الضبط فقط. تُربط
        بـ trace لأن command في Spinbox لا يُستدعى عند الكتابة اليدوية.
        والحارس ضروري لأن trace يُستدعى أيضاً على الكتابة البرمجية،
        فبدونه يعيد إدخاله نفسه ويصارع الدقيقة.
        """
        box = ttk.LabelFrame(parent, text="تبديل الوقت (تجربة)", padding=8)
        box.pack(fill="x", pady=6)

        ttk.Checkbutton(
            box,
            text="تدبيل الوقت (تجربة)",
            variable=self.sim_on,
            command=self._on_sim_toggle,
        ).pack(anchor="w")

        ttk.Label(box, textvariable=self.sim_readout, font=("Consolas", 9)).pack(
            anchor="w", pady=(4, 2)
        )

        row = ttk.Frame(box)
        row.pack(fill="x", pady=2)
        ttk.Label(row, text="الساعة").pack(side="left", padx=(0, 4))
        hbox = ttk.Spinbox(row, from_=0, to=23, width=4, textvariable=self.sim_hour)
        hbox.pack(side="left")
        ttk.Label(row, text="الدقيقة").pack(side="left", padx=(10, 4))
        mbox = ttk.Spinbox(row, from_=0, to=59, width=4, textvariable=self.sim_minute)
        mbox.pack(side="left")

        # trace بدل command: يلتقط الكتابة اليدوية والأسهم معاً
        self.sim_hour.trace_add("write", self._on_sim_time)
        self.sim_minute.trace_add("write", self._on_sim_time)

        nav = ttk.Frame(box)
        nav.pack(fill="x", pady=4)
        ttk.Button(nav, text="-1 س", command=lambda: self._on_sim_nudge(-1)).pack(
            side="left", padx=2
        )
        ttk.Button(nav, text="+1 س", command=lambda: self._on_sim_nudge(1)).pack(
            side="left", padx=2
        )
        ttk.Button(nav, text="عودة للوقت الحقيقي", command=self._on_sim_reset).pack(
            side="left", padx=2
        )

        ttk.Label(
            box,
            text=SIM_HINT,
            font=("Segoe UI", 8),
            foreground="#666",
            justify="left",
        ).pack(anchor="w", pady=(4, 0))

    def _log(self, msg: str):
        """يكتب سطراً في لوحة السجل، وفي ملف السجل عبر stdout.

        مهم في وضع exe: اللوحة تختفي عند الإغلاق، والملف يبقى.
        الاستثناءات هنا مقبولة لأن السجل ميزة عرض - يجب ألّا يُسقط حلقة
        التشغيل. أما خطأ الكتابة نفسه فيُبلَّغ إلى الطرفية.
        """
        line = f"{datetime.now().strftime('%H:%M:%S')} {msg}"
        try:
            self.log.insert("end", f"{line}\n")
            self.log.see("end")
        except tk.TclError:
            pass
        print(line)

    def start(self):
        if self.use_mic.get() and self.mic.available:
            ok = self.mic.start()
            self._log(f"mic start: {ok} ({self.mic.backend})")
            if self.mic.noise_floor_db is not None:
                self.vad.set_noise_floor(self.mic.noise_floor_db)
        self._log(
            f"webrtcvad: {'on' if self.vad.webrtc_available else 'off (energy only)'}"
        )
        self.engine.reset()
        self.vad.reset()  # حالة الكشف تتراكم أثناء التوقف بدون هذا
        self.running = True
        self.status_text.set("يعمل - صوت حقيقي عبر السماعات")
        self._log(f"engine started (player={self.player.backend})")

    def stop(self):
        self.running = False
        self.mic.stop()
        self.player.stop()
        self.status_text.set("متوقف")

    def _load_prayer_async(self):
        def work():
            try:
                result = load_today_times(self.config)
            except Exception as e:
                self._prayer_queue.put(("error", str(e)))
                return
            self._prayer_queue.put(("ok", result))

        threading.Thread(target=work, daemon=True).start()

    def _drain_prayer_queue(self):
        """
        يستهلك نتائج الخيوط العاملة - كل التحديثات تتم هنا في خيط الواجهة.

        كل عنصر يُعالج داخل try مستقل: فشل عنصر واحد يجب ألّا يبتلع البقية.
        """
        while True:
            try:
                kind, payload = self._prayer_queue.get_nowait()
            except queue.Empty:
                return
            try:
                if kind == "error":
                    self.prayer_text.set(f"تعذر التحميل: {payload}")
                elif kind == "cal":
                    self.vad.set_noise_floor(payload)
                    self.cal_label.config(text=f"أرضية الضجيج: {payload:.0f}dB")
                    self._log(f"معايرة ضجيج الغرفة: {payload:.1f}dB")
                else:
                    times, source, cached_date = payload
                    self.engine.prayer.set_times(times)
                    self.prayer_source.set(describe_source(source, cached_date))
                    self.prayer_text.set(
                        f"{describe_times(times)}  [{self.prayer_source.get()}]"
                    )
                    self._prayer_date = datetime.now().date()
                    self._log(f"مواقيت الصلاة: {self.prayer_source.get()}")
            except (tk.TclError, KeyError) as e:
                self._log(f"queue item {kind} failed: {e}")

    def _calibrate(self):
        if not self.mic.available:
            self._log("المعايرة: الميكروفون غير متاح")
            return
        if not self.mic.is_running:
            self.mic.start()
        self.cal_label.config(text="... جاري المعايرة: اصمت 2ث ...")

        def work():
            floor = self.mic.calibrate(seconds=CALIBRATE_SEC)
            if floor is not None:
                self._prayer_queue.put(("cal", floor))
            else:
                self._prayer_queue.put(("error", "فشلت المعايرة"))

        threading.Thread(target=work, daemon=True).start()

    def _scenario(self, kind: str):
        """
        يزرع سيناريو لثوانٍ قليلة.

        بدون التثبيت كانت النبضة التالية (200ms) تستبدله بإطار حقيقي من
        الميكروفون فيختفي الأثر فوراً. نحتفظ بالإطار المزروع حتى ينتهي.

        تصفير الحالة هنا مقصود: زر «نقاش حامي» يثبّت كتم 60 ثانية، فلئن
        تختار «كلام عادي» تظل مكتوماً ولا يعرف المستخدم أن الزر الأول
        هو ما علقه. الزر يصفّر ثم يزرع، فيعكس الاختيار مباشرة.
        """
        if not self.running:
            self.start()
        self.engine.reset()
        self.vad.reset()
        self._scenario_until = 0.0
        self._scenario_db_value = 0.0
        now = self.clock.now()
        # الطابع زمن حقيقي: لو أخذ الطابع من الساعة المحاكاة لأصبح
        # الإطار المزروع في المستقبل بـ 12 ساعة عند الضبط على 02:00،
        # فينتهي تبريد النقاش ومؤقت النشاط فوراً.
        ts = self.clock.real_now().timestamp()
        if kind == "debate":
            fr = AudioFrame(
                timestamp=ts, db_level=70, is_speech=True, is_overlapping=True
            )
            label, hold = "نقاش حامي 70dB", 3.0
        elif kind == "welcome":
            fr = AudioFrame(
                timestamp=ts, db_level=62, is_speech=True, is_greeting_tone=True
            )
            label, hold = "ترحيب ضيوف", 9.0
        elif kind == "silence":
            base = ts - 11
            self.engine.process_frame(
                AudioFrame(timestamp=base, db_level=45, is_speech=True), now
            )
            fr = AudioFrame(timestamp=ts, db_level=20, is_speech=False)
            label, hold = "صمت 11ث -> Fade-In", 3.0
        else:
            fr = AudioFrame(timestamp=ts, db_level=50, is_speech=True)
            label, hold = "كلام عادي", 3.0
        self._scenario_until = time.time() + hold
        self._scenario_db_value = fr.db_level
        self._log(f"سيناريو: {label} (مثبَّت {hold:.0f}ث)")
        cmd = self.engine.process_frame(fr, now)
        self._log(self.player.apply(cmd))
        self._log(str(cmd))

    def _scenario_db(self) -> float | None:
        """مستوى dB المزروع ما زال سارياً، وإلا None.

        يُعيد قيمة السيناريو نفسه لا قيمة ثابتة، وإلا خالف العدّاد
        والتسمية على الشاشة ما يقوله السجل.
        """
        if self._scenario_until and time.time() < self._scenario_until:
            return self._scenario_db_value
        self._scenario_until = 0.0
        return None

    def _current_db(self) -> float:
        pinned = self._scenario_db()
        if pinned is not None:
            return pinned
        if self.use_mic.get() and self.mic.available:
            db = self.mic.read_db()
            if db is not None:
                return db
        return float(self.manual_db.get())

    def _tick(self):
        """
        نبضة واحدة كل 200ms: تحدّث الواجهة وتغذّي المحرك بإطار صوتي.

        الفصل هنا هو جوهر محاكاة الوقت: `now` قد يكون مزيفاً ليراه
        المحرك فيقرر (الفترة، نافذة الصلاة)، بينما `real_now` حقيقي
        لطوابع الإطارات. لو استُعملت الساعة المحاكاة للطوابع لأمكن
        فكّ مؤقتات المحرك كلها بقفزة واحدة.
        """
        try:
            self._drain_prayer_queue()
            now = self.clock.now()
            real_now = self.clock.real_now()
            # تغيّر التاريخ يُقاس بالزمن الحقيقي: قفزة محاكاة عبر منتصف
            # الليل لا تستدعي إعادة تحميل المواقيت بلا فائدة.
            if self._prayer_date is not None and real_now.date() != self._prayer_date:
                self._prayer_date = None
                self._athan_played.clear()
                self._load_prayer_async()
            if self.running and self.athan_enabled.get():
                self._maybe_play_athan(now)
            db = self._current_db()
            self._apply_output_settings(now, db)
            # الطابع زمن حقيقي دائماً - هذا هو السطر المحوري.
            auto = self.vad.analyze_frame(db, timestamp=real_now.timestamp())
            if self.greeting.get():
                auto.is_greeting_tone = True
                auto.is_speech = True
            if self.running:
                cmd = self.engine.process_frame(auto, now)
                # عطل في التشغيل لا يجب أن يوقف قراءة الواجهة: نعزله هنا
                # ليبقى العدّاد والساعة يعملان. ونُظهره في القراءة أيضاً،
                # فسطر واحد في السجل لا يكفي: الواجهة كانت تعلن حالة
                # عادية بينما لا صوت في الواقع.
                try:
                    self.player.apply(cmd)
                except Exception as e:
                    self._player_error = str(e)
                    if not self._player_error_logged:
                        self._log(f"عطل في التشغيل: {e}")
                        self._player_error_logged = True
                else:
                    self._player_error_logged = False
                    self._player_error = ""
                self._update_readouts(now, cmd, db, auto.is_speech, auto.is_speech_raw)
            else:
                self.db_label.config(text=f"dB: {db:.0f} (المحرك متوقف)")
            self._update_sim_readout(now, real_now)
            self._maybe_save_settings()
        except Exception as e:
            self._log(f"خطأ في النبضة: {e}")
        self.root.after(TICK_MS, self._tick)

    # ---------- محاكاة الوقت ----------
    def _on_sim_toggle(self):
        """
        تفعيل/تعطيل محاكاة الساعة.

        لا نلمس الإزاحة هنا: هي تُستعاد عند الإنشاء من sim_offset_sec
        المحفوظ، فلا يحتاج التفعيل إلى استرجاع شيء. الشرط الوحيد هو
        مزامنة حقول الإدخال عند أول تفعيل بلا إزاحة، وإلا عرضت الحقول
        ساعة بناء من جلسة سابقة.

        لا نمسّ حالة المحرك: مؤقّتاتها على الزمن الحقيقي، وتصفيرها عند
        القفزة كان سيهدم عدّاد الخمول بلا داع.
        """
        self.clock.set_enabled(bool(self.sim_on.get()))
        if self.clock.enabled and not self.clock.offset_sec:
            self._sync_sim_fields()
        self._log(
            f"محاكاة الوقت: {'مفعّلة' if self.clock.enabled else 'معطّلة'} "
            f"(إزاحة {self.clock.offset_sec:+.0f}ث)"
        )

    def _on_sim_time(self, *_args):
        """
        تغيير حقل الساعة أو الدقيقة - يُستدعى من trace لا من command.

        تعديل الحقل طلبٌ لتحريك الساعة، فالمحاكاة تُفعَّل عنده. تركها
        معطّلة كان يعني تخزين إزاحة صامتة تُطبَّق عند أول ضغطة على المربع
        لاحقاً، وهي مفاجأة لا تخدم شيئاً.
        """
        if self._updating:
            return
        try:
            hour = int(self.sim_hour.get())
            minute = int(self.sim_minute.get())
        except (TypeError, ValueError, tk.TclError):
            # إدخال غير صالح: نعيد الحقل إلى ما تعكسه الساعة فعلاً، وإلا
            # بقي الحقل يعرض قيمة لم تصل الساعة أصلاً.
            self._sync_sim_fields()
            return
        if not self.clock.enabled:
            self.sim_on.set(True)
            self._on_sim_toggle()
            self._log(f"فُعّلت المحاكاة بتعديل الحقل إلى {hour:02d}:{minute:02d}")
        self._updating = True
        try:
            self.clock.set_hhmmss(hour, minute)
        finally:
            self._updating = False

    def _on_sim_nudge(self, hours: float):
        self.clock.nudge(hours)
        self._sync_sim_fields()
        self._log(f"إزاحة الساعة {hours:+.0f}س -> {self.clock.offset_sec:+.0f}ث")

    def _on_sim_reset(self):
        """عودة كاملة: تعطيل المحاكاة وتصفير الإزاحة."""
        self.clock.reset()
        self.sim_on.set(False)
        self._sync_sim_fields()
        self._log("عودة إلى الوقت الحقيقي")

    def _sync_sim_fields(self):
        """يكتب الساعة المحاكاة في حقول الإدخال مع حارس ضد إعادة الدخول."""
        self._updating = True
        try:
            now = self.clock.now()
            self.sim_hour.set(now.hour)
            self.sim_minute.set(now.minute)
        finally:
            self._updating = False

    def _update_sim_readout(self, now, real_now):
        """يعرض الساعتين. القراءة هنا هي المرجع، لا قيم الحقول."""
        if not self.clock.enabled:
            self.sim_readout.set(f"الحقيقي: {real_now:%Y-%m-%d %H:%M:%S}")
            return
        self.sim_readout.set(
            f"المحاكى: {now:%Y-%m-%d %H:%M:%S}   |   الحقيقي: {real_now:%H:%M:%S}"
        )

    def _maybe_play_athan(self, now: datetime):
        """يشغّل نغمة الأذان مرة واحدة عند لحظة الأذان."""
        hit = self.engine.prayer.athan_moment(now)
        if hit is None:
            return
        key = f"{now.date().isoformat()}:{hit}"
        if key in self._athan_played:
            return
        self._athan_played.add(key)
        self.player.play_once("athan_chime.wav", volume=ATHAN_VOLUME)
        self._log(f"نغمة الأذان: {hit}")

    def _apply_output_settings(self, now: datetime, db: float):
        """
        يرفع الصوت الرئيسي والكتم ومعادل الفترة إلى المشغّل.

        لا نعيد دفع القيم لم يتغيّر: كل دفعة تستهلك قفلاً في المشغّل،
        وتحريك المعامل المتكرر يُولّد خيوط تدرّج بلا فائدة.

        إن أُعيد إنشاء المشغّل لازم تُصفَّر الحواجز، وإلا لن يصله مستوى
        الصوت الحالي أبداً لأن القيم ستُعتبر "لم تتغيّر".
        """
        if self.player is not self._last_player:
            self._last_player = self.player
            self._last_vol = -1.0
            self._last_eq = -1.0
        vol = float(self.master_vol.get()) / 100.0
        if abs(vol - self._last_vol) >= 0.005:
            self.player.set_master_volume(vol)
            self._last_vol = vol
        mute = bool(self.master_mute.get())
        if mute != self._last_mute:
            self.player.set_master_muted(mute)
            self._last_mute = mute
        period = self.engine.calendar.period_for_datetime(now)
        eq = self.period_eq.get(period.value, 1.0)
        if eq != self._last_eq:
            self.player.set_eq(eq)
            self._last_eq = eq

    def _update_readouts(
        self, now, cmd, db: float, is_speech: bool, is_speech_raw: bool = False
    ):
        """يحدّث لوحات القراءة: الفترة، الحالة، العدّاد، الصلاة التالية، الشريط."""
        period = self.engine.calendar.period_for_datetime(now)
        label = PERIOD_LABELS_AR.get(period, period.value)
        self.period_text.set(f"الفترة: {label} ({period.value})")
        self.state_text.set(f"الحالة: {cmd.state.value} - {cmd.reason}")
        if self._player_error:
            # الحالة أعلاه من قرار المحرك، فالصوت لا يتبعها.
            # نلفت النظر بدل ترك المستخدم يقرأ "يومية" ويظن أن الصوت يعمل.
            self.state_text.set(
                f"⚠ بلا صوت: {self._player_error[:40]} | {self.state_text.get()}"
            )
        # عند اختلاف المؤشرين نعرض الخام أيضاً: هو ما يفسّر تأخّر 200ms
        # الذي يفرضه فلتر الثبات، وإلا بدا الكتم غير مبرَّر.
        speech_txt = f"كلام={int(is_speech) * 1}"
        if bool(is_speech_raw) != bool(is_speech):
            speech_txt += f" خام={int(is_speech_raw) * 1}"
        self.db_label.config(text=f"dB: {db:.0f} {speech_txt} ({self.player.backend})")
        nxt = self.engine.prayer.next_prayer(now)
        if nxt is not None:
            mins = max(0, int((nxt.adhan - now).total_seconds() // 60))
            name = (
                "الجمعة (بدل الظهر)"
                if nxt.name == "dhuhr" and now.weekday() == 4
                else nxt.name
            )
            self.next_text.set(
                f"الصلاة التالية: {name} {nxt.adhan.strftime('%H:%M')} (بعد {mins} د)"
            )
        width = self.meter.winfo_width() or 580
        self.meter.delete("all")
        self.meter.create_rectangle(
            0, 0, width * min(db, 100) / 100, METER_H, fill="#4caf50"
        )

    def _maybe_save_settings(self):
        """يحفظ الإعدادات دورياً (كل SAVE_EVERY_TICKS نبضة) لا في كل نبضة."""
        self._save_counter += 1
        if self._save_counter < SAVE_EVERY_TICKS:
            return
        self._save_counter = 0
        # عبر _save_settings لا save_settings مباشرة: هناك طريقتان
        # للمفاتيح نفسها، وتحديث self._settings يحدث في واحدة فقط. يوم
        # تضيف مفتاحاً وتسقطه من الأخرى يظهر الفرق عند إعادة القراءة.
        self._save_settings()


def main(log_path: Optional[Path] = None) -> None:
    """
    نقطة تشغيل الواجهة - لكل مسارات الدخول.

    يثبّت التسجيل هنا لا في نقطة دخول الـ exe وحدها، حتى يحصل
    `python -m src.context_aware_audio.app` (أمر التطوير الموثّق) سجلاً
    أيضاً. تمرير log_path مسبقاً (من desktop_app.py) يتجاوز التثبيت.
    """
    if tk is None:
        print("tkinter is not available")
        sys.exit(2)
    if log_path is None:
        from .log_setup import install as install_logging

        log_path = install_logging()
    root = tk.Tk()
    DesktopApp(root, log_path=log_path)
    try:
        root.mainloop()
    finally:
        from .log_setup import close as close_logging

        close_logging()  # يُغلق مقبض الملف حتى يمكن تدويره أو حذفه


if __name__ == "__main__":
    main()
