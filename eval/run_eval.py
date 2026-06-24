from __future__ import annotations

import argparse
import json
from pathlib import Path

from serve.query_engine import query


def run_eval(index_dir: str = "data/runtime", gt_path: str = "eval/ground_truth.json") -> dict:
    questions = json.loads(Path(gt_path).read_text(encoding="utf-8"))
    rows = []
    recalled = 0
    faithful = 0
    for item in questions:
        result = query(item["question"], top_k=5, index_dir=index_dir)
        hit_text = json.dumps(result["hits"], ensure_ascii=False).lower()
        expected = [term.lower() for term in item["expected_terms"]]
        ok = any(term in hit_text for term in expected)
        citation_ids = {str(row["id"]) for row in result.get("citations", [])}
        used_ids = set(__import__("re").findall(r"\[(\d+)\]", result["answer"]))
        cited = bool(used_ids) and used_ids.issubset(citation_ids)
        recalled += int(ok)
        faithful += int(cited and bool(result["hits"]))
        rows.append({"question": item["question"], "recall_hit": ok, "faithful": cited})
    total = len(questions)
    return {
        "total": total,
        "recall@5": round(recalled / total, 3),
        "answer_faithfulness": round(faithful / total, 3),
        "rows": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--index-dir", default="data/runtime")
    parser.add_argument("--gt-path", default="eval/ground_truth.json")
    args = parser.parse_args()
    print(json.dumps(run_eval(args.index_dir, args.gt_path), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
