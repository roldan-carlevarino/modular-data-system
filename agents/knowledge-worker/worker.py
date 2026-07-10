#!/usr/bin/env python3
"""Knowledge Engine extraction worker.

Runs on a private machine (e.g. a Mac Mini) with Ollama installed. It ONLY
makes outbound calls: it logs in, polls the backend for extraction jobs, runs
a local LLM to extract concepts / relations / units from a document's chunks,
and posts the candidates back. Nothing needs to be exposed to the internet.

Config via environment variables:
  API_BASE       Backend base URL            (default http://localhost:8000)
  KN_USERNAME    Admin username for login    (required)
  KN_PASSWORD    Admin password for login    (required)
  OLLAMA_URL     Ollama base URL             (default http://localhost:11434)
  OLLAMA_MODEL   Model tag                   (default qwen3.5:4b)
  OLLAMA_NUM_CTX Context window tokens       (default 4096)
  EMBED_MODEL    Embedding model tag         (default bge-m3)
  EMBED_DIM      Embedding dimension         (default 1024)
  EMBED_BATCH    Concepts embedded per batch (default 16)
  WORKER_ID      Worker identifier           (default hostname)
  POLL_INTERVAL  Seconds between empty polls (default 5)
  MAX_CHUNKS     Max chunks per prompt       (default 8)

Tuned for an Apple Silicon (M1) Mac Mini with 8 GB unified memory: qwen3.5:4b
at Q4 (~3.4 GB) fits in RAM without swapping, leaving headroom for the OS.
"""

import json
import os
import socket
import sys
import time

import requests

API_BASE = os.environ.get("API_BASE", "http://localhost:8000").rstrip("/")
KN_USERNAME = os.environ.get("KN_USERNAME", "")
KN_PASSWORD = os.environ.get("KN_PASSWORD", "")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434").rstrip("/")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen3.5:4b")
OLLAMA_NUM_CTX = int(os.environ.get("OLLAMA_NUM_CTX", "4096"))
EMBED_MODEL = os.environ.get("EMBED_MODEL", "bge-m3")
EMBED_DIM = int(os.environ.get("EMBED_DIM", "1024"))
EMBED_BATCH = int(os.environ.get("EMBED_BATCH", "16"))
WORKER_ID = os.environ.get("WORKER_ID", socket.gethostname())
POLL_INTERVAL = float(os.environ.get("POLL_INTERVAL", "5"))
MAX_CHUNKS = int(os.environ.get("MAX_CHUNKS", "8"))

SYSTEM_PROMPT = (
    "You extract a knowledge graph from study material. "
    "Return STRICT JSON only, no prose, matching exactly this shape:\n"
    "{\n"
    '  "concepts":  [{"name": string, "aliases": [string]}],\n'
    '  "relations": [{"src": string, "dst": string, "rel_type": string, "confidence": number}],\n'
    '  "units":     [{"content": string, "role": string, "factuality": string, '
    '"concepts": [string], '
    '"confidence": number, "basis_chunk_ids": [number]}]\n'
    "}\n"
    "Rules:\n"
    "- Concepts are canonical noun phrases (topics, terms, entities).\n"
    "- rel_type MUST be EXACTLY one of this closed English list (never invent, "
    "never translate, never use another language): is_a, part_of, has_part, "
    "requires, causes, produces, enables, defined_by, example_of, contradicts, "
    "related_to. If none fits well, use related_to.\n"
    "- 'src' and 'dst' MUST be names that appear in 'concepts'.\n"
    "- A unit is one atomic statement (definition, claim, fact, procedure step). "
    "role is one of: definition, claim, fact, procedure, example.\n"
    "- factuality classifies the statement on an objectivity axis (independent of "
    "role): use \"fact\" for objective, verifiable statements (definitions, data, "
    "established science, procedures); use \"opinion\" for subjective, evaluative, "
    "normative or speculative statements (judgements, recommendations, predictions, "
    "'should'/'best'/'better' claims). If genuinely unclear, use \"unknown\".\n"
    "- UNITS ARE THE MOST IMPORTANT OUTPUT. Turn EVERY sentence that states a "
    "definition, fact, claim, or step into its own unit. Do NOT leave 'units' "
    "empty when the text contains statements. Aim for at least one unit per "
    "meaningful sentence.\n"
    "- 'concepts' in a unit must reference names from 'concepts'.\n"
    "- basis_chunk_ids are the chunk_id values the unit is grounded in.\n"
    "- confidence is 0..1. Be conservative; omit anything you are unsure about.\n"
    "- Output ONLY the JSON object."
)


class WorkerError(Exception):
    pass


