from fastapi import APIRouter, HTTPException
import psycopg2
import os
from datetime import date, datetime

from routers.tz import local_today, local_now

router = APIRouter(prefix="/tasks", tags=["Tasks"]) 


def _current_occurrence_by_hour(hour: int) -> str:
    if hour < 12:
        return "morning"
    if hour < 18:
        return "afternoon"
    return "evening"


@router.get("/today")
def get_tasks_today():
    try:
        conn = psycopg2.connect(os.getenv("TASKS_URL"), sslmode="require")
        cur = conn.cursor()

        today = local_today()

        cur.execute("""
            SELECT
                task_occurrences.id,
                task.name,
                task_occurrences.date,
                task_occurrences.completed,
                task_occurrences.position,
                task_occurrences.occurrence
            FROM task_occurrences 
            JOIN task ON task.id = task_occurrences.task_id
            WHERE task_occurrences.date = %s
            ORDER BY task_occurrences.position;
        """, (today,))

        rows = cur.fetchall()

        cur.close()
        conn.close()

        return [
            {
                "occurrences_id": r[0],
                "name": r[1],
                "date": r[2].isoformat(),
                "completed": r[3],
                "position": r[4],
                "day_context": r[5]
            }
            for r in rows
        ]

    except Exception as e:
        
        raise


@router.get("/today/current-occurrence")
def get_tasks_today_current_occurrence():
    conn = None
    cur = None

    try:
        conn = psycopg2.connect(os.getenv("TASKS_URL"), sslmode="require")
        cur = conn.cursor()

        today = local_today()
        occurrence = _current_occurrence_by_hour(local_now().hour)

        cur.execute(
            """
            SELECT
                task_occurrences.id,
                task.name,
                task_occurrences.completed,
                task_occurrences.position,
                task_occurrences.occurrence
            FROM task_occurrences
            JOIN task ON task.id = task_occurrences.task_id
            WHERE task_occurrences.date = %s
              AND task_occurrences.occurrence = %s
            ORDER BY task_occurrences.position;
            """,
            (today, occurrence),
        )

        rows = cur.fetchall()

        return {
            "occurrence": occurrence,
            "tasks": [
                {
                    "occurrences_id": r[0],
                    "name": r[1],
                    "completed": r[2],
                    "position": r[3],
                    "day_context": r[4],
                }
                for r in rows
            ],
        }

    except Exception as e:
        raise HTTPException(500, f"Current-occurrence fetch failed: {str(e)}")

    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

@router.post("/today/refresh_occurrences")
def refresh_tasks_today():
    conn = None
    cur = None
    
    try:
        conn = psycopg2.connect(os.getenv("TASKS_URL"), sslmode="require")
        cur = conn.cursor()
        
        actual_hour = local_now().hour
        today = local_today()
        
        # Determinar transición
        transitions = []
        if 12 <= actual_hour < 18:
            transitions.append(("morning", "afternoon"))
        if 18 <= actual_hour <= 23:
            transitions.append(("afternoon", "evening"))
        
        for from_occ, to_occ in transitions:
            cur.execute("""
                UPDATE task_occurrences
                SET occurrence = %s,
                    intraday_spill = GREATEST(0, intraday_spill - 1)
                WHERE occurrence = %s
                  AND completed = false
                  AND intraday_spill > 0
                  AND date = %s
            """, (to_occ, from_occ, today))
        
        conn.commit()
        return {"ok": True, "transitions": len(transitions)}
        
    except Exception as e:
        if conn:
            conn.rollback()
        raise HTTPException(500, f"Refresh failed: {str(e)}")
        
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()



@router.patch("/today/move")
def move_task_today(payload: dict):
    conn = None
    cur = None
    
    try:
        occurrences_id = int(payload["occurrences_id"])              
        before_id = payload.get("before_id")          
        after_id = payload.get("after_id")
        target_occurrence = payload.get("target_occurrence")

        conn = psycopg2.connect(os.getenv("TASKS_URL"), sslmode="require")
        cur = conn.cursor()
        today = local_today()

        STEP = 20

        cur.execute("""
            SELECT position, occurrence
            FROM task_occurrences
            WHERE id = %s
              AND date = %s;
        """, (occurrences_id, today))
        row = cur.fetchone()

        if not row:
            raise HTTPException(404, "Task occurrence not found")

        _, old_occurrence = row

        final_occurrence = target_occurrence or old_occurrence

        pos_before = None
        if before_id is not None:
            if before_id == occurrences_id:
                raise HTTPException(400, "Invalid before_id")

            cur.execute("""
                SELECT position
                FROM task_occurrences
                WHERE id = %s
                  AND date = %s
                  AND occurrence = %s;
            """, (before_id, today, final_occurrence))
            r = cur.fetchone()
            if not r:
                raise HTTPException(400, "before_id not valid in target occurrence")
            pos_before = r[0]

        pos_after = None
        if after_id is not None:
            if after_id == occurrences_id:
                raise HTTPException(400, "Invalid after_id")

            cur.execute("""
                SELECT position
                FROM task_occurrences
                WHERE id = %s
                  AND date = %s
                  AND occurrence = %s;
            """, (after_id, today, final_occurrence))
            r = cur.fetchone()
            if not r:
                raise HTTPException(400, "after_id not valid in target occurrence")
            pos_after = r[0]

        if pos_before is not None and pos_after is not None:
            new_position = (pos_before + pos_after) / 2
        elif pos_before is not None:
            new_position = pos_before + STEP
        elif pos_after is not None:
            new_position = pos_after / 2
        else:
            new_position = STEP

        cur.execute("""
            UPDATE task_occurrences
            SET occurrence = %s,
                position = %s
            WHERE id = %s;
        """, (final_occurrence, new_position, occurrences_id))

        conn.commit()
        
        return {
            "ok": True,
            "occurrence": final_occurrence,
            "position": new_position
        }
        
    except HTTPException:
        if conn:
            conn.rollback()
        raise
        
    except Exception as e:
        if conn:
            conn.rollback()
        raise HTTPException(500, f"Move failed: {str(e)}")
        
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()








    

# @app.get("/task/yesterday")
# REVISAR ESTO

@router.post("/today/checkbox")
def update_task_today(payload: dict):

    occurrences_id = int(payload["occurrences_id"])
    completed = bool(payload["completed"])

    conn = psycopg2.connect(os.getenv("TASKS_URL"), sslmode="require")
    cur = conn.cursor()

    today = local_today()

    cur.execute("""
        UPDATE task_occurrences
        SET completed = %s
        WHERE id = %s
          AND date = %s;
    """, (completed, occurrences_id, today))

    conn.commit()
    cur.close()
    conn.close()

    return {"ok": True}