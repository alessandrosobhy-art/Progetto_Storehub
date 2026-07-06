"""analysis_repository.py

Calcola i KPI di consumo per la pagina "Analisi".

Formula consumo:
  Inventario iniziale + Delivery + Trasferimenti In - Trasferimenti Out - Inventario Finale

Note:
  - Le somme vengono restituite per bucket: FoodPaper e Operating.
  - I ricavi vengono presi da DatiDatabase (fatturato lordo) e convertiti a netto: lordo / 1.1
  - Waste NON entra nel calcolo del consumo, ma viene mostrato a parte.
"""

from __future__ import annotations

from app_logging import log_swallowed
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from date_utils import to_iso as _to_iso_date, parse_any_date as _parse_any_date

from app_db import get_connection, get_backend

import delivery_repository
import inventory_repository


# ------------------------------
#  Helpers (robusti)
# ------------------------------


def _norm(s: str) -> str:
    return (s or "").strip().lower().replace("_", " ")


def _safe_float(v: Any) -> float:
    """Parse numeri provenienti da DB.

    In Access spesso arrivano già come numerici.
    In SQL Server (post-migrazione) possono arrivare anche come stringhe con
    separatori EU/US (es. '1.234,56' o '1,234.56').
    """
    if v is None:
        return 0.0

    # numerici già pronti
    try:
        return float(v)
    except Exception:
        log_swallowed('analysis_repository:49')

    s = str(v).strip()
    if not s:
        return 0.0

    # pulizia base
    s = s.replace("€", "").replace(" ", "").replace("\xa0", "")

    # se contiene sia ',' che '.', scegli il separatore decimale come l'ultimo incontrato
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            # EU: '.' migliaia, ',' decimale
            s = s.replace(".", "").replace(",", ".")
        else:
            # US: ',' migliaia, '.' decimale
            s = s.replace(",", "")
    elif "," in s:
        # solo ',' -> tipicamente decimale (EU)
        parts = s.split(",")
        # caso "1,234,567" (solo migliaia)
        if len(parts) > 2 and all(len(p) == 3 for p in parts[1:]):
            s = "".join(parts)
        else:
            s = s.replace(",", ".")
    elif "." in s:
        # solo '.' -> può essere decimale o migliaia
        parts = s.split(".")
        if len(parts) > 2 and all(len(p) == 3 for p in parts[1:]):
            s = "".join(parts)

    try:
        return float(s)
    except Exception:
        return 0.0


def _bucket_from_group(group_val: Any) -> str:
    """Ritorna FoodPaper / Operating a partire dal valore della colonna GRUPPO."""
    g = _norm(str(group_val or ""))
    if not g:
        return "Operating"

    if g in (
        "food",
        "paper",
        "foodpaper",
        "food paper",
        "food&paper",
        "food & paper",
    ):
        return "FoodPaper"
    if "food" in g or "paper" in g:
        return "FoodPaper"
    return "Operating"


def _detect_group_col(cols: List[str]) -> str:
    cols_map = {_norm(c): c for c in cols or []}
    for key in ("gruppo", "group", "grp", "categoria", "category"):
        if key in cols_map:
            return cols_map[key]
    for c in cols or []:
        n = _norm(c)
        if "grupp" in n:
            return c
    for c in cols or []:
        n = _norm(c)
        if "categ" in n:
            return c
    return ""


def _parse_iso_date(s: str) -> date:
    return datetime.strptime((s or "").strip(), "%Y-%m-%d").date()


def _parse_any_date_to_iso(d: Any) -> Optional[str]:
    """Converte date/datetime o stringhe in YYYY-MM-DD, robusto rispetto a DMY/MDY."""
    return _to_iso_date(d)
    if isinstance(d, datetime):
        return d.date().isoformat()
    if isinstance(d, date):
        return d.isoformat()

    s = str(d).strip()
    if not s:
        return None

    # yyyy-mm-dd
    try:
        if len(s) >= 10 and s[4] == "-" and s[7] == "-":
            return datetime.strptime(s[:10], "%Y-%m-%d").date().isoformat()
    except Exception:
        log_swallowed('analysis_repository:143')

    # dd/mm/yyyy
    try:
        if "/" in s:
            return datetime.strptime(s[:10], "%d/%m/%Y").date().isoformat()
    except Exception:
        log_swallowed('analysis_repository:150')

    return None


