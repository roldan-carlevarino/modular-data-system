// ==========================================================================
// Intel "Add" modal — replaces the legacy chain of native prompt() dialogs.
//
// Hijacks #addConceptBtn (cloned to strip its original listener from
// intel.js) and shows a single modal with two tabs:
//   • Concept  → name input, contextual parent/project info.
//   • Block    → block-type chips, textarea (Source Serif), hint about the
//                first `# heading` becoming the block title.
//
// Keyboard:
//   Ctrl/Cmd+Enter → submit
//   Esc            → close
//
// Depends on globals defined in intel.js:
//   knowledgeState, KNOWLEDGE_API_BASE, fetchKnowledge, loadConcepts,
//   cachedConcepts, extractBlockTitle.
// ==========================================================================

(function () {
    const BLOCK_TYPES = ['definition', 'intuition', 'formula', 'example', 'warning', 'code'];
    let modalEl = null;
    let activeTab = 'block';   // last-used tab persists per page load
    let chosenType = 'definition';

    function $(sel, root) { return (root || document).querySelector(sel); }

    function findConceptName(id) {
        if (!id) return null;
        const tree = (typeof cachedConcepts !== 'undefined' && Array.isArray(cachedConcepts)) ? cachedConcepts : [];
        const stack = [...tree];
        while (stack.length) {
            const c = stack.pop();
            if (!c) continue;
            if (c.id === id) return c.name;
            if (Array.isArray(c.children)) stack.push(...c.children);
        }
        return null;
    }

    function buildModal() {
        if (modalEl) return modalEl;
        modalEl = document.createElement('div');
        modalEl.className = 'add-modal-backdrop';
        modalEl.innerHTML = `
            <div class="add-modal" role="dialog" aria-modal="true" aria-label="Add new content">
                <div class="add-modal__tabs">
                    <button type="button" data-tab="block" class="add-modal__tab">Block</button>
                    <button type="button" data-tab="concept" class="add-modal__tab">Concept</button>
                    <button type="button" class="add-modal__close" aria-label="Close">&times;</button>
                </div>

                <div class="add-modal__panel" data-panel="block">
                    <div class="add-modal__context"></div>
                    <div class="add-modal__chips" role="radiogroup" aria-label="Block type"></div>
                    <textarea class="add-modal__textarea" rows="9"
                        placeholder="Write the block content. First `# heading` becomes the block title."></textarea>
                    <div class="add-modal__hint">
                        <span><kbd>Ctrl</kbd>+<kbd>Enter</kbd> to save · <kbd>Esc</kbd> to cancel</span>
                        <span class="add-modal__error" hidden></span>
                    </div>
                    <div class="add-modal__actions">
                        <button type="button" class="add-modal__btn add-modal__btn--ghost" data-action="cancel">Cancel</button>
                        <button type="button" class="add-modal__btn add-modal__btn--primary" data-action="save-block">Add block</button>
                    </div>
                </div>

                <div class="add-modal__panel" data-panel="concept" hidden>
                    <div class="add-modal__context"></div>
                    <input type="text" class="add-modal__input" placeholder="Concept name…" />
                    <div class="add-modal__hint">
                        <span><kbd>Enter</kbd> to save · <kbd>Esc</kbd> to cancel</span>
                        <span class="add-modal__error" hidden></span>
                    </div>
                    <div class="add-modal__actions">
                        <button type="button" class="add-modal__btn add-modal__btn--ghost" data-action="cancel">Cancel</button>
                        <button type="button" class="add-modal__btn add-modal__btn--primary" data-action="save-concept">Add concept</button>
                    </div>
                </div>
            </div>
        `;

        // Chips
        const chipsBox = $('.add-modal__chips', modalEl);
        BLOCK_TYPES.forEach(t => {
            const chip = document.createElement('button');
            chip.type = 'button';
            chip.className = 'add-modal__chip';
            chip.dataset.type = t;
            chip.textContent = t;
            chip.addEventListener('click', () => setChosenType(t));
            chipsBox.appendChild(chip);
        });

        // Wire up tab switching
        modalEl.querySelectorAll('.add-modal__tab').forEach(btn => {
            btn.addEventListener('click', () => switchTab(btn.dataset.tab));
        });

        // Close interactions
        $('.add-modal__close', modalEl).addEventListener('click', closeModal);
        modalEl.querySelectorAll('[data-action="cancel"]').forEach(b =>
            b.addEventListener('click', closeModal));
        modalEl.addEventListener('click', e => {
            if (e.target === modalEl) closeModal();
        });

        // Save buttons
        $('[data-action="save-block"]', modalEl).addEventListener('click', submitBlock);
        $('[data-action="save-concept"]', modalEl).addEventListener('click', submitConcept);

        // Keyboard inside modal
        modalEl.addEventListener('keydown', e => {
            if (e.key === 'Escape') {
                e.preventDefault();
                closeModal();
                return;
            }
            const isSubmit = (e.key === 'Enter') && (e.ctrlKey || e.metaKey);
            if (isSubmit) {
                e.preventDefault();
                if (activeTab === 'block') submitBlock();
                else submitConcept();
            } else if (e.key === 'Enter' && activeTab === 'concept' && e.target.classList.contains('add-modal__input')) {
                e.preventDefault();
                submitConcept();
            }
        });

        document.body.appendChild(modalEl);
        return modalEl;
    }

    function setChosenType(t) {
        chosenType = t;
        modalEl.querySelectorAll('.add-modal__chip').forEach(c => {
            c.classList.toggle('is-active', c.dataset.type === t);
        });
    }

    function switchTab(tab) {
        activeTab = tab;
        modalEl.querySelectorAll('.add-modal__tab').forEach(b => {
            b.classList.toggle('is-active', b.dataset.tab === tab);
        });
        modalEl.querySelectorAll('.add-modal__panel').forEach(p => {
            p.hidden = p.dataset.panel !== tab;
        });
        // Focus the right field
        setTimeout(() => {
            if (tab === 'block') $('.add-modal__textarea', modalEl).focus();
            else $('.add-modal__input', modalEl).focus();
        }, 30);
    }

    function refreshContext() {
        const state = (typeof knowledgeState !== 'undefined') ? knowledgeState : {};
        const conceptId = state.concept_id;
        const conceptName = findConceptName(conceptId);
        const projectId = state.project_id;
        const mode = state.mode;

        const blockCtx = modalEl.querySelector('[data-panel="block"] .add-modal__context');
        const conceptCtx = modalEl.querySelector('[data-panel="concept"] .add-modal__context');

        // Block context: must have a concept
        if (!conceptId) {
            blockCtx.innerHTML = `<span class="add-modal__warn">Select a concept first to add a block.</span>`;
            $('[data-action="save-block"]', modalEl).disabled = true;
            $('.add-modal__textarea', modalEl).disabled = true;
        } else {
            const parts = [`<span class="add-modal__pill">Concept · ${conceptName || ('#' + conceptId)}</span>`];
            if (projectId) parts.push(`<span class="add-modal__pill">Project · #${projectId}</span>`);
            if (mode) parts.push(`<span class="add-modal__pill">Mode · ${mode}</span>`);
            blockCtx.innerHTML = parts.join('');
            $('[data-action="save-block"]', modalEl).disabled = false;
            $('.add-modal__textarea', modalEl).disabled = false;
        }

        // Concept context: shows parent if we're nested, else "top-level"
        if (conceptId) {
            conceptCtx.innerHTML = `<span class="add-modal__pill">Parent · ${conceptName || ('#' + conceptId)}</span>`;
        } else {
            conceptCtx.innerHTML = `<span class="add-modal__pill add-modal__pill--muted">Top-level concept</span>`;
        }
    }

    function clearErrors() {
        modalEl.querySelectorAll('.add-modal__error').forEach(el => {
            el.hidden = true; el.textContent = '';
        });
    }

    function showError(panel, msg) {
        const el = modalEl.querySelector(`[data-panel="${panel}"] .add-modal__error`);
        if (!el) return;
        el.textContent = msg;
        el.hidden = false;
    }

    function openModal() {
        buildModal();
        refreshContext();
        const state = (typeof knowledgeState !== 'undefined') ? knowledgeState : {};
        // If no concept yet, force the concept tab.
        const startTab = state.concept_id ? activeTab : 'concept';
        switchTab(startTab);
        setChosenType(chosenType);
        clearErrors();
        modalEl.classList.add('is-open');
        document.body.classList.add('add-modal-open');
    }

    function closeModal() {
        if (!modalEl) return;
        modalEl.classList.remove('is-open');
        document.body.classList.remove('add-modal-open');
        // Reset transient inputs but remember tab + chosen type
        $('.add-modal__textarea', modalEl).value = '';
        $('.add-modal__input', modalEl).value = '';
        clearErrors();
    }

    async function submitBlock() {
        clearErrors();
        const state = (typeof knowledgeState !== 'undefined') ? knowledgeState : {};
        const conceptId = state.concept_id;
        if (!conceptId) {
            showError('block', 'No concept selected.');
            return;
        }
        const textarea = $('.add-modal__textarea', modalEl);
        const content = textarea.value.trim();
        if (!content) {
            showError('block', 'Content cannot be empty.');
            textarea.focus();
            return;
        }
        const btn = $('[data-action="save-block"]', modalEl);
        btn.disabled = true;
        btn.textContent = 'Saving…';
        try {
            const titleFn = (typeof extractBlockTitle === 'function') ? extractBlockTitle : () => null;
            const apiBase = (typeof KNOWLEDGE_API_BASE !== 'undefined') ? KNOWLEDGE_API_BASE : '';
            const res = await fetch(`${apiBase}/knowledge/block/new`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    concept_id: conceptId,
                    content,
                    block_type: chosenType,
                    project_id: state.project_id || null,
                    mode: state.mode || null,
                    name: titleFn(content)
                })
            });
            if (!res.ok) throw new Error(await res.text());
            closeModal();
            if (typeof fetchKnowledge === 'function') fetchKnowledge();
        } catch (err) {
            console.error('Add block failed:', err);
            showError('block', 'Failed to create block.');
        } finally {
            btn.disabled = false;
            btn.textContent = 'Add block';
        }
    }

    async function submitConcept() {
        clearErrors();
        const input = $('.add-modal__input', modalEl);
        const name = input.value.trim();
        if (!name) {
            showError('concept', 'Name cannot be empty.');
            input.focus();
            return;
        }
        const btn = $('[data-action="save-concept"]', modalEl);
        btn.disabled = true;
        btn.textContent = 'Saving…';
        try {
            const state = (typeof knowledgeState !== 'undefined') ? knowledgeState : {};
            const apiBase = (typeof KNOWLEDGE_API_BASE !== 'undefined') ? KNOWLEDGE_API_BASE : '';
            const res = await fetch(`${apiBase}/knowledge/concepts/new`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    name,
                    parent_concept_id: state.concept_id || null,
                    project_id: state.project_id || null
                })
            });
            if (!res.ok) throw new Error(await res.text());
            closeModal();
            if (typeof loadConcepts === 'function') loadConcepts();
        } catch (err) {
            console.error('Add concept failed:', err);
            showError('concept', 'Failed to create concept.');
        } finally {
            btn.disabled = false;
            btn.textContent = 'Add concept';
        }
    }

    // Replace the existing #addConceptBtn handler from intel.js. We use
    // capture-phase delegation on document so we don't fight whatever order
    // intel.js attaches its own listener — we always intercept first AND we
    // stop propagation so the legacy prompt() chain never fires.
    function installInterceptor() {
        if (window.__intelAddModalInstalled) return;
        window.__intelAddModalInstalled = true;
        document.addEventListener('click', (e) => {
            const btn = e.target && e.target.closest && e.target.closest('#addConceptBtn');
            if (!btn) return;
            e.preventDefault();
            e.stopImmediatePropagation();
            openModal();
        }, true); // useCapture = true → runs before bubbling listeners
        // Cosmetic: refresh tooltip + label once the DOM is ready
        const apply = () => {
            const b = document.getElementById('addConceptBtn');
            if (b) b.title = 'Add concept or block';
        };
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', apply, { once: true });
        } else {
            apply();
        }
    }

    installInterceptor();

    // Expose for debugging / external triggers (e.g. a future keyboard shortcut)
    window.openIntelAddModal = openModal;
})();
