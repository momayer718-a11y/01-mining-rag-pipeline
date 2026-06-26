from __future__ import annotations

import email.utils
import html
import json
import os
import re
import time
import xml.etree.ElementTree as ET
from csv import DictReader
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from urllib.parse import quote, urlencode, urljoin

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
    ("northern-miner", "https://www.northernminer.com/feed/"),
    ("mining-technology", "https://www.mining-technology.com/news/feed/"),
    ("international-mining", "https://im-mining.com/feed/"),
    ("spglobal", "https://www.spglobal.com/energy/en/rss/metals"),
]

POLICY_URLS = [
    ("disr", "https://www.industry.gov.au/publications/critical-minerals-strategy-2023-2030"),
    ("iea", "https://www.iea.org/topics/critical-minerals"),
    ("iea", "https://www.iea.org/reports/critical-minerals-market-review-2023"),
    ("geoscience-australia", "https://www.ga.gov.au/scientific-topics/minerals/critical-minerals"),
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

FEDERAL_REGISTER_TERMS = [
    "mining",
    "mineral",
    "critical minerals",
    "copper",
    "lithium",
    "nickel",
    "cobalt",
    "rare earth",
    "uranium",
    "gold",
    "coal",
    "steel",
    "iron ore",
]

PRICE_PROXY_SYMBOLS = [
    {"symbol": "HG=F", "commodity": "copper", "label": "COMEX copper futures"},
    {"symbol": "ALI=F", "commodity": "aluminum", "label": "COMEX aluminum futures"},
    {"symbol": "ZNC=F", "commodity": "zinc", "label": "COMEX zinc futures"},
    {"symbol": "TIO=F", "commodity": "iron ore", "label": "CME iron ore futures"},
    {"symbol": "GC=F", "commodity": "gold", "label": "COMEX gold futures"},
    {"symbol": "SI=F", "commodity": "silver", "label": "COMEX silver futures"},
    {"symbol": "PL=F", "commodity": "platinum", "label": "NYMEX platinum futures"},
    {"symbol": "PA=F", "commodity": "palladium", "label": "NYMEX palladium futures"},
    {"symbol": "LIT", "commodity": "lithium", "label": "lithium and battery-tech equity proxy"},
    {"symbol": "PICK", "commodity": "diversified mining", "label": "global metals and mining equity proxy"},
    {"symbol": "COPX", "commodity": "copper", "label": "copper miners equity proxy"},
]

FRED_PRICE_SERIES = [
    {"series": "PCOPPUSDM", "commodity": "copper", "label": "Global copper price", "unit": "USD per metric ton"},
    {"series": "PNICKUSDM", "commodity": "nickel", "label": "Global nickel price", "unit": "USD per metric ton"},
    {"series": "PZINCUSDM", "commodity": "zinc", "label": "Global zinc price", "unit": "USD per metric ton"},
    {"series": "PIORECRUSDM", "commodity": "iron ore", "label": "Global iron ore price", "unit": "USD per dry metric ton"},
    {"series": "PALUMUSDM", "commodity": "aluminum", "label": "Global aluminum price", "unit": "USD per metric ton"},
    {"series": "PGOLDUSDM", "commodity": "gold", "label": "Global gold price", "unit": "USD per troy ounce"},
    {"series": "PSILVERUSDM", "commodity": "silver", "label": "Global silver price", "unit": "USD per troy ounce"},
    {"series": "PPLTUSDM", "commodity": "platinum", "label": "Global platinum price", "unit": "USD per troy ounce"},
]

AUTHORIZED_PRICE_DIRS = [
    Path("data/raw/prices"),
    Path("data/authorized_prices"),
]

AUTHORIZED_PRICE_API_URLS = [url.strip() for url in os.getenv("AUTHORIZED_PRICE_API_URLS", "").split(",") if url.strip()]

HTTP_CACHE_DIR = Path(os.getenv("HTTP_CACHE_DIR", "data/cache/http"))
HTTP_CACHE_TTL_HOURS = int(os.getenv("HTTP_CACHE_TTL_HOURS", "12"))
SOURCE_WINDOW_DAYS = int(os.getenv("SOURCE_WINDOW_DAYS", "30"))
REQUEST_DELAY_SECONDS = float(os.getenv("REQUEST_DELAY_SECONDS", "0.12"))
TARGET_PER_SOURCE_TYPE = int(os.getenv("TARGET_PER_SOURCE_TYPE", "200"))
FETCH_RETRIES = int(os.getenv("FETCH_RETRIES", "3"))
FETCH_CONNECT_TIMEOUT = float(os.getenv("FETCH_CONNECT_TIMEOUT", "4"))
_LAST_REQUEST_AT = 0.0


def collect_all(per_source: int = 200, force_fixture: bool = False) -> list[DocumentRecord]:
    if force_fixture:
        return generate_fixture_documents(per_source=per_source)

    docs: list[DocumentRecord] = []
    docs.extend(_collect_news(per_source))
    docs.extend(_collect_policy(per_source))
    docs.extend(_collect_authorized_price_api())
    docs.extend(_collect_authorized_price_csv())
    docs.extend(_collect_fred_price_series(per_source))
    docs.extend(_collect_price_proxy(per_source))
    docs.extend(_collect_third_party_public_supplements(per_source))
    docs.extend(_collect_price_public_notes())
    return docs


def _collect_news(per_source: int) -> list[DocumentRecord]:
    rows: list[DocumentRecord] = []
    for source, feed_url in RSS_FEEDS:
        try:
            raw = _fetch(feed_url)
            root = ET.fromstring(raw)
            items = root.findall(".//item")[:per_source]
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
            html_text = _fetch(url)
            title = _first_match(html_text, r"<title[^>]*>(.*?)</title>") or source
            text = _html_to_text(html_text)[:8000]
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
                        "region": _detect_region(f"{title} {text}"),
                        "evidence_kind": "source_text",
                    },
                )
            )
        except Exception as exc:
            rows.append(_source_limited_doc(source, "policy", url, f"Policy page fetch failed: {type(exc).__name__}."))
    rows.extend(_collect_federal_register_policy(per_source))
    rows.extend(_collect_china_rare_earth_policy(per_source))
    return rows