def _parse_any_date_to_iso_in_range(raw_date: Any, start: date, end_inclusive: date) -> Optional[str]:
    """Parsa una data (anche testo) scegliendo l'interpretazione che cade nel range.

    Serve per gestire DB/Windows in locale EN dove le date testuali possono essere MDY,
    mentre in IT sono tipicamente DMY.
    """
    if raw_date is None:
        return None

    # se è già date/datetime, non è ambiguo
    if isinstance(raw_date, datetime):
        d0 = raw_date.date()
        return d0.isoformat() if start <= d0 <= end_inclusive else d0.isoformat()
    if isinstance(raw_date, date):
        d0 = raw_date
        return d0.isoformat() if start <= d0 <= end_inclusive else d0.isoformat()

    d_dmy = _parse_any_date(raw_date, default_order="DMY")
    d_mdy = _parse_any_date(raw_date, default_order="MDY")

    in_dmy = bool(d_dmy and start <= d_dmy <= end_inclusive)
    in_mdy = bool(d_mdy and start <= d_mdy <= end_inclusive)

    if in_dmy and not in_mdy:
        return d_dmy.isoformat()  # type: ignore[union-attr]
    if in_mdy and not in_dmy:
        return d_mdy.isoformat()  # type: ignore[union-attr]
    if in_dmy:
        return d_dmy.isoformat()  # type: ignore[union-attr]
    if in_mdy:
        return d_mdy.isoformat()  # type: ignore[union-attr]

    # fallback: prova prima DMY (coerente con IT)
    if d_dmy:
        return d_dmy.isoformat()
    if d_mdy:
        return d_mdy.isoformat()
    return None


def _iter_month_starts(start: date, end_inclusive: date) -> List[date]:
    """Ritorna i primi giorni dei mesi coperti dal range inclusivo."""
    months: List[date] = []
    d = date(start.year, start.month, 1)
    last = date(end_inclusive.year, end_inclusive.month, 1)
    while d <= last:
        months.append(d)
        if d.month == 12:
            d = date(d.year + 1, 1, 1)
        else:
            d = date(d.year, d.month + 1, 1)
    return months


# ------------------------------
#  Revenues
# ------------------------------


def _detect_database_layout(conn, table: str) -> Dict[str, str]:
    cols = inventory_repository._get_table_columns(conn, table)
    cols_norm = {_norm(c): c for c in cols}

    # site
    site_col = (
        cols_norm.get("site")
        or cols_norm.get("store")
        or cols_norm.get("store code")
        or cols_norm.get("storecode")
        or cols_norm.get("negozio")
    )

    # date
    date_col = (
        cols_norm.get("data")
        or cols_norm.get("date")
        or cols_norm.get("giorno")
        or cols_norm.get("ac")
        or cols_norm.get("dataac")
    )

    # fatturato lordo (preferenze in ordine: "lordo" esplicito -> generici)
    fatt_col = (
        cols_norm.get("fatturato lordo")
        or cols_norm.get("fatturatolordo")
        or cols_norm.get("giro affari lordo")
        or cols_norm.get("giroaffarilordo")
        or cols_norm.get("fatt lordo")
        or cols_norm.get("fatturato")
        or cols_norm.get("sales")
        or cols_norm.get("revenue")
    )

    return {
        "site_col": site_col or "",
        "date_col": date_col or "",
        "fatt_col": fatt_col or "",
    }


