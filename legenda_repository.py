from __future__ import annotations

import re
from typing import Any, Dict, List

from app_db import get_connection, supports_schema_alter


def _qname(name: str) -> str:
    return f"[{str(name).replace(']', ']]')}]"


def _access_has_table(cur, table_name: str) -> bool:
    t = str(table_name).strip().lower()
    for row in cur.tables(tableType="TABLE"):
        n = getattr(row, "table_name", None) or (len(row) > 2 and row[2]) or None
        if n and str(n).strip().lower() == t:
            return True
    return False


def _get_table_columns_ordered(cur, table_name: str) -> List[str]:
    cols: List[str] = []
    for row in cur.columns(table=table_name):
        n = getattr(row, "column_name", None) or (len(row) > 3 and row[3]) or None
        if n:
            cols.append(str(n))
    return cols


def _ensure_legenda_table(conn) -> None:
    if not supports_schema_alter():
        return
    cur = conn.cursor()
    if not _access_has_table(cur, "LEGENDA"):
        sql = """
        CREATE TABLE LEGENDA (
            Site TEXT(20),
            nomelegenda TEXT(255),
            colorelegenda TEXT(20)
        )
        """.strip()
        cur.execute(sql)
        conn.commit()
        return

    cols = _get_table_columns_ordered(cur, "LEGENDA")
    low = [c.lower() for c in cols]
    if "site" not in low:
        try:
            cur.execute(f"ALTER TABLE {_qname('LEGENDA')} ADD COLUMN Site TEXT(20)")
            conn.commit()
        except Exception:
            pass
    if "nomelegenda" not in low:
        try:
            cur.execute(f"ALTER TABLE {_qname('LEGENDA')} ADD COLUMN nomelegenda TEXT(255)")
            conn.commit()
        except Exception:
            pass
    if "colorelegenda" not in low:
        try:
            cur.execute(f"ALTER TABLE {_qname('LEGENDA')} ADD COLUMN colorelegenda TEXT(20)")
            conn.commit()
        except Exception:
            pass


def _norm_hex(color: str) -> str:
    c = (color or "").strip()
    if not c:
        return ""
    if re.match(r"^#[0-9a-fA-F]{6}$", c):
        return c.lower()
    return ""


# Colori consentiti: devono coincidere con quelli selezionabili nella pagina Orari (quadrati prestabiliti)
ALLOWED_LEGENDA_COLORS = {
    "#f8f9fa",
    "#e9ecef",
    "#fff3cd",
    "#d1e7dd",
    "#cff4fc",
    "#f8d7da",
    "#ffe5d0",
    "#e2d9f3",
    "#d2f4ea",
    "#f7d6e6",
}


def list_legenda(*, store_code: str) -> List[Dict[str, Any]]:
    conn = get_connection(store_code)
    try:
        _ensure_legenda_table(conn)
        cur = conn.cursor()
        cols = _get_table_columns_ordered(cur, "LEGENDA")
        low = [c.lower() for c in cols]

        # colonne attese
        col_site = cols[low.index("site")] if "site" in low else None
        col_nome = cols[low.index("nomelegenda")] if "nomelegenda" in low else None
        col_colore = cols[low.index("colorelegenda")] if "colorelegenda" in low else None

        select_cols: List[str] = []
        if col_site:
            select_cols.append(_qname(col_site))
        if col_nome:
            select_cols.append(_qname(col_nome))
        if col_colore:
            select_cols.append(_qname(col_colore))

        where_sql = ""
        params: List[Any] = []
        if col_site:
            where_sql = f" WHERE {_qname(col_site)} = ?"
            params.append(str(store_code))

        order_sql = f" ORDER BY {_qname(col_nome)}" if col_nome else ""
        sql = f"SELECT {', '.join(select_cols)} FROM {_qname('LEGENDA')}{where_sql}{order_sql}"
        cur.execute(sql, params)

        out: List[Dict[str, Any]] = []
        for row in cur.fetchall() or []:
            # offset dipende dalla presenza di site
            off = 1 if col_site else 0
            nome = (row[off + 0] or "").strip() if col_nome else ""
            colore = (row[off + 1] or "").strip() if col_colore else ""
            out.append(
                {
                    "nomelegenda": nome,
                    "colorelegenda": _norm_hex(colore) or colore,
                }
            )

        return out
    finally:
        try:
            conn.close()
        except Exception:
            pass


def insert_legenda(*, store_code: str, nomelegenda: str, colorelegenda: str) -> bool:
    conn = get_connection(store_code)
    try:
        _ensure_legenda_table(conn)
        cur = conn.cursor()
        nome = (nomelegenda or "").strip()
        col = (_norm_hex(colorelegenda) or (colorelegenda or "").strip()).lower()
        if not nome or not col:
            return False
        if col not in ALLOWED_LEGENDA_COLORS:
            return False
        sql = f"INSERT INTO {_qname('LEGENDA')} ({_qname('Site')},{_qname('nomelegenda')},{_qname('colorelegenda')}) VALUES (?,?,?)"
        cur.execute(sql, [str(store_code), nome, col])
        conn.commit()
        return True
    finally:
        try:
            conn.close()
        except Exception:
            pass

    return False


def delete_legenda(*, store_code: str, nomelegenda: str, colorelegenda: str) -> bool:
    conn = get_connection(store_code)
    try:
        _ensure_legenda_table(conn)
        cur = conn.cursor()
        nome = (nomelegenda or "").strip()
        col = (colorelegenda or "").strip()
        if not nome or not col:
            return False
        sql = f"DELETE FROM {_qname('LEGENDA')} WHERE {_qname('Site')}=? AND {_qname('nomelegenda')}=? AND {_qname('colorelegenda')}=?"
        cur.execute(sql, [str(store_code), nome, col])
        conn.commit()
        return bool(getattr(cur, "rowcount", 0) or 0)
    finally:
        try:
            conn.close()
        except Exception:
            pass
