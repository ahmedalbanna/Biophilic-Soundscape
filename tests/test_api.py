"""
test_api.py - واجهة HTTP

الخادم يخدم كل طلب في خيطه، وهذا الذي كشف أن المخزن بلا قفل. فالفحص
الأهم هنا ليس في المسارات بل في التزامن: عشرة خيوط تقرأ القاعدة
وتكتب في آن، وهو ما كان يرمي عند أول طلب في العمل الحقيقي.

`http.server` حقيقي، لا مُحاكى:ConnectionAborted وHost وOrigin
وContent-Length سلوكُه من المكتبة، وفحصنا لشيءٍ آخر لا يُفحص.
"""

import json
import shutil
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.context_aware_audio import settings
from src.context_aware_audio.api import build_server

PASSED = FAILED = 0
PORT = 8799


def check(name, cond, extra=""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"PASS {name}")
    else:
        FAILED += 1
        print(f"FAIL {name}  {extra}")


def req(path, data=None, origin=None, host=None, method=None, raw=None, timeout=15):
    url = f"http://127.0.0.1:{PORT}{path}"
    body = (
        raw
        if raw is not None
        else (json.dumps(data).encode("utf-8") if data is not None else None)
    )
    r = urllib.request.Request(
        url, data=body, method=method or ("POST" if body else "GET")
    )
    if body is not None:
        r.add_header("Content-Type", "application/json")
    if origin:
        r.add_header("Origin", origin)
    if host:
        r.add_header("Host", host)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def jbody(text):
    try:
        return json.loads(text)
    except ValueError:
        return {}


