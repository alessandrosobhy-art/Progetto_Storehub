param(
  [string]$ComputerName = "10.24.1.1",
  [string]$DeployScriptRoot = "C:\Users\aless\Desktop\Progetto_Calendar\scripts",
  [int]$SampleSeconds = 8
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
  try { return $hash.ComputeHash($seed) } finally { $hash.Dispose() }
}

function Load-DeployCredential {
  param([string]$Name)
  $path = Get-CredentialStorePath -Name $Name
  if (-not (Test-Path -LiteralPath $path)) { throw "Credenziali salvate non trovate: $path" }
  $raw = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
  $secure = ConvertTo-SecureString -String ([string]$raw.password) -Key (Get-CredentialCryptoKey)
  return New-Object System.Management.Automation.PSCredential ([string]$raw.username), $secure
}

$cred = Load-DeployCredential -Name $ComputerName

Invoke-Command -ComputerName $ComputerName -Credential $cred -ArgumentList $SampleSeconds -ScriptBlock {
  param([int]$SampleSeconds)
  $ErrorActionPreference = "Continue"

  Write-Host "=== CPU SAMPLE DELTA ==="
  $p1 = Get-Process | Select-Object Id, ProcessName, CPU, WorkingSet64, PrivateMemorySize64
  Start-Sleep -Seconds $SampleSeconds
  $p2 = Get-Process | Select-Object Id, ProcessName, CPU, WorkingSet64, PrivateMemorySize64
  $map = @{}
  foreach ($p in $p1) { $map[[int]$p.Id] = $p }
  $cpuRows = foreach ($p in $p2) {
    $old = $map[[int]$p.Id]
    if (-not $old -or $null -eq $p.CPU -or $null -eq $old.CPU) { continue }
    $delta = [double]$p.CPU - [double]$old.CPU
    if ($delta -lt 0.05) { continue }
    [pscustomobject]@{
      Id = $p.Id
      ProcessName = $p.ProcessName
      CpuSecondsDelta = [math]::Round($delta, 2)
      ApproxCpuPct = [math]::Round(($delta / [double]$SampleSeconds) * 100, 1)
      WS_MB = [math]::Round($p.WorkingSet64 / 1MB, 1)
      PM_MB = [math]::Round($p.PrivateMemorySize64 / 1MB, 1)
    }
  }
  $cpuRows | Sort-Object CpuSecondsDelta -Descending | Select-Object -First 20 | Format-Table -AutoSize

  Write-Host "=== APP PROCESS PORT MAP ==="
  $portRows = foreach ($port in 5000,5001,5002,5050) {
    $conn = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $conn) {
      [pscustomobject]@{Port=$port; Pid=""; Project=""; Process=""; CommandLine=""}
      continue
    }
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$($conn.OwningProcess)" -ErrorAction SilentlyContinue
    $cmd = [string]($proc.CommandLine)
    $project = if ($cmd -match "Progetto_FP") { "Progetto_FP" } elseif ($cmd -match "Progetto_Calendar") { "Progetto_Calendar" } elseif ($cmd -match "Progetto_Google") { "Progetto_Google" } elseif ($cmd -match "Progetto_Controller") { "Progetto_Controller" } else { "" }
    [pscustomobject]@{Port=$port; Pid=$conn.OwningProcess; Project=$project; Process=$proc.Name; CommandLine=$cmd}
  }
  $portRows | Format-List

  $sqlcmd = Get-Command sqlcmd.exe -ErrorAction SilentlyContinue
  if (-not $sqlcmd) {
    Write-Host "sqlcmd.exe non trovato."
    return
  }

  Write-Host "=== DBCC OPENTRAN IPRATICO ==="
  & sqlcmd.exe -S ".\SQLEXPRESS" -E -W -d "ipratico" -Q "DBCC OPENTRAN WITH TABLERESULTS;"

  Write-Host "=== USER SESSIONS OPEN TRANCOUNT ==="
  & sqlcmd.exe -S ".\SQLEXPRESS" -E -W -Q "
SET NOCOUNT ON;
SELECT
  s.session_id,
  DB_NAME(COALESCE(r.database_id, s.database_id)) AS database_name,
  s.status,
  s.open_transaction_count,
  r.command,
  r.status AS request_status,
  r.blocking_session_id,
  r.wait_type,
  r.total_elapsed_time,
  s.cpu_time,
  s.reads,
  s.writes,
  s.logical_reads,
  s.host_name,
  s.program_name,
  s.login_name,
  txt.text AS most_recent_sql_text
FROM sys.dm_exec_sessions s
LEFT JOIN sys.dm_exec_requests r ON r.session_id = s.session_id
LEFT JOIN sys.dm_exec_connections c ON c.session_id = s.session_id
OUTER APPLY sys.dm_exec_sql_text(c.most_recent_sql_handle) txt
WHERE s.is_user_process = 1
ORDER BY s.open_transaction_count DESC, s.cpu_time DESC;
"

  Write-Host "=== POWER BI / MASHUP SESSION COUNT ==="
  & sqlcmd.exe -S ".\SQLEXPRESS" -E -W -Q "
SET NOCOUNT ON;
SELECT
  program_name,
  host_name,
  COUNT(*) AS sessions,
  SUM(cpu_time) AS cpu_time,
  SUM(reads) AS reads,
  SUM(logical_reads) AS logical_reads,
  SUM(open_transaction_count) AS open_transactions
FROM sys.dm_exec_sessions
WHERE is_user_process = 1
GROUP BY program_name, host_name
ORDER BY cpu_time DESC;
"
}
