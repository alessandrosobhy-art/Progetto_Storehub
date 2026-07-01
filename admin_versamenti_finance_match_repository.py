from __future__ import annotations

import hashlib
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List

from app_db import get_connection_sqlserver_database


MATCH_TABLE = "dbo.VERSAMENTI_FINANCE_MATCH"


def _money_to_decimal(v: Any) -> Decimal:
    raw = str(v or "").strip()
    if raw and ("." in raw) and ("," in raw):
        raw = raw.replace(".", "").replace(",", ".")
    else:
        raw = raw.replace(",", ".")
    try:
        return Decimal(raw or "0")
    except Exception:
        return Decimal("0")


def _digits_only(v: str) -> str:
    return "".join(ch for ch in str(v or "") if ch.isdigit())


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
    rid = str((row or {}).get("id") or "").strip()
    site = str((row or {}).get("site") or "").strip()
    if rid:
        return f"{site}|{rid}"
    payload = "|".join(
        [
            site,
            str((row or {}).get("data_versamento_iso") or "").strip(),
            str((row or {}).get("dal_iso") or "").strip(),
            str((row or {}).get("al_iso") or "").strip(),
            str((row or {}).get("nome") or "").strip(),
            str((row or {}).get("tipo") or "").strip(),
            str((row or {}).get("tessera") or "").strip(),
            str((row or {}).get("riferimento") or "").strip(),
            str((row or {}).get("valore_key") or "").strip(),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


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
                    AppRecordKey NVARCHAR(200) NOT NULL,
                    AppRecordId NVARCHAR(100) NULL,
                    AppStore NVARCHAR(50) NULL,
                    FinanceVersamentoId INT NOT NULL,
                    SlotNo TINYINT NOT NULL,
                    MatchScore DECIMAL(5,2) NULL,
                    MatchSource NVARCHAR(20) NOT NULL CONSTRAINT DF_VERSAMENTI_FINANCE_MATCH_Source DEFAULT ('manual'),
                    CreatedAt DATETIME2 NOT NULL CONSTRAINT DF_VERSAMENTI_FINANCE_MATCH_Created DEFAULT (SYSDATETIME()),
                    UpdatedAt DATETIME2 NOT NULL CONSTRAINT DF_VERSAMENTI_FINANCE_MATCH_Updated DEFAULT (SYSDATETIME())
                );
                CREATE UNIQUE INDEX UX_VERSAMENTI_FINANCE_MATCH_AppSlot ON {MATCH_TABLE}(AppRecordKey, SlotNo);
                CREATE UNIQUE INDEX UX_VERSAMENTI_FINANCE_MATCH_FinanceId ON {MATCH_TABLE}(FinanceVersamentoId);
                CREATE INDEX IX_VERSAMENTI_FINANCE_MATCH_AppKey ON {MATCH_TABLE}(AppRecordKey);
            END
            """
        )
        conn.commit()
    finally:
        try:
            cur.close()
        except Exception:
            pass
        conn.close()


def list_finance_versamenti(*, start_iso: str = "", end_iso: str = "", assignment_filter: str = "all") -> List[Dict[str, Any]]:
    ensure_match_table()
    conn = get_connection_sqlserver_database("ILP_FINANCE", read_only=True)
    app_conn = get_connection_sqlserver_database("APP_STOREHUB", read_only=True)
    try:
        app_cur = app_conn.cursor()
        app_cur.execute(
            f"""
            SELECT FinanceVersamentoId, AppRecordKey, SlotNo
            FROM {MATCH_TABLE}
            """
        )
        assigned_map = {int(r[0]): {"app_record_key": str(r[1] or ""), "slot_no": int(r[2] or 0)} for r in app_cur.fetchall()}

        cur = conn.cursor()
        cur.execute(
            """
            SELECT
                Id,
                DataContabile,
                DataValuta,
                Societa,
                Banca,
                Importo,
                TipoVersamento,
                NotaVers,
                NumeroTessera,
                NumeroATM,
                Orario,
                DataInserimento
            FROM dbo.[Versamenti]
            ORDER BY Id DESC
            """
        )
        out: List[Dict[str, Any]] = []
        for row in cur.fetchall():
            rec = {
                "id": int(row[0]),
                "data_contabile_raw": str(row[1] or "").strip(),
                "data_valuta_raw": str(row[2] or "").strip(),
                "societa": str(row[3] or "").strip(),
                "banca": str(row[4] or "").strip(),
                "importo": float(row[5] or 0),
                "importo_key": str(Decimal(str(row[5] or 0))),
                "tipo_versamento": str(row[6] or "").strip(),
                "nota": str(row[7] or "").strip(),
                "numero_tessera": _digits_only(str(row[8] or "")),
                "numero_atm": str(row[9] or "").strip(),
                "orario": str(row[10] or "").strip(),
                "data_inserimento": row[11],
            }
            rec["data_contabile_iso"] = _parse_finance_date(rec["data_contabile_raw"])
            rec["data_valuta_iso"] = _parse_finance_date(rec["data_valuta_raw"])
            assigned = assigned_map.get(rec["id"])
            rec["assigned"] = bool(assigned)
            rec["assigned_app_record_key"] = (assigned or {}).get("app_record_key", "")
            rec["assigned_slot_no"] = (assigned or {}).get("slot_no", 0)

            if start_iso:
                chk = rec["data_contabile_iso"] or rec["data_valuta_iso"]
                if not chk or chk < start_iso:
                    continue
            if end_iso:
                chk = rec["data_contabile_iso"] or rec["data_valuta_iso"]
                if not chk or chk > end_iso:
                    continue

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
            pass
        try:
            app_cur.close()
        except Exception:
            pass
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
            SELECT AppRecordKey, FinanceVersamentoId, SlotNo, MatchScore, MatchSource
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
                    "finance_id": int(row[1]),
                    "slot_no": int(row[2] or 0),
                    "match_score": float(row[3] or 0),
                    "match_source": str(row[4] or "").strip(),
                }
            )
        return out
    finally:
        try:
            cur.close()
        except Exception:
            pass
        conn.close()


def replace_app_matches(
    *,
    app_record_key: str,
    app_record_id: str,
    app_store: str,
    items: List[Dict[str, Any]],
) -> None:
    ensure_match_table()
    key = str(app_record_key or "").strip()
    if not key:
        raise ValueError("app_record_key mancante")

    clean_items: List[Dict[str, Any]] = []
    seen_finance: set[int] = set()
    for raw in items or []:
        try:
            finance_id = int(raw.get("finance_id") or 0)
        except Exception:
            finance_id = 0
        if not finance_id or finance_id in seen_finance:
            continue
        seen_finance.add(finance_id)
        try:
            slot_no = int(raw.get("slot_no") or 0)
        except Exception:
            slot_no = 0
        if slot_no not in (1, 2, 3):
            continue
        clean_items.append(
            {
                "finance_id": finance_id,
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
            cur.execute(f"DELETE FROM {MATCH_TABLE} WHERE FinanceVersamentoId IN ({ph})", [x["finance_id"] for x in clean_items])
            for item in clean_items:
                cur.execute(
                    f"""
                    INSERT INTO {MATCH_TABLE}
                    (AppRecordKey, AppRecordId, AppStore, FinanceVersamentoId, SlotNo, MatchScore, MatchSource)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    key,
                    str(app_record_id or "").strip() or None,
                    str(app_store or "").strip() or None,
                    int(item["finance_id"]),
                    int(item["slot_no"]),
                    float(item["match_score"] or 0),
                    str(item["match_source"] or "manual"),
                )
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        try:
            cur.close()
        except Exception:
            pass
        conn.close()
