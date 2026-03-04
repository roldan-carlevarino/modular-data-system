from datetime import date, datetime, time, timedelta
from typing import Optional

import os
import psycopg2
from fastapi import APIRouter, HTTPException, Query


router = APIRouter(prefix="/calendar", tags=["Calendar"])


def _connect():
    return psycopg2.connect(os.getenv("TASKS_URL"), sslmode="require")


def _parse_day(day: Optional[str]) -> date:
    if not day:
        return date.today()
    try:
        return date.fromisoformat(day)
    except ValueError:
        raise HTTPException(400, "Invalid day format, expected YYYY-MM-DD")


def _ensure_day_slots(cur, target_day: date, start_hour: int = 5, end_hour: int = 21):
    for hour in range(start_hour, end_hour + 1):
        start_dt = datetime.combine(target_day, time(hour=hour, minute=0))
        end_dt = start_dt + timedelta(hours=1)

        cur.execute(
            """
            SELECT id
            FROM calendar_slot
            WHERE start_time = %s
              AND end_time = %s
            LIMIT 1
            """,
            (start_dt, end_dt),
        )
        row = cur.fetchone()

        if row is None:
            cur.execute(
                """
                INSERT INTO calendar_slot (start_time, end_time)
                VALUES (%s, %s)
                """,
                (start_dt, end_dt),
            )


@router.get("/day")
def get_day_calendar(day: Optional[str] = Query(default=None)):
    target_day = _parse_day(day)
    day_start = datetime.combine(target_day, time.min)
    day_end = day_start + timedelta(days=1)

    conn = None
    cur = None

    try:
        conn = _connect()
        cur = conn.cursor()

        _ensure_day_slots(cur, target_day)
        conn.commit()

        cur.execute(
            """
            WITH uniq_slots AS (
                SELECT DISTINCT ON (s.start_time, s.end_time)
                    s.id,
                    s.start_time,
                    s.end_time
                FROM calendar_slot s
                WHERE s.start_time >= %s
                  AND s.start_time < %s
                ORDER BY s.start_time, s.end_time, s.id ASC
            )
            SELECT
                s.id,
                s.start_time,
                s.end_time,
                i.id,
                i.item_kind,
                i.item_ref_id,
                i.title,
                i.position,
                i.duration_minutes
            FROM uniq_slots s
            LEFT JOIN LATERAL (
                SELECT id, item_kind, item_ref_id, title, position, duration_minutes
                FROM calendar_item ci
                WHERE ci.calendar_slot_id = s.id
                ORDER BY ci.position ASC, ci.id ASC
                LIMIT 1
            ) i ON TRUE
            ORDER BY s.start_time ASC
            """,
            (day_start, day_end),
        )

        rows = cur.fetchall()
        slots = []

        for r in rows:
            slot = {
                "slot_id": r[0],
                "start_time": r[1].isoformat(),
                "end_time": r[2].isoformat(),
                "hour": r[1].hour,
                "item": None,
            }
            if r[3] is not None:
                slot["item"] = {
                    "id": r[3],
                    "item_kind": r[4],
                    "item_ref_id": r[5],
                    "title": r[6] or "",
                    "position": r[7],
                    "duration_minutes": r[8],
                }
            slots.append(slot)

        return {"day": target_day.isoformat(), "slots": slots}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Failed to load calendar day: {str(e)}")
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


