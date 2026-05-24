/* Library tab — papers, books, competitions */
(function () {
    'use strict';

    const API = "https://api-dashboard-production-fc05.up.railway.app";
    const $ = (id) => document.getElementById(id);

    // ---- Status options per type ----
    const STATUS_OPTIONS = {
        paper: ['wishlist', 'reading', 'done', 'archived'],
        book: ['wishlist', 'reading', 'done', 'archived'],
        competition: ['wishlist', 'upcoming', 'active', 'submitted', 'done', 'abandoned'],
    };

    // ---- State ----
    const state = {
        items: [],
        collections: [],
        projects: [],
        tags: [],
        filters: { type: '', status: '', q: '', collection_id: null, tag: null },
        selectedId: null,
        editing: null,        // null = new, otherwise existing id
        editingCollection: null, // null = new, otherwise collection object
        importDraft: null,
    };

    // ---- API helpers ----
    async function api(path, opts = {}) {
        const res = await fetch(`${API}${path}`, opts);
        if (!res.ok) {
            let msg = res.statusText;
            try { msg = (await res.json()).detail || msg; } catch (_) {}
            throw new Error(msg);
        }
        if (res.status === 204) return null;
        return res.json();
    }
    const apiJson = (path, method, body) => api(path, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    });

    // ---- Init ----
    function init() {
        if (!$('library')) return;

        // Top buttons
        $('libNewBtn').addEventListener('click', () => openItemModal(null));
        $('libImportBtn').addEventListener('click', openImportModal);
        $('libNewCollectionBtn').addEventListener('click', () => openCollectionModal(null));

        // Collection modal
        $('libColModalClose').addEventListener('click', closeCollectionModal);
        $('libColModalCancel').addEventListener('click', closeCollectionModal);
        $('libColModalSave').addEventListener('click', saveCollectionModal);
        $('libColModalDelete').addEventListener('click', deleteCollectionFromModal);

        // Search (debounced)
        let searchTimer = null;
        $('libSearch').addEventListener('input', (e) => {
            clearTimeout(searchTimer);
            const v = e.target.value;
            searchTimer = setTimeout(() => {
                state.filters.q = v.trim();
                loadItems();
            }, 250);
        });

        // Filter chips
        document.querySelectorAll('[data-group="lib-type"] .library__chip').forEach(btn => {
            btn.addEventListener('click', () => {
                document.querySelectorAll('[data-group="lib-type"] .library__chip').forEach(b => b.classList.remove('is-active'));
                btn.classList.add('is-active');
                state.filters.type = btn.dataset.value;
                loadItems();
            });
        });
        document.querySelectorAll('[data-group="lib-status"] .library__chip').forEach(btn => {
            btn.addEventListener('click', () => {
                document.querySelectorAll('[data-group="lib-status"] .library__chip').forEach(b => b.classList.remove('is-active'));
                btn.classList.add('is-active');
                state.filters.status = btn.dataset.value;
                loadItems();
            });
        });

        // Item modal
        $('libModalClose').addEventListener('click', closeItemModal);
        $('libModalCancel').addEventListener('click', closeItemModal);
        $('libModalSave').addEventListener('click', saveItemModal);
        document.querySelectorAll('[data-group="lib-modal-type"] .library__chip').forEach(btn => {
            btn.addEventListener('click', () => {
                document.querySelectorAll('[data-group="lib-modal-type"] .library__chip').forEach(b => b.classList.remove('is-active'));
                btn.classList.add('is-active');
                refreshStatusOptions(btn.dataset.value);
                toggleDateFields(btn.dataset.value);
            });
        });

        // Import modal
        $('libImportClose').addEventListener('click', closeImportModal);
        $('libImportCancel').addEventListener('click', closeImportModal);
        $('libImportFetch').addEventListener('click', fetchImport);
        $('libImportSave').addEventListener('click', saveImport);
        $('libImportInput').addEventListener('keydown', (e) => {
            if (e.key === 'Enter') { e.preventDefault(); fetchImport(); }
        });

        // Lazy load when the tab becomes active
        const tab = $('tab12');
        if (tab) {
            tab.addEventListener('change', () => { if (tab.checked) refreshAll(); });
            if (tab.checked) refreshAll();
        } else {
            refreshAll();
        }
    }

    async function refreshAll() {
        await Promise.all([loadProjects(), loadCollections(), loadTags(), loadStats(), loadItems()]);
    }

    // ---- Loaders ----
    async function loadItems() {
        const params = new URLSearchParams();
        if (state.filters.type) params.set('type', state.filters.type);
        if (state.filters.status) params.set('status', state.filters.status);
        if (state.filters.q) params.set('q', state.filters.q);
        if (state.filters.tag) params.set('tag', state.filters.tag);
        if (state.filters.collection_id) params.set('collection_id', state.filters.collection_id);
        try {
            state.items = await api(`/library/items?${params}`);
        } catch (e) {
            state.items = [];
            console.warn('library/items failed:', e);
        }
        renderList();
    }

    async function loadCollections() {
        try { state.collections = await api('/library/collections'); }
        catch (e) { state.collections = []; }
        renderCollections();
    }

    async function loadProjects() {
        try { state.projects = await api('/library/projects'); }
        catch (e) { state.projects = []; }
    }

    async function loadTags() {
        try { state.tags = await api('/library/tags'); }
        catch (e) { state.tags = []; }
        renderTags();
    }

    async function loadStats() {
        try {
            const s = await api('/library/stats');
            $('libStats').innerHTML = `
                <div><strong>${s.total}</strong> items</div>
                <div><strong>${s.with_files}</strong> with PDF</div>
            `;
        } catch (e) {
            $('libStats').textContent = '—';
        }
    }

    // ---- Renderers ----
    function renderList() {
        const list = $('libList');
        const empty = $('libEmpty');
        if (!state.items.length) {
            list.innerHTML = '';
            empty.style.display = 'block';
            return;
        }
        empty.style.display = 'none';
        list.innerHTML = state.items.map(it => `
            <article class="library__card ${state.selectedId === it.id ? 'is-selected' : ''}" data-id="${it.id}">
                <div class="library__card-head">
                    <span class="library__card-type library__card-type--${it.type}">${typeLabel(it.type)}</span>
                    <span class="library__card-status library__card-status--${it.status}">${it.status}</span>
                    ${it.year ? `<span class="library__card-year">${it.year}</span>` : ''}
                    ${dueBadge(it)}
                </div>
                <h3 class="library__card-title">${escapeHtml(it.title || '(untitled)')}</h3>
                ${it.authors && it.authors.length
                    ? `<div class="library__card-authors">${it.authors.slice(0, 3).map(a => escapeHtml(a.name || a)).join(', ')}${it.authors.length > 3 ? ' et al.' : ''}</div>`
                    : ''}
                <div class="library__card-meta">
                    ${it.tags && it.tags.length ? `<span>🏷 ${it.tags.slice(0, 3).map(escapeHtml).join(', ')}</span>` : ''}
                    ${it.notes_count ? `<span>📝 ${it.notes_count}</span>` : ''}
                    ${it.highlights_count ? `<span>✎ ${it.highlights_count}</span>` : ''}
                    ${it.file_path ? `<span title="Has PDF">📄</span>` : ''}
                    ${it.links && it.links.length ? `<span>🔗 ${it.links.length}</span>` : ''}
                </div>
            </article>
        `).join('');

        list.querySelectorAll('.library__card').forEach(card => {
            card.addEventListener('click', () => {
                const id = parseInt(card.dataset.id, 10);
                state.selectedId = id;
                renderList();
                renderDetail(id);
            });
        });
    }

    function renderCollections() {
        const ul = $('libCollectionList');
        const all = `<li class="library__col-item ${state.filters.collection_id === null ? 'is-active' : ''}" data-id=""><span>All items</span><em>${state.items.length}</em></li>`;
        ul.innerHTML = all + state.collections.map(c => `
            <li class="library__col-item ${state.filters.collection_id === c.id ? 'is-active' : ''}" data-id="${c.id}">
                <span class="library__col-name">${escapeHtml(c.name)}${c.project_name ? `<span class="library__col-proj" title="Linked to project">· ${escapeHtml(c.project_name)}</span>` : ''}</span>
                <span class="library__col-right">
                    <em>${c.item_count}</em>
                    <button type="button" class="library__col-edit" data-edit-id="${c.id}" title="Edit collection">⚙</button>
                </span>
            </li>
        `).join('');
        ul.querySelectorAll('.library__col-item').forEach(li => {
            li.addEventListener('click', (e) => {
                if (e.target.closest('.library__col-edit')) return; // handled below
                const v = li.dataset.id;
                state.filters.collection_id = v ? parseInt(v, 10) : null;
                renderCollections();
                loadItems();
            });
        });
        ul.querySelectorAll('.library__col-edit').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                const id = parseInt(btn.dataset.editId, 10);
                const col = state.collections.find(c => c.id === id);
                if (col) openCollectionModal(col);
            });
        });
    }

    function renderTags() {
        const el = $('libTagCloud');
        if (!state.tags.length) { el.innerHTML = '<span class="library__hint">No tags yet.</span>'; return; }
        el.innerHTML = state.tags.slice(0, 30).map(t => `
            <button class="library__tag ${state.filters.tag === t.tag ? 'is-active' : ''}" data-tag="${escapeHtml(t.tag)}">
                ${escapeHtml(t.tag)} <em>${t.count}</em>
            </button>
        `).join('');
        el.querySelectorAll('.library__tag').forEach(b => {
            b.addEventListener('click', () => {
                state.filters.tag = (state.filters.tag === b.dataset.tag) ? null : b.dataset.tag;
                renderTags();
                loadItems();
            });
        });
    }

    async function renderDetail(id) {
        const det = $('libDetail');
        det.innerHTML = '<div class="library__detail-empty">Loading…</div>';
        let item, notes = [], highlights = [];
        try {
            [item, notes, highlights] = await Promise.all([
                api(`/library/items/${id}`),
                api(`/library/items/${id}/notes`),
                api(`/library/items/${id}/highlights`),
            ]);
        } catch (e) {
            det.innerHTML = `<div class="library__detail-empty">Error: ${escapeHtml(e.message)}</div>`;
            return;
        }
        const authorsStr = (item.authors || []).map(a => escapeHtml(a.name || a)).join(', ');
        det.innerHTML = `
            <div class="library__detail-head">
                <span class="library__card-type library__card-type--${item.type}">${typeLabel(item.type)}</span>
                <div class="library__detail-actions">
                    <button class="library__icon-btn" id="libDetEdit" title="Edit">✎</button>
                    <button class="library__icon-btn" id="libDetDelete" title="Delete">🗑</button>
                </div>
            </div>
            <h3 class="library__detail-title">${escapeHtml(item.title || '(untitled)')}</h3>
            <div class="library__detail-meta">
                ${authorsStr ? `<div>${authorsStr}</div>` : ''}
                ${item.year ? `<div>${item.year}</div>` : ''}
                <div>Status:
                    <select id="libDetStatus" class="library__inline-select">
                        ${(STATUS_OPTIONS[item.type] || []).map(s =>
                            `<option value="${s}" ${s === item.status ? 'selected' : ''}>${s}</option>`).join('')}
                    </select>
                </div>
                ${item.external_id ? `<div class="library__hint">${escapeHtml(item.external_id)}</div>` : ''}
                ${item.primary_url
                    ? `<div><a href="${escapeAttr(item.primary_url)}" target="_blank" rel="noopener">Open primary URL ↗</a></div>`
                    : ''}
                ${(item.start_date || item.due_date)
                    ? `<div>${item.start_date ? `Start: <strong>${item.start_date}</strong>` : ''}${item.start_date && item.due_date ? ' · ' : ''}${item.due_date ? `Due: <strong>${item.due_date}</strong> ${dueBadge(item)}` : ''}</div>`
                    : ''}
            </div>
            ${item.summary
                ? `<details class="library__detail-summary"><summary>Summary</summary><p>${escapeHtml(item.summary)}</p></details>`
                : ''}
            ${item.tags && item.tags.length
                ? `<div class="library__detail-tags">${item.tags.map(t => `<span class="library__tag">${escapeHtml(t)}</span>`).join('')}</div>`
                : ''}

            <section class="library__sect">
                <h4>Links <button class="library__icon-btn" id="libAddLinkBtn">+</button></h4>
                <ul class="library__links">
                    ${(item.links || []).map(l => `
                        <li>
                            <a href="${escapeAttr(l.url)}" target="_blank" rel="noopener">${escapeHtml(l.label)}</a>
                            <span class="library__link-kind">${escapeHtml(l.kind)}</span>
                            <button class="library__icon-btn library__link-del" data-id="${l.id}" title="Remove">×</button>
                        </li>
                    `).join('') || '<li class="library__hint">No links yet.</li>'}
                </ul>
            </section>

            <section class="library__sect">
                <h4>PDF</h4>
                <div class="library__file">
                    ${item.file_path
                        ? `<button class="library__btn library__btn--ghost" id="libOpenPdf">Open PDF ↗</button>
                           <button class="library__btn library__btn--ghost" id="libDeletePdf">Remove</button>
                           <div class="library__hint">${escapeHtml(item.file_path)}</div>`
                        : `<input type="file" id="libUploadPdf" accept="application/pdf">
                           <span class="library__hint">No PDF attached.</span>`}
                </div>
            </section>

            <section class="library__sect">
                <h4>Notes <button class="library__icon-btn" id="libAddNoteBtn">+</button></h4>
                <div id="libNotes" class="library__notes">
                    ${notes.map(n => `
                        <div class="library__note" data-id="${n.id}">
                            <textarea class="library__note-body">${escapeHtml(n.body_md)}</textarea>
                            <div class="library__note-foot">
                                <span class="library__hint">${formatTime(n.updated_at)}</span>
                                <button class="library__btn library__btn--ghost library__note-save">Save</button>
                                <button class="library__icon-btn library__note-del">🗑</button>
                            </div>
                        </div>
                    `).join('') || '<div class="library__hint">No notes yet.</div>'}
                </div>
            </section>

            <section class="library__sect">
                <h4>Highlights <button class="library__icon-btn" id="libAddHlBtn">+</button></h4>
                <div id="libHighlights" class="library__highlights">
                    ${highlights.map(h => `
                        <blockquote class="library__hl" data-id="${h.id}">
                            ${h.locator ? `<div class="library__hl-loc">${escapeHtml(h.locator)}</div>` : ''}
                            <p>${escapeHtml(h.quote)}</p>
                            ${h.comment ? `<div class="library__hl-comment">${escapeHtml(h.comment)}</div>` : ''}
                            <button class="library__icon-btn library__hl-del">×</button>
                        </blockquote>
                    `).join('') || '<div class="library__hint">No highlights yet.</div>'}
                </div>
            </section>
        `;

        wireDetail(item, notes, highlights);
    }

    function wireDetail(item) {
        $('libDetEdit').addEventListener('click', () => openItemModal(item.id));
        $('libDetDelete').addEventListener('click', async () => {
            if (!confirm(`Delete "${item.title}"?`)) return;
            try {
                await api(`/library/items/${item.id}`, { method: 'DELETE' });
                state.selectedId = null;
                $('libDetail').innerHTML = '<div class="library__detail-empty">Select an item to see details.</div>';
                await refreshAll();
            } catch (e) { alert(`Delete failed: ${e.message}`); }
        });
        $('libDetStatus').addEventListener('change', async (e) => {
            try {
                await apiJson(`/library/items/${item.id}`, 'PATCH', { status: e.target.value });
                await loadItems();
            } catch (err) { alert(err.message); }
        });

        $('libAddLinkBtn').addEventListener('click', async () => {
            const url = prompt('URL?');
            if (!url) return;
            const label = prompt('Label?', url) || url;
            const kind = prompt('Kind (main, docs, leaderboard, github, discord, writeup, video, pdf):', 'main') || 'main';
            try {
                await apiJson(`/library/items/${item.id}/links`, 'POST', { url, label, kind });
                renderDetail(item.id);
            } catch (e) { alert(e.message); }
        });
        document.querySelectorAll('.library__link-del').forEach(b => {
            b.addEventListener('click', async () => {
                if (!confirm('Remove this link?')) return;
                try {
                    await api(`/library/links/${b.dataset.id}`, { method: 'DELETE' });
                    renderDetail(item.id);
                } catch (e) { alert(e.message); }
            });
        });

        // PDF
        const upl = $('libUploadPdf');
        if (upl) {
            upl.addEventListener('change', async (e) => {
                const f = e.target.files[0];
                if (!f) return;
                const fd = new FormData();
                fd.append('file', f);
                try {
                    await fetch(`${API}/library/items/${item.id}/file`, { method: 'POST', body: fd });
                    await Promise.all([loadItems(), loadStats()]);
                    renderDetail(item.id);
                } catch (err) { alert(err.message); }
            });
        }
        const openPdf = $('libOpenPdf');
        if (openPdf) {
            openPdf.addEventListener('click', async () => {
                try {
                    const r = await api(`/library/items/${item.id}/file-url`);
                    window.open(r.url, '_blank');
                } catch (e) { alert(e.message); }
            });
        }
        const delPdf = $('libDeletePdf');
        if (delPdf) {
            delPdf.addEventListener('click', async () => {
                if (!confirm('Remove the PDF from storage?')) return;
                try {
                    await api(`/library/items/${item.id}/file`, { method: 'DELETE' });
                    await Promise.all([loadItems(), loadStats()]);
                    renderDetail(item.id);
                } catch (e) { alert(e.message); }
            });
        }

        // Notes
        $('libAddNoteBtn').addEventListener('click', async () => {
            try {
                await apiJson(`/library/items/${item.id}/notes`, 'POST', { body_md: '' });
                renderDetail(item.id);
            } catch (e) { alert(e.message); }
        });
        document.querySelectorAll('.library__note-save').forEach(btn => {
            btn.addEventListener('click', async () => {
                const note = btn.closest('.library__note');
                const body = note.querySelector('.library__note-body').value;
                try {
                    await apiJson(`/library/notes/${note.dataset.id}`, 'PATCH', { body_md: body });
                    btn.textContent = 'Saved ✓';
                    setTimeout(() => { btn.textContent = 'Save'; }, 1200);
                } catch (e) { alert(e.message); }
            });
        });
        document.querySelectorAll('.library__note-del').forEach(btn => {
            btn.addEventListener('click', async () => {
                const note = btn.closest('.library__note');
                if (!confirm('Delete this note?')) return;
                try {
                    await api(`/library/notes/${note.dataset.id}`, { method: 'DELETE' });
                    renderDetail(item.id);
                } catch (e) { alert(e.message); }
            });
        });

        // Highlights
        $('libAddHlBtn').addEventListener('click', async () => {
            const quote = prompt('Quote?');
            if (!quote) return;
            const locator = prompt('Locator (page, section, timestamp)?') || null;
            const comment = prompt('Comment?') || null;
            try {
                await apiJson(`/library/items/${item.id}/highlights`, 'POST', { quote, locator, comment });
                renderDetail(item.id);
            } catch (e) { alert(e.message); }
        });
        document.querySelectorAll('.library__hl-del').forEach(btn => {
            btn.addEventListener('click', async () => {
                const hl = btn.closest('.library__hl');
                if (!confirm('Delete this highlight?')) return;
                try {
                    await api(`/library/highlights/${hl.dataset.id}`, { method: 'DELETE' });
                    renderDetail(item.id);
                } catch (e) { alert(e.message); }
            });
        });
    }

    // ---- Item modal ----
    function openItemModal(id) {
        state.editing = id;
        const modal = $('libModal');
        $('libModalTitle').textContent = id ? 'Edit item' : 'New item';
        if (id) {
            const it = state.items.find(x => x.id === id);
            if (!it) return;
            setActiveChip('lib-modal-type', it.type);
            refreshStatusOptions(it.type, it.status);
            toggleDateFields(it.type);
            $('libFTitle').value = it.title || '';
            $('libFYear').value = it.year || '';
            $('libFAuthors').value = (it.authors || []).map(a => a.name || a).join(', ');
            $('libFUrl').value = it.primary_url || '';
            $('libFTags').value = (it.tags || []).join(', ');
            $('libFSummary').value = it.summary || '';
            $('libFStart').value = it.start_date || '';
            $('libFDue').value = it.due_date || '';
            renderCollectionsChecklist((it.collections || []).map(c => c.id));
        } else {
            setActiveChip('lib-modal-type', 'paper');
            refreshStatusOptions('paper');
            toggleDateFields('paper');
            ['libFTitle', 'libFYear', 'libFAuthors', 'libFUrl', 'libFTags', 'libFSummary', 'libFStart', 'libFDue']
                .forEach(k => $(k).value = '');
            renderCollectionsChecklist([]);
        }
        modal.style.display = 'flex';
        $('libFTitle').focus();
    }
    function closeItemModal() { $('libModal').style.display = 'none'; }

    function renderCollectionsChecklist(selectedIds) {
        const el = $('libFCollections');
        if (!el) return;
        const sel = new Set((selectedIds || []).map(Number));
        if (!state.collections.length) {
            el.innerHTML = '<em class="library__hint">No collections yet. Create one from the sidebar.</em>';
            return;
        }
        el.innerHTML = state.collections.map(c => `
            <label class="library__chk">
                <input type="checkbox" value="${c.id}" ${sel.has(c.id) ? 'checked' : ''}>
                <span>${escapeHtml(c.name)}</span>
            </label>`).join('');
    }

    function getSelectedCollectionIds() {
        return Array.from(document.querySelectorAll('#libFCollections input[type=checkbox]:checked'))
            .map(i => parseInt(i.value, 10));
    }

    function refreshStatusOptions(type, current) {
        const sel = $('libFStatus');
        sel.innerHTML = (STATUS_OPTIONS[type] || []).map(s =>
            `<option value="${s}" ${s === current ? 'selected' : ''}>${s}</option>`).join('');
    }

    function toggleDateFields(type) {
        const el = $('libFDates');
        if (el) el.style.display = (type === 'competition') ? 'flex' : 'none';
    }

    function setActiveChip(group, value) {
        document.querySelectorAll(`[data-group="${group}"] .library__chip`).forEach(b => {
            b.classList.toggle('is-active', b.dataset.value === value);
        });
    }

    async function saveItemModal() {
        const type = document.querySelector('[data-group="lib-modal-type"] .library__chip.is-active')?.dataset.value || 'paper';
        const title = $('libFTitle').value.trim();
        if (!title) { alert('Title is required'); return; }
        const yearVal = $('libFYear').value.trim();
        const payload = {
            type,
            title,
            year: yearVal ? parseInt(yearVal, 10) : null,
            status: $('libFStatus').value,
            authors: $('libFAuthors').value.split(',').map(s => ({ name: s.trim() })).filter(a => a.name),
            primary_url: $('libFUrl').value.trim() || null,
            summary: $('libFSummary').value.trim() || null,
            tags: $('libFTags').value.split(',').map(s => s.trim()).filter(Boolean),
            collection_ids: getSelectedCollectionIds(),
            start_date: (type === 'competition' ? ($('libFStart').value || null) : null),
            due_date: (type === 'competition' ? ($('libFDue').value || null) : null),
        };
        try {
            if (state.editing) {
                await apiJson(`/library/items/${state.editing}`, 'PATCH', payload);
            } else {
                const r = await apiJson('/library/items', 'POST', payload);
                state.selectedId = r.id;
            }
            closeItemModal();
            await refreshAll();
            if (state.selectedId) renderDetail(state.selectedId);
        } catch (e) { alert(`Save failed: ${e.message}`); }
    }

    // ---- Import modal ----
    function openImportModal() {
        state.importDraft = null;
        $('libImportInput').value = '';
        $('libImportPreview').innerHTML = '';
        $('libImportSave').disabled = true;
        $('libImportModal').style.display = 'flex';
        $('libImportInput').focus();
    }
    function closeImportModal() { $('libImportModal').style.display = 'none'; }

    async function fetchImport() {
        const value = $('libImportInput').value.trim();
        if (!value) return;
        $('libImportPreview').innerHTML = '<div class="library__hint">Fetching…</div>';
        $('libImportSave').disabled = true;
        try {
            const r = await apiJson('/library/import', 'POST', { value, save: false });
            state.importDraft = r.draft;
            const d = r.draft;
            $('libImportPreview').innerHTML = `
                <div class="library__import-card">
                    <div class="library__card-type library__card-type--${d.type}">${typeLabel(d.type)}</div>
                    <h4>${escapeHtml(d.title || '(untitled)')}</h4>
                    <div class="library__hint">${(d.authors || []).map(a => escapeHtml(a.name)).join(', ')}${d.year ? ` · ${d.year}` : ''}</div>
                    ${d.summary ? `<p class="library__import-summary">${escapeHtml(d.summary).slice(0, 400)}${d.summary.length > 400 ? '…' : ''}</p>` : ''}
                    <div class="library__hint">${escapeHtml(d.external_id || '')}</div>
                </div>
            `;
            $('libImportSave').disabled = false;
        } catch (e) {
            $('libImportPreview').innerHTML = `<div class="library__error">${escapeHtml(e.message)}</div>`;
        }
    }

    async function saveImport() {
        const value = $('libImportInput').value.trim();
        if (!value) return;
        try {
            const r = await apiJson('/library/import', 'POST', { value, save: true });
            state.selectedId = r.id;
            closeImportModal();
            await refreshAll();
            renderDetail(r.id);
        } catch (e) { alert(`Save failed: ${e.message}`); }
    }

    // ---- Collections ----
    function openCollectionModal(col) {
        state.editingCollection = col || null;
        $('libColModalTitle').textContent = col ? 'Edit collection' : 'New collection';
        $('libColName').value = col ? (col.name || '') : '';
        const sel = $('libColProject');
        sel.innerHTML = '<option value="">— Not linked to a project —</option>' +
            state.projects.map(p => `<option value="${p.id}">${escapeHtml(p.name)}</option>`).join('');
        sel.value = (col && col.project_id != null) ? String(col.project_id) : '';
        $('libColModalDelete').style.display = col ? 'inline-block' : 'none';
        $('libColModal').style.display = 'flex';
        setTimeout(() => $('libColName').focus(), 0);
    }
    function closeCollectionModal() { $('libColModal').style.display = 'none'; }

    async function saveCollectionModal() {
        const name = $('libColName').value.trim();
        if (!name) { alert('Name is required'); return; }
        const projVal = $('libColProject').value;
        const project_id = projVal ? parseInt(projVal, 10) : null;
        try {
            if (state.editingCollection) {
                await apiJson(`/library/collections/${state.editingCollection.id}`, 'PATCH',
                    { name, project_id });
            } else {
                await apiJson('/library/collections', 'POST', { name, project_id });
            }
            closeCollectionModal();
            await loadCollections();
        } catch (e) { alert(e.message); }
    }

    async function deleteCollectionFromModal() {
        if (!state.editingCollection) return;
        if (!confirm(`Delete collection "${state.editingCollection.name}"? Items are kept; only the collection link is removed.`)) return;
        try {
            await api(`/library/collections/${state.editingCollection.id}`, { method: 'DELETE' });
            closeCollectionModal();
            if (state.filters.collection_id === state.editingCollection.id) {
                state.filters.collection_id = null;
            }
            await loadCollections();
            await loadItems();
        } catch (e) { alert(e.message); }
    }

    // ---- Utils ----
    function typeLabel(t) {
        return { paper: 'Paper', book: 'Book', competition: 'Competition' }[t] || t;
    }
    function dueBadge(it) {
        if (!it.due_date) return '';
        const today = new Date(); today.setHours(0, 0, 0, 0);
        const due = new Date(it.due_date + 'T00:00:00');
        const days = Math.round((due - today) / 86400000);
        let cls = 'far', label;
        if (days < 0) { cls = 'over'; label = `${-days}d ago`; }
        else if (days === 0) { cls = 'soon'; label = 'today'; }
        else if (days <= 7) { cls = 'soon'; label = `${days}d`; }
        else if (days <= 30) { cls = 'mid'; label = `${days}d`; }
        else { label = `${days}d`; }
        return `<span class="library__card-due library__card-due--${cls}" title="Due ${it.due_date}">⏳ ${label}</span>`;
    }
    function escapeHtml(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }
    function escapeAttr(s) { return escapeHtml(s); }
    function formatTime(iso) {
        if (!iso) return '';
        const d = new Date(iso);
        const diff = (Date.now() - d.getTime()) / 1000;
        if (diff < 60) return 'just now';
        if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
        if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
        return d.toISOString().slice(0, 10);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
