from __future__ import annotations

from app_db import get_connection_sqlserver_database, get_storehub_database_name


def _conn(read_only: bool = False):
    return get_connection_sqlserver_database(get_storehub_database_name(), read_only=read_only)


def ensure_warehouse_operational_schema() -> None:
    """Create the base warehouse movement tables used by DDT, inventory and TX flows."""
    sql = """
IF OBJECT_ID('dbo.DatiDelivery','U') IS NULL
BEGIN
  CREATE TABLE dbo.DatiDelivery (
    Site NVARCHAR(50) NOT NULL,
    Fattura DATE NULL,
    Data DATE NULL,
    Fornitore NVARCHAR(255) NULL,
    Codice NVARCHAR(100) NULL,
    Descrizione NVARCHAR(500) NULL,
    GRUPPO NVARCHAR(100) NULL,
    PREZZO DECIMAL(18,4) NULL,
    UNITA NVARCHAR(50) NULL,
    QTACAR DECIMAL(18,4) NULL,
    QTAINT DECIMAL(18,4) NULL,
    CONV DECIMAL(18,4) NULL,
    CAR DECIMAL(18,4) NOT NULL DEFAULT 0,
    VALORE DECIMAL(18,4) NOT NULL DEFAULT 0,
    row_uuid UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
  );
  CREATE INDEX IX_DatiDelivery_site_data ON dbo.DatiDelivery(Site, Data);
  CREATE INDEX IX_DatiDelivery_supplier ON dbo.DatiDelivery(Fornitore, Data);
END

IF OBJECT_ID('dbo.datiinventario','U') IS NULL
BEGIN
  CREATE TABLE dbo.datiinventario (
    Site NVARCHAR(50) NOT NULL,
    Data DATE NOT NULL,
    TipoTrans NVARCHAR(50) NOT NULL,
    Fornitore NVARCHAR(255) NULL,
    Codice NVARCHAR(100) NULL,
    Descrizione NVARCHAR(500) NULL,
    GRUPPO NVARCHAR(100) NULL,
    PREZZO DECIMAL(18,4) NULL,
    UNITA NVARCHAR(50) NULL,
    QTACAR DECIMAL(18,4) NULL,
    QTAINT DECIMAL(18,4) NULL,
    CONV DECIMAL(18,4) NULL,
    CAR DECIMAL(18,4) NOT NULL DEFAULT 0,
    INTERNO DECIMAL(18,4) NOT NULL DEFAULT 0,
    PEZ DECIMAL(18,4) NOT NULL DEFAULT 0,
    TOTPZ DECIMAL(18,4) NOT NULL DEFAULT 0,
    TOTEURO DECIMAL(18,4) NOT NULL DEFAULT 0,
    row_uuid UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
  );
  CREATE INDEX IX_datiinventario_site_data ON dbo.datiinventario(Site, Data, TipoTrans);
  CREATE INDEX IX_datiinventario_supplier ON dbo.datiinventario(Fornitore, Data);
END

IF OBJECT_ID('dbo.DatiTX','U') IS NULL
BEGIN
  CREATE TABLE dbo.DatiTX (
    Site NVARCHAR(50) NOT NULL,
    SITE2 NVARCHAR(50) NOT NULL,
    Data DATE NOT NULL,
    TipoTrans NVARCHAR(50) NOT NULL,
    Fornitore NVARCHAR(255) NULL,
    Codice NVARCHAR(100) NULL,
    Descrizione NVARCHAR(500) NULL,
    GRUPPO NVARCHAR(100) NULL,
    PREZZO DECIMAL(18,4) NULL,
    UNITA NVARCHAR(50) NULL,
    QTACAR DECIMAL(18,4) NULL,
    QTAINT DECIMAL(18,4) NULL,
    CONV DECIMAL(18,4) NULL,
    CAR DECIMAL(18,4) NOT NULL DEFAULT 0,
    INTERNO DECIMAL(18,4) NOT NULL DEFAULT 0,
    PEZ DECIMAL(18,4) NOT NULL DEFAULT 0,
    TOTPZ DECIMAL(18,4) NOT NULL DEFAULT 0,
    TOTEURO DECIMAL(18,4) NOT NULL DEFAULT 0,
    row_uuid UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
  );
  CREATE INDEX IX_DatiTX_site_data ON dbo.DatiTX(Site, Data, TipoTrans);
  CREATE INDEX IX_DatiTX_site2_data ON dbo.DatiTX(SITE2, Data, TipoTrans);
  CREATE INDEX IX_DatiTX_supplier ON dbo.DatiTX(Fornitore, Data);
END
"""
    with _conn(read_only=False) as conn:
        cur = conn.cursor()
        cur.execute(sql)
        conn.commit()
