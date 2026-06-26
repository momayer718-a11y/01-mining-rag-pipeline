# QA_REPORT - Mining RAG Pipeline

- Status: passed
- Ingest mode: existing_index
- Source modes: {'real_rss': 2255, 'source_limited': 10, 'real_html': 475, 'official_api': 612}
- Quantity index: data/runtime
- Quantity source modes: {'real_rss': 2255, 'source_limited': 10, 'real_html': 475, 'official_api': 612}
- Coverage audit: {"has_audit": true, "target_per_source_type": 200, "target_total": 600, "usable_total": 721, "source_limited_total": 10, "meets_full_quantity_target": false, "price_boundary_enforced": true, "meets_runtime_gate": true, "by_type": {"news": {"usable": 320, "target": 200, "meets_target": true}, "policy": {"usable": 401, "target": 200, "meets_target": true}, "price": {"usable": 0, "target": 200, "meets_target": false}}, "note": "QA requires transparent coverage audit. Source-limited and discovery-only rows are not answer evidence; missing official price feeds pass only when price questions stay limited/abstain instead of hard-answering."}
- Backend cases: 25/25
- Avg elapsed: 1153.89 ms
- P95 elapsed: 1527.16 ms
- Abstain rate: 0.24
- Unique answer signatures: 24
- Frontend passed: True
- Placeholder hits: none
