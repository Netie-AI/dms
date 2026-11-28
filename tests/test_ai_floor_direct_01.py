"""AI floor direct: every attempt kept and fed forward, first reason kept, read-only DB checks."""

from __future__ import annotations

import duckdb
import pytest
from dms_executor import ai_floor_direct as fl


@pytest.fixture()
def lake(tmp_path):
    p = tmp_path / "lake.duckdb"
    con = duckdb.connect(str(p))
    con.execute("create table orders(id int, region varchar, amount double)")
    con.execute("insert into orders values (1,'north',10),(2,'south',5),(3,'north',7)")
    con.close()
    return p


def _reply(sql: str) -> dict:
    return {
        "choices": [{"message": {"content": f"```sql\n{sql}\n```"}}],
        "model": "fake-model",
        "usage": {},
    }


def test_flag_accepts_only_the_digit_one() -> None:
    assert fl.enabled({"DMS_AI_FLOOR_DIRECT": "1"}) is True
    for v in ("", "0", "true", "yes", " 2"):
        assert fl.enabled({"DMS_AI_FLOOR_DIRECT": v}) is False


def test_failed_attempt_sql_and_exact_error_reach_the_next_prompt(lake) -> None:
    seen: list[str] = []
    replies = iter([_reply("select amout from orders"), _reply("select sum(amount) from orders")])

    def chat(role, messages, **_kw):
        seen.append("\n".join(m["content"] for m in messages))
        return next(replies)

    out = fl.run_direct("total amount?", db=fl.Db(lake), chat=chat, max_attempts=3)
    assert out["abstained"] is False and out["row_list"] == [[22.0]]
    assert len(seen) == 2
    assert "select amout from orders" in seen[1]
    assert "amout" in seen[1] and "Binder" in seen[1]
    assert [a["sql"] for a in out["attempts"]][:1] == ["select amout from orders"]


def test_abstain_keeps_the_first_reason(lake) -> None:
    replies = iter([_reply("select nope from orders"), _reply("no sql here"), _reply("still none")])
    out = fl.run_direct("x?", db=fl.Db(lake), chat=lambda r, m, **k: next(replies), max_attempts=3)
    assert out["abstained"] is True
    assert "nope" in str(out["reason"])


def test_destructive_and_ungranted_sql_never_run(lake) -> None:
    for bad, head in (
        ("delete from orders", "statement_not_allowed"),
        ("select * from secrets", "ungranted"),
    ):
        out = fl.run_direct(
            "x?",
            db=fl.Db(lake),
            grantable={"orders"},
            chat=lambda r, m, **k: _reply(bad),
            max_attempts=1,
        )
        assert out["abstained"] is True and str(out["reason"]).startswith(head), out["reason"]
    con = duckdb.connect(str(lake), read_only=True)
    assert con.execute("select count(*) from orders").fetchone()[0] == 3


def test_floor_compute_feeds_earlier_attempts_forward(lake) -> None:
    seen: list[str] = []
    replies = iter([_reply("select amout from orders"), _reply("select count(*) from orders")])

    def chat(role, messages, **_kw):
        seen.append("\n".join(m["content"] for m in messages))
        return next(replies)

    compute = fl.floor_compute("how many orders?", db_path=lake, grantable={"orders"}, chat=chat)
    first = compute(None)
    assert "query_sql" not in first and first.get("error")
    second = compute(None)
    assert second["query_sql"] == "select count(*) from orders"
    assert "select amout from orders" in seen[1]


def test_model_can_decline_or_ask_instead_of_forcing_sql(lake) -> None:
    for text, head in (
        ("CANNOT: the database has no staff data", "model_cannot"),
        ("ASK: this month or last month?", "model_asks"),
    ):
        reply = {"choices": [{"message": {"content": text}}], "model": "fake-model", "usage": {}}
        out = fl.run_direct("x?", db=fl.Db(lake), chat=lambda r, m, **k: reply, max_attempts=3)
        assert out["abstained"] is True and out["reason"].startswith(head)
        assert len([a for a in out["attempts"] if a.get("source") == "model"]) == 1
    assert out["ask"] == "this month or last month?"
