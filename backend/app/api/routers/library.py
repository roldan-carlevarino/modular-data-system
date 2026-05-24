"""
Mini-Zotero: papers, books and competitions with metadata, links, notes,
highlights, tags, collections and PDF storage on Backblaze B2.

Item types
----------
- paper        : DOI / arXiv-backed academic items
- book         : ISBN-backed books
- competition  : multi-link items (IMC Prosperity, Kaggle, etc.)
                 with a richer status workflow:
                 wishlist | upcoming | active | submitted | done | abandoned

Status field accepts any string but the UI exposes:
- paper/book : wishlist | reading | done | archived
- competition: wishlist | upcoming | active | submitted | done | abandoned
"""

import json
import mimetypes
import os
import re
import time
import urllib.parse
import urllib.request
from datetime import date, datetime
from typing import Optional, Tuple
from xml.etree import ElementTree as ET

import psycopg2
import psycopg2.extras
from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile

from routers.media import _get_b2

router = APIRouter(prefix="/library", tags=["Library"])


VALID_TYPES = {"paper", "book", "competition"}
VALID_STATUSES = {
    "wishlist", "reading", "active", "upcoming",
    "submitted", "done", "archived", "abandoned",
}


# ---------- Helpers ----------

def _conn():
    return psycopg2.connect(os.getenv("TASKS_URL"), sslmode="require")


def _row_to_item(row, tags=None, collections=None, links=None, notes_count=0, highlights_count=0):
    return {
        "id": row["id"],
        "type": row["type"],
        "title": row["title"],
        "year": row["year"],
        "status": row["status"],
        "authors": row["authors"] or [],
        "external_id": row["external_id"],
        "primary_url": row["primary_url"],
        "file_path": row["file_path"],
        "summary": row["summary"],
        "metadata": row["metadata"] or {},
        "start_date": row["start_date"].isoformat() if row.get("start_date") else None,
        "due_date": row["due_date"].isoformat() if row.get("due_date") else None,
        "added_at": row["added_at"].isoformat() if row["added_at"] else None,
        "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
        "tags": tags or [],
        "collections": collections or [],
        "links": links or [],
        "notes_count": notes_count,
        "highlights_count": highlights_count,
    }


def _safe_filename(name: str) -> str:
    """Strip path separators and odd chars from an upload filename."""
    name = (name or "file").replace("\\", "/").split("/")[-1]
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_")
    return name or "file"


