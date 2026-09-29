"""
real_player.py - مشغل صوت حقيقي على سطح المكتب
يدعم ثلاث طبقات بالترتيب: pygame (crossfade و fade ناعم)،
winsound (بديل Windows القياسي)، أو log فقط إن تعذر كل شيء.
يستهلك نفس PlaybackCommand الذي يصدره المحرك.
"""

import threading
import time
import wave
from pathlib import Path

import numpy as np

from .audio_types import PlaybackCommand
from .paths import assets_dir, user_data_dir
from .sound_synth import ensure_assets


class RealPlayer:
    """مشغّل حقيقي بصوت فعلي: crossfade، fade ناعم، master volume، وEQ."""

    def __init__(self):
        self.current_file = None
        self.current_volume = 1.0
        self.is_muted = True
        self.history = []
        self.backend = "log"
        self.master_volume = 1.0
        self.master_muted = False
        self.eq_gain = 1.0
        self._lock = threading.Lock()
        self._scaled_cache = {}
        self._tmp_dir = user_data_dir() / "_scaled"
        self._winsound = None
        self._pg = None
        self._sounds = {}
        self._active_idx = 0
        self._ramp_gen = 0
        try:
            ensure_assets(force=False)
        except Exception:
            pass
        try:
            import pygame  # type: ignore

            pygame.mixer.init(frequency=22050, size=-16, channels=2, buffer=512)
            pygame.mixer.set_num_channels(8)
            self._pg = pygame
            self.backend = "pygame"
        except Exception:
            self._pg = None
            try:
                import winsound as _w

                self._winsound = _w
                self.backend = "winsound"
            except Exception:
                self.backend = "log"

    # ---------- إعدادات الإخراج ----------
    def set_master_volume(self, v: float) -> None:
        with self._lock:
            self.master_volume = max(0.0, min(1.0, v))

    def set_master_muted(self, m: bool) -> None:
        with self._lock:
            self.master_muted = bool(m)
            if m:
                self._fadeout_all(300)

    def set_eq(self, gain: float) -> None:
        with self._lock:
            self.eq_gain = max(0.0, min(1.5, gain))

    def _effective(self, ratio: float) -> float:
        return max(0.0, min(1.0, ratio * self.master_volume * self.eq_gain))

    # ---------- winsound fallback ----------
    def _scaled_path(self, name: str, ratio: float) -> str | None:
        bucket = round(max(0.0, min(1.0, ratio)) * 10) / 10
        key = (name, bucket)
        if key in self._scaled_cache:
            return self._scaled_cache[key]
        src = assets_dir() / name
        if not src.exists():
            return None
        if bucket >= 0.99:
            self._scaled_cache[key] = str(src)
            return str(src)
        try:
            self._tmp_dir.mkdir(parents=True, exist_ok=True)
            dst = self._tmp_dir / f"{Path(name).stem}_v{int(bucket * 100):03d}.wav"
            if not dst.exists():
                with wave.open(str(src), "rb") as r:
                    params = r.getparams()
                    frames = r.readframes(r.getnframes())
                pcm = np.frombuffer(frames, dtype=np.int16).astype(np.float32)
                pcm = np.clip(pcm * bucket, -32768, 32767).astype(np.int16)
                with wave.open(str(dst), "wb") as w:
                    w.setparams(params)
                    w.writeframes(pcm.tobytes())
            self._scaled_cache[key] = str(dst)
            return str(dst)
        except Exception:
            return str(src)

    def _play_loop_winsound(self, path: str | None) -> None:
        if self._winsound is None:
            return
        try:
            if path is None:
                self._winsound.PlaySound(None, self._winsound.SND_PURGE)
            else:
                flags = (
                    self._winsound.SND_FILENAME
                    | self._winsound.SND_ASYNC
                    | self._winsound.SND_LOOP
                )
                self._winsound.PlaySound(path, flags)
        except Exception:
            pass

    # ---------- pygame: crossfade + ramp ----------
    def _ensure_sound(self, name: str):
        if name in self._sounds:
            return self._sounds[name]
        src = str(assets_dir() / name)
        snd = self._pg.mixer.Sound(src)
        self._sounds[name] = snd
        return snd

    def _fadeout_all(self, fade_ms: int) -> None:
        if self._pg is None:
            return
        try:
            for i in range(2):
                self._pg.mixer.Channel(i).fadeout(max(0, fade_ms))
        except Exception:
            pass

    def _ramp_volume(
        self, idx: int, start: float, end: float, duration_sec: float
    ) -> None:
        self._ramp_gen += 1
        gen = self._ramp_gen
        if duration_sec <= 0.05 or abs(end - start) < 0.005:
            try:
                self._pg.mixer.Channel(idx).set_volume(end)
            except Exception:
                pass
            return

        def work():
            steps = max(2, int(duration_sec / 0.05))
            for s in range(1, steps + 1):
                with self._lock:
                    if gen != self._ramp_gen:
                        return
                v = start + (end - start) * s / steps
                try:
                    self._pg.mixer.Channel(idx).set_volume(v)
                except Exception:
                    return
                time.sleep(0.05)

        threading.Thread(target=work, daemon=True).start()

    def _pg_crossfade(self, name: str, vol: float, fade_ms: int) -> None:
        snd = self._ensure_sound(name)
        new_idx = 1 - self._active_idx
        old_idx = self._active_idx
        try:
            new_ch = self._pg.mixer.Channel(new_idx)
            old_ch = self._pg.mixer.Channel(old_idx)
            new_ch.play(snd, loops=-1, fade_ms=max(0, fade_ms))
            new_ch.set_volume(vol)
            if fade_ms > 0:
                old_ch.fadeout(fade_ms)
            else:
                old_ch.stop()
        except Exception:
            try:
                self._pg.mixer.music.load(str(assets_dir() / name))
                self._pg.mixer.music.set_volume(vol)
                self._pg.mixer.music.play(loops=-1, fade_ms=max(0, fade_ms))
            except Exception:
                pass
        self._active_idx = new_idx

    # ---------- API ----------
    def apply(self, cmd: PlaybackCommand) -> str:
        with self._lock:
            eff = self._effective(cmd.volume_ratio)
            if cmd.is_muted or cmd.file is None or self.master_muted or eff <= 0.001:
                action = "MUTE"
                if not self.is_muted:
                    if self._pg is not None:
                        fade_ms = int(max(0.0, cmd.fade_duration_sec) * 1000) or 300
                        self._ramp_gen += 1
                        self._fadeout_all(fade_ms)
                    else:
                        self._play_loop_winsound(None)
                self.is_muted = True
                self.current_volume = 0.0
            elif self._pg is not None:
                fade_ms = int(max(0.0, cmd.fade_duration_sec) * 1000)
                if self.is_muted or self.current_file != cmd.file:
                    try:
                        self._ramp_gen += 1
                        self._pg_crossfade(cmd.file, eff, fade_ms or 400)
                    except Exception:
                        pass
                    action = f"PLAY {cmd.file} vol={eff:.0%}"
                else:
                    if abs(eff - self.current_volume) >= 0.01:
                        self._ramp_volume(
                            self._active_idx,
                            self.current_volume,
                            eff,
                            min(cmd.fade_duration_sec or 0.3, 1.5),
                        )
                        action = f"FADE {cmd.file} {self.current_volume:.0%}->{eff:.0%}"
                    else:
                        action = f"KEEP {cmd.file} vol={eff:.0%}"
                self.current_file = cmd.file
                self.current_volume = eff
                self.is_muted = False
            else:
                path = self._scaled_path(cmd.file, eff)
                key = (cmd.file, round(eff * 10) / 10)
                current_key = (self.current_file, round(self.current_volume * 10) / 10)
                if self.is_muted or current_key != key:
                    if path is not None:
                        self._play_loop_winsound(path)
                    action = f"PLAY {cmd.file} vol={eff:.0%}"
                else:
                    action = f"KEEP {cmd.file} vol={eff:.0%}"
                self.current_file = cmd.file
                self.current_volume = eff
                self.is_muted = False
            line = f"{action} [{cmd.state.value}] {cmd.reason} (backend={self.backend})"
            self.history.append(line)
            return line

    def stop(self) -> None:
        with self._lock:
            try:
                if self._pg is not None:
                    self._ramp_gen += 1
                    self._fadeout_all(400)
                    try:
                        self._pg.mixer.music.stop()
                    except Exception:
                        pass
            except Exception:
                pass
            self._play_loop_winsound(None)
            self.is_muted = True

    def play_once(self, name: str, volume: float = 0.8) -> bool:
        """تشغيل لقطة واحدة غير متكررة (تنبيه الأذان) دون قطع الخلفية."""
        with self._lock:
            if self.master_muted:
                return False
            vol = max(0.0, min(1.0, volume * self.master_volume))
            try:
                if self._pg is not None:
                    src = str(assets_dir() / name)
                    snd = self._pg.mixer.Sound(src)
                    ch = self._pg.mixer.Channel(7)
                    ch.set_volume(vol)
                    ch.play(snd, loops=0)
                    return True
                if self._winsound is not None:
                    src = str(assets_dir() / name)
                    self._winsound.PlaySound(
                        src, self._winsound.SND_FILENAME | self._winsound.SND_ASYNC
                    )
                    return True
            except Exception:
                pass
            return False
