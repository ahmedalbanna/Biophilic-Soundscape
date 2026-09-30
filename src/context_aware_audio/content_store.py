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
from datetime import datetime
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
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        # WAL Journal دوام أوسع للقراءة مع الكتابة، وهو ما تحتاجه
        # قراءة الواجهة أثناء تسجيل المشغّل.
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

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
        self._conn.execute(
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
        self._conn.commit()

    def all_content(self) -> List[sqlite3.Row]:
        return list(
            self._conn.execute(
                "SELECT * FROM audio_content WHERE enabled=1 ORDER BY id"
            )
        )

    def count(self) -> int:
        return int(
            self._conn.execute("SELECT COUNT(*) FROM audio_content").fetchone()[0]
        )

    def get(self, content_id: str) -> Optional[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM audio_content WHERE id=?", (content_id,)
        ).fetchone()

    # ---------- اختيار المقطع التالي ----------
    def next_for_window(self, window_key: str) -> Optional[sqlite3.Row]:
        """
        يختار المقطع التالي لنافذة واحدة.

        مساران، بحسب وجود sequence_index:

        - مع تسلسل (قصص الأنبياء): أدنى sequence_index ليس له سجل
          بنتيجة completed. هذا هو التقدّم التصاعدي نفسه، فما
          انتهى منه لا يعود مرشّحاً أبداً إلا بإعادة الفحص.

        - بلا تسلسل (أحاديث): الأقدم تشغيلاً، فالأقل عدداً، فالمعرّف.
          الصلة الخارجية تحفظ من لم يُشغَّل أبداً، وترتيبه NULL يجعله
          أولاً في SQLite، فيُقدَّم جديد بلا سجل على قديم.
        """,
        sequenced = self._conn.execute(
            """
            SELECT c.* FROM audio_content c
            WHERE c.enabled=1 AND c.window_key=?
              AND c.sequence_index IS NOT NULL
              AND NOT EXISTS (
                SELECT 1 FROM playback_log p
                WHERE p.audio_id=c.id AND p.outcome=?
              )
            ORDER BY c.sequence_index ASC, c.id ASC
            LIMIT 1
            """,
            (window_key, OUTCOME_COMPLETED),
        ).fetchone()
        if sequenced is not None:
            return sequenced
        return self._conn.execute(
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
        ).fetchone()

    # ---------- سجل التشغيل ----------
    def log_start(self, audio_id: str, window_key: str, started_at: str) -> int:
        cur = self._conn.execute(
            """
            INSERT INTO playback_log (audio_id, window_key, started_at, outcome)
            VALUES (?,?,?,?)
            """,
            (audio_id, window_key, started_at, OUTCOME_INTERRUPTED),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def log_finish(
        self, log_id: int, outcome: str, position_sec: float, ended_at: str
    ) -> None:
        self._conn.execute(
            """
            UPDATE playback_log
            SET outcome=?, position_sec=?, ended_at=?
            WHERE id=?
            """,
            (outcome, position_sec, ended_at, log_id),
        )
        self._conn.commit()

    def plays_today(self, window_key: str, day: str) -> int:
        """
        عدد ما شُغّل من نافذة اليوم.

        اليوم نص ISO (YYYY-MM-DD)، والمقارنة نصية عمداً: started_at
        مخزّن ISO فرزه الأبجدي هو الزمن.
        """
        return int(
            self._conn.execute(
                "SELECT COUNT(*) FROM playback_log WHERE window_key=? "
                "AND started_at LIKE ?",
                (window_key, f"{day}%"),
            ).fetchone()[0]
        )

    def last_started(self, window_key: str) -> Optional[str]:
        row = self._conn.execute(
            "SELECT MAX(started_at) FROM playback_log WHERE window_key=?",
            (window_key,),
        ).fetchone()
        return row[0] if row and row[0] else None

    def stats(self) -> List[Tuple[str, int, float]]:
        """
        تجميع لكل مقطع: عدد التشغيلات ومجموع المواضع.

        تجميع على Rows، لا عمود مخزّن. تغيّر المخطط لا يفقد تاريخاً.
        """
        return [
            (row["audio_id"], int(row["plays"]), float(row["seconds"]))
            for row in self._conn.execute(
                "SELECT audio_id, COUNT(*) AS plays, SUM(position_sec) AS seconds "
                "FROM playback_log GROUP BY audio_id ORDER BY audio_id"
            )
        ]
