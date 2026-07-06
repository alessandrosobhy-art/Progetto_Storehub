from __future__ import annotations

from app_logging import log_swallowed
import re

from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app_db import get_backend, get_connection, sql_date, sql_cast_str, supports_schema_alter


def _qname(name: str) -> str:
    return f"[{str(name).replace(']', ']]')}]"


def _norm(s: str) -> str:
    s = (s or "").strip().lower()
    out: List[str] = []
    for ch in s:
        if ch.isalnum():
            out.append(ch)
    return "".join(out)


def _access_has_table(cur, table_name: str) -> bool:
    t = str(table_name).strip().lower()
    for row in cur.tables(tableType="TABLE"):
        n = getattr(row, "table_name", None)
        if not n:
            try:
                n = row[2]
            except Exception:
                n = None
        if n and str(n).strip().lower() == t:
            return True
    return False


def _is_sqlserver() -> bool:
    return get_backend() == "sqlserver"


def _access_columns(cur, table_name: str) -> List[str]:
    cols: List[str] = []
    try:
        for row in cur.columns(table=table_name):
            n = getattr(row, "column_name", None)
            if not n:
                try:
                    n = row[3]
                except Exception:
                    n = None
            if n:
                cols.append(str(n))
    except Exception:
        cols = []
    return cols



def _access_column_types(cur, table_name: str) -> Dict[str, str]:
    """Return a map {normalized_column_name: TYPE_NAME} for an Access table."""
    out: Dict[str, str] = {}
    try:
        for row in cur.columns(table=table_name):
            n = getattr(row, "column_name", None)
            if not n:
                try:
                    n = row[3]
                except Exception:
                    n = None
            t = getattr(row, "type_name", None)
            if not t:
                try:
                    t = row[5]
                except Exception:
                    t = None
            if n:
                out[_norm(str(n))] = str(t or "").upper()
    except Exception:
        log_swallowed('orari_repository:81')
    return out


def _is_text_type(type_name: str) -> bool:
    t = (type_name or "").upper()
    return any(x in t for x in ("CHAR", "TEXT", "VARCHAR", "LONGCHAR", "MEMO"))


def _coerce_site_param(store_code: str, type_name: str) -> Any:
    if _is_text_type(type_name):
        return str(store_code)
    # Numeric / integer-like
    s = str(store_code or "").strip()
    try:
        if s.lower().startswith("0") and len(s) > 1:
            # keep leading zeros only if text; otherwise numeric cast is ok
            return int(s)
        return int(s)
    except Exception:
        try:
            return float(s)
        except Exception:
            return s


def _coerce_date_param(d: date, type_name: str) -> Any:
    if _is_text_type(type_name):
        return d.isoformat()
    return _to_access_datetime(d)

def _pick_column(cols: List[str], candidates: List[str]) -> Optional[str]:
    if not cols:
        return None
    ncols = [(c, _norm(c)) for c in cols]

    cand_norm = [_norm(c) for c in candidates if c]
    for cn in cand_norm:
        for c, nc in ncols:
            if nc == cn:
                return c

    for cn in cand_norm:
        for c, nc in ncols:
            if cn and (cn in nc or nc in cn):
                return c

    return None


def _candidate_sets() -> Dict[str, List[str]]:
    return {
        "site": ["Site", "SITE", "Sito", "Store", "CodiceStore"],
        "data": ["Data", "DATA", "Giorno", "Date", "DataTurno"],
        "nominativo": ["Nominativo", "NomeCognome", "Nome e Cognome", "Dipendente", "Staff"],
        "staff_id": ["StaffId", "Staff_ID", "staff_id", "IdStaff", "IDStaff", "DipendenteId", "EmployeeId"],
        "causale": ["Causale", "Causa", "Motivo"],
        "causale2": ["Causale2", "Causale_2", "Causa2", "Motivo2", "Causale 2"],
        "inizio_1": ["Inizio_1", "Inizio1", "Entrata_1", "Start1", "Inizio 1"],
        "fine_1": ["Fine_1", "Fine1", "Uscita_1", "End1", "Fine 1"],
        "inizio_2": ["Inizio_2", "Inizio2", "Entrata_2", "Start2", "Inizio 2"],
        "fine_2": ["Fine_2", "Fine2", "Uscita_2", "End2", "Fine 2"],
        "s_prestito": ["S_Prestito", "S_prestito", "S_presito", "SPrestito", "Prestito", "StorePrestito", "S Prestito", "S Presito"],
        "s_prestito2": ["S_Prestito2", "S_prestito2", "S_presito2", "SPrestito2", "Prestito2", "StorePrestito2", "S Prestito2"],
        "inquadramento": ["Inquadramento", "InquadramentoStaff", "Ruolo", "Role"],
        "colore": ["Colore", "Color", "ColoreGiorno", "ColorCode"],
    }


