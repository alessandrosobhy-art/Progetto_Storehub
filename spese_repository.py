from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from app_db import get_connection, sql_date, sql_trim, supports_schema_alter

REPO_PATCH_VERSION = "sql_fix_v1.0.2"


# -------------------------
# Helpers
# -------------------------

def _qname(name: str) -> str:
    # Escape ']' for Access bracket quoting
    return f"[{str(name).replace(']', ']]')}]"


def _parse_date_iso(s: str) -> date:
    s = (s or "").strip()
    # Accept YYYY-MM-DD (input type=date)
    return datetime.strptime(s, "%Y-%m-%d").date()


def _money_to_decimal(v: str) -> Decimal:
    s = (v or "").strip().replace(",", ".")
    return Decimal(s)

def _last_data_col(cols: List[str]) -> str:
    """Fallback per individuare una colonna 'dato' (es. importo).

    Scansiona da destra verso sinistra escludendo colonne tecniche/metadati
    (es. row_uuid, site, foto_file)."""
    if not cols:
        return "IMPORTO"

    def norm(s: str) -> str:
        return (s or "").strip().lower().replace(" ", "")

    exclude = {
        "row_uuid",
        "rowuuid",
        "uuid",
        "site",
        "store",
        "foto_file",
        "fotofile",
        "foto",
        "image",
        "allegato",
        "attachment",
    }

    for c in reversed(cols):
        n = norm(c)
        if not n:
            continue
        if n in exclude or n.endswith("_uuid"):
            continue
        return c

    return cols[-1]


@dataclass(frozen=True)
class SpeseColumns:
    date_col: str
    tipo_col: str
    descr_col: str
    doc_col: str
    importo_col: str
    site_col: Optional[str] = None  # opzionale: se esiste nel DB
    foto_col: Optional[str] = None  # opzionale: nome file foto allegata


def _normalize_text(v: str) -> str:
    return (v or "").strip()


def _get_table_columns_ordered(cur, table: str) -> List[str]:
    cur.execute(f"SELECT * FROM {_qname(table)} WHERE 1=0")
    return [d[0] for d in (cur.description or [])]


def _guess_spese_columns(columns: List[str]) -> SpeseColumns:
    cols = columns[:]  # ordered
    low = [c.lower() for c in cols]

    def find_by_keywords(keys: Tuple[str, ...]) -> Optional[str]:
        for k in keys:
            for i, c in enumerate(low):
                if k in c:
                    return cols[i]
        return None

    date_col = find_by_keywords(("data", "date", "dt")) or (cols[0] if cols else "DATA")
    importo_col = find_by_keywords(("import", "euro", "tot", "val")) or _last_data_col(cols)
    tipo_col = find_by_keywords(("tipo", "operaz")) or None
    descr_col = find_by_keywords(("fornit", "spesa", "descr", "causal", "note")) or None
    doc_col = find_by_keywords(("scontr", "fatt", "doc", "numero", "nr")) or None

    # colonna site/store opzionale
    site_col = find_by_keywords(("site", "store", "negoz", "punto")) or None

    # colonna foto/allegato opzionale
    foto_col = find_by_keywords(("foto", "img", "image", "alleg", "attachment")) or None

    used = {c for c in (date_col, importo_col, tipo_col, descr_col, doc_col) if c}
    remaining = [c for c in cols if c not in used and c != site_col and c != foto_col]

    def take_next() -> Optional[str]:
        return remaining.pop(0) if remaining else None

    if not tipo_col:
        tipo_col = take_next() or (cols[1] if len(cols) > 1 else "TIPO")
    if not descr_col:
        descr_col = take_next() or (cols[2] if len(cols) > 2 else "DESCRIZIONE")
    if not doc_col:
        doc_col = take_next() or (cols[3] if len(cols) > 3 else "DOCUMENTO")

    return SpeseColumns(
        date_col=date_col,
        tipo_col=tipo_col,
        descr_col=descr_col,
        doc_col=doc_col,
        importo_col=importo_col,
        site_col=site_col,
        foto_col=foto_col,
    )


