"""
web.py - نقطة تشغيل خادم الواجهة

`python -m src.context_aware_audio.web` يفتح المحرك في المتصفح.
البديل عن `desktop_app.py` بعد إزالة Tk.
"""

from __future__ import annotations

import argparse
import sys


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m src.context_aware_audio.web",
        description="محرك الصوت التكيفي — واجهة ويب محلية",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="العنوان. لا تغيّره إلا إن كنت تعرف ما تفعل: "
        "الخادم يفتح المجلدات ويفتح الميكروفون.",
    )
    parser.add_argument("--port", type=int, default=8788)
    parser.add_argument(
        "--no-browser", action="store_true", help="لا تفتح المتصفح تلقائياً"
    )
    args = parser.parse_args(argv)

    from .api import serve
    from .log_setup import close as close_logging
    from .log_setup import install as install_logging

    log_path = install_logging()
    print(f"السجل: {log_path}")
    try:
        serve(host=args.host, port=args.port, open_browser=not args.no_browser)
    except OSError as exc:
        print(f"تعذّر الاستماع على {args.host}:{args.port} — {exc}")
        print("هل المنفذ مشغول؟ جرّب --port 8789")
        return 2
    finally:
        close_logging()
    return 0


if __name__ == "__main__":
    sys.exit(main())
