from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

from pipeline.ingest import run_ingest
from pipeline.store import LocalVectorStore
from serve.intent import COMMODITY_TERMS, REGION_TERMS, QueryIntent, parse_intent, preferred_source_types, terms_for
from serve.model_client import complete_json, complete_json_with_diagnostics, model_metadata

DIRECT_PRICE_MODES = {"authorized_csv", "authorized_api", "public_visible_price", "price_proxy_public", "third_party_public"}


def ensure_index(index_dir: str = "data/runtime") -> None:
    chunks_path = Path(index_dir) / "chunks.jsonl"
    if not chunks_path.exists():
        run_ingest(out=index_dir, per_source=20, fixture=False)


def query(question: str, top_k: int = 5, days: int | None = None, index_dir: str = "data/runtime", enhance: bool = False) -> dict:
    started = time.perf_counter()
    ensure_index(index_dir)
    intent = parse_intent(question, default_days=days)
    candidate_k = max(top_k * 12, 80)
    store = LocalVectorStore(index_dir)
    retrieval_trace = _new_retrieval_trace(question, top_k, days, intent, candidate_k)
    search_days = _search_days(intent)
    raw_hits = store.search(question, top_k=candidate_k, days=search_days)
    _trace_search(retrieval_trace, "primary", question, raw_hits)
    supplemental_hits = _supplemental_hits(store, intent, top_k, search_days, retrieval_trace, question)
    raw_hits.extend(supplemental_hits)
    filtered_hits, warnings = _filter_evidence(question, raw_hits, intent, top_k, retrieval_trace)
    citations = _build_citations(filtered_hits, intent)
    status = _status(intent, citations, warnings)
    data_quality = _data_quality(citations, warnings)
    answer_result = _compose_answer(question, intent, citations, status, warnings, enhance=enhance)
    retrieval_trace["model_called"] = answer_result.get("model_called", False)
    retrieval_trace["model_payload_valid"] = answer_result.get("model_payload_valid", False)
    retrieval_trace["model_attempted"] = answer_result.get("model_attempted", False)
    retrieval_trace["model_completed"] = answer_result.get("model_completed", False)
    retrieval_trace["model_elapsed_ms"] = answer_result.get("model_elapsed_ms", 0)
    retrieval_trace["model_error_type"] = answer_result.get("model_error_type", "")
    retrieval_trace["model_timeout_seconds"] = answer_result.get("model_timeout_seconds")
    retrieval_trace["answer_stage"] = answer_result.get("answer_stage", "fast_answer")
    retrieval_trace["selected_hit_count"] = len(filtered_hits)
    retrieval_trace["citation_count"] = len(citations)
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    return {
        "status": status,
        "question": question,
        "intent": intent.to_dict(),
        "answer": answer_result["answer"],
        "fast_answer": answer_result["fast_answer"],
        "model_answer": answer_result.get("model_answer"),
        "model_status": answer_result.get("model_status", "not_requested"),
        "answer_stage": answer_result.get("answer_stage", "fast_answer"),
        "answer_points": answer_result["answer_points"],
        "citations": citations,
        "top_k": top_k,
        "hits": filtered_hits,
        "warnings": warnings,
        "source_mode": _source_mode(filtered_hits),
        "data_quality": data_quality,
        "elapsed_ms": elapsed_ms,
        "retrieval_trace": retrieval_trace,
        **answer_result["model"],
    }


