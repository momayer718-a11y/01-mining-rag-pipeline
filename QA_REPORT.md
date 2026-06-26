# QA_REPORT - Mining RAG Pipeline

- Status: passed
- Ingest mode: real_first
- Source modes: {'real_rss': 51, 'source_limited': 9, 'real_html': 6, 'official_api': 5, 'public_visible_price': 5, 'third_party_public': 6}
- Quantity index: data/runtime
- Quantity source modes: {'real_rss': 2212, 'source_limited': 10, 'real_html': 225, 'official_api': 708, 'public_visible_price': 250, 'third_party_public': 6}
- Coverage audit: {"has_audit": true, "target_per_source_type": 200, "target_total": 600, "usable_total": 1011, "source_limited_total": 10, "meets_interview_quantity_target": true, "by_type": {"news": {"usable": 316, "target": 200, "meets_target": true}, "policy": {"usable": 442, "target": 200, "meets_target": true}, "price": {"usable": 253, "target": 200, "meets_target": true}}, "note": "QA requires transparent full-quantity coverage audit. It does not count source_limited or discovery-only rows as usable evidence."}
- Backend cases: 25/25
- Avg elapsed: 92.59 ms
- P95 elapsed: 138.88 ms
- Abstain rate: 0.2
- Unique answer signatures: 23
- Frontend passed: True
- Placeholder hits: none
