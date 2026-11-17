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

# 과거 사례 검색: 키워드 검색과 임베딩 검색 결과를 합쳐 상위 몇 건을 AI 선택 후보로 넘길지 (환경 설정 화면에서 변경 가능)
RETRIEVAL_TOP_K = _int_env("RETRIEVAL_TOP_K", 10)
RETRIEVAL_TOP_K_MAX = 30
# 임베딩 검색에 쓰는 OpenAI 임베딩 모델 (바꾸면 다음 검토 때 기존 사례를 자동으로 다시 변환)
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "").strip() or "text-embedding-3-small"

# 웹 모드: 같은 네트워크의 여러 사람이 브라우저로 접속(로그인 없음, 데이터는 모두 공용).
# AI 호출은 각 사용자가 화면에 입력한 API 키로만 한다. start_web.bat 이 WEB_MODE=1 로 실행한다.
WEB_MODE = os.getenv("WEB_MODE", "").strip().lower() in ("1", "true", "yes")

# 모든 로컬 데이터(지침, 사례 문서, SQLite DB)가 저장되는 폴더.
# 우선순위: 환경 설정 화면에서 고른 폴더(local_settings.json) > .env의 ADMIN_AI_DATA_DIR > 기본 폴더
DEFAULT_DATA_DIR = Path("C:/Catholic_Budget_Helper_DB") if os.name == "nt" else Path.home() / "Catholic_Budget_Helper_DB"
LEGACY_DATA_DIR = BASE_DIR / "data"  # 예전 기본 폴더(프로젝트 안). 자료가 남아 있으면 화면에서 가져오기를 안내한다.
LOCAL_SETTINGS = BASE_DIR / "local_settings.json"  # 이 PC 전체에 적용되는 설정(저장 폴더). 브라우저 쿠키 설정과 별개


def load_local_settings() -> dict:
    import json

    try:
        data = json.loads(LOCAL_SETTINGS.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_local_settings(values: dict) -> None:
    import json

    data = {**load_local_settings(), **values}
    LOCAL_SETTINGS.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


DATA_DIR = Path(str(load_local_settings().get("data_dir") or "").strip() or os.getenv("ADMIN_AI_DATA_DIR", "").strip() or DEFAULT_DATA_DIR)
