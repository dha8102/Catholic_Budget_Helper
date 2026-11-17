"""검토 절차(1~6단계)를 순서대로 실행한다. 화면(app.py)과 분리해 테스트할 수 있게 했다.

state(dict) 하나에 진행 결과를 단계별로 쌓는다. 중간 단계에서 AI 오류가 나면
이미 끝난 단계는 그대로 두고, 같은 state로 run_review를 다시 부르면 실패한 단계부터 이어서 실행한다.
"""
import uuid
from datetime import datetime

import ai
import config
import retrieval
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
        "keywords": c.get("keywords", []),
        "attached_file_names": [p.split("/")[-1] for p in c["file_paths"]],
    }


def run_review(state: dict, client, top_k: int | None = None) -> dict:
    """top_k: 과거 사례 검색에서 AI 선택 후보로 넘길 최대 건수(환경 설정 값, 없으면 .env/기본값)."""
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

    # 2단계: 과거 사례 분석 (키워드 검색 + 임베딩 검색 → 상위 K건 후보 → AI가 관련 사례 선택)
    if "selection" not in state:
        cases = storage.list_cases()
        if not flagged:
            state["selection"] = {"selected": [], "note": "추가 확인이 필요한 항목이 없어 과거 사례 조회를 생략했습니다.", "ignored_ids": []}
        elif not cases:
            state["selection"] = {"selected": [], "note": "저장된 과거 사례가 없습니다.", "ignored_ids": []}
        else:
            k = max(1, min(int(top_k or config.RETRIEVAL_TOP_K), config.RETRIEVAL_TOP_K_MAX))
            state["selection"] = _select_cases(state, flagged, cases, client, k)

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


def _select_cases(state: dict, flagged: list[dict], cases: list[dict], client, top_k: int) -> dict:
    business = state["initial"]["business_name"]
    # 검색: 키워드 검색·임베딩 검색 결과를 합쳐 상위 top_k건만 후보로 남긴다(사례가 많아도 입력이 일정).
    found = retrieval.search_cases(business, flagged, cases, client, top_k)
    for w in found["warnings"]:
        _warn(state, w)
    candidates = found["candidates"]
    state["retrieval"] = {
        "total": found["total"], "top_k": top_k, "keywords": found["keywords"],
        "keyword_hit_count": len(found["keyword_hits"]), "embedding_hit_count": len(found["embedding_hits"]),
        "candidates": [{"case_id": c["id"], "business_name": c["business_name"], "expense_item": c["expense_item"],
                        "amount": c["amount"], **c["search"]} for c in candidates],
    }
    if not candidates:
        return {"selected": [], "note": f"저장된 사례 {len(cases)}건 중 키워드·임베딩 검색으로 찾은 유사 사례가 없습니다.", "ignored_ids": []}

    items = [{k: i[k] for k in ("item_no", "expense_item", "amount", "purpose", "status", "reasoning", "unclear_points", "keywords")} for i in flagged]
    prompt = ai.build_case_selection_prompt(business, items, [_case_for_prompt(c) for c in candidates], config.MAX_SELECTED_CASES)
    ai.check_size(prompt, "과거 사례 선택(2단계)")
    data = client.complete_json("case_selection", ai.SYSTEM_PROMPT, prompt)
    selection = ai.normalize_selection(data, {c["id"] for c in candidates}, config.MAX_SELECTED_CASES)
    if selection["ignored_ids"]:
        _warn(state, f"AI가 검색 후보에 없는 사례 번호 {selection['ignored_ids']}를 제시하여 무시했습니다.")
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


# ---------------------------------------------------------------- 항목별 화면 구성 도우미
def question_label(q: dict) -> str:
    """화면·프롬프트에 쓰는 질문 번호: '항목번호-순번' (예: 1-2)."""
    return f"{q['item_no']}-{q['seq']}"


def questions_by_item(state: dict) -> dict[int, list[dict]]:
    out: dict[int, list[dict]] = {}
    for q in state.get("questions") or []:
        out.setdefault(q["item_no"], []).append(q)
    return out


def past_cases_for_item(state: dict, item_no: int) -> list[dict]:
    """이 항목과 관련된 과거 사례: AI가 이 항목과 관련 있다고 고른 사례 + 이 항목 질문에서 참고한 사례.
    어느 항목과 관련 있는지 표시되지 않은 사례는 모든 항목에 보여 준다."""
    refs = {cid for q in questions_by_item(state).get(item_no, []) for cid in q["referenced_case_ids"]}
    return [c for c in state.get("past_cases") or []
            if item_no in (c.get("related_item_nos") or []) or c["id"] in refs or not c.get("related_item_nos")]


