// ---------------- DAILY SCHEDULE ----------------

const scheduleDate = document.getElementById('scheduleDate');
const scheduleHours = document.getElementById('scheduleHours');
const scheduleCreateEventBtn = document.getElementById('scheduleCreateEventBtn');
const monthCalendarTitle = document.getElementById('monthCalendarTitle');
const monthCalendarGrid = document.getElementById('monthCalendarGrid');
const monthCalendarPrev = document.getElementById('monthCalendarPrev');
const monthCalendarNext = document.getElementById('monthCalendarNext');
const CALENDAR_API_BASE = "https://api-dashboard-production-fc05.up.railway.app";

let draggedHour = null;
let currentSlotsByHour = {};
let selectedDate = new Date();
let monthCursor = new Date(selectedDate.getFullYear(), selectedDate.getMonth(), 1);
let monthActivityCountByIso = {};
let monthFeaturedCountByIso = {};

async function loadMonthSummary() {
  const year = monthCursor.getFullYear();
  const month = monthCursor.getMonth() + 1;
  const res = await fetch(`${CALENDAR_API_BASE}/calendar/month-summary?year=${year}&month=${month}`);
  if (!res.ok) {
    throw new Error(await res.text());
  }
  const payload = await res.json();
  const next = {};
  const nextFeatured = {};
  (payload.days || []).forEach(d => {
    next[d.day] = Number(d.items_count) || 0;
    nextFeatured[d.day] = Number(d.featured_count) || 0;
  });
  monthActivityCountByIso = next;
  monthFeaturedCountByIso = nextFeatured;
}

async function refreshMonthCalendar() {
  try {
    await loadMonthSummary();
  } catch (err) {
    console.error('Error loading month summary:', err);
    monthActivityCountByIso = {};
    monthFeaturedCountByIso = {};
  }
  renderMonthCalendar();
}

function renderMonthCalendar() {
  if (!monthCalendarTitle || !monthCalendarGrid) return;

  const year = monthCursor.getFullYear();
  const month = monthCursor.getMonth();
  const today = new Date();
  const todayIso = toIsoDate(today);
  const selectedIso = toIsoDate(selectedDate);

  monthCalendarTitle.textContent = monthCursor.toLocaleDateString('en-US', {
    month: 'long',
    year: 'numeric'
  });

  monthCalendarGrid.innerHTML = '';

  const firstDay = new Date(year, month, 1);
  const firstDayMondayIndex = (firstDay.getDay() + 6) % 7;
  const daysInMonth = new Date(year, month + 1, 0).getDate();
  const prevMonthDays = new Date(year, month, 0).getDate();

  for (let i = 0; i < firstDayMondayIndex; i++) {
    const dayNumber = prevMonthDays - firstDayMondayIndex + i + 1;
    const cellDate = new Date(year, month - 1, dayNumber);
    const cellIso = toIsoDate(cellDate);

    const cell = document.createElement('div');
    cell.className = 'month-day other-month';
    cell.textContent = String(dayNumber);

    if ((monthFeaturedCountByIso[cellIso] || 0) > 0) {
      cell.classList.add('has-featured');
      cell.title = `${monthFeaturedCountByIso[cellIso]} featured`;
    } else if ((monthActivityCountByIso[cellIso] || 0) > 0) {
      cell.classList.add('has-activity');
      cell.title = `${monthActivityCountByIso[cellIso]} item(s)`;
    }

    cell.addEventListener('click', () => {
      selectedDate = cellDate;
      monthCursor = new Date(cellDate.getFullYear(), cellDate.getMonth(), 1);
      refreshMonthCalendar().then(() => renderSchedule());
    });

    monthCalendarGrid.appendChild(cell);
  }

  for (let day = 1; day <= daysInMonth; day++) {
    const cell = document.createElement('div');
    cell.className = 'month-day';
    cell.textContent = String(day);
    const cellDate = new Date(year, month, day);
    const cellIso = toIsoDate(cellDate);

    if (cellIso === todayIso) {
      cell.classList.add('today');
    }
    if (cellIso === selectedIso) {
      cell.classList.add('selected');
    }
    if ((monthFeaturedCountByIso[cellIso] || 0) > 0) {
      cell.classList.add('has-featured');
      cell.title = `${monthFeaturedCountByIso[cellIso]} featured`;
    } else if ((monthActivityCountByIso[cellIso] || 0) > 0) {
      cell.classList.add('has-activity');
      cell.title = `${monthActivityCountByIso[cellIso]} item(s)`;
    }

    cell.addEventListener('click', () => {
      selectedDate = cellDate;
      renderMonthCalendar();
      renderSchedule();
    });

    monthCalendarGrid.appendChild(cell);
  }

  const totalCells = monthCalendarGrid.children.length;
  const trailing = (7 - (totalCells % 7)) % 7;
  for (let i = 1; i <= trailing; i++) {
    const cellDate = new Date(year, month + 1, i);
    const cellIso = toIsoDate(cellDate);

    const cell = document.createElement('div');
    cell.className = 'month-day other-month';
    cell.textContent = String(i);

    if ((monthFeaturedCountByIso[cellIso] || 0) > 0) {
      cell.classList.add('has-featured');
      cell.title = `${monthFeaturedCountByIso[cellIso]} featured`;
    } else if ((monthActivityCountByIso[cellIso] || 0) > 0) {
      cell.classList.add('has-activity');
      cell.title = `${monthActivityCountByIso[cellIso]} item(s)`;
    }

    cell.addEventListener('click', () => {
      selectedDate = cellDate;
      monthCursor = new Date(cellDate.getFullYear(), cellDate.getMonth(), 1);
      refreshMonthCalendar().then(() => renderSchedule());
    });

    monthCalendarGrid.appendChild(cell);
  }
}

