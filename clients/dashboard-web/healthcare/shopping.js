const API_BASE = "https://api-dashboard-production-fc05.up.railway.app";
const API_LIST = `${API_BASE}/shopping/list`;
const API_ITEMS = `${API_BASE}/shopping/items`;
const API_INSERT = `${API_BASE}/shopping/insert_list`;
const API_DELETE = `${API_BASE}/shopping/delete_list`;

let availableItems = [];
let currentList = [];

// Cargar lista actual
async function loadShoppingList() {
    try {
        const res = await fetch(API_LIST);
        if (!res.ok) throw new Error("Failed to load list");
        currentList = await res.json();
        renderShoppingList();
    } catch (err) {
        console.error("Error loading shopping list:", err);
    }
}

// Cargar items disponibles
async function loadAvailableItems() {
    try {
        const res = await fetch(API_ITEMS);
        if (!res.ok) throw new Error("Failed to load items");
        availableItems = await res.json();
    } catch (err) {
        console.error("Error loading available items:", err);
    }
}

// Renderizar lista actual
function renderShoppingList() {
    const listEl = document.getElementById("shoppingList");
    const btnEl = document.getElementById("shoppingBtnValue");
    listEl.innerHTML = "";

    // Update button value
    if (btnEl) {
        btnEl.textContent = currentList.length > 0 ? `${currentList.length} items` : "Empty";
    }

    if (currentList.length === 0) {
        listEl.innerHTML = "<li class='empty'>No items in list</li>";
        return;
    }

    currentList.forEach(item => {
        const li = document.createElement("li");
        li.classList.add("shopping-item");

        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.classList.add("delete-checkbox");
        checkbox.addEventListener("change", () => {
            if (checkbox.checked) {
                removeFromList(item);
            }
        });

        const span = document.createElement("span");
        span.textContent = item;

        li.appendChild(checkbox);
        li.appendChild(span);
        listEl.appendChild(li);
    });
}

// Agregar item a la lista
async function addToList(item) {
    try {
        const res = await fetch(API_INSERT, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ items: [item] })
        });

        if (!res.ok) {
            const errorText = await res.text();
            throw new Error(`Failed to add item: ${errorText}`);
        }
        await loadShoppingList();
    } catch (err) {
        console.error("Error adding item:", err);
    }
}

// Eliminar item de la lista
async function removeFromList(item) {
    try {
        const res = await fetch(API_DELETE, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ items: [item] })
        });

        if (!res.ok) {
            const errorText = await res.text();
            throw new Error(`Failed to remove item: ${errorText}`);
        }
        await loadShoppingList();
    } catch (err) {
        console.error("Error removing item:", err);
    }
}

// Filtrar y mostrar resultados de búsqueda
function filterItems(query) {
    const resultsEl = document.getElementById("searchResults");
    resultsEl.innerHTML = "";

    if (!query) {
        resultsEl.style.display = "none";
        return;
    }

    const filtered = availableItems.filter(item =>
        item.toLowerCase().includes(query.toLowerCase())
    );

    if (filtered.length === 0) {
        resultsEl.innerHTML = "<div class='no-results'>No items found</div>";
        resultsEl.style.display = "block";
        return;
    }

    filtered.forEach(item => {
        const div = document.createElement("div");
        div.classList.add("search-result-item");
        div.textContent = item;
        div.addEventListener("click", () => {
            addToList(item);
            document.getElementById("searchInput").value = "";
            document.getElementById("searchResults").style.display = "none";
            document.getElementById("itemSearchBox").style.display = "none";
        });
        resultsEl.appendChild(div);
    });

    resultsEl.style.display = "block";
}

// Inicializar
document.addEventListener("DOMContentLoaded", async () => {
    await loadAvailableItems();
    await loadShoppingList();

    // Botón para mostrar buscador
    const addBtn = document.getElementById("addItemBtn");
    const searchBox = document.getElementById("itemSearchBox");
    const searchInput = document.getElementById("searchInput");

    addBtn.addEventListener("click", () => {
        const isVisible = searchBox.style.display !== "none";
        searchBox.style.display = isVisible ? "none" : "block";
        if (!isVisible) {
            searchInput.focus();
        }
    });

    // Input de búsqueda
    searchInput.addEventListener("input", (e) => {
        filterItems(e.target.value);
    });

    // Cerrar buscador al hacer clic fuera
    document.addEventListener("click", (e) => {
        if (!searchBox.contains(e.target) && e.target !== addBtn) {
            searchBox.style.display = "none";
            searchInput.value = "";
            document.getElementById("searchResults").style.display = "none";
        }
    });
});