# ---------------------------------------------------------------- 5~6단계
def add_attachment(bucket: list[dict], name: str, data: bytes) -> str | None:
    """답변 첨부 목록(bucket)에 파일을 추가한다. 형식·크기가 맞지 않으면 오류 메시지를 돌려준다.
    내용(해시)이 같은 파일은 한 번만 넣고, 텍스트를 뽑지 못한 파일(스캔본 등)도 보관용으로 넣는다."""
    err = storage.validate_upload(name, len(data))
    if err:
        return err
    digest = storage.file_hash(data)
    if any(storage.file_hash(x["data"]) == digest for x in bucket):
        return None
    try:
        bucket.append({"name": name, "data": data, "text": storage.extract_text(name, data), "error": None})
    except storage.ExtractionError as e:
        bucket.append({"name": name, "data": data, "text": None, "error": str(e)})
    return None


def _answered_files(answers: dict[int, str], files: dict | None) -> dict[int, list[dict]]:
    """답변 내용이 입력된 질문의 첨부 증빙만 남긴다. files: {qid: [{"name", "data", "text", "error"}]}"""
    return {int(qid): list(fs) for qid, fs in (files or {}).items() if fs and answers.get(int(qid))}


def unique_files(file_lists) -> list[dict]:
    """여러 질문에 첨부된 파일을 내용(해시) 기준으로 하나씩만 남긴다(처음 올린 파일명 사용)."""
    seen, out = set(), []
    for files in file_lists:
        for f in files:
            digest = storage.file_hash(f["data"])
            if digest not in seen:
                seen.add(digest)
                out.append(f)
    return out


def _evidence_for_prompt(attached: dict[int, list[dict]], questions: list[dict]) -> list[dict]:
    """같은 파일은 AI에도 한 번만 보내고, 어느 질문들에 첨부됐는지 함께 적는다."""
    labels = {q["qid"]: question_label(q) for q in questions}
    by_hash: dict[str, dict] = {}
    for qid in sorted(attached):
        for f in attached[qid]:
            entry = by_hash.setdefault(storage.file_hash(f["data"]), {"name": f["name"], "text": f["text"], "questions": []})
            if labels.get(qid) and labels[qid] not in entry["questions"]:
                entry["questions"].append(labels[qid])
    return list(by_hash.values())


def generate_final(state: dict, answers: dict[int, str], client, files: dict | None = None) -> dict:
    """사용자 답변(과 답변에 첨부한 증빙)을 반영해 최종 의견을 만든다. 사용한 답변·첨부를 함께 기록해 둔다.
    answers·files의 키는 질문 번호(qid)다. 한 항목에 질문이 여러 개면 일부만 답해도 된다."""
    answers = {int(k): (v or "").strip() for k, v in answers.items()}
    answered_q = [q for q in state["questions"] if answers.get(q["qid"])]
    if not answered_q:
        raise ai.AIError("답변이 입력된 질문이 없습니다. 하나 이상 답변을 입력하세요.")
    attached = _answered_files(answers, files)
    items = {i["item_no"]: i for i in state["initial"]["items"]}
    answered_items = sorted({q["item_no"] for q in answered_q})
    qa = [
        {
            "question_no": question_label(q),
            "item_no": q["item_no"],
            "expense_item": items[q["item_no"]]["expense_item"],
            "initial_status": items[q["item_no"]]["status"],
            "ai_question": q["question"],
            "user_answer": answers.get(q["qid"]) or "(답변 없음)",
            "attached_evidence": [f["name"] for f in attached.get(q["qid"], [])],
        }
        for q in state["questions"]
    ]
    prompt = ai.build_final_prompt(state["guidelines"], state["documents"], state["initial"], qa, state["past_cases"],
                                   _evidence_for_prompt(attached, state["questions"]))
    ai.check_size(prompt, "최종 의견 생성(6단계)")
    data = client.complete_json("final_opinion", ai.SYSTEM_PROMPT, prompt)
    result = ai.normalize_final(data, state["initial"], answered_items)
    state["final"] = {"result": result, "answers": {q["qid"]: answers[q["qid"]] for q in answered_q}, "files": attached}
    return state["final"]


def _fmt_amount(amount) -> str:
    return f"{amount:,}원" if isinstance(amount, int) else "금액 미기재"


def format_review_result(item: dict, final_item: dict | None, attachments: list[str] | None = None) -> str:
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
    if attachments:
        lines.append("답변 첨부 증빙: " + ", ".join(attachments))
    lines.append(DISCLAIMER)
    return "\n".join(lines)


