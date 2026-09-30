"""
content_store.py - مخزن SQLite لمسار المحتوى (الفهرس وسجل التشغيل)

وحدة مستقلة تماماً: لا تعرف شيئاً عن المحرك ولا عن الصوت. تُفتح
against ملف، وتُغلق، ولا تفترض وجود الصوت.

مبدأ التخزين: كل تشغيل صف واحد في playback_log. والمجموع وآخر
تشغيل استعلامان على الصفوف، لا عمودان يُحدَّثان مع كل سطر.

عمود تجميعي يخزن عدّاداً يتقادم: نبضة واحدة تكفي ليتعارض
نبضتان على الملف نفسه. الصفوف لا تتعارض.
"""

import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Tuple

SCHEMA = """
CREATE TABLE IF NOT EXISTS audio_content (
  id                   TEXT PRIMARY KEY,
  title                TEXT NOT NULL,
  file_path            TEXT NOT NULL,
  kind                 TEXT NOT NULL,
  window_key           TEXT NOT NULL,
  sequence_index       INTEGER,
  duration_sec         REAL NOT NULL,
  min_room_silence_sec REAL NOT NULL,
  ducking_ratio        REAL NOT NULL,
  tags                 TEXT NOT NULL DEFAULT '',
  enabled              INTEGER NOT NULL DEFAULT 1,
  added_at             TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS playback_log (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  audio_id     TEXT NOT NULL,
  window_key   TEXT NOT NULL,
  started_at   TEXT NOT NULL,
  ended_at     TEXT,
  outcome      TEXT NOT NULL,
  position_sec REAL NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_log_audio   ON playback_log(audio_id);
CREATE INDEX IF NOT EXISTS idx_log_started ON playback_log(started_at);
CREATE INDEX IF NOT EXISTS idx_log_window  ON playback_log(window_key, started_at);
"""

# لا فهرس على audio_content(window_key, sequence_index)، مع أن الاستعلام
# يفلتر بالنافذة ويرتّب بالتسلسل ويبدو نازلاً لفهرس. قِسْ ولا تخمّن:
#
#   صفوف    بلا فهرس    مع فهرس
#      200      327 us     379 us
#    2000     2.9 ms      4.0 ms
#   20000      34 ms     264 ms
#
# الفهرس ثمانية أضعاف أبطأ في الأخير. السبب أن الترتيب يحتاج شجرة
# مؤقتة على أي حال، فالمسح ثم الترتيب أرخص من اجتياز الفهرس ثم فحص
# `NOT EXISTS` لكل صف مرشّح فيه. ومكتبة بيت في مقطعين أو ثلاثة؛ ولو
# كبرت يوماً فالعلاج تحديد عدد المرشّحين، لا فهرس أعمى.

# نتائج ممكنة لسجل التشغيل. مكتوبة كنصوص لا كأعداد حتى يبقى السجل
# مقروءاً بقاعدة البيانات نفسها بلا رجوع إلى الشيفرة.
OUTCOME_COMPLETED = "completed"
OUTCOME_INTERRUPTED = "interrupted"
OUTCOME_SKIPPED = "skipped"
OUTCOME_ABANDONED = "abandoned"

CONTENT_KINDS = ("hadith", "story", "dhikr", "wisdom")


