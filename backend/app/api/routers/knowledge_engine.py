"""
Knowledge Engine — V1, Phase 1: the source-of-truth spine.

Architecture (see design discussion):
  * Source of truth (immutable, append-only):
      - kn_event     : every creative/ingestion act, attributed + timestamped.
      - kn_document  : the raw source artifact incorporated by a human.
  * Projections (regenerable, disposable):
      - kn_chunk     : deterministic fragments of a document's text (retrieval unit).

This phase intentionally ships WITHOUT embeddings / concepts / relations.
It proves the discipline that matters most: documents+events are truth, and
chunks are a projection that can be blown away and rebuilt at any time
(POST /kn/admin/rebuild-projections — the "regeneration drill").

Retrieval here is lexical (Postgres full-text search), reusing the proven
tsvector+GIN pattern already used by the Library module. Semantic retrieval
(pgvector) is Phase 2, pending confirmation of pgvector support on the host.
"""

import hashlib
import json
import os
import re
import unicodedata
from typing import List, Optional

import psycopg2
import psycopg2.extras
from fastapi import APIRouter, Body, Depends, HTTPException, Query

from routers.auth import get_current_user

router = APIRouter(prefix="/kn", tags=["KnowledgeEngine"])

# Bump this when the chunking algorithm changes; recorded on every chunk so we
# can tell which strategy produced a projection and force a rebuild.
CHUNK_STRATEGY = "v1"
CHUNK_TARGET_CHARS = 800
CHUNK_OVERLAP_CHARS = 100

VALID_SOURCE_TYPES = {"note", "pdf", "obsidian", "transcript", "slides", "paper", "book"}


# ---------------------------------------------------------------------------
# Connection + schema
# ---------------------------------------------------------------------------

def _conn():
    return psycopg2.connect(os.getenv("TASKS_URL"), sslmode="require")


