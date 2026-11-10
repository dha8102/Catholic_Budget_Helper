"""요구사항의 필수 테스트 시나리오 1~6을 모의 AI로 검증한다."""
import pytest

import ai
import config
import review
import storage
from conftest import QUESTIONS, FakeAI, final_response, load_doc, load_guidelines, review_response

ADVISORY_QUOTE = "이 지침에서 집행기준을 정하지 아니한 비목을 집행하려는 경우 사업단장의 사전 승인을 받아야 한다."
ANSWER_T2 = "내부 회의 후 사업단장 재가를 받았습니다."
PAST_DOC_MARKER = "2026. 5. 15. 사업단 내부 회의에서"  # 결과보고서_자문회의.docx 안에만 있는 문장


def save_test2_case() -> dict:
    """테스트 2 흐름을 끝까지 실행해 사례를 저장하고 state를 돌려준다."""
    fake = FakeAI({
        "guideline_review": review_response("자문회의 참석수당", "300,000원", "판단 불가", ADVISORY_QUOTE, ["산정 근거", "승인 여부"]),
        "questions": QUESTIONS,
        "final_opinion": final_response(),
    })
    state = review.new_state(load_doc("test2_자문수당/기안문_자문회의.txt"),
                             [load_doc("test2_자문수당/결과보고서_자문회의.docx")], load_guidelines())
    review.run_review(state, fake)
    review.generate_final(state, {1: ANSWER_T2}, fake)
    review.save_review(state)
    return state


def test_1_normal_expense_no_question_no_save(data_dir):
    save_test2_case()  # DB에 사례가 있어도 적합 문서는 사례 조회·질문을 하지 않아야 함
    fake = FakeAI({"guideline_review": review_response("회의비(식비)", 176000, "적합", "회의 식비는 1인당 30,000원 이내로 한다.")})
    state = review.new_state(load_doc("test1_정상집행/지출결의서_회의비.txt"), [load_doc("test1_정상집행/회의록.txt")], load_guidelines())
    review.run_review(state, fake)

    assert fake.tasks() == ["guideline_review"]  # 사례 선택·질문 생성 호출 없음
    assert state["initial"]["items"][0]["status"] == "적합"
    assert state["initial"]["items"][0]["related_guidelines"][0]["verified"] is True
    assert state["questions"] == []
    assert review.build_case_records(state) == []
    with pytest.raises(ai.AIError):
        review.generate_final(state, {}, fake)
    with pytest.raises(ValueError):
        review.save_review(state)
    assert len(storage.list_cases()) == 1  # 테스트 2 사례만 존재


def test_2_unregulated_advisory_fee_saved_to_db_and_files(data_dir):
    state = save_test2_case()
    item = state["initial"]["items"][0]
    assert item["status"] == "판단 불가" and item["amount"] == 300000
    assert state["questions"][0]["item_no"] == 1
    assert state["selection"]["note"] == "저장된 과거 사례가 없습니다."

    cases = storage.list_cases()
    assert len(cases) == 1
    c = cases[0]
    assert c["expense_item"] == "자문회의 참석수당" and c["amount"] == 300000
    assert c["user_answer"] == ANSWER_T2
    assert "[최초 지침 검토] 판단 불가" in c["review_result"] and "[답변 반영 후 최종 의견]" in c["review_result"]
    assert c["file_paths"] == ["documents/case_001/기안문_자문회의.txt", "documents/case_001/결과보고서_자문회의.docx"]
    for rel in c["file_paths"]:
        assert (data_dir / rel).is_file()

    # 저장 버튼을 다시 눌러도 중복 생성되지 않음
    again = review.save_review(state)
    assert again["already_saved"]
    state.pop("saved")
    dup = review.save_review(state)
    assert dup["saved_ids"] == [] and dup["duplicate_ids"] == [1]
    assert len(storage.list_cases()) == 1
    assert len(list((data_dir / "documents").iterdir())) == 1


