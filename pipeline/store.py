from __future__ import annotations

import json
import math
import re
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
        query_tokens = _expanded_query_tokens(question)
        query_counts = Counter(query_tokens)
        doc_freq = _document_frequency(chunks)
        avg_len = sum(len(chunk.tokens) for chunk in chunks) / max(1, len(chunks))
        now = date.today()
        scored = []
        for chunk in chunks:
            if days is not None and not _within_days(chunk.metadata.get("published_at", ""), now, days):
                continue
            token_counts = Counter(chunk.tokens)
            lexical = _bm25_score(query_counts, token_counts, doc_freq, len(chunks), len(chunk.tokens), avg_len)
            lexical += _metadata_boost(question, chunk)
            lexical += _phrase_boost(question, chunk)
            lexical += _soft_keyword_score(question, chunk)
            recency = _recency_boost(chunk.metadata.get("published_at", ""), now)
            score = lexical * (1.0 + recency)
            if score > 0:
                scored.append({"score": round(score, 4), "chunk": chunk.to_dict(), "search_debug": {"hybrid_score": round(score, 4)}})
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


def _document_frequency(chunks: list[ChunkRecord]) -> Counter:
    freq: Counter[str] = Counter()
    for chunk in chunks:
        freq.update(set(chunk.tokens))
    return freq


def _expanded_query_tokens(question: str) -> list[str]:
    tokens = tokenize(question)
    lowered = question.lower()
    expansions = {
        "稀土": ["rare", "earth", "quota", "traceability", "export", "control", "policy"],
        "traceability": ["rare", "earth", "quota", "regulatory", "supervision"],
        "pilbara": ["pilbara", "minerals", "spodumene", "shipment", "port", "concentrate", "offtake"],
        "出货": ["shipment", "shipments", "shipping", "port", "logistics"],
        "发运": ["shipment", "shipments", "shipping", "port", "logistics"],
        "碳酸锂": ["lithium", "carbonate", "shfe", "price"],
        "shfe": ["lithium", "carbonate", "price"],
        "mysteel": ["iron", "ore", "steel", "blast", "furnace", "price"],
        "铁矿石": ["iron", "ore", "steel", "blast", "furnace", "mysteel"],
        "锌": ["zinc", "lme", "fred", "price"],
        "价格": ["price", "trend", "fred", "proxy"],
        "政策": ["policy", "regulation", "permitting", "quota", "approval"],
    }
    for needle, words in expansions.items():
        if needle in lowered or needle in question:
            tokens.extend(words)
    return tokens


def _bm25_score(query_counts: Counter, token_counts: Counter, doc_freq: Counter, total_docs: int, doc_len: int, avg_len: float) -> float:
    if not query_counts:
        return 0.0
    k1 = 1.4
    b = 0.72
    score = 0.0
    length_norm = k1 * (1 - b + b * doc_len / max(avg_len, 1.0))
    for token, query_count in query_counts.items():
        tf = token_counts.get(token, 0)
        if tf <= 0:
            continue
        idf = math.log(1 + (total_docs - doc_freq.get(token, 0) + 0.5) / (doc_freq.get(token, 0) + 0.5))
        score += idf * ((tf * (k1 + 1)) / (tf + length_norm)) * min(query_count, 2)
    return score


def _metadata_boost(question: str, chunk: ChunkRecord) -> float:
    q = question.lower()
    meta = chunk.metadata
    title = str(meta.get("title", "")).lower()
    source = str(meta.get("source", "")).lower()
    commodity = str(meta.get("commodity", "")).lower()
    region = str(meta.get("region", "")).lower()
    haystack = f"{title} {source} {commodity} {region}"
    boost = 0.0
    for term in tokenize(question):
        if term in haystack:
            boost += 1.2 if term in title else 0.7
    source_type = str(meta.get("source_type", "")).lower()
    if any(term in q for term in ["价格", "price", "lme", "shfe", "mysteel"]) and source_type == "price":
        boost += 3.5
    if any(term in q for term in ["政策", "policy", "quota", "traceability", "permitting", "审批"]) and source_type == "policy":
        boost += 3.0
    if any(term in q for term in ["新闻", "news", "project", "项目", "shipment", "出货"]) and source_type == "news":
        boost += 2.2
    if "pilbara" in q and ("pilbara" in haystack or "pilbara" in title):
        boost += 5.0
    if ("rare earth" in q or "稀土" in question) and commodity == "rare earth":
        boost += 4.0
    return boost


def _phrase_boost(question: str, chunk: ChunkRecord) -> float:
    q = question.lower()
    text = f"{chunk.metadata.get('title', '')} {chunk.text}".lower()
    boost = 0.0
    phrase_groups = [
        (("rare earth", "traceability"), ["rare earth", "traceability", "quota", "export control", "regulatory supervision"]),
        (("pilbara",), ["pilbara", "spodumene", "shipment", "shipments", "port", "concentrate", "offtake"]),
        (("shfe", "碳酸锂"), ["shfe", "lithium carbonate", "lithium", "price"]),
        (("mysteel", "铁矿石"), ["mysteel", "iron ore", "blast furnace", "steel mill", "steel"]),
        (("zinc", "锌"), ["zinc", "lme", "fred", "price", "mine supply"]),
    ]
    for triggers, phrases in phrase_groups:
        if any(trigger in q or trigger in question for trigger in triggers):
            boost += sum(1.8 for phrase in phrases if phrase in text)
    if re.search(r"\b(export|shipment|shipments|shipping)\b", q) or any(term in question for term in ["出口", "出货", "发运"]):
        boost += sum(1.2 for phrase in ["export", "exports", "shipment", "shipments", "shipping", "port"] if phrase in text)
    return boost


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
