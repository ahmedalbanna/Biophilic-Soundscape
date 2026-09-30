---
name: context-aware-audio-conventions
description: House conventions for the Context-Aware Audio Engine repo - module layout, the seven-state decision priority, Arabic docstring style, test conventions, and the run/test/build commands. Use when adding modules, changing decision logic, or writing tests in this project.
---

# Context-Aware Audio Engine — Project Conventions

Scoped to this repository. Complements the general-development skill; where
they conflict, these win.

## Commands

Windows console scripts need UTF-8; the launchers already set it.

```bat
run_desktop.bat                        # launch the desktop app
build_exe.bat                          # PyInstaller -> dist\context_audio.exe
```

```bash
set PYTHONUTF8=1
python -m src.context_aware_audio.sound_synth   # generate assets/*.wav
python -m src.context_aware_audio.simulate      # 6 scenarios, no audio hardware
python -m src.context_aware_audio.app           # desktop UI
python tests/test_engine.py                     # 128 checks - decision logic
python tests/test_content_store.py              #  26 - content DB + play log
python tests/test_content_library.py            #  41 - library folder scan
python tests/test_content_engine.py             #  84 - narration state machine
python tests/test_content_gate.py               #  52 - gate + priority integration
python tests/test_content_player.py             #  46 - pygame.music + music_claimed
python tests/test_content_settings.py           #  41 - settings coercion
python tests/test_content_pcm.py                #  34 - read_pcm + the tick gate
python tests/test_content_panel.py              #  51 - the Tk panel (real time)
python tests/test_simulate.py                   #  25 - scenarios 7 and 8
python tests/test_prose.py                      #   7 - no CJK, no English prose
python tests/test_desktop.py                    # 245 checks - assets/player/VAD/prayer/UI
```

Before declaring work done: `py_compile` all modules, `pyflakes`, all twelve
test files, and a UI smoke test. `simulate.py` is the fastest way to see a
decision change take effect.

## Module layout

```
src/context_aware_audio/
  config.py             # all thresholds and timings + location
  audio_types.py        # DayPeriod, EngineState, AudioFrame, PlaybackCommand
  vad.py                # dB -> speech/overlap/greeting
  cultural_calendar.py  # 24h schedule -> DayPeriod
  prayer_engine.py      # mute windows around adhan
  prayer_provider.py    # Aladhan fetch + cache
  engine.py             # the decision (this is the core)
  content_store.py      # SQLite: clips + play log (WAL) + once-per-day window
  content_library.py    # scans the user folder; <window>__<seq>__<title>
  content_engine.py     # narration state machine: gate, interrupt, resume
  simulated_player.py   # no audio, for tests and simulate.py
  real_player.py        # pygame -> winsound -> log
  real_content.py       # narration over mixer.music; executes, never decides
  mic_input.py          # sounddevice/pyaudio + calibration
  settings.py           # JSON persistence
  sound_synth.py        # numpy WAV generation
  sim_clock.py          # fakes wall clock for testing; engine is untouched
  app.py                # Tkinter UI
  simulate.py           # scenario runner
```

Two players, one interface. Anything that produces sound consumes
`PlaybackCommand` and never decides policy. Anything that decides policy never
touches audio. Keep that line.

Content is a guest, not an eighth priority. Every background-emitting branch
in `engine.py` routes through `_with_content(cmd, frame, ts, now, period)`;
the two mute branches (prayer, debate) bypass it and call
`force_stop` / `force_pause`, then `_attach` the result to the command so the
player still learns. The ambient floor is a **ceiling**, applied as
`min(cmd.volume_ratio, content_ambient_ratio)`.

Not a replacement. Replacing the duck ratio outright pins the background to
20% whatever the curve decided: at 64.9dB the engine decides 0.10 and the log
says "90% reduction" while the mixer gets 0.20 — louder than decided, 0.1dB
from the debate mute, with `MIN_DUCK_RATIO` unreachable for as long as content
plays. Multiplication is equally wrong for the opposite reason: 0.20 x 0.10 is
2%, i.e. silence. The ceiling caps in both directions.

Naming: modules snake_case, classes CapWords, one public class per module.
Prefer descriptive over terse — `athan_moment` beats `is_a`, `set_noise_floor`
beats `cal`.

## The seven-state decision priority

`engine.py` returns early at each step. Order is the specification, not a
preference:

1. `PRAYER_MUTED` — adhan -3min through adhan +20min +15min lock
2. `DEBATE_MUTED` — dB >= 65 with overlap; returns after 60s calm
3. `WELCOME` — greeting tone; distant waterfall at 15% for 8s
4. `SLEEP_SILENCE` — night + 5min without activity; 20dB crickets or off
5. `CONTEMPLATION_FADE` — 10s silence; 3s fade-in (wind through coffee trees
   in maqil, otherwise the daily sound)
6. `DUCKED` — ordinary speech; 70-90% reduction, 1s fade-out
7. `DAILY_AMBIENT` — the scheduled file at its scheduled level

Prayer must beat everything, including an explicit welcome. Do not add a
branch above it.

A hard mute also clears transient state (`_in_debate_mute = False`), so
returning from prayer does not resume a stale mute.

`reset()` clears every timer. If you add state, add it there too — a missed
field leaks state across scenario boundaries and shows up only in tests.

## Spec values live in config, not in code

