# GitHub bootstrap

Il repository locale e pronto.

## 1. Login GitHub CLI

Eseguire:

```powershell
C:\Users\aless\Desktop\Progetto_FP\tools\gh-cli\bin\gh.exe auth login
```

## 2. Creazione repository remoto

Esempio consigliato:

```powershell
cd C:\Users\aless\Desktop\Progetto_FP
tools\gh-cli\bin\gh.exe repo create Progetto_FP --private --source . --remote origin --push
```

In alternativa, se il repository esiste gia:

```powershell
cd C:\Users\aless\Desktop\Progetto_FP
cmd /c git remote add origin https://github.com/<owner>/<repo>.git
cmd /c git push -u origin main
```

## 3. Secrets GitHub Actions da impostare

- `AZUREAPPSERVICE_PUBLISH_PROFILE_STAGING`

## 4. Dopo il push

1. creare App Service Linux
2. creare slot `staging`
3. impostare startup command `bash startup.sh`
4. configurare App Settings
5. lanciare workflow GitHub Actions
6. testare `https://<staging-url>/controller/metrics`
