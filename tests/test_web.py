"""웹 모드: 로그인 없이 바로 화면이 열리고, AI는 사용자가 입력한 API 키로만 호출한다."""
import pytest
from streamlit.testing.v1 import AppTest

import ai
import config
import review
from conftest import QUESTIONS, ROOT, FakeAI, final_response, load_doc, load_guidelines, review_response


@pytest.fixture
def web(data_dir, monkeypatch):
    monkeypatch.setattr(config, "WEB_MODE", True)
    return data_dir


def new_app():
    return AppTest.from_file(str(ROOT / "app.py"), default_timeout=30)


def test_pages_open_without_login(web):
    at = new_app().run()
    assert not at.exception
    for page in ["지침 관리", "저장된 사례", "신규 문서 검토"]:
        at.radio(key="page").set_value(page).run()
        assert not at.exception, page
    assert at.text_input(key="api_key") is not None
    assert any("함께 봅니다" in i.value for i in at.info)


def test_ai_uses_users_own_key(web, monkeypatch):
    fake = FakeAI({"final_opinion": final_response()})
    keys = []
    monkeypatch.setattr(ai, "get_client", lambda api_key=None: keys.append(api_key) or fake)
    pre = FakeAI({"guideline_review": review_response("자문수당", 300000, "판단 불가"), "questions": QUESTIONS})
    state = review.new_state(load_doc("test2_자문수당/기안문_자문회의.txt"), [], load_guidelines())
    review.run_review(state, pre)

    at = new_app()
    at.session_state["review"] = state
    at.run()
    at.text_area(key=f"answer_{state['id']}_1").input("재가 받음")
    button = "💬 답변 반영 및 최종 의견 생성"
    next(b for b in at.button if b.label == button).click()
    at.run()
    assert any("API 키" in e.value for e in at.error) and keys == []  # 키 없으면 서버 키로 호출하지 않음

    at.text_input(key="api_key").input("sk-user-key")
    next(b for b in at.button if b.label == button).click()
    at.run()
    assert not at.exception
    assert keys == ["sk-user-key"]
    assert any("4. 최종 검토 의견" in s.value for s in at.subheader)
