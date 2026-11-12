"""검토 절차(1~6단계)를 순서대로 실행한다. 화면(app.py)과 분리해 테스트할 수 있게 했다.

state(dict) 하나에 진행 결과를 단계별로 쌓는다. 중간 단계에서 AI 오류가 나면
이미 끝난 단계는 그대로 두고, 같은 state로 run_review를 다시 부르면 실패한 단계부터 이어서 실행한다.
"""
import uuid
from datetime import datetime

import ai
import config
import storage
from ai import FLAGGED

DISCLAIMER = "※ 이 결과는 AI의 검토 의견이며 법적 적정성을 최종 보증하지 않습니다. 최종 판단은 담당자와 관련 부서가 해야 합니다."


def new_state(main_doc: dict, evidence_docs: list[dict], guidelines: list[dict]) -> dict:
    """main_doc/evidence_docs: {"name", "data"(bytes), "text"(None이면 추출 실패)}
    guidelines: {"name", "text"}"""
    return {
        "id": uuid.uuid4().hex,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "documents": [{**main_doc, "role": "대표 검토 문서"}] + [{**d, "role": "추가 증빙자료"} for d in evidence_docs],
        "guidelines": guidelines,
        "warnings": [],
        "done": False,
    }


def _warn(state: dict, msg: str) -> None:
    if msg not in state["warnings"]:
        state["warnings"].append(msg)


def flagged_items(state: dict) -> list[dict]:
    return [i for i in state["initial"]["items"] if i["status"] in FLAGGED]


def _case_for_prompt(c: dict) -> dict:
    return {
        "case_id": c["id"],
        "business_name": c["business_name"],
        "expense_item": c["expense_item"],
        "amount": c["amount"],
        "review_result": c["review_result"],
        "ai_question": c["ai_question"],
        "user_answer": c["user_answer"],
        "created_at": c["created_at"],
        "attached_file_names": [p.split("/")[-1] for p in c["file_paths"]],
    }


def run_review(state: dict, client) -> dict:
    # 1단계: 공식 지침 분석
    if "initial" not in state:
        prompt = ai.build_review_prompt(state["guidelines"], state["documents"])
        ai.check_size(prompt, "지침 검토(1단계)")
        data = client.complete_json("guideline_review", ai.SYSTEM_PROMPT, prompt)
        state["initial"] = ai.normalize_review(data, state["guidelines"])
        for item in state["initial"]["items"]:
            if any(not r["verified"] for r in item["related_guidelines"]):
                _warn(state, f"'{item['expense_item']}' 항목의 지침 인용 중 등록된 지침 원문에서 확인되지 않는 문장이 있습니다. 해당 인용은 직접 확인하세요.")

    flagged = flagged_items(state)

    # 2단계: 과거 사례 분석 (SQLite 전체 사례 → AI가 관련 사례 ID 선택)
    if "selection" not in state:
        cases = storage.list_cases()
        if not flagged:
            state["selection"] = {"selected": [], "note": "추가 확인이 필요한 항목이 없어 과거 사례 조회를 생략했습니다.", "ignored_ids": []}
        elif not cases:
            state["selection"] = {"selected": [], "note": "저장된 과거 사례가 없습니다.", "ignored_ids": []}
        else:
            state["selection"] = _select_cases(state, flagged, cases, client)

    # 3단계: 선택된 사례의 원본 문서를 실제로 열어 텍스트 추출 (선택되지 않은 사례의 파일은 열지 않는다)
    if "past_cases" not in state:
        state["past_cases"] = _load_past_cases(state)

    # 4단계: 추가 질문 생성
    if "questions" not in state:
        if not flagged:
            state["questions"] = []
        else:
            prompt = ai.build_question_prompt(
                state["guidelines"], state["documents"], state["initial"], [i["item_no"] for i in flagged], state["past_cases"]
            )
            ai.check_size(prompt, "추가 질문 생성(4단계)")
            data = client.complete_json("questions", ai.SYSTEM_PROMPT, prompt)
            state["questions"] = ai.normalize_questions(data, state["initial"])

    state["done"] = True
    return state


