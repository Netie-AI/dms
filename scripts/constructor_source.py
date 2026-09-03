"""Pull Constructor-generated ontology over HTTP. Never merge into ontology.py.

Constructor is a Cortex consumer skin. DMS may ingest its catalog as a source
document (objects/actions/fetch_places) the same way it ingests Excel: stage
bytes, then a human/steward decides. This module does not compile measures and
does not import CortexOS.

Foundry CLI / Marketplace dumps are refused (P-DMS-31).

    python scripts/constructor_source.py --self-check
    python scripts/constructor_source.py --fixture-ask
    python scripts/constructor_source.py --url http://127.0.0.1:8012
    python scripts/constructor_source.py --url http://127.0.0.1:8012 --ingest
    python scripts/constructor_source.py --url http://127.0.0.1:8012 --ask
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
STAGE_DIR = ROOT / ".tmp"

# Shape of GET /cortex/constructor/ontology (packs.dms.constructor_fetch.catalog).
FIXTURE_CATALOG: dict[str, Any] = {
    "ok": True,
    "objects": {
        "inventory": {
            "points": {"sku": "string", "qty": "number"},
            "primary_key": "sku",
            "description": "On-hand stock",
        },
        "shipments": {
            "points": {"shipment_id": "string", "status": "string"},
            "primary_key": "shipment_id",
            "description": "In-transit lots",
        },
    },
    "actions": ["export_pptx"],
    "action_meta": [{"id": "export_pptx", "kind": "read"}],
    "tiers": ["T0", "T1"],
    "fetch_places": ["warehouse.inventory", "warehouse.shipments"],
}

# Constructor object -> existing certified question. Not compiled SQL.
OBJECT_ASKS = {
    "inventory": "What is total stock value by category?",
    "suppliers": "What is our total spend by supplier country?",
    "locations": "Show warehouse capacity utilisation",
    "shipments": "Show shipment cost by destination",
    "transactions": "Top 5 selling SKUs by revenue",
    "alerts": "List active alerts across the warehouse network",
}

_FOUNDRY_MARKERS = ("foundryCli", "osdk", "marketplace.palantir", "OntologyManager")


class ConstructorSourceError(ValueError):
    """Catalog is missing, malformed, or a clone dump we refuse."""


def validate_catalog(data: dict[str, Any]) -> dict[str, Any]:
    blob = json.dumps(data)
    for marker in _FOUNDRY_MARKERS:
        if marker.lower() in blob.lower():
            raise ConstructorSourceError(
                f"Foundry clone dump refused ({marker}). Distill objects+links, do not clone CLI."
            )
    objects = data.get("objects")
    if not isinstance(objects, dict) or not objects:
        raise ConstructorSourceError("constructor catalog has no objects")
    for name, spec in objects.items():
        if not isinstance(name, str) or not name.strip():
            raise ConstructorSourceError("object id must be a non-empty string")
        if not isinstance(spec, dict):
            raise ConstructorSourceError(f"object {name!r} must be an object spec")
    return data


def fetch_catalog(base_url: str, *, token: str | None = None, timeout: float = 5.0) -> dict[str, Any]:
    """HTTP only. Token is X-API-Key when Cortex requires it."""
    import httpx

    url = base_url.rstrip("/") + "/cortex/constructor/ontology"
    headers = {}
    if token:
        headers["X-API-Key"] = token
    with httpx.Client(timeout=timeout) as client:
        resp = client.get(url, headers=headers)
        resp.raise_for_status()
        data = resp.json()
    if not isinstance(data, dict):
        raise ConstructorSourceError("constructor ontology is not an object")
    data.setdefault("ok", True)
    return validate_catalog(data)


def stage_catalog(data: dict[str, Any], dest: Path | None = None) -> Path:
    dest = dest or (STAGE_DIR / "constructor_ontology_staging.json")
    dest.parent.mkdir(parents=True, exist_ok=True)
    payload = validate_catalog(data)
    dest.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return dest


def catalog_as_ingest_plan(catalog: dict[str, Any]) -> dict[str, Any]:
    """Steward-facing table list. Not a grain compile. Not scripts/ontology.py."""
    payload = validate_catalog(catalog)
    tables: list[dict[str, Any]] = []
    for name, spec in payload["objects"].items():
        points = spec.get("points") if isinstance(spec.get("points"), dict) else {}
        tables.append(
            {
                "name": name,
                "primary_key": spec.get("primary_key"),
                "columns": list(points.keys()),
                "description": spec.get("description"),
            }
        )
    return {
        "source": "constructor",
        "objects": len(tables),
        "tables": tables,
        "actions": list(payload.get("actions") or []),
        "fetch_places": list(payload.get("fetch_places") or []),
    }


def pick_space_for_object(name: str, spaces: list[dict[str, Any]]) -> str | None:
    """First Space whose warehouse tables include ``name``. Ungranted -> None."""
    key = str(name or "").strip().lower()
    if not key:
        return None
    for space in spaces:
        tables = space.get("tables") or []
        for raw in tables:
            label = raw if isinstance(raw, str) else str(raw.get("table") or "")
            if label.strip().lower() == key:
                sid = str(space.get("id") or "").strip()
                return sid or None
    return None


def discover_space_tables(dms_url: str, *, timeout: float = 10.0) -> list[dict[str, Any]]:
    """Read grantable warehouse tables per Space. HTTP only; no CortexOS."""
    import httpx

    out: list[dict[str, Any]] = []
    with httpx.Client(timeout=timeout) as client:
        resp = client.get(dms_url.rstrip("/") + "/v1/spaces")
        resp.raise_for_status()
        body = resp.json()
        spaces = body.get("spaces") if isinstance(body, dict) else body
        for space in spaces or []:
            if not isinstance(space, dict):
                continue
            sid = space.get("id")
            if not sid:
                continue
            tr = client.get(
                dms_url.rstrip("/") + "/v1/library/warehouse/tables",
                params={"space_id": sid},
            )
            tr.raise_for_status()
            tables = tr.json()
            names: list[str] = []
            if isinstance(tables, list):
                for item in tables:
                    if isinstance(item, dict) and item.get("table"):
                        names.append(str(item["table"]))
            out.append({"id": str(sid), "name": space.get("name"), "tables": names})
    return out


def catalog_as_ask_plan(
    catalog: dict[str, Any],
    *,
    spaces: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Map catalog objects to certified questions. Does not generate SQL.

    When ``spaces`` is provided, skip objects no Space grants (alerts) and
    stamp the Space that can actually ask. Without ``spaces``, keep every
    mapped object (self-check / offline).
    """
    plan = catalog_as_ingest_plan(catalog)
    asks: list[dict[str, Any]] = []
    unknown: list[str] = []
    ungranted: list[str] = []
    for table in plan["tables"]:
        name = table.get("name")
        if name not in OBJECT_ASKS:
            unknown.append(str(name))
            continue
        space_id = pick_space_for_object(str(name), spaces) if spaces is not None else None
        if spaces is not None and space_id is None:
            ungranted.append(str(name))
            continue
        asks.append(
            {
                "object": name,
                "question": OBJECT_ASKS[name],
                "space_id": space_id,
            }
        )
    return {
        "source": "constructor",
        "asks": asks,
        "unknown_objects": unknown,
        "ungranted": ungranted,
    }


