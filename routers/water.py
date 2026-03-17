from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from datetime import date
import psycopg2
import os

from routers.tz import local_today

router = APIRouter(prefix="/water", tags=["Water"]) 


class WaterEventPayload(BaseModel):
    water_increase: int
    water_event: str = "drink"


def _ensure_water_tables(cur):
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS water_day (
            date DATE PRIMARY KEY,
            water INTEGER DEFAULT 0
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS water_log (
            id SERIAL PRIMARY KEY,
            event TEXT,
            date DATE NOT NULL,
            water_increment INTEGER NOT NULL,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
        """
    )


@router.get("/today")
def get_today_water():
    today = local_today()

    conn = None
    cur = None

    try:
        conn = psycopg2.connect(os.getenv("TASKS_URL"), sslmode="require")
        cur = conn.cursor()
        _ensure_water_tables(cur)
        conn.commit()

        cur.execute(
            """
            SELECT COALESCE(water, 0)
            FROM water_day
            WHERE date = %s
            LIMIT 1
            """,
            (today,),
        )

        row = cur.fetchone()
        total_water = int(row[0]) if row and row[0] is not None else 0

        return {
            "date": today.isoformat(),
            "water_total": total_water,
        }
    except Exception as e:
        raise HTTPException(500, f"Failed to load water intake: {str(e)}")
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()



@router.post("/drink")
def event_water(payload: WaterEventPayload):
    
    today = local_today()

    conn = None
    cur = None

    try:
        water_increase = int(payload.water_increase)
        if water_increase <= 0:
            raise HTTPException(400, "water_increase must be > 0")

        water_event = (payload.water_event or "drink").strip() or "drink"

        conn = psycopg2.connect(os.getenv("TASKS_URL"), sslmode="require")
        cur = conn.cursor()
        _ensure_water_tables(cur)


        cur.execute(
            """
            UPDATE water_day
            SET water = COALESCE(water, 0) + %s
            WHERE date = %s
            RETURNING water
            """,
        (water_increase, today),
        )

        updated_row = cur.fetchone()
        if updated_row is None:
            cur.execute(
                """
                INSERT INTO water_day (date, water)
                VALUES (%s, %s)
                RETURNING water
                """,
                (today, water_increase),
            )
            updated_row = cur.fetchone()

        total_water = int(updated_row[0]) if updated_row and updated_row[0] is not None else water_increase

        cur.execute(
            """
            INSERT INTO water_log (event, date, water_increment)
            VALUES (%s, %s, %s)
            """,
            (water_event, today, water_increase),
        )

        conn.commit()

        return {
            "date": today.isoformat(),
            "water_increment": water_increase,
            "water_total": total_water,
            "event": water_event,
        }
    except HTTPException:
        if conn:
            conn.rollback()
        raise
    except Exception as e:
        if conn:
            conn.rollback()
        raise HTTPException(500, f"Failed to update water intake: {str(e)}")
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()