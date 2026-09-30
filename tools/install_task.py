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


def _run_string() -> str:
    """
    نص المهمة: انتقال إلى جذر المستودع ثم تشغيل القلب.

    `cmd /c cd /d "<root>" && ...` — الانتقال ضروري لأن الوحدة
    تُشغَّل بـ -m وتبحث عن src في مجلد العمل.

    أي `&` أو `^` في المسار يكسر سلسلة cmd، والبادئة `^^`
    تهرّبه. والمسار بأقواس أصلاً.
    """
    root = Path(__file__).resolve().parents[2]
    escaped = str(root).replace("^", "^^").replace("&", "^^&")
    return (
        f'cmd /c cd /d "{escaped}" && "'
        f'{sys.executable}" -m src.context_aware_audio.content_heartbeat'
    )


def _task_cmd(interval_minutes: int = 60) -> list:
    """
    أمر schtasks كامل.

    دالة واحدة يبنيها الفروع: كان `install` يعيد بناء الأمر مرتين
    متطابقتين لا فرق بينهما إلا مبادلتَا /SC و/MO.

    الحارس في الأعلى: صفر أو سالب كان ينتج /MO 0 في فرع الدقيقة،
    لأن max(1, …) لم يكن إلا في فرع الساعة. و`/MO 0` يرفضه
    schtasks، فيفشل التثبيت برسالة غامضة.
    """
    minutes = max(1, int(interval_minutes))
    if minutes < 60:
        schedule = ["/SC", "MINUTE", "/MO", str(minutes)]
    else:
        schedule = ["/SC", "HOURLY", "/MO", str(max(1, minutes // 60))]
    return [
        "schtasks", "/Create", "/TN", TASK_NAME,
        "/TR", _run_string(),
        *schedule,
        "/F",
    ]


def install(interval_minutes: int = 60) -> int:
    """ينشئ المهمة أو يحدّثها إن وُجدت. يعيد رمز العملية."""
    cmd = _task_cmd(interval_minutes)
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
