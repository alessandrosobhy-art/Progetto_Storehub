from __future__ import annotations

from app_db import get_connection


def ensure_rendiconto_legacy_schema() -> None:
    """Crea le tabelle minime usate dalla maschera Rendiconto.

    I tenant nuovi usano le tabelle StoreHub per report e dati normalizzati,
    ma alcune maschere storiche leggono ancora queste tabelle operative.
    """
    with get_connection(read_only=False) as conn:
        cur = conn.cursor()
        cur.execute(
            """
IF OBJECT_ID('dbo.ELENCHI','U') IS NULL
BEGIN
  CREATE TABLE dbo.ELENCHI (
    row_uuid UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    TICKET NVARCHAR(255) NULL,
    TC NVARCHAR(10) NULL,
    DELIVERY NVARCHAR(255) NULL,
    DC NVARCHAR(10) NULL,
    COUPON NVARCHAR(255) NULL,
    CC NVARCHAR(10) NULL,
    created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
  );
END
"""
        )
        cur.execute(
            """
IF OBJECT_ID('dbo.DATIPRIMANOTA','U') IS NULL
BEGIN
  CREATE TABLE dbo.DATIPRIMANOTA (
    row_uuid UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    SITE NVARCHAR(50) NULL,
    DATA DATE NOT NULL,
    CATEGORIA NVARCHAR(120) NOT NULL,
    VOCE NVARCHAR(255) NOT NULL,
    TIPO NVARCHAR(10) NULL,
    VALORE DECIMAL(18,4) NOT NULL DEFAULT 0,
    created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
  );
  CREATE INDEX IX_DATIPRIMANOTA_site_data
    ON dbo.DATIPRIMANOTA (SITE, DATA, CATEGORIA);
END
"""
        )
        cur.execute(
            """
IF OBJECT_ID('dbo.SPESE','U') IS NULL
BEGIN
  CREATE TABLE dbo.SPESE (
    row_uuid UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    SITE NVARCHAR(50) NULL,
    DATA DATE NOT NULL,
    TIPO NVARCHAR(120) NULL,
    FORNITORE NVARCHAR(255) NULL,
    DOCUMENTO NVARCHAR(255) NULL,
    IMPORTO DECIMAL(18,4) NOT NULL DEFAULT 0,
    FOTO_FILE NVARCHAR(255) NULL,
    created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
  );
  CREATE INDEX IX_SPESE_site_data
    ON dbo.SPESE (SITE, DATA);
END
"""
        )
        cur.execute(
            """
IF OBJECT_ID('dbo.VERSAMENTI_APP','U') IS NULL
BEGIN
  CREATE TABLE dbo.VERSAMENTI_APP (
    row_uuid UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    SITE NVARCHAR(50) NULL,
    DATA_VERSAMENTO DATE NOT NULL,
    DAL DATE NOT NULL,
    AL DATE NOT NULL,
    NOME_COGNOME NVARCHAR(255) NULL,
    TIPO NVARCHAR(50) NULL,
    TESSERA NVARCHAR(120) NULL,
    RIFERIMENTO NVARCHAR(255) NULL,
    VALORE DECIMAL(18,4) NOT NULL DEFAULT 0,
    FOTO_FILE NVARCHAR(255) NULL,
    NO_RECEIPT_FLAG INT NOT NULL DEFAULT 0,
    LOST_RECEIPT_FLAG INT NOT NULL DEFAULT 0,
    created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
  );
  CREATE INDEX IX_VERSAMENTI_APP_site_data
    ON dbo.VERSAMENTI_APP (SITE, DATA_VERSAMENTO);
  CREATE INDEX IX_VERSAMENTI_APP_site_periodo
    ON dbo.VERSAMENTI_APP (SITE, DAL, AL);
END
"""
        )
        conn.commit()
