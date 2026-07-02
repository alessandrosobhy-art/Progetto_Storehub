from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from app_db import get_connection, sql_date, sql_trim


def _qname(name: str) -> str:
    return f"[{str(name).replace(']', ']]')}]"


def _parse_date_iso(s: str) -> date:
    s = (s or "").strip()
    return datetime.strptime(s, "%Y-%m-%d").date()


def _norm_si_no(v: Any) -> str:
    s = str(v or "").strip().upper()
    if s.startswith("N"):
        return "NO"
    if s in ("0", "FALSE", "F"):
        return "NO"
    if not s:
        return "SI"
    return "SI"


def _get_table_columns_ordered(cur, table: str) -> List[str]:
    cur.execute(f"SELECT * FROM {_qname(table)} WHERE 1=0")
    return [d[0] for d in (cur.description or [])]


def _find_col(cols: List[str], keys: Tuple[str, ...]) -> Optional[str]:
    low = [c.lower() for c in cols]

    # 1) match esatto
    for k in keys:
        kl = k.lower()
        for i, c in enumerate(low):
            if c == kl:
                return cols[i]

    # 2) match parziale
    for k in keys:
        kl = k.lower()
        for i, c in enumerate(low):
            if kl and kl in c:
                return cols[i]
    return None


@dataclass(frozen=True)
class PrimaNotaColumns:
    date_col: str
    categoria_col: str
    voce_col: str
    tipo_col: str
    valore_col: str
    site_col: Optional[str] = None


@dataclass(frozen=True)
class ElenchiColumns:
    ticket_col: Optional[str]
    delivery_col: Optional[str]
    coupon_col: Optional[str]
    tc_col: Optional[str]
    dc_col: Optional[str]
    cc_col: Optional[str]


def _guess_primanota_columns(cols: List[str]) -> PrimaNotaColumns:
    site_col = _find_col(cols, ("site", "store", "negoz", "punto"))
    date_col = _find_col(cols, ("data", "date", "dt")) or (cols[1] if len(cols) > 1 else cols[0])
    categoria_col = _find_col(cols, ("categoria", "cat")) or (cols[2] if len(cols) > 2 else "CATEGORIA")
    voce_col = _find_col(cols, ("voce", "descr", "dett")) or (cols[3] if len(cols) > 3 else "VOCE")
    tipo_col = _find_col(cols, ("tipo", "si", "flag")) or (cols[4] if len(cols) > 4 else "TIPO")
    valore_col = _find_col(cols, ("valore", "import", "euro", "amm", "tot")) or _last_data_col(cols)

    # evita che site_col sovrascriva altri campi
    used = {date_col, categoria_col, voce_col, tipo_col, valore_col}
    if site_col in used:
        site_col = None

    return PrimaNotaColumns(
        date_col=date_col,
        categoria_col=categoria_col,
        voce_col=voce_col,
        tipo_col=tipo_col,
        valore_col=valore_col,
        site_col=site_col,
    )


def _resolve_primanota_columns(store_code: str) -> PrimaNotaColumns:
    conn = get_connection(store_code, read_only=True)
    try:
        cur = conn.cursor()
        cols = _get_table_columns_ordered(cur, "DATIPRIMANOTA")
        if not cols:
            return PrimaNotaColumns("DATA", "CATEGORIA", "VOCE", "TIPO", "VALORE", "SITE")
        return _guess_primanota_columns(cols)
    finally:
        try:
            conn.close()
        except Exception:
            pass


