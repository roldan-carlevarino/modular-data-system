// ---------------- PROJECTS ----------------

const PROJECTS_URL = "https://api-dashboard-production-fc05.up.railway.app/projects/";
const projectsList = document.getElementById("projects");
const pomodoroProjectsList = document.getElementById("pomodoroProjects");
const SCHEMAS_STORAGE_KEY = "dashboard.projectSchemas.v1";

let cachedProjects = [];
let schemaAutosaveTimer = null;
let activeProjectId = null;
const collapsedProjectIds = new Set();

function normalizeProjectType(rawType) {
  const t = String(rawType || '').trim().toLowerCase();
  if (t === 'project' || t === 'projects') return 'project';
  if (t === 'task' || t === 'tasks') return 'task';
  return t || 'task';
}

const schemaProjectSelect = document.getElementById('projectSchemaProject');
const schemaSelect = document.getElementById('projectSchemaSelect');
const schemaNewBtn = document.getElementById('projectSchemaNewBtn');
const schemaRenameBtn = document.getElementById('projectSchemaRenameBtn');
const schemaDuplicateBtn = document.getElementById('projectSchemaDuplicateBtn');
const schemaTemplateBtn = document.getElementById('projectSchemaTemplateBtn');
const schemaDeleteBtn = document.getElementById('projectSchemaDeleteBtn');
const schemaExportBtn = document.getElementById('projectSchemaExportBtn');
const schemaDiagramBtn = document.getElementById('projectSchemaDiagramBtn');
const schemaDiagramContainer = document.getElementById('projectSchemaDiagramContainer');
const schemaText = document.getElementById('projectSchemaText');
const schemaStatus = document.getElementById('projectSchemaStatus');
const schemaModal = document.getElementById('projectSchemasModal');
const schemaModalOpenBtn = document.getElementById('projectSchemasBtn');
const schemaModalCloseBtn = document.getElementById('projectSchemasCloseBtn');
let schemaDiagramActive = false;

const SCHEMA_TEMPLATES = {
  Architecture: [
    '# Architecture',
    '',
    '- Goal',
    '- Core modules',
    '  - Data ingestion',
    '  - Signal generation',
    '  - Execution',
    '  - Risk management',
    '- External dependencies',
    '- Open technical risks'
  ].join('\n'),
  Roadmap: [
    '# Roadmap',
    '',
    '- Phase 1 (MVP)',
    '  - Scope',
    '  - Deliverables',
    '- Phase 2 (Hardening)',
    '  - Testing',
    '  - Monitoring',
    '- Phase 3 (Scale)',
    '  - Performance',
    '  - Reliability'
  ].join('\n'),
  Research: [
    '# Research Notes',
    '',
    '- Hypothesis',
    '- Assumptions',
    '- Data sources',
    '- Experiments',
    '- Findings',
    '- Next actions'
  ].join('\n')
};

function isSchemaUIAvailable() {
  return !!(
    schemaProjectSelect && schemaSelect && schemaNewBtn && schemaRenameBtn &&
    schemaDuplicateBtn && schemaTemplateBtn && schemaDeleteBtn && schemaExportBtn &&
    schemaText && schemaStatus
  );
}

function loadSchemaStore() {
  try {
    const parsed = JSON.parse(localStorage.getItem(SCHEMAS_STORAGE_KEY) || "{}");
    return parsed && typeof parsed === 'object' ? parsed : {};
  } catch {
    return {};
  }
}

function saveSchemaStore(store) {
  localStorage.setItem(SCHEMAS_STORAGE_KEY, JSON.stringify(store));
}

let schemaStore = loadSchemaStore();

function getSchemaBucket(projectId) {
  if (!projectId) return null;
  if (!schemaStore[projectId]) {
    schemaStore[projectId] = {
      General: {
        content: "",
        updated_at: new Date().toISOString()
      }
    };
    saveSchemaStore(schemaStore);
  }
  return schemaStore[projectId];
}

function setSchemaStatus(text) {
  if (schemaStatus) schemaStatus.textContent = text;
}

