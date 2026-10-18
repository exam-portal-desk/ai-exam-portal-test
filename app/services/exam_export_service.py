"""
app/services/exam_export_service.py
Bulk exam export/import — a .zip holding one manifest.json (every selected exam, its questions and the image/subject
relationships needed to rebuild them) plus an assets/ folder with each distinct image once, named by its content hash.

FORMAT
    smartai-exams-export-v2  (written by this module)
        subjects  every Subject in the source (id + name) with `used` = referenced by an exported image
        assets    one entry per distinct source image: id, subject, filename, zip path, sha256, size, content type
        exams     exam fields + source_id + category/subcategory names + expected_question_count / expected_image_count
                  + questions[] (fields + source_id + position + image_asset_id / image_status)
        warnings  everything the export could not do perfectly (an image missing in the source, a stale counter ...)
    smartai-exams-export-v1  (still imported): no subjects/assets tables, images only as `image_asset` zip paths, so the
        Subject an image belonged to is unknown. See LEGACY_SUBJECT below for exactly what happens to those images.

WHAT A SUBJECT IS HERE
    A Subject (table `subjects`: subject_name, subject_folder_id = the same name) is the image folder. A question's
    image_path is "<SubjectName>/<file>" and the Image Mapping tool only accepts keys starting with "<SubjectName>/" for an
    existing Subject. Subjects have no parent/category/board, so the NAME is their only identity; categories/subcategories
    (a separate hierarchy used by exams) are matched the same way, a subcategory within its category.
    A question has exactly one image_path (no ordering, caption or alt-text columns exist), so "one image per question" is
    the whole relationship there is to preserve.

MATCHING (categories, subcategories, subjects)
    1. exactly one existing record with the identical name        -> reuse it
    2. else exactly one whose name is equal after Unicode NFC, trimming, collapsing whitespace and case folding -> reuse it
       (and report that it was matched loosely; its own spelling is used for storage keys)
    3. several candidates                                         -> AMBIGUOUS: that exam fails, nothing is guessed
    4. none                                                       -> create it (a Subject only if the importing admin may
       manage Subjects), inside the exam's transaction, so a failed exam leaves nothing behind.
    Nothing existing is renamed or overwritten to make an import succeed.

IMPORT SAFETY
    - The whole package is validated before anything is written (structure, versions, counts, archive paths, image types,
      sizes and hashes). A bad package changes nothing.
    - Each exam is one DB transaction under a Postgres advisory lock, so concurrent imports are serialized and cannot race on
      category/subject creation. A failure rolls the exam back, and the image objects THIS exam created are deleted again;
      objects that already existed (or that another exam of the package created and committed) are never touched.
    - Storage and Postgres cannot commit together; compensation is best effort and is reported (`cleanup`).
    - Re-importing the same package is detected by name + question content (on_duplicate="skip", the default) instead of
      silently creating renamed copies.
"""

import hashlib
import json
import os
import re
import tempfile
import time
import unicodedata
import zipfile
from typing import Callable, Dict, List, Optional, Tuple

from werkzeug.utils import secure_filename

import app.config as config
from app.db import fetch_all, fetch_one, transaction
from app.db.exams import _ALL_COLS as _EXAM_COLS
from app.db.questions import get_questions_by_exam_strict
from app.storage import get_storage
from app.storage.service import StorageError, classify_storage_error, is_not_found_error
from app.utils.datetime_service import now_utc_naive

EXPORT_FORMAT_V1 = "smartai-exams-export-v1"
EXPORT_FORMAT = "smartai-exams-export-v2"
SUPPORTED_FORMATS = (EXPORT_FORMAT_V1, EXPORT_FORMAT)

# Where images of a v1 package go: v1 does not say which Subject an image belonged to and that cannot be reconstructed, so
# they are restored into one clearly named Subject (created if missing) instead of an invented one. Every such import
# carries a warning, and the Image Mapping tool can browse that Subject.
LEGACY_SUBJECT = "Imported"

_EXAM_FIELDS = [
    "name", "date", "start_time", "duration", "total_questions", "status",
    "instructions", "positive_marks", "negative_marks", "max_attempts",
    "result_mode", "result_delay", "results_released", "passing_percentage",
    "scheduled_mode", "prep_window_minutes", "completion_buffer_minutes",
    "allow_manual_submission", "available_after_datetime",
]
_QUESTION_FIELDS = [
    "question_text", "option_a", "option_b", "option_c", "option_d",
    "correct_answer", "question_type", "positive_marks", "negative_marks",
    "tolerance", "metadata",
]
# Column defaults of `questions`, applied when an (older) package leaves a field out, instead of inserting NULL over them.
_QUESTION_DEFAULTS = {"question_type": "MCQ", "positive_marks": 1, "negative_marks": 0, "tolerance": 0}

_EXPORT_DIR = os.path.join(tempfile.gettempdir(), "smartai_exam_exports")
_EXPORT_TTL_SECONDS = 3600

MAX_EXAMS_PER_PACKAGE = 500
MAX_ARCHIVE_MEMBERS = 20000
MAX_MANIFEST_BYTES = 64 * 1024 * 1024
MAX_TOTAL_UNCOMPRESSED_BYTES = 2 * 1024 * 1024 * 1024
MAX_IMPORT_IMAGE_BYTES = max(getattr(config, "MAX_IMAGE_SIZE_BYTES", 0), 5 * 1024 * 1024)
_IMPORT_LOCK_KEY = 7261001          # pg_advisory_xact_lock key serializing imports
_INSERT_CHUNK = 100
_STORAGE_RETRIES = 3

ProgressFn = Optional[Callable[[str, str, int], None]]


class ExportError(ValueError):
    """The export could not be produced reliably. The message is shown to the admin."""


class ImportValidationError(ValueError):
    """The package is malformed/unsupported/inconsistent; nothing was written. `errors` lists every problem found."""

    def __init__(self, errors: List[str]):
        self.errors = list(errors)
        shown = "; ".join(self.errors[:8])
        more = f" (and {len(self.errors) - 8} more)" if len(self.errors) > 8 else ""
        super().__init__(f"This file can't be imported ({len(self.errors)} problem(s)): {shown}{more}")


class _Ambiguous(ValueError):
    pass


# ─────────────────────────────────────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────────────────────────────────────

def export_out_path(job_id: str) -> str:
    os.makedirs(_EXPORT_DIR, exist_ok=True)
    return os.path.join(_EXPORT_DIR, f"{job_id}.zip")


def import_in_path(job_id: str) -> str:
    os.makedirs(_EXPORT_DIR, exist_ok=True)
    return os.path.join(_EXPORT_DIR, f"import-{job_id}.zip")


