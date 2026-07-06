from __future__ import annotations

from app_logging import log_swallowed
import os
import re
import csv
import io
from typing import Any, Dict, List, Tuple, Optional

from app_db import get_connection, get_backend
from supplier_orders_repository import (
    ensure_supplier_orders_schema,
    migrate_legacy_pricelists,
    load_pricelist as load_unified_pricelist,
    upsert_pricelist_rows,
)


def _norm(s: Any) -> str:
    return str(s or "").strip().lower()


def _find_col(cols: List[str], wanted: str) -> Optional[str]:
    if not wanted:
        return None
    w = _norm(wanted)
    for c in cols:
        if _norm(c) == w:
            return c
    return None


def _find_col_by_keywords(cols: List[str], keywords: List[str]) -> Optional[str]:
    kws = [_norm(k) for k in (keywords or []) if k]
    for c in cols:
        lc = _norm(c)
        if any(k in lc for k in kws):
            return c
    return None


def _normalize_key(s: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", _norm(s))


def _is_tech_col(col_name: str) -> bool:
    n = _normalize_key(col_name)
    if not n:
        return False
    if n in ("rowuuid", "uuid", "createdat", "updatedat", "insertedat", "modifiedat"):
        return True
    if n.endswith("uuid"):
        return True
    return False


def _find_site_col(cols: List[str]) -> Optional[str]:
    wanted = {
        "site",
        "store",
        "storecode",
        "storeid",
        "codicestore",
        "codicenegozio",
        "negozio",
        "sitecode",
    }
    for c in cols:
        if _normalize_key(c) in wanted:
            return c
    return None


def _ui_visible_cols(cols: List[str]) -> List[str]:
    site_col = _find_site_col(cols)
    return [c for c in (cols or []) if not _is_tech_col(c) and c != site_col]


_NUM_RE = re.compile(r"^\s*[-+]?(\d+([.,]\d+)?|(\d{1,3}([.\s]\d{3})+)([.,]\d+)?)\s*$")


def _looks_numeric(s: str) -> bool:
    if s is None:
        return False
    s = str(s).strip()
    if not s:
        return False
    s = s.replace("€", "").replace("\u20ac", "").replace(" ", "")
    # Allow standard EU/US thousands+decimal patterns
    return _NUM_RE.match(s) is not None


def _parse_number_any(v: Any) -> float:
    """Parse numbers written as:
    - '6,48' -> 6.48
    - '1.234,56' -> 1234.56
    - '1,234.56' -> 1234.56  (best-effort)
    """
    if v is None:
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if not s:
        return 0.0
    s = s.replace("€", "").replace("\u20ac", "").replace(" ", "")

    # If both separators appear, choose last as decimal separator
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            # EU: 1.234,56
            s = s.replace(".", "").replace(",", ".")
        else:
            # US: 1,234.56
            s = s.replace(",", "")
    else:
        # Single separator, interpret comma as decimal
        s = s.replace(",", ".")
    try:
        return float(s)
    except Exception:
        return 0.0


def _get_table_columns(conn, table_name: str) -> List[str]:
    cur = conn.cursor()
    cur.execute(f"SELECT TOP 1 * FROM [{table_name}]")
    cols = [d[0] for d in cur.description]
    cur.close()
    return cols


def _get_table_schema(conn, table_name: str) -> Dict[str, Dict[str, Any]]:
    """Schema map keyed by normalized column name.

    Uses ODBC cursor.columns when available (Windows Access ODBC). If it fails, returns empty dict.
    """
    schema: Dict[str, Dict[str, Any]] = {}
    cur = conn.cursor()
    try:
        # pyodbc columns(): (table_cat, table_schem, table_name, column_name, data_type, type_name, ...)
        for r in cur.columns(table=table_name):
            try:
                col_name = r[3]
                data_type = r[4]
                type_name = r[5]
            except Exception:
                continue
            if col_name is None:
                continue
            schema[_norm(col_name)] = {
                "data_type": int(data_type) if data_type is not None else 0,
                "type_name": str(type_name or ""),
            }
    except Exception:
        schema = {}
    finally:
        try:
            cur.close()
        except Exception:
            log_swallowed('listini_repository:160')
    return schema


def _is_numeric_schema(info: Optional[Dict[str, Any]]) -> bool:
    """Return True only for real numeric types.

    IMPORTANT: do not treat 'LONGCHAR'/'LONGTEXT' as numeric (previous versions did due to 'long' keyword).
    Prefer data_type codes.
    """
    if not info:
        return False
    dt = int(info.get("data_type") or 0)
    tn = _norm(info.get("type_name") or "")

    # ODBC numeric-ish SQL types
    if dt in (2, 3, 4, 5, 6, 7, 8, -6, -5):  # NUMERIC, DECIMAL, INTEGER, SMALLINT, FLOAT, REAL, DOUBLE, TINYINT, BIGINT
        return True

    # Fallback on type_name (but avoid generic 'long')
    if any(k in tn for k in ["decimal", "numeric", "double", "float", "real", "currency", "money", "integer", "smallint", "tinyint", "bigint"]):
        return True

    return False


def _is_integer_schema(info: Optional[Dict[str, Any]]) -> bool:
    if not info:
        return False
    dt = int(info.get("data_type") or 0)
    tn = _norm(info.get("type_name") or "")

    if dt in (4, 5, -6, -5):  # INTEGER, SMALLINT, TINYINT, BIGINT
        return True

    if any(k in tn for k in ["integer", "smallint", "tinyint", "bigint", "byte"]):
        return True

    return False


def _coerce_value_for_column(v: Any, col_info: Optional[Dict[str, Any]]) -> Any:
    """Coerce UI values into proper Python types for Access/ODBC.

    Key points:
    - EU decimals like '6,48' must be converted to float for numeric columns.
    - BUT: if the value is NOT numeric-looking, keep it as text (avoid turning 'PZ' into 0).
    """
    if v is None:
        return None

    # Keep booleans
    if isinstance(v, bool):
        return v

    if isinstance(v, str):
        s = v.strip()
        if not s:
            return None

        if _is_numeric_schema(col_info):
            # Only parse if it looks numeric; otherwise keep string to avoid silent 0
            if _looks_numeric(s):
                num = _parse_number_any(s)
                if _is_integer_schema(col_info):
                    try:
                        return int(round(num))
                    except Exception:
                        return int(num)
                return float(num)
            return s

        return s

    if isinstance(v, (int, float)):
        if _is_numeric_schema(col_info):
            if _is_integer_schema(col_info):
                return int(v)
            return float(v)
        return v

    return v


def _resolve_listino_table(listino_type: str) -> str:
    food_table = os.getenv("ACCESS_PRICELIST_FOOD_TABLE", "FoodPaper")
    oper_table = os.getenv("ACCESS_PRICELIST_OPER_TABLE", "Operating")
    return food_table if listino_type == "FoodPaper" else oper_table


def _resolve_conv_table_name(conn) -> str:
    env = os.getenv("ACCESS_FP_CONV_TABLE", "").strip()
    if env:
        return env
    return "FP CONV"


def _detect_conv_cols(conn, table_name: str) -> Dict[str, str]:
    cols = _get_table_columns(conn, table_name)
    descr = _find_col_by_keywords(cols, ["descr"])
    supplier = _find_col_by_keywords(cols, ["fornit", "supplier"])
    group = _find_col_by_keywords(cols, ["grupp"])
    conv = _find_col_by_keywords(cols, ["conv"])
    return {"descr": descr or "", "supplier": supplier or "", "group": group or "", "conv": conv or "", "cols": cols}


def _load_conv_maps(conn, table_name: str, source_store_code: str = "") -> Tuple[Dict[Tuple[str, str], float], Dict[str, float], Dict[str, str]]:
    layout = _detect_conv_cols(conn, table_name)
    descr_col = layout["descr"]
    conv_col = layout["conv"]
    supplier_col = layout["supplier"]
    site_col = _find_site_col(layout.get("cols", []))
    if not descr_col or not conv_col:
        return {}, {}, layout

    cur = conn.cursor()
    try:
        if site_col and str(source_store_code or "").strip():
            cur.execute(f"SELECT * FROM [{table_name}] WHERE [{site_col}]=?", (str(source_store_code).strip(),))
        else:
            cur.execute(f"SELECT * FROM [{table_name}]")
    except Exception:
        cur.close()
        return {}, {}, layout

    cols = [d[0] for d in cur.description]
    by_supplier_descr: Dict[Tuple[str, str], float] = {}
    by_descr: Dict[str, float] = {}

    while True:
        row = cur.fetchone()
        if row is None:
            break
        rec = dict(zip(cols, row))
        d = _norm(rec.get(descr_col))
        if not d:
            continue
        cval = _parse_number_any(rec.get(conv_col))
        by_descr.setdefault(d, cval)
        if supplier_col:
            s = _norm(rec.get(supplier_col))
            if s:
                by_supplier_descr[(s, d)] = cval

    cur.close()
    return by_supplier_descr, by_descr, layout

def load_admin_pricelist(listino_type: str, source_store_code: str = "9001", max_rows: int = 20000) -> Dict[str, Any]:
    """Load full pricelist (FoodPaper or Operating) from SQL/DB and add virtual column CONV."""
    if str(get_backend() or "").strip().lower() == "sqlserver":
        ensure_supplier_orders_schema()
        migrate_legacy_pricelists()
        return load_unified_pricelist(listino_type)

    listino_type = (listino_type or "").strip() or "FoodPaper"
    if listino_type not in ("FoodPaper", "Operating"):
        listino_type = "FoodPaper"

    table_name = _resolve_listino_table(listino_type)

    out: Dict[str, Any] = {
        "ok": False,
        "source_store": source_store_code,
        "listino_type": listino_type,
        "table": table_name,
        "columns": [],
        "rows": [],
        "key_column": "",
        "desc_column": "",
        "supplier_column": "",
        "conv_table": "",
        "error": None,
    }

    conn = None
    try:
        conn = get_connection(source_store_code)
        raw_cols = _get_table_columns(conn, table_name)
        site_col = _find_site_col(raw_cols)
        cols = _ui_visible_cols(raw_cols)

        desc_col_cfg = os.getenv("ACCESS_PRICELIST_DESC_COL", "").strip()
        supplier_col_cfg = os.getenv("ACCESS_PRICELIST_SUPPLIER_COL", "").strip()
        desc_col = _find_col(cols, desc_col_cfg) or _find_col_by_keywords(cols, ["descr"])
        supplier_col = _find_col(cols, supplier_col_cfg) or _find_col_by_keywords(cols, ["fornit", "supplier"])

        if not desc_col:
            desc_col = cols[0] if cols else ""

        out["desc_column"] = desc_col
        out["supplier_column"] = supplier_col or ""
        out["key_column"] = desc_col

        conv_sd: Dict[Tuple[str, str], float] = {}
        conv_d: Dict[str, float] = {}
        conv_table = ""
        if listino_type == "FoodPaper":
            conv_table = _resolve_conv_table_name(conn)
            try:
                conv_sd, conv_d, _ = _load_conv_maps(conn, conv_table, source_store_code=source_store_code)
            except Exception:
                conv_sd, conv_d = {}, {}
        out["conv_table"] = conv_table

        order_parts = []
        if supplier_col:
            order_parts.append(f"[{supplier_col}]")
        if desc_col:
            order_parts.append(f"[{desc_col}]")
        order_sql = ", ".join(order_parts) if order_parts else ""

        cur = conn.cursor()
        if site_col and str(source_store_code or "").strip():
            sql = f"SELECT * FROM [{table_name}] WHERE [{site_col}]=?"
            if order_sql:
                sql += f" ORDER BY {order_sql}"
            cur.execute(sql, (str(source_store_code).strip(),))
        elif order_sql:
            cur.execute(f"SELECT * FROM [{table_name}] ORDER BY {order_sql}")
        else:
            cur.execute(f"SELECT * FROM [{table_name}]")

        colnames = [d[0] for d in cur.description]
        rows: List[Dict[str, Any]] = []
        n = 0
        while True:
            rec = cur.fetchone()
            if rec is None:
                break
            n += 1
            if n > max_rows:
                break
            raw_row = dict(zip(colnames, rec))
            row = {c: raw_row.get(c) for c in cols}

            if listino_type == "FoodPaper":
                descr_v = _norm(row.get(desc_col))
                supp_v = _norm(row.get(supplier_col)) if supplier_col else ""
                conv_val = 0.0
                if supp_v and descr_v and (supp_v, descr_v) in conv_sd:
                    conv_val = conv_sd[(supp_v, descr_v)]
                elif descr_v and descr_v in conv_d:
                    conv_val = conv_d[descr_v]
                row["CONV"] = conv_val

            rows.append(row)

        cur.close()

        out_cols = cols[:]
        if listino_type == "FoodPaper" and "CONV" not in out_cols:
            out_cols.append("CONV")

        out["columns"] = out_cols
        out["rows"] = rows
        out["ok"] = True
        return out
    except Exception as e:
        out["error"] = str(e)
        return out
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            log_swallowed('listini_repository:425')

def _row_get_case_insensitive(row: Dict[str, Any], col: str) -> Any:
    if col in row:
        return row.get(col)
    target = _norm(col)
    for k, v in row.items():
        if _norm(k) == target:
            return v
    return None


def _upsert_conv(
    cur,
    conv_table: str,
    conv_layout: Dict[str, str],
    supplier_val: str,
    descr_val: str,
    group_val: Any,
    conv_val: Any,
    site_val: str = "",
) -> None:
    descr_col = conv_layout.get("descr") or _find_col_by_keywords(conv_layout.get("cols", []), ["descr"]) or "DESCRIZIONE"
    supplier_col = conv_layout.get("supplier") or _find_col_by_keywords(conv_layout.get("cols", []), ["fornit"]) or "FORNITORE"
    group_col = conv_layout.get("group") or _find_col_by_keywords(conv_layout.get("cols", []), ["grupp"]) or "GRUPPO"
    conv_col = conv_layout.get("conv") or _find_col_by_keywords(conv_layout.get("cols", []), ["conv"]) or "CONV"
    site_col = _find_site_col(conv_layout.get("cols", []))

    if site_col and site_val:
        cur.execute(
            f"SELECT COUNT(*) FROM [{conv_table}] WHERE [{site_col}]=? AND [{supplier_col}]=? AND [{descr_col}]=?",
            (site_val, supplier_val, descr_val),
        )
    else:
        cur.execute(f"SELECT COUNT(*) FROM [{conv_table}] WHERE [{supplier_col}]=? AND [{descr_col}]=?", (supplier_val, descr_val))
    exists = (cur.fetchone()[0] or 0) > 0

    if exists:
        if site_col and site_val:
            cur.execute(
                f"UPDATE [{conv_table}] SET [{group_col}]=?, [{conv_col}]=? WHERE [{site_col}]=? AND [{supplier_col}]=? AND [{descr_col}]=?",
                (group_val, conv_val, site_val, supplier_val, descr_val),
            )
        else:
            cur.execute(
                f"UPDATE [{conv_table}] SET [{group_col}]=?, [{conv_col}]=? WHERE [{supplier_col}]=? AND [{descr_col}]=?",
                (group_val, conv_val, supplier_val, descr_val),
            )
    else:
        if site_col and site_val:
            cur.execute(
                f"INSERT INTO [{conv_table}] ([{site_col}], [{supplier_col}], [{descr_col}], [{group_col}], [{conv_col}]) VALUES (?,?,?,?,?)",
                (site_val, supplier_val, descr_val, group_val, conv_val),
            )
        else:
            cur.execute(
                f"INSERT INTO [{conv_table}] ([{supplier_col}], [{descr_col}], [{group_col}], [{conv_col}]) VALUES (?,?,?,?)",
                (supplier_val, descr_val, group_val, conv_val),
            )

def apply_pricelist_to_stores(
    listino_type: str,
    rows: List[Dict[str, Any]],
    columns: List[str],
    store_codes: List[str],
    source_store_code: str = "9001",
) -> Dict[str, Any]:
    """Apply edited pricelist to target DBs/tables.

    In SQL Server shared mode the same DB is used for all stores, with store segregation on the
    detected site/store column when present. Technical columns like row_uuid stay DB-managed.
    """
    if str(get_backend() or "").strip().lower() == "sqlserver":
        ensure_supplier_orders_schema()
        migrate_legacy_pricelists()
        return upsert_pricelist_rows(listino_type, rows or [])

    listino_type = (listino_type or "").strip() or "FoodPaper"
    if listino_type not in ("FoodPaper", "Operating"):
        listino_type = "FoodPaper"

    table_name = _resolve_listino_table(listino_type)

    result: Dict[str, Any] = {
        "ok": False,
        "stores_total": len(store_codes or []),
        "stores_ok": 0,
        "stores_fail": 0,
        "stores": [],
        "error": None,
    }

    try:
        from app_db import get_backend as _get_backend  # type: ignore
    except Exception:
        _get_backend = lambda: "access"  # type: ignore

    shared_sql_mode = str(_get_backend() or "").strip().lower() == "sqlserver"
    shared_conn = None

    try:
        if shared_sql_mode:
            shared_conn = get_connection(None)

        for sc in (store_codes or []):
            sc = str(sc)
            store_res = {"store": sc, "ok": False, "updates": 0, "inserts": 0, "deletes": 0, "conv_updates": 0, "conv_inserts": 0, "conv_deletes": 0, "error": None}
            conn = shared_conn if shared_conn is not None else None
            own_conn = False
            try:
                if conn is None:
                    conn = get_connection(sc)
                    own_conn = True
                cur = conn.cursor()

                tgt_all_cols = _get_table_columns(conn, table_name)
                tgt_cols = _ui_visible_cols(tgt_all_cols)
                schema_map = _get_table_schema(conn, table_name)
                site_col = _find_site_col(tgt_all_cols)

                supplier_col = _find_col_by_keywords(tgt_cols, ["fornit", "supplier"])
                desc_col = _find_col_by_keywords(tgt_cols, ["descr"])
                group_col = _find_col_by_keywords(tgt_cols, ["grupp"])

                if not desc_col:
                    desc_col = tgt_cols[0] if tgt_cols else ""

                conv_table = ""
                conv_layout: Dict[str, str] = {}
                if listino_type == "FoodPaper":
                    conv_table = _resolve_conv_table_name(conn)
                    try:
                        conv_layout = _detect_conv_cols(conn, conv_table)
                    except Exception:
                        conv_layout = {}

                write_cols: List[str] = []
                for c in (columns or []):
                    if _norm(c) == "conv" or _is_tech_col(c):
                        continue
                    real = _find_col(tgt_cols, c) or (c if c in tgt_cols else None)
                    if real and real not in write_cols:
                        write_cols.append(real)

                ops = 0
                for row in (rows or []):
                    descr_val = _row_get_case_insensitive(row, desc_col) if desc_col else None
                    supp_val = _row_get_case_insensitive(row, supplier_col) if supplier_col else None

                    descr_val_s = str(descr_val or "").strip()
                    supp_val_s = str(supp_val or "").strip()

                    is_deleted = False
                    try:
                        if isinstance(row, dict):
                            if row.get("__deleted") or row.get("_deleted"):
                                is_deleted = True
                            elif str(row.get("__action") or "").strip().lower() == "delete":
                                is_deleted = True
                    except Exception:
                        is_deleted = False

                    where_sql = []
                    where_params: List[Any] = []
                    if site_col:
                        where_sql.append(f"[{site_col}]=?")
                        where_params.append(sc)
                    if supplier_col and desc_col:
                        if not descr_val_s or not supp_val_s:
                            continue
                        where_sql.extend([f"[{supplier_col}]=?", f"[{desc_col}]=?"])
                        where_params.extend([supp_val_s, descr_val_s])
                    else:
                        if not descr_val_s:
                            continue
                        where_sql.append(f"[{desc_col}]=?")
                        where_params.append(descr_val_s)
                    where_clause = " AND ".join(where_sql)

                    if is_deleted:
                        cur.execute(f"DELETE FROM [{table_name}] WHERE {where_clause}", where_params)
                        store_res["deletes"] += 1

                        if listino_type == "FoodPaper" and conv_table:
                            try:
                                descr_c = conv_layout.get("descr") or _find_col_by_keywords(conv_layout.get("cols", []), ["descr"])
                                supp_c = conv_layout.get("supplier") or _find_col_by_keywords(conv_layout.get("cols", []), ["fornit"])
                                conv_site_col = _find_site_col(conv_layout.get("cols", []))
                                if descr_c and supp_c:
                                    conv_where_sql = []
                                    conv_where_params: List[Any] = []
                                    if conv_site_col:
                                        conv_where_sql.append(f"[{conv_site_col}]=?")
                                        conv_where_params.append(sc)
                                    conv_where_sql.extend([f"[{supp_c}]=?", f"[{descr_c}]=?"])
                                    conv_where_params.extend([supp_val_s, descr_val_s])
                                    cur.execute(f"DELETE FROM [{conv_table}] WHERE {' AND '.join(conv_where_sql)}", conv_where_params)
                                    store_res["conv_deletes"] += 1
                            except Exception:
                                log_swallowed('listini_repository:624')

                        ops += 1
                        if ops % 200 == 0:
                            conn.commit()
                        continue

                    cur.execute(f"SELECT COUNT(*) FROM [{table_name}] WHERE {where_clause}", where_params)
                    exists = (cur.fetchone()[0] or 0) > 0

                    vals: Dict[str, Any] = {}
                    for c in write_cols:
                        raw_val = _row_get_case_insensitive(row, c)
                        vals[c] = _coerce_value_for_column(raw_val, schema_map.get(_norm(c)))

                    if site_col:
                        vals[site_col] = sc
                    if supplier_col:
                        vals[supplier_col] = supp_val_s
                    if desc_col:
                        vals[desc_col] = descr_val_s

                    if exists:
                        set_cols = [c for c in vals.keys() if c not in (site_col, supplier_col, desc_col) and not _is_tech_col(c)]
                        if set_cols:
                            set_sql = ", ".join([f"[{c}]=?" for c in set_cols])
                            params = [vals[c] for c in set_cols] + where_params
                            cur.execute(f"UPDATE [{table_name}] SET {set_sql} WHERE {where_clause}", params)
                            store_res["updates"] += 1
                    else:
                        ins_cols = [c for c in vals.keys() if not _is_tech_col(c)]
                        ph = ", ".join(["?"] * len(ins_cols))
                        cols_sql = ", ".join([f"[{c}]" for c in ins_cols])
                        cur.execute(f"INSERT INTO [{table_name}] ({cols_sql}) VALUES ({ph})", [vals[c] for c in ins_cols])
                        store_res["inserts"] += 1

                    if listino_type == "FoodPaper" and conv_table:
                        raw_conv = _row_get_case_insensitive(row, "CONV")
                        raw_conv_s = str(raw_conv).strip() if raw_conv is not None else ""
                        if raw_conv is not None and raw_conv_s != "":
                            conv_val_num = _parse_number_any(raw_conv_s)
                            group_val = _row_get_case_insensitive(row, group_col) if group_col else _row_get_case_insensitive(row, "GRUPPO")
                            try:
                                descr_c = conv_layout.get("descr") or _find_col_by_keywords(conv_layout.get("cols", []), ["descr"])
                                supp_c = conv_layout.get("supplier") or _find_col_by_keywords(conv_layout.get("cols", []), ["fornit"])
                                conv_site_col = _find_site_col(conv_layout.get("cols", []))
                                if descr_c and supp_c:
                                    conv_where_sql = []
                                    conv_where_params: List[Any] = []
                                    if conv_site_col:
                                        conv_where_sql.append(f"[{conv_site_col}]=?")
                                        conv_where_params.append(sc)
                                    conv_where_sql.extend([f"[{supp_c}]=?", f"[{descr_c}]=?"])
                                    conv_where_params.extend([supp_val_s, descr_val_s])
                                    cur.execute(f"SELECT COUNT(*) FROM [{conv_table}] WHERE {' AND '.join(conv_where_sql)}", conv_where_params)
                                    conv_exists = (cur.fetchone()[0] or 0) > 0
                                else:
                                    conv_exists = False

                                _upsert_conv(cur, conv_table, conv_layout, supp_val_s, descr_val_s, group_val, conv_val_num, site_val=sc)
                                if conv_exists:
                                    store_res["conv_updates"] += 1
                                else:
                                    store_res["conv_inserts"] += 1
                            except Exception:
                                log_swallowed('listini_repository:689')

                    ops += 1
                    if ops % 200 == 0:
                        conn.commit()

                conn.commit()
                store_res["ok"] = True
                result["stores_ok"] += 1
                result["stores"].append(store_res)
            except Exception as e:
                store_res["error"] = str(e)
                result["stores_fail"] += 1
                result["stores"].append(store_res)
                try:
                    if conn is not None:
                        conn.rollback()
                except Exception:
                    log_swallowed('listini_repository:707')
            finally:
                try:
                    if own_conn and conn is not None:
                        conn.close()
                except Exception:
                    log_swallowed('listini_repository:713')
    finally:
        try:
            if shared_conn is not None:
                shared_conn.close()
        except Exception:
            log_swallowed('listini_repository:719')

    result["ok"] = result["stores_fail"] == 0
    if not result["ok"] and not result["error"]:
        result["error"] = "Uno o pi? store non sono stati aggiornati."
    return result

def parse_pricelist_csv(
    csv_bytes: bytes,
    target_columns: List[str],
    desc_col: str,
    supplier_col: str = "",
) -> Dict[str, Any]:
    """Parse CSV rows to be merged into admin pricelist UI.

    Returns rows containing only the mapped columns (keys included). Does not write to DB.
    - If supplier_col is provided (non-empty), both supplier+desc are required for a row to be valid.
    - If supplier_col is empty, only desc is required.
    """

    def _decode(b: bytes) -> str:
        for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
            try:
                return b.decode(enc)
            except Exception:
                continue
        # last resort
        return b.decode("utf-8", errors="replace")

    def _hnorm(h: str) -> str:
        h = (h or "").strip().lower()
        return re.sub(r"[^a-z0-9]+", "", h)

    text = _decode(csv_bytes or b"")
    if not text.strip():
        return {"ok": False, "error": "CSV vuoto o non leggibile."}

    sample = text[:8192]
    delimiter = None
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,\t|,")
        delimiter = getattr(dialect, "delimiter", None)
    except Exception:
        # fallback: choose most frequent between ; and ,
        sc = sample.count(";")
        cc = sample.count(",")
        delimiter = ";" if sc > cc else ","
        dialect = csv.excel
        dialect.delimiter = delimiter

    f = io.StringIO(text)
    reader = csv.DictReader(f, dialect=dialect)
    headers = reader.fieldnames or []
    if not headers:
        return {"ok": False, "error": "Intestazioni CSV mancanti."}

    # Build header lookup by normalized name
    header_by_norm: Dict[str, str] = {}
    for h in headers:
        if not h:
            continue
        hn = _hnorm(h)
        if hn and hn not in header_by_norm:
            header_by_norm[hn] = h

    def _find_header_for_target(target_col: str) -> Optional[str]:
        tn = _hnorm(target_col)
        if tn in header_by_norm:
            return header_by_norm[tn]

        # synonyms for key columns
        if _hnorm(target_col) == _hnorm(desc_col):
            for s in ("descrizione", "descr", "prodotto", "articolo", "item", "description", "desc", "nomeprodotto", "nome"):
                sn = _hnorm(s)
                if sn in header_by_norm:
                    return header_by_norm[sn]

        if supplier_col and _hnorm(target_col) == _hnorm(supplier_col):
            for s in ("fornitore", "forn", "supplier", "vendor", "venditore", "brand"):
                sn = _hnorm(s)
                if sn in header_by_norm:
                    return header_by_norm[sn]

        if _hnorm(target_col) == "conv":
            for s in ("conv", "conversione", "conversion", "fattore", "factor"):
                sn = _hnorm(s)
                if sn in header_by_norm:
                    return header_by_norm[sn]

        return None

    # Map target columns -> csv header
    mapping: Dict[str, str] = {}
    mapped_cols: List[str] = []
    for c in (target_columns or []):
        hc = _find_header_for_target(c)
        if hc:
            mapping[c] = hc
            mapped_cols.append(c)

    # We must have desc, and supplier if used
    desc_h = mapping.get(desc_col) or _find_header_for_target(desc_col)
    if desc_h:
        mapping[desc_col] = desc_h
        if desc_col not in mapped_cols:
            mapped_cols.append(desc_col)

    supp_h = None
    if supplier_col:
        supp_h = mapping.get(supplier_col) or _find_header_for_target(supplier_col)
        if supp_h:
            mapping[supplier_col] = supp_h
            if supplier_col not in mapped_cols:
                mapped_cols.append(supplier_col)

    if not mapping.get(desc_col):
        return {"ok": False, "error": f"Colonna descrizione non trovata nel CSV (attesa: {desc_col}).", "headers": headers}

    if supplier_col and not mapping.get(supplier_col):
        return {"ok": False, "error": f"Colonna fornitore non trovata nel CSV (attesa: {supplier_col}).", "headers": headers}

    total = 0
    skipped = 0
    empty = 0

    dedup: Dict[str, Dict[str, Any]] = {}

    for raw in reader:
        if raw is None:
            continue
        # skip empty lines
        if all(str(v or "").strip() == "" for v in raw.values()):
            empty += 1
            continue

        total += 1

        d = str(raw.get(mapping[desc_col]) or "").strip()
        s = ""
        if supplier_col:
            s = str(raw.get(mapping[supplier_col]) or "").strip()

        if not d or (supplier_col and not s):
            skipped += 1
            continue

        out_row: Dict[str, Any] = {}

        # include only mapped columns (but always include keys)
        for c in mapped_cols:
            h = mapping.get(c)
            if not h:
                continue
            out_row[c] = str(raw.get(h) or "").strip()

        # ensure keys are present
        out_row[desc_col] = d
        if supplier_col:
            out_row[supplier_col] = s

        key = (s + "␟" + d).lower() if supplier_col else d.lower()
        dedup[key] = out_row

    unknown_headers = []
    mapped_header_set = set(mapping.values())
    for h in headers:
        if h and h not in mapped_header_set:
            unknown_headers.append(h)

    return {
        "ok": True,
        "delimiter": delimiter,
        "headers": headers,
        "mapped": mapping,
        "mapped_cols": mapped_cols,
        "rows_total": total,
        "rows_skipped": skipped,
        "rows_empty": empty,
        "rows_deduped": len(dedup),
        "unknown_headers": unknown_headers,
        "rows": list(dedup.values()),
    }
