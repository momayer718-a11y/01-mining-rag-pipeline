# Mining RAG Pipeline Console

## 项目简介

Mining RAG Pipeline Console 是一个面向矿业研究场景的本地 RAG 问答系统。它将矿业新闻、政策资料、价格线索和项目事件组织成可检索知识库，并通过 FastAPI 与 Web 控制台提供中文或中英混合问答、引用展示、检索审计和模型增强回答。

Mining RAG Pipeline Console is a local RAG assistant for mining research workflows. It organizes mining news, policy material, price context and project events into a searchable knowledge base, then exposes Chinese or mixed Chinese/English Q&A through a FastAPI API and Web console with citations, retrieval audit traces and optional model-enhanced answers.

## 核心能力 / Key Features

- 快速首答 / Fast first answer: `/query` 先返回基于引用的快速答案，DeepSeek V4 Pro 增强回答作为第二阶段执行。
- 宽泛问题检索 / Broad-query retrieval: 面向矿业政策、价格趋势、项目新闻和供应链风险自动展开查询并重排证据。
- 可审计引用 / Auditable citations: 每个答案保留 citation、命中段、中文概括、选中原因和 `retrieval_trace`。
- 双语问题支持 / Bilingual query support: 支持中文、英文和中英混合矿业问题。
- 本地可运行 / Local runnable stack: 提供采集、索引、查询、Web 控制台、QA、评测和 Docker 运行路径。

## 最新验证 / Latest Validation

| Check | Result |
| --- | --- |
| Unit tests | 17 passed |
| Industry QA | 25/25 backend cases passed |
| 50-case generalization eval | 50/50 passed |
| First-answer latency | avg 1119.56 ms, p95 1971.3 ms |

## What It Does

- Crawls original RSS/HTML sources and writes `data/runtime/collection_snapshot.json`.
- Uses HTTP cache, retry, timeout, source rate limiting and optional local proxy variables for normal network egress.
- Imports public-visible/proxy price rows and optional licensed price rows from `data/raw/prices/*.csv` or authorized JSON/CSV API endpoints.
- Deduplicates by `source + canonical_url + content_hash`.
- Splits documents into chunks and indexes them in a local hybrid lexical/BM25-style store with metadata, phrase, source-type and recency boosts.
- Parses mining questions by commodity, region, intent and time window.
- Expands broad mining questions into multiple source/commodity/policy searches before reranking.
- Filters evidence so source links must match the requested commodity/region; irrelevant fixture or debug notes are not used as answer facts.
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

This is a local mining research assistant, not a financial-advice product or production trading terminal. For formal trading or investment workflows, connect licensed market-data services and run the same indexing/query chain under production controls.
