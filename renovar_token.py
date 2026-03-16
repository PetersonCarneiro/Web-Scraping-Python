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
from selenium.common.exceptions import TimeoutException

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload


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

def autenticar_drive():
    """Autentica no Google Drive via Service Account."""
    info  = json.loads(SA_JSON)
    creds = service_account.Credentials.from_service_account_info(
        info,
        scopes=['https://www.googleapis.com/auth/drive']
    )
    return build('drive', 'v3', credentials=creds)


def salvar_excel_no_drive(service, df: pd.DataFrame):
    """Salva o DataFrame como Excel no Google Drive, substituindo o arquivo anterior."""
    resultado = service.files().list(
        q=f"name='{ARQUIVO_EXCEL}' and '{FOLDER_ID}' in parents and trashed=false",
        fields="files(id, name)"
    ).execute()

    arquivos = resultado.get('files', [])

    buffer = io.BytesIO()
    df.to_excel(buffer, index=False)
    buffer.seek(0)

    media = MediaIoBaseUpload(
        buffer,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )

    if arquivos:
        file_id = arquivos[0]['id']
        service.files().update(fileId=file_id, media_body=media).execute()
        print(f"✔ Arquivo atualizado no Drive (id: {file_id})")
    else:
        metadata = {'name': ARQUIVO_EXCEL, 'parents': [FOLDER_ID]}
        service.files().create(body=metadata, media_body=media).execute()
        print(f"✔ Novo arquivo criado no Drive")


# ============================================================
#  SEÇÃO 3 — FUNÇÕES AUXILIARES
# ============================================================

def decodificar_expiracao_jwt(token: str):
    """Extrai o campo 'exp' do payload JWT."""
    try:
        payload_b64  = token.split('.')[1]
        payload_b64 += '=' * (-len(payload_b64) % 4)
        payload      = json.loads(base64.b64decode(payload_b64).decode('utf-8'))
        return payload.get('exp')
    except Exception as e:
        print(f"⚠ Não foi possível decodificar o JWT: {e}")
        return None


def configurar_driver():
    """Inicializa o Chrome headless com Selenium Wire."""
    import subprocess
    import shutil

    # Diagnóstico do ambiente
    print(f"► Chrome path: {shutil.which('google-chrome') or shutil.which('google-chrome-stable') or 'NÃO ENCONTRADO'}")
    print(f"► ChromeDriver path: {shutil.which('chromedriver') or 'NÃO ENCONTRADO'}")

    try:
        result = subprocess.run(['google-chrome', '--version'], capture_output=True, text=True)
        print(f"► Chrome versão: {result.stdout.strip()}")
    except Exception as e:
        print(f"► Erro ao checar Chrome: {e}")

    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_argument("--disable-extensions")
    chrome_options.add_argument("--ignore-certificate-errors")
    chrome_options.add_argument("--ignore-ssl-errors=yes")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option("useAutomationExtension", False)

    seleniumwire_options = {
        # Evita falhas de TLS ao trafegar HTTPS pelo proxy interno do selenium-wire
        "verify_ssl": False,
    }

    # Tenta 3 abordagens em sequência
    erros = []

    # Abordagem 1: chromedriver no PATH
    try:
        print("► Tentando chromedriver do PATH...")
        service = Service("/usr/bin/chromedriver")
        driver  = webdriver.Chrome(
            service=service,
            options=chrome_options,
            seleniumwire_options=seleniumwire_options,
        )
        print("✔ Driver iniciado via /usr/bin/chromedriver")
        return driver
    except Exception as e:
        erros.append(f"PATH: {e}")

    # Abordagem 2: ChromeDriverManager
    try:
        print("► Tentando ChromeDriverManager...")
        service = Service(ChromeDriverManager().install())
        driver  = webdriver.Chrome(
            service=service,
            options=chrome_options,
            seleniumwire_options=seleniumwire_options,
        )
        print("✔ Driver iniciado via ChromeDriverManager")
        return driver
    except Exception as e:
        erros.append(f"ChromeDriverManager: {e}")

    # Abordagem 3: deixar o Selenium encontrar automaticamente
    try:
        print("► Tentando Selenium auto-detect...")
        driver = webdriver.Chrome(
            options=chrome_options,
            seleniumwire_options=seleniumwire_options,
        )
        print("✔ Driver iniciado via auto-detect")
        return driver
    except Exception as e:
        erros.append(f"Auto-detect: {e}")

    raise RuntimeError(f"Não foi possível iniciar o Chrome. Erros:\n" + "\n".join(erros))


