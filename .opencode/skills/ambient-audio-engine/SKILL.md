---
name: ambient-audio-engine
description: Building adaptive ambient audio engines - VAD, noise-floor calibration, dB-based ducking, crossfades, priority state machines, and synthesizing seamless loop assets with numpy. Use when designing or tuning audio that reacts to room activity and a daily schedule.
---

# Ambient Audio Engine Design

An engine that plays background sound and reacts to speech, a cultural daily
schedule, and hard mute windows. Patterns that proved out in practice.

## Priority chain, not an if/elif ladder

Order the rules by how strongly they must not be violated, and return early.
A prayer lock that a "welcome chime" can override is a bug, not a preference.

```python
if prayer_muted:      return mute
if loud_debate:       return mute
if greeting:          return welcome
if sleep_window:      return night_ambient
if long_silence:      return fade_in
if is_speech:         return ducked
return daily_ambient
```

Each branch returns a complete `PlaybackCommand` — file, target dB, volume
ratio, fade duration, state, reason. The player stays dumb and never decides
policy. A `reason` string on every command makes the whole decision traceable
in logs, which is how you debug "why is it quiet".

## Normalize to one measurement

Convert everything to dB at the boundary and work in dB internally. Mixing
linear amplitude and dB is how thresholds silently disagree.

```python
# 16-bit PCM -> dB, referenced so a quiet room lands near 0-10
db = 20 * math.log10(rms / 32768) + 100
db = max(0.0, min(100.0, db))
```

## Calibrate the noise floor; do not hardcode thresholds

A room that reads 15 dB at 3am and 35 dB at noon needs different thresholds.
Measure a median over a couple of seconds while the room is quiet, then raise
the speech threshold above it:

```python
threshold = max(config.speech_threshold_db, noise_floor_db + margin_db)
```

Keep the margin in config, not in the code. Use the median, not the mean — one
door slam should not move the baseline.

## Speech detection: energy, then persistence

Energy alone triggers constantly on coughs, doors, and music. Require three
things together:

- level above threshold
- sustained for a short window (0.2-0.3s) — filters transients
- a cool-down before re-firing (3s) — prevents chatter

Ducking maps level to ratio continuously rather than as two states, so the
transition is not audible:

```python
t = (db - lo) / max(hi - lo, 1e-6)
ratio = max_ratio - t * (max_ratio - min_ratio)
```

Optional `webrtcvad` upgrades the decision; keep energy-only as the fallback so
the app runs with no optional deps. Probe with `importlib.util.find_spec`.

## Timers are state, and reset must be honest

Silence, loudness, and activity each need a "since" timestamp. The subtle bug:
silence begins when speech *ended*, not when the first silent frame arrived —
otherwise a 10s fade-in fires 10s late.

```python
if frame.is_speech:
    self._last_speech = ts
    self._silence_since = None
elif self._silence_since is None:
    self._silence_since = self._last_speech if self._last_speech else ts
```

If you expose a `reset()`, it must clear every timer. A forgotten field shows
up as a state that leaks across scenario boundaries in tests.

## Fades and crossfades

- Ducking: 1s fade out when speech starts, 3s fade in when it stops. Asymmetric
  on purpose — ducking down late is noticeable, coming back late is not.
- File changes need a real crossfade, not a restart. Two channels, alternate
  between them, fade the outgoing one.
- Volume changes: ramp in ~50ms steps from a worker thread, guarded by a
  generation counter so a newer ramp cancels an older one.
- Round volume to buckets (0.1) before comparing, or every tick triggers a
  mixer write and you get clicks.

## Generate loopable assets with numpy

No audio library needed. Filtered noise gives wind and water; sparse decaying
sine bursts give birds and rain; a gated sine gives crickets; a lowpass of
lowpass noise gives distant water.

Seamless loop: crossfade the tail into the head, then truncate.

```python
n_fade = int(SR * 0.5)
head, tail = samples[:n_fade].copy(), samples[-n_fade:].copy()
samples[:n_fade] = head * np.linspace(0, 1, n_fade) + tail * np.linspace(1, 0, n_fade)
samples = samples[:-n_fade]
```

Always `_normalize()` to a peak around 0.5 to leave headroom, and write
16-bit mono. Build into a cache dir and skip existing files so startup is fast.

## Test the decision logic with time injection

Feed frames with explicit timestamps instead of sleeping. Timers become
instant and the suite stays deterministic:

```python
cmd = engine.process_frame(frame(t0, db=70, speech=True), now)
cmd2 = engine.process_frame(frame(t0 + 95, db=30, speech=False), now + timedelta(seconds=95))
assert not cmd2.is_muted          # 60s cooldown elapsed
```

Test the priority order explicitly — the interesting failures are conflicts
(speech during a prayer lock), not the happy path.

## Separate simulated and real players

Keep a no-audio `SimulatedPlayer` for tests and a `RealPlayer` for the desktop
app, both consuming the same `PlaybackCommand`. Tests stay fast and
hardware-independent; the simulation stays runnable with no sound card.
