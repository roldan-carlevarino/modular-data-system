from fastapi import FastAPI
import psycopg2
import os
from dotenv import load_dotenv
from datetime import date
from fastapi.middleware.cors import CORSMiddleware


load_dotenv()

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5500",
        "http://localhost:5500",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


print("API DATABASE:", os.getenv("TASKS_URL"))

@app.get("/")
def health():
    return {"status": "ok"}

@app.get("/task-log")
def get_task_log():
    conn = psycopg2.connect(os.getenv("TASKS_URL"), sslmode="require")
    cur = conn.cursor()

    cur.execute("""
        SELECT
            t.id,
            t.name,
            l.date,
            l.weekday,
            l.completed
        FROM task_log l
        JOIN tasks t ON t.id = l.task_id
        ORDER BY l.date DESC, t.id;
    """)

    rows = cur.fetchall()

    cur.close()
    conn.close()

    return [
        {
            "task_id": r[0],
            "name": r[1],
            "date": r[2].isoformat(),   # 👈 CLAVE
            "weekday": r[3],
            "completed": r[4]
        }
        for r in rows
    ]

@app.get("/tasks/today")
def get_tasks_today():
    try:
        conn = psycopg2.connect(os.getenv("TASKS_URL"), sslmode="require")
        cur = conn.cursor()

        today = date.today()

        cur.execute("""
            SELECT
                t.id,
                t.name,
                l.date,
                l.weekday,
                l.completed
            FROM task_log l
            JOIN tasks t ON t.id = l.task_id
            WHERE l.date = %s
            ORDER BY t.id;
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