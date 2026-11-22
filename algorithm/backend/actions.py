"""Translate mini-program actions into real RAC-HPO application calls."""
import os
from pathlib import Path
import re

ADMIN_ACTIONS = {"finalize", "promote", "rollback", "decline"}
ACTIONS = {"prepare", "fetch", "train", "optimize", "industrial_audit", "industrial_fetch",
           "industrial_run", "loop_create", "loop_run", "llm_status", "llm_train"} | ADMIN_ACTIONS

# Ranges mirror the desktop controls and config schema, not the mini-program form.
N_JOBS = 4


def identifier(value, prefix):
    if not isinstance(value, str) or not re.fullmatch(prefix + r"-[A-Za-z0-9-]{1,80}", value):
        raise ValueError("Invalid identifier")
    return value


def number(params, key, default, minimum, maximum):
    value = params.get(key, default)
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"Invalid {key}: expected {minimum}–{maximum}")
    return value


def seconds(params, key, default, minimum, maximum):
    value = params.get(key, default)
    if type(value) not in (int, float) or isinstance(value, bool) or not minimum <= value <= maximum:
        raise ValueError(f"Invalid {key}: expected {minimum}–{maximum}")
    return float(value)


def services(root):
    from rac_hpo.application.experiment_service import ApplicationService
    from rac_hpo.application.industrial_service import IndustrialService
    from rac_hpo.application.loop_service import LoopService
    return ApplicationService(root), IndustrialService(root), LoopService(root)


def upload_path(root, upload_id):
    """One file for tabular data, a directory for multi-file industrial sources."""
    directory = Path(root) / "uploads" / identifier(upload_id, "UP")
    files = sorted(item for item in directory.iterdir() if item.is_file() and not item.name.endswith(".part"))
    if not files:
        raise ValueError("Upload not found")
    return files[0] if len(files) == 1 else directory


def run_action(root, action, params, progress, cancel, actor):
    if action == "llm_status":
        from rac_hpo.application.llm_service import check_availability
        return check_availability(params.get("apiKey"))
    if action == "llm_train":
        from rac_hpo.application.llm_service import DEFAULT_BASE_MODEL, run_sft
        examples = params.get("examples")
        return run_sft(root, api_key=params.get("apiKey"),
                       base_model=params.get("baseModel") or DEFAULT_BASE_MODEL,
                       examples=examples,
                       iterations=number(params, "iterations", 5, 1, 100),
                       learning_rate=seconds(params, "learningRate", 1e-4, 1e-6, 1e-1),
                       rank=number(params, "rank", 8, 4, 64),
                       name=params.get("name") or "lab-sft",
                       template=params.get("template") or "Question: {input}\nAnswer:",
                       evaluate=params.get("evaluate") is not False,
                       max_tokens=number(params, "maxTokens", 40, 1, 512),
                       progress=progress, cancel_check=cancel)

    from rac_hpo.data.registry import DEFAULT_PATHS, download
    from rac_hpo.application.industrial_service import DATASET_KEYS
    experiment, industrial, loop = services(root)
    dataset = params.get("dataset", "steel_energy")
    path = upload_path(root, params["upload"]) if params.get("upload") else None
    if action in {"prepare", "fetch", "train", "optimize"} and dataset not in DEFAULT_PATHS:
        raise ValueError("Unknown regression dataset")
    if action.startswith("industrial_") and dataset not in DATASET_KEYS:
        raise ValueError("Unknown industrial dataset")
    if action == "prepare":
        return experiment.prepare_dataset(path, dataset)
    if action == "fetch":
        return {"path": str(download(dataset, root))}
    if action == "industrial_audit":
        if dataset == 'user_csv':
            return industrial.audit(dataset, path, params.get('mapping'))
        return industrial.audit(dataset, path)
    if action == "industrial_fetch":
        return industrial.fetch(dataset)
    if action in {"train", "optimize", "industrial_run"}:
        is_industrial = action == "industrial_run"
        seed = number(params, "seed", 42, 0, 2147483647)
        estimators = number(params, "estimators", 120 if is_industrial else 300, 10, 10000)
        trials = number(params, "trials", 24 if is_industrial else 12, 1, 10000)
        timeout = seconds(params, "timeout", 300 if is_industrial else 120, 0.1, 86400)
        algorithm = params.get("algorithm", "tpe")
        if is_industrial:
            extra = {"mapping": params.get("mapping")} if dataset == 'user_csv' else {}
            return industrial.run(dataset, params.get("model", "lightgbm"), algorithm, path,
                                  seed, trials, timeout,
                                  {"n_estimators": estimators, "n_jobs": N_JOBS,
                                   "decision_mode": params.get("decision", "native")},
                                  progress, cancel, **extra)
        return experiment.run_experiment("baseline" if action == "train" else algorithm, path,
            {"experiment": {"seed": seed}, "data": {"dataset_id": dataset, "path": DEFAULT_PATHS[dataset]},
             "model": {"n_estimators": estimators, "n_jobs": N_JOBS},
             "optimization": {"max_trials": trials, "timeout_seconds": timeout}}, progress, cancel)
    if action == "finalize":
        return experiment.finalize_experiment(identifier(params.get("id"), "EXP"))
    if action == "loop_create":
        policy = params.get("policy") or {}
        if not isinstance(policy, dict):
            raise ValueError("Invalid loop policy")
        approved = {
            "scenario": policy.get("scenario", "natural"),
            "max_trials": number(policy, "max_trials", 48, 1, 48),
            "timeout_seconds": seconds(policy, "timeout_seconds", 120, 0.1, 120),
            "training_budget_seconds": seconds(policy, "training_budget_seconds", 300, 0.1, 300),
        }
        session = loop.create_session(path, params.get("authorize") is True, approved)
        progress({"type": "loop_session_created", "session_id": session["id"]})
        if not session.get("authorized_policy_sha256"):
            return session
        return loop.run_session(session["id"], manual_demo=params.get("manual") is True,
                                progress_callback=progress, cancel_check=cancel)
    loop_id = identifier(params.get("id"), "LOOP")
    if action == "loop_run":
        return loop.run_session(loop_id, manual_demo=params.get("manual") is True,
                                progress_callback=progress, cancel_check=cancel)
    operation = {"promote": loop.promote, "rollback": loop.rollback, "decline": loop.reject_release}[action]
    return operation(loop_id, number(params, "revision", -1, 0, 2147483647), actor)


def worker(root, action, params, events, stop, actor, parent_pid):
    os.chdir(root)
    try:
        result = run_action(root, action, params, lambda item: events.put(("progress", item)),
                            lambda: stop.is_set() or os.getppid() != parent_pid, actor)
        events.put(("result", result))
    except Exception as error:
        events.put(("error", f"{type(error).__name__}: {error}"))
