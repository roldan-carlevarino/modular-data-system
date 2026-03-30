
const knowledgeState = {
  concept_id: null,
  project_id: null,
  mode: null,
  block_types: [] // empty array means “all block types”
};

let cachedConcepts = []; // global concept cache for ingest preview

const KNOWLEDGE_API_BASE = "https://api-dashboard-production-fc05.up.railway.app";

// ── Backblaze B2 signed-URL cache ──────────────────────────────
const b2Cache = new Map(); // path → { url, expiry }

async function resolveB2Images(text) {
  if (!text || !text.includes('b2://')) return text;

  const paths = [...new Set(
    [...text.matchAll(/b2:\/\/([^\s)"']+)/g)].map(m => m[1])
  )];
  if (!paths.length) return text;

  const now = Date.now();
  const resolved = {};

  await Promise.all(paths.map(async (path) => {
    const cached = b2Cache.get(path);
    if (cached && cached.expiry > now) {
      resolved[path] = cached.url;
      return;
    }
    try {
      const res = await fetch(
        `${KNOWLEDGE_API_BASE}/media/signed-url?file=${encodeURIComponent(path)}`
      );
      const data = await res.json();
      const expiry = now + 55 * 60 * 1000; // 55 min (under 1-hr TTL)
      b2Cache.set(path, { url: data.url, expiry });
      resolved[path] = data.url;
    } catch {
      resolved[path] = `b2://${path}`; // keep original on error
    }
  }));

  return text.replace(/b2:\/\/([^\s)"']+)/g, (_, p) => resolved[p] ?? `b2://${p}`);
}
// ──────────────────────────────────────────────────────────────


const projectSelect = document.getElementById("projectSelect");
const conceptTree = document.getElementById("conceptTree");
const modeSelect = document.getElementById("modeSelect");
const blockTypeFilters = document.getElementById("blockTypeFilters");
const viewer = document.getElementById("knowledgeViewer");

// ===============================
// INIT
// ===============================
document.addEventListener("DOMContentLoaded", () => {
  setupModeSelector();
  setupBlockTypeFilters();
  setupKnowledgeSidebar();
  loadProjects();
  loadConcepts();

  const searchInput = document.getElementById("conceptSearch");
  if (searchInput) {
    searchInput.addEventListener("input", () => filterConceptTree(searchInput.value));
  }
});

function setupKnowledgeSidebar() {
  const sidebar = document.getElementById("knowledgeSidebar");
  const toggleBtn = document.getElementById("knowledgeToggle");
  const layout = document.querySelector(".knowledge-layout");

  if (!sidebar || !toggleBtn || !layout) return;

  toggleBtn.addEventListener("click", () => {
    const collapsed = sidebar.classList.toggle("collapsed");
    layout.classList.toggle("collapsed", collapsed);
    toggleBtn.textContent = collapsed ? "\u203a" : "\u2039";
    toggleBtn.title = collapsed ? "Expand sidebar" : "Collapse sidebar";
  });
}

async function loadProjects() {
  if (!projectSelect) return;

  const res = await fetch(`${KNOWLEDGE_API_BASE}/knowledge/projects`);
  const projects = await res.json();

  projectSelect.innerHTML = `<option value="">None</option>`;

  projects.forEach(p => {
    const opt = document.createElement("option");
    opt.value = p.id;
    opt.textContent = p.name;
    projectSelect.appendChild(opt);
  });

  projectSelect.addEventListener("change", () => {
    knowledgeState.project_id = projectSelect.value
      ? Number(projectSelect.value)
      : null;

    loadConcepts();
    fetchKnowledge(); // Reload with new filter
  });
}

async function loadConcepts() {
  if (!conceptTree) return;

  let url = `${KNOWLEDGE_API_BASE}/knowledge/concepts`;
  if (knowledgeState.project_id) {
    url += `?project_id=${knowledgeState.project_id}`;
  }

  const res = await fetch(url);
  if (!res.ok) {
    const errorText = await res.text();
    renderConceptTree([]);
    console.error("Error loading concepts:", errorText);
    return;
  }

  const concepts = await res.json();
  if (!Array.isArray(concepts)) {
    renderConceptTree([]);
    console.error("Knowledge concepts invalid response:", concepts);
    return;
  }

  renderConceptTree(concepts);
  cachedConcepts = concepts;
}

// REVISAR QUE ESTO ES LO DEL BOTON DE ADD CONCEPT

// MODO MODIFICAR CONTENIDOS
let isModifyingContents = false;
const modifyContentsBtn = document.getElementById('modifyConceptBtn');
if (modifyContentsBtn) {
  modifyContentsBtn.addEventListener('click', () => {
    isModifyingContents = !isModifyingContents;
    modifyContentsBtn.classList.toggle('active', isModifyingContents);
    viewer.classList.toggle('modifying', isModifyingContents);
    conceptTree.classList.toggle('is-modifying', isModifyingContents);
    fetchKnowledge();
  });
}

async function createConceptFromPrompt() {
  const name = prompt('New Concept');
  if (!name) return;

  const parent_concept_id = knowledgeState.concept_id || null;
  const project_id = knowledgeState.project_id || null;

  const res = await fetch(`${KNOWLEDGE_API_BASE}/knowledge/concepts/new`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, parent_concept_id, project_id })
  });

  if (res.ok) {
    loadConcepts();
  } else {
    alert('Error creating concept');
  }
}

async function createBlockFromPrompt() {
  if (!knowledgeState.concept_id) {
    alert('Select a concept first');
    return;
  }

  const content = prompt('Content of the new block:');
  if (!content) return;

  const block_type = prompt('Block type (definition, intuition, formula, etc):');
  if (!block_type) return;

  const res = await fetch(`${KNOWLEDGE_API_BASE}/knowledge/block/new`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      concept_id: knowledgeState.concept_id,
      content,
      block_type,
      project_id: knowledgeState.project_id || null,
      mode: knowledgeState.mode || null
    })
  });

  if (res.ok) {
    fetchKnowledge();
  } else {
    alert('Error creating block');
  }
}

const addBtn = document.getElementById('addConceptBtn');
if (addBtn) {
  addBtn.addEventListener('click', async () => {
    const defaultOption = knowledgeState.concept_id ? 'b' : 'c';
    const choice = (prompt('Add what? (c = concept, b = block)', defaultOption) || '').trim().toLowerCase();
    if (!choice) return;

    if (choice === 'b' || choice === 'block') {
      await createBlockFromPrompt();
      return;
    }

    if (choice === 'c' || choice === 'concept') {
      await createConceptFromPrompt();
      return;
    }

    alert('Use c (concept) or b (block)');
  });
}

function renderConceptTree(concepts) {
  // Persist which concepts had their children collapsed
  const collapsedIds = new Set(
    [...conceptTree.querySelectorAll(".concept-children.collapsed")]
      .map(el => el.dataset.conceptId)
  );

  conceptTree.innerHTML = "";

  if (!Array.isArray(concepts) || concepts.length === 0) {
    const empty = document.createElement("div");
    empty.classList.add("knowledge-empty");
    empty.textContent = "No concepts available";
    conceptTree.appendChild(empty);
    return;
  }

  const byParent = {};
  concepts.forEach(c => {
    const parentKey = c.parent_concept_id ?? "root";
    byParent[parentKey] ||= [];
    byParent[parentKey].push(c);
  });

  function renderNode(parentId, container, depth = 0) {
    (byParent[parentId] || []).forEach(c => {
      const hasChildren = (byParent[c.id] || []).length > 0;

      const el = document.createElement("div");
      el.style.paddingLeft = `${depth * 12}px`;
      el.classList.add("concept-item");
      el.dataset.conceptId = c.id;
      el.dataset.parentId = c.parent_concept_id ?? "root";
      el.dataset.conceptName = c.name.toLowerCase();

      const toggleBtn = document.createElement("button");
      toggleBtn.classList.add("concept-toggle");
      toggleBtn.textContent = hasChildren ? "▾" : "";
      toggleBtn.style.visibility = hasChildren ? "visible" : "hidden";

      const nameSpan = document.createElement("span");
      nameSpan.textContent = c.name;
      nameSpan.classList.add("concept-name");
      nameSpan.addEventListener("click", () => {
        const wasActive = el.classList.contains("active");

        document
          .querySelectorAll(".concept-item.active")
          .forEach(x => x.classList.remove("active"));

        if (wasActive) {
          knowledgeState.concept_id = null;
          viewer.innerHTML = "";
          return;
        }

        el.classList.add("active");
        knowledgeState.concept_id = c.id;
        console.log("🔵 Concepto seleccionado:", c.id, c.name);
        pinRootAncestorToTop(c.id);
        fetchKnowledge();
      });

      const deleteBtn = document.createElement("button");
      deleteBtn.textContent = "×";
      deleteBtn.classList.add("concept-delete-btn");
      deleteBtn.title = "Delete concept (and all children)";
      deleteBtn.addEventListener("click", async (e) => {
        e.stopPropagation();
        if (!confirm(`Delete "${c.name}" and all its children?`)) return;
        try {
          const res = await fetch(`${KNOWLEDGE_API_BASE}/knowledge/concepts/${c.id}`, {
            method: "DELETE"
          });
          if (!res.ok) throw new Error(await res.text());
          if (knowledgeState.concept_id === c.id) {
            knowledgeState.concept_id = null;
            viewer.innerHTML = "";
          }
          loadConcepts();
        } catch (err) {
          console.error("Error deleting concept:", err);
          alert("Error deleting concept");
        }
      });

      el.appendChild(toggleBtn);
      el.appendChild(nameSpan);

      // Schema button for this concept
      const schemaBtn = document.createElement("button");
      schemaBtn.textContent = "\uD83D\uDCD0";
      schemaBtn.classList.add("concept-schema-btn");
      if (conceptSchemaStore[c.id] && Object.keys(conceptSchemaStore[c.id]).some(k => conceptSchemaStore[c.id][k].content)) {
        schemaBtn.classList.add("has-schemas");
      }
      schemaBtn.title = "Schemas for " + c.name;
      schemaBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        openConceptSchemasModal(c.id, c.name);
      });
      el.appendChild(schemaBtn);

      el.appendChild(deleteBtn);
      container.appendChild(el);

      const childrenContainer = document.createElement("div");
      childrenContainer.classList.add("concept-children");
      childrenContainer.dataset.conceptId = c.id;
      if (collapsedIds.has(String(c.id))) {
        childrenContainer.classList.add("collapsed");
        toggleBtn.textContent = "▸";
      }
      container.appendChild(childrenContainer);
      renderNode(c.id, childrenContainer, depth + 1);

      if (hasChildren) {
        toggleBtn.addEventListener("click", (e) => {
          e.stopPropagation();
          const collapsed = childrenContainer.classList.toggle("collapsed");
          toggleBtn.textContent = collapsed ? "▸" : "▾";
        });
      }
    });
  }

  renderNode("root", conceptTree);

  // Re-apply current search filter after re-render
  const searchInput = document.getElementById("conceptSearch");
  if (searchInput && searchInput.value.trim()) {
    filterConceptTree(searchInput.value.trim());
  }
}

function pinRootAncestorToTop(conceptId) {
  // Walk up parent chain until we reach a direct child of conceptTree (parentId === "root")
  let el = conceptTree.querySelector(`.concept-item[data-concept-id="${conceptId}"]`);
  if (!el) return;

  while (el && el.dataset.parentId !== "root") {
    const parentId = el.dataset.parentId;
    el = conceptTree.querySelector(`.concept-item[data-concept-id="${parentId}"]`);
  }
  if (!el) return;

  // el is now the root-level concept-item; its next sibling is the concept-children div
  const children = el.nextElementSibling;
  conceptTree.insertBefore(el, conceptTree.firstChild);
  if (children && children.classList.contains("concept-children")) {
    conceptTree.insertBefore(children, conceptTree.children[1]);
  }
}

function filterConceptTree(term) {
  const tree = conceptTree;
  const q = term.toLowerCase().trim();

  if (!q) {
    tree.classList.remove("is-searching");
    tree.querySelectorAll(".concept-item").forEach(el => el.classList.remove("concept-hidden"));
    return;
  }

  tree.classList.add("is-searching");

  // Build parent map: conceptId -> parentId
  const parentMap = {};
  tree.querySelectorAll(".concept-item[data-concept-id]").forEach(el => {
    parentMap[el.dataset.conceptId] = el.dataset.parentId;
  });

  // Find directly matching IDs
  const matchedIds = new Set();
  tree.querySelectorAll(".concept-item[data-concept-name]").forEach(el => {
    if (el.dataset.conceptName.includes(q)) matchedIds.add(el.dataset.conceptId);
  });

  // Collect all ancestor IDs of matched nodes
  const visibleIds = new Set(matchedIds);
  matchedIds.forEach(id => {
    let cur = parentMap[id];
    while (cur && cur !== "root") {
      visibleIds.add(cur);
      cur = parentMap[cur];
    }
  });

  // Show/hide items
  tree.querySelectorAll(".concept-item[data-concept-id]").forEach(el => {
    el.classList.toggle("concept-hidden", !visibleIds.has(el.dataset.conceptId));
  });
}

