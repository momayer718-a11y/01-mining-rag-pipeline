# DATA_NOTES

## Source Policy

Default mode is real-first.

- `news`: MINING.com RSS feeds are ingested as original article links. S&P Global Metals/Mining RSS is attempted; if the current network returns access restriction, the original RSS URL is recorded as `source_limited`.
- `policy`: Australia DISR Critical Minerals Strategy and China Rare Earth Group public pages are attempted. China Rare Earth list/article URLs are preserved as `regcc.cn` links. Slow or blocked policy pages are recorded as `source_limited`.
- `price`: LME copper/zinc/nickel, SHFE lithium carbonate and Mysteel iron ore are treated as restricted market-data sources unless an authorized feed is configured. The MVP preserves original source URLs and returns `direct_price_evidence_not_found` rather than fabricating numeric trends.
- `fixture`: synthetic fixture records exist only for offline tests and `make fixture-ingest`. They are excluded from default business answers and should never be presented as original-source evidence.

## Document Schema

- `id`: SHA256 prefix generated from `source + canonical_url + content_hash`.
- `source`: source system, for example `mining.com`, `spglobal`, `disr`, `china-rare-earth`, `lme`, `shfe`, `mysteel`.
- `source_type`: one of `news`, `policy`, `price`.
- `title`: article/page/source title.
- `url`: canonical original URL or explicit fixture URL in fixture mode only.
- `published_at`: ISO date where available; otherwise collection date.
- `content`: cleaned source text, RSS full text, page text, list-page context or a source-access note.
- `metadata.source_mode`: `real_rss`, `real_html`, `source_limited` or `fixture`.
- `metadata.evidence_kind`: `source_text` for usable source text, `source_status` for access/availability notes.
- `metadata.commodity` / `metadata.region`: lightweight detected dimensions used for retrieval filtering.

## Dedup Strategy

1. Canonicalize URL by lowercasing host, removing fragments and stripping common tracking query params.
2. Normalize whitespace in content.
3. Hash `source + canonical_url + content_hash`.
4. Keep the first document for each hash.

This means the same article from repeated RSS feeds is collapsed after cleaning.

## Chunk Schema

- `chunk_id`: `document_id:index`.
- `document_id`: parent document ID.
- `text`: chunk text.
- `tokens`: local lexical retrieval tokens.
- `metadata`: inherited source, source type, title, URL, date, source mode and detected dimensions.

## Query Evidence Rules

- Source-status notes are not treated as factual evidence.
- Fixture links are excluded from default business answers.
- Commodity and region dimensions must match the question when present.
- Price questions prefer `price` evidence. If only news/policy evidence is available, status becomes `limited` and warnings include `direct_price_evidence_not_found`.
- Policy questions prefer `policy` evidence. If only news/price evidence is available, status becomes `limited` and warnings include `direct_policy_evidence_not_found`.
- Unsupported or missing regions, such as DRC policy or Peru community-conflict sources in this MVP, return `abstain` instead of a forced answer.

## Known Limitations

- The local store is lexical/vector-style retrieval, not a hosted vector DB. It can be swapped for Qdrant/FAISS plus embeddings without changing `/query`.
- RSS availability and source blocking vary by network. This is captured in `source_mode`, `warnings` and `data_quality`.
- Numeric LME/SHFE/Mysteel histories require authorized data access for production use.
- The interview prompt asks for 200 documents per source. This MVP exposes the same `--per-source` option, but default Docker/local settings keep counts lower for a five-minute runnable demo and do not synthesize fake documents to reach 600.
