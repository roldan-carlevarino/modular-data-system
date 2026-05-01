/* =====================================================================
   intel-preview.js — Live preview + Save shortcuts for the block editor.

   Shortcuts (when the block editor textarea is focused):
     Ctrl/Cmd + Enter  → toggle / refresh live preview pane.
     Ctrl/Cmd + S      → click the Save button for this block.
     Esc               → close the live preview pane (if open).

   Implementation: a MutationObserver attaches the listener directly to
   each `.block-editor` textarea as soon as it is inserted into the
   DOM. Visible Preview / Save buttons are also injected into the
   existing block editor toolbar as a fallback.
   ===================================================================== */
(function () {
  'use strict';

  const DEBOUNCE_MS = 250;
  const HINT_TEXT   = 'Ctrl+Enter preview · Ctrl+S save · Esc close';
  const ATTACHED    = 'data-preview-bound';

  // ────────────────────────────── Preview rendering ─────────────────
  function findOrCreatePreview(textarea) {
    const block = textarea.closest('.knowledge-block');
    if (!block) return null;
    let preview = block.querySelector(':scope > .block-live-preview');
    if (preview) return preview;

    preview = document.createElement('div');
    preview.className = 'block-live-preview';
    preview.innerHTML = `
      <div class="block-live-preview-header">
        <span class="block-live-preview-title">Preview</span>
        <span class="block-live-preview-hint">${HINT_TEXT}</span>
        <button type="button" class="block-live-preview-close" title="Close preview (Esc)">&times;</button>
      </div>
      <div class="block-live-preview-body block-content"></div>
    `;
    block.appendChild(preview);
    preview.querySelector('.block-live-preview-close')
      .addEventListener('click', () => closePreview(textarea));
    return preview;
  }

  function closePreview(textarea) {
    const block = textarea && textarea.closest && textarea.closest('.knowledge-block');
    if (!block) return;
    block.classList.remove('has-live-preview');
    const preview = block.querySelector(':scope > .block-live-preview');
    if (preview) preview.remove();
    delete textarea.dataset.previewOpen;
  }

  function escapeHtml(s) {
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  async function renderPreview(textarea) {
    const preview = findOrCreatePreview(textarea);
    if (!preview) return;
    const body = preview.querySelector('.block-live-preview-body');
    if (!body) return;

    const raw = textarea.value || '';
    let html;
    try {
      if (typeof window.contentToHtml === 'function')      html = window.contentToHtml(raw);
      else if (typeof contentToHtml === 'function')        html = contentToHtml(raw);
      else                                                 html = raw.replace(/\n/g, '<br>');
    } catch (err) {
      body.innerHTML = `<pre class="block-live-preview-error">Preview error: ${escapeHtml((err && err.message) || String(err))}</pre>`;
      return;
    }
    body.innerHTML = html;

    if (typeof renderMathInElement === 'function') {
      try {
        renderMathInElement(body, {
          delimiters: [
            { left: '$$', right: '$$', display: true },
            { left: '$',  right: '$',  display: false }
          ],
          throwOnError: false, strict: false
        });
      } catch (_) { /* ignore */ }
    }

    if (typeof window.mermaid !== 'undefined') {
      const nodes = body.querySelectorAll('.mermaid-render');
      let i = 0;
      for (const div of nodes) {
        const code = div.dataset.mermaidCode;
        if (!code) continue;
        try {
          const id = 'mermaid-preview-' + Date.now() + '-' + (i++);
          const { svg } = await window.mermaid.render(id, code);
          div.innerHTML = svg;
        } catch (err) {
          div.innerHTML = `<pre class="diagram-error">\u26a0 ${escapeHtml((err && err.message) || 'Diagram error')}</pre>`;
        }
      }
    }

    if (typeof window.renderCharts === 'function') {
      try { window.renderCharts(body); } catch (_) {}
    }
  }

  const timers = new WeakMap();
  function schedulePreview(textarea) {
    if (!textarea.dataset.previewOpen) return;
    const prev = timers.get(textarea);
    if (prev) clearTimeout(prev);
    timers.set(textarea, setTimeout(() => renderPreview(textarea), DEBOUNCE_MS));
  }

  function togglePreview(textarea, forceOpen) {
    const block = textarea.closest('.knowledge-block');
    if (!block) return;
    const isOpen = !!textarea.dataset.previewOpen;
    if (isOpen && !forceOpen) {
      closePreview(textarea);
    } else {
      textarea.dataset.previewOpen = '1';
      block.classList.add('has-live-preview');
      renderPreview(textarea);
    }
  }

  function clickSave(textarea) {
    const block = textarea.closest('.knowledge-block');
    if (!block) return;
    const editBtn = block.querySelector('.edit-btn');
    if (editBtn && /save/i.test(editBtn.textContent || '')) editBtn.click();
  }

  // ────────────────────────────── Per-textarea binding ──────────────
  function handleKey(textarea, e) {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
      e.preventDefault(); e.stopPropagation();
      if (textarea.dataset.previewOpen) renderPreview(textarea);
      else togglePreview(textarea, true);
      return true;
    }
    if ((e.ctrlKey || e.metaKey) && (e.key === 's' || e.key === 'S')) {
      e.preventDefault(); e.stopPropagation();
      clickSave(textarea);
      return true;
    }
    if (e.key === 'Escape' && textarea.dataset.previewOpen) {
      e.preventDefault(); e.stopPropagation();
      closePreview(textarea);
      return true;
    }
    return false;
  }

  function bindTextarea(textarea) {
    if (!textarea || textarea.getAttribute(ATTACHED) === '1') return;
    textarea.setAttribute(ATTACHED, '1');

    textarea.addEventListener('keydown', (e) => handleKey(textarea, e));
    textarea.addEventListener('input',   () => schedulePreview(textarea));

    // Inject Preview / Save buttons into toolbar
    const toolbar = textarea.parentElement &&
                    textarea.parentElement.querySelector('.block-editor-toolbar');
    if (toolbar && !toolbar.querySelector('.btn-toggle-preview')) {
      const previewBtn = document.createElement('button');
      previewBtn.type = 'button';
      previewBtn.className = 'btn-toggle-preview';
      previewBtn.title = 'Toggle live preview (Ctrl+Enter)';
      previewBtn.textContent = 'Preview';
      previewBtn.addEventListener('click', (ev) => {
        ev.preventDefault();
        togglePreview(textarea);
        textarea.focus();
      });

      const saveBtn = document.createElement('button');
      saveBtn.type = 'button';
      saveBtn.className = 'btn-quick-save';
      saveBtn.title = 'Save block (Ctrl+S)';
      saveBtn.textContent = 'Save';
      saveBtn.addEventListener('click', (ev) => {
        ev.preventDefault();
        clickSave(textarea);
      });

      toolbar.appendChild(previewBtn);
      toolbar.appendChild(saveBtn);
    }
  }

  function scanAndBind(root) {
    const scope = root && root.querySelectorAll ? root : document;
    scope.querySelectorAll('textarea.block-editor').forEach(bindTextarea);
  }

  // ────────────────────────────── Boot ──────────────────────────────
  function init() {
    scanAndBind(document);

    const obs = new MutationObserver((mutations) => {
      for (const m of mutations) {
        for (const node of m.addedNodes) {
          if (node.nodeType !== 1) continue;
          if (node.matches && node.matches('textarea.block-editor')) bindTextarea(node);
          else if (node.querySelectorAll) scanAndBind(node);
        }
      }
    });
    obs.observe(document.body, { childList: true, subtree: true });

    // Capture-phase fallback (in case another handler stops propagation later).
    document.addEventListener('keydown', (e) => {
      const ta = e.target;
      if (!ta || !ta.classList || !ta.classList.contains('block-editor')) return;
      handleKey(ta, e);
    }, true);

    console.info('[intel-preview] ready — Ctrl+Enter preview · Ctrl+S save · Esc close');
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
