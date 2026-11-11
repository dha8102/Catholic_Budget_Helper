"""AI 기반 대학 행정지침 검토 및 집행사례 관리 시스템 (Streamlit 화면)

실행: streamlit run app.py  (또는 start.bat)
"""
from pathlib import Path

import streamlit as st

import ai
import config
import review
import storage

st.set_page_config(page_title="행정지침 검토 도우미", page_icon="📑", layout="wide")

API_NOTICE = (
    "이 프로그램은 완전한 오프라인 프로그램이 아닙니다. AI 검토를 실행하면 업로드한 문서, 등록된 지침, "
    "관련 과거 사례의 내용이 외부 OpenAI API로 전송됩니다. 개인정보·민감정보가 포함된 문서는 전송 전에 확인하세요. "
    + ("지침·사례 DB·첨부파일은 서버 PC에 저장되며 접속한 모든 사람이 함께 봅니다."
       if config.WEB_MODE else "지침·사례 DB·첨부파일은 이 PC의 data 폴더에만 저장됩니다.")
)
FILE_WARNING = (
    "저장된 문서의 파일명이나 폴더 위치를 임의로 변경하거나 파일을 삭제하면 이후 AI가 과거 사례의 원본 문서를 확인할 수 없습니다. "
    "해당 폴더의 파일은 프로그램을 통해 관리해 주세요."
)
STATUS_BADGE = {"적합": ":green[**적합**]", "위반 가능성": ":red[**위반 가능성**]", "판단 불가": ":orange[**판단 불가**]"}


def esc(text) -> str:
    """AI·사용자 텍스트를 마크다운으로 보여줄 때 ~(취소선), $(수식)가 깨지지 않게 한다."""
    return str(text or "").replace("~", "\\~").replace("$", "\\$")


def won(amount) -> str:
    return f"{amount:,}원" if isinstance(amount, int) else "금액 미기재"


@st.cache_data(show_spinner=False)
def extract_cached(path_str: str, mtime: float, size: int):
    """지침 텍스트 추출 결과를 파일 수정시각 기준으로 캐시한다. 반환: (text, error)"""
    try:
        return storage.extract_text_from_file(Path(path_str)), None
    except storage.ExtractionError as e:
        return None, str(e)
    except OSError as e:
        return None, f"파일을 읽을 수 없습니다: {e}"


def load_guideline(path: Path):
    stat = path.stat()
    return extract_cached(str(path), stat.st_mtime, stat.st_size)


def make_client():
    """웹 모드에서는 사용자가 화면에 입력한 API 키만 쓴다(서버 .env 키를 다른 사람이 쓰지 않도록)."""
    if config.WEB_MODE:
        key = (st.session_state.get("api_key") or "").strip()
        if not key:
            raise ai.AIError("왼쪽 메뉴의 'OpenAI API 키' 칸에 본인의 API 키를 입력한 뒤 다시 시도하세요.")
        return ai.get_client(key)
    return ai.get_client()


def flash(msg: str, kind: str = "success"):
    st.session_state.setdefault("flash", []).append((kind, msg))


def show_flash():
    for kind, msg in st.session_state.pop("flash", []):
        getattr(st, kind)(msg)


