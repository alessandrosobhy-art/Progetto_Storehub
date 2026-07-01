IF OBJECT_ID('dbo.OrdiniFornitori','U') IS NULL
BEGIN
  CREATE TABLE dbo.OrdiniFornitori (
    row_uuid UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID() PRIMARY KEY,
    numero_ordine NVARCHAR(50) NOT NULL,
    site NVARCHAR(50) NOT NULL,
    store_name NVARCHAR(255) NULL,
    supplier_row_uuid UNIQUEIDENTIFIER NOT NULL,
    supplier_name NVARCHAR(255) NOT NULL,
    supplier_contact_row_uuid UNIQUEIDENTIFIER NULL,
    order_date DATE NOT NULL,
    requested_delivery_date DATE NOT NULL,
    order_mode NVARCHAR(20) NOT NULL,
    status NVARCHAR(30) NOT NULL,
    note_ordine NVARCHAR(MAX) NULL,
    total_estimated DECIMAL(18,2) NOT NULL DEFAULT 0,
    viewed_at DATETIME2 NULL,
    completed_at DATETIME2 NULL,
    archived_at DATETIME2 NULL,
    reopened_at DATETIME2 NULL,
    sent_email_at DATETIME2 NULL,
    pdf_rel_path NVARCHAR(500) NULL,
    pdf_filename NVARCHAR(255) NULL,
    ddt_supplier_code NVARCHAR(100) NULL,
    ddt_data_doc NVARCHAR(20) NULL,
    ddt_data_rif NVARCHAR(20) NULL,
    ddt_numero NVARCHAR(100) NULL,
    ddt_created_at DATETIME2 NULL,
    created_by NVARCHAR(100) NULL,
    created_by_name NVARCHAR(255) NULL,
    created_by_email NVARCHAR(255) NULL,
    updated_by NVARCHAR(100) NULL,
    created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    updated_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
  );
  CREATE UNIQUE INDEX UX_OrdiniFornitori_numero ON dbo.OrdiniFornitori(numero_ordine);
  CREATE INDEX IX_OrdiniFornitori_site ON dbo.OrdiniFornitori(site, order_date DESC);
  CREATE INDEX IX_OrdiniFornitori_supplier ON dbo.OrdiniFornitori(supplier_row_uuid, status, requested_delivery_date);
END;

IF OBJECT_ID('dbo.OrdiniFornitoriRighe','U') IS NULL
BEGIN
  CREATE TABLE dbo.OrdiniFornitoriRighe (
    row_uuid UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID() PRIMARY KEY,
    order_row_uuid UNIQUEIDENTIFIER NOT NULL,
    sort_order INT NOT NULL DEFAULT 0,
    tipo_listino NVARCHAR(100) NULL,
    product_code NVARCHAR(100) NULL,
    descrizione NVARCHAR(255) NOT NULL,
    gruppo NVARCHAR(100) NULL,
    unita NVARCHAR(50) NULL,
    qta_car INT NULL,
    qta_int INT NULL,
    conv DECIMAL(18,4) NULL,
    qty_colli DECIMAL(18,4) NULL,
    qty_pezzi DECIMAL(18,4) NULL,
    qty_ordered DECIMAL(18,4) NOT NULL DEFAULT 0,
    estimated_price DECIMAL(18,4) NULL,
    price_source NVARCHAR(20) NULL,
    subtotal DECIMAL(18,2) NOT NULL DEFAULT 0,
    picked BIT NOT NULL DEFAULT 0,
    lotto NVARCHAR(100) NULL,
    scadenza DATE NULL,
    note_fornitore NVARCHAR(MAX) NULL,
    picked_at DATETIME2 NULL,
    updated_by_supplier_at DATETIME2 NULL,
    supplier_added BIT NOT NULL DEFAULT 0,
    replacement_for_row_uuid UNIQUEIDENTIFIER NULL,
    created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    updated_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
  );
  CREATE INDEX IX_OrdiniFornitoriRighe_order ON dbo.OrdiniFornitoriRighe(order_row_uuid, sort_order, created_at);
END;

IF OBJECT_ID('dbo.OrdiniFornitoriLog','U') IS NULL
BEGIN
  CREATE TABLE dbo.OrdiniFornitoriLog (
    row_uuid UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID() PRIMARY KEY,
    order_row_uuid UNIQUEIDENTIFIER NOT NULL,
    old_status NVARCHAR(30) NULL,
    new_status NVARCHAR(30) NOT NULL,
    event_type NVARCHAR(50) NOT NULL,
    note NVARCHAR(MAX) NULL,
    actor_uid NVARCHAR(100) NULL,
    actor_name NVARCHAR(255) NULL,
    actor_email NVARCHAR(255) NULL,
    actor_role NVARCHAR(50) NULL,
    created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
  );
  CREATE INDEX IX_OrdiniFornitoriLog_order ON dbo.OrdiniFornitoriLog(order_row_uuid, created_at DESC);
END;

IF OBJECT_ID('dbo.FornitoriUtenti','U') IS NULL
BEGIN
  CREATE TABLE dbo.FornitoriUtenti (
    row_uuid UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID() PRIMARY KEY,
    user_uid NVARCHAR(100) NOT NULL,
    supplier_row_uuid UNIQUEIDENTIFIER NOT NULL,
    created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
  );
  CREATE UNIQUE INDEX UX_FornitoriUtenti_pair ON dbo.FornitoriUtenti(user_uid, supplier_row_uuid);
  CREATE INDEX IX_FornitoriUtenti_user ON dbo.FornitoriUtenti(user_uid);
END;
