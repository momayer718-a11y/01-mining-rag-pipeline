.PHONY: ingest serve eval test qa package demo

ingest:
	python3 -m pipeline.ingest --out data/runtime --per-source 200 --fixture

serve:
	uvicorn serve.app:app --host 0.0.0.0 --port 8001

eval:
	python3 -m eval.run_eval --index-dir data/runtime

test:
	pytest -q

qa:
	python3 -m qa.run_qa

package:
	cd .. && zip -qr /Users/Zhuanz/Desktop/01-mining-rag-pipeline-tool.zip 01-mining-rag-pipeline -x '01-mining-rag-pipeline/.pytest_cache/*' '01-mining-rag-pipeline/**/__pycache__/*'

demo: ingest eval
	python3 -m serve.query_engine "近 7 天澳洲锂出口政策有何变化?"
