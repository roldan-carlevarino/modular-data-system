/* Careers tab — kanban pipeline tracker (IIFE module) */
(() => {
    const API = "https://api-dashboard-production-fc05.up.railway.app";

    // Canonical kanban columns
    const STATUSES = [
        "saved", "applied", "oa", "phone", "onsite",
        "offer", "accepted", "rejected", "withdrawn", "ghosted",
    ];
    const STATUS_LABELS = {
        saved: "Saved", applied: "Applied", oa: "OA", phone: "Phone",
        onsite: "Onsite", offer: "Offer", accepted: "Accepted",
        rejected: "Rejected", withdrawn: "Withdrawn", ghosted: "Ghosted",
    };
    const TYPE_LABELS = {
        internship: "Internship", new_grad: "New-grad", research: "Research",
        phd: "PhD", summer_school: "Summer school", grant: "Grant",
    };
    const EVENT_KIND_LABELS = {
        applied: "Applied", oa_received: "OA received", oa_done: "OA done",
        interview_phone: "Phone interview", interview_onsite: "Onsite interview",
        interview: "Interview", offer: "Offer", accepted: "Accepted",
        rejection: "Rejection", withdrawn: "Withdrawn", ghosted: "Ghosted",
        status_change: "Status change", note: "Note", followup: "Follow-up",
        email: "Email", call: "Call",
    };
    const EVENT_KIND_OPTIONS = [
        "note", "followup", "email", "call",
        "interview", "interview_phone", "interview_onsite",
        "oa_received", "oa_done", "offer", "rejection",
    ];

    const RELATIONSHIP_OPTIONS = [
        "recruiter", "referral", "interviewer", "hiring_manager", "peer", "other",
    ];
    const RELATIONSHIP_LABELS = {
        recruiter: "Recruiter", referral: "Referral", interviewer: "Interviewer",
        hiring_manager: "Hiring manager", peer: "Peer", other: "Other",
    };

    const state = {
        items: [],
        filters: { type: "", q: "", active_only: false },
        editing: null,
        viewing: null,    // id of currently open drawer
        events: [],
        contacts: [],
        loaded: false,
    };

    // ---- People (research / outreach) ----
    const PERSON_CATEGORY_LABELS = {
        researcher: "Researcher", phd_student: "PhD student", junior: "Junior",
        alumni: "Alumni", recruiter: "Recruiter", hiring_manager: "Hiring manager",
        engineer: "Engineer", founder: "Founder", professor: "Professor", other: "Other",
    };
    const OUTREACH_LABELS = {
        to_contact: "To contact", contacted: "Contacted", replied: "Replied",
        in_conversation: "In conversation", intro_done: "Intro done",
        stalled: "Stalled", archived: "Archived",
    };

    const peopleState = {
        items: [],
        filters: { category: "", outreach_status: "", tag: "", q: "" },
        editing: null,
        loaded: false,
    };
    let currentView = "pipeline";

    const $ = (id) => document.getElementById(id);

    // ---- Init ----
    function init() {
        $("careerNewBtn").addEventListener("click", () => openModal(null));

        // Search debounced
        let st;
        $("careerSearch").addEventListener("input", (e) => {
            clearTimeout(st);
            st = setTimeout(() => {
                state.filters.q = e.target.value.trim();
                loadAndRender();
            }, 250);
        });

        $("careerActiveOnly").addEventListener("change", (e) => {
            state.filters.active_only = e.target.checked;
            loadAndRender();
        });

        // Type chips
        document.querySelectorAll('[data-group="career-type"] .careers__chip').forEach((b) => {
            b.addEventListener("click", () => {
                document.querySelectorAll('[data-group="career-type"] .careers__chip')
                    .forEach((x) => x.classList.remove("is-active"));
                b.classList.add("is-active");
                state.filters.type = b.dataset.value;
                loadAndRender();
            });
        });

        // Modal wiring
        $("careerModalClose").addEventListener("click", closeModal);
        $("careerModalCancel").addEventListener("click", closeModal);
        $("careerModalSave").addEventListener("click", saveModal);
        $("careerModalDelete").addEventListener("click", deleteCurrent);

        // Overview toggle
        $("careerOverviewToggle").addEventListener("click", toggleOverview);

        // Drawer wiring
        $("careerDrawerClose").addEventListener("click", closeDrawer);
        $("careerDrawerBackdrop").addEventListener("click", closeDrawer);
        $("careerDrawerEdit").addEventListener("click", () => {
            const id = state.viewing;
            closeDrawer();
            if (id) openModal(id);
        });
        document.addEventListener("keydown", (e) => {
            if (e.key === "Escape") {
                if ($("careerDrawer").style.display !== "none") closeDrawer();
                else if ($("careerModal").style.display !== "none") closeModal();
                else if ($("careerPersonModal").style.display !== "none") closePersonModal();
            }
        });

        // ---- People sub-view wiring ----
        document.querySelectorAll(".careers__subtab").forEach((b) => {
            b.addEventListener("click", () => switchView(b.dataset.view));
        });

        $("careerNewPersonBtn").addEventListener("click", () => openPersonModal(null));

        let pst;
        $("careerPersonSearch").addEventListener("input", (e) => {
            clearTimeout(pst);
            pst = setTimeout(() => {
                peopleState.filters.q = e.target.value.trim();
                loadPeople();
            }, 250);
        });
        $("careerPersonStatus").addEventListener("change", (e) => {
            peopleState.filters.outreach_status = e.target.value;
            loadPeople();
        });
        document.querySelectorAll('[data-group="person-category"] .careers__chip').forEach((b) => {
            b.addEventListener("click", () => {
                document.querySelectorAll('[data-group="person-category"] .careers__chip')
                    .forEach((x) => x.classList.remove("is-active"));
                b.classList.add("is-active");
                peopleState.filters.category = b.dataset.value;
                loadPeople();
            });
        });

        $("careerPersonModalClose").addEventListener("click", closePersonModal);
        $("careerPersonModalCancel").addEventListener("click", closePersonModal);
        $("careerPersonModalSave").addEventListener("click", savePersonModal);
        $("careerPersonModalDelete").addEventListener("click", deletePerson);

        // Lazy-load on first activation
        $("tab13").addEventListener("change", () => {
            if ($("tab13").checked && !state.loaded) {
                state.loaded = true;
                loadAndRender();
            }
        });
        if ($("tab13") && $("tab13").checked) {
            state.loaded = true;
            loadAndRender();
        }
    }

    // ---- Data ----
    async function api(path, method = "GET", body) {
        const opts = { method };
        if (body) {
            opts.headers = { "Content-Type": "application/json" };
            opts.body = JSON.stringify(body);
        }
        const r = await fetch(`${API}${path}`, opts);
        if (!r.ok) {
            let msg = `${r.status} ${r.statusText}`;
            try {
                const j = await r.json();
                if (typeof j.detail === "string") {
                    msg = j.detail;
                } else if (Array.isArray(j.detail)) {
                    // FastAPI 422: list of {loc, msg, type}
                    msg = j.detail.map((d) =>
                        `${(d.loc || []).join(".")}: ${d.msg}`).join(" | ");
                } else if (j.detail) {
                    msg = JSON.stringify(j.detail);
                }
            } catch (_) {}
            throw new Error(msg);
        }
        if (r.status === 204) return null;
        return r.json();
    }

    async function loadAndRender() {
        try {
            const params = new URLSearchParams();
            if (state.filters.type) params.set("type", state.filters.type);
            if (state.filters.q) params.set("q", state.filters.q);
            if (state.filters.active_only) params.set("active_only", "true");
            params.set("limit", "500");
            state.items = await api(`/careers?${params}`);
            await loadStats();
            renderBoard();
            if (overviewVisible) loadOverview();
        } catch (e) {
            $("careerBoard").innerHTML = `<div class="careers__error">Load failed: ${escapeHtml(e.message)}</div>`;
        }
    }

    async function loadStats() {
        try {
            const s = await api("/careers/stats/summary");
            $("careerStats").innerHTML = `
                <span><strong>${s.active}</strong> active</span>
                <span>· <strong>${s.total}</strong> total</span>
                ${s.upcoming_deadlines_14d ? `<span>· <strong>${s.upcoming_deadlines_14d}</strong> deadline${s.upcoming_deadlines_14d > 1 ? "s" : ""} ≤14d</span>` : ""}
            `;
        } catch (_) {
            $("careerStats").innerHTML = "";
        }
    }

    // ---- Overview widgets ----
    let overviewVisible = false;

    async function toggleOverview() {
        overviewVisible = !overviewVisible;
        const panel = $("careerOverview");
        const btn = $("careerOverviewToggle");
        if (overviewVisible) {
            panel.style.display = "grid";
            btn.textContent = "Hide overview";
            await loadOverview();
        } else {
            panel.style.display = "none";
            btn.textContent = "Show overview";
        }
    }

    async function loadOverview() {
        const panel = $("careerOverview");
        panel.innerHTML = '<div class="careers__hint">Loading…</div>';
        try {
            const w = await api("/careers/intel/widgets?deadline_days=21&stale_days=14");
            renderOverview(w);
        } catch (e) {
            panel.innerHTML = `<div class="careers__error">Overview failed: ${escapeHtml(e.message)}</div>`;
        }
    }

    function renderOverview(w) {
        const pipeline = STATUSES.map((s) => `
            <div class="careers__widget-row">
                <span class="careers__widget-label careers__widget-label--${s}">${STATUS_LABELS[s]}</span>
                <span class="careers__widget-bar"><span style="width:${w.active ? Math.min(100, ((w.by_status[s] || 0) / Math.max(1, w.active)) * 100) : 0}%"></span></span>
                <span class="careers__widget-num">${w.by_status[s] || 0}</span>
            </div>
        `).join("");

        const deadlineList = w.deadlines.length
            ? w.deadlines.map((d) => `
                <li data-id="${d.id}" class="careers__widget-item">
                    <span class="careers__widget-when">${d.deadline}</span>
                    <span class="careers__widget-co">${escapeHtml(d.company)}</span>
                    <span class="careers__widget-role">${escapeHtml(d.role)}</span>
                </li>
            `).join("")
            : '<li class="careers__hint">No upcoming deadlines.</li>';

        const interviewList = w.interviews.length
            ? w.interviews.map((d) => `
                <li data-id="${d.id}" class="careers__widget-item">
                    <span class="careers__widget-pill careers__widget-pill--${d.status}">${STATUS_LABELS[d.status]}</span>
                    <span class="careers__widget-co">${escapeHtml(d.company)}</span>
                    <span class="careers__widget-role">${escapeHtml(d.role)}</span>
                </li>
            `).join("")
            : '<li class="careers__hint">No active interviews.</li>';

        const stalledList = w.stalled.length
            ? w.stalled.map((d) => `
                <li data-id="${d.id}" class="careers__widget-item">
                    <span class="careers__widget-when">${d.last_activity ? d.last_activity.slice(0, 10) : "—"}</span>
                    <span class="careers__widget-co">${escapeHtml(d.company)}</span>
                    <span class="careers__widget-role">${escapeHtml(d.role)}</span>
                </li>
            `).join("")
            : '<li class="careers__hint">Nothing stalled. Nice.</li>';

        const offerList = w.offers.length
            ? w.offers.map((d) => `
                <li data-id="${d.id}" class="careers__widget-item">
                    <span class="careers__widget-co">${escapeHtml(d.company)}</span>
                    <span class="careers__widget-role">${escapeHtml(d.role)}</span>
                    ${d.salary ? `<span class="careers__widget-num">${escapeHtml(d.salary)}</span>` : ""}
                    ${d.deadline ? `<span class="careers__widget-when">decide by ${d.deadline}</span>` : ""}
                </li>
            `).join("")
            : "";

        $("careerOverview").innerHTML = `
            <article class="careers__widget">
                <h4>Pipeline <span class="careers__widget-sub">${w.active} active</span></h4>
                <div class="careers__widget-body">${pipeline}</div>
            </article>
            <article class="careers__widget">
                <h4>Upcoming deadlines <span class="careers__widget-sub">≤21d</span></h4>
                <ul class="careers__widget-body">${deadlineList}</ul>
            </article>
            <article class="careers__widget">
                <h4>Active interviews</h4>
                <ul class="careers__widget-body">${interviewList}</ul>
            </article>
            <article class="careers__widget">
                <h4>Stalled <span class="careers__widget-sub">no activity ≥14d</span></h4>
                <ul class="careers__widget-body">${stalledList}</ul>
            </article>
            ${offerList ? `
                <article class="careers__widget careers__widget--span">
                    <h4>Open offers <span class="careers__widget-sub">${w.offers.length}</span></h4>
                    <ul class="careers__widget-body">${offerList}</ul>
                </article>
            ` : ""}
        `;

        $("careerOverview").querySelectorAll(".careers__widget-item[data-id]").forEach((li) => {
            li.style.cursor = "pointer";
            li.addEventListener("click", () => openDrawer(parseInt(li.dataset.id, 10)));
        });
    }

    // ---- Renderers ----
    function renderBoard() {
        const board = $("careerBoard");
        // Group by status
        const groups = {};
        STATUSES.forEach((s) => (groups[s] = []));
        state.items.forEach((it) => {
            if (groups[it.status]) groups[it.status].push(it);
            else groups.saved.push(it); // unknown statuses fallback
        });

        board.innerHTML = STATUSES.map((s) => `
            <section class="careers__col" data-status="${s}">
                <header class="careers__col-head careers__col-head--${s}">
                    <span>${STATUS_LABELS[s]}</span>
                    <em>${groups[s].length}</em>
                </header>
                <div class="careers__col-body" data-status="${s}">
                    ${groups[s].map(renderCard).join("") ||
                      '<div class="careers__col-empty">—</div>'}
                </div>
            </section>
        `).join("");

        // Drag handlers
        board.querySelectorAll(".careers__card").forEach((card) => {
            card.addEventListener("dragstart", onDragStart);
            card.addEventListener("dragend", onDragEnd);
            card.addEventListener("click", () => openDrawer(parseInt(card.dataset.id, 10)));
        });
        board.querySelectorAll(".careers__col-body").forEach((col) => {
            col.addEventListener("dragover", onDragOver);
            col.addEventListener("dragleave", onDragLeave);
            col.addEventListener("drop", onDrop);
        });
    }

    function renderCard(it) {
        return `
            <article class="careers__card" draggable="true" data-id="${it.id}" data-status="${it.status}">
                <div class="careers__card-head">
                    <span class="careers__card-type">${TYPE_LABELS[it.type] || it.type}</span>
                    ${deadlineBadge(it)}
                </div>
                <div class="careers__card-company">${escapeHtml(it.company)}</div>
                <div class="careers__card-role">${escapeHtml(it.role)}</div>
                <div class="careers__card-meta">
                    ${it.location ? `<span>📍 ${escapeHtml(it.location)}</span>` : ""}
                    ${it.applied_at ? `<span>✉ ${it.applied_at}</span>` : ""}
                    ${it.url ? `<a href="${escapeAttr(it.url)}" target="_blank" rel="noopener" onclick="event.stopPropagation()">↗</a>` : ""}
                </div>
            </article>
        `;
    }

    function deadlineBadge(it) {
        if (!it.deadline) return "";
        const today = new Date(); today.setHours(0, 0, 0, 0);
        const due = new Date(it.deadline + "T00:00:00");
        const days = Math.round((due - today) / 86400000);
        let cls = "far", label;
        if (days < 0) { cls = "over"; label = `${-days}d ago`; }
        else if (days === 0) { cls = "soon"; label = "today"; }
        else if (days <= 7) { cls = "soon"; label = `${days}d`; }
        else if (days <= 30) { cls = "mid"; label = `${days}d`; }
        else { label = `${days}d`; }
        return `<span class="careers__deadline careers__deadline--${cls}" title="Deadline ${it.deadline}">⏳ ${label}</span>`;
    }

    // ---- Drag & drop ----
    let dragId = null;

    function onDragStart(e) {
        dragId = parseInt(e.currentTarget.dataset.id, 10);
        e.currentTarget.classList.add("is-dragging");
        e.dataTransfer.effectAllowed = "move";
    }
    function onDragEnd(e) {
        e.currentTarget.classList.remove("is-dragging");
        document.querySelectorAll(".careers__col-body.is-over")
            .forEach((c) => c.classList.remove("is-over"));
    }
    function onDragOver(e) {
        e.preventDefault();
        e.dataTransfer.dropEffect = "move";
        e.currentTarget.classList.add("is-over");
    }
    function onDragLeave(e) {
        e.currentTarget.classList.remove("is-over");
    }
    async function onDrop(e) {
        e.preventDefault();
        e.currentTarget.classList.remove("is-over");
        if (!dragId) return;
        const newStatus = e.currentTarget.dataset.status;
        const item = state.items.find((x) => x.id === dragId);
        if (!item || item.status === newStatus) {
            dragId = null;
            return;
        }
        // Optimistic update
        const prev = item.status;
        item.status = newStatus;
        renderBoard();
        try {
            await api(`/careers/${dragId}`, "PATCH", { status: newStatus });
            await loadStats();
            if (state.viewing === dragId) {
                state.events = await api(`/careers/${dragId}/events`);
                renderDrawer();
            }
        } catch (err) {
            item.status = prev;
            renderBoard();
            alert(`Move failed: ${err.message}`);
        }
        dragId = null;
    }

    // ---- Drawer (read view + timeline) ----
    async function openDrawer(id) {
        state.viewing = id;
        state.events = [];
        state.contacts = [];
        $("careerDrawer").style.display = "block";
        requestAnimationFrame(() => $("careerDrawer").classList.add("is-open"));
        renderDrawer();
        try {
            const [events, contacts] = await Promise.all([
                api(`/careers/${id}/events`),
                api(`/careers/${id}/contacts`),
            ]);
            state.events = events;
            state.contacts = contacts;
            renderDrawer();
        } catch (e) {
            $("careerDrawerBody").innerHTML +=
                `<div class="careers__error">Load failed: ${escapeHtml(e.message)}</div>`;
        }
    }

    function closeDrawer() {
        $("careerDrawer").classList.remove("is-open");
        state.viewing = null;
        state.events = [];
        state.contacts = [];
        setTimeout(() => { $("careerDrawer").style.display = "none"; }, 180);
    }

    function renderDrawer() {
        const id = state.viewing;
        if (!id) return;
        const it = state.items.find((x) => x.id === id);
        if (!it) { closeDrawer(); return; }

        $("careerDrawerTitle").innerHTML = `
            <div class="careers__drawer-company">${escapeHtml(it.company)}</div>
            <div class="careers__drawer-role">${escapeHtml(it.role)}</div>
        `;

        const meta = [];
        meta.push(`<span class="careers__drawer-pill careers__drawer-pill--${it.status}">${STATUS_LABELS[it.status] || it.status}</span>`);
        meta.push(`<span class="careers__drawer-pill">${TYPE_LABELS[it.type] || it.type}</span>`);
        if (it.location) meta.push(`<span>📍 ${escapeHtml(it.location)}</span>`);
        if (it.deadline) meta.push(`<span>⏳ Deadline ${it.deadline}</span>`);
        if (it.applied_at) meta.push(`<span>✉ Applied ${it.applied_at}</span>`);
        if (it.start_date || it.end_date) {
            meta.push(`<span>📅 ${it.start_date || "?"} → ${it.end_date || "?"}</span>`);
        }
        if (it.salary) meta.push(`<span>💰 ${escapeHtml(it.salary)}</span>`);
        if (it.source) meta.push(`<span>via ${escapeHtml(it.source)}</span>`);

        const links = it.url
            ? `<div class="careers__drawer-links"><a href="${escapeAttr(it.url)}" target="_blank" rel="noopener">Open job posting ↗</a></div>`
            : "";

        const notes = it.notes
            ? `<div class="careers__drawer-notes"><pre>${escapeHtml(it.notes)}</pre></div>`
            : "";

        const addForm = `
            <form class="careers__event-form" id="careerEventForm">
                <select id="careerEventKind" class="careers__input careers__event-kind">
                    ${EVENT_KIND_OPTIONS.map((k) =>
                        `<option value="${k}">${EVENT_KIND_LABELS[k] || k}</option>`).join("")}
                </select>
                <input id="careerEventTitle" class="careers__input careers__event-title"
                       type="text" placeholder="Event title (e.g. Phone screen with Alice)">
                <input id="careerEventWhen" class="careers__input careers__event-when" type="datetime-local">
                <textarea id="careerEventBody" class="careers__input careers__event-body"
                          rows="2" placeholder="Notes (optional)"></textarea>
                <button type="submit" class="careers__btn careers__btn--primary careers__event-add">Add event</button>
            </form>
        `;

        const events = state.events.length
            ? state.events.map(renderEvent).join("")
            : '<div class="careers__hint">No events yet — add the first one above.</div>';

        const contactsForm = `
            <form class="careers__contact-form" id="careerContactForm">
                <input id="careerContactName" class="careers__input" type="text" placeholder="Name *" required>
                <select id="careerContactRel" class="careers__input">
                    ${RELATIONSHIP_OPTIONS.map((k) =>
                        `<option value="${k}">${RELATIONSHIP_LABELS[k]}</option>`).join("")}
                </select>
                <input id="careerContactRole" class="careers__input" type="text" placeholder="Title / role">
                <input id="careerContactEmail" class="careers__input" type="email" placeholder="email@…">
                <input id="careerContactPhone" class="careers__input" type="tel" placeholder="Phone">
                <input id="careerContactLinkedin" class="careers__input" type="url" placeholder="LinkedIn URL">
                <textarea id="careerContactNotes" class="careers__input" rows="2" placeholder="Notes"></textarea>
                <button type="submit" class="careers__btn careers__btn--primary">Add contact</button>
            </form>
        `;

        const contacts = state.contacts.length
            ? state.contacts.map(renderContact).join("")
            : '<div class="careers__hint">No contacts yet.</div>';

        $("careerDrawerBody").innerHTML = `
            <div class="careers__drawer-meta">${meta.join(" · ")}</div>
            ${links}
            ${notes}
            <section class="careers__drawer-sect">
                <h4>Contacts (${state.contacts.length})</h4>
                <div class="careers__contacts">${contacts}</div>
                ${contactsForm}
            </section>
            <section class="careers__drawer-sect">
                <h4>Timeline</h4>
                ${addForm}
                <div class="careers__timeline">${events}</div>
            </section>
        `;

        $("careerEventForm").addEventListener("submit", onAddEvent);
        $("careerContactForm").addEventListener("submit", onAddContact);
        $("careerDrawerBody").querySelectorAll(".careers__event-del").forEach((b) => {
            b.addEventListener("click", () => deleteEvent(parseInt(b.dataset.id, 10)));
        });
        $("careerDrawerBody").querySelectorAll(".careers__contact-del").forEach((b) => {
            b.addEventListener("click", () => deleteContact(parseInt(b.dataset.id, 10)));
        });
    }

    function renderContact(c) {
        const rel = RELATIONSHIP_LABELS[c.relationship] || c.relationship;
        const lines = [];
        if (c.role) lines.push(escapeHtml(c.role));
        if (c.email) lines.push(`<a href="mailto:${escapeAttr(c.email)}">${escapeHtml(c.email)}</a>`);
        if (c.phone) lines.push(`<a href="tel:${escapeAttr(c.phone)}">${escapeHtml(c.phone)}</a>`);
        if (c.linkedin) lines.push(`<a href="${escapeAttr(c.linkedin)}" target="_blank" rel="noopener">LinkedIn ↗</a>`);
        const meta = lines.length ? `<div class="careers__contact-meta">${lines.join(" · ")}</div>` : "";
        const notes = c.notes ? `<div class="careers__contact-notes">${escapeHtml(c.notes)}</div>` : "";
        return `
            <article class="careers__contact">
                <div class="careers__contact-head">
                    <span class="careers__contact-name">${escapeHtml(c.name)}</span>
                    <span class="careers__contact-rel">${escapeHtml(rel)}</span>
                    <button class="careers__contact-del" data-id="${c.id}" title="Delete contact">×</button>
                </div>
                ${meta}
                ${notes}
            </article>
        `;
    }

    function renderEvent(ev) {
        const label = EVENT_KIND_LABELS[ev.kind] || ev.kind;
        const auto = ev.metadata && ev.metadata.auto;
        const when = ev.occurred_at ? formatEventTime(ev.occurred_at) : "";
        return `
            <article class="careers__event ${auto ? "is-auto" : ""}">
                <div class="careers__event-head">
                    <span class="careers__event-kind">${escapeHtml(label)}</span>
                    <span class="careers__event-time">${when}</span>
                    <button class="careers__event-del" data-id="${ev.id}" title="Delete event">×</button>
                </div>
                ${ev.title ? `<div class="careers__event-title-line">${escapeHtml(ev.title)}</div>` : ""}
                ${ev.body ? `<div class="careers__event-body-line">${escapeHtml(ev.body)}</div>` : ""}
            </article>
        `;
    }

    async function onAddEvent(e) {
        e.preventDefault();
        const id = state.viewing;
        if (!id) return;
        const kind = $("careerEventKind").value;
        const title = $("careerEventTitle").value.trim();
        const body = $("careerEventBody").value.trim();
        const when = $("careerEventWhen").value;
        const payload = {
            kind,
            title: title || null,
            body: body || null,
            occurred_at: when || null,
        };
        try {
            await api(`/careers/${id}/events`, "POST", payload);
            $("careerEventTitle").value = "";
            $("careerEventBody").value = "";
            $("careerEventWhen").value = "";
            state.events = await api(`/careers/${id}/events`);
            renderDrawer();
        } catch (err) {
            alert(`Add failed: ${err.message}`);
        }
    }

    async function deleteEvent(eid) {
        if (!confirm("Delete this event?")) return;
        try {
            await api(`/careers/events/${eid}`, "DELETE");
            state.events = await api(`/careers/${state.viewing}/events`);
            renderDrawer();
        } catch (err) {
            alert(`Delete failed: ${err.message}`);
        }
    }

    async function onAddContact(e) {
        e.preventDefault();
        const id = state.viewing;
        if (!id) return;
        const name = $("careerContactName").value.trim();
        if (!name) return;
        const payload = {
            name,
            relationship: $("careerContactRel").value,
            role: $("careerContactRole").value.trim() || null,
            email: $("careerContactEmail").value.trim() || null,
            phone: $("careerContactPhone").value.trim() || null,
            linkedin: $("careerContactLinkedin").value.trim() || null,
            notes: $("careerContactNotes").value.trim() || null,
        };
        try {
            await api(`/careers/${id}/contacts`, "POST", payload);
            state.contacts = await api(`/careers/${id}/contacts`);
            renderDrawer();
        } catch (err) {
            alert(`Add failed: ${err.message}`);
        }
    }

    async function deleteContact(cid) {
        if (!confirm("Delete this contact?")) return;
        try {
            await api(`/careers/contacts/${cid}`, "DELETE");
            state.contacts = await api(`/careers/${state.viewing}/contacts`);
            renderDrawer();
        } catch (err) {
            alert(`Delete failed: ${err.message}`);
        }
    }

    function formatEventTime(iso) {
        const d = new Date(iso);
        const now = new Date();
        const sameYear = d.getFullYear() === now.getFullYear();
        const opts = sameYear
            ? { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }
            : { year: "numeric", month: "short", day: "numeric" };
        return d.toLocaleString(undefined, opts);
    }

    // ---- Modal ----
    function openModal(id) {
        state.editing = id;
        $("careerModalTitle").textContent = id ? "Edit application" : "New application";
        $("careerModalDelete").style.display = id ? "inline-flex" : "none";

        const fields = {
            careerFCompany: "company", careerFRole: "role",
            careerFLocation: "location", careerFType: "type",
            careerFStatus: "status", careerFApplied: "applied_at",
            careerFDeadline: "deadline", careerFStart: "start_date",
            careerFEnd: "end_date", careerFSalary: "salary",
            careerFSource: "source", careerFUrl: "url",
            careerFNotes: "notes",
        };

        if (id) {
            const it = state.items.find((x) => x.id === id);
            if (!it) return;
            Object.entries(fields).forEach(([fid, key]) => {
                $(fid).value = it[key] || "";
            });
        } else {
            Object.keys(fields).forEach((fid) => ($(fid).value = ""));
            $("careerFType").value = "internship";
            $("careerFStatus").value = "saved";
        }
        $("careerModal").style.display = "flex";
        setTimeout(() => $("careerFCompany").focus(), 30);
    }

    function closeModal() { $("careerModal").style.display = "none"; }

    async function saveModal() {
        const company = $("careerFCompany").value.trim();
        const role = $("careerFRole").value.trim();
        if (!company || !role) {
            alert("Company and role are required");
            return;
        }
        const payload = {
            company, role,
            type: $("careerFType").value,
            status: $("careerFStatus").value,
            location: $("careerFLocation").value.trim() || null,
            applied_at: $("careerFApplied").value || null,
            deadline: $("careerFDeadline").value || null,
            start_date: $("careerFStart").value || null,
            end_date: $("careerFEnd").value || null,
            salary: $("careerFSalary").value.trim() || null,
            source: $("careerFSource").value.trim() || null,
            url: $("careerFUrl").value.trim() || null,
            notes: $("careerFNotes").value.trim() || null,
        };
        try {
            if (state.editing) {
                await api(`/careers/${state.editing}`, "PATCH", payload);
            } else {
                await api("/careers", "POST", payload);
            }
            const wasEditing = state.editing;
            closeModal();
            await loadAndRender();
            // Re-open drawer if we were editing from the drawer
            if (wasEditing) openDrawer(wasEditing);
        } catch (e) {
            alert(`Save failed: ${e.message}`);
        }
    }

    async function deleteCurrent() {
        if (!state.editing) return;
        const it = state.items.find((x) => x.id === state.editing);
        if (!confirm(`Delete application "${it ? it.company : ""}"?`)) return;
        try {
            await api(`/careers/${state.editing}`, "DELETE");
            closeModal();
            await loadAndRender();
        } catch (e) {
            alert(`Delete failed: ${e.message}`);
        }
    }

    // ---- People view ----
    function switchView(view) {
        if (view === currentView) return;
        currentView = view;
        document.querySelectorAll(".careers__subtab").forEach((b) => {
            b.classList.toggle("is-active", b.dataset.view === view);
        });
        document.querySelectorAll(".careers__view").forEach((v) => {
            v.style.display = v.dataset.view === view ? "" : "none";
        });
        $("careerNewBtn").style.display = view === "pipeline" ? "" : "none";
        $("careerNewPersonBtn").style.display = view === "people" ? "" : "none";
        if (view === "people" && !peopleState.loaded) {
            peopleState.loaded = true;
            loadPeople();
            loadPersonTags();
        }
    }

    async function loadPeople() {
        const list = $("careerPeople");
        list.innerHTML = '<div class="careers__hint">Loading…</div>';
        try {
            const params = new URLSearchParams();
            if (peopleState.filters.category) params.set("category", peopleState.filters.category);
            if (peopleState.filters.outreach_status) params.set("outreach_status", peopleState.filters.outreach_status);
            if (peopleState.filters.tag) params.set("tag", peopleState.filters.tag);
            if (peopleState.filters.q) params.set("q", peopleState.filters.q);
            params.set("sort", "interest");
            params.set("limit", "500");
            peopleState.items = await api(`/careers/people?${params}`);
            renderPeople();
        } catch (e) {
            list.innerHTML = `<div class="careers__error">Load failed: ${escapeHtml(e.message)}</div>`;
        }
    }

    async function loadPersonTags() {
        try {
            const tags = await api("/careers/people-tags");
            renderPersonTags(tags);
        } catch (_) {
            $("careerPersonTags").innerHTML = "";
        }
    }

    function renderPersonTags(tags) {
        const cur = peopleState.filters.tag;
        const html = [
            `<button type="button" class="careers__tag-chip ${!cur ? "is-active" : ""}" data-tag="">all</button>`,
            ...tags.map((t) =>
                `<button type="button" class="careers__tag-chip ${cur === t.tag ? "is-active" : ""}"
                         data-tag="${escapeAttr(t.tag)}">#${escapeHtml(t.tag)} <em>${t.n}</em></button>`),
        ].join("");
        $("careerPersonTags").innerHTML = html;
        $("careerPersonTags").querySelectorAll(".careers__tag-chip").forEach((b) => {
            b.addEventListener("click", () => {
                peopleState.filters.tag = b.dataset.tag;
                renderPersonTags(tags);
                loadPeople();
            });
        });
    }

    function renderPeople() {
        const list = $("careerPeople");
        if (!peopleState.items.length) {
            list.innerHTML = '<div class="careers__hint">No people yet. Click + New person to add someone.</div>';
            return;
        }
        list.innerHTML = peopleState.items.map(renderPersonCard).join("");
        list.querySelectorAll(".careers__person").forEach((card) => {
            card.addEventListener("click", (e) => {
                if (e.target.closest("a")) return;  // let links fire
                openPersonModal(parseInt(card.dataset.id, 10));
            });
        });
    }

    function renderPersonCard(p) {
        const stars = "★".repeat(p.interest) + "☆".repeat(3 - p.interest);
        const tags = (p.tags || []).map((t) =>
            `<span class="careers__person-tag">#${escapeHtml(t)}</span>`).join("");
        const links = [];
        if (p.linkedin) links.push(`<a href="${escapeAttr(p.linkedin)}" target="_blank" rel="noopener" onclick="event.stopPropagation()">in ↗</a>`);
        if (p.website) links.push(`<a href="${escapeAttr(p.website)}" target="_blank" rel="noopener" onclick="event.stopPropagation()">site ↗</a>`);
        if (p.email) links.push(`<a href="mailto:${escapeAttr(p.email)}" onclick="event.stopPropagation()">✉</a>`);
        return `
            <article class="careers__person" data-id="${p.id}">
                <header class="careers__person-head">
                    <span class="careers__person-name">${escapeHtml(p.name)}</span>
                    <span class="careers__person-stars" title="Interest">${stars}</span>
                </header>
                ${p.headline ? `<div class="careers__person-headline">${escapeHtml(p.headline)}</div>` : ""}
                <div class="careers__person-meta">
                    <span class="careers__person-cat">${PERSON_CATEGORY_LABELS[p.category] || p.category}</span>
                    <span class="careers__person-status careers__person-status--${p.outreach_status}">
                        ${OUTREACH_LABELS[p.outreach_status] || p.outreach_status}
                    </span>
                    ${p.company ? `<span>· ${escapeHtml(p.company)}</span>` : ""}
                    ${p.location ? `<span>· ${escapeHtml(p.location)}</span>` : ""}
                </div>
                ${tags ? `<div class="careers__person-tags">${tags}</div>` : ""}
                ${p.notes ? `<div class="careers__person-notes">${escapeHtml(p.notes.length > 220 ? p.notes.slice(0, 220) + "…" : p.notes)}</div>` : ""}
                <footer class="careers__person-foot">
                    ${links.length ? `<span class="careers__person-links">${links.join(" · ")}</span>` : ""}
                    ${p.last_contact_at ? `<span class="careers__person-last">last contact ${p.last_contact_at}</span>` : `<span class="careers__person-last careers__person-last--never">never contacted</span>`}
                </footer>
            </article>
        `;
    }

    function openPersonModal(id) {
        peopleState.editing = id;
        $("careerPersonModalTitle").textContent = id ? "Edit person" : "New person";
        $("careerPersonModalDelete").style.display = id ? "inline-flex" : "none";

        const p = id ? peopleState.items.find((x) => x.id === id) : null;
        $("careerPFName").value = p?.name || "";
        $("careerPFHeadline").value = p?.headline || "";
        $("careerPFCompany").value = p?.company || "";
        $("careerPFLocation").value = p?.location || "";
        $("careerPFLinkedin").value = p?.linkedin || "";
        $("careerPFEmail").value = p?.email || "";
        $("careerPFWebsite").value = p?.website || "";
        $("careerPFCategory").value = p?.category || "researcher";
        $("careerPFStatus").value = p?.outreach_status || "to_contact";
        $("careerPFInterest").value = String(p?.interest || 2);
        $("careerPFLastContact").value = p?.last_contact_at || "";
        $("careerPFTags").value = (p?.tags || []).join(", ");
        $("careerPFNotes").value = p?.notes || "";

        $("careerPersonModal").style.display = "flex";
    }

    function closePersonModal() {
        $("careerPersonModal").style.display = "none";
        peopleState.editing = null;
    }

    async function savePersonModal() {
        const name = $("careerPFName").value.trim();
        if (!name) {
            alert("Name is required");
            return;
        }
        const tagsRaw = $("careerPFTags").value;
        const tags = tagsRaw.split(",").map((t) => t.trim()).filter(Boolean);
        const payload = {
            name,
            headline: $("careerPFHeadline").value.trim() || null,
            company: $("careerPFCompany").value.trim() || null,
            location: $("careerPFLocation").value.trim() || null,
            linkedin: $("careerPFLinkedin").value.trim() || null,
            email: $("careerPFEmail").value.trim() || null,
            website: $("careerPFWebsite").value.trim() || null,
            category: $("careerPFCategory").value,
            outreach_status: $("careerPFStatus").value,
            interest: parseInt($("careerPFInterest").value, 10),
            last_contact_at: $("careerPFLastContact").value || null,
            tags,
            notes: $("careerPFNotes").value.trim() || null,
        };
        try {
            if (peopleState.editing) {
                await api(`/careers/people/${peopleState.editing}`, "PATCH", payload);
            } else {
                await api("/careers/people", "POST", payload);
            }
            closePersonModal();
            await loadPeople();
            loadPersonTags();
        } catch (e) {
            alert(`Save failed: ${e.message}`);
        }
    }

    async function deletePerson() {
        if (!peopleState.editing) return;
        if (!confirm("Delete this person?")) return;
        try {
            await api(`/careers/people/${peopleState.editing}`, "DELETE");
            closePersonModal();
            await loadPeople();
            loadPersonTags();
        } catch (e) {
            alert(`Delete failed: ${e.message}`);
        }
    }

    // ---- Utils ----
    function escapeHtml(s) {
        return String(s == null ? "" : s)
            .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
    }
    function escapeAttr(s) { return escapeHtml(s); }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
