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

# Closed vocabulary for relation types. The extractor is instructed to emit only
# these; anything else is normalized on write (synonyms mapped, unknowns ->
# 'related_to') so the graph stays consistent and language-agnostic.
REL_TYPES = {
    "is_a", "part_of", "has_part", "requires", "causes", "produces",
    "enables", "defined_by", "example_of", "contradicts", "related_to",
}
REL_TYPE_ALIASES = {
    # English synonyms
    "isa": "is_a", "type_of": "is_a", "kind_of": "is_a", "subclass_of": "is_a",
    "instance_of": "is_a",
    "belongs_to": "part_of", "component_of": "part_of", "member_of": "part_of",
    "contains": "has_part", "includes": "has_part",
    "needs": "requires", "depends_on": "requires", "uses": "requires",
    "cause": "causes", "leads_to": "causes", "results_in": "causes",
    "converts_to": "produces", "generates": "produces", "creates": "produces",
    "forms": "produces", "yields": "produces",
    "allows": "enables",
    "defines": "defined_by", "definition_of": "defined_by",
    "example": "example_of", "instance": "example_of",
    "opposes": "contradicts",
    "related": "related_to", "relates_to": "related_to", "associated_with": "related_to",
    # Spanish (extractor sometimes ignores the English-only instruction)
    "es_un": "is_a", "es_una": "is_a", "tipo_de": "is_a",
    "parte_de": "part_of", "pertenece_a": "part_of",
    "contiene": "has_part",
    "requiere": "requires", "necesita": "requires", "usa": "requires",
    "depende_de": "requires", "absorbe": "requires",
    "causa": "causes", "provoca": "causes",
    "convierte_en": "produces", "converte_en": "produces", "produce": "produces",
    "fija": "produces", "genera": "produces", "forma": "produces",
    "permite": "enables",
    "define": "defined_by",
    "ejemplo_de": "example_of",
    "contradice": "contradicts",
    "relacionado_con": "related_to",
}

# Embeddings (Phase 3): concept dedup via pgvector cosine similarity. Vectors
# are produced by the Mac worker (Ollama) and stored in kn_embedding. The
# dimension is fixed by the chosen model (bge-m3 / mxbai-embed-large -> 1024).
EMBED_DIM = 1024
EMBED_MODEL_DEFAULT = "bge-m3"


# ---------------------------------------------------------------------------
# Connection + schema
# ---------------------------------------------------------------------------

def _conn():
    return psycopg2.connect(os.getenv("TASKS_URL"), sslmode="require")


_SCHEMA_READY = False


def _ensure_schema(cur):
    # DDL is expensive and, run concurrently (e.g. the worker polling while an
    # ingest happens), can deadlock on catalog locks. migrate() runs this once
    # at startup and commits; after that every request skips it via this flag.
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
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
    # Optional provenance link to a Library item. NULL for documents entered by
    # hand (notes, transcripts...) that have no library source. ON DELETE SET
    # NULL so removing a library item never orphans the extracted knowledge.
    cur.execute("ALTER TABLE kn_document ADD COLUMN IF NOT EXISTS library_item_id BIGINT")
    cur.execute("CREATE INDEX IF NOT EXISTS kn_document_libitem_idx ON kn_document(library_item_id)")

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

    # ---- Job queue (pulled by an external worker, e.g. the Mac Mini) ----
    # The worker polls /kn/worker/claim (outbound only), runs a local LLM, and
    # posts candidates back. Jobs are leased: a stale 'claimed' job is requeued.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS kn_job (
            id           BIGSERIAL PRIMARY KEY,
            kind         TEXT NOT NULL DEFAULT 'extract',
            document_id  BIGINT REFERENCES kn_document(id) ON DELETE CASCADE,
            status       TEXT NOT NULL DEFAULT 'pending',
            worker_id    TEXT,
            attempts     INTEGER NOT NULL DEFAULT 0,
            error        TEXT,
            result_meta  JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            claimed_at   TIMESTAMPTZ,
            finished_at  TIMESTAMPTZ
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS kn_job_status_idx ON kn_job(status, id)")
    cur.execute("CREATE INDEX IF NOT EXISTS kn_job_doc_idx    ON kn_job(document_id)")

    # ---- Chat turns (Phase 4 RAG) ----
    # A question is queued here; the Mac worker embeds it, runs vector search,
    # generates an answer with the local LLM, and posts it back. Leased like jobs.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS kn_chat (
            id           BIGSERIAL PRIMARY KEY,
            question     TEXT NOT NULL,
            answer       TEXT,
            status       TEXT NOT NULL DEFAULT 'pending',
            top_k        INTEGER NOT NULL DEFAULT 6,
            context      JSONB NOT NULL DEFAULT '[]'::jsonb,
            model        TEXT,
            error        TEXT,
            worker_id    TEXT,
            attempts     INTEGER NOT NULL DEFAULT 0,
            create_event BIGINT REFERENCES kn_event(id),
            created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            claimed_at   TIMESTAMPTZ,
            finished_at  TIMESTAMPTZ
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS kn_chat_status_idx ON kn_chat(status, id)")

    _SCHEMA_READY = True


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


_EMBED_SCHEMA_READY = False


