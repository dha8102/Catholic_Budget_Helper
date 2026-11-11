"""환경변수(.env)에서 설정값을 읽는다."""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "").strip() or "gpt-4.1-mini"
OPENAI_TIMEOUT = _int_env("OPENAI_TIMEOUT", 180)  # 초

# AI 1회 호출에 넣을 수 있는 최대 글자 수(모델 입력 한도에 맞춰 조정).
# 한국어는 대략 1글자 ≈ 1토큰으로 보수적으로 잡는다.
MAX_INPUT_CHARS = _int_env("MAX_INPUT_CHARS", 300_000)
# 과거 사례 원본 문서 1개당 AI에 넣는 최대 글자 수(초과 시 사용자에게 알리고 앞부분만 사용)
PAST_DOC_MAX_CHARS = _int_env("PAST_DOC_MAX_CHARS", 20_000)
# AI가 고를 수 있는 과거 사례 최대 개수
MAX_SELECTED_CASES = _int_env("MAX_SELECTED_CASES", 5)
# 업로드 파일 1개당 최대 크기(MB)
MAX_FILE_MB = _int_env("MAX_FILE_MB", 20)

# 웹 모드: 같은 네트워크의 여러 사람이 브라우저로 접속(로그인 없음, 데이터는 모두 공용).
# AI 호출은 각 사용자가 화면에 입력한 API 키로만 한다. start_web.bat 이 WEB_MODE=1 로 실행한다.
WEB_MODE = os.getenv("WEB_MODE", "").strip().lower() in ("1", "true", "yes")

# 모든 로컬 데이터(지침, 사례 문서, SQLite DB)가 저장되는 폴더
DATA_DIR = Path(os.getenv("ADMIN_AI_DATA_DIR", "").strip() or BASE_DIR / "data")
