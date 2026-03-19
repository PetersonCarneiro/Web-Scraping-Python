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

- O workflow executa o Chrome em modo não-headless dentro de `xvfb-run`, para ficar mais próximo do comportamento manual observado no Colab.
- O script não depende apenas de mudança de URL para confirmar o login; ele também aceita sinais da interface após autenticação.
- A captura do token usa os performance logs do Chrome, compatíveis com Selenium Python no ambiente do Actions.
- Em caso de falha, o workflow publica artefatos HTML/PNG para diagnóstico.
