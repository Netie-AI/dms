"""CLI entry for the semantic layer. The layer itself is ``dms_executor.ontology``.

    python scripts/ontology.py --demo             # build and verify on the demo warehouse
    python scripts/ontology.py --adventureworks   # derive from the extracted lake
    python scripts/ontology.py --describe         # print the ontology as json

Why the split (SQLSRC-08, Netie-AI/dms#156): the layer used to live here, and
`packages/` cannot import from `scripts/`. So `verify()` - the function EPIC-020
clause 2 promises will "refuse, naming the link, any link the data violates" -
was reachable only from bench scripts, and never ran against a customer extract.
It also calls `duckdb.execute`, which hard rule 7 permits only inside
`packages/executor`. Both problems have the same fix, and this file is what is
left: an argv parser, so the bench and demo invocations keep working unchanged.

Import the layer from `dms_executor.ontology`, never from here.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "executor"))

from dms_executor.ontology import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