def cleanup_expired_exports() -> None:
    """Called from the app's 5-minute cleanup loop: deletes export/import .zip files older than the TTL."""
    if not os.path.isdir(_EXPORT_DIR):
        return
    cutoff = time.time() - _EXPORT_TTL_SECONDS
    for fname in os.listdir(_EXPORT_DIR):
        path = os.path.join(_EXPORT_DIR, fname)
        try:
            if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
                os.remove(path)
        except OSError:
            pass


def _silent_remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def _norm(value) -> str:
    """Comparison form of a name: NFC, trimmed, inner whitespace collapsed, case folded."""
    return " ".join(unicodedata.normalize("NFC", str(value)).split()).casefold()


_IMAGE_KINDS = {            # kind -> (extensions, content type)
    "png": ((".png",), "image/png"),
    "jpeg": ((".jpg", ".jpeg"), "image/jpeg"),
    "gif": ((".gif",), "image/gif"),
    "webp": ((".webp",), "image/webp"),
    "bmp": ((".bmp",), "image/bmp"),
}


def detect_image_kind(head: bytes) -> Optional[str]:
    """The image type judged by the file's own first bytes, never by its name."""
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head[:2] == b"BM":
        return "bmp"
    return None


def _image_key(raw) -> Optional[str]:
    if raw is None:
        return None
    key = str(raw).strip()
    return None if key.lower() in ("", "nan", "none") else key


def _split_key(key: str) -> Tuple[Optional[str], str]:
    """'Subject/file.png' -> ('Subject', 'file.png'); a key without a folder has no Subject."""
    if "/" not in key:
        return None, key
    return key.split("/", 1)[0], key.rsplit("/", 1)[1]


def valid_subject_name(name) -> bool:
    """A name that is safe as both a Subject name and a storage-key folder."""
    if not isinstance(name, str):
        return False
    if name != name.strip() or not 1 <= len(name) <= 100:
        return False
    if "/" in name or "\\" in name or name.startswith(".") or name.endswith("."):
        return False
    return not any(ord(c) < 32 or ord(c) == 127 for c in name)


# ─────────────────────────────────────────────────────────────────────────────
# Export
# ─────────────────────────────────────────────────────────────────────────────

# Thin readers that RAISE on a database error (the app's usual helpers return []/None, which an export must never mistake
# for "there is nothing here"). Kept as separate functions so tests can inject failures.
def _db_exams(exam_ids: List[int]) -> List[Dict]:
    return fetch_all(f"SELECT {_EXAM_COLS} FROM exams WHERE id = ANY(%s)", (list(exam_ids),))


def _db_category(category_id: int) -> Optional[Dict]:
    return fetch_one("SELECT id, name FROM categories WHERE id=%s", (category_id,))


def _db_subcategory(subcategory_id: int) -> Optional[Dict]:
    return fetch_one("SELECT id, name, category_id FROM subcategories WHERE id=%s", (subcategory_id,))


def _db_subjects() -> List[Dict]:
    return fetch_all("SELECT id, subject_name FROM subjects ORDER BY subject_name")


def _with_retry(fn, key: str):
    """Runs a storage call; transient failures (throttling, network) are retried a couple of times, everything else is
    classified into a StorageError. A genuine 'not found' is re-raised untouched for the caller to treat as missing."""
    last = None
    for attempt in range(_STORAGE_RETRIES):
        try:
            return fn()
        except Exception as e:
            if is_not_found_error(e):
                raise
            last = classify_storage_error(e, key)
            if last.kind in ("throttled", "network") and attempt < _STORAGE_RETRIES - 1:
                time.sleep(0.5 * (attempt + 1))
                continue
            raise last from e
    raise last  # pragma: no cover


def _read_object(storage, key: str) -> Optional[bytes]:
    """The object's bytes, or None when it genuinely does not exist. Any other failure raises StorageError."""
    exists = getattr(storage, "exists_strict", storage.exists)
    try:
        if not _with_retry(lambda: exists(key), key):
            return None
        return _with_retry(lambda: storage.download(key), key)
    except StorageError:
        raise
    except Exception as e:
        if is_not_found_error(e):
            return None
        raise classify_storage_error(e, key) from e


def run_export(exam_ids: List[int], out_path: str, progress: ProgressFn = None) -> Dict:
    """Streams the export to out_path (one image in memory at a time). Raises ExportError rather than writing a package
    that silently lacks questions or images; what is genuinely absent in the source is recorded in the manifest warnings."""
    try:
        return _run_export(exam_ids, out_path, progress)
    except BaseException:
        _silent_remove(out_path)
        raise


