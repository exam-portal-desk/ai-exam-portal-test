"""PostgreSQL data access for private student notebooks."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.db import fetch_one, fetch_all, execute, execute_returning, set_clause, insert_returning, transaction
# _normalize_row is "private" (leading underscore) but this module is the one place outside
# app/db/__init__.py that needs it directly: the versioned object-save below has to run several
# conditional statements in ONE transaction (see replace_page_objects_versioned), so it can't use
# the fetch_one/fetch_all/execute_returning wrappers (each of those opens and commits its own
# connection) — it uses the raw transaction() cursor instead, which returns un-normalized rows.
from app.db import _normalize_row


_SECTION_FILTERS = {
    "created": "AND is_imported = false",
    "imported": "AND is_imported = true",
    "public": "AND visibility = 'public'",
}


def list_notebooks_for_owner(owner_id: int, limit: int = 24, offset: int = 0, section: str = None, search: str = None) -> List[Dict[str, Any]]:
    """Fetches limit+1 rows so the caller can tell if there's another page
    without a separate COUNT(*) query — trim the extra row before display.

    `section` (created/imported/public) scopes the My Notebooks page to one
    independently-paginated section instead of the full unfiltered list;
    omitting it preserves the original unfiltered query for other callers.
    `search` does a server-side ILIKE on title — the section stays small
    (paginated) so this never means scanning a client-side copy of everything."""
    section_sql = _SECTION_FILTERS.get(section, "")
    params: List[Any] = [owner_id]
    search_sql = ""
    if search:
        search_sql = "AND title ILIKE %s"
        params.append(f"%{search}%")
    params += [limit + 1, offset]
    return fetch_all(
        "SELECT id,title,description,visibility,subject,course,tags,is_imported,created_at,updated_at,published_at "
        f"FROM notes_notebooks WHERE owner_id=%s AND deleted_at IS NULL {section_sql} {search_sql} "
        "ORDER BY updated_at DESC, id DESC LIMIT %s OFFSET %s",
        params,
    )


def list_trashed_notebooks_for_owner(owner_id: int) -> List[Dict[str, Any]]:
    return fetch_all(
        "SELECT id,title,deleted_at,updated_at FROM notes_notebooks "
        "WHERE owner_id=%s AND deleted_at IS NOT NULL ORDER BY deleted_at DESC",
        (owner_id,),
    )


def get_owned_notebook(notebook_id: str, owner_id: int, *, include_deleted: bool = False) -> Optional[Dict[str, Any]]:
    query = "SELECT * FROM notes_notebooks WHERE id=%s AND owner_id=%s"
    if not include_deleted:
        query += " AND deleted_at IS NULL"
    return fetch_one(query + " LIMIT 1", (notebook_id, owner_id))


def get_share(notebook_id: str, user_id: int) -> Optional[Dict[str, Any]]:
    return fetch_one(
        "SELECT * FROM notes_notebook_shares WHERE notebook_id=%s AND user_id=%s LIMIT 1",
        (notebook_id, user_id),
    )


def get_accessible_notebook(notebook_id: str, user_id: int, *, include_deleted: bool = False) -> Optional[Dict[str, Any]]:
    """Owner OR active-share access — the single permission gate everything
    else (get_editor_notebook and everything that funnels through it) relies
    on. Adds an `access` key to the returned row: 'owner', 'editor', or
    'viewer'. Returns None if the notebook doesn't exist or this user has
    neither ownership nor a share."""
    query = "SELECT * FROM notes_notebooks WHERE id=%s"
    if not include_deleted:
        query += " AND deleted_at IS NULL"
    notebook = fetch_one(query + " LIMIT 1", (notebook_id,))
    if not notebook:
        return None
    if notebook["owner_id"] == user_id:
        notebook["access"] = "owner"
        return notebook
    share = get_share(notebook_id, user_id)
    if share:
        notebook["access"] = share["permission"]
        notebook["share_id"] = share["id"]
        return notebook
    return None


def update_shared_notebook_fields(notebook_id: str, editor_user_id: int, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Like update_owned_notebook, but for an Editor updating title/description
    (the only notebook-metadata fields an Editor may touch — see
    notes_service.update_notebook). Re-verifies the editor share inside the
    WHERE clause itself rather than only trusting the caller's check, so this
    stays safe even if called from a new code path later."""
    sc, params = set_clause(updates)
    rows = execute_returning(
        f"UPDATE notes_notebooks SET {sc} WHERE id=%s AND deleted_at IS NULL AND EXISTS ("
        "SELECT 1 FROM notes_notebook_shares WHERE notebook_id=%s AND user_id=%s AND permission='editor'"
        ") RETURNING *",
        params + [notebook_id, notebook_id, editor_user_id],
    )
    return rows[0] if rows else None