def get_revenues_net(store_code: str, start: date, end_inclusive: date) -> Tuple[float, List[str]]:
    """Somma fatturato lordo nel periodo e converte in netto (lordo / 1.1)."""
    try:
        from daily_sales_repository import get_revenues_net_range

        value = get_revenues_net_range(store_code=str(store_code), start_day=start, end_day=end_inclusive)
        return float(value or 0.0), []
    except Exception as ex:
        return 0.0, [f"Errore lettura ricavi StoreHubDailySales/DatiDatabase: {ex}"]

    warnings: List[str] = []

    backend = get_backend()

    conn = get_connection(store_code)
    try:
        try:
            table = inventory_repository._resolve_table_name(
                conn,
                "DatiDatabase",
                extra_candidates=["DatiDatabase", "DATIDATABASE", "dati database", "database"],
            )
        except Exception as ex:
            return 0.0, [f"Tabella DatiDatabase non trovata: {ex}"]
        layout = _detect_database_layout(conn, table)
        if not layout["date_col"] or not layout["fatt_col"]:
            return 0.0, ["Layout DatiDatabase non riconosciuto (manca data o fatturato lordo)."]

        site_col = layout["site_col"]
        date_col = layout["date_col"]
        fatt_col = layout["fatt_col"]

        # In Access il DB è per-store, quindi l'assenza di SITE non è un problema.
        # In SQL Server invece è fondamentale per non sommare tutti gli store.
        if backend == "sqlserver" and not site_col:
            return 0.0, [
                "DatiDatabase: colonna 'site' non trovata: in SQL Server non posso filtrare per store. "
                "Verifica che la tabella DatiDatabase contenga la colonna 'site' (o equivalente)."
            ]

        cur = conn.cursor()

        where: List[str] = []
        params: List[Any] = []

        if site_col:
            where.append(f"[{site_col}] = ?")
            params.append(str(store_code))

        if backend == "sqlserver":
            # Conversione robusta: gestisce stringhe in formati diversi senza dipendere da DATEFORMAT/LANGUAGE.
            dc = f"[{date_col}]"
            date_expr = (
                "COALESCE("
                f"TRY_CONVERT(date, {dc}, 23),"   # yyyy-mm-dd
                f"TRY_CONVERT(date, {dc}, 111),"  # yyyy/mm/dd
                f"TRY_CONVERT(date, {dc}, 103),"  # dd/mm/yyyy
                f"TRY_CONVERT(date, {dc}, 105),"  # dd-mm-yyyy
                f"TRY_CONVERT(date, {dc}, 104),"  # dd.mm.yyyy
                f"TRY_CONVERT(date, {dc}, 101),"  # mm/dd/yyyy
                f"TRY_CONVERT(date, {dc})"
                ")"
            )
            where.append(f"{date_expr} >= ?")
            params.append(start)
            where.append(f"{date_expr} < ?")
            params.append(end_inclusive + timedelta(days=1))
        else:
            start_dt = datetime(start.year, start.month, start.day)
            end_excl = datetime(end_inclusive.year, end_inclusive.month, end_inclusive.day) + timedelta(days=1)
            where.append(f"[{date_col}] >= ?")
            params.append(start_dt)
            where.append(f"[{date_col}] < ?")
            params.append(end_excl)

        sql = f"SELECT [{fatt_col}] FROM [{table}] WHERE " + " AND ".join(where)
        total_gross = 0.0
        try:
            cur.execute(sql, params)
            for (v,) in cur.fetchall():
                total_gross += _safe_float(v)
        except Exception:
            # fallback: date come testo -> carica tutto e filtra in Python
            warnings.append("DatiDatabase: filtro date SQL non riuscito, applico filtro in Python.")
            where = []
            params = []
            if site_col:
                where.append(f"[{site_col}] = ?")
                params.append(str(store_code))
            sql = f"SELECT [{date_col}], [{fatt_col}] FROM [{table}]" + (" WHERE " + " AND ".join(where) if where else "")
            cur.execute(sql, params)
            for r in cur.fetchall():
                d_iso = _parse_any_date_to_iso_in_range(r[0], start, end_inclusive)
                if not d_iso:
                    continue
                d = datetime.strptime(d_iso, "%Y-%m-%d").date()
                if d < start or d > end_inclusive:
                    continue
                total_gross += _safe_float(r[1])

        total_net = float(total_gross / 1.1) if total_gross else 0.0
        return total_net, warnings
    finally:
        try:
            conn.close()
        except Exception:
            log_swallowed('analysis_repository:361')


def _close_cursor_quietly(cur) -> None:
    """Chiude un cursor pyodbc senza sollevare eccezioni."""
    try:
        if cur is not None:
            cur.close()
    except Exception:
        log_swallowed('analysis_repository:370')


