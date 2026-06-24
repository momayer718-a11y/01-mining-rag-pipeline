from __future__ import annotations

import re
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class QueryIntent:
    commodity: str | None
    region: str | None
    intent: str
    days: int | None
    coverage_status: str
    missing_dimensions: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


COMMODITY_TERMS = {
    "lithium": ["锂", "lithium", "spodumene", "碳酸锂", "lce"],
    "copper": ["铜", "copper", "cu"],
    "nickel": ["镍", "nickel", "ni"],
    "zinc": ["锌", "zinc", "zn"],
    "iron ore": ["铁矿石", "iron ore", "mysteel"],
    "rare earth": ["稀土", "rare earth"],
    "cobalt": ["钴", "cobalt"],
    "gold": ["金", "gold", "au"],
    "uranium": ["铀", "uranium"],
    "graphite": ["石墨", "graphite"],
    "manganese": ["锰", "manganese"],
}

REGION_TERMS = {
    "australia": ["澳洲", "澳大利亚", "australia", "australian"],
    "pilbara": ["pilbara", "皮尔巴拉"],
    "china": ["中国", "china"],
    "indonesia": ["印尼", "印度尼西亚", "indonesia", "indonesian"],
    "peru": ["秘鲁", "peru"],
    "drc": ["刚果", "drc", "congo"],
    "chile": ["智利", "chile"],
    "canada": ["加拿大", "canada"],
    "usa": ["美国", "usa", "u.s.", "united states"],
}

INTENT_TERMS = {
    "price": ["价格", "价", "price", "trend", "走势", "涨", "跌", "lme", "shfe", "mysteel"],
    "policy": ["政策", "监管", "配额", "审批", "限制", "policy", "quota", "regulation", "permitting", "restriction", "traceability"],
    "news": ["新闻", "事件", "更新", "news", "update"],
    "supply_risk": ["供应", "出货", "风险", "扰动", "维护", "库存", "risk", "supply", "shipment", "maintenance", "inventory", "destocking", "community", "water"],
    "resources": ["资源量", "储量", "resource", "reserve", "indicated", "inferred"],
    "investment": ["投资", "项目", "investment", "project", "economics"],
    "data_gap": ["缺口", "证据不足", "data gap", "coverage"],
}

SUPPORTED_COMMODITIES = {"lithium", "copper", "nickel", "zinc", "iron ore", "rare earth"}
SUPPORTED_REGIONS = {"australia", "pilbara", "china", "indonesia", "chile"}


def parse_intent(text: str, default_days: int | None = None) -> QueryIntent:
    lowered = text.lower()
    commodity = _first_match(lowered, text, COMMODITY_TERMS)
    region = _first_match(lowered, text, REGION_TERMS)
    intent = _detect_intent(lowered, text)
    days = default_days or _extract_days(text)
    missing = []
    if commodity and commodity not in SUPPORTED_COMMODITIES and commodity != "cobalt":
        missing.append(f"{commodity} source not loaded")
    if region and region not in SUPPORTED_REGIONS and region not in {"drc", "peru"}:
        missing.append(f"{region} source not loaded")
    if commodity == "cobalt" or region == "drc":
        missing.append("DRC/cobalt source not loaded")
    if region == "peru":
        missing.append("Peru community/conflict source not loaded")
    coverage = "supported" if not missing else "unsupported"
    return QueryIntent(
        commodity=commodity,
        region=region,
        intent=intent,
        days=days,
        coverage_status=coverage,
        missing_dimensions=sorted(set(missing)),
    )


def terms_for(intent: QueryIntent) -> list[str]:
    terms: list[str] = []
    if intent.commodity:
        terms.extend(COMMODITY_TERMS.get(intent.commodity, [intent.commodity]))
    if intent.region:
        terms.extend(REGION_TERMS.get(intent.region, [intent.region]))
    terms.extend(INTENT_TERMS.get(intent.intent, []))
    return terms


def preferred_source_types(intent: QueryIntent) -> list[str]:
    if intent.intent == "price":
        return ["price", "policy", "news"]
    if intent.intent == "policy":
        return ["policy", "news", "price"]
    if intent.intent == "resources":
        return ["news", "policy", "price"]
    if intent.intent in {"supply_risk", "investment", "news"}:
        return ["news", "policy", "price"]
    return ["news", "policy", "price"]


def _first_match(lowered: str, original: str, mapping: dict[str, list[str]]) -> str | None:
    for value, terms in mapping.items():
        if any(term in lowered or term in original for term in terms):
            return value
    return None


def _detect_intent(lowered: str, original: str) -> str:
    for intent, terms in INTENT_TERMS.items():
        if any(term in lowered or term in original for term in terms):
            return intent
    return "news"


def _extract_days(text: str) -> int | None:
    match = re.search(r"近\s*(\d+)\s*天", text)
    if match:
        return int(match.group(1))
    lowered = text.lower()
    if "today" in lowered or "今日" in text:
        return 1
    if "最近" in text or "recent" in lowered:
        return 30
    return None
