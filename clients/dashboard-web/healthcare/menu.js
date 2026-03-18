// Menu Module - Healthcare Dashboard
const MENU_API_BASE = "https://api-dashboard-production-fc05.up.railway.app/menu";

const menuState = {
  todayMenu: null,
  weekMenu: null,
  currentView: 'today',
  tracking: null
};

const OCCURRENCE_ORDER = ['morning', 'afternoon', 'evening'];

const OCCURRENCE_LABELS = {
  'morning': '🌅 Morning',
  'afternoon': '☀️ Afternoon',
  'evening': '🌙 Evening'
};

function getOccurrenceLabel(occurrence) {
  return OCCURRENCE_LABELS[occurrence?.toLowerCase()] || occurrence;
}

// Load today's menu with tracking
async function loadTodayMenu() {
  try {
    const res = await fetch(`${MENU_API_BASE}/tracking/today`);
    if (!res.ok) throw new Error("Failed to load today's menu");
    menuState.tracking = await res.json();
    renderTodayMenu();
    updateMenuButton();
  } catch (err) {
    console.error("Error loading today's menu:", err);
    document.getElementById("menuTodayList").innerHTML = 
      '<p class="menu-error">Could not load menu</p>';
  }
}

// Toggle meal completion
async function toggleMeal(occurrence) {
  try {
    const res = await fetch(`${MENU_API_BASE}/tracking/toggle?occurrence=${occurrence}`, {
      method: 'POST'
    });
    if (!res.ok) throw new Error("Failed to toggle meal");
    
    // Reload to get updated state
    await loadTodayMenu();
  } catch (err) {
    console.error("Error toggling meal:", err);
  }
}

// Load full week menu
async function loadWeekMenu() {
  try {
    const res = await fetch(`${MENU_API_BASE}/all`);
    if (!res.ok) throw new Error("Failed to load week menu");
    const data = await res.json();
    menuState.weekMenu = data.menu;
    renderWeekMenu();
  } catch (err) {
    console.error("Error loading week menu:", err);
    document.getElementById("menuWeekList").innerHTML = 
      '<p class="menu-error">Could not load menu</p>';
  }
}

// Render today's menu with tracking
function renderTodayMenu() {
  const container = document.getElementById("menuTodayList");
  const titleEl = document.getElementById("menuTodayTitle");
  const data = menuState.tracking;

  if (!data || !data.meals) {
    container.innerHTML = '<p class="menu-empty">No meals scheduled for today</p>';
    return;
  }

  titleEl.textContent = `Today - ${data.completed}/${data.total} completed`;

  if (data.meals.length === 0) {
    container.innerHTML = '<p class="menu-empty">No meals scheduled for today</p>';
    return;
  }

  container.innerHTML = data.meals.map(meal => `
    <div class="menu-meal ${meal.completed ? 'menu-meal--completed' : ''}" data-occurrence="${meal.occurrence}">
      <button class="menu-meal-toggle" onclick="toggleMeal('${meal.occurrence}')">
        ${meal.completed ? '✓' : '○'}
      </button>
      <span class="menu-meal-time">${getOccurrenceLabel(meal.occurrence)}</span>
      <span class="menu-meal-name">${meal.name}</span>
    </div>
  `).join('');
}

// Render week menu
function renderWeekMenu() {
  const container = document.getElementById("menuWeekList");
  const data = menuState.weekMenu;

  if (!data || data.length === 0) {
    container.innerHTML = '<p class="menu-empty">No menu data available</p>';
    return;
  }

  container.innerHTML = data.map(day => `
    <div class="menu-day">
      <h4 class="menu-day-title">${day.weekday_name}</h4>
      <div class="menu-day-meals">
        ${day.meals.length > 0 ? day.meals.map(meal => `
          <div class="menu-meal menu-meal--compact">
            <span class="menu-meal-time">${getOccurrenceLabel(meal.occurrence)}</span>
            <span class="menu-meal-name">${meal.name}</span>
          </div>
        `).join('') : '<p class="menu-empty-day">No meals</p>'}
      </div>
    </div>
  `).join('');
}

// Update button value
function updateMenuButton() {
  const btnEl = document.getElementById("menuBtnValue");
  if (!btnEl) return;

  const data = menuState.tracking;
  if (data) {
    btnEl.textContent = `${data.completed}/${data.total}`;
  } else {
    btnEl.textContent = "--";
  }
}

// Switch between today/week views
function switchMenuView(view) {
  menuState.currentView = view;
  
  const todayView = document.getElementById("menuTodayView");
  const weekView = document.getElementById("menuWeekView");
  
  document.querySelectorAll(".menu-tab").forEach(tab => {
    tab.classList.toggle("active", tab.dataset.view === view);
  });

  if (view === 'today') {
    todayView.style.display = 'block';
    weekView.style.display = 'none';
  } else {
    todayView.style.display = 'none';
    weekView.style.display = 'block';
    // Load week menu on first switch
    if (!menuState.weekMenu) {
      loadWeekMenu();
    }
  }
}

// Initialize
document.addEventListener("DOMContentLoaded", () => {
  // Load today's menu on page load
  loadTodayMenu();

  // Tab switching
  document.querySelectorAll(".menu-tab").forEach(tab => {
    tab.addEventListener("click", () => {
      switchMenuView(tab.dataset.view);
    });
  });
});