def _collect_federal_register_policy(per_source: int) -> list[DocumentRecord]:
    rows: list[DocumentRecord] = []
    seen: set[str] = set()
    end = date.today()
    start = end - timedelta(days=SOURCE_WINDOW_DAYS)
    for term in FEDERAL_REGISTER_TERMS:
        if len(rows) >= per_source:
            break
        try:
            payload = _fetch_json(
                "https://www.federalregister.gov/api/v1/documents.json",
                {
                    "per_page": 100,
                    "order": "newest",
                    "conditions[term]": term,
                    "conditions[publication_date][gte]": start.isoformat(),
                    "conditions[publication_date][lte]": end.isoformat(),
                },
            )
        except Exception as exc:
            rows.append(_source_limited_doc("federal-register", "policy", "https://www.federalregister.gov/", f"Federal Register API fetch failed: {type(exc).__name__}."))
            continue
        for item in payload.get("results", []):
            url = item.get("html_url") or item.get("pdf_url") or item.get("json_url")
            if not url or url in seen:
                continue
            title = _clean_text(item.get("title", "Federal Register document"))
            content = _federal_register_content(item, term)
            if not content or not _is_mining_policy_relevant(f"{title} {content}"):
                continue
            seen.add(url)
            rows.append(
                DocumentRecord(
                    id="",
                    source="federal-register",
                    source_type="policy",
                    title=title,
                    url=url,
                    published_at=item.get("publication_date") or date.today().isoformat(),
                    content=content,
                    metadata={
                        "source_mode": "official_api",
                        "origin_url": url,
                        "api_url": item.get("json_url", ""),
                        "policy_query": term,
                        "commodity": _detect_commodity(f"{title} {content}"),
                        "region": _detect_region(f"{title} {content}") or "usa",
                        "agency": _agency_names(item),
                        "evidence_kind": "source_text",
                    },
                )
            )
            if len(rows) >= per_source:
                break
    if not rows:
        rows.append(_source_limited_doc("federal-register", "policy", "https://www.federalregister.gov/", "Federal Register API returned no mining-policy records in the configured window."))
    return rows


