"""항목별 통합 화면: 항목당 여러 질문, 질문별 답변·증빙, 같은 파일 한 번만 저장, 항목별 과거 사례 (모의 AI 사용)."""
from streamlit.testing.v1 import AppTest

import ai
import review
import storage
from conftest import ROOT, FakeAI, final_response, load_doc, load_guidelines, review_response

MINUTES = "2026. 5. 15. 사업단 내부 회의록: 자문 단가 2시간 30만 원으로 결정"


def att(name, text):
    return {"name": name, "data": text.encode("utf-8"), "text": text, "error": None}


def q(item_no, text, cases=None):
    return {"item_no": item_no, "question": text, "reason": "확인 필요", "reason_types": [],
            "referenced_case_ids": cases or [], "past_case_comparison": ""}


def multi_state(selection=None):
    """항목 1(자문수당)에 질문 2개, 항목 2(다과비)에 질문 1개."""
    data = review_response("자문회의 참석수당", 300000, "판단 불가", unclear=["산정 근거", "재가 여부"], keywords=["자문수당"])
    data["items"].append({**data["items"][0], "expense_item": "다과비", "amount": 50000, "status": "위반 가능성", "keywords": ["다과비"]})
    final = final_response()
    final["items"].append({**final["items"][0], "item_no": 2})
    responses = {
        "guideline_review": data,
        "questions": {"questions": [q(1, "단가 30만 원의 산정 근거는?"), q(1, "사업단장 재가를 받았나요?"), q(2, "다과 제공 대상은?")]},
        "final_opinion": final,
    }
    if selection:
        responses["case_selection"] = selection
    fake = FakeAI(responses)
    state = review.new_state(load_doc("test2_자문수당/기안문_자문회의.txt"), [], load_guidelines())
    review.run_review(state, fake)
    return state, fake


# ---------------------------------------------------------------- 질문 정리
def test_normalize_questions_allows_several_per_item_with_numbers():
    rv = {"items": [
        {"item_no": 1, "expense_item": "자문수당", "status": "판단 불가", "unclear_points": ["산정 근거"], "reasoning": ""},
        {"item_no": 2, "expense_item": "상품권", "status": "위반 가능성", "unclear_points": ["구매 목적", "수령자 명단"], "reasoning": ""},
    ]}
    qs = ai.normalize_questions({"questions": [
        q(1, "산정 근거는?"), q(1, "재가는?"), q(1, "재가는? "),  # 같은 질문 중복은 버림
        *[q(1, f"추가 질문 {n}") for n in range(5)],  # 항목당 최대 개수 초과분은 버림
    ]}, rv)
    item1 = [x for x in qs if x["item_no"] == 1]
    assert len(item1) == ai.MAX_QUESTIONS_PER_ITEM
    assert [x["question"] for x in item1[:2]] == ["산정 근거는?", "재가는?"]
    assert [review.question_label(x) for x in qs] == ["1-1", "1-2", "1-3", "1-4", "2-1", "2-2"]
    assert [x["qid"] for x in qs] == [1, 2, 3, 4, 5, 6]
    # AI가 빠뜨린 항목 2는 확인 필요 사항마다 질문 하나씩
    item2 = [x for x in qs if x["item_no"] == 2]
    assert all(x["auto_generated"] for x in item2)
    assert "구매 목적" in item2[0]["question"] and "수령자 명단" in item2[1]["question"]


def test_question_prompt_asks_for_multiple_questions(data_dir):
    _, fake = multi_state()
    prompt = fake.prompts("questions")[0]
    assert f"질문을 1~{ai.MAX_QUESTIONS_PER_ITEM}개" in prompt and "한 가지 사항만" in prompt
    # 증빙 제출 요구가 아니라 '지침 요건 → 확인 여부'를 묻게 하고, 집행 전 문서에 집행 후 서류를 묻지 않게 한다
    assert "증빙자료 제출을 요구하지 마세요" in prompt and "인데, (문서에서 확인되지 않은 부분)이" in prompt
    assert "집행 후에야 생기는 사항은 묻지 마세요" in prompt
    assert "증빙을 첨부할 수 있는" not in prompt


