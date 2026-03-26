const API_URL = 'https://api-dashboard-production-fc05.up.railway.app';

document.addEventListener('DOMContentLoaded', () => {
    let pomState      = null;   // { pomodoroId, activeType, studyRemaining, restRemaining, focusNow, expectations }
    let timerInterval = null;
    let allProjects   = [];     // flat list from /projects/
    let projectMap    = {};     // id -> project object
    let focusPanelOpen = false;

    // DOM
    const startForm          = document.getElementById('startForm');
    const activePomodoro     = document.getElementById('activePomodoro');
    const studyBlock         = document.getElementById('studyTimerBlock');
    const restBlock          = document.getElementById('restTimerBlock');
    const studyDisplay       = document.getElementById('studyTimerDisplay');
    const restDisplay        = document.getElementById('restTimerDisplay');
    const currentFocusDisp   = document.getElementById('currentFocus');
    const todayDiv           = document.getElementById('todayPomodoros');
    const switchBtn          = document.getElementById('switchBtn');
    const changeFocusBtn     = document.getElementById('changeFocusBtn');
    const endBtn             = document.getElementById('endBtn');
    const expectationsTable  = document.getElementById('expectationsTable');
    const changeFocusPanel   = document.getElementById('changeFocusPanel');
    const startPomodoroBtn   = document.getElementById('startPomodoroBtn');
    const addExpectationBtn  = document.getElementById('addExpectationBtn');
    const addCustomExpBtn    = document.getElementById('addCustomExpBtn');
    const expectationsList   = document.getElementById('expectationsList');

    loadProjects().then(() => {
        checkCurrentPomodoro();
    });
    loadTodayPomodoros();
    setInterval(loadTodayPomodoros, 60000);

    // ── Load projects from API ───────────────────────────────────
    async function loadProjects() {
        try {
            const res = await fetch(`${API_URL}/projects/`);
            allProjects = await res.json();
            projectMap = {};
            allProjects.forEach(p => { projectMap[p.id] = p; });
        } catch (e) { console.error('Failed to load projects', e); }
    }

    function projectLabel(p) {
        // Short label: name only. Path available as p.path if needed.
        return `${p.type === 'task' ? '📄' : '📁'} ${p.name}`;
    }

    function buildProjectSelect(selectedId = null) {
        const select = document.createElement('select');
        select.className = 'exp-project-select';
        const blank = document.createElement('option');
        blank.value = '';
        blank.textContent = '— pick project/task —';
        select.appendChild(blank);

        // Group: top-level projects first, then tasks indented by parent
        const grouped = [];
        const topLevel = allProjects.filter(p => !p.parent_id || !projectMap[p.parent_id]);
        topLevel.forEach(parent => {
            grouped.push(parent);
            allProjects.filter(c => c.parent_id === parent.id).forEach(child => grouped.push(child));
        });

        grouped.forEach(p => {
            const opt = document.createElement('option');
            opt.value = p.id;
            opt.dataset.type = p.type;
            const indent = p.parent_id && projectMap[p.parent_id] ? '\u00a0\u00a0\u00a0' : '';
            opt.textContent = indent + projectLabel(p);
            if (selectedId && p.id === selectedId) opt.selected = true;
            select.appendChild(opt);
        });
        return select;
    }

    // ── Add custom (free-text) expectation item ────────────────
    addCustomExpBtn?.addEventListener('click', () => {
        const div = document.createElement('div');
        div.className = 'expectation-item expectation-item--custom';

        const labelInput = document.createElement('input');
        labelInput.type = 'text';
        labelInput.className = 'exp-custom-label';
        labelInput.placeholder = 'e.g. review notes, read chapter 3...';

        const weightInput = document.createElement('input');
        weightInput.type = 'number';
        weightInput.value = '1';
        weightInput.min = '1';
        weightInput.className = 'exp-weight';
        weightInput.placeholder = 'w';

        const removeBtn = document.createElement('button');
        removeBtn.className = 'btn-small remove-exp';
        removeBtn.textContent = '×';
        removeBtn.addEventListener('click', () => div.remove());

        div.appendChild(labelInput);
        div.appendChild(weightInput);
        div.appendChild(removeBtn);
        expectationsList.appendChild(div);
    });

    // ── Add expectation item (start form) ────────────────────────
    addExpectationBtn?.addEventListener('click', () => {
        const div = document.createElement('div');
        div.className = 'expectation-item';

        const select = buildProjectSelect();

        const detailsInput = document.createElement('input');
        detailsInput.type = 'text';
        detailsInput.className = 'exp-details';
        detailsInput.placeholder = 'details / notes...';

        const weightInput = document.createElement('input');
        weightInput.type = 'number';
        weightInput.value = '1';
        weightInput.min = '1';
        weightInput.className = 'exp-weight';
        weightInput.placeholder = 'w';

        const removeBtn = document.createElement('button');
        removeBtn.className = 'btn-small remove-exp';
        removeBtn.textContent = '×';
        removeBtn.addEventListener('click', () => div.remove());

        div.appendChild(select);
        div.appendChild(detailsInput);
        div.appendChild(weightInput);
        div.appendChild(removeBtn);
        expectationsList.appendChild(div);
    });

    // ── Start session ────────────────────────────────────────────
    startPomodoroBtn?.addEventListener('click', async () => {
        const expItems = expectationsList.querySelectorAll('.expectation-item');
        const expectations = Array.from(expItems).map((item, idx) => {
            // Custom (free-text) item
            const customLabel = item.querySelector('.exp-custom-label');
            if (customLabel) {
                const text = customLabel.value.trim();
                if (!text) return null;
                return {
                    ref_type: 'manual',
                    ref_id:   -(idx + 1),   // unique negative id per custom item
                    details:  text,
                    weight:   parseInt(item.querySelector('.exp-weight').value) || 1
                };
            }
            // Project/task item
            const select = item.querySelector('.exp-project-select');
            const id = parseInt(select.value);
            const proj = projectMap[id];
            if (!proj) return null;
            return {
                ref_type: proj.type,
                ref_id:   proj.id,
                details:  item.querySelector('.exp-details')?.value.trim() || null,
                weight:   parseInt(item.querySelector('.exp-weight').value) || 1
            };
        }).filter(Boolean);

        // Initial focus = first expectation, else manual
        const initialFocus = expectations.length > 0
            ? { ref_type: expectations[0].ref_type, ref_id: expectations[0].ref_id }
            : { ref_type: 'manual', ref_id: 0 };

        try {
            const res = await fetch(`${API_URL}/pomodoro/start`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ initial_focus: initialFocus, expectations })
            });
            if (res.ok) {
                expectationsList.innerHTML = '';
                checkCurrentPomodoro();
            }
        } catch (e) { console.error(e); }
    });

    // ── Switch active timer ──────────────────────────────────────
    switchBtn?.addEventListener('click', async () => {
        switchBtn.disabled = true;
        try {
            const res = await fetch(`${API_URL}/pomodoro/change_state`, { method: 'POST' });
            if (res.ok) await checkCurrentPomodoro();
        } catch (e) { console.error(e); }
        switchBtn.disabled = false;
    });

    // ── Change focus ─────────────────────────────────────────────
    changeFocusBtn?.addEventListener('click', () => {
        focusPanelOpen = !focusPanelOpen;
        renderFocusPanel();
        if (changeFocusBtn) {
            changeFocusBtn.textContent = focusPanelOpen ? 'Cancel ×' : 'Change Focus';
        }
    });

    // ── End session ──────────────────────────────────────────────
    endBtn?.addEventListener('click', async () => {
        if (!confirm('End this study block?')) return;
        try {
            const res = await fetch(`${API_URL}/pomodoro/end`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ contents: [] })
            });
            if (res.ok) {
                stopTimer();
                focusPanelOpen = false;
                startForm.style.display      = 'block';
                activePomodoro.style.display = 'none';
                loadTodayPomodoros();
            }
        } catch (e) { console.error(e); }
    });

    // ── Core functions ───────────────────────────────────────────
    async function checkCurrentPomodoro() {
        try {
            const res  = await fetch(`${API_URL}/pomodoro/current`);
            const data = await res.json();
            if (data && data.pomodoro_id) {
                pomState = {
                    pomodoroId:     data.pomodoro_id,
                    activeType:     data.active_type,
                    studyRemaining: data.study_remaining,
                    restRemaining:  data.rest_remaining,
                    focusNow:       data.focus_now,
                    expectations:   data.expectations || []
                };
                startForm.style.display      = 'none';
                activePomodoro.style.display = 'block';
                updateDisplays();
                startLocalTimer();
            } else {
                pomState = null;
                startForm.style.display      = 'block';
                activePomodoro.style.display = 'none';
                stopTimer();
            }
        } catch (e) { console.error(e); }
    }

    function startLocalTimer() {
        stopTimer();
        timerInterval = setInterval(() => {
            if (!pomState) return;
            if (pomState.activeType === 'study') {
                pomState.studyRemaining = Math.max(0, pomState.studyRemaining - 1);
            } else {
                pomState.restRemaining = Math.max(0, pomState.restRemaining - 1);
            }
            updateDisplays();
        }, 1000);
    }

    function stopTimer() {
        if (timerInterval) { clearInterval(timerInterval); timerInterval = null; }
    }

    function resolveLabel(ref_type, ref_id, details) {
        if (ref_type === 'manual' || ref_id <= 0 || !ref_id) return details || 'manual';
        const proj = projectMap[ref_id];
        if (proj) return `${proj.type === 'task' ? '\ud83d\udcc4' : '\ud83d\udcc1'} ${proj.name}`;
        return `${ref_type} #${ref_id}`;
    }

    function updateDisplays() {
        if (!pomState) return;
        if (studyDisplay) studyDisplay.textContent = formatTime(pomState.studyRemaining);
        if (restDisplay)  restDisplay.textContent  = formatTime(pomState.restRemaining);

        studyBlock?.classList.toggle('timer-active', pomState.activeType === 'study');
        restBlock?.classList.toggle('timer-active',  pomState.activeType === 'rest');
        studyBlock?.classList.toggle('timer-paused', pomState.activeType !== 'study');
        restBlock?.classList.toggle('timer-paused',  pomState.activeType !== 'rest');

        if (switchBtn) {
            switchBtn.textContent = pomState.activeType === 'study'
                ? '⏸ Pause study · Start rest →'
                : '← Resume study · Pause rest ⏸';
        }
        if (currentFocusDisp && pomState.focusNow) {
            const f = pomState.focusNow;
            // For manual/custom, try to find label from expectations
            const matched = pomState.expectations.find(e =>
                e.ref_type === f.ref_type && Number(e.ref_id) === Number(f.ref_id)
            );
            currentFocusDisp.textContent = resolveLabel(f.ref_type, f.ref_id, matched?.details ?? null);
        }

        renderExpectationsTable();
        if (focusPanelOpen) renderFocusPanel();
    }

    // ── Expectations table (always visible during active session) ─
    function renderExpectationsTable() {
        if (!expectationsTable) return;
        const exps = pomState?.expectations || [];
        if (exps.length === 0) {
            expectationsTable.innerHTML = '';
            return;
        }

        const fn = pomState?.focusNow;

        const rows = exps.map(e => {
            const isActive = fn &&
                e.ref_type === fn.ref_type &&
                Number(e.ref_id) === Number(fn.ref_id);
            const label    = resolveLabel(e.ref_type, e.ref_id, e.details);
            const isCustom = e.ref_type === 'manual' && (e.ref_id === 0 || !e.ref_id);
            return `<div class="exp-row ${isActive ? 'exp-row--active' : ''} ${isCustom ? 'exp-row--custom' : ''}">
                <div class="exp-row-main">
                    <span class="exp-row-label">${label}</span>
                    <span class="exp-row-weight">w:${e.weight}</span>
                    ${isActive ? '<span class="exp-row-badge">▶ now</span>' : ''}
                </div>
                ${!isCustom && e.details ? `<div class="exp-row-details">${e.details}</div>` : ''}
            </div>`;
        }).join('');

        expectationsTable.innerHTML = `
            <div class="exp-table-header">Plan for this session</div>
            <div class="exp-table-rows">${rows}</div>
        `;
    }

    // ── Focus picker panel (click to switch) ─────────────────────
    function renderFocusPanel() {
        if (!changeFocusPanel) return;
        if (!focusPanelOpen) {
            changeFocusPanel.style.display = 'none';
            return;
        }

        const exps = pomState?.expectations || [];
        if (exps.length === 0) {
            changeFocusPanel.innerHTML = '<p class="focus-panel-empty">No plan items — add expectations at start</p>';
            changeFocusPanel.style.display = 'block';
            return;
        }

        const fn = pomState?.focusNow;

        const buttons = exps.map(e => {
            const isActive = fn &&
                e.ref_type === fn.ref_type &&
                Number(e.ref_id) === Number(fn.ref_id);
            const label    = resolveLabel(e.ref_type, e.ref_id, e.details);
            return `<button
                class="focus-pick-btn ${isActive ? 'focus-pick-btn--active' : ''}"
                data-reftype="${e.ref_type}"
                data-refid="${e.ref_id}"
                ${isActive ? 'disabled' : ''}
            ><span class="focus-pick-name">${label}</span>${!isActive && e.ref_type !== 'manual' && e.details ? `<span class="focus-pick-details">${e.details}</span>` : ''}<span class="focus-pick-weight">w:${e.weight}</span></button>`;
        }).join('');

        changeFocusPanel.innerHTML = `<div class="focus-pick-list">${buttons}</div>`;
        changeFocusPanel.style.display = 'block';

        changeFocusPanel.querySelectorAll('.focus-pick-btn:not([disabled])').forEach(btn => {
            btn.addEventListener('click', async () => {
                const refType = btn.dataset.reftype;
                const refId   = parseInt(btn.dataset.refid);
                try {
                    const res = await fetch(`${API_URL}/pomodoro/change_focus`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ focus: { ref_type: refType, ref_id: refId } })
                    });
                    if (res.ok) {
                        focusPanelOpen = false;
                        if (changeFocusBtn) changeFocusBtn.textContent = 'Change Focus';
                        if (changeFocusPanel) changeFocusPanel.style.display = 'none';
                        await checkCurrentPomodoro();
                    }
                } catch (e) { console.error(e); }
            });
        });
    }

    function formatTime(secs) {
        const h = Math.floor(secs / 3600);
        const m = Math.floor((secs % 3600) / 60);
        const s = secs % 60;
        return h > 0
            ? `${pad(h)}:${pad(m)}:${pad(s)}`
            : `${pad(m)}:${pad(s)}`;
    }

    function pad(n) { return String(n).padStart(2, '0'); }

    // ── Today's sessions ─────────────────────────────────────────
    async function loadTodayPomodoros() {
        try {
            const res       = await fetch(`${API_URL}/pomodoro/today`);
            const pomodoros = await res.json();
            if (!todayDiv || !Array.isArray(pomodoros)) return;
            if (pomodoros.length === 0) {
                todayDiv.innerHTML = '<p style="opacity:0.5;font-size:0.85rem">No sessions today yet</p>';
                return;
            }
            todayDiv.innerHTML = pomodoros.map(p => {
                const start    = new Date(p.start_time).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
                const end      = p.end_time ? new Date(p.end_time).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '…';
                const duration = p.end_time
                    ? Math.floor((new Date(p.end_time) - new Date(p.start_time)) / 60000) + ' min'
                    : '';
                return `<div class="pomodoro-item">
                    <span class="pom-time">${start} – ${end}</span>
                    <span class="pom-dur">${duration}</span>
                </div>`;
            }).join('');
        } catch (e) { console.error(e); }
    }

}); // End DOMContentLoaded

