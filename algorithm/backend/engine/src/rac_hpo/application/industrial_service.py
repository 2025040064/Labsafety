"""Append-only industrial classification runs, shared by CLI and desktop UI."""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import time
import traceback
import uuid
from pathlib import Path

import joblib

from rac_hpo.domain.schemas import TaskCancelled
from rac_hpo.infrastructure.artifacts import (
    atomic_text, environment_snapshot, file_hash, save_convergence, utc_now, write_json,
)


DATASET_KEYS = ("ai4i_2020", "secom", "paderborn", "xjtu_sy", "lenovo", "pneumatic_pressure", "user_csv")
MODEL_NAMES = ("random_forest", "xgboost", "lightgbm")
ALGORITHMS = ("baseline", "random", "tpe", "rac_hpo")


class IndustrialService:
    """Keep classification metrics and artifacts separate from regression history.

    Raw data acquisition is always explicit. A failed import or training attempt
    still receives its own durable run and error log. No existing DB is changed.
    """

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.runs = self.root / "workspace/industrial_runs"
        root_key = hashlib.sha256(str(self.root).encode()).hexdigest()[:16]
        self.manifests = self.root / "datasets/manifests/industrial-live" / root_key
        os.environ.setdefault("MPLCONFIGDIR", str(self.root / ".cache/matplotlib"))

    def _data_path(self, dataset_key, path=None):
        from rac_hpo.data.industrial import DEFAULT_PATHS
        if dataset_key not in DATASET_KEYS:
            raise ValueError(f"unsupported industrial dataset: {dataset_key}")
        target = Path(path or DEFAULT_PATHS[dataset_key])
        return target if target.is_absolute() else self.root / target

    def _prepare(self, dataset_key, path, mapping=None):
        from rac_hpo.data.industrial import prepare
        if dataset_key == 'user_csv':
            from rac_hpo.data.industrial.user_csv import prepare_user_csv
            if path is None:
                raise ValueError('Upload a CSV file first')
            path = Path(path)
            return prepare_user_csv(path, mapping, self.manifests / 'user_csv' / path.parent.name)
        return prepare(dataset_key, self._data_path(dataset_key, path), self.manifests,
                       archive_cache_dir=self.root / "datasets/processed/industrial")

    def audit(self, dataset_key, path=None, mapping=None):
        dataset = self._prepare(dataset_key, path, mapping)
        from rac_hpo.data.quality_audit import audit_dataset
        return {"dataset_key": dataset_key, "dataset_id": dataset.dataset_id,
                "quality_audit": audit_dataset(dataset),
                "task_type": dataset.manifest["task_type"], "row_count": len(dataset.y),
                "features": dataset.features, "target": dataset.target,
                "source_sha256": dataset.source_sha256,
                "split_counts": {key: len(value) for key, value in dataset.splits.items()},
                "regimes": {str(key): int(value) for key, value in dataset.regimes.value_counts().items()},
                "manifest": dataset.manifest}

    def fetch(self, dataset_key):
        from rac_hpo.data.industrial import fetch
        target = self._data_path(dataset_key)
        log_dir = self.root / "workspace/industrial_acquisition"
        log_dir.mkdir(parents=True, exist_ok=True)
        record = {"dataset_key": dataset_key, "started_at": utc_now(), "status": "running"}
        record_path = log_dir / f"{dataset_key}-{uuid.uuid4().hex[:12]}.json"
        write_json(record_path, record)
        try:
            downloaded = fetch(dataset_key, target)
            record.update(status="completed", path=str(downloaded), ended_at=utc_now())
        except Exception as error:
            record.update(status="failed", error=f"{type(error).__name__}: {error}", ended_at=utc_now())
            write_json(record_path, record)
            raise
        write_json(record_path, record)
        return {**record, "acquisition_log": str(record_path)}

    def run(self, dataset_key, model_name="lightgbm", algorithm="baseline", data_path=None,
            seed=42, max_trials=24, timeout_seconds=300, model_config=None,
            progress_callback=None, cancel_check=None, mapping=None):
        from rac_hpo.optimization.classification import optimize
        from rac_hpo.training.classification import train

        if dataset_key not in DATASET_KEYS or model_name not in MODEL_NAMES or algorithm not in ALGORITHMS:
            raise ValueError("unsupported dataset, classification model or algorithm")
        config = {"dataset_key": dataset_key, "model_name": model_name, "algorithm": algorithm,
                  "seed": seed, "max_trials": max_trials, "timeout_seconds": timeout_seconds,
                  "model_config": {"n_estimators": 120, "early_stopping_rounds": 20,
                                   "n_jobs": 2, **(model_config or {})},
                  "data_path": str(self._data_path(dataset_key, data_path)) if data_path is not None or dataset_key != 'user_csv' else None,
                  "evaluation_partition": "validation", "test_accessed": False}
        if dataset_key == 'user_csv':
            config['mapping'] = mapping
        if config["model_config"].get("decision_mode") == "time_oof":
            config["model_config"].setdefault("oof_folds", 3)
        run_id = f"IND-{uuid.uuid4().hex[:16]}"
        directory = self.runs / run_id
        directory.mkdir(parents=True, exist_ok=False)
        (directory / "artifacts").mkdir()
        (directory / "logs").mkdir()
        detail = {"id": run_id, "status": "running", "created_at": utc_now(),
                  "task_type": "classification", "dataset_key": dataset_key,
                  "model_name": model_name, "algorithm": algorithm,
                  "config": config, "directory": str(directory), "metrics": {},
                  "best_params": {}, "best_trial": None, "best_value": None,
                  "artifacts": {"log": str(directory / "logs/experiment.log")},
                  "trials": [], "manifest": {}, "duration_seconds": 0.0,
                  "pid": os.getpid(), "training_version": "industrial-classification-1.0"}
        write_json(directory / "config.json", config)
        write_json(directory / "run.json", detail)
        started = time.perf_counter()

        def persist():
            write_json(directory / "run.json", detail)

        def emit(event):
            message = {"timestamp": utc_now(), "run_id": run_id, "experiment_id": run_id, **event}
            with (directory / "logs/experiment.log").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(message, ensure_ascii=False, allow_nan=False) + "\n")
            if progress_callback:
                progress_callback(message)

        def progress(event):
            if event.get("type") in {"trial_completed", "baseline_completed"} and "trial" in event:
                detail["trials"].append(event["trial"])
                atomic_text(directory / "trials.jsonl", "".join(
                    json.dumps(item, ensure_ascii=False, allow_nan=False) + "\n"
                    for item in detail["trials"]))
                persist()
            emit(event)

        try:
            emit({"type": "started", "message": "Auditing sealed industrial classification dataset"})
            if cancel_check and cancel_check():
                raise TaskCancelled("cancelled before dataset preparation")
            dataset = self._prepare(dataset_key, Path(config["data_path"]) if config["data_path"] else None, mapping)
            if dataset.manifest.get("task_type") != "classification":
                raise ValueError("This training path requires a classification task")
            detail.update(dataset_id=dataset.dataset_id, data_sha256=dataset.source_sha256,
                          manifest=dataset.manifest)
            write_json(directory / "manifest.json", dataset.manifest)
            from rac_hpo.data.quality_audit import audit_dataset
            detail["quality_audit"] = audit_dataset(dataset)
            quality_path = directory / "quality_audit.json"
            write_json(quality_path, detail["quality_audit"])
            detail["artifacts"]["quality_audit"] = str(quality_path)
            write_json(directory / "environment.json", environment_snapshot(self.root))
            if (self.root / "uv.lock").is_file():
                shutil.copy2(self.root / "uv.lock", directory / "uv.lock")
            persist()
            if algorithm == "baseline":
                result = train(dataset, model_name, seed=seed, model_config=config["model_config"],
                               cancel_check=cancel_check)
                record = {"number": -1, "role": "default_baseline", "params": result.params,
                          "seed": seed, "status": result.status, "metrics": result.metrics,
                          "duration_seconds": result.duration_seconds,
                          "objective": result.metrics["overall_error"], "failure_reason": result.failure_reason}
                progress({"type": "trial_completed", "trial": record})
                detail.update(status=result.status, best_params=result.params, best_trial=-1,
                              best_value=result.metrics["overall_error"], metrics=result.metrics,
                              objective_definition="validation balanced error = 1 - balanced accuracy")
            else:
                search = optimize(dataset, model_name, algorithm, seed=seed, max_trials=max_trials,
                                  timeout_seconds=timeout_seconds, model_config=config["model_config"],
                                  progress_callback=progress, cancel_check=cancel_check)
                result = search.get("best_result")
                detail.update({key: value for key, value in search.items()
                               if key not in {"best_result", "baseline_result", "trials", "duration_seconds"}})
                detail["trials"] = search["trials"]
                detail["search_duration_seconds"] = search.get("search_seconds", search.get("duration_seconds"))
                baseline_result = search.get("baseline_result")
                if baseline_result:
                    detail["baseline_metrics"] = baseline_result.metrics
                if result is not None:
                    detail["metrics"] = result.metrics
                if detail["status"] == "failed":
                    detail["failure_reason"] = "No successful search trial; inspect saved trial errors"
            if result is not None and detail["status"] == "completed":
                buffer = io.BytesIO()
                joblib.dump(result.model, buffer, compress=3)
                model_path = directory / "artifacts/model.joblib"
                temporary = model_path.with_suffix(".tmp")
                try:
                    with temporary.open("wb") as handle:
                        handle.write(buffer.getvalue())
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.replace(temporary, model_path)
                finally:
                    temporary.unlink(missing_ok=True)
                detail["artifacts"]["model"] = str(model_path)
                detail["model_sha256"] = file_hash(model_path)
                detail["best_iteration"] = result.best_iteration
                self._curves(directory, detail)
        except TaskCancelled as error:
            detail.update(status="cancelled", failure_reason=str(error))
            emit({"type": "cancelled", "message": str(error)})
        except Exception as error:
            detail.update(status="failed", failure_reason=f"{type(error).__name__}: {error}")
            if not detail["trials"]:
                detail["trials"].append({"number": -1, "role": "default_baseline", "params": {},
                                         "seed": seed, "status": "failed", "metrics": {},
                                         "objective": None, "duration_seconds": time.perf_counter() - started,
                                         "failure_reason": detail["failure_reason"]})
            atomic_text(directory / "logs/traceback.log", traceback.format_exc())
            emit({"type": "error", "message": detail["failure_reason"]})
        finally:
            detail.update(duration_seconds=time.perf_counter() - started, ended_at=utc_now())
            atomic_text(directory / "trials.jsonl", "".join(
                json.dumps(item, ensure_ascii=False, allow_nan=False) + "\n" for item in detail["trials"]))
            write_json(directory / "metrics.json", detail["metrics"])
            persist()
            emit({"type": "finished", "status": detail["status"], "message": detail.get("failure_reason", "")})
        return detail

    @staticmethod
    def _curves(directory, detail):
        curve = directory / "artifacts/convergence.png"
        records = [item for item in detail["trials"] if item["number"] >= 0] or detail["trials"]
        save_convergence(curve, records)
        detail["artifacts"]["convergence"] = str(curve)
        learning = detail["metrics"].get("learning_curve", [])
        write_json(directory / "artifacts/learning_curve.json", learning)
        detail["artifacts"]["learning_curve_data"] = str(directory / "artifacts/learning_curve.json")
        if learning:
            import matplotlib
            matplotlib.use("Agg")
            from matplotlib.figure import Figure
            figure = Figure(figsize=(7, 4), tight_layout=True)
            axis = figure.subplots()
            for field in ("train_loss", "validation_loss"):
                points = [(item["iteration"], item[field]) for item in learning if item.get(field) is not None]
                if points:
                    axis.plot(*zip(*points), label=field)
            axis.set(xlabel="Boosting iteration", ylabel="Classification log loss")
            axis.legend()
            target = directory / "artifacts/learning_curve.png"
            figure.savefig(target, dpi=140)
            detail["artifacts"]["learning_curve"] = str(target)

    def get_run(self, run_id):
        if not re.fullmatch(r"IND-[a-f0-9]{16}", run_id):
            raise ValueError("invalid industrial run id")
        value = json.loads((self.runs / run_id / "run.json").read_text(encoding="utf-8"))
        log = self.runs / run_id / "logs/experiment.log"
        if value.get("status") == "running" and value.get("pid"):
            from rac_hpo.infrastructure.loop_repository import process_alive
            if not process_alive(value["pid"]):
                value.update(status="failed", interrupted=True, timing_incomplete=True, ended_at=utc_now(),
                             failure_reason="Training worker exited before completion; no automatic retry")
                log.parent.mkdir(parents=True, exist_ok=True)
                value.setdefault("artifacts", {})["log"] = str(log)
                with log.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps({"timestamp": utc_now(), "run_id": run_id, "type": "interrupted",
                                              "message": value["failure_reason"]}) + "\n")
                write_json(self.runs / run_id / "run.json", value)
        value["log_text"] = log.read_text(encoding="utf-8") if log.is_file() else ""
        return value

    def list_runs(self):
        if not self.runs.exists():
            return []
        results = []
        for target in self.runs.glob("IND-*/run.json"):
            results.append(self.get_run(target.parent.name))
        return sorted(results, key=lambda item: item["created_at"], reverse=True)

    def compare(self, run_ids):
        runs = [self.get_run(run_id) for run_id in run_ids]
        signatures = {json.dumps({"task": run["task_type"], "dataset": run["dataset_key"],
                                  "source": run.get("data_sha256"),
                                  "split": run["manifest"].get("split_fingerprint"),
                                  "evaluation": run["config"]["evaluation_partition"]}, sort_keys=True)
                      for run in runs}
        descriptive = (len(runs) >= 2 and len(signatures) == 1
                       and all(run["status"] == "completed" for run in runs)
                       and all(run["manifest"].get("split_fingerprint") for run in runs))
        controls = {json.dumps({"model": run["model_name"], "seed": run["config"]["seed"],
                               "max_trials": run["config"]["max_trials"],
                               "timeout_seconds": run["config"]["timeout_seconds"],
                               "resources": run["config"]["model_config"],
                               "objective": run.get("objective_definition"),
                               "baseline_errors": run.get("baseline_errors"),
                               "weights": run.get("weights"),
                               "search_space": run.get("search_space")}, sort_keys=True)
                    for run in runs}
        comparable = descriptive and len(controls) == 1
        return {"comparable": comparable, "descriptive_comparable": descriptive,
                "fair_hpo_comparable": comparable and all(run["algorithm"] != "baseline" for run in runs),
                "runs": runs,
                "reason": "Identical source, sealed split, model, seed, objective, budget and resources" if comparable else
                          "Only descriptive metrics may be shown when source/split match; fair ranking requires identical controls"}