def _get_revenues_net_from_conn(conn, store_code: str, start: date, end_inclusive: date) -> Tuple[float, List[str]]:
    return get_revenues_net(store_code, start, end_inclusive)

    """Come get_revenues_net(), ma riusa una connessione già aperta."""
    warnings: List[str] = []

    backend = get_backend()

    try:
        try:
            table = inventory_repository._resolve_table_name(
                conn,
                "DatiDatabase",
                extra_candidates=["DatiDatabase", "DATIDATABASE", "dati database", "database"],
            )
        except Exception as ex:
            return 0.0, [f"Tabella DatiDatabase non trovata: {ex}"]

        layout = _detect_database_layout(conn, table)
        if not layout["date_col"] or not layout["fatt_col"]:
            return 0.0, ["Layout DatiDatabase non riconosciuto (manca data o fatturato lordo)."]

        site_col = layout["site_col"]
        date_col = layout["date_col"]
        fatt_col = layout["fatt_col"]

        # In Access il DB è per-store; in SQL Server è fondamentale per non sommare tutti gli store.
        if backend == "sqlserver" and not site_col:
            return 0.0, [
                "DatiDatabase: colonna 'site' (o equivalente) non trovata: impossibile filtrare per store in SQL. "
                "Verifica che la tabella DatiDatabase contenga la colonna SITE."
            ]

        cur = conn.cursor()
        try:
            start_dt = datetime(start.year, start.month, start.day)
            end_excl = datetime(end_inclusive.year, end_inclusive.month, end_inclusive.day) + timedelta(days=1)

            where = []
            params: List[Any] = []
            if site_col:
                where.append(f"[{site_col}] = ?")
                params.append(str(store_code))
            if backend == "sqlserver":
                # Conversione robusta: gestisce stringhe in formati diversi senza dipendere da DATEFORMAT/LANGUAGE.
                dc = f"[{date_col}]"
                date_expr = (
                    "COALESCE("
                    f"TRY_CONVERT(date, {dc}, 23),"   # yyyy-mm-dd
                    f"TRY_CONVERT(date, {dc}, 111),"  # yyyy/mm/dd
                    f"TRY_CONVERT(date, {dc}, 103),"  # dd/mm/yyyy
                    f"TRY_CONVERT(date, {dc}, 105),"  # dd-mm-yyyy
                    f"TRY_CONVERT(date, {dc}, 104),"  # dd.mm.yyyy
                    f"TRY_CONVERT(date, {dc}, 101),"  # mm/dd/yyyy
                    f"TRY_CONVERT(date, {dc})"
                    ")"
                )
                where.append(f"{date_expr} >= ?")
                params.append(start)
                where.append(f"{date_expr} < ?")
                params.append(end_inclusive + timedelta(days=1))
            else:
                where.append(f"[{date_col}] >= ?")
                params.append(start_dt)
                where.append(f"[{date_col}] < ?")
                params.append(end_excl)

            sql = f"SELECT [{fatt_col}] FROM [{table}] WHERE " + " AND ".join(where)
            total_gross = 0.0
            try:
                cur.execute(sql, params)
                for (v,) in cur.fetchall():
                    total_gross += _safe_float(v)
            except Exception:
                # fallback: date come testo -> carica tutto e filtra in Python
                warnings.append("DatiDatabase: filtro date SQL non riuscito, applico filtro in Python.")
                where = []
                params = []
                if site_col:
                    where.append(f"[{site_col}] = ?")
                    params.append(str(store_code))
                sql = f"SELECT [{date_col}], [{fatt_col}] FROM [{table}]" + (
                    (" WHERE " + " AND ".join(where)) if where else ""
                )
                cur.execute(sql, params)
                for r in cur.fetchall():
                    d_iso = _parse_any_date_to_iso_in_range(r[0], start, end_inclusive)
                    if not d_iso:
                        continue
                    d = datetime.strptime(d_iso, "%Y-%m-%d").date()
                    if d < start or d > end_inclusive:
                        continue
                    total_gross += _safe_float(r[1])

            total_net = float(total_gross / 1.1) if total_gross else 0.0
            return total_net, warnings
        finally:
            _close_cursor_quietly(cur)
    except Exception as ex:
        return 0.0, [f"Errore lettura ricavi: {ex}"]


