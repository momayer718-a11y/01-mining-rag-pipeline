from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

from pipeline.ingest import run_ingest
from pipeline.store import LocalVectorStore
from serve.intent import QueryIntent, parse_intent, preferred_source_types, terms_for
from serve.model_client import complete_json, model_metadata


def ensure_index(index_dir: str = "data/runtime") -> None:
    if not (Path(index_dir) / "chunks.jsonl").exists():
        run_ingest(out=index_dir, per_source=200, fixture=True)


def query(question: str, top_k: int = 5, days: int | None = None, index_dir: str = "data/runtime") -> dict:
    started = time.perf_counter()
    ensure_index(index_dir)
    intent = parse_intent(question, default_days=days)
    candidate_k = max(top_k * 8, 30)
    store = LocalVectorStore(index_dir)
    raw_hits = store.search(question, top_k=candidate_k, days=intent.days)
    raw_hits.extend(_supplemental_hits(store, intent, top_k, intent.days))
    filtered_hits, warnings = _filter_evidence(question, raw_hits, intent, top_k)
    citations = _build_citations(filtered_hits, intent)
    status = _status(intent, citations, warnings)
    data_quality = _data_quality(citations, warnings)
    answer_result = _compose_answer(question, intent, citations, status, warnings)
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    return {
        "status": status,
        "question": question,
        "intent": intent.to_dict(),
        "answer": answer_result["answer"],
        "answer_points": answer_result["answer_points"],
        "citations": citations,
        "top_k": top_k,
        "hits": filtered_hits,
        "warnings": warnings,
        "source_mode": _source_mode(filtered_hits),
        "data_quality": data_quality,
        "elapsed_ms": elapsed_ms,
        **answer_result["model"],
    }


def _filter_evidence(question: str, hits: list[dict], intent: QueryIntent, top_k: int) -> tuple[list[dict], list[str]]:
    warnings: list[str] = []
    if intent.missing_dimensions:
        warnings.append("unsupported_or_missing_source: " + ", ".join(intent.missing_dimensions))
    preferred = preferred_source_types(intent)
    enriched: list[dict] = []
    seen_urls: set[str] = set()
    for hit in hits:
        meta = hit["chunk"]["metadata"]
        url = meta.get("url", "")
        if url in seen_urls:
            continue
        seen_urls.add(url)
        relevance, matched_terms = _evidence_score(question, hit, intent)
        source_type = meta.get("source_type", "unknown")
        if relevance <= 0:
            continue
        row = dict(hit)
        row["evidence_relevance"] = relevance
        row["matched_terms"] = matched_terms
        row["source_reliability"] = _source_reliability(meta)
        row["source_priority"] = preferred.index(source_type) if source_type in preferred else len(preferred)
        enriched.append(row)
    enriched.sort(key=lambda row: (row["source_priority"], -row["evidence_relevance"], -row.get("score", 0)))
    selected = enriched[:top_k]
    source_types = {row["chunk"]["metadata"].get("source_type") for row in selected}
    if not selected:
        warnings.append("no_relevant_evidence_above_threshold")
    if intent.intent == "price" and "price" not in source_types and intent.coverage_status == "supported":
        warnings.append("direct_price_evidence_not_found")
    if intent.intent == "policy" and "policy" not in source_types and intent.coverage_status == "supported":
        warnings.append("direct_policy_evidence_not_found")
    if intent.missing_dimensions:
        return [], warnings
    return selected, warnings


def _supplemental_hits(store: LocalVectorStore, intent: QueryIntent, top_k: int, days: int | None) -> list[dict]:
    queries = []
    if intent.intent == "price":
        price_queries = {
            "lithium": "SHFE lithium carbonate price trend",
            "copper": "LME copper price trend inventories",
            "nickel": "LME nickel price trend Indonesian supply",
            "zinc": "LME zinc price trend mine supply",
            "iron ore": "Mysteel iron ore price trend blast furnace",
            "rare earth": "rare earth price trend quota policy",
        }
        queries.append(price_queries.get(intent.commodity or "", f"{intent.commodity or ''} price trend"))
    if intent.intent == "policy":
        queries.append(f"{intent.region or ''} {intent.commodity or ''} critical minerals policy permitting quota traceability")
    if intent.intent == "supply_risk":
        queries.append(f"{intent.region or ''} {intent.commodity or ''} supply risk shipments maintenance inventory")
    rows: list[dict] = []
    for query_text in queries:
        rows.extend(store.search(query_text.strip(), top_k=top_k * 4, days=days))
    return rows


