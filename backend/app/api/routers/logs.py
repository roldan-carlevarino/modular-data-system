from fastapi import APIRouter, HTTPException
import psycopg2
import os

router = APIRouter(prefix="/logs", tags=["Logs"])  

@router.get("/")
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