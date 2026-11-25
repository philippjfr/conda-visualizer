"""Side panel next to the graph: the selected package, or an environment summary."""

from __future__ import annotations

import html
import re
from collections.abc import Callable
from pathlib import Path

import panel as pn
import panel_material_ui as pmui

from .graph import DependencyGraph
from .package_list import GREEN_BARS, PackageList
from .theme import GREEN, LILAC

#: Shared look for the HTML panes; colors come from the surrounding text so dark mode works.
CSS = f"""
.kv {{ display: grid; grid-template-columns: 64px 1fr; gap: 4px 12px; font-size: 14px; }}
.kv span {{ opacity: 0.65; }}
.kv code {{ font-size: 12.5px; overflow-wrap: anywhere; }}
.tiles {{ display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }}
.tile {{
  border: 1px solid rgb(128 128 128 / 30%); border-radius: 8px; padding: 10px 12px;
}}
.tile b {{
  display: block; font: 500 24px/1.2 "Space Grotesk", Arial, sans-serif; letter-spacing: -0.02em;
}}
.tile span {{ font-size: 13px; opacity: 0.65; }}
.head {{
  display: flex; align-items: center; justify-content: space-between; gap: 8px;
  font-size: 14px; font-weight: 600;
}}
.head small {{ font-weight: 400; font-size: 13px; opacity: 0.65; }}
.head i {{
  display: inline-block; width: 10px; height: 10px; margin-right: 8px; border-radius: 2px;
  border: 2px solid currentColor; vertical-align: -1px;
}}
.head i.depends {{ border-color: {GREEN[700]}; }}
.head i.whoneeds {{ border-color: {LILAC[500]}; }}
.head .count {{ font-weight: 400; opacity: 0.65; margin-left: 6px; }}
.hint {{
  display: flex; gap: 10px; padding: 10px 12px; border-radius: 8px;
  background: rgb(128 128 128 / 10%); font-size: 13px; line-height: 1.5; opacity: 0.85;
}}
.meta {{ font-size: 13px; opacity: 0.7; }}
.tree {{
  margin: 0; padding: 10px 12px; border-radius: 8px; background: rgb(128 128 128 / 8%);
  font: 12px/1.55 "Space Mono", monospace; white-space: pre; overflow-x: auto;
}}
"""

TITLE_SX = {"fontSize": "26px", "fontWeight": 500, "lineHeight": 1.25, "wordBreak": "break-word"}

SOURCE_KINDS = {
    ".dot": "DOT file",
    ".gv": "DOT file",
    ".lock": "Lockfile",
    ".yml": "Environment file",
    ".yaml": "Environment file",
    ".txt": "Explicit lockfile",
}

HINT = (
    '<div class="hint"><span>👆</span><span>Click a package to inspect it. '
    "Double-click to focus the graph on its neighborhood.</span></div>"
)


def _html(text: str = "", **params) -> pn.pane.HTML:
    params.setdefault("sizing_mode", "stretch_width")
    params.setdefault("margin", (4, 0))
    return pn.pane.HTML(text, stylesheets=[CSS], **params)


def heading(title: str, note: str = "", count: int | None = None, swatch: str = "") -> str:
    icon = f'<i class="{swatch}"></i>' if swatch else ""
    number = f'<span class="count">{count}</span>' if count is not None else ""
    small = f"<small>{html.escape(note)}</small>" if note else ""
    return f'<div class="head"><span>{icon}{html.escape(title)}{number}</span>{small}</div>'


def constraint(name: str, spec: str) -> str:
    """``spec`` without the package name it constrains, which the row already shows."""
    # Merged specs look like "numpy >=1.26, numpy >=1.25,<3".
    stripped = re.sub(rf"(^|,\s*){re.escape(name)}(\s+|$)", r"\1", spec).strip(", ")
    return stripped or ("any" if spec else "")