function setupModeSelector() {
  if (!modeSelect) return;
  modeSelect.addEventListener("change", () => {
    console.log("Mode changed to:", modeSelect.value);
    knowledgeState.mode = modeSelect.value || null;
    console.log("Updated knowledgeState:", knowledgeState);
    fetchKnowledge();
  });

  
}


function setupBlockTypeFilters() {
  if (!blockTypeFilters) return;

  const types = [
    "definition",
    "intuition",
    "formula",
    "example",
    "warning",
    "code"
  ];

  blockTypeFilters.innerHTML = "";

  types.forEach(type => {
    const label = document.createElement("label");
    label.style.marginRight = "8px";

    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.value = type;
    cb.checked = true;

    cb.addEventListener("change", () => {
      const checked = [...blockTypeFilters.querySelectorAll("input:checked")]
        .map(x => x.value);

      knowledgeState.block_types =
        checked.length === types.length ? [] : checked;

      fetchKnowledge();
    });

    label.appendChild(cb);
    label.appendChild(document.createTextNode(" " + type));
    blockTypeFilters.appendChild(label);
  });
}

// ===============================
// FETCH KNOWLEDGE
// ===============================
async function fetchKnowledge() {
  if (!knowledgeState.concept_id || !viewer) {
    console.log("❌ No hay concept_id, saliendo");  // ← Añade esto
    return;
  }

  const params = new URLSearchParams();
  params.append("concept_id", knowledgeState.concept_id);

  if (knowledgeState.project_id) {
    params.append("project_id", knowledgeState.project_id);
  }

  if (knowledgeState.mode) {
    params.append("mode", knowledgeState.mode);
  }

  console.log("🔵 URL:", `${KNOWLEDGE_API_BASE}/knowledge/query?${params.toString()}`);  // ← Añade esto

  const res = await fetch(`${KNOWLEDGE_API_BASE}/knowledge/query?${params.toString()}`);
  const blocks = await res.json();
  console.log("🔵 Blocks recibidos:", blocks.length); 
  await renderKnowledge(blocks);
}

// ===============================
// MERMAID DIAGRAM HELPERS
// ===============================

// Initialize mermaid with dark theme
if (typeof mermaid !== 'undefined') {
    mermaid.initialize({
        startOnLoad: false,
        theme: 'dark',
        securityLevel: 'loose',
        flowchart: { useMaxWidth: true, htmlLabels: true }
    });
}

const DIAGRAM_TEMPLATES = {
    flowchart: `graph TD\n    A[Start] --> B{Decision}\n    B -->|Yes| C[Result 1]\n    B -->|No| D[Result 2]`,
    sequence: `sequenceDiagram\n    participant A\n    participant B\n    A->>B: Request\n    B-->>A: Response`,
    classDiagram: `classDiagram\n    class Animal {\n        +String name\n        +eat()\n    }\n    class Dog {\n        +bark()\n    }\n    Animal <|-- Dog`,
    mindmap: `mindmap\n    root((Topic))\n        Branch A\n            Leaf 1\n            Leaf 2\n        Branch B\n            Leaf 3`
};

let _diagramIdCounter = 0;

// ── Plotly chart rendering ──────────────────────────────────────
function renderCharts(container) {
  if (typeof Plotly === 'undefined') return;
  const isDark = document.documentElement.getAttribute('data-theme') !== 'light';
  const fontColor = isDark ? '#c9d1d9' : '#1f2328';
  const gridColor = isDark ? 'rgba(255,255,255,0.1)' : 'rgba(0,0,0,0.1)';

  container.querySelectorAll('.chart-render:not(.chart-rendered)').forEach(div => {
    try {
      const json = div.dataset.chartJson;
      const config = JSON.parse(json);
      const layoutDefaults = {
        paper_bgcolor: 'transparent',
        plot_bgcolor: 'transparent',
        font: { family: 'Inter, system-ui, sans-serif', color: fontColor },
        margin: { t: 40, r: 20, b: 50, l: 60 },
        xaxis: { gridcolor: gridColor, zerolinecolor: gridColor },
        yaxis: { gridcolor: gridColor, zerolinecolor: gridColor }
      };
      const layout = Object.assign({}, layoutDefaults, config.layout || {});
      // Merge nested xaxis/yaxis if provided
      if (config.layout?.xaxis) layout.xaxis = Object.assign({}, layoutDefaults.xaxis, config.layout.xaxis);
      if (config.layout?.yaxis) layout.yaxis = Object.assign({}, layoutDefaults.yaxis, config.layout.yaxis);

      Plotly.newPlot(div, config.data || [], layout, { responsive: true, displayModeBar: false });
      div.classList.add('chart-rendered');
    } catch (e) {
      div.innerHTML = `<pre class="diagram-error">⚠ Chart error: ${e.message}</pre>`;
    }
  });
}
// ───────────────────────────────────────────────────────────────

function extractMermaidCode(text) {
    // Check if content is a mermaid block
    const match = text.match(/^\s*```mermaid\s*\n([\s\S]*?)\n\s*```\s*$/);
    if (match) return match[1].trim();
    // Also accept raw mermaid syntax (starts with graph, flowchart, sequenceDiagram, etc.)
    if (/^\s*(graph |flowchart |sequenceDiagram|classDiagram|mindmap|erDiagram|gantt|pie|gitGraph)/m.test(text)) {
        return text.trim();
    }
    return null;
}

function wrapMermaidCode(code) {
    return '```mermaid\n' + code.trim() + '\n```';
}

async function renderMermaidPreview(code, container) {
    container.innerHTML = '';
    if (!code.trim()) {
        container.innerHTML = '<p class="diagram-placeholder">Write Mermaid code on the left...</p>';
        return;
    }
    try {
        const id = 'mermaid-preview-' + (++_diagramIdCounter);
        const { svg } = await mermaid.render(id, code.trim());
        container.innerHTML = svg;
    } catch (err) {
        container.innerHTML = `<p class="diagram-error">⚠ ${err.message || 'Syntax error'}</p>`;
    }
}

function buildDiagramEditor(mermaidCode) {
    const code = mermaidCode || DIAGRAM_TEMPLATES.flowchart;
    return `
    <div class="diagram-editor">
        <div class="diagram-toolbar">
            <span class="diagram-toolbar-label">Quick add:</span>
            <button type="button" class="diagram-quick-btn" data-insert="    X[Node] --> Y[Node]\\n" title="Add arrow">→ Arrow</button>
            <button type="button" class="diagram-quick-btn" data-insert="    X{Decision}\\n" title="Add decision">{?} Decision</button>
            <button type="button" class="diagram-quick-btn" data-insert="    X([Rounded])\\n" title="Add rounded node">◯ Rounded</button>
            <button type="button" class="diagram-quick-btn" data-insert="    X[[Subroutine]]\\n" title="Add subroutine">▭ Subroutine</button>
            <button type="button" class="diagram-quick-btn" data-insert="    X[(Database)]\\n" title="Add database">⛁ Database</button>
            <button type="button" class="diagram-quick-btn" data-insert="    X -->|label| Y\\n" title="Add labeled arrow">🏷 Label</button>
            <button type="button" class="diagram-quick-btn" data-insert="    X -.-> Y\\n" title="Add dotted arrow">⋯ Dotted</button>
            <button type="button" class="diagram-quick-btn" data-insert="    X ==> Y\\n" title="Add thick arrow">⇒ Thick</button>
            <select class="diagram-template-select" title="Load template">
                <option value="">Template...</option>
                <option value="flowchart">Flowchart</option>
                <option value="sequence">Sequence</option>
                <option value="classDiagram">Class Diagram</option>
                <option value="mindmap">Mindmap</option>
            </select>
        </div>
        <div class="diagram-panes">
            <textarea class="diagram-code">${code.replace(/</g, '&lt;')}</textarea>
            <div class="diagram-preview"></div>
        </div>
    </div>`;
}

function initDiagramEditor(container) {
    const codeArea = container.querySelector('.diagram-code');
    const preview = container.querySelector('.diagram-preview');
    const templateSelect = container.querySelector('.diagram-template-select');

    // Initial render
    renderMermaidPreview(codeArea.value, preview);

    // Live preview on input (debounced)
    let debounce = null;
    codeArea.addEventListener('input', () => {
        clearTimeout(debounce);
        debounce = setTimeout(() => renderMermaidPreview(codeArea.value, preview), 400);
    });

    // Quick-add buttons
    container.querySelectorAll('.diagram-quick-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            const insert = btn.dataset.insert.replace(/\\n/g, '\n');
            const start = codeArea.selectionStart;
            const end = codeArea.selectionEnd;
            codeArea.value = codeArea.value.slice(0, start) + insert + codeArea.value.slice(end);
            codeArea.selectionStart = codeArea.selectionEnd = start + insert.length;
            codeArea.focus();
            codeArea.dispatchEvent(new Event('input'));
        });
    });

    // Template selector
    templateSelect.addEventListener('change', () => {
        const key = templateSelect.value;
        if (key && DIAGRAM_TEMPLATES[key]) {
            if (codeArea.value.trim() && !confirm('Replace current diagram with template?')) {
                templateSelect.value = '';
                return;
            }
            codeArea.value = DIAGRAM_TEMPLATES[key];
            codeArea.dispatchEvent(new Event('input'));
        }
        templateSelect.value = '';
    });
}

// ===============================
// CONCEPT SCHEMAS
// ===============================

const CONCEPT_SCHEMAS_KEY = "dashboard.conceptSchemas.v1";
let conceptSchemaStore = (() => {
    try {
        const p = JSON.parse(localStorage.getItem(CONCEPT_SCHEMAS_KEY) || "{}");
        return p && typeof p === 'object' ? p : {};
    } catch { return {}; }
})();

function saveConceptSchemaStore() {
    localStorage.setItem(CONCEPT_SCHEMAS_KEY, JSON.stringify(conceptSchemaStore));
}

function getConceptSchemaBucket(conceptId) {
    if (!conceptId) return null;
    if (!conceptSchemaStore[conceptId]) {
        conceptSchemaStore[conceptId] = {
            General: { content: "", updated_at: new Date().toISOString() }
        };
        saveConceptSchemaStore();
    }
    return conceptSchemaStore[conceptId];
}

// DOM refs
const csModal       = document.getElementById('conceptSchemasModal');
const csCloseBtn    = document.getElementById('conceptSchemasCloseBtn');
const csTitle       = document.getElementById('conceptSchemaTitle');
const csSelect      = document.getElementById('conceptSchemaSelect');
const csNewBtn      = document.getElementById('conceptSchemaNewBtn');
const csRenameBtn   = document.getElementById('conceptSchemaRenameBtn');
const csDuplicateBtn= document.getElementById('conceptSchemaDuplicateBtn');
const csTemplateBtn = document.getElementById('conceptSchemaTemplateBtn');
const csDeleteBtn   = document.getElementById('conceptSchemaDeleteBtn');
const csExportBtn   = document.getElementById('conceptSchemaExportBtn');
const csDiagramBtn  = document.getElementById('conceptSchemaDiagramBtn');
const csText        = document.getElementById('conceptSchemaText');
const csDiagramCont = document.getElementById('conceptSchemaDiagramContainer');
const csStatus      = document.getElementById('conceptSchemaStatus');

let csActiveConceptId   = null;
let csActiveConceptName = '';
let csDiagramActive     = false;
let csAutosaveTimer     = null;

function csSetStatus(text) { if (csStatus) csStatus.textContent = text; }

function csSaveCurrentSchema() {
    if (!csActiveConceptId || !csSelect.value) return;
    syncCsDiagramToTextarea();
    const bucket = getConceptSchemaBucket(csActiveConceptId);
    bucket[csSelect.value] = {
        content: csText.value,
        updated_at: new Date().toISOString()
    };
    saveConceptSchemaStore();
    csSetStatus('Saved "' + csSelect.value + '" \u00B7 ' + new Date().toLocaleTimeString());
}

function csQueueAutosave() {
    clearTimeout(csAutosaveTimer);
    csSetStatus("Saving\u2026");
    csAutosaveTimer = setTimeout(csSaveCurrentSchema, 450);
}

function csFlushAutosave() {
    if (csAutosaveTimer) {
        clearTimeout(csAutosaveTimer);
        csAutosaveTimer = null;
        csSaveCurrentSchema();
    }
}

function csRefreshSelectors() {
    if (!csActiveConceptId) return;
    const bucket = getConceptSchemaBucket(csActiveConceptId);
    const prev = csSelect.value;
    const names = Object.keys(bucket).sort((a, b) => {
        const at = bucket[a]?.updated_at ? new Date(bucket[a].updated_at).getTime() : 0;
        const bt = bucket[b]?.updated_at ? new Date(bucket[b].updated_at).getTime() : 0;
        return bt - at || a.localeCompare(b);
    });
    csSelect.innerHTML = "";
    names.forEach(n => {
        const opt = document.createElement('option');
        opt.value = n;
        opt.textContent = n;
        csSelect.appendChild(opt);
    });
    if (names.includes(prev)) csSelect.value = prev;
    else csSelect.value = names[0] || '';

    const current = bucket[csSelect.value];
    csText.disabled = false;
    csText.value = current?.content || "";
    const ts = current?.updated_at ? new Date(current.updated_at).toLocaleString() : "never";
    csSetStatus('Editing "' + csSelect.value + '" \u00B7 Last save: ' + ts);
}

