"""
paths.py - مسارات التطبيق الموحّدة
مصدر واحد لكل مسارات القراءة والكتابة حتى لا تتفرق النسخ بين الوحدات.
"""

import os
import sys
import tempfile
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


def atomic_write(path: Path, text: str) -> None:
    """
    يكتب عبر ملف مؤقت ثم `os.replace` - كتابة ذرّية.

    الكتابة المباشرة تُبقي الملف مقتطعاً إذا قُتلت العملية أثناءها، ثم
    يغذّي التنظيفَ التالي. الاستبدال ذرّي على NTFS.

    اسم الملف المؤقت فريد (NamedTemporaryFile) لا ثابت: نسختان من التطبيق
    تكتبان نفس الهدف، والنسخة الخاسرة كانت تحذف ملف الخاسرة.
    """
    tmp_name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=path.name + ".",
            suffix=".tmp",
            delete=False,
        ) as tmp:
            tmp.write(text)
            tmp_name = tmp.name
        os.replace(tmp_name, path)
    except OSError:
        if tmp_name is not None:
            try:
                Path(tmp_name).unlink(missing_ok=True)
            except OSError:
                pass
        raise
