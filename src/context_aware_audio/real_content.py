"""
real_content.py - مشغّل المحتوى: ينفّذ ولا يقرر

غلاف رقيق حول pygame.mixer.music. يقرأ الإجراء من ContentEngine
وينفّذه. لا يعرف شيئاً عن النوافذ ولا البوابة ولا الأولويات.

الموضع لا يُقرّر هنا. المحرك يملكه من طوابع الإطارات، وهذا المشغّل
يستقبل الموضع المطلوب عند START وRESUME ويضعه بـ set_pos. لا يقرأ
get_pos لاتخاذ قرار أبداً: قراءةُ موضع الجهاز нет Authority عليه، وكل
قراءة تجعل المسار الوهمي والحقيقي يتصرفان مختلفين.

music لا يملك get_length، فلا يُحمَّل المقطع في Sound إلا لقياس
مدته - وفكّ مقطع عشرين دقيقة يستهلك نحو 211 ميغابايت لحظياً.
"""

import wave
from pathlib import Path
from typing import Optional

from .audio_types import ContentAction
from .paths import assets_dir

# ما يرميه pygame فعلاً عند تحميل أصل: الملف الناقص والتالف معاً
# كلاهما pygame.error وهو فرع من RuntimeError.
CONTENT_ERRORS = (OSError, ValueError, RuntimeError, wave.Error)

# المحتوى لا يستعمل قنوات الخلفية: مسار مستقل بالكامل


class ContentBackendUnavailable:
    """تعذّر تشغيل المحتوى، والسبب معروف ومسجَّل."""

    def __init__(self, backend: str, reason: str):
        self.backend = backend
        self.reason = reason


