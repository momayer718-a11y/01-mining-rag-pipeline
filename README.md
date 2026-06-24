# Mining RAG Pipeline Console

Standalone MVP for interview question 1: a mining news + policy + price-source RAG assistant with a FastAPI API and Web console.

The important behavior is evidence discipline. The default pipeline now ingests original public sources first and does not use synthetic fixture records as business evidence. If an original source is blocked, paywalled, rate-limited or too slow, the response returns `limited`/`abstain` with explicit warnings instead of inventing a price move or policy change.

## Interview Requirement Mapping

Question 1 asks for a 24-hour build of a three-source aggregation pipeline:

- Mining news: MINING.com RSS and S&P Global Mining/Metals RSS.
- Critical-minerals policy: Australia DISR Critical Minerals Strategy and China Rare Earth Group public pages.
- Price sources: LME copper/zinc/nickel, SHFE lithium carbonate and Mysteel iron ore.
- Delivery: `pipeline/`, `serve/`, `eval/`, `DATA_NOTES.md`, `/query` REST API, deduplication, vector-style retrieval, 20 Q&A eval and runnable documentation.

This MVP implements the full chain while respecting source boundaries. MINING.com and China Rare Earth public pages are ingested as original-source documents where available. S&P, DISR, LME, SHFE and Mysteel can return access restrictions in this environment; those URLs are preserved as audit links and surfaced as warnings, not converted into fake numeric evidence.

## What It Does

- Crawls original RSS/HTML sources and writes `data/runtime/collection_snapshot.json`.
- Deduplicates by `source + canonical_url + content_hash`.
- Splits documents into chunks and indexes them in a local lexical vector-style store.
- Parses mining questions by commodity, region, intent and time window.
- Filters evidence so source links must match the requested commodity/region; irrelevant fixture or blocked-source notes are not used as answer facts.
- Generates Chinese answers with numbered citations in the form `[1]`, `[2]`.
- Uses a configured OpenAI-compatible model for answer synthesis when `MODEL_API_KEY` is present.
- Falls back to deterministic Chinese output when no key is configured.
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

## Model Configuration

The app reads model settings from environment variables only. Do not commit real keys.

```bash
export MODEL_API_KEY=your_key
export MODEL_BASE_URL=https://apihub.agnes-ai.com/v1
export MODEL_NAME=agnes-2.0-flash
```

When a key is present, `/query` asks the configured chat model to synthesize the Chinese answer and citation summaries from retrieved evidence only. Without a key, the deterministic fallback remains runnable for tests and demos.

## API Example

```bash
curl -s http://localhost:8001/query \
  -H 'content-type: application/json' \
  -d '{"question":"近 7 天澳洲锂出口政策有何变化?","top_k":5,"days":7}' | jq
```

Stable response fields include `status`, `warnings`, `source_mode`, `elapsed_ms`, `data_quality`, `intent`, `answer`, `answer_points`, `citations` and `hits`.

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
make package
```

`make qa` runs 25 industry generalization cases against the real-first index and verifies:

- No `fixture.local` citation links.
- Answers cite available source IDs correctly.
- Price-source gaps return `limited` or `abstain` warnings.
- Frontend text has the expected labels `命中段` / `概括` and no placeholder/debug leakage.

`make package` creates `/Users/Zhuanz/Desktop/01-mining-rag-pipeline-tool.zip`.

## Boundaries

This is a complete interview MVP, not a production market-data terminal. It does not bypass login walls, Cloudflare checks, paid feeds or rate limits. For formal trading/investment use, connect licensed LME/SHFE/Mysteel data and re-run the same indexing/query chain.