def _ensure_embedding_schema(cur):
    """Isolated pgvector schema (kn_embedding + HNSW cosine index).

    Kept separate from the core schema so a missing 'vector' extension only
    breaks embedding features, not all of /kn. Created lazily by the embedding
    endpoints; guarded so the DDL runs at most once per process.
    """
    global _EMBED_SCHEMA_READY
    if _EMBED_SCHEMA_READY:
        return
    cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS kn_embedding (
            id          BIGSERIAL PRIMARY KEY,
            kind        TEXT NOT NULL,
            ref_id      BIGINT NOT NULL,
            model       TEXT NOT NULL,
            dim         INTEGER NOT NULL,
            vec         vector({EMBED_DIM}) NOT NULL,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (kind, ref_id, model)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS kn_embedding_ref_idx ON kn_embedding(kind, ref_id)")
    cur.execute("""
        CREATE INDEX IF NOT EXISTS kn_embedding_hnsw
        ON kn_embedding USING hnsw (vec vector_cosine_ops)
    """)
    _EMBED_SCHEMA_READY = True


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


def _canonical_rel_type(raw: str) -> str:
    """Map any relation label onto the closed vocabulary.

    Lowercases, collapses spaces/hyphens to underscores, resolves synonyms.
    Unknown labels fall back to 'related_to' so no edge is silently dropped.
    """
    key = re.sub(r"[\s-]+", "_", (raw or "").strip().lower())
    if key in REL_TYPES:
        return key
    return REL_TYPE_ALIASES.get(key, "related_to")


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


def _ensure_concept(cur, name, actor, generated=False, aliases=None):
    """Get-or-create a concept by slug, returning its canonical id.

    Used by the worker result handler. Machine-created concepts enter as
    'candidate' with a `concept_generated` event, so they pass the human gate.
    """
    name = (name or "").strip()
    if not name:
        return None
    slug = _slugify(name)
    cur.execute("SELECT id FROM kn_concept WHERE slug = %s", (slug,))
    row = cur.fetchone()
    if row:
        return _resolve_concept(cur, row[0])

    event_type = "concept_generated" if generated else "concept_created"
    status = "candidate" if generated else "reviewed"
    event_id, _ = _append_event(
        cur, actor, event_type,
        payload={"name": name, "slug": slug, "status": status},
        epistemic_status=("generated_machine" if generated else "asserted"),
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
    for al in (aliases or []):
        al = (al or "").strip()
        if al:
            cur.execute("""
                INSERT INTO kn_concept_alias (concept_id, alias, alias_norm, source)
                VALUES (%s, %s, %s, 'generated') ON CONFLICT DO NOTHING
            """, (concept_id, al, _normalize(al)))
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
    library_item_id = payload.get("library_item_id")

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
            # Backfill the library link if this ingest supplies one and the
            # existing row lacks it. The link is an EVENT first (source of
            # truth); the column is just its projection.
            if library_item_id is not None:
                cur.execute(
                    "SELECT 1 FROM kn_document WHERE id = %s AND library_item_id IS NULL",
                    (existing[0],))
                if cur.fetchone():
                    _append_event(
                        cur, f"human:{user}", "document_library_linked",
                        payload={"document_id": existing[0],
                                 "library_item_id": int(library_item_id)})
                    cur.execute(
                        "UPDATE kn_document SET library_item_id = %s WHERE id = %s",
                        (int(library_item_id), existing[0]))
                    conn.commit()
            cur.close()
            return {"document_id": existing[0], "duplicate": True}

        actor = f"human:{user}"
        event_id, _ = _append_event(
            cur, actor, "document_ingested",
            payload={"title": title, "source_type": source_type, "sha256": sha,
                     "library_item_id": library_item_id},
        )

        cur.execute("""
            INSERT INTO kn_document
                (source_type, title, sha256, raw_content, ingest_event, library_item_id)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (source_type, title, sha, content, event_id,
              int(library_item_id) if library_item_id is not None else None))
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


def _extract_pdf_text_from_b2(file_path: str) -> str:
    """Download a PDF stored on B2 and return its extracted text. Empty string
    on any failure (missing text layer, download error, etc.)."""
    import io

    from routers.media import _get_b2

    try:
        _, bucket = _get_b2()
        buf = io.BytesIO()
        bucket.download_file_by_name(file_path).save(buf)
        data = buf.getvalue()
    except Exception:
        return ""
    if not data:
        return ""

    try:
        import pdfplumber

        pages = []
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for page in pdf.pages:
                t = page.extract_text() or ""
                if t.strip():
                    pages.append(t.strip())
        return "\n\n".join(pages).strip()
    except Exception:
        return ""


@router.post("/documents/from-library")
def ingest_from_library(payload: dict = Body(...), user: str = Depends(get_current_user)):
    """Ingest a Library item into the knowledge base, assembling its text from
    the item's summary + notes + highlights (and the uploaded PDF's full text,
    if present), and recording the provenance link (kn_document.library_item_id)
    so extracted units trace back to this source.

    Idempotent by content hash like /documents. Returns the same shape plus the
    assembled title/source_type.
    """
    library_item_id = payload.get("library_item_id")
    if library_item_id is None:
        raise HTTPException(400, "library_item_id is required")
    lib_id = int(library_item_id)

    conn = _conn()
    try:
        cur = conn.cursor()
        _ensure_schema(cur)

        cur.execute("""
            SELECT title, type, coalesce(summary, ''), coalesce(authors::text, ''),
                   file_path
            FROM lib_item WHERE id = %s
        """, (lib_id,))
        item = cur.fetchone()
        if not item:
            raise HTTPException(404, "library item not found")
        title, lib_type, summary, authors_raw, file_path = item

        # authors is a JSONB array of {"name": ...} objects; flatten to names.
        authors = ""
        if authors_raw:
            try:
                parsed = json.loads(authors_raw)
                if isinstance(parsed, list):
                    names = [
                        (a.get("name") if isinstance(a, dict) else str(a))
                        for a in parsed
                    ]
                    authors = ", ".join(n for n in names if n)
                elif isinstance(parsed, str):
                    authors = parsed
            except (ValueError, TypeError):
                authors = authors_raw

        cur.execute("""
            SELECT coalesce(body_md, '') FROM lib_note
            WHERE item_id = %s ORDER BY id
        """, (lib_id,))
        notes = [r[0] for r in cur.fetchall() if r[0].strip()]

        cur.execute("""
            SELECT coalesce(quote, ''), coalesce(comment, '') FROM lib_highlight
            WHERE item_id = %s ORDER BY id
        """, (lib_id,))
        highlights = cur.fetchall()

        # Assemble a single text artifact. Highlights (the user's own extracted
        # quotes) are the richest material for extraction.
        parts = []
        if authors:
            parts.append(f"Authors: {authors}")
        if summary.strip():
            parts.append(summary.strip())
        if highlights:
            parts.append("Highlights:")
            for quote, comment in highlights:
                line = f"- {quote.strip()}" if quote.strip() else ""
                if comment.strip():
                    line += f" ({comment.strip()})" if line else f"- {comment.strip()}"
                if line:
                    parts.append(line)
        if notes:
            parts.append("Notes:")
            parts.extend(notes)

        # For papers where only the PDF is uploaded (no summary/notes/highlights),
        # extract the PDF's full text so it can still be ingested.
        if file_path:
            pdf_text = _extract_pdf_text_from_b2(file_path)
            if pdf_text:
                parts.append("Full text:")
                parts.append(pdf_text)

        content = "\n\n".join(parts).strip()
        if not content:
            raise HTTPException(
                400,
                "library item has no summary, notes, highlights or readable PDF text to ingest",
            )

        source_type = lib_type if lib_type in VALID_SOURCE_TYPES else "note"
        sha = _sha256(content)

        cur.execute("SELECT id, library_item_id FROM kn_document WHERE sha256 = %s", (sha,))
        existing = cur.fetchone()
        if existing:
            # Same content already ingested. Record the link as an event (truth)
            # and project it onto the column only if not already linked.
            if existing[1] is None:
                _append_event(
                    cur, f"human:{user}", "document_library_linked",
                    payload={"document_id": existing[0], "library_item_id": lib_id})
                cur.execute(
                    "UPDATE kn_document SET library_item_id = %s WHERE id = %s",
                    (lib_id, existing[0]))
                conn.commit()
            cur.close()
            return {"document_id": existing[0], "duplicate": True,
                    "title": title, "source_type": source_type}

        actor = f"human:{user}"
        event_id, _ = _append_event(
            cur, actor, "document_ingested",
            payload={"title": title, "source_type": source_type, "sha256": sha,
                     "library_item_id": lib_id},
        )
        cur.execute("""
            INSERT INTO kn_document
                (source_type, title, sha256, raw_content, ingest_event, library_item_id)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (source_type, title, sha, content, event_id, lib_id))
        document_id = cur.fetchone()[0]
        n_chunks = _build_chunks_for_document(cur, document_id, content)

        # Auto-enqueue extraction so the library button is one-click: ingest ->
        # the worker will extract concepts/units and embed them, then it's askable.
        cur.execute("""
            INSERT INTO kn_job (kind, document_id, status)
            VALUES ('extract', %s, 'pending') RETURNING id
        """, (document_id,))
        job_id = cur.fetchone()[0]
        _append_event(cur, actor, "extraction_requested",
                      payload={"job_id": job_id, "document_id": document_id})

        conn.commit()
        cur.close()
        return {"document_id": document_id, "event_id": event_id, "chunks": n_chunks,
                "job_id": job_id, "duplicate": False,
                "title": title, "source_type": source_type}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, f"Library ingestion failed: {e}")
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


