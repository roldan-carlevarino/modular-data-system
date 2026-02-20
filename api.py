from fastapi import FastAPI, HTTPException, Body, Query
import psycopg2
import os
from dotenv import load_dotenv
from datetime import date, datetime
from fastapi.middleware.cors import CORSMiddleware
from math import floor
from typing import List, Optional

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

@app.get("/task/today")
def get_tasks_today():
    try:
        conn = psycopg2.connect(os.getenv("TASKS_URL"), sslmode="require")
        cur = conn.cursor()

        today = date.today()

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

@app.post("/task/today/refresh_occurrences")
def refresh_tasks_today():
    conn = None
    cur = None
    
    try:
        conn = psycopg2.connect(os.getenv("TASKS_URL"), sslmode="require")
        cur = conn.cursor()
        
        actual_hour = datetime.now().hour
        today = date.today()
        
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



@app.patch("/task/today/move")
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
        today = date.today()

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

@app.post("/task/today/checkbox")
def update_task_today(payload: dict):

    occurrences_id = int(payload["occurrences_id"])
    completed = bool(payload["completed"])

    conn = psycopg2.connect(os.getenv("TASKS_URL"), sslmode="require")
    cur = conn.cursor()

    today = date.today()

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

#POMODORO
@app.post("/pomodoro/start")
def start_pomodoro(payload: dict):
    try:
        
        initial_focus = payload.get("initial_focus", {})
        ref_type = initial_focus.get("ref_type", "manual")
        ref_id = initial_focus.get("ref_id", 0)
        expectations = payload.get("expectations", []) 
        
        conn = psycopg2.connect(os.getenv("TASKS_URL"), sslmode="require")
        cur = conn.cursor()


        cur.execute("""
            INSERT INTO pomodoro_log (start_time, status)
            VALUES (%s, 'active')
            RETURNING id
        """, (now(),))
        pomodoro_id = cur.fetchone()[0]

        cur.execute("""
            INSERT INTO pomodoro_event
            (pomodoro_id, type, started, remaining_seconds)
            VALUES (%s, %s, %s, %s)
        """, (pomodoro_id, "study", now(), 3 * 60 * 60))
    
        for exp in expectations:
            cur.execute("""
                INSERT INTO pomodoro_expectation 
                (pomodoro_id, ref_type, ref_id, weight)
                VALUES (%s, %s, %s, %s)
            """, (
                pomodoro_id, 
                exp.get("ref_type"), 
                exp.get("ref_id"), 
                exp.get("weight", 1)
            ))

        cur.execute("""
            INSERT INTO pomodoro_focus_now 
            (pomodoro_id, ref_type, ref_id, since)
            VALUES (%s, %s, %s, %s)
        """, (pomodoro_id, ref_type, ref_id, now()))
        
        cur.execute("""
            INSERT INTO pomodoro_focus_log
            (pomodoro_id, ref_type, ref_id, started)
            VALUES (%s, %s, %s, %s)
        """, (pomodoro_id, ref_type, ref_id, now()))

        conn.commit()
        cur.close()
        conn.close()

        return {"pomodoro_id": pomodoro_id}

    except Exception as e:
        raise HTTPException(500, str(e))

@app.post("/pomodoro/change_state")
def change_state():
    conn = psycopg2.connect(os.getenv("TASKS_URL"), sslmode="require")
    cur = conn.cursor() 

    cur.execute("""
        SELECT id
        FROM pomodoro_log
        WHERE status = 'active'
        ORDER BY start_time DESC
        LIMIT 1
    """)
    row = cur.fetchone()
    
    if not row:
        cur.close()
        conn.close()
        raise HTTPException(404, "No active pomodoro")
    
    pomodoro_id = row[0]

    cur.execute("""
        SELECT type, started, remaining_seconds
        FROM pomodoro_event
        WHERE pomodoro_id = %s
            AND finished IS NULL
        LIMIT 1
     """, (pomodoro_id,))
    current = cur.fetchone()
    
    if not current:
        cur.close()
        conn.close()
        raise HTTPException(400, "No active event")
    
    current_type, started, remaining = current
                       
    elapsed = int((now() - started).total_seconds())
    remaining = max(0, remaining - elapsed)

    cur.execute("""
        UPDATE pomodoro_event
        SET finished = %s,
            remaining_seconds = %s
        WHERE pomodoro_id = %s AND finished IS NULL
    """, (now(), remaining, pomodoro_id))


    next_type = "rest" if current_type == "study" else "study"


    cur.execute("""
        SELECT remaining_seconds
        FROM pomodoro_event
        WHERE pomodoro_id = %s
          AND type = %s
        ORDER BY id DESC
        LIMIT 1
    """, (pomodoro_id, next_type))
    prev = cur.fetchone()

    if prev:
        next_remaining = prev[0]  
    else:
        next_remaining = 3 * 60 * 60 if next_type == "study" else 30 * 60
    
    cur.execute("""
        INSERT INTO pomodoro_event
        (pomodoro_id, type, started, remaining_seconds)
        VALUES (%s, %s, %s, %s)
    """, (pomodoro_id, next_type, now(), next_remaining))
    
    conn.commit()
    cur.close()     
    conn.close()
    return {"ok": True}

