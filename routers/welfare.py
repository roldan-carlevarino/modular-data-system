from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional, List
from datetime import date, datetime, timedelta
import psycopg2
import os

router = APIRouter(prefix="/welfare", tags=["Welfare"])

# ---------- Config ----------

WEIGHTS = {
    "water": 0.10,
    "exercise": 0.25,
    "nutrition": 0.20,
    "mental": 0.25,
    "study": 0.20,
}

GOALS = {
    "water_ml": 2500,
    "gym_days_per_week": 5,
    "study_minutes_per_day": 480,
    "mental": None  # No fixed goal, calculated from sub-metrics
}


# ---------- Helpers ----------

def _get_conn():
    return psycopg2.connect(os.getenv("TASKS_URL"), sslmode="require")


def _clamp(value: float, min_val: float = 0, max_val: float = 100) -> float:
    return max(min_val, min(max_val, value))


def _ensure_mental_tables(cur):
    """Create mental health tables if they don't exist"""
    cur.execute("""
        CREATE TABLE IF NOT EXISTS mental_log (
            id SERIAL PRIMARY KEY,
            date DATE NOT NULL,
            sleep_hours NUMERIC(3,1),
            stress INTEGER CHECK (stress >= 1 AND stress <= 5),
            journal_note TEXT,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)


# ---------- Score Calculations ----------

def _calc_water_score(cur, target_date: date) -> dict:
 
    cur.execute("""
        SELECT COALESCE(water, 0)
        FROM water_day
        WHERE date = %s
    """, (target_date,))
    row = cur.fetchone()
    ml = row[0] if row else 0
    
    score = _clamp((ml / GOALS["water_ml"]) * 100)
    return {
        "score": round(score),
        "raw": f"{ml}ml",
        "details": {"ml": ml, "goal": GOALS["water_ml"]}
    }


def _calc_exercise_score(cur, target_date: date) -> dict:
    """Exercise score: 100 if gym today, 0 if not (binary for now)"""
    # TODO: Later, evaluate based on intra-workout metrics (reps, sets, etc.)
    cur.execute("""
        SELECT COUNT(*)
        FROM gym_log_session
        WHERE DATE(date) = %s
    """, (target_date,))
    row = cur.fetchone()
    went_to_gym = (row[0] or 0) > 0
    
    score = 100 if went_to_gym else 0
    return {
        "score": score,
        "raw": "✓" if went_to_gym else "✗",
        "details": {"attended": went_to_gym}
    }


def _calc_nutrition_score(cur, target_date: date) -> dict:
    """
    Nutrition score: 100 if all 3 meals completed, proportional otherwise
    """
    cur.execute("""
        SELECT COUNT(*)
        FROM calories_mealtrack
        WHERE date = %s AND completed = true
    """, (target_date,))
    row = cur.fetchone()
    completed = row[0] if row else 0
    
    total_meals = 3  # morning, afternoon, evening
    score = (completed / total_meals) * 100
    
    return {
        "score": round(score),
        "raw": f"{completed}/{total_meals}",
        "details": {"completed": completed, "total": total_meals}
    }


def _calc_mental_score(cur, target_date: date) -> dict:
    """
    Mental score: weighted average of sleep and inverse stress
    - Sleep hours (50%): optimal 7-8h = 100, less/more = lower
    - Stress inverted (50%): 1-5 scale, inverted -> 0-100
    """
    _ensure_mental_tables(cur)
    
    cur.execute("""
        SELECT sleep_hours, stress
        FROM mental_log
        WHERE date = %s
    """, (target_date,))
    row = cur.fetchone()
    
    if not row:
        return {
            "score": 0,
            "raw": "sin datos",
            "details": {"note": "No mental health data for today"}
        }
    
    sleep_hours, stress = row
    
    # Sleep score: optimal is 7-8 hours
    # 7-8h = 100, 6h or 9h = 75, 5h or 10h = 50, etc.
    if sleep_hours:
        sleep_diff = abs(float(sleep_hours) - 7.5)
        sleep_score = max(0, 100 - (sleep_diff * 25))
    else:
        sleep_score = 50  # Default neutral if not logged
    
    # Stress score: inverted (low stress = high score)
    stress_score = ((6 - (stress or 3)) / 5) * 100
    
    # Weighted average (50/50)
    score = (sleep_score * 0.50) + (stress_score * 0.50)
    score = _clamp(score)
    
    return {
        "score": round(score),
        "raw": f"{sleep_hours or '-'}h / stress {stress or '-'}",
        "details": {
            "sleep_hours": float(sleep_hours) if sleep_hours else None,
            "stress": stress
        }
    }


def _calc_study_score(cur, target_date: date) -> dict:
    """Study score: (study_minutes_today / goal) * 100"""
    # Get study time from pomodoro_log (completed sessions)
    day_start = datetime.combine(target_date, datetime.min.time())
    day_end = datetime.combine(target_date, datetime.max.time())
    
    cur.execute("""
        SELECT COALESCE(SUM(
            GREATEST(0, EXTRACT(EPOCH FROM (end_time - start_time)) / 60 - 30)
        ), 0)
        FROM pomodoro_log
        WHERE start_time >= %s
          AND start_time < %s
          AND end_time IS NOT NULL
    """, (day_start, day_end))
    row = cur.fetchone()
    minutes = int(row[0]) if row and row[0] else 0
    
    score = _clamp((minutes / GOALS["study_minutes_per_day"]) * 100)
    hours = minutes / 60
    
    return {
        "score": round(score),
        "raw": f"{hours:.1f}h",
        "details": {"minutes": minutes, "goal": GOALS["study_minutes_per_day"]}
    }


def _calc_daily_index(cur, target_date: date) -> dict:
    """Calculate the full welfare index for a given date"""
    water = _calc_water_score(cur, target_date)
    exercise = _calc_exercise_score(cur, target_date)
    nutrition = _calc_nutrition_score(cur, target_date)
    mental = _calc_mental_score(cur, target_date)
    study = _calc_study_score(cur, target_date)
    
    # Weighted sum
    total = (
        water["score"] * WEIGHTS["water"] +
        exercise["score"] * WEIGHTS["exercise"] +
        nutrition["score"] * WEIGHTS["nutrition"] +
        mental["score"] * WEIGHTS["mental"] +
        study["score"] * WEIGHTS["study"]
    )
    
    return {
        "date": target_date.isoformat(),
        "score": round(total),
        "breakdown": {
            "water": {**water, "weight": WEIGHTS["water"]},
            "exercise": {**exercise, "weight": WEIGHTS["exercise"]},
            "nutrition": {**nutrition, "weight": WEIGHTS["nutrition"]},
            "mental": {**mental, "weight": WEIGHTS["mental"]},
            "study": {**study, "weight": WEIGHTS["study"]},
        }
    }


# ---------- Endpoints ----------

@router.get("/index")
def get_welfare_index(days: int = Query(default=30, ge=1, le=90)):
    """
    Get welfare index with history.
    
    - days: Number of days of history to return (default 30, max 90)
    """
    conn = None
    cur = None
    
    try:
        conn = _get_conn()
        cur = conn.cursor()
        
        today = date.today()
        
        # Calculate current day's full breakdown
        current = _calc_daily_index(cur, today)
        
        # Calculate history (just scores, no breakdown)
        history = []
        for i in range(1, days):
            past_date = today - timedelta(days=i)
            try:
                day_data = _calc_daily_index(cur, past_date)
                history.append({
                    "date": day_data["date"],
                    "score": day_data["score"]
                })
            except Exception:
                # Skip days with errors
                continue
        
        conn.commit()
        
        return {
            "current": current,
            "history": history
        }
        
    except Exception as e:
        raise HTTPException(500, f"Failed to calculate welfare index: {str(e)}")
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


@router.get("/goals")
def get_goals():
    """Get current welfare goals/targets"""
    return {
        "water_ml": GOALS["water_ml"],
        "gym_days_per_week": GOALS["gym_days_per_week"],
        "study_minutes_per_day": GOALS["study_minutes_per_day"],
        "weights": WEIGHTS
    }


# ---------- Mental Health Endpoints ----------

class MentalLogCreate(BaseModel):
    sleep_hours: Optional[float] = None
    stress: Optional[int] = None
    journal_note: Optional[str] = None


@router.get("/mental/today")
def get_mental_today():
    """Get today's mental health entry"""
    conn = None
    cur = None
    
    try:
        conn = _get_conn()
        cur = conn.cursor()
        _ensure_mental_tables(cur)
        conn.commit()
        
        cur.execute("""
            SELECT sleep_hours, stress, journal_note
            FROM mental_log
            WHERE date = %s
        """, (date.today(),))
        row = cur.fetchone()
        
        if not row:
            raise HTTPException(404, "No entry for today")
        
        return {
            "date": date.today().isoformat(),
            "sleep_hours": float(row[0]) if row[0] else None,
            "stress": row[1],
            "journal_note": row[2]
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Failed to load mental entry: {str(e)}")
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


@router.post("/mental/log")
def log_mental(payload: MentalLogCreate):
    """Log or update today's mental health entry"""
    conn = None
    cur = None
    
    try:
        conn = _get_conn()
        cur = conn.cursor()
        _ensure_mental_tables(cur)
        
        today = date.today()
        
        # Check if entry exists for today
        cur.execute("SELECT id FROM mental_log WHERE date = %s", (today,))
        existing = cur.fetchone()
        
        if existing:
            cur.execute("""
                UPDATE mental_log SET
                    sleep_hours = COALESCE(%s, sleep_hours),
                    stress = COALESCE(%s, stress),
                    journal_note = COALESCE(%s, journal_note)
                WHERE date = %s
                RETURNING sleep_hours, stress, journal_note
            """, (payload.sleep_hours, payload.stress, payload.journal_note, today))
        else:
            cur.execute("""
                INSERT INTO mental_log (date, sleep_hours, stress, journal_note)
                VALUES (%s, %s, %s, %s)
                RETURNING sleep_hours, stress, journal_note
            """, (today, payload.sleep_hours, payload.stress, payload.journal_note))
        
        row = cur.fetchone()
        conn.commit()
        
        return {
            "date": today.isoformat(),
            "sleep_hours": float(row[0]) if row[0] else None,
            "stress": row[1],
            "journal_note": row[2]
        }
        
    except Exception as e:
        if conn:
            conn.rollback()
        raise HTTPException(500, f"Failed to save mental entry: {str(e)}")
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


@router.get("/mental/history")
def get_mental_history(days: int = Query(default=30, ge=1, le=90)):
    """Get mental health history"""
    conn = None
    cur = None
    
    try:
        conn = _get_conn()
        cur = conn.cursor()
        _ensure_mental_tables(cur)
        conn.commit()
        
        start_date = date.today() - timedelta(days=days)
        
        cur.execute("""
            SELECT date, sleep_hours, stress, journal_note
            FROM mental_log
            WHERE date >= %s
            ORDER BY date DESC
        """, (start_date,))
        rows = cur.fetchall()
        
        return [
            {
                "date": row[0].isoformat(),
                "sleep_hours": float(row[1]) if row[1] else None,
                "stress": row[2],
                "journal_note": row[3]
            }
            for row in rows
        ]
        
    except Exception as e:
        raise HTTPException(500, f"Failed to load mental history: {str(e)}")
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()
