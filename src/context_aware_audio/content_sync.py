"""
content_sync.py - سحب المحتوى التعليمي من الخادم البعيد

يبني على عقد التسمية القائم: المشغّل يكتشف الملفات بالأسماء، فلا حاجة
لأي تعديل في المحرك. هذه الوحدة تجلب ما ينقص فقط، تتحقق من كل ملف
 ببصمة SHA-256، ثم تترك الفحص للمكتبة كما هي.

الحد الأدنى: المكتبة القياسية فقط. `urllib.request` و`hashlib` و`json` -
بلا أي اعتمادية جديدة، تماماً كبقية المشروع.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

# ملف الحالة بجانب المحتوى: يربط كل اسم ببصمته آخر مرة نُزّل فيها.
STATE_FILENAME = ".content-sync-state.json"

# السقف المطلق لردّ الخادم. `part["bytes"]` يحدّ ما نتوقّعه، والخادم
# قد يرسل أكثر: بلا سقف يملأ قرص المستخدم إلى أن يمتلئ. القيمة
# أرفع من أي مقطع تعليمي معقول، وتبقى حدّاً ضدّ خادم معطوب.
MAX_BYTES_PER_FILE = 512 * 1024 * 1024
MAX_BYTES_MANIFEST = 8 * 1024 * 1024

# استثناءات الشبكة التي يجب ألا تُسقط بقية النافذة. IncompleteRead
# وHTTPException ليستا من URLError ولا من OSError، فاستثناء واحد ناقص
# كان يوقف السحب عند أول استجابة مقطوعة بدل أن يحسبها فشلاً واحداً.
NET_ERRORS = (
    urllib.error.URLError,
    OSError,
    http.client.HTTPException,
    ValueError,
)


@dataclass
class SyncResult:
    """نتيجة محاولة مزامنة واحدة."""

    window: str = ""
    total: int = 0
    transferred: int = 0
    failed: int = 0
    rejected: int = 0
    verified: int = 0
    skipped: int = 0
    errors: List[str] = field(default_factory=list)
    cancelled: bool = False

    @property
    def ok(self) -> bool:
        return self.failed == 0 and not self.cancelled


def safe_name(name: object) -> Optional[str]:
    """
    يعيد الاسم إن كان اسم ملفّاً آمناً، وإلا None.

    `filename` يأتي من خادم على HTTP صريح، فأي متناصق على الطريق
    يستطيع كتابته. القاعدة: اسمٌ مجرّد، بلا فواصل، بلا محرّك، وبلا
    نقطتين ولا نقطة. أي شيء آخر يُرفض قبل أن يُلمس القرص.

    كان `library_dir / name` بلا فحص: `../../x.bat` يكتب فوق قائمة
    بدء ويندوز، وقد جرّب ذلك فكتب ملفين فوق مجلد المكتبة وأبلغ
    عن نجاح النقل.
    """
    if not isinstance(name, str):
        return None
    name = name.strip()
    if not name or name in (".", ".."):
        return None
    # مجرّد: بلا فاصل ولا محرّك. Path(name).name == name يغطّي
    # الفواصل على ويندوز وبوسكس معاً.
    if Path(name).name != name:
        return None
    if "/" in name or "\\" in name or ":" in name:
        return None
    if name.startswith("~"):
        return None
    return name


def _within(child: Path, parent: Path) -> bool:
    """هل `child` داخل `parent` بعد حلّ الروابط؟ حزام ثانٍ لا يكلّف."""
    try:
        return child.resolve().is_relative_to(parent.resolve())
    except (OSError, ValueError):
        return False


def _read_json(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def manifest_url(base: str, window: str, token: str) -> str:
    """يبني رابط الـmanifest، والرمز في سلسلة الاستعلام."""
    root = base.rstrip("/")
    url = f"{root}/{window}-manifest.json"
    if token:
        url += "?" + urllib.parse.urlencode({"token": token})
    return url


def list_windows(base: str, token: str, timeout: float = 15.0) -> List[dict]:
    """
    يفهرس النوافذ المتاحة على الخادم.

    الفهرس اختياري: خادمٌ بلا `/` يبقى صالحاً للمزامنة، والمهم هو
    وجود الـmanifest. لذلك يعيد قائمة فارغة عند الفشل بدل أن يرمي.
    """
    root = base.rstrip("/")
    url = root + "/"
    if token:
        url += "?" + urllib.parse.urlencode({"token": token})
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            raw = resp.read(MAX_BYTES_MANIFEST + 1)
            if len(raw) > MAX_BYTES_MANIFEST:
                return []
            data = json.loads(raw.decode("utf-8"))
    except NET_ERRORS:
        return []
    windows = data.get("windows") if isinstance(data, dict) else None
    return windows if isinstance(windows, list) else []


def list_windows_why(base: str, token: str, timeout: float = 15.0) -> tuple[List[dict], str]:
    """
    الفهرس مع سبب الفشل.

    فهرس `/` اختياري: خادمٌ بلا `/` يبقى صالحاً للمزامنة. لكن الفهرس
    *و* الـmanifest معطّلان معناه «لا خادم»، والفرق بينهما يظهر
    لللمستخدم عند الفشل لا في السجل.
    """
    root = base.rstrip("/")
    url = root + "/"
    if token:
        url += "?" + urllib.parse.urlencode({"token": token})
    global timeout_hint
    timeout_hint = f"{timeout:.0f}ث"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            raw = resp.read(MAX_BYTES_MANIFEST + 1)
            if len(raw) > MAX_BYTES_MANIFEST:
                return [], "فهرس الخادم أكبر من السقف"
            data = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        return [], _why(exc)
    windows = data.get("windows") if isinstance(data, dict) else None
    if not isinstance(windows, list):
        return [], "فهرس الخادم لا يحمل قائمة نوافذ"
    return windows, ""


class _TooLarge(Exception):
    """تجاوز الردّ الحجم المتوقّع."""


def _download(
    url: str, dest: Path, timeout: float, max_bytes: int = 0
) -> int:
    """
    ينزّل ملفاً إلى مساره النهائي عبر ملف مؤقت ثم `os.replace`.

    التنزيل إلى ملف مؤقت مقصود: قطع الاتصال في المنتصف يترك ملفاً
    ناقصاً باسمه النهائي، فيُحسب موجوداً. الاستبدال ذرّي، فلا يُرى
    إلا ملف مكتمل أو لا شيء.
    """
    tmp_name = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=dest.parent,
            prefix=dest.name + ".",
            suffix=".part",
            delete=False,
        ) as tmp:
            tmp_name = tmp.name
            ceiling = max_bytes or MAX_BYTES_PER_FILE
            if max_bytes:
                ceiling = min(ceiling, MAX_BYTES_PER_FILE)
            written = 0
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                while True:
                    chunk = resp.read(262_144)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > ceiling:
                        # نرمي قبل الكتابة الزائدة: الملف المؤقت
                        # يُحذف في التحت، فلا يبقى ناقصٌ باسمه.
                        raise _TooLarge(
                            f"{dest.name}: {written} بايت بعد سقف {ceiling}"
                        )
                    tmp.write(chunk)
        os.replace(tmp_name, dest)
        return dest.stat().st_size
    except Exception:
        if tmp_name is not None:
            try:
                Path(tmp_name).unlink(missing_ok=True)
            except OSError:
                pass
        raise


def sync_window(
    library_dir: Path,
    base_url: str,
    window: str,
    token: str = "",
    timeout: float = 60.0,
    max_parts: int = 0,
    should_cancel: Optional[Callable[[], bool]] = None,
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> SyncResult:
    """
    يجلب نافذة واحدة: الفارغ والمنتشر فقط، مع تحقق من البصمة.

    `max_parts` يحدّ المتابعة في جلسة واحدة - التحميل hundreds من
    الملفات دفعة واحدة يبقي قرصاً مشغولاً، والأفضل أن يعود المستخدم
    ويكمل. صفر يعني بلا حدّ.
    """
    result = SyncResult(window=window)
    library_dir = Path(library_dir)
    state_path = library_dir / STATE_FILENAME

    manifest, why = _read_json_url(
        manifest_url(base_url, window, token), timeout
    )
    if manifest is None:
        result.errors.append(f"تعذّر جلب قائمة المحتوى: {why}")
        result.failed = 1
        return result

    parts = manifest.get("parts")
    if not isinstance(parts, list) or not parts:
        result.errors.append("القائمة فارغة")
        return result

    result.total = len(parts)
    state = _read_json(state_path) or {}
    known: Dict[str, dict] = state.get("files")
    if not isinstance(known, dict):
        known = {}

    library_dir.mkdir(parents=True, exist_ok=True)
    transferred = verified = rejected = 0
    new_state: Dict[str, dict] = {}

    for index, part in enumerate(parts, start=1):
        if should_cancel is not None and should_cancel():
            result.cancelled = True
            break
        if max_parts and transferred >= max_parts:
            break
        if not isinstance(part, dict):
            continue
        raw_name = part.get("filename")
        want_hash = part.get("sha256")
        name = safe_name(raw_name)
        if name is None:
            rejected += 1
            if len(result.errors) < 5 and raw_name is not None:
                result.errors.append(f"اسم ملف غير مقبول: {raw_name!r}")
            continue
        if not want_hash:
            continue

        dest = library_dir / name
        # حزام ثانٍ بعد مسمّى آمن: المسار المحسوب يجب أن يبقى داخل
        # المكتبة حتى لو تغيّرت قواعد التسمية غداً.
        if not _within(dest, library_dir):
            rejected += 1
            if len(result.errors) < 5:
                result.errors.append(f"المسار خارج المكتبة: {name}")
            continue
        prev = known.get(name)
        existed_before = dest.exists()
        up_to_date = False
        if prev and prev.get("sha256") == want_hash:
            # الحجم أولاً: أرخص من البصمة ويكشف القطع والتلف الشائع.
            try:
                up_to_date = dest.stat().st_size == part.get("bytes")
            except OSError:
                up_to_date = False
            if not up_to_date:
                up_to_date = _matches_hash(dest, want_hash)
        if up_to_date:
            result.skipped += 1
            new_state[name] = prev
            if on_progress is not None:
                on_progress(index, result.total)
            continue

        # safe="" يقفل ممرّ '/'، فلا يصير اسماً متشعّباً في الرابط.
        # الاسم مُتحقَّق منه آنفاً، فهذه طبقة ثالثة لا أولى.
        url = f"{base_url.rstrip('/')}/{urllib.parse.quote(window)}/" \
              f"{urllib.parse.quote(name, safe='')}"
        if token:
            url += "?" + urllib.parse.urlencode({"token": token})

        # السقف يأتي من الـmanifest نفسه، فخادمٌ يكذب في الحجم يحدّ
        # نفسه - لكن السقف المطلق أدناه لا يعتمد على أحد.
        expect = part.get("bytes")
        expect = expect if isinstance(expect, int) and expect > 0 else 0
        try:
            _download(url, dest, timeout, max_bytes=expect)
        except _TooLarge:
            result.failed += 1
            if len(result.errors) < 5:
                result.errors.append(f"{name}: تجاوز الحجم المتوقّع")
            continue
        except NET_ERRORS as exc:
            result.failed += 1
            if len(result.errors) < 5:
                result.errors.append(f"{name}: {exc}")
            continue

        # التحقق من ما وصل فعلاً، لا مما طُلب.
        if _sha256(dest) != want_hash:
            result.failed += 1
            # لا نحذف إلا ما أنشأناه في هذه المحاولة. الملف المحلي
            # الذي كان هنا قبل التنزيل نبقيه: البصمة غير المطابقة قد
            # تكون استجابة مقطوعة، والملف الجيد أبقى من ملف ناقص،
            # ولا القاعدة أن تحذف ما لم تمسّه.
            if not existed_before:
                try:
                    dest.unlink(missing_ok=True)
                except OSError:
                    pass
            if len(result.errors) < 5:
                result.errors.append(f"{name}: بصمة غير مطابقة")
            continue

        transferred += 1
        verified += 1
        new_state[name] = {"sha256": want_hash, "bytes": part.get("bytes")}
        if on_progress is not None:
            on_progress(index, result.total)

    # حالة النافذة alone تُدمج مع غيرها: ملف واحد لكل المحتوى.
    merged = dict(known)
    merged.update(new_state)
    _write_state(state_path, merged, window)

    result.transferred = transferred
    result.verified = verified
    result.rejected = rejected
    return result


timeout_hint = ""


def _why(exc: Exception) -> str:
    """
    سبب الجواب بلغة المستخدم، لا اسم الاستثناء.

    كان الفشل كلّه يصير لاشيء ثم «تعذّر جلب قائمة المحتوى». وأسباب
    مختلفة: نقطة ميتة، أو رمز خاطئ، أو مهلة منتهية، أو نصّ تالف —
    ورسالة واحدة، ولا سبيل يعرف المستخدم أيّها. والشاشة هي كل ما
    لديه.
    """
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code in (401, 403):
            return f"الخادم رفض الرمز ({exc.code}) - تحقّق من الرمز"
        if exc.code == 404:
            return "لا توجد قائمة بهذه النافذة على الخادم (404)"
        return f"الخادم أجاب {exc.code}"
    if isinstance(exc, urllib.error.URLError):
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, TimeoutError) or "timed out" in str(reason):
            return f"انتهت المهلة ({timeout_hint}) - الخادم لا يستجيب"
        return f"تعذّر الوصول إلى الخادم: {reason}"
    if isinstance(exc, TimeoutError):
        return "انتهت المهلة - الخادم لا يستجيب"
    if isinstance(exc, ValueError):
        return "ردّ الخادم ليس JSON صالحاً"
    if isinstance(exc, OSError):
        return f"خطأ في الشبكة أو القرص: {exc}"
    return f"{type(exc).__name__}: {exc}"


def _read_json_url(url: str, timeout: float) -> tuple[Optional[dict], str]:
    """
    يجلب JSON ويعيد (البيانات أو لا شيء، سبب الفشل أو نص فارغ).

    سببين بدل واحد: الفحص يريد أن يعرف أهي شبكةٌ أم نصٌّ تالف،
    والمستخدم يريد أن يرى أيّهما.
    """
    global timeout_hint
    timeout_hint = f"{timeout:.0f}ث"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            raw = resp.read(MAX_BYTES_MANIFEST + 1)
            if len(raw) > MAX_BYTES_MANIFEST:
                return None, "قائمة المحتوى أكبر من السقف المسموح"
            return json.loads(raw.decode("utf-8")), ""
    except Exception as exc:
        return None, _why(exc)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _matches_hash(path: Path, want: str) -> bool:
    try:
        return _sha256(path) == want
    except OSError:
        return False


def _write_state(path: Path, files: Dict[str, dict], window: str) -> None:
    """يكتب الحالة ذرّياً - نفس قاعدة `atomic_write` في paths.py."""
    payload = {
        "files": files,
        "last_window": window,
        "updated_at": _now(),
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    tmp_name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=path.name + ".",
            suffix=".tmp",
            delete=False,
        ) as tmp:
            tmp.write(text)
            tmp_name = tmp.name
        os.replace(tmp_name, path)
    except OSError:
        if tmp_name is not None:
            try:
                Path(tmp_name).unlink(missing_ok=True)
            except OSError:
                pass
        raise


def _now() -> str:
    from datetime import datetime

    return datetime.now().isoformat(timespec="seconds")