@app.post("/pomodoro/change_focus")
def change_focus(payload: dict):
    new_focus = payload.get("focus")

    if not new_focus:
        raise HTTPException(400, "Missing focus")

    ref_type = new_focus.get("ref_type")
    ref_id   = new_focus.get("ref_id")

    if ref_type is None or ref_id is None:
        raise HTTPException(400, "Invalid focus payload")

    conn = psycopg2.connect(os.getenv("TASKS_URL"), sslmode="require")
    cur = conn.cursor()

    # 1️⃣ obtener pomodoro activo
    cur.execute("""
        SELECT id
        FROM pomodoro_log
        WHERE status = 'active'
        ORDER BY start_time DESC
        LIMIT 1
    """)
    row = cur.fetchone()

    if not row:
        cur.close()
        conn.close()
        raise HTTPException(404, "No active pomodoro")

    pomodoro_id = row[0]

    # 2️⃣ cerrar focus_log activo
    cur.execute("""
        UPDATE pomodoro_focus_log
        SET finished = %s
        WHERE pomodoro_id = %s
          AND finished IS NULL
    """, (now(), pomodoro_id))

    # 3️⃣ actualizar focus_now (cartel)
    cur.execute("""
        UPDATE pomodoro_focus_now
        SET ref_type = %s,
            ref_id   = %s,
            since    = %s
        WHERE pomodoro_id = %s
    """, (ref_type, ref_id, now(), pomodoro_id))

    # 4️⃣ abrir nuevo focus_log
    cur.execute("""
        INSERT INTO pomodoro_focus_log
        (pomodoro_id, ref_type, ref_id, started)
        VALUES (%s, %s, %s, %s)
    """, (pomodoro_id, ref_type, ref_id, now()))

    conn.commit()
    cur.close()
    conn.close()

    return {"ok": True}

@app.post("/pomodoro/end")
def end_pomodoro(payload: dict):

    contents = payload.get("contents", [])
    conn = psycopg2.connect(os.getenv("TASKS_URL"), sslmode="require")
    cur = conn.cursor()
    cur.execute("""
        SELECT id
        FROM pomodoro_log
        WHERE status = 'active'
        ORDER BY start_time DESC
        """)
    pomodoro_id = cur.fetchone()[0]
    cur.execute("""
        UPDATE pomodoro_log
        SET status = 'ended', end_time = %s
        WHERE id = %s   
        """, (now(), pomodoro_id))
    
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
        UPDATE pomodoro_focus_log
        SET finished = %s
        WHERE pomodoro_id = %s
          AND finished IS NULL
    """, (now(), pomodoro_id))

    for cont in contents: 
        ref_type = cont.get("ref_type")
        ref_id = cont.get("ref_id")
        weight = cont.get("weight", 1)

        cur.execute("""
            INSERT INTO pomodoro_content
            (pomodoro_id, ref_type, ref_id, weight)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT DO NOTHING
        """, (pomodoro_id, ref_type, ref_id, weight))
    
    conn.commit()
    cur.close()
    conn.close()
    return {"ok": True}

@app.get("/pomodoro/current")
def current_pomodoro():
    conn = psycopg2.connect(os.getenv("TASKS_URL"), sslmode="require")
    cur = conn.cursor()

    cur.execute("""
        SELECT id
        FROM pomodoro_log
        WHERE status = 'active'
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

@app.get("/pomodoro/today")
def todays_pomodoros():
    conn = psycopg2.connect(os.getenv("TASKS_URL"), sslmode="require")
    cur = conn.cursor()

    today = date.today()

    cur.execute("""
        SELECT
            id,
            start_time,
            end_time,
            status
        FROM pomodoro_log
        WHERE DATE(start_time) = %s AND status = 'ended'
        ORDER BY start_time DESC;
    """, (today,))

    rows = cur.fetchall()

    cur.close()
    conn.close()

    return [
        {
            "pomodoro_id": r[0],
            "start_time": r[1],
            "end_time": r[2],
            "status": r[3]
        }
        for r in rows
    ]
    

