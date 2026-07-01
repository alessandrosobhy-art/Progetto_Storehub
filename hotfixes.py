"""Hotfix runtime helpers for MAGAZZINO project.

This module is meant to be imported at startup (via sitecustomize.py).
It provides:
- missing helper names referenced by updated repositories (take_next_last_data_col, _is_tech_col)
- a safer supplier reader for SQL Server when code/name column is the same

It is intentionally defensive and should not break Access mode.
"""

from __future__ import annotations

import builtins
import os
from typing import Any, Dict, List, Optional


def _norm(s: str) -> str:
    return "".join(ch for ch in (s or "").strip().lower() if ch.isalnum() or ch == "_")


def _is_tech_col(name: str) -> bool:
    n = _norm(name)
    if not n:
        return False
    if n in {"row_uuid", "uuid", "id", "created_at", "updated_at"}:
        return True
    if n.endswith("_uuid"):
        return True
    return False


def take_next_last_data_col(seq: List[str], default: Optional[str] = None) -> Optional[str]:
    """Pop and return the *last* column that looks like a date column.

    Designed to match older refactors where a list of remaining columns is
    progressively consumed. If no date-like column is found, it pops the last
    non-technical column. Returns `default` (or None) if the list is empty.

    Heuristics:
    - prefer names containing 'data', 'date', 'giorno'
    - ignore technical columns like row_uuid
    """
    if not seq:
        return default

    # search from end for date-like columns
    for i in range(len(seq) - 1, -1, -1):
        c = seq[i]
        n = _norm(c)
        if _is_tech_col(n):
            continue
        if "data" in n or "date" in n or "giorno" in n:
            return seq.pop(i)

    # otherwise pop last non-tech
    for i in range(len(seq) - 1, -1, -1):
        c = seq[i]
        if _is_tech_col(c):
            continue
        return seq.pop(i)

    # only tech columns left
    return seq.pop() if seq else default


# Expose missing names in builtins so older modules can resolve them
builtins._is_tech_col = _is_tech_col  # type: ignore[attr-defined]
builtins.take_next_last_data_col = take_next_last_data_col  # type: ignore[attr-defined]


def _find_col(cols_raw: List[str], desired: str) -> Optional[str]:
    dn = _norm(desired)
    if not dn:
        return None
    for c in cols_raw:
        if _norm(c) == dn:
            return c
    # contains match as fallback
    for c in cols_raw:
        if dn in _norm(c):
            return c
    return None


def _patched_get_suppliers_for_store(store_code: str) -> Dict[str, Any]:
    """Replacement for delivery_repository.get_suppliers_for_store.

    Fixes SQL Server error: ambiguous column name when selecting the same column
    twice and ordering by it.
    """
    table = os.getenv("ACCESS_SUPPLIERS_TABLE", "FORNITORI")
    code_want = os.getenv("ACCESS_SUPPLIERS_CODE_COL", "Fornitore")
    name_want = os.getenv("ACCESS_SUPPLIERS_NAME_COL", "Fornitore")

    result: Dict[str, Any] = {
        "table": table,
        "code_column": code_want,
        "name_column": name_want,
        "available_columns": [],
        "suppliers": [],
        "error": None,
    }

    try:
        # Prefer app_db router if present
        try:
            from app_db import get_connection  # type: ignore
        except Exception:
            from access_db import get_connection  # type: ignore

        conn = get_connection(str(store_code))
    except Exception as e:
        result["error"] = f"Errore di connessione al database: {e}"
        return result

    try:
        cur = conn.cursor()
        # Works on both SQL Server and Access
        cur.execute(f"SELECT TOP 1 * FROM [{table}]")
        cols_raw = [d[0] for d in (cur.description or [])]
        result["available_columns"] = cols_raw

        code_col = _find_col(cols_raw, code_want)
        name_col = _find_col(cols_raw, name_want)

        if not code_col:
            result["error"] = (
                f"La colonna codice fornitori '{code_want}' non è stata trovata nella tabella {table}. "
                f"Colonne disponibili: {', '.join(cols_raw)}"
            )
            return result
        if not name_col:
            result["error"] = (
                f"La colonna nome fornitori '{name_want}' non è stata trovata nella tabella {table}. "
                f"Colonne disponibili: {', '.join(cols_raw)}"
            )
            return result

        # Alias output columns to avoid SQL Server ORDER BY ambiguity when code_col == name_col
        sql_all = (
            f"SELECT DISTINCT [{code_col}] AS code, [{name_col}] AS name "
            f"FROM [{table}] "
            f"ORDER BY 1"
        )
        cur.execute(sql_all)
        suppliers = [{"code": r[0], "name": r[1]} for r in cur.fetchall()]

        result["suppliers"] = suppliers
        result["code_column"] = code_col
        result["name_column"] = name_col
        return result
    except Exception as e:
        result["error"] = f"Errore durante la lettura dei fornitori dalla tabella {table}: {e}"
        return result
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _apply_monkey_patches() -> None:
    # Patch delivery_repository if present
    try:
        import delivery_repository  # type: ignore

        # Provide helper in module namespace too (not only builtins)
        setattr(delivery_repository, "_is_tech_col", _is_tech_col)

        # Replace suppliers reader
        setattr(delivery_repository, "get_suppliers_for_store", _patched_get_suppliers_for_store)
    except Exception:
        pass


_apply_monkey_patches()
