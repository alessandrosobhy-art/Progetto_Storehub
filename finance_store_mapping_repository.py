from __future__ import annotations

from app_logging import log_swallowed
import re
from typing import Any, Dict, Iterable, List

from app_db import get_connection_sqlserver_database, get_storehub_database_name


CATALOG_TABLE = "dbo.FINANCE_STORE_CODE_CATALOG"
ASSIGN_TABLE = "dbo.FINANCE_STORE_CODE_ASSIGNMENTS"


def _conn(read_only: bool = False):
    return get_connection_sqlserver_database(get_storehub_database_name(), read_only=read_only)


def ensure_finance_store_mapping_tables() -> None:
    conn = _conn(read_only=False)
    cur = conn.cursor()
    try:
        cur.execute(
            f"""
            IF OBJECT_ID('{CATALOG_TABLE}', 'U') IS NULL
            BEGIN
              CREATE TABLE {CATALOG_TABLE} (
                CodeId INT IDENTITY(1,1) NOT NULL PRIMARY KEY,
                CodeValue NVARCHAR(50) NOT NULL,
                SourceLabel NVARCHAR(255) NULL,
                NormalizedLabel NVARCHAR(255) NULL,
                SeedSource NVARCHAR(100) NULL,
                CreatedAt DATETIME2(0) NOT NULL CONSTRAINT DF_FINANCE_STORE_CODE_CATALOG_CreatedAt DEFAULT SYSUTCDATETIME(),
                UpdatedAt DATETIME2(0) NOT NULL CONSTRAINT DF_FINANCE_STORE_CODE_CATALOG_UpdatedAt DEFAULT SYSUTCDATETIME()
              );
            END
            """
        )
        cur.execute(
            f"""
            IF NOT EXISTS (
              SELECT 1
              FROM sys.indexes
              WHERE name = 'UX_FINANCE_STORE_CODE_CATALOG_CodeValue'
                AND object_id = OBJECT_ID('{CATALOG_TABLE}')
            )
            BEGIN
              CREATE UNIQUE INDEX UX_FINANCE_STORE_CODE_CATALOG_CodeValue
              ON {CATALOG_TABLE}(CodeValue);
            END
            """
        )
        cur.execute(
            f"""
            IF OBJECT_ID('{ASSIGN_TABLE}', 'U') IS NULL
            BEGIN
              CREATE TABLE {ASSIGN_TABLE} (
                AssignmentId INT IDENTITY(1,1) NOT NULL PRIMARY KEY,
                CodeValue NVARCHAR(50) NOT NULL,
                StoreCode NVARCHAR(20) NOT NULL,
                ValidFrom DATE NOT NULL,
                Note NVARCHAR(255) NULL,
                CreatedAt DATETIME2(0) NOT NULL CONSTRAINT DF_FINANCE_STORE_CODE_ASSIGNMENTS_CreatedAt DEFAULT SYSUTCDATETIME(),
                UpdatedAt DATETIME2(0) NOT NULL CONSTRAINT DF_FINANCE_STORE_CODE_ASSIGNMENTS_UpdatedAt DEFAULT SYSUTCDATETIME()
              );
            END
            """
        )
        cur.execute(
            f"""
            IF NOT EXISTS (
              SELECT 1
              FROM sys.indexes
              WHERE name = 'UX_FINANCE_STORE_CODE_ASSIGNMENTS_CodeValue_ValidFrom'
                AND object_id = OBJECT_ID('{ASSIGN_TABLE}')
            )
            BEGIN
              CREATE UNIQUE INDEX UX_FINANCE_STORE_CODE_ASSIGNMENTS_CodeValue_ValidFrom
              ON {ASSIGN_TABLE}(CodeValue, ValidFrom);
            END
            """
        )
        cur.execute(
            f"""
            IF NOT EXISTS (
              SELECT 1
              FROM sys.indexes
              WHERE name = 'IX_FINANCE_STORE_CODE_ASSIGNMENTS_CodeValue'
                AND object_id = OBJECT_ID('{ASSIGN_TABLE}')
            )
            BEGIN
              CREATE INDEX IX_FINANCE_STORE_CODE_ASSIGNMENTS_CodeValue
              ON {ASSIGN_TABLE}(CodeValue, ValidFrom DESC);
            END
            """
        )
        conn.commit()
    finally:
        try:
            cur.close()
        except Exception:
            log_swallowed('finance_store_mapping_repository:100')
        conn.close()


