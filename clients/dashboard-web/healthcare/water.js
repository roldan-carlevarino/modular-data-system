const WATER_API_BASE = "https://api-dashboard-production-fc05.up.railway.app";
const WATER_TODAY_API = `${WATER_API_BASE}/water/today`;
const WATER_DRINK_API = `${WATER_API_BASE}/water/drink`;

let waterIsUpdating = false;

function setWaterStatus(message, isError = false) {
  const statusEl = document.getElementById("waterStatus");
  if (!statusEl) return;
  statusEl.textContent = message || "";
  statusEl.style.color = isError ? "#ff8a8a" : "rgba(255, 255, 255, 0.72)";
}

function setWaterTotal(totalMl) {
  const totalEl = document.getElementById("waterTotalValue");
  const btnEl = document.getElementById("waterBtnValue");
  const text = `${Number(totalMl || 0)} ml`;
  if (totalEl) totalEl.textContent = text;
  if (btnEl) btnEl.textContent = text;
}

async function loadWaterToday() {
  try {
    const response = await fetch(WATER_TODAY_API);
    if (!response.ok) {
      throw new Error(`Failed to load water total (${response.status})`);
    }

    const data = await response.json();
    setWaterTotal(data.water_total || 0);
    setWaterStatus("");
  } catch (error) {
    console.error("Error loading water total:", error);
    setWaterStatus("Could not load water total", true);
  }
}

async function addWater(amountMl) {
  if (waterIsUpdating) return;

  waterIsUpdating = true;
  setWaterStatus("Updating...");

  try {
    const response = await fetch(WATER_DRINK_API, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        water_increase: amountMl,
        water_event: "quick_add",
      }),
    });

    if (!response.ok) {
      const errorText = await response.text();
      throw new Error(errorText || `Failed to update water (${response.status})`);
    }

    const data = await response.json();
    setWaterTotal(data.water_total || 0);
    setWaterStatus(`+${amountMl} ml added`);
  } catch (error) {
    console.error("Error updating water:", error);
    setWaterStatus("Could not update water", true);
  } finally {
    waterIsUpdating = false;
  }
}

document.addEventListener("DOMContentLoaded", () => {
  const addButtons = document.querySelectorAll(".water-add-btn");
  addButtons.forEach((button) => {
    button.addEventListener("click", () => {
      const amount = Number(button.dataset.ml || 0);
      if (amount > 0) {
        addWater(amount);
      }
    });
  });

  loadWaterToday();
});
