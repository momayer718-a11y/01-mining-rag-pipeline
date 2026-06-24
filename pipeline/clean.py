from __future__ import annotations

import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pipeline.data_models import DocumentRecord

TRACKING_PARAMS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "fbclid"}


def normalize_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text


def canonicalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query) if k not in TRACKING_PARAMS])
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), query, ""))


def content_hash(content: str) -> str:
    return hashlib.sha256(normalize_text(content).encode("utf-8")).hexdigest()


def document_id(source: str, url: str, content: str) -> str:
    payload = f"{source}|{canonicalize_url(url)}|{content_hash(content)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def clean_document(doc: DocumentRecord) -> DocumentRecord:
    content = normalize_text(doc.content)
    url = canonicalize_url(doc.url)
    return DocumentRecord(
        id=document_id(doc.source, url, content),
        source=doc.source,
        source_type=doc.source_type,
        title=normalize_text(doc.title),
        url=url,
        published_at=doc.published_at,
        content=content,
        metadata=doc.metadata,
    )


def dedupe_documents(docs: list[DocumentRecord]) -> list[DocumentRecord]:
    seen: set[str] = set()
    output: list[DocumentRecord] = []
    for doc in docs:
        cleaned = clean_document(doc)
        if cleaned.id in seen:
            continue
        seen.add(cleaned.id)
        output.append(cleaned)
    return output