def _collect_authorized_price_csv() -> list[DocumentRecord]:
    rows: list[DocumentRecord] = []
    for folder in AUTHORIZED_PRICE_DIRS:
        if not folder.exists():
            continue
        for csv_path in sorted(folder.glob("*.csv")):
            try:
                with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
                    for row in DictReader(handle):
                        commodity = _clean_text(row.get("commodity", ""))
                        value_date = _clean_text(row.get("date", ""))
                        price = _clean_text(row.get("price", ""))
                        if not commodity or not _valid_iso_date(value_date) or not _valid_decimal(price):
                            continue
                        source = _clean_text(row.get("source", "")) or csv_path.stem
                        unit = _clean_text(row.get("unit", "")) or "reported unit"
                        currency = _clean_text(row.get("currency", "")) or "reported currency"
                        url = _clean_text(row.get("url", "")) or f"authorized-csv://{csv_path.name}/{commodity}/{value_date}"
                        title = _clean_text(row.get("title", "")) or f"{source} {commodity} authorized price {value_date}"
                        content = (
                            f"Authorized price feed row for {commodity} on {value_date}: price {price} {currency} per {unit}. "
                            f"Source {source}. This row was supplied through a licensed or user-authorized CSV file and can be used as numeric price evidence."
                        )
                        rows.append(
                            DocumentRecord(
                                id="",
                                source=source,
                                source_type="price",
                                title=title,
                                url=url,
                                published_at=value_date,
                                content=content,
                                metadata={
                                    "source_mode": "authorized_csv",
                                    "origin_url": url,
                                    "commodity": _detect_commodity(commodity) or commodity.lower(),
                                    "region": _detect_region(row.get("region", "")),
                                    "price": price,
                                    "currency": currency,
                                    "unit": unit,
                                    "import_file": str(csv_path),
                                    "evidence_kind": "price_row",
                                },
                            )
                        )
            except Exception:
                continue
    return rows


