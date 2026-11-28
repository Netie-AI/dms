"""Reproduce F-0046: a max_rows cap invents orphans verify() must refuse.

What this reproduces
--------------------
``db_connector`` caps every extracted table independently at ``max_rows``.
Cap a parent table and the child rows that pointed at the cut parents become
orphans **the source never had**. Until SQLSRC-07 (#157) ``Ontology.verify()``
measured three claims and referential integrity was not one of them, so a
capped parent left an ontology that verified clean.

The compiler then emits ``LEFT JOIN`` on purpose so no fact row is dropped.
That protects the grand total and makes the per-group numbers wrong: every
orphaned order is attributed to a NULL region instead of the region the
source actually gave it. The emitted note reads "verified many-to-one, so no
fact row is duplicated" - true, and about a different hazard than the one
present.

    python scripts/repro_capped_parent_orphans.py

Exit 1 = reproduced (verify() silent, or a grouped number differs from source
truth with nothing said). Exit 0 = the claim landed: verify() names the
orphans and compile() refuses the join.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "executor"))

import duckdb  # noqa: E402

from dms_executor.ontology import Ontology  # noqa: E402

CAP = 1000
N_CUSTOMERS = 1100
REGIONS = ("North", "South", "East", "West")


def _seed(con: duckdb.DuckDBPyConnection) -> None:
    """The source, whole: every order's customer exists."""
    con.execute("CREATE TABLE src_customers (cust_ref VARCHAR, region VARCHAR)")
    con.execute(
        "CREATE TABLE src_orders (order_id VARCHAR, cust_ref VARCHAR, amount DECIMAL(12,2))"
    )
    con.executemany(
        "INSERT INTO src_customers VALUES (?, ?)",
        [(f"C{1 + i:04d}", REGIONS[i % len(REGIONS)]) for i in range(N_CUSTOMERS)],
    )
    # 80 orders sit on the last 80 customers - the ones the cap will cut.
    orders = [(f"O{i:04d}", f"C{1 + (i % CAP):04d}", 10.00) for i in range(80)]
    orders += [(f"O1{i:04d}", f"C{N_CUSTOMERS - 80 + i:04d}", 10.00) for i in range(80)]
    con.executemany("INSERT INTO src_orders VALUES (?, ?, ?)", orders)


def _extract(con: duckdb.DuckDBPyConnection) -> None:
    """What db_connector lands: each table capped independently."""
    con.execute(
        f"CREATE TABLE customers AS SELECT * FROM src_customers ORDER BY cust_ref LIMIT {CAP}"
    )
    con.execute("CREATE TABLE orders AS SELECT * FROM src_orders")


def _ontology() -> Ontology:
    onto = Ontology()
    onto.add_object("customer", "customers", ["cust_ref"], truncated=True)
    onto.add_object("order", "orders", ["order_id"])
    onto.add_link("order_customer", "order", ["cust_ref"], "customer", ["cust_ref"])
    onto.add_measure("revenue", "order", "SUM(amount)")
    return onto


def main() -> int:
    con = duckdb.connect(":memory:")
    _seed(con)
    _extract(con)

    orphans = con.execute(
        "SELECT COUNT(*) FROM orders o LEFT JOIN customers c USING (cust_ref) "
        "WHERE c.cust_ref IS NULL"
    ).fetchone()[0]
    print(
        f"landed: {con.execute('SELECT COUNT(*) FROM customers').fetchone()[0]} customers "
        f"(capped from {N_CUSTOMERS}), "
        f"{con.execute('SELECT COUNT(*) FROM orders').fetchone()[0]} orders"
    )
    print(f"orphans the cap invented: {orphans} (the source had 0)")

    onto = _ontology()
    violations = onto.verify(con)
    print(
        f"\nverify() -> {len(violations)} violation(s): "
        f"{[(v.check, v.subject) for v in violations] or 'none'}"
    )
    for v in violations:
        print(f"  [{v.check}] {v.subject}: {v.detail}")

    compiled = onto.compile("revenue", group_by=[("customer", "region")])
    if not compiled:
        named = [v for v in violations if v.check == "fk_intact"]
        if named and "max_rows" in named[0].detail:
            print(f"compile() refused: {compiled.reason} - {compiled.detail}")
            return 0
        print(f"compile() refused for the wrong reason: {compiled}")
        return 1

    answered = {r[0]: float(r[1]) for r in con.execute(compiled.sql).fetchall()}
    truth = {
        r[0]: float(r[1])
        for r in con.execute(
            "SELECT c.region, SUM(o.amount) FROM src_orders o "
            "JOIN src_customers c USING (cust_ref) GROUP BY c.region"
        ).fetchall()
    }

    print(f"\n{'region':<10}{'answered':>12}{'source truth':>16}")
    wrong = []
    for region in sorted(set(truth) | {k for k in answered if k is not None}):
        got, want = answered.get(region, 0.0), truth.get(region, 0.0)
        flag = "" if got == want else "  <-- WRONG"
        if got != want:
            wrong.append(region)
        print(f"{region:<10}{got:>12,.2f}{want:>16,.2f}{flag}")
    if None in answered:
        print(f"{'NULL':<10}{answered[None]:>12,.2f}{'-':>16}   <-- orphans, in no region")

    silent = not violations
    if silent and wrong:
        print(
            f"\nREPRODUCED: verify() said nothing, and {len(wrong)} of "
            f"{len(truth)} regions are understated."
        )
        return 1
    print("\nverify() stayed silent and compile() still produced a number.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