def upsert_share(notebook_id: str, user_id: int, permission: str, shared_by: int) -> Dict[str, Any]:
    rows = execute_returning(
        "INSERT INTO notes_notebook_shares (notebook_id, user_id, permission, shared_by) VALUES (%s,%s,%s,%s) "
        "ON CONFLICT (notebook_id, user_id) DO UPDATE SET permission=EXCLUDED.permission, updated_at=now() "
        "RETURNING *",
        (notebook_id, user_id, permission, shared_by),
    )
    return rows[0]


def update_share_permission(notebook_id: str, user_id: int, permission: str) -> Optional[Dict[str, Any]]:
    rows = execute_returning(
        "UPDATE notes_notebook_shares SET permission=%s, updated_at=now() WHERE notebook_id=%s AND user_id=%s RETURNING *",
        (permission, notebook_id, user_id),
    )
    return rows[0] if rows else None


def delete_share(notebook_id: str, user_id: int) -> bool:
    rows = execute_returning(
        "DELETE FROM notes_notebook_shares WHERE notebook_id=%s AND user_id=%s RETURNING id",
        (notebook_id, user_id),
    )
    return bool(rows)


def list_shares_for_notebook(notebook_id: str) -> List[Dict[str, Any]]:
    """Who currently has access to a notebook, for the owner's manage-access panel."""
    return fetch_all(
        "SELECT s.user_id, s.permission, s.created_at, s.updated_at, "
        "u.username, u.full_name, u.profile_photo_key "
        "FROM notes_notebook_shares s JOIN users u ON u.id = s.user_id "
        "WHERE s.notebook_id=%s ORDER BY s.created_at",
        (notebook_id,),
    )


def search_shareable_users(term: str, notebook_id: str, owner_id: int, limit: int = 10) -> List[Dict[str, Any]]:
    """Candidates to add to a share: excludes the searching owner and anyone
    who already has a share on this notebook."""
    return fetch_all(
        "SELECT id, username, full_name, profile_photo_key FROM users "
        "WHERE username ILIKE %s AND id != %s "
        "AND id NOT IN (SELECT user_id FROM notes_notebook_shares WHERE notebook_id=%s) "
        "ORDER BY username LIMIT %s",
        (f"%{term}%", owner_id, notebook_id, limit),
    )


def list_notebooks_shared_with_user(user_id: int, limit: int = 20, offset: int = 0, search: str = None) -> List[Dict[str, Any]]:
    """Fetches limit+1 rows (same has_more trick as list_notebooks_for_owner)."""
    search_sql = ""
    params: List[Any] = [user_id]
    if search:
        search_sql = "AND n.title ILIKE %s"
        params.append(f"%{search}%")
    params += [limit + 1, offset]
    return fetch_all(
        "SELECT n.id, n.title, n.description, n.visibility, n.subject, n.course, n.tags, "
        "n.is_imported, n.created_at, n.updated_at, "
        "s.permission, "
        "o.id AS owner_id, o.username AS owner_username, o.full_name AS owner_full_name "
        "FROM notes_notebook_shares s "
        "JOIN notes_notebooks n ON n.id = s.notebook_id AND n.deleted_at IS NULL "
        "JOIN users o ON o.id = n.owner_id "
        f"WHERE s.user_id=%s {search_sql} "
        "ORDER BY n.updated_at DESC, n.id DESC LIMIT %s OFFSET %s",
        params,
    )


