from __future__ import annotations

from app_logging import log_swallowed
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

from app_db import get_backend, get_connection, sql_cast_str, supports_schema_alter


def _qname(name: str) -> str:
    return f"[{str(name).replace(']', ']]')}]"


def _access_has_table(cur, table_name: str) -> bool:
    t = str(table_name).strip().lower()
    for row in cur.tables(tableType="TABLE"):
        n = getattr(row, "table_name", None) or (len(row) > 2 and row[2]) or None
        if n and str(n).strip().lower() == t:
            return True
    return False


def _is_sqlserver() -> bool:
    return get_backend() == "sqlserver"


def _get_table_columns_ordered(cur, table_name: str) -> List[str]:
    cols: List[str] = []
    for row in cur.columns(table=table_name):
        n = getattr(row, "column_name", None) or (len(row) > 3 and row[3]) or None
        if n:
            cols.append(str(n))
    return cols


def _pick_column(columns: List[str], candidates: Iterable[str]) -> Optional[str]:
    """Pick the first matching column name from a list of candidates.

    Tries exact match first (case-insensitive), then substring match.
    """
    cols = [str(c) for c in (columns or []) if c]
    low = [c.lower() for c in cols]

    cand = [str(x) for x in candidates if x]
    cand_low = [c.lower() for c in cand]

    # Exact
    for i, c in enumerate(cand_low):
        for j, cc in enumerate(low):
            if cc == c:
                return cols[j]

    # Substring
    for i, c in enumerate(cand_low):
        for j, cc in enumerate(low):
            if c and c in cc:
                return cols[j]

    return None


def _delete_staff_turni_rows(conn, *, store_code: str, nominativo: str) -> int:
    """Best-effort deletion of schedule rows for a nominativo.

    Removes rows from STAFF_P / STAFF_TURNI (if present) for the current store DB.
    Returns the total deleted rows (sum of rowcount when available).
    """
    deleted_total = 0
    cur = conn.cursor()

    turni_tables = []
    for t in ("STAFF_P", "STAFF_TURNI"):
        if _access_has_table(cur, t):
            turni_tables.append(t)

    if not turni_tables:
        return 0

    # Candidate columns aligned to orari_repository
    site_candidates = ["Site", "SITE", "Sito", "Store", "CodiceStore"]
    nom_candidates = ["Nominativo", "NomeCognome", "Nome e Cognome", "Dipendente", "Staff"]

    for table in turni_tables:
        try:
            cols = _get_table_columns_ordered(cur, table)
            site_col = _pick_column(cols, site_candidates)
            nom_col = _pick_column(cols, nom_candidates)
            if not nom_col:
                continue

            where_parts: List[str] = []
            params: List[Any] = []

            # Match store if possible; fall back to nominativo-only if the schema lacks Site.
            if site_col:
                where_parts.append(f"{sql_cast_str(_qname(site_col))} = ?")
                params.append(str(store_code))

            where_parts.append(f"{sql_cast_str(_qname(nom_col))} = ?")
            params.append(str(nominativo).strip())

            sql = f"DELETE FROM {_qname(table)} WHERE {' AND '.join(where_parts)}"
            cur.execute(sql, params)

            try:
                deleted_total += int(cur.rowcount or 0)
            except Exception:
                # Some ODBC drivers return -1; we still proceed.
                log_swallowed('staff_repository:109')
        except Exception:
            # Best-effort: keep deleting staff even if schedules can't be removed.
            log_swallowed('staff_repository:112')

    try:
        conn.commit()
    except Exception:
        log_swallowed('staff_repository:116')

    return deleted_total


