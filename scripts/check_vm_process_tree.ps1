param(
  [string]$ComputerName = "10.24.1.1",
  [string]$CredentialPath = "C:\Users\aless\Desktop\Progetto_Calendar\scripts\credentials\10.24.1.1.json"
)

$ErrorActionPreference = "Stop"

$raw = Get-Content -LiteralPath $CredentialPath -Raw | ConvertFrom-Json
$seed = [Text.Encoding]::UTF8.GetBytes("CodexDeploySharedKey::Progetto_Calendar::2026")
$sha = [System.Security.Cryptography.SHA256]::Create()
try { $key = $sha.ComputeHash($seed) } finally { $sha.Dispose() }
$secure = ConvertTo-SecureString -String ([string]$raw.password) -Key $key
$cred = New-Object System.Management.Automation.PSCredential ([string]$raw.username), $secure

Invoke-Command -ComputerName $ComputerName -Credential $cred -ScriptBlock {
  $all = @{}
  Get-CimInstance Win32_Process | ForEach-Object { $all[[int]$_.ProcessId] = $_ }

  function Chain([int]$ProcessId) {
    $items = @()
    $current = $ProcessId
    $guard = 0
    while($current -gt 0 -and $all.ContainsKey($current) -and $guard -lt 12) {
      $p = $all[$current]
      $items += ("{0}:{1}" -f $p.ProcessId, $p.Name)
      $current = [int]$p.ParentProcessId
      $guard++
    }
    return ($items -join " <- ")
  }

  Write-Output "=== ALL LISTENERS OWNED BY PYTHON/CMD ==="
  Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
    ForEach-Object {
      $owner = $all[[int]$_.OwningProcess]
      [pscustomobject]@{
        LocalAddress = $_.LocalAddress
        LocalPort = $_.LocalPort
        PID = $_.OwningProcess
        Name = if($owner){ $owner.Name } else { "" }
        CommandLine = if($owner){ $owner.CommandLine } else { "" }
        Chain = if($owner){ Chain ([int]$owner.ProcessId) } else { "" }
      }
    } |
    Where-Object { $_.Name -match "python|cmd|powershell" -or $_.LocalPort -in @(5000,5001,5002,5050) } |
    Sort-Object LocalPort |
    Format-List |
    Out-String

  Write-Output "=== RUN_WAITRESS PROCESSES WITH CHAINS ==="
  Get-CimInstance Win32_Process |
    Where-Object { ([string]$_.CommandLine) -like "*run_waitress.py*" } |
    ForEach-Object {
      [pscustomobject]@{
        PID = $_.ProcessId
        PPID = $_.ParentProcessId
        Name = $_.Name
        WorkingSetMB = [math]::Round(($_.WorkingSetSize / 1MB), 1)
        Chain = Chain ([int]$_.ProcessId)
        CommandLine = $_.CommandLine
      }
    } |
    Sort-Object Chain, PID |
    Format-List |
    Out-String

  Write-Output "=== RECENT CPU SNAPSHOT ==="
  Get-Process |
    Where-Object { $_.ProcessName -match "python|cmd|powershell" } |
    Sort-Object CPU -Descending |
    Select-Object -First 20 Id, ProcessName, CPU, WorkingSet64 |
    Format-Table -AutoSize |
    Out-String
}