@router.patch("/concepts/{concept_id}/reject")
def reject_concept(concept_id: int, user: str = Depends(get_current_user)):
    """Reject a candidate concept (non-destructive: status -> rejected)."""
    conn = _conn()
    try:
        cur = conn.cursor()
        _ensure_schema(cur)
        cur.execute("SELECT status FROM kn_concept WHERE id = %s", (concept_id,))
        r = cur.fetchone()
        if not r:
            raise HTTPException(404, "Concept not found")
        _append_event(cur, f"human:{user}", "concept_rejected",
                      payload={"concept_id": concept_id, "from": r[0], "to": "rejected"})
        cur.execute("UPDATE kn_concept SET status = 'rejected' WHERE id = %s", (concept_id,))
        conn.commit()
        cur.close()
        return {"ok": True, "status": "rejected"}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, f"Reject failed: {e}")
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


@router.patch("/units/{unit_id}/review")
def review_unit(unit_id: int, user: str = Depends(get_current_user)):
    """Accept a machine-generated unit (epistemic_status -> reviewed_human)."""
    conn = _conn()
    try:
        cur = conn.cursor()
        _ensure_schema(cur)
        cur.execute("SELECT epistemic_status FROM kn_knowledge_unit WHERE id = %s AND status = 'active'", (unit_id,))
        r = cur.fetchone()
        if not r:
            raise HTTPException(404, "Unit not found")
        _append_event(cur, f"human:{user}", "unit_reviewed",
                      payload={"unit_id": unit_id, "from": r[0], "to": "reviewed_human"})
        cur.execute("UPDATE kn_knowledge_unit SET epistemic_status = 'reviewed_human', updated_at = NOW() WHERE id = %s", (unit_id,))
        conn.commit()
        cur.close()
        return {"ok": True, "epistemic_status": "reviewed_human"}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, f"Review unit failed: {e}")
    finally:
        conn.close()


@router.patch("/units/{unit_id}/reject")
def reject_unit(unit_id: int, user: str = Depends(get_current_user)):
    """Reject a candidate unit (soft-retract via a `unit_rejected` event)."""
    conn = _conn()
    try:
        cur = conn.cursor()
        _ensure_schema(cur)
        cur.execute("SELECT status FROM kn_knowledge_unit WHERE id = %s", (unit_id,))
        r = cur.fetchone()
        if not r:
            raise HTTPException(404, "Unit not found")
        _append_event(cur, f"human:{user}", "unit_rejected", payload={"unit_id": unit_id})
        cur.execute("UPDATE kn_knowledge_unit SET status = 'retracted', updated_at = NOW() WHERE id = %s", (unit_id,))
        conn.commit()
        cur.close()
        return {"ok": True, "status": "rejected"}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, f"Reject unit failed: {e}")
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
    rel_type_raw = (payload.get("rel_type") or "").strip()
    confidence = float(payload.get("confidence") if payload.get("confidence") is not None else 1.0)
    status = (payload.get("status") or "candidate").strip()
    if not src or not dst or not rel_type_raw:
        raise HTTPException(400, "src_concept_id, dst_concept_id and rel_type are required")
    # Enforce the closed vocabulary: synonyms are mapped, unknown labels are
    # rejected so a human can't silently pollute the graph.
    _key = re.sub(r"[\s-]+", "_", rel_type_raw.lower())
    rel_type = _key if _key in REL_TYPES else REL_TYPE_ALIASES.get(_key)
    if rel_type is None:
        raise HTTPException(400, f"Invalid rel_type '{rel_type_raw}'. Allowed: {sorted(REL_TYPES)}")
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


