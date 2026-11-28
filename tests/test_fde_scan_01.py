"""FDE-SCAN-01: data-quality and PII scan over a landed Space."""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SCAN = ROOT / "scripts" / "fde" / "kits" / "scan.py"
PY_PATH = os.pathsep.join(
    [
        str(ROOT / "packages" / "core"),
        str(ROOT / "packages" / "executor"),
        str(ROOT / "scripts" / "fde"),
        str(ROOT / "scripts" / "verify"),
        str(ROOT / "tests" / "fde_scenarios"),
    ]
)

EMAIL = "ada.scan@example.com"
IC = "900101-14-1231"
CARD = "4111111111111111"
PASSWORD = "synthetic-password-not-a-secret-7f3a"
API_KEY = "sk-synthetic-SHOULD-NOT-LEAK-xyz9"
PLANTED = (EMAIL, IC, CARD, PASSWORD, API_KEY)


def _load():
    spec = importlib.util.spec_from_file_location("fde_scan_under_test", SCAN)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _env(**extra: str) -> dict[str, str]:
    base = os.environ.copy()
    base["PYTHONPATH"] = PY_PATH
    base.pop("DMS_FDE_KIT", None)
    base.update(extra)
    return base


def _run(eng: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCAN), "--engagement", str(eng)],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        cwd=str(ROOT),
    )


