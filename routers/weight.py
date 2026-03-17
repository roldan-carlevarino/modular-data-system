from datetime import date
import os

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import psycopg2

from routers.tz import local_today


router: APIRouter = APIRouter(prefix="/weight", tags=["Weight"])


class WeightEventPayload(BaseModel):
    weight: int


def _ensure_weight_table(cur):
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS weight_log (
            id SERIAL PRIMARY KEY,
            date DATE NOT NULL,
            weight INTEGER NOT NULL,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
        """
    )


@router.get("/today")
def get_today_weight():
    today = local_today()

    conn = None
    cur = None

    try:
        conn = psycopg2.connect(os.getenv("TASKS_URL"), sslmode="require")
        cur = conn.cursor()
        _ensure_weight_table(cur)
        conn.commit()

        cur.execute(
            """
            SELECT weight
            FROM weight_log
            WHERE date = %s
            ORDER BY date DESC
            LIMIT 1
            """,
            (today,),
        )

        row = cur.fetchone()
        current_weight = int(row[0]) if row and row[0] is not None else 0

        return {
            "date": today.isoformat(),
            "weight": current_weight,
        }
    except Exception as e:
        raise HTTPException(500, f"Failed to load weight: {str(e)}")
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


@router.post("/new")
def add_new_weight(payload: WeightEventPayload):
    today = local_today()

    conn = None
    cur = None

    try:
        if int(payload.weight) <= 0:
            raise HTTPException(400, "weight must be > 0")

        conn = psycopg2.connect(os.getenv("TASKS_URL"), sslmode="require")
        cur = conn.cursor()
        _ensure_weight_table(cur)

        cur.execute(
            """
            INSERT INTO weight_log (date, weight)
            VALUES (%s, %s)
            """,
            (today, int(payload.weight)),
        )

        conn.commit()

        return {
            "status": "success",
            "date": today.isoformat(),
            "weight": int(payload.weight),
        }
    except HTTPException:
        if conn:
            conn.rollback()
        raise
    except Exception as e:
        if conn:
            conn.rollback()
        raise HTTPException(500, f"Failed to add weight: {str(e)}")
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


__all__ = ["router"]