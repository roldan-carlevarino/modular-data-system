from fastapi import APIRouter, HTTPException, Body, Query
import psycopg2
import os
from typing import List, Optional

router = APIRouter(prefix="/knowledge", tags=["Knowledge"]) 

@router.get("/concept/{concept_id}")
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


@router.get("/viewer")
def knowledge_viewer(payload: dict):
    conn = psycopg2.connect(os.getenv("TASKS_URL"), sslmode="require")
    cur = conn.cursor()

    cur.execute("""
        SELECT name, content
        FROM knowledge_blocks
        WHERE concept_id = %s AND block_type = %s AND project_id = %s AND mode = %s AND reviewed = TRUE
        ORDER BY priority DESC;
    """, (payload.get("concept_id"), payload.get("block_type"), payload.get("project_id"), payload.get("mode")))
    rows = cur.fetchall()
    cur.close(); conn.close()

    return [{"name": r[0], "content": r[1]} for r in rows]

@router.get("/query")
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

@router.get("/projects")
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

@router.get("/concepts")
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

@router.post("/concepts/new")
def create_concept(payload: dict):
    conn = None
    cur = None
    try:
        conn = psycopg2.connect(os.getenv("TASKS_URL"), sslmode="require")
        cur = conn.cursor()

        name = payload.get("name")
        parent_concept_id = payload.get("parent_concept_id")
        project_id = payload.get("project_id")

        if not name:
            raise HTTPException(400, "Name is required")

        cur.execute("""
            INSERT INTO knowledge_concepts (name, parent_concept_id)
            VALUES (%s, %s)
            RETURNING id
        """, (name, parent_concept_id))

        concept_id = cur.fetchone()[0]

        cur.execute("""
            INSERT INTO knowledge_block_projects (block_id, project_id)
            VALUES (%s, %s)
        """, (concept_id, project_id))

        conn.commit()

        return {"id": concept_id}

    except HTTPException:
        raise
    except Exception as e:
        if conn:
            conn.rollback()
        raise HTTPException(500, f"Failed to create concept: {str(e)}")

    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


@router.put("/block/{block_id}")
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

@router.post("/block/new")
def create_block(payload: dict):
    conn = None
    cur = None

    try:
        conn = psycopg2.connect(os.getenv("TASKS_URL"), sslmode="require")
        cur = conn.cursor()

        concept_id = payload.get("concept_id")
        block_type = payload.get("block_type")
        content = payload.get("content", "")
        mode = payload.get("mode")
        project_id = payload.get("project_id")

        if not concept_id or not block_type:
            raise HTTPException(400, "concept_id and block_type are required")

        # Inserta el bloque siempre
        cur.execute("""
            INSERT INTO knowledge_blocks (concept_id, block_type, content, mode, reviewed)
            VALUES (%s, %s, %s, %s, TRUE)
            RETURNING id
        """, (concept_id, block_type, content, mode))

        block_id = cur.fetchone()[0]

        # Si hay project_id, inserta en knowledge_block_projects
        if project_id:
            cur.execute("""
                INSERT INTO knowledge_block_projects (block_id, project_id)
                VALUES (%s, %s)
            """, (block_id, project_id))

        conn.commit()

        return {"id": block_id}

    except HTTPException:
        raise
    except Exception as e:
        if conn:
            conn.rollback()
        raise HTTPException(500, f"Failed to create block: {str(e)}")

    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

@router.delete("/block/{block_id}")
def delete_block(block_id: int):
    conn = None
    cur = None

    try:
        conn = psycopg2.connect(os.getenv("TASKS_URL"), sslmode="require")
        cur = conn.cursor()

        cur.execute("""
            DELETE FROM knowledge_blocks
            WHERE id = %s
        """, (block_id,))

        if cur.rowcount == 0:
            raise HTTPException(404, f"Block {block_id} not found")

        conn.commit()

        return {"ok": True}

    except HTTPException:
        raise
    except Exception as e:
        if conn:
            conn.rollback()
        raise HTTPException(500, f"Failed to delete block: {str(e)}")

    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()