// Diagram toggle for concept schemas
function syncCsDiagramToTextarea() {
    if (!csDiagramActive || !csDiagramCont) return;
    const codeArea = csDiagramCont.querySelector('.diagram-code');
    if (codeArea) csText.value = wrapMermaidCode(codeArea.value);
}

function activateCsDiagram() {
    if (csDiagramActive) return;
    csDiagramActive = true;
    csDiagramBtn.classList.add('kb-btn--active');
    const mermaidCode = extractMermaidCode(csText.value) || DIAGRAM_TEMPLATES.flowchart;
    csDiagramCont.innerHTML = buildDiagramEditor(mermaidCode);
    csDiagramCont.style.display = '';
    csText.style.display = 'none';
    initDiagramEditor(csDiagramCont);
    const codeArea = csDiagramCont.querySelector('.diagram-code');
    if (codeArea) {
        codeArea.addEventListener('input', () => {
            syncCsDiagramToTextarea();
            csQueueAutosave();
        });
    }
}

function deactivateCsDiagram() {
    if (!csDiagramActive) return;
    syncCsDiagramToTextarea();
    csDiagramActive = false;
    csDiagramBtn.classList.remove('kb-btn--active');
    csDiagramCont.innerHTML = '';
    csDiagramCont.style.display = 'none';
    csText.style.display = '';
}

function openConceptSchemasModal(conceptId, conceptName) {
    csFlushAutosave();
    deactivateCsDiagram();
    csActiveConceptId = conceptId;
    csActiveConceptName = conceptName;
    if (csTitle) csTitle.textContent = "\uD83D\uDCD0 " + conceptName + " \u2014 Schemas";
    csRefreshSelectors();
    csModal.style.display = 'flex';
    document.body.style.overflow = 'hidden';
}

function closeConceptSchemasModal() {
    csFlushAutosave();
    deactivateCsDiagram();
    csModal.style.display = 'none';
    document.body.style.overflow = '';
}

// Wire up concept schema UI
(function setupConceptSchemas() {
    if (!csModal || !csCloseBtn) return;

    csCloseBtn.addEventListener('click', closeConceptSchemasModal);
    csModal.addEventListener('click', e => { if (e.target === csModal) closeConceptSchemasModal(); });
    document.addEventListener('keydown', e => {
        if (e.key === 'Escape' && csModal.style.display !== 'none') closeConceptSchemasModal();
    });

    csSelect.addEventListener('change', () => {
        csFlushAutosave();
        deactivateCsDiagram();
        csRefreshSelectors();
    });

    csText.addEventListener('input', csQueueAutosave);
    csText.addEventListener('keydown', e => {
        if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
            e.preventDefault();
            csFlushAutosave();
            return;
        }
        if (e.key === 'Tab') {
            e.preventDefault();
            // Indent / outdent
            const text = csText.value;
            const start = csText.selectionStart;
            const end = csText.selectionEnd;
            const lineStart = text.lastIndexOf('\n', start - 1) + 1;
            const nextNl = text.indexOf('\n', end);
            const lineEnd = nextNl === -1 ? text.length : nextNl;
            const before = text.slice(0, lineStart);
            const block = text.slice(lineStart, lineEnd);
            const after = text.slice(lineEnd);
            const transformed = block.split('\n').map(l => {
                if (!e.shiftKey) return '  ' + l;
                if (l.startsWith('  ')) return l.slice(2);
                if (l.startsWith('\t')) return l.slice(1);
                return l;
            }).join('\n');
            csText.value = before + transformed + after;
            const delta = transformed.length - block.length;
            csText.selectionStart = lineStart;
            csText.selectionEnd = end + delta;
            csQueueAutosave();
        }
    });

    if (csDiagramBtn) {
        csDiagramBtn.addEventListener('click', () => {
            csDiagramActive ? deactivateCsDiagram() : activateCsDiagram();
        });
    }

    csNewBtn.addEventListener('click', () => {
        csFlushAutosave();
        if (!csActiveConceptId) return;
        const name = prompt('New schema name', 'Architecture');
        if (!name || !name.trim()) return;
        const bucket = getConceptSchemaBucket(csActiveConceptId);
        const key = name.trim();
        if (Object.keys(bucket).some(k => k.toLowerCase() === key.toLowerCase())) {
            alert('A schema with that name already exists');
            return;
        }
        bucket[key] = { content: '', updated_at: new Date().toISOString() };
        saveConceptSchemaStore();
        deactivateCsDiagram();
        csRefreshSelectors();
        csSelect.value = key;
        csRefreshSelectors();
        csText.focus();
    });

    csRenameBtn.addEventListener('click', () => {
        csFlushAutosave();
        const old = csSelect.value;
        if (!old) return;
        const next = prompt('Rename schema', old);
        if (!next || !next.trim() || next.trim() === old) return;
        const bucket = getConceptSchemaBucket(csActiveConceptId);
        const newKey = next.trim();
        if (Object.keys(bucket).some(k => k !== old && k.toLowerCase() === newKey.toLowerCase())) {
            alert('A schema with that name already exists');
            return;
        }
        bucket[newKey] = bucket[old];
        delete bucket[old];
        bucket[newKey].updated_at = new Date().toISOString();
        saveConceptSchemaStore();
        csRefreshSelectors();
        csSelect.value = newKey;
        csRefreshSelectors();
    });

    csDuplicateBtn.addEventListener('click', () => {
        csFlushAutosave();
        const old = csSelect.value;
        if (!old) return;
        const bucket = getConceptSchemaBucket(csActiveConceptId);
        let copyName = old + ' copy';
        let n = 2;
        while (Object.keys(bucket).some(k => k.toLowerCase() === copyName.toLowerCase())) {
            copyName = old + ' copy (' + n + ')';
            n++;
        }
        bucket[copyName] = { content: csText.value, updated_at: new Date().toISOString() };
        saveConceptSchemaStore();
        csRefreshSelectors();
        csSelect.value = copyName;
        csRefreshSelectors();
    });

    csTemplateBtn.addEventListener('click', () => {
        const keys = Object.keys(DIAGRAM_TEMPLATES);
        // Also add text templates
        const allKeys = ['Architecture', 'Roadmap', 'Research', ...keys];
        const sel = prompt('Template: ' + allKeys.join(', '), allKeys[0]);
        if (!sel) return;
        // Check mermaid diagram templates first
        const diagramKey = keys.find(k => k.toLowerCase() === sel.trim().toLowerCase());
        if (diagramKey) {
            if (csText.value.trim() && !confirm('Replace current content with template?')) return;
            csText.value = wrapMermaidCode(DIAGRAM_TEMPLATES[diagramKey]);
            csQueueAutosave();
            csText.focus();
            return;
        }
        // Text templates (reuse from SCHEMA_TEMPLATES in project.js if available)
        const textTemplates = {
            Architecture: '# Architecture\n\n- Goal\n- Core modules\n  - Module A\n  - Module B\n- Dependencies\n- Open risks',
            Roadmap: '# Roadmap\n\n- Phase 1 (MVP)\n  - Task 1\n  - Task 2\n- Phase 2\n  - Task 3\n- Future',
            Research: '# Research\n\n- Hypothesis\n- Key findings\n  - Finding 1\n  - Finding 2\n- Open questions\n- References'
        };
        const textKey = Object.keys(textTemplates).find(k => k.toLowerCase() === sel.trim().toLowerCase());
        if (textKey) {
            if (csText.value.trim() && !confirm('Replace current content with template?')) return;
            csText.value = textTemplates[textKey];
            csQueueAutosave();
            csText.focus();
            return;
        }
        alert('Template not found');
    });

    csDeleteBtn.addEventListener('click', () => {
        csFlushAutosave();
        const name = csSelect.value;
        if (!name) return;
        const bucket = getConceptSchemaBucket(csActiveConceptId);
        if (Object.keys(bucket).length <= 1) {
            alert('At least one schema must remain');
            return;
        }
        if (!confirm('Delete schema "' + name + '"?')) return;
        delete bucket[name];
        saveConceptSchemaStore();
        deactivateCsDiagram();
        csRefreshSelectors();
    });

    csExportBtn.addEventListener('click', () => {
        csFlushAutosave();
        const name = csSelect.value;
        if (!name) return;
        const bucket = getConceptSchemaBucket(csActiveConceptId);
        const schema = bucket[name];
        const md = [
            '# ' + csActiveConceptName + ' \u00B7 ' + name,
            '',
            'Exported: ' + new Date().toLocaleString(),
            '',
            schema?.content || ''
        ].join('\n');
        const blob = new Blob([md], { type: 'text/markdown;charset=utf-8' });
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = (csActiveConceptName + '-' + name + '.md').replace(/\s+/g, '_');
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(a.href);
    });
})();

// ===============================
// SPREADSHEET HELPERS
// ===============================

function parseBlockToRows(text) {
    const lines = text.split('\n').filter(l => l.trim());
    if (lines.length === 0) return [['']]; 

    // Detect markdown table (lines with |)
    const mdTableLines = lines.filter(l => l.includes('|'));
    if (mdTableLines.length >= 2) {
        return mdTableLines
            .filter(l => !/^[\s|:-]+$/.test(l)) // remove separator rows like |---|---|
            .map(l => l.split('|').map(c => c.trim()).filter((_, i, arr) => !(i === 0 && arr[0] === '') && !(i === arr.length - 1 && arr[arr.length - 1] === '')));
    }

    // Detect tab-separated
    if (lines.some(l => l.includes('\t'))) {
        return lines.map(l => l.split('\t'));
    }

    // Detect comma-separated
    if (lines.some(l => l.includes(','))) {
        return lines.map(l => l.split(',').map(c => c.trim()));
    }

    // Fallback: each line is one row, one column
    return lines.map(l => [l]);
}

function buildSpreadsheetHtml(rows) {
    // Normalize column count
    const maxCols = Math.max(...rows.map(r => r.length), 1);
    let html = '<table class="spreadsheet-table">';
    rows.forEach((row, ri) => {
        html += '<tr>';
        for (let ci = 0; ci < maxCols; ci++) {
            const raw = row[ci] || '';
            const cls = ri === 0 ? 'spreadsheet-cell spreadsheet-header' : 'spreadsheet-cell';
            // Detect dropdown syntax: {opt1/opt2/opt3}selected  or  {opt1/opt2/opt3}
            const ddMatch = raw.match(/^\{([^}]+)\}(.*)$/);
            if (ddMatch) {
                const options = ddMatch[1].split('/');
                const selected = ddMatch[2].trim();
                const opts = options.map(o => {
                    const ot = o.trim();
                    return `<option value="${ot}"${ot === selected ? ' selected' : ''}>${ot}</option>`;
                }).join('');
                html += `<td class="${cls} spreadsheet-dropdown-cell"><select class="spreadsheet-select">${opts}</select></td>`;
            } else {
                const val = raw.replace(/</g, '&lt;').replace(/>/g, '&gt;');
                html += `<td class="${cls}" contenteditable="true">${val}</td>`;
            }
        }
        html += '</tr>';
    });
    html += '</table>';
    html += '<div class="spreadsheet-actions">';
    html += '<button type="button" class="spreadsheet-add-row">+ Row</button>';
    html += '<button type="button" class="spreadsheet-add-col">+ Col</button>';
    html += '</div>';
    return html;
}

function spreadsheetToText(table) {
    const rows = [];
    for (const tr of table.rows) {
        const cells = [];
        for (const td of tr.cells) {
            const sel = td.querySelector('select');
            if (sel) {
                // Persist dropdown definition + selected value
                const opts = Array.from(sel.options).map(o => o.value).join('/');
                cells.push(`{${opts}}${sel.value}`);
            } else {
                cells.push(td.textContent.trim());
            }
        }
        rows.push(cells);
    }
    if (rows.length === 0) return '';

    // Build markdown table
    const header = '| ' + rows[0].join(' | ') + ' |';
    const sep = '| ' + rows[0].map(() => '---').join(' | ') + ' |';
    const body = rows.slice(1).map(r => '| ' + r.join(' | ') + ' |').join('\n');
    return header + '\n' + sep + '\n' + body;
}

// ---- Spreadsheet context menu (right-click → Set dropdown) ----
let _spreadsheetCtxTarget = null;

