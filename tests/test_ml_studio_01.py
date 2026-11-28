"""DMS_ML_STUDIO. Off unless the value is the digit 1.

Synthetic series with a known signal. The held-out metric has to beat the
baseline. A planted copy of the target is leakage. A time split keeps the
future out of the early window. Not PASS.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_BANNED = {"train_score", "train_metric", "training_score", "train_accuracy", "train_rmse"}
_SENTINEL = "987654321.25"


@pytest.fixture
def ml_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_ML_STUDIO", "1")


def test_flag_accepts_only_the_digit_1(monkeypatch: pytest.MonkeyPatch) -> None:
    from dms_executor.ml_fit import enabled as fit_enabled
    from dms_executor.ml_studio import enabled as studio_enabled

    monkeypatch.delenv("DMS_ML_STUDIO", raising=False)
    assert fit_enabled() is False
    assert studio_enabled() is False
    for value in ("", "0", "true", "01", "1 ", " 1", "yes"):
        monkeypatch.setenv("DMS_ML_STUDIO", value)
        assert fit_enabled() is False
        assert studio_enabled() is False
    monkeypatch.setenv("DMS_ML_STUDIO", "1")
    assert fit_enabled() is True
    assert studio_enabled() is True


def test_flag_off_route_is_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DMS_ML_STUDIO", raising=False)
    from dms_api.app import create_app
    from fastapi.testclient import TestClient

    app = create_app()
    paths = {getattr(route, "path", "") for route in app.routes}
    assert not any(str(path).startswith("/v1/studio/ml") for path in paths)
    client = TestClient(app)
    assert client.post("/v1/studio/ml/runs", json={"question": "hello"}).status_code == 404
    assert client.get("/v1/studio/ml/runs/abc").status_code == 404


def test_route_module_does_not_import_executor() -> None:
    text = (ROOT / "apps/api/dms_api/routes/ml_studio.py").read_text(encoding="utf-8")
    assert "dms_executor" not in text
    assert "sklearn" not in text


def test_flag_off_does_not_fit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DMS_ML_STUDIO", raising=False)
    from dms_executor.ml_fit import fit_frame

    card = fit_frame(
        task="regression",
        columns=["x", "y"],
        rows=[[float(i), float(i)] for i in range(50)],
        target="y",
        features=["x"],
    )
    assert card["status"] == "refused"
    assert card["reason"] == "flag_off"
    assert card["models"] == []
    assert card["proven_solution"] is False


def test_time_split_keeps_the_future_out() -> None:
    from dms_executor.ml_fit import lag_source_indexes, time_split_indices

    times = [5, 1, 9, 3, 7, 2, 8, 4, 6, 0]
    split = time_split_indices(times, test_frac=0.3, val_frac=0.3)
    ordered = [times[i] for i in split["order"]]
    early = [ordered[i] for i in split["early"]]
    hold = [ordered[i] for i in split["holdout"]]
    assert early and hold
    assert max(early) < min(hold)
    for t in range(4, 40):
        lag1, lag_s = lag_source_indexes(t, 4)
        assert lag1 < t
        assert lag_s < t


def test_classification_beats_logistic(ml_on: None) -> None:
    import numpy as np
    from dms_executor.ml_fit import fit_frame

    rng = np.random.default_rng(0)
    n = 600
    x1 = rng.normal(size=n)
    x2 = rng.normal(size=n)
    y = ((x1 > 0) ^ (x2 > 0)).astype(int)
    rows = [
        [float(a), float(b), int(label), _SENTINEL]
        for a, b, label in zip(x1, x2, y, strict=True)
    ]
    card = fit_frame(
        task="classification",
        columns=["x1", "x2", "y", "note"],
        rows=rows,
        target="y",
        features=["x1", "x2"],
    )
    assert card["status"] == "succeeded", card["reason"]
    assert card["proven_solution"] is True
    assert card["winner"] != "logistic"
    assert card["result_metric"]["split"] == "held_out"
    assert card["result_metric"]["value"] > card["baseline_metric"]["value"]
    matrix = card["error_analysis"]["confusion"]
    total = sum(sum(row) for row in matrix)
    hit = sum(matrix[i][i] for i in range(len(matrix)))
    assert hit / total == pytest.approx(card["result_metric"]["value"])
    _assert_held_out_only(card)
    _assert_no_sentinel(card)


def test_regression_beats_linear(ml_on: None) -> None:
    import numpy as np
    from dms_executor.ml_fit import fit_frame

    rng = np.random.default_rng(1)
    x = rng.uniform(-3, 3, size=400)
    y = x**2 + rng.normal(0, 0.05, size=400)
    rows = [
        [None if i == 3 else float(a), float(b)]
        for i, (a, b) in enumerate(zip(x, y, strict=True))
    ]
    card = fit_frame(
        task="regression",
        columns=["x", "y"],
        rows=rows,
        target="y",
        features=["x"],
    )
    assert card["status"] == "succeeded", card["reason"]
    assert card["row_count"] == 400
    assert card["proven_solution"] is True
    assert card["winner"] != "linear"
    resid = np.array(card["error_analysis"]["residuals"], dtype=float)
    rmse = float(np.sqrt(np.mean(resid**2)))
    assert rmse == pytest.approx(card["result_metric"]["value"])
    assert card["result_metric"]["value"] < card["baseline_metric"]["value"]
    assert card["result_metric"]["split"] == "held_out"
    _assert_held_out_only(card)


def test_forecast_beats_naive_with_band_and_backtest(ml_on: None) -> None:
    import numpy as np
    from dms_executor.ml_fit import fit_frame

    n = 120
    t = np.arange(n)
    y = 1.5 * t + 4.0 * np.sin(2 * np.pi * t / 12)
    order = np.arange(n)
    rng = np.random.default_rng(2)
    rng.shuffle(order)
    rows = [[int(t[i]), float(y[i])] for i in order]
    card = fit_frame(
        task="forecast",
        columns=["month_index", "revenue"],
        rows=rows,
        target="revenue",
        features=[],
        time_column="month_index",
        season=12,
    )
    assert card["status"] == "succeeded", card["reason"]
    assert card["split"]["kind"] == "time"
    assert card["split"]["early_time_max"] < card["split"]["selection_time_min"]
    assert card["split"]["selection_time_max"] < card["split"]["holdout_time_min"]
    back = card["backtest"]
    assert back["actual"] and len(back["actual"]) == len(back["predicted"])
    assert len(back["low"]) == len(back["predicted"])
    triples = zip(back["low"], back["predicted"], back["high"], strict=True)
    assert all(lo <= mid <= hi for lo, mid, hi in triples)
    pred = np.array(back["predicted"])
    actual = np.array(back["actual"])
    rmse = float(np.sqrt(np.mean((pred - actual) ** 2)))
    assert rmse == pytest.approx(card["result_metric"]["value"])
    assert card["result_metric"]["split"] == "held_out"
    by_name = {row["name"]: row["held_out"] for row in card["models"]}
    assert by_name["ets"] < by_name["seasonal_naive"]
    assert by_name["gboost_lag"] < by_name["seasonal_naive"]
    assert card["proven_solution"] is True
    assert card["winner"] != "seasonal_naive"
    assert card["forward"]["predicted"]
    assert card["charts"]
    assert card["charts"][0]["png_b64"].startswith("iVBORw0K")
    _assert_held_out_only(card)
    assert "PASS" not in json.dumps(card)


def test_time_split_on_a_classifier(ml_on: None) -> None:
    import numpy as np
    from dms_executor.ml_fit import fit_frame

    rng = np.random.default_rng(0)
    n = 500
    x1 = rng.normal(size=n)
    x2 = rng.normal(size=n)
    y = ((x1 > 0) ^ (x2 > 0)).astype(int)
    rows = [
        [i, float(a), float(b), int(label)]
        for i, (a, b, label) in enumerate(zip(x1, x2, y, strict=True))
    ]
    rng.shuffle(rows)
    card = fit_frame(
        task="classification",
        columns=["t", "x1", "x2", "y"],
        rows=rows,
        target="y",
        features=["x1", "x2"],
        time_column="t",
    )
    assert card["status"] == "succeeded", card["reason"]
    assert card["split"]["kind"] == "time"
    assert card["split"]["early_time_max"] < card["split"]["holdout_time_min"]
    assert card["proven_solution"] is True


def test_clustering_beats_random_labels(ml_on: None) -> None:
    import numpy as np
    from dms_executor.ml_fit import fit_frame

    rng = np.random.default_rng(0)
    a = rng.normal(0.0, 0.3, size=(80, 2))
    b = rng.normal(6.0, 0.3, size=(80, 2))
    block = np.vstack([a, b])
    rows = [[float(p), float(q)] for p, q in block]
    card = fit_frame(
        task="clustering",
        columns=["f1", "f2"],
        rows=rows,
        target=None,
        features=["f1", "f2"],
    )
    assert card["status"] == "succeeded", card["reason"]
    assert card["proven_solution"] is True
    assert card["result_metric"]["name"] == "silhouette"
    assert card["result_metric"]["split"] == "held_out"
    assert card["result_metric"]["value"] > card["baseline_metric"]["value"]
    assert card["split"]["holdout_count"] < card["row_count"]
    _assert_held_out_only(card)


def test_planted_affine_leak_refuses(ml_on: None) -> None:
    from dms_executor.ml_fit import fit_frame

    rows = []
    for i in range(80):
        x = float(i)
        y = x * x
        rows.append([x, y, 3.0 * y + 2.0])
    card = fit_frame(
        task="regression",
        columns=["x", "y", "leak"],
        rows=rows,
        target="y",
        features=["x", "leak"],
    )
    assert card["status"] == "refused"
    assert card["reason"] == "leakage"
    assert card["models"] == []
    assert card["proven_solution"] is False
    assert any(code.startswith("affine_copy:") for code in card["eda"]["leakage"])


def test_planted_lead_refuses(ml_on: None) -> None:
    import numpy as np
    from dms_executor.ml_fit import fit_frame

    n = 80
    t = np.arange(n)
    y = list(1.5 * t + 4.0 * np.sin(2 * np.pi * t / 12))
    lead = list(y[1:]) + [y[-1]]
    rows = [[int(t[i]), float(y[i]), float(lead[i])] for i in range(n)]
    card = fit_frame(
        task="forecast",
        columns=["month_index", "revenue", "lead"],
        rows=rows,
        target="revenue",
        features=["lead"],
        time_column="month_index",
        season=12,
    )
    assert card["status"] == "refused"
    assert card["reason"] == "leakage"
    assert card["models"] == []
    assert card["proven_solution"] is False


def test_reinforcement_is_only_an_extension_point(ml_on: None) -> None:
    from dms_executor.ml_fit import extensions, fit_frame, register_task

    with pytest.raises(ValueError, match="task_out_of_scope"):
        register_task("reinforcement", "not in this build")
    register_task("ranking", "held-out metric, a baseline, and the same gates")
    assert "ranking" in extensions()
    card = fit_frame(
        task="ranking",
        columns=["x", "y"],
        rows=[[float(i), float(i)] for i in range(20)],
        target="y",
        features=["x"],
    )
    assert card["status"] == "refused"
    assert card["reason"] == "task_out_of_scope"
    card = fit_frame(
        task="reinforcement",
        columns=["x", "y"],
        rows=[[float(i), float(i)] for i in range(20)],
        target="y",
        features=["x"],
    )
    assert card["reason"] == "task_out_of_scope"


def test_planner_does_not_read_the_question_or_cells(ml_on: None) -> None:
    from dms_executor.ml_studio import check_model_sql, insight_messages, plan_job

    seen: list[str] = []

    def chat(role: str, messages: list[dict[str, str]], timeout: float) -> dict[str, object]:
        del timeout
        seen.append(role + json.dumps(messages))
        if role == "summarize":
            return {"choices": [{"message": {"content": "not a proven solution"}}]}
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "task": "classification",
                                "target": "y",
                                "features": ["x"],
                                "time_column": "",
                                "season": 12,
                                "sql": "SELECT x, COUNT(*) AS n, AVG(y) AS y FROM t GROUP BY x",
                                "candidates": ["logistic"],
                            }
                        )
                    }
                }
            ]
        }

    schema = [
        {"table_schema": "main", "table_name": "t", "column_name": "x", "data_type": "DOUBLE"},
        {"table_schema": "main", "table_name": "t", "column_name": "y", "data_type": "DOUBLE"},
    ]
    for question in ("hello", "please forecast revenue for next month"):
        plan, why = plan_job(question, schema, chat)
        assert why is None
        assert plan is not None
        assert plan["task"] == "classification"
    assert _SENTINEL not in "".join(seen)
    assert check_model_sql("SELECT * FROM t", {"t"}) == "not_aggregated"
    assert check_model_sql("DELETE FROM t", {"t"}) == "statement_not_allowed"
    assert check_model_sql("SELECT SUM(y) AS y FROM t", {"t"}) is None
    from dms_executor.ml_fit import fit_frame

    card = fit_frame(
        task="regression",
        columns=["x", "y", "note"],
        rows=[[float(i), float(i * i), _SENTINEL] for i in range(60)],
        target="y",
        features=["x"],
    )
    blob = json.dumps(insight_messages(card))
    assert _SENTINEL not in blob
    assert "held_out" in blob


def test_saved_run_finishes_in_the_background(
    ml_on: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import duckdb
    from cortex_client.gate import ComplianceDecision
    from dms_api.app import create_app
    from dms_api.settings import get_settings
    from fastapi.testclient import TestClient

    db = tmp_path / "sales.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE sales (month INTEGER, revenue DOUBLE, secret DOUBLE)")
    for i in range(40):
        revenue = 1.5 * i + 4.0 * math.sin(2 * math.pi * i / 4)
        con.execute("INSERT INTO sales VALUES (?, ?, ?)", [i, revenue, 987654321.25])
    con.close()
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(db))
    monkeypatch.setenv("DMS_ML_STUDIO_DIR", str(tmp_path / "runs"))
    get_settings.cache_clear()

    def chat(role: str, messages: list[dict[str, str]], timeout: float) -> dict[str, object]:
        del timeout
        blob = json.dumps(messages)
        assert _SENTINEL not in blob
        if role == "summarize":
            return {
                "choices": [
                    {"message": {"content": "The held-out error is below the baseline."}}
                ]
            }
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "task": "forecast",
                                "target": "revenue",
                                "features": [],
                                "time_column": "month",
                                "season": 4,
                                "sql": (
                                    "SELECT month, SUM(revenue) AS revenue "
                                    "FROM sales GROUP BY month ORDER BY month"
                                ),
                                "candidates": ["seasonal_naive", "ets", "gboost_lag"],
                            }
                        )
                    }
                }
            ]
        }

    def allow(*, action: str, actor: str | None = None, **_kwargs: object) -> ComplianceDecision:
        del actor
        return ComplianceDecision(allowed=True, reason="test_allow", action=action, event_id="evt")

    monkeypatch.setattr("dms_executor.ml_studio.default_chat", chat)
    monkeypatch.setattr("dms_api.routes.ml_studio.compliance_gate", allow)
    app = create_app()
    client = TestClient(app)
    created = client.post("/v1/studio/ml/runs", json={"question": "What happens next month?"})
    assert created.status_code == 202, created.text
    run_id = created.json()["run_id"]
    doc = {}
    for _ in range(90):
        doc = client.get(f"/v1/studio/ml/runs/{run_id}").json()
        if doc["status"] in {"succeeded", "refused", "failed"}:
            break
        import time

        time.sleep(1)
    assert doc["status"] == "succeeded", doc
    card = doc["card"]
    assert card["proven_solution"] is True
    assert card["backtest"]["low"]
    assert card["insights_status"] == "ok"
    assert card["audit"]["gate_event_id"] == "evt"
    assert card["audit"]["question_sha256"]
    assert _SENTINEL not in json.dumps(card["audit"])
    saved = tmp_path / "runs" / f"{run_id}.json"
    assert saved.is_file()


def _assert_held_out_only(card: dict) -> None:
    found: set[str] = set()

    def walk(obj: object) -> None:
        if isinstance(obj, dict):
            found.update(str(key) for key in obj)
            for value in obj.values():
                walk(value)
        elif isinstance(obj, list):
            for value in obj:
                walk(value)

    walk(card)
    assert not _BANNED & found
    for row in card["models"]:
        assert "held_out" in row


def _assert_no_sentinel(card: dict) -> None:
    from dms_executor.ml_studio import insight_messages

    assert _SENTINEL not in json.dumps(insight_messages(card))