# PROJECTS ENDPOINT DE GET 

@app.get("/projects")
def get_projects():
    conn = psycopg2.connect(os.getenv("TASKS_URL"), sslmode="require")
    cur = conn.cursor()

    cur.execute("""
        SELECT
            id,
            parent_id,
            type,
            name,
            description,
            status,
            path
        FROM projects_path
        WHERE status = 'active'
        ORDER BY path;
    """)
    rows = cur.fetchall()

    cur.close()
    conn.close()

    return [
        {
            "id": r[0],
            "parent_id": r[1],
            "type": r[2],
            "name": r[3],
            "description": r[4],
            "status": r[5],
            "path": r[6]
        }
        for r in rows
    ]




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
            raise HTTPException(status_code=404, detail="Pando not initialized")

        love_level, last_updated_at = row

        love, now = recalc_love(love_level, last_updated_at)

        cur.execute("""
            UPDATE pando_resources
            SET love_level = %s, last_updated_at = %s
            WHERE id = 1
        """, (love, now))
        conn.commit()

        cur.close()
        conn.close()

        mood = (
            "happy" if love >= 70 else
            "neutral" if love >= 40 else
            "sad"
        )

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


@app.get("/shopping/items")
def get_all_items():
    conn = None
    cur = None
    
    try:
        conn = psycopg2.connect(os.getenv("TASKS_URL"), sslmode="require")
        cur = conn.cursor()

        cur.execute("""
            SELECT item FROM shopping_food
                    WHERE active = true
            UNION ALL
            SELECT item FROM shopping_others
                    WHERE active = true
        """)
        rows = cur.fetchall()

        return [r[0] for r in rows]
        
    except Exception as e:
        raise HTTPException(500, f"Failed to get items: {str(e)}")
        
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

@app.get("/shopping/list")
def get_shopping_list():
    conn = None
    cur = None
    
    try:
        conn = psycopg2.connect(os.getenv("TASKS_URL"), sslmode="require")
        cur = conn.cursor()

        cur.execute("""
            SELECT item 
            FROM shopping_list
            """)
        rows = cur.fetchall()

        return [r[0] for r in rows]
        
    except Exception as e:
        raise HTTPException(500, f"Failed to get shopping list: {str(e)}")
        
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

@app.post("/shopping/insert_list")
def insert_shopping_list(payload = Body(...)):
    conn = None
    cur = None
    
    try:
        conn = psycopg2.connect(os.getenv("TASKS_URL"), sslmode="require")
        cur = conn.cursor()

        if isinstance(payload, dict):
            items = payload.get("items", [])
        else:
            items = payload

        if not isinstance(items, list):
            raise HTTPException(400, "Invalid items payload")

        for item in items:
            cur.execute("""
                INSERT INTO shopping_list (item)
                SELECT %s
                WHERE NOT EXISTS (
                    SELECT 1 FROM shopping_list WHERE item = %s
                );
            """, (item, item))

        conn.commit()
        return {"ok": True}
        
    except Exception as e:
        if conn:
            conn.rollback()
        raise HTTPException(500, f"Failed to insert items: {str(e)}")
        
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

@app.post("/shopping/delete_list")
def delete_shopping_list(payload = Body(...)):
    conn = None
    cur = None
    
    try:
        conn = psycopg2.connect(os.getenv("TASKS_URL"), sslmode="require")
        cur = conn.cursor()

        if isinstance(payload, dict):
            items = payload.get("items", [])
        else:
            items = payload

        if not isinstance(items, list):
            raise HTTPException(400, "Invalid items payload")

        for item in items:
            cur.execute("""
                DELETE FROM shopping_list
                WHERE item = %s
            """, (item,))

        conn.commit()
        return {"ok": True}
        
    except Exception as e:
        if conn:
            conn.rollback()
        raise HTTPException(500, f"Failed to delete items: {str(e)}")
        
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

@app.get("/knowledge/concept/{concept_id}")
def get_concept(concept_id: int):
    conn = psycopg2.connect(os.getenv("TASKS_URL"), sslmode="require")
    cur = conn.cursor()

    cur.execute("""
        SELECT block_type, content
        FROM knowledge_blocks
        WHERE concept_id = %s
          AND project_id IS NULL
          AND mode IS NULL
          AND reviewed = TRUE
        ORDER BY
          CASE block_type
            WHEN 'definition' THEN 1
            WHEN 'intuition'  THEN 2
            WHEN 'formula'    THEN 3
            WHEN 'example'    THEN 4
            WHEN 'warning'    THEN 5
            ELSE 99
          END,
          priority DESC
    """, (concept_id,))

    rows = cur.fetchall()
    cur.close(); conn.close()

    return [{"type": r[0], "content": r[1]} for r in rows]


