"""FDE-ONBOARD-01 (dms#541): onboard a customer's files into a Space, draft its configuration.

    python scripts/fde_onboard.py --source <dir> --space <space id> --out <pack> [options]

Stages run in order. Each writes its file(s) into the pack directory and is checked before the
next. Stages 1-4 need no model and no network. Stage 5 calls the model through OpenVault only
(``ao_vendor.llm.ov_client``, role alias ``ontology``, no model id), and only after a person
has read the files listed in ``send_manifest.json`` and named its sha256 with
``--confirm-pack``. Stage 6 asks each kept question through ``POST /v1/chat/ask`` on the
DMS app, in this process. The Space must already exist: the CLI creates none.

Stage 1 lands the files in bronze directly, as an operator-run extract (the same kind of
write as ``scripts/load_adventureworks.py``): it is not a FastAPI mutation route, so rule 8
does not reach it. Every item-store write goes through a gated route instead, and only with
``--import``: ``POST /v1/ontology/data/profile`` and ``POST /v1/ontology/data/spaces/{id}/onboard``
(steward grant, then Cortex F5, then the store). Without ``--import`` nothing is stored.

The pack directory is what may leave the machine. It holds names, landed types, counts, shapes
and PII tokens. No cell value. Raw values (value maps, SQL results) stay in the local item
store and in this process. Every configuration item is stored ``proposed``. Nothing here
approves or applies one: a steward does that, item by item.

Exit codes: 0 every requested stage done; 2 usage or input; 3 a stage refused with a named
reason (OpenVault or Cortex unreachable, key missing, model output invalid); 4 the leak check
failed (pack files removed, ``LEAK_REFUSED.json`` holds counts only); 5 a prompt file drifted
from its pinned sha256; 6 an unexpected error, named by class only (the pack is still checked,
and removed if the check cannot pass); 2 also when the actor holds no steward grant.
stdout is one JSON object of codes and counts.

Swap: none. Reuses the bronze ingest, the #514 profiler, the PII masker, the item store, the
OpenVault client and the ask route. No new port, dependency or config key (rule 6).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import uuid
from collections import Counter
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for _p in (
    ROOT / "apps" / "api",
    ROOT / "packages" / "core",
    ROOT / "packages" / "executor",
    ROOT / "packages" / "cortex_client",
    ROOT / "packages" / "ledger",
    ROOT / "packages" / "ao_vendor",
):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

TOOL = "fde_onboard/1"
EXIT_OK, EXIT_INPUT, EXIT_REFUSED, EXIT_LEAK, EXIT_DRIFT, EXIT_INTERNAL = 0, 2, 3, 4, 5, 6
STAGE_NAMES = {
    1: "ingest",
    2: "profile",
    3: "type_and_clean_proposals",
    4: "masked_pack",
    5: "fixed_prompts",
    6: "measure",
}
LEAK_FILE = "LEAK_REFUSED.json"
#: The onboard route takes at most 500 items per request.
IMPORT_BATCH = 500
#: What each fixed prompt is sent with: user-message key -> pack file. Nothing else is
#: sent; send_manifest.json records it with the sha256 of each file and prompt.
SEND_CALLS: tuple[tuple[str, dict[str, str]], ...] = (
    (
        "01_ontology_draft.md",
        {"schema": "schema.json", "profile": "profile.json", "samples": "samples.masked.json"},
    ),
    (
        "02_verified_questions.md",
        {"schema": "schema.json", "profile": "profile.json", "samples": "samples.masked.json"},
    ),
    (
        "03_value_mappings.md",
        {"schema": "schema.json", "value_groups": "proposals/value_groups.json"},
    ),
)


class _Refuse(Exception):
    def __init__(self, code: int, reason: str) -> None:
        self.code = code
        self.reason = reason
        super().__init__(reason)


def _stage_rows() -> list[dict[str, Any]]:
    return [
        {"stage": n, "name": name, "status": "not_reached", "reason": None, "counts": {}}
        for n, name in STAGE_NAMES.items()
    ]


def _cell(value: Any) -> tuple[str, str]:
    """A cell with its type class, so ``'3'`` and ``3``, or ``True`` and ``'True'``, differ.

    Numbers compare by value (``3``, ``3.0`` and ``Decimal('3.00')`` are one number). A date
    is its ISO text, because the JSON envelope can only carry it as text.
    """
    if value is None:
        return ("null", "")
    if isinstance(value, bool):
        return ("bool", str(value))
    if isinstance(value, int | float | Decimal):
        try:
            return ("num", format(Decimal(str(value)).normalize(), "f"))
        except InvalidOperation:
            return ("num", str(value))
    if isinstance(value, date | datetime):
        return ("text", value.isoformat())
    return ("text", str(value))


def _multiset(rows: Sequence[dict[str, Any]]) -> Counter[tuple[tuple[str, str], ...]]:
    """Rows in column order (aliases may differ, positions may not), duplicates kept."""
    return Counter(tuple(_cell(v) for v in row.values()) for row in rows)


def _source_files(source: Path) -> list[Path]:
    """Regular files directly in ``source``. A symlink is skipped: it can point anywhere."""
    root = source.resolve()
    return sorted(
        p
        for p in source.iterdir()
        if not p.is_symlink()
        and p.is_file()
        and not p.name.startswith(".")
        and p.resolve().parent == root
    )


class Onboard:
    """One run. Holds the raw, local-only state between stages."""

    def __init__(
        self,
        args: argparse.Namespace,
        *,
        http: Any = None,
        transport: Callable[..., Any] | None = None,
    ) -> None:
        self.args = args
        self.http = http
        self.transport = transport
        self.out = Path(args.out)
        self.run_id = str(uuid.uuid4())
        self.stages = _stage_rows()
        self.written: list[Path] = []
        self.space_id = ""
        self.store_info: dict[str, Any] = {}
        self.prompts: dict[str, str] = {}
        self.prompt_manifest: dict[str, Any] = {}
        self.leak: dict[str, Any] = {"status": "not_run"}
        self.kept_questions: list[dict[str, Any]] = []
        self.proposed = 0
        self.held = 0
        self.held_approved = 0
        self.sources: dict[str, str] = {}
        self.item_ids: set[str] = set()

    # ------------------------------------------------------------------ helpers

    def _stage(self, n: int) -> dict[str, Any]:
        return self.stages[n - 1]

    def _done(self, n: int, **counts: Any) -> None:
        row = self._stage(n)
        row["status"] = "done"
        row["counts"] = dict(counts)

    def _write(self, rel: str, obj: Any) -> None:
        from dms_executor.fde_pack import write_pack_file

        write_pack_file(self.out, rel, obj)
        path = self.out / rel
        if path not in self.written:
            self.written.append(path)

    def _load(self, rel: str) -> Any:
        return json.loads((self.out / rel).read_text(encoding="utf-8"))

    def _store_items(
        self, items: list[tuple[str, dict[str, Any], dict[str, Any]]], stage: str
    ) -> list[dict[str, Any]]:
        """Pending proposals of one stage. Stored only with ``--import``, and only through
        the gated route (steward grant, then Cortex F5, then the store): rule 8.

        Without ``--import`` nothing is written to the item store; each item comes back
        ``proposed`` with no id, for the pack to list.
        """
        if not self.args.do_import:
            return [
                {"id": None, "status": "proposed", "kind": k, "payload": dict(p)}
                for k, p, _e in items
            ]
        if not items:
            return []
        stored: list[dict[str, Any]] = []
        held: set[str] = set()
        for start in range(0, len(items), IMPORT_BATCH):
            chunk = items[start : start + IMPORT_BATCH]
            r = self.http.post(
                f"/v1/ontology/data/spaces/{self.space_id}/onboard",
                json={
                    "items": [{"kind": k, "payload": p, "evidence": e} for k, p, e in chunk],
                    "run": {"run_id": self.run_id, "stage": stage, "tool": TOOL},
                },
            )
            if r.status_code != 200:
                raise _Refuse(EXIT_REFUSED, f"import_refused:{_detail_code(r)}")
            result = r.json()
            self.store_info["persisted"] = bool(result.get("persisted"))
            stored.extend(result.get("items") or [])
            held.update(result.get("held") or [])
        fresh = [i for i in stored if i.get("id") not in held]
        if len(stored) != len(items) or any(i.get("status") != "proposed" for i in fresh):
            raise _Refuse(EXIT_REFUSED, "store_not_proposed")
        # A re-run keeps what a steward already decided; it never sets a status itself.
        self.proposed += len(fresh)
        self.held += len(stored) - len(fresh)
        self.item_ids.update(str(i["id"]) for i in stored)
        self.held_approved += sum(
            1 for i in stored if i.get("id") in held and i.get("status") == "approved"
        )
        return stored

    def _store_counts(self) -> dict[str, Any]:
        """``approved`` counts only items a steward approved before this run: none is ours."""
        return {
            **self.store_info,
            "proposed": self.proposed,
            "held_from_earlier_runs": self.held,
            "approved": self.held_approved,
        }

    def _settings(self) -> Any:
        from dms_api.settings import get_settings

        return get_settings()

    # ------------------------------------------------------------------ stages

    def stage1(self) -> None:
        from dms_api.wiring import batch_ingest

        files = _source_files(Path(self.args.source))
        if not files:
            raise _Refuse(EXIT_INPUT, "no_source_files")
        receipt = batch_ingest([(p.name, p.read_bytes()) for p in files], space_id=self.space_id)
        ingested = int(receipt.get("ingested") or 0)
        attention = int(receipt.get("need_attention") or 0)
        if ingested == 0:
            self._stage(1)["counts"] = {"files_seen": len(files), "need_attention": attention}
            raise _Refuse(EXIT_INPUT, "nothing_ingested")
        for row in receipt.get("files") or []:
            if row.get("table") and row.get("file"):
                self.sources[str(row["table"])] = str(row["file"])
        self._done(1, files_seen=len(files), ingested=ingested, need_attention=attention)

    def stage2(self) -> None:
        import dms_executor
        from dms_executor.fde_pack import column_stats

        self.profile = dms_executor.profile_space(self.space_id)
        if not self.profile.get("file_tables"):
            raise _Refuse(EXIT_INPUT, "no_file_tables")
        self.stats = column_stats(self.space_id)
        stored_items = 0
        if self.args.do_import:
            # The #514 route: steward grant, Cortex F5, then the store.
            r = self.http.post(f"/v1/ontology/data/profile?space_id={self.space_id}")
            if r.status_code != 200:
                raise _Refuse(EXIT_REFUSED, f"import_refused:{_detail_code(r)}")
            body = r.json()
            self.store_info["profile_persisted"] = bool(body.get("persisted"))
            stored_items = len(body.get("items") or [])
        self._done(
            2,
            tables=len(self.stats),
            keys=len(self.profile.get("keys") or []),
            links=len(self.profile.get("links") or []),
            profile_items=stored_items,
        )

    def stage3(self) -> None:
        from dms_executor.fde_pack import (
            NameMask,
            data_header_tables,
            type_items,
            value_groups,
            value_items,
        )

        types = type_items(self.stats)
        self.groups = value_groups(self.space_id, self.stats)
        values = value_items(self.groups)
        stored = self._store_items([*types, *values], "3")
        # A table whose header row reads as data (a CSV with no header line) would send
        # its first row out as column names: every name of it is tokenised.
        data_headers = data_header_tables(self.stats)
        self.names = NameMask(
            [(t, list(cols)) for t, cols in self.stats.items()],
            sources=self.sources,
            force={c for t in data_headers for c in self.stats[t]},
        )
        self.data_header_tables = len(data_headers)
        n = len(types)
        # Raw rows: build_pack masks every name it writes.
        # The pack rows carry no store id: these files are sent, and what a person confirms
        # in a dry run must be byte for byte what an --import run sends. Ids stay in the store.
        self.type_rows = [
            {"status": "proposed", "kind": i["kind"], **i["payload"]} for i in stored[:n]
        ]
        self.group_rows = [{**group, "status": "proposed"} for group in self.groups]
        self._done(
            3,
            column_types=len(types),
            value_groups=len(self.groups),
            tables_with_data_headers=self.data_header_tables,
        )

    def stage4(self) -> None:
        from dms_executor.fde_pack import build_pack

        built = build_pack(
            self.out,
            space_id=self.space_id,
            space_name=self.args.space,
            profile=self.profile,
            stats=self.stats,
            names=self.names,
            type_rows=self.type_rows,
            group_rows=self.group_rows,
        )
        for rel in built.get("files") or []:
            path = self.out / rel
            if path not in self.written:
                self.written.append(path)
        self._write("send_manifest.json", self._send_manifest())
        self._done(4, files=len(built.get("files") or []) + 1)
        self.finalize()  # the gate before anything is sent

    def _send_manifest(self) -> dict[str, Any]:
        """Every byte stage 5 will send: the files of each call and the prompt text.

        A person reads these files and confirms the sha256 of this manifest with
        ``--confirm-pack``. Stage 5 then sends only bytes that match it.
        """
        files = {}
        for rel in sorted({rel for _p, parts in SEND_CALLS for rel in parts.values()}):
            data = (self.out / rel).read_bytes()
            files[rel] = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
        prompts = (self.prompt_manifest.get("files") or {}) if self.prompt_manifest else {}
        return {
            "format": "fde_send/1",
            "files": files,
            "calls": [
                {
                    "prompt": {
                        "name": name,
                        "sha256": str((prompts.get(name) or {}).get("sha256") or ""),
                    },
                    "user_message": dict(parts),
                }
                for name, parts in SEND_CALLS
            ],
        }

    def _tables_raw(self) -> dict[str, set[str]]:
        return {t: set(cols) for t, cols in self.stats.items()}

    def stage5(self) -> None:
        from ao_vendor.llm.ov_client import OpenVaultOntologyClient
        from dms_executor import fde_ai
        from dms_executor.demo_warehouse import warehouse_path
        from dms_executor.fde_pack import LeakChecker

        # Nothing is sent until a person has named the sha256 of send_manifest.json, which
        # lists every file and prompt stage 5 sends. Each file is read once, checked against
        # the manifest, and parsed from those bytes: a file changed after the check cannot
        # be what is sent.
        manifest_bytes = (self.out / "send_manifest.json").read_bytes()
        if not self.args.confirm_pack:
            raise _Refuse(EXIT_REFUSED, "pack_not_confirmed")
        if self.args.confirm_pack.strip().lower() != hashlib.sha256(manifest_bytes).hexdigest():
            raise _Refuse(EXIT_REFUSED, "pack_changed")
        send = json.loads(manifest_bytes)
        confirmed: dict[str, Any] = {}
        for rel, meta in (send.get("files") or {}).items():
            data = (self.out / rel).read_bytes()
            if hashlib.sha256(data).hexdigest() != meta.get("sha256"):
                raise _Refuse(EXIT_REFUSED, "pack_changed")
            confirmed[rel] = json.loads(data)
        for call in send.get("calls") or []:
            name = str((call.get("prompt") or {}).get("name"))
            text = self.prompts.get(name, "")
            if hashlib.sha256(text.encode()).hexdigest() != (call.get("prompt") or {}).get(
                "sha256"
            ):
                raise _Refuse(EXIT_REFUSED, "pack_changed")

        def payload(prompt: str) -> dict[str, Any]:
            parts = next(c["user_message"] for c in send["calls"] if c["prompt"]["name"] == prompt)
            return {key: confirmed[rel] for key, rel in parts.items()}

        client = OpenVaultOntologyClient(self.transport)
        tables = self._tables_raw()
        names = self.names
        counts: dict[str, int] = {}

        # (a) ontology draft
        draft = fde_ai.run_stage(
            client,
            "01_ontology_draft",
            self.prompts["01_ontology_draft.md"],
            payload("01_ontology_draft.md"),
            fde_ai.OntologyDraftOut,
        )
        items, dropped = fde_ai.ontology_items(draft, back=names.back, tables=tables)
        stored = self._store_items(
            [(k, raw, {"prompt": "01_ontology_draft.md"}) for k, raw, _m in items], "5a"
        )
        self._write(
            "proposals/ontology_draft.json",
            {
                "stage": 5,
                "prompt": "01_ontology_draft.md",
                "items": [
                    {"id": i["id"], "status": i["status"], "kind": k, **masked}
                    for i, (k, _raw, masked) in zip(stored, items, strict=True)
                ],
                "dropped": [_drop_row(d) for d in dropped],
            },
        )
        counts["ontology_items"] = len(stored)
        counts["ontology_dropped"] = len(dropped)

        # (b) verified questions with SQL
        asked = fde_ai.run_stage(
            client,
            "02_verified_questions",
            self.prompts["02_verified_questions.md"],
            payload("02_verified_questions.md"),
            fde_ai.VerifiedQuestionsOut,
        )
        leak = LeakChecker(self.space_id)
        warehouse = warehouse_path()
        q_items: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
        q_pack: list[dict[str, Any]] = []
        q_drop: list[dict[str, Any]] = []
        q_rows: list[list[dict[str, Any]]] = []
        for index, q in enumerate(asked.questions):
            if leak.hits(f"{q.question}\n{q.sql}") > 0:
                q_drop.append({"index": index, "reason": "echoes_source_value"})
                continue
            reason, info = fde_ai.check_question_sql(
                q.sql, space_id=self.space_id, warehouse=warehouse, tables=tables, back=names.back
            )
            if reason:
                q_drop.append({"index": index, "reason": fde_ai.reason_code(reason)})
                continue
            q_items.append(
                (
                    "verified_query",
                    {
                        "name": "verified_query:"
                        + hashlib.sha256(info["sql"].encode()).hexdigest()[:16],
                        "question": q.question,
                        "sql": info["sql"],
                        "row_count": info["row_count"],
                        "result_sha256": info["result_sha256"],
                    },
                    {"row_count": info["row_count"], "prompt": "02_verified_questions.md"},
                )
            )
            q_pack.append(fde_ai.masked_question_row("", q.question, q.sql, info))
            q_rows.append(list(info["rows"]))
        stored_q = self._store_items(q_items, "5b")
        for item, pack_row, rows in zip(stored_q, q_pack, q_rows, strict=True):
            pack_row.update({"id": item["id"], "status": item["status"]})
            self.kept_questions.append(
                {"id": item["id"], "question": item["payload"]["question"], "rows": rows}
            )
        # Totals only. Which question was kept, and why one was dropped, is data-dependent:
        # an adversarial model can read one bit of a cell per question from that pattern.
        # The kept questions live in the item store (--import), never in the pack.
        self._write(
            "proposals/verified_questions.json",
            {
                "stage": 5,
                "prompt": "02_verified_questions.md",
                "kept": len(q_pack),
                "dropped": len(q_drop),
            },
        )
        counts["questions_kept"] = len(q_pack)
        counts["questions_dropped"] = len(q_drop)

        # (c) value mappings
        mapped = fde_ai.run_stage(
            client,
            "03_value_mappings",
            self.prompts["03_value_mappings.md"],
            payload("03_value_mappings.md"),
            fde_ai.ValueMappingsOut,
        )
        by_id = {str(g["group_id"]): g for g in self.groups}
        v_items, v_drop = fde_ai.value_mapping_items(mapped, by_id)
        stored_v = self._store_items(
            [(k, raw, {"prompt": "03_value_mappings.md"}) for k, raw, _m in v_items], "5c"
        )
        self._write(
            "proposals/value_mappings.json",
            {
                "stage": 5,
                "prompt": "03_value_mappings.md",
                "items": [
                    {"id": i["id"], "status": i["status"], **masked}
                    for i, (_k, _raw, masked) in zip(stored_v, v_items, strict=True)
                ],
                "dropped": [_drop_row(d) for d in v_drop],
            },
        )
        counts["value_mappings"] = len(stored_v)
        counts["value_mappings_dropped"] = len(v_drop)
        counts["model_calls"] = client.calls_ok
        self._done(5, **counts)

    def stage6(self, http: Any) -> None:
        from dms_executor.envelope import assert_envelope_valid

        health = http.get("/health")
        cortex = ((health.json() or {}).get("dependencies") or {}).get("cortex") or {}
        if health.status_code != 200 or not cortex.get("ok"):
            raise _Refuse(EXIT_REFUSED, "cortex_unreachable")
        # The only reference is the proposal's own SQL, which no steward has reviewed. So
        # this counts agreement with it, never correctness: there is no truth to count
        # against until a steward approves a verified question.
        tally = Counter(
            {"agrees": 0, "abstained": 0, "disagrees": 0, "invalid_envelope": 0, "http_error": 0}
        )
        for q in self.kept_questions:
            r = http.post(
                "/v1/chat/ask",
                json={
                    "question": q["question"],
                    "space_id": self.space_id,
                    "session_id": f"fde_onboard_{self.run_id}",
                },
            )
            outcome = "http_error"
            if r.status_code == 200:
                env = r.json()
                try:
                    assert_envelope_valid(env)
                except AssertionError:
                    outcome = "invalid_envelope"
                else:
                    if env.get("abstained"):
                        outcome = "abstained"
                    elif _multiset(env.get("rows") or []) == _multiset(q["rows"]):
                        outcome = "agrees"
                    else:
                        outcome = "disagrees"
            tally[outcome] += 1
        self._write(
            "measure.json",
            {
                "stage": 6,
                "reference": "proposed_sql_unreviewed",
                "truth": "none_until_a_steward_approves",
                "correct": None,
                "wrong": None,
                "asked": len(self.kept_questions),
                **dict(tally),
            },
        )
        self._done(6, asked=len(self.kept_questions), **dict(tally))

    # ------------------------------------------------------------------ manifest + leak gate

    def _manifest(self) -> dict[str, Any]:
        from dms_executor.fde_pack import PACK_FORMAT, pack_files

        return {
            "format": PACK_FORMAT,
            "tool": TOOL,
            "run_id": self.run_id,
            "created_at": datetime.now(UTC).isoformat(),
            "space": {"id": self.space_id},
            "stages": self.stages,
            "files": pack_files(self.out),
            "prompts": self.prompt_manifest,
            "leak_check": self.leak,
            "store": {**self._store_counts(), "imported": bool(self.args.do_import)},
        }

    def _generated(self) -> set[str]:
        """Every id, digest and timestamp this run wrote. Only these skip the leak check."""
        from dms_executor.fde_pack import pack_files

        out = {self.run_id, self.space_id, *self.item_ids}
        out.update(str(f["sha256"]) for f in pack_files(self.out).values())
        out.update(
            str(f.get("sha256"))
            for f in (self.prompt_manifest.get("files") or {}).values()
            if f.get("sha256")
        )
        manifest = self.out / "manifest.json"
        if manifest.is_file():
            try:
                out.add(str(json.loads(manifest.read_text(encoding="utf-8"))["created_at"]))
            except (ValueError, KeyError):
                pass
        return out

    def finalize(self) -> None:
        """Write the manifest, then prove no source value is in any pack file. Fail closed."""
        from dms_executor.fde_pack import LeakChecker

        if not self.space_id or not self.out.is_dir():
            return
        checker = LeakChecker(self.space_id)
        self._write("manifest.json", self._manifest())
        self.leak = checker.check_pack(self.out, generated=self._generated())
        self._write("manifest.json", self._manifest())
        final = checker.check_pack(self.out, generated=self._generated())
        if final.get("status") != "passed" or self.leak.get("status") != "passed":
            hits = {**(self.leak.get("hits") or {}), **(final.get("hits") or {})}
            unlisted = {
                **(self.leak.get("unlisted_numbers") or {}),
                **(final.get("unlisted_numbers") or {}),
            }
            for path in self.written:
                path.unlink(missing_ok=True)
            self.written = []
            (self.out / LEAK_FILE).write_text(
                json.dumps(
                    {"reason": "pack_leak", "hits": hits, "unlisted_numbers": unlisted},
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
            raise _Refuse(EXIT_LEAK, "pack_leak")

    # ------------------------------------------------------------------ run

    def run(self) -> tuple[int, str | None]:
        from fastapi.testclient import TestClient

        stop = int(self.args.stop_after)
        try:
            self._preflight(stop)
            if self.http is not None:
                return self._run_all(self.http, stop)
            from dms_api.app import create_app

            # The lifespan binds the same stores and Cortex client the server binds.
            with TestClient(create_app()) as http:
                return self._run_all(http, stop)
        except _Refuse as why:
            return self._refused(why)

    def _refused(self, why: _Refuse) -> tuple[int, str | None]:
        if why.code != EXIT_LEAK and self.space_id and self.out.is_dir():
            try:
                self.finalize()
            except _Refuse as leak:
                return leak.code, leak.reason
        return why.code, why.reason

    def _run_all(self, http: Any, stop: int) -> tuple[int, str | None]:
        self.http = http
        try:
            return self._run_stages(http, stop)
        except _Refuse:
            raise
        except Exception as exc:  # noqa: BLE001 - a crash must not skip the leak gate
            # The class name only: a message or traceback can quote a cell value.
            reason = f"internal_error:{type(exc).__name__}"
            for row in self.stages:
                if row["status"] == "running":
                    row["status"], row["reason"] = "failed", reason
            try:
                self.finalize()
            except _Refuse as leak:
                return leak.code, leak.reason
            except Exception:  # noqa: BLE001 - cannot prove the pack clean: remove it
                for path in self.written:
                    path.unlink(missing_ok=True)
                self.written = []
            return EXIT_INTERNAL, reason

    def _run_stages(self, http: Any, stop: int) -> tuple[int, str | None]:
        from dms_core.control_plane.onto_data import ontology_data_store

        app = http.app
        settings = self._settings()
        # The same grant POST /v1/ontology/data/profile requires (#514).
        if not ontology_data_store().holds_steward_grant(
            role=settings.dms_actor_role, actor_id=settings.dms_actor_user_id
        ):
            raise _Refuse(EXIT_INPUT, "steward_grant_required")
        try:
            space = app.state.space_store.get(str(self.args.space).strip())
            if space is None:
                # Creating a Space is a DMS write: it belongs to the gated API or the UI.
                raise _Refuse(EXIT_REFUSED, "space_not_found")
            self.space_id = str(space.id)
            binding = getattr(app.state, "space_store_binding", None)
            self.store_info["space_persisted"] = bool(getattr(binding, "persistent", False))
            self._store_binding()
            self.out.mkdir(parents=True, exist_ok=True)
            for n, step in ((1, self.stage1), (2, self.stage2), (3, self.stage3)):
                self._run_stage(n, step)
            self._run_stage(4, self.stage4)
            for n in (5, 6):
                if n > stop:
                    self._stage(n)["status"] = "not_requested"
            if stop >= 5:
                self._run_stage(5, self.stage5)
            if stop >= 6:
                self._run_stage(6, lambda: self.stage6(http))
            self.finalize()
        except _Refuse as why:
            return self._refused(why)
        return EXIT_OK, None

    def _run_stage(self, n: int, step: Callable[[], None]) -> None:
        from dms_executor.fde_ai import StageRefused

        self._stage(n)["status"] = "running"
        try:
            step()
        except StageRefused as refused:
            self._stage(n)["status"] = "refused"
            self._stage(n)["reason"] = refused.code
            raise _Refuse(EXIT_REFUSED, refused.code) from None
        except _Refuse as why:
            if why.code == EXIT_LEAK:
                self._stage(n)["status"] = "failed"
                self._stage(n)["reason"] = why.reason
            else:
                self._stage(n)["status"] = "refused" if why.code == EXIT_REFUSED else "failed"
                self._stage(n)["reason"] = why.reason
            raise

    def _preflight(self, stop: int) -> None:
        from dms_executor import fde_ai

        source = Path(self.args.source)
        if not source.is_dir():
            raise _Refuse(EXIT_INPUT, "source_not_a_directory")
        if self.out.exists() and (not self.out.is_dir() or any(self.out.iterdir())):
            raise _Refuse(EXIT_INPUT, "out_not_empty")
        if not str(self.args.space or "").strip():
            raise _Refuse(EXIT_INPUT, "space_name_missing")
        try:
            self.prompts = fde_ai.load_prompts()
        except fde_ai.PromptDrift as drift:
            if stop >= 5:
                raise _Refuse(EXIT_DRIFT, f"prompt_drift:{drift.name}") from None
        self.prompt_manifest = fde_ai.prompt_manifest()

    def _store_binding(self) -> None:
        from dms_core.control_plane.onto_data import (
            PostgresOntologyDataStore,
            ontology_data_store,
        )

        persisted = isinstance(ontology_data_store(), PostgresOntologyDataStore)
        self.store_info["persisted"] = persisted
        self.store_info["hint"] = (
            None
            if persisted
            else "the item store is in memory (DMS_ONTO_PG is off): these proposals are "
            "lost when this command exits"
        )


def _detail_code(r: Any) -> str:
    """The route's refusal code (``steward_grant_required``, ``gate_unavailable``, ...)."""
    from dms_executor.fde_ai import reason_code

    try:
        detail = r.json().get("detail")
    except ValueError:
        detail = None
    if isinstance(detail, dict):
        detail = detail.get("code")
    code = reason_code(str(detail)) if isinstance(detail, str) else "unknown_reason"
    return code if code != "unknown_reason" else f"http_{r.status_code}"


