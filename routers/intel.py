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