const API = "https://api-dashboard-production-fc05.up.railway.app/tasks/today";
const API_CHECKBOX = "https://api-dashboard-production-fc05.up.railway.app/tasks/today/checkbox";
const API_MOVE = "https://api-dashboard-production-fc05.up.railway.app/tasks/today/move";
const API_REFRESH = "https://api-dashboard-production-fc05.up.railway.app/tasks/today/refresh_occurrences";
const taskList = document.getElementById("task");

let draggedTask = null;

const OccurrenceSections = {
    morning: createOccurrenceSection("Morning", "morning"),
    afternoon: createOccurrenceSection("Afternoon", "afternoon"),
    evening: createOccurrenceSection("Evening", "evening")
}

Object.values(OccurrenceSections).forEach(section => taskList.appendChild(section));

function getOccurrence() {
    const hour = new Date().getHours();
    if (hour >= 5 && hour < 12) return "morning";
    if (hour >= 12 && hour < 18) return "afternoon";
    return "evening";
}

function createOccurrenceSection(titleText, occurrence) {
    const section = document.createElement("div");
    section.classList.add("task-section");
    section.dataset.occurrence = occurrence;

    const title = document.createElement("h3");
    title.textContent = titleText;
    title.classList.add("task-section-title");

    const ul = document.createElement("ul");
    ul.classList.add("task-list");
    ul.dataset.occurrence = occurrence;

    // Drop events
    ul.addEventListener("dragover", handleDragOver);
    ul.addEventListener("dragleave", handleDragLeave);
    ul.addEventListener("drop", handleDrop);

    title.addEventListener("click", () => {
        const wasCollapsed = section.classList.contains("collapsed");
        // Collapse all sections first
        Object.values(OccurrenceSections).forEach(s => s.classList.add("collapsed"));
        // Toggle the clicked one open if it was collapsed
        if (wasCollapsed) {
            section.classList.remove("collapsed");
        }
    });

    section.appendChild(title);
    section.appendChild(ul);

    return section;
}


function renderTasks(tasks) {
    Object.values(OccurrenceSections).forEach(section => {
    section.querySelector("ul").innerHTML = "";
    });
    tasks.forEach(task => {
        renderTask(task);
    });
}

function renderTask(task) {
    const li = document.createElement("li");
    li.classList.add("task-item");
    li.draggable = true;
    li.dataset.taskId = task.occurrences_id;
    li.dataset.position = task.position;
    li.dataset.occurrence = task.day_context;
    
    const task_span = document.createElement("span");
    task_span.textContent = task.name.trim();

    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = Boolean(task.completed);
    checkbox.dataset.taskId = task.occurrences_id;
    checkbox.style.display = "none"; // Oculta el checkbox visualmente

    li.appendChild(checkbox);
    li.appendChild(task_span);

    if (task.completed) {
        li.classList.add("completed");
    }

    // Drag events
    li.addEventListener("dragstart", handleDragStart);
    li.addEventListener("dragend", handleDragEnd);

    // Permite tachar/destachar haciendo clic en el texto o el li
    li.addEventListener("click", (e) => {
        // Evita conflicto con drag o clicks en botones internos
        if (e.target.tagName === "INPUT") return;
        const completed = !li.classList.contains("completed");
        li.classList.toggle("completed", completed);
        checkbox.checked = completed;
        updateTaskCheckbox(task.occurrences_id, completed);
    });

    // Mantén el evento original por si acaso
    checkbox.addEventListener("change", () => {
        const completed = checkbox.checked;
        li.classList.toggle("completed", completed);
        updateTaskCheckbox(task.occurrences_id, completed);
    });

    const context = task.day_context; 
    OccurrenceSections[context].querySelector("ul").appendChild(li);
}

function showOccurrenceTasks(activeContext) {
    Object.entries(OccurrenceSections).forEach(([context, section]) => {
        if (context === activeContext) {
            section.classList.remove("collapsed");
            section.classList.add("current-occurrence");
        } else {
            section.classList.add("collapsed");
            section.classList.remove("current-occurrence");
        }
    });
}


