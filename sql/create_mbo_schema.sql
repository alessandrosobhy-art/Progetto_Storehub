IF OBJECT_ID('dbo.MboAreaManagers','U') IS NULL
BEGIN
  CREATE TABLE dbo.MboAreaManagers (
    row_uuid UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID() PRIMARY KEY,
    manager_name NVARCHAR(255) NOT NULL,
    ilp_am_value NVARCHAR(255) NULL,
    sort_order INT NOT NULL DEFAULT 0,
    is_active BIT NOT NULL DEFAULT 1,
    created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    updated_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
  );
  CREATE UNIQUE INDEX UX_MboAreaManagers_manager_name ON dbo.MboAreaManagers(manager_name);
  CREATE UNIQUE INDEX UX_MboAreaManagers_ilp_am_value ON dbo.MboAreaManagers(ilp_am_value) WHERE ilp_am_value IS NOT NULL;
END
GO

IF OBJECT_ID('dbo.MboStoreAreaManagerMonthly','U') IS NULL
BEGIN
  CREATE TABLE dbo.MboStoreAreaManagerMonthly (
    row_uuid UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID() PRIMARY KEY,
    store_code NVARCHAR(50) NOT NULL,
    year_month CHAR(7) NOT NULL,
    area_manager_row_uuid UNIQUEIDENTIFIER NULL,
    created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    updated_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
  );
  CREATE UNIQUE INDEX UX_MboStoreAreaManagerMonthly_store_month
    ON dbo.MboStoreAreaManagerMonthly(store_code, year_month);
  CREATE INDEX IX_MboStoreAreaManagerMonthly_month
    ON dbo.MboStoreAreaManagerMonthly(year_month, store_code);
END
GO

IF OBJECT_ID('dbo.MboAuditImports','U') IS NULL
BEGIN
  CREATE TABLE dbo.MboAuditImports (
    import_uuid UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID() PRIMARY KEY,
    source_filename NVARCHAR(255) NULL,
    imported_by NVARCHAR(255) NULL,
    rows_total INT NOT NULL DEFAULT 0,
    rows_imported INT NOT NULL DEFAULT 0,
    created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
  );
END
GO

IF OBJECT_ID('dbo.MboAuditRows','U') IS NULL
BEGIN
  CREATE TABLE dbo.MboAuditRows (
    row_uuid UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID() PRIMARY KEY,
    import_uuid UNIQUEIDENTIFIER NULL,
    source_key NVARCHAR(80) NOT NULL,
    mission_data_id NVARCHAR(80) NULL,
    mission_id NVARCHAR(80) NULL,
    mission_title NVARCHAR(255) NULL,
    address NVARCHAR(1000) NOT NULL,
    address_norm NVARCHAR(450) NOT NULL,
    audit_date DATE NOT NULL,
    audit_year_month CHAR(7) NOT NULL,
    score DECIMAL(9,4) NULL,
    matched_store_code NVARCHAR(50) NULL,
    assigned_store_code NVARCHAR(50) NULL,
    assigned_year_month CHAR(7) NULL,
    ignored BIT NOT NULL DEFAULT 0,
    username NVARCHAR(255) NULL,
    user_full_name NVARCHAR(255) NULL,
    raw_json NVARCHAR(MAX) NULL,
    created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    updated_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
  );
  CREATE UNIQUE INDEX UX_MboAuditRows_source_key ON dbo.MboAuditRows(source_key);
  CREATE INDEX IX_MboAuditRows_store_month ON dbo.MboAuditRows(assigned_store_code, assigned_year_month);
  CREATE INDEX IX_MboAuditRows_audit_month ON dbo.MboAuditRows(audit_year_month);
END
GO

IF OBJECT_ID('dbo.MboAuditRows','U') IS NOT NULL
   AND COL_LENGTH('dbo.MboAuditRows', 'ignored') IS NULL
BEGIN
  ALTER TABLE dbo.MboAuditRows ADD ignored BIT NOT NULL CONSTRAINT DF_MboAuditRows_ignored DEFAULT 0;
END
GO

IF OBJECT_ID('dbo.MboAuditAddressStoreMap','U') IS NULL
BEGIN
  CREATE TABLE dbo.MboAuditAddressStoreMap (
    address_norm NVARCHAR(450) NOT NULL PRIMARY KEY,
    address_sample NVARCHAR(1000) NOT NULL,
    store_code NVARCHAR(50) NOT NULL,
    created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    updated_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
  );
  CREATE INDEX IX_MboAuditAddressStoreMap_store ON dbo.MboAuditAddressStoreMap(store_code);
END
GO

IF OBJECT_ID('dbo.MboAuditManualMonthlyValues','U') IS NULL
BEGIN
  CREATE TABLE dbo.MboAuditManualMonthlyValues (
    row_uuid UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID() PRIMARY KEY,
    store_code NVARCHAR(50) NOT NULL,
    year_month CHAR(7) NOT NULL,
    score DECIMAL(9,4) NOT NULL DEFAULT 0,
    note NVARCHAR(500) NULL,
    created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    updated_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
  );
  CREATE UNIQUE INDEX UX_MboAuditManualMonthlyValues_store_month
    ON dbo.MboAuditManualMonthlyValues(store_code, year_month);
