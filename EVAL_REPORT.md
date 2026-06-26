# EVAL_REPORT - 50-Case Generalization RAG Evaluation

## Scope

- Eval set: `eval/generalization_50.json`
- Report JSON: `eval/generalization_50_report.json`
- Runtime index: `data/runtime`
- Model config: `deepseek-v4-pro`
- Default answer mode: `fast_answer`
- Model reasoning: `not_requested` unless `enhance=true`

## Commands Run

```bash
PYTHONPATH=. python3 -m eval.run_eval --index-dir data/runtime --gt-path eval/generalization_50.json --out-path eval/generalization_50_report.json --progress
PYTHONPATH=. pytest -q
PYTHONPATH=. python3 -m qa.run_qa
```

## Summary

- Total cases: 50
- Passed: 50
- Pass rate: 1.0
- Recall@5: 1.0
- Answer faithfulness: 1.0
- Status accuracy: 1.0
- Min citation rate: 1.0
- Source-type accuracy: 1.0
- Retrieval trace accuracy: 1.0
- Avg first-answer latency: 1119.56 ms
- p95 first-answer latency: 1971.3 ms
- Model called rate: 0.0 in this eval because default `/query` does not request enhancement
- Model reasoning: `not_requested` for all 50 default fast-answer cases

## Scenario Results

| Scenario | Passed | Total | Pass Rate | Statuses |
|---|---:|---:|---:|---|
| australia_policy_export | 5 | 5 | 1.0 | ok: 4, limited: 1 |
| boundary | 5 | 5 | 1.0 | abstain: 5 |
| broad_industry | 5 | 5 | 1.0 | ok: 4, limited: 1 |
| china_rare_earth_policy | 5 | 5 | 1.0 | ok: 5 |
| mixed_language | 5 | 5 | 1.0 | ok: 5 |
| multi_source_synthesis | 5 | 5 | 1.0 | ok: 5 |
| price_boundary | 5 | 5 | 1.0 | ok: 5 |
| price_trend | 5 | 5 | 1.0 | ok: 5 |
| project_news | 5 | 5 | 1.0 | ok: 5 |
| supply_risk | 5 | 5 | 1.0 | ok: 5 |

## Interpretation

The previous slow-response issue was addressed by changing `/query` to a two-stage design. Stage 1 returns a citation-based fast answer after retrieval. Stage 2 is optional and only runs when `enhance=true`; the Web console starts that second request in the background and keeps the fast answer if the model times out.

The previous broad-query retrieval issue was addressed by hybrid retrieval and reranking: BM25-style lexical score, metadata/source-type boost, Chinese/English synonym expansion, phrase boost, broad mining query planning, low-quality chunk filtering, URL/chunk diversity limits and `retrieval_trace` auditing.

The previous price evidence gap was addressed for MVP use by adding public-visible/proxy and third-party public evidence rows. These are labelled as `public_visible_price`, `price_proxy_public` or `third_party_public`; answers must not present them as official LME/SHFE/Mysteel licensed data.

## Remaining Boundary

This is still not a production market-data terminal. Official LME/SHFE/Mysteel settlement/history data requires authorized CSV/API feeds. The current public and third-party rows are acceptable for MVP question answering only because they are explicitly labelled as non-official and non-authorized.
