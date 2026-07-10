// ============================================================================
// Ask — chat UI over the Knowledge Engine (RAG).
// Sends a question to POST /kn/chat/ask, then polls GET /kn/chat/{id} until the
// Mac worker has embedded it, searched the base and generated a grounded answer.
// Conversation history is kept client-side (each chat turn is independent on the
// server) and persisted in localStorage so it survives reloads.
// ============================================================================

const ASK_API_BASE = "https://api-dashboard-production-fc05.up.railway.app";
const ASK_STORAGE_KEY = "dashboard.ask.history";
const ASK_POLL_INTERVAL = 1500;   // ms between polls
const ASK_POLL_TIMEOUT = 120000;  // ms before giving up

// messages: [{ role: 'user'|'assistant', content, status?, context?, error? }]
let askMessages = [];
let askBusy = false;

const askEls = {};

document.addEventListener("DOMContentLoaded", () => {
  askEls.messages = document.getElementById("askMessages");
  askEls.form = document.getElementById("askForm");
  askEls.input = document.getElementById("askInput");
  askEls.send = document.getElementById("askSend");
  askEls.newChat = document.getElementById("askNewChat");
  if (!askEls.messages || !askEls.form) return;

  loadAskHistory();
  renderAskMessages();

  askEls.form.addEventListener("submit", (e) => {
    e.preventDefault();
    submitAsk();
  });

  // Enter to send, Shift+Enter for a newline. Auto-grow the textarea.
  askEls.input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submitAsk();
    }
  });
  askEls.input.addEventListener("input", autoGrowAskInput);

  askEls.newChat.addEventListener("click", () => {
    if (askBusy) return;
    askMessages = [];
    saveAskHistory();
    renderAskMessages();
    askEls.input.focus();
  });

  // Clicking a library-backed citation jumps to that item in the Library tab.
  askEls.messages.addEventListener("click", (e) => {
    const btn = e.target.closest(".ask-cite-src--link");
    if (!btn) return;
    const id = btn.getAttribute("data-lib-item");
    if (id && typeof window.openLibraryItem === "function") {
      window.openLibraryItem(id);
    }
  });
});

function autoGrowAskInput() {
  const el = askEls.input;
  el.style.height = "auto";
  el.style.height = Math.min(el.scrollHeight, 160) + "px";
}

async function submitAsk() {
  if (askBusy) return;
  const question = (askEls.input.value || "").trim();
  if (!question) return;

  askMessages.push({ role: "user", content: question });
  const assistantIdx = askMessages.push({
    role: "assistant", content: "", status: "pending",
  }) - 1;
  saveAskHistory();

  askEls.input.value = "";
  autoGrowAskInput();
  setAskBusy(true);
  renderAskMessages();

  try {
    const res = await fetch(`${ASK_API_BASE}/kn/chat/ask`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
    });
    if (!res.ok) throw new Error(`ask failed (${res.status})`);
    const data = await res.json();
    await pollAsk(data.chat_id, assistantIdx);
  } catch (err) {
    askMessages[assistantIdx].status = "error";
    askMessages[assistantIdx].error = String(err.message || err);
    saveAskHistory();
    renderAskMessages();
  } finally {
    setAskBusy(false);
    askEls.input.focus();
  }
}

async function pollAsk(chatId, assistantIdx) {
  const started = Date.now();
  while (Date.now() - started < ASK_POLL_TIMEOUT) {
    await sleep(ASK_POLL_INTERVAL);
    const res = await fetch(`${ASK_API_BASE}/kn/chat/${chatId}`);
    if (!res.ok) throw new Error(`poll failed (${res.status})`);
    const data = await res.json();

    if (data.status === "done") {
      askMessages[assistantIdx] = {
        role: "assistant",
        content: data.answer || "",
        status: "done",
        context: data.context || [],
      };
      saveAskHistory();
      renderAskMessages();
      return;
    }
    if (data.status === "error") {
      throw new Error(data.error || "worker error");
    }
    // pending | in_progress: keep the thinking indicator, keep polling.
  }
  throw new Error("timed out waiting for an answer");
}

function setAskBusy(busy) {
  askBusy = busy;
  if (askEls.send) askEls.send.disabled = busy;
  if (askEls.input) askEls.input.disabled = busy;
}

// --- rendering -------------------------------------------------------------

function renderAskMessages() {
  const box = askEls.messages;
  if (askMessages.length === 0) {
    box.innerHTML =
      '<div class="ask-empty">Ask a question about anything in your knowledge base.</div>';
    return;
  }

  box.innerHTML = askMessages.map((m, i) => renderAskMessage(m, i)).join("");

  // Render markdown answers (and any math) after inserting into the DOM.
  box.querySelectorAll(".ask-md[data-md]").forEach((el) => {
    const raw = askMessages[Number(el.dataset.idx)]?.content || "";
    el.innerHTML = renderMarkdown(raw);
    if (window.renderMathInElement) {
      try {
        window.renderMathInElement(el, {
          delimiters: [
            { left: "$$", right: "$$", display: true },
            { left: "$", right: "$", display: false },
          ],
          throwOnError: false,
        });
      } catch (_) { /* noop */ }
    }
  });

  box.scrollTop = box.scrollHeight;
}

