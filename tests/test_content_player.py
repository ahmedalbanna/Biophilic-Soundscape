"""
real_content.py: التشغيل الفعلي عبر pygame.mixer.music.

يستخدم pygame حقيقةً. الاختبار لا يحاكي mixer: الحارس music_claimed
وإ مكان music.load لا يظهران إلا مع مكتبة حقيقية.
"""

import shutil
import sys
import tempfile
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.context_aware_audio.audio_types import ContentAction
from src.context_aware_audio.real_content import RealContentPlayer
from src.context_aware_audio.real_player import RealPlayer

PASSED = FAILED = 0


def check(name, cond, extra=""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"PASS {name}")
    else:
        FAILED += 1
        print(f"FAIL {name}  {extra}")


def wav(path, seconds=3.0, rate=22050):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(seconds * rate))
    return path


tmp = Path(tempfile.mkdtemp())
lib = tmp / "library"
wav(lib / "maqil_story__001__first.wav", 4.0)
wav(lib / "maqil_story__002__second.wav", 5.0)
(lib / "broken.wav").write_bytes(b"RIFFnope" * 30)

# ===== 1) المسار الطبيعي =====
p = RealContentPlayer(lib)
check("content: backend is pygame-music", p.backend == "pygame-music", p.backend)
check("content: available", p.available is True)
check("content: starts idle", p.is_playing is False)

line = p.apply(ContentAction.START, "maqil_story__001__first.wav", 0.85, 0.0)
check(
    "content: START reports success",
    "START" in line and "PLAY-FAILED" not in line,
    line,
)
check("content: is playing", p.is_playing is True)
check("content: file remembered", p.current_file == "maqil_story__001__first.wav")
time.sleep(0.4)
check(
    "content: the device really advanced",
    p.device_position_sec() > 0.0,
    p.device_position_sec(),
)

# ===== 2) الإيقاف المؤقت والاستئناف من موضع المحرك =====
p.apply(ContentAction.PAUSE)
check("content: paused", p.is_paused is True and p.is_playing is True)
check("content: pause logged", "PAUSE" in p.history[-1], p.history[-1])
line = p.apply(ContentAction.RESUME, volume=0.85, position_sec=2.0)
check("content: RESUME reported", "RESUME" in line and "FAILED" not in line, line)
check("content: no longer paused", p.is_paused is False)
# music.set_pos لا أثر له على بعض التيارات في pygame 2.6.1: الموضع
# يمضي من حيث كان. فالفحص لا يدّعي قفزة الجهاز، ويطالب فقط بأن
# الاستئناف نجح وأن السطر يحمل موضع المحرك لا موضع الجهاز.
check("content: resume succeeded", "RESUME" in line and "FAILED" not in line, line)
check("content: resume reports the engine's position", "@2.0s" in line, line)
check("content: the device position is reported separately",
      p.device_position_sec() >= 0.0, p.device_position_sec())

# ===== 3) الإنهاء =====
line = p.apply(ContentAction.FINISHED)
check("content: FINISHED stops", p.is_playing is False and p.current_file is None, line)
check("content: FINISHED reported", "finished" in line, line)

# ===== 4) START من موضع غير الصفر =====
# الإقلاع من موضع غير صفر كان يرمي "Music isn't playing" لأن set_pos
# يسبق play. الترتيب الصحيح play ثم set_pos، والفحص يطالب بأن البدء
# نجح وأن السطر يحمل الموضع المطلوب.
line = p.apply(ContentAction.START, "maqil_story__001__first.wav", 0.85, 2.5)
check("content: START at a non-zero position does not fail",
      "PLAY-FAILED" not in line and "START" in line, line)
check("content: START reports the requested position", "@2.5s" in line, line)
check("content: it is playing", p.is_playing is True)
p.apply(ContentAction.STOP)
p.apply(ContentAction.STOP)

# ===== 5) ملف مفقود وتالف: PLAY-FAILED لا صمت =====
line = p.apply(ContentAction.START, "does_not_exist.wav", 0.85, 0.0)
check("content: a missing file reports PLAY-FAILED", "PLAY-FAILED" in line, line)
check("content: a missing file is not playing", p.is_playing is False)
line = p.apply(ContentAction.START, "broken.wav", 0.85, 0.0)
check("content: a corrupt file reports PLAY-FAILED", "PLAY-FAILED" in line, line)
check("content: a corrupt file is not playing", p.is_playing is False)

