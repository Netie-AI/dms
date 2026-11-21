"""FDE-ONBOARD-01 (dms#541) stages 2 to 4: the masked pack and its leak check.

The fixture is landed through the real ingest (``ingest_batch``) into a tmp lake.
Seven CANARIES (one per kind: code, email, phone, decimal, date-ish text, prompt
injection, JSON cell) each appear once in the source. The pack must hold none of
them, in raw file text or in decoded JSON atoms, and the leak check must be able
to fail: a deliberately leaky pack and a number under an unlisted key are refused.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import duckdb
import pytest
from dms_core.pii import is_mask_token
from dms_executor import demo_warehouse as dw
from dms_executor import fde_pack as fp
from dms_executor import ingest_batch, profile_space
from dms_executor.demo_warehouse import ensure_demo_warehouse

FIXTURE = Path(__file__).parent / "fixtures" / "fde_onboard"
CANARIES: list[str] = json.loads((FIXTURE / "CANARIES.json").read_text(encoding="utf-8"))
SPACE = "fde-test-space"


class Landed:
    def __init__(self, lake: Path, tables: dict[str, str]) -> None:
        self.lake = lake
        self.tables = tables


@pytest.fixture(scope="module")
def landed(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Landed]:
    lake = tmp_path_factory.mktemp("fde") / "fde.duckdb"
    mp = pytest.MonkeyPatch()
    mp.setenv("DMS_WAREHOUSE_DB", str(lake))
    dw._SEEDED.clear()
    ensure_demo_warehouse(lake)
    files = [(p.name, p.read_bytes()) for p in sorted(FIXTURE.glob("*.csv"))]
    receipt = ingest_batch(files, path=lake, space_id=SPACE, database_url="")
    tables = {str(f.file): str(f.table) for f in receipt.files}
    assert set(tables) == {"customers.csv", "orders.csv", "products.csv"}
    # The sniffer lands ISO dates as DATE (already exact, no proposal). A text date
    # column is what a legacy or database source gives, so keep one as VARCHAR.
    con = duckdb.connect(str(lake))
    try:
        con.execute(f"ALTER TABLE {tables['orders.csv']} ALTER order_date TYPE VARCHAR")
    finally:
        con.close()
    yield Landed(lake, tables)
    mp.undo()


@pytest.fixture(scope="module")
def stats(landed: Landed) -> dict[str, dict[str, dict[str, Any]]]:
    return fp.column_stats(SPACE, path=landed.lake)


def _public(kind: str, payload: dict[str, Any], evidence: dict[str, Any], n: int) -> dict[str, Any]:
    return {
        "id": f"item-{n}",
        "status": "proposed",
        "kind": kind,
        "payload": payload,
        "provenance": {"evidence": evidence},
    }


def make_pack(landed: Landed, out: Path) -> dict[str, Any]:
    st = fp.column_stats(SPACE, path=landed.lake)
    names = fp.NameMask([(t, list(cols)) for t, cols in st.items()])
    groups = fp.value_groups(SPACE, st, path=landed.lake)
    type_rows = [_public(k, p, e, i) for i, (k, p, e) in enumerate(fp.type_items(st), start=1)]
    group_rows = [{**g, "id": f"grp-{i}", "status": "proposed"} for i, g in enumerate(groups)]
    profile = profile_space(SPACE, path=landed.lake)
    return fp.build_pack(
        out,
        space_id=SPACE,
        space_name="FDE test",
        profile=profile,
        stats=st,
        names=names,
        type_rows=type_rows,
        group_rows=group_rows,
        path=landed.lake,
    )


def _source_text(lake: Path) -> str:
    con = duckdb.connect(str(lake), read_only=True)
    try:
        parts: list[str] = []
        for table in ("customers", "orders", "products"):
            for row in con.execute(
                f"SELECT * EXCLUDE (_src, _ingest_id) FROM bronze.{table}"
            ).fetchall():
                parts.extend("" if c is None else str(c) for c in row)
        return "\n".join(parts)
    finally:
        con.close()


# (1) the canaries are really in the source: the checks below could fail ----------------


def test_every_canary_is_in_the_landed_source(landed: Landed) -> None:
    assert len(CANARIES) >= 6 and len(set(CANARIES)) == len(CANARIES)
    source = _source_text(landed.lake)
    for canary in CANARIES:
        assert source.count(canary) == 1, canary


# (2) + (3) the pack holds no canary and the leak check passes --------------------------


def test_pack_has_no_canary_in_raw_text_or_decoded_atoms(landed: Landed, tmp_path: Path) -> None:
    built = make_pack(landed, tmp_path / "pack")
    files = sorted((tmp_path / "pack").rglob("*.json"))
    assert {f.relative_to(tmp_path / "pack").as_posix() for f in files} == set(built["files"])
    assert len(files) == 5
    for f in files:
        raw = f.read_text(encoding="utf-8")
        atoms = "\n".join(fp._atoms(json.loads(raw)))
        for canary in CANARIES:
            assert canary.casefold() not in raw.casefold(), (f.name, canary)
            assert canary.casefold() not in atoms.casefold(), (f.name, canary)


def test_leak_check_passes_on_a_clean_pack(landed: Landed, tmp_path: Path) -> None:
    make_pack(landed, tmp_path / "pack")
    result = fp.LeakChecker(SPACE, path=landed.lake).check_pack(tmp_path / "pack")
    assert result["status"] == "passed", result
    assert result["hits"] == {} and result["unlisted_numbers"] == {}
    assert result["min_length"] == fp.MIN_LEAK_LEN
    assert result["short_integer_max_digits"] == fp.SHORT_INT_MAX_DIGITS
    assert result["columns_checked"] == 14
    assert result["distinct_values_checked"] > 50
    manifest_like = {"files": fp.pack_files(tmp_path / "pack")}
    assert "manifest.json" not in manifest_like["files"]
    assert all(len(v["sha256"]) == 64 for v in manifest_like["files"].values())


# (4) the check can fail ----------------------------------------------------------------


def test_leak_check_fails_on_a_canary_and_on_an_unlisted_number(
    landed: Landed, tmp_path: Path
) -> None:
    pack = tmp_path / "pack"
    make_pack(landed, pack)
    fp.write_pack_file(pack, "leak_value.json", {"note": f"saw {CANARIES[0]} here"})
    fp.write_pack_file(pack, "proposals/leak_key.json", {CANARIES[1]: 1})
    fp.write_pack_file(
        pack, "leak_number.json", {"balance": 42, "rows": [1, 2], "nested": {"x": 7}}
    )
    checker = fp.LeakChecker(SPACE, path=landed.lake)
    result = checker.check_pack(pack)
    assert result["status"] == "failed"
    assert result["hits"] == {"leak_value.json": 1, "proposals/leak_key.json": 1}
    assert result["unlisted_numbers"] == {
        "leak_number.json": ["balance", "x"],
        "proposals/leak_key.json": ["<key_holds_source_value>"],
    }
    assert checker.hits(f"prefix {CANARIES[3]} suffix") == 1
    assert checker.hits("nothing from the source here") == 0
    # The returned dict carries counts only.
    assert not any(c in json.dumps(result) for c in CANARIES)


# (5) shapes ----------------------------------------------------------------------------


def test_shape_examples() -> None:
    assert fp.shape("SKU-BETA") == "shape:U3-1U4"
    assert fp.shape("12.50") == "shape:D2.1D2"
    assert fp.shape("Kuala Lumpur") == "shape:U1L4S1U1L5"
    assert fp.shape("a--b") == "shape:L1-2L1"
    assert fp.shape("é!") == "shape:L1?1"
    long = fp.shape("aB" * 100)
    assert long.endswith("+") and len(long) <= len("shape:") + fp.SHAPE_MAX + 1
    assert fp.shape("QX7CANARYZ9") == "shape:U2D1U7D1"


# (6) type proposals and stats ----------------------------------------------------------


def _by_column(
    items: list[tuple[str, dict[str, Any], dict[str, Any]]],
) -> dict[str, dict[str, Any]]:
    return {f"{p['table']}.{p['column']}": p for _k, p, _e in items}


def test_type_items_propose_decimal_code_and_date(
    landed: Landed, stats: dict[str, dict[str, dict[str, Any]]]
) -> None:
    items = fp.type_items(stats)
    assert {k for k, _p, _e in items} == {"column_type"}
    by = _by_column(items)
    price = by["bronze.products.unit_price"]
    assert price["proposed_type"] == "DECIMAL(38,2)" and price["landed_type"] == "DOUBLE"
    code = by["bronze.products.code"]
    assert code["proposed_type"] == "code" and code["reason"] == "leading_zero"
    date = by["bronze.orders.order_date"]
    assert date["proposed_type"] == "DATE" and date["format"] == "%Y-%m-%d"
    assert date["name"] == "column_type:bronze.orders.order_date"
    assert by["bronze.orders.amount"]["proposed_type"] == "DECIMAL(38,6)"
    assert "bronze.customers.phone" not in by  # PII, not plain decimal, not a category
    for _k, _p, evidence in items:
        assert not any(isinstance(v, str) for v in evidence.values())


def test_column_stats_observed_scale_and_no_values(
    landed: Landed, stats: dict[str, dict[str, dict[str, Any]]]
) -> None:
    amount = stats["bronze.orders"]["amount"]
    assert amount["decimal"]["observed_scale"] == 6 and amount["decimal"]["lossless"] is True
    assert amount["decimal"]["basis"] == "double_text"
    assert amount["magnitude"]["min_exp"] == 1 and amount["magnitude"]["max_exp"] == 5
    assert amount["magnitude"]["negative"] is False
    assert stats["bronze.products"]["unit_price"]["decimal"]["observed_scale"] == 2
    assert stats["bronze.products"]["code"]["leading_zero"] is True
    assert stats["bronze.orders"]["order_date"]["date"] == {
        "formats": ["%Y-%m-%d"],
        "lossless": True,
    }
    assert stats["bronze.customers"]["region"]["distinct_count"] == 4
    assert stats["bronze.customers"]["email"]["pii_kind"] == "email"
    length = stats["bronze.customers"]["customer_id"]["length"]
    assert length == {"min": 4, "max": 4}
    text = json.dumps(stats, default=str)
    for canary in CANARIES:
        assert canary not in text
    assert set(stats) == set(landed.tables.values())


def test_a_failed_measurement_names_the_class_only(
    landed: Landed, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_a: Any, **_k: Any) -> dict[str, Any]:
        raise duckdb.ConversionException("Could not convert string 'QX7CANARYZ9' to INT32")

    monkeypatch.setattr(fp, "_decimal", boom)
    st = fp.column_stats(SPACE, path=landed.lake)
    assert st["bronze.products"]["unit_price"]["decimal"] == {"error": "ConversionException"}
    assert "QX7CANARYZ9" not in json.dumps(st, default=str)
    assert not [i for i in fp.type_items(st) if i[1]["column"] == "unit_price"]


# (7) value groups ----------------------------------------------------------------------


def test_value_groups_link_spellings_and_leave_others_alone(
    landed: Landed, stats: dict[str, dict[str, dict[str, Any]]]
) -> None:
    groups = fp.value_groups(SPACE, stats, path=landed.lake)
    region = [g for g in groups if g["column"] == "region"]
    assert len(region) == 1
    members = {m["value"] for m in region[0]["members"]}
    assert members == {"KL", "Kuala Lumpur", "kuala lumpur "}
    assert "Penang" not in members
    assert {"case_space", "initials"} <= set(region[0]["folds"])
    assert region[0]["target"] in {"Kuala Lumpur", "kuala lumpur ", "KL"}
    top = max(region[0]["members"], key=lambda m: (m["rows"], ""))
    assert region[0]["members"][0]["value"] == region[0]["target"]
    assert top["rows"] == region[0]["members"][0]["rows"]
    sku = [g for g in groups if g["column"] == "sku" and g["table"] == "bronze.orders"]
    sku_sets = sorted(sorted(m["value"] for m in g["members"]) for g in sku)
    assert sku_sets == [["ALPHA", "SKU-ALPHA"], ["BETA", "SKU-BETA"]]
    assert {m["mask"] for m in region[0]["members"]} == {"V01", "V02", "V03"}
    assert all(m["shape"].startswith("shape:") for m in region[0]["members"])
    items = fp.value_items(groups)
    by_name = {p["name"]: (p, e) for _k, p, e in items}
    payload, evidence = by_name[f"value_map:bronze.customers.region:{region[0]['group_id']}"]
    assert payload["origin"] == "measured"
    assert set(payload["map"]) == members - {region[0]["target"]}
    assert set(payload["map"].values()) == {region[0]["target"]}
    assert set(evidence["rows"]) == {"V01", "V02", "V03"}


# (8) names -----------------------------------------------------------------------------


def test_name_mask_masks_an_email_shaped_column_and_restores_it() -> None:
    names = fp.NameMask(
        [("bronze.people", ["id", "kim.tan@example.test", "region", "other@example.test"])]
    )
    token = names.out("kim.tan@example.test")
    assert token == "fde_name_01" and names.out("other@example.test") == "fde_name_02"
    assert names.out("region") == "region" and names.out("id") == "id"
    assert names.out_table("bronze.people") == "bronze.people"
    assert names.tokens() == {
        "fde_name_01": "kim.tan@example.test",
        "fde_name_02": "other@example.test",
    }
    assert names.back("SELECT fde_name_01, fde_name_02 FROM t") == (
        "SELECT kim.tan@example.test, other@example.test FROM t"
    )
    assert names.text("column_type:bronze.people.kim.tan@example.test") == (
        "column_type:bronze.people.fde_name_01"
    )
    many = fp.NameMask([("t", [f"u{i}@example.test" for i in range(11)])])
    assert many.back("fde_name_10 fde_name_01") == "u9@example.test u0@example.test"


def test_pack_masks_a_pii_column_name(tmp_path: Path) -> None:
    lake = tmp_path / "names.duckdb"
    mp = pytest.MonkeyPatch()
    mp.setenv("DMS_WAREHOUSE_DB", str(lake))
    dw._SEEDED.clear()
    ensure_demo_warehouse(lake)
    try:
        raw = b"id,kim.tan@example.test\nA1,x1\nA2,x2\n"
        ingest_batch([("names.csv", raw)], path=lake, space_id="names-space", database_url="")
        st = fp.column_stats("names-space", path=lake)
        names = fp.NameMask([(t, list(c)) for t, c in st.items()])
        assert names.tokens() == {"fde_name_01": "kim.tan@example.test"}
        out = tmp_path / "pack"
        fp.build_pack(
            out,
            space_id="names-space",
            space_name="n",
            profile=profile_space("names-space", path=lake),
            stats=st,
            names=names,
            type_rows=[],
            group_rows=[],
            path=lake,
        )
        for f in out.rglob("*.json"):
            assert "kim.tan@example.test" not in f.read_text(encoding="utf-8"), f.name
        assert "fde_name_01" in (out / "schema.json").read_text(encoding="utf-8")
        assert fp.LeakChecker("names-space", path=lake).check_pack(out)["status"] == "passed"
    finally:
        mp.undo()


# (9) samples ---------------------------------------------------------------------------


def test_samples_cells_are_none_tokens_or_shapes(landed: Landed) -> None:
    st = fp.column_stats(SPACE, path=landed.lake)
    names = fp.NameMask([(t, list(c)) for t, c in st.items()])
    samples = fp.masked_samples(SPACE, names, rows=5, path=landed.lake)
    assert samples["max_rows"] == 5 and samples["mask"] == "pii_token_then_shape/1"
    assert set(samples["tables"]) == set(landed.tables.values())
    seen_token = seen_shape = False
    for table in samples["tables"].values():
        assert 0 < len(table["rows"]) <= 5
        for row in table["rows"]:
            assert len(row) == len(table["columns"])
            for cell in row:
                assert cell is None or is_mask_token(cell) or str(cell).startswith("shape:"), cell
                seen_token |= isinstance(cell, str) and is_mask_token(cell)
                seen_shape |= isinstance(cell, str) and cell.startswith("shape:")
    assert seen_token and seen_shape
    cust = samples["tables"]["bronze.customers"]
    email_col = cust["columns"].index("email")
    assert all(is_mask_token(r[email_col]) for r in cust["rows"])


# mutation proof: a masker that keeps raw cells makes the canary check go red -----------


def test_the_canary_check_goes_red_when_samples_keep_raw_cells(
    landed: Landed, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = fp.masked_samples

    def leaky(space_id: str, names: fp.NameMask, **kw: Any) -> dict[str, Any]:
        out = real(space_id, names, **kw)
        con = duckdb.connect(str(landed.lake), read_only=True)
        try:
            raw = con.execute(
                f"SELECT note FROM {landed.tables['orders.csv']} WHERE note LIKE '%instructions%'"
            ).fetchone()
        finally:
            con.close()
        out["tables"]["bronze.orders"]["rows"][0][0] = raw[0] if raw else None
        return out

    monkeypatch.setattr(fp, "masked_samples", leaky)
    pack = tmp_path / "pack"
    make_pack(landed, pack)
    raw_text = (pack / "samples.masked.json").read_text(encoding="utf-8")
    assert CANARIES[5].casefold() in raw_text.casefold()
    result = fp.LeakChecker(SPACE, path=landed.lake).check_pack(pack)
    assert result["status"] == "failed" and result["hits"] == {"samples.masked.json": 1}


# --- adversary round fixes (dms#541) -----------------------------------------------------


def _lake(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, space: str, files: list[tuple[str, bytes]]
) -> tuple[Path, dict[str, str]]:
    lake = tmp_path / "adv.duckdb"
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(lake))
    dw._SEEDED.clear()
    ensure_demo_warehouse(lake)
    receipt = ingest_batch(files, path=lake, space_id=space, database_url="")
    return lake, {str(f.file): str(f.table) for f in receipt.files}


def _clean_pack(out: Path, **extra: Any) -> None:
    fp.write_pack_file(out, "schema.json", {"format": fp.PACK_FORMAT, "tables": []})
    for rel, obj in extra.items():
        fp.write_pack_file(out, rel.replace("__", "/") + ".json", obj)


def test_reserved_names_are_tokenised_and_restore_to_their_own_raw() -> None:
    names = fp.NameMask(
        [("bronze.t", ["fde_name_01", "boss@corp.test", "DMSMASK_name_01", "shape:U3", "region"])]
    )
    a, b = names.out("fde_name_01"), names.out("boss@corp.test")
    assert a != b
    assert len({names.out(c) for c in ("fde_name_01", "boss@corp.test")}) == 2
    assert names.out("DMSMASK_name_01").startswith("fde_name_")
    assert names.out("shape:U3").startswith("fde_name_")
    assert names.out("region") == "region"
    for raw in ("fde_name_01", "boss@corp.test", "DMSMASK_name_01", "shape:U3"):
        assert names.back(names.out(raw)) == raw
    # A raw name that is another name's token must not be re-replaced in one pass.
    swap = fp.NameMask([("t", ["fde_name_02", "x@y.test"])])
    assert swap.text("fde_name_02 x@y.test") == "fde_name_01 fde_name_02"


def test_back_replaces_whole_tokens_only() -> None:
    names = fp.NameMask([("t", ["a@b.test"])])
    assert names.back("fde_name_01") == "a@b.test"
    assert names.back("fde_name_011") == "fde_name_011"
    assert (
        names.back("xfde_name_01 fde_name_99 fde_name_01.") == "xfde_name_01 fde_name_99 a@b.test."
    )


def test_original_file_name_is_tokenised_even_when_the_table_name_is_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    space = "src-name-space"
    lake, tables = _lake(
        tmp_path,
        monkeypatch,
        space,
        [("alice.tan@corp.com.csv", b"id,region\nA1,north\nA2,south\n")],
    )
    label = tables["alice.tan@corp.com.csv"]
    assert "alice_tan_corp_com" in label
    st = fp.column_stats(space, path=lake)
    pairs = [(t, list(c)) for t, c in st.items()]
    assert fp.NameMask(pairs).out_table(label) == label  # the table name alone is not caught
    names = fp.NameMask(pairs, sources={label: "alice.tan@corp.com.csv"})
    assert names.out_table(label).startswith("bronze.fde_name_")
    assert names.text("loaded alice.tan@corp.com.csv and alice.tan@corp.com") == (
        f"loaded {names.out(label.rpartition('.')[2])} and {names.out(label.rpartition('.')[2])}"
    )
    out = tmp_path / "pack"
    fp.build_pack(
        out,
        space_id=space,
        space_name="n",
        profile=profile_space(space, path=lake),
        stats=st,
        names=names,
        type_rows=[],
        group_rows=[],
        path=lake,
    )
    for f in out.rglob("*.json"):
        assert "alice_tan_corp_com" not in f.read_text(encoding="utf-8"), f.name


def test_shape_writes_every_punctuation_run_with_its_count() -> None:
    assert fp.shape("-") == "shape:-1"
    assert fp.shape("+") == "shape:+1"
    assert fp.shape("@.@") == "shape:@1.1@1"
    for punct in ("-", "--", "@", "()", "a-"):
        assert fp.shape(punct) != punct and fp.shape(punct) != f"shape:{punct}"


def _hit_lake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, str, fp.LeakChecker]:
    space = "norm-space"
    csv = (
        "id,city\nA1,\u0130STANBUL\nA2,Melbourne Harbour\nA3,done\n"
        "A4,3f2b8c1e-aaaa-bbbb-cccc-1234567890ab\n"
    )
    lake, _ = _lake(tmp_path, monkeypatch, space, [("c.csv", csv.encode("utf-8"))])
    return lake, space, fp.LeakChecker(space, path=lake)


def test_leak_check_normalises_zero_width_fullwidth_and_exact_unicode_copies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lake, _space, checker = _hit_lake(tmp_path, monkeypatch)
    assert checker.hits("nothing here") == 0
    assert checker.hits("Melbourne Harbour") == 1
    assert checker.hits("Melbou\u200brne Harbour") == 1  # zero-width inside
    assert checker.hits("\uff2d\uff45\uff4c\uff42\uff4f\uff55\uff52\uff4e\uff45 Harbour") == 1
    assert checker.hits("see \u0130STANBUL now") == 1  # exact copy
    pack = tmp_path / "pack"
    for i, text in enumerate(
        ("Melbou\u200brne Harbour", "\uff2delbourne Harbour", "\u0130STANBUL")
    ):
        out = pack / str(i)
        _clean_pack(out, profile={"note": text})
        res = checker.check_pack(out)
        assert res["status"] == "failed" and res["hits"] == {"profile.json": 1}, text


def test_leak_check_refuses_files_outside_the_pack_and_scans_non_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _lake_path, _space, checker = _hit_lake(tmp_path, monkeypatch)
    out = tmp_path / "pack"
    _clean_pack(out)
    assert checker.check_pack(out)["status"] == "passed"
    (out / "notes.txt").write_text("raw Melbourne Harbour text", encoding="utf-8")
    (out / "proposals").mkdir()
    (out / "proposals" / "extra.json").write_text("{}", encoding="utf-8")
    res = checker.check_pack(out)
    assert res["status"] == "failed"
    assert res["unexpected_files"] == ["notes.txt", "proposals/extra.json"]
    assert res["hits"] == {"notes.txt": 1}


def test_a_generated_looking_value_is_skipped_only_when_the_caller_listed_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _lake_path, _space, checker = _hit_lake(tmp_path, monkeypatch)
    uuid = "3f2b8c1e-aaaa-bbbb-cccc-1234567890ab"
    out = tmp_path / "pack"
    _clean_pack(out, profile={"id": uuid})
    res = checker.check_pack(out)
    assert res["status"] == "failed" and res["hits"] == {"profile.json": 1}
    assert checker.check_pack(out, generated=[uuid])["status"] == "passed"
    other = tmp_path / "pack2"
    _clean_pack(other, profile={"id": "item-1", "run_id": uuid})
    res = checker.check_pack(other, generated=["item-1"])
    assert res["status"] == "failed" and res["hits"] == {"profile.json": 1}
    # a sha256 is skipped only when it is the digest of a pack file or a listed value
    digest = hashlib.sha256(b"prompt text").hexdigest()
    third = tmp_path / "pack3"
    _clean_pack(third, profile={"sha256": digest, "note": "x"})
    assert checker.check_pack(third, generated=["prompt text"])["status"] == "passed"
    fake = tmp_path / "pack4"
    _clean_pack(fake, profile={"sha256": "melbourne harbour"})
    assert checker.check_pack(fake)["status"] == "failed"


def test_numbers_must_be_counts_or_ratios_under_allowed_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _lake_path, _space, checker = _hit_lake(tmp_path, monkeypatch)

    def run(name: str, doc: dict[str, Any]) -> dict[str, Any]:
        out = tmp_path / name
        _clean_pack(out, profile=doc)
        return checker.check_pack(out)

    assert (
        run("ok", {"rows": 12, "null_rate": 0.25, "min_exp": -3, "max_exp": 4})["status"]
        == "passed"
    )
    bad = run("float", {"rows": 913377.123457})
    assert bad["status"] == "failed" and bad["unlisted_numbers"] == {
        "profile.json": ["rows:not_an_int"]
    }
    assert run("neg", {"rows": -1})["unlisted_numbers"] == {"profile.json": ["rows:not_a_count"]}
    assert run("ratio", {"null_rate": 1.5})["unlisted_numbers"] == {
        "profile.json": ["null_rate:not_a_ratio"]
    }
    assert run("expf", {"min_exp": 1.5})["status"] == "failed"
    assert run("len", {"length": {"min": 2.5}})["status"] == "failed"


def test_tool_words_are_skipped_only_as_whole_atoms(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _lake_path, _space, checker = _hit_lake(tmp_path, monkeypatch)
    assert {"done", "proposed", "VARCHAR"} <= fp.TOOL_VOCAB
    out = tmp_path / "pack"
    _clean_pack(out, profile={"stage": 4, "status": "done", "type": "VARCHAR"})
    assert checker.check_pack(out)["status"] == "passed"
    inside = tmp_path / "pack2"
    _clean_pack(inside, profile={"question": "how many done orders"})
    res = checker.check_pack(inside)
    assert res["status"] == "failed" and res["hits"] == {"profile.json": 1}
    before = set(fp.TOOL_VOCAB)
    checker.check_pack(inside)
    assert set(fp.TOOL_VOCAB) == before