def _collect_authorized_price_api() -> list[DocumentRecord]:
    rows: list[DocumentRecord] = []
    if not AUTHORIZED_PRICE_API_URLS:
        return rows
    headers = {"Accept": "application/json,text/csv;q=0.9,*/*;q=0.5"}
    token = os.getenv("AUTHORIZED_PRICE_API_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    for api_url in AUTHORIZED_PRICE_API_URLS:
        try:
            session = requests.Session()
            session.trust_env = True
            response = session.get(api_url, headers=headers, timeout=(4, 30), verify=False)
            if response.status_code >= 400:
                raise RuntimeError(f"HTTP {response.status_code}")
            rows.extend(_price_rows_to_documents(_parse_authorized_price_payload(response.text, response.headers.get("content-type", ""), api_url), f"authorized-api://{api_url}"))
        except Exception:
            continue
    return rows


def _parse_authorized_price_payload(text: str, content_type: str, api_url: str) -> list[dict[str, str]]:
    if "json" in content_type.lower() or text.lstrip().startswith(("{", "[")):
        payload = json.loads(text)
        if isinstance(payload, dict):
            payload = payload.get("rows") or payload.get("data") or payload.get("prices") or []
        if not isinstance(payload, list):
            return []
        return [{str(k): "" if v is None else str(v) for k, v in row.items()} for row in payload if isinstance(row, dict)]
    cache_name = sha256(api_url.encode("utf-8")).hexdigest()[:12]
    temp_path = HTTP_CACHE_DIR / f"authorized_price_api_{cache_name}.csv"
    _write_csv_text(temp_path, text)
    try:
        with temp_path.open("r", encoding="utf-8-sig", newline="") as handle:
            return [dict(row) for row in DictReader(handle)]
    finally:
        try:
            temp_path.unlink()
        except OSError:
            pass


def _price_rows_to_documents(rows: list[dict[str, str]], origin: str) -> list[DocumentRecord]:
    docs: list[DocumentRecord] = []
    for row in rows:
        commodity = _clean_text(row.get("commodity", ""))
        value_date = _clean_text(row.get("date", ""))
        price = _clean_text(row.get("price", ""))
        if not commodity or not _valid_iso_date(value_date) or not _valid_decimal(price):
            continue
        source = _clean_text(row.get("source", "")) or "authorized-price-api"
        unit = _clean_text(row.get("unit", "")) or "reported unit"
        currency = _clean_text(row.get("currency", "")) or "reported currency"
        url = _clean_text(row.get("url", "")) or origin
        title = _clean_text(row.get("title", "")) or f"{source} {commodity} authorized price {value_date}"
        docs.append(
            DocumentRecord(
                id="",
                source=source,
                source_type="price",
                title=title,
                url=url,
                published_at=value_date,
                content=(
                    f"Authorized price API row for {commodity} on {value_date}: price {price} {currency} per {unit}. "
                    f"Source {source}. This row was supplied through a licensed or user-authorized API and can be used as numeric price evidence."
                ),
                metadata={
                    "source_mode": "authorized_api",
                    "origin_url": url,
                    "commodity": _detect_commodity(commodity) or commodity.lower(),
                    "region": _detect_region(row.get("region", "")),
                    "price": price,
                    "currency": currency,
                    "unit": unit,
                    "evidence_kind": "price_row",
                    "api_origin": origin,
                },
            )
        )
    return docs


def _collect_fred_price_series(per_source: int) -> list[DocumentRecord]:
    if os.getenv("FETCH_FRED_PRICE_SERIES", "1").lower() in {"0", "false", "no"}:
        return []
    series_docs: list[list[DocumentRecord]] = []
    for spec in FRED_PRICE_SERIES:
        series_id = spec["series"]
        url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={quote(series_id, safe='')}"
        try:
            csv_text = _fetch(url, timeout=20, accept="text/csv,application/csv,text/plain;q=0.9,*/*;q=0.5")
            parsed_rows = _parse_fred_csv(csv_text, series_id)
        except Exception:
            continue
        docs = [_fred_price_doc(spec, value_date, price, url) for value_date, price in parsed_rows[-per_source:]]
        if docs:
            series_docs.append(docs)
    return _balanced_latest_price_rows(series_docs, per_source)


def _balanced_latest_price_rows(series_docs: list[list[DocumentRecord]], limit: int) -> list[DocumentRecord]:
    rows: list[DocumentRecord] = []
    max_len = max((len(docs) for docs in series_docs), default=0)
    for offset in range(max_len):
        for docs in series_docs:
            if offset < len(docs):
                rows.append(docs[-(offset + 1)])
                if len(rows) >= limit:
                    return rows
    return rows


def _parse_fred_csv(text: str, series_id: str) -> list[tuple[str, str]]:
    cache_name = sha256(f"fred:{series_id}".encode("utf-8")).hexdigest()[:12]
    temp_path = HTTP_CACHE_DIR / f"fred_price_{cache_name}.csv"
    _write_csv_text(temp_path, text)
    rows: list[tuple[str, str]] = []
    try:
        with temp_path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in DictReader(handle):
                value_date = _clean_text(row.get("observation_date", "") or row.get("DATE", "") or row.get("date", ""))
                price = _clean_text(row.get(series_id, "") or row.get("value", "") or row.get("price", ""))
                if _valid_iso_date(value_date) and _valid_decimal(price):
                    rows.append((value_date, price))
    finally:
        try:
            temp_path.unlink()
        except OSError:
            pass
    return rows


def _fred_price_doc(spec: dict[str, str], value_date: str, price: str, url: str) -> DocumentRecord:
    commodity = spec["commodity"]
    source = "fred"
    title = f"FRED {spec['label']} public price {value_date}"
    unit = spec["unit"]
    content = (
        f"Public visible price row for {commodity} on {value_date}: price {price} USD, unit {unit}. "
        f"Source FRED series {spec['series']} ({spec['label']}). "
        "This is a publicly accessible price series/proxy for mining-market analysis, not a licensed LME, SHFE or Mysteel official feed."
    )
    return DocumentRecord(
        id="",
        source=source,
        source_type="price",
        title=title,
        url=url,
        published_at=value_date,
        content=content,
        metadata={
            "source_mode": "public_visible_price",
            "origin_url": url,
            "commodity": commodity,
            "region": "",
            "price": price,
            "currency": "USD",
            "unit": unit,
            "series": spec["series"],
            "evidence_kind": "price_row",
            "official_market_source": False,
        },
    )


def _write_csv_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _collect_price_proxy(per_source: int) -> list[DocumentRecord]:
    rows: list[DocumentRecord] = []
    if os.getenv("FETCH_PRICE_PROXIES", "1").lower() in {"0", "false", "no"}:
        return rows
    rows_per_symbol = max(1, per_source // max(1, len(PRICE_PROXY_SYMBOLS)) + 4)
    earliest = date.today() - timedelta(days=SOURCE_WINDOW_DAYS)
    for spec in PRICE_PROXY_SYMBOLS:
        try:
            payload = _fetch_json(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(spec['symbol'], safe='')}",
                {"range": "45d", "interval": "1d"},
                timeout=12,
            )
            result = (payload.get("chart", {}).get("result") or [None])[0]
            if not result:
                continue
            meta = result.get("meta", {})
            timestamps = result.get("timestamp") or []
            quote_rows = (result.get("indicators", {}).get("quote") or [{}])[0]
            closes = quote_rows.get("close") or []
            opens = quote_rows.get("open") or []
            highs = quote_rows.get("high") or []
            lows = quote_rows.get("low") or []
            volumes = quote_rows.get("volume") or []
            points = []
            for idx, ts in enumerate(timestamps):
                close = closes[idx] if idx < len(closes) else None
                if close is None:
                    continue
                value_date = datetime.fromtimestamp(ts, tz=timezone.utc).date()
                if value_date < earliest:
                    continue
                points.append((value_date, close, _at(opens, idx), _at(highs, idx), _at(lows, idx), _at(volumes, idx)))
            for value_date, close, open_, high, low, volume in points[-rows_per_symbol:]:
                symbol = spec["symbol"]
                url = f"https://finance.yahoo.com/quote/{quote(symbol, safe='')}/history?date={value_date.isoformat()}"
                currency = meta.get("currency", "USD")
                label = spec["label"]
                commodity = spec["commodity"]
                change_text = _price_change_text(open_, close)
                content = (
                    f"Yahoo Finance public market proxy for {label} ({symbol}) on {value_date.isoformat()}: "
                    f"close {close:.4f} {currency}; open {_fmt_num(open_)}; high {_fmt_num(high)}; low {_fmt_num(low)}; volume {_fmt_num(volume)}. "
                    f"{change_text} This is a public proxy price/equity series for mining-market analysis and is not a licensed LME, SHFE or Mysteel feed."
                )
                rows.append(
                    DocumentRecord(
                        id="",
                        source="yahoo-finance",
                        source_type="price",
                        title=f"{label} price proxy {value_date.isoformat()}",
                        url=url,
                        published_at=value_date.isoformat(),
                        content=content,
                        metadata={
                            "source_mode": "price_proxy_public",
                            "origin_url": url,
                            "symbol": symbol,
                            "commodity": commodity,
                            "currency": currency,
                            "unit": "market quote",
                            "evidence_kind": "price_row",
                            "official_market_source": False,
                        },
                    )
                )
        except Exception:
            continue
        if len(rows) >= per_source:
            break
    if not rows:
        rows.append(_source_limited_doc("yahoo-finance", "price", "https://finance.yahoo.com/", "Public price proxy API returned no usable rows."))
    return rows[:per_source]


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
                        "The MVP keeps the original source URL for audit and requires an authorized feed for official numeric trends."
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


def _collect_third_party_public_supplements(per_source: int) -> list[DocumentRecord]:
    if os.getenv("FETCH_THIRD_PARTY_PUBLIC_SUPPLEMENTS", "1").lower() in {"0", "false", "no"}:
        return []
    today = date.today()
    specs = [
        {
            "source": "third-party-public-price",
            "source_type": "price",
            "commodity": "zinc",
            "region": "",
            "title": "Third-party public zinc price context",
            "url": "https://www.investing.com/commodities/zinc-historical-data",
            "content": "Third-party public zinc price context: zinc price trend is commonly assessed against LME zinc, FRED global zinc price series, mine supply disruption risk, smelter demand and construction demand. This is public market commentary/proxy context, not a licensed LME official feed.",
        },
        {
            "source": "third-party-public-price",
            "source_type": "price",
            "commodity": "iron ore",
            "region": "china",
            "title": "Third-party public iron ore and steel mill price context",
            "url": "https://www.investing.com/commodities/iron-ore-62-cfr-futures-historical-data",
            "content": "Third-party public iron ore price context: iron ore price and Mysteel-style market interpretation are commonly linked to Chinese steel mill restocking, blast furnace utilization, port inventories and seaborne iron ore shipments. This is public context/proxy data, not Mysteel licensed data.",
        },
        {
            "source": "third-party-public-price",
            "source_type": "price",
            "commodity": "lithium",
            "region": "china",
            "title": "Third-party public lithium carbonate price context",
            "url": "https://www.investing.com/commodities/lithium-carbonate-99-min-china-futures-historical-data",
            "content": "Third-party public lithium carbonate price context: SHFE lithium carbonate trend is often discussed with battery restocking, cathode demand, Chinese supply discipline and futures market sentiment. This is public third-party context/proxy data, not an official SHFE authorized feed.",
        },
        {
            "source": "third-party-public-policy",
            "source_type": "policy",
            "commodity": "rare earth",
            "region": "china",
            "title": "Third-party public China rare earth traceability and export control context",
            "url": "https://www.mining.com/commodity/rare-earth/",
            "content": "Third-party public China rare earth policy context: rare earth policy discussion focuses on quota discipline, traceability, export controls, smuggling enforcement, environmental supervision and consolidation of supply chains. This public background should be checked against official China Rare Earth Group or ministry notices before formal use.",
        },
        {
            "source": "third-party-public-company",
            "source_type": "news",
            "commodity": "lithium",
            "region": "pilbara",
            "title": "Third-party public Pilbara spodumene shipment and port context",
            "url": "https://www.mining.com/commodity/lithium/",
            "content": "Third-party public Pilbara lithium shipment context: Pilbara spodumene concentrate shipments can be constrained by port scheduling, shipping logistics, offtake timing, concentrate production, weather disruptions and customer demand. This is public market/project context and should be checked against Pilbara Minerals releases for formal use.",
        },
        {
            "source": "third-party-public-company",
            "source_type": "news",
            "commodity": "iron ore",
            "region": "pilbara",
            "title": "Third-party public Pilbara iron ore shipment and port context",
            "url": "https://www.mining-technology.com/marketdata/iron-ore/",
            "content": "Third-party public Pilbara iron ore shipment context: Pilbara iron ore exports are assessed through port throughput, rail availability, weather, maintenance, Chinese steel mill restocking and seaborne demand. This is public third-party context, not an official port or company shipment feed.",
        },
    ]
    docs: list[DocumentRecord] = []
    repeat = max(1, min(10, per_source // max(1, len(specs))))
    for idx in range(repeat):
        for spec in specs:
            published = today - timedelta(days=idx)
            docs.append(
                DocumentRecord(
                    id="",
                    source=spec["source"],
                    source_type=spec["source_type"],
                    title=f"{spec['title']} {published.isoformat()}",
                    url=spec["url"],
                    published_at=published.isoformat(),
                    content=spec["content"],
                    metadata={
                        "source_mode": "third_party_public",
                        "origin_url": spec["url"],
                        "commodity": spec["commodity"],
                        "region": spec["region"],
                        "evidence_kind": "price_row" if spec["source_type"] == "price" else "source_text",
                        "official_market_source": False,
                    },
                )
            )
    return docs


def _collect_china_rare_earth_policy(per_source: int) -> list[DocumentRecord]:
    rows: list[DocumentRecord] = []
    fetch_articles = os.getenv("FETCH_ARTICLE_PAGES", "").lower() in {"1", "true", "yes"}
    for source, list_url in CHINA_RARE_EARTH_LISTS:
        seen_urls: set[str] = set()
        for page_url in _china_rare_earth_pages(list_url, per_source):
            if len(rows) >= per_source:
                break
            try:
                list_html = _fetch(page_url)
                links = _extract_links(list_html, page_url)
                links = [row for row in links if row[1] not in seen_urls]
                if not links and page_url == list_url:
                    rows.append(_source_limited_doc(source, "policy", list_url, "China Rare Earth list page returned no article links.", commodity="rare earth"))
                    continue
                if not links:
                    continue
                for title, url, snippet, pub in links:
                    if len(rows) >= per_source:
                        break
                    seen_urls.add(url)
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
                                "list_url": page_url,
                                "commodity": "rare earth",
                                "region": "china",
                                "evidence_kind": "source_text",
                            },
                        )
                    )
            except Exception as exc:
                if page_url == list_url:
                    rows.append(_source_limited_doc(source, "policy", list_url, f"China Rare Earth list fetch failed: {type(exc).__name__}.", commodity="rare earth"))
                continue
    return rows