def login():
    """Authenticate and return a bearer token."""
    if not KN_USERNAME or not KN_PASSWORD:
        raise WorkerError("KN_USERNAME and KN_PASSWORD must be set")
    r = requests.post(
        f"{API_BASE}/auth/login",
        data={"username": KN_USERNAME, "password": KN_PASSWORD},
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    token = data.get("access_token") or data.get("token")
    if not token:
        raise WorkerError(f"Login response missing token: {data}")
    return token


def claim(session):
    r = session.post(
        f"{API_BASE}/kn/worker/claim",
        json={"worker_id": WORKER_ID},
        timeout=30,
    )
    r.raise_for_status()
    return r.json().get("job")


def build_prompt(job):
    chunks = job.get("chunks") or []
    if MAX_CHUNKS > 0:
        chunks = chunks[:MAX_CHUNKS]
    header = f"Document: {job.get('document_title') or '(untitled)'} " \
             f"[type={job.get('source_type')}]\n\n"
    body = "\n\n".join(
        f"[chunk_id={c['chunk_id']}]\n{c['text']}" for c in chunks
    )
    return header + "CHUNKS:\n" + body


def run_ollama(prompt):
    """Call the local Ollama chat endpoint with JSON-formatted output."""
    return _run_ollama_json(SYSTEM_PROMPT, prompt)


def _run_ollama_json(system, prompt):
    """Ollama chat with strict JSON output and a custom system prompt."""
    payload = {
        "model": OLLAMA_MODEL,
        "format": "json",
        "stream": False,
        "think": False,  # qwen3.5 is a thinking model; disable so content isn't empty
        "keep_alive": "30m",
        "options": {"temperature": 0.1, "num_ctx": OLLAMA_NUM_CTX},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
    }
    r = requests.post(f"{OLLAMA_URL}/api/chat", json=payload, timeout=600)
    r.raise_for_status()
    msg = r.json().get("message", {})
    content = msg.get("content", "")
    if not content:
        raise WorkerError("Empty response from Ollama")
    return json.loads(content)


def post_result(session, job_id, extraction):
    body = {
        "model": OLLAMA_MODEL,
        "concepts": extraction.get("concepts") or [],
        "relations": extraction.get("relations") or [],
        "units": extraction.get("units") or [],
    }
    r = session.post(
        f"{API_BASE}/kn/worker/jobs/{job_id}/result",
        json=body,
        timeout=60,
    )
    r.raise_for_status()
    return r.json()


def report_fail(session, job_id, error):
    try:
        session.post(
            f"{API_BASE}/kn/worker/jobs/{job_id}/fail",
            json={"error": str(error)[:1000]},
            timeout=30,
        )
    except Exception as e:  # noqa: BLE001
        print(f"[warn] could not report failure for job {job_id}: {e}")


def make_session(token):
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {token}"})
    return s


def process_one(session):
    """Claim and process a single job. Returns True if a job was handled."""
    job = claim(session)
    if not job:
        return False
    job_id = job["id"]
    print(f"[job {job_id}] claimed (doc={job.get('document_id')}, "
          f"chunks={len(job.get('chunks') or [])})")
    try:
        prompt = build_prompt(job)
        extraction = run_ollama(prompt)
        result = post_result(session, job_id, extraction)
        print(f"[job {job_id}] done: {result.get('counts')}")
    except Exception as e:  # noqa: BLE001
        print(f"[job {job_id}] failed: {e}")
        report_fail(session, job_id, e)
    return True


def run_embeddings(texts):
    """Compute embeddings for a batch of texts via Ollama's /api/embed."""
    r = requests.post(
        f"{OLLAMA_URL}/api/embed",
        json={"model": EMBED_MODEL, "input": texts, "keep_alive": "30m"},
        timeout=600,
    )
    r.raise_for_status()
    embs = r.json().get("embeddings")
    if not embs or len(embs) != len(texts):
        raise WorkerError(f"Embedding count mismatch: got {len(embs or [])} for {len(texts)} texts")
    return embs


def claim_embed(session):
    r = session.post(
        f"{API_BASE}/kn/worker/embed/claim",
        json={"worker_id": WORKER_ID, "model": EMBED_MODEL, "limit": EMBED_BATCH},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def process_embeddings(session):
    """Embed one batch of concepts that still lack a vector. Returns True if
    any work was done."""
    batch = claim_embed(session)
    items = batch.get("items") or []
    if not items:
        return False
    texts = [it["text"] for it in items]
    vecs = run_embeddings(texts)
    payload = {
        "model": EMBED_MODEL,
        "items": [{"kind": it.get("kind", "concept"), "ref_id": it["id"], "vec": v}
                  for it, v in zip(items, vecs)],
    }
    r = session.post(f"{API_BASE}/kn/worker/embed/result", json=payload, timeout=120)
    r.raise_for_status()
    print(f"[embed] {r.json().get('count')} vectors ({EMBED_MODEL})")
    return True


CHAT_SYSTEM_PROMPT = (
    "Eres un asistente que responde preguntas usando EXCLUSIVAMENTE el CONTEXTO "
    "proporcionado (fragmentos recuperados de una base de conocimiento). "
    "Cada unidad viene etiquetada como (hecho) o (opinion). "
    "Reglas: (1) No inventes: si el contexto no contiene la respuesta, di que no "
    "hay informacion suficiente. (2) Responde en el mismo idioma que la pregunta. "
    "(3) Cita las unidades que uses con su marcador [U<id>]. (4) Distingue "
    "claramente los hechos objetivos de las opiniones: cuando algo provenga de una "
    "unidad (opinion), preséntalo como una opinión o valoración, no como un hecho. "
    "(5) Se conciso y claro."
)


def run_ollama_text(system, user_msg):
    """Plain (non-JSON) chat completion with the local LLM for answer generation."""
    payload = {
        "model": OLLAMA_MODEL,
        "stream": False,
        "think": False,
        "keep_alive": "30m",
        "options": {"temperature": 0.2, "num_ctx": OLLAMA_NUM_CTX},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_msg},
        ],
    }
    r = requests.post(f"{OLLAMA_URL}/api/chat", json=payload, timeout=600)
    r.raise_for_status()
    content = r.json().get("message", {}).get("content", "")
    if not content:
        raise WorkerError("Empty answer from Ollama")
    return content.strip()


