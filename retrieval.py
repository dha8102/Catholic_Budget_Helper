"""과거 사례 검색: 키워드 검색과 임베딩(의미) 검색을 각각 실행하고, 두 결과를 합쳐 상위 K건을 고른다.

- 키워드: 사례를 저장할 때 검토 결과에서 뽑은 키워드를 함께 저장해 두고(case_index 테이블),
  신규 문서 검토 때 1단계(지침 검토)에서 AI가 뽑은 키워드와 비교한다. 띄어쓰기·부분 일치·글자 유사도까지 본다.
- 임베딩: 사례 내용을 OpenAI 임베딩 벡터로 바꿔 저장해 두고, 신규 문서의 확인 필요 항목과 코사인 유사도를 비교한다.
- 합치기: 두 순위를 RRF(Reciprocal Rank Fusion)로 합쳐 상위 K건을 AI 선택(2단계) 후보로 넘긴다.
  두 검색의 점수 척도가 달라도 순위만으로 공정하게 합칠 수 있다.
"""
import hashlib
import re

import numpy as np

import ai
import config
import storage

RRF_K = 60  # RRF 표준 상수: 순위가 낮아질수록 점수가 완만하게 줄어든다
KEYWORD_BATCH = 30  # 키워드 없는 옛 사례를 AI로 처리할 때 한 번에 보낼 사례 수


# ---------------------------------------------------------------- 키워드 검색
def _norm(word: str) -> str:
    return re.sub(r"[\s\W_]+", "", (word or "").lower())


def _bigrams(s: str) -> set[str]:
    return {s[i:i + 2] for i in range(len(s) - 1)} or {s}


def keyword_similarity(a: str, b: str) -> float:
    """두 키워드의 유사도(0~1): 같으면 1, 한쪽이 다른 쪽을 포함하면 0.7, 글자 2개 묶음이 많이 겹치면 최대 0.6."""
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if len(a) >= 2 and len(b) >= 2 and (a in b or b in a):
        return 0.7
    ba, bb = _bigrams(a), _bigrams(b)
    dice = 2 * len(ba & bb) / (len(ba) + len(bb))
    return round(dice * 0.6, 3) if dice >= 0.4 else 0.0


def case_keywords(case: dict, index: dict) -> list[str]:
    """사례의 검색 키워드 = 저장된 키워드 + 집행항목명."""
    kws = list(index.get(case["id"], {}).get("keywords") or [])
    return kws + [case["expense_item"]] if case.get("expense_item") else kws


def query_keywords(flagged_items: list[dict]) -> list[str]:
    out = []
    for item in flagged_items:
        for k in list(item.get("keywords") or []) + [item["expense_item"]]:
            if _norm(k) and _norm(k) not in {_norm(x) for x in out}:
                out.append(k)
    return out


def keyword_search(query: list[str], cases: list[dict], index: dict, top_k: int) -> list[dict]:
    """반환: [{"case_id", "score", "matched": [일치한 검색어]}] 점수 높은 순"""
    hits = []
    for c in cases:
        ckws = case_keywords(c, index)
        score, matched = 0.0, []
        for q in query:
            best = max((keyword_similarity(q, k) for k in ckws), default=0.0)
            if best > 0:
                score += best
                matched.append(q)
        if score > 0:
            hits.append({"case_id": c["id"], "score": round(score, 2), "matched": matched})
    hits.sort(key=lambda h: (-h["score"], -h["case_id"]))  # 동점이면 최신 사례 우선
    return hits[:top_k]


# ---------------------------------------------------------------- 임베딩 검색
def case_text(case: dict, keywords: list[str]) -> str:
    """사례를 임베딩할 때 쓰는 글. 사람이 검토 결과를 고치면 내용이 바뀌어 자동으로 다시 임베딩된다."""
    amount = f"{case['amount']:,}원" if isinstance(case.get("amount"), int) else "금액 미기재"
    return "\n".join([
        f"사업명: {case.get('business_name') or ''}",
        f"집행항목: {case.get('expense_item') or ''} ({amount})",
        "키워드: " + ", ".join(keywords),
        f"질문: {case.get('ai_question') or ''}",
        f"답변: {case.get('user_answer') or ''}",
        f"검토 결과: {(case.get('review_result') or '')[:1500]}",
    ])


