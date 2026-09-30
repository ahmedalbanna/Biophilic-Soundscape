"""
content_heartbeat.py - مزامنة دورية بلا واجهة، بعملية قصيرة واحدة

يقصد به مشغّل جدولة خارج التطبيق: كل ساعة ينادي `python -m
src.context_aware_audio.content_heartbeat`، فيسحب ما نقص ثم يخرج.
عملية قصيرة، بلا خيط دائم، وبلا Tk.

لماذا بلا WebSocket: المزامنة سحب لا بثّ. قناة مفتوحة على مدار
اليوم لشيء لا يقع فيه إلا عند وجود نقص - ثمن دائم مقابل فائدة
عند الطلب. نداء واحد كل ساعة أرخص وأبسط من قناة دائمة.

المزامنة لا تفتح قاعدة المحتوى. المكتبة تُفحص مرة واحدة فقط وعندها
فقط، وإلا لتكرر قياس 312 ملفاً كل ساعة بلا جديد.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.context_aware_audio.config import EngineConfig  # noqa: E402
from src.context_aware_audio.content_library import ContentLibrary  # noqa: E402
from src.context_aware_audio.content_store import ContentStore  # noqa: E402
from src.context_aware_audio.content_sync import SyncResult, list_windows, sync_window  # noqa: E402
from src.context_aware_audio.paths import content_library_dir  # noqa: E402
from src.context_aware_audio.settings import load_settings  # noqa: E402

# النوافذ بالترتيب الذي يفضّله المحرك اختيارها. الترتيب لا يهم
# للمزامنة (كل نافذة مستقلة) لكنه يثبّت السجل.
DEFAULT_WINDOWS = ["fajr_dhikr", "duha_wisdom", "maqil_story", "evening_ethic"]


def _echo(message: str) -> None:
    print(message, flush=True)


def run(
    windows: Optional[List[str]] = None,
    max_parts: int = 0,
    dry_run: bool = False,
) -> int:
    """
    مزامنة واحدة كاملة. يُرجع رمز خروج للمشغّل: 0 كل شيء تمام.

    `max_parts` صفر يعني بلا حدّ - وهو الوضع الصحيح لساعة واحدة
    في الخلفية، بخلاف الجلسة اليدوية داخل التطبيق حيث يحدّه
    المستخدم حتى لا ينتظر.
    """
    settings = load_settings()
    base = str(settings.get("content_sync_url", "")).strip()
    token = str(settings.get("content_sync_token", "")).strip()

    if not base:
        _echo("القلب: لا يوجد خادم (content_sync_url فارغ) - لا شيء يفعل")
        return 0

    targets = windows or DEFAULT_WINDOWS
    dest = content_library_dir(str(settings.get("content_library_dir", "")))
    _echo(f"القلب: {base}")
    _echo(f"القلب: {len(targets)} نافذة، كل{max_parts or 'بلا'} حد، "
          f"الوجهة {dest}")

    if dry_run:
        found = list_windows(base, token)
        _echo(f"القلب: على الخادم {len(found)} نافذة")
        for w in found:
            _echo(f"   {w.get('window')}: {w.get('parts')} جزءاً")
        return 0

    total_new = 0
    total_failed = 0
    any_new = False
    results: List[SyncResult] = []

    for window in targets:
        r = sync_window(dest, base, window, token, max_parts=max_parts)
        results.append(r)
        total_new += r.transferred
        total_failed += r.failed
        if r.transferred:
            any_new = True
        if r.cancelled:
            _echo(f"   {window}: أُلغيت")
        elif r.failed:
            _echo(f"   {window}: {r.transferred} جديد، {r.failed} فشل")
            for e in r.errors[:2]:
                _echo(f"      {e}")
        else:
            _echo(f"   {window}: {r.transferred} جديد، {r.skipped} موجود")

    # الفحص مرة واحدة عندها فقط، وعندها فقط: كل ساعة بلا جديد
    # تعني 312 قياساً بلا فائدة. والقياس يقرأ الملفات كل مرة.
    if any_new and not dry_run:
        try:
            store = ContentStore(dest.parent / "content.db")
            report = ContentLibrary(dest, store, EngineConfig()).scan()
            store.close()
            _echo(f"القلب: فُحص {report.total} مقطعاً "
                  f"({report.added} جديد، {report.updated} محدَّث)")
        except Exception as exc:
            _echo(f"القلب: تعذّر الفحص: {exc}")

    _echo(f"القلب: انتهى - {total_new} جديد، {total_failed} فشل")
    return 1 if total_failed else 0


def main() -> int:
    args = sys.argv[1:]
    dry = "--dry-run" in args
    windows: List[str] = []
    max_parts = 0
    if "--window" in args:
        i = args.index("--window")
        if i + 1 < len(args):
            windows = [args[i + 1]]
    if "--max-parts" in args:
        i = args.index("--max-parts")
        if i + 1 < len(args):
            try:
                max_parts = int(args[i + 1])
            except ValueError:
                pass
    return run(windows or None, max_parts=max_parts, dry_run=dry)


if __name__ == "__main__":
    raise SystemExit(main())
