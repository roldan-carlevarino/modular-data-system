from datetime import date, datetime, time, timedelta
from typing import Optional

import os
import psycopg2
from fastapi import APIRouter, HTTPException, Query


router = APIRouter(prefix="/calendar/template", tags=["Calendar Template"])


def _connect():
    return psycopg2.connect(os.getenv("TASKS_URL"), sslmode="require")


# ── list all template rows (optionally filter by day) ───────────────

@router.get("")
def list_templates(day_of_week: Optional[int] = Query(default=None)):
    conn = None
    cur = None
    try:
        conn = _connect()
        cur = conn.cursor()

        if day_of_week is not None:
            if day_of_week < 0 or day_of_week > 6:
                raise HTTPException(400, "day_of_week must be 0 (Mon) – 6 (Sun)")
            cur.execute(
                """
                SELECT id, day_of_week, start_hour, start_minute,
                       duration_minutes, title, item_kind, active
                FROM calendar_weekly_template
                WHERE day_of_week = %s
                ORDER BY start_hour, start_minute, id
                """,
                (day_of_week,),
            )
        else:
            cur.execute(
                """
                SELECT id, day_of_week, start_hour, start_minute,
                       duration_minutes, title, item_kind, active
                FROM calendar_weekly_template
                ORDER BY day_of_week, start_hour, start_minute, id
                """
            )

        rows = cur.fetchall()
        items = [
            {
                "id": r[0],
                "day_of_week": r[1],
                "start_hour": r[2],
                "start_minute": r[3],
                "duration_minutes": r[4],
                "title": r[5],
                "item_kind": r[6],
                "active": r[7],
            }
            for r in rows
        ]
        return {"count": len(items), "items": items}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Failed to list templates: {e}")
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


# ── create a new template row ────────────────────────────────────────

@router.post("")
def create_template(payload: dict):
    conn = None
    cur = None
    try:
        day_of_week = int(payload.get("day_of_week", -1))
        start_hour = int(payload.get("start_hour", -1))
        start_minute = int(payload.get("start_minute", 0))
        duration_minutes = int(payload.get("duration_minutes", 60))
        title = (payload.get("title") or "").strip()
        item_kind = (payload.get("item_kind") or "note").strip() or "note"
        active = bool(payload.get("active", True))

        if day_of_week < 0 or day_of_week > 6:
            raise HTTPException(400, "day_of_week must be 0 (Mon) – 6 (Sun)")
        if start_hour < 0 or start_hour > 23:
            raise HTTPException(400, "start_hour must be 0-23")
        if start_minute < 0 or start_minute > 59:
            raise HTTPException(400, "start_minute must be 0-59")
        if duration_minutes <= 0:
            raise HTTPException(400, "duration_minutes must be > 0")
        if not title:
            raise HTTPException(400, "title is required")

        conn = _connect()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO calendar_weekly_template
                (day_of_week, start_hour, start_minute, duration_minutes, title, item_kind, active)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (day_of_week, start_hour, start_minute, duration_minutes, title, item_kind, active),
        )
        new_id = cur.fetchone()[0]
        conn.commit()
        return {"ok": True, "id": new_id}

    except HTTPException:
        if conn:
            conn.rollback()
        raise
    except Exception as e:
        if conn:
            conn.rollback()
        raise HTTPException(500, f"Failed to create template: {e}")
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


# ── update an existing row ───────────────────────────────────────────

@router.put("/{template_id}")
def update_template(template_id: int, payload: dict):
    conn = None
    cur = None
    try:
        conn = _connect()
        cur = conn.cursor()

        cur.execute("SELECT id FROM calendar_weekly_template WHERE id = %s", (template_id,))
        if not cur.fetchone():
            raise HTTPException(404, "Template row not found")

        fields = []
        values = []

        for key, validator in [
            ("day_of_week", lambda v: 0 <= int(v) <= 6),
            ("start_hour", lambda v: 0 <= int(v) <= 23),
            ("start_minute", lambda v: 0 <= int(v) <= 59),
            ("duration_minutes", lambda v: int(v) > 0),
        ]:
            if key in payload:
                val = int(payload[key])
                if not validator(val):
                    raise HTTPException(400, f"Invalid {key}")
                fields.append(f"{key} = %s")
                values.append(val)

        if "title" in payload:
            t = (payload["title"] or "").strip()
            if not t:
                raise HTTPException(400, "title cannot be empty")
            fields.append("title = %s")
            values.append(t)
        if "item_kind" in payload:
            fields.append("item_kind = %s")
            values.append((payload["item_kind"] or "note").strip() or "note")
        if "active" in payload:
            fields.append("active = %s")
            values.append(bool(payload["active"]))

        if not fields:
            return {"ok": True, "changed": False}

        values.append(template_id)
        cur.execute(
            f"UPDATE calendar_weekly_template SET {', '.join(fields)} WHERE id = %s",
            tuple(values),
        )
        conn.commit()
        return {"ok": True, "changed": True}

    except HTTPException:
        if conn:
            conn.rollback()
        raise
    except Exception as e:
        if conn:
            conn.rollback()
        raise HTTPException(500, f"Failed to update template: {e}")
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


# ── delete a row ─────────────────────────────────────────────────────

@router.delete("/{template_id}")
def delete_template(template_id: int):
    conn = None
    cur = None
    try:
        conn = _connect()
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM calendar_weekly_template WHERE id = %s RETURNING id",
            (template_id,),
        )
        if not cur.fetchone():
            raise HTTPException(404, "Template row not found")
        conn.commit()
        return {"ok": True, "deleted": template_id}
    except HTTPException:
        if conn:
            conn.rollback()
        raise
    except Exception as e:
        if conn:
            conn.rollback()
        raise HTTPException(500, f"Failed to delete template: {e}")
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


# ── toggle active ────────────────────────────────────────────────────

@router.patch("/{template_id}/active")
def toggle_active(template_id: int, payload: dict):
    conn = None
    cur = None
    try:
        conn = _connect()
        cur = conn.cursor()

        active = payload.get("active")
        if active is None:
            cur.execute(
                "UPDATE calendar_weekly_template SET active = NOT active WHERE id = %s RETURNING active",
                (template_id,),
            )
        else:
            cur.execute(
                "UPDATE calendar_weekly_template SET active = %s WHERE id = %s RETURNING active",
                (bool(active), template_id),
            )

        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Template row not found")
        conn.commit()
        return {"ok": True, "id": template_id, "active": row[0]}
    except HTTPException:
        if conn:
            conn.rollback()
        raise
    except Exception as e:
        if conn:
            conn.rollback()
        raise HTTPException(500, f"Failed to toggle active: {e}")
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()
