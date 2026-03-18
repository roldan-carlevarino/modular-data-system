// Welfare Index Line Chart
(function() {
  'use strict';

  const CHART_WIDTH = 400;
  const CHART_HEIGHT = 200;
  const PADDING = { top: 32, right: 10, bottom: 10, left: 10 }; // Aumentado el top

  /**
   * Generate demo data for the last 30 days
   */
  function generateDemoData() {
    const data = [];
    const today = new Date();
    for (let i = 29; i >= 0; i--) {
      const date = new Date(today);
      date.setDate(date.getDate() - i);
      // Generate somewhat realistic fluctuating data
      const base = 65 + Math.sin(i * 0.3) * 10;
      const noise = (Math.random() - 0.5) * 15;
      data.push({
        date: date,
        value: Math.round(Math.max(30, Math.min(100, base + noise)))
      });
    }
    return data;
  }

  /**
   * Render the line chart to a specific container
   * @param {Array} data - Array of {date, value} objects
   * @param {string} prefix - Element ID prefix ('home' for home tab, '' for healthcare tab)
   */
  function renderChartTo(data, prefix = '') {
    if (!data || data.length === 0) return;

    const lineEl = document.getElementById(prefix ? `${prefix}ChartLine` : 'chartLine');
    const areaEl = document.getElementById(prefix ? `${prefix}ChartArea` : 'chartArea');
    const dotsEl = document.getElementById(prefix ? `${prefix}ChartDots` : 'chartDots');
    const labelsEl = document.getElementById(prefix ? `${prefix}ChartLabels` : 'chartLabels');
    const currentEl = document.getElementById(prefix ? `${prefix}HealthIndexCurrent` : 'healthIndexCurrent');

    if (!lineEl || !areaEl) return;

    // Calculate bounds
    const minVal = Math.min(...data.map(d => d.value));
    const maxVal = Math.max(...data.map(d => d.value));
    const range = maxVal - minVal || 1;

    const plotWidth = CHART_WIDTH - PADDING.left - PADDING.right;
    const plotHeight = CHART_HEIGHT - PADDING.top - PADDING.bottom;

    // Generate line path
    const points = data.map((d, i) => {
      const x = PADDING.left + (i / (data.length - 1)) * plotWidth;
      const y = PADDING.top + plotHeight - ((d.value - minVal) / range) * plotHeight;
      return { x, y, value: d.value, date: d.date };
    });

    // Create line path
    const linePath = points.map((p, i) => 
      (i === 0 ? 'M' : 'L') + `${p.x},${p.y}`
    ).join(' ');
    lineEl.setAttribute('d', linePath);

    // Create area path (closed polygon under the line)
    const areaPath = linePath + 
      ` L${points[points.length - 1].x},${CHART_HEIGHT - PADDING.bottom}` +
      ` L${PADDING.left},${CHART_HEIGHT - PADDING.bottom} Z`;
    areaEl.setAttribute('d', areaPath);

    // Create dots
    dotsEl.innerHTML = points.map((p, i) => 
      `<circle class="chart-dot" cx="${p.x}" cy="${p.y}" r="3" data-value="${p.value}" data-date="${p.date.toLocaleDateString()}" />`
    ).join('');

    // Add tooltips on hover
    dotsEl.querySelectorAll('.chart-dot').forEach(dot => {
      dot.addEventListener('mouseenter', (e) => {
        if (currentEl) {
          currentEl.textContent = e.target.dataset.value;
          currentEl.title = e.target.dataset.date;
        }
      });
    });

    // Create labels (first, middle, last dates)
    if (labelsEl && data.length > 0) {
      const firstDate = data[0].date;
      const lastDate = data[data.length - 1].date;
      const formatDate = (d) => `${d.getDate()}/${d.getMonth() + 1}`;
      labelsEl.innerHTML = `
        <span>${formatDate(firstDate)}</span>
        <span>Last 30 days</span>
        <span>${formatDate(lastDate)}</span>
      `;
    }

    // Update current value (latest)
    if (currentEl) {
      currentEl.textContent = data[data.length - 1].value;
    }
  }

  /**
   * Render to all chart locations
   * @param {Array} data - Array of {date, value} objects
   */
  function renderChart(data) {
    renderChartTo(data, '');      // Healthcare tab
    renderChartTo(data, 'home');  // Home tab
  }

  /**
   * Update chart with new data
   * @param {Array} data - Array of {date, value} objects
   */
  function updateHealthIndex(data) {
    renderChart(data);
  }

  /**
   * Fetch welfare index from API
   */
  async function fetchWelfareIndex() {
    try {
      const response = await fetch('https://api-dashboard-production-fc05.up.railway.app/welfare/index?days=30');
      if (!response.ok) throw new Error('API error');
      
      const data = await response.json();
      
      // Transform API response to chart format
      const chartData = [];
      
      // Add history (oldest first)
      if (data.history && data.history.length > 0) {
        const sortedHistory = [...data.history].sort((a, b) => 
          new Date(a.date) - new Date(b.date)
        );
        sortedHistory.forEach(item => {
          chartData.push({
            date: new Date(item.date),
            value: item.score
          });
        });
      }
      
      // Add current day
      if (data.current) {
        chartData.push({
          date: new Date(data.current.date),
          value: data.current.score
        });
      }
      
      return chartData;
    } catch (error) {
      console.error('Error fetching welfare index:', error);
      return null;
    }
  }

  /**
   * Demo function (fallback)
   */
  function demo() {
    const demoData = generateDemoData();
    renderChart(demoData);
  }

  /**
   * Initialize - try API first, fallback to demo
   */
  async function init() {
    const apiData = await fetchWelfareIndex();
    if (apiData && apiData.length > 0) {
      renderChart(apiData);
    } else {
      demo();
    }
  }

  // Initialize on DOM ready
  document.addEventListener('DOMContentLoaded', () => {
    setTimeout(init, 300);
  });

  // Expose API globally
  window.healthIndex = {
    update: updateHealthIndex,
    refresh: init,
    demo: demo,
    generateDemoData: generateDemoData
  };
})();