function initSpreadsheetContextMenu(container) {
    // Remove previous menu if any
    let menu = container.querySelector('.spreadsheet-ctx-menu');
    if (!menu) {
        menu = document.createElement('div');
        menu.className = 'spreadsheet-ctx-menu';
        menu.innerHTML = `
          <button class="ctx-set-dropdown">Set dropdown…</button>
          <button class="ctx-remove-dropdown">Remove dropdown</button>`;
        container.appendChild(menu);
    }

    // Right-click on cell
    container.querySelector('table').addEventListener('contextmenu', (e) => {
        const td = e.target.closest('td');
        if (!td) return;
        e.preventDefault();
        _spreadsheetCtxTarget = td;
        const rect = container.getBoundingClientRect();
        menu.style.display = 'block';
        menu.style.left = (e.clientX - rect.left) + 'px';
        menu.style.top = (e.clientY - rect.top) + 'px';
    });

    // Hide on click outside
    document.addEventListener('click', () => {
        menu.style.display = 'none';
    }, { capture: true });

    // Set dropdown
    menu.querySelector('.ctx-set-dropdown').addEventListener('click', () => {
        menu.style.display = 'none';
        if (!_spreadsheetCtxTarget) return;
        const current = _spreadsheetCtxTarget.querySelector('select');
        const existing = current
            ? Array.from(current.options).map(o => o.value).join(', ')
            : '';
        const input = prompt('Dropdown values (comma-separated):', existing);
        if (input === null) return;
        const values = input.split(',').map(v => v.trim()).filter(Boolean);
        if (values.length === 0) return;
        convertCellToDropdown(_spreadsheetCtxTarget, values);
    });

    // Remove dropdown
    menu.querySelector('.ctx-remove-dropdown').addEventListener('click', () => {
        menu.style.display = 'none';
        if (!_spreadsheetCtxTarget) return;
        const sel = _spreadsheetCtxTarget.querySelector('select');
        if (sel) {
            const val = sel.value;
            _spreadsheetCtxTarget.innerHTML = '';
            _spreadsheetCtxTarget.textContent = val;
            _spreadsheetCtxTarget.contentEditable = 'true';
            _spreadsheetCtxTarget.classList.remove('spreadsheet-dropdown-cell');
        }
    });
}

function convertCellToDropdown(td, values, selected) {
    td.contentEditable = 'false';
    td.classList.add('spreadsheet-dropdown-cell');
    const sel = document.createElement('select');
    sel.className = 'spreadsheet-select';
    values.forEach(v => {
        const opt = document.createElement('option');
        opt.value = v;
        opt.textContent = v;
        if (v === selected) opt.selected = true;
        sel.appendChild(opt);
    });
    td.innerHTML = '';
    td.appendChild(sel);
}

// ===============================
// RENDER: KNOWLEDGE VIEWER
// ===============================
// ...existing code...

function contentToHtml(text) {
  if (!text) return '';
  // Strip all post-it note prefixes before rendering
  text = text.replace(/^(:::note\n[\s\S]*?\n:::\n?)+/, '');

  // 0. Check if the entire block is a Mermaid diagram
  const mermaidMatch = text.match(/^\s*```mermaid\s*\n([\s\S]*?)\n\s*```\s*$/);
  if (mermaidMatch) {
    const code = mermaidMatch[1].trim();
    const id = 'mermaid-view-' + (++_diagramIdCounter);
    // Return a placeholder div; we'll render it async after insertion
    return `<div class="mermaid-render" data-mermaid-code="${code.replace(/"/g, '&quot;')}">\n<pre class="mermaid-loading">Loading diagram\u2026</pre></div>`;
  }

  // 0b. Protect ```chart blocks before markdown parsing
  const chartChunks = [];
  const chartPH = (i) => `CHARTPLACEHOLDER${i}ENDCHART`;
  text = text.replace(/```chart\n([\s\S]*?)\n```/g, (_, json) => {
    chartChunks.push(json);
    return chartPH(chartChunks.length - 1);
  });

  // 1. Extract and protect math blocks before markdown parsing
  const mathChunks = [];
  const placeholder = (i) => `MATHPLACEHOLDER${i}ENDMATH`;

  // Protect $$...$$ (display) first, then $...$ (inline)
  let protected_ = text
    .replace(/\$\$([\s\S]+?)\$\$/g, (_, inner) => {
      mathChunks.push({ display: true, inner });
      return placeholder(mathChunks.length - 1);
    })
    .replace(/\$([^$\n]+?)\$/g, (_, inner) => {
      mathChunks.push({ display: false, inner });
      return placeholder(mathChunks.length - 1);
    });

  // 2. Parse markdown
  let html;
  if (typeof marked !== 'undefined') {
    html = marked.parse(protected_, { breaks: true, gfm: true });
  } else {
    // Fallback if marked not loaded yet
    html = protected_.replace(/\n/g, '<br>');
  }

  // 3. Restore math expressions
  mathChunks.forEach((chunk, i) => {
    const delim = chunk.display ? '$$' : '$';
    html = html.replace(placeholder(i), `${delim}${chunk.inner}${delim}`);
  });

  // 4. Restore chart blocks as placeholder divs (rendered later by renderCharts)
  chartChunks.forEach((json, i) => {
    const safe = json.replace(/&/g, '&amp;').replace(/"/g, '&quot;');
    const div = `<div class="chart-render" data-chart-json="${safe}"></div>`;
    const ph = chartPH(i);
    html = html.replace(`<p>${ph}</p>`, div);
    html = html.replace(ph, div);
  });

  return html;
}

async function renderKnowledge(blocks) {
  const viewer = document.getElementById('knowledgeViewer');
  viewer.innerHTML = '';
    
  if (blocks.length === 0) {
    viewer.innerHTML = '<p>No blocks available for this concept.</p>';
    return;
  }

  for (const block of blocks) {
      const blockDiv = document.createElement('div');
      blockDiv.className = `knowledge-block ${block.block_type}`;
      blockDiv.dataset.blockId = block.id;
      blockDiv.dataset.blockType = block.block_type;

      const header = document.createElement('div');
      header.className = 'block-header';

      let headerHtml = '';
      // Mostrar etiqueta de tipo de bloque solo si está en modo modificar
      if (isModifyingContents) {
        headerHtml += `<strong>${block.block_type.toUpperCase()}</strong>`;
      }
      // Mostrar etiqueta de modo solo si está en modo modificar y hay modo
      if (isModifyingContents && block.mode) {
        headerHtml += ` <span class="mode-badge">${block.mode}</span>`;
      }
      // Mostrar botones solo si está en modo modificar
      if (isModifyingContents) {
        headerHtml += ` <button class="edit-btn" data-block-id="${block.id}">Edit</button>`;
        headerHtml += ` <button class="delete-btn" data-block-id="${block.id}">Delete</button>`;
      }
      header.innerHTML = headerHtml;

      const content = document.createElement('div');
      content.className = 'block-content';
      const _blockParsed = parseNotes(block.content);
      content.innerHTML = contentToHtml(await resolveB2Images(_blockParsed.body));
      content.dataset.originalContent = block.content;

      blockDiv.appendChild(header);
      blockDiv.appendChild(content);
      viewer.appendChild(blockDiv);
      _blockParsed.notes.forEach((n, i) => addPostItToBlock(blockDiv, n, block.id, i));
  }

  renderMathInElement(viewer, {
    delimiters: [
      {left: '$$', right: '$$', display: true},
      {left: '$', right: '$', display: false}
    ],
    throwOnError: false,
    strict: false
  });

  // Render Mermaid diagrams in view mode
  viewer.querySelectorAll('.mermaid-render').forEach(async (div) => {
    const code = div.dataset.mermaidCode;
    if (code && typeof mermaid !== 'undefined') {
      try {
        const id = 'mermaid-view-' + (++_diagramIdCounter);
        const { svg } = await mermaid.render(id, code);
        div.innerHTML = svg;
      } catch (err) {
        div.innerHTML = `<pre class="diagram-error">\u26a0 Diagram error: ${err.message || 'Unknown'}</pre>`;
      }
    }
  });

  // Render Plotly charts in view mode
  renderCharts(viewer);

  if (isModifyingContents) {
    document.querySelectorAll('.edit-btn').forEach(btn => {
      btn.addEventListener('click', handleEditClick);
    });
    document.querySelectorAll('.delete-btn').forEach(btn => {
      btn.addEventListener('click', handleDeleteClick);
    });

    // Image resize: click on any img inside a block while in modify mode
    document.querySelectorAll('.block-content img').forEach(img => {
      img.style.cursor = 'pointer';
      img.title = 'Click to resize';
      img.addEventListener('click', handleImageResizeClick);
    });
  }
}

function handleImageResizeClick(e) {
  e.stopPropagation();
  const img = e.currentTarget;

  // Remove any existing popover
  document.querySelectorAll('.img-resize-popover').forEach(p => p.remove());

  const currentWidth = img.getAttribute('width') || img.getAttribute('style')?.match(/width:\s*([^;]+)/)?.[1] || '';

  const popover = document.createElement('div');
  popover.className = 'img-resize-popover';
  popover.innerHTML = `
    <span class="img-resize-label">Width</span>
    <input class="img-resize-input" type="text" value="${currentWidth}" placeholder="e.g. 400px, 60%">
    <button class="img-resize-apply">Apply</button>
    <button class="img-resize-cancel">✕</button>
  `;

  // Position below the image
  const rect = img.getBoundingClientRect();
  popover.style.cssText = `position:fixed;top:${rect.bottom + 6}px;left:${rect.left}px;z-index:9999`;
  document.body.appendChild(popover);

  const input = popover.querySelector('.img-resize-input');
  input.focus();
  input.select();

  const applyResize = async () => {
    const val = input.value.trim();
    popover.remove();

    // Update the img element
    img.removeAttribute('width');
    img.style.width = '';
    if (val) img.setAttribute('width', val);

    // Update the source text in dataset.originalContent
    const contentDiv = img.closest('.block-content');
    const blockDiv = img.closest('[data-block-id]');
    if (!contentDiv || !blockDiv) return;

    const blockId = blockDiv.dataset.blockId;
    const src = img.getAttribute('src') || img.src;
    let text = contentDiv.dataset.originalContent || '';

    // Replace both <img ...> and ![alt](src) patterns for this image src
    // Match <img ... src="src" ...> with or without width attr
    text = text.replace(/<img([^>]*?)src="[^"]*?"([^>]*?)>/g, (match, pre, post) => {
      if (!match.includes(src.split('?')[0].split('/').pop())) return match;
      let attrs = (pre + post).replace(/\s*width="[^"]*"/, '').trim();
      return val ? `<img${attrs ? ' ' + attrs : ''} src="${src}" width="${val}">` : `<img${attrs ? ' ' + attrs : ''} src="${src}">`;
    });
    // Match ![alt](src) markdown pattern
    text = text.replace(/!\[([^\]]*)\]\(([^)]+)\)/g, (match, alt, msrc) => {
      if (!msrc.includes(src.split('?')[0].split('/').pop())) return match;
      return val ? `<img src="${msrc}" alt="${alt}" width="${val}">` : match;
    });

    contentDiv.dataset.originalContent = text;

    // Persist to backend
    try {
      const typeSelect = blockDiv.querySelector('.block-type-editor');
      const blockType = typeSelect ? typeSelect.value : (blockDiv.dataset.blockType || null);
      await fetch(`${KNOWLEDGE_API_BASE}/knowledge/block/${blockId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content: text, block_type: blockType })
      });
    } catch (err) {
      console.error('Failed to save image resize:', err);
    }
  };

  popover.querySelector('.img-resize-apply').addEventListener('click', applyResize);
  popover.querySelector('.img-resize-cancel').addEventListener('click', () => popover.remove());
  input.addEventListener('keydown', e => {
    if (e.key === 'Enter') applyResize();
    if (e.key === 'Escape') popover.remove();
  });

  // Close if clicking outside
  setTimeout(() => {
    document.addEventListener('click', function handler(ev) {
      if (!popover.contains(ev.target)) {
        popover.remove();
        document.removeEventListener('click', handler);
      }
    });
  }, 0);
}

// ===============================
// POST-IT NOTES
// ===============================

function parseNotes(content) {
  if (!content) return { notes: [], body: '' };
  const notes = [];
  let rest = content;
  const pat = /^:::note\n([\s\S]*?)\n:::\n?/;
  let m;
  while ((m = rest.match(pat))) { notes.push(m[1]); rest = rest.slice(m[0].length); }
  return { notes, body: rest };
}

function buildNotesContent(notes, body) {
  const valid = (notes || []).filter(n => n.trim());
  if (!valid.length) return body;
  return valid.map(n => `:::note\n${n.trim()}\n:::`).join('\n') + '\n' + body;
}

function makeDraggable(el, container, storageKey) {
  const handle = el.querySelector('.postit-handle') || el;
  let startX, startY, startLeft, startTop;
  handle.addEventListener('mousedown', e => {
    if (e.target.closest('button, textarea')) return;
    e.preventDefault();
    const cRect = container.getBoundingClientRect();
    const eRect = el.getBoundingClientRect();
    startX = e.clientX; startY = e.clientY;
    startLeft = eRect.left - cRect.left;
    startTop  = eRect.top  - cRect.top;
    const onMove = e => {
      el.style.left = (startLeft + e.clientX - startX) + 'px';
      el.style.top  = (startTop  + e.clientY - startY) + 'px';
    };
    const onUp = () => {
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
      if (storageKey) localStorage.setItem(storageKey, JSON.stringify({ top: el.style.top, left: el.style.left }));
    };
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
  });
}

function addPostItToBlock(blockDiv, noteText, blockId, index) {
  const postit = document.createElement('div');
  postit.className = 'block-postit';
  postit.dataset.blockId = blockId;
  postit.dataset.noteIndex = index;
  const safeText = contentToHtml(noteText);
  const minKey = `postit-min-${blockId}-${index}`;
  const isMinimized = localStorage.getItem(minKey) === '1';
  postit.innerHTML = `
    <div class="postit-handle">📌 <span class="postit-hint">drag</span><button class="postit-toggle" title="Minimize">${isMinimized ? '▲' : '▼'}</button></div>
    <div class="postit-text" style="display:${isMinimized ? 'none' : ''}">${safeText}</div>
    ${isModifyingContents ? '<button class="postit-delete" title="Delete note">×</button>' : ''}
  `;
  postit.classList.toggle('postit-minimized', isMinimized);
  const posKey = `postit-pos-${blockId}-${index}`;
  const saved = JSON.parse(localStorage.getItem(posKey) || 'null');
  if (saved) {
    postit.style.top = saved.top;
    postit.style.left = saved.left;
  } else if (index > 0) {
    postit.style.top = '-10px';
    postit.style.left = (20 + index * 170) + 'px';
  }
  blockDiv.appendChild(postit);
  makeDraggable(postit, blockDiv, posKey);

  // Minimize toggle
  postit.querySelector('.postit-toggle').addEventListener('click', e => {
    e.stopPropagation();
    const textEl = postit.querySelector('.postit-text');
    const btn = postit.querySelector('.postit-toggle');
    const minimized = textEl.style.display === 'none';
    textEl.style.display = minimized ? '' : 'none';
    btn.textContent = minimized ? '▼' : '▲';
    postit.classList.toggle('postit-minimized', !minimized);
    localStorage.setItem(minKey, minimized ? '0' : '1');
  });

  // Render KaTeX inside the post-it
  const textEl = postit.querySelector('.postit-text');
  if (textEl) {
    renderMathInElement(textEl, {
      delimiters: [
        { left: '$$', right: '$$', display: true },
        { left: '$', right: '$', display: false }
      ],
      throwOnError: false,
      strict: false
    });
  }
  if (isModifyingContents) {
    postit.querySelector('.postit-delete').addEventListener('click', async e => {
      e.stopPropagation();
      if (!confirm('Delete this post-it note?')) return;
      const contentDiv = blockDiv.querySelector('.block-content');
      const { notes: _all, body } = parseNotes(contentDiv?.dataset.originalContent || '');
      const noteIdx = parseInt(postit.dataset.noteIndex);
      const updatedContent = buildNotesContent(_all.filter((_, i) => i !== noteIdx), body);
      try {
        await fetch(`${KNOWLEDGE_API_BASE}/knowledge/block/${blockId}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ content: updatedContent, block_type: blockDiv.dataset.blockType || null })
        });
        if (contentDiv) contentDiv.dataset.originalContent = updatedContent;
        postit.remove();
        blockDiv.querySelectorAll('.block-postit').forEach((p, i) => { p.dataset.noteIndex = i; });
        localStorage.removeItem(posKey);
      } catch(err) { console.error('Failed to delete note:', err); }
    });
  }
}

