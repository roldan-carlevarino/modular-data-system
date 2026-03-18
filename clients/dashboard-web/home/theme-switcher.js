const themes = {
    'dark-cyan': 'Dark - Cyan',
    'light-cyan': 'Light - Cyan',
    'dark-purple': 'Dark - Purple'
}

const DEFAULT_THEME = 'dark-cyan';

function normalizeTheme(themeName) {
    return Object.prototype.hasOwnProperty.call(themes, themeName)
        ? themeName
        : DEFAULT_THEME;
}

function loadTheme() {
    const savedTheme = localStorage.getItem('theme');
    const attrTheme = document.documentElement.getAttribute('data-theme');
    const initialTheme = normalizeTheme(savedTheme || attrTheme || DEFAULT_THEME);
    setTheme(initialTheme);
    return initialTheme;
}

function setTheme(themeName) {
    const normalizedTheme = normalizeTheme(themeName);
    document.documentElement.setAttribute('data-theme', normalizedTheme);
    localStorage.setItem('theme', normalizedTheme);

    const themeSelect = document.getElementById('themeSelect');
    if (themeSelect && themeSelect.value !== normalizedTheme) {
        themeSelect.value = normalizedTheme;
    }
}


function populateThemeSelector() {
    const themeSelect = document.getElementById('themeSelect');
    
    if (!themeSelect) {
        console.error('themeSelect element not found');
        return;
    }
    
    themeSelect.innerHTML = '';
    
    Object.entries(themes).forEach(([key, label]) => {
        const option = document.createElement('option');
        option.value = key;
        option.textContent = label;
        themeSelect.appendChild(option);
    });
    
    const activeTheme = loadTheme();
    themeSelect.value = activeTheme;
    
    themeSelect.addEventListener('change', (e) => {
        setTheme(e.target.value);
    });
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', populateThemeSelector);
} else {
    populateThemeSelector();
}
