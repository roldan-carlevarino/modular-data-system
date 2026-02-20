from fastapi import APIRouter, HTTPException
import psycopg2
import os

router = APIRouter(prefix="/gym", tags=["Gym"]) 


@router.get("/log")
def get_gym_log():
    conn = psycopg2.connect(os.getenv("TASKS_URL"), sslmode="require")
    cur = conn.cursor()

    cur.execute("""
        SELECT
            gym_routine.id,
            gym_routine.routine,
            gym_log.date
        FROM gym_log 
        JOIN gym_routine ON gym_routine.id = gym_log.routine_id
        ORDER BY gym_log.date DESC, gym_routine.id;
    """)
    rows = cur.fetchall()

    cur.close()
    conn.close()

    return [
        {
            "routine_id": r[0],
            "name": r[1],
            "date": r[2].isoformat()
        }
        for r in rows
    ]