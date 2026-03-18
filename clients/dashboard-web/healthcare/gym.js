const GYM_API_BASE = "https://api-dashboard-production-fc05.up.railway.app/gym";

let gymState = {
  routines: [],
  todaySession: null,
  selectedExerciseLog: null,
  selectedRoutine: null,
  isLoading: false
};

// ========== HELPERS ==========

function setGymStatus(message, isError = false) {
  const el = document.getElementById("gymStatus");
  if (!el) return;
  el.textContent = message || "";
  el.style.color = isError ? "#ff8a8a" : "rgba(255, 255, 255, 0.72)";
}

function showGymSection(sectionId) {
  const sections = ["gymRoutineList", "gymRoutineDetail", "gymSessionView", "gymSetLogger"];
  sections.forEach(id => {
    const el = document.getElementById(id);
    if (el) el.style.display = id === sectionId ? "block" : "none";
  });
}



async function loadRoutines() {
  try {
    const res = await fetch(`${GYM_API_BASE}/routines`);
    if (!res.ok) throw new Error("Failed to load routines");
    gymState.routines = await res.json();
    renderRoutineList();
  } catch (err) {
    console.error(err);
    setGymStatus("Could not load routines", true);
  }
}

async function loadTodaySession() {
  try {
    const res = await fetch(`${GYM_API_BASE}/sessions/today`);
    if (!res.ok) throw new Error("Failed to load today's session");
    const data = await res.json();
    
    if (data.id) {
      gymState.todaySession = data;
      renderSessionView();
      showGymSection("gymSessionView");
    } else {
      gymState.todaySession = null;
      showGymSection("gymRoutineList");
    }
  } catch (err) {
    console.error(err);
    setGymStatus("Could not load today's session", true);
    showGymSection("gymRoutineList");
  }
}

async function startSession(routineId) {
  setGymStatus("Starting session...");
  try {
    const res = await fetch(`${GYM_API_BASE}/sessions/today?routine_id=${routineId}`);
    if (!res.ok) throw new Error("Failed to start session");
    gymState.todaySession = await res.json();
    renderSessionView();
    showGymSection("gymSessionView");
    setGymStatus("Session started!");
  } catch (err) {
    console.error(err);
    setGymStatus("Could not start session", true);
  }
}

async function addExerciseToSession(routineExerciseId) {
  if (!gymState.todaySession) return;
  
  setGymStatus("Adding exercise...");
  try {
    const res = await fetch(`${GYM_API_BASE}/sessions/today/exercises`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ routine_exercise_id: routineExerciseId })
    });
    if (!res.ok) throw new Error("Failed to add exercise");
    
    await loadTodaySession();
    setGymStatus("Exercise added!");
  } catch (err) {
    console.error(err);
    setGymStatus("Could not add exercise", true);
  }
}

async function loadExerciseSets(exerciseLogId) {
  try {
    const res = await fetch(`${GYM_API_BASE}/log-exercises/${exerciseLogId}/sets`);
    if (!res.ok) throw new Error("Failed to load sets");
    return await res.json();
  } catch (err) {
    console.error(err);
    setGymStatus("Could not load sets", true);
    return null;
  }
}

async function saveSet(exerciseLogId, setData) {
  setGymStatus("Saving set...");
  try {
    const res = await fetch(`${GYM_API_BASE}/log-exercises/${exerciseLogId}/sets`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(setData)
    });
    if (!res.ok) throw new Error("Failed to save set");
    setGymStatus("Set saved!");
    return true;
  } catch (err) {
    console.error(err);
    setGymStatus("Could not save set", true);
    return false;
  }
}

// ========== RENDER FUNCTIONS ==========

function renderRoutineList() {
  const container = document.getElementById("routineListContainer");
  if (!container) return;
  
  if (gymState.routines.length === 0) {
    container.innerHTML = '<p class="gym-empty">No routines available</p>';
    return;
  }
  
  container.innerHTML = gymState.routines
    .filter(r => r.status === "active")
    .map(r => `
      <div class="gym-routine-card" data-routine-id="${r.id}">
        <span class="routine-name">${r.routine}</span>
        <div class="routine-buttons">
          <button class="btn-small btn-view" data-routine-id="${r.id}">View</button>
          <button class="btn-small btn-start" data-routine-id="${r.id}">Start</button>
        </div>
      </div>
    `).join("");
  
  container.querySelectorAll(".btn-start").forEach(btn => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      startSession(Number(btn.dataset.routineId));
    });
  });
  
  container.querySelectorAll(".btn-view").forEach(btn => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      viewRoutineDetail(Number(btn.dataset.routineId));
    });
  });
}

