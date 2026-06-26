# Mining RAG Pipeline Console

Mining RAG Pipeline Console is a runnable mining-industry research assistant for interview question 1. It aggregates mining news, critical-minerals policy sources and price evidence, then answers Chinese or mixed Chinese/English questions with citations, retrieval audit traces and explicit evidence-quality boundaries.

The implementation is optimized for a practical MVP demo rather than a toy RAG script:

- Fast first answer: `/query` returns a citation-based answer immediately, while optional DeepSeek V4 Pro enhancement runs as a second stage.
- Broad-question retrieval: Australian export-policy, rare-earth, Pilbara shipment and price questions trigger query expansion, source-type preference and reranking.
- Evidence governance: blocked, paywalled, CAPTCHA, Cloudflare or licensed feeds become `source_limited` audit records instead of fabricated facts.
- Price-source boundary: public-visible/proxy/third-party price rows can support MVP analysis, but are labelled separately from official LME/SHFE/Mysteel authorized data.
- QA visibility: `retrieval_trace`, coverage audit, selected/dropped evidence reasons and 50-case eval reports are included for review.

Latest checked results:

| Check | Result |
| --- | --- |
| Unit tests | 17 passed |
| Industry QA | 25/25 backend cases passed |
| Runtime coverage | news 316, policy 442, price 253, total usable 1011 |
| 50-case generalization eval | 50/50 passed |
| First-answer latency | avg 1119.56 ms, p95 1971.3 ms |

The important product behavior is evidence discipline. The pipeline ingests original public sources first, separates usable evidence from access-status notes, and does not use synthetic fixture records as business evidence. If an original source is blocked, paywalled, rate-limited or too slow, the response returns `limited`/`abstain` with explicit warnings instead of inventing a price move or policy change.

## Interview Requirement Mapping

Question 1 asks for a 24-hour build of a three-source aggregation pipeline:

- Mining news: MINING.com RSS and S&P Global Mining/Metals RSS.
- Critical-minerals policy: Australia DISR Critical Minerals Strategy and China Rare Earth Group public pages.
- Price sources: LME copper/zinc/nickel, SHFE lithium carbonate and Mysteel iron ore.
- Delivery: `pipeline/`, `serve/`, `eval/`, `DATA_NOTES.md`, `/query` REST API, deduplication, hybrid vector-style retrieval, QA and 50-case generalization eval, plus runnable documentation.

This MVP implements the full chain while respecting source boundaries. MINING.com, Federal Register, China Rare Earth public pages, public-visible/proxy price rows and clearly labelled third-party public supplements are ingested where available. S&P, DISR, LME, SHFE and Mysteel can return access restrictions in some environments; those URLs are preserved as audit links and surfaced as warnings, not converted into fake official numeric evidence.

## Source Coverage

The bundled runtime cache can be rebuilt with public-visible/proxy price rows and third-party public supplements so news, policy and price each target 200 usable evidence records. LME/SHFE/Mysteel official pages remain audit links when programmatic access is blocked; public-visible/proxy/third-party rows are labelled separately from authorized exchange/vendor feeds.

`/stats` and the Web console expose `coverage_audit`:

| Type | Target | Current usable | Status |
| --- | ---: | ---: | --- |
| news | 200 | 316 in latest checked `data/runtime` | pass |
| policy | 200 | 442 in latest checked `data/runtime` | pass |
| price | 200 | 253 in latest checked `data/runtime` | pass |
| total | 600 | 1011 usable in latest checked `data/runtime` | pass |

Source-limited records and source-discovery rows are audit records only and never count toward the 600-record target.

## What It Does

- Crawls original RSS/HTML sources and writes `data/runtime/collection_snapshot.json`.
- Uses HTTP cache, retry, timeout, source rate limiting and optional local proxy variables for normal network egress.
- Imports public-visible/proxy price rows and optional licensed price rows from `data/raw/prices/*.csv` or authorized JSON/CSV API endpoints.
- Deduplicates by `source + canonical_url + content_hash`.
- Splits documents into chunks and indexes them in a local hybrid lexical/BM25-style store with metadata, phrase, source-type and recency boosts.
- Parses mining questions by commodity, region, intent and time window.
- Expands broad mining questions into multiple source/commodity/policy searches before reranking.
- Filters evidence so source links must match the requested commodity/region; irrelevant fixture or blocked-source notes are not used as answer facts.
- Generates Chinese answers with numbered citations in the form `[1]`, `[2]`.
- Returns a deterministic citation-based fast answer first; the Web console then optionally asks the configured OpenAI-compatible model for an enhanced answer.
- Keeps the fast answer when the model times out or returns invalid JSON.
- Provides a Web console with collapsible answer sources and raw backend JSON.

## Answer Format

Business output:

```text
结论：... [1][2]
关键依据：... [1]
风险/限制：... [3]
下一步建议：...
```

Citation cards:

```text
1 - 原文标题
命中段：Original sentence or paragraph from the source
概括：中文概括，基于该条命中段和问题生成
链接：https://original-source-url
```

Debug fields such as matched terms, relevance scores and raw hits stay in the folded backend JSON block.

## Quick Start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
make ingest
make serve
```

Open `http://localhost:8001`.

Docker:

```bash
docker compose up --build
```