def _ensure_foto_column(store_code: str, table_name: str = "SPESE", col_name: str = "FOTO_FILE") -> None:
    """Assicura l'esistenza della colonna per il filename della foto.

    In caso di errori (permessi, DB bloccato, ecc.) ignora senza bloccare l'app.
    """
    if not supports_schema_alter():
        return

    conn = get_connection(store_code)
    try:
        cur = conn.cursor()
        cols = _get_table_columns_ordered(cur, table_name)
        if any(str(c).lower() == col_name.lower() for c in (cols or [])):
            return

        # Access SQL: TEXT(255)
        try:
            cur.execute(f"ALTER TABLE {_qname(table_name)} ADD COLUMN {_qname(col_name)} TEXT(255)")
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _resolve_spese_columns(store_code: str, *, ensure_schema: bool = True) -> SpeseColumns:
    # Di default manteniamo l'auto-fix dello schema (colonna foto).
    # Per operazioni di sola lettura/aggregazione (es. riepiloghi) possiamo
    # disattivarlo per evitare ALTER TABLE + upload su SharePoint.
    if ensure_schema:
        _ensure_foto_column(str(store_code))

    conn = get_connection(store_code, read_only=not ensure_schema)
    try:
        cur = conn.cursor()
        cols = _get_table_columns_ordered(cur, "SPESE")
        if not cols:
            # fallback hard-coded
            return SpeseColumns("DATA", "TIPO", "FORNITORE", "DOCUMENTO", "IMPORTO", None, None)
        return _guess_spese_columns(cols)
    finally:
        try:
            conn.close()
        except Exception:
            pass


def sum_spese_month_total_net(*, store_code: str, year: int, month: int) -> float:
    """Totale spese NETTO nel mese (totale - note credito).

    Pensata per riepiloghi multi-store: una singola query aggregata e nessun
    ensure/ALTER schema.
    """
    if year < 2000 or not (1 <= month <= 12):
        return 0.0

    cols = _resolve_spese_columns(str(store_code), ensure_schema=False)

    start = date(int(year), int(month), 1)
    end = date(int(year) + 1, 1, 1) if month == 12 else date(int(year), int(month) + 1, 1)

    where_parts = [
        f"{sql_date(_qname(cols.date_col))} >= ?",
        f"{sql_date(_qname(cols.date_col))} < ?",
    ]
    params: List[Any] = [start, end]
    if cols.site_col:
        where_parts.append(f"{sql_trim(_qname(cols.site_col))} = ?")
        params.append(str(store_code).strip())

    sql = f"""
    SELECT
      {_qname(cols.tipo_col)} AS sp_tipo,
      SUM({_qname(cols.importo_col)}) AS sp_sum
    FROM {_qname('SPESE')}
    WHERE {' AND '.join(where_parts)}
    GROUP BY {_qname(cols.tipo_col)}
    """

    def _is_nota_credito(v: str) -> bool:
        s = (v or "").strip().lower()
        return ("nota" in s) and ("credit" in s)

    total = Decimal("0")
    nc = Decimal("0")

    conn = get_connection(str(store_code), read_only=True)
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        rows = cur.fetchall() or []
        for r in rows:
            tipo = str(r[0] or "")
            s = r[1]
            try:
                s_dec = s if isinstance(s, Decimal) else Decimal(str(s))
            except Exception:
                s_dec = Decimal("0")
            total += s_dec
            if _is_nota_credito(tipo):
                nc += s_dec
    finally:
        try:
            conn.close()
        except Exception:
            pass

    net = total - nc

