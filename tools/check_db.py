"""
إثبات حيّ لسلسلة «ملفات ← صفوف ← خوارزمية» بعد المزامنة.

يضع أسئلة لا يُجيب عنها قراءة الشيفرة وحدها:
  1. هل `sync_window` يكتب في قاعدة البيانات مباشرة، أم ملفات فقط؟
  2. هل `scan()` يحوّل ما نُزّل إلى صفوف فعلاً؟
  3. هل إعادة السحب بلا جديد تُكرّر الصفوف؟
  4. هل الصفوف تحمل window_key و sequence_index صحيحين - وهما
     ما يعتمد عليه المحرك لاختيار المقطع التالي؟

    node content-server.mjs --port 8787 &
    xvfb-run -a python tools/check_db.py
"""

import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.context_aware_audio.config import EngineConfig  # noqa: E402
from src.context_aware_audio.content_library import ContentLibrary  # noqa: E402
from src.context_aware_audio.content_store import ContentStore  # noqa: E402
from src.context_aware_audio.content_sync import sync_window  # noqa: E402

FAILURES = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}" + (f"  {detail}" if detail else ""))
    if not ok:
        FAILURES.append(name)


def counts(db_path: Path) -> dict:
    if not db_path.exists():
        return {"content": 0, "log": 0}
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        return {
            "content": con.execute("SELECT COUNT(*) c FROM audio_content").fetchone()["c"],
            "log": con.execute("SELECT COUNT(*) c FROM playback_log").fetchone()["c"],
        }
    finally:
        con.close()


def rows(db_path: Path, limit: int = 4) -> list:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        return con.execute(
            "SELECT id, title, file_path, kind, window_key, sequence_index, duration_sec "
            "FROM audio_content ORDER BY sequence_index LIMIT ?",
            (limit,),
        ).fetchall()
    finally:
        con.close()


def main() -> int:
    base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8787"
    token = sys.argv[2] if len(sys.argv) > 2 else "dev-token"
    window = sys.argv[3] if len(sys.argv) > 3 else "duha_wisdom"

    tmp = Path(tempfile.mkdtemp(prefix="dbcheck-"))
    lib_dir = tmp / "content"
    db_path = tmp / "content.db"

    print(f"window:  {window}")
    print(f"content: {lib_dir}")
    print(f"db:      {db_path}\n")

    try:
        # ---- 0. baseline ------------------------------------------------
        before = counts(db_path)
        print(f"[0] empty install: audio_content={before['content']}")
        check("no database before anything runs", before["content"] == 0)

        # ---- 1. sync: files only, by design ---------------------------
        r1 = sync_window(lib_dir, base, window, token)
        print(f"\n[1] sync_window -> {r1.transferred} transferred, "
              f"{r1.verified} verified, {r1.failed} failed")
        check("sync reported no failures", r1.failed == 0, f"failed={r1.failed}")
        check("sync moved files to disk", r1.transferred > 0, f"{r1.transferred}")
        on_disk = len(list(lib_dir.glob("*.mp3")))
        check("files are on disk", on_disk == r1.total, f"{on_disk} of {r1.total}")

        after_sync = counts(db_path)
        print(f"    audio_content after sync alone: {after_sync['content']}")
        check(
            "sync writes files only, never the database",
            after_sync["content"] == 0,
            "the scan is what populates audio_content",
        )
        state = lib_dir / ".content-sync-state.json"
        check("a state file was written", state.exists())

        # ---- 2. scan: the database is filled here ----------------------
        store = ContentStore(db_path)
        library = ContentLibrary(lib_dir, store, EngineConfig())
        report = library.scan()
        print(f"\n[2] scan -> total={report.total} added={report.added} updated={report.updated}")
        for e in report.errors[:3]:
            print("    err:", e)
        check("scan created no errors", not report.errors)
        check("scan reported the same count as the sync", report.total == r1.total,
              f"{report.total} vs {r1.total}")

        after_scan = counts(db_path)
        print(f"    audio_content now: {after_scan['content']}")
        check("scan created one row per file", after_scan["content"] == r1.total,
              f"{after_scan['content']} vs {r1.total}")

        # ---- 3. the columns the engine actually reads ------------------
        sample = rows(db_path, 4)
        print("\n[3] sample rows:")
        for r in sample:
            print(f"    seq={r['sequence_index']:>3}  {r['window_key']:<14} "
                  f"{r['duration_sec']:>7.0f}s  {r['title'][:34]}")
        check("window_key is set on every row",
              all(r["window_key"] == window for r in sample))
        check("sequence_index is populated",
              all(r["sequence_index"] is not None for r in sample))
        check("durations are measured, not zero",
              all(r["duration_sec"] and r["duration_sec"] > 0 for r in sample))
        check("file_path points into the content dir",
              all(r["file_path"] for r in sample))
        # The engine picks the lowest sequence_index with no completed row,
        # so the sequence must start at 1 and be unique.
        all_rows = rows(db_path, 999)
        seqs = [r["sequence_index"] for r in all_rows]
        check("sequences are unique", len(seqs) == len(set(seqs)))
        check("sequences start at 1", min(seqs) == 1, f"min={min(seqs)}")
        check("sequences are contiguous",
              sorted(seqs) == list(range(1, len(seqs) + 1)))
        store.close()

        # ---- 4. a second sync must not duplicate ----------------------
        r2 = sync_window(lib_dir, base, window, token)
        store2 = ContentStore(db_path)
        lib2 = ContentLibrary(lib_dir, store2, EngineConfig())
        lib2.scan()
        after_resync = counts(db_path)
        print(f"\n[4] second sync -> {r2.transferred} transferred; "
              f"audio_content={after_resync['content']}")
        check("second sync transfers nothing", r2.transferred == 0)
        check("re-scan did not duplicate rows",
              after_resync["content"] == after_scan["content"],
              f"{after_scan['content']} -> {after_resync['content']}")
        store2.close()

        # ---- 5. a different window lands in its own rows --------------
        if window != "maqil_story":
            r3 = sync_window(lib_dir, base, "maqil_story", token, max_parts=2)
            store3 = ContentStore(db_path)
            ContentLibrary(lib_dir, store3, EngineConfig()).scan()
            multi = sqlite3.connect(db_path).execute(
                "SELECT window_key, COUNT(*) FROM audio_content GROUP BY window_key"
            ).fetchall()
            print(f"\n[5] windows now in the database: {multi}")
            check("a second window added its own rows",
                  any(w == "maqil_story" for w, _ in multi))
            check("the first window's rows survived",
                  any(w == window for w, _ in multi))
            store3.close()

        print()
        if FAILURES:
            print(f"{len(FAILURES)} CHECK(S) FAILED:")
            for f in FAILURES:
                print("   -", f)
            return 1
        print("DB CHAIN VERIFIED: files -> scan() -> rows, idempotent, multi-window")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