def load_primanota_month_agg(
    store_code: str,
    *,
    year: int,
    month: int,
    categories: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Carica aggregati mensili da DATIPRIMANOTA.

    Output (lista di righe):
      {
        "date": "YYYY-MM-DD",
        "categoria": "...",
        "voce": "...",
        "tipo": "SI"|"NO",
        "sum": <float>
      }

    Note:
    - Filtra automaticamente per store/site se la colonna esiste.
    - Per performance, di default limita alle categorie usate nel Rendiconto.
    """
    if year < 2000 or not (1 <= month <= 12):
        return []

    cols = _resolve_primanota_columns(str(store_code))

    start = date(int(year), int(month), 1)
    end = date(int(year) + 1, 1, 1) if month == 12 else date(int(year), int(month) + 1, 1)

    cats = categories or ["Dati chiusura", "Distinte", "Ticket", "Delivery", "Coupon"]
    in_placeholders = ",".join(["?"] * len(cats))

    where_parts = [
        f"{sql_date(_qname(cols.date_col))} >= ?",
        f"{sql_date(_qname(cols.date_col))} < ?",
        f"{_qname(cols.categoria_col)} IN ({in_placeholders})",
    ]
    params: List[Any] = [start, end] + cats

    if cols.site_col:
        where_parts.append(f"{sql_trim(_qname(cols.site_col))} = ?")
        params.append(str(store_code).strip())

    sql = f"""
    SELECT
      {sql_date(_qname(cols.date_col))} AS d,
      {_qname(cols.categoria_col)} AS c,
      {_qname(cols.voce_col)} AS v,
      {_qname(cols.tipo_col)} AS t,
      SUM({_qname(cols.valore_col)}) AS s
    FROM {_qname('DATIPRIMANOTA')}
    WHERE {' AND '.join(where_parts)}
    GROUP BY {sql_date(_qname(cols.date_col))}, {_qname(cols.categoria_col)}, {_qname(cols.voce_col)}, {_qname(cols.tipo_col)}
    ORDER BY {sql_date(_qname(cols.date_col))}, {_qname(cols.categoria_col)}, {_qname(cols.voce_col)}
    """

    conn = get_connection(store_code, read_only=True)
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        rows = cur.fetchall() or []

        out: List[Dict[str, Any]] = []
        for r in rows:
            dt_val = r[0]
            if isinstance(dt_val, datetime):
                dt_val = dt_val.date()
            if isinstance(dt_val, date):
                d_iso = dt_val.isoformat()
            else:
                d_iso = str(dt_val or "").strip()
                if not d_iso:
                    continue

            cat = str(r[1] or "")
            voce = str(r[2] or "")
            tipo = _norm_si_no(r[3])
            s = r[4]
            try:
                s_dec = s if isinstance(s, Decimal) else Decimal(str(s))
            except Exception:
                s_dec = Decimal("0")

            out.append({"date": d_iso, "categoria": cat, "voce": voce, "tipo": tipo, "sum": float(s_dec)})

        return out
    finally:
        try:
            conn.close()
        except Exception:
            pass


def load_primanota_month_agg_totals(
    store_code: str,
    *,
    year: int,
    month: int,
    categories: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Carica aggregati mensili SENZA raggruppamento per giorno.

    Output:
      {
        "categoria": "...",
        "voce": "...",
        "tipo": "SI"|"NO",
        "sum": <float>
      }
    """
    if year < 2000 or not (1 <= month <= 12):
        return []

    cols = _resolve_primanota_columns(str(store_code))

    start = date(int(year), int(month), 1)
    end = date(int(year) + 1, 1, 1) if month == 12 else date(int(year), int(month) + 1, 1)

    cats = categories or ["Dati chiusura", "Distinte", "Ticket", "Delivery", "Coupon"]
    in_placeholders = ",".join(["?"] * len(cats))

    where_parts = [
        f"{sql_date(_qname(cols.date_col))} >= ?",
        f"{sql_date(_qname(cols.date_col))} < ?",
        f"{_qname(cols.categoria_col)} IN ({in_placeholders})",
    ]
    params: List[Any] = [start, end] + cats

    if cols.site_col:
        where_parts.append(f"{sql_trim(_qname(cols.site_col))} = ?")
        params.append(str(store_code).strip())

    sql = f"""
    SELECT
      {_qname(cols.categoria_col)} AS c,
      {_qname(cols.voce_col)} AS v,
      {_qname(cols.tipo_col)} AS t,
      SUM({_qname(cols.valore_col)}) AS s
    FROM {_qname('DATIPRIMANOTA')}
    WHERE {' AND '.join(where_parts)}
    GROUP BY {_qname(cols.categoria_col)}, {_qname(cols.voce_col)}, {_qname(cols.tipo_col)}
    ORDER BY {_qname(cols.categoria_col)}, {_qname(cols.voce_col)}
    """

    conn = get_connection(store_code, read_only=True)
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        rows = cur.fetchall() or []

        out: List[Dict[str, Any]] = []
        for r in rows:
            cat = str(r[0] or "")
            voce = str(r[1] or "")
            tipo = _norm_si_no(r[2])
            s = r[3]
            try:
                s_dec = s if isinstance(s, Decimal) else Decimal(str(s))
            except Exception:
                s_dec = Decimal("0")
            out.append({"categoria": cat, "voce": voce, "tipo": tipo, "sum": float(s_dec)})
        return out
    finally:
        try:
            conn.close()
        except Exception:
            pass



def load_primanota_month_agg_totals_multi(
    store_codes: List[str],
    *,
    year: int,
    month: int,
    categories: Optional[List[str]] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """Carica aggregati mensili per PIÙ store in una sola query (SQL Server).

    Ritorna una mappa:
      {
        "<site>": [ {categoria, voce, tipo, sum}, ... ],
        ...
      }

    Nota: in Access, di norma non esiste la colonna SITE (un db per store),
    quindi questa funzione va usata solo con backend SQL Server.
    """
    store_codes = [str(s).strip() for s in (store_codes or []) if str(s).strip()]
    if year < 2000 or not (1 <= month <= 12) or not store_codes:
        return {}

    cols = _resolve_primanota_columns(store_codes[0])

    start = date(int(year), int(month), 1)
    end = date(int(year) + 1, 1, 1) if month == 12 else date(int(year), int(month) + 1, 1)

    cats = categories or ["Dati chiusura", "Distinte", "Ticket", "Delivery", "Coupon"]
    cats = [str(c).strip() for c in (cats or []) if str(c).strip()]
    if not cats:
        cats = ["Dati chiusura", "Distinte", "Ticket", "Delivery", "Coupon"]

    if not cols.site_col:
        return {}

    in_cats = ",".join(["?"] * len(cats))
    in_sites = ",".join(["?"] * len(store_codes))

    where_parts = [
        f"{sql_date(_qname(cols.date_col))} >= ?",
        f"{sql_date(_qname(cols.date_col))} < ?",
        f"{_qname(cols.categoria_col)} IN ({in_cats})",
        f"{sql_trim(_qname(cols.site_col))} IN ({in_sites})",
    ]
    params: List[Any] = [start, end] + cats + store_codes

    sql = f"""
    SELECT
      {sql_trim(_qname(cols.site_col))} AS site,
      {_qname(cols.categoria_col)} AS c,
      {_qname(cols.voce_col)} AS v,
      {_qname(cols.tipo_col)} AS t,
      SUM({_qname(cols.valore_col)}) AS s
    FROM {_qname('DATIPRIMANOTA')}
    WHERE {' AND '.join(where_parts)}
    GROUP BY {sql_trim(_qname(cols.site_col))}, {_qname(cols.categoria_col)}, {_qname(cols.voce_col)}, {_qname(cols.tipo_col)}
    ORDER BY {sql_trim(_qname(cols.site_col))}, {_qname(cols.categoria_col)}, {_qname(cols.voce_col)}
    """

    conn = get_connection(store_codes[0], read_only=True)
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        rows = cur.fetchall() or []
        out: Dict[str, List[Dict[str, Any]]] = {}
        for r in rows:
            site = str(r[0] or "").strip()
            if not site:
                continue
            out.setdefault(site, []).append(
                {
                    "categoria": str(r[1] or ""),
                    "voce": str(r[2] or ""),
                    "tipo": _norm_si_no(r[3]),
                    "sum": float(r[4] or 0.0),
                }
            )
        return out
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _guess_elenchi_columns(cols: List[str]) -> ElenchiColumns:
    ticket_col = _find_col(cols, ("ticket",))
    delivery_col = _find_col(cols, ("delivery",))
    coupon_col = _find_col(cols, ("coupon", "buoni", "sconto"))

    # Flag: proviamo prima match esatto poi parziale; "cc" è delicato, quindi prima esatto.
    tc_col = _find_col(cols, ("tc", "t_cassa", "ticket_cassa"))
    dc_col = _find_col(cols, ("dc", "d_cassa", "delivery_cassa"))

    cc_exact = _find_col(cols, ("cc",))
    cc_col = cc_exact or _find_col(cols, ("cc", "c_cassa", "coupon_cassa"))

    # Se manca coupon_col ma l'utente ha scritto che usa DELIVERY, fallback.
    if not coupon_col and delivery_col:
        coupon_col = delivery_col

    return ElenchiColumns(
        ticket_col=ticket_col,
        delivery_col=delivery_col,
        coupon_col=coupon_col,
        tc_col=tc_col,
        dc_col=dc_col,
        cc_col=cc_col,
    )


def _resolve_elenchi_columns(store_code: str) -> ElenchiColumns:
    conn = get_connection(store_code, read_only=True)
    try:
        cur = conn.cursor()
        cols = _get_table_columns_ordered(cur, "ELENCHI")
        if not cols:
            return ElenchiColumns("TICKET", "DELIVERY", "COUPON", "TC", "DC", "CC")
        return _guess_elenchi_columns(cols)
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _fetch_elenchi_options(
    cur,
    *,
    value_col: Optional[str],
    flag_col: Optional[str],
) -> List[Dict[str, str]]:
    if not value_col:
        return []
    if flag_col:
        sql = f"""
        SELECT {sql_trim(_qname(value_col))} AS v, {_qname(flag_col)} AS f
        FROM {_qname('ELENCHI')}
        WHERE {_qname(value_col)} IS NOT NULL AND {sql_trim(_qname(value_col))} <> ''
        """
    else:
        sql = f"""
        SELECT {sql_trim(_qname(value_col))} AS v
        FROM {_qname('ELENCHI')}
        WHERE {_qname(value_col)} IS NOT NULL AND {sql_trim(_qname(value_col))} <> ''
        """

    cur.execute(sql)
    rows = cur.fetchall() or []

    # mapping valore -> tipo (SI/NO)
    mp: Dict[str, str] = {}
    for r in rows:
        v = str(r[0] or "").strip()
        # Alcuni DB hanno righe "vuote" che finiscono a 0 (es. colonne numeriche).
        # Evitiamo di popolare le tendine con "0" ripetuti.
        if not v or v in {"0", "0,0", "0.0", "0,00", "0.00"}:
            continue
        t = "SI"
        if flag_col:
            t = _norm_si_no(r[1] if len(r) > 1 else "")
        mp[v] = t

    return [{"value": k, "tipo": mp[k]} for k in sorted(mp.keys(), key=lambda s: s.lower())]


def get_elenchi_options(*, store_code: str) -> Dict[str, Any]:
    """Opzioni per tendine Ticket/Delivery/Coupon.

    Ritorna:
      {
        "tickets": [{value, tipo}, ...],
        "deliveries": [...],
        "coupons": [...],
      }
    """
    cols = _resolve_elenchi_columns(str(store_code))
    conn = get_connection(store_code, read_only=True)
    try:
        cur = conn.cursor()
        tickets = _fetch_elenchi_options(cur, value_col=cols.ticket_col, flag_col=cols.tc_col)
        deliveries = _fetch_elenchi_options(cur, value_col=cols.delivery_col, flag_col=cols.dc_col)
        coupons = _fetch_elenchi_options(cur, value_col=cols.coupon_col, flag_col=cols.cc_col)
        return {"tickets": tickets, "deliveries": deliveries, "coupons": coupons}
    finally:
        try:
            conn.close()
        except Exception:
            pass


def load_primanota_range_agg(
    store_code: str,
    *,
    start_date: date,
    end_date: date,
    categories: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Carica aggregati per giorno nel range [start_date, end_date] inclusivo."""
    if not start_date or not end_date:
        return []
    if end_date < start_date:
        start_date, end_date = end_date, start_date

    cols = _resolve_primanota_columns(str(store_code))
    end_exclusive = end_date + timedelta(days=1)

    cats = categories or ["Dati chiusura", "Distinte", "Ticket", "Delivery", "Coupon"]
    in_placeholders = ",".join(["?"] * len(cats))

    where = [
        f"{sql_date(_qname(cols.date_col))} >= ?",
        f"{sql_date(_qname(cols.date_col))} < ?",
        f"{_qname(cols.categoria_col)} IN ({in_placeholders})",
    ]
    params: List[Any] = [start_date, end_exclusive] + cats

    if cols.site_col:
        where.append(f"{sql_trim(_qname(cols.site_col))} = ?")
        params.append(str(store_code).strip())

    sql = f"""
    SELECT
      {sql_date(_qname(cols.date_col))} AS d,
      {_qname(cols.categoria_col)} AS c,
      {_qname(cols.voce_col)} AS v,
      {_qname(cols.tipo_col)} AS t,
      SUM({_qname(cols.valore_col)}) AS s
    FROM {_qname('DATIPRIMANOTA')}
    WHERE {' AND '.join(where)}
    GROUP BY {sql_date(_qname(cols.date_col))}, {_qname(cols.categoria_col)}, {_qname(cols.voce_col)}, {_qname(cols.tipo_col)}
    ORDER BY {sql_date(_qname(cols.date_col))}, {_qname(cols.categoria_col)}, {_qname(cols.voce_col)}
    """

    conn = get_connection(store_code, read_only=True)
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        rows = cur.fetchall() or []
        out: List[Dict[str, Any]] = []
        for r in rows:
            dt_val = r[0]
            if isinstance(dt_val, datetime):
                dt_val = dt_val.date()
            if isinstance(dt_val, date):
                d_iso = dt_val.isoformat()
            else:
                d_iso = str(dt_val or "").strip()
                if not d_iso:
                    continue
            cat = str(r[1] or "")
            voce = str(r[2] or "")
            tipo = _norm_si_no(r[3])
            s = r[4]
            try:
                s_dec = s if isinstance(s, Decimal) else Decimal(str(s))
            except Exception:
                s_dec = Decimal("0")
            out.append({"date": d_iso, "categoria": cat, "voce": voce, "tipo": tipo, "sum": float(s_dec)})
        return out
    finally:
        try:
            conn.close()
        except Exception:
            pass


def load_primanota_day(
    *,
    store_code: str,
    data_iso: str,
    categories: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    cols = _resolve_primanota_columns(str(store_code))
    d = _parse_date_iso(data_iso)

    cats = categories or ["Dati chiusura", "Distinte", "Ticket", "Delivery", "Coupon"]
    in_placeholders = ",".join(["?"] * len(cats))

    where = [f"{sql_date(_qname(cols.date_col))} = ?", f"{_qname(cols.categoria_col)} IN ({in_placeholders})"]
    params: List[Any] = [d] + cats

    if cols.site_col:
        where.append(f"{sql_trim(_qname(cols.site_col))} = ?")
        params.append(str(store_code).strip())

    sql = f"""
    SELECT
      {_qname(cols.categoria_col)} AS c,
      {_qname(cols.voce_col)} AS v,
      {_qname(cols.tipo_col)} AS t,
      {_qname(cols.valore_col)} AS a
    FROM {_qname('DATIPRIMANOTA')}
    WHERE {' AND '.join(where)}
    ORDER BY {_qname(cols.categoria_col)}, {_qname(cols.voce_col)}
    """

    conn = get_connection(store_code, read_only=True)
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        rows = cur.fetchall() or []
        out: List[Dict[str, Any]] = []
        for r in rows:
            cat = str(r[0] or "")
            voce = str(r[1] or "")
            tipo = _norm_si_no(r[2])
            val = r[3]
            try:
                val_dec = val if isinstance(val, Decimal) else Decimal(str(val))
            except Exception:
                val_dec = Decimal("0")
            out.append({"categoria": cat, "voce": voce, "tipo": tipo, "valore": float(val_dec)})
        return out
    finally:
        try:
            conn.close()
        except Exception:
            pass


def replace_primanota_day(
    *,
    store_code: str,
    data_iso: str,
    entries: List[Dict[str, Any]],
    categories: Optional[List[str]] = None,
) -> None:
    """Sostituisce (delete+insert) le righe di DATIPRIMANOTA per data e categorie."""
    cols = _resolve_primanota_columns(str(store_code))
    d = _parse_date_iso(data_iso)

    cats = categories or ["Dati chiusura", "Distinte", "Ticket", "Delivery", "Coupon"]
    in_placeholders = ",".join(["?"] * len(cats))

    where = [f"{sql_date(_qname(cols.date_col))} = ?", f"{_qname(cols.categoria_col)} IN ({in_placeholders})"]
    del_params: List[Any] = [d] + cats
    if cols.site_col:
        where.append(f"{sql_trim(_qname(cols.site_col))} = ?")
        del_params.append(str(store_code).strip())

    del_sql = f"DELETE FROM {_qname('DATIPRIMANOTA')} WHERE {' AND '.join(where)}"

    # insert
    col_names: List[str] = []
    if cols.site_col:
        col_names.append(cols.site_col)
    col_names += [cols.date_col, cols.categoria_col, cols.voce_col, cols.tipo_col, cols.valore_col]
    insert_cols = ",".join(_qname(c) for c in col_names)
    placeholders = ",".join(["?"] * len(col_names))
    ins_sql = f"INSERT INTO {_qname('DATIPRIMANOTA')} ({insert_cols}) VALUES ({placeholders})"

    conn = get_connection(store_code)
    try:
        cur = conn.cursor()
        cur.execute(del_sql, del_params)

        for e in entries or []:
            cat = str(e.get("categoria") or "").strip()
            voce = str(e.get("voce") or "").strip()
            tipo = _norm_si_no(e.get("tipo") or "SI")
            val = e.get("valore")
            try:
                val_f = float(val)
            except Exception:
                val_f = 0.0

            if not (cat and voce):
                continue

            row: List[Any] = []
            if cols.site_col:
                row.append(str(store_code).strip())
            row += [d, cat, voce, tipo, val_f]
            cur.execute(ins_sql, row)

        conn.commit()
    finally:
        try:
            conn.close()
        except Exception:
            pass


def delete_primanota_day(
    *,
    store_code: str,
    data_iso: str,
    categories: Optional[List[str]] = None,
) -> None:
    """Elimina le righe di DATIPRIMANOTA per una data (opzionalmente filtrando per categorie).

    Usato per cancellare completamente una Distinta di Cassa.
    """
    cols = _resolve_primanota_columns(str(store_code))
    d = _parse_date_iso(data_iso)

    where: List[str] = [f"{sql_date(_qname(cols.date_col))} = ?"]
    params: List[Any] = [d]

    if categories:
        in_placeholders = ",".join(["?"] * len(categories))
        where.append(f"{_qname(cols.categoria_col)} IN ({in_placeholders})")
        params += list(categories)

    if cols.site_col:
        where.append(f"{sql_trim(_qname(cols.site_col))} = ?")
        params.append(str(store_code).strip())

    del_sql = f"DELETE FROM {_qname('DATIPRIMANOTA')} WHERE {' AND '.join(where)}"

    conn = get_connection(store_code)
    try:
        cur = conn.cursor()
        cur.execute(del_sql, params)
        conn.commit()
    finally:
        try:
            conn.close()
        except Exception:
            pass



def sum_categoria_period(
    *,
    store_code: str,
    start_iso: str,
    end_iso: str,
    categoria: str = "Distinte",
) -> float:
    """Somma il valore di una singola categoria (es. 'Distinte') nel periodo [start, end] inclusivo."""
    cols = _resolve_primanota_columns(str(store_code))

    d_start = _parse_date_iso(start_iso)
    d_end = _parse_date_iso(end_iso)
    if d_end < d_start:
        d_start, d_end = d_end, d_start

    where = [
        f"{sql_date(_qname(cols.date_col))} >= ?",
        f"{sql_date(_qname(cols.date_col))} <= ?",
        f"{_qname(cols.categoria_col)} = ?",
    ]
    params: List[Any] = [d_start, d_end, str(categoria)]

    if cols.site_col:
        where.append(f"{sql_trim(_qname(cols.site_col))} = ?")
        params.append(str(store_code).strip())

    sql = f"""
    SELECT SUM({_qname(cols.valore_col)}) AS s
    FROM {_qname('DATIPRIMANOTA')}
    WHERE {' AND '.join(where)}
    """

    conn = get_connection(store_code, read_only=True)
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        row = cur.fetchone()
        s = row[0] if row else 0
        try:
            s_dec = s if isinstance(s, Decimal) else Decimal(str(s))
        except Exception:
            s_dec = Decimal('0')
        return float(s_dec)
    finally:
        try:
            conn.close()
        except Exception:
            pass


def sum_categoria_by_day_range(
    *,
    store_code: str,
    start_iso: str,
    end_iso: str,
    categoria: str = "Distinte",
) -> Dict[str, float]:
    """Ritorna somma per giorno di una categoria nel periodo [start, end] inclusivo.

    Output:
      {"YYYY-MM-DD": <float>, ...}
    """
    cols = _resolve_primanota_columns(str(store_code))

    d_start = _parse_date_iso(start_iso)
    d_end = _parse_date_iso(end_iso)
    if d_end < d_start:
        d_start, d_end = d_end, d_start

    where = [
        f"{sql_date(_qname(cols.date_col))} >= ?",
        f"{sql_date(_qname(cols.date_col))} <= ?",
        f"{_qname(cols.categoria_col)} = ?",
    ]
    params: List[Any] = [d_start, d_end, str(categoria)]

    if cols.site_col:
        where.append(f"{sql_trim(_qname(cols.site_col))} = ?")
        params.append(str(store_code).strip())

    sql = f"""
    SELECT
      {sql_date(_qname(cols.date_col))} AS d,
      SUM({_qname(cols.valore_col)}) AS s
    FROM {_qname('DATIPRIMANOTA')}
    WHERE {' AND '.join(where)}
    GROUP BY {sql_date(_qname(cols.date_col))}
    ORDER BY {sql_date(_qname(cols.date_col))}
    """

    conn = get_connection(store_code, read_only=True)
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        rows = cur.fetchall() or []

        out: Dict[str, float] = {}
        for r in rows:
            dt_val = r[0]
            if isinstance(dt_val, datetime):
                dt_val = dt_val.date()
            if isinstance(dt_val, date):
                d_iso = dt_val.isoformat()
            else:
                d_iso = str(dt_val or '').strip()
                if not d_iso:
                    continue

            s = r[1]
            try:
                s_dec = s if isinstance(s, Decimal) else Decimal(str(s))
            except Exception:
                s_dec = Decimal('0')
            out[d_iso] = float(s_dec)

        return out
    finally:
        try:
            conn.close()
        except Exception:
            pass


def sum_delivery_voce_range(
    *,
    store_code: str,
    start_iso: str,
    end_iso: str,
) -> Dict[str, float]:
    """Somma per VOCE della categoria 'Delivery' nel periodo [start, end] inclusivo.

    Output:
      {"DELIVEROO": 123.45, "DELIVEROO CONTANTI": 67.89, ...}

    Note:
    - Filtra automaticamente per store/site se la colonna esiste.
    - Non applica logiche SI/NO: somma i valori come salvati in DATIPRIMANOTA.
    """
    cols = _resolve_primanota_columns(str(store_code))

    d_start = _parse_date_iso(start_iso)
    d_end = _parse_date_iso(end_iso)
    if d_end < d_start:
        d_start, d_end = d_end, d_start

    where = [
        f"{sql_date(_qname(cols.date_col))} >= ?",
        f"{sql_date(_qname(cols.date_col))} <= ?",
        f"{_qname(cols.categoria_col)} = ?",
    ]
    params: List[Any] = [d_start, d_end, "Delivery"]

    if cols.site_col:
        where.append(f"{sql_trim(_qname(cols.site_col))} = ?")
        params.append(str(store_code).strip())

    sql = f"""
    SELECT
      {_qname(cols.voce_col)} AS v,
      SUM({_qname(cols.valore_col)}) AS s
    FROM {_qname('DATIPRIMANOTA')}
    WHERE {' AND '.join(where)}
    GROUP BY {_qname(cols.voce_col)}
    """

    conn = get_connection(store_code, read_only=True)
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        rows = cur.fetchall() or []

        out: Dict[str, float] = {}
        for r in rows:
            voce = str(r[0] or "").strip()
            if not voce:
                continue
            s = r[1]
            try:
                s_dec = s if isinstance(s, Decimal) else Decimal(str(s))
            except Exception:
                s_dec = Decimal("0")
            out[voce] = float(s_dec)

        return out
    finally:
        try:
            conn.close()
        except Exception:
            pass