def sum_spese_month_total_net_multi(store_codes: List[str], *, year: int, month: int) -> Dict[str, float]:
    """Totale spese NETTO nel mese per più store (solo SQL Server).

    Restituisce un dict {site: net}.
    In Access non è applicabile (DB separati per store), quindi ritorna {}.
    """
    # Solo SQL Server: in Access i dati sono su DB diversi.
    if supports_schema_alter():
        return {}

    codes = [str(c).strip() for c in (store_codes or []) if str(c).strip()]
    if not codes:
        return {}

    if year < 2000 or not (1 <= int(month) <= 12):
        return {c: 0.0 for c in codes}

    cols = _resolve_spese_columns(codes[0], ensure_schema=False)
    if not cols.site_col:
        # Se manca la colonna site non possiamo distinguere gli store.
        return {}

    start = date(int(year), int(month), 1)
    end = date(int(year) + 1, 1, 1) if int(month) == 12 else date(int(year), int(month) + 1, 1)

    # IN (?, ?, ...) dinamico
    in_ph = ", ".join(["?"] * len(codes))

    where_parts = [
        f"{sql_date(_qname(cols.date_col))} >= ?",
        f"{sql_date(_qname(cols.date_col))} < ?",
        f"{sql_trim(_qname(cols.site_col))} IN ({in_ph})",
    ]
    params: List[Any] = [start, end]
    params.extend(codes)

    sql = f"""
    SELECT
      {sql_trim(_qname(cols.site_col))} AS sp_site,
      {_qname(cols.tipo_col)} AS sp_tipo,
      SUM({_qname(cols.importo_col)}) AS sp_sum
    FROM {_qname('SPESE')}
    WHERE {' AND '.join(where_parts)}
    GROUP BY {sql_trim(_qname(cols.site_col))}, {_qname(cols.tipo_col)}
    """

    def _is_nota_credito(v: str) -> bool:
        s = (v or "").strip().lower()
        return ("nota" in s) and ("credit" in s)

    total_by: Dict[str, Decimal] = {c: Decimal("0") for c in codes}
    nc_by: Dict[str, Decimal] = {c: Decimal("0") for c in codes}

    conn = get_connection(None, read_only=True)
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        rows = cur.fetchall() or []
        for r in rows:
            site = str(r[0] or "").strip()
            if not site:
                continue
            tipo = str(r[1] or "")
            s = r[2]
            try:
                s_dec = s if isinstance(s, Decimal) else Decimal(str(s))
            except Exception:
                s_dec = Decimal("0")
            total_by[site] = total_by.get(site, Decimal("0")) + s_dec
            if _is_nota_credito(tipo):
                nc_by[site] = nc_by.get(site, Decimal("0")) + s_dec
    finally:
        try:
            conn.close()
        except Exception:
            pass

    out: Dict[str, float] = {}
    for c in codes:
        net = total_by.get(c, Decimal("0")) - nc_by.get(c, Decimal("0"))
        out[c] = float(net)

    return out

    return float(net)


# -------------------------
# Public API
# -------------------------

def insert_spesa(
    *,
    store_code: str,
    data_iso: str,
    tipo_operazione: str,
    fornitore_spesa: str,
    documento: str,
    importo_euro: str,
    foto_file: str | None = None,
) -> None:
    cols = _resolve_spese_columns(str(store_code))
    d = _parse_date_iso(data_iso)
    imp = float(_money_to_decimal(importo_euro))

    values = [
        d,
        _normalize_text(tipo_operazione),
        _normalize_text(fornitore_spesa),
        _normalize_text(documento),
        imp,
    ]
    col_names = [cols.date_col, cols.tipo_col, cols.descr_col, cols.doc_col, cols.importo_col]

    # Se nel DB esiste una colonna store/site e non è una delle 5, valorizziamo automaticamente.
    if cols.site_col and cols.site_col not in col_names:
        col_names.append(cols.site_col)
        values.append(str(store_code).strip())

    # Colonna foto (nome file) se presente
    if cols.foto_col and cols.foto_col not in col_names:
        col_names.append(cols.foto_col)
        values.append((foto_file or "").strip())

    placeholders = ",".join(["?"] * len(values))
    sql = f"INSERT INTO {_qname('SPESE')} ({','.join(_qname(c) for c in col_names)}) VALUES ({placeholders})"

    conn = get_connection(store_code)
    try:
        cur = conn.cursor()
        cur.execute(sql, values)
        conn.commit()
    finally:
        try:
            conn.close()
        except Exception:
            pass


