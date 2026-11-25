"""start.bat 도우미: requirements.txt가 마지막 설치 이후 바뀌었는지 확인한다(바뀌었으면 패키지를 다시 설치).

    python scripts/deps_marker.py         → 설치된 상태와 같으면 종료 코드 0, 다르면 1
    python scripts/deps_marker.py write   → 지금 requirements.txt 기준으로 설치 완료 표시
"""
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MARK = ROOT / ".venv" / "installed.ok"
digest = hashlib.sha256((ROOT / "requirements.txt").read_bytes()).hexdigest()

if sys.argv[1:] == ["write"]:
    MARK.write_text(digest, encoding="ascii")
    sys.exit(0)
sys.exit(0 if MARK.is_file() and MARK.read_text(encoding="ascii", errors="ignore").strip() == digest else 1)
