from __future__ import annotations

import email.utils
import html
import json
import os
import re
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path
from urllib.parse import urljoin

import requests
import urllib3

from pipeline.data_models import DocumentRecord
from pipeline.fixtures import generate_fixture_documents

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

RSS_FEEDS = [
    ("mining.com", "https://www.mining.com/feed/"),
    ("mining.com", "https://www.mining.com/commodity/lithium/feed/"),
    ("mining.com", "https://www.mining.com/commodity/copper/feed/"),
    ("mining.com", "https://www.mining.com/commodity/nickel/feed/"),
    ("mining.com", "https://www.mining.com/commodity/zinc/feed/"),
    ("mining.com", "https://www.mining.com/commodity/rare-earth/feed/"),
    ("mining.com", "https://www.mining.com/commodity/cobalt/feed/"),
    ("mining.com", "https://www.mining.com/commodity/uranium/feed/"),
    ("mining.com", "https://www.mining.com/commodity/graphite/feed/"),
    ("mining.com", "https://www.mining.com/commodity/iron-ore/feed/"),
    ("mining.com", "https://www.mining.com/region/australia/feed/"),
    ("spglobal", "https://www.spglobal.com/energy/en/rss/metals"),
]

POLICY_URLS = [
    ("disr", "https://www.industry.gov.au/publications/critical-minerals-strategy-2023-2030"),
]

CHINA_RARE_EARTH_LISTS = [
    ("china-rare-earth", "https://www.regcc.cn/zgxtjt/jtnew/list.shtml"),
    ("china-rare-earth", "https://www.regcc.cn/zgxtjt/cydt/list.shtml"),
    ("china-rare-earth", "https://www.regcc.cn/zgxtjt/gsgg/list.shtml"),
]

PRICE_SOURCE_URLS = [
    ("lme", "copper", "https://www.lme.com/en/Metals/Non-ferrous/LME-Copper"),
    ("lme", "nickel", "https://www.lme.com/en/Metals/Non-ferrous/LME-Nickel"),
    ("lme", "zinc", "https://www.lme.com/en/Metals/Non-ferrous/LME-Zinc"),
    ("shfe", "lithium", "https://www.shfe.com.cn/en/products/nonferrous/lithiumcarbonate/"),
    ("mysteel", "iron ore", "https://www.mysteel.net/"),
]


def collect_all(per_source: int = 200, force_fixture: bool = False) -> list[DocumentRecord]:
    if force_fixture:
        return generate_fixture_documents(per_source=per_source)

    docs: list[DocumentRecord] = []
    docs.extend(_collect_news(per_source))
    docs.extend(_collect_policy(per_source))
    docs.extend(_collect_price_public_notes())
    return docs


def _collect_news(per_source: int) -> list[DocumentRecord]:
    rows: list[DocumentRecord] = []
    for source, feed_url in RSS_FEEDS:
        try:
            raw = _fetch(feed_url)
            root = ET.fromstring(raw)
            items = root.findall(".//item")[: max(2, min(per_source, 5))]
            if not items:
                rows.append(_source_limited_doc(source, "news", feed_url, "RSS feed returned no items."))
            for item in items:
                title = _text(item, "title")
                link = _text(item, "link") or feed_url
                description = _html_to_text(_text(item, "description"))
                encoded = _html_to_text(_text(item, "{http://purl.org/rss/1.0/modules/content/}encoded"))
                pub = _parse_date(_text(item, "pubDate"))
                content = encoded or description or title
                rows.append(
                    DocumentRecord(
                        id="",
                        source=source,
                        source_type="news",
                        title=title,
                        url=link,
                        published_at=pub,
                        content=content,
                        metadata={
                            "source_mode": "real_rss",
                            "feed": feed_url,
                            "origin_url": link,
                            "commodity": _detect_commodity(f"{title} {content}"),
                            "region": _detect_region(f"{title} {content}"),
                            "evidence_kind": "source_text",
                        },
                    )
                )
        except Exception as exc:
            rows.append(_source_limited_doc(source, "news", feed_url, f"RSS fetch failed: {type(exc).__name__}."))
    return rows


def _collect_policy(per_source: int) -> list[DocumentRecord]:
    rows: list[DocumentRecord] = []
    for source, url in POLICY_URLS:
        try:
            html = _fetch(url)
            title = _first_match(html, r"<title[^>]*>(.*?)</title>") or source
            text = _html_to_text(html)[:8000]
            rows.append(
                DocumentRecord(
                    id="",
                    source=source,
                    source_type="policy",
                    title=title,
                    url=url,
                    published_at=date.today().isoformat(),
                    content=text,
                    metadata={
                        "source_mode": "real_html",
                        "origin_url": url,
                        "commodity": _detect_commodity(f"{title} {text}") or "critical minerals",
                        "region": "australia",
                        "evidence_kind": "source_text",
                    },
                )
            )
        except Exception as exc:
            rows.append(_source_limited_doc(source, "policy", url, f"Policy page fetch failed: {type(exc).__name__}."))
    rows.extend(_collect_china_rare_earth_policy(per_source))
    return rows


