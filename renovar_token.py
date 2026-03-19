# ============================================================
#  EQS - RENOVAÇÃO AUTOMÁTICA DE TOKEN | GitHub Actions
#  Usa Selenium 4 + logs de performance do Chrome
# ============================================================

import os
import io
import base64
import json
import time
import traceback
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

EQS_LOGIN = os.environ['EQS_LOGIN']
EQS_PASSWORD = os.environ['EQS_PASSWORD']
FOLDER_ID = os.environ['GOOGLE_DRIVE_FOLDER_ID']
SA_JSON = os.environ['GOOGLE_SERVICE_ACCOUNT_JSON']
EQS_HEADLESS = os.environ.get('EQS_HEADLESS', 'true').lower() != 'false'

MAX_TENTATIVAS = 3
URL_ALVO = "chamado/rel-reembolsavel-chamado-estacao/listar"
ARQUIVO_EXCEL = "Eqs_Tokens.xlsx"


# ============================================================
#  SEÇÃO 2 — GOOGLE DRIVE (SERVICE ACCOUNT)
# ============================================================

def autenticar_drive():
    """Autentica no Google Drive via Service Account."""
    info = json.loads(SA_JSON)
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
        print("✔ Novo arquivo criado no Drive")


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


def validar_ambiente():
    """Valida e registra configurações essenciais antes da execução."""
    obrigatorias = {
        'EQS_LOGIN': EQS_LOGIN,
        'EQS_PASSWORD': EQS_PASSWORD,
        'GOOGLE_DRIVE_FOLDER_ID': FOLDER_ID,
        'GOOGLE_SERVICE_ACCOUNT_JSON': SA_JSON,
    }
    faltando = [nome for nome, valor in obrigatorias.items() if not str(valor).strip()]
    if faltando:
        raise RuntimeError(f"Variáveis obrigatórias ausentes ou vazias: {', '.join(faltando)}")

    try:
        json.loads(SA_JSON)
    except json.JSONDecodeError as e:
        raise RuntimeError(
            "GOOGLE_SERVICE_ACCOUNT_JSON não contém um JSON válido. "
            "No GitHub Actions, salve o conteúdo bruto do arquivo JSON na secret."
        ) from e

    print(f"► Execução headless: {EQS_HEADLESS}")


def configurar_driver():
    """
    Inicializa o Chrome com Selenium 4 usando performance logs,
    compatível com Selenium Python no GitHub Actions.
    """
    import subprocess
    import shutil

    print(f"► Chrome path : {shutil.which('google-chrome') or shutil.which('google-chrome-stable') or 'NÃO ENCONTRADO'}")
    print(f"► ChromeDriver: {shutil.which('chromedriver') or 'NÃO ENCONTRADO'}")

    for chrome_bin in ('google-chrome', 'google-chrome-stable', 'chromium-browser', 'chromium'):
        if shutil.which(chrome_bin):
            try:
                result = subprocess.run([chrome_bin, '--version'], capture_output=True, text=True, check=False)
                print(f"► Chrome versão ({chrome_bin}): {result.stdout.strip() or result.stderr.strip()}")
                break
            except Exception as e:
                print(f"► Erro ao checar {chrome_bin}: {e}")

    chrome_options = Options()
    if EQS_HEADLESS:
        chrome_options.add_argument('--headless=new')
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')
    chrome_options.add_argument('--disable-gpu')
    chrome_options.add_argument('--window-size=1920,1080')
    chrome_options.add_argument('--disable-blink-features=AutomationControlled')
    chrome_options.add_argument('--disable-extensions')
    chrome_options.add_argument('--ignore-certificate-errors')
    chrome_options.add_argument('--ignore-ssl-errors=yes')
    chrome_options.add_argument('--remote-debugging-port=9222')
    chrome_options.set_capability('goog:loggingPrefs', {'performance': 'ALL', 'browser': 'ALL'})
    chrome_options.add_experimental_option('excludeSwitches', ['enable-automation'])
    chrome_options.add_experimental_option('useAutomationExtension', False)

    erros = []

    for path in ['/usr/bin/chromedriver', '/usr/local/bin/chromedriver']:
        if os.path.exists(path):
            try:
                print(f"► Tentando chromedriver em {path}...")
                driver = webdriver.Chrome(service=Service(path), options=chrome_options)
                print(f"✔ Driver iniciado via {path}")
                return driver
            except Exception as e:
                erros.append(f"{path}: {e}")

    try:
        print('► Tentando ChromeDriverManager...')
        driver = webdriver.Chrome(
            service=Service(ChromeDriverManager().install()),
            options=chrome_options
        )
        print('✔ Driver iniciado via ChromeDriverManager')
        return driver
    except Exception as e:
        erros.append(f"ChromeDriverManager: {e}")

    try:
        print('► Tentando Selenium Manager...')
        driver = webdriver.Chrome(options=chrome_options)
        print('✔ Driver iniciado via Selenium Manager')
        return driver
    except Exception as e:
        erros.append(f"Selenium Manager: {e}")

    raise RuntimeError('Não foi possível iniciar o Chrome. Erros:\n' + '\n'.join(erros))