def _ensure_staff_table(conn) -> None:
    cur = conn.cursor()
    if not _access_has_table(cur, "STAFF"):
        if _is_sqlserver():
            sql = """
            CREATE TABLE STAFF (
                id INT IDENTITY(1,1) NOT NULL PRIMARY KEY,
                Site NVARCHAR(20) NULL,
                NomeCognome NVARCHAR(255) NULL,
                Ruolo NVARCHAR(50) NULL,
                OreContrattuali INT NULL,
                Attivo INT NULL
            )
            """.strip()
        else:
            if not supports_schema_alter():
                return
            sql = """
            CREATE TABLE STAFF (
                id COUNTER PRIMARY KEY,
                Site TEXT(20),
                NomeCognome TEXT(255),
                Ruolo TEXT(50),
                OreContrattuali LONG,
                Attivo LONG
            )
            """.strip()
        cur.execute(sql)
        conn.commit()
        return

    cols = _get_table_columns_ordered(cur, "STAFF")
    low = [c.lower() for c in cols]
    if "attivo" not in low and "active" not in low and "isactive" not in low:
        try:
            ddl = "Attivo INT NULL" if _is_sqlserver() else "Attivo LONG"
            add_kw = "ADD" if _is_sqlserver() else "ADD COLUMN"
            cur.execute(f"ALTER TABLE {_qname('STAFF')} {add_kw} {ddl}")
            conn.commit()
        except Exception:
            log_swallowed('staff_repository:161')

    try:
        cur.execute(f"UPDATE {_qname('STAFF')} SET {_qname('Attivo')}=1 WHERE {_qname('Attivo')} IS NULL")
        conn.commit()
    except Exception:
        log_swallowed('staff_repository:167')


def ensure_staff_schema() -> None:
    conn = get_connection()
    try:
        _ensure_staff_table(conn)
    finally:
        try:
            conn.close()
        except Exception:
            log_swallowed('staff_repository:178')


@dataclass
class StaffColumns:
    nome_col: str
    ruolo_col: str
    ore_col: str
    codice_dipendente_col: Optional[str] = None
    scheduling_col: Optional[str] = None
    site_col: Optional[str] = None
    id_col: Optional[str] = None
    attivo_col: Optional[str] = None