def _evidence_score(question: str, hit: dict, intent: QueryIntent) -> tuple[float, list[str]]:
    text = f"{hit['chunk']['metadata'].get('title', '')} {hit['chunk'].get('text', '')}".lower()
    query_terms = terms_for(intent)
    matched = [term for term in query_terms if term.lower() in text]
    source_type = hit["chunk"]["metadata"].get("source_type")
    required = 1
    if intent.commodity:
        required += 1
    if intent.region:
        required += 1
    base = min(1.0, len(set(matched)) / max(required, 1))
    if source_type == preferred_source_types(intent)[0]:
        base += 0.35
    if intent.commodity and intent.commodity in hit["chunk"]["metadata"].get("commodity", ""):
        base += 0.25
    return round(base, 3), sorted(set(matched))


def _build_citations(hits: list[dict], intent: QueryIntent) -> list[dict]:
    citations = []
    for idx, hit in enumerate(hits, start=1):
        chunk = hit["chunk"]
        meta = chunk["metadata"]
        excerpt = _best_excerpt(chunk["text"], intent)
        citations.append(
            {
                "id": idx,
                "title": meta.get("title", "Untitled source"),
                "matched_excerpt_en": excerpt,
                "summary_zh": _summarize_excerpt_zh(excerpt, intent, meta),
                "url": meta.get("url", ""),
                "source_type": meta.get("source_type", "unknown"),
                "published_at": meta.get("published_at", ""),
                "source": meta.get("source", ""),
            }
        )
    return citations


def _compose_answer(question: str, intent: QueryIntent, citations: list[dict], status: str, warnings: list[str]) -> dict:
    model = model_metadata()
    if status in {"ok", "limited"} and citations:
        model_payload = complete_json(
            "你是矿业行业 RAG 问答助手。只允许基于 citations 回答中文。每个关键判断后必须使用 [数字] 引用。输出 JSON: answer:string, answer_points:list。",
            {
                "question": question,
                "intent": intent.to_dict(),
                "status": status,
                "warnings": warnings,
                "citations": citations,
            },
        )
        if _valid_model_answer(model_payload, citations):
            return {"answer": model_payload["answer"], "answer_points": model_payload["answer_points"], "model": model}
    answer, points = _fallback_answer(question, intent, citations, status, warnings)
    return {"answer": answer, "answer_points": points, "model": model}


def _fallback_answer(question: str, intent: QueryIntent, citations: list[dict], status: str, warnings: list[str]) -> tuple[str, list[dict]]:
    if not citations:
        reason = "；".join(warnings) or "没有检索到足够相关的来源"
        answer = (
            f"结论：当前证据不足，不能可靠回答“{question}”。\n"
            f"关键依据：已检索数据中缺少直接支持该问题的来源。\n"
            f"风险/限制：{reason}。\n"
            "下一步建议：补充对应矿种、地区和问题类型的一手新闻、政策或价格数据后重新检索。"
        )
        return answer, [
            {"text": "当前证据不足，不能可靠作答。", "citation_ids": [], "confidence": "low"},
            {"text": reason, "citation_ids": [], "confidence": "low"},
        ]
    ids = [row["id"] for row in citations]
    primary = citations[0]
    direct_gap = _direct_gap(intent, warnings)
    conclusion = _conclusion(intent, citations, direct_gap)
    basis = _basis(intent, citations)
    risk = _risk(intent, warnings)
    next_step = _next_step(intent, warnings)
    answer = (
        f"结论：{conclusion} {_cite(ids[: min(2, len(ids))])}\n"
        f"关键依据：{basis} {_cite([primary['id']])}\n"
        f"风险/限制：{risk} {_cite([ids[-1]])}\n"
        f"下一步建议：{next_step}"
    )
    return answer, [
        {"text": conclusion, "citation_ids": ids[: min(2, len(ids))], "confidence": "medium" if status == "ok" else "low"},
        {"text": basis, "citation_ids": [primary["id"]], "confidence": "medium"},
        {"text": risk, "citation_ids": [ids[-1]], "confidence": "low" if warnings else "medium"},
        {"text": next_step, "citation_ids": [], "confidence": "medium"},
    ]


def _conclusion(intent: QueryIntent, citations: list[dict], direct_gap: str | None) -> str:
    subject = _subject(intent)
    if direct_gap:
        return f"关于{subject}，当前资料只能给出间接判断：{direct_gap}"
    if intent.intent == "price":
        return f"{subject}的价格判断应以价格源为主；当前证据显示价格方向与库存、需求预期或供应扰动相关"
    if intent.intent == "policy":
        return f"{subject}的政策变化重点在审批、融资、配额、追溯或下游加工要求，而不是单一事件"
    if intent.intent == "supply_risk":
        return f"{subject}的主要风险集中在出货、库存、维护、社区/水资源或政策约束"
    if intent.intent == "investment":
        return f"{subject}的投资判断需要同时看政策约束、价格方向和供应链执行风险"
    return f"{subject}目前可从新闻、政策和价格资料中形成方向性判断"


def _basis(intent: QueryIntent, citations: list[dict]) -> str:
    source_labels = "、".join(_source_type_label(row["source_type"]) for row in citations[:3])
    return f"已检索到的{source_labels}来源中，最直接的证据来自“{citations[0]['title']}”，其原文段落说明了与问题相关的市场或政策背景"


