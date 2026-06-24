from __future__ import annotations

from datetime import date, timedelta
from hashlib import sha256

from pipeline.data_models import DocumentRecord


NEWS_TOPICS = [
    ("Pilbara lithium mine expands spodumene concentrate output", "Pilbara Minerals reported higher lithium concentrate shipments and tighter port scheduling."),
    ("Copper supply risk rises after maintenance at Chile concentrator", "Analysts expect LME copper inventories to remain sensitive to smelter treatment charges."),
    ("Nickel producers cut guidance amid weak battery demand", "Indonesian supply growth and stainless demand are pressuring nickel prices."),
    ("Iron ore shipments recover as Chinese mills restock", "Seaborne iron ore demand improved after mills rebuilt inventories."),
]

POLICY_TOPICS = [
    ("Australia critical minerals strategy updates lithium export funding", "Australia expanded export finance and processing grants for lithium and rare earth supply chains."),
    ("DISR consultation highlights offtake and permitting risk", "The policy note flags approvals, water use, Indigenous engagement and downstream refining capacity."),
    ("China rare earth group emphasizes quota discipline", "Rare earth supply policy is focusing on traceability, environmental checks and consolidation."),
    ("Critical minerals partnerships target battery supply resilience", "New agreements support nickel, copper, lithium and rare earth project development."),
    ("Indonesia nickel export restrictions and refining quota policy", "Indonesia nickel policy keeps export restrictions, refining quota approvals and smelter margins in focus for laterite supply chains."),
]

PRICE_TOPICS = [
    ("LME copper price trend", "LME copper moved higher as inventories declined and policy support improved demand expectations."),
    ("LME zinc price trend", "LME zinc traded sideways with mine supply disruptions offset by soft construction demand."),
    ("LME nickel price trend", "LME nickel weakened due to Indonesian supply growth and battery sector destocking."),
    ("SHFE lithium carbonate price trend", "SHFE lithium carbonate stabilized after battery restocking and cautious export policy signals."),
    ("Mysteel iron ore price trend", "Mysteel iron ore assessments firmed as blast furnace utilization improved."),
]


def _stable_id(source: str, url: str, content: str) -> str:
    return sha256(f"{source}|{url}|{content}".encode("utf-8")).hexdigest()[:24]


def generate_fixture_documents(per_source: int = 200, today: date | None = None) -> list[DocumentRecord]:
    today = today or date.today()
    rows: list[DocumentRecord] = []
    groups = [
        ("news", "mining.com", NEWS_TOPICS),
        ("policy", "disr-critical-minerals", POLICY_TOPICS),
        ("price", "market-prices", PRICE_TOPICS),
    ]
    for source_type, source, topics in groups:
        for idx in range(per_source):
            title, base = topics[idx % len(topics)]
            published = today - timedelta(days=idx % 30)
            commodity = _commodity_for(title)
            content = (
                f"{base} This fixture record covers {commodity}, mining assets, policy risk, "
                f"export flows, price direction and investment implications for the last 30 days. "
                f"Record number {idx + 1} is intentionally varied for retrieval evaluation. "
                "Downstream refining capacity, regulatory supervision, environmental checks, "
                "supply chain resilience and mining investment risk are tracked as recurring themes."
            )
            if "Australia" in title or "DISR" in title:
                content += " In the last 7 days, Australia lithium export policy emphasized financing, permitting and downstream refining rather than a direct export ban."
            if "Pilbara" in title:
                content += " Pilbara lithium operations remain exposed to spodumene pricing, port availability and regulatory approvals."
            if "rare earth" in title.lower():
                content += " Rare earth policy emphasizes quota discipline, traceability, regulatory supervision, environmental checks and consolidation."
            if "Indonesia nickel" in title:
                content += " Indonesia nickel export policy remains tied to quota approvals, domestic refining capacity, smelter margins and battery demand."
            if "SHFE" in title:
                content += " SHFE lithium carbonate stabilized after restocking, with price trend signals watched by battery producers."
            url = f"https://fixture.local/{source_type}/{idx + 1}"
            rows.append(
                DocumentRecord(
                    id=_stable_id(source, url, content),
                    source=source,
                    source_type=source_type,
                    title=f"{title} #{idx + 1}",
                    url=url,
                    published_at=published.isoformat(),
                    content=content,
                    metadata={"source_mode": "fixture", "commodity": commodity},
                )
            )
    return rows


def _commodity_for(title: str) -> str:
    lowered = title.lower()
    for name in ["lithium", "copper", "zinc", "nickel", "iron ore", "rare earth"]:
        if name in lowered:
            return name
    if "锂" in title:
        return "lithium"
    return "critical minerals"