@router.patch("/relations/{relation_id}/review")
def review_relation(relation_id: int, user: str = Depends(get_current_user)):
    """Promote a candidate relation to reviewed (the human gate)."""
    conn = _conn()
    try:
        cur = conn.cursor()
        _ensure_schema(cur)
        cur.execute("SELECT status FROM kn_relation WHERE id = %s", (relation_id,))
        r = cur.fetchone()
        if not r:
            raise HTTPException(404, "Relation not found")
        _append_event(cur, f"human:{user}", "relation_reviewed",
                      payload={"relation_id": relation_id, "from": r[0], "to": "reviewed"})
        cur.execute("UPDATE kn_relation SET status = 'reviewed' WHERE id = %s", (relation_id,))
        conn.commit()
        cur.close()
        return {"ok": True, "status": "reviewed"}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, f"Review relation failed: {e}")
    finally:
        conn.close()


@router.patch("/relations/{relation_id}/reject")
def reject_relation(relation_id: int, user: str = Depends(get_current_user)):
    """Reject a candidate relation (non-destructive: status -> rejected)."""
    conn = _conn()
    try:
        cur = conn.cursor()
        _ensure_schema(cur)
        cur.execute("SELECT status FROM kn_relation WHERE id = %s", (relation_id,))
        r = cur.fetchone()
        if not r:
            raise HTTPException(404, "Relation not found")
        _append_event(cur, f"human:{user}", "relation_rejected",
                      payload={"relation_id": relation_id, "from": r[0], "to": "rejected"})
        cur.execute("UPDATE kn_relation SET status = 'rejected' WHERE id = %s", (relation_id,))
        conn.commit()
        cur.close()
        return {"ok": True, "status": "rejected"}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, f"Reject relation failed: {e}")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Review queue (everything a human still needs to approve/reject)
# ---------------------------------------------------------------------------

@router.get("/review/queue")
def review_queue(limit: int = Query(100, ge=1, le=500),
                 user: str = Depends(get_current_user)):
    """Single pane of everything pending human review: candidate concepts,
    candidate relations, and machine-generated units still unreviewed."""
    conn = _conn()
    try:
        cur = conn.cursor()
        _ensure_schema(cur)

        cur.execute("""
            SELECT id, slug, name
            FROM kn_concept
            WHERE status = 'candidate' AND merged_into IS NULL
            ORDER BY id LIMIT %s
        """, (limit,))
        concepts = [{"id": r[0], "slug": r[1], "name": r[2]} for r in cur.fetchall()]

        cur.execute("""
            SELECT r.id, r.rel_type, r.src_concept, s.name, r.dst_concept, d.name, r.confidence
            FROM kn_relation r
            JOIN kn_concept s ON s.id = r.src_concept
            JOIN kn_concept d ON d.id = r.dst_concept
            WHERE r.status = 'candidate'
            ORDER BY r.id LIMIT %s
        """, (limit,))
        relations = [{"id": r[0], "rel_type": r[1], "src_id": r[2], "src": r[3],
                      "dst_id": r[4], "dst": r[5], "confidence": r[6]} for r in cur.fetchall()]

        cur.execute("""
            SELECT u.id, u.role, u.content, u.confidence,
                   COALESCE(array_agg(c.name) FILTER (WHERE c.id IS NOT NULL), '{}') AS concepts
            FROM kn_knowledge_unit u
            LEFT JOIN kn_unit_concept uc ON uc.unit_id = u.id
            LEFT JOIN kn_concept c ON c.id = uc.concept_id
            WHERE u.status = 'active' AND u.epistemic_status = 'generated_machine'
            GROUP BY u.id, u.role, u.content, u.confidence
            ORDER BY u.id LIMIT %s
        """, (limit,))
        units = [{"id": r[0], "role": r[1], "content": r[2], "confidence": r[3],
                  "concepts": list(r[4])} for r in cur.fetchall()]

        cur.close()
        return {
            "concepts": concepts,
            "relations": relations,
            "units": units,
            "counts": {"concepts": len(concepts),
                       "relations": len(relations),
                       "units": len(units)},
        }
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


# ===========================================================================
# EXTRACTION — job queue pulled by an external worker (e.g. the Mac Mini)
# The backend never calls an LLM. It enqueues a job; the worker claims it,
# runs a local model, and posts candidates back as `*_generated` events.
# ===========================================================================

LEASE_MINUTES = 10


@router.post("/documents/{document_id}/extract")
def enqueue_extract(document_id: int, user: str = Depends(get_current_user)):
    """Enqueue an extraction job for a document (idempotent while one is live)."""
    conn = _conn()
    try:
        cur = conn.cursor()
        _ensure_schema(cur)
        cur.execute("SELECT id FROM kn_document WHERE id = %s", (document_id,))
        if not cur.fetchone():
            raise HTTPException(404, "Document not found")

        cur.execute("""
            SELECT id FROM kn_job
            WHERE document_id = %s AND kind = 'extract' AND status IN ('pending', 'claimed')
        """, (document_id,))
        live = cur.fetchone()
        if live:
            cur.close()
            return {"job_id": live[0], "status": "already_queued"}

        cur.execute("""
            INSERT INTO kn_job (kind, document_id, status)
            VALUES ('extract', %s, 'pending') RETURNING id
        """, (document_id,))
        job_id = cur.fetchone()[0]
        _append_event(cur, f"human:{user}", "extraction_requested",
                      payload={"job_id": job_id, "document_id": document_id})
        conn.commit()
        cur.close()
        return {"job_id": job_id, "status": "pending"}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, f"Enqueue failed: {e}")
    finally:
        conn.close()


