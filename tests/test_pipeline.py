from __future__ import annotations

from pathlib import Path

import pytest

from eval.run_eval import run_eval
from pipeline.collectors import _collect_authorized_price_csv, _collect_third_party_public_supplements, _fred_price_doc
from pipeline.data_models import DocumentRecord
from pipeline.fixtures import generate_fixture_documents
from pipeline.ingest import run_ingest
from pipeline.splitter import split_documents
from pipeline.store import LocalVectorStore
from scripts.import_price_csv import validate_file
from serve.query_engine import query


def _disable_live_model(monkeypatch) -> None:
    monkeypatch.setenv("MODEL_API_KEY", "")
    monkeypatch.setattr("serve.query_engine.complete_json", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "serve.query_engine.model_metadata",
        lambda: {
            "model_provider": "fallback",
            "model_name": "deterministic-template",
            "model_mode": "fallback",
            "model_reasoning": "not_requested",
            "model_timeout_seconds": 15,
            "model_max_tokens": 1400,
        },
    )


@pytest.fixture(autouse=True)
def disable_live_model_for_tests(monkeypatch) -> None:
    _disable_live_model(monkeypatch)


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


def test_coverage_audit_present_in_ingest(tmp_path: Path) -> None:
    index_dir = tmp_path / "runtime"
    summary = run_ingest(str(index_dir), per_source=20, fixture=True)
    audit = summary["coverage_audit"]
    assert audit["news"]["usable_evidence_count"] == 0
    assert audit["total"]["usable_evidence_count"] == 0
    assert "fixture_mode_enabled" in summary["warnings"][0]


def test_price_csv_validation_and_authorized_ingest(tmp_path: Path, monkeypatch) -> None:
    raw_dir = tmp_path / "data" / "raw" / "prices"
    raw_dir.mkdir(parents=True)
    csv_path = raw_dir / "lme_prices.csv"
    csv_path.write_text(
        "date,commodity,price,currency,unit,source,title,url,region\n"
        "2026-06-24,copper,9800.5,USD,t,LME,LME Copper Official Price,https://www.lme.com/en/Metals/Non-ferrous/LME-Copper,\n",
        encoding="utf-8",
    )
    report = validate_file(csv_path)
    assert report["valid"]
    monkeypatch.chdir(tmp_path)
    docs = _collect_authorized_price_csv()
    assert len(docs) == 1
    assert docs[0].source_type == "price"
    assert docs[0].metadata["source_mode"] == "authorized_csv"
    assert docs[0].metadata["evidence_kind"] == "price_row"


def test_missing_authorized_price_source_does_not_hard_answer(tmp_path: Path) -> None:
    doc = DocumentRecord(
        id="lme-status",
        source="lme",
        source_type="price",
        title="LME copper source access limitation",
        url="https://www.lme.com/en/Metals/Non-ferrous/LME-Copper",
        published_at="2026-06-24",
        content="LME copper price history requires an authorized feed. This record is a source-availability note, not market evidence.",
        metadata={"source_mode": "source_limited", "commodity": "copper", "evidence_kind": "source_status"},
    )
    index_dir = tmp_path / "runtime"
    LocalVectorStore(index_dir).write([doc], split_documents([doc]))
    result = query("LME 铜价格最近趋势如何?", top_k=5, index_dir=str(index_dir))
    assert result["status"] in {"limited", "abstain"}
    assert "direct_price_evidence_not_found" in result["warnings"] or not result["citations"]


def test_public_visible_price_source_answers_price_question(tmp_path: Path) -> None:
    doc = _fred_price_doc(
        {"series": "PCOPPUSDM", "commodity": "copper", "label": "Global copper price", "unit": "USD per metric ton"},
        "2026-05-01",
        "13483.75",
        "https://fred.stlouisfed.org/graph/fredgraph.csv?id=PCOPPUSDM",
    )
    index_dir = tmp_path / "runtime"
    LocalVectorStore(index_dir).write([doc], split_documents([doc]))
    result = query("近 30 天铜价有什么变化", top_k=5, days=30, index_dir=str(index_dir))
    assert result["status"] == "ok"
    assert result["citations"]
    assert result["citations"][0]["source_mode"] == "public_visible_price"
    assert "direct_price_evidence_not_found" not in result["warnings"]


def test_third_party_public_price_is_direct_but_labelled(tmp_path: Path) -> None:
    docs = [doc for doc in _collect_third_party_public_supplements(per_source=20) if doc.source_type == "price" and doc.metadata["commodity"] == "zinc"]
    index_dir = tmp_path / "runtime"
    LocalVectorStore(index_dir).write(docs, split_documents(docs))
    result = query("锌价近期趋势如何?", top_k=5, index_dir=str(index_dir))
    assert result["status"] == "ok"
    assert result["citations"][0]["source_mode"] == "third_party_public"
    assert "direct_price_evidence_not_found" not in result["warnings"]
    assert "不是 LME/SHFE/Mysteel 授权行情" in result["answer"]


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
    assert metrics["answer_faithfulness"] >= 0.9