def _filter_evidence(question: str, hits: list[dict], intent: QueryIntent, top_k: int, trace: dict | None = None) -> tuple[list[dict], list[str]]:
    warnings: list[str] = []
    if intent.missing_dimensions:
        warnings.append("unsupported_or_missing_source: " + ", ".join(intent.missing_dimensions))
    preferred = preferred_source_types(intent)
    enriched: list[dict] = []
    fixture_enriched: list[dict] = []
    url_counts: dict[str, int] = {}
    seen_chunks: set[str] = set()
    limited_sources: set[str] = set()
    fixture_hits = 0
    explicit_fixture = _explicit_fixture_index(hits)
    used_fixture_answer = False
    for hit in hits:
        meta = hit["chunk"]["metadata"]
        url = meta.get("url", "")
        mode = meta.get("source_mode", "")
        chunk_id = hit["chunk"].get("chunk_id", "")
        if _is_fixture_source(meta):
            fixture_hits += 1
            if explicit_fixture:
                relevance, matched_terms = _evidence_score(question, hit, intent)
                if relevance > 0:
                    row = dict(hit)
                    row["evidence_relevance"] = relevance
                    row["matched_terms"] = matched_terms
                    row["source_reliability"] = "demo_fixture"
                    row["source_priority"] = preferred.index(meta.get("source_type", "unknown")) if meta.get("source_type") in preferred else len(preferred)
                    fixture_enriched.append(row)
            continue
        if meta.get("evidence_kind") in {"source_status", "source_discovery"} or mode == "source_limited":
            limited_sources.add(meta.get("source", "unknown"))
            _trace_drop(trace, hit, "source_limited_or_status")
            continue
        if chunk_id in seen_chunks:
            _trace_drop(trace, hit, "duplicate_chunk")
            continue
        seen_chunks.add(chunk_id)
        if _low_quality_evidence(hit["chunk"].get("text", ""), meta):
            _trace_drop(trace, hit, "low_quality_reference_or_table_chunk")
            continue
        if url_counts.get(url, 0) >= _max_chunks_per_url(intent):
            _trace_drop(trace, hit, "url_chunk_limit")
            continue
        relevance, matched_terms = _evidence_score(question, hit, intent)
        source_type = meta.get("source_type", "unknown")
        if relevance <= 0:
            _trace_drop(trace, hit, "dimension_or_relevance_mismatch")
            continue
        row = dict(hit)
        row["evidence_relevance"] = relevance
        row["matched_terms"] = matched_terms
        row["source_reliability"] = _source_reliability(meta)
        row["source_priority"] = preferred.index(source_type) if source_type in preferred else len(preferred)
        row["directness"] = _evidence_directness(question, row, intent)
        row["selection_reason"] = _selection_reason(row, intent)
        row["rerank_score"] = _rerank_score(question, row, intent)
        enriched.append(row)
        url_counts[url] = url_counts.get(url, 0) + 1
    enriched.sort(key=lambda row: (row["source_priority"], -row.get("rerank_score", 0), -row["evidence_relevance"], -row.get("score", 0)))
    evidence_limit = _evidence_limit(intent, enriched, top_k)
    selected = enriched[:evidence_limit]
    _trace_rerank(trace, selected)
    if not selected and fixture_enriched:
        fixture_enriched.sort(key=lambda row: (row["source_priority"], -row["evidence_relevance"], -row.get("score", 0)))
        selected = fixture_enriched[:top_k]
        used_fixture_answer = True
        warnings.append("fixture_mode_answer: using explicit demo fixture index, not original-source evidence")
    source_types = {row["chunk"]["metadata"].get("source_type") for row in selected}
    if not selected:
        warnings.append("no_relevant_evidence_above_threshold")
    if limited_sources:
        warnings.append("source_access_limited: " + ", ".join(sorted(limited_sources)))
    if fixture_hits and not used_fixture_answer:
        warnings.append("fixture_sources_excluded_from_business_answer")
    if intent.intent == "price" and "price" not in source_types and intent.coverage_status == "supported":
        warnings.append("direct_price_evidence_not_found")
    elif intent.intent == "price" and intent.coverage_status == "supported":
        price_modes = {
            row["chunk"]["metadata"].get("source_mode", "")
            for row in selected
            if row["chunk"]["metadata"].get("source_type") == "price"
        }
        if price_modes and not price_modes.intersection(DIRECT_PRICE_MODES):
            warnings.append("direct_price_evidence_not_found")
    if intent.intent == "policy" and "policy" not in source_types and intent.coverage_status == "supported":
        warnings.append("direct_policy_evidence_not_found")
    if intent.intent == "policy" and _asks_export_or_shipment(question) and selected and not _has_direct_policy_change_evidence(selected):
        warnings.append("direct_policy_change_evidence_not_found")
    if intent.intent == "news" and _asks_export_or_shipment(question) and selected and not _has_export_or_shipment_evidence(selected):
        warnings.append("direct_export_evidence_not_found")
    if intent.domain == "broad_mining" and len(selected) < min(top_k, 3):
        warnings.append("broad_query_evidence_thin")
    if intent.missing_dimensions:
        return [], warnings
    return selected, warnings


def _supplemental_hits(store: LocalVectorStore, intent: QueryIntent, top_k: int, days: int | None, trace: dict | None = None, original_question: str = "") -> list[dict]:
    queries = _planned_queries(intent)
    dimension_query = _dimension_query(intent)
    queries.extend(_targeted_query_expansion(original_question, intent))
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
        queries.append(f"{dimension_query} critical minerals policy permitting quota traceability")
    if intent.intent == "news":
        queries.extend(
            [
                f"{dimension_query} mining news update",
                f"{dimension_query} project supply market",
            ]
        )
    if intent.intent == "supply_risk":
        queries.append(f"{dimension_query} supply risk shipments maintenance inventory")
    rows: list[dict] = []
    for query_text in queries:
        clean_query = query_text.strip()
        if not clean_query:
            continue
        hits = store.search(clean_query, top_k=max(top_k * 6, 30), days=days)
        _trace_search(trace, "supplemental", clean_query, hits)
        rows.extend(hits)
    return rows


def _planned_queries(intent: QueryIntent) -> list[str]:
    if intent.domain != "broad_mining":
        return []
    region = _canonical_search_term(intent.region, REGION_TERMS) if intent.region else ""
    if intent.intent == "policy":
        return [
            f"{region} critical minerals export policy",
            f"{region} iron ore export royalty policy",
            f"{region} lithium export policy downstream processing",
            f"{region} copper nickel mining policy permitting",
            f"{region} mineral trade restrictions tariff quota",
            f"{region} resources policy permitting approvals",
            f"{region} critical minerals strategy financing processing",
            f"{region} mining news export policy change",
        ]
    return [
        f"{region} mining news critical minerals",
        f"{region} iron ore lithium copper nickel update",
        f"{region} mineral export shipments policy",
    ]


