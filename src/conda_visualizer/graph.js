import cytoscape from "cytoscape";
import dagre from "cytoscape-dagre";
import elk from "cytoscape-elk";
import fcose from "cytoscape-fcose";
import svg from "cytoscape-svg";

// Registering an extension twice throws, and the module may be evaluated
// again when the component is re-rendered in --dev mode.
for (const ext of [dagre, elk, fcose, svg]) {
  try { cytoscape.use(ext) } catch (e) {}
}

// Anaconda 2026 brand palette (see theme.py). Yellow is deliberately unused:
// the brand reserves it for under 5% of a layout and never for UI.
const PALETTES = {
  light: {
    bg: "#FFFFFF", node: "#F7F8F9", text: "#0C0C0C", muted: "#6A6D74", edge: "#C9CCD5", edge_line: "#A6ABB7",
    conda: "#594AC8", pypi: "#898D96", virtual: "#A6ABB7", missing: "#D00100", node_kind: "#594AC8",
    depends: "#068F35", whoneeds: "#6D5BF6", selected: "#1F1945", match: "#08CA4A",
    panel: "rgba(255,255,255,0.96)", link: "#594AC8",
    up_bg: "#DEFFAA", up: "#068F35", down_bg: "#CCC6FF", down: "#443998",
    add_bg: "#9CF215", add: "#056B29", rebuild_bg: "#E4E6EB", rebuild: "#6A6D74", remove: "#898D96",
    legend_bg: "#FFFFFF",
  },
  dark: {
    bg: "#1F1945", node: "#2E2667", text: "#FFFFFF", muted: "#B8BDC8", edge: "#594AC8", edge_line: "#594AC8",
    conda: "#A296FE", pypi: "#A6ABB7", virtual: "#898D96", missing: "#F2504F", node_kind: "#A296FE",
    depends: "#08CA4A", whoneeds: "#A296FE", selected: "#FFFFFF", match: "#9CF215",
    panel: "rgba(46,38,103,0.96)", link: "#A296FE",
    up_bg: "#035420", up: "#5FE419", down_bg: "#443998", down: "#B1A6FF",
    add_bg: "#056B29", add: "#9CF215", rebuild_bg: "#4B4D52", rebuild: "#B8BDC8", remove: "#898D96",
    legend_bg: "#2E2667",
  },
};

const LAYOUTS = ["auto", "dagre", "elk", "fcose", "breadthfirst", "cose", "concentric", "circle", "grid"];
const RANKED = new Set(["dagre", "elk"]);
const HIGHLIGHTS = {
  both: "Up & downstream", depends: "Dependencies", whoneeds: "Dependents",
  neighbors: "Neighbors",
};
const DIRECTIONS = { TB: "DOWN", LR: "RIGHT", BT: "UP", RL: "LEFT" };

const FAMILY = 'Inter, Arial, sans-serif';
const FONT_SIZE = 12;
const FONT = `${FONT_SIZE}px ${FAMILY}`;
const measure = document.createElement("canvas").getContext("2d");
measure.font = FONT;

// Labels drop out below FONT_SIZE * zoom = MIN_FONT, so the overview style takes over
// at exactly that zoom and nodes never render as empty boxes.
const MIN_FONT = 6;
const OVERVIEW_ZOOM = MIN_FONT / FONT_SIZE;
const FIT_PADDING = 40;
// Fitting a handful of nodes would otherwise zoom them to several times their size.
const MAX_FIT_ZOOM = 1.25;
// Below this fitted zoom a hierarchical layout is too spread out to read, so auto uses fcose.
const AUTO_MIN_ZOOM = 0.35;
// Leaves, requested packages and this many of the most-needed packages keep a label in the overview.
const LANDMARK_HUBS = 3;

// Second label line: what a what-if plan does to the version, else the newest one.
function versionText(d, showLatest) {
  if (d.change === "add") return `+ ${d.version}`;
  if (d.change === "remove") return `${d.version} removed`;
  if (d.change === "rebuild") return `${d.version} rebuilt`;
  if (d.change) return `${d.old_version} → ${d.version}`;
  if (showLatest && d.outdated && d.latest) return `${d.version} ↑ ${d.latest}`;
  return d.version || "";
}

function nodeWidth(label, version) {
  const widest = Math.max(measure.measureText(label).width, version ? measure.measureText(version).width : 0);
  return Math.ceil(widest) + 22;
}

