"""
بناء لوحة المحتوى في بيئة بلا شاشة، وضغط زر السحب فعلياً.

هذا فحص يدوي: Tk لا يعمل على جهازCI، فيُبنى التطبيق تحت Xvfb
ويُنادى `_on_content_sync` مباشرة. الغرض التأكد من أن Selenium
الجديد مربوط صحيحاً: لا NameError، والزر يُعطَّل ثم يُفعَّل،
والنتيجة تعود عبر الطابور.

    xvfb-run -a python tools/check_gui.py
"""

import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# مجلد محتوى مؤقت حتى لا نلمس بيانات المستخدم.
TMP = Path(tempfile.mkdtemp(prefix="gui-content-"))
os.environ["XDG_CONFIG_HOME"] = str(TMP)

from src.context_aware_audio import app as appmod  # noqa: E402


def main() -> int:
    root_mod = appmod.tk
    if root_mod is None:
        print("SKIP: tkinter unavailable")
        return 0

    try:
        ui = appmod.DesktopApp(root_mod.Tk())
    except Exception as exc:
        print(f"FAIL: app construction raised {type(exc).__name__}: {exc}")
        return 1
    print("app constructed OK")

    # --- the widgets the new code adds must exist ---------------------
    for name in ("_sync_queue", "_sync_running", "_sync_windows", "_sync_button", "_sync_combo"):
        if not hasattr(ui, name):
            print(f"FAIL: missing attribute {name}")
            return 1
    print("sync attributes present:", ", ".join(
        ["_sync_queue", "_sync_windows", "_sync_button", "_sync_combo"]
    ))
    print("windows offered:", ui._sync_windows)

    # --- no server configured: must log, not raise --------------------
    ui.content_sync_url = ""
    ui._on_content_sync()
    print("no-server path: OK (logged, no exception)")

    # --- with a server: the button must disable, then re-enable --------
    ui.content_sync_url = "http://127.0.0.1:8787"
    ui._settings["content_sync_token"] = "dev-token"
    ui._sync_window.set("duha_wisdom")

    states = []
    ui._on_content_sync()
    # ttk.Button.instate is not a method; the widget states are read via
    # `state()` which returns a tuple of names.
    states.append(tuple(ui._sync_button.state()))

    # Wait for the worker thread to finish, draining like the tick does.
    deadline = time.time() + 90
    while time.time() < deadline and ui._sync_running:
        ui._drain_sync_queue()
        time.sleep(0.3)
    ui._drain_sync_queue()

    states.append(tuple(ui._sync_button.state()))

    print("button disabled during work:", states[0])
    print("button re-enabled after   :", states[1])
    print("status text:", ui.content_sync_text.get())
    print("sync_running:", ui._sync_running)

    files = list(ui.content_library_path.rglob("*.mp3"))
    print(f"files landed in {ui.content_library_path}: {len(files)}")

    if "disabled" not in states[0] or "disabled" in states[1]:
        print("FAIL: button state did not follow the work")
        return 1
    if not files:
        print("FAIL: nothing was downloaded")
        return 1

    print("\nGUI SYNC CHECKS PASSED")
    return 0
    # NOTE: the app object is intentionally not destroyed; the process exits.


if __name__ == "__main__":
    raise SystemExit(main())