function toIsoDate(dateObj) {
  const yyyy = dateObj.getFullYear();
  const mm = String(dateObj.getMonth() + 1).padStart(2, '0');
  const dd = String(dateObj.getDate()).padStart(2, '0');
  return `${yyyy}-${mm}-${dd}`;
}

function toLocalIsoDateTime(dateObj) {
  const yyyy = dateObj.getFullYear();
  const mm = String(dateObj.getMonth() + 1).padStart(2, '0');
  const dd = String(dateObj.getDate()).padStart(2, '0');
  const hh = String(dateObj.getHours()).padStart(2, '0');
  const mi = String(dateObj.getMinutes()).padStart(2, '0');
  const ss = String(dateObj.getSeconds()).padStart(2, '0');
  return `${yyyy}-${mm}-${dd}T${hh}:${mi}:${ss}`;
}

function parseHourMinute(raw) {
  const m = String(raw || '').trim().match(/^(\d{1,2}):(\d{2})$/);
  if (!m) return null;
  const hour = Number(m[1]);
  const minute = Number(m[2]);
  if (hour < 0 || hour > 23 || minute < 0 || minute > 59) return null;
  return { hour, minute };
}

function getSelectedDayIso() {
  return toIsoDate(selectedDate);
}

async function loadActivities() {
  const day = getSelectedDayIso();
  const res = await fetch(`${CALENDAR_API_BASE}/calendar/day?day=${encodeURIComponent(day)}`);
  if (!res.ok) {
    throw new Error(await res.text());
  }

  const payload = await res.json();
  currentSlotsByHour = {};
  (payload.slots || []).forEach(slot => {
    const items = Array.isArray(slot.items)
      ? slot.items.slice().sort((a, b) => (a.start_minute ?? 0) - (b.start_minute ?? 0))
      : (slot.item ? [slot.item] : []);

    currentSlotsByHour[slot.hour] = slot;
    currentSlotsByHour[slot.hour].items = items;
  });
}

async function saveActivity(hour, data) {
  const slot = currentSlotsByHour[hour];
  if (!slot?.slot_id) return;

  const res = await fetch(`${CALENDAR_API_BASE}/calendar/slot/${slot.slot_id}/item`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      item_id: data.item_id ?? null,
      title: data.title,
      item_kind: 'note',
      start_minute: data.start_minute,
      duration_minutes: data.duration_minutes
    })
  });

  if (!res.ok) {
    throw new Error(await res.text());
  }
}

async function swapActivities(fromHour, toHour) {
  const fromSlot = currentSlotsByHour[fromHour];
  const toSlot = currentSlotsByHour[toHour];
  if (!fromSlot?.slot_id || !toSlot?.slot_id) return;

  const res = await fetch(`${CALENDAR_API_BASE}/calendar/swap`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      from_slot_id: fromSlot.slot_id,
      to_slot_id: toSlot.slot_id
    })
  });

  if (!res.ok) {
    throw new Error(await res.text());
  }
}