def extrair_headers_performance_logs(driver):
    """Lê os performance logs do Chrome e procura a requisição alvo."""
    logs = driver.get_log('performance')
    for entry in logs:
        try:
            message = json.loads(entry['message'])['message']
        except (KeyError, json.JSONDecodeError, TypeError):
            continue

        if message.get('method') != 'Network.requestWillBeSent':
            continue

        request = message.get('params', {}).get('request', {})
        url = request.get('url', '')
        if URL_ALVO not in url:
            continue

        headers = request.get('headers', {})
        if headers:
            print(f"✔ Requisição alvo interceptada via performance logs: {url}")
            return headers
    return None


def aguardar_headers_requisicao(driver, timeout=30):
    """Aguarda até a requisição alvo aparecer nos performance logs."""
    def _buscar(_):
        headers = extrair_headers_performance_logs(driver)
        return headers if headers else False

    return WebDriverWait(driver, timeout, poll_frequency=1).until(_buscar)


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
            drv.execute_script('return document.readyState') == 'complete'
            or len(drv.find_elements(By.CSS_SELECTOR, "input[type='password']")) > 0
            or len(drv.find_elements(By.ID, 'login')) > 0
            or len(drv.find_elements(By.NAME, 'login')) > 0
        )
    )
    if 'chrome-error' in driver.current_url:
        raise TimeoutException(f"Chrome abriu página de erro: {driver.current_url}")


def login_ainda_visivel(driver):
    """Indica se o formulário de login ainda está visível na tela."""
    seletores = [
        (By.ID, 'login'),
        (By.NAME, 'login'),
        (By.ID, 'senha'),
        (By.NAME, 'senha'),
        (By.CSS_SELECTOR, "input[type='password']"),
    ]
    for by, valor in seletores:
        for elemento in driver.find_elements(by, valor):
            try:
                if elemento.is_displayed():
                    return True
            except Exception:
                continue
    return False


def extrair_mensagem_erro_login(driver):
    """Coleta mensagens de erro visíveis na tela após tentativa de login."""
    seletores = [
        (By.CSS_SELECTOR, '.alert'),
        (By.CSS_SELECTOR, '.alert-danger'),
        (By.CSS_SELECTOR, '.error'),
        (By.CSS_SELECTOR, '.error-message'),
        (By.CSS_SELECTOR, '.toast-message'),
        (By.CSS_SELECTOR, '.swal2-html-container'),
    ]
    mensagens = []
    for by, valor in seletores:
        for elemento in driver.find_elements(by, valor):
            try:
                texto = elemento.text.strip()
            except Exception:
                continue
            if texto and texto not in mensagens:
                mensagens.append(texto)
    return mensagens


def aguardar_pos_login(driver, timeout=30):
    """Aguarda sinais confiáveis de login concluído em SPA sem depender só da URL."""
    url_login = 'https://eqs.arenanet.com.br/dist/#/login'

    def _login_concluido(drv):
        if 'chrome-error' in drv.current_url:
            raise TimeoutException(f"Chrome abriu página de erro: {drv.current_url}")

        if drv.current_url != url_login:
            return True

        if not login_ainda_visivel(drv):
            return True

        indicadores_pos_login = [
            (By.XPATH, "//span[text()='Relatórios (CHM)']"),
            (By.XPATH, "//span[text()='Itens de LPU Por Local']"),
            (By.CSS_SELECTOR, 'nav'),
            (By.CSS_SELECTOR, '.sidebar'),
        ]
        for by, valor in indicadores_pos_login:
            for elemento in drv.find_elements(by, valor):
                try:
                    if elemento.is_displayed():
                        return True
                except Exception:
                    continue

        mensagens = extrair_mensagem_erro_login(drv)
        if mensagens:
            raise RuntimeError('Falha no login: ' + ' | '.join(mensagens))

        return False

    try:
        return WebDriverWait(driver, timeout, poll_frequency=1).until(_login_concluido)
    except TimeoutException as exc:
        mensagens = extrair_mensagem_erro_login(driver)
        detalhes = f" URL atual: {driver.current_url}"
        if mensagens:
            detalhes += ' | Mensagens: ' + ' | '.join(mensagens)
        raise TimeoutException(
            'Login não concluiu dentro do tempo esperado.' + detalhes
        ) from exc


def dump_diagnostico_pagina(driver, prefixo='diagnostico'):
    """Salva screenshot e HTML para troubleshooting no GitHub Actions."""
    timestamp = int(time.time())
    artefatos = [
        ('png', lambda f: driver.save_screenshot(f)),
        ('html', lambda f: open(f, 'w', encoding='utf-8').write(driver.page_source)),
    ]
    for ext, fn in artefatos:
        filepath = f"{prefixo}_{timestamp}.{ext}"
        try:
            fn(filepath)
            print(f"► Diagnóstico salvo: {filepath}")
        except Exception as e:
            print(f"⚠ Falha ao salvar {ext}: {e}")