function toElements(elements, showVersions, showLatest) {
  const raw = elements.nodes || [];
  const hubs = new Set(raw.filter((n) => n.in > 0).sort((a, b) => b.in - a.in)
    .slice(0, LANDMARK_HUBS).map((n) => n.id));
  const nodes = raw.map((n) => {
    const label = n.label || n.id;
    const version = showVersions ? versionText(n, showLatest) : "";
    return {
      group: "nodes",
      data: {
        ...n,
        name: label,
        caption: version ? `${label}\n${version}` : label,
        w: nodeWidth(label, version),
        h: version ? 36 : 24,
        leaf: n.in === 0 ? 1 : 0,
        landmark: n.in === 0 || n.requested || hubs.has(n.id) ? 1 : 0,
      },
    };
  });
  const ids = new Set(nodes.map((n) => n.data.id));
  const edges = (elements.edges || [])
    .filter((e) => ids.has(e.source) && ids.has(e.target))
    .map((e) => ({ group: "edges", data: { id: e.id || `${e.source}->${e.target}`, ...e } }));
  return [...nodes, ...edges];
}

// Overview sizes are in screen pixels: ``view.inv`` is 1 / zoom, refreshed while zooming.
function overviewSize(n, view) {
  const px = n.data("leaf") ? 11 : Math.min(7 + 2.5 * Math.sqrt(n.data("in") || 0), 30);
  return px * view.inv;
}

function stylesheet(p, view) {
  const px = (value) => () => value * view.inv;
  return [
    { selector: "node", style: {
      shape: "round-rectangle", width: "data(w)", height: "data(h)",
      "background-color": p.node, "border-width": 1.5, "border-color": p.conda,
      label: "data(caption)", "text-wrap": "wrap", "text-valign": "center", "text-halign": "center",
      "font-family": FAMILY, "font-size": FONT_SIZE, color: p.text,
      "min-zoomed-font-size": MIN_FONT,
    }},
    { selector: 'node[kind = "pypi"]', style: { "border-color": p.pypi } },
    { selector: 'node[kind = "node"]', style: { "border-color": p.node_kind } },
    { selector: 'node[kind = "virtual"]', style: { "border-style": "dashed", "border-color": p.virtual, color: p.muted } },
    { selector: 'node[kind = "missing"]', style: { "border-style": "dashed", "border-color": p.missing, color: p.missing } },
    { selector: "node[?requested]", style: { "border-width": 3, "font-weight": "bold" } },
    { selector: "node[leaf = 1]", style: { "background-color": p.bg } },
    // A newer version exists: double border (Cytoscape needs a width of 3 or more).
    { selector: "node[?outdated]", style: { "border-style": "double", "border-width": 4 } },
    { selector: 'node[change = "rebuild"]', style: { "background-color": p.rebuild_bg, "border-color": p.rebuild } },
    { selector: 'node[change = "upgrade"]', style: { "background-color": p.up_bg, "border-color": p.up, "border-width": 3 } },
    { selector: 'node[change = "downgrade"]', style: { "background-color": p.down_bg, "border-color": p.down, "border-width": 3 } },
    { selector: 'node[change = "add"]', style: { "background-color": p.add_bg, "border-color": p.add, "border-width": 3 } },
    { selector: 'node[change = "remove"]', style: { "border-style": "dashed", "border-color": p.remove, color: p.muted, "background-opacity": 0.4 } },
    { selector: "node[?target]", style: { "border-width": 5, "font-weight": "bold" } },
    // Zoomed out too far for labels: dots sized by dependents, leaves hollow,
    // and only landmark packages labelled.
    { selector: "node.overview", style: {
      shape: "ellipse", width: (n) => overviewSize(n, view), height: (n) => overviewSize(n, view),
      label: "", "border-width": px(1.5), "border-style": "solid",
    }},
    { selector: "node.overview[!change]", style: {
      "background-color": p.conda, "background-opacity": 0.45, "border-color": p.conda,
    }},
    { selector: 'node.overview[kind = "pypi"][!change]', style: { "background-color": p.pypi, "border-color": p.pypi } },
    { selector: "node.overview[leaf = 1][!change]", style: {
      "background-color": p.bg, "background-opacity": 1, "border-width": px(3),
    }},
    { selector: "node.overview[landmark = 1], node.overview.focus, node.overview.match", style: {
      label: "data(name)", "text-valign": "bottom", "text-margin-y": px(3), "font-weight": 600,
      "font-size": px(FONT_SIZE), "min-zoomed-font-size": 0,
      "text-outline-color": p.bg, "text-outline-width": px(2.5),
    }},
    { selector: "edge", style: {
      width: 1, "line-color": p.edge_line, "target-arrow-color": p.edge_line, "target-arrow-shape": "triangle",
      "arrow-scale": 0.7, "curve-style": "straight", opacity: 0.8,
    }},
    { selector: "edge.overview", style: {
      width: px(1), "line-color": p.conda, opacity: 0.3,
      "target-arrow-shape": "none", "curve-style": "haystack",
    }},
    { selector: ".faded", style: { opacity: 0.12 } },
    { selector: "edge.faded", style: { opacity: 0.05 } },
    { selector: ".hidden", style: { display: "none" } },
    { selector: "node.depends", style: { "border-color": p.depends, "border-width": 2, "background-color": p.bg } },
    { selector: "node.whoneeds", style: { "border-color": p.whoneeds, "border-width": 2, "background-color": p.bg } },
    { selector: "edge.depends", style: { "line-color": p.depends, "target-arrow-color": p.depends, width: 1.8, opacity: 1 } },
    { selector: "edge.whoneeds", style: { "line-color": p.whoneeds, "target-arrow-color": p.whoneeds, width: 1.8, opacity: 1 } },
    { selector: "node.overview.depends", style: {
      "background-color": p.depends, "background-opacity": 0.85, "border-width": px(1.5),
    }},
    { selector: "node.overview.whoneeds", style: {
      "background-color": p.whoneeds, "background-opacity": 0.85, "border-width": px(1.5),
    }},
    { selector: "edge.overview.depends, edge.overview.whoneeds", style: { width: px(2), opacity: 0.9 } },
    { selector: "node.match", style: {
      "border-color": p.match, "border-width": 3, "background-color": p.match,
      "background-opacity": 0.18, opacity: 1, "z-index": 9,
    }},
    { selector: "node.focus", style: {
      "border-color": p.selected, "border-width": 2, "background-color": p.selected,
      "background-opacity": 1, color: p.bg, opacity: 1, "z-index": 10,
      "underlay-color": p.whoneeds, "underlay-opacity": 0.35, "underlay-padding": 5,
      "underlay-shape": "round-rectangle",
    }},
    { selector: "node.overview.match", style: { "border-width": px(3), "background-opacity": 0.6 } },
    // The label sits below the dot in the overview, on the canvas background.
    { selector: "node.overview.focus", style: {
      width: px(18), height: px(18), color: p.text, "border-width": px(2),
      "underlay-padding": px(5), "underlay-shape": "ellipse",
    }},
    { selector: "node:selected", style: { "overlay-opacity": 0 } },
  ];
}