def _write_eng(root: Path, **body: object) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    data = {"tenant_id": "t-scan", "name": "scan-eng", "seed": 1}
    data.update(body)
    (root / "engagement.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    return root


def _latest(eng: Path) -> tuple[Path, dict, dict, str]:
    runs = sorted(p for p in (eng / "runs").glob("*-scan") if p.is_dir())
    assert runs, "scan wrote no run folder"
    run = runs[-1]
    report = json.loads((run / "report.json").read_text(encoding="utf-8"))
    decisions = yaml.safe_load((run / "decisions.yaml").read_text(encoding="utf-8"))
    md = (run / "report.md").read_text(encoding="utf-8")
    return run, report, decisions, md


def _outputs(eng: Path) -> str:
    parts = []
    for path in eng.rglob("*"):
        if path.is_file() and path.name != "engagement.yaml":
            parts.append(path.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(parts)


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _kinds(report: dict, levels: set[str] | None = None) -> set[str]:
    found = set()
    for check in report["checks"]:
        items = check["evidence"].get("items") or []
        for item in items:
            if levels is None or item.get("level") in levels:
                found.add(item.get("kind"))
    return found


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("flag", [None, "", "true", "01", " 1 ", "1 ", "yes"])
def test_flag_off_refuses_and_writes_nothing(tmp_path: Path, flag: str | None) -> None:
    eng = _write_eng(tmp_path / "eng", space={"warehouse": "missing.duckdb"})
    env = _env()
    if flag is not None:
        env["DMS_FDE_KIT"] = flag
    got = _run(eng, env)
    assert got.returncode == 2
    assert "fde_kit_disabled" in got.stderr
    assert not (eng / "runs").exists()
    assert not (eng / "decisions.yaml").exists()
    assert "missing.duckdb" not in got.stdout


def test_flag_on_digit_with_spaces_is_not_one(tmp_path: Path) -> None:
    eng = _write_eng(tmp_path / "eng")
    got = _run(eng, _env(DMS_FDE_KIT=" 1"))
    assert got.returncode == 2
    assert not (eng / "runs").exists()


def test_password_key_in_engagement_writes_nothing(tmp_path: Path) -> None:
    eng = tmp_path / "eng"
    eng.mkdir()
    (eng / "engagement.yaml").write_text(
        "tenant_id: t-scan\nname: scan-eng\npassword: " + PASSWORD + "\n",
        encoding="utf-8",
    )
    got = _run(eng, _env(DMS_FDE_KIT="1"))
    assert got.returncode == 2
    assert "fde_kit_config" in got.stderr
    assert PASSWORD not in got.stdout
    assert PASSWORD not in got.stderr
    assert not (eng / "runs").exists()


def test_missing_warehouse_is_skipped_not_pass(tmp_path: Path) -> None:
    eng = _write_eng(tmp_path / "eng", space={"warehouse": "no-such.duckdb"})
    got = _run(eng, _env(DMS_FDE_KIT="1"))
    assert got.returncode == 2
    _run_dir, report, _decisions, md = _latest(eng)
    assert report["status"] == "SKIPPED"
    assert report["checks"]
    assert all(check["status"] == "SKIPPED" for check in report["checks"])
    assert "PASS" not in {check["status"] for check in report["checks"]}
    assert "SKIPPED" in md


def test_rollup_of_no_checks_is_skipped() -> None:
    mod = _load()
    assert mod.rollup([]) == "SKIPPED"
    text = mod.render_md({"status": "SKIPPED", "checks": [], "schema": "netie.fde.report/1"})
    assert "SKIPPED" in text
    assert "No failed checks." in text


def test_render_md_follows_the_report_dict_only() -> None:
    mod = _load()
    report = {
        "schema": "netie.fde.report/1",
        "status": "FAIL",
        "checks": [
            {
                "id": "K4.05",
                "status": "FAIL",
                "severity": "high",
                "title": "Duplicate-load candidates",
                "fix": "Filter the repeat load.",
                "evidence": {"items": []},
            },
            {
                "id": "K4.03",
                "status": "PASS",
                "severity": "info",
                "title": "Distinct counts",
                "fix": "Counted.",
                "evidence": {},
            },
        ],
    }
    text = mod.render_md(report)
    assert text.index("K4.05") < text.index("K4.03")
    assert "Filter the repeat load." in text
    assert text.index("## Failed items") > text.index("| K4.05 |")


def _trap(
    path: Path,
    table: str,
    cols: tuple[str, ...],
    codes: tuple[str, str],
    tokens: tuple[str, str],
) -> None:
    import duckdb

    idc, tsc, payload, code, amt, case = cols
    con = duckdb.connect(str(path))
    con.execute(
        f"CREATE TABLE {_q(table)} ("
        f"{_q(idc)} BIGINT, {_q(tsc)} TIMESTAMP, {_q(payload)} INTEGER, "
        f"{_q(code)} VARCHAR, {_q(amt)} DECIMAL(12,2), {_q(case)} VARCHAR)"
    )
    code_a, code_b = codes
    token_lo, token_hi = tokens
    for i in range(20):
        token = token_lo if i < 18 else token_hi
        code_v = code_a if i < 10 else code_b
        amount = 100 if i < 10 else 400
        for k in range(2):
            con.execute(
                f"INSERT INTO {_q(table)} VALUES (?, ?, ?, ?, ?, ?)",
                [i * 2 + k, datetime(2026, 1, 1) + timedelta(days=i), i, code_v, amount, token],
            )
    con.close()


@pytest.mark.parametrize(
    ("table", "cols", "codes", "tokens"),
    [
        (
            "zz_alpha",
            ("zz_k", "zz_when", "zz_n", "zz_code", "zz_amt", "zz_case"),
            ("AAA", "BBB"),
            ("ab", "AB"),
        ),
        (
            "yy_one",
            ("yy_a", "yy_b", "yy_c", "yy_d", "yy_e", "yy_f"),
            ("QQQ", "RRR"),
            ("cd", "CD"),
        ),
    ],
)
def test_traps_are_found_without_hardcoded_names(
    tmp_path: Path,
    table: str,
    cols: tuple[str, ...],
    codes: tuple[str, str],
    tokens: tuple[str, str],
) -> None:
    db = tmp_path / "wh.duckdb"
    _trap(db, table, cols, codes, tokens)
    before = _sha256(db)
    eng = _write_eng(tmp_path / "eng", space={"warehouse": str(db)})
    got = _run(eng, _env(DMS_FDE_KIT="1"))
    assert got.returncode == 1, got.stderr
    assert _sha256(db) == before
    _run_dir, report, decisions, md = _latest(eng)
    assert report["schema"] == "netie.fde.report/1"
    assert report["kit"] == "scan"
    assert report["stage"] == "K4"
    assert report["status"] == "FAIL"
    assert {"duplicate_load", "mixed_unit", "casing"} <= _kinds(report, {"high", "critical"})
    actions = {row["action"] for row in decisions["rows"]}
    assert actions <= {"filter", "convert", "map", "accept"}
    assert "convert" in actions
    assert "filter" in actions
    assert "map" in actions
    first = report["checks"][0]
    assert first["status"] == "FAIL"
    assert first["fix"] in md
    assert f"| {first['id']} |" in md.split("## Failed items")[0]
    source = SCAN.read_text(encoding="utf-8")
    for word in (table, *cols):
        assert word not in source


def test_detector_source_has_no_domain_words() -> None:
    tree = ast.parse(SCAN.read_text(encoding="utf-8"))
    constants: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            constants.append(node.value.casefold())
    blob = "\n".join(constants)
    # ``usd`` is the report cost field from the shared schema, not a code list.
    for word in ("pharmacy", "pharma", "myr", "ringgit", "financial", "zz_alpha", "yy_one"):
        assert word not in blob


def test_planted_values_never_appear_and_classes_do(tmp_path: Path) -> None:
    import duckdb

    db = tmp_path / "pii.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE obs (a VARCHAR, b VARCHAR, c VARCHAR, d VARCHAR)")
    for _ in range(6):
        con.execute("INSERT INTO obs VALUES (?, ?, ?, ?)", [EMAIL, IC, CARD, PASSWORD])
    con.close()
    receipt = tmp_path / "connect.json"
    receipt.write_text(
        json.dumps({"password": PASSWORD, "api_key": API_KEY, "tables": []}),
        encoding="utf-8",
    )
    eng = _write_eng(
        tmp_path / "eng",
        space={"warehouse": str(db)},
        receipts={"connect": str(receipt)},
        password_env="FDE_SCAN_PASSWORD",
    )
    env = _env(DMS_FDE_KIT="1", FDE_SCAN_PASSWORD=PASSWORD)
    got = _run(eng, env)
    assert got.returncode == 1, got.stderr
    text = _outputs(eng)
    for planted in PLANTED:
        assert planted not in text
        assert planted not in got.stdout
        assert planted not in got.stderr
    _run_dir, report, decisions, _md = _latest(eng)
    classes: set[str] = set()
    for check in report["checks"]:
        if check["id"] == "K4.01":
            classes = {item.get("class") for item in check["evidence"].get("items") or []}
    assert {"email", "nric", "account"} <= classes
    assert all(row["action"] in {"filter", "convert", "map", "accept"} for row in decisions["rows"])
    blob = json.dumps(report) + json.dumps(decisions)
    for planted in PLANTED:
        assert planted not in blob


def test_same_seed_is_reproducible(tmp_path: Path) -> None:
    import duckdb

    db = tmp_path / "same.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE obs (a VARCHAR)")
    for i in range(6):
        con.execute("INSERT INTO obs VALUES (?)", [f"user{i}@example.com"])
    con.close()
    eng = _write_eng(tmp_path / "eng", space={"warehouse": str(db)}, seed=7)
    env = _env(DMS_FDE_KIT="1")
    assert _run(eng, env).returncode == 1
    _one, first, dec_a, _md = _latest(eng)
    assert _run(eng, env).returncode == 1
    _two, second, dec_b, _md2 = _latest(eng)
    assert first["checks"] == second["checks"]
    assert first["metrics"] == second["metrics"]
    assert dec_a == dec_b