async function handleEditClick(event) {
    const blockId = event.target.dataset.blockId;
    const blockDiv = document.querySelector(`[data-block-id="${blockId}"]`);
    const contentDiv = blockDiv.querySelector('.block-content');
    const editBtn = event.target;
    
    if (editBtn.textContent === 'Save') {
        // GUARDAR
        // If spreadsheet is active, sync its data back to the textarea first
        const spreadsheetGrid = contentDiv.querySelector('.spreadsheet-container');
        const textarea = contentDiv.querySelector('textarea');
        if (spreadsheetGrid) {
            textarea.value = spreadsheetToText(spreadsheetGrid.querySelector('table'));
        }
        // If diagram is active, sync its code back to the textarea
        const diagramContainer = contentDiv.querySelector('.diagram-container');
        if (diagramContainer) {
            const codeArea = diagramContainer.querySelector('.diagram-code');
            textarea.value = wrapMermaidCode(codeArea.value);
        }
        const _noteEls = contentDiv.querySelectorAll('.postit-note-editor');
        const newContent = buildNotesContent(Array.from(_noteEls).map(el => el.value), textarea.value);
        const typeSelect = contentDiv.querySelector('.block-type-editor');
        const newType = typeSelect ? typeSelect.value : (blockDiv.dataset.blockType || null);
        
        try {
            const response = await fetch(`${KNOWLEDGE_API_BASE}/knowledge/block/${blockId}`, {
                method: 'PUT',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ content: newContent, block_type: newType })
            });
            
            if (!response.ok) throw new Error('Failed to update');
            
            // ✅ Guardar el nuevo contenido original
            contentDiv.dataset.originalContent = newContent;
            if (newType) {
              blockDiv.dataset.blockType = newType;
              blockDiv.className = `knowledge-block ${newType}`;
            }
            
            // Renderizar el HTML
            contentDiv.innerHTML = contentToHtml(await resolveB2Images(newContent));
            editBtn.textContent = 'Edit';

            // Re-render post-its
            blockDiv.querySelectorAll('.block-postit').forEach(p => p.remove());
            parseNotes(newContent).notes.forEach((n, i) => addPostItToBlock(blockDiv, n, blockId, i));

            // Renderizar KaTeX
            renderMathInElement(contentDiv, {
                delimiters: [
                    {left: '$$', right: '$$', display: true},
                    {left: '$', right: '$', display: false}
                ],
                throwOnError: false,
                strict: false
            });

            // Render Mermaid diagrams after save
            contentDiv.querySelectorAll('.mermaid-render').forEach(async (div) => {
                const code = div.dataset.mermaidCode;
                if (code && typeof mermaid !== 'undefined') {
                    try {
                        const id = 'mermaid-view-' + (++_diagramIdCounter);
                        const { svg } = await mermaid.render(id, code);
                        div.innerHTML = svg;
                    } catch (err) {
                        div.innerHTML = `<pre class="diagram-error">\u26a0 ${err.message}</pre>`;
                    }
                }
            });

            // Render Plotly charts after save
            renderCharts(contentDiv);
            
        } catch (error) {
            console.error('Error updating block:', error);
            alert('Error al guardar los cambios');
        }
        
    } else {
        // EDITAR
        // ✅ Usar el contenido original (con $ y $$), no el renderizado
        const originalContent = contentDiv.dataset.originalContent || contentDiv.innerHTML;
        const { notes: _editNotes, body: _editBody } = parseNotes(originalContent);
        const currentType = blockDiv.dataset.blockType || 'definition';

        const BLOCK_TYPES = ['definition','intuition','formula','example','proof','theorem','remark','exercise','summary'];
        const typeOptions = BLOCK_TYPES.map(t =>
          `<option value="${t}" ${t === currentType ? 'selected' : ''}>${t}</option>`
        ).join('');
        
        // Remove post-it overlays while editing (shown as textareas below)
        blockDiv.querySelectorAll('.block-postit').forEach(p => p.remove());

        const _notesHtml = _editNotes.map(n =>
          `<textarea class="postit-note-editor">${n}</textarea>`
        ).join('');
        contentDiv.innerHTML = `
          <select class="block-type-editor">${typeOptions}</select>
          <textarea class="block-editor">${_editBody}</textarea>
          <div class="block-editor-toolbar">
            <label class="btn-upload-img" title="Upload image to B2">
              📎 Upload image
              <input type="file" accept="image/*,video/*,application/pdf" style="display:none">
            </label>
            <button type="button" class="btn-toggle-spreadsheet" title="Toggle spreadsheet view">📊 Spreadsheet</button>
            <button type="button" class="btn-toggle-diagram" title="Toggle diagram editor">🔀 Diagram</button>
            <button type="button" class="btn-insert-chart" title="Insert Plotly chart template">📈 Chart</button>
            <span class="upload-status"></span>
          </div>
          <div class="postit-notes-editor-list">${_notesHtml}</div>
          <button type="button" class="btn-add-postit">📌 Add post-it</button>`;
        editBtn.textContent = 'Save';

        contentDiv.querySelector('.btn-add-postit').addEventListener('click', () => {
          const list = contentDiv.querySelector('.postit-notes-editor-list');
          const ta = document.createElement('textarea');
          ta.className = 'postit-note-editor';
          ta.placeholder = 'Write post-it note…';
          list.appendChild(ta);
          ta.focus();
        });

        // Insert chart template
        const chartBtn = contentDiv.querySelector('.btn-insert-chart');
        chartBtn.addEventListener('click', () => {
          const ta = contentDiv.querySelector('.block-editor');
          const template = [
            '',
            '```chart',
            '{',
            '  "data": [',
            '    {',
            '      "x": [0, 1, 2, 3, 4, 5],',
            '      "y": [10, 8, 6, 4, 2, 0],',
            '      "name": "Demand",',
            '      "type": "scatter",',
            '      "mode": "lines"',
            '    },',
            '    {',
            '      "x": [0, 1, 2, 3, 4, 5],',
            '      "y": [0, 2, 4, 6, 8, 10],',
            '      "name": "Supply",',
            '      "type": "scatter",',
            '      "mode": "lines"',
            '    }',
            '  ],',
            '  "layout": {',
            '    "title": "Supply & Demand",',
            '    "xaxis": { "title": "Quantity" },',
            '    "yaxis": { "title": "Price" }',
            '  }',
            '}',
            '```',
            ''
          ].join('\n');
          const start = ta.selectionStart;
          ta.value = ta.value.slice(0, start) + template + ta.value.slice(ta.selectionEnd);
          ta.selectionStart = ta.selectionEnd = start + template.length;
          ta.dispatchEvent(new Event('input'));
          ta.focus();
        });

        const textarea = contentDiv.querySelector('textarea');
        textarea.focus();

        // ✅ Auto-resize del textarea
        textarea.style.height = 'auto';
        textarea.style.height = textarea.scrollHeight + 'px';
        textarea.addEventListener('input', () => {
            textarea.style.height = 'auto';
            textarea.style.height = textarea.scrollHeight + 'px';
        });

        // Toggle spreadsheet view handler
        const spreadsheetBtn = contentDiv.querySelector('.btn-toggle-spreadsheet');
        let spreadsheetActive = false;
        spreadsheetBtn.addEventListener('click', () => {
            const textarea = contentDiv.querySelector('textarea');

            if (!spreadsheetActive) {
                // Switch to spreadsheet mode
                const text = textarea.value.trim();
                const rows = parseBlockToRows(text);
                const gridHtml = buildSpreadsheetHtml(rows);
                textarea.style.display = 'none';
                const container = document.createElement('div');
                container.className = 'spreadsheet-container';
                container.innerHTML = gridHtml;
                textarea.parentNode.insertBefore(container, textarea.nextSibling);
                spreadsheetBtn.textContent = 'Textarea';
                spreadsheetActive = true;

                // Init context menu for dropdown columns
                initSpreadsheetContextMenu(container);

                // Add row / add col buttons
                container.querySelector('.spreadsheet-add-row').addEventListener('click', () => {
                    const table = container.querySelector('table');
                    const colCount = table.rows[0] ? table.rows[0].cells.length : 1;
                    const lastRow = table.rows[table.rows.length - 1];
                    const tr = table.insertRow();
                    for (let c = 0; c < colCount; c++) {
                        const td = tr.insertCell();
                        td.className = 'spreadsheet-cell';
                        // Inherit dropdown from column above
                        const refCell = lastRow ? lastRow.cells[c] : null;
                        const refSel = refCell ? refCell.querySelector('select') : null;
                        if (refSel) {
                            const opts = Array.from(refSel.options).map(o => o.value);
                            convertCellToDropdown(td, opts);
                        } else {
                            td.contentEditable = 'true';
                        }
                    }
                });
                container.querySelector('.spreadsheet-add-col').addEventListener('click', () => {
                    const table = container.querySelector('table');
                    for (const row of table.rows) {
                        const td = row.insertCell();
                        td.contentEditable = 'true';
                        td.className = row.rowIndex === 0 ? 'spreadsheet-cell spreadsheet-header' : 'spreadsheet-cell';
                    }
                });
            } else {
                // Switch back to textarea mode
                const grid = contentDiv.querySelector('.spreadsheet-container');
                textarea.value = spreadsheetToText(grid.querySelector('table'));
                textarea.style.display = '';
                textarea.style.height = 'auto';
                textarea.style.height = textarea.scrollHeight + 'px';
                grid.remove();
                spreadsheetBtn.textContent = 'Spreadsheet';
                spreadsheetActive = false;
            }
        });

        // Toggle diagram editor handler
        const diagramBtn = contentDiv.querySelector('.btn-toggle-diagram');
        let diagramActive = false;
        diagramBtn.addEventListener('click', () => {
            const textarea = contentDiv.querySelector('textarea');

            if (!diagramActive) {
                // If spreadsheet is active, close it first
                if (spreadsheetActive) {
                    const grid = contentDiv.querySelector('.spreadsheet-container');
                    if (grid) {
                        textarea.value = spreadsheetToText(grid.querySelector('table'));
                        grid.remove();
                    }
                    textarea.style.display = '';
                    spreadsheetBtn.textContent = 'Spreadsheet';
                    spreadsheetActive = false;
                }

                // Extract mermaid code from textarea or start fresh
                const mermaidCode = extractMermaidCode(textarea.value.trim());
                textarea.style.display = 'none';

                const container = document.createElement('div');
                container.className = 'diagram-container';
                container.innerHTML = buildDiagramEditor(mermaidCode);
                textarea.parentNode.insertBefore(container, textarea.nextSibling);

                initDiagramEditor(container);
                diagramBtn.textContent = 'Textarea';
                diagramActive = true;
            } else {
                // Sync diagram code back to textarea
                const container = contentDiv.querySelector('.diagram-container');
                const codeArea = container.querySelector('.diagram-code');
                textarea.value = wrapMermaidCode(codeArea.value);
                textarea.style.display = '';
                textarea.style.height = 'auto';
                textarea.style.height = textarea.scrollHeight + 'px';
                container.remove();
                diagramBtn.textContent = 'Diagram';
                diagramActive = false;
            }
        });

        const fileInput = contentDiv.querySelector('input[type=file]');
        const statusEl = contentDiv.querySelector('.upload-status');
        fileInput.addEventListener('change', async () => {
            const file = fileInput.files[0];
            if (!file) return;
            if (!knowledgeState.concept_id) {
                statusEl.textContent = 'Select a concept first';
                return;
            }
            statusEl.textContent = 'Uploading…';
            try {
                const formData = new FormData();
                formData.append('file', file);
                formData.append('concept_id', knowledgeState.concept_id);
                formData.append('block_id', blockId);
                const res = await fetch(`${KNOWLEDGE_API_BASE}/media/upload`, {
                    method: 'POST',
                    body: formData,
                });
                if (!res.ok) throw new Error(await res.text());
                const { b2_ref, path } = await res.json();
                const filename = path.split('/').pop();

                // Ask for optional width
                const widthInput = prompt('Image width (e.g. 400px, 50%, leave blank for original):', '');
                const widthAttr = widthInput && widthInput.trim() ? ` width="${widthInput.trim()}"` : '';
                const md = widthAttr
                  ? `<img src="${b2_ref}" alt="${filename}"${widthAttr}>`
                  : `![${filename}](${b2_ref})`;

                // Insert at cursor position
                const start = textarea.selectionStart;
                const end = textarea.selectionEnd;
                textarea.value = textarea.value.slice(0, start) + md + textarea.value.slice(end);
                textarea.selectionStart = textarea.selectionEnd = start + md.length;
                textarea.dispatchEvent(new Event('input'));
                textarea.focus();
                statusEl.textContent = '✓ Inserted';
                setTimeout(() => { statusEl.textContent = ''; }, 3000);
            } catch (err) {
                console.error(err);
                statusEl.textContent = '✖ Upload failed';
            }
            fileInput.value = '';
        });
    }
}

