# ============================================================
#  EQS - RENOVAÇÃO AUTOMÁTICA DE TOKEN | GitHub Actions
# ============================================================

import os
import io
import base64
import json
import time
import pandas as pd

from seleniumwire import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload, MediaIoBaseDownload


# ============================================================
#  SEÇÃO 1 — CONFIGURAÇÃO
# ============================================================

EQS_LOGIN    = os.environ['EQS_LOGIN']
EQS_PASSWORD = os.environ['EQS_PASSWORD']
FOLDER_ID    = os.environ['GOOGLE_DRIVE_FOLDER_ID']
SA_JSON      = os.environ['GOOGLE_SERVICE_ACCOUNT_JSON']

MAX_TENTATIVAS = 3
URL_ALVO       = "chamado/rel-reembolsavel-chamado-estacao/listar"
ARQUIVO_EXCEL  = "Eqs_Tokens.xlsx"


# ============================================================
#  SEÇÃO 2 — GOOGLE DRIVE (SERVICE ACCOUNT)
# ============================================================

def configurar_driver():
    """Inicializa o Chrome headless com Selenium Wire."""
    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_argument("--disable-extensions")
    chrome_options.add_argument("--remote-debugging-port=9222")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option("useAutomationExtension", False)

    # No GitHub Actions o Chrome já está instalado — não precisa do ChromeDriverManager
    try:
        service = Service("/usr/bin/chromedriver")
        driver = webdriver.Chrome(service=service, options=chrome_options)
    except Exception:
        # Fallback para ChromeDriverManager se chromedriver não estiver no PATH
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=chrome_options)

    return driver


def salvar_excel_no_drive(service, df: pd.DataFrame):
    """Salva o DataFrame como Excel no Google Drive, substituindo o arquivo anterior."""

    # Verifica se o arquivo já existe na pasta
    resultado = service.files().list(
        q=f"name='{ARQUIVO_EXCEL}' and '{FOLDER_ID}' in parents and trashed=false",
        fields="files(id, name)"
    ).execute()

    arquivos = resultado.get('files', [])

    # Converte DataFrame para bytes Excel em memória
    buffer = io.BytesIO()
    df.to_excel(buffer, index=False)
    buffer.seek(0)

    media = MediaIoBaseUpload(
        buffer,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )

    if arquivos:
        # Atualiza o arquivo existente
        file_id = arquivos[0]['id']
        service.files().update(fileId=file_id, media_body=media).execute()
        print(f"✔ Arquivo atualizado no Drive (id: {file_id})")
    else:
        # Cria novo arquivo
        metadata = {'name': ARQUIVO_EXCEL, 'parents': [FOLDER_ID]}
        service.files().create(body=metadata, media_body=media).execute()
        print(f"✔ Novo arquivo criado no Drive")


# ============================================================
#  SEÇÃO 3 — FUNÇÕES AUXILIARES
# ============================================================

def decodificar_expiracao_jwt(token: str):
    """Extrai o campo 'exp' do payload JWT."""
    try:
        payload_b64 = token.split('.')[1]
        payload_b64 += '=' * (-len(payload_b64) % 4)
        payload = json.loads(base64.b64decode(payload_b64).decode('utf-8'))
        return payload.get('exp')
    except Exception as e:
        print(f"⚠ Não foi possível decodificar o JWT: {e}")
        return None


def configurar_driver():
    """Inicializa o Chrome headless com Selenium Wire."""
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")

    service = Service(ChromeDriverManager().install())
    return webdriver.Chrome(service=service, options=chrome_options)


# ============================================================
#  SEÇÃO 4 — CAPTURA DO TOKEN
# ============================================================

token = ido = cookie = token_expiracao = None

