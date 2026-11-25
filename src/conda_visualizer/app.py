"""Panel app for exploring conda dependency graphs.

Serve with ``panel serve src/conda_visualizer/app.py`` or ``conda-visualizer serve``;
pass ``?source=<prefix|env name|file>`` to open a specific input.
"""

from __future__ import annotations

import functools
import html
import io
from pathlib import Path

import pandas as pd
import panel as pn
import panel_material_ui as pmui
import param

# Absolute imports so `panel serve app.py` can run this file as a script.
from conda_visualizer.component import CytoscapeGraph
from conda_visualizer.details_panel import DetailsPanel
from conda_visualizer.export import standalone
from conda_visualizer.graph import DependencyGraph
from conda_visualizer.sources import (
    BROWSER,
    Source,
    SourceError,
    default_source_path,
    list_environments,
    open_source,
    rattler,
)
from conda_visualizer.theme import (
    FAVICON,
    FONTS_CSS,
    LOGO,
    RAW_CSS,
    STANDALONE_CSS,
    THEME_CONFIG,
)
from conda_visualizer.updates_panel import UpdatesPanel

pn.extension(
    "tabulator",
    notifications=True,
    throttled=True,
    css_files=[FONTS_CSS],
    raw_css=[RAW_CSS],
)

#: conda-tree's ``--small`` hides these; nearly every package depends on them.
COMMON = ("python", "conda")

#: Above this many nodes the full view is replaced by the requested packages.
MAX_NODES = 1500

#: Export menu entries: label -> (icon, format).
EXPORTS = {
    "Interactive HTML": ("public", "html"),
    "PNG image": ("image", "png"),
    "SVG image": ("polyline", "svg"),
    "DOT (Graphviz)": ("account_tree", "dot"),
    "JSON": ("data_object", "json"),
}

MIME_TYPES = {"html": "text/html", "dot": "text/vnd.graphviz", "json": "application/json"}

LAYOUT_NAMES = {
    "Auto": "auto",
    "Hierarchy": "dagre",
    "Layered (ELK)": "elk",
    "Organic": "fcose",
    "Breadth-first": "breadthfirst",
    "Force-directed": "cose",
    "Concentric": "concentric",
    "Circle": "circle",
    "Grid": "grid",
}

ISOLATE_TIP = "Show only the selected package and its relatives"

#: Direction applies only to the hierarchical layouts.
RANKED = ("dagre", "elk")

SOURCE_CSS = """
.src { display: flex; flex-direction: column; min-width: 0; }
.src b {
  font: 600 15px/1.3 Inter, Arial, sans-serif; overflow: hidden; text-overflow: ellipsis;
  white-space: nowrap;
}
.src span { font-size: 13px; opacity: 0.7; }
"""


@functools.cache
def discovered_environments() -> dict[str, str]:
    return list_environments()


