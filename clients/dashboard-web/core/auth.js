// ============================================================
// AUTH MODULE — login, token management, protected fetch
// ============================================================

const AUTH_API = "https://api-dashboard-production-fc05.up.railway.app/auth";
const AUTH_TOKEN_KEY = "dashboard.authToken";

// ---- Token helpers ----
function getAuthToken() {
  return localStorage.getItem(AUTH_TOKEN_KEY);
}

function setAuthToken(token) {
  localStorage.setItem(AUTH_TOKEN_KEY, token);
}

function clearAuthToken() {
  localStorage.removeItem(AUTH_TOKEN_KEY);
}

function isTokenExpired(token) {
  if (!token) return true;
  try {
    const payload = JSON.parse(atob(token.split('.')[1]));
    return payload.exp * 1000 < Date.now();
  } catch {
    return true;
  }
}

function isAuthenticated() {
  const token = getAuthToken();
  return token && !isTokenExpired(token);
}

function getCurrentUsername() {
  const token = getAuthToken();
  if (!token) return null;
  try {
    const payload = JSON.parse(atob(token.split('.')[1]));
    return payload.sub || null;
  } catch { return null; }
}

function isDemoUser() {
  return getCurrentUsername() === 'demo';
}

// ---- Demo toast ----
function showDemoToast(msg) {
  let toast = document.getElementById('demoToast');
  if (!toast) {
    toast = document.createElement('div');
    toast.id = 'demoToast';
    toast.style.cssText =
      'position:fixed;bottom:24px;left:50%;transform:translateX(-50%);' +
      'background:#e65100;color:#fff;padding:10px 24px;border-radius:8px;' +
      'font-size:14px;z-index:99999;opacity:0;transition:opacity .3s;pointer-events:none;';
    document.body.appendChild(toast);
  }
  toast.textContent = msg || 'Demo account is read-only';
  toast.style.opacity = '1';
  clearTimeout(toast._tid);
  toast._tid = setTimeout(() => { toast.style.opacity = '0'; }, 2500);
}

// ---- Login ----
async function doLogin(username, password) {
  const params = { username, password: password || '' };
  const res = await fetch(`${AUTH_API}/login`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams(params)
  });

  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || "Login failed");
  }

  const data = await res.json();
  setAuthToken(data.access_token);
  return data;
}

async function doDemoLogin() {
  try {
    await doLogin('demo', 'demo');
    window.location.reload();
  } catch (err) {
    const errorEl = document.getElementById('loginError');
    if (errorEl) errorEl.textContent = err.message || 'Demo login failed';
  }
}

function doLogout() {
  clearAuthToken();
  showLoginScreen();
}

// ---- Protected fetch wrapper ----
const _originalFetch = window.fetch;

window.fetch = function(url, options = {}) {
  const token = getAuthToken();

  // Don't add auth header to login requests or external URLs
  const isAuthEndpoint = typeof url === 'string' && url.includes('/auth/login');
  const isApiCall = typeof url === 'string' && url.includes('api-dashboard-production');

  if (token && isApiCall && !isAuthEndpoint) {
    options.headers = options.headers || {};
    if (options.headers instanceof Headers) {
      if (!options.headers.has('Authorization')) {
        options.headers.set('Authorization', `Bearer ${token}`);
      }
    } else {
      if (!options.headers['Authorization']) {
        options.headers['Authorization'] = `Bearer ${token}`;
      }
    }
  }

  return _originalFetch.call(window, url, options).then(response => {
    // If we get a 401 on an API call, session expired — show login
    if (response.status === 401 && isApiCall && !isAuthEndpoint) {
      clearAuthToken();
      showLoginScreen();
    }
    // If we get a 403 and it's the demo user, show a friendly toast
    if (response.status === 403 && isApiCall && isDemoUser()) {
      showDemoToast('Demo account is read-only');
    }
    return response;
  });
};

// Manage session expiration //

// ---- Login UI ----
function showLoginScreen() {
  document.getElementById('loginOverlay').style.display = 'flex';
  document.getElementById('dashboardMain').style.display = 'none';
}

function showDashboard() {
  document.getElementById('loginOverlay').style.display = 'none';
  document.getElementById('dashboardMain').style.display = '';
}

function setupLoginForm() {
  const form = document.getElementById('loginForm');
  const errorEl = document.getElementById('loginError');

  if (!form) return;

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    errorEl.textContent = '';

    const username = document.getElementById('loginUsername').value.trim();
    const password = document.getElementById('loginPassword').value;

    if (!password) {
      errorEl.textContent = 'Enter password';
      return;
    }

    const submitBtn = form.querySelector('button[type="submit"]');
    submitBtn.disabled = true;
    submitBtn.textContent = 'Logging in...';

    try {
      await doLogin(username, password);
      window.location.reload();
    } catch (err) {
      errorEl.textContent = err.message || 'Login failed';
    } finally {
      submitBtn.disabled = false;
      submitBtn.textContent = 'Login';
    }
  });
}

// ---- Showcase slideshow ----
function initShowcaseSlideshow() {
  const slides = document.querySelectorAll('.showcase-slide');
  if (slides.length < 2) return;
  let current = 0;
  setInterval(() => {
    slides[current].classList.remove('active');
    current = (current + 1) % slides.length;
    slides[current].classList.add('active');
  }, 3500);
}

// ---- Auth guard on page load ----
document.addEventListener('DOMContentLoaded', () => {
  setupLoginForm();
  initShowcaseSlideshow();

  // Demo button
  const demoBtn = document.getElementById('demoLoginBtn');
  if (demoBtn) demoBtn.addEventListener('click', doDemoLogin);

  // Logout button
  const logoutBtn = document.getElementById('logoutBtn');
  if (logoutBtn) {
    logoutBtn.addEventListener('click', doLogout);
  }

  if (isAuthenticated()) {
    showDashboard();
    // Show demo badge if demo user
    if (isDemoUser()) {
      const badge = document.createElement('div');
      badge.id = 'demoBadge';
      badge.textContent = 'DEMO';
      badge.style.cssText =
        'position:fixed;top:8px;right:12px;background:#e65100;color:#fff;' +
        'padding:3px 10px;border-radius:6px;font-size:11px;font-weight:700;' +
        'letter-spacing:1px;z-index:99999;opacity:.85;';
      document.body.appendChild(badge);
    }
  } else {
    showLoginScreen();
  }
});