# ====================================================================== 화면 1. 지침 관리
def page_guidelines():
    st.header("📚 지침 관리")
    st.caption("교육부·지자체·교내 지침 파일을 등록합니다. "
               + ("등록된 지침은 접속한 모든 사람이 함께 사용합니다." if config.WEB_MODE else "등록된 파일은 data/guidelines/ 폴더에 저장됩니다."))
    show_flash()

    nonce = st.session_state.get("g_nonce", 0)
    files = st.file_uploader("지침 파일 등록 (PDF, DOCX, TXT · 여러 개 선택 가능)", type=["pdf", "docx", "txt"],
                             accept_multiple_files=True, key=f"g_upload_{nonce}")
    if st.button("지침 등록", type="primary", disabled=not files):
        for f in files:
            data = f.getvalue()
            err = storage.validate_upload(f.name, len(data))
            if err:
                flash(err, "error")
                continue
            try:
                text = storage.extract_text(f.name, data)
            except storage.ExtractionError as e:
                flash(f"'{f.name}' 등록 실패: {e}", "error")
                continue
            saved = storage.save_guideline(f.name, data)
            flash(f"'{saved.name}' 등록 완료 (텍스트 {len(text):,}자 추출)")
        st.session_state.g_nonce = nonce + 1  # 업로드 창 비우기
        st.rerun()

    st.subheader("등록된 지침 목록")
    paths = storage.list_guidelines()
    if not paths:
        st.info("등록된 지침이 없습니다.")
        return
    total = 0
    for p in paths:
        text, err = load_guideline(p)
        total += len(text or "")
        with st.container(border=True):
            c1, c2, c3 = st.columns([5, 3, 1])
            c1.markdown(f"**{esc(p.name)}**  \n{p.stat().st_size / 1024:,.0f} KB")
            if err:
                c2.error(f"텍스트 추출 실패: {err}")
            else:
                c2.success(f"텍스트 추출 정상 ({len(text):,}자)")
            if c3.button("삭제", key=f"del_g_{p.name}"):
                storage.delete_guideline(p.name)
                flash(f"'{p.name}' 삭제 완료")
                st.rerun()
            if text:
                with st.expander("추출된 텍스트 확인"):
                    st.text(text[:5000] + ("\n...(이하 생략, 미리보기는 5,000자까지만 표시)" if len(text) > 5000 else ""))
    msg = f"전체 지침 분량: {total:,}자 / AI 1회 입력 한도: {config.MAX_INPUT_CHARS:,}자"
    if total > config.MAX_INPUT_CHARS:
        st.error(msg + " — 전체 지침을 한 번에 검토할 수 없습니다. 검토 화면에서 사용할 지침을 골라 주세요.")
    else:
        st.caption(msg)


# ====================================================================== 화면 2. 신규 문서 검토
def read_upload(f, errors: list) -> dict | None:
    data = f.getvalue()
    err = storage.validate_upload(f.name, len(data))
    if err:
        errors.append(err)
        return None
    try:
        text = storage.extract_text(f.name, data)
        return {"name": f.name, "data": data, "text": text, "error": None}
    except storage.ExtractionError as e:
        return {"name": f.name, "data": data, "text": None, "error": str(e)}


def run_steps(state: dict):
    st.session_state.review_error = None
    try:
        client = make_client()
        with st.spinner("AI가 검토 중입니다: 지침 분석 → 과거 사례 선택 → 원본 문서 확인 → 추가 질문 생성 (수십 초~몇 분 걸릴 수 있습니다)"):
            review.run_review(state, client)
    except ai.AIError as e:
        st.session_state.review_error = str(e)
    except Exception as e:  # 예기치 못한 오류도 프로그램이 멈추지 않게 표시만 한다
        st.session_state.review_error = f"예기치 않은 오류가 발생했습니다: {type(e).__name__}: {e}"


def page_review():
    st.header("📝 신규 문서 검토")
    st.info(API_NOTICE)

    # ---------------- 상단: 문서 업로드
    st.subheader("1. 문서 업로드")
    main_file = st.file_uploader("검토할 행정문서 (기안문·지출결의서·결과보고서 등 대표 문서 1개)", type=["pdf", "docx", "txt"], key="main_upload")
    evidence_files = st.file_uploader("추가 증빙자료 (여러 개 가능)", type=["pdf", "docx", "txt"], accept_multiple_files=True, key="evidence_upload")

    paths = storage.list_guidelines()
    if not paths:
        st.warning("등록된 지침이 없습니다. 먼저 '지침 관리' 화면에서 지침을 등록하세요.")
    names = [p.name for p in paths]
    chosen = st.multiselect("검토에 사용할 지침", names, default=names)
    guidelines = []
    for p in paths:
        if p.name in chosen:
            text, err = load_guideline(p)
            if err:
                st.warning(f"지침 '{p.name}'은(는) 텍스트를 추출할 수 없어 검토에서 제외됩니다: {err}")
            else:
                guidelines.append({"name": p.name, "text": text})
    total = sum(len(g["text"]) for g in guidelines)
    too_big = total > config.MAX_INPUT_CHARS
    if too_big:
        st.error(f"선택한 지침 분량({total:,}자)이 AI 입력 한도({config.MAX_INPUT_CHARS:,}자)를 초과합니다. "
                 "내용을 잘라서 검토하지 않으므로, 이번 문서와 관련된 지침만 선택해 주세요.")
    else:
        st.caption(f"선택한 지침 분량: {total:,}자 / 한도 {config.MAX_INPUT_CHARS:,}자")

    if st.button("🔍 AI 검토 실행", type="primary", disabled=not main_file or not guidelines or too_big):
        errors = []
        main = read_upload(main_file, errors)
        evidence = [d for d in (read_upload(f, errors) for f in evidence_files or []) if d]
        if main and main["error"]:
            errors.append(f"대표 문서 '{main['name']}': {main['error']}")
        if errors:
            for e in errors:
                st.error(e)
        else:
            state = review.new_state(main, evidence, guidelines)
            for d in evidence:
                if d["error"]:
                    state["warnings"].append(f"증빙자료 '{d['name']}': {d['error']} 이 파일은 AI가 내용을 확인하지 못하지만 사례 저장 시 함께 보관됩니다.")
            st.session_state.review = state
            st.session_state.final_error = None
            run_steps(state)

    state = st.session_state.get("review")
    if not state:
        return
    st.divider()
    show_review(state)


