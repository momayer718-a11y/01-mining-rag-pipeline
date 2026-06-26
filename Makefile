.PHONY: ingest ingest-full fixture-ingest import-price-csv serve eval test qa package demo

ingest:
	python3 -m pipeline.ingest --out data/runtime --per-source 20

ingest-full:
	FETCH_PRICE_PROXIES=0 FETCH_RETRIES=1 FETCH_CONNECT_TIMEOUT=2 REQUEST_DELAY_SECONDS=0.02 SOURCE_WINDOW_DAYS=30 TARGET_PER_SOURCE_TYPE=200 python3 -m pipeline.ingest --out data/runtime_full --per-source 200

fixture-ingest:
	python3 -m pipeline.ingest --out data/runtime --per-source 50 --fixture

import-price-csv:
	python3 scripts/import_price_csv.py $(CSV) --strict

serve:
	uvicorn serve.app:app --host 0.0.0.0 --port 8001

eval:
	PYTHONPATH=. python3 -m eval.run_eval --index-dir data/runtime --gt-path eval/generalization_50.json --out-path eval/generalization_50_report.json --progress

test:
	PYTHONPATH=. pytest -q

qa:
	PYTHONPATH=. python3 -m qa.run_qa

package:
	rm -f /Users/Zhuanz/Desktop/01-mining-rag-pipeline-tool.zip
	cd .. && zip -qr /Users/Zhuanz/Desktop/01-mining-rag-pipeline-tool.zip 01-mining-rag-pipeline -x '01-mining-rag-pipeline/.git/*' '01-mining-rag-pipeline/.env' '01-mining-rag-pipeline/.env.local' '01-mining-rag-pipeline/.venv/*' '01-mining-rag-pipeline/.pytest_cache/*' '01-mining-rag-pipeline/**/__pycache__/*' '01-mining-rag-pipeline/data/cache/*' '01-mining-rag-pipeline/data/runtime*/*' '01-mining-rag-pipeline/data/raw/prices/*.csv' '01-mining-rag-pipeline/outputs/*.log' '01-mining-rag-pipeline/outputs/server.*'

demo: ingest eval
	python3 -m serve.query_engine "近 7 天澳洲锂出口政策有何变化?"