def _run_export(exam_ids, out_path, progress) -> Dict:
    def _report(phase, message, percent):
        if progress:
            progress(phase, message, percent)

    _report("preparing", "Loading selected exams...", 0)
    ids = list(dict.fromkeys(int(i) for i in exam_ids))
    try:
        rows = _db_exams(ids)
    except Exception as e:
        raise ExportError(f"Could not read the selected exams from the database: {e}") from e
    by_id = {r["id"]: r for r in rows}
    absent = [i for i in ids if i not in by_id]
    if absent:
        raise ExportError(f"These selected exams no longer exist: {', '.join(map(str, absent))}. Refresh the page and try again.")
    ordered = [by_id[i] for i in ids]

    per_exam_questions: Dict[int, List[Dict]] = {}
    for exam in ordered:
        try:
            per_exam_questions[exam["id"]] = get_questions_by_exam_strict(exam["id"])
        except Exception as e:
            raise ExportError(f"Could not read the questions of exam '{exam.get('name', exam['id'])}': {e}") from e
    total_units = sum(len(qs) for qs in per_exam_questions.values()) or 1

    try:
        subject_rows = _db_subjects()
    except Exception as e:
        raise ExportError(f"Could not read the subjects from the database: {e}") from e
    subject_id_by_name = {s["subject_name"]: s["id"] for s in subject_rows}

    storage = get_storage()
    warnings: List[Dict] = []
    storage_failures: List[str] = []
    assets: List[Dict] = []
    asset_by_key: Dict[str, Optional[Dict]] = {}       # source key -> asset entry (None = unusable, reason already warned)
    written_members: Dict[str, str] = {}               # sha256 -> zip member
    used_subjects: set = set()
    manifest_exams: List[Dict] = []
    done = 0

    def warn(code, message, **ctx):
        warnings.append({"code": code, "message": message, **ctx})

    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        def resolve_asset(key: str, exam_name: str, position: int) -> Tuple[Optional[Dict], str]:
            """(asset entry, state) where state is ok | missing | failed."""
            if key in asset_by_key:
                entry = asset_by_key[key]
                return (entry, "ok") if entry else (None, "missing")
            try:
                content = _read_object(storage, key)
            except StorageError as e:
                storage_failures.append(f"{key}: {e.kind}")
                return None, "failed"
            if content is None:
                asset_by_key[key] = None
                warn("image_missing", f"Image '{key}' does not exist in the source storage.", exam=exam_name, position=position, source_key=key)
                return None, "missing"
            kind = detect_image_kind(content[:16])
            if kind is None or not content:
                asset_by_key[key] = None
                warn("image_unusable", f"'{key}' is not a recognizable image file and was not exported.", exam=exam_name, position=position, source_key=key)
                return None, "missing"
            if len(content) > MAX_IMPORT_IMAGE_BYTES:
                asset_by_key[key] = None
                warn("image_too_large", f"Image '{key}' is {len(content)} bytes, over the {MAX_IMPORT_IMAGE_BYTES}-byte import limit, and was not exported.",
                     exam=exam_name, position=position, source_key=key)
                return None, "missing"
            sha = hashlib.sha256(content).hexdigest()
            member = written_members.get(sha)
            if member is None:
                member = f"assets/{sha}{_IMAGE_KINDS[kind][0][0]}"
                zf.writestr(member, content)
                written_members[sha] = member
            subject, filename = _split_key(key)
            if subject:
                used_subjects.add(subject)
                if subject not in subject_id_by_name:
                    warn("subject_not_registered", f"Images are stored under the folder '{subject}' but no such Subject exists in the source.",
                         source_key=key)
            else:
                warn("image_without_subject", f"Image '{key}' is not inside a Subject folder; it will be restored into '{LEGACY_SUBJECT}'.",
                     exam=exam_name, position=position, source_key=key)
            entry = {"id": f"a{len(assets) + 1}", "source_key": key, "subject": subject, "filename": filename, "path": member,
                     "sha256": sha, "size": len(content), "content_type": _IMAGE_KINDS[kind][1]}
            assets.append(entry)
            asset_by_key[key] = entry
            return entry, "ok"

        for idx, exam in enumerate(ordered):
            questions = per_exam_questions[exam["id"]]
            try:
                category = _db_category(exam["category_id"]) if exam.get("category_id") else None
                subcategory = _db_subcategory(exam["subcategory_id"]) if exam.get("subcategory_id") else None
            except Exception as e:
                raise ExportError(f"Could not read the category of exam '{exam.get('name')}': {e}") from e
            if exam.get("category_id") and not category:
                raise ExportError(f"Exam '{exam.get('name')}' points at a category that does not exist (id {exam['category_id']}).")
            if exam.get("subcategory_id") and not subcategory:
                raise ExportError(f"Exam '{exam.get('name')}' points at a subcategory that does not exist (id {exam['subcategory_id']}).")

            exam_out = {f: exam.get(f) for f in _EXAM_FIELDS}
            exam_out["source_id"] = exam["id"]
            exam_out["category_name"] = category["name"] if category else None
            exam_out["subcategory_name"] = subcategory["name"] if subcategory else None
            exam_out["source_category_id"] = exam.get("category_id")
            exam_out["source_subcategory_id"] = exam.get("subcategory_id")
            exam_out["questions"] = []

            if not questions:
                warn("exam_has_no_questions", f"Exam '{exam.get('name')}' has no questions in the source.", exam=exam.get("name"))
            if exam.get("total_questions") not in (None, len(questions)):
                warn("question_counter_stale", f"Exam '{exam.get('name')}' says {exam.get('total_questions')} questions but has {len(questions)}; "
                     f"the {len(questions)} real questions were exported.", exam=exam.get("name"))

            total_q = len(questions) or 1
            image_count = missing_count = 0
            for qi, q in enumerate(questions):
                q_out = {f: q.get(f) for f in _QUESTION_FIELDS}
                q_out.update({"source_id": q.get("id"), "position": qi, "image_asset": None, "image_asset_id": None, "image_status": "none"})
                key = _image_key(q.get("image_path"))
                if key:
                    entry, state = resolve_asset(key, exam.get("name"), qi)
                    if state == "ok":
                        q_out.update(image_asset=entry["path"], image_asset_id=entry["id"], image_status="ok")
                        image_count += 1
                    else:
                        q_out["image_status"] = "missing"
                        missing_count += 1
                exam_out["questions"].append(q_out)
                done += 1
                _report("exporting", f"Exporting exam {idx + 1} of {len(ordered)}: {exam.get('name', '')} — {qi + 1}/{total_q} questions",
                        min(99, round(done / total_units * 99)))
            exam_out["expected_question_count"] = len(questions)
            exam_out["expected_image_count"] = image_count
            exam_out["missing_image_count"] = missing_count
            manifest_exams.append(exam_out)

        if storage_failures:
            shown = "; ".join(storage_failures[:5])
            more = f" (and {len(storage_failures) - 5} more)" if len(storage_failures) > 5 else ""
            raise ExportError(f"Could not read {len(storage_failures)} image(s) from storage, so no export was produced: {shown}{more}. "
                              "This is a storage problem (permissions, network or throttling), not a missing file; try again.")

        # the package must agree with itself before it is declared good
        asset_ids = {a["id"] for a in assets}
        for e in manifest_exams:
            if e["expected_question_count"] != len(e["questions"]):
                raise ExportError(f"Internal consistency check failed for exam '{e['name']}' (question count).")
            refs = [q["image_asset_id"] for q in e["questions"] if q["image_status"] == "ok"]
            if len(refs) != e["expected_image_count"] or any(r not in asset_ids for r in refs):
                raise ExportError(f"Internal consistency check failed for exam '{e['name']}' (image references).")

        manifest = {
            "format": EXPORT_FORMAT,
            "exported_at": now_utc_naive().isoformat(),
            "subjects": [{"source_id": s["id"], "name": s["subject_name"], "used": s["subject_name"] in used_subjects} for s in subject_rows],
            "assets": assets,
            "exams": manifest_exams,
            "warnings": warnings,
            "totals": {"exams": len(manifest_exams), "questions": sum(len(e["questions"]) for e in manifest_exams),
                       "images": sum(e["expected_image_count"] for e in manifest_exams), "assets": len(assets),
                       "asset_files": len(written_members)},
        }
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, default=str))

    _report("complete", "Export complete", 100)
    return {"exam_count": len(ordered), "question_count": manifest["totals"]["questions"], "image_count": manifest["totals"]["images"],
            "asset_count": len(assets), "subject_count": len(used_subjects), "warning_count": len(warnings), "warnings": warnings[:50],
            "status": "warnings" if warnings else "complete"}


# ─────────────────────────────────────────────────────────────────────────────
# Import: validation (no writes happen before this has passed)
# ─────────────────────────────────────────────────────────────────────────────

_MEMBER_RE = re.compile(r"^assets/[A-Za-z0-9._-]{1,200}$")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_LEGACY_PREFIX_RE = re.compile(r"^[0-9a-f]{12}_")
_SCALAR = (str, int, float, bool, type(None))