def _collect_price_public_notes() -> list[DocumentRecord]:
    rows: list[DocumentRecord] = []
    for source, commodity, url in PRICE_SOURCE_URLS:
        if os.getenv("FETCH_PRICE_PAGES", "").lower() not in {"1", "true", "yes"}:
            rows.append(
                _source_limited_doc(
                    source,
                    "price",
                    url,
                    (
                        f"{source.upper()} {commodity} price history is treated as a restricted market-data source. "
                        "The MVP keeps the original source URL for audit and requires an authorized feed for numeric trends."
                    ),
                    commodity=commodity,
                )
            )
            continue
        title = f"{source.upper()} {commodity} public price source status"
        try:
            page = _fetch(url, timeout=4)
            text = _html_to_text(page)
            if _looks_blocked(text):
                raise RuntimeError("usable public price values not exposed")
            rows.append(
                DocumentRecord(
                    id="",
                    source=source,
                    source_type="price",
                    title=title,
                    url=url,
                    published_at=date.today().isoformat(),
                    content=text[:4000],
                    metadata={
                        "source_mode": "real_html",
                        "origin_url": url,
                        "commodity": commodity,
                        "evidence_kind": "source_text",
                    },
                )
            )
        except Exception as exc:
            rows.append(_source_limited_doc(source, "price", url, f"{source.upper()} {commodity} price page fetch failed or is restricted: {type(exc).__name__}.", commodity=commodity))
    return rows


def _collect_china_rare_earth_policy(per_source: int) -> list[DocumentRecord]:
    rows: list[DocumentRecord] = []
    max_items = max(3, min(per_source, 8))
    fetch_articles = os.getenv("FETCH_ARTICLE_PAGES", "").lower() in {"1", "true", "yes"}
    for source, list_url in CHINA_RARE_EARTH_LISTS:
        try:
            list_html = _fetch(list_url)
            links = _extract_links(list_html, list_url)
            if not links:
                rows.append(_source_limited_doc(source, "policy", list_url, "China Rare Earth list page returned no article links.", commodity="rare earth"))
                continue
            for title, url, snippet, pub in links[:max_items]:
                content = snippet
                if fetch_articles:
                    try:
                        article_html = _fetch(url, timeout=4)
                        article_text = _html_to_text(article_html)
                        if len(article_text) > len(content):
                            content = article_text[:8000]
                    except Exception:
                        pass
                rows.append(
                    DocumentRecord(
                        id="",
                        source=source,
                        source_type="policy",
                        title=title,
                        url=url,
                        published_at=pub or date.today().isoformat(),
                        content=content,
                        metadata={
                            "source_mode": "real_html",
                            "origin_url": url,
                            "list_url": list_url,
                            "commodity": "rare earth",
                            "region": "china",
                            "evidence_kind": "source_text",
                        },
                    )
                )
        except Exception as exc:
            rows.append(_source_limited_doc(source, "policy", list_url, f"China Rare Earth list fetch failed: {type(exc).__name__}.", commodity="rare earth"))
    return rows


def _fetch(url: str, timeout: int = 10) -> str:
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; mining-interview-mvp/0.2; +https://github.com/dianyxx/01-mining-rag-pipeline)",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    session = requests.Session()
    session.trust_env = True
    response = session.get(url, headers=headers, timeout=(3, timeout), verify=False)
    if response.status_code >= 400:
        text = response.text or response.reason
        raise RuntimeError(f"HTTP {response.status_code}: {text[:120]}")
    if not response.encoding:
        response.encoding = response.apparent_encoding or "utf-8"
    return response.text


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
    return _clean_text(match.group(1))


def _html_to_text(raw: str) -> str:
    text = re.sub(r"(?is)<(script|style|noscript).*?</\1>", " ", raw or "")
    text = re.sub(r"(?is)<br\s*/?>|</p>|</div>|</li>|</h[1-6]>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\b[a-zA-Z]+(?:[-_][a-zA-Z]+)?=(?:\"[^\"]*\"|'[^']*'|[^\s>]+)", " ", text)
    text = re.sub(r"\b(?:div|span|class|href|target|self|blank)\b", " ", text, flags=re.I)
    return _clean_text(html.unescape(text))


