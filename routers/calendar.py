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


def _parse_iso_dt(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except Exception:
        raise HTTPException(400, "Invalid datetime format, expected ISO 8601")


def _slot_duration_minutes(start_dt: datetime, end_dt: datetime) -> int:
    return int((end_dt - start_dt).total_seconds() // 60)


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


def _ensure_no_time_overlap(cur, start_dt: datetime, end_dt: datetime, exclude_item_id: Optional[int] = None):
    params = [start_dt, end_dt]
    extra = ""
    if exclude_item_id is not None:
        extra = "AND ci.id <> %s"
        params.append(exclude_item_id)

    cur.execute(
        f"""
        SELECT ci.id, ci.title
        FROM calendar_item ci
        LEFT JOIN calendar_slot cs ON cs.id = ci.calendar_slot_id
        WHERE (
            COALESCE(
                ci.start_time,
                cs.start_time + make_interval(mins => COALESCE(ci.start_minute, 0))
            ) < %s
        )
          AND (
            COALESCE(
                ci.end_time,
                COALESCE(
                    ci.start_time,
                    cs.start_time + make_interval(mins => COALESCE(ci.start_minute, 0))
                ) + make_interval(mins => COALESCE(ci.duration_minutes, 60))
            ) > %s
        )
          {extra}
        LIMIT 1
        """,
        tuple(params),
    )

    conflict = cur.fetchone()
    if conflict:
        raise HTTPException(
            409,
            {
                "message": "Time overlap detected",
                "conflict_item_id": conflict[0],
                "conflict_title": conflict[1],
            },
        )


def _first_item_in_slot(cur, slot_id: int):
    cur.execute(
        """
        SELECT id, start_time, end_time, start_minute, duration_minutes, title
        FROM calendar_item
        WHERE calendar_slot_id = %s
        ORDER BY start_minute ASC NULLS FIRST, position ASC, id ASC
        LIMIT 1
        """,
        (slot_id,),
    )
    return cur.fetchone()


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
                i.start_minute,
                i.start_time,
                i.end_time
            FROM uniq_slots s
            LEFT JOIN LATERAL (
                SELECT
                    ci.id,
                    ci.item_kind,
                    ci.item_ref_id,
                    ci.title,
                    ci.position,
                    ci.duration_minutes,
                    ci.start_minute,
                    ci.start_time,
                    ci.end_time
                FROM calendar_item ci
                WHERE (
                    ci.start_time IS NOT NULL
                    AND ci.end_time IS NOT NULL
                    AND ci.start_time < s.end_time
                    AND ci.end_time > s.start_time
                )
                   OR (
                    ci.start_time IS NULL
                    AND ci.end_time IS NULL
                    AND ci.calendar_slot_id = s.id
                )
                ORDER BY
                    COALESCE(ci.start_time, s.start_time + make_interval(mins => COALESCE(ci.start_minute, 0))) ASC,
                    ci.position ASC,
                    ci.id ASC
            ) i ON TRUE
            ORDER BY s.start_time ASC
            """,
            (day_start, day_end),
        )

        rows = cur.fetchall()
        slots_map = {}

        for r in rows:
            slot_id = r[0]
            slot_start = r[1]
            slot_end = r[2]

            if slot_id not in slots_map:
                slots_map[slot_id] = {
                    "slot_id": slot_id,
                    "start_time": slot_start.isoformat(),
                    "end_time": slot_end.isoformat(),
                    "hour": slot_start.hour,
                    "items": [],
                    "item": None,
                }

            if r[3] is None:
                continue

            item_start_dt = r[10]
            item_end_dt = r[11]
            if item_start_dt is not None:
                rel_start = int((item_start_dt - slot_start).total_seconds() // 60)
                start_minute = max(0, rel_start)
            else:
                start_minute = r[9] if r[9] is not None else 0

            duration_minutes = r[8]
            if duration_minutes is None and item_start_dt is not None and item_end_dt is not None:
                duration_minutes = int((item_end_dt - item_start_dt).total_seconds() // 60)

            item_obj = {
                "id": r[3],
                "item_kind": r[4],
                "item_ref_id": r[5],
                "title": r[6] or "",
                "position": r[7],
                "duration_minutes": duration_minutes,
                "start_minute": start_minute,
                "start_time": item_start_dt.isoformat() if item_start_dt else None,
                "end_time": item_end_dt.isoformat() if item_end_dt else None,
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


@router.get("/month-summary")
def get_month_summary(year: int = Query(...), month: int = Query(...)):
    if month < 1 or month > 12:
        raise HTTPException(400, "month must be between 1 and 12")

    conn = None
    cur = None

    try:
        month_start = datetime(year, month, 1)
        if month == 12:
            month_end = datetime(year + 1, 1, 1)
        else:
            month_end = datetime(year, month + 1, 1)

        conn = _connect()
        cur = conn.cursor()

        cur.execute(
            """
            WITH day_series AS (
                SELECT generate_series(%s::date, (%s::date - INTERVAL '1 day')::date, INTERVAL '1 day')::date AS day
            ),
            item_times AS (
                SELECT
                    ci.id,
                    COALESCE(
                        ci.start_time,
                        cs.start_time + make_interval(mins => COALESCE(ci.start_minute, 0))
                    ) AS item_start,
                    COALESCE(
                        ci.end_time,
                        COALESCE(
                            ci.start_time,
                            cs.start_time + make_interval(mins => COALESCE(ci.start_minute, 0))
                        ) + make_interval(mins => COALESCE(ci.duration_minutes, 60))
                    ) AS item_end
                FROM calendar_item ci
                LEFT JOIN calendar_slot cs ON cs.id = ci.calendar_slot_id
            )
            SELECT
                ds.day,
                COUNT(DISTINCT it.id) AS items_count
            FROM day_series ds
            LEFT JOIN item_times it
              ON it.item_start < (ds.day + INTERVAL '1 day')
             AND it.item_end > ds.day
            GROUP BY ds.day
            ORDER BY ds.day
            """,
            (month_start.date(), month_end.date()),
        )

        rows = cur.fetchall()
        days = [
            {
                "day": r[0].isoformat(),
                "items_count": int(r[1]),
            }
            for r in rows
        ]

        return {
            "year": year,
            "month": month,
            "days": days,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Failed to load month summary: {str(e)}")
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


@router.get("/upcoming")
def get_upcoming_events(days: int = Query(default=7), limit: int = Query(default=12)):
    if days < 1:
        raise HTTPException(400, "days must be >= 1")
    if limit < 1 or limit > 200:
        raise HTTPException(400, "limit must be between 1 and 200")

    conn = None
    cur = None

    try:
        window_start = datetime.now()
        window_end = window_start + timedelta(days=days)

        conn = _connect()
        cur = conn.cursor()

        cur.execute(
            """
            SELECT
                ci.id,
                ci.title,
                ci.item_kind,
                COALESCE(
                    ci.start_time,
                    cs.start_time + make_interval(mins => COALESCE(ci.start_minute, 0))
                ) AS event_start,
                COALESCE(
                    ci.end_time,
                    COALESCE(
                        ci.start_time,
                        cs.start_time + make_interval(mins => COALESCE(ci.start_minute, 0))
                    ) + make_interval(mins => COALESCE(ci.duration_minutes, 60))
                ) AS event_end,
                COALESCE(ci.duration_minutes, 60) AS duration_minutes
            FROM calendar_item ci
            LEFT JOIN calendar_slot cs ON cs.id = ci.calendar_slot_id
            WHERE COALESCE(
                    ci.end_time,
                    COALESCE(
                        ci.start_time,
                        cs.start_time + make_interval(mins => COALESCE(ci.start_minute, 0))
                    ) + make_interval(mins => COALESCE(ci.duration_minutes, 60))
                  ) >= %s
              AND COALESCE(
                    ci.start_time,
                    cs.start_time + make_interval(mins => COALESCE(ci.start_minute, 0))
                  ) < %s
            ORDER BY event_start ASC
            LIMIT %s
            """,
            (window_start, window_end, limit),
        )

        rows = cur.fetchall()
        events = [
            {
                "id": r[0],
                "title": r[1] or "(untitled event)",
                "item_kind": r[2],
                "start_time": r[3].isoformat() if r[3] else None,
                "end_time": r[4].isoformat() if r[4] else None,
                "duration_minutes": r[5],
            }
            for r in rows
        ]

        return {
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
            "count": len(events),
            "events": events,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Failed to load upcoming events: {str(e)}")
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

        provided_start_time = payload.get("start_time")
        provided_end_time = payload.get("end_time")

        conn = _connect()
        cur = conn.cursor()

        cur.execute("SELECT id, start_time, end_time FROM calendar_slot WHERE id = %s", (slot_id,))
        slot_row = cur.fetchone()
        if slot_row is None:
            raise HTTPException(404, "Calendar slot not found")

        _, slot_start, slot_end = slot_row
        slot_duration = _slot_duration_minutes(slot_start, slot_end)
        if start_minute < 0 or start_minute >= slot_duration:
            raise HTTPException(400, f"start_minute must be between 0 and {slot_duration - 1}")
        if duration_minutes <= 0:
            raise HTTPException(400, "duration_minutes must be > 0")

        if not title:
            if item_id is not None:
                cur.execute(
                    "DELETE FROM calendar_item WHERE id = %s AND calendar_slot_id = %s",
                    (item_id, slot_id),
                )
            else:
                cur.execute("DELETE FROM calendar_item WHERE calendar_slot_id = %s", (slot_id,))
            conn.commit()
            return {"ok": True, "slot_id": slot_id, "cleared": True}

        if provided_start_time and provided_end_time:
            start_dt = _parse_iso_dt(provided_start_time)
            end_dt = _parse_iso_dt(provided_end_time)
            if end_dt <= start_dt:
                raise HTTPException(400, "end_time must be greater than start_time")
            duration_minutes = int((end_dt - start_dt).total_seconds() // 60)
            rel_minutes = int((start_dt - slot_start).total_seconds() // 60)
            start_minute = max(0, rel_minutes)
        else:
            start_dt = slot_start + timedelta(minutes=start_minute)
            end_dt = start_dt + timedelta(minutes=duration_minutes)

        _ensure_no_time_overlap(cur, start_dt, end_dt, exclude_item_id=item_id)

        if item_id is not None:
            cur.execute(
                "SELECT id FROM calendar_item WHERE id = %s AND calendar_slot_id = %s",
                (item_id, slot_id),
            )
            if not cur.fetchone():
                raise HTTPException(404, "Calendar item not found in slot")

            cur.execute(
                """
                UPDATE calendar_item
                SET title = %s,
                    item_kind = %s,
                    duration_minutes = %s,
                    start_minute = %s,
                    start_time = %s,
                    end_time = %s
                WHERE id = %s
                """,
                (title, item_kind, duration_minutes, start_minute, start_dt, end_dt, item_id),
            )
        else:
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
                    start_minute,
                    start_time,
                    end_time
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (slot_id, item_kind, title, next_position, duration_minutes, start_minute, start_dt, end_dt),
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
            "SELECT id, start_time, end_time FROM calendar_slot WHERE id IN (%s, %s) ORDER BY id",
            (from_slot_id, to_slot_id),
        )
        slot_rows = cur.fetchall()
        if len(slot_rows) != 2:
            raise HTTPException(404, "One or both calendar slots not found")

        slot_by_id = {r[0]: {"start": r[1], "end": r[2]} for r in slot_rows}
        from_slot = slot_by_id[from_slot_id]
        to_slot = slot_by_id[to_slot_id]

        from_item = _first_item_in_slot(cur, from_slot_id)
        to_item = _first_item_in_slot(cur, to_slot_id)

        if not from_item and not to_item:
            return {"ok": True, "swapped": False}

        def _target_times(item_row, source_slot_start: datetime, target_slot_start: datetime):
            item_id, item_start_dt, item_end_dt, item_start_minute, item_duration, _title = item_row
            duration = item_duration
            if duration is None and item_start_dt and item_end_dt:
                duration = int((item_end_dt - item_start_dt).total_seconds() // 60)
            if duration is None:
                duration = 60

            if item_start_dt is not None:
                offset = int((item_start_dt - source_slot_start).total_seconds() // 60)
            else:
                offset = item_start_minute if item_start_minute is not None else 0

            new_start = target_slot_start + timedelta(minutes=offset)
            new_end = new_start + timedelta(minutes=duration)
            new_start_min = max(0, offset)
            return item_id, new_start, new_end, new_start_min, duration

        from_new = _target_times(from_item, from_slot["start"], to_slot["start"]) if from_item else None
        to_new = _target_times(to_item, to_slot["start"], from_slot["start"]) if to_item else None

        if from_new:
            _ensure_no_time_overlap(cur, from_new[1], from_new[2], exclude_item_id=from_new[0])
        if to_new:
            _ensure_no_time_overlap(cur, to_new[1], to_new[2], exclude_item_id=to_new[0])

        if from_new:
            cur.execute(
                """
                UPDATE calendar_item
                SET calendar_slot_id = %s,
                    start_time = %s,
                    end_time = %s,
                    start_minute = %s,
                    duration_minutes = %s
                WHERE id = %s
                """,
                (to_slot_id, from_new[1], from_new[2], from_new[3], from_new[4], from_new[0]),
            )

        if to_new:
            cur.execute(
                """
                UPDATE calendar_item
                SET calendar_slot_id = %s,
                    start_time = %s,
                    end_time = %s,
                    start_minute = %s,
                    duration_minutes = %s
                WHERE id = %s
                """,
                (from_slot_id, to_new[1], to_new[2], to_new[3], to_new[4], to_new[0]),
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