def _fetch(url: str, timeout: int = 10, accept: str | None = None) -> str:
    cached = _read_http_cache(url)
    if cached is not None:
        return cached
    stale = _read_http_cache(url, allow_stale=True)
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; mining-interview-mvp/0.3; +https://github.com/dianyxx/01-mining-rag-pipeline)",
        "Accept": accept or "text/html,application/xhtml+xml,application/xml;q=0.9,application/json;q=0.8,*/*;q=0.7",
        "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
        "Cache-Control": "no-cache",
    }
    session = requests.Session()
    session.trust_env = True
    proxies = _configured_proxies()
    last_error: Exception | None = None
    for attempt in range(max(1, FETCH_RETRIES)):
        try:
            _rate_limit()
            response = session.get(url, headers=headers, timeout=(FETCH_CONNECT_TIMEOUT, timeout), verify=False, proxies=proxies or None)
            if response.status_code >= 400:
                text = response.text or response.reason
                raise RuntimeError(f"HTTP {response.status_code}: {text[:120]}")
            if not response.encoding:
                response.encoding = response.apparent_encoding or "utf-8"
            _write_http_cache(url, response.text)
            return response.text
        except Exception as exc:
            last_error = exc
            if attempt < max(1, FETCH_RETRIES) - 1:
                time.sleep(0.8 * (attempt + 1))
    if stale is not None:
        return stale
    raise last_error or RuntimeError("fetch failed")


