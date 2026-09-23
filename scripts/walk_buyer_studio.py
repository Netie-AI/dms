"""AGI-BUYER-WALK-01 (#236): steward Studio buyer walk (ask + refuse).

5-day supply-chain AGI-for-DB demo under EPIC-INSIGHTS-UX #178.

  python scripts/walk_buyer_studio.py --self-check
  STUDIO_ORIGIN=https://studio.netie.ai \\
  DMS_API_BASE=https://studio.netie.ai/api \\
  python scripts/walk_buyer_studio.py

Asks POST ``ask_path=generative`` so a hit must be ``plan_source=ontology_plan``
or honest ABSTAIN. Exact-match L0 does not count. The planted refuse must
ABSTAIN and print why. Artifacts cited: Studio receipt, ask envelope,
Excel export from that envelope (Cover-only is allowed). No invented charts.

No silent ``127.0.0.1:8090`` default. Unset env is CONFIG, not PASS.
Does not stamp #178 COMPLETE. Does not invent logos / ARR.
Live leftover is Platform: post the printed report on #178.

Exit: 0 PASS / 1 FAIL / 2 CONFIG / 3 BLOCKED
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import zipfile
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
PACK_PATH = ROOT / "tests" / "fixtures" / "buyer_walk" / "questions.yaml"
STUDIO_PAGE = ROOT / "apps" / "ui" / "src" / "pages" / "StudioPage.tsx"
CHAT_PAGE = ROOT / "apps" / "ui" / "src" / "pages" / "ChatPage.tsx"

EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_CONFIG = 2
EXIT_BLOCKED = 3

OWNER = "Platform/UX steward"
MIN_ASK = 5
MIN_REFUSE = 1
CONFIDENT = frozenset(
    {"L0_CERTIFIED", "L1_GOVERNED_METRIC", "L2_VALIDATED", "L2_ANOMALOUS"}
)
RESOLVED = frozenset({"ONTOLOGY", "ABSTAIN"})
ARTIFACTS = ("receipt", "ask", "export")
REQUIRED_COPY = (
    "Buyer walk",
    "5-day AGI-for-DB",
    "honest ABSTAIN",
    "receipt",
    "Download Excel",
    "Not COMPLETE",
    "No buyer logos",
    "No ARR",
    "invent charts",
)


class ConfigError(Exception):
    pass


@dataclass
class Config:
    studio_origin: str
    api_base: str
    timeout: float = 120.0


@dataclass
class Check:
    name: str
    status: str
    detail: str
    error_type: str | None = None
    owner: str | None = None


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)
    artifacts: dict[str, str] = field(default_factory=dict)
    resolved: int = 0
    refuse_shown: int = 0

    def add(self, check: Check) -> None:
        extra = f" error.type={check.error_type}" if check.error_type else ""
        who = f" owner={check.owner}" if check.owner else ""
        print(f"  [{check.status}] {check.name}{extra}{who}")
        if check.detail:
            print(f"         {check.detail}")
        self.checks.append(check)

    def verdict(self) -> str:
        if any(c.status == "FAIL" for c in self.checks):
            return "FAIL"
        if any(c.status == "BLOCKED" for c in self.checks):
            return "BLOCKED"
        if any(c.status == "CONFIG" for c in self.checks):
            return "CONFIG"
        if self.resolved < MIN_ASK or self.refuse_shown < MIN_REFUSE:
            return "FAIL"
        if any(name not in self.artifacts for name in ARTIFACTS):
            return "FAIL"
        if not self.checks:
            return "BLOCKED"
        return "PASS"


def _strip(value: str | None) -> str:
    return (value or "").strip()


def _forbid_wildcard(label: str, url: str) -> None:
    host = (urlparse(url).hostname or "").lower()
    if host in {"0.0.0.0", "*", "::", "[::]"}:
        raise ConfigError(
            f"{label} hostname {host!r} is a public bind. DMS :8090 stays "
            "127.0.0.1. Do not use 0.0.0.0."
        )


def load_config(env: dict[str, str], *, origin: str = "", url: str = "") -> Config:
    studio = _strip(origin) or _strip(env.get("STUDIO_ORIGIN"))
    api = _strip(url) or _strip(env.get("DMS_API_BASE") or env.get("STUDIO_API_BASE"))
    missing: list[str] = []
    if not studio:
        missing.append("STUDIO_ORIGIN")
    if not api:
        missing.append("DMS_API_BASE")
    if missing:
        raise ConfigError(
            "unset "
            + ", ".join(missing)
            + ". No silent 127.0.0.1:8090 default. Set STUDIO_ORIGIN and "
            "DMS_API_BASE (Studio /api). Laptop: export them to the local "
            "origin; do not invent PASS."
        )
    _forbid_wildcard("STUDIO_ORIGIN", studio)
    _forbid_wildcard("DMS_API_BASE", api)
    return Config(studio_origin=studio.rstrip("/"), api_base=api.rstrip("/"))


def load_pack(path: Path = PACK_PATH) -> dict[str, Any]:
    import yaml

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError("buyer walk pack is not an object")
    return data


def questions(pack: dict[str, Any]) -> list[dict[str, Any]]:
    rows = list(pack.get("questions") or [])
    out: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, dict) and row.get("id") and row.get("question"):
            out.append(row)
    return out


def space_id(pack: dict[str, Any], case: dict[str, Any]) -> str:
    spaces = dict(pack.get("spaces") or {})
    key = str(case.get("space") or "finance")
    return str(spaces.get(key) or spaces.get("finance") or "")


def is_confident(env: dict[str, Any]) -> bool:
    return str(env.get("badge") or "") in CONFIDENT and not env.get("abstained")


def is_abstain(env: dict[str, Any]) -> bool:
    return bool(env.get("abstained")) or str(env.get("badge") or "") == "ABSTAIN"


def plan_source(env: dict[str, Any]) -> str:
    raw = str(env.get("plan_source") or "").strip().lower()
    return raw if raw in {"ontology_plan", "bind_plan", "other"} else "other"


def judge_ask(env: dict[str, Any]) -> str:
    """ONTOLOGY | ABSTAIN | FAIL_NOT_ONTOLOGY | FAIL_FALLBACK | FAIL."""
    if env.get("demo_fallback_used"):
        return "FAIL_FALLBACK"
    if is_abstain(env) and not is_confident(env):
        return "ABSTAIN"
    if is_confident(env) and plan_source(env) == "ontology_plan":
        return "ONTOLOGY"
    if is_confident(env):
        return "FAIL_NOT_ONTOLOGY"
    return "FAIL"


def judge_refuse(env: dict[str, Any]) -> str:
    """ABSTAIN | WRONG | FAIL_FALLBACK | FAIL."""
    if env.get("demo_fallback_used"):
        return "FAIL_FALLBACK"
    if is_confident(env):
        return "WRONG"
    if is_abstain(env):
        return "ABSTAIN"
    return "FAIL"


def invented_claim_hits(text: str) -> list[str]:
    hits: list[str] = []
    for match in re.finditer(r"\bCOMPLETE\b", text):
        before = text[max(0, match.start() - 36) : match.start()].lower()
        allowed = ("not ", "no invent", "never ", "don't ", "do not ", "not_")
        if not any(token in before for token in allowed):
            hits.append("bare COMPLETE")
    if re.search(r"ARR\s*[:=$]|\$[\d.]+[MBK]?\s*ARR|annual recurring", text, re.I):
        hits.append("invented ARR")
    if re.search(r"(logo\.png|customer logo|Fortune 500|Acme Corp)", text, re.I):
        hits.append("invented logo")
    if re.search(r"(dashboard\.png|fake screenshot|screenshot\.png)", text, re.I):
        hits.append("invented chart/screenshot")
    return sorted(set(hits))


def _http(
    method: str,
    url: str,
    *,
    json_body: dict[str, Any] | None = None,
    timeout: float,
) -> Any:
    """httpx/curl-class fetch. urllib CF1010s studio.netie.ai (SCORE-CLIENT-01)."""
    import httpx

    kwargs: dict[str, Any] = {"timeout": timeout, "follow_redirects": True}
    if json_body is not None:
        kwargs["json"] = json_body
    return httpx.request(method, url, **kwargs)


def error_type_of(*, status: int | None = None, text: str | None = None, exc: BaseException | None = None) -> str:
    blob = (text or "").lower()
    if status == 403 and ("error code: 1010" in blob or "error 1010" in blob):
        return "cf1010"
    if exc is not None:
        name = type(exc).__name__.lower()
        if "timeout" in name:
            return "timeout"
        if "connect" in name:
            return "connect"
        return name
    if status in {401, 403}:
        return "auth"
    if status in {502, 503, 504}:
        return "upstream"
    return "http"


def _snippet(text: str, n: int = 240) -> str:
    return re.sub(r"\s+", " ", text or "")[:n]


def check_copy(report: Report) -> None:
    studio = STUDIO_PAGE.read_text(encoding="utf-8")
    chat = CHAT_PAGE.read_text(encoding="utf-8")
    pack = load_pack()
    missing = [phrase for phrase in REQUIRED_COPY if phrase not in studio]
    if missing:
        report.add(
            Check(
                "Studio copy phrases",
                "FAIL",
                "missing " + ", ".join(missing),
                error_type="studio_copy",
            )
        )
    else:
        report.add(Check("Studio copy phrases", "PASS", "buyer-walk honesty + artifacts"))
    hits = invented_claim_hits(studio) + invented_claim_hits(json.dumps(pack))
    if hits:
        report.add(
            Check(
                "no invented COMPLETE/ARR/logos/charts",
                "FAIL",
                ", ".join(hits),
                error_type="invented_claim",
            )
        )
    else:
        report.add(
            Check("no invented COMPLETE/ARR/logos/charts", "PASS", "pack + Studio copy clean")
        )
    if "draftQuestion" not in chat:
        report.add(
            Check(
                "Chat prefill from Studio",
                "FAIL",
                "ChatPage must consume draftQuestion from Studio walk clicks",
                error_type="chat_prefill",
            )
        )
    else:
        report.add(Check("Chat prefill from Studio", "PASS", "draftQuestion"))
    if "data-testid=\"buyer-walk\"" not in studio:
        report.add(
            Check(
                "Studio buyer-walk panel",
                "FAIL",
                "missing data-testid=buyer-walk",
                error_type="studio_panel",
            )
        )
    else:
        report.add(Check("Studio buyer-walk panel", "PASS", "data-testid=buyer-walk"))


def check_pack(report: Report) -> None:
    pack = load_pack()
    rows = questions(pack)
    ids = [str(row["id"]) for row in rows]
    asks = [row for row in rows if str(row.get("kind") or "ask") == "ask"]
    refuses = [row for row in rows if str(row.get("kind")) == "refuse"]
    detail = f"asks={len(asks)} refuses={len(refuses)} n={len(rows)}"
    ok = (
        len(ids) == len(set(ids))
        and len(asks) >= MIN_ASK
        and len(refuses) >= MIN_REFUSE
        and all(str(row.get("why") or "").strip() for row in refuses)
        and set(pack.get("artifacts") or []) >= set(ARTIFACTS)
    )
    report.add(
        Check(
            "buyer walk pack",
            "PASS" if ok else "FAIL",
            detail if ok else detail + " (need >=5 ask, >=1 refuse with why, artifacts)",
            error_type=None if ok else "pack",
        )
    )
    studio = STUDIO_PAGE.read_text(encoding="utf-8")
    missing_q = [str(row["question"]) for row in rows if str(row["question"]) not in studio]
    report.add(
        Check(
            "Studio lists pack questions",
            "PASS" if not missing_q else "FAIL",
            "all pack questions in StudioPage" if not missing_q else "missing " + "; ".join(missing_q[:3]),
            error_type=None if not missing_q else "studio_questions",
        )
    )
    why = str(refuses[0].get("why") or "") if refuses else ""
    if why and why not in studio:
        report.add(
            Check(
                "Studio shows refuse why",
                "FAIL",
                "refuse why must appear in Studio copy",
                error_type="refuse_why",
            )
        )
    else:
        report.add(Check("Studio shows refuse why", "PASS", "honest ABSTAIN why"))


def check_judges(report: Report) -> None:
    onto = {
        "badge": "L2_VALIDATED",
        "abstained": False,
        "plan_source": "ontology_plan",
        "answer_id": "ans_onto",
        "audit_id": "aud_onto",
    }
    abstain = {"badge": "ABSTAIN", "abstained": True, "text": "no as-of month"}
    l0 = {"badge": "L0_CERTIFIED", "abstained": False, "answer_id": "ans_l0"}
    green_refuse = {"badge": "L2_VALIDATED", "abstained": False, "plan_source": "ontology_plan"}
    fallback = {**onto, "demo_fallback_used": True}
    ok = (
        judge_ask(onto) == "ONTOLOGY"
        and judge_ask(abstain) == "ABSTAIN"
        and judge_ask(l0) == "FAIL_NOT_ONTOLOGY"
        and judge_ask(fallback) == "FAIL_FALLBACK"
        and judge_refuse(abstain) == "ABSTAIN"
        and judge_refuse(green_refuse) == "WRONG"
    )
    report.add(
        Check(
            "walk judges",
            "PASS" if ok else "FAIL",
            "ontology or ABSTAIN; L0 is not ontology; green refuse is WRONG",
            error_type=None if ok else "judge",
        )
    )


def cite_envelope(env: dict[str, Any]) -> str:
    aid = env.get("audit_id") or env.get("answer_id") or ""
    return (
        f"badge={env.get('badge')} abstained={env.get('abstained')} "
        f"plan_source={env.get('plan_source') or '(none)'} "
        f"answer_id={env.get('answer_id') or '(none)'} audit_id={aid or '(none)'} "
        f"rows={len(env.get('rows') or [])}"
    )


def xlsx_is_real(data: bytes) -> bool:
    if not data.startswith(b"PK"):
        return False
    try:
        names = zipfile.ZipFile(BytesIO(data)).namelist()
    except zipfile.BadZipFile:
        return False
    return "xl/workbook.xml" in names


def check_origin(cfg: Config, report: Report) -> None:
    url = cfg.studio_origin + "/studio"
    try:
        resp = _http("GET", url, timeout=min(cfg.timeout, 20.0))
    except Exception as exc:  # noqa: BLE001
        report.add(
            Check(
                "GET /studio",
                "BLOCKED",
                str(exc)[:300],
                error_type=error_type_of(exc=exc),
                owner=OWNER,
            )
        )
        return
    body = resp.text or ""
    ok = resp.status_code == 200 and ("studio" in body.lower() or "netie" in body.lower())
    report.add(
        Check(
            "GET /studio",
            "PASS" if ok else "FAIL",
            f"status={resp.status_code} {_snippet(body, 160)}",
            error_type=None if ok else error_type_of(status=resp.status_code, text=body),
        )
    )


def check_health(cfg: Config, report: Report) -> None:
    url = cfg.api_base + "/health"
    try:
        resp = _http("GET", url, timeout=min(cfg.timeout, 15.0))
    except Exception as exc:  # noqa: BLE001
        report.add(
            Check(
                "GET /health",
                "BLOCKED",
                str(exc)[:300],
                error_type=error_type_of(exc=exc),
                owner=OWNER,
            )
        )
        return
    body: dict[str, Any]
    try:
        parsed = resp.json()
        body = parsed if isinstance(parsed, dict) else {}
    except Exception:  # noqa: BLE001
        body = {}
    ok = resp.status_code == 200 and bool(body.get("status") or body.get("product"))
    if body.get("demo_fallback") is True:
        report.add(
            Check(
                "GET /health",
                "FAIL",
                "demo_fallback=true is a lying affordance unless the UI banner is unmissable",
                error_type="demo_fallback",
            )
        )
        return
    report.add(
        Check(
            "GET /health",
            "PASS" if ok else "FAIL",
            f"status={resp.status_code} product={body.get('product')} "
            f"ask_mode={body.get('ask_mode')} demo_fallback={body.get('demo_fallback')}",
            error_type=None if ok else error_type_of(status=resp.status_code, text=resp.text),
        )
    )


def check_receipt(cfg: Config, pack: dict[str, Any], report: Report) -> None:
    sid = str((pack.get("spaces") or {}).get("finance") or "")
    url = cfg.api_base + "/v1/library/tree"
    if sid:
        url += f"?space_id={sid}"
    try:
        resp = _http("GET", url, timeout=min(cfg.timeout, 30.0))
    except Exception as exc:  # noqa: BLE001
        report.add(
            Check(
                "artifact receipt (library tree)",
                "BLOCKED",
                str(exc)[:300],
                error_type=error_type_of(exc=exc),
                owner=OWNER,
            )
        )
        return
    body: dict[str, Any]
    try:
        parsed = resp.json()
        body = parsed if isinstance(parsed, dict) else {}
    except Exception:  # noqa: BLE001
        body = {}
    nodes = body.get("nodes") if isinstance(body.get("nodes"), list) else []
    if resp.status_code != 200:
        report.add(
            Check(
                "artifact receipt (library tree)",
                "BLOCKED",
                f"status={resp.status_code} {_snippet(resp.text or '', 200)}",
                error_type=error_type_of(status=resp.status_code, text=resp.text),
                owner=OWNER,
            )
        )
        return
    space_name = str(body.get("space_name") or "Finance")
    detail = f"space={space_name} nodes={len(nodes)} (Studio ingest receipt is the buyer-visible twin)"
    report.add(Check("artifact receipt (library tree)", "PASS", detail))
    report.artifacts["receipt"] = detail


def _ask(
    cfg: Config,
    question: str,
    space: str,
    *,
    ask_path: str | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"question": question, "space_id": space}
    if ask_path:
        payload["ask_path"] = ask_path
    resp = _http(
        "POST",
        cfg.api_base + "/v1/chat/ask",
        json_body=payload,
        timeout=cfg.timeout,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"status={resp.status_code} {_snippet(resp.text or '', 300)}")
    body = resp.json()
    if not isinstance(body, dict) or "badge" not in body:
        raise RuntimeError("ask response is not an envelope")
    if body.get("abstained") is True and body.get("badge") != "ABSTAIN":
        raise RuntimeError("green badge on abstention prose (E1) -- P0")
    return body


def check_asks(cfg: Config, pack: dict[str, Any], report: Report) -> list[dict[str, Any]]:
    envelopes: list[dict[str, Any]] = []
    for case in questions(pack):
        kind = str(case.get("kind") or "ask")
        qid = str(case["id"])
        question = str(case["question"])
        sid = space_id(pack, case)
        path = "generative" if kind == "ask" else "product"
        try:
            env = _ask(cfg, question, sid, ask_path=path)
        except Exception as exc:  # noqa: BLE001
            report.add(
                Check(
                    f"ask {qid}",
                    "BLOCKED",
                    str(exc)[:400],
                    error_type=error_type_of(exc=exc),
                    owner=OWNER,
                )
            )
            continue
        envelopes.append(env)
        cited = cite_envelope(env)
        if kind == "refuse":
            grade = judge_refuse(env)
            why = str(case.get("why") or "").strip()
            if grade == "ABSTAIN":
                report.refuse_shown += 1
                report.add(
                    Check(
                        f"refuse {qid}",
                        "PASS",
                        f"{cited} why={why}",
                    )
                )
            else:
                report.add(
                    Check(
                        f"refuse {qid}",
                        "FAIL",
                        f"{grade} {cited} why={why}",
                        error_type="refuse_green" if grade == "WRONG" else grade.lower(),
                    )
                )
            continue
        grade = judge_ask(env)
        if grade in RESOLVED:
            report.resolved += 1
            report.add(Check(f"ask {qid}", "PASS", f"{grade} {cited}"))
        elif grade == "FAIL_FALLBACK":
            report.add(
                Check(
                    f"ask {qid}",
                    "FAIL",
                    f"{grade} {cited}",
                    error_type="demo_fallback",
                )
            )
        else:
            report.add(
                Check(
                    f"ask {qid}",
                    "FAIL",
                    f"{grade} {cited} (need ontology_plan or honest ABSTAIN)",
                    error_type=grade.lower(),
                )
            )
    if envelopes:
        report.artifacts["ask"] = cite_envelope(envelopes[0])
    return envelopes


def check_export(cfg: Config, envelopes: list[dict[str, Any]], report: Report) -> None:
    usable = [env for env in envelopes if env.get("answer_id") and env.get("badge")]
    if not usable:
        report.add(
            Check(
                "artifact export",
                "FAIL",
                "no ask envelope to export; will not invent an xlsx",
                error_type="export_missing",
            )
        )
        return
    env = usable[0]
    try:
        resp = _http(
            "POST",
            cfg.api_base + "/v1/chat/export.xlsx",
            json_body={"envelope": env},
            timeout=min(cfg.timeout, 30.0),
        )
    except Exception as exc:  # noqa: BLE001
        report.add(
            Check(
                "artifact export",
                "BLOCKED",
                str(exc)[:300],
                error_type=error_type_of(exc=exc),
                owner=OWNER,
            )
        )
        return
    if resp.status_code != 200:
        report.add(
            Check(
                "artifact export",
                "FAIL",
                f"status={resp.status_code} {_snippet(resp.text or '', 200)}",
                error_type=error_type_of(status=resp.status_code, text=resp.text),
            )
        )
        return
    data = resp.content or b""
    if not xlsx_is_real(data):
        report.add(
            Check(
                "artifact export",
                "FAIL",
                "response is not a real xlsx workbook",
                error_type="export_not_xlsx",
            )
        )
        return
    name = f"dms_answer_{env.get('answer_id')}.xlsx"
    detail = f"{name} bytes={len(data)} from envelope {env.get('answer_id')} (copied rows, not an invented chart)"
    report.add(Check("artifact export", "PASS", detail))
    report.artifacts["export"] = detail


def self_check() -> int:
    print("AGI-BUYER-WALK-01 self-check. No network. Not #178 COMPLETE.")
    report = Report()
    try:
        load_config({})
        report.add(Check("empty env CONFIG", "FAIL", "empty env must raise", error_type="config"))
    except ConfigError as exc:
        msg = str(exc)
        ok = "STUDIO_ORIGIN" in msg and "DMS_API_BASE" in msg and "127.0.0.1:8090" in msg
        report.add(
            Check(
                "empty env CONFIG",
                "PASS" if ok else "FAIL",
                msg[:240],
                error_type=None if ok else "config",
            )
        )
    try:
        load_config(
            {
                "STUDIO_ORIGIN": "http://0.0.0.0:3000",
                "DMS_API_BASE": "http://127.0.0.1:8090",
            }
        )
        report.add(Check("wildcard origin CONFIG", "FAIL", "0.0.0.0 must raise", error_type="config"))
    except ConfigError as exc:
        report.add(
            Check(
                "wildcard origin CONFIG",
                "PASS" if "0.0.0.0" in str(exc) else "FAIL",
                str(exc)[:200],
            )
        )
    check_pack(report)
    check_copy(report)
    check_judges(report)
    src = Path(__file__).read_text(encoding="utf-8")
    import_lines = [
        line.split("#", 1)[0].strip()
        for line in src.splitlines()
        if line.split("#", 1)[0].strip().startswith(("import ", "from "))
    ]
    dirty = [
        line
        for line in import_lines
        if any(tok in line.lower() for tok in ("dms_", "cortex", "ontology", "freeroute"))
    ]
    report.add(
        Check(
            "walk script stays off engine compile",
            "PASS" if not dirty else "FAIL",
            "HTTP + Studio copy only" if not dirty else "imports " + ", ".join(dirty),
            error_type=None if not dirty else "scope",
        )
    )
    # Offline artifact citation template -- live run fills the same keys.
    report.artifacts = {name: f"cited:{name}" for name in ARTIFACTS}
    report.resolved = MIN_ASK
    report.refuse_shown = MIN_REFUSE
    if any(c.status == "FAIL" for c in report.checks):
        print("VERDICT: FAIL")
        print("Do not invent COMPLETE.")
        return EXIT_FAIL
    print("self-check ok")
    print("VERDICT: PASS")
    print("Not #178 COMPLETE. Live walk is Platform.")
    return EXIT_PASS


def run(env: dict[str, str], *, origin: str = "", url: str = "") -> int:
    print("AGI-BUYER-WALK-01 Studio buyer walk (#236). Not #178 COMPLETE.")
    print("No invented charts / logos / ARR. Ontology path or honest ABSTAIN.")
    try:
        cfg = load_config(env, origin=origin, url=url)
    except ConfigError as exc:
        print(f"CONFIG: {exc}")
        print("VERDICT: CONFIG")
        return EXIT_CONFIG
    print(f"STUDIO_ORIGIN={cfg.studio_origin}")
    print(f"DMS_API_BASE={cfg.api_base}")
    pack = load_pack()
    report = Report()
    check_origin(cfg, report)
    check_health(cfg, report)
    check_receipt(cfg, pack, report)
    envelopes = check_asks(cfg, pack, report)
    check_export(cfg, envelopes, report)
    print("-" * 60)
    print(f"resolved={report.resolved} (need >={MIN_ASK} ontology_plan or ABSTAIN)")
    print(f"refuse_shown={report.refuse_shown} (need >={MIN_REFUSE})")
    print("artifacts: " + ", ".join(f"{k}={v}" for k, v in report.artifacts.items()) or "(none)")
    verdict = report.verdict()
    if verdict == "PASS" and (report.resolved < MIN_ASK or report.refuse_shown < MIN_REFUSE):
        verdict = "FAIL"
    print("=" * 60)
    print(f"VERDICT: {verdict}")
    if verdict != "PASS":
        print(f"Do not invent PASS. Owner for live leftover: {OWNER}.")
        print("Do not invent COMPLETE for #178.")
    else:
        print("PASS is this walk only. Not #178 COMPLETE.")
        print(f"Post this report on #178 ({OWNER}).")
    return {"PASS": EXIT_PASS, "FAIL": EXIT_FAIL, "BLOCKED": EXIT_BLOCKED, "CONFIG": EXIT_CONFIG}[
        verdict
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--self-check", action="store_true", help="pack + copy + judges; no network")
    parser.add_argument("--origin", default="", help="Studio origin (else STUDIO_ORIGIN)")
    parser.add_argument("--url", default="", help="API base (else DMS_API_BASE)")
    args = parser.parse_args(argv)
    if args.self_check:
        return self_check()
    return run(dict(os.environ), origin=args.origin, url=args.url)


if __name__ == "__main__":
    raise SystemExit(main())
