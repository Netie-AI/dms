#!/usr/bin/env python3
"""Regenerate the pipeline-map inventory from the DMS tree.

Reads this file's graph (anchors + why). Resolves each anchor to a line in
the working tree, extracts named abstain / INVALID / route tokens from the
cited span, and writes inventory.js + inventory.json next to itself.

No network. No secrets copied: line text is the source line, truncated.
Exit 1 when a required anchor is missing. Absent-on-purpose nodes stay
status "absent" and are listed under unverified.

    python tools/pipeline-map/regen.py
    python tools/pipeline-map/regen.py --check
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = Path(__file__).resolve().parent
SPAN = 80

# Anchors are unique substrings. The script records the first matching line.
# `require` tokens must occur inside the span or the node is unverified.
# `absent_needles` must NOT occur anywhere under `absent_globs` or the
# "not on this commit" claim is unverified the other way.

NODES: list[dict] = [
    {
        "id": "intake",
        "stage": "1. Question intake",
        "title": "POST /v1/chat/ask",
        "why": "This is the only customer door for a question. The text is a JSON field, not an x-dms header, so the caller cannot supply an identity.",
        "on_ask_path": True,
        "citations": [
            {"path": "apps/api/dms_api/routes/chat.py", "anchor": "def chat_ask("},
            {"path": "apps/api/dms_api/routes/chat.py", "anchor": "class AskBody"},
            {
                "path": "packages/cortex_client/cortex_client/generated/api/contract/ask.py",
                "anchor": '"url": "/v1/contract/ask"',
            },
        ],
        "scans": [
            {"path": "apps/api/dms_api/routes/chat.py", "anchor": "class AskBody", "span": 20},
        ],
    },
    {
        "id": "harness",
        "stage": "1. Question intake",
        "title": "Harness ask_path gate",
        "why": "exact and generative are measurement lanes. A product server refuses them before any gate, submit, or ledger write, so a keyword plan cannot ship under a confident badge.",
        "on_ask_path": True,
        "citations": [
            {
                "path": "apps/api/dms_api/routes/chat.py",
                "anchor": "if body.ask_path in _HARNESS_ASK_PATHS",
            },
        ],
        "scans": [
            {
                "path": "apps/api/dms_api/routes/chat.py",
                "anchor": "if body.ask_path in _HARNESS_ASK_PATHS",
                "span": 16,
            },
        ],
        "require": ["ask_path_not_allowed"],
    },
    {
        "id": "space_lookup",
        "stage": "2. Space and grants",
        "title": "Space exists",
        "why": "An id that is not a Space must 404. Treating it as 'no boundary' used to open the whole warehouse.",
        "on_ask_path": True,
        "citations": [
            {
                "path": "apps/api/dms_api/routes/chat.py",
                "anchor": 'detail="space_not_found"',
            },
        ],
        "scans": [
            {
                "path": "apps/api/dms_api/routes/chat.py",
                "anchor": 'detail="space_not_found"',
                "span": 4,
            },
        ],
        "require": ["space_not_found"],
    },
    {
        "id": "f5",
        "stage": "2. Space and grants",
        "title": "compliance_gate (Cortex F5)",
        "why": "DMS does not decide allow or deny. It posts the action to Cortex and only soft-continues on two catalog misses.",
        "on_ask_path": True,
        "citations": [
            {
                "path": "apps/api/dms_api/routes/chat.py",
                "anchor": 'action="chat.ask"',
            },
            {
                "path": "packages/cortex_client/cortex_client/gate.py",
                "anchor": "def compliance_gate(",
            },
        ],
        "scans": [
            {
                "path": "packages/cortex_client/cortex_client/gate.py",
                "anchor": "def compliance_gate(",
                "span": 75,
            },
            {
                "path": "apps/api/dms_api/routes/chat.py",
                "anchor": "_SOFT_GATE = frozenset",
                "span": 4,
            },
        ],
        "require": ["gate_unavailable", "gate_task_unknown", "gate_refused"],
    },
    {
        "id": "mode",
        "stage": "2. Space and grants",
        "title": "Demo mode or live",
        "why": "Demo mode answers from the local demo warehouse and never calls Cortex. Live mode calls Cortex, or refuses when the client is missing.",
        "on_ask_path": True,
        "citations": [
            {
                "path": "apps/api/dms_api/routes/chat.py",
                "anchor": 'want_demo = settings.dms_ask_mode == "demo"',
            },
            {
                "path": "apps/api/dms_api/routes/chat.py",
                "anchor": '"code": "cortex_unavailable"',
            },
        ],
        "scans": [
            {
                "path": "apps/api/dms_api/routes/chat.py",
                "anchor": 'want_demo = settings.dms_ask_mode == "demo"',
                "span": 20,
            },
        ],
        "require": ["cortex_unavailable"],
    },
    {
        "id": "grants",
        "stage": "2. Space and grants",
        "title": "Space grants and grounding",
        "why": "The manifest's table list is what Cortex will enforce. A ticked file this Space cannot grant is refused, because dropping it used to grant the whole demo warehouse while the UI said one file.",
        "on_ask_path": True,
        "citations": [
            {
                "path": "packages/executor/dms_executor/__init__.py",
                "anchor": "def grantable_tables(",
            },
            {
                "path": "packages/executor/dms_executor/__init__.py",
                "anchor": "def demo_acl(",
            },
            {
                "path": "packages/executor/dms_executor/__init__.py",
                "anchor": "raise GroundingRefused(",
            },
            {
                "path": "packages/executor/dms_executor/acl.py",
                "anchor": "def intersect_space_grants(",
            },
            {
                "path": "packages/core/dms_core/ask.py",
                "anchor": 'code = "grounding_not_grantable"',
            },
        ],
        "scans": [
            {
                "path": "packages/executor/dms_executor/__init__.py",
                "anchor": "def demo_acl(",
                "span": 55,
            },
            {
                "path": "packages/core/dms_core/ask.py",
                "anchor": "class GroundingRefused",
                "span": 30,
            },
            {
                "path": "packages/executor/dms_executor/acl.py",
                "anchor": "def intersect_space_grants(",
                "span": 30,
            },
        ],
        "require": ["grounding_not_grantable"],
    },
    {
        "id": "ladder",
        "stage": "3. Certified first",
        "title": "live_ask ladder",
        "why": "Product tries a follow-up, then certified SQL, then generation, then Cortex's own ask. exact stops after certified SQL. generative skips certified SQL.",
        "on_ask_path": True,
        "citations": [
            {
                "path": "packages/executor/dms_executor/__init__.py",
                "anchor": "def live_ask(",
            },
            {
                "path": "packages/executor/dms_executor/__init__.py",
                "anchor": "def _live_ask(",
            },
        ],
        "scans": [
            {
                "path": "packages/executor/dms_executor/__init__.py",
                "anchor": "certified_first = ladder != \"generative\"",
                "span": 12,
            },
        ],
    },
    {
        "id": "followup",
        "stage": "3. Certified first",
        "title": "Session follow-up",
        "why": "A short follow-up ('average that') may only use numbers already on the previous turn. It runs before the grant lookup, and a real $as_of placeholder abstains instead of executing.",
        "on_ask_path": True,
        "citations": [
            {
                "path": "packages/executor/dms_executor/__init__.py",
                "anchor": "follow = maybe_followup(",
            },
            {
                "path": "packages/executor/dms_executor/session_followup.py",
                "anchor": "def maybe_followup(",
            },
            {
                "path": "packages/executor/dms_executor/session_followup.py",
                "anchor": "def _followup_execute(",
            },
            {
                "path": "packages/executor/dms_executor/demo_warehouse.py",
                "anchor": 'RESERVED_PARAM_AS_OF = "reserved_param:as_of"',
            },
        ],
        "scans": [
            {
                "path": "packages/executor/dms_executor/session_followup.py",
                "anchor": "def _abstain(",
                "span": 22,
            },
            {
                "path": "packages/executor/dms_executor/session_followup.py",
                "anchor": "def _followup_execute(",
                "span": 18,
            },
        ],
        "require": ["reserved_param:as_of"],
    },
    {
        "id": "vq",
        "stage": "3. Certified first",
        "title": "Verified query (stored SQL)",
        "why": "A person registered this exact question and this exact SQL for this Space. That certificate runs before any model, and it still has to be executed by Cortex and appended to the ledger. There is no local DuckDB fallback.",
        "on_ask_path": True,
        "citations": [
            {
                "path": "packages/executor/dms_executor/__init__.py",
                "anchor": "verified_env = maybe_verified_ask(",
            },
            {
                "path": "packages/executor/dms_executor/verified_queries.py",
                "anchor": "def maybe_verified_ask(",
            },
        ],
        "scans": [
            {
                "path": "packages/executor/dms_executor/verified_queries.py",
                "anchor": "def maybe_verified_ask(",
                "span": 55,
            },
        ],
        "require": ["verified_query"],
    },
    {
        "id": "pack",
        "stage": "3. Certified first",
        "title": "Demo pack (stored SQL)",
        "why": "Same shape as a verified query for the curated metrics: exact question, stored SQL, Cortex submit, ledger append. A submit or ledger miss falls through. It does not become a 503 and it does not run locally.",
        "on_ask_path": True,
        "citations": [
            {
                "path": "packages/executor/dms_executor/__init__.py",
                "anchor": "pack_env = maybe_pack_ask(",
            },
            {
                "path": "packages/executor/dms_executor/demo_pack.py",
                "anchor": "def maybe_pack_ask(",
            },
        ],
        "scans": [
            {
                "path": "packages/executor/dms_executor/demo_pack.py",
                "anchor": "def maybe_pack_ask(",
                "span": 50,
            },
        ],
        "require": ["ask.governed_metric"],
    },
    {
        "id": "planted",
        "stage": "3. Certified first",
        "title": "Planted uncertified refuse",
        "why": "Three exact phrasings are traps. They must abstain. A governed number on them would be a guess wearing a green badge.",
        "on_ask_path": True,
        "citations": [
            {
                "path": "packages/executor/dms_executor/__init__.py",
                "anchor": "refuse_env = maybe_uncertified_refuse_ask(",
            },
            {
                "path": "packages/executor/dms_executor/demo_pack.py",
                "anchor": "def maybe_uncertified_refuse_ask(",
            },
        ],
        "scans": [
            {
                "path": "packages/executor/dms_executor/demo_pack.py",
                "anchor": "def maybe_uncertified_refuse_ask(",
                "span": 40,
            },
        ],
        "require": ["ABSTAIN"],
    },
    {
        "id": "exact_miss",
        "stage": "3. Certified first",
        "title": "Exact-lane miss",
        "why": "The exact measurement lane has nothing left to try. It abstains instead of mixing in generation or Cortex.",
        "on_ask_path": True,
        "citations": [
            {
                "path": "packages/executor/dms_executor/__init__.py",
                "anchor": "exact-match miss: not a certified VQ/pack hit",
            },
        ],
        "scans": [
            {
                "path": "packages/executor/dms_executor/__init__.py",
                "anchor": 'if ladder == "exact":',
                "span": 12,
            },
        ],
        "require": ["exact-match miss"],
    },
    {
        "id": "cascade",
        "stage": "4. Ontology and ingest",
        "title": "Constraint cascade (off)",
        "why": "It binds filter words to spellings that actually landed, and abstains when a binding cannot be certified. It ships off: the engagement rule both over-fired and missed, so it does not decide a customer answer until that is measured.",
        "on_ask_path": True,
        "citations": [
            {
                "path": "packages/executor/dms_executor/__init__.py",
                "anchor": "if cascade.blocked_at is not None:",
            },
            {
                "path": "packages/executor/dms_executor/cca/cascade.py",
                "anchor": "def cascade_enabled(",
            },
        ],
        "scans": [
            {
                "path": "packages/executor/dms_executor/cca/cascade.py",
                "anchor": "def cascade_enabled(",
                "span": 8,
            },
        ],
        "require": ["DMS_CCA_CASCADE"],
    },
    {
        "id": "bronze",
        "stage": "4. Ontology and ingest",
        "title": "Named workbook sheet",
        "why": "A question that names one workbook and one sheet is read from that bronze table. A miss returns nothing so the next lane can run. This path uses DuckDB inside the executor. It does not call Cortex submit.",
        "on_ask_path": True,
        "citations": [
            {
                "path": "packages/executor/dms_executor/__init__.py",
                "anchor": "bronze_env = maybe_bronze_sheet_ask(",
            },
            {
                "path": "packages/executor/dms_executor/bronze_sheet_ask.py",
                "anchor": "def maybe_bronze_sheet_ask(",
            },
        ],
        "scans": [
            {
                "path": "packages/executor/dms_executor/bronze_sheet_ask.py",
                "anchor": "def maybe_bronze_sheet_ask(",
                "span": 70,
            },
        ],
    },
    {
        "id": "onto_verify",
        "stage": "4. Ontology and ingest",
        "title": "Ontology verify()",
        "why": "Join shape is a claim about this data. verify() measures keys, nulls, business keys, and foreign keys before any compile is allowed to treat a link as many-to-one.",
        "on_ask_path": True,
        "citations": [
            {
                "path": "packages/executor/dms_executor/ontology.py",
                "anchor": "def verify(self, con: Any)",
            },
            {
                "path": "packages/executor/dms_executor/generative_ask.py",
                "anchor": "def load_verified_ontology(",
            },
        ],
        "scans": [
            {
                "path": "packages/executor/dms_executor/ontology.py",
                "anchor": "def verify(self, con: Any)",
                "span": 210,
            },
        ],
        "require": [
            "key_unique",
            "key_not_null",
            "business_key_unique",
            "fk_intact",
            "fk_is_child_key",
            "relation_readable",
            "link_readable",
            "link_unmeasurable",
            "link_cardinality",
        ],
    },
    {
        "id": "join_rule",
        "stage": "4. Ontology and ingest",
        "title": "Join rule",
        "why": "Two declared links between the same objects are two different questions. The compiler refuses to pick one, and a via that is not on the path it would use is also a refusal.",
        "on_ask_path": True,
        "citations": [
            {
                "path": "packages/executor/dms_executor/ontology.py",
                "anchor": "def _resolve_path(",
            },
            {
                "path": "packages/executor/dms_executor/ontology.py",
                "anchor": "A join cannot be checked unless both sides name the same arity.",
            },
        ],
        "scans": [
            {
                "path": "packages/executor/dms_executor/ontology.py",
                "anchor": "def _resolve_link(",
                "span": 40,
            },
            {
                "path": "packages/executor/dms_executor/ontology.py",
                "anchor": "def _resolve_path(",
                "span": 180,
            },
        ],
        "require": ["ambiguous_path", "unknown_link", "unused_via", "unknown_object"],
    },
    {
        "id": "fanout",
        "stage": "4. Ontology and ingest",
        "title": "Fan-out guard",
        "why": "A join whose parent side is not unique multiplies fact rows and inflates the measure. Grouping may only walk many-to-one hops. Filters may use a semi-join, and that reading is named existential rather than hidden.",
        "on_ask_path": True,
        "citations": [
            {
                "path": "packages/executor/dms_executor/ontology.py",
                "anchor": '"fanout_refused"',
            },
        ],
        "scans": [
            {
                "path": "packages/executor/dms_executor/ontology.py",
                "anchor": '"fanout_refused"',
                "span": 12,
            },
        ],
        "require": ["fanout_refused"],
    },
    {
        "id": "typed_ingest",
        "stage": "4. Ontology and ingest",
        "title": "Typed ingest (not on the ask call)",
        "why": "Column types are proposed when a file or table is ingested, before any question. chat_ask does not call this. It is on the map because the warehouse the ask reads was typed here.",
        "on_ask_path": False,
        "citations": [
            {
                "path": "packages/executor/dms_executor/triage.py",
                "anchor": "def classify_grid(",
            },
            {
                "path": "packages/executor/dms_executor/contract_infer.py",
                "anchor": "def infer_contract(",
            },
            {
                "path": "packages/executor/dms_executor/bronze.py",
                "anchor": "def ingest_csv_bytes(",
            },
            {
                "path": "apps/api/dms_api/routes/studio.py",
                "anchor": "async def ingest_file(",
            },
        ],
        "scans": [
            {
                "path": "packages/executor/dms_executor/triage.py",
                "anchor": "dirty_reasons: list[str] = []",
                "span": 16,
            },
            {
                "path": "packages/executor/dms_executor/contract_infer.py",
                "anchor": "def _map_type(",
                "span": 12,
            },
            {
                "path": "packages/executor/dms_executor/bronze.py",
                "anchor": "def ingest_csv_bytes(",
                "span": 55,
            },
        ],
        "require": [
            "column_type_inconsistency",
            "empty_file",
            "unsupported_kind",
        ],
    },
    {
        "id": "named_gaps",
        "stage": "4. Ontology and ingest",
        "title": "Named abstain vocabulary",
        "why": "When Cortex ranks a metric DMS cannot compile, the envelope names the gap. It does not keyword-bind a guess and it does not fall through to a confident badge.",
        "on_ask_path": True,
        "citations": [
            {
                "path": "packages/executor/dms_executor/gen_path_refuse.py",
                "anchor": "GAP_REASONS = frozenset",
            },
            {
                "path": "packages/executor/dms_executor/generative_ask.py",
                "anchor": "def violation_reason(",
            },
        ],
        "scans": [
            {
                "path": "packages/executor/dms_executor/gen_path_refuse.py",
                "anchor": "GAP_REASONS = frozenset",
                "span": 28,
            },
        ],
        "require": [
            "unknown_measure",
            "no_path",
            "missing_join",
            "ontology_unverified",
            "unhonored_qualifier",
            "insights_timeout",
            "insights_bearer_missing",
        ],
    },
    {
        "id": "sql_rules",
        "stage": "5. SQL lanes",
        "title": "Slot compiler",
        "why": "compile() turns a typed plan (measure, group-by, filters) into SQL. The model fills slots. It does not write the statement. There is no switch named 'rules only' on this commit; this function is the rules lane the code actually has.",
        "on_ask_path": True,
        "lane_name_unverified": "rules only",
        "citations": [
            {
                "path": "packages/executor/dms_executor/ontology.py",
                "anchor": "def compile(",
            },
            {
                "path": "packages/executor/dms_executor/ontology.py",
                "anchor": "Rules, in order:",
            },
        ],
        "scans": [
            {
                "path": "packages/executor/dms_executor/ontology.py",
                "anchor": "def compile(",
                "span": 230,
            },
        ],
        "require": [
            "ontology_unverified",
            "unknown_measure",
            "unknown_column",
            "no_path",
            "bad_operator",
            "bad_limit",
            "coverage_invalid",
        ],
    },
    {
        "id": "sql_template",
        "stage": "5. SQL lanes",
        "title": "Template lane",
        "why": "No ask-path function generates SQL from a template. The stored statements are the verified-query and demo-pack hits above. gate.py's filled_template is the F5 payload, not a SQL generator.",
        "on_ask_path": False,
        "status": "absent",
        "lane_name_unverified": "template",
        "citations": [
            {
                "path": "packages/cortex_client/cortex_client/gate.py",
                "anchor": 'filled = dict(meta.get("filled_template") or {})',
            },
        ],
        "scans": [
            {
                "path": "packages/cortex_client/cortex_client/gate.py",
                "anchor": 'filled = dict(meta.get("filled_template") or {})',
                "span": 8,
            },
        ],
        "require": ["filled_template"],
        "absent_globs": [
            "packages/executor/dms_executor/generative_ask.py",
            "packages/cortex_client/cortex_client/compute.py",
        ],
        "absent_needles": ["rules only", "rules_only", "sql_template"],
    },
    {
        "id": "sql_ai",
        "stage": "5. SQL lanes",
        "title": "Insights generate (FreeRoute hint)",
        "why": "The model call is Cortex POST /v1/insights with generate=true. DMS sends model_preference free+normal and does not name a provider. OpenVault holds the keys inside Cortex. A missing or demo key, or plain http off loopback, abstains before the call.",
        "on_ask_path": True,
        "citations": [
            {
                "path": "packages/cortex_client/cortex_client/compute.py",
                "anchor": 'FREEROUTE_PREFERENCE = "free+normal"',
            },
            {
                "path": "packages/cortex_client/cortex_client/compute.py",
                "anchor": "def compute_insights(",
            },
            {
                "path": "packages/cortex_client/cortex_client/compute.py",
                "anchor": "def _insights_body(",
            },
            {
                "path": "packages/cortex_client/cortex_client/insights.py",
                "anchor": 'INSIGHTS_PATH = "/v1/insights"',
            },
            {
                "path": "packages/cortex_client/cortex_client/insights.py",
                "anchor": "def generate_bearer_refuse(",
            },
            {
                "path": "packages/executor/dms_executor/__init__.py",
                "anchor": "def _insights_compute_seam(",
            },
        ],
        "scans": [
            {
                "path": "packages/cortex_client/cortex_client/compute.py",
                "anchor": "INSIGHTS_FAIL_UNARMED = ",
                "span": 20,
            },
            {
                "path": "packages/cortex_client/cortex_client/compute.py",
                "anchor": "def _insights_body(",
                "span": 26,
            },
            {
                "path": "packages/cortex_client/cortex_client/insights.py",
                "anchor": "def generate_bearer_refuse(",
                "span": 24,
            },
        ],
        "require": ["free+normal", "insights_unarmed", "insights_bearer_missing"],
    },
    {
        "id": "strict_pin",
        "stage": "5. SQL lanes",
        "title": "Strict model pin",
        "why": "Not on this commit. No strict_pin module exists under packages/cortex_client. The generate body sends free+normal, not a pinned model id. An open pull request adds a pin; this map does not borrow it.",
        "on_ask_path": False,
        "status": "absent",
        "lane_name_unverified": "strict pin",
        "citations": [
            {
                "path": "packages/cortex_client/cortex_client/compute.py",
                "anchor": 'FREEROUTE_PREFERENCE = "free+normal"',
            },
        ],
        "scans": [
            {
                "path": "packages/cortex_client/cortex_client/compute.py",
                "anchor": 'FREEROUTE_PREFERENCE = "free+normal"',
                "span": 4,
            },
        ],
        "absent_globs": ["packages/cortex_client/**/*.py"],
        "absent_needles": ["strict_pin", "STRICT_PIN"],
    },
    {
        "id": "fallback",
        "stage": "5. SQL lanes",
        "title": "Generate fallback chain",
        "why": "Empty generate can climb to a ranked ontology plan. A failed validate can climb the same way if ranked slots exist. A named Insights failure abstains. Keyword bind runs only when a harness asks for it. The product path leaves a pure miss for Cortex ask.",
        "on_ask_path": True,
        "citations": [
            {
                "path": "packages/executor/dms_executor/generative_ask.py",
                "anchor": 'NOTE_FALLBACK_GENERATE_EMPTY = "fallback:generate_empty"',
            },
            {
                "path": "packages/executor/dms_executor/generative_ask.py",
                "anchor": 'NOTE_FALLBACK_VALIDATE_PREFIX = "fallback:validate:"',
            },
            {
                "path": "packages/executor/dms_executor/generative_ask.py",
                "anchor": 'fallback_note = "compute_fallback:bind_plan"',
            },
            {
                "path": "packages/cortex_client/cortex_client/compute.py",
                "anchor": "def _run_insights_legs(",
            },
            {
                "path": "packages/executor/dms_executor/__init__.py",
                "anchor": "bind_on_miss=False",
            },
        ],
        "scans": [
            {
                "path": "packages/executor/dms_executor/generative_ask.py",
                "anchor": "NOTE_INSIGHTS_RANKING = ",
                "span": 6,
            },
            {
                "path": "packages/executor/dms_executor/generative_ask.py",
                "anchor": "if kind == \"miss\" and ranked_slots is not None:",
                "span": 20,
            },
            {
                "path": "packages/cortex_client/cortex_client/compute.py",
                "anchor": "def _run_insights_legs(",
                "span": 46,
            },
        ],
        "require": [
            "fallback:generate_empty",
            "fallback:validate:",
            "compute_fallback:bind_plan",
        ],
    },
    {
        "id": "contract_ask",
        "stage": "6. Cortex submit",
        "title": "POST /v1/contract/ask",
        "why": "If generation returns nothing and this is the product lane, DMS sends the question to Cortex's ask endpoint on the bound session. A refusal route outranks any confident badge Cortex sent with it.",
        "on_ask_path": True,
        "citations": [
            {
                "path": "packages/executor/dms_executor/__init__.py",
                "anchor": "resp = self._cortex.ask(",
            },
            {
                "path": "packages/cortex_client/cortex_client/client.py",
                "anchor": "def ask(self, req: AskRequest)",
            },
            {
                "path": "packages/cortex_client/cortex_client/generated/api/contract/ask.py",
                "anchor": '"url": "/v1/contract/ask"',
            },
        ],
        "scans": [
            {
                "path": "packages/executor/dms_executor/__init__.py",
                "anchor": "_REFUSAL_ROUTES = frozenset",
                "span": 4,
            },
        ],
        "require": ["abstain", "blocked", "needs_clarification", "refused"],
    },
    {
        "id": "manifest",
        "stage": "6. Cortex submit",
        "title": "Signed manifest",
        "why": "The session ACL is signed with the OpenVault intermediate key over canonical bytes imported from cortex-contract. DMS does not reimplement that canonicalisation. A one-byte drift would break every signature.",
        "on_ask_path": True,
        "citations": [
            {
                "path": "packages/executor/dms_executor/manifest.py",
                "anchor": "def mint_manifest(self, acl: SessionAcl)",
            },
            {
                "path": "packages/executor/dms_executor/manifest.py",
                "anchor": "payload = canonical_manifest_bytes(unsigned)",
            },
        ],
        "scans": [
            {
                "path": "packages/executor/dms_executor/manifest.py",
                "anchor": "def mint_manifest(self, acl: SessionAcl)",
                "span": 40,
            },
            {
                "path": "packages/executor/dms_executor/manifest.py",
                "anchor": "def classify_submit_error(",
                "span": 40,
            },
        ],
        "require": ["canonical_manifest_bytes", "manifest_signature_invalid"],
    },
    {
        "id": "submit",
        "stage": "6. Cortex submit",
        "title": "POST /v1/contract/submit",
        "why": "Certified and compiled SQL is posted as plan.kind sql plus the signed manifest. Hostile SQL is rejected first. A real $as_of placeholder is rejected here and is not executed.",
        "on_ask_path": True,
        "citations": [
            {
                "path": "packages/executor/dms_executor/__init__.py",
                "anchor": "def submit_sql(",
            },
            {
                "path": "packages/cortex_client/cortex_client/client.py",
                "anchor": "def submit(self, req: ContractSubmitRequest)",
            },
            {
                "path": "packages/cortex_client/cortex_client/generated/api/contract/submit.py",
                "anchor": '"url": "/v1/contract/submit"',
            },
        ],
        "scans": [
            {
                "path": "packages/executor/dms_executor/__init__.py",
                "anchor": "def submit_sql(",
                "span": 36,
            },
        ],
        "require": ["path_not_allowed", "statement_not_allowed", "manifest_rejected"],
    },
    {
        "id": "ledger",
        "stage": "6. Cortex submit",
        "title": "Ledger append",
        "why": "After a successful verified, pack, or generated submit, DMS appends a pointer through Cortex. There is no local hash chain. A missing entry id or a hash that equals the id is not an answer.",
        "on_ask_path": True,
        "citations": [
            {
                "path": "packages/executor/dms_executor/__init__.py",
                "anchor": "def _ledger_verified_query(",
            },
            {
                "path": "packages/cortex_client/cortex_client/client.py",
                "anchor": "def ledger_append(",
            },
            {
                "path": "packages/cortex_client/cortex_client/generated/api/contract/ledger_append.py",
                "anchor": '"url": "/v1/contract/ledger/append"',
            },
        ],
        "scans": [
            {
                "path": "packages/executor/dms_executor/__init__.py",
                "anchor": "def _ledger_verified_query(",
                "span": 24,
            },
        ],
        "require": ["ask.verified_query", "ask.governed_metric", "ask.generated_ontology"],
    },
    {
        "id": "submit_abstain",
        "stage": "6. Cortex submit",
        "title": "Submit refusal becomes an answer or an HTTP error",
        "why": "A Space that lacks a table returns an abstain envelope, not a crash. A grounded question that needs an unticked table is 403 outside_grounded_scope. Unknown engine codes are 502, and timeouts are 504, so a cold engine is not reported as 'you may not'.",
        "on_ask_path": True,
        "citations": [
            {
                "path": "apps/api/dms_api/routes/chat.py",
                "anchor": "def _space_refusal_envelope(",
            },
            {
                "path": "apps/api/dms_api/routes/chat.py",
                "anchor": "def _status_for(",
            },
            {
                "path": "apps/api/dms_api/routes/chat.py",
                "anchor": '"code": "outside_grounded_scope"',
            },
        ],
        "scans": [
            {
                "path": "apps/api/dms_api/routes/chat.py",
                "anchor": "_STATUS_BY_CODE: dict[str, int] = {",
                "span": 28,
            },
            {
                "path": "apps/api/dms_api/routes/chat.py",
                "anchor": "def _space_refusal_envelope(",
                "span": 30,
            },
        ],
        "require": [
            "path_not_allowed",
            "outside_grounded_scope",
            "session_unbound",
            "pool_saturated",
            "ABSTAIN",
        ],
    },
    {
        "id": "mask",
        "stage": "7. Mask and serve",
        "title": "PII mask inside the envelope",
        "why": "Before text or rows are returned, a local detector replaces person data with DMSMASK tokens. If the detector errors, cells become DMSMASK_unknown_00 rather than leaking. This is not a network call.",
        "on_ask_path": True,
        "citations": [
            {
                "path": "packages/executor/dms_executor/envelope.py",
                "anchor": "masked = fail_closed_mask_payload(",
            },
            {
                "path": "packages/core/dms_core/pii.py",
                "anchor": "def mask_payload(",
            },
            {
                "path": "packages/core/dms_core/pii.py",
                "anchor": "def mask_envelope(",
            },
            {
                "path": "packages/core/dms_core/pii.py",
                "anchor": "def fail_closed_mask_payload(",
            },
        ],
        "scans": [
            {
                "path": "packages/core/dms_core/pii.py",
                "anchor": "KINDS = frozenset",
                "span": 6,
            },
            {
                "path": "packages/executor/dms_executor/envelope.py",
                "anchor": "masked = fail_closed_mask_payload(",
                "span": 16,
            },
        ],
        "require": ["name", "nric", "phone", "email", "dob", "DMSMASK_unknown_00"],
    },
    {
        "id": "serve",
        "stage": "7. Mask and serve",
        "title": "Served envelope (SQL + rows, or abstain)",
        "why": "The customer object carries the badge, the prose, sql_used, and the rows. Abstain clears rows, values, and the drillthrough token. A figure that is not in the result, or a forecast answered with history, is demoted here.",
        "on_ask_path": True,
        "citations": [
            {
                "path": "packages/executor/dms_executor/__init__.py",
                "anchor": "def map_ask_response_to_envelope(",
            },
            {
                "path": "packages/executor/dms_executor/envelope.py",
                "anchor": "def build_answer_envelope(",
            },
            {
                "path": "packages/executor/dms_executor/generative_ask.py",
                "anchor": "def with_served_attribution(",
            },
        ],
        "scans": [
            {
                "path": "packages/executor/dms_executor/__init__.py",
                "anchor": "_BADGE_MAP = {",
                "span": 14,
            },
            {
                "path": "packages/executor/dms_executor/envelope.py",
                "anchor": "prose figure not in query result",
                "span": 8,
            },
        ],
        "require": ["L0_CERTIFIED", "L2_VALIDATED", "ABSTAIN", "sql_used"],
    },
    {
        "id": "refusal",
        "stage": "7. Mask and serve",
        "title": "Named refusal",
        "why": "A refusal route (abstain, blocked, needs_clarification, refused) forces badge ABSTAIN even if Cortex also sent a confident badge. Policy failures stay HTTP errors. Demo numbers are not pasted over a policy refusal.",
        "on_ask_path": True,
        "citations": [
            {
                "path": "packages/executor/dms_executor/__init__.py",
                "anchor": "_REFUSAL_ROUTES = frozenset",
            },
            {
                "path": "apps/api/dms_api/routes/chat.py",
                "anchor": "_POLICY_CODES = frozenset",
            },
            {
                "path": "packages/executor/dms_executor/envelope.py",
                "anchor": "def reserved_as_of_abstain(",
            },
        ],
        "scans": [
            {
                "path": "packages/executor/dms_executor/__init__.py",
                "anchor": "_REFUSAL_ROUTES = frozenset",
                "span": 3,
            },
            {
                "path": "apps/api/dms_api/routes/chat.py",
                "anchor": "_POLICY_CODES = frozenset",
                "span": 18,
            },
            {
                "path": "packages/executor/dms_executor/envelope.py",
                "anchor": "def reserved_as_of_abstain(",
                "span": 30,
            },
        ],
        "require": ["refused", "path_not_allowed", "manifest_signature_invalid"],
    },
    {
        "id": "score_curated",
        "stage": "8. Scoring",
        "title": "score_curated.py",
        "why": "The curated judge compares the served envelope to an oracle. A confident wrong row set is WRONG. Abstain is not WRONG. A masked column the oracle compares, a missing engine date, or a round that crosses midnight is INVALID and stays in the count.",
        "on_ask_path": False,
        "citations": [
            {
                "path": "scripts/score_curated.py",
                "anchor": "def judge_detailed(",
            },
            {
                "path": "scripts/score_curated.py",
                "anchor": "def no_envelope_verdict(",
            },
            {
                "path": "scripts/score_curated.py",
                "anchor": "def round_date_label(",
            },
            {
                "path": "scripts/oracle_row_match.py",
                "anchor": "def rows_mismatch_reason(",
            },
        ],
        "scans": [
            {
                "path": "scripts/score_curated.py",
                "anchor": "def judge_detailed(",
                "span": 50,
            },
            {
                "path": "scripts/score_curated.py",
                "anchor": "def _judge_badge(",
                "span": 20,
            },
            {
                "path": "scripts/score_curated.py",
                "anchor": "def no_envelope_verdict(",
                "span": 10,
            },
            {
                "path": "scripts/score_curated.py",
                "anchor": "def _mask_compare_gate(",
                "span": 40,
            },
            {
                "path": "scripts/score_curated.py",
                "anchor": "def round_date_label(",
                "span": 12,
            },
            {
                "path": "scripts/score_curated.py",
                "anchor": "def baseline_eligibility(",
                "span": 20,
            },
            {
                "path": "scripts/oracle_row_match.py",
                "anchor": "def rows_mismatch_reason(",
                "span": 16,
            },
            {
                "path": "scripts/score_curated.py",
                "anchor": "def judge_envelope(",
                "span": 14,
            },
        ],
        "require": [
            "INVALID",
            "ABSTAIN",
            "WRONG",
            "ORACLE_ERROR",
            "masked_compare:",
            "rows_mismatch:count=",
            "rows_mismatch:values",
            "ask_error:",
            "RATE_LIMIT",
            "demo_fallback_used",
            "oracle_error:missing_sql",
            "round_spans_midnight",
            "round_end_unread",
            "record_unidentified",
            "record_write_failed",
            "overmask_star:dms#284",
        ],
    },
    {
        "id": "score_bird",
        "stage": "8. Scoring",
        "title": "score_bird.py",
        "why": "The BIRD harness posts the same /v1/chat/ask envelope and grades it with score_curated.judge. A transport block is BLOCKED, not a made-up PASS. Mini-Dev gold grading (GOLD_ERROR) runs only when this script is given --minidev, and that grader lives in bird_minidev.py.",
        "on_ask_path": False,
        "citations": [
            {"path": "scripts/score_bird.py", "anchor": "def run_live("},
            {"path": "scripts/score_bird.py", "anchor": "def run_exact("},
            {
                "path": "scripts/score_bird.py",
                "anchor": "from bird_minidev import run_minidev_cli",
            },
            {
                "path": "scripts/bird_minidev.py",
                "anchor": "def grade_envelope(",
            },
            {
                "path": "scripts/bird_minidev.py",
                "anchor": "def gold_error_dominating(",
            },
        ],
        "scans": [
            {"path": "scripts/score_bird.py", "anchor": "def run_live(", "span": 50},
            {
                "path": "scripts/bird_minidev.py",
                "anchor": "def grade_envelope(",
                "span": 16,
            },
            {
                "path": "scripts/bird_minidev.py",
                "anchor": "def gold_error_dominating(",
                "span": 12,
            },
            {
                "path": "scripts/bird_minidev.py",
                "anchor": "def pg_gold_error(",
                "span": 16,
            },
        ],
        "require": ["ABSTAIN", "WRONG", "GOLD_ERROR", "dead_connection", "sql_error"],
    },
]

EDGES: list[dict] = [
    {"frm": "intake", "to": "harness", "label": "body accepted", "anchor_path": "apps/api/dms_api/routes/chat.py", "anchor": "if body.ask_path in _HARNESS_ASK_PATHS"},
    {"frm": "harness", "to": "space_lookup", "label": "product path, or harness enabled", "anchor_path": "apps/api/dms_api/routes/chat.py", "anchor": "if body.space_id and store.get"},
    {"frm": "harness", "to": "refusal", "label": "400 ask_path_not_allowed", "anchor_path": "apps/api/dms_api/routes/chat.py", "anchor": '"code": "ask_path_not_allowed"'},
    {"frm": "space_lookup", "to": "f5", "label": "Space found or no space_id", "anchor_path": "apps/api/dms_api/routes/chat.py", "anchor": 'action="chat.ask"'},
    {"frm": "space_lookup", "to": "refusal", "label": "404 space_not_found", "anchor_path": "apps/api/dms_api/routes/chat.py", "anchor": 'detail="space_not_found"'},
    {"frm": "f5", "to": "mode", "label": "allowed, or soft gate", "anchor_path": "apps/api/dms_api/routes/chat.py", "anchor": "if not decision.allowed and decision.reason not in _SOFT_GATE:"},
    {"frm": "f5", "to": "refusal", "label": "403 gate reason", "anchor_path": "apps/api/dms_api/routes/chat.py", "anchor": "raise HTTPException(status_code=403, detail=decision.reason)"},
    {"frm": "mode", "to": "ladder", "label": "live and Cortex client present", "anchor_path": "apps/api/dms_api/routes/chat.py", "anchor": "return ask.live_ask("},
    {"frm": "mode", "to": "serve", "label": "demo mode, or bannered fallback", "anchor_path": "apps/api/dms_api/routes/chat.py", "anchor": "def _stamp_demo_fallback("},
    {"frm": "mode", "to": "refusal", "label": "503 cortex_unavailable", "anchor_path": "apps/api/dms_api/routes/chat.py", "anchor": '"code": "cortex_unavailable"'},
    {"frm": "ladder", "to": "followup", "label": "product lane only, before grants", "anchor_path": "packages/executor/dms_executor/__init__.py", "anchor": "follow = maybe_followup("},
    {"frm": "followup", "to": "serve", "label": "hit: local compute, not Cortex submit", "anchor_path": "packages/executor/dms_executor/session_followup.py", "anchor": "def run_followup_sql("},
    {"frm": "followup", "to": "vq", "label": "not a follow-up, certified-first", "anchor_path": "packages/executor/dms_executor/__init__.py", "anchor": "verified_env = maybe_verified_ask("},
    {"frm": "vq", "to": "grants", "label": "grantable set checked inside the lookup", "anchor_path": "packages/executor/dms_executor/__init__.py", "anchor": "grantable=set(self.grantable_tables(space_id=space_id))"},
    {"frm": "vq", "to": "submit", "label": "hit: Cortex submit of stored SQL", "anchor_path": "packages/executor/dms_executor/verified_queries.py", "anchor": "result = submit(hit[\"sql\"])"},
    {"frm": "vq", "to": "pack", "label": "miss", "anchor_path": "packages/executor/dms_executor/__init__.py", "anchor": "pack_env = maybe_pack_ask("},
    {"frm": "pack", "to": "submit", "label": "hit", "anchor_path": "packages/executor/dms_executor/demo_pack.py", "anchor": "result = submit(hit.sql)"},
    {"frm": "pack", "to": "ledger", "label": "submit ok", "anchor_path": "packages/executor/dms_executor/demo_pack.py", "anchor": 'led = ledger_append({"sql": hit.sql, "run_id": run_id})'},
    {"frm": "submit", "to": "ledger", "label": "verified / pack / generated SQL", "anchor_path": "packages/executor/dms_executor/__init__.py", "anchor": "def _ledger_verified_query("},
    {"frm": "pack", "to": "planted", "label": "miss", "anchor_path": "packages/executor/dms_executor/__init__.py", "anchor": "refuse_env = maybe_uncertified_refuse_ask("},
    {"frm": "planted", "to": "refusal", "label": "exact phrase hit", "anchor_path": "packages/executor/dms_executor/demo_pack.py", "anchor": "def maybe_uncertified_refuse_ask("},
    {"frm": "planted", "to": "exact_miss", "label": "exact lane and not a trap", "anchor_path": "packages/executor/dms_executor/__init__.py", "anchor": 'if ladder == "exact":'},
    {"frm": "exact_miss", "to": "refusal", "label": "ABSTAIN path miss", "anchor_path": "packages/executor/dms_executor/__init__.py", "anchor": "exact-match miss: not a certified VQ/pack hit"},
    {"frm": "planted", "to": "grants", "label": "product or generative continues", "anchor_path": "packages/executor/dms_executor/__init__.py", "anchor": "default_readable = [t for t in granted if t in DEMO_TABLES]"},
    {"frm": "grants", "to": "cascade", "label": "readable set, then cascade if the flag is on", "anchor_path": "packages/executor/dms_executor/__init__.py", "anchor": "if cascade.blocked_at is not None:"},
    {"frm": "grants", "to": "refusal", "label": "GroundingRefused", "anchor_path": "packages/executor/dms_executor/__init__.py", "anchor": "raise GroundingRefused("},
    {"frm": "cascade", "to": "refusal", "label": "blocked binding", "anchor_path": "packages/executor/dms_executor/__init__.py", "anchor": "return cascade_abstain_envelope("},
    {"frm": "cascade", "to": "bronze", "label": "not blocked (flag default off skips the check)", "anchor_path": "packages/executor/dms_executor/__init__.py", "anchor": "bronze_env = maybe_bronze_sheet_ask("},
    {"frm": "bronze", "to": "serve", "label": "unique workbook+sheet hit, local DuckDB", "anchor_path": "packages/executor/dms_executor/bronze_sheet_ask.py", "anchor": 'badge="L0_CERTIFIED"'},
    {"frm": "bronze", "to": "named_gaps", "label": "miss, generation allowed", "anchor_path": "packages/executor/dms_executor/__init__.py", "anchor": "gen_env = maybe_generative_ask("},
    {"frm": "typed_ingest", "to": "onto_verify", "label": "earlier: typed bronze the verify reads. Not called by chat_ask.", "anchor_path": "packages/executor/dms_executor/contract_infer.py", "anchor": "def infer_contract("},
    {"frm": "named_gaps", "to": "onto_verify", "label": "declared ontology must have been verified", "anchor_path": "packages/executor/dms_executor/generative_ask.py", "anchor": "def load_verified_ontology("},
    {"frm": "onto_verify", "to": "join_rule", "label": "compile walks links only after verify", "anchor_path": "packages/executor/dms_executor/ontology.py", "anchor": "def _resolve_path("},
    {"frm": "join_rule", "to": "fanout", "label": "non many-to-one hop while grouping", "anchor_path": "packages/executor/dms_executor/ontology.py", "anchor": '"fanout_refused"'},
    {"frm": "fanout", "to": "sql_rules", "label": "clean path becomes SQL", "anchor_path": "packages/executor/dms_executor/ontology.py", "anchor": "def compile("},
    {"frm": "sql_rules", "to": "refusal", "label": "Refusal reason on the envelope", "anchor_path": "packages/executor/dms_executor/generative_ask.py", "anchor": 'q, f"{compiled.reason}: {compiled.detail}"'},
    {"frm": "named_gaps", "to": "sql_ai", "label": "compute_insights before compile", "anchor_path": "packages/executor/dms_executor/__init__.py", "anchor": "def _insights_compute_seam("},
    {"frm": "sql_ai", "to": "strict_pin", "label": "pin not in this tree; preference is free+normal", "anchor_path": "packages/cortex_client/cortex_client/compute.py", "anchor": 'FREEROUTE_PREFERENCE = "free+normal"'},
    {"frm": "sql_ai", "to": "fallback", "label": "generate, ranking, one retry", "anchor_path": "packages/cortex_client/cortex_client/compute.py", "anchor": "def _run_insights_legs("},
    {"frm": "sql_template", "to": "vq", "label": "stored SQL is the nearest thing. It is not a template generator.", "anchor_path": "packages/executor/dms_executor/verified_queries.py", "anchor": "def maybe_verified_ask("},
    {"frm": "fallback", "to": "sql_rules", "label": "ranked plan or climbed slots", "anchor_path": "packages/executor/dms_executor/generative_ask.py", "anchor": "compiled = _compile_maybe_unverified("},
    {"frm": "fallback", "to": "submit", "label": "generate SQL that validates", "anchor_path": "packages/executor/dms_executor/generative_ask.py", "anchor": "def _submit_validated("},
    {"frm": "fallback", "to": "refusal", "label": "named insights_fail or qualifier gap", "anchor_path": "packages/executor/dms_executor/generative_ask.py", "anchor": "fail = insights_fail_reason("},
    {"frm": "fallback", "to": "contract_ask", "label": "product miss and bind_on_miss is false", "anchor_path": "packages/executor/dms_executor/__init__.py", "anchor": "resp = self._cortex.ask("},
    {"frm": "contract_ask", "to": "manifest", "label": "session_bind then ask, both carry the manifest", "anchor_path": "packages/executor/dms_executor/__init__.py", "anchor": "def bind_session("},
    {"frm": "manifest", "to": "submit", "label": "signed bytes on the submit body", "anchor_path": "packages/executor/dms_executor/__init__.py", "anchor": "def submit_sql("},
    {"frm": "submit", "to": "submit_abstain", "label": "engine refusal or timeout", "anchor_path": "packages/executor/dms_executor/manifest.py", "anchor": "def classify_submit_error("},
    {"frm": "submit_abstain", "to": "refusal", "label": "envelope or HTTP status", "anchor_path": "apps/api/dms_api/routes/chat.py", "anchor": "def _status_for("},
    {"frm": "ledger", "to": "mask", "label": "build_answer_envelope masks before return", "anchor_path": "packages/executor/dms_executor/envelope.py", "anchor": "masked = fail_closed_mask_payload("},
    {"frm": "contract_ask", "to": "mask", "label": "map_ask_response_to_envelope builds the envelope", "anchor_path": "packages/executor/dms_executor/__init__.py", "anchor": "def map_ask_response_to_envelope("},
    {"frm": "mask", "to": "serve", "label": "masked text, rows, values, sql", "anchor_path": "packages/executor/dms_executor/envelope.py", "anchor": 'sql_out = sql_used'},
    {"frm": "serve", "to": "score_curated", "label": "harness posts the same envelope. Not inside chat_ask.", "anchor_path": "scripts/score_curated.py", "anchor": "def judge_detailed("},
    {"frm": "serve", "to": "score_bird", "label": "BIRD harness, same judge", "anchor_path": "scripts/score_bird.py", "anchor": "verdict = judge(scored, env)"},
    {"frm": "score_bird", "to": "score_curated", "label": "imports judge", "anchor_path": "scripts/score_bird.py", "anchor": "from score_curated import"},
]

QUOTED_RE = re.compile(r"""["']([^"'\n]{2,80})["']""")
WORD_RE = re.compile(r"\b(ABSTAIN|INVALID|WRONG|ORACLE_ERROR|RATE_LIMIT|GOLD_ERROR|LAYER|L0_CERTIFIED|L1_GOVERNED_METRIC|L2_VALIDATED)\b")
DENY = frozenset(
    {
        "TRUE",
        "FALSE",
        "NULL",
        "NONE",
        "SELECT",
        "FROM",
        "WHERE",
        "AND",
        "OR",
        "NOT",
        "AS",
        "ON",
        "JOIN",
        "LEFT",
        "INNER",
        "the",
        "and",
        "for",
        "utf-8",
    }
)

SECRET_RE = re.compile(
    r"(?i)(api[_-]?key|secret|password|token|bearer)\s*[:=]\s*['\"][^'\"]{6,}['\"]"
)


def git_sha() -> str:
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return proc.stdout.strip()


def read_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def find_line(lines: list[str], anchor: str) -> int | None:
    hits = [i for i, line in enumerate(lines, start=1) if anchor in line]
    if not hits:
        return None
    return hits[0]


def line_text(lines: list[str], lineno: int) -> str:
    raw = lines[lineno - 1].strip()
    raw = SECRET_RE.sub(r"\1=<redacted>", raw)
    if len(raw) > 180:
        raw = raw[:177] + "..."
    return raw


def keep_token(token: str) -> bool:
    if token in DENY or " " in token:
        return False
    if re.search(r"(?i)(sk-|ov_|gsk_|bearer |api_key|password|dms-demo-viewer)", token):
        return False
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_:.+#-]{2,70}", token):
        return False
    if token.isupper() and "_" not in token and len(token) < 6:
        return False
    return "_" in token or ":" in token or "+" in token or token.isupper() or token.startswith("DMS")


# Quoted names that show up next to real reasons and are not themselves reasons.
SCAN_SKIP = frozenset(
    {
        "event_id",
        "task_id",
        "min_rows",
        "as_of",
        "base_url",
        "filled_template",
        "session_id",
        "space_id",
        "answer_id",
        "run_id",
        "utf-8",
    }
)


def scan_tokens(lines: list[str], start: int, span: int) -> list[dict]:
    end = min(len(lines), start + span - 1)
    found: list[dict] = []
    seen: set[str] = set()
    for lineno in range(start, end + 1):
        line = lines[lineno - 1]
        cands = [m.group(1) for m in QUOTED_RE.finditer(line)]
        cands.extend(WORD_RE.findall(line))
        for token in cands:
            if token in seen or token in SCAN_SKIP or not keep_token(token):
                continue
            seen.add(token)
            found.append({"token": token, "line": lineno})
    return found


def files_for(node: dict) -> list[str]:
    paths: list[str] = []
    for cite in node["citations"]:
        if cite["path"] not in paths:
            paths.append(cite["path"])
    for scan in node.get("scans") or []:
        if scan["path"] not in paths:
            paths.append(scan["path"])
    return paths


def line_has_token(line: str, token: str) -> bool:
    """Whole token. 'name' does not match 'names'. Quotes are not part of the token."""
    return (
        re.search(
            r"(?<![A-Za-z0-9_])" + re.escape(token) + r"(?![A-Za-z0-9_])",
            line,
        )
        is not None
    )


def locate_token(paths: list[str], token: str, cache: dict[str, list[str]]) -> tuple[str, int] | None:
    hits: list[tuple[int, str, int]] = []
    for rel in paths:
        lines = cache.get(rel) or []
        for lineno, line in enumerate(lines, start=1):
            if not line_has_token(line, token):
                continue
            score = 0
            if any(mark in line for mark in ("return ", "JudgeResult", "Refusal", "Violation")):
                score += 2
            if f'"{token}"' in line or f"'{token}'" in line:
                score += 1
            hits.append((score, rel, lineno))
    if not hits:
        return None
    hits.sort(key=lambda item: (-item[0], item[2]))
    return hits[0][1], hits[0][2]


def absent_hits(globs: list[str], needles: list[str]) -> list[dict]:
    hits: list[dict] = []
    files: list[Path] = []
    for pattern in globs:
        files.extend(ROOT.glob(pattern))
    for path in files:
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for needle in needles:
            if needle in text:
                rel = path.relative_to(ROOT).as_posix()
                hits.append({"path": rel, "needle": needle})
    return hits


def resolve() -> dict:
    sha = git_sha()
    problems: list[str] = []
    unverified: list[dict] = []
    nodes_out: list[dict] = []
    file_cache: dict[str, list[str]] = {}

    def lines_for(rel: str) -> list[str] | None:
        if rel not in file_cache:
            path = ROOT / rel
            if not path.is_file():
                file_cache[rel] = []
            else:
                file_cache[rel] = read_lines(path)
        got = file_cache[rel]
        return got or None

    for node in NODES:
        citations = []
        node_bad = False
        for cite in node["citations"]:
            rel = cite["path"]
            lines = lines_for(rel)
            lineno = find_line(lines, cite["anchor"]) if lines else None
            if lineno is None:
                node_bad = True
                problems.append(f"{node['id']}: missing anchor in {rel}: {cite['anchor']}")
                citations.append(
                    {
                        "path": rel,
                        "anchor": cite["anchor"],
                        "line": None,
                        "text": "",
                        "ok": False,
                    }
                )
            else:
                citations.append(
                    {
                        "path": rel,
                        "anchor": cite["anchor"],
                        "line": lineno,
                        "text": line_text(lines or [], lineno),
                        "ok": True,
                    }
                )
        reasons: list[dict] = []
        seen_tokens: set[str] = set()
        for scan in node.get("scans") or []:
            lines = lines_for(scan["path"])
            lineno = find_line(lines, scan["anchor"]) if lines else None
            if lineno is None or lines is None:
                node_bad = True
                problems.append(
                    f"{node['id']}: scan anchor missing in {scan['path']}: {scan['anchor']}"
                )
                continue
            for item in scan_tokens(lines, lineno, int(scan.get("span") or SPAN)):
                if item["token"] in seen_tokens:
                    continue
                seen_tokens.add(item["token"])
                reasons.append(
                    {"token": item["token"], "path": scan["path"], "line": item["line"]}
                )
        for token in node.get("require") or []:
            if token in seen_tokens:
                continue
            hit = locate_token(files_for(node), token, file_cache)
            if hit is None:
                continue
            seen_tokens.add(token)
            reasons.append({"token": token, "path": hit[0], "line": hit[1]})
        missing = [tok for tok in node.get("require") or [] if tok not in seen_tokens]
        if missing:
            node_bad = True
            problems.append(f"{node['id']}: required tokens not in cited files: {', '.join(missing)}")
        declared = node.get("status")
        if declared == "absent":
            hits = absent_hits(node.get("absent_globs") or [], node.get("absent_needles") or [])
            if hits:
                node_bad = True
                problems.append(f"{node['id']}: expected absent, found {hits[:4]}")
                status = "unverified"
            else:
                status = "absent"
        elif node_bad:
            status = "unverified"
        else:
            status = "verified"
        lane = node.get("lane_name_unverified")
        if lane and status == "verified":
            status = "partial"
        if status != "verified":
            unverified.append(
                {
                    "id": node["id"],
                    "title": node["title"],
                    "status": status,
                    "lane_name": lane,
                    "on_ask_path": node["on_ask_path"],
                    "missing_tokens": missing,
                    "why": node["why"],
                }
            )
        nodes_out.append(
            {
                "id": node["id"],
                "stage": node["stage"],
                "title": node["title"],
                "why": node["why"],
                "on_ask_path": node["on_ask_path"],
                "status": status,
                "lane_name_unverified": lane,
                "citations": citations,
                "reasons": reasons,
            }
        )

    edges_out = []
    ids = {n["id"] for n in nodes_out}
    for edge in EDGES:
        if edge["frm"] not in ids or edge["to"] not in ids:
            problems.append(f"edge {edge['frm']}->{edge['to']} names an unknown node")
            continue
        lines = lines_for(edge["anchor_path"])
        lineno = find_line(lines, edge["anchor"]) if lines else None
        ok = lineno is not None
        if not ok:
            problems.append(
                f"edge {edge['frm']}->{edge['to']}: missing anchor in {edge['anchor_path']}: {edge['anchor']}"
            )
        edges_out.append(
            {
                "from": edge["frm"],
                "to": edge["to"],
                "label": edge["label"],
                "path": edge["anchor_path"],
                "line": lineno,
                "text": line_text(lines, lineno) if ok and lines and lineno else "",
                "ok": ok,
            }
        )
        if not ok:
            unverified.append(
                {
                    "id": f"edge:{edge['frm']}->{edge['to']}",
                    "title": edge["label"],
                    "status": "unverified",
                    "lane_name": None,
                    "on_ask_path": True,
                    "missing_tokens": [edge["anchor"]],
                    "why": "Edge anchor was not found in the tree.",
                }
            )

    inventory = {
        "commit": sha,
        "repo": "netie/dms",
        "generated_by": "tools/pipeline-map/regen.py",
        "nodes": nodes_out,
        "edges": edges_out,
        "unverified": unverified,
        "problems": problems,
    }
    return inventory


def write_outputs(inventory: dict) -> None:
    json_path = OUT_DIR / "inventory.json"
    js_path = OUT_DIR / "inventory.js"
    payload = json.dumps(inventory, indent=2)
    json_path.write_text(payload + "\n", encoding="utf-8")
    js_path.write_text(
        "/* Generated by tools/pipeline-map/regen.py. Do not hand-edit. */\n"
        "window.PIPELINE_INVENTORY = "
        + payload
        + ";\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="exit 1 on unresolved anchors")
    args = parser.parse_args()
    inventory = resolve()
    write_outputs(inventory)
    n = len(inventory["nodes"])
    e = len(inventory["edges"])
    u = len(inventory["unverified"])
    print(f"commit {inventory['commit']}")
    print(f"nodes {n}  edges {e}  unverified {u}  problems {len(inventory['problems'])}")
    for item in inventory["unverified"]:
        print(f"  {item['status']:12} {item['id']}")
    for problem in inventory["problems"]:
        print(f"PROBLEM {problem}")
    if args.check and inventory["problems"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