def _unsafe_member_name(name: str) -> bool:
    if not name or "\x00" in name or "\\" in name or name.startswith("/") or re.match(r"^[A-Za-z]:", name):
        return True
    return any(part in ("..", ".") for part in name.rstrip("/").split("/"))


def _validate_archive(zf: zipfile.ZipFile) -> Tuple[Dict[str, zipfile.ZipInfo], List[str]]:
    errors: List[str] = []
    infos: Dict[str, zipfile.ZipInfo] = {}
    members = zf.infolist()
    if len(members) > MAX_ARCHIVE_MEMBERS:
        errors.append(f"The archive holds {len(members)} files (limit {MAX_ARCHIVE_MEMBERS}).")
        return infos, errors
    total = 0
    for info in members:
        name = info.filename
        if _unsafe_member_name(name):
            errors.append(f"Unsafe path inside the archive: {name!r}.")
            continue
        if info.is_dir():
            continue
        if (info.external_attr >> 16) & 0o170000 == 0o120000:
            errors.append(f"'{name}' is a symbolic link, which is not allowed.")
            continue
        if info.flag_bits & 0x1:
            errors.append(f"'{name}' is encrypted.")
            continue
        if name != "manifest.json" and not _MEMBER_RE.match(name):
            errors.append(f"Unexpected file in the archive: {name!r}.")
            continue
        if name in infos:
            errors.append(f"'{name}' appears more than once in the archive.")
            continue
        total += info.file_size
        if name == "manifest.json" and info.file_size > MAX_MANIFEST_BYTES:
            errors.append(f"manifest.json is larger than {MAX_MANIFEST_BYTES // (1024 * 1024)} MB.")
        elif name != "manifest.json":
            if info.file_size > MAX_IMPORT_IMAGE_BYTES:
                errors.append(f"'{name}' is {info.file_size} bytes, over the {MAX_IMPORT_IMAGE_BYTES}-byte image limit.")
            if info.compress_size and info.file_size > 1024 * 1024 and info.file_size / info.compress_size > 500:
                errors.append(f"'{name}' has a suspicious compression ratio.")
        infos[name] = info
    if total > MAX_TOTAL_UNCOMPRESSED_BYTES:
        errors.append("The archive would expand to more than 2 GB.")
    return infos, errors


def _read_member(zf: zipfile.ZipFile, info: zipfile.ZipInfo, limit: int) -> bytes:
    with zf.open(info) as fh:
        data = fh.read(limit + 1)
    if len(data) > limit:
        raise ImportValidationError([f"'{info.filename}' is larger than {limit} bytes."])
    return data


def _is_number(v) -> bool:
    if v is None:
        return True
    if isinstance(v, bool):
        return False
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False