def _targeted_query_expansion(question: str, intent: QueryIntent) -> list[str]:
    lowered = question.lower()
    queries: list[str] = []
    if "rare earth" in lowered or "稀土" in question:
        queries.extend(
            [
                "China rare earth quota traceability export controls policy regulatory supervision",
                "rare earth export control policy quota traceability China",
                "china-rare-earth rare earth regulatory supervision quota",
            ]
        )
    if "pilbara" in lowered or "皮尔巴拉" in question:
        queries.extend(
            [
                "Pilbara Minerals spodumene shipment port logistics concentrate offtake",
                "Pilbara lithium shipments port spodumene concentrate",
                "Pilbara iron ore shipments port logistics",
            ]
        )
    if intent.intent == "price":
        if intent.commodity == "zinc":
            queries.extend(["FRED zinc public price trend", "COMEX zinc futures price proxy", "zinc mine supply price trend"])
        if intent.commodity == "iron ore":
            queries.extend(["FRED iron ore public price trend", "CME iron ore futures price proxy", "Mysteel iron ore blast furnace steel mills"])
        if intent.commodity == "lithium":
            queries.extend(["SHFE lithium carbonate public price trend", "lithium carbonate price battery restocking", "lithium public price proxy"])
        if intent.commodity == "nickel":
            queries.extend(["FRED nickel public price trend", "LME nickel Indonesian supply battery destocking"])
        if intent.commodity == "copper":
            queries.extend(["FRED copper public price trend", "LME copper inventories smelter charges"])
    if _asks_export_or_shipment(question):
        queries.append(f"{_dimension_query(intent)} export shipments shipping port policy trade".strip())
    return queries


def _dimension_query(intent: QueryIntent) -> str:
    terms = []
    if intent.region:
        terms.append(_canonical_search_term(intent.region, REGION_TERMS))
    if intent.commodity:
        terms.append(_canonical_search_term(intent.commodity, COMMODITY_TERMS))
    return " ".join(terms).strip()


def _canonical_search_term(value: str, mapping: dict[str, list[str]]) -> str:
    for term in mapping.get(value, [value]):
        if term.isascii() and re.search(r"[a-zA-Z]", term):
            return term
    return value


def _evidence_score(question: str, hit: dict, intent: QueryIntent) -> tuple[float, list[str]]:
    text = f"{hit['chunk']['metadata'].get('title', '')} {hit['chunk'].get('text', '')}".lower()
    meta = hit["chunk"]["metadata"]
    if not _dimension_matches(text, meta, intent):
        return 0.0, []
    if intent.commodity and _weak_commodity_match(text, meta, intent.commodity):
        return 0.0, []
    query_terms = terms_for(intent)
    matched = [term for term in query_terms if term.lower() in text]
    source_type = meta.get("source_type")
    required = 1
    if intent.commodity:
        required += 1
    if intent.region:
        required += 1
    base = min(1.0, len(set(matched)) / max(required, 1))
    if source_type == preferred_source_types(intent)[0]:
        base += 0.35
    if intent.commodity and intent.commodity in meta.get("commodity", ""):
        base += 0.25
    if _targeted_direct_match(question, text, meta, intent):
        base += 0.45
    return round(base, 3), sorted(set(matched))


def _targeted_direct_match(question: str, text: str, meta: dict, intent: QueryIntent) -> bool:
    lowered = question.lower()
    mode = str(meta.get("source_mode", "")).lower()
    if intent.intent == "price" and meta.get("source_type") == "price" and mode in DIRECT_PRICE_MODES:
        return True
    if ("rare earth" in lowered or "稀土" in question) and meta.get("source_type") == "policy" and "rare earth" in text:
        return True
    if "pilbara" in lowered and "pilbara" in text and any(term in text for term in ("shipment", "shipments", "port", "spodumene", "concentrate", "offtake")):
        return True
    return False


def _rerank_score(question: str, row: dict, intent: QueryIntent) -> float:
    meta = row["chunk"]["metadata"]
    text = f"{meta.get('title', '')} {row['chunk'].get('text', '')}".lower()
    score = float(row.get("score", 0)) + row.get("evidence_relevance", 0) * 10
    directness = row.get("directness", "")
    if directness.startswith("direct"):
        score += 8
    if meta.get("source_type") == preferred_source_types(intent)[0]:
        score += 5
    if str(meta.get("source_mode", "")) == "third_party_public":
        score += 2
    if _targeted_direct_match(question, text, meta, intent):
        score += 10
    if intent.commodity and intent.commodity in str(meta.get("commodity", "")):
        score += 3
    if intent.region and intent.region in str(meta.get("region", "")):
        score += 3
    return round(score, 3)


