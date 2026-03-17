from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List
import psycopg2
import os
from datetime import date

from routers.tz import local_today

router: APIRouter = APIRouter(prefix="/gym", tags=["Gym"])
__all__ = ["router"]


# ---------- Pydantic Models ----------

class ExerciseLogCreate(BaseModel):
    routine_exercise_id: int
    position: Optional[int] = None
    notes: Optional[str] = None


class SetCreate(BaseModel):
    set_number: int
    weight: Optional[float] = None
    reps: Optional[int] = None
    rir: Optional[int] = None
    notes: Optional[str] = None


class SessionCreate(BaseModel):
    routine_id: int
    duration_minutes: Optional[int] = None
    feeling: Optional[str] = None
    notes: Optional[str] = None


# ---------- Helper ----------

def _get_conn():
    return psycopg2.connect(os.getenv("TASKS_URL"), sslmode="require")


# ==========================================================
#                    DASHBOARD ENDPOINTS
# ==========================================================

# GET /gym/routines - Get all routines
@router.get("/routines")
def get_routines():
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, routine, status, reason
        FROM gym_routine
        ORDER BY id
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [
        {"id": r[0], "routine": r[1], "status": r[2], "reason": r[3]}
        for r in rows
    ]


# GET /gym/routines/{routine_id} - Get routine with its exercises
@router.get("/routines/{routine_id}")
def get_routine(routine_id: int, weekday: Optional[int] = None):
    conn = _get_conn()
    cur = conn.cursor()
    
    # Get routine info
    cur.execute("SELECT id, routine, status, reason FROM gym_routine WHERE id = %s", (routine_id,))
    routine = cur.fetchone()
    if not routine:
        cur.close()
        conn.close()
        raise HTTPException(status_code=404, detail="Routine not found")
    
    # Get exercises for this routine (optionally filtered by weekday)
    if weekday is not None:
        cur.execute("""
            SELECT id, exercise, series, reps, position, weekday
            FROM gym_routine_exercise
            WHERE routine_id = %s AND weekday = %s
            ORDER BY position
        """, (routine_id, weekday))
    else:
        cur.execute("""
            SELECT id, exercise, series, reps, position, weekday
            FROM gym_routine_exercise
            WHERE routine_id = %s
            ORDER BY position
        """, (routine_id,))
    exercises = cur.fetchall()
    
    cur.close()
    conn.close()
    
    return {
        "id": routine[0],
        "routine": routine[1],
        "status": routine[2],
        "reason": routine[3],
        "exercises": [
            {"id": e[0], "exercise": e[1], "series": e[2], "reps": e[3], "position": e[4], "weekday": e[5]}
            for e in exercises
        ]
    }


# GET /gym/exercises/{routine_exercise_id}/history - Get history of a specific exercise
@router.get("/exercises/{routine_exercise_id}/history")
def get_exercise_history(routine_exercise_id: int):
    conn = _get_conn()
    cur = conn.cursor()
    
    # Get the exercise name first
    cur.execute("SELECT exercise FROM gym_routine_exercise WHERE id = %s", (routine_exercise_id,))
    ex = cur.fetchone()
    if not ex:
        cur.close()
        conn.close()
        raise HTTPException(status_code=404, detail="Exercise not found")
    
    # Get all logged sets for this exercise
    cur.execute("""
        SELECT 
            s.date,
            ls.set_number,
            ls.weight,
            ls.reps,
            ls.rir,
            ls.notes
        FROM gym_log_set ls
        JOIN gym_log_exercise le ON le.id = ls.exercise_log_id
        JOIN gym_log_session s ON s.id = le.log_session_id
        WHERE le.routine_exercise_id = %s
        ORDER BY s.date DESC, ls.set_number
    """, (routine_exercise_id,))
    rows = cur.fetchall()
    
    cur.close()
    conn.close()
    
    return {
        "exercise": ex[0],
        "routine_exercise_id": routine_exercise_id,
        "history": [
            {
                "date": r[0].isoformat(),
                "set_number": r[1],
                "weight": float(r[2]) if r[2] else None,
                "reps": r[3],
                "rir": r[4],
                "notes": r[5]
            }
            for r in rows
        ]
    }


