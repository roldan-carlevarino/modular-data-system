
/* WEATHER AND TIME */

const weatherHourEl = document.querySelector('.weather-hour');
const weatherTempEl = document.querySelector('.weather-temp');
const weatherDescriptionEl = document.querySelector('.weather-description');
const scheduleDateEl = document.getElementById('scheduleDate');

const dateOptions = { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' };

function updateClockAndDate() {
  const now = new Date();

  if (weatherHourEl) {
    const weatherDate = now
      .toLocaleDateString('en-US', {
        weekday: 'long',
        month: 'long',
        day: 'numeric'
      })
      .replace(',', '');
    const weatherTime = now.toLocaleTimeString('en-US', {
      hour: '2-digit',
      minute: '2-digit',
      hour12: false
    });
    weatherHourEl.textContent = `${weatherDate} · ${weatherTime}`;
  }

  if (scheduleDateEl) {
    scheduleDateEl.textContent = now.toLocaleDateString('en-US', dateOptions);
  }
}

function weatherCodeLabel(code) {
  if (code === 0) return 'Clear sky';
  if (code >= 1 && code <= 3) return 'Partly cloudy';
  if (code === 45 || code === 48) return 'Fog';
  if (code >= 51 && code <= 57) return 'Drizzle';
  if (code >= 61 && code <= 67) return 'Rain';
  if (code >= 71 && code <= 77) return 'Snow';
  if (code >= 80 && code <= 82) return 'Rain showers';
  if (code >= 95 && code <= 99) return 'Thunderstorm';
  return 'Weather';
}

function weatherCodeIcon(code) {
  if (code === 0) return '☀️';
  if (code >= 1 && code <= 3) return '⛅';
  if (code === 45 || code === 48) return '🌫️';
  if (code >= 51 && code <= 57) return '🌦️';
  if (code >= 61 && code <= 67) return '🌧️';
  if (code >= 71 && code <= 77) return '🌨️';
  if (code >= 80 && code <= 82) return '🌦️';
  if (code >= 95 && code <= 99) return '⛈️';
  return '🌤️';
}

async function loadWeather() {
  // Añade hourly o daily para pronóstico
  const url = 'https://api.open-meteo.com/v1/forecast?latitude=50.8514&longitude=5.6909&current_weather=true&hourly=precipitation_probability,precipitation&daily=precipitation_sum,precipitation_probability_max&timezone=auto';
  
  try {
    const res = await fetch(url);
    const data = await res.json();
    
    // Temperatura actual
    const temp = data.current_weather.temperature;
    const weatherCode = data.current_weather.weathercode;
    if (weatherTempEl) weatherTempEl.textContent = `${Math.round(temp)}°C`;
    
    if (weatherDescriptionEl) {
      weatherDescriptionEl.textContent = weatherCodeIcon(weatherCode);
      weatherDescriptionEl.setAttribute('title', weatherCodeLabel(weatherCode));
      weatherDescriptionEl.setAttribute('aria-label', weatherCodeLabel(weatherCode));
    }

    updateClockAndDate();
    
  } catch (error) {
    console.error('Weather error:', error);
  }
}

updateClockAndDate();
loadWeather();
setInterval(loadWeather, 600000);
setInterval(updateClockAndDate, 30000);


/* LOGS */

