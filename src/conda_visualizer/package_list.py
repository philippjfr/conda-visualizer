"""Clickable list of packages with a value and an optional bar per row."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import param
from panel.custom import JSComponent

from .theme import GREEN, LILAC, NEUTRAL

CSS = f"""
:host {{ --pl-link: {LILAC[600]}; --pl-muted: {NEUTRAL[700]}; --pl-track: {NEUTRAL[100]}; }}
.dark {{ --pl-link: {LILAC[300]}; --pl-muted: {NEUTRAL[400]}; --pl-track: {LILAC[700]}; }}
.pl-list {{ font: 14px/1.4 Inter, Arial, sans-serif; }}
.pl-row {{
  display: flex; align-items: center; gap: 12px; min-height: 30px;
}}
.pl-name {{
  flex: 0 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  color: var(--pl-link); cursor: pointer; font-weight: 500;
}}
.pl-name:hover {{ text-decoration: underline; }}
.pl-track {{
  flex: 1 1 auto; display: flex; justify-content: flex-end; height: 6px; min-width: 40px;
}}
.pl-track i {{ display: block; height: 100%; border-radius: 3px; background: var(--pl-bar); }}
.pl-value {{
  flex: 1 0 auto; max-width: 55%; margin-left: auto; text-align: right; overflow: hidden;
  text-overflow: ellipsis; white-space: nowrap; color: var(--pl-muted);
  font: 12px "Space Mono", monospace;
}}
.pl-track + .pl-value {{ flex: 0 0 34px; }}
.pl-empty {{ color: var(--pl-muted); font-size: 13px; padding: 4px 0; }}
"""


class PackageList(JSComponent):
    """Package rows; clicking a name calls the ``on_select`` callbacks with it.

    ``items`` holds ``{"name": ..., "value": ..., "bar": ...}`` dicts, where ``bar``
    is a fraction in [0, 1] or omitted for no bar.
    """

    items = param.List(default=[], doc="Rows to show.")

    empty = param.String(default="None", doc="Text shown when there are no items.")

    bar_color = param.Color(default=LILAC[200], doc="Bar color in the light theme.")

    bar_color_dark = param.Color(default=LILAC[500], doc="Bar color in the dark theme.")

    dark_theme = param.Boolean(default=False)

    _esm = Path(__file__).parent / "package_list.js"

    _stylesheets = [CSS]  # noqa: RUF012

    def __init__(self, **params):
        params.setdefault("sizing_mode", "stretch_width")
        super().__init__(**params)
        self._select_callbacks: list[Callable[[str], None]] = []

    def on_select(self, callback: Callable[[str], None]) -> None:
        self._select_callbacks.append(callback)

    def _handle_msg(self, data) -> None:
        if data.get("type") == "select":
            for callback in self._select_callbacks:
                callback(data["name"])

    @staticmethod
    def ranked(counts: dict[str, int], limit: int | None = None) -> list[dict]:
        """Rows sorted by count, largest first, with bars relative to the largest."""
        rows = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]
        top = max((c for _, c in rows), default=0) or 1
        return [{"name": n, "value": str(c), "bar": c / top} for n, c in rows]


GREEN_BARS = {"bar_color": GREEN[300], "bar_color_dark": GREEN[500]}
