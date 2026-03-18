// Healthcare Panel Management
(function() {
  'use strict';

  const panels = ['waterPanel', 'weightPanel', 'gymPanel', 'shoppingPanel', 'menuPanel', 'mentalPanel'];

  function openPanel(panelId) {
    const panel = document.getElementById(panelId);
    if (panel) {
      panel.style.display = 'flex';
      document.body.style.overflow = 'hidden';
    }
  }

  function closePanel(panelId) {
    const panel = document.getElementById(panelId);
    if (panel) {
      panel.style.display = 'none';
      document.body.style.overflow = '';
    }
  }

  function closeAllPanels() {
    panels.forEach(id => closePanel(id));
  }

  document.addEventListener('DOMContentLoaded', () => {
    // Button click handlers
    document.querySelectorAll('.healthcare-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        const panelName = btn.dataset.panel;
        const panelId = panelName + 'Panel';
        openPanel(panelId);
      });
    });

    // Close button handlers
    document.querySelectorAll('.rel-close[data-close]').forEach(btn => {
      btn.addEventListener('click', () => {
        closePanel(btn.dataset.close);
      });
    });

    // Click outside to close
    panels.forEach(panelId => {
      const panel = document.getElementById(panelId);
      if (panel) {
        panel.addEventListener('click', e => {
          if (e.target === panel) closePanel(panelId);
        });
      }
    });

    // Escape key to close
    document.addEventListener('keydown', e => {
      if (e.key === 'Escape') {
        closeAllPanels();
      }
    });
  });

  // Expose functions globally for other scripts
  window.healthcarePanels = {
    open: openPanel,
    close: closePanel,
    closeAll: closeAllPanels
  };
})();
