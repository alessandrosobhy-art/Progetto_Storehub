# PATCH: rendiconto_ricerca_v1.3.1
from __future__ import annotations

SCRIPT_VERSION = "versamenti_repository_v1.4.5_site_rowuuid_fix"

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from app_db import get_connection, sql_date, sql_trim, supports_schema_alter

REPO_PATCH_VERSION = "rendiconto_ricerca_v1.3.1"


# -------------------------
# Helpers
# -------------------------

def _qname(name: str) -> str:
    return f"[{str(name).replace(']', ']]')}]"


def _parse_date_iso(s: str) -> date:
    s = (s or "").strip()
    return datetime.strptime(s, "%Y-%m-%d").date()


def _money_to_decimal(v: str) -> Decimal:
    # accetta 1.234,56 e 1234.56
    raw = (v or "").strip()
    if raw and ("." in raw) and ("," in raw):
        raw = raw.replace(".", "").replace(",", ".")
    else:
        raw = raw.replace(",", ".")
    return Decimal(raw)


def _normalize_text(v: str) -> str:
    return (v or "").strip()


def _digits_only(v: str) -> str:
    return "".join(ch for ch in (v or "") if ch.isdigit())


def _digits_from_text(v: str) -> str:
    """Estrae solo le cifre da una stringa.

    Serve per ricavare il numero tessera da campi testuali (es. "Mario Rossi 123456").
    """
    return _digits_only(v)


def _get_table_columns_ordered(cur, table: str) -> List[str]:
    cur.execute(f"SELECT * FROM {_qname(table)} WHERE 1=0")
    return [d[0] for d in (cur.description or [])]


def _find_col(cols: List[str], keys: Tuple[str, ...]) -> Optional[str]:
    low = [c.lower() for c in cols]

    # 1) match esatto (case-insensitive)
    for k in keys:
        kl = k.lower().strip()
        if not kl:
            continue
        for i, c in enumerate(low):
            if c == kl:
                return cols[i]

    # 2) match "contiene" (solo per chiavi abbastanza lunghe)
    for k in keys:
        kl = k.lower().strip()
        if not kl or len(kl) < 3:
            continue
        for i, c in enumerate(low):
            if kl in c:
                return cols[i]

    return None




def take_next_last_data_col(cols: List[str]) -> str:
    """Fallback per individuare la colonna importo/valore.

    Se non si riesce a riconoscere la colonna tramite le chiavi, scansiona da destra
    verso sinistra escludendo colonne tecniche/metadati (es. row_uuid, site, foto_file).
    """
    if not cols:
        return "VALORE"

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
class VersamentiColumns:
    date_vers_col: str
    dal_col: str
    al_col: str
    nome_col: str
    tipo_col: str
    riferimento_col: str
    valore_col: str
    tessera_col: Optional[str] = None
    foto_col: Optional[str] = None
    no_receipt_col: Optional[str] = None
    lost_receipt_col: Optional[str] = None
    site_col: Optional[str] = None
    id_col: Optional[str] = None


def _guess_versamenti_columns(columns: List[str]) -> VersamentiColumns:
    cols = columns[:]  # ordered

    # ID (opzionale): preferiamo match esatto per evitare falsi positivi
    id_col = _find_col(cols, ("row_uuid", "rowuuid", "id", "id_versamento", "idversamento", "pk"))

    # Date
    date_vers_col = _find_col(cols, ("data_versamento", "dataversamento", "data vers", "versamento_data", "data"))
    dal_col = _find_col(cols, ("data_dal", "periodo_dal", "dal", "data_da", "da", "inizio"))
    # Per "al" evitiamo substring corto: usiamo chiavi più specifiche + match esatto
    al_col = _find_col(cols, ("data_al", "periodo_al", "al", "data_a", "a", "fine"))

    nome_col = _find_col(cols, ("nome_cognome", "nome e cognome", "operatore", "nome", "cognome", "intestat"))
    tipo_col = _find_col(cols, ("tipo_versamento", "tipo versamento", "tipo", "operatore/tessera"))
    tessera_col = _find_col(cols, ("tessera", "card", "numero_tessera"))
    riferimento_col = _find_col(cols, ("riferimento", "riferimento_versamento", "rif", "causale", "descr"))
    valore_col = _find_col(cols, ("versamento", "valore", "importo", "euro", "ammontare", "tot"))

    foto_col = _find_col(cols, ("foto_file", "foto", "img", "image", "alleg", "attachment"))
    no_receipt_col = _find_col(cols, ("no_receipt", "no_receipt_flag", "sportello_senza_ricevuta", "sportello_no_ricevuta", "senza_ricevuta"))
    lost_receipt_col = _find_col(cols, ("lost_receipt", "lost_receipt_flag", "ricevuta_smarrita"))

    site_col = _find_col(cols, ("site", "store", "negoz", "punto"))

    # fallback: riempi i mancanti prendendo le colonne rimanenti
    used = {c for c in (id_col, date_vers_col, dal_col, al_col, nome_col, tipo_col, tessera_col, riferimento_col, valore_col, foto_col, no_receipt_col, lost_receipt_col, site_col) if c}
    remaining = [c for c in cols if c not in used]

    def take_next(default: str) -> str:
        return remaining.pop(0) if remaining else default

    if not date_vers_col:
        date_vers_col = take_next(cols[0] if cols else "DATA")
    if not dal_col:
        dal_col = take_next(cols[1] if len(cols) > 1 else "DAL")
    if not al_col:
        al_col = take_next(cols[2] if len(cols) > 2 else "AL")
    if not nome_col:
        nome_col = take_next(cols[3] if len(cols) > 3 else "NOME")
    if not tipo_col:
        tipo_col = take_next(cols[4] if len(cols) > 4 else "TIPO")
    if not riferimento_col:
        riferimento_col = take_next(cols[5] if len(cols) > 5 else "RIFERIMENTO")
    if not valore_col:
        valore_col = take_next_last_data_col(cols)

    return VersamentiColumns(
        id_col=id_col,
        date_vers_col=date_vers_col,
        dal_col=dal_col,
        al_col=al_col,
        nome_col=nome_col,
        tipo_col=tipo_col,
        tessera_col=tessera_col,
        riferimento_col=riferimento_col,
        valore_col=valore_col,
        no_receipt_col=no_receipt_col,
        lost_receipt_col=lost_receipt_col,
        foto_col=foto_col,
        site_col=site_col,
    )