def _evidence_limit(intent: QueryIntent, rows: list[dict], top_k: int) -> int:
    if not rows:
        return top_k
    if intent.domain == "broad_mining":
        return min(max(top_k, 5), 8)
    source_types = {row["chunk"]["metadata"].get("source_type") for row in rows[:top_k]}
    if intent.intent == "price" and "price" not in source_types:
        return min(top_k, 3)
    if any(row["evidence_relevance"] < 0.8 for row in rows[:top_k]):
        return min(top_k, 3)
    return top_k


def _dimension_matches(text: str, meta: dict, intent: QueryIntent) -> bool:
    if intent.commodity and not _matches_terms(text, meta, intent.commodity, COMMODITY_TERMS):
        return False
    if intent.region and not _matches_terms(text, meta, intent.region, REGION_TERMS):
        return False
    return True


def _matches_terms(text: str, meta: dict, value: str, mapping: dict[str, list[str]]) -> bool:
    meta_values = " ".join(str(meta.get(key, "")) for key in ("commodity", "region", "source", "title")).lower()
    haystack = f"{meta_values} {text}"
    if value in haystack:
        return True
    return any(term.lower() in haystack for term in mapping.get(value, [value]))


def _weak_commodity_match(text: str, meta: dict, commodity: str) -> bool:
    declared = str(meta.get("commodity", "")).lower()
    if declared == commodity:
        return False
    title = str(meta.get("title", "")).lower()
    terms = [term.lower() for term in COMMODITY_TERMS.get(commodity, [commodity])]
    title_hits = sum(title.count(term) for term in terms)
    text_hits = sum(text.count(term) for term in terms)
    if title_hits > 0:
        return False
    if str(meta.get("source_type", "")) == "news":
        return text_hits < 3
    return text_hits < 2


def _max_chunks_per_url(intent: QueryIntent) -> int:
    return 2 if intent.domain == "broad_mining" else 1


def _low_quality_evidence(text: str, meta: dict) -> bool:
    normalized = re.sub(r"\s+", " ", text.strip())
    lowered = normalized.lower()
    if not normalized:
        return True
    if len(normalized) < 70:
        return True
    reference_markers = [
        "publications office of the european union",
        "accessed 3 march",
        "ministry of mines. 2023.",
        "u.s. geological survey. 2025. about the",
        "references",
    ]
    if any(marker in lowered for marker in reference_markers):
        return True
    alpha_chars = sum(1 for char in normalized if char.isalpha())
    digit_chars = sum(1 for char in normalized if char.isdigit())
    if digit_chars > alpha_chars * 0.65 and normalized.count(" Yes ") + normalized.count(" No ") >= 4:
        return True
    if normalized.count("Yes") + normalized.count("No data") >= 8 and not _has_direct_policy_terms(lowered):
        return True
    if meta.get("source_type") == "policy" and "skip to content" in lowered and "home -->" in lowered:
        return True
    return False


def _evidence_directness(question: str, hit: dict, intent: QueryIntent) -> str:
    text = f"{hit['chunk']['metadata'].get('title', '')} {hit['chunk'].get('text', '')}".lower()
    if intent.intent == "policy":
        if _has_direct_policy_terms(text):
            return "direct_policy_or_regulatory_evidence"
        if hit["chunk"]["metadata"].get("source_type") == "policy":
            return "official_background"
        return "industry_background"
    if intent.intent == "price":
        mode = hit["chunk"]["metadata"].get("source_mode", "")
        if mode in {"authorized_csv", "authorized_api"}:
            return "direct_authorized_price"
        if mode in {"public_visible_price", "price_proxy_public"}:
            return "direct_public_visible_price"
        return "indirect_price_context"
    if intent.intent == "news":
        if _asks_export_or_shipment(question) and _has_export_or_shipment_evidence([hit]):
            return "direct_export_or_shipment_news"
        return "related_news"
    return "related_context"


def _selection_reason(hit: dict, intent: QueryIntent) -> str:
    meta = hit["chunk"]["metadata"]
    terms = ", ".join(hit.get("matched_terms", [])) or "semantic match"
    return f"{_source_type_label(meta.get('source_type'))} source; {hit.get('directness', 'related_context')}; matched {terms}"


def _has_direct_policy_terms(text: str) -> bool:
    direct_terms = (
        "export control",
        "export controls",
        "export ban",
        "export permit",
        "export licence",
        "export license",
        "export quota",
        "tariff",
        "tariffs",
        "royalty",
        "royalties",
        "quota",
        "quotas",
        "trade restriction",
        "trade restrictions",
        "permitting",
        "approvals",
        "downstream processing",
        "critical minerals strategy",
        "financing",
        "出口",
        "关税",
        "配额",
        "许可",
        "审批",
        "下游加工",
    )
    return any(term in text for term in direct_terms)