// ===============================
// RELATIONS MODAL
// ===============================

let relAllConcepts = [];
let relAllProjects = [];
let relAllBlocks = [];
let relShowBlocks = false;
let relSelectedConcept = null;
let relSelectedBlock = null;

document.getElementById('modifyBlockBtn').addEventListener('click', openRelationsModal);

async function openRelationsModal() {
  const modal = document.getElementById('relationsModal');
  modal.style.display = 'flex';
  document.body.style.overflow = 'hidden';

  const relProjectFilter = document.getElementById('relProjectFilter');
  if (relAllProjects.length === 0) {
    const res = await fetch(`${KNOWLEDGE_API_BASE}/knowledge/projects`);
    relAllProjects = await res.json();
  }
  relProjectFilter.innerHTML = `<option value="">All projects</option>`;
  relAllProjects.forEach(p => {
    const o = document.createElement('option');
    o.value = p.id;
    o.textContent = p.name;
    relProjectFilter.appendChild(o);
  });
  if (knowledgeState.project_id) relProjectFilter.value = knowledgeState.project_id;
  relProjectFilter.onchange = () => loadRelData(relProjectFilter.value || null);

  // Sync toggle state
  document.getElementById('relShowBlocksToggle').checked = relShowBlocks;

  await loadRelData(knowledgeState.project_id);
}

async function loadRelData(projectId) {
  let url = `${KNOWLEDGE_API_BASE}/knowledge/concepts`;
  if (projectId) url += `?project_id=${projectId}`;
  const res = await fetch(url);
  relAllConcepts = await res.json();

  if (relShowBlocks) {
    await loadRelBlocks(projectId);
  } else {
    relAllBlocks = [];
  }

  relSelectedConcept = null;
  relSelectedBlock = null;
  renderRelEditPanel(null);
  renderRelTree();
}

async function loadRelBlocks(projectId) {
  let url = `${KNOWLEDGE_API_BASE}/knowledge/blocks`;
  if (projectId) url += `?project_id=${projectId}`;
  const res = await fetch(url);
  if (res.ok) relAllBlocks = await res.json();
  else relAllBlocks = [];
}

document.getElementById('relShowBlocksToggle').addEventListener('change', async (e) => {
  relShowBlocks = e.target.checked;
  const projectId = document.getElementById('relProjectFilter').value || null;
  if (relShowBlocks && relAllConcepts.length > 0) {
    await loadRelBlocks(projectId);
  } else {
    relAllBlocks = [];
  }
  relSelectedBlock = null;
  renderRelTree();
});

function renderRelTree() {
  const container = document.getElementById('relConceptTree');
  container.innerHTML = '';

  const byParent = {};
  relAllConcepts.forEach(c => {
    const key = c.parent_concept_id ?? 'root';
    byParent[key] ||= [];
    byParent[key].push(c);
  });
  const blocksByConcept = {};
  relAllBlocks.forEach(b => {
    blocksByConcept[b.concept_id] ||= [];
    blocksByConcept[b.concept_id].push(b);
  });

  function makeIndent(isLastArr, addCorner) {
    const indentDiv = document.createElement('div');
    indentDiv.classList.add('rel-node-indent');
    isLastArr.forEach(parentIsLast => {
      const line = document.createElement('span');
      if (parentIsLast) {
        line.style.cssText = 'display:inline-block;width:16px;flex-shrink:0';
      } else {
        line.classList.add('rel-line-v');
      }
      indentDiv.appendChild(line);
    });
    if (addCorner) {
      const corner = document.createElement('span');
      corner.classList.add('rel-line-corner');
      indentDiv.appendChild(corner);
    }
    return indentDiv;
  }

  function renderRelNode(parentId, container, depth, isLastArr) {
    const children = byParent[parentId] || [];
    children.forEach((c, idx) => {
      const conceptBlocks = relShowBlocks ? (blocksByConcept[c.id] || []) : [];
      const hasSubConcepts = (byParent[c.id] || []).length > 0;
      const isLast = idx === children.length - 1;

      const row = document.createElement('div');
      row.classList.add('rel-node-row');
      if (relSelectedConcept && relSelectedConcept.id === c.id) row.classList.add('selected');

      row.appendChild(makeIndent(isLastArr, depth > 0));

      const icon = document.createElement('span');
      icon.classList.add('rel-node-icon');
      icon.textContent = (hasSubConcepts || conceptBlocks.length > 0) ? '▶' : '●';

      const name = document.createElement('span');
      name.classList.add('rel-node-name');
      name.textContent = c.name;

      row.appendChild(icon);
      row.appendChild(name);
      row.addEventListener('click', () => {
        relSelectedConcept = c;
        relSelectedBlock = null;
        document.querySelectorAll('.rel-node-row').forEach(r => r.classList.remove('selected'));
        row.classList.add('selected');
        renderRelEditPanel(c);
      });
      container.appendChild(row);

      // Sub-concepts
      renderRelNode(c.id, container, depth + 1, [...isLastArr, isLast && conceptBlocks.length === 0]);

      // Blocks under this concept
      conceptBlocks.forEach((b, bidx) => {
        const bIsLast = bidx === conceptBlocks.length - 1;
        const blockRow = document.createElement('div');
        blockRow.classList.add('rel-node-row', 'rel-block-row');
        if (relSelectedBlock && relSelectedBlock.id === b.id) blockRow.classList.add('selected');

        blockRow.appendChild(makeIndent([...isLastArr, isLast], true));

        const bIcon = document.createElement('span');
        bIcon.classList.add('rel-node-icon', 'rel-block-icon');
        bIcon.textContent = '□';

        const bName = document.createElement('span');
        bName.classList.add('rel-node-name', 'rel-block-name');
        bName.textContent = `[${b.block_type}] ${b.content_preview || ''}`;

        blockRow.appendChild(bIcon);
        blockRow.appendChild(bName);
        blockRow.addEventListener('click', () => {
          relSelectedBlock = b;
          relSelectedConcept = null;
          document.querySelectorAll('.rel-node-row').forEach(r => r.classList.remove('selected'));
          blockRow.classList.add('selected');
          renderRelBlockEditPanel(b);
        });
        container.appendChild(blockRow);
      });
    });
  }

  renderRelNode('root', container, 0, []);
}

function renderRelEditPanel(concept) {
  const panel = document.getElementById('relEditPanel');
  if (!concept) {
    panel.innerHTML = '<div class="rel-edit-empty">← Select a concept or block to edit its relations</div>';
    return;
  }

  const currentParent = concept.parent_concept_id
    ? relAllConcepts.find(c => c.id === concept.parent_concept_id)
    : null;

  const descendants = getDescendantIds(concept.id);
  const parentOptions = relAllConcepts.filter(c => c.id !== concept.id && !descendants.has(c.id));

  let parentSelectHtml = `<option value="">— None (root) —</option>`;
  parentOptions.forEach(c => {
    const sel = c.id === concept.parent_concept_id ? 'selected' : '';
    parentSelectHtml += `<option value="${c.id}" ${sel}>${c.name}</option>`;
  });

  panel.innerHTML = `
    <div class="rel-edit-title-row">
      <span id="relEditTitleText" class="rel-edit-title">${concept.name}</span>
      <button id="relEditNameBtn" class="rel-inline-edit-btn" title="Rename">✎</button>
    </div>
    <div class="rel-edit-section">
      <div class="rel-edit-section-label">ID</div>
      <div class="rel-edit-value">#${concept.id}</div>
    </div>
    <div class="rel-edit-section">
      <div class="rel-edit-section-label">Current Parent</div>
      <div class="rel-edit-value">${currentParent ? currentParent.name : '— root —'}</div>
    </div>
    <div class="rel-edit-section">
      <div class="rel-edit-section-label">Change Parent</div>
      <select id="relParentSelect" class="rel-parent-select">${parentSelectHtml}</select>
    </div>
    <button id="relSaveParentBtn" class="rel-save-btn">Save Parent</button>
  `;

  document.getElementById('relEditNameBtn').addEventListener('click', () => {
    const titleEl = document.getElementById('relEditTitleText');
    const btn = document.getElementById('relEditNameBtn');
    const input = document.createElement('input');
    input.type = 'text';
    input.value = titleEl.textContent;
    input.className = 'rel-name-input';
    titleEl.replaceWith(input);
    btn.style.display = 'none';
    input.focus();
    input.select();

    const saveName = async () => {
      const newName = input.value.trim();
      if (!newName || newName === concept.name) {
        input.replaceWith(titleEl);
        btn.style.display = '';
        return;
      }
      try {
        const res = await fetch(`${KNOWLEDGE_API_BASE}/knowledge/concepts/${concept.id}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: newName })
        });
        if (!res.ok) throw new Error(await res.text());
        const c = relAllConcepts.find(x => x.id === concept.id);
        c.name = newName;
        relSelectedConcept = c;
        titleEl.textContent = newName;
        concept.name = newName;
        renderRelTree();
        loadConcepts();
      } catch (err) { console.error(err); alert('Error saving name'); }
      input.replaceWith(titleEl);
      btn.style.display = '';
    };

    input.addEventListener('keydown', e => {
      if (e.key === 'Enter') saveName();
      if (e.key === 'Escape') { input.replaceWith(titleEl); btn.style.display = ''; }
    });
    input.addEventListener('blur', saveName);
  });

  document.getElementById('relSaveParentBtn').addEventListener('click', async () => {
    const newParentId = document.getElementById('relParentSelect').value;
    const btn = document.getElementById('relSaveParentBtn');
    try {
      const res = await fetch(`${KNOWLEDGE_API_BASE}/knowledge/concepts/${concept.id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ parent_concept_id: newParentId ? Number(newParentId) : null })
      });
      if (!res.ok) throw new Error(await res.text());
      const c = relAllConcepts.find(x => x.id === concept.id);
      c.parent_concept_id = newParentId ? Number(newParentId) : null;
      relSelectedConcept = c;
      btn.textContent = '✓ Saved'; btn.classList.add('saved');
      setTimeout(() => { btn.textContent = 'Save Parent'; btn.classList.remove('saved'); }, 1500);
      renderRelTree();
      loadConcepts();
    } catch (err) { console.error(err); alert('Error saving parent'); }
  });

  // Load and render concept projects section
  fetch(`${KNOWLEDGE_API_BASE}/knowledge/concepts/${concept.id}/projects`)
    .then(r => r.json())
    .then(data => {
      const currentIds = new Set(data.project_ids || []);
      let projectsHtml = relAllProjects.map(p => {
        const checked = currentIds.has(p.id) ? 'checked' : '';
        return `<label class="rel-checkbox-item"><input type="checkbox" class="rel-concept-proj-cb" value="${p.id}" ${checked}> ${p.name}</label>`;
      }).join('');
      if (!projectsHtml) projectsHtml = '<span style="opacity:0.5;font-size:0.82rem">No projects available</span>';

      const projectSection = document.createElement('div');
      projectSection.innerHTML = `
        <div class="rel-edit-section">
          <div class="rel-edit-section-label">Projects</div>
          <div class="rel-checkbox-list">${projectsHtml}</div>
        </div>
        <button id="relSaveConceptProjectsBtn" class="rel-save-btn">Save Projects</button>
      `;
      document.getElementById('relEditPanel').appendChild(projectSection);

      document.getElementById('relSaveConceptProjectsBtn').addEventListener('click', async () => {
        const checked = [...document.querySelectorAll('.rel-concept-proj-cb:checked')].map(cb => Number(cb.value));
        const btn = document.getElementById('relSaveConceptProjectsBtn');
        try {
          const res = await fetch(`${KNOWLEDGE_API_BASE}/knowledge/concepts/${concept.id}/projects`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ project_ids: checked })
          });
          if (!res.ok) throw new Error(await res.text());
          btn.textContent = '✓ Saved'; btn.classList.add('saved');
          setTimeout(() => { btn.textContent = 'Save Projects'; btn.classList.remove('saved'); }, 1500);
          loadConcepts();
        } catch (err) { console.error(err); alert('Error saving projects'); }
      });
    })
    .catch(err => console.error('Error loading concept projects:', err));
}

