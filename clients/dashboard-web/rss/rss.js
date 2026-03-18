
// RSS Dashboard
const RSS_API_BASE = "https://api-dashboard-production-fc05.up.railway.app/rss";

const RSS_CATEGORIES = [
  { key: "global",   label: "🌐 Global",   endpoint: "/top-global" },
  { key: "ml",       label: "🤖 ML",        endpoint: "/top/ml" },
  { key: "markets",  label: "📈 Markets",  endpoint: "/top/markets" },
  { key: "quant",    label: "📐 Quant",    endpoint: "/top/quant" },
  { key: "politics", label: "🏛 Politics", endpoint: "/top/politics" }
];

const SCORE_COLORS = {
  ml:       { bg: "rgba(159,122,234,0.18)", color: "#b794f4", border: "rgba(159,122,234,0.35)" },
  markets:  { bg: "rgba(72,187,120,0.15)",  color: "#68d391", border: "rgba(72,187,120,0.32)" },
  quant:    { bg: "rgba(45,212,191,0.15)",  color: "#4fd1c5", border: "rgba(45,212,191,0.32)" },
  politics: { bg: "rgba(246,173,85,0.15)",  color: "#f6ad55", border: "rgba(246,173,85,0.32)" },
};

let rssActiveCategory = "global";
let rssCache = {};
let rssLimit = 20;

document.addEventListener("DOMContentLoaded", () => {
  const tabContent = document.querySelector('.content.c5');
  if (!tabContent) return;

  tabContent.querySelectorAll('.rss-tab').forEach(btn => {
    btn.addEventListener('click', () => {
      tabContent.querySelectorAll('.rss-tab').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      rssActiveCategory = btn.dataset.cat;
      loadRssCategory(rssActiveCategory);
    });
  });

  document.getElementById('rssLimitSelect').addEventListener('change', (e) => {
    rssLimit = Number(e.target.value);
    rssCache = {};
    loadRssCategory(rssActiveCategory);
  });

  document.getElementById('rssRefreshBtn').addEventListener('click', () => {
    rssCache = {};
    loadRssCategory(rssActiveCategory);
  });

  loadRssCategory(rssActiveCategory);
});

async function loadRssCategory(catKey) {
  const feed = document.getElementById('rssFeed');
  if (!feed) return;

  const cacheKey = catKey + '_' + rssLimit;
  if (rssCache[cacheKey]) {
    renderRssFeed(rssCache[cacheKey]);
    return;
  }

  feed.innerHTML = '<div class="rss-loading"><span class="rss-spinner"></span> Loading...</div>';

  const cat = RSS_CATEGORIES.find(c => c.key === catKey);
  try {
    const res = await fetch(`${RSS_API_BASE}${cat.endpoint}?limit=${rssLimit}`);
    const data = await res.json();
    const articles = data.articles || [];
    rssCache[cacheKey] = articles;
    renderRssFeed(articles);
  } catch (e) {
    feed.innerHTML = '<div class="rss-error">Failed to load articles.</div>';
  }
}

function renderRssFeed(articles) {
  const feed = document.getElementById('rssFeed');
  if (!feed) return;

  if (articles.length === 0) {
    feed.innerHTML = '<div class="rss-empty">No articles available.</div>';
    return;
  }

  feed.innerHTML = '';
  articles.forEach((article, idx) => {
    const rank = article.global_rank ?? article.category_rank ?? (idx + 1);
    const date = article.created_at ? formatRssDate(article.created_at) : '';
    const topCat = article.top_category;

    const card = document.createElement('div');
    card.className = 'rss-card';
    if (topCat) card.classList.add(`rss-card--${topCat}`);
    card.innerHTML = `
      <div class="rss-card-rank">#${rank}</div>
      <div class="rss-card-body">
        <a href="${article.link}" target="_blank" rel="noopener" class="rss-card-title">${article.title}</a>
        <div class="rss-card-meta">
          ${article.source_id ? `<span class="rss-source">${article.source_id}</span>` : ''}
          ${date ? `<span class="rss-date">${date}</span>` : ''}
          ${topCat ? `<span class="rss-top-cat rss-top-cat--${topCat}">${topCat}</span>` : ''}
        </div>
        <div class="rss-card-scores">
          ${renderScoreBadge('ml', article.scores?.ml)}
          ${renderScoreBadge('markets', article.scores?.markets)}
          ${renderScoreBadge('quant', article.scores?.quant)}
          ${renderScoreBadge('politics', article.scores?.politics)}
        </div>
      </div>
    `;
    feed.appendChild(card);
  });
}

function renderScoreBadge(key, value) {
  if (value == null) return '';
  const v = parseFloat(value);
  if (v <= 0) return '';
  const c = SCORE_COLORS[key];
  const pct = Math.min(100, Math.round(v * 100));
  return `<span class="rss-score-badge" style="background:${c.bg};color:${c.color};border-color:${c.border}">${key} <strong>${pct}%</strong></span>`;
}

function formatRssDate(iso) {
  const d = new Date(iso);
  const now = new Date();
  const diff = Math.floor((now - d) / 1000);
  if (diff < 3600)  return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short' });
}

