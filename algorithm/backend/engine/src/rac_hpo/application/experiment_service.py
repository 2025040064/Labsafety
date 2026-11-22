import csv
import io
import json
import os
import shutil
import time
import uuid
from pathlib import Path

from rac_hpo import ALGORITHM_VERSION, SCHEMA_VERSION, __version__
from rac_hpo.domain.schemas import TaskCancelled
from rac_hpo.infrastructure.artifacts import (
    atomic_text, environment_snapshot, file_hash, save_convergence, utc_now,
    write_json, write_yaml, source_fingerprint,
)
from rac_hpo.infrastructure.config_loader import load_config
from rac_hpo.infrastructure.database import ExperimentRepository


class ApplicationService:
    """The only experiment API used by both CLI and GUI."""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        os.environ.setdefault("MPLCONFIGDIR", str(self.root / ".cache/matplotlib"))
        self.config_path = self.root / "configs/default.yaml"
        self.repository = ExperimentRepository(self.root / "workspace/database/experiments.sqlite3")

    def _prepare(self, path=None, config=None, dataset_id=None):
        from rac_hpo.data.registry import DEFAULT_PATHS, prepare
        config = config or load_config(self.config_path)
        data_path = Path(path or (DEFAULT_PATHS[dataset_id] if dataset_id else config["data"]["path"]))
        if not data_path.is_absolute():
            data_path = self.root / data_path
        return prepare(data_path, self.root / "datasets/manifests")

    def prepare_dataset(self, path=None, dataset_id=None) -> dict:
        dataset = self._prepare(path, dataset_id=dataset_id)
        from rac_hpo.data.quality_audit import audit_dataset
        return {
            "dataset_id": dataset.dataset_id, "row_count": len(dataset.y),
            "target": dataset.target, "target_unit": dataset.target_unit,
            "features": dataset.features, "feature_names": dataset.features,
            "source_path": dataset.source_path, "source_sha256": dataset.source_sha256,
            "regimes": {str(key): int(value) for key, value in dataset.regimes.value_counts().items()},
            "split_counts": {key: len(value) for key, value in dataset.splits.items()},
            "manifest": dataset.manifest, "quality_audit": audit_dataset(dataset),
            "limitations": dataset.manifest.get("limitations", [
                "Calendar-only regression; no production schedule or electrical measurement inputs.",
                "Date blocks are temporal groups, not proven independent production batches.",
                "No independent manufacturing-batch confidence claims from date blocks.",
            ]),
        }

    def run_experiment(self, algorithm="baseline", data_path=None, overrides=None,
                       progress_callback=None, cancel_check=None, dataset_view_id=None) -> dict:
        from rac_hpo.optimization.base import optimize
        from rac_hpo.optimization.robust_objective import apply_robust_metrics
        from rac_hpo.training.lightgbm_trainer import train

        from rac_hpo.optimization.baselines import SEARCH_METHODS
        if algorithm not in SEARCH_METHODS | {"baseline"}:
            raise ValueError(f"Unsupported method: {algorithm}")
        config = load_config(self.config_path, overrides)
        if dataset_view_id is not None:
            from rac_hpo.data.loop_views import load_view
            dataset = load_view(self.root, dataset_view_id)
        else:
            dataset = self._prepare(data_path, config)
        config["data"]["path"] = dataset.source_path
        config["data"]["dataset_id"] = "cnc_milling_2025" if "ADOC" in dataset.features else "steel_energy"
        if algorithm != "baseline":
            config["optimization"]["algorithm"] = algorithm
        experiment_id = f"EXP-{uuid.uuid4().hex[:16]}"
        directory = self.root / "workspace/experiments" / experiment_id
        directory.mkdir(parents=True, exist_ok=False)
        (directory / "artifacts").mkdir()
        (directory / "logs").mkdir()
        started = time.perf_counter()
        detail = {
            "id": experiment_id, "algorithm": algorithm, "status": "running",
            "created_at": utc_now(), "dataset_id": dataset.dataset_id,
            "data_sha256": dataset.source_sha256, "target_unit": dataset.target_unit,
            "app_version": __version__, "algorithm_version": ALGORITHM_VERSION if algorithm == "rac_hpo" else "baseline-1.0",
            "schema_version": SCHEMA_VERSION, "metrics": {}, "best_params": {},
            "best_value": None, "best_trial": None, "duration_seconds": 0.0,
            "artifacts": {}, "directory": str(directory), "config": config,
        }
        if dataset_view_id is not None:
            detail['dataset_view_id'] = dataset_view_id
            detail['data_mode'] = dataset.manifest['data_mode']
        write_json(directory / "manifest.json", dataset.manifest)
        write_yaml(directory / "config.yaml", config)
        if (self.root / "uv.lock").is_file():
            shutil.copy2(self.root / "uv.lock", directory / "uv.lock")
        environment = environment_snapshot(self.root)
        if dataset_view_id is not None:
            implementation_root = Path(__file__).resolve().parents[3]
            environment['implementation_root'] = str(implementation_root)
            environment['implementation_source_sha256'] = source_fingerprint(implementation_root)
        write_json(directory / "environment.json", environment)
        self.repository.save_experiment(detail)
        records = []
        baseline_attempt_started = None

        def emit(event):
            event = {"timestamp": utc_now(), "experiment_id": experiment_id, **event}
            if dataset_view_id is not None:
                event['data_mode'] = detail['data_mode']
            with (directory / "logs/experiment.log").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n")
            if progress_callback:
                progress_callback(event)

        def save_trial(record):
            self.repository.save_trial(experiment_id, record)
            records.append(record)
            atomic_text(directory / "trials.jsonl", "".join(
                json.dumps(value, ensure_ascii=False, allow_nan=False) + "\n" for value in records
            ))

        try:
            from rac_hpo.data.quality_audit import audit_dataset
            quality_path = directory / "quality_audit.json"
            write_json(quality_path, audit_dataset(dataset))
            detail["artifacts"]["quality_audit"] = str(quality_path)
            emit({"type": "started", "algorithm": algorithm, "message": "Preparing default baseline"})
            if cancel_check and cancel_check():
                raise TaskCancelled("Cancelled before training")
            seed = config["experiment"]["seed"]
            model_config = {key: value for key, value in config["model"].items() if key != "name"}
            robustness = config["robustness"]
            baseline_attempt_started = time.perf_counter()
            evaluation_config = {"minimum_groups": config["robustness"]["minimum_independent_groups"]}
            baseline = train(dataset, {}, seed, model_config, cancel_check, evaluation_config)
            if cancel_check and cancel_check():
                raise TaskCancelled("Baseline cancellation acknowledged")
            baseline_errors = {key: value["mae"] for key, value in baseline.metrics["regimes"].items()}
            counts = {key: value["samples"] for key, value in baseline.metrics["regimes"].items()}
            weights = {key: count / sum(counts.values()) for key, count in counts.items()}
            objective_controls = {
                "baseline_errors": baseline_errors, "weights": weights,
                "alpha": robustness["alpha"], "epsilon": robustness["epsilon"],
            }
            baseline.metrics = apply_robust_metrics(baseline.metrics, **objective_controls)
            detail["fixed_objective"] = objective_controls
            detail["baseline_metrics"] = baseline.metrics
            detail["baseline_duration_seconds"] = baseline.duration_seconds
            write_json(directory / "fixed_objective.json", objective_controls)
            best_model = baseline.model
            best_iteration = baseline.best_iteration
            baseline_record = {
                "number": -1, "params": baseline.params, "seed": seed,
                "status": "completed", "duration_seconds": baseline.duration_seconds,
                "metrics": baseline.metrics,
                "objective": baseline.metrics["robust_objective" if algorithm in {"tpe_robust", "rac_hpo"} else "overall_mae"],
                "failure_reason": None, "role": "default_baseline",
            }
            save_trial(baseline_record)
            emit({"type": "baseline_completed", "metrics": baseline.metrics,
                  "duration_seconds": baseline.duration_seconds})
            if algorithm == "baseline":
                detail.update(status="completed", metrics=baseline.metrics, best_params=baseline.params,
                              best_value=baseline.metrics["overall_mae"], best_trial=-1,
                              search_duration_seconds=0.0)
            else:
                latest = {}
                best_search_value = float("inf")

                def runner(params, number, report=None):
                    trained = train(dataset, params, seed, model_config, cancel_check, evaluation_config,
                                    resource_callback=report)
                    latest.clear()
                    latest[number] = trained
                    return trained

                def progress(event):
                    nonlocal best_model, best_iteration, best_search_value
                    if event["type"] == "trial_completed":
                        trial = event["trial"]
                        save_trial(trial)
                        if trial["status"] == "completed" and trial["objective"] < best_search_value:
                            best_search_value = trial["objective"]
                            trained = latest[trial["number"]]
                            best_model = trained.model
                            best_iteration = trained.best_iteration
                            detail["metrics"] = trial["metrics"]
                    emit(event)

                search_config = {**config["optimization"], **objective_controls, "seed": seed,
                                 "baseline_overall_mae": baseline.metrics["overall_mae"],
                                 "minimum_independent_groups": robustness["minimum_independent_groups"],
                                 "max_resource": model_config["n_estimators"]}
                result = optimize(search_config, runner, progress, cancel_check)
                detail.update(status=result.status, best_params=result.best_params,
                              best_value=result.best_value, best_trial=result.best_trial,
                              search_duration_seconds=result.duration_seconds)
                if result.status == "failed" and result.best_trial is None:
                    detail["failure_reason"] = (
                        "No successful trial; inspect trial failure reasons and the configured time budget"
                    )
                detail["budget_overrun_seconds"] = max(0.0, result.duration_seconds - search_config["timeout_seconds"])
                if algorithm == "rac_hpo":
                    from collections import Counter
                    detail["search_diagnostics"] = {
                        "decision_counts": dict(Counter(trial.get("selection", {}).get("method", "unknown") for trial in result.trials)),
                        "fallback_reasons": dict(Counter(trial.get("selection", {}).get("reason", "unknown") for trial in result.trials
                                                         if trial.get("selection", {}).get("method") == "tpe_fallback")),
                        "selection_seconds": sum(trial.get("selection_duration_seconds", 0) for trial in result.trials),
                    }
            if best_model is not None and (detail["best_trial"] is not None):
                model_path = directory / "artifacts/model.txt"
                atomic_text(model_path, best_model.booster_.model_to_string())
                detail["best_iteration"] = best_iteration
                detail["artifacts"]["model"] = str(model_path)
            if records:
                curve = directory / "artifacts/convergence.png"
                save_convergence(curve, [record for record in records if record["number"] >= 0] or records)
                detail["artifacts"]["convergence"] = str(curve)
            emit({"type": "finished", "status": detail["status"], "best_value": detail["best_value"]})
        except TaskCancelled as error:
            detail["status"] = "cancelled"
            detail["failure_reason"] = str(error)
            emit({"type": "cancelled", "message": str(error)})
        except Exception as error:
            detail["status"] = "failed"
            detail["failure_reason"] = f"{type(error).__name__}: {error}"
            emit({"type": "error", "message": detail["failure_reason"]})
        finally:
            if baseline_attempt_started is not None and not any(record["number"] == -1 for record in records):
                save_trial({"number": -1, "params": {}, "seed": config["experiment"]["seed"],
                            "status": detail["status"], "metrics": {}, "objective": None,
                            "duration_seconds": time.perf_counter() - baseline_attempt_started,
                            "failure_reason": detail.get("failure_reason"), "role": "default_baseline"})
            detail["duration_seconds"] = time.perf_counter() - started
            detail["ended_at"] = utc_now()
            write_json(directory / "metrics.json", detail["metrics"])
            for name, path in detail["artifacts"].items():
                self.repository.save_artifact(experiment_id, name, path, file_hash(path))
            self.repository.save_experiment(detail)
            write_json(directory / "result.json", {**detail, "trials": records})
        return self.get_experiment(experiment_id)

    def list_experiments(self) -> list[dict]:
        results = self.repository.list_experiments()
        for result in results:
            trials = self.repository.get_experiment(result["id"])["trials"]
            result["trial_count"] = sum(trial["number"] >= 0 for trial in trials)
        return results

    def get_experiment(self, experiment_id: str) -> dict:
        return self.repository.get_experiment(experiment_id)

    def export_results(self, experiment_id: str, destination=None) -> str:
        detail = self.get_experiment(experiment_id)
        if destination:
            destination = Path(destination).resolve()
            destination.mkdir(parents=True, exist_ok=True)
        else:
            destination = self.root / "workspace/exports"
            destination.mkdir(parents=True, exist_ok=True)
        directory = destination / f"{experiment_id}-{uuid.uuid4().hex[:8]}"
        directory.mkdir(exist_ok=False)
        write_json(directory / "experiment.json", detail)
        buffer = io.StringIO(newline="")
        columns = ["number", "status", "duration_seconds", "objective", "params", "metrics", "failure_reason"]
        writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for trial in detail["trials"]:
            writer.writerow({**trial, "params": json.dumps(trial["params"]),
                             "metrics": json.dumps(trial["metrics"])})
        atomic_text(directory / "trials.csv", buffer.getvalue())
        source = Path(detail["directory"])
        manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
        for filename in ("config.yaml", "manifest.json", "environment.json", "fixed_objective.json", "uv.lock", "test_evaluation.json"):
            if (source / filename).exists():
                shutil.copy2(source / filename, directory / filename)
        for path in detail["artifacts"].values():
            shutil.copy2(path, directory / Path(path).name)
        atomic_text(directory / "report.md", (
            f"# {experiment_id}\n\n" + (f"Data mode: {detail['data_mode']} (offline simulation)\n\n" if detail.get('data_mode') else '') +
            f"Method: {detail['algorithm']}\n\n"
            f"Status: {detail['status']}\n\nValidation metrics ({detail['target_unit']}):\n\n"
            f"```json\n{json.dumps(detail['metrics'], indent=2)}\n```\n\n"
            f"Grouping: {manifest.get('group_definition', 'See manifest')}\n"
            "Test data was excluded from all search decisions.\n"
        ))
        return str(directory)

    def finalize_experiment(self, experiment_id: str) -> dict:
        """One explicit sealed-test evaluation, using saved parameters and iteration count."""
        import pandas as pd
        from rac_hpo.evaluation.evaluator import evaluate
        from rac_hpo.training.lightgbm_trainer import build_regressor

        detail = self.get_experiment(experiment_id)
        if detail.get('dataset_view_id'):
            raise ValueError('Closed-loop dataset views cannot be finalized against the sealed test')
        if detail.get("test_evaluation"):
            raise ValueError("Test was already evaluated for this experiment; use saved report")
        if detail["status"] != "completed" or detail.get("best_trial") is None:
            raise ValueError("Only completed experiments with a selected model can be finalized")
        dataset = self._prepare(detail["config"]["data"]["path"], detail["config"])
        if dataset.source_sha256 != detail["data_sha256"]:
            raise ValueError("Source data checksum changed; refusing sealed-test evaluation")
        saved_manifest = json.loads((Path(detail["directory"]) / "manifest.json").read_text(encoding="utf-8"))
        if dataset.manifest != saved_manifest:
            raise ValueError("Prepared dataset protocol changed; refusing sealed-test evaluation")
        x_train, y_train, _, _ = dataset.split("train")
        x_valid, y_valid, _, _ = dataset.split("validation")
        x_test, y_test, regimes, groups = dataset.split("test")
        model_config = {key: value for key, value in detail["config"]["model"].items() if key != "name"}
        model_config["n_estimators"] = detail["best_iteration"]
        model = build_regressor(detail["best_params"], detail["config"]["experiment"]["seed"], model_config)
        model.fit(pd.concat([x_train, x_valid]), pd.concat([y_train, y_valid]))
        controls = detail["fixed_objective"]
        metrics = evaluate(y_test, model.predict(x_test), regimes, groups, **controls,
                           group_kind=dataset.manifest.get("independent_unit_kind", "calendar_blocks"),
                           minimum_groups=detail["config"]["robustness"]["minimum_independent_groups"])
        report = {"evaluated_at": utc_now(), "protocol": "refit_train_plus_validation_fixed_best_iteration",
                  "metrics": metrics, "test_rows": len(y_test), "best_iteration": detail["best_iteration"]}
        directory = Path(detail["directory"])
        model_path = directory / "artifacts/final_model.txt"
        atomic_text(model_path, model.booster_.model_to_string())
        write_json(directory / "test_evaluation.json", report)
        self.repository.save_artifact(experiment_id, "final_model", str(model_path), file_hash(model_path))
        detail["artifacts"]["final_model"] = str(model_path)
        detail["test_evaluation"] = report
        detail.pop("trials", None)
        self.repository.save_experiment(detail)
        write_json(directory / "result.json", self.get_experiment(experiment_id))
        return report
