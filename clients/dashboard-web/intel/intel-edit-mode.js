/* =====================================================================
   intel-edit-mode.js — Hover-to-edit shortcut for Intel blocks.

   Behaviour:
     • The knowledge viewer always shows the rendered (Overleaf) view.
     • Hover a block, press Ctrl/Cmd + E  → that single block flips to
       its editor (textarea + toolbar). All other blocks stay rendered.
     • Inside the editor:
         Ctrl/Cmd + S   → save (existing handler in intel-preview.js)
         Ctrl/Cmd + Enter → toggle live preview pane (intel-preview.js)
         Esc            → cancel edit, restore the rendered view
                          without saving.

   Implementation: re-uses the existing `handleEditClick` from
   intel.js by synthesising a fake edit button with the right
   dataset, so the same edit/save pipeline (B2 images, post-its,
   spreadsheet/diagram toggles, KaTeX, Mermaid, Plotly) is used.
   ===================================================================== */
(function () {
  'use strict';

  let lastMouseX = 0, lastMouseY = 0;

  document.addEventListener('mousemove', (e) => {
    lastMouseX = e.clientX;
    lastMouseY = e.clientY;
  }, { passive: true });

  function blockUnderCursor() {
    const el = document.elementFromPoint(lastMouseX, lastMouseY);
    if (!el || !el.closest) return null;
    const block = el.closest('.knowledge-block');
    if (!block) return null;
    // Must live inside the knowledge viewer
    if (!block.closest('#knowledgeViewer')) return null;
    return block;
  }

  function isInEditMode(block) {
    return !!block.querySelector('.block-editor');
  }

  function ensureFakeEditBtn(block) {
    if (block._fakeEditBtn && document.contains(block._fakeEditBtn)) {
      return block._fakeEditBtn;
    }
    const btn = document.createElement('button');
    btn.className = 'edit-btn hover-edit-fake';
    btn.style.display = 'none';
    btn.dataset.blockId = block.dataset.blockId;
    btn.textContent = 'Edit';
    // Bind the same handler the real Edit button uses, so a normal
    // .click() on this button (e.g. from Ctrl+S in intel-preview.js
    // or the toolbar Save button) triggers the save pipeline.
    if (typeof window.handleEditClick === 'function') {
      btn.addEventListener('click', window.handleEditClick);
    }
    block.appendChild(btn);
    block._fakeEditBtn = btn;
    return btn;
  }

  async function enterEdit(block) {
    if (typeof window.handleEditClick !== 'function') {
      // Fallback: if intel.js exposed handleEditClick differently
      const realBtn = block.querySelector('.edit-btn');
      if (realBtn) realBtn.click();
      return;
    }
    if (isInEditMode(block)) return;
    const btn = ensureFakeEditBtn(block);
    btn.textContent = 'Edit';
    block.classList.add('hover-editing');
    await window.handleEditClick({ target: btn });
    // Focus the textarea so the user can start typing immediately
    const ta = block.querySelector('.block-editor');
    if (ta) {
      ta.focus();
      // Place caret at end
      const len = ta.value.length;
      try { ta.setSelectionRange(len, len); } catch (_) {}
    }
  }

  async function saveEdit(block) {
    if (!isInEditMode(block)) return;
    const btn = block._fakeEditBtn;
    if (!btn) return;
    // handleEditClick saves when the button text reads 'Save'
    btn.textContent = 'Save';
    if (typeof window.handleEditClick === 'function') {
      await window.handleEditClick({ target: btn });
    }
    block.classList.remove('hover-editing');
    // Cleanup any preview pane left open
    const preview = block.querySelector(':scope > .block-live-preview');
    if (preview) preview.remove();
    block.classList.remove('has-live-preview');
  }

  async function cancelEdit(block) {
    if (!isInEditMode(block)) return;
    const contentDiv = block.querySelector('.block-content');
    if (!contentDiv) return;
    const original = contentDiv.dataset.originalContent || '';
    const blockId  = block.dataset.blockId;

    // Re-render the original content using existing helpers.
    try {
      const resolved = (typeof window.resolveB2Images === 'function')
        ? await window.resolveB2Images(original)
        : original;
      const parsed = (typeof window.parseNotes === 'function')
        ? window.parseNotes(resolved)
        : { body: resolved, notes: [] };
      const html = (typeof window.contentToHtml === 'function')
        ? window.contentToHtml(parsed.body)
        : parsed.body;
      contentDiv.innerHTML = html;

      // Re-render post-its
      block.querySelectorAll('.block-postit').forEach(p => p.remove());
      if (typeof window.addPostItToBlock === 'function') {
        parsed.notes.forEach((n, i) => window.addPostItToBlock(block, n, blockId, i));
      }

      if (typeof renderMathInElement === 'function') {
        renderMathInElement(contentDiv, {
          delimiters: [
            { left: '$$', right: '$$', display: true },
            { left: '$',  right: '$',  display: false }
          ],
          throwOnError: false, strict: false
        });
      }
      if (typeof window.renderCharts === 'function') {
        try { window.renderCharts(contentDiv); } catch (_) {}
      }
      // Mermaid
      if (typeof window.mermaid !== 'undefined') {
        contentDiv.querySelectorAll('.mermaid-render').forEach(async (div) => {
          const code = div.dataset.mermaidCode;
          if (!code) return;
          try {
            const id = 'mermaid-cancel-' + Date.now() + '-' + Math.random().toString(36).slice(2,7);
            const { svg } = await window.mermaid.render(id, code);
            div.innerHTML = svg;
          } catch (err) {
            div.innerHTML = `<pre class="diagram-error">\u26a0 ${err.message || 'Diagram error'}</pre>`;
          }
        });
      }
    } catch (err) {
      console.error('[intel-edit-mode] cancel error', err);
    }

    block.classList.remove('hover-editing');
    if (block._fakeEditBtn) block._fakeEditBtn.textContent = 'Edit';
    const preview = block.querySelector(':scope > .block-live-preview');
    if (preview) preview.remove();
    block.classList.remove('has-live-preview');
  }

  // ────────────────────────────── Keyboard ──────────────────────────
  document.addEventListener('keydown', (e) => {
    // Ctrl/Cmd + E → enter edit mode on hovered block
    if ((e.ctrlKey || e.metaKey) && (e.key === 'e' || e.key === 'E')) {
      // Don't intercept if the user is typing in another input
      const t = e.target;
      if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA') &&
          !(t.classList && t.classList.contains('block-editor'))) {
        return;
      }
      const block = blockUnderCursor();
      if (!block) return;
      e.preventDefault();
      e.stopPropagation();
      if (isInEditMode(block)) {
        // Already editing → toggle = save
        saveEdit(block);
      } else {
        enterEdit(block);
      }
      return;
    }

    // Esc inside the editor → cancel edit
    if (e.key === 'Escape') {
      const t = e.target;
      if (t && t.classList && t.classList.contains('block-editor')) {
        const block = t.closest('.knowledge-block');
        if (block && block.classList.contains('hover-editing')) {
          // If preview pane is open, intel-preview.js handles Esc to close it.
          // Only cancel the edit when no preview is open.
          if (!t.dataset.previewOpen) {
            e.preventDefault();
            e.stopPropagation();
            cancelEdit(block);
          }
        }
      }
    }
  }, true);

  // ────────────────────────────── Visual hint ───────────────────────
  function injectHint() {
    const viewer = document.getElementById('knowledgeViewer');
    if (!viewer || viewer.parentElement.querySelector('.intel-edit-hint')) return;
    const hint = document.createElement('div');
    hint.className = 'intel-edit-hint';
    hint.innerHTML = `
      <span><kbd>Ctrl</kbd>+<kbd>E</kbd> edit hovered block</span>
      <span class="sep">·</span>
      <span><kbd>Ctrl</kbd>+<kbd>S</kbd> save</span>
      <span class="sep">·</span>
      <span><kbd>Esc</kbd> cancel</span>
      <span class="sep">·</span>
      <span><kbd>Ctrl</kbd>+<kbd>Enter</kbd> preview</span>
    `;
    viewer.parentElement.insertBefore(hint, viewer);
  }

  function init() {
    // Re-inject hint after the viewer is rebuilt (new concept selected)
    // Hint disabled — see the System tab for the cheatsheet.
    console.info('[intel-edit-mode] ready — hover a block + Ctrl+E to edit');
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