def ingest_plan_to_dms(
    plan: dict[str, Any],
    *,
    dms_url: str,
    space_id: str,
    timeout: float = 60.0,
) -> dict[str, Any]:
    """POST the plan as a tabular source. JSON stays staged; CSV is what bronze can hold."""
    import csv
    import httpx
    import io

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["object", "primary_key", "columns", "description"])
    for table in plan["tables"]:
        writer.writerow(
            [
                table.get("name") or "",
                table.get("primary_key") or "",
                ",".join(table.get("columns") or []),
                table.get("description") or "",
            ]
        )
    files = [
        (
            "files",
            ("constructor_objects.csv", buf.getvalue().encode("utf-8"), "text/csv"),
        )
    ]
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(
            dms_url.rstrip("/") + "/v1/studio/ingest-batch",
            files=files,
            data={"space_id": space_id},
        )
        resp.raise_for_status()
        receipt = resp.json()
    if not isinstance(receipt, dict):
        raise ConstructorSourceError("ingest receipt is not an object")
    return receipt


def _ask_plan_live(
    ask_plan: dict[str, Any],
    *,
    dms_url: str,
    space_id: str,
    timeout: float = 60.0,
) -> int:
    """Ask certified questions named by the catalog. Cortex still owns SQL."""
    import httpx

    n_ok = 0
    n_abs = 0
    with httpx.Client(timeout=timeout) as client:
        for item in ask_plan["asks"]:
            try:
                sid = item.get("space_id") or space_id
                resp = client.post(
                    dms_url.rstrip("/") + "/v1/chat/ask",
                    json={"question": item["question"], "space_id": sid},
                )
                resp.raise_for_status()
                env = resp.json()
            except Exception as exc:  # noqa: BLE001
                print(f"{item['object']}\tERROR\t{type(exc).__name__}: {exc}")
                return 5
            badge = env.get("badge") if isinstance(env, dict) else None
            abstained = bool(isinstance(env, dict) and env.get("abstained"))
            rows = (env.get("rows") or []) if isinstance(env, dict) else []
            if abstained or badge == "ABSTAIN":
                n_abs += 1
            else:
                n_ok += 1
            print(
                f"{item['object']}\t{badge}\tabstain={abstained}\t"
                f"rows={len(rows)}\tspace={sid}\tq={item['question']}"
            )
    print(
        f"constructor-ask answered={n_ok} abstain={n_abs} "
        f"unknown={ask_plan.get('unknown_objects')} "
        f"ungranted={ask_plan.get('ungranted')}"
    )
    return 0


