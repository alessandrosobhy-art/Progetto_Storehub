from __future__ import annotations

from datetime import date, datetime
from typing import Dict, Any, List, Optional

from app_db import get_connection, sql_date, sql_cast_str, supports_schema_alter
from app_db import get_backend

import inventory_repository
from date_utils import to_iso as _to_iso_date


def _qname(name: str) -> str:
    return f"[{str(name).replace(']', ']]')}]"


def _access_has_table(cur, table_name: str) -> bool:
    t = str(table_name).strip().lower()
    try:
        for row in cur.tables(tableType="TABLE"):
            n = getattr(row, "table_name", None)
            if not n:
                try:
                    n = row[2]
                except Exception:
                    n = None
            if n and str(n).strip().lower() == t:
                return True
    except Exception:
        return False
    return False


def _date_sql_expr(column_expr: str) -> str:
    if get_backend() == "sqlserver":
        return (
            "COALESCE("
            f"TRY_CONVERT(date, {column_expr}, 23),"
            f"TRY_CONVERT(date, {column_expr}, 111),"
            f"TRY_CONVERT(date, {column_expr}, 103),"
            f"TRY_CONVERT(date, {column_expr}, 105),"
            f"TRY_CONVERT(date, {column_expr}, 104),"
            f"TRY_CONVERT(date, {column_expr}, 101),"
            f"TRY_CONVERT(date, {column_expr})"
            ")"
        )
    return sql_date(column_expr)


def _row_date_iso(raw_day: Any) -> str:
    if isinstance(raw_day, datetime):
        return raw_day.date().isoformat()
    if isinstance(raw_day, date):
        return raw_day.isoformat()
    d_iso = _to_iso_date(raw_day)
    if d_iso:
        return d_iso
    try:
        return datetime.fromisoformat(str(raw_day)[:10]).date().isoformat()
    except Exception:
        return str(raw_day or "")[:10]


def list_fatturato_lordo_range(*, store_code: str, start_day: date, end_day: date) -> Dict[str, float]:
    conn = get_connection(store_code)
    try:
        cur = conn.cursor()
        try:
            table = inventory_repository._resolve_table_name(
                conn,
                "DatiDatabase",
                extra_candidates=["DatiDatabase", "DATIDATABASE", "dati database", "database"],
            )
        except Exception:
            if not _access_has_table(cur, "DatiDatabase"):
                return {}
            table = "DatiDatabase"

        cols = _detect_datidatabase_columns(conn, table)
        site_col = cols.get("site") or "Site"
        date_col = cols.get("date") or "Data"
        gross_col = cols.get("fatt_lordo") or "FatturatoLordo"
        date_expr = _date_sql_expr(_qname(date_col))

        sql = (
            f"SELECT {_qname(date_col)}, {_qname(gross_col)} "
            f"FROM {_qname(table)} "
            f"WHERE {sql_cast_str(_qname(site_col))}=? "
            f"AND {date_expr}>=? "
            f"AND {date_expr}<=?"
        )

        cur.execute(sql, [str(store_code), start_day.isoformat(), end_day.isoformat()])

        out: Dict[str, float] = {}
        for row in cur.fetchall() or []:
            d = row[0]
            v = row[1]

            di = _row_date_iso(d)

            try:
                num = float(v) if v is not None else 0.0
            except Exception:
                try:
                    num = float(str(v).replace(',', '.'))
                except Exception:
                    num = 0.0

            out[di] = out.get(di, 0.0) + num

        return out
    finally:
        try:
            conn.close()
        except Exception:
            pass


