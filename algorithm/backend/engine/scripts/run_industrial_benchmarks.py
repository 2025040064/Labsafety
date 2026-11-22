"""Real-data, equal-resource classification baselines and multi-seed HPO.

No downloads and no test evaluation occur here. Each run remains independently
reproducible through its config, sealed manifest, lockfile and persisted model.
"""
import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import json
import math
import os
from pathlib import Path
import statistics
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("MPLCONFIGDIR", str(ROOT / ".cache/matplotlib"))

from rac_hpo.application.industrial_service import IndustrialService
from rac_hpo.infrastructure.artifacts import utc_now, write_json


HPO_ALGORITHMS = ("random", "tpe", "rac_hpo")
METRIC_NAMES = ("overall_balanced_accuracy", "overall_f1_macro", "overall_log_loss",
                "overall_error", "robust_objective")


def campaign_row(detail):
    """Keep actual run evidence, rather than infer it from campaign arguments."""
    row = {key: deepcopy(detail.get(key)) for key in (
        "id", "dataset_key", "model_name", "algorithm", "status", "task_type", "best_trial",
        "best_value", "duration_seconds", "failure_reason", "data_sha256", "config", "budget",
        "objective_definition", "search_space", "baseline_errors", "weights", "training_version")}
    trials = detail.get("trials", [])
    row.update(seed=detail["config"]["seed"],
               split_fingerprint=detail.get("manifest", {}).get("split_fingerprint"),
               data_origin=detail.get("manifest", {}).get("data_origin"),
               dataset_version=detail.get("manifest", {}).get("dataset_version"),
               metrics=deepcopy(detail.get("metrics", {})),
               search_seconds=detail.get("search_duration_seconds"),
               completed_trials=sum(item["number"] >= 0 and item["status"] == "completed" for item in trials),
               completed_trial_numbers=sorted(item["number"] for item in trials
                                              if item["number"] >= 0 and item["status"] == "completed"),
               selection_counts=dict(Counter(item.get("selection", {}).get("method", "none")
                                             for item in trials if item["number"] >= 0)))
    return row


def _statistics(runs):
    result = {}
    for name in (*METRIC_NAMES, "duration_seconds", "search_seconds"):
        values = [(run if name.endswith("seconds") else run.get("metrics", {})).get(name) for run in runs]
        values = [value for value in values if isinstance(value, (int, float))
                  and not isinstance(value, bool) and math.isfinite(value)]
        if values:
            result[name] = {"mean": statistics.mean(values), "std": statistics.stdev(values) if len(values) > 1 else None,
                            "count": len(values)}
    return result


def _descriptive_summary(runs):
    grouped = defaultdict(list)
    for run in runs:
        if run["status"] == "completed":
            grouped[(run["dataset_key"], run["model_name"], run["algorithm"])].append(run)
    return [{"dataset_key": dataset, "model_name": model, "algorithm": algorithm,
             "run_ids": [run["id"] for run in items], "seeds": [run["seed"] for run in items],
             "descriptive_only": True,
             "comparison_scope": "successful runs only; not a fairness proof or ranking",
             **_statistics(items)} for (dataset, model, algorithm), items in sorted(grouped.items())]


