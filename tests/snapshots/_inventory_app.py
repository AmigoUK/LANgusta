"""Helper app for the snapshot harness.

pytest-textual-snapshot executes a Python file whose top-level defines
`app`. We launch the inventory screen directly so snapshots aren't timing-
sensitive to Textual's initial focus/layout dance.
"""

from __future__ import annotations

from langusta.tui.app import LangustaApp

app = LangustaApp()
