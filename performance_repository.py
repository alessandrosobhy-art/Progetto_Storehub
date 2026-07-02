from __future__ import annotations

from app_db import get_connection_sqlserver_database, get_storehub_database_name


def _conn(read_only: bool = False):
    return get_connection_sqlserver_database(get_storehub_database_name(), read_only=read_only)


def ensure_tenant_performance_indexes() -> None:
    """Create non-destructive SQL Server indexes for the hottest tenant tables.

    These indexes are intentionally conservative:
    - no schema changes
    - only additive indexes
    - only on tenant-local operational tables
    """
    sql = """
IF OBJECT_ID('dbo.StoreHubDailySales', 'U') IS NOT NULL
   AND NOT EXISTS (
      SELECT 1
      FROM sys.indexes
      WHERE object_id = OBJECT_ID('dbo.StoreHubDailySales')
        AND name = 'IX_StoreHubDailySales_store_date_cover'
   )
BEGIN
  CREATE INDEX IX_StoreHubDailySales_store_date_cover
    ON dbo.StoreHubDailySales (store_code, business_date)
    INCLUDE (
      gross_revenue,
      net_revenue,
      receipts_count,
      delivery_total,
      delivery_online_amount,
      delivery_cash_amount,
      expenses_net,
      cash_difference
    );
END;

IF OBJECT_ID('dbo.StoreHubSalesForecast', 'U') IS NOT NULL
   AND NOT EXISTS (
      SELECT 1
      FROM sys.indexes
      WHERE object_id = OBJECT_ID('dbo.StoreHubSalesForecast')
        AND name = 'IX_StoreHubSalesForecast_store_date_cover'
   )
BEGIN
  CREATE INDEX IX_StoreHubSalesForecast_store_date_cover
    ON dbo.StoreHubSalesForecast (store_code, business_date)
    INCLUDE (forecast_net);
END;

IF OBJECT_ID('dbo.DATIPRIMANOTA', 'U') IS NOT NULL
   AND NOT EXISTS (
      SELECT 1
      FROM sys.indexes
      WHERE object_id = OBJECT_ID('dbo.DATIPRIMANOTA')
        AND name = 'IX_DATIPRIMANOTA_site_data_voce_cover'
   )
   AND COL_LENGTH('dbo.DATIPRIMANOTA', 'SITE') IS NOT NULL
   AND COL_LENGTH('dbo.DATIPRIMANOTA', 'DATA') IS NOT NULL
   AND COL_LENGTH('dbo.DATIPRIMANOTA', 'CATEGORIA') IS NOT NULL
   AND COL_LENGTH('dbo.DATIPRIMANOTA', 'VOCE') IS NOT NULL
   AND COL_LENGTH('dbo.DATIPRIMANOTA', 'TIPO') IS NOT NULL
   AND COL_LENGTH('dbo.DATIPRIMANOTA', 'VALORE') IS NOT NULL
BEGIN
  CREATE INDEX IX_DATIPRIMANOTA_site_data_voce_cover
    ON dbo.DATIPRIMANOTA (SITE, DATA, CATEGORIA, VOCE)
    INCLUDE (TIPO, VALORE);
END;

IF OBJECT_ID('dbo.SPESE', 'U') IS NOT NULL
   AND NOT EXISTS (
      SELECT 1
      FROM sys.indexes
      WHERE object_id = OBJECT_ID('dbo.SPESE')
        AND name = 'IX_SPESE_site_data_cover'
   )
   AND COL_LENGTH('dbo.SPESE', 'SITE') IS NOT NULL
   AND COL_LENGTH('dbo.SPESE', 'DATA') IS NOT NULL
   AND COL_LENGTH('dbo.SPESE', 'TIPO') IS NOT NULL
   AND COL_LENGTH('dbo.SPESE', 'FORNITORE') IS NOT NULL
   AND COL_LENGTH('dbo.SPESE', 'DOCUMENTO') IS NOT NULL
   AND COL_LENGTH('dbo.SPESE', 'IMPORTO') IS NOT NULL
BEGIN
  CREATE INDEX IX_SPESE_site_data_cover
    ON dbo.SPESE (SITE, DATA)
    INCLUDE (TIPO, FORNITORE, DOCUMENTO, IMPORTO);
END;

IF OBJECT_ID('dbo.VERSAMENTI_APP', 'U') IS NOT NULL
   AND NOT EXISTS (
      SELECT 1
      FROM sys.indexes
      WHERE object_id = OBJECT_ID('dbo.VERSAMENTI_APP')
        AND name = 'IX_VERSAMENTI_APP_site_data_cover'
   )
   AND COL_LENGTH('dbo.VERSAMENTI_APP', 'SITE') IS NOT NULL
   AND COL_LENGTH('dbo.VERSAMENTI_APP', 'DATA_VERSAMENTO') IS NOT NULL
   AND COL_LENGTH('dbo.VERSAMENTI_APP', 'DAL') IS NOT NULL
   AND COL_LENGTH('dbo.VERSAMENTI_APP', 'AL') IS NOT NULL
   AND COL_LENGTH('dbo.VERSAMENTI_APP', 'NOME_COGNOME') IS NOT NULL
   AND COL_LENGTH('dbo.VERSAMENTI_APP', 'TIPO') IS NOT NULL
   AND COL_LENGTH('dbo.VERSAMENTI_APP', 'TESSERA') IS NOT NULL
   AND COL_LENGTH('dbo.VERSAMENTI_APP', 'RIFERIMENTO') IS NOT NULL
   AND COL_LENGTH('dbo.VERSAMENTI_APP', 'VALORE') IS NOT NULL
BEGIN
  CREATE INDEX IX_VERSAMENTI_APP_site_data_cover
    ON dbo.VERSAMENTI_APP (SITE, DATA_VERSAMENTO)
    INCLUDE (DAL, AL, NOME_COGNOME, TIPO, TESSERA, RIFERIMENTO, VALORE);
END;

IF OBJECT_ID('dbo.DatiDelivery', 'U') IS NOT NULL
   AND NOT EXISTS (
      SELECT 1
      FROM sys.indexes
      WHERE object_id = OBJECT_ID('dbo.DatiDelivery')
        AND name = 'IX_DatiDelivery_site_data_cover'
   )
   AND COL_LENGTH('dbo.DatiDelivery', 'Site') IS NOT NULL
   AND COL_LENGTH('dbo.DatiDelivery', 'Data') IS NOT NULL
   AND COL_LENGTH('dbo.DatiDelivery', 'Fornitore') IS NOT NULL
   AND COL_LENGTH('dbo.DatiDelivery', 'GRUPPO') IS NOT NULL
   AND COL_LENGTH('dbo.DatiDelivery', 'VALORE') IS NOT NULL
   AND COL_LENGTH('dbo.DatiDelivery', 'Codice') IS NOT NULL
   AND COL_LENGTH('dbo.DatiDelivery', 'Descrizione') IS NOT NULL
BEGIN
  CREATE INDEX IX_DatiDelivery_site_data_cover
    ON dbo.DatiDelivery (Site, Data)
    INCLUDE (Fornitore, GRUPPO, VALORE, Codice, Descrizione);
END;

IF OBJECT_ID('dbo.datiinventario', 'U') IS NOT NULL
   AND NOT EXISTS (
      SELECT 1
      FROM sys.indexes
      WHERE object_id = OBJECT_ID('dbo.datiinventario')
        AND name = 'IX_datiinventario_site_data_cover'
   )
   AND COL_LENGTH('dbo.datiinventario', 'Site') IS NOT NULL
   AND COL_LENGTH('dbo.datiinventario', 'Data') IS NOT NULL
   AND COL_LENGTH('dbo.datiinventario', 'TipoTrans') IS NOT NULL
   AND COL_LENGTH('dbo.datiinventario', 'Fornitore') IS NOT NULL
   AND COL_LENGTH('dbo.datiinventario', 'GRUPPO') IS NOT NULL
   AND COL_LENGTH('dbo.datiinventario', 'TOTEURO') IS NOT NULL
   AND COL_LENGTH('dbo.datiinventario', 'Codice') IS NOT NULL
   AND COL_LENGTH('dbo.datiinventario', 'Descrizione') IS NOT NULL
BEGIN
  CREATE INDEX IX_datiinventario_site_data_cover
    ON dbo.datiinventario (Site, Data, TipoTrans)
    INCLUDE (Fornitore, GRUPPO, TOTEURO, Codice, Descrizione);
END;

IF OBJECT_ID('dbo.DatiTX', 'U') IS NOT NULL
   AND NOT EXISTS (
      SELECT 1
      FROM sys.indexes
      WHERE object_id = OBJECT_ID('dbo.DatiTX')
        AND name = 'IX_DatiTX_site_data_cover'
   )
   AND COL_LENGTH('dbo.DatiTX', 'Site') IS NOT NULL
   AND COL_LENGTH('dbo.DatiTX', 'Data') IS NOT NULL
   AND COL_LENGTH('dbo.DatiTX', 'TipoTrans') IS NOT NULL
   AND COL_LENGTH('dbo.DatiTX', 'SITE2') IS NOT NULL
   AND COL_LENGTH('dbo.DatiTX', 'Fornitore') IS NOT NULL
   AND COL_LENGTH('dbo.DatiTX', 'GRUPPO') IS NOT NULL
   AND COL_LENGTH('dbo.DatiTX', 'TOTEURO') IS NOT NULL
   AND COL_LENGTH('dbo.DatiTX', 'Codice') IS NOT NULL
   AND COL_LENGTH('dbo.DatiTX', 'Descrizione') IS NOT NULL
BEGIN
  CREATE INDEX IX_DatiTX_site_data_cover
    ON dbo.DatiTX (Site, Data, TipoTrans)
    INCLUDE (SITE2, Fornitore, GRUPPO, TOTEURO, Codice, Descrizione);
END;

IF OBJECT_ID('dbo.DatiTX', 'U') IS NOT NULL
   AND NOT EXISTS (
      SELECT 1
      FROM sys.indexes
      WHERE object_id = OBJECT_ID('dbo.DatiTX')
        AND name = 'IX_DatiTX_site2_data_cover'
   )
   AND COL_LENGTH('dbo.DatiTX', 'SITE2') IS NOT NULL
   AND COL_LENGTH('dbo.DatiTX', 'Data') IS NOT NULL
   AND COL_LENGTH('dbo.DatiTX', 'TipoTrans') IS NOT NULL
   AND COL_LENGTH('dbo.DatiTX', 'Site') IS NOT NULL
   AND COL_LENGTH('dbo.DatiTX', 'Fornitore') IS NOT NULL
   AND COL_LENGTH('dbo.DatiTX', 'GRUPPO') IS NOT NULL
   AND COL_LENGTH('dbo.DatiTX', 'TOTEURO') IS NOT NULL
   AND COL_LENGTH('dbo.DatiTX', 'Codice') IS NOT NULL
   AND COL_LENGTH('dbo.DatiTX', 'Descrizione') IS NOT NULL
BEGIN
  CREATE INDEX IX_DatiTX_site2_data_cover
    ON dbo.DatiTX (SITE2, Data, TipoTrans)
    INCLUDE (Site, Fornitore, GRUPPO, TOTEURO, Codice, Descrizione);
END;
"""
    with _conn(read_only=False) as conn:
        cur = conn.cursor()
        cur.execute(sql)
        conn.commit()
