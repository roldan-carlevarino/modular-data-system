"""
Careers: pipeline tracker for internships, new-grad, research positions and grants.

Status workflow (UI columns, in canonical order):
    saved | applied | oa | phone | onsite | offer | accepted | rejected | withdrawn | ghosted

Types: internship | new_grad | research | phd | summer_school | grant
"""

import csv
import io
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime
from typing import Optional

import psycopg2
import psycopg2.extras
from fastapi import APIRouter, Body, File, HTTPException, Query, UploadFile

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


# ---------- People (research / outreach CRM) ----------
# IMPORTANT: declared BEFORE /{app_id} routes to avoid path collision
# (otherwise /careers/people would be parsed as app_id="people" → 422).

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


@router.get("/people-tags")
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


@router.post("/people/import-linkedin")
async def import_linkedin_csv(file: UploadFile = File(...)):
    """
    Import a LinkedIn 'Connections.csv' export.
    Format (after the 3 leading 'Notes' lines):
      First Name, Last Name, URL, Email Address, Company, Position, Connected On
    Dedup by linkedin URL: existing rows have company/position/email refreshed,
    'linkedin' tag ensured. New rows inserted with defaults
    (category=other, outreach_status=to_contact, interest=2, tag=linkedin).
    """
    raw = await file.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")

    # LinkedIn export prepends a 'Notes:' block; the real CSV header starts at
    # the first line containing 'First Name'. Skip everything before it.
    lines = text.splitlines()
    header_idx = next(
        (i for i, ln in enumerate(lines) if ln.lower().startswith("first name")),
        None,
    )
    if header_idx is None:
        raise HTTPException(400, "CSV does not look like a LinkedIn Connections export")

    reader = csv.DictReader(lines[header_idx:])

    inserted = 0
    updated = 0
    skipped = 0
    errors: list[str] = []

    conn = _conn()
    cur = conn.cursor()
    try:
        for row in reader:
            try:
                first = (row.get("First Name") or "").strip()
                last = (row.get("Last Name") or "").strip()
                name = (first + " " + last).strip()
                url = (row.get("URL") or "").strip() or None
                email = (row.get("Email Address") or "").strip() or None
                company = (row.get("Company") or "").strip() or None
                position = (row.get("Position") or "").strip() or None

                if not name and not url:
                    skipped += 1
                    continue
                if not name:
                    name = url or "(unknown)"

                # Dedup by linkedin URL
                existing_id = None
                if url:
                    cur.execute(
                        "SELECT id FROM career_person WHERE linkedin = %s LIMIT 1",
                        (url,),
                    )
                    r = cur.fetchone()
                    if r:
                        existing_id = r[0]

                if existing_id is not None:
                    cur.execute(
                        """
                        UPDATE career_person SET
                            company  = COALESCE(%s, company),
                            headline = COALESCE(%s, headline),
                            email    = COALESCE(email, %s),
                            tags     = (
                                SELECT ARRAY(
                                    SELECT DISTINCT UNNEST(COALESCE(tags, ARRAY[]::text[]) || ARRAY['linkedin']::text[])
                                )
                            ),
                            updated_at = NOW()
                        WHERE id = %s
                        """,
                        (company, position, email, existing_id),
                    )
                    updated += 1
                else:
                    cur.execute(
                        """
                        INSERT INTO career_person (
                            name, headline, company, linkedin, email,
                            category, outreach_status, tags, interest, metadata
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                        """,
                        (
                            name, position, company, url, email,
                            "other", "to_contact", ["linkedin"], 2,
                            json.dumps({"source": "linkedin_csv"}),
                        ),
                    )
                    inserted += 1
            except Exception as e:  # noqa: BLE001
                errors.append(f"{row.get('First Name','')} {row.get('Last Name','')}: {e}")
                skipped += 1
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, f"Import failed: {e}")
    finally:
        cur.close()
        conn.close()

    return {
        "inserted": inserted,
        "updated": updated,
        "skipped": skipped,
        "errors": errors[:20],
    }


# ==========================================================================
# Opportunity agent: profile + market sources + AI fit scoring
# --------------------------------------------------------------------------
# The agent fetches openings from public job-board APIs, scores each against
# the user's profile (CV + projects + library) with the local LLM running on
# the Mac knowledge-worker (via a claim/result queue mirroring kn_chat), and
# lets the user promote a scored opportunity into the kanban pipeline.
# NOTE: all routes here are declared BEFORE /{app_id} to avoid path collision.
# ==========================================================================

import html as _html  # noqa: E402  (local import keeps the top of the file lean)

SOURCE_KINDS = {"greenhouse", "lever", "ashby", "remotive", "arbeitnow", "remoteok"}
SCORE_LEASE_MINUTES = 10
_MAX_DESC = 6000

_AGENT_SCHEMA_READY = False