def _sum_inventory_like_from_conn(
    conn,
    store_code: str,
    table_name: str,
    mov_type: str,
    start: date,
    end_inclusive: date,
    bucket: str,
    supplier_name: Optional[str] = None,
) -> Tuple[float, List[str]]:
    """Come _sum_inventory_like(), ma riusa una connessione già aperta."""
    warnings: List[str] = []
    bucket = bucket if bucket in ("FoodPaper", "Operating") else "FoodPaper"

    try:
        try:
            table = inventory_repository._resolve_table_name(
                conn,
                table_name,
                extra_candidates=[table_name, table_name.upper(), table_name.lower()],
            )
        except Exception as ex:
            return 0.0, [f"Tabella '{table_name}' non trovata: {ex}"]

        cols = inventory_repository._get_table_columns(conn, table)

        require_site2 = table_name.lower() in ("datitx", "tx", "dati tx")
        layout = inventory_repository._detect_inventory_layout(cols, require_site2=require_site2)
        required = ("site_col", "date_col", "mov_col", "toteuro_col")
        if any(not layout.get(k) for k in required):
            return 0.0, [f"Layout tabella '{table}' non riconosciuto (colonne mancanti per il calcolo)."]

        group_col = _detect_group_col(cols)
        supplier_col = inventory_repository._detect_supplier_col(cols)
        if supplier_name and not supplier_col:
            warnings.append("Colonna fornitore non trovata: filtro fornitore non applicabile.")

        start_dt = datetime(start.year, start.month, start.day)
        end_excl = datetime(end_inclusive.year, end_inclusive.month, end_inclusive.day) + timedelta(days=1)

        # DatiTX: i trasferimenti vengono salvati solo come TXOUT, con SITE2 = destinazione.
        # Per rappresentare i TXIN, consideriamo i record TXOUT dove SITE2 = store.
        site_filter_col = layout["site_col"]
        mov_type_eff = mov_type
        if require_site2 and str(mov_type or "").strip().upper() == "TXIN":
            site_filter_col = layout.get("site2_col") or layout["site_col"]
            mov_type_eff = "TXOUT"
        elif require_site2 and str(mov_type or "").strip().upper() == "TXOUT":
            mov_type_eff = "TXOUT"

        select_cols = [layout["toteuro_col"]]
        if group_col:
            select_cols.append(group_col)
        if supplier_col:
            select_cols.append(supplier_col)

        where = [
            f"[{site_filter_col}] = ?",
            f"[{layout['mov_col']}] = ?",
            f"[{layout['date_col']}] >= ?",
            f"[{layout['date_col']}] < ?",
        ]
        params: List[Any] = [str(store_code), mov_type_eff, start_dt, end_excl]
        if supplier_name and supplier_col:
            where.append(f"[{supplier_col}] = ?")
            params.append(supplier_name)

        sql = f"SELECT {', '.join('['+c+']' for c in select_cols)} FROM [{table}] WHERE " + " AND ".join(where)

        cur = conn.cursor()
        try:
            total = 0.0
            cur.execute(sql, params)
            for r in cur.fetchall():
                idx = 0
                val = r[idx]; idx += 1
                raw_group = r[idx] if group_col else None
                if group_col:
                    idx += 1
                b = _bucket_from_group(raw_group)
                if b != bucket:
                    continue
                total += _safe_float(val)

            return float(total), warnings
        finally:
            _close_cursor_quietly(cur)
    except Exception as ex:
        return 0.0, [f"Errore lettura '{table_name}' ({mov_type}): {ex}"]


def _sum_delivery_from_conn(
    conn,
    store_code: str,
    start: date,
    end_inclusive: date,
    bucket: str,
    supplier_name: Optional[str] = None,
) -> Tuple[float, List[str]]:
    """Come _sum_delivery(), ma riusa una connessione già aperta."""
    warnings: List[str] = []
    bucket = bucket if bucket in ("FoodPaper", "Operating") else "FoodPaper"

    table = delivery_repository.get_delivery_table_name()

    try:
        layout = delivery_repository._detect_delivery_layout(conn, table)
        if layout.get("error"):
            return 0.0, [layout.get("error")]

        site_col = layout["site_col"]
        val_col = layout["val_col"]
        supplier_col = layout.get("supplier_col")
        date_col = layout.get("deliv_date_col") or layout.get("doc_date_col")
        if not date_col:
            return 0.0, ["Colonna data non trovata in DatiDelivery"]

        cols = layout.get("columns") or []
        group_col = _detect_group_col(cols) or ""

        if supplier_name and not supplier_col:
            warnings.append("DatiDelivery: colonna fornitore non trovata, filtro fornitore non applicabile.")

        cur = conn.cursor()
        try:
            total = 0.0

            # DatiDelivery spesso salva la data come testo dd/mm/yyyy: lavoriamo per mesi e filtriamo in Python.
            for m_start in _iter_month_starts(start, end_inclusive):
                mm_yyyy = f"{m_start.month:02d}/{m_start.year}"

                select_cols = [date_col, val_col]
                if supplier_col:
                    select_cols.append(supplier_col)
                if group_col:
                    select_cols.append(group_col)

                rows = []
                # Prova 1: Month/Year su date native
                try:
                    where = [f"[{site_col}] = ?", f"Month([{date_col}]) = ?", f"Year([{date_col}]) = ?"]
                    params: List[Any] = [str(store_code), m_start.month, m_start.year]
                    if supplier_name and supplier_col:
                        where.append(f"[{supplier_col}] = ?")
                        params.append(supplier_name)
                    sql = (
                        f"SELECT {', '.join('['+c+']' for c in select_cols)} FROM [{table}] "
                        f"WHERE " + " AND ".join(where)
                    )
                    cur.execute(sql, params)
                    rows = cur.fetchall()
                except Exception:
                    # Prova 2: Right(date,7) = 'MM/YYYY'
                    where = [f"[{site_col}] = ?", f"Right([{date_col}], 7) = ?"]
                    params = [str(store_code), mm_yyyy]
                    if supplier_name and supplier_col:
                        where.append(f"[{supplier_col}] = ?")
                        params.append(supplier_name)
                    sql = (
                        f"SELECT {', '.join('['+c+']' for c in select_cols)} FROM [{table}] "
                        f"WHERE " + " AND ".join(where)
                    )
                    cur.execute(sql, params)
                    rows = cur.fetchall()

                for r in rows:
                    idx = 0
                    raw_date = r[idx]; idx += 1
                    raw_val = r[idx]; idx += 1
                    raw_supplier = r[idx] if supplier_col else None
                    if supplier_col:
                        idx += 1
                    raw_group = r[idx] if group_col else None

                    if supplier_name and supplier_col:
                        if str(raw_supplier or "").strip() != str(supplier_name).strip():
                            continue

                    d_iso = _parse_any_date_to_iso(raw_date)
                    if not d_iso:
                        continue
                    d = datetime.strptime(d_iso, "%Y-%m-%d").date()
                    if d < start or d > end_inclusive:
                        continue
                    b = _bucket_from_group(raw_group)
                    if b != bucket:
                        continue
                    total += _safe_float(raw_val)

            return float(total), warnings
        finally:
            _close_cursor_quietly(cur)
    except Exception as ex:
        return 0.0, [f"Errore lettura DatiDelivery: {ex}"]