def list_recent_shares_for_user(user_id: int, limit: int = 15) -> List[Dict[str, Any]]:
    """Recently shared-with-me notebooks, sorted by the actual share-grant
    timestamp (s.created_at) — unlike list_notebooks_shared_with_user, which
    sorts by notebook content-update time and is the wrong signal for "what
    was recently shared with me". Small candidate pool for the dashboard's
    unseen-filtering, not a paginated browse list."""
    return fetch_all(
        "SELECT s.id AS share_id, s.permission, s.created_at AS shared_at, "
        "n.id AS notebook_id, n.title, "
        "o.id AS owner_id, o.username AS owner_username, o.full_name AS owner_full_name "
        "FROM notes_notebook_shares s "
        "JOIN notes_notebooks n ON n.id = s.notebook_id AND n.deleted_at IS NULL "
        "JOIN users o ON o.id = n.owner_id "
        "WHERE s.user_id=%s ORDER BY s.created_at DESC LIMIT %s",
        (user_id, limit),
    )


def list_notebooks_shared_by_owner(owner_id: int, limit: int = 20, offset: int = 0, search: str = None) -> List[Dict[str, Any]]:
    """Owner's own notebooks that currently have at least one active share."""
    search_sql = ""
    params: List[Any] = [owner_id]
    if search:
        search_sql = "AND n.title ILIKE %s"
        params.append(f"%{search}%")
    params += [limit + 1, offset]
    return fetch_all(
        "SELECT n.id, n.title, n.description, n.visibility, n.subject, n.course, n.tags, "
        "n.is_imported, n.created_at, n.updated_at, COUNT(s.user_id) AS share_count "
        "FROM notes_notebooks n "
        "JOIN notes_notebook_shares s ON s.notebook_id = n.id "
        f"WHERE n.owner_id=%s AND n.deleted_at IS NULL {search_sql} "
        "GROUP BY n.id "
        "ORDER BY n.updated_at DESC, n.id DESC LIMIT %s OFFSET %s",
        params,
    )


def create_notebook(owner_id: int, notebook: Dict[str, Any]) -> Dict[str, Any]:
    return insert_returning("notes_notebooks", {"owner_id": owner_id, **notebook})