// Fitting is done afterwards by ``fitView`` so the zoom can be capped.
function layoutOptions(name, rankDir, eles) {
  const common = { animate: false, fit: false, padding: FIT_PADDING };
  switch (name) {
    case "dagre":
      return { ...common, name: "dagre", rankDir, nodeSep: 14, rankSep: 70, edgeSep: 6 };
    case "elk":
      return { ...common, name: "elk", elk: {
        algorithm: "layered", "elk.direction": DIRECTIONS[rankDir],
        "elk.layered.spacing.nodeNodeBetweenLayers": 60, "elk.spacing.nodeNode": 14,
        "elk.layered.nodePlacement.strategy": "NETWORK_SIMPLEX",
      }};
    case "fcose":
      return { ...common, name: "fcose", quality: eles.nodes().length > 800 ? "draft" : "default",
               randomize: true, nodeRepulsion: 8000, idealEdgeLength: 90,
               nodeDimensionsIncludeLabels: true };
    case "breadthfirst":
      return { ...common, name: "breadthfirst", directed: true, spacingFactor: 1.1,
               roots: eles.nodes().filter((n) => n.indegree(false) === 0) };
    case "concentric":
      return { ...common, name: "concentric", concentric: (n) => n.indegree(false),
               levelWidth: () => 4, minNodeSpacing: 12 };
    default:
      return { ...common, name };
  }
}

// Zoom at which ``eles`` would fit a ``width`` x ``height`` canvas.
function fitZoom(eles, width, height) {
  const bb = eles.boundingBox();
  return Math.min((width - 2 * FIT_PADDING) / (bb.w || 1), (height - 2 * FIT_PADDING) / (bb.h || 1));
}