def _ensure_turni_table(conn) -> str:
    cur = conn.cursor()

    if _access_has_table(cur, "STAFF_P"):
        table = "STAFF_P"
    elif _access_has_table(cur, "STAFF_TURNI"):
        table = "STAFF_TURNI"
    else:
        if _is_sqlserver():
            cur.execute(
                """
                CREATE TABLE STAFF_P (
                    id INT IDENTITY(1,1) NOT NULL PRIMARY KEY,
                    Site NVARCHAR(20) NULL,
                    Data DATETIME NULL,
                    Nominativo NVARCHAR(255) NULL,
                    StaffId NVARCHAR(50) NULL,
                    Causale NVARCHAR(20) NULL,
                    Causale2 NVARCHAR(20) NULL,
                    Inizio_1 NVARCHAR(5) NULL,
                    Fine_1 NVARCHAR(5) NULL,
                    Inizio_2 NVARCHAR(5) NULL,
                    Fine_2 NVARCHAR(5) NULL,
                    Inquadramento NVARCHAR(50) NULL,
                    S_Prestito NVARCHAR(255) NULL,
                    S_Prestito2 NVARCHAR(255) NULL,
                    Colore INT NULL
                )
                """
            )
        else:
            cur.execute(
                """
                CREATE TABLE STAFF_P (
                    id COUNTER PRIMARY KEY,
                    Site TEXT(20),
                    Data DATETIME,
                    Nominativo TEXT(255),
                    StaffId TEXT(50),
                    Causale TEXT(20),
                    Causale2 TEXT(20),
                    Inizio_1 TEXT(5),
                    Fine_1 TEXT(5),
                    Inizio_2 TEXT(5),
                    Fine_2 TEXT(5),
                    Inquadramento TEXT(50),
                    S_Prestito TEXT(255),
                    S_Prestito2 TEXT(255),
                    Colore LONG
                )
                """
            )
        try:
            cur.execute("CREATE INDEX idx_staff_p_site_data_nom ON STAFF_P (Site, Data, Nominativo)")
        except Exception:
            log_swallowed('orari_repository:205')
        try:
            cur.execute("CREATE INDEX idx_staff_p_site_data_staffid ON STAFF_P (Site, Data, StaffId)")
        except Exception:
            log_swallowed('orari_repository:209')
        conn.commit()
        return "STAFF_P"

    cols = _access_columns(cur, table)
    # Ensure Inquadramento column exists (added later)
    try:
        low_cols = [c.lower() for c in cols]
        if 'inquadramento' not in low_cols:
            ddl = 'Inquadramento NVARCHAR(50) NULL' if _is_sqlserver() else 'Inquadramento TEXT(50)'
            add_kw = ' ADD ' if _is_sqlserver() else ' ADD COLUMN '
            cur.execute('ALTER TABLE ' + table + add_kw + ddl)
            conn.commit()
            cols = _access_columns(cur, table)
    except Exception:
        log_swallowed('orari_repository:224')
    cand = _candidate_sets()

    def _has_any(logical: str) -> bool:
        return _pick_column(cols, cand[logical]) is not None

    def _add_if_missing(logical: str, ddl: str) -> None:
        if not _has_any(logical):
            try:
                add_kw = "ADD" if _is_sqlserver() else "ADD COLUMN"
                cur.execute(f"ALTER TABLE {_qname(table)} {add_kw} {ddl}")
            except Exception:
                log_swallowed('orari_repository:235')

    if _is_sqlserver():
        _add_if_missing("site", "Site NVARCHAR(20) NULL")
        _add_if_missing("data", "Data DATETIME NULL")
        _add_if_missing("nominativo", "Nominativo NVARCHAR(255) NULL")
        _add_if_missing("staff_id", "StaffId NVARCHAR(50) NULL")
        _add_if_missing("causale", "Causale NVARCHAR(20) NULL")
        _add_if_missing("causale2", "Causale2 NVARCHAR(20) NULL")
        _add_if_missing("inizio_1", "Inizio_1 NVARCHAR(5) NULL")
        _add_if_missing("fine_1", "Fine_1 NVARCHAR(5) NULL")
        _add_if_missing("inizio_2", "Inizio_2 NVARCHAR(5) NULL")
        _add_if_missing("fine_2", "Fine_2 NVARCHAR(5) NULL")
        _add_if_missing("s_prestito", "S_Prestito NVARCHAR(255) NULL")
        _add_if_missing("s_prestito2", "S_Prestito2 NVARCHAR(255) NULL")
        _add_if_missing("colore", "Colore INT NULL")
    else:
        _add_if_missing("site", "Site TEXT(20)")
        _add_if_missing("data", "Data DATETIME")
        _add_if_missing("nominativo", "Nominativo TEXT(255)")
        _add_if_missing("staff_id", "StaffId TEXT(50)")
        _add_if_missing("causale", "Causale TEXT(20)")
        _add_if_missing("causale2", "Causale2 TEXT(20)")
        _add_if_missing("inizio_1", "Inizio_1 TEXT(5)")
        _add_if_missing("fine_1", "Fine_1 TEXT(5)")
        _add_if_missing("inizio_2", "Inizio_2 TEXT(5)")
        _add_if_missing("fine_2", "Fine_2 TEXT(5)")
        _add_if_missing("s_prestito", "S_Prestito TEXT(255)")
        _add_if_missing("s_prestito2", "S_Prestito2 TEXT(255)")
        _add_if_missing("colore", "Colore LONG")

    try:
        conn.commit()
    except Exception:
        log_swallowed('orari_repository:270')

    return table