def test_3_reuses_past_case_and_reads_original_file(data_dir):
    save_test2_case()
    fake = FakeAI({
        "guideline_review": review_response("자문회의 참석수당", 800000, "판단 불가", ADVISORY_QUOTE),
        "case_selection": {"selected_cases": [{"case_id": 1, "related_item_nos": [1], "reason": "유사 자문수당"},
                                              {"case_id": 99, "reason": "존재하지 않는 사례"}], "note": ""},
        "questions": {"questions": [{**QUESTIONS["questions"][0], "referenced_case_ids": [1]}]},
    })
    state = review.new_state(load_doc("test3_유사자문수당/지출결의서_자문수당.txt"), [], load_guidelines())
    review.run_review(state, fake)

    assert fake.tasks() == ["guideline_review", "case_selection", "questions"]
    sel_prompt = fake.prompts("case_selection")[0]
    assert ANSWER_T2 in sel_prompt  # 2단계: DB 사례 전체(질문·답변)를 AI에 제공
    assert PAST_DOC_MARKER not in sel_prompt  # 2단계에서는 파일을 열지 않음

    assert [c["id"] for c in state["past_cases"]] == [1]
    docx_doc = next(d for d in state["past_cases"][0]["documents"] if d["name"].endswith(".docx"))
    assert PAST_DOC_MARKER in docx_doc["text"]  # 3단계: 저장 경로로 실제 파일을 열어 텍스트 추출

    q_prompt = fake.prompts("questions")[0]
    assert PAST_DOC_MARKER in q_prompt  # 4단계: 과거 원본 문서 내용 + 담당자 답변을 함께 전달
    assert ANSWER_T2 in q_prompt
    assert state["questions"][0]["referenced_case_ids"] == [1]
    assert any("99" in w for w in state["warnings"])  # 없는 사례 번호는 무시하고 알림


def test_3b_unselected_case_files_are_not_opened(data_dir, monkeypatch):
    storage.save_cases([{"business_name": "A", "expense_item": "자문수당", "amount": 1, "review_result": "r", "ai_question": "q", "user_answer": "a"}],
                       [("a.txt", "사례A 문서".encode())])
    storage.save_cases([{"business_name": "B", "expense_item": "회의비", "amount": 2, "review_result": "r", "ai_question": "q", "user_answer": "b"}],
                       [("b.txt", "사례B 문서".encode())])
    fake = FakeAI({
        "guideline_review": review_response("자문수당", 1, "판단 불가"),
        "case_selection": {"selected_cases": [{"case_id": 1, "reason": "관련"}]},
        "questions": QUESTIONS,
    })
    state = review.new_state(load_doc("test3_유사자문수당/지출결의서_자문수당.txt"), [], load_guidelines())
    opened = []
    original = storage.extract_text_from_file
    monkeypatch.setattr(storage, "extract_text_from_file", lambda p: opened.append(p.name) or original(p))
    review.run_review(state, fake)
    assert opened == ["a.txt"]
    assert "사례A 문서" in fake.prompts("questions")[0] and "사례B 문서" not in fake.prompts("questions")[0]


def test_4_data_survives_restart(data_dir):
    save_test2_case()
    # 재실행: 초기화 함수를 다시 불러도 기존 DB·파일이 유지되어야 함
    storage.init_storage()
    cases = storage.list_cases()
    assert len(cases) == 1
    docs = storage.read_case_documents(cases[0])
    assert all(d["text"] and d["warning"] is None for d in docs)
    assert any(PAST_DOC_MARKER in d["text"] for d in docs)


def test_5_missing_attachment_warns_and_continues(data_dir):
    save_test2_case()
    for f in (data_dir / "documents" / "case_001").iterdir():
        f.unlink()
    fake = FakeAI({
        "guideline_review": review_response("자문회의 참석수당", 800000, "판단 불가"),
        "case_selection": {"selected_cases": [{"case_id": 1, "reason": "유사"}]},
        "questions": QUESTIONS,
    })
    state = review.new_state(load_doc("test3_유사자문수당/지출결의서_자문수당.txt"), [], load_guidelines())
    review.run_review(state, fake)
    assert state["done"]
    assert any("과거 사례의 첨부파일을 찾을 수 없습니다" in w for w in state["warnings"])
    q_prompt = fake.prompts("questions")[0]
    assert ANSWER_T2 in q_prompt  # DB에 남은 질문·답변으로 계속 검토
    assert "DB 기록만 참고" in q_prompt