async function viewRoutineDetail(routineId) {
  setGymStatus("Loading routine...");
  try {
    const res = await fetch(`${GYM_API_BASE}/routines/${routineId}`);
    if (!res.ok) throw new Error("Failed to load routine");
    const routine = await res.json();
    gymState.selectedRoutine = routine;
    renderRoutineDetail(routine);
    showGymSection("gymRoutineDetail");
    setGymStatus("");
  } catch (err) {
    console.error(err);
    setGymStatus("Could not load routine", true);
  }
}

function renderRoutineDetail(routine) {
  const titleEl = document.getElementById("routineDetailTitle");
  const listEl = document.getElementById("routineExercisesList");
  
  if (titleEl) titleEl.textContent = routine.routine;
  
  if (listEl) {
    if (routine.exercises && routine.exercises.length > 0) {
      // Group by weekday if present
      const byWeekday = {};
      const weekdayNames = ['', 'Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado', 'Domingo'];
      
      routine.exercises.forEach(ex => {
        const day = ex.weekday || 0;
        if (!byWeekday[day]) byWeekday[day] = [];
        byWeekday[day].push(ex);
      });
      
      let html = '';
      const days = Object.keys(byWeekday).map(Number).sort((a, b) => a - b);
      
      days.forEach(day => {
        if (day > 0 && days.length > 1) {
          html += `<h4 class="weekday-header">${weekdayNames[day]}</h4>`;
        }
        byWeekday[day].forEach(ex => {
          html += `
            <div class="gym-exercise-card">
              <div class="exercise-info">
                <span class="exercise-name">${ex.exercise}</span>
                <span class="exercise-target">${ex.series}x${ex.reps}</span>
              </div>
            </div>
          `;
        });
      });
      
      listEl.innerHTML = html;
    } else {
      listEl.innerHTML = '<p class="gym-empty">No exercises in this routine</p>';
    }
  }
}

function renderSessionView() {
  const session = gymState.todaySession;
  if (!session) return;
  
  const titleEl = document.getElementById("sessionTitle");
  const exercisesEl = document.getElementById("sessionExercises");
  const addExerciseEl = document.getElementById("addExerciseSelect");
  
  if (titleEl) titleEl.textContent = session.routine || "Today's Session";
  
  if (exercisesEl) {
    if (session.exercises && session.exercises.length > 0) {
      exercisesEl.innerHTML = session.exercises.map(ex => `
        <div class="gym-exercise-card" data-log-id="${ex.log_id}">
          <div class="exercise-info">
            <span class="exercise-name">${ex.exercise}</span>
            <span class="exercise-target">${ex.target_series}x${ex.target_reps}</span>
          </div>
          <button class="btn-small btn-log" data-log-id="${ex.log_id}" data-exercise="${ex.exercise}">Log Sets</button>
        </div>
      `).join("");
      
      exercisesEl.querySelectorAll(".btn-log").forEach(btn => {
        btn.addEventListener("click", () => {
          openSetLogger(Number(btn.dataset.logId), btn.dataset.exercise);
        });
      });
    } else {
      exercisesEl.innerHTML = '<p class="gym-empty">No exercises logged yet. Add one below.</p>';
    }
  }
  
  // Populate add exercise dropdown with routine exercises not yet added
  if (addExerciseEl) {
    loadRoutineExercises(session.routine_id, addExerciseEl);
  }
}

async function loadRoutineExercises(routineId, selectEl) {
  try {
    const res = await fetch(`${GYM_API_BASE}/routines/${routineId}`);
    if (!res.ok) throw new Error("Failed to load routine");
    const routine = await res.json();
    
    const addedIds = new Set((gymState.todaySession?.exercises || []).map(e => e.routine_exercise_id));
    const available = routine.exercises.filter(e => !addedIds.has(e.id));
    
    if (available.length === 0) {
      selectEl.innerHTML = '<option value="">All exercises added</option>';
      selectEl.disabled = true;
    } else {
      selectEl.innerHTML = '<option value="">+ Add exercise...</option>' +
        available.map(e => `<option value="${e.id}">${e.exercise} (${e.series}x${e.reps})</option>`).join("");
      selectEl.disabled = false;
    }
  } catch (err) {
    console.error(err);
    selectEl.innerHTML = '<option value="">Error loading</option>';
  }
}

