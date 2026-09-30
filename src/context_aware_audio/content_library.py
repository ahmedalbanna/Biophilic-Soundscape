"""
content_library.py - فحص مجلد المحتوى وقياس مدد الملفات

وحدة مستقلة: لا تعرف المحرك ولا الصوت. تعطي صفوفاً جاهزة للمخزن.

اصطلاح التسمية
---------------
اسم الملف يحمل نافذته وتسلسله وعنوانه:

    <window_key>__<sequence_index>__<title>.<ext>
    maqil_story__001__قصة_آدم.mp3
    duha_wisdom__حكمة_الرفق.mp3      (بلا تسلسل)

النافذة يجب أن تطابق مفتاحاً في EngineConfig.content_windows، وإلا
سقط الملف إلى النافذة الافتراضية بدل رفضه. التسلسل اختياري: غيابه
يعني نصاً يُختار بالتناوب لا تقدّماً تصاعدياً.

المعرّف مشتق من المسار النسبي بـ sha1: إعادة الفحص لا تُنتج تكراراً،
وتغيير المجلد لا يفسد سجل التشغيل.
"""

import hashlib
import re
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

WAV_SUFFIXES = (".wav",)
DECODED_SUFFIXES = (".mp3", ".ogg", ".flac", ".m4a", ".opus")
KNOWN_SUFFIXES = WAV_SUFFIXES + DECODED_SUFFIXES

# فصل حقول الاسم على "__" لا على "_": أسماء النوافذ نفسها فيها
# شرطات سفلية (maqil_story)، فالفصل على "_" مفرد يقطعها في منتصفها.
_NAME_RE = re.compile(r"^(?P<window>.+?)__(?P<rest>.*)$")

DEFAULT_WINDOW = "duha_wisdom"


@dataclass
class ScanReport:
    """نتيجة فحص واحد: ماذا تغيّر، وماذا رُفض ولماذا."""

    root: Path
    added: int = 0
    updated: int = 0
    skipped: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.added + self.updated

    @property
    def is_empty(self) -> bool:
        """مكتبة فارغة حالة صريحة لا صمت: الواجهة تعرضها كما هي."""
        return self.total == 0 and not self.errors


def content_id_for(relative_path: str) -> str:
    """معرّف ثابت اشتقاقاً من المسار النسبي."""
    norm = relative_path.replace("\\", "/").lower()
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()[:16]


def parse_name(filename: str, window_keys: List[str]) -> Tuple[str, Optional[int], str]:
    """
     يفكّ اسم الملف إلى (نافذة، تسلسل، عنوان).

    _unknown prefix or missing window falls back to the default window:
     a file the user dropped in without a prefix should still be playable,
     just not in a window they chose.
    """
    stem = Path(filename).stem
    match = _NAME_RE.match(stem)
    if match:
        window = match.group("window")
        rest = match.group("rest")
        if window in window_keys:
            seq_text, _, title = rest.partition("__")
            if seq_text.isdigit():
                return window, int(seq_text), title or stem
            return window, None, title or rest or stem
    return DEFAULT_WINDOW, None, stem


def measure_wav(path: Path) -> Optional[float]:
    """مدّة ملف wav من ترويسته فقط - بلا فك ترميز وبلا اعتماديات."""
    try:
        with wave.open(str(path), "rb") as handle:
            frames = handle.getnframes()
            rate = handle.getframerate()
            if rate <= 0:
                return None
            return frames / float(rate)
    except (OSError, EOFError, wave.Error):
        return None


def measure_decoded(path: Path) -> Optional[float]:
    """
    مدّة mp3/ogg/flac عبر pygame.

    يفكّ الملف كله في ذاكرة المشغّل: مقطع عشرين دقيقة يستهلك نحو
    211 ميغابايت لحظياً. لذلك يُقاس مرة واحدة عند الإضافة ويُخزَّن،
    ولا يُعاد إلا إذا تغيّر زمن التعديل أو الحجم.
    """
    try:
        import pygame

        if not pygame.mixer.get_init():
            pygame.mixer.init(frequency=22050, size=-16, channels=2, buffer=512)
        sound = pygame.mixer.Sound(str(path))
        seconds = sound.get_length()
        # المرجع يُحرَّر فوراً: إبقاؤه يعني بقاء 211 ميغابايت.
        del sound
        return float(seconds) if seconds and seconds > 0 else None
    except Exception:
        return None