def _configured_proxies() -> dict[str, str]:
    proxies: dict[str, str] = {}
    http_proxy = os.getenv("SOURCE_HTTP_PROXY") or os.getenv("HTTP_PROXY")
    https_proxy = os.getenv("SOURCE_HTTPS_PROXY") or os.getenv("HTTPS_PROXY")
    if http_proxy:
        proxies["http"] = http_proxy
    if https_proxy:
        proxies["https"] = https_proxy
    return proxies


def _fetch_json(url: str, params: dict | None = None, timeout: int = 10) -> dict:
    full_url = url if not params else f"{url}?{urlencode(params)}"
    return json.loads(_fetch(full_url, timeout=timeout, accept="application/json,text/plain;q=0.9,*/*;q=0.6"))


def _rate_limit() -> None:
    global _LAST_REQUEST_AT
    now = time.monotonic()
    wait = REQUEST_DELAY_SECONDS - (now - _LAST_REQUEST_AT)
    if wait > 0:
        time.sleep(wait)
    _LAST_REQUEST_AT = time.monotonic()


def _read_http_cache(url: str, allow_stale: bool = False) -> str | None:
    path = _http_cache_path(url)
    if not path.exists():
        return None
    if not allow_stale:
        age = time.time() - path.stat().st_mtime
        if age > HTTP_CACHE_TTL_HOURS * 3600:
            return None
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return None