for tentativa in range(1, MAX_TENTATIVAS + 1):
    driver = None
    print(f"\n{'='*55}")
    print(f"  Tentativa {tentativa}/{MAX_TENTATIVAS}")
    print(f"{'='*55}")

    try:
        driver = configurar_driver()

       # Login
        print("► Acessando página de login...")
        driver.get("https://eqs.arenanet.com.br/dist/#/login")

        # Aguarda o campo de login estar visível e interagível
        campo_login = WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable((By.ID, "login"))
        )
        campo_login.clear()
        campo_login.send_keys(EQS_LOGIN)

        campo_senha = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.ID, "senha"))
        )
        campo_senha.clear()
        campo_senha.send_keys(EQS_PASSWORD)

        # Pequena pausa antes de clicar — evita que o Angular ignore o clique
        time.sleep(1)

        botao = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.TAG_NAME, "button"))
        )
        botao.click()

        print("► Aguardando redirecionamento após login...")

        # Aguarda a URL mudar OU o token aparecer nas requisições (o que vier primeiro)
        WebDriverWait(driver, 30).until(
            lambda drv: (
                drv.current_url != "https://eqs.arenanet.com.br/dist/#/login"
                or any(URL_ALVO in req.url for req in drv.requests)
            )
        )

        url_atual = driver.current_url
        print(f"✔ Login bem-sucedido! URL atual: {url_atual}")

        # Navegação
        print("► Expandindo menu 'Relatórios (CHM)'...")
        relatorios_menu = WebDriverWait(driver, 30).until(
            EC.presence_of_element_located(
                (By.XPATH, "//span[text()='Relatórios (CHM)']/..")
            )
        )
        driver.execute_script("arguments[0].click();", relatorios_menu)

        print("► Clicando em 'Itens de LPU Por Local'...")
        lpu_local_menu = WebDriverWait(driver, 30).until(
            EC.presence_of_element_located(
                (By.XPATH, "//span[text()='Itens de LPU Por Local']/..")
            )
        )
        driver.execute_script("arguments[0].click();", lpu_local_menu)

        # Aguarda requisição alvo
        print(f"► Aguardando requisição: .../{URL_ALVO}")
        WebDriverWait(driver, 30).until(
            lambda drv: any(URL_ALVO in req.url for req in drv.requests)
        )

        # Captura headers
        for req in driver.requests:
            if URL_ALVO in req.url:
                headers          = req.headers
                token            = headers.get('Authorization') or headers.get('authorization')
                ido              = headers.get('ido') or headers.get('Ido')
                cookie           = headers.get('Cookie') or headers.get('cookie')
                token_expiracao  = decodificar_expiracao_jwt(token)
                break

        if not token:
            raise ValueError("Token não encontrado na requisição.")

        # Valida expiração
        agora = int(time.time())
        if token_expiracao and token_expiracao <= agora:
            raise ValueError("Token capturado já está expirado.")

        minutos = (token_expiracao - agora) // 60 if token_expiracao else '?'
        print(f"✔ Token válido! Expira em {minutos} minutos.")
        break

    except Exception as e:
        print(f"✖ Erro na tentativa {tentativa}: {e}")
        if tentativa == MAX_TENTATIVAS:
            print("✖ Todas as tentativas falharam.")
            raise  # Faz o GitHub Actions marcar o job como falha

    finally:
        if driver:
            driver.quit()
            print("► Driver encerrado.")


# ============================================================
#  SEÇÃO 5 — SALVA NO GOOGLE DRIVE
# ============================================================

print("\n► Autenticando no Google Drive...")
drive_service = autenticar_drive()

df = pd.DataFrame([{
    'Token':          token,
    'Ido':            ido,
    'Cookie':         cookie,
    'TokenExpiracao': token_expiracao
}])

salvar_excel_no_drive(drive_service, df)

print("\n" + "="*55)
print("  CONCLUÍDO COM SUCESSO")
print("="*55)
print(f"  Token:    {token[:50]}...")
print(f"  Ido:      {ido}")
print(f"  Expira:   {token_expiracao} (Unix)")
print("="*55)
