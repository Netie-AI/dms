"""FDE-PIPELINE-01: a seeded factory of synthetic tenants for scale tests.

``tests/fde_scenarios`` holds five hand-written companies with hand-checked gold.
This module makes as many more as a run asks for: each tenant is a domain (clinic,
logistics, school, ...), a random subset of that domain's tables, a row scale, and a
physical naming style (``snake``, ``camel``, ``legacy`` 8.3-style abbreviations,
``prefixed``). The business label of every table and column is kept beside the
physical name, the way an FDE collects a glossary at a new customer.

Every value comes from ``random.Random(seed)``: the same ``(seed, index)`` gives
byte-identical tenants on every machine, so a scale run is reproducible without
shipping data. The returned :class:`scenarios.Dataset` loads through the same
engine loaders as the hand-written packs.
"""

from __future__ import annotations

import random
import re
import sys
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests" / "fde_scenarios"))
from scenarios import Dataset, Table  # noqa: E402

NAMING_STYLES = ("snake", "camel", "legacy", "prefixed")
START = date(2025, 1, 1)
DAYS = 547  # through 2026-06-30, the same window as the hand-written packs


@dataclass(frozen=True)
class DimSpec:
    name: str
    label: str
    categories: tuple[str, str, tuple[str, ...]]  # (column, label, values)
    names: tuple[str, ...]  # stems for the display-name column


@dataclass(frozen=True)
class FactSpec:
    name: str
    label: str
    dims: tuple[str, ...]
    measures: tuple[tuple[str, str, float, float], ...]  # (column, label, lo, hi)
    date_col: tuple[str, str]
    status: tuple[str, str, tuple[str, ...]] | None = None
    lines: tuple[str, str, tuple[tuple[str, str, float, float], ...]] | None = None


@dataclass(frozen=True)
class Domain:
    key: str
    title: str
    dims: tuple[DimSpec, ...]
    facts: tuple[FactSpec, ...]


def _dim(name: str, label: str, cat: str, cat_label: str, cats: str, names: str) -> DimSpec:
    return DimSpec(name, label, (cat, cat_label, tuple(cats.split("|"))), tuple(names.split("|")))