function getProjectNameById(projectId) {
  const p = cachedProjects.find(x => String(x.id) === String(projectId));
  return p ? p.name : `Project ${projectId}`;
}

function getSortedSchemaNames(bucket) {
  return Object.keys(bucket).sort((a, b) => {
    const aTime = bucket[a]?.updated_at ? new Date(bucket[a].updated_at).getTime() : 0;
    const bTime = bucket[b]?.updated_at ? new Date(bucket[b].updated_at).getTime() : 0;
    if (aTime !== bTime) return bTime - aTime;
    return a.localeCompare(b);
  });
}

function hasSchemaName(bucket, candidate, ignoreName = null) {
  const key = candidate.trim().toLowerCase();
  return Object.keys(bucket).some(name => {
    if (ignoreName && name === ignoreName) return false;
    return name.trim().toLowerCase() === key;
  });
}

function makeUniqueSchemaName(bucket, baseName) {
  if (!hasSchemaName(bucket, baseName)) return baseName;
  let n = 2;
  while (hasSchemaName(bucket, `${baseName} (${n})`)) n += 1;
  return `${baseName} (${n})`;
}

function flushSchemaAutosave() {
  if (schemaAutosaveTimer) {
    clearTimeout(schemaAutosaveTimer);
    schemaAutosaveTimer = null;
    syncSchemaDiagramToTextarea();
    saveCurrentSchema();
  }
}

function refreshSchemaSelectors() {
  if (!isSchemaUIAvailable()) return;

  const projectId = schemaProjectSelect.value;
  const bucket = getSchemaBucket(projectId);
  if (!bucket) {
    schemaSelect.innerHTML = "";
    schemaText.value = "";
    schemaText.disabled = true;
    setSchemaStatus("No project available.");
    return;
  }

  const prevSchema = schemaSelect.value;
  const schemaNames = getSortedSchemaNames(bucket);
  schemaSelect.innerHTML = "";
  schemaNames.forEach(name => {
    const opt = document.createElement('option');
    opt.value = name;
    opt.textContent = name;
    schemaSelect.appendChild(opt);
  });

  if (schemaNames.includes(prevSchema)) {
    schemaSelect.value = prevSchema;
  } else {
    schemaSelect.value = schemaNames[0];
  }

  const current = bucket[schemaSelect.value];
  schemaText.disabled = false;
  schemaText.value = current?.content || "";

  const timestamp = current?.updated_at
    ? new Date(current.updated_at).toLocaleString()
    : "never";
  setSchemaStatus(`Editing "${schemaSelect.value}" · Last save: ${timestamp}`);
}

function saveCurrentSchema() {
  if (!isSchemaUIAvailable()) return;
  const projectId = schemaProjectSelect.value;
  const schemaName = schemaSelect.value;
  if (!projectId || !schemaName) return;

  const bucket = getSchemaBucket(projectId);
  bucket[schemaName] = {
    content: schemaText.value,
    updated_at: new Date().toISOString()
  };
  saveSchemaStore(schemaStore);
  setSchemaStatus(`Saved "${schemaName}" · ${new Date(bucket[schemaName].updated_at).toLocaleTimeString()}`);
}

function queueSchemaAutosave() {
  clearTimeout(schemaAutosaveTimer);
  setSchemaStatus("Saving…");
  schemaAutosaveTimer = setTimeout(saveCurrentSchema, 450);
}

function applyTemplateToSchema(templateName) {
  const tpl = SCHEMA_TEMPLATES[templateName];
  if (!tpl) return;
  if (schemaText.value.trim()) {
    const replace = confirm('Replace current schema content with template?\nPress Cancel to append it below.');
    if (replace) {
      schemaText.value = tpl;
    } else {
      schemaText.value = `${schemaText.value.trimEnd()}\n\n${tpl}`;
    }
  } else {
    schemaText.value = tpl;
  }
  queueSchemaAutosave();
  schemaText.focus();
}

