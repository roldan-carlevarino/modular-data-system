/* =====================================================================
   intel-resizer.js — Drag-to-resize divider for the Intel sidebar.

   Injects a thin vertical handle between the sidebar and the content,
   and lets the user drag it to set the sidebar width. The width is
   persisted in localStorage. Double-click resets to the default.
   ===================================================================== */
(function () {
  'use strict';

  const STORAGE_KEY = 'intel.sidebarWidth';
  const MIN = 180;
  const MAX = 560;
  const DEFAULT = 260;

  function applyWidth(layout, w) {
    const clamped = Math.max(MIN, Math.min(MAX, w));
    layout.style.setProperty('--knowledge-sidebar-w', clamped + 'px');
    return clamped;
  }

  function init() {
    const layout = document.querySelector('.knowledge-layout');
    if (!layout) return;
    if (layout.querySelector(':scope > .knowledge-resizer')) return;

    // Restore saved width
    const saved = parseInt(localStorage.getItem(STORAGE_KEY), 10);
    applyWidth(layout, Number.isFinite(saved) ? saved : DEFAULT);

    // Insert resizer between sidebar and content
    const sidebar = layout.querySelector(':scope > .knowledge-sidebar');
    const content = layout.querySelector(':scope > .knowledge-content');
    if (!sidebar || !content) return;

    const handle = document.createElement('div');
    handle.className = 'knowledge-resizer';
    handle.setAttribute('role', 'separator');
    handle.setAttribute('aria-orientation', 'vertical');
    handle.title = 'Drag to resize · Double-click to reset';
    handle.innerHTML = '<span class="knowledge-resizer-grip"></span>';
    layout.insertBefore(handle, content);

    let dragging = false;

    function onMove(e) {
      if (!dragging) return;
      const rect = layout.getBoundingClientRect();
      const x = (e.touches ? e.touches[0].clientX : e.clientX) - rect.left;
      const w = applyWidth(layout, x);
      // While dragging we throttle the persistence
      handle._pendingW = w;
    }

    function onUp() {
      if (!dragging) return;
      dragging = false;
      document.body.classList.remove('knowledge-resizing');
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup',   onUp);
      window.removeEventListener('touchmove', onMove);
      window.removeEventListener('touchend',  onUp);
      if (Number.isFinite(handle._pendingW)) {
        try { localStorage.setItem(STORAGE_KEY, String(handle._pendingW)); } catch (_) {}
      }
    }

    function onDown(e) {
      // Ignore if sidebar is collapsed
      if (sidebar.classList.contains('collapsed')) return;
      dragging = true;
      document.body.classList.add('knowledge-resizing');
      e.preventDefault();
      window.addEventListener('mousemove', onMove);
      window.addEventListener('mouseup',   onUp);
      window.addEventListener('touchmove', onMove, { passive: false });
      window.addEventListener('touchend',  onUp);
    }

    handle.addEventListener('mousedown',  onDown);
    handle.addEventListener('touchstart', onDown, { passive: false });

    handle.addEventListener('dblclick', () => {
      applyWidth(layout, DEFAULT);
      try { localStorage.setItem(STORAGE_KEY, String(DEFAULT)); } catch (_) {}
    });

    console.info('[intel-resizer] ready — drag the divider to resize the sidebar');
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
