param(
  [string]$ComputerName = "10.24.1.1",
  [string]$DeployScriptRoot = "C:\Users\aless\Desktop\Progetto_Calendar\scripts",
  [string]$SourceRoot = "C:\Users\aless\Desktop\Progetto_FP",
  [string]$DestinationRoot = "\\10.24.1.1\c$\Users\administratoror\Desktop\Progetto_FP"
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
  if(-not (Test-Path -LiteralPath $path)){
    throw "Credenziali salvate non trovate: $path"
  }
  $raw = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
  $secure = ConvertTo-SecureString -String ([string]$raw.password) -Key (Get-CredentialCryptoKey)
  return New-Object System.Management.Automation.PSCredential ([string]$raw.username), $secure
}

function Connect-Share {
  param([pscredential]$Credential)
  $shareRoot = "\\$ComputerName\c$"
  $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Credential.Password)
  try {
    $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    try { & net.exe use $shareRoot /delete /y *>$null } catch {}
    & net.exe use $shareRoot "/user:$($Credential.UserName)" $plain /persistent:no | Out-Null
    if($LASTEXITCODE -ne 0){ throw "Connessione share fallita: $shareRoot" }
  } finally {
    if($bstr -ne [IntPtr]::Zero){ [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
  }
}

function Copy-One {
  param([string]$RelativePath)
  $srcDir = Split-Path -Parent (Join-Path $SourceRoot $RelativePath)
  $fileName = Split-Path -Leaf $RelativePath
  $dstDir = Split-Path -Parent (Join-Path $DestinationRoot $RelativePath)
  if(-not (Test-Path -LiteralPath $dstDir)){
    New-Item -ItemType Directory -Path $dstDir -Force | Out-Null
  }
  & robocopy $srcDir $dstDir $fileName /FFT /Z /R:10 /W:2 /NFL /NDL /NP /NJH /NJS
  $code = $LASTEXITCODE
  if($code -ge 8){ throw "Robocopy fallito su $RelativePath (exit $code)" }
  Write-Host "OK $RelativePath (robocopy exit $code)" -ForegroundColor Green
}

$cred = Load-DeployCredential -ComputerName $ComputerName
Connect-Share -Credential $cred

Copy-One "mobipos_repository.py"
Copy-One "templates\master_mobipos_test.html"
Copy-One "templates\master_home.html"
Copy-One "app.py"
