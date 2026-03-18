const logs_API = 'https://api-dashboard-production-fc05.up.railway.app/logs/'

const HOME_ADVICES = [
  'Prioritize one high-impact task before opening chats.',
  'If context-switching rises, block 25 minutes of single focus.',
  'Review tomorrow\'s top 3 outcomes before ending the day.'
];

const HOME_FOCUS_TIPS = [
  'Use a 50/10 cycle: 50 min focus, 10 min reset.',
  'Turn one vague task into a concrete next action.',
  'Batch small admin tasks into one dedicated slot.'
];

const HOME_SYSTEM_NOTES = [
  'Sync calendar and tasks after planning changes.',
  'Archive finished items weekly to keep lists clean.',
  'Check upcoming 7-day events before committing new work.'
];

let homeRotatorIntervalId = null;
let homeRotatorCurrentIndex = 0;

async function loadLogs() {
  try {
    const res = await fetch(logs_API);
    const logs = await res.json();
    
    console.log('Logs received:', logs); 
    
    const logsList = document.getElementById('logs');
    if (!logsList) {
      console.error('logsList element not found');
      return;
    }
    
    // Limpiar lista
    logsList.innerHTML = '';
    
    // Renderizar cada log
    logs.forEach(log => {
      const li = document.createElement('li');
      li.className = 'log-item';
      li.innerHTML = `
        <span class="log-message">${log.message}</span>
        <span class="log-time">${formatTime(log.timestamp)}</span>
      `;
      logsList.appendChild(li);
    });
    
  } catch (error) {
    console.error('Error:', error);
    const logsList = document.getElementById('logs');
    if (logsList) {
      logsList.innerHTML = '<li class="log-item">Error loading logs</li>';
    }
  }
}

function formatTime(timestamp) {
  const date = new Date(timestamp);
  return date.toLocaleTimeString('es-ES', { 
    hour: '2-digit', 
    minute: '2-digit' 
  });
}

function fillInfoList(containerId, items) {
  const container = document.getElementById(containerId);
  if (!container) return;
  container.innerHTML = '';

  items.forEach((text) => {
    const row = document.createElement('div');
    row.className = 'home-info-item';
    row.textContent = text;
    container.appendChild(row);
  });
}

function goToRotatorPanel(index) {
  const track = document.getElementById('homeRotatorTrack');
  const dotsWrap = document.getElementById('homeRotatorDots');
  const dots = dotsWrap ? Array.from(dotsWrap.querySelectorAll('.home-rotator-dot')) : [];
  if (!track || !dots.length) return;

  const panelCount = dots.length;
  const nextIndex = ((index % panelCount) + panelCount) % panelCount;
  track.style.transform = `translateX(-${nextIndex * 100}%)`;

  dots.forEach((dot, dotIndex) => {
    dot.classList.toggle('is-active', dotIndex === nextIndex);
  });

  track.dataset.activeIndex = String(nextIndex);
  homeRotatorCurrentIndex = nextIndex;
}

function startHomeRotator() {
  const track = document.getElementById('homeRotatorTrack');
  const dotsWrap = document.getElementById('homeRotatorDots');
  const dots = dotsWrap ? dotsWrap.querySelectorAll('.home-rotator-dot') : [];
  if (!track || !dots.length) return;

  if (homeRotatorIntervalId) clearInterval(homeRotatorIntervalId);
  homeRotatorIntervalId = setInterval(() => {
    goToRotatorPanel(homeRotatorCurrentIndex + 1);
  }, 5500);
}

function initHomeRotator() {
  const rotator = document.getElementById('homeRotator');
  const track = document.getElementById('homeRotatorTrack');
  const dotsWrap = document.getElementById('homeRotatorDots');
  if (!rotator || !track || !dotsWrap) return;

  const panels = Array.from(track.querySelectorAll('.home-rotator-panel'));
  if (!panels.length) return;

  dotsWrap.innerHTML = '';
  panels.forEach((_, index) => {
    const dot = document.createElement('button');
    dot.type = 'button';
    dot.className = 'home-rotator-dot';
    dot.setAttribute('aria-label', `Show panel ${index + 1}`);
    dot.addEventListener('click', () => {
      goToRotatorPanel(index);
      startHomeRotator();
    });
    dotsWrap.appendChild(dot);
  });

  goToRotatorPanel(0);
  startHomeRotator();

  document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
      if (homeRotatorIntervalId) clearInterval(homeRotatorIntervalId);
      return;
    }
    startHomeRotator();
  });
}

function initHomePanels() {
  fillInfoList('homeAdvice', HOME_ADVICES);
  fillInfoList('homeFocusTips', HOME_FOCUS_TIPS);
  fillInfoList('homeSystemTips', HOME_SYSTEM_NOTES);
  initHomeRotator();
}

function initHomeLogsAndPanels() {
  loadLogs();
  initHomePanels();
}

// Inicializar
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initHomeLogsAndPanels);
} else {
  initHomeLogsAndPanels();
}

// Actualizar cada 30 segundos
setInterval(loadLogs, 30000);