class DetailsPanel(pn.viewable.Viewer):
    """Package details with relations, tree and versions; a summary when nothing is selected.

    ``on_select`` gets package names clicked in any list, ``on_focus`` the package to
    focus (or None to clear), ``on_isolate`` a request to toggle isolation, and
    ``on_close`` a request to clear the selection.
    """

    def __init__(self, version_controls=None, **params):
        super().__init__(**params)
        self._select_callbacks: list[Callable[[str], None]] = []
        self.on_focus: Callable[[str | None], None] = lambda name: None
        self.on_isolate: Callable[[], None] = lambda: None
        self.on_close: Callable[[], None] = lambda: None
        self._name: str | None = None
        self._focused = False

        # Package view
        self.title = pmui.Typography(variant="h5", margin=(0, 0, 4, 0), sx=TITLE_SX)
        self._close = pmui.IconButton(icon="close", size="small", description="Clear selection")
        chip = dict(size="small", visible=False)
        self._version_chip = pmui.Chip(color="primary", **chip)
        self._channel_chip = pmui.Chip(variant="outlined", **chip)
        self._requested_chip = pmui.Chip(
            label="requested", color="success", variant="outlined", **chip
        )
        self._latest_chip = pmui.Chip(color="secondary", variant="outlined", **chip)
        self._change_chip = pmui.Chip(color="warning", **chip)
        self._chips = pmui.Row(
            self._version_chip,
            self._channel_chip,
            self._requested_chip,
            self._latest_chip,
            self._change_chip,
            margin=(0, 0, 4, -4),
        )
        self.meta = _html()
        self._focus_btn = pmui.Button(label="Focus", icon="center_focus_strong", size="small")
        self._isolate_btn = pmui.Button(
            label="Isolate", icon="filter_center_focus", variant="outlined", size="small"
        )
        self.depends = PackageList(empty="No dependencies", **GREEN_BARS)
        self.whoneeds = PackageList(empty="Nothing needs this package")
        self._depends_head = _html()
        self._whoneeds_head = _html()
        self.tree = _html(margin=(8, 0))
        self.relations = pmui.Column(
            self._depends_head,
            self.depends,
            self._whoneeds_head,
            self.whoneeds,
            sizing_mode="stretch_width",
        )
        tabs = [("Relations", self.relations), ("Tree", self.tree)]
        if version_controls is not None:
            tabs.append(("Versions", version_controls))
        # Default tab widths overflow the 370px panel with three tabs.
        self.tabs = pmui.Tabs(
            *tabs,
            sizing_mode="stretch_width",
            margin=(8, 0, 0, 0),
            sx={"& .MuiTab-root": {"minWidth": 0, "px": 1.5}},
        )
        self._package = pmui.Column(
            pmui.Row(
                pmui.Typography("Package", variant="overline", margin=0),
                pn.layout.HSpacer(),
                self._close,
                sizing_mode="stretch_width",
                margin=0,
            ),
            self.title,
            self._chips,
            self.meta,
            pmui.Row(self._focus_btn, self._isolate_btn, margin=(4, 0, 0, -10)),
            self.tabs,
            sizing_mode="stretch_width",
            visible=False,
        )

        # Environment view
        self._env_title = pmui.Typography(variant="h5", margin=(0, 0, 0, 0), sx=TITLE_SX)
        self._env_meta = _html()
        self._tiles = _html()
        self._leaves_head = _html(heading("Leaves", "direct deps"), margin=(12, 0, 0, 0))
        self.leaves = PackageList(empty="No leaves", **GREEN_BARS)
        self._hubs_head = _html(heading("Most needed", "dependents"), margin=(12, 0, 0, 0))
        self.hubs = PackageList(empty="No dependencies")
        self._environment = pmui.Column(
            pmui.Typography("Environment", variant="overline", margin=0),
            self._env_title,
            self._env_meta,
            self._tiles,
            self._leaves_head,
            self.leaves,
            self._hubs_head,
            self.hubs,
            _html(HINT, margin=(16, 0, 0, 0)),
            sizing_mode="stretch_width",
        )

        for lst in (self.depends, self.whoneeds, self.leaves, self.hubs):
            lst.on_select(self._select)
        self._close.on_click(lambda event: self.on_close())
        self._focus_btn.on_click(lambda event: self.on_focus(None if self._focused else self._name))
        self._isolate_btn.on_click(lambda event: self.on_isolate())

        for column in (self._package, self._environment):
            column.param.update(margin=(14, 18), sizing_mode="stretch_width")
        self._layout = pmui.Paper(
            self._package,
            self._environment,
            width=370,
            sizing_mode="stretch_height",
            scroll="y-auto",
            margin=(0, 0, 0, 12),
            variant="outlined",
        )

    def __panel__(self):
        return self._layout

    def on_select(self, callback: Callable[[str], None]) -> None:
        self._select_callbacks.append(callback)

    def _select(self, name: str) -> None:
        for callback in self._select_callbacks:
            callback(name)

    def set_dark(self, dark: bool) -> None:
        for lst in (self.depends, self.whoneeds, self.leaves, self.hubs):
            lst.dark_theme = dark

    # Package ----------------------------------------------------------------

    def show_package(
        self,
        info: dict,
        tree: str,
        focused: bool,
        isolated: bool,
        highlight: str = "both",
    ) -> None:
        self._name, self._focused = info["name"], focused
        was = f" from {info['old_version']}" if info.get("old_version") else ""
        chips = {
            self._version_chip: info.get("version") or "",
            self._channel_chip: info.get("channel") or "",
            self._requested_chip: "requested" if info.get("requested") else "",
            self._latest_chip: f"↑ {info['latest']} available" if info.get("outdated") else "",
            self._change_chip: f"preview: {info['change']}{was}" if info.get("change") else "",
        }
        rows = [
            (
                "Build",
                f"<code>{html.escape(str(info['build']))}</code>" if info.get("build") else "",
            ),
            ("Kind", f"{info['kind']} package" if info.get("kind") else ""),
            ("License", html.escape(str(info.get("license") or ""))),
            ("Size", f"{info['size'] / 1e6:.2f} MB" if info.get("size") else ""),
        ]
        self.meta.object = (
            '<div class="kv">'
            + "".join(f"<span>{k}</span><div>{v}</div>" for k, v in rows if v)
            + "</div>"
        )
        depends = [{"name": n, "value": constraint(n, s)} for n, s in info["depends"][:300]]
        whoneeds = [
            {"name": n, "value": constraint(info["name"], s)} for n, s in info["whoneeds"][:300]
        ]
        with pn.io.hold():
            self.title.object = info["name"]
            for widget, label in chips.items():
                widget.param.update(label=label or widget.label, visible=bool(label))
            self._depends_head.object = heading(
                "Depends on", count=len(info["depends"]), swatch="depends"
            )
            self._whoneeds_head.object = heading(
                "Needed by", count=len(info["whoneeds"]), swatch="whoneeds"
            )
            self.depends.items = depends
            self.whoneeds.items = whoneeds
            self._depends_head.visible = self.depends.visible = highlight != "whoneeds"
            self._whoneeds_head.visible = self.whoneeds.visible = highlight != "depends"
            self.tree.object = f'<pre class="tree">{html.escape(tree)}</pre>'
            self._focus_btn.param.update(
                label="Clear focus" if focused else "Focus",
                icon="close" if focused else "center_focus_strong",
            )
            self.set_isolated(isolated)
            self._package.visible = True
            self._environment.visible = False

    def set_isolated(self, isolated: bool) -> None:
        self._isolate_btn.label = "Show all" if isolated else "Isolate"

    # Environment ------------------------------------------------------------

    def show_environment(self, full: DependencyGraph | None) -> None:
        self._name = None
        with pn.io.hold():
            self._package.visible = False
            self._environment.visible = True
            if full is None:
                self._env_title.object = "No environment"
                self._env_meta.object = ""
                self._tiles.object = ""
                self.leaves.items = self.hubs.items = []
                return
            name = Path(full.source).name or full.source
            kind = SOURCE_KINDS.get(Path(name).suffix, "")
            meta = " · ".join(part for part in (full.platform, kind) if part)
            graph = full.graph
            tiles = (
                (len(full), "packages"),
                (graph.number_of_edges(), "dependencies"),
                (len(full.leaves()), "leaves"),
                (len(full.cycles()), "cycles"),
            )
            self._env_title.object = name
            self._env_meta.object = f'<div class="meta">{html.escape(meta)}</div>'
            self._tiles.object = (
                '<div class="tiles">'
                + "".join(
                    f'<div class="tile"><b>{n}</b><span>{label}</span></div>' for n, label in tiles
                )
                + "</div>"
            )
            self.leaves.items = PackageList.ranked(
                {n: graph.out_degree(n) for n in full.leaves()}, limit=8
            )
            self.hubs.items = PackageList.ranked(
                {n: graph.in_degree(n) for n in graph if graph.in_degree(n)}, limit=5
            )