def _clean_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    text = re.sub(r"^[>\\s]+", "", text)
    text = re.sub(r"\s+>", " ", text)
    return text.strip()


def _extract_links(raw: str, base_url: str) -> list[tuple[str, str, str, str]]:
    links: list[tuple[str, str, str, str]] = []
    for match in re.finditer(r"<a[^>]+href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", raw, flags=re.I | re.S):
        href = match.group(1)
        if href.startswith("javascript:"):
            continue
        url = urljoin(base_url, href)
        if not re.search(r"/20\d{4}/.+\.shtml$", url):
            continue
        block = match.group(0)
        title = _html_to_text(match.group(2))
        if not title or title in {"详情", "更多"}:
            context = raw[max(0, match.start() - 260) : min(len(raw), match.end() + 620)]
            title = _first_match(context, r"(\d{4}-\d{2}\s+\d{2}\s+[^<]{6,120})") or _html_to_text(context)[:120]
        context = raw[max(0, match.start() - 220) : min(len(raw), match.end() + 900)]
        snippet = _html_to_text(context or block)
        pub = _parse_chinese_date(snippet)
        title = re.sub(r"^\d{4}-\d{2}\s+\d{2}\s*", "", title).strip()
        if title and url not in {existing[1] for existing in links}:
            links.append((title[:160], url, snippet[:3000], pub))
    return links


def _parse_chinese_date(text: str) -> str:
    match = re.search(r"(20\d{2})[-年]\s*(\d{1,2})[-月]\s*(\d{1,2})", text)
    if not match:
        return date.today().isoformat()
    year, month, day = (int(part) for part in match.groups())
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return date.today().isoformat()


def _source_limited_doc(source: str, source_type: str, url: str, reason: str, commodity: str | None = None) -> DocumentRecord:
    source_label = {
        "spglobal": "S&P Global Mining RSS",
        "lme": "LME public price page",
        "shfe": "SHFE public price page",
        "mysteel": "Mysteel public price page",
        "china-rare-earth": "China Rare Earth Group public page",
        "disr": "Australia DISR Critical Minerals Strategy",
    }.get(source, source)
    content = (
        f"{source_label} original source URL: {url}. {reason} "
        "This record is a source-availability note, not market or policy evidence. "
        "Do not infer price moves, policy changes or project facts from this note."
    )
    return DocumentRecord(
        id="",
        source=source,
        source_type=source_type,
        title=f"{source_label} source access limitation",
        url=url,
        published_at=date.today().isoformat(),
        content=content,
        metadata={
            "source_mode": "source_limited",
            "origin_url": url,
            "commodity": commodity or "",
            "evidence_kind": "source_status",
            "warning": reason,
        },
    )


def _looks_blocked(text: str) -> bool:
    lowered = (text or "").lower()
    blocked_tokens = [
        "access denied",
        "just a moment",
        "captcha",
        "应用防火墙",
        "login",
        "sign in",
        "forbidden",
        "do not infer price moves",
    ]
    return any(token in lowered for token in blocked_tokens) or len(_clean_text(text)) < 180


def _detect_commodity(text: str) -> str:
    lowered = text.lower()
    terms = [
        ("lithium", ["lithium", "spodumene", "碳酸锂", "锂"]),
        ("copper", ["copper", "铜"]),
        ("nickel", ["nickel", "镍"]),
        ("zinc", ["zinc", "锌"]),
        ("iron ore", ["iron ore", "铁矿石"]),
        ("rare earth", ["rare earth", "稀土"]),
        ("cobalt", ["cobalt", "钴"]),
        ("gold", ["gold", "黄金"]),
    ]
    for value, needles in terms:
        if any(needle in lowered for needle in needles):
            return value
    return ""


def _detect_region(text: str) -> str:
    lowered = text.lower()
    terms = [
        ("australia", ["australia", "australian", "澳洲", "澳大利亚"]),
        ("pilbara", ["pilbara", "皮尔巴拉"]),
        ("china", ["china", "chinese", "中国"]),
        ("indonesia", ["indonesia", "indonesian", "印尼"]),
        ("peru", ["peru", "秘鲁"]),
        ("drc", ["drc", "congo", "刚果"]),
        ("chile", ["chile", "智利"]),
        ("usa", ["united states", "u.s.", "us ", "美国"]),
    ]
    for value, needles in terms:
        if any(needle in lowered for needle in needles):
            return value
    return ""


def write_collection_snapshot(path: Path, docs: list[DocumentRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([doc.to_dict() for doc in docs], ensure_ascii=False, indent=2), encoding="utf-8")