def _ensure_versamenti_optional_columns(store_code: str, table_name: str = "VERSAMENTI_APP") -> None:
    """Assicura l'esistenza delle colonne opzionali usate dall'app.

    In caso di errori (permessi, DB bloccato, ecc.) ignora senza bloccare l'app.
    """
    if not supports_schema_alter():
        return

    conn = get_connection(store_code)
    try:
        cur = conn.cursor()
        cols = _get_table_columns_ordered(cur, table_name)
        existing = {str(c).lower() for c in (cols or [])}

        wanted = [
            ("FOTO_FILE", "TEXT(255)"),
            ("NO_RECEIPT_FLAG", "LONG"),
            ("LOST_RECEIPT_FLAG", "LONG"),
        ]
        for col_name, col_type in wanted:
            if col_name.lower() in existing:
                continue
            try:
                cur.execute(f"ALTER TABLE {_qname(table_name)} ADD COLUMN {_qname(col_name)} {col_type}")
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

def _resolve_versamenti_columns(store_code: str, *, ensure_schema: bool = True) -> VersamentiColumns:
    # Di default manteniamo l'auto-fix dello schema (colonna foto).
    # Per operazioni di sola lettura/aggregazione (es. riepiloghi) possiamo
    # disattivarlo per evitare ALTER TABLE + upload su SharePoint.
    if ensure_schema:
        _ensure_versamenti_optional_columns(str(store_code))

    conn = get_connection(store_code, read_only=not ensure_schema)
    try:
        cur = conn.cursor()
        cols = _get_table_columns_ordered(cur, "VERSAMENTI_APP")
        if not cols:
            # fallback hard-coded minimale
            return VersamentiColumns(
                id_col=None,
                date_vers_col="DATA_VERSAMENTO",
                dal_col="DAL",
                al_col="AL",
                nome_col="NOME_COGNOME",
                tipo_col="TIPO",
                tessera_col="TESSERA",
                riferimento_col="RIFERIMENTO",
                valore_col="VALORE",
                site_col=None,
            )
        return _guess_versamenti_columns(cols)
    finally:
        try:
            conn.close()
        except Exception:
            pass


def sum_versamenti_month_total(*, store_code: str, year: int, month: int) -> float:
    """Totale versamenti nel mese (query aggregata).

    Pensata per riepiloghi multi-store: evita di caricare tutte le righe.
    """
    if year < 2000 or not (1 <= month <= 12):
        return 0.0

    cols = _resolve_versamenti_columns(str(store_code), ensure_schema=False)

    start = date(int(year), int(month), 1)
    end = date(int(year) + 1, 1, 1) if int(month) == 12 else date(int(year), int(month) + 1, 1)

    where = [f"{sql_date(_qname(cols.al_col))} >= ?", f"{sql_date(_qname(cols.al_col))} < ?"]
    params: List[Any] = [start, end]
    if cols.site_col:
        where.append(f"{sql_trim(_qname(cols.site_col))} = ?")
        params.append(str(store_code).strip())

    sql = f"SELECT SUM({_qname(cols.valore_col)}) AS s FROM {_qname('VERSAMENTI_APP')} WHERE {' AND '.join(where)}"

    conn = get_connection(str(store_code), read_only=True)
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        row = cur.fetchone()
        s = row[0] if row else None
        try:
            s_dec = s if isinstance(s, Decimal) else Decimal(str(s))
        except Exception:
            s_dec = Decimal("0")
        return float(s_dec)
    finally:
        try:
            conn.close()
        except Exception:
            pass

def sum_versamenti_month_total_multi(store_codes: List[str], *, year: int, month: int) -> Dict[str, float]:
    """Totale versamenti nel mese per più store (solo SQL Server).

    Restituisce un dict {site: totale}.
    In Access non è applicabile (DB separati per store), quindi ritorna {}.
    """
    if supports_schema_alter():
        return {}

    codes = [str(c).strip() for c in (store_codes or []) if str(c).strip()]
    if not codes:
        return {}

    if year < 2000 or not (1 <= int(month) <= 12):
        return {c: 0.0 for c in codes}

    cols = _resolve_versamenti_columns(codes[0], ensure_schema=False)
    if not cols.site_col:
        return {}

    start = date(int(year), int(month), 1)
    end = date(int(year) + 1, 1, 1) if int(month) == 12 else date(int(year), int(month) + 1, 1)

    in_ph = ", ".join(["?"] * len(codes))
    where = [
        f"{sql_date(_qname(cols.al_col))} >= ?",
        f"{sql_date(_qname(cols.al_col))} < ?",
        f"{sql_trim(_qname(cols.site_col))} IN ({in_ph})",
    ]
    params: List[Any] = [start, end]
    params.extend(codes)

    sql = f"""
    SELECT
      {sql_trim(_qname(cols.site_col))} AS v_site,
      SUM({_qname(cols.valore_col)}) AS s
    FROM {_qname('VERSAMENTI_APP')}
    WHERE {' AND '.join(where)}
    GROUP BY {sql_trim(_qname(cols.site_col))}
    """

    out: Dict[str, float] = {c: 0.0 for c in codes}

    conn = get_connection(None, read_only=True)
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        rows = cur.fetchall() or []
        for r in rows:
            site = str(r[0] or "").strip()
            if not site:
                continue
            s = r[1]
            try:
                s_dec = s if isinstance(s, Decimal) else Decimal(str(s))
            except Exception:
                s_dec = Decimal("0")
            out[site] = float(s_dec)
    finally:
        try:
            conn.close()
        except Exception:
            pass

    return out