app.get("/knowledge/viewer")
def knowledge_viewer(payload: dict):
    conn = psycopg2.connect(os.getenv("TASKS_URL"), sslmode="require")
    cur = conn.cursor()

    cur.execute("""
        SELECT name, content
        FROM knowledge_blocks
        WHERE concept_id = %s, block_type = %s, project_id %s, mode %s, reviewed = TRUE
        ORDER BY priority DESC;
    """, (payload.get("concept_id"), payload.get("block_type"), payload.get("project_id"), payload.get("mode")))
    rows = cur.fetchall()
    cur.close(); conn.close()

    return [{"name": r[0], "content": r[1]} for r in rows]

@app.get("/knowledge/query")
def knowledge_query(
    concept_id: int,
    mode: Optional[str] = None,
    project_id: Optional[int] = None,
    block_type: Optional[List[str]] = Query(default=None)
):
    conn = None
    cur = None

    try:
        conn = psycopg2.connect(os.getenv("TASKS_URL"), sslmode="require")
        cur = conn.cursor()

        if block_type is not None and len(block_type) == 0:
            block_type = None

        cur.execute("""
            SELECT DISTINCT
                b.id,
                b.block_type,
                b.content,
                b.mode,
                b.exercise_id,
                b.position,
                b.depends_on_block_id
            FROM knowledge_blocks b
            LEFT JOIN knowledge_block_projects bp
                   ON bp.block_id = b.id
            WHERE b.concept_id = %(concept_id)s
              AND b.reviewed = TRUE
              AND (
                    %(mode)s IS NULL
                    OR b.mode = %(mode)s
                  )
              AND (
                    %(project_id)s IS NULL
                    OR bp.project_id = %(project_id)s
                  )
              AND (
                    %(block_types)s IS NULL
                    OR b.block_type = ANY (%(block_types)s)
                  )
            ORDER BY
                b.exercise_id NULLS LAST,
                b.position NULLS LAST,
                b.id;
        """, {
            "concept_id": concept_id,
            "mode": mode,
            "project_id": project_id,
            "block_types": block_type
        })

        rows = cur.fetchall()

        return [
            {
                "id": r[0],
                "block_type": r[1],
                "content": r[2],
                "mode": r[3],
                "exercise_id": r[4],
                "position": r[5],
                "depends_on_block_id": r[6]
            }
            for r in rows
        ]

    except Exception as e:
        raise HTTPException(500, f"Knowledge query failed: {str(e)}")

    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

@app.get("/knowledge/projects")
def get_knowledge_projects():
    conn = None
    cur = None

    try:
        conn = psycopg2.connect(os.getenv("TASKS_URL"), sslmode="require")
        cur = conn.cursor()

        cur.execute("""
            SELECT id, name, parent_id
            FROM projects
            ORDER BY name;
        """)
        rows = cur.fetchall()

        return [
            {
                "id": r[0],
                "name": r[1],
                "parent_id": r[2]
            }
            for r in rows
        ]

    except Exception as e:
        raise HTTPException(500, f"Failed to get knowledge projects: {str(e)}")

    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

@app.get("/knowledge/concepts")
def get_knowledge_concepts(project_id: Optional[int] = None):
    conn = None
    cur = None

    try:
        conn = psycopg2.connect(os.getenv("TASKS_URL"), sslmode="require")
        cur = conn.cursor()

        if project_id is None:
            cur.execute("""
                SELECT id, name, parent_concept_id
                FROM knowledge_concepts
                ORDER BY parent_concept_id NULLS FIRST, name
            """)
        else:
            cur.execute("""
                SELECT DISTINCT
                    c.id,
                    c.name,
                    c.parent_concept_id
                FROM knowledge_concepts c
                JOIN knowledge_blocks b
                     ON b.concept_id = c.id
                JOIN knowledge_block_projects bp
                     ON bp.block_id = b.id
                 WHERE b.reviewed = TRUE
                  AND bp.project_id = %(project_id)s
                ORDER BY c.parent_concept_id NULLS FIRST, c.name
            """, {"project_id": project_id})

        rows = cur.fetchall()

        return [
            {
                "id": r[0],
                "name": r[1],
                "parent_concept_id": r[2]
            }
            for r in rows
        ]

    except Exception as e:
        raise HTTPException(500, f"Failed to get knowledge concepts: {str(e)}")

    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


