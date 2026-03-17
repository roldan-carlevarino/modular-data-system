import os
import psycopg2
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

_LOCAL_TZ = ZoneInfo("Europe/Amsterdam")
def _local_today() -> date:
    return datetime.now(_LOCAL_TZ).date()


LOOKAHEAD_DAYS = 7  # populate today + next 6 days


def _apply_template_for_day(cur, target_day: date):
    """Create calendar items from the weekly template for a single day.
    Returns the number of items created."""
    weekday = target_day.weekday()

    cur.execute(
        """
        SELECT id, start_hour, start_minute, duration_minutes, title, item_kind
        FROM calendar_weekly_template
        WHERE day_of_week = %s AND active = true
        ORDER BY start_hour, start_minute, id
        """,
        (weekday,),
    )
    templates = cur.fetchall()

    created = 0
    for tpl_id, start_hour, start_minute_offset, duration_minutes, title, item_kind in templates:
        # Compute absolute start/end datetimes
        slot_hour_start = datetime.combine(target_day, time(hour=start_hour))
        slot_hour_end = slot_hour_start + timedelta(hours=1)
        item_start = slot_hour_start + timedelta(minutes=start_minute_offset)
        item_end = item_start + timedelta(minutes=duration_minutes)

        # Ensure the anchor slot exists
        cur.execute(
            """
            SELECT id FROM calendar_slot
            WHERE start_time = %s AND end_time = %s
            LIMIT 1
            """,
            (slot_hour_start, slot_hour_end),
        )
        slot_row = cur.fetchone()
        if slot_row:
            slot_id = slot_row[0]
        else:
            cur.execute(
                """
                INSERT INTO calendar_slot (start_time, end_time)
                VALUES (%s, %s)
                RETURNING id
                """,
                (slot_hour_start, slot_hour_end),
            )
            slot_id = cur.fetchone()[0]

        # Skip if an item with the same title already exists in that time window
        cur.execute(
            """
            SELECT id FROM calendar_item
            WHERE title = %s
              AND start_time = %s
              AND end_time = %s
            LIMIT 1
            """,
            (title, item_start, item_end),
        )
        if cur.fetchone():
            continue  # already present

        # Find next position in the slot
        cur.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 FROM calendar_item WHERE calendar_slot_id = %s",
            (slot_id,),
        )
        next_pos = cur.fetchone()[0]

        cur.execute(
            """
            INSERT INTO calendar_item (
                calendar_slot_id, item_kind, title, position,
                duration_minutes, start_minute, start_time, end_time,
                featured
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, false)
            """,
            (
                slot_id,
                item_kind,
                title,
                next_pos,
                duration_minutes,
                start_minute_offset,
                item_start,
                item_end,
            ),
        )
        created += 1

    return created


def create_daily_calendar():
    """
    Cron job that runs once per day.
    Applies the weekly template for today + the next LOOKAHEAD_DAYS-1 days,
    creating calendar_item entries where they don't already exist.
    """
    conn = None
    cur = None

    try:
        conn = psycopg2.connect(os.getenv("TASKS_URL"), sslmode="require")
        cur = conn.cursor()

        today = _local_today()
        total_created = 0

        for offset in range(LOOKAHEAD_DAYS):
            target_day = today + timedelta(days=offset)
            total_created += _apply_template_for_day(cur, target_day)

        conn.commit()

        # Log success
        cur.execute(
            """
            INSERT INTO crons_log (cron_name, status, message, timestamp)
            VALUES (%s, %s, %s, NOW())
            """,
            (
                "create_daily_calendar",
                "success",
                f"Created {total_created} item(s) over {LOOKAHEAD_DAYS} days",
            ),
        )
        conn.commit()

    except psycopg2.Error as e:
        if conn:
            conn.rollback()
        try:
            cur.execute(
                """
                INSERT INTO crons_log (cron_name, status, message, error, timestamp)
                VALUES (%s, %s, %s, %s, NOW())
                """,
                ("create_daily_calendar", "error", "Database error", str(e)),
            )
            conn.commit()
        except Exception:
            pass
        print(f"Database error in create_daily_calendar: {e}")
        raise

    except Exception as e:
        if conn:
            conn.rollback()
        try:
            cur.execute(
                """
                INSERT INTO crons_log (cron_name, status, message, error, timestamp)
                VALUES (%s, %s, %s, %s, NOW())
                """,
                ("create_daily_calendar", "error", "General error", str(e)),
            )
            conn.commit()
        except Exception:
            pass
        print(f"Error in create_daily_calendar: {e}")
        raise

    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    create_daily_calendar()
    print("Daily calendar items created")