def list_spese_month(
    *,
    store_code: str,
    year: int,
    month: int,
) -> Dict[str, Any]:
    cols = _resolve_spese_columns(str(store_code))

    start = date(int(year), int(month), 1)
    end = date(int(year) + 1, 1, 1) if int(month) == 12 else date(int(year), int(month) + 1, 1)

    select_parts = [
        f"{sql_date(_qname(cols.date_col))} AS sp_date",
        f"{_qname(cols.tipo_col)} AS sp_tipo",
        f"{_qname(cols.descr_col)} AS sp_descr",
        f"{_qname(cols.doc_col)} AS sp_doc",
        f"{_qname(cols.importo_col)} AS sp_importo",
    ]
    if cols.foto_col:
        select_parts.append(f"{_qname(cols.foto_col)} AS sp_foto")

    where_parts = [
        f"{sql_date(_qname(cols.date_col))} >= ?",
        f"{sql_date(_qname(cols.date_col))} < ?",
    ]
    params: List[Any] = [start, end]

    if cols.site_col:
        where_parts.append(f"{sql_trim(_qname(cols.site_col))} = ?")
        params.append(str(store_code).strip())

    select_sql = ", ".join(select_parts)
    sql = f"""
    SELECT
      {select_sql}
    FROM {_qname('SPESE')}
    WHERE {" AND ".join(where_parts)}
    ORDER BY {sql_date(_qname(cols.date_col))} DESC
    """

    conn = get_connection(store_code)
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        rows = cur.fetchall() or []
        out_rows: List[Dict[str, Any]] = []
        total = Decimal("0")

        for r in rows:
            dt_val = r[0]
            if isinstance(dt_val, datetime):
                dt_val = dt_val.date()
            if isinstance(dt_val, date):
                date_display = dt_val.strftime("%d/%m/%Y")
                date_iso = dt_val.isoformat()
            else:
                date_display = str(dt_val or "")
                date_iso = ""

            imp = r[4]
            try:
                imp_dec = imp if isinstance(imp, Decimal) else Decimal(str(imp))
            except Exception:
                imp_dec = Decimal("0")

            total += imp_dec

            foto_file = ""
            if cols.foto_col:
                try:
                    foto_file = str(r[5] or "").strip()
                except Exception:
                    foto_file = ""

            out_rows.append(
                {
                    "data": date_display,
                    "data_iso": date_iso,
                    "tipo": str(r[1] or ""),
                    "tipo_raw": str(r[1] or ""),
                    "fornitore": str(r[2] or ""),
                    "fornitore_raw": str(r[2] or ""),
                    "documento": str(r[3] or ""),
                    "documento_raw": str(r[3] or ""),
                    "importo": float(imp_dec),
                    "importo_key": str(imp_dec),
                    "foto_file": foto_file,
                }
            )

        return {"rows": out_rows, "total": float(total)}
    finally:
        try:
            conn.close()
        except Exception:
            pass



def sum_spese_day(
    *,
    store_code: str,
    data_iso: str,
) -> Dict[str, Any]:
    """Somma spese per una singola data.

    Ritorna:
      - total: somma di tutte le spese
      - note_credito: somma delle spese con tipo "Nota di credito"
      - net: total - note_credito  (come da regola richiesta)
    """
    cols = _resolve_spese_columns(str(store_code))
    d = _parse_date_iso(data_iso)

    where_parts = [f"{sql_date(_qname(cols.date_col))} = ?"]
    params: List[Any] = [d]

    if cols.site_col:
        where_parts.append(f"{sql_trim(_qname(cols.site_col))} = ?")
        params.append(str(store_code).strip())

    sql = f"""
    SELECT
      {_qname(cols.tipo_col)} AS sp_tipo,
      {_qname(cols.importo_col)} AS sp_importo
    FROM {_qname('SPESE')}
    WHERE {" AND ".join(where_parts)}
    """

    def _is_nota_credito(v: str) -> bool:
        s = (v or "").strip().lower()
        return ("nota" in s) and ("credit" in s)

    conn = get_connection(store_code)
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        rows = cur.fetchall() or []

        total = Decimal("0")
        note_credito = Decimal("0")
        for r in rows:
            tipo = str(r[0] or "")
            imp = r[1]
            try:
                imp_dec = imp if isinstance(imp, Decimal) else Decimal(str(imp))
            except Exception:
                imp_dec = Decimal("0")

            total += imp_dec
            if _is_nota_credito(tipo):
                note_credito += imp_dec

        net = total - note_credito
        return {"total": float(total), "note_credito": float(note_credito), "net": float(net)}
    finally:
        try:
            conn.close()
        except Exception:
            pass



