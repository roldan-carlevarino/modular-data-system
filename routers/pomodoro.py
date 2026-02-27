from fastapi import APIRouter, HTTPException
import psycopg2
import os
from datetime import date, datetime

router = APIRouter(prefix="/pomodoro", tags=["Pomodoro"]) 

def now():
    """Returns current UTC datetime for PostgreSQL compatibility"""
    return datetime.utcnow()

@router.post("/start")
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

        # Pre-create rest event as finished so remaining_seconds is stored
        cur.execute("""
            INSERT INTO pomodoro_event
            (pomodoro_id, type, started, finished, remaining_seconds)
            VALUES (%s, 'rest', %s, %s, %s)
        """, (pomodoro_id, now(), now(), 30 * 60))

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

@router.post("/change_state")
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

@router.post("/change_focus")
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

@router.post("/end")
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

@router.get("/current")
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

    # Active event
    cur.execute("""
        SELECT type, started, remaining_seconds
        FROM pomodoro_event
        WHERE pomodoro_id = %s AND finished IS NULL
        ORDER BY id DESC
        LIMIT 1
    """, (pomodoro_id,))
    active = cur.fetchone()

    # Last known remaining for each type
    cur.execute("""
        SELECT type, remaining_seconds
        FROM pomodoro_event
        WHERE pomodoro_id = %s
        ORDER BY id DESC
    """, (pomodoro_id,))
    all_events = cur.fetchall()

    # Build remaining map: last seen remaining per type
    remaining_map = {"study": 3 * 3600, "rest": 30 * 60}
    seen = set()
    for ev_type, ev_remaining in all_events:
        if ev_type not in seen and ev_remaining is not None:
            remaining_map[ev_type] = ev_remaining
            seen.add(ev_type)

    # Focus
    cur.execute("""
        SELECT ref_type, ref_id, since
        FROM pomodoro_focus_now
        WHERE pomodoro_id = %s
    """, (pomodoro_id,))
    focus = cur.fetchone()

    cur.close()
    conn.close()

    if not active:
        return None

    active_type, active_started, active_remaining_db = active
    elapsed = int((now() - active_started).total_seconds())
    active_remaining_now = max(0, (active_remaining_db or 0) - elapsed)

    # The inactive type shows its last stored remaining
    inactive_type = "rest" if active_type == "study" else "study"
    inactive_remaining = remaining_map.get(inactive_type, 0)

    return {
        "pomodoro_id": pomodoro_id,
        "focus_now": {
            "ref_type": focus[0],
            "ref_id": focus[1],
            "since": focus[2]
        } if focus else None,
        "active_type": active_type,
        "study_remaining": active_remaining_now if active_type == "study" else inactive_remaining if inactive_type == "study" else remaining_map["study"],
        "rest_remaining":  active_remaining_now if active_type == "rest"  else inactive_remaining if inactive_type == "rest"  else remaining_map["rest"],
        "active_started": active_started.isoformat()
    }

@router.get("/today")
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