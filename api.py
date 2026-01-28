from fastapi import FastAPI, HTTPException
import psycopg2
import os
from dotenv import load_dotenv
from datetime import date, datetime
from fastapi.middleware.cors import CORSMiddleware
from math import floor

load_dotenv()

def now():
    """Returns current UTC datetime for PostgreSQL compatibility"""
    return datetime.utcnow()

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


#POMODORO
@app.post("/pomodoro/start")
def start_pomodoro(ref_type: str, ref_id: int):
    conn = psycopg2.connect(os.getenv("TASKS_URL"), sslmode="require")
    cur = conn.cursor()

    cur.execute("""
    SELECT 1
    FROM pomodoro_log
    WHERE status = 'running'
    LIMIT 1
    """)

    if cur.fetchone():
        cur.close()
        conn.close()
        raise HTTPException(400, "Pomodoro already running")

    cur.execute("""
        INSERT INTO pomodoro_log (start_time, status)
        VALUES (%s, 'running')
        RETURNING id
    """, (now(),))
    pomodoro_id = cur.fetchone()[0]

    cur.execute("""
        INSERT INTO pomodoro_focus_now
        (pomodoro_id, ref_type, ref_id, since)
        VALUES (%s, %s, %s, %s)
    """, (pomodoro_id, ref_type, ref_id, now()))

    cur.execute("""
        INSERT INTO pomodoro_event
        (pomodoro_id, type, started)
        VALUES (%s, 'study', %s)
    """, (pomodoro_id, now()))

    conn.commit()
    cur.close()
    conn.close()

    return {"pomodoro_id": pomodoro_id}

@app.get("/pomodoro/current")
def get_current_pomodoro():
    conn = psycopg2.connect(os.getenv("TASKS_URL"), sslmode="require")
    cur = conn.cursor()

    cur.execute("""
        SELECT
            id,
            start_time,
            end_time,
            status
        FROM pomodoro_log
        ORDER BY start_time DESC
        LIMIT 1;
    """)
    row = cur.fetchone()

    cur.close()
    conn.close()

    if row is None:
        raise HTTPException(status_code=404, detail="No pomodoro found")

    pomodoro_id, start_time, end_time, status = row

    return {
        "id": pomodoro_id,
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat() if end_time else None,
        "status": status,
        "completed": status == "ended"
    }


@app.post("/pomodoro/state")
def change_state(pomodoro_id: int, new_type: str):
    if new_type not in ("study", "rest"):
        raise HTTPException(400, "Invalid state")

    conn = psycopg2.connect(os.getenv("TASKS_URL"), sslmode="require")
    cur = conn.cursor()

    # 1️⃣ Obtener evento activo actual
    cur.execute("""
        SELECT id, type, started, remaining_seconds
        FROM pomodoro_event
        WHERE pomodoro_id = %s
          AND finished IS NULL
        LIMIT 1
    """, (pomodoro_id,))
    row = cur.fetchone()

    if not row:
        cur.close()
        conn.close()
        raise HTTPException(400, "No active pomodoro event")

    event_id, current_type, started_at, remaining = row

    # 2️⃣ Calcular tiempo consumido
    elapsed = int((now() - started_at).total_seconds())
    updated_remaining = max(0, remaining - elapsed)

    # 3️⃣ Cerrar evento actual guardando remaining actualizado
    cur.execute("""
        UPDATE pomodoro_event
        SET finished = %s,
            remaining_seconds = %s
        WHERE id = %s
    """, (now(), updated_remaining, event_id))

    # 4️⃣ Obtener remaining previo del bloque al que entramos
    # (último evento de ese tipo)
    cur.execute("""
        SELECT remaining_seconds
        FROM pomodoro_event
        WHERE pomodoro_id = %s
          AND type = %s
        ORDER BY id DESC
        LIMIT 1
    """, (pomodoro_id, new_type))
    prev = cur.fetchone()

    if prev and prev[0] is not None:
        next_remaining = prev[0]
    else:
        # inicialización por defecto
        next_remaining = 3 * 60 * 60 if new_type == "study" else 30 * 60

    # 5️⃣ Crear nuevo evento con remaining heredado
    cur.execute("""
        INSERT INTO pomodoro_event
        (pomodoro_id, type, started, remaining_seconds)
        VALUES (%s, %s, %s, %s)
    """, (pomodoro_id, new_type, now(), next_remaining))

    conn.commit()
    cur.close()
    conn.close()

    return {"ok": True}


@app.post("/pomodoro/focus")
def change_focus(pomodoro_id: int, ref_type: str, ref_id: int):
    conn = psycopg2.connect(os.getenv("TASKS_URL"), sslmode="require")
    cur = conn.cursor()

    cur.execute("""
        UPDATE pomodoro_focus_now
        SET ref_type = %s,
            ref_id = %s,
            since = %s
        WHERE pomodoro_id = %s
    """, (ref_type, ref_id, now(), pomodoro_id))

    # opcional: registrar contenido
    cur.execute("""
        INSERT INTO pomodoro_content
        (pomodoro_id, ref_type, ref_id, weight)
        VALUES (%s, %s, %s, 1)
        ON CONFLICT DO NOTHING
    """, (pomodoro_id, ref_type, ref_id))

    conn.commit()
    cur.close()
    conn.close()

    return {"ok": True}

@app.post("/pomodoro/end")
def end_pomodoro(pomodoro_id: int):
    conn = psycopg2.connect(os.getenv("TASKS_URL"), sslmode="require")
    cur = conn.cursor()

    cur.execute("""
        UPDATE pomodoro_event
        SET finished = %s
        WHERE pomodoro_id = %s
          AND finished IS NULL
    """, (now(), pomodoro_id))

    cur.execute("""
        DELETE FROM pomodoro_focus_now
        WHERE pomodoro_id = %s
    """, (pomodoro_id,))

    cur.execute("""
        UPDATE pomodoro_log
        SET status = 'ended', end_time = %s
        WHERE id = %s
    """, (now(), pomodoro_id))

    conn.commit()
    cur.close()
    conn.close()

    return {"ok": True}

@app.get("/pomodoro/status")
def current_pomodoro_status():
    conn = psycopg2.connect(os.getenv("TASKS_URL"), sslmode="require")
    cur = conn.cursor()

    cur.execute("""
        SELECT id
        FROM pomodoro_log
        WHERE status = 'running'
        ORDER BY start_time DESC
        LIMIT 1
    """)
    row = cur.fetchone()
    
    if not row:
        cur.close()
        conn.close()
        return None

    pomodoro_id = row[0]

    cur.execute("""
        SELECT ref_type, ref_id, since
        FROM pomodoro_focus_now
        WHERE pomodoro_id = %s
    """, (pomodoro_id,))
    focus = cur.fetchone()

    cur.execute("""
        SELECT type, started
        FROM pomodoro_event
        WHERE pomodoro_id = %s
          AND finished IS NULL
    """, (pomodoro_id,))
    event = cur.fetchone()

    cur.close()
    conn.close()

    return {
        "pomodoro_id": pomodoro_id,
        "focus_now": {
            "ref_type": focus[0],
            "ref_id": focus[1],
            "since": focus[2]
        } if focus else None,
        "state": {
            "type": event[0],
            "started": event[1]
        } if event else None
    }


# GYM 

@app.get("/gym/log")
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