DOMAINS: tuple[Domain, ...] = (
    Domain(
        "clinic",
        "Klinik Sejahtera",
        (
            _dim(
                "patient",
                "patient",
                "insurance_tier",
                "insurance tier",
                "basic|silver|gold|none",
                "Aminah|Bala|Chong|Devi|Farid|Grace|Hafiz|Indra",
            ),
            _dim(
                "doctor",
                "doctor",
                "specialty",
                "specialty",
                "general|paediatrics|cardiology|dermatology",
                "Dr Lim|Dr Rao|Dr Tan|Dr Wong",
            ),
            _dim(
                "branch",
                "clinic branch",
                "state",
                "state",
                "Selangor|Johor|Penang|Sabah",
                "Klang|Kulai|Bayan|Likas|Subang",
            ),
        ),
        (
            FactSpec(
                "visit",
                "visits",
                ("patient", "doctor", "branch"),
                (("fee", "consultation fee", 30, 400),),
                ("visit_date", "visit date"),
                ("visit_type", "visit type", ("walk-in", "booked", "follow-up")),
            ),
            FactSpec(
                "prescription",
                "prescriptions",
                ("patient", "doctor"),
                (("amount", "prescription amount", 5, 250),),
                ("issued_on", "issue date"),
                lines=(
                    "prescription_item",
                    "prescription items",
                    (("qty", "quantity", 1, 30), ("line_amount", "item amount", 2, 90)),
                ),
            ),
        ),
    ),
    Domain(
        "logistics",
        "Laju Freight",
        (
            _dim(
                "shipper",
                "shipper",
                "segment",
                "customer segment",
                "sme|enterprise|government",
                "Alpha|Bina|Cahaya|Delta|Era|Fokus|Gemilang",
            ),
            _dim(
                "depot",
                "depot",
                "region",
                "region",
                "north|central|south|east",
                "Prai|Shah Alam|Senai|Kuantan|Ipoh",
            ),
            _dim(
                "vehicle",
                "vehicle",
                "vehicle_class",
                "vehicle class",
                "van|3-tonne|10-tonne|prime mover",
                "WXA|JTB|PKC|BMD",
            ),
        ),
        (
            FactSpec(
                "shipment",
                "shipments",
                ("shipper", "depot"),
                (("weight_kg", "weight in kg", 1, 900), ("charge", "freight charge", 15, 3000)),
                ("ship_date", "ship date"),
                ("status", "shipment status", ("delivered", "in transit", "returned")),
            ),
            FactSpec(
                "trip",
                "trips",
                ("vehicle", "depot"),
                (("distance_km", "distance in km", 5, 700), ("fuel_cost", "fuel cost", 10, 900)),
                ("trip_date", "trip date"),
            ),
        ),
    ),
    Domain(
        "school",
        "Ilmu Learning Centres",
        (
            _dim(
                "student",
                "student",
                "level",
                "level",
                "primary|lower secondary|upper secondary",
                "Ali|Mei|Kumar|Siti|Jason|Nurul",
            ),
            _dim(
                "course",
                "course",
                "subject",
                "subject",
                "maths|science|english|coding",
                "Algebra|Physics|Essay|Python|Geometry",
            ),
            _dim(
                "centre",
                "centre",
                "city",
                "city",
                "Kuala Lumpur|Penang|Johor Bahru|Kota Kinabalu",
                "Cheras|Gurney|Tebrau|Lintas",
            ),
        ),
        (
            FactSpec(
                "enrolment",
                "enrolments",
                ("student", "course", "centre"),
                (("fee_paid", "fee paid", 80, 1500),),
                ("enrolled_on", "enrolment date"),
                ("status", "enrolment status", ("active", "completed", "dropped")),
            ),
            FactSpec(
                "assessment",
                "assessments",
                ("student", "course"),
                (("score", "score", 20, 100),),
                ("assessed_on", "assessment date"),
            ),
        ),
    ),
    Domain(
        "hospitality",
        "Pantai Hotels",
        (
            _dim(
                "guest",
                "guest",
                "loyalty_tier",
                "loyalty tier",
                "none|silver|gold|platinum",
                "Ahmad|Bella|Chen|Dina|Eric|Fatimah",
            ),
            _dim(
                "property",
                "hotel",
                "property_type",
                "property type",
                "city|resort|airport",
                "Langkawi|Melaka|Sepang|Cherating|KLCC",
            ),
            _dim(
                "channel",
                "booking channel",
                "channel_group",
                "channel group",
                "direct|ota|corporate",
                "Web|App|Agoda|Booking|Travel Desk",
            ),
        ),
        (
            FactSpec(
                "reservation",
                "reservations",
                ("guest", "property", "channel"),
                (("room_revenue", "room revenue", 120, 4000), ("nights", "nights", 1, 9)),
                ("check_in", "check-in date"),
                ("status", "reservation status", ("stayed", "cancelled", "no-show")),
                lines=(
                    "folio_charge",
                    "folio charges",
                    (("charge_amount", "charge amount", 5, 600),),
                ),
            ),
        ),
    ),
    Domain(
        "agri",
        "Ladang Hijau Estates",
        (
            _dim(
                "estate",
                "estate",
                "crop",
                "crop",
                "oil palm|rubber|durian|cocoa",
                "Bukit|Sungai|Tanjung|Kampung|Ulu",
            ),
            _dim(
                "mill",
                "mill",
                "mill_state",
                "mill state",
                "Pahang|Johor|Perak|Sabah",
                "Bera|Kluang|Kinta|Sandakan",
            ),
            _dim(
                "grader",
                "grader",
                "shift",
                "shift",
                "morning|evening|night",
                "Azman|Bakri|Chandran|Daud|Effendy",
            ),
        ),
        (
            FactSpec(
                "harvest",
                "harvests",
                ("estate", "grader"),
                (("tonnes", "tonnes harvested", 1, 80),),
                ("harvest_date", "harvest date"),
            ),
            FactSpec(
                "delivery",
                "mill deliveries",
                ("estate", "mill"),
                (("net_weight", "net weight", 2, 60), ("payout", "payout", 500, 40000)),
                ("delivered_on", "delivery date"),
                ("grade", "fruit grade", ("A", "B", "reject")),
            ),
        ),
    ),
    Domain(
        "telco",
        "Sambung Mobile",
        (
            _dim(
                "subscriber",
                "subscriber",
                "plan",
                "plan",
                "prepaid|postpaid basic|postpaid max|family",
                "S-A|S-B|S-C|S-D|S-E|S-F",
            ),
            _dim(
                "cell_site",
                "cell site",
                "site_region",
                "site region",
                "klang valley|northern|southern|borneo",
                "KV|NR|SR|BN",
            ),
            _dim(
                "dealer",
                "dealer",
                "dealer_type",
                "dealer type",
                "kiosk|store|online",
                "Mega|Kedai|Pusat|Laman",
            ),
        ),
        (
            FactSpec(
                "usage_record",
                "usage records",
                ("subscriber", "cell_site"),
                (("data_mb", "data used in MB", 1, 5000), ("voice_min", "voice minutes", 0, 300)),
                ("usage_date", "usage date"),
            ),
            FactSpec(
                "topup",
                "top-ups",
                ("subscriber", "dealer"),
                (("topup_amount", "top-up amount", 5, 100),),
                ("topup_date", "top-up date"),
                ("method", "payment method", ("card", "ewallet", "cash")),
            ),
        ),
    ),
    Domain(
        "property",
        "Rumah Prima Management",
        (
            _dim(
                "tenant",
                "tenant",
                "tenant_type",
                "tenant type",
                "residential|retail|office",
                "Unit Holder A|Kedai B|Firma C|Keluarga D",
            ),
            _dim(
                "building",
                "building",
                "district",
                "district",
                "Petaling|Gombak|Hulu Langat|Klang",
                "Menara|Residensi|Plaza|Vista",
            ),
            _dim(
                "contractor",
                "contractor",
                "trade",
                "trade",
                "plumbing|electrical|lift|cleaning",
                "Jaya|Bersih|Kuasa|Paip",
            ),
        ),
        (
            FactSpec(
                "rent_invoice",
                "rent invoices",
                ("tenant", "building"),
                (("rent_amount", "rent amount", 800, 25000),),
                ("invoice_date", "invoice date"),
                ("status", "invoice status", ("paid", "overdue", "partial")),
            ),
            FactSpec(
                "work_order",
                "maintenance work orders",
                ("building", "contractor"),
                (("cost", "repair cost", 50, 12000),),
                ("raised_on", "date raised"),
            ),
        ),
    ),
    Domain(
        "fnb",
        "Selera Kitchens",
        (
            _dim(
                "outlet",
                "outlet",
                "outlet_format",
                "outlet format",
                "kiosk|cafe|restaurant",
                "Bangsar|Puchong|Seremban|Ipoh|Kuching",
            ),
            _dim(
                "menu_item",
                "menu item",
                "menu_category",
                "menu category",
                "rice|noodles|drinks|dessert",
                "Nasi|Mee|Teh|Kuih|Roti|Laksa",
            ),
            _dim(
                "supplier",
                "supplier",
                "supply_type",
                "supply type",
                "produce|meat|dry goods|packaging",
                "Segar|Halal|Pasar|Kotak",
            ),
        ),
        (
            FactSpec(
                "sale",
                "sales",
                ("outlet", "menu_item"),
                (("qty", "quantity sold", 1, 12), ("net_sales", "net sales", 3, 180)),
                ("sale_date", "sale date"),
                ("order_type", "order type", ("dine-in", "takeaway", "delivery")),
            ),
            FactSpec(
                "purchase",
                "supplier purchases",
                ("outlet", "supplier"),
                (("purchase_amount", "purchase amount", 40, 6000),),
                ("purchased_on", "purchase date"),
            ),
        ),
    ),
)