def sum_spese_month_by_day(
    *,
    store_code: str,
    year: int,
    month: int,
) -> Dict[str, Dict[str, float]]:
    """Somma spese per giorno nel mese.

    Output:
      {
        "YYYY-MM-DD": {"total": ..., "note_credito": ..., "net": ...},
        ...
      }
    """
    if year < 2000 or not (1 <= month <= 12):
        return {}

    cols = _resolve_spese_columns(str(store_code))

    start = date(int(year), int(month), 1)
    end = date(int(year) + 1, 1, 1) if int(month) == 12 else date(int(year), int(month) + 1, 1)

    where_parts = [
        f"{sql_date(_qname(cols.date_col))} >= ?",
        f"{sql_date(_qname(cols.date_col))} < ?",
    ]
    params: List[Any] = [start, end]

    if cols.site_col:
        where_parts.append(f"{sql_trim(_qname(cols.site_col))} = ?")
        params.append(str(store_code).strip())

    sql = f"""
    SELECT
      {sql_date(_qname(cols.date_col))} AS sp_date,
      {_qname(cols.tipo_col)} AS sp_tipo,
      SUM({_qname(cols.importo_col)}) AS sp_sum
    FROM {_qname('SPESE')}
    WHERE {" AND ".join(where_parts)}
    GROUP BY {sql_date(_qname(cols.date_col))}, {_qname(cols.tipo_col)}
    """

    def _is_nota_credito(v: str) -> bool:
        s = (v or "").strip().lower()
        return ("nota" in s) and ("credit" in s)

    by_day: Dict[str, Dict[str, Decimal]] = {}

    conn = get_connection(store_code)
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        rows = cur.fetchall() or []

        for r in rows:
            dt_val = r[0]
            if isinstance(dt_val, datetime):
                dt_val = dt_val.date()
            if isinstance(dt_val, date):
                d_iso = dt_val.isoformat()
            else:
                d_iso = str(dt_val or "")
                if not d_iso:
                    continue

            tipo = str(r[1] or "")
            s = r[2]
            try:
                s_dec = s if isinstance(s, Decimal) else Decimal(str(s))
            except Exception:
                s_dec = Decimal("0")

            day = by_day.setdefault(d_iso, {"total": Decimal("0"), "note_credito": Decimal("0")})
            day["total"] += s_dec
            if _is_nota_credito(tipo):
                day["note_credito"] += s_dec

        out: Dict[str, Dict[str, float]] = {}
        for d_iso, sums in by_day.items():
            total = sums.get("total", Decimal("0"))
            nc = sums.get("note_credito", Decimal("0"))
            net = total - nc
            out[d_iso] = {"total": float(total), "note_credito": float(nc), "net": float(net)}
        return out
    finally:
        try:
            conn.close()
        except Exception:
            pass