async function deleteActivity(hour, itemId) {
  const slot = currentSlotsByHour[hour];
  if (!slot?.slot_id) return;

  const res = await fetch(`${CALENDAR_API_BASE}/calendar/slot/${slot.slot_id}/item`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ item_id: itemId, title: '' })
  });

  if (!res.ok) {
    throw new Error(await res.text());
  }
}

async function addTaskToSlot(hour) {
  const slot = currentSlotsByHour[hour];
  if (!slot) return;

  const items = (slot.items || []).slice().sort((a, b) => (a.start_minute ?? 0) - (b.start_minute ?? 0));
  if (items.length >= 2) {
    alert('Por ahora solo se permiten 2 tareas por slot');
    return;
  }

  const title = (prompt('Nueva tarea para este slot') || '').trim();
  if (!title) return;

  try {
    if (items.length === 0) {
      await saveActivity(hour, {
        item_id: null,
        title,
        start_minute: 0,
        duration_minutes: 60
      });
    } else {
      const first = items[0];
      await saveActivity(hour, {
        item_id: first.id,
        title: first.title,
        start_minute: 0,
        duration_minutes: 30
      });
      await saveActivity(hour, {
        item_id: null,
        title,
        start_minute: 30,
        duration_minutes: 30
      });
    }
    await renderSchedule();
  } catch (err) {
    console.error('Error adding task to slot:', err);
  }
}

async function adjustTaskDuration(hour, targetItemId) {
  const slot = currentSlotsByHour[hour];
  if (!slot) return;

  const items = (slot.items || []).slice().sort((a, b) => (a.start_minute ?? 0) - (b.start_minute ?? 0));
  if (items.length !== 2) {
    alert('El ajuste rápido de minutos funciona cuando hay 2 tareas en el slot');
    return;
  }

  const first = items[0];
  const second = items[1];
  const targetIsFirst = first.id === targetItemId;
  const defaultMinutes = targetIsFirst ? first.duration_minutes : second.duration_minutes;

  const input = prompt('Minutos para esta tarea (1-59)', String(defaultMinutes || 30));
  if (input === null) return;

  const targetMinutes = Math.max(1, Math.min(59, Number(input) || 0));
  if (!targetMinutes) return;

  const newFirstDuration = targetIsFirst ? targetMinutes : (60 - targetMinutes);
  const newSecondDuration = 60 - newFirstDuration;

  try {
    const oldBoundary = Number(first.duration_minutes || 30);
    const newBoundary = newFirstDuration;

    if (newBoundary >= oldBoundary) {
      await saveActivity(hour, {
        item_id: second.id,
        title: second.title,
        start_minute: newBoundary,
        duration_minutes: newSecondDuration
      });
      await saveActivity(hour, {
        item_id: first.id,
        title: first.title,
        start_minute: 0,
        duration_minutes: newFirstDuration
      });
    } else {
      await saveActivity(hour, {
        item_id: first.id,
        title: first.title,
        start_minute: 0,
        duration_minutes: newFirstDuration
      });
      await saveActivity(hour, {
        item_id: second.id,
        title: second.title,
        start_minute: newBoundary,
        duration_minutes: newSecondDuration
      });
    }

    await renderSchedule();
  } catch (err) {
    console.error('Error adjusting task duration:', err);
    alert('No se pudo ajustar el tiempo (posible solape)');
  }
}

