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
END;

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
END;

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
END;

IF COL_LENGTH('dbo.Fornitori', 'TipoOrdine') IS NULL
BEGIN
  ALTER TABLE dbo.Fornitori ADD TipoOrdine NVARCHAR(20) NULL;
END;

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
END;

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
END;

IF NOT EXISTS (SELECT 1 FROM dbo.ListinoTipi WHERE tipo_listino = 'FoodPaper')
  INSERT INTO dbo.ListinoTipi (tipo_listino, sort_order, is_active) VALUES ('FoodPaper', 10, 1);
IF NOT EXISTS (SELECT 1 FROM dbo.ListinoTipi WHERE tipo_listino = 'Operating')
  INSERT INTO dbo.ListinoTipi (tipo_listino, sort_order, is_active) VALUES ('Operating', 20, 1);

IF NOT EXISTS (SELECT 1 FROM dbo.ListinoGruppi WHERE gruppo = 'FOOD')
  INSERT INTO dbo.ListinoGruppi (gruppo, sort_order, is_active) VALUES ('FOOD', 10, 1);
IF NOT EXISTS (SELECT 1 FROM dbo.ListinoGruppi WHERE gruppo = 'PAPER')
  INSERT INTO dbo.ListinoGruppi (gruppo, sort_order, is_active) VALUES ('PAPER', 20, 1);
IF NOT EXISTS (SELECT 1 FROM dbo.ListinoGruppi WHERE gruppo = 'OPERATING')
  INSERT INTO dbo.ListinoGruppi (gruppo, sort_order, is_active) VALUES ('OPERATING', 30, 1);
