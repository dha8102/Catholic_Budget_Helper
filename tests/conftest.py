"""테스트 공통 도구: 임시 data 폴더, 모의(가짜) AI, 샘플 파일 로더."""
import copy
import hashlib
from pathlib import Path

import pytest

import config
import storage

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "sample_data"

EDU_GUIDELINE = "교육부_대학혁신지원사업_운영지침(가상).txt"


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """실제 data 폴더 대신 임시 폴더를 쓴다. .env 설정값에 영향받지 않게 한도도 고정한다."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    # 실제 프로그램 폴더의 저장 폴더 설정 파일·예전 data 폴더를 건드리거나 읽지 않게 한다
    monkeypatch.setattr(config, "LOCAL_SETTINGS", tmp_path / "local_settings.json")
    monkeypatch.setattr(config, "LEGACY_DATA_DIR", tmp_path / "legacy_data")
    monkeypatch.setattr(config, "MAX_INPUT_CHARS", 300_000)
    monkeypatch.setattr(config, "PAST_DOC_MAX_CHARS", 20_000)
    monkeypatch.setattr(config, "MAX_SELECTED_CASES", 5)
    monkeypatch.setattr(config, "MAX_FILE_MB", 20)
    monkeypatch.setattr(config, "OPENAI_API_KEY", "sk-test-env")  # 실제 .env 키에 영향받지 않게
    monkeypatch.setattr(config, "OPENAI_MODEL", "gpt-6-luna")
    storage.init_storage()
    return tmp_path / "data"


def fake_vector(text: str, dim: int = 256) -> list[float]:
    """글자 2개 묶음을 해시해 만든 벡터. 비슷한 글은 비슷한 벡터가 되어 실제 임베딩처럼 동작한다."""
    vec = [0.0] * dim
    s = "".join(text.split())
    for i in range(len(s) - 1):
        vec[int(hashlib.md5(s[i:i + 2].encode()).hexdigest(), 16) % dim] += 1.0
    return vec


class FakeAI:
    """task 이름별로 정해 둔 JSON을 돌려주는 모의 AI. 받은 프롬프트를 기록한다.
    키워드 없는 옛 사례의 키워드 추출(case_keywords)은 따로 정하지 않으면 빈 결과를 돌려준다."""

    def __init__(self, responses: dict, embed_error: Exception | None = None):
        self.responses = responses
        self.calls = []
        self.embedded: list[str] = []  # 임베딩한 글 기록
        self.embed_error = embed_error

    def complete_json(self, task, system, user):
        self.calls.append((task, system, user))
        if task == "case_keywords" and task not in self.responses:
            return {"cases": []}
        r = self.responses[task]
        return r(user) if callable(r) else copy.deepcopy(r)

    def embed(self, texts):
        if self.embed_error:
            raise self.embed_error
        self.embedded += list(texts)
        return [fake_vector(t) for t in texts]

    def prompts(self, task):
        return [u for t, _, u in self.calls if t == task]

    def tasks(self):
        return [t for t, _, _ in self.calls]


def load_doc(rel: str) -> dict:
    path = SAMPLES / "documents" / rel
    data = path.read_bytes()
    return {"name": path.name, "data": data, "text": storage.extract_text(path.name, data)}


def load_guidelines() -> list[dict]:
    return [{"name": p.name, "text": storage.extract_text_from_file(p)} for p in sorted((SAMPLES / "guidelines").iterdir())]


# ---------------------------------------------------------------- 모의 AI 응답
def review_response(expense_item, amount, status, quote=None, unclear=None, keywords=None):
    refs = [{"guideline": EDU_GUIDELINE, "quote": quote}] if quote else []
    return {
        "business_name": "2026년 테스트 사업",
        "document_summary": "테스트 문서",
        "items": [{
            "item_no": 1, "expense_item": expense_item, "amount": amount, "purpose": "테스트",
            "status": status, "related_guidelines": refs, "reasoning": "테스트 판단 근거",
            "unclear_points": unclear or [], "keywords": keywords or [],
        }],
        "guideline_priority_note": "",
    }


QUESTIONS = {"questions": [{
    "item_no": 1,
    "question": "자문수당 30만 원의 산정 근거는 무엇이며, 사업단장 승인을 받았나요?",
    "reason": "지침에 자문수당 기준이 없음",
    "reason_types": ["금액 산정 근거 불분명"],
    "referenced_case_ids": [],
    "past_case_comparison": "",
}]}


def final_response(status="판단 불가"):
    return {
        "items": [{
            "item_no": 1, "guideline_review": "지침에 기준 없음", "user_explanation": "사업단장 재가",
            "related_past_cases": "", "guideline_issues": "단가 근거 불확실", "additional_evidence": ["재가 문서"],
            "final_status": status, "opinion": "추가 증빙 필요",
        }],
        "overall": {"summary": "요약", "problem_items": "자문수당", "past_cases_summary": "",
                    "answer_reflection": "반영함", "additional_checks": ["재가 문서 확인"]},
    }


NAV_KEYS = {"신규 문서 검토": "nav_review", "지침 관리": "nav_guidelines",
            "저장된 사례": "nav_cases", "환경 설정": "nav_settings"}


def goto(at, page):
    """사이드바 메뉴 버튼을 눌러 화면을 이동한다."""
    at.button(key=NAV_KEYS[page]).click().run()
    return at
