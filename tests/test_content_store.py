"""content_store.py وحده: المخطط والاستعلامات."""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.context_aware_audio.content_store import (
    ContentStore,
    OUTCOME_COMPLETED,
    OUTCOME_INTERRUPTED,
)

PASSED = FAILED = 0


def check(name, cond, extra=""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"PASS {name}")
    else:
        FAILED += 1
        print(f"FAIL {name}  {extra}")


tmp = Path(tempfile.mkdtemp())
db = tmp / "content.db"


def add(store, cid, seq=None, kind="story", window="maqil_story", dur=120.0):
    store.upsert(
        content_id=cid,
        title=f"title {cid}",
        file_path=f"{cid}.mp3",
        kind=kind,
        window_key=window,
        sequence_index=seq,
        duration_sec=dur,
        min_room_silence_sec=5.0,
        ducking_ratio=0.2,
    )


with ContentStore(db) as s:
    # --- المخطط ---
    check("store: file created", db.exists())
    check("store: count starts at zero", s.count() == 0, s.count())

    # --- الإدراج ---
    add(s, "story_a", seq=1)
    add(s, "story_b", seq=2)
    add(s, "story_c", seq=3)
    check("store: three rows", s.count() == 3, s.count())
    check("store: get returns the row", s.get("story_a")["sequence_index"] == 1)

    # --- الإدراج المكرر لا يُنتج صفاً ثانياً ---
    add(s, "story_a", seq=1)
    check("store: upsert is idempotent", s.count() == 3, s.count())

    # --- تحديث المدة ---
    add(s, "story_a", seq=1, dur=999.0)
    check(
        "store: duration updates on rescan",
        s.get("story_a")["duration_sec"] == 999.0,
        s.get("story_a")["duration_sec"],
    )

    # --- التقدّم التصاعدي: الأدنى غير المنتهي أولاً ---
    nxt = s.next_for_window("maqil_story")
    check("seq: picks the lowest sequence_index", nxt["id"] == "story_a", nxt["id"])

    s.log_start("story_a", "maqil_story", "2026-09-30T14:00:00")
    s.log_finish(1, OUTCOME_COMPLETED, 999.0, "2026-09-30T14:20:00")
    nxt = s.next_for_window("maqil_story")
    check("seq: advances past a completed story", nxt["id"] == "story_b", nxt["id"])

    # --- قصة لم تنتهِ بعد لا يجوز أن يتقدّم التسلسل ---
    s.log_start("story_b", "maqil_story", "2026-09-30T15:00:00")
    nxt = s.next_for_window("maqil_story")
    check(
        "seq: an unfinished story stays a candidate", nxt["id"] == "story_b", nxt["id"]
    )
    s.log_finish(2, OUTCOME_INTERRUPTED, 30.0, "2026-09-30T15:01:00")
    nxt = s.next_for_window("maqil_story")
    check(
        "seq: an interrupted story stays a candidate too",
        nxt["id"] == "story_b",
        nxt["id"],
    )

    s.log_start("story_b", "maqil_story", "2026-09-30T16:00:00")
    s.log_finish(3, OUTCOME_COMPLETED, 999.0, "2026-09-30T16:20:00")
    nxt = s.next_for_window("maqil_story")
    check("seq: continues to the third", nxt["id"] == "story_c", nxt["id"])

    # --- بلا تسلسل: الأقل تشغيلاً أولاً ---
    add(s, "hadith_x", seq=None, kind="hadith", window="duha_wisdom")
    add(s, "hadith_y", seq=None, kind="hadith", window="duha_wisdom")
    add(s, "hadith_z", seq=None, kind="hadith", window="duha_wisdom")
    nxt = s.next_for_window("duha_wisdom")
    check(
        "unseq: first pick is arbitrary but stable", nxt["id"] == "hadith_x", nxt["id"]
    )
    s.log_start("hadith_x", "duha_wisdom", "2026-09-30T09:00:00")
    s.log_finish(4, OUTCOME_COMPLETED, 60.0, "2026-09-30T09:01:00")
    nxt = s.next_for_window("duha_wisdom")
    check(
        "unseq: a never-played hadith is preferred", nxt["id"] == "hadith_y", nxt["id"]
    )
    s.log_start("hadith_y", "duha_wisdom", "2026-09-30T10:00:00")
    s.log_finish(5, OUTCOME_COMPLETED, 60.0, "2026-09-30T10:01:00")
    s.log_start("hadith_z", "duha_wisdom", "2026-09-30T12:00:00")
    s.log_finish(6, OUTCOME_COMPLETED, 60.0, "2026-09-30T11:01:00")
    nxt = s.next_for_window("duha_wisdom")
    check(
        "unseq: rotates to the least recently played",
        nxt["id"] == "hadith_x",
        nxt["id"],
    )
    # تشغيل ثانٍ لـ hadith_x: للتجميع صفّان لا صفّ واحد
    s.log_start("hadith_x", "duha_wisdom", "2026-09-30T12:00:00")
    s.log_finish(7, OUTCOME_COMPLETED, 45.0, "2026-09-30T12:01:00")

    # --- نافذة لا محتوى فيها ---
    check(
        "empty: an unknown window returns None",
        s.next_for_window("no_such_window") is None,
    )

    # --- plays_today ---
    check(
        "today: counts rows for the day",
        s.plays_today("duha_wisdom", "2026-09-30") == 4,
        s.plays_today("duha_wisdom", "2026-09-30"),
    )
    check("today: another day is zero", s.plays_today("duha_wisdom", "2026-10-01") == 0)
    check("today: other window is zero", s.plays_today("samra", "2026-09-30") == 0)
    check(
        "today: last_started returns the max",
        s.last_started("duha_wisdom") == "2026-09-30T12:00:00",
        s.last_started("duha_wisdom"),
    )

    # --- التجميع: صفان لا صف واحد ---
    stats = dict((a, (p, sec)) for a, p, sec in s.stats())
    check(
        "stats: two log rows aggregate to 2 plays",
        stats["hadith_x"][0] == 2,
        stats["hadith_x"],
    )
    check(
        "stats: seconds sum across both rows",
        abs(stats["hadith_x"][1] - 105.0) < 1e-6,
        stats["hadith_x"],
    )
    check(
        "stats: the other two have one play each",
        stats["hadith_y"][0] == 1 and stats["hadith_z"][0] == 1,
        (stats["hadith_y"], stats["hadith_z"]),
    )

