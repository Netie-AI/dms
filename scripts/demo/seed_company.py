"""Harborline Distribution: a deterministic demo company for a buyer walk.

Synthetic wholesale data only. No product ask path.

  python scripts/demo/seed_company.py --scale full --seed 20261128 \\
    --duckdb data/demo/harborline.duckdb \\
    --write-gold tests/demo/gold.json \\
    --write-walk docs/demo/BUYER_WALK.md \\
    --write-catalog docs/demo/harborline_catalog.json

  python scripts/demo/seed_company.py --scale small --duckdb /tmp/h.duckdb \\
    --check-gold tests/demo/gold.json

  python scripts/demo/seed_company.py --scale full \\
    --duckdb data/demo/harborline.duckdb --postgres "$DATABASE_URL"

Same seed rebuilds the same rows. ``full`` is two years and at least a million
rows. ``small`` keeps the schema, the planted names, and the gold SQL runnable.
Gold answers are the SQL result on ``full``, not figures typed by hand.

ponytail: nightly on-hand is stored, then movements are the day-to-day delta.
Receipts and shipments do not post a perpetual ledger. Ceiling: those tables
will not tie to the snapshot. Upgrade: one stock ledger built from both.
"""

from __future__ import annotations

import argparse
import json
import textwrap
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = "harborline"
DEFAULT_SEED = 20261128
START = date(2024, 1, 1)
AS_OF = date(2025, 12, 31)
CUTOFF = date(2025, 8, 31)
STOCKOUT_START = date(2025, 6, 2)
STOCKOUT_END = date(2025, 8, 17)
CATALOG_PATH = ROOT / "docs" / "demo" / "harborline_catalog.json"

MONTH_PCT = {
    1: 82,
    2: 88,
    3: 105,
    4: 108,
    5: 110,
    6: 102,
    7: 95,
    8: 92,
    9: 106,
    10: 112,
    11: 118,
    12: 122,
}
DOW_PCT = {0: 100, 1: 100, 2: 100, 3: 100, 4: 96, 5: 35, 6: 15}
MONTH_NAMES = (
    "",
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
DOW_NAMES = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)
REGIONS = (
    (1, "NL", "Northland", "Minneapolis"),
    (2, "HL", "Heartland", "Kansas City"),
    (3, "CS", "Coastline", "Seattle"),
    (4, "GP", "Gulfplain", "Houston"),
    (5, "HR", "Highridge", "Denver"),
    (6, "LS", "Lakeshore", "Cleveland"),
)
DC_CODE = {
    "NL": "DC-NORTH",
    "HL": "DC-HEART",
    "CS": "DC-COAST",
    "GP": "DC-GULF",
    "HR": "DC-RIDGE",
    "LS": "DC-LAKE",
}
FIRST = (
    "Avery",
    "Blair",
    "Casey",
    "Drew",
    "Ellis",
    "Finley",
    "Gray",
    "Harper",
    "Indigo",
    "Jules",
    "Kai",
    "Logan",
    "Morgan",
    "Noel",
    "Parker",
    "Quinn",
    "Reese",
    "Sawyer",
    "Taylor",
    "Val",
)
LAST = (
    "Adler",
    "Beck",
    "Cho",
    "Diaz",
    "Ellison",
    "Frost",
    "Garcia",
    "Holt",
    "Iverson",
    "Janis",
    "Keller",
    "Lang",
    "Moss",
    "Nguyen",
    "Okada",
    "Pratt",
    "Quill",
    "Rossi",
    "Shah",
    "Tran",
)
ADJ = (
    "Bright",
    "Cedar",
    "Delta",
    "Elm",
    "Fair",
    "Granite",
    "Inland",
    "Juniper",
    "Keystone",
    "Lakeside",
    "Metro",
    "Oak",
    "Pioneer",
    "Quartz",
    "River",
    "Summit",
    "Twin",
    "Union",
    "Valley",
    "West",
)
NOUN = ("Supply", "Industrial", "MRO", "Wholesale", "Parts", "Trading")
ITEM_NOUNS = (
    "hex bolt",
    "nitrile glove",
    "stretch wrap",
    "led driver",
    "shop towel",
    "adjustable wrench",
    "air filter",
    "cable tie",
    "safety glasses",
    "pallet wrap",
    "junction box",
    "degreaser",
    "torque bit",
    "hose clamp",
    "wire nut",
    "absorbent pad",
)
SCALES: dict[str, dict[str, int]] = {
    "full": {
        "customers_per_region": 50,
        "products": 70,
        "suppliers": 24,
        "reps_per_region": 4,
        "warehouse_staff_per_region": 6,
        "procurement": 4,
        "finance": 4,
        "extra_warehouses": 2,
        "order_base": 220,
        "track_skus": 28,
        "min_lines": 2,
        "line_span": 3,
        "target_base": 480000,
    },
    "small": {
        "customers_per_region": 6,
        "products": 10,
        "suppliers": 6,
        "reps_per_region": 2,
        "warehouse_staff_per_region": 1,
        "procurement": 1,
        "finance": 1,
        "extra_warehouses": 0,
        "order_base": 2,
        "track_skus": 4,
        "min_lines": 1,
        "line_span": 2,
        "target_base": 8000,
    },
}


@dataclass(frozen=True)
class Col:
    name: str
    type: str
    description: str
    pk: bool = False
    null: bool = False
    unique: bool = False


@dataclass(frozen=True)
class Fk:
    column: str
    ref_table: str
    ref_column: str


@dataclass(frozen=True)
class Table:
    name: str
    description: str
    columns: tuple[Col, ...]
    fks: tuple[Fk, ...] = ()


def col(name: str, typ: str, description: str, *flags: str) -> Col:
    return Col(
        name,
        typ,
        description,
        pk="pk" in flags,
        null="null" in flags,
        unique="unique" in flags,
    )


def fk(column: str, ref_table: str, ref_column: str) -> Fk:
    return Fk(column, ref_table, ref_column)