def delete_spesa(
    *,
    store_code: str,
    orig_data_iso: str,
    orig_tipo: str,
    orig_fornitore: str,
    orig_documento: str,
    orig_importo_key: str,
) -> int:
    """Cancella una spesa usando come chiave i valori originali.

    Nota: se esistono righe duplicate identiche, questa delete può rimuovere più righe.
    """
    cols = _resolve_spese_columns(str(store_code))
    d = _parse_date_iso(orig_data_iso)
    imp = float(_money_to_decimal(orig_importo_key))

    where_parts = [
        f"{sql_date(_qname(cols.date_col))} = ?",
        f"{sql_trim(_qname(cols.tipo_col))} = ?",
        f"{sql_trim(_qname(cols.descr_col))} = ?",
        f"{sql_trim(_qname(cols.doc_col))} = ?",
        f"{_qname(cols.importo_col)} = ?",
    ]
    params: List[Any] = [
        d,
        _normalize_text(orig_tipo),
        _normalize_text(orig_fornitore),
        _normalize_text(orig_documento),
        imp,
    ]

    if cols.site_col:
        where_parts.append(f"{sql_trim(_qname(cols.site_col))} = ?")
        params.append(_normalize_text(store_code))

    sql = f"DELETE FROM {_qname('SPESE')} WHERE " + " AND ".join(where_parts)

    conn = get_connection(store_code)
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        conn.commit()
        try:
            return int(cur.rowcount)
        except Exception:
            return 0
    finally:
        try:
            conn.close()
        except Exception:
            pass


def get_spesa_photo_file(
    *,
    store_code: str,
    orig_data_iso: str,
    orig_tipo: str,
    orig_fornitore: str,
    orig_documento: str,
    orig_importo_key: str,
) -> Optional[str]:
    """Ritorna il filename della foto associata alla spesa (se presente)."""
    cols = _resolve_spese_columns(str(store_code))
    if not cols.foto_col:
        return None

    d = _parse_date_iso(orig_data_iso)
    imp = float(_money_to_decimal(orig_importo_key))

    where_parts = [
        f"{sql_date(_qname(cols.date_col))} = ?",
        f"{sql_trim(_qname(cols.tipo_col))} = ?",
        f"{sql_trim(_qname(cols.descr_col))} = ?",
        f"{sql_trim(_qname(cols.doc_col))} = ?",
        f"{_qname(cols.importo_col)} = ?",
    ]
    params: List[Any] = [
        d,
        _normalize_text(orig_tipo),
        _normalize_text(orig_fornitore),
        _normalize_text(orig_documento),
        imp,
    ]

    if cols.site_col:
        where_parts.append(f"{sql_trim(_qname(cols.site_col))} = ?")
        params.append(_normalize_text(store_code))

    # Access: TOP 1
    sql = f"SELECT TOP 1 {_qname(cols.foto_col)} FROM {_qname('SPESE')} WHERE " + " AND ".join(where_parts)

    conn = get_connection(store_code)
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        row = cur.fetchone()
        if not row:
            return None
        return str(row[0] or '').strip() or None
    finally:
        try:
            conn.close()
        except Exception:
            pass


def update_spesa(
    *,
    store_code: str,
    orig_data_iso: str,
    orig_tipo: str,
    orig_fornitore: str,
    orig_documento: str,
    orig_importo_key: str,
    new_data_iso: str,
    new_tipo: str,
    new_fornitore: str,
    new_documento: str,
    new_importo_euro: str,
    new_foto_file: str | None = None,
) -> int:
    """Aggiorna una spesa usando come chiave i valori originali.

    Nota: se esistono righe duplicate identiche, questa update può aggiornare più righe.
    """
    cols = _resolve_spese_columns(str(store_code))
    od = _parse_date_iso(orig_data_iso)
    oimp = float(_money_to_decimal(orig_importo_key))
    nd = _parse_date_iso(new_data_iso)
    nimp = float(_money_to_decimal(new_importo_euro))

    set_parts = [
        f"{_qname(cols.date_col)} = ?",
        f"{_qname(cols.tipo_col)} = ?",
        f"{_qname(cols.descr_col)} = ?",
        f"{_qname(cols.doc_col)} = ?",
        f"{_qname(cols.importo_col)} = ?",
    ]
    if new_foto_file is not None and cols.foto_col:
        set_parts.append(f"{_qname(cols.foto_col)} = ?")
    set_sql = ", ".join(set_parts)

    where_parts = [
        f"{sql_date(_qname(cols.date_col))} = ?",
        f"{sql_trim(_qname(cols.tipo_col))} = ?",
        f"{sql_trim(_qname(cols.descr_col))} = ?",
        f"{sql_trim(_qname(cols.doc_col))} = ?",
        f"{_qname(cols.importo_col)} = ?",
    ]

    params: List[Any] = [
        nd,
        _normalize_text(new_tipo),
        _normalize_text(new_fornitore),
        _normalize_text(new_documento),
        nimp,
    ]

    if new_foto_file is not None and cols.foto_col:
        params.append((new_foto_file or "").strip())

    params += [
        od,
        _normalize_text(orig_tipo),
        _normalize_text(orig_fornitore),
        _normalize_text(orig_documento),
        oimp,
    ]

    if cols.site_col:
        where_parts.append(f"{sql_trim(_qname(cols.site_col))} = ?")
        params.append(_normalize_text(store_code))

    sql = f"UPDATE {_qname('SPESE')} SET {set_sql} WHERE " + " AND ".join(where_parts)

    conn = get_connection(store_code)
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        conn.commit()
        try:
            return int(cur.rowcount)
        except Exception:
            return 0
    finally:
        try:
            conn.close()
        except Exception:
            pass


