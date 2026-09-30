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

# رمز التحقق من المحاولة الأولى: نسخة قديمة تالفة تعيد التنزيل
# بلا أن نترك المستخدم بلا محتوى.
MIN_SYNC_INTERVAL_SEC = 300.0


@dataclass
class SyncResult:
    """نتيجة محاولة مزامنة واحدة."""

    window: str = ""
    total: int = 0
    transferred: int = 0
    failed: int = 0
    verified: int = 0
    skipped: int = 0
    errors: List[str] = field(default_factory=list)
    cancelled: bool = False

    @property
    def ok(self) -> bool:
        return self.failed == 0 and not self.cancelled


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
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return []
    windows = data.get("windows") if isinstance(data, dict) else None
    return windows if isinstance(windows, list) else []


def _download(url: str, dest: Path, timeout: float) -> int:
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
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                while True:
                    chunk = resp.read(262_144)
                    if not chunk:
                        break
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

    manifest = _read_json_url(manifest_url(base_url, window, token), timeout)
    if manifest is None:
        result.errors.append("تعذّر جلب قائمة المحتوى")
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
    transferred = verified = 0
    new_state: Dict[str, dict] = {}

    for index, part in enumerate(parts, start=1):
        if should_cancel is not None and should_cancel():
            result.cancelled = True
            break
        if max_parts and transferred >= max_parts:
            break
        if not isinstance(part, dict):
            continue
        name = part.get("filename")
        want_hash = part.get("sha256")
        if not name or not want_hash:
            continue

        dest = library_dir / name
        prev = known.get(name)
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

        url = f"{base_url.rstrip('/')}/{urllib.parse.quote(window)}/" \
              f"{urllib.parse.quote(name)}"
        if token:
            url += "?" + urllib.parse.urlencode({"token": token})

        try:
            _download(url, dest, timeout)
        except (urllib.error.URLError, OSError) as exc:
            result.failed += 1
            if len(result.errors) < 5:
                result.errors.append(f"{name}: {exc}")
            continue

        # التحقق من ما وصل فعلاً، لا مما طُلب.
        if _sha256(dest) != want_hash:
            result.failed += 1
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
    return result


def _read_json_url(url: str, timeout: float) -> Optional[dict]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return None


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
