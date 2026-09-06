"""Harvest ordinary BI questions from public datasets for CCA false-engage stress test."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_COUNTRY_TERMS = (
    "usa", "u.s.a.", "united states", "uk", "united kingdom", "germany", "france",
    "china", "japan", "india", "malaysia", "singapore", "australia", "canada",
    "mexico", "brazil", "spain", "italy", "russia", "korea", "taiwan", "thailand",
    "indonesia", "philippines", "vietnam", "netherlands", "belgium", "sweden",
    "norway", "denmark", "finland", "poland", "austria", "switzerland", "ireland",
    "portugal", "greece", "turkey", "egypt", "saudi", "uae", "dubai", "hong kong",
    "new zealand", "south africa", "argentina", "chile", "colombia", "peru",
    "venezuela", "pakistan", "bangladesh", "nepal", "sri lanka", "europe", "asia",
    "africa", "america", "apac", "emea", "latam", "middle east", "eastern europe",
    "western europe", "north america", "south america", "southeast asia", "east asia",
    "scandinavia", "balkans", "iberian", "nordic", "oceania", "caribbean",
    "alameda county", "los angeles county", "san francisco county",
)
_COUNTRY_FILTER_PATTERNS = [
    re.compile(rf"\bin\s+(the\s+)?{re.escape(term)}\b", re.I) for term in _COUNTRY_TERMS
] + [
    re.compile(r"\b(in a given nation|given nation|that nation|for that nation)\b", re.I),
    re.compile(r"\bsupplier in that nation\b", re.I),
    re.compile(r"\bwithin a specific range of country codes\b", re.I),
    re.compile(r"\bcountry code is\b", re.I),
    re.compile(r"\bcustomers in germany\b", re.I),
    re.compile(r"\bsales in the usa\b", re.I),
    re.compile(r"\bshow me sales in the usa\b", re.I),
    re.compile(r"\bonly\s+apac\b", re.I),
    re.compile(r"\bapac only\b", re.I),
]
COUNTRY_SUBJECT = re.compile(
    r"\b(gdp|population|life\s+expectancy|capital|surface\s+area|independence)\s+of\s+"
    r"(malaysia|china|india|germany|france|japan|usa|uk|australia|canada|brazil|"
    r"mexico|spain|italy|russia|indonesia|thailand|singapore|chile|peru|egypt|"
    r"argentina|colombia|venezuela|pakistan|bangladesh|philippines|vietnam|"
    r"netherlands|belgium|sweden|norway|denmark|finland|poland|austria|switzerland|"
    r"ireland|portugal|greece|turkey|saudi|uae|new\s+zealand|south\s+africa)\b",
    re.I,
)
TENURE_PROPERTY = re.compile(
    r"\b(lease|leased|leasing|rent|rented|rental|buy\s+vs|purchase\s+vs|"
    r"tenure|freehold|leasehold|mortgage|commercial\s+property|residential\s+property|"
    r"commercial\s+vs\s+residential|residential\s+vs\s+commercial|"
    r"office\s+space\s+lease|property\s+class)\b",
    re.I,
)
SECTOR_FILTER = re.compile(
    r"\b(agriculture|agricultural|farm(ing)?|plantation|livestock|poultry|"
    r"dairy\s+farm|crop\s+sector|farming\s+sector|"
    r"in\s+the\s+agriculture|in\s+agriculture|agriculture\s+industry|"
    r"livestock\s+industry|plantation\s+sector)\b",
    re.I,
)
NATION_FILTER_TPC = re.compile(
    r"\b(in\s+a\s+given\s+nation|given\s+nation|that\s+nation|for\s+that\s+nation|"
    r"supplier\s+in\s+that\s+nation|within\s+a\s+specific\s+range\s+of\s+country\s+codes|"
    r"country\s+code\s+is)\b",
    re.I,
)
ORDINARY_OK_GROUP = re.compile(r"\b(group\s+by|rank\s+by|breakdown\s+by|split\s+by)\s+country\b", re.I)

SPIDER_URL = "https://huggingface.co/datasets/xlangai/spider"
BIRD_URL = "https://huggingface.co/datasets/birdsql/bird_sql_dev_20251106"
WIKISQL_URL = "https://github.com/salesforce/WikiSQL"


def normalize_key(q: str) -> str:
    return re.sub(r"\s+", " ", q.lower().strip())


def is_excluded(q: str) -> bool:
    if COUNTRY_SUBJECT.search(q):
        return True
    if TENURE_PROPERTY.search(q):
        return True
    if SECTOR_FILTER.search(q):
        return True
    if NATION_FILTER_TPC.search(q):
        return True
    if any(p.search(q) for p in _COUNTRY_FILTER_PATTERNS):
        if ORDINARY_OK_GROUP.search(q):
            return False
        if re.search(r"\b(by|per)\s+country\b", q, re.I) and not re.search(
            r"\bin\s+\w+\b.*\bcountry\b|\bcountry\s*=\s*['\"]", q, re.I
        ):
            return False
        return True
    return False


def looks_like_question(q: str) -> bool:
    q = q.strip()
    if len(q) < 12 or len(q) > 300:
        return False
    if q.count("?") > 2:
        return False
    if re.match(r"^(SELECT|INSERT|UPDATE|DELETE|WITH)\b", q, re.I):
        return False
    return True


def infer_domain(q: str) -> str:
    ql = q.lower()
    if any(w in ql for w in ("warehouse", "shipment", "shipping", "inventory", "stock", "supplier", "purchase order", "fulfillment", "dock", "pick", "pack", "wms", "logistics", "freight", "delivery")):
        return "logistics"
    if any(w in ql for w in ("employee", "salary", "hire", "department", "payroll", "headcount", "vacation", "leave", "benefit", " hr")):
        return "hr"
    if any(w in ql for w in ("revenue", "profit", "margin", "cost", "expense", "budget", "invoice", "payment", "account", "financial", "discount")):
        return "finance"
    if any(w in ql for w in ("customer", "order", "product", "sales", "retail", "store", "sku", "category", "basket", "checkout")):
        return "retail"
    if any(w in ql for w in ("production", "assembly", "factory", "bom", "work order", "machine", "defect", "yield")):
        return "manufacturing"
    return "other"


def add_question(
    seen: set[str],
    out: list[dict[str, Any]],
    question: str,
    source_url: str,
    domain: str = "other",
) -> bool:
    q = question.strip()
    if not looks_like_question(q) or is_excluded(q):
        return False
    key = normalize_key(q)
    if key in seen:
        return False
    seen.add(key)
    dom = domain if domain != "other" else infer_domain(q)
    out.append({"id": "", "question": q, "source_url": source_url, "domain": dom})
    return True


def curated_questions() -> list[tuple[str, str, str]]:
    return [
        ("What is the total shipped quantity and average extended price by return flag and line status for orders shipped on or before a cutoff date?", "https://www.tpc.org/tpc_documents_current_versions/pdf/tpc-h_v2.17.1.pdf", "logistics"),
        ("Which parts have the lowest supply cost for a given part type and size, and who are the suppliers?", "https://www.tpc.org/tpc_documents_current_versions/pdf/tpc-h_v2.17.1.pdf", "logistics"),
        ("What is the total revenue from unshipped orders with the highest potential value as of a given date?", "https://www.tpc.org/tpc_documents_current_versions/pdf/tpc-h_v2.17.1.pdf", "finance"),
        ("How many orders were shipped using each mode, and how many arrived late versus on time in a given year?", "https://www.tpc.org/tpc_documents_current_versions/pdf/tpc-h_v2.17.1.pdf", "logistics"),
        ("What share of revenue in a given month came from promotional parts?", "https://www.tpc.org/tpc_documents_current_versions/pdf/tpc-h_v2.17.1.pdf", "retail"),
        ("Who was the top supplier by revenue in a given quarter?", "https://www.tpc.org/tpc_documents_current_versions/pdf/tpc-h_v2.17.1.pdf", "logistics"),
        ("How many suppliers can supply parts matching a customer's size and type requirements?", "https://www.tpc.org/tpc_documents_current_versions/pdf/tpc-h_v2.17.1.pdf", "logistics"),
        ("Who are the top 100 customers who placed the largest quantity orders?", "https://www.tpc.org/tpc_documents_current_versions/pdf/tpc-h_v2.17.1.pdf", "retail"),
        ("What is gross discounted revenue for orders shipped by air and delivered in person for selected part brands?", "https://www.tpc.org/tpc_documents_current_versions/pdf/tpc-h_v2.17.1.pdf", "finance"),
        ("Which suppliers failed to meet committed delivery dates on multi-supplier orders?", "https://www.tpc.org/tpc_documents_current_versions/pdf/tpc-h_v2.17.1.pdf", "logistics"),
        ("How is customer order count distributed, including customers with no orders?", "https://www.tpc.org/tpc_documents_current_versions/pdf/tpc-h_v2.17.1.pdf", "retail"),
        ("What is the profit by product type for parts whose names contain a given substring?", "https://www.tpc.org/tpc_documents_current_versions/pdf/tpc-h_v2.17.1.pdf", "finance"),
        ("Which sales has the highest revenue?", "https://github.com/MicrosoftDocs/powerbi-docs/blob/main/powerbi-docs/natural-language/q-and-a-intro.md", "retail"),
        ("Show me sales in the last year", "https://github.com/MicrosoftDocs/powerbi-docs/blob/main/powerbi-docs/natural-language/q-and-a-intro.md", "retail"),
        ("Top 10 products by sales", "https://github.com/MicrosoftDocs/powerbi-docs/blob/main/powerbi-docs/natural-language/q-and-a-intro.md", "retail"),
        ("Which customers bought cheese and wine?", "https://github.com/MicrosoftDocs/powerbi-docs/blob/main/powerbi-docs/natural-language/q-and-a-intro.md", "retail"),
        ("Show me median sales by product", "https://github.com/MicrosoftDocs/powerbi-docs/blob/main/powerbi-docs/natural-language/q-and-a-intro.md", "retail"),
        ("Show me top 10 countries by sales ordered by country code", "https://github.com/MicrosoftDocs/powerbi-docs/blob/main/powerbi-docs/natural-language/q-and-a-intro.md", "retail"),
        ("Show me date by total sales vs total cost", "https://github.com/MicrosoftDocs/powerbi-docs/blob/main/powerbi-docs/natural-language/q-and-a-intro.md", "finance"),
        ("Show me sales over time", "https://github.com/MicrosoftDocs/powerbi-docs/blob/main/powerbi-docs/natural-language/q-and-a-intro.md", "retail"),
        ("What were last month's sales?", "https://powerbiconsulting.com/blog/power-bi-copilot-semantic-model-optimization-2025", "retail"),
        ("Show me year-over-year growth by region", "https://powerbiconsulting.com/blog/power-bi-copilot-semantic-model-optimization-2025", "finance"),
        ("Which products have declining sales?", "https://powerbiconsulting.com/blog/power-bi-copilot-semantic-model-optimization-2025", "retail"),
        ("How many new customers did we acquire last quarter?", "https://powerbiconsulting.com/blog/power-bi-copilot-semantic-model-optimization-2025", "retail"),
        ("What is our customer retention rate?", "https://powerbiconsulting.com/blog/power-bi-copilot-semantic-model-optimization-2025", "retail"),
        ("Who are our top 10 customers by revenue?", "https://powerbiconsulting.com/blog/power-bi-copilot-semantic-model-optimization-2025", "retail"),
        ("Are we meeting our sales targets?", "https://powerbiconsulting.com/blog/power-bi-copilot-semantic-model-optimization-2025", "finance"),
        ("What is our profit margin trend?", "https://powerbiconsulting.com/blog/power-bi-copilot-semantic-model-optimization-2025", "finance"),
        ("Which regions are underperforming?", "https://powerbiconsulting.com/blog/power-bi-copilot-semantic-model-optimization-2025", "retail"),
        ("List all products in the catalog", "https://learnsql.com/blog/sql-exercises-northwind/", "retail"),
        ("Which products cost more than 3.50?", "https://learnsql.com/blog/sql-exercises-northwind/", "retail"),
        ("Show customer names and email addresses", "https://learnsql.com/blog/sql-exercises-northwind/", "retail"),
        ("List each product with its category name", "https://learnsql.com/blog/sql-exercises-northwind/", "retail"),
        ("Which employees handled which customer orders?", "https://learnsql.com/blog/sql-exercises-northwind/", "hr"),
        ("Sort employees by birth date", "https://learnsql.com/blog/sql-exercises-northwind/", "hr"),
        ("List product names and prices sorted by price", "https://learnsql.com/blog/sql-exercises-northwind/", "retail"),
        ("How many product categories do we have?", "https://learnsql.com/blog/sql-exercises-northwind/", "retail"),
        ("How many orders has each customer placed?", "https://learnsql.com/blog/sql-exercises-northwind/", "retail"),
        ("What is the revenue for each customer and employee pair?", "https://learnsql.com/blog/sql-exercises-northwind/", "finance"),
        ("What is the average price per category?", "https://learnsql.com/blog/sql-exercises-northwind/", "retail"),
        ("Which categories have active products?", "https://learnsql.com/blog/sql-exercises-northwind/", "retail"),
        ("What is the cost of Northwoods Cranberry Sauce?", "https://sagenlp.com/nlpDb.htm", "retail"),
        ("Do you have Chef Anton's Cajun Seasoning in stock?", "https://sagenlp.com/nlpDb.htm", "retail"),
        ("How many units of Aniseed Syrup are in stock?", "https://sagenlp.com/nlpDb.htm", "retail"),
        ("List all products that we sell", "https://sagenlp.com/nlpDb.htm", "retail"),
        ("What is our current order accuracy rate?", "https://www.metaoption.com/blog/faq/warehouse-management-system-wms-faqs/", "logistics"),
        ("What is our dock-to-stock cycle time this week?", "https://www.metaoption.com/blog/faq/warehouse-management-system-wms-faqs/", "logistics"),
        ("What is inventory accuracy by warehouse?", "https://www.metaoption.com/blog/faq/warehouse-management-system-wms-faqs/", "logistics"),
        ("What is labor utilization in the warehouse today?", "https://www.metaoption.com/blog/faq/warehouse-management-system-wms-faqs/", "logistics"),
        ("What is our cost per order fulfilled?", "https://www.metaoption.com/blog/faq/warehouse-management-system-wms-faqs/", "logistics"),
        ("Where is every item in the warehouse right now?", "https://www.modernmaterialshandling.com/warehouse-mgmt/warehouse-management-system/", "logistics"),
        ("What pick tasks are pending in warehouse A?", "https://www.modernmaterialshandling.com/warehouse-mgmt/warehouse-management-system/", "logistics"),
        ("How many open purchase orders are awaiting receipt?", "https://www.modernmaterialshandling.com/warehouse-mgmt/warehouse-management-system/", "logistics"),
        ("What is the average pick path length by shift?", "https://www.modernmaterialshandling.com/warehouse-mgmt/warehouse-management-system/", "logistics"),
        ("Which SKUs are below reorder point?", "https://grexpro.com/blog/complete-guide-to-warehouse-management-system-wms/", "logistics"),
        ("How long does putaway take on average?", "https://grexpro.com/blog/complete-guide-to-warehouse-management-system-wms/", "logistics"),
        ("What is the fulfillment rate for same-day orders?", "https://grexpro.com/blog/complete-guide-to-warehouse-management-system-wms/", "logistics"),
        ("How many backorders do we have by SKU?", "https://grexpro.com/blog/complete-guide-to-warehouse-management-system-wms/", "logistics"),
        ("What is on-time shipment percentage this month?", "https://traceconsultants.com.au/thinking/the-2026-australian-wms-buyers-guide", "logistics"),
        ("How many lines were picked per hour yesterday?", "https://traceconsultants.com.au/thinking/the-2026-australian-wms-buyers-guide", "logistics"),
        ("Show top 5 total sales by country", "https://github.com/cesar39299/Northwind_AI_Web_Assistant", "retail"),
        ("What are total sales by product category this year?", "https://github.com/cesar39299/Northwind_AI_Web_Assistant", "retail"),
        ("List employees hired in the last 12 months", "https://github.com/cesar39299/Northwind_AI_Web_Assistant", "hr"),
    ]


def score_question(q: str, source_url: str) -> float:
    """Higher = better ordinary BI ask for false-engage test."""
    s = 0.0
    ql = q.lower()
    if "?" in q or ql.startswith(("show", "list", "what", "how", "which", "who", "when", "top", "give")):
        s += 2
    if any(w in ql for w in ("sales", "revenue", "order", "customer", "product", "inventory", "employee", "supplier", "warehouse", "profit", "cost")):
        s += 2
    if any(w in ql for w in ("last year", "this month", "quarter", "by category", "by product", "top ", "average", "total")):
        s += 1
    if SPIDER_URL in source_url or BIRD_URL in source_url:
        s += 0.5
    if "tpc.org" in source_url or "powerbi" in source_url or "learnsql" in source_url:
        s += 1
    if len(q) > 180:
        s -= 1
    return s


def main() -> None:
    from datasets import load_dataset

    root = Path(__file__).resolve().parents[1]
    out_path = root / ".tmp" / "cca_ordinary_harvest.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    stats: dict[str, int] = {}

    for q, url, dom in curated_questions():
        if add_question(seen, out, q, url, dom):
            stats["curated"] = stats.get("curated", 0) + 1

    for split in ("train", "validation"):
        ds = load_dataset("xlangai/spider", split=split)
        n = 0
        for row in ds:
            if add_question(seen, out, row["question"], SPIDER_URL):
                n += 1
        stats[f"spider_{split}"] = n

    ds = load_dataset("birdsql/bird_sql_dev_20251106", split="dev_20251106")
    n = 0
    for row in ds:
        if add_question(seen, out, row["question"], BIRD_URL):
            n += 1
    stats["bird"] = n

    # WikiSQL: direct jsonl if available (HF dataset script deprecated)
    try:
        import urllib.request

        wikisql_dev = "https://cdn.jsdelivr.net/gh/salesforce/WikiSQL@master/data/dev.jsonl"
        req = urllib.request.Request(wikisql_dev, headers={"User-Agent": "Mozilla/5.0"})
        raw = urllib.request.urlopen(req, timeout=120).read().decode("utf-8")
        n = 0
        for line in raw.splitlines():
            if len(out) >= 500:
                break
            row = json.loads(line)
            if add_question(seen, out, row["question"], WIKISQL_URL):
                n += 1
        stats["wikisql_dev"] = n
    except Exception as exc:
        stats["wikisql_dev"] = f"skipped: {exc}"

    # Rank and take best 500
    ranked = sorted(out, key=lambda r: score_question(r["question"], r["source_url"]), reverse=True)
    final = ranked[:500]
    for i, row in enumerate(final, 1):
        row["id"] = f"ord-{i:03d}"

    out_path.write_text(json.dumps(final, indent=2, ensure_ascii=False), encoding="utf-8")
    meta = {"total_harvested": len(out), "exported": len(final), "stats": stats, "path": str(out_path)}
    (root / ".tmp" / "cca_ordinary_harvest_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(json.dumps(meta))


if __name__ == "__main__":
    main()
