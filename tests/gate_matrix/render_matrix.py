"""Render docs/subagents_findings/2026-10-05_gate-matrix-plan-e.md from the audit's JSON data.

Not a test (no test_ prefix). Run from the repo root:

    python tests/gate_matrix/render_matrix.py [out.md]

Inputs, all under docs/subagents_findings/:
  gate-matrix-entry-points.json   49 entry points (merged list + critic notes)
  gate-matrix-trace.json          one row per entry point, 8 gate cells with file:line evidence
  gate-matrix-review*.json        blind re-trace results ({"reviews": [...]}); several files allowed
  gate-matrix-review-sample.json  seed and sample of the first review pass
  gate-matrix-gaps.json           root-cause gaps over the no/partial cells
  gate-matrix-tests.json          test outcomes per gap
  gate-matrix-nodes.txt           optional: pytest --collect-only -q output, to drop stale node ids

A tracer "yes" counts as passing only when a blind reviewer also said yes. The document is generated,
so fix the JSON or this script, never the markdown.
"""

from __future__ import annotations

import glob
import json
import sys
from collections import Counter
from pathlib import Path

D = Path(__file__).resolve().parents[2] / "docs" / "subagents_findings"
GATES = ["G1", "G2", "G3", "G4", "G5", "G6", "G7", "G8"]
NAMES = {
    "G1": "grant / Space check",
    "G2": "ontology verification",
    "G3": "join rule + fan-out guard",
    "G4": "typed ingest",
    "G5": "PII mask (envelope + record)",
    "G6": "strict pin + served fields",
    "G7": "Cortex submit (manifest + ledger)",
    "G8": "named abstain on failure",
}
TOK = {"yes": "yes", "disputed": "Y?", "yes_unreviewed": "yes(1x)", "partial": "part", "no": "**NO**", "n_a": "n/a"}

PRD_QUESTIONS = [
    "A-0007 closed the Space-less read on the Library routes, but the ask path still resolves a request with no `space_id` to every demo "
    "table (including `alerts`, which no Space grants) plus every Space's uploads (`no-space-ask-widest-grant`). Is that intended, "
    "or should it refuse `no_space` like the bronze lane?",
    "FOLLOWUP-CONTRACT-01 as ruled covers the missing submit and the shared grant function. The matrix finds three more follow-up "
    "defects it does not name: the turn cache is keyed only by caller-supplied `session_id` (no principal, DR-0004), the answer is "
    "relabelled RM whatever the unit, and a failed turn can leave a stale prior figure. Do they belong under that ruling?",
    "Bronze lane: a table whose name is also a `warehouse_`-prefixed alias is treated as granted by `table_is_granted` "
    "(`bronze-grant-warehouse-alias-collision`, test in `test_gap_grants.py`), and the lane mints `L0_CERTIFIED` locally with no "
    "Cortex submit or ledger entry (hard rule 3). Does either belong under BRONZE-GRANT-01 / BRONZE-WAREHOUSE-01?",
    "Hard rule 11: `DMS_ASK_MODE=demo` and the demo fallback paths answer with demo numbers and no Space grant; the demo-mode "
    "path sets no banner. With `DMS_DEMO_FALLBACK=1`, three 403 policy codes (`manifest_malformed`, `sql_not_analyzable`, "
    "`manifest_not_yet_valid`) and a refused verified-query submit come back as demo numbers instead of an error.",
    "Masking: previews, drillthrough, chunk search, `POST /v1/insights`, the audit receipt and the ledger SQL payload skip the "
    "personal-data masker. Previews exist so a steward can see raw rows; is that exemption intended, and does it extend to the others?",
    "G2 as written asks for ontology verification at serving time. Stored (verified-query / pack) SQL is certified by a person and "
    "never re-verified; the generative lane can ship over a failed-verify ontology (#258). Which is the authority?",
    "Zero-row guard: rule 12's net fires on an empty set, but an aggregate that returns one row of NULL or 0 ships green "
    "(`rule12-aggregate-zero-row-green`).",
    "G6 has no serving-time check of served fields: the only checker is in the offline scorer (#337 touches it). Is a runtime check wanted?",
]