# -------------------------
# Public API
# -------------------------

def insert_versamento(
    *,
    store_code: str,
    data_versamento_iso: str,
    periodo_dal_iso: str,
    periodo_al_iso: str,
    nome_cognome: str,
    tipo_versamento: str,
    tessera: str,
    riferimento: str,
    valore_euro: str,
    foto_file: Optional[str] = None,
    no_receipt_flag: bool = False,
    lost_receipt_flag: bool = False,
) -> None:
    cols = _resolve_versamenti_columns(str(store_code))

    d_vers = _parse_date_iso(data_versamento_iso)
    d_dal = _parse_date_iso(periodo_dal_iso)
    d_al = _parse_date_iso(periodo_al_iso)

    val = float(_money_to_decimal(valore_euro))

    tipo_norm = _normalize_text(tipo_versamento)
    tess = _digits_only(tessera)[:16]
    if tipo_norm.strip().lower().startswith("oper"):
        tess = "0"
    if not tess:
        tess = "0"

    values: List[Any] = [
        d_vers,
        d_dal,
        d_al,
        _normalize_text(nome_cognome),
        tipo_norm,
        _normalize_text(riferimento),
        val,
    ]

    col_names: List[str] = [
        cols.date_vers_col,
        cols.dal_col,
        cols.al_col,
        cols.nome_col,
        cols.tipo_col,
        cols.riferimento_col,
        cols.valore_col,
    ]

    if cols.tessera_col:
        # inseriamo tessera subito dopo tipo (solo per leggibilità)
        # ma mantenendo ordine coerente tra col_names e values
        # -> inseriamo in posizione 5 (dopo tipo) prima di riferimento
        insert_at = 5
        col_names.insert(insert_at, cols.tessera_col)
        values.insert(insert_at, tess)

    if cols.site_col and cols.site_col not in col_names:
        col_names.append(cols.site_col)
        values.append(str(store_code).strip())

    if cols.foto_col and cols.foto_col not in col_names:
        col_names.append(cols.foto_col)
        values.append((foto_file or "").strip())
    if cols.no_receipt_col and cols.no_receipt_col not in col_names:
        col_names.append(cols.no_receipt_col)
        values.append(1 if bool(no_receipt_flag) else 0)
    if cols.lost_receipt_col and cols.lost_receipt_col not in col_names:
        col_names.append(cols.lost_receipt_col)
        values.append(1 if bool(lost_receipt_flag) else 0)

    placeholders = ",".join(["?"] * len(values))
    sql = f"INSERT INTO {_qname('VERSAMENTI_APP')} ({','.join(_qname(c) for c in col_names)}) VALUES ({placeholders})"

    conn = get_connection(store_code)
    try:
        cur = conn.cursor()

        # Blocco anti-duplicati: stesso periodo di competenza (Dal/Al) non deve comparire più volte.
        # Questo protegge anche da doppi-click / richieste ripetute.
        try:
            sql_check = (
                f"SELECT COUNT(*) AS n FROM {_qname('VERSAMENTI_APP')} "
                f"WHERE {sql_date(_qname(cols.dal_col))} = ? AND {sql_date(_qname(cols.al_col))} = ?"
            )
            params_check = [d_dal, d_al]
            if cols.site_col:
                sql_check = sql_check + f" AND {sql_trim(_qname(cols.site_col))} = ?"
                params_check.append(str(store_code))
            cur.execute(sql_check, params_check)
            row = cur.fetchone()
            n = int(row[0]) if row and row[0] is not None else 0
            if n > 0:
                raise RuntimeError("Esiste già un versamento per il periodo selezionato.")
        except RuntimeError:
            raise
        except Exception:
            # Se per qualche motivo il check fallisce, continuiamo comunque:
            # il blocco overlap lato endpoint dovrebbe prevenire i duplicati.
            pass

        cur.execute(sql, values)
        conn.commit()
    finally:
        try:
            conn.close()
        except Exception:
            pass