function makeTaskRow(hour, item, showAdd) {
  const row = document.createElement('div');
  row.className = 'slot-task-row';

  const title = document.createElement('button');
  title.className = 'slot-task-title';
  title.type = 'button';
  title.textContent = item.title || 'Task';
  title.title = 'Renombrar tarea';
  title.addEventListener('click', async () => {
    const next = (prompt('Editar tarea', item.title || '') || '').trim();
    if (!next || next === item.title) return;
    try {
      await saveActivity(hour, {
        item_id: item.id,
        title: next,
        start_minute: item.start_minute ?? 0,
        duration_minutes: item.duration_minutes ?? 60
      });
      await renderSchedule();
    } catch (err) {
      console.error('Error renaming task:', err);
    }
  });

  const dur = document.createElement('button');
  dur.className = 'slot-task-duration';
  dur.type = 'button';
  dur.textContent = `${item.duration_minutes ?? 60}m`;
  dur.title = 'Ajustar minutos';
  dur.addEventListener('click', () => adjustTaskDuration(hour, item.id));

  const star = document.createElement('button');
  star.className = 'slot-task-star' + (item.featured ? ' active' : '');
  star.type = 'button';
  star.textContent = '★';
  star.title = item.featured ? 'Quitar destacado' : 'Destacar';
  star.addEventListener('click', async () => {
    try {
      const res = await fetch(`${CALENDAR_API_BASE}/calendar/item/${item.id}/featured`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({})
      });
      if (!res.ok) throw new Error(await res.text());
      await renderSchedule();
    } catch (err) {
      console.error('Error toggling featured:', err);
    }
  });

  const del = document.createElement('button');
  del.className = 'slot-task-delete';
  del.type = 'button';
  del.textContent = '×';
  del.title = 'Eliminar tarea';
  del.addEventListener('click', async () => {
    try {
      await deleteActivity(hour, item.id);
      await renderSchedule();
    } catch (err) {
      console.error('Error deleting task:', err);
    }
  });

  row.appendChild(title);
  row.appendChild(dur);
  row.appendChild(star);
  row.appendChild(del);

  if (showAdd) {
    const addBtn = document.createElement('button');
    addBtn.className = 'slot-add-btn';
    addBtn.type = 'button';
    addBtn.textContent = '+';
    addBtn.title = 'Añadir segunda tarea';
    addBtn.addEventListener('click', () => addTaskToSlot(hour));
    row.appendChild(addBtn);
  }

  return row;
}