def _guess_staff_columns(columns: List[str]) -> StaffColumns:
    cols = columns[:]  # ordered
    low = [c.lower() for c in cols]

    def _norm(s: Optional[str]) -> str:
        return str(s).strip().lower() if s else ""

    def find_by_keywords(
        keys: tuple[str, ...],
        *,
        exclude: tuple[str, ...] = (),
        exclude_cols: Iterable[Optional[str]] = (),
    ) -> Optional[str]:
        excluded_cols = {_norm(c) for c in exclude_cols if c}
        exclude_keys = tuple(_norm(k) for k in exclude if k)
        for k in keys:
            kk = _norm(k)
            if not kk:
                continue
            for i, c in enumerate(low):
                if c in excluded_cols:
                    continue
                if kk in c:
                    if exclude_keys and any(e in c for e in exclude_keys):
                        continue
                    return cols[i]
        return None

    def find_contains_all(
        required: tuple[str, ...],
        *,
        exclude: tuple[str, ...] = (),
        exclude_cols: Iterable[Optional[str]] = (),
    ) -> Optional[str]:
        excluded_cols = {_norm(c) for c in exclude_cols if c}
        req = [_norm(r) for r in required if r]
        ex = [_norm(e) for e in exclude if e]
        for i, c in enumerate(low):
            if c in excluded_cols:
                continue
            if req and all(r in c for r in req):
                if ex and any(e in c for e in ex):
                    continue
                return cols[i]
        return None

    # --- identify key columns (prefer explicit names first) ---
    id_col: Optional[str] = None
    for i, c in enumerate(low):
        if c == "id" or c.endswith("_id") or c.startswith("id_") or "counter" in c:
            id_col = cols[i]
            break
    if not id_col:
        for i, c in enumerate(low):
            if c.endswith("id") and "site" not in c and "store" not in c:
                id_col = cols[i]
                break

    site_col = find_by_keywords(("site", "store", "negoz", "punto"))

    # Codice dipendente: prefer match specifico, poi fallback "codice"+"dipend"
    codice_dipendente_col = (
        find_by_keywords(
            (
                "codice_dipendente",
                "codicedipendente",
                "cod_dipendente",
                "employee_code",
                "employeeid",
                "employee_id",
                "matricola",
                "matric",
                "badge",
            ),
            exclude=("site", "store"),
        )
        or find_contains_all(("codice", "dipend"), exclude=("site", "store"))
        or find_contains_all(("code", "employee"), exclude=("site", "store"))
        or None
    )

    scheduling_col = find_by_keywords(("scheduling", "schedul", "sched"), exclude=("site", "store"))

    # Nome dipendente: evita di scambiare "codice_dipendente" per nominativo
    nome_col = (
        find_by_keywords(
            ("nomecognome", "nome_cognome", "nominativo", "nome", "cognome"),
            exclude=("codice", "matric", "badge", "employee", "id", "sched"),
            exclude_cols=(codice_dipendente_col, scheduling_col),
        )
        or find_by_keywords(
            ("dipend", "staff"),
            exclude=("codice", "matric", "badge", "employee", "id", "sched"),
            exclude_cols=(codice_dipendente_col, scheduling_col),
        )
        or None
    )

    ruolo_col = find_by_keywords(("ruolo", "inquadr", "role", "mans"))
    ore_col = find_by_keywords(("orecontrattuali", "ore_contrattuali", "ore", "contratt", "hour"))
    attivo_col = find_by_keywords(("attivo", "active", "isactive", "inatt"))

    # --- fallbacks: pick remaining columns in order ---
    used = {c for c in (id_col, site_col, nome_col, ruolo_col, ore_col, codice_dipendente_col, scheduling_col, attivo_col) if c}
    leftovers = [c for c in cols if c not in used]

    if not nome_col:
        nome_col = leftovers.pop(0) if leftovers else cols[0]
    if not ruolo_col:
        ruolo_col = leftovers.pop(0) if leftovers else cols[min(1, len(cols) - 1)]
    if not ore_col:
        ore_col = leftovers.pop(0) if leftovers else cols[min(2, len(cols) - 1)]

    # Guard: evita selezioni duplicate che possono generare "colonna ambigua" in ORDER BY
    if codice_dipendente_col and _norm(codice_dipendente_col) == _norm(nome_col):
        codice_dipendente_col = None
    if scheduling_col and _norm(scheduling_col) == _norm(nome_col):
        scheduling_col = None
    if attivo_col and _norm(attivo_col) == _norm(nome_col):
        attivo_col = None

    return StaffColumns(
        nome_col=nome_col,
        ruolo_col=ruolo_col,
        ore_col=ore_col,
        codice_dipendente_col=codice_dipendente_col,
        scheduling_col=scheduling_col,
        site_col=site_col,
        id_col=id_col,
        attivo_col=attivo_col,
    )