def item_query_text(business_name: str, item: dict) -> str:
    """신규 문서의 확인 필요 항목 1개를 검색어 글로 만든다."""
    amount = f"{item['amount']:,}원" if isinstance(item.get("amount"), int) else "금액 미기재"
    return "\n".join([
        f"사업명: {business_name}",
        f"집행항목: {item['expense_item']} ({amount})",
        "키워드: " + ", ".join(item.get("keywords") or []),
        f"집행 목적: {item.get('purpose') or ''}",
        f"검토 상태: {item['status']} - {item.get('reasoning') or ''}",
        "확인 필요: " + " / ".join(item.get("unclear_points") or []),
    ])


def _source_hash(text: str) -> str:
    return hashlib.sha1(f"{config.EMBEDDING_MODEL}\n{text}".encode("utf-8")).hexdigest()


def _unit(m: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(m, axis=-1, keepdims=True)
    return m / np.where(norms == 0, 1, norms)


def embedding_search(query_vectors: list[list[float]], cases: list[dict], index: dict, top_k: int) -> list[dict]:
    """반환: [{"case_id", "score"(코사인 유사도, 항목 여러 개면 가장 높은 값)}] 점수 높은 순"""
    usable = [c for c in cases if index.get(c["id"], {}).get("embedding") and index[c["id"]].get("embedding_model") == config.EMBEDDING_MODEL]
    if not usable or not query_vectors:
        return []
    matrix = _unit(np.stack([np.frombuffer(index[c["id"]]["embedding"], dtype=np.float32) for c in usable]))
    queries = _unit(np.asarray(query_vectors, dtype=np.float32))
    scores = (queries @ matrix.T).max(axis=0)
    hits = [{"case_id": c["id"], "score": round(float(s), 3)} for c, s in zip(usable, scores)]
    hits.sort(key=lambda h: (-h["score"], -h["case_id"]))
    return hits[:top_k]


# ---------------------------------------------------------------- 색인 관리
def ensure_keywords(cases: list[dict], index: dict, client, warnings: list[str]) -> dict:
    """키워드 없이 저장된 사례(이 기능 도입 전 사례 등)는 AI로 키워드를 뽑아 저장한다.
    실패하면 집행항목명만으로 키워드 검색을 하고, 다음 검토 때 다시 시도한다."""
    missing = [c for c in cases if not index.get(c["id"], {}).get("keywords")]
    for i in range(0, len(missing), KEYWORD_BATCH):
        chunk = missing[i:i + KEYWORD_BATCH]
        recs = [{"case_id": c["id"], "business_name": c["business_name"], "expense_item": c["expense_item"],
                 "ai_question": c["ai_question"], "user_answer": c["user_answer"],
                 "review_result": (c["review_result"] or "")[:500]} for c in chunk]
        try:
            data = client.complete_json("case_keywords", ai.SYSTEM_PROMPT, ai.build_case_keywords_prompt(recs))
            got = ai.normalize_case_keywords(data, {c["id"] for c in chunk})
        except ai.AIError as e:
            warnings.append(f"키워드가 없는 과거 사례 {len(missing) - i}건의 키워드를 만들지 못해 집행항목명으로만 키워드 검색했습니다: {e}")
            break
        for cid, kws in got.items():
            storage.set_case_keywords(cid, kws)
            index.setdefault(cid, {})["keywords"] = kws
    return index


def ensure_embeddings(cases: list[dict], index: dict, client) -> dict:
    """임베딩이 없거나, 사례 내용·임베딩 모델이 바뀐 사례만 새로 임베딩해 저장한다. 실패하면 AIError."""
    need = []
    for c in cases:
        text = case_text(c, case_keywords(c, index))
        entry = index.get(c["id"], {})
        if not entry.get("embedding") or entry.get("embedding_model") != config.EMBEDDING_MODEL or entry.get("embedding_source") != _source_hash(text):
            need.append((c["id"], text))
    if need:
        vectors = client.embed([t for _, t in need])
        for (cid, text), vec in zip(need, vectors):
            blob = np.asarray(vec, dtype=np.float32).tobytes()
            storage.set_case_embedding(cid, blob, config.EMBEDDING_MODEL, _source_hash(text))
            index.setdefault(cid, {}).update(embedding=blob, embedding_model=config.EMBEDDING_MODEL, embedding_source=_source_hash(text))
    return index


def index_cases(case_ids: list[int], client) -> None:
    """사례를 저장한 직후 검색 색인(키워드·임베딩)을 만든다. 실패하면 AIError (다음 검토 때 자동으로 다시 만든다)."""
    cases = storage.get_cases(case_ids)
    warnings: list[str] = []
    index = ensure_keywords(cases, storage.get_case_index(), client, warnings)
    ensure_embeddings(cases, index, client)
    if warnings:
        raise ai.AIError(warnings[0])


# ---------------------------------------------------------------- 검색 (키워드 + 임베딩 → 상위 K)
def fuse(keyword_hits: list[dict], embedding_hits: list[dict], top_k: int) -> list[dict]:
    """두 검색 순위를 RRF로 합친다. 반환: [{"case_id", "rrf", "keyword_rank", "embedding_rank"}]"""
    merged: dict[int, dict] = {}
    for source, hits in (("keyword", keyword_hits), ("embedding", embedding_hits)):
        for rank, h in enumerate(hits, 1):
            m = merged.setdefault(h["case_id"], {"case_id": h["case_id"], "rrf": 0.0, "keyword_rank": None, "embedding_rank": None})
            m["rrf"] += 1 / (RRF_K + rank)
            m[f"{source}_rank"] = rank
    out = sorted(merged.values(), key=lambda m: (-m["rrf"], -m["case_id"]))
    for m in out:
        m["rrf"] = round(m["rrf"], 5)
    return out[:top_k]


def search_cases(business_name: str, flagged_items: list[dict], cases: list[dict], client, top_k: int) -> dict:
    """확인 필요 항목과 비슷한 과거 사례를 찾는다.
    반환: {"candidates": [사례 dict + 검색 정보], "keywords", "keyword_hits", "embedding_hits", "top_k", "total", "warnings"}"""
    warnings: list[str] = []
    index = ensure_keywords(cases, storage.get_case_index(), client, warnings)
    query = query_keywords(flagged_items)
    kw_hits = keyword_search(query, cases, index, top_k)
    emb_hits: list[dict] = []
    try:
        index = ensure_embeddings(cases, index, client)
        vectors = client.embed([item_query_text(business_name, i) for i in flagged_items])
        emb_hits = embedding_search(vectors, cases, index, top_k)
    except ai.AIError as e:
        warnings.append(f"임베딩 검색을 사용하지 못해 키워드 검색 결과만으로 과거 사례 후보를 골랐습니다: {e}")

    by_id = {c["id"]: c for c in cases}
    kw_info = {h["case_id"]: h for h in kw_hits}
    emb_info = {h["case_id"]: h for h in emb_hits}
    candidates = []
    for f in fuse(kw_hits, emb_hits, top_k):
        cid = f["case_id"]
        candidates.append({
            **by_id[cid],
            "keywords": case_keywords(by_id[cid], index),
            "search": {**f, "keyword_score": kw_info.get(cid, {}).get("score"), "matched": kw_info.get(cid, {}).get("matched", []),
                       "embedding_score": emb_info.get(cid, {}).get("score")},
        })
    return {"candidates": candidates, "keywords": query, "keyword_hits": kw_hits, "embedding_hits": emb_hits,
            "top_k": top_k, "total": len(cases), "warnings": warnings}
