from __future__ import annotations

import argparse
import json

from pipeline.clean import dedupe_documents
from pipeline.collectors import collect_all, write_collection_snapshot
from pipeline.splitter import split_documents
from pipeline.store import LocalVectorStore


def run_ingest(out: str = "data/runtime", per_source: int = 200, fixture: bool = False) -> dict:
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
        "index_dir": str(store.index_dir),
    }
    return summary


def _counts(docs):
    output = {}
    for doc in docs:
        output[doc.source_type] = output.get(doc.source_type, 0) + 1
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="data/runtime")
    parser.add_argument("--per-source", type=int, default=200)
    parser.add_argument("--fixture", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run_ingest(args.out, args.per_source, args.fixture), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