def list_legacy_daily_sales_range(*, store_code: str, start_day: date, end_day: date) -> Dict[str, Dict[str, float]]:
    """Legge i dati giornalieri disponibili da DatiDatabase per fallback legacy.

    Output:
      {
        "YYYY-MM-DD": {
          "gross_revenue": <float>,
          "delivery_total": <float>,
          "receipts_count": <float>
        }
      }
    """
    conn = get_connection(store_code)
    try:
        cur = conn.cursor()
        try:
            table = inventory_repository._resolve_table_name(
                conn,
                "DatiDatabase",
                extra_candidates=["DatiDatabase", "DATIDATABASE", "dati database", "database"],
            )
        except Exception:
            if not _access_has_table(cur, "DatiDatabase"):
                return {}
            table = "DatiDatabase"

        cols = _detect_datidatabase_columns(conn, table)
        site_col = cols.get("site") or "Site"
        date_col = cols.get("date") or "Data"
        gross_col = cols.get("fatt_lordo")
        delivery_col = cols.get("delivery")
        receipts_col = cols.get("scontrini")

        if not gross_col:
            return {}

        select_cols = [date_col, gross_col]
        if delivery_col:
            select_cols.append(delivery_col)
        if receipts_col:
            select_cols.append(receipts_col)

        where = []
        params: List[Any] = []
        if site_col:
            where.append(f"{sql_cast_str(_qname(site_col))}=?")
            params.append(str(store_code))
        elif get_backend() == "sqlserver":
            return {}

        date_expr = _date_sql_expr(_qname(date_col))
        where.append(f"{date_expr}>=?")
        params.append(start_day.isoformat())
        where.append(f"{date_expr}<=?")
        params.append(end_day.isoformat())

        sql = (
            "SELECT "
            + ", ".join(_qname(c) for c in select_cols)
            + f" FROM {_qname(table)} "
            + ("WHERE " + " AND ".join(where) if where else "")
        )

        cur.execute(sql, params)

        out: Dict[str, Dict[str, float]] = {}
        for row in cur.fetchall() or []:
            d_iso = _row_date_iso(row[0])
            if not d_iso:
                continue

            idx = 2
            gross = _safe_num(row[1])
            delivery = 0.0
            receipts = 0.0
            if delivery_col:
                delivery = _safe_num(row[idx])
                idx += 1
            if receipts_col:
                receipts = _safe_num(row[idx])

            bucket = out.setdefault(d_iso, {"gross_revenue": 0.0, "delivery_total": 0.0, "receipts_count": 0.0})
            bucket["gross_revenue"] += gross
            bucket["delivery_total"] += delivery
            bucket["receipts_count"] += receipts
        return out
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _safe_num(value: Any) -> float:
    try:
        return float(value or 0.0)
    except Exception:
        try:
            return float(str(value or "0").replace(",", "."))
        except Exception:
            return 0.0


def _norm(s: Any) -> str:
    return str(s or "").strip().lower()


def _detect_datidatabase_columns(conn, table_name: str) -> Dict[str, str]:
    """Rileva i nomi colonne reali della tabella DatiDatabase in modo robusto."""
    cols: List[str] = inventory_repository._get_table_columns(conn, table_name)
    cols_map = inventory_repository._cols_norm_map(cols)
    find = inventory_repository._find_col

    site_col = find(cols_map, ["site", "store", "negozio", "store code", "storecode"])
    date_col = find(cols_map, ["data", "date", "giorno", "dataac", "ac"])

    fatt_lordo = find(cols_map, ["fatturatolordo", "fatturato lordo", "fatturato"])
    fatt_instore = find(cols_map, ["fatturatoinstore", "fatturato instore", "instore"])
    delivery = find(cols_map, ["delivery", "totale delivery"])
    scontrini = find(cols_map, ["scontrini", "scontrino"])

    ore_tot = find(cols_map, ["oretotali", "ore totali"])
    ore_int = find(cols_map, ["oreinterne", "ore interne"])
    ore_stage = find(cols_map, ["orestage", "ore stage"])
    ore_training = find(cols_map, ["oretraining", "ore training"])
    fatt_budget = find(cols_map, ["fatturatobudget", "fatturato budget", "budget"])

    return {
        "site": site_col or "",
        "date": date_col or "",
        "fatt_lordo": fatt_lordo or "",
        "fatt_instore": fatt_instore or "",
        "delivery": delivery or "",
        "scontrini": scontrini or "",
        "ore_tot": ore_tot or "",
        "ore_int": ore_int or "",
        "ore_stage": ore_stage or "",
        "ore_training": ore_training or "",
        "fatt_budget": fatt_budget or "",
    }