# ---------------------------------------------------------------- 답변·최종 의견·저장
def test_partial_answers_and_one_case_per_item(data_dir):
    state, fake = multi_state()
    qid = {review.question_label(x): x["qid"] for x in state["questions"]}
    answers = {qid["1-1"]: "내부 회의로 단가 결정", qid["2-1"]: "회의 참석자 다과"}  # 1-2는 답하지 않음
    review.generate_final(state, answers, fake, files={qid["1-1"]: [att("회의록.txt", MINUTES)]})

    prompt = fake.prompts("final_opinion")[0]
    assert '"question_no": "1-2"' in prompt and "(답변 없음)" in prompt  # 답하지 않은 질문도 '확인 안 됨'으로 전달
    assert "모든 질문의 답변을 종합" in prompt

    recs = review.build_case_records(state)
    assert [r["item_no"] for r in recs] == [1, 2]  # 항목 1개 = 사례 1건
    r1 = recs[0]
    assert r1["ai_question"] == "1) 단가 30만 원의 산정 근거는?\n2) 사업단장 재가를 받았나요?"
    assert r1["user_answer"] == "1) 내부 회의로 단가 결정\n2) (답변 없음)"
    assert (r1["question_count"], r1["answered_count"]) == (2, 1)
    assert recs[1]["ai_question"] == "다과 제공 대상은?"  # 질문 1개면 번호 없이 그대로

    res = review.save_review(state)
    c1, c2 = storage.get_cases(res["saved_ids"])
    assert c1["file_paths"][-1] == "documents/case_001/회의록.txt" and "회의록.txt" not in str(c2["file_paths"])


def test_item_without_any_answer_is_not_saved(data_dir):
    state, fake = multi_state()
    review.generate_final(state, {state["questions"][1]["qid"]: "재가 받음"}, fake)  # 항목 1의 두 번째 질문만 답변
    assert [r["item_no"] for r in review.build_case_records(state)] == [1]


def test_answers_changed_tracks_each_question(data_dir):
    state, fake = multi_state()
    answers = {1: "답1", 2: "답2"}
    review.generate_final(state, answers, fake)
    assert not review.answers_changed(state, answers)
    assert review.answers_changed(state, {1: "답1", 2: "답2", 3: "새 답변"})
    assert review.answers_changed(state, {1: "답1"})


# ---------------------------------------------------------------- 같은 파일은 한 번만
def test_same_file_on_several_questions_is_stored_once(data_dir):
    state, fake = multi_state()
    main = state["documents"][0]
    same_as_main = {"name": "기안문_사본.txt", "data": main["data"], "text": main["text"], "error": None}
    files = {
        1: [att("회의록.txt", MINUTES)],
        2: [att("회의록(재가).txt", MINUTES), same_as_main],  # 같은 항목의 다른 질문에 같은 회의록 + 검토 문서와 같은 파일
        3: [att("회의록_다과.txt", MINUTES), att("영수증.txt", "다과 영수증")],  # 다른 항목에도 같은 회의록
    }
    review.generate_final(state, {1: "회의", 2: "재가", 3: "다과"}, fake, files=files)

    prompt = fake.prompts("final_opinion")[0]
    assert prompt.count(MINUTES) == 1  # AI에도 한 번만 보냄
    assert "질문 1-1, 1-2, 2-1" in prompt

    res = review.save_review(state)
    folder = data_dir / "documents/case_001"
    assert sorted(p.name for p in folder.iterdir()) == ["기안문_자문회의.txt", "영수증.txt", "회의록.txt"]
    assert res["file_paths"] == ["documents/case_001/기안문_자문회의.txt", "documents/case_001/회의록.txt", "documents/case_001/영수증.txt"]
    c1, c2 = storage.get_cases(res["saved_ids"])
    assert c1["file_paths"] == ["documents/case_001/기안문_자문회의.txt", "documents/case_001/회의록.txt"]
    assert c2["file_paths"] == ["documents/case_001/기안문_자문회의.txt", "documents/case_001/회의록.txt", "documents/case_001/영수증.txt"]
    assert "답변 첨부 증빙: 회의록.txt, 기안문_사본.txt" in c1["review_result"]  # 이름은 기록하되 파일은 하나