def _ensure_agent_schema(cur):
    """Idempotent DDL for the opportunity-agent tables (guarded, runs once)."""
    global _AGENT_SCHEMA_READY
    if _AGENT_SCHEMA_READY:
        return
    cur.execute("""
        CREATE TABLE IF NOT EXISTS career_profile (
            id          INTEGER PRIMARY KEY DEFAULT 1,
            headline    TEXT,
            summary     TEXT,
            cv_text     TEXT,
            cv_filename TEXT,
            skills      TEXT[]  NOT NULL DEFAULT '{}',
            interests   TEXT[]  NOT NULL DEFAULT '{}',
            locations   TEXT[]  NOT NULL DEFAULT '{}',
            links       JSONB   NOT NULL DEFAULT '{}'::jsonb,
            updated_at  TIMESTAMP NOT NULL DEFAULT NOW(),
            CONSTRAINT career_profile_singleton CHECK (id = 1)
        )
    """)
    cur.execute("INSERT INTO career_profile (id) VALUES (1) ON CONFLICT (id) DO NOTHING")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS career_source (
            id              SERIAL PRIMARY KEY,
            kind            TEXT NOT NULL,
            slug            TEXT NOT NULL,
            label           TEXT,
            enabled         BOOLEAN NOT NULL DEFAULT TRUE,
            filters         JSONB NOT NULL DEFAULT '{}'::jsonb,
            last_fetched_at TIMESTAMP,
            last_status     TEXT,
            created_at      TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS career_opportunity (
            id             SERIAL PRIMARY KEY,
            source_id      INTEGER REFERENCES career_source(id) ON DELETE SET NULL,
            source_kind    TEXT NOT NULL,
            external_id    TEXT NOT NULL,
            title          TEXT NOT NULL,
            company        TEXT,
            location       TEXT,
            url            TEXT,
            description    TEXT,
            remote         BOOLEAN NOT NULL DEFAULT FALSE,
            posted_at      TIMESTAMP,
            raw            JSONB NOT NULL DEFAULT '{}'::jsonb,
            fit_score      INTEGER,
            fit_reason     TEXT,
            suggested_type TEXT,
            suggested_tags TEXT[] NOT NULL DEFAULT '{}',
            gaps           TEXT,
            score_status   TEXT NOT NULL DEFAULT 'pending',
            model          TEXT,
            scored_at      TIMESTAMP,
            promoted_application_id INTEGER,
            created_at     TIMESTAMP NOT NULL DEFAULT NOW(),
            updated_at     TIMESTAMP NOT NULL DEFAULT NOW(),
            UNIQUE (source_kind, external_id)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS career_opp_status_idx ON career_opportunity(score_status)")
    cur.execute("CREATE INDEX IF NOT EXISTS career_opp_score_idx ON career_opportunity(fit_score DESC NULLS LAST)")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS career_score_job (
            id             SERIAL PRIMARY KEY,
            opportunity_id INTEGER NOT NULL REFERENCES career_opportunity(id) ON DELETE CASCADE,
            status         TEXT NOT NULL DEFAULT 'pending',
            worker_id      TEXT,
            attempts       INTEGER NOT NULL DEFAULT 0,
            claimed_at     TIMESTAMP,
            error          TEXT,
            created_at     TIMESTAMP NOT NULL DEFAULT NOW(),
            finished_at    TIMESTAMP
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS career_score_job_status_idx ON career_score_job(status)")
    _AGENT_SCHEMA_READY = True


def migrate():
    """Create the opportunity-agent tables once at startup (called from main.py)."""
    conn = _conn()
    try:
        cur = conn.cursor()
        _ensure_agent_schema(cur)
        conn.commit()
        cur.close()
    finally:
        conn.close()


# ---------- Row serializers ----------

def _profile_row(r):
    return {
        "headline": r["headline"],
        "summary": r["summary"],
        "cv_text": r["cv_text"],
        "cv_filename": r["cv_filename"],
        "has_cv": bool(r["cv_text"]),
        "skills": list(r["skills"] or []),
        "interests": list(r["interests"] or []),
        "locations": list(r["locations"] or []),
        "links": r["links"] or {},
        "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
    }


def _source_row(r):
    return {
        "id": r["id"],
        "kind": r["kind"],
        "slug": r["slug"],
        "label": r["label"],
        "enabled": r["enabled"],
        "filters": r["filters"] or {},
        "last_fetched_at": r["last_fetched_at"].isoformat() if r["last_fetched_at"] else None,
        "last_status": r["last_status"],
        "created_at": r["created_at"].isoformat() if r["created_at"] else None,
    }


def _opportunity_row(r):
    return {
        "id": r["id"],
        "source_id": r["source_id"],
        "source_kind": r["source_kind"],
        "external_id": r["external_id"],
        "title": r["title"],
        "company": r["company"],
        "location": r["location"],
        "url": r["url"],
        "description": r["description"],
        "remote": r["remote"],
        "posted_at": r["posted_at"].isoformat() if r["posted_at"] else None,
        "fit_score": r["fit_score"],
        "fit_reason": r["fit_reason"],
        "suggested_type": r["suggested_type"],
        "suggested_tags": list(r["suggested_tags"] or []),
        "gaps": r["gaps"],
        "score_status": r["score_status"],
        "model": r["model"],
        "scored_at": r["scored_at"].isoformat() if r["scored_at"] else None,
        "promoted_application_id": r["promoted_application_id"],
        "created_at": r["created_at"].isoformat() if r["created_at"] else None,
    }


# ---------- Profile ----------

@router.get("/profile")
def get_profile():
    conn = _conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        _ensure_agent_schema(cur)
        conn.commit()
        cur.execute("SELECT * FROM career_profile WHERE id = 1")
        r = cur.fetchone()
        return _profile_row(r) if r else {}
    finally:
        cur.close()
        conn.close()


def _norm_list(val):
    if val is None:
        return None
    if isinstance(val, str):
        val = re.split(r"[,\n]", val)
    if not isinstance(val, list):
        raise HTTPException(400, "expected a list or comma-separated string")
    return [str(v).strip() for v in val if str(v).strip()]


@router.put("/profile")
def update_profile(payload: dict = Body(...)):
    fields, params = [], []
    for col in ("headline", "summary", "cv_text"):
        if col in payload:
            fields.append(f"{col} = %s")
            params.append(payload[col])
    for col in ("skills", "interests", "locations"):
        if col in payload:
            fields.append(f"{col} = %s")
            params.append(_norm_list(payload[col]) or [])
    if "links" in payload:
        if not isinstance(payload["links"], dict):
            raise HTTPException(400, "links must be an object")
        fields.append("links = %s::jsonb")
        params.append(json.dumps(payload["links"]))
    if not fields:
        raise HTTPException(400, "no fields to update")
    fields.append("updated_at = NOW()")

    conn = _conn()
    cur = conn.cursor()
    try:
        _ensure_agent_schema(cur)
        cur.execute(f"UPDATE career_profile SET {', '.join(fields)} WHERE id = 1", params)
        conn.commit()
        return {"ok": True}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, f"Failed to update profile: {e}")
    finally:
        cur.close()
        conn.close()


@router.post("/profile/cv")
async def upload_cv(file: UploadFile = File(...)):
    """Extract text from an uploaded CV (PDF) and store it on the profile."""
    raw = await file.read()
    name = file.filename or "cv.pdf"
    text = ""
    if name.lower().endswith(".pdf"):
        try:
            import pdfplumber
            with pdfplumber.open(io.BytesIO(raw)) as pdf:
                text = "\n".join((p.extract_text() or "") for p in pdf.pages)
        except Exception as e:
            raise HTTPException(400, f"Could not read PDF: {e}")
    else:
        try:
            text = raw.decode("utf-8", errors="ignore")
        except Exception:
            raise HTTPException(400, "Unsupported file; upload a PDF or plain text")

    text = text.strip()
    if not text:
        raise HTTPException(400, "No text could be extracted from the file")

    conn = _conn()
    cur = conn.cursor()
    try:
        _ensure_agent_schema(cur)
        cur.execute(
            "UPDATE career_profile SET cv_text = %s, cv_filename = %s, updated_at = NOW() WHERE id = 1",
            (text[:40000], name),
        )
        conn.commit()
        return {"ok": True, "filename": name, "chars": len(text)}
    finally:
        cur.close()
        conn.close()


def _build_profile_context(cur) -> str:
    """Assemble the full candidate profile the LLM scores against: stored
    profile + active projects (projects_path) + a compact library snapshot."""
    cur.execute("SELECT * FROM career_profile WHERE id = 1")
    p = cur.fetchone()
    parts = ["PERFIL DEL CANDIDATO"]
    if p:
        if p["headline"]:
            parts.append(f"Titular: {p['headline']}")
        if p["summary"]:
            parts.append(f"Resumen: {p['summary']}")
        if p["skills"]:
            parts.append("Skills: " + ", ".join(p["skills"]))
        if p["interests"]:
            parts.append("Intereses: " + ", ".join(p["interests"]))
        if p["locations"]:
            parts.append("Ubicaciones preferidas: " + ", ".join(p["locations"]))
        if p["links"]:
            parts.append("Enlaces: " + ", ".join(f"{k}: {v}" for k, v in p["links"].items()))
        if p["cv_text"]:
            parts.append("\nCV:\n" + p["cv_text"][:6000])

    # Active projects
    try:
        cur.execute("""
            SELECT name, description FROM projects_path
            WHERE status = 'active' ORDER BY path LIMIT 40
        """)
        proj = cur.fetchall()
        if proj:
            parts.append("\nPROYECTOS ACTIVOS:")
            for pr in proj:
                desc = (pr["description"] or "").strip()
                parts.append(f"- {pr['name']}" + (f": {desc}" if desc else ""))
    except Exception:
        pass

    # Compact library snapshot (reading / research interests)
    try:
        cur.execute("""
            SELECT title, type, year FROM lib_item
            ORDER BY added_at DESC LIMIT 30
        """)
        lib = cur.fetchall()
        if lib:
            parts.append("\nBIBLIOTECA / LECTURAS:")
            for li in lib:
                yr = f" ({li['year']})" if li["year"] else ""
                parts.append(f"- [{li['type']}] {li['title']}{yr}")
    except Exception:
        pass

    return "\n".join(parts)


@router.get("/profile/context")
def get_profile_context():
    """Full profile context string used by the scoring worker."""
    conn = _conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        _ensure_agent_schema(cur)
        conn.commit()
        ctx = _build_profile_context(cur)
        return {"context": ctx, "chars": len(ctx)}
    finally:
        cur.close()
        conn.close()


# ---------- Sources ----------

@router.get("/sources")
def list_sources():
    conn = _conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        _ensure_agent_schema(cur)
        conn.commit()
        cur.execute("SELECT * FROM career_source ORDER BY id")
        return [_source_row(r) for r in cur.fetchall()]
    finally:
        cur.close()
        conn.close()


@router.post("/sources")
def create_source(payload: dict = Body(...)):
    kind = (payload.get("kind") or "").strip().lower()
    slug = (payload.get("slug") or "").strip()
    if kind not in SOURCE_KINDS:
        raise HTTPException(400, f"kind must be one of {sorted(SOURCE_KINDS)}")
    # Aggregators (remotive/remoteok/arbeitnow) don't need a board slug.
    if kind in {"greenhouse", "lever", "ashby"} and not slug:
        raise HTTPException(400, f"{kind} requires a board slug (the company token)")
    filters = payload.get("filters") or {}
    if not isinstance(filters, dict):
        raise HTTPException(400, "filters must be an object")

    conn = _conn()
    cur = conn.cursor()
    try:
        _ensure_agent_schema(cur)
        cur.execute("""
            INSERT INTO career_source (kind, slug, label, enabled, filters)
            VALUES (%s, %s, %s, %s, %s::jsonb) RETURNING id
        """, (kind, slug, payload.get("label"), bool(payload.get("enabled", True)),
              json.dumps(filters)))
        new_id = cur.fetchone()[0]
        conn.commit()
        return {"id": new_id}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, f"Failed to create source: {e}")
    finally:
        cur.close()
        conn.close()


@router.patch("/sources/{sid}")
def update_source(sid: int, payload: dict = Body(...)):
    fields, params = [], []
    for col in ("slug", "label"):
        if col in payload:
            fields.append(f"{col} = %s")
            params.append(payload[col])
    if "enabled" in payload:
        fields.append("enabled = %s")
        params.append(bool(payload["enabled"]))
    if "filters" in payload:
        if not isinstance(payload["filters"], dict):
            raise HTTPException(400, "filters must be an object")
        fields.append("filters = %s::jsonb")
        params.append(json.dumps(payload["filters"]))
    if not fields:
        raise HTTPException(400, "no fields to update")
    params.append(sid)
    conn = _conn()
    cur = conn.cursor()
    try:
        _ensure_agent_schema(cur)
        cur.execute(f"UPDATE career_source SET {', '.join(fields)} WHERE id = %s", params)
        if cur.rowcount == 0:
            raise HTTPException(404, "Source not found")
        conn.commit()
        return {"ok": True}
    except HTTPException:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


@router.delete("/sources/{sid}")
def delete_source(sid: int):
    conn = _conn()
    cur = conn.cursor()
    try:
        _ensure_agent_schema(cur)
        cur.execute("DELETE FROM career_source WHERE id = %s", (sid,))
        if cur.rowcount == 0:
            raise HTTPException(404, "Source not found")
        conn.commit()
        return {"ok": True}
    finally:
        cur.close()
        conn.close()


# ---------- Market fetchers (public job-board APIs, stdlib only) ----------

def _http_get_json(url: str, timeout: int = 25):
    req = urllib.request.Request(url, headers={
        "User-Agent": "modular-data-careers/1.0",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="ignore"))


def _strip_html(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = _html.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()[:_MAX_DESC]


def _parse_ts(val):
    if val in (None, ""):
        return None
    if isinstance(val, (int, float)):
        ts = val / 1000 if val > 1e11 else val
        try:
            return datetime.utcfromtimestamp(ts)
        except (OverflowError, OSError, ValueError):
            return None
    s = str(val).strip()
    if s.isdigit():
        return _parse_ts(int(s))
    s = s.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s).replace(tzinfo=None)
    except ValueError:
        try:
            return datetime.strptime(s[:10], "%Y-%m-%d")
        except ValueError:
            return None


def _fetch_source(source: dict) -> list:
    """Return a list of normalized opportunity dicts for one configured source.
    Never raises: returns [] on any network/parse error (caller records status)."""
    kind = source["kind"]
    slug = (source.get("slug") or "").strip()
    label = source.get("label") or slug
    out = []
    try:
        if kind == "greenhouse":
            data = _http_get_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true")
            for j in data.get("jobs", []):
                loc = (j.get("location") or {}).get("name") or ""
                out.append({
                    "external_id": f"gh:{slug}:{j.get('id')}",
                    "title": j.get("title"), "company": label, "location": loc,
                    "url": j.get("absolute_url"),
                    "description": _strip_html(j.get("content") or ""),
                    "remote": "remote" in loc.lower(),
                    "posted_at": _parse_ts(j.get("updated_at")),
                    "raw": {"id": j.get("id")},
                })
        elif kind == "lever":
            data = _http_get_json(f"https://api.lever.co/v0/postings/{slug}?mode=json")
            for j in data:
                cats = j.get("categories") or {}
                loc = cats.get("location") or ""
                out.append({
                    "external_id": f"lever:{slug}:{j.get('id')}",
                    "title": j.get("text"), "company": label, "location": loc,
                    "url": j.get("hostedUrl"),
                    "description": j.get("descriptionPlain") or _strip_html(j.get("description") or ""),
                    "remote": "remote" in loc.lower() or (cats.get("commitment") or "").lower() == "remote",
                    "posted_at": _parse_ts(j.get("createdAt")),
                    "raw": {"id": j.get("id"), "team": cats.get("team")},
                })
        elif kind == "ashby":
            data = _http_get_json(f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true")
            for j in data.get("jobs", []):
                loc = j.get("location") or ""
                out.append({
                    "external_id": f"ashby:{slug}:{j.get('id')}",
                    "title": j.get("title"), "company": label, "location": loc,
                    "url": j.get("jobUrl") or j.get("applyUrl"),
                    "description": j.get("descriptionPlain") or _strip_html(j.get("descriptionHtml") or ""),
                    "remote": bool(j.get("isRemote")) or "remote" in loc.lower(),
                    "posted_at": _parse_ts(j.get("publishedAt")),
                    "raw": {"id": j.get("id"), "department": j.get("department")},
                })
        elif kind == "remotive":
            q = (source.get("filters") or {}).get("search") or ""
            url = "https://remotive.com/api/remote-jobs?limit=50"
            if q:
                url += "&search=" + urllib.parse.quote(q)
            data = _http_get_json(url)
            for j in data.get("jobs", []):
                out.append({
                    "external_id": f"remotive:{j.get('id')}",
                    "title": j.get("title"), "company": j.get("company_name"),
                    "location": j.get("candidate_required_location") or "Remote",
                    "url": j.get("url"),
                    "description": _strip_html(j.get("description") or ""),
                    "remote": True,
                    "posted_at": _parse_ts(j.get("publication_date")),
                    "raw": {"category": j.get("category")},
                })
        elif kind == "arbeitnow":
            data = _http_get_json("https://www.arbeitnow.com/api/job-board-api")
            for j in data.get("data", []):
                out.append({
                    "external_id": f"arbeitnow:{j.get('slug')}",
                    "title": j.get("title"), "company": j.get("company_name"),
                    "location": j.get("location") or "",
                    "url": j.get("url"),
                    "description": _strip_html(j.get("description") or ""),
                    "remote": bool(j.get("remote")),
                    "posted_at": _parse_ts(j.get("created_at")),
                    "raw": {"tags": j.get("tags")},
                })
        elif kind == "remoteok":
            data = _http_get_json("https://remoteok.com/api")
            for j in data:
                if not isinstance(j, dict) or not j.get("id"):
                    continue
                out.append({
                    "external_id": f"remoteok:{j.get('id')}",
                    "title": j.get("position") or j.get("title"),
                    "company": j.get("company"),
                    "location": j.get("location") or "Remote",
                    "url": j.get("url"),
                    "description": _strip_html(j.get("description") or ""),
                    "remote": True,
                    "posted_at": _parse_ts(j.get("date")),
                    "raw": {"tags": j.get("tags")},
                })
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError, KeyError, TypeError):
        return []
    # Drop malformed entries (need a title + external_id)
    return [o for o in out if o.get("title") and o.get("external_id")]


def _passes_filters(opp: dict, filters: dict) -> bool:
    if not filters:
        return True
    hay = f"{opp.get('title','')} {opp.get('description','')} {opp.get('location','')}".lower()
    kws = [k.lower() for k in (filters.get("keywords") or []) if k]
    if kws and not any(k in hay for k in kws):
        return False
    excl = [k.lower() for k in (filters.get("exclude") or []) if k]
    if excl and any(k in hay for k in excl):
        return False
    return True


def _enqueue_score(cur, opp_id: int):
    cur.execute(
        "SELECT 1 FROM career_score_job WHERE opportunity_id = %s AND status IN ('pending','in_progress') LIMIT 1",
        (opp_id,),
    )
    if not cur.fetchone():
        cur.execute("INSERT INTO career_score_job (opportunity_id) VALUES (%s)", (opp_id,))
    cur.execute(
        "UPDATE career_opportunity SET score_status = 'queued', updated_at = NOW() WHERE id = %s",
        (opp_id,),
    )


def _ingest_source(cur, source: dict) -> dict:
    items = _fetch_source(source)
    filters = source.get("filters") or {}
    inserted = 0
    for opp in items:
        if not _passes_filters(opp, filters):
            continue
        cur.execute("""
            INSERT INTO career_opportunity
                (source_id, source_kind, external_id, title, company, location,
                 url, description, remote, posted_at, raw)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (source_kind, external_id) DO NOTHING
            RETURNING id
        """, (
            source.get("id"), source["kind"], opp["external_id"], opp["title"],
            opp.get("company"), opp.get("location"), opp.get("url"),
            opp.get("description"), opp.get("remote", False),
            opp.get("posted_at"), json.dumps(opp.get("raw") or {}),
        ))
        row = cur.fetchone()
        if row:
            _enqueue_score(cur, row[0])
            inserted += 1
    return {"fetched": len(items), "inserted": inserted}


@router.post("/opportunities/fetch")
def fetch_opportunities(payload: dict = Body(default={})):
    """Fetch openings from enabled sources, insert new ones and queue them for
    AI scoring. Optional body {source_id} restricts to one source."""
    source_id = payload.get("source_id")
    conn = _conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        _ensure_agent_schema(cur)
        conn.commit()
        if source_id:
            cur.execute("SELECT * FROM career_source WHERE id = %s", (source_id,))
        else:
            cur.execute("SELECT * FROM career_source WHERE enabled = TRUE ORDER BY id")
        sources = cur.fetchall()
        if not sources:
            return {"sources": 0, "fetched": 0, "inserted": 0, "per_source": []}

        total_fetched = total_inserted = 0
        per_source = []
        for s in sources:
            src = _source_row(s)
            try:
                res = _ingest_source(cur, src)
                status = f"ok: {res['inserted']} new / {res['fetched']} listed"
            except Exception as e:  # noqa: BLE001
                res = {"fetched": 0, "inserted": 0}
                status = f"error: {e}"
            cur.execute(
                "UPDATE career_source SET last_fetched_at = NOW(), last_status = %s WHERE id = %s",
                (status[:300], src["id"]),
            )
            conn.commit()
            total_fetched += res["fetched"]
            total_inserted += res["inserted"]
            per_source.append({"id": src["id"], "kind": src["kind"],
                               "label": src["label"], **res, "status": status})
        return {"sources": len(sources), "fetched": total_fetched,
                "inserted": total_inserted, "per_source": per_source}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, f"Fetch failed: {e}")
    finally:
        cur.close()
        conn.close()


