# README_DEPLOY

## Scopo
Deploy del progetto `fp` verso la VM `10.24.1.1` usando il deploy manager centrale presente in `Progetto_Calendar`.

## Script principale
- `C:\Users\TUO_UTENTE\Desktop\Progetto_Calendar\scripts\deploy_projects.ps1`

## Configurazione usata
- file: `C:\Users\TUO_UTENTE\Desktop\Progetto_Calendar\scripts\deploy.projects.json`
- progetto: `fp`
- destinazione VM:
  - `\\10.24.1.1\c$\Users\administratoror\Desktop\Progetto_FP`
- restart:
  - `C:\Users\administratoror\Desktop\Progetto_FP\scripts\run_server_windows_watchdog.bat`

## Comandi utili
### Deploy completo
```powershell
powershell -ExecutionPolicy Bypass -File "C:\Users\TUO_UTENTE\Desktop\Progetto_Calendar\scripts\deploy_projects.ps1" -Project fp -UseSavedCredential
```

### Solo restart
```powershell
powershell -ExecutionPolicy Bypass -File "C:\Users\TUO_UTENTE\Desktop\Progetto_Calendar\scripts\deploy_projects.ps1" -Project fp -RestartOnly -UseSavedCredential
```

### Solo sync senza restart
```powershell
powershell -ExecutionPolicy Bypass -File "C:\Users\TUO_UTENTE\Desktop\Progetto_Calendar\scripts\deploy_projects.ps1" -Project fp -SkipRestart -UseSavedCredential
```

## Cosa copiare sul nuovo PC
- cartella `Progetto_Calendar\scripts`
- `deploy.projects.json`
- `credentials\10.24.1.1.json`
- `credentials\10.24.1.1.xml`

## Requisiti
- accesso alla share `\\10.24.1.1\c$`
- PowerShell remoting funzionante verso la VM
- credenziali salvate o reinserite con `-SaveCredential`

## Nota pratica
Se il nuovo PC cambia utente Windows o cifratura locale, puo essere necessario rieseguire una volta:
```powershell
powershell -ExecutionPolicy Bypass -File "C:\Users\TUO_UTENTE\Desktop\Progetto_Calendar\scripts\deploy_projects.ps1" -Project fp -SaveCredential -SkipRestart
```