# --- البقاء على القرص ---
with ContentStore(db) as s2:
    check("store: data survives reopen", s2.count() == 6, s2.count())
    check(
        "store: logs survive reopen", s2.plays_today("duha_wisdom", "2026-09-30") == 4
    )
    check("store: sequence survives reopen", s2.get("story_a")["sequence_index"] == 1)
    check("store: duration survives reopen", s2.get("story_a")["duration_sec"] == 999.0)

# القوس الختاميّ



# ===== 9) قصّ السجل: متاح، وغير مربوط تلقائياً =====
# playback_log ينمو بلا حد. القصّ دالة في المخزن لا نداء عند الإقلاع:
# حذف تاريخ المستخدم قراره، ومن يعمل تسعين يوماً يظنّ السجل يبقى.
check("prune: the method exists", hasattr(ContentStore, "prune_older_than"))
check(
    "prune: the cutoff is injected, not read from the clock",
    "today" in ContentStore.prune_older_than.__code__.co_varnames,
    ContentStore.prune_older_than.__code__.co_varnames,
)

_pdb = Path(tmp) / "prune.db"
with ContentStore(_pdb) as pstore:
    for i, day in enumerate(
        ("2025-01-01", "2026-03-15", "2026-08-30", "2026-09-30")
    ):
        cid = f"clip{i}"
        pstore.upsert(
            cid, f"t{i}", f"{cid}.mp3", "story", "maqil_story", i,
            60.0, 5.0, 0.2,
        )
        lid = pstore.log_start(cid, "maqil_story", f"{day}T10:00:00")
        pstore.log_finish(lid, OUTCOME_COMPLETED, 60.0, f"{day}T10:01:00")

    check("prune: four rows before pruning", len(pstore.stats()) == 4,
          len(pstore.stats()))
    check("prune: zero days removes nothing",
          pstore.prune_older_than(0, today="2026-09-30") == 0)
    check("prune: a negative window removes nothing",
          pstore.prune_older_than(-5, today="2026-09-30") == 0)

    removed = pstore.prune_older_than(90, today="2026-09-30")
    check("prune: ninety days removes the two old rows", removed == 2, removed)
    left = {r[0] for r in pstore.stats()}
    check("prune: the recent rows survive", left == {"clip2", "clip3"},
          sorted(left))
    check("prune: the clip index is untouched", pstore.count() == 4, pstore.count())
    check("prune: today counts survive",
          pstore.plays_today("maqil_story", "2026-09-30") == 1,
          pstore.plays_today("maqil_story", "2026-09-30"))
    check("prune: a second call is a no-op",
          pstore.prune_older_than(90, today="2026-09-30") == 0)
    check("prune: the store is still usable after pruning",
          pstore.next_for_window("maqil_story") is not None)
print(f"\nRESULT: {PASSED} passed / {FAILED} failed")
sys.exit(1 if FAILED else 0)
