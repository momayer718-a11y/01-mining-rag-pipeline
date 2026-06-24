from __future__ import annotations

import json
import math
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

from pipeline.data_models import ChunkRecord, DocumentRecord
from pipeline.splitter import tokenize


class LocalVectorStore:
    def __init__(self, index_dir: str | Path = "data/runtime") -> None:
        self.index_dir = Path(index_dir)
        self.documents_path = self.index_dir / "documents.jsonl"
        self.chunks_path = self.index_dir / "chunks.jsonl"

    def write(self, docs: list[DocumentRecord], chunks: list[ChunkRecord]) -> None:
        self.index_dir.mkdir(parents=True, exist_ok=True)
        _write_jsonl(self.documents_path, [doc.to_dict() for doc in docs])
        _write_jsonl(self.chunks_path, [chunk.to_dict() for chunk in chunks])

    def load_chunks(self) -> list[ChunkRecord]:
        if not self.chunks_path.exists():
            return []
        return [ChunkRecord.from_dict(row) for row in _read_jsonl(self.chunks_path)]

    def search(self, question: str, top_k: int = 5, days: int | None = None) -> list[dict]:
        chunks = self.load_chunks()
        query_tokens = tokenize(question)
        query_counts = Counter(query_tokens)
        now = date.today()
        scored = []
        for chunk in chunks:
            if days is not None and not _within_days(chunk.metadata.get("published_at", ""), now, days):
                continue
            token_counts = Counter(chunk.tokens)
            lexical = sum(min(query_counts[t], token_counts[t]) for t in query_counts)
            lexical += _soft_keyword_score(question, chunk)
            recency = _recency_boost(chunk.metadata.get("published_at", ""), now)
            score = lexical * (1.0 + recency)
            if score > 0:
                scored.append({"score": round(score, 4), "chunk": chunk.to_dict()})
        scored.sort(key=lambda row: row["score"], reverse=True)
        return scored[:top_k]


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _within_days(value: str, today: date, days: int) -> bool:
    try:
        parsed = datetime.fromisoformat(value).date()
    except Exception:
        return True
    return parsed >= today - timedelta(days=days)


def _recency_boost(value: str, today: date) -> float:
    try:
        age = max(0, (today - datetime.fromisoformat(value).date()).days)
    except Exception:
        return 0
    return max(0.0, 0.25 - math.log1p(age) / 20)


def _soft_keyword_score(question: str, chunk: ChunkRecord) -> float:
    q = question.lower()
    text = f"{chunk.metadata.get('title', '')} {chunk.text}".lower()
    score = 0.0
    phrase_weights = {
        "稀土": [("rare earth", 4.0), ("quota", 2.0), ("traceability", 2.0)],
        "碳酸锂": [("lithium carbonate", 4.0), ("shfe", 3.0), ("stabilized", 2.0)],
        "shfe": [("shfe", 4.0), ("lithium carbonate", 2.0)],
    }
    for needle, weighted_terms in phrase_weights.items():
        if needle in q:
            for term, weight in weighted_terms:
                if term in text:
                    score += weight
    synonyms = {
        "澳洲": ["australia", "australian"],
        "锂": ["lithium", "spodumene"],
        "出口": ["export", "shipments"],
        "政策": ["policy", "strategy", "permitting"],
        "价格": ["price", "trend", "lme", "shfe", "mysteel"],
        "铜": ["copper"],
        "锌": ["zinc"],
        "镍": ["nickel"],
        "铁矿石": ["iron ore"],
        "下游": ["downstream"],
        "加工": ["processing", "refining"],
        "能力": ["capacity"],
        "供应链": ["supply chain"],
        "监管": ["regulatory", "supervision", "environmental"],
        "关注": ["risk", "tracked", "watch"],
        "投资": ["investment"],
        "矿业": ["mining"],
    }
    for zh, words in synonyms.items():
        if zh in q and any(word in text for word in words):
            score += 1.0
    return score
