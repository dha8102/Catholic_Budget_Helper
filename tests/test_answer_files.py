"""AI 추가 질문에 답하면서 첨부한 증빙자료: 최종 의견 반영, 사례별 저장, 이후 검토에서 재활용 (모의 AI 사용)."""
from streamlit.testing.v1 import AppTest

import ai
import review
import storage
from conftest import QUESTIONS, ROOT, FakeAI, final_response, load_doc, load_guidelines, review_response

MINUTES = "2026. 5. 15. 사업단 내부 회의록: 자문 단가 2시간 30만 원으로 결정"


def att(name, text):
    return {"name": name, "data": text.encode("utf-8"), "text": text, "error": None}


def two_item_state():
    """확인 필요 항목 2개(1: 자문수당, 2: 다과비)와 질문 2개가 있는 검토 상태."""
    data = review_response("자문회의 참석수당", 300000, "판단 불가", unclear=["산정 근거"], keywords=["자문수당"])
    data["items"].append({**data["items"][0], "expense_item": "다과비", "amount": 50000, "status": "위반 가능성", "keywords": ["다과비"]})
    final = final_response()
    final["items"].append({**final["items"][0], "item_no": 2})
    fake = FakeAI({
        "guideline_review": data,
        "questions": {"questions": [{**QUESTIONS["questions"][0], "item_no": 1}, {**QUESTIONS["questions"][0], "item_no": 2}]},
        "final_opinion": final,
    })
    state = review.new_state(load_doc("test2_자문수당/기안문_자문회의.txt"), [], load_guidelines())
    review.run_review(state, fake)
    return state, fake


def test_attachments_go_to_final_opinion_and_only_their_case(data_dir):
    state, fake = two_item_state()
    files = {1: [att("내부회의록.txt", MINUTES)], 2: [att("다과_영수증.txt", "다과 영수증 50,000원")]}
    review.generate_final(state, {1: "내부 회의 후 재가 받음", 2: "회의 다과 제공"}, fake, files=files)

    prompt = fake.prompts("final_opinion")[0]
    assert MINUTES in prompt and "다과 영수증 50,000원" in prompt  # 첨부 내용이 AI에 전달됨
    assert '"attached_evidence": [\n   "내부회의록.txt"' in prompt
    assert "답변을 실제로 뒷받침하는지" in prompt

    res = review.save_review(state)
    c1, c2 = storage.get_cases(res["saved_ids"])
    assert c1["file_paths"] == ["documents/case_001/기안문_자문회의.txt", "documents/case_001/내부회의록.txt"]
    assert c2["file_paths"] == ["documents/case_001/기안문_자문회의.txt", "documents/case_001/다과_영수증.txt"]
    assert (data_dir / "documents/case_001/내부회의록.txt").read_text(encoding="utf-8") == MINUTES
    assert "답변 첨부 증빙: 내부회의록.txt" in c1["review_result"]


def test_attachment_without_answer_is_ignored(data_dir):
    state, fake = two_item_state()
    review.generate_final(state, {1: "재가 받음"}, fake, files={2: [att("무관.txt", "답변 없는 항목의 첨부")]})
    assert "답변 없는 항목의 첨부" not in fake.prompts("final_opinion")[0]
    assert state["final"]["files"] == {}


def test_changing_attachments_requires_new_final_opinion(data_dir):
    state, fake = two_item_state()
    answers = {1: "재가 받음"}
    files = {1: [att("회의록.txt", MINUTES)]}
    review.generate_final(state, answers, fake, files=files)
    assert not review.answers_changed(state, answers, files)
    assert review.answers_changed(state, answers, {1: files[1] + [att("추가.txt", "추가 증빙")]})  # 추가
    assert review.answers_changed(state, answers, {})  # 제거
    assert not review.answers_changed(state, answers, {**files, 2: [att("x.txt", "답변 없는 항목")]})  # 답변 없는 항목 첨부는 무관


def test_unreadable_attachment_is_still_saved(data_dir):
    state, fake = two_item_state()
    scan = {"name": "스캔본.pdf", "data": b"%PDF-scan", "text": None, "error": "텍스트 추출 불가"}
    review.generate_final(state, {1: "재가 받음"}, fake, files={1: [scan]})
    assert "스캔본.pdf" in fake.prompts("final_opinion")[0] and "내용 확인 불가" in fake.prompts("final_opinion")[0]
    res = review.save_review(state)
    assert "documents/case_001/스캔본.pdf" in storage.get_cases(res["saved_ids"])[0]["file_paths"]


def test_saved_attachment_is_read_in_later_review(data_dir):
    state, fake = two_item_state()
    review.generate_final(state, {1: "재가 받음"}, fake, files={1: [att("내부회의록.txt", MINUTES)]})
    review.save_review(state)

    later = FakeAI({
        "guideline_review": review_response("자문회의 참석수당", 800000, "판단 불가", keywords=["자문수당"]),
        "case_selection": {"selected_cases": [{"case_id": 1, "reason": "유사 자문수당"}]},
        "questions": QUESTIONS,
    })
    s2 = review.new_state(load_doc("test3_유사자문수당/지출결의서_자문수당.txt"), [], load_guidelines())
    review.run_review(s2, later)
    assert MINUTES in later.prompts("questions")[0]  # 과거 사례의 답변 첨부 증빙 원문을 다음 검토에서 참고


# ---------------------------------------------------------------- 화면
def test_review_page_attachment_flow(data_dir, monkeypatch):
    state, fake = two_item_state()
    monkeypatch.setattr(ai, "get_client", lambda **kw: fake)
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30)
    at.session_state["review"] = state
    # 파일 업로드 위젯은 AppTest가 지원하지 않아, 업로드 처리 후 상태(첨부 목록)를 직접 넣는다
    at.session_state["answer_files"] = {state["id"]: {1: [att("내부회의록.txt", MINUTES), att("잘못첨부.txt", "다른 파일")]}}
    at.run()
    assert not at.exception
    assert any("📎 내부회의록.txt" in c.value for c in at.caption)

    at.button(key=f"rm_af_{state['id']}_1_1").click().run()  # 잘못 올린 파일 빼기
    assert not any("잘못첨부.txt" in c.value for c in at.caption)

    at.text_area(key=f"answer_{state['id']}_1").input("내부 회의 후 재가 받음")
    next(b for b in at.button if "최종 의견" in b.label).click()
    at.run()
    assert not at.exception
    assert any("첨부 증빙 1개" in c.label for c in at.checkbox)
    next(b for b in at.button if "사례 DB 저장" in b.label).click()
    at.run()
    assert not at.exception
    case = storage.list_cases()[0]
    assert case["file_paths"][-1] == "documents/case_001/내부회의록.txt"
    assert not any("잘못첨부" in p for p in case["file_paths"])


def test_add_attachment_validates_dedupes_and_keeps_unreadable(data_dir):
    bucket = []
    assert review.add_attachment(bucket, "회의록.txt", MINUTES.encode("utf-8")) is None
    assert review.add_attachment(bucket, "회의록.txt", MINUTES.encode("utf-8")) is None  # 같은 파일은 한 번만
    assert "지원하지 않는" in review.add_attachment(bucket, "결재.hwp", b"x")
    assert review.add_attachment(bucket, "깨진.pdf", b"not a pdf") is None  # 읽지 못해도 보관
    assert [(f["name"], bool(f["text"]), bool(f["error"])) for f in bucket] == [("회의록.txt", True, False), ("깨진.pdf", False, True)]