def test_6_wrong_past_case_does_not_make_it_compliant(data_dir):
    storage.save_cases([{
        "business_name": "2026년 AI 해커톤", "expense_item": "참가자 기념품(문화상품권)", "amount": 300000,
        "review_result": "[최초 지침 검토] 위반 가능성\n상품권 구매 제한", "ai_question": "상품권 구매가 지침상 허용되나요?",
        "user_answer": "작년에도 상품권으로 지급했고 사업단장 재가를 받았습니다.",
    }], [("1차_지출결의서_상품권.txt", load_doc("test6_잘못된과거사례/1차_지출결의서_상품권.txt")["data"])])
    fake = FakeAI({
        "guideline_review": review_response("우수 참가자 기념품(모바일 상품권)", 400000, "위반 가능성",
                                            "상품권, 기프티콘 등 현금성 유가증권의 구매"),
        "case_selection": {"selected_cases": [{"case_id": 1, "reason": "과거 상품권 지급 사례(지침 위반 가능성 있음)"}]},
        "questions": QUESTIONS,
        "final_opinion": final_response(status="적합"),  # AI가 과거 관행만 보고 적합으로 바꾼 상황을 가정
    })
    state = review.new_state(load_doc("test6_잘못된과거사례/2차_지출결의서_상품권.txt"), [], load_guidelines())
    review.run_review(state, fake)
    review.generate_final(state, {1: "작년에도 같은 방식으로 지급했습니다."}, fake)

    # 프롬프트에 '지침 우선, 과거 관행은 적합 근거 아님' 원칙이 들어가는지
    for task in ("questions", "final_opinion"):
        _, system, user = next(c for c in fake.calls if c[0] == task)
        assert "과거에 같은 방식으로 집행했다는 사실만으로 적합하다고 판단하지 마세요" in system
    assert "내부 승인으로 정당화되지 않습니다" in fake.prompts("final_opinion")[0]
    # 위반 가능성 → 적합으로 바뀌면 화면에 경고 표시
    final_item = state["final"]["result"]["items"][0]
    assert final_item["status_note"] and "공식 지침" in final_item["status_note"]
    rec = review.build_case_records(state)[0]
    assert "[최초 지침 검토] 위반 가능성" in rec["review_result"]


# ---------------------------------------------------------------- 오류 처리
def test_input_over_limit_is_not_silently_truncated(data_dir, monkeypatch):
    monkeypatch.setattr(config, "MAX_INPUT_CHARS", 1000)
    fake = FakeAI({})
    state = review.new_state(load_doc("test2_자문수당/기안문_자문회의.txt"), [], load_guidelines())
    with pytest.raises(ai.AIError, match="초과"):
        review.run_review(state, fake)
    assert fake.calls == []


def test_too_many_cases_warns_and_uses_recent(data_dir, monkeypatch):
    for i in range(30):
        storage.save_cases([{"business_name": f"사업{i}", "expense_item": "자문수당", "amount": i, "review_result": "가" * 2000,
                             "ai_question": "q", "user_answer": f"답변{i}"}], [])
    monkeypatch.setattr(config, "MAX_INPUT_CHARS", 20_000)
    fake = FakeAI({
        "guideline_review": review_response("자문수당", 1, "판단 불가"),
        "case_selection": {"selected_cases": []},
        "questions": QUESTIONS,
    })
    state = review.new_state(load_doc("test2_자문수당/기안문_자문회의.txt"), [], load_guidelines())
    review.run_review(state, fake)
    prompt = fake.prompts("case_selection")[0]
    assert len(prompt) <= 20_000
    assert "답변29" in prompt and "답변0\"" not in prompt
    assert any("최신" in w and "전체 30건" in w for w in state["warnings"])


def test_bad_ai_response_can_be_retried_from_failed_step(data_dir):
    attempts = {"n": 0}

    def flaky(_):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise ai.AIFormatError("AI 응답을 JSON으로 해석하지 못했습니다.")
        return QUESTIONS

    fake = FakeAI({"guideline_review": review_response("자문수당", 1, "판단 불가"), "questions": flaky})
    state = review.new_state(load_doc("test2_자문수당/기안문_자문회의.txt"), [], load_guidelines())
    with pytest.raises(ai.AIFormatError):
        review.run_review(state, fake)
    assert "initial" in state and not state["done"]
    review.run_review(state, fake)  # 다시 시도
    assert state["done"] and len(state["questions"]) == 1
    assert fake.tasks().count("guideline_review") == 1  # 끝난 단계는 다시 호출하지 않음


def test_answer_changed_after_final_blocks_save(data_dir):
    fake = FakeAI({"guideline_review": review_response("자문수당", 1, "판단 불가"), "questions": QUESTIONS, "final_opinion": final_response()})
    state = review.new_state(load_doc("test2_자문수당/기안문_자문회의.txt"), [], load_guidelines())
    review.run_review(state, fake)
    review.generate_final(state, {1: "답변"}, fake)
    assert not review.answers_changed(state, {1: "답변 "})
    assert review.answers_changed(state, {1: "다른 답변"})
