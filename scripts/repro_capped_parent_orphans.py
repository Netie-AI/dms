"""Reproduce F-0046: verify() is silent about the orphans a max_rows cap invents.

What this reproduces
--------------------
``db_connector`` caps every extracted table independently at ``max_rows``
(``packages/executor/dms_executor/db_connector.py:436``). Cap a parent table and
the child rows that pointed at the cut parents become orphans **the source never
had**. ``truncated: true`` lands on the receipt and the preview; it never
reaches the semantic layer.

``Ontology.verify()`` (``packages/executor/dms_executor/ontology.py``) measures three claims -
``key_unique``, ``key_not_null``, and parent-side ``link_cardinality``. The
child side is read only to prove its declared columns exist
(``SELECT {child_cols} FROM {child} LIMIT 0``, ~``:311``). **Referential
integrity is never measured, and no Violation class for it exists.** So a
capped parent leaves an ontology that verifies clean.

The compiler then emits ``LEFT JOIN`` on purpose (its ``_join_chain``) so that no fact row
is dropped. That protects the grand total and makes the per-group numbers wrong:
every orphaned order is attributed to a NULL region instead of the region the
source actually gave it. The emitted note reads "verified many-to-one, so no
fact row is duplicated" - true, and about a different hazard than the one
present.

Customer-visible shape: a plausible number under a green badge, with no
abstention. CLAUDE.md hard rule 12 names this the most dangerous single failure.

Seed is the shape of SQLSRC-06 (Netie-AI/dms#116) on SQL Server: 1100 customers,
160 orders, 80 of them on customers C1021-C1100, pulled with a cap that lands
1000 customers.

    python scripts/repro_capped_parent_orphans.py

Exit 1 = reproduced (verify() silent, or a grouped number differs from source
truth with nothing said). Exit 0 = something now catches it.
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
    con.execute("CREATE TABLE src_orders (order_id VARCHAR, cust_ref VARCHAR, amount DECIMAL(12,2))")
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

    orphans = con.execute(
        "SELECT COUNT(*) FROM orders o LEFT JOIN customers c USING (cust_ref) "
        "WHERE c.cust_ref IS NULL"
    ).fetchone()[0]
    print(f"landed: {con.execute('SELECT COUNT(*) FROM customers').fetchone()[0]} customers "
          f"(capped from {N_CUSTOMERS}), {con.execute('SELECT COUNT(*) FROM orders').fetchone()[0]} orders")
    print(f"orphans the cap invented: {orphans} (the source had 0)")

    onto = _ontology()
    violations = onto.verify(con)
    print(f"\nverify() -> {len(violations)} violation(s): "
          f"{[(v.check, v.subject) for v in violations] or 'none'}")

    compiled = onto.compile("revenue", group_by=[("customer", "region")])
    if not compiled:
        print(f"compile() refused: {compiled.reason} - {compiled.detail}")
        return 0
    print("notes:", *(f"\n  - {n}" for n in compiled.notes))

    answered = {r[0]: float(r[1]) for r in con.execute(compiled.sql).fetchall()}
    truth = {
        r[0]: float(r[1])
        for r in con.execute(
            "SELECT c.region, SUM(o.amount) FROM src_orders o "
            "JOIN src_customers c USING (cust_ref) GROUP BY c.region"
        ).fetchall()
    }

    print(f"\n{'region':<10}{'answered':>12}{'source truth':>16}{'':>4}")
    wrong = []
    for region in sorted(set(truth) | {k for k in answered if k is not None}):
        got, want = answered.get(region, 0.0), truth.get(region, 0.0)
        flag = "" if got == want else "  <-- WRONG"
        if got != want:
            wrong.append(region)
        print(f"{region:<10}{got:>12,.2f}{want:>16,.2f}{flag}")
    if None in answered:
        print(f"{'NULL':<10}{answered[None]:>12,.2f}{'-':>16}   <-- orphans, in no region")

    print(f"\ntotal answered {sum(answered.values()):,.2f} == source total "
          f"{sum(truth.values()):,.2f}: {sum(answered.values()) == sum(truth.values())}"
          "  (LEFT JOIN protects the total; it does not protect the parts)")

    silent = not violations
    if silent and wrong:
        print(f"\nREPRODUCED: verify() said nothing, and {len(wrong)} of "
              f"{len(truth)} regions are understated. Nothing in the envelope "
              "would tell the customer which.")
        return 1
    if not silent:
        print("\nverify() now reports something - check whether it names the orphans.")
    elif not wrong:
        print("\nGrouped numbers match the source; this shape no longer reproduces.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