@router.get("/jobs")
def list_jobs(status: Optional[str] = Query(None), limit: int = Query(50, ge=1, le=200)):
    conn = _conn()
    try:
        cur = conn.cursor()
        _ensure_schema(cur)
        if status:
            cur.execute("""
                SELECT id, kind, document_id, status, attempts, error, created_at, finished_at
                FROM kn_job WHERE status = %s ORDER BY id DESC LIMIT %s
            """, (status, limit))
        else:
            cur.execute("""
                SELECT id, kind, document_id, status, attempts, error, created_at, finished_at
                FROM kn_job ORDER BY id DESC LIMIT %s
            """, (limit,))
        rows = cur.fetchall()
        cur.close()
        return [
            {"id": r[0], "kind": r[1], "document_id": r[2], "status": r[3],
             "attempts": r[4], "error": r[5],
             "created_at": r[6].isoformat() if r[6] else None,
             "finished_at": r[7].isoformat() if r[7] else None}
            for r in rows
        ]
    finally:
        conn.close()


@router.post("/worker/claim")
def worker_claim(payload: dict = Body(default={}), user: str = Depends(get_current_user)):
    """Worker pulls the next pending job (with a lease). Returns the job plus the
    document's chunks so the worker has everything it needs to extract offline."""
    worker_id = (payload.get("worker_id") or "worker").strip()
    conn = _conn()
    try:
        cur = conn.cursor()
        _ensure_schema(cur)

        # Requeue stale leases (crashed workers).
        cur.execute("""
            UPDATE kn_job SET status = 'pending', worker_id = NULL
            WHERE status = 'claimed'
              AND claimed_at < NOW() - INTERVAL '%s minutes'
        """ % LEASE_MINUTES)

        # Atomically claim the oldest pending job.
        cur.execute("""
            UPDATE kn_job
            SET status = 'claimed', worker_id = %s, claimed_at = NOW(), attempts = attempts + 1
            WHERE id = (
                SELECT id FROM kn_job
                WHERE status = 'pending'
                ORDER BY id
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            RETURNING id, kind, document_id
        """, (worker_id,))
        job = cur.fetchone()
        if not job:
            conn.commit()
            cur.close()
            return {"job": None}

        job_id, kind, document_id = job
        cur.execute("SELECT title, source_type FROM kn_document WHERE id = %s", (document_id,))
        doc = cur.fetchone()
        cur.execute("""
            SELECT id, ord, text FROM kn_chunk WHERE document_id = %s ORDER BY ord
        """, (document_id,))
        chunks = [{"chunk_id": c[0], "ord": c[1], "text": c[2]} for c in cur.fetchall()]

        conn.commit()
        cur.close()
        return {
            "job": {
                "id": job_id, "kind": kind, "document_id": document_id,
                "document_title": doc[0] if doc else None,
                "source_type": doc[1] if doc else None,
                "chunks": chunks,
            }
        }
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, f"Claim failed: {e}")
    finally:
        conn.close()