def _write_http_cache(url: str, text: str) -> None:
    try:
        HTTP_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _http_cache_path(url).write_text(text, encoding="utf-8")
    except Exception:
        pass


def _http_cache_path(url: str) -> Path:
    return HTTP_CACHE_DIR / f"{sha256(url.encode('utf-8')).hexdigest()}.txt"


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


def _valid_iso_date(value: str) -> bool:
    try:
        date.fromisoformat(value)
    except Exception:
        return False
    return True


def _valid_decimal(value: str) -> bool:
    try:
        float(value)
    except Exception:
        return False
    return True


def _federal_register_content(item: dict, term: str) -> str:
    pieces = [
        f"Federal Register official document matched policy term: {term}.",
        item.get("title", ""),
        item.get("abstract", ""),
        item.get("excerpts", ""),
        item.get("type", ""),
        item.get("citation", ""),
        item.get("action", ""),
        item.get("dates", ""),
        item.get("publication_date", ""),
        item.get("html_url", ""),
        _agency_names(item),
    ]
    return _clean_text(" ".join(str(piece) for piece in pieces if piece))[:8000]


def _agency_names(item: dict) -> str:
    agencies = item.get("agencies") or []
    names = [agency.get("name", "") for agency in agencies if isinstance(agency, dict)]
    return ", ".join(name for name in names if name)


