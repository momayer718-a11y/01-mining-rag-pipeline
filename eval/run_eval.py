from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import re
import sys
import statistics
from pathlib import Path

import serve.query_engine as query_engine


def run_eval(
    index_dir: str = "data/runtime",
    gt_path: str = "eval/ground_truth.json",
    top_k: int = 5,
    disable_model: bool = False,
    model_timeout: int | None = None,
    progress: bool = False,
) -> dict:
    if disable_model:
        _disable_live_model()
    elif model_timeout is not None:
        _set_model_timeout(model_timeout)
    questions = json.loads(Path(gt_path).read_text(encoding="utf-8"))
    rows = []
    recalled = 0
    faithful = 0
    passed = 0
    status_checked = 0
    status_passed = 0
    min_citations_passed = 0
    source_type_checked = 0
    source_type_passed = 0
    trace_checked = 0
    trace_passed = 0
    model_called = 0
    model_payload_valid = 0
    elapsed_values: list[float] = []
    failure_reasons: Counter[str] = Counter()
    model_modes: Counter[str] = Counter()
    model_reasoning: Counter[str] = Counter()
    scenario_rows: dict[str, list[dict]] = defaultdict(list)
    for idx, item in enumerate(questions, start=1):
        if progress:
            print(f"[eval] {idx}/{len(questions)} {item.get('id', '')} {item['question']}", file=sys.stderr, flush=True)
        result = query_engine.query(item["question"], top_k=top_k, index_dir=index_dir)
        elapsed_values.append(float(result.get("elapsed_ms", 0)))
        hit_text = json.dumps(
            {
                "hits": result.get("hits", []),
                "citations": result.get("citations", []),
                "answer": result.get("answer", ""),
                "warnings": result.get("warnings", []),
            },
            ensure_ascii=False,
        ).lower()
        expected = [term.lower() for term in item["expected_terms"]]
        recall_ok = not expected or any(term in hit_text for term in expected)
        cited = _citation_integrity(result)
        status_ok, has_status_expectation = _status_ok(result.get("status", ""), item)
        min_citations_ok = _min_citations_ok(result, item)
        source_types_ok, has_source_type_expectation = _source_types_ok(result, item)
        trace_ok, has_trace_expectation = _retrieval_trace_ok(result, item)
        row_passed = recall_ok and cited and status_ok and min_citations_ok and source_types_ok and trace_ok
        if not row_passed:
            for reason in _failure_reasons(row_status=result.get("status", ""), recall_ok=recall_ok, cited=cited, status_ok=status_ok, min_citations_ok=min_citations_ok, source_types_ok=source_types_ok, trace_ok=trace_ok, warnings=result.get("warnings", [])):
                failure_reasons[reason] += 1

        recalled += int(recall_ok)
        faithful += int(cited)
        passed += int(row_passed)
        status_checked += int(has_status_expectation)
        status_passed += int(status_ok and has_status_expectation)
        min_citations_passed += int(min_citations_ok)
        source_type_checked += int(has_source_type_expectation)
        source_type_passed += int(source_types_ok and has_source_type_expectation)
        trace_checked += int(has_trace_expectation)
        trace_passed += int(trace_ok and has_trace_expectation)
        model_called += int(bool(result.get("retrieval_trace", {}).get("model_called")))
        model_payload_valid += int(bool(result.get("retrieval_trace", {}).get("model_payload_valid")))
        model_modes.update([str(result.get("model_mode", "unknown"))])
        model_reasoning.update([str(result.get("model_reasoning", "unknown"))])

        row = {
            "id": item.get("id"),
            "scenario": item.get("scenario", "default"),
            "question": item["question"],
            "expected_status": item.get("expect_status"),
            "acceptable_statuses": item.get("acceptable_statuses"),
            "actual_status": result.get("status"),
            "passed": row_passed,
            "recall_hit": recall_ok,
            "faithful": cited,
            "min_citations_ok": min_citations_ok,
            "source_types_ok": source_types_ok,
            "retrieval_trace_ok": trace_ok,
            "citation_count": len(result.get("citations", [])),
            "source_types": sorted({row.get("source_type", "unknown") for row in result.get("citations", [])}),
            "warnings": result.get("warnings", []),
            "model_mode": result.get("model_mode", "unknown"),
            "model_name": result.get("model_name", "unknown"),
            "model_reasoning": result.get("model_reasoning", "unknown"),
            "model_called": bool(result.get("retrieval_trace", {}).get("model_called")),
            "model_payload_valid": bool(result.get("retrieval_trace", {}).get("model_payload_valid")),
            "search_count": len(result.get("retrieval_trace", {}).get("searches", [])),
            "dropped_count": len(result.get("retrieval_trace", {}).get("dropped", [])),
        }
        rows.append(row)
        scenario_rows[row["scenario"]].append(row)
    total = len(questions)
    return {
        "total": total,
        "passed": passed,
        "pass_rate": round(passed / total, 3),
        "recall@5": round(recalled / total, 3),
        "answer_faithfulness": round(faithful / total, 3),
        "status_accuracy": round(status_passed / status_checked, 3) if status_checked else None,
        "min_citation_rate": round(min_citations_passed / total, 3),
        "source_type_accuracy": round(source_type_passed / source_type_checked, 3) if source_type_checked else None,
        "retrieval_trace_accuracy": round(trace_passed / trace_checked, 3) if trace_checked else None,
        "model_called_rate": round(model_called / total, 3),
        "model_payload_valid_rate": round(model_payload_valid / total, 3),
        "model_enhancement_success_rate": round(model_payload_valid / max(model_called, 1), 3),
        "avg_elapsed_ms": round(statistics.mean(elapsed_values), 2) if elapsed_values else 0,
        "p95_elapsed_ms": _p95(elapsed_values),
        "failure_reasons": dict(failure_reasons),
        "model_modes": dict(model_modes),
        "model_reasoning": dict(model_reasoning),
        "by_scenario": _scenario_summary(scenario_rows),
        "rows": rows,
    }


