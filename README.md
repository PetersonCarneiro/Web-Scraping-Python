# Web-Scraping-Python

Automação para fazer login no portal EQS, capturar os headers da requisição usada no relatório e salvar os dados em um arquivo Excel no Google Drive.

## Execução no GitHub Actions

O workflow principal está em `.github/workflows/renovar_token.yml`.

### Secrets necessárias

Cadastre estas secrets no repositório:

- `EQS_LOGIN`
- `EQS_PASSWORD`
- `GOOGLE_SERVICE_ACCOUNT_JSON` — conteúdo bruto do JSON da service account
- `GOOGLE_DRIVE_FOLDER_ID`

### Observações importantes

- O script `renovar_token.py` roda em modo headless por padrão no GitHub Actions.
- A captura do token usa os performance logs do Chrome, compatíveis com Selenium Python no ambiente do Actions.
- Em caso de falha, o workflow publica artefatos HTML/PNG para diagnóstico.
- O fluxo de login agora valida sinais de sucesso além da URL, o que ajuda em páginas SPA que mantêm `#/login` por alguns instantes antes de renderizar o menu interno.