def _select_cases(state: dict, flagged: list[dict], cases: list[dict], client) -> dict:
    items = [{k: i[k] for k in ("item_no", "expense_item", "amount", "purpose", "status", "reasoning", "unclear_points")} for i in flagged]
    records = [_case_for_prompt(c) for c in cases]
    business = state["initial"]["business_name"]

    def make(recs, note=""):
        return ai.build_case_selection_prompt(business, items, recs, config.MAX_SELECTED_CASES, note)

    prompt = make(records)
    if len(prompt) > config.MAX_INPUT_CHARS:
        # 사례가 너무 많으면 최신 사례부터 한도 안에서 넣고, 그 사실을 사용자와 AI 모두에게 알린다.
        kept = list(records)
        while kept and len(make(kept, "※ 입력 한도 때문에 최신 사례 일부만 제공되었습니다.")) > config.MAX_INPUT_CHARS:
            kept.pop(0)
        if not kept:
            ai.check_size(prompt, "과거 사례 선택(2단계)")
        _warn(state, f"저장된 사례가 많아 입력 한도를 초과했습니다. 전체 {len(records)}건 중 최신 {len(kept)}건만 AI가 검토했습니다. "
                     "오래된 사례는 이번 검토에서 참고되지 않았습니다.")
        prompt = make(kept, "※ 입력 한도 때문에 최신 사례 일부만 제공되었습니다.")
    data = client.complete_json("case_selection", ai.SYSTEM_PROMPT, prompt)
    selection = ai.normalize_selection(data, {c["id"] for c in cases}, config.MAX_SELECTED_CASES)
    if selection["ignored_ids"]:
        _warn(state, f"AI가 존재하지 않는 사례 번호 {selection['ignored_ids']}를 제시하여 무시했습니다.")
    return selection


def _load_past_cases(state: dict) -> list[dict]:
    selected = state["selection"]["selected"]
    if not selected:
        return []
    reasons = {s["case_id"]: s for s in selected}
    cache = {}  # 여러 사례가 같은 파일을 참조하면 한 번만 읽는다
    result = []
    for case in storage.get_cases([s["case_id"] for s in selected]):
        docs = []
        for rel in case["file_paths"]:
            if rel not in cache:
                cache[rel] = storage.read_case_documents({"file_paths": [rel]})[0]
            doc = dict(cache[rel])
            if doc["text"] and len(doc["text"]) > config.PAST_DOC_MAX_CHARS:
                total = len(doc["text"])
                doc["text"] = doc["text"][: config.PAST_DOC_MAX_CHARS] + f"\n[이하 생략: 전체 {total:,}자 중 앞부분 {config.PAST_DOC_MAX_CHARS:,}자만 제공됨]"
                _warn(state, f"사례 #{case['id']}의 문서 '{doc['name']}'가 길어 앞부분 {config.PAST_DOC_MAX_CHARS:,}자만 AI에 제공했습니다(전체 {total:,}자).")
            if doc["warning"]:
                _warn(state, f"사례 #{case['id']}: {doc['warning']} — DB에 저장된 질문·답변만으로 검토합니다.")
            docs.append(doc)
        if not case["file_paths"]:
            _warn(state, f"사례 #{case['id']}: 과거 사례의 첨부파일을 찾을 수 없습니다(저장된 파일 경로 없음). DB 기록만으로 검토합니다.")
        result.append({**case, "reason": reasons[case["id"]]["reason"], "related_item_nos": reasons[case["id"]]["related_item_nos"], "documents": docs})
    return result


# ---------------------------------------------------------------- 5~6단계
def generate_final(state: dict, answers: dict[int, str], client) -> dict:
    """사용자 답변을 반영해 최종 의견을 만든다. 사용한 답변을 함께 기록해 둔다."""
    answers = {int(k): (v or "").strip() for k, v in answers.items()}
    answered = [q["item_no"] for q in state["questions"] if answers.get(q["item_no"])]
    if not answered:
        raise ai.AIError("답변이 입력된 질문이 없습니다. 하나 이상 답변을 입력하세요.")
    items = {i["item_no"]: i for i in state["initial"]["items"]}
    qa = [
        {
            "item_no": q["item_no"],
            "expense_item": items[q["item_no"]]["expense_item"],
            "initial_status": items[q["item_no"]]["status"],
            "ai_question": q["question"],
            "user_answer": answers.get(q["item_no"]) or "(답변 없음)",
        }
        for q in state["questions"]
    ]
    prompt = ai.build_final_prompt(state["guidelines"], state["documents"], state["initial"], qa, state["past_cases"])
    ai.check_size(prompt, "최종 의견 생성(6단계)")
    data = client.complete_json("final_opinion", ai.SYSTEM_PROMPT, prompt)
    result = ai.normalize_final(data, state["initial"], answered)
    state["final"] = {"result": result, "answers": {n: answers[n] for n in answered}}
    return state["final"]


