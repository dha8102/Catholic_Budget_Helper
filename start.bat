@echo off
chcp 65001 > nul
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" goto install
echo [1/3] 가상환경을 만듭니다...
py -3 -m venv .venv
if not exist ".venv\Scripts\python.exe" python -m venv .venv
if not exist ".venv\Scripts\python.exe" goto nopython

:install
".venv\Scripts\python.exe" -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"
if errorlevel 1 goto oldpython
if exist ".venv\installed.ok" goto env
echo [2/3] 필요한 패키지를 설치합니다. 처음 한 번만 실행되며 몇 분 걸릴 수 있습니다...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto pipfail
echo ok> ".venv\installed.ok"

:env
if exist ".env" goto run
copy ".env.example" ".env" > nul
echo.
echo [안내] .env 파일을 만들었습니다. 메모장에서 OPENAI_API_KEY 값을 입력하고 저장한 뒤 메모장을 닫아 주세요.
notepad ".env"

:run
echo [3/3] 프로그램을 시작합니다. 잠시 후 브라우저에서 http://127.0.0.1:8501 이 열립니다.
echo 종료하려면 이 창에서 Ctrl+C 를 누르거나 창을 닫으세요.
".venv\Scripts\python.exe" -m streamlit run app.py --server.address 127.0.0.1 --server.port 8501
pause
exit /b 0

:nopython
echo [오류] Python을 찾을 수 없습니다. https://www.python.org 에서 Python 3.11 이상을 설치하세요.
echo        설치 화면에서 "Add python.exe to PATH" 를 꼭 체크하세요.
pause
exit /b 1

:oldpython
echo [오류] Python 3.11 이상이 필요합니다. 최신 Python을 설치한 뒤 .venv 폴더를 지우고 다시 실행하세요.
pause
exit /b 1

:pipfail
echo [오류] 패키지 설치에 실패했습니다. 인터넷 연결을 확인한 뒤 다시 실행하세요.
pause
exit /b 1
