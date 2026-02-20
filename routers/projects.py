from fastapi import APIRouter, HTTPException
import psycopg2
import os

router = APIRouter(prefix="/projects", tags=["Projects"])  


@router.get("/list")
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