class RealContentPlayer:
    """
    مشغّل المحتوى. `background` هو مشغّل الخلفية، يُقرأ منه فقط
    علم music_claimed: إن كان الخلفية استعملت mixer.music فلا مسار
    للمحتوى على هذا الجهاز، ويُعطَّل بسبب مسجّل لا بصمت.
    """

    def __init__(self, library_root: Path, background=None):
        self.root = Path(library_root)
        self.background = background
        self.history = []
        self.current_file: Optional[str] = None
        self.is_playing = False
        self.is_paused = False
        self.volume = 0.0
        self._pg = None
        self.unavailable: Optional[ContentBackendUnavailable] = None
        self._init_backend()

    # ---------- الإقلاع ----------
    def _init_backend(self) -> None:
        claimed = bool(getattr(self.background, "music_claimed", False))
        if claimed:
            self.unavailable = ContentBackendUnavailable(
                "claimed",
                "الخلفية استعملت mixer.music للعبور المتقاطع - "
                "الخلفية استعملت mixer.music للعبور المتقاطع - "
            )
            return
        try:
            import pygame

            if not pygame.mixer.get_init():
                pygame.mixer.init(frequency=22050, size=-16, channels=2, buffer=512)
            self._pg = pygame
        except Exception as exc:
            self.unavailable = ContentBackendUnavailable("unavailable", str(exc))

    @property
    def backend(self) -> str:
        if self.unavailable is not None:
            return self.unavailable.backend
        return "pygame-music" if self._pg is not None else "log"

    @property
    def available(self) -> bool:
        return self.unavailable is None and self._pg is not None

    def _log(self, line: str) -> str:
        self.history.append(line)
        if len(self.history) > 500:
            del self.history[: len(self.history) - 500]
        return line

    # ---------- المسار ----------
    def _resolve(self, name: str) -> Optional[Path]:
        """
        يحوّل المسار النسبي في المخزن إلى مسار على القرص.

        لا نبحث في assets: ملفات المحتوى في مجلد المستخدم. إن لقي
        الاسم داخل assets (خطأ في الإدخال) قُبل، فهو الوحيد المضمون.
        """
        candidate = self.root / name
        if candidate.is_file():
            return candidate
        fallback = assets_dir() / Path(name).name
        if fallback.is_file():
            return fallback
        return None

    # ---------- الأفعال ----------
    def apply(
        self,
        action: ContentAction,
        file: Optional[str] = None,
        volume: float = 0.0,
        position_sec: float = 0.0,
    ) -> str:
        """
        ينفّذ إجراءً واحداً ويعيد سطر السجل.

        أي شيء سوى START وRESUME يمرّ إلى الكتم: بلا pygame، أو مع
        تعطّل، أو مع مسار محجوز. نتكلّم في السجل ولا ندّعي ما لا
        نفعله.
        """
        if not self.available:
            return self._log(f"SKIP {action.value} {file or '-'} ({self._blocked()})")
        if action is ContentAction.START:
            return self._start(file, volume, position_sec)
        if action is ContentAction.RESUME:
            return self._resume(volume, position_sec)
        if action is ContentAction.PAUSE:
            return self._pause()
        if action in (ContentAction.STOP, ContentAction.FINISHED):
            return self._stop(action)
        return self._log(f"{action.value} {file or '-'} (بلا أثر)")

    def _blocked(self) -> str:
        if self.unavailable is None:
            return "لا pygame"
        return self.unavailable.reason

    def _start(self, file, volume, position_sec) -> str:
        path = self._resolve(file or "")
        if path is None:
            return self._log(f"PLAY-FAILED {file} (الملف غير موجود في المكتبة)")
        try:
            self._pg.mixer.music.load(str(path))
            self._pg.mixer.music.set_volume(max(0.0, min(1.0, volume)))
            # الترتيب مهم: set_pos قبل play يرمي "Music isn't playing".
            # فالإقلاع من موضع غير صفر كان يفشل ويُبلَّغ PLAY-FAILED كأنه عطب في الملف.
            self._pg.mixer.music.play(loops=0)
        except CONTENT_ERRORS as exc:
            self.current_file = None
            self.is_playing = False
            return self._log(f"PLAY-FAILED {file} ({exc})")
        note = self._seek(position_sec)
        self.current_file = file
        self.volume = volume
        self.is_playing = True
        self.is_paused = False
        return self._log(
            f"START {file} @{position_sec:.1f}s vol={volume:.0%}{note}"
        )

    def _seek(self, position_sec: float) -> str:
        """
        يحاول الإرجاع إلى موضع المحرك، ويصرّح إن لم ينجح.

        music.set_pos لا أثر له على بعض التيارات في pygame 2.6.1:
        الموضع يمضي من حيث كان. لا ندّعي أن الإرجاع تم - نكتب ذلك
        في السطر، والموضع الذي يقرر به المحرك هو المرجع على أي حال.
        """
        if position_sec <= 0:
            return ""
        try:
            self._pg.mixer.music.set_pos(position_sec)
        except Exception as exc:
            return f" (تعذّر الإرجاع: {exc})"
        return ""

    def _resume(self, volume, position_sec) -> str:
        if self.current_file is None:
            return self._log("RESUME بلا مقطع جارٍ")
        try:
            self._pg.mixer.music.unpause()
            self._pg.mixer.music.set_volume(max(0.0, min(1.0, volume)))
        except Exception as exc:
            return self._log(f"RESUME-FAILED {self.current_file} ({exc})")
        note = self._seek(position_sec)
        self.is_playing = True
        self.is_paused = False
        self.volume = volume
        return self._log(
            f"RESUME {self.current_file} @{position_sec:.1f}s{note}"
        )

    def _pause(self) -> str:
        if not self.is_playing or self.is_paused:
            return self._log("PAUSE (ليس مشغّلاً)")
        try:
            self._pg.mixer.music.pause()
        except Exception as exc:
            return self._log(f"PAUSE-FAILED {self.current_file} ({exc})")
        self.is_paused = True
        return self._log(f"PAUSE {self.current_file}")

    def _stop(self, action) -> str:
        if self.current_file is None and not self.is_playing:
            return self._log(f"{action.value} (ليس مشغّلاً)")
        name = self.current_file
        try:
            self._pg.mixer.music.stop()
        except Exception:
            pass
        self.current_file = None
        self.is_playing = False
        self.is_paused = False
        self.volume = 0.0
        return self._log(f"{action.value} {name or '-'}")

    # ---------- تشخيص ----------
    def device_position_sec(self) -> float:
        """
        موضع الجهاز بالثواني، أو -1 إن لم يكن مشغّلاً.

        لا يشارك في أي قرار: الموضع الذي يقرر به
        المحرك هو المرجع، وهذا رقم يقرأه الجهاز وقد يكون أخرف.
        """
        if not self.available or not self.is_playing:
            return -1.0
        try:
            ms = self._pg.mixer.music.get_pos()
        except Exception:
            return -1.0
        return ms / 1000.0 if ms and ms >= 0 else -1.0

    def close(self) -> None:
        try:
            if self._pg is not None and self.is_playing:
                self._pg.mixer.music.stop()
        except Exception:
            pass
        self.is_playing = False
        self.current_file = None
