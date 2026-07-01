from __future__ import annotations

from datetime import datetime
from typing import Dict, Iterable, Optional

from app_db import get_backend, get_connection


TABLE_NAME = "RENDICONTO_VERIFICHE"


def _qname(name: str) -> str:
    return f"[{str(name).replace(']', ']]')}]"


def _ensure_table() -> None:
    if get_backend() != "sqlserver":
        return
    conn = get_connection(None)
    try:
        cur = conn.cursor()
        cur.execute(
            f"""
            IF OBJECT_ID('dbo.{TABLE_NAME}', 'U') IS NULL
            BEGIN
                CREATE TABLE dbo.{TABLE_NAME} (
                    id INT IDENTITY(1,1) NOT NULL PRIMARY KEY,
                    row_uuid UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
                    record_key NVARCHAR(64) NOT NULL,
                    record_kind NVARCHAR(20) NOT NULL,
                    site NVARCHAR(20) NOT NULL,
                    verificato BIT NOT NULL DEFAULT 0,
                    nota NVARCHAR(2000) NULL,
                    created_at DATETIME2(0) NOT NULL DEFAULT SYSDATETIME(),
                    updated_at DATETIME2(0) NOT NULL DEFAULT SYSDATETIME()
                );
                CREATE UNIQUE INDEX UX_RENDICONTO_VERIFICHE_RECORD_KEY
                    ON dbo.{TABLE_NAME}(record_key);
            END
            """
        )
        conn.commit()
    finally:
        try:
            conn.close()
        except Exception:
            pass


def get_meta_map(record_keys: Iterable[str]) -> Dict[str, Dict[str, object]]:
    _ensure_table()
    keys = [str(k or "").strip() for k in (record_keys or []) if str(k or "").strip()]
    if not keys:
        return {}

    conn = get_connection(None, read_only=True)
    try:
        cur = conn.cursor()
        ph = ",".join(["?"] * len(keys))
        cur.execute(
            f"""
            SELECT record_key, verificato, nota
            FROM dbo.{TABLE_NAME}
            WHERE record_key IN ({ph})
            """,
            keys,
        )
        out: Dict[str, Dict[str, object]] = {}
        for rk, verificato, nota in (cur.fetchall() or []):
            key = str(rk or "").strip()
            if not key:
                continue
            out[key] = {
                "verificato": bool(verificato),
                "nota": str(nota or ""),
            }
        return out
    finally:
        try:
            conn.close()
        except Exception:
            pass


def save_meta(*, record_key: str, record_kind: str, site: str, verificato: bool, nota: Optional[str]) -> None:
    _ensure_table()
    rk = str(record_key or "").strip()
    if not rk:
        raise ValueError("record_key mancante.")

    kind = str(record_kind or "").strip().upper()
    if kind not in {"VERSAMENTI", "SPESE"}:
        raise ValueError("record_kind non valido.")

    site_v = str(site or "").strip()
    if not site_v:
        raise ValueError("site mancante.")

    nota_v = str(nota or "").strip()

    conn = get_connection(None)
    try:
        cur = conn.cursor()

        if (not verificato) and (not nota_v):
            cur.execute(f"DELETE FROM dbo.{TABLE_NAME} WHERE record_key = ?", rk)
            conn.commit()
            return

        cur.execute(
            f"""
            MERGE dbo.{TABLE_NAME} AS tgt
            USING (SELECT ? AS record_key, ? AS record_kind, ? AS site, ? AS verificato, ? AS nota) AS src
            ON tgt.record_key = src.record_key
            WHEN MATCHED THEN
                UPDATE SET
                    record_kind = src.record_kind,
                    site = src.site,
                    verificato = src.verificato,
                    nota = src.nota,
                    updated_at = SYSDATETIME()
            WHEN NOT MATCHED THEN
                INSERT (record_key, record_kind, site, verificato, nota)
                VALUES (src.record_key, src.record_kind, src.site, src.verificato, src.nota);
            """,
            rk,
            kind,
            site_v,
            1 if verificato else 0,
            nota_v or None,
        )
        conn.commit()
    finally:
        try:
            conn.close()
        except Exception:
            pass