def update_owned_notebook(notebook_id: str, owner_id: int, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    sc, params = set_clause(updates)
    rows = execute_returning(
        f"UPDATE notes_notebooks SET {sc} WHERE id=%s AND owner_id=%s AND deleted_at IS NULL RETURNING *",
        params + [notebook_id, owner_id],
    )
    return rows[0] if rows else None


def soft_delete_owned_notebook(notebook_id: str, owner_id: int) -> bool:
    rows = execute_returning(
        "UPDATE notes_notebooks SET deleted_at=%s WHERE id=%s AND owner_id=%s AND deleted_at IS NULL RETURNING id",
        (datetime.now(timezone.utc).isoformat(), notebook_id, owner_id),
    )
    return bool(rows)


def restore_owned_notebook(notebook_id: str, owner_id: int) -> Optional[Dict[str, Any]]:
    rows = execute_returning(
        "UPDATE notes_notebooks SET deleted_at=NULL WHERE id=%s AND owner_id=%s AND deleted_at IS NOT NULL RETURNING *",
        (notebook_id, owner_id),
    )
    return rows[0] if rows else None


def list_pages(notebook_id: str) -> List[Dict[str, Any]]:
    return fetch_all(
        "SELECT id,title,position,created_at,updated_at FROM notes_pages WHERE notebook_id=%s ORDER BY position",
        (notebook_id,),
    )


def create_page(notebook_id: str, title: str, position: int) -> Dict[str, Any]:
    return insert_returning("notes_pages", {"notebook_id": notebook_id, "title": title, "position": position})


def update_page(page_id: str, notebook_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    sc, params = set_clause(updates)
    rows = execute_returning(
        f"UPDATE notes_pages SET {sc} WHERE id=%s AND notebook_id=%s RETURNING *",
        params + [page_id, notebook_id],
    )
    return rows[0] if rows else None


def delete_page(page_id: str, notebook_id: str) -> bool:
    rows = execute_returning(
        "DELETE FROM notes_pages WHERE id=%s AND notebook_id=%s RETURNING id", (page_id, notebook_id)
    )
    return bool(rows)


def page_name_exists(notebook_id: str, title: str, exclude_page_id: str | None = None) -> bool:
    rows = fetch_all(
        "SELECT id FROM notes_pages WHERE notebook_id=%s AND title ILIKE %s", (notebook_id, title)
    )
    return any(row["id"] != exclude_page_id for row in rows)


def replace_page_objects_versioned(
    page_id: str,
    new_objects: List[Dict[str, Any]],
    updates: List[Dict[str, Any]],
    deletions: List[Dict[str, Any]],
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Optimistic-concurrency canvas save: never silently overwrites or
    resurrects an object someone else has already changed since this client
    last read it.

    - `new_objects`: records with no `id` — always inserted, version=1, can't conflict.
    - `updates`: records WITH `id` and `expected_version` — applied only if
      the row's current version still matches; otherwise it's a conflict.
    - `deletions`: `{"id", "expected_version"}` — deleted only if the version
      still matches; if the row is already gone, that's NOT a conflict (the
      caller's desired end state — object absent — already holds); if it
      still exists with a different version, it IS a conflict (someone
      changed it after this client last read it, so don't destroy their edit).

    Batched, not per-object: an earlier version of this function ran one
    UPDATE/DELETE/INSERT (plus a follow-up SELECT on every miss) per object,
    inside a single held transaction — for a page with N objects that's
    roughly 2N sequential round trips to a remote Postgres instance, which
    in production turned "save a page" into a multi-second operation and,
    under load, occasionally exhausted the connection pool outright (a raw
    PoolError, not a handled ValueError — surfaced to users as a 500). This
    version does at most 4 round trips total *regardless of object count*:
    one batched conditional UPDATE (via `FROM (VALUES ...)`), one batched
    conditional DELETE (via `USING (VALUES ...)`), one multi-row INSERT, and
    one follow-up SELECT covering every id that didn't get applied (to
    report what its current server-side state actually is) — same
    conflict-detection guarantees, just set-based instead of row-at-a-time.

    Returns (saved_rows, conflicts) where each conflict is
    {"id": ..., "current": {...} | None} — None means it was deleted
    upstream, a dict is the object's current server-side state.
    """
    saved: List[Dict[str, Any]] = []
    conflicts: List[Dict[str, Any]] = []
    with transaction() as cur:
        updated_ids: set = set()
        if updates:
            values_sql = ", ".join(["(%s::uuid,%s::text,%s::int,%s::jsonb,%s::jsonb,%s::uuid,%s::int)"] * len(updates))
            params: List[Any] = []
            for item in updates:
                params += [item["id"], item["object_type"], item["z_index"], item["transform"], item["payload"], item["asset_id"], item["expected_version"]]
            cur.execute(
                "UPDATE notes_objects AS o SET object_type=v.object_type, z_index=v.z_index, transform=v.transform, "
                "payload=v.payload, asset_id=v.asset_id, version=o.version+1, updated_at=now() "
                f"FROM (VALUES {values_sql}) AS v(id,object_type,z_index,transform,payload,asset_id,expected_version) "
                "WHERE o.id=v.id AND o.page_id=%s AND o.version=v.expected_version RETURNING o.*",
                params + [page_id],
            )
            for row in cur.fetchall():
                row = _normalize_row(dict(row))
                saved.append(row)
                updated_ids.add(str(row["id"]))

        deleted_ids: set = set()
        if deletions:
            values_sql = ", ".join(["(%s::uuid,%s::int)"] * len(deletions))
            params = []
            for item in deletions:
                params += [item["id"], item["expected_version"]]
            cur.execute(
                "DELETE FROM notes_objects AS o USING (VALUES " + values_sql + ") AS v(id,expected_version) "
                "WHERE o.id=v.id AND o.page_id=%s AND o.version=v.expected_version RETURNING o.id",
                params + [page_id],
            )
            deleted_ids = {str(row["id"]) for row in cur.fetchall()}

        if new_objects:
            # Uses each CLIENT's id (generated client-side, see editor.js uid()), not a
            # DB-generated one — the client's in-memory Fabric object already carries this id
            # and will reference it on every future update/delete for this object; if the
            # server minted a different one here, this object would become permanently
            # unreachable from the client's next save (it would look "deleted upstream" forever).
            values_sql = ", ".join(["(%s::uuid,%s::uuid,%s::text,%s::int,%s::jsonb,%s::jsonb,%s::uuid)"] * len(new_objects))
            params = []
            for item in new_objects:
                params += [item["id"], page_id, item["object_type"], item["z_index"], item["transform"], item["payload"], item["asset_id"]]
            cur.execute(
                "INSERT INTO notes_objects (id,page_id,object_type,z_index,transform,payload,asset_id) "
                f"VALUES {values_sql} RETURNING *",
                params,
            )
            for row in cur.fetchall():
                saved.append(_normalize_row(dict(row)))

        # Anything requested but not applied is a conflict — resolved with ONE batched lookup
        # covering every such id, instead of a SELECT per miss.
        unresolved_update_ids = {item["id"] for item in updates} - updated_ids
        unresolved_delete_ids = {item["id"] for item in deletions} - deleted_ids
        lookup_ids = list(unresolved_update_ids | unresolved_delete_ids)
        current_by_id: Dict[str, Dict[str, Any]] = {}
        if lookup_ids:
            cur.execute("SELECT * FROM notes_objects WHERE id = ANY(%s::uuid[]) AND page_id=%s", (lookup_ids, page_id))
            for row in cur.fetchall():
                row = _normalize_row(dict(row))
                current_by_id[str(row["id"])] = row
        for oid in unresolved_update_ids:
            conflicts.append({"id": oid, "current": current_by_id.get(oid)})
        for oid in unresolved_delete_ids:
            if oid in current_by_id:  # still exists at a different version — a real conflict
                conflicts.append({"id": oid, "current": current_by_id[oid]})
            # else: already gone — not a conflict, the desired end state already holds.

    return saved, conflicts


def create_objects_bulk(records: List[Dict[str, Any]]) -> int:
    """Real single-round-trip multi-row INSERT — NOT insert_many()/executemany(), which issues
    one INSERT per row under the hood (a well-known psycopg2 limitation). Mirrors the multi-VALUES
    pattern replace_page_objects() already uses for the editor's autosave path, minus ON CONFLICT
    (these are always brand-new rows) and minus RETURNING (the caller — notes_service.import_notebook,
    the only caller — doesn't need the rows back). This is what turns a page with hundreds of
    objects from hundreds of round trips into one."""
    if not records:
        return 0
    cols = list(records[0].keys())
    row_sql = "(" + ", ".join(["%s"] * len(cols)) + ")"
    values_sql = ", ".join([row_sql] * len(records))
    query = f"INSERT INTO notes_objects ({', '.join(cols)}) VALUES {values_sql}"
    params = [row[c] for row in records for c in cols]
    return execute(query, params)


def list_page_objects(page_id: str) -> List[Dict[str, Any]]:
    return fetch_all(
        "SELECT id,object_type,z_index,transform,payload,asset_id,version,updated_at "
        "FROM notes_objects WHERE page_id=%s ORDER BY z_index",
        (page_id,),
    )


def create_asset(asset: Dict[str, Any]) -> Dict[str, Any]:
    return insert_returning("notes_assets", asset)


def get_owned_asset(asset_id: str, owner_id: int) -> Optional[Dict[str, Any]]:
    return fetch_one("SELECT * FROM notes_assets WHERE id=%s AND owner_id=%s LIMIT 1", (asset_id, owner_id))


def get_asset(asset_id: str) -> Optional[Dict[str, Any]]:
    return fetch_one("SELECT * FROM notes_assets WHERE id=%s LIMIT 1", (asset_id,))


def get_asset_by_storage_path(storage_path: str) -> Optional[Dict[str, Any]]:
    return fetch_one("SELECT * FROM notes_assets WHERE storage_path=%s LIMIT 1", (storage_path,))


def get_assets_by_ids(asset_ids: List[str]) -> List[Dict[str, Any]]:
    """Batch asset lookup so a page with many images costs one DB round
    trip instead of one per image. Used to resolve fresh signed URLs on
    page load — see notes_service._refresh_image_urls()."""
    if not asset_ids:
        return []
    return fetch_all(
        "SELECT id,storage_path,content_type,original_filename FROM notes_assets WHERE id = ANY(%s::uuid[])",
        (list(set(asset_ids)),),
    )


def list_notebook_assets(notebook_id: str) -> List[Dict[str, Any]]:
    return fetch_all("SELECT * FROM notes_assets WHERE notebook_id=%s", (notebook_id,))


def asset_still_referenced(asset_id: str) -> bool:
    return bool(fetch_one("SELECT 1 FROM notes_objects WHERE asset_id=%s LIMIT 1", (asset_id,)))


def delete_assets_by_ids(asset_ids: List[str]) -> None:
    if asset_ids:
        execute("DELETE FROM notes_assets WHERE id = ANY(%s::uuid[])", (asset_ids,))


def delete_notebook_hard(notebook_id: str) -> None:
    """Explicit cascading delete used ONLY to roll back a failed import that
    already created a notebook/pages/objects/assets. Deletes children before
    parents regardless of what the FK ON DELETE behavior actually turns out
    to be — if a cascade already handles a table, the matching delete here
    is simply a no-op.
    This is deliberately separate from the trash/soft-delete flow: it is not
    reachable from any route, only from notes_service.import_notebook()'s
    rollback path.
    """
    page_ids = [row["id"] for row in fetch_all("SELECT id FROM notes_pages WHERE notebook_id=%s", (notebook_id,))]
    if page_ids:
        execute("DELETE FROM notes_objects WHERE page_id = ANY(%s::uuid[])", (page_ids,))
    execute("DELETE FROM notes_assets WHERE notebook_id=%s", (notebook_id,))
    if page_ids:
        execute("DELETE FROM notes_pages WHERE notebook_id=%s", (notebook_id,))
    execute("DELETE FROM notes_notebooks WHERE id=%s", (notebook_id,))


def permanently_delete_trashed_notebook(notebook_id: str, owner_id: int) -> bool:
    rows = execute_returning(
        "DELETE FROM notes_notebooks WHERE id=%s AND owner_id=%s AND deleted_at IS NOT NULL RETURNING id",
        (notebook_id, owner_id),
    )
    return bool(rows)


def list_expired_trashed_notebooks(cutoff: str) -> List[Dict[str, Any]]:
    return fetch_all(
        "SELECT id,owner_id,deleted_at FROM notes_notebooks WHERE deleted_at IS NOT NULL AND deleted_at < %s",
        (cutoff,),
    )


def permanently_delete_expired_notebook(notebook_id: str) -> bool:
    rows = execute_returning(
        "DELETE FROM notes_notebooks WHERE id=%s AND deleted_at IS NOT NULL RETURNING id", (notebook_id,)
    )
    return bool(rows)


def export_notebook(notebook_id: str) -> Dict[str, Any]:
    notebook = fetch_one("SELECT * FROM notes_notebooks WHERE id=%s LIMIT 1", (notebook_id,))
    pages = list_pages(notebook_id)
    for page in pages:
        objects = list_page_objects(page["id"])
        for obj in objects:
            # Legacy rows saved before asset_id was backfilled onto every object
            # only carry the reference inside payload.fabric.assetId. Recover it
            # here so an export always stays importable by its own importer,
            # mirroring the same fallback notes_service._refresh_image_urls()
            # already applies for the live editor's page-load path.
            if obj["object_type"] == "image" and not obj.get("asset_id"):
                fabric = (obj.get("payload") or {}).get("fabric")
                if isinstance(fabric, dict) and fabric.get("assetId"):
                    obj["asset_id"] = fabric["assetId"]
        page["objects"] = objects
    return {
        "format": "smartai-notes-export-v1",
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "notebook": notebook,
        "pages": pages,
        "assets": list_notebook_assets(notebook_id),
    }


def get_public_notebook(notebook_id: str) -> Optional[Dict[str, Any]]:
    return fetch_one(
        "SELECT * FROM notes_notebooks WHERE id=%s AND visibility=%s "
        "AND published_at IS NOT NULL AND deleted_at IS NULL LIMIT 1",
        (notebook_id, "public"),
    )


def search_public_notebooks(term: str, limit: int = 24, offset: int = 0) -> List[Dict[str, Any]]:
    """Fetches limit+1 rows so the caller can tell if there's another page
    without a separate COUNT(*) query — trim the extra row before display."""
    query = (
        "SELECT id,title,description,subject,department,semester,course,topic,tags,"
        "author_display_name,author_deleted,published_at,updated_at FROM notes_notebooks "
        "WHERE visibility=%s AND published_at IS NOT NULL AND deleted_at IS NULL"
    )
    params: List[Any] = ["public"]
    if term:
        pattern = f"%{term}%"
        query += (
            " AND (title ILIKE %s OR description ILIKE %s OR subject ILIKE %s "
            "OR course ILIKE %s OR topic ILIKE %s OR author_display_name ILIKE %s)"
        )
        params += [pattern] * 6
    query += " ORDER BY published_at DESC, id DESC LIMIT %s OFFSET %s"
    params += [limit + 1, offset]
    return fetch_all(query, params)


def toggle_engagement(table: str, notebook_id: str, user_id: int) -> bool:
    existing = fetch_one(
        f"SELECT notebook_id FROM {table} WHERE notebook_id=%s AND user_id=%s LIMIT 1", (notebook_id, user_id)
    )
    if existing:
        execute(f"DELETE FROM {table} WHERE notebook_id=%s AND user_id=%s", (notebook_id, user_id))
        return False
    insert_returning(table, {"notebook_id": notebook_id, "user_id": user_id})
    return True


def engagement_counts(notebook_id: str) -> Dict[str, int]:
    likes = fetch_one("SELECT COUNT(*) AS count FROM notes_likes WHERE notebook_id=%s", (notebook_id,))
    bookmarks = fetch_one("SELECT COUNT(*) AS count FROM notes_bookmarks WHERE notebook_id=%s", (notebook_id,))
    return {"likes": likes["count"] if likes else 0, "bookmarks": bookmarks["count"] if bookmarks else 0}


def get_user_engagement_flags(user_id: int, notebook_ids: List[str]) -> Dict[str, Dict[str, bool]]:
    """Per-user liked/bookmarked state for a set of public notebooks, so the
    library page can render correct initial icon state instead of just counts."""
    if not notebook_ids:
        return {}
    liked = {
        row["notebook_id"] for row in fetch_all(
            "SELECT notebook_id FROM notes_likes WHERE user_id=%s AND notebook_id = ANY(%s::uuid[])",
            (user_id, notebook_ids),
        )
    }
    bookmarked = {
        row["notebook_id"] for row in fetch_all(
            "SELECT notebook_id FROM notes_bookmarks WHERE user_id=%s AND notebook_id = ANY(%s::uuid[])",
            (user_id, notebook_ids),
        )
    }
    return {nid: {"liked": nid in liked, "bookmarked": nid in bookmarked} for nid in notebook_ids}