function indentSelectedLines(outdent = false) {
  const text = schemaText.value;
  const start = schemaText.selectionStart;
  const end = schemaText.selectionEnd;

  const lineStart = text.lastIndexOf('\n', start - 1) + 1;
  const nextNewline = text.indexOf('\n', end);
  const lineEnd = nextNewline === -1 ? text.length : nextNewline;

  const before = text.slice(0, lineStart);
  const block = text.slice(lineStart, lineEnd);
  const after = text.slice(lineEnd);
  const lines = block.split('\n');

  const transformed = lines.map(line => {
    if (!outdent) return `  ${line}`;
    if (line.startsWith('  ')) return line.slice(2);
    if (line.startsWith('\t')) return line.slice(1);
    return line;
  }).join('\n');

  schemaText.value = `${before}${transformed}${after}`;
  const delta = transformed.length - block.length;
  schemaText.selectionStart = lineStart;
  schemaText.selectionEnd = end + delta;
  queueSchemaAutosave();
}

function updateSchemaProjectOptions(projects) {
  if (!isSchemaUIAvailable()) return;

  const prev = schemaProjectSelect.value;
  schemaProjectSelect.innerHTML = "";

  if (!projects.length) {
    const opt = document.createElement('option');
    opt.value = "";
    opt.textContent = "No projects";
    schemaProjectSelect.appendChild(opt);
    schemaText.disabled = true;
    schemaSelect.innerHTML = "";
    setSchemaStatus("Create a project first.");
    return;
  }

  projects.forEach(project => {
    const pathStr = project.path ? String(project.path) : '';
    const level = (pathStr.match(/\./g) || []).length;
    const opt = document.createElement('option');
    opt.value = project.id;
    opt.textContent = `${'\u00a0'.repeat(level * 2)}${project.name}`;
    schemaProjectSelect.appendChild(opt);
  });

  if (projects.some(p => String(p.id) === String(prev))) {
    schemaProjectSelect.value = prev;
  } else {
    schemaProjectSelect.value = String(projects[0].id);
  }

  refreshSchemaSelectors();
}

// ---- Schema Diagram Mode ----

function syncSchemaDiagramToTextarea() {
  if (!schemaDiagramActive || !schemaDiagramContainer) return;
  const codeArea = schemaDiagramContainer.querySelector('.diagram-code');
  if (codeArea) {
    schemaText.value = wrapMermaidCode(codeArea.value);
  }
}

function activateSchemaDiagram() {
  if (schemaDiagramActive) return;
  schemaDiagramActive = true;
  schemaDiagramBtn.classList.add('kb-btn--active');

  // Extract mermaid code from current textarea or start fresh
  const mermaidCode = extractMermaidCode(schemaText.value) || DIAGRAM_TEMPLATES.flowchart;
  schemaDiagramContainer.innerHTML = buildDiagramEditor(mermaidCode);
  schemaDiagramContainer.style.display = '';
  schemaText.style.display = 'none';

  initDiagramEditor(schemaDiagramContainer);

  // Hook diagram input → autosave
  const codeArea = schemaDiagramContainer.querySelector('.diagram-code');
  if (codeArea) {
    codeArea.addEventListener('input', () => {
      syncSchemaDiagramToTextarea();
      queueSchemaAutosave();
    });
  }
}

function deactivateSchemaDiagram() {
  if (!schemaDiagramActive) return;
  syncSchemaDiagramToTextarea();
  schemaDiagramActive = false;
  schemaDiagramBtn.classList.remove('kb-btn--active');
  schemaDiagramContainer.innerHTML = '';
  schemaDiagramContainer.style.display = 'none';
  schemaText.style.display = '';
}

