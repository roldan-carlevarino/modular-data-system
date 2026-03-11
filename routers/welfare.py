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
            date DATE NOT NULL UNIQUE,
            mood INTEGER CHECK (mood BETWEEN 1 AND 5),
            sleep_hours NUMERIC(3,1),
            sleep_quality INTEGER CHECK (sleep_quality BETWEEN 1 AND 4),
            stress INTEGER CHECK (stress BETWEEN 1 AND 5),
            mindfulness_minutes INTEGER DEFAULT 0,
            journal_note TEXT,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW()
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
    Mental score: weighted average of mood, sleep, and inverse stress
    - Mood (40%): 1-5 scale -> 0-100
    - Sleep quality (30%): 1-4 scale -> 0-100
    - Stress inverted (30%): 1-5 scale, inverted -> 0-100
    """
    _ensure_mental_tables(cur)
    
    cur.execute("""
        SELECT mood, sleep_hours, sleep_quality, stress, mindfulness_minutes
        FROM mental_log
        WHERE date = %s
    """, (target_date,))
    row = cur.fetchone()
    
    if not row or not row[0]:  # No data or no mood logged
        return {
            "score": 0,
            "raw": "sin datos",
            "details": {"note": "No mental health data for today"}
        }
    
    mood, sleep_hours, sleep_quality, stress, mindfulness = row
    
    # Calculate sub-scores
    mood_score = ((mood or 3) / 5) * 100  # Default to neutral if null
    sleep_score = ((sleep_quality or 2) / 4) * 100
    stress_score = ((6 - (stress or 3)) / 5) * 100  # Invert: low stress = high score
    
    # Bonus for mindfulness (up to 10 extra points for 30+ minutes)
    mindfulness_bonus = min(10, (mindfulness or 0) / 3)
    
    # Weighted average
    score = (mood_score * 0.40) + (sleep_score * 0.30) + (stress_score * 0.30) + mindfulness_bonus
    score = _clamp(score)
    
    return {
        "score": round(score),
        "raw": f"mood {mood}",
        "details": {
            "mood": mood,
            "sleep_hours": float(sleep_hours) if sleep_hours else None,
            "sleep_quality": sleep_quality,
            "stress": stress,
            "mindfulness_minutes": mindfulness
        }
    }


def _calc_study_score(cur, target_date: date) -> dict:
    """Study score: (study_minutes_today / goal) * 100"""
    # Get study time from pomodoro events
    day_start = datetime.combine(target_date, datetime.min.time())
    day_end = datetime.combine(target_date, datetime.max.time())
    
    cur.execute("""
        SELECT COALESCE(SUM(
            EXTRACT(EPOCH FROM (COALESCE(finished, NOW()) - started)) / 60
        ), 0)
        FROM pomodoro_event
        WHERE type = 'study'
          AND started >= %s
          AND started < %s
          AND finished IS NOT NULL
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
    mood: Optional[int] = None
    sleep_hours: Optional[float] = None
    sleep_quality: Optional[int] = None
    stress: Optional[int] = None
    mindfulness_minutes: Optional[int] = 0
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
            SELECT mood, sleep_hours, sleep_quality, stress, mindfulness_minutes, journal_note
            FROM mental_log
            WHERE date = %s
        """, (date.today(),))
        row = cur.fetchone()
        
        if not row:
            raise HTTPException(404, "No entry for today")
        
        return {
            "date": date.today().isoformat(),
            "mood": row[0],
            "sleep_hours": float(row[1]) if row[1] else None,
            "sleep_quality": row[2],
            "stress": row[3],
            "mindfulness_minutes": row[4],
            "journal_note": row[5]
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
        
        cur.execute("""
            INSERT INTO mental_log (date, mood, sleep_hours, sleep_quality, stress, mindfulness_minutes, journal_note)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (date) DO UPDATE SET
                mood = COALESCE(EXCLUDED.mood, mental_log.mood),
                sleep_hours = COALESCE(EXCLUDED.sleep_hours, mental_log.sleep_hours),
                sleep_quality = COALESCE(EXCLUDED.sleep_quality, mental_log.sleep_quality),
                stress = COALESCE(EXCLUDED.stress, mental_log.stress),
                mindfulness_minutes = COALESCE(EXCLUDED.mindfulness_minutes, mental_log.mindfulness_minutes),
                journal_note = COALESCE(EXCLUDED.journal_note, mental_log.journal_note),
                updated_at = NOW()
            RETURNING mood, sleep_hours, sleep_quality, stress, mindfulness_minutes, journal_note
        """, (
            today,
            payload.mood,
            payload.sleep_hours,
            payload.sleep_quality,
            payload.stress,
            payload.mindfulness_minutes,
            payload.journal_note
        ))
        
        row = cur.fetchone()
        conn.commit()
        
        return {
            "date": today.isoformat(),
            "mood": row[0],
            "sleep_hours": float(row[1]) if row[1] else None,
            "sleep_quality": row[2],
            "stress": row[3],
            "mindfulness_minutes": row[4],
            "journal_note": row[5]
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
            SELECT date, mood, sleep_hours, sleep_quality, stress, mindfulness_minutes, journal_note
            FROM mental_log
            WHERE date >= %s
            ORDER BY date DESC
        """, (start_date,))
        rows = cur.fetchall()
        
        return [
            {
                "date": row[0].isoformat(),
                "mood": row[1],
                "sleep_hours": float(row[2]) if row[2] else None,
                "sleep_quality": row[3],
                "stress": row[4],
                "mindfulness_minutes": row[5],
                "journal_note": row[6]
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