@router.post("/worker/jobs/{job_id}/result")
def worker_result(job_id: int, payload: dict = Body(...), user: str = Depends(get_current_user)):
    """Worker posts extracted candidates. The backend writes them as
    `*_generated` events (candidates that pass the human review gate).

    Expected payload:
      {
        "model": "qwen2.5:14b",
        "concepts":  [{"name": "...", "aliases": ["..."]}],
        "relations": [{"src": "A", "dst": "B", "rel_type": "requires", "confidence": 0.8}],
        "units":     [{"content": "...", "role": "definition",
                       "concepts": ["A"], "confidence": 0.7, "basis_chunk_ids": [1,2]}]
      }
    """
    model = (payload.get("model") or "unknown").strip()
    concepts = payload.get("concepts") or []
    relations = payload.get("relations") or []
    units = payload.get("units") or []
    actor = f"agent:extractor@{model}"

    conn = _conn()
    try:
        cur = conn.cursor()
        _ensure_schema(cur)

        cur.execute("SELECT id, document_id, status FROM kn_job WHERE id = %s", (job_id,))
        job = cur.fetchone()
        if not job:
            raise HTTPException(404, "Job not found")
        if job[2] == "done":
            cur.close()
            return {"ok": True, "status": "already_done"}

        counts = {"concepts": 0, "relations": 0, "units": 0}
        name_to_id = {}

        # 1) Concepts (get-or-create as candidates).
        for c in concepts:
            name = (c.get("name") or "").strip() if isinstance(c, dict) else str(c).strip()
            if not name:
                continue
            cid = _ensure_concept(cur, name, actor, generated=True,
                                  aliases=(c.get("aliases") if isinstance(c, dict) else None))
            if cid:
                name_to_id[_normalize(name)] = cid
                counts["concepts"] += 1

        def _lookup(name):
            key = _normalize(name or "")
            if key in name_to_id:
                return name_to_id[key]
            # Fall back to an existing concept by slug.
            cur.execute("SELECT id FROM kn_concept WHERE slug = %s", (_slugify(name or ""),))
            r = cur.fetchone()
            if r:
                cid = _resolve_concept(cur, r[0])
                name_to_id[key] = cid
                return cid
            # Create it on the fly so relations/units aren't dropped.
            cid = _ensure_concept(cur, name, actor, generated=True)
            if cid:
                name_to_id[key] = cid
            return cid

        # 2) Relations (candidates).
        for rel in relations:
            if not isinstance(rel, dict):
                continue
            src = _lookup(rel.get("src"))
            dst = _lookup(rel.get("dst"))
            raw_rtype = (rel.get("rel_type") or "").strip()
            if not src or not dst or not raw_rtype or src == dst:
                continue
            rtype = _canonical_rel_type(raw_rtype)
            conf = float(rel.get("confidence", 0.6))
            ev, _ = _append_event(cur, actor, "relation_generated",
                                  payload={"src": src, "dst": dst, "rel_type": rtype,
                                           "rel_type_raw": raw_rtype},
                                  basis=[{"job": job_id}], confidence=conf,
                                  epistemic_status="generated_machine")
            cur.execute("""
                INSERT INTO kn_relation (src_concept, dst_concept, rel_type, confidence, status, create_event)
                VALUES (%s, %s, %s, %s, 'candidate', %s)
                ON CONFLICT (src_concept, dst_concept, rel_type) DO NOTHING
            """, (src, dst, rtype, conf, ev))
            counts["relations"] += 1

        # 3) Units (machine-authored, anchored to concepts).
        for u in units:
            if not isinstance(u, dict):
                continue
            content = (u.get("content") or "").strip()
            if not content:
                continue
            anchor_ids = []
            for nm in (u.get("concepts") or []):
                cid = _lookup(nm)
                if cid:
                    anchor_ids.append(cid)
            anchor_ids = sorted(set(anchor_ids))
            if not anchor_ids:
                continue
            role = (u.get("role") or "claim").strip()
            conf = float(u.get("confidence", 0.6))
            basis = [{"chunk_id": b} for b in (u.get("basis_chunk_ids") or [])]
            ev, _ = _append_event(cur, actor, "unit_generated",
                                  payload={"content": content, "role": role, "concept_ids": anchor_ids},
                                  basis=basis, confidence=conf,
                                  epistemic_status="generated_machine")
            cur.execute("""
                INSERT INTO kn_knowledge_unit
                    (content, role, epistemic_status, confidence, create_event)
                VALUES (%s, %s, 'generated_machine', %s, %s) RETURNING id
            """, (content, role, conf, ev))
            unit_id = cur.fetchone()[0]
            for cid in anchor_ids:
                cur.execute("""
                    INSERT INTO kn_unit_concept (unit_id, concept_id)
                    VALUES (%s, %s) ON CONFLICT DO NOTHING
                """, (unit_id, cid))
            counts["units"] += 1

        cur.execute("""
            UPDATE kn_job SET status = 'done', finished_at = NOW(), result_meta = %s::jsonb
            WHERE id = %s
        """, (json.dumps({"model": model, **counts}), job_id))

        conn.commit()
        cur.close()
        return {"ok": True, "counts": counts}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, f"Result ingestion failed: {e}")
    finally:
        conn.close()


@router.post("/worker/jobs/{job_id}/fail")
def worker_fail(job_id: int, payload: dict = Body(default={}), user: str = Depends(get_current_user)):
    """Worker reports a failure. Job is requeued (up to a few attempts) or parked."""
    error = (payload.get("error") or "unknown")[:1000]
    conn = _conn()
    try:
        cur = conn.cursor()
        _ensure_schema(cur)
        cur.execute("SELECT attempts FROM kn_job WHERE id = %s", (job_id,))
        r = cur.fetchone()
        if not r:
            raise HTTPException(404, "Job not found")
        new_status = "failed" if r[0] >= 3 else "pending"
        cur.execute("""
            UPDATE kn_job SET status = %s, worker_id = NULL, error = %s,
                   finished_at = CASE WHEN %s = 'failed' THEN NOW() ELSE NULL END
            WHERE id = %s
        """, (new_status, error, new_status, job_id))
        conn.commit()
        cur.close()
        return {"ok": True, "status": new_status}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, f"Fail report failed: {e}")
    finally:
        conn.close()


# ===========================================================================
# EMBEDDINGS (Phase 3) — vectors computed by the Mac worker (Ollama), stored
# in pgvector; used to suggest merges of near-duplicate concepts.
# ===========================================================================

# What we embed and where the text comes from. Order = claim priority.
EMBED_SOURCES = {
    "concept": ("kn_concept",         "name",    "merged_into IS NULL"),
    "unit":    ("kn_knowledge_unit",  "content", "status = 'active'"),
    "chunk":   ("kn_chunk",           "text",    "TRUE"),
}


@router.post("/worker/embed/claim")
def worker_embed_claim(payload: dict = Body(...), user: str = Depends(get_current_user)):
    """Return a batch of items (concepts, then units, then chunks) that still
    need an embedding for `model`.

    Idempotent (no lease): posting results is an upsert, so re-handing the same
    item is harmless. The worker embeds the returned texts and posts them back
    with their `kind`, so this endpoint is fully kind-agnostic downstream.
    """
    model = (payload.get("model") or EMBED_MODEL_DEFAULT).strip()
    limit = max(1, min(int(payload.get("limit") or 32), 128))
    # Optional: restrict to specific kinds (defaults to all known sources).
    kinds = payload.get("kinds") or list(EMBED_SOURCES.keys())
    kinds = [k for k in kinds if k in EMBED_SOURCES]
    conn = _conn()
    try:
        cur = conn.cursor()
        _ensure_schema(cur)
        _ensure_embedding_schema(cur)
        items = []
        for kind in kinds:
            if len(items) >= limit:
                break
            table, col, where = EMBED_SOURCES[kind]
            cur.execute(f"""
                SELECT t.id, t.{col}
                FROM {table} t
                WHERE {where}
                  AND t.{col} IS NOT NULL AND t.{col} <> ''
                  AND NOT EXISTS (
                      SELECT 1 FROM kn_embedding e
                      WHERE e.kind = %s AND e.ref_id = t.id AND e.model = %s
                  )
                ORDER BY t.id
                LIMIT %s
            """, (kind, model, limit - len(items)))
            items += [{"id": r[0], "kind": kind, "text": r[1]} for r in cur.fetchall()]
        conn.commit()
        cur.close()
        return {"items": items, "model": model, "dim": EMBED_DIM}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(503, f"embed claim failed (pgvector unavailable?): {e}")
    finally:
        conn.close()