def _ensure_schema(cur):
    # ---- Source of truth: the event log ----
    cur.execute("""
        CREATE TABLE IF NOT EXISTS kn_event (
            id               BIGSERIAL PRIMARY KEY,
            ts               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            actor            TEXT NOT NULL,
            event_type       TEXT NOT NULL,
            payload          JSONB NOT NULL DEFAULT '{}'::jsonb,
            basis            JSONB NOT NULL DEFAULT '[]'::jsonb,
            confidence       REAL NOT NULL DEFAULT 1.0,
            epistemic_status TEXT NOT NULL DEFAULT 'asserted'
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS kn_event_ts_idx   ON kn_event(ts DESC)")
    cur.execute("CREATE INDEX IF NOT EXISTS kn_event_type_idx ON kn_event(event_type)")

    # ---- Source of truth: documents (raw artifact) ----
    cur.execute("""
        CREATE TABLE IF NOT EXISTS kn_document (
            id            BIGSERIAL PRIMARY KEY,
            source_type   TEXT NOT NULL DEFAULT 'note',
            title         TEXT NOT NULL,
            sha256        TEXT NOT NULL UNIQUE,
            lang          TEXT NOT NULL DEFAULT 'english',
            -- For text-native sources (note/obsidian/transcript) the raw text
            -- IS the artifact and lives here. For binary sources (pdf...) this
            -- is NULL and b2_path points at the irreplaceable original.
            raw_content   TEXT,
            b2_path       TEXT,
            ingest_event  BIGINT REFERENCES kn_event(id),
            created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS kn_document_created_idx ON kn_document(created_at DESC)")

    # ---- Projection: chunks (deterministic, regenerable) ----
    cur.execute("""
        CREATE TABLE IF NOT EXISTS kn_chunk (
            id           BIGSERIAL PRIMARY KEY,
            document_id  BIGINT NOT NULL REFERENCES kn_document(id) ON DELETE CASCADE,
            ord          INTEGER NOT NULL,
            text         TEXT NOT NULL,
            char_start   INTEGER NOT NULL,
            char_end     INTEGER NOT NULL,
            token_est    INTEGER NOT NULL DEFAULT 0,
            sha256       TEXT NOT NULL,
            strategy     TEXT NOT NULL DEFAULT 'v1'
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS kn_chunk_doc_idx ON kn_chunk(document_id, ord)")
    cur.execute("""
        CREATE INDEX IF NOT EXISTS kn_chunk_fts_idx ON kn_chunk
        USING GIN (to_tsvector('english', coalesce(text, '')))
    """)

    # ---- Projection: concepts (the anchor / referent) ----
    # Event-folded: created/renamed/merged/reviewed are events. The row is the
    # current materialized state and is read-only except via the event folder.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS kn_concept (
            id            BIGSERIAL PRIMARY KEY,
            slug          TEXT NOT NULL UNIQUE,
            name          TEXT NOT NULL,
            status        TEXT NOT NULL DEFAULT 'candidate',
            merged_into   BIGINT REFERENCES kn_concept(id),
            create_event  BIGINT REFERENCES kn_event(id),
            created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS kn_concept_status_idx ON kn_concept(status)")
    cur.execute("CREATE INDEX IF NOT EXISTS kn_concept_merged_idx ON kn_concept(merged_into)")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS kn_concept_alias (
            id          BIGSERIAL PRIMARY KEY,
            concept_id  BIGINT NOT NULL REFERENCES kn_concept(id) ON DELETE CASCADE,
            alias       TEXT NOT NULL,
            alias_norm  TEXT NOT NULL,
            source      TEXT NOT NULL DEFAULT 'manual',
            UNIQUE (concept_id, alias_norm)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS kn_concept_alias_norm_idx ON kn_concept_alias(alias_norm)")

    # ---- Projection: knowledge units (the atom of content) ----
    cur.execute("""
        CREATE TABLE IF NOT EXISTS kn_knowledge_unit (
            id               BIGSERIAL PRIMARY KEY,
            content          TEXT NOT NULL,
            role             TEXT NOT NULL DEFAULT 'claim',
            epistemic_status TEXT NOT NULL DEFAULT 'authored_human',
            confidence       REAL NOT NULL DEFAULT 1.0,
            status           TEXT NOT NULL DEFAULT 'active',
            version          INTEGER NOT NULL DEFAULT 1,
            create_event     BIGINT REFERENCES kn_event(id),
            created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS kn_unit_role_idx   ON kn_knowledge_unit(role)")
    cur.execute("CREATE INDEX IF NOT EXISTS kn_unit_status_idx ON kn_knowledge_unit(status)")
    cur.execute("""
        CREATE INDEX IF NOT EXISTS kn_unit_fts_idx ON kn_knowledge_unit
        USING GIN (to_tsvector('english', coalesce(content, '')))
    """)

    # Multi-concept anchoring (a unit is *about* one or more concepts).
    cur.execute("""
        CREATE TABLE IF NOT EXISTS kn_unit_concept (
            unit_id     BIGINT NOT NULL REFERENCES kn_knowledge_unit(id) ON DELETE CASCADE,
            concept_id  BIGINT NOT NULL REFERENCES kn_concept(id) ON DELETE CASCADE,
            PRIMARY KEY (unit_id, concept_id)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS kn_unit_concept_concept_idx ON kn_unit_concept(concept_id)")

    # ---- Projection: relations (typed edges = assertions between concepts) ----
    cur.execute("""
        CREATE TABLE IF NOT EXISTS kn_relation (
            id            BIGSERIAL PRIMARY KEY,
            src_concept   BIGINT NOT NULL REFERENCES kn_concept(id) ON DELETE CASCADE,
            dst_concept   BIGINT NOT NULL REFERENCES kn_concept(id) ON DELETE CASCADE,
            rel_type      TEXT NOT NULL,
            confidence    REAL NOT NULL DEFAULT 1.0,
            status        TEXT NOT NULL DEFAULT 'candidate',
            create_event  BIGINT REFERENCES kn_event(id),
            created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (src_concept, dst_concept, rel_type)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS kn_relation_src_idx ON kn_relation(src_concept)")
    cur.execute("CREATE INDEX IF NOT EXISTS kn_relation_dst_idx ON kn_relation(dst_concept)")

    # ---- Projection: mentions (the RAG <-> KG bridge) ----
    # V2 builds these deterministically by lexical match of concept terms in
    # chunk text, so they are a pure, regenerable projection (part of the drill).
    cur.execute("""
        CREATE TABLE IF NOT EXISTS kn_mention (
            id          BIGSERIAL PRIMARY KEY,
            concept_id  BIGINT NOT NULL REFERENCES kn_concept(id) ON DELETE CASCADE,
            chunk_id    BIGINT NOT NULL REFERENCES kn_chunk(id) ON DELETE CASCADE,
            score       REAL NOT NULL DEFAULT 1.0,
            source      TEXT NOT NULL DEFAULT 'lexical',
            UNIQUE (concept_id, chunk_id, source)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS kn_mention_concept_idx ON kn_mention(concept_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS kn_mention_chunk_idx   ON kn_mention(chunk_id)")


def migrate():
    """Idempotent schema creation; called once at startup from main.py."""
    conn = _conn()
    try:
        cur = conn.cursor()
        _ensure_schema(cur)
        conn.commit()
        cur.close()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Pure helpers (deterministic — the drill depends on this)
# ---------------------------------------------------------------------------

def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _normalize(text: str) -> str:
    """Lowercase + strip accents + collapse whitespace. For slugs/aliases/dedup."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(text.lower().split())


def _slugify(name: str) -> str:
    norm = _normalize(name)
    slug = re.sub(r"[^a-z0-9]+", "-", norm).strip("-")
    return slug or "concept"


def _chunk_text(text: str):
    """Split text into deterministic, slightly-overlapping windows.

    Pure function: the same input always yields the same chunks, so rebuilding
    projections from a document reproduces them exactly. Returns a list of
    dicts: {ord, text, char_start, char_end}.
    """
    text = text or ""
    n = len(text)
    chunks = []
    if n == 0:
        return chunks
    start = 0
    ord_ = 0
    step = max(1, CHUNK_TARGET_CHARS - CHUNK_OVERLAP_CHARS)
    while start < n:
        end = min(start + CHUNK_TARGET_CHARS, n)
        # Prefer to break on a paragraph/sentence boundary near the window end.
        if end < n:
            window = text[start:end]
            for sep in ("\n\n", "\n", ". ", " "):
                idx = window.rfind(sep)
                if idx > CHUNK_TARGET_CHARS // 2:
                    end = start + idx + len(sep)
                    break
        piece = text[start:end].strip()
        if piece:
            chunks.append({
                "ord": ord_,
                "text": piece,
                "char_start": start,
                "char_end": end,
            })
            ord_ += 1
        if end <= start:
            break
        start = end - CHUNK_OVERLAP_CHARS if end < n else end
        if start < 0:
            start = 0
    return chunks


def _append_event(cur, actor, event_type, payload, basis=None,
                  confidence=1.0, epistemic_status="asserted"):
    """Append-only write to the source of truth. Never updated, never deleted."""
    cur.execute("""
        INSERT INTO kn_event (actor, event_type, payload, basis, confidence, epistemic_status)
        VALUES (%s, %s, %s::jsonb, %s::jsonb, %s, %s)
        RETURNING id, ts
    """, (
        actor, event_type, json.dumps(payload or {}),
        json.dumps(basis or []), confidence, epistemic_status,
    ))
    return cur.fetchone()  # (id, ts)


def _build_chunks_for_document(cur, document_id, text, strategy=CHUNK_STRATEGY):
    """(Re)materialize the chunk projection for one document. Deterministic."""
    cur.execute("DELETE FROM kn_chunk WHERE document_id = %s", (document_id,))
    rows = _chunk_text(text)
    for c in rows:
        cur.execute("""
            INSERT INTO kn_chunk
                (document_id, ord, text, char_start, char_end, token_est, sha256, strategy)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            document_id, c["ord"], c["text"], c["char_start"], c["char_end"],
            max(1, len(c["text"]) // 4), _sha256(c["text"]), strategy,
        ))
    return len(rows)


def _build_mentions(cur):
    """(Re)materialize the lexical mention projection: concept term -> chunk.

    Pure and deterministic: a concept's name/aliases matched against chunk text.
    This is the RAG<->KG bridge and part of the regeneration drill. Merged
    concepts are skipped (their terms live on the surviving concept).
    """
    cur.execute("DELETE FROM kn_mention WHERE source = 'lexical'")

    # Gather terms per surviving concept: canonical name + all aliases.
    cur.execute("SELECT id, name FROM kn_concept WHERE merged_into IS NULL")
    terms = {}  # concept_id -> set of normalized terms
    for cid, name in cur.fetchall():
        norm = _normalize(name)
        if len(norm) >= 3:
            terms.setdefault(cid, set()).add(norm)
    cur.execute("""
        SELECT a.concept_id, a.alias_norm
        FROM kn_concept_alias a
        JOIN kn_concept c ON c.id = a.concept_id
        WHERE c.merged_into IS NULL
    """)
    for cid, alias_norm in cur.fetchall():
        if alias_norm and len(alias_norm) >= 3:
            terms.setdefault(cid, set()).add(alias_norm)

    if not terms:
        return 0

    cur.execute("SELECT id, text FROM kn_chunk")
    chunks = [(cid, _normalize(txt)) for cid, txt in cur.fetchall()]

    total = 0
    for concept_id, concept_terms in terms.items():
        for chunk_id, norm_text in chunks:
            score = 0
            for term in concept_terms:
                # Word-boundary count so 'var' doesn't match 'variance'.
                count = len(re.findall(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", norm_text))
                score += count
            if score > 0:
                cur.execute("""
                    INSERT INTO kn_mention (concept_id, chunk_id, score, source)
                    VALUES (%s, %s, %s, 'lexical')
                    ON CONFLICT (concept_id, chunk_id, source) DO UPDATE SET score = EXCLUDED.score
                """, (concept_id, chunk_id, float(score)))
                total += 1
    return total


def _resolve_concept(cur, concept_id):
    """Follow the merged_into chain to the surviving canonical concept id."""
    seen = set()
    while concept_id is not None and concept_id not in seen:
        seen.add(concept_id)
        cur.execute("SELECT merged_into FROM kn_concept WHERE id = %s", (concept_id,))
        row = cur.fetchone()
        if not row:
            return None
        if row[0] is None:
            return concept_id
        concept_id = row[0]
    return concept_id


# ---------------------------------------------------------------------------
# Ingestion (writes truth, then projects)
# ---------------------------------------------------------------------------

@router.post("/documents")
def ingest_document(
    payload: dict = Body(...),
    user: str = Depends(get_current_user),
):
    """Ingest a text-native document (note / obsidian / transcript).

    Records a `document_ingested` event (source of truth), stores the artifact,
    then projects it into chunks. Idempotent by content hash.
    """
    title = (payload.get("title") or "").strip()
    content = payload.get("content") or ""
    source_type = (payload.get("source_type") or "note").strip()

    if not title:
        raise HTTPException(400, "title is required")
    if not content.strip():
        raise HTTPException(400, "content is required")
    if source_type not in VALID_SOURCE_TYPES:
        raise HTTPException(400, f"Invalid source_type. Allowed: {sorted(VALID_SOURCE_TYPES)}")

    sha = _sha256(content)
    conn = _conn()
    try:
        cur = conn.cursor()
        _ensure_schema(cur)

        # Idempotency: same content already ingested -> return existing.
        cur.execute("SELECT id FROM kn_document WHERE sha256 = %s", (sha,))
        existing = cur.fetchone()
        if existing:
            cur.close()
            return {"document_id": existing[0], "duplicate": True}

        actor = f"human:{user}"
        event_id, _ = _append_event(
            cur, actor, "document_ingested",
            payload={"title": title, "source_type": source_type, "sha256": sha},
        )

        cur.execute("""
            INSERT INTO kn_document (source_type, title, sha256, raw_content, ingest_event)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id
        """, (source_type, title, sha, content, event_id))
        document_id = cur.fetchone()[0]

        n_chunks = _build_chunks_for_document(cur, document_id, content)

        conn.commit()
        cur.close()
        return {
            "document_id": document_id,
            "event_id": event_id,
            "chunks": n_chunks,
            "duplicate": False,
        }
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, f"Ingestion failed: {e}")
    finally:
        conn.close()


@router.get("/documents")
def list_documents(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    conn = _conn()
    try:
        cur = conn.cursor()
        _ensure_schema(cur)
        cur.execute("""
            SELECT d.id, d.source_type, d.title, d.created_at,
                   (SELECT count(*) FROM kn_chunk c WHERE c.document_id = d.id) AS chunks
            FROM kn_document d
            ORDER BY d.created_at DESC
            LIMIT %s OFFSET %s
        """, (limit, offset))
        rows = cur.fetchall()
        cur.close()
        return [
            {"id": r[0], "source_type": r[1], "title": r[2],
             "created_at": r[3].isoformat() if r[3] else None, "chunks": r[4]}
            for r in rows
        ]
    finally:
        conn.close()


@router.get("/documents/{document_id}")
def get_document(document_id: int):
    conn = _conn()
    try:
        cur = conn.cursor()
        _ensure_schema(cur)
        cur.execute("""
            SELECT id, source_type, title, sha256, lang, ingest_event, created_at
            FROM kn_document WHERE id = %s
        """, (document_id,))
        r = cur.fetchone()
        if not r:
            raise HTTPException(404, "Document not found")
        cur.execute("SELECT count(*) FROM kn_chunk WHERE document_id = %s", (document_id,))
        n = cur.fetchone()[0]
        cur.close()
        return {
            "id": r[0], "source_type": r[1], "title": r[2], "sha256": r[3],
            "lang": r[4], "ingest_event": r[5],
            "created_at": r[6].isoformat() if r[6] else None,
            "chunks": n,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Retrieval (first Engine primitive — lexical for now)
# ---------------------------------------------------------------------------

@router.post("/retrieve")
def retrieve(payload: dict = Body(...)):
    """Lexical retrieval over the chunk projection (Postgres FTS).

    Returns evidence: chunks with their document + locator, ranked by relevance.
    This is the seed of the Knowledge Engine read API; agents will consume this.
    """
    query = (payload.get("query") or "").strip()
    limit = int(payload.get("limit") or 10)
    limit = max(1, min(limit, 50))
    if not query:
        raise HTTPException(400, "query is required")

    conn = _conn()
    try:
        cur = conn.cursor()
        _ensure_schema(cur)
        cur.execute("""
            SELECT c.id, c.document_id, d.title, c.ord, c.text,
                   c.char_start, c.char_end,
                   ts_rank(to_tsvector('english', c.text),
                           plainto_tsquery('english', %s)) AS rank
            FROM kn_chunk c
            JOIN kn_document d ON d.id = c.document_id
            WHERE to_tsvector('english', c.text) @@ plainto_tsquery('english', %s)
            ORDER BY rank DESC
            LIMIT %s
        """, (query, query, limit))
        rows = cur.fetchall()
        cur.close()
        return {
            "query": query,
            "results": [
                {
                    "chunk_id": r[0], "document_id": r[1], "document_title": r[2],
                    "ord": r[3], "text": r[4],
                    "locator": {"char_start": r[5], "char_end": r[6]},
                    "score": float(r[7]),
                }
                for r in rows
            ],
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Audit + the regeneration drill
# ---------------------------------------------------------------------------

@router.get("/events")
def list_events(limit: int = Query(50, ge=1, le=200)):
    """Expose the source-of-truth log so it stays visible and auditable."""
    conn = _conn()
    try:
        cur = conn.cursor()
        _ensure_schema(cur)
        cur.execute("""
            SELECT id, ts, actor, event_type, payload, confidence, epistemic_status
            FROM kn_event ORDER BY id DESC LIMIT %s
        """, (limit,))
        rows = cur.fetchall()
        cur.close()
        return [
            {"id": r[0], "ts": r[1].isoformat() if r[1] else None,
             "actor": r[2], "event_type": r[3], "payload": r[4],
             "confidence": r[5], "epistemic_status": r[6]}
            for r in rows
        ]
    finally:
        conn.close()


@router.post("/admin/rebuild-projections")
def rebuild_projections(user: str = Depends(get_current_user)):
    """THE regeneration drill.

    Blows away every chunk and rebuilds it from the documents (source of truth).
    If the rebuilt projection ever diverges from a fresh ingest, the truth is
    incomplete — and you want to discover that now, not in three years.
    Run this regularly. Returns before/after counts to prove equivalence.
    """
    conn = _conn()
    try:
        cur = conn.cursor()
        _ensure_schema(cur)

        cur.execute("SELECT count(*) FROM kn_chunk")
        before = cur.fetchone()[0]

        cur.execute("DELETE FROM kn_chunk")

        cur.execute("SELECT id, raw_content FROM kn_document WHERE raw_content IS NOT NULL")
        docs = cur.fetchall()
        rebuilt = 0
        for doc_id, raw in docs:
            rebuilt += _build_chunks_for_document(cur, doc_id, raw)

        # Mentions derive from chunks + concepts; rebuild after chunks.
        mentions = _build_mentions(cur)

        _append_event(
            cur, f"human:{user}", "projections_rebuilt",
            payload={"projections": ["kn_chunk", "kn_mention"],
                     "chunks_before": before, "chunks_after": rebuilt,
                     "mentions": mentions},
        )

        conn.commit()
        cur.close()
        return {
            "projection": ["kn_chunk", "kn_mention"],
            "chunks_before": before,
            "chunks_after": rebuilt,
            "documents_rebuilt": len(docs),
            "mentions": mentions,
            "equivalent": before == rebuilt,
        }
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, f"Rebuild failed: {e}")
    finally:
        conn.close()


# ===========================================================================
# DOMAIN MODEL — Concepts (anchors), Units (atoms), Relations (edges)
# All writes go through kn_event; the rows are read-only projections.
# ===========================================================================

# ---------------------------------------------------------------------------
# Concepts
# ---------------------------------------------------------------------------

@router.post("/concepts")
def create_concept(payload: dict = Body(...), user: str = Depends(get_current_user)):
    """Create a concept anchor. Deduplicated by slug; if it already exists the
    existing concept is returned (and any new alias recorded)."""
    name = (payload.get("name") or "").strip()
    status = (payload.get("status") or "candidate").strip()
    if not name:
        raise HTTPException(400, "name is required")
    if status not in ("candidate", "reviewed"):
        raise HTTPException(400, "status must be 'candidate' or 'reviewed'")

    slug = _slugify(name)
    conn = _conn()
    try:
        cur = conn.cursor()
        _ensure_schema(cur)

        cur.execute("SELECT id, merged_into FROM kn_concept WHERE slug = %s", (slug,))
        existing = cur.fetchone()
        if existing:
            canonical = _resolve_concept(cur, existing[0])
            conn.commit()
            cur.close()
            return {"id": canonical, "slug": slug, "duplicate": True}

        actor = f"human:{user}"
        event_id, _ = _append_event(
            cur, actor, "concept_created",
            payload={"name": name, "slug": slug, "status": status},
        )
        cur.execute("""
            INSERT INTO kn_concept (slug, name, status, create_event)
            VALUES (%s, %s, %s, %s) RETURNING id
        """, (slug, name, status, event_id))
        concept_id = cur.fetchone()[0]
        cur.execute("""
            INSERT INTO kn_concept_alias (concept_id, alias, alias_norm, source)
            VALUES (%s, %s, %s, 'canonical') ON CONFLICT DO NOTHING
        """, (concept_id, name, _normalize(name)))

        conn.commit()
        cur.close()
        return {"id": concept_id, "slug": slug, "event_id": event_id, "duplicate": False}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, f"Create concept failed: {e}")
    finally:
        conn.close()


@router.get("/concepts")
def list_concepts(status: Optional[str] = Query(None),
                  include_merged: bool = Query(False),
                  limit: int = Query(200, ge=1, le=1000)):
    conn = _conn()
    try:
        cur = conn.cursor()
        _ensure_schema(cur)
        where = []
        params = []
        if not include_merged:
            where.append("merged_into IS NULL")
        if status:
            where.append("status = %s")
            params.append(status)
        clause = ("WHERE " + " AND ".join(where)) if where else ""
        params.append(limit)
        cur.execute(f"""
            SELECT id, slug, name, status, merged_into,
                   (SELECT count(*) FROM kn_unit_concept uc WHERE uc.concept_id = kn_concept.id) AS units
            FROM kn_concept {clause}
            ORDER BY name LIMIT %s
        """, params)
        rows = cur.fetchall()
        cur.close()
        return [
            {"id": r[0], "slug": r[1], "name": r[2], "status": r[3],
             "merged_into": r[4], "units": r[5]}
            for r in rows
        ]
    finally:
        conn.close()


@router.get("/concepts/{concept_id}")
def get_concept(concept_id: int):
    conn = _conn()
    try:
        cur = conn.cursor()
        _ensure_schema(cur)
        cur.execute("SELECT id, slug, name, status, merged_into FROM kn_concept WHERE id = %s", (concept_id,))
        r = cur.fetchone()
        if not r:
            raise HTTPException(404, "Concept not found")
        cur.execute("SELECT alias, source FROM kn_concept_alias WHERE concept_id = %s ORDER BY alias", (concept_id,))
        aliases = [{"alias": a[0], "source": a[1]} for a in cur.fetchall()]
        cur.execute("""
            SELECT u.id, u.role, u.content, u.epistemic_status, u.confidence
            FROM kn_knowledge_unit u
            JOIN kn_unit_concept uc ON uc.unit_id = u.id
            WHERE uc.concept_id = %s AND u.status = 'active'
            ORDER BY u.id
        """, (concept_id,))
        units = [{"id": u[0], "role": u[1], "content": u[2],
                  "epistemic_status": u[3], "confidence": u[4]} for u in cur.fetchall()]
        cur.execute("""
            SELECT r.id, r.rel_type, r.dst_concept, c.name, r.confidence, r.status
            FROM kn_relation r JOIN kn_concept c ON c.id = r.dst_concept
            WHERE r.src_concept = %s ORDER BY r.rel_type
        """, (concept_id,))
        out_rel = [{"id": x[0], "type": x[1], "target_id": x[2], "target": x[3],
                    "confidence": x[4], "status": x[5]} for x in cur.fetchall()]
        cur.close()
        return {
            "id": r[0], "slug": r[1], "name": r[2], "status": r[3],
            "merged_into": r[4], "aliases": aliases, "units": units,
            "relations_out": out_rel,
        }
    finally:
        conn.close()


@router.post("/concepts/{concept_id}/aliases")
def add_alias(concept_id: int, payload: dict = Body(...), user: str = Depends(get_current_user)):
    alias = (payload.get("alias") or "").strip()
    if not alias:
        raise HTTPException(400, "alias is required")
    conn = _conn()
    try:
        cur = conn.cursor()
        _ensure_schema(cur)
        cur.execute("SELECT id FROM kn_concept WHERE id = %s", (concept_id,))
        if not cur.fetchone():
            raise HTTPException(404, "Concept not found")
        _append_event(cur, f"human:{user}", "concept_alias_added",
                      payload={"concept_id": concept_id, "alias": alias})
        cur.execute("""
            INSERT INTO kn_concept_alias (concept_id, alias, alias_norm, source)
            VALUES (%s, %s, %s, 'manual') ON CONFLICT DO NOTHING
        """, (concept_id, alias, _normalize(alias)))
        conn.commit()
        cur.close()
        return {"ok": True}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, f"Add alias failed: {e}")
    finally:
        conn.close()


@router.patch("/concepts/{concept_id}/review")
def review_concept(concept_id: int, user: str = Depends(get_current_user)):
    """Promote a candidate concept to reviewed (the human gate)."""
    conn = _conn()
    try:
        cur = conn.cursor()
        _ensure_schema(cur)
        cur.execute("SELECT status FROM kn_concept WHERE id = %s", (concept_id,))
        r = cur.fetchone()
        if not r:
            raise HTTPException(404, "Concept not found")
        _append_event(cur, f"human:{user}", "concept_reviewed",
                      payload={"concept_id": concept_id, "from": r[0], "to": "reviewed"})
        cur.execute("UPDATE kn_concept SET status = 'reviewed' WHERE id = %s", (concept_id,))
        conn.commit()
        cur.close()
        return {"ok": True, "status": "reviewed"}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, f"Review failed: {e}")
    finally:
        conn.close()


@router.post("/concepts/{concept_id}/merge")
def merge_concept(concept_id: int, payload: dict = Body(...), user: str = Depends(get_current_user)):
    """Merge `concept_id` INTO `into_id` (non-destructive: recorded as an event,
    aliases and links repointed, source flagged merged_into)."""
    into_id = payload.get("into_id")
    if not into_id:
        raise HTTPException(400, "into_id is required")
    if int(into_id) == int(concept_id):
        raise HTTPException(400, "Cannot merge a concept into itself")
    conn = _conn()
    try:
        cur = conn.cursor()
        _ensure_schema(cur)
        cur.execute("SELECT id, name FROM kn_concept WHERE id = %s", (concept_id,))
        src = cur.fetchone()
        cur.execute("SELECT id FROM kn_concept WHERE id = %s", (into_id,))
        dst = cur.fetchone()
        if not src or not dst:
            raise HTTPException(404, "Concept not found")

        _append_event(cur, f"human:{user}", "concept_merged",
                      payload={"from": concept_id, "into": into_id})

        # Source name becomes an alias of the survivor.
        cur.execute("""
            INSERT INTO kn_concept_alias (concept_id, alias, alias_norm, source)
            VALUES (%s, %s, %s, 'merge') ON CONFLICT DO NOTHING
        """, (into_id, src[1], _normalize(src[1])))

        # Move aliases (drop ones that already exist on the survivor first).
        cur.execute("""
            DELETE FROM kn_concept_alias a
            WHERE a.concept_id = %s
              AND EXISTS (SELECT 1 FROM kn_concept_alias b
                          WHERE b.concept_id = %s AND b.alias_norm = a.alias_norm)
        """, (concept_id, into_id))
        cur.execute("UPDATE kn_concept_alias SET concept_id = %s WHERE concept_id = %s",
                    (into_id, concept_id))

        # Repoint unit anchors (drop units already anchored to the survivor).
        cur.execute("""
            DELETE FROM kn_unit_concept uc
            WHERE uc.concept_id = %s
              AND EXISTS (SELECT 1 FROM kn_unit_concept x
                          WHERE x.unit_id = uc.unit_id AND x.concept_id = %s)
        """, (concept_id, into_id))
        cur.execute("UPDATE kn_unit_concept SET concept_id = %s WHERE concept_id = %s",
                    (into_id, concept_id))

        # Repoint relations: remove self-loops and duplicates, then repoint.
        cur.execute("""
            DELETE FROM kn_relation
            WHERE (src_concept = %s AND dst_concept = %s)
               OR (src_concept = %s AND dst_concept = %s)
        """, (concept_id, into_id, into_id, concept_id))
        cur.execute("""
            DELETE FROM kn_relation a
            WHERE a.src_concept = %s
              AND EXISTS (SELECT 1 FROM kn_relation b
                          WHERE b.src_concept = %s AND b.dst_concept = a.dst_concept
                            AND b.rel_type = a.rel_type)
        """, (concept_id, into_id))
        cur.execute("UPDATE kn_relation SET src_concept = %s WHERE src_concept = %s",
                    (into_id, concept_id))
        cur.execute("""
            DELETE FROM kn_relation a
            WHERE a.dst_concept = %s
              AND EXISTS (SELECT 1 FROM kn_relation b
                          WHERE b.dst_concept = %s AND b.src_concept = a.src_concept
                            AND b.rel_type = a.rel_type)
        """, (concept_id, into_id))
        cur.execute("UPDATE kn_relation SET dst_concept = %s WHERE dst_concept = %s",
                    (into_id, concept_id))

        # Flag the source as merged.
        cur.execute("UPDATE kn_concept SET merged_into = %s WHERE id = %s", (into_id, concept_id))

        conn.commit()
        cur.close()
        return {"ok": True, "merged": concept_id, "into": into_id}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, f"Merge failed: {e}")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Knowledge Units (the atom of content)
# ---------------------------------------------------------------------------

@router.post("/units")
def author_unit(payload: dict = Body(...), user: str = Depends(get_current_user)):
    """Author a knowledge unit anchored to one or more concepts.

    Records a `unit_authored` event (the content lives in the event = truth)
    and materializes the unit + its concept anchors.
    """
    content = (payload.get("content") or "").strip()
    role = (payload.get("role") or "claim").strip()
    concept_ids = payload.get("concept_ids") or []
    epistemic_status = (payload.get("epistemic_status") or "authored_human").strip()
    confidence = float(payload.get("confidence") if payload.get("confidence") is not None else 1.0)
    basis = payload.get("basis") or []

    if not content:
        raise HTTPException(400, "content is required")
    if not isinstance(concept_ids, list) or not concept_ids:
        raise HTTPException(400, "concept_ids must be a non-empty list")

    conn = _conn()
    try:
        cur = conn.cursor()
        _ensure_schema(cur)

        # Resolve anchors through any merges; validate existence.
        resolved = []
        for cid in concept_ids:
            canonical = _resolve_concept(cur, cid)
            if canonical is None:
                raise HTTPException(404, f"Concept {cid} not found")
            resolved.append(canonical)
        resolved = sorted(set(resolved))

        actor = f"human:{user}"
        event_id, _ = _append_event(
            cur, actor, "unit_authored",
            payload={"content": content, "role": role, "concept_ids": resolved},
            basis=basis, confidence=confidence, epistemic_status=epistemic_status,
        )
        cur.execute("""
            INSERT INTO kn_knowledge_unit
                (content, role, epistemic_status, confidence, create_event)
            VALUES (%s, %s, %s, %s, %s) RETURNING id
        """, (content, role, epistemic_status, confidence, event_id))
        unit_id = cur.fetchone()[0]
        for cid in resolved:
            cur.execute("""
                INSERT INTO kn_unit_concept (unit_id, concept_id)
                VALUES (%s, %s) ON CONFLICT DO NOTHING
            """, (unit_id, cid))

        conn.commit()
        cur.close()
        return {"id": unit_id, "event_id": event_id, "concept_ids": resolved}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, f"Author unit failed: {e}")
    finally:
        conn.close()


@router.get("/units")
def list_units(role: Optional[str] = Query(None),
               concept_id: Optional[int] = Query(None),
               limit: int = Query(100, ge=1, le=500)):
    conn = _conn()
    try:
        cur = conn.cursor()
        _ensure_schema(cur)
        where = ["u.status = 'active'"]
        params = []
        join = ""
        if concept_id is not None:
            join = "JOIN kn_unit_concept uc ON uc.unit_id = u.id"
            where.append("uc.concept_id = %s")
            params.append(concept_id)
        if role:
            where.append("u.role = %s")
            params.append(role)
        params.append(limit)
        cur.execute(f"""
            SELECT DISTINCT u.id, u.role, u.content, u.epistemic_status, u.confidence, u.version
            FROM kn_knowledge_unit u {join}
            WHERE {' AND '.join(where)}
            ORDER BY u.id LIMIT %s
        """, params)
        rows = cur.fetchall()
        cur.close()
        return [
            {"id": r[0], "role": r[1], "content": r[2], "epistemic_status": r[3],
             "confidence": r[4], "version": r[5]}
            for r in rows
        ]
    finally:
        conn.close()


@router.put("/units/{unit_id}")
def edit_unit(unit_id: int, payload: dict = Body(...), user: str = Depends(get_current_user)):
    """Edit a unit's content. Recorded as a `unit_edited` event (never a silent
    UPDATE) and the version is bumped."""
    content = (payload.get("content") or "").strip()
    role = payload.get("role")
    if not content:
        raise HTTPException(400, "content is required")
    conn = _conn()
    try:
        cur = conn.cursor()
        _ensure_schema(cur)
        cur.execute("SELECT version, role FROM kn_knowledge_unit WHERE id = %s AND status = 'active'", (unit_id,))
        r = cur.fetchone()
        if not r:
            raise HTTPException(404, "Unit not found")
        new_role = (role or r[1]).strip()
        _append_event(cur, f"human:{user}", "unit_edited",
                      payload={"unit_id": unit_id, "content": content, "role": new_role,
                               "version": r[0] + 1})
        cur.execute("""
            UPDATE kn_knowledge_unit
            SET content = %s, role = %s, version = version + 1, updated_at = NOW()
            WHERE id = %s
        """, (content, new_role, unit_id))
        conn.commit()
        cur.close()
        return {"ok": True, "version": r[0] + 1}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, f"Edit unit failed: {e}")
    finally:
        conn.close()


@router.delete("/units/{unit_id}")
def retract_unit(unit_id: int, user: str = Depends(get_current_user)):
    """Soft-retract a unit (recorded as a `unit_retracted` event)."""
    conn = _conn()
    try:
        cur = conn.cursor()
        _ensure_schema(cur)
        cur.execute("SELECT status FROM kn_knowledge_unit WHERE id = %s", (unit_id,))
        r = cur.fetchone()
        if not r:
            raise HTTPException(404, "Unit not found")
        _append_event(cur, f"human:{user}", "unit_retracted", payload={"unit_id": unit_id})
        cur.execute("UPDATE kn_knowledge_unit SET status = 'retracted', updated_at = NOW() WHERE id = %s", (unit_id,))
        conn.commit()
        cur.close()
        return {"ok": True}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, f"Retract failed: {e}")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Relations (typed edges between concepts)
# ---------------------------------------------------------------------------

@router.post("/relations")
def assert_relation(payload: dict = Body(...), user: str = Depends(get_current_user)):
    """Assert a typed relation between two concepts (an assertion with
    confidence, not a hard truth). Recorded as a `relation_asserted` event."""
    src = payload.get("src_concept_id")
    dst = payload.get("dst_concept_id")
    rel_type = (payload.get("rel_type") or "").strip()
    confidence = float(payload.get("confidence") if payload.get("confidence") is not None else 1.0)
    status = (payload.get("status") or "candidate").strip()
    if not src or not dst or not rel_type:
        raise HTTPException(400, "src_concept_id, dst_concept_id and rel_type are required")
    if int(src) == int(dst):
        raise HTTPException(400, "A relation must connect two different concepts")

    conn = _conn()
    try:
        cur = conn.cursor()
        _ensure_schema(cur)
        src_c = _resolve_concept(cur, src)
        dst_c = _resolve_concept(cur, dst)
        if src_c is None or dst_c is None:
            raise HTTPException(404, "Concept not found")

        event_id, _ = _append_event(
            cur, f"human:{user}", "relation_asserted",
            payload={"src": src_c, "dst": dst_c, "rel_type": rel_type},
            confidence=confidence, epistemic_status=status,
        )
        cur.execute("""
            INSERT INTO kn_relation (src_concept, dst_concept, rel_type, confidence, status, create_event)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (src_concept, dst_concept, rel_type)
            DO UPDATE SET confidence = EXCLUDED.confidence, status = EXCLUDED.status
            RETURNING id
        """, (src_c, dst_c, rel_type, confidence, status, event_id))
        rel_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
        return {"id": rel_id, "src_concept_id": src_c, "dst_concept_id": dst_c, "rel_type": rel_type}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, f"Assert relation failed: {e}")
    finally:
        conn.close()


@router.get("/concepts/{concept_id}/neighborhood")
def concept_neighborhood(concept_id: int, depth: int = Query(1, ge=1, le=3)):
    """Return the concept's local graph up to `depth` hops (recursive CTE —
    the reason we stayed on Postgres instead of Neo4j)."""
    conn = _conn()
    try:
        cur = conn.cursor()
        _ensure_schema(cur)
        cur.execute("SELECT id FROM kn_concept WHERE id = %s", (concept_id,))
        if not cur.fetchone():
            raise HTTPException(404, "Concept not found")
        cur.execute("""
            WITH RECURSIVE nb AS (
                SELECT %s::bigint AS concept_id, 0 AS d
                UNION
                SELECT CASE WHEN r.src_concept = nb.concept_id THEN r.dst_concept
                            ELSE r.src_concept END,
                       nb.d + 1
                FROM nb
                JOIN kn_relation r
                  ON (r.src_concept = nb.concept_id OR r.dst_concept = nb.concept_id)
                WHERE nb.d < %s
            )
            SELECT DISTINCT c.id, c.name, c.status, MIN(nb.d) AS dist
            FROM nb JOIN kn_concept c ON c.id = nb.concept_id
            WHERE c.merged_into IS NULL
            GROUP BY c.id, c.name, c.status
            ORDER BY dist, c.name
        """, (concept_id, depth))
        nodes = [{"id": r[0], "name": r[1], "status": r[2], "distance": r[3]} for r in cur.fetchall()]
        node_ids = [n["id"] for n in nodes]
        edges = []
        if node_ids:
            cur.execute("""
                SELECT id, src_concept, dst_concept, rel_type, confidence, status
                FROM kn_relation
                WHERE src_concept = ANY(%s) AND dst_concept = ANY(%s)
            """, (node_ids, node_ids))
            edges = [{"id": r[0], "source": r[1], "target": r[2], "type": r[3],
                      "confidence": r[4], "status": r[5]} for r in cur.fetchall()]
        cur.close()
        return {"nodes": nodes, "edges": edges}
    finally:
        conn.close()


@router.post("/admin/relink-mentions")
def relink_mentions(user: str = Depends(get_current_user)):
    """Rebuild the lexical mention projection (concept terms -> chunks)."""
    conn = _conn()
    try:
        cur = conn.cursor()
        _ensure_schema(cur)
        n = _build_mentions(cur)
        _append_event(cur, f"human:{user}", "mentions_relinked", payload={"mentions": n})
        conn.commit()
        cur.close()
        return {"ok": True, "mentions": n}
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, f"Relink failed: {e}")
    finally:
        conn.close()