def _has_direct_policy_change_evidence(hits: list[dict]) -> bool:
    for hit in hits:
        meta = hit["chunk"]["metadata"]
        text = f"{meta.get('title', '')} {hit['chunk'].get('text', '')}".lower()
        if meta.get("source_type") == "policy" and _has_direct_policy_terms(text):
            if not _low_quality_evidence(hit["chunk"].get("text", ""), meta):
                return True
        if meta.get("source_type") == "news" and _has_direct_policy_terms(text):
            return True
    return False


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
                "summary_zh": _summarize_excerpt_zh(excerpt, intent, meta, idx),
                "url": meta.get("url", ""),
                "source_type": meta.get("source_type", "unknown"),
                "source_mode": meta.get("source_mode", "unknown"),
                "published_at": meta.get("published_at", ""),
                "source": meta.get("source", ""),
                "directness": hit.get("directness", "unknown"),
                "selection_reason": hit.get("selection_reason", ""),
            }
        )
    return citations


def _compose_answer(question: str, intent: QueryIntent, citations: list[dict], status: str, warnings: list[str], enhance: bool = False) -> dict:
    model = model_metadata()
    fast_answer, fast_points = _fallback_answer(question, intent, citations, status, warnings)
    base = {
        "answer": fast_answer,
        "fast_answer": fast_answer,
        "model_answer": None,
        "answer_points": fast_points,
        "model": model,
        "model_called": False,
        "model_attempted": False,
        "model_completed": False,
        "model_payload_valid": False,
        "model_elapsed_ms": 0,
        "model_error_type": "",
        "model_timeout_seconds": model.get("model_timeout_seconds"),
        "model_status": "not_requested",
        "answer_stage": "fast_answer",
    }
    if not enhance:
        return base
    if status in {"ok", "limited"} and citations:
        model_result = complete_json_with_diagnostics(
            (
                "你是矿业行业 RAG 问答助手。你只能基于 citations 中的原文命中段进行中文回答，"
                "不能编造 citations 之外的事实。回答要先给结论，再给关键依据、风险/限制、下一步建议；"
                "每个关键判断后必须使用 [数字] 引用，数字必须对应 citations 的 id。"
                "如果 citations 的链接不是目标问题的直接价格/政策/新闻原文，必须说明证据有限。"
                "如果证据有限，要直接说明缺什么证据，不要硬凑。"
                "同时为每条 citation 生成不同的中文概括，概括必须根据该条命中段和问题思考得出，不能套用同一句模板。"
                "输出 JSON 字段：answer:string, answer_points:list, citation_summaries:list。"
                "citation_summaries 每项格式为 {id:number, summary_zh:string}。"
            ),
            {
                "question": question,
                "intent": intent.to_dict(),
                "status": status,
                "warnings": warnings,
                "citations": _citations_for_model(citations),
            },
        )
        model_payload = model_result.get("payload")
        valid_model_answer = _valid_model_answer(model_payload, citations)
        if valid_model_answer:
            _apply_model_citation_summaries(model_payload, citations)
            answer_points = _normalize_model_points(model_payload, citations)
            answer = _normalize_model_answer(model_payload["answer"], answer_points, citations, intent, warnings)
            return {
                **base,
                "answer": answer,
                "model_answer": answer,
                "answer_points": answer_points,
                "model_called": True,
                "model_attempted": True,
                "model_completed": True,
                "model_payload_valid": True,
                "model_elapsed_ms": model_result.get("elapsed_ms", 0),
                "model_error_type": "",
                "model_timeout_seconds": model_result.get("timeout_seconds"),
                "model_status": "completed",
                "answer_stage": "model_enhanced",
            }
        error_type = model_result.get("error_type") or "invalid_payload"
        return {
            **base,
            "model_called": True,
            "model_attempted": True,
            "model_elapsed_ms": model_result.get("elapsed_ms", 0),
            "model_error_type": error_type,
            "model_timeout_seconds": model_result.get("timeout_seconds"),
            "model_status": "timeout" if error_type == "timeout" else "failed",
            "answer_stage": "model_timeout" if error_type == "timeout" else "fallback",
        }
    return base


def _citations_for_model(citations: list[dict]) -> list[dict]:
    return [
        {
            "id": row["id"],
            "title": row["title"],
            "matched_excerpt_en": row["matched_excerpt_en"],
            "url": row["url"],
            "source_type": row["source_type"],
            "source_mode": row.get("source_mode", "unknown"),
            "published_at": row["published_at"],
            "source": row["source"],
        }
        for row in citations
    ]


def _apply_model_citation_summaries(payload: dict, citations: list[dict]) -> None:
    summaries = payload.get("citation_summaries")
    if not isinstance(summaries, list):
        return
    by_id = {}
    for row in summaries:
        if not isinstance(row, dict):
            continue
        try:
            citation_id = int(row.get("id"))
        except (TypeError, ValueError):
            continue
        summary = row.get("summary_zh")
        if isinstance(summary, str) and summary.strip():
            by_id[citation_id] = summary.strip()
    for citation in citations:
        if citation["id"] in by_id:
            citation["summary_zh"] = by_id[citation["id"]]