def upsert_datidatabase_from_distinta(
    *,
    store_code: str,
    data_iso: str,
    giro_affari: float,
    totale_delivery: float,
    scontrini: int,
) -> Dict[str, Any]:
    """Inserisce/aggiorna DatiDatabase con i dati provenienti dalla Distinta di Cassa.

    Mapping richiesto:
    - SITE -> SITE
    - DATA -> DATA
    - FATTURATOLORDO -> GIRO AFFARI
    - FATTURATOINSTORE -> GIRO AFFARI - DELIVERY
    - DELIVERY -> TOTALE DELIVERY
    - SCONTRINI -> SCONTRINI
    - ORE* e FATTURATOBUDGET -> 0
    """

    out: Dict[str, Any] = {"ok": False, "error": None, "table": None}

    try:
        d_dt = datetime.fromisoformat(str(data_iso)).replace(hour=0, minute=0, second=0, microsecond=0)
    except Exception:
        out["error"] = "Data non valida"
        return out

    conn = get_connection(store_code)
    try:
        # risolve nome tabella (case-insensitive)
        try:
            table = inventory_repository._resolve_table_name(
                conn,
                "DatiDatabase",
                extra_candidates=["DatiDatabase", "DATIDATABASE", "dati database", "database"],
            )
        except Exception as ex:
            out["error"] = f"Tabella DatiDatabase non trovata: {ex}"
            return out

        out["table"] = table
        cols = _detect_datidatabase_columns(conn, table)

        if not cols.get("date"):
            out["error"] = "Colonna DATA non trovata in DatiDatabase"
            return out

        # Valori
        fatt_lordo = float(giro_affari or 0.0)
        deliv = float(totale_delivery or 0.0)
        fatt_instore = float(fatt_lordo - deliv)
        scontr = int(scontrini or 0)

        # Upsert
        cur = conn.cursor()

        where_parts: List[str] = []
        where_params: List[Any] = []
        if cols.get("site"):
            site_expr = f"[{cols['site']}]"
            where_parts.append(f"{sql_cast_str(site_expr)}=?")
            where_params.append(str(store_code))
        # DateValue su stringa per tollerare anche data salvata come testo o DateTime
        date_expr = f"[{cols['date']}]"
        where_parts.append(f"{sql_date(sql_cast_str(date_expr))} = ?")
        where_params.append(d_dt.date().isoformat())

        exists_sql = f"SELECT COUNT(*) FROM [{table}] WHERE " + " AND ".join(where_parts)
        exists = False
        try:
            cur.execute(exists_sql, where_params)
            exists = (cur.fetchone()[0] or 0) > 0
        except Exception:
            # fallback: carica date e filtra in python
            sel_cols = [cols.get("date")]
            if cols.get("site"):
                sel_cols.append(cols.get("site"))
            sel_cols = [c for c in sel_cols if c]
            cur.execute(
                f"SELECT {', '.join([f'[{c}]' for c in sel_cols])} FROM [{table}]" +
                (f" WHERE {sql_cast_str(site_expr)}=?" if cols.get("site") else ""),
                ([str(store_code)] if cols.get("site") else []),
            )
            for r in cur.fetchall() or []:
                try:
                    dd = r[0]
                    if isinstance(dd, datetime):
                        di = dd.date()
                    elif isinstance(dd, date):
                        di = dd
                    else:
                        di = datetime.fromisoformat(str(dd)).date()
                    if di == d_dt.date():
                        exists = True
                        break
                except Exception:
                    continue

        # colonne da scrivere (solo se esistono)
        write_map: Dict[str, Any] = {}
        if cols.get("fatt_lordo"):
            write_map[cols["fatt_lordo"]] = fatt_lordo
        if cols.get("fatt_instore"):
            write_map[cols["fatt_instore"]] = fatt_instore
        if cols.get("delivery"):
            write_map[cols["delivery"]] = deliv
        if cols.get("scontrini"):
            write_map[cols["scontrini"]] = scontr

        # Ore e budget: 0
        for k in ["ore_tot", "ore_int", "ore_stage", "ore_training", "fatt_budget"]:
            c = cols.get(k)
            if c:
                write_map[c] = 0

        if exists:
            if not write_map:
                out["ok"] = True
                return out
            set_sql = ", ".join([f"[{c}]=?" for c in write_map.keys()])
            params = list(write_map.values()) + where_params
            upd_sql = f"UPDATE [{table}] SET {set_sql} WHERE " + " AND ".join(where_parts)
            cur.execute(upd_sql, params)
        else:
            ins_cols: List[str] = []
            ins_vals: List[Any] = []

            if cols.get("site"):
                ins_cols.append(cols["site"])
                ins_vals.append(str(store_code))
            ins_cols.append(cols["date"])
            ins_vals.append(d_dt)

            for c, v in write_map.items():
                if c in ins_cols:
                    continue
                ins_cols.append(c)
                ins_vals.append(v)

            col_sql = ",".join([f"[{c}]" for c in ins_cols])
            ph = ",".join(["?"] * len(ins_cols))
            ins_sql = f"INSERT INTO [{table}] ({col_sql}) VALUES ({ph})"
            cur.execute(ins_sql, ins_vals)

        conn.commit()
        out["ok"] = True
        return out

    except Exception as e:
        out["error"] = str(e)
        try:
            conn.rollback()
        except Exception:
            pass
        return out
    finally:
        try:
            conn.close()
        except Exception:
            pass