@router.post("/worker/embed/result")
def worker_embed_result(payload: dict = Body(...), user: str = Depends(get_current_user)):
    """Upsert a batch of embeddings posted by the worker."""
    model = (payload.get("model") or EMBED_MODEL_DEFAULT).strip()
    items = payload.get("items") or []
    conn = _conn()
    try:
        cur = conn.cursor()
        _ensure_schema(cur)
        _ensure_embedding_schema(cur)
        count = 0
        for it in items:
            if not isinstance(it, dict):
                continue
            kind = (it.get("kind") or "concept").strip()
            ref_id = it.get("ref_id")
            vec = it.get("vec")
            if ref_id is None or not isinstance(vec, list) or len(vec) != EMBED_DIM:
                continue
            vec_str = "[" + ",".join(repr(float(x)) for x in vec) + "]"
            cur.execute("""
                INSERT INTO kn_embedding (kind, ref_id, model, dim, vec)
                VALUES (%s, %s, %s, %s, %s::vector)
                ON CONFLICT (kind, ref_id, model)
                DO UPDATE SET vec = EXCLUDED.vec, dim = EXCLUDED.dim, created_at = NOW()
            """, (kind, int(ref_id), model, len(vec), vec_str))
            count += 1
        conn.commit()
        cur.close()
        return {"ok": True, "count": count}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, f"embed result failed: {e}")
    finally:
        conn.close()


@router.get("/review/merge-suggestions")
def merge_suggestions(threshold: float = Query(0.9, ge=0.5, le=1.0),
                      model: str = Query(EMBED_MODEL_DEFAULT),
                      limit: int = Query(50, ge=1, le=200),
                      user: str = Depends(get_current_user)):
    """Concept pairs whose embeddings are near-duplicates (cosine >= threshold),
    ready for a human to confirm a merge. Cross-language duplicates surface here
    too, since the embedding space is multilingual."""
    conn = _conn()
    try:
        cur = conn.cursor()
        _ensure_schema(cur)
        _ensure_embedding_schema(cur)
        cur.execute("""
            SELECT a.ref_id, ca.name, ca.status,
                   b.ref_id, cb.name, cb.status,
                   1 - (a.vec <=> b.vec) AS sim
            FROM kn_embedding a
            JOIN kn_embedding b
              ON b.kind = 'concept' AND b.model = a.model AND b.ref_id > a.ref_id
            JOIN kn_concept ca ON ca.id = a.ref_id AND ca.merged_into IS NULL
            JOIN kn_concept cb ON cb.id = b.ref_id AND cb.merged_into IS NULL
            WHERE a.kind = 'concept' AND a.model = %s
              AND 1 - (a.vec <=> b.vec) >= %s
            ORDER BY sim DESC
            LIMIT %s
        """, (model, threshold, limit))
        pairs = [{"a_id": r[0], "a": r[1], "a_status": r[2],
                  "b_id": r[3], "b": r[4], "b_status": r[5],
                  "similarity": round(float(r[6]), 4)} for r in cur.fetchall()]
        cur.close()
        return {"model": model, "threshold": threshold,
                "suggestions": pairs, "count": len(pairs)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(503, f"merge suggestions failed (pgvector unavailable?): {e}")
    finally:
        conn.close()


@router.post("/search")
def kn_search(payload: dict = Body(...), user: str = Depends(get_current_user)):
    """Semantic (vector) search over embedded items.

    The query vector can be supplied two ways:
      1. `vec`: a raw query embedding (len == EMBED_DIM). A RAG client / the Mac
         worker embeds the user's text with the same model and posts it here.
      2. `kind` + `ref_id`: "more like this" — reuse the stored embedding of an
         existing item as the query. Works entirely server-side (no Ollama), so
         it is testable straight from Swagger.

    Searches over `target_kind` (concept | unit | chunk) and returns the nearest
    neighbours by cosine similarity. The query item is excluded from results.
    """
    model = (payload.get("model") or EMBED_MODEL_DEFAULT).strip()
    target_kind = (payload.get("target_kind") or "unit").strip()
    limit = max(1, min(int(payload.get("limit") or 10), 100))
    if target_kind not in EMBED_SOURCES:
        raise HTTPException(400, f"target_kind must be one of {list(EMBED_SOURCES)}")

    vec = payload.get("vec")
    src_kind = payload.get("kind")
    src_ref = payload.get("ref_id")

    conn = _conn()
    try:
        cur = conn.cursor()
        _ensure_schema(cur)
        _ensure_embedding_schema(cur)

        # Resolve the query vector into a pgvector literal string.
        if isinstance(vec, list):
            if len(vec) != EMBED_DIM:
                raise HTTPException(400, f"vec must have length {EMBED_DIM}")
            qvec = "[" + ",".join(repr(float(x)) for x in vec) + "]"
        elif src_kind and src_ref is not None:
            cur.execute("""
                SELECT vec::text FROM kn_embedding
                WHERE kind = %s AND ref_id = %s AND model = %s
            """, (str(src_kind).strip(), int(src_ref), model))
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, "no embedding for that kind/ref_id/model")
            qvec = row[0]
        else:
            raise HTTPException(400, "provide either `vec` or (`kind` and `ref_id`)")

        table, col, where = EMBED_SOURCES[target_kind]
        # Exclude the query item itself when it is of the same kind.
        exclude_id = int(src_ref) if (src_kind == target_kind and src_ref is not None) else -1
        cur.execute(f"""
            SELECT e.ref_id, t.{col}, 1 - (e.vec <=> %s::vector) AS sim
            FROM kn_embedding e
            JOIN {table} t ON t.id = e.ref_id AND {where}
            WHERE e.kind = %s AND e.model = %s AND e.ref_id <> %s
            ORDER BY e.vec <=> %s::vector
            LIMIT %s
        """, (qvec, target_kind, model, exclude_id, qvec, limit))
        results = [{"kind": target_kind, "ref_id": r[0], "text": r[1],
                    "similarity": round(float(r[2]), 4)} for r in cur.fetchall()]
        cur.close()
        return {"model": model, "target_kind": target_kind,
                "results": results, "count": len(results)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(503, f"search failed (pgvector unavailable?): {e}")
    finally:
        conn.close()


# ===========================================================================
# CHAT (Phase 4) — RAG over the knowledge base. A question is queued; the Mac
# worker embeds it, runs vector search, and generates a grounded answer with
# the local LLM, then posts it back. The browser polls for the answer.
# ===========================================================================

CHAT_LEASE_MINUTES = 5


@router.post("/chat/ask")
def chat_ask(payload: dict = Body(...), user: str = Depends(get_current_user)):
    """Queue a question for the worker to answer via RAG. Returns a chat_id the
    client polls with GET /kn/chat/{id}."""
    question = (payload.get("question") or "").strip()
    if not question:
        raise HTTPException(400, "question is required")
    top_k = max(1, min(int(payload.get("top_k") or 6), 20))
    conn = _conn()
    try:
        cur = conn.cursor()
        _ensure_schema(cur)
        ev_id, _ = _append_event(cur, f"human:{user}", "chat_asked",
                                 payload={"question": question, "top_k": top_k})
        cur.execute("""
            INSERT INTO kn_chat (question, top_k, status, create_event)
            VALUES (%s, %s, 'pending', %s) RETURNING id
        """, (question, top_k, ev_id))
        chat_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
        return {"chat_id": chat_id, "status": "pending"}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, f"chat ask failed: {e}")
    finally:
        conn.close()


