from fastapi import APIRouter, HTTPException
import psycopg2
import psycopg2.extras
import json
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


# ─────────────────────────────────────────────────────────────────
# Attachments — Excel-like spreadsheets attached to a project
# ─────────────────────────────────────────────────────────────────

def _conn():
    return psycopg2.connect(os.getenv("TASKS_URL"), sslmode="require")


@router.get("/{project_id}/attachments")
def list_attachments(project_id: int):
    conn = cur = None
    try:
        conn = _conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT id, project_id, kind, name, position,
                   created_at, updated_at
            FROM project_attachment
            WHERE project_id = %s
            ORDER BY position, id
        """, (project_id,))
        rows = cur.fetchall()
        return [
            {
                "id": r[0],
                "project_id": r[1],
                "kind": r[2],
                "name": r[3],
                "position": r[4],
                "created_at": r[5].isoformat() if r[5] else None,
                "updated_at": r[6].isoformat() if r[6] else None,
            }
            for r in rows
        ]
    finally:
        if cur: cur.close()
        if conn: conn.close()


@router.get("/attachments/{attachment_id}")
def get_attachment(attachment_id: int):
    conn = cur = None
    try:
        conn = _conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT id, project_id, kind, name, data, position,
                   created_at, updated_at
            FROM project_attachment
            WHERE id = %s
        """, (attachment_id,))
        r = cur.fetchone()
        if not r:
            raise HTTPException(404, "Attachment not found")
        return {
            "id": r[0],
            "project_id": r[1],
            "kind": r[2],
            "name": r[3],
            "data": r[4],
            "position": r[5],
            "created_at": r[6].isoformat() if r[6] else None,
            "updated_at": r[7].isoformat() if r[7] else None,
        }
    finally:
        if cur: cur.close()
        if conn: conn.close()


@router.post("/{project_id}/attachments")
def create_attachment(project_id: int, payload: dict):
    name = (payload.get("name") or "Untitled").strip() or "Untitled"
    kind = (payload.get("kind") or "excel").strip() or "excel"
    data = payload.get("data") or {}
    conn = cur = None
    try:
        conn = _conn()
        cur = conn.cursor()
        # Verify project exists
        cur.execute("SELECT 1 FROM projects WHERE id = %s", (project_id,))
        if not cur.fetchone():
            raise HTTPException(404, "Project not found")
        # Append at end
        cur.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 FROM project_attachment WHERE project_id = %s",
            (project_id,),
        )
        position = cur.fetchone()[0]
        cur.execute("""
            INSERT INTO project_attachment (project_id, kind, name, data, position)
            VALUES (%s, %s, %s, %s::jsonb, %s)
            RETURNING id
        """, (project_id, kind, name, json.dumps(data), position))
        new_id = cur.fetchone()[0]
        conn.commit()
        return {"id": new_id, "project_id": project_id, "kind": kind,
                "name": name, "position": position}
    except HTTPException:
        raise
    except Exception as e:
        if conn: conn.rollback()
        raise HTTPException(500, f"Failed to create attachment: {e}")
    finally:
        if cur: cur.close()
        if conn: conn.close()


@router.patch("/attachments/{attachment_id}")
def update_attachment(attachment_id: int, payload: dict):
    conn = cur = None
    try:
        conn = _conn()
        cur = conn.cursor()
        sets, vals = [], []
        if "name" in payload:
            sets.append("name = %s")
            vals.append((payload.get("name") or "Untitled").strip() or "Untitled")
        if "data" in payload:
            sets.append("data = %s::jsonb")
            vals.append(json.dumps(payload.get("data") or {}))
        if "position" in payload:
            sets.append("position = %s")
            vals.append(int(payload.get("position") or 0))
        if not sets:
            raise HTTPException(400, "Nothing to update")
        sets.append("updated_at = NOW()")
        vals.append(attachment_id)
        cur.execute(
            f"UPDATE project_attachment SET {', '.join(sets)} WHERE id = %s",
            tuple(vals),
        )
        if cur.rowcount == 0:
            raise HTTPException(404, "Attachment not found")
        conn.commit()
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        if conn: conn.rollback()
        raise HTTPException(500, f"Failed to update attachment: {e}")
    finally:
        if cur: cur.close()
        if conn: conn.close()


@router.delete("/attachments/{attachment_id}")
def delete_attachment(attachment_id: int):
    conn = cur = None
    try:
        conn = _conn()
        cur = conn.cursor()
        cur.execute("DELETE FROM project_attachment WHERE id = %s", (attachment_id,))
        if cur.rowcount == 0:
            raise HTTPException(404, "Attachment not found")
        conn.commit()
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        if conn: conn.rollback()
        raise HTTPException(500, f"Failed to delete attachment: {e}")
    finally:
        if cur: cur.close()
        if conn: conn.close()