function renderAskMessage(m, i) {
  if (m.role === "user") {
    return `<div class="ask-msg ask-msg--user"><div class="ask-bubble">${
      escapeHtml(m.content)
    }</div></div>`;
  }

  // assistant
  if (m.status === "pending") {
    return `<div class="ask-msg ask-msg--assistant"><div class="ask-bubble">
      <span class="ask-thinking"><span></span><span></span><span></span></span>
      <span class="ask-thinking-label">Thinking\u2026</span>
    </div></div>`;
  }
  if (m.status === "error") {
    return `<div class="ask-msg ask-msg--assistant"><div class="ask-bubble ask-bubble--error">
      ${escapeHtml(m.error || "Something went wrong.")}
    </div></div>`;
  }

  const citations = renderAskCitations(m.context);
  return `<div class="ask-msg ask-msg--assistant"><div class="ask-bubble">
    <div class="ask-md" data-md data-idx="${i}"></div>
    ${citations}
  </div></div>`;
}

function renderAskCitations(context) {
  if (!Array.isArray(context) || context.length === 0) return "";
  const chips = context.map((u) => {
    // Personal-metric answers carry a live-data summary, not a unit citation.
    if (u.kind === "personal") {
      return `<div class="ask-cite ask-cite--data">
        <span class="ask-cite-data-tag">📊 ${escapeHtml(labelPersonalDomain(u.domain))}${
          u.period_days ? ` · ${u.period_days}d` : ""
        }</span>
        <span class="ask-cite-text">${escapeHtml(u.summary || "")}</span>
      </div>`;
    }
    const sim = typeof u.similarity === "number"
      ? ` <span class="ask-cite-sim">${(u.similarity * 100).toFixed(0)}%</span>`
      : "";
    return `<div class="ask-cite" title="${escapeHtml(u.text || "")}">
      <span class="ask-cite-id">[U${u.ref_id}]</span>
      ${renderAskFactuality(u.factuality)}
      <span class="ask-cite-text">${escapeHtml(u.text || "")}</span>${sim}
      ${renderAskSource(u.sources)}
    </div>`;
  }).join("");
  return `<details class="ask-cites">
    <summary>${context.length} source${context.length === 1 ? "" : "s"}</summary>
    ${chips}
  </details>`;
}

function labelPersonalDomain(domain) {
  return { gym: "Entrenamientos", weight: "Peso", water: "Agua" }[domain] || "Datos";
}

// Objectivity badge: distinguishes a verifiable fact from a subjective opinion.
function renderAskFactuality(factuality) {
  if (factuality === "fact") {
    return `<span class="ask-cite-fact ask-cite-fact--fact" title="Objective fact">hecho</span>`;
  }
  if (factuality === "opinion") {
    return `<span class="ask-cite-fact ask-cite-fact--opinion" title="Subjective opinion">opinión</span>`;
  }
  return "";
}

// A unit can trace back to one or more source documents. When a document was
// ingested from the Library, render a clickable link that opens that item.
function renderAskSource(sources) {
  if (!Array.isArray(sources) || sources.length === 0) return "";
  const seen = new Set();
  const tags = [];
  for (const s of sources) {
    const label = s.library_title || s.document_title;
    if (!label) continue;
    const key = s.library_item_id != null ? `L${s.library_item_id}` : `D${s.document_id}`;
    if (seen.has(key)) continue;
    seen.add(key);
    if (s.library_item_id != null && typeof window.openLibraryItem === "function") {
      tags.push(
        `<button type="button" class="ask-cite-src ask-cite-src--link" ` +
        `data-lib-item="${s.library_item_id}" title="Open in Library">` +
        `📄 ${escapeHtml(label)}</button>`
      );
    } else {
      tags.push(`<span class="ask-cite-src">📄 ${escapeHtml(label)}</span>`);
    }
  }
  return tags.length ? `<div class="ask-cite-sources">${tags.join("")}</div>` : "";
}

function renderMarkdown(text) {
  if (window.marked && typeof window.marked.parse === "function") {
    try {
      return window.marked.parse(text, { breaks: true });
    } catch (_) { /* fall through */ }
  }
  return escapeHtml(text).replace(/\n/g, "<br>");
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

// --- persistence -----------------------------------------------------------

function loadAskHistory() {
  try {
    const raw = localStorage.getItem(ASK_STORAGE_KEY);
    if (!raw) return;
    const parsed = JSON.parse(raw);
    if (Array.isArray(parsed)) {
      // Drop any turn that was still pending when the page was closed.
      askMessages = parsed.filter(
        (m) => !(m.role === "assistant" && m.status === "pending")
      );
    }
  } catch (_) { /* ignore corrupt history */ }
}

function saveAskHistory() {
  try {
    localStorage.setItem(ASK_STORAGE_KEY, JSON.stringify(askMessages));
  } catch (_) { /* storage full / unavailable */ }
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}