def self_check() -> int:
    staged = stage_catalog(FIXTURE_CATALOG)
    loaded = json.loads(staged.read_text(encoding="utf-8"))
    assert loaded["objects"]["inventory"]["primary_key"] == "sku"
    try:
        validate_catalog({"ok": True, "objects": {"x": {}}, "foundryCli": True})
        print("FAIL: Foundry marker was accepted")
        return 1
    except ConstructorSourceError:
        pass
    plan = catalog_as_ingest_plan(FIXTURE_CATALOG)
    assert plan["source"] == "constructor"
    assert {t["name"] for t in plan["tables"]} == {"inventory", "shipments"}
    asks = catalog_as_ask_plan(FIXTURE_CATALOG)
    assert {a["object"] for a in asks["asks"]} == {"inventory", "shipments"}
    routed = catalog_as_ask_plan(
        FIXTURE_CATALOG,
        spaces=[
            {"id": "fin", "tables": ["inventory"]},
            {"id": "ops", "tables": ["shipments"]},
        ],
    )
    by_obj = {a["object"]: a["space_id"] for a in routed["asks"]}
    assert by_obj["inventory"] == "fin"
    assert by_obj["shipments"] == "ops"
    print(
        f"PASS: staged {staged} ({len(loaded['objects'])} objects). "
        f"Ingest plan {plan['objects']} tables. Ask plan {len(asks['asks'])}. "
        "Foundry dump refused. Space routing ok."
    )
    return 0


def main(argv: list[str]) -> int:
    if "--self-check" in argv:
        return self_check()
    if "--fixture-ask" in argv:
        dms = os.environ.get("DMS_URL", "http://127.0.0.1:8090")
        space = os.environ.get("DMS_SPACE_ID", "cccccccc-cccc-cccc-cccc-cccccccccccc")
        try:
            spaces = discover_space_tables(dms)
        except Exception as exc:  # noqa: BLE001
            print(f"SPACE_DISCOVER_MISS: {type(exc).__name__}: {exc}")
            spaces = None
        ask_plan = catalog_as_ask_plan(FIXTURE_CATALOG, spaces=spaces)
        ask_path = STAGE_DIR / "constructor_ask_plan.json"
        ask_path.write_text(json.dumps(ask_plan, indent=2) + "\n", encoding="utf-8")
        print(f"fixture-ask plan {ask_path} n={len(ask_plan['asks'])}")
        return _ask_plan_live(ask_plan, dms_url=dms, space_id=space)
    if "--foundry" in argv:
        print("REFUSE: Foundry CLI/Marketplace clone is P-DMS-31. Use Constructor HTTP catalog.")
        return 2
    url = os.environ.get("CORTEX_URL", "http://127.0.0.1:8012")
    if "--url" in argv:
        i = argv.index("--url")
        url = argv[i + 1]
    token = os.environ.get("CORTEX_API_KEY")
    want_ingest = "--ingest" in argv
    want_ask = "--ask" in argv
    try:
        catalog = fetch_catalog(url, token=token)
    except Exception as exc:  # noqa: BLE001
        print(f"LIVE_MISS: {type(exc).__name__}: {exc}")
        print("Use --self-check when Cortex/Constructor is down.")
        return 3
    path = stage_catalog(catalog)
    plan = catalog_as_ingest_plan(catalog)
    plan_path = STAGE_DIR / "constructor_ingest_plan.json"
    plan_path.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
    print(f"staged {path} objects={list(catalog['objects'])}")
    print(f"ingest plan {plan_path} tables={plan['objects']}")
    if want_ask:
        dms = os.environ.get("DMS_URL", "http://127.0.0.1:8090")
        space = os.environ.get("DMS_SPACE_ID", "cccccccc-cccc-cccc-cccc-cccccccccccc")
        try:
            spaces = discover_space_tables(dms)
        except Exception as exc:  # noqa: BLE001
            print(f"SPACE_DISCOVER_MISS: {type(exc).__name__}: {exc}")
            spaces = None
        ask_plan = catalog_as_ask_plan(catalog, spaces=spaces)
        ask_path = STAGE_DIR / "constructor_ask_plan.json"
        ask_path.write_text(json.dumps(ask_plan, indent=2) + "\n", encoding="utf-8")
        print(f"ask plan {ask_path} n={len(ask_plan['asks'])}")
        return _ask_plan_live(ask_plan, dms_url=dms, space_id=space)
    if not want_ingest:
        return 0
    dms = os.environ.get("DMS_URL", "http://127.0.0.1:8090")
    space = os.environ.get("DMS_SPACE_ID", "cccccccc-cccc-cccc-cccc-cccccccccccc")
    try:
        receipt = ingest_plan_to_dms(plan, dms_url=dms, space_id=space)
    except Exception as exc:  # noqa: BLE001
        print(f"INGEST_MISS: {type(exc).__name__}: {exc}")
        return 4
    print(
        f"ingest files_seen={receipt.get('files_seen')} "
        f"ingested={receipt.get('ingested')} "
        f"need_attention={receipt.get('need_attention')} "
        f"table={(receipt.get('files') or [{}])[0].get('table')} "
        f"summary={receipt.get('summary')}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
