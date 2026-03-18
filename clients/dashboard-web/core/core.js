// Guardar el tab activo cuando se selecciona
document.querySelectorAll('.tab-labels label').forEach((label, idx) => {
    label.addEventListener('click', function () {
        localStorage.setItem('activeTab', idx);
    });
});

// Restaurar el tab activo al cargar la página
window.addEventListener('DOMContentLoaded', function () {
    const activeTabIdx = localStorage.getItem('activeTab');
    if (activeTabIdx !== null) {
        // Marcar el input radio correspondiente como checked
        const radios = document.querySelectorAll('.tabs input[type="radio"]');
        if (radios[activeTabIdx]) {
            radios[activeTabIdx].checked = true;
        }
    }
});