"""
test_store_threads.py - القفل حتماً لا حظاً

الفحص الثقيل بعشرة خيوط لا يلتقط سباق التنفيذ والسحب على هذا
المعالج: نافذة قفل بايثون ضيّقة فيصادفها الفحص نادراً. وقد جُرّب
ذلك: مخزن بلا حارس البتّة يمرّ فحص التزامن في test_api.py. فالفحص
الذي لا يسقط بإبطال سببِه ليس فحصاً.

هنا الحارس مقيس لا مرصود: نُبدل الاتصال بوابل يسجّل إن كان القفل
ممسوكاً عند كل خطوة، والقفل بعلم يرفعه. فالسحب خارج القفل يفشل
حتماً بدل أن ينجح أحياناً.
"""

import ast
import inspect
import shutil
import sqlite3
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.context_aware_audio import content_store as cs

PASSED = FAILED = 0


def check(name, cond, extra=""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"PASS {name}")
    else:
        FAILED += 1
        print(f"FAIL {name}  {extra}")


class FlagLock:
    """قفل يرفع علمه ما دام ممسوكاً، ويطيع المنع."""

    def __init__(self):
        self.held = False

    def __enter__(self):
        self.held = True
        return self

    def __exit__(self, *exc):
        self.held = False
        return False

    def acquire(self):
        self.held = True

    def release(self):
        self.held = False


class OpenLock:
    """قفلٌ لا يحرس شيئاً: يماثل المخزن الذي فقد حارسه."""

    def __init__(self):
        self.held = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def acquire(self):
        pass

    def release(self):
        pass


class SpyCursor:
    """يسجّل إن كان القفل ممسوكاً عند السحب."""

    def __init__(self, lock, rows, lastrowid=1, rowcount=1):
        self.lock = lock
        self.rows = rows
        self.lastrowid = lastrowid
        self.rowcount = rowcount
        self.at_fetch = None

    def fetchall(self):
        self.at_fetch = self.lock.held
        return self.rows

    def fetchone(self):
        self.at_fetch = self.lock.held
        return self.rows[0] if self.rows else None

    def __iter__(self):
        return iter(self.rows)


class SpyConn:
    """اتصال يسجّل كل خطوة، ويكشف ما يجري خارج القفل."""

    def __init__(self, lock, rows=()):
        self.lock = lock
        self.rows = list(rows)
        self.last = None
        self.outside = []
        self.commits = 0
        self.statements = []

    def execute(self, sql, args=()):
        self.statements.append(sql)
        if not self.lock.held:
            self.outside.append("execute")
        self.last = SpyCursor(self.lock, self.rows)
        return self.last

    def commit(self):
        self.commits += 1
        if not self.lock.held:
            self.outside.append("commit")

    def close(self):
        pass

    def executescript(self, script):
        pass


def spy_store(rows=(), lock=None):
    """مخزن بشكله الحقيقي واتصال وابل."""
    lock = lock or FlagLock()
    conn = SpyConn(lock, rows)
    store = cs.ContentStore.__new__(cs.ContentStore)
    store.path = Path("spy.db")
    store._lock = lock
    store._closed = False
    store._conn = conn
    return store, conn, lock


# ===== 1) القراءة: السحب داخل القفل =====
store, conn, lock = spy_store([(1, "a")])
store._query("SELECT * FROM audio_content")
check("read: execute ran under the lock", "execute" not in conn.outside, conn.outside)
check(
    "read: the fetch ran under the lock too",
    conn.last.at_fetch is True,
    f"at_fetch={conn.last.at_fetch}",
)

# الوابل يشتغل: بقفلٍ لا يحرس، يُبلّغ عن التنفيذ والسحب معاً.
# لولا هذا لكان الفحص يمرّ على وابلٍ أعمى فلا يكشف شيئاً.
store, conn, lock = spy_store([(1, "a")], lock=OpenLock())
store._query("SELECT 1", ())
check(
    "spy: it reports an unguarded execute",
    "execute" in conn.outside,
    conn.outside,
)
check(
    "spy: and an unguarded fetch",
    conn.last.at_fetch is False,
    f"at_fetch={conn.last.at_fetch}",
)