def _drop_row(row: dict[str, Any]) -> dict[str, Any]:
    """A drop as the pack may carry it: position or group id, and the reason code only."""
    from dms_executor.fde_ai import reason_code

    out: dict[str, Any] = {"reason": reason_code(str(row.get("reason") or ""))}
    for key in ("kind", "index"):
        if key in row:
            out[key] = row[key]
    gid = str(row.get("group_id") or "")
    if re.fullmatch(r"g\d{3,}", gid):
        out["group_id"] = gid
    return out


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="fde_onboard", description=__doc__.split("\n\n")[0])
    ap.add_argument("--source", required=True, help="directory of CSV or sheet files")
    ap.add_argument(
        "--space", required=True, help="id of an existing Space (create it in the UI or API)"
    )
    ap.add_argument("--out", required=True, help="pack directory, must be new or empty")
    ap.add_argument(
        "--stop-after",
        type=int,
        choices=(4, 5, 6),
        default=6,
        help="4 = offline run (no model, no ask); default 6",
    )
    ap.add_argument(
        "--confirm-pack",
        default="",
        metavar="SHA256",
        help="sha256 of the send_manifest.json a person read; stage 5 sends nothing without it",
    )
    ap.add_argument(
        "--import",
        dest="do_import",
        action="store_true",
        help="store every proposal as pending through the gated DMS routes (needs Cortex F5)",
    )
    return ap.parse_args(argv)