def _tables() -> tuple[Table, ...]:
    money = "DECIMAL(14,2)"
    return (
        Table(
            "company_profile",
            "One row for Harborline Distribution, the demo wholesaler.",
            (
                col("company_id", "INTEGER", "Surrogate key for the company row.", "pk"),
                col("legal_name", "VARCHAR", "Registered legal name."),
                col("trade_name", "VARCHAR", "Name used with customers."),
                col("currency_code", "VARCHAR", "Currency of every money column. USD."),
                col("as_of_date", "DATE", "Last business date included in this load."),
                col("fiscal_year_start_month", "INTEGER", "Month number the fiscal year starts."),
                col("line_of_business", "VARCHAR", "What the company distributes."),
            ),
        ),
        Table(
            "regions",
            "Sales regions. Northland is the region with the late-2025 drop.",
            (
                col("region_id", "INTEGER", "Surrogate key for the region.", "pk"),
                col("region_code", "VARCHAR", "Short region code.", "unique"),
                col("region_name", "VARCHAR", "Sales region name, such as Northland."),
                col("city", "VARCHAR", "City of the regional distribution center."),
            ),
        ),
        Table(
            "departments",
            "Employee departments.",
            (
                col("department_id", "INTEGER", "Surrogate key for the department.", "pk"),
                col("department_name", "VARCHAR", "Department name.", "unique"),
            ),
        ),
        Table(
            "customer_segments",
            "How Harborline groups customer accounts.",
            (
                col("segment_id", "INTEGER", "Surrogate key for the segment.", "pk"),
                col("segment_name", "VARCHAR", "Segment name, such as Key account.", "unique"),
            ),
        ),
        Table(
            "brands",
            "Product brands sold by Harborline.",
            (
                col("brand_id", "INTEGER", "Surrogate key for the brand.", "pk"),
                col("brand_name", "VARCHAR", "Brand name.", "unique"),
            ),
        ),
        Table(
            "categories",
            "Product categories such as Hose, Fasteners, and Safety.",
            (
                col("category_id", "INTEGER", "Surrogate key for the category.", "pk"),
                col("category_name", "VARCHAR", "Category name.", "unique"),
            ),
        ),
        Table(
            "units_of_measure",
            "Units a SKU is sold in. EA is each, CS is a case.",
            (
                col("uom_code", "VARCHAR", "Unit code.", "pk"),
                col("uom_name", "VARCHAR", "Unit name."),
            ),
        ),
        Table(
            "carriers",
            "Outbound carriers that deliver customer orders.",
            (
                col("carrier_id", "INTEGER", "Surrogate key for the carrier.", "pk"),
                col("carrier_name", "VARCHAR", "Carrier name.", "unique"),
                col("mode", "VARCHAR", "Transport mode, such as ltl or parcel."),
                col(
                    "typical_transit_days",
                    "INTEGER",
                    "Typical transit days before the lane adjustment.",
                ),
            ),
        ),
        Table(
            "payment_terms",
            "Invoice payment terms.",
            (
                col("terms_id", "INTEGER", "Surrogate key for the terms.", "pk"),
                col("terms_name", "VARCHAR", "Terms name, such as Net 30.", "unique"),
                col("net_days", "INTEGER", "Days after the invoice date that payment is due."),
            ),
        ),
        Table(
            "channels",
            "Order channels: inside sales, web, or counter.",
            (
                col("channel_id", "INTEGER", "Surrogate key for the channel.", "pk"),
                col("channel_name", "VARCHAR", "Channel name.", "unique"),
            ),
        ),
        Table(
            "return_reasons",
            "Why a customer returned goods.",
            (
                col("reason_id", "INTEGER", "Surrogate key for the reason.", "pk"),
                col("reason_name", "VARCHAR", "Return reason.", "unique"),
            ),
        ),
        Table(
            "tax_codes",
            "Tax codes. This load assigns STD and does not add tax to invoices.",
            (
                col("tax_code", "VARCHAR", "Tax code.", "pk"),
                col("tax_name", "VARCHAR", "Tax code name."),
                col("rate", "DECIMAL(8,4)", "Tax rate as a fraction. 0.0625 is 6.25 percent."),
            ),
        ),
        Table(
            "gl_accounts",
            "Chart of accounts reference. This load does not post a journal.",
            (
                col("account_id", "INTEGER", "Surrogate key for the account.", "pk"),
                col("account_name", "VARCHAR", "Account name.", "unique"),
                col("account_type", "VARCHAR", "income, expense, asset, or liability."),
            ),
        ),
        Table(
            "warehouses",
            "Distribution centers and overflow bulk sites. Orders ship from the regional center.",
            (
                col("warehouse_id", "INTEGER", "Surrogate key for the warehouse.", "pk"),
                col("region_id", "INTEGER", "Region this warehouse serves."),
                col("warehouse_code", "VARCHAR", "Short code, such as DC-NORTH.", "unique"),
                col("warehouse_name", "VARCHAR", "Warehouse name."),
                col("city", "VARCHAR", "City of the warehouse."),
                col("is_primary", "BOOLEAN", "True when the region's orders ship from here."),
                col("capacity_pallets", "INTEGER", "Pallet positions the building can hold."),
            ),
            (fk("region_id", "regions", "region_id"),),
        ),
        Table(
            "warehouse_zones",
            "Pick and bulk zones inside a warehouse.",
            (
                col("zone_id", "INTEGER", "Surrogate key for the zone.", "pk"),
                col("warehouse_id", "INTEGER", "Warehouse that contains the zone."),
                col("zone_code", "VARCHAR", "Short zone code.", "unique"),
                col("zone_type", "VARCHAR", "Zone type: pick or bulk."),
            ),
            (fk("warehouse_id", "warehouses", "warehouse_id"),),
        ),
        Table(
            "employees",
            "Harborline employees. Account executives own customer orders.",
            (
                col("employee_id", "INTEGER", "Surrogate key for the employee.", "pk"),
                col("department_id", "INTEGER", "Department the employee works in."),
                col("region_id", "INTEGER", "Home region."),
                col("warehouse_id", "INTEGER", "Home warehouse. Empty for finance staff.", "null"),
                col("full_name", "VARCHAR", "Employee name."),
                col("job_title", "VARCHAR", "Job title."),
                col("hire_date", "DATE", "Date the employee was hired."),
                col("email", "VARCHAR", "Work email on the example.test domain.", "unique"),
                col("active", "BOOLEAN", "True when the employee is active."),
            ),
            (
                fk("department_id", "departments", "department_id"),
                fk("region_id", "regions", "region_id"),
                fk("warehouse_id", "warehouses", "warehouse_id"),
            ),
        ),
        Table(
            "employee_assignments",
            "Warehouse assignment for an employee who works a building.",
            (
                col("assignment_id", "INTEGER", "Surrogate key for the assignment.", "pk"),
                col("employee_id", "INTEGER", "Employee on the assignment."),
                col("warehouse_id", "INTEGER", "Warehouse the employee is assigned to."),
                col("role_name", "VARCHAR", "Role at that warehouse."),
                col("start_date", "DATE", "Date the assignment started."),
            ),
            (
                fk("employee_id", "employees", "employee_id"),
                fk("warehouse_id", "warehouses", "warehouse_id"),
            ),
        ),
        Table(
            "region_managers",
            "Manager responsible for a sales region.",
            (
                col("region_id", "INTEGER", "Region the manager runs.", "pk"),
                col("employee_id", "INTEGER", "Employee who manages the region."),
                col("start_date", "DATE", "Date the manager took the region."),
            ),
            (
                fk("region_id", "regions", "region_id"),
                fk("employee_id", "employees", "employee_id"),
            ),
        ),
        Table(
            "customers",
            "Customer accounts. CUST-PINNACLE is the Northland key account that stops ordering.",
            (
                col("customer_id", "INTEGER", "Surrogate key for the customer.", "pk"),
                col("customer_code", "VARCHAR", "Stable customer code.", "unique"),
                col("customer_name", "VARCHAR", "Customer account name."),
                col("segment_id", "INTEGER", "Customer segment."),
                col("region_id", "INTEGER", "Sales region that owns the account."),
                col("tax_code", "VARCHAR", "Tax code on the account. This load uses STD."),
                col("city", "VARCHAR", "Customer city."),
                col("opened_on", "DATE", "Date the account opened."),
                col("credit_limit", money, "Credit limit in USD."),
                col("active", "BOOLEAN", "True when the account can place orders."),
            ),
            (
                fk("segment_id", "customer_segments", "segment_id"),
                fk("region_id", "regions", "region_id"),
                fk("tax_code", "tax_codes", "tax_code"),
            ),
        ),
        Table(
            "customer_sites",
            "Ship-to sites. This load has one default site per customer.",
            (
                col("site_id", "INTEGER", "Surrogate key for the site.", "pk"),
                col("customer_id", "INTEGER", "Customer that owns the site."),
                col("city", "VARCHAR", "Ship-to city."),
                col("is_default", "BOOLEAN", "True when orders default to this site."),
            ),
            (fk("customer_id", "customers", "customer_id"),),
        ),
        Table(
            "customer_contacts",
            "Buyer contacts at a customer account.",
            (
                col("contact_id", "INTEGER", "Surrogate key for the contact.", "pk"),
                col("customer_id", "INTEGER", "Customer the contact buys for."),
                col("full_name", "VARCHAR", "Contact name."),
                col("role_name", "VARCHAR", "Role at the customer, such as buyer."),
                col("email", "VARCHAR", "Work email on the example.test domain."),
            ),
            (fk("customer_id", "customers", "customer_id"),),
        ),
        Table(
            "suppliers",
            "Suppliers. Late deliveries are measured on inbound receipts, not on this row.",
            (
                col("supplier_id", "INTEGER", "Surrogate key for the supplier.", "pk"),
                col("supplier_code", "VARCHAR", "Stable supplier code.", "unique"),
                col("supplier_name", "VARCHAR", "Supplier name."),
                col("home_warehouse_id", "INTEGER", "Warehouse that receives this supplier."),
                col("city", "VARCHAR", "Supplier city."),
                col("lead_time_days", "INTEGER", "Quoted lead time in days."),
                col("preferred", "BOOLEAN", "True when procurement marks the supplier preferred."),
            ),
            (fk("home_warehouse_id", "warehouses", "warehouse_id"),),
        ),
        Table(
            "supplier_contacts",
            "Contacts at a supplier.",
            (
                col("contact_id", "INTEGER", "Surrogate key for the contact.", "pk"),
                col("supplier_id", "INTEGER", "Supplier the contact works for."),
                col("full_name", "VARCHAR", "Contact name."),
                col("role_name", "VARCHAR", "Role at the supplier."),
                col("email", "VARCHAR", "Work email on the example.test domain."),
            ),
            (fk("supplier_id", "suppliers", "supplier_id"),),
        ),
        Table(
            "products",
            "Product families. A product has one each-SKU and one case-SKU.",
            (
                col("product_id", "INTEGER", "Surrogate key for the product.", "pk"),
                col("brand_id", "INTEGER", "Brand of the product."),
                col("category_id", "INTEGER", "Category of the product."),
                col("product_name", "VARCHAR", "Product family name."),
            ),
            (
                fk("brand_id", "brands", "brand_id"),
                fk("category_id", "categories", "category_id"),
            ),
        ),
        Table(
            "skus",
            "Sellable SKUs. HX-440 is the each-pack that stocks out at DC-NORTH.",
            (
                col("sku_id", "INTEGER", "Surrogate key for the SKU.", "pk"),
                col("product_id", "INTEGER", "Product family of the SKU."),
                col("sku_code", "VARCHAR", "Stable SKU code.", "unique"),
                col("sku_name", "VARCHAR", "SKU name a buyer would recognize."),
                col("uom_code", "VARCHAR", "Unit of measure code."),
                col("list_price", money, "List price in USD for one unit."),
                col("standard_cost", money, "Standard cost in USD for one unit."),
                col("weight_kg", "DECIMAL(8,2)", "Shipping weight in kilograms."),
            ),
            (
                fk("product_id", "products", "product_id"),
                fk("uom_code", "units_of_measure", "uom_code"),
            ),
        ),
        Table(
            "sku_suppliers",
            "Primary supplier for a SKU. One row per SKU in this load.",
            (
                col("sku_id", "INTEGER", "SKU.", "pk"),
                col("supplier_id", "INTEGER", "Primary supplier of the SKU."),
                col("is_primary", "BOOLEAN", "True on the primary supplier row."),
            ),
            (
                fk("sku_id", "skus", "sku_id"),
                fk("supplier_id", "suppliers", "supplier_id"),
            ),
        ),
        Table(
            "carrier_lanes",
            "Transit days for a carrier from a warehouse into a region.",
            (
                col("lane_id", "INTEGER", "Surrogate key for the lane.", "pk"),
                col("carrier_id", "INTEGER", "Carrier on the lane."),
                col("warehouse_id", "INTEGER", "Origin warehouse."),
                col("region_id", "INTEGER", "Destination region."),
                col("transit_days", "INTEGER", "Planned transit days on this lane."),
            ),
            (
                fk("carrier_id", "carriers", "carrier_id"),
                fk("warehouse_id", "warehouses", "warehouse_id"),
                fk("region_id", "regions", "region_id"),
            ),
        ),
        Table(
            "price_lists",
            "Price lists. This load has one standard list.",
            (
                col("price_list_id", "INTEGER", "Surrogate key for the price list.", "pk"),
                col("price_list_name", "VARCHAR", "Price list name."),
                col("currency_code", "VARCHAR", "Currency of the list prices."),
            ),
        ),
        Table(
            "price_list_items",
            "List price of a SKU on a price list.",
            (
                col("price_list_id", "INTEGER", "Price list.", "pk"),
                col("sku_id", "INTEGER", "SKU.", "pk"),
                col("unit_price", money, "Unit price in USD."),
                col("effective_from", "DATE", "Date this price became effective."),
            ),
            (
                fk("price_list_id", "price_lists", "price_list_id"),
                fk("sku_id", "skus", "sku_id"),
            ),
        ),
        Table(
            "promotions",
            "Promotions. Order lines in this load keep discount_pct at zero.",
            (
                col("promotion_id", "INTEGER", "Surrogate key for the promotion.", "pk"),
                col("promotion_name", "VARCHAR", "Promotion name."),
                col("start_date", "DATE", "First date the promotion is offered."),
                col("end_date", "DATE", "Last date the promotion is offered."),
                col(
                    "discount_pct",
                    "DECIMAL(5,2)",
                    "Offered discount percent. Not written on lines.",
                ),
            ),
        ),
        Table(
            "promotion_skus",
            "SKUs included in a promotion.",
            (
                col("promotion_id", "INTEGER", "Promotion.", "pk"),
                col("sku_id", "INTEGER", "SKU on the promotion.", "pk"),
            ),
            (
                fk("promotion_id", "promotions", "promotion_id"),
                fk("sku_id", "skus", "sku_id"),
            ),
        ),
        Table(
            "cost_centers",
            "Cost centers, one per sales region.",
            (
                col("cost_center_id", "INTEGER", "Surrogate key for the cost center.", "pk"),
                col("region_id", "INTEGER", "Region the cost center belongs to."),
                col("cost_center_name", "VARCHAR", "Cost center name."),
            ),
            (fk("region_id", "regions", "region_id"),),
        ),
        Table(
            "sales_targets",
            "Monthly revenue target by region in USD. Not a forecast of actuals.",
            (
                col("region_id", "INTEGER", "Region the target applies to.", "pk"),
                col("target_year", "INTEGER", "Calendar year.", "pk"),
                col("target_month", "INTEGER", "Month number, 1 through 12.", "pk"),
                col("target_usd", money, "Revenue target in USD for that month."),
            ),
            (fk("region_id", "regions", "region_id"),),
        ),
        Table(
            "calendar",
            "One row per day from 2024-01-01 through 2025-12-31.",
            (
                col("calendar_date", "DATE", "Calendar date.", "pk"),
                col("calendar_year", "INTEGER", "Calendar year."),
                col("quarter", "INTEGER", "Calendar quarter, 1 through 4."),
                col("calendar_month", "INTEGER", "Month number, 1 through 12."),
                col("month_name", "VARCHAR", "English month name."),
                col("dow", "INTEGER", "Day of week. 0 is Monday, 6 is Sunday."),
                col("dow_name", "VARCHAR", "English weekday name."),
                col("is_weekend", "BOOLEAN", "True on Saturday and Sunday."),
            ),
        ),
        Table(
            "orders",
            "Customer orders. One row per order. Status filled or short.",
            (
                col("order_id", "INTEGER", "Surrogate key for the order.", "pk"),
                col("customer_id", "INTEGER", "Customer that placed the order."),
                col("site_id", "INTEGER", "Ship-to site."),
                col("warehouse_id", "INTEGER", "Warehouse that ships the order."),
                col("channel_id", "INTEGER", "Channel the order came in on."),
                col("terms_id", "INTEGER", "Payment terms copied onto the order."),
                col("employee_id", "INTEGER", "Account executive on the order."),
                col("order_date", "DATE", "Date placed. Monthly revenue uses this date."),
                col("status", "VARCHAR", "filled when every line shipped, otherwise short."),
            ),
            (
                fk("customer_id", "customers", "customer_id"),
                fk("site_id", "customer_sites", "site_id"),
                fk("warehouse_id", "warehouses", "warehouse_id"),
                fk("channel_id", "channels", "channel_id"),
                fk("terms_id", "payment_terms", "terms_id"),
                fk("employee_id", "employees", "employee_id"),
            ),
        ),
        Table(
            "order_lines",
            "Order lines. Recognized revenue is line_amount, fulfilled quantity times price.",
            (
                col("order_line_id", "INTEGER", "Surrogate key for the line.", "pk"),
                col("order_id", "INTEGER", "Order this line belongs to."),
                col("sku_id", "INTEGER", "SKU ordered."),
                col("qty", "INTEGER", "Quantity ordered."),
                col("fulfilled_qty", "INTEGER", "Quantity shipped. Zero means it was not filled."),
                col("unit_price", money, "Selling price in USD for one unit."),
                col("discount_pct", "DECIMAL(5,2)", "Discount percent. This load writes zero."),
                col(
                    "line_amount",
                    money,
                    "Recognized revenue in USD: fulfilled quantity times unit price.",
                ),
            ),
            (
                fk("order_id", "orders", "order_id"),
                fk("sku_id", "skus", "sku_id"),
            ),
        ),
        Table(
            "shipments",
            "Outbound shipment for an order that had at least one filled line.",
            (
                col("shipment_id", "INTEGER", "Surrogate key for the shipment.", "pk"),
                col("order_id", "INTEGER", "Order this shipment fills."),
                col("warehouse_id", "INTEGER", "Warehouse the shipment left."),
                col("carrier_id", "INTEGER", "Carrier that took the shipment."),
                col("ship_date", "DATE", "Date the warehouse shipped."),
                col(
                    "delivered_date",
                    "DATE",
                    "Delivery date, or the expected date while in transit.",
                ),
                col("status", "VARCHAR", "delivered or in_transit."),
            ),
            (
                fk("order_id", "orders", "order_id"),
                fk("warehouse_id", "warehouses", "warehouse_id"),
                fk("carrier_id", "carriers", "carrier_id"),
            ),
        ),
        Table(
            "shipment_lines",
            "Filled units on a shipment. Unfilled units are not shipped.",
            (
                col("shipment_line_id", "INTEGER", "Surrogate key for the shipment line.", "pk"),
                col("shipment_id", "INTEGER", "Shipment."),
                col("order_line_id", "INTEGER", "Order line that was shipped."),
                col("sku_id", "INTEGER", "SKU shipped."),
                col("qty", "INTEGER", "Quantity shipped."),
            ),
            (
                fk("shipment_id", "shipments", "shipment_id"),
                fk("order_line_id", "order_lines", "order_line_id"),
                fk("sku_id", "skus", "sku_id"),
            ),
        ),
        Table(
            "invoices",
            "One invoice per order. Total equals recognized line revenue. No tax is added.",
            (
                col("invoice_id", "INTEGER", "Surrogate key for the invoice.", "pk"),
                col("order_id", "INTEGER", "Order this invoice bills."),
                col("customer_id", "INTEGER", "Customer billed."),
                col("terms_id", "INTEGER", "Payment terms."),
                col("invoice_date", "DATE", "Invoice date. Same as the order date in this load."),
                col("due_date", "DATE", "Date payment is due."),
                col("total_amount", money, "Invoice total in USD, the sum of line revenue."),
                col("status", "VARCHAR", "paid when a payment is applied, otherwise open."),
            ),
            (
                fk("order_id", "orders", "order_id"),
                fk("customer_id", "customers", "customer_id"),
                fk("terms_id", "payment_terms", "terms_id"),
            ),
        ),
        Table(
            "invoice_lines",
            "Invoice lines copied from order lines after fulfillment.",
            (
                col("invoice_line_id", "INTEGER", "Surrogate key for the invoice line.", "pk"),
                col("invoice_id", "INTEGER", "Invoice."),
                col("order_line_id", "INTEGER", "Order line billed."),
                col("sku_id", "INTEGER", "SKU billed."),
                col("qty", "INTEGER", "Quantity billed, equal to fulfilled quantity."),
                col("unit_price", money, "Unit price in USD."),
                col("line_amount", money, "Line revenue in USD."),
            ),
            (
                fk("invoice_id", "invoices", "invoice_id"),
                fk("order_line_id", "order_lines", "order_line_id"),
                fk("sku_id", "skus", "sku_id"),
            ),
        ),
        Table(
            "payments",
            "Customer payments against invoices that fell due inside the load window.",
            (
                col("payment_id", "INTEGER", "Surrogate key for the payment.", "pk"),
                col("customer_id", "INTEGER", "Customer that paid."),
                col("payment_date", "DATE", "Date the payment was received."),
                col("amount", money, "Payment amount in USD."),
                col("method", "VARCHAR", "Payment method: ach, check, or card."),
            ),
            (fk("customer_id", "customers", "customer_id"),),
        ),
        Table(
            "payment_applications",
            "How a payment was applied to an invoice. One application per payment here.",
            (
                col("application_id", "INTEGER", "Surrogate key for the application.", "pk"),
                col("payment_id", "INTEGER", "Payment applied."),
                col("invoice_id", "INTEGER", "Invoice the payment was applied to."),
                col("amount", money, "Amount applied in USD."),
            ),
            (
                fk("payment_id", "payments", "payment_id"),
                fk("invoice_id", "invoices", "invoice_id"),
            ),
        ),
        Table(
            "purchase_orders",
            "Purchase orders to suppliers, one per supplier per week.",
            (
                col("po_id", "INTEGER", "Surrogate key for the purchase order.", "pk"),
                col("supplier_id", "INTEGER", "Supplier the PO was sent to."),
                col("warehouse_id", "INTEGER", "Warehouse that will receive the goods."),
                col("order_date", "DATE", "Date the PO was placed. First day of that week bucket."),
                col("expected_date", "DATE", "Expected receipt date, order date plus lead time."),
                col("status", "VARCHAR", "PO status. This load marks them received."),
                col("week_index", "INTEGER", "Week bucket. 0 is 2024-01-01, then every 7 days."),
            ),
            (
                fk("supplier_id", "suppliers", "supplier_id"),
                fk("warehouse_id", "warehouses", "warehouse_id"),
            ),
        ),
        Table(
            "purchase_order_lines",
            "Lines on a purchase order.",
            (
                col("po_line_id", "INTEGER", "Surrogate key for the PO line.", "pk"),
                col("po_id", "INTEGER", "Purchase order."),
                col("sku_id", "INTEGER", "SKU ordered from the supplier."),
                col("qty", "INTEGER", "Quantity ordered."),
                col("unit_cost", money, "Unit cost in USD."),
            ),
            (
                fk("po_id", "purchase_orders", "po_id"),
                fk("sku_id", "skus", "sku_id"),
            ),
        ),
        Table(
            "inbound_receipts",
            "Goods receipts. A late delivery is late_days above zero versus the promised date.",
            (
                col("receipt_id", "INTEGER", "Surrogate key for the receipt.", "pk"),
                col("po_id", "INTEGER", "Purchase order this receipt closes against."),
                col("supplier_id", "INTEGER", "Supplier that shipped."),
                col("warehouse_id", "INTEGER", "Warehouse that received the goods."),
                col("promised_date", "DATE", "Date the supplier promised delivery."),
                col("received_date", "DATE", "Date the warehouse actually received the goods."),
                col(
                    "late_days",
                    "INTEGER",
                    "Days after the promised date. A late delivery is a value above zero.",
                ),
            ),
            (
                fk("po_id", "purchase_orders", "po_id"),
                fk("supplier_id", "suppliers", "supplier_id"),
                fk("warehouse_id", "warehouses", "warehouse_id"),
            ),
        ),
        Table(
            "inbound_receipt_lines",
            "SKUs on an inbound receipt.",
            (
                col("receipt_line_id", "INTEGER", "Surrogate key for the receipt line.", "pk"),
                col("receipt_id", "INTEGER", "Receipt."),
                col("sku_id", "INTEGER", "SKU received."),
                col("qty", "INTEGER", "Quantity received."),
            ),
            (
                fk("receipt_id", "inbound_receipts", "receipt_id"),
                fk("sku_id", "skus", "sku_id"),
            ),
        ),
        Table(
            "returns",
            "Customer returns against an order.",
            (
                col("return_id", "INTEGER", "Surrogate key for the return.", "pk"),
                col("order_id", "INTEGER", "Order the goods came from."),
                col("customer_id", "INTEGER", "Customer that returned the goods."),
                col("reason_id", "INTEGER", "Return reason."),
                col("return_date", "DATE", "Date the return was received."),
                col("status", "VARCHAR", "Return status. This load uses received."),
            ),
            (
                fk("order_id", "orders", "order_id"),
                fk("customer_id", "customers", "customer_id"),
                fk("reason_id", "return_reasons", "reason_id"),
            ),
        ),
        Table(
            "return_lines",
            "Units and amount on a customer return.",
            (
                col("return_line_id", "INTEGER", "Surrogate key for the return line.", "pk"),
                col("return_id", "INTEGER", "Return."),
                col("sku_id", "INTEGER", "SKU returned."),
                col("qty", "INTEGER", "Quantity returned."),
                col("amount", money, "Return amount in USD."),
            ),
            (
                fk("return_id", "returns", "return_id"),
                fk("sku_id", "skus", "sku_id"),
            ),
        ),
        Table(
            "inventory_snapshots",
            "Nightly on-hand count by warehouse and SKU. Not a roll-forward of receipts.",
            (
                col("snapshot_date", "DATE", "Date of the nightly count.", "pk"),
                col("warehouse_id", "INTEGER", "Warehouse counted.", "pk"),
                col("sku_id", "INTEGER", "SKU counted.", "pk"),
                col("qty_on_hand", "INTEGER", "Units on hand at day end. Zero is a stockout."),
                col("qty_on_order", "INTEGER", "Units on purchase order and not yet in the bin."),
            ),
            (
                fk("warehouse_id", "warehouses", "warehouse_id"),
                fk("sku_id", "skus", "sku_id"),
            ),
        ),
        Table(
            "inventory_movements",
            "Change in on-hand versus the prior snapshot for the same warehouse and SKU.",
            (
                col("movement_id", "INTEGER", "Surrogate key for the movement.", "pk"),
                col("warehouse_id", "INTEGER", "Warehouse."),
                col("sku_id", "INTEGER", "SKU."),
                col("movement_date", "DATE", "Snapshot date this change is measured on."),
                col("qty", "INTEGER", "Units added, or removed when negative."),
                col("movement_type", "VARCHAR", "opening on the first day, otherwise net."),
            ),
            (
                fk("warehouse_id", "warehouses", "warehouse_id"),
                fk("sku_id", "skus", "sku_id"),
            ),
        ),
    )


