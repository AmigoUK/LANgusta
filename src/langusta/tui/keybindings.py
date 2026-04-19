"""Keybinding presets for the TUI.

By default LANgusta's TUI uses Textual's native arrow-key navigation. Power
users who live in vim can opt in via:

    LANGUSTA_KEYBINDINGS=vim langusta ui

The preset is additive — it layers vim-style aliases on top of the default
bindings and does not remove or remap the defaults. Unknown preset names
resolve to an empty tuple so misconfigurations degrade gracefully.
"""

from __future__ import annotations

import os
from typing import Final

from textual.binding import Binding

# Vim preset — pure navigation aliases; no modal editing semantics.
#
# The actions dispatch to whatever widget has focus (DataTable / Input /
# OptionList / …) via the Textual screen's default action handling. Each
# binding stays `priority=False` so `Input` widgets that capture printable
# keys (e.g. the search screen) keep receiving them as typed text.
VIM_PRESET: Final[tuple[Binding, ...]] = (
    Binding("j", "cursor_down", "↓", show=False),
    Binding("k", "cursor_up", "↑", show=False),
    Binding("g", "scroll_home", "Top", show=False),
    Binding("G", "scroll_end", "Bottom", show=False),
    Binding("ctrl+d", "page_down", "Page ↓", show=False),
    Binding("ctrl+u", "page_up", "Page ↑", show=False),
)

_PRESETS: Final[dict[str, tuple[Binding, ...]]] = {
    "vim": VIM_PRESET,
}

_ENV_VAR: Final[str] = "LANGUSTA_KEYBINDINGS"


def resolve_preset(name: str | None) -> tuple[Binding, ...]:
    """Map a preset name to its binding tuple.

    Empty, None, or unknown names resolve to `()` so callers never crash on
    a typo'd env var.
    """
    if not name:
        return ()
    return _PRESETS.get(name.strip().lower(), ())


def active_preset_from_env() -> tuple[Binding, ...]:
    """Read `LANGUSTA_KEYBINDINGS` and return the matching bindings."""
    return resolve_preset(os.environ.get(_ENV_VAR))