def test_truncation_receipt_is_a_linked_critical_finding(tmp_path: Path) -> None:
    import duckdb

    db = tmp_path / "cap.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE small (id BIGINT)")
    con.execute("INSERT INTO small VALUES (1)")
    con.close()
    receipt = {
        "checks": [
            {
                "id": "K2.08",
                "status": "FAIL",
                "evidence": {
                    "table": "zz_cap_table",
                    "rows": 600000,
                    "cap": 500000,
                    "truncated": True,
                },
            }
        ],
        "password": PASSWORD,
        "api_key": API_KEY,
    }
    path = tmp_path / "connect.json"
    path.write_text(json.dumps(receipt), encoding="utf-8")
    eng = _write_eng(
        tmp_path / "eng",
        space={"warehouse": str(db)},
        receipts={"connect": str(path)},
    )
    got = _run(eng, _env(DMS_FDE_KIT="1"))
    assert got.returncode == 1, got.stderr
    _run_dir, report, decisions, md = _latest(eng)
    check = next(c for c in report["checks"] if c["id"] == "K4.10")
    assert check["status"] == "FAIL"
    assert check["severity"] == "critical"
    item = check["evidence"]["items"][0]
    assert item["table"] == "zz_cap_table"
    assert item["rows"] == 600000
    assert item["cap"] == 500000
    assert item["source_check"] == "K2.08"
    assert "Raise max_rows" in check["fix"]
    assert "Raise max_rows" in md
    assert any(
        row["kind"] == "truncation" and row["action"] == "filter" for row in decisions["rows"]
    )
    text = _outputs(eng)
    assert PASSWORD not in text
    assert API_KEY not in text
    assert "zz_cap_table" not in SCAN.read_text(encoding="utf-8")