@app.put("/knowledge/block/{block_id}")
def update_block_content(block_id: int, payload: dict):
    conn = None
    cur = None

    try:
        conn = psycopg2.connect(os.getenv("TASKS_URL"), sslmode="require")
        cur = conn.cursor()

        # Solo validar que venga content
        content = payload.get("content")
        
        if content is None:
            raise HTTPException(400, "Content is required")

        # Update simple
        cur.execute("""
            UPDATE knowledge_blocks
            SET content = %s
            WHERE id = %s
        """, (content, block_id))

        if cur.rowcount == 0:
            raise HTTPException(404, f"Block {block_id} not found")

        conn.commit()

        return {"ok": True}

    except HTTPException:
        raise
    except Exception as e:
        if conn:
            conn.rollback()
        raise HTTPException(500, f"Failed to update: {str(e)}")

    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()
            

@app.get("/logs")
def logs():
    conn = None
    cur = None

    try:
        conn = psycopg2.connect(os.getenv("TASKS_URL"), sslmode="require")
        cur = conn.cursor()

        cur.execute("""
            SELECT cron_name, status, message, error, timestamp
            FROM crons_log
            ORDER BY timestamp DESC
            LIMIT 5
        """)
        rows = cur.fetchall()

        return [
            {
                "id": i,
                "message": f"{r[2] if r[1] == 'success' else r[3]}",
                "timestamp": r[4].isoformat(),
                "level": r[1]
            }
            for i, r in enumerate(rows, 1)
        ]

    except Exception as e:
        raise HTTPException(500, f"Failed to get logs: {str(e)}")

    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()



@app.get("/rss/top-global")
def get_top_global(limit: int = 10):


    conn = psycopg2.connect(os.getenv("TASKS_URL"), sslmode="require")
    cur = conn.cursor()
    
    try:
        cur.execute("""
            SELECT 
                id,
                source_id,
                title,
                link,
                score_ml,
                score_markets,
                score_quant,
                score_politics,
                global_rank,
                top_category,
                category_rank,
                created_at
            FROM rss_articles
            WHERE global_rank IS NOT NULL
            ORDER BY global_rank ASC
            LIMIT %s
        """, (limit,))
        
        rows = cur.fetchall()
        
        articles = []
        for row in rows:
            articles.append({
                "id": row[0],
                "source_id": row[1],
                "title": row[2],
                "link": row[3],
                "scores": {
                    "ml": float(row[4]),
                    "markets": float(row[5]),
                    "quant": float(row[6]),
                    "politics": float(row[7])
                },
                "global_rank": row[8],
                "top_category": row[9],
                "category_rank": row[10],
                "created_at": row[11].isoformat() if row[11] else None
            })
        
        return {
            "total": len(articles),
            "limit": limit,
            "articles": articles
        }
        
    finally:
        cur.close()
        conn.close()


@app.get("/rss/top/{category}")
def get_top_category(category: str, limit: int = 10):
    """
    Obtiene los mejores artículos de una categoría específica.
    
    Parámetros:
    - category: ml, markets, quant, o politics
    - limit: Número de artículos a retornar (default: 10)
    """
    valid_categories = ["ml", "markets", "quant", "politics"]
    
    if category not in valid_categories:
        raise HTTPException(
            status_code=400, 
            detail=f"Invalid category. Must be one of: {', '.join(valid_categories)}"
        )
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        cur.execute("""
            SELECT 
                id,
                source_id,
                title,
                link,
                score_ml,
                score_markets,
                score_quant,
                score_politics,
                global_rank,
                top_category,
                category_rank,
                created_at
            FROM rss_articles
            WHERE top_category = %s
            ORDER BY category_rank ASC
            LIMIT %s
        """, (category, limit))
        
        rows = cur.fetchall()
        
        articles = []
        for row in rows:
            articles.append({
                "id": row[0],
                "source_id": row[1],
                "title": row[2],
                "link": row[3],
                "scores": {
                    "ml": float(row[4]),
                    "markets": float(row[5]),
                    "quant": float(row[6]),
                    "politics": float(row[7])
                },
                "global_rank": row[8],
                "top_category": row[9],
                "category_rank": row[10],
                "created_at": row[11].isoformat() if row[11] else None
            })
        
        return {
            "category": category,
            "total": len(articles),
            "limit": limit,
            "articles": articles
        }
        
    finally:
        cur.close()
        conn.close()