# ------------------------------
#  Aggregazioni movimenti
# ------------------------------


def _sum_inventory_like(
    store_code: str,
    table_name: str,
    mov_type: str,
    start: date,
    end_inclusive: date,
    bucket: str,
    supplier_name: Optional[str] = None,
) -> Tuple[float, List[str]]:
    """Somma TOTEURO per un mov_type su una tabella tipo inventario.

    Nota: per DatiTX i trasferimenti vengono gestiti leggendo solo TXOUT e usando SITE2 come destinazione.
    Di conseguenza:
      - TXOUT: SITE = store
      - TXIN:  SITE2 = store (derivato dai TXOUT)
    """
    conn = get_connection(store_code)
    try:
        return _sum_inventory_like_from_conn(
            conn,
            store_code=store_code,
            table_name=table_name,
            mov_type=mov_type,
            start=start,
            end_inclusive=end_inclusive,
            bucket=bucket,
            supplier_name=supplier_name,
        )
    finally:
        try:
            conn.close()
        except Exception:
            log_swallowed('analysis_repository:708')


def _sum_delivery(
    store_code: str,
    start: date,
    end_inclusive: date,
    bucket: str,
    supplier_name: Optional[str] = None,
) -> Tuple[float, List[str]]:
    """Somma VALORE DDT (DatiDelivery) nel range inclusivo, filtrato per bucket."""
    warnings: List[str] = []
    bucket = bucket if bucket in ("FoodPaper", "Operating") else "FoodPaper"

    table = delivery_repository.get_delivery_table_name()
    conn = get_connection(store_code)
    try:
        layout = delivery_repository._detect_delivery_layout(conn, table)
        if layout.get("error"):
            return 0.0, [layout.get("error")]

        site_col = layout["site_col"]
        val_col = layout["val_col"]
        supplier_col = layout.get("supplier_col")
        date_col = layout.get("deliv_date_col") or layout.get("doc_date_col")
        if not date_col:
            return 0.0, ["Colonna data non trovata in DatiDelivery"]

        cols = layout.get("columns") or []
        group_col = _detect_group_col(cols) or ""

        if supplier_name and not supplier_col:
            warnings.append("DatiDelivery: colonna fornitore non trovata, filtro fornitore non applicabile.")

        cur = conn.cursor()
        total = 0.0

        # DatiDelivery spesso salva la data come testo dd/mm/yyyy: lavoriamo per mesi e filtriamo in Python.
        for m_start in _iter_month_starts(start, end_inclusive):
            mm_yyyy = f"{m_start.month:02d}/{m_start.year}"

            select_cols = [date_col, val_col]
            if supplier_col:
                select_cols.append(supplier_col)
            if group_col:
                select_cols.append(group_col)

            rows = []
            # Prova 1: Month/Year su date native
            try:
                where = [f"[{site_col}] = ?", f"Month([{date_col}]) = ?", f"Year([{date_col}]) = ?"]
                params: List[Any] = [str(store_code), m_start.month, m_start.year]
                if supplier_name and supplier_col:
                    where.append(f"[{supplier_col}] = ?")
                    params.append(supplier_name)
                sql = (
                    f"SELECT {', '.join('['+c+']' for c in select_cols)} FROM [{table}] "
                    f"WHERE " + " AND ".join(where)
                )
                cur.execute(sql, params)
                rows = cur.fetchall()
            except Exception:
                # Prova 2: Right(date,7) = 'MM/YYYY'
                where = [f"[{site_col}] = ?", f"Right([{date_col}], 7) = ?"]
                params = [str(store_code), mm_yyyy]
                if supplier_name and supplier_col:
                    where.append(f"[{supplier_col}] = ?")
                    params.append(supplier_name)
                sql = (
                    f"SELECT {', '.join('['+c+']' for c in select_cols)} FROM [{table}] "
                    f"WHERE " + " AND ".join(where)
                )
                cur.execute(sql, params)
                rows = cur.fetchall()

            for r in rows:
                idx = 0
                raw_date = r[idx]; idx += 1
                raw_val = r[idx]; idx += 1
                raw_supplier = r[idx] if supplier_col else None
                if supplier_col:
                    idx += 1
                raw_group = r[idx] if group_col else None

                if supplier_name and supplier_col:
                    # nel caso fallback Right(), la where già filtra, qui solo safety
                    if str(raw_supplier or "").strip() != str(supplier_name).strip():
                        continue

                d_iso = _parse_any_date_to_iso(raw_date)
                if not d_iso:
                    continue
                d = datetime.strptime(d_iso, "%Y-%m-%d").date()
                if d < start or d > end_inclusive:
                    continue
                b = _bucket_from_group(raw_group)
                if b != bucket:
                    continue
                total += _safe_float(raw_val)

        return float(total), warnings
    finally:
        try:
            conn.close()
        except Exception:
            log_swallowed('analysis_repository:813')


