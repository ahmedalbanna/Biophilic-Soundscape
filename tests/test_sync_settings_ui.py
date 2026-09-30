"""فحص: حقول إعدادات الخادم في الواجهة، والحفظ، ورفض العنوان."""

import shutil
import sys
import tempfile
import tkinter as tk
from pathlib import Path
from unittest import mock

sys.path.insert(0, r"C:\Users\ahmed\work")

from src.context_aware_audio import settings

PASSED = FAILED = 0


def check(name, cond, extra=""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"PASS {name}")
    else:
        FAILED += 1
        print(f"FAIL {name}  {extra}")


tmp = Path(tempfile.mkdtemp())
root = tk.Tk()
root.geometry("1000x600+1400+400")
root.update()
try:
    from src.context_aware_audio.app import DesktopApp

    with mock.patch.object(settings, "writable_path", lambda n: Path(tmp) / n):
        app = DesktopApp(root, log_path=None)
        root.update()

        # 1) المتغيّرات موجودة وقابلة للتحرير
        for name in (
            "content_sync_url",
            "content_sync_token",
            "content_sync_parts",
            "content_sync_note",
        ):
            check(f"field: {name} exists", hasattr(app, name))
            check(
                f"field: {name} is an editable var",
                isinstance(getattr(app, name), tk.StringVar),
                type(getattr(app, name, None)).__name__,
            )

        # 2) الحقول في الشجرة فعلاً (لا متغيّر بلا عنصر)
        labels = set()

        def walk(widget):
            for child in widget.winfo_children():
                if isinstance(child, tk.Entry) and child.winfo_ismapped():
                    # ismapped لا قائمة العناصر: عنصر مبنيّ ومطويّ
                    # يبقى في القائمة ولا يراه المستخدم، ففحصُ القائمة
                    # يحتسب حقلاً مخفياً كأنه مرئي.
                    tv = child.cget("textvariable")
                    if tv:
                        labels.add(tv)
                walk(child)

        walk(root)
        root.update_idletasks()
        for var in (
            app.content_sync_url,
            app.content_sync_token,
            app.content_sync_parts,
        ):
            check(f"field: {var._name} is on screen", str(var) in labels, sorted(labels))

        # 3) كتابة عنوان وحفظه
        app.content_sync_url.set("http://185.190.140.93:8787")
        app.content_sync_token.set("my-token")
        app.content_sync_parts.set("7")
        snap = app._settings_snapshot()
        check(
            "save: the url is in the snapshot",
            snap["content_sync_url"] == "http://185.190.140.93:8787",
            snap.get("content_sync_url"),
        )
        check(
            "save: the token is in the snapshot",
            snap["content_sync_token"] == "my-token",
            snap.get("content_sync_token"),
        )
        check(
            "save: max_parts is in the snapshot",
            snap["content_sync_max_parts"] == 7,
            snap.get("content_sync_max_parts"),
        )

        ok = app._save_settings()
        check("save: it wrote to disk", ok is True)
        on_disk = settings.load_settings()
        check(
            "save: the url survives a reload",
            on_disk["content_sync_url"] == "http://185.190.140.93:8787",
            on_disk.get("content_sync_url"),
        )
        check(
            "save: the token survives a reload",
            on_disk["content_sync_token"] == "my-token",
            on_disk.get("content_sync_token"),
        )
        check(
            "save: max_parts survives a reload",
            on_disk["content_sync_max_parts"] == 7,
            on_disk.get("content_sync_max_parts"),
        )

        # 4) القارئ الفعلي لا لقطة قديمة
        check(
            "use: _sync_base reads the field",
            app._sync_base() == "http://185.190.140.93:8787",
            app._sync_base(),
        )
        check(
            "use: _sync_parts reads the field",
            app._sync_parts() == 7,
            app._sync_parts(),
        )

        # 5) رفض العنوان يشرح نفسه
        app.content_sync_url.set("185.190.140.93:8787")
        check("reject: a scheme-less url is refused", app._sync_base() == "")
        check(
            "reject: and the reason is shown",
            "http" in app.content_sync_note.get(),
            app.content_sync_note.get(),
        )
        app.content_sync_url.set("file:///C:/secret.json")
        check("reject: file:// is refused", app._sync_base() == "")
        app.content_sync_url.set("https://ok.example")
        check("accept: https passes", app._sync_base() == "https://ok.example")

        # 6) سقف الجلسات
        for raw, want in (("0", 1), ("-4", 1), ("9999", 500), ("junk", 20), ("12", 12)):
            app.content_sync_parts.set(raw)
            check(
                f"parts: {raw!r} -> {want}",
                app._sync_parts() == want,
                app._sync_parts(),
            )

        # 7) الرمز من البيئة يُعلَن
        import os

        os.environ["CONTENT_SYNC_TOKEN"] = "env-token"
        try:
            got = app._sync_token_now()
            check("token: the env var wins", got == "env-token", got)
            check(
                "token: and the UI says so",
                "CONTENT_SYNC_TOKEN" in app.content_sync_note.get(),
                app.content_sync_note.get(),
            )
        finally:
            del os.environ["CONTENT_SYNC_TOKEN"]
        app.content_sync_url.set("")
        check(
            "token: the typed token is used when there is no env var",
            app._sync_token_now() == "my-token",
            app._sync_token_now(),
        )

        app._on_close()
finally:
    try:
        root.destroy()
    except Exception:
        pass
    shutil.rmtree(tmp, ignore_errors=True)

print(f"\nRESULT: {PASSED} passed / {FAILED} failed")
sys.exit(1 if FAILED else 0)