# ============================================================
#  SEÇÃO 4 — CAPTURA DO TOKEN
# ============================================================

validar_ambiente()

token = ido = cookie = token_expiracao = None

for tentativa in range(1, MAX_TENTATIVAS + 1):
    driver = None
    print(f"\n{'=' * 55}")
    print(f"  Tentativa {tentativa}/{MAX_TENTATIVAS}")
    print(f"{'=' * 55}")

    try:
        driver = configurar_driver()
        driver.execute_cdp_cmd('Network.enable', {})
        driver.get_log('performance')

        print('► Acessando página de login...')
        driver.get('https://eqs.arenanet.com.br/dist/#/login')
        print(f"► URL: {driver.current_url} | Título: {driver.title}")

        aguardar_login_disponivel(driver, timeout=40)

        campo_login = aguardar_primeiro_elemento_clicavel(
            driver, timeout=30,
            seletores=[
                (By.ID, 'login'),
                (By.NAME, 'login'),
                (By.CSS_SELECTOR, "input[type='text']"),
                (By.CSS_SELECTOR, "input[type='email']"),
            ],
        )
        campo_login.clear()
        campo_login.send_keys(EQS_LOGIN)

        campo_senha = aguardar_primeiro_elemento_clicavel(
            driver, timeout=20,
            seletores=[
                (By.ID, 'senha'),
                (By.NAME, 'senha'),
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
                (By.CSS_SELECTOR, 'button.btn.btn-primary'),
                (By.TAG_NAME, 'button'),
            ],
        )
        botao.click()

        print('► Aguardando conclusão do login...')
        aguardar_pos_login(driver, timeout=30)
        print(f"✔ Login bem-sucedido! URL atual: {driver.current_url}")

        print("► Expandindo menu 'Relatórios (CHM)'...")
        relatorios_menu = WebDriverWait(driver, 30).until(
            EC.presence_of_element_located(
                (By.XPATH, "//span[text()='Relatórios (CHM)']/..")
            )
        )
        driver.execute_script('arguments[0].click();', relatorios_menu)

        print("► Clicando em 'Itens de LPU Por Local'...")
        lpu_local_menu = WebDriverWait(driver, 30).until(
            EC.presence_of_element_located(
                (By.XPATH, "//span[text()='Itens de LPU Por Local']/..")
            )
        )
        driver.execute_script('arguments[0].click();', lpu_local_menu)

        print(f"► Aguardando interceptação da requisição: .../{URL_ALVO}")
        headers_capturados = aguardar_headers_requisicao(driver, timeout=30)

        token = headers_capturados.get('authorization') or headers_capturados.get('Authorization')
        ido = headers_capturados.get('ido') or headers_capturados.get('Ido') or headers_capturados.get('IDO')
        cookie = headers_capturados.get('cookie') or headers_capturados.get('Cookie')

        if not token:
            raise ValueError('Token (Authorization) não encontrado nos headers capturados.')

        token_expiracao = decodificar_expiracao_jwt(token)
        agora = int(time.time())

        if token_expiracao and token_expiracao <= agora:
            raise ValueError(f'Token capturado já expirou (exp={token_expiracao}, agora={agora}).')

        minutos = (token_expiracao - agora) // 60 if token_expiracao else '?'
        print(f"✔ Token válido! Expira em ~{minutos} minutos.")
        break

    except Exception as e:
        print(f"✖ Erro na tentativa {tentativa}: {type(e).__name__}: {e!r}")
        print(traceback.format_exc())
        if driver:
            print(f"► URL no momento do erro: {driver.current_url}")
            dump_diagnostico_pagina(driver, prefixo=f'falha_tentativa_{tentativa}')
        if tentativa == MAX_TENTATIVAS:
            print('✖ Todas as tentativas falharam.')
            raise

    finally:
        if driver:
            driver.quit()
            print('► Driver encerrado.')


# ============================================================
#  SEÇÃO 5 — SALVA NO GOOGLE DRIVE
# ============================================================

print('\n► Autenticando no Google Drive...')
drive_service = autenticar_drive()

df = pd.DataFrame([{
    'Token': token,
    'Ido': ido,
    'Cookie': cookie,
    'TokenExpiracao': token_expiracao,
}])

salvar_excel_no_drive(drive_service, df)

print('\n' + '=' * 55)
print('  CONCLUÍDO COM SUCESSO')
print('=' * 55)
print(f'  Token  : {token[:60]}...')
print(f'  Ido    : {ido}')
print(f'  Expira : {token_expiracao} (Unix timestamp)')
print('=' * 55)