def _parse_date(val, field: str) -> Optional[date]:
    """Accept None, '' (clears), 'YYYY-MM-DD' or ISO datetime."""
    if val is None or val == "":
        return None
    if isinstance(val, date) and not isinstance(val, datetime):
        return val
    try:
        return datetime.strptime(str(val)[:10], "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(400, f"{field} must be YYYY-MM-DD")


# ---------- Items: list / create / read / update / delete ----------

@router.get("/items")
def list_items(
    type: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    tag: Optional[str] = Query(None),
    collection_id: Optional[int] = Query(None),
    project_id: Optional[int] = Query(None),
    due_before: Optional[str] = Query(None),
    due_after: Optional[str] = Query(None),
    sort: Optional[str] = Query(None, description="updated|added|due|due_asc|due_desc|title"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    where = []
    params = []

    if type:
        if type not in VALID_TYPES:
            raise HTTPException(400, f"Invalid type. Allowed: {sorted(VALID_TYPES)}")
        where.append("i.type = %s")
        params.append(type)
    if status:
        where.append("i.status = %s")
        params.append(status)
    if q:
        where.append("""(
            to_tsvector('english',
                coalesce(i.title, '') || ' ' ||
                coalesce(i.summary, '') || ' ' ||
                coalesce(i.authors::text, '')
            ) @@ plainto_tsquery('english', %s)
            OR i.title ILIKE %s
        )""")
        params.extend([q, f"%{q}%"])
    if tag:
        where.append("EXISTS (SELECT 1 FROM lib_tag t WHERE t.item_id = i.id AND t.tag = %s)")
        params.append(tag)
    if collection_id is not None:
        where.append("EXISTS (SELECT 1 FROM lib_item_collection ic WHERE ic.item_id = i.id AND ic.collection_id = %s)")
        params.append(collection_id)
    if project_id is not None:
        where.append("""EXISTS (
            SELECT 1 FROM lib_item_collection ic
            JOIN lib_collection c ON c.id = ic.collection_id
            WHERE ic.item_id = i.id AND c.project_id = %s
        )""")
        params.append(project_id)
    if due_before:
        d = _parse_date(due_before, "due_before")
        where.append("i.due_date IS NOT NULL AND i.due_date <= %s")
        params.append(d)
    if due_after:
        d = _parse_date(due_after, "due_after")
        where.append("i.due_date IS NOT NULL AND i.due_date >= %s")
        params.append(d)

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    order_sql = "ORDER BY i.updated_at DESC"
    if sort in ("due", "due_asc"):
        order_sql = "ORDER BY i.due_date ASC NULLS LAST, i.updated_at DESC"
    elif sort == "due_desc":
        order_sql = "ORDER BY i.due_date DESC NULLS LAST, i.updated_at DESC"
    elif sort == "added":
        order_sql = "ORDER BY i.added_at DESC"
    elif sort == "title":
        order_sql = "ORDER BY i.title ASC"

    sql = f"""
        SELECT
            i.*,
            COALESCE(
                (SELECT json_agg(t.tag ORDER BY t.tag) FROM lib_tag t WHERE t.item_id = i.id),
                '[]'::json
            ) AS tags,
            COALESCE(
                (SELECT json_agg(json_build_object('id', c.id, 'name', c.name)
                                 ORDER BY c.name)
                 FROM lib_item_collection ic
                 JOIN lib_collection c ON c.id = ic.collection_id
                 WHERE ic.item_id = i.id),
                '[]'::json
            ) AS collections,
            (SELECT COUNT(*) FROM lib_note n WHERE n.item_id = i.id) AS notes_count,
            (SELECT COUNT(*) FROM lib_highlight h WHERE h.item_id = i.id) AS highlights_count
        FROM lib_item i
        {where_sql}
        {order_sql}
        LIMIT %s OFFSET %s
    """
    params.extend([limit, offset])

    conn = _conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute(sql, params)
        rows = cur.fetchall()
    finally:
        cur.close()
        conn.close()

    return [
        _row_to_item(
            r,
            tags=r["tags"],
            collections=r["collections"],
            notes_count=r["notes_count"],
            highlights_count=r["highlights_count"],
        )
        for r in rows
    ]


@router.post("/items")
def create_item(payload: dict):
    item_type = (payload.get("type") or "").strip()
    title = (payload.get("title") or "").strip()
    if item_type not in VALID_TYPES:
        raise HTTPException(400, f"type must be one of {sorted(VALID_TYPES)}")
    if not title:
        raise HTTPException(400, "title is required")

    status = (payload.get("status") or "wishlist").strip()
    year = payload.get("year")
    if year is not None:
        try:
            year = int(year)
        except (TypeError, ValueError):
            raise HTTPException(400, "year must be an integer")
    authors = payload.get("authors") or []
    metadata = payload.get("metadata") or {}
    tags = payload.get("tags") or []
    collection_ids = payload.get("collection_ids") or []
    links = payload.get("links") or []
    start_date = _parse_date(payload.get("start_date"), "start_date")
    due_date = _parse_date(payload.get("due_date"), "due_date")

    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO lib_item (type, title, year, status, authors, external_id,
                                  primary_url, file_path, summary, metadata,
                                  start_date, due_date)
            VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s::jsonb, %s, %s)
            RETURNING id
        """, (
            item_type, title, year, status, json.dumps(authors),
            payload.get("external_id"), payload.get("primary_url"),
            payload.get("file_path"), payload.get("summary"),
            json.dumps(metadata), start_date, due_date,
        ))
        new_id = cur.fetchone()[0]

        for tag in tags:
            tag = (tag or "").strip()
            if tag:
                cur.execute(
                    "INSERT INTO lib_tag (item_id, tag) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                    (new_id, tag),
                )
        for cid in collection_ids:
            cur.execute(
                "INSERT INTO lib_item_collection (item_id, collection_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (new_id, int(cid)),
            )
        for idx, link in enumerate(links):
            url = (link.get("url") or "").strip()
            label = (link.get("label") or url).strip()
            if not url:
                continue
            cur.execute("""
                INSERT INTO lib_link (item_id, label, url, kind, sort_order)
                VALUES (%s, %s, %s, %s, %s)
            """, (new_id, label, url, link.get("kind") or "main", idx))

        conn.commit()
        return {"id": new_id}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, f"Failed to create item: {e}")
    finally:
        cur.close()
        conn.close()


@router.get("/items/{item_id}")
def get_item(item_id: int):
    conn = _conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute("SELECT * FROM lib_item WHERE id = %s", (item_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Item not found")
        cur.execute("SELECT tag FROM lib_tag WHERE item_id = %s ORDER BY tag", (item_id,))
        tags = [r["tag"] for r in cur.fetchall()]
        cur.execute("""
            SELECT c.id, c.name FROM lib_collection c
            JOIN lib_item_collection ic ON ic.collection_id = c.id
            WHERE ic.item_id = %s ORDER BY c.name
        """, (item_id,))
        collections = [dict(r) for r in cur.fetchall()]
        cur.execute("""
            SELECT id, label, url, kind, sort_order
            FROM lib_link WHERE item_id = %s
            ORDER BY sort_order, id
        """, (item_id,))
        links = [dict(r) for r in cur.fetchall()]
        cur.execute("SELECT COUNT(*) AS c FROM lib_note WHERE item_id = %s", (item_id,))
        notes_count = cur.fetchone()["c"]
        cur.execute("SELECT COUNT(*) AS c FROM lib_highlight WHERE item_id = %s", (item_id,))
        highlights_count = cur.fetchone()["c"]
    finally:
        cur.close()
        conn.close()
    return _row_to_item(row, tags, collections, links, notes_count, highlights_count)


@router.patch("/items/{item_id}")
def update_item(item_id: int, payload: dict):
    fields = []
    params = []
    for key in ("title", "year", "status", "external_id", "primary_url", "file_path", "summary"):
        if key in payload:
            val = payload[key]
            if key == "year" and val is not None:
                try:
                    val = int(val)
                except (TypeError, ValueError):
                    raise HTTPException(400, "year must be an integer")
            fields.append(f"{key} = %s")
            params.append(val)
    for key in ("start_date", "due_date"):
        if key in payload:
            fields.append(f"{key} = %s")
            params.append(_parse_date(payload[key], key))
    if "type" in payload:
        if payload["type"] not in VALID_TYPES:
            raise HTTPException(400, f"type must be one of {sorted(VALID_TYPES)}")
        fields.append("type = %s")
        params.append(payload["type"])
    if "authors" in payload:
        fields.append("authors = %s::jsonb")
        params.append(json.dumps(payload["authors"] or []))
    if "metadata" in payload:
        fields.append("metadata = %s::jsonb")
        params.append(json.dumps(payload["metadata"] or {}))

    if not fields and "tags" not in payload and "collection_ids" not in payload:
        raise HTTPException(400, "No fields to update")

    conn = _conn()
    cur = conn.cursor()
    try:
        if fields:
            fields.append("updated_at = NOW()")
            params.append(item_id)
            cur.execute(f"UPDATE lib_item SET {', '.join(fields)} WHERE id = %s", params)
            if cur.rowcount == 0:
                raise HTTPException(404, "Item not found")

        if "tags" in payload:
            cur.execute("DELETE FROM lib_tag WHERE item_id = %s", (item_id,))
            for tag in payload["tags"] or []:
                tag = (tag or "").strip()
                if tag:
                    cur.execute(
                        "INSERT INTO lib_tag (item_id, tag) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                        (item_id, tag),
                    )

        if "collection_ids" in payload:
            cur.execute("DELETE FROM lib_item_collection WHERE item_id = %s", (item_id,))
            for cid in payload["collection_ids"] or []:
                cur.execute(
                    "INSERT INTO lib_item_collection (item_id, collection_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                    (item_id, int(cid)),
                )

        conn.commit()
        return {"ok": True}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, f"Failed to update item: {e}")
    finally:
        cur.close()
        conn.close()


@router.delete("/items/{item_id}")
def delete_item(item_id: int):
    """Deletes the item row. The associated B2 PDF (if any) is left in the bucket
    intentionally — call /library/items/{id}/file with DELETE first if you want
    the file removed too."""
    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM lib_item WHERE id = %s", (item_id,))
        if cur.rowcount == 0:
            raise HTTPException(404, "Item not found")
        conn.commit()
        return {"ok": True}
    finally:
        cur.close()
        conn.close()


# ---------- Links (multi-URL items, esp. competitions) ----------

@router.post("/items/{item_id}/links")
def add_link(item_id: int, payload: dict):
    url = (payload.get("url") or "").strip()
    if not url:
        raise HTTPException(400, "url is required")
    label = (payload.get("label") or url).strip()
    kind = payload.get("kind") or "main"

    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT 1 FROM lib_item WHERE id = %s", (item_id,))
        if not cur.fetchone():
            raise HTTPException(404, "Item not found")
        cur.execute(
            "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM lib_link WHERE item_id = %s",
            (item_id,),
        )
        next_order = cur.fetchone()[0]
        cur.execute("""
            INSERT INTO lib_link (item_id, label, url, kind, sort_order)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id
        """, (item_id, label, url, kind, next_order))
        new_id = cur.fetchone()[0]
        conn.commit()
        return {"id": new_id}
    finally:
        cur.close()
        conn.close()


@router.patch("/links/{link_id}")
def update_link(link_id: int, payload: dict):
    fields = []
    params = []
    for key in ("label", "url", "kind", "sort_order"):
        if key in payload:
            fields.append(f"{key} = %s")
            params.append(payload[key])
    if not fields:
        raise HTTPException(400, "No fields to update")
    params.append(link_id)
    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute(f"UPDATE lib_link SET {', '.join(fields)} WHERE id = %s", params)
        if cur.rowcount == 0:
            raise HTTPException(404, "Link not found")
        conn.commit()
        return {"ok": True}
    finally:
        cur.close()
        conn.close()


@router.delete("/links/{link_id}")
def delete_link(link_id: int):
    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM lib_link WHERE id = %s", (link_id,))
        if cur.rowcount == 0:
            raise HTTPException(404, "Link not found")
        conn.commit()
        return {"ok": True}
    finally:
        cur.close()
        conn.close()


# ---------- Collections ----------

@router.get("/collections")
def list_collections():
    conn = _conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute("""
            SELECT c.id, c.name, c.parent_id, c.color, c.project_id,
                (SELECT name FROM projects p WHERE p.id = c.project_id) AS project_name,
                (SELECT COUNT(*) FROM lib_item_collection ic WHERE ic.collection_id = c.id) AS item_count
            FROM lib_collection c
            ORDER BY c.name
        """)
        return [dict(r) for r in cur.fetchall()]
    finally:
        cur.close()
        conn.close()


@router.get("/projects")
def list_projects_for_library():
    """Lightweight project list for the library collection selector."""
    conn = _conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute("SELECT id, name FROM projects ORDER BY name")
        return [dict(r) for r in cur.fetchall()]
    finally:
        cur.close()
        conn.close()


@router.post("/collections")
def create_collection(payload: dict):
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "name is required")
    parent_id = payload.get("parent_id")
    color = payload.get("color")
    project_id = payload.get("project_id")
    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO lib_collection (name, parent_id, color, project_id) VALUES (%s, %s, %s, %s) RETURNING id",
            (name, parent_id, color, project_id),
        )
        new_id = cur.fetchone()[0]
        conn.commit()
        return {"id": new_id}
    finally:
        cur.close()
        conn.close()


@router.patch("/collections/{cid}")
def update_collection(cid: int, payload: dict):
    fields, params = [], []
    for key in ("name", "parent_id", "color", "project_id"):
        if key in payload:
            fields.append(f"{key} = %s")
            params.append(payload[key])
    if not fields:
        raise HTTPException(400, "No fields to update")
    params.append(cid)
    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute(f"UPDATE lib_collection SET {', '.join(fields)} WHERE id = %s", params)
        if cur.rowcount == 0:
            raise HTTPException(404, "Collection not found")
        conn.commit()
        return {"ok": True}
    finally:
        cur.close()
        conn.close()


@router.delete("/collections/{cid}")
def delete_collection(cid: int):
    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM lib_collection WHERE id = %s", (cid,))
        if cur.rowcount == 0:
            raise HTTPException(404, "Collection not found")
        conn.commit()
        return {"ok": True}
    finally:
        cur.close()
        conn.close()


# ---------- Notes ----------

@router.get("/items/{item_id}/notes")
def list_notes(item_id: int):
    conn = _conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute("""
            SELECT id, body_md, created_at, updated_at
            FROM lib_note WHERE item_id = %s
            ORDER BY updated_at DESC
        """, (item_id,))
        rows = cur.fetchall()
        return [{
            "id": r["id"],
            "body_md": r["body_md"],
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
        } for r in rows]
    finally:
        cur.close()
        conn.close()


@router.post("/items/{item_id}/notes")
def create_note(item_id: int, payload: dict):
    body = payload.get("body_md") or ""
    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT 1 FROM lib_item WHERE id = %s", (item_id,))
        if not cur.fetchone():
            raise HTTPException(404, "Item not found")
        cur.execute(
            "INSERT INTO lib_note (item_id, body_md) VALUES (%s, %s) RETURNING id",
            (item_id, body),
        )
        new_id = cur.fetchone()[0]
        conn.commit()
        return {"id": new_id}
    finally:
        cur.close()
        conn.close()


@router.patch("/notes/{note_id}")
def update_note(note_id: int, payload: dict):
    if "body_md" not in payload:
        raise HTTPException(400, "body_md required")
    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute(
            "UPDATE lib_note SET body_md = %s, updated_at = NOW() WHERE id = %s",
            (payload["body_md"], note_id),
        )
        if cur.rowcount == 0:
            raise HTTPException(404, "Note not found")
        conn.commit()
        return {"ok": True}
    finally:
        cur.close()
        conn.close()


@router.delete("/notes/{note_id}")
def delete_note(note_id: int):
    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM lib_note WHERE id = %s", (note_id,))
        if cur.rowcount == 0:
            raise HTTPException(404, "Note not found")
        conn.commit()
        return {"ok": True}
    finally:
        cur.close()
        conn.close()


# ---------- Highlights ----------

@router.get("/items/{item_id}/highlights")
def list_highlights(item_id: int):
    conn = _conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute("""
            SELECT id, locator, quote, comment, color, created_at
            FROM lib_highlight WHERE item_id = %s
            ORDER BY created_at DESC
        """, (item_id,))
        return [{
            "id": r["id"],
            "locator": r["locator"],
            "quote": r["quote"],
            "comment": r["comment"],
            "color": r["color"],
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        } for r in cur.fetchall()]
    finally:
        cur.close()
        conn.close()


@router.post("/items/{item_id}/highlights")
def create_highlight(item_id: int, payload: dict):
    quote = (payload.get("quote") or "").strip()
    if not quote:
        raise HTTPException(400, "quote is required")
    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT 1 FROM lib_item WHERE id = %s", (item_id,))
        if not cur.fetchone():
            raise HTTPException(404, "Item not found")
        cur.execute("""
            INSERT INTO lib_highlight (item_id, locator, quote, comment, color)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id
        """, (item_id, payload.get("locator"), quote,
              payload.get("comment"), payload.get("color")))
        new_id = cur.fetchone()[0]
        conn.commit()
        return {"id": new_id}
    finally:
        cur.close()
        conn.close()


@router.delete("/highlights/{hid}")
def delete_highlight(hid: int):
    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM lib_highlight WHERE id = %s", (hid,))
        if cur.rowcount == 0:
            raise HTTPException(404, "Highlight not found")
        conn.commit()
        return {"ok": True}
    finally:
        cur.close()
        conn.close()


# ---------- Tags (lookup helper for the UI) ----------

@router.get("/tags")
def list_tags():
    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT tag, COUNT(*) AS n FROM lib_tag
            GROUP BY tag ORDER BY n DESC, tag
        """)
        return [{"tag": r[0], "count": r[1]} for r in cur.fetchall()]
    finally:
        cur.close()
        conn.close()


# ---------- PDF storage on B2 ----------

@router.post("/items/{item_id}/file")
async def upload_file(item_id: int, file: UploadFile = File(...)):
    """Upload a PDF (or any binary) for the item. Stored under
    library/{item_id}/{ts}_{name} in the bucket; sets lib_item.file_path."""
    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT type FROM lib_item WHERE id = %s", (item_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Item not found")

        api, bucket = _get_b2()
        content = await file.read()
        if not content:
            raise HTTPException(400, "Empty file")
        safe = _safe_filename(file.filename)
        b2_path = f"library/{item_id}/{int(time.time())}_{safe}"
        content_type = (
            file.content_type
            or mimetypes.guess_type(file.filename or "")[0]
            or "application/octet-stream"
        )
        bucket.upload_bytes(
            data_bytes=content,
            file_name=b2_path,
            content_type=content_type,
        )
        cur.execute(
            "UPDATE lib_item SET file_path = %s, updated_at = NOW() WHERE id = %s",
            (b2_path, item_id),
        )
        conn.commit()
        return {"path": b2_path, "size": len(content), "content_type": content_type}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, f"Upload failed: {e}")
    finally:
        cur.close()
        conn.close()


