// ---------------- TASKS ----------------

const API_URL = "https://api-dashboard-production-fc05.up.railway.app/task/log/today";
const API_UPDATE_URL = "https://api-dashboard-production-fc05.up.railway.app/tasks/log/today/status";
const taskList = document.getElementById("tasks");

// Cargar tareas de hoy
fetch(API_URL)
  .then(res => res.json())
  .then(tasks => {
    console.log("Tasks recibidas:", tasks); // 👈 Debug
    tasks.forEach(task => {
      console.log(`Task ${task.task_id}: completed =`, task.completed, typeof task.completed); // 👈 Debug
      const li = document.createElement("li");
      li.classList.add("task-item");
      li.dataset.taskId = task.task_id;

      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.checked = Boolean(task.completed); // 👈 Forzar a boolean
      checkbox.dataset.taskId = task.task_id;

      const label = document.createElement("span");
      label.textContent = task.name.trim();

      if (task.completed) { // 👈 Esto también debería marcar como completed
        li.classList.add("completed");
      }

      checkbox.addEventListener("change", () => {
        const taskId = Number(checkbox.dataset.taskId); // 👈 int real
        const completed = checkbox.checked;              // 👈 bool real

        // UI optimista
        li.classList.toggle("completed", completed);

        updateTask(taskId, completed);
      });

      li.appendChild(checkbox);
      li.appendChild(label);
      taskList.appendChild(li);
    });
  })
  .catch(err => {
    console.error("Error cargando tasks:", err);
  });

// ---------------- API WRITE ----------------

function updateTask(taskId, completed) {
  fetch(API_UPDATE_URL, {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify({
      task_id: taskId,
      completed: completed
    })
  })
  .then(res => {
    if (!res.ok) {
      throw new Error("POST failed");
    }
  })
  .catch(err => {
    console.error("Error actualizando task:", err);
  });
}

// ---------------- DAILY SCHEDULE ----------------

const scheduleDate = document.getElementById('scheduleDate');
const scheduleHours = document.getElementById('scheduleHours');

let draggedElement = null;
let draggedHour = null;

// Load activities from localStorage
function loadActivities() {
  const stored = localStorage.getItem('dailySchedule');
  return stored ? JSON.parse(stored) : {};
}

// Save activities to localStorage
function saveActivities(activities) {
  localStorage.setItem('dailySchedule', JSON.stringify(activities));
}

function renderSchedule() {
  if (!scheduleDate || !scheduleHours) return;
  
  const today = new Date();
  const options = { weekday: 'long', month: 'long', day: 'numeric' };
  scheduleDate.textContent = today.toLocaleDateString('en-US', options);
  
  scheduleHours.innerHTML = '';
  const activities = loadActivities();
  
  // Create hourly slots from 5 to 21
  for (let hour = 5; hour <= 21; hour++) {
    const hourSlot = document.createElement('div');
    hourSlot.classList.add('hour-slot');
    hourSlot.dataset.hour = hour;
    
    const hourLabel = document.createElement('div');
    hourLabel.classList.add('hour-label');
    hourLabel.textContent = `${hour.toString().padStart(2, '0')}:00`;
    
    const activityInput = document.createElement('input');
    activityInput.type = 'text';
    activityInput.classList.add('activity-input');
    activityInput.placeholder = 'Activity';
    activityInput.value = activities[hour] || '';
    activityInput.dataset.hour = hour;
    
    // Make draggable if has content
    if (activityInput.value.trim()) {
      activityInput.draggable = true;
      activityInput.classList.add('has-content');
    }
    
    // Drag events
    activityInput.addEventListener('dragstart', (e) => {
      draggedElement = e.target;
      draggedHour = parseInt(e.target.dataset.hour);
      e.target.classList.add('dragging');
    });
    
    activityInput.addEventListener('dragend', (e) => {
      e.target.classList.remove('dragging');
      draggedElement = null;
      draggedHour = null;
    });
    
    // Save on input
    activityInput.addEventListener('input', (e) => {
      const allActivities = loadActivities();
      if (e.target.value.trim()) {
        allActivities[hour] = e.target.value;
        e.target.draggable = true;
        e.target.classList.add('has-content');
      } else {
        delete allActivities[hour];
        e.target.draggable = false;
        e.target.classList.remove('has-content');
      }
      saveActivities(allActivities);
    });
    
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
        const allActivities = loadActivities();
        const draggedActivity = allActivities[draggedHour];
        
        // Swap activities
        if (allActivities[targetHour]) {
          allActivities[draggedHour] = allActivities[targetHour];
        } else {
          delete allActivities[draggedHour];
        }
        
        allActivities[targetHour] = draggedActivity;
        saveActivities(allActivities);
        renderSchedule();
      }
    });
    
    // Highlight current hour
    const currentHour = today.getHours();
    if (hour === currentHour) {
      hourSlot.classList.add('current-hour');
    }
    
    hourSlot.appendChild(hourLabel);
    hourSlot.appendChild(activityInput);
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

// Initial render
renderSchedule();