function setupProjectSchemas() {
  if (!isSchemaUIAvailable()) return;

  schemaProjectSelect.addEventListener('change', () => {
    flushSchemaAutosave();
    deactivateSchemaDiagram();
    refreshSchemaSelectors();
  });

  schemaSelect.addEventListener('change', () => {
    flushSchemaAutosave();
    deactivateSchemaDiagram();
    refreshSchemaSelectors();
  });

  schemaText.addEventListener('input', queueSchemaAutosave);
  schemaText.addEventListener('keydown', e => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
      e.preventDefault();
      flushSchemaAutosave();
      return;
    }
    if (e.key === 'Tab') {
      e.preventDefault();
      indentSelectedLines(e.shiftKey);
    }
  });

  // Diagram toggle button
  if (schemaDiagramBtn) {
    schemaDiagramBtn.addEventListener('click', () => {
      if (schemaDiagramActive) {
        deactivateSchemaDiagram();
      } else {
        activateSchemaDiagram();
      }
    });
  }

  schemaNewBtn.addEventListener('click', () => {
    flushSchemaAutosave();
    const projectId = schemaProjectSelect.value;
    if (!projectId) return;
    const name = prompt('New schema name', 'Architecture');
    if (!name) return;
    const cleanName = name.trim();
    if (!cleanName) return;

    const bucket = getSchemaBucket(projectId);
    if (hasSchemaName(bucket, cleanName)) {
      alert('A schema with that name already exists');
      return;
    }

    bucket[cleanName] = {
      content: '',
      updated_at: new Date().toISOString()
    };
    saveSchemaStore(schemaStore);
    refreshSchemaSelectors();
    schemaSelect.value = cleanName;
    refreshSchemaSelectors();
    schemaText.focus();
  });

  schemaRenameBtn.addEventListener('click', () => {
    flushSchemaAutosave();
    const projectId = schemaProjectSelect.value;
    const schemaName = schemaSelect.value;
    if (!projectId || !schemaName) return;

    const next = prompt('Rename schema', schemaName);
    if (!next) return;
    const nextName = next.trim();
    if (!nextName || nextName === schemaName) return;

    const bucket = getSchemaBucket(projectId);
    if (hasSchemaName(bucket, nextName, schemaName)) {
      alert('A schema with that name already exists');
      return;
    }

    bucket[nextName] = bucket[schemaName];
    delete bucket[schemaName];
    bucket[nextName].updated_at = new Date().toISOString();
    saveSchemaStore(schemaStore);
    refreshSchemaSelectors();
    schemaSelect.value = nextName;
    refreshSchemaSelectors();
  });

  schemaDuplicateBtn.addEventListener('click', () => {
    flushSchemaAutosave();
    const projectId = schemaProjectSelect.value;
    const schemaName = schemaSelect.value;
    if (!projectId || !schemaName) return;

    const bucket = getSchemaBucket(projectId);
    const copyName = makeUniqueSchemaName(bucket, `${schemaName} copy`);
    bucket[copyName] = {
      content: schemaText.value,
      updated_at: new Date().toISOString()
    };
    saveSchemaStore(schemaStore);
    refreshSchemaSelectors();
    schemaSelect.value = copyName;
    refreshSchemaSelectors();
  });

  schemaTemplateBtn.addEventListener('click', () => {
    const keys = Object.keys(SCHEMA_TEMPLATES);
    const selected = prompt(`Template: ${keys.join(', ')}`, keys[0]);
    if (!selected) return;
    const exact = keys.find(k => k.toLowerCase() === selected.trim().toLowerCase());
    if (!exact) {
      alert('Template not found');
      return;
    }
    applyTemplateToSchema(exact);
  });

  schemaDeleteBtn.addEventListener('click', () => {
    flushSchemaAutosave();
    const projectId = schemaProjectSelect.value;
    const schemaName = schemaSelect.value;
    if (!projectId || !schemaName) return;

    const bucket = getSchemaBucket(projectId);
    const names = Object.keys(bucket);
    if (names.length <= 1) {
      alert('At least one schema must remain for the project');
      return;
    }
    if (!confirm(`Delete schema "${schemaName}"?`)) return;

    delete bucket[schemaName];
    saveSchemaStore(schemaStore);
    refreshSchemaSelectors();
  });

  schemaExportBtn.addEventListener('click', () => {
    flushSchemaAutosave();
    const projectId = schemaProjectSelect.value;
    const schemaName = schemaSelect.value;
    if (!projectId || !schemaName) return;

    const bucket = getSchemaBucket(projectId);
    const schema = bucket[schemaName];
    const projectName = getProjectNameById(projectId);

    const md = [
      `# ${projectName} · ${schemaName}`,
      '',
      `Exported: ${new Date().toLocaleString()}`,
      '',
      schema?.content || ''
    ].join('\n');

    const blob = new Blob([md], { type: 'text/markdown;charset=utf-8' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `${projectName}-${schemaName}.md`.replace(/\s+/g, '_');
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(a.href);
  });
}

function setupProjectSchemasModal() {
  if (!schemaModal || !schemaModalOpenBtn || !schemaModalCloseBtn) return;

  const closeModal = () => {
    flushSchemaAutosave();
    deactivateSchemaDiagram();
    schemaModal.style.display = 'none';
    document.body.style.overflow = '';
  };

  schemaModalOpenBtn.addEventListener('click', () => {
    schemaModal.style.display = 'flex';
    document.body.style.overflow = 'hidden';
    updateSchemaProjectOptions(cachedProjects);
  });

  schemaModalCloseBtn.addEventListener('click', closeModal);

  schemaModal.addEventListener('click', e => {
    if (e.target === schemaModal) closeModal();
  });

  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && schemaModal.style.display !== 'none') closeModal();
  });
}