def _normalize_model_points(payload: dict, citations: list[dict]) -> list[dict]:
    citation_ids = {row["id"] for row in citations}
    rows = []
    for point in payload.get("answer_points", []):
        if isinstance(point, dict):
            text = str(point.get("text", "")).strip()
            raw_ids = point.get("citation_ids", [])
            if not isinstance(raw_ids, list):
                raw_ids = []
        else:
            text = str(point).strip()
            raw_ids = re.findall(r"\[(\d+)\]", text)
        ids = []
        for raw_id in raw_ids:
            try:
                citation_id = int(raw_id)
            except (TypeError, ValueError):
                continue
            if citation_id in citation_ids and citation_id not in ids:
                ids.append(citation_id)
        if text:
            rows.append({"text": text, "citation_ids": ids, "confidence": "medium"})
    return rows


def _normalize_model_answer(answer: str, answer_points: list[dict], citations: list[dict], intent: QueryIntent, warnings: list[str]) -> str:
    text = answer.strip()
    citation_ids = {row["id"] for row in citations}
    used = _used_citation_ids(text)
    if {"结论：", "关键依据："}.issubset(text) and used and used.issubset(citation_ids):
        return text
    point_ids = _point_citation_ids(answer_points)
    if not point_ids:
        point_ids = [row["id"] for row in citations[:2]]
    conclusion_ids = point_ids[: min(2, len(point_ids))]
    basis_lines = [row["text"] for row in answer_points[:2] if row.get("text")]
    basis = "；".join(basis_lines) if basis_lines else f"模型基于“{citations[0]['title']}”等来源形成判断"
    risk = "；".join(warnings) if warnings else "当前结论基于已检索公开来源，正式决策仍需授权行情源和原文公告复核"
    risk_ids = [point_ids[-1]] if point_ids else [citations[-1]["id"]]
    return (
        f"结论：{text.rstrip('。')} {_cite(conclusion_ids)}\n"
        f"关键依据：{basis}\n"
        f"风险/限制：{risk} {_cite(risk_ids)}\n"
        f"下一步建议：{_next_step(intent, warnings)}"
    )


def _point_citation_ids(answer_points: list[dict]) -> list[int]:
    ids: list[int] = []
    for point in answer_points:
        for citation_id in point.get("citation_ids", []):
            if citation_id not in ids:
                ids.append(citation_id)
    return ids


def _used_citation_ids(text: str) -> set[int]:
    return {int(match) for match in re.findall(r"\[(\d+)\]", text)}


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
    if intent.intent == "price" and citations[0]["source_type"] != "price":
        return f"已检索到的{source_labels}来源只能作为间接背景，最相关证据来自“{citations[0]['title']}”，但它不是 LME/SHFE/Mysteel 数值行情"
    if intent.intent == "price" and citations[0].get("source_mode") in {"public_visible_price", "price_proxy_public", "third_party_public"}:
        return f"已检索到的{source_labels}来源来自公开可见/公开代理或第三方公开价格，最相关证据是“{citations[0]['title']}”，可用于 MVP 价格问答，但不是 LME/SHFE/Mysteel 授权行情"
    if intent.intent == "policy" and citations[0]["source_type"] != "policy":
        return f"已检索到的{source_labels}来源只能作为间接背景，最相关证据来自“{citations[0]['title']}”，但它不是目标政策原文"
    return f"已检索到的{source_labels}来源中，最直接的证据来自“{citations[0]['title']}”，其原文段落说明了与问题相关的市场或政策背景"


def _risk(intent: QueryIntent, warnings: list[str]) -> str:
    if warnings:
        return _warning_text(warnings)
    if intent.intent == "price":
        return "当前价格结论基于公开可见/公开代理价格；正式交易或投资判断仍需复核交易所或价格服务授权源"
    return "当前为公开源检索结果，仍需结合一手公告、交易所/价格授权源和人工复核"


def _next_step(intent: QueryIntent, warnings: list[str]) -> str:
    if intent.intent == "price":
        return "继续补充更多公开可见价格页；正式用途再接授权价格源复核。"
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
    if "direct_policy_change_evidence_not_found" in warnings:
        return "未检索到直接政策改动、出口管制、关税、配额或许可调整证据"
    if "direct_export_evidence_not_found" in warnings:
        return "检索到的是相关矿种/地区新闻，但没有直接出口或出货证据"
    if "broad_query_evidence_thin" in warnings:
        return "宽泛问题的可用证据不足，当前只能整理有限背景和证据缺口"
    return None


def _status(intent: QueryIntent, citations: list[dict], warnings: list[str]) -> str:
    if intent.missing_dimensions or not citations:
        return "abstain"
    if any(warning.startswith("direct_") for warning in warnings):
        return "limited"
    if "broad_query_evidence_thin" in warnings:
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