class ContentStore:
    """فهرس المحتوى وسجل تشغيله. واجهة واحدة على SQLite."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # القفل لازم الآن لا لاحقاً. كان الاستدعاء كله في خيط واحد
        # (واجهة Tk)، فبلا قفل كان الأمر مقبولاً. وواجهة HTTP تخدم كل
        # طلب في خيطه: المخزن يُقرأ في state() من خيط طلب ويُكتب في
        # الفحص من خيط آخر. بلا قفل يرمي sqlite3 «أُنشئ في خيط آخر»
        # عند أول طلب — وهو ما حدث.
        #
        # RLock لا Lock: يُستدعى المخزن من داخل update() وهو محميّ
        # أصلاً بقفل الخدمة، فقفلٌ عادي يجمّد على نفسه.
        self._lock = threading.RLock()
        self._closed = False
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        # WAL يسمح لقارئ أن يقرأ أثناء الكتابة. لا خيط ثانٍ في
        # التطبيق الواحد بعد استبدال Tk، لكنه لازم هنا: الطلبات
        # تعمل كلّها في خيوط.
        self._conn.execute("PRAGMA journal_mode=WAL")
        # مهلة الانتظار: نبضة المحرّك والمكتبة والطلب يقرؤون القاعدة
        # نفسها. مهلة بايثون الافتراضية خمس ثوانٍ قد لا تكفي في فحص
        # 312 ملفاً، فيرمي أحدهم «مقفلة» وينتظر الآخر. عشرون تكفي.
        self._conn.execute("PRAGMA busy_timeout=20000")
        self._conn.executescript(SCHEMA)
        self._conn.commit()


    def _query(self, sql: str, args: tuple = ()) -> List["sqlite3.Row"]:
        """
        ينفّذ ويسحب داخل القفل معاً، ويعيد كل الصفوف.

        السحب داخل القفل هو كل شيء هنا. القفل على التنفيذ وحده لا
        يكفي: على اتصال واحد يتسابق المؤشّران، فيُسحب صفُ خيطٍ بعد
        أن ينفّذ خيطٌ آخر - ويعود `fetchone()` بلا شيء. عزل الخيوط في
        sqlite3 يحمي الاتصال لا المؤشّر.

        كل الصفوف لا صفاً واحداً: القائمة أمتن من مؤشّرٍ يجب أن
        يبقى حياً بعد أن يُحرَّر القفل.
        """
        with self._lock:
            self._live()
            return self._conn.execute(sql, args).fetchall()

    def _write(self, sql: str, args: tuple = ()) -> int:
        """ينفّذ كتابةً ويثبّتها داخل القفل، ويعيد رقم الصفّ."""
        with self._lock:
            self._live()
            cur = self._conn.execute(sql, args)
            self._conn.commit()
            return int(cur.lastrowid or 0)

    def _delete(self, sql: str, args: tuple = ()) -> int:
        """يحذف ويثبّت داخل القفل، ويعيد عدد الصفوف المحذوفة."""
        with self._lock:
            self._live()
            cur = self._conn.execute(sql, args)
            self._conn.commit()
            return int(cur.rowcount or 0)

    def _live(self) -> None:
        """اتصال حيّ. يُستدعى داخل القفل فقط."""
        if self._conn is None or self._closed:
            raise sqlite3.ProgrammingError("المخزن مغلق")


    @property
    def is_closed(self) -> bool:
        with self._lock:
            return self._closed

    def close(self) -> None:
        with self._lock:
            if self._conn is not None and not self._closed:
                self._conn.close()
            self._conn = None
            self._closed = True

    def __enter__(self) -> "ContentStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---------- الفهرس ----------
    def upsert(
        self,
        content_id: str,
        title: str,
        file_path: str,
        kind: str,
        window_key: str,
        sequence_index: Optional[int],
        duration_sec: float,
        min_room_silence_sec: float,
        ducking_ratio: float,
        tags: str = "",
        added_at: Optional[str] = None,
    ) -> None:
        """
        يُدخل صفاً أو يحدّثه بالمعرّف نفسه.

        المعرّف مشتق من مسار الملف، فإعادة الفحص لا تُنتج تكراراً.
        duration_sec يُحدَّث فقط إن اختلف فعلاً: إعادة الكتابة بنفس
        القيمة تجعل «تغيّر المدة» أسبق من التحديث بلا داع.
        """
        stamp = added_at or datetime.now().isoformat(timespec="seconds")
        self._write(
            """
            INSERT INTO audio_content (
              id, title, file_path, kind, window_key, sequence_index,
              duration_sec, min_room_silence_sec, ducking_ratio, tags,
              enabled, added_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,1,?)
            ON CONFLICT(id) DO UPDATE SET
              title=excluded.title,
              file_path=excluded.file_path,
              kind=excluded.kind,
              window_key=excluded.window_key,
              sequence_index=excluded.sequence_index,
              duration_sec=excluded.duration_sec,
              min_room_silence_sec=excluded.min_room_silence_sec,
              ducking_ratio=excluded.ducking_ratio,
              tags=excluded.tags
            WHERE audio_content.duration_sec IS NOT excluded.duration_sec
               OR audio_content.title IS NOT excluded.title
               OR audio_content.file_path IS NOT excluded.file_path
               OR audio_content.kind IS NOT excluded.kind
               OR audio_content.window_key IS NOT excluded.window_key
               OR audio_content.sequence_index IS NOT excluded.sequence_index
               OR audio_content.min_room_silence_sec IS NOT excluded.min_room_silence_sec
               OR audio_content.ducking_ratio IS NOT excluded.ducking_ratio
               OR audio_content.tags IS NOT excluded.tags
            """,
            (
                content_id,
                title,
                file_path,
                kind,
                window_key,
                sequence_index,
                duration_sec,
                min_room_silence_sec,
                ducking_ratio,
                tags,
                stamp,
            ),
        )

    def all_content(self) -> List[sqlite3.Row]:
        return self._query(
            "SELECT * FROM audio_content WHERE enabled=1 ORDER BY id"
        )

    def count(self) -> int:
        rows = self._query("SELECT COUNT(*) FROM audio_content")
        return int(rows[0][0]) if rows else 0

    def get(self, content_id: str) -> Optional[sqlite3.Row]:
        rows = self._query(
            "SELECT * FROM audio_content WHERE id=?", (content_id,)
        )
        return rows[0] if rows else None

    # ---------- اختيار المقطع التالي ----------
    def next_for_window(self, window_key: str) -> Optional[sqlite3.Row]:
        (
            """
        يختار المقطع التالي لنافذة واحدة.

        مساران، بحسب وجود sequence_index:

        - مع تسلسل (قصص الأنبياء): أدنى sequence_index ليس له سجل
          بنتيجة completed. هذا هو التقدّم التصاعدي نفسه، فما
          انتهى أو تُخطّي منه لا يعود مرشّحاً. أما ما قطعته الصلاة
          (abandoned) فيبقى مرشّحاً: لم يُسمع، فليس تقدّماً.

        - بلا تسلسل (أحاديث): الأقدم تشغيلاً، فالأقل عدداً، فالمعرّف.
          الصلة الخارجية تحفظ من لم يُشغَّل أبداً، وترتيبه NULL يجعله
          أولاً في SQLite، فيُقدَّم جديد بلا سجل على قديم.
        """,
        )
        sequenced = self._query(
            """
            SELECT c.* FROM audio_content c
            WHERE c.enabled=1 AND c.window_key=?
              AND c.sequence_index IS NOT NULL
              AND NOT EXISTS (
                SELECT 1 FROM playback_log p
                WHERE p.audio_id=c.id AND p.outcome IN (?, ?)
              )
            ORDER BY c.sequence_index ASC, c.id ASC
            LIMIT 1
            """,
            (window_key, OUTCOME_COMPLETED, OUTCOME_SKIPPED),
        )
        if sequenced:
            return sequenced[0]
        rows = self._query(
            """
            SELECT c.* FROM audio_content c
            LEFT JOIN playback_log p ON p.audio_id = c.id
            WHERE c.enabled=1 AND c.window_key=?
              AND c.sequence_index IS NULL
            GROUP BY c.id
            ORDER BY MIN(p.started_at) ASC, COUNT(p.id) ASC, c.id ASC
            LIMIT 1
            """,
            (window_key,),
        )
        return rows[0] if rows else None

    # ---------- سجل التشغيل ----------
    def log_start(self, audio_id: str, window_key: str, started_at: str) -> int:
        return self._write(
            """
            INSERT INTO playback_log (audio_id, window_key, started_at, outcome)
            VALUES (?,?,?,?)
            """,
            (audio_id, window_key, started_at, OUTCOME_INTERRUPTED),
        )

    def log_finish(
        self, log_id: int, outcome: str, position_sec: float, ended_at: str
    ) -> None:
        self._write(
            """
            UPDATE playback_log
            SET outcome=?, position_sec=?, ended_at=?
            WHERE id=?
            """,
            (outcome, position_sec, ended_at, log_id),
        )

    def plays_today(
        self, window_key: str, day: str, exclude_outcome: Optional[str] = None
    ) -> int:
        """
        عدد ما شُغّل من نافذة اليوم.

        اليوم نص ISO (YYYY-MM-DD)، والمقارنة نصية عمداً: started_at
        مخزّن ISO فرزه الأبجدي هو الزمن.

        exclude_outcome يطرح صفاً بعينه. «مرة في اليوم» تستعمله
        لتتجاهل abandoned: مقطع قطعته الصلاة قبل أن يُسمع لم يُبثّ،
        وعدّاده كان يمنع النافذة بقية اليوم بلا سبب.
        """
        if exclude_outcome:
            rows = self._query(
                "SELECT COUNT(*) FROM playback_log WHERE window_key=? "
                "AND started_at LIKE ? AND outcome IS NOT ?",
                (window_key, f"{day}%", exclude_outcome),
            )
        else:
            rows = self._query(
                "SELECT COUNT(*) FROM playback_log WHERE window_key=? "
                "AND started_at LIKE ?",
                (window_key, f"{day}%"),
            )
        return int(rows[0][0]) if rows else 0

    def last_started(
        self, window_key: str, exclude_outcome: Optional[str] = None
    ) -> Optional[str]:
        """
        آخر بدء في النافذة، أو None إن لم يوجد.

        exclude_outcome يترك صفاً بعينه خارج الحساب. الفجوة بين المقاطع
        تستعمله لتتجاهل outcome=abandoned: مقطع قطعته الصلاة قبل أن
        يُسمع لا يُعدّ مقطعاً بُثّ، وإلا أمسكت النافذة ثلاثين دقيقة
        بعد أن انتهت الصلاة بلا سبب.
        """
        if exclude_outcome:
            rows = self._query(
                "SELECT MAX(started_at) FROM playback_log "
                "WHERE window_key=? AND outcome IS NOT ?",
                (window_key, exclude_outcome),
            )
        else:
            rows = self._query(
                "SELECT MAX(started_at) FROM playback_log WHERE window_key=?",
                (window_key,),
            )
        if not rows or rows[0][0] is None:
            return None
        return rows[0][0]

    def stats(self) -> List[Tuple[str, int, float]]:
        """
        تجميع لكل مقطع: عدد التشغيلات ومجموع المواضع.

        تجميع على Rows، لا عمود مخزّن. تغيّر المخطط لا يفقد تاريخاً.
        """
        return [
            (row["audio_id"], int(row["plays"]), float(row["seconds"]))
            for row in self._query(
                "SELECT audio_id, COUNT(*) AS plays, SUM(position_sec) AS seconds "
                "FROM playback_log GROUP BY audio_id ORDER BY audio_id"
            )
        ]

    # ---------- صيانة السجل ----------
    def prune_older_than(self, days: int, today: Optional[str] = None) -> int:
        """
        يحذف ما قبل تاريخ من سجل التشغيل، ويعيد عدد الصفوف المحذوفة.

        **غير مربوط تلقائياً.** حذف تاريخ المستخدم قرارُه، لا قرارُ
        الشيفرة: من يعمل تسعين يوماً يظنّ أن السجل يبقى، فمن
        ينادي هذا عند الإقلاع يفقد ذلك بلا إذن. الاتصال متاح لمن
        يريد، والقرار منفصل.

        `today` يحقن لأجل الفحص: «قبل كذا» يُقاس بتاريخ الجهاز لو لم
        يُمرَّر، فلا يمكن تثبيت سلوك الفحص.
        """
        if days <= 0:
            return 0
        cutoff = (datetime.fromisoformat(today) - timedelta(days=days)).date()
        return self._delete(
            "DELETE FROM playback_log WHERE started_at < ?", (cutoff.isoformat(),)
        )