# الفشل لا يُبلَّغ عن تشغيل
check(
    "content: no action claims a play it did not do",
    p.current_file is None,
    p.current_file,
)

# ===== 6) مستوى خارج المدى يُقصّ =====
p.apply(ContentAction.START, "maqil_story__001__first.wav", 5.0, 0.0)
check("content: an out-of-range volume does not raise", p.is_playing is True)
p.apply(ContentAction.STOP)

# ===== 7) حارس music_claimed =====
bg = RealPlayer()
check("guard: the background starts unclaimed", bg.music_claimed is False)
blocked = RealContentPlayer(lib, background=bg)
check(
    "guard: an unclaimed background leaves content available",
    blocked.available is True,
    blocked.unavailable,
)
blocked.close()

bg.music_claimed = True
guarded = RealContentPlayer(lib, background=bg)
check(
    "guard: a claimed music path disables content",
    guarded.available is False,
    guarded.available,
)
check("guard: the backend says why", guarded.backend == "claimed", guarded.backend)
check(
    "guard: the reason is recorded",
    guarded.unavailable is not None and "mixer.music" in guarded.unavailable.reason,
    guarded.unavailable.reason if guarded.unavailable else None,
)
line = guarded.apply(ContentAction.START, "maqil_story__001__first.wav", 0.85, 0.0)
check(
    "guard: START logs a skip with the reason",
    "SKIP" in line and "mixer.music" in line,
    line,
)
check("guard: nothing is claimed as playing", guarded.is_playing is False)
check("guard: a skip never claims a file", guarded.current_file is None)
for act in (ContentAction.PAUSE, ContentAction.RESUME, ContentAction.STOP):
    line = guarded.apply(act)
    check(f"guard: {act.value} is skipped too", "SKIP" in line, line)
check(
    "guard: the history is bounded", len(guarded.history) <= 500, len(guarded.history)
)
guarded.close()

# ===== 8) الخلفية لا توقف music الذي لم تحجزه =====
# هذا هو العيب الذي يقطع السرد عند كتم الخلفية: stop() كان يوقف
# music دائماً. الآن لا يوقفه إلا إن كان هو من استعمله.
# العيب الحقيقي: stop() كان يوقف music دائماً، فيقطع السرد عند كتم
# الخلفية أو إغلاق التطبيق. الفحص يراقب mixer نفسه لا العلم.
bg2 = RealPlayer()
check("stop: the background is unclaimed", bg2.music_claimed is False)
cp2 = RealContentPlayer(lib, background=bg2)
cp2.apply(ContentAction.START, "maqil_story__001__first.wav", 0.85, 0.0)
check("stop: the content is really broadcasting", cp2.is_playing is True)
import pygame as _pg_mod

check("stop: the mixer is busy with the narration",
      _pg_mod.mixer.music.get_busy() is True)
bg2.stop()
check("stop: stopping the background leaves the narration playing",
      cp2.is_playing is True and _pg_mod.mixer.music.get_busy() is True,
      f"playing={cp2.is_playing} busy={_pg_mod.mixer.music.get_busy()}")

# الآن العكس: من يحجز music يُوقِفه
bg3 = RealPlayer()
bg3.music_claimed = True
cp3 = RealContentPlayer(lib, background=bg3)
check("stop: a claimed background has no content path", cp3.available is False)
bg3.stop()
check("stop: the claimed music was released",
      _pg_mod.mixer.music.get_busy() is False,
      _pg_mod.mixer.music.get_busy())
cp2.close()
cp3.close()

# ===== 9) التشخيص لا يشارك في القرار =====
p2 = RealContentPlayer(lib)
check("diag: no position when idle", p2.device_position_sec() == -1.0)
p2.apply(ContentAction.START, "maqil_story__001__first.wav", 0.85, 0.0)
time.sleep(0.3)
check("diag: a position when playing", p2.device_position_sec() >= 0.0)
p2.close()
check("diag: no position after close", p2.device_position_sec() == -1.0)

p.close()
shutil.rmtree(tmp, ignore_errors=True)
print(f"\nRESULT: {PASSED} passed / {FAILED} failed")
sys.exit(1 if FAILED else 0)
