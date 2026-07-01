# GitHub bootstrap

Il progetto e gia collegato al repository remoto:

- Repository: `https://github.com/alessandrosobhy-art/Progetto_Storehub.git`
- Branch principale: `main`

## 1. Verifiche rapide locali

Eseguire nella root del progetto:

```powershell
cd C:\Users\aless\Desktop\Progetto_FP
cmd /c git remote -v
cmd /c git branch --show-current
cmd /c git status --short
```

Risultato atteso:

- `origin` punta a `Progetto_Storehub`
- branch corrente `main`
- working tree pulito oppure con sole modifiche intenzionali

## 2. Push modifiche

```powershell
cd C:\Users\aless\Desktop\Progetto_FP
cmd /c git add .
cmd /c git commit -m "Messaggio chiaro"
cmd /c git push origin main
```

## 3. Secret GitHub Actions richiesto

- `AZUREAPPSERVICE_PUBLISH_PROFILE_STAGING`

Il valore deve essere il contenuto XML completo del publish profile dello slot Azure `staging`.

## 4. Workflow attivo

Nel repository deve rimanere attivo un solo workflow applicativo:

- `.github/workflows/deploy-azure-staging.yml`

Questo workflow:

1. gira su push a `main`
2. installa le dipendenze
3. compila i file Python principali
4. fa deploy sullo slot Azure `staging`

## 5. Dopo il push

1. controllare GitHub Actions
2. verificare esito verde del workflow `Deploy FP to Azure staging`
3. aprire lo slot `staging`
4. testare `/controller/metrics`
5. testare login, dashboard e almeno una pagina SQL pesante
