"""
Context-Aware Audio Engine - Yemeni Cultural Edition
محرك الصوت التكييف للسياق - النسخة اليمنية

الاستيراد من هذه الحزمة لا يُحمّل أي مكتبات صوتية: المشغّل والميكروفون
متاحان بتحميل كسول (lazy import) عبر __getattr__ بالأسفل.
"""

import importlib
from typing import TYPE_CHECKING

from .audio_types import AudioFrame, DayPeriod, EngineState, PlaybackCommand
from .config import EngineConfig
from .cultural_calendar import CulturalCalendar
from .engine import ContextAwareAudioEngine
from .prayer_engine import PrayerEngine
from .vad import VadProcessor

if TYPE_CHECKING:  # لأجل أدوات التحليل فقط - لا يُنفَّذ وقت التشغيل
    from .mic_input import MicInput
    from .real_player import RealPlayer

__all__ = [
    "ContextAwareAudioEngine",
    "EngineConfig",
    "CulturalCalendar",
    "DayPeriod",
    "EngineState",
    "AudioFrame",
    "PlaybackCommand",
    "PrayerEngine",
    "VadProcessor",
    "RealPlayer",
    "MicInput",
]

_LAZY = {
    "RealPlayer": "real_player",
    "MicInput": "mic_input",
}

__version__ = "1.2.0"


def __getattr__(name: str):
    """تحميل كسول للمشغّل والميكروفون حتى لا تُحمَّل مكتبات الصوت بلا داع."""
    module_name = _LAZY.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(importlib.import_module(f".{module_name}", __name__), name)
    globals()[name] = value
    return value


def __dir__():
    return sorted(__all__)