TABLES: tuple[Table, ...] = _tables()


def quote(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def sql_text(text: str) -> str:
    return textwrap.dedent(text).strip()


def validate_model() -> None:
    names = [table.name for table in TABLES]
    if len(names) < 40:
        raise RuntimeError(f"need at least 40 tables, found {len(names)}")
    if len(names) != len(set(names)):
        raise RuntimeError("duplicate table name")
    by = {table.name: table for table in TABLES}
    seen: set[str] = set()
    for table in TABLES:
        cols = [item.name for item in table.columns]
        if len(cols) != len(set(cols)):
            raise RuntimeError(f"duplicate column on {table.name}")
        if not table.description.strip():
            raise RuntimeError(f"blank description on {table.name}")
        for item in table.columns:
            if not item.description.strip():
                raise RuntimeError(f"blank description on {table.name}.{item.name}")
        for link in table.fks:
            if link.column not in cols:
                raise RuntimeError(f"{table.name}.{link.column} is not a column")
            parent = by.get(link.ref_table)
            if parent is None or link.ref_table not in seen:
                raise RuntimeError(
                    f"{table.name}.{link.column} -> {link.ref_table} is out of order"
                )
            parent_cols = {item.name for item in parent.columns}
            if link.ref_column not in parent_cols:
                raise RuntimeError(f"{link.ref_table}.{link.ref_column} missing")
        seen.add(table.name)


def ddl(table: Table) -> str:
    lines: list[str] = []
    pk = [item.name for item in table.columns if item.pk]
    for item in table.columns:
        if item.pk and len(pk) == 1:
            lines.append(f"{item.name} {item.type} PRIMARY KEY")
            continue
        piece = f"{item.name} {item.type}"
        if not item.null:
            piece += " NOT NULL"
        if item.unique:
            piece += " UNIQUE"
        lines.append(piece)
    if len(pk) > 1:
        lines.append("PRIMARY KEY (" + ", ".join(pk) + ")")
    for link in table.fks:
        lines.append(
            f"FOREIGN KEY ({link.column}) REFERENCES {SCHEMA}.{link.ref_table} ({link.ref_column})"
        )
    body = ",\n  ".join(lines)
    return f"CREATE TABLE {SCHEMA}.{table.name} (\n  {body}\n)"


def catalog_document() -> dict:
    datasets = []
    relationships = []
    for table in TABLES:
        pk = {item.name for item in table.columns if item.pk}
        datasets.append(
            {
                "name": table.name,
                "description": table.description,
                "columns": [
                    {
                        "name": item.name,
                        "type": item.type,
                        "description": item.description,
                        "primary_key": item.name in pk,
                    }
                    for item in table.columns
                ],
            }
        )
        for link in table.fks:
            relationships.append(
                {
                    "name": f"{table.name}_{link.column}_{link.ref_table}",
                    "from": table.name,
                    "from_column": link.column,
                    "to": link.ref_table,
                    "to_column": link.ref_column,
                }
            )
    return {
        "dialect": "duckdb",
        "schema": SCHEMA,
        "description": (
            "Harborline Distribution, a synthetic mid-size industrial wholesaler. "
            "Rows run from 2024-01-01 through 2025-12-31. Currency is USD. "
            "Tables live in schema harborline. Qualify SQL as harborline.table_name. "
            "Recognized revenue is order_lines.line_amount."
        ),
        "datasets": datasets,
        "relationships": relationships,
        "measures": [
            {
                "name": "recognized_revenue",
                "grain": "order_lines",
                "expression": "sum(line_amount)",
                "description": (
                    "Recognized revenue in USD. Fulfilled quantity times unit price, "
                    "dated by orders.order_date."
                ),
            },
            {
                "name": "late_receipt_rate",
                "grain": "inbound_receipts",
                "expression": "avg(case when late_days > 0 then 1 else 0 end)",
                "description": "Share of inbound receipts that are a late delivery.",
            },
            {
                "name": "stockout_days",
                "grain": "inventory_snapshots",
                "expression": "count rows where qty_on_hand = 0",
                "description": "Nights a warehouse and SKU had a stockout, on-hand zero.",
            },
        ],
    }


def dump_json(payload: dict) -> str:
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def order_count_for(day: date, base: int, seed: int) -> int:
    jitter = 96 + ((day.toordinal() * 17 + seed) % 9)
    count = (base * MONTH_PCT[day.month] * DOW_PCT[day.weekday()] * jitter) // 1_000_000
    return max(1, count)


def person_name(index: int) -> str:
    return f"{FIRST[index % len(FIRST)]} {LAST[(index * 3) % len(LAST)]}"


def customer_name(index: int) -> str:
    return f"{ADJ[index % len(ADJ)]} {NOUN[(index // len(ADJ)) % len(NOUN)]} {index:03d}"


def cents(amount: int) -> Decimal:
    return (Decimal(amount) / Decimal(100)).quantize(Decimal("0.01"))


@dataclass
class Ctx:
    pinnacle_id: int
    pinnacle_site: int
    kelso_id: int
    arden_id: int
    northland_id: int
    dc_north_id: int
    n_skus: int
    n_channels: int
    n_terms: int
    n_reasons: int
    n_carriers: int
    pick_n: int
    reps_per_region: int
    max_orders: int
    max_extra: int
    track_skus: int
    min_lines: int
    line_span: int


def insert_rows(con, table: str, columns: list[str], rows: list[tuple]) -> None:
    if not rows:
        raise RuntimeError(f"no rows for {table}")
    marks = ", ".join(["?"] * len(columns))
    names = ", ".join(columns)
    con.executemany(
        f"INSERT INTO {SCHEMA}.{table} ({names}) VALUES ({marks})",
        rows,
    )


def load_dimensions(con, scale: dict[str, int], seed: int) -> Ctx:
    insert_rows(
        con,
        "company_profile",
        [
            "company_id",
            "legal_name",
            "trade_name",
            "currency_code",
            "as_of_date",
            "fiscal_year_start_month",
            "line_of_business",
        ],
        [
            (
                1,
                "Harborline Distribution LLC",
                "Harborline",
                "USD",
                AS_OF,
                1,
                "Industrial and MRO wholesale distribution",
            )
        ],
    )
    insert_rows(
        con,
        "regions",
        ["region_id", "region_code", "region_name", "city"],
        list(REGIONS),
    )
    insert_rows(
        con,
        "departments",
        ["department_id", "department_name"],
        [(1, "Sales"), (2, "Warehouse"), (3, "Procurement"), (4, "Finance")],
    )
    insert_rows(
        con,
        "customer_segments",
        ["segment_id", "segment_name"],
        [(1, "Key account"), (2, "Regional"), (3, "Counter"), (4, "Web")],
    )
    insert_rows(
        con,
        "brands",
        ["brand_id", "brand_name"],
        [
            (1, "Harborline House"),
            (2, "Northmill"),
            (3, "Brightpack"),
            (4, "Fieldline"),
            (5, "Redbolt"),
            (6, "Plainstock"),
        ],
    )
    insert_rows(
        con,
        "categories",
        ["category_id", "category_name"],
        [
            (1, "Hose"),
            (2, "Fasteners"),
            (3, "Safety"),
            (4, "Packaging"),
            (5, "Electrical"),
            (6, "Janitorial"),
            (7, "Tools"),
            (8, "HVAC"),
        ],
    )
    insert_rows(
        con,
        "units_of_measure",
        ["uom_code", "uom_name"],
        [("EA", "Each"), ("CS", "Case")],
    )
    carriers = [
        (1, "Redline Freight", "ltl", 2),
        (2, "Harbor Cartage", "truck", 5),
        (3, "Inland Parcel", "parcel", 3),
    ]
    insert_rows(
        con,
        "carriers",
        ["carrier_id", "carrier_name", "mode", "typical_transit_days"],
        carriers,
    )
    terms = [(1, "Net 15", 15), (2, "Net 30", 30), (3, "Net 45", 45)]
    insert_rows(con, "payment_terms", ["terms_id", "terms_name", "net_days"], terms)
    channels = [(1, "Inside sales"), (2, "Web"), (3, "Counter")]
    insert_rows(con, "channels", ["channel_id", "channel_name"], channels)
    reasons = [
        (1, "Damaged"),
        (2, "Wrong item"),
        (3, "Ordered in error"),
        (4, "Late delivery"),
        (5, "Other"),
    ]
    insert_rows(con, "return_reasons", ["reason_id", "reason_name"], reasons)
    insert_rows(
        con,
        "tax_codes",
        ["tax_code", "tax_name", "rate"],
        [
            ("STD", "Standard zero", Decimal("0.0000")),
            ("STATE", "State sample", Decimal("0.0625")),
        ],
    )
    insert_rows(
        con,
        "gl_accounts",
        ["account_id", "account_name", "account_type"],
        [
            (1, "Revenue", "income"),
            (2, "Cost of goods", "expense"),
            (3, "Inventory", "asset"),
            (4, "Accounts receivable", "asset"),
            (5, "Accounts payable", "liability"),
            (6, "Cash", "asset"),
        ],
    )
    warehouses = []
    for region_id, code, name, city in REGIONS:
        warehouses.append(
            (
                region_id,
                region_id,
                DC_CODE[code],
                f"{name} distribution center",
                city,
                True,
                2400 + region_id * 100,
            )
        )
    if scale["extra_warehouses"]:
        warehouses.append((7, 2, "BK-HEART", "Heartland bulk", "Kansas City", False, 4000))
        warehouses.append((8, 3, "BK-COAST", "Coastline bulk", "Seattle", False, 3600))
    insert_rows(
        con,
        "warehouses",
        [
            "warehouse_id",
            "region_id",
            "warehouse_code",
            "warehouse_name",
            "city",
            "is_primary",
            "capacity_pallets",
        ],
        warehouses,
    )
    zones = []
    zone_id = 1
    for warehouse_id, _region, code, _name, _city, _primary, _cap in warehouses:
        for zone_type in ("pick", "bulk"):
            zones.append((zone_id, warehouse_id, f"{code}-{zone_type.upper()}", zone_type))
            zone_id += 1
    insert_rows(
        con,
        "warehouse_zones",
        ["zone_id", "warehouse_id", "zone_code", "zone_type"],
        zones,
    )

    employees: list[tuple] = []
    assignments: list[tuple] = []
    managers: list[tuple] = []
    reps: dict[int, list[int]] = {region_id: [] for region_id, *_rest in REGIONS}
    eid = 1
    assignment_id = 1

    def add_employee(dept: int, region_id: int, warehouse_id: int | None, title: str) -> int:
        nonlocal eid, assignment_id
        current = eid
        hired = date(2019, 1, 1) + timedelta(days=(current * 17) % 1400)
        employees.append(
            (
                current,
                dept,
                region_id,
                warehouse_id,
                person_name(current),
                title,
                hired,
                f"e{current}@example.test",
                True,
            )
        )
        if warehouse_id is not None:
            assignments.append((assignment_id, current, warehouse_id, title, hired))
            assignment_id += 1
        eid += 1
        return current

    for region_id, _code, _name, _city in REGIONS:
        manager = add_employee(1, region_id, region_id, "Regional Manager")
        hired = employees[-1][6]
        managers.append((region_id, manager, hired))
        for _slot in range(scale["reps_per_region"]):
            reps[region_id].append(add_employee(1, region_id, region_id, "Account Executive"))
        for _slot in range(scale["warehouse_staff_per_region"]):
            add_employee(2, region_id, region_id, "Warehouse associate")
    for index in range(scale["procurement"]):
        region_id = (index % 6) + 1
        add_employee(3, region_id, region_id, "Buyer")
    for index in range(scale["finance"]):
        add_employee(4, 1, None, "Accountant")
    insert_rows(
        con,
        "employees",
        [
            "employee_id",
            "department_id",
            "region_id",
            "warehouse_id",
            "full_name",
            "job_title",
            "hire_date",
            "email",
            "active",
        ],
        employees,
    )
    insert_rows(
        con,
        "employee_assignments",
        ["assignment_id", "employee_id", "warehouse_id", "role_name", "start_date"],
        assignments,
    )
    insert_rows(
        con,
        "region_managers",
        ["region_id", "employee_id", "start_date"],
        managers,
    )

    customers = []
    sites = []
    contacts = []
    picks = []
    pinnacle_id = 0
    cid = 0
    for region_id, code, _name, city in REGIONS:
        for offset in range(scale["customers_per_region"]):
            cid += 1
            if code == "NL" and offset == 0:
                customer_code = "CUST-PINNACLE"
                name = "Pinnacle Industrial Supply"
                segment_id = 1
                pinnacle_id = cid
            else:
                customer_code = f"CUST-{cid:05d}"
                name = customer_name(cid)
                segment_id = 1 + (cid % 4)
            credit = 250000 if segment_id == 1 else 40000 + (cid % 5) * 5000
            customers.append(
                (
                    cid,
                    customer_code,
                    name,
                    segment_id,
                    region_id,
                    "STD",
                    city,
                    date(2022, 1, 1) + timedelta(days=cid % 500),
                    cents(credit * 100),
                    True,
                )
            )
            sites.append((cid, cid, city, True))
            contacts.append(
                (
                    cid,
                    cid,
                    person_name(cid + 50),
                    "Buyer",
                    f"c{cid}@example.test",
                )
            )
            if customer_code != "CUST-PINNACLE":
                picks.append((len(picks), cid, region_id, region_id, cid))
    if pinnacle_id == 0:
        raise RuntimeError("Pinnacle was not created")
    insert_rows(
        con,
        "customers",
        [
            "customer_id",
            "customer_code",
            "customer_name",
            "segment_id",
            "region_id",
            "tax_code",
            "city",
            "opened_on",
            "credit_limit",
            "active",
        ],
        customers,
    )
    insert_rows(
        con,
        "customer_sites",
        ["site_id", "customer_id", "city", "is_default"],
        sites,
    )
    insert_rows(
        con,
        "customer_contacts",
        ["contact_id", "customer_id", "full_name", "role_name", "email"],
        contacts,
    )

    n_suppliers = scale["suppliers"]
    suppliers = []
    supplier_contacts = []
    for sid in range(1, n_suppliers + 1):
        if sid == 1:
            suppliers.append((1, "SUP-KELSO", "Kelso Components", 2, "Kansas City", 14, False))
            contact_name = "Morgan Keller"
        elif sid == 2:
            suppliers.append((2, "SUP-ARDEN", "Arden Fasteners", 1, "Minneapolis", 7, True))
            contact_name = "Riley Holt"
        else:
            home = ((sid - 1) % 6) + 1
            suppliers.append(
                (
                    sid,
                    f"SUP-{sid:04d}",
                    f"{ADJ[sid % len(ADJ)]} Components {sid:02d}",
                    home,
                    REGIONS[home - 1][3],
                    7,
                    sid % 5 != 0,
                )
            )
            contact_name = person_name(sid + 80)
        supplier_contacts.append(
            (sid, sid, contact_name, "Account manager", f"s{sid}@example.test")
        )
    insert_rows(
        con,
        "suppliers",
        [
            "supplier_id",
            "supplier_code",
            "supplier_name",
            "home_warehouse_id",
            "city",
            "lead_time_days",
            "preferred",
        ],
        suppliers,
    )
    insert_rows(
        con,
        "supplier_contacts",
        ["contact_id", "supplier_id", "full_name", "role_name", "email"],
        supplier_contacts,
    )

    products = []
    skus = []
    sku_suppliers = []
    n_products = scale["products"]
    for index in range(n_products):
        product_id = index + 1
        if index == 0:
            product_name = "Harbor Hose"
            category_id = 1
            brand_id = 1
            ea_code = "HX-440"
            ea_name = "Harbor Hose 25mm"
        else:
            noun = ITEM_NOUNS[index % len(ITEM_NOUNS)]
            product_name = f"{noun} {index:03d}"
            category_id = 1 + (index % 8)
            brand_id = 1 + (index % 6)
            ea_code = f"SKU-{index:04d}"
            ea_name = product_name
        products.append((product_id, brand_id, category_id, product_name))
        ea_cents = 450 + (index * 137) % 8000
        case_cents = (ea_cents * 12 * 9) // 10
        weight = (Decimal("0.40") + Decimal(index % 20) / Decimal(10)).quantize(Decimal("0.01"))
        ea_id = index * 2 + 1
        case_id = ea_id + 1
        skus.append(
            (
                ea_id,
                product_id,
                ea_code,
                ea_name,
                "EA",
                cents(ea_cents),
                cents(ea_cents * 6 // 10),
                weight,
            )
        )
        skus.append(
            (
                case_id,
                product_id,
                f"{ea_code}-CS",
                f"{ea_name} case",
                "CS",
                cents(case_cents),
                cents(case_cents * 6 // 10),
                (weight * 12).quantize(Decimal("0.01")),
            )
        )
        supplier_id = 2 if index == 0 else 1 + (index % n_suppliers)
        sku_suppliers.append((ea_id, supplier_id, True))
        sku_suppliers.append((case_id, supplier_id, True))
    insert_rows(
        con,
        "products",
        ["product_id", "brand_id", "category_id", "product_name"],
        products,
    )
    insert_rows(
        con,
        "skus",
        [
            "sku_id",
            "product_id",
            "sku_code",
            "sku_name",
            "uom_code",
            "list_price",
            "standard_cost",
            "weight_kg",
        ],
        skus,
    )
    insert_rows(
        con,
        "sku_suppliers",
        ["sku_id", "supplier_id", "is_primary"],
        sku_suppliers,
    )

    lanes = []
    lane_id = 1
    typical = {row[0]: row[3] for row in carriers}
    for carrier_id, _name, _mode, _days in carriers:
        for warehouse_id, region_id, *_rest in [(row[0], row[1]) for row in warehouses]:
            lanes.append(
                (
                    lane_id,
                    carrier_id,
                    warehouse_id,
                    region_id,
                    typical[carrier_id] + (region_id % 2),
                )
            )
            lane_id += 1
    insert_rows(
        con,
        "carrier_lanes",
        ["lane_id", "carrier_id", "warehouse_id", "region_id", "transit_days"],
        lanes,
    )
    insert_rows(
        con,
        "price_lists",
        ["price_list_id", "price_list_name", "currency_code"],
        [(1, "Standard", "USD")],
    )
    insert_rows(
        con,
        "price_list_items",
        ["price_list_id", "sku_id", "unit_price", "effective_from"],
        [(1, row[0], row[5], START) for row in skus],
    )
    insert_rows(
        con,
        "promotions",
        ["promotion_id", "promotion_name", "start_date", "end_date", "discount_pct"],
        [(1, "Spring refill", date(2024, 3, 1), date(2024, 4, 30), Decimal("8.00"))],
    )
    promo_skus = [1]
    if len(skus) >= 3:
        promo_skus.append(3)
    insert_rows(
        con,
        "promotion_skus",
        ["promotion_id", "sku_id"],
        [(1, sku_id) for sku_id in promo_skus],
    )
    insert_rows(
        con,
        "cost_centers",
        ["cost_center_id", "region_id", "cost_center_name"],
        [(region_id, region_id, f"{name} sales") for region_id, _code, name, _city in REGIONS],
    )
    targets = []
    for region_id, *_rest in REGIONS:
        for year in (2024, 2025):
            for month in range(1, 13):
                amount = cents(scale["target_base"] * MONTH_PCT[month])
                targets.append((region_id, year, month, amount))
    insert_rows(
        con,
        "sales_targets",
        ["region_id", "target_year", "target_month", "target_usd"],
        targets,
    )

    calendar_rows = []
    build_rows = []
    max_orders = 1
    max_extra = 1
    day = START
    day_index = 0
    while day <= AS_OF:
        count = order_count_for(day, scale["order_base"], seed)
        extra = max(1, (count * 12) // 100) if day <= CUTOFF else 0
        max_orders = max(max_orders, count)
        max_extra = max(max_extra, extra)
        weekend = day.weekday() >= 5
        calendar_rows.append(
            (
                day,
                day.year,
                (day.month - 1) // 3 + 1,
                day.month,
                MONTH_NAMES[day.month],
                day.weekday(),
                DOW_NAMES[day.weekday()],
                weekend,
            )
        )
        build_rows.append((day, day_index, day_index // 7, count, extra))
        day += timedelta(days=1)
        day_index += 1
    insert_rows(
        con,
        "calendar",
        [
            "calendar_date",
            "calendar_year",
            "quarter",
            "calendar_month",
            "month_name",
            "dow",
            "dow_name",
            "is_weekend",
        ],
        calendar_rows,
    )
    con.execute(
        """
        CREATE SCHEMA harborline_build;
        CREATE TABLE harborline_build.calendar (
          calendar_date DATE PRIMARY KEY,
          day_index INTEGER NOT NULL,
          week_index INTEGER NOT NULL,
          order_count INTEGER NOT NULL,
          pinnacle_extra INTEGER NOT NULL
        );
        CREATE TABLE harborline_build.pick (
          slot INTEGER PRIMARY KEY,
          customer_id INTEGER NOT NULL,
          region_id INTEGER NOT NULL,
          warehouse_id INTEGER NOT NULL,
          site_id INTEGER NOT NULL
        );
        CREATE TABLE harborline_build.rep (
          region_id INTEGER NOT NULL,
          slot INTEGER NOT NULL,
          employee_id INTEGER NOT NULL,
          PRIMARY KEY (region_id, slot)
        );
        """
    )
    con.executemany(
        "INSERT INTO harborline_build.calendar VALUES (?, ?, ?, ?, ?)",
        build_rows,
    )
    con.executemany(
        "INSERT INTO harborline_build.pick VALUES (?, ?, ?, ?, ?)",
        picks,
    )
    rep_rows = []
    for region_id, employee_ids in reps.items():
        if len(employee_ids) != scale["reps_per_region"]:
            raise RuntimeError("rep count drifted")
        for slot, employee_id in enumerate(employee_ids):
            rep_rows.append((region_id, slot, employee_id))
    con.executemany(
        "INSERT INTO harborline_build.rep VALUES (?, ?, ?)",
        rep_rows,
    )
    return Ctx(
        pinnacle_id=pinnacle_id,
        pinnacle_site=pinnacle_id,
        kelso_id=1,
        arden_id=2,
        northland_id=1,
        dc_north_id=1,
        n_skus=len(skus),
        n_channels=len(channels),
        n_terms=len(terms),
        n_reasons=len(reasons),
        n_carriers=len(carriers),
        pick_n=len(picks),
        reps_per_region=scale["reps_per_region"],
        max_orders=max_orders,
        max_extra=max_extra,
        track_skus=scale["track_skus"],
        min_lines=scale["min_lines"],
        line_span=scale["line_span"],
    )


def load_facts(con, ctx: Ctx, seed: int) -> None:
    con.execute(
        f"""
        CREATE MACRO hl_mix(i, salt) AS (
          (
            (
              CAST(i AS HUGEINT) * 1103515245
              + CAST(salt AS HUGEINT) * 12345
              + {int(seed)}
            ) % 2147483647
          )
        )
        """
    )
    line_cap = ctx.min_lines + ctx.line_span - 1
    con.execute(
        f"""
        CREATE TABLE harborline_build.orders AS
        SELECT
          CAST(row_number() OVER (ORDER BY order_date, kind, n) AS INTEGER) AS order_id,
          order_date,
          kind,
          n
        FROM (
          SELECT c.calendar_date AS order_date, 'base' AS kind, CAST(r.n AS INTEGER) AS n
          FROM harborline_build.calendar c
          CROSS JOIN range(0, {ctx.max_orders}) AS r(n)
          WHERE r.n < c.order_count
          UNION ALL
          SELECT c.calendar_date, 'pinnacle', CAST(r.n AS INTEGER)
          FROM harborline_build.calendar c
          CROSS JOIN range(0, {ctx.max_extra}) AS r(n)
          WHERE c.pinnacle_extra > 0 AND r.n < c.pinnacle_extra
        ) seed
        """
    )
    con.execute(
        f"""
        INSERT INTO {SCHEMA}.orders (
          order_id, customer_id, site_id, warehouse_id, channel_id, terms_id,
          employee_id, order_date, status
        )
        SELECT
          b.order_id,
          CASE WHEN b.kind = 'pinnacle' THEN {ctx.pinnacle_id} ELSE p.customer_id END,
          CASE WHEN b.kind = 'pinnacle' THEN {ctx.pinnacle_site} ELSE p.site_id END,
          CASE WHEN b.kind = 'pinnacle' THEN {ctx.dc_north_id} ELSE p.warehouse_id END,
          1 + CAST(hl_mix(b.order_id, 3) % {ctx.n_channels} AS INTEGER),
          1 + CAST(hl_mix(b.order_id, 4) % {ctx.n_terms} AS INTEGER),
          r.employee_id,
          b.order_date,
          'filled'
        FROM harborline_build.orders b
        JOIN harborline_build.pick p
          ON p.slot = CAST(hl_mix(b.order_id, 1) % {ctx.pick_n} AS INTEGER)
        JOIN harborline_build.rep r
          ON r.region_id = CASE
               WHEN b.kind = 'pinnacle' THEN {ctx.northland_id} ELSE p.region_id
             END
         AND r.slot = CAST(hl_mix(b.order_id, 2) % {ctx.reps_per_region} AS INTEGER)
        """
    )
    con.execute(
        f"""
        INSERT INTO {SCHEMA}.order_lines (
          order_line_id, order_id, sku_id, qty, fulfilled_qty,
          unit_price, discount_pct, line_amount
        )
        SELECT
          line_id,
          order_id,
          sku_id,
          qty,
          qty,
          unit_price,
          CAST(0 AS DECIMAL(5,2)),
          CAST(ROUND(qty * CAST(unit_price AS DOUBLE PRECISION), 2) AS DECIMAL(14,2))
        FROM (
          SELECT
            CAST(row_number() OVER (ORDER BY b.order_id, r.n) AS INTEGER) AS line_id,
            b.order_id,
            s.sku_id,
            (1 + CAST(hl_mix(b.order_id * 20 + r.n, 6) % 8 AS INTEGER))
              * CASE WHEN b.kind = 'pinnacle' THEN 4 ELSE 1 END AS qty,
            CAST(ROUND(
              CAST(s.list_price AS DOUBLE PRECISION)
              * (90 + CAST(hl_mix(b.order_id * 20 + r.n, 7) % 21 AS INTEGER))
              / 100.0,
              2
            ) AS DECIMAL(14,2)) AS unit_price
          FROM harborline_build.orders b
          CROSS JOIN range(0, {line_cap}) AS r(n)
          JOIN {SCHEMA}.skus s
            ON s.sku_id = 1 + CAST(hl_mix(b.order_id * 20 + r.n, 8) % {ctx.n_skus} AS INTEGER)
          WHERE r.n < (
            {ctx.min_lines} + CAST(hl_mix(b.order_id, 5) % {ctx.line_span} AS INTEGER)
          )
        ) lines
        """
    )
    con.execute(
        f"""
        INSERT INTO {SCHEMA}.inventory_snapshots (
          snapshot_date, warehouse_id, sku_id, qty_on_hand, qty_on_order
        )
        SELECT
          c.calendar_date,
          w.warehouse_id,
          s.sku_id,
          15 + CAST(
            hl_mix(c.day_index * 100000 + w.warehouse_id * 1000 + s.sku_id, 12) % 70
            AS INTEGER
          ),
          0
        FROM harborline_build.calendar c
        CROSS JOIN {SCHEMA}.warehouses w
        CROSS JOIN {SCHEMA}.skus s
        WHERE s.sku_id <= {ctx.track_skus}
        """
    )
    con.execute(
        f"""
        UPDATE {SCHEMA}.inventory_snapshots
        SET qty_on_hand = 0, qty_on_order = 30
        WHERE sku_id = (SELECT sku_id FROM {SCHEMA}.skus WHERE sku_code = 'HX-440')
          AND warehouse_id = (
            SELECT warehouse_id FROM {SCHEMA}.warehouses WHERE warehouse_code = 'DC-NORTH'
          )
          AND snapshot_date BETWEEN DATE '2025-06-02' AND DATE '2025-08-17'
        """
    )
    con.execute(
        f"""
        UPDATE {SCHEMA}.order_lines
        SET fulfilled_qty = 0, line_amount = 0
        WHERE sku_id = (SELECT sku_id FROM {SCHEMA}.skus WHERE sku_code = 'HX-440')
          AND order_id IN (
            SELECT o.order_id
            FROM {SCHEMA}.orders o
            JOIN {SCHEMA}.warehouses w ON w.warehouse_id = o.warehouse_id
            WHERE w.warehouse_code = 'DC-NORTH'
              AND o.order_date BETWEEN DATE '2025-06-02' AND DATE '2025-08-17'
          )
        """
    )
    con.execute(
        f"""
        UPDATE {SCHEMA}.orders
        SET status = 'short'
        WHERE order_id IN (
          SELECT order_id FROM {SCHEMA}.order_lines WHERE fulfilled_qty < qty
        )
        """
    )
    con.execute(
        f"""
        INSERT INTO {SCHEMA}.inventory_movements (
          movement_id, warehouse_id, sku_id, movement_date, qty, movement_type
        )
        SELECT
          CAST(row_number() OVER (
            ORDER BY warehouse_id, sku_id, snapshot_date
          ) AS INTEGER),
          warehouse_id,
          sku_id,
          snapshot_date,
          qty_on_hand - COALESCE(prev_qty, 0),
          CASE WHEN prev_qty IS NULL THEN 'opening' ELSE 'net' END
        FROM (
          SELECT
            warehouse_id,
            sku_id,
            snapshot_date,
            qty_on_hand,
            LAG(qty_on_hand) OVER (
              PARTITION BY warehouse_id, sku_id ORDER BY snapshot_date
            ) AS prev_qty
          FROM {SCHEMA}.inventory_snapshots
        ) snapped
        """
    )
    con.execute(
        f"""
        INSERT INTO {SCHEMA}.shipments (
          shipment_id, order_id, warehouse_id, carrier_id, ship_date, delivered_date, status
        )
        SELECT
          o.order_id,
          o.order_id,
          o.warehouse_id,
          lane.carrier_id,
          o.order_date,
          o.order_date + lane.transit_days,
          CASE
            WHEN o.order_date + lane.transit_days <= DATE '2025-12-31' THEN 'delivered'
            ELSE 'in_transit'
          END
        FROM {SCHEMA}.orders o
        JOIN {SCHEMA}.customers c ON c.customer_id = o.customer_id
        JOIN {SCHEMA}.carrier_lanes lane
          ON lane.warehouse_id = o.warehouse_id
         AND lane.region_id = c.region_id
         AND lane.carrier_id = 1 + CAST(hl_mix(o.order_id, 7) % {ctx.n_carriers} AS INTEGER)
        WHERE EXISTS (
          SELECT 1 FROM {SCHEMA}.order_lines ol
          WHERE ol.order_id = o.order_id AND ol.fulfilled_qty > 0
        )
        """
    )
    con.execute(
        f"""
        INSERT INTO {SCHEMA}.shipment_lines (
          shipment_line_id, shipment_id, order_line_id, sku_id, qty
        )
        SELECT ol.order_line_id, ol.order_id, ol.order_line_id, ol.sku_id, ol.fulfilled_qty
        FROM {SCHEMA}.order_lines ol
        WHERE ol.fulfilled_qty > 0
        """
    )
    con.execute(
        f"""
        INSERT INTO {SCHEMA}.invoices (
          invoice_id, order_id, customer_id, terms_id, invoice_date, due_date, total_amount, status
        )
        SELECT
          o.order_id,
          o.order_id,
          o.customer_id,
          o.terms_id,
          o.order_date,
          o.order_date + t.net_days,
          CAST(ROUND(COALESCE(SUM(ol.line_amount), 0), 2) AS DECIMAL(14,2)),
          'open'
        FROM {SCHEMA}.orders o
        JOIN {SCHEMA}.payment_terms t ON t.terms_id = o.terms_id
        LEFT JOIN {SCHEMA}.order_lines ol ON ol.order_id = o.order_id
        GROUP BY o.order_id, o.customer_id, o.terms_id, o.order_date, t.net_days
        """
    )
    con.execute(
        f"""
        INSERT INTO {SCHEMA}.invoice_lines (
          invoice_line_id, invoice_id, order_line_id, sku_id, qty, unit_price, line_amount
        )
        SELECT
          ol.order_line_id, ol.order_id, ol.order_line_id, ol.sku_id,
          ol.fulfilled_qty, ol.unit_price, ol.line_amount
        FROM {SCHEMA}.order_lines ol
        """
    )
    con.execute(
        f"""
        INSERT INTO {SCHEMA}.payments (
          payment_id, customer_id, payment_date, amount, method
        )
        SELECT
          i.invoice_id,
          i.customer_id,
          i.invoice_date + (
            t.net_days - 4 + CAST(hl_mix(i.invoice_id, 4) % 10 AS INTEGER)
          ),
          i.total_amount,
          CASE CAST(hl_mix(i.invoice_id, 5) % 3 AS INTEGER)
            WHEN 0 THEN 'ach'
            WHEN 1 THEN 'check'
            ELSE 'card'
          END
        FROM {SCHEMA}.invoices i
        JOIN {SCHEMA}.payment_terms t ON t.terms_id = i.terms_id
        WHERE i.total_amount > 0
          AND i.invoice_date + (
            t.net_days - 4 + CAST(hl_mix(i.invoice_id, 4) % 10 AS INTEGER)
          ) <= DATE '2025-12-31'
        """
    )
    con.execute(
        f"""
        INSERT INTO {SCHEMA}.payment_applications (
          application_id, payment_id, invoice_id, amount
        )
        SELECT payment_id, payment_id, payment_id, amount
        FROM {SCHEMA}.payments
        """
    )
    con.execute(
        f"""
        UPDATE {SCHEMA}.invoices
        SET status = 'paid'
        WHERE invoice_id IN (SELECT invoice_id FROM {SCHEMA}.payment_applications)
        """
    )
    con.execute(
        f"""
        INSERT INTO {SCHEMA}.purchase_orders (
          po_id, supplier_id, warehouse_id, order_date, expected_date, status, week_index
        )
        SELECT
          CAST(row_number() OVER (ORDER BY supplier_id, week_index) AS INTEGER),
          supplier_id,
          home_warehouse_id,
          week_start,
          week_start + lead_time_days,
          'received',
          week_index
        FROM (
          SELECT
            s.supplier_id,
            s.home_warehouse_id,
            s.lead_time_days,
            c.week_index,
            MIN(c.calendar_date) AS week_start
          FROM {SCHEMA}.suppliers s
          JOIN harborline_build.calendar c ON TRUE
          GROUP BY s.supplier_id, s.home_warehouse_id, s.lead_time_days, c.week_index
        ) weeks
        """
    )
    con.execute(
        f"""
        INSERT INTO {SCHEMA}.purchase_order_lines (
          po_line_id, po_id, sku_id, qty, unit_cost
        )
        SELECT
          CAST(row_number() OVER (ORDER BY po.po_id, ss.sku_id) AS INTEGER),
          po.po_id,
          ss.sku_id,
          12 + CAST(hl_mix(po.po_id * 10 + ss.sku_id, 13) % 20 AS INTEGER),
          sk.standard_cost
        FROM {SCHEMA}.purchase_orders po
        JOIN (
          SELECT
            supplier_id,
            sku_id,
            row_number() OVER (PARTITION BY supplier_id ORDER BY sku_id) AS rn
          FROM {SCHEMA}.sku_suppliers
        ) ss ON ss.supplier_id = po.supplier_id AND ss.rn <= 3
        JOIN {SCHEMA}.skus sk ON sk.sku_id = ss.sku_id
        """
    )
    con.execute(
        f"""
        INSERT INTO {SCHEMA}.inbound_receipts (
          receipt_id, po_id, supplier_id, warehouse_id, promised_date, received_date, late_days
        )
        SELECT
          receipt_id,
          po_id,
          supplier_id,
          warehouse_id,
          promised_date,
          promised_date + late_days,
          late_days
        FROM (
          SELECT
            CAST(row_number() OVER (
              ORDER BY s.supplier_id, c.calendar_date
            ) AS INTEGER) AS receipt_id,
            po.po_id,
            s.supplier_id,
            s.home_warehouse_id AS warehouse_id,
            c.calendar_date AS promised_date,
            CASE
              WHEN CAST(hl_mix(s.supplier_id * 1000 + c.day_index, 11) // 21474836 AS INTEGER)
                   < CASE
                       WHEN s.supplier_id = {ctx.kelso_id}
                       THEN 6 + (40 * c.day_index) // 730
                       ELSE 8
                     END
              THEN 1 + CAST(hl_mix(s.supplier_id * 1000 + c.day_index, 14) % 8 AS INTEGER)
              ELSE 0
            END AS late_days
          FROM {SCHEMA}.suppliers s
          JOIN harborline_build.calendar c ON TRUE
          JOIN {SCHEMA}.purchase_orders po
            ON po.supplier_id = s.supplier_id
           AND po.week_index = c.week_index
        ) receipts
        """
    )
    con.execute(
        f"""
        INSERT INTO {SCHEMA}.inbound_receipt_lines (
          receipt_line_id, receipt_id, sku_id, qty
        )
        SELECT
          CAST(row_number() OVER (ORDER BY r.receipt_id, ss.sku_id) AS INTEGER),
          r.receipt_id,
          ss.sku_id,
          8 + CAST(hl_mix(r.receipt_id * 10 + ss.sku_id, 15) % 16 AS INTEGER)
        FROM {SCHEMA}.inbound_receipts r
        JOIN (
          SELECT
            supplier_id,
            sku_id,
            row_number() OVER (PARTITION BY supplier_id ORDER BY sku_id) AS rn
          FROM {SCHEMA}.sku_suppliers
        ) ss ON ss.supplier_id = r.supplier_id AND ss.rn <= 3
        WHERE NOT (
          ss.sku_id = (SELECT sku_id FROM {SCHEMA}.skus WHERE sku_code = 'HX-440')
          AND r.warehouse_id = (
            SELECT warehouse_id FROM {SCHEMA}.warehouses WHERE warehouse_code = 'DC-NORTH'
          )
          AND r.promised_date BETWEEN DATE '2025-06-02' AND DATE '2025-08-17'
        )
        """
    )
    con.execute(
        f"""
        INSERT INTO {SCHEMA}.returns (
          return_id, order_id, customer_id, reason_id, return_date, status
        )
        SELECT
          CAST(row_number() OVER (ORDER BY o.order_id) AS INTEGER),
          o.order_id,
          o.customer_id,
          1 + CAST(hl_mix(o.order_id, 9) % {ctx.n_reasons} AS INTEGER),
          o.order_date + 5,
          'received'
        FROM {SCHEMA}.orders o
        WHERE CAST(hl_mix(o.order_id, 16) % 100 AS INTEGER) < 4
          AND o.order_date <= DATE '2025-12-20'
          AND EXISTS (
            SELECT 1 FROM {SCHEMA}.order_lines ol
            WHERE ol.order_id = o.order_id AND ol.fulfilled_qty > 0
          )
        """
    )
    con.execute(
        f"""
        INSERT INTO {SCHEMA}.return_lines (
          return_line_id, return_id, sku_id, qty, amount
        )
        SELECT
          return_id,
          return_id,
          sku_id,
          qty,
          CAST(ROUND(qty * CAST(unit_price AS DOUBLE PRECISION), 2) AS DECIMAL(14,2))
        FROM (
          SELECT
            r.return_id,
            ol.sku_id,
            ol.unit_price,
            LEAST(
              ol.fulfilled_qty,
              1 + CAST(hl_mix(r.return_id, 17) % 3 AS INTEGER)
            ) AS qty
          FROM {SCHEMA}.returns r
          JOIN (
            SELECT
              order_id,
              order_line_id,
              sku_id,
              unit_price,
              fulfilled_qty,
              row_number() OVER (PARTITION BY order_id ORDER BY order_line_id) AS rn
            FROM {SCHEMA}.order_lines
            WHERE fulfilled_qty > 0
          ) ol ON ol.order_id = r.order_id AND ol.rn = 1
        ) lines
        """
    )
    con.execute("DROP SCHEMA harborline_build CASCADE")
    con.execute("DROP MACRO hl_mix")


def apply_comments(con) -> None:
    for table in TABLES:
        con.execute(f"COMMENT ON TABLE {SCHEMA}.{table.name} IS {quote(table.description)}")
        for item in table.columns:
            con.execute(
                f"COMMENT ON COLUMN {SCHEMA}.{table.name}.{item.name} IS {quote(item.description)}"
            )


def total_rows(con) -> int:
    parts = [f"(SELECT COUNT(*) FROM {SCHEMA}.{table.name})" for table in TABLES]
    return int(con.execute("SELECT " + " + ".join(parts)).fetchone()[0])


def json_cell(value):
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return int(value)
        return format(value.quantize(Decimal("0.01")), "f")
    if isinstance(value, float):
        return f"{value:.2f}"
    if isinstance(value, int):
        return value
    return value


def run_query(con, statement: str) -> list[dict]:
    cursor = con.execute(statement)
    names = [item[0] for item in cursor.description]
    return [
        {name: json_cell(value) for name, value in zip(names, row, strict=True)}
        for row in cursor.fetchall()
    ]


def money(value) -> str:
    return f"{float(value):,.2f}"


def plain(value) -> str:
    return f"{float(value):.2f}"


def answer_for(question_id: str, rows: list[dict]) -> str:
    if question_id == "q01":
        lines = [
            f"{index}. {row['customer_name']} ({row['customer_code']}): "
            f"{money(row['revenue_usd'])} USD"
            for index, row in enumerate(rows, start=1)
        ]
        return "Top customers by 2025 recognized revenue:\n" + "\n".join(lines)
    if question_id == "q02":
        lines = [
            f"{index}. {row['sku_name']} ({row['sku_code']}): {money(row['revenue_usd'])} USD"
            for index, row in enumerate(rows, start=1)
        ]
        return "Top SKUs by 2025 recognized revenue:\n" + "\n".join(lines)
    if question_id == "q03":
        lines = [f"{row['region_name']}: {money(row['revenue_usd'])} USD" for row in rows]
        return "2025 recognized revenue by region:\n" + "\n".join(lines)
    if question_id == "q04":
        lines = [
            f"{index}. {row['supplier_name']} ({row['supplier_code']}): "
            f"{plain(row['late_pct'])} percent late "
            f"({row['late_receipts']} of {row['receipts']} receipts)"
            for index, row in enumerate(rows, start=1)
        ]
        return "Highest late-receipt rates in 2025:\n" + "\n".join(lines)
    if question_id == "q05":
        lines = [f"{row['month_start']}: {money(row['revenue_usd'])} USD" for row in rows]
        peak = max(rows, key=lambda row: float(row["revenue_usd"]))
        trough = min(rows, key=lambda row: float(row["revenue_usd"]))
        return (
            "Monthly recognized revenue:\n"
            + "\n".join(lines)
            + f"\nHighest month: {peak['month_start']} at {money(peak['revenue_usd'])} USD."
            + f"\nLowest month: {trough['month_start']} at {money(trough['revenue_usd'])} USD."
        )
    if question_id == "q06":
        lines = [
            f"{row['region_name']}: H2 2024 {money(row['h2_2024_usd'])} USD, "
            f"H2 2025 {money(row['h2_2025_usd'])} USD, "
            f"change {plain(row['change_pct'])} percent"
            for row in rows
        ]
        worst = min(rows, key=lambda row: float(row["change_pct"]))
        return (
            "Second-half recognized revenue by region:\n"
            + "\n".join(lines)
            + f"\nLargest decline: {worst['region_name']} at {plain(worst['change_pct'])} percent."
        )
    if question_id == "q07":
        lines = [
            f"{index}. {row['customer_name']} ({row['customer_code']}): "
            f"Sep-Dec 2024 {money(row['sep_dec_2024_usd'])} USD, "
            f"Sep-Dec 2025 {money(row['sep_dec_2025_usd'])} USD, "
            f"decline {money(row['decline_usd'])} USD"
            for index, row in enumerate(rows, start=1)
        ]
        return "Largest Northland declines, Sep-Dec versus the prior year:\n" + "\n".join(lines)
    if question_id == "q08":
        lines = [
            f"{index}. {row['supplier_name']} ({row['supplier_code']}): "
            f"2024 H1 {plain(row['late_pct_h1_2024'])} percent, "
            f"2025 H2 {plain(row['late_pct_h2_2025'])} percent, "
            f"change {plain(row['late_pct_point_change'])} points"
            for index, row in enumerate(rows, start=1)
        ]
        return "Suppliers whose late-receipt rate rose the most:\n" + "\n".join(lines)
    if question_id == "q09":
        lines = [
            f"{row['sku_name']} ({row['sku_code']}) at {row['warehouse_code']}: "
            f"{row['zero_days']} stockout days, {row['first_day']} through {row['last_day']}, "
            f"unfilled demand {money(row['missed_usd'])} USD"
            for row in rows
        ]
        return "Longest stockouts:\n" + "\n".join(lines)
    if question_id == "q10":
        row = rows[0]
        return (
            f"January 2026 forecast: {money(row['forecast_usd'])} USD. "
            f"January 2025 was {money(row['january_2025_usd'])} USD. "
            f"July-December 2025 was {money(row['jul_dec_2025_usd'])} USD. "
            f"July-December 2024 was {money(row['jul_dec_2024_usd'])} USD."
        )
    if question_id == "q11":
        lines = [
            f"- {row['customer_name']}: {money(row['revenue_usd'])} USD, "
            f"{plain(row['share_pct'])} percent of 2025 revenue"
            for row in rows
        ]
        return (
            "Slide title: Five largest customers in 2025\n"
            "Subtitle: Recognized revenue, USD\n" + "\n".join(lines)
        )
    if question_id == "q12":
        lines = [
            f"{index}. {row['carrier_name']}: {row['shipments']} shipments, "
            f"average transit {plain(row['avg_transit_days'])} days"
            for index, row in enumerate(rows, start=1)
        ]
        return "Carriers by 2025 delivered shipments:\n" + "\n".join(lines)
    raise RuntimeError(f"no summary for {question_id}")


def questions() -> list[dict[str, str]]:
    return [
        {
            "id": "q01",
            "minute": "0:40",
            "buyer": "Who were our ten largest customers by revenue in 2025?",
            "direction": "Ask it in those words. Read the top three names and figures.",
            "sql": sql_text(
                f"""
                SELECT c.customer_code, c.customer_name,
                  ROUND(CAST(SUM(ol.line_amount) AS NUMERIC), 2) AS revenue_usd
                FROM {SCHEMA}.order_lines ol
                JOIN {SCHEMA}.orders o ON o.order_id = ol.order_id
                JOIN {SCHEMA}.customers c ON c.customer_id = o.customer_id
                WHERE o.order_date >= DATE '2025-01-01'
                  AND o.order_date < DATE '2026-01-01'
                GROUP BY c.customer_code, c.customer_name
                ORDER BY revenue_usd DESC, c.customer_code
                LIMIT 10
                """
            ),
        },
        {
            "id": "q02",
            "minute": "1:20",
            "buyer": "Which ten SKUs brought in the most revenue in 2025?",
            "direction": "Same definition of revenue. Read the top SKU code and the figure.",
            "sql": sql_text(
                f"""
                SELECT s.sku_code, s.sku_name,
                  ROUND(CAST(SUM(ol.line_amount) AS NUMERIC), 2) AS revenue_usd
                FROM {SCHEMA}.order_lines ol
                JOIN {SCHEMA}.orders o ON o.order_id = ol.order_id
                JOIN {SCHEMA}.skus s ON s.sku_id = ol.sku_id
                WHERE o.order_date >= DATE '2025-01-01'
                  AND o.order_date < DATE '2026-01-01'
                GROUP BY s.sku_code, s.sku_name
                ORDER BY revenue_usd DESC, s.sku_code
                LIMIT 10
                """
            ),
        },
        {
            "id": "q03",
            "minute": "2:00",
            "buyer": "How did 2025 revenue break down by region?",
            "direction": "This is the join across orders, lines, customers, and regions.",
            "sql": sql_text(
                f"""
                SELECT g.region_name,
                  ROUND(CAST(SUM(ol.line_amount) AS NUMERIC), 2) AS revenue_usd
                FROM {SCHEMA}.order_lines ol
                JOIN {SCHEMA}.orders o ON o.order_id = ol.order_id
                JOIN {SCHEMA}.customers c ON c.customer_id = o.customer_id
                JOIN {SCHEMA}.regions g ON g.region_id = c.region_id
                WHERE o.order_date >= DATE '2025-01-01'
                  AND o.order_date < DATE '2026-01-01'
                GROUP BY g.region_name
                ORDER BY g.region_name
                """
            ),
        },
        {
            "id": "q04",
            "minute": "2:40",
            "buyer": "Which eight suppliers had the highest late-delivery rate in 2025?",
            "direction": "Late means the receipt arrived after the promised date.",
            "sql": sql_text(
                f"""
                SELECT s.supplier_code, s.supplier_name,
                  COUNT(*) AS receipts,
                  SUM(CASE WHEN r.late_days > 0 THEN 1 ELSE 0 END) AS late_receipts,
                  ROUND(
                    100.0 * SUM(CASE WHEN r.late_days > 0 THEN 1 ELSE 0 END) / COUNT(*),
                    2
                  ) AS late_pct
                FROM {SCHEMA}.inbound_receipts r
                JOIN {SCHEMA}.suppliers s ON s.supplier_id = r.supplier_id
                WHERE r.promised_date >= DATE '2025-01-01'
                  AND r.promised_date < DATE '2026-01-01'
                GROUP BY s.supplier_code, s.supplier_name
                ORDER BY late_pct DESC, s.supplier_code
                LIMIT 8
                """
            ),
        },
        {
            "id": "q05",
            "minute": "3:20",
            "buyer": "Show me monthly revenue for the last two years.",
            "direction": "Point at the winter dip and the late-year rise. Do not smooth it.",
            "sql": sql_text(
                f"""
                SELECT CAST(date_trunc('month', o.order_date) AS DATE) AS month_start,
                  ROUND(CAST(SUM(ol.line_amount) AS NUMERIC), 2) AS revenue_usd
                FROM {SCHEMA}.order_lines ol
                JOIN {SCHEMA}.orders o ON o.order_id = ol.order_id
                GROUP BY 1
                ORDER BY 1
                """
            ),
        },
        {
            "id": "q06",
            "minute": "4:00",
            "buyer": "Did any region fall off in the second half of 2025?",
            "direction": "Compare July through December with the same months in 2024.",
            "sql": sql_text(
                f"""
                SELECT region_name, h2_2024_usd, h2_2025_usd,
                  ROUND(100.0 * (h2_2025_usd - h2_2024_usd) / h2_2024_usd, 2) AS change_pct
                FROM (
                  SELECT g.region_name,
                    ROUND(CAST(SUM(CASE
                      WHEN o.order_date BETWEEN DATE '2024-07-01' AND DATE '2024-12-31'
                      THEN ol.line_amount ELSE 0 END) AS NUMERIC), 2) AS h2_2024_usd,
                    ROUND(CAST(SUM(CASE
                      WHEN o.order_date BETWEEN DATE '2025-07-01' AND DATE '2025-12-31'
                      THEN ol.line_amount ELSE 0 END) AS NUMERIC), 2) AS h2_2025_usd
                  FROM {SCHEMA}.order_lines ol
                  JOIN {SCHEMA}.orders o ON o.order_id = ol.order_id
                  JOIN {SCHEMA}.customers c ON c.customer_id = o.customer_id
                  JOIN {SCHEMA}.regions g ON g.region_id = c.region_id
                  GROUP BY g.region_name
                ) halves
                ORDER BY change_pct, region_name
                """
            ),
        },
        {
            "id": "q07",
            "minute": "4:40",
            "buyer": "Why did Northland revenue drop after August 2025?",
            "direction": "Same four months versus last year, by customer. Read the top decline.",
            "sql": sql_text(
                f"""
                SELECT customer_code, customer_name,
                  ROUND(CAST(sep_dec_2024 AS NUMERIC), 2) AS sep_dec_2024_usd,
                  ROUND(CAST(sep_dec_2025 AS NUMERIC), 2) AS sep_dec_2025_usd,
                  ROUND(CAST(sep_dec_2024 - sep_dec_2025 AS NUMERIC), 2) AS decline_usd
                FROM (
                  SELECT c.customer_code, c.customer_name,
                    SUM(CASE
                      WHEN o.order_date BETWEEN DATE '2024-09-01' AND DATE '2024-12-31'
                      THEN ol.line_amount ELSE 0 END) AS sep_dec_2024,
                    SUM(CASE
                      WHEN o.order_date BETWEEN DATE '2025-09-01' AND DATE '2025-12-31'
                      THEN ol.line_amount ELSE 0 END) AS sep_dec_2025
                  FROM {SCHEMA}.order_lines ol
                  JOIN {SCHEMA}.orders o ON o.order_id = ol.order_id
                  JOIN {SCHEMA}.customers c ON c.customer_id = o.customer_id
                  JOIN {SCHEMA}.regions g ON g.region_id = c.region_id
                  WHERE g.region_code = 'NL'
                  GROUP BY c.customer_code, c.customer_name
                ) windows
                ORDER BY decline_usd DESC, customer_code
                LIMIT 5
                """
            ),
        },
        {
            "id": "q08",
            "minute": "5:20",
            "buyer": "Which supplier's deliveries have been getting later?",
            "direction": "Compare the first half of 2024 with the second half of 2025.",
            "sql": sql_text(
                f"""
                SELECT supplier_code, supplier_name,
                  receipts_h1_2024, late_h1_2024,
                  ROUND(100.0 * late_h1_2024 / receipts_h1_2024, 2) AS late_pct_h1_2024,
                  receipts_h2_2025, late_h2_2025,
                  ROUND(100.0 * late_h2_2025 / receipts_h2_2025, 2) AS late_pct_h2_2025,
                  ROUND(
                    100.0 * late_h2_2025 / receipts_h2_2025
                    - 100.0 * late_h1_2024 / receipts_h1_2024,
                    2
                  ) AS late_pct_point_change
                FROM (
                  SELECT s.supplier_code, s.supplier_name,
                    SUM(CASE WHEN r.promised_date < DATE '2024-07-01' THEN 1 ELSE 0 END)
                      AS receipts_h1_2024,
                    SUM(CASE
                      WHEN r.promised_date < DATE '2024-07-01' AND r.late_days > 0 THEN 1
                      ELSE 0 END) AS late_h1_2024,
                    SUM(CASE WHEN r.promised_date >= DATE '2025-07-01' THEN 1 ELSE 0 END)
                      AS receipts_h2_2025,
                    SUM(CASE
                      WHEN r.promised_date >= DATE '2025-07-01' AND r.late_days > 0 THEN 1
                      ELSE 0 END) AS late_h2_2025
                  FROM {SCHEMA}.inbound_receipts r
                  JOIN {SCHEMA}.suppliers s ON s.supplier_id = r.supplier_id
                  GROUP BY s.supplier_code, s.supplier_name
                ) halves
                ORDER BY late_pct_point_change DESC, supplier_code
                LIMIT 5
                """
            ),
        },
        {
            "id": "q09",
            "minute": "6:00",
            "buyer": "Did we stock out of anything, and what did that cost us?",
            "direction": "On-hand zero is the stockout. Dollars are units we did not ship.",
            "sql": sql_text(
                f"""
                SELECT s.sku_code, s.sku_name, w.warehouse_code,
                  z.zero_days, z.first_day, z.last_day,
                  COALESCE(m.missed_usd, 0) AS missed_usd
                FROM (
                  SELECT sku_id, warehouse_id,
                    COUNT(*) AS zero_days,
                    MIN(snapshot_date) AS first_day,
                    MAX(snapshot_date) AS last_day
                  FROM {SCHEMA}.inventory_snapshots
                  WHERE qty_on_hand = 0
                  GROUP BY sku_id, warehouse_id
                ) z
                JOIN {SCHEMA}.skus s ON s.sku_id = z.sku_id
                JOIN {SCHEMA}.warehouses w ON w.warehouse_id = z.warehouse_id
                LEFT JOIN (
                  SELECT o.warehouse_id, ol.sku_id,
                    ROUND(CAST(SUM(
                      (ol.qty - ol.fulfilled_qty) * ol.unit_price
                    ) AS NUMERIC), 2) AS missed_usd
                  FROM {SCHEMA}.order_lines ol
                  JOIN {SCHEMA}.orders o ON o.order_id = ol.order_id
                  GROUP BY o.warehouse_id, ol.sku_id
                ) m ON m.sku_id = z.sku_id AND m.warehouse_id = z.warehouse_id
                ORDER BY z.zero_days DESC, s.sku_code
                LIMIT 5
                """
            ),
        },
        {
            "id": "q10",
            "minute": "6:40",
            "buyer": "What should we plan for revenue next month?",
            "direction": (
                "Say the method before the number. Next month is January 2026. "
                "The method is January 2025 times July-December 2025 over July-December 2024."
            ),
            "sql": sql_text(
                f"""
                SELECT
                  ROUND(CAST(january_2025 * jul_dec_2025 / jul_dec_2024 AS NUMERIC), 2)
                    AS forecast_usd,
                  ROUND(CAST(january_2025 AS NUMERIC), 2) AS january_2025_usd,
                  ROUND(CAST(jul_dec_2025 AS NUMERIC), 2) AS jul_dec_2025_usd,
                  ROUND(CAST(jul_dec_2024 AS NUMERIC), 2) AS jul_dec_2024_usd
                FROM (
                  SELECT
                    SUM(CASE WHEN month_start = DATE '2025-01-01' THEN revenue ELSE 0 END)
                      AS january_2025,
                    SUM(CASE
                      WHEN month_start BETWEEN DATE '2025-07-01' AND DATE '2025-12-01'
                      THEN revenue ELSE 0 END) AS jul_dec_2025,
                    SUM(CASE
                      WHEN month_start BETWEEN DATE '2024-07-01' AND DATE '2024-12-01'
                      THEN revenue ELSE 0 END) AS jul_dec_2024
                  FROM (
                    SELECT CAST(date_trunc('month', o.order_date) AS DATE) AS month_start,
                      SUM(ol.line_amount) AS revenue
                    FROM {SCHEMA}.order_lines ol
                    JOIN {SCHEMA}.orders o ON o.order_id = ol.order_id
                    GROUP BY 1
                  ) monthly
                ) parts
                """
            ),
        },
        {
            "id": "q11",
            "minute": "7:20",
            "buyer": "Create a slide of our five biggest customers this year.",
            "direction": "Read the title and the five bullets. This year means 2025.",
            "sql": sql_text(
                f"""
                SELECT customer_name, revenue_usd,
                  ROUND(100.0 * revenue_usd / SUM(revenue_usd) OVER (), 2) AS share_pct
                FROM (
                  SELECT c.customer_name,
                    ROUND(CAST(SUM(ol.line_amount) AS NUMERIC), 2) AS revenue_usd
                  FROM {SCHEMA}.order_lines ol
                  JOIN {SCHEMA}.orders o ON o.order_id = ol.order_id
                  JOIN {SCHEMA}.customers c ON c.customer_id = o.customer_id
                  WHERE o.order_date >= DATE '2025-01-01'
                    AND o.order_date < DATE '2026-01-01'
                  GROUP BY c.customer_name
                ) ranked
                ORDER BY revenue_usd DESC, customer_name
                LIMIT 5
                """
            ),
        },
        {
            "id": "q12",
            "minute": "8:00",
            "buyer": "Which carrier delivered the most 2025 shipments, and how long did they take?",
            "direction": "Count delivered shipments. Transit is delivered date minus ship date.",
            "sql": sql_text(
                f"""
                SELECT c.carrier_name,
                  COUNT(*) AS shipments,
                  ROUND(CAST(AVG(s.delivered_date - s.ship_date) AS NUMERIC), 2)
                    AS avg_transit_days
                FROM {SCHEMA}.shipments s
                JOIN {SCHEMA}.carriers c ON c.carrier_id = s.carrier_id
                WHERE s.status = 'delivered'
                  AND s.ship_date >= DATE '2025-01-01'
                  AND s.ship_date < DATE '2026-01-01'
                GROUP BY c.carrier_name
                ORDER BY shipments DESC, c.carrier_name
                """
            ),
        },
    ]


def render_walk(gold: dict) -> str:
    lines = [
        "# Harborline buyer walk",
        "",
        "Ten minutes. Twelve questions. Synthetic company, not a live customer.",
        "",
        "Harborline Distribution is a mid-size industrial wholesaler. Six regions,",
        "two years of orders, receipts, invoices, and a nightly stock count.",
        "Books close on 2025-12-31. Currency is USD.",
        "",
        "Recognized revenue is fulfilled quantity times unit price",
        "(`order_lines.line_amount`), dated by `orders.order_date`.",
        "A late delivery is an inbound receipt with `late_days` above zero.",
        "A stockout is a nightly snapshot with `qty_on_hand` at zero.",
        "",
        "The figure you may say is the expected answer under each question.",
        "That text is the SQL result. If the product abstains, show the SQL",
        "and that answer. Do not invent a closer number.",
        "",
        "Do not tell the buyer that receipts minus shipments equal on-hand.",
        "The nightly count is stored. It is not a perpetual ledger.",
        "",
        "## Before the meeting",
        "",
        "```",
        "python scripts/demo/seed_company.py --scale full --seed 20261128 \\",
        "  --duckdb data/demo/harborline.duckdb \\",
        '  --postgres "$DATABASE_URL"',
        "```",
        "",
        f"Seed {gold['seed']}. Scale {gold['scale']}. "
        f"Tables {gold['table_count']}. Rows {gold['row_count']}.",
        "Load the DuckDB file or the `harborline` schema in Postgres.",
        "Catalog text for schema retrieval is `docs/demo/harborline_catalog.json`.",
        "The ranker default of 480 tokens drops this catalog to an empty schema.",
        "A budget of 4000 tokens keeps the table descriptions.",
        "Gold rows are `tests/demo/gold.json`.",
        "",
        "## 0:00 Open",
        "",
        "Harborline, two years, USD. I will ask in plain language.",
        "Every number I repeat is the query result on this database.",
        "",
    ]
    for item in gold["questions"]:
        lines.extend(
            [
                f"## {item['minute']} {item['id']}",
                "",
                f"Buyer: {item['buyer']}",
                "",
                item["direction"],
                "",
                "```sql",
                item["sql"],
                "```",
                "",
                "Expected answer:",
                "",
                "```",
                item["answer"],
                "```",
                "",
            ]
        )
    lines.extend(
        [
            "## 8:40 Close",
            "",
            "Three things an analyst can check on this database:",
            "- Kelso Components late receipts rise across the two years.",
            "- SKU HX-440 is out of stock at DC-NORTH from 2025-06-02 through 2025-08-17.",
            "- Northland falls after August 2025 because Pinnacle Industrial stops ordering.",
            "- The stockout dollars do not explain the Northland drop.",
            "",
            "Stop at 10:00. Offer to rerun any one question from the SQL above.",
            "",
        ]
    )
    return "\n".join(lines)


def assert_structure(con, scale_name: str) -> int:
    tables = con.execute(
        """
        SELECT table_name, comment
        FROM duckdb_tables()
        WHERE schema_name = ?
        ORDER BY table_name
        """,
        [SCHEMA],
    ).fetchall()
    if len(tables) != len(TABLES):
        found = sorted(row[0] for row in tables)
        raise RuntimeError(f"table count {len(tables)} != {len(TABLES)}: {found}")
    blank_tables = [name for name, comment in tables if not (comment or "").strip()]
    if blank_tables:
        raise RuntimeError(f"tables missing comments: {blank_tables}")
    blank_cols = con.execute(
        """
        SELECT table_name, column_name
        FROM duckdb_columns()
        WHERE schema_name = ?
          AND (comment IS NULL OR length(trim(comment)) = 0)
        """,
        [SCHEMA],
    ).fetchall()
    if blank_cols:
        raise RuntimeError(f"columns missing comments: {blank_cols[:8]}")
    fk_count = con.execute(
        """
        SELECT COUNT(*)
        FROM duckdb_constraints()
        WHERE schema_name = ? AND constraint_type = 'FOREIGN KEY'
        """,
        [SCHEMA],
    ).fetchone()[0]
    expected_fk = sum(len(table.fks) for table in TABLES)
    if int(fk_count) != expected_fk:
        raise RuntimeError(f"foreign keys {fk_count} != {expected_fk}")
    rows = total_rows(con)
    if scale_name == "full" and rows < 1_000_000:
        raise RuntimeError(f"full scale has {rows} rows, need at least 1000000")
    if scale_name == "small" and rows > 200_000:
        raise RuntimeError(f"small scale has {rows} rows, cap is 200000")
    return rows


def assert_story(con) -> None:
    span = con.execute(
        f"SELECT MIN(order_date), MAX(order_date), COUNT(*) FROM {SCHEMA}.orders"
    ).fetchone()
    if span[0] != START or span[1] != AS_OF:
        raise RuntimeError(f"orders do not cover every endpoint: {span}")
    missing_days = con.execute(
        f"""
        SELECT COUNT(*) FROM {SCHEMA}.calendar c
        WHERE NOT EXISTS (
          SELECT 1 FROM {SCHEMA}.orders o WHERE o.order_date = c.calendar_date
        )
        """
    ).fetchone()[0]
    if int(missing_days) != 0:
        raise RuntimeError(f"{missing_days} days have no orders")
    pinnacle_after = con.execute(
        f"""
        SELECT COUNT(*)
        FROM {SCHEMA}.orders o
        JOIN {SCHEMA}.customers c ON c.customer_id = o.customer_id
        WHERE c.customer_code = 'CUST-PINNACLE'
          AND o.order_date > DATE '2025-08-31'
        """
    ).fetchone()[0]
    if int(pinnacle_after) != 0:
        raise RuntimeError("Pinnacle still orders after the cutoff")
    stock = con.execute(
        f"""
        SELECT s.sku_code, w.warehouse_code, COUNT(*)
        FROM {SCHEMA}.inventory_snapshots i
        JOIN {SCHEMA}.skus s ON s.sku_id = i.sku_id
        JOIN {SCHEMA}.warehouses w ON w.warehouse_id = i.warehouse_id
        WHERE i.qty_on_hand = 0
        GROUP BY s.sku_code, w.warehouse_code
        ORDER BY 1, 2
        """
    ).fetchall()
    if stock != [("HX-440", "DC-NORTH", 77)]:
        raise RuntimeError(f"stockout shape changed: {stock}")
    balance = con.execute(
        f"""
        SELECT
          (
            SELECT SUM(m.qty)
            FROM {SCHEMA}.inventory_movements m
            JOIN {SCHEMA}.skus s ON s.sku_id = m.sku_id
            JOIN {SCHEMA}.warehouses w ON w.warehouse_id = m.warehouse_id
            WHERE s.sku_code = 'HX-440' AND w.warehouse_code = 'DC-NORTH'
          ),
          (
            SELECT i.qty_on_hand
            FROM {SCHEMA}.inventory_snapshots i
            JOIN {SCHEMA}.skus s ON s.sku_id = i.sku_id
            JOIN {SCHEMA}.warehouses w ON w.warehouse_id = i.warehouse_id
            WHERE s.sku_code = 'HX-440'
              AND w.warehouse_code = 'DC-NORTH'
              AND i.snapshot_date = DATE '2025-12-31'
          )
        """
    ).fetchone()
    if int(balance[0]) != int(balance[1]):
        raise RuntimeError(f"movement balance {balance} does not match the last snapshot")
    by_id = {item["id"]: item["sql"] for item in questions()}
    why = run_query(con, by_id["q07"])
    if not why or why[0]["customer_code"] != "CUST-PINNACLE":
        raise RuntimeError(f"Northland decline is not Pinnacle: {why[:2]}")
    if float(why[0]["sep_dec_2025_usd"]) != 0:
        raise RuntimeError(f"Pinnacle still has Sep-Dec 2025 revenue: {why[0]}")
    late = run_query(con, by_id["q08"])
    if not late or late[0]["supplier_code"] != "SUP-KELSO":
        raise RuntimeError(f"rising late supplier is not Kelso: {late[:2]}")
    if float(late[0]["late_pct_point_change"]) < 15:
        raise RuntimeError(f"Kelso late-rate rise is too small: {late[0]}")
    outage = run_query(con, by_id["q09"])
    if (
        not outage
        or outage[0]["sku_code"] != "HX-440"
        or outage[0]["warehouse_code"] != "DC-NORTH"
        or int(outage[0]["zero_days"]) != 77
    ):
        raise RuntimeError(f"stockout answer changed: {outage}")


def build(path: str, scale_name: str, seed: int):
    import duckdb

    validate_model()
    if scale_name not in SCALES:
        raise RuntimeError(f"unknown scale {scale_name}")
    con = duckdb.connect(path)
    try:
        con.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        con.execute("DROP SCHEMA IF EXISTS harborline_build CASCADE")
        con.execute("DROP MACRO IF EXISTS hl_mix")
        con.execute(f"CREATE SCHEMA {SCHEMA}")
        for table in TABLES:
            con.execute(ddl(table))
        apply_comments(con)
        ctx = load_dimensions(con, SCALES[scale_name], seed)
        if ctx.track_skus > ctx.n_skus:
            raise RuntimeError("track_skus exceeds sku count")
        load_facts(con, ctx, seed)
        rows = assert_structure(con, scale_name)
        assert_story(con)
        return con, rows
    except Exception:
        con.close()
        raise


def gold_document(con, scale_name: str, seed: int, rows: int) -> dict:
    asked = []
    for item in questions():
        result = run_query(con, item["sql"])
        if not result:
            raise RuntimeError(f"{item['id']} returned no rows")
        asked.append(
            {
                "id": item["id"],
                "minute": item["minute"],
                "buyer": item["buyer"],
                "direction": item["direction"],
                "sql": item["sql"],
                "rows": result,
                "answer": answer_for(item["id"], result),
            }
        )
    return {
        "dataset": "harborline",
        "seed": seed,
        "scale": scale_name,
        "as_of": AS_OF.isoformat(),
        "currency": "USD",
        "table_count": len(TABLES),
        "row_count": rows,
        "questions": asked,
    }


def check_gold(con, path: Path) -> None:
    gold = json.loads(path.read_text(encoding="utf-8"))
    if not CATALOG_PATH.is_file():
        raise RuntimeError(f"missing catalog {CATALOG_PATH}")
    on_disk = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    if on_disk != catalog_document():
        raise RuntimeError("docs/demo/harborline_catalog.json does not match the generator")
    live = {item["id"]: item["sql"] for item in questions()}
    asked = gold.get("questions") or []
    if len(asked) != 12:
        raise RuntimeError(f"gold has {len(asked)} questions, need 12")
    for item in asked:
        statement = item.get("sql")
        if statement != live.get(item.get("id")):
            raise RuntimeError(f"{item.get('id')} SQL does not match the generator")
        con.execute(statement).fetchall()
    print(f"gold_sql_ok={len(asked)}", flush=True)


def load_postgres_files(dsn: str, con, directory: Path) -> None:
    import psycopg

    with psycopg.connect(dsn) as pg, pg.cursor() as cur:
        cur.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        cur.execute(f"CREATE SCHEMA {SCHEMA}")
        for table in TABLES:
            cur.execute(ddl(table))
        for table in TABLES:
            target = directory / f"{table.name}.csv"
            escaped = str(target).replace("'", "''")
            con.execute(
                f"COPY {SCHEMA}.{table.name} TO '{escaped}' (HEADER false, FORMAT CSV, NULL '')"
            )
            with (
                target.open("r", encoding="utf-8") as handle,
                cur.copy(
                    f"COPY {SCHEMA}.{table.name} FROM STDIN WITH (FORMAT CSV, NULL '')"
                ) as writer,
            ):
                while True:
                    chunk = handle.read(1024 * 1024)
                    if not chunk:
                        break
                    writer.write(chunk)
            target.unlink()
        for table in TABLES:
            cur.execute(f"COMMENT ON TABLE {SCHEMA}.{table.name} IS {quote(table.description)}")
            for item in table.columns:
                cur.execute(
                    "COMMENT ON COLUMN "
                    f"{SCHEMA}.{table.name}.{item.name} IS {quote(item.description)}"
                )
        pg.commit()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Build the Harborline demo company")
    parser.add_argument("--scale", choices=tuple(SCALES), default="full")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--duckdb", default="")
    parser.add_argument("--postgres", default="")
    parser.add_argument("--write-gold", default="")
    parser.add_argument("--write-walk", default="")
    parser.add_argument("--write-catalog", default="")
    parser.add_argument("--check-gold", default="")
    args = parser.parse_args(argv)
    if args.write_catalog:
        path = Path(args.write_catalog)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(dump_json(catalog_document()), encoding="utf-8")
        print(f"wrote_catalog={path}", flush=True)
    need_db = bool(
        args.duckdb or args.postgres or args.write_gold or args.check_gold or args.write_walk
    )
    if not need_db:
        return
    if args.write_gold and args.scale != "full":
        raise SystemExit("write-gold requires --scale full")
    import tempfile

    own_file = None
    db_path = args.duckdb
    if not db_path:
        own_file = tempfile.NamedTemporaryFile(prefix="harborline-", suffix=".duckdb")
        db_path = own_file.name
    else:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    con, rows = build(db_path, args.scale, args.seed)
    try:
        print(
            f"scale={args.scale} seed={args.seed} tables={len(TABLES)} rows={rows}",
            flush=True,
        )
        if args.write_gold or args.write_walk:
            gold = gold_document(con, args.scale, args.seed, rows)
            if args.write_gold:
                target = Path(args.write_gold)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(dump_json(gold), encoding="utf-8")
                print(f"wrote_gold={target}", flush=True)
            if args.write_walk:
                target = Path(args.write_walk)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(render_walk(gold), encoding="utf-8")
                print(f"wrote_walk={target}", flush=True)
        if args.check_gold:
            check_gold(con, Path(args.check_gold))
        if args.postgres:
            import tempfile as tempmod

            with tempmod.TemporaryDirectory(prefix="harborline-copy-") as directory:
                load_postgres_files(args.postgres, con, Path(directory))
            print("postgres_ok=1", flush=True)
    finally:
        con.close()
        if own_file is not None:
            own_file.close()


if __name__ == "__main__":
    main()
