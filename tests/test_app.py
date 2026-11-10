"""Streamlit 화면을 실제로 실행해 보는 테스트(모의 AI 사용).
파일 업로드 위젯은 AppTest가 지원하지 않아, 검토 실행 직후 상태를 session_state에 넣고 그다음 흐름을 확인한다."""
from streamlit.testing.v1 import AppTest

import ai
import review
import storage
from conftest import QUESTIONS, ROOT, FakeAI, final_response, load_doc, load_guidelines, review_response


def new_app():
    return AppTest.from_file(str(ROOT / "app.py"), default_timeout=30)


def test_all_pages_render(data_dir):
    storage.save_guideline("지침.txt", "제1조 회의비는 1인당 30,000원 이내".encode())
    at = new_app().run()
    assert not at.exception
    for page in ["지침 관리", "저장된 사례", "신규 문서 검토"]:
        at.radio(key="page").set_value(page).run()
        assert not at.exception, page
    assert any("외부 OpenAI API" in i.value for i in at.info)


def test_question_answer_final_save_flow(data_dir, monkeypatch):
    fake = FakeAI({
        "guideline_review": review_response("자문회의 참석수당", 300000, "판단 불가", unclear=["산정 근거"]),
        "questions": QUESTIONS,
        "final_opinion": final_response(),
    })
    monkeypatch.setattr(ai, "get_client", lambda: fake)
    state = review.new_state(load_doc("test2_자문수당/기안문_자문회의.txt"),
                             [load_doc("test2_자문수당/결과보고서_자문회의.docx")], load_guidelines())
    review.run_review(state, fake)

    at = new_app()
    at.session_state["review"] = state
    at.run()
    assert not at.exception
    assert any("판단 불가" in m.value for m in at.markdown)

    at.text_area(key=f"answer_{state['id']}_1").input("내부 회의 후 사업단장 재가를 받았습니다.")
    next(b for b in at.button if "최종 의견" in b.label).click()
    at.run()
    assert not at.exception
    assert any("4. 최종 검토 의견" in s.value for s in at.subheader)

    save = next(b for b in at.button if "사례 DB 저장" in b.label)
    assert not save.disabled
    save.click()
    at.run()
    assert not at.exception
    assert any("저장 완료" in s.value for s in at.success)
    assert len(storage.list_cases()) == 1

    # 저장 버튼이 비활성화되어 연속 클릭으로 중복 저장되지 않음
    assert next(b for b in at.button if "사례 DB 저장" in b.label).disabled

    at.radio(key="page").set_value("저장된 사례").run()
    assert not at.exception
    assert any("#1" in e.label for e in at.expander)


def test_saved_cases_page_with_missing_file(data_dir):
    storage.save_cases([{"business_name": "사업", "expense_item": "자문수당", "amount": 1, "review_result": "r",
                         "ai_question": "q", "user_answer": "a"}], [("a.txt", b"x")])
    (data_dir / "documents" / "case_001" / "a.txt").unlink()
    at = new_app()
    at.session_state["page"] = "저장된 사례"
    at.run()
    assert not at.exception
    assert any("과거 사례의 첨부파일을 찾을 수 없습니다" in w.value for w in at.warning)


def test_ai_error_is_shown_not_crash(data_dir, monkeypatch):
    def broken():
        raise ai.AIError("OPENAI_API_KEY가 설정되지 않았습니다.")

    monkeypatch.setattr(ai, "get_client", broken)
    state = review.new_state(load_doc("test2_자문수당/기안문_자문회의.txt"), [], load_guidelines())
    at = new_app()
    at.session_state["review"] = state
    at.session_state["review_error"] = "OPENAI_API_KEY가 설정되지 않았습니다."
    at.run()
    assert not at.exception
    retry = next(b for b in at.button if "다시 시도" in b.label)
    retry.click()
    at.run()
    assert not at.exception
    assert any("OPENAI_API_KEY" in e.value for e in at.error)
