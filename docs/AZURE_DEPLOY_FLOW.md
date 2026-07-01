# Azure deploy flow

Questa nota descrive il flusso operativo standard per StoreHub su Azure App Service Linux.

## Architettura corrente

- Codice sorgente: repository GitHub `Progetto_Storehub`
- Branch di deploy: `main`
- App Service Azure: `progetto-storehub`
- Slot di collaudo: `staging`
- Startup command: `bash startup.sh`
- Entry point WSGI: `wsgi:app`

## Workflow da tenere

Workflow applicativo da mantenere:

- `.github/workflows/deploy-azure-staging.yml`

Obiettivo:

- ogni push su `main` pubblica automaticamente sullo slot `staging`

## Flusso consigliato

### 1. Sviluppo

Le modifiche si preparano localmente o sulla VM di lavoro.

Verifiche minime prima del push:

```powershell
cd C:\Users\aless\Desktop\Progetto_FP
cmd /c git status --short
.venv\Scripts\python.exe -m py_compile app.py wsgi.py run_waitress.py controller_monitoring.py
```

### 2. Commit e push

```powershell
cd C:\Users\aless\Desktop\Progetto_FP
cmd /c git add .
cmd /c git commit -m "Descrizione modifica"
cmd /c git push origin main
```

### 3. Deploy automatico su staging

GitHub Actions esegue:

1. checkout repository
2. setup Python 3.12
3. install dipendenze
4. compile check
5. deploy Azure con publish profile dello slot `staging`

### 4. Test su staging

Checklist minima:

1. `/controller/metrics`
2. login
3. dashboard
4. rendiconto
5. magazzino
6. gestione orari
7. almeno una pagina multi-tenant
8. almeno una funzione che legge SQL e una che legge Supabase

### 5. Passaggio a production

Quando staging e stabile si puo scegliere una delle due strade:

- slot swap `staging -> production`
- deploy diretto su production con workflow dedicato

Per ora e consigliato mantenere staging come ambiente di validazione.

## Variabili da tenere allineate su Azure

Da verificare sempre nello slot corretto:

- `FLASK_ENV`
- `FLASK_DEBUG`
- `FLASK_SECRET`
- `SUPABASE_URL`
- `SUPABASE_ANON_KEY`
- `SUPABASE_SERVICE_ROLE_KEY`
- `SQLSERVER_SERVER`
- `SQLSERVER_DATABASE`
- `SQLSERVER_USER`
- `SQLSERVER_PASSWORD`
- `SQLSERVER_DRIVER`
- `SQLSERVER_ENCRYPT`
- `SQLSERVER_TRUST_CERT`
- `OPENAI_*` se usati
- `MS_*` se usati
- `CONTROLLER_*` se abilitati

## Nota importante su SQL

Il deploy dell'app puo risultare corretto anche se la connessione SQL del singolo slot non e abilitata lato rete.

Quando un nuovo slot o una nuova app non vede SQL:

1. controllare le env nel container App Service
2. testare da SSH con `sqlcmd`
3. testare il flusso applicativo reale
4. verificare che la connettivita sia stata abilitata proprio per quello slot
