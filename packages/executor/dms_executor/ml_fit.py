"""Modelling harness for DMS_ML_STUDIO.

The provider picks the task. This module only fits, scores, and refuses.
It never reads a question. Training-set scores are not returned.

Forecast candidates are a seasonal naive baseline, additive Holt-Winters
(ETS), and a lag gradient booster. ETS is the statsmodels stand-in.
Swap: statsmodels.tsa.holtwinters.ExponentialSmoothing with the same
history-in, horizon-out forecast.

Reinforcement learning is not implemented. ``register_task`` only records
a note. ``fit_frame`` still refuses that name. Unlock: a runner that
returns held-out metrics and a baseline and passes the leakage, split,
and budget gates. See PARKING_LOT.

ponytail: the time budget is checked between models, so one fit can run
long. Upgrade: cancel the fit when the clock expires.
ponytail: forecast models use the target history only. Extra columns are
leak-checked and listed. Upgrade: add those columns as lagged inputs.
ponytail: cross-validation imputes with the outer early-window medians.
Upgrade: impute inside each fold.
"""

from __future__ import annotations

import base64
import io
import math
import os
import time
from typing import Any

FLAG = "DMS_ML_STUDIO"
MAX_ROWS = 8000
MAX_SECONDS = 25.0
MAX_FEATURES = 32
TASKS = ("forecast", "classification", "regression", "clustering")
MENUS: dict[str, tuple[str, ...]] = {
    "forecast": ("seasonal_naive", "ets", "gboost_lag"),
    "classification": ("logistic", "gboost", "rf"),
    "regression": ("linear", "gboost", "rf"),
    "clustering": ("kmeans",),
}
BASELINES = {
    "forecast": "seasonal_naive",
    "classification": "logistic",
    "regression": "linear",
    "clustering": "random_labels",
}

_EXTENSIONS: dict[str, str] = {}


def enabled() -> bool:
    """True only when DMS_ML_STUDIO is the digit 1. Unset is off."""
    return os.environ.get(FLAG) == "1"


def register_task(name: str, note: str = "") -> None:
    """Record an extension. This build does not run it."""
    if name == "reinforcement":
        raise ValueError("task_out_of_scope")
    if name in TASKS:
        raise ValueError("task_exists")
    if not str(note).strip():
        raise ValueError("note_required")
    _EXTENSIONS[name] = str(note).strip()


def extensions() -> dict[str, str]:
    return dict(_EXTENSIONS)


def lag_source_indexes(t: int, season: int) -> tuple[int, int]:
    """Indexes of the history a lag row may read. Both are strictly before t."""
    return (t - 1, t - season)


def time_split_indices(
    times: list[Any], *, test_frac: float = 0.2, val_frac: float = 0.2
) -> dict[str, list[int]]:
    """Sort by time. Early rows are fit, later rows are held out.

    The returned lists are positions in the sorted series, so index 0 is
    the earliest row. A later timestamp cannot land in the early slice.
    """
    order = sorted(range(len(times)), key=lambda i: _sort_key(times[i]))
    n = len(order)
    n_test = max(1, int(round(n * test_frac)))
    n_val = max(1, int(round((n - n_test) * val_frac)))
    n_early = n - n_test - n_val
    if n_early < 1:
        return {"order": order, "early": [], "selection": [], "holdout": []}
    return {
        "order": order,
        "early": list(range(0, n_early)),
        "selection": list(range(n_early, n_early + n_val)),
        "holdout": list(range(n_early + n_val, n)),
    }


def fit_frame(
    *,
    task: str,
    columns: list[str],
    rows: list[list[Any]],
    target: str | None,
    features: list[str],
    time_column: str | None = None,
    season: int = 12,
) -> dict[str, Any]:
    """Fit one task. The headline metric is the holdout, never the fit window."""
    card = _empty(task)
    if not enabled():
        card["status"] = "refused"
        card["reason"] = "flag_off"
        return card
    if task == "reinforcement" or task not in TASKS:
        card["status"] = "refused"
        card["reason"] = "task_out_of_scope"
        return card
    if len(rows) > MAX_ROWS:
        card["status"] = "refused"
        card["reason"] = "row_budget"
        card["budgets"]["hit"] = "row_budget"
        return card
    if len(rows) == 0:
        card["status"] = "refused"
        card["reason"] = "too_small"
        return card
    try:
        if task == "forecast":
            return _forecast(card, columns, rows, target, features, time_column, int(season))
        if task == "clustering":
            return _clustering(card, columns, rows, features, time_column)
        return _supervised(
            card, task, columns, rows, target, features, time_column
        )
    except Exception as exc:  # noqa: BLE001 - the run stores the class, not the text
        card["status"] = "failed"
        card["reason"] = type(exc).__name__
        return card


def _empty(task: str) -> dict[str, Any]:
    return {
        "status": "succeeded",
        "reason": None,
        "task": task,
        "proven_solution": False,
        "winner": None,
        "baseline": BASELINES.get(task),
        "result_metric": None,
        "baseline_metric": None,
        "models": [],
        "error_analysis": {},
        "eda": {"histograms": [], "correlations": [], "leakage": []},
        "backtest": None,
        "forward": None,
        "charts": [],
        "insights": None,
        "insights_status": "not_run",
        "row_count": 0,
        "split": {"kind": None, "holdout_count": 0},
        "budgets": {
            "max_rows": MAX_ROWS,
            "max_seconds": MAX_SECONDS,
            "max_features": MAX_FEATURES,
            "hit": None,
        },
        "features": [],
        "audit": None,
    }


