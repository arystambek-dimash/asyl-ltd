"""Client statement exports.

The public API deliberately hides the data/rendering split from API views.
"""

from .data import build_statement_data
from .pdf import render_statement_pdf
from .sections import ALL_CLIENT_SECTIONS, CLIENT_SECTIONS
from .xlsx import render_statement_xlsx

RENDERERS = {"xlsx": render_statement_xlsx, "pdf": render_statement_pdf}


def build_statement(export: str, **filters) -> bytes:
    """Build a statement snapshot (one client or all) and render it as ``export``."""
    return RENDERERS[export](build_statement_data(**filters))


__all__ = (
    "ALL_CLIENT_SECTIONS",
    "CLIENT_SECTIONS",
    "build_statement",
)