def _new_retrieval_trace(question: str, top_k: int, requested_days: int | None, intent: QueryIntent, candidate_k: int) -> dict:
    return {
        "question": question,
        "requested_top_k": top_k,
        "requested_days": requested_days,
        "effective_days": intent.days,
        "search_days": _search_days(intent),
        "intent": intent.to_dict(),
        "candidate_k": candidate_k,
        "searches": [],
        "dropped": [],
        "selected_hit_count": 0,
        "citation_count": 0,
        "model_called": False,
        "model_attempted": False,
        "model_completed": False,
        "model_payload_valid": False,
        "model_elapsed_ms": 0,
        "model_error_type": "",
        "model_timeout_seconds": model_metadata().get("model_timeout_seconds"),
        "answer_stage": "fast_answer",
    }


def _search_days(intent: QueryIntent) -> int | None:
    if intent.intent == "price" and intent.days is not None:
        return max(intent.days, 120)
    return intent.days


def _trace_search(trace: dict | None, kind: str, query_text: str, hits: list[dict]) -> None:
    if trace is None:
        return
    trace["searches"].append(
        {
            "kind": kind,
            "query": query_text,
            "hit_count": len(hits),
            "top_hits": [
                {
                    "score": row.get("score"),
                    "title": row["chunk"]["metadata"].get("title", ""),
                    "source": row["chunk"]["metadata"].get("source", ""),
                    "source_type": row["chunk"]["metadata"].get("source_type", ""),
                    "source_mode": row["chunk"]["metadata"].get("source_mode", ""),
                    "region": row["chunk"]["metadata"].get("region", ""),
                    "commodity": row["chunk"]["metadata"].get("commodity", ""),
                    "published_at": row["chunk"]["metadata"].get("published_at", ""),
                    "hybrid_score": row.get("search_debug", {}).get("hybrid_score"),
                }
                for row in hits[:5]
            ],
        }
    )


def _trace_rerank(trace: dict | None, hits: list[dict]) -> None:
    if trace is None:
        return
    trace["rerank"] = [
        {
            "title": row["chunk"]["metadata"].get("title", ""),
            "source": row["chunk"]["metadata"].get("source", ""),
            "source_type": row["chunk"]["metadata"].get("source_type", ""),
            "source_mode": row["chunk"]["metadata"].get("source_mode", ""),
            "rerank_score": row.get("rerank_score", 0),
            "evidence_relevance": row.get("evidence_relevance", 0),
            "directness": row.get("directness", ""),
            "matched_terms": row.get("matched_terms", []),
        }
        for row in hits[:10]
    ]


def _trace_drop(trace: dict | None, hit: dict, reason: str) -> None:
    if trace is None:
        return
    if len(trace["dropped"]) >= 80:
        return
    meta = hit["chunk"]["metadata"]
    trace["dropped"].append(
        {
            "reason": reason,
            "title": meta.get("title", ""),
            "source": meta.get("source", ""),
            "source_type": meta.get("source_type", ""),
            "source_mode": meta.get("source_mode", ""),
            "url": meta.get("url", ""),
            "chunk_id": hit["chunk"].get("chunk_id", ""),
            "excerpt_head": hit["chunk"].get("text", "")[:160],
        }
    )


def _is_fixture_source(meta: dict) -> bool:
    mode = meta.get("source_mode", "")
    url = meta.get("url", "")
    return "fixture" in mode or "fixture.local" in url


def _explicit_fixture_index(hits: list[dict]) -> bool:
    modes = {hit["chunk"]["metadata"].get("source_mode", "") for hit in hits}
    return bool(modes) and all("fixture" in mode for mode in modes)


def _fixture_only_index(index_dir: str) -> bool:
    try:
        chunks = LocalVectorStore(index_dir).load_chunks()
    except Exception:
        return False
    if not chunks:
        return False
    modes = {chunk.metadata.get("source_mode", "") for chunk in chunks[:50]}
    return bool(modes) and all("fixture" in mode for mode in modes)


def _best_excerpt(text: str, intent: QueryIntent) -> str:
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    terms = [term.lower() for term in terms_for(intent)]
    best = max(sentences, key=lambda sentence: sum(1 for term in terms if term in sentence.lower()), default=text)
    if len(best) < 80 and len(sentences) > 1:
        idx = sentences.index(best)
        best = " ".join(sentences[idx : idx + 2])
    return best[:520].strip()


