.PHONY: ingest fixture-ingest serve eval test qa package demo

ingest:
	python3 -m pipeline.ingest --out data/runtime --per-source 20

fixture-ingest:
	python3 -m pipeline.ingest --out data/runtime --per-source 50 --fixture

serve:
	uvicorn serve.app:app --host 0.0.0.0 --port 8001

eval:
	python3 -m eval.run_eval --index-dir data/runtime

test:
	PYTHONPATH=. pytest -q

qa:
	PYTHONPATH=. python3 -m qa.run_qa

package:
	rm -f /Users/Zhuanz/Desktop/01-mining-rag-pipeline-tool.zip
	cd .. && zip -qr /Users/Zhuanz/Desktop/01-mining-rag-pipeline-tool.zip 01-mining-rag-pipeline -x '01-mining-rag-pipeline/.git/*' '01-mining-rag-pipeline/.env' '01-mining-rag-pipeline/.env.local' '01-mining-rag-pipeline/.venv/*' '01-mining-rag-pipeline/.pytest_cache/*' '01-mining-rag-pipeline/**/__pycache__/*' '01-mining-rag-pipeline/data/runtime/*' '01-mining-rag-pipeline/outputs/*.log'

demo: ingest eval
	python3 -m serve.query_engine "近 7 天澳洲锂出口政策有何变化?"