// Función para refrescar occurrences y luego cargar tasks
function loadTasks() {
  return fetch(API_REFRESH, { method: "POST" })
    .then(res => res.json())
    .catch(err => {
      console.warn("Refresh occurrences warning:", err);
      // Continuar aunque falle el refresh
    })
    .then(() => fetch(API))
    .then(res => {
      if (!res.ok) throw new Error("Error loading tasks");
      return res.json();
    })
    .then(tasks => {
      renderTasks(tasks);
      const activeContext = getOccurrence();
      showOccurrenceTasks(activeContext);
    });
}

// Cargar tasks al inicio
loadTasks();


function updateTaskCheckbox(taskId, completed) {
  fetch(API_CHECKBOX, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ occurrences_id: taskId, completed })
  })
  .then(() => {
    // Refrescar occurrences después de completar una tarea
    // (por si hay tareas que deben moverse)
    return loadTasks();
  })
  .catch(err => {
    console.error("Error updating task", err);
  });
}

// ========== DRAG AND DROP HANDLERS ==========

function handleDragStart(e) {
    draggedTask = {
        id: parseInt(e.target.dataset.taskId),
        occurrence: e.target.dataset.occurrence,
        element: e.target
    };
    e.target.classList.add("dragging");
}

function handleDragEnd(e) {
    e.target.classList.remove("dragging");
    draggedTask = null;
}

function handleDragOver(e) {
    e.preventDefault();
    const ul = e.currentTarget;
    ul.classList.add("drag-over");
}

function handleDragLeave(e) {
    const ul = e.currentTarget;
    ul.classList.remove("drag-over");
}

function handleDrop(e) {
    e.preventDefault();
    const ul = e.currentTarget;
    ul.classList.remove("drag-over");
    
    if (!draggedTask) return;
    
    const targetOccurrence = ul.dataset.occurrence;
    const afterElement = getDragAfterElement(ul, e.clientY);
    
    let before_id = null;
    let after_id = null;
    
    if (afterElement == null) {
        // Drop al final de la lista
        const items = [...ul.querySelectorAll('.task-item:not(.dragging)')];
        if (items.length > 0) {
            before_id = parseInt(items[items.length - 1].dataset.taskId);
        }
    } else {
        // Drop entre elementos
        after_id = parseInt(afterElement.dataset.taskId);
        const prevElement = afterElement.previousElementSibling;
        if (prevElement && prevElement.classList.contains('task-item') && !prevElement.classList.contains('dragging')) {
            before_id = parseInt(prevElement.dataset.taskId);
        }
    }
    
    const payload = {
        occurrences_id: draggedTask.id,
        before_id: before_id,
        after_id: after_id
    };
    
    // Solo añadir target_occurrence si cambia de sección
    if (targetOccurrence !== draggedTask.occurrence) {
        payload.target_occurrence = targetOccurrence;
    }
    
    moveTask(payload);
}

function getDragAfterElement(container, y) {
    const draggableElements = [...container.querySelectorAll('.task-item:not(.dragging)')];
    
    return draggableElements.reduce((closest, child) => {
        const box = child.getBoundingClientRect();
        const offset = y - box.top - box.height / 2;
        
        if (offset < 0 && offset > closest.offset) {
            return { offset: offset, element: child };
        } else {
            return closest;
        }
    }, { offset: Number.NEGATIVE_INFINITY }).element;
}

function moveTask(payload) {
    fetch(API_MOVE, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
    })
    .then(res => {
        if (!res.ok) throw new Error("Move failed");
        return res.json();
    })
    .then(() => {

        fetch(API)
            .then(res => res.json())
            .then(tasks => {
                renderTasks(tasks);
                const activeContext = getOccurrence();
                showOccurrenceTasks(activeContext);
            });
    })
    .catch(err => {
        console.error("Error moving task", err);
        // Recargar en caso de error para restaurar estado
        fetch(API)
            .then(res => res.json())
            .then(tasks => {
                renderTasks(tasks);
                const activeContext = getOccurrence();
                showOccurrenceTasks(activeContext);
            });
    });
}




