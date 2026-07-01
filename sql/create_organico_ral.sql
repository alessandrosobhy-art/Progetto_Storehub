IF OBJECT_ID('dbo.ORGANICO_RAL', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.ORGANICO_RAL (
        id BIGINT IDENTITY(1,1) NOT NULL
            CONSTRAINT PK_ORGANICO_RAL PRIMARY KEY,
        row_uuid UNIQUEIDENTIFIER NOT NULL
            CONSTRAINT DF_ORGANICO_RAL_row_uuid DEFAULT NEWID(),

        codice_azienda NVARCHAR(20) NOT NULL,
        denominazione NVARCHAR(255) NULL,
        data_inizio_periodo DATE NOT NULL,
        data_fine_periodo DATE NOT NULL,
        dipendente NVARCHAR(30) NOT NULL,
        cognome NVARCHAR(100) NULL,
        nome NVARCHAR(100) NULL,
        codice_fiscale NVARCHAR(32) NULL,
        data_assunzione DATE NULL,
        data_cessazione DATE NULL,
        filiale NVARCHAR(255) NULL,
        centro_di_costo NVARCHAR(255) NULL,
        importo_elemento_di_paga DECIMAL(18, 5) NULL,
        ral DECIMAL(18, 5) NULL,
        cod_contratto NVARCHAR(100) NULL,
        natura_rapporto NVARCHAR(100) NULL,
        percentuale_part_time DECIMAL(7, 2) NULL,
        codice_presenze NVARCHAR(30) NULL,

        source_file_name NVARCHAR(255) NULL,
        source_sheet_name NVARCHAR(128) NULL,
        source_row_num INT NULL,
        created_at DATETIME2(0) NOT NULL
            CONSTRAINT DF_ORGANICO_RAL_created_at DEFAULT SYSUTCDATETIME(),
        updated_at DATETIME2(0) NOT NULL
            CONSTRAINT DF_ORGANICO_RAL_updated_at DEFAULT SYSUTCDATETIME()
    );
END;
GO

IF NOT EXISTS (
    SELECT 1
    FROM sys.indexes
    WHERE object_id = OBJECT_ID(N'dbo.ORGANICO_RAL')
      AND name = N'UX_ORGANICO_RAL_natural_key'
)
BEGIN
    CREATE UNIQUE INDEX UX_ORGANICO_RAL_natural_key
        ON dbo.ORGANICO_RAL (
            codice_azienda,
            dipendente,
            data_inizio_periodo,
            data_fine_periodo
        );
END;
GO

IF NOT EXISTS (
    SELECT 1
    FROM sys.indexes
    WHERE object_id = OBJECT_ID(N'dbo.ORGANICO_RAL')
      AND name = N'UX_ORGANICO_RAL_row_uuid'
)
BEGIN
    CREATE UNIQUE INDEX UX_ORGANICO_RAL_row_uuid
        ON dbo.ORGANICO_RAL (row_uuid);
END;
GO

IF NOT EXISTS (
    SELECT 1
    FROM sys.indexes
    WHERE object_id = OBJECT_ID(N'dbo.ORGANICO_RAL')
      AND name = N'IX_ORGANICO_RAL_period'
)
BEGIN
    CREATE INDEX IX_ORGANICO_RAL_period
        ON dbo.ORGANICO_RAL (data_fine_periodo, filiale, centro_di_costo);
END;
GO

CREATE OR ALTER PROCEDURE dbo.usp_upsert_organico_ral
    @codice_azienda NVARCHAR(20),
    @denominazione NVARCHAR(255) = NULL,
    @data_inizio_periodo DATE,
    @data_fine_periodo DATE,
    @dipendente NVARCHAR(30),
    @cognome NVARCHAR(100) = NULL,
    @nome NVARCHAR(100) = NULL,
    @codice_fiscale NVARCHAR(32) = NULL,
    @data_assunzione DATE = NULL,
    @data_cessazione DATE = NULL,
    @filiale NVARCHAR(255) = NULL,
    @centro_di_costo NVARCHAR(255) = NULL,
    @importo_elemento_di_paga DECIMAL(18, 5) = NULL,
    @ral DECIMAL(18, 5) = NULL,
    @cod_contratto NVARCHAR(100) = NULL,
    @natura_rapporto NVARCHAR(100) = NULL,
    @percentuale_part_time DECIMAL(7, 2) = NULL,
    @codice_presenze NVARCHAR(30) = NULL,
    @source_file_name NVARCHAR(255) = NULL,
    @source_sheet_name NVARCHAR(128) = NULL,
    @source_row_num INT = NULL
AS
BEGIN
    SET NOCOUNT ON;

    MERGE dbo.ORGANICO_RAL WITH (HOLDLOCK) AS tgt
    USING (
        SELECT
            @codice_azienda AS codice_azienda,
            @dipendente AS dipendente,
            @data_inizio_periodo AS data_inizio_periodo,
            @data_fine_periodo AS data_fine_periodo
    ) AS src
      ON tgt.codice_azienda = src.codice_azienda
     AND tgt.dipendente = src.dipendente
     AND tgt.data_inizio_periodo = src.data_inizio_periodo
     AND tgt.data_fine_periodo = src.data_fine_periodo
    WHEN MATCHED THEN
        UPDATE SET
            denominazione = @denominazione,
            cognome = @cognome,
            nome = @nome,
            codice_fiscale = @codice_fiscale,
            data_assunzione = @data_assunzione,
            data_cessazione = @data_cessazione,
            filiale = @filiale,
            centro_di_costo = @centro_di_costo,
            importo_elemento_di_paga = @importo_elemento_di_paga,
            ral = @ral,
            cod_contratto = @cod_contratto,
            natura_rapporto = @natura_rapporto,
            percentuale_part_time = @percentuale_part_time,
            codice_presenze = @codice_presenze,
            source_file_name = @source_file_name,
            source_sheet_name = @source_sheet_name,
            source_row_num = @source_row_num,
            updated_at = SYSUTCDATETIME()
    WHEN NOT MATCHED THEN
        INSERT (
            codice_azienda,
            denominazione,
            data_inizio_periodo,
            data_fine_periodo,
            dipendente,
            cognome,
            nome,
            codice_fiscale,
            data_assunzione,
            data_cessazione,
            filiale,
            centro_di_costo,
            importo_elemento_di_paga,
            ral,
            cod_contratto,
            natura_rapporto,
            percentuale_part_time,
            codice_presenze,
            source_file_name,
            source_sheet_name,
            source_row_num
        )
        VALUES (
            @codice_azienda,
            @denominazione,
            @data_inizio_periodo,
            @data_fine_periodo,
            @dipendente,
            @cognome,
            @nome,
            @codice_fiscale,
            @data_assunzione,
            @data_cessazione,
            @filiale,
            @centro_di_costo,
            @importo_elemento_di_paga,
            @ral,
            @cod_contratto,
            @natura_rapporto,
            @percentuale_part_time,
            @codice_presenze,
            @source_file_name,
            @source_sheet_name,
            @source_row_num
        );
END;
GO
