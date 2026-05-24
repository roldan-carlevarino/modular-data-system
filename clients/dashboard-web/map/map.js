/* Knowledge Map (tab14) — Obsidian-style Cytoscape graph */
(() => {
  const API = "https://api-dashboard-production-fc05.up.railway.app";

  // Obsidian-ish palette: soft, slightly desaturated, distinct hues
  const TYPE_COLORS = {
    project:    "#e8b86c", // warm amber
    collection: "#7aa2f7", // soft blue
    item:       "#bb9af7", // lavender
    attachment: "#9ece6a", // muted green
    concept:    "#f7768e", // soft pink/red
    block:      "#7dcfff", // cyan
  };
  const TYPE_LABELS = {
    project:    "Project",
    collection: "Collection",
    item:       "Library item",
    attachment: "Attachment",
    concept:    "Concept",
    block:      "Block",
  };

  const state = {
    loaded:     false,
    cy:         null,
    raw:        null,
    typeFilter: "all",
    layout:     "fcose",
    showLabels: false,
  };

  const $ = (id) => document.getElementById(id);

  // Register fcose extension if available
  if (typeof cytoscape !== "undefined" && typeof window.cytoscapeFcose !== "undefined") {
    try { cytoscape.use(window.cytoscapeFcose); } catch (_) {}
  }

  function setStatus(msg, isError) {
    const el = $("mapStatus");
    if (!el) return;
    el.textContent = msg || "";
    el.style.color = isError ? "#f7768e" : "";
  }

  function buildLegend() {
    const el = $("mapLegend");
    if (!el) return;
    el.innerHTML = Object.keys(TYPE_COLORS).map((k) => `
      <span class="map-legend__item" style="color:${TYPE_COLORS[k]}">
        <span class="map-legend__dot" style="background:${TYPE_COLORS[k]}"></span>
        <span style="color:#9ca3af">${TYPE_LABELS[k]}</span>
      </span>
    `).join("");
  }

  function filterElements(graph) {
    const f = state.typeFilter;
    const keepNode = (n) => {
      if (f === "all") return true;
      if (f === "projects")  return n.type === "project";
      if (f === "no-blocks") return n.type !== "block";
      if (f === "no-items")  return n.type !== "item";
      return true;
    };
    const nodes = graph.nodes.filter(keepNode);
    const ids = new Set(nodes.map((n) => n.id));
    const edges = graph.edges.filter((e) => ids.has(e.source) && ids.has(e.target));
    return { nodes, edges };
  }

  function toCyElements({ nodes, edges }) {
    // Compute degree so we can size by connectivity (Obsidian style)
    const degree = new Map();
    edges.forEach((e) => {
      degree.set(e.source, (degree.get(e.source) || 0) + 1);
      degree.set(e.target, (degree.get(e.target) || 0) + 1);
    });
    const cyNodes = nodes.map((n) => ({
      data: {
        id: n.id,
        label: n.label || n.id,
        type: n.type,
        deg: degree.get(n.id) || 0,
        meta: n,
      },
    }));
    const cyEdges = edges.map((e, i) => ({
      data: {
        id: `e${i}`,
        source: e.source,
        target: e.target,
        kind: e.kind,
      },
    }));
    return [...cyNodes, ...cyEdges];
  }

  // Node radius scales with degree (Obsidian-like)
  function nodeSize(ele) {
    const deg = ele.data("deg") || 0;
    const base = ele.data("type") === "project" ? 10 : 6;
    return base + Math.min(18, Math.sqrt(deg) * 3);
  }

  function buildStyle() {
    return [
      {
        selector: "core",
        style: { "active-bg-opacity": 0 },
      },
      {
        selector: "node",
        style: {
          "background-color": (ele) => TYPE_COLORS[ele.data("type")] || "#888",
          "background-opacity": 0.95,
          "width":  nodeSize,
          "height": nodeSize,
          "border-width": 0,
          // Glow via shadow
          "shadow-blur":    14,
          "shadow-color":   (ele) => TYPE_COLORS[ele.data("type")] || "#888",
          "shadow-opacity": 0.55,
          "shadow-offset-x": 0,
          "shadow-offset-y": 0,
          // Label
          "label": "data(label)",
          "color": "#d4d4d8",
          "font-size": 8,
          "font-weight": 400,
          "text-valign": "bottom",
          "text-halign": "center",
          "text-margin-y": 5,
          "min-zoomed-font-size": 7,
          "text-opacity": 0, // hidden by default; revealed on zoom/hover
          "text-background-color": "#0c0c10",
          "text-background-opacity": 0.7,
          "text-background-padding": 2,
          "text-background-shape": "roundrectangle",
          "transition-property": "shadow-blur, shadow-opacity, background-opacity, text-opacity",
          "transition-duration": "150ms",
        },
      },
      // Show labels for high-degree nodes always
      {
        selector: "node[deg >= 4]",
        style: { "text-opacity": 0.85 },
      },
      // Show labels when zoomed in (handled in JS via cy.zoom listener)
      {
        selector: "node.show-label",
        style: { "text-opacity": 1 },
      },
      {
        selector: "node:active, node.hover",
        style: {
          "shadow-blur": 24,
          "shadow-opacity": 0.9,
          "text-opacity": 1,
          "z-index": 999,
        },
      },
      {
        selector: "node:selected",
        style: {
          "border-width": 2,
          "border-color": "#ffffff",
          "border-opacity": 0.85,
          "shadow-blur": 28,
          "shadow-opacity": 1,
          "text-opacity": 1,
        },
      },
      {
        selector: "edge",
        style: {
          "width": 0.7,
          "line-color": "#4b5269",
          "line-opacity": 0.55,
          "curve-style": "straight",
          "target-arrow-shape": "none",
          "transition-property": "line-color, width, line-opacity",
          "transition-duration": "150ms",
        },
      },
      {
        selector: "edge.highlight",
        style: {
          "line-color": "#e5e7eb",
          "line-opacity": 0.9,
          "width": 1.4,
          "z-index": 998,
        },
      },
      {
        selector: ".faded",
        style: {
          "opacity": 0.08,
          "text-opacity": 0,
        },
      },
    ];
  }

  function getLayoutOpts() {
    const name = state.layout;
    const common = { animate: true, animationDuration: 600, fit: true, padding: 40 };

    if (name === "fcose") {
      return {
        name: "fcose",
        quality: "default",
        randomize: true,
        animate: true,
        animationDuration: 800,
        fit: true,
        padding: 50,
        nodeRepulsion: 8000,
        idealEdgeLength: 70,
        edgeElasticity: 0.45,
        gravity: 0.25,
        gravityRange: 3.8,
        numIter: 2500,
        tile: true,
        nodeSeparation: 80,
      };
    }
    if (name === "cose") {
      return {
        name: "cose",
        ...common,
        nodeRepulsion: 10000,
        idealEdgeLength: 90,
        gravity: 0.2,
        numIter: 1500,
      };
    }
    if (name === "concentric") {
      return {
        name: "concentric",
        ...common,
        concentric: (n) => (n.data("type") === "project" ? 10 : n.data("type") === "concept" ? 5 : 1),
        levelWidth: () => 1,
        minNodeSpacing: 30,
      };
    }
    return { name, ...common };
  }

  function showInfo(node) {
    const el = $("mapInfo");
    if (!el) return;
    const m = node.data("meta") || {};
    const t = node.data("type");
    const color = TYPE_COLORS[t] || "#888";
    const rows = [];
    if (m.status)       rows.push(`<div class="map-info__row">Status: ${m.status}</div>`);
    if (m.project_type) rows.push(`<div class="map-info__row">Type: ${m.project_type}</div>`);
    if (m.subtype)      rows.push(`<div class="map-info__row">Subtype: ${m.subtype}</div>`);
    if (m.project_name) rows.push(`<div class="map-info__row">Project: ${m.project_name}</div>`);
    if (m.concept_name) rows.push(`<div class="map-info__row">Concept: ${m.concept_name}</div>`);
    rows.push(`<div class="map-info__row">Connections: ${node.degree()}</div>`);
    el.innerHTML = `
      <button class="map-info__close" id="mapInfoClose" type="button" aria-label="Close">×</button>
      <div class="map-info__title">
        <span>${escapeHtml(node.data("label"))}</span>
      </div>
      <span class="map-info__type" style="background:${color}22;color:${color};border:1px solid ${color}44">${TYPE_LABELS[t]||t}</span>
      <div style="margin-top:0.55rem">${rows.join("")}</div>
    `;
    el.style.display = "block";
    const c = $("mapInfoClose");
    if (c) c.onclick = () => { el.style.display = "none"; clearHighlight(); };
  }

  function escapeHtml(s) {
    return String(s || "").replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }

  function highlightNeighborhood(node) {
    const cy = state.cy;
    if (!cy) return;
    cy.batch(() => {
      cy.elements().addClass("faded");
      const nb = node.closedNeighborhood();
      nb.removeClass("faded");
      nb.edges().addClass("highlight");
    });
  }

  function clearHighlight() {
    const cy = state.cy;
    if (!cy) return;
    cy.batch(() => {
      cy.elements().removeClass("faded");
      cy.edges().removeClass("highlight");
    });
  }

  function applyZoomLabelToggle() {
    const cy = state.cy;
    if (!cy) return;
    const z = cy.zoom();
    if (z > 1.4) cy.nodes().addClass("show-label");
    else cy.nodes().removeClass("show-label");
  }

  function render() {
    if (!state.raw) return;
    if (typeof cytoscape === "undefined") {
      setStatus("Cytoscape failed to load.", true);
      return;
    }
    const container = $("mapCanvas");
    if (!container) return;

    const filtered = filterElements(state.raw);
    const elements = toCyElements(filtered);

    if (state.cy) {
      try { state.cy.destroy(); } catch (_) {}
      state.cy = null;
    }

    state.cy = cytoscape({
      container,
      elements,
      style: buildStyle(),
      layout: getLayoutOpts(),
      wheelSensitivity: 0.25,
      minZoom: 0.1,
      maxZoom: 4.0,
      pixelRatio: "auto",
      textureOnViewport: true,
      hideEdgesOnViewport: false,
    });

    // Interactions
    state.cy.on("tap", "node", (evt) => {
      const n = evt.target;
      showInfo(n);
      highlightNeighborhood(n);
    });
    state.cy.on("tap", (evt) => {
      if (evt.target === state.cy) {
        clearHighlight();
        const el = $("mapInfo");
        if (el) el.style.display = "none";
      }
    });
    state.cy.on("mouseover", "node", (evt) => evt.target.addClass("hover"));
    state.cy.on("mouseout",  "node", (evt) => evt.target.removeClass("hover"));
    state.cy.on("zoom", applyZoomLabelToggle);

    setStatus(`${filtered.nodes.length} nodes · ${filtered.edges.length} edges`);
  }

  async function load() {
    setStatus("Loading graph…");
    try {
      const r = await fetch(`${API}/graph`);
      if (!r.ok) {
        let msg = `${r.status} ${r.statusText}`;
        try { const j = await r.json(); if (j.detail) msg = j.detail; } catch (_) {}
        throw new Error(msg);
      }
      state.raw = await r.json();
      render();
    } catch (err) {
      console.error("[map] load failed", err);
      setStatus(`Error: ${err.message || err}`, true);
    }
  }

  function bindControls() {
    const fit = $("mapFitBtn");
    if (fit) fit.addEventListener("click", () => { if (state.cy) state.cy.animate({ fit: { padding: 40 } }, { duration: 400 }); });

    const reload = $("mapReloadBtn");
    if (reload) reload.addEventListener("click", load);

    const lay = $("mapLayoutSelect");
    if (lay) lay.addEventListener("change", () => {
      state.layout = lay.value;
      if (state.cy) state.cy.layout(getLayoutOpts()).run();
    });

    const filt = $("mapTypeFilter");
    if (filt) filt.addEventListener("change", () => {
      state.typeFilter = filt.value;
      render();
    });
  }

  function init() {
    buildLegend();
    bindControls();
    const tab = $("tab14");
    if (tab) {
      tab.addEventListener("change", () => {
        if (tab.checked && !state.loaded) {
          state.loaded = true;
          load();
        }
      });
      if (tab.checked && !state.loaded) {
        state.loaded = true;
        load();
      }
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
