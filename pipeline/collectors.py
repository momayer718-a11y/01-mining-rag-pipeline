from __future__ import annotations

import email.utils
import json
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

from pipeline.data_models import DocumentRecord
from pipeline.fixtures import generate_fixture_documents

RSS_FEEDS = [
    ("mining.com", "https://www.mining.com/feed/"),
    ("spglobal", "https://www.spglobal.com/commodityinsights/en/rss-feed"),
]

POLICY_URLS = [
    ("disr", "https://www.industry.gov.au/publications/critical-minerals-strategy-2023-2030"),
    ("china-rare-earth", "https://www.cre-ol.com/"),
]


def collect_all(per_source: int = 200, force_fixture: bool = False) -> list[DocumentRecord]:
    if force_fixture:
        return generate_fixture_documents(per_source=per_source)

    docs: list[DocumentRecord] = []
    docs.extend(_collect_news())
    docs.extend(_collect_policy())
    docs.extend(_collect_price_public_notes())
    docs.extend(_fixture_gap(docs, "news", per_source))
    docs.extend(_fixture_gap(docs, "policy", per_source))
    docs.extend(_fixture_gap(docs, "price", per_source))
    return docs


def _fixture_gap(existing: list[DocumentRecord], source_type: str, per_source: int) -> list[DocumentRecord]:
    count = sum(1 for doc in existing if doc.source_type == source_type)
    if count >= per_source:
        return []
    fixture = [doc for doc in generate_fixture_documents(per_source=per_source) if doc.source_type == source_type]
    for doc in fixture:
        doc.metadata["source_mode"] = "fixture_gap_fill"
    return fixture[: per_source - count]


def _collect_news() -> list[DocumentRecord]:
    rows: list[DocumentRecord] = []
    for source, feed_url in RSS_FEEDS:
        try:
            raw = _fetch(feed_url)
            root = ET.fromstring(raw)
            for item in root.findall(".//item")[:200]:
                title = _text(item, "title")
                link = _text(item, "link") or feed_url
                description = re.sub(r"<[^>]+>", " ", _text(item, "description"))
                pub = _parse_date(_text(item, "pubDate"))
                rows.append(
                    DocumentRecord(
                        id="",
                        source=source,
                        source_type="news",
                        title=title,
                        url=link,
                        published_at=pub,
                        content=description or title,
                        metadata={"source_mode": "real_rss", "feed": feed_url},
                    )
                )
        except Exception:
            continue
    return rows


def _collect_policy() -> list[DocumentRecord]:
    rows: list[DocumentRecord] = []
    for source, url in POLICY_URLS:
        try:
            html = _fetch(url)
            title = _first_match(html, r"<title[^>]*>(.*?)</title>") or source
            text = re.sub(r"<[^>]+>", " ", html)
            text = re.sub(r"\s+", " ", text)[:4000]
            rows.append(
                DocumentRecord(
                    id="",
                    source=source,
                    source_type="policy",
                    title=title,
                    url=url,
                    published_at=date.today().isoformat(),
                    content=text,
                    metadata={"source_mode": "real_html"},
                )
            )
        except Exception:
            continue
    return rows


def _collect_price_public_notes() -> list[DocumentRecord]:
    # LME, SHFE and Mysteel price history often requires paid/login access.
    # The MVP records the limitation and relies on fixture_gap for runnable data.
    return []


def _fetch(url: str, timeout: int = 8) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "mining-interview-mvp/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def _text(node: ET.Element, tag: str) -> str:
    child = node.find(tag)
    return (child.text or "").strip() if child is not None else ""


def _parse_date(value: str) -> str:
    if not value:
        return date.today().isoformat()
    try:
        return email.utils.parsedate_to_datetime(value).date().isoformat()
    except Exception:
        return date.today().isoformat()


def _first_match(text: str, pattern: str) -> str:
    match = re.search(pattern, text, flags=re.I | re.S)
    if not match:
        return ""
    return re.sub(r"\s+", " ", match.group(1)).strip()


def write_collection_snapshot(path: Path, docs: list[DocumentRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([doc.to_dict() for doc in docs], ensure_ascii=False, indent=2), encoding="utf-8")

