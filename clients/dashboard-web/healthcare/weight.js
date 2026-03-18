const WEIGHT_API_BASE = "https://api-dashboard-production-fc05.up.railway.app";
const WEIGHT_TODAY_API = `${WEIGHT_API_BASE}/weight/today`;
const WEIGHT_NEW_API = `${WEIGHT_API_BASE}/weight/new`;

let weightIsUpdating = false;

function setWeightStatus(message, isError = false) {
  const statusEl = document.getElementById("weightStatus");
  if (!statusEl) return;
  statusEl.textContent = message || "";
  statusEl.style.color = isError ? "#ff8a8a" : "rgba(255, 255, 255, 0.72)";
}

function setWeightValue(weightKg) {
  const valueEl = document.getElementById("weightTodayValue");
  const btnEl = document.getElementById("weightBtnValue");
  const numeric = Number(weightKg);
  const text = Number.isFinite(numeric) && numeric > 0 ? `${numeric} kg` : "-- kg";
  if (valueEl) valueEl.textContent = text;
  if (btnEl) btnEl.textContent = text;
}

async function loadTodayWeight() {
  try {
    const response = await fetch(WEIGHT_TODAY_API);
    if (!response.ok) {
      throw new Error(`Failed to load weight (${response.status})`);
    }

    const data = await response.json();
    setWeightValue(data.weight);
    setWeightStatus("");
  } catch (error) {
    console.error("Error loading weight:", error);
    setWeightStatus("Could not load weight", true);
  }
}

async function saveTodayWeight(weightKg) {
  if (weightIsUpdating) return;

  weightIsUpdating = true;
  setWeightStatus("Saving...");

  try {
    const response = await fetch(WEIGHT_NEW_API, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ weight: weightKg }),
    });

    if (!response.ok) {
      const errorText = await response.text();
      throw new Error(errorText || `Failed to save weight (${response.status})`);
    }

    setWeightValue(weightKg);
    setWeightStatus("Weight saved");
  } catch (error) {
    console.error("Error saving weight:", error);
    setWeightStatus("Could not save weight", true);
  } finally {
    weightIsUpdating = false;
  }
}

document.addEventListener("DOMContentLoaded", () => {
  const saveBtn = document.getElementById("saveWeightBtn");
  const inputEl = document.getElementById("weightInput");

  if (saveBtn && inputEl) {
    const submit = () => {
      const value = Number(inputEl.value);
      if (!Number.isFinite(value) || value <= 0) {
        setWeightStatus("Enter a valid weight", true);
        return;
      }
      saveTodayWeight(Math.round(value));
    };

    saveBtn.addEventListener("click", submit);
    inputEl.addEventListener("keydown", (event) => {
      if (event.key === "Enter") submit();
    });
  }

  loadTodayWeight();
});
