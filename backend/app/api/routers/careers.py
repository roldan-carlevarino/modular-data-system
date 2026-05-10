"""
Careers: pipeline tracker for internships, new-grad, research positions and grants.

Status workflow (UI columns, in canonical order):
    saved | applied | oa | phone | onsite | offer | accepted | rejected | withdrawn | ghosted

Types: internship | new_grad | research | phd | summer_school | grant
"""

import json
import os
from datetime import date, datetime
from typing import Optional

import psycopg2
import psycopg2.extras
from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/careers", tags=["Careers"])

VALID_TYPES = {"internship", "new_grad", "research", "phd", "summer_school", "grant"}
VALID_STATUSES = [
    "saved", "applied", "oa", "phone", "onsite",
    "offer", "accepted", "rejected", "withdrawn", "ghosted",
]
ACTIVE_STATUSES = {"saved", "applied", "oa", "phone", "onsite", "offer"}


def _conn():
    return psycopg2.connect(os.getenv("TASKS_URL"), sslmode="require")


def _parse_date(val, field: str) -> Optional[date]:
    if val is None or val == "":
        return None
    if isinstance(val, date) and not isinstance(val, datetime):
        return val
    try:
        return datetime.strptime(str(val)[:10], "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(400, f"{field} must be YYYY-MM-DD")


def _row(r):
    return {
        "id": r["id"],
        "type": r["type"],
        "company": r["company"],
        "role": r["role"],
        "location": r["location"],
        "status": r["status"],
        "source": r["source"],
        "applied_at": r["applied_at"].isoformat() if r["applied_at"] else None,
        "deadline": r["deadline"].isoformat() if r["deadline"] else None,
        "start_date": r["start_date"].isoformat() if r["start_date"] else None,
        "end_date": r["end_date"].isoformat() if r["end_date"] else None,
        "salary": r["salary"],
        "url": r["url"],
        "notes": r["notes"],
        "metadata": r["metadata"] or {},
        "sort_order": r["sort_order"],
        "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
    }


def _event_row(r):
    return {
        "id": r["id"],
        "application_id": r["application_id"],
        "kind": r["kind"],
        "title": r["title"],
        "body": r["body"],
        "occurred_at": r["occurred_at"].isoformat() if r["occurred_at"] else None,
        "metadata": r["metadata"] or {},
        "created_at": r["created_at"].isoformat() if r["created_at"] else None,
    }


# Map status transitions to auto-event templates
_STATUS_EVENT_KIND = {
    "applied": "applied",
    "oa": "oa_received",
    "phone": "interview_phone",
    "onsite": "interview_onsite",
    "offer": "offer",
    "accepted": "accepted",
    "rejected": "rejection",
    "withdrawn": "withdrawn",
    "ghosted": "ghosted",
}


def _insert_status_event(cur, app_id: int, new_status: str, prev_status: Optional[str]):
    """Insert an automatic event when status changes (best-effort)."""
    if not new_status or new_status == prev_status:
        return
    kind = _STATUS_EVENT_KIND.get(new_status, "status_change")
    title = f"Status → {new_status}" if not prev_status else f"{prev_status} → {new_status}"
    cur.execute("""
        INSERT INTO career_event (application_id, kind, title, body, metadata)
        VALUES (%s, %s, %s, NULL, %s::jsonb)
    """, (app_id, kind, title, json.dumps({"auto": True, "from": prev_status, "to": new_status})))


# ---------- List / Create / Read / Update / Delete ----------

@router.get("")
def list_applications(
    status: Optional[str] = Query(None),
    type: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    active_only: bool = Query(False),
    deadline_before: Optional[str] = Query(None),
    deadline_after: Optional[str] = Query(None),
    sort: Optional[str] = Query(None, description="updated|deadline|applied|company"),
    limit: int = Query(500, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    where, params = [], []

    if status:
        where.append("status = %s")
        params.append(status)
    if type:
        if type not in VALID_TYPES:
            raise HTTPException(400, f"type must be one of {sorted(VALID_TYPES)}")
        where.append("type = %s")
        params.append(type)
    if active_only:
        placeholders = ", ".join(["%s"] * len(ACTIVE_STATUSES))
        where.append(f"status IN ({placeholders})")
        params.extend(sorted(ACTIVE_STATUSES))
    if q:
        where.append("(company ILIKE %s OR role ILIKE %s OR notes ILIKE %s)")
        like = f"%{q}%"
        params.extend([like, like, like])
    if deadline_before:
        where.append("deadline IS NOT NULL AND deadline <= %s")
        params.append(_parse_date(deadline_before, "deadline_before"))
    if deadline_after:
        where.append("deadline IS NOT NULL AND deadline >= %s")
        params.append(_parse_date(deadline_after, "deadline_after"))

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    order_sql = "ORDER BY sort_order ASC, updated_at DESC"
    if sort == "deadline":
        order_sql = "ORDER BY deadline ASC NULLS LAST, updated_at DESC"
    elif sort == "applied":
        order_sql = "ORDER BY applied_at DESC NULLS LAST"
    elif sort == "company":
        order_sql = "ORDER BY company ASC"
    elif sort == "updated":
        order_sql = "ORDER BY updated_at DESC"

    sql = f"SELECT * FROM career_application {where_sql} {order_sql} LIMIT %s OFFSET %s"
    params.extend([limit, offset])

    conn = _conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute(sql, params)
        rows = cur.fetchall()
    finally:
        cur.close()
        conn.close()
    return [_row(r) for r in rows]


@router.post("")
def create_application(payload: dict):
    company = (payload.get("company") or "").strip()
    role = (payload.get("role") or "").strip()
    if not company or not role:
        raise HTTPException(400, "company and role are required")

    type_ = (payload.get("type") or "internship").strip()
    if type_ not in VALID_TYPES:
        raise HTTPException(400, f"type must be one of {sorted(VALID_TYPES)}")
    status = (payload.get("status") or "saved").strip()

    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO career_application
                (type, company, role, location, status, source, applied_at,
                 deadline, start_date, end_date, salary, url, notes, metadata)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            RETURNING id
        """, (
            type_, company, role,
            payload.get("location"),
            status,
            payload.get("source"),
            _parse_date(payload.get("applied_at"), "applied_at"),
            _parse_date(payload.get("deadline"), "deadline"),
            _parse_date(payload.get("start_date"), "start_date"),
            _parse_date(payload.get("end_date"), "end_date"),
            payload.get("salary"),
            payload.get("url"),
            payload.get("notes"),
            json.dumps(payload.get("metadata") or {}),
        ))
        new_id = cur.fetchone()[0]
        # Initial event
        _insert_status_event(cur, new_id, status, None)
        conn.commit()
        return {"id": new_id}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, f"Failed to create application: {e}")
    finally:
        cur.close()
        conn.close()


@router.get("/{app_id}")
def get_application(app_id: int):
    conn = _conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute("SELECT * FROM career_application WHERE id = %s", (app_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Application not found")
        return _row(row)
    finally:
        cur.close()
        conn.close()


@router.patch("/{app_id}")
def update_application(app_id: int, payload: dict):
    fields, params = [], []

    for key in ("company", "role", "location", "status", "source",
                "salary", "url", "notes"):
        if key in payload:
            fields.append(f"{key} = %s")
            params.append(payload[key])

    if "type" in payload:
        if payload["type"] not in VALID_TYPES:
            raise HTTPException(400, f"type must be one of {sorted(VALID_TYPES)}")
        fields.append("type = %s")
        params.append(payload["type"])

    for key in ("applied_at", "deadline", "start_date", "end_date"):
        if key in payload:
            fields.append(f"{key} = %s")
            params.append(_parse_date(payload[key], key))

    if "metadata" in payload:
        fields.append("metadata = %s::jsonb")
        params.append(json.dumps(payload["metadata"] or {}))

    if "sort_order" in payload:
        try:
            fields.append("sort_order = %s")
            params.append(int(payload["sort_order"]))
        except (TypeError, ValueError):
            raise HTTPException(400, "sort_order must be an integer")

    # Auto-stamp applied_at when transitioning to 'applied' if not provided
    if payload.get("status") == "applied" and "applied_at" not in payload:
        fields.append("applied_at = COALESCE(applied_at, CURRENT_DATE)")

    if not fields:
        raise HTTPException(400, "No fields to update")

    fields.append("updated_at = NOW()")
    params.append(app_id)

    conn = _conn()
    cur = conn.cursor()
    try:
        prev_status = None
        if "status" in payload:
            cur.execute("SELECT status FROM career_application WHERE id = %s", (app_id,))
            row = cur.fetchone()
            if row:
                prev_status = row[0]
        cur.execute(f"UPDATE career_application SET {', '.join(fields)} WHERE id = %s", params)
        if cur.rowcount == 0:
            raise HTTPException(404, "Application not found")
        if "status" in payload and payload["status"] != prev_status:
            _insert_status_event(cur, app_id, payload["status"], prev_status)
        conn.commit()
        return {"ok": True}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, f"Failed to update application: {e}")
    finally:
        cur.close()
        conn.close()


@router.delete("/{app_id}")
def delete_application(app_id: int):
    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM career_application WHERE id = %s", (app_id,))
        if cur.rowcount == 0:
            raise HTTPException(404, "Application not found")
        conn.commit()
        return {"ok": True}
    finally:
        cur.close()
        conn.close()


# ---------- Bulk reorder (kanban drag) ----------

@router.post("/reorder")
def reorder(payload: dict):
    """
    Body: { "items": [{ "id": int, "status": str, "sort_order": int }, ...] }
    Updates status + sort_order in one transaction. Used after kanban drag.
    """
    items = payload.get("items") or []
    if not isinstance(items, list):
        raise HTTPException(400, "items must be a list")

    conn = _conn()
    cur = conn.cursor()
    try:
        for it in items:
            try:
                aid = int(it["id"])
                so = int(it.get("sort_order", 0))
            except (KeyError, TypeError, ValueError):
                raise HTTPException(400, "Each item needs id and sort_order")
            status = it.get("status")
            if status:
                cur.execute("SELECT status FROM career_application WHERE id = %s", (aid,))
                row = cur.fetchone()
                prev_status = row[0] if row else None
                # Auto-stamp applied_at when transitioning to applied
                cur.execute("""
                    UPDATE career_application
                    SET status = %s,
                        sort_order = %s,
                        applied_at = CASE
                            WHEN %s = 'applied' AND applied_at IS NULL
                                THEN CURRENT_DATE ELSE applied_at
                        END,
                        updated_at = NOW()
                    WHERE id = %s
                """, (status, so, status, aid))
                if status != prev_status:
                    _insert_status_event(cur, aid, status, prev_status)
            else:
                cur.execute(
                    "UPDATE career_application SET sort_order = %s, updated_at = NOW() WHERE id = %s",
                    (so, aid),
                )
        conn.commit()
        return {"ok": True, "count": len(items)}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, f"Reorder failed: {e}")
    finally:
        cur.close()
        conn.close()


# ---------- Events (timeline) ----------

@router.get("/{app_id}/events")
def list_events(app_id: int):
    conn = _conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute("""
            SELECT * FROM career_event
            WHERE application_id = %s
            ORDER BY occurred_at DESC, id DESC
        """, (app_id,))
        rows = cur.fetchall()
    finally:
        cur.close()
        conn.close()
    return [_event_row(r) for r in rows]


@router.post("/{app_id}/events")
def create_event(app_id: int, payload: dict):
    kind = (payload.get("kind") or "note").strip() or "note"
    title = payload.get("title")
    body = payload.get("body")
    occurred_at = payload.get("occurred_at")  # ISO datetime or YYYY-MM-DD; NULL → NOW()

    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT 1 FROM career_application WHERE id = %s", (app_id,))
        if not cur.fetchone():
            raise HTTPException(404, "Application not found")

        if occurred_at:
            cur.execute("""
                INSERT INTO career_event (application_id, kind, title, body, occurred_at, metadata)
                VALUES (%s, %s, %s, %s, %s, %s::jsonb) RETURNING id
            """, (app_id, kind, title, body, occurred_at, json.dumps(payload.get("metadata") or {})))
        else:
            cur.execute("""
                INSERT INTO career_event (application_id, kind, title, body, metadata)
                VALUES (%s, %s, %s, %s, %s::jsonb) RETURNING id
            """, (app_id, kind, title, body, json.dumps(payload.get("metadata") or {})))
        new_id = cur.fetchone()[0]
        # Touch application updated_at so it bubbles up in lists
        cur.execute("UPDATE career_application SET updated_at = NOW() WHERE id = %s", (app_id,))
        conn.commit()
        return {"id": new_id}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, f"Failed to create event: {e}")
    finally:
        cur.close()
        conn.close()


@router.delete("/events/{event_id}")
def delete_event(event_id: int):
    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM career_event WHERE id = %s", (event_id,))
        if cur.rowcount == 0:
            raise HTTPException(404, "Event not found")
        conn.commit()
        return {"ok": True}
    finally:
        cur.close()
        conn.close()


# ---------- Stats ----------

@router.get("/stats/summary")
def stats_summary():
    conn = _conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute("""
            SELECT status, COUNT(*) AS n
            FROM career_application
            GROUP BY status
        """)
        by_status = {r["status"]: r["n"] for r in cur.fetchall()}

        cur.execute("SELECT COUNT(*) AS n FROM career_application")
        total = cur.fetchone()["n"]

        cur.execute("""
            SELECT COUNT(*) AS n FROM career_application
            WHERE status IN ('saved','applied','oa','phone','onsite','offer')
        """)
        active = cur.fetchone()["n"]

        cur.execute("""
            SELECT COUNT(*) AS n FROM career_application
            WHERE deadline IS NOT NULL
              AND deadline >= CURRENT_DATE
              AND deadline <= CURRENT_DATE + INTERVAL '14 days'
        """)
        upcoming_deadlines = cur.fetchone()["n"]
    finally:
        cur.close()
        conn.close()

    return {
        "total": total,
        "active": active,
        "by_status": by_status,
        "upcoming_deadlines_14d": upcoming_deadlines,
    }


# ---------- Contacts ----------

VALID_RELATIONSHIPS = {"recruiter", "referral", "interviewer", "hiring_manager", "peer", "other"}


def _contact_row(r):
    return {
        "id": r["id"],
        "application_id": r["application_id"],
        "name": r["name"],
        "role": r["role"],
        "email": r["email"],
        "phone": r["phone"],
        "linkedin": r["linkedin"],
        "relationship": r["relationship"],
        "notes": r["notes"],
        "metadata": r["metadata"] or {},
        "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
    }


@router.get("/{app_id}/contacts")
def list_contacts(app_id: int):
    conn = _conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute("""
            SELECT * FROM career_contact
            WHERE application_id = %s
            ORDER BY created_at ASC
        """, (app_id,))
        return [_contact_row(r) for r in cur.fetchall()]
    finally:
        cur.close()
        conn.close()


@router.post("/{app_id}/contacts")
def create_contact(app_id: int, payload: dict):
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "name is required")
    relationship = (payload.get("relationship") or "recruiter").strip()
    if relationship not in VALID_RELATIONSHIPS:
        raise HTTPException(400, f"relationship must be one of {sorted(VALID_RELATIONSHIPS)}")

    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT 1 FROM career_application WHERE id = %s", (app_id,))
        if not cur.fetchone():
            raise HTTPException(404, "Application not found")

        cur.execute("""
            INSERT INTO career_contact
                (application_id, name, role, email, phone, linkedin, relationship, notes, metadata)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            RETURNING id
        """, (
            app_id, name,
            payload.get("role"),
            payload.get("email"),
            payload.get("phone"),
            payload.get("linkedin"),
            relationship,
            payload.get("notes"),
            json.dumps(payload.get("metadata") or {}),
        ))
        new_id = cur.fetchone()[0]
        cur.execute("UPDATE career_application SET updated_at = NOW() WHERE id = %s", (app_id,))
        conn.commit()
        return {"id": new_id}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, f"Failed to create contact: {e}")
    finally:
        cur.close()
        conn.close()


@router.patch("/contacts/{contact_id}")
def update_contact(contact_id: int, payload: dict):
    fields = []
    params = []
    for col in ("name", "role", "email", "phone", "linkedin", "notes"):
        if col in payload:
            val = payload[col]
            if col == "name" and (val is None or not str(val).strip()):
                raise HTTPException(400, "name cannot be empty")
            fields.append(f"{col} = %s")
            params.append(val)
    if "relationship" in payload:
        rel = (payload["relationship"] or "").strip()
        if rel not in VALID_RELATIONSHIPS:
            raise HTTPException(400, f"relationship must be one of {sorted(VALID_RELATIONSHIPS)}")
        fields.append("relationship = %s")
        params.append(rel)
    if "metadata" in payload:
        fields.append("metadata = %s::jsonb")
        params.append(json.dumps(payload["metadata"] or {}))

    if not fields:
        raise HTTPException(400, "no fields to update")

    fields.append("updated_at = NOW()")
    params.append(contact_id)

    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute(f"""
            UPDATE career_contact SET {", ".join(fields)}
            WHERE id = %s
            RETURNING application_id
        """, params)
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Contact not found")
        cur.execute("UPDATE career_application SET updated_at = NOW() WHERE id = %s", (row[0],))
        conn.commit()
        return {"ok": True}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, f"Failed to update contact: {e}")
    finally:
        cur.close()
        conn.close()


@router.delete("/contacts/{contact_id}")
def delete_contact(contact_id: int):
    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM career_contact WHERE id = %s", (contact_id,))
        if cur.rowcount == 0:
            raise HTTPException(404, "Contact not found")
        conn.commit()
        return {"ok": True}
    finally:
        cur.close()
        conn.close()


# ---------- Intel widgets ----------

@router.get("/intel/widgets")
def intel_widgets(deadline_days: int = Query(14, ge=1, le=90),
                  stale_days: int = Query(14, ge=1, le=180)):
    """Aggregated data for the Intel dashboard widgets."""
    conn = _conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        # Pipeline counts
        cur.execute("""
            SELECT status, COUNT(*) AS n
            FROM career_application
            GROUP BY status
        """)
        by_status = {r["status"]: r["n"] for r in cur.fetchall()}
        active = sum(by_status.get(s, 0) for s in ACTIVE_STATUSES)

        # Upcoming deadlines (active only)
        cur.execute("""
            SELECT id, company, role, status, type, deadline
            FROM career_application
            WHERE deadline IS NOT NULL
              AND deadline >= CURRENT_DATE
              AND deadline <= CURRENT_DATE + (%s || ' days')::interval
              AND status IN ('saved','applied','oa','phone','onsite','offer')
            ORDER BY deadline ASC
            LIMIT 20
        """, (deadline_days,))
        deadlines = [{
            "id": r["id"], "company": r["company"], "role": r["role"],
            "status": r["status"], "type": r["type"],
            "deadline": r["deadline"].isoformat() if r["deadline"] else None,
        } for r in cur.fetchall()]

        # Active interviews (phone / onsite)
        cur.execute("""
            SELECT id, company, role, status, type, updated_at
            FROM career_application
            WHERE status IN ('phone','onsite')
            ORDER BY updated_at DESC
            LIMIT 20
        """)
        interviews = [{
            "id": r["id"], "company": r["company"], "role": r["role"],
            "status": r["status"], "type": r["type"],
            "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
        } for r in cur.fetchall()]

        # Stalled: active applications with no events for N days
        cur.execute("""
            SELECT a.id, a.company, a.role, a.status, a.type,
                   COALESCE(MAX(e.occurred_at), a.created_at) AS last_activity
            FROM career_application a
            LEFT JOIN career_event e ON e.application_id = a.id
            WHERE a.status IN ('saved','applied','oa','phone','onsite','offer')
            GROUP BY a.id
            HAVING COALESCE(MAX(e.occurred_at), a.created_at)
                   < NOW() - (%s || ' days')::interval
            ORDER BY last_activity ASC
            LIMIT 20
        """, (stale_days,))
        stalled = [{
            "id": r["id"], "company": r["company"], "role": r["role"],
            "status": r["status"], "type": r["type"],
            "last_activity": r["last_activity"].isoformat() if r["last_activity"] else None,
        } for r in cur.fetchall()]

        # Offers (open)
        cur.execute("""
            SELECT id, company, role, type, salary, deadline
            FROM career_application
            WHERE status = 'offer'
            ORDER BY COALESCE(deadline, '9999-12-31'::date) ASC
            LIMIT 20
        """)
        offers = [{
            "id": r["id"], "company": r["company"], "role": r["role"],
            "type": r["type"], "salary": r["salary"],
            "deadline": r["deadline"].isoformat() if r["deadline"] else None,
        } for r in cur.fetchall()]
    finally:
        cur.close()
        conn.close()

    return {
        "by_status": by_status,
        "active": active,
        "deadlines": deadlines,
        "interviews": interviews,
        "stalled": stalled,
        "offers": offers,
        "params": {"deadline_days": deadline_days, "stale_days": stale_days},
    }


# ---------- People (research / outreach CRM) ----------

VALID_PERSON_CATEGORIES = {
    "researcher", "junior", "alumni", "recruiter", "hiring_manager",
    "founder", "engineer", "professor", "phd_student", "other",
}
VALID_OUTREACH_STATUSES = {
    "to_contact", "contacted", "replied", "in_conversation",
    "intro_done", "stalled", "archived",
}


def _person_row(r):
    return {
        "id": r["id"],
        "name": r["name"],
        "headline": r["headline"],
        "company": r["company"],
        "location": r["location"],
        "linkedin": r["linkedin"],
        "email": r["email"],
        "website": r["website"],
        "category": r["category"],
        "outreach_status": r["outreach_status"],
        "tags": list(r["tags"] or []),
        "interest": r["interest"],
        "last_contact_at": r["last_contact_at"].isoformat() if r["last_contact_at"] else None,
        "notes": r["notes"],
        "metadata": r["metadata"] or {},
        "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
    }


def _normalize_tags(val):
    if val is None:
        return []
    if isinstance(val, str):
        val = [t.strip() for t in val.split(",")]
    if not isinstance(val, list):
        raise HTTPException(400, "tags must be a list of strings")
    return [str(t).strip() for t in val if str(t).strip()]


@router.get("/people")
def list_people(
    category: Optional[str] = None,
    outreach_status: Optional[str] = None,
    tag: Optional[str] = None,
    q: Optional[str] = None,
    sort: str = Query("updated", pattern="^(updated|name|interest|last_contact)$"),
    limit: int = Query(500, ge=1, le=2000),
    offset: int = Query(0, ge=0),
):
    where = []
    params = []
    if category:
        if category not in VALID_PERSON_CATEGORIES:
            raise HTTPException(400, f"category must be one of {sorted(VALID_PERSON_CATEGORIES)}")
        where.append("category = %s")
        params.append(category)
    if outreach_status:
        if outreach_status not in VALID_OUTREACH_STATUSES:
            raise HTTPException(400, f"outreach_status must be one of {sorted(VALID_OUTREACH_STATUSES)}")
        where.append("outreach_status = %s")
        params.append(outreach_status)
    if tag:
        where.append("%s = ANY(tags)")
        params.append(tag)
    if q:
        where.append("(name ILIKE %s OR company ILIKE %s OR headline ILIKE %s OR notes ILIKE %s)")
        like = f"%{q}%"
        params.extend([like, like, like, like])

    order = {
        "updated": "updated_at DESC",
        "name": "name ASC",
        "interest": "interest DESC, updated_at DESC",
        "last_contact": "last_contact_at DESC NULLS LAST",
    }[sort]

    sql = "SELECT * FROM career_person"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += f" ORDER BY {order} LIMIT %s OFFSET %s"
    params.extend([limit, offset])

    conn = _conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute(sql, params)
        return [_person_row(r) for r in cur.fetchall()]
    finally:
        cur.close()
        conn.close()


@router.post("/people")
def create_person(payload: dict):
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "name is required")

    category = (payload.get("category") or "other").strip()
    if category not in VALID_PERSON_CATEGORIES:
        raise HTTPException(400, f"category must be one of {sorted(VALID_PERSON_CATEGORIES)}")

    outreach_status = (payload.get("outreach_status") or "to_contact").strip()
    if outreach_status not in VALID_OUTREACH_STATUSES:
        raise HTTPException(400, f"outreach_status must be one of {sorted(VALID_OUTREACH_STATUSES)}")

    interest = payload.get("interest", 2)
    try:
        interest = int(interest)
    except (TypeError, ValueError):
        raise HTTPException(400, "interest must be an integer 1-3")
    if interest < 1 or interest > 3:
        raise HTTPException(400, "interest must be 1, 2, or 3")

    tags = _normalize_tags(payload.get("tags"))
    last_contact = _parse_date(payload.get("last_contact_at"), "last_contact_at")

    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO career_person (
                name, headline, company, location, linkedin, email, website,
                category, outreach_status, tags, interest, last_contact_at, notes, metadata
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            RETURNING id
        """, (
            name,
            payload.get("headline"),
            payload.get("company"),
            payload.get("location"),
            payload.get("linkedin"),
            payload.get("email"),
            payload.get("website"),
            category,
            outreach_status,
            tags,
            interest,
            last_contact,
            payload.get("notes"),
            json.dumps(payload.get("metadata") or {}),
        ))
        new_id = cur.fetchone()[0]
        conn.commit()
        return {"id": new_id}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, f"Failed to create person: {e}")
    finally:
        cur.close()
        conn.close()


@router.get("/people/{pid}")
def get_person(pid: int):
    conn = _conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute("SELECT * FROM career_person WHERE id = %s", (pid,))
        r = cur.fetchone()
        if not r:
            raise HTTPException(404, "Person not found")
        return _person_row(r)
    finally:
        cur.close()
        conn.close()


@router.patch("/people/{pid}")
def update_person(pid: int, payload: dict):
    fields = []
    params = []

    for col in ("name", "headline", "company", "location",
                "linkedin", "email", "website", "notes"):
        if col in payload:
            val = payload[col]
            if col == "name" and (val is None or not str(val).strip()):
                raise HTTPException(400, "name cannot be empty")
            fields.append(f"{col} = %s")
            params.append(val)

    if "category" in payload:
        cat = (payload["category"] or "").strip()
        if cat not in VALID_PERSON_CATEGORIES:
            raise HTTPException(400, f"category must be one of {sorted(VALID_PERSON_CATEGORIES)}")
        fields.append("category = %s")
        params.append(cat)

    if "outreach_status" in payload:
        st = (payload["outreach_status"] or "").strip()
        if st not in VALID_OUTREACH_STATUSES:
            raise HTTPException(400, f"outreach_status must be one of {sorted(VALID_OUTREACH_STATUSES)}")
        fields.append("outreach_status = %s")
        params.append(st)

    if "interest" in payload:
        try:
            iv = int(payload["interest"])
        except (TypeError, ValueError):
            raise HTTPException(400, "interest must be an integer 1-3")
        if iv < 1 or iv > 3:
            raise HTTPException(400, "interest must be 1, 2, or 3")
        fields.append("interest = %s")
        params.append(iv)

    if "tags" in payload:
        fields.append("tags = %s")
        params.append(_normalize_tags(payload["tags"]))

    if "last_contact_at" in payload:
        fields.append("last_contact_at = %s")
        params.append(_parse_date(payload["last_contact_at"], "last_contact_at"))

    if "metadata" in payload:
        fields.append("metadata = %s::jsonb")
        params.append(json.dumps(payload["metadata"] or {}))

    if not fields:
        raise HTTPException(400, "no fields to update")

    fields.append("updated_at = NOW()")
    params.append(pid)

    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute(
            f"UPDATE career_person SET {', '.join(fields)} WHERE id = %s",
            params,
        )
        if cur.rowcount == 0:
            raise HTTPException(404, "Person not found")
        conn.commit()
        return {"ok": True}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, f"Failed to update person: {e}")
    finally:
        cur.close()
        conn.close()


@router.delete("/people/{pid}")
def delete_person(pid: int):
    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM career_person WHERE id = %s", (pid,))
        if cur.rowcount == 0:
            raise HTTPException(404, "Person not found")
        conn.commit()
        return {"ok": True}
    finally:
        cur.close()
        conn.close()


@router.get("/people/meta/tags")
def list_person_tags():
    """Distinct list of all tags across people, with counts."""
    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT tag, COUNT(*) AS n
            FROM (
                SELECT UNNEST(tags) AS tag FROM career_person
            ) t
            GROUP BY tag
            ORDER BY n DESC, tag ASC
        """)
        return [{"tag": r[0], "n": r[1]} for r in cur.fetchall()]
    finally:
        cur.close()
        conn.close()
