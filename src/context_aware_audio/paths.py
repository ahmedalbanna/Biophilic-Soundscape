"""
paths.py - مسارات التطبيق الموحّدة
مصدر واحد لكل مسارات القراءة والكتابة حتى لا تتفرق النسخ بين الوحدات.
"""

import os
import sys
from pathlib import Path

APP_DIR_NAME = "ContextAudio"


def app_root() -> Path:
    """جذر التطبيق: مجلد المشروع، أو sys._MEIPASS عند التجميد مع PyInstaller."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parents[2]


def assets_dir() -> Path:
    """مجلد الأصول (قراءة فقط) الذي يحوي ملفات WAV المولّدة."""
    return app_root() / "assets"


def user_data_dir() -> Path:
    """
    مجلد قابل للكتابة: بجانب الـ exe، أو `%LOCALAPPDATA%` في وضع التجميد.

    لا يُنشأ المجلد هنا - الإنشاء يحدث عند أول كتابة فعلية حتى لا
    يُستدعى استيراد الوحدة بلا داع.
    """
    if getattr(sys, "frozen", False):
        base = Path(
            os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))
        )
        return base / APP_DIR_NAME
    return app_root() / "assets"


def writable_path(name: str) -> Path:
    """مسار ملف قابل للكتابة داخل مجلد بيانات المستخدم (ينشئ المجلد عند الحاجة)."""
    d = user_data_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d / name