// Función para renderizar proyectos
function renderProjects(container, clickable = false) {
  if (!container) return;

  fetch(PROJECTS_URL)
    .then(res => {
      if (!res.ok) {
        throw new Error(`HTTP ${res.status}: ${res.statusText}`);
      }
      return res.json();
    })
    .then(projects => {
      console.log("Projects recibidos:", projects);
      cachedProjects = Array.isArray(projects) ? projects : [];
      updateSchemaProjectOptions(cachedProjects);
      
      if (!Array.isArray(projects) || projects.length === 0) {
        container.innerHTML = '<li class="no-data">No active projects</li>';
        return;
      }

      container.innerHTML = '';

      const byParent = {};
      const projectIds = new Set(projects.map(p => p.id));

      projects.forEach(project => {
        const parentKey = (project.parent_id && projectIds.has(project.parent_id))
          ? project.parent_id
          : 'root';
        byParent[parentKey] ||= [];
        byParent[parentKey].push(project);
      });

      const sortByPath = (a, b) => String(a.path || '').localeCompare(String(b.path || ''));
      Object.values(byParent).forEach(list => list.sort(sortByPath));

      function renderNode(parentKey, depth = 0) {
        const children = byParent[parentKey] || [];
        children.forEach(project => {
          const hasChildren = (byParent[project.id] || []).length > 0;
          const isCollapsed = collapsedProjectIds.has(String(project.id));
          const normalizedType = normalizeProjectType(project.type);

          const li = document.createElement("li");
          li.classList.add("project-item");
          li.dataset.projectId = project.id;
          li.dataset.type = normalizedType;
          li.style.marginLeft = `${depth * 0.9}rem`;

          if (clickable) li.classList.add("clickable");
          if (String(project.id) === String(activeProjectId)) li.classList.add('active');

          const icon = normalizedType === 'project' ? '📁' : '📄';
          const toggle = hasChildren ? (isCollapsed ? '▸' : '▾') : '';

          const content = document.createElement('div');
          content.className = 'project-content';
          content.innerHTML = `
            <button class="project-toggle" type="button" ${hasChildren ? '' : 'disabled'}>${toggle}</button>
            <span class="project-icon">${icon}</span>
            <span class="project-name">${project.name}</span>
            ${project.description ? `<span class="project-desc">${project.description}</span>` : ''}
          `;

          const toggleBtn = content.querySelector('.project-toggle');
          if (hasChildren && toggleBtn) {
            toggleBtn.addEventListener('click', (e) => {
              e.stopPropagation();
              const key = String(project.id);
              if (collapsedProjectIds.has(key)) collapsedProjectIds.delete(key);
              else collapsedProjectIds.add(key);
              renderProjects(container, clickable);
            });
          }

          if (clickable) {
            li.addEventListener('click', () => {
              activeProjectId = project.id;
              container.querySelectorAll('.project-item.active').forEach(x => x.classList.remove('active'));
              li.classList.add('active');

              const focusRefType = document.getElementById('focusRefType');
              const focusRefId = document.getElementById('focusRefId');
              if (focusRefType && focusRefId) {
                focusRefType.value = normalizedType;
                focusRefId.value = project.id;
                document.getElementById('startForm')?.scrollIntoView({ behavior: 'smooth' });
              }

              if (isSchemaUIAvailable()) {
                schemaProjectSelect.value = String(project.id);
                refreshSchemaSelectors();
              }
            });
          }

          li.appendChild(content);
          container.appendChild(li);

          if (hasChildren && !isCollapsed) {
            renderNode(project.id, depth + 1);
          }
        });
      }

      renderNode('root', 0);
    })
    .catch(err => {
      console.error("Error cargando projects:", err);
      if (container) {
        container.innerHTML = `<li class="error">Error loading projects: ${err.message}</li>`;
      }
    });
}

