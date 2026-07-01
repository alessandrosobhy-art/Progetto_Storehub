param(
  [string]$ComputerName = "10.24.1.1",
  [string]$DeployScriptRoot = "C:\Users\aless\Desktop\Progetto_Calendar\scripts"
)

$ErrorActionPreference = "Stop"

function Get-CredentialStorePath {
  param([string]$Name)
  $safeName = ($Name -replace '[^a-zA-Z0-9\.-]', '_')
  return Join-Path (Join-Path $DeployScriptRoot "credentials") "$safeName.json"
}

function Get-CredentialCryptoKey {
  $seed = [Text.Encoding]::UTF8.GetBytes("CodexDeploySharedKey::Progetto_Calendar::2026")
  $hash = [System.Security.Cryptography.SHA256]::Create()
  try {
    return $hash.ComputeHash($seed)
  } finally {
    $hash.Dispose()
  }
}

function Load-DeployCredential {
  param([string]$Name)
  $path = Get-CredentialStorePath -Name $Name
  if (-not (Test-Path -LiteralPath $path)) {
    throw "Credenziali salvate non trovate: $path"
  }
  $raw = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
  $secure = ConvertTo-SecureString -String ([string]$raw.password) -Key (Get-CredentialCryptoKey)
  return New-Object System.Management.Automation.PSCredential ([string]$raw.username), $secure
}

$cred = Load-DeployCredential -Name $ComputerName

