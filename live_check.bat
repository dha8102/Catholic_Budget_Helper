@echo off
chcp 65001 > nul
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo [오류] 먼저 start.bat 을 한 번 실행해 가상환경과 패키지를 설치하세요.
  pause
  exit /b 1
)
if not exist ".env" (
  echo [오류] .env 파일이 없습니다. start.bat 을 실행해 .env 를 만들고 API 키를 입력하세요.
  pause
  exit /b 1
)
echo 실제 OpenAI API로 테스트 1, 2, 3, 6 을 실행합니다. 몇 분 걸리며 API 요금이 소액 발생합니다.
".venv\Scripts\python.exe" scripts\live_check.py %*
pause