def _summarize_excerpt_zh(excerpt: str, intent: QueryIntent, meta: dict, citation_id: int = 1) -> str:
    subject = _subject(intent)
    source_type = _source_type_label(meta.get("source_type", "unknown"))
    title = meta.get("title", "该来源")
    focus = _excerpt_focus(excerpt)
    if intent.intent == "price":
        if meta.get("source_type") != "price":
            variants = [
                f"该{source_type}来源只提供{subject}的供应、项目或市场背景，不是直接行情证据。",
                f"这条证据可解释{subject}的潜在供需因素，但仍需搭配公开可见价格行或授权价格数据。",
                f"该命中段与{subject}有关，可作为价格问题的间接线索，仍需补充直接价格源。",
            ]
            return variants[(citation_id - 1) % len(variants)]
        variants = [
            f"该{source_type}来源显示{focus}，可用于判断{subject}价格变化是否有直接行情依据。",
            f"这条证据来自“{title}”，重点是{focus}，更适合支持价格方向或库存/需求解释。",
            f"该命中段说明{focus}，但仍需区分其是否直接覆盖{subject}的目标地区和出口口径。",
        ]
        return variants[(citation_id - 1) % len(variants)]
    if intent.intent == "policy":
        variants = [
            f"该{source_type}来源围绕{focus}，用于判断{subject}政策或监管变化。",
            f"这条来源强调{focus}，可作为审批、配额、追溯或下游加工政策的依据。",
            f"该命中段把{focus}与政策执行背景联系起来，适合用于解释监管影响。",
        ]
        return variants[(citation_id - 1) % len(variants)]
    if intent.intent == "supply_risk":
        variants = [
            f"该{source_type}来源指出{focus}，用于识别{subject}的供应或出货风险。",
            f"这条证据把{focus}作为风险线索，可用于判断维护、物流或合规约束。",
            f"该命中段体现{focus}，适合作为供应链扰动的可追溯依据。",
        ]
        return variants[(citation_id - 1) % len(variants)]
    return f"该{source_type}证据提到{focus}，为{subject}问题提供背景和可追溯依据。"


def _excerpt_focus(excerpt: str) -> str:
    text = excerpt.strip().rstrip(".")
    lowered = text.lower()
    if "price" in lowered or "lme" in lowered or "shfe" in lowered:
        return "价格、库存或交易所趋势信号"
    if "policy" in lowered or "quota" in lowered or "permit" in lowered or "traceability" in lowered:
        return "政策、审批或追溯要求"
    if "shipment" in lowered or "port" in lowered or "maintenance" in lowered or "community" in lowered:
        return "出货、维护或社区许可风险"
    if len(text) > 90:
        text = text[:87].rstrip() + "..."
    return text or "原文中的关键事实"


def _source_reliability(meta: dict) -> str:
    mode = meta.get("source_mode", "")
    if mode in {"authorized_csv", "authorized_api"}:
        return "high"
    if mode in {"public_visible_price", "price_proxy_public", "third_party_public"}:
        return "public_proxy"
    if mode.startswith("real"):
        return "high"
    if mode == "source_limited":
        return "access_limited"
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


def _warning_text(warnings: list[str]) -> str:
    labels = {
        "direct_price_evidence_not_found": "未检索到直接价格证据，不能仅凭新闻或政策判断价格变化",
        "direct_policy_evidence_not_found": "未检索到直接政策证据，不能仅凭新闻或价格判断政策变化",
        "direct_policy_change_evidence_not_found": "未检索到直接政策改动、出口管制、关税、配额或许可调整证据",
        "direct_export_evidence_not_found": "检索到相关新闻，但没有直接出口/出货原文证据",
        "broad_query_evidence_thin": "宽泛问题命中的有效证据偏少，应继续补充官方政策和新闻来源",
        "no_relevant_evidence_above_threshold": "没有达到相关性门槛的原文证据",
        "fixture_sources_excluded_from_business_answer": "已排除模拟 fixture 来源，避免把样例数据当作原文证据",
        "fixture_mode_answer: using explicit demo fixture index, not original-source evidence": "当前使用显式样例索引，不能视为原站证据",
    }
    readable = []
    for warning in warnings:
        if warning.startswith("source_access_limited:"):
            readable.append("部分原站访问受限，需要授权源或人工打开原站复核")
        elif warning.startswith("unsupported_or_missing_source:"):
            readable.append("当前索引缺少该地区或矿种的一手来源")
        else:
            readable.append(labels.get(warning, warning))
    return "；".join(dict.fromkeys(readable))


def _asks_export_or_shipment(question: str) -> bool:
    lowered = question.lower()
    return any(term in lowered or term in question for term in ("出口", "出货", "发运", "export", "shipment", "shipments"))


def _has_export_or_shipment_evidence(hits: list[dict]) -> bool:
    for hit in hits:
        meta = hit["chunk"]["metadata"]
        text = f"{meta.get('title', '')} {hit['chunk'].get('text', '')}".lower()
        if any(term in text for term in ("export", "exports", "shipment", "shipments", "shipping", "出货", "出口", "发运")):
            return True
    return False


def _valid_model_answer(payload: dict | None, citations: list[dict]) -> bool:
    if not isinstance(payload, dict):
        return False
    answer = payload.get("answer")
    points = payload.get("answer_points")
    citation_ids = {row["id"] for row in citations}
    if not isinstance(answer, str) or not isinstance(points, list):
        return False
    used = _used_citation_ids(answer)
    for point in points:
        if isinstance(point, dict):
            raw_ids = point.get("citation_ids", [])
            if isinstance(raw_ids, list):
                for raw_id in raw_ids:
                    try:
                        used.add(int(raw_id))
                    except (TypeError, ValueError):
                        continue
            used.update(_used_citation_ids(str(point.get("text", ""))))
        else:
            used.update(_used_citation_ids(str(point)))
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