Invoke-Command -ComputerName $ComputerName -Credential $cred -ScriptBlock {
  $ErrorActionPreference = "Continue"
  $sqlcmd = Get-Command sqlcmd.exe -ErrorAction SilentlyContinue
  if (-not $sqlcmd) {
    Write-Host "sqlcmd.exe non trovato."
    return
  }

  Write-Host "=== SQL DATABASE LOG STATUS ==="
  & sqlcmd.exe -S ".\SQLEXPRESS" -E -W -Q "
SET NOCOUNT ON;
SELECT
  d.name AS database_name,
  d.recovery_model_desc,
  d.log_reuse_wait_desc,
  CAST(SUM(CASE WHEN mf.type_desc='ROWS' THEN mf.size ELSE 0 END) * 8.0 / 1024 AS DECIMAL(18,2)) AS data_mb,
  CAST(SUM(CASE WHEN mf.type_desc='LOG' THEN mf.size ELSE 0 END) * 8.0 / 1024 AS DECIMAL(18,2)) AS log_mb,
  MAX(CASE WHEN mf.type_desc='LOG' THEN mf.name END) AS log_file_name
FROM sys.databases d
JOIN sys.master_files mf ON mf.database_id = d.database_id
GROUP BY d.name, d.recovery_model_desc, d.log_reuse_wait_desc
ORDER BY log_mb DESC;
"

  Write-Host "=== ACTIVE REQUESTS ==="
  & sqlcmd.exe -S ".\SQLEXPRESS" -E -W -Q "
SET NOCOUNT ON;
SELECT TOP 30
  r.session_id,
  DB_NAME(r.database_id) AS database_name,
  r.status,
  r.command,
  r.blocking_session_id,
  r.wait_type,
  r.wait_time,
  r.cpu_time,
  r.total_elapsed_time,
  r.reads,
  r.writes,
  r.logical_reads,
  s.host_name,
  s.program_name,
  s.login_name,
  SUBSTRING(t.text, (r.statement_start_offset/2)+1,
    CASE WHEN r.statement_end_offset = -1
      THEN LEN(CONVERT(nvarchar(max), t.text))
      ELSE (r.statement_end_offset-r.statement_start_offset)/2+1
    END) AS running_statement
FROM sys.dm_exec_requests r
JOIN sys.dm_exec_sessions s ON s.session_id = r.session_id
OUTER APPLY sys.dm_exec_sql_text(r.sql_handle) t
WHERE r.session_id <> @@SPID
ORDER BY r.total_elapsed_time DESC;
"

  Write-Host "=== OPEN TRANSACTIONS BY SESSION ==="
  & sqlcmd.exe -S ".\SQLEXPRESS" -E -W -Q "
SET NOCOUNT ON;
SELECT TOP 50
  s.session_id,
  s.status,
  s.open_transaction_count,
  DB_NAME(dt.database_id) AS database_name,
  at.transaction_begin_time,
  DATEDIFF(second, at.transaction_begin_time, SYSDATETIME()) AS open_seconds,
  at.transaction_type,
  at.transaction_state,
  s.host_name,
  s.program_name,
  s.login_name,
  c.client_net_address,
  txt.text AS last_sql_text
FROM sys.dm_tran_session_transactions st
JOIN sys.dm_tran_active_transactions at ON at.transaction_id = st.transaction_id
JOIN sys.dm_tran_database_transactions dt ON dt.transaction_id = st.transaction_id
JOIN sys.dm_exec_sessions s ON s.session_id = st.session_id
LEFT JOIN sys.dm_exec_connections c ON c.session_id = s.session_id
OUTER APPLY sys.dm_exec_sql_text(c.most_recent_sql_handle) txt
ORDER BY open_seconds DESC;
"

  Write-Host "=== BLOCKING CHAINS ==="
  & sqlcmd.exe -S ".\SQLEXPRESS" -E -W -Q "
SET NOCOUNT ON;
SELECT
  r.session_id AS waiting_session_id,
  r.blocking_session_id,
  DB_NAME(r.database_id) AS database_name,
  r.wait_type,
  r.wait_time,
  r.total_elapsed_time,
  s.host_name,
  s.program_name,
  SUBSTRING(t.text, (r.statement_start_offset/2)+1,
    CASE WHEN r.statement_end_offset = -1
      THEN LEN(CONVERT(nvarchar(max), t.text))
      ELSE (r.statement_end_offset-r.statement_start_offset)/2+1
    END) AS waiting_statement
FROM sys.dm_exec_requests r
JOIN sys.dm_exec_sessions s ON s.session_id = r.session_id
OUTER APPLY sys.dm_exec_sql_text(r.sql_handle) t
WHERE r.blocking_session_id <> 0
ORDER BY r.wait_time DESC;
"

  Write-Host "=== TOP SESSIONS BY CPU/MEMORY ==="
  & sqlcmd.exe -S ".\SQLEXPRESS" -E -W -Q "
SET NOCOUNT ON;
SELECT TOP 30
  s.session_id,
  s.status,
  s.open_transaction_count,
  s.cpu_time,
  s.memory_usage,
  s.reads,
  s.writes,
  s.logical_reads,
  s.host_name,
  s.program_name,
  s.login_name,
  c.client_net_address,
  txt.text AS most_recent_sql_text
FROM sys.dm_exec_sessions s
LEFT JOIN sys.dm_exec_connections c ON c.session_id = s.session_id
OUTER APPLY sys.dm_exec_sql_text(c.most_recent_sql_handle) txt
WHERE s.is_user_process = 1
ORDER BY s.cpu_time DESC, s.logical_reads DESC;
"

  Write-Host "=== IPRATICO TABLE COUNTS ==="
  & sqlcmd.exe -S ".\SQLEXPRESS" -E -W -d "ipratico" -Q "
SET NOCOUNT ON;
SELECT TOP 20
  s.name + '.' + t.name AS table_name,
  SUM(p.rows) AS rows_count,
  CAST(SUM(a.total_pages) * 8.0 / 1024 AS DECIMAL(18,2)) AS total_mb
FROM sys.tables t
JOIN sys.schemas s ON s.schema_id = t.schema_id
JOIN sys.indexes i ON t.object_id = i.object_id
JOIN sys.partitions p ON i.object_id = p.object_id AND i.index_id = p.index_id
JOIN sys.allocation_units a ON p.partition_id = a.container_id
GROUP BY s.name, t.name
ORDER BY total_mb DESC;
"
}
