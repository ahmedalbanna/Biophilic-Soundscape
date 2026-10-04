"""
api.py - واجهة HTTP محلية للخدمة

`http.server` من المكتبة القياسية، بلا اعتمادية جديدة. المشروع
وعدٍ بألا يحتاج pip، وواجهة ويب هي الموضع الغالب لخرق ذلك الوعد،
فبقيتُ على ما في المكتبة.

الأمان: الاستماع على 127.0.0.1 وحده. التطبيق يفتح المجلدات
ويشغّل الصوت ويفتح الميكروفون، فعلى 0.0.0.0 يصبح أي جهاز على
الشبكة قادراً على ذلك. ورفض `Origin` الغريب يمنع صفحةً على الإنترنت
من أن ترسل POST إلى localhost باسم المستخدم - وهو هجوم CSRF حقيقي
على خادم محلي.
"""

from __future__ import annotations

import json
import mimetypes
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple
from urllib.parse import unquote, urlparse

from .service import AudioService

WEB_DIR = Path(__file__).resolve().parents[2] / "web"
MAX_BODY_BYTES = 64 * 1024
DEFAULT_PORT = 8788
DEFAULT_HOST = "127.0.0.1"
SSE_INTERVAL_SEC = 0.2

# المصادر المسموحة: نحن وحدنا. الغائب (طلب سطر أوامر) مقبول،
# من مصدر آخر مرفوض.
SELF_ORIGINS = {
    f"http://{DEFAULT_HOST}:{DEFAULT_PORT}",
    f"http://localhost:{DEFAULT_PORT}",
    "http://127.0.0.1",
    "http://localhost",
}


def _commands(service: AudioService) -> Dict[str, Callable[[list], Any]]:
    """
    جدول الأوامر: اسم في JSON ← دالة في الخدمة.

    صريحٌ لا getattr: اسمٌ من الطلب لا يبني اسم دالة. `eval` هنا
    بابٌ مفتوح على كل صنف بايثون.
    """
    return {
        "start": lambda a: service.start_engine(),
        "stop": lambda a: service.stop_engine(),
        "settings": lambda a: service.update_settings(a[0] if a else {}),
        "scenario": lambda a: service.scenario(a[0] if a else ""),
        "content_play": lambda a: service.content_play_now(),
        "content_skip": lambda a: service.content_skip(),
        "content_rescan": lambda a: service.content_rescan(),
        "content_open": lambda a: service.content_open_folder(),
        "sync": lambda a: service.content_sync(a[0] if a else None),
        "sync_scan": lambda a: service.sync_scan(),
        "prayer_reload": lambda a: service.prayer_reload(),
        "mic_calibrate": lambda a: service.mic_calibrate(),
        "mic_devices": lambda a: service.mic_devices(),
        "mic_device": lambda a: service.update_settings({"mic_device": a[0]}),
        "manual_db": lambda a: service.set_manual_db(a[0]),
        "sim_enable": lambda a: service.sim_enable(bool(a[0])),
        "sim_time": lambda a: service.sim_set_time(a[0], a[1]),
        "sim_nudge": lambda a: service.sim_nudge(float(a[0])),
        "sim_reset": lambda a: service.sim_reset(),
    }


