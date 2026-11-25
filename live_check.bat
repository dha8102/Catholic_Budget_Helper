@echo off
chcp 65001 > nul
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo [오류] 먼저 start.bat 을 한 번 실행해 실행 환경과 패키지를 준비하세요.
  pause
  exit /b 1
)
if exist ".env" goto run
copy ".env.example" ".env" > nul
:run
findstr /r /c:"^OPENAI_API_KEY=..*" ".env" > nul
if not errorlevel 1 goto go
echo [안내] 이 검증은 .env 파일의 API 키를 사용합니다. 메모장에서 OPENAI_API_KEY 값을 입력하고 저장한 뒤 메모장을 닫아 주세요.
notepad ".env"
:go
echo 실제 OpenAI API로 테스트 1, 2, 3, 6 을 실행합니다. 몇 분 걸리며 API 요금이 소액 발생합니다.
".venv\Scripts\python.exe" scripts\live_check.py %*
pause