def list_staff(*, store_code: str, only_active: bool = False) -> List[Dict[str, Any]]:
    conn = get_connection(store_code)
    try:
        _ensure_staff_table(conn)
        cur = conn.cursor()
        cols = _get_table_columns_ordered(cur, "STAFF")
        sc = _guess_staff_columns(cols)

        select_cols: List[str] = []
        if sc.id_col:
            select_cols.append(_qname(sc.id_col))
        select_cols += [
            _qname(sc.nome_col),
            _qname(sc.ruolo_col),
            _qname(sc.ore_col),
        ]
        if sc.codice_dipendente_col:
            select_cols.append(_qname(sc.codice_dipendente_col))
        if sc.scheduling_col:
            select_cols.append(_qname(sc.scheduling_col))
        if sc.attivo_col:
            select_cols.append(_qname(sc.attivo_col))

        where_parts: List[str] = []
        params: List[Any] = []

        if sc.site_col:
            where_parts.append(f"{_qname(sc.site_col)} = ?")
            params.append(str(store_code))

        if only_active and sc.attivo_col:
            where_parts.append(f"({_qname(sc.attivo_col)} <> 0)")

        where_sql = f" WHERE {' AND '.join(where_parts)}" if where_parts else ""
        order_sql = f" ORDER BY {_qname(sc.nome_col)}"
        sql = f"SELECT {', '.join(select_cols)} FROM {_qname('STAFF')}{where_sql}{order_sql}"
        cur.execute(sql, params)

        out: List[Dict[str, Any]] = []
        for row in cur.fetchall() or []:
            d: Dict[str, Any] = {}
            i = 0
            if sc.id_col:
                d["id"] = row[i]
                i += 1
            else:
                d["id"] = None

            d["nome_cognome"] = (row[i] or "").strip() if row[i] is not None else ""
            i += 1
            d["ruolo"] = (row[i] or "").strip() if row[i] is not None else ""
            i += 1
            try:
                d["ore_contrattuali"] = int(row[i]) if row[i] is not None else 0
            except Exception:
                d["ore_contrattuali"] = 0
            i += 1

            if sc.codice_dipendente_col:
                d["codice_dipendente"] = (row[i] or "").strip() if row[i] is not None else ""
                i += 1
            else:
                d["codice_dipendente"] = ""

            if sc.scheduling_col:
                try:
                    d["scheduling"] = bool(int(row[i] or 0))
                except Exception:
                    d["scheduling"] = False
                i += 1
            else:
                d["scheduling"] = False

            if sc.attivo_col:
                try:
                    d["attivo"] = bool(int(row[i] or 0))
                except Exception:
                    d["attivo"] = True
                i += 1
            else:
                d["attivo"] = True

            out.append(d)

        return out
    finally:
        try:
            conn.close()
        except Exception:
            log_swallowed('staff_repository:415')


def insert_staff(
    *,
    store_code: str,
    nome_cognome: str,
    ruolo: str,
    ore_contrattuali: int,
    codice_dipendente: Optional[str] = None,
    scheduling: bool = False,
) -> None:
    conn = get_connection(store_code)
    try:
        _ensure_staff_table(conn)
        cur = conn.cursor()
        cols = _get_table_columns_ordered(cur, "STAFF")
        sc = _guess_staff_columns(cols)

        col_names: List[str] = []
        values: List[Any] = []

        if sc.site_col:
            col_names.append(sc.site_col)
            values.append(str(store_code))

        col_names += [sc.nome_col, sc.ruolo_col, sc.ore_col]
        values += [str(nome_cognome).strip(), str(ruolo).strip(), int(ore_contrattuali)]

        if sc.codice_dipendente_col:
            col_names.append(sc.codice_dipendente_col)
            v = (codice_dipendente or "").strip()
            values.append(v if v else None)

        if sc.scheduling_col:
            col_names.append(sc.scheduling_col)
            values.append(1 if bool(scheduling) else 0)

        if sc.attivo_col:
            col_names.append(sc.attivo_col)
            values.append(1)

        placeholders = ",".join(["?"] * len(col_names))
        sql = f"INSERT INTO {_qname('STAFF')} ({','.join(_qname(c) for c in col_names)}) VALUES ({placeholders})"
        cur.execute(sql, values)
        conn.commit()
    finally:
        try:
            conn.close()
        except Exception:
            log_swallowed('staff_repository:465')