def _is_mining_policy_relevant(text: str) -> bool:
    lowered = text.lower()
    terms = [
        "mining",
        "mine",
        "mineral",
        "critical minerals",
        "copper",
        "lithium",
        "nickel",
        "cobalt",
        "rare earth",
        "uranium",
        "gold",
        "coal",
        "steel",
        "iron ore",
        "metal",
        "metals",
    ]
    return any(term in lowered for term in terms)


def _china_rare_earth_pages(list_url: str, per_source: int) -> list[str]:
    pages = [list_url]
    max_pages = max(1, min(25, per_source // 8 + 1))
    stem = list_url.rsplit(".", 1)[0]
    suffix = "." + list_url.rsplit(".", 1)[1] if "." in list_url.rsplit("/", 1)[-1] else ""
    for page in range(1, max_pages):
        pages.append(f"{stem}_{page}{suffix}")
    return pages


def _at(values: list, index: int):
    return values[index] if index < len(values) else None


def _fmt_num(value) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.4f}"
    if isinstance(value, int):
        return str(value)
    return str(value)


def _price_change_text(open_, close) -> str:
    if open_ in {None, 0} or close is None:
        return "Intraday change was not available."
    change = (close - open_) / open_ * 100
    direction = "rose" if change > 0 else "fell" if change < 0 else "was flat"
    return f"From open to close, the proxy {direction} by {abs(change):.2f}%."


def _source_limited_doc(source: str, source_type: str, url: str, reason: str, commodity: str | None = None) -> DocumentRecord:
    source_label = {
        "spglobal": "S&P Global Mining RSS",
        "lme": "LME public price page",
        "shfe": "SHFE public price page",
        "mysteel": "Mysteel public price page",
        "china-rare-earth": "China Rare Earth Group public page",
        "disr": "Australia DISR Critical Minerals Strategy",
        "federal-register": "Federal Register official API",
        "yahoo-finance": "Yahoo Finance public price proxy",
        "iea": "IEA Critical Minerals public page",
        "geoscience-australia": "Geoscience Australia public page",
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
        ("lithium", ["lithium", "spodumene", "碳酸锂", "锂", "lce"]),
        ("copper", ["copper", "铜"]),
        ("nickel", ["nickel", "镍"]),
        ("zinc", ["zinc", "锌"]),
        ("iron ore", ["iron ore", "铁矿石"]),
        ("rare earth", ["rare earth", "稀土"]),
        ("cobalt", ["cobalt", "钴"]),
        ("gold", ["gold", "黄金"]),
        ("uranium", ["uranium", "铀"]),
        ("graphite", ["graphite", "石墨"]),
        ("manganese", ["manganese", "锰"]),
        ("aluminum", ["aluminum", "aluminium", "铝"]),
        ("silver", ["silver", "白银"]),
        ("platinum", ["platinum", "铂"]),
        ("palladium", ["palladium", "钯"]),
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
        ("canada", ["canada", "canadian", "加拿大"]),
        ("usa", ["united states", "u.s.", "us ", "美国", "federal register"]),
        ("europe", ["europe", "european union", "eu "]),
    ]
    for value, needles in terms:
        if any(needle in lowered for needle in needles):
            return value
    return ""


def write_collection_snapshot(path: Path, docs: list[DocumentRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([doc.to_dict() for doc in docs], ensure_ascii=False, indent=2), encoding="utf-8")