def claim_chat(session):
    r = session.post(
        f"{API_BASE}/kn/worker/chat/claim",
        json={"worker_id": WORKER_ID},
        timeout=30,
    )
    r.raise_for_status()
    return r.json().get("chat")


def _factuality_label(f):
    """Spanish label for a unit's fact/opinion classification, for the prompt."""
    return {"fact": "hecho", "opinion": "opinion"}.get(f, "sin clasificar")


# Intent router: decides whether a question is about the user's own tracked
# metrics (answered with live SQL aggregates) or general knowledge (answered via
# RAG). qwen only has to pick from a closed menu + a period — no free-form tools.
ROUTER_SYSTEM_PROMPT = (
    "Clasificas la intencion de una pregunta de un asistente personal. "
    "Devuelve SOLO JSON con esta forma exacta:\n"
    '{"mode": "personal" | "knowledge", "domain": "gym" | "weight" | "water" | null, '
    '"period_days": number | null}\n'
    "Reglas:\n"
    "- mode='personal' SOLO si la pregunta es sobre los datos propios del usuario "
    "que se registran: entrenamientos/gimnasio (gym), peso corporal (weight), o "
    "consumo de agua (water). Ejemplos: 'como llevo los entrenamientos', 'cuanto "
    "peso', 'he bebido suficiente agua esta semana'.\n"
    "- Si no encaja EXACTAMENTE en gym/weight/water, mode='knowledge' y domain=null "
    "(preguntas de conocimiento, conceptos, documentos, etc.).\n"
    "- period_days: interpreta expresiones temporales. hoy=1, esta semana=7, "
    "este mes=30, ultimamente/reciente=30, este año=365. Si no se especifica, usa 30.\n"
    "- Responde SOLO el objeto JSON."
)

PERSONAL_SYSTEM_PROMPT = (
    "Eres un asistente personal que responde sobre los datos propios del usuario. "
    "Usa EXCLUSIVAMENTE los DATOS proporcionados (ya son cifras reales agregadas de "
    "su base de datos). No inventes numeros ni tendencias que no aparezcan. "
    "Responde en el mismo idioma que la pregunta, de forma breve, concreta y "
    "cercana, resaltando lo mas relevante. Si los datos indican que no hay "
    "registros, dilo con naturalidad."
)

# gym/weight/water are the only intents the summary endpoint understands.
PERSONAL_DOMAINS = {"gym", "weight", "water"}


def classify_intent(question):
    """Return {mode, domain, period_days}. Falls back to knowledge on any doubt."""
    try:
        out = _run_ollama_json(ROUTER_SYSTEM_PROMPT, f"PREGUNTA: {question}")
    except Exception as e:  # noqa: BLE001
        print(f"[router] classify failed, defaulting to knowledge: {e}")
        return {"mode": "knowledge", "domain": None, "period_days": 30}
    mode = out.get("mode")
    domain = out.get("domain")
    period = out.get("period_days")
    if mode != "personal" or domain not in PERSONAL_DOMAINS:
        return {"mode": "knowledge", "domain": None, "period_days": 30}
    try:
        period = int(period)
    except (TypeError, ValueError):
        period = 30
    period = max(1, min(period, 365))
    return {"mode": "personal", "domain": domain, "period_days": period}


