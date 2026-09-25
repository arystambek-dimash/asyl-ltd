"""Public section keys shared by statement builders and API validation."""

CLIENT_SECTIONS = (
    "summary",
    "ledger",
    "orders",
    "items",
    "payments",
    "debts",
)
ALL_CLIENT_SECTIONS = (
    "summary",
    "clients",
    "ledger",
    "orders",
    "items",
    "payments",
    "debts",
)

def select_sections(
    sections,
    available: tuple[str, ...],
) -> tuple[str, ...]:
    """Return selected sections in their canonical document order."""
    if sections is None:
        return available
    chosen = set(sections)
    return tuple(key for key in available if key in chosen)