def measure_duration(path: Path) -> Optional[float]:
    """مدّة أي ملف مدعوم، أو None إن تعذّر القياس."""
    suffix = path.suffix.lower()
    if suffix in WAV_SUFFIXES:
        return measure_wav(path)
    if suffix in DECODED_SUFFIXES:
        return measure_decoded(path)
    return None


class ContentLibrary:
    """يفحص مجلد المحتوى ويغذّي المخزن. قياس واحد، ثم تخزين."""

    def __init__(self, root: Path, store, config):
        self.root = Path(root)
        self.store = store
        self.config = config

    # ---------- المجلد ----------
    def ensure_root(self) -> Path:
        """ينشئ المجلد إن لم يكن. وجوده شرط ليعمل «افتح المجلد»."""
        self.root.mkdir(parents=True, exist_ok=True)
        return self.root

    def _iter_files(self):
        if not self.root.is_dir():
            return
        for path in sorted(self.root.rglob("*")):
            if path.is_file():
                yield path

    def _window_keys(self) -> List[str]:
        return list(self.config.content_windows.keys())

    # ---------- الفحص ----------
    def scan(self) -> ScanReport:
        """
        يمسح المجلد ويدخل الصفوف الجديدة ويحدّث المتغيّرة.

        لا يحذف: ملف حذفه المستخدم يبقى صفّه مع enabled=1، والشاشة
        تُظهر المجلد لا القاعدة. الحذف قرار للمستخدم لا أثر جانبي.
        """
        report = ScanReport(root=self.root)
        keys = self._window_keys()
        seen = 0
        for path in self._iter_files():
            suffix = path.suffix.lower()
            if suffix not in KNOWN_SUFFIXES:
                if suffix:
                    report.skipped.append(f"{path.name} (صيغة غير مدعومة)")
                continue
            seen += 1
            try:
                rel = path.relative_to(self.root)
            except ValueError:
                report.skipped.append(f"{path.name} (خارج المجلد)")
                continue
            cid = content_id_for(str(rel))
            existing = self.store.get(cid)

            stamp = self._file_stamp(path)
            if existing is not None and existing["duration_sec"] > 0:
                # المدة مخزّنة: نعيد القياس فقط عند تغيّر الملف
                old = existing["added_at"] or ""
                if stamp and self._stamp_of(old) == stamp:
                    self._upsert(path, rel, cid, keys, existing["duration_sec"])
                    continue
                if stamp is None:
                    continue

            duration = measure_duration(path)
            if duration is None:
                report.errors.append(f"{path.name} (تعذّر قراءة المدة)")
                continue
            self._upsert(path, rel, cid, keys, duration)
            report.added += 1 if existing is None else 0
            report.updated += 1 if existing is not None else 0
        if seen == 0:
            report.skipped.append("(المجلد فارغ)")
        return report

    @staticmethod
    def _file_stamp(path: Path) -> Optional[str]:
        try:
            st = path.stat()
            return f"{st.st_mtime_ns}:{st.st_size}"
        except OSError:
            return None

    @staticmethod
    def _stamp_of(added_at: str) -> str:
        """added_at يحمل البصمة بعد الفاصل، فالفحص التالي يقارن بها."""
        _, _, tail = added_at.partition("|")
        return tail

    def _upsert(
        self,
        path: Path,
        rel: Path,
        cid: str,
        keys: List[str],
        duration: float,
    ) -> None:
        window, sequence, title = parse_name(path.name, keys)
        spec = self.config.content_windows.get(window, {})
        stamp = self._file_stamp(path) or ""
        self.store.upsert(
            content_id=cid,
            title=title,
            file_path=str(rel).replace("\\", "/"),
            kind=spec.get("kind", "hadith"),
            window_key=window,
            sequence_index=sequence,
            duration_sec=duration,
            min_room_silence_sec=float(spec.get("min_room_silence_sec", 5.0)),
            ducking_ratio=float(self.config.content_ambient_ratio),
            tags="",
            added_at=f"{self._now_stamp()}|{stamp}",
        )

    @staticmethod
    def _now_stamp() -> str:
        from datetime import datetime

        return datetime.now().isoformat(timespec="seconds")