async function renderSchedule() {
  if (!scheduleDate || !scheduleHours) return;

  const day = selectedDate;
  const options = { weekday: 'long', month: 'long', day: 'numeric' };
  scheduleDate.textContent = day.toLocaleDateString('en-US', options);

  scheduleHours.innerHTML = '';

  try {
    await loadActivities();
  } catch (err) {
    console.error('Error loading calendar activities:', err);
    scheduleHours.innerHTML = '<div class="hour-slot" style="grid-column:1 / -1;">Error loading calendar</div>';
    return;
  }
  
  // Create hourly slots from 5 to 21
  for (let hour = 5; hour <= 21; hour++) {
    const hourSlot = document.createElement('div');
    hourSlot.classList.add('hour-slot');
    hourSlot.dataset.hour = hour;
    
    const hourLabel = document.createElement('div');
    hourLabel.classList.add('hour-label');
    hourLabel.textContent = `${hour.toString().padStart(2, '0')}:00`;

    const slot = currentSlotsByHour[hour] || { items: [] };
    const items = (slot.items || []).slice().sort((a, b) => (a.start_minute ?? 0) - (b.start_minute ?? 0));

    const slotContent = document.createElement('div');
    slotContent.className = 'slot-content';

    items.forEach((item, idx) => {
      const isLast = idx === items.length - 1;
      const showAdd = isLast && items.length < 2;
      slotContent.appendChild(makeTaskRow(hour, item, showAdd));
    });

    if (items.length === 0) {
      const emptyRow = document.createElement('div');
      emptyRow.className = 'slot-empty-row';

      const quickInput = document.createElement('input');
      quickInput.type = 'text';
      quickInput.className = 'activity-input';
      quickInput.placeholder = 'Activity';
      quickInput.dataset.hour = String(hour);

      quickInput.addEventListener('keydown', async (e) => {
        if (e.key !== 'Enter') return;
        e.preventDefault();
        const title = quickInput.value.trim();
        if (!title) return;
        try {
          await saveActivity(hour, {
            item_id: null,
            title,
            start_minute: 0,
            duration_minutes: 60
          });
          await renderSchedule();
        } catch (err) {
          console.error('Error creating slot activity:', err);
        }
      });

      const addBtn = document.createElement('button');
      addBtn.className = 'slot-add-btn';
      addBtn.type = 'button';
      addBtn.textContent = '+';
      addBtn.title = 'A\u00f1adir tarea';
      addBtn.addEventListener('click', () => addTaskToSlot(hour));

      emptyRow.appendChild(quickInput);
      emptyRow.appendChild(addBtn);
      slotContent.appendChild(emptyRow);
    }

    const canDrag = items.length > 0;
    hourSlot.draggable = canDrag;
    if (canDrag) {
      hourSlot.classList.add('slot-draggable');
      hourSlot.addEventListener('dragstart', () => {
        draggedHour = hour;
        hourSlot.classList.add('dragging');
      });
      hourSlot.addEventListener('dragend', () => {
        draggedHour = null;
        hourSlot.classList.remove('dragging');
      });
    }
    
    // Drop zone events
    hourSlot.addEventListener('dragover', (e) => {
      e.preventDefault();
      hourSlot.classList.add('drag-over');
    });
    
    hourSlot.addEventListener('dragleave', (e) => {
      hourSlot.classList.remove('drag-over');
    });
    
    hourSlot.addEventListener('drop', (e) => {
      e.preventDefault();
      hourSlot.classList.remove('drag-over');
      
      const targetHour = parseInt(hourSlot.dataset.hour);
      
      if (draggedHour !== null && draggedHour !== targetHour) {
        swapActivities(draggedHour, targetHour)
          .then(() => renderSchedule())
          .catch(err => console.error('Error swapping calendar activities:', err));
      }
    });
    
    // Highlight current hour only when viewing today
    const now = new Date();
    const isToday = toIsoDate(day) === toIsoDate(now);
    const currentHour = now.getHours();
    if (isToday && hour === currentHour) {
      hourSlot.classList.add('current-hour');
    }

    hourSlot.appendChild(hourLabel);
    hourSlot.appendChild(slotContent);
    scheduleHours.appendChild(hourSlot);
  }
  
  // Scroll to current hour
  setTimeout(() => {
    const currentSlot = scheduleHours.querySelector('.current-hour');
    if (currentSlot) {
      currentSlot.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
  }, 100);
}

if (monthCalendarPrev) {
  monthCalendarPrev.addEventListener('click', () => {
    monthCursor = new Date(monthCursor.getFullYear(), monthCursor.getMonth() - 1, 1);
    refreshMonthCalendar();
  });
}

if (monthCalendarNext) {
  monthCalendarNext.addEventListener('click', () => {
    monthCursor = new Date(monthCursor.getFullYear(), monthCursor.getMonth() + 1, 1);
    refreshMonthCalendar();
  });
}

if (scheduleCreateEventBtn) {
  scheduleCreateEventBtn.addEventListener('click', async () => {
    const title = (prompt('Event title') || '').trim();
    if (!title) return;

    const timeInput = prompt('Start time (HH:MM)', '09:00');
    if (!timeInput) return;
    const hm = parseHourMinute(timeInput);
    if (!hm) {
      alert('Invalid time. Use HH:MM');
      return;
    }

    const durationInput = prompt('Duration in minutes', '60');
    if (!durationInput) return;
    const duration = Number(durationInput);
    if (!Number.isFinite(duration) || duration <= 0) {
      alert('Duration must be a positive number');
      return;
    }

    const startDt = new Date(
      selectedDate.getFullYear(),
      selectedDate.getMonth(),
      selectedDate.getDate(),
      hm.hour,
      hm.minute,
      0,
      0
    );
    const endDt = new Date(startDt.getTime() + duration * 60 * 1000);

    const anchorSlot = currentSlotsByHour[hm.hour];
    if (!anchorSlot?.slot_id) {
      alert('No slot available for that hour');
      return;
    }

    try {
      const res = await fetch(`${CALENDAR_API_BASE}/calendar/slot/${anchorSlot.slot_id}/item`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          title,
          item_kind: 'event',
          duration_minutes: duration,
          start_minute: hm.minute,
          start_time: toLocalIsoDateTime(startDt),
          end_time: toLocalIsoDateTime(endDt)
        })
      });

      if (!res.ok) {
        const errText = await res.text();
        throw new Error(errText);
      }

      await refreshMonthCalendar();
      await renderSchedule();
    } catch (err) {
      console.error('Error creating event:', err);
      alert('Could not create event. It may overlap with another event.');
    }
  });
}

// Initial render
refreshMonthCalendar().then(() => renderSchedule());