def aguardar_primeiro_elemento_clicavel(driver, timeout, seletores):
    """Retorna o primeiro elemento clicável encontrado entre múltiplos seletores."""
    espera = WebDriverWait(driver, timeout)
    ultimo_erro = None

    for by, valor in seletores:
        try:
            return espera.until(EC.element_to_be_clickable((by, valor)))
        except TimeoutException as e:
            ultimo_erro = e

    raise TimeoutException(
        f"Não encontrou elemento clicável com nenhum seletor: {seletores}"
    ) from ultimo_erro


def dump_diagnostico_pagina(driver, prefixo='diagnostico'):
    """Salva screenshot e HTML para facilitar troubleshooting no GitHub Actions."""
    timestamp = int(time.time())
    screenshot = f"{prefixo}_{timestamp}.png"
    html = f"{prefixo}_{timestamp}.html"

    try:
        driver.save_screenshot(screenshot)
        print(f"► Screenshot salvo: {screenshot}")
    except Exception as e:
        print(f"⚠ Falha ao salvar screenshot: {e}")

    try:
        with open(html, 'w', encoding='utf-8') as f:
            f.write(driver.page_source)
        print(f"► HTML salvo: {html}")
    except Exception as e:
        print(f"⚠ Falha ao salvar HTML: {e}")


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

        WebDriverWait(driver, 30).until(
            lambda drv: drv.execute_script("return document.readyState") == "complete"
        )

        campo_login = aguardar_primeiro_elemento_clicavel(
            driver,
            timeout=30,
            seletores=[
                (By.ID, "login"),
                (By.NAME, "login"),
                (By.CSS_SELECTOR, "input[type='text']"),
                (By.CSS_SELECTOR, "input[type='email']"),
            ],
        )
        campo_login.clear()
        campo_login.send_keys(EQS_LOGIN)

        campo_senha = aguardar_primeiro_elemento_clicavel(
            driver,
            timeout=20,
            seletores=[
                (By.ID, "senha"),
                (By.NAME, "senha"),
                (By.CSS_SELECTOR, "input[type='password']"),
            ],
        )
        campo_senha.clear()
        campo_senha.send_keys(EQS_PASSWORD)

        time.sleep(1)

        botao = aguardar_primeiro_elemento_clicavel(
            driver,
            timeout=20,
            seletores=[
                (By.CSS_SELECTOR, "button[type='submit']"),
                (By.CSS_SELECTOR, "button.btn.btn-primary"),
                (By.TAG_NAME, "button"),
            ],
        )
        botao.click()

        print("► Aguardando redirecionamento após login...")
        WebDriverWait(driver, 30).until(
            lambda drv: (
                drv.current_url != "https://eqs.arenanet.com.br/dist/#/login"
                or any(URL_ALVO in req.url for req in drv.requests)
            )
        )
        print(f"✔ Login bem-sucedido! URL atual: {driver.current_url}")

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
                headers         = req.headers
                token           = headers.get('Authorization') or headers.get('authorization')
                ido             = headers.get('ido') or headers.get('Ido')
                cookie          = headers.get('Cookie') or headers.get('cookie')
                token_expiracao = decodificar_expiracao_jwt(token)
                break

        if not token:
            raise ValueError("Token não encontrado na requisição.")

        agora = int(time.time())
        if token_expiracao and token_expiracao <= agora:
            raise ValueError("Token capturado já está expirado.")

        minutos = (token_expiracao - agora) // 60 if token_expiracao else '?'
        print(f"✔ Token válido! Expira em {minutos} minutos.")
        break

    except Exception as e:
        print(f"✖ Erro na tentativa {tentativa}: {e}")
        if driver:
            print(f"► URL atual no erro: {driver.current_url}")
            print(f"► Título da página no erro: {driver.title}")
            dump_diagnostico_pagina(driver, prefixo=f"falha_tentativa_{tentativa}")
        if tentativa == MAX_TENTATIVAS:
            print("✖ Todas as tentativas falharam.")
            raise

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