The Docker command runs real-first ingestion before starting the Web console.

Full quantity-target ingestion:

```bash
make ingest-full
```

This runs with `TARGET_PER_SOURCE_TYPE=200` and writes `data/runtime_full`. Runtime depends on current network reachability, public source availability and any authorized price files or API endpoints you provide.

## Price Data

Public-visible/proxy price collection is enabled by default for MVP coverage. These rows use `metadata.source_mode = public_visible_price`, `price_proxy_public` or `third_party_public` and can support MVP price Q&A, but they are not represented as official LME/SHFE/Mysteel licensed feeds.

For production-grade official exchange/vendor prices, place licensed LME/SHFE/Mysteel exports in `data/raw/prices/*.csv` using the schema documented in `data/raw/prices/README.md`.

Required columns:

```csv
date,commodity,price,currency,unit,source,title,url,region
```

Validate and import a CSV:

```bash
python3 scripts/import_price_csv.py /path/to/lme_prices.csv --strict
```

Authorized APIs are supported when endpoints return JSON rows or CSV with the same schema:

```bash
export AUTHORIZED_PRICE_API_URLS="https://vendor.example/lme.csv,https://vendor.example/shfe.json"
export AUTHORIZED_PRICE_API_TOKEN="your_vendor_token"
```

Rows imported from CSV use `metadata.source_mode = authorized_csv`; rows imported from API use `authorized_api`. Public-visible/proxy rows remain separately labelled and are not treated as LME/SHFE/Mysteel official data.

## Model Configuration

The app reads model settings from environment variables only. Do not commit real keys.

```bash
export MODEL_API_KEY=your_key
export MODEL_BASE_URL=https://api.deepseek.com
export MODEL_NAME=deepseek-v4-pro
export MODEL_THINKING_ENABLED=0
export MODEL_REASONING_EFFORT=medium
export MODEL_TIMEOUT_SECONDS=15
export MODEL_MAX_TOKENS=1400
export MODEL_RETRY_COUNT=0
export MODEL_KEY_PASSPHRASE=your_local_decryption_passphrase
```

`/query` defaults to `enhance=false`, so it returns a fast citation-based answer without waiting for the model. Send `enhance=true` to ask the configured chat model to synthesize a model-enhanced answer from retrieved evidence only. The Web console does this as a second background request: users see the fast answer first, then the enhanced answer if the model completes.

Thinking is disabled by default with `MODEL_THINKING_ENABLED=0` because DeepSeek V4 Pro thinking mode is slower and can timeout in bulk QA. Enable it only for deep analysis, audit runs or live model-output tests. `MODEL_TIMEOUT_SECONDS`, `MODEL_MAX_TOKENS` and `MODEL_RETRY_COUNT` keep one slow request from blocking the tool.

`config/model_api_key.enc.json` may be committed because it contains only the encrypted DeepSeek key. Runtime uses `MODEL_API_KEY` first; if it is empty, it decrypts this file with `MODEL_KEY_PASSPHRASE`.

## API Example

```bash
curl -s http://localhost:8001/query \
  -H 'content-type: application/json' \
  -d '{"question":"近 7 天澳洲锂出口政策有何变化?","top_k":5}' | jq
```

Stable response fields include `status`, `warnings`, `source_mode`, `elapsed_ms`, `data_quality`, `intent`, `answer`, `fast_answer`, `model_answer`, `model_status`, `answer_stage`, `answer_points`, `citations`, `hits` and `retrieval_trace`.

## Fixture Mode

Fixtures are retained only for offline tests and deterministic demos:

```bash
make fixture-ingest
```

Do not present fixture results as original-source evidence. The normal `make ingest`, Docker startup and Web console use real-first ingestion.

## QA And Packaging

```bash
make test
make qa
PYTHONPATH=. python3 -m eval.run_eval --index-dir data/runtime --gt-path eval/generalization_50.json --out-path eval/generalization_50_report.json --progress
make package
```

`make qa` runs 25 industry generalization cases against the real-first index and verifies:

- No `fixture.local` citation links.
- Answers cite available source IDs correctly.
- Price-source gaps return `limited` or `abstain` warnings.
- Frontend text has the expected labels `命中段` / `概括` and no placeholder/debug leakage.
- Coverage audit is present and distinguishes usable evidence from access-status rows.

The latest 50-case generalization eval covers broad industry summaries, Australia export/policy questions, China rare-earth policy, price trend and price-boundary questions, project news, supply-chain risk, mixed Chinese/English prompts, abstention, and multi-source synthesis. The latest checked report is `eval/generalization_50_report.json`: 50/50 passed, pass rate 1.0, answer faithfulness 1.0, retrieval trace accuracy 1.0, average first-answer latency 1119.56 ms and p95 first-answer latency 1971.3 ms.

`make package` creates `/Users/Zhuanz/Desktop/01-mining-rag-pipeline-tool.zip`.

## Boundaries

This is a complete interview MVP, not a production market-data terminal. It does not bypass login walls, CAPTCHA, Cloudflare challenges, paid feeds or rate limits. It uses public RSS/API/pages, caching, retry, rate limiting and optional proxy configuration for normal network egress. For formal trading/investment use, connect licensed LME/SHFE/Mysteel data and re-run the same indexing/query chain.
