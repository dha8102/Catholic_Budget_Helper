"""실제 OpenAI API로 sample_data 테스트 1·2·3·6을 자동 실행하고 판정·시간·비용을 확인한다.

사용법 (프로젝트 폴더에서):
    .venv\\Scripts\\python.exe scripts\\live_check.py
    .venv\\Scripts\\python.exe scripts\\live_check.py --price-in 0.40 --price-out 1.60

- .env의 OPENAI_API_KEY, OPENAI_MODEL을 사용합니다.
- 실제 data 폴더는 건드리지 않고 data/live_check/<시각>/ 에 별도 DB·파일을 만듭니다.
- 결과 전체(AI 응답 포함)는 같은 폴더의 report.json 에 저장됩니다.
- 테스트 4·5(재실행·파일 누락)는 프로그램 동작이라 모의 AI 자동 테스트(pytest)로 이미 검증됩니다.
"""
import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import ai  # noqa: E402
import config  # noqa: E402
import review  # noqa: E402
import storage  # noqa: E402

SAMPLES = ROOT / "sample_data"
ANSWER_T2 = "5월 15일 사업단 내부 회의에서 자문 단가를 2시간 30만 원으로 정하고 5월 18일 사업단장 재가를 받았습니다."
ANSWER_T6_1 = "작년에도 상품권으로 지급했고 사업단장 재가를 받았습니다."
ANSWER_T6_2 = "과거에도 같은 방식으로 지급했습니다."


def load_doc(rel: str) -> dict:
    path = SAMPLES / "documents" / rel
    data = path.read_bytes()
    return {"name": path.name, "data": data, "text": storage.extract_text(path.name, data)}


def load_guidelines() -> list[dict]:
    return [{"name": p.name, "text": storage.extract_text_from_file(p)} for p in sorted((SAMPLES / "guidelines").iterdir())]


class Runner:
    def __init__(self, client, price_in: float, price_out: float):
        self.client = client
        self.price_in, self.price_out = price_in, price_out
        self.checks: list[tuple[str, str, bool, str]] = []  # (테스트, 항목, 통과, 상세)
        self.report: dict = {}

    def check(self, test: str, label: str, ok: bool, detail: str = "") -> None:
        self.checks.append((test, label, bool(ok), detail))
        mark = "통과" if ok else "확인 필요"
        print(f"  [{mark}] {label}" + (f" — {detail}" if detail and not ok else ""))

    def cost(self, logs: list[dict]) -> tuple[int, int, float | None]:
        pin = sum(x["prompt_tokens"] for x in logs)
        pout = sum(x["completion_tokens"] for x in logs)
        if self.price_in is None or self.price_out is None:
            return pin, pout, None
        return pin, pout, pin / 1e6 * self.price_in + pout / 1e6 * self.price_out

    def run(self, test: str, title: str, fn) -> None:
        print(f"\n=== {test}: {title} ===")
        start_idx, started = len(self.client.usage_log), time.perf_counter()
        try:
            data = fn()
        except ai.AIError as e:
            self.check(test, "AI 호출 성공", False, str(e))
            data = {"error": str(e)}
        logs = self.client.usage_log[start_idx:]
        pin, pout, usd = self.cost(logs)
        secs = time.perf_counter() - started
        print(f"  소요 {secs:.0f}초 · AI 호출 {len(logs)}회 · 입력 {pin:,} / 출력 {pout:,} 토큰 · {fmt_usd(usd)}")
        self.report[test] = {"seconds": round(secs, 1), "calls": logs, "usd": usd, "data": data}


def fmt_usd(usd: float | None) -> str:
    return f"약 ${usd:.4f}" if usd is not None else "비용: 단가 미지정"


def summarize_state(state: dict) -> dict:
    keep = ("initial", "selection", "questions", "final", "warnings")
    out = {k: state.get(k) for k in keep}
    out["past_case_ids"] = [c["id"] for c in state.get("past_cases", [])]
    return out


def print_items(state: dict) -> None:
    for i in state["initial"]["items"]:
        unverified = sum(not r["verified"] for r in i["related_guidelines"])
        warn = f" ⚠️ 원문 미확인 인용 {unverified}건" if unverified else ""
        print(f"  - 항목 {i['item_no']}: {i['expense_item']} ({i['amount']}) → {i['status']}{warn}")
    for q in state.get("questions", []):
        print(f"  ? 질문(항목 {q['item_no']}): {q['question']}")
    for w in state["warnings"]:
        print(f"  ! 경고: {w}")


