from datetime import date, datetime, time, timedelta
from typing import Optional

import os
import psycopg2
from fastapi import APIRouter, HTTPException, Query


router = APIRouter(prefix="/calendar", tags=["Calendar"])


def _connect():
    return psycopg2.connect(os.getenv("TASKS_URL"), sslmode="require")


def _slot_duration_minutes(start_dt: datetime, end_dt: datetime) -> int:
    return int((end_dt - start_dt).total_seconds() // 60)


def _validate_item_window(start_minute: int, duration_minutes: int, slot_duration: int):
    if start_minute < 0:
        raise HTTPException(400, "start_minute must be >= 0")
    if duration_minutes <= 0:
        raise HTTPException(400, "duration_minutes must be > 0")
    end_minute = start_minute + duration_minutes
    if end_minute > slot_duration:
        raise HTTPException(400, f"Item exceeds slot duration ({slot_duration} min)")


def _ensure_no_overlap(cur, slot_id: int, start_minute: int, duration_minutes: int, exclude_item_id: Optional[int] = None):
    end_minute = start_minute + duration_minutes

    params = [slot_id]
    exclude_sql = ""
    if exclude_item_id is not None:
        exclude_sql = "AND id <> %s"
        params.append(exclude_item_id)

    cur.execute(
        f"""
        SELECT id, title, start_minute, duration_minutes
        FROM calendar_item
        WHERE calendar_slot_id = %s
          {exclude_sql}
        ORDER BY start_minute ASC, id ASC
        """,
        tuple(params),
    )

    for row in cur.fetchall():
        existing_id, existing_title, existing_start, existing_duration = row
        if existing_start is None or existing_duration is None:
            continue
        existing_end = existing_start + existing_duration
        if start_minute < existing_end and end_minute > existing_start:
            raise HTTPException(
                409,
                {
                    "message": "Time overlap detected",
                    "conflict_item_id": existing_id,
                    "conflict_title": existing_title,
                    "conflict_start_minute": existing_start,
                    "conflict_duration_minutes": existing_duration,
                },
            )


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
                i.duration_minutes,
                i.start_minute
            FROM uniq_slots s
            LEFT JOIN LATERAL (
                SELECT id, item_kind, item_ref_id, title, position, duration_minutes, start_minute
                FROM calendar_item ci
                WHERE ci.calendar_slot_id = s.id
                ORDER BY ci.start_minute ASC NULLS FIRST, ci.position ASC, ci.id ASC
            ) i ON TRUE
            ORDER BY s.start_time ASC
            """,
            (day_start, day_end),
        )

        rows = cur.fetchall()
        slots_map = {}

        for r in rows:
            slot_id = r[0]
            if slot_id not in slots_map:
                slots_map[slot_id] = {
                    "slot_id": slot_id,
                    "start_time": r[1].isoformat(),
                    "end_time": r[2].isoformat(),
                    "hour": r[1].hour,
                    "items": [],
                    "item": None,
                }

            if r[3] is not None:
                item_obj = {
                    "id": r[3],
                    "item_kind": r[4],
                    "item_ref_id": r[5],
                    "title": r[6] or "",
                    "position": r[7],
                    "duration_minutes": r[8],
                    "start_minute": r[9] if r[9] is not None else 0,
                }
                slots_map[slot_id]["items"].append(item_obj)

        slots = sorted(slots_map.values(), key=lambda s: s["start_time"])
        for slot in slots:
            if slot["items"]:
                slot["item"] = slot["items"][0]

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
        duration_minutes = int(payload.get("duration_minutes") or 60)
        start_minute = int(payload.get("start_minute") or 0)
        item_id = payload.get("item_id")
        item_id = int(item_id) if item_id is not None else None

        conn = _connect()
        cur = conn.cursor()

        cur.execute("SELECT id, start_time, end_time FROM calendar_slot WHERE id = %s", (slot_id,))
        slot_row = cur.fetchone()
        if slot_row is None:
            raise HTTPException(404, "Calendar slot not found")
        _, slot_start, slot_end = slot_row
        slot_duration = _slot_duration_minutes(slot_start, slot_end)

        if not title:
            if item_id is not None:
                cur.execute(
                    "DELETE FROM calendar_item WHERE id = %s AND calendar_slot_id = %s",
                    (item_id, slot_id),
                )
            else:
                cur.execute(
                    "DELETE FROM calendar_item WHERE calendar_slot_id = %s",
                    (slot_id,),
                )
            conn.commit()
            return {"ok": True, "slot_id": slot_id, "cleared": True}

        _validate_item_window(start_minute, duration_minutes, slot_duration)

        if item_id is not None:
            cur.execute(
                "SELECT id FROM calendar_item WHERE id = %s AND calendar_slot_id = %s",
                (item_id, slot_id),
            )
            row = cur.fetchone()

            if not row:
                raise HTTPException(404, "Calendar item not found in slot")

            _ensure_no_overlap(cur, slot_id, start_minute, duration_minutes, exclude_item_id=item_id)

            cur.execute(
                """
                UPDATE calendar_item
                SET title = %s,
                    item_kind = %s,
                    duration_minutes = %s,
                    start_minute = %s
                WHERE id = %s
                """,
                (title, item_kind, duration_minutes, start_minute, item_id),
            )
        else:
            _ensure_no_overlap(cur, slot_id, start_minute, duration_minutes)

            cur.execute(
                """
                SELECT COALESCE(MAX(position), -1) + 1
                FROM calendar_item
                WHERE calendar_slot_id = %s
                """,
                (slot_id,),
            )
            next_position = cur.fetchone()[0]

            cur.execute(
                """
                INSERT INTO calendar_item (
                    calendar_slot_id,
                    item_kind,
                    title,
                    position,
                    duration_minutes,
                    start_minute
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (slot_id, item_kind, title, next_position, duration_minutes, start_minute),
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
            SELECT id, start_minute, duration_minutes
            FROM calendar_item
            WHERE calendar_slot_id = %s
            ORDER BY start_minute ASC NULLS FIRST, position ASC, id ASC
            LIMIT 1
            """,
            (from_slot_id,),
        )
        from_item = cur.fetchone()

        cur.execute(
            """
            SELECT id, start_minute, duration_minutes
            FROM calendar_item
            WHERE calendar_slot_id = %s
            ORDER BY start_minute ASC NULLS FIRST, position ASC, id ASC
            LIMIT 1
            """,
            (to_slot_id,),
        )
        to_item = cur.fetchone()

        from_item_id = from_item[0] if from_item else None
        to_item_id = to_item[0] if to_item else None

        def has_overlap_in_slot(slot_id: int, start_m: int, dur_m: int, exclude_id: Optional[int] = None) -> bool:
            if start_m is None or dur_m is None:
                return False
            params = [slot_id]
            extra = ""
            if exclude_id is not None:
                extra = " AND id <> %s"
                params.append(exclude_id)

            cur.execute(
                f"""
                SELECT start_minute, duration_minutes
                FROM calendar_item
                WHERE calendar_slot_id = %s
                  {extra}
                """,
                tuple(params),
            )
            new_end = start_m + dur_m
            for ex_start, ex_dur in cur.fetchall():
                if ex_start is None or ex_dur is None:
                    continue
                ex_end = ex_start + ex_dur
                if start_m < ex_end and new_end > ex_start:
                    return True
            return False

        if from_item and has_overlap_in_slot(to_slot_id, from_item[1], from_item[2], exclude_id=to_item_id):
            raise HTTPException(409, "Cannot swap: moved source item would overlap in target slot")

        if to_item and has_overlap_in_slot(from_slot_id, to_item[1], to_item[2], exclude_id=from_item_id):
            raise HTTPException(409, "Cannot swap: moved target item would overlap in source slot")

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
