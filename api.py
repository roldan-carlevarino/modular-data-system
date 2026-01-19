from fastapi import FastAPI, HTTPException
import psycopg2
import os
from dotenv import load_dotenv
from datetime import date, datetime
from fastapi.middleware.cors import CORSMiddleware
from math import floor

load_dotenv()

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5500",
        "http://localhost:5500",
        "https://rrollpk.github.io"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


print("API DATABASE:", os.getenv("TASKS_URL"))

@app.get("/")
def health():
    return {"status": "ok"}

@app.get("/task/log")
def get_task_log():
    conn = psycopg2.connect(os.getenv("TASKS_URL"), sslmode="require")
    cur = conn.cursor()

    cur.execute("""
        SELECT
            task.id,
            task.name,
            task_log.date,
            task_log.weekday,
            task_log.completed
        FROM task_log
        JOIN task ON task.id = task_log.task_id
        ORDER BY task_log.date DESC, task.id;
    """
)
    rows = cur.fetchall()

    cur.close()
    conn.close()

    return [
        {
            "task_id": r[0],
            "name": r[1],
            "date": r[2].isoformat(),   
            "weekday": r[3],
            "completed": r[4]
        }
        for r in rows
    ]

@app.get("/task/log/today")
def get_tasks_today():
    try:
        conn = psycopg2.connect(os.getenv("TASKS_URL"), sslmode="require")
        cur = conn.cursor()

        today = date.today()

        cur.execute("""
            SELECT
                task.id,
                task.name,
                task_log.date,
                task_log.weekday,
                task_log.completed
            FROM task_log 
            JOIN task ON task.id = task_log.task_id
            WHERE task_log.date = %s
            ORDER BY task.id;
        """, (today,))

        rows = cur.fetchall()

        cur.close()
        conn.close()

        return [
            {
                "task_id": r[0],
                "name": r[1],
                "date": r[2].isoformat(),
                "weekday": r[3],
                "completed": r[4]
            }
            for r in rows
        ]

    except Exception as e:
        
        raise

# REVISAR ESTO

@app.post("/tasks/log/today/status")
def update_task_today(payload: dict):

    task_id = int(payload["task_id"])
    completed = bool(payload["completed"])

    conn = psycopg2.connect(os.getenv("TASKS_URL"), sslmode="require")
    cur = conn.cursor()

    today = date.today()

    cur.execute("""
        UPDATE task_log
        SET completed = %s
        WHERE task_id = %s
          AND date = %s;
    """, (completed, task_id, today))

    conn.commit()
    cur.close()
    conn.close()

    return {"ok": True}


# GYM 

@app.get("/gym/log")
def get_gym_log():
    conn = psycopg2.connect(os.getenv("TASKS_URL"), sslmode="require")
    cur = conn.cursor()

    cur.execute("""
        SELECT
            gym_routine.id,
            gym_routine.name,
            gym_log.date,
            gym_log.completed
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
            "date": r[2].isoformat(),
            "completed": r[3]
        }
        for r in rows
    ]
# TODO: Implementar endpoint

# @app.get("/gym/routines")
# TODO: Implementar endpoint

# @app.get("/gym/routine/exercises")
# TODO: Implementar endpoint

# PANDO


DECAY_RATE = 1
MAX_LOVE = 100

def recalc_love(love_level: int, last_updated_at: datetime):
    now = datetime.utcnow()
    hours_passed = int((now - last_updated_at).total_seconds() / 3600)

    if hours_passed <= 0:
        return love_level, last_updated_at

    new_love = max(0, love_level - DECAY_RATE * hours_passed)
    return new_love, now

@app.get("/pando/love")
def get_pando_love():
    try:
        # 1️⃣ Conectar a la DB
        conn = psycopg2.connect(
            os.getenv("TASKS_URL"),
            sslmode="require"
        )
        cur = conn.cursor()

        # 2️⃣ Leer estado actual
        cur.execute("""
            SELECT love_level, last_updated_at
            FROM pando_resources
            WHERE id = 1
        """)
        row = cur.fetchone()

        if row is None:
            raise HTTPException(status_code=404, detail="Pando not initialized")

        love_level, last_updated_at = row

        # 3️⃣ Recalcular amor (lazy decay)
        love, now = recalc_love(love_level, last_updated_at)

        # 4️⃣ Guardar nuevo estado
        cur.execute("""
            UPDATE pando_resources
            SET love_level = %s, last_updated_at = %s
            WHERE id = 1
        """, (love, now))
        conn.commit()

        # 5️⃣ Cerrar conexión
        cur.close()
        conn.close()

        # 6️⃣ Mood derivado (NO se guarda)
        mood = (
            "happy" if love >= 70 else
            "neutral" if love >= 40 else
            "sad"
        )

        # 7️⃣ Respuesta
        return {
            "love": love,
            "mood": mood,
            "updated_at": now.isoformat()
        }

    except HTTPException:
        raise

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    

def feed_calc(love_level: int, last_updated_at: datetime, feed_amount: int):
    love, now = recalc_love(love_level, last_updated_at)
    new_love = min(MAX_LOVE, love + feed_amount)
    return new_love, now

@app.post("/pando/events")

def pando_event():

    effects = {
    "feed_pando": 10,
    "pet_pando": 5,
    "play_pando": 8,
    }

    try:
        conn = psycopg2.connect(
            os.getenv("TASKS_URL"),
            sslmode="require"
        )
        cur = conn.cursor()
        
        cur.execute("""
            SELECT love_level, last_updated_at
            FROM pando_resources
            WHERE id = 1
        """)
        row = cur.fetchone()
        
        if row is None:
            raise HTTPException(status_code=404, detail="Error")
        
        love_level, last_updated_at = row
        new_love, now = feed_calc(love_level, last_updated_at, effects["feed_pando"])
        cur.execute("""
            UPDATE pando_resources
            SET love_level = %s, last_updated_at = %s
            WHERE id = 1
        """, (new_love, now))
        conn.commit()
        cur.close()
        conn.close()

        return {"love": new_love, "updated_at": now.isoformat()}

    except Exception as e:
        
        raise HTTPException(status_code=500, detail=str(e))