async function openSetLogger(exerciseLogId, exerciseName) {
  gymState.selectedExerciseLog = exerciseLogId;
  
  const titleEl = document.getElementById("setLoggerTitle");
  const setsContainer = document.getElementById("setsContainer");
  
  if (titleEl) titleEl.textContent = exerciseName;
  if (setsContainer) setsContainer.innerHTML = '<p>Loading sets...</p>';
  
  showGymSection("gymSetLogger");
  
  const data = await loadExerciseSets(exerciseLogId);
  if (!data) return;
  
  renderSets(data.sets || []);
}

function renderSets(sets) {
  const container = document.getElementById("setsContainer");
  if (!container) return;
  
  let html = '<div class="sets-table">';
  html += '<div class="sets-header"><span>Set</span><span>Weight</span><span>Reps</span><span>RIR</span><span></span></div>';
  
  // Existing sets
  sets.forEach(s => {
    html += `
      <div class="set-row" data-set-number="${s.set_number}">
        <span class="set-num">${s.set_number}</span>
        <span>${s.weight || '-'} kg</span>
        <span>${s.reps || '-'}</span>
        <span>${s.rir !== null ? s.rir : '-'}</span>
        <button class="btn-tiny btn-edit-set" data-set="${s.set_number}" data-weight="${s.weight || ''}" data-reps="${s.reps || ''}" data-rir="${s.rir || ''}">Edit</button>
      </div>
    `;
  });
  
  html += '</div>';
  
  // New set form
  const nextSetNum = sets.length > 0 ? Math.max(...sets.map(s => s.set_number)) + 1 : 1;
  html += `
    <div class="new-set-form">
      <h4>Add Set #${nextSetNum}</h4>
      <div class="set-inputs">
        <input type="number" id="newSetWeight" placeholder="Weight (kg)" step="0.5" min="0">
        <input type="number" id="newSetReps" placeholder="Reps" min="1">
        <input type="number" id="newSetRir" placeholder="RIR" min="0" max="10">
      </div>
      <button class="btn-primary" id="saveNewSetBtn" data-set-number="${nextSetNum}">Save Set</button>
    </div>
  `;
  
  container.innerHTML = html;
  
  // Event listeners
  document.getElementById("saveNewSetBtn")?.addEventListener("click", async () => {
    const setNumber = Number(document.getElementById("saveNewSetBtn").dataset.setNumber);
    const weight = parseFloat(document.getElementById("newSetWeight").value) || null;
    const reps = parseInt(document.getElementById("newSetReps").value) || null;
    const rir = parseInt(document.getElementById("newSetRir").value);
    
    if (!reps) {
      setGymStatus("Please enter reps", true);
      return;
    }
    
    const success = await saveSet(gymState.selectedExerciseLog, {
      set_number: setNumber,
      weight: weight,
      reps: reps,
      rir: isNaN(rir) ? null : rir
    });
    
    if (success) {
      const data = await loadExerciseSets(gymState.selectedExerciseLog);
      if (data) renderSets(data.sets || []);
    }
  });
  
  container.querySelectorAll(".btn-edit-set").forEach(btn => {
    btn.addEventListener("click", () => {
      document.getElementById("newSetWeight").value = btn.dataset.weight;
      document.getElementById("newSetReps").value = btn.dataset.reps;
      document.getElementById("newSetRir").value = btn.dataset.rir;
      document.getElementById("saveNewSetBtn").dataset.setNumber = btn.dataset.set;
      document.querySelector(".new-set-form h4").textContent = `Edit Set #${btn.dataset.set}`;
    });
  });
}

// ========== INITIALIZATION ==========

document.addEventListener("DOMContentLoaded", () => {
  // Back to routines button
  document.getElementById("backToRoutines")?.addEventListener("click", () => {
    showGymSection("gymRoutineList");
  });
  
  // Back to routine list from detail
  document.getElementById("backToRoutineList")?.addEventListener("click", () => {
    showGymSection("gymRoutineList");
  });
  
  // Start routine from detail view
  document.getElementById("startRoutineFromDetail")?.addEventListener("click", () => {
    if (gymState.selectedRoutine) {
      startSession(gymState.selectedRoutine.id);
    }
  });
  
  // Back to session button
  document.getElementById("backToSession")?.addEventListener("click", () => {
    showGymSection("gymSessionView");
  });
  
  // Add exercise select
  document.getElementById("addExerciseSelect")?.addEventListener("change", (e) => {
    const val = Number(e.target.value);
    if (val) {
      addExerciseToSession(val);
      e.target.value = "";
    }
  });
  
  // Load data
  loadRoutines();
  loadTodaySession();
});
