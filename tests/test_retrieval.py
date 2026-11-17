"""과거 사례 검색(키워드 + 임베딩 → 상위 K건)과 항목 번호 매기기 테스트 (모의 AI 사용)."""
import re

import ai
import config
import retrieval
import review
import storage
from conftest import QUESTIONS, FakeAI, final_response, load_doc, load_guidelines, review_response


def add_case(name, item, keywords=None, answer="답변"):
    rec = {"business_name": name, "expense_item": item, "amount": 100000, "review_result": f"{item} 검토 결과",
           "ai_question": f"{item}의 근거는?", "user_answer": answer}
    if keywords:
        rec["keywords"] = keywords
    return storage.save_cases([rec], [])["saved_ids"][0]


def advisory_fake(**responses):
    return FakeAI({
        "guideline_review": review_response("자문회의 참석수당", 800000, "판단 불가", keywords=["자문수당", "단가산정근거"]),
        "case_selection": {"selected_cases": []},
        "questions": QUESTIONS,
        **responses,
    })


def run(fake, top_k=None):
    state = review.new_state(load_doc("test3_유사자문수당/지출결의서_자문수당.txt"), [], load_guidelines())
    review.run_review(state, fake, top_k=top_k)
    return state


def prompt_case_ids(prompt: str) -> list[int]:
    return [int(x) for x in re.findall(r'"case_id": (\d+)', prompt.split("과거 사례 목록")[-1])]


# ---------------------------------------------------------------- 키워드 유사도 / 순위 합치기
def test_keyword_similarity():
    assert retrieval.keyword_similarity("자문수당", "자문 수당") == 1.0  # 띄어쓰기 무시
    assert retrieval.keyword_similarity("참석수당", "자문회의참석수당") == 0.7  # 부분 일치
    assert 0 < retrieval.keyword_similarity("자문수당", "자문회의참석수당") < 0.7  # 글자 묶음 일부 일치
    assert retrieval.keyword_similarity("자문수당", "상품권") == 0.0


def test_fuse_puts_cases_found_by_both_first():
    kw = [{"case_id": 1}, {"case_id": 2}]
    emb = [{"case_id": 2}, {"case_id": 3}]
    fused = retrieval.fuse(kw, emb, top_k=2)
    assert [f["case_id"] for f in fused] == [2, 1]  # 두 검색 모두에서 나온 사례가 1위, 상위 2건만
    assert fused[0]["keyword_rank"] == 2 and fused[0]["embedding_rank"] == 1


# ---------------------------------------------------------------- 하이브리드 검색
def test_only_top_k_candidates_go_to_ai(data_dir):
    advisory = [add_case(f"사업{i}", "자문회의 참석수당", ["자문수당", "자문료"]) for i in range(6)]
    for i in range(6):
        add_case(f"행사{i}", "회의 식비", ["회의비", "식비"])
    fake = advisory_fake()
    state = run(fake, top_k=4)

    ids = prompt_case_ids(fake.prompts("case_selection")[0])
    assert len(ids) == 4 and set(ids) <= set(advisory)  # 12건 중 관련 있는 자문수당 사례 4건만 AI에 전달
    info = state["retrieval"]
    assert info["total"] == 12 and info["top_k"] == 4 and len(info["candidates"]) == 4
    assert "자문수당" in info["keywords"]
    assert all(c["keyword_score"] and c["embedding_score"] is not None for c in info["candidates"])
    assert len(fake.embedded) == 12 + 1  # 사례 12건 색인 + 검색어(확인 필요 항목) 1건


def test_index_is_reused_and_refreshed_when_case_edited(data_dir):
    ids = [add_case(f"사업{i}", "자문회의 참석수당", ["자문수당"]) for i in range(3)]
    run(advisory_fake())
    fake = advisory_fake()
    run(fake)
    assert len(fake.embedded) == 1  # 저장된 임베딩을 다시 쓰고 검색어만 임베딩

    storage.update_case_review(ids[0], "담당자가 고친 검토 결과")
    fake = advisory_fake()
    run(fake)
    assert len(fake.embedded) == 2 and "담당자가 고친 검토 결과" in fake.embedded[0]  # 고친 사례만 다시 임베딩


def test_embedding_finds_case_that_keywords_miss(data_dir):
    cid = add_case("교육사업", "외부 전문가 자문 회의 수당 지급", ["워크숍"])
    retrieval_case = storage.get_cases([cid])[0]
    # 키워드로는 못 찾지만(일치 없음) 임베딩 검색 후보로는 들어온다
    assert retrieval.keyword_search(["상품권"], [retrieval_case], storage.get_case_index(), 10) == []
    fake = advisory_fake(guideline_review=review_response("상품권 구매", 300000, "위반 가능성", keywords=["상품권"]))
    state = run(fake)
    c = state["retrieval"]["candidates"][0]
    assert c["case_id"] == cid and c["keyword_score"] is None and c["embedding_score"] is not None


