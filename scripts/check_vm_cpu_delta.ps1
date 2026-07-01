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
  function Snapshot {
    Get-Process |
      Where-Object { $_.ProcessName -match "python|cmd|powershell" } |
      Select-Object Id, ProcessName, CPU, WorkingSet64
  }

  $a = Snapshot
  Start-Sleep -Seconds 5
  $b = Snapshot
  $byId = @{}
  foreach($item in $a){ $byId[[int]$item.Id] = $item }
  $rows = foreach($item in $b){
    $old = $byId[[int]$item.Id]
    if(-not $old){ continue }
    [pscustomobject]@{
      Id = $item.Id
      ProcessName = $item.ProcessName
      CpuDeltaSeconds = [math]::Round(([double]($item.CPU - $old.CPU)), 3)
      WorkingSetMB = [math]::Round(($item.WorkingSet64 / 1MB), 1)
    }
  }
  $rows |
    Sort-Object CpuDeltaSeconds -Descending |
    Select-Object -First 20 |
    Format-Table -AutoSize |
    Out-String
}
