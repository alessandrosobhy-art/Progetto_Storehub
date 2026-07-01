from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

from app_db import get_connection_sqlserver_database, get_storehub_database_name


TABLE_LISTINI = "ListiniPrezzi"
TABLE_LISTINI_ELENCHI = "ListiniElenchi"
TABLE_LISTINI_STORES = "ListiniStore"
TABLE_TIPI = "ListinoTipi"
TABLE_GRUPPI = "ListinoGruppi"
TABLE_FORNITORI = "Fornitori"
TABLE_FORNITORI_CONTATTI = "FornitoriContatti"
TABLE_FORNITORI_CONTATTI_STORES = "FornitoriContattiStores"


def _conn(read_only: bool = False):
    return get_connection_sqlserver_database(get_storehub_database_name(), read_only=read_only)


def _normalize_decimal_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    text = str(value or "").strip()
    if not text:
        return None
    text = text.replace("\u00a0", "").replace(" ", "")
    if "," in text and "." in text:
        # Formato italiano con migliaia: 1.234,56
        text = text.replace(".", "").replace(",", ".")
    elif "," in text:
        text = text.replace(",", ".")
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return value


def _normalize_int_value(value: Any) -> Any:
    dec = _normalize_decimal_value(value)
    if dec is None:
        return None
    if isinstance(dec, Decimal):
        try:
            return int(dec)
        except Exception:
            return dec
    return dec