# ------------------------------
#  Public API
# ------------------------------


def get_consumption_summary(
    store_code: str,
    start_iso: str,
    end_iso: str,
    supplier_name: Optional[str] = None,
    buckets: Optional[List[str]] = None,
) -> Dict[str, Any]:
    start = _parse_iso_date(start_iso)
    end = _parse_iso_date(end_iso)
    if end < start:
        start, end = end, start

    inv_init_date = start - timedelta(days=1)

    revenues_net, rev_warn = get_revenues_net(store_code, start, end)

    warnings: List[str] = []
    warnings.extend(rev_warn)

    def pct(v: float) -> float:
        if revenues_net <= 0:
            return 0.0
        return float((v / revenues_net) * 100.0)

    out: Dict[str, Any] = {
        "period": {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "inv_initial_date": inv_init_date.isoformat(),
        },
        "supplier": supplier_name or "",
        "revenues_net": float(revenues_net),
        "buckets": {},
        "warnings": warnings,
    }

    buckets_to_compute = buckets or ["FoodPaper", "Operating"]
    # Normalizza eventuali valori sporchi
    buckets_to_compute = [str(b or "").strip() for b in buckets_to_compute]
    buckets_to_compute = [b for b in buckets_to_compute if b]
    if not buckets_to_compute:
        buckets_to_compute = ["FoodPaper", "Operating"]

    for bucket in buckets_to_compute:
        inv_initial, w1 = _sum_inventory_like(store_code, "DatiInventario", "INV", inv_init_date, inv_init_date, bucket, supplier_name)
        delivery, w2 = _sum_delivery(store_code, start, end, bucket, supplier_name)
        tx_in, w3 = _sum_inventory_like(store_code, "DatiTX", "TXIN", start, end, bucket, supplier_name)
        tx_out, w4 = _sum_inventory_like(store_code, "DatiTX", "TXOUT", start, end, bucket, supplier_name)
        inv_final, w5 = _sum_inventory_like(store_code, "DatiInventario", "INV", end, end, bucket, supplier_name)
        # In DB la movimentazione waste è registrata come "WASTE CRUDO"
        waste, w6 = _sum_inventory_like(store_code, "DatiInventario", "WASTE CRUDO", start, end, bucket, supplier_name)

        warnings.extend(w1 + w2 + w3 + w4 + w5 + w6)

        consumo = float(inv_initial + delivery + tx_in - tx_out - inv_final)

        out["buckets"][bucket] = {
            "inv_initial": float(inv_initial),
            "delivery": float(delivery),
            "tx_in": float(tx_in),
            "tx_out": float(tx_out),
            "inv_final": float(inv_final),
            "waste": float(waste),
            "consumption": float(consumo),
            "consumption_pct": float(pct(consumo)),
            "waste_pct": float(pct(waste)),
        }

    return out