def _norm_text(value: str) -> str:
    s = str(value or "").upper().strip()
    s = re.sub(r"[^A-Z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _norm_code(value: str) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def parse_code_catalog_text(text: str) -> Dict[str, Any]:
    rows: list[dict[str, str]] = []
    warnings: list[str] = []
    seen: set[str] = set()

    for idx, raw_line in enumerate(str(text or "").splitlines(), start=1):
        line = str(raw_line or "").strip()
        if not line:
            continue
        line = re.sub(r"\s+", " ", line)
        m = re.match(r"^\s*(\d{5,20})\s*[-–]\s*(.+?)\s*$", line)
        if not m:
            warnings.append(f"Riga {idx} non riconosciuta: {line}")
            continue
        code = _norm_code(m.group(1))
        label = str(m.group(2) or "").strip()
        if not code or not label:
            warnings.append(f"Riga {idx} incompleta: {line}")
            continue
        if code in seen:
            continue
        seen.add(code)
        rows.append(
            {
                "code": code,
                "label": label,
                "normalized_label": _norm_text(label),
            }
        )

    return {"rows": rows, "warnings": warnings}


def import_code_catalog_rows(rows: Iterable[Dict[str, Any]], *, seed_source: str = "manual") -> int:
    ensure_finance_store_mapping_tables()
    items = [dict(r or {}) for r in rows if str((r or {}).get("code") or "").strip()]
    if not items:
        return 0

    conn = _conn(read_only=False)
    cur = conn.cursor()
    try:
        done = 0
        for item in items:
            code = _norm_code(item.get("code"))
            label = str(item.get("label") or "").strip() or None
            normalized = str(item.get("normalized_label") or _norm_text(label or "")) or None
            cur.execute(
                f"""
                MERGE {CATALOG_TABLE} AS tgt
                USING (SELECT ? AS CodeValue, ? AS SourceLabel, ? AS NormalizedLabel, ? AS SeedSource) AS src
                   ON tgt.CodeValue = src.CodeValue
                WHEN MATCHED THEN
                  UPDATE SET
                    SourceLabel = src.SourceLabel,
                    NormalizedLabel = src.NormalizedLabel,
                    SeedSource = src.SeedSource,
                    UpdatedAt = SYSUTCDATETIME()
                WHEN NOT MATCHED THEN
                  INSERT (CodeValue, SourceLabel, NormalizedLabel, SeedSource)
                  VALUES (src.CodeValue, src.SourceLabel, src.NormalizedLabel, src.SeedSource);
                """,
                code,
                label,
                normalized,
                str(seed_source or "manual").strip() or "manual",
            )
            done += 1
        conn.commit()
        return done
    finally:
        try:
            cur.close()
        except Exception:
            log_swallowed('finance_store_mapping_repository:188')
        conn.close()


def list_code_catalog(search: str = "") -> List[Dict[str, Any]]:
    ensure_finance_store_mapping_tables()
    conn = _conn(read_only=True)
    cur = conn.cursor()
    try:
        params: list[Any] = []
        where_sql = ""
        q = str(search or "").strip()
        if q:
            q_norm = _norm_text(q)
            where_sql = " WHERE CodeValue LIKE ? OR UPPER(ISNULL(SourceLabel, '')) LIKE ? OR UPPER(ISNULL(NormalizedLabel, '')) LIKE ?"
            params.extend([f"%{_norm_code(q)}%", f"%{q.upper()}%", f"%{q_norm}%"])

        cur.execute(
            f"""
            SELECT
              c.CodeId,
              c.CodeValue,
              c.SourceLabel,
              c.NormalizedLabel,
              c.SeedSource,
              c.CreatedAt,
              c.UpdatedAt,
              ca.StoreCode AS CurrentStoreCode,
              ca.ValidFrom AS CurrentValidFrom
            FROM {CATALOG_TABLE} c
            OUTER APPLY (
              SELECT TOP 1 a.StoreCode, a.ValidFrom
              FROM {ASSIGN_TABLE} a
              WHERE a.CodeValue = c.CodeValue
              ORDER BY a.ValidFrom DESC, a.AssignmentId DESC
            ) ca
            {where_sql}
            ORDER BY c.CodeValue ASC
            """,
            params,
        )
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        try:
            cur.close()
        except Exception:
            log_swallowed('finance_store_mapping_repository:235')
        conn.close()


def list_code_assignments(code: str = "") -> List[Dict[str, Any]]:
    ensure_finance_store_mapping_tables()
    conn = _conn(read_only=True)
    cur = conn.cursor()
    try:
        params: list[Any] = []
        where_sql = ""
        code_norm = _norm_code(code)
        if code_norm:
            where_sql = " WHERE CodeValue = ?"
            params.append(code_norm)
        cur.execute(
            f"""
            SELECT AssignmentId, CodeValue, StoreCode, ValidFrom, Note, CreatedAt, UpdatedAt
            FROM {ASSIGN_TABLE}
            {where_sql}
            ORDER BY ValidFrom DESC, AssignmentId DESC
            """,
            params,
        )
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        try:
            cur.close()
        except Exception:
            log_swallowed('finance_store_mapping_repository:265')
        conn.close()


def save_code_assignment(*, code: str, store_code: str, valid_from: str, note: str = "") -> None:
    ensure_finance_store_mapping_tables()
    code_norm = _norm_code(code)
    store_code = str(store_code or "").strip()
    valid_from = str(valid_from or "").strip()
    if not code_norm or not store_code or not valid_from:
        raise ValueError("Codice, store e data decorrenza sono obbligatori.")

    conn = _conn(read_only=False)
    cur = conn.cursor()
    try:
        cur.execute(
            f"""
            MERGE {ASSIGN_TABLE} AS tgt
            USING (SELECT ? AS CodeValue, ? AS StoreCode, ? AS ValidFrom, ? AS Note) AS src
               ON tgt.CodeValue = src.CodeValue AND tgt.ValidFrom = src.ValidFrom
            WHEN MATCHED THEN
              UPDATE SET
                StoreCode = src.StoreCode,
                Note = src.Note,
                UpdatedAt = SYSUTCDATETIME()
            WHEN NOT MATCHED THEN
              INSERT (CodeValue, StoreCode, ValidFrom, Note)
              VALUES (src.CodeValue, src.StoreCode, src.ValidFrom, src.Note);
            """,
            code_norm,
            store_code,
            valid_from,
            str(note or "").strip() or None,
        )
        conn.commit()
    finally:
        try:
            cur.close()
        except Exception:
            log_swallowed('finance_store_mapping_repository:304')
        conn.close()


def update_code_assignment(*, assignment_id: int, code: str, store_code: str, valid_from: str, note: str = "") -> None:
    ensure_finance_store_mapping_tables()
    code_norm = _norm_code(code)
    store_code = str(store_code or "").strip()
    valid_from = str(valid_from or "").strip()
    if not assignment_id or not code_norm or not store_code or not valid_from:
        raise ValueError("ID, codice, store e data decorrenza sono obbligatori.")

    conn = _conn(read_only=False)
    cur = conn.cursor()
    try:
        cur.execute(
            f"""
            UPDATE {ASSIGN_TABLE}
            SET
              CodeValue = ?,
              StoreCode = ?,
              ValidFrom = ?,
              Note = ?,
              UpdatedAt = SYSUTCDATETIME()
            WHERE AssignmentId = ?
            """,
            code_norm,
            store_code,
            valid_from,
            str(note or "").strip() or None,
            int(assignment_id),
        )
        conn.commit()
    finally:
        try:
            cur.close()
        except Exception:
            log_swallowed('finance_store_mapping_repository:341')
        conn.close()


def delete_code_assignment(assignment_id: int) -> None:
    ensure_finance_store_mapping_tables()
    conn = _conn(read_only=False)
    cur = conn.cursor()
    try:
        cur.execute(f"DELETE FROM {ASSIGN_TABLE} WHERE AssignmentId = ?", int(assignment_id))
        conn.commit()
    finally:
        try:
            cur.close()
        except Exception:
            log_swallowed('finance_store_mapping_repository:356')
        conn.close()


def delete_assignments_by_code(code: str) -> None:
    ensure_finance_store_mapping_tables()
    code_norm = _norm_code(code)
    if not code_norm:
        return
    conn = _conn(read_only=False)
    cur = conn.cursor()
    try:
        cur.execute(f"DELETE FROM {ASSIGN_TABLE} WHERE CodeValue = ?", code_norm)
        conn.commit()
    finally:
        try:
            cur.close()
        except Exception:
            log_swallowed('finance_store_mapping_repository:374')
        conn.close()


def get_code_catalog_map(codes: Iterable[str]) -> Dict[str, Dict[str, Any]]:
    ensure_finance_store_mapping_tables()
    values = [_norm_code(x) for x in (codes or []) if _norm_code(x)]
    if not values:
        return {}
    conn = _conn(read_only=True)
    cur = conn.cursor()
    try:
        placeholders = ",".join("?" for _ in values)
        cur.execute(
            f"""
            SELECT CodeValue, SourceLabel, NormalizedLabel, SeedSource
            FROM {CATALOG_TABLE}
            WHERE CodeValue IN ({placeholders})
            """,
            values,
        )
        cols = [c[0] for c in cur.description]
        out: Dict[str, Dict[str, Any]] = {}
        for row in cur.fetchall():
            item = dict(zip(cols, row))
            out[str(item.get("CodeValue") or "").strip()] = item
        return out
    finally:
        try:
            cur.close()
        except Exception:
            log_swallowed('finance_store_mapping_repository:405')
        conn.close()


def get_assignments_by_code(codes: Iterable[str]) -> Dict[str, List[Dict[str, Any]]]:
    ensure_finance_store_mapping_tables()
    values = [_norm_code(x) for x in (codes or []) if _norm_code(x)]
    if not values:
        return {}
    conn = _conn(read_only=True)
    cur = conn.cursor()
    try:
        placeholders = ",".join("?" for _ in values)
        cur.execute(
            f"""
            SELECT AssignmentId, CodeValue, StoreCode, ValidFrom, Note, CreatedAt, UpdatedAt
            FROM {ASSIGN_TABLE}
            WHERE CodeValue IN ({placeholders})
            ORDER BY CodeValue ASC, ValidFrom DESC, AssignmentId DESC
            """,
            values,
        )
        cols = [c[0] for c in cur.description]
        out: Dict[str, List[Dict[str, Any]]] = {}
        for row in cur.fetchall():
            item = dict(zip(cols, row))
            code = str(item.get("CodeValue") or "").strip()
            out.setdefault(code, []).append(item)
        return out
    finally:
        try:
            cur.close()
        except Exception:
            log_swallowed('finance_store_mapping_repository:438')
        conn.close()
