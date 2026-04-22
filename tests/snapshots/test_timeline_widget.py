"""Snapshot tests — TimelineWidget rendering across mixed and empty entries.

Wave-3 TEST-T-010 (single-lens test-gap, medium). TimelineWidget has no
snapshot coverage; a CSS tweak or kind-marker change could silently
break the TUI presentation. Baselines are kept for two cases:
  - mixed-kind (system + scan_diff + note + monitor_event + correction)
  - empty (the "(no timeline entries yet)" muted placeholder branch)
"""

from __future__ import annotations

from pathlib import Path

APP_SCRIPT_MIXED = Path(__file__).parent / "_timeline_mixed_app.py"
APP_SCRIPT_EMPTY = Path(__file__).parent / "_timeline_empty_app.py"


def test_timeline_widget_mixed_kinds(snap_compare) -> None:
    assert snap_compare(str(APP_SCRIPT_MIXED), terminal_size=(80, 24))


def test_timeline_widget_empty(snap_compare) -> None:
    assert snap_compare(str(APP_SCRIPT_EMPTY), terminal_size=(80, 8))
