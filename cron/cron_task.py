import os
import psycopg2
from datetime import date, timedelta
from zoneinfo import ZoneInfo

_LOCAL_TZ = ZoneInfo("Europe/Amsterdam")
def _local_today() -> date:
    from datetime import datetime
    return datetime.now(_LOCAL_TZ).date()



def create_daily_tasks():
    conn = None
    cur = None
    
    try:
        conn = psycopg2.connect(os.getenv("TASKS_URL"), sslmode="require")
        cur = conn.cursor()
        
        today = _local_today()
        weekday = today.weekday()
        yesterday = today - timedelta(days=1)
        yesterday_weekday = yesterday.weekday()

        cur.execute("""
                    SELECT
                    task_id,
                    date,
                    position,
                    completed
                    FROM task_occurrences
                    WHERE date = %s
                    """, (yesterday,))
        yesterday_rows = cur.fetchall()
        
        for row in yesterday_rows:
            task_id_yesterday, yesterday_date, position_yesterday, yesterday_completed = row
            cur.execute("""
                INSERT INTO task_log (task_id, date, weekday, position, completed)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (task_id, date) DO UPDATE
                        SET completed = EXCLUDED.completed
                        
                """, (task_id_yesterday, yesterday_date, yesterday_weekday, position_yesterday, yesterday_completed))
        
        cur.execute("""
            DELETE FROM task_occurrences
                    WHERE date = %s
                    """, (yesterday,))
        
        cur.execute("""
            SELECT 
                task_id,
                interval_days,
                start_date
            FROM task_schedule 
            WHERE active = true
        """)
        
        schedules = cur.fetchall()
        
        for task_id, interval_days, start_date in schedules:
            should_create = False
            
            if start_date is not None and interval_days is not None:
                days_since = (today - start_date).days
                
                if days_since >= 0 and days_since % interval_days == 0:
                    should_create = True

                else:
                    should_create = False

            if should_create:
                
                cur.execute("""
                            SELECT 
                                ot.occurrence,
                                ot.position,
                                task.intraday_spill
                            FROM task_occurrences_template ot
                            JOIN task ON task.id = ot.task_id
                            WHERE task_id = %s
                            ORDER BY ot.position
                            """, (task_id,))
                
                rows = cur.fetchall()
                
                for row in rows:
                    occurrence, position, intraday_spill = row
                    cur.execute("""
                        INSERT INTO task_occurrences (task_id, date, position, completed, occurrence, intraday_spill)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT (task_id, date, occurrence) DO NOTHING    
                            
                            """, (task_id, today, position, False, occurrence, intraday_spill))
            
        conn.commit()
        
        # Log success
        cur.execute("""
            INSERT INTO crons_log (cron_name, status, message, timestamp)
            VALUES (%s, %s, %s, NOW())
        """, ('create_daily_tasks', 'success', 'Tasks created'))
        conn.commit()
        
    except psycopg2.Error as e:
        if conn:
            conn.rollback()
        
        # Log database error
        try:
            cur.execute("""
                INSERT INTO crons_log (cron_name, status, message, error, timestamp)
                VALUES (%s, %s, %s, %s, NOW())
            """, ('create_daily_tasks', 'error', 'Database error', str(e)))
            conn.commit()
        except:
            pass
        
        print(f"Database error in create_daily_tasks: {e}")
        raise
        
    except Exception as e:
        if conn:
            conn.rollback()
        
        # Log general error
        try:
            cur.execute("""
                INSERT INTO crons_log (cron_name, status, message, error, timestamp)
                VALUES (%s, %s, %s, %s, NOW())
            """, ('create_daily_tasks', 'error', 'General error', str(e)))
            conn.commit()
        except:
            pass
        
        print(f"Error in create_daily_tasks: {e}")
        raise
        
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    create_daily_tasks()
    print("Tasks created")