Thresholds, timings, levels, sounds, and location are all `EngineConfig`
fields. Never inline a 65, a 10, or a filename in the engine. Changing behavior
should mean editing config.

Levels are per-period in `period_sounds[...]["db"]` — there are no separate
`morning_level_db`-style fields. Do not reintroduce them.

Duck ratios are **derived** from `duck_depth` through `engine.duck_max_ratio()`
and `duck_min_ratio()`. There are no `ducking_max_ratio` / `ducking_min_ratio`
config fields; they existed, nothing read them, and five tests compared
against them — agreeing only at the default depth of 70. A config field that
agrees with the live value at the default and diverges everywhere else is
worse than an absent one, because editing it appears to work.

`content_gate_delay_min` was also deleted, for the mirror-image reason: the
spec wrote it, nothing read it, and the engine counts postponements instead.

## Tests

Plain scripts with a `check(name, condition)` helper, not a test framework. No
dependencies, exit code from the failure count. Both files print a summary and
`sys.exit(1 if FAILED else 0)`.

```python
check("cumulative name", cond, f"got {value}")
```

Conventions that matter:
- No sleeping. Inject timestamps; timers are tested instantly. The one
  exception is `test_content_panel.py`: the Tk tick reads the wall clock, so
  waiting there is unavoidable and the file takes ~13s. Do not "fix" that by
  making the app injectable for tests.
- Test conflicts, not just happy paths: prayer vs welcome, cooldown expiry.
- `test_engine.py` for decision logic, `test_desktop.py` for hardware-adjacent
  code with hardware faked out. Content has its own five suites because the
  layers fail independently.
- A helper that returns only the *last* decision is a trap. `START` lands
  mid-window, so the final frame shows steady state, not the transition.
  Collect every action and assert on the set. This mistake cost four
  revisions of `test_content_gate.py`.
- Index a transition map with `.get(action, fallback)`, never `[action]`.
  A missing transition should fail a check, not raise `KeyError` and abort
  the file — a crash reads like a pass in a probe.
- `test_content_player.py` needs real pygame. The `music_claimed` guard and
  the `set_pos` order only appear against a real mixer; do not fake it.
- A crash is not a failing check. `list.index(x)` on a missing value,
  `dict[k]` on a missing key, and an assertion on a length both end the
  file, and a probe reads that as inconclusive. Guard membership first
  and let the check fail cleanly.
- Assert the invariant, not the symptom. "stop clears the block" passed
  with the clearing removed, because the `_stream` guard already returned
  nothing. Read the field when the invariant is about the field.
- When a test spies on two functions and one calls the other, the spy
  sees both. `analyze_pcm` calls `analyze_frame` internally; recording
  both made the gate look as if it had chosen `analyze_frame`.
- Cover every new code path, and reproduce each bug before fixing it. Three
  rounds of review review found bugs that the green suite could not see
  because the happy path was the only path tested. A test that cannot fail
  for the reason you fixed is not a regression test — a fake whose
  `terminate()` is missing will happily pass a handle-leak assertion.
- `pytest` is not the runner: these scripts call `sys.exit()`, so pytest
  errors during collection. That is expected, not a bug.
- **No test may depend on the wall clock or the cached prayer schedule.**
  Two desktop checks did, and both failed on a real afternoon: the debate
  latch between dhuhr and dhuhr+15min, and the maghrib mute whenever the
  cached times moved. Disable `engine.prayer.check` for a section that is
  not about prayer — no fixed set of five times leaves every minute of the
  day safe. Pin `prayer.set_times` only when the check asserts a specific
  claim at a specific time. Verify by poisoning `assets/prayer_cache.json`
  to five minutes ago and re-running.

## Prose is a gate, not a promise

`tests/test_prose.py` fails the build on a CJK character, a replacement
character, or an English phrase in an Arabic comment. The skill used to ask
for a manual scan and nobody ran one; two Korean characters reached `vad.py`
and `README.md` that way, plus five in total across the sources.

Its English check scans only the text after `#`, needs two adjacent Latin
words before it reports, and carries an allowlist for library names. An
earlier version scanned whole lines and produced 200 false positives on
`self.engine`, `False` and `for i in range` — a gate that cries wolf gets
switched off, so keep it precise even at the cost of coverage.

## Docstring and comment style

Arabic, matching the existing files. Fully Arabic prose — no English words
mixed into an Arabic sentence. It is easy to leak ("three backends", "Uses
config", and once `tanpa` and `contra` slipped in) and it reads as
unfinished. Run a scan over comments and docstrings before committing:
Latin words adjacent to Arabic letters, excluding identifiers in code.

```python
def athan_moment(self, now, window_sec=60):
    """اسم الصلاة إذا كنا ضمن نافذة لحظة الأذان، وإلا None."""
```

Every `PlaybackCommand` carries a `reason` in Arabic. It is how the decision
chain stays debuggable in logs and in the UI.

## Cleanup expectations

`build/`, `dist/`, `__pycache__/`, `.ruff_cache/`, `assets/_scaled/`, and the
runtime `assets/settings.json` + `assets/prayer_cache.json` are all disposable
and gitignored. The 9 WAV files in `assets/` are generated but committed, so a
fresh clone can run immediately.

Before finishing any task, check for dead code — grep before deleting to
confirm zero references, including in tests and the README. Wiring dead config
up to real use is better than deleting it, if the wiring is small.