class Handler(BaseHTTPRequestHandler):
    service: AudioService = None  # type: ignore[assignment]
    server_version = "ContextAudio/2"
    protocol_version = "HTTP/1.1"

    # ---------- أدوات ----------
    def log_message(self, fmt, *args):
        """يكتم سجلّ الوصول: كل نبضة SSE كانت تملأ السجل."""

    def _json(self, payload: Any, status: int = 200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _text(self, body: str, status: int = 200):
        raw = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _origin_ok(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True  # طلب سطر أوامر أو curl
        return origin in SELF_ORIGINS or origin.startswith(f"http://{DEFAULT_HOST}:")

    def _host_local(self) -> bool:
        """الـHost يجب أن يكون محلياً: خادم على 0.0.0.0 يُبلَغ باسمه."""
        host = (self.headers.get("Host") or "").split(":")[0]
        return host in ("127.0.0.1", "localhost", "::1", "")

    def _guard(self) -> bool:
        if self._host_local() and self._origin_ok():
            return True
        self._json({"error": "طلب من خارج المحلي مرفوض"}, 403)
        return False

    # ---------- GET ----------
    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            return self._serve_file("index.html")
        if path.startswith("/static/"):
            return self._serve_file(path[len("/static/") :])
        if path == "/api/state":
            if not self._guard():
                return
            return self._json(self.service.state())
        if path == "/api/log":
            if not self._guard():
                return
            n = (
                int(urlparse(self.path).query.split("n=")[-1] or 80)
                if "n=" in self.path
                else 80
            )
            return self._json({"lines": self.service.log_tail(n)})
        if path == "/api/stream":
            if not self._guard():
                return
            return self._stream()
        return self._json({"error": "لا يوجد"}, 404)

    def _serve_file(self, rel: str):
        """
        يخدم ملفاً من web/، ولا يخدم ما خارجه.

        فكّ الترميز قبل الفحص لا بعده: `%2e%2e` هي `..` في معنى
        المسار، وبلا فكّها كانت الحراسة ميّتة - الحراسة الحقيقية
        لمسارٍ نظيف لا يجتاز شيئاً. وفكّها في المرور ضروري أصلاً:
        اسمٌ فيه مسافة أو حرف غير ascii لا يُخدَم بلا فكّ.

        الترتيب هو الحماية: تُفكّ النقاط، ثم يُقارن المسار المحلول
        بمجلد web. لو قُورن قبل الحلّ لأمكن الخروج بحرفٍ واحد.
        """
        target = (WEB_DIR / unquote(rel)).resolve()
        try:
            target.relative_to(WEB_DIR.resolve())
        except ValueError:
            return self._json({"error": "خارج المجلد"}, 403)
        if not target.is_file():
            return self._json({"error": "لا يوجد الملف"}, 404)
        body = target.read_bytes()
        ctype = mimetypes.guess_type(str(target))[0] or "text/plain"
        self.send_response(200)
        self.send_header("Content-Type", f"{ctype}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _stream(self):
        """بثّ الحالة عبر SSE: لقطة كل 200ms."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        try:
            while True:
                payload = json.dumps(self.service.state(), ensure_ascii=False)
                self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                self.wfile.flush()
                time.sleep(SSE_INTERVAL_SEC)
        except (BrokenPipeError, ConnectionResetError, ValueError, OSError):
            return  # إغلاق طبيعي للمتصفح

    # ---------- POST ----------
    def do_POST(self):
        if not self._guard():
            return
        path = urlparse(self.path).path
        if path != "/api/command":
            return self._json({"error": "لا يوجد المسار"}, 404)
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return self._json({"error": "Content-Length غير صالح"}, 400)
        # سقف قبل القراءة لا بعدها: الخادم الذي يقرأ gigabyte ثم يرفضه
        # ليس خادماً؛ والرفض أبكر وأرخص. والصفحة ترسل إعدادات
        # كيلوبايت، فالسقف لا يزعجها.
        if length > MAX_BODY_BYTES:
            return self._json({"error": "الطلب أكبر من السقف"}, 413)
        try:
            raw = self.rfile.read(length) if length else b"{}"
            body = json.loads(raw.decode("utf-8") or "{}")
        except (ValueError, OSError) as exc:
            return self._json({"error": f"طلب غير صالح: {exc}"}, 400)
        if not isinstance(body, dict):
            return self._json({"error": "الجذر يجب أن يكون كائناً"}, 400)

        name = body.get("name")
        args = body.get("args") or []
        if not isinstance(args, list):
            return self._json({"error": "args يجب أن تكون قائمة"}, 400)
        table = _commands(self.service)
        if name not in table:
            return self._json(
                {"error": f"أمر مجهول: {name}", "known": sorted(table)}, 400
            )
        try:
            result = table[name](args)
        except Exception as exc:
            self.service.log(f"أمر «{name}» فشل: {exc}")
            return self._json({"error": str(exc), "command": name}, 500)
        return self._json({"ok": True, "command": name, "result": result})


class AudioServer(ThreadingHTTPServer):
    """
    خادم بطابور انتظار واسع.

    الطابور الافتراضي في http.server خمسة اتصالات. الصفحة تفتح بثّاً
    وتسأل عن السجل كل أربع ثوانٍ وتُرسل أمراً عند الضغط — فخمس
    اتصالات في اللحظة الواحدة ليس افتراضاً بل حالة عادية. وما
    فوق الخمسة يُهمَل على ويندوز، فيرى الطلب «انقطع الاتصال قبل
    جواب». أُنجز ذلك في فحص عشرة خيوط متزامنة، لا بالتخمين.
    """

    daemon_threads = True
    request_queue_size = 128


def build_server(
    service: Optional[AudioService] = None,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
) -> Tuple[AudioServer, AudioService]:
    """خادم جاهز. يُرجع (الخادم، الخدمة) ليملكها المتصل."""
    service = service or AudioService()
    handler = type("BoundHandler", (Handler,), {"service": service})
    httpd = AudioServer((host, port), handler)
    return httpd, service


def serve(
    service: Optional[AudioService] = None,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    open_browser: bool = True,
) -> None:
    """يشغّل الخدمة والخادم إلى الأبد."""
    httpd, service = build_server(service, host, port)
    service.start_background()
    url = f"http://{host}:{port}/"
    service.log(f"الواجهة: {url}")
    print(f"الواجهة على {url} — Ctrl+C للإيقاف")
    if open_browser:
        try:
            import webbrowser

            webbrowser.open(url)
        except Exception:
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.shutdown()
        service.stop_background()
        service.shutdown()