# ===== 2) الكتابة: التنفيذ والتثبيت معاً =====
store, conn, lock = spy_store()
store._write("UPDATE playback_log SET outcome=? WHERE id=?", ("done", 1))
check("write: execute ran under the lock", "execute" not in conn.outside, conn.outside)
check("write: commit ran under the lock", "commit" not in conn.outside, conn.outside)
check("write: it returns the row id", store._write("x") == 1, "no id")
# فرق لا مجموع: استدعاءٌ سابق رفع العدّاد، والعدّاد المطلق يجعل
# الفحص ينجح أو يسقط على حساب استدعاءات أخرى.
before = conn.commits
store._write("y")
check(
    "write: and it commits on every call",
    conn.commits == before + 1,
    f"{before} -> {conn.commits}",
)

# ===== 3) الحذف =====
store, conn, lock = spy_store()
got = store._delete("DELETE FROM playback_log WHERE started_at < ?", ("x",))
check("delete: it returns the row count", got == 1, got)
check("delete: under the lock", "commit" not in conn.outside, conn.outside)
check("delete: and it committed", conn.commits == 1, conn.commits)

# ===== 4) لا تنفيذ مباشر خارج __init__ =====
# بالشجرة لا بالنص: تقسيم النص على اسم الدالة يلتقط مساعدات القفل
# نفسها، فيُبلّغ عن تنفيذٍ وهو مقصود ومقفل. الشجرة تعرف أي دالة.
#
# المستثنى ثلاثة: الحارس نفسه. وهي المواضع الوحيدة
# التي يجوز أن تلمس الاتصال، وهي ما يمسك القفل، فاستثناؤها من
# الفحص هو الفحص: لو مُسّ الاتصال من غيرها فسقط.
GUARDED = ("__init__", "_query", "_write", "_delete")
tree = inspect.getsource(cs.ContentStore)
raw = []
for node in ast.walk(ast.parse(tree)):
    if not isinstance(node, ast.FunctionDef) or node.name in GUARDED:
        continue
    for sub in ast.walk(node):
        if (
            isinstance(sub, ast.Attribute)
            and sub.attr in ("execute", "executemany", "executescript", "commit")
            and isinstance(sub.value, ast.Attribute)
            and sub.value.attr == "_conn"
        ):
            raw.append(f"{node.name}:{sub.lineno} {sub.attr}")
check("lock: nothing outside the three guarded helpers touches the conn", not raw, raw)
check(
    "lock: the three helpers exist",
    all(hasattr(cs.ContentStore, n) for n in ("_query", "_write", "_delete")),
)
check("lock: _exec is gone", not hasattr(cs.ContentStore, "_exec"))

# ===== 5) قفل حقيقي: التزامن لا يرمي =====
tmp = Path(tempfile.mkdtemp())
real = cs.ContentStore(tmp / "t.db")
real.upsert("a", "A", "a.mp3", "story", "w", 1, 60.0, 12.0, 0.5)
real.upsert("b", "B", "b.mp3", "story", "w", 2, 60.0, 12.0, 0.5)
errors = []
barrier = threading.Barrier(8, timeout=30)


def hammer():
    try:
        barrier.wait()
        for _ in range(60):
            real.count()
            real.all_content()
            real.get("a")
            real.next_for_window("w")
            real.plays_today("w", "2026-09-30")
            real.last_started("w")
            real.stats()
            real.log_start("a", "w", "2026-09-30T08:00:00")
    except Exception as exc:
        errors.append(f"{type(exc).__name__}: {exc}")


threads = [threading.Thread(target=hammer) for _ in range(8)]
for t in threads:
    t.start()
for t in threads:
    t.join(60)
check("threads: eight readers and writers stayed clean", not errors, errors[:3])
check("threads: and the data survived", real.count() == 2, real.count())

real.close()
check("close: it reports closed", real.is_closed)
try:
    real.count()
    check("close: use after close fails loudly", False, "no error raised")
except sqlite3.ProgrammingError:
    check("close: use after close fails loudly", True)

shutil.rmtree(tmp, ignore_errors=True)
print(f"\nRESULT: {PASSED} passed / {FAILED} failed")
sys.exit(1 if FAILED else 0)