def list_versamenti_month(*, store_code: str, year: int, month: int) -> Dict[str, Any]:
    cols = _resolve_versamenti_columns(str(store_code))

    start = date(int(year), int(month), 1)
    end = date(int(year) + 1, 1, 1) if int(month) == 12 else date(int(year), int(month) + 1, 1)

    select_cols = [
        f"{sql_date(_qname(cols.date_vers_col))} AS dv",
        f"{sql_date(_qname(cols.dal_col))} AS dd",
        f"{sql_date(_qname(cols.al_col))} AS da",
        f"{_qname(cols.nome_col)} AS nm",
        f"{_qname(cols.tipo_col)} AS tp",
        f"{_qname(cols.riferimento_col)} AS rf",
        f"{_qname(cols.valore_col)} AS vl",
    ]
    if cols.foto_col:
        select_cols.append(f"{_qname(cols.foto_col)} AS ff")
    if cols.no_receipt_col:
        select_cols.append(f"{_qname(cols.no_receipt_col)} AS nr")
    if cols.lost_receipt_col:
        select_cols.append(f"{_qname(cols.lost_receipt_col)} AS lr")
    if cols.tessera_col:
        select_cols.insert(5, f"{_qname(cols.tessera_col)} AS ts")
    if cols.id_col:
        select_cols.insert(0, f"{_qname(cols.id_col)} AS id")

    where = [f"{sql_date(_qname(cols.al_col))} >= ?", f"{sql_date(_qname(cols.al_col))} < ?"]
    params: List[Any] = [start, end]

    if cols.site_col:
        where.append(f"{sql_trim(_qname(cols.site_col))} = ?")
        params.append(str(store_code).strip())

    sql = (
        f"SELECT {', '.join(select_cols)} "
        f"FROM {_qname('VERSAMENTI_APP')} "
        f"WHERE {' AND '.join(where)} "
        f"ORDER BY {sql_date(_qname(cols.al_col))} DESC, {sql_date(_qname(cols.dal_col))} DESC, {sql_date(_qname(cols.date_vers_col))} DESC"
    )

    conn = get_connection(store_code)
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        rows = cur.fetchall() or []
        desc = [str((d[0] or "")).strip().lower() for d in (cur.description or [])]
        pos = {name: i for i, name in enumerate(desc)}

        out_rows: List[Dict[str, Any]] = []
        total = Decimal("0")

        def _cell(row, alias: str):
            i = pos.get(alias.lower())
            if i is None:
                return None
            try:
                return row[i]
            except Exception:
                return None

        for r in rows:
            rid = _cell(r, "id") if cols.id_col else None
            dv = _cell(r, "dv")
            dd = _cell(r, "dd")
            da = _cell(r, "da")
            nm = _cell(r, "nm")
            tp = _cell(r, "tp")
            ts_val = _cell(r, "ts") if cols.tessera_col else None
            rf = _cell(r, "rf")
            vl = _cell(r, "vl")
            ff = _cell(r, "ff") if cols.foto_col else None
            nr = _cell(r, "nr") if cols.no_receipt_col else 0
            lr = _cell(r, "lr") if cols.lost_receipt_col else 0

            def _to_iso_and_disp(dt_val):
                if isinstance(dt_val, datetime):
                    dt_val = dt_val.date()
                if isinstance(dt_val, date):
                    return dt_val.isoformat(), dt_val.strftime("%d/%m/%Y")
                s = str(dt_val or "").strip()
                return s, s

            dv_iso, dv_disp = _to_iso_and_disp(dv)
            dd_iso, dd_disp = _to_iso_and_disp(dd)
            da_iso, da_disp = _to_iso_and_disp(da)

            try:
                vl_dec = vl if isinstance(vl, Decimal) else Decimal(str(vl))
            except Exception:
                vl_dec = Decimal("0")

            total += vl_dec

            tessera_digits = _digits_only(str(ts_val or ""))[:16] if ts_val is not None else "0"
            if not tessera_digits:
                tessera_digits = "0"

            out_rows.append(
                {
                    "id": str(rid) if rid is not None else "",
                    "data_versamento": dv_disp,
                    "data_versamento_iso": dv_iso,
                    "dal": dd_disp,
                    "dal_iso": dd_iso,
                    "al": da_disp,
                    "al_iso": da_iso,
                    "nome": str(nm or ""),
                    "nome_raw": str(nm or ""),
                    "tipo": str(tp or ""),
                    "tipo_raw": str(tp or ""),
                    "tessera": tessera_digits,
                    "tessera_raw": tessera_digits,
                    "riferimento": str(rf or ""),
                    "riferimento_raw": str(rf or ""),
                    "valore": float(vl_dec),
                    "valore_key": str(vl_dec),
                    "foto_file": (str(ff or "").strip() or None) if cols.foto_col else None,
                    "no_receipt_flag": bool(int(nr or 0)) if cols.no_receipt_col else False,
                    "lost_receipt_flag": bool(int(lr or 0)) if cols.lost_receipt_col else False,
                }
            )

        return {"rows": out_rows, "total": float(total), "has_id": bool(cols.id_col)}
    finally:
        try:
            conn.close()
        except Exception:
            pass


