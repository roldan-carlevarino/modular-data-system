// =========================================================================
// MATH TRAINER — mental arithmetic with per-attempt analytics
//
// All problem generation is client-side. When a session ends we POST the full
// batch to /math/session and reload aggregate stats.
//
// State machine: setup → session → results → setup
// =========================================================================

(function () {
    const API = "https://api-dashboard-production-fc05.up.railway.app";

    // ---------- Defaults & persistence ----------
    const DEFAULTS = {
        ops: ['+', '-', '*'],
        numtype: 'int',
        range: 'medium',
        duration: 120,
        mode: 'normal',
    };
    const STORAGE_KEY = 'math.config.v1';

    function loadConfig() {
        try {
            const raw = localStorage.getItem(STORAGE_KEY);
            if (!raw) return { ...DEFAULTS };
            const c = JSON.parse(raw);
            return {
                ops: Array.isArray(c.ops) && c.ops.length ? c.ops : DEFAULTS.ops,
                numtype: c.numtype || DEFAULTS.numtype,
                range: c.range || DEFAULTS.range,
                duration: parseInt(c.duration, 10) || DEFAULTS.duration,
                mode: c.mode === 'weakness' ? 'weakness' : 'normal',
            };
        } catch (e) {
            return { ...DEFAULTS };
        }
    }
    function saveConfig(c) {
        try { localStorage.setItem(STORAGE_KEY, JSON.stringify(c)); } catch (e) { /* ignore */ }
    }

    let config = loadConfig();

    // ---------- DOM refs (resolved lazily so this works even before tab is opened) ----------
    function $(id) { return document.getElementById(id); }
    function viewEl(name) {
        return document.querySelector(`#mathTrainer .math-view[data-view="${name}"]`);
    }

    // ---------- View switcher ----------
    function showView(name) {
        ['setup', 'session', 'results'].forEach(v => {
            const el = viewEl(v);
            if (el) el.hidden = (v !== name);
        });
    }

    // ---------- Chips ----------
    function paintChips() {
        document.querySelectorAll('#mathTrainer .math-chips').forEach(group => {
            const key = group.dataset.group;
            const isSingle = group.dataset.single === 'true';
            group.querySelectorAll('.math-chip').forEach(chip => {
                const v = chip.dataset.value;
                const active = isSingle
                    ? String(config[key]) === String(v)
                    : (config[key] || []).includes(v);
                chip.classList.toggle('is-active', active);
            });
        });
    }

    function bindChips() {
        document.querySelectorAll('#mathTrainer .math-chips').forEach(group => {
            const key = group.dataset.group;
            const isSingle = group.dataset.single === 'true';
            group.addEventListener('click', e => {
                const chip = e.target.closest('.math-chip');
                if (!chip) return;
                const v = chip.dataset.value;
                if (isSingle) {
                    // Numeric for duration, string for the rest
                    config[key] = (key === 'duration') ? parseInt(v, 10) : v;
                } else {
                    const arr = Array.isArray(config[key]) ? [...config[key]] : [];
                    const idx = arr.indexOf(v);
                    if (idx >= 0) arr.splice(idx, 1); else arr.push(v);
                    if (arr.length === 0) return; // never allow empty multi-select
                    config[key] = arr;
                }
                saveConfig(config);
                paintChips();
            });
        });
    }

    // ---------- Problem generator ----------
    const RANGES = {
        easy:   { lo: 1,  hi: 12,  divDigits: 1 },
        medium: { lo: 2,  hi: 25,  divDigits: 1 },
        hard:   { lo: 10, hi: 99,  divDigits: 2 },
        expert: { lo: 10, hi: 999, divDigits: 2 },
    };

    function rint(lo, hi) { return Math.floor(Math.random() * (hi - lo + 1)) + lo; }

    function pick(arr) { return arr[Math.floor(Math.random() * arr.length)]; }

    function roundDecimal(x) {
        // 1 decimal place when in decimal mode
        return Math.round(x * 10) / 10;
    }

    function genProblem(cfg) {
        const range = RANGES[cfg.range] || RANGES.medium;
        const op = pick(cfg.ops);
        const isDecimal = cfg.numtype === 'dec';

        let a, b, answer;

        if (op === '+') {
            a = rint(range.lo, range.hi);
            b = rint(range.lo, range.hi);
            if (isDecimal) {
                a = roundDecimal(a + Math.random());
                b = roundDecimal(b + Math.random());
                answer = roundDecimal(a + b);
            } else {
                answer = a + b;
            }
        } else if (op === '-') {
            a = rint(range.lo, range.hi);
            b = rint(range.lo, range.hi);
            if (a < b) [a, b] = [b, a]; // keep result non-negative
            if (isDecimal) {
                a = roundDecimal(a + Math.random());
                b = roundDecimal(b + Math.random());
                if (a < b) [a, b] = [b, a];
                answer = roundDecimal(a - b);
            } else {
                answer = a - b;
            }
        } else if (op === '*') {
            // Cap multiplication to keep it mental-friendly even at "expert"
            const mulHi = Math.min(range.hi, cfg.range === 'expert' ? 99 : range.hi);
            a = rint(range.lo, mulHi);
            b = rint(range.lo, mulHi);
            if (isDecimal) {
                a = roundDecimal(a + Math.random());
                answer = roundDecimal(a * b);
            } else {
                answer = a * b;
            }
        } else if (op === '/') {
            // Build cleanly divisible integer division, then optionally turn into decimal display
            b = rint(Math.max(2, range.lo), Math.min(range.hi, 25));
            const quot = rint(range.lo, range.hi);
            a = b * quot;
            answer = quot;
            if (isDecimal) {
                // Append a trailing tenths to numerator: a.x / b
                const tenth = rint(0, 9);
                a = roundDecimal(a + tenth / 10);
                answer = roundDecimal(a / b);
            }
        }

        const problem = `${a} ${displayOp(op)} ${b}`;
        return { op, a, b, answer, problem };
    }

    function displayOp(op) {
        return { '+': '+', '-': '−', '*': '×', '/': '÷' }[op] || op;
    }

    // ---------- Answer comparison ----------
    function parseUserAnswer(raw) {
        if (raw == null) return null;
        const s = String(raw).trim().replace(',', '.');
        if (!s) return null;
        const n = Number(s);
        return Number.isFinite(n) ? n : null;
    }

    function isCorrect(userVal, expected) {
        if (userVal === null) return false;
        // tolerate tiny floating-point drift on decimals
        return Math.abs(userVal - expected) < 1e-6;
    }

    // ---------- Session state ----------
    const session = {
        active: false,
        started: 0,
        endsAt: 0,
        durationS: 0,
        attempts: [],
        current: null,
        currentShownAt: 0,
        timerInterval: null,
        endTimeout: null,
        weakPool: null,    // [{op,a,b,weight}] when mode='weakness'
        weakWeightSum: 0,
    };

    function resetSessionState() {
        session.active = false;
        session.started = 0;
        session.endsAt = 0;
        session.durationS = 0;
        session.attempts = [];
        session.current = null;
        session.currentShownAt = 0;
        session.weakPool = null;
        session.weakWeightSum = 0;
        if (session.timerInterval) { clearInterval(session.timerInterval); session.timerInterval = null; }
        if (session.endTimeout) { clearTimeout(session.endTimeout); session.endTimeout = null; }
    }

    function startSession() {
        if (!config.ops || !config.ops.length) {
            alert('Pick at least one operation.');
            return;
        }
        resetSessionState();
        session.active = true;
        session.durationS = parseInt(config.duration, 10);
        session.started = Date.now();
        session.endsAt = session.started + session.durationS * 1000;

        $('mathCorrectCount').textContent = '0';
        $('mathWrongCount').textContent = '0';
        $('mathPaceCount').textContent = '0';
        $('mathFeedback').textContent = '';
        $('mathAnswerInput').value = '';

        showView('session');

        // Weakness mode: prefetch weak pool, then start. Falls back silently on failure.
        const launch = () => {
            nextProblem();
            $('mathAnswerInput').focus();
            session.timerInterval = setInterval(updateTimer, 200);
            session.endTimeout = setTimeout(endSession, session.durationS * 1000);
            updateTimer();
        };
        if (config.mode === 'weakness') {
            loadWeakPool().finally(launch);
        } else {
            launch();
        }
    }

    async function loadWeakPool() {
        try {
            const res = await fetch(`${API}/math/weakness?min_attempts=2&top_k=80`);
            if (!res.ok) return;
            const data = await res.json();
            const pairs = (data.weak_pairs || []).filter(p => config.ops.includes(p.op));
            if (!pairs.length) return;
            // Weight: emphasize wrong + slow. err = 1-acc, lat factor caps at ~3s.
            const weighted = pairs.map(p => {
                const err = Math.max(0, 1 - (p.accuracy || 0));
                const latNorm = Math.min(1, (p.avg_latency_ms || 0) / 3000);
                // Always give a small base weight so even mostly-correct-but-slow pairs appear
                const w = (err * 3) + (latNorm * 1) + 0.1;
                return { op: p.op, a: p.a, b: p.b, weight: w };
            });
            session.weakPool = weighted;
            session.weakWeightSum = weighted.reduce((s, p) => s + p.weight, 0);
        } catch (err) {
            console.warn('Weak pool fetch failed:', err);
        }
    }

    function pickFromWeakPool() {
        if (!session.weakPool || !session.weakPool.length) return null;
        let r = Math.random() * session.weakWeightSum;
        for (const p of session.weakPool) {
            r -= p.weight;
            if (r <= 0) return p;
        }
        return session.weakPool[session.weakPool.length - 1];
    }

    function buildFromPair(pair) {
        const { op, a, b } = pair;
        let answer;
        if (op === '+') answer = a + b;
        else if (op === '-') answer = a - b;
        else if (op === '*') answer = a * b;
        else if (op === '/') answer = (b !== 0) ? a / b : null;
        if (answer === null || !Number.isFinite(answer)) return null;
        // Round to 1 dp to match generator semantics for decimals
        if (!Number.isInteger(answer)) answer = Math.round(answer * 10) / 10;
        // Display integers without trailing .0
        const aDisp = Number.isInteger(a) ? a : (Math.round(a * 10) / 10);
        const bDisp = Number.isInteger(b) ? b : (Math.round(b * 10) / 10);
        return {
            op, a: aDisp, b: bDisp, answer,
            problem: `${aDisp} ${displayOp(op)} ${bDisp}`,
        };
    }

    function nextProblem() {
        let p = null;
        // In weakness mode, 75% pick from weak pool, 25% normal random for variety
        if (config.mode === 'weakness' && session.weakPool && session.weakPool.length && Math.random() < 0.75) {
            const pair = pickFromWeakPool();
            if (pair) p = buildFromPair(pair);
        }
        if (!p) p = genProblem(config);
        session.current = p;
        session.currentShownAt = performance.now();
        $('mathProblem').textContent = `${p.problem} = ?`;
    }

    function updateTimer() {
        const remaining = Math.max(0, session.endsAt - Date.now());
        const s = Math.ceil(remaining / 1000);
        const mm = String(Math.floor(s / 60)).padStart(2, '0');
        const ss = String(s % 60).padStart(2, '0');
        $('mathTimer').textContent = `${mm}:${ss}`;

        const elapsed = Math.max(1, (Date.now() - session.started) / 1000);
        const correct = session.attempts.filter(a => a.is_correct).length;
        const pace = (correct / elapsed) * 60;
        $('mathPaceCount').textContent = pace.toFixed(1);
    }

    function flashFeedback(ok, expected) {
        const el = $('mathFeedback');
        el.className = 'math-feedback ' + (ok ? 'is-ok' : 'is-bad');
        el.textContent = ok ? '✓' : `✗  (${expected})`;
        clearTimeout(flashFeedback._t);
        flashFeedback._t = setTimeout(() => { el.className = 'math-feedback'; el.textContent = ''; }, ok ? 280 : 1200);
    }

    function submitAnswer() {
        if (!session.active || !session.current) return;
        const inp = $('mathAnswerInput');
        const raw = inp.value;
        const userVal = parseUserAnswer(raw);
        if (userVal === null) { inp.focus(); return; }

        const p = session.current;
        const ok = isCorrect(userVal, p.answer);
        const latency = Math.round(performance.now() - session.currentShownAt);

        session.attempts.push({
            problem: p.problem,
            op: p.op,
            a_value: p.a,
            b_value: p.b,
            user_answer: String(raw).trim(),
            correct_answer: String(p.answer),
            latency_ms: latency,
            is_correct: ok,
        });

        if (ok) {
            $('mathCorrectCount').textContent = String(parseInt($('mathCorrectCount').textContent, 10) + 1);
        } else {
            $('mathWrongCount').textContent = String(parseInt($('mathWrongCount').textContent, 10) + 1);
        }
        flashFeedback(ok, p.answer);

        inp.value = '';
        nextProblem();
    }

    async function endSession(opts = { aborted: false }) {
        if (!session.active) return;
        session.active = false;
        if (session.timerInterval) { clearInterval(session.timerInterval); session.timerInterval = null; }
        if (session.endTimeout) { clearTimeout(session.endTimeout); session.endTimeout = null; }

        const elapsedS = Math.min(session.durationS, Math.round((Date.now() - session.started) / 1000));
        const correct = session.attempts.filter(a => a.is_correct).length;
        const wrong = session.attempts.length - correct;
        const score = elapsedS > 0 ? (correct / elapsedS) * 60 : 0;
        const accuracy = session.attempts.length ? (correct / session.attempts.length) : 0;
        const avgLatency = session.attempts.length
            ? Math.round(session.attempts.reduce((s, a) => s + a.latency_ms, 0) / session.attempts.length)
            : 0;

        // Render results view
        $('resultScore').textContent = score.toFixed(1);
        $('resultCorrect').textContent = correct;
        $('resultWrong').textContent = wrong;
        $('resultAccuracy').textContent = session.attempts.length ? `${(accuracy * 100).toFixed(0)}%` : '—';
        $('resultLatency').textContent = avgLatency || '—';
        showView('results');

        // Persist (only if there's something to save and not zero-length)
        if (session.attempts.length > 0 && elapsedS > 0) {
            try {
                await fetch(`${API}/math/session`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        duration_s: elapsedS,
                        settings: { ...config, aborted: !!opts.aborted },
                        attempts: session.attempts,
                    }),
                });
                loadStats();
            } catch (err) {
                console.error('Failed to save math session:', err);
            }
        }
    }

    // ---------- Stats ----------
    let heatmapOp = '*';
    const HEATMAP_RANGES = {
        '+': { lo: 1, hi: 20 },
        '-': { lo: 1, hi: 20 },
        '*': { lo: 1, hi: 12 },
        '/': { lo: 1, hi: 12 },
    };

    async function loadStats() {
        try {
            const res = await fetch(`${API}/math/stats`);
            if (!res.ok) return;
            const stats = await res.json();
            renderStats(stats);
        } catch (err) {
            console.error('Failed to load math stats:', err);
        }
        loadHeatmap();
    }

    async function loadHeatmap() {
        const el = $('mathHeatmap');
        if (!el) return;
        const r = HEATMAP_RANGES[heatmapOp] || HEATMAP_RANGES['*'];
        try {
            const res = await fetch(`${API}/math/heatmap?op=${encodeURIComponent(heatmapOp)}&lo=${r.lo}&hi=${r.hi}`);
            if (!res.ok) { el.innerHTML = '<div class="math-empty">No heatmap data</div>'; return; }
            const data = await res.json();
            renderHeatmap(data);
        } catch (err) {
            console.error('Failed to load heatmap:', err);
        }
    }

    function renderHeatmap(data) {
        const el = $('mathHeatmap');
        if (!el) return;
        const lo = data.lo, hi = data.hi;
        const cells = data.cells || [];
        if (!cells.length) {
            el.innerHTML = '<div class="math-empty">Not enough data for this operation yet — train more to populate the grid.</div>';
            return;
        }
        // Index cells by "a,b"
        const map = new Map();
        let maxN = 0;
        for (const c of cells) {
            map.set(`${c.a},${c.b}`, c);
            if (c.n > maxN) maxN = c.n;
        }

        const size = hi - lo + 1;
        const parts = [];
        // Build CSS grid: an extra header row + header column for axis labels
        parts.push(`<div class="math-heatmap-grid" style="grid-template-columns: auto repeat(${size}, 1fr);">`);
        // Top-left blank + column headers
        parts.push('<div class="math-heatmap-axis math-heatmap-axis--corner"></div>');
        for (let b = lo; b <= hi; b++) {
            parts.push(`<div class="math-heatmap-axis">${b}</div>`);
        }
        // Rows
        const symbol = displayOp(data.op);
        for (let a = lo; a <= hi; a++) {
            parts.push(`<div class="math-heatmap-axis math-heatmap-axis--row">${a}</div>`);
            for (let b = lo; b <= hi; b++) {
                const c = map.get(`${a},${b}`);
                if (!c) {
                    parts.push('<div class="math-heatmap-cell math-heatmap-cell--empty"></div>');
                } else {
                    // Color: red (acc=0) -> yellow (0.5) -> green (1)
                    const acc = c.accuracy;
                    const hue = Math.round(acc * 120); // 0=red, 120=green
                    const sat = 60;
                    const light = 60;
                    // Opacity scales by attempts (log-ish)
                    const op = 0.35 + 0.65 * Math.min(1, Math.log2(c.n + 1) / Math.log2(maxN + 1));
                    const bg = `hsla(${hue}, ${sat}%, ${light}%, ${op.toFixed(2)})`;
                    const tip = `${a} ${symbol} ${b}\n${c.n} attempts, ${(acc * 100).toFixed(0)}% correct${c.avg_latency_ms ? `, ~${c.avg_latency_ms}ms avg` : ''}`;
                    parts.push(`<div class="math-heatmap-cell" style="background:${bg};" title="${tip}"><span class="math-heatmap-cell__n">${c.n}</span></div>`);
                }
            }
        }
        parts.push('</div>');
        el.innerHTML = parts.join('');
    }

    function renderStats(s) {
        $('statHighest').textContent = s.highest_per_min ? s.highest_per_min.toFixed(1) : '—';
        $('statSessions').textContent = s.sessions || 0;
        $('statAttempts').textContent = (s.total_correct || 0) + (s.total_wrong || 0);

        // Trend sparkline (inline SVG, no external lib needed)
        const trendEl = $('statTrend');
        trendEl.innerHTML = '';
        if (Array.isArray(s.trend) && s.trend.length >= 2) {
            const W = 280, H = 56, P = 4;
            const vals = s.trend.map(t => t.score_per_min);
            const lo = Math.min(...vals);
            const hi = Math.max(...vals);
            const span = (hi - lo) || 1;
            const stepX = (W - 2 * P) / (vals.length - 1);
            const pts = vals.map((v, i) => {
                const x = P + i * stepX;
                const y = P + (H - 2 * P) * (1 - (v - lo) / span);
                return `${x.toFixed(1)},${y.toFixed(1)}`;
            }).join(' ');
            trendEl.innerHTML = `
                <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" class="math-trend__svg">
                    <polyline points="${pts}" fill="none" stroke="currentColor" stroke-width="1.5" />
                    <circle cx="${(P + (vals.length - 1) * stepX).toFixed(1)}"
                            cy="${(P + (H - 2 * P) * (1 - (vals[vals.length-1] - lo) / span)).toFixed(1)}"
                            r="2.5" fill="currentColor" />
                </svg>`;
        } else {
            trendEl.innerHTML = '<span class="math-empty">Not enough data yet</span>';
        }

        // Per-op breakdown
        const opEl = $('mathByOp');
        opEl.innerHTML = '';
        if (Array.isArray(s.by_op) && s.by_op.length) {
            const labelMap = { '+': 'Addition', '-': 'Subtraction', '*': 'Multiplication', '/': 'Division' };
            s.by_op.forEach(row => {
                const acc = row.accuracy != null ? `${(row.accuracy * 100).toFixed(0)}%` : '—';
                const lat = row.avg_latency_ms != null ? `${row.avg_latency_ms} ms` : '—';
                const div = document.createElement('div');
                div.className = 'math-op-card';
                div.innerHTML = `
                    <div class="math-op-card__head">
                        <span class="math-op-card__sym">${displayOp(row.op)}</span>
                        <span class="math-op-card__name">${labelMap[row.op] || row.op}</span>
                    </div>
                    <div class="math-op-card__metrics">
                        <span><strong>${row.n}</strong> attempts</span>
                        <span><strong>${acc}</strong> accuracy</span>
                        <span><strong>${lat}</strong> avg</span>
                    </div>`;
                opEl.appendChild(div);
            });
        }
    }

    // ---------- Wire up ----------
    function init() {
        if (!document.getElementById('mathTrainer')) return;
        bindChips();
        paintChips();

        $('mathStartBtn').addEventListener('click', startSession);
        $('mathAbortBtn').addEventListener('click', () => endSession({ aborted: true }));
        $('mathAgainBtn').addEventListener('click', startSession);
        $('mathBackBtn').addEventListener('click', () => { showView('setup'); });

        $('mathAnswerForm').addEventListener('submit', e => {
            e.preventDefault();
            submitAnswer();
        });

        // Heatmap op selector
        const hmChips = document.getElementById('heatmapOpChips');
        if (hmChips) {
            hmChips.addEventListener('click', e => {
                const chip = e.target.closest('.math-chip');
                if (!chip) return;
                heatmapOp = chip.dataset.op || '*';
                hmChips.querySelectorAll('.math-chip').forEach(c => c.classList.toggle('is-active', c === chip));
                loadHeatmap();
            });
        }

        // Keyboard: Esc inside session aborts
        document.addEventListener('keydown', e => {
            if (e.key === 'Escape' && session.active) {
                e.preventDefault();
                endSession({ aborted: true });
            }
        });

        showView('setup');
        loadStats();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
