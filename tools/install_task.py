"""
install_task.py - جدولة نبض المزامنة على ويندوز

ينشئ مهمة في Task Scheduler تعمل كل ساعة كعملية قصيرة: تشغيل
مزامنة واحدة ثم خروج. لا نوافذ، لا خيط دائم، ولا اعتماد على أن
التطبيق يعمل - فهذا شرط لو أراد الجهاز أن يسحب وهو مغلق.

ليست حاجة إلى حساب بصلاحيات: المهمة تعمل كمستخدم مُسجَّل ضمن
جدولة المستخدم.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

TASK_NAME = "ContextAudio Content Sync"
TASK_DESC = "سحب المحتوى التعليمي من الخادم - كل ساعة"


def _task_cmd() -> list:
    return [
        "schtasks", "/Create", "/TN", TASK_NAME,
        "/TR", f'"{sys.executable}" -m src.context_aware_audio.content_heartbeat',
        "/SC", "HOURLY", "/MO", "1",
        "/F",
    ]


def install(interval_minutes: int = 60) -> int:
    """ينشئ المهمة أو يحدّثها إن وُجدت. يعيد رمز العملية."""
    root = Path(__file__).resolve().parents[2]
    cmd = [
        "schtasks", "/Create", "/TN", TASK_NAME,
        "/TR", f'cmd /c cd /d "{root}" && "{sys.executable}" -m src.context_aware_audio.content_heartbeat',
        "/SC", "HOURLY",
        "/MO", str(max(1, interval_minutes // 60) if interval_minutes >= 60 else 1),
        "/F",
    ]
    if interval_minutes < 60:
        cmd = [
            "schtasks", "/Create", "/TN", TASK_NAME,
            "/TR", f'cmd /c cd /d "{root}" && "{sys.executable}" -m src.context_aware_audio.content_heartbeat',
            "/SC", "MINUTE", "/MO", str(interval_minutes),
            "/F",
        ]
    print("إنشاء المهمة:")
    print("  " + " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        print("تم.")
        print(f"\nللتحقق:  schtasks /Query /TN \"{TASK_NAME}\"")
        print(f"لتشغيلها الآن:  schtasks /Run /TN \"{TASK_NAME}\"")
        print(f"لإزالتها:  schtasks /Delete /TN \"{TASK_NAME}\" /F")
    else:
        print("فشل الإنشاء.")
        if result.stdout.strip():
            print("  out:", result.stdout.strip())
        if result.stderr.strip():
            print("  err:", result.stderr.strip())
    return result.returncode


def uninstall() -> int:
    result = subprocess.run(
        ["schtasks", "/Delete", "/TN", TASK_NAME, "/F"],
        capture_output=True, text=True,
    )
    print("تم الحذف." if result.returncode == 0 else "لم تكن موجودة أو فشلت.")
    return result.returncode


def main() -> int:
    action = sys.argv[1] if len(sys.argv) > 1 else "install"
    if action in ("uninstall", "remove"):
        return uninstall()
    minutes = 60
    if "--minutes" in sys.argv:
        i = sys.argv.index("--minutes")
        if i + 1 < len(sys.argv):
            try:
                minutes = int(sys.argv[i + 1])
            except ValueError:
                pass
    if action == "install":
        return install(minutes)
    print("usage: python install_task.py [install|uninstall] [--minutes N]")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