def _disable_live_model() -> None:
    query_engine.complete_json = lambda *args, **kwargs: None
    query_engine.model_metadata = lambda: {
        "model_provider": "fallback",
        "model_name": "deterministic-template",
        "model_mode": "fallback",
        "model_reasoning": "not_requested",
    }


def _set_model_timeout(timeout: int) -> None:
    original_complete_json = query_engine.complete_json

    def complete_json_with_timeout(system_prompt: str, user_payload: dict, **kwargs: object) -> dict | None:
        kwargs["timeout"] = timeout
        return original_complete_json(system_prompt, user_payload, **kwargs)

    query_engine.complete_json = complete_json_with_timeout


def _citation_integrity(result: dict) -> bool:
    if result.get("status") == "abstain":
        return True
    citation_ids = {str(row["id"]) for row in result.get("citations", [])}
    used_ids = set(re.findall(r"\[(\d+)\]", result.get("answer", "")))
    return bool(used_ids) and used_ids.issubset(citation_ids)


def _status_ok(actual: str, item: dict) -> tuple[bool, bool]:
    if "acceptable_statuses" in item:
        return actual in set(item["acceptable_statuses"]), True
    if "expect_status" in item:
        return actual == item["expect_status"], True
    return True, False


def _min_citations_ok(result: dict, item: dict) -> bool:
    minimum = int(item.get("min_citations", 0))
    return len(result.get("citations", [])) >= minimum


def _source_types_ok(result: dict, item: dict) -> tuple[bool, bool]:
    required = set(item.get("required_source_types", []))
    if not required:
        return True, False
    actual = {row.get("source_type", "unknown") for row in result.get("citations", [])}
    return required.issubset(actual), True


def _retrieval_trace_ok(result: dict, item: dict) -> tuple[bool, bool]:
    minimum = item.get("min_searches")
    if minimum is None:
        return True, False
    searches = result.get("retrieval_trace", {}).get("searches", [])
    return len(searches) >= int(minimum), True


def _scenario_summary(rows_by_scenario: dict[str, list[dict]]) -> dict:
    summary = {}
    for scenario, rows in sorted(rows_by_scenario.items()):
        total = len(rows)
        summary[scenario] = {
            "total": total,
            "passed": sum(1 for row in rows if row["passed"]),
            "pass_rate": round(sum(1 for row in rows if row["passed"]) / total, 3),
            "statuses": dict(Counter(row["actual_status"] for row in rows)),
        }
    return summary


def _failure_reasons(
    row_status: str,
    recall_ok: bool,
    cited: bool,
    status_ok: bool,
    min_citations_ok: bool,
    source_types_ok: bool,
    trace_ok: bool,
    warnings: list[str],
) -> list[str]:
    reasons = []
    if not recall_ok:
        reasons.append("expected_terms_not_found")
    if not cited:
        reasons.append("citation_integrity_failed")
    if not status_ok:
        reasons.append(f"status_mismatch:{row_status}")
    if not min_citations_ok:
        reasons.append("too_few_citations")
    if not source_types_ok:
        reasons.append("required_source_type_missing")
    if not trace_ok:
        reasons.append("retrieval_trace_missing")
    reasons.extend(warnings)
    return reasons or ["unknown"]


def _p95(values: list[float]) -> float:
    if not values:
        return 0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(len(ordered) * 0.95) - 1))
    return round(ordered[index], 2)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--index-dir", default="data/runtime")
    parser.add_argument("--gt-path", default="eval/ground_truth.json")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--out-path", default="")
    parser.add_argument("--disable-model", action="store_true")
    parser.add_argument("--model-timeout", type=int, default=None)
    parser.add_argument("--progress", action="store_true")
    args = parser.parse_args()
    result = run_eval(
        args.index_dir,
        args.gt_path,
        top_k=args.top_k,
        disable_model=args.disable_model,
        model_timeout=args.model_timeout,
        progress=args.progress,
    )
    if args.out_path:
        Path(args.out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out_path).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
