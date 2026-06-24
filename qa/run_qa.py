from __future__ import annotations

import json
import re
import statistics
import time
from pathlib import Path

from pipeline.ingest import run_ingest
from serve.app import CONSOLE_HTML
from serve.query_engine import query

PLACEHOLDERS = ["TODO", "placeholder", "Traceback", "undefined", "null null"]


def run() -> dict:
    started = time.perf_counter()
    run_ingest("data/runtime", per_source=200, fixture=True)
    cases = json.loads(Path("qa/industry_cases.json").read_text(encoding="utf-8"))
    rows = []
    elapsed = []
    answer_signatures = set()
    for case in cases:
        result = query(case["question"], top_k=5)
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
                "passed": result["status"] == case["expect_status"] and citation_ok and not debug_leak,
                "elapsed_ms": result["elapsed_ms"],
                "warnings": result["warnings"],
                "evidence_count": len(result["hits"]),
                "citation_ok": citation_ok,
                "debug_leak": debug_leak,
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
        "status": "passed" if backend["passed"] == backend["total"] and frontend["passed"] else "failed",
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
        "frontend": frontend,
        "backend": backend,
    }
    out_dir = Path("qa/reports")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "qa_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "frontend_report.json").write_text(json.dumps(frontend, ensure_ascii=False, indent=2), encoding="utf-8")
    Path("QA_REPORT.md").write_text(_markdown(report), encoding="utf-8")
    if report["status"] != "passed":
        raise SystemExit(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def _frontend_report(html: str) -> dict:
    required = ["三源聚合 RAG 控制台", "中文问题", "答案", "答案来源", "后台 JSON 输出"]
    missing = [text for text in required if text not in html]
    placeholders = [token for token in PLACEHOLDERS if token.lower() in html.lower()]
    viewports = [
        {"width": 1280, "height": 720, "expected": "desktop"},
        {"width": 768, "height": 900, "expected": "tablet"},
        {"width": 390, "height": 844, "expected": "mobile"},
    ]
    return {"passed": not missing and not placeholders, "missing": missing, "placeholder_hits": placeholders, "viewports": viewports}


def _citation_integrity(result: dict) -> bool:
    if result["status"] == "abstain":
        return True
    citation_ids = {str(row["id"]) for row in result.get("citations", [])}
    used = set(re.findall(r"\[(\d+)\]", result.get("answer", "")))
    return bool(used) and used.issubset(citation_ids)


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
