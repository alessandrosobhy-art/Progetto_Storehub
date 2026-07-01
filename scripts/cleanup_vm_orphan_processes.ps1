param(
  [string]$ComputerName = "10.24.1.1",
  [string]$CredentialPath = "C:\Users\aless\Desktop\Progetto_Calendar\scripts\credentials\10.24.1.1.json",
  [int[]]$ProcessIds = @(11212, 9120, 4176, 10236)
)

$ErrorActionPreference = "Stop"

$raw = Get-Content -LiteralPath $CredentialPath -Raw | ConvertFrom-Json
$seed = [Text.Encoding]::UTF8.GetBytes("CodexDeploySharedKey::Progetto_Calendar::2026")
$sha = [System.Security.Cryptography.SHA256]::Create()
try { $key = $sha.ComputeHash($seed) } finally { $sha.Dispose() }
$secure = ConvertTo-SecureString -String ([string]$raw.password) -Key $key
$cred = New-Object System.Management.Automation.PSCredential ([string]$raw.username), $secure

Invoke-Command -ComputerName $ComputerName -Credential $cred -ArgumentList (,$ProcessIds) -ScriptBlock {
  param([int[]]$TargetPids)

  Write-Output "=== STOPPING ORPHAN PROCESSES ==="
  foreach($processId in $TargetPids | Select-Object -Unique) {
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$processId" -ErrorAction SilentlyContinue
    if(-not $proc) {
      Write-Output "PID $processId non trovato."
      continue
    }
    Write-Output ("Stop PID {0} {1} :: {2}" -f $proc.ProcessId, $proc.Name, $proc.CommandLine)
    try {
      taskkill.exe /PID $processId /T /F | Out-Null
    } catch {
      Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
    }
  }

  Start-Sleep -Seconds 2

  Write-Output "=== LISTENERS AFTER CLEANUP ==="
  Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
    Where-Object { @(5000,5001,5002,5050) -contains $_.LocalPort } |
    Select-Object LocalAddress, LocalPort, OwningProcess |
    Format-Table -AutoSize |
    Out-String
}
