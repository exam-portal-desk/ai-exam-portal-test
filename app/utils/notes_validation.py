"""Validation and normalization for the Notes module."""

from __future__ import annotations

from typing import Any, Dict, List
from uuid import UUID


VISIBILITIES = {"private", "public", "unlisted"}
MAX_TITLE_LENGTH = 160
MAX_DESCRIPTION_LENGTH = 2_000

# Single source of truth for canvas object types the app understands.
# Shared by notes_service.save_page_objects() (editor autosave) and the
# notebook importer below, so the two can never silently drift apart.
ALLOWED_OBJECT_TYPES = {"rich_text", "drawing", "image", "sticky_note", "shape"}
# Max objects in a SINGLE save request/payload — not a cap on a page's total content. A page
# with more than this is saved as several chunked requests (see editor.js save()), each one
# this size at most; notes_service.import_notebook() chunks its bulk inserts the same way.
MAX_OBJECTS_PER_PAGE = 500

# export_notebook() stamps this into every export; the importer checks
# incoming files against the same constant so the two stay in lockstep.
NOTEBOOK_EXPORT_FORMAT = "smartai-notes-export-v1"
IMPORT_TITLE_PREFIX = "Imported - "

_INVALID_FILE_MESSAGE = "Invalid Notebook file. Please select a Notebook exported from Smart AI Exam Portal."


class NotesValidationError(ValueError):
    """Raised when Notes input is invalid."""


def validate_notebook_id(value: str) -> str:
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise NotesValidationError("Invalid notebook identifier.") from exc


def normalize_notebook_payload(payload: Dict[str, Any], *, partial: bool = False) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise NotesValidationError("Invalid request data.")

    clean: Dict[str, Any] = {}
    allowed = ("title", "description", "visibility", "subject", "department", "semester", "course", "topic", "language", "tags")
    unknown = set(payload) - set(allowed)
    if unknown:
        raise NotesValidationError("Unsupported notebook fields.")

    if "title" in payload or not partial:
        title = str(payload.get("title", "")).strip()
        if not title:
            raise NotesValidationError("A notebook title is required.")
        if len(title) > MAX_TITLE_LENGTH:
            raise NotesValidationError(f"Notebook titles can be at most {MAX_TITLE_LENGTH} characters.")
        clean["title"] = title

    for field in ("description", "subject", "department", "semester", "course", "topic", "language"):
        if field in payload:
            value = str(payload[field] or "").strip()
            limit = MAX_DESCRIPTION_LENGTH if field == "description" else 120
            if len(value) > limit:
                raise NotesValidationError(f"{field.replace('_', ' ').title()} is too long.")
            clean[field] = value or None

    if "visibility" in payload:
        visibility = str(payload["visibility"] or "").lower().strip()
        if visibility not in VISIBILITIES:
            raise NotesValidationError("Visibility must be private, public, or unlisted.")
        clean["visibility"] = visibility

    if "tags" in payload:
        raw_tags = payload["tags"]
        if isinstance(raw_tags, str):
            raw_tags = raw_tags.split(",")
        if not isinstance(raw_tags, list):
            raise NotesValidationError("Tags must be a list or comma-separated text.")
        tags = []
        for raw in raw_tags:
            tag = str(raw).strip()
            if tag and tag.lower() not in {item.lower() for item in tags}:
                if len(tag) > 50:
                    raise NotesValidationError("Each tag must be 50 characters or fewer.")
                tags.append(tag)
        if len(tags) > 12:
            raise NotesValidationError("Use at most 12 tags.")
        clean["tags"] = tags

    return clean


def _clean_text(value: Any, limit: int, *, required: bool = False, field: str = "value") -> str:
    text = str(value or "").strip()
    if required and not text:
        raise NotesValidationError(f"Notebook {field} is required.")
    if len(text) > limit:
        text = text[:limit]
    return text


def validate_notebook_import(raw: Any) -> Dict[str, Any]:
    """Validate a parsed Notebook export file end-to-end and return a
    sanitized structure ready for notes_service.import_notebook().

    This never partially accepts a file: any structural problem raises
    NotesValidationError with the same generic message the product spec
    asks for, so we don't leak schema details to the client. Only after
    every page and every object passes do we return sanitized data.
    """
    if not isinstance(raw, dict):
        raise NotesValidationError(_INVALID_FILE_MESSAGE)

    if raw.get("format") != NOTEBOOK_EXPORT_FORMAT:
        raise NotesValidationError(_INVALID_FILE_MESSAGE)

    source_notebook = raw.get("notebook")
    pages_in = raw.get("pages")
    assets_in = raw.get("assets", [])
    if not isinstance(source_notebook, dict) or not isinstance(pages_in, list) or not isinstance(assets_in, list):
        raise NotesValidationError(_INVALID_FILE_MESSAGE)
    if not pages_in:
        raise NotesValidationError("This Notebook file has no pages to import.")

    title = _clean_text(source_notebook.get("title"), MAX_TITLE_LENGTH, required=True, field="title")
    description = _clean_text(source_notebook.get("description"), MAX_DESCRIPTION_LENGTH)

    # Assets are keyed by their ORIGINAL id so page objects below can be
    # re-linked to the right sanitized asset entry by that same original id.
    assets_by_original_id: Dict[str, Dict[str, Any]] = {}
    for asset in assets_in:
        if not isinstance(asset, dict):
            raise NotesValidationError(_INVALID_FILE_MESSAGE)
        asset_id = str(asset.get("id") or "")
        storage_path = str(asset.get("storage_path") or "")
        if not asset_id or not storage_path:
            raise NotesValidationError(_INVALID_FILE_MESSAGE)
        assets_by_original_id[asset_id] = {
            "storage_path": storage_path,
            "original_filename": (str(asset.get("original_filename") or "")[:255] or None),
            "content_type": str(asset.get("content_type") or "") or None,
            "byte_size": asset.get("byte_size") if isinstance(asset.get("byte_size"), int) else None,
        }

    clean_pages: List[Dict[str, Any]] = []
    for page in pages_in:
        if not isinstance(page, dict):
            raise NotesValidationError(_INVALID_FILE_MESSAGE)
        page_title = _clean_text(page.get("title"), MAX_TITLE_LENGTH) or "Untitled page"
        objects_in = page.get("objects", [])
        if not isinstance(objects_in, list):
            raise NotesValidationError(_INVALID_FILE_MESSAGE)

        clean_objects: List[Dict[str, Any]] = []
        for obj in objects_in:
            if not isinstance(obj, dict) or obj.get("object_type") not in ALLOWED_OBJECT_TYPES:
                raise NotesValidationError(_INVALID_FILE_MESSAGE)
            transform = obj.get("transform") if isinstance(obj.get("transform"), dict) else {}
            payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
            original_asset_id = obj.get("asset_id")
            if obj["object_type"] == "image":
                original_asset_id = str(original_asset_id or "")
                if original_asset_id not in assets_by_original_id:
                    raise NotesValidationError("This Notebook file references an image that is missing from the export.")
            else:
                original_asset_id = None
            clean_objects.append({
                "object_type": obj["object_type"],
                "transform": transform,
                "payload": payload,
                "original_asset_id": original_asset_id,
            })
        clean_pages.append({"title": page_title, "objects": clean_objects})

    return {
        "title": title,
        "description": description,
        "pages": clean_pages,
        "assets_by_original_id": assets_by_original_id,
    }