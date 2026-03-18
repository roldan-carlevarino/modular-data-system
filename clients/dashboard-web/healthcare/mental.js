// Mental Health Tracking
(function() {
  'use strict';

  const MENTAL_API_BASE = "https://api-dashboard-production-fc05.up.railway.app";
  const MENTAL_TODAY_API = `${MENTAL_API_BASE}/welfare/mental/today`;
  const MENTAL_LOG_API = `${MENTAL_API_BASE}/welfare/mental/log`;
  const MENTAL_HISTORY_API = `${MENTAL_API_BASE}/welfare/mental/history`;

  let mentalIsUpdating = false;
  let currentEntry = {
    sleep_hours: null,
    stress: null,
    journal_note: ''
  };

  function setMentalStatus(message, isError = false) {
    const statusEl = document.getElementById("mentalStatus");
    if (!statusEl) return;
    statusEl.textContent = message || "";
    statusEl.style.color = isError ? "#ff8a8a" : "rgba(255, 255, 255, 0.72)";
  }

  function updateBtnValue() {
    const btnEl = document.getElementById("mentalBtnValue");
    if (!btnEl) return;

    if (currentEntry.stress) {
      btnEl.textContent = `😰${currentEntry.stress}`;
    } else if (currentEntry.sleep_hours) {
      btnEl.textContent = `${currentEntry.sleep_hours}h`;
    } else {
      btnEl.textContent = '--';
    }
  }

  function setActiveButton(containerSelector, value, dataAttr) {
    const container = document.querySelector(containerSelector);
    if (!container) return;

    container.querySelectorAll('button').forEach(btn => {
      btn.classList.remove('active');
      if (btn.dataset[dataAttr] == value) {
        btn.classList.add('active');
      }
    });
  }

  function setupStressSelector() {
    const container = document.getElementById('stressSelector');
    if (!container) return;

    container.querySelectorAll('.stress-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        currentEntry.stress = parseInt(btn.dataset.stress);
        setActiveButton('#stressSelector', currentEntry.stress, 'stress');
      });
    });
  }

  function setupTabSwitching() {
    document.querySelectorAll('.mental-tab').forEach(tab => {
      tab.addEventListener('click', () => {
        const view = tab.dataset.view;
        
        document.querySelectorAll('.mental-tab').forEach(t => t.classList.remove('active'));
        tab.classList.add('active');

        document.getElementById('mentalLogView').style.display = view === 'log' ? 'block' : 'none';
        document.getElementById('mentalHistoryView').style.display = view === 'history' ? 'block' : 'none';

        if (view === 'history') {
          loadHistory();
        }
      });
    });
  }

  async function loadTodayEntry() {
    try {
      const response = await fetch(MENTAL_TODAY_API);
      if (!response.ok) {
        if (response.status === 404) {
          // No entry today, that's fine
          return;
        }
        throw new Error(`Failed to load mental entry (${response.status})`);
      }

      const data = await response.json();
      if (data) {
        currentEntry = {
          sleep_hours: data.sleep_hours || null,
          stress: data.stress || null,
          journal_note: data.journal_note || ''
        };
        populateForm();
        updateBtnValue();
      }
    } catch (error) {
      console.error("Error loading mental entry:", error);
    }
  }

  function populateForm() {
    if (currentEntry.sleep_hours) {
      const sleepInput = document.getElementById('sleepHours');
      if (sleepInput) sleepInput.value = currentEntry.sleep_hours;
    }
    if (currentEntry.stress) {
      setActiveButton('#stressSelector', currentEntry.stress, 'stress');
    }
    
    const journalInput = document.getElementById('mentalJournalNote');
    if (journalInput) journalInput.value = currentEntry.journal_note || '';
  }

  async function saveEntry() {
    if (mentalIsUpdating) return;

    // Gather form data
    const sleepHoursInput = document.getElementById('sleepHours');
    const journalInput = document.getElementById('mentalJournalNote');

    currentEntry.sleep_hours = sleepHoursInput?.value ? parseFloat(sleepHoursInput.value) : null;
    currentEntry.journal_note = journalInput?.value || '';

    // Validate - need at least sleep or stress
    if (!currentEntry.sleep_hours && !currentEntry.stress) {
      setMentalStatus("Please enter sleep hours or stress level", true);
      return;
    }

    mentalIsUpdating = true;
    setMentalStatus("Saving...");

    try {
      const response = await fetch(MENTAL_LOG_API, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(currentEntry)
      });

      if (!response.ok) {
        const errorText = await response.text();
        throw new Error(errorText || `Failed to save entry (${response.status})`);
      }

      setMentalStatus("Entry saved ✓");
      updateBtnValue();
    } catch (error) {
      console.error("Error saving mental entry:", error);
      setMentalStatus("Could not save entry (API not implemented yet)", true);
    } finally {
      mentalIsUpdating = false;
    }
  }

  async function loadHistory() {
    const container = document.getElementById('mentalHistoryList');
    if (!container) return;

    container.innerHTML = '<p class="mental-loading">Loading history...</p>';

    try {
      const response = await fetch(`${MENTAL_HISTORY_API}?days=30`);
      if (!response.ok) {
        throw new Error(`Failed to load history (${response.status})`);
      }

      const data = await response.json();
      renderHistory(data);
    } catch (error) {
      console.error("Error loading mental history:", error);
      container.innerHTML = '<p class="mental-empty">History not available (API not implemented yet)</p>';
    }
  }

  function renderHistory(entries) {
    const container = document.getElementById('mentalHistoryList');
    if (!container) return;

    if (!entries || entries.length === 0) {
      container.innerHTML = '<p class="mental-empty">No entries yet</p>';
      return;
    }

    container.innerHTML = entries.map(entry => `
      <div class="mental-history-entry">
        <div class="mental-history-date">${formatDate(entry.date)}</div>
        <div class="mental-history-metrics">
          <span class="mental-history-metric" title="Sleep">😴 ${entry.sleep_hours || '--'}h</span>
          <span class="mental-history-metric" title="Stress">😰 ${entry.stress || '--'}/5</span>
        </div>
        ${entry.journal_note ? `<div class="mental-history-note">${entry.journal_note}</div>` : ''}
      </div>
    `).join('');
  }

  function formatDate(dateStr) {
    const date = new Date(dateStr);
    return date.toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short' });
  }

  document.addEventListener('DOMContentLoaded', () => {
    setupStressSelector();
    setupTabSwitching();

    const saveBtn = document.getElementById('saveMentalBtn');
    if (saveBtn) {
      saveBtn.addEventListener('click', saveEntry);
    }

    // Load today's entry when panel opens
    const mentalPanel = document.getElementById('mentalPanel');
    if (mentalPanel) {
      const observer = new MutationObserver((mutations) => {
        mutations.forEach((mutation) => {
          if (mutation.attributeName === 'style' && mentalPanel.style.display === 'flex') {
            loadTodayEntry();
          }
        });
      });
      observer.observe(mentalPanel, { attributes: true });
    }
  });

  // Expose API globally
  window.mentalHealth = {
    loadToday: loadTodayEntry,
    save: saveEntry,
    loadHistory: loadHistory
  };
})();
