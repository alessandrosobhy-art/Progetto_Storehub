from __future__ import annotations

from app_logging import log_swallowed
from datetime import datetime
from typing import Any, Dict, List

from app_db import get_connection_sqlserver_database


MATCH_TABLE = "dbo.POS_FINANCE_MATCH"


def _parse_finance_date(v: Any) -> str:
    s = str(v or "").strip()
    if not s:
        return ""
    for fmt in ("%d/%m/%y", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except Exception:
            continue
    return ""


def build_app_record_key(row: Dict[str, Any]) -> str:
    site = str((row or {}).get("site") or "").strip()
    date_iso = str((row or {}).get("date_iso") or "").strip()
    return f"{site}|{date_iso}|POS"


def build_finance_row_uid(row: Dict[str, Any]) -> str:
    source = str((row or {}).get("source_table") or "").strip().lower()
    rid = int((row or {}).get("id") or 0)
    return f"{source}:{rid}" if source and rid else ""


def ensure_match_table() -> None:
    conn = get_connection_sqlserver_database("APP_STOREHUB", read_only=False)
    try:
        cur = conn.cursor()
        cur.execute(
            f"""
            IF OBJECT_ID('{MATCH_TABLE}', 'U') IS NULL
            BEGIN
                CREATE TABLE {MATCH_TABLE}(
                    MatchId INT IDENTITY(1,1) NOT NULL PRIMARY KEY,
                    AppRecordKey NVARCHAR(120) NOT NULL,
                    AppStore NVARCHAR(50) NULL,
                    AppDate DATE NULL,
                    FinanceRowUid NVARCHAR(80) NOT NULL,
                    FinanceSourceTable NVARCHAR(30) NOT NULL,
                    FinanceRowId INT NOT NULL,
                    SlotNo TINYINT NOT NULL,
                    MatchScore DECIMAL(5,2) NULL,
                    MatchSource NVARCHAR(20) NOT NULL CONSTRAINT DF_POS_FINANCE_MATCH_Source DEFAULT ('manual'),
                    CreatedAt DATETIME2 NOT NULL CONSTRAINT DF_POS_FINANCE_MATCH_Created DEFAULT (SYSDATETIME()),
                    UpdatedAt DATETIME2 NOT NULL CONSTRAINT DF_POS_FINANCE_MATCH_Updated DEFAULT (SYSDATETIME())
                );
                CREATE UNIQUE INDEX UX_POS_FINANCE_MATCH_AppSlot ON {MATCH_TABLE}(AppRecordKey, SlotNo);
                CREATE UNIQUE INDEX UX_POS_FINANCE_MATCH_FinanceUid ON {MATCH_TABLE}(FinanceRowUid);
                CREATE INDEX IX_POS_FINANCE_MATCH_AppKey ON {MATCH_TABLE}(AppRecordKey);
            END
            """
        )
        conn.commit()
    finally:
        try:
            cur.close()
        except Exception:
            log_swallowed('admin_pos_finance_match_repository:69')
        conn.close()


def _pos_like_where(alias_prefix: str = "") -> str:
    p = f"{alias_prefix}." if alias_prefix else ""
    checks = [
        f"UPPER(ISNULL({p}[Categoria], '')) LIKE '%POS%'",
        f"UPPER(ISNULL({p}[Descrizione], '')) LIKE '%POS%'",
        f"UPPER(ISNULL({p}[Note], '')) LIKE '%POS%'",
        f"UPPER(ISNULL({p}[Descrizione], '')) LIKE '%NUMIA%'",
        f"UPPER(ISNULL({p}[Note], '')) LIKE '%NUMIA%'",
        f"UPPER(ISNULL({p}[Descrizione], '')) LIKE '%AMEX%'",
        f"UPPER(ISNULL({p}[Note], '')) LIKE '%AMEX%'",
        f"UPPER(ISNULL({p}[Descrizione], '')) LIKE '%AMERICAN EXPRESS%'",
        f"UPPER(ISNULL({p}[Note], '')) LIKE '%AMERICAN EXPRESS%'",
        f"UPPER(ISNULL({p}[Descrizione], '')) LIKE '%NEXI%'",
        f"UPPER(ISNULL({p}[Note], '')) LIKE '%NEXI%'",
        f"UPPER(ISNULL({p}[Descrizione], '')) LIKE '%AXERVE%'",
        f"UPPER(ISNULL({p}[Note], '')) LIKE '%AXERVE%'",
        f"UPPER(ISNULL({p}[Descrizione], '')) LIKE '%WORLDLINE%'",
        f"UPPER(ISNULL({p}[Note], '')) LIKE '%WORLDLINE%'",
    ]
    return "(" + " OR ".join(checks) + ")"


def list_finance_pos_rows(*, start_iso: str = "", end_iso: str = "", assignment_filter: str = "all") -> List[Dict[str, Any]]:
    ensure_match_table()
    conn = get_connection_sqlserver_database("ILP_FINANCE", read_only=True)
    app_conn = get_connection_sqlserver_database("APP_STOREHUB", read_only=True)
    try:
        app_cur = app_conn.cursor()
        app_cur.execute(
            f"""
            SELECT FinanceRowUid, AppRecordKey, SlotNo
            FROM {MATCH_TABLE}
            """
        )
        assigned_map = {
            str(r[0] or ""): {"app_record_key": str(r[1] or ""), "slot_no": int(r[2] or 0)}
            for r in app_cur.fetchall()
        }

        params: List[Any] = []
        date_filters: List[str] = []
        if start_iso:
            date_filters.append(
                "(TRY_CONVERT(date, NULLIF(DataContabile, ''), 3) >= ? OR TRY_CONVERT(date, NULLIF(DataValuta, ''), 3) >= ?)"
            )
            params.extend([start_iso, start_iso])
        if end_iso:
            date_filters.append(
                "(TRY_CONVERT(date, NULLIF(DataContabile, ''), 3) <= ? OR TRY_CONVERT(date, NULLIF(DataValuta, ''), 3) <= ?)"
            )
            params.extend([end_iso, end_iso])
        where_tail = ""
        if date_filters:
            where_tail = " AND " + " AND ".join(date_filters)

        sql = f"""
        SELECT
            src.SourceTable,
            src.Id,
            src.DataContabile,
            src.DataValuta,
            src.Societa,
            src.Banca,
            src.Importo,
            src.Categoria,
            src.Tipo,
            src.Store,
            src.Descrizione,
            src.Note,
            src.DataInserimento
        FROM (
            SELECT
                'transazioni' AS SourceTable,
                Id,
                DataContabile,
                DataValuta,
                Societa,
                Banca,
                Importo,
                Categoria,
                Tipo,
                Store,
                Descrizione,
                Note,
                DataInserimento
            FROM dbo.Transazioni
            WHERE ISNULL(Importo, 0) > 0
              AND {_pos_like_where()}
        ) src
        WHERE 1=1
        {where_tail}
        ORDER BY TRY_CONVERT(datetime, src.DataInserimento) DESC, src.SourceTable, src.Id DESC
        """

        cur = conn.cursor()
        cur.execute(sql, params)
        out: List[Dict[str, Any]] = []
        for row in cur.fetchall():
            rec = {
                "source_table": str(row[0] or "").strip().lower(),
                "id": int(row[1] or 0),
                "data_contabile_raw": str(row[2] or "").strip(),
                "data_valuta_raw": str(row[3] or "").strip(),
                "societa": str(row[4] or "").strip(),
                "banca": str(row[5] or "").strip(),
                "importo": float(row[6] or 0),
                "categoria": str(row[7] or "").strip(),
                "tipo": str(row[8] or "").strip(),
                "store": str(row[9] or "").strip(),
                "descrizione": str(row[10] or "").strip(),
                "nota": str(row[11] or "").strip(),
                "data_inserimento": row[12],
            }
            rec["finance_row_uid"] = build_finance_row_uid(rec)
            rec["data_contabile_iso"] = _parse_finance_date(rec["data_contabile_raw"])
            rec["data_valuta_iso"] = _parse_finance_date(rec["data_valuta_raw"])
            assigned = assigned_map.get(rec["finance_row_uid"])
            rec["assigned"] = bool(assigned)
            rec["assigned_app_record_key"] = (assigned or {}).get("app_record_key", "")
            rec["assigned_slot_no"] = (assigned or {}).get("slot_no", 0)

            af = str(assignment_filter or "all").strip().lower()
            if af == "assigned" and not rec["assigned"]:
                continue
            if af == "unassigned" and rec["assigned"]:
                continue
            out.append(rec)
        return out
    finally:
        try:
            cur.close()
        except Exception:
            log_swallowed('admin_pos_finance_match_repository:205')
        try:
            app_cur.close()
        except Exception:
            log_swallowed('admin_pos_finance_match_repository:209')
        conn.close()
        app_conn.close()


def get_matches_for_app_keys(app_record_keys: List[str]) -> Dict[str, List[Dict[str, Any]]]:
    keys = [str(k or "").strip() for k in (app_record_keys or []) if str(k or "").strip()]
    if not keys:
        return {}
    ensure_match_table()
    conn = get_connection_sqlserver_database("APP_STOREHUB", read_only=True)
    try:
        cur = conn.cursor()
        ph = ",".join("?" for _ in keys)
        cur.execute(
            f"""
            SELECT AppRecordKey, FinanceRowUid, FinanceSourceTable, FinanceRowId, SlotNo, MatchScore, MatchSource
            FROM {MATCH_TABLE}
            WHERE AppRecordKey IN ({ph})
            ORDER BY AppRecordKey, SlotNo
            """,
            keys,
        )
        out: Dict[str, List[Dict[str, Any]]] = {}
        for row in cur.fetchall():
            k = str(row[0] or "")
            out.setdefault(k, []).append(
                {
                    "finance_row_uid": str(row[1] or ""),
                    "finance_source_table": str(row[2] or ""),
                    "finance_row_id": int(row[3] or 0),
                    "slot_no": int(row[4] or 0),
                    "match_score": float(row[5] or 0),
                    "match_source": str(row[6] or "").strip(),
                }
            )
        return out
    finally:
        try:
            cur.close()
        except Exception:
            log_swallowed('admin_pos_finance_match_repository:250')
        conn.close()


def replace_app_matches(*, app_record_key: str, app_store: str, app_date: str, items: List[Dict[str, Any]]) -> None:
    ensure_match_table()
    key = str(app_record_key or "").strip()
    if not key:
        raise ValueError("app_record_key mancante")

    clean_items: List[Dict[str, Any]] = []
    seen_uids: set[str] = set()
    for raw in items or []:
        fin_uid = str(raw.get("finance_row_uid") or "").strip().lower()
        fin_source = str(raw.get("finance_source_table") or "").strip().lower()
        try:
            fin_id = int(raw.get("finance_row_id") or 0)
        except Exception:
            fin_id = 0
        if not fin_uid or not fin_source or not fin_id or fin_uid in seen_uids:
            continue
        seen_uids.add(fin_uid)
        try:
            slot_no = int(raw.get("slot_no") or 0)
        except Exception:
            slot_no = 0
        if slot_no not in (1, 2, 3):
            continue
        clean_items.append(
            {
                "finance_row_uid": fin_uid,
                "finance_source_table": fin_source,
                "finance_row_id": fin_id,
                "slot_no": slot_no,
                "match_score": float(raw.get("match_score") or 0),
                "match_source": str(raw.get("match_source") or "manual").strip() or "manual",
            }
        )

    conn = get_connection_sqlserver_database("APP_STOREHUB", read_only=False)
    try:
        cur = conn.cursor()
        cur.execute(f"DELETE FROM {MATCH_TABLE} WHERE AppRecordKey = ?", key)
        if clean_items:
            ph = ",".join("?" for _ in clean_items)
            cur.execute(f"DELETE FROM {MATCH_TABLE} WHERE FinanceRowUid IN ({ph})", [x["finance_row_uid"] for x in clean_items])
            for item in clean_items:
                cur.execute(
                    f"""
                    INSERT INTO {MATCH_TABLE}
                    (AppRecordKey, AppStore, AppDate, FinanceRowUid, FinanceSourceTable, FinanceRowId, SlotNo, MatchScore, MatchSource)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    key,
                    str(app_store or "").strip() or None,
                    str(app_date or "").strip() or None,
                    item["finance_row_uid"],
                    item["finance_source_table"],
                    item["finance_row_id"],
                    int(item["slot_no"]),
                    float(item["match_score"] or 0),
                    str(item["match_source"] or "manual"),
                )
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            log_swallowed('admin_pos_finance_match_repository:318')
        raise
    finally:
        try:
            cur.close()
        except Exception:
            log_swallowed('admin_pos_finance_match_repository:324')
        conn.close()