@router.get("/items/{item_id}/file-url")
def get_file_url(item_id: int):
    """Returns a 1-hour signed URL for the item's PDF."""
    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT file_path FROM lib_item WHERE id = %s", (item_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Item not found")
        file_path = row[0]
        if not file_path:
            raise HTTPException(404, "Item has no file attached")
    finally:
        cur.close()
        conn.close()

    api, bucket = _get_b2()
    token = bucket.get_download_authorization(
        file_name_prefix=file_path,
        valid_duration_in_seconds=3600,
    )
    base_url = api.get_download_url_for_file_name(bucket.name, file_path)
    return {"url": f"{base_url}?Authorization={token}", "expires_in": 3600}


@router.delete("/items/{item_id}/file")
def delete_file(item_id: int):
    """Removes the PDF from B2 and clears file_path."""
    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT file_path FROM lib_item WHERE id = %s", (item_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Item not found")
        file_path = row[0]
        if file_path:
            try:
                _, bucket = _get_b2()
                file_version = bucket.get_file_info_by_name(file_path)
                bucket.delete_file_version(file_version.id_, file_path)
            except Exception:
                # Best-effort: if B2 delete fails, still clear DB pointer
                pass
        cur.execute(
            "UPDATE lib_item SET file_path = NULL, updated_at = NOW() WHERE id = %s",
            (item_id,),
        )
        conn.commit()
        return {"ok": True}
    finally:
        cur.close()
        conn.close()


