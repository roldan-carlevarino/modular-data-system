"""
Graph view of the workspace: builds a node/edge representation by walking
project-related foreign keys, for an Obsidian-style visualization.
"""
from fastapi import APIRouter, HTTPException
import psycopg2
import os

router = APIRouter(prefix="/graph", tags=["Graph"])


def _conn():
    return psycopg2.connect(os.getenv("TASKS_URL"), sslmode="require")


@router.get("")
def get_graph():
    """
    Returns { nodes: [...], edges: [...] }.
    Node id format: "<type>:<db_id>" (so types don't collide).
    Types: project, collection, attachment, concept, block, item.
    """
    nodes = []
    edges = []
    seen_nodes = set()

    def add_node(node_type: str, db_id: int, label: str, **extra):
        nid = f"{node_type}:{db_id}"
        if nid in seen_nodes:
            return nid
        seen_nodes.add(nid)
        nodes.append({
            "id": nid,
            "type": node_type,
            "label": label or f"{node_type} #{db_id}",
            **extra,
        })
        return nid

    def add_edge(source: str, target: str, kind: str):
        if source in seen_nodes and target in seen_nodes:
            edges.append({"source": source, "target": target, "kind": kind})

    conn = _conn()
    cur = conn.cursor()
    try:
        # ── Projects (with parent self-reference) ──────────────────────
        cur.execute("SELECT id, name, parent_id, type, status FROM projects")
        for pid, name, parent_id, ptype, status in cur.fetchall():
            add_node("project", pid, name, project_type=ptype, status=status)
        # parent edges (after all projects added)
        cur.execute("SELECT id, parent_id FROM projects WHERE parent_id IS NOT NULL")
        for pid, parent_id in cur.fetchall():
            add_edge(f"project:{parent_id}", f"project:{pid}", "subproject")

        # ── Library collections ────────────────────────────────────────
        try:
            cur.execute("SELECT id, name, project_id FROM lib_collection")
            for cid, name, proj_id in cur.fetchall():
                add_node("collection", cid, name)
                if proj_id is not None:
                    add_edge(f"project:{proj_id}", f"collection:{cid}", "has_collection")
        except Exception:
            conn.rollback()

        # ── Library items (linked via collections) ─────────────────────
        try:
            cur.execute("""
                SELECT i.id, i.title, ic.collection_id
                FROM lib_item i
                JOIN lib_item_collection ic ON ic.item_id = i.id
            """)
            for iid, title, cid in cur.fetchall():
                add_node("item", iid, title or f"Item #{iid}")
                add_edge(f"collection:{cid}", f"item:{iid}", "contains")
        except Exception:
            conn.rollback()

        # ── Project attachments (spreadsheets) ─────────────────────────
        try:
            cur.execute("SELECT id, name, project_id FROM project_attachment")
            for aid, name, proj_id in cur.fetchall():
                add_node("attachment", aid, name or f"Attachment #{aid}")
                add_edge(f"project:{proj_id}", f"attachment:{aid}", "has_attachment")
        except Exception:
            conn.rollback()

        # ── Knowledge concepts (via pivot) ─────────────────────────────
        try:
            cur.execute("SELECT id, name FROM knowledge_concepts")
            for cid, name in cur.fetchall():
                add_node("concept", cid, name)
            cur.execute("SELECT concept_id, project_id FROM knowledge_concept_projects")
            for cid, pid in cur.fetchall():
                add_edge(f"project:{pid}", f"concept:{cid}", "has_concept")
        except Exception:
            conn.rollback()

        # ── Knowledge blocks (via pivot; some may also have direct project_id) ─
        try:
            cur.execute("""
                SELECT id, COALESCE(NULLIF(name, ''),
                                    LEFT(content, 60),
                                    'block') AS label,
                       concept_id
                FROM knowledge_blocks
            """)
            for bid, label, concept_id in cur.fetchall():
                add_node("block", bid, label)
                if concept_id is not None:
                    add_edge(f"concept:{concept_id}", f"block:{bid}", "in_concept")
            cur.execute("SELECT block_id, project_id FROM knowledge_block_projects")
            for bid, pid in cur.fetchall():
                add_edge(f"project:{pid}", f"block:{bid}", "has_block")
        except Exception:
            conn.rollback()

        return {"nodes": nodes, "edges": edges}

    except Exception as e:
        raise HTTPException(500, f"Graph build failed: {e}")
    finally:
        cur.close()
        conn.close()
