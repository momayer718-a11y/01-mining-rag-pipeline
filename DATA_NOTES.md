# DATA_NOTES

## Source Policy

Default mode is real-first.

- `news`: MINING.com RSS plus commodity RSS, Northern Miner, Mining Technology, International Mining and S&P Global Metals/Mining RSS are attempted. Usable RSS/API/HTML text is stored as `source_text`.
- `policy`: Australia DISR Critical Minerals Strategy, IEA, Geoscience Australia, Federal Register API and China Rare Earth Group public pages are attempted. China Rare Earth list/article URLs are preserved as `regcc.cn` links. Slow or blocked policy pages are recorded as `source_limited`.
- `price`: public-visible price rows from accessible CSV/page endpoints, public proxy price rows, clearly labelled third-party public supplements and optional authorized CSV/API rows are stored as price evidence. LME/SHFE/Mysteel pages are still preserved as audit URLs when programmatic access returns Cloudflare, login, rate-limit or other restricted responses.
- `fixture`: synthetic fixture records exist only for offline tests and `make fixture-ingest`. They are excluded from default business answers and should never be presented as original-source evidence.

## Anti-Blocking / Network Policy

The collector does not bypass CAPTCHA, login walls, paid feeds or Cloudflare challenges. It uses compliant resilience only:

- official RSS, public API and official page entry points;
- reasonable user agent, timeout, retry, HTTP cache and source rate limiting;
- optional `SOURCE_HTTP_PROXY` / `SOURCE_HTTPS_PROXY` for legitimate local network egress configuration;
- public-visible price CSV/page imports, third-party public supplements, plus local authorized CSV/API import for licensed LME/SHFE/Mysteel data;
- explicit `source_limited` records when a source cannot be accessed.

`source_limited` and `source_discovery` rows are audit records only. They preserve original URLs and failure reasons but do not count as usable evidence.

## Document Schema

- `id`: SHA256 prefix generated from `source + canonical_url + content_hash`.
- `source`: source system, for example `mining.com`, `spglobal`, `disr`, `china-rare-earth`, `lme`, `shfe`, `mysteel`.
- `source_type`: one of `news`, `policy`, `price`.
- `title`: article/page/source title.
- `url`: canonical original URL or explicit fixture URL in fixture mode only.
- `published_at`: ISO date where available; otherwise collection date.
- `content`: cleaned source text, RSS full text, page text, list-page context or a source-access note.
- `metadata.source_mode`: `real_rss`, `official_api`, `real_html`, `public_visible_price`, `authorized_csv`, `authorized_api`, `price_proxy_public`, `third_party_public`, `source_limited` or `fixture`.
- `metadata.evidence_kind`: `source_text` for usable source text, `price_row` for numeric rows, `source_status` for access/availability notes, `source_discovery` for URL discovery rows.
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
- Source-discovery rows are not treated as factual evidence.
- Fixture links are excluded from default business answers.
- Commodity and region dimensions must match the question when present.
- Price questions prefer `price` evidence. If only news/policy evidence is available, status becomes `limited` and warnings include `direct_price_evidence_not_found`.
- Public-visible, public proxy and third-party public price rows can support MVP price answers. They are labelled separately from `authorized_csv` / `authorized_api` and must not be described as official LME/SHFE/Mysteel licensed data.
- Policy questions prefer `policy` evidence. If only news/price evidence is available, status becomes `limited` and warnings include `direct_policy_evidence_not_found`.
- Unsupported or missing regions, such as DRC policy or Peru community-conflict sources in this MVP, return `abstain` instead of a forced answer.

## Known Limitations

- The local store is hybrid lexical/BM25-style retrieval with metadata, phrase, source-type and recency boosts, not a hosted semantic vector DB. It can be swapped for Qdrant/FAISS plus embeddings without changing `/query`.
- RSS availability and source blocking vary by network. This is captured in `source_mode`, `warnings` and `data_quality`.
- Numeric LME/SHFE/Mysteel histories require authorized data access for production or trading use.
- The interview prompt asks for 200 records per source type, 600 total. `coverage_audit` reports usable evidence counts, source-limited counts and gaps. Price coverage can be satisfied by public-visible/proxy/third-party public price rows for the MVP while preserving source-mode labels for audit.