def test_query_fast_answer_does_not_call_model(monkeypatch, tmp_path: Path) -> None:
    doc = _fred_price_doc(
        {"series": "PCOPPUSDM", "commodity": "copper", "label": "Global copper price", "unit": "USD per metric ton"},
        "2026-05-01",
        "13483.75",
        "https://fred.stlouisfed.org/graph/fredgraph.csv?id=PCOPPUSDM",
    )
    index_dir = tmp_path / "runtime"
    LocalVectorStore(index_dir).write([doc], split_documents([doc]))
    called = {"count": 0}

    def fake_model(*args, **kwargs):
        called["count"] += 1
        return {"ok": False, "payload": None, "error_type": "timeout", "elapsed_ms": 10, "timeout_seconds": 1}

    monkeypatch.setattr("serve.query_engine.complete_json_with_diagnostics", fake_model)
    result = query("近 30 天铜价有什么变化", top_k=5, index_dir=str(index_dir))
    assert called["count"] == 0
    assert result["answer_stage"] == "fast_answer"
    assert result["retrieval_trace"]["model_attempted"] is False


def test_query_enhance_timeout_keeps_fast_answer(monkeypatch, tmp_path: Path) -> None:
    doc = _fred_price_doc(
        {"series": "PCOPPUSDM", "commodity": "copper", "label": "Global copper price", "unit": "USD per metric ton"},
        "2026-05-01",
        "13483.75",
        "https://fred.stlouisfed.org/graph/fredgraph.csv?id=PCOPPUSDM",
    )
    index_dir = tmp_path / "runtime"
    LocalVectorStore(index_dir).write([doc], split_documents([doc]))
    monkeypatch.setattr(
        "serve.query_engine.complete_json_with_diagnostics",
        lambda *args, **kwargs: {"ok": False, "payload": None, "error_type": "timeout", "elapsed_ms": 12, "timeout_seconds": 1},
    )
    result = query("近 30 天铜价有什么变化", top_k=5, index_dir=str(index_dir), enhance=True)
    assert result["answer_stage"] == "model_timeout"
    assert result["model_status"] == "timeout"
    assert result["fast_answer"]
    assert result["retrieval_trace"]["model_error_type"] == "timeout"


def test_model_thinking_disabled_by_default(monkeypatch) -> None:
    monkeypatch.delenv("MODEL_THINKING_ENABLED", raising=False)
    from serve.model_client import model_metadata

    meta = model_metadata()
    if meta["model_mode"] == "live":
        assert meta["model_reasoning"] == "not_requested"


def test_broad_australia_export_policy_has_trace_and_limited_quality(monkeypatch) -> None:
    _disable_live_model(monkeypatch)
    result = query("最近澳洲矿石出口有哪些政策改动", top_k=5, index_dir="data/runtime")
    assert result["intent"]["domain"] == "broad_mining"
    assert result["intent"]["days"] == 30
    assert result["status"] in {"limited", "abstain"}
    assert "retrieval_trace" in result
    assert len(result["retrieval_trace"]["searches"]) >= 4
    assert result["retrieval_trace"]["dropped"]
    assert not any("Publications Office of the European Union" in c["matched_excerpt_en"] for c in result["citations"])
    assert "direct_policy_change_evidence_not_found" in result["warnings"] or "broad_query_evidence_thin" in result["warnings"]


def test_recent_broad_query_ignores_stale_frontend_days(monkeypatch) -> None:
    _disable_live_model(monkeypatch)
    result = query("最近澳洲关键矿产政策有哪些变化", top_k=5, days=7, index_dir="data/runtime")
    assert result["intent"]["days"] == 30
    assert result["retrieval_trace"]["requested_days"] == 7


def test_australia_lithium_export_news_remains_limited_without_direct_export(monkeypatch) -> None:
    _disable_live_model(monkeypatch)
    result = query("近 7 天澳洲锂出口有哪些新闻", top_k=5, days=7, index_dir="data/runtime")
    assert result["status"] == "limited"
    assert result["citations"]
    assert "direct_export_evidence_not_found" in result["warnings"]


def test_copper_price_uses_public_visible_or_proxy_price_source(monkeypatch) -> None:
    _disable_live_model(monkeypatch)
    result = query("近 30 天铜价有什么变化", top_k=5, days=30, index_dir="data/runtime")
    assert result["status"] in {"ok", "limited"}
    if result["citations"]:
        assert result["citations"][0]["source_type"] == "price"
        assert "direct_price_evidence_not_found" not in result["warnings"]


def test_pilbara_shipment_has_multiple_citations_or_limited(monkeypatch) -> None:
    _disable_live_model(monkeypatch)
    result = query("Pilbara 出货受哪些约束?", top_k=5, index_dir="data/runtime")
    assert result["status"] == "limited" or len(result["citations"]) >= 2
    assert "rerank" in result["retrieval_trace"]