def _normalize_pricelist_row_numbers(row: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(row or {})
    for key in ("PREZZO", "CONV"):
        if key in out:
            out[key] = _normalize_decimal_value(out.get(key))
    for key in ("QTACAR", "QTAINT"):
        if key in out:
            out[key] = _normalize_int_value(out.get(key))
    return out


def ensure_supplier_orders_schema() -> None:
    conn = _conn(False)
    try:
        cur = conn.cursor()
        cur.execute(
            """
IF OBJECT_ID('dbo.ListinoTipi','U') IS NULL
BEGIN
  CREATE TABLE dbo.ListinoTipi (
    row_uuid UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID() PRIMARY KEY,
    tipo_listino NVARCHAR(100) NOT NULL,
    sort_order INT NOT NULL DEFAULT 0,
    is_active BIT NOT NULL DEFAULT 1,
    created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    updated_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
  );
  CREATE UNIQUE INDEX UX_ListinoTipi_tipo_listino ON dbo.ListinoTipi(tipo_listino);
END

IF OBJECT_ID('dbo.ListinoGruppi','U') IS NULL
BEGIN
  CREATE TABLE dbo.ListinoGruppi (
    row_uuid UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID() PRIMARY KEY,
    gruppo NVARCHAR(100) NOT NULL,
    sort_order INT NOT NULL DEFAULT 0,
    is_active BIT NOT NULL DEFAULT 1,
    created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    updated_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
  );
  CREATE UNIQUE INDEX UX_ListinoGruppi_gruppo ON dbo.ListinoGruppi(gruppo);
END

IF OBJECT_ID('dbo.ListiniPrezzi','U') IS NULL
BEGIN
  CREATE TABLE dbo.ListiniPrezzi (
    row_uuid UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID() PRIMARY KEY,
    tipo_listino NVARCHAR(100) NOT NULL,
    Descrizione NVARCHAR(255) NOT NULL,
    GRUPPO NVARCHAR(100) NULL,
    FORNITORE NVARCHAR(255) NOT NULL,
    CODICE NVARCHAR(100) NULL,
    PREZZO DECIMAL(18,4) NULL,
    UNITA NVARCHAR(50) NULL,
    QTACAR INT NULL,
    QTAINT INT NULL,
    CONV DECIMAL(18,4) NULL,
    created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    updated_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
  );
  CREATE UNIQUE INDEX UX_ListiniPrezzi_tipo_forn_descr
    ON dbo.ListiniPrezzi(tipo_listino, FORNITORE, Descrizione);
  CREATE INDEX IX_ListiniPrezzi_tipo_fornitore
    ON dbo.ListiniPrezzi(tipo_listino, FORNITORE);
END

IF OBJECT_ID('dbo.Fornitori','U') IS NULL
BEGIN
  CREATE TABLE dbo.Fornitori (
    row_uuid UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID() PRIMARY KEY,
    Fornitore NVARCHAR(255) NOT NULL,
    Referente NVARCHAR(255) NULL,
    Email NVARCHAR(255) NULL,
    Telefono1 NVARCHAR(100) NULL,
    Telefono2 NVARCHAR(100) NULL,
    TipoOrdine NVARCHAR(20) NULL,
    is_active BIT NOT NULL DEFAULT 1,
    created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    updated_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
  );
  CREATE UNIQUE INDEX UX_Fornitori_Fornitore ON dbo.Fornitori(Fornitore);
END

IF COL_LENGTH('dbo.Fornitori', 'TipoOrdine') IS NULL
BEGIN
  ALTER TABLE dbo.Fornitori ADD TipoOrdine NVARCHAR(20) NULL;
END
IF COL_LENGTH('dbo.Fornitori', 'is_active') IS NULL
BEGIN
  ALTER TABLE dbo.Fornitori ADD is_active BIT NOT NULL DEFAULT 1;
END
IF COL_LENGTH('dbo.Fornitori', 'created_at') IS NULL
BEGIN
  ALTER TABLE dbo.Fornitori ADD created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME();
END
IF COL_LENGTH('dbo.Fornitori', 'updated_at') IS NULL
BEGIN
  ALTER TABLE dbo.Fornitori ADD updated_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME();
END

IF OBJECT_ID('dbo.FornitoriContatti','U') IS NULL
BEGIN
  CREATE TABLE dbo.FornitoriContatti (
    row_uuid UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID() PRIMARY KEY,
    fornitore_row_uuid UNIQUEIDENTIFIER NOT NULL,
    Referente NVARCHAR(255) NULL,
    Email NVARCHAR(255) NULL,
    Telefono1 NVARCHAR(100) NULL,
    Telefono2 NVARCHAR(100) NULL,
    TipoOrdine NVARCHAR(20) NULL,
    sort_order INT NOT NULL DEFAULT 0,
    created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    updated_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
  );
  CREATE INDEX IX_FornitoriContatti_fornitore ON dbo.FornitoriContatti(fornitore_row_uuid, sort_order, created_at);
END

IF OBJECT_ID('dbo.FornitoriContattiStores','U') IS NULL
BEGIN
  CREATE TABLE dbo.FornitoriContattiStores (
    row_uuid UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID() PRIMARY KEY,
    contatto_row_uuid UNIQUEIDENTIFIER NOT NULL,
    store_code NVARCHAR(50) NOT NULL,
    created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
  );
  CREATE UNIQUE INDEX UX_FornitoriContattiStores_pair ON dbo.FornitoriContattiStores(contatto_row_uuid, store_code);
  CREATE INDEX IX_FornitoriContattiStores_store ON dbo.FornitoriContattiStores(store_code);
END
"""
        )
        conn.commit()
    finally:
        try:
            conn.close()
        except Exception:
            pass
    migrate_legacy_fornitori_contacts()


def ensure_price_lists_schema() -> None:
    ensure_supplier_orders_schema()
    conn = _conn(False)
    try:
        cur = conn.cursor()
        cur.execute(
            """
IF OBJECT_ID('dbo.ListiniElenchi','U') IS NULL
BEGIN
  CREATE TABLE dbo.ListiniElenchi (
    row_uuid UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID() PRIMARY KEY,
    nome NVARCHAR(255) NOT NULL,
    is_default BIT NOT NULL DEFAULT 0,
    is_active BIT NOT NULL DEFAULT 1,
    created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    updated_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
  );
  CREATE UNIQUE INDEX UX_ListiniElenchi_nome ON dbo.ListiniElenchi(nome);
END

IF OBJECT_ID('dbo.ListiniStore','U') IS NULL
BEGIN
  CREATE TABLE dbo.ListiniStore (
    row_uuid UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID() PRIMARY KEY,
    listino_uuid UNIQUEIDENTIFIER NOT NULL,
    store_code NVARCHAR(50) NOT NULL,
    created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    updated_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
  );
  CREATE UNIQUE INDEX UX_ListiniStore_store ON dbo.ListiniStore(store_code);
  CREATE INDEX IX_ListiniStore_listino ON dbo.ListiniStore(listino_uuid);
END
"""
        )
        cur.execute(
            """

IF NOT EXISTS (SELECT 1 FROM dbo.ListiniElenchi WHERE nome = 'Listino DOS')
  INSERT INTO dbo.ListiniElenchi (nome, is_default, is_active) VALUES ('Listino DOS', 1, 1);

UPDATE dbo.ListiniElenchi
SET is_default = CASE WHEN nome = 'Listino DOS' THEN 1 ELSE 0 END,
    is_active = CASE WHEN nome = 'Listino DOS' THEN 1 ELSE is_active END,
    updated_at = SYSUTCDATETIME()
WHERE nome = 'Listino DOS' OR is_default = 1;

IF COL_LENGTH('dbo.ListiniPrezzi', 'listino_uuid') IS NULL
BEGIN
  ALTER TABLE dbo.ListiniPrezzi ADD listino_uuid UNIQUEIDENTIFIER NULL;
END
"""
        )
        cur.execute(
            """

UPDATE dbo.ListiniPrezzi
SET listino_uuid = (SELECT TOP 1 row_uuid FROM dbo.ListiniElenchi WHERE nome = 'Listino DOS')
WHERE listino_uuid IS NULL;
"""
        )
        cur.execute(
            """

IF EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'UX_ListiniPrezzi_tipo_forn_descr' AND object_id = OBJECT_ID('dbo.ListiniPrezzi'))
BEGIN
  DROP INDEX UX_ListiniPrezzi_tipo_forn_descr ON dbo.ListiniPrezzi;
END

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_ListiniPrezzi_listino_tipo_forn_descr' AND object_id = OBJECT_ID('dbo.ListiniPrezzi'))
BEGIN
  CREATE INDEX IX_ListiniPrezzi_listino_tipo_forn_descr
    ON dbo.ListiniPrezzi(listino_uuid, tipo_listino, FORNITORE, Descrizione);
END
"""
        )
        conn.commit()
    finally:
        try:
            conn.close()
        except Exception:
            pass


def migrate_legacy_fornitori_contacts() -> int:
    conn = _conn(False)
    created = 0
    try:
        cur = conn.cursor()
        cur.execute(
            """
SELECT row_uuid, Fornitore, Referente, Email, Telefono1, Telefono2, ISNULL(TipoOrdine, 'Mail')
FROM dbo.Fornitori
WHERE
  COALESCE(NULLIF(LTRIM(RTRIM(Referente)), ''), NULLIF(LTRIM(RTRIM(Email)), ''), NULLIF(LTRIM(RTRIM(Telefono1)), ''), NULLIF(LTRIM(RTRIM(Telefono2)), '')) IS NOT NULL
"""
        )
        rows = cur.fetchall()
        for r in rows:
            fornitore_row_uuid = str(r[0])
            cur.execute("SELECT COUNT(*) FROM dbo.FornitoriContatti WHERE fornitore_row_uuid=?", (fornitore_row_uuid,))
            if int(cur.fetchone()[0] or 0) > 0:
                continue
            cur.execute(
                """
INSERT INTO dbo.FornitoriContatti (fornitore_row_uuid, Referente, Email, Telefono1, Telefono2, TipoOrdine, sort_order)
VALUES (?, ?, ?, ?, ?, ?, 0)
""",
                (fornitore_row_uuid, r[2], r[3], r[4], r[5], r[6] or "Mail"),
            )
            created += 1
        conn.commit()
        return created
    finally:
        try:
            conn.close()
        except Exception:
            pass


def migrate_legacy_pricelists() -> Dict[str, int]:
    ensure_price_lists_schema()
    conn = _conn(False)
    out = {"FoodPaper": 0, "Operating": 0}
    try:
        cur = conn.cursor()
        cur.execute(
            """
SELECT
  CASE WHEN OBJECT_ID('dbo.FoodPaper', 'U') IS NOT NULL THEN 1 ELSE 0 END AS has_foodpaper,
  CASE WHEN OBJECT_ID('dbo.Operating', 'U') IS NOT NULL THEN 1 ELSE 0 END AS has_operating,
  CASE WHEN OBJECT_ID('dbo.[FP CONV]', 'U') IS NOT NULL THEN 1 ELSE 0 END AS has_fp_conv
"""
        )
        flags = cur.fetchone()
        has_foodpaper = bool(flags[0]) if flags else False
        has_operating = bool(flags[1]) if flags else False
        has_fp_conv = bool(flags[2]) if flags else False

        if not has_foodpaper and not has_operating:
            return out

        if has_foodpaper:
            cur.execute("SELECT COUNT(*) FROM dbo.[FoodPaper]")
            out["FoodPaper"] = int(cur.fetchone()[0] or 0)
        if has_operating:
            cur.execute("SELECT COUNT(*) FROM dbo.[Operating]")
            out["Operating"] = int(cur.fetchone()[0] or 0)

        if has_foodpaper:
            conv_join_sql = """
    LEFT JOIN dbo.[FP CONV] conv
      ON LTRIM(RTRIM(conv.FORNITORE)) = LTRIM(RTRIM(fp.FORNITORE))
     AND LTRIM(RTRIM(conv.DESCRIZIONE)) = LTRIM(RTRIM(fp.Descrizione))
"""
            conv_value_sql = "MAX(conv.CONV) AS CONV" if has_fp_conv else "CAST(NULL AS DECIMAL(18,4)) AS CONV"
            cur.execute(
                f"""
MERGE dbo.ListiniPrezzi AS tgt
USING (
  SELECT
    (SELECT TOP 1 row_uuid FROM dbo.ListiniElenchi WHERE nome = 'Listino DOS') AS listino_uuid,
    'FoodPaper' AS tipo_listino,
    src.Descrizione,
    src.GRUPPO,
    src.FORNITORE,
    src.CODICE,
    src.PREZZO,
    src.UNITA,
    src.QTACAR,
    src.QTAINT,
    src.CONV
  FROM (
    SELECT
      fp.Descrizione,
      MAX(fp.GRUPPO) AS GRUPPO,
      fp.FORNITORE,
      MAX(fp.CODICE) AS CODICE,
      MAX(fp.PREZZO) AS PREZZO,
      MAX(fp.UNITA) AS UNITA,
      MAX(fp.QTACAR) AS QTACAR,
      MAX(fp.QTAINT) AS QTAINT,
      {conv_value_sql}
    FROM dbo.[FoodPaper] fp
    {conv_join_sql if has_fp_conv else ""}
    GROUP BY fp.FORNITORE, fp.Descrizione
  ) src
) AS src
ON tgt.listino_uuid = src.listino_uuid
 AND tgt.tipo_listino = src.tipo_listino
 AND LTRIM(RTRIM(tgt.FORNITORE)) = LTRIM(RTRIM(src.FORNITORE))
 AND LTRIM(RTRIM(tgt.Descrizione)) = LTRIM(RTRIM(src.Descrizione))
WHEN MATCHED THEN
  UPDATE SET
    GRUPPO = src.GRUPPO,
    CODICE = src.CODICE,
    PREZZO = src.PREZZO,
    UNITA = src.UNITA,
    QTACAR = src.QTACAR,
    QTAINT = src.QTAINT,
    CONV = src.CONV,
    updated_at = SYSUTCDATETIME()
WHEN NOT MATCHED THEN
  INSERT (listino_uuid, tipo_listino, FORNITORE, Descrizione, GRUPPO, CODICE, PREZZO, UNITA, QTACAR, QTAINT, CONV)
  VALUES (src.listino_uuid, src.tipo_listino, src.FORNITORE, src.Descrizione, src.GRUPPO, src.CODICE, src.PREZZO, src.UNITA, src.QTACAR, src.QTAINT, src.CONV);
"""
            )
        if has_operating:
            cur.execute(
                """
MERGE dbo.ListiniPrezzi AS tgt
USING (
  SELECT
    (SELECT TOP 1 row_uuid FROM dbo.ListiniElenchi WHERE nome = 'Listino DOS') AS listino_uuid,
    'Operating' AS tipo_listino,
    src.Descrizione,
    src.GRUPPO,
    src.FORNITORE,
    src.CODICE,
    src.PREZZO,
    src.UNITA,
    src.QTACAR,
    src.QTAINT,
    src.CONV
  FROM (
    SELECT
      op.Descrizione,
      MAX(op.GRUPPO) AS GRUPPO,
      op.FORNITORE,
      MAX(op.CODICE) AS CODICE,
      MAX(op.PREZZO) AS PREZZO,
      MAX(op.UNITA) AS UNITA,
      MAX(op.QTACAR) AS QTACAR,
      MAX(op.QTAINT) AS QTAINT,
      CAST(NULL AS DECIMAL(18,4)) AS CONV
    FROM dbo.[Operating] op
    GROUP BY op.FORNITORE, op.Descrizione
  ) src
) AS src
ON tgt.listino_uuid = src.listino_uuid
 AND tgt.tipo_listino = src.tipo_listino
 AND LTRIM(RTRIM(tgt.FORNITORE)) = LTRIM(RTRIM(src.FORNITORE))
 AND LTRIM(RTRIM(tgt.Descrizione)) = LTRIM(RTRIM(src.Descrizione))
WHEN MATCHED THEN
  UPDATE SET
    GRUPPO = src.GRUPPO,
    CODICE = src.CODICE,
    PREZZO = src.PREZZO,
    UNITA = src.UNITA,
    QTACAR = src.QTACAR,
    QTAINT = src.QTAINT,
    CONV = src.CONV,
    updated_at = SYSUTCDATETIME()
WHEN NOT MATCHED THEN
  INSERT (listino_uuid, tipo_listino, FORNITORE, Descrizione, GRUPPO, CODICE, PREZZO, UNITA, QTACAR, QTAINT, CONV)
  VALUES (src.listino_uuid, src.tipo_listino, src.FORNITORE, src.Descrizione, src.GRUPPO, src.CODICE, src.PREZZO, src.UNITA, src.QTACAR, src.QTAINT, src.CONV);
"""
            )
        conn.commit()
        return out
    finally:
        try:
            conn.close()
        except Exception:
            pass


def list_listino_types(include_inactive: bool = False) -> List[Dict[str, Any]]:
    ensure_supplier_orders_schema()
    conn = _conn(True)
    try:
        cur = conn.cursor()
        sql = "SELECT row_uuid, tipo_listino, sort_order, is_active FROM dbo.ListinoTipi"
        if not include_inactive:
            sql += " WHERE is_active = 1"
        sql += " ORDER BY sort_order, tipo_listino"
        cur.execute(sql)
        return [
            {
                "row_uuid": str(r[0]),
                "tipo_listino": r[1],
                "sort_order": int(r[2] or 0),
                "is_active": bool(r[3]),
            }
            for r in cur.fetchall()
        ]
    finally:
        try:
            conn.close()
        except Exception:
            pass


def list_listino_groups(include_inactive: bool = False) -> List[Dict[str, Any]]:
    ensure_supplier_orders_schema()
    conn = _conn(True)
    try:
        cur = conn.cursor()
        sql = "SELECT row_uuid, gruppo, sort_order, is_active FROM dbo.ListinoGruppi"
        if not include_inactive:
            sql += " WHERE is_active = 1"
        sql += " ORDER BY sort_order, gruppo"
        cur.execute(sql)
        return [
            {
                "row_uuid": str(r[0]),
                "gruppo": r[1],
                "sort_order": int(r[2] or 0),
                "is_active": bool(r[3]),
            }
            for r in cur.fetchall()
        ]
    finally:
        try:
            conn.close()
        except Exception:
            pass


def save_listino_type(row_uuid: str | None, tipo_listino: str, sort_order: int = 0, is_active: bool = True) -> str:
    ensure_supplier_orders_schema()
    tipo_listino = str(tipo_listino or "").strip()
    if not tipo_listino:
        raise ValueError("Tipo listino obbligatorio.")
    conn = _conn(False)
    try:
        cur = conn.cursor()
        if row_uuid:
            cur.execute(
                """
UPDATE dbo.ListinoTipi
SET tipo_listino=?, sort_order=?, is_active=?, updated_at=SYSUTCDATETIME()
WHERE row_uuid=?
""",
                (tipo_listino, int(sort_order or 0), 1 if is_active else 0, row_uuid),
            )
            new_id = row_uuid
        else:
            cur.execute(
                """
INSERT INTO dbo.ListinoTipi (tipo_listino, sort_order, is_active)
OUTPUT inserted.row_uuid
VALUES (?, ?, ?)
""",
                (tipo_listino, int(sort_order or 0), 1 if is_active else 0),
            )
            new_id = str(cur.fetchone()[0])
        conn.commit()
        return new_id
    finally:
        try:
            conn.close()
        except Exception:
            pass


def delete_listino_type(row_uuid: str) -> None:
    conn = _conn(False)
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM dbo.ListinoTipi WHERE row_uuid=?", (row_uuid,))
        conn.commit()
    finally:
        try:
            conn.close()
        except Exception:
            pass


def save_listino_group(row_uuid: str | None, gruppo: str, sort_order: int = 0, is_active: bool = True) -> str:
    ensure_supplier_orders_schema()
    gruppo = str(gruppo or "").strip().upper()
    if not gruppo:
        raise ValueError("Gruppo obbligatorio.")
    conn = _conn(False)
    try:
        cur = conn.cursor()
        if row_uuid:
            cur.execute(
                """
UPDATE dbo.ListinoGruppi
SET gruppo=?, sort_order=?, is_active=?, updated_at=SYSUTCDATETIME()
WHERE row_uuid=?
""",
                (gruppo, int(sort_order or 0), 1 if is_active else 0, row_uuid),
            )
            new_id = row_uuid
        else:
            cur.execute(
                """
INSERT INTO dbo.ListinoGruppi (gruppo, sort_order, is_active)
OUTPUT inserted.row_uuid
VALUES (?, ?, ?)
""",
                (gruppo, int(sort_order or 0), 1 if is_active else 0),
            )
            new_id = str(cur.fetchone()[0])
        conn.commit()
        return new_id
    finally:
        try:
            conn.close()
        except Exception:
            pass


def delete_listino_group(row_uuid: str) -> None:
    conn = _conn(False)
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM dbo.ListinoGruppi WHERE row_uuid=?", (row_uuid,))
        conn.commit()
    finally:
        try:
            conn.close()
        except Exception:
            pass


def list_listino_type_names() -> List[str]:
    return [str(r.get("tipo_listino") or "").strip() for r in list_listino_types(False) if str(r.get("tipo_listino") or "").strip()]


def list_listino_group_names() -> List[str]:
    return [str(r.get("gruppo") or "").strip() for r in list_listino_groups(False) if str(r.get("gruppo") or "").strip()]


def _default_price_list_uuid(cur) -> str:
    cur.execute("SELECT TOP 1 row_uuid FROM dbo.ListiniElenchi WHERE is_default = 1 AND is_active = 1 ORDER BY updated_at DESC")
    row = cur.fetchone()
    if row:
        return str(row[0])
    cur.execute("SELECT TOP 1 row_uuid FROM dbo.ListiniElenchi WHERE nome = 'Listino DOS'")
    row = cur.fetchone()
    if row:
        return str(row[0])
    cur.execute("INSERT INTO dbo.ListiniElenchi (nome, is_default, is_active) OUTPUT inserted.row_uuid VALUES ('Listino DOS', 1, 1)")
    return str(cur.fetchone()[0])


def list_price_lists(include_inactive: bool = True) -> List[Dict[str, Any]]:
    try:
        ensure_price_lists_schema()
    except Exception:
        pass
    conn = _conn(True)
    try:
        cur = conn.cursor()
        cur.execute("SELECT COL_LENGTH('dbo.ListiniPrezzi', 'listino_uuid')")
        has_price_list_uuid = bool(cur.fetchone()[0])
        row_count_expr = (
            "(SELECT COUNT(*) FROM dbo.ListiniPrezzi p WHERE p.listino_uuid = l.row_uuid)"
            if has_price_list_uuid
            else "(CASE WHEN l.nome = 'Listino DOS' THEN (SELECT COUNT(*) FROM dbo.ListiniPrezzi) ELSE 0 END)"
        )
        sql = f"""
SELECT l.row_uuid, l.nome, l.is_default, l.is_active,
       (SELECT COUNT(*) FROM dbo.ListiniStore s WHERE s.listino_uuid = l.row_uuid) AS [StoreCount],
       {row_count_expr} AS [RowCount]
FROM dbo.ListiniElenchi l
"""
        if not include_inactive:
            sql += " WHERE l.is_active = 1"
        sql += " ORDER BY l.is_default DESC, l.nome ASC"
        cur.execute(sql)
        return [
            {
                "row_uuid": str(r[0]),
                "nome": str(r[1] or ""),
                "is_default": bool(r[2]),
                "is_active": bool(r[3]),
                "store_count": int(r[4] or 0),
                "row_count": int(r[5] or 0),
            }
            for r in cur.fetchall()
        ]
    finally:
        try:
            conn.close()
        except Exception:
            pass


def get_price_list_for_store(store_code: str | None = None) -> Dict[str, Any]:
    ensure_price_lists_schema()
    store_code = str(store_code or "").strip()
    conn = _conn(True)
    try:
        cur = conn.cursor()
        if store_code:
            cur.execute(
                """
SELECT TOP 1 l.row_uuid, l.nome, l.is_default, l.is_active
FROM dbo.ListiniStore s
JOIN dbo.ListiniElenchi l ON l.row_uuid = s.listino_uuid
WHERE s.store_code = ? AND l.is_active = 1
""",
                (store_code,),
            )
            row = cur.fetchone()
            if row:
                return {"row_uuid": str(row[0]), "nome": str(row[1] or ""), "is_default": bool(row[2]), "is_active": bool(row[3])}
        cur.execute(
            """
SELECT TOP 1 row_uuid, nome, is_default, is_active
FROM dbo.ListiniElenchi
WHERE is_default = 1 AND is_active = 1
ORDER BY updated_at DESC
"""
        )
        row = cur.fetchone()
        if row:
            return {"row_uuid": str(row[0]), "nome": str(row[1] or ""), "is_default": bool(row[2]), "is_active": bool(row[3])}
        return {}
    finally:
        try:
            conn.close()
        except Exception:
            pass


def save_price_list(row_uuid: str | None, nome: str, is_active: bool = True) -> str:
    ensure_price_lists_schema()
    nome = str(nome or "").strip()
    if not nome:
        raise ValueError("Nome listino obbligatorio.")
    conn = _conn(False)
    try:
        cur = conn.cursor()
        if row_uuid:
            cur.execute(
                """
UPDATE dbo.ListiniElenchi
SET nome = ?, is_active = ?, updated_at = SYSUTCDATETIME()
WHERE row_uuid = ? AND is_default = 0
""",
                (nome, 1 if is_active else 0, row_uuid),
            )
            new_id = row_uuid
        else:
            cur.execute(
                """
INSERT INTO dbo.ListiniElenchi (nome, is_default, is_active)
OUTPUT inserted.row_uuid
VALUES (?, 0, ?)
""",
                (nome, 1 if is_active else 0),
            )
            new_id = str(cur.fetchone()[0])
        conn.commit()
        return new_id
    finally:
        try:
            conn.close()
        except Exception:
            pass


def set_default_price_list(listino_uuid: str) -> None:
    ensure_price_lists_schema()
    listino_uuid = str(listino_uuid or "").strip()
    if not listino_uuid:
        raise ValueError("Listino mancante.")
    conn = _conn(False)
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(1) FROM dbo.ListiniElenchi WHERE row_uuid = ?", (listino_uuid,))
        if int(cur.fetchone()[0] or 0) <= 0:
            raise ValueError("Listino non trovato.")
        cur.execute(
            """
UPDATE dbo.ListiniElenchi
SET is_default = CASE WHEN row_uuid = ? THEN 1 ELSE 0 END,
    is_active = CASE WHEN row_uuid = ? THEN 1 ELSE is_active END,
    updated_at = SYSUTCDATETIME()
"""
            ,
            (listino_uuid, listino_uuid),
        )
        conn.commit()
    finally:
        try:
            conn.close()
        except Exception:
            pass


def delete_price_list(listino_uuid: str) -> None:
    ensure_price_lists_schema()
    listino_uuid = str(listino_uuid or "").strip()
    if not listino_uuid:
        raise ValueError("Listino mancante.")
    conn = _conn(False)
    try:
        cur = conn.cursor()
        cur.execute("SELECT nome, is_default FROM dbo.ListiniElenchi WHERE row_uuid = ?", (listino_uuid,))
        row = cur.fetchone()
        if not row:
            raise ValueError("Listino non trovato.")
        if bool(row[1]):
            raise ValueError("Non puoi eliminare il listino di default. Impostane prima un altro come default.")

        default_uuid = _default_price_list_uuid(cur)
        if str(default_uuid or "").strip() == listino_uuid:
            raise ValueError("Non puoi eliminare il listino di default.")

        cur.execute(
            """
UPDATE dbo.ListiniStore
SET listino_uuid = ?, updated_at = SYSUTCDATETIME()
WHERE listino_uuid = ?
""",
            (default_uuid, listino_uuid),
        )
        cur.execute("DELETE FROM dbo.ListiniPrezzi WHERE listino_uuid = ?", (listino_uuid,))
        cur.execute("DELETE FROM dbo.ListiniStore WHERE listino_uuid = ?", (listino_uuid,))
        cur.execute("DELETE FROM dbo.ListiniElenchi WHERE row_uuid = ?", (listino_uuid,))
        conn.commit()
    finally:
        try:
            conn.close()
        except Exception:
            pass


def list_price_list_store_assignments() -> Dict[str, List[str]]:
    ensure_price_lists_schema()
    conn = _conn(True)
    try:
        cur = conn.cursor()
        cur.execute("SELECT listino_uuid, store_code FROM dbo.ListiniStore ORDER BY store_code")
        out: Dict[str, List[str]] = {}
        for r in cur.fetchall():
            out.setdefault(str(r[0]), []).append(str(r[1] or "").strip())
        return out
    finally:
        try:
            conn.close()
        except Exception:
            pass


def replace_price_list_store_assignments(listino_uuid: str, store_codes: List[str]) -> None:
    ensure_price_lists_schema()
    listino_uuid = str(listino_uuid or "").strip()
    if not listino_uuid:
        raise ValueError("Listino mancante.")
    codes: List[str] = []
    seen = set()
    for raw in store_codes or []:
        code = str(raw or "").strip()
        if not code or code in seen:
            continue
        seen.add(code)
        codes.append(code)
    conn = _conn(False)
    try:
        cur = conn.cursor()
        for code in codes:
            cur.execute(
                """
MERGE dbo.ListiniStore AS tgt
USING (SELECT ? AS store_code) AS src
ON tgt.store_code = src.store_code
WHEN MATCHED THEN UPDATE SET listino_uuid = ?, updated_at = SYSUTCDATETIME()
WHEN NOT MATCHED THEN INSERT (listino_uuid, store_code) VALUES (?, src.store_code);
""",
                (code, listino_uuid, listino_uuid),
            )
        if codes:
            placeholders = ",".join(["?"] * len(codes))
            cur.execute(
                f"DELETE FROM dbo.ListiniStore WHERE listino_uuid = ? AND store_code NOT IN ({placeholders})",
                [listino_uuid] + codes,
            )
        else:
            cur.execute("DELETE FROM dbo.ListiniStore WHERE listino_uuid = ?", (listino_uuid,))
        conn.commit()
    finally:
        try:
            conn.close()
        except Exception:
            pass


def assign_default_price_list_to_store(store_code: str) -> bool:
    ensure_price_lists_schema()
    code = str(store_code or "").strip()
    if not code:
        raise ValueError("Codice store obbligatorio.")
    conn = _conn(False)
    try:
        cur = conn.cursor()
        default_uuid = _default_price_list_uuid(cur)
        cur.execute(
            """
MERGE dbo.ListiniStore AS tgt
USING (SELECT ? AS store_code) AS src
ON tgt.store_code = src.store_code
WHEN NOT MATCHED THEN
  INSERT (listino_uuid, store_code) VALUES (?, src.store_code);
""",
            (code, default_uuid),
        )
        changed = bool(cur.rowcount)
        conn.commit()
        return changed
    finally:
        try:
            conn.close()
        except Exception:
            pass


def remove_price_list_store_assignment(store_code: str) -> bool:
    ensure_price_lists_schema()
    code = str(store_code or "").strip()
    if not code:
        raise ValueError("Codice store obbligatorio.")
    conn = _conn(False)
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM dbo.ListiniStore WHERE store_code = ?", (code,))
        changed = bool(cur.rowcount)
        conn.commit()
        return changed
    finally:
        try:
            conn.close()
        except Exception:
            pass


def copy_price_list_products(
    source_listino_uuid: str,
    target_listino_uuid: str,
    tipo_listino: str | None = None,
    overwrite: bool = False,
    products: List[Dict[str, Any]] | None = None,
) -> Dict[str, Any]:
    ensure_price_lists_schema()
    source_listino_uuid = str(source_listino_uuid or "").strip()
    target_listino_uuid = str(target_listino_uuid or "").strip()
    tipo_listino = str(tipo_listino or "").strip()
    if not source_listino_uuid or not target_listino_uuid:
        raise ValueError("Listino origine e destinazione sono obbligatori.")
    if source_listino_uuid == target_listino_uuid:
        raise ValueError("Origine e destinazione devono essere diverse.")
    product_keys: List[tuple[str, str]] = []
    for p in products or []:
        supplier = str((p or {}).get("supplier") or (p or {}).get("fornitore") or "").strip()
        description = str((p or {}).get("description") or (p or {}).get("descrizione") or "").strip()
        if supplier and description:
            key = (supplier, description)
            if key not in product_keys:
                product_keys.append(key)
    conn = _conn(False)
    try:
        cur = conn.cursor()
        if product_keys:
            cur.execute("CREATE TABLE #CopyListinoKeys (FORNITORE NVARCHAR(255) NOT NULL, Descrizione NVARCHAR(500) NOT NULL)")
            cur.executemany(
                "INSERT INTO #CopyListinoKeys (FORNITORE, Descrizione) VALUES (?, ?)",
                product_keys,
            )
        source_filter = "WHERE listino_uuid = ?"
        params: List[Any] = [source_listino_uuid]
        if tipo_listino:
            source_filter += " AND tipo_listino = ?"
            params.append(tipo_listino)
        if product_keys:
            source_filter += """
    AND EXISTS (
      SELECT 1
      FROM #CopyListinoKeys k
      WHERE LTRIM(RTRIM(k.FORNITORE)) = LTRIM(RTRIM(dbo.ListiniPrezzi.FORNITORE))
        AND LTRIM(RTRIM(k.Descrizione)) = LTRIM(RTRIM(dbo.ListiniPrezzi.Descrizione))
    )
"""
        source_sql = f"""
  SELECT
    tipo_listino,
    LTRIM(RTRIM(FORNITORE)) AS FORNITORE,
    LTRIM(RTRIM(Descrizione)) AS Descrizione,
    MAX(GRUPPO) AS GRUPPO,
    MAX(CODICE) AS CODICE,
    MAX(PREZZO) AS PREZZO,
    MAX(UNITA) AS UNITA,
    MAX(QTACAR) AS QTACAR,
    MAX(QTAINT) AS QTAINT,
    MAX(CONV) AS CONV
  FROM dbo.ListiniPrezzi
  {source_filter}
  GROUP BY tipo_listino, LTRIM(RTRIM(FORNITORE)), LTRIM(RTRIM(Descrizione))
"""
        if overwrite:
            sql = """
MERGE dbo.ListiniPrezzi AS tgt
USING (
""" + source_sql + """
) AS src
ON tgt.listino_uuid = ?
 AND tgt.tipo_listino = src.tipo_listino
 AND LTRIM(RTRIM(tgt.FORNITORE)) = LTRIM(RTRIM(src.FORNITORE))
 AND LTRIM(RTRIM(tgt.Descrizione)) = LTRIM(RTRIM(src.Descrizione))
WHEN MATCHED THEN
  UPDATE SET GRUPPO = src.GRUPPO, CODICE = src.CODICE, PREZZO = src.PREZZO, UNITA = src.UNITA,
             QTACAR = src.QTACAR, QTAINT = src.QTAINT, CONV = src.CONV, updated_at = SYSUTCDATETIME()
WHEN NOT MATCHED THEN
  INSERT (listino_uuid, tipo_listino, FORNITORE, Descrizione, GRUPPO, CODICE, PREZZO, UNITA, QTACAR, QTAINT, CONV)
  VALUES (?, src.tipo_listino, src.FORNITORE, src.Descrizione, src.GRUPPO, src.CODICE, src.PREZZO, src.UNITA, src.QTACAR, src.QTAINT, src.CONV);
"""
            params.extend([target_listino_uuid, target_listino_uuid])
            cur.execute(sql, params)
        else:
            sql = """
INSERT INTO dbo.ListiniPrezzi (listino_uuid, tipo_listino, FORNITORE, Descrizione, GRUPPO, CODICE, PREZZO, UNITA, QTACAR, QTAINT, CONV)
SELECT ?, src.tipo_listino, src.FORNITORE, src.Descrizione, src.GRUPPO, src.CODICE, src.PREZZO, src.UNITA, src.QTACAR, src.QTAINT, src.CONV
FROM (
""" + source_sql + """
) src
WHERE 1 = 1
  AND NOT EXISTS (
    SELECT 1
    FROM dbo.ListiniPrezzi tgt
    WHERE tgt.listino_uuid = ?
      AND tgt.tipo_listino = src.tipo_listino
      AND LTRIM(RTRIM(tgt.FORNITORE)) = LTRIM(RTRIM(src.FORNITORE))
      AND LTRIM(RTRIM(tgt.Descrizione)) = LTRIM(RTRIM(src.Descrizione))
  )
"""
            params = [target_listino_uuid] + params + [target_listino_uuid]
            cur.execute(sql, params)
        affected = int(cur.rowcount or 0)
        conn.commit()
        return {"ok": True, "copied": affected, "overwrite": bool(overwrite), "selected": len(product_keys)}
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        return {"ok": False, "copied": 0, "overwrite": bool(overwrite), "error": str(e)}
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _target_store_codes_for_assignments() -> List[str]:
    try:
        from flask import has_request_context, session

        tenant_key = ""
        if has_request_context():
            tenant_key = str(
                session.get("master_admin_tenant_key")
                or session.get("tenant_key")
                or ""
            ).strip()
        if tenant_key and tenant_key not in {"default"}:
            try:
                from tenant_config_repository import current_tenant_key, list_tenant_stores

                if tenant_key != str(current_tenant_key() or "").strip():
                    rows = list_tenant_stores(tenant_key, active_only=True)
                    codes = [str((r or {}).get("store_code") or "").strip() for r in rows or []]
                    return [c for c in codes if c]
            except Exception:
                pass
    except Exception:
        pass

    try:
        from db_integration import get_warehouse_stores

        stores = get_warehouse_stores() or []
    except Exception:
        stores = []
    return [str(s.get("code") or "").strip() for s in stores if str(s.get("code") or "").strip()]


def ensure_default_price_list_assignments(store_codes: List[str] | None = None) -> int:
    ensure_price_lists_schema()
    if store_codes is None:
        codes = _target_store_codes_for_assignments()
    else:
        codes = [str(s or "").strip() for s in store_codes if str(s or "").strip()]
    if not codes:
        return 0
    conn = _conn(False)
    inserted = 0
    try:
        cur = conn.cursor()
        default_uuid = _default_price_list_uuid(cur)
        for code in codes:
            cur.execute(
                """
IF NOT EXISTS (SELECT 1 FROM dbo.ListiniStore WHERE store_code = ?)
BEGIN
  INSERT INTO dbo.ListiniStore (listino_uuid, store_code) VALUES (?, ?);
END
""",
                (code, default_uuid, code),
            )
            inserted += int(cur.rowcount or 0)
        conn.commit()
        return inserted
    finally:
        try:
            conn.close()
        except Exception:
            pass


def list_fornitori() -> List[Dict[str, Any]]:
    ensure_supplier_orders_schema()
    conn = _conn(True)
    try:
        cur = conn.cursor()
        cur.execute(
            """
SELECT
  f.row_uuid,
  f.Fornitore,
  f.Referente,
  f.Email,
  f.Telefono1,
  f.Telefono2,
  ISNULL(f.TipoOrdine, 'Mail'),
  (SELECT COUNT(*) FROM dbo.FornitoriContatti c WHERE c.fornitore_row_uuid = f.row_uuid) AS ContactCount
FROM dbo.Fornitori f
ORDER BY Fornitore
"""
        )
        return [
            {
                "row_uuid": str(r[0]),
                "Fornitore": r[1] or "",
                "Referente": r[2] or "",
                "Email": r[3] or "",
                "Telefono1": r[4] or "",
                "Telefono2": r[5] or "",
                "TipoOrdine": r[6] or "Mail",
                "ContactCount": int(r[7] or 0),
            }
            for r in cur.fetchall()
        ]
    finally:
        try:
            conn.close()
        except Exception:
            pass


def save_fornitore(row_uuid: str | None, data: Dict[str, Any]) -> str:
    ensure_supplier_orders_schema()
    nome = str((data or {}).get("Fornitore") or "").strip()
    if not nome:
        raise ValueError("Fornitore obbligatorio.")
    tipo_ordine = str((data or {}).get("TipoOrdine") or "Mail").strip() or "Mail"
    if tipo_ordine not in {"Mail", "Online"}:
        tipo_ordine = "Mail"
    conn = _conn(False)
    try:
        cur = conn.cursor()
        vals = (
            nome,
            str((data or {}).get("Referente") or "").strip() or None,
            str((data or {}).get("Email") or "").strip() or None,
            str((data or {}).get("Telefono1") or "").strip() or None,
            str((data or {}).get("Telefono2") or "").strip() or None,
            tipo_ordine,
        )
        if row_uuid:
            cur.execute(
                """
UPDATE dbo.Fornitori
SET Fornitore=?, Referente=?, Email=?, Telefono1=?, Telefono2=?, TipoOrdine=?
WHERE row_uuid=?
""",
                vals + (row_uuid,),
            )
            new_id = row_uuid
        else:
            cur.execute(
                """
INSERT INTO dbo.Fornitori (Fornitore, Referente, Email, Telefono1, Telefono2, TipoOrdine)
OUTPUT inserted.row_uuid
VALUES (?, ?, ?, ?, ?, ?)
""",
                vals,
            )
            new_id = str(cur.fetchone()[0])
        conn.commit()
        return new_id
    finally:
        try:
            conn.close()
        except Exception:
            pass


def delete_fornitore(row_uuid: str) -> None:
    conn = _conn(False)
    try:
        cur = conn.cursor()
        cur.execute(
            """
DELETE s
FROM dbo.FornitoriContattiStores s
JOIN dbo.FornitoriContatti c ON c.row_uuid = s.contatto_row_uuid
WHERE c.fornitore_row_uuid = ?
""",
            (row_uuid,),
        )
        cur.execute("DELETE FROM dbo.FornitoriContatti WHERE fornitore_row_uuid=?", (row_uuid,))
        cur.execute("DELETE FROM dbo.Fornitori WHERE row_uuid=?", (row_uuid,))
        conn.commit()
    finally:
        try:
            conn.close()
        except Exception:
            pass


def list_fornitore_contacts(fornitore_row_uuid: str) -> List[Dict[str, Any]]:
    ensure_supplier_orders_schema()
    conn = _conn(True)
    try:
        cur = conn.cursor()
        cur.execute(
            """
SELECT row_uuid, Referente, Email, Telefono1, Telefono2, ISNULL(TipoOrdine, 'Mail'), sort_order
FROM dbo.FornitoriContatti
WHERE fornitore_row_uuid = ?
ORDER BY sort_order, created_at, row_uuid
""",
            (fornitore_row_uuid,),
        )
        rows = []
        for r in cur.fetchall():
            contact_id = str(r[0])
            cur2 = conn.cursor()
            cur2.execute(
                "SELECT store_code FROM dbo.FornitoriContattiStores WHERE contatto_row_uuid=? ORDER BY store_code",
                (contact_id,),
            )
            store_codes = [str(x[0] or "").strip() for x in cur2.fetchall() if str(x[0] or "").strip()]
            cur2.close()
            rows.append(
                {
                    "row_uuid": contact_id,
                    "Referente": r[1] or "",
                    "Email": r[2] or "",
                    "Telefono1": r[3] or "",
                    "Telefono2": r[4] or "",
                    "TipoOrdine": r[5] or "Mail",
                    "sort_order": int(r[6] or 0),
                    "store_codes": store_codes,
                }
            )
        return rows
    finally:
        try:
            conn.close()
        except Exception:
            pass


def save_fornitore_contact(
    fornitore_row_uuid: str,
    row_uuid: str | None,
    data: Dict[str, Any],
    store_codes: List[str] | None = None,
) -> str:
    ensure_supplier_orders_schema()
    if not str(fornitore_row_uuid or "").strip():
        raise ValueError("Fornitore mancante.")
    tipo_ordine = str((data or {}).get("TipoOrdine") or "Mail").strip() or "Mail"
    if tipo_ordine not in {"Mail", "Online"}:
        tipo_ordine = "Mail"
    store_codes = [str(x or "").strip() for x in (store_codes or []) if str(x or "").strip()]
    conn = _conn(False)
    try:
        cur = conn.cursor()
        vals = (
            str((data or {}).get("Referente") or "").strip() or None,
            str((data or {}).get("Email") or "").strip() or None,
            str((data or {}).get("Telefono1") or "").strip() or None,
            str((data or {}).get("Telefono2") or "").strip() or None,
            tipo_ordine,
            int((data or {}).get("sort_order") or 0),
        )
        if row_uuid:
            cur.execute(
                """
UPDATE dbo.FornitoriContatti
SET Referente=?, Email=?, Telefono1=?, Telefono2=?, TipoOrdine=?, sort_order=?, updated_at=SYSUTCDATETIME()
WHERE row_uuid=? AND fornitore_row_uuid=?
""",
                vals + (row_uuid, fornitore_row_uuid),
            )
            new_id = row_uuid
        else:
            cur.execute(
                """
INSERT INTO dbo.FornitoriContatti (fornitore_row_uuid, Referente, Email, Telefono1, Telefono2, TipoOrdine, sort_order)
OUTPUT inserted.row_uuid
VALUES (?, ?, ?, ?, ?, ?, ?)
""",
                (fornitore_row_uuid,) + vals,
            )
            new_id = str(cur.fetchone()[0])

        cur.execute("DELETE FROM dbo.FornitoriContattiStores WHERE contatto_row_uuid=?", (new_id,))
        for sc in store_codes:
            cur.execute(
                "INSERT INTO dbo.FornitoriContattiStores (contatto_row_uuid, store_code) VALUES (?, ?)",
                (new_id, sc),
            )
        conn.commit()
        return new_id
    finally:
        try:
            conn.close()
        except Exception:
            pass


def delete_fornitore_contact(fornitore_row_uuid: str, row_uuid: str) -> None:
    conn = _conn(False)
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM dbo.FornitoriContattiStores WHERE contatto_row_uuid=?", (row_uuid,))
        cur.execute("DELETE FROM dbo.FornitoriContatti WHERE row_uuid=? AND fornitore_row_uuid=?", (row_uuid, fornitore_row_uuid))
        conn.commit()
    finally:
        try:
            conn.close()
        except Exception:
            pass


def load_pricelist(type_name: str, price_list_uuid: str | None = None) -> Dict[str, Any]:
    try:
        ensure_price_lists_schema()
    except Exception:
        return _load_pricelist_legacy(type_name)
    type_name = str(type_name or "").strip() or "FoodPaper"
    conn = _conn(True)
    try:
        cur = conn.cursor()
        price_list_uuid = str(price_list_uuid or "").strip() or _default_price_list_uuid(cur)
        cur.execute("SELECT TOP 1 nome FROM dbo.ListiniElenchi WHERE row_uuid = ?", (price_list_uuid,))
        list_row = cur.fetchone()
        price_list_name = str(list_row[0] or "") if list_row else "Listino DOS"
        cur.execute(
            """
SELECT tipo_listino, Descrizione, GRUPPO, FORNITORE, CODICE, PREZZO, UNITA, QTACAR, QTAINT, CONV
FROM dbo.ListiniPrezzi
WHERE tipo_listino = ? AND listino_uuid = ?
ORDER BY FORNITORE, Descrizione
""",
            (type_name, price_list_uuid),
        )
        rows = []
        for r in cur.fetchall():
            rows.append(
                {
                    "TipoListino": r[0],
                    "Descrizione": r[1],
                    "GRUPPO": r[2],
                    "FORNITORE": r[3],
                    "CODICE": r[4],
                    "PREZZO": r[5],
                    "UNITA": r[6],
                    "QTACAR": r[7],
                    "QTAINT": r[8],
                    "CONV": r[9],
                }
            )
        return {
            "ok": True,
            "source_store": "9001",
            "price_list_uuid": price_list_uuid,
            "price_list_name": price_list_name,
            "listino_type": type_name,
            "table": TABLE_LISTINI,
            "columns": ["FORNITORE", "Descrizione", "GRUPPO", "CODICE", "PREZZO", "UNITA", "QTACAR", "QTAINT", "CONV"],
            "rows": rows,
            "key_column": "Descrizione",
            "desc_column": "Descrizione",
            "supplier_column": "FORNITORE",
            "conv_table": "",
            "suppliers": [str(r.get("Fornitore") or "").strip() for r in list_fornitori() if str(r.get("Fornitore") or "").strip()],
            "groups": [str(g.get("gruppo") or "").strip() for g in list_listino_groups(False) if str(g.get("gruppo") or "").strip()],
            "types": [str(t.get("tipo_listino") or "").strip() for t in list_listino_types(False) if str(t.get("tipo_listino") or "").strip()],
            "price_lists": list_price_lists(False),
        }
    finally:
        try:
            conn.close()
        except Exception:
            pass


def export_pricelist_for_store(store_code: str, type_name: str | None = None) -> Dict[str, Any]:
    try:
        ensure_price_lists_schema()
    except Exception:
        return _export_pricelist_legacy(type_name)

    store_code = str(store_code or "").strip()
    type_name = str(type_name or "").strip()
    conn = _conn(True)
    try:
        cur = conn.cursor()
        assigned = get_price_list_for_store(store_code)
        price_list_uuid = str(assigned.get("row_uuid") or "").strip() or _default_price_list_uuid(cur)
        price_list_name = str(assigned.get("nome") or "").strip() or "Listino DOS"
        sql = """
SELECT tipo_listino, FORNITORE, Descrizione, GRUPPO, CODICE, PREZZO, UNITA, QTACAR, QTAINT, CONV
FROM dbo.ListiniPrezzi
WHERE listino_uuid = ?
"""
        params: List[Any] = [price_list_uuid]
        if type_name:
            sql += " AND tipo_listino = ?"
            params.append(type_name)
        sql += " ORDER BY tipo_listino, FORNITORE, Descrizione"
        cur.execute(sql, params)
        rows = []
        for r in cur.fetchall():
            rows.append(
                {
                    "TipoListino": r[0],
                    "FORNITORE": r[1],
                    "Descrizione": r[2],
                    "GRUPPO": r[3],
                    "CODICE": r[4],
                    "PREZZO": r[5],
                    "UNITA": r[6],
                    "QTACAR": r[7],
                    "QTAINT": r[8],
                    "CONV": r[9],
                }
            )
        return {
            "ok": True,
            "store_code": store_code,
            "price_list_uuid": price_list_uuid,
            "price_list_name": price_list_name,
            "listino_type": type_name,
            "columns": ["TipoListino", "FORNITORE", "Descrizione", "GRUPPO", "CODICE", "PREZZO", "UNITA", "QTACAR", "QTAINT", "CONV"],
            "rows": rows,
        }
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _export_pricelist_legacy(type_name: str | None = None) -> Dict[str, Any]:
    ensure_supplier_orders_schema()
    type_name = str(type_name or "").strip()
    conn = _conn(True)
    try:
        cur = conn.cursor()
        sql = """
SELECT tipo_listino, FORNITORE, Descrizione, GRUPPO, CODICE, PREZZO, UNITA, QTACAR, QTAINT, CONV
FROM dbo.ListiniPrezzi
WHERE 1 = 1
"""
        params: List[Any] = []
        if type_name:
            sql += " AND tipo_listino = ?"
            params.append(type_name)
        sql += " ORDER BY tipo_listino, FORNITORE, Descrizione"
        cur.execute(sql, params)
        rows = []
        for r in cur.fetchall():
            rows.append(
                {
                    "TipoListino": r[0],
                    "FORNITORE": r[1],
                    "Descrizione": r[2],
                    "GRUPPO": r[3],
                    "CODICE": r[4],
                    "PREZZO": r[5],
                    "UNITA": r[6],
                    "QTACAR": r[7],
                    "QTAINT": r[8],
                    "CONV": r[9],
                }
            )
        return {
            "ok": True,
            "store_code": "",
            "price_list_uuid": "",
            "price_list_name": "Listino DOS",
            "listino_type": type_name,
            "columns": ["TipoListino", "FORNITORE", "Descrizione", "GRUPPO", "CODICE", "PREZZO", "UNITA", "QTACAR", "QTAINT", "CONV"],
            "rows": rows,
        }
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _load_pricelist_legacy(type_name: str) -> Dict[str, Any]:
    ensure_supplier_orders_schema()
    type_name = str(type_name or "").strip() or "FoodPaper"
    conn = _conn(True)
    try:
        cur = conn.cursor()
        cur.execute(
            """
SELECT tipo_listino, Descrizione, GRUPPO, FORNITORE, CODICE, PREZZO, UNITA, QTACAR, QTAINT, CONV
FROM dbo.ListiniPrezzi
WHERE tipo_listino = ?
ORDER BY FORNITORE, Descrizione
""",
            (type_name,),
        )
        rows = []
        for r in cur.fetchall():
            rows.append(
                {
                    "TipoListino": r[0],
                    "Descrizione": r[1],
                    "GRUPPO": r[2],
                    "FORNITORE": r[3],
                    "CODICE": r[4],
                    "PREZZO": r[5],
                    "UNITA": r[6],
                    "QTACAR": r[7],
                    "QTAINT": r[8],
                    "CONV": r[9],
                }
            )
        return {
            "ok": True,
            "source_store": "9001",
            "price_list_uuid": "",
            "price_list_name": "Listino DOS",
            "listino_type": type_name,
            "table": TABLE_LISTINI,
            "columns": ["FORNITORE", "Descrizione", "GRUPPO", "CODICE", "PREZZO", "UNITA", "QTACAR", "QTAINT", "CONV"],
            "rows": rows,
            "key_column": "Descrizione",
            "desc_column": "Descrizione",
            "supplier_column": "FORNITORE",
            "conv_table": "",
            "suppliers": [str(r.get("Fornitore") or "").strip() for r in list_fornitori() if str(r.get("Fornitore") or "").strip()],
            "groups": [str(g.get("gruppo") or "").strip() for g in list_listino_groups(False) if str(g.get("gruppo") or "").strip()],
            "types": [str(t.get("tipo_listino") or "").strip() for t in list_listino_types(False) if str(t.get("tipo_listino") or "").strip()],
            "price_lists": [{"row_uuid": "", "nome": "Listino DOS", "is_default": True, "is_active": True}],
        }
    finally:
        try:
            conn.close()
        except Exception:
            pass


def upsert_pricelist_rows(type_name: str, rows: List[Dict[str, Any]], price_list_uuid: str | None = None) -> Dict[str, Any]:
    try:
        ensure_price_lists_schema()
    except Exception:
        return _upsert_pricelist_rows_legacy(type_name, rows)
    conn = _conn(False)
    stats = {"ok": False, "stores_total": 1, "stores_ok": 1, "stores_fail": 0, "stores": [{"store": "9001", "ok": True, "updates": 0, "inserts": 0, "deletes": 0, "conv_updates": 0, "conv_inserts": 0, "conv_deletes": 0, "error": None}], "error": None}
    try:
        cur = conn.cursor()
        price_list_uuid = str(price_list_uuid or "").strip() or _default_price_list_uuid(cur)
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            row = _normalize_pricelist_row_numbers(row)
            is_deleted = bool(row.get("__deleted") or row.get("_deleted") or str(row.get("__action") or "").strip().lower() == "delete")
            fornitore = str(row.get("FORNITORE") or "").strip()
            descr = str(row.get("Descrizione") or row.get("DESCRIZIONE") or "").strip()
            if not fornitore or not descr:
                continue
            if is_deleted:
                cur.execute(
                    "DELETE FROM dbo.ListiniPrezzi WHERE listino_uuid=? AND tipo_listino=? AND LTRIM(RTRIM(FORNITORE))=LTRIM(RTRIM(?)) AND LTRIM(RTRIM(Descrizione))=LTRIM(RTRIM(?))",
                    (price_list_uuid, type_name, fornitore, descr),
                )
                stats["stores"][0]["deletes"] += 1
                continue
            cur.execute(
                """
MERGE dbo.ListiniPrezzi AS tgt
USING (SELECT ? AS listino_uuid, ? AS tipo_listino, ? AS FORNITORE, ? AS Descrizione) AS src
ON tgt.listino_uuid = src.listino_uuid
 AND tgt.tipo_listino = src.tipo_listino
 AND LTRIM(RTRIM(tgt.FORNITORE)) = LTRIM(RTRIM(src.FORNITORE))
 AND LTRIM(RTRIM(tgt.Descrizione)) = LTRIM(RTRIM(src.Descrizione))
WHEN MATCHED THEN
  UPDATE SET
    GRUPPO = ?,
    CODICE = ?,
    PREZZO = ?,
    UNITA = ?,
    QTACAR = ?,
    QTAINT = ?,
    CONV = ?,
    updated_at = SYSUTCDATETIME()
WHEN NOT MATCHED THEN
  INSERT (listino_uuid, tipo_listino, FORNITORE, Descrizione, GRUPPO, CODICE, PREZZO, UNITA, QTACAR, QTAINT, CONV)
  VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
""",
                (
                    price_list_uuid,
                    type_name,
                    fornitore,
                    descr,
                    row.get("GRUPPO"),
                    row.get("CODICE"),
                    row.get("PREZZO"),
                    row.get("UNITA"),
                    row.get("QTACAR"),
                    row.get("QTAINT"),
                    row.get("CONV"),
                    price_list_uuid,
                    type_name,
                    fornitore,
                    descr,
                    row.get("GRUPPO"),
                    row.get("CODICE"),
                    row.get("PREZZO"),
                    row.get("UNITA"),
                    row.get("QTACAR"),
                    row.get("QTAINT"),
                    row.get("CONV"),
                ),
            )
            if cur.rowcount and cur.rowcount > 0:
                # merge rowcount is not exact for insert/update split, count as update for existing shape
                stats["stores"][0]["updates"] += 1
        conn.commit()
        stats["ok"] = True
        return stats
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        stats["ok"] = False
        stats["stores_ok"] = 0
        stats["stores_fail"] = 1
        stats["stores"][0]["ok"] = False
        stats["stores"][0]["error"] = str(e)
        stats["error"] = str(e)
        return stats
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _upsert_pricelist_rows_legacy(type_name: str, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    ensure_supplier_orders_schema()
    conn = _conn(False)
    stats = {"ok": False, "stores_total": 1, "stores_ok": 1, "stores_fail": 0, "stores": [{"store": "9001", "ok": True, "updates": 0, "inserts": 0, "deletes": 0, "conv_updates": 0, "conv_inserts": 0, "conv_deletes": 0, "error": None}], "error": None}
    try:
        cur = conn.cursor()
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            row = _normalize_pricelist_row_numbers(row)
            is_deleted = bool(row.get("__deleted") or row.get("_deleted") or str(row.get("__action") or "").strip().lower() == "delete")
            fornitore = str(row.get("FORNITORE") or "").strip()
            descr = str(row.get("Descrizione") or row.get("DESCRIZIONE") or "").strip()
            if not fornitore or not descr:
                continue
            if is_deleted:
                cur.execute(
                    "DELETE FROM dbo.ListiniPrezzi WHERE tipo_listino=? AND LTRIM(RTRIM(FORNITORE))=LTRIM(RTRIM(?)) AND LTRIM(RTRIM(Descrizione))=LTRIM(RTRIM(?))",
                    (type_name, fornitore, descr),
                )
                stats["stores"][0]["deletes"] += 1
                continue
            cur.execute(
                """
MERGE dbo.ListiniPrezzi AS tgt
USING (SELECT ? AS tipo_listino, ? AS FORNITORE, ? AS Descrizione) AS src
ON tgt.tipo_listino = src.tipo_listino
 AND LTRIM(RTRIM(tgt.FORNITORE)) = LTRIM(RTRIM(src.FORNITORE))
 AND LTRIM(RTRIM(tgt.Descrizione)) = LTRIM(RTRIM(src.Descrizione))
WHEN MATCHED THEN
  UPDATE SET GRUPPO = ?, CODICE = ?, PREZZO = ?, UNITA = ?, QTACAR = ?, QTAINT = ?, CONV = ?, updated_at = SYSUTCDATETIME()
WHEN NOT MATCHED THEN
  INSERT (tipo_listino, FORNITORE, Descrizione, GRUPPO, CODICE, PREZZO, UNITA, QTACAR, QTAINT, CONV)
  VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
""",
                (
                    type_name, fornitore, descr,
                    row.get("GRUPPO"), row.get("CODICE"), row.get("PREZZO"), row.get("UNITA"), row.get("QTACAR"), row.get("QTAINT"), row.get("CONV"),
                    type_name, fornitore, descr,
                    row.get("GRUPPO"), row.get("CODICE"), row.get("PREZZO"), row.get("UNITA"), row.get("QTACAR"), row.get("QTAINT"), row.get("CONV"),
                ),
            )
            stats["stores"][0]["updates"] += 1
        conn.commit()
        stats["ok"] = True
        return stats
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        stats["ok"] = False
        stats["stores_ok"] = 0
        stats["stores_fail"] = 1
        stats["stores"][0]["ok"] = False
        stats["stores"][0]["error"] = str(e)
        stats["error"] = str(e)
        return stats
    finally:
        try:
            conn.close()
        except Exception:
            pass


def list_prices_for_supplier_all_types(supplier_code: str, max_rows: int = 500, store_code: str | None = None, price_list_uuid: str | None = None) -> Dict[str, Any]:
    try:
        ensure_price_lists_schema()
    except Exception:
        return _list_prices_for_supplier_all_types_legacy(supplier_code, max_rows=max_rows)
    supplier_code = str(supplier_code or "").strip()
    conn = _conn(True)
    try:
        cur = conn.cursor()
        if not str(price_list_uuid or "").strip():
            assigned = get_price_list_for_store(store_code)
            price_list_uuid = str(assigned.get("row_uuid") or "").strip()
        price_list_uuid = str(price_list_uuid or "").strip() or _default_price_list_uuid(cur)
        cur.execute(
            """
SELECT TOP (?) tipo_listino, FORNITORE, Descrizione, GRUPPO, CODICE, PREZZO, UNITA, QTACAR, QTAINT, CONV
FROM dbo.ListiniPrezzi
WHERE listino_uuid = ? AND LTRIM(RTRIM(FORNITORE)) = LTRIM(RTRIM(?))
ORDER BY tipo_listino, Descrizione
""",
            (int(max_rows or 500), price_list_uuid, supplier_code),
        )
        rows = []
        for r in cur.fetchall():
            rows.append(
                {
                    "FORNITORE": r[1],
                    "Descrizione": r[2],
                    "GRUPPO": r[3],
                    "CODICE": r[4],
                    "PREZZO": r[5],
                    "UNITA": r[6],
                    "QTACAR": r[7],
                    "QTAINT": r[8],
                    "CONV": r[9],
                    "_TipoListino": r[0],
                }
            )
        return {
            "tables": [TABLE_LISTINI],
            "supplier_code": supplier_code,
            "columns": ["FORNITORE", "Descrizione", "GRUPPO", "CODICE", "PREZZO", "UNITA", "QTACAR", "QTAINT", "CONV", "_TipoListino"],
            "rows": rows,
            "error": None,
            "available_columns": {TABLE_LISTINI: ["FORNITORE", "Descrizione", "GRUPPO", "CODICE", "PREZZO", "UNITA", "QTACAR", "QTAINT", "CONV", "_TipoListino"]},
            "code_column": "CODICE",
            "desc_column": "Descrizione",
            "price_column": "PREZZO",
            "unit_column": "QTACAR",
        }
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _list_prices_for_supplier_all_types_legacy(supplier_code: str, max_rows: int = 500) -> Dict[str, Any]:
    ensure_supplier_orders_schema()
    supplier_code = str(supplier_code or "").strip()
    conn = _conn(True)
    try:
        cur = conn.cursor()
        cur.execute(
            """
SELECT TOP (?) tipo_listino, FORNITORE, Descrizione, GRUPPO, CODICE, PREZZO, UNITA, QTACAR, QTAINT, CONV
FROM dbo.ListiniPrezzi
WHERE LTRIM(RTRIM(FORNITORE)) = LTRIM(RTRIM(?))
ORDER BY tipo_listino, Descrizione
""",
            (int(max_rows or 500), supplier_code),
        )
        rows = []
        for r in cur.fetchall():
            rows.append(
                {
                    "FORNITORE": r[1],
                    "Descrizione": r[2],
                    "GRUPPO": r[3],
                    "CODICE": r[4],
                    "PREZZO": r[5],
                    "UNITA": r[6],
                    "QTACAR": r[7],
                    "QTAINT": r[8],
                    "CONV": r[9],
                    "_TipoListino": r[0],
                }
            )
        return {
            "tables": [TABLE_LISTINI],
            "supplier_code": supplier_code,
            "columns": ["FORNITORE", "Descrizione", "GRUPPO", "CODICE", "PREZZO", "UNITA", "QTACAR", "QTAINT", "CONV", "_TipoListino"],
            "rows": rows,
            "error": None,
            "available_columns": {TABLE_LISTINI: ["FORNITORE", "Descrizione", "GRUPPO", "CODICE", "PREZZO", "UNITA", "QTACAR", "QTAINT", "CONV", "_TipoListino"]},
            "code_column": "CODICE",
            "desc_column": "Descrizione",
            "price_column": "PREZZO",
            "unit_column": "QTACAR",
        }
    finally:
        try:
            conn.close()
        except Exception:
            pass