# ---------- Stats / overview ----------

@router.get("/stats")
def stats():
    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT type, status, COUNT(*) FROM lib_item
            GROUP BY type, status ORDER BY type, status
        """)
        by_type_status = [
            {"type": r[0], "status": r[1], "count": r[2]} for r in cur.fetchall()
        ]
        cur.execute("SELECT COUNT(*) FROM lib_item")
        total = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM lib_item WHERE file_path IS NOT NULL")
        with_files = cur.fetchone()[0]
    finally:
        cur.close()
        conn.close()
    return {"total": total, "with_files": with_files, "by_type_status": by_type_status}


# ---------- Metadata import (DOI / arXiv / ISBN) ----------

_HTTP_HEADERS = {
    "User-Agent": "modular-data-system/1.0 (mailto:contact@example.com)",
    "Accept": "application/json",
}


def _http_get(url: str, accept: str = "application/json", timeout: int = 8) -> bytes:
    req = urllib.request.Request(url, headers={**_HTTP_HEADERS, "Accept": accept})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _detect_kind(value: str) -> Tuple[str, str]:
    """Returns (kind, normalized_value). kind ∈ {'doi','arxiv','isbn'}."""
    v = (value or "").strip()
    if not v:
        raise HTTPException(400, "Empty identifier")

    # arXiv: arxiv:1234.5678 / arXiv:1234.5678v2 / 1234.5678 / cs.LG/0102003
    m = re.match(r"^(?:arxiv:)?([a-z\-]+(?:\.[A-Z]{2})?/\d{7}|\d{4}\.\d{4,5})(v\d+)?$", v, re.I)
    if m:
        return "arxiv", m.group(1)

    # DOI: 10.xxxx/yyyy (optionally prefixed with doi: or https://doi.org/)
    m = re.search(r"(10\.\d{4,9}/[^\s]+)$", v.replace("https://doi.org/", "").replace("doi:", "").strip())
    if m:
        return "doi", m.group(1)

    # ISBN: 10 or 13 digits (allowing dashes/spaces)
    digits = re.sub(r"[^\dXx]", "", v)
    if len(digits) in (10, 13):
        return "isbn", digits

    raise HTTPException(400, f"Could not detect identifier kind for '{value}'")


def _fetch_arxiv(arxiv_id: str) -> dict:
    """Use the public arXiv API (Atom XML)."""
    url = f"http://export.arxiv.org/api/query?id_list={urllib.parse.quote(arxiv_id)}"
    try:
        body = _http_get(url, accept="application/atom+xml")
    except Exception as e:
        raise HTTPException(502, f"arXiv fetch failed: {e}")

    ns = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
    root = ET.fromstring(body)
    entry = root.find("a:entry", ns)
    if entry is None:
        raise HTTPException(404, "arXiv id not found")

    title = (entry.findtext("a:title", default="", namespaces=ns) or "").strip()
    summary = (entry.findtext("a:summary", default="", namespaces=ns) or "").strip()
    published = entry.findtext("a:published", default="", namespaces=ns) or ""
    year = int(published[:4]) if published[:4].isdigit() else None
    authors = []
    for a in entry.findall("a:author", ns):
        name = (a.findtext("a:name", default="", namespaces=ns) or "").strip()
        if name:
            authors.append({"name": name})

    abs_url = None
    pdf_url = None
    for link in entry.findall("a:link", ns):
        href = link.get("href")
        if link.get("title") == "pdf":
            pdf_url = href
        elif link.get("rel") == "alternate":
            abs_url = href

    return {
        "type": "paper",
        "title": title,
        "year": year,
        "authors": authors,
        "summary": summary,
        "external_id": f"arxiv:{arxiv_id}",
        "primary_url": abs_url or f"https://arxiv.org/abs/{arxiv_id}",
        "metadata": {"arxiv_id": arxiv_id, "pdf_url": pdf_url, "source": "arxiv"},
    }


def _fetch_doi(doi: str) -> dict:
    """Use CrossRef REST API."""
    url = f"https://api.crossref.org/works/{urllib.parse.quote(doi)}"
    try:
        body = _http_get(url)
    except Exception as e:
        raise HTTPException(502, f"CrossRef fetch failed: {e}")
    try:
        data = json.loads(body).get("message", {})
    except Exception:
        raise HTTPException(502, "CrossRef returned invalid JSON")

    title_list = data.get("title") or []
    title = (title_list[0] if title_list else "").strip()
    container = data.get("container-title") or []
    venue = container[0] if container else None
    year = None
    issued = data.get("issued", {}).get("date-parts") or [[]]
    if issued and issued[0]:
        try:
            year = int(issued[0][0])
        except (TypeError, ValueError):
            year = None

    authors = []
    for a in data.get("author", []) or []:
        given = (a.get("given") or "").strip()
        family = (a.get("family") or "").strip()
        full = f"{given} {family}".strip() or a.get("name", "").strip()
        if full:
            authors.append({"name": full, "orcid": a.get("ORCID")})

    item_type = "paper"
    crossref_type = data.get("type", "")
    if crossref_type == "book":
        item_type = "book"

    return {
        "type": item_type,
        "title": title,
        "year": year,
        "authors": authors,
        "summary": (data.get("abstract") or "").strip() or None,
        "external_id": f"doi:{doi}",
        "primary_url": data.get("URL") or f"https://doi.org/{doi}",
        "metadata": {
            "doi": doi,
            "venue": venue,
            "publisher": data.get("publisher"),
            "type": crossref_type,
            "source": "crossref",
        },
    }


def _fetch_isbn(isbn: str) -> dict:
    """Use OpenLibrary's books API."""
    url = f"https://openlibrary.org/api/books?bibkeys=ISBN:{isbn}&format=json&jscmd=data"
    try:
        body = _http_get(url)
    except Exception as e:
        raise HTTPException(502, f"OpenLibrary fetch failed: {e}")
    try:
        data = json.loads(body)
    except Exception:
        raise HTTPException(502, "OpenLibrary returned invalid JSON")
    book = data.get(f"ISBN:{isbn}")
    if not book:
        raise HTTPException(404, "ISBN not found in OpenLibrary")

    year = None
    pub_date = book.get("publish_date") or ""
    m = re.search(r"\b(19|20)\d{2}\b", pub_date)
    if m:
        year = int(m.group(0))

    authors = [{"name": a.get("name")} for a in book.get("authors", []) if a.get("name")]
    publishers = [p.get("name") for p in book.get("publishers", []) if p.get("name")]

    return {
        "type": "book",
        "title": (book.get("title") or "").strip(),
        "year": year,
        "authors": authors,
        "summary": book.get("subtitle"),
        "external_id": f"isbn:{isbn}",
        "primary_url": book.get("url"),
        "metadata": {
            "isbn": isbn,
            "publishers": publishers,
            "publish_date": pub_date or None,
            "number_of_pages": book.get("number_of_pages"),
            "cover": (book.get("cover") or {}).get("medium"),
            "source": "openlibrary",
        },
    }


