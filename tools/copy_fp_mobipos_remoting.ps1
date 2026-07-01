param(
  [string]$ComputerName = "10.24.1.1",
  [string]$DeployScriptRoot = "C:\Users\aless\Desktop\Progetto_Calendar\scripts",
  [string]$SourceRoot = "C:\Users\aless\Desktop\Progetto_FP",
  [string]$RemoteRoot = "C:\Users\administratoror\Desktop\Progetto_FP"
)

$ErrorActionPreference = "Stop"

function Get-CredentialCryptoKey {
  $seed = [Text.Encoding]::UTF8.GetBytes('CodexDeploySharedKey::Progetto_Calendar::2026')
  $hash = [System.Security.Cryptography.SHA256]::Create()
  try {
    return $hash.ComputeHash($seed)
  } finally {
    $hash.Dispose()
  }
}

function Load-DeployCredential {
  param([string]$ComputerName)
  $safeName = ($ComputerName -replace '[^a-zA-Z0-9\.-]', '_')
  $path = Join-Path (Join-Path $DeployScriptRoot "credentials") "$safeName.json"
  $raw = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
  $secure = ConvertTo-SecureString -String ([string]$raw.password) -Key (Get-CredentialCryptoKey)
  return New-Object System.Management.Automation.PSCredential ([string]$raw.username), $secure
}

$cred = Load-DeployCredential -ComputerName $ComputerName
$session = New-PSSession -ComputerName $ComputerName -Credential $cred
try {
  $files = @(
    "mobipos_repository.py",
    "templates\master_mobipos_test.html",
    "templates\master_home.html",
    "app.py"
  )
  foreach($relative in $files){
    $local = Join-Path $SourceRoot $relative
    $remote = Join-Path $RemoteRoot $relative
    $remoteDir = Split-Path -Parent $remote
    Invoke-Command -Session $session -ScriptBlock {
      param($Path)
      if(-not (Test-Path -LiteralPath $Path)){
        New-Item -ItemType Directory -Path $Path -Force | Out-Null
      }
    } -ArgumentList $remoteDir
    Copy-Item -LiteralPath $local -Destination $remote -ToSession $session -Force
    Write-Host "OK $relative" -ForegroundColor Green
  }
} finally {
  if($session){ Remove-PSSession $session }
}
