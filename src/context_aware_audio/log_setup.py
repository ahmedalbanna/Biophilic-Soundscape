"""
log_setup.py - تسجيل الأخطاء في وضع الـ exe

`PyInstaller --windowed` لا يربط stdout/stderr، فيصبحان `None` لا مجريين
حقيقيين. أي انهيار عندها يختفي بلا أثر. هذه الوحدة تحلّ المشكلتين:
تربط المخرجات بملف، وتلتقط الاستثناءات غير المعالجة في الخيوط الرئيسية والعاملة.
تعمل بلا اعتماديات خارجية حتى لا تعيق الاستيراد الكسول للحزمة.
"""

import sys
import threading
import traceback
from pathlib import Path
from typing import Optional, TextIO

from .paths import writable_path

LOG_FILENAME = "context_audio.log"
MAX_LOG_BYTES = 1_000_000  # تدوير عند 1 ميغابايت


class _NullWriter:
    """بديل صامت لـ stdout: في وضع --windowed لا يوجد مجرى أصلاً."""

    def write(self, text: str) -> int:
        return len(text)

    def flush(self) -> None:
        pass

    def isatty(self) -> bool:
        return False

    def fileno(self):
        raise OSError("no fileno")

    @property
    def encoding(self) -> str:
        return "utf-8"


class _TeeWriter:
    """يكتب على مجرى المخرج وعلى ملف، ويلتقط ما يكتبه أي thread."""

    def __init__(self, primary: Optional[TextIO], log_file: TextIO):
        # وضع --windowed يجعل stdout/stderr يساوي None لا مجرى حقيقي
        self._primary = primary if primary is not None else _NullWriter()
        self._log = log_file
        self._lock = threading.Lock()

    def write(self, text: str) -> int:
        with self._lock:
            try:
                self._primary.write(text)
            except (OSError, ValueError):
                pass
            try:
                self._log.write(text)
            except (OSError, ValueError):
                pass
        return len(text)

    def flush(self) -> None:
        for stream in (self._primary, self._log):
            try:
                stream.flush()
            except (OSError, ValueError):
                pass

    def isatty(self) -> bool:
        return False

    def fileno(self):
        raise OSError("no fileno")

    @property
    def encoding(self) -> str:
        return getattr(self._primary, "encoding", "utf-8")


def _rotate_if_large(path: Path) -> None:
    """يحذف السجل إن تجاوز الحجم المسموح، بدل النمو بلا سقف."""
    try:
        if path.exists() and path.stat().st_size > MAX_LOG_BYTES:
            path.unlink()
    except OSError:
        pass


def install() -> Optional[Path]:
    """
    يربط stdout/stderr بملف سجل ويعيد مساره.

    آمن للاستدعاء أكثر من مرة، ويُعيد None إن تعذر فتح الملف.
    """
    path = writable_path(LOG_FILENAME)
    _rotate_if_large(path)
    try:
        log_file = path.open("a", encoding="utf-8", buffering=1)
    except OSError:
        return None

    sys.stdout = _TeeWriter(sys.stdout, log_file)
    sys.stderr = _TeeWriter(sys.stderr, log_file)

    def handle_exception(exc_type, exc, tb):
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        try:
            log_file.write(f"\n--- unhandled exception ---\n{text}")
            log_file.flush()
        except (OSError, ValueError):
            pass
        original = sys.__stderr__
        if original is not None:
            try:
                original.write(text)
            except (OSError, ValueError):
                pass

    sys.excepthook = handle_exception

    def handle_thread(args):
        handle_exception(args.exc_type, args.exc_value, args.exc_traceback)

    threading.excepthook = handle_thread
    return path


def close() -> None:
    """يعيد stdout/stderr الأصليين ويغلق ملف السجل (يحرّر مقبض الملف)."""
    for name in ("stdout", "stderr"):
        current = getattr(sys, name)
        if isinstance(current, _TeeWriter):
            setattr(sys, name, current._primary)
            try:
                current._log.flush()
                current._log.close()
            except (OSError, ValueError):
                pass
