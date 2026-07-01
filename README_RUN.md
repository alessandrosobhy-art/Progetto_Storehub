# README_RUN

## Scopo
Store Hub 360 - progetto operativo principale per dashboard, rendiconto, magazzino, ordini fornitore, orari, estrazioni e controlli.

## Requisiti locali
- Python 3.12 x64
- virtual environment `.venv`
- ODBC Driver 18 for SQL Server x64
- Microsoft Access Database Engine x64
- accesso di rete a `10.24.1.1\SQLEXPRESS`

## Setup rapido
```powershell
cd C:\Users\TUO_UTENTE\Desktop\Progetto_FP
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m pip install pyodbc
```

## Configurazione
- Copiare `.env`
- Verificare in `.env`:
  - `SUPABASE_URL`
  - `SUPABASE_SERVICE_ROLE_KEY`
  - `SQLSERVER_SERVER`
  - `SQLSERVER_DATABASE`
  - `SQLSERVER_USER`
  - `SQLSERVER_PASSWORD`
  - `MS_CLIENT_ID`
  - `MS_CLIENT_SECRET`
  - `MS_REDIRECT_URI`
  - `OPENAI_*`

## Avvio locale
```powershell
cd C:\Users\TUO_UTENTE\Desktop\Progetto_FP
.venv\Scripts\python.exe run_waitress.py
```

## Porta attesa
- default `5000`

## Check minimo
- login applicazione
- caricamento topbar
- accesso a Dashboard
- accesso a Rendiconto / Magazzino / Ordini al fornitore in base ai permessi

## Note
- Non copiare la `.venv` dal vecchio PC
- Alcuni moduli usano SQL Server remoto
- Le connessioni Microsoft servono per SharePoint