def test_embedding_failure_falls_back_to_keyword_search(data_dir):
    cid = add_case("사업", "자문회의 참석수당", ["자문수당"])
    add_case("행사", "회의 식비", ["회의비"])
    fake = advisory_fake()
    fake.embed_error = ai.AIError("OpenAI 서버에 연결할 수 없습니다.")
    state = run(fake)
    assert state["done"]
    assert any("임베딩 검색을 사용하지 못해 키워드 검색 결과만" in w for w in state["warnings"])
    assert [c["case_id"] for c in state["retrieval"]["candidates"]] == [cid]


def test_old_cases_without_keywords_get_keywords_once(data_dir):
    cid = add_case("AI 해커톤", "참가자 기념품(문화상품권)")  # 키워드 없이 저장된 옛 사례
    fake = advisory_fake(
        guideline_review=review_response("우수 참가자 기념품(모바일 상품권)", 400000, "위반 가능성", keywords=["상품권", "기프티콘"]),
        case_keywords={"cases": [{"case_id": cid, "keywords": ["상품권", "기프티콘", "현금성경품"]}]},
    )
    state = run(fake)
    assert fake.tasks().count("case_keywords") == 1
    assert storage.get_case_index()[cid]["keywords"] == ["상품권", "기프티콘", "현금성경품"]
    assert "상품권" in state["retrieval"]["candidates"][0]["matched"]
    fake2 = advisory_fake(guideline_review=fake.responses["guideline_review"])
    run(fake2)
    assert "case_keywords" not in fake2.tasks()  # 한 번 만든 키워드는 저장되어 다시 만들지 않음


def test_top_k_is_clamped(data_dir):
    add_case("사업", "자문회의 참석수당", ["자문수당"])
    assert run(advisory_fake(), top_k=999)["retrieval"]["top_k"] == config.RETRIEVAL_TOP_K_MAX
    assert run(advisory_fake(), top_k=0)["retrieval"]["top_k"] == config.RETRIEVAL_TOP_K


# ---------------------------------------------------------------- 저장 시 색인
def _answered_state(fake):
    state = run(fake)
    review.generate_final(state, {1: "사업단장 재가를 받았습니다."}, fake)
    return state


def test_save_builds_index_immediately(data_dir):
    fake = advisory_fake(final_opinion=final_response())
    state = _answered_state(fake)
    res = review.save_review(state, client=fake)
    cid = res["saved_ids"][0]
    entry = storage.get_case_index()[cid]
    assert entry["keywords"] == ["자문수당", "단가산정근거"] and entry["embedding"] and res["index_warning"] is None


def test_save_succeeds_even_if_index_fails(data_dir):
    fake = advisory_fake(final_opinion=final_response())
    state = _answered_state(fake)
    fake.embed_error = ai.AIError("OpenAI 서버에 연결할 수 없습니다.")
    res = review.save_review(state, client=fake)
    assert res["saved_ids"] and "다음 검토 때 자동으로" in res["index_warning"]
    assert storage.get_case_index()[res["saved_ids"][0]]["embedding"] is None  # 다음 검토 때 만든다


def test_delete_case_removes_index(data_dir):
    cid = add_case("사업", "자문수당", ["자문수당"])
    storage.delete_case(cid)
    assert cid not in storage.get_case_index()


# ---------------------------------------------------------------- 항목 번호: 적합 항목은 번호에서 제외
def test_flagged_items_numbered_without_gaps():
    def item(name, status, unclear=None):
        return {"expense_item": name, "amount": 1, "status": status, "unclear_points": unclear or []}

    data = {"items": [item("식비", "적합"), item("자문수당", "판단 불가", ["근거"]), item("강사료", "적합"),
                      item("상품권", "위반 가능성", ["금지 품목"])]}
    items = ai.normalize_review(data, [{"name": "g", "text": "지침"}])["items"]
    assert [(i["item_no"], i["expense_item"], i["status"]) for i in items] == [
        (1, "자문수당", "판단 불가"), (2, "상품권", "위반 가능성"), (3, "식비", "적합"), (4, "강사료", "적합")]


# ---------------------------------------------------------------- 화면: 검색 결과 요약·후보 표
def test_review_page_shows_search_summary(data_dir):
    from streamlit.testing.v1 import AppTest

    from conftest import ROOT

    add_case("사업", "자문회의 참석수당", ["자문수당"])
    add_case("행사", "회의 식비", ["회의비"])
    state = run(advisory_fake(case_selection={"selected_cases": [{"case_id": 1, "reason": "유사 자문수당"}]}), top_k=5)
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30)
    at.session_state["review"] = state
    at.run()
    assert not at.exception
    assert any("저장된 사례 2건 → 키워드 검색" in c.value and "AI가 1건 선택" in c.value for c in at.caption)
    assert any("검색 후보" in e.label for e in at.expander)
    assert len(at.dataframe) == 1
