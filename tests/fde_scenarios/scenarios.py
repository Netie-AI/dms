"""FDE-SCENARIOS-01: deterministic multi-company datasets for forward-deployed evals.

Five synthetic companies, each a small but realistic operational database:

* ``retail``        Kedai Maju Trading: multi-warehouse inventory, SKU joins under
                    three different column names, sales and purchasing.
* ``manufacturing`` Sinar Precision: plants, machines, work orders, BOM, runs,
                    downtime, inspections.
* ``finance_ar``    Teguh Distribution: invoices, partial payments, allocations,
                    credit notes, multi-currency with monthly FX.
* ``hr``            Harmoni Group: employees with a manager self-join, salary history,
                    leave, reviews. Carries PII columns on purpose (name, email).
* ``saas``          Awan Cloud: plans, subscriptions, MRR events, weekly usage,
                    support tickets, invoices.

Nothing here is a customer's data. Every row comes from ``random.Random(seed)``, so
the same seed gives byte-identical tables on every machine, and the gold answers in
``answers.json`` can be re-derived and compared (``scripts/verify/fde_scenarios.py
--self-test``).

The schema is portable: column kinds are ``int``, ``text``, ``date``, ``ts``,
``dec`` (12,2) and ``rate`` (12,6). Each engine loader maps those to its own DDL.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

COMPANIES = ("retail", "manufacturing", "finance_ar", "hr", "saas")


@dataclass
class Table:
    name: str
    columns: list[tuple[str, str]]
    pk: list[str]
    fks: list[tuple[str, str, str]] = field(default_factory=list)  # (col, ref_table, ref_col)
    rows: list[tuple[Any, ...]] = field(default_factory=list)


@dataclass
class Dataset:
    company: str
    title: str
    tables: list[Table]

    def table(self, name: str) -> Table:
        for t in self.tables:
            if t.name == name:
                return t
        raise KeyError(name)


def _d(s: str) -> date:
    return date.fromisoformat(s)


def _rand_date(r: random.Random, lo: date, hi: date) -> date:
    return lo + timedelta(days=r.randint(0, (hi - lo).days))


def _money(x: float) -> float:
    return round(x + 1e-9, 2)


START = _d("2025-01-01")
END = _d("2026-06-30")


# --------------------------------------------------------------------------- retail
def build_retail() -> Dataset:
    r = random.Random(1101)
    warehouses = Table(
        "warehouses",
        [
            ("warehouse_id", "int"),
            ("code", "text"),
            ("name", "text"),
            ("city", "text"),
            ("capacity_units", "int"),
        ],
        ["warehouse_id"],
    )
    for i, (code, name, city, cap) in enumerate(
        [
            ("KL1", "Kuala Lumpur Central", "Kuala Lumpur", 60000),
            ("SHA", "Shah Alam DC", "Shah Alam", 90000),
            ("JHB", "Johor Bahru South", "Johor Bahru", 55000),
            ("KCH", "Kuching East", "Kuching", 50000),
        ],
        start=1,
    ):
        warehouses.rows.append((i, code, name, city, cap))

    categories = Table("categories", [("category_id", "int"), ("name", "text")], ["category_id"])
    cat_names = [
        "Beverages",
        "Snacks",
        "Household",
        "Personal Care",
        "Frozen",
        "Rice & Grains",
        "Baby",
        "Stationery",
    ]
    for i, n in enumerate(cat_names, start=1):
        categories.rows.append((i, n))

    suppliers = Table(
        "suppliers",
        [("supplier_id", "int"), ("name", "text"), ("country", "text"), ("lead_time_days", "int")],
        ["supplier_id"],
    )
    sup_names = [
        "Borneo Foods",
        "Pacific Home",
        "Selangor Mills",
        "Asia Personal",
        "Golden Rice Co",
        "Mega Paper",
        "Delta Frozen",
        "Kinder Asia",
        "Melaka Drinks",
        "Thai Snack Works",
        "Cebu Imports",
        "Lion Supplies",
    ]
    for i, n in enumerate(sup_names, start=1):
        suppliers.rows.append(
            (i, n, r.choice(["MY", "MY", "TH", "ID", "PH", "CN"]), r.randint(5, 40))
        )

    products = Table(
        "products",
        [
            ("sku", "text"),
            ("name", "text"),
            ("category_id", "int"),
            ("supplier_id", "int"),
            ("unit_cost", "dec"),
            ("list_price", "dec"),
            ("reorder_point", "int"),
            ("discontinued", "int"),
        ],
        ["sku"],
        [("category_id", "categories", "category_id"), ("supplier_id", "suppliers", "supplier_id")],
    )
    skus: list[str] = []
    cost: dict[str, float] = {}
    price: dict[str, float] = {}
    for i in range(1, 121):
        cat = r.randint(1, len(cat_names))
        sku = f"{cat_names[cat - 1][:3].upper()}-{1000 + i}"
        c = _money(r.uniform(1.5, 60))
        p = _money(c * r.uniform(1.15, 1.8))
        skus.append(sku)
        cost[sku], price[sku] = c, p
        products.rows.append(
            (
                sku,
                f"{cat_names[cat - 1]} item {i}",
                cat,
                r.randint(1, 12),
                c,
                p,
                r.choice([20, 50, 80, 120, 200]),
                1 if r.random() < 0.08 else 0,
            )
        )
    active = [p[0] for p in products.rows if p[7] == 0]

    stock = Table(
        "stock_levels",
        [
            ("warehouse_id", "int"),
            ("sku_code", "text"),
            ("qty_on_hand", "int"),
            ("qty_reserved", "int"),
            ("last_counted", "date"),
        ],
        ["warehouse_id", "sku_code"],
        [("warehouse_id", "warehouses", "warehouse_id"), ("sku_code", "products", "sku")],
    )
    for w in range(1, 5):
        for sku in skus:
            if r.random() < 0.8:
                on = 0 if r.random() < 0.07 else r.randint(1, 900)
                res = min(on, r.randint(0, 60)) if on else 0
                stock.rows.append((w, sku, on, res, _rand_date(r, _d("2026-05-01"), END)))

    customers = Table(
        "customers",
        [("customer_id", "int"), ("name", "text"), ("segment", "text"), ("state", "text")],
        ["customer_id"],
    )
    states = ["Selangor", "Kuala Lumpur", "Johor", "Penang", "Sarawak", "Sabah", "Perak"]
    for i in range(1, 201):
        customers.rows.append(
            (
                i,
                f"Customer {i:03d} Sdn Bhd",
                r.choice(["retail", "retail", "wholesale", "online"]),
                r.choice(states),
            )
        )

    orders = Table(
        "sales_orders",
        [
            ("order_id", "int"),
            ("customer_id", "int"),
            ("warehouse_id", "int"),
            ("order_date", "date"),
            ("status", "text"),
        ],
        ["order_id"],
        [
            ("customer_id", "customers", "customer_id"),
            ("warehouse_id", "warehouses", "warehouse_id"),
        ],
    )
    lines = Table(
        "sales_order_lines",
        [
            ("order_id", "int"),
            ("line_no", "int"),
            ("item_code", "text"),
            ("qty", "int"),
            ("unit_price", "dec"),
            ("discount_pct", "dec"),
        ],
        ["order_id", "line_no"],
        [("order_id", "sales_orders", "order_id"), ("item_code", "products", "sku")],
    )
    hot = r.sample(active, 25)
    for oid in range(1, 1601):
        od = _rand_date(r, START, END)
        st = r.choices(["shipped", "cancelled", "returned", "pending"], [80, 8, 5, 7])[0]
        if od > _d("2026-06-20") and st == "shipped" and r.random() < 0.5:
            st = "pending"
        orders.rows.append((oid, r.randint(1, 200), r.choice([1, 1, 2, 2, 2, 3, 4]), od, st))
        for ln in range(1, r.randint(1, 5) + 1):
            sku = r.choice(hot) if r.random() < 0.5 else r.choice(skus)
            lines.rows.append(
                (
                    oid,
                    ln,
                    sku,
                    r.randint(1, 40),
                    _money(price[sku] * r.uniform(0.95, 1.05)),
                    float(r.choice([0, 0, 0, 5, 10, 15])),
                )
            )

    pos = Table(
        "purchase_orders",
        [
            ("po_id", "int"),
            ("supplier_id", "int"),
            ("warehouse_id", "int"),
            ("order_date", "date"),
            ("expected_date", "date"),
            ("received_date", "date"),
            ("status", "text"),
        ],
        ["po_id"],
        [
            ("supplier_id", "suppliers", "supplier_id"),
            ("warehouse_id", "warehouses", "warehouse_id"),
        ],
    )
    pol = Table(
        "purchase_order_lines",
        [
            ("po_id", "int"),
            ("line_no", "int"),
            ("sku", "text"),
            ("qty_ordered", "int"),
            ("qty_received", "int"),
            ("unit_cost", "dec"),
        ],
        ["po_id", "line_no"],
        [("po_id", "purchase_orders", "po_id"), ("sku", "products", "sku")],
    )
    lead = {s[0]: s[3] for s in suppliers.rows}
    for pid in range(1, 321):
        sup = r.randint(1, 12)
        od = _rand_date(r, START, END)
        exp_d = od + timedelta(days=lead[sup])
        late = r.randint(-3, 12)
        rec = exp_d + timedelta(days=late)
        if rec > END or r.random() < 0.05:
            recv, status = None, "open"
        else:
            recv, status = rec, "received"
        pos.rows.append((pid, sup, r.randint(1, 4), od, exp_d, recv, status))
        sup_skus = [p[0] for p in products.rows if p[3] == sup] or skus
        for ln in range(1, r.randint(1, 4) + 1):
            sku = r.choice(sup_skus)
            q = r.choice([100, 200, 300, 500, 1000])
            got = 0 if recv is None else (q if r.random() < 0.8 else int(q * r.uniform(0.6, 0.98)))
            pol.rows.append((pid, ln, sku, q, got, _money(cost[sku] * r.uniform(0.97, 1.03))))

    transfers = Table(
        "stock_transfers",
        [
            ("transfer_id", "int"),
            ("from_warehouse_id", "int"),
            ("to_warehouse_id", "int"),
            ("sku", "text"),
            ("qty", "int"),
            ("transfer_date", "date"),
        ],
        ["transfer_id"],
        [
            ("from_warehouse_id", "warehouses", "warehouse_id"),
            ("to_warehouse_id", "warehouses", "warehouse_id"),
            ("sku", "products", "sku"),
        ],
    )
    for tid in range(1, 181):
        a, b = r.sample([1, 2, 3, 4], 2)
        transfers.rows.append(
            (tid, a, b, r.choice(skus), r.randint(10, 300), _rand_date(r, START, END))
        )

    return Dataset(
        "retail",
        "Kedai Maju Trading (retail, multi-warehouse inventory)",
        [
            warehouses,
            categories,
            suppliers,
            products,
            stock,
            customers,
            orders,
            lines,
            pos,
            pol,
            transfers,
        ],
    )


# --------------------------------------------------------------------------- manufacturing
def build_manufacturing() -> Dataset:
    r = random.Random(2202)
    plants = Table(
        "plants", [("plant_id", "int"), ("name", "text"), ("country", "text")], ["plant_id"]
    )
    plants.rows += [(1, "Shah Alam", "MY"), (2, "Penang", "MY"), (3, "Batam", "ID")]
    machines = Table(
        "machines",
        [
            ("machine_id", "int"),
            ("plant_id", "int"),
            ("machine_code", "text"),
            ("machine_type", "text"),
            ("commissioned_date", "date"),
        ],
        ["machine_id"],
        [("plant_id", "plants", "plant_id")],
    )
    types = ["CNC Mill", "CNC Lathe", "Injection Molder", "Press", "Assembly Cell"]
    mid = 0
    for p in (1, 2, 3):
        for _ in range({1: 9, 2: 7, 3: 5}[p]):
            mid += 1
            t = r.choice(types)
            machines.rows.append(
                (
                    mid,
                    p,
                    f"{['SA', 'PG', 'BT'][p - 1]}-{t.split()[-1][:3].upper()}-{mid:02d}",
                    t,
                    _rand_date(r, _d("2015-01-01"), _d("2024-12-31")),
                )
            )
    reasons = Table(
        "downtime_reasons",
        [("reason_code", "text"), ("description", "text"), ("planned", "int")],
        ["reason_code"],
    )
    reasons.rows += [
        ("PM", "Preventive maintenance", 1),
        ("CO", "Changeover", 1),
        ("BRK", "Breakdown", 0),
        ("MAT", "Material shortage", 0),
        ("QA", "Quality hold", 0),
        ("OPR", "No operator", 0),
    ]
    products = Table(
        "products",
        [
            ("product_code", "text"),
            ("description", "text"),
            ("family", "text"),
            ("std_cycle_time_sec", "int"),
        ],
        ["product_code"],
    )
    fams = ["Brackets", "Housings", "Shafts", "Connectors"]
    for i in range(1, 31):
        f = r.choice(fams)
        products.rows.append((f"P{i:03d}", f"{f[:-1]} model {i}", f, r.randint(20, 240)))
    components = Table(
        "components",
        [("component_code", "text"), ("description", "text"), ("unit_cost", "dec")],
        ["component_code"],
    )
    for i in range(1, 41):
        components.rows.append((f"C{i:03d}", f"Component {i}", _money(r.uniform(0.2, 25))))
    bom = Table(
        "bom",
        [("product_code", "text"), ("component_code", "text"), ("qty_per", "int")],
        ["product_code", "component_code"],
        [
            ("product_code", "products", "product_code"),
            ("component_code", "components", "component_code"),
        ],
    )
    for p in products.rows:
        for c in r.sample(range(1, 41), r.randint(2, 6)):
            bom.rows.append((p[0], f"C{c:03d}", r.randint(1, 8)))
    wos = Table(
        "work_orders",
        [
            ("wo_id", "int"),
            ("product_code", "text"),
            ("plant_id", "int"),
            ("qty_planned", "int"),
            ("created_date", "date"),
            ("due_date", "date"),
            ("status", "text"),
        ],
        ["wo_id"],
        [("product_code", "products", "product_code"), ("plant_id", "plants", "plant_id")],
    )
    runs = Table(
        "production_runs",
        [
            ("run_id", "int"),
            ("wo_id", "int"),
            ("machine_id", "int"),
            ("run_date", "date"),
            ("shift", "text"),
            ("good_qty", "int"),
            ("scrap_qty", "int"),
            ("run_minutes", "int"),
        ],
        ["run_id"],
        [("wo_id", "work_orders", "wo_id"), ("machine_id", "machines", "machine_id")],
    )
    insp = Table(
        "quality_inspections",
        [
            ("inspection_id", "int"),
            ("run_id", "int"),
            ("inspected_qty", "int"),
            ("defects_found", "int"),
            ("defect_type", "text"),
        ],
        ["inspection_id"],
        [("run_id", "production_runs", "run_id")],
    )
    by_plant = {p: [m[0] for m in machines.rows if m[1] == p] for p in (1, 2, 3)}
    scrap_bias = {m[0]: r.uniform(0.005, 0.06) for m in machines.rows}
    run_id = insp_id = 0
    for wo in range(1, 461):
        p = r.choice([1, 1, 2, 2, 3])
        cd = _rand_date(r, START, _d("2026-06-10"))
        due = cd + timedelta(days=r.randint(7, 35))
        qty = r.choice([500, 1000, 1500, 2000, 3000])
        status = "cancelled" if r.random() < 0.06 else "completed"
        if status == "completed" and due > _d("2026-06-15") and r.random() < 0.7:
            status = "in_progress"
        wos.rows.append((wo, f"P{r.randint(1, 30):03d}", p, qty, cd, due, status))
        if status == "cancelled":
            continue
        target = qty if status == "completed" else int(qty * r.uniform(0.1, 0.8))
        if status == "completed" and r.random() < 0.15:
            target = int(qty * r.uniform(1.01, 1.08))
        made = 0
        day = cd + timedelta(days=1)
        while made < target:
            m = r.choice(by_plant[p])
            good = min(target - made, r.randint(150, 600))
            scrap = int(round(good * scrap_bias[m] * r.uniform(0.5, 1.6)))
            shift = r.choice("ABC")
            if shift == "C":
                scrap += int(good * 0.01)
            run_id += 1
            rd = min(day, END)
            runs.rows.append((run_id, wo, m, rd, shift, good, scrap, r.randint(120, 480)))
            made += good
            day += timedelta(days=r.randint(0, 3))
            if r.random() < 0.4:
                insp_id += 1
                inspected = min(good, r.randint(20, 120))
                defects = r.choice([0, 0, 0, 1, 2, 3, 5])
                insp.rows.append(
                    (
                        insp_id,
                        run_id,
                        inspected,
                        defects,
                        None
                        if defects == 0
                        else r.choice(["Burr", "Dimensional", "Scratch", "Crack", "Porosity"]),
                    )
                )
    down = Table(
        "downtime_events",
        [
            ("event_id", "int"),
            ("machine_id", "int"),
            ("start_ts", "ts"),
            ("end_ts", "ts"),
            ("reason_code", "text"),
        ],
        ["event_id"],
        [
            ("machine_id", "machines", "machine_id"),
            ("reason_code", "downtime_reasons", "reason_code"),
        ],
    )
    for e in range(1, 901):
        m = r.randint(1, mid)
        st = datetime.combine(_rand_date(r, START, END), datetime.min.time()) + timedelta(
            minutes=r.randint(0, 23 * 60)
        )
        dur = r.randint(10, 480)
        down.rows.append(
            (
                e,
                m,
                st,
                st + timedelta(minutes=dur),
                r.choices(["PM", "CO", "BRK", "MAT", "QA", "OPR"], [20, 25, 25, 12, 8, 10])[0],
            )
        )
    return Dataset(
        "manufacturing",
        "Sinar Precision Components (discrete manufacturing)",
        [plants, machines, reasons, products, components, bom, wos, runs, insp, down],
    )


# --------------------------------------------------------------------------- finance / AR
def build_finance_ar() -> Dataset:
    r = random.Random(3303)
    customers = Table(
        "customers",
        [
            ("cust_no", "text"),
            ("legal_name", "text"),
            ("region", "text"),
            ("credit_limit", "dec"),
            ("payment_terms_days", "int"),
        ],
        ["cust_no"],
    )
    regions = ["Central", "Northern", "Southern", "East Malaysia", "Singapore"]
    for i in range(1, 121):
        customers.rows.append(
            (
                f"C{i:05d}",
                f"Debtor {i:03d} Bhd",
                r.choice(regions),
                float(r.choice([20000, 50000, 100000, 250000])),
                r.choice([14, 30, 30, 45, 60]),
            )
        )
    fx = Table(
        "fx_rates",
        [("currency", "text"), ("rate_month", "text"), ("rate_to_myr", "rate")],
        ["currency", "rate_month"],
    )
    months = []
    m = _d("2025-01-01")
    while m <= END:
        months.append(m.strftime("%Y-%m"))
        m = (m.replace(day=28) + timedelta(days=4)).replace(day=1)
    for ym in months:
        fx.rows.append(("MYR", ym, 1.0))
        fx.rows.append(("SGD", ym, round(r.uniform(3.35, 3.55), 6)))
        fx.rows.append(("USD", ym, round(r.uniform(4.2, 4.75), 6)))
    inv = Table(
        "invoices",
        [
            ("invoice_no", "text"),
            ("cust_no", "text"),
            ("invoice_date", "date"),
            ("due_date", "date"),
            ("currency", "text"),
            ("status", "text"),
        ],
        ["invoice_no"],
        [("cust_no", "customers", "cust_no")],
    )
    lines = Table(
        "invoice_lines",
        [("invoice_no", "text"), ("line_no", "int"), ("description", "text"), ("amount", "dec")],
        ["invoice_no", "line_no"],
        [("invoice_no", "invoices", "invoice_no")],
    )
    pays = Table(
        "payments",
        [
            ("payment_id", "int"),
            ("cust_no", "text"),
            ("payment_date", "date"),
            ("amount", "dec"),
            ("method", "text"),
        ],
        ["payment_id"],
        [("cust_no", "customers", "cust_no")],
    )
    alloc = Table(
        "payment_allocations",
        [("payment_id", "int"), ("invoice_no", "text"), ("amount_applied", "dec")],
        ["payment_id", "invoice_no"],
        [("payment_id", "payments", "payment_id"), ("invoice_no", "invoices", "invoice_no")],
    )
    cns = Table(
        "credit_notes",
        [
            ("credit_note_no", "text"),
            ("invoice_no", "text"),
            ("issue_date", "date"),
            ("amount", "dec"),
        ],
        ["credit_note_no"],
        [("invoice_no", "invoices", "invoice_no")],
    )
    terms = {c[0]: c[4] for c in customers.rows}
    cur_of = {
        c[0]: ("SGD" if c[2] == "Singapore" else r.choice(["MYR"] * 8 + ["USD"]))
        for c in customers.rows
    }
    pid = cn = 0
    for i in range(1, 2201):
        cust = f"C{r.randint(1, 120):05d}"
        idt = _rand_date(r, START, END)
        if idt >= _d("2026-01-01") and int(cust[1:]) % 13 == 0:
            # A few debtors stop buying after 2025 (lapsed accounts).
            cust = f"C{int(cust[1:]) - 1:05d}"
        due = idt + timedelta(days=terms[cust])
        status = "void" if r.random() < 0.03 else "open"
        no = f"INV-{idt.year}-{i:05d}"
        inv_rows_total = 0.0
        for ln in range(1, r.randint(1, 4) + 1):
            amt = _money(r.uniform(150, 9000))
            inv_rows_total += amt
            lines.rows.append((no, ln, r.choice(["Goods", "Freight", "Service", "Handling"]), amt))
        total = _money(inv_rows_total)
        open_amt = total
        if status == "open" and r.random() < 0.06:
            cn += 1
            c_amt = _money(total * r.uniform(0.05, 0.3))
            cns.rows.append((f"CN-{cn:05d}", no, idt + timedelta(days=r.randint(2, 40)), c_amt))
            open_amt = _money(open_amt - c_amt)
        if status == "open":
            behaviour = r.random()
            pay_day = due + timedelta(days=r.randint(-10, 75))
            if behaviour < 0.7 and pay_day <= END:
                pid += 1
                pays.rows.append(
                    (
                        pid,
                        cust,
                        pay_day,
                        open_amt,
                        r.choice(["bank_transfer", "bank_transfer", "cheque", "card"]),
                    )
                )
                alloc.rows.append((pid, no, open_amt))
                status = "paid"
            elif behaviour < 0.85 and pay_day <= END:
                pid += 1
                part = _money(open_amt * r.uniform(0.2, 0.8))
                pays.rows.append((pid, cust, pay_day, part, "bank_transfer"))
                alloc.rows.append((pid, no, part))
        inv.rows.append((no, cust, idt, due, cur_of[cust], status))
    # Unapplied cash: payments with no or partial allocation.
    for _ in range(25):
        pid += 1
        cust = f"C{r.randint(1, 120):05d}"
        pays.rows.append(
            (pid, cust, _rand_date(r, START, END), _money(r.uniform(200, 5000)), "bank_transfer")
        )
    return Dataset(
        "finance_ar",
        "Teguh Distribution (finance, accounts receivable)",
        [customers, fx, inv, lines, pays, alloc, cns],
    )


# --------------------------------------------------------------------------- HR
def build_hr() -> Dataset:
    r = random.Random(4404)
    depts = Table(
        "departments",
        [("dept_id", "int"), ("dept_name", "text"), ("cost_center", "text")],
        ["dept_id"],
    )
    names = [
        "Engineering",
        "Sales",
        "Finance",
        "Operations",
        "Customer Support",
        "People",
        "Marketing",
    ]
    for i, n in enumerate(names, start=1):
        depts.rows.append((i, n, f"CC{100 + i}"))
    locs = Table(
        "locations",
        [("location_id", "int"), ("city", "text"), ("country", "text"), ("currency", "text")],
        ["location_id"],
    )
    locs.rows += [
        (1, "Kuala Lumpur", "MY", "MYR"),
        (2, "Penang", "MY", "MYR"),
        (3, "Singapore", "SG", "SGD"),
        (4, "Jakarta", "ID", "IDR"),
    ]
    emps = Table(
        "employees",
        [
            ("emp_id", "int"),
            ("full_name", "text"),
            ("email", "text"),
            ("hire_date", "date"),
            ("termination_date", "date"),
            ("dept_id", "int"),
            ("location_id", "int"),
            ("manager_id", "int"),
            ("job_level", "int"),
            ("employment_type", "text"),
            ("gender", "text"),
        ],
        ["emp_id"],
        [
            ("dept_id", "departments", "dept_id"),
            ("location_id", "locations", "location_id"),
            ("manager_id", "employees", "emp_id"),
        ],
    )
    first = [
        "Aisyah",
        "Wei Jie",
        "Priya",
        "Daniel",
        "Nurul",
        "Kumar",
        "Mei Ling",
        "Hafiz",
        "Siti",
        "Jason",
        "Farah",
        "Arjun",
        "Li Na",
        "Imran",
        "Grace",
        "Rizal",
    ]
    last = ["Tan", "Abdullah", "Lim", "Raj", "Wong", "Ismail", "Lee", "Ng", "Hassan", "Chong"]
    managers_by_dept: dict[int, list[int]] = {d: [] for d in range(1, 8)}
    for e in range(1, 421):
        dept = ((e - 1) % 7) + 1 if e <= 7 else r.choice([1, 1, 1, 2, 2, 3, 4, 4, 5, 5, 6, 7])
        hire = _rand_date(r, _d("2014-01-01"), _d("2026-06-15")) if e > 7 else _d("2014-03-01")
        term = None
        if e > 7 and r.random() < 0.22:
            t = hire + timedelta(days=r.randint(90, 2500))
            if t <= END:
                term = t
        level = 7 if e <= 7 else r.choices([1, 2, 3, 4, 5, 6], [20, 25, 22, 15, 10, 4])[0]
        mgr = None
        if e > 7:
            cands = [m for m in managers_by_dept[dept]] or [dept]
            mgr = r.choice(cands)
        if level >= 4 and e > 7:
            managers_by_dept[dept].append(e)
        fn = f"{r.choice(first)} {r.choice(last)}"
        emps.rows.append(
            (
                e,
                fn,
                f"{fn.lower().replace(' ', '.')}.{e}@harmoni.example",
                hire,
                term,
                dept,
                r.choice([1, 1, 1, 2, 3, 4]) if e > 7 else 1,
                mgr,
                level,
                r.choices(["full_time", "part_time", "contract"], [80, 7, 13])[0],
                r.choice(["F", "M"]),
            )
        )
    loc_cur = {loc[0]: loc[3] for loc in locs.rows}
    sal = Table(
        "salary_history",
        [
            ("emp_id", "int"),
            ("effective_date", "date"),
            ("base_salary_annual", "dec"),
            ("currency", "text"),
        ],
        ["emp_id", "effective_date"],
        [("emp_id", "employees", "emp_id")],
    )
    mult = {"MYR": 1.0, "SGD": 0.42, "IDR": 3500.0}
    for e in emps.rows:
        cur = loc_cur[e[6]]
        base = (36000 + e[8] * 18000) * r.uniform(0.85, 1.2)
        d = e[3]
        end = e[4] or END
        while d <= end:
            sal.rows.append((e[0], d, _money(base * mult[cur]), cur))
            d = date(d.year + 1, 4, 1) if d.month < 4 else date(d.year + 1, 4, 1)
            base *= r.choice([1.0, 1.03, 1.04, 1.05, 1.06, 1.08, 1.12, 1.15])
    leave = Table(
        "leave_requests",
        [
            ("request_id", "int"),
            ("emp_id", "int"),
            ("leave_type", "text"),
            ("start_date", "date"),
            ("end_date", "date"),
            ("days", "int"),
            ("status", "text"),
        ],
        ["request_id"],
        [("emp_id", "employees", "emp_id")],
    )
    rid = 0
    for e in emps.rows:
        for _ in range(r.randint(0, 9)):
            sd = _rand_date(r, START, END)
            if sd < e[3] or (e[4] and sd > e[4]):
                continue
            days = r.randint(1, 7)
            rid += 1
            leave.rows.append(
                (
                    rid,
                    e[0],
                    r.choice(["annual", "annual", "annual", "sick", "unpaid"]),
                    sd,
                    sd + timedelta(days=days - 1),
                    days,
                    r.choices(["approved", "rejected", "pending"], [82, 8, 10])[0],
                )
            )
    rev = Table(
        "performance_reviews",
        [("emp_id", "int"), ("review_year", "int"), ("rating", "int")],
        ["emp_id", "review_year"],
        [("emp_id", "employees", "emp_id")],
    )
    dept_bias = {1: 0.3, 2: -0.6, 3: 0.1, 4: -0.2, 5: -0.9, 6: 0.2, 7: 0.0}
    for e in emps.rows:
        for y in (2024, 2025):
            if e[3] <= date(y, 6, 30) and (e[4] is None or e[4] >= date(y, 12, 31)):
                if r.random() < 0.9:
                    rating = max(1, min(5, round(3 + dept_bias[e[5]] + r.gauss(0, 0.9))))
                    rev.rows.append((e[0], y, rating))
    return Dataset(
        "hr", "Harmoni Group (HR and people analytics)", [depts, locs, emps, sal, leave, rev]
    )


# --------------------------------------------------------------------------- SaaS
def build_saas() -> Dataset:
    r = random.Random(5505)
    plans = Table(
        "plans",
        [
            ("plan_id", "int"),
            ("plan_name", "text"),
            ("tier", "text"),
            ("price_per_seat", "dec"),
            ("billing_period", "text"),
        ],
        ["plan_id"],
    )
    plans.rows += [
        (1, "Starter Monthly", "Starter", 9.0, "monthly"),
        (2, "Starter Annual", "Starter", 7.5, "annual"),
        (3, "Growth Monthly", "Growth", 25.0, "monthly"),
        (4, "Growth Annual", "Growth", 21.0, "annual"),
        (5, "Enterprise Annual", "Enterprise", 55.0, "annual"),
    ]
    accts = Table(
        "accounts",
        [
            ("account_id", "int"),
            ("company_name", "text"),
            ("country", "text"),
            ("industry", "text"),
            ("signup_date", "date"),
            ("sales_owner", "text"),
        ],
        ["account_id"],
    )
    owners = ["Amir", "Bella", "Chen", "Devi"]
    for a in range(1, 321):
        accts.rows.append(
            (
                a,
                f"Awan Client {a:03d}",
                r.choice(["MY", "MY", "SG", "ID", "TH", "PH", "VN"]),
                r.choice(
                    ["Retail", "Logistics", "Healthcare", "Education", "Fintech", "Manufacturing"]
                ),
                _rand_date(r, _d("2023-01-01"), _d("2026-06-15")),
                r.choice(owners),
            )
        )
    subs = Table(
        "subscriptions",
        [
            ("subscription_id", "int"),
            ("account_id", "int"),
            ("plan_id", "int"),
            ("start_date", "date"),
            ("end_date", "date"),
            ("seats", "int"),
            ("mrr", "dec"),
        ],
        ["subscription_id"],
        [("account_id", "accounts", "account_id"), ("plan_id", "plans", "plan_id")],
    )
    events = Table(
        "subscription_events",
        [
            ("event_id", "int"),
            ("subscription_id", "int"),
            ("event_date", "date"),
            ("event_type", "text"),
            ("mrr_delta", "dec"),
        ],
        ["event_id"],
        [("subscription_id", "subscriptions", "subscription_id")],
    )
    price = {p[0]: p[3] for p in plans.rows}
    sid = eid = 0
    for a in accts.rows:
        start = a[4]
        plan = r.choices([1, 2, 3, 4, 5], [25, 15, 25, 20, 15])[0]
        seats = r.randint(3, 40) * (4 if plan == 5 else 1)
        while start <= END:
            sid += 1
            mrr = _money(seats * price[plan])
            end = None
            nxt = None
            roll = r.random()
            if roll < 0.25:
                end = start + timedelta(days=r.randint(60, 700))
                if end > END:
                    end = None
            elif roll < 0.5:
                nxt = start + timedelta(days=r.randint(60, 500))
                if nxt > END:
                    nxt = None
                else:
                    end = nxt
            subs.rows.append((sid, a[0], plan, start, end, seats, mrr))
            if start >= START:
                eid += 1
                events.rows.append(
                    (
                        eid,
                        sid,
                        start,
                        "new"
                        if nxt is None and sid == 0
                        else ("new" if subs.rows[-1][3] == a[4] else "reactivation"),
                        mrr,
                    )
                )
            if end is not None and nxt is None:
                if end >= START:
                    eid += 1
                    events.rows.append((eid, sid, end, "churn", -mrr))
                break
            if nxt is None:
                break
            old_mrr = mrr
            plan2 = min(5, plan + 1) if r.random() < 0.6 else max(1, plan - 1)
            seats = max(1, seats + r.randint(-5, 15))
            new_mrr = _money(seats * price[plan2])
            if nxt >= START:
                eid += 1
                events.rows.append(
                    (
                        eid,
                        sid,
                        nxt,
                        "upgrade" if new_mrr > old_mrr else "downgrade",
                        _money(new_mrr - old_mrr),
                    )
                )
            plan = plan2
            start = nxt
            # mark next subscription as a continuation (not counted as new)
            sid += 1
            subs.rows.append((sid, a[0], plan, start, None, seats, new_mrr))
            if r.random() < 0.2:
                end2 = start + timedelta(days=r.randint(30, 400))
                if end2 <= END:
                    subs.rows[-1] = (sid, a[0], plan, start, end2, seats, new_mrr)
                    if end2 >= START:
                        eid += 1
                        events.rows.append((eid, sid, end2, "churn", -new_mrr))
            break
    usage = Table(
        "usage_weekly",
        [
            ("account_id", "int"),
            ("week_start", "date"),
            ("active_users", "int"),
            ("api_calls", "int"),
        ],
        ["account_id", "week_start"],
        [("account_id", "accounts", "account_id")],
    )
    active_span: dict[int, list[tuple[date, date | None, int]]] = {}
    for s in subs.rows:
        active_span.setdefault(s[1], []).append((s[3], s[4], s[5]))
    wk = _d("2025-12-29")
    weeks = []
    while wk <= END:
        weeks.append(wk)
        wk += timedelta(days=7)
    decline = set(r.sample(range(1, 321), 40))
    for a in accts.rows:
        for i, w in enumerate(weeks):
            seats = 0
            for st, en, se in active_span.get(a[0], []):
                if st <= w and (en is None or en > w):
                    seats = se
            if seats == 0:
                continue
            frac = r.uniform(0.5, 0.95)
            if a[0] in decline:
                frac *= max(0.15, 1 - i / len(weeks))
            au = max(1, int(seats * frac))
            usage.rows.append((a[0], w, au, au * r.randint(50, 900)))
    tickets = Table(
        "support_tickets",
        [
            ("ticket_id", "int"),
            ("account_id", "int"),
            ("opened_at", "ts"),
            ("closed_at", "ts"),
            ("priority", "text"),
            ("category", "text"),
        ],
        ["ticket_id"],
        [("account_id", "accounts", "account_id")],
    )
    for t in range(1, 1401):
        op = datetime.combine(_rand_date(r, START, END), datetime.min.time()) + timedelta(
            minutes=r.randint(0, 1439)
        )
        pr = r.choices(["low", "normal", "high", "urgent"], [30, 45, 18, 7])[0]
        hrs = {"low": 72, "normal": 36, "high": 12, "urgent": 4}[pr] * r.uniform(0.2, 3)
        cl = op + timedelta(hours=hrs)
        if cl > datetime(2026, 6, 30, 23, 59) or r.random() < 0.03:
            cl = None
        tickets.rows.append(
            (
                t,
                r.randint(1, 320),
                op,
                cl,
                pr,
                r.choice(["billing", "bug", "how-to", "integration", "outage"]),
            )
        )
    invoices = Table(
        "invoices",
        [
            ("invoice_id", "int"),
            ("account_id", "int"),
            ("invoice_date", "date"),
            ("amount", "dec"),
            ("paid_date", "date"),
        ],
        ["invoice_id"],
        [("account_id", "accounts", "account_id")],
    )
    iid = 0
    for s in subs.rows:
        d = max(s[3], START)
        stop = min(s[4] or END, END)
        while d <= stop:
            iid += 1
            paid = d + timedelta(days=r.randint(0, 45))
            invoices.rows.append(
                (iid, s[1], d, s[6], None if (paid > END or r.random() < 0.04) else paid)
            )
            d = (d.replace(day=28) + timedelta(days=4)).replace(day=min(d.day, 28))
    return Dataset(
        "saas",
        "Awan Cloud (B2B SaaS subscriptions)",
        [plans, accts, subs, events, usage, tickets, invoices],
    )


BUILDERS = {
    "retail": build_retail,
    "manufacturing": build_manufacturing,
    "finance_ar": build_finance_ar,
    "hr": build_hr,
    "saas": build_saas,
}


def build(company: str) -> Dataset:
    return BUILDERS[company]()
