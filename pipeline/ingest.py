from __future__ import annotations

import argparse
import json

from pipeline.clean import dedupe_documents
from pipeline.collectors import TARGET_PER_SOURCE_TYPE, collect_all, write_collection_snapshot
from pipeline.splitter import split_documents
from pipeline.store import LocalVectorStore


def run_ingest(out: str = "data/runtime", per_source: int = 20, fixture: bool = False) -> dict:
    docs = collect_all(per_source=per_source, force_fixture=fixture)
    clean_docs = dedupe_documents(docs)
    chunks = split_documents(clean_docs)
    store = LocalVectorStore(out)
    store.write(clean_docs, chunks)
    write_collection_snapshot(store.index_dir / "collection_snapshot.json", clean_docs)
    summary = {
        "documents": len(clean_docs),
        "chunks": len(chunks),
        "by_source_type": _counts(clean_docs),
        "usable_evidence_by_source_type": _usable_counts(clean_docs),
        "source_limited_by_source_type": _limited_counts(clean_docs),
        "target_per_source_type": TARGET_PER_SOURCE_TYPE,
        "target_total": TARGET_PER_SOURCE_TYPE * 3,
        "coverage_audit": _coverage_audit(clean_docs),
        "source_modes": _source_modes(clean_docs),
        "source_urls": _source_urls(clean_docs),
        "warnings": _warnings(clean_docs, fixture),
        "source_mode": "fixture" if fixture else "real_first",
        "index_dir": str(store.index_dir),
    }
    return summary


def _counts(docs):
    output = {}
    for doc in docs:
        output[doc.source_type] = output.get(doc.source_type, 0) + 1
    return output


def _source_modes(docs):
    output = {}
    for doc in docs:
        mode = doc.metadata.get("source_mode", "unknown")
        output[mode] = output.get(mode, 0) + 1
    return output


def _usable_counts(docs):
    output = {}
    for doc in docs:
        if _is_usable_evidence(doc):
            output[doc.source_type] = output.get(doc.source_type, 0) + 1
    return output


def _limited_counts(docs):
    output = {}
    for doc in docs:
        if _is_limited_or_audit_only(doc):
            output[doc.source_type] = output.get(doc.source_type, 0) + 1
    return output


def _coverage_audit(docs):
    usable = _usable_counts(docs)
    limited = _limited_counts(docs)
    rows = {}
    for source_type in ("news", "policy", "price"):
        count = usable.get(source_type, 0)
        rows[source_type] = {
            "target": TARGET_PER_SOURCE_TYPE,
            "usable_evidence_count": count,
            "source_limited_count": limited.get(source_type, 0),
            "gap": max(0, TARGET_PER_SOURCE_TYPE - count),
            "meets_target": count >= TARGET_PER_SOURCE_TYPE,
        }
    total = sum(usable.values())
    rows["total"] = {
        "target": TARGET_PER_SOURCE_TYPE * 3,
        "usable_evidence_count": total,
        "source_limited_count": sum(limited.values()),
        "gap": max(0, TARGET_PER_SOURCE_TYPE * 3 - total),
        "meets_target": total >= TARGET_PER_SOURCE_TYPE * 3,
    }
    return rows


def _source_urls(docs):
    urls = {}
    for doc in docs:
        source = doc.source
        urls.setdefault(source, [])
        if doc.url not in urls[source]:
            urls[source].append(doc.url)
    return {source: values[:8] for source, values in urls.items()}


def _warnings(docs, fixture: bool) -> list[str]:
    if fixture:
        return ["fixture_mode_enabled: demo data is synthetic and must not be presented as original-source evidence"]
    warnings = []
    audit = _coverage_audit(docs)
    for source_type in ("news", "policy", "price"):
        if not audit[source_type]["meets_target"]:
            warnings.append(f"{source_type}_target_gap: usable {audit[source_type]['usable_evidence_count']}/{audit[source_type]['target']}, gap {audit[source_type]['gap']}")
    by_mode = _source_modes(docs)
    if by_mode.get("source_limited"):
        warnings.append("source_limited_present: some original sources were reachable only as access/status notes")
    if not any(doc.source_type == "price" and doc.metadata.get("evidence_kind") == "price_row" for doc in docs):
        warnings.append("price_feed_required: no public-visible, proxy or authorized numeric price rows were loaded")
    if any(doc.source == "spglobal" and doc.metadata.get("source_mode") == "source_limited" for doc in docs):
        warnings.append("spglobal_access_limited: S&P Global RSS returned an access restriction in this environment")
    return warnings


def _is_usable_evidence(doc):
    if doc.metadata.get("source_mode") == "fixture":
        return False
    return not _is_limited_or_audit_only(doc)


def _is_limited_or_audit_only(doc):
    if doc.metadata.get("source_mode") == "source_limited":
        return True
    return doc.metadata.get("evidence_kind") in {"source_status", "source_discovery"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="data/runtime")
    parser.add_argument("--per-source", type=int, default=20)
    parser.add_argument("--fixture", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run_ingest(args.out, args.per_source, args.fixture), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