def _forecast(
    card: dict[str, Any],
    columns: list[str],
    rows: list[list[Any]],
    target: str | None,
    features: list[str],
    time_column: str | None,
    season: int,
) -> dict[str, Any]:
    import numpy as np

    if not target or target not in columns:
        card["status"] = "refused"
        card["reason"] = "target_missing"
        return card
    if not time_column or time_column not in columns:
        card["status"] = "refused"
        card["reason"] = "time_column_required"
        return card
    if season < 2 or season > 52:
        card["status"] = "refused"
        card["reason"] = "plan_invalid"
        return card
    rows = _sort_rows(columns, rows, time_column)
    ti = columns.index(time_column)
    yi = columns.index(target)
    times = [row[ti] for row in rows]
    if any(t is None for t in times):
        card["status"] = "refused"
        card["reason"] = "time_missing"
        return card
    y_list: list[float] = []
    keep_times: list[Any] = []
    kept_rows: list[list[Any]] = []
    for row, t in zip(rows, times, strict=True):
        num = _as_float(row[yi])
        if num is None:
            continue
        y_list.append(num)
        keep_times.append(t)
        kept_rows.append(row)
    y = np.asarray(y_list, dtype=float)
    card["row_count"] = int(y.size)
    card["features"] = [name for name in features if name in columns and name != target]
    leaks = _leak_codes(np, columns, kept_rows, target, card["features"])
    card["eda"] = _eda(np, columns, kept_rows, target, card["features"], leaks)
    if leaks:
        card["status"] = "refused"
        card["reason"] = "leakage"
        return card
    horizon = max(2, min(season, int(y.size) // 6))
    if y.size < season + 2 * horizon:
        card["status"] = "refused"
        card["reason"] = "too_small"
        return card
    hold_start = int(y.size) - horizon
    sel_start = hold_start - horizon
    early = y[:sel_start]
    val = y[sel_start:hold_start]
    history = y[:hold_start]
    _reject_future(card, keep_times, sel_start, hold_start)
    if card["status"] == "refused":
        return card
    card["split"] = {
        "kind": "time",
        "holdout_count": horizon,
        "early_time_max": _json_time(keep_times[sel_start - 1]),
        "selection_time_min": _json_time(keep_times[sel_start]),
        "selection_time_max": _json_time(keep_times[hold_start - 1]),
        "holdout_time_min": _json_time(keep_times[hold_start]),
    }
    started = time.monotonic()
    params = _ets_params(np, early, season) if _within_budget(started, card) else None
    specs = (
        ("seasonal_naive", lambda hist, h: _forecast_naive(np, hist, season, h)),
        ("ets", lambda hist, h: _forecast_ets(np, hist, season, h, params)),
        ("gboost_lag", lambda hist, h: _forecast_gboost(np, hist, season, h)),
    )
    selection: dict[str, float | None] = {}
    holdout: dict[str, float | None] = {}
    val_pred: dict[str, Any] = {}
    test_pred: dict[str, Any] = {}
    for name, fn in specs:
        if name != "seasonal_naive" and not _within_budget(started, card):
            selection[name] = None
            holdout[name] = None
            continue
        try:
            pred_val = fn(early, len(val))
            pred_test = fn(history, horizon)
        except Exception:  # noqa: BLE001
            pred_val = None
            pred_test = None
        if pred_val is None or pred_test is None:
            selection[name] = None
            holdout[name] = None
            continue
        if not (np.all(np.isfinite(pred_val)) and np.all(np.isfinite(pred_test))):
            selection[name] = None
            holdout[name] = None
            continue
        selection[name] = _rmse(np, pred_val, val)
        holdout[name] = _rmse(np, pred_test, y[hold_start:])
        val_pred[name] = pred_val
        test_pred[name] = pred_test
    winner = _pick_lower(selection, "seasonal_naive")
    base_raw = holdout.get("seasonal_naive")
    if winner is None or base_raw is None:
        card["status"] = "refused"
        card["reason"] = "baseline_failed"
        return card
    base = float(base_raw)
    win_value = holdout.get(winner)
    card["winner"] = winner
    card["proven_solution"] = bool(win_value is not None and win_value < base)
    card["result_metric"] = {
        "name": "rmse",
        "value": None if win_value is None else float(win_value),
        "split": "held_out",
        "higher_is_better": False,
    }
    card["baseline_metric"] = {
        "name": "rmse",
        "value": base,
        "split": "held_out",
        "higher_is_better": False,
    }
    card["models"] = [
        {
            "name": name,
            "held_out": None if holdout.get(name) is None else float(holdout[name]),  # type: ignore[arg-type]
            "selection": None if selection.get(name) is None else float(selection[name]),  # type: ignore[arg-type]
            "importance": _lag_importance(name),
        }
        for name, _fn in specs
    ]
    pred = test_pred.get(winner)
    band = _band_std(np, val_pred.get(winner), val)
    actual = [float(v) for v in y[hold_start:]]
    predicted = [] if pred is None else [float(v) for v in pred]
    card["backtest"] = {
        "horizon": horizon,
        "actual": actual,
        "predicted": predicted,
        "low": [p - 1.96 * band for p in predicted],
        "high": [p + 1.96 * band for p in predicted],
    }
    card["error_analysis"] = {
        "by_horizon": [abs(a - p) for a, p in zip(actual, predicted, strict=True)]
    }
    try:
        forward = _forecast_named(np, winner, y, season, horizon, params)
    except Exception:  # noqa: BLE001
        forward = None
    if forward is not None and np.all(np.isfinite(forward)):
        pts = [float(v) for v in forward]
        card["forward"] = {
            "horizon": horizon,
            "predicted": pts,
            "low": [p - 1.96 * band for p in pts],
            "high": [p + 1.96 * band for p in pts],
        }
    _attach_charts(card)
    return card


def _supervised(
    card: dict[str, Any],
    task: str,
    columns: list[str],
    rows: list[list[Any]],
    target: str | None,
    features: list[str],
    time_column: str | None,
) -> dict[str, Any]:
    import numpy as np

    if not target or target not in columns:
        card["status"] = "refused"
        card["reason"] = "target_missing"
        return card
    use = [name for name in features if name in columns and name not in {target, time_column}]
    if not use:
        card["status"] = "refused"
        card["reason"] = "no_features"
        return card
    if len(use) > MAX_FEATURES:
        card["status"] = "refused"
        card["reason"] = "feature_budget"
        card["budgets"]["hit"] = "feature_budget"
        return card
    if time_column and time_column in columns:
        rows = _sort_rows(columns, rows, time_column)
    kept = [row for row in rows if _as_float(row[columns.index(target)]) is not None]
    leaks = _leak_codes(np, columns, kept, target, use)
    card["eda"] = _eda(np, columns, kept, target, use, leaks)
    card["features"] = use
    card["row_count"] = len(kept)
    if leaks:
        card["status"] = "refused"
        card["reason"] = "leakage"
        return card
    built = _design(np, columns, kept, target, use, task)
    if built is None:
        card["status"] = "refused"
        card["reason"] = "target_not_numeric" if task == "regression" else "too_small"
        return card
    x, y, names, _times = built
    times = None
    if time_column and time_column in columns:
        times = [row[columns.index(time_column)] for row in kept]
    if names and len(names) > MAX_FEATURES:
        card["status"] = "refused"
        card["reason"] = "feature_budget"
        card["budgets"]["hit"] = "feature_budget"
        return card
    parts = _parts(np, y, times, times is not None, task)
    if parts is None:
        card["status"] = "refused"
        card["reason"] = "too_small"
        return card
    early_i, sel_i, hold_i, kind = parts
    if times is not None:
        _reject_future_idx(card, times, early_i, sel_i, hold_i)
        if card["status"] == "refused":
            return card
    card["split"] = _split_doc(times, early_i, sel_i, hold_i, kind)
    x_early, y_early = x[early_i], y[early_i]
    x_sel, y_sel = x[sel_i], y[sel_i]
    x_hold, y_hold = x[hold_i], y[hold_i]
    med, lo, hi, keep = _prep_stats(np, x_early)
    x_early = _apply_prep(np, x_early, med, lo, hi, keep)
    x_sel = _apply_prep(np, x_sel, med, lo, hi, keep)
    x_fit = _apply_prep(np, x[np.concatenate([early_i, sel_i])], med, lo, hi, keep)
    y_fit = y[np.concatenate([early_i, sel_i])]
    x_hold = _apply_prep(np, x_hold, med, lo, hi, keep)
    names = [names[i] for i in keep]
    if x_early.shape[1] == 0:
        card["status"] = "refused"
        card["reason"] = "no_features"
        return card
    started = time.monotonic()
    higher = task == "classification"
    menu = MENUS[task]
    selection: dict[str, float | None] = {}
    holdout: dict[str, float | None] = {}
    preds: dict[str, Any] = {}
    importances: dict[str, list[dict[str, Any]]] = {}
    for name in menu:
        if name != menu[0] and not _within_budget(started, card):
            selection[name] = None
            holdout[name] = None
            continue
        try:
            model = _fit_model(task, name, x_early, y_early)
            pred_sel = _predict(task, model, x_sel)
            model_f = _fit_model(task, name, x_fit, y_fit)
            pred_hold = _predict(task, model_f, x_hold)
            selection[name] = _score(np, task, y_sel, pred_sel)
            holdout[name] = _score(np, task, y_hold, pred_hold)
            preds[name] = pred_hold
            importances[name] = _model_importance(np, task, name, model_f, x_sel, y_sel, names)
        except Exception:  # noqa: BLE001
            selection[name] = None
            holdout[name] = None
    baseline = BASELINES[task]
    winner = _pick(selection, baseline, higher)
    if winner is None or holdout.get(baseline) is None:
        card["status"] = "refused"
        card["reason"] = "baseline_failed"
        return card
    base = float(holdout[baseline])  # type: ignore[arg-type]
    win_value = holdout.get(winner)
    card["winner"] = winner
    card["proven_solution"] = bool(
        win_value is not None and (win_value > base if higher else win_value < base)
    )
    metric = "accuracy" if task == "classification" else "rmse"
    card["result_metric"] = _metric(metric, win_value, higher)
    card["baseline_metric"] = _metric(metric, base, higher)
    card["models"] = [
        {
            "name": name,
            "held_out": None if holdout.get(name) is None else float(holdout[name]),  # type: ignore[arg-type]
            "selection": None if selection.get(name) is None else float(selection[name]),  # type: ignore[arg-type]
            "importance": importances.get(name, []),
        }
        for name in menu
    ]
    pred = preds.get(winner)
    if pred is not None:
        card["error_analysis"] = _supervised_errors(np, task, y_hold, pred)
    _attach_charts(card)
    return card


def _clustering(
    card: dict[str, Any],
    columns: list[str],
    rows: list[list[Any]],
    features: list[str],
    time_column: str | None,
) -> dict[str, Any]:
    import numpy as np
    from sklearn.cluster import KMeans
    from sklearn.metrics import silhouette_score

    use = [name for name in features if name in columns and name != time_column]
    if not use:
        card["status"] = "refused"
        card["reason"] = "no_features"
        return card
    if len(use) > MAX_FEATURES:
        card["status"] = "refused"
        card["reason"] = "feature_budget"
        return card
    if time_column and time_column in columns:
        rows = _sort_rows(columns, rows, time_column)
    card["features"] = use
    card["eda"] = _eda(np, columns, rows, None, use, [])
    built = _design(np, columns, rows, None, use, "clustering")
    if built is None:
        card["status"] = "refused"
        card["reason"] = "too_small"
        return card
    x, _y, names, _times = built
    times = None
    if time_column and time_column in columns:
        times = [row[columns.index(time_column)] for row in rows]
    card["row_count"] = int(x.shape[0])
    parts = _parts(np, np.zeros(x.shape[0]), times, times is not None, "clustering")
    if parts is None:
        card["status"] = "refused"
        card["reason"] = "too_small"
        return card
    early_i, sel_i, hold_i, kind = parts
    if times is not None:
        _reject_future_idx(card, times, early_i, sel_i, hold_i)
        if card["status"] == "refused":
            return card
    card["split"] = _split_doc(times, early_i, sel_i, hold_i, kind)
    med, lo, hi, keep = _prep_stats(np, x[early_i])
    names = [names[i] for i in keep]

    def prep(idx: Any) -> Any:
        return _scale(np, _apply_prep(np, x[idx], med, lo, hi, keep), x[early_i], med, lo, hi, keep)

    x_early, x_sel = prep(early_i), prep(sel_i)
    fit_i = np.concatenate([early_i, sel_i])
    x_fit, x_hold = prep(fit_i), prep(hold_i)
    if x_early.shape[0] < 10 or x_hold.shape[0] < 4 or x_early.shape[1] == 0:
        card["status"] = "refused"
        card["reason"] = "too_small"
        return card
    best_k = None
    best_sel = -2.0
    for k in (2, 3):
        if k >= x_early.shape[0]:
            continue
        pred = KMeans(n_clusters=k, n_init=10, random_state=0).fit(x_early).predict(x_sel)
        score = _safe_silhouette(silhouette_score, x_sel, pred)
        if score is not None and score > best_sel:
            best_sel = score
            best_k = k
    if best_k is None:
        card["status"] = "refused"
        card["reason"] = "baseline_failed"
        return card
    model = KMeans(n_clusters=best_k, n_init=10, random_state=0).fit(x_fit)
    hold_labels = model.predict(x_hold)
    held = _safe_silhouette(silhouette_score, x_hold, hold_labels)
    rng = np.random.default_rng(0)
    random_scores = []
    for _ in range(5):
        guess = rng.integers(0, best_k, size=x_hold.shape[0])
        score = _safe_silhouette(silhouette_score, x_hold, guess)
        if score is not None:
            random_scores.append(score)
    baseline = float(np.mean(random_scores)) if random_scores else None
    card["winner"] = "kmeans"
    card["proven_solution"] = bool(held is not None and baseline is not None and held > baseline)
    card["result_metric"] = _metric("silhouette", held, True)
    card["baseline_metric"] = _metric("silhouette", baseline, True)
    card["models"] = [
        {
            "name": "kmeans",
            "held_out": held,
            "selection": None if best_sel < -1 else float(best_sel),
            "importance": [],
            "k": best_k,
        }
    ]
    sizes: dict[str, int] = {}
    for label in hold_labels:
        key = str(int(label))
        sizes[key] = sizes.get(key, 0) + 1
    card["error_analysis"] = {"sizes": sizes, "k": best_k}
    _attach_charts(card)
    return card


def _design(
    np: Any,
    columns: list[str],
    rows: list[list[Any]],
    target: str | None,
    features: list[str],
    task: str,
) -> tuple[Any, Any, list[str], list[Any] | None] | None:
    if len(rows) < 8:
        return None
    times = None
    blocks: list[Any] = []
    names: list[str] = []
    for name in features:
        values = [row[columns.index(name)] for row in rows]
        numeric = [_as_float(v) for v in values]
        finite = sum(v is not None for v in numeric)
        if finite >= 0.8 * len(values):
            col = np.array([np.nan if v is None else v for v in numeric], dtype=float)
            blocks.append(col.reshape(-1, 1))
            names.append(name)
            continue
        levels = []
        for value in values:
            text = "" if value is None else str(value)
            if text not in levels:
                levels.append(text)
        if 1 < len(levels) <= 6:
            for level in levels:
                blocks.append(
                    np.array(
                        [1.0 if ("" if v is None else str(v)) == level else 0.0 for v in values]
                    ).reshape(-1, 1)
                )
                names.append(f"{name}={level}")
    if not blocks:
        return None
    x = np.hstack(blocks)
    y = np.zeros(len(rows))
    if task != "clustering":
        assert target is not None
        clean: list[float] = []
        for row in rows:
            value = _as_float(row[columns.index(target)])
            if value is None:
                return None
            clean.append(value)
        y = np.array(clean, dtype=float)
        if task == "classification":
            if not np.allclose(y, np.round(y)):
                return None
            y = np.round(y).astype(int)
            if len(set(y.tolist())) < 2 or len(set(y.tolist())) > 12:
                return None
    return x, y, names, times


def _parts(
    np: Any, y: Any, times: list[Any] | None, timed: bool, task: str
) -> tuple[Any, Any, Any, str] | None:
    n = int(y.shape[0])
    minimum = 30 if task == "clustering" else 40
    if n < minimum:
        return None
    if timed and times is not None:
        split = time_split_indices(times)
        if not split["early"] or not split["selection"] or not split["holdout"]:
            return None
        return (
            np.array(split["early"]),
            np.array(split["selection"]),
            np.array(split["holdout"]),
            "time",
        )
    idx = np.arange(n)
    rng = np.random.default_rng(0)
    rng.shuffle(idx)
    frac = 0.2 if task == "clustering" else 0.25
    n_hold = max(1, int(round(n * frac)))
    hold = idx[:n_hold]
    rest = idx[n_hold:]
    n_sel = max(1, int(round(len(rest) * 0.2)))
    sel = rest[:n_sel]
    early = rest[n_sel:]
    if task == "classification":
        if len(set(y[early].tolist())) < 2 or len(set(y[hold].tolist())) < 2:
            return None
    if len(early) < 8:
        return None
    return early, sel, hold, "random"


def _prep_stats(np: Any, x: Any) -> tuple[Any, Any, Any, list[int]]:
    med = np.nanmedian(x, axis=0)
    med = np.where(np.isfinite(med), med, 0.0)
    filled = np.where(np.isfinite(x), x, med)
    lo = np.percentile(filled, 1, axis=0)
    hi = np.percentile(filled, 99, axis=0)
    keep = [i for i in range(filled.shape[1]) if float(np.std(filled[:, i])) > 1e-8]
    if not keep:
        keep = [0]
    return med, lo, hi, keep


def _apply_prep(np: Any, x: Any, med: Any, lo: Any, hi: Any, keep: list[int]) -> Any:
    filled = np.where(np.isfinite(x), x, med)
    clipped = np.minimum(np.maximum(filled, lo), hi)
    return clipped[:, keep]


def _scale(np: Any, x: Any, x_early_raw: Any, med: Any, lo: Any, hi: Any, keep: list[int]) -> Any:
    base = _apply_prep(np, x_early_raw, med, lo, hi, keep)
    mu = base.mean(axis=0)
    sd = base.std(axis=0)
    sd = np.where(sd < 1e-8, 1.0, sd)
    return (x - mu) / sd


def _fit_model(task: str, name: str, x: Any, y: Any) -> Any:
    if task == "classification" and name == "logistic":
        from sklearn.linear_model import LogisticRegression

        model = LogisticRegression(max_iter=400)
        model.fit(x, y)
        return model
    if task == "regression" and name == "linear":
        from sklearn.linear_model import LinearRegression

        model = LinearRegression()
        model.fit(x, y)
        return model
    if name == "gboost":
        if task == "classification":
            from sklearn.ensemble import HistGradientBoostingClassifier

            model = HistGradientBoostingClassifier(
                max_iter=60, max_depth=4, learning_rate=0.08, random_state=0, early_stopping=False
            )
        else:
            from sklearn.ensemble import HistGradientBoostingRegressor

            model = HistGradientBoostingRegressor(
                max_iter=80, max_depth=3, learning_rate=0.08, random_state=0, early_stopping=False
            )
        model.fit(x, y)
        return model
    if name == "rf":
        if task == "classification":
            from sklearn.ensemble import RandomForestClassifier

            model = RandomForestClassifier(
                n_estimators=40, max_depth=6, random_state=0, n_jobs=1
            )
        else:
            from sklearn.ensemble import RandomForestRegressor

            model = RandomForestRegressor(
                n_estimators=40, max_depth=6, random_state=0, n_jobs=1
            )
        model.fit(x, y)
        return model
    raise ValueError("unknown_model")


def _predict(task: str, model: Any, x: Any) -> Any:
    if task == "classification":
        return model.predict(x)
    return model.predict(x)


def _score(np: Any, task: str, y: Any, pred: Any) -> float:
    if task == "classification":
        return float(np.mean(np.asarray(y) == np.asarray(pred)))
    return _rmse(np, pred, y)


def _supervised_errors(np: Any, task: str, y: Any, pred: Any) -> dict[str, Any]:
    y = np.asarray(y)
    pred = np.asarray(pred)
    if task == "classification":
        labels = sorted(set(y.tolist()) | set(pred.tolist()))
        matrix = [
            [int(np.sum((y == actual) & (pred == guess))) for guess in labels] for actual in labels
        ]
        return {"labels": labels, "confusion": matrix}
    resid = [float(v) for v in (y - pred)]
    abs_err = np.sort(np.abs(y - pred))
    return {
        "residuals": resid,
        "abs_error_p50": float(abs_err[len(abs_err) // 2]),
        "abs_error_p90": float(abs_err[min(len(abs_err) - 1, int(len(abs_err) * 0.9))]),
    }


def _model_importance(
    np: Any, task: str, name: str, model: Any, x_sel: Any, y_sel: Any, names: list[str]
) -> list[dict[str, Any]]:
    if hasattr(model, "coef_"):
        coef = np.abs(np.atleast_2d(np.asarray(model.coef_, dtype=float))).mean(axis=0)
        return [{"feature": n, "value": float(v)} for n, v in zip(names, coef, strict=False)]
    if hasattr(model, "feature_importances_"):
        return [
            {"feature": n, "value": float(v)}
            for n, v in zip(names, model.feature_importances_, strict=False)
        ]
    try:
        from sklearn.inspection import permutation_importance

        scoring = "accuracy" if task == "classification" else "neg_root_mean_squared_error"
        result = permutation_importance(
            model, x_sel, y_sel, n_repeats=3, random_state=0, scoring=scoring
        )
        return [
            {"feature": n, "value": float(v)}
            for n, v in zip(names, result.importances_mean, strict=False)
        ]
    except Exception:  # noqa: BLE001
        return []


def _forecast_naive(np: Any, history: Any, season: int, horizon: int) -> Any | None:
    if len(history) < season or horizon < 1:
        return None
    return np.array(
        [float(history[-season + (h % season)]) for h in range(horizon)], dtype=float
    )


def _forecast_gboost(np: Any, history: Any, season: int, horizon: int) -> Any | None:
    """Lag booster on the detrended series. The line carries the trend past the sample.

    A tree on the raw level does not extrapolate. The line does. Lags still
    read only earlier points.
    """
    hist = np.asarray(history, dtype=float)
    if hist.size < season + 8:
        return None
    from sklearn.ensemble import HistGradientBoostingRegressor

    slope, intercept = _line(np, hist)
    resid = hist - (intercept + slope * np.arange(hist.size))
    values = [float(v) for v in resid]
    xs = [_lag_vector(values, t, season) for t in range(season, len(values))]
    ys = [values[t] for t in range(season, len(values))]
    model = HistGradientBoostingRegressor(
        max_iter=80, max_depth=3, learning_rate=0.08, random_state=0, early_stopping=False
    )
    model.fit(np.asarray(xs, dtype=float), np.asarray(ys, dtype=float))
    grown = list(values)
    preds: list[float] = []
    origin = int(hist.size)
    for step in range(horizon):
        t = len(grown)
        residual = float(model.predict(np.asarray([_lag_vector(grown, t, season)], dtype=float))[0])
        if not math.isfinite(residual):
            return None
        level = intercept + slope * float(origin + step)
        preds.append(level + residual)
        grown.append(residual)
    return np.asarray(preds, dtype=float)


def _line(np: Any, hist: Any) -> tuple[float, float]:
    t = np.arange(hist.size, dtype=float)
    var = float(np.dot(t - t.mean(), t - t.mean()))
    if var <= 1e-12:
        return 0.0, float(np.mean(hist))
    slope = float(np.dot(t - t.mean(), hist - hist.mean()) / var)
    intercept = float(np.mean(hist) - slope * np.mean(t))
    return slope, intercept


def _lag_vector(history: list[float], t: int, season: int) -> list[float]:
    lag1, lag_s = lag_source_indexes(t, season)
    if min(lag1, lag_s) < 0 or max(lag1, lag_s) >= t:
        raise RuntimeError("lag_future")
    return [history[lag1], history[lag_s], float(t % season)]


def _lag_importance(name: str) -> list[dict[str, Any]]:
    if name != "gboost_lag":
        return []
    return [
        {"feature": "lag_1", "value": None},
        {"feature": "lag_season", "value": None},
        {"feature": "season_slot", "value": None},
    ]


def _ets_params(np: Any, history: Any, season: int) -> tuple[float, float, float] | None:
    if len(history) < 3 * season:
        return None
    tail = season
    base = history[:-tail]
    target = history[-tail:]
    best: tuple[float, float, float] | None = None
    best_err = math.inf
    for alpha in (0.2, 0.4, 0.6, 0.8):
        for beta in (0.1, 0.3, 0.5):
            for gamma in (0.2, 0.4, 0.6):
                pred = _hw_forecast(np, base, season, tail, alpha, beta, gamma)
                if pred is None:
                    continue
                err = _rmse(np, pred, target)
                if err < best_err:
                    best_err = err
                    best = (alpha, beta, gamma)
    return best


def _forecast_ets(
    np: Any, history: Any, season: int, horizon: int, params: tuple[float, float, float] | None
) -> Any | None:
    if params is None:
        return None
    return _hw_forecast(np, history, season, horizon, params[0], params[1], params[2])


def _forecast_named(
    np: Any, name: str, history: Any, season: int, horizon: int, params: Any
) -> Any | None:
    if name == "seasonal_naive":
        return _forecast_naive(np, history, season, horizon)
    if name == "ets":
        return _forecast_ets(np, history, season, horizon, params)
    if name == "gboost_lag":
        return _forecast_gboost(np, history, season, horizon)
    return None


def _hw_forecast(
    np: Any,
    history: Any,
    season: int,
    horizon: int,
    alpha: float,
    beta: float,
    gamma: float,
) -> Any | None:
    """Additive Holt-Winters. Level, trend, and a seasonal index of length `season`."""
    y = np.asarray(history, dtype=float)
    n = int(y.size)
    if n < 2 * season or horizon < 1:
        return None
    level = float(np.mean(y[:season]))
    trend = float((np.mean(y[season : 2 * season]) - level) / season)
    seasons = [float(y[i] - level) for i in range(season)]
    for t in range(n):
        slot = t % season
        seasonal = seasons[slot]
        new_level = alpha * (float(y[t]) - seasonal) + (1 - alpha) * (level + trend)
        new_trend = beta * (new_level - level) + (1 - beta) * trend
        seasons[slot] = gamma * (float(y[t]) - new_level) + (1 - gamma) * seasonal
        level, trend = new_level, new_trend
        if not all(math.isfinite(v) for v in (level, trend)):
            return None
    preds = []
    for step in range(1, horizon + 1):
        value = level + step * trend + seasons[(n + step - 1) % season]
        if not math.isfinite(value):
            return None
        preds.append(float(value))
    return np.asarray(preds, dtype=float)


def _leak_codes(
    np: Any, columns: list[str], rows: list[list[Any]], target: str | None, features: list[str]
) -> list[str]:
    if not target or target not in columns or not rows:
        return []
    y = np.array([_as_float(row[columns.index(target)]) for row in rows], dtype=float)
    codes: list[str] = []
    for name in features:
        if name == target:
            codes.append(f"affine_copy:{name}")
            continue
        x = np.array([_as_float(row[columns.index(name)]) for row in rows], dtype=float)
        if _affine_copy(np, x, y):
            codes.append(f"affine_copy:{name}")
        elif _lead_copy(np, x, y):
            codes.append(f"lead:{name}")
    return codes


def _affine_copy(np: Any, x: Any, y: Any) -> bool:
    mask = np.isfinite(x) & np.isfinite(y)
    if int(mask.sum()) < 8:
        return False
    xs = x[mask]
    ys = y[mask]
    scale = 1.0 + np.abs(ys)
    if float(np.mean(np.abs(xs - ys) <= 1e-8 * scale)) >= 0.999:
        return True
    x0 = xs - xs.mean()
    y0 = ys - ys.mean()
    var = float(np.dot(x0, x0))
    yvar = float(np.dot(y0, y0))
    if var <= 1e-18 or yvar <= 1e-18:
        return False
    beta = float(np.dot(x0, y0) / var)
    resid = y0 - beta * x0
    sigma = math.sqrt(yvar / len(y0))
    return float(np.max(np.abs(resid))) <= 1e-6 * (sigma + 1e-9)


def _lead_copy(np: Any, x: Any, y: Any) -> bool:
    if len(x) < 8:
        return False
    left = x[:-1]
    right = y[1:]
    mask = np.isfinite(left) & np.isfinite(right)
    if int(mask.sum()) < 8:
        return False
    scale = 1.0 + np.abs(right[mask])
    return bool(float(np.mean(np.abs(left[mask] - right[mask]) <= 1e-8 * scale)) >= 0.99)


def _eda(
    np: Any,
    columns: list[str],
    rows: list[list[Any]],
    target: str | None,
    features: list[str],
    leaks: list[str],
) -> dict[str, Any]:
    names = []
    if target and target in columns:
        names.append(target)
    for name in features:
        if name not in names and name in columns:
            names.append(name)
    names = names[:8]
    hist = []
    series: dict[str, Any] = {}
    for name in names:
        values = [_as_float(row[columns.index(name)]) for row in rows]
        arr = np.array([np.nan if v is None else v for v in values], dtype=float)
        series[name] = arr
        finite = arr[np.isfinite(arr)]
        if finite.size == 0:
            continue
        counts, edges = np.histogram(finite, bins=8)
        hist.append(
            {
                "column": name,
                "edges": [float(v) for v in edges],
                "counts": [int(v) for v in counts],
            }
        )
    corr = []
    for i, left in enumerate(names):
        for right in names[i + 1 :]:
            a = series.get(left)
            b = series.get(right)
            if a is None or b is None:
                continue
            mask = np.isfinite(a) & np.isfinite(b)
            if int(mask.sum()) < 8:
                continue
            if float(np.std(a[mask])) < 1e-12 or float(np.std(b[mask])) < 1e-12:
                continue
            r = float(np.corrcoef(a[mask], b[mask])[0, 1])
            if math.isfinite(r):
                corr.append({"a": left, "b": right, "r": r})
    return {"histograms": hist, "correlations": corr, "leakage": leaks}


def _reject_future(card: dict[str, Any], times: list[Any], sel_start: int, hold_start: int) -> None:
    if _sort_key(times[sel_start - 1]) > _sort_key(times[sel_start]):
        card["status"] = "refused"
        card["reason"] = "time_split"
        return
    if _sort_key(times[hold_start - 1]) > _sort_key(times[hold_start]):
        card["status"] = "refused"
        card["reason"] = "time_split"


def _reject_future_idx(
    card: dict[str, Any], times: list[Any], early: Any, sel: Any, hold: Any
) -> None:
    early_max = max((_sort_key(times[int(i)]) for i in early), default=None)
    sel_keys = [_sort_key(times[int(i)]) for i in sel]
    hold_keys = [_sort_key(times[int(i)]) for i in hold]
    if early_max is None or not sel_keys or not hold_keys:
        card["status"] = "refused"
        card["reason"] = "time_split"
        return
    if early_max > min(sel_keys) or max(sel_keys) > min(hold_keys):
        card["status"] = "refused"
        card["reason"] = "time_split"


def _split_doc(
    times: list[Any] | None, early: Any, sel: Any, hold: Any, kind: str
) -> dict[str, Any]:
    doc: dict[str, Any] = {"kind": kind, "holdout_count": int(len(hold))}
    if times is not None:
        doc["early_time_max"] = _json_time(_edge(times, early, max))
        doc["holdout_time_min"] = _json_time(_edge(times, hold, min))
        doc["selection_time_min"] = _json_time(_edge(times, sel, min))
        doc["selection_time_max"] = _json_time(_edge(times, sel, max))
    return doc


def _edge(times: list[Any], idxs: Any, pick: Any) -> Any:
    chosen = pick(idxs, key=lambda i: _sort_key(times[int(i)]))
    return times[int(chosen)]


def _attach_charts(card: dict[str, Any]) -> None:
    try:
        card["charts"] = _charts(card)
    except Exception:  # noqa: BLE001
        card["charts"] = []


def _charts(card: dict[str, Any]) -> list[dict[str, str]]:
    from PIL import Image, ImageDraw

    charts = []
    models = [m for m in card.get("models") or [] if m.get("held_out") is not None]
    if models:
        charts.append(
            {
                "name": "metrics",
                "media_type": "image/png",
                "png_b64": _png(
                    Image,
                    ImageDraw,
                    lambda dr, w, h: _draw_bars(
                        dr,
                        w,
                        h,
                        [m["name"] for m in models],
                        [float(m["held_out"]) for m in models],
                    ),
                ),
            }
        )
    back = card.get("backtest") or {}
    if back.get("actual") and back.get("predicted"):
        charts.append(
            {
                "name": "backtest",
                "media_type": "image/png",
                "png_b64": _png(
                    Image,
                    ImageDraw,
                    lambda dr, w, h: _draw_lines(
                        dr,
                        w,
                        h,
                        back["actual"],
                        back["predicted"],
                        back.get("low") or [],
                        back.get("high") or [],
                    ),
                ),
            }
        )
    hist = (card.get("eda") or {}).get("histograms") or []
    if hist:
        charts.append(
            {
                "name": "distribution",
                "media_type": "image/png",
                "png_b64": _png(
                    Image,
                    ImageDraw,
                    lambda dr, w, h: _draw_bars(
                        dr, w, h, [str(i) for i in range(len(hist[0]["counts"]))], hist[0]["counts"]
                    ),
                ),
            }
        )
    return charts


def _png(image_cls: Any, draw_cls: Any, paint: Any) -> str:
    image = image_cls.new("RGB", (640, 360), "white")
    paint(draw_cls.Draw(image), 640, 360)
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _draw_bars(dr: Any, w: int, h: int, labels: list[str], values: list[float]) -> None:
    dr.rectangle((0, 0, w, 36), fill="#f4f1ea")
    dr.text((16, 12), "Held-out", fill="#222222")
    if not values:
        return
    top = max(abs(float(v)) for v in values) or 1.0
    span = max(1, len(values))
    gap = 16
    width = max(8, (w - 40 - gap * span) // span)
    for i, (label, value) in enumerate(zip(labels, values, strict=False)):
        height = int((abs(float(value)) / top) * (h - 90))
        x0 = 20 + i * (width + gap)
        y0 = h - 40 - height
        dr.rectangle((x0, y0, x0 + width, h - 40), fill="#3d5a40")
        dr.text((x0, h - 28), str(label)[:12], fill="#222222")


def _draw_lines(
    dr: Any,
    w: int,
    h: int,
    actual: list[float],
    pred: list[float],
    low: list[float],
    high: list[float],
) -> None:
    dr.rectangle((0, 0, w, 36), fill="#f4f1ea")
    dr.text((16, 12), "Backtest", fill="#222222")
    series = list(actual) + list(pred) + list(low) + list(high)
    if len(actual) < 2:
        return
    lo = min(series)
    hi = max(series)
    if hi == lo:
        hi = lo + 1.0

    def xy(i: int, value: float) -> tuple[int, int]:
        x = 30 + int(i * (w - 50) / max(1, len(actual) - 1))
        y = 50 + int((hi - value) / (hi - lo) * (h - 80))
        return x, y

    if low and high and len(low) == len(actual):
        band = [xy(i, low[i]) for i in range(len(actual))] + [
            xy(i, high[i]) for i in range(len(actual) - 1, -1, -1)
        ]
        dr.polygon(band, fill="#d9e4d4")
    dr.line([xy(i, actual[i]) for i in range(len(actual))], fill="#222222", width=2)
    dr.line([xy(i, pred[i]) for i in range(len(pred))], fill="#9a3412", width=2)


def _metric(name: str, value: float | None, higher: bool) -> dict[str, Any]:
    return {
        "name": name,
        "value": None if value is None else float(value),
        "split": "held_out",
        "higher_is_better": higher,
    }


def _pick_lower(scores: dict[str, float | None], baseline: str) -> str | None:
    return _pick(scores, baseline, False)


def _pick(scores: dict[str, float | None], baseline: str, higher: bool) -> str | None:
    winner = None
    best = None
    for name, score in scores.items():
        if score is None:
            continue
        if best is None or (score > best if higher else score < best) or (
            score == best and name == baseline
        ):
            best = score
            winner = name
    return winner


def _rmse(np: Any, pred: Any, actual: Any) -> float:
    err = np.asarray(pred, dtype=float) - np.asarray(actual, dtype=float)
    return float(np.sqrt(np.mean(err * err)))


def _band_std(np: Any, pred: Any, actual: Any) -> float:
    if pred is None:
        return 0.0
    err = np.asarray(pred, dtype=float) - np.asarray(actual, dtype=float)
    sd = float(np.std(err))
    return sd if math.isfinite(sd) else 0.0


def _safe_silhouette(fn: Any, x: Any, labels: Any) -> float | None:
    try:
        import numpy as np

        if len(set(int(v) for v in np.asarray(labels).tolist())) < 2:
            return None
        score = float(fn(x, labels))
    except Exception:  # noqa: BLE001
        return None
    if not math.isfinite(score):
        return None
    return score


def _within_budget(started: float, card: dict[str, Any]) -> bool:
    if time.monotonic() - started <= MAX_SECONDS:
        return True
    card["budgets"]["hit"] = "time_budget"
    return False


def _sort_rows(columns: list[str], rows: list[list[Any]], time_column: str) -> list[list[Any]]:
    ti = columns.index(time_column)
    return sorted(rows, key=lambda row: _sort_key(row[ti]))


def _sort_key(value: Any) -> tuple[int, float, str]:
    if value is None:
        return (1, 0.0, "")
    number = _as_float(value)
    if number is not None and not hasattr(value, "isoformat"):
        return (0, number, "")
    text = value.isoformat() if hasattr(value, "isoformat") else str(value)
    return (0, 0.0, text)


def _json_time(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    number = _as_float(value)
    if number is not None:
        return number
    return str(value)


def _as_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if hasattr(value, "isoformat"):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number
