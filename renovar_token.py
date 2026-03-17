# ============================================================
#  EQS - RENOVAÇÃO AUTOMÁTICA DE TOKEN | GitHub Actions
#  Usa Selenium 4 + CDP nativo (sem selenium-wire)
# ============================================================

import os
import io
import base64
import json
import time
import traceback
import threading
import pandas as pd

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from webdriver_manager.chrome import ChromeDriverManager

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
    """
    Inicializa o Chrome com Selenium 4 puro (sem selenium-wire).
    Ativa o remote debugging port necessário para o CDP funcionar.
    """
    import subprocess, shutil

    print(f"► Chrome path : {shutil.which('google-chrome') or shutil.which('google-chrome-stable') or 'NÃO ENCONTRADO'}")
    print(f"► ChromeDriver: {shutil.which('chromedriver') or 'NÃO ENCONTRADO'}")

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
    chrome_options.add_argument("--remote-debugging-port=9222")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option("useAutomationExtension", False)

    erros = []

    # Abordagem 1: chromedriver no PATH do sistema
    for path in ["/usr/bin/chromedriver", "/usr/local/bin/chromedriver"]:
        if shutil.which(path.split("/")[-1]) or os.path.exists(path):
            try:
                print(f"► Tentando chromedriver em {path}...")
                driver = webdriver.Chrome(service=Service(path), options=chrome_options)
                print(f"✔ Driver iniciado via {path}")
                return driver
            except Exception as e:
                erros.append(f"{path}: {e}")

    # Abordagem 2: ChromeDriverManager (baixa a versão compatível automaticamente)
    try:
        print("► Tentando ChromeDriverManager...")
        driver = webdriver.Chrome(
            service=Service(ChromeDriverManager().install()),
            options=chrome_options
        )
        print("✔ Driver iniciado via ChromeDriverManager")
        return driver
    except Exception as e:
        erros.append(f"ChromeDriverManager: {e}")

    # Abordagem 3: auto-detect do Selenium
    try:
        print("► Tentando Selenium auto-detect...")
        driver = webdriver.Chrome(options=chrome_options)
        print("✔ Driver iniciado via auto-detect")
        return driver
    except Exception as e:
        erros.append(f"Auto-detect: {e}")

    raise RuntimeError("Não foi possível iniciar o Chrome. Erros:\n" + "\n".join(erros))


def habilitar_interceptacao_cdp(driver):
    """
    Ativa o CDP (Chrome DevTools Protocol) para interceptar requisições
    de rede sem precisar de proxy externo (substitui o selenium-wire).
    Retorna um dicionário compartilhado que será populado com os headers
    da primeira requisição que bater em URL_ALVO.
    """
    headers_capturados = {}
    lock = threading.Lock()

    # Ativa a domain Network do CDP
    driver.execute_cdp_cmd("Network.enable", {})

    def on_request(params):
        url = params.get("request", {}).get("url", "")
        if URL_ALVO in url:
            with lock:
                if not headers_capturados:   # captura apenas a primeira ocorrência
                    headers_capturados.update(params.get("request", {}).get("headers", {}))
                    print(f"✔ Requisição alvo interceptada via CDP: .../{URL_ALVO}")

    driver.add_cdp_listener("Network.requestWillBeSent", on_request)
    return headers_capturados


def aguardar_primeiro_elemento_clicavel(driver, timeout, seletores):
    """Retorna o primeiro elemento clicável encontrado entre múltiplos seletores."""
    ultimo_erro = None
    for by, valor in seletores:
        try:
            return WebDriverWait(driver, timeout).until(
                EC.element_to_be_clickable((by, valor))
            )
        except TimeoutException as e:
            ultimo_erro = e
    raise TimeoutException(
        f"Nenhum elemento clicável encontrado com os seletores: {seletores}"
    ) from ultimo_erro