def show_review(state: dict):
    if st.session_state.get("review_error"):
        st.error(st.session_state.review_error)
        if not state.get("done"):
            st.caption("이미 끝난 단계의 결과는 유지되며, 실패한 단계부터 다시 실행합니다.")
            if st.button("🔁 중단된 단계부터 다시 시도"):
                run_steps(state)
                st.rerun()
    for w in state["warnings"]:
        st.warning(w)
    if "initial" not in state:
        return

    initial = state["initial"]
    items = {i["item_no"]: i for i in initial["items"]}

    # ---------------- 중단: AI 검토 결과
    st.subheader("2. AI 검토 결과")
    st.caption(review.DISCLAIMER)
    st.markdown(f"**사업명:** {esc(initial['business_name'])}")
    if initial["document_summary"]:
        st.markdown(f"**문서 요약:** {esc(initial['document_summary'])}")
    st.caption("검토 문서: " + ", ".join(f"{d['name']} ({d['role']})" for d in state["documents"]))
    if initial["guideline_priority_note"]:
        st.warning(f"지침 간 적용 관계: {initial['guideline_priority_note']}")
    if not initial["items"]:
        st.info("문서에서 예산 집행항목을 찾지 못했습니다.")
    for it in initial["items"]:
        with st.container(border=True):
            c1, c2, c3 = st.columns([4, 2, 2])
            c1.markdown(f"**항목 {it['item_no']}. {esc(it['expense_item'])}**")
            c2.markdown(f"금액: **{won(it['amount'])}**")
            c3.markdown(f"검토 상태: {STATUS_BADGE[it['status']]}")
            if it["purpose"]:
                st.markdown(f"**집행 목적·내용:** {esc(it['purpose'])}")
            st.markdown(f"**판단 근거:** {esc(it['reasoning'])}")
            if it["related_guidelines"]:
                st.markdown("**관련 지침**")
                for r in it["related_guidelines"]:
                    mark = "" if r["verified"] else " ⚠️ *(등록된 지침 원문에서 확인되지 않은 인용 — 직접 확인 필요)*"
                    st.markdown(f"- {esc(r['guideline'])}: “{esc(r['quote'])}”{mark}")
            else:
                st.markdown("**관련 지침:** 제공된 지침에서 근거를 찾지 못함")
            if it["unclear_points"]:
                st.markdown("**추가 확인 필요:** " + esc(" / ".join(it["unclear_points"])))

    st.markdown("#### 관련 과거 사례")
    sel = state.get("selection")
    if sel is not None:
        if not state.get("past_cases"):
            st.caption(sel.get("note") or "관련 과거 사례가 없습니다.")
        for c in state.get("past_cases", []):
            with st.container(border=True):
                st.markdown(f"**사례 #{c['id']}** · {esc(c['business_name'])} · {esc(c['expense_item'])} · {won(c['amount'])}")
                st.markdown(f"선택 이유: {esc(c['reason'])}")
                st.markdown(f"당시 질문: {esc(c['ai_question'])}  \n당시 답변: {esc(c['user_answer'])}")
                for d in c["documents"]:
                    if d["text"]:
                        st.caption(f"📄 원본 문서 확인: {d['name']} ({len(d['text']):,}자를 읽어 AI에 제공)")
                    else:
                        st.caption(f"⚠️ {d['warning']}")

    if not state.get("done"):
        return

    # ---------------- 하단: AI 추가 질문
    st.subheader("3. AI 추가 질문")
    questions = state["questions"]
    if not questions:
        st.success("추가 확인이 필요한 항목이 없어 질문하지 않습니다. (적합 판정 항목은 사례 DB에 저장하지 않습니다.)")
        return
    answers = {}
    for q in questions:
        it = items[q["item_no"]]
        with st.container(border=True):
            st.markdown(f"**질문 {q['item_no']}. [{esc(it['expense_item'])} · {won(it['amount'])} · {it['status']}]**")
            st.markdown(esc(q["question"]))
            detail = f"질문 사유: {q['reason']}"
            if q["reason_types"]:
                detail += f" ({', '.join(q['reason_types'])})"
            if q["referenced_case_ids"]:
                detail += f" · 참고한 과거 사례: {', '.join('#' + str(i) for i in q['referenced_case_ids'])}"
            st.caption(detail)
            if q["past_case_comparison"]:
                st.caption(f"과거 사례 비교: {q['past_case_comparison']}")
            answers[q["item_no"]] = st.text_area("담당자 답변", key=f"answer_{state['id']}_{q['item_no']}",
                                                 placeholder="예: 내부 회의를 거쳐 사업단장 재가를 받았습니다. (회의록 첨부)")

    # 버튼을 비활성화하지 않는다: 입력 직후 클릭하면 입력값이 확정되기 전이라 첫 클릭이 무시되기 때문
    if st.button("💬 답변 반영 및 최종 의견 생성", type="primary"):
        st.session_state.final_error = None
        try:
            with st.spinner("답변을 반영해 최종 의견을 작성 중입니다..."):
                review.generate_final(state, answers, make_client())
        except ai.AIError as e:
            st.session_state.final_error = str(e)
        except Exception as e:
            st.session_state.final_error = f"예기치 않은 오류가 발생했습니다: {type(e).__name__}: {e}"
    if st.session_state.get("final_error"):
        st.error(st.session_state.final_error)
        st.caption("버튼을 다시 눌러 재시도할 수 있습니다.")

    final = state.get("final")
    if not final:
        return
    show_final(state, final, items)

    # ---------------- 사례 DB 저장
    st.subheader("5. 사례 DB 저장")
    st.caption("저장 대상: '위반 가능성' 또는 '판단 불가' 항목 중 AI 질문에 답변한 항목. 업로드한 문서는 data/documents/ 에 함께 보관됩니다.")
    st.warning(FILE_WARNING)
    changed = review.answers_changed(state, answers)
    if changed:
        st.warning("최종 의견을 만든 뒤 답변이 바뀌었습니다. '답변 반영 및 최종 의견 생성'을 다시 눌러야 저장할 수 있습니다.")
    records = review.build_case_records(state)
    if st.button("💾 사례 DB 저장", disabled=changed or not records or bool(state.get("saved"))):
        try:
            review.save_review(state)
        except Exception as e:
            st.error(f"저장 중 오류가 발생해 저장을 취소했습니다(부분 저장된 내용은 정리했습니다): {e}")
        else:
            st.rerun()  # 저장 버튼을 바로 비활성화해 연속 클릭을 막는다
    saved = state.get("saved")
    if saved:
        ids = saved["saved_ids"]
        if ids:
            st.success(f"저장 완료: 사례 {', '.join('#' + str(i) for i in ids)} / 보관 파일 {len(saved['file_paths'])}개")
            for p in saved["file_paths"]:
                st.caption(f"data/{p}")
        if saved["duplicate_ids"]:
            st.info(f"이미 같은 내용으로 저장된 사례가 있어 새로 만들지 않았습니다: {', '.join('#' + str(i) for i in saved['duplicate_ids'])}")