function renderRelBlockEditPanel(block) {
  const panel = document.getElementById('relEditPanel');
  const siblingBlocks = relAllBlocks.filter(b => b.concept_id === block.concept_id && b.id !== block.id);

  let dependsHtml = `<option value="">— None —</option>`;
  siblingBlocks.forEach(b => {
    const sel = b.id === block.depends_on_block_id ? 'selected' : '';
    dependsHtml += `<option value="${b.id}" ${sel}>[${b.block_type}] ${(b.content_preview || '').slice(0, 40)}</option>`;
  });

  let projectsHtml = relAllProjects.map(p => {
    const checked = (block.project_ids || []).includes(p.id) ? 'checked' : '';
    return `<label class="rel-checkbox-item"><input type="checkbox" class="rel-proj-cb" value="${p.id}" ${checked}> ${p.name}</label>`;
  }).join('');
  if (!projectsHtml) projectsHtml = '<span style="opacity:0.5;font-size:0.82rem">No projects available</span>';

  panel.innerHTML = `
    <div class="rel-edit-title" style="color:#76c3f0;">[${block.block_type}] Block #${block.id}</div>
    <div class="rel-edit-section">
      <div class="rel-edit-section-label">Content preview</div>
      <div class="rel-edit-value" style="font-style:italic;font-size:0.8rem;opacity:0.7">${block.content_preview || '—'}</div>
    </div>
    <div class="rel-edit-section">
      <div class="rel-edit-section-label">Depends on block</div>
      <select id="relDependsSelect" class="rel-parent-select">${dependsHtml}</select>
    </div>
    <button id="relSaveDependsBtn" class="rel-save-btn">Save Dependency</button>
    <div class="rel-edit-section" style="margin-top:0.75rem">
      <div class="rel-edit-section-label">Projects</div>
      <div class="rel-checkbox-list">${projectsHtml}</div>
    </div>
    <button id="relSaveProjectsBtn" class="rel-save-btn">Save Projects</button>
  `;

  document.getElementById('relSaveDependsBtn').addEventListener('click', async () => {
    const val = document.getElementById('relDependsSelect').value;
    const btn = document.getElementById('relSaveDependsBtn');
    try {
      const res = await fetch(`${KNOWLEDGE_API_BASE}/knowledge/block/${block.id}/relations`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ depends_on_block_id: val ? Number(val) : null })
      });
      if (!res.ok) throw new Error(await res.text());
      block.depends_on_block_id = val ? Number(val) : null;
      btn.textContent = '✓ Saved'; btn.classList.add('saved');
      setTimeout(() => { btn.textContent = 'Save Dependency'; btn.classList.remove('saved'); }, 1500);
    } catch (err) { console.error(err); alert('Error saving dependency'); }
  });

  document.getElementById('relSaveProjectsBtn').addEventListener('click', async () => {
    const checked = [...document.querySelectorAll('.rel-proj-cb:checked')].map(cb => Number(cb.value));
    const btn = document.getElementById('relSaveProjectsBtn');
    try {
      const res = await fetch(`${KNOWLEDGE_API_BASE}/knowledge/block/${block.id}/projects`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ project_ids: checked })
      });
      if (!res.ok) throw new Error(await res.text());
      block.project_ids = checked;
      btn.textContent = '✓ Saved'; btn.classList.add('saved');
      setTimeout(() => { btn.textContent = 'Save Projects'; btn.classList.remove('saved'); }, 1500);
    } catch (err) { console.error(err); alert('Error saving projects'); }
  });
}

function getDescendantIds(conceptId) {
  const result = new Set();
  function recurse(id) {
    relAllConcepts.filter(c => c.parent_concept_id === id).forEach(c => {
      result.add(c.id);
      recurse(c.id);
    });
  }
  recurse(conceptId);
  return result;
}

// Close modal
document.getElementById('relCloseBtn').addEventListener('click', () => {
  document.getElementById('relationsModal').style.display = 'none';
  document.body.style.overflow = '';
});
document.getElementById('relationsModal').addEventListener('click', (e) => {
  if (e.target === e.currentTarget) {
    e.currentTarget.style.display = 'none';
    document.body.style.overflow = '';
  }
});
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    const modal = document.getElementById('relationsModal');
    if (modal && modal.style.display !== 'none') {
      modal.style.display = 'none';
      document.body.style.overflow = '';
    }
  }
});

async function handleDeleteClick(event) {
    const blockId = event.target.dataset.blockId;
    if (!confirm('Are you sure you want to delete this block?')) return;

    try {
        const response = await fetch(`${KNOWLEDGE_API_BASE}/knowledge/block/${blockId}`, {
            method: 'DELETE'
        });
        if (!response.ok) throw new Error('Failed to delete');

        // Eliminar del DOM
        const blockDiv = document.querySelector(`[data-block-id="${blockId}"]`);
        if (blockDiv) blockDiv.remove();

    } catch (error) {
        console.error('Error deleting block:', error);
        alert('Error al eliminar el bloque');
    }
}

// ==============================
// INGEST MODAL
// ==============================