def main(
    argv: Sequence[str] | None = None,
    *,
    http: Any = None,
    transport: Callable[..., Any] | None = None,
    stdout: Any = None,
) -> int:
    """``http`` and ``transport`` are for tests: an in-process client and a recorded model."""
    args = parse_args(argv)
    run = Onboard(args, http=http, transport=transport)
    code, reason = run.run()
    summary = {
        "exit": code,
        "reason": reason,
        "run_id": run.run_id,
        "space_id": run.space_id,
        "stages": [
            {"stage": s["stage"], "status": s["status"], "reason": s["reason"], **s["counts"]}
            for s in run.stages
        ],
        "leak_check": run.leak.get("status"),
        "store": run._store_counts(),
        "pack": str(run.out),
        "imported": bool(args.do_import),
        # What a person confirms with --confirm-pack before anything is sent.
        "send_manifest_sha256": (
            hashlib.sha256((run.out / "send_manifest.json").read_bytes()).hexdigest()
            if (run.out / "send_manifest.json").is_file()
            else None
        ),
        "manifest_sha256": (
            hashlib.sha256((run.out / "manifest.json").read_bytes()).hexdigest()
            if (run.out / "manifest.json").is_file()
            else None
        ),
    }
    print(json.dumps(summary, sort_keys=True), file=stdout or sys.stdout)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
