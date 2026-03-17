import os
import psycopg2
from datetime import date
from zoneinfo import ZoneInfo

_LOCAL_TZ = ZoneInfo("Europe/Amsterdam")
def _local_today() -> date:
    from datetime import datetime
    return datetime.now(_LOCAL_TZ).date()

def create_gym_tasks():
    conn = psycopg2.connect(os.getenv("TASKS_URL"), sslmode="require")
    cur = conn.cursor()
    
    today = _local_today()
    weekday = today.weekday()

    # Obtener tareas activas del gym_schedule
    cur.execute("""
        SELECT 
            gym_schedule.task_id,
            gym_schedule.interval_days,
            gym_schedule.start_date
        FROM gym_schedule
        WHERE gym_schedule.active = true
    """)
    
    schedules = cur.fetchall()
    
    for task_id, interval_days, start_date in schedules:
        should_create = False
        
        # Calcular si hoy corresponde esta tarea
        if start_date is not None and interval_days is not None:
            days_since = (today - start_date).days
            
            # Si han pasado suficientes días y es múltiplo del intervalo
            if days_since >= 0 and days_since % interval_days == 0:
                should_create = True
        
        # Insertar en gym_task_log si corresponde
        if should_create:
            cur.execute("""
                INSERT INTO gym_task_log (task_id, date, weekday, completed)
                VALUES (%s, %s, %s, false)
                ON CONFLICT (task_id, date) DO NOTHING
            """, (task_id, today, weekday))
    
    conn.commit()
    cur.close()
    conn.close()