def _fmt_amount(amount) -> str:
    return f"{amount:,}원" if isinstance(amount, int) else "금액 미기재"


def format_review_result(item: dict, final_item: dict | None) -> str:
    """DB review_result 칸에 넣을 텍스트(최초 검토 + 답변 반영 후 최종 의견)."""
    lines = [f"[최초 지침 검토] {item['status']}", f"판단 근거: {item['reasoning']}"]
    for r in item["related_guidelines"]:
        mark = "" if r["verified"] else " (원문 미확인 인용)"
        lines.append(f"관련 지침: {r['guideline']} - \"{r['quote']}\"{mark}")
    if item["unclear_points"]:
        lines.append("불명확 사항: " + " / ".join(item["unclear_points"]))
    if final_item:
        lines += [
            "",
            f"[답변 반영 후 최종 의견] {final_item['final_status']}",
            f"지침 검토: {final_item['guideline_review']}",
            f"담당자 설명: {final_item['user_explanation']}",
            f"과거 사례(관행): {final_item['related_past_cases']}",
            f"지침상 문제·불확실성: {final_item['guideline_issues']}",
            "추가 확인: " + (" / ".join(final_item["additional_evidence"]) or "없음"),
            f"종합 의견: {final_item['opinion']}",
        ]
    lines.append(DISCLAIMER)
    return "\n".join(lines)


def build_case_records(state: dict, item_nos: list[int] | None = None) -> list[dict]:
    """저장 대상만 고른다: 위반 가능성/판단 불가 + AI 질문 있음 + 사용자 답변 있음(최종 의견에 반영된 답변).
    item_nos를 주면 사용자가 저장하기로 선택한 항목만 남긴다."""
    final = state.get("final")
    if not final:
        return []
    items = {i["item_no"]: i for i in state["initial"]["items"]}
    finals = {f["item_no"]: f for f in final["result"]["items"]}
    records = []
    for q in state["questions"]:
        item = items[q["item_no"]]
        answer = final["answers"].get(q["item_no"], "").strip()
        if item["status"] not in FLAGGED or not q["question"] or not answer:
            continue
        if item_nos is not None and q["item_no"] not in item_nos:
            continue
        records.append(
            {
                "item_no": q["item_no"],
                "business_name": state["initial"]["business_name"],
                "expense_item": item["expense_item"],
                "amount": item["amount"],
                "review_result": format_review_result(item, finals.get(q["item_no"])),
                "ai_question": q["question"],
                "user_answer": answer,
            }
        )
    return records


def save_review(state: dict, item_nos: list[int] | None = None) -> dict:
    """사례를 DB에 저장하고 업로드 문서를 로컬 폴더에 복사한다. 같은 검토를 두 번 저장하지 않는다.
    item_nos를 주면 그 항목만 저장한다."""
    if state.get("saved"):
        return {**state["saved"], "already_saved": True}
    records = build_case_records(state, item_nos)
    if not records:
        raise ValueError("저장할 사례가 없습니다. 위반 가능성·판단 불가 항목의 질문에 답변하고 최종 의견을 생성한 뒤, 저장할 항목을 선택하세요.")
    files = [(d["name"], d["data"]) for d in state["documents"]]
    result = storage.save_cases(records, files)
    state["saved"] = result
    return result


def answers_changed(state: dict, answers: dict[int, str]) -> bool:
    """최종 의견을 만든 뒤 답변을 고쳤는지 확인한다(고쳤다면 최종 의견을 다시 만들어야 저장 가능)."""
    final = state.get("final")
    if not final:
        return False
    current = {int(k): (v or "").strip() for k, v in answers.items() if (v or "").strip()}
    return current != {int(k): v for k, v in final["answers"].items()}