// Cargar en Daily Notes (no clickable)
if (projectsList) {
  renderProjects(projectsList, false);
}

// Cargar en Pomodoro tab (clickable)
if (pomodoroProjectsList) {
  renderProjects(pomodoroProjectsList, true);
}

setupProjectSchemas();
setupProjectSchemasModal();

// ---- NEW PROJECT FORM ----
const newProjectBtn    = document.getElementById('newProjectBtn');
const newProjectForm   = document.getElementById('newProjectForm');
const newProjectCancel = document.getElementById('newProjectCancel');
const newProjectSave   = document.getElementById('newProjectSave');
const newProjectParent = document.getElementById('newProjectParent');

if (newProjectBtn) {
  // Populate parent select when form opens
  newProjectBtn.addEventListener('click', async () => {
    const visible = newProjectForm.style.display !== 'none';
    if (visible) {
      newProjectForm.style.display = 'none';
      return;
    }
    // Load projects into parent select
    newProjectParent.innerHTML = '<option value="">\u2014 No parent (root) \u2014</option>';
    try {
      const res = await fetch(PROJECTS_URL);
      const projects = await res.json();
      projects.forEach(p => {
        const o = document.createElement('option');
        o.value = p.id;
        o.textContent = '\u00a0'.repeat((String(p.path).match(/\./g) || []).length * 2) + p.name;
        newProjectParent.appendChild(o);
      });
    } catch (e) { console.error(e); }
    newProjectForm.style.display = 'flex';
    document.getElementById('newProjectName').focus();
  });

  newProjectCancel.addEventListener('click', () => {
    newProjectForm.style.display = 'none';
    document.getElementById('newProjectName').value = '';
    document.getElementById('newProjectDesc').value = '';
  });

  newProjectSave.addEventListener('click', async () => {
    const name = document.getElementById('newProjectName').value.trim();
    if (!name) { document.getElementById('newProjectName').focus(); return; }

    const payload = {
      name,
      parent_id: newProjectParent.value ? Number(newProjectParent.value) : null,
      type: document.getElementById('newProjectType').value,
      description: document.getElementById('newProjectDesc').value.trim() || null
    };

    try {
      newProjectSave.textContent = '...';
      const res = await fetch(PROJECTS_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      if (!res.ok) throw new Error(await res.text());

      // Reset & close form
      newProjectForm.style.display = 'none';
      document.getElementById('newProjectName').value = '';
      document.getElementById('newProjectDesc').value = '';
      newProjectSave.textContent = 'Save';

      // Reload list
      renderProjects(pomodoroProjectsList, true);
    } catch (err) {
      console.error(err);
      alert('Error creating project');
      newProjectSave.textContent = 'Save';
    }
  });

  // Submit on Enter in name field
  document.getElementById('newProjectName').addEventListener('keydown', e => {
    if (e.key === 'Enter') newProjectSave.click();
  });
}