@router.post("/opportunities/rescore")
def rescore_opportunities(payload: dict = Body(default={})):
    """Queue opportunities for (re)scoring. scope: 'unscored' (default) | 'all';
    or pass explicit {ids: [...]}."""
    ids = payload.get("ids")
    scope = (payload.get("scope") or "unscored").strip()
    conn = _conn()
    cur = conn.cursor()
    try:
        _ensure_agent_schema(cur)
        if ids:
            cur.execute(
                "SELECT id FROM career_opportunity WHERE id = ANY(%s) AND promoted_application_id IS NULL",
                (list(ids),),
            )
        elif scope == "all":
            cur.execute("SELECT id FROM career_opportunity WHERE promoted_application_id IS NULL AND score_status != 'dismissed'")
        else:
            cur.execute("SELECT id FROM career_opportunity WHERE score_status IN ('pending','error')")
        opp_ids = [r[0] for r in cur.fetchall()]
        for oid in opp_ids:
            _enqueue_score(cur, oid)
        conn.commit()
        return {"enqueued": len(opp_ids)}
    finally:
        cur.close()
        conn.close()


@router.get("/opportunities")
def list_opportunities(
    score_status: Optional[str] = Query(None),
    source_kind: Optional[str] = Query(None),
    min_score: Optional[int] = Query(None, ge=0, le=100),
    q: Optional[str] = Query(None),
    include_promoted: bool = Query(False),
    include_dismissed: bool = Query(False),
    sort: str = Query("score", pattern="^(score|new)$"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    where, params = [], []
    if not include_promoted:
        where.append("promoted_application_id IS NULL")
    if not include_dismissed:
        where.append("score_status != 'dismissed'")
    if score_status:
        where.append("score_status = %s")
        params.append(score_status)
    if source_kind:
        where.append("source_kind = %s")
        params.append(source_kind)
    if min_score is not None:
        where.append("fit_score >= %s")
        params.append(min_score)
    if q:
        where.append("(title ILIKE %s OR company ILIKE %s OR description ILIKE %s)")
        like = f"%{q}%"
        params.extend([like, like, like])
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    order = ("fit_score DESC NULLS LAST, created_at DESC" if sort == "score"
             else "created_at DESC")
    params.extend([limit, offset])

    conn = _conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        _ensure_agent_schema(cur)
        conn.commit()
        cur.execute(
            f"SELECT * FROM career_opportunity {where_sql} ORDER BY {order} LIMIT %s OFFSET %s",
            params,
        )
        return [_opportunity_row(r) for r in cur.fetchall()]
    finally:
        cur.close()
        conn.close()


@router.get("/opportunities/stats")
def opportunities_stats():
    conn = _conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        _ensure_agent_schema(cur)
        conn.commit()
        cur.execute("""
            SELECT
                COUNT(*) FILTER (WHERE promoted_application_id IS NULL AND score_status != 'dismissed') AS open,
                COUNT(*) FILTER (WHERE score_status = 'queued') AS queued,
                COUNT(*) FILTER (WHERE score_status = 'scored' AND promoted_application_id IS NULL) AS scored,
                COUNT(*) FILTER (WHERE fit_score >= 70 AND promoted_application_id IS NULL) AS strong,
                COUNT(*) AS total
            FROM career_opportunity
        """)
        return dict(cur.fetchone())
    finally:
        cur.close()
        conn.close()


@router.post("/opportunities/{oid}/promote")
def promote_opportunity(oid: int, payload: dict = Body(default={})):
    """Create a kanban application from a scored opportunity (status 'saved')."""
    conn = _conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        _ensure_agent_schema(cur)
        cur.execute("SELECT * FROM career_opportunity WHERE id = %s", (oid,))
        opp = cur.fetchone()
        if not opp:
            raise HTTPException(404, "Opportunity not found")
        if opp["promoted_application_id"]:
            return {"application_id": opp["promoted_application_id"], "already": True}

        type_ = (payload.get("type") or opp["suggested_type"] or "internship").strip()
        if type_ not in VALID_TYPES:
            type_ = "internship"
        company = opp["company"] or opp["source_kind"]
        role = opp["title"]
        notes_bits = []
        if opp["fit_reason"]:
            notes_bits.append(opp["fit_reason"])
        if opp["gaps"]:
            notes_bits.append("Qué me falta: " + opp["gaps"])
        notes = "\n\n".join(notes_bits) or None
        metadata = {
            "origin": "agent",
            "opportunity_id": oid,
            "source_kind": opp["source_kind"],
            "fit_score": opp["fit_score"],
            "suggested_tags": list(opp["suggested_tags"] or []),
        }
        cur.execute("""
            INSERT INTO career_application
                (type, company, role, location, status, source, url, notes, metadata)
            VALUES (%s, %s, %s, %s, 'saved', %s, %s, %s, %s::jsonb)
            RETURNING id
        """, (type_, company, role, opp["location"], opp["source_kind"],
              opp["url"], notes, json.dumps(metadata)))
        app_id = cur.fetchone()["id"]
        _insert_status_event(cur, app_id, "saved", None)
        cur.execute(
            "UPDATE career_opportunity SET promoted_application_id = %s, updated_at = NOW() WHERE id = %s",
            (app_id, oid),
        )
        conn.commit()
        return {"application_id": app_id, "type": type_}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, f"Promote failed: {e}")
    finally:
        cur.close()
        conn.close()


@router.post("/opportunities/{oid}/dismiss")
def dismiss_opportunity(oid: int):
    conn = _conn()
    cur = conn.cursor()
    try:
        _ensure_agent_schema(cur)
        cur.execute(
            "UPDATE career_opportunity SET score_status = 'dismissed', updated_at = NOW() WHERE id = %s",
            (oid,),
        )
        if cur.rowcount == 0:
            raise HTTPException(404, "Opportunity not found")
        conn.commit()
        return {"ok": True}
    finally:
        cur.close()
        conn.close()


@router.delete("/opportunities/{oid}")
def delete_opportunity(oid: int):
    conn = _conn()
    cur = conn.cursor()
    try:
        _ensure_agent_schema(cur)
        cur.execute("DELETE FROM career_opportunity WHERE id = %s", (oid,))
        if cur.rowcount == 0:
            raise HTTPException(404, "Opportunity not found")
        conn.commit()
        return {"ok": True}
    finally:
        cur.close()
        conn.close()


# ---------- Scoring queue (claimed by the Mac knowledge-worker) ----------

@router.post("/worker/score/claim")
def worker_score_claim(payload: dict = Body(default={})):
    """Worker pulls the next opportunity to score (leased). Includes the current
    profile context so the worker can build the prompt in one round-trip."""
    worker_id = (payload.get("worker_id") or "worker").strip()
    conn = _conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        _ensure_agent_schema(cur)
        cur.execute("""
            WITH nxt AS (
                SELECT id FROM career_score_job
                WHERE status = 'pending'
                   OR (status = 'in_progress'
                       AND claimed_at < NOW() - INTERVAL '%s minutes')
                ORDER BY id
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            UPDATE career_score_job j
            SET status = 'in_progress', worker_id = %%s,
                attempts = j.attempts + 1, claimed_at = NOW()
            FROM nxt WHERE j.id = nxt.id
            RETURNING j.id, j.opportunity_id
        """ % SCORE_LEASE_MINUTES, (worker_id,))
        row = cur.fetchone()
        if not row:
            conn.commit()
            return {"job": None}
        cur.execute("""
            SELECT id, title, company, location, url, description, source_kind, remote
            FROM career_opportunity WHERE id = %s
        """, (row["opportunity_id"],))
        opp = cur.fetchone()
        ctx = _build_profile_context(cur)
        conn.commit()
        if not opp:
            return {"job": None}
        return {"job": {
            "id": row["id"],
            "opportunity_id": row["opportunity_id"],
            "profile_context": ctx,
            "opportunity": {
                "title": opp["title"], "company": opp["company"],
                "location": opp["location"], "url": opp["url"],
                "remote": opp["remote"], "source_kind": opp["source_kind"],
                "description": (opp["description"] or "")[:_MAX_DESC],
            },
        }}
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, f"score claim failed: {e}")
    finally:
        cur.close()
        conn.close()