@router.get("/chat/{chat_id}")
def chat_get(chat_id: int, user: str = Depends(get_current_user)):
    """Poll a chat turn. status is pending | in_progress | done | error."""
    conn = _conn()
    try:
        cur = conn.cursor()
        _ensure_schema(cur)
        cur.execute("""
            SELECT id, question, answer, status, top_k, context, model, error,
                   created_at, finished_at
            FROM kn_chat WHERE id = %s
        """, (chat_id,))
        r = cur.fetchone()
        cur.close()
        if not r:
            raise HTTPException(404, "chat not found")
        return {"chat_id": r[0], "question": r[1], "answer": r[2], "status": r[3],
                "top_k": r[4], "context": r[5], "model": r[6], "error": r[7],
                "created_at": r[8].isoformat() if r[8] else None,
                "finished_at": r[9].isoformat() if r[9] else None}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"chat get failed: {e}")
    finally:
        conn.close()


@router.post("/worker/chat/claim")
def worker_chat_claim(payload: dict = Body(default={}), user: str = Depends(get_current_user)):
    """Worker pulls the next pending chat turn (with a lease). A stale in_progress
    turn is requeued after CHAT_LEASE_MINUTES."""
    worker_id = (payload.get("worker_id") or "worker").strip()
    conn = _conn()
    try:
        cur = conn.cursor()
        _ensure_schema(cur)
        cur.execute("""
            WITH nxt AS (
                SELECT id FROM kn_chat
                WHERE status = 'pending'
                   OR (status = 'in_progress'
                       AND claimed_at < NOW() - INTERVAL '%s minutes')
                ORDER BY id
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            UPDATE kn_chat c
            SET status = 'in_progress', worker_id = %%s,
                attempts = c.attempts + 1, claimed_at = NOW()
            FROM nxt WHERE c.id = nxt.id
            RETURNING c.id, c.question, c.top_k
        """ % CHAT_LEASE_MINUTES, (worker_id,))
        row = cur.fetchone()
        conn.commit()
        cur.close()
        if not row:
            return {"chat": None}
        return {"chat": {"id": row[0], "question": row[1], "top_k": row[2]}}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, f"chat claim failed: {e}")
    finally:
        conn.close()


@router.post("/worker/chat/result")
def worker_chat_result(payload: dict = Body(...), user: str = Depends(get_current_user)):
    """Worker posts the generated answer + the retrieved context."""
    chat_id = payload.get("chat_id")
    answer = (payload.get("answer") or "").strip()
    context = payload.get("context") or []
    model = (payload.get("model") or "").strip() or None
    if chat_id is None:
        raise HTTPException(400, "chat_id is required")
    conn = _conn()
    try:
        cur = conn.cursor()
        _ensure_schema(cur)
        _append_event(cur, "machine:worker", "chat_answered",
                      payload={"chat_id": chat_id, "answer": answer,
                               "context": context, "model": model},
                      epistemic_status="generated_machine")
        cur.execute("""
            UPDATE kn_chat
            SET answer = %s, context = %s::jsonb, model = %s,
                status = 'done', error = NULL, finished_at = NOW()
            WHERE id = %s
        """, (answer, json.dumps(context), model, int(chat_id)))
        conn.commit()
        cur.close()
        return {"ok": True, "chat_id": chat_id}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, f"chat result failed: {e}")
    finally:
        conn.close()


@router.post("/worker/chat/fail")
def worker_chat_fail(payload: dict = Body(...), user: str = Depends(get_current_user)):
    """Worker reports a failure. The turn is marked 'error' so the client stops
    polling; the error text is stored for debugging."""
    chat_id = payload.get("chat_id")
    error = (payload.get("error") or "unknown error")[:1000]
    if chat_id is None:
        raise HTTPException(400, "chat_id is required")
    conn = _conn()
    try:
        cur = conn.cursor()
        _ensure_schema(cur)
        cur.execute("""
            UPDATE kn_chat
            SET status = 'error', error = %s, finished_at = NOW()
            WHERE id = %s
        """, (error, int(chat_id)))
        conn.commit()
        cur.close()
        return {"ok": True, "chat_id": chat_id}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, f"chat fail failed: {e}")
    finally:
        conn.close()

