"""F-0046: the orphans a max_rows cap invents, and what now catches them.

What this covers
----------------
``db_connector`` caps every extracted table independently at ``max_rows``
(``packages/executor/dms_executor/db_connector.py:436``). Cap a parent table and
the child rows that pointed at the cut parents become orphans **the source never
had**. ``truncated: true`` lands on the receipt and the preview.

Before SQLSRC-07, ``Ontology.verify()`` measured three claims - ``key_unique``,
``key_not_null``, and parent-side ``link_cardinality`` - and touched the child
side only to prove its declared columns exist. Referential integrity was never
measured and no ``Violation`` kind for it existed, so a capped parent left an
ontology that verified clean. The compiler then emitted ``LEFT JOIN`` on purpose
so no fact row is dropped, which protects the grand total and makes every named
group wrong: each orphaned order is attributed to a NULL region instead of the
region the source actually gave it. A plausible number under a green badge, with
no abstention - CLAUDE.md hard rule 12's most dangerous single failure.

The two branches below are the whole point, because ``verify()`` reads a DuckDB
connection and **cannot tell a source that was always dirty from one we cut
ourselves**. Those want opposite treatment:

A. Not told about the cap. Orphans are recorded on ``Ontology.orphans`` and
   measurement continues. That is correct for a parent landed whole - those
   children point at members that genuinely do not exist, no named group is
   wrong, and the NULL bucket preserves the total. Refusing it would be a
   control rejecting legitimate work (R-0005). Here it is wrong, and that is
   exactly why the fact has to be passed in rather than guessed.

B. Told the parent was capped, which is what ``verify_source_links`` does on the
   real path from ``SourcePull.truncated``. The link is refused by name and left
   unverified, so no measure can be grouped through it.

Seed is the shape of SQLSRC-06 (Netie-AI/dms#116) on SQL Server: 1100 customers,
160 orders, 80 of them on customers C1021-C1100, pulled with a cap that lands
1000 customers.

    python scripts/repro_capped_parent_orphans.py

Exit 0 = branch B refuses (the claim is in place). Exit 1 = a capped parent
still answers, which is the defect.
"""

from __future__ import annotations

import duckdb

from dms_executor.ontology import Ontology

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
    onto.add_object("customer", "customers", ["cust_ref"])
    onto.add_object("order", "orders", ["order_id"])
    onto.add_link("order_customer", "order", ["cust_ref"], "customer", ["cust_ref"])
    onto.add_measure("revenue", "order", "SUM(amount)")
    return onto


def main() -> int:
    con = duckdb.connect(":memory:")
    _seed(con)
    _extract(con)

    landed = con.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
    n_orders = con.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    orphans = con.execute(
        "SELECT COUNT(*) FROM orders o LEFT JOIN customers c USING (cust_ref) "
        "WHERE c.cust_ref IS NULL"
    ).fetchone()[0]
    print(f"landed: {landed} customers (capped from {N_CUSTOMERS}), {n_orders} orders")
    print(f"orphans the cap invented: {orphans} (the source had 0)")

    truth = {
        r[0]: float(r[1])
        for r in con.execute(
            "SELECT c.region, SUM(o.amount) FROM src_orders o "
            "JOIN src_customers c USING (cust_ref) GROUP BY c.region"
        ).fetchall()
    }

    print()
    print("A. verify() not told about the cap")
    blind = _ontology()
    blind_violations = blind.verify(con)
    recorded = blind.orphans.get("order_customer", {})
    print(f"   {len(blind_violations)} violation(s); orphans recorded: "
          f"rows={recorded.get('rows')} parent_capped={recorded.get('parent_capped')}")
    compiled = blind.compile("revenue", group_by=[("customer", "region")])
    if compiled:
        answered = {r[0]: float(r[1]) for r in con.execute(compiled.sql).fetchall()}
        wrong = [k for k, v in truth.items() if answered.get(k, 0.0) != v]
        print(f"   answers anyway: {len(wrong)} of {len(truth)} regions understated, "
              f"NULL bucket {answered.get(None):,.2f}, total "
              f"{sum(answered.values()):,.2f} == source {sum(truth.values()):,.2f}")
        print("   correct for a parent landed whole; wrong here - hence branch B")

    print()
    print("B. verify() told the parent was capped")
    told = _ontology()
    told_violations = told.verify(con, capped_relations={"customers"})
    print(f"   {len(told_violations)} violation(s)")
    for v in told_violations:
        print(f"   [{v.check}] {v.subject}: {v.detail}")
    refused = told.compile("revenue", group_by=[("customer", "region")])
    verdict = f"REFUSED: {refused.reason}" if not refused else "ANSWERED"
    print(f"   cardinality now {told.links['order_customer'].cardinality!r}; "
          f"compile() -> {verdict}")

    named = any(v.check == "link_intact" for v in told_violations)
    if named and not refused:
        print()
        print("CAUGHT: a capped parent refuses the join by name, and the orphan "
              "count is on the ontology either way.")
        return 0
    print()
    print("REPRODUCED: the capped parent still answers. Nothing would tell the "
          "customer which regions are understated.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
