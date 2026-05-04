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
        negatives: false,
        tolerance: 'strict', // 'strict' | 'pct5' | 'pct10'
        input: 'type',       // 'type' | 'mc'
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
                negatives: !!c.negatives,
                tolerance: ['strict', 'pct5', 'pct10'].includes(c.tolerance) ? c.tolerance : 'strict',
                input: c.input === 'mc' ? 'mc' : 'type',
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
            const isToggle = group.dataset.toggle === 'true';
            group.querySelectorAll('.math-chip').forEach(chip => {
                const v = chip.dataset.value;
                let active;
                if (isToggle) {
                    active = !!config[key];
                } else if (isSingle) {
                    active = String(config[key]) === String(v);
                } else {
                    active = (config[key] || []).includes(v);
                }
                chip.classList.toggle('is-active', active);
            });
        });
    }

    function bindChips() {
        document.querySelectorAll('#mathTrainer .math-chips').forEach(group => {
            const key = group.dataset.group;
            const isSingle = group.dataset.single === 'true';
            const isToggle = group.dataset.toggle === 'true';
            group.addEventListener('click', e => {
                const chip = e.target.closest('.math-chip');
                if (!chip) return;
                const v = chip.dataset.value;
                if (isToggle) {
                    config[key] = !config[key];
                } else if (isSingle) {
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
                if (key === 'mode' || key === 'ops') loadWeakPreview();
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
            // Allow negative results when toggle is on (~40% chance to ensure variety)
            if (!cfg.negatives && a < b) [a, b] = [b, a];
            if (isDecimal) {
                a = roundDecimal(a + Math.random());
                b = roundDecimal(b + Math.random());
                if (!cfg.negatives && a < b) [a, b] = [b, a];
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
        } else if (op === '%') {
            // Randomly pick between two percentage question forms
            const subForm = Math.random() < 0.5 ? 'of' : 'what';
            const percentBank = cfg.range === 'easy'
                ? [10, 20, 25, 50]
                : (cfg.range === 'medium'
                    ? [5, 10, 15, 20, 25, 50, 75]
                    : [5, 10, 12, 15, 20, 25, 30, 33, 40, 50, 60, 75, 80, 90]);
            const baseHi = Math.min(range.hi * 4, 1000);
            const baseLo = Math.max(range.lo, 10);

            if (subForm === 'of') {
                a = pick(percentBank);
                const yMult = (a % 25 === 0) ? 4 : ((a % 10 === 0) ? 10 : 20);
                b = yMult * rint(Math.max(1, Math.floor(baseLo / yMult)), Math.max(1, Math.floor(baseHi / yMult)));
                answer = (a * b) / 100;
                if (!Number.isInteger(answer)) answer = roundDecimal(answer);
                return { op, a, b, answer, problem: `${a}% of ${b}` };
            } else {
                const pct = pick(percentBank);
                const yMult = (pct % 25 === 0) ? 4 : ((pct % 10 === 0) ? 10 : 20);
                b = yMult * rint(Math.max(1, Math.floor(baseLo / yMult)), Math.max(1, Math.floor(baseHi / yMult)));
                a = (pct * b) / 100;
                answer = pct;
                return { op, a, b, answer, problem: `${a} is what % of ${b}` };
            }
        } else if (op === 'f') {
            // Fraction add or subtract with small denominators. Answer accepted as decimal or fraction.
            const denoms = cfg.range === 'easy' ? [2, 3, 4]
                          : cfg.range === 'medium' ? [2, 3, 4, 5, 6, 8]
                          : [2, 3, 4, 5, 6, 7, 8, 9, 10, 12];
            const d1 = pick(denoms), d2 = pick(denoms);
            const n1 = rint(1, d1 - 1), n2 = rint(1, d2 - 1);
            const sub = Math.random() < 0.5;
            // Compute as decimal (we accept user's fraction as decimal at parse time)
            const v1 = n1 / d1, v2 = n2 / d2;
            let raw = sub ? (v1 - v2) : (v1 + v2);
            // Normalize to non-negative for subtraction display
            let dispN1 = n1, dispD1 = d1, dispN2 = n2, dispD2 = d2;
            if (sub && raw < 0) {
                [dispN1, dispD1, dispN2, dispD2] = [n2, d2, n1, d1];
                raw = -raw;
            }
            a = v1; b = v2;
            answer = Math.round(raw * 1000) / 1000; // 3 dp tolerance
            const problemStr = `${dispN1}/${dispD1} ${sub ? '−' : '+'} ${dispN2}/${dispD2}`;
            return { op, a, b, answer, problem: problemStr };
        } else if (op === 's') {
            // Squares & roots. ~50/50 between x² and √x (perfect squares).
            // Range scales: easy 2–12, medium 2–20, hard 5–30, expert 10–50
            const sqRanges = { easy: [2, 20], medium: [2, 25], hard: [5, 30], expert: [10, 50] };
            const [slo, shi] = sqRanges[cfg.range] || sqRanges.medium;
            if (Math.random() < 0.5) {
                // Square: x² = ?
                a = rint(slo, shi);
                b = a;
                answer = a * a;
                return { op, a, b, answer, problem: `${a}²` };
            } else {
                // Root: √(x²) = ?  (always perfect)
                const r = rint(slo, shi);
                a = r * r;
                b = r;
                answer = r;
                return { op, a, b, answer, problem: `√${a}` };
            }
        } else if (op === 'c') {
            // Conversions: rotate among fraction→%, %→decimal, decimal→fraction-as-decimal
            // Bank of clean fraction ↔ percent equivalences
            const bank = [
                { f: '1/2', d: 0.5,   p: 50 },
                { f: '1/4', d: 0.25,  p: 25 },
                { f: '3/4', d: 0.75,  p: 75 },
                { f: '1/5', d: 0.2,   p: 20 },
                { f: '2/5', d: 0.4,   p: 40 },
                { f: '3/5', d: 0.6,   p: 60 },
                { f: '4/5', d: 0.8,   p: 80 },
                { f: '1/8', d: 0.125, p: 12.5 },
                { f: '3/8', d: 0.375, p: 37.5 },
                { f: '5/8', d: 0.625, p: 62.5 },
                { f: '7/8', d: 0.875, p: 87.5 },
                { f: '1/10', d: 0.1,  p: 10 },
                { f: '3/10', d: 0.3,  p: 30 },
                { f: '7/10', d: 0.7,  p: 70 },
                { f: '9/10', d: 0.9,  p: 90 },
                { f: '1/3', d: 0.333, p: 33.3 },
                { f: '2/3', d: 0.667, p: 66.7 },
            ];
            const item = pick(bank);
            const form = pick(['f2p', 'p2d', 'd2p']);
            a = 0; b = 0;
            if (form === 'f2p') {
                answer = item.p;
                return { op, a, b, answer, problem: `${item.f} = ? %` };
            } else if (form === 'p2d') {
                answer = item.d;
                return { op, a, b, answer, problem: `${item.p}% = ?` };
            } else {
                answer = item.p;
                return { op, a, b, answer, problem: `${item.d} = ? %` };
            }
        }

        const problem = `${a} ${displayOp(op)} ${b}`;
        return { op, a, b, answer, problem };
    }

    function displayOp(op) {
        return {
            '+': '+', '-': '−', '*': '×', '/': '÷',
            '%': '%', 'f': 'frac', 's': 'x²/√', 'c': 'conv',
        }[op] || op;
    }

    // ---------- Answer comparison ----------
    function parseUserAnswer(raw) {
        if (raw == null) return null;
        let s = String(raw).trim().replace(',', '.');
        if (!s) return null;
        // Accept fraction notation "a/b"
        const m = s.match(/^(-?\d+(?:\.\d+)?)\s*\/\s*(-?\d+(?:\.\d+)?)$/);
        if (m) {
            const num = Number(m[1]);
            const den = Number(m[2]);
            if (Number.isFinite(num) && Number.isFinite(den) && den !== 0) {
                return num / den;
            }
            return null;
        }
        // Accept trailing % sign ("15%" => 15)
        if (s.endsWith('%')) s = s.slice(0, -1).trim();
        const n = Number(s);
        return Number.isFinite(n) ? n : null;
    }

    function isCorrect(userVal, expected) {
        if (userVal === null) return false;
        // Approximation tolerance modes
        const tol = config.tolerance;
        if (tol === 'pct5' || tol === 'pct10') {
            const pct = tol === 'pct5' ? 0.05 : 0.10;
            const margin = Math.max(Math.abs(expected) * pct, 0.5);
            return Math.abs(userVal - expected) <= margin;
        }
        // Strict mode: 0.005 tolerance for fractions/conversions, tight otherwise
        return Math.abs(userVal - expected) < 0.005;
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
        weakSnapshot: null, // Map<key, {acc,lat,n}> baseline at session start
        drillIdx: null,
        drillStreak: 0,
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
        session.weakSnapshot = null;
        session.drillIdx = null;
        session.drillStreak = 0;
        const badge = document.getElementById('mathDrillBadge');
        if (badge) { badge.hidden = true; badge.textContent = ''; }
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
            if (config.input !== 'mc') $('mathAnswerInput').focus();
            session.timerInterval = setInterval(updateTimer, 200);
            session.endTimeout = setTimeout(endSession, session.durationS * 1000);
            updateTimer();
        };
        if (config.mode === 'weakness' || config.mode === 'drill') {
            loadWeakPool().finally(launch);
        } else {
            launch();
        }
    }

    async function loadWeakPool() {
        try {
            const res = await fetch(`${API}/math/weakness?min_attempts=2&top_k=120`);
            if (!res.ok) return;
            const data = await res.json();
            const pairs = (data.weak_pairs || []).filter(p => config.ops.includes(p.op));
            if (!pairs.length) return;
            const now = Date.now();
            // Weight: error rate (heavy) + slowness + recency boost (recent wrongs much hotter)
            const weighted = pairs.map(p => {
                const err = Math.max(0, 1 - (p.accuracy || 0));
                const latNorm = Math.min(1, (p.avg_latency_ms || 0) / 3000);
                // Recency boost: 1.0 if wrong in last 24h, decays over 14 days, 0 if never wrong
                let recency = 0;
                if (p.last_wrong_at) {
                    const daysSince = (now - new Date(p.last_wrong_at).getTime()) / 86400000;
                    recency = Math.max(0, 1 - daysSince / 14);
                }
                // Recent (7d) wrong density adds to priority
                const recentWrongRate = p.recent_n ? (p.recent_wrong / p.recent_n) : 0;
                const w = (err * 3) + (latNorm * 1) + (recency * 2) + (recentWrongRate * 1.5) + 0.1;
                return {
                    op: p.op, a: p.a, b: p.b, weight: w,
                    accuracy: p.accuracy, avg_latency_ms: p.avg_latency_ms,
                    n: p.n, last_wrong_at: p.last_wrong_at,
                };
            });
            // Sort by weight desc so drill mode picks the hottest first
            weighted.sort((a, b) => b.weight - a.weight);
            session.weakPool = weighted;
            session.weakWeightSum = weighted.reduce((s, p) => s + p.weight, 0);

            // Snapshot baseline (acc + latency) per pair, used for end-of-session mastery diff
            session.weakSnapshot = new Map();
            weighted.forEach(p => {
                session.weakSnapshot.set(`${p.op}|${p.a}|${p.b}`, {
                    acc: p.accuracy, lat: p.avg_latency_ms, n: p.n,
                });
            });
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

    // Drill mode: pick the worst pair, repeat until mastered (3 in a row), then advance
    function pickForDrill() {
        if (!session.weakPool || !session.weakPool.length) return null;
        // Already locked on a pair? Stay until mastered
        if (session.drillIdx == null) session.drillIdx = 0;
        if (session.drillStreak == null) session.drillStreak = 0;
        if (session.drillIdx >= session.weakPool.length) return null;
        return session.weakPool[session.drillIdx];
    }

    function advanceDrill(wasCorrect) {
        if (config.mode !== 'drill') return;
        if (wasCorrect) {
            session.drillStreak = (session.drillStreak || 0) + 1;
            if (session.drillStreak >= 3) {
                session.drillStreak = 0;
                session.drillIdx = (session.drillIdx || 0) + 1;
            }
        } else {
            session.drillStreak = 0;
        }
        renderDrillBadge();
    }

    function renderDrillBadge() {
        const el = $('mathFeedback');
        if (!el || config.mode !== 'drill') return;
        const pair = session.weakPool && session.weakPool[session.drillIdx];
        if (!pair) return;
        // Show streak under the timer area via the problem element label (non-intrusive)
        const badge = $('mathDrillBadge');
        if (badge) {
            badge.hidden = false;
            badge.textContent = `Drill ${session.drillIdx + 1}/${session.weakPool.length} · streak ${session.drillStreak || 0}/3`;
        }
    }

    // ---------- Weak spots preview (shown on setup view) ----------
    let weakPreviewCache = null;
    async function loadWeakPreview(force = false) {
        const el = $('mathWeakPreview');
        if (!el) return;
        if (config.mode !== 'weakness' && config.mode !== 'drill') {
            el.hidden = true;
            return;
        }
        el.hidden = false;
        if (!weakPreviewCache || force) {
            el.innerHTML = '<div class="math-weak-preview__loading">Loading your weak spots…</div>';
            try {
                const res = await fetch(`${API}/math/weakness?min_attempts=2&top_k=120`);
                if (!res.ok) { el.innerHTML = '<div class="math-empty">Unable to load weak spots.</div>'; return; }
                const data = await res.json();
                weakPreviewCache = data.weak_pairs || [];
            } catch (err) {
                el.innerHTML = '<div class="math-empty">Unable to load weak spots.</div>';
                return;
            }
        }
        renderWeakPreview();
    }

    function renderWeakPreview() {
        const el = $('mathWeakPreview');
        if (!el) return;
        const all = weakPreviewCache || [];
        const filtered = all.filter(p => config.ops.includes(p.op));
        if (!filtered.length) {
            el.innerHTML = `
                <div class="math-weak-preview__head">
                    <strong>Target weak spots</strong>
                    <span class="math-weak-preview__hint">No weak data yet for the selected ops — train a few sessions first.</span>
                </div>`;
            return;
        }
        // Re-rank by the same recency-aware formula used at session start so the user sees what'll be drilled
        const now = Date.now();
        const scored = filtered.map(p => {
            const err = Math.max(0, 1 - (p.accuracy || 0));
            const latNorm = Math.min(1, (p.avg_latency_ms || 0) / 3000);
            let recency = 0;
            if (p.last_wrong_at) {
                const daysSince = (now - new Date(p.last_wrong_at).getTime()) / 86400000;
                recency = Math.max(0, 1 - daysSince / 14);
            }
            const recentWrongRate = p.recent_n ? (p.recent_wrong / p.recent_n) : 0;
            const score = (err * 3) + (latNorm * 1) + (recency * 2) + (recentWrongRate * 1.5);
            return { ...p, _score: score };
        }).sort((a, b) => b._score - a._score).slice(0, 12);

        const rows = scored.map(p => {
            const aDisp = Number.isInteger(p.a) ? p.a : Math.round(p.a * 10) / 10;
            const bDisp = Number.isInteger(p.b) ? p.b : Math.round(p.b * 10) / 10;
            const accPct = Math.round((p.accuracy || 0) * 100);
            const accCls = accPct < 50 ? 'is-bad' : accPct < 80 ? 'is-mid' : 'is-ok';
            const recentTag = p.last_wrong_at
                ? `<span class="math-weak-preview__recent" title="Last wrong: ${new Date(p.last_wrong_at).toLocaleString()}">·  wrong ${timeAgo(p.last_wrong_at)}</span>`
                : '';
            return `
                <div class="math-weak-row">
                    <code class="math-weak-row__pair">${aDisp} ${displayOp(p.op)} ${bDisp}</code>
                    <span class="math-weak-row__acc ${accCls}">${accPct}%</span>
                    <span class="math-weak-row__lat">${p.avg_latency_ms || '—'} ms</span>
                    <span class="math-weak-row__n">${p.n}×</span>
                    ${recentTag}
                </div>`;
        }).join('');

        const modeLabel = config.mode === 'drill'
            ? 'Drill mode locks onto one pair at a time. Hit it correctly 3 times in a row to advance.'
            : 'Weakness mode mixes these in (75%) with random fills (25%). Recency boosts priority.';

        el.innerHTML = `
            <div class="math-weak-preview__head">
                <strong>Top ${scored.length} pairs to train</strong>
                <span class="math-weak-preview__hint">${modeLabel}</span>
            </div>
            <div class="math-weak-preview__list">${rows}</div>`;
    }

    function renderMasteryDiff() {
        const el = $('mathMastery');
        if (!el) return;
        if ((config.mode !== 'weakness' && config.mode !== 'drill') || !session.weakSnapshot || !session.weakSnapshot.size) {
            el.hidden = true;
            el.innerHTML = '';
            return;
        }
        // Aggregate this session's attempts by (op,a,b) for pairs we had in the snapshot
        const inSession = new Map();
        for (const a of session.attempts) {
            if (a.a_value == null || a.b_value == null) continue;
            const key = `${a.op}|${a.a_value}|${a.b_value}`;
            if (!session.weakSnapshot.has(key)) continue;
            let s = inSession.get(key);
            if (!s) { s = { n: 0, correct: 0, latSum: 0 }; inSession.set(key, s); }
            s.n += 1;
            s.correct += a.is_correct ? 1 : 0;
            s.latSum += a.latency_ms;
        }
        if (!inSession.size) { el.hidden = true; return; }

        let improved = 0, regressed = 0, same = 0;
        const rows = [];
        for (const [key, s] of inSession.entries()) {
            const before = session.weakSnapshot.get(key);
            const beforeAcc = before.acc || 0;
            const nowAcc = s.n ? (s.correct / s.n) : 0;
            const beforeLat = before.lat || 0;
            const nowLat = s.n ? Math.round(s.latSum / s.n) : 0;
            const accDelta = nowAcc - beforeAcc;
            const latDelta = beforeLat ? (nowLat - beforeLat) : 0;
            // "Improved" = acc went up by 10pp+, OR latency dropped 200ms+ at same/better acc
            if (accDelta >= 0.1 || (accDelta >= -0.05 && latDelta <= -200)) improved++;
            else if (accDelta <= -0.15 || (accDelta <= 0.05 && latDelta >= 400)) regressed++;
            else same++;

            const [op, a, b] = key.split('|');
            rows.push({
                op, a, b,
                beforeAcc, nowAcc, beforeLat, nowLat, n: s.n,
                accDelta, latDelta,
            });
        }
        // Sort: improvements first, then regressions, then same
        rows.sort((x, y) => y.accDelta - x.accDelta);

        const rowsHtml = rows.slice(0, 12).map(r => {
            const sign = r.accDelta > 0.001 ? '+' : '';
            const accCls = r.accDelta > 0.05 ? 'is-ok' : r.accDelta < -0.05 ? 'is-bad' : '';
            const latCls = r.latDelta < -100 ? 'is-ok' : r.latDelta > 200 ? 'is-bad' : '';
            const aDisp = Number.isInteger(+r.a) ? r.a : (Math.round(+r.a * 10) / 10);
            const bDisp = Number.isInteger(+r.b) ? r.b : (Math.round(+r.b * 10) / 10);
            return `
                <div class="math-mastery-row">
                    <code class="math-weak-row__pair">${aDisp} ${displayOp(r.op)} ${bDisp}</code>
                    <span class="math-mastery-row__acc ${accCls}">
                        ${Math.round(r.beforeAcc * 100)}% → ${Math.round(r.nowAcc * 100)}%
                        <em>(${sign}${Math.round(r.accDelta * 100)}pp)</em>
                    </span>
                    <span class="math-mastery-row__lat ${latCls}">
                        ${r.beforeLat || '—'} → ${r.nowLat} ms
                    </span>
                    <span class="math-mastery-row__n">${r.n}×</span>
                </div>`;
        }).join('');

        el.hidden = false;
        el.innerHTML = `
            <div class="math-mastery__head">
                <h3>Weak-spot mastery this session</h3>
                <div class="math-mastery__chips">
                    <span class="math-mastery__chip is-ok">▲ ${improved} improved</span>
                    <span class="math-mastery__chip is-mid">= ${same} unchanged</span>
                    <span class="math-mastery__chip is-bad">▼ ${regressed} regressed</span>
                </div>
            </div>
            <div class="math-mastery__list">${rowsHtml}</div>`;
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
        // Drill mode: lock onto the worst pair until mastered, then move on
        if (config.mode === 'drill' && session.weakPool && session.weakPool.length) {
            const pair = pickForDrill();
            if (pair) p = buildFromPair(pair);
            renderDrillBadge();
        }
        // Weakness mode: 75% pick from weak pool, 25% normal random for variety
        if (!p && config.mode === 'weakness' && session.weakPool && session.weakPool.length && Math.random() < 0.75) {
            const pair = pickFromWeakPool();
            if (pair) p = buildFromPair(pair);
        }
        if (!p) p = genProblem(config);
        session.current = p;
        session.currentShownAt = performance.now();
        // Some problem strings already include their own '= ?' suffix (conversions, "what %")
        const display = /[?=]/.test(p.problem) ? p.problem : `${p.problem} = ?`;
        $('mathProblem').textContent = display;

        // Render input or choices
        const inputForm = $('mathAnswerForm');
        const choicesEl = $('mathChoices');
        if (config.input === 'mc') {
            inputForm.hidden = true;
            choicesEl.hidden = false;
            renderChoices(p);
        } else {
            inputForm.hidden = false;
            choicesEl.hidden = true;
            choicesEl.innerHTML = '';
            $('mathAnswerInput').value = '';
            $('mathAnswerInput').focus();
        }
    }

    // ---------- Multiple choice ----------
    function formatChoice(v) {
        if (Number.isInteger(v)) return String(v);
        // Round to 3 dp then strip trailing zeros
        return String(Math.round(v * 1000) / 1000);
    }

    function genDistractors(p) {
        const correct = p.answer;
        const out = new Set();
        const candidates = [];

        // Common error patterns by op
        if (p.op === '+' || p.op === '-') {
            candidates.push(correct + 1, correct - 1, correct + 10, correct - 10, correct + p.b, correct - p.b);
        } else if (p.op === '*') {
            candidates.push(
                correct + p.a, correct - p.a, correct + p.b, correct - p.b,
                p.a * (p.b + 1), p.a * (p.b - 1), (p.a + 1) * p.b, (p.a - 1) * p.b,
                correct + 10, correct - 10
            );
        } else if (p.op === '/') {
            candidates.push(correct + 1, correct - 1, correct * 2, Math.round(correct / 2), p.a, p.b);
        } else if (p.op === '%') {
            candidates.push(correct * 10, correct / 10, correct + 5, correct - 5, correct * 2, correct + 1, correct - 1);
        } else if (p.op === 'f') {
            candidates.push(correct + 0.1, correct - 0.1, correct + 0.05, correct - 0.05, correct * 2, correct / 2);
        } else if (p.op === 's') {
            candidates.push(correct + 1, correct - 1, correct + p.b, correct - p.b, Math.round(correct * 1.1));
        } else if (p.op === 'c') {
            candidates.push(correct * 10, correct / 10, correct + 10, correct - 10, correct + 5, correct - 5);
        }
        // Generic perturbations
        candidates.push(
            correct + Math.max(1, Math.round(Math.abs(correct) * 0.1)),
            correct - Math.max(1, Math.round(Math.abs(correct) * 0.1)),
        );

        // Filter: must be valid, distinct from correct, distinct among themselves
        for (const c of candidates) {
            if (!Number.isFinite(c)) continue;
            const v = Number.isInteger(correct) ? Math.round(c) : Math.round(c * 1000) / 1000;
            if (Math.abs(v - correct) < 1e-9) continue;
            if (!config.negatives && v < 0 && correct >= 0) continue;
            out.add(v);
            if (out.size >= 3) break;
        }
        // Top-up with random nearby values if we ran short
        let safety = 20;
        while (out.size < 3 && safety-- > 0) {
            const delta = (Math.random() < 0.5 ? -1 : 1) * (1 + Math.floor(Math.random() * Math.max(2, Math.abs(correct) * 0.3)));
            const v = Number.isInteger(correct) ? (correct + delta) : Math.round((correct + delta) * 1000) / 1000;
            if (Math.abs(v - correct) < 1e-9) continue;
            out.add(v);
        }
        return [...out].slice(0, 3);
    }

    function renderChoices(p) {
        const el = $('mathChoices');
        const distractors = genDistractors(p);
        const all = [p.answer, ...distractors];
        // Shuffle
        for (let i = all.length - 1; i > 0; i--) {
            const j = Math.floor(Math.random() * (i + 1));
            [all[i], all[j]] = [all[j], all[i]];
        }
        el.innerHTML = all.map(v => `<button type="button" class="math-choice" data-value="${v}">${formatChoice(v)}</button>`).join('');
    }

    function pickChoice(v) {
        if (!session.active || !session.current) return;
        const p = session.current;
        const userVal = Number(v);
        const ok = isCorrect(userVal, p.answer);
        const latency = Math.round(performance.now() - session.currentShownAt);

        session.attempts.push({
            problem: p.problem,
            op: p.op,
            a_value: typeof p.a === 'number' ? p.a : null,
            b_value: typeof p.b === 'number' ? p.b : null,
            user_answer: String(v),
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
        advanceDrill(ok);
        nextProblem();
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
        advanceDrill(ok);

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
        renderMasteryDiff();
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
                weakPreviewCache = null; // refresh weak data with new attempts
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
        loadLatencies();
        loadMistakes();
        loadSessions();
    }

    async function loadLatencies() {
        const el = $('mathLatency');
        if (!el) return;
        try {
            const res = await fetch(`${API}/math/latencies?limit=2000`);
            if (!res.ok) return;
            const data = await res.json();
            renderLatencies(data);
        } catch (err) {
            console.error('Failed to load latencies:', err);
        }
    }

    function renderLatencies(data) {
        const el = $('mathLatency');
        const correct = data.correct || [];
        const wrong = data.wrong || [];
        const total = correct.length + wrong.length;
        if (total < 5) {
            el.innerHTML = '<div class="math-empty">Not enough data yet</div>';
            return;
        }
        // Bucket: 0-500, 500-1000, ..., 5000+, with 500ms resolution
        const BUCKET_MS = 500;
        const N_BUCKETS = 12; // 0-6s, last bucket = 6000+
        const cBuckets = new Array(N_BUCKETS).fill(0);
        const wBuckets = new Array(N_BUCKETS).fill(0);
        const bucketIdx = v => Math.min(N_BUCKETS - 1, Math.floor(v / BUCKET_MS));
        correct.forEach(v => cBuckets[bucketIdx(v)]++);
        wrong.forEach(v => wBuckets[bucketIdx(v)]++);
        const maxCount = Math.max(...cBuckets, ...wBuckets, 1);

        // Compute medians
        const median = (arr) => {
            if (!arr.length) return null;
            const s = [...arr].sort((a, b) => a - b);
            const m = Math.floor(s.length / 2);
            return s.length % 2 ? s[m] : Math.round((s[m - 1] + s[m]) / 2);
        };
        const cMed = median(correct), wMed = median(wrong);

        let html = `
            <div class="math-latency-summary">
                <span><span class="math-latency-dot is-ok"></span>Correct median: <strong>${cMed != null ? cMed + ' ms' : '—'}</strong> (${correct.length})</span>
                <span><span class="math-latency-dot is-bad"></span>Wrong median: <strong>${wMed != null ? wMed + ' ms' : '—'}</strong> (${wrong.length})</span>
            </div>
            <div class="math-latency-grid" style="grid-template-columns: repeat(${N_BUCKETS}, 1fr);">
        `;
        for (let i = 0; i < N_BUCKETS; i++) {
            const cH = (cBuckets[i] / maxCount) * 100;
            const wH = (wBuckets[i] / maxCount) * 100;
            const label = i === N_BUCKETS - 1 ? `${(i * BUCKET_MS / 1000).toFixed(1)}+` : `${((i + 1) * BUCKET_MS / 1000).toFixed(1)}`;
            html += `
                <div class="math-latency-col" title="bucket ${(i * BUCKET_MS)}–${((i + 1) * BUCKET_MS)}ms · ${cBuckets[i]} ok / ${wBuckets[i]} wrong">
                    <div class="math-latency-bars">
                        <div class="math-latency-bar is-ok" style="height: ${cH}%;"></div>
                        <div class="math-latency-bar is-bad" style="height: ${wH}%;"></div>
                    </div>
                    <div class="math-latency-axis">${label}s</div>
                </div>`;
        }
        html += `</div>`;
        el.innerHTML = html;
    }

    async function loadMistakes() {
        const el = $('mathMistakes');
        if (!el) return;
        try {
            const res = await fetch(`${API}/math/mistakes?limit=20`);
            if (!res.ok) return;
            const data = await res.json();
            renderMistakes(data);
        } catch (err) {
            console.error('Failed to load mistakes:', err);
        }
    }

    function renderMistakes(rows) {
        const el = $('mathMistakes');
        if (!Array.isArray(rows) || !rows.length) {
            el.innerHTML = '<div class="math-empty">No mistakes recorded yet — keep going.</div>';
            return;
        }
        const items = rows.map(r => {
            const tsLocal = new Date(r.ts).toLocaleString();
            const cleanProblem = (r.problem || '').replace(/[?=]/g, '').trim();
            return `
                <div class="math-mistake">
                    <code class="math-mistake__q">${cleanProblem} = ${r.correct_answer}</code>
                    <span class="math-mistake__meta">
                        you said <strong>${r.user_answer ?? '—'}</strong> · ${r.latency_ms} ms
                    </span>
                    <span class="math-mistake__ts" title="${tsLocal}">${timeAgo(r.ts)}</span>
                </div>`;
        }).join('');
        el.innerHTML = items;
    }

    function timeAgo(iso) {
        const t = new Date(iso).getTime();
        const diff = Math.max(0, Date.now() - t) / 1000;
        if (diff < 60) return `${Math.round(diff)}s ago`;
        if (diff < 3600) return `${Math.round(diff / 60)}m ago`;
        if (diff < 86400) return `${Math.round(diff / 3600)}h ago`;
        return `${Math.round(diff / 86400)}d ago`;
    }

    async function loadSessions() {
        const el = $('mathSessions');
        if (!el) return;
        try {
            const res = await fetch(`${API}/math/sessions?limit=20`);
            if (!res.ok) return;
            const rows = await res.json();
            renderSessions(rows);
        } catch (err) {
            console.error('Failed to load sessions:', err);
        }
    }

    function renderSessions(rows) {
        const el = $('mathSessions');
        if (!Array.isArray(rows) || !rows.length) {
            el.innerHTML = '<div class="math-empty">No sessions yet</div>';
            return;
        }
        el.innerHTML = rows.map(r => {
            const total = (r.correct || 0) + (r.wrong || 0);
            const acc = total ? Math.round((r.correct / total) * 100) : 0;
            const ops = (r.settings && r.settings.ops) ? r.settings.ops.join(' ') : '—';
            return `
                <div class="math-session-row" data-id="${r.id}">
                    <span class="math-session-row__ts" title="${new Date(r.started_at).toLocaleString()}">${timeAgo(r.started_at)}</span>
                    <span class="math-session-row__score">${(r.score_per_min ?? 0).toFixed(1)} <span class="math-session-row__unit">/min</span></span>
                    <span class="math-session-row__meta">${total} attempts · ${acc}%</span>
                    <span class="math-session-row__ops">${ops}</span>
                    <span class="math-session-row__dur">${r.duration_s}s</span>
                    <button type="button" class="math-session-row__del" data-id="${r.id}" title="Delete this session">×</button>
                </div>`;
        }).join('');
    }

    async function deleteSession(id) {
        if (!id) return;
        if (!confirm('Delete this session and all its attempts?')) return;
        try {
            const res = await fetch(`${API}/math/session/${id}`, { method: 'DELETE' });
            if (!res.ok) { alert('Delete failed'); return; }
            loadStats();
        } catch (err) {
            console.error('Delete failed:', err);
        }
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
            const labelMap = {
                '+': 'Addition', '-': 'Subtraction', '*': 'Multiplication', '/': 'Division',
                '%': 'Percentages', 'f': 'Fractions', 's': 'Squares & roots', 'c': 'Conversions',
            };
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
        loadWeakPreview();

        $('mathStartBtn').addEventListener('click', startSession);
        $('mathAbortBtn').addEventListener('click', () => endSession({ aborted: true }));
        $('mathAgainBtn').addEventListener('click', startSession);
        $('mathBackBtn').addEventListener('click', () => { showView('setup'); });

        $('mathAnswerForm').addEventListener('submit', e => {
            e.preventDefault();
            submitAnswer();
        });

        // Multiple choice click delegation
        const choicesEl = $('mathChoices');
        if (choicesEl) {
            choicesEl.addEventListener('click', e => {
                const btn = e.target.closest('.math-choice');
                if (!btn) return;
                pickChoice(btn.dataset.value);
            });
        }

        // Session delete delegation
        const sessionsEl = $('mathSessions');
        if (sessionsEl) {
            sessionsEl.addEventListener('click', e => {
                const btn = e.target.closest('.math-session-row__del');
                if (!btn) return;
                deleteSession(btn.dataset.id);
            });
        }

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