def get_versamento_photo_file(
    *,
    store_code: str,
    record_id: str,
    orig_data_vers_iso: str,
    orig_dal_iso: str,
    orig_al_iso: str,
    orig_nome: str,
    orig_tipo: str,
    orig_tessera: str,
    orig_riferimento: str,
    orig_valore_key: str,
) -> Optional[str]:
    """Ritorna il filename foto associato al record, se presente."""
    cols = _resolve_versamenti_columns(str(store_code))
    if not cols.foto_col:
        return None

    conn = get_connection(store_code)
    try:
        cur = conn.cursor()

        if cols.id_col and record_id:
            sql = f"SELECT TOP 1 {_qname(cols.foto_col)} FROM {_qname('VERSAMENTI_APP')} WHERE {_qname(cols.id_col)} = ?"
            cur.execute(sql, [record_id])
            row = cur.fetchone()
            if not row:
                return None
            return str(row[0] or '').strip() or None

        dv = _parse_date_iso(orig_data_vers_iso)
        dd = _parse_date_iso(orig_dal_iso)
        da = _parse_date_iso(orig_al_iso)
        try:
            val = float(Decimal(orig_valore_key.replace(",", ".")))
        except Exception:
            val = 0.0

        where = [
            f"{sql_date(_qname(cols.date_vers_col))} = ?",
            f"{sql_date(_qname(cols.dal_col))} = ?",
            f"{sql_date(_qname(cols.al_col))} = ?",
            f"{sql_trim(_qname(cols.nome_col))} = ?",
            f"{sql_trim(_qname(cols.tipo_col))} = ?",
            f"{sql_trim(_qname(cols.riferimento_col))} = ?",
            f"{_qname(cols.valore_col)} = ?",
        ]
        params: List[Any] = [dv, dd, da, _normalize_text(orig_nome), _normalize_text(orig_tipo), _normalize_text(orig_riferimento), val]

        if cols.tessera_col:
            where.insert(5, f"{sql_trim(_qname(cols.tessera_col))} = ?")
            params.insert(5, _digits_only(orig_tessera)[:16] or "0")

        if cols.site_col:
            where.append(f"{sql_trim(_qname(cols.site_col))} = ?")
            params.append(str(store_code).strip())

        sql = f"SELECT TOP 1 {_qname(cols.foto_col)} FROM {_qname('VERSAMENTI_APP')} WHERE {' AND '.join(where)}"
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


def delete_versamento(
    *,
    store_code: str,
    record_id: str,
    orig_data_vers_iso: str,
    orig_dal_iso: str,
    orig_al_iso: str,
    orig_nome: str,
    orig_tipo: str,
    orig_tessera: str,
    orig_riferimento: str,
    orig_valore_key: str,
) -> int:
    cols = _resolve_versamenti_columns(str(store_code))

    conn = get_connection(store_code)
    try:
        cur = conn.cursor()

        if cols.id_col and record_id:
            sql = f"DELETE FROM {_qname('VERSAMENTI_APP')} WHERE {_qname(cols.id_col)} = ?"
            cur.execute(sql, [record_id])
            conn.commit()
            return int(cur.rowcount or 0)

        # fallback: chiave composta
        dv = _parse_date_iso(orig_data_vers_iso)
        dd = _parse_date_iso(orig_dal_iso)
        da = _parse_date_iso(orig_al_iso)
        try:
            val = float(Decimal(orig_valore_key.replace(",", ".")))
        except Exception:
            val = 0.0

        where = [
            f"{sql_date(_qname(cols.date_vers_col))} = ?",
            f"{sql_date(_qname(cols.dal_col))} = ?",
            f"{sql_date(_qname(cols.al_col))} = ?",
            f"{sql_trim(_qname(cols.nome_col))} = ?",
            f"{sql_trim(_qname(cols.tipo_col))} = ?",
            f"{sql_trim(_qname(cols.riferimento_col))} = ?",
            f"{_qname(cols.valore_col)} = ?",
        ]
        params: List[Any] = [dv, dd, da, _normalize_text(orig_nome), _normalize_text(orig_tipo), _normalize_text(orig_riferimento), val]

        if cols.tessera_col:
            where.insert(5, f"{sql_trim(_qname(cols.tessera_col))} = ?")
            params.insert(5, _digits_only(orig_tessera)[:16] or "0")

        if cols.site_col:
            where.append(f"{sql_trim(_qname(cols.site_col))} = ?")
            params.append(str(store_code).strip())

        sql = f"DELETE FROM {_qname('VERSAMENTI_APP')} WHERE {' AND '.join(where)}"
        cur.execute(sql, params)
        conn.commit()
        return int(cur.rowcount or 0)
    finally:
        try:
            conn.close()
        except Exception:
            pass