(function setupIngest() {
  const modal       = document.getElementById('ingestModal');
  const openBtn     = document.getElementById('ingestBtn');
  const closeBtn    = document.getElementById('ingestCloseBtn');
  const dropArea    = document.getElementById('ingestDropArea');
  const fileInput   = document.getElementById('ingestFileInput');
  const fileInfo    = document.getElementById('ingestFileInfo');
  const fileName    = document.getElementById('ingestFileName');
  const clearFile   = document.getElementById('ingestClearFile');
  const projectSel  = document.getElementById('ingestProjectSelect');
  const instructions= document.getElementById('ingestInstructions');
  const runBtn      = document.getElementById('ingestRunBtn');
  const status      = document.getElementById('ingestStatus');
  const listEl      = document.getElementById('ingestSuggestions');
  const bulkActions = document.getElementById('ingestBulkActions');
  const acceptAll   = document.getElementById('ingestAcceptAll');
  const rejectAll   = document.getElementById('ingestRejectAll');
  const pageRange   = document.getElementById('ingestPageRange');
  const pageFrom    = document.getElementById('ingestPageFrom');
  const pageTo      = document.getElementById('ingestPageTo');

  let currentFile = null;
  let suggestions = []; // [{concept, block_type, content, parent_concept_name, _state}]
  let ingestExistingConcepts = []; // concepts fetched by backend for this ingest run
  let allSugNodes = []; // all nodes (real + virtual) for the current render
  const committedByName = {}; // name.lower → real concept id, for auto-committed virtual parents

  function updateRunAvailability() {
    const hasFile = !!currentFile;
    const hasInstructions = !!instructions.value.trim();
    runBtn.disabled = !(hasFile || hasInstructions);
  }

  // Open / close
  openBtn.addEventListener('click', () => {
    modal.style.display = 'flex';
    document.body.style.overflow = 'hidden';
    populateProjectSelect();
  });
  const closeModal = () => {
    modal.style.display = 'none';
    document.body.style.overflow = '';
  };
  closeBtn.addEventListener('click', closeModal);
  modal.addEventListener('click', e => { if (e.target === modal) closeModal(); });
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && modal.style.display !== 'none') closeModal();
  });

  // Populate project selector (reuse existing projects from knowledgeState context)
  function populateProjectSelect() {
    projectSel.innerHTML = '<option value="">No project</option>';
    document.querySelectorAll('#projectSelect option').forEach(opt => {
      if (!opt.value) return;
      const o = document.createElement('option');
      o.value = opt.value;
      o.textContent = opt.textContent;
      if (opt.value == knowledgeState.project_id) o.selected = true;
      projectSel.appendChild(o);
    });
  }

  // File picking
  dropArea.addEventListener('click', () => fileInput.click());
  fileInput.addEventListener('change', () => setFile(fileInput.files[0]));

  dropArea.addEventListener('dragover', e => { e.preventDefault(); dropArea.classList.add('drag-over'); });
  dropArea.addEventListener('dragleave', () => dropArea.classList.remove('drag-over'));
  dropArea.addEventListener('drop', e => {
    e.preventDefault();
    dropArea.classList.remove('drag-over');
    setFile(e.dataTransfer.files[0]);
  });

  clearFile.addEventListener('click', () => setFile(null));
  instructions.addEventListener('input', updateRunAvailability);

  function setFile(file) {
    if (file && !['application/pdf',
      'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
    ].includes(file.type) && !file.name.match(/\.(pdf|docx)$/i)) {
      status.textContent = 'Only PDF and DOCX files are supported.';
      return;
    }
    currentFile = file || null;
    if (currentFile) {
      fileName.textContent = currentFile.name;
      fileInfo.style.display = 'flex';
      dropArea.style.display = 'none';
      status.textContent = '';
      const isPdf = currentFile.name.match(/\.pdf$/i);
      if (pageRange) pageRange.style.display = isPdf ? 'block' : 'none';
    } else {
      fileInfo.style.display = 'none';
      dropArea.style.display = '';
      fileInput.value = '';
      if (pageRange) pageRange.style.display = 'none';
      if (pageFrom) pageFrom.value = '';
      if (pageTo) pageTo.value = '';
    }
    updateRunAvailability();
  }

  // Run analysis
  runBtn.addEventListener('click', async () => {
    const hasInstructions = !!instructions.value.trim();
    if (!currentFile && !hasInstructions) return;
    runBtn.disabled = true;
    status.textContent = currentFile
      ? '⏳ Uploading and analysing…'
      : '⏳ Analysing instructions…';
    listEl.innerHTML = `<div class="ingest-empty">${currentFile ? 'Analysing document…' : 'Analysing instructions…'}</div>`;
    bulkActions.style.display = 'none';
    suggestions = [];
    allSugNodes = [];
    Object.keys(committedByName).forEach(k => delete committedByName[k]);

    try {
      const formData = new FormData();
      if (currentFile) formData.append('file', currentFile);
      if (projectSel.value) formData.append('project_id', projectSel.value);
      if (hasInstructions) formData.append('instructions', instructions.value.trim());
      if (pageFrom && pageFrom.value) formData.append('page_from', pageFrom.value);
      if (pageTo && pageTo.value) formData.append('page_to', pageTo.value);

      const res = await fetch(`${KNOWLEDGE_API_BASE}/knowledge/ingest`, {
        method: 'POST',
        body: formData
      });

      if (!res.ok) {
        const err = await res.text();
        throw new Error(err);
      }

      const payload = await res.json();
      const rawSuggestions = Array.isArray(payload) ? payload : (payload.suggestions || []);
      ingestExistingConcepts = Array.isArray(payload.existing_concepts) ? payload.existing_concepts : cachedConcepts;
      if (!rawSuggestions.length) {
        listEl.innerHTML = '<div class="ingest-empty">No suggestions generated. Try different instructions.</div>';
        status.textContent = '';
        return;
      }
      suggestions = rawSuggestions.map(s => ({ ...s, _state: 'pending' }));
      renderSuggestions();
      bulkActions.style.display = 'flex';
      status.textContent = `${suggestions.length} suggestions ready.`;
    } catch (err) {
      console.error(err);
      status.textContent = '❌ Error: ' + err.message;
      listEl.innerHTML = '<div class="ingest-empty">Something went wrong.</div>';
    } finally {
      updateRunAvailability();
    }
  });

  // Render suggestion tree as contextual hierarchy preview
  function renderSuggestions() {
    listEl.innerHTML = '';
    if (!suggestions.length) return;

    // Build lookup maps — use concepts the backend actually fed to GPT
    const sourceList = ingestExistingConcepts.length ? ingestExistingConcepts : cachedConcepts;
    const existingById = {};
    const existingByName = {};
    const existingByNameNorm = {}; // spaces+case stripped for fuzzy fallback
    sourceList.forEach(c => {
      existingById[c.id] = c;
      existingByName[c.name.toLowerCase().trim()] = c;
      existingByNameNorm[c.name.toLowerCase().replace(/\s+/g, '')] = c;
    });

    // fuzzy name lookup: exact → normalised → startsWith
    function findExisting(rawName) {
      if (!rawName) return null;
      const lower = rawName.toLowerCase().trim();
      if (existingByName[lower]) return existingByName[lower];
      const norm = lower.replace(/\s+/g, '');
      if (existingByNameNorm[norm]) return existingByNameNorm[norm];
      // startsWith fallback
      const found = Object.values(existingByName).find(c =>
        c.name.toLowerCase().startsWith(lower) || lower.startsWith(c.name.toLowerCase())
      );
      return found || null;
    }

    console.log('[ingest] existing concept names:', Object.keys(existingByName));
    console.log('[ingest] suggestions from GPT:', suggestions.map(s => ({ concept: s.concept, parent: s.parent_concept_name })));

    // ── Build sugNodes (synthetic IDs: -1, -2, …) ─────────────────────────
    // Parent resolution order:
    //   1. Existing concept (fuzzy match)
    //   2. Another suggestion in the same batch (by name)
    //   3. Virtual intermediate node (auto-created as a pending suggestion)

    let nextVirtualId = -(suggestions.length + 1);
    const virtualNodes = []; // extra nodes created to bridge orphaned suggestions

    // First pass – create stubs (parent resolved in second pass)
    const sugNodes = suggestions.map((s, i) => ({
      _sugIdx: i,
      id: -(i + 1),
      name: s.concept,
      _parentName: s.parent_concept_name,
      parent_concept_id: null, // filled below
      isSuggested: true,
      _state: s._state,
      block_type: s.block_type,
      content: s.content
    }));

    // Name → sugNode map for cross-suggestion lookup
    const sugNodeByName = {};
    sugNodes.forEach(sn => {
      sugNodeByName[sn.name.toLowerCase().trim()] = sn;
      sugNodeByName[sn.name.toLowerCase().replace(/\s+/g, '')] = sn;
    });

    function findOrCreateVirtual(rawName) {
      const lower = rawName.toLowerCase().trim();
      const norm  = lower.replace(/\s+/g, '');
      let v = virtualNodes.find(n =>
        n.name.toLowerCase().trim() === lower ||
        n.name.toLowerCase().replace(/\s+/g, '') === norm
      );
      if (!v) {
        console.warn(`[ingest] creating virtual parent: "${rawName}"`);
        v = {
          _sugIdx: null, _isVirtual: true,
          id: nextVirtualId--,
          name: rawName,
          _parentName: null,
          parent_concept_id: null,
          isSuggested: true,
          _state: 'pending',
          block_type: 'definition',
          content: ''
        };
        virtualNodes.push(v);
        sugNodeByName[lower] = v;
        sugNodeByName[norm]   = v;
      }
      return v;
    }

    // Second pass – resolve parents
    sugNodes.forEach(sn => {
      const raw = sn._parentName;
      if (!raw) return;
      // 1. existing concept
      const existing = findExisting(raw);
      if (existing) { sn.parent_concept_id = existing.id; return; }
      // 2. sibling suggestion
      const lower  = raw.toLowerCase().trim();
      const norm   = raw.toLowerCase().replace(/\s+/g, '').trim();
      const sibling = sugNodeByName[lower] || sugNodeByName[norm];
      if (sibling && sibling !== sn) { sn.parent_concept_id = sibling.id; return; }
      // 3. create virtual
      sn.parent_concept_id = findOrCreateVirtual(raw).id;
    });

    allSugNodes = [...sugNodes, ...virtualNodes];

    // Collect relevant existing concept IDs (ancestors of all attachment points)
    const relevantIds = new Set();
    function collectAncestors(id) {
      if (!id || relevantIds.has(id)) return;
      relevantIds.add(id);
      const c = existingById[id];
      if (c && c.parent_concept_id) collectAncestors(c.parent_concept_id);
    }
    allSugNodes.forEach(sn => {
      if (sn.parent_concept_id && sn.parent_concept_id > 0) collectAncestors(sn.parent_concept_id);
    });

    // Build combined byParent map
    const byParent = {};
    sourceList.forEach(c => {
      if (!relevantIds.has(c.id)) return;
      const parentInScope = c.parent_concept_id && existingById[c.parent_concept_id];
      const key = parentInScope ? c.parent_concept_id : 'root';
      byParent[key] ||= [];
      byParent[key].push({ ...c, isSuggested: false });
    });
    allSugNodes.forEach(sn => {
      const parentInScope = sn.parent_concept_id &&
        (existingById[sn.parent_concept_id] ||
         allSugNodes.find(n => n.id === sn.parent_concept_id));
      const key = parentInScope ? sn.parent_concept_id : 'root';
      byParent[key] ||= [];
      byParent[key].push(sn);
    });

    console.log('[ingest] allSugNodes:', allSugNodes.map(sn => ({ name: sn.name, parent_concept_id: sn.parent_concept_id, virtual: !!sn._isVirtual })));
    console.log('[ingest] byParent keys:', Object.keys(byParent));
    console.log('[ingest] byParent[root]:', (byParent['root'] || []).map(n => n.name));

    // Recursive renderer
    function renderNode(parentKey, depth, isLastArr) {
      const children = (byParent[parentKey] || []);
      children.forEach((node, idx) => {
        const isLast = idx === children.length - 1;
        const hasChildren = (byParent[node.id] || []).length > 0;

        const wrap = document.createElement('div');

        // Row
        const row = document.createElement('div');
        row.className = 'ingest-tree-row' + (node.isSuggested ? ' is-suggested state-' + node._state : ' is-existing');

        // Indent + connectors
        if (depth > 0) {
          const indent = document.createElement('span');
          indent.className = 'ingest-tree-indent';
          // vertical continuation lines for ancestor levels
          isLastArr.forEach(parentWasLast => {
            const span = document.createElement('span');
            span.style.cssText = `display:inline-block;width:16px;flex-shrink:0;${
              parentWasLast ? '' : 'border-left:1px solid rgba(255,255,255,0.09);margin-left:0'
            }`;
            indent.appendChild(span);
          });
          const corner = document.createElement('span');
          corner.className = isLast ? 'ingest-line-corner' : 'ingest-line-tee';
          indent.appendChild(corner);
          row.appendChild(indent);
        }

        const dot = document.createElement('span');
        dot.className = 'ingest-tree-dot' + (node.isSuggested ? ' suggested state-' + node._state : ' existing');
        row.appendChild(dot);

        const name = document.createElement('span');
        name.className = 'ingest-tree-name' + (node.isSuggested ? ' suggested' : ' existing');
        name.textContent = node.name;
        row.appendChild(name);

        if (node.isSuggested) {
          const badge = document.createElement('span');
          badge.className = 'ingest-tree-type';
          badge.textContent = node.block_type || 'definition';
          row.appendChild(badge);

          const acceptBtn = document.createElement('button');
          acceptBtn.className = 'ingest-tree-btn ingest-tree-btn--accept';
          acceptBtn.dataset.i = node._sugIdx;
          acceptBtn.title = 'Accept';
          acceptBtn.textContent = '✔';
          acceptBtn.disabled = node._state === 'accepted';

          const rejectBtn = document.createElement('button');
          rejectBtn.className = 'ingest-tree-btn ingest-tree-btn--reject';
          rejectBtn.dataset.i = node._sugIdx;
          rejectBtn.title = 'Reject';
          rejectBtn.textContent = '✖';
          rejectBtn.disabled = node._state === 'rejected';

          row.appendChild(acceptBtn);
          row.appendChild(rejectBtn);

          // Block content detail (toggle on click)
          const detail = document.createElement('div');
          detail.className = 'ingest-tree-detail';
          detail.style.display = 'none';
          const contentEl = document.createElement('div');
          contentEl.className = 'ingest-tree-content';
          contentEl.textContent = node.content;
          detail.appendChild(contentEl);

          row.addEventListener('click', e => {
            if (e.target.closest('.ingest-tree-btn')) return;
            detail.style.display = detail.style.display === 'none' ? 'block' : 'none';
          });

          wrap.appendChild(row);
          wrap.appendChild(detail);
        } else {
          // Existing concept: just expander icon
          const icon = document.createElement('span');
          icon.className = 'ingest-tree-expander';
          icon.textContent = hasChildren ? '▾' : '';
          row.insertBefore(icon, dot);
          wrap.appendChild(row);
        }

        listEl.appendChild(wrap);

        // Recurse
        if (byParent[node.id]) {
          renderNode(node.id, depth + 1, [...isLastArr, isLast]);
        }
      });
    }

    renderNode('root', 0, []);

    listEl.querySelectorAll('.ingest-tree-btn--accept').forEach(btn =>
      btn.addEventListener('click', () => setSuggestionState(+btn.dataset.i, 'accepted'))
    );
    listEl.querySelectorAll('.ingest-tree-btn--reject').forEach(btn =>
      btn.addEventListener('click', () => setSuggestionState(+btn.dataset.i, 'rejected'))
    );
  }

  async function setSuggestionState(i, state) {
    if (suggestions[i]._state === state) return;
    const prev = suggestions[i]._state;
    suggestions[i]._state = state;

    if (state === 'accepted') {
      try {
        await commitSuggestion(suggestions[i]);
      } catch (e) {
        suggestions[i]._state = prev;
        alert('Error creating concept/block: ' + e.message);
      }
    }
    renderSuggestions();
  }

  async function commitSuggestion(s) {
    const projectId = projectSel.value ? Number(projectSel.value) : null;
    return commitByNode({ name: s.concept, parent_concept_name: s.parent_concept_name, content: s.content, block_type: s.block_type }, projectId);
  }

  // Commit a node (real suggestion or virtual) and return the new concept id
  async function commitByNode(node, projectId) {
    const lower = node.name.toLowerCase().trim();
    if (committedByName[lower] !== undefined) return committedByName[lower];

    const parentId = await resolveParentIdByName(node.parent_concept_name, projectId);

    const cRes = await fetch(`${KNOWLEDGE_API_BASE}/knowledge/concepts/new`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: node.name, parent_concept_id: parentId, project_id: projectId })
    });
    if (!cRes.ok) throw new Error(await cRes.text());
    const { id: conceptId } = await cRes.json();
    committedByName[lower] = conceptId;

    if (node.content) {
      const bRes = await fetch(`${KNOWLEDGE_API_BASE}/knowledge/block/new`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          concept_id: conceptId,
          content: node.content,
          block_type: node.block_type || 'definition',
          project_id: projectId,
          mode: null
        })
      });
      if (!bRes.ok) throw new Error(await bRes.text());
    }

    loadConcepts();
    return conceptId;
  }

  async function resolveParentIdByName(parentName, projectId) {
    if (!parentName) return null;
    const lower = parentName.toLowerCase().trim();

    if (committedByName[lower] !== undefined) return committedByName[lower];

    // Sidebar DOM
    const all = document.querySelectorAll('#conceptTree .concept-item[data-concept-name]');
    for (const el of all) {
      if (el.dataset.conceptName === lower) return Number(el.dataset.conceptId);
    }

    // Another sugNode → auto-commit first
    const norm = lower.replace(/\s+/g, '');
    const parentNode = allSugNodes.find(n =>
      n.name.toLowerCase().trim() === lower ||
      n.name.toLowerCase().replace(/\s+/g, '') === norm
    );
    if (parentNode) return await commitByNode(parentNode, projectId);

    return null;
  }

  // Bulk actions
  acceptAll.addEventListener('click', async () => {
    for (let i = 0; i < suggestions.length; i++) {
      if (suggestions[i]._state === 'pending') await setSuggestionState(i, 'accepted');
    }
  });
  rejectAll.addEventListener('click', () => {
    suggestions.forEach((_, i) => { if (suggestions[i]._state === 'pending') suggestions[i]._state = 'rejected'; });
    renderSuggestions();
  });
})();

