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
  CAREERS_AUTO   1 to autonomously drive the careers pipeline (default 1)
  CAREERS_INTERVAL_SEC  Seconds between careers cycles (default 21600 = 6h)
  VOICE_ENABLED  1 to run the wake-word voice mode on this machine (default 0);
                 see voice_mode.py for its own config + requirements-voice.txt.

Tuned for an Apple Silicon (M1) Mac Mini with 8 GB unified memory: qwen3.5:4b
at Q4 (~3.4 GB) fits in RAM without swapping, leaving headroom for the OS.
"""

import json
import os
import socket
import sys
import threading
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

# Careers autonomy: when on, the worker drives the whole opportunity pipeline on
# a timer (enqueue discovery -> fetch openings -> queue scoring) with no cron or
# button. Interval in seconds (default 6h).
CAREERS_AUTO = os.environ.get("CAREERS_AUTO", "1") == "1"
CAREERS_INTERVAL_SEC = float(os.environ.get("CAREERS_INTERVAL_SEC", "21600"))

# Voice mode (optional): an always-on wake-word listener on this machine's mic.
# When it hears the wake word it pauses background jobs (so Ollama is free) and
# answers the spoken request first, in-process, via the shared answer engine.
VOICE_ENABLED = os.environ.get("VOICE_ENABLED", "0") == "1"

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


class JobInterrupted(Exception):
    """Raised when an in-flight background LLM job is preempted (e.g. by a voice
    request) so the worker can release it back to the queue immediately."""
    pass


# Set by the voice thread the moment the wake word fires, to abort any
# in-flight background extraction so Ollama is freed for the spoken request.
# Only extraction (run_ollama) honours it; voice's own LLM calls do not.
INTERRUPT = threading.Event()


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


def run_ollama(prompt, interrupt=None):
    """Call the local Ollama chat endpoint with JSON-formatted output."""
    return _run_ollama_json(SYSTEM_PROMPT, prompt, interrupt=interrupt)


def _run_ollama_json(system, prompt, interrupt=None):
    """Ollama chat with strict JSON output and a custom system prompt.

    If `interrupt` (a threading.Event) is given, the response is streamed and
    aborted mid-generation when the event is set, raising JobInterrupted."""
    payload = {
        "model": OLLAMA_MODEL,
        "format": "json",
        "stream": interrupt is not None,
        "think": False,  # qwen3.5 is a thinking model; disable so content isn't empty
        "keep_alive": "30m",
        "options": {"temperature": 0.1, "num_ctx": OLLAMA_NUM_CTX},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
    }
    if interrupt is None:
        r = requests.post(f"{OLLAMA_URL}/api/chat", json=payload, timeout=600)
        r.raise_for_status()
        msg = r.json().get("message", {})
        content = msg.get("content", "")
        if not content:
            raise WorkerError("Empty response from Ollama")
        return json.loads(content)

    # Streaming path: check the interrupt flag between chunks so a voice request
    # can preempt a long extraction. Closing the response aborts the generation.
    parts = []
    with requests.post(f"{OLLAMA_URL}/api/chat", json=payload,
                       stream=True, timeout=600) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            if interrupt.is_set():
                raise JobInterrupted()
            if not line:
                continue
            chunk = json.loads(line)
            parts.append(chunk.get("message", {}).get("content", ""))
            if chunk.get("done"):
                break
    content = "".join(parts)
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


def release_job(session, job_id):
    """Requeue a preempted job (attempt-neutral) so it runs first again."""
    try:
        session.post(
            f"{API_BASE}/kn/worker/jobs/{job_id}/release",
            json={},
            timeout=30,
        )
    except Exception as e:  # noqa: BLE001
        print(f"[warn] could not release job {job_id}: {e}")


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
        extraction = run_ollama(prompt, interrupt=INTERRUPT)
        result = post_result(session, job_id, extraction)
        print(f"[job {job_id}] done: {result.get('counts')}")
    except JobInterrupted:
        print(f"[job {job_id}] preempted by voice -> released back to queue")
        release_job(session, job_id)
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
    "(5) Se conciso y claro. (6) Si hay una CONVERSACION PREVIA, usala solo para "
    "entender referencias de la pregunta actual (p. ej. 'desarrolla mas', 'y eso'); "
    "la respuesta debe seguir basandose en el CONTEXTO."
)


# Rewrites a terse follow-up into a self-contained search query using the prior
# turns, so the vector search embeds a query that actually carries the topic.
REWRITE_SYSTEM_PROMPT = (
    "Reescribe la ULTIMA pregunta del usuario como una consulta de busqueda "
    "autonoma y completa, resolviendo pronombres y referencias con la "
    "CONVERSACION PREVIA (p. ej. 'y sus desventajas' -> 'desventajas del event "
    "sourcing'). Reglas: (1) Devuelve SOLO la consulta reescrita, sin explicaciones "
    "ni comillas. (2) Mismo idioma que la pregunta. (3) Si la pregunta ya es "
    "autonoma, devuelvela tal cual. (4) No inventes temas que no aparezcan en la "
    "conversacion. (5) Se breve (una sola frase o sintagma)."
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
    '{"mode": "personal" | "knowledge", "domain": "gym" | "weight" | "water" | "schedule" | "focus" | "math" | "mental" | "menu" | "careers" | "rss" | null, '
    '"period_days": number | null}\n'
    "Reglas:\n"
    "- mode='personal' SOLO si la pregunta es sobre los datos propios del usuario "
    "que se registran: entrenamientos/gimnasio (gym), peso corporal (weight), "
    "consumo de agua (water), su agenda: tareas/to-do y eventos del calendario "
    "(schedule), sesiones de pomodoro/concentracion/estudio (focus), entrenamiento "
    "de calculo mental/matematicas (math), bienestar: sueno y estres (mental), "
    "que le toca comer hoy/menu del dia (menu), sus solicitudes de trabajo/becas/"
    "internships y deadlines (careers), o noticias/articulos/papers por leer (rss). "
    "Ejemplos: 'como llevo los entrenamientos', 'cuanto peso', "
    "'he bebido suficiente agua esta semana', 'que tareas tengo pendientes', "
    "'que tengo en el calendario esta semana', 'como llevo las tareas', "
    "'cuanto he estudiado con pomodoro', 'como voy en las mates', "
    "'cuanto he dormido esta semana', 'como esta mi estres', "
    "'que me toca comer hoy', 'como van mis solicitudes de trabajo', "
    "'que deadlines tengo', 'que noticias tengo por leer'.\n"
    "- Si no encaja EXACTAMENTE en gym/weight/water/schedule/focus/math/mental/menu/careers/rss, "
    "mode='knowledge' y domain=null (preguntas de conocimiento, conceptos, "
    "documentos, etc.).\n"
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
    "registros, dilo con naturalidad. Si hay una CONVERSACION PREVIA, usala solo "
    "para entender referencias de la pregunta actual, no para inventar datos."
)

# gym/weight/water/schedule/focus/math/mental/menu/careers/rss are the only intents the summary endpoint understands.
PERSONAL_DOMAINS = {"gym", "weight", "water", "schedule", "focus", "math", "mental",
                    "menu", "careers", "rss"}


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
    history = chat.get("history") or []
    print(f"[chat {chat_id}] {question!r}")
    try:
        answer, ctx, intent = answer_question(session, question, history, top_k)
        print(f"[chat {chat_id}] intent -> {intent}")
        rr = session.post(
            f"{API_BASE}/kn/worker/chat/result",
            json={"chat_id": chat_id, "answer": answer,
                  "context": ctx, "model": OLLAMA_MODEL},
            timeout=60,
        )
        rr.raise_for_status()
        print(f"[chat {chat_id}] answered ({intent['mode']})")
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


def answer_question(session, question, history=None, top_k=6):
    """Shared answer engine used by BOTH the chat queue (web dashboard) and the
    local voice mode. Classifies intent, then answers from personal-data SQL
    aggregates or via RAG. Returns (answer_text, context_list, intent_dict)."""
    intent = classify_intent(question)
    if intent["mode"] == "personal":
        answer, ctx = _answer_personal(session, question, intent, history)
    else:
        answer, ctx = _answer_knowledge(session, question, top_k, history)
    return answer, ctx, intent


def _format_history(history):
    """Render prior turns as a short preamble so the LLM can resolve references
    like 'desarrolla mas' or 'y la semana pasada'. Oldest first."""
    if not history:
        return ""
    lines = []
    for turn in history:
        q = (turn.get("question") or "").strip()
        a = (turn.get("answer") or "").strip()
        if q:
            lines.append(f"Usuario: {q}")
        if a:
            lines.append(f"Asistente: {a}")
    if not lines:
        return ""
    return "CONVERSACION PREVIA (mas antigua primero):\n" + "\n".join(lines) + "\n\n"


def _answer_personal(session, question, intent, history=None):
    """Answer a question about the user's own metrics from live aggregates.
    Returns (answer_text, context_list)."""
    domain = intent["domain"]
    period = intent["period_days"]
    summ = fetch_personal_summary(session, domain, period)
    user_msg = (
        f"{_format_history(history)}"
        f"DATOS ({domain}, ultimos {period} dias):\n{summ.get('summary', '')}\n\n"
        f"PREGUNTA: {question}"
    )
    answer = run_ollama_text(PERSONAL_SYSTEM_PROMPT, user_msg)
    ctx = [{"kind": "personal", "domain": domain, "period_days": period,
            "summary": summ.get("summary", ""), "data": summ.get("data")}]
    return answer, ctx


def _standalone_query(question, history):
    """Turn a terse follow-up into a self-contained search query using prior
    turns, so the embedding carries the topic. Falls back to the raw question
    when there is no history or the LLM misbehaves (empty / suspiciously long)."""
    if not history:
        return question
    try:
        user_msg = f"{_format_history(history)}ULTIMA PREGUNTA: {question}"
        rewritten = run_ollama_text(REWRITE_SYSTEM_PROMPT, user_msg).strip()
    except Exception as e:  # noqa: BLE001
        print(f"[warn] query rewrite failed, using raw question: {e}")
        return question
    rewritten = rewritten.strip('"').strip()
    if not rewritten or len(rewritten) > 300:
        return question
    if rewritten != question:
        print(f"[rewrite] {question!r} -> {rewritten!r}")
    return rewritten


def _answer_knowledge(session, question, top_k, history=None):
    """Answer a general-knowledge question via RAG over the knowledge base.
    Returns (answer_text, context_list)."""
    search_query = _standalone_query(question, history)
    qvec = run_embeddings([search_query])[0]
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
    user_msg = (
        f"{_format_history(history)}"
        f"CONTEXTO:\n{context_txt}\n\nPREGUNTA: {question}"
    )
    answer = run_ollama_text(CHAT_SYSTEM_PROMPT, user_msg)
    return answer, results


# ---------------------------------------------------------------------------
# Careers opportunity agent: score fetched market openings against the user's
# profile (CV + projects + library). Same claim/result queue shape as chat.
# ---------------------------------------------------------------------------

SCORE_SYSTEM_PROMPT = (
    "Eres un asesor de carrera que evalua cuanto encaja una oportunidad laboral "
    "(internship, new-grad, investigacion, phd, summer school o beca) con el "
    "PERFIL de un candidato. Usa SOLO la informacion dada. Devuelve SOLO JSON "
    "con esta forma exacta:\n"
    "{\n"
    '  "fit_score": number,            // 0-100, cuanto encaja con el perfil\n'
    '  "fit_reason": string,           // 2-3 frases en espanol: por que encaja\n'
    '  "suggested_type": string,       // uno de: internship, new_grad, research, phd, summer_school, grant\n'
    '  "suggested_tags": [string],     // 3-6 areas/skills clave de la oferta\n'
    '  "gaps": string                  // que le falta al candidato o como mejorar el encaje\n'
    "}\n"
    "Reglas:\n"
    "- fit_score alto solo si skills, intereses, proyectos y nivel del candidato "
    "coinciden con lo que pide la oferta. Sé exigente y realista.\n"
    "- suggested_type DEBE ser exactamente uno de la lista.\n"
    "- Responde SOLO el objeto JSON, sin texto adicional."
)


def _build_score_prompt(profile_context, opp):
    remote = "sí" if opp.get("remote") else "no"
    return (
        f"{profile_context}\n\n"
        "OPORTUNIDAD:\n"
        f"Titulo: {opp.get('title')}\n"
        f"Empresa: {opp.get('company')}\n"
        f"Ubicacion: {opp.get('location')} (remoto: {remote})\n"
        f"Fuente: {opp.get('source_kind')}\n"
        f"Descripcion:\n{opp.get('description') or '(sin descripcion)'}\n\n"
        "Evalua el encaje de esta oportunidad con el perfil y responde en JSON."
    )


def process_score(session):
    """Claim and score one queued opportunity. Returns True if one was handled."""
    r = session.post(
        f"{API_BASE}/careers/worker/score/claim",
        json={"worker_id": WORKER_ID},
        timeout=30,
    )
    r.raise_for_status()
    job = r.json().get("job")
    if not job:
        return False
    job_id = job["id"]
    opp = job.get("opportunity") or {}
    ctx = job.get("profile_context") or ""
    print(f"[score {job_id}] {opp.get('title')!r} @ {opp.get('company')!r}")
    try:
        out = _run_ollama_json(SCORE_SYSTEM_PROMPT, _build_score_prompt(ctx, opp))
        body = {
            "job_id": job_id,
            "fit_score": out.get("fit_score"),
            "fit_reason": out.get("fit_reason"),
            "suggested_type": out.get("suggested_type"),
            "suggested_tags": out.get("suggested_tags"),
            "gaps": out.get("gaps"),
            "model": OLLAMA_MODEL,
        }
        rr = session.post(
            f"{API_BASE}/careers/worker/score/result", json=body, timeout=60,
        )
        rr.raise_for_status()
        print(f"[score {job_id}] fit={out.get('fit_score')}")
    except Exception as e:  # noqa: BLE001
        print(f"[score {job_id}] failed: {e}")
        try:
            session.post(
                f"{API_BASE}/careers/worker/score/fail",
                json={"job_id": job_id, "error": str(e)[:1000]},
                timeout=30,
            )
        except Exception as e2:  # noqa: BLE001
            print(f"[warn] could not report score failure {job_id}: {e2}")
    return True


# ---------------------------------------------------------------------------
# Careers source discovery: propose employers + search phrases that fit the
# profile. The worker only PROPOSES; the backend hard-validates every candidate
# against the live ATS boards before adding anything (so hallucinated slugs and
# dead companies never make it in).
# ---------------------------------------------------------------------------

DISCOVER_SYSTEM_PROMPT = (
    "Eres un headhunter que amplia la lista de fuentes de empleo de un candidato. "
    "A partir de su PERFIL, propones EMPRESAS REALES y frases de busqueda que "
    "encajen con lo que busca (data science, machine learning, quant trading, "
    "quant research, research assistant; nivel internship/new-grad/research). "
    "Devuelve SOLO JSON con esta forma exacta:\n"
    "{\n"
    '  "companies": [string],   // 15-30 nombres de empresas REALES y conocidas\n'
    '  "queries":   [string]    // 5-10 frases de busqueda cortas en ingles\n'
    "}\n"
    "Reglas MUY IMPORTANTES:\n"
    "- Propon SOLO empresas que existan de verdad y que suelan contratar estos "
    "perfiles (tech, fintech, IA, trading cuantitativo, laboratorios, hedge funds). "
    "NO inventes nombres. Un validador comprobara cada empresa en vivo y descartara "
    "las que no existan, asi que la calidad importa mas que la cantidad.\n"
    "- PRIORIZA empresas que contraten en las UBICACIONES preferidas del candidato "
    "(o totalmente remoto). Evita empresas que solo contraten fuera de esas zonas.\n"
    "- Usa el nombre comun de la empresa (ej. 'Jane Street', 'Two Sigma', 'Hudson "
    "River Trading', 'Scale AI'), no dominios ni URLs.\n"
    "- NO repitas las empresas ni las queries que ya estan en la lista de conocidas.\n"
    "- queries: frases cortas tipo 'machine learning intern', 'quant research new grad'.\n"
    "- Responde SOLO el objeto JSON, sin texto adicional."
)


def _build_discover_prompt(profile_context, known_companies, known_queries, locations=None):
    known_c = ", ".join(known_companies[:120]) or "(ninguna)"
    known_q = ", ".join(known_queries[:40]) or "(ninguna)"
    locs = ", ".join(locations or []) or "(cualquiera)"
    return (
        f"{profile_context}\n\n"
        f"UBICACIONES PREFERIDAS (prioriza empresas que contraten aqui o remoto): {locs}\n\n"
        f"EMPRESAS YA CONFIGURADAS (no las repitas):\n{known_c}\n\n"
        f"QUERIES YA CONFIGURADAS (no las repitas):\n{known_q}\n\n"
        "Propon nuevas empresas reales y nuevas frases de busqueda que encajen "
        "con el perfil y responde en JSON."
    )


def process_discover(session):
    """Claim a discovery job, propose companies/queries, let the backend validate.
    Returns True if a job was handled."""
    r = session.post(
        f"{API_BASE}/careers/worker/discover/claim",
        json={"worker_id": WORKER_ID},
        timeout=30,
    )
    r.raise_for_status()
    job = r.json().get("job")
    if not job:
        return False
    job_id = job["id"]
    ctx = job.get("profile_context") or ""
    known_c = job.get("known_companies") or []
    known_q = job.get("known_queries") or []
    locations = job.get("locations") or []
    print(f"[discover {job_id}] proposing (known: {len(known_c)} companies)")
    try:
        out = _run_ollama_json(
            DISCOVER_SYSTEM_PROMPT,
            _build_discover_prompt(ctx, known_c, known_q, locations),
        )
        body = {
            "job_id": job_id,
            "companies": out.get("companies") or [],
            "queries": out.get("queries") or [],
        }
        rr = session.post(
            f"{API_BASE}/careers/worker/discover/result", json=body, timeout=300,
        )
        rr.raise_for_status()
        summ = rr.json()
        print(f"[discover {job_id}] added={len(summ.get('added', []))} "
              f"rejected={len(summ.get('rejected', []))} "
              f"queries+={len(summ.get('queries_added', []))}")
    except Exception as e:  # noqa: BLE001
        print(f"[discover {job_id}] failed: {e}")
        try:
            session.post(
                f"{API_BASE}/careers/worker/discover/fail",
                json={"job_id": job_id, "error": str(e)[:1000]},
                timeout=30,
            )
        except Exception as e2:  # noqa: BLE001
            print(f"[warn] could not report discover failure {job_id}: {e2}")
    return True


def careers_orchestrate(session):
    """Drive the full opportunity pipeline once: enqueue a discovery pass, fetch
    new openings from every source, and queue anything unscored. The per-job LLM
    work (discovery proposals + fit scoring) is then handled by the normal loop."""
    try:
        session.post(f"{API_BASE}/careers/discover", json={}, timeout=30)
    except Exception as e:  # noqa: BLE001
        print(f"[careers] discover enqueue failed: {e}")
    try:
        r = session.post(f"{API_BASE}/careers/opportunities/fetch", json={}, timeout=600)
        r.raise_for_status()
        data = r.json()
        print(f"[careers] fetch: {data.get('inserted')} new from {data.get('sources')} source(s)")
    except Exception as e:  # noqa: BLE001
        print(f"[careers] fetch failed: {e}")
    try:
        r = session.post(f"{API_BASE}/careers/opportunities/rescore",
                         json={"scope": "unscored"}, timeout=120)
        r.raise_for_status()
        print(f"[careers] queued for scoring: {r.json().get('enqueued')}")
    except Exception as e:  # noqa: BLE001
        print(f"[careers] rescore failed: {e}")


def main():
    print(f"knowledge-worker starting: api={API_BASE} model={OLLAMA_MODEL} "
          f"embed={EMBED_MODEL} worker_id={WORKER_ID}")
    token = login()
    session = make_session(token)
    # Mutable holder so the voice thread always sees the current session even
    # after a token refresh (which replaces the session object).
    holder = {"session": session}

    pause_event = None
    voice = None
    if VOICE_ENABLED:
        try:
            from voice_mode import VoiceMode
            pause_event = threading.Event()
            voice = VoiceMode(
                pause_event=pause_event,
                get_session=lambda: holder["session"],
                answer_fn=answer_question,
                interrupt_event=INTERRUPT,
            )
            voice.start()
            print("[voice] wake-word listener active")
        except Exception as e:  # noqa: BLE001
            print(f"[voice] disabled: {e}")
            pause_event = None

    last_careers = None  # None => force a careers cycle on the first iteration
    try:
        while True:
            # While a voice interaction is running, give it exclusive use of
            # Ollama: do not claim chats / extraction / embedding jobs.
            if pause_event is not None and pause_event.is_set():
                time.sleep(0.2)
                continue
            # Autonomous careers pipeline: kick off a fetch/discovery cycle on a
            # timer (first pass runs immediately on startup).
            if CAREERS_AUTO and (last_careers is None
                                 or (time.monotonic() - last_careers) >= CAREERS_INTERVAL_SEC):
                last_careers = time.monotonic()
                try:
                    careers_orchestrate(holder["session"])
                except requests.HTTPError as e:
                    if e.response is not None and e.response.status_code == 401:
                        token = login()
                        holder["session"] = make_session(token)
                    else:
                        print(f"[careers] orchestrate HTTP error: {e}")
                except Exception as e:  # noqa: BLE001
                    print(f"[careers] orchestrate error: {e}")
            try:
                # Priority: chat turns (human waiting) > careers scoring >
                # source discovery > extraction > embed backfill.
                handled = process_chat(holder["session"])
                if not handled:
                    handled = process_score(holder["session"])
                if not handled:
                    handled = process_discover(holder["session"])
                if not handled:
                    handled = process_one(holder["session"])
                if not handled:
                    # Idle: use the time to backfill embeddings.
                    handled = process_embeddings(holder["session"])
            except requests.HTTPError as e:
                if e.response is not None and e.response.status_code == 401:
                    print("[auth] token expired, re-logging in")
                    token = login()
                    holder["session"] = make_session(token)
                    continue
                print(f"[error] HTTP: {e}")
                handled = False
            except Exception as e:  # noqa: BLE001
                print(f"[error] {e}")
                handled = False
            if not handled:
                time.sleep(POLL_INTERVAL)
    finally:
        # Close the audio stream cleanly so Ctrl+C doesn't segfault in PortAudio.
        if voice is not None:
            voice.stop()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nstopped")
        sys.exit(0)