def _join_numbered(lines: list[str]) -> str:
    """질문이 하나면 그대로, 여러 개면 '1) ... / 2) ...'처럼 번호를 붙여 한 칸에 저장한다."""
    return lines[0] if len(lines) == 1 else "\n".join(f"{n}) {x}" for n, x in enumerate(lines, 1))


def build_case_records(state: dict, item_nos: list[int] | None = None) -> list[dict]:
    """저장 대상만 고른다: 위반 가능성/판단 불가 항목 중 질문에 하나 이상 답변한 항목(최종 의견에 반영된 답변).
    집행항목 하나가 사례 1건이 되며, 그 항목의 질문·답변을 모두 묶어 저장한다.
    item_nos를 주면 사용자가 저장하기로 선택한 항목만 남긴다."""
    final = state.get("final")
    if not final:
        return []
    items = {i["item_no"]: i for i in state["initial"]["items"]}
    finals = {f["item_no"]: f for f in final["result"]["items"]}
    records = []
    for no, qs in sorted(questions_by_item(state).items()):
        item = items[no]
        answers = [final["answers"].get(q["qid"], "").strip() for q in qs]
        if item["status"] not in FLAGGED or not any(answers):
            continue
        if item_nos is not None and no not in item_nos:
            continue
        # 같은 파일을 이 항목의 여러 질문에 첨부했어도 한 번만 저장한다(다른 항목과의 중복은 save_cases에서 처리)
        attached = unique_files(final.get("files", {}).get(q["qid"], []) for q in qs)
        records.append(
            {
                "item_no": no,
                "business_name": state["initial"]["business_name"],
                "expense_item": item["expense_item"],
                "amount": item["amount"],
                "review_result": format_review_result(item, finals.get(no), [f["name"] for f in attached]),
                "ai_question": _join_numbered([q["question"] for q in qs]),
                "user_answer": _join_numbered([a or "(답변 없음)" for a in answers]),
                "question_count": len(qs),
                "answered_count": sum(1 for a in answers if a),
                "keywords": item.get("keywords", []),  # 이후 키워드 검색에 사용
                "extra_files": [(f["name"], f["data"]) for f in attached],  # 이 사례에만 연결되는 답변 첨부 증빙
            }
        )
    return records


def save_review(state: dict, item_nos: list[int] | None = None, client=None) -> dict:
    """사례를 DB에 저장하고 업로드 문서를 로컬 폴더에 복사한다. 같은 검토를 두 번 저장하지 않는다.
    item_nos를 주면 그 항목만 저장한다. client를 주면 저장 직후 임베딩 검색 색인도 만든다
    (실패하거나 client가 없으면 다음 검토 때 자동으로 만든다)."""
    if state.get("saved"):
        return {**state["saved"], "already_saved": True}
    records = build_case_records(state, item_nos)
    if not records:
        raise ValueError("저장할 사례가 없습니다. 위반 가능성·판단 불가 항목의 질문에 답변하고 최종 의견을 생성한 뒤, 저장할 항목을 선택하세요.")
    files = [(d["name"], d["data"]) for d in state["documents"]]  # 검토 문서·증빙은 모든 사례가 함께 참조
    result = storage.save_cases(records, files)
    result["index_warning"] = None
    if result["saved_ids"] and client is not None:
        try:
            retrieval.index_cases(result["saved_ids"], client)
        except ai.AIError as e:
            result["index_warning"] = f"사례는 저장했지만 검색 색인(임베딩)을 만들지 못했습니다. 다음 검토 때 자동으로 다시 만듭니다: {e}"
    state["saved"] = result
    return result


def _files_signature(files: dict[int, list[dict]]) -> dict[int, list[tuple[str, str]]]:
    return {int(qid): sorted((f["name"], storage.file_hash(f["data"])) for f in fs) for qid, fs in files.items() if fs}


def answers_changed(state: dict, answers: dict[int, str], files: dict | None = None) -> bool:
    """최종 의견을 만든 뒤 답변이나 첨부 증빙을 바꿨는지 확인한다(바꿨다면 최종 의견을 다시 만들어야 저장 가능)."""
    final = state.get("final")
    if not final:
        return False
    current = {int(k): (v or "").strip() for k, v in answers.items() if (v or "").strip()}
    if current != {int(k): v for k, v in final["answers"].items()}:
        return True
    return _files_signature(_answered_files(current, files)) != _files_signature(final.get("files", {}))

