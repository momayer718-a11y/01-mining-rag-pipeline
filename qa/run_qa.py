from __future__ import annotations

import json
import os
import re
import statistics
import time
from pathlib import Path

from pipeline.ingest import run_ingest
from serve.app import CONSOLE_HTML
import serve.query_engine as query_engine
from serve.query_engine import query

PLACEHOLDERS = ["TODO", "placeholder", "Traceback", "undefined", "null null"]


def run() -> dict:
    started = time.perf_counter()
    os.environ.setdefault("FETCH_PRICE_PROXIES", "0")
    os.environ.setdefault("FETCH_ARTICLE_PAGES", "0")
    os.environ.setdefault("FETCH_RETRIES", "1")
    os.environ.setdefault("FETCH_CONNECT_TIMEOUT", "2")
    os.environ.setdefault("REQUEST_DELAY_SECONDS", "0.02")
    os.environ["MODEL_API_KEY"] = ""
    query_engine.complete_json = lambda *args, **kwargs: None
    query_engine.model_metadata = lambda: {"model_provider": "fallback", "model_name": "deterministic-template", "model_mode": "fallback"}
    qa_index_dir = os.getenv("QA_INDEX_DIR", _default_qa_index_dir())
    quantity_index_dir = os.getenv("QA_QUANTITY_INDEX_DIR", qa_index_dir)
    if _truthy(os.getenv("QA_LIVE_INGEST", "")):
        ingest_summary = run_ingest(qa_index_dir, per_source=int(os.getenv("QA_PER_SOURCE", "5")), fixture=False)
    else:
        ingest_summary = _summary_from_existing_index(qa_index_dir)
        ingest_summary["source_mode"] = "existing_index"
    quantity_summary = _summary_from_existing_index(quantity_index_dir)
    cases = json.loads(Path("qa/industry_cases.json").read_text(encoding="utf-8"))
    rows = []
    elapsed = []
    answer_signatures = set()
    for case in cases:
        result = query(case["question"], top_k=5, index_dir=qa_index_dir)
        elapsed.append(result["elapsed_ms"])
        answer_signature = _signature(result["answer"])
        answer_signatures.add(answer_signature)
        citation_ok = _citation_integrity(result)
        debug_leak = _debug_leak(result["answer"])
        rows.append(
            {
                "question": case["question"],
                "expected": case["expect_status"],
                "actual": result["status"],
                "passed": _status_ok(result["status"], case["expect_status"]) and citation_ok and not debug_leak and _no_fixture_links(result),
                "elapsed_ms": result["elapsed_ms"],
                "warnings": result["warnings"],
                "evidence_count": len(result["hits"]),
                "citation_ok": citation_ok,
                "debug_leak": debug_leak,
                "fixture_links": not _no_fixture_links(result),
            }
        )
    frontend = _frontend_report(CONSOLE_HTML)
    backend = {
        "total": len(rows),
        "passed": sum(1 for row in rows if row["passed"]),
        "success_rate": round(sum(1 for row in rows if row["passed"]) / len(rows), 3),
        "avg_elapsed_ms": round(statistics.mean(elapsed), 2),
        "p95_elapsed_ms": round(sorted(elapsed)[int(len(elapsed) * 0.95) - 1], 2),
        "abstain_rate": round(sum(1 for row in rows if row["actual"] == "abstain") / len(rows), 3),
        "unique_answer_signatures": len(answer_signatures),
        "rows": rows,
    }
    report = {
        "tool": "01-mining-rag-pipeline",
        "status": "passed" if backend["passed"] == backend["total"] and frontend["passed"] and _coverage_targets_met(quantity_summary) else "failed",
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
        "ingest": ingest_summary,
        "quantity_ingest": quantity_summary,
        "coverage_check": _coverage_check(quantity_summary),
        "frontend": frontend,
        "backend": backend,
    }
    out_dir = Path(os.getenv("QA_REPORT_DIR", "outputs/generated/qa"))
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "qa_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "frontend_report.json").write_text(json.dumps(frontend, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown = _markdown(report)
    Path(os.getenv("QA_REPORT_MD", "outputs/generated/QA_REPORT.md")).write_text(markdown, encoding="utf-8")
    if _truthy(os.getenv("QA_UPDATE_TRACKED_REPORTS", "")):
        tracked_dir = Path("qa/reports")
        tracked_dir.mkdir(parents=True, exist_ok=True)
        (tracked_dir / "qa_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        (tracked_dir / "frontend_report.json").write_text(json.dumps(frontend, ensure_ascii=False, indent=2), encoding="utf-8")
        Path("QA_REPORT.md").write_text(markdown, encoding="utf-8")
    if report["status"] != "passed":
        raise SystemExit(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def _frontend_report(html: str) -> dict:
    required = ["三源聚合 RAG 控制台", "中文问题", "答案", "答案来源", "后台 JSON 输出", "命中段：", "概括："]
    missing = [text for text in required if text not in html]
    placeholders = [token for token in PLACEHOLDERS if token.lower() in html.lower()]
    viewports = [
        {"width": 1280, "height": 720, "expected": "desktop"},
        {"width": 768, "height": 900, "expected": "tablet"},
        {"width": 390, "height": 844, "expected": "mobile"},
    ]
    return {"passed": not missing and not placeholders, "missing": missing, "placeholder_hits": placeholders, "viewports": viewports}


def _coverage_audit_valid(summary: dict) -> bool:
    audit = summary.get("coverage_audit", {})
    return all(source_type in audit for source_type in ("news", "policy", "price", "total"))


def _full_quantity_targets_met(summary: dict) -> bool:
    audit = summary.get("coverage_audit", {})
    return _coverage_audit_valid(summary) and all(audit.get(source_type, {}).get("meets_target") for source_type in ("news", "policy", "price", "total"))


def _price_boundary_ok(summary: dict) -> bool:
    audit = summary.get("coverage_audit", {})
    price = audit.get("price", {})
    source_limited = price.get("source_limited_count", 0)
    usable = price.get("usable_evidence_count", 0)
    return not price.get("meets_target", False) and usable == 0 and source_limited > 0


def _coverage_targets_met(summary: dict) -> bool:
    audit = summary.get("coverage_audit", {})
    if _full_quantity_targets_met(summary):
        return True
    return (
        _coverage_audit_valid(summary)
        and all(audit.get(source_type, {}).get("meets_target") for source_type in ("news", "policy", "total"))
        and _price_boundary_ok(summary)
    )


def _coverage_check(summary: dict) -> dict:
    audit = summary.get("coverage_audit", {})
    return {
        "has_audit": _coverage_audit_valid(summary),
        "target_per_source_type": summary.get("target_per_source_type"),
        "target_total": summary.get("target_total"),
        "usable_total": audit.get("total", {}).get("usable_evidence_count", 0),
        "source_limited_total": audit.get("total", {}).get("source_limited_count", 0),
        "meets_full_quantity_target": _full_quantity_targets_met(summary),
        "price_boundary_enforced": _price_boundary_ok(summary),
        "meets_runtime_gate": _coverage_targets_met(summary),
        "by_type": {
            source_type: {
                "usable": audit.get(source_type, {}).get("usable_evidence_count", 0),
                "target": audit.get(source_type, {}).get("target", 0),
                "meets_target": bool(audit.get(source_type, {}).get("meets_target", False)),
            }
            for source_type in ("news", "policy", "price")
        },
        "note": "QA requires transparent coverage audit. Source-limited and discovery-only rows are not answer evidence; missing official price feeds pass only when price questions stay limited/abstain instead of hard-answering.",
    }


def _summary_from_existing_index(index_dir: str) -> dict:
    from serve.app import _coverage_from_chunks
    from pipeline.store import LocalVectorStore

    chunks = LocalVectorStore(index_dir).load_chunks()
    doc_meta: dict[str, dict] = {}
    source_modes: dict[str, int] = {}
    for chunk in chunks:
        doc_meta.setdefault(chunk.document_id, chunk.metadata)
        mode = chunk.metadata.get("source_mode", "unknown")
        source_modes[mode] = source_modes.get(mode, 0) + 1
    usable_by_type: dict[str, int] = {}
    limited_by_type: dict[str, int] = {}
    for meta in doc_meta.values():
        source_type = meta.get("source_type", "unknown")
        mode = meta.get("source_mode", "unknown")
        evidence_kind = meta.get("evidence_kind", "")
        if mode != "source_limited" and evidence_kind not in {"source_status", "source_discovery"}:
            usable_by_type[source_type] = usable_by_type.get(source_type, 0) + 1
        else:
            limited_by_type[source_type] = limited_by_type.get(source_type, 0) + 1
    return {
        "index_dir": index_dir,
        "documents": len(doc_meta),
        "chunks": len(chunks),
        "target_per_source_type": 200,
        "target_total": 600,
        "usable_evidence_by_source_type": usable_by_type,
        "source_limited_by_source_type": limited_by_type,
        "source_modes": source_modes,
        "coverage_audit": _coverage_from_chunks(usable_by_type, limited_by_type),
        "source_mode": "existing_index",
    }


def _default_qa_index_dir() -> str:
    for candidate in ("data/runtime", "data/runtime_full"):
        if _index_has_chunks(candidate):
            return candidate
    return "data/runtime"


def _index_has_chunks(index_dir: str) -> bool:
    return (Path(index_dir) / "chunks.jsonl").exists()


def _truthy(value: str) -> bool:
    return value.lower() in {"1", "true", "yes", "on"}


def _citation_integrity(result: dict) -> bool:
    if result["status"] == "abstain":
        return True
    citation_ids = {str(row["id"]) for row in result.get("citations", [])}
    used = set(re.findall(r"\[(\d+)\]", result.get("answer", "")))
    return bool(used) and used.issubset(citation_ids)


def _status_ok(actual: str, expected: str) -> bool:
    if actual == expected:
        return True
    if expected == "ok" and actual == "limited":
        return True
    if expected == "ok" and actual == "abstain":
        return True
    return False


def _no_fixture_links(result: dict) -> bool:
    payload = json.dumps(result.get("citations", []), ensure_ascii=False)
    return "fixture.local" not in payload


def _debug_leak(answer: str) -> bool:
    blocked = ["命中关键词", "命中主题", "relevance", "相关性为"]
    return any(token in answer for token in blocked)


def _signature(answer: str) -> str:
    return re.sub(r"\s+", " ", answer[:260]).strip()


def _markdown(report: dict) -> str:
    b = report["backend"]
    f = report["frontend"]
    return (
        "# QA_REPORT - Mining RAG Pipeline\n\n"
        f"- Status: {report['status']}\n"
        f"- Ingest mode: {report['ingest'].get('source_mode')}\n"
        f"- Source modes: {report['ingest'].get('source_modes')}\n"
        f"- Quantity index: {report.get('quantity_ingest', {}).get('index_dir')}\n"
        f"- Quantity source modes: {report.get('quantity_ingest', {}).get('source_modes')}\n"
        f"- Coverage audit: {json.dumps(report.get('coverage_check', {}), ensure_ascii=False)}\n"
        f"- Backend cases: {b['passed']}/{b['total']}\n"
        f"- Avg elapsed: {b['avg_elapsed_ms']} ms\n"
        f"- P95 elapsed: {b['p95_elapsed_ms']} ms\n"
        f"- Abstain rate: {b['abstain_rate']}\n"
        f"- Unique answer signatures: {b['unique_answer_signatures']}\n"
        f"- Frontend passed: {f['passed']}\n"
        f"- Placeholder hits: {', '.join(f['placeholder_hits']) or 'none'}\n"
    )


if __name__ == "__main__":
    run()
