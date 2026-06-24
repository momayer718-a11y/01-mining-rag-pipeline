from __future__ import annotations

from pathlib import Path

from eval.run_eval import run_eval
from pipeline.fixtures import generate_fixture_documents
from pipeline.ingest import run_ingest
from serve.query_engine import query


def test_fixture_generates_600_documents() -> None:
    docs = generate_fixture_documents(per_source=200)
    assert len(docs) == 600
    assert {doc.source_type for doc in docs} == {"news", "policy", "price"}


def test_ingest_and_query(tmp_path: Path) -> None:
    index_dir = tmp_path / "runtime"
    summary = run_ingest(str(index_dir), per_source=20, fixture=True)
    assert summary["documents"] == 60
    assert summary["chunks"] >= 60
    result = query("近 7 天澳洲锂出口政策有何变化?", top_k=5, days=7, index_dir=str(index_dir))
    assert result["status"] == "ok"
    assert result["hits"]
    assert "澳洲" in result["answer"] or "锂" in result["answer"]
    assert "elapsed_ms" in result


def test_unsupported_region_abstains(tmp_path: Path) -> None:
    index_dir = tmp_path / "runtime"
    run_ingest(str(index_dir), per_source=20, fixture=True)
    result = query("刚果钴矿政策风险如何?", top_k=5, index_dir=str(index_dir))
    assert result["status"] == "abstain"
    assert not result["hits"]
    assert result["warnings"]


def test_eval_runs(tmp_path: Path) -> None:
    index_dir = tmp_path / "runtime"
    run_ingest(str(index_dir), per_source=20, fixture=True)
    metrics = run_eval(index_dir=str(index_dir))
    assert metrics["total"] == 20
    assert metrics["recall@5"] >= 0.6
    assert metrics["answer_faithfulness"] == 1.0