def aguardar_login_disponivel(driver, timeout=40):
    """Aguarda a página de login ficar utilizável."""
    WebDriverWait(driver, timeout).until(
        lambda drv: (
            drv.execute_script("return document.readyState") == "complete"
            or len(drv.find_elements(By.CSS_SELECTOR, "input[type='password']")) > 0
            or len(drv.find_elements(By.ID, "login")) > 0
            or len(drv.find_elements(By.NAME, "login")) > 0
        )
    )
    if "chrome-error" in driver.current_url:
        raise TimeoutException(f"Chrome abriu página de erro: {driver.current_url}")


def dump_diagnostico_pagina(driver, prefixo="diagnostico"):
    """Salva screenshot e HTML para troubleshooting no GitHub Actions."""
    timestamp = int(time.time())
    for ext, fn in [("png", lambda f: driver.save_screenshot(f)),
                    ("html", lambda f: open(f, "w", encoding="utf-8").write(driver.page_source))]:
        filepath = f"{prefixo}_{timestamp}.{ext}"
        try:
            fn(filepath)
            print(f"► Diagnóstico salvo: {filepath}")
        except Exception as e:
            print(f"⚠ Falha ao salvar {ext}: {e}")


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

        # Ativa a interceptação CDP ANTES de carregar qualquer página
        headers_capturados = habilitar_interceptacao_cdp(driver)

        # ── Login ──────────────────────────────────────────────
        print("► Acessando página de login...")
        driver.get("https://eqs.arenanet.com.br/dist/#/login")
        print(f"► URL: {driver.current_url} | Título: {driver.title}")

        aguardar_login_disponivel(driver, timeout=40)

        campo_login = aguardar_primeiro_elemento_clicavel(
            driver, timeout=30,
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
            driver, timeout=20,
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
            driver, timeout=20,
            seletores=[
                (By.CSS_SELECTOR, "button[type='submit']"),
                (By.CSS_SELECTOR, "button.btn.btn-primary"),
                (By.TAG_NAME, "button"),
            ],
        )
        botao.click()

        print("► Aguardando redirecionamento após login...")
        WebDriverWait(driver, 30).until(
            lambda drv: drv.current_url != "https://eqs.arenanet.com.br/dist/#/login"
        )
        print(f"✔ Login bem-sucedido! URL atual: {driver.current_url}")

        # ── Navegação ──────────────────────────────────────────
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

        # ── Aguarda o CDP capturar a requisição alvo ───────────
        print(f"► Aguardando interceptação CDP da requisição: .../{URL_ALVO}")
        WebDriverWait(driver, 30).until(lambda _: bool(headers_capturados))

        # ── Extrai os valores dos headers ──────────────────────
        # Headers HTTP são case-insensitive; o CDP os entrega em lowercase
        token  = headers_capturados.get("authorization") or headers_capturados.get("Authorization")
        ido    = headers_capturados.get("ido")
        cookie = headers_capturados.get("cookie") or headers_capturados.get("Cookie")

        if not token:
            raise ValueError("Token (Authorization) não encontrado nos headers capturados.")

        token_expiracao = decodificar_expiracao_jwt(token)
        agora = int(time.time())

        if token_expiracao and token_expiracao <= agora:
            raise ValueError(f"Token capturado já expirou (exp={token_expiracao}, agora={agora}).")

        minutos = (token_expiracao - agora) // 60 if token_expiracao else "?"
        print(f"✔ Token válido! Expira em ~{minutos} minutos.")
        break   # sai do loop de tentativas

    except Exception as e:
        print(f"✖ Erro na tentativa {tentativa}: {type(e).__name__}: {e!r}")
        print(traceback.format_exc())
        if driver:
            print(f"► URL no momento do erro: {driver.current_url}")
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
    "Token":          token,
    "Ido":            ido,
    "Cookie":         cookie,
    "TokenExpiracao": token_expiracao,
}])

salvar_excel_no_drive(drive_service, df)

print("\n" + "=" * 55)
print("  CONCLUÍDO COM SUCESSO")
print("=" * 55)
print(f"  Token  : {token[:60]}...")
print(f"  Ido    : {ido}")
print(f"  Expira : {token_expiracao} (Unix timestamp)")
print("=" * 55)