# -------------------------
# Ricerca (multi-store)
# -------------------------


def search_spese_range_multi(
    store_codes: List[str],
    *,
    start: date,
    end: date,
) -> Dict[str, Any]:
    """Ricerca Spese per intervallo date su più store.

    - In SQL Server: una sola query con filtro site IN (...)
    - In Access: loop store-by-store (DB separati)
    """
    codes = [str(c).strip() for c in (store_codes or []) if str(c).strip()]
    if not codes:
        return {"rows": [], "total": 0.0, "warnings": []}

    # end inclusivo -> end esclusivo
    end_excl = end + timedelta(days=1)

    warnings: List[str] = []
    out_rows: List[Dict[str, Any]] = []
    total = Decimal("0")

    if supports_schema_alter():
        # Access (DB separati per store)
        for code in codes:
            try:
                res = _search_spese_range_one_store(store_code=code, start=start, end_excl=end_excl)
                out_rows.extend(res["rows"])
                total += res["total_dec"]
                warnings.extend(res.get("warnings") or [])
            except Exception as ex:
                warnings.append(f"[{code}] Spese: {ex}")
        return {"rows": out_rows, "total": float(total), "warnings": warnings}

    # SQL Server (DB unico)
    cols = _resolve_spese_columns(codes[0], ensure_schema=False)

    select_parts = [
        f"{sql_trim(_qname(cols.site_col))} AS sp_site" if cols.site_col else "'' AS sp_site",
        f"{sql_date(_qname(cols.date_col))} AS sp_date",
        f"{_qname(cols.tipo_col)} AS sp_tipo",
        f"{_qname(cols.descr_col)} AS sp_descr",
        f"{_qname(cols.doc_col)} AS sp_doc",
        f"{_qname(cols.importo_col)} AS sp_imp",
    ]
    if cols.foto_col:
        select_parts.append(f"{_qname(cols.foto_col)} AS sp_foto")

    where = []
    params: List[Any] = []

    if cols.site_col:
        ph = ",".join(["?"] * len(codes))
        where.append(f"{sql_trim(_qname(cols.site_col))} IN ({ph})")
        params.extend(codes)

    where.append(f"{sql_date(_qname(cols.date_col))} >= ?")
    params.append(start)

    where.append(f"{sql_date(_qname(cols.date_col))} < ?")
    params.append(end_excl)

    sql = f"""
    SELECT {", ".join(select_parts)}
    FROM {_qname("SPESE")}
    WHERE {" AND ".join(where)}
    ORDER BY {sql_date(_qname(cols.date_col))} DESC
    """

    conn = get_connection(None, read_only=True)
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        rows = cur.fetchall() or []

        for r in rows:
            idx = 0
            site_val = str(r[idx] or "").strip(); idx += 1

            dt_val = r[idx]; idx += 1
            if isinstance(dt_val, datetime):
                dt_val = dt_val.date()
            if isinstance(dt_val, date):
                date_display = dt_val.strftime("%d/%m/%Y")
                date_iso = dt_val.isoformat()
            else:
                date_display = str(dt_val or "")
                date_iso = ""

            tipo = str(r[idx] or ""); idx += 1
            forn = str(r[idx] or ""); idx += 1
            doc = str(r[idx] or ""); idx += 1

            imp = r[idx]; idx += 1
            try:
                imp_dec = imp if isinstance(imp, Decimal) else Decimal(str(imp))
            except Exception:
                # prova a gestire importi testuali
                try:
                    imp_dec = _money_to_decimal(str(imp or "0"))
                except Exception:
                    imp_dec = Decimal("0")
            total += imp_dec

            foto_file = None
            if cols.foto_col:
                try:
                    foto_file = (str(r[idx] or "").strip() or None)
                except Exception:
                    foto_file = None

            out_rows.append(
                {
                    "site": site_val,
                    "data": date_display,
                    "data_iso": date_iso,
                    "tipo": tipo,
                    "fornitore": forn,
                    "documento": doc,
                    "importo": float(imp_dec),
                    "importo_key": str(imp_dec),
                    "foto_file": foto_file,
                }
            )

        return {"rows": out_rows, "total": float(total), "warnings": warnings}
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _search_spese_range_one_store(*, store_code: str, start: date, end_excl: date) -> Dict[str, Any]:
    """Ricerca spese su un singolo store (Access o fallback)."""
    cols = _resolve_spese_columns(str(store_code), ensure_schema=False)

    select_parts = [
        f"{sql_date(_qname(cols.date_col))} AS sp_date",
        f"{_qname(cols.tipo_col)} AS sp_tipo",
        f"{_qname(cols.descr_col)} AS sp_descr",
        f"{_qname(cols.doc_col)} AS sp_doc",
        f"{_qname(cols.importo_col)} AS sp_imp",
    ]
    if cols.foto_col:
        select_parts.append(f"{_qname(cols.foto_col)} AS sp_foto")

    sql = f"""
    SELECT {", ".join(select_parts)}
    FROM {_qname("SPESE")}
    WHERE {sql_date(_qname(cols.date_col))} >= ?
      AND {sql_date(_qname(cols.date_col))} < ?
    ORDER BY {sql_date(_qname(cols.date_col))} DESC
    """

    warnings: List[str] = []
    out_rows: List[Dict[str, Any]] = []
    total = Decimal("0")

    conn = get_connection(store_code, read_only=True)
    try:
        cur = conn.cursor()
        cur.execute(sql, [start, end_excl])
        rows = cur.fetchall() or []
        for r in rows:
            idx = 0
            dt_val = r[idx]; idx += 1
            if isinstance(dt_val, datetime):
                dt_val = dt_val.date()
            if isinstance(dt_val, date):
                date_display = dt_val.strftime("%d/%m/%Y")
                date_iso = dt_val.isoformat()
            else:
                date_display = str(dt_val or "")
                date_iso = ""

            tipo = str(r[idx] or ""); idx += 1
            forn = str(r[idx] or ""); idx += 1
            doc = str(r[idx] or ""); idx += 1

            imp = r[idx]; idx += 1
            try:
                imp_dec = imp if isinstance(imp, Decimal) else Decimal(str(imp))
            except Exception:
                try:
                    imp_dec = _money_to_decimal(str(imp or "0"))
                except Exception:
                    imp_dec = Decimal("0")
            total += imp_dec

            foto_file = None
            if cols.foto_col:
                try:
                    foto_file = (str(r[idx] or "").strip() or None)
                except Exception:
                    foto_file = None

            out_rows.append(
                {
                    "site": str(store_code),
                    "data": date_display,
                    "data_iso": date_iso,
                    "tipo": tipo,
                    "fornitore": forn,
                    "documento": doc,
                    "importo": float(imp_dec),
                    "importo_key": str(imp_dec),
                    "foto_file": foto_file,
                }
            )

        return {"rows": out_rows, "total_dec": total, "warnings": warnings}
    finally:
        try:
            conn.close()
        except Exception:
            pass