END
GO

IF OBJECT_ID('dbo.MboGoogleManualMonthlyValues','U') IS NULL
BEGIN
  CREATE TABLE dbo.MboGoogleManualMonthlyValues (
    row_uuid UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID() PRIMARY KEY,
    store_code NVARCHAR(50) NOT NULL,
    year_month CHAR(7) NOT NULL,
    rating DECIMAL(9,4) NULL,
    note NVARCHAR(500) NULL,
    created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    updated_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
  );
  CREATE UNIQUE INDEX UX_MboGoogleManualMonthlyValues_store_month
    ON dbo.MboGoogleManualMonthlyValues(store_code, year_month);
END
GO

IF OBJECT_ID('dbo.MboGlovoManualMonthlyValues','U') IS NULL
BEGIN
  CREATE TABLE dbo.MboGlovoManualMonthlyValues (
    row_uuid UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID() PRIMARY KEY,
    store_code NVARCHAR(50) NOT NULL,
    year_month CHAR(7) NOT NULL,
    value_pct DECIMAL(9,4) NULL,
    note NVARCHAR(500) NULL,
    created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    updated_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
  );
  CREATE UNIQUE INDEX UX_MboGlovoManualMonthlyValues_store_month
    ON dbo.MboGlovoManualMonthlyValues(store_code, year_month);
END
GO

IF OBJECT_ID('dbo.MboDeliverooManualMonthlyValues','U') IS NULL
BEGIN
  CREATE TABLE dbo.MboDeliverooManualMonthlyValues (
    row_uuid UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID() PRIMARY KEY,
    store_code NVARCHAR(50) NOT NULL,
    year_month CHAR(7) NOT NULL,
    rating DECIMAL(9,4) NULL,
    note NVARCHAR(500) NULL,
    created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    updated_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
  );
  CREATE UNIQUE INDEX UX_MboDeliverooManualMonthlyValues_store_month
    ON dbo.MboDeliverooManualMonthlyValues(store_code, year_month);
END

IF OBJECT_ID('dbo.MboSoftSkillPeriods','U') IS NULL
BEGIN
  CREATE TABLE dbo.MboSoftSkillPeriods (
    period_uuid UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID() PRIMARY KEY,
    token NVARCHAR(80) NOT NULL,
    period_label NVARCHAR(255) NOT NULL,
    start_year_month CHAR(7) NOT NULL,
    end_year_month CHAR(7) NOT NULL,
    is_active BIT NOT NULL DEFAULT 1,
    created_by NVARCHAR(255) NULL,
    created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    updated_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
  );
  CREATE UNIQUE INDEX UX_MboSoftSkillPeriods_token ON dbo.MboSoftSkillPeriods(token);
  CREATE INDEX IX_MboSoftSkillPeriods_months ON dbo.MboSoftSkillPeriods(start_year_month, end_year_month);
END

IF OBJECT_ID('dbo.MboSoftSkillSubmissions','U') IS NULL
BEGIN
  CREATE TABLE dbo.MboSoftSkillSubmissions (
    submission_uuid UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID() PRIMARY KEY,
    group_uuid UNIQUEIDENTIFIER NULL,
    period_uuid UNIQUEIDENTIFIER NOT NULL,
    full_name NVARCHAR(255) NOT NULL,
    role NVARCHAR(50) NOT NULL,
    store_code NVARCHAR(50) NOT NULL,
    answers_json NVARCHAR(MAX) NOT NULL,
    computed_json NVARCHAR(MAX) NULL,
    final_score DECIMAL(9,4) NULL,
    created_by NVARCHAR(255) NULL,
    updated_by NVARCHAR(255) NULL,
    created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    updated_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    deleted_at DATETIME2 NULL
  );
  CREATE INDEX IX_MboSoftSkillSubmissions_period ON dbo.MboSoftSkillSubmissions(period_uuid, deleted_at);
  CREATE INDEX IX_MboSoftSkillSubmissions_store ON dbo.MboSoftSkillSubmissions(store_code);
  CREATE INDEX IX_MboSoftSkillSubmissions_group ON dbo.MboSoftSkillSubmissions(group_uuid);
END

IF OBJECT_ID('dbo.MboSoftSkillSubmissions','U') IS NOT NULL
   AND COL_LENGTH('dbo.MboSoftSkillSubmissions', 'group_uuid') IS NULL
BEGIN
  ALTER TABLE dbo.MboSoftSkillSubmissions ADD group_uuid UNIQUEIDENTIFIER NULL;
  CREATE INDEX IX_MboSoftSkillSubmissions_group ON dbo.MboSoftSkillSubmissions(group_uuid);
END
GO