def ensure_turni_schema() -> None:
    conn = get_connection()
    try:
        _ensure_turni_table(conn)
    finally:
        try:
            conn.close()
        except Exception:
            log_swallowed('orari_repository:282')


def _to_access_datetime(d: date) -> datetime:
    return datetime(d.year, d.month, d.day)


def _to_iso_date(v: Any) -> str:
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    try:
        return datetime.fromisoformat(str(v)).date().isoformat()
    except Exception:
        return str(v)

def _time_to_hhmm(v: Any) -> str:
    """Convert various Access/pyodbc time representations to 'HH:MM' string."""
    if v is None:
        return ""
    if isinstance(v, datetime):
        return v.strftime("%H:%M")
    s = str(v).strip()
    if not s:
        return ""
    mm = re.search(r"(\b\d{1,2}:\d{2})(?::\d{2})?\b", s)
    if mm:
        t = mm.group(1)
        parts = t.split(":")
        try:
            hh = int(parts[0]); mi = int(parts[1])
            if 0 <= hh <= 23 and 0 <= mi <= 59:
                return f"{hh:02d}:{mi:02d}"
        except Exception:
            log_swallowed('orari_repository:318')
        return t
    return ""

def _resolve_schema(conn, table: str) -> Dict[str, Optional[str]]:
    cur = conn.cursor()
    cols = _access_columns(cur, table)
    cand = _candidate_sets()

    schema: Dict[str, Optional[str]] = {}
    for logical, candidates in cand.items():
        schema[logical] = _pick_column(cols, candidates)

    if not schema.get("site") or not schema.get("data") or not schema.get("nominativo"):
        cols2 = _access_columns(cur, table)
        for logical, candidates in cand.items():
            if not schema.get(logical):
                schema[logical] = _pick_column(cols2, candidates)

    if not schema.get("site") or not schema.get("data") or not schema.get("nominativo"):
        missing = [k for k in ("site", "data", "nominativo") if not schema.get(k)]
        raise RuntimeError(f"Tabella {table}: colonne mancanti per {', '.join(missing)}")

    return schema




def _load_staff_identity_maps(conn, *, store_code: str) -> tuple[Dict[str, Dict[str, str]], Dict[str, str]]:
    try:
        cur = conn.cursor()
        cols = _access_columns(cur, "STAFF")
        if not cols:
            return {}, {}
    except Exception:
        return {}, {}

    site_col = _pick_column(cols, ["Site", "SITE", "Sito", "Store", "CodiceStore"])
    id_col = _pick_column(cols, ["id", "staff_id", "idstaff", "employeeid", "dipendenteid"])
    nome_col = _pick_column(cols, ["NomeCognome", "Nome_Cognome", "Nominativo", "Nome", "Dipendente", "Staff"])
    ruolo_col = _pick_column(cols, ["Ruolo", "Inquadramento", "Role", "Mansione"])
    if not id_col or not nome_col:
        return {}, {}

    select_cols = [_qname(id_col), _qname(nome_col)]
    if ruolo_col:
        select_cols.append(_qname(ruolo_col))
    sql = f"SELECT {', '.join(select_cols)} FROM {_qname('STAFF')}"
    params: List[Any] = []
    if site_col:
        sql += f" WHERE {_qname(site_col)} = ?"
        params.append(str(store_code))
    sql += f" ORDER BY {_qname(nome_col)}"

    by_id: Dict[str, Dict[str, str]] = {}
    name_to_id: Dict[str, str] = {}
    try:
        cur.execute(sql, params)
        for row in cur.fetchall() or []:
            sid = str(row[0] or '').strip()
            nome = str(row[1] or '').strip()
            ruolo = str(row[2] or '').strip() if ruolo_col else ''
            if not sid or not nome:
                continue
            by_id[sid] = {'nome_cognome': nome, 'ruolo': ruolo}
            name_to_id[nome] = sid
    except Exception:
        return {}, {}
    return by_id, name_to_id