@router.post("/slot/{slot_id}/item")
def upsert_slot_item(slot_id: int, payload: dict):
    conn = None
    cur = None

    try:
        title = (payload.get("title") or "").strip()
        item_kind = (payload.get("item_kind") or "note").strip() or "note"
        duration_minutes = payload.get("duration_minutes")

        conn = _connect()
        cur = conn.cursor()

        cur.execute("SELECT id FROM calendar_slot WHERE id = %s", (slot_id,))
        if cur.fetchone() is None:
            raise HTTPException(404, "Calendar slot not found")

        if not title:
            cur.execute(
                "DELETE FROM calendar_item WHERE calendar_slot_id = %s",
                (slot_id,),
            )
            conn.commit()
            return {"ok": True, "slot_id": slot_id, "cleared": True}

        cur.execute(
            """
            SELECT id
            FROM calendar_item
            WHERE calendar_slot_id = %s
            ORDER BY position ASC, id ASC
            LIMIT 1
            """,
            (slot_id,),
        )
        row = cur.fetchone()

        if row:
            item_id = row[0]
            cur.execute(
                """
                UPDATE calendar_item
                SET title = %s,
                    item_kind = %s,
                    duration_minutes = %s
                WHERE id = %s
                """,
                (title, item_kind, duration_minutes, item_id),
            )
        else:
            cur.execute(
                """
                INSERT INTO calendar_item (
                    calendar_slot_id,
                    item_kind,
                    title,
                    position,
                    duration_minutes
                )
                VALUES (%s, %s, %s, 0, %s)
                RETURNING id
                """,
                (slot_id, item_kind, title, duration_minutes),
            )
            item_id = cur.fetchone()[0]

        conn.commit()
        return {"ok": True, "slot_id": slot_id, "item_id": item_id}

    except HTTPException:
        if conn:
            conn.rollback()
        raise
    except Exception as e:
        if conn:
            conn.rollback()
        raise HTTPException(500, f"Failed to save calendar item: {str(e)}")
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


@router.post("/swap")
def swap_slot_items(payload: dict):
    conn = None
    cur = None

    try:
        from_slot_id = int(payload.get("from_slot_id"))
        to_slot_id = int(payload.get("to_slot_id"))

        if from_slot_id == to_slot_id:
            return {"ok": True, "swapped": False}

        conn = _connect()
        cur = conn.cursor()

        cur.execute(
            "SELECT id FROM calendar_slot WHERE id IN (%s, %s)",
            (from_slot_id, to_slot_id),
        )
        slot_rows = cur.fetchall()
        if len(slot_rows) != 2:
            raise HTTPException(404, "One or both calendar slots not found")

        cur.execute(
            """
            SELECT id
            FROM calendar_item
            WHERE calendar_slot_id = %s
            ORDER BY position ASC, id ASC
            LIMIT 1
            """,
            (from_slot_id,),
        )
        from_item = cur.fetchone()

        cur.execute(
            """
            SELECT id
            FROM calendar_item
            WHERE calendar_slot_id = %s
            ORDER BY position ASC, id ASC
            LIMIT 1
            """,
            (to_slot_id,),
        )
        to_item = cur.fetchone()

        from_item_id = from_item[0] if from_item else None
        to_item_id = to_item[0] if to_item else None

        if from_item_id and to_item_id:
            cur.execute(
                """
                UPDATE calendar_item
                SET calendar_slot_id = CASE
                    WHEN id = %s THEN %s
                    WHEN id = %s THEN %s
                    ELSE calendar_slot_id
                END
                WHERE id IN (%s, %s)
                """,
                (from_item_id, to_slot_id, to_item_id, from_slot_id, from_item_id, to_item_id),
            )
        elif from_item_id and not to_item_id:
            cur.execute(
                "UPDATE calendar_item SET calendar_slot_id = %s WHERE id = %s",
                (to_slot_id, from_item_id),
            )
        elif to_item_id and not from_item_id:
            cur.execute(
                "UPDATE calendar_item SET calendar_slot_id = %s WHERE id = %s",
                (from_slot_id, to_item_id),
            )

        conn.commit()
        return {
            "ok": True,
            "swapped": True,
            "from_slot_id": from_slot_id,
            "to_slot_id": to_slot_id,
        }

    except HTTPException:
        if conn:
            conn.rollback()
        raise
    except Exception as e:
        if conn:
            conn.rollback()
        raise HTTPException(500, f"Failed to swap calendar items: {str(e)}")
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()
