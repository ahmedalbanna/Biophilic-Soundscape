"""
config.py - إعدادات المحرك القابلة للضبط
جميع العتبات والأزمنة والمستويات حسب وثيقة thinking.md
"""

from dataclasses import dataclass, field
from typing import Dict

from .audio_types import DayPeriod


@dataclass
class EngineConfig:
    """إعدادات المحرك - كل القيم حسب الوثيقة الأصلية"""

    # ---- عتبات الشدة (dB) ----
    loud_debate_threshold_db: float = 65.0  # نقاش حامي
    speech_threshold_db: float = 40.0  # حد اعتبار الصوت كلاماً
    activity_threshold_db: float = 5.0  # أي صوت فوقه يُحسب نشاطاً (يمنع وضع النوم)
    noise_floor_track_sec: float = 30.0  # نافذة تتبّع أرضية الضجيج
    noise_floor_margin_db: float = 12.0  # هامش عتبة الكلام فوق ضجيج الغرفة

    # ---- ثبات الكلام ----
    # استمرارية: الواجهة تقرأ كل 200ms، فالنوافذ تُقاس بعدد إطارات.
    # 0.2ث = إطاران، 0.4ث = إطاران. الثبات أطول من البدء حتى لا يطنّش.
    speech_onset_sec: float = 0.2  # تأكيد الكلام بعد هذا الزمن
    speech_release_sec: float = 0.4  # إنهاءه بعد الهبوط هذا الزمن
    speech_retrigger_cooldown_sec: float = 3.0  # تهدئة قبل تأكيد جديد
    speech_hysteresis_db: float = 4.0  # فرق الخروج عن الدخول

    # ---- نسب Ducking ----
    # النسب مشتقّة من duck_depth عبر engine.duck_max_ratio() و
    # duck_min_ratio()، لا حقول مستقلة هنا. كانا حقلين (``ducking_*_ratio``)
    # يبقيان على 0.30 و0.10 مهما غُيّر duck_depth، فكان يتطابقان
    # بالصدفة عند الافتراضي 70% فقط، ويسمح لمن يعدّل العمق بأن يرى
    # لا أثراً - وهو أسوأ من الغياب. خُذ النسبة من المحرّك لا من هنا.
    duck_depth: float = 70.0  # أقصى خفض مئوية
    welcome_duck_ratio: float = 0.15  # ترحيب ضيوف -> 15%

    # ---- كشف نبرة الترحيب ----
    greeting_min_db: float = 55.0  # أدنى شدة تُعدّ نبرة ترحيب
    greeting_min_jump_db: float = 12.0  # قفزة البداية المطلوبة (dB)
    greeting_onset_window_sec: float = 1.5  # أقصى فارق زمني يُحسب بداية
    greeting_sustain_sec: float = 0.3  # مدة استمرار النبرة فوق العتبة
    greeting_cooldown_sec: float = 3.0  # تهدئة قبل إعادة التفعيل

    # ---- الأزمنة (بالثواني) ----
    tick_interval_sec: float = 0.2  # نبضة قراءة الميكروفون (تحكم حجم نوافذ التتبّع)
    duck_hold_sec: float = 3.0  # استمرار الخفض بعد الكلام قبل الصعود
    silence_for_fade_in_sec: float = 10.0  # هدوء مفاجئ -> Fade-In
    debate_cooldown_sec: float = 60.0  # عودة الصوت بعد النقاش الحامي
    sleep_no_activity_sec: float = 300.0  # 5 دقائق -> وضع النوم
    fade_in_duration_sec: float = 3.0  # Fade-In ناعم
    fade_out_duration_sec: float = 1.0  # اختفاء عند بدء الكلام (ثانية واحدة)
    welcome_hold_sec: float = 8.0  # مدة نغمة الترحيب

    # ---- أوقات الصلاة ----
    pre_adhan_mute_min: int = 3  # كتم قبل الأذان بـ 3 دقائق
    prayer_duration_min: int = 20  # مدة الصلاة التقديرية
    post_prayer_lock_min: int = 15  # حظر 15 دقيقة بعد الصلاة

    # ---- الموقع الجغرافي (افتراضي: صنعاء) ----
    latitude: float = 15.3694
    longitude: float = 44.1910
    city: str = "Sanaa"
    country: str = "Yemen"
    timezone_offset: int = 3  # Asia/Aden UTC+3
    prayer_method: int = 3  # 3 = رابطة العالم الإسلامي (MWL)

    # ---- الجدول اليومي -> (ملف صوتي، مستوى dB) ----
    period_sounds: Dict[str, Dict] = field(
        default_factory=lambda: {
            "fajr_sabah": {
                "file": "mountain_breeze_birds.wav",
                "db": 35,
                "label": "نسيم الجبال + عصافير",
            },
            "duha_work": {"file": "water_stream.wav", "db": 38, "label": "خرير الماء"},
            "lunch": {"file": "light_rain_leaves.wav", "db": 30, "label": "قطرات مطر"},
            "maqil": {
                "file": "warm_breeze.wav",
                "db": 32,
                "label": "نسيم دافئ (تفاعلي)",
            },
            "maghrib_isha": {"file": None, "db": 0, "label": "كتم تام - وقت عبادة"},
            "samra": {
                "file": "sea_waves_fireplace.wav",
                "db": 33,
                "label": "أمواج / حطب",
            },
            "night_sleep": {
                "file": "crickets_light.wav",
                "db": 20,
                "label": "صرار الليل",
            },
        }
    )

    # ---- أصوات الحالات الخاصة ----
    # ملاحظة: لا يوجد إدخال لـ debate_mute لأن النقاش الحامي = كتم تام وليس ملفاً صوتياً
    special_sounds: Dict[str, Dict] = field(
        default_factory=lambda: {
            "welcome": {
                "file": "distant_waterfall.wav",
                "db": 30,
                "label": "شلال بعيد ترحيبي",
            },
            "contemplation": {
                "file": "wind_coffee_trees.wav",
                "db": 32,
                "label": "ريح بين شجر البن",
            },
        }
    )

    # ===================================================================
    # مسار المحتوى التعليمي: أحاديث وقصص الأنبياء
    # ===================================================================
    # المحتوى فوق الخلفية لا بدلاً منها. يقرر content_engine، وينفّذ
    # real_content. المفتاح هو: أي فترة من اليوم يسمح بسرد أي نص.

    content_enabled: bool = True

    # --- بوابة الانتباه ---
    # فوق هذا المستوى نؤجّل البثّ: المجلس الصاخب أو فيه ضجيج
    # يقطع السرد ولا يقطع سماعه.
    content_gate_max_db: float = 50.0
    # كان هنا `content_gate_delay_min: int = 10` — مدة تأجيل بالدقائق
    # لا مرجع له في الشيفرة إطلاقاً، فالمواصفة كتبته ولم ينفّذه أحد.
    # عدّاد التكرار أقوى من مؤقّت: ثلاث جلسات صاخبة متتالية تعني
    # مجلساً مشغولاً، ومدة زمنية تعني جلسةً واحدة طويلة.
    content_gate_max_postpones: int = 3  # بعدها تُهجر النافذة لليوم

    # --- المقاطعة والاستئناف ---
    content_rewind_sec: float = 3.0  # نتراجع هذه الثواني عند كلام
    content_resume_quiet_sec: float = 5.0  # هدوء متصل قبل الاستئناف

    # --- المستويات ---
    # الاستبدال لا الضرب: تحت المحتوى نسبة 20% ثم هي المبلغ. لو ضُربت
    # في منحنى الخفض لصارت الخلفية صامتة تماماً (0.20 × 0.10).
    content_ambient_ratio: float = 0.20
    content_volume: float = 0.85
    content_min_gap_min: int = 30  # لا مقطعين متتاليين في نافذة واحدة

    # --- نوافذ المحتوى ---
    # مربوطة بفترات DayPeriod القائمة، لا بنظام موازٍ. «samra» لا
    # «maghrib_isha» لأن الأخيرة فترة عبادة مكتومة.
    content_windows: Dict[str, Dict] = field(
        default_factory=lambda: {
            "fajr_dhikr": {
                "periods": [DayPeriod.FAJR_SABAH],
                "label": "أذكار الفجر",
                "kind": "dhikr",
                "min_room_silence_sec": 5.0,
                "once_per_day": True,
            },
            "duha_wisdom": {
                "periods": [DayPeriod.DUHA_WORK],
                "label": "حكمة وذكرى",
                "kind": "wisdom",
                "min_room_silence_sec": 8.0,
                "once_per_day": True,
            },
            "maqil_story": {
                "periods": [DayPeriod.MAQIL],
                "label": "قصة من السيرة",
                "kind": "story",
                "min_room_silence_sec": 10.0,
                "once_per_day": True,
            },
            "evening_ethic": {
                "periods": [DayPeriod.SAMRA],
                "label": "أخلاق وآداب",
                "kind": "hadith",
                "min_room_silence_sec": 8.0,
                "once_per_day": True,
            },
            "night_calm": {
                "periods": [DayPeriod.NIGHT_SLEEP],
                "label": "ذكر هادئ",
                "kind": "dhikr",
                "min_room_silence_sec": 15.0,
                "once_per_day": True,
            },
        }
    )