# GET /gym/sessions/{session_id}/exercises - Get exercises for a session
@router.get("/sessions/{session_id}/exercises")
def get_session_exercises(session_id: int):
    conn = _get_conn()
    cur = conn.cursor()
    
    # Verify session exists
    cur.execute("SELECT id, date, routine_id FROM gym_log_session WHERE id = %s", (session_id,))
    session = cur.fetchone()
    if not session:
        cur.close()
        conn.close()
        raise HTTPException(status_code=404, detail="Session not found")
    
    # Get logged exercises with routine exercise info
    cur.execute("""
        SELECT 
            le.id,
            le.routine_exercise_id,
            re.exercise,
            re.series,
            re.reps,
            le.position,
            le.notes
        FROM gym_log_exercise le
        JOIN gym_routine_exercise re ON re.id = le.routine_exercise_id
        WHERE le.log_session_id = %s
        ORDER BY le.position, le.id
    """, (session_id,))
    exercises = cur.fetchall()
    
    cur.close()
    conn.close()
    
    return {
        "session_id": session_id,
        "date": session[1].isoformat(),
        "exercises": [
            {
                "log_id": e[0],
                "routine_exercise_id": e[1],
                "exercise": e[2],
                "target_series": e[3],
                "target_reps": e[4],
                "position": e[5],
                "notes": e[6]
            }
            for e in exercises
        ]
    }


# POST /gym/sessions/{session_id}/exercises - Add exercise to a session
@router.post("/sessions/{session_id}/exercises")
def add_exercise_to_session(session_id: int, payload: ExerciseLogCreate):
    conn = _get_conn()
    cur = conn.cursor()
    
    # Verify session exists
    cur.execute("SELECT id FROM gym_log_session WHERE id = %s", (session_id,))
    if not cur.fetchone():
        cur.close()
        conn.close()
        raise HTTPException(status_code=404, detail="Session not found")
    
    cur.execute("""
        INSERT INTO gym_log_exercise (log_session_id, routine_exercise_id, position, notes)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (log_session_id, routine_exercise_id) 
        DO UPDATE SET position = EXCLUDED.position, notes = EXCLUDED.notes
        RETURNING id
    """, (session_id, payload.routine_exercise_id, payload.position, payload.notes))
    
    new_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()
    
    return {"id": new_id, "message": "Exercise added to session"}


# ==========================================================
#                    T-WATCH ENDPOINTS
# ==========================================================

# GET /gym/sessions/today - Get today's session (or create one)
@router.get("/sessions/today")
def get_today_session(routine_id: Optional[int] = None):
    today = local_today()
    conn = _get_conn()
    cur = conn.cursor()
    
    # Check if session exists for today
    cur.execute("""
        SELECT s.id, s.date, s.routine_id, r.routine, s.duration_minutes, s.feeling, s.notes
        FROM gym_log_session s
        JOIN gym_routine r ON r.id = s.routine_id
        WHERE s.date = %s
        ORDER BY s.id DESC
        LIMIT 1
    """, (today,))
    session = cur.fetchone()
    
    if session:
        # Get exercises for this session
        cur.execute("""
            SELECT 
                le.id,
                le.routine_exercise_id,
                re.exercise,
                re.series,
                re.reps,
                le.position,
                le.notes
            FROM gym_log_exercise le
            JOIN gym_routine_exercise re ON re.id = le.routine_exercise_id
            WHERE le.log_session_id = %s
            ORDER BY le.position, le.id
        """, (session[0],))
        exercises = cur.fetchall()
        
        cur.close()
        conn.close()
        
        return {
            "id": session[0],
            "date": session[1].isoformat(),
            "routine_id": session[2],
            "routine": session[3],
            "duration_minutes": session[4],
            "feeling": session[5],
            "notes": session[6],
            "exercises": [
                {
                    "log_id": e[0],
                    "routine_exercise_id": e[1],
                    "exercise": e[2],
                    "target_series": e[3],
                    "target_reps": e[4],
                    "position": e[5],
                    "notes": e[6]
                }
                for e in exercises
            ]
        }
    
    # No session today - if routine_id provided, create one
    if routine_id:
        cur.execute("""
            INSERT INTO gym_log_session (date, routine_id)
            VALUES (%s, %s)
            RETURNING id
        """, (today, routine_id))
        new_id = cur.fetchone()[0]
        conn.commit()
        
        # Get routine name
        cur.execute("SELECT routine FROM gym_routine WHERE id = %s", (routine_id,))
        routine_name = cur.fetchone()[0]
        
        cur.close()
        conn.close()
        
        return {
            "id": new_id,
            "date": today.isoformat(),
            "routine_id": routine_id,
            "routine": routine_name,
            "duration_minutes": None,
            "feeling": None,
            "notes": None,
            "exercises": []
        }
    
    cur.close()
    conn.close()
    return {"session": None, "message": "No session today. Provide routine_id to create one."}