def _parse_package(zf: zipfile.ZipFile) -> Dict:
    """Validates the whole package and returns the parsed form the importer works from:
    {"version", "legacy", "assets": {ref: asset}, "exams": [parsed exam...], "warnings": [...], "infos": {...}}.
    Raises ImportValidationError listing every problem; never writes anything."""
    infos, errors = _validate_archive(zf)
    if errors:
        raise ImportValidationError(errors)
    if "manifest.json" not in infos:
        raise ImportValidationError(["No manifest.json inside the file — this doesn't look like an exam export."])
    try:
        manifest = json.loads(_read_member(zf, infos["manifest.json"], MAX_MANIFEST_BYTES))
    except ImportValidationError:
        raise
    except (ValueError, UnicodeDecodeError):
        raise ImportValidationError(["manifest.json is corrupt and can't be read."])
    if not isinstance(manifest, dict):
        raise ImportValidationError(["manifest.json must contain a JSON object."])

    fmt = manifest.get("format")
    if fmt is None:
        fmt = EXPORT_FORMAT_V1                      # the original exporter always wrote it; tolerate a hand-edited package
    if fmt not in SUPPORTED_FORMATS:
        raise ImportValidationError([f"Unsupported export format '{fmt}'. This version can import: {', '.join(SUPPORTED_FORMATS)}."])
    legacy = fmt == EXPORT_FORMAT_V1
    warnings: List[Dict] = []
    if legacy:
        warnings.append({"code": "legacy_package", "message": "This is an older (v1) export: it does not say which Subject each image belonged to, "
                         f"so every image is restored into the Subject '{LEGACY_SUBJECT}'. Move them to the right Subject if needed."})
    for w in manifest.get("warnings") or []:
        if isinstance(w, dict) and isinstance(w.get("message"), str):
            warnings.append({"code": "export_warning", "message": f"Reported by the export: {w['message']}"})

    exams = manifest.get("exams")
    if not isinstance(exams, list) or not exams:
        raise ImportValidationError(["This file has no exams to import."])
    if len(exams) > MAX_EXAMS_PER_PACKAGE:
        raise ImportValidationError([f"Too many exams in one file ({len(exams)}; limit {MAX_EXAMS_PER_PACKAGE})."])

    # assets table (v2) ------------------------------------------------------
    assets: Dict[str, Dict] = {}
    if not legacy:
        raw_assets = manifest.get("assets")
        if not isinstance(raw_assets, list):
            errors.append("assets must be a list.")
            raw_assets = []
        for i, a in enumerate(raw_assets):
            where = f"assets[{i}]"
            if not isinstance(a, dict) or not isinstance(a.get("id"), str) or not a["id"]:
                errors.append(f"{where}: missing id.")
                continue
            if a["id"] in assets:
                errors.append(f"{where}: duplicate asset id '{a['id']}'.")
                continue
            path = a.get("path")
            if not isinstance(path, str) or not _MEMBER_RE.match(path) or path not in infos:
                errors.append(f"{where}: its file '{path}' is not in the archive.")
                continue
            sha = a.get("sha256")
            if not isinstance(sha, str) or not _SHA_RE.match(sha):
                errors.append(f"{where}: invalid sha256.")
                continue
            if a.get("size") != infos[path].file_size:
                errors.append(f"{where}: size {a.get('size')} does not match the archived file ({infos[path].file_size}).")
                continue
            subject = a.get("subject")
            if subject is not None and not valid_subject_name(subject):
                errors.append(f"{where}: unsafe subject name {subject!r}.")
                continue
            filename = a.get("filename")
            if not isinstance(filename, str) or not filename:
                errors.append(f"{where}: missing filename.")
                continue
            assets[a["id"]] = {"ref": a["id"], "path": path, "subject": subject, "filename": filename, "sha256": sha}
        for i, s in enumerate(manifest.get("subjects") or []):
            if not isinstance(s, dict) or not isinstance(s.get("name"), str):
                errors.append(f"subjects[{i}]: missing name.")

    parsed_exams = []
    for ei, e in enumerate(exams):
        where = f"exams[{ei}]"
        if not isinstance(e, dict):
            errors.append(f"{where}: not an object.")
            continue
        name = e.get("name")
        if not isinstance(name, str) or not name.strip():
            if legacy:
                warnings.append({"code": "exam_unnamed", "message": f"{where} has no name; it will be called 'Imported exam {ei + 1}'."})
                name = f"Imported exam {ei + 1}"
            else:
                errors.append(f"{where}: missing name.")
                continue
        name = name.strip()
        where = f"exam '{name}'"
        if len(name) > 255:
            errors.append(f"{where}: name is longer than 255 characters.")
        bad = [f for f in _EXAM_FIELDS if f in e and not isinstance(e[f], _SCALAR)]
        if bad:
            errors.append(f"{where}: unsupported value type in {', '.join(bad)}.")
        cat, sub = e.get("category_name"), e.get("subcategory_name")
        for label, val, limit in (("category_name", cat, 50), ("subcategory_name", sub, 100)):
            if val is not None and (not isinstance(val, str) or len(val.strip()) > limit):
                errors.append(f"{where}: invalid {label}.")
        ewarn: List[Dict] = []
        cat = cat.strip() if isinstance(cat, str) and cat.strip() else None
        sub = sub.strip() if isinstance(sub, str) and sub.strip() else None
        if sub and not cat:
            ewarn.append({"code": "subcategory_without_category", "message": f"Subcategory '{sub}' has no category in the package, so it was not recreated."})
            sub = None
        questions = e.get("questions")
        if not isinstance(questions, list):
            errors.append(f"{where}: questions must be a list.")
            continue
        if not legacy and e.get("expected_question_count") != len(questions):
            errors.append(f"{where}: expected {e.get('expected_question_count')} questions but the package contains {len(questions)}.")
        pq_list = []
        ok_images = 0
        for qi, q in enumerate(questions):
            qw = f"{where}, question {qi + 1}"
            if not isinstance(q, dict):
                errors.append(f"{qw}: not an object.")
                continue
            text = q.get("question_text")
            if not isinstance(text, str) or not text.strip():
                errors.append(f"{qw}: empty question text.")
                continue
            bad = [f for f in _QUESTION_FIELDS if f != "metadata" and f in q and not isinstance(q[f], _SCALAR)]
            if bad or (q.get("metadata") is not None and not isinstance(q.get("metadata"), dict)):
                errors.append(f"{qw}: unsupported value type in {', '.join(bad) or 'metadata'}.")
                continue
            if not all(_is_number(q.get(f)) for f in ("positive_marks", "negative_marks", "tolerance")):
                errors.append(f"{qw}: marks must be numbers.")
                continue
            ref, state = None, "none"
            if legacy:
                path = q.get("image_asset")
                if path:
                    if not isinstance(path, str) or not _MEMBER_RE.match(path):
                        errors.append(f"{qw}: unsafe image path {path!r}.")
                        continue
                    ref = path
                    if path in infos:
                        state = "ok"
                        assets.setdefault(path, {"ref": path, "path": path, "subject": None, "sha256": None,
                                                 "filename": _LEGACY_PREFIX_RE.sub("", path.split("/", 1)[1])})
                    else:
                        state = "missing_package"
                        ewarn.append({"code": "image_missing_in_package", "message": f"Question {qi + 1}: its image '{path}' is missing from the package; the question is imported without it."})
            else:
                status = q.get("image_status", "none")
                if status == "ok":
                    ref = q.get("image_asset_id")
                    if ref not in assets:
                        errors.append(f"{qw}: refers to image '{ref}' which is not in the package.")
                        continue
                    state = "ok"
                elif status == "missing":
                    state = "missing_source"
                    ewarn.append({"code": "image_missing_at_source", "message": f"Question {qi + 1}: its image was already missing when the exam was exported."})
                elif status not in ("none", None):
                    errors.append(f"{qw}: unknown image_status {status!r}.")
                    continue
            if state == "ok":
                ok_images += 1
            pq_list.append({"q": q, "ref": ref, "state": state, "position": qi})
        if not legacy and e.get("expected_image_count") != ok_images:
            errors.append(f"{where}: expected {e.get('expected_image_count')} images but the package references {ok_images}.")
        parsed_exams.append({"data": e, "name": name, "questions": pq_list, "warnings": ewarn, "category": cat, "subcategory": sub})

    # image files: type, size, integrity — streamed, one at a time --------------
    inspected: Dict[str, Tuple[Optional[str], str, int]] = {}
    for a in assets.values():
        path = a["path"]
        if path in inspected:
            kind, sha, size = inspected[path]
        else:
            h, size, head = hashlib.sha256(), 0, b""
            with zf.open(infos[path]) as fh:
                while True:
                    chunk = fh.read(64 * 1024)
                    if not chunk:
                        break
                    if not head:
                        head = chunk[:16]
                    size += len(chunk)
                    if size > MAX_IMPORT_IMAGE_BYTES:
                        break
                    h.update(chunk)
            kind, sha = detect_image_kind(head), h.hexdigest()
            inspected[path] = (kind, sha, size)
        ext = os.path.splitext(path)[1].lower()
        if size > MAX_IMPORT_IMAGE_BYTES:
            errors.append(f"'{path}' is larger than the {MAX_IMPORT_IMAGE_BYTES}-byte image limit.")
        elif kind is None:
            errors.append(f"'{path}' is not a PNG/JPEG/GIF/WebP/BMP image.")
        elif ext not in _IMAGE_KINDS[kind][0] or ext not in config.ALLOWED_IMAGE_EXTS:
            errors.append(f"'{path}' has an extension that does not match its content ({kind}).")
        elif a["sha256"] is not None and a["sha256"] != sha:
            errors.append(f"'{path}' is corrupt (its hash differs from the manifest).")
        else:
            a["sha256"], a["kind"] = sha, kind
    if errors:
        raise ImportValidationError(errors)
    return {"version": fmt, "legacy": legacy, "assets": assets, "exams": parsed_exams, "warnings": warnings, "infos": infos}


# ─────────────────────────────────────────────────────────────────────────────
# Import: database helpers (all take the exam's transaction cursor)
# ─────────────────────────────────────────────────────────────────────────────

def _q_all(cur, sql: str, params=()) -> List[Dict]:
    cur.execute(sql, params)
    return [dict(r) for r in cur.fetchall()]


def _pick(rows: List[Dict], column: str, name: str, kind: str) -> Tuple[Optional[Dict], Optional[str]]:
    """The single existing record `name` refers to: (row, 'exact'|'normalized'), or (None, None) when there is none.
    Several candidates raise _Ambiguous — never a guess."""
    stripped = name.strip()
    exact = [r for r in rows if r[column] == stripped]
    if len(exact) == 1:
        return exact[0], "exact"
    loose = [r for r in rows if _norm(r[column]) == _norm(name)]
    if len(exact) > 1 or len(loose) > 1:
        found = exact or loose
        raise _Ambiguous(f"{kind} '{name}' matches {len(found)} existing records (ids {', '.join(str(r['id']) for r in found)}); "
                         f"remove the duplicates in this environment, then retry.")
    return (loose[0], "normalized") if loose else (None, None)