def get_consumption_summary_on_conn(
    conn,
    store_code: str,
    start_iso: str,
    end_iso: str,
    supplier_name: Optional[str] = None,
    buckets: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Come get_consumption_summary_single_connection(), ma riusa una connessione già aperta.

    Utile in SQL Server (unico DB): si può aprire una sola connessione per la richiesta
    e calcolare più store senza costi di handshake/connessione ripetuti.

    Nota: questa funzione esiste anche per retro-compatibilità con patch precedenti.
    """

    start = _parse_iso_date(start_iso)
    end = _parse_iso_date(end_iso)
    if end < start:
        start, end = end, start

    inv_init_date = start - timedelta(days=1)

    # Normalizza bucket list
    buckets_to_compute = buckets or ["FoodPaper", "Operating"]
    buckets_to_compute = [str(b or "").strip() for b in buckets_to_compute]
    buckets_to_compute = [b for b in buckets_to_compute if b]
    if not buckets_to_compute:
        buckets_to_compute = ["FoodPaper", "Operating"]

    revenues_net, rev_warn = _get_revenues_net_from_conn(conn, store_code, start, end)

    warnings: List[str] = []
    warnings.extend(rev_warn)

    def pct(v: float) -> float:
        if revenues_net <= 0:
            return 0.0
        return float((v / revenues_net) * 100.0)

    out: Dict[str, Any] = {
        "period": {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "inv_initial_date": inv_init_date.isoformat(),
        },
        "supplier": supplier_name or "",
        "revenues_net": float(revenues_net),
        "buckets": {},
        "warnings": warnings,
    }

    for bucket in buckets_to_compute:
        inv_initial, w1 = _sum_inventory_like_from_conn(
            conn, store_code, "DatiInventario", "INV", inv_init_date, inv_init_date, bucket, supplier_name
        )
        delivery, w2 = _sum_delivery_from_conn(conn, store_code, start, end, bucket, supplier_name)
        tx_in, w3 = _sum_inventory_like_from_conn(
            conn, store_code, "DatiTX", "TXIN", start, end, bucket, supplier_name
        )
        tx_out, w4 = _sum_inventory_like_from_conn(
            conn, store_code, "DatiTX", "TXOUT", start, end, bucket, supplier_name
        )
        inv_final, w5 = _sum_inventory_like_from_conn(
            conn, store_code, "DatiInventario", "INV", end, end, bucket, supplier_name
        )
        # In DB la movimentazione waste è registrata come "WASTE CRUDO"
        waste, w6 = _sum_inventory_like_from_conn(
            conn, store_code, "DatiInventario", "WASTE CRUDO", start, end, bucket, supplier_name
        )

        warnings.extend(w1 + w2 + w3 + w4 + w5 + w6)

        consumo = float(inv_initial + delivery + tx_in - tx_out - inv_final)

        out["buckets"][bucket] = {
            "inv_initial": float(inv_initial),
            "delivery": float(delivery),
            "tx_in": float(tx_in),
            "tx_out": float(tx_out),
            "inv_final": float(inv_final),
            "waste": float(waste),
            "consumption": float(consumo),
            "consumption_pct": float(pct(consumo)),
            "waste_pct": float(pct(waste)),
        }

    return out

def get_consumption_summary_single_connection(
    store_code: str,
    start_iso: str,
    end_iso: str,
    supplier_name: Optional[str] = None,
    buckets: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Versione ottimizzata per chiamate multi-store.

    A differenza di get_consumption_summary(), apre UNA sola connessione per lo store
    e la riusa per tutte le query, riducendo drasticamente il numero di connessioni/cursor.

    In SQL Server (unico DB): per calcoli multi-store, è preferibile aprire una sola connessione
    a monte e chiamare get_consumption_summary_on_conn() per ogni store.
    """
    conn = get_connection(store_code)
    try:
        return get_consumption_summary_on_conn(
            conn,
            store_code=store_code,
            start_iso=start_iso,
            end_iso=end_iso,
            supplier_name=supplier_name,
            buckets=buckets,
        )
    finally:
        try:
            conn.close()
        except Exception:
            log_swallowed('analysis_repository:1012')
