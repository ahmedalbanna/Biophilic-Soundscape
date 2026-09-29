"""
mic_input.py - التقاط الميكروفون الحقيقي (اختياري)
يعمل مع sounddevice أو pyaudio إن وُجدا، وإلا يعود لوضع منزلق يدوي.
"""

import threading
import time


class MicInput:
    """يلتقط الصوت من الميكروفون ويحوّله إلى مستوى dB."""

    def __init__(self, sample_rate: int = 16000, device=None):
        self.sample_rate = sample_rate
        self.device = device
        self.backend = None
        self.available = False
        self.noise_floor_db: float | None = None
        self._db = 0.0
        self._lock = threading.Lock()
        self._stream = None
        self._pyaudio = None
        self._stop_flag = True
        self._last_pcm: bytes = b""
        self._sd = None
        self._detect_backend()

    @property
    def is_running(self) -> bool:
        """هل التيار مفتوح حالياً؟ (بدل قراءة الحالة الخاصة من الخارج)"""
        return self._stream is not None

    def _detect_backend(self) -> None:
        """يبحث عن مكتبة التقاط متاحة: sounddevice أولاً ثم pyaudio."""
        self._sd = None
        self._pyaudio = None
        self.backend = None
        self.available = False
        try:
            import sounddevice as sd  # type: ignore

            self._sd = sd
            self.backend = "sounddevice"
            self.available = True
            return
        except Exception:
            pass
        try:
            import pyaudio  # type: ignore

            self._pyaudio = pyaudio
            self.backend = "pyaudio"
            self.available = True
        except Exception:
            pass

    def start(self) -> bool:
        if not self.available or self._stream is not None:
            return self.available
        try:
            if self.backend == "sounddevice":

                def cb(indata, frames, time_info, status):
                    db = self._pcm_to_db(bytes(indata))
                    with self._lock:
                        self._db = db
                        self._last_pcm = bytes(indata)

                self._stream = self._sd.InputStream(
                    samplerate=self.sample_rate,
                    channels=1,
                    dtype="int16",
                    callback=cb,
                    blocksize=1600,
                    device=self.device,
                )
                self._stream.start()
                return True
            if self.backend == "pyaudio":
                pa = self._pyaudio.PyAudio()
                stream = pa.open(
                    format=self._pyaudio.paInt16,
                    channels=1,
                    rate=self.sample_rate,
                    input=True,
                    frames_per_buffer=1600,
                )
                self._stream = stream
                self._stop_flag = False

                def loop():
                    while not self._stop_flag:
                        try:
                            data = stream.read(1600, exception_on_overflow=False)
                            db = self._pcm_to_db(bytes(data))
                            with self._lock:
                                self._db = db
                                self._last_pcm = bytes(data)
                        except Exception:
                            break

                t = threading.Thread(target=loop, daemon=True)
                t.start()
                self._thread = t
                return True
        except Exception:
            self.available = False
            return False
        return False

    @staticmethod
    def _pcm_to_db(pcm: bytes) -> float:
        """PCM16 little-endian -> dB. يحسبها VAD حتى لا يتكرر التنفيذ في مكانين."""
        from .vad import VadProcessor

        return VadProcessor.pcm_to_db(pcm)

    def read_db(self) -> float | None:
        if not self.available or self._stream is None:
            return None
        with self._lock:
            return float(self._db)

    def read_pcm(self) -> bytes:
        with self._lock:
            return bytes(self._last_pcm)

    @staticmethod
    def list_devices() -> list:
        """يعيد [(id, name)] لأجهزة الإدخال المتاحة."""
        out = []
        try:
            import sounddevice as sd  # type: ignore

            for i, d in enumerate(sd.query_devices()):
                if d.get("max_input_channels", 0) > 0:
                    out.append((i, d.get("name", str(i))))
        except Exception:
            pass
        return out

    def calibrate(self, seconds: float = 2.0) -> float | None:
        """يقيس ضجيج الغرفة أثناء الصمت ويحفظه كأرضية ضجيج."""
        if not self.available or self._stream is None:
            return None
        vals = []
        end = time.time() + max(0.5, seconds)
        while time.time() < end:
            db = self.read_db()
            if db is not None:
                vals.append(db)
            time.sleep(0.1)
        if not vals:
            return None
        vals.sort()
        floor = float(vals[len(vals) // 2])
        with self._lock:
            self.noise_floor_db = floor
        return floor

    def stop(self) -> None:
        self._stop_flag = True
        try:
            if self.backend == "sounddevice" and self._stream is not None:
                self._stream.stop()
                self._stream.close()
            elif self.backend == "pyaudio" and self._stream is not None:
                self._stop_flag = True
                self._stream.stop_stream()
                self._stream.close()
        except Exception:
            pass
        finally:
            self._stream = None
            if self._pyaudio is not None:
                try:
                    self._pyaudio.terminate()
                except Exception:
                    pass
                self._pyaudio = None
