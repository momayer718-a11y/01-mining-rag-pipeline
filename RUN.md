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
export MODEL_BASE_URL=https://apihub.agnes-ai.com/v1
export MODEL_NAME=agnes-2.0-flash
```

The key is read from the environment only. Do not write real keys into project files, Docker Compose, README, QA reports or release zips.

## Query Smoke Test

```bash
curl -s http://localhost:8001/query \
  -H 'content-type: application/json' \
  -d '{"question":"近 7 天澳洲锂出口政策有何变化?","top_k":5,"days":7}' | jq
```

Expected shape: Chinese `answer`, numbered `[1]` citations, citation rows with `命中段` / `概括` on the Web console, and a folded backend JSON block for audit.

If LME/SHFE/Mysteel or DISR/S&P are inaccessible in the current network, expected status is `limited` or `abstain` with warnings. That is intentional; the app should not fake prices or policy changes.

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
make package
```

`make qa` writes `QA_REPORT.md` and `qa/reports/*.json`. `make package` writes `/Users/Zhuanz/Desktop/01-mining-rag-pipeline-tool.zip`.