def update_staff(
    *,
    store_code: str,
    staff_id: Optional[str],
    orig_nome_cognome: str,
    nome_cognome: str,
    ruolo: str,
    ore_contrattuali: int,
    codice_dipendente: Optional[str] = None,
    scheduling: Optional[bool] = None,
) -> bool:
    conn = get_connection(store_code)
    try:
        _ensure_staff_table(conn)
        cur = conn.cursor()
        cols = _get_table_columns_ordered(cur, "STAFF")
        sc = _guess_staff_columns(cols)

        sets = [
            f"{_qname(sc.nome_col)} = ?",
            f"{_qname(sc.ruolo_col)} = ?",
            f"{_qname(sc.ore_col)} = ?",
        ]
        params: List[Any] = [str(nome_cognome).strip(), str(ruolo).strip(), int(ore_contrattuali)]

        if sc.codice_dipendente_col is not None:
            sets.append(f"{_qname(sc.codice_dipendente_col)} = ?")
            v = (codice_dipendente or "").strip()
            params.append(v if v else None)

        if sc.scheduling_col is not None and scheduling is not None:
            sets.append(f"{_qname(sc.scheduling_col)} = ?")
            params.append(1 if bool(scheduling) else 0)

        where_sql = ""
        if staff_id and sc.id_col:
            where_sql = f" WHERE {_qname(sc.id_col)} = ?"
            try:
                params.append(int(staff_id))
            except Exception:
                params.append(staff_id)
        else:
            where_parts = [f"{_qname(sc.nome_col)} = ?"]
            params.append(str(orig_nome_cognome).strip())
            if sc.site_col:
                where_parts.append(f"{_qname(sc.site_col)} = ?")
                params.append(str(store_code))
            where_sql = f" WHERE {' AND '.join(where_parts)}"

        sql = f"UPDATE {_qname('STAFF')} SET {', '.join(sets)}{where_sql}"
        cur.execute(sql, params)
        conn.commit()
        try:
            return int(cur.rowcount or 0) > 0
        except Exception:
            return True
    finally:
        try:
            conn.close()
        except Exception:
            log_swallowed('staff_repository:528')


def set_staff_active(*, store_code: str, staff_id: Optional[str], orig_nome_cognome: str, active: bool) -> bool:
    conn = get_connection(store_code)
    try:
        _ensure_staff_table(conn)
        cur = conn.cursor()
        cols = _get_table_columns_ordered(cur, "STAFF")
        sc = _guess_staff_columns(cols)
        if not sc.attivo_col:
            return False

        params: List[Any] = [1 if active else 0]
        where_sql = ""
        if staff_id and sc.id_col:
            where_sql = f" WHERE {_qname(sc.id_col)} = ?"
            try:
                params.append(int(staff_id))
            except Exception:
                params.append(staff_id)
        else:
            where_parts = [f"{_qname(sc.nome_col)} = ?"]
            params.append(str(orig_nome_cognome).strip())
            if sc.site_col:
                where_parts.append(f"{_qname(sc.site_col)} = ?")
                params.append(str(store_code))
            where_sql = f" WHERE {' AND '.join(where_parts)}"

        sql = f"UPDATE {_qname('STAFF')} SET {_qname(sc.attivo_col)} = ?{where_sql}"
        cur.execute(sql, params)
        conn.commit()
        try:
            return int(cur.rowcount or 0) > 0
        except Exception:
            return True
    finally:
        try:
            conn.close()
        except Exception:
            log_swallowed('staff_repository:568')


def delete_staff(*, store_code: str, staff_id: Optional[str], orig_nome_cognome: str) -> bool:
    conn = get_connection(store_code)
    try:
        _ensure_staff_table(conn)
        cur = conn.cursor()
        cols = _get_table_columns_ordered(cur, "STAFF")
        sc = _guess_staff_columns(cols)

        params: List[Any] = []
        where_sql = ""
        if staff_id and sc.id_col:
            where_sql = f" WHERE {_qname(sc.id_col)} = ?"
            try:
                params.append(int(staff_id))
            except Exception:
                params.append(staff_id)
        else:
            where_parts = [f"{_qname(sc.nome_col)} = ?"]
            params.append(str(orig_nome_cognome).strip())
            if sc.site_col:
                where_parts.append(f"{_qname(sc.site_col)} = ?")
                params.append(str(store_code))
            where_sql = f" WHERE {' AND '.join(where_parts)}"

        # 1) Delete schedule rows (orari) associated to the person.
        #    Best-effort: if it fails, we still proceed with STAFF deletion.
        try:
            _delete_staff_turni_rows(conn, store_code=str(store_code), nominativo=str(orig_nome_cognome).strip())
        except Exception:
            log_swallowed('staff_repository:600')

        # 2) Delete staff record
        sql = f"DELETE FROM {_qname('STAFF')}{where_sql}"
        cur.execute(sql, params)
        conn.commit()
        try:
            return int(cur.rowcount or 0) > 0
        except Exception:
            return True
    finally:
        try:
            conn.close()
        except Exception:
            log_swallowed('staff_repository:614')