def test_secret_rules_skip_when_module_absent(tmp_path: Path) -> None:
    import duckdb

    db = tmp_path / "s.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE s (id BIGINT)")
    con.execute("INSERT INTO s VALUES (1)")
    con.close()
    eng = _write_eng(tmp_path / "eng", space={"warehouse": str(db)})
    got = _run(eng, _env(DMS_FDE_KIT="1"))
    assert got.returncode in (0, 1)
    _run_dir, report, _decisions, _md = _latest(eng)
    check = next(c for c in report["checks"] if c["id"] == "K4.11")
    assert check["status"] == "SKIPPED"
    assert "not on this tree" in check["evidence"]["reason"]


def test_lake_promise_duplicate_key_fails(tmp_path: Path) -> None:
    import duckdb

    lake = tmp_path / "lake"
    lake.mkdir()
    parquet = lake / "t.parquet"
    con = duckdb.connect()
    con.execute(f"COPY (SELECT 1 AS id UNION ALL SELECT 1) TO '{parquet}' (FORMAT PARQUET)")
    con.close()
    manifest = lake / "manifest.json"
    manifest.write_text(
        json.dumps(
            [
                {
                    "database": "toy",
                    "tables": [
                        {
                            "schema": "main",
                            "table": "t",
                            "path": "t.parquet",
                            "extracted_rows": 2,
                            "declared_rows": 2,
                        }
                    ],
                    "primary_keys": {"main.t": ["id"]},
                    "foreign_keys": [],
                }
            ]
        ),
        encoding="utf-8",
    )
    eng = _write_eng(
        tmp_path / "eng",
        lake_manifest=str(manifest),
        lake_root=str(lake),
    )
    got = _run(eng, _env(DMS_FDE_KIT="1"))
    assert got.returncode == 1, got.stderr
    _run_dir, report, decisions, _md = _latest(eng)
    check = next(c for c in report["checks"] if c["id"] == "K4.09")
    assert check["status"] == "FAIL"
    assert check["severity"] == "high"
    assert any(item.get("lake_check") == "pk_unique" for item in check["evidence"]["items"])
    assert any(row["kind"] == "lake" and row["action"] == "map" for row in decisions["rows"])


def test_default_cap_matches_the_connector() -> None:
    mod = _load()
    assert mod.default_max_rows() == 500_000


def _factory_tenant(style: str, used: set[int]):
    sys.path.insert(0, str(ROOT / "scripts" / "fde"))
    sys.path.insert(0, str(ROOT / "tests" / "fde_scenarios"))
    import tenant_factory as tf

    for seed in range(0, 80):
        if seed in used:
            continue
        tenant = tf.make_tenant(seed, 1, rows_scale=0.12)
        if tenant.naming == style:
            used.add(seed)
            return tenant
    raise AssertionError(style)


def test_clean_factory_tenants_have_zero_high_findings(tmp_path: Path) -> None:
    sys.path.insert(0, str(ROOT / "scripts" / "verify"))
    sys.path.insert(0, str(ROOT / "scripts" / "fde"))
    sys.path.insert(0, str(ROOT / "tests" / "fde_scenarios"))
    import fde_scenarios as fs

    used: set[int] = set()
    tenants = [
        _factory_tenant("legacy", used),
        _factory_tenant("camel", used),
        _factory_tenant("snake", used),
    ]
    env = _env(DMS_FDE_KIT="1")
    for index, tenant in enumerate(tenants):
        db = tmp_path / f"{tenant.naming}-{index}.duckdb"
        fs.build_duckdb_dataset(tenant.dataset, db)
        before = _sha256(db)
        eng = _write_eng(
            tmp_path / f"eng-{index}",
            tenant_id=tenant.key,
            name=tenant.key,
            space={"warehouse": str(db)},
            seed=tenant.seed,
        )
        got = _run(eng, env)
        assert got.returncode in (0, 1), got.stderr
        assert _sha256(db) == before
        _run_dir, report, _decisions, _md = _latest(eng)
        assert report["metrics"]["high_findings"] == 0, json.dumps(
            [
                item
                for check in report["checks"]
                for item in (check["evidence"].get("items") or [])
                if item.get("level") in ("high", "critical")
            ],
            default=str,
        )
        assert report["status"] != "PASS" or report["metrics"]["high_findings"] == 0
        for planted in PLANTED:
            assert planted not in _outputs(eng)
