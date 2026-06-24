from __future__ import annotations

import re

from pipeline.data_models import ChunkRecord, DocumentRecord


def tokenize(text: str) -> list[str]:
    tokens = re.findall(r"[A-Za-z0-9%]+|[\u4e00-\u9fff]", text.lower())
    stop = {"the", "and", "for", "with", "this", "that", "from", "are", "was", "were"}
    return [token for token in tokens if token not in stop]


def split_documents(docs: list[DocumentRecord], chunk_words: int = 90, overlap: int = 20) -> list[ChunkRecord]:
    chunks: list[ChunkRecord] = []
    for doc in docs:
        words = doc.content.split()
        if not words:
            continue
        step = max(1, chunk_words - overlap)
        for index, start in enumerate(range(0, len(words), step)):
            text = " ".join(words[start : start + chunk_words])
            if not text:
                continue
            chunks.append(
                ChunkRecord(
                    chunk_id=f"{doc.id}:{index}",
                    document_id=doc.id,
                    text=text,
                    tokens=tokenize(f"{doc.title} {text}"),
                    metadata={
                        "source": doc.source,
                        "source_type": doc.source_type,
                        "title": doc.title,
                        "url": doc.url,
                        "published_at": doc.published_at,
                        **doc.metadata,
                    },
                )
            )
    return chunks

