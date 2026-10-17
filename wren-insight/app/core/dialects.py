"""Mapping from Wren data-source names to sqlglot dialect names."""

from __future__ import annotations

# Wren uses ``mssql`` internally; sqlglot calls the dialect ``tsql``.
_SQLGLOT_DIALECTS = {
    "mssql": "tsql",
}


def sqlglot_dialect(data_source: str) -> str:
    return _SQLGLOT_DIALECTS.get(data_source, data_source)