def show_final(state: dict, final: dict, items: dict):
    res = final["result"]
    ov = res["overall"]
    st.subheader("4. 최종 검토 의견")
    st.caption(review.DISCLAIMER)
    with st.container(border=True):
        st.markdown(f"**주요 검토 내용**  \n{esc(ov['summary'])}")
        st.markdown(f"**문제 가능성이 있는 항목**  \n{esc(ov['problem_items'])}")
        st.markdown(f"**과거 유사 사례**  \n{esc(ov['past_cases_summary'] or '없음')}")
        st.markdown(f"**사용자 답변 반영 결과**  \n{esc(ov['answer_reflection'])}")
        if ov["additional_checks"]:
            st.markdown("**추가 확인사항**\n" + "\n".join(f"- {esc(x)}" for x in ov["additional_checks"]))
    for f in res["items"]:
        it = items[f["item_no"]]
        with st.expander(f"항목 {f['item_no']}. {it['expense_item']} — 최초 '{it['status']}' → 최종 '{f['final_status']}'", expanded=True):
            if f["status_note"]:
                st.warning(f["status_note"])
            st.markdown(f"1. **지침 검토 결과:** {esc(f['guideline_review'])}")
            st.markdown(f"2. **담당자가 설명한 집행 사유:** {esc(f['user_explanation'])}")
            st.markdown(f"3. **관련 과거 사례(관행 — 지침 근거와 별개):** {esc(f['related_past_cases'] or '없음')}")
            st.markdown(f"4. **지침상 문제 여부 및 남은 불확실성:** {esc(f['guideline_issues'])}")
            st.markdown("5. **추가 증빙·확인 필요 사항:** " + (esc(" / ".join(f["additional_evidence"])) or "없음"))
            st.markdown(f"6. **종합 검토 의견:** {STATUS_BADGE[f['final_status']]} — {esc(f['opinion'])}")