# POST /gym/sessions/today/exercises - Add exercise to today's session
@router.post("/sessions/today/exercises")
def add_exercise_to_today(payload: ExerciseLogCreate):
    today = local_today()
    conn = _get_conn()
    cur = conn.cursor()
    
    # Get today's session
    cur.execute("SELECT id FROM gym_log_session WHERE date = %s ORDER BY id DESC LIMIT 1", (today,))
    session = cur.fetchone()
    
    if not session:
        cur.close()
        conn.close()
        raise HTTPException(status_code=404, detail="No session today. Create one first via GET /gym/sessions/today?routine_id=X")
    
    session_id = session[0]
    
    cur.execute("""
        INSERT INTO gym_log_exercise (log_session_id, routine_exercise_id, position, notes)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (log_session_id, routine_exercise_id) 
        DO UPDATE SET position = EXCLUDED.position, notes = EXCLUDED.notes
        RETURNING id
    """, (session_id, payload.routine_exercise_id, payload.position, payload.notes))
    
    new_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()
    
    return {"id": new_id, "session_id": session_id, "message": "Exercise added"}


# GET /gym/log-exercises/{exercise_log_id}/sets - Get sets for an exercise log
@router.get("/log-exercises/{exercise_log_id}/sets")
def get_exercise_sets(exercise_log_id: int):
    conn = _get_conn()
    cur = conn.cursor()
    
    # Get exercise info
    cur.execute("""
        SELECT le.id, re.exercise
        FROM gym_log_exercise le
        JOIN gym_routine_exercise re ON re.id = le.routine_exercise_id
        WHERE le.id = %s
    """, (exercise_log_id,))
    ex = cur.fetchone()
    
    if not ex:
        cur.close()
        conn.close()
        raise HTTPException(status_code=404, detail="Exercise log not found")
    
    # Get sets
    cur.execute("""
        SELECT id, set_number, weight, reps, rir, notes
        FROM gym_log_set
        WHERE exercise_log_id = %s
        ORDER BY set_number
    """, (exercise_log_id,))
    sets = cur.fetchall()
    
    cur.close()
    conn.close()
    
    return {
        "exercise_log_id": exercise_log_id,
        "exercise": ex[1],
        "sets": [
            {
                "id": s[0],
                "set_number": s[1],
                "weight": float(s[2]) if s[2] else None,
                "reps": s[3],
                "rir": s[4],
                "notes": s[5]
            }
            for s in sets
        ]
    }


# POST /gym/log-exercises/{exercise_log_id}/sets - Add a set to an exercise log
@router.post("/log-exercises/{exercise_log_id}/sets")
def add_set_to_exercise(exercise_log_id: int, payload: SetCreate):
    conn = _get_conn()
    cur = conn.cursor()
    
    # Verify exercise log exists
    cur.execute("SELECT id FROM gym_log_exercise WHERE id = %s", (exercise_log_id,))
    if not cur.fetchone():
        cur.close()
        conn.close()
        raise HTTPException(status_code=404, detail="Exercise log not found")
    
    cur.execute("""
        INSERT INTO gym_log_set (exercise_log_id, set_number, weight, reps, rir, notes)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (exercise_log_id, set_number) 
        DO UPDATE SET weight = EXCLUDED.weight, reps = EXCLUDED.reps, rir = EXCLUDED.rir, notes = EXCLUDED.notes
        RETURNING id
    """, (exercise_log_id, payload.set_number, payload.weight, payload.reps, payload.rir, payload.notes))
    
    new_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()
    
    return {"id": new_id, "message": "Set logged"}


# ==========================================================
#                    LEGACY / EXTRA
# ==========================================================

# GET /gym/log - Get all logged sessions (legacy)
@router.get("/log")
def get_gym_log():
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT
            s.id,
            r.id as routine_id,
            r.routine,
            s.date,
            s.duration_minutes,
            s.feeling
        FROM gym_log_session s
        JOIN gym_routine r ON r.id = s.routine_id
        ORDER BY s.date DESC, s.id DESC
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    
    return [
        {
            "session_id": r[0],
            "routine_id": r[1],
            "name": r[2],
            "date": r[3].isoformat(),
            "duration_minutes": r[4],
            "feeling": r[5]
        }
        for r in rows
    ]