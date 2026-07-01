# CODEX_HANDOVER

## Progetto
Store Hub 360 - FP

## Scopo
Applicazione principale per operativita punti vendita, controllo rendiconto, magazzino, ordini fornitore, orari, estrazioni e moduli dedicati.

## Stato tecnico attuale
- backend Flask
- server locale/VM con Waitress
- DB principale su SQL Server remoto `APP_STOREHUB`
- altri flussi leggono anche `ILP`
- autenticazione e profili via Supabase
- integrazione Microsoft usata per SharePoint

## Moduli chiave presenti
- Dashboard
- Cruscotto
- Magazzino
- Ordini al fornitore
- Rendiconto
- Gestione Orari
- Estrazioni
- Estrazioni HQ
- Controlli di gestione
- MBO (prima base)

## Note operative importanti
- I deploy si fanno tramite il deploy manager centrale in `Progetto_Calendar`
- Non usare la `.venv` migrata: ricrearla
- Alcuni file vecchi hanno problemi di encoding, preferire testo ASCII quando si fanno fix rapidi di template
- I moduli lato utente dipendono da `access_profile_id` e snapshot `access_modules` in sessione

## Stato lavori recente
- `Ordini al fornitore` introdotto con:
  - `Invia Ordine`
  - `Ordini inviati`
  - `Ordini ricevuti`
- flusso mail ordini tornato a SMTP, non Microsoft Graph mail
- profilo `fornitore` senza store obbligatorio
- `MBO` creato come nuova sezione con:
  - `Riepilogo`
  - `Impostazioni`
  - matrice store x mesi per area manager

## MBO - stato
- default area manager letto da `ILP.dbo.STORE.AM`
- override mensili salvati in `APP_STOREHUB`
- da testare lato profili accesso / visibilita modulo
- da costruire in seguito le pagine di calcolo MBO vere e proprie

## Rischi / cose da controllare dopo migrazione PC
- `.env` completo
- driver ODBC SQL Server presenti
- `pyodbc` installato nella venv
- deploy manager con credenziali funzionanti
- accesso rete a `10.24.1.1`

## Come ripartire in chat
Quando riapri il progetto in Codex, fai leggere questo file e `README_RUN.md` e `README_DEPLOY.md` per riallineare velocemente il contesto.