def fetch_personal_summary(session, domain, period_days):
    """GET the real-time aggregate summary for a personal-data domain."""
    r = session.get(
        f"{API_BASE}/insights/summary",
        params={"domain": domain, "period_days": period_days},
        timeout=60,
    )
    r.raise_for_status()
    return r.json()


def process_chat(session):
    """Answer one queued chat turn. Personal-metric questions are answered from
    live SQL aggregates; everything else via RAG. Returns True if a turn was
    handled."""
    chat = claim_chat(session)
    if not chat:
        return False
    chat_id = chat["id"]
    question = chat["question"]
    top_k = chat.get("top_k") or 6
    print(f"[chat {chat_id}] {question!r}")
    try:
        intent = classify_intent(question)
        print(f"[chat {chat_id}] intent -> {intent}")
        if intent["mode"] == "personal":
            _answer_personal(session, chat_id, question, intent)
        else:
            _answer_knowledge(session, chat_id, question, top_k)
    except Exception as e:  # noqa: BLE001
        print(f"[chat {chat_id}] failed: {e}")
        try:
            session.post(
                f"{API_BASE}/kn/worker/chat/fail",
                json={"chat_id": chat_id, "error": str(e)[:1000]},
                timeout=30,
            )
        except Exception as e2:  # noqa: BLE001
            print(f"[warn] could not report chat failure {chat_id}: {e2}")
    return True


def _answer_personal(session, chat_id, question, intent):
    """Answer a question about the user's own metrics from live aggregates."""
    domain = intent["domain"]
    period = intent["period_days"]
    summ = fetch_personal_summary(session, domain, period)
    user_msg = (
        f"DATOS ({domain}, ultimos {period} dias):\n{summ.get('summary', '')}\n\n"
        f"PREGUNTA: {question}"
    )
    answer = run_ollama_text(PERSONAL_SYSTEM_PROMPT, user_msg)
    ctx = [{"kind": "personal", "domain": domain, "period_days": period,
            "summary": summ.get("summary", ""), "data": summ.get("data")}]
    rr = session.post(
        f"{API_BASE}/kn/worker/chat/result",
        json={"chat_id": chat_id, "answer": answer,
              "context": ctx, "model": OLLAMA_MODEL},
        timeout=60,
    )
    rr.raise_for_status()
    print(f"[chat {chat_id}] answered (personal:{domain}/{period}d)")


def _answer_knowledge(session, chat_id, question, top_k):
    """Answer a general-knowledge question via RAG over the knowledge base."""
    qvec = run_embeddings([question])[0]
    sr = session.post(
        f"{API_BASE}/kn/search",
        json={"model": EMBED_MODEL, "vec": qvec,
              "target_kind": "unit", "limit": top_k},
        timeout=60,
    )
    sr.raise_for_status()
    results = sr.json().get("results") or []
    if results:
        context_txt = "\n".join(
            f"[U{u['ref_id']}] ({_factuality_label(u.get('factuality'))}) {u['text']}"
            for u in results
        )
    else:
        context_txt = "(no hay fragmentos relevantes)"
    user_msg = f"CONTEXTO:\n{context_txt}\n\nPREGUNTA: {question}"
    answer = run_ollama_text(CHAT_SYSTEM_PROMPT, user_msg)
    rr = session.post(
        f"{API_BASE}/kn/worker/chat/result",
        json={"chat_id": chat_id, "answer": answer,
              "context": results, "model": OLLAMA_MODEL},
        timeout=60,
    )
    rr.raise_for_status()
    print(f"[chat {chat_id}] answered ({len(results)} units)")


def main():
    print(f"knowledge-worker starting: api={API_BASE} model={OLLAMA_MODEL} "
          f"embed={EMBED_MODEL} worker_id={WORKER_ID}")
    token = login()
    session = make_session(token)
    while True:
        try:
            # Priority: chat turns (a human is waiting) > extraction > embed backfill.
            handled = process_chat(session)
            if not handled:
                handled = process_one(session)
            if not handled:
                # Idle: use the time to backfill embeddings.
                handled = process_embeddings(session)
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 401:
                print("[auth] token expired, re-logging in")
                token = login()
                session = make_session(token)
                continue
            print(f"[error] HTTP: {e}")
            handled = False
        except Exception as e:  # noqa: BLE001
            print(f"[error] {e}")
            handled = False
        if not handled:
            time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nstopped")
        sys.exit(0)
