/* Knowledge Map (tab14) — Cytoscape graph of projects + linked entities */
(() => {
  const API = "https://api-dashboard-production-fc05.up.railway.app";

  const TYPE_COLORS = {
    project:    "#d97706",
    collection: "#3b82f6",
    item:       "#ec4899",
    attachment: "#10b981",
    concept:    "#8b5cf6",
    block:      "#9ca3af",
  };
  const TYPE_LABELS = {
    project:    "Project",
    collection: "Library collection",
    item:       "Library item",
    attachment: "Project attachment",
    concept:    "Knowledge concept",
    block:      "Knowledge block",
  };
  const EDGE_COLORS = {
    subproject:     "#d97706",
    has_collection: "#3b82f6",
    contains:       "#ec4899",
    has_attachment: "#10b981",
    has_concept:    "#8b5cf6",
    in_concept:     "#a78bfa",
    has_block:      "#9ca3af",
  };

  const state = {
    loaded:    false,
    cy:        null,
    raw:       null,
    typeFilter:"all",
    layout:    "cose",
  };

  function $(id) { return document.getElementById(id); }

  function setStatus(msg, isError) {
    const el = $("mapStatus");
    if (!el) return;
    el.textContent = msg || "";
    el.style.color = isError ? "#ef4444" : "";
  }

  function buildLegend() {
    const el = $("mapLegend");
    if (!el) return;
    el.innerHTML = Object.keys(TYPE_COLORS).map((k) => `
      <span class="map-legend__item">
        <span class="map-legend__dot" style="background:${TYPE_COLORS[k]}"></span>
        ${TYPE_LABELS[k]}
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
    const cyNodes = nodes.map((n) => ({
      data: {
        id: n.id, label: n.label || n.id, type: n.type,
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

  function nodeSize(type) {
    if (type === "project") return 38;
    if (type === "concept") return 28;
    if (type === "collection") return 26;
    if (type === "attachment") return 18;
    if (type === "item") return 16;
    return 14; // block
  }

  function buildStyle() {
    return [
      {
        selector: "node",
        style: {
          "background-color": (ele) => TYPE_COLORS[ele.data("type")] || "#666",
          "label": "data(label)",
          "color": "#e5e7eb",
          "font-size": 9,
          "text-valign": "bottom",
          "text-halign": "center",
          "text-margin-y": 4,
          "text-outline-color": "#111827",
          "text-outline-width": 2,
          "width":  (ele) => nodeSize(ele.data("type")),
          "height": (ele) => nodeSize(ele.data("type")),
          "border-width": 1,
          "border-color": "#0b1220",
        },
      },
      {
        selector: "node:selected",
        style: {
          "border-width": 3,
          "border-color": "#fbbf24",
        },
      },
      {
        selector: "edge",
        style: {
          "width": 1.2,
          "line-color": (ele) => EDGE_COLORS[ele.data("kind")] || "#4b5563",
          "target-arrow-color": (ele) => EDGE_COLORS[ele.data("kind")] || "#4b5563",
          "target-arrow-shape": "triangle",
          "arrow-scale": 0.7,
          "curve-style": "bezier",
          "opacity": 0.55,
        },
      },
      {
        selector: "edge:selected",
        style: { "opacity": 1, "width": 2 },
      },
      {
        selector: "node.faded",
        style: { "opacity": 0.15 },
      },
      {
        selector: "edge.faded",
        style: { "opacity": 0.05 },
      },
    ];
  }

  function getLayoutOpts() {
    const name = state.layout;
    const common = { animate: false, fit: true, padding: 30 };
    if (name === "cose") {
      return {
        name: "cose",
        ...common,
        animate: false,
        nodeRepulsion: 8000,
        idealEdgeLength: 90,
        gravity: 0.25,
        numIter: 1000,
      };
    }
    if (name === "concentric") {
      return {
        name: "concentric",
        ...common,
        concentric: (n) => (n.data("type") === "project" ? 10 : n.data("type") === "concept" ? 5 : 1),
        levelWidth: () => 1,
      };
    }
    return { name, ...common };
  }

  function showInfo(node) {
    const el = $("mapInfo");
    if (!el) return;
    const m = node.data("meta") || {};
    const t = node.data("type");
    const rows = [];
    if (m.status)       rows.push(`<div class="map-info__row">Status: ${m.status}</div>`);
    if (m.project_type) rows.push(`<div class="map-info__row">Type: ${m.project_type}</div>`);
    if (m.subtype)      rows.push(`<div class="map-info__row">Subtype: ${m.subtype}</div>`);
    if (m.project_name) rows.push(`<div class="map-info__row">Project: ${m.project_name}</div>`);
    if (m.concept_name) rows.push(`<div class="map-info__row">Concept: ${m.concept_name}</div>`);
    if (m.db_id != null) rows.push(`<div class="map-info__row">ID: ${m.db_id}</div>`);
    const deg = node.degree();
    rows.push(`<div class="map-info__row">Connections: ${deg}</div>`);
    el.innerHTML = `
      <button class="map-info__close" id="mapInfoClose" type="button">×</button>
      <div class="map-info__title">
        <span>${node.data("label")}</span>
        <span class="map-info__type" style="background:${TYPE_COLORS[t]}33;color:${TYPE_COLORS[t]}">${TYPE_LABELS[t]||t}</span>
      </div>
      ${rows.join("")}
    `;
    el.style.display = "block";
    const c = $("mapInfoClose");
    if (c) c.onclick = () => { el.style.display = "none"; };
  }

  function highlightNeighborhood(node) {
    const cy = state.cy;
    if (!cy) return;
    cy.elements().addClass("faded");
    const nb = node.closedNeighborhood();
    nb.removeClass("faded");
  }

  function clearHighlight() {
    const cy = state.cy;
    if (cy) cy.elements().removeClass("faded");
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
      wheelSensitivity: 0.2,
      minZoom: 0.1,
      maxZoom: 3.0,
    });

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
    if (fit) fit.addEventListener("click", () => { if (state.cy) state.cy.fit(undefined, 30); });

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
