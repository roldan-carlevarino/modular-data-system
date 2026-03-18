from fastapi import APIRouter, HTTPException
import psycopg2
import os

router = APIRouter(prefix="/projects", tags=["Projects"])  


@router.get("/")
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


@router.post("/")
def create_project(payload: dict):
    conn = None
    cur = None
    try:
        conn = psycopg2.connect(os.getenv("TASKS_URL"), sslmode="require")
        cur = conn.cursor()

        name = payload.get("name", "").strip()
        if not name:
            raise HTTPException(400, "Name is required")

        parent_id = payload.get("parent_id") or None
        proj_type = payload.get("type", "project")
        description = (payload.get("description") or "").strip() or None

        cur.execute("""
            INSERT INTO projects (name, parent_id, type, description, status)
            VALUES (%s, %s, %s, %s, 'active')
            RETURNING id
        """, (name, parent_id, proj_type, description))

        new_id = cur.fetchone()[0]
        conn.commit()
        return {"id": new_id}

    except HTTPException:
        raise
    except Exception as e:
        if conn: conn.rollback()
        raise HTTPException(500, f"Failed to create project: {str(e)}")
    finally:
        if cur: cur.close()
        if conn: conn.close()