def update_versamento(
    *,
    store_code: str,
    record_id: str,
    orig_data_vers_iso: str,
    orig_dal_iso: str,
    orig_al_iso: str,
    orig_nome: str,
    orig_tipo: str,
    orig_tessera: str,
    orig_riferimento: str,
    orig_valore_key: str,
    new_data_vers_iso: str,
    new_dal_iso: str,
    new_al_iso: str,
    new_nome: str,
    new_tipo: str,
    new_tessera: str,
    new_riferimento: str,
    new_valore_euro: str,
    new_foto_file: Optional[str] = None,
    new_no_receipt_flag: Optional[bool] = None,
    new_lost_receipt_flag: Optional[bool] = None,
) -> int:
    cols = _resolve_versamenti_columns(str(store_code))

    ndv = _parse_date_iso(new_data_vers_iso)
    ndd = _parse_date_iso(new_dal_iso)
    nda = _parse_date_iso(new_al_iso)

    new_val = float(_money_to_decimal(new_valore_euro))
    new_tipo_norm = _normalize_text(new_tipo)
    new_tess = _digits_only(new_tessera)[:16]
    if new_tipo_norm.strip().lower().startswith("oper"):
        new_tess = "0"
    if not new_tess:
        new_tess = "0"

    set_parts = [
        f"{_qname(cols.date_vers_col)} = ?",
        f"{_qname(cols.dal_col)} = ?",
        f"{_qname(cols.al_col)} = ?",
        f"{_qname(cols.nome_col)} = ?",
        f"{_qname(cols.tipo_col)} = ?",
        f"{_qname(cols.riferimento_col)} = ?",
        f"{_qname(cols.valore_col)} = ?",
    ]
    set_params: List[Any] = [ndv, ndd, nda, _normalize_text(new_nome), new_tipo_norm, _normalize_text(new_riferimento), new_val]

    if cols.tessera_col:
        # dopo tipo
        insert_at = 5
        set_parts.insert(insert_at, f"{_qname(cols.tessera_col)} = ?")
        set_params.insert(insert_at, new_tess)


    if new_foto_file is not None and cols.foto_col:
        set_parts.append(f"{_qname(cols.foto_col)} = ?")
        set_params.append((new_foto_file or "").strip())
    if new_no_receipt_flag is not None and cols.no_receipt_col:
        set_parts.append(f"{_qname(cols.no_receipt_col)} = ?")
        set_params.append(1 if bool(new_no_receipt_flag) else 0)
    if new_lost_receipt_flag is not None and cols.lost_receipt_col:
        set_parts.append(f"{_qname(cols.lost_receipt_col)} = ?")
        set_params.append(1 if bool(new_lost_receipt_flag) else 0)

    conn = get_connection(store_code)
    try:
        cur = conn.cursor()

        if cols.id_col and record_id:
            sql = f"UPDATE {_qname('VERSAMENTI_APP')} SET {', '.join(set_parts)} WHERE {_qname(cols.id_col)} = ?"
            cur.execute(sql, set_params + [record_id])
            conn.commit()
            return int(cur.rowcount or 0)

        # fallback: chiave composta
        odv = _parse_date_iso(orig_data_vers_iso)
        odd = _parse_date_iso(orig_dal_iso)
        oda = _parse_date_iso(orig_al_iso)
        try:
            oval = float(Decimal(orig_valore_key.replace(",", ".")))
        except Exception:
            oval = 0.0

        where = [
            f"{sql_date(_qname(cols.date_vers_col))} = ?",
            f"{sql_date(_qname(cols.dal_col))} = ?",
            f"{sql_date(_qname(cols.al_col))} = ?",
            f"{sql_trim(_qname(cols.nome_col))} = ?",
            f"{sql_trim(_qname(cols.tipo_col))} = ?",
            f"{sql_trim(_qname(cols.riferimento_col))} = ?",
            f"{_qname(cols.valore_col)} = ?",
        ]
        where_params: List[Any] = [odv, odd, oda, _normalize_text(orig_nome), _normalize_text(orig_tipo), _normalize_text(orig_riferimento), oval]

        if cols.tessera_col:
            where.insert(5, f"{sql_trim(_qname(cols.tessera_col))} = ?")
            where_params.insert(5, _digits_only(orig_tessera)[:16] or "0")

        if cols.site_col:
            where.append(f"{sql_trim(_qname(cols.site_col))} = ?")
            where_params.append(str(store_code).strip())

        sql = f"UPDATE {_qname('VERSAMENTI_APP')} SET {', '.join(set_parts)} WHERE {' AND '.join(where)}"
        cur.execute(sql, set_params + where_params)
        conn.commit()
        return int(cur.rowcount or 0)
    finally:
        try:
            conn.close()
        except Exception:
            pass


# -------------------------
# Periodi competenza (lock distinte)
# -------------------------


def list_versamenti_periods_overlapping(
    *,
    store_code: str,
    start_iso: str,
    end_iso: str,
) -> Dict[str, Any]:
    """Ritorna i versamenti con periodo competenza che si sovrappone al range [start, end].

    Serve per bloccare/modificare le distinte di cassa su giornate già incluse in un versamento.

    Output:
      {
        "rows": [
          {"id": "..."|"", "dal_iso": "YYYY-MM-DD", "al_iso": "YYYY-MM-DD", ...},
          ...
        ],
        "has_id": bool
      }
    """
    cols = _resolve_versamenti_columns(str(store_code))

    d_start = _parse_date_iso(start_iso)
    d_end = _parse_date_iso(end_iso)
    if d_end < d_start:
        d_start, d_end = d_end, d_start

    select_cols: List[str] = [
        f"{sql_date(_qname(cols.dal_col))} AS dd",
        f"{sql_date(_qname(cols.al_col))} AS da",
        f"{sql_date(_qname(cols.date_vers_col))} AS dv",
        f"{_qname(cols.nome_col)} AS nm",
        f"{_qname(cols.tipo_col)} AS tp",
        f"{_qname(cols.riferimento_col)} AS rf",
        f"{_qname(cols.valore_col)} AS vl",
    ]

    if cols.tessera_col:
        select_cols.insert(5, f"{_qname(cols.tessera_col)} AS ts")
    if cols.id_col:
        select_cols.insert(0, f"{_qname(cols.id_col)} AS id")

    where = [
        f"{sql_date(_qname(cols.dal_col))} <= ?",
        f"{sql_date(_qname(cols.al_col))} >= ?",
    ]
    params: List[Any] = [d_end, d_start]

    if cols.site_col:
        where.append(f"{sql_trim(_qname(cols.site_col))} = ?")
        params.append(str(store_code).strip())

    sql = f"SELECT {', '.join(select_cols)} FROM {_qname('VERSAMENTI_APP')} WHERE {' AND '.join(where)}"

    conn = get_connection(store_code)
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        rows = cur.fetchall() or []

        out: List[Dict[str, Any]] = []

        for r in rows:
            idx = 0
            rid = ""
            if cols.id_col:
                rid = str(r[idx]) if r[idx] is not None else ""
                idx += 1

            dd = r[idx]; idx += 1
            da = r[idx]; idx += 1
            dv = r[idx]; idx += 1
            nm = r[idx]; idx += 1
            tp = r[idx]; idx += 1

            ts_val = None
            if cols.tessera_col:
                ts_val = r[idx]
                idx += 1

            rf = r[idx]; idx += 1
            vl = r[idx]; idx += 1

            def _to_iso(dt_val):
                if isinstance(dt_val, datetime):
                    dt_val = dt_val.date()
                if isinstance(dt_val, date):
                    return dt_val.isoformat()
                return str(dt_val or '').strip()

            dd_iso = _to_iso(dd)
            da_iso = _to_iso(da)
            dv_iso = _to_iso(dv)

            try:
                vl_dec = vl if isinstance(vl, Decimal) else Decimal(str(vl))
            except Exception:
                vl_dec = Decimal("0")

            tessera_digits = _digits_only(str(ts_val or ""))[:16] if cols.tessera_col else "0"
            if not tessera_digits:
                tessera_digits = "0"

            out.append(
                {
                    "id": rid,
                    "dal_iso": dd_iso,
                    "al_iso": da_iso,
                    "data_versamento_iso": dv_iso,
                    "nome_raw": str(nm or ""),
                    "tipo_raw": str(tp or ""),
                    "tessera_raw": tessera_digits,
                    "riferimento_raw": str(rf or ""),
                    "valore_key": str(vl_dec),
                }
            )

        return {"rows": out, "has_id": bool(cols.id_col)}
    finally:
        try:
            conn.close()
        except Exception:
            pass


