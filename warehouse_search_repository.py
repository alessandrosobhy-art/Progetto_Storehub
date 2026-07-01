from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from app_db import get_connection

from date_utils import parse_any_date

# Import interni dai repository esistenti: qui servono per leggere robustamente i campi header.
from delivery_repository import (
    get_delivery_table_name,
    _detect_delivery_layout,  # type: ignore
    _get_odbc_columns_info,  # type: ignore
    _is_date_like_col,  # type: ignore
)

from inventory_repository import (
    get_inventory_table_name,
    get_tx_table_name,
    _resolve_table_name,  # type: ignore
    _get_table_columns,  # type: ignore
    _detect_inventory_layout,  # type: ignore
    _detect_supplier_col,  # type: ignore
)


def _parse_iso_date(s: str) -> Optional[datetime]:
    try:
        return datetime.strptime((s or '').strip(), "%Y-%m-%d")
    except Exception:
        return None


def _to_iso_from_access_date(v: Any) -> str:
    """Convert Access value (datetime/date/str dd/mm/yyyy) to ISO yyyy-mm-dd."""
    if v is None:
        return ""
    if isinstance(v, datetime):
        return v.date().isoformat()
    try:
        # pyodbc può restituire date come datetime.date
        import datetime as _dt

        if isinstance(v, _dt.date):
            return v.isoformat()
    except Exception:
        pass

    s = str(v).strip()
    if not s:
        return ""
    # prova formati comuni
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y", "%m-%d-%Y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except Exception:
            continue
    return ""


def search_ddt_headers(
    *,
    store_code: str,
    start_iso: str,
    end_iso: str,
) -> List[Dict[str, Any]]:
    """Ritorna un elenco di DDT (una riga per movimento) per intervallo di data consegna."""
    start_dt = _parse_iso_date(start_iso)
    end_dt = _parse_iso_date(end_iso)
    if not start_dt or not end_dt:
        return []
    # include tutta la giornata di fine
    end_dt = end_dt.replace(hour=23, minute=59, second=59)

    conn = get_connection(store_code)
    try:
        table = get_delivery_table_name()
        layout = _detect_delivery_layout(conn, table)
        if layout.get("error"):
            return []

        site_col = layout.get("site_col")
        supplier_col = layout.get("supplier_col")
        deliv_col = layout.get("deliv_date_col")
        doc_col = layout.get("doc_date_col")
        if not (site_col and supplier_col and deliv_col and doc_col):
            return []

        col_info = _get_odbc_columns_info(conn, table)
        deliv_is_date = _is_date_like_col(col_info.get(deliv_col))

        cur = conn.cursor()
        rows: List[Tuple[Any, Any, Any]] = []

        # Tentativo 1: filtro SQL diretto
        try:
            if deliv_is_date:
                sql = (
                    f"SELECT [{supplier_col}], [{deliv_col}], [{doc_col}] "
                    f"FROM [{table}] "
                    f"WHERE [{site_col}] = ? AND [{deliv_col}] BETWEEN ? AND ? "
                    f"GROUP BY [{supplier_col}], [{deliv_col}], [{doc_col}] "
                    f"ORDER BY [{deliv_col}] DESC"
                )
                cur.execute(sql, (str(store_code), start_dt, end_dt))
            else:
                # Se deliv_col è testo, proviamo a convertirlo lato Access.
                sql = (
                    f"SELECT [{supplier_col}], [{deliv_col}], [{doc_col}] "
                    f"FROM [{table}] "
                    f"WHERE [{site_col}] = ? AND CDate([{deliv_col}]) BETWEEN ? AND ? "
                    f"GROUP BY [{supplier_col}], [{deliv_col}], [{doc_col}] "
                    f"ORDER BY CDate([{deliv_col}]) DESC"
                )
                cur.execute(sql, (str(store_code), start_dt, end_dt))
            rows = [(r[0], r[1], r[2]) for r in cur.fetchall()]
        except Exception:
            # Fallback: estraiamo dal DB i soli record dello store e filtriamo in Python.
            try:
                cur.execute(
                    f"SELECT [{supplier_col}], [{deliv_col}], [{doc_col}] "
                    f"FROM [{table}] WHERE [{site_col}] = ?",
                    (str(store_code),),
                )
                raw = [(r[0], r[1], r[2]) for r in cur.fetchall()]
                rows = []
                for sup, deliv_v, doc_v in raw:
                    iso = _to_iso_from_access_date(deliv_v)
                    if not iso:
                        continue
                    dt = _parse_iso_date(iso)
                    if not dt:
                        continue
                    if start_dt <= dt <= end_dt:
                        rows.append((sup, deliv_v, doc_v))
            except Exception:
                rows = []

        out: List[Dict[str, Any]] = []
        for sup, deliv_v, doc_v in rows:
            iso_deliv = _to_iso_from_access_date(deliv_v)
            iso_doc = _to_iso_from_access_date(doc_v)
            if not iso_doc:
                iso_doc = iso_deliv
            out.append(
                {
                    "kind": "DDT",
                    "supplier": (sup or "").strip(),
                    "date": iso_deliv,
                    "data_rif": iso_deliv,
                    "data_doc": iso_doc,
                }
            )
        return out
    finally:
        try:
            conn.close()
        except Exception:
            pass