def _risk(intent: QueryIntent, warnings: list[str]) -> str:
    if warnings:
        return "；".join(warnings)
    if intent.intent == "price":
        return "价格源为样例缓存，正式投资判断仍需替换为授权行情源"
    return "当前为公开源和样例缓存结果，仍需结合一手公告、交易所/价格授权源和人工复核"


def _next_step(intent: QueryIntent, warnings: list[str]) -> str:
    if intent.intent == "price":
        return "优先补充授权价格源，并按同一日期窗口复核趋势。"
    if intent.intent == "policy":
        return "补充监管原文、政策发布日期和项目所在地要求后再做正式判断。"
    if warnings:
        return "先补齐缺失地区或矿种的数据源，再重新运行问答。"
    return "把引用来源作为审计入口，继续核对原文与最新市场数据。"


def _direct_gap(intent: QueryIntent, warnings: list[str]) -> str | None:
    if "direct_price_evidence_not_found" in warnings:
        return "未检索到直接价格证据，不能仅凭政策或新闻判断价格变化"
    if "direct_policy_evidence_not_found" in warnings:
        return "未检索到直接政策证据，不能仅凭新闻或价格判断政策变化"
    return None


def _status(intent: QueryIntent, citations: list[dict], warnings: list[str]) -> str:
    if intent.missing_dimensions or not citations:
        return "abstain"
    if any(warning.startswith("direct_") for warning in warnings):
        return "limited"
    return "ok"


def _data_quality(citations: list[dict], warnings: list[str]) -> dict:
    return {
        "grade": "usable" if citations and not warnings else "limited" if citations else "insufficient",
        "evidence_count": len(citations),
        "warning_count": len(warnings),
    }


def _source_mode(hits: list[dict]) -> str:
    modes = {hit["chunk"]["metadata"].get("source_mode", "unknown") for hit in hits}
    return ",".join(sorted(modes)) if modes else "none"


def _best_excerpt(text: str, intent: QueryIntent) -> str:
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    terms = [term.lower() for term in terms_for(intent)]
    best = max(sentences, key=lambda sentence: sum(1 for term in terms if term in sentence.lower()), default=text)
    if len(best) < 80 and len(sentences) > 1:
        idx = sentences.index(best)
        best = " ".join(sentences[idx : idx + 2])
    return best[:520].strip()


def _summarize_excerpt_zh(excerpt: str, intent: QueryIntent, meta: dict) -> str:
    subject = _subject(intent)
    source_type = _source_type_label(meta.get("source_type", "unknown"))
    if intent.intent == "price":
        return f"该{source_type}证据用于判断{subject}的价格方向、库存/需求或供应扰动。"
    if intent.intent == "policy":
        return f"该{source_type}证据说明{subject}相关政策、审批、配额或下游加工要求。"
    if intent.intent == "supply_risk":
        return f"该{source_type}证据说明{subject}面临的供应、出货、维护或合规风险。"
    return f"该{source_type}证据为{subject}问题提供背景和可追溯依据。"


def _source_reliability(meta: dict) -> str:
    mode = meta.get("source_mode", "")
    if mode.startswith("real"):
        return "high"
    if "fixture" in mode:
        return "demo_fixture"
    return "unknown"


def _source_type_label(source_type: str | None) -> str:
    return {"news": "新闻", "policy": "政策", "price": "价格"}.get(source_type or "", "资料")


def _subject(intent: QueryIntent) -> str:
    commodity = {
        "lithium": "锂",
        "copper": "铜",
        "nickel": "镍",
        "zinc": "锌",
        "iron ore": "铁矿石",
        "rare earth": "稀土",
        "cobalt": "钴",
    }.get(intent.commodity or "", intent.commodity or "该矿业主题")
    region = {
        "australia": "澳洲",
        "pilbara": "Pilbara",
        "china": "中国",
        "indonesia": "印尼",
        "peru": "秘鲁",
        "drc": "刚果/DRC",
        "chile": "智利",
    }.get(intent.region or "", intent.region or "")
    return f"{region}{commodity}" if region else commodity


def _cite(ids: list[int]) -> str:
    return "".join(f"[{id_}]" for id_ in ids)


def _valid_model_answer(payload: dict | None, citations: list[dict]) -> bool:
    if not isinstance(payload, dict):
        return False
    answer = payload.get("answer")
    points = payload.get("answer_points")
    citation_ids = {row["id"] for row in citations}
    if not isinstance(answer, str) or not isinstance(points, list):
        return False
    used = {int(match) for match in re.findall(r"\[(\d+)\]", answer)}
    return bool(used) and used.issubset(citation_ids)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("question")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--days", type=int)
    parser.add_argument("--index-dir", default="data/runtime")
    args = parser.parse_args()
    print(json.dumps(query(args.question, args.top_k, args.days, args.index_dir), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