def summarize_campaign(campaign):
    """Generate fair means only from the full predeclared paired seed cohort.

    Default baselines are displayed separately. Successful partial cohorts are
    still visible as descriptive statistics, but never silently replace failed
    seeds or mix changed source/split/budget evidence in a fair comparison.
    """
    config, runs = campaign["config"], campaign.get("runs", [])
    requested_seeds = config["seeds"]
    requested_trials, requested_seconds = config["trials"], config["seconds"]
    hpo = [run for run in runs if run["algorithm"] != "baseline"]
    result = {"summary": _descriptive_summary(hpo),
              "baseline_summary": _descriptive_summary([run for run in runs if run["algorithm"] == "baseline"]),
              "fair_comparisons": [], "noncomparable": []}
    datasets = sorted(set(config.get("datasets", [])) | {run["dataset_key"] for run in hpo})
    for dataset in datasets:
        dataset_runs = [run for run in hpo if run["dataset_key"] == dataset]
        models = sorted({run["model_name"] for run in dataset_runs}) or ["lightgbm"]
        for model in models:
            candidates = [run for run in dataset_runs if run["model_name"] == model]
            reasons = []

            def reject(code, **evidence):
                reasons.append({"code": code, **evidence})

            if len(set(requested_seeds)) != len(requested_seeds):
                reject("duplicate_requested_seed")
            for failure in campaign.get("failures", []):
                if failure.get("dataset_key") == dataset:
                    reject("acquisition_or_audit_failure", failure=failure)
            selected = []
            for algorithm in HPO_ALGORITHMS:
                for seed in dict.fromkeys(requested_seeds):
                    matching = [run for run in candidates if run["algorithm"] == algorithm and run["seed"] == seed]
                    if len(matching) != 1:
                        reject("missing_run" if not matching else "duplicate_run", algorithm=algorithm,
                               seed=seed, run_ids=[run["id"] for run in matching])
                        continue
                    run = matching[0]
                    selected.append(run)
                    if run["status"] != "completed":
                        reject("run_not_completed", run_id=run["id"], status=run["status"])
                    if (run.get("completed_trials") != requested_trials
                            or ("completed_trial_numbers" in run and run["completed_trial_numbers"] != list(range(requested_trials)))):
                        reject("incomplete_trial_budget", run_id=run["id"],
                               completed_trials=run.get("completed_trials"), requested_trials=requested_trials)
                    if any(not isinstance(run.get("metrics", {}).get(name), (int, float))
                           or isinstance(run["metrics"][name], bool)
                           or not math.isfinite(run["metrics"][name]) for name in METRIC_NAMES):
                        reject("missing_or_invalid_metrics", run_id=run["id"])
            for run in candidates:
                if run["algorithm"] not in HPO_ALGORITHMS or run["seed"] not in requested_seeds:
                    reject("unexpected_run", run_id=run["id"], algorithm=run["algorithm"], seed=run["seed"])

            def shared(name, values, required=True):
                if required and any(value is None or value == {} or value == "" for value in values):
                    reject(f"{name}_missing")
                if len({json.dumps(value, sort_keys=True, allow_nan=False) for value in values}) > 1:
                    reject(f"{name}_mismatch")

            if selected:
                shared("source", [run.get("data_sha256") for run in selected])
                shared("split", [run.get("split_fingerprint") for run in selected])
                shared("objective", [run.get("objective_definition") for run in selected])
                shared("search_space", [run.get("search_space") for run in selected])
                shared("task", [run.get("task_type") for run in selected])
                shared("resources", [(run.get("budget") or {}).get("model_resources") for run in selected])
                shared("budget", [{key: (run.get("budget") or {}).get(key) for key in (
                    "max_trials", "timeout_seconds", "baseline_in_search_budget", "running_trial_can_overrun_deadline")}
                    for run in selected])
                for run in selected:
                    budget, run_config = run.get("budget") or {}, run.get("config") or {}
                    resources = budget.get("model_resources") or {}
                    if (budget.get("max_trials") != requested_trials or budget.get("timeout_seconds") != requested_seconds
                            or budget.get("baseline_in_search_budget") is not False
                            or run_config.get("max_trials") != requested_trials
                            or run_config.get("timeout_seconds") != requested_seconds):
                        reject("budget_mismatch", run_id=run["id"])
                    if (resources != run_config.get("model_config") or resources.get("n_estimators") != config["estimators"]
                            or resources.get("n_jobs") != config["threads"]):
                        reject("resources_mismatch", run_id=run["id"])
                    if (run.get("task_type") != "classification" or run_config.get("evaluation_partition") != "validation"
                            or run_config.get("test_accessed") is not False or run_config.get("seed") != run["seed"]):
                        reject("evaluation_contract_mismatch", run_id=run["id"])
                # Baselines may vary by random seed, but must be identical across
                # algorithms within each seed for fixed normalized J comparisons.
                for seed in dict.fromkeys(requested_seeds):
                    paired = [run for run in selected if run["seed"] == seed]
                    shared("baseline", [run.get("baseline_errors") for run in paired])
                    shared("weights", [run.get("weights") for run in paired])
            entry = {"dataset_key": dataset, "model_name": model, "seeds": list(requested_seeds),
                     "run_ids": [run["id"] for run in candidates]}
            if reasons:
                result["noncomparable"].append({**entry, "reasons": reasons,
                                                "comparison_scope": "no fair aggregate; inspect descriptive results and individual runs"})
                continue
            if not selected:
                result["noncomparable"].append({**entry, "reasons": [{"code": "missing_run"}]})
                continue
            result["fair_comparisons"].append({
                **entry, "descriptive_only": False, "paired_all_requested_seeds": True,
                "data_sha256": selected[0]["data_sha256"], "split_fingerprint": selected[0]["split_fingerprint"],
                "budget": deepcopy(selected[0]["budget"]),
                "objective_definition": deepcopy(selected[0]["objective_definition"]),
                "search_space": deepcopy(selected[0]["search_space"]),
                "algorithms": {algorithm: {"run_ids": [run["id"] for run in selected if run["algorithm"] == algorithm],
                                            "seeds": list(requested_seeds),
                                            **_statistics([run for run in selected if run["algorithm"] == algorithm])}
                               for algorithm in HPO_ALGORITHMS},
                "per_seed": [{"seed": seed, "baseline_errors": deepcopy(next(run["baseline_errors"] for run in selected if run["seed"] == seed)),
                              "weights": deepcopy(next(run["weights"] for run in selected if run["seed"] == seed)),
                              "run_ids": {run["algorithm"]: run["id"] for run in selected if run["seed"] == seed}}
                             for seed in requested_seeds],
            })
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--datasets", nargs="+", default=["ai4i_2020", "secom", "paderborn"])
    parser.add_argument("--seeds", nargs="+", type=int, default=[42, 71, 103])
    parser.add_argument("--trials", type=int, default=24)
    parser.add_argument("--seconds", type=float, default=300)
    parser.add_argument("--estimators", type=int, default=120)
    parser.add_argument("--threads", type=int, default=2)
    parser.add_argument("--decision-mode", choices=["native", "time_oof"], default="native")
    parser.add_argument("--skip-baselines", action="store_true")
    args = parser.parse_args()
    if len(set(args.seeds)) != len(args.seeds):
        parser.error("--seeds must contain distinct seeds")
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    service = IndustrialService(ROOT)
    directory = ROOT / "workspace/industrial_benchmarks" / f"campaign-{uuid.uuid4().hex[:12]}"
    directory.mkdir(parents=True)
    campaign = {"created_at": utc_now(), "config": vars(args), "runs": [], "failures": [],
                "test_accessed": False, "evaluation_partition": "validation"}
    write_json(directory / "campaign.json", campaign)
    for dataset_key in args.datasets:
        try:
            audit = service.audit(dataset_key)
            write_json(directory / f"audit-{dataset_key}.json", audit)
        except Exception as error:
            campaign["failures"].append({"dataset_key": dataset_key, "stage": "audit",
                                         "error": f"{type(error).__name__}: {error}"})
            write_json(directory / "campaign.json", campaign)
            continue
        schedule = [] if args.skip_baselines else [(name, "baseline", args.seeds[0]) for name in
                                                   ("random_forest", "xgboost", "lightgbm")]
        schedule += [("lightgbm", algorithm, seed) for seed in args.seeds
                     for algorithm in ("random", "tpe", "rac_hpo")]
        for model_name, algorithm, seed in schedule:
            print(f"RUN {dataset_key} {model_name} {algorithm} seed={seed}", flush=True)
            model_config = {"n_estimators": args.estimators, "n_jobs": args.threads}
            if args.decision_mode != "native":
                model_config["decision_mode"] = args.decision_mode
            detail = service.run(dataset_key, model_name=model_name, algorithm=algorithm, seed=seed,
                                 max_trials=args.trials, timeout_seconds=args.seconds,
                                 model_config=model_config)
            row = campaign_row(detail)
            campaign["runs"].append(row)
            write_json(directory / "campaign.json", campaign)
            print(json.dumps({key: row[key] for key in ("id", "status", "best_value", "completed_trials")}), flush=True)
    campaign.update(ended_at=utc_now(), **summarize_campaign(campaign))
    write_json(directory / "campaign.json", campaign)
    write_json(directory / "summary.json", {key: campaign[key] for key in (
        "summary", "baseline_summary", "fair_comparisons", "noncomparable")})
    print(f"CAMPAIGN {directory}", flush=True)
    return 1 if campaign["failures"] or campaign["noncomparable"] or any(run["status"] != "completed" for run in campaign["runs"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
