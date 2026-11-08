"""The step a pipeline event is applied in: every concern's methods, in one object."""

from __future__ import annotations

from .planning import StepPlanning


class _Step(StepPlanning):
    """One event's work on a copy of the state. Its methods live with their concern."""
