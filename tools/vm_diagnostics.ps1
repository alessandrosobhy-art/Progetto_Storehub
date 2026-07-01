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
  $paths = @(
    @{Name="fp"; Path="C:\Users\administratoror\Desktop\Progetto_FP"; Port=5000},
    @{Name="calendar"; Path="C:\Users\administratoror\Desktop\Progetto_Calendar"; Port=5001},
    @{Name="google"; Path="C:\Users\administratoror\Desktop\Progetto_Google"; Port=5002},
    @{Name="controller"; Path="C:\Users\administratoror\Desktop\Progetto_Controller"; Port=5050}
  )

  Write-Host "=== VM ==="
  $os = Get-CimInstance Win32_OperatingSystem
  $cs = Get-CimInstance Win32_ComputerSystem
  $cpu = Get-CimInstance Win32_Processor
  $uptime = (Get-Date) - $os.LastBootUpTime
  [pscustomobject]@{
    Computer = $env:COMPUTERNAME
    OS = $os.Caption
    UptimeHours = [math]::Round($uptime.TotalHours, 1)
    CpuName = ($cpu | Select-Object -First 1).Name
    CpuLoadPct = [math]::Round((($cpu | Measure-Object LoadPercentage -Average).Average), 1)
    Cores = ($cpu | Measure-Object NumberOfCores -Sum).Sum
    LogicalProcessors = ($cpu | Measure-Object NumberOfLogicalProcessors -Sum).Sum
    RamGB = [math]::Round($cs.TotalPhysicalMemory / 1GB, 2)
    FreeRamGB = [math]::Round($os.FreePhysicalMemory / 1MB, 2)
  } | Format-List

  Write-Host "=== DISCHI ==="
  Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3" |
    Select-Object DeviceID,
      @{n="SizeGB";e={[math]::Round($_.Size/1GB,2)}},
      @{n="FreeGB";e={[math]::Round($_.FreeSpace/1GB,2)}},
      @{n="FreePct";e={[math]::Round(($_.FreeSpace/$_.Size)*100,1)}} |
    Format-Table -AutoSize

  Write-Host "=== TOP PROCESSI CPU ==="
  Get-Process |
    Sort-Object CPU -Descending |
    Select-Object -First 20 Id,ProcessName,
      @{n="CPU_s";e={[math]::Round(($_.CPU),1)}},
      @{n="WS_MB";e={[math]::Round($_.WorkingSet64/1MB,1)}},
      @{n="PM_MB";e={[math]::Round($_.PrivateMemorySize64/1MB,1)}} |
    Format-Table -AutoSize

  Write-Host "=== PROCESSI PYTHON / WAITRESS / APP ==="
  Get-CimInstance Win32_Process |
    Where-Object {
      $_.Name -match "python|waitress|cmd|powershell" -or
      ($_.CommandLine -and ($_.CommandLine -match "Progetto_FP|Progetto_Calendar|Progetto_Google|Progetto_Controller"))
    } |
    Select-Object ProcessId,Name,
      @{n="WorkingSetMB";e={[math]::Round(($_.WorkingSetSize/1MB),1)}},
      CommandLine |
    Format-List

  Write-Host "=== PORTE APP ==="
  $portRows = foreach ($p in $paths) {
    $conn = Get-NetTCPConnection -LocalPort $p.Port -State Listen -ErrorAction SilentlyContinue
    [pscustomobject]@{
      App = $p.Name
      Port = $p.Port
      Listening = [bool]$conn
      OwningProcess = if ($conn) { ($conn | Select-Object -First 1).OwningProcess } else { "" }
    }
  }
  $portRows | Format-Table -AutoSize

  Write-Host "=== DIMENSIONI LOG / CARTELLE ==="
  foreach ($p in $paths) {
    $logDir = Join-Path $p.Path "logs"
    $logs = if (Test-Path -LiteralPath $logDir) {
      Get-ChildItem -LiteralPath $logDir -File -ErrorAction SilentlyContinue |
        Sort-Object Length -Descending |
        Select-Object -First 8 Name,
          @{n="MB";e={[math]::Round($_.Length/1MB,2)}},
          LastWriteTime
    } else { @() }
    Write-Host "--- $($p.Name): $($p.Path) ---"
    if ($logs) { $logs | Format-Table -AutoSize } else { Write-Host "Nessun log trovato." }
  }

  Write-Host "=== CARTELLE PRINCIPALI DESKTOP ==="
  Get-ChildItem -LiteralPath "C:\Users\administratoror\Desktop" -Directory -ErrorAction SilentlyContinue |
    ForEach-Object {
      $sum = (Get-ChildItem -LiteralPath $_.FullName -Recurse -File -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum
      [pscustomobject]@{Name=$_.Name; GB=[math]::Round(($sum/1GB),2); Path=$_.FullName}
    } |
    Sort-Object GB -Descending |
    Select-Object -First 20 |
    Format-Table -AutoSize

  Write-Host "=== SQL EXPRESS DATABASE SIZE ==="
  $sqlcmd = Get-Command sqlcmd.exe -ErrorAction SilentlyContinue
  if ($sqlcmd) {
    & sqlcmd.exe -S ".\SQLEXPRESS" -E -W -Q "
SET NOCOUNT ON;
SELECT
  DB_NAME(database_id) AS database_name,
  CAST(SUM(size) * 8.0 / 1024 AS DECIMAL(18,2)) AS size_mb,
  CAST(SUM(CASE WHEN type_desc='ROWS' THEN size ELSE 0 END) * 8.0 / 1024 AS DECIMAL(18,2)) AS data_mb,
  CAST(SUM(CASE WHEN type_desc='LOG' THEN size ELSE 0 END) * 8.0 / 1024 AS DECIMAL(18,2)) AS log_mb
FROM sys.master_files
GROUP BY database_id
ORDER BY size_mb DESC;
"
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
  } else {
    Write-Host "sqlcmd.exe non trovato."
  }

  Write-Host "=== EVENTI ERRORI RECENTI APP/SYSTEM ==="
  Get-WinEvent -FilterHashtable @{LogName='Application'; Level=1,2,3; StartTime=(Get-Date).AddHours(-24)} -MaxEvents 20 -ErrorAction SilentlyContinue |
    Select-Object TimeCreated,ProviderName,Id,LevelDisplayName,Message |
    Format-List

  Write-Host "=== TAIL LOG FP ==="
  $fpLogs = @(
    "C:\Users\administratoror\Desktop\Progetto_FP\logs\app.log",
    "C:\Users\administratoror\Desktop\Progetto_FP\logs\error.log",
    "C:\Users\administratoror\Desktop\Progetto_FP\logs\fault.log"
  )
  foreach ($log in $fpLogs) {
    if (Test-Path -LiteralPath $log) {
      Write-Host "--- $log ---"
      Get-Content -LiteralPath $log -Tail 80 -ErrorAction SilentlyContinue
    }
  }
}