# -------------------------
# Ricerca (multi-store)
# -------------------------


def search_versamenti_range_multi(
    store_codes: List[str],
    *,
    start: date,
    end: date,
) -> Dict[str, Any]:
    """Ricerca Versamenti per intervallo date su più store.

    - In SQL Server: una sola query con filtro site IN (...)
    - In Access: loop store-by-store (DB separati)
    """
    codes = [str(c).strip() for c in (store_codes or []) if str(c).strip()]
    if not codes:
        return {"rows": [], "total": 0.0, "warnings": []}

    end_excl = end + timedelta(days=1)

    warnings: List[str] = []
    out_rows: List[Dict[str, Any]] = []
    total = Decimal("0")

    if supports_schema_alter():
        for code in codes:
            try:
                res = _search_versamenti_range_one_store(store_code=code, start=start, end_excl=end_excl)
                out_rows.extend(res["rows"])
                total += res["total_dec"]
                warnings.extend(res.get("warnings") or [])
            except Exception as ex:
                warnings.append(f"[{code}] Versamenti: {ex}")
        return {"rows": out_rows, "total": float(total), "warnings": warnings}

    cols = _resolve_versamenti_columns(codes[0], ensure_schema=False)

    # site_col è fondamentale in SQL multi-store
    select_cols = []
    if cols.site_col:
        select_cols.append(f"{sql_trim(_qname(cols.site_col))} AS v_site")
    else:
        select_cols.append("'' AS v_site")

    if cols.id_col:
        select_cols.append(f"{_qname(cols.id_col)} AS rid")

    select_cols.extend(
        [
            f"{sql_date(_qname(cols.date_vers_col))} AS dv",
            f"{sql_date(_qname(cols.dal_col))} AS dd",
            f"{sql_date(_qname(cols.al_col))} AS da",
            f"{_qname(cols.nome_col)} AS nm",
            f"{_qname(cols.tipo_col)} AS tp",
            f"{_qname(cols.riferimento_col)} AS rf",
            f"{_qname(cols.valore_col)} AS vl",
        ]
    )
    if cols.tessera_col:
        select_cols.insert(len(select_cols) - 2, f"{_qname(cols.tessera_col)} AS ts")
    if cols.foto_col:
        select_cols.append(f"{_qname(cols.foto_col)} AS ff")

    where = []
    params: List[Any] = []

    if cols.site_col:
        ph = ",".join(["?"] * len(codes))
        where.append(f"{sql_trim(_qname(cols.site_col))} IN ({ph})")
        params.extend(codes)

    where.append(f"{sql_date(_qname(cols.date_vers_col))} >= ?")
    params.append(start)
    where.append(f"{sql_date(_qname(cols.date_vers_col))} < ?")
    params.append(end_excl)

    sql = f"""
    SELECT {", ".join(select_cols)}
    FROM {_qname("VERSAMENTI_APP")}
    WHERE {" AND ".join(where)}
    ORDER BY {sql_date(_qname(cols.date_vers_col))} DESC
    """

    conn = get_connection(None, read_only=True)
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        rows = cur.fetchall() or []
        desc = [str((d[0] or "")).strip().lower() for d in (cur.description or [])]
        pos = {name: i for i, name in enumerate(desc)}

        def _cell(row, alias: str):
            i = pos.get(alias.lower())
            if i is None:
                return None
            try:
                return row[i]
            except Exception:
                return None

        for r in rows:
            site_val = str(_cell(r, "v_site") or "").strip()
            rid = _cell(r, "rid") if cols.id_col else None
            dv = _cell(r, "dv")
            dd = _cell(r, "dd")
            da = _cell(r, "da")
            nm = _cell(r, "nm")
            tp = _cell(r, "tp")
            ts = _cell(r, "ts") if cols.tessera_col else None
            rf = _cell(r, "rf")
            vl = _cell(r, "vl")
            ff = _cell(r, "ff") if cols.foto_col else None

            # Date formatting
            dv_disp, dv_iso = _fmt_date(dv)
            dd_disp, dd_iso = _fmt_date(dd)
            da_disp, da_iso = _fmt_date(da)

            tessera_digits = _digits_only(str(ts or ""))[:16] if cols.tessera_col else "0"
            if not tessera_digits:
                tessera_digits = "0"
            try:
                vl_dec = vl if isinstance(vl, Decimal) else Decimal(str(vl))
            except Exception:
                try:
                    vl_dec = _money_to_decimal(str(vl or "0"))
                except Exception:
                    vl_dec = Decimal("0")

            total += vl_dec

            out_rows.append(
                {
                    "site": site_val,
                    "id": str(rid) if rid is not None else "",
                    "data_versamento": dv_disp,
                    "data_versamento_iso": dv_iso,
                    "dal": dd_disp,
                    "dal_iso": dd_iso,
                    "al": da_disp,
                    "al_iso": da_iso,
                    "nome": str(nm or ""),
                    "tipo": str(tp or ""),
                    "tessera": tessera_digits,
                    "riferimento": str(rf or ""),
                    "valore": float(vl_dec),
                    "valore_key": str(vl_dec),
                    "foto_file": (str(ff or "").strip() or None) if cols.foto_col else None,
                }
            )

        return {"rows": out_rows, "total": float(total), "warnings": warnings}
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _search_versamenti_range_one_store(*, store_code: str, start: date, end_excl: date) -> Dict[str, Any]:
    """Ricerca versamenti su singolo store (Access o fallback)."""
    cols = _resolve_versamenti_columns(str(store_code), ensure_schema=False)

    select_cols = []
    if cols.id_col:
        select_cols.append(f"{_qname(cols.id_col)} AS rid")

    select_cols.extend(
        [
            f"{sql_date(_qname(cols.date_vers_col))} AS dv",
            f"{sql_date(_qname(cols.dal_col))} AS dd",
            f"{sql_date(_qname(cols.al_col))} AS da",
            f"{_qname(cols.nome_col)} AS nm",
            f"{_qname(cols.tipo_col)} AS tp",
            f"{_qname(cols.riferimento_col)} AS rf",
            f"{_qname(cols.valore_col)} AS vl",
        ]
    )
    if cols.tessera_col:
        select_cols.insert(len(select_cols) - 2, f"{_qname(cols.tessera_col)} AS ts")
    if cols.foto_col:
        select_cols.append(f"{_qname(cols.foto_col)} AS ff")

    sql = f"""
    SELECT {", ".join(select_cols)}
    FROM {_qname("VERSAMENTI_APP")}
    WHERE {sql_date(_qname(cols.date_vers_col))} >= ?
      AND {sql_date(_qname(cols.date_vers_col))} < ?
    ORDER BY {sql_date(_qname(cols.date_vers_col))} DESC
    """

    warnings: List[str] = []
    out_rows: List[Dict[str, Any]] = []
    total = Decimal("0")

    conn = get_connection(store_code, read_only=True)
    try:
        cur = conn.cursor()
        cur.execute(sql, [start, end_excl])
        rows = cur.fetchall() or []
        desc = [str((d[0] or "")).strip().lower() for d in (cur.description or [])]
        pos = {name: i for i, name in enumerate(desc)}

        def _cell(row, alias: str):
            i = pos.get(alias.lower())
            if i is None:
                return None
            try:
                return row[i]
            except Exception:
                return None

        for r in rows:
            rid = _cell(r, "rid") if cols.id_col else None
            dv = _cell(r, "dv")
            dd = _cell(r, "dd")
            da = _cell(r, "da")
            nm = _cell(r, "nm")
            tp = _cell(r, "tp")
            ts = _cell(r, "ts") if cols.tessera_col else None
            rf = _cell(r, "rf")
            vl = _cell(r, "vl")
            ff = _cell(r, "ff") if cols.foto_col else None

            dv_disp, dv_iso = _fmt_date(dv)
            dd_disp, dd_iso = _fmt_date(dd)
            da_disp, da_iso = _fmt_date(da)

            tessera_digits = _digits_only(str(ts or ""))[:16] if cols.tessera_col else "0"
            if not tessera_digits:
                tessera_digits = "0"
            try:
                vl_dec = vl if isinstance(vl, Decimal) else Decimal(str(vl))
            except Exception:
                try:
                    vl_dec = _money_to_decimal(str(vl or "0"))
                except Exception:
                    vl_dec = Decimal("0")
            total += vl_dec

            out_rows.append(
                {
                    "site": str(store_code),
                    "id": str(rid) if rid is not None else "",
                    "data_versamento": dv_disp,
                    "data_versamento_iso": dv_iso,
                    "dal": dd_disp,
                    "dal_iso": dd_iso,
                    "al": da_disp,
                    "al_iso": da_iso,
                    "nome": str(nm or ""),
                    "tipo": str(tp or ""),
                    "tessera": tessera_digits,
                    "riferimento": str(rf or ""),
                    "valore": float(vl_dec),
                    "valore_key": str(vl_dec),
                    "foto_file": (str(ff or "").strip() or None) if cols.foto_col else None,
                }
            )

        return {"rows": out_rows, "total_dec": total, "warnings": warnings}
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _fmt_date(v) -> tuple[str, str]:
    """Ritorna (dd/mm/YYYY, YYYY-MM-DD)"""
    if isinstance(v, datetime):
        v = v.date()
    if isinstance(v, date):
        return v.strftime("%d/%m/%Y"), v.isoformat()
    if v is None:
        return "", ""
    s = str(v).strip()
    # se arriva già iso-like
    try:
        dt = datetime.strptime(s[:10], "%Y-%m-%d").date()
        return dt.strftime("%d/%m/%Y"), dt.isoformat()
    except Exception:
        return s, ""
