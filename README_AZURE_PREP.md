# Azure App Service Prep

Questa nota serve a preparare il progetto per la migrazione su Azure App Service Linux.

## 1. Obiettivo

Portare il progetto su Azure mantenendo due principi:

- il codice pubblicato deve arrivare da Git
- la configurazione sensibile deve restare fuori dal repository

## 2. Cosa va nel repository

Da versionare:

- file `.py`
- cartelle `templates/`, `static/`, `sql/`, `scripts/` utili al deploy
- `requirements.txt`
- `Procfile`
- documentazione tecnica

Da non versionare:

- `.env`
- cartelle `logs/`, `tmp/`, `outputs/`, `BK/`
- ambienti virtuali
- export temporanei
- cache Python

## 3. Entry point applicativo

Il progetto espone:

- `wsgi.py` -> `from app import app`
- `Procfile` -> `web: gunicorn app:app`

Questo e sufficiente per un deploy Flask base su App Service Linux, se il runtime installa le dipendenze da `requirements.txt`.

## 4. Variabili ambiente da spostare su Azure

Le variabili del file `.env` non devono essere caricate dal repository.
Vanno replicate in:

- Azure App Service -> Environment variables / App Settings

Prima del go-live va fatto un inventario delle chiavi effettivamente necessarie.

## 5. Strategia deploy consigliata

Dato che l'App Service e su piano S1:

1. repository Git dedicato al progetto
2. GitHub Actions per build e deploy
3. deployment slot `staging`
4. test su `staging`
5. swap verso `production`

## 6. Checklist prima del primo deploy

- [ ] repository Git inizializzato
- [ ] `.gitignore` verificata
- [ ] `requirements.txt` completo
- [ ] variabili ambiente censite
- [ ] startup command confermato
- [ ] slot `staging` creato
- [ ] accesso DB/API verificato da Azure

## 7. Scelta processo

Per il primo rilascio conviene:

1. deploy iniziale controllato su `staging`
2. test funzionale
3. swap in produzione

Per i rilasci successivi:

1. modifica locale
2. commit Git
3. push
4. deploy automatico su `staging`
5. swap quando approvato

