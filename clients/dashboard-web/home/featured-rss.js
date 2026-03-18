const FEATURED_W_ITEMS = [
    'Watchlist summary ready for your daily review.',
    'Macro pulse: check rates and volatility before entries.',
    'Risk note: keep position size aligned with plan.'
];

let featuredRotatorIntervalId = null;
let featuredRotatorCurrentIndex = 0;

async function loadFeaturedNews() {
    const container = document.getElementById('featuredNews');
    if (!container) return;

    try {
        const res = await fetch('https://api-dashboard-production-fc05.up.railway.app/rss/top-global?limit=3');
        if (!res.ok) throw new Error('No se pudieron cargar las noticias destacadas');
        const data = await res.json();

        container.innerHTML = '';
        data.articles.forEach(article => {
            const link = document.createElement('a');
            link.href = article.link;
            link.target = '_blank';
            link.textContent = article.title;
            link.className = 'featured-news-link';
            container.appendChild(link);
        });
    } catch (err) {
        container.innerHTML = '<p>Error cargando noticias destacadas.</p>';
    }
}

function loadFeaturedW() {
    const container = document.getElementById('featuredW');
    if (!container) return;

    container.innerHTML = '';
    FEATURED_W_ITEMS.forEach((text) => {
        const row = document.createElement('div');
        row.className = 'home-info-item';
        row.textContent = text;
        container.appendChild(row);
    });
}

function goToFeaturedRotatorPanel(index) {
    const track = document.getElementById('featuredRotatorTrack');
    const dots = Array.from(document.querySelectorAll('#featuredRotatorDots .home-rotator-dot'));
    if (!track || !dots.length) return;

    const panelCount = dots.length;
    const nextIndex = ((index % panelCount) + panelCount) % panelCount;
    track.style.transform = `translateX(-${nextIndex * 100}%)`;

    dots.forEach((dot, dotIndex) => {
        dot.classList.toggle('is-active', dotIndex === nextIndex);
    });

    track.dataset.activeIndex = String(nextIndex);
    featuredRotatorCurrentIndex = nextIndex;
}

function startFeaturedRotator() {
    const track = document.getElementById('featuredRotatorTrack');
    const dots = document.querySelectorAll('#featuredRotatorDots .home-rotator-dot');
    if (!track || !dots.length) return;

    if (featuredRotatorIntervalId) clearInterval(featuredRotatorIntervalId);
    featuredRotatorIntervalId = setInterval(() => {
        goToFeaturedRotatorPanel(featuredRotatorCurrentIndex + 1);
    }, 8000);
}

function initFeaturedRotator() {
    const rotator = document.getElementById('featuredRotator');
    const track = document.getElementById('featuredRotatorTrack');
    const dotsWrap = document.getElementById('featuredRotatorDots');
    if (!rotator || !track || !dotsWrap) return;

    const panels = Array.from(track.querySelectorAll('.home-rotator-panel'));
    if (!panels.length) return;

    dotsWrap.innerHTML = '';
    panels.forEach((_, index) => {
        const dot = document.createElement('button');
        dot.type = 'button';
        dot.className = 'home-rotator-dot';
        dot.setAttribute('aria-label', `Show featured panel ${index + 1}`);
        dot.addEventListener('click', () => {
            goToFeaturedRotatorPanel(index);
            startFeaturedRotator();
        });
        dotsWrap.appendChild(dot);
    });

    goToFeaturedRotatorPanel(0);
    startFeaturedRotator();

    document.addEventListener('visibilitychange', () => {
        if (document.hidden) {
            if (featuredRotatorIntervalId) clearInterval(featuredRotatorIntervalId);
            return;
        }
        startFeaturedRotator();
    });
}

function formatEventWhen(iso) {
    if (!iso) return '';
    const dt = new Date(iso);
    const now = new Date();
    const isToday = dt.toDateString() === now.toDateString();
    if (isToday) {
        return dt.toLocaleString('en-US', { hour: '2-digit', minute: '2-digit' });
    }
    return dt.toLocaleString('en-US', {
        weekday: 'short',
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit'
    });
}

function renderEventRows(container, events) {
    container.innerHTML = '';
    events.forEach(evt => {
        const row = document.createElement('div');
        row.className = 'featured-event-row';

        const title = document.createElement('div');
        title.className = 'featured-event-title';
        title.textContent = evt.title || '(untitled event)';

        const when = document.createElement('div');
        when.className = 'featured-event-when';
        when.textContent = formatEventWhen(evt.start_time);

        row.appendChild(title);
        row.appendChild(when);
        container.appendChild(row);
    });
}

async function loadFeaturedEvents() {
    const todayContainer = document.getElementById('todayEvents');
    const upcomingContainer = document.getElementById('upcomingEvents');
    if (!todayContainer && !upcomingContainer) return;

    try {
        const res = await fetch('https://api-dashboard-production-fc05.up.railway.app/calendar/upcoming?days=14&limit=80');
        if (!res.ok) throw new Error('Could not load upcoming events');
        const payload = await res.json();
        const events = Array.isArray(payload.events) ? payload.events : [];

        const now = new Date();
        const todayStr = now.toDateString();

        // Today's events: same calendar day, sorted by proximity (already sorted by start_time ASC)
        const todayEvents = events
            .filter(evt => evt.start_time && new Date(evt.start_time).toDateString() === todayStr)
            .slice(0, 4);

        // Upcoming featured: tomorrow onwards, featured === true, limit 4
        const upcomingFeatured = events
            .filter(evt => {
                if (!evt.start_time) return false;
                const evtDate = new Date(evt.start_time);
                return evtDate.toDateString() !== todayStr && evt.featured === true;
            })
            .slice(0, 4);

        // Render today
        if (todayContainer) {
            if (todayEvents.length) {
                todayContainer.innerHTML = '<h4 class="featured-events-header">Today</h4>';
                const list = document.createElement('div');
                list.className = 'featured-events-list';
                todayContainer.appendChild(list);
                renderEventRows(list, todayEvents);
            } else {
                todayContainer.innerHTML = '<h4 class="featured-events-header">Today</h4><p class="featured-events-empty">No events today.</p>';
            }
        }

        // Render upcoming featured
        if (upcomingContainer) {
            if (upcomingFeatured.length) {
                upcomingContainer.innerHTML = '<h4 class="featured-events-header">Upcoming</h4>';
                const list = document.createElement('div');
                list.className = 'featured-events-list';
                upcomingContainer.appendChild(list);
                renderEventRows(list, upcomingFeatured);
            } else {
                upcomingContainer.innerHTML = '<h4 class="featured-events-header">Upcoming</h4><p class="featured-events-empty">No featured upcoming events.</p>';
            }
        }
    } catch (err) {
        if (todayContainer) todayContainer.innerHTML = '<p class="featured-events-empty">Error loading events.</p>';
        if (upcomingContainer) upcomingContainer.innerHTML = '';
    }
}

document.addEventListener('DOMContentLoaded', () => {
    loadFeaturedNews();
    loadFeaturedW();
    initFeaturedRotator();
    loadFeaturedEvents();
});