def test_add_attachment_dedupes_by_content_not_name(data_dir):
    bucket = []
    review.add_attachment(bucket, "회의록.txt", MINUTES.encode("utf-8"))
    review.add_attachment(bucket, "회의록_복사본.txt", MINUTES.encode("utf-8"))  # 이름만 다르고 내용 같음
    review.add_attachment(bucket, "회의록.txt", b"other content")  # 이름은 같고 내용 다름
    assert [f["data"] for f in bucket] == [MINUTES.encode("utf-8"), b"other content"]


# ---------------------------------------------------------------- 항목별 과거 사례
def _past(cid, related):
    return {"id": cid, "related_item_nos": related}


def test_past_cases_for_item():
    state = {
        "questions": [{"qid": 1, "item_no": 2, "seq": 1, "referenced_case_ids": [7]}],
        "past_cases": [_past(5, [1]), _past(6, [2]), _past(7, [1]), _past(8, [])],
    }
    assert [c["id"] for c in review.past_cases_for_item(state, 1)] == [5, 7, 8]  # 8: 관련 항목 표시 없음 → 모든 항목
    assert [c["id"] for c in review.past_cases_for_item(state, 2)] == [6, 7, 8]  # 7: 항목 2 질문에서 참고


# ---------------------------------------------------------------- 화면
def _seed_case(data_dir):
    s, f = multi_state()
    review.generate_final(s, {1: "작년 회의에서 결정"}, f)
    return review.save_review(s)["saved_ids"][0]


def test_review_page_shows_items_with_cases_and_questions(data_dir, monkeypatch):
    cid = _seed_case(data_dir)
    state, fake = multi_state(selection={"selected_cases": [{"case_id": cid, "related_item_nos": [1], "reason": "같은 자문수당"}]})
    monkeypatch.setattr(ai, "get_client", lambda **kw: fake)
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30)
    at.session_state["review"] = state
    same = att("회의록.txt", MINUTES)
    at.session_state["answer_files"] = {state["id"]: {1: [same], 3: [att("회의록_사본.txt", MINUTES)]}}
    at.run()
    assert not at.exception

    subheaders = [s.value for s in at.subheader]
    assert not any("AI 추가 질문" in s for s in subheaders)  # 별도 질문 메뉴 없이 항목 카드 안에 통합
    md = [m.value for m in at.markdown]
    # 항목 1 카드 안의 순서: 제목 → 판단 근거 → 관련 과거 사례 → AI 질문 1-1, 1-2 → 항목 2
    order = [next(i for i, v in enumerate(md) if key in v) for key in
             ("**항목 1. 자문회의 참석수당**", "**판단 근거:**", "**🗂️ 관련 과거 사례**", "**질문 1-1.**", "**질문 1-2.**", "**항목 2. 다과비**", "**질문 2-1.**")]
    assert order == sorted(order)
    assert not any("**추가 확인 필요**" in v for v in md)  # 확인 필요 사항은 AI 질문으로 대체
    assert [e.label for e in at.expander if e.label.startswith(f"사례 #{cid}")] == [f"사례 #{cid} · 2026년 테스트 사업 · 자문회의 참석수당 · 300,000원 — 같은 자문수당"]
    assert any("이 항목과 관련된 과거 사례가 없습니다" in c.value for c in at.caption)  # 항목 2
    assert any("질문 2-1에도 있어 한 번만 저장됨" in c.value for c in at.caption)

    at.text_area(key=f"answer_{state['id']}_1").input("내부 회의로 결정")
    at.text_area(key=f"answer_{state['id']}_3").input("참석자 다과")
    next(b for b in at.button if "최종 의견" in b.label).click()
    at.run()
    assert not at.exception
    assert any("질문 2개 중 1개 답변" in c.label for c in at.checkbox)
    next(b for b in at.button if "사례 DB 저장" in b.label).click()
    at.run()
    assert not at.exception
    new = storage.list_cases()[-2:]
    assert new[0]["file_paths"][-1] == new[1]["file_paths"][-1]  # 같은 회의록 파일 하나를 두 사례가 함께 참조