def load(name: str):
    return json.loads((D / name).read_text(encoding="utf-8"))


def esc(s: object, n: int = 0) -> str:
    t = str(s or "").replace("|", "\\|").replace("\n", " ").replace("\r", " ").strip()
    return t[: n - 1] + "..." if n and len(t) > n else t


def anchor(rid: str) -> str:
    return "ep-" + rid.lower().replace(":", "-").replace("_", "-")


def main(out: str) -> None:
    ep_data = load("gate-matrix-entry-points.json")["merged"]
    eps = {e["id"]: e for e in ep_data["entry_points"]}
    order = list(eps)
    rows = {r["id"]: r for r in load("gate-matrix-trace.json")["rows"]}
    gaps = load("gate-matrix-gaps.json")
    tests = load("gate-matrix-tests.json")
    sample1 = load("gate-matrix-review-sample.json")
    reviewed: dict[tuple[str, str], str] = {}
    review_files = sorted(glob.glob(str(D / "gate-matrix-review*.json")))
    for f in review_files:
        if f.endswith("review-sample.json"):
            continue
        for rv in json.loads(Path(f).read_text(encoding="utf-8")).get("reviews", []):
            for c in rv["cells"]:
                reviewed[(rv["id"], c["gate"])] = c["verdict"]

    nodes_file = D / "gate-matrix-nodes.txt"
    live_nodes = set(nodes_file.read_text(encoding="utf-8").split()) if nodes_file.exists() else None

    def status(rid: str, g: str) -> str:
        v = rows[rid]["cells"][g]["verdict"]
        if v != "yes":
            return v
        r = reviewed.get((rid, g))
        if r is None:
            return "yes_unreviewed"
        return "yes" if r == "yes" else "disputed"

    cell_gap: dict[str, str] = {}
    for gp in gaps["gaps"]:
        for c in gp["cells"]:
            cell_gap[c] = gp["gap_id"]
    outcome = {}
    for w in tests["written"]:
        for g in w["gaps"]:
            nodes = [n for n in g["test_node_ids"] if live_nodes is None or n in live_nodes]
            outcome[g["gap_id"]] = (g["outcome"], nodes, g["user_sees"])

    st = Counter(status(r, g) for r in order for g in GATES)
    n_cells = len(order) * len(GATES)
    L: list[str] = []
    w = L.append

    w("# Gate matrix - does every answer path pass every gate?")
    w("")
    w("Plan E, a read-only audit of `netie/dms` at `7a8d6c1` (origin/main on 2026-10-05, BRONZE-GRANT-01 #333 and "
      "BRONZE-WAREHOUSE-01 #335 merged). **Generated** by `tests/gate_matrix/render_matrix.py` from the JSON beside it; "
      "do not hand-edit. Feedback for the PRD, not a fix and not a ticket: no product code was changed.")
    w("")
    w("## Read this first")
    w("")
    w("- **No entry point passes all 8 gates.** This describes the code, not an accuracy figure.")
    w("- **The gate definitions are strict and mine.** Part of the partial/no count comes from the definition "
      "(for example G6 asks for a serving-time check of served fields, which exists only in the offline scorer). "
      "Class `bypass` in the gap table is the provable set: a request on default or documented config that a test shows.")
    w("- **A tracer's yes counts only if a blind reviewer agreed.** Cells where they differ are shown `Y?` and treated as not passing.")
    w("- **Everything is from reading code at one commit, plus the tests.** No live lane, no real model, no Cortex "
      "enforcement was exercised. Test fakes stand in for Cortex.")
    w("")
    w("## Outcome counts")
    w("")
    w(f"- Entry points n = {len(order)} (two blind enumerators found 43 and 39, a critic merged them and added 3). All {len(rows)} traced.")
    w(f"- Cells n = {n_cells} = {len(order)} x 8: yes confirmed {st['yes']}, yes disputed by review {st['disputed']}, "
      f"yes unreviewed {st['yes_unreviewed']}, partial {st['partial']}, no {st['no']}, n/a {st['n_a']}.")
    w("")
    w("| Gate | yes (confirmed) | Y? (disputed) | partial | no | n/a |")
    w("|---|---|---|---|---|---|")
    for g in GATES:
        c = Counter(status(r, g) for r in order)
        w(f"| {g} {NAMES[g]} | {c['yes'] + c['yes_unreviewed']} | {c['disputed']} | {c['partial']} | {c['no']} | {c['n_a']} |")
    w("")

    # review summary
    y_n = y_ok = d_n = d_ok = 0
    over: list[str] = []
    s1 = {tuple(x) for x in sample1["sample"]}
    for (rid, g), rv in reviewed.items():
        orig = rows[rid]["cells"][g]["verdict"]
        if orig == "yes":
            y_n += 1
            y_ok += rv == "yes"
            if rv != "yes":
                over.append(f"{rid} {g} (reviewer: {rv})")
        else:
            d_n += 1
            d_ok += rv == orig
    w("## Independent review of the yes cells")
    w("")
    w(f"- Every tracer yes was re-traced blind (the reviewer saw no earlier verdict or evidence, and each reviewer also "
      f"judged unlabeled non-yes cells from the same row). Pass 1 was a seeded 30% sample (seed {sample1['seed']}, "
      f"{len(s1)} of {sample1['n_yes']} yes cells). Pass 2 covered the remaining yes cells.")
    w(f"- Yes cells re-judged: n = {y_n} of {sample1['n_yes']}. Reviewer agreed {y_ok}, overturned {y_n - y_ok}. "
      f"Agreement {y_ok}/{y_n}.")
    w(f"- Unlabeled non-yes cells: n = {d_n}, reviewer reached the same verdict on {d_ok}.")
    w("- Overturned (tracer said yes): " + "; ".join(sorted(over)) + ".")
    w(f"- Reviewers agreed with {y_ok} of {y_n} tracer yes cells ({100 * y_ok // y_n}%) but with {d_ok} of {d_n} "
      f"non-yes cells ({100 * d_ok // d_n}%). A tracer yes is therefore weaker evidence than a tracer partial or no: "
      "false passes were the common mistake. These are two model reviews of the same code, not a measured error rate; "
      "the 11 overturned cells have not had a third opinion.")
    w("")

    w("## Matrix")
    w("")
    w("`yes` = tracer and blind reviewer agree. `Y?` = tracer said yes, reviewer disagreed (not counted as passing). "
      "`part` = partial. `**NO**` = gate absent on that path. `n/a` = gate cannot apply (reason cited in the section below). "
      "Each row links to its cited cells.")
    w("")
    w("| Entry point | " + " | ".join(GATES) + " |")
    w("|---|" + "---|" * 8)
    for rid in order:
        w(f"| [`{rid}`](#{anchor(rid)}) | " + " | ".join(TOK[status(rid, g)] for g in GATES) + " |")
    w("")

    w("## Gaps and their tests")
    w("")
    cls = Counter(gp["class"] for gp in gaps["gaps"])
    w(f"The {gaps['coverage']['no_cells'] + gaps['coverage']['partial_cells']} no/partial cells reduce to "
      f"{len(gaps['gaps'])} root-cause gaps ({', '.join(f'{k} {v}' for k, v in cls.items())}), 0 unassigned. "
      "`bypass` = a request can show it; `systemic` = the gate exists only in the offline scorer or the definition is "
      "stricter than the design; `dormant` = needs a non-default flag or is dead code. Tests are in "
      "`tests/gate_matrix/` as strict xfails: the gap assertion failing is an expected failure, a fixed gap becomes a loud "
      "XPASS, anything else is a hard failure. Run `python -m pytest tests/gate_matrix -p no:cacheprovider --runxfail` "
      "to see the real failures.")
    w("")
    w("| Gap | Class | Tracked by | Cells | Test result | Severity hint |")
    w("|---|---|---|---|---|---|")
    order_cls = {"bypass": 0, "systemic": 1, "dormant": 2}
    for gp in sorted(gaps["gaps"], key=lambda x: (order_cls.get(x["class"], 9), -len(x["cells"]))):
        oc = outcome.get(gp["gap_id"])
        if oc and oc[0] == "demonstrated":
            res = f"demonstrated ({len(oc[1])} test{'s' if len(oc[1]) != 1 else ''})"
        elif oc:
            res = oc[0]
        else:
            if str(gp.get("testable_in_process")).lower() == "false":
                res = "unproven, no in-process test: " + esc(gp.get("testable_note"), 70)
            else:
                res = "no test written (class " + gp["class"] + ", no runtime trigger a request can show)"
        w(f"| `{gp['gap_id']}` | {gp['class']} | {esc(gp['tracked_by'], 60)} | {len(gp['cells'])} | {res} | {esc(gp.get('severity_hint'), 90)} |")
    w("")
    w("### Bypass gaps in detail")
    w("")
    for gp in gaps["gaps"]:
        if gp["class"] != "bypass":
            continue
        oc = outcome.get(gp["gap_id"])
        w(f"**`{gp['gap_id']}`** - {esc(gp['title'])}")
        w("")
        w(f"- Cells: {', '.join(gp['cells'])}")
        w(f"- Tracked by: {esc(gp['tracked_by'])}. Severity hint: {esc(gp.get('severity_hint'))}")
        w(f"- Repro: {esc(gp.get('repro'))}")
        if oc and oc[1]:
            w(f"- What the user gets (from the test run): {esc(oc[2])}")
            w("- Test: " + "; ".join(f"`{n}`" for n in oc[1]))
        w("")

    w("## Entry points, cell by cell")
    w("")
    w("Each line: gate, status, the first two citations (`path:line`), and the gap it belongs to. Full evidence, "
      "reasoning and bypass scenarios for every cell are in `gate-matrix-trace.json`.")
    w("")
    for rid in order:
        e = eps[rid]
        r = rows[rid]
        w(f'<a id="{anchor(rid)}"></a>')
        w(f"### `{rid}`")
        w("")
        w(f"{esc(e['name'])}. Producer: {esc(e['producer'], 200)}. Reachable: {esc(r['reachable'], 220)}")
        w("")
        w("| Gate | Status | Citations | Gap |")
        w("|---|---|---|---|")
        for g in GATES:
            c = r["cells"][g]
            cites = "<br>".join(f"`{esc(x, 150)}`" for x in c["evidence"][:2])
            gp = cell_gap.get(f"{rid} {g}", "")
            w(f"| {g} {NAMES[g]} | {TOK[status(rid, g)]} ({c['confidence']}) | {cites} | {('`' + gp + '`') if gp else ''} |")
        for f in r.get("other_findings", [])[:3]:
            w("")
            w(f"Also on this path: {esc(f, 300)}")
        w("")

    w("## For the PRD (questions, not rulings; no ticket is minted from this)")
    w("")
    for q in PRD_QUESTIONS:
        w(f"- {q}")
    w("")
    w("### Related open work, not touched here")
    w("")
    w("- #338 (draft, another session) is a different audit, Plan C: 338 adversarial SQL cases through the gate with strict-xfail "
      "tests in `tests/redteam/`. Its findings and this matrix overlap on L2 SQL (fan-out, zero-row); no file overlaps.")
    w("- #331 GRANT-READ-02 would close part of `ungranted-tick-dropped-not-refused`; #336 SERVING-PRECHECK-01 and #337 "
      "PIN-NOMODEL-01 touch the scorer side of G6. None is on main, so none turns a cell into yes here.")
    w("")
    w("## Tracer contradictions to re-check")
    w("")
    for i, c in enumerate(gaps["contradictions"], 1):
        w(f"{i}. {esc(c['disagreement'], 500)} (cells: {esc(', '.join(c['cells']), 200)}; shared code: {esc(c['shared_code'], 160)})")
    w("")
    w("## Off-matrix findings from the tracers")
    w("")
    for n in gaps.get("notes", []):
        w(f"- {esc(n, 600)}")
    w("")
    Path(out).write_text("\n".join(L) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {out}: {len(rows)} rows, {n_cells} cells, {dict(st)}; review yes {y_ok}/{y_n}; decoys {d_ok}/{d_n}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else str(D / "2026-10-05_gate-matrix-plan-e.md"))
