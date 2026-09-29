"""
sound_synth.py - توليد ملفات صوتية حقيقية (WAV) بدون اعتماديات خارجية
يولّد أصوات طبيعة قابلة للتكرار (loopable) باستخدام numpy فقط.
تشغيل: python -m src.context_aware_audio.sound_synth
"""

import math
import wave
from pathlib import Path

import numpy as np

from .paths import assets_dir

SR = 22050
DURATION_SEC = 8.0

ASSETS_DIR = assets_dir()


def _write_wav(path: Path, samples: np.ndarray) -> None:
    samples = np.clip(samples, -1.0, 1.0)
    # crossfade اخر 0.5ث مع البداية ليكون loop ناعماً
    n_fade = int(SR * 0.5)
    if len(samples) > n_fade * 2:
        fade_out = np.linspace(1.0, 0.0, n_fade)
        fade_in = np.linspace(0.0, 1.0, n_fade)
        head = samples[:n_fade].copy()
        tail = samples[-n_fade:].copy()
        samples[:n_fade] = head * fade_in + tail * fade_out
        samples = samples[:-n_fade]
    pcm = (samples * 32767).astype(np.int16)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def _noise(n: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.standard_normal(n)


def _lowpass(x: np.ndarray, window: int) -> np.ndarray:
    if window <= 1:
        return x
    kernel = np.ones(window) / window
    return np.convolve(x, kernel, mode="same")


def _normalize(x: np.ndarray, peak: float = 0.5) -> np.ndarray:
    m = np.max(np.abs(x))
    if m < 1e-9:
        return x
    return x * (peak / m)


def mountain_breeze_birds() -> np.ndarray:
    n = int(SR * DURATION_SEC)
    wind = _lowpass(_noise(n, 11), 220) * 0.6
    t = np.arange(n) / SR
    out = wind.copy()
    rng = np.random.default_rng(12)
    # زقزقة عصافير: 6 تغريدات sine-sweep عشوائية
    for _ in range(6):
        start = int(rng.integers(0, n - SR))
        length = int(rng.integers(int(SR * 0.15), int(SR * 0.4)))
        f0 = float(rng.uniform(2200, 3200))
        f1 = float(rng.uniform(3200, 4500))
        tt = np.arange(length) / SR
        sweep = np.sin(
            2 * math.pi * (f0 * tt + (f1 - f0) * tt * tt / (2 * max(tt[-1], 1e-6)))
        )
        env = np.sin(math.pi * tt / max(tt[-1], 1e-6)) ** 2
        out[start : start + length] += sweep * env * 0.25
    out += 0.03 * np.sin(2 * math.pi * 0.2 * t)
    return _normalize(out, 0.5)


def water_stream() -> np.ndarray:
    n = int(SR * DURATION_SEC)
    base = _lowpass(_noise(n, 21), 24)
    t = np.arange(n) / SR
    burble = (0.6 + 0.4 * np.sin(2 * math.pi * 5 * t)) * (
        0.7 + 0.3 * np.sin(2 * math.pi * 7.3 * t)
    )
    return _normalize(base * burble, 0.5)


def light_rain_leaves() -> np.ndarray:
    n = int(SR * DURATION_SEC)
    rng = np.random.default_rng(31)
    out = np.zeros(n)
    # قطرات: نبضات عشوائية باضمحلال سريع
    for _ in range(350):
        pos = int(rng.integers(0, n - 200))
        length = int(rng.integers(30, 160))
        decay = np.exp(-np.arange(length) / (length / 4))
        freq = float(rng.uniform(3000, 7000))
        tt = np.arange(length) / SR
        out[pos : pos + length] += np.sin(2 * math.pi * freq * tt) * decay * 0.3
    out += _lowpass(_noise(n, 32), 60) * 0.08
    return _normalize(out, 0.4)


def warm_breeze() -> np.ndarray:
    n = int(SR * DURATION_SEC)
    base = _lowpass(_noise(n, 41), 400)
    t = np.arange(n) / SR
    swell = 0.65 + 0.35 * np.sin(2 * math.pi * 0.2 * t)
    return _normalize(base * swell, 0.5)


def sea_waves_fireplace() -> np.ndarray:
    n = int(SR * DURATION_SEC)
    t = np.arange(n) / SR
    waves = _lowpass(_noise(n, 51), 300) * (0.6 + 0.4 * np.sin(2 * math.pi * 0.12 * t))
    rng = np.random.default_rng(52)
    crackle = np.zeros(n)
    for _ in range(120):
        pos = int(rng.integers(0, n - 100))
        length = int(rng.integers(20, 90))
        crackle[pos : pos + length] += (
            rng.standard_normal(length) * 0.35 * np.exp(-np.arange(length) / 20)
        )
    return _normalize(waves + crackle * 0.5, 0.5)


def crickets_light() -> np.ndarray:
    n = int(SR * DURATION_SEC)
    t = np.arange(n) / SR
    carrier = np.sin(2 * math.pi * 4200 * t)
    gate = (np.sin(2 * math.pi * 22 * t) > 0).astype(float) * 0.7 + 0.3
    chirp = carrier * gate * 0.18
    floor = _lowpass(_noise(n, 61), 500) * 0.05
    return _normalize(chirp + floor, 0.35)


def distant_waterfall() -> np.ndarray:
    n = int(SR * DURATION_SEC)
    base = _lowpass(_noise(n, 71), 60)
    delayed = np.zeros_like(base)
    d = SR // 3
    delayed[d:] = base[:-d] * 0.35
    return _normalize(base + delayed, 0.5)


def wind_coffee_trees() -> np.ndarray:
    n = int(SR * DURATION_SEC)
    base = _lowpass(_noise(n, 81), 120)
    t = np.arange(n) / SR
    rustle = 0.7 + 0.3 * np.sin(2 * math.pi * 1.7 * t) * np.sin(2 * math.pi * 0.6 * t)
    hiss = (
        _lowpass(_noise(n, 82), 12) * 0.12 * (0.5 + 0.5 * np.sin(2 * math.pi * 2.3 * t))
    )
    return _normalize(base * rustle + hiss, 0.5)


def athan_chime() -> np.ndarray:
    n = int(SR * 4.0)
    out = np.zeros(n)
    for i, freq in enumerate((523.25, 659.25, 783.99)):
        start = int(i * SR * 1.0)
        length = int(SR * 1.2)
        tt = np.arange(length) / SR
        tone = np.sin(2 * math.pi * freq * tt) * np.exp(-tt * 2.5) * 0.4
        seg = min(length, n - start)
        if seg > 0:
            out[start : start + seg] += tone[:seg]
    return _normalize(out, 0.5)


BUILDERS = {
    "mountain_breeze_birds.wav": mountain_breeze_birds,
    "water_stream.wav": water_stream,
    "light_rain_leaves.wav": light_rain_leaves,
    "warm_breeze.wav": warm_breeze,
    "sea_waves_fireplace.wav": sea_waves_fireplace,
    "crickets_light.wav": crickets_light,
    "distant_waterfall.wav": distant_waterfall,
    "wind_coffee_trees.wav": wind_coffee_trees,
    "athan_chime.wav": athan_chime,
}


def ensure_assets(force: bool = False) -> list:
    made = []
    for name, fn in BUILDERS.items():
        path = ASSETS_DIR / name
        if path.exists() and not force:
            continue
        _write_wav(path, fn())
        made.append(str(path))
    return made


def main() -> None:
    made = ensure_assets(force=True)
    print(f"generated {len(made)} files in {ASSETS_DIR}")
    for p in made:
        print(f"  {p}")


if __name__ == "__main__":
    main()
