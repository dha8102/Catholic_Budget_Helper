"""테스트 공통 도구: 임시 data 폴더, 모의(가짜) AI, 샘플 파일 로더."""
import copy
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
    monkeypatch.setattr(config, "MAX_INPUT_CHARS", 300_000)
    monkeypatch.setattr(config, "PAST_DOC_MAX_CHARS", 20_000)
    monkeypatch.setattr(config, "MAX_SELECTED_CASES", 5)
    monkeypatch.setattr(config, "MAX_FILE_MB", 20)
    monkeypatch.setattr(config, "OPENAI_API_KEY", "sk-test-env")  # 실제 .env 키에 영향받지 않게
    monkeypatch.setattr(config, "OPENAI_MODEL", "gpt-6-luna")
    storage.init_storage()
    return tmp_path / "data"


class FakeAI:
    """task 이름별로 정해 둔 JSON을 돌려주는 모의 AI. 받은 프롬프트를 기록한다."""

    def __init__(self, responses: dict):
        self.responses = responses
        self.calls = []

    def complete_json(self, task, system, user):
        self.calls.append((task, system, user))
        r = self.responses[task]
        return r(user) if callable(r) else copy.deepcopy(r)

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
def review_response(expense_item, amount, status, quote=None, unclear=None):
    refs = [{"guideline": EDU_GUIDELINE, "quote": quote}] if quote else []
    return {
        "business_name": "2026년 테스트 사업",
        "document_summary": "테스트 문서",
        "items": [{
            "item_no": 1, "expense_item": expense_item, "amount": amount, "purpose": "테스트",
            "status": status, "related_guidelines": refs, "reasoning": "테스트 판단 근거",
            "unclear_points": unclear or [],
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
