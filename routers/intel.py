from fastapi import APIRouter, HTTPException, Body, Query, UploadFile, File, Form
import psycopg2
import os
import json
import tempfile
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
                SELECT DISTINCT c.id, c.name, c.parent_concept_id
                FROM knowledge_concepts c
                WHERE
                    EXISTS (
                        SELECT 1
                        FROM knowledge_blocks b
                        JOIN knowledge_block_projects bp ON bp.block_id = b.id
                        WHERE b.concept_id = c.id
                          AND b.reviewed = TRUE
                          AND bp.project_id = %(project_id)s
                    )
                    OR EXISTS (
                        SELECT 1
                        FROM knowledge_concept_projects cp
                        WHERE cp.concept_id = c.id
                          AND cp.project_id = %(project_id)s
                    )
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
        
        print("name:", name, "parent_concept_id:", parent_concept_id, "project_id:", project_id)
        cur.execute("""
            INSERT INTO knowledge_concepts (name, parent_concept_id)
            VALUES (%s, %s)
            RETURNING id
        """, (name, parent_concept_id))

        concept_id = cur.fetchone()[0]

        if project_id is not None:
            cur.execute("""
                INSERT INTO knowledge_concept_projects (concept_id, project_id)
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
        block_type = payload.get("block_type")
        
        if content is None:
            raise HTTPException(400, "Content is required")

        # Update simple
        if block_type:
            cur.execute("""
                UPDATE knowledge_blocks
                SET content = %s, block_type = %s
                WHERE id = %s
            """, (content, block_type, block_id))
        else:
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

@router.get("/blocks")
def get_blocks_for_relations(project_id: Optional[int] = None):
    conn = None
    cur = None
    try:
        conn = psycopg2.connect(os.getenv("TASKS_URL"), sslmode="require")
        cur = conn.cursor()

        if project_id is None:
            cur.execute("""
                SELECT
                    b.id,
                    b.concept_id,
                    b.block_type,
                    LEFT(b.content, 60) AS content_preview,
                    b.depends_on_block_id,
                    COALESCE(
                        ARRAY_AGG(bp.project_id) FILTER (WHERE bp.project_id IS NOT NULL),
                        ARRAY[]::int[]
                    ) AS project_ids
                FROM knowledge_blocks b
                LEFT JOIN knowledge_block_projects bp ON bp.block_id = b.id
                GROUP BY b.id
                ORDER BY b.concept_id, b.id
            """)
        else:
            cur.execute("""
                SELECT
                    b.id,
                    b.concept_id,
                    b.block_type,
                    LEFT(b.content, 60) AS content_preview,
                    b.depends_on_block_id,
                    COALESCE(
                        ARRAY_AGG(bp.project_id) FILTER (WHERE bp.project_id IS NOT NULL),
                        ARRAY[]::int[]
                    ) AS project_ids
                FROM knowledge_blocks b
                LEFT JOIN knowledge_block_projects bp ON bp.block_id = b.id
                WHERE EXISTS (
                    SELECT 1 FROM knowledge_block_projects bp2
                    WHERE bp2.block_id = b.id AND bp2.project_id = %(project_id)s
                )
                GROUP BY b.id
                ORDER BY b.concept_id, b.id
            """, {"project_id": project_id})

        rows = cur.fetchall()
        return [
            {
                "id": r[0],
                "concept_id": r[1],
                "block_type": r[2],
                "content_preview": r[3],
                "depends_on_block_id": r[4],
                "project_ids": list(r[5]) if r[5] else []
            }
            for r in rows
        ]
    except Exception as e:
        raise HTTPException(500, f"Failed to get blocks: {str(e)}")
    finally:
        if cur: cur.close()
        if conn: conn.close()


@router.put("/block/{block_id}/relations")
def update_block_relations(block_id: int, payload: dict):
    conn = None
    cur = None
    try:
        conn = psycopg2.connect(os.getenv("TASKS_URL"), sslmode="require")
        cur = conn.cursor()
        cur.execute("""
            UPDATE knowledge_blocks
            SET depends_on_block_id = %s
            WHERE id = %s
        """, (payload.get("depends_on_block_id"), block_id))
        if cur.rowcount == 0:
            raise HTTPException(404, f"Block {block_id} not found")
        conn.commit()
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        if conn: conn.rollback()
        raise HTTPException(500, f"Failed to update block relations: {str(e)}")
    finally:
        if cur: cur.close()
        if conn: conn.close()


@router.put("/block/{block_id}/projects")
def update_block_projects(block_id: int, payload: dict):
    conn = None
    cur = None
    try:
        conn = psycopg2.connect(os.getenv("TASKS_URL"), sslmode="require")
        cur = conn.cursor()
        project_ids = payload.get("project_ids", [])
        # Replace all project associations
        cur.execute("DELETE FROM knowledge_block_projects WHERE block_id = %s", (block_id,))
        for pid in project_ids:
            cur.execute("""
                INSERT INTO knowledge_block_projects (block_id, project_id)
                VALUES (%s, %s)
                ON CONFLICT DO NOTHING
            """, (block_id, pid))
        conn.commit()
        return {"ok": True}
    except Exception as e:
        if conn: conn.rollback()
        raise HTTPException(500, f"Failed to update block projects: {str(e)}")
    finally:
        if cur: cur.close()
        if conn: conn.close()


@router.get("/concepts/{concept_id}/projects")
def get_concept_projects(concept_id: int):
    conn = None
    cur = None
    try:
        conn = psycopg2.connect(os.getenv("TASKS_URL"), sslmode="require")
        cur = conn.cursor()
        cur.execute("""
            SELECT project_id FROM knowledge_concept_projects
            WHERE concept_id = %s
        """, (concept_id,))
        return {"project_ids": [r[0] for r in cur.fetchall()]}
    except Exception as e:
        raise HTTPException(500, f"Failed to get concept projects: {str(e)}")
    finally:
        if cur: cur.close()
        if conn: conn.close()


@router.put("/concepts/{concept_id}/projects")
def update_concept_projects(concept_id: int, payload: dict):
    conn = None
    cur = None
    try:
        conn = psycopg2.connect(os.getenv("TASKS_URL"), sslmode="require")
        cur = conn.cursor()
        new_project_ids = set(payload.get("project_ids", []))

        # Current projects for this concept
        cur.execute("SELECT project_id FROM knowledge_concept_projects WHERE concept_id = %s", (concept_id,))
        old_project_ids = set(r[0] for r in cur.fetchall())

        added_projects = new_project_ids - old_project_ids

        # Get all descendant concept ids (including self)
        cur.execute("""
            WITH RECURSIVE concept_tree AS (
                SELECT id FROM knowledge_concepts WHERE id = %s
                UNION ALL
                SELECT c.id FROM knowledge_concepts c
                INNER JOIN concept_tree ct ON c.parent_concept_id = ct.id
            )
            SELECT id FROM concept_tree
        """, (concept_id,))
        all_concept_ids = [r[0] for r in cur.fetchall()]

        # Update concept-project links for root concept only
        cur.execute("DELETE FROM knowledge_concept_projects WHERE concept_id = %s", (concept_id,))
        for pid in new_project_ids:
            cur.execute("""
                INSERT INTO knowledge_concept_projects (concept_id, project_id)
                VALUES (%s, %s) ON CONFLICT DO NOTHING
            """, (concept_id, pid))

        # Cascade newly added projects to all descendant concepts and their blocks
        if added_projects:
            added_list = list(added_projects)

            # Add to descendant concepts (excluding root already handled)
            for cid in all_concept_ids:
                if cid == concept_id:
                    continue
                for pid in added_list:
                    cur.execute("""
                        INSERT INTO knowledge_concept_projects (concept_id, project_id)
                        VALUES (%s, %s) ON CONFLICT DO NOTHING
                    """, (cid, pid))

            # Add to all blocks of all concepts in the tree
            cur.execute("""
                SELECT id FROM knowledge_blocks WHERE concept_id = ANY(%s)
            """, (all_concept_ids,))
            block_ids = [r[0] for r in cur.fetchall()]

            for bid in block_ids:
                for pid in added_list:
                    cur.execute("""
                        INSERT INTO knowledge_block_projects (block_id, project_id)
                        VALUES (%s, %s) ON CONFLICT DO NOTHING
                    """, (bid, pid))

        conn.commit()
        return {"ok": True, "cascaded_to_concepts": len(all_concept_ids) - 1, "added_projects": list(added_projects)}
    except Exception as e:
        if conn: conn.rollback()
        raise HTTPException(500, f"Failed to update concept projects: {str(e)}")
    finally:
        if cur: cur.close()
        if conn: conn.close()


@router.put("/concepts/{concept_id}")
def update_concept(concept_id: int, payload: dict):
    conn = None
    cur = None

    try:
        conn = psycopg2.connect(os.getenv("TASKS_URL"), sslmode="require")
        cur = conn.cursor()

        # Prevent setting a descendant as parent (would create a cycle)
        if "parent_concept_id" in payload and payload["parent_concept_id"] is not None:
            new_parent = payload["parent_concept_id"]
            cur.execute("""
                WITH RECURSIVE desc_tree AS (
                    SELECT id FROM knowledge_concepts WHERE id = %s
                    UNION ALL
                    SELECT c.id FROM knowledge_concepts c
                    INNER JOIN desc_tree dt ON c.parent_concept_id = dt.id
                )
                SELECT id FROM desc_tree WHERE id = %s
            """, (concept_id, new_parent))
            if cur.fetchone():
                raise HTTPException(400, "Cannot set a descendant as parent (circular reference)")

        fields = []
        values = []

        if "name" in payload:
            fields.append("name = %s")
            values.append(payload["name"])

        if "parent_concept_id" in payload:
            fields.append("parent_concept_id = %s")
            values.append(payload["parent_concept_id"])

        if not fields:
            raise HTTPException(400, "Nothing to update")

        values.append(concept_id)
        cur.execute(f"UPDATE knowledge_concepts SET {', '.join(fields)} WHERE id = %s", values)

        if cur.rowcount == 0:
            raise HTTPException(404, f"Concept {concept_id} not found")

        conn.commit()
        return {"ok": True}

    except HTTPException:
        raise
    except Exception as e:
        if conn:
            conn.rollback()
        raise HTTPException(500, f"Failed to update concept: {str(e)}")

    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


@router.delete("/concepts/{concept_id}")
def delete_concept(concept_id: int):
    conn = None
    cur = None

    try:
        conn = psycopg2.connect(os.getenv("TASKS_URL"), sslmode="require")
        cur = conn.cursor()

        # Get the concept and all descendants recursively
        cur.execute("""
            WITH RECURSIVE concept_tree AS (
                SELECT id FROM knowledge_concepts WHERE id = %s
                UNION ALL
                SELECT c.id FROM knowledge_concepts c
                INNER JOIN concept_tree ct ON c.parent_concept_id = ct.id
            )
            SELECT id FROM concept_tree
        """, (concept_id,))

        ids = [row[0] for row in cur.fetchall()]

        if not ids:
            raise HTTPException(404, f"Concept {concept_id} not found")

        # Delete associated blocks (and their project links via cascade or explicit)
        cur.execute("""
            DELETE FROM knowledge_block_projects
            WHERE block_id IN (
                SELECT id FROM knowledge_blocks WHERE concept_id = ANY(%s)
            )
        """, (ids,))
        cur.execute("DELETE FROM knowledge_blocks WHERE concept_id = ANY(%s)", (ids,))
        cur.execute("DELETE FROM knowledge_concept_projects WHERE concept_id = ANY(%s)", (ids,))
        cur.execute("DELETE FROM knowledge_concepts WHERE id = ANY(%s)", (ids,))

        conn.commit()

        return {"ok": True, "deleted": ids}

    except HTTPException:
        raise
    except Exception as e:
        if conn:
            conn.rollback()
        raise HTTPException(500, f"Failed to delete concept: {str(e)}")

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


# ── INGEST ────────────────────────────────────────────────────────────────────

@router.post("/ingest")
async def ingest_document(
    file: UploadFile = File(...),
    project_id: Optional[int] = Form(None),
    instructions: Optional[str] = Form(None),
):
    """
    Extract text from a PDF or DOCX, fetch existing concepts for context,
    and ask an LLM to suggest new concepts + blocks.
    Returns a list of suggestions:
      [{concept, block_type, content, parent_concept_name}]
    """
    import pdfplumber
    import docx as docxlib
    from openai import OpenAI

    # 1. Extract text
    suffix = os.path.splitext(file.filename)[1].lower()
    content_bytes = await file.read()

    text = ""
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(content_bytes)
        tmp_path = tmp.name

    try:
        if suffix == ".pdf":
            with pdfplumber.open(tmp_path) as pdf:
                pages = [p.extract_text() or "" for p in pdf.pages]
            text = "\n\n".join(pages)
        elif suffix == ".docx":
            doc = docxlib.Document(tmp_path)
            text = "\n\n".join(p.text for p in doc.paragraphs if p.text.strip())
        else:
            raise HTTPException(400, "Only PDF and DOCX are supported")
    finally:
        os.unlink(tmp_path)

    if not text.strip():
        raise HTTPException(422, "Could not extract text from document")

    # Truncate to ~12k chars to stay within token limits
    text = text[:12000]

    # 2. Fetch existing concepts for context
    conn = None
    existing_concepts = []
    try:
        conn = psycopg2.connect(os.getenv("TASKS_URL"), sslmode="require")
        cur = conn.cursor()
        if project_id:
            cur.execute("""
                SELECT kc.id, kc.name, kc.parent_concept_id
                FROM knowledge_concepts kc
                JOIN knowledge_concept_projects kcp ON kcp.concept_id = kc.id
                WHERE kcp.project_id = %s
            """, (project_id,))
        else:
            cur.execute("SELECT id, name, parent_concept_id FROM knowledge_concepts LIMIT 200")
        rows = cur.fetchall()
        existing_concepts = [{"id": r[0], "name": r[1], "parent_id": r[2]} for r in rows]
    except Exception as e:
        raise HTTPException(500, f"Failed to fetch concepts: {str(e)}")
    finally:
        if conn:
            conn.close()

    concept_list = "\n".join(
        f"- {c['name']}" + (f" (child of id {c['parent_id']})" if c['parent_id'] else "")
        for c in existing_concepts
    ) or "None yet."

    # 3. Build prompt
    system_prompt = (
        "You are a knowledge extraction assistant. "
        "Given a document excerpt and an existing concept tree, "
        "suggest NEW concepts and their first knowledge block. "
        "Return ONLY a JSON array. Each item: "
        "{\"concept\": string, \"block_type\": string, \"content\": string, \"parent_concept_name\": string|null}. "
        "block_type must be one of: definition, intuition, formula, example, proof, theorem, remark, exercise, summary. "
        "parent_concept_name must exactly match an existing concept name or be null. "
        "Do not suggest concepts that already exist. Aim for 5-15 suggestions."
    )
    user_prompt = (
        f"EXISTING CONCEPTS:\n{concept_list}\n\n"
        f"{'INSTRUCTIONS: ' + instructions + chr(10) + chr(10) if instructions else ''}"
        f"DOCUMENT EXCERPT:\n{text}"
    )

    # 4. Call LLM
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt},
        ],
        temperature=0.3,
        response_format={"type": "json_object"},
    )

    raw = response.choices[0].message.content
    try:
        parsed = json.loads(raw)
        # model may wrap in {"suggestions": [...]} or return array directly
        if isinstance(parsed, list):
            suggestions = parsed
        else:
            suggestions = next(v for v in parsed.values() if isinstance(v, list))
    except Exception:
        raise HTTPException(500, "LLM returned invalid JSON")

    return suggestions