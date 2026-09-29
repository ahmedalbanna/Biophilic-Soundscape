---
name: windows-tk-audio
description: Windows desktop app pitfalls for Tkinter UIs and audio playback in Python. Use when building or debugging a Tkinter GUI, wiring a mic, playing sound, freezing to an exe, or when Arabic/emoji output crashes the console.
---

# Windows + Tkinter + Audio Pitfalls

Hard-won constraints on Windows, Python 3.12, Tkinter 8.6, pygame, sounddevice.
Each item below caused a real failure in this project.

## Console encoding — the first thing to break

`cp1252` is the default stdout encoding. Arabic text and emoji raise
`UnicodeEncodeError` and kill the process mid-run.

| Fix | When |
|---|---|
| `set PYTHONUTF8=1` in the launcher `.bat` | Console scripts, always |
| `chcp 65001 >nul` in the launcher `.bat` | Console scripts, always |
| `sys.stdout.reconfigure(encoding="utf-8", errors="replace")` | Library entry points |

A crash inside a `print` is not a test failure — it is a crash. If a script
"passes" on a machine with UTF-8 already set, it is untested on a default
Windows shell.

## Tkinter is not thread-safe

Tk calls must happen on the thread that created the root window. Calling any
widget method from a worker thread raises
`RuntimeError: main thread is not in main loop`, and `root.after(0, ...)` from a
thread is *not* a fix — it registers the same failing command.

**Use a queue.** The worker posts data; the main thread's existing tick loop
drains it.

```python
# worker thread
self._queue.put(("prayer", times))

# main thread, inside the existing root.after loop
def _drain(self):
    try:
        while True:
            kind, payload = self._queue.get_nowait()
            ...  # touch widgets here
    except queue.Empty:
        pass
```

Prefer this over `after(0, ...)`: one drain point, no ordering surprises, and
the widget update lands in the same tick as everything else.

## Widget config needs keyword arguments

```python
label.config("some text")          # TclError: unknown option "-some text"
label.config(text="some text")     # correct
```

Same trap with `pack`, `grid`, `create_text`. Always keyword.

## Silent failure is the real hazard

```python
try:
    self.log.insert("end", msg)
except Exception:
    pass
```

This hides bugs permanently. A `try/except: pass` around UI updates will
swallow an `AttributeError` from a widget that was never created, and you get
a blank panel instead of a traceback. Use it only around genuinely optional
work, and verify the happy path explicitly in a smoke test.

## Code that lands in the wrong scope

Appending a method near similar code is how a widget-building block ends up
inside an unrelated event handler. After any such edit:

- `flake8 --select=F` — catches `undefined name 'f'`
- Call the handler once in a test: tick the checkbox, press the button

Do not trust that "the app still starts" means the UI is intact.

## Microphone

```python
import sounddevice as sd   # preferred: callback based, no polling thread
```

- 16 kHz, mono, `dtype="int16"`, `blocksize=1600` (~100ms) is a good baseline.
- Verify a device exists before offering it: `sd.query_devices()` and filter
  `max_input_channels > 0`.
- A quiet room reads near 0 dBFS. Calibrate a noise floor rather than trusting
  fixed thresholds.
- The sounddevice callback runs on an audio thread: it must not block, and it
  should not allocate heavily. Copy the buffer, compute, store under a lock.
- `pyaudio` needs `PyAudio().terminate()` to release the port. If you construct
  it, own its teardown.

## Playback

Preference order that worked: `pygame` (smooth volume and crossfade) →
`winsound` (always present, but no volume control) → log only.

- Volume changes in coarse buckets (0.1 steps) to avoid rewriting the mixer
  every tick.
- Crossfade needs two mixer channels: play the new file on the idle channel,
  fade out the other.
- Windows has no per-app master volume. `winsound` cannot duck at all — if
  ducking matters, pygame is the floor, not the ceiling.

## PyInstaller

```bat
python -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name app --add-data "assets;assets" --hidden-import=pygame --hidden-import=sounddevice entry.py
```

- Paths: bundled data is read-only and lives under `sys._MEIPASS`. Anything
  written at runtime (settings, caches, temp files) must go to
  `%LOCALAPPDATA%\<App>`, or it vanishes and may be unwritable.
- `sys.frozen` / `sys._MEIPASS` do not exist when running from source. Guard
  every path helper or `pip`-installed development runs break.
- An autostart `.bat` written into the Windows Startup folder must call
  `sys.executable` when frozen, and `python -m ...` when not. Writing the
  wrong one produces an entry that silently does nothing.
- `--windowed` suppresses stdout, so a crash is invisible. Log to a file.

## Smoke test the UI, do not just construct it

```python
root = tk.Tk(); root.withdraw()      # withdraw avoids stealing focus
app = MyApp(root); app.start()
for _ in range(20): root.update(); time.sleep(0.2)
# assert on real state, exercise every callback
app._on_close()
```

Instantiating proves nothing about layout or handlers. Drive the loop, then
call the risky callbacks (toggles, calibrations, device switches) explicitly.