def list_turni_week(
    *,
    store_code: str,
    start_day: date,
    end_day: date,
    nominativi: Optional[Iterable[str]] = None,
) -> List[Dict[str, Any]]:
    conn = get_connection(store_code)
    try:
        table = _ensure_turni_table(conn)
        schema = _resolve_schema(conn, table)

        site_col = schema.get("site") or "Site"
        data_col = schema.get("data") or "Data"
        nom_col = schema.get("nominativo") or "Nominativo"
        staff_id_col = schema.get("staff_id")

        cur = conn.cursor()
        staff_by_id, name_to_staff_id = _load_staff_identity_maps(conn, store_code=store_code)

        # Detect Access types to build a safe WHERE (avoids -3030 mismatch)
        types = _access_column_types(cur, table)
        site_t = types.get(_norm(site_col), "")
        data_t = types.get(_norm(data_col), "")

        store_code_s = str(store_code or "").strip()
        start_dt = datetime(start_day.year, start_day.month, start_day.day)
        end_excl = datetime(end_day.year, end_day.month, end_day.day) + timedelta(days=1)

        params: List[Any] = []
        where_parts: List[str] = []

        # Site
        where_parts.append(f"{_qname(site_col)}=?")
        params.append(_coerce_site_param(store_code_s, site_t))

        # Date range (inclusive of end_day)
        if "DATE" in (data_t or "").upper() or "TIME" in (data_t or "").upper():
            where_parts.append(f"{_qname(data_col)}>=? AND {_qname(data_col)}<?")
            params.extend([start_dt, end_excl])
        else:
            # Text/date stored as string: keep it robust with DateValue on both sides (params as strings)
            where_parts.append(f"{sql_date(_qname(data_col))}>=? AND {sql_date(_qname(data_col))}<=?")
            params.extend([start_day.isoformat(), end_day.isoformat()])

        # Nominativi filter
        noms = [str(x).strip() for x in (nominativi or []) if str(x or "").strip()]
        if noms:
            clause_parts: List[str] = []
            placeholders = ",".join(["?"] * len(noms))
            clause_parts.append(f"{_qname(nom_col)} IN ({placeholders})")
            params.extend(noms)
            if staff_id_col:
                staff_ids = [name_to_staff_id.get(n) for n in noms]
                staff_ids = [str(x).strip() for x in staff_ids if str(x or '').strip()]
                if staff_ids:
                    sid_ph = ",".join(["?"] * len(staff_ids))
                    clause_parts.append(f"{sql_cast_str(_qname(staff_id_col))} IN ({sid_ph})")
                    params.extend(staff_ids)
            where_parts.append("(" + " OR ".join(clause_parts) + ")")

        where = " WHERE " + " AND ".join(where_parts)
        sql = f"SELECT * FROM {_qname(table)}{where} ORDER BY {_qname(nom_col)}, {_qname(data_col)}"
        cur.execute(sql, params)

        col_names = [d[0] for d in (cur.description or [])]
        idx = {str(n or "").strip().lower(): i for i, n in enumerate(col_names) if n}

        def _val(row, name: Optional[str]) -> Any:
            if not name:
                return None
            i = idx.get(str(name).strip().lower())
            if i is None:
                return None
            try:
                return row[i]
            except Exception:
                return None

        def _get(row, logical: str, fallbacks: List[str]) -> Any:
            phys = schema.get(logical)
            if phys:
                v = _val(row, phys)
                if v is not None:
                    return v
            for fb in fallbacks:
                v = _val(row, fb)
                if v is not None:
                    return v
            return None

        out: List[Dict[str, Any]] = []
        for row in cur.fetchall() or []:
            dt = _get(row, "data", ["Data", "DATA", "Giorno", "Date"])
            sid = str(_get(row, "staff_id", ["StaffId", "Staff_ID", "staff_id", "IdStaff"]) or "").strip()
            staff_live = staff_by_id.get(sid) if sid else None
            colore_v = _get(row, "colore", ["Colore", "Color", "ColorCode"])
            try:
                colore_i = int(colore_v) if colore_v is not None else 0
            except Exception:
                colore_i = 0

            out.append(
                {
                    "site": str(_get(row, "site", ["Site", "SITE"]) or "").strip(),
                    "data": _to_iso_date(dt),
                    "staff_id": sid,
                    "nominativo": str((staff_live or {}).get("nome_cognome") or _get(row, "nominativo", ["Nominativo", "NomeCognome", "Staff"]) or "").strip(),
                    "causale": str(_get(row, "causale", ["Causale", "Causa", "Motivo"]) or "").strip(),
                    "causale2": str(_get(row, "causale2", ["Causale2", "Causale_2", "Causa2", "Motivo2", "Causale 2"]) or "").strip(),
                    "inizio_1": _time_to_hhmm(_get(row, "inizio_1", ["Inizio_1", "Inizio1", "Start1"])),
                    "fine_1": _time_to_hhmm(_get(row, "fine_1", ["Fine_1", "Fine1", "End1"])),
                    "inizio_2": _time_to_hhmm(_get(row, "inizio_2", ["Inizio_2", "Inizio2", "Start2"])),
                    "fine_2": _time_to_hhmm(_get(row, "fine_2", ["Fine_2", "Fine2", "End2"])),
                    "s_prestito": str(_get(row, "s_prestito", ["S_Prestito", "S_prestito", "S_presito", "SPrestito", "Prestito"]) or "").strip(),
                    "s_prestito2": str(_get(row, "s_prestito2", ["S_Prestito2", "S_prestito2", "S_presito2", "SPrestito2", "Prestito2"]) or "").strip(),
                    "colore": colore_i,
                    "inquadramento": str((staff_live or {}).get("ruolo") or _get(row, "inquadramento", ["Inquadramento", "InquadramentoStaff", "Ruolo", "Role"]) or "").strip(),
                }
            )

        return out
    finally:
        try:
            conn.close()
        except Exception:
            log_swallowed('orari_repository:514')

