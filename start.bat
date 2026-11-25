@echo off
chcp 65001 > nul
setlocal
cd /d "%~dp0"
rem start.bat web  →  웹 모드(같은 네트워크의 다른 PC에서 브라우저로 접속, 각자 API 키 입력)
set "MODE=local"
if /i "%~1"=="web" set "MODE=web"
set "PY=.venv\Scripts\python.exe"
set "UV=.tools\uv\uv.exe"
rem 내려받은 전용 Python·패키지 캐시는 프로그램 폴더 안(.tools)에만 둔다
set "UV_PYTHON_INSTALL_DIR=%CD%\.tools\python"
set "UV_CACHE_DIR=%CD%\.tools\cache"

if exist "%PY%" goto check
echo [1/3] 실행 환경을 준비합니다. 처음 한 번만 실행됩니다...
rem 1) 이 PC에 Python 3.11 이상이 설치되어 있으면 그것을 쓴다
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" > nul 2>&1
if not errorlevel 1 (
  py -3 -m venv .venv
  goto created
)
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" > nul 2>&1
if not errorlevel 1 (
  python -m venv .venv
  goto created
)
rem 2) 없으면 프로그램 폴더 안에 전용 Python을 내려받는다 (관리자 권한·설치 과정 없음)
echo     이 PC에 Python이 없어 프로그램 폴더 안에 전용 Python을 내려받습니다.
echo     따로 설치할 것은 없으며, 인터넷 연결이 필요하고 몇 분 걸릴 수 있습니다.
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\bootstrap.ps1" -Root "%CD%\."
if errorlevel 4 goto bootfail
if errorlevel 3 goto checksumfail
if errorlevel 2 goto downloadfail
if errorlevel 1 goto bootfail

:created
if not exist "%PY%" goto bootfail

:check
"%PY%" -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"
if errorlevel 1 goto oldpython
"%PY%" scripts\deps_marker.py
if not errorlevel 1 goto env
echo [2/3] 필요한 패키지를 설치합니다. 처음(또는 프로그램 업데이트 후) 한 번만 실행되며 몇 분 걸릴 수 있습니다...
if exist "%UV%" (
  "%UV%" pip install --python "%PY%" -r requirements.txt
) else (
  "%PY%" -m pip install --upgrade pip
  "%PY%" -m pip install -r requirements.txt
)
if errorlevel 1 goto pipfail
"%PY%" scripts\deps_marker.py write

:env
if not exist ".env" copy ".env.example" ".env" > nul

:run
if "%MODE%"=="web" goto runweb
echo [3/3] 프로그램을 시작합니다. 잠시 후 브라우저에서 http://127.0.0.1:8501 이 열립니다.
echo  - OpenAI API 키는 화면 왼쪽 아래 '환경 설정'에서 입력하세요.
echo  - 종료하려면 이 창에서 Ctrl+C 를 누르거나 창을 닫으세요.
"%PY%" -m streamlit run app.py --server.address 127.0.0.1 --server.port 8501
pause
exit /b 0

:runweb
set "WEB_MODE=1"
echo [3/3] 웹 모드로 시작합니다. 같은 네트워크의 다른 PC에서 아래 주소 중 하나로 접속하세요.
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"IPv4"') do for /f "tokens=*" %%b in ("%%a") do echo     http://%%b:8501
echo     (이 PC에서는 http://127.0.0.1:8501)
echo.
echo  - 처음 실행 시 Windows 방화벽 창이 뜨면 "개인 네트워크" 허용을 눌러야 다른 PC에서 접속됩니다.
echo  - 접속한 모든 사람이 같은 지침과 저장된 사례를 함께 봅니다.
echo  - 종료하려면 이 창에서 Ctrl+C 를 누르거나 창을 닫으세요.
"%PY%" -m streamlit run app.py --server.address 0.0.0.0 --server.port 8501 --server.headless true
pause
exit /b 0

:downloadfail
echo [오류] 전용 Python 도구(uv)를 내려받지 못했습니다. 인터넷 연결을 확인한 뒤 다시 실행하세요.
echo        회사·학교 방화벽이 github.com 접속을 막는 경우 전산 담당자에게 문의하거나,
echo        https://www.python.org 에서 Python 3.11 이상을 설치("Add python.exe to PATH" 체크)한 뒤 다시 실행하세요.
pause
exit /b 1

:checksumfail
echo [오류] 내려받은 파일이 원본과 다릅니다(변조 또는 다운로드 손상 가능). 실행하지 않았습니다. 잠시 후 다시 시도하세요.
pause
exit /b 1

:bootfail
echo [오류] 실행 환경을 만들지 못했습니다. 인터넷 연결을 확인하고, 프로그램 폴더의 .venv 와 .tools 폴더를 지운 뒤 다시 실행하세요.
pause
exit /b 1

:oldpython
echo [오류] Python 3.11 이상이 필요합니다. 프로그램 폴더의 .venv 폴더를 지운 뒤 다시 실행하면 자동으로 다시 준비합니다.
pause
exit /b 1

:pipfail
echo [오류] 패키지 설치에 실패했습니다. 인터넷 연결을 확인한 뒤 다시 실행하세요.
pause
exit /b 1