def _resolve_category(cur, name: str, rep: Dict) -> Dict:
    row, how = _pick(_q_all(cur, "SELECT id, name FROM categories"), "name", name, "Category")
    if row:
        rep["category"] = {"name": row["name"], "action": "reused" if how == "exact" else "matched_normalized", "id": row["id"]}
        return row
    cur.execute("INSERT INTO categories (name) VALUES (%s) RETURNING id, name", (name.strip(),))
    row = dict(cur.fetchone())
    rep["category"] = {"name": row["name"], "action": "created", "id": row["id"]}
    return row


def _resolve_subcategory(cur, category_id: int, name: str, rep: Dict) -> Dict:
    row, how = _pick(_q_all(cur, "SELECT id, name FROM subcategories WHERE category_id=%s", (category_id,)), "name", name, "Subcategory")
    if row:
        rep["subcategory"] = {"name": row["name"], "action": "reused" if how == "exact" else "matched_normalized", "id": row["id"]}
        return row
    cur.execute("INSERT INTO subcategories (category_id, name) VALUES (%s, %s) RETURNING id, name", (category_id, name.strip()))
    row = dict(cur.fetchone())
    rep["subcategory"] = {"name": row["name"], "action": "created", "id": row["id"]}
    return row


def _resolve_subject(cur, name: str, may_create: bool, rep: Dict) -> Dict:
    if not valid_subject_name(name):
        raise ValueError(f"Subject name {name!r} is not valid for a storage folder.")
    row, how = _pick(_q_all(cur, "SELECT id, subject_name FROM subjects"), "subject_name", name, "Subject")
    if row:
        rep["subjects"].append({"source": name, "destination": row["subject_name"], "action": "reused" if how == "exact" else "matched_normalized"})
        return row
    if not may_create:
        raise PermissionError(f"Subject '{name}' does not exist in this environment and your account is not allowed to create Subjects. "
                              "Create it under Manage Subjects (or ask an admin with that permission) and retry.")
    cur.execute(
        "INSERT INTO subjects (subject_name, subject_folder_id, subject_folder_created_at) VALUES (%s, %s, %s) RETURNING id, subject_name",
        (name, name, now_utc_naive().strftime("%Y-%m-%d %H:%M:%S")),
    )
    row = dict(cur.fetchone())
    rep["subjects"].append({"source": name, "destination": row["subject_name"], "action": "created"})
    return row


def _num(v) -> float:
    try:
        return round(float(v), 6)
    except (TypeError, ValueError):
        return 0.0


def _question_values(q: Dict) -> Dict:
    """A question as it will be stored, with the column defaults an older package may rely on."""
    out = {}
    for f in _QUESTION_FIELDS:
        v = q.get(f)
        out[f] = _QUESTION_DEFAULTS.get(f) if v is None and f in _QUESTION_DEFAULTS else v
    return out


def _fingerprint_row(v: Dict, has_image: bool) -> str:
    return json.dumps([v["question_text"], v["option_a"], v["option_b"], v["option_c"], v["option_d"], v["correct_answer"],
                       v["question_type"], _num(v["positive_marks"]), _num(v["negative_marks"]), _num(v["tolerance"]), bool(has_image)],
                      ensure_ascii=False)


def _exam_fingerprint(rows: List[str]) -> str:
    return hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest()


_Q_FINGERPRINT_SQL = ("SELECT question_text, option_a, option_b, option_c, option_d, correct_answer, question_type, positive_marks, "
                      "negative_marks, tolerance, image_path FROM questions WHERE exam_id=%s ORDER BY id")


def _restore_asset(storage, zf, infos, asset: Dict, dest_subject: str, created_keys: List[str]) -> str:
    """Puts one image into storage as '<Subject>/<file>' and returns that key. Never overwrites: an existing identical file
    is reused, a different file with the same name pushes this one to a hash-suffixed name."""
    ext = os.path.splitext(asset["path"])[1].lower()
    base = secure_filename(asset["filename"]) or "image"
    stem = os.path.splitext(base)[0] or "image"
    if os.path.splitext(base)[1].lower() not in config.ALLOWED_IMAGE_EXTS:
        base = stem + ext
    data = _read_member(zf, infos[asset["path"]], MAX_IMPORT_IMAGE_BYTES)
    sha = hashlib.sha256(data).hexdigest()
    if sha != asset["sha256"]:
        raise ValueError(f"'{asset['path']}' changed while it was being imported.")
    exists = getattr(storage, "exists_strict", storage.exists)
    mime = _IMAGE_KINDS[asset["kind"]][1]
    for name in (base, f"{stem}-{sha[:8]}{ext}", f"{stem}-{sha[:16]}{ext}"):
        key = f"{dest_subject}/{name}"
        if not exists(key):
            storage.upload(key, data, mime)
            created_keys.append(key)
            return key
        if hashlib.sha256(storage.download(key)).hexdigest() == sha:
            return key                                   # identical file already there: reuse it, own nothing
    raise ValueError(f"Could not find a free file name for '{asset['filename']}' in Subject '{dest_subject}'.")


def _cleanup(storage, keys: List[str], er: Dict) -> None:
    """Deletes ONLY the objects this exam's import created (never an existing or shared one)."""
    removed, failed = [], []
    for key in dict.fromkeys(keys):
        try:
            storage.delete([key])
            removed.append(key)
        except Exception as e:
            failed.append({"key": key, "error": str(e)})
    er["cleanup"] = {"removed": removed, "failed": failed}
    if failed:
        er["warnings"].append({"code": "cleanup_incomplete", "message": f"{len(failed)} uploaded file(s) could not be removed after the failure and may be orphaned: "
                               + ", ".join(f['key'] for f in failed[:5])})


def _invalidate_category_cache() -> None:
    try:
        from app.db.categories import _invalidate_categories_cache
        _invalidate_categories_cache()
    except Exception:
        pass


def _exam_row_exists(exam_id: int) -> bool:
    try:
        return fetch_one("SELECT id FROM exams WHERE id=%s", (exam_id,)) is not None
    except Exception:
        return False