@router.post("/import")
def import_metadata(payload: dict):
    """
    Fetch metadata from DOI / arXiv / ISBN and either return a draft
    or persist it directly.

    Body:
      { "value": "10.1234/abcd" | "arxiv:2305.12345" | "9780131103627",
        "kind": "doi" | "arxiv" | "isbn" | null,   # auto-detect if null
        "save": true | false }                      # default false → returns draft

    Returns:
      - save=false: { "draft": {...} }
      - save=true:  { "id": <new_item_id>, "draft": {...} }
    """
    value = (payload.get("value") or "").strip()
    if not value:
        raise HTTPException(400, "value is required")
    kind = (payload.get("kind") or "").strip().lower() or None

    if kind:
        if kind not in {"doi", "arxiv", "isbn"}:
            raise HTTPException(400, "kind must be doi, arxiv or isbn")
        normalized = value
        if kind == "isbn":
            normalized = re.sub(r"[^\dXx]", "", value)
        elif kind == "doi":
            normalized = value.replace("https://doi.org/", "").replace("doi:", "").strip()
        elif kind == "arxiv":
            normalized = re.sub(r"^arxiv:", "", value, flags=re.I).strip()
    else:
        kind, normalized = _detect_kind(value)

    if kind == "arxiv":
        draft = _fetch_arxiv(normalized)
    elif kind == "doi":
        draft = _fetch_doi(normalized)
    else:
        draft = _fetch_isbn(normalized)

    if not payload.get("save"):
        return {"draft": draft}

    # Persist via the existing create_item path so all defaults apply.
    created = create_item({
        "type": draft["type"],
        "title": draft["title"] or "(untitled)",
        "year": draft.get("year"),
        "status": payload.get("status") or "wishlist",
        "authors": draft.get("authors") or [],
        "external_id": draft.get("external_id"),
        "primary_url": draft.get("primary_url"),
        "summary": draft.get("summary"),
        "metadata": draft.get("metadata") or {},
        "tags": payload.get("tags") or [],
        "collection_ids": payload.get("collection_ids") or [],
    })
    return {"id": created["id"], "draft": draft}