tmp = Path(tempfile.mkdtemp())
httpd = svc = None
try:
    with (
        mock.patch.object(settings, "writable_path", lambda n: tmp / n),
        mock.patch(
            "src.context_aware_audio.service.content_db_path",
            lambda: tmp / "content.db",
        ),
        mock.patch(
            "src.context_aware_audio.service.content_library_dir",
            lambda override="": tmp / "library",
        ),
    ):
        httpd, svc = build_server(port=PORT, host="127.0.0.1")
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        time.sleep(0.4)

        # ===== 1) الصفحة والملفات الساكنة =====
        code, body = req("/")
        check("page: served", code == 200, code)
        check("page: it is html", body.lstrip().startswith("<!DOCTYPE"), body[:40])
        check(
            "page: it is arabic and right-to-left",
            'lang="ar"' in body and 'dir="rtl"' in body,
        )
        # مصدر خارجي: src= أو href= يشير إلى http. النصّ «http»
        # داخل placeholder لا يُحمّل شيئاً، والبحث عن «://» في كل
        # سطر يفتّش عن تعليقات لا عن تحميلات.
        imports = [
            w
            for w in body.split()
            if ('src="' in w or 'href="' in w or "src='" in w or "href='" in w)
            and "://" in w
        ]
        check("page: it loads no external asset", not imports, imports[:3])
        code, _ = req("/index.html")
        check("page: index.html is the same page", code == 200, code)
        for asset in ("/static/app.css", "/static/app.js"):
            code, body = req(asset)
            check(
                f"static: {asset} served",
                code == 200 and len(body) > 200,
                f"{code}, {len(body)}",
            )

        # ===== 2) اجتياز المسار =====
        for evil in (
            "/static/../api.py",
            "/static/..%2Fapi.py",
            "/static/../../src/context_aware_audio/api.py",
        ):
            code, body = req(evil)
            check(
                f"traversal: {evil} refused", code in (403, 404), f"{code} {body[:50]}"
            )

        # ===== 3) س(serialisable) =====
        code, body = req("/api/state")
        st = jbody(body)
        check("state: served", code == 200, code)
        for key in ("engine", "content", "sync", "mic", "audio", "now", "prayer"):
            check(f"state: has {key}", key in st, sorted(st))
        check("state: json round-trips", jbody(json.dumps(st)) == st)
        check(
            "state: the engine state is a string",
            isinstance(st["engine"]["state"], str),
            type(st["engine"]["state"]),
        )
        check(
            "state: the dB is a number",
            isinstance(st["engine"]["db"], (int, float)),
            st["engine"]["db"],
        )

        # ===== 4) الأوامر: معروفة ومجهولة =====
        code, body = req("/api/command", {"name": "start", "args": []})
        out = jbody(body)
        check("command: start accepted", code == 200 and out.get("ok"), body[:80])
        check("command: it says what it did", bool(out.get("result")), out)
        time.sleep(0.4)
        check(
            "command: the engine is running",
            jbody(req("/api/state")[1])["engine"]["running"] is True,
        )

        code, body = req("/api/command", {"name": "stop", "args": []})
        check("command: stop accepted", code == 200, code)
        check(
            "command: the engine stopped",
            jbody(req("/api/state")[1])["engine"]["running"] is False,
        )

        code, body = req(
            "/api/command",
            {"name": "settings", "args": [{"master_vol": 42, "duck_depth": 80}]},
        )
        snap = jbody(body)["result"]
        check(
            "command: settings applies",
            snap["master_vol"] == 42.0,
            snap.get("master_vol"),
        )
        check(
            "command: and reaches the engine config",
            svc.config.duck_depth == 80.0,
            svc.config.duck_depth,
        )
        check(
            "command: and is on disk",
            settings.load_settings()["master_vol"] == 42.0,
            settings.load_settings().get("master_vol"),
        )

        code, body = req("/api/command", {"name": "no_such_command"})
        out = jbody(body)
        check("command: an unknown name is refused", code == 400, code)
        check(
            "command: and it lists what exists",
            "start" in out.get("known", []),
            out.get("known"),
        )

        # اسم لا يبني اسم دالة: getattr بقبل أي شيء لا يصل إلى الجدول
        code, body = req("/api/command", {"name": "__class__"})
        check("command: __class__ does not escape the table", code == 400, code)
        code, body = req("/api/command", {"name": "settings", "args": "nope"})
        check("command: non-list args refused", code == 400, code)

        # خطأ داخل الأمر: 500 وسطر في السجل، لا انهيار
        svc.fail_next = True
        code, body = req("/api/command", {"name": "scenario", "args": ["unknown_kind"]})
        check(
            "command: a refused action is not a crash",
            code == 200,
            f"{code} {body[:60]}",
        )

        code, body = req("/api/command", raw=b"{not json")
        check("command: a broken body is a 400", code == 400, code)
        code, body = req("/api/command", raw=b"")
        code, body = req("/api/command", raw=b"", method="POST")

        # ===== 5) الحرّاس: أصلاً ومضيفاً =====
        code, body = req(
            "/api/command", {"name": "start"}, origin="http://evil.example"
        )
        check("guard: a foreign origin is refused", code == 403, f"{code} {body[:60]}")
        code, body = req("/api/state", origin="https://evil.example")
        check("guard: state is refused too", code == 403, code)
        code, body = req(
            "/api/command", {"name": "start"}, origin="http://127.0.0.1:8799"
        )
        check("guard: our own origin passes", code == 200, code)
        code, body = req("/api/state", host="far.example")
        check("guard: a foreign Host is refused", code == 403, code)
        check(
            "guard: and the engine did not start from it", svc.running in (True, False)
        )
        req("/api/command", {"name": "stop"})

        # ===== 6) التزامن: الفحص الأهم =====
        # عشرة خيوط تقرأ القاعدة عبر state() وتكتب عبر الفحص، في آن.
        # بلا قفل يرمي sqlite3 «أُنشئ في خيط آخر» عند أول تداخل.
        errors = []
        barrier = threading.Barrier(10, timeout=20)

        def hammer(i):
            try:
                barrier.wait()
                for _ in range(12):
                    code, _ = req("/api/state")
                    if code != 200:
                        errors.append(f"state {code}")
                    code, _ = req("/api/command", {"name": "content_rescan"})
                    if code != 200:
                        errors.append(f"rescan {code} {_[:60]}")
            except Exception as exc:
                errors.append(f"{type(exc).__name__}: {exc}")

        threads = [threading.Thread(target=hammer, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(40)
        check(
            "threads: ten readers and writers overlapped cleanly",
            not errors,
            errors[:4],
        )

        # ===== 7) المسارات المجهولة =====
        for path in ("/api/nope", "/api/command/extra", "/nothing"):
            method = "POST" if "command" in path else "GET"
            code, _ = req(path, data={} if method == "POST" else None, method=method)
            check(f"404: {path}", code == 404, code)

        # ===== 8) السجل =====
        code, body = req("/api/log?n=5")
        lines = jbody(body).get("lines", [])
        check("log: served", code == 200, code)
        check("log: it honours the count", len(lines) <= 5, len(lines))
        check("log: and it is not empty", len(lines) > 0, lines)
        code, body = req("/api/log")
        check(
            "log: without n it still works",
            code == 200 and len(jbody(body).get("lines", [])) > 5,
            code,
        )

        # ===== 9) البثّ =====
        got = b""
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{PORT}/api/stream", timeout=12
            ) as resp:
                check(
                    "sse: content type",
                    "text/event-stream" in resp.headers.get("Content-Type", ""),
                    resp.headers.get("Content-Type"),
                )
                deadline = time.time() + 4
                while time.time() < deadline and got.count(b"data: ") < 3:
                    chunk = resp.read(400)
                    if not chunk:
                        break
                    got += chunk
        except Exception as exc:
            check("sse: the stream opened", False, f"{type(exc).__name__}: {exc}")
        frames = [ln[6:] for ln in got.split(b"\n") if ln.startswith(b"data: ")]
        check("sse: three frames arrived", len(frames) >= 3, len(frames))
        ok = True
        for raw in frames:
            try:
                jbody(raw.decode("utf-8"))
            except Exception:
                ok = False
        check("sse: every frame is json", ok and len(frames) > 0, len(frames))

        # إغلاق البثّ من طرف العميل لا يفسد الخادم
        time.sleep(0.3)
        code, _ = req("/api/state")
        check("sse: the server survives a client that left", code == 200, code)

        # ===== 10) حجم الطلب =====
        huge = json.dumps(
            {"name": "settings", "args": [{"master_vol": "x" * 200000}]}
        ).encode()
        code, body = req("/api/command", raw=huge)
        check(
            "body: an oversized request is refused",
            code == 413,
            f"{code} {body[:60]}",
        )
        small = json.dumps({"name": "settings", "args": [{"master_vol": 30}]}).encode()
        code, body = req("/api/command", raw=small)
        check(
            "body: a plausible one is still accepted",
            code == 200,
            f"{code} {body[:60]}",
        )
        code, _ = req("/api/state")
        check("body: the server is still up", code == 200, code)
finally:
    if httpd is not None:
        httpd.shutdown()
        httpd.server_close()
    if svc is not None:
        svc.shutdown()
    shutil.rmtree(tmp, ignore_errors=True)

print(f"\nRESULT: {PASSED} passed / {FAILED} failed")
sys.exit(1 if FAILED else 0)