// Dagre in the orientation that fits the canvas at the higher zoom. When that is too far
// out for labels, fcose is tried too and the larger fit wins; below AUTO_MIN_ZOOM, as for
// most whole environments, fcose is used outright. ``positioned`` means the chosen layout
// was the last one run, so its positions are already in place.
function autoOptions(eles, width, height) {
  let best = null;
  let last = null;
  const attempt = (opts) => {
    eles.layout(opts).run();
    last = opts;
    const zoom = fitZoom(eles, width, height);
    if (!best || zoom > best.zoom) best = { opts, zoom };
  };
  attempt(layoutOptions("dagre", "TB", eles));
  attempt(layoutOptions("dagre", "LR", eles));
  if (best.zoom < AUTO_MIN_ZOOM) return { opts: layoutOptions("fcose", null, eles), positioned: false };
  if (best.zoom < OVERVIEW_ZOOM) attempt(layoutOptions("fcose", null, eles));
  return { opts: best.opts, positioned: best.opts === last };
}

function download(href, filename) {
  const a = document.createElement("a");
  a.href = href;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
}

const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

function h(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  Object.assign(node, props);
  node.append(...children);
  return node;
}

function select(options, value, title) {
  const s = h("select", { title });
  for (const [v, label] of options) s.append(h("option", { value: v, textContent: label }));
  s.value = value;
  return s;
}