def _new_exam_report(idx: int, pe: Dict) -> Dict:
    imgs = sum(1 for p in pe["questions"] if p["state"] in ("ok", "missing_package", "missing_source"))
    return {
        "index": idx, "name": pe["name"], "source_name": pe["name"], "source_id": pe["data"].get("source_id"), "id": None, "status": "failed",
        "error": None, "warnings": [dict(w) for w in pe["warnings"]], "existing_exam_id": None,
        "questions": {"expected": len(pe["questions"]), "imported": 0},
        "images": {"expected": imgs, "restored": 0, "reused": 0, "missing": 0},
        "mappings": {"restored": 0, "unresolved": 0},
        "category": None, "subcategory": None, "subjects": [], "cleanup": None, "id_map": {"exam": None, "questions": 0},
    }


def _import_exam(ctx: Dict, idx: int, pe: Dict) -> Dict:
    data, storage = pe["data"], ctx["storage"]
    er = _new_exam_report(idx, pe)
    created_keys: List[str] = []
    new_exam_id = None
    new_ids: List[int] = []
    mapped = 0
    try:
        with transaction() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(%s)", (_IMPORT_LOCK_KEY,))
            src_name = pe["name"]

            # 1. is this exam already here? (decided before anything is created)
            src_values = [_question_values(p["q"]) for p in pe["questions"]]
            src_fp = _exam_fingerprint([_fingerprint_row(v, p["state"] == "ok") for v, p in zip(src_values, pe["questions"])])
            same_name = _q_all(cur, "SELECT id, name FROM exams WHERE lower(trim(name)) = lower(trim(%s))", (src_name,))
            identical = None
            for ex in same_name:
                rows = _q_all(cur, _Q_FINGERPRINT_SQL, (ex["id"],))
                fp = _exam_fingerprint([_fingerprint_row(_question_values(r), bool(r.get("image_path"))) for r in rows])
                if fp == src_fp:
                    identical = ex
                    break
            if identical and ctx["on_duplicate"] == "skip":
                er.update(status="skipped", id=identical["id"], existing_exam_id=identical["id"])
                er["warnings"].append({"code": "already_imported", "message": f"An exam with this name and identical questions already exists (id {identical['id']}); skipped."})
                return er
            exam_name = src_name
            if same_name:
                n, stamp = 1, ctx["stamp"]
                while True:
                    exam_name = f"{src_name} (Imported {stamp})" if n == 1 else f"{src_name} (Imported {stamp} #{n})"
                    if not _q_all(cur, "SELECT id FROM exams WHERE lower(trim(name)) = lower(trim(%s))", (exam_name,)):
                        break
                    n += 1
                er["warnings"].append({"code": "name_conflict_renamed",
                                       "message": (f"An identical exam already exists; imported again as a separate copy named '{exam_name}'." if identical else
                                                   f"A different exam named '{src_name}' already exists; this one was imported as '{exam_name}'.")})
            er["name"] = exam_name

            # 2. category / subcategory
            category = subcategory = None
            if pe["category"]:
                category = _resolve_category(cur, pe["category"], er)
                if pe["subcategory"]:
                    subcategory = _resolve_subcategory(cur, category["id"], pe["subcategory"], er)

            # 3. subjects needed by this exam's images
            needed = {p["ref"]: ctx["assets"][p["ref"]] for p in pe["questions"] if p["state"] == "ok"}
            dest_subject = {}
            for source_subject in sorted({a["subject"] for a in needed.values()}, key=lambda s: (s is None, s or "")):
                row = _resolve_subject(cur, source_subject if source_subject is not None else LEGACY_SUBJECT, ctx["allow_create_subjects"], er)
                dest_subject[source_subject] = row["subject_name"]
            if any(a["subject"] is None for a in needed.values()) and not ctx["legacy"]:
                er["warnings"].append({"code": "images_without_subject", "message": f"Images that had no Subject were restored into '{LEGACY_SUBJECT}'."})

            # 4. exam row
            exam_row = {f: data.get(f) for f in _EXAM_FIELDS if f in data}
            exam_row.update(name=exam_name, category_id=category["id"] if category else None,
                            subcategory_id=subcategory["id"] if subcategory else None)
            cols = list(exam_row)
            cur.execute(f"INSERT INTO exams ({','.join(cols)}) VALUES ({','.join(['%s'] * len(cols))}) RETURNING id", [exam_row[c] for c in cols])
            new_exam_id = cur.fetchone()["id"]

            # 5. images (storage; compensated below if anything after this fails)
            keys: Dict[str, str] = {}
            for ref, asset in needed.items():
                before = len(created_keys)
                keys[ref] = _restore_asset(storage, ctx["zf"], ctx["infos"], asset, dest_subject[asset["subject"]], created_keys)
                if len(created_keys) == before:
                    er["images"]["reused"] += 1

            # 6. questions
            q_cols = _QUESTION_FIELDS + ["exam_id", "image_path"]
            row_sql = "(" + ",".join(["%s"] * len(q_cols)) + ")"
            for start in range(0, len(pe["questions"]), _INSERT_CHUNK):
                chunk = list(zip(src_values[start:start + _INSERT_CHUNK], pe["questions"][start:start + _INSERT_CHUNK]))
                params = []
                for v, p in chunk:
                    params.extend([v[f] for f in _QUESTION_FIELDS] + [new_exam_id, keys.get(p["ref"]) if p["state"] == "ok" else None])
                cur.execute(f"INSERT INTO questions ({','.join(q_cols)}) VALUES {','.join([row_sql] * len(chunk))} RETURNING id", params)
                new_ids.extend(r["id"] for r in cur.fetchall())
                ctx["tick"](len(chunk))

            # 7. verify before committing: anything off rolls the exam back
            mapped = sum(1 for p in pe["questions"] if p["state"] == "ok")
            if len(new_ids) != len(pe["questions"]):
                raise RuntimeError(f"Verification failed: inserted {len(new_ids)} of {len(pe['questions'])} questions.")
            cur.execute("SELECT COUNT(*) AS count FROM questions WHERE exam_id=%s", (new_exam_id,))
            if cur.fetchone()["count"] != len(pe["questions"]):
                raise RuntimeError("Verification failed: the stored question count differs from the package.")
            cur.execute("SELECT COUNT(*) AS count FROM questions WHERE exam_id=%s AND image_path IS NOT NULL", (new_exam_id,))
            if cur.fetchone()["count"] != mapped:
                raise RuntimeError("Verification failed: the stored image mappings differ from the package.")
            if dest_subject:
                wanted = sorted(set(dest_subject.values()))
                cur.execute("SELECT COUNT(*) AS count FROM subjects WHERE subject_name = ANY(%s)", (wanted,))
                if cur.fetchone()["count"] < len(wanted):
                    raise RuntimeError("Verification failed: a Subject for the restored images is missing.")
            exists = getattr(storage, "exists_strict", storage.exists)
            for key in dict.fromkeys(keys.values()):
                if not exists(key):
                    raise RuntimeError(f"Verification failed: image '{key}' is not in storage.")
            cur.execute("SELECT category_id, subcategory_id FROM exams WHERE id=%s", (new_exam_id,))
            row = cur.fetchone()
            if row["category_id"] != exam_row["category_id"] or row["subcategory_id"] != exam_row["subcategory_id"]:
                raise RuntimeError("Verification failed: the exam's category/subcategory differ from the package.")

        # committed ------------------------------------------------------------
        er["id"] = new_exam_id
        er["id_map"] = {"exam": [data.get("source_id"), new_exam_id], "questions": len(new_ids)}
        er["questions"]["imported"] = len(new_ids)
        er["images"]["restored"] = mapped
        er["images"]["missing"] = er["images"]["expected"] - mapped
        er["mappings"] = {"restored": mapped, "unresolved": er["images"]["missing"]}
        if er["images"]["missing"]:
            er["warnings"].append({"code": "images_missing", "message": f"{er['images']['missing']} question image(s) could not be restored (see above)."})
        er["status"] = "warnings" if er["warnings"] else "complete"
        if (er["category"] and er["category"]["action"] == "created") or (er["subcategory"] and er["subcategory"]["action"] == "created"):
            _invalidate_category_cache()
        return er
    except Exception as e:
        if new_exam_id is not None and _exam_row_exists(new_exam_id):
            # the failure surfaced after the commit went through: the exam is real, so its files must stay
            er.update(id=new_exam_id, status="warnings", error=None)
            er["warnings"].append({"code": "commit_state_uncertain", "message": f"The database reported '{e}' after saving; the exam exists, please check it."})
            return er
        import traceback
        traceback.print_exc()
        er["status"], er["id"] = "failed", None
        if getattr(e, "pgcode", None) == "42703":
            er["error"] = f"{e} — this environment's database is missing a column the package uses; run the latest migrations."
        else:
            er["error"] = str(e) or type(e).__name__
        if created_keys:
            _cleanup(storage, created_keys, er)
        return er


