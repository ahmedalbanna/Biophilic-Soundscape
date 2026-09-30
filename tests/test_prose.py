"""
فحص النثر: لا حروف صينية، ولا جملة إنجليزية داخل نصّ عربي.

المهارة تشترط هذا قبل كل التزام، والوعد بلا اختبار لا يُنجز.
هذا الملف يمسّ كل مصدر ووثيقة ويطالب بأن يكون نثره نظيفاً.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PASSED = FAILED = 0

ROOT = Path(__file__).resolve().parents[1]
SOURCES = sorted((ROOT / "src").rglob("*.py")) + sorted((ROOT / "tests").rglob("*.py"))
DOCS = [ROOT / n for n in ("README.md", "content-track.md", "thinking.md")]
# هذا الملف يحمل عيّنات متعمّدة، فاستثناؤه من الفحص ليس تهاوناً:
# العيّنات تُفحص في القسم الرابع، لا هنا.
SELF = Path(__file__).resolve()
PY_SOURCES = [f for f in SOURCES if f != SELF]
SCANNED = PY_SOURCES + [d for d in DOCS if d.exists()]

# الحروف الصينية واليابانية والكورية. حرف واحد منها في تعليق عربي
# يعني أن أبجدية أخرى تسرّبت إلى الجملة، وهي تُقرأ كخطأ ترميز على
# وحدة cp1252 لا كعيب في النص.
CJK = re.compile(r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]")

ARABIC = re.compile(r"[\u0600-\u06ff]")

# جملة إنجليزية: كلمتان لاتينيتان متجاورتان أو أكثر. الكلمة الواحدة
# ليست دليلاً - `self.engine` و False و Tkinter أسماء لا نثر، وثلاثمئة
# تنبيه كاذب تفسد قيمة البوابة. الجملة حجّة.
PHRASE = re.compile(r"\b[A-Za-z][A-Za-z'-]*\s+[A-Za-z][A-Za-z'-]*\b")

# ما ليس نثراً: بين graves، ونصوص، ومسارات، ومعرّفات snake_case،
# وثوابت بأحرف كبيرة، ونسق بين قوسين. فوق ذلك قائمة أسماء
# علم تبقى كما هي: Tkinter في عنوان الوحدة مقبولة، أما lazy import
# وسط جملة عربية فمسروق.
CODEISH = [
    r"`[^`]*`",
    r'"[^"]*"',
    r"'[^']*'",
    r"\b[\w.]+\.(py|md|wav|mp3|json|db|txt|exe|bat)\b",
    r"\b\w*_\w+\b",
    r"\b[A-Z]{2,}\b",
    r"https?://\S+",
    r"\b\d+(?:[.,]\d+)*\b",
    r"\(\s*\w[\w.]*\s*\)",
    r"\bself\.\w+",
    r"\b\w+(?:\.\w+){2,}\b",  # src.context_aware_audio.app
    r"\b(?:if|not|and|or|else|return|await|async|import|from)\b",
    r"\b\d+-bit\b",
    r"\b(?:mono|stereo|webrtcvad|PCM|RMS|dB|Hz|kHz|MB|GB|WAV|mp3|exe)\b",
    r"\b(?:def|class|except|as|with|lambda|assert|raise|yield|global|nonlocal|del|pass)\b",
]
_CODE = re.compile("|".join(CODEISH))

# أسماء علم وأسماء مكتبات: تبقى كما هي، فهي أسماء لا تُترجَم.
ALLOWED = re.compile(
    r"\b(Tkinter|PyInstaller|Windows|winsound|Aladhan|numpy|pygame|tkinter"
    r"|Startup|INTERLOCK|Interlock|None|True|False|git)\b"
)


def check(name, cond, extra=""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"PASS {name}")
    else:
        FAILED += 1
        print(f"FAIL {name}  {extra}")


# ===== 1) لا حروف صينية في أي مكان =====
cjk_hits = []
for f in SCANNED:
    for i, line in enumerate(f.read_text(encoding="utf-8").split("\n"), 1):
        found = CJK.findall(line)
        if found:
            cjk_hits.append(f"{f.relative_to(ROOT)}:{i}  {''.join(sorted(set(found)))}")
check(
    "prose: no CJK characters anywhere in the sources or the docs",
    not cjk_hits,
    cjk_hits[:5],
)

# ===== 2) لا محرف بديل (تلف ترميز) =====
repl = [
    str(f.relative_to(ROOT))
    for f in SCANNED
    if "\ufffd" in f.read_text(encoding="utf-8")
]
check("prose: no replacement characters", not repl, repl)

# ===== 3) لا جملة إنجليزية داخل تعليق فيه عربية =====
# نفحص الذيل بعد # لا السطر كله. السطر كله شيفرة: `except Exception
# as exc` و`for i in range(4)` يبدوان جملتين إنكليزيتين وكلاهما
# شيفرة. النثر هو ما بعد العلامة فقط.
prose_hits = []
for f in PY_SOURCES:
    for i, line in enumerate(f.read_text(encoding="utf-8").split("\n"), 1):
        if "#" not in line:
            continue
        tail = line.split("#", 1)[1]
        bare = _CODE.sub(" ", tail).strip()
        if not bare or not ARABIC.search(bare):
            continue
        bare = ALLOWED.sub(" ", bare)
        found = PHRASE.findall(bare)
        if found:
            prose_hits.append(f"{f.relative_to(ROOT)}:{i}  {found[:2]}")
check("prose: no English phrases in Arabic comments", not prose_hits, prose_hits[:6])

# ===== 4) الأنماط تلتقط ما نُحقن فيه =====
# بدون هذا لا نعرف أن النمط يعمل أصلاً، وقد يمرّ نظيفاً لأنه معطّل
# لا لأن النص نظيف.
missed = [
    why
    for sample, pattern, why in (
        ("# المفتاح想问: أي فترة", CJK, "صيني في تعليق"),
        ("# فيتوقف الكلام عن认可ه هنا", CJK, "صيني وسط عربية"),
        ("# متاح بـ lazy import عبر __getattr__", PHRASE, "lazy import"),
        ("يقرأ the background now", PHRASE, "جملة إنجليزية"),
    )
    if not pattern.search(sample)
]
check("prose: the patterns catch known samples", not missed, missed)

# صياغة عربية تحوي معرّفات وأسماء علم: يجب ألا يُبلَّغ عنها
for ok_sample in (
    "# نقرأ content_engine لتحديث المخزن و duck_max_ratio",
    "# app.py - تطبيق سطح المكتب (Tkinter)",
    "# نستخدم sys.executable دائماً بدل القيمة النصية",
):
    bare = _CODE.sub(" ", ok_sample).lstrip("#").strip()
    bare = ALLOWED.sub(" ", bare)
    check(
        f"prose: tolerated: {ok_sample[:38]}",
        not PHRASE.search(bare),
        PHRASE.findall(bare),
    )

print(f"\nRESULT: {PASSED} passed / {FAILED} failed")
sys.exit(1 if FAILED else 0)