# ====================================================================== 화면 3. 저장된 사례
def page_cases():
    st.header("🗂️ 저장된 사례")
    st.warning(FILE_WARNING)
    show_flash()
    cases = storage.list_cases()
    if not cases:
        st.info("저장된 사례가 없습니다.")
        return
    st.caption(f"총 {len(cases)}건 (최신순)")
    for c in reversed(cases):
        with st.expander(f"#{c['id']} | {c['business_name']} | {c['expense_item']} | {won(c['amount'])} | {c['created_at']}"):
            st.markdown(f"**사업명:** {esc(c['business_name'])}  \n**예산 집행항목:** {esc(c['expense_item'])}  \n**집행금액:** {won(c['amount'])}")
            st.markdown("**AI 질문**")
            st.markdown(esc(c["ai_question"]))
            st.markdown("**담당자 답변**")
            st.markdown(esc(c["user_answer"]))
            st.markdown("**지침 검토 결과**")
            st.code(c["review_result"] or "", language=None, wrap_lines=True)
            st.markdown("**관련 파일**")
            if not c["file_paths"]:
                st.caption("저장된 파일 없음")
            for i, rel in enumerate(c["file_paths"]):
                path = storage.resolve_case_file(rel)
                if path and path.is_file():
                    st.download_button(f"📄 {path.name} 열기/다운로드", data=path.read_bytes(), file_name=path.name, key=f"dl_{c['id']}_{i}")
                else:
                    st.warning(f"과거 사례의 첨부파일을 찾을 수 없습니다: data/{rel}")
            confirm = st.checkbox("이 사례를 삭제합니다", key=f"confirm_{c['id']}")
            if st.button("사례 삭제", key=f"del_c_{c['id']}", disabled=not confirm):
                storage.delete_case(c["id"])
                flash(f"사례 #{c['id']}를 삭제했습니다.")
                st.rerun()


# ====================================================================== 웹 모드
def web_sidebar():
    st.sidebar.text_input("OpenAI API 키", type="password", key="api_key", placeholder="sk-...",
                          help="본인 키로 AI를 호출하며 요금도 본인 계정에 청구됩니다.")
    st.sidebar.caption("API 키는 서버에 저장되지 않습니다. 페이지를 새로고침하면 다시 입력해야 합니다.")
    if not (st.session_state.get("api_key") or "").strip():
        st.sidebar.warning("AI 검토를 하려면 API 키를 입력하세요.")


# ====================================================================== 메인
def main():
    storage.init_storage()
    st.sidebar.title("📑 행정지침 검토 도우미")
    if config.WEB_MODE:
        web_sidebar()
        st.sidebar.divider()
    page = st.sidebar.radio("화면 선택", ["신규 문서 검토", "지침 관리", "저장된 사례"], key="page")
    st.sidebar.divider()
    st.sidebar.caption(f"AI 모델: {config.OPENAI_MODEL}")
    if not config.WEB_MODE:
        if not config.OPENAI_API_KEY:
            st.sidebar.error("OPENAI_API_KEY가 설정되지 않았습니다. .env 파일을 확인하세요.")
        st.sidebar.caption(f"데이터 폴더: {storage.data_dir()}")
    st.sidebar.caption("⚠️ AI 검토 시 문서 내용이 외부 OpenAI API로 전송됩니다.")

    if page == "지침 관리":
        page_guidelines()
    elif page == "저장된 사례":
        page_cases()
    else:
        page_review()


main()