@router.post("/worker/score/result")
def worker_score_result(payload: dict = Body(...)):
    """Worker posts the fit score + classification for one opportunity."""
    job_id = payload.get("job_id")
    if job_id is None:
        raise HTTPException(400, "job_id is required")
    try:
        score = int(payload.get("fit_score"))
    except (TypeError, ValueError):
        score = None
    if score is not None:
        score = max(0, min(100, score))
    reason = (payload.get("fit_reason") or "").strip() or None
    gaps = (payload.get("gaps") or "").strip() or None
    stype = (payload.get("suggested_type") or "").strip()
    if stype not in VALID_TYPES:
        stype = None
    tags = payload.get("suggested_tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",")]
    tags = [str(t).strip() for t in tags if str(t).strip()][:12]
    model = (payload.get("model") or "").strip() or None

    conn = _conn()
    cur = conn.cursor()
    try:
        _ensure_agent_schema(cur)
        cur.execute("SELECT opportunity_id FROM career_score_job WHERE id = %s", (int(job_id),))
        r = cur.fetchone()
        if not r:
            raise HTTPException(404, "Score job not found")
        opp_id = r[0]
        cur.execute("""
            UPDATE career_opportunity
            SET fit_score = %s, fit_reason = %s, suggested_type = %s,
                suggested_tags = %s, gaps = %s, model = %s,
                score_status = 'scored', scored_at = NOW(), updated_at = NOW()
            WHERE id = %s
        """, (score, reason, stype, tags, gaps, model, opp_id))
        cur.execute(
            "UPDATE career_score_job SET status = 'done', error = NULL, finished_at = NOW() WHERE id = %s",
            (int(job_id),),
        )
        conn.commit()
        return {"ok": True, "opportunity_id": opp_id}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, f"score result failed: {e}")
    finally:
        cur.close()
        conn.close()


@router.post("/worker/score/fail")
def worker_score_fail(payload: dict = Body(...)):
    job_id = payload.get("job_id")
    error = (payload.get("error") or "unknown error")[:1000]
    if job_id is None:
        raise HTTPException(400, "job_id is required")
    conn = _conn()
    cur = conn.cursor()
    try:
        _ensure_agent_schema(cur)
        cur.execute("SELECT opportunity_id FROM career_score_job WHERE id = %s", (int(job_id),))
        r = cur.fetchone()
        cur.execute(
            "UPDATE career_score_job SET status = 'error', error = %s, finished_at = NOW() WHERE id = %s",
            (error, int(job_id)),
        )
        if r:
            cur.execute(
                "UPDATE career_opportunity SET score_status = 'error', updated_at = NOW() WHERE id = %s",
                (r[0],),
            )
        conn.commit()
        return {"ok": True}
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


# ---------- People moved above /{app_id} to avoid path collision ----------
