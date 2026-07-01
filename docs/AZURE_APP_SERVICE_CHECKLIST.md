# Azure App Service Checklist

Questa checklist serve per il primo deploy di `Progetto_FP` su Azure App Service Linux con slot `staging`.

## Stack applicativo

- Runtime Azure: Python 3.12
- Startup command: `bash startup.sh`
- Entrypoint WSGI: `wsgi:app`
- Deploy source: GitHub Actions

## App Service

- App Service name: da definire in Azure
- Slot staging: `staging`
- Always On: attivo
- Health check: facoltativo, consigliato su `/controller/metrics`

## GitHub Actions secrets attesi

- `AZUREAPPSERVICE_PUBLISH_PROFILE_STAGING`

Se si preferisce il deploy via service principal invece del publish profile:

- `AZURE_CLIENT_ID`
- `AZURE_TENANT_ID`
- `AZURE_SUBSCRIPTION_ID`

## App Settings / variabili ambiente da censire su Azure

Minimo da verificare e popolare:

- `FLASK_SECRET_KEY`
- `APP_VERSION`
- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- `SUPABASE_ANON_KEY` se usata da qualche flow
- `SQLSERVER_SERVER`
- `SQLSERVER_DATABASE`
- `SQLSERVER_USER`
- `SQLSERVER_PASSWORD`
- `SQLSERVER_DRIVER`
- `SQLSERVER_ENCRYPT`
- `SQLSERVER_TRUST_CERT`
- `SQLSERVER_TIMEOUT`
- `STOREHUB_TENANT_DATABASE` se usato come default
- `MS_CLIENT_ID`
- `MS_CLIENT_SECRET`
- `MS_REDIRECT_URI`
- `SMTP_*` se usati per email ordini o notifiche
- `OPENAI_*` se AI abilitata
- `CONTROLLER_HEARTBEAT_ENABLED`
- `CONTROLLER_BASE_URL`
- `CONTROLLER_TOKEN`
- `CONTROLLER_HEARTBEAT_INTERVAL`
- `CONTROLLER_HEARTBEAT_APP`
- `CONTROLLER_METRICS_SLOW_MS`

## Test minimi su staging

1. Aprire `/controller/metrics` e verificare JSON valido.
2. Verificare login.
3. Verificare caricamento topbar e menu.
4. Verificare Dashboard.
5. Verificare Rendiconto.
6. Verificare Magazzino.
7. Verificare Gestione Orari.
8. Verificare Cruscotto.
9. Verificare MBO se abilitato per il tenant di test.
10. Verificare almeno una query SQL e una chiamata Supabase dal JSON metriche.

## Networking SQL su VM

Soluzione temporanea:

- autorizzare gli outbound IP reali dell'App Service verso SQL Server sulla VM

Soluzione corretta/stabile:

- VNet Integration dell'App Service
- subnet dedicata
- regole firewall/NAT coerenti verso la VM

## Swap production

Fare swap verso production solo dopo:

1. test funzionali completati su staging
2. verifica `/controller/metrics`
3. verifica accesso SQL
4. verifica accesso Supabase
5. verifica eventuale integrazione Microsoft / SharePoint