def search_inventory_headers(
    *,
    store_code: str,
    start_iso: str,
    end_iso: str,
    mov_type: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Ritorna un elenco di movimenti inventario/trasferimenti (una riga per header)."""
    start_dt = _parse_iso_date(start_iso)
    end_dt = _parse_iso_date(end_iso)
    if not start_dt or not end_dt:
        return []
    end_dt = end_dt.replace(hour=23, minute=59, second=59)

    mov_type = (mov_type or "").strip().upper() or None

    conn = get_connection(store_code)
    try:
        inv_table = _resolve_table_name(conn, get_inventory_table_name(), extra_candidates=["datiinventario"])  # type: ignore
        tx_table = _resolve_table_name(conn, get_tx_table_name(), extra_candidates=["DatiTX", "datitx"])  # type: ignore

        out: List[Dict[str, Any]] = []
        def _query_table(table: str, require_site2: bool) -> None:
            cols = _get_table_columns(conn, table)  # type: ignore
            layout = _detect_inventory_layout(cols, require_site2=require_site2)  # type: ignore
            supplier_col = _detect_supplier_col(cols)  # type: ignore
            if not supplier_col:
                return

            site_col = layout["site_col"]
            date_col = layout["date_col"]
            mov_col = layout["mov_col"]
            site2_col = (layout.get("site2_col") or "").strip()

            is_tx = bool(require_site2)

            cur = conn.cursor()
            rows_db: List[Tuple[Any, ...]] = []

            try:
                if is_tx:
                    # Trasferimenti: leggiamo da DatiTx (serve SITE2). In ricerca mostriamo/modifichiamo
                    # solo i record inseriti dallo store (Site = store_code).
                    if not site2_col:
                        return

                    where = [f"[{date_col}] BETWEEN ? AND ?", f"[{site_col}] = ?"]
                    params: List[Any] = [start_dt, end_dt, str(store_code)]

                    if mov_type in ("TXIN", "TXOUT"):
                        where.append(f"[{mov_col}] = ?")
                        params.append(mov_type)
                    else:
                        # mov_type None: includiamo solo trasferimenti (TXIN/TXOUT) inseriti dallo store
                        where.append(f"([{mov_col}] = ? OR [{mov_col}] = ?)")
                        params.extend(["TXIN", "TXOUT"])

                    select = [supplier_col, date_col, mov_col, site_col, site2_col]
                    group = list(select)

                    sql = (
                        "SELECT "
                        + ", ".join(f"[{c}]" for c in select)
                        + f" FROM [{table}] WHERE "
                        + " AND ".join(where)
                        + " GROUP BY "
                        + ", ".join(f"[{c}]" for c in group)
                        + f" ORDER BY [{date_col}] DESC"
                    )
                    cur.execute(sql, params)
                else:
                    where = [f"[{site_col}] = ?", f"[{date_col}] BETWEEN ? AND ?"]
                    params = [str(store_code), start_dt, end_dt]
                    if mov_type:
                        where.append(f"[{mov_col}] = ?")
                        params.append(mov_type)

                    select = [supplier_col, date_col, mov_col]
                    group = list(select)
                    sql = (
                        "SELECT "
                        + ", ".join(f"[{c}]" for c in select)
                        + f" FROM [{table}] WHERE "
                        + " AND ".join(where)
                        + " GROUP BY "
                        + ", ".join(f"[{c}]" for c in group)
                        + f" ORDER BY [{date_col}] DESC"
                    )
                    cur.execute(sql, params)

                rows_db = cur.fetchall()
            except Exception:
                # Fallback per date salvate come testo/localizzate: filtriamo in Python.
                select_fb = [supplier_col, date_col, mov_col]
                where_fb: List[str] = []
                params_fb: List[Any] = []

                if is_tx:
                    if not site2_col:
                        return
                    select_fb += [site_col, site2_col]

                    # Solo record inseriti dallo store (Site=store_code)
                    where_fb.append(f"[{site_col}] = ?")
                    params_fb.append(str(store_code))

                    if mov_type in ("TXIN", "TXOUT"):
                        where_fb.append(f"[{mov_col}] = ?")
                        params_fb.append(mov_type)
                    else:
                        where_fb.append(f"([{mov_col}] = ? OR [{mov_col}] = ?)")
                        params_fb.extend(["TXIN", "TXOUT"])
                else:
                    where_fb.append(f"[{site_col}] = ?")
                    params_fb.append(str(store_code))
                    if mov_type:
                        where_fb.append(f"[{mov_col}] = ?")
                        params_fb.append(mov_type)

                sql_fb = "SELECT " + ", ".join(f"[{c}]" for c in select_fb) + f" FROM [{table}]"
                if where_fb:
                    sql_fb += " WHERE " + " AND ".join(where_fb)

                try:
                    cur.execute(sql_fb, params_fb)
                    raw_rows = cur.fetchall()
                except Exception:
                    raw_rows = []

                rows_db = []
                for rr in raw_rows:
                    d_raw = rr[1] if len(rr) > 1 else None
                    d_parsed = parse_any_date(d_raw)
                    if not d_parsed:
                        continue
                    d_dt = datetime(d_parsed.year, d_parsed.month, d_parsed.day)
                    if start_dt <= d_dt <= end_dt:
                        rows_db.append(rr)

            for r in rows_db:
                if is_tx:
                    # r: sup, date, mov, site, site2
                    sup, d, mt, _s_from, s_to = r[0], r[1], r[2], r[3], r[4]
                    mt_str = (str(mt).strip().upper() if mt is not None else "")
                    if mt_str not in ("TXIN", "TXOUT"):
                        continue

                    out.append(
                        {
                            "kind": mt_str,
                            "supplier": (sup or "").strip(),
                            "date": _to_iso_from_access_date(d),
                            "data_mov": _to_iso_from_access_date(d),
                            "mov_type": mt_str,
                            "site2": (str(s_to).strip() if s_to is not None else ""),
                        }
                    )
                else:
                    sup, d, mt = r[0], r[1], r[2]
                    mt_str = (mt or "").strip()

                    # TXIN/TXOUT vengono mostrati solo da DatiTx (serve SITE2)
                    if mt_str.upper() in ("TXIN", "TXOUT"):
                        continue

                    out.append(
                        {
                            "kind": mt_str,
                            "supplier": (sup or "").strip(),
                            "date": _to_iso_from_access_date(d),
                            "data_mov": _to_iso_from_access_date(d),
                            "mov_type": mt_str,
                            "site2": "",
                        }
                    )

        # Movimenti inventario: li leggiamo da DatiInventario.
        # Nota: TXIN/TXOUT vengono mostrati SOLO da DatiTX (per avere SITE2).
        if mov_type not in ("TXIN", "TXOUT"):
            _query_table(inv_table, require_site2=False)

        # Trasferimenti: da DatiTX (serve SITE2)
        if mov_type in (None, "TXIN", "TXOUT"):
            _query_table(tx_table, require_site2=True)

        # dedup: stessa chiave può apparire in inv_table e tx_table (per TX)
        seen = set()
        deduped: List[Dict[str, Any]] = []
        for r in sorted(out, key=lambda x: (x.get("date") or "", x.get("supplier") or ""), reverse=True):
            k = (
                (r.get("mov_type") or "").upper(),
                (r.get("supplier") or "").lower(),
                r.get("date") or "",
                (r.get("site2") or "").strip(),
            )
            if k in seen:
                continue
            seen.add(k)
            deduped.append(r)
        return deduped
    finally:
        try:
            conn.close()
        except Exception:
            pass
