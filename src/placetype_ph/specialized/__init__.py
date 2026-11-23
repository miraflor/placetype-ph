"""Conservative classification-evidence APIs.

Pure policy modules are importable without optional table-processing dependencies.
The table-processing helpers are loaded only when invoked.
"""

from __future__ import annotations

from .policy import POLICY_ID, as_columns, assess, fingerprint

__all__ = [
    "ADDED_COLUMNS",
    "POLICY_ID",
    "as_columns",
    "assess",
    "enrich_summary",
    "fingerprint",
    "report",
]


def __getattr__(name: str):
    if name in {"ADDED_COLUMNS", "enrich_summary", "report"}:
        from .integration import ADDED_COLUMNS, enrich_summary, report

        return {"ADDED_COLUMNS": ADDED_COLUMNS, "enrich_summary": enrich_summary, "report": report}[
            name
        ]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
