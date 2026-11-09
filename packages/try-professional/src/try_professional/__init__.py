"""Try Professional: a structured company of LLM agents.

The stable API is `Company`, in try_professional.api. See docs/building-on.md.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

__version__ = "0.6.3"
__all__ = ["Company", "CompanyError", "__version__"]

if TYPE_CHECKING:
    from .api import Company, CompanyError


def __getattr__(name: str) -> Any:
    # Loaded on first use, so importing the package stays quick.
    if name in ("Company", "CompanyError"):
        from . import api

        return getattr(api, name)
    raise AttributeError(name)
