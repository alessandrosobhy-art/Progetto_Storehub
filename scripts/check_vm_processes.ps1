param(
  [string]$ComputerName = "10.24.1.1",
  [string]$CredentialPath = "C:\Users\aless\Desktop\Progetto_Calendar\scripts\credentials\10.24.1.1.json"
)

$ErrorActionPreference = "Stop"

$raw = Get-Content -LiteralPath $CredentialPath -Raw | ConvertFrom-Json
$seed = [Text.Encoding]::UTF8.GetBytes("CodexDeploySharedKey::Progetto_Calendar::2026")
$sha = [System.Security.Cryptography.SHA256]::Create()
try {
  $key = $sha.ComputeHash($seed)
} finally {
  $sha.Dispose()
}
$secure = ConvertTo-SecureString -String ([string]$raw.password) -Key $key
$cred = New-Object System.Management.Automation.PSCredential ([string]$raw.username), $secure

Invoke-Command -ComputerName $ComputerName -Credential $cred -ScriptBlock {
  $projects = @("Progetto_FP", "Progetto_Calendar", "Progetto_Google")
  $ports = @(5000, 5001, 5002)

  Write-Output "=== LISTENERS ==="
  Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
    Where-Object { $ports -contains $_.LocalPort } |
    Select-Object LocalAddress, LocalPort, OwningProcess |
    Format-Table -AutoSize |
    Out-String

  Write-Output "=== PROCESSES MATCHING PROJECTS ==="
  Get-CimInstance Win32_Process |
    Where-Object {
      $cmd = [string]$_.CommandLine
      if(-not $cmd){ return $false }
      foreach($project in $projects){
        if($cmd -like "*$project*"){ return $true }
      }
      return $false
    } |
    Select-Object ProcessId, ParentProcessId, Name, CommandLine |
    Sort-Object CommandLine, ProcessId |
    Format-List |
    Out-String

  Write-Output "=== PYTHON/CMD/POWERSHELL BY MEMORY ==="
  Get-CimInstance Win32_Process |
    Where-Object { $_.Name -match "python|cmd|powershell|waitress" } |
    Select-Object ProcessId, ParentProcessId, Name, WorkingSetSize, CommandLine |
    Sort-Object WorkingSetSize -Descending |
    Select-Object -First 30 |
    Format-List |
    Out-String
}