@dataclass
class Tenant:
    """One generated company: the dataset plus the glossary an FDE would collect."""

    key: str
    domain: str
    title: str
    naming: str
    seed: int
    dataset: Dataset
    labels: dict[str, str] = field(default_factory=dict)  # physical name -> business label
    roles: dict[str, Any] = field(default_factory=dict)  # table -> structural role


_VOWELS = re.compile(r"(?<!^)[aeiou]")


def _legacy(word: str) -> str:
    parts = word.split("_")
    return "_".join(_VOWELS.sub("", p)[:5] or p[:3] for p in parts).upper()


def physical(style: str, logical: str, *, kind: str) -> str:
    """Map a logical snake_case name to a physical name in ``style``."""
    if style == "snake":
        return logical
    if style == "camel":
        out = "".join(p.capitalize() for p in logical.split("_"))
        return out if kind == "table" else out[0].lower() + out[1:]
    if style == "legacy":
        return ("T_" if kind == "table" else "") + _legacy(logical)
    if style == "prefixed":
        return ("tbl_" if kind == "table" else "") + logical
    raise ValueError(f"unknown naming style {style!r}")


def _unique(names: dict[str, str], logical: str, phys: str) -> str:
    """Legacy abbreviations can collide; suffix a digit like a real 1990s schema."""
    used = set(names.values())
    out, n = phys, 2
    while out in used:
        out, n = f"{phys}{n}", n + 1
    names[logical] = out
    return out