class Explorer(pn.viewable.Viewer):
    """Load an environment definition and browse its dependency graph."""

    source = param.String(default="", doc="Prefix, environment name or file path.")

    environment_name = param.Selector(
        default=None, objects=[], allow_None=True, label="Conda environment"
    )

    platform = param.Selector(default=None, objects=[], allow_None=True)

    environment = param.Selector(
        default=None, objects=[], allow_None=True, label="Lockfile environment"
    )

    focus = param.Selector(default=None, objects=[], allow_None=True, label="Focus package")

    direction = param.Selector(
        default="both",
        objects={"Both": "both", "Depends on": "depends", "Needed by": "whoneeds"},
        label="Show",
    )

    depth = param.Integer(default=0, bounds=(0, 10), doc="Levels around the focus; 0 for all.")

    exclude = param.ListSelector(default=[], objects=[], label="Stop expanding at")

    hide = param.ListSelector(default=[], objects=[], label="Hide packages")

    hide_virtual = param.Boolean(default=True, label="Hide virtual packages")

    small = param.Boolean(default=False, label="Hide python and conda")

    changes_only = param.Boolean(
        default=True,
        label="Show only changed packages",
        doc="While a version change is previewed, hide packages it does not touch.",
    )

    full = param.Parameter(default=None, doc="The complete DependencyGraph of the source.")

    view = param.Parameter(default=None, doc="The displayed subgraph.")

    def __init__(self, **params):
        params.setdefault("source", default_source_path())
        # The app draws its own Material toolbar and details panel; the built-in
        # ones remain for the standalone HTML export.
        self._graph = CytoscapeGraph(
            sizing_mode="stretch_both", min_height=400, toolbar=False, show_details=False
        )
        self._stats = pn.pane.HTML(
            margin=(0, 12), align="center", styles={"white-space": "nowrap", "font-size": "13px"}
        )
        self._message = pmui.Alert(
            visible=False, severity="warning", margin=(5, 10), sizing_mode="stretch_width"
        )
        table = dict(
            sizing_mode="stretch_width",
            styles={"height": "calc(100vh - 150px)"},
            theme="materialize",
            show_index=False,
            disabled=True,
            pagination=None,
            header_filters=True,
        )
        self._table = pn.widgets.Tabulator(pd.DataFrame(), **table)
        self._leaves = pn.widgets.Tabulator(pd.DataFrame(), **table)
        self._cycles = pn.pane.Markdown(sizing_mode="stretch_width")
        self._updates = UpdatesPanel()
        self._updates.table.param.update(
            sizing_mode="stretch_width", height=None, styles={"height": "calc(100vh - 150px)"}
        )
        self._details = DetailsPanel(
            version_controls=self._updates.package_controls if rattler else None
        )
        self._keep_view = False
        self._last_focus: str | None = None
        self._source_obj: Source | None = None
        self._uploaded: tuple | None = None
        super().__init__(**params)

        envs = discovered_environments()
        self.param.environment_name.objects = list(envs)
        self._envs = envs
        self._build_widgets()
        self._build_layout()
        self._wire()
        self._load_source()

    # Construction ---------------------------------------------------------

    def _build_widgets(self):
        self._env_select = pmui.AutocompleteInput.from_param(
            self.param.environment_name,
            placeholder="Pick an installed environment",
            search_strategy="includes",
            min_characters=0,
            restrict=True,
            sizing_mode="stretch_width",
        )
        self._path_input = pmui.TextInput.from_param(
            self.param.source,
            label="Path",
            placeholder="prefix, pixi.lock, conda-lock.yml, environment.yml or .dot",
            sizing_mode="stretch_width",
        )
        # The browser build has no filesystem or conda install to browse.
        self._env_select.visible = self._path_input.visible = not BROWSER
        # Converted (pyodide-worker) builds need holoviz/panel#8844 for this widget's
        # chunked uploads to reach Python.
        self._upload = pmui.FileInput(
            label="Upload file",
            accept=".lock,.yml,.yaml,.dot,.gv,.txt",
            sizing_mode="stretch_width",
        )
        self._intro = pmui.Alert(
            object=(
                "Running in your browser. Upload a DOT file (e.g. from "
                "`conda tree deptree --dot` or `conda-visualizer convert`), a pixi.lock, "
                "conda-lock.yml or explicit lockfile."
            ),
            severity="info",
            visible=BROWSER,
            margin=(5, 10),
            sizing_mode="stretch_width",
        )
        self._platform_select = pmui.Select.from_param(
            self.param.platform, sizing_mode="stretch_width", visible=False
        )
        self._env_lock_select = pmui.Select.from_param(
            self.param.environment, sizing_mode="stretch_width", visible=False
        )
        self._source_summary = pn.pane.HTML(
            sizing_mode="stretch_width", margin=0, stylesheets=[SOURCE_CSS]
        )
        self._focus_chip = pmui.Chip(
            label="", icon="cancel", color="primary", size="small", visible=False,
            description="Clear focus",
        )  # fmt: skip
        self._focus_hint = pn.pane.Markdown(
            "No focus. Double-click a package, or use **Focus** in its details, to show "
            "only its neighborhood.",
            margin=(0, 10),
            sizing_mode="stretch_width",
            styles={"font-size": "13px", "opacity": "0.8"},
        )
        self._direction = pmui.RadioButtonGroup.from_param(
            self.param.direction, size="small", sizing_mode="stretch_width"
        )
        # 1 to 10 levels, with one more step at the right end for unlimited.
        self._depth = pmui.IntSlider(
            label="",
            start=1,
            end=11,
            value=11,
            marks=[{"value": 1, "label": "1"}, {"value": 11, "label": "All"}],
            show_value=False,
            margin=(5, 20, 15, 20),
            sizing_mode="stretch_width",
        )
        self._depth_label = pn.pane.HTML(
            margin=(10, 12, 0, 0), align="center", styles={"font-size": "14px"}
        )
        self._exclude = pmui.MultiChoice.from_param(
            self.param.exclude, placeholder="Pick packages…", sizing_mode="stretch_width"
        )
        self._hide = pmui.MultiChoice.from_param(
            self.param.hide, placeholder="Pick packages…", sizing_mode="stretch_width"
        )
        self._hide_virtual = pmui.Switch.from_param(self.param.hide_virtual)
        self._small = pmui.Switch.from_param(self.param.small)

        self._find = pmui.AutocompleteInput(
            placeholder="Find package",
            search_strategy="includes",
            min_characters=1,
            restrict=True,
            size="small",
            width=190,
            margin=(10, 5),
        )
        self._layout = pmui.Select.from_param(
            self._graph.param.layout,
            options=LAYOUT_NAMES,
            label="Layout",
            description=None,
            size="small",
            width=140,
            margin=(10, 5),
        )
        self._rank_dir = pmui.RadioButtonGroup.from_param(
            self._graph.param.rank_dir,
            options={"↓": "TB", "→": "LR"},
            label="",
            size="small",
            width=90,
            margin=(10, 5),
        )
        self._fit = pmui.IconButton(icon="fit_screen", description="Fit to view", margin=(10, 0))
        self._isolate = pmui.IconButton(
            icon="filter_center_focus",
            description=ISOLATE_TIP,
            margin=(10, 0),
            disabled=True,
        )
        self._versions = pmui.Switch.from_param(
            self._graph.param.show_versions, label="Show versions"
        )
        self._changes_only_switch = pmui.Switch.from_param(self.param.changes_only, visible=False)
        self._latest = pmui.Switch.from_param(
            self._graph.param.show_latest, label="Show newest versions"
        )
        self._export = pmui.MenuButton(
            label="Export",
            icon="download",
            variant="outlined",
            color="light",
            items=[{"label": label, "icon": icon} for label, (icon, _) in EXPORTS.items()],
        )

    def _section(self, title, margin=(15, 10, 0, 10)):
        return pmui.Typography(title, variant="overline", margin=margin)

    def _build_layout(self):
        self._source_card = pmui.Card(
            self._intro,
            self._env_select,
            self._path_input,
            self._upload,
            self._platform_select,
            self._env_lock_select,
            header=self._source_summary,
            collapsible=True,
            # In the browser build uploading is the only way in, so it starts open.
            collapsed=not BROWSER,
            outlined=True,
            margin=(5, 10),
            sizing_mode="stretch_width",
        )
        sections = [
            (
                "Filters",
                pmui.Column(
                    self._exclude,
                    self._hide,
                    self._hide_virtual,
                    self._small,
                    sizing_mode="stretch_width",
                ),
            ),
            ("Display", pmui.Column(self._versions, self._latest, sizing_mode="stretch_width")),
        ]
        # Looking up and previewing versions solves with py-rattler, which has
        # no Pyodide build and is an optional extra elsewhere.
        if rattler is not None:
            sections.append(
                (
                    "Versions",
                    pmui.Column(
                        self._updates.environment_controls,
                        self._changes_only_switch,
                        sizing_mode="stretch_width",
                    ),
                )
            )
        else:
            self._latest.visible = False
        self._accordion = pmui.Accordion(
            *sections,
            active=[0],
            toggle=False,
            title_variant="overline",
            disable_gutters=True,
            sizing_mode="stretch_width",
            margin=(10, 0),
        )
        sidebar = pmui.Column(
            self._section("Source"),
            self._source_card,
            self._message,
            pmui.Divider(margin=(10, 0)),
            self._section("Focus", margin=(5, 10, 0, 10)),
            pmui.Row(self._focus_chip, margin=(0, 5)),
            self._focus_hint,
            self._direction,
            pmui.Row(
                pmui.Typography("Depth", variant="body2", margin=(10, 10, 0, 10)),
                pn.layout.HSpacer(),
                self._depth_label,
                align="center",
                sizing_mode="stretch_width",
            ),
            self._depth,
            pmui.Divider(margin=(0, 0)),
            self._accordion,
            sizing_mode="stretch_width",
        )
        toolbar = pmui.Row(
            self._find,
            self._layout,
            self._rank_dir,
            self._fit,
            self._isolate,
            pn.layout.HSpacer(),
            self._stats,
            align="center",
            sizing_mode="stretch_width",
            margin=(4, 4, 0, 4),
            styles={"border-bottom": "1px solid rgb(128 128 128 / 25%)", "flex-wrap": "wrap"},
        )
        graph_card = pmui.Paper(
            toolbar,
            self._graph,
            variant="outlined",
            sizing_mode="stretch_both",
            margin=0,
        )
        # Fills the window below the header and tab bar, so the graph and details never
        # push the page into scrolling.
        graph_view = pmui.Row(
            graph_card,
            self._details,
            sizing_mode="stretch_width",
            margin=(12, 0, 0, 0),
            styles={"height": "calc(100vh - 150px)", "min-height": "480px"},
        )
        tabs = [
            ("Graph", graph_view),
            ("Packages", self._table),
            ("Leaves", self._leaves),
            *([("Changes", self._updates.table)] if rattler else []),
            ("Cycles", self._cycles),
        ]
        self._tab_titles = [title for title, _ in tabs]
        self._tabs = pmui.Tabs(
            *tabs, dynamic=True, sizing_mode="stretch_width", margin=(0, 16, 16, 16)
        )
        main = self._tabs
        self._page = pmui.Page(
            title="Conda Visualizer",
            logo=LOGO,
            favicon=FAVICON,
            theme_config=THEME_CONFIG,
            sidebar=[sidebar],
            main=[main],
            header=[pmui.Row(pn.layout.HSpacer(), self._export, sizing_mode="stretch_width")],
            sidebar_width=330,
        )

    def _wire(self):
        self._upload.param.watch(self._on_upload, ["value", "filename"])
        self._focus_chip.param.watch(self._on_clear_focus, "clicks")
        self._export.on_click(self._on_export)
        self._graph.on_dblclick(self._on_dblclick)
        self._graph.param.watch(self._update_details, ["selected", "highlight"])
        self._graph.param.watch(self._on_isolated, "isolated")
        self._graph.param.watch(self._sync_layout_controls, "layout")
        self._depth.param.watch(self._on_depth_slider, "value_throttled")
        self._find.param.watch(self._on_find, "value")
        self._fit.on_click(lambda event: self._graph.fit())
        self._isolate.on_click(self._toggle_isolate)
        self._details.on_select(self.go_to)
        self._details.on_focus = lambda name: setattr(self, "focus", name)
        self._details.on_isolate = self._toggle_isolate
        self._details.on_close = lambda: setattr(self._graph, "selected", None)
        self._table.on_click(self._on_row_click)
        self._updates.table.on_click(self._on_row_click)
        self._updates.param.watch(self._on_current, "current")
        self._updates.param.watch(self._on_plan, "plan")
        self._graph.param.watch(lambda e: setattr(self._updates, "selected", e.new), "selected")
        self._leaves.on_click(self._on_row_click)
        self._page.param.watch(self._sync_theme, "dark_theme")
        self._graph.dark_theme = self._page.dark_theme
        self._details.set_dark(self._page.dark_theme)
        self._sync_layout_controls()
        self._sync_direction()
        self._sync_focus_controls()

    def __panel__(self):
        return self._page

    # Loading --------------------------------------------------------------

    @param.depends("environment_name", watch=True)
    def _on_environment_name(self):
        if self.environment_name:
            self.source = self._envs[self.environment_name]

    @param.depends("source", watch=True)
    def _load_source(self):
        if not self.source:
            return
        self._open(lambda: open_source(self.source))

    def _on_upload(self, *events):
        # In Pyodide value and filename arrive as separate updates; reload when the
        # filename catches up so detection and labels use the right name.
        value, filename = self._upload.value, self._upload.filename
        if not value or (value, filename) == self._uploaded:
            return
        self._uploaded = (value, filename)
        self._open(lambda: open_source(value, filename=filename))

    def _open(self, factory):
        self._graph.loading = True
        try:
            source = factory()
        except Exception as exc:
            # Unexpected parser failures must reach the user too, not just the log.
            expected = isinstance(exc, (SourceError, OSError))
            self._show_message(str(exc) if expected else f"{type(exc).__name__}: {exc}", "error")
            self._graph.loading = False
            return
        self._source_obj = source
        values = {"platform": source.default_platform, "environment": source.default_environment}
        unchanged = values == {"platform": self.platform, "environment": self.environment}
        with pn.io.hold():
            self.param.platform.objects = source.platforms
            self.param.environment.objects = source.environments
            # update() fires _build_graph once for both values; same values fire nothing.
            self.param.update(values)
            self._platform_select.visible = len(source.platforms) > 1
            self._env_lock_select.visible = len(source.environments) > 1
            if unchanged:
                self._build_graph()

    @param.depends("platform", "environment", watch=True)
    def _build_graph(self):
        if self._source_obj is None:
            return
        self._graph.loading = True
        try:
            full = self._source_obj.graph(self.platform, self.environment)
        except Exception as exc:
            self._show_message(f"{type(exc).__name__}: {exc}", "error")
            self._graph.loading = False
            return
        names = full.packages
        with pn.io.hold():
            self.param.focus.objects = names
            self.param.exclude.objects = names
            self.param.hide.objects = names
            self._find.options = names
            name = Path(full.source).name or full.source
            meta = " · ".join(p for p in (full.platform, f"{len(full)} packages") if p)
            self._source_summary.object = (
                f'<div class="src"><b title="{html.escape(full.source)}">{html.escape(name)}</b>'
                f"<span>{html.escape(meta)}</span></div>"
            )
            self.param.update(
                focus=self.focus if self.focus in full else None,
                exclude=[n for n in self.exclude if n in full],
                hide=[n for n in self.hide if n in full],
                full=full,
            )
            self._updates.reset(full)
            if full.warnings:
                self._show_message("\n".join(full.warnings), "warning")
            else:
                self._message.visible = False

    def _on_plan(self, event):
        self._changes_only_switch.visible = event.new is not None

    def _on_current(self, event):
        """Show the annotated graph, with the active preview applied."""
        if event.new is not None and event.new is not self.full:
            # Looking up versions or previewing a change must not move the selected package.
            self._keep_view = True
            try:
                self.full = event.new
            finally:
                self._keep_view = False

    def _show_message(self, text, severity):
        self._message.param.update(object=text, severity=severity, visible=True)

    # View -----------------------------------------------------------------

    @param.depends(
        "full",
        "focus",
        "direction",
        "depth",
        "exclude",
        "hide",
        "hide_virtual",
        "small",
        "changes_only",
        watch=True,
    )
    def _update_view(self):
        full: DependencyGraph | None = self.full
        if full is None:
            return
        hide = set(self.hide)
        if self.hide_virtual:
            hide |= set(full.without_kinds(["virtual"]))
        if self.small:
            hide |= set(COMMON)
        focus = [self.focus] if self.focus else []
        nodes = full.neighborhood(focus, self.direction, self.depth or None, self.exclude, hide)
        truncated = False
        if not focus and len(nodes) > MAX_NODES:
            requested = [n for n in nodes if full.graph.nodes[n].get("requested")]
            nodes = full.neighborhood(requested or full.leaves(), "depends", 1, hide=hide)
            truncated = True
        nodes = self._only_changes(full, nodes)
        view = full.subgraph(nodes)
        with pn.io.hold():
            self.view = view
            elements = view.to_elements()
            if self._keep_view:
                elements["keep_view"] = True
            self._graph.param.update(elements=elements, filename=self._export_name)
            # A new focus is what the user is now looking at, so it replaces the selection.
            refocused = self.focus is not None and self.focus != self._last_focus
            self._last_focus = self.focus
            if refocused or not self._graph.selected or self._graph.selected not in view:
                self._graph.selected = self.focus
            self._graph.loading = False
            self._update_tables(full, view)
            self._update_stats(full, view, truncated)
            self._update_details()

    def _only_changes(self, full: DependencyGraph, nodes: set[str]) -> set[str]:
        """While a preview is active, keep the changed packages and what the pinned ones need."""
        data = dict(full.graph.nodes(data=True))
        changed = {n for n in nodes if data[n].get("change")}
        if not self.changes_only or not any(d.get("change") for d in data.values()):
            return nodes
        pinned = [n for n in changed if data[n].get("target")]
        context = {dep for n in pinned for dep in full.graph.successors(n)}
        return changed | (context & nodes)

    def _update_stats(self, full, view, truncated):
        shown = (
            f"<b>{len(view)}</b> of {len(full)} packages"
            if len(view) < len(full)
            else f"<b>{len(full)}</b> packages"
        )
        parts = [shown, f"<b>{view.graph.number_of_edges()}</b> edges"]
        if any("latest" in d for _, d in full.graph.nodes(data=True)):
            late = sum(1 for _, d in full.graph.nodes(data=True) if d.get("outdated"))
            parts.append(f"<b>{late}</b> outdated")
        title = (
            f"Limited to direct dependencies of requested packages above {MAX_NODES}"
            if truncated
            else ""
        )
        self._stats.object = f'<span title="{title}">{" · ".join(parts)}</span>'

    def _update_tables(self, full, view):
        self._table.value = pd.DataFrame(view.records())
        leaves = [r for r in full.records() if r["dependents"] == 0]
        self._leaves.value = pd.DataFrame(leaves)
        cycles = full.cycles()
        self._cycles.object = (
            "\n".join(f"- {' → '.join([*c, c[0]])}" for c in cycles)
            if cycles
            else "No dependency cycles."
        )
        counts = {"Packages": len(view), "Leaves": len(leaves), "Cycles": len(cycles)}
        self._tabs._names = [f"{t} ({counts[t]})" if t in counts else t for t in self._tab_titles]

    def _update_details(self, *_):
        full = self.full
        name = self._graph.selected
        self._isolate.disabled = not name and not self._graph.isolated
        if full is None or not name or name not in full:
            self._details.show_environment(full)
            return
        reverse = self.direction == "whoneeds"
        self._details.show_package(
            full.details(name),
            full.tree(name, reverse=reverse, exclude=self.exclude),
            focused=name == self.focus,
            isolated=self._graph.isolated,
            highlight=self._graph.highlight,
        )

    def _sync_focus_controls(self):
        self._focus_chip.param.update(label=self.focus or "", visible=self.focus is not None)
        self._focus_hint.visible = self.focus is None
        # Depth only limits a focused view, so without a focus it reads as "All".
        self._depth.disabled = self.focus is None
        self._depth.value = self.depth or 11
        levels = "All levels" if not self.depth else f"{self.depth} level" + "s" * (self.depth > 1)
        self._depth_label.object = f"<b>{levels}</b>"

    # Handlers -------------------------------------------------------------

    @param.depends("focus", "depth", watch=True)
    def _on_focus_changed(self):
        self._sync_focus_controls()

    @param.depends("direction", watch=True)
    def _sync_direction(self):
        self._graph.highlight = self.direction

    def _on_depth_slider(self, event):
        self.depth = 0 if event.new >= 11 else event.new

    def _sync_layout_controls(self, *_):
        self._rank_dir.disabled = self._graph.layout not in RANKED

    def _on_isolated(self, event):
        self._details.set_isolated(event.new)
        self._isolate.param.update(
            icon="select_all" if event.new else "filter_center_focus",
            description="Show all packages" if event.new else ISOLATE_TIP,
        )

    def _toggle_isolate(self, *_):
        self._graph.isolated = not self._graph.isolated

    def _on_find(self, event):
        if event.new:
            self.go_to(event.new)
            self._find.value = None

    def go_to(self, name: str) -> None:
        """Select ``name``, focusing it first when the current view leaves it out."""
        self._tabs.active = 0
        if self.view is not None and name in self.view:
            self._graph.selected = name
        else:
            self.focus = name

    def _on_dblclick(self, name):
        self.focus = name

    def _on_clear_focus(self, event):
        self.focus = None

    def _on_row_click(self, event):
        if event.column == "name" and event.value:
            self.go_to(event.value)

    def _sync_theme(self, event):
        self._graph.dark_theme = event.new
        self._details.set_dark(event.new)

    # Export ---------------------------------------------------------------

    @property
    def _export_name(self):
        name = Path(self.full.source).name if self.full is not None else "graph"
        # Only strip input-file suffixes; prefix names like "dev3.14" contain dots too.
        for suffix in (".dot", ".gv", ".lock", ".yml", ".yaml", ".txt"):
            name = name.removesuffix(suffix)
        return f"{name}-{self.focus}" if self.focus else name

    def _on_export(self, item):
        fmt = EXPORTS[item["label"]][1]
        if fmt in ("png", "svg"):
            self._graph.export(fmt, self._export_name)
            return
        text = getattr(self, f"_{fmt}_text")()
        self._graph.download(text, f"{self._export_name}.{fmt}", MIME_TYPES[fmt])

    def _dot_text(self) -> str:
        return self.view.to_dot() if self.view is not None else "digraph {}\n"

    def _html_text(self) -> str:
        buffer = io.StringIO()
        if self.view is not None:
            page = standalone(
                self.view,
                title=f"Dependencies of {self._export_name}",
                layout=self._graph.layout,
                rank_dir=self._graph.rank_dir,
                highlight=self._graph.highlight,
                show_versions=self._graph.show_versions,
                selected=self._graph.selected,
            )
            with pn.config.set(raw_css=[STANDALONE_CSS]):
                page.save(buffer, title=self._export_name, resources="cdn")
        return buffer.getvalue()

    def _json_text(self) -> str:
        return self.view.to_json() if self.view is not None else "{}"


def create_app(source: str | None = None) -> Explorer:
    args = pn.state.session_args or {}
    if source is None and "source" in args:
        source = args["source"][0].decode()
    return Explorer(source=source) if source else Explorer()


# Only when this file is the served script; since Panel 1.10 pn.state.served is also
# true when a converted (Pyodide) app imports this module, which would add a second app.
if __name__.startswith("bokeh_app"):
    create_app().servable(title="Conda Visualizer")