# ─────────────────────────────────────────────────────────────────────────────
# Import: entry point
# ─────────────────────────────────────────────────────────────────────────────

def run_import(zip_path: str, progress: ProgressFn = None, *, on_duplicate: str = "skip",
               allow_create_subjects: bool = True, storage=None) -> Dict:
    """Validates the whole package, then imports it exam by exam. Returns a report:
    {"status": complete|warnings|partial|failed|skipped, "imported": [...names], "failed": [{"name","error"}], "exams": [per-exam],
     "totals": {...}, "subjects": {...}, "categories": {...}, "warnings": [...], "legacy": bool, "format": str}.
    Raises ImportValidationError (a ValueError) when the package is rejected; nothing has been written in that case."""
    def _report(phase, message, percent):
        if progress:
            progress(phase, message, percent)

    if on_duplicate not in ("skip", "rename"):
        raise ValueError("on_duplicate must be 'skip' or 'rename'.")
    _report("validating", "Reading export file...", 0)
    try:
        zf = zipfile.ZipFile(zip_path, "r")
    except zipfile.BadZipFile:
        raise ImportValidationError(["This file isn't a valid .zip archive."])

    with zf:
        _report("validating", "Validating the package...", 1)
        package = _parse_package(zf)
        storage = storage or get_storage()
        exams = package["exams"]
        total_units = sum(len(e["questions"]) for e in exams) or 1
        state = {"done": 0, "cur": 0, "name": ""}

        def tick(n):
            state["done"] += n
            _report("importing", f"Importing exam {state['cur'] + 1} of {len(exams)}: {state['name']} — {state['done']}/{total_units} questions",
                    min(99, round(state["done"] / total_units * 99)))

        ctx = {"zf": zf, "infos": package["infos"], "assets": package["assets"], "storage": storage, "on_duplicate": on_duplicate,
               "allow_create_subjects": allow_create_subjects, "legacy": package["legacy"], "tick": tick,
               "stamp": now_utc_naive().strftime("%Y%m%d%H%M%S")}
        results = []
        for idx, pe in enumerate(exams):
            state["cur"], state["name"] = idx, pe["name"]
            before = state["done"]
            results.append(_import_exam(ctx, idx, pe))
            state["done"] = before + len(pe["questions"])           # a failed/skipped exam still advances the bar
        _report("complete", "Import complete", 100)

    return _build_report(package, results)


def _build_report(package: Dict, results: List[Dict]) -> Dict:
    imported = [r for r in results if r["status"] in ("complete", "warnings")]
    failed = [r for r in results if r["status"] == "failed"]
    skipped = [r for r in results if r["status"] == "skipped"]
    if failed and imported:
        status = "partial"
    elif failed:
        status = "failed"
    elif imported:
        status = "warnings" if (any(r["status"] == "warnings" for r in imported) or package["warnings"]) else "complete"
    else:
        status = "skipped"
    subjects = {"reused": [], "created": [], "matched_normalized": []}
    cats = {"created": [], "reused": []}
    subs = {"created": [], "reused": []}
    for r in imported:
        for s in r["subjects"]:
            bucket = subjects["created" if s["action"] == "created" else "matched_normalized" if s["action"] == "matched_normalized" else "reused"]
            if s["destination"] not in bucket:
                bucket.append(s["destination"])
        for key, target in (("category", cats), ("subcategory", subs)):
            info = r.get(key)
            if info:
                bucket = target["created" if info["action"] == "created" else "reused"]
                if info["name"] not in bucket:
                    bucket.append(info["name"])

    # a record created by one exam and reused by the next ones was created, once
    for group in (subjects, cats, subs):
        made = set(group.get("created", []))
        for k in group:
            if k != "created":
                group[k] = [n for n in group[k] if n not in made]

    def total(group, field):
        return sum(r[group][field] for r in imported)

    totals = {
        "exams_imported": len(imported), "exams_failed": len(failed), "exams_skipped": len(skipped),
        "questions_expected": sum(r["questions"]["expected"] for r in results), "questions_imported": total("questions", "imported"),
        "images_expected": total("images", "expected"), "images_restored": total("images", "restored"),
        "images_reused": total("images", "reused"), "images_missing": total("images", "missing"),
        "mappings_restored": total("mappings", "restored"), "mappings_unresolved": total("mappings", "unresolved"),
    }
    return {
        "status": status, "ok": status in ("complete", "warnings", "skipped"), "format": package["version"], "legacy": package["legacy"],
        "imported": [r["name"] for r in imported], "failed": [{"name": r["source_name"], "error": r["error"]} for r in failed],
        "skipped": [{"name": r["source_name"], "existing_exam_id": r["existing_exam_id"]} for r in skipped],
        "exams": results, "totals": totals, "subjects": subjects, "categories": cats, "subcategories": subs,
        "warnings": package["warnings"],
        "cleanup_failures": [f for r in results if r.get("cleanup") for f in r["cleanup"]["failed"]],
    }