def answer_all(state: dict, text: str) -> dict:
    return {q["item_no"]: text for q in state["questions"]}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--price-in", type=float, help="입력 100만 토큰당 달러 (gpt-4.1-mini는 지정하지 않으면 0.40)")
    p.add_argument("--price-out", type=float, help="출력 100만 토큰당 달러 (gpt-4.1-mini는 지정하지 않으면 1.60)")
    args = p.parse_args()
    if args.price_in is None and args.price_out is None and config.OPENAI_MODEL == "gpt-4.1-mini":
        args.price_in, args.price_out = 0.40, 1.60

    out_dir = ROOT / "data" / "live_check" / datetime.now().strftime("%Y%m%d_%H%M%S")
    config.DATA_DIR = out_dir
    storage.init_storage()
    try:
        client = ai.get_client()
    except ai.AIError as e:
        print(f"[오류] {e}")
        return 2
    print(f"모델: {config.OPENAI_MODEL} · 결과 폴더: {out_dir}")
    if args.price_in is None or args.price_out is None:
        print("단가 미지정: 토큰 수만 표시합니다. 비용을 보려면 --price-in/--price-out 에 이 모델의 100만 토큰당 단가(달러)를 지정하세요.")
    else:
        print(f"단가: 입력 ${args.price_in}/1M, 출력 ${args.price_out}/1M 토큰")

    guidelines = load_guidelines()
    r = Runner(client, args.price_in, args.price_out)

    # 테스트 1: 정상 집행 → 적합, 질문 없음
    def t1():
        s = review.new_state(load_doc("test1_정상집행/지출결의서_회의비.txt"), [load_doc("test1_정상집행/회의록.txt")], guidelines)
        review.run_review(s, client)
        print_items(s)
        r.check("테스트1", "모든 항목이 '적합'", all(i["status"] == "적합" for i in s["initial"]["items"]),
                str([i["status"] for i in s["initial"]["items"]]))
        r.check("테스트1", "질문이 생성되지 않음", not s["questions"])
        return summarize_state(s)

    # 테스트 2: 지침에 없는 자문수당 → 판단 불가, 산정 근거·승인 질문, 저장
    def t2():
        s = review.new_state(load_doc("test2_자문수당/기안문_자문회의.txt"), [load_doc("test2_자문수당/결과보고서_자문회의.docx")], guidelines)
        review.run_review(s, client)
        print_items(s)
        advisory = [i for i in s["initial"]["items"] if "자문" in i["expense_item"]]
        r.check("테스트2", "자문수당 항목이 '판단 불가'", advisory and all(i["status"] == "판단 불가" for i in advisory),
                str([(i["expense_item"], i["status"]) for i in s["initial"]["items"]]))
        # 증빙(결과보고서)에 산정 근거·재가가 이미 적혀 있으므로, AI가 이를 판단 근거에 반영했거나 질문으로 물으면 된다.
        qs = " ".join(q["question"] for q in s["questions"])
        seen = qs + " " + " ".join(i["reasoning"] for i in advisory)
        r.check("테스트2", "단가 산정 근거를 확인(판단 근거에 반영 또는 질문)", any(k in seen for k in ("산정", "단가")), seen[:300])
        r.check("테스트2", "승인·재가 절차를 확인(판단 근거에 반영 또는 질문)", any(k in seen for k in ("승인", "재가")), seen[:300])
        r.check("테스트2", "질문이 1개 이상 생성됨", bool(s["questions"]))
        if s["questions"]:
            review.generate_final(s, answer_all(s, ANSWER_T2), client)
            saved = review.save_review(s)
            r.check("테스트2", "사례 저장됨", bool(saved.get("saved_ids")), str(saved))
        return summarize_state(s)

    # 테스트 3: 유사 자문수당 → 사례 #1 선택, 과거 원본(5/15 회의, 5/18 재가) 반영
    def t3():
        s = review.new_state(load_doc("test3_유사자문수당/지출결의서_자문수당.txt"), [], guidelines)
        review.run_review(s, client)
        print_items(s)
        print(f"  선택된 사례: {[c['id'] for c in s['past_cases']]}")
        r.check("테스트3", "사례 #1 선택", 1 in [c["id"] for c in s["past_cases"]], str(s["selection"]))
        text = " ".join(q["question"] + " " + q["past_case_comparison"] for q in s["questions"])
        r.check("테스트3", "질문에 과거 사례 언급", any(k in text for k in ("#1", "사례 1", "과거")), text[:200])
        r.check("테스트3", "과거 회의·재가 내용 반영(5/15, 5/18 또는 재가)",
                any(k in text for k in ("15일", "5/15", "5. 15", "18일", "5/18", "5. 18", "재가")), text[:200])
        r.check("테스트3", "50만 원 초과·위원회 심의 언급", any(k in text for k in ("50만", "500,000", "위원회", "심의")), text[:200])
        return summarize_state(s)

    # 테스트 6: 잘못된 과거 사례(상품권)가 있어도 위반 가능성 유지
    def t6():
        s1 = review.new_state(load_doc("test6_잘못된과거사례/1차_지출결의서_상품권.txt"), [], guidelines)
        review.run_review(s1, client)
        print("  [1차 문서]")
        print_items(s1)
        r.check("테스트6", "1차: 상품권 항목이 '위반 가능성'", any(i["status"] == "위반 가능성" for i in s1["initial"]["items"]),
                str([(i["expense_item"], i["status"]) for i in s1["initial"]["items"]]))
        if s1["questions"]:
            review.generate_final(s1, answer_all(s1, ANSWER_T6_1), client)
            review.save_review(s1)
        s2 = review.new_state(load_doc("test6_잘못된과거사례/2차_지출결의서_상품권.txt"), [], guidelines)
        review.run_review(s2, client)
        print("  [2차 문서]")
        print_items(s2)
        gift = [i for i in s2["initial"]["items"] if "상품권" in i["expense_item"] or "기념품" in i["expense_item"]] or s2["initial"]["items"]
        r.check("테스트6", "2차 최초 검토: '위반 가능성'", all(i["status"] == "위반 가능성" for i in gift),
                str([(i["expense_item"], i["status"]) for i in gift]))
        if s2["questions"]:
            review.generate_final(s2, answer_all(s2, ANSWER_T6_2), client)
            finals = {f["item_no"]: f for f in s2["final"]["result"]["items"]}
            got = [(i["expense_item"], finals[i["item_no"]]["final_status"]) for i in gift if i["item_no"] in finals]
            for _, st in got:
                print(f"  최종 판정: {st}")
            r.check("테스트6", "2차 답변 반영 후에도 '위반 가능성' 유지 (가장 중요)", got and all(st == "위반 가능성" for _, st in got), str(got))
        return {"first": summarize_state(s1), "second": summarize_state(s2)}

    r.run("테스트1", "정상 집행", t1)
    r.run("테스트2", "지침에 없는 자문수당", t2)
    r.run("테스트3", "과거 사례 재활용", t3)
    r.run("테스트6", "잘못된 과거 사례", t6)

    pin, pout, usd = r.cost(client.usage_log)
    secs = sum(x["seconds"] for x in client.usage_log)
    failed = [c for c in r.checks if not c[2]]
    print("\n=== 요약 ===")
    print(f"확인 항목 {len(r.checks)}개 중 통과 {len(r.checks) - len(failed)}개, 확인 필요 {len(failed)}개")
    for t, label, _, detail in failed:
        print(f"  - {t} / {label}: {detail[:300]}")
    print(f"AI 호출 {len(client.usage_log)}회 · AI 대기 합계 {secs:.0f}초 · 입력 {pin:,} / 출력 {pout:,} 토큰 · {fmt_usd(usd)}")
    by_task: dict[str, list] = {}
    for x in client.usage_log:
        by_task.setdefault(x["task"], []).append(x)
    for task, xs in by_task.items():
        print(f"  {task}: {len(xs)}회, 평균 {sum(x['seconds'] for x in xs) / len(xs):.0f}초, "
              f"평균 입력 {sum(x['prompt_tokens'] for x in xs) // len(xs):,} 토큰")

    r.report["summary"] = {"model": config.OPENAI_MODEL, "checks": r.checks, "prompt_tokens": pin,
                           "completion_tokens": pout, "usd": usd, "ai_seconds": round(secs, 1)}
    (out_dir / "report.json").write_text(json.dumps(r.report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(f"\n상세 결과: {out_dir / 'report.json'}")
    print("※ '확인 필요'는 키워드 기준 자동 판정입니다. report.json 또는 위 질문 문장을 직접 읽고 최종 판단하세요.")
    return 1 if failed else 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