def save_turni_week(*, store_code: str, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    conn = get_connection(store_code)
    try:
        table = _ensure_turni_table(conn)
        schema = _resolve_schema(conn, table)

        site_col = schema["site"]
        data_col = schema["data"]
        nom_col = schema["nominativo"]
        staff_id_col = schema.get("staff_id")

        def _col_or_default(logical: str, default_name: str) -> str:
            c = schema.get(logical)
            return c if c else default_name

        causale_col = _col_or_default("causale", "Causale")
        causale2_col = schema.get("causale2")
        in1_col = _col_or_default("inizio_1", "Inizio_1")
        fi1_col = _col_or_default("fine_1", "Fine_1")
        in2_col = _col_or_default("inizio_2", "Inizio_2")
        fi2_col = _col_or_default("fine_2", "Fine_2")
        prestito_col = _col_or_default("s_prestito", "S_Prestito")
        prestito2_col = _col_or_default("s_prestito2", "S_Prestito2")
        inq_col = schema.get("inquadramento")
        colore_col = _col_or_default("colore", "Colore")

        cur = conn.cursor()

        # Detect Access types to avoid -3030 mismatch in WHERE criteria
        types = _access_column_types(cur, table)
        site_t = types.get(_norm(site_col), "")
        data_t = types.get(_norm(data_col), "")
        colore_t = types.get(_norm(colore_col), "")
        in1_t = types.get(_norm(in1_col), "")
        fi1_t = types.get(_norm(fi1_col), "")
        in2_t = types.get(_norm(in2_col), "")
        fi2_t = types.get(_norm(fi2_col), "")

        store_code_s = str(store_code or "").strip()
        _staff_by_id, name_to_staff_id = _load_staff_identity_maps(conn, store_code=store_code)

        def _parse_day(d_iso: str) -> date:
            try:
                return datetime.fromisoformat(str(d_iso)).date()
            except Exception:
                try:
                    return datetime.strptime(str(d_iso), "%d/%m/%Y").date()
                except Exception:
                    return datetime.today().date()

        def _coerce_time_param(v: str, type_name: str) -> Any:
            s = (v or "").strip()
            if not s:
                if "DATE" in (type_name or "").upper() or "TIME" in (type_name or "").upper():
                    return None
                return ""
            m = re.match(r"^\s*(\d{1,2})\s*:\s*(\d{2})\s*$", s)
            if not m:
                return s if _is_text_type(type_name) else None
            hh = int(m.group(1))
            mm = int(m.group(2))
            if hh < 0 or hh > 23 or mm < 0 or mm > 59:
                return s if _is_text_type(type_name) else None
            if "DATE" in (type_name or "").upper() or "TIME" in (type_name or "").upper():
                return datetime(1899, 12, 30, hh, mm, 0)
            return f"{hh:02d}:{mm:02d}"

        def _coerce_colore_param(v: int, type_name: str) -> Any:
            if _is_text_type(type_name):
                return str(int(v))
            return int(v)

        def _build_where(nominativo: str, staff_id: str, d: date) -> Tuple[str, List[Any], datetime]:
            d_start = datetime(d.year, d.month, d.day)
            if staff_id and staff_id_col:
                where = (
                    f"{sql_cast_str(_qname(site_col))}=? AND "
                    f"({sql_cast_str(_qname(staff_id_col))}=? OR ({_qname(staff_id_col)} IS NULL AND {sql_cast_str(_qname(nom_col))}=?)) AND "
                    f"{sql_date(_qname(data_col))}=?"
                )
                params = [store_code_s, staff_id, nominativo, d_start]
            else:
                where = (
                    f"{sql_cast_str(_qname(site_col))}=? AND "
                    f"{sql_cast_str(_qname(nom_col))}=? AND "
                    f"{sql_date(_qname(data_col))}=?"
                )
                params = [store_code_s, nominativo, d_start]
            return where, params, d_start

        saved = 0
        deleted = 0

        for row in rows or []:
            nominativo = str((row or {}).get("nominativo") or "").strip()
            data_s = str((row or {}).get("data") or "").strip()
            if not nominativo or not data_s:
                continue

            # Normalize incoming date to ISO if possible
            try:
                data_iso = datetime.fromisoformat(data_s).date().isoformat()
            except Exception:
                data_iso = data_s

            staff_id = str((row or {}).get("staff_id") or name_to_staff_id.get(nominativo) or "").strip()
            causale = str((row or {}).get("causale") or "").strip()
            causale2 = str((row or {}).get("causale2") or "").strip()
            in1 = str((row or {}).get("inizio_1") or "").strip()
            fi1 = str((row or {}).get("fine_1") or "").strip()
            in2 = str((row or {}).get("inizio_2") or "").strip()
            fi2 = str((row or {}).get("fine_2") or "").strip()
            prestito = str((row or {}).get("S_Prestito") or (row or {}).get("s_prestito") or "").strip()
            prestito2 = str((row or {}).get("S_Prestito2") or (row or {}).get("s_prestito2") or "").strip()
            inquadramento = str((row or {}).get("inquadramento") or (row or {}).get("Inquadramento") or "").strip()

            try:
                colore = int((row or {}).get("colore") or 0)
            except Exception:
                colore = 0

            is_empty = (not causale) and (not causale2) and (not in1) and (not fi1) and (not in2) and (not fi2) and (not prestito) and (not prestito2) and (colore == 0)

            # Coerce values based on Access column types (TEXT vs DATETIME/LONG)
            in1_p = _coerce_time_param(in1, in1_t)
            fi1_p = _coerce_time_param(fi1, fi1_t)
            in2_p = _coerce_time_param(in2, in2_t)
            fi2_p = _coerce_time_param(fi2, fi2_t)
            colore_p = _coerce_colore_param(colore, colore_t)

            d_obj = _parse_day(data_iso)
            where, key_params, data_value = _build_where(nominativo, staff_id, d_obj)

            if is_empty:
                # Keep an explicit row for each day/person. Empty values are saved as NULL/"" and colore=0.
                pass
# Try update first (avoids COUNT(*) edge cases)
            # Build UPDATE with optional Inquadramento
            set_parts = [
                f"{_qname(nom_col)}=?",
                f"{_qname(causale_col)}=?",
                f"{_qname(in1_col)}=?",
                f"{_qname(fi1_col)}=?",
                f"{_qname(in2_col)}=?",
                f"{_qname(fi2_col)}=?",
                f"{_qname(prestito_col)}=?",
            ]
            upd_params = [nominativo, causale, in1_p, fi1_p, in2_p, fi2_p, prestito]

            if staff_id_col:
                set_parts.append(f"{_qname(staff_id_col)}=?")
                upd_params.append(staff_id if staff_id else None)
            if causale2_col:
                set_parts.append(f"{_qname(causale2_col)}=?")
                upd_params.append(causale2)
            if prestito2_col:
                set_parts.append(f"{_qname(prestito2_col)}=?")
                upd_params.append(prestito2)
            if inq_col:
                set_parts.append(f"{_qname(inq_col)}=?")
                upd_params.append(inquadramento)
            set_parts.append(f"{_qname(colore_col)}=?")
            upd_params.append(colore_p)

            cur.execute(
                f"UPDATE {_qname(table)} SET " + ", ".join(set_parts) + f" WHERE {where}",
                upd_params + key_params,
            )
            updated = 0
            try:
                updated = int(cur.rowcount or 0)
            except Exception:
                updated = 0

            if updated <= 0:
                # Insert new row
                
                # Insert new row (optional Inquadramento)
                cols_ins = [
                    _qname(site_col),
                    _qname(nom_col),
                    _qname(data_col),
                    _qname(causale_col),
                    _qname(in1_col),
                    _qname(fi1_col),
                    _qname(in2_col),
                    _qname(fi2_col),
                    _qname(prestito_col),
                ]
                vals = [
                    _coerce_site_param(store_code_s, site_t),
                    nominativo,
                    data_value,
                    causale,
                    in1_p,
                    fi1_p,
                    in2_p,
                    fi2_p,
                    prestito,
                ]
                if staff_id_col:
                    cols_ins.append(_qname(staff_id_col))
                    vals.append(staff_id if staff_id else None)
                if causale2_col:
                    cols_ins.append(_qname(causale2_col))
                    vals.append(causale2)
                if prestito2_col:
                    cols_ins.append(_qname(prestito2_col))
                    vals.append(prestito2)
                if inq_col:
                    cols_ins.append(_qname(inq_col))
                    vals.append(inquadramento)
                cols_ins.append(_qname(colore_col))
                vals.append(colore_p)

                qmarks = ",".join(["?"] * len(vals))
                cur.execute(
                    f"INSERT INTO {_qname(table)} (" + ", ".join(cols_ins) + f") VALUES ({qmarks})",
                    vals,
                )
            saved += 1
        conn.commit()
        return {"ok": True, "saved": saved, "deleted": deleted}
    finally:
        try:
            conn.close()
        except Exception:
            log_swallowed('orari_repository:743')





def update_turni_inquadramento_by_nominativo(*, store_code: str, nominativo: str, inquadramento: str) -> Dict[str, Any]:
    """Aggiorna retroattivamente l'inquadramento salvato negli orari per un nominativo (tutte le date dello store)."""
    conn = get_connection(store_code)
    try:
        table = _ensure_turni_table(conn)
        schema = _resolve_schema(conn, table)

        site_col = schema.get("site") or "Site"
        nom_col = schema.get("nominativo") or "Nominativo"
        inq_col = schema.get("inquadramento")
        if not inq_col:
            return {"ok": False, "updated": 0, "reason": "inquadramento_column_missing"}

        nom = str(nominativo or "").strip()
        if not nom:
            return {"ok": False, "updated": 0, "reason": "missing_nominativo"}

        cur = conn.cursor()
        sql = (
            f"UPDATE {_qname(table)} SET {_qname(inq_col)}=? "
            f"WHERE {sql_cast_str(_qname(site_col))}=? AND {sql_cast_str(_qname(nom_col))}=?"
        )
        cur.execute(sql, [str(inquadramento or "").strip(), str(store_code or "").strip(), nom])
        try:
            updated = int(cur.rowcount or 0)
        except Exception:
            updated = 0
        try:
            conn.commit()
        except Exception:
            log_swallowed('orari_repository:779')
        return {"ok": True, "updated": updated, "table": table}
    finally:
        try:
            conn.close()
        except Exception:
            log_swallowed('orari_repository:784')


def relink_turni_staff_identity(
    *,
    store_code: str,
    staff_id: Optional[str],
    old_nominativo: str,
    new_nominativo: str,
    inquadramento: Optional[str] = None,
) -> Dict[str, Any]:
    """Aggiorna i record orari esistenti per seguire il rename in anagrafica."""
    conn = get_connection(store_code)
    try:
        table = _ensure_turni_table(conn)
        schema = _resolve_schema(conn, table)

        site_col = schema.get("site") or "Site"
        nom_col = schema.get("nominativo") or "Nominativo"
        staff_id_col = schema.get("staff_id")
        inq_col = schema.get("inquadramento")

        old_nome_s = str(old_nominativo or "").strip()
        new_nome_s = str(new_nominativo or "").strip()
        staff_id_s = str(staff_id or "").strip()
        inq_s = str(inquadramento or "").strip()

        if not old_nome_s or not new_nome_s:
            return {"ok": False, "updated": 0, "reason": "missing_nominativo"}

        cur = conn.cursor()
        set_parts = [f"{_qname(nom_col)}=?"]
        params: List[Any] = [new_nome_s]

        if staff_id_col and staff_id_s:
            set_parts.append(f"{_qname(staff_id_col)}=?")
            params.append(staff_id_s)

        if inq_col:
            set_parts.append(f"{_qname(inq_col)}=?")
            params.append(inq_s if inq_s else None)

        where_parts = [f"{sql_cast_str(_qname(site_col))}=?"]
        params.append(str(store_code or "").strip())

        if staff_id_col and staff_id_s:
            where_parts.append(
                "("
                f"{sql_cast_str(_qname(staff_id_col))}=? "
                f"OR ({_qname(staff_id_col)} IS NULL AND {sql_cast_str(_qname(nom_col))}=?)"
                ")"
            )
            params.extend([staff_id_s, old_nome_s])
        else:
            where_parts.append(f"{sql_cast_str(_qname(nom_col))}=?")
            params.append(old_nome_s)

        sql = f"UPDATE {_qname(table)} SET {', '.join(set_parts)} WHERE {' AND '.join(where_parts)}"
        cur.execute(sql, params)
        try:
            updated = int(cur.rowcount or 0)
        except Exception:
            updated = 0
        try:
            conn.commit()
        except Exception:
            log_swallowed('orari_repository:850')
        return {"ok": True, "updated": updated, "table": table}
    finally:
        try:
            conn.close()
        except Exception:
            log_swallowed('orari_repository:856')


def _monday(d: date) -> date:
    # Monday = 0 in Python weekday()
    try:
        return d - timedelta(days=int(d.weekday()))
    except Exception:
        return d


def delete_turni_range(*, store_code: str, start_day: date, end_day: date) -> Dict[str, Any]:
    """Delete ALL shift rows in the given date range (inclusive) for the store."""
    conn = get_connection(store_code)
    try:
        table = _ensure_turni_table(conn)
        schema = _resolve_schema(conn, table)

        site_col = schema.get("site") or "Site"
        data_col = schema.get("data") or "Data"

        cur = conn.cursor()

        store_code_s = str(store_code or "").strip()

        d1 = datetime(start_day.year, start_day.month, start_day.day)
        d2 = datetime(end_day.year, end_day.month, end_day.day)

        sql = (
            f"DELETE FROM {_qname(table)} "
            f"WHERE {sql_cast_str(_qname(site_col))}=? AND {sql_date(_qname(data_col))} BETWEEN ? AND ?"
        )
        cur.execute(sql, [store_code_s, d1, d2])

        deleted = 0
        try:
            deleted = int(cur.rowcount or 0)
        except Exception:
            deleted = 0

        try:
            conn.commit()
        except Exception:
            log_swallowed('orari_repository:900')

        return {"ok": True, "deleted": deleted, "table": table}
    finally:
        try:
            conn.close()
        except Exception:
            log_swallowed('orari_repository:907')


def overwrite_week_from_week(
    *,
    store_code: str,
    source_week_start: date,
    target_week_start: date,
) -> Dict[str, Any]:
    """Overwrite target week with the shifts of source week (same store)."""
    src0 = _monday(source_week_start)
    tgt0 = _monday(target_week_start)
    src_end = src0 + timedelta(days=6)
    tgt_end = tgt0 + timedelta(days=6)

    # Load all rows from source week (including empty rows if they exist)
    src_rows = list_turni_week(store_code=store_code, start_day=src0, end_day=src_end, nominativi=None)

    # Delete ALL rows in target week
    del_res = delete_turni_range(store_code=store_code, start_day=tgt0, end_day=tgt_end)

    # Shift dates and save
    delta = (tgt0 - src0).days
    shifted: List[Dict[str, Any]] = []
    for r in (src_rows or []):
        d_iso = str((r or {}).get("data") or "").strip()
        if not d_iso:
            continue
        try:
            d = date.fromisoformat(d_iso)
        except Exception:
            continue
        rr = dict(r or {})
        rr["data"] = (d + timedelta(days=delta)).isoformat()
        shifted.append(rr)

    save_res = save_turni_week(store_code=store_code, rows=shifted)

    return {
        "ok": True,
        "source_week_start": src0.isoformat(),
        "target_week_start": tgt0.isoformat(),
        "source_rows": len(src_rows or []),
        "deleted": int(del_res.get("deleted") or 0),
        "saved": int(save_res.get("saved") or 0),
    }



def list_compiled_nominativi_week(*, store_code: str, start_day: date, end_day: date) -> List[str]:
    rows = list_turni_week(store_code=store_code, start_day=start_day, end_day=end_day, nominativi=None)
    out: set[str] = set()
    for r in rows or []:
        nom = str((r or {}).get("nominativo") or "").strip()
        if not nom:
            continue
        causale = str((r or {}).get("causale") or "").strip()
        causale2 = str((r or {}).get("causale2") or "").strip()
        s_prestito = str((r or {}).get("s_prestito") or "").strip()
        s_prestito2 = str((r or {}).get("s_prestito2") or "").strip()
        in1 = str((r or {}).get("inizio_1") or "").strip()
        fi1 = str((r or {}).get("fine_1") or "").strip()
        in2 = str((r or {}).get("inizio_2") or "").strip()
        fi2 = str((r or {}).get("fine_2") or "").strip()
        try:
            col = int((r or {}).get("colore") or 0)
        except Exception:
            col = 0
        if causale or causale2 or s_prestito or s_prestito2 or in1 or fi1 or in2 or fi2 or col:
            out.add(nom)
    return sorted(out, key=lambda x: x.lower())