export function render({ model, el }) {
  const container = h("div", { className: "cy-container" });
  // Cytoscape checks the inline position before the shadow stylesheet applies.
  container.style.position = "absolute";
  const tooltip = h("div", { className: "cy-tooltip" });
  const empty = h("div", { className: "cy-empty", textContent: "No packages to display" });
  const card = h("div", { className: "cy-card" });

  // Toolbar: everything here works without a Python server, so a saved
  // HTML file keeps search, layout switching, isolation and image export.
  const search = h("input", { type: "search", placeholder: "Search packages…", className: "cy-search" });
  search.setAttribute("list", "cy-names");
  const names = h("datalist", { id: "cy-names" });
  const count = h("span", { className: "cy-count" });
  const layoutSel = select(LAYOUTS.map((l) => [l, l]), model.layout, "Layout");
  const rankSel = select(Object.keys(DIRECTIONS).map((d) => [d, d]), model.rank_dir, "Direction");
  const hlSel = select(Object.entries(HIGHLIGHTS), model.highlight, "Highlight on select");
  const button = (label, title) => h("button", { textContent: label, title, type: "button" });
  const isolateBtn = button("Isolate", "Show only the highlighted packages");
  const fitBtn = button("Fit", "Fit graph to view");
  const pngBtn = button("PNG", "Download PNG");
  const svgBtn = button("SVG", "Download SVG");
  const toolbar = h("div", { className: "cy-toolbar" },
    h("div", { className: "cy-search-wrap" }, search, count), names,
    layoutSel, rankSel, hlSel, isolateBtn, fitBtn, pngBtn, svgBtn);

  const legend = h("div", { className: "cy-legend" });
  const zoomIn = h("button", { type: "button", textContent: "+", title: "Zoom in" });
  const zoomOut = h("button", { type: "button", textContent: "−", title: "Zoom out" });
  const zoomBox = h("div", { className: "cy-zoom" }, zoomIn, zoomOut);
  el.append(container, toolbar, card, tooltip, empty, legend, zoomBox);

  const palette = () => PALETTES[model.dark_theme ? "dark" : "light"];
  const view = { inv: 1 };

  const cy = cytoscape({
    container,
    style: stylesheet(palette(), view),
    minZoom: 0.02,
    maxZoom: 4,
    boxSelectionEnabled: false,
    selectionType: "single",
  });
  // Exposed for Playwright tests and console debugging.
  container._cy = cy;

  // Cytoscape caches the container's page position and only refreshes it on window
  // scroll or resize. Scrolling an ancestor, such as a page's main area, or content
  // shifting above the graph would otherwise offset every pointer position.
  const invalidateCoords = () => cy.renderer().invalidateContainerClientCoordsCache();
  // Cytoscape also hit-tests on pointerover, which precedes pointermove on entry.
  for (const type of ["pointerover", "pointerdown", "pointermove", "wheel"]) {
    container.addEventListener(type, invalidateCoords, { capture: true, passive: true });
  }

  // True once the viewport is user-controlled or pinned to a selection; stops auto-fitting.
  let touched = false;
  let replacing = false;
  let matches = [];
  let matchIndex = -1;
  let overview = false;
  let zoomPending = false;
  // Canvas size the last auto layout was chosen for.
  let layoutSize = null;

  function applyTheme() {
    const p = palette();
    for (const [k, v] of Object.entries({ bg: p.bg, text: p.text, muted: p.muted, panel: p.panel, border: p.edge, accent: p.selected, link: p.link })) {
      el.style.setProperty(`--cy-${k}`, v);
    }
    cy.style(stylesheet(p, view));
    renderLegend();
  }

  function applyChrome() {
    toolbar.style.display = model.toolbar ? "flex" : "none";
    container.style.top = model.toolbar ? "42px" : "0";
    // The legend takes the bottom-left corner, so the card goes top-left under the toolbar.
    card.style.top = model.toolbar ? "52px" : "10px";
    if (!model.show_details) card.style.display = "none";
  }

  function applyLayoutControls() {
    layoutSel.value = model.layout;
    rankSel.value = model.rank_dir;
    rankSel.disabled = !RANKED.has(model.layout);
    rankSel.title = model.layout === "auto" ? "Direction (chosen automatically)" : "Direction";
  }

  function setOverview(on) {
    el.dataset.overview = on ? "1" : "0";
    if (on === overview) return;
    overview = on;
    cy.batch(() => cy.elements().toggleClass("overview", on));
    renderLegend();
  }

  // Switches between the overview and the labelled style, and keeps the overview's
  // screen-pixel sizes constant while zooming.
  function updateZoomStyle() {
    zoomPending = false;
    view.inv = 1 / cy.zoom();
    const on = cy.zoom() < OVERVIEW_ZOOM;
    if (on !== overview) setOverview(on);
    else if (on) cy.elements().updateStyle();
    el.dataset.overview = on ? "1" : "0";
  }

  cy.on("zoom", () => {
    if (zoomPending) return;
    zoomPending = true;
    requestAnimationFrame(updateZoomStyle);
  });

  const visible = () => cy.elements().not(".hidden");

  function fitView(eles = visible()) {
    if (!eles.length) return;
    cy.fit(eles, FIT_PADDING);
    if (cy.zoom() > MAX_FIT_ZOOM) {
      cy.zoom(MAX_FIT_ZOOM);
      cy.center(eles);
    }
    updateZoomStyle();
  }

  function runLayout(fit = true) {
    const eles = visible();
    if (!eles.nodes().length) return;
    const t0 = performance.now();
    // Layouts read node sizes from the style, which must be the labelled one.
    setOverview(false);
    let opts;
    let positioned = false;
    if (model.layout === "auto") {
      const width = container.clientWidth || 800;
      const height = container.clientHeight || 600;
      layoutSize = [width, height];
      ({ opts, positioned } = autoOptions(eles, width, height));
    } else {
      opts = layoutOptions(model.layout, model.rank_dir, eles);
    }
    el.dataset.layout = opts.name === "dagre" ? `dagre-${opts.rankDir}` : opts.name;
    const done = () => {
      if (fit) fitView(eles);
      else updateZoomStyle();
    };
    if (positioned) {
      done();
    } else {
      const layout = eles.layout(opts);
      layout.one("layoutstop", done);
      layout.run();
    }
    el.dataset.layoutMs = Math.round(performance.now() - t0);
  }

  function relatives(node) {
    const mode = model.highlight;
    const out = { depends: cy.collection(), whoneeds: cy.collection() };
    if (mode === "neighbors") {
      out.depends = node.outgoers();
      out.whoneeds = node.incomers();
    } else {
      if (mode === "both" || mode === "depends") out.depends = node.successors();
      if (mode === "both" || mode === "whoneeds") out.whoneeds = node.predecessors();
    }
    return out;
  }

  function renderCard(node) {
    if (!model.show_details || !node) {
      card.style.display = "none";
      return;
    }
    const d = node.data();
    const list = (eles, label) => {
      const ids = eles.map((n) => n.id()).sort();
      if (!ids.length) return "";
      const shown = ids.slice(0, 40).map((id) => `<a data-id="${esc(id)}">${esc(id)}</a>`).join(", ");
      return `<div class="cy-card-list"><span>${label} (${ids.length})</span>${shown}${ids.length > 40 ? " …" : ""}</div>`;
    };
    const meta = [d.version, d.build, d.channel].filter(Boolean).map(esc).join(" · ");
    const notes = [
      d.change && `${esc(d.change)}${d.old_version && d.old_version !== d.version ? `: ${esc(d.old_version)} → ${esc(d.version)}` : ""}`,
      d.outdated && d.latest && `newest available: ${esc(d.latest)}`,
    ].filter(Boolean).join(" · ");
    card.innerHTML = `<div class="cy-tt-title">${esc(d.id)}${d.requested ? ' <span class="cy-tt-tag">requested</span>' : ""}</div>` +
      (meta ? `<div class="cy-card-meta">${meta}</div>` : "") +
      (notes ? `<div class="cy-card-meta cy-card-note">${notes}</div>` : "") +
      list(node.outgoers().nodes(), "Depends on") + list(node.incomers().nodes(), "Needed by");
    card.style.display = "block";
  }

  card.addEventListener("click", (evt) => {
    const id = evt.target?.dataset?.id;
    if (id) model.selected = id;
  });

  function highlight(center = false) {
    const node = model.selected ? cy.getElementById(model.selected) : null;
    const valid = node && !node.empty();
    cy.batch(() => {
      cy.elements().removeClass("faded depends whoneeds focus");
      if (!valid) {
        cy.$(":selected").unselect();
        const changed = cy.nodes("[change]");
        if (matches.length) {
          cy.elements().not(cy.collection(matches)).addClass("faded");
        } else if (changed.length) {
          cy.elements().not(changed.union(changed.connectedEdges())).addClass("faded");
        }
        return;
      }
      const rel = relatives(node);
      rel.depends.addClass("depends");
      rel.whoneeds.addClass("whoneeds");
      let keep = node.union(rel.depends).union(rel.whoneeds);
      // A previewed change stays visible while something is selected.
      const changed = cy.nodes("[change]");
      if (changed.length) keep = keep.union(changed).union(changed.edgesWith(changed));
      cy.elements().not(keep).addClass("faded");
      node.addClass("focus");
      if (!node.selected()) node.select();
    });
    renderCard(valid ? node : null);
    isolateBtn.disabled = !valid && !model.isolated;
    renderLegend();
    if (model.isolated) applyIsolation();
    if (center && valid) centerOn(node);
  }

  function centerOn(node) {
    const ext = cy.extent();
    const pos = node.position();
    const outside = pos.x < ext.x1 || pos.x > ext.x2 || pos.y < ext.y1 || pos.y > ext.y2;
    if (outside || cy.zoom() < 0.35) {
      cy.animate({ center: { eles: node }, zoom: Math.max(cy.zoom(), 0.8) }, { duration: 300 });
    }
  }

  function applyIsolation() {
    const on = model.isolated;
    isolateBtn.classList.toggle("active", on);
    isolateBtn.textContent = on ? "Show all" : "Isolate";
    cy.batch(() => {
      cy.elements().removeClass("hidden");
      if (on && model.selected) cy.elements(".faded").addClass("hidden");
    });
    runLayout();
  }

  function runSearch() {
    const q = (model.search || "").trim().toLowerCase();
    cy.nodes().removeClass("match");
    matches = [];
    matchIndex = -1;
    if (q) {
      const rank = (id) => (id === q ? 0 : id.startsWith(q) ? 1 : 2);
      matches = cy.nodes().not(".hidden").filter((n) => n.id().toLowerCase().includes(q))
        .sort((a, b) => rank(a.id().toLowerCase()) - rank(b.id().toLowerCase()) || a.id().localeCompare(b.id()))
        .toArray();
      cy.collection(matches).addClass("match");
    }
    count.textContent = q ? `${matches.length} match${matches.length === 1 ? "" : "es"}` : "";
    el.dataset.matches = matches.length;
    if (!model.selected) highlight();
  }

  function stepMatch(delta) {
    if (!matches.length) return;
    matchIndex = (matchIndex + delta + matches.length) % matches.length;
    count.textContent = `${matchIndex + 1} / ${matches.length}`;
    model.selected = matches[matchIndex].id();
  }

  // With ``keepView`` and a similar set of nodes the zoom stays and the selected package
  // is re-centered, so refreshing the data does not make the package under study jump away.
  function setElements(keepView = false) {
    const elements = toElements(model.elements, model.show_versions, model.show_latest);
    // New data starts un-isolated; the layout below already shows everything.
    if (model.isolated) {
      replacing = true;
      model.isolated = false;
      replacing = false;
    }
    isolateBtn.classList.remove("active");
    isolateBtn.textContent = "Isolate";
    const before = new Set(cy.nodes().map((n) => n.id()));
    cy.batch(() => {
      cy.elements().remove();
      cy.add(elements);
    });
    // A small edit of what was shown keeps the zoom; a different graph is fitted.
    const shared = cy.nodes().filter((n) => before.has(n.id())).length;
    const similar = shared / (before.size + cy.nodes().length - shared || 1) >= 0.8;
    names.replaceChildren(...cy.nodes().map((n) => h("option", { value: n.id() })).sort((a, b) => a.value.localeCompare(b.value)));
    empty.style.display = cy.nodes().length ? "none" : "flex";
    el.dataset.nodes = cy.nodes().length;
    el.dataset.edges = cy.edges().length;
    renderLegend();
    const anchor = keepView && model.selected ? cy.getElementById(model.selected) : null;
    const keep = similar && anchor && !anchor.empty();
    runLayout(!keep);
    if (keep) cy.center(anchor);
    touched = Boolean(keep);
    if (model.selected && cy.getElementById(model.selected).empty()) {
      model.selected = null;
    }
    runSearch();
    highlight();
  }

  // Only explains what is on screen, so a plain graph with nothing selected shows no legend.
  function renderLegend() {
    const p = palette();
    const items = [];
    const seen = new Set(cy.nodes().map((n) => n.data("change")).filter(Boolean));
    const swatch = (bg, border, style = "solid") =>
      `<i style="background:${bg};border:2px ${style} ${border}"></i>`;
    const dot = (bg, border) =>
      `<i class="dot" style="background:${bg};border:2px solid ${border}"></i>`;
    const selected = model.selected && !cy.getElementById(model.selected).empty();
    if (selected) {
      items.push(`${swatch(p.selected, p.selected)}Selected`);
      if (model.highlight !== "whoneeds") items.push(`${swatch(p.bg, p.depends)}Depends on`);
      if (model.highlight !== "depends") items.push(`${swatch(p.bg, p.whoneeds)}Needed by`);
    } else if (overview) {
      items.push(`${dot(p.bg, p.conda)}Leaf`);
      items.push(`${dot(p.conda + "73", p.conda)}Dependency, sized by dependents`);
      items.push(`<em>Zoom in for all labels</em>`);
    }
    const kinds = [
      ["upgrade", "Upgraded", swatch(p.up_bg, p.up)],
      ["downgrade", "Downgraded", swatch(p.down_bg, p.down)],
      ["add", "Added", swatch(p.add_bg, p.add)],
      ["remove", "Removed", swatch("transparent", p.remove, "dashed")],
      ["rebuild", "Rebuilt", swatch(p.rebuild_bg, p.rebuild)],
    ];
    for (const [key, label, icon] of kinds) if (seen.has(key)) items.push(`${icon}${label}`);
    if (model.show_latest && cy.nodes("[?outdated]").length) {
      items.push(`${swatch("transparent", p.conda, "double")}Newer version available (↑)`);
    }
    legend.innerHTML = items.map((i) => `<span>${i}</span>`).join("");
    legend.style.display = items.length ? "flex" : "none";
  }

  function showTooltip(evt, html) {
    tooltip.innerHTML = html;
    tooltip.style.display = "block";
    positionTooltip(evt);
  }

  function positionTooltip(evt) {
    const rect = el.getBoundingClientRect();
    const offset = container.offsetTop;
    const x = evt.renderedPosition.x;
    const y = evt.renderedPosition.y + offset;
    tooltip.style.left = `${Math.max(4, Math.min(x + 14, rect.width - tooltip.offsetWidth - 6))}px`;
    tooltip.style.top = `${Math.min(y + 14, rect.height - tooltip.offsetHeight - 6)}px`;
  }

  cy.on("mouseover", "node", (evt) => {
    const d = evt.target.data();
    const rows = [
      ["version", d.version], ["latest", d.latest], ["was", d.old_version], ["change", d.change],
      ["build", d.build], ["channel", d.channel], ["kind", d.kind],
      ["depends", d.out], ["needed by", d.in],
    ].filter(([, v]) => v !== undefined && v !== "");
    showTooltip(evt, `<div class="cy-tt-title">${esc(d.id)}${d.requested ? ' <span class="cy-tt-tag">requested</span>' : ""}</div>` +
      rows.map(([k, v]) => `<div><span>${k}</span>${esc(v)}</div>`).join(""));
  });
  cy.on("mouseover", "edge", (evt) => {
    const d = evt.target.data();
    showTooltip(evt, `<div class="cy-tt-title">${esc(d.source)} → ${esc(d.target)}</div>` +
      (d.spec ? `<div><span>spec</span>${esc(d.spec)}</div>` : ""));
  });
  cy.on("mousemove", "node, edge", (evt) => {
    if (tooltip.style.display === "block") positionTooltip(evt);
  });
  cy.on("mouseout", "node, edge", () => { tooltip.style.display = "none"; });
  // Leaving the graph in one move skips Cytoscape's mouseout, which leaves the tooltip
  // up and suppresses the next mouseover on the same element.
  container.addEventListener("pointerleave", () => {
    tooltip.style.display = "none";
    cy.renderer().hoverData.last = null;
  });
  cy.on("viewport", () => { tooltip.style.display = "none"; });

  cy.on("tap", "node", (evt) => { model.selected = evt.target.id(); });
  cy.on("tap", (evt) => { if (evt.target === cy) model.selected = null; });
  cy.on("dbltap", "node", (evt) => model.send_msg({ type: "dblclick", id: evt.target.id() }));

  search.addEventListener("input", () => { model.search = search.value; });
  search.addEventListener("keydown", (evt) => {
    if (evt.key === "Enter") {
      evt.preventDefault();
      stepMatch(evt.shiftKey ? -1 : 1);
    } else if (evt.key === "Escape") {
      model.search = "";
      model.selected = null;
    }
  });
  layoutSel.addEventListener("change", () => { model.layout = layoutSel.value; });
  rankSel.addEventListener("change", () => { model.rank_dir = rankSel.value; });
  hlSel.addEventListener("change", () => { model.highlight = hlSel.value; });
  isolateBtn.addEventListener("click", () => { model.isolated = !model.isolated; });
  model.on("isolated", () => { if (!replacing) applyIsolation(); });
  const zoomBy = (factor) => {
    touched = true;
    cy.animate({
      zoom: { level: cy.zoom() * factor, renderedPosition: { x: cy.width() / 2, y: cy.height() / 2 } },
    }, { duration: 150 });
  };
  zoomIn.addEventListener("click", () => zoomBy(1.4));
  zoomOut.addEventListener("click", () => zoomBy(1 / 1.4));

  function fit() {
    const start = { zoom: cy.zoom(), pan: { ...cy.pan() } };
    fitView();
    const target = { zoom: cy.zoom(), pan: { ...cy.pan() } };
    cy.viewport(start);
    cy.animate(target, { duration: 300 });
  }

  function exportImage(format, filename) {
    const bg = palette().bg;
    if (format === "svg") {
      const blob = new Blob([cy.svg({ full: true, bg })], { type: "image/svg+xml" });
      download(URL.createObjectURL(blob), `${filename}.svg`);
    } else {
      const blob = cy.png({ output: "blob", full: true, bg, maxWidth: 6000, maxHeight: 6000 });
      download(URL.createObjectURL(blob), `${filename}.png`);
    }
  }

  fitBtn.addEventListener("click", fit);
  pngBtn.addEventListener("click", () => exportImage("png", model.filename));
  svgBtn.addEventListener("click", () => exportImage("svg", model.filename));

  model.on("elements", () => setElements(Boolean(model.elements.keep_view)));
  model.on(["show_versions", "show_latest"], () => setElements(true));
  model.on(["layout", "rank_dir"], () => { applyLayoutControls(); runLayout(); });
  model.on("selected", () => highlight(true));
  model.on("highlight", () => { hlSel.value = model.highlight; highlight(); });
  model.on("search", () => { if (search.value !== model.search) search.value = model.search; runSearch(); });
  model.on("dark_theme", applyTheme);
  model.on(["toolbar", "show_details"], () => { applyChrome(); cy.resize(); highlight(); });

  model.on("msg:custom", (msg) => {
    if (msg.type === "fit") fit();
    else if (msg.type === "layout") runLayout();
    else if (msg.type === "export") exportImage(msg.format, msg.filename);
    else if (msg.type === "zoom") zoomBy(msg.factor);
    else if (msg.type === "download") {
      download(URL.createObjectURL(new Blob([msg.text], { type: msg.mime })), msg.filename);
    }
  });

  // Panel may size the component after render; keep fitting until the user
  // takes control of the viewport.
  cy.on("dragpan scrollzoom pinchzoom", () => { touched = true; });
  const observer = new ResizeObserver(() => {
    const width = container.clientWidth;
    const height = container.clientHeight;
    if (!width || !height) return;
    cy.resize();
    if (touched) return;
    // Auto picks its layout for the canvas shape, which is unknown until Panel sizes it.
    const reshaped = layoutSize && (Math.abs(width / layoutSize[0] - 1) > 0.25 ||
                                    Math.abs(height / layoutSize[1] - 1) > 0.25);
    if (model.layout === "auto" && reshaped) runLayout();
    else fitView();
  });
  observer.observe(container);
  model.on("remove", () => { observer.disconnect(); cy.destroy(); });

  applyTheme();
  applyChrome();
  applyLayoutControls();
  search.value = model.search || "";
  setElements();

  // Node widths are measured with Inter, so lay out again once it has loaded.
  document.fonts?.load(FONT).then((faces) => { if (faces.length) setElements(); });
}