def make_tenant(seed: int, index: int, *, rows_scale: float = 1.0) -> Tenant:
    r = random.Random(seed * 100_003 + index)
    dom = DOMAINS[r.randrange(len(DOMAINS))]
    style = NAMING_STYLES[r.randrange(len(NAMING_STYLES))]
    facts = [f for f in dom.facts if r.random() < 0.85] or [dom.facts[0]]
    dims_needed = sorted({d for f in facts for d in f.dims})
    key = f"t{index:03d}_{dom.key}"
    labels: dict[str, str] = {}
    roles: dict[str, Any] = {}
    tnames: dict[str, str] = {}
    tables: list[Table] = []
    dim_rows: dict[str, list[tuple[Any, ...]]] = {}

    def col(table: str, logical: str, label: str) -> str:
        phys = physical(style, logical, kind="column")
        labels[f"{table}.{phys}"] = label
        return phys

    for dname in dims_needed:
        spec = next(d for d in dom.dims if d.name == dname)
        tname = _unique(tnames, dname, physical(style, dname, kind="table"))
        labels[tname] = spec.label
        idc = col(tname, f"{dname}_id", f"{spec.label} id")
        namec = col(tname, f"{dname}_name", f"{spec.label} name")
        catc = col(tname, spec.categories[0], spec.categories[1])
        n = max(8, int(r.randint(12, 160) * max(rows_scale, 0.05)))
        rows = []
        for i in range(1, n + 1):
            stem = spec.names[r.randrange(len(spec.names))]
            rows.append(
                (i, f"{stem} {i:04d}", spec.categories[2][r.randrange(len(spec.categories[2]))])
            )
        dim_rows[dname] = rows
        tables.append(
            Table(tname, [(idc, "int"), (namec, "text"), (catc, "text")], [idc], [], rows)
        )
        roles[tname] = {
            "role": "dimension",
            "id": idc,
            "name": namec,
            "category": catc,
            "category_values": list(spec.categories[2]),
        }

    for f in facts:
        tname = _unique(tnames, f.name, physical(style, f.name, kind="table"))
        labels[tname] = f.label
        idc = col(tname, f"{f.name}_id", f"{f.label} id")
        cols: list[tuple[str, str]] = [(idc, "int")]
        fks: list[tuple[str, str, str]] = []
        dim_cols: dict[str, str] = {}
        for d in f.dims:
            dt = tnames[d]
            dspec = next(x for x in dom.dims if x.name == d)
            c = col(tname, f"{d}_id", dspec.label)
            dim_cols[dt] = c
            cols.append((c, "int"))
            fks.append((c, dt, roles[dt]["id"]))
        datec = col(tname, f.date_col[0], f.date_col[1])
        cols.append((datec, "date"))
        mcols = []
        for m, mlabel, _, _ in f.measures:
            mc = col(tname, m, mlabel)
            mcols.append(mc)
            cols.append((mc, "dec"))
        statc = None
        if f.status:
            statc = col(tname, f.status[0], f.status[1])
            cols.append((statc, "text"))
        n = max(50, int(r.randint(600, 4000) * rows_scale))
        rows = []
        for i in range(1, n + 1):
            row: list[Any] = [i]
            for d in f.dims:
                row.append(r.randint(1, len(dim_rows[d])))
            row.append(START + timedelta(days=r.randrange(DAYS)))
            for _, _, lo, hi in f.measures:
                # ~3% NULL measures: real sources have gaps, and SUM/AVG must skip them.
                row.append(None if r.random() < 0.03 else round(r.uniform(lo, hi), 2))
            if f.status:
                row.append(f.status[2][r.randrange(len(f.status[2]))])
            rows.append(tuple(row))
        tables.append(Table(tname, cols, [idc], fks, rows))
        roles[tname] = {
            "role": "fact",
            "id": idc,
            "dims": dim_cols,
            "date": datec,
            "measures": mcols,
            "status": statc,
            "status_values": list(f.status[2]) if f.status else [],
        }
        if f.lines:
            lname, llabel, lmeasures = f.lines
            lt = _unique(tnames, lname, physical(style, lname, kind="table"))
            labels[lt] = llabel
            hdr = col(lt, f"{f.name}_id", f.label.rstrip("s"))
            lno = col(lt, "line_no", "line number")
            lcols: list[tuple[str, str]] = [(hdr, "int"), (lno, "int")]
            lmcols = []
            for m, mlabel, _, _ in lmeasures:
                mc = col(lt, m, mlabel)
                lmcols.append(mc)
                lcols.append((mc, "dec"))
            lrows = []
            for h in range(1, n + 1):
                for ln in range(1, r.randint(1, 4) + 1):
                    vals = [round(r.uniform(lo, hi), 2) for _, _, lo, hi in lmeasures]
                    lrows.append((h, ln, *vals))
            tables.append(Table(lt, lcols, [hdr, lno], [(hdr, tname, idc)], lrows))
            roles[lt] = {"role": "lines", "header": tname, "header_col": hdr, "measures": lmcols}
    return Tenant(
        key,
        dom.key,
        dom.title,
        style,
        seed,
        Dataset(company=key, title=dom.title, tables=tables),
        labels,
        roles,
    )


def make_fleet(n: int, seed: int, *, rows_scale: float = 1.0) -> list[Tenant]:
    return [make_tenant(seed, i, rows_scale=rows_scale) for i in range(n)]