def delete_datidatabase_day(*, store_code: str, data_iso: str) -> Dict[str, Any]:
    """Elimina (se presente) la riga di DatiDatabase associata alla Distinta di Cassa del giorno."""
    out: Dict[str, Any] = {"ok": False, "error": None, "table": None, "deleted": 0}

    try:
        d_dt = datetime.fromisoformat(str(data_iso)).replace(hour=0, minute=0, second=0, microsecond=0)
    except Exception:
        out["error"] = "Data non valida"
        return out

    conn = get_connection(store_code)
    try:
        try:
            table = inventory_repository._resolve_table_name(
                conn,
                "DatiDatabase",
                extra_candidates=["DatiDatabase", "DATIDATABASE", "dati database", "database"],
            )
        except Exception:
            # Se non esiste la tabella, consideriamo l'operazione riuscita (nulla da cancellare).
            out["ok"] = True
            out["deleted"] = 0
            return out

        out["table"] = table
        cols = _detect_datidatabase_columns(conn, table)
        if not cols.get("date"):
            out["error"] = "Colonna DATA non trovata in DatiDatabase"
            return out

        where_parts: List[str] = []
        params: List[Any] = []

        if cols.get("site"):
            site_expr = f"[{cols['site']}]"
            where_parts.append(f"{sql_cast_str(site_expr)}=?")
            params.append(str(store_code))

        date_expr = f"[{cols['date']}]"
        where_parts.append(f"{sql_date(sql_cast_str(date_expr))} = ?")
        params.append(d_dt.date().isoformat())

        sql = f"DELETE FROM [{table}] WHERE " + " AND ".join(where_parts)

        cur = conn.cursor()
        cur.execute(sql, params)
        try:
            out["deleted"] = int(cur.rowcount) if cur.rowcount is not None else 0
        except Exception:
            out["deleted"] = 0

        conn.commit()
        out["ok"] = True
        return out

    except Exception as e:
        out["error"] = str(e)
        try:
            conn.rollback()
        except Exception:
            pass
        return out
    finally:
        try:
            conn.close()
        except Exception:
            pass
