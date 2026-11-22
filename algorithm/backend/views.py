"""Compact view models for the mini-program workspace API.

The experiment details saved by RAC-HPO can contain hundreds of trials with full
metrics.  The page never needs that payload: this module computes the summary,
chart series and paged trial rows on the Python side.
"""
from collections import Counter
import json
import math

MAX_SCATTER_POINTS = 400
MAX_PARALLEL_ROWS = 300
DISTRIBUTION_BINS = 12


def finite(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def search_trials(detail):
    return [trial for trial in (detail.get("trials") or []) if (trial.get("number") or 0) >= 0]


def baseline_trial(detail):
    for trial in detail.get("trials") or []:
        if trial.get("number") == -1:
            return trial
    return None


def trial_rows(detail):
    """Keep only the fields a table or chart needs."""
    rows = []
    for trial in search_trials(detail):
        metrics = trial.get("metrics") or {}
        rows.append({
            "number": trial.get("number"),
            "status": trial.get("status") or "unknown",
            "objective": finite(trial.get("objective")),
            "mae": finite(metrics.get("overall_mae")),
            "error": finite(metrics.get("overall_error")),
            "duration": finite(trial.get("duration_seconds")),
            "params": trial.get("params") or {},
            "selection": (trial.get("selection") or {}).get("method"),
            "failure_reason": trial.get("failure_reason"),
        })
    return rows


def _sort_key(row, key):
    if key == "status":
        return row.get("status") or ""
    return finite(row.get(key)) or 0.0


def _has_value(row, key):
    if key == "status":
        return bool(row.get("status"))
    return finite(row.get(key)) is not None


def filter_rows(rows, query="", status="", param="", low=None, high=None,
                sort="number", order="asc"):
    needle = (query or "").strip().lower()
    matched = []
    for row in rows:
        if status and row["status"] != status:
            continue
        if param:
            value = finite((row["params"] or {}).get(param))
            if value is None or (low is not None and value < low) or (high is not None and value > high):
                continue
        if needle:
            haystack = " ".join((str(row["number"]), row["status"], row["selection"] or "",
                                 row["failure_reason"] or "", json.dumps(row["params"], ensure_ascii=False)))
            if needle not in haystack.lower():
                continue
        matched.append(row)
    if sort not in {"number", "objective", "mae", "error", "duration", "status"}:
        sort = "number"
    # Missing values always stay at the bottom, regardless of sort direction.
    present = [row for row in matched if _has_value(row, sort)]
    missing = [row for row in matched if not _has_value(row, sort)]
    present.sort(key=lambda row: _sort_key(row, sort), reverse=order == "desc")
    return present + missing


def paginate(rows, page=1, size=20):
    page = max(1, page)
    size = min(max(1, size), 100)
    start = (page - 1) * size
    return {"total": len(rows), "page": page, "size": size, "rows": rows[start:start + size]}


def numeric_parameters(trials):
    names = set()
    for trial in trials:
        for key, value in (trial.get("params") or {}).items():
            if finite(value) is not None and not isinstance(value, bool):
                names.add(key)
    return sorted(names)


def charts(detail, subset=None):
    """Chart series; `subset` is the set of trial numbers highlighted by filters."""
    trials = search_trials(detail)
    selected = None if subset is None else set(subset)
    baseline = baseline_trial(detail)

    history = []
    best = math.inf
    for trial in trials:
        value = finite(trial.get("objective"))
        if value is not None and trial.get("status") == "completed":
            best = min(best, value)
        history.append({"number": trial.get("number"), "value": value,
                        "best": best if math.isfinite(best) else None,
                        "status": trial.get("status") or "unknown"})

    values = [finite(trial.get("objective")) for trial in trials
              if finite(trial.get("objective")) is not None
              and trial.get("status") == "completed"
              and (selected is None or trial.get("number") in selected)]
    distribution = {"count": len(values), "bins": [], "min": None, "max": None}
    if values:
        low, high = min(values), max(values)
        width = (high - low) or 1.0
        counts = [0] * DISTRIBUTION_BINS
        for value in values:
            index = min(DISTRIBUTION_BINS - 1, int((value - low) / width * DISTRIBUTION_BINS))
            counts[index] += 1
        distribution.update(min=low, max=high, bins=[
            {"low": low + width * index / DISTRIBUTION_BINS,
             "high": low + width * (index + 1) / DISTRIBUTION_BINS,
             "count": counts[index]} for index in range(DISTRIBUTION_BINS)])

    parameters = numeric_parameters(trials)
    scatter = []
    for name in parameters:
        points = []
        for trial in trials:
            if selected is not None and trial.get("number") not in selected:
                continue
            x_value = finite((trial.get("params") or {}).get(name))
            y_value = finite(trial.get("objective"))
            if x_value is not None and y_value is not None:
                points.append({"number": trial.get("number"), "x": x_value, "y": y_value,
                               "status": trial.get("status") or "unknown"})
        if points:
            scatter.append({"param": name, "points": points[-MAX_SCATTER_POINTS:]})

    parallel = {"params": parameters, "rows": [], "ranges": {}}
    if parameters:
        ranges = {}
        for name in parameters:
            column = [finite((trial.get("params") or {}).get(name)) for trial in trials]
            column = [value for value in column if value is not None]
            ranges[name] = (min(column), max(column)) if column else (0.0, 1.0)
        parallel["ranges"] = {name: {"low": low, "high": high} for name, (low, high) in ranges.items()}
        for trial in trials[-MAX_PARALLEL_ROWS:]:
            if selected is not None and trial.get("number") not in selected:
                continue
            column = [finite((trial.get("params") or {}).get(name)) for name in parameters]
            parallel["rows"].append({
                "number": trial.get("number"),
                "values": [None if value is None else
                           (value - ranges[name][0]) / ((ranges[name][1] - ranges[name][0]) or 1.0)
                           for value, name in zip(column, parameters)],
                "objective": finite(trial.get("objective")),
                "status": trial.get("status") or "unknown",
            })

    return {
        "history": history,
        "distribution": distribution,
        "scatter": scatter,
        "status": [{"status": key, "count": value} for key, value in
                   sorted(Counter(trial.get("status") or "unknown" for trial in trials).items())],
        "parallel": parallel,
        "parameters": parameters,
        "baseline": None if baseline is None else {
            "status": baseline.get("status"),
            "objective": finite(baseline.get("objective")),
            "mae": finite((baseline.get("metrics") or {}).get("overall_mae")),
            "duration": finite(baseline.get("duration_seconds")),
        },
        "filtered_numbers": None if selected is None else sorted(selected),
        "objective_label": detail.get("objective_definition") or "验证目标（越小越好）",
    }


def run_summary(detail):
    """Detail without trials and without chart series (those come from /charts)."""
    summary = {key: value for key, value in detail.items() if key != "trials"}
    trials = search_trials(detail)
    baseline = baseline_trial(detail)
    summary["trial_count"] = len(trials)
    summary["baseline"] = None if baseline is None else {
        "status": baseline.get("status"),
        "objective": finite(baseline.get("objective")),
        "mae": finite((baseline.get("metrics") or {}).get("overall_mae")),
        "duration": finite(baseline.get("duration_seconds")),
    }
    summary["charts"] = {
        "objective_label": detail.get("objective_definition") or "验证目标（越小越好）",
        "status": [{"status": key, "count": value} for key, value in
                   sorted(Counter(trial.get("status") or "unknown" for trial in trials).items())],
        "parameters": numeric_parameters(trials),
    }
    return summary


def compare_rows(details):
    rows = []
    for detail in details:
        config = detail.get("config") or {}
        manifest = detail.get("manifest") or {}
        rows.append({
            "id": detail.get("id"),
            "dataset": detail.get("dataset_id") or detail.get("dataset_key"),
            "algorithm": detail.get("algorithm"),
            "model": detail.get("model_name") or config.get("model", {}).get("name"),
            "status": detail.get("status"),
            "best_value": finite(detail.get("best_value")),
            "target_unit": detail.get("target_unit"),
            "objective_definition": detail.get("objective_definition"),
            "metrics": detail.get("metrics") or {},
            "seed": config.get("experiment", {}).get("seed") or config.get("seed"),
            "max_trials": config.get("optimization", {}).get("max_trials") or config.get("max_trials"),
            "timeout_seconds": config.get("optimization", {}).get("timeout_seconds") or config.get("timeout_seconds"),
            "data_sha256": detail.get("data_sha256"),
            "split_counts": manifest.get("split_counts"),
            "group_definition": manifest.get("group_definition"),
            "app_version": detail.get("app_version"),
            "algorithm_version": detail.get("algorithm_version"),
            "schema_version": detail.get("schema_version"),
            "best_params": detail.get("best_params"),
        })
    return rows


def event_rows(events, query="", page=1, size=50):
    needle = (query or "").strip().lower()
    rows = [event for event in events if not needle or
            needle in json.dumps(event, ensure_ascii=False, default=str).lower()]
    return paginate(rows, page, size)


def integer(query, key, default, minimum, maximum):
    raw = query.get(key, [str(default)])[0]
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ValueError(f"Invalid {key}")
    if not minimum <= value <= maximum:
        raise ValueError(f"Invalid {key}: expected {minimum}–{maximum}")
    return value


def optional_float(query, key):
    raw = query.get(key, [None])[0]
    if raw in (None, ""):
        return None
    value = finite(raw)
    if value is None:
        raise ValueError(f"Invalid {key}")
    return value
