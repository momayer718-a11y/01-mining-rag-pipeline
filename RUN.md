# RUN

## Local 5 Minute Check

```bash
cd /Users/Zhuanz/Desktop/面试题目MVP/01-mining-rag-pipeline
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
make ingest
make serve
```

Open `http://localhost:8001`.

`make ingest` uses original public source URLs where reachable. It does not use fixture records.

The current runtime can be rebuilt with public-visible/proxy price rows. `/stats` displays the coverage audit: news, policy and price each target 200 usable evidence records; LME/SHFE/Mysteel pages that block programmatic access are retained as source-limited audit links.

## Full Interview Quantity Check

```bash
make ingest-full
python3 - <<'PY'
import json
from pathlib import Path
snapshot = json.loads(Path("data/runtime_full/collection_snapshot.json").read_text())
print("documents", len(snapshot))
PY
```

The ingestion summary prints `coverage_audit`:

- `usable_evidence_count`: real source text, public-visible/proxy price rows or authorized price rows that can support answers.
- `source_limited_count`: original URLs and failure/status notes, not answer evidence.
- `gap`: how many usable records are still missing versus the 200/type target.

If your network needs a local proxy for normal public-source access:

```bash
export SOURCE_HTTPS_PROXY=http://127.0.0.1:7890
export SOURCE_HTTP_PROXY=http://127.0.0.1:7890
```

This is for legitimate local network routing only. The collector does not bypass CAPTCHA, login walls, paid feeds or Cloudflare challenges.

## Price Data

Public-visible/proxy price collection is enabled by default. For production-grade official exchange/vendor prices, validate and import licensed LME/SHFE/Mysteel CSV exports:

```bash
python3 scripts/import_price_csv.py /path/to/lme_prices.csv --strict
PYTHONPATH=. python3 -m pipeline.ingest --out data/runtime_full --per-source 200
```

For authorized APIs that return JSON rows or CSV with the same schema:

```bash
export AUTHORIZED_PRICE_API_URLS="https://vendor.example/lme.csv,https://vendor.example/shfe.json"
export AUTHORIZED_PRICE_API_TOKEN="your_vendor_token"
PYTHONPATH=. python3 -m pipeline.ingest --out data/runtime_full --per-source 200
```

Optional one-shot CLI demo:

```bash
python3 -m serve.query_engine "近 7 天澳洲锂出口政策有何变化?"
```

## Docker

```bash
cd /Users/Zhuanz/Desktop/面试题目MVP/01-mining-rag-pipeline
docker compose up --build
```

Open `http://localhost:8001` after startup.

## Optional Live Model

```bash
cp .env.example .env
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

The app reads `MODEL_API_KEY` first. If it is empty, it can decrypt `config/model_api_key.enc.json` with `MODEL_KEY_PASSPHRASE`; commit only the encrypted JSON, not a plaintext `.env`.

Normal `/query` calls return a fast citation-based answer first. Send `enhance=true` to run DeepSeek V4 Pro as a second-stage enhancer. Thinking is disabled by default for speed; enable `MODEL_THINKING_ENABLED=1` only for deep analysis or live model-output checks.

## Query Smoke Test

```bash
curl -s http://localhost:8001/query \
  -H 'content-type: application/json' \
  -d '{"question":"近 7 天澳洲锂出口政策有何变化?","top_k":5}' | jq
```

Expected shape: Chinese `answer`, numbered `[1]` citations, citation rows with `命中段` / `概括` on the Web console, and a folded backend JSON block for audit.

If LME/SHFE/Mysteel or DISR/S&P are inaccessible in the current network, those pages remain source-limited audit records. Public-visible/proxy price rows can still support MVP price answers, but they are labelled separately from official licensed feeds.

## Fixture Demo

```bash
make fixture-ingest
python3 -m serve.query_engine "近 7 天澳洲锂出口政策有何变化?"
```

Fixture mode is for offline deterministic tests only. Normal Docker, `make ingest` and the Web console use real-first ingestion.

## QA And Package

```bash
make test
make qa
PYTHONPATH=. python3 -m eval.run_eval --index-dir data/runtime --gt-path eval/generalization_50.json --out-path eval/generalization_50_report.json --progress
make package
```

`make qa` writes `QA_REPORT.md` and `qa/reports/*.json`. `make package` writes `/Users/Zhuanz/Desktop/01-mining-rag-pipeline-tool.zip`.
