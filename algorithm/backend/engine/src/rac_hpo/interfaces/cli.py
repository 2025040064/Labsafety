import argparse
import json
import sys
from pathlib import Path


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Industrial LightGBM training and reproducible HPO")
    result.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[3])
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("gui", help="Open the desktop application")
    fetch = commands.add_parser("fetch", help="Download a supported author-published dataset")
    fetch.add_argument("--dataset", choices=["steel_energy", "cnc_milling_2025"], default="steel_energy")
    prepare = commands.add_parser("import", help="Validate and persist a fixed dataset manifest")
    prepare.add_argument("--data")
    prepare.add_argument("--dataset", choices=["steel_energy", "cnc_milling_2025"])
    for name in ("train", "optimize"):
        command = commands.add_parser(name)
        command.add_argument("--data")
        command.add_argument("--dataset", choices=["steel_energy", "cnc_milling_2025"])
        command.add_argument("--seed", type=int, default=42)
        command.add_argument("--estimators", type=int, default=300)
        if name == "optimize":
            command.add_argument("--algorithm", choices=["random", "grid", "tpe", "tpe_robust", "rac_hpo", "successive_halving", "hyperband"], default="tpe")
            command.add_argument("--max-trials", type=int, default=12)
            command.add_argument("--timeout", type=float, default=120)
    commands.add_parser("list")
    for name in ("show", "export", "finalize"):
        command = commands.add_parser(name)
        command.add_argument("experiment_id")
        if name == "export":
            command.add_argument("--destination", type=Path)
    industrial = commands.add_parser("industrial", help="Audited industrial classification and HPO")
    industrial_actions = industrial.add_subparsers(dest="industrial_action", required=True)
    from rac_hpo.application.industrial_service import DATASET_KEYS, MODEL_NAMES, ALGORITHMS
    for name in ("fetch", "audit", "train", "optimize"):
        action = industrial_actions.add_parser(name)
        action.add_argument("--dataset", choices=DATASET_KEYS, required=True)
        if name != "fetch":
            action.add_argument("--data", type=Path)
        if name in {"train", "optimize"}:
            action.add_argument("--model", choices=MODEL_NAMES, default="lightgbm")
            action.add_argument("--seed", type=int, default=42)
            action.add_argument("--estimators", type=int, default=120)
            action.add_argument("--threads", type=int, default=2)
            action.add_argument("--decision-mode", choices=["native", "time_oof"], default="native")
        if name == "optimize":
            action.add_argument("--algorithm", choices=[value for value in ALGORITHMS if value != "baseline"], default="tpe")
            action.add_argument("--max-trials", type=int, default=24)
            action.add_argument("--timeout", type=float, default=300)
    industrial_actions.add_parser("list")
    show = industrial_actions.add_parser("show")
    show.add_argument("run_id")
    compare = industrial_actions.add_parser("compare")
    compare.add_argument("run_ids", nargs="+", help="Compare only identical source and sealed split")
    loop = commands.add_parser('loop', help='Controlled offline CNC optimization lifecycle')
    actions = loop.add_subparsers(dest='loop_action', required=True)
    create = actions.add_parser('create', help='Freeze protocol and explicitly authorize one training policy')
    create.add_argument('--data', type=Path)
    create.add_argument('--authorize-training', action='store_true')
    create.add_argument('--policy', type=Path, help='JSON policy, with strict supported keys')
    actions.add_parser('list')
    for name in ('run', 'show', 'export', 'promote', 'rollback', 'decline'):
        action = actions.add_parser(name)
        action.add_argument('session_id')
        if name == 'run':
            action.add_argument('--manual-demo', action='store_true', help='Allow explicit demonstration HPO if no natural trigger')
        if name == 'export':
            action.add_argument('--destination', type=Path)
        if name in ('promote', 'rollback', 'decline'):
            action.add_argument('--expected-revision', type=int, required=True)
            action.add_argument('--actor', default='user')
    return result


def main(argv=None) -> int:
    # Keep redirected JSON and diagnostics portable across Windows locale settings.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    arguments = parser().parse_args(argv)
    try:
        if arguments.command == "industrial":
            value = run_industrial_command(arguments)
        elif arguments.command == 'loop':
            value = run_loop_command(arguments)
        elif arguments.command == "gui":
            from rac_hpo.interfaces.gui.main_window import launch_gui
            return launch_gui(arguments.root)
        elif arguments.command == "fetch":
            from rac_hpo.data.registry import download
            value = {"path": str(download(arguments.dataset, arguments.root))}
        else:
            from rac_hpo.application.experiment_service import ApplicationService
            service = ApplicationService(arguments.root)
            if arguments.command == "import":
                value = service.prepare_dataset(arguments.data, arguments.dataset)
                manifest = value.pop("manifest")
                value["source_metadata"] = {key: item for key, item in manifest.items()
                                            if key in {"source_url", "license", "citation", "dataset_version", "split_strategy", "provenance_status"}}
            elif arguments.command in {"train", "optimize"}:
                overrides = {"experiment": {"seed": arguments.seed},
                             "model": {"n_estimators": arguments.estimators}}
                if arguments.dataset:
                    from rac_hpo.data.registry import DEFAULT_PATHS
                    overrides["data"] = {"dataset_id": arguments.dataset, "path": DEFAULT_PATHS[arguments.dataset]}
                algorithm = "baseline" if arguments.command == "train" else arguments.algorithm
                if arguments.command == "optimize":
                    overrides["optimization"] = {"max_trials": arguments.max_trials,
                                                 "timeout_seconds": arguments.timeout}

                def progress(event):
                    event = {key: item for key, item in event.items() if key != "trial"}
                    print(json.dumps(event, ensure_ascii=False), file=sys.stderr, flush=True)

                value = service.run_experiment(algorithm, arguments.data, overrides, progress)
            elif arguments.command == "list":
                value = [{key: item.get(key) for key in (
                    "id", "algorithm", "status", "best_value", "duration_seconds", "created_at"
                )} for item in service.list_experiments()]
            elif arguments.command == "show":
                value = service.get_experiment(arguments.experiment_id)
            elif arguments.command == "export":
                value = {"path": service.export_results(arguments.experiment_id, arguments.destination)}
            else:
                value = service.finalize_experiment(arguments.experiment_id)
        print(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False))
        if isinstance(value, dict) and value.get('state') in {'FAILED', 'INTERRUPTED', 'DATA_REVIEW', 'BUDGET_EXHAUSTED', 'CANCELLED'}:
            return 130 if value['state'] == 'CANCELLED' else 1
        if isinstance(value, dict) and value.get("status") in {"failed", "cancelled"}:
            return 130 if value["status"] == "cancelled" else 1
        return 0
    except (ValueError, OSError, RuntimeError) as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return 2


def run_industrial_command(arguments):
    from rac_hpo.application.industrial_service import IndustrialService
    service = IndustrialService(arguments.root)
    action = arguments.industrial_action
    if action == "fetch":
        return service.fetch(arguments.dataset)
    if action == "audit":
        return service.audit(arguments.dataset, arguments.data)
    if action == "list":
        return [{key: run.get(key) for key in ("id", "dataset_key", "model_name", "algorithm", "status", "best_value")}
                for run in service.list_runs()]
    if action == "show":
        return service.get_run(arguments.run_id)
    if action == "compare":
        return service.compare(arguments.run_ids)
    def progress(event):
        print(json.dumps({key: value for key, value in event.items() if key != "trial"},
                         ensure_ascii=False, allow_nan=False), file=sys.stderr, flush=True)
    model_config = {"n_estimators": arguments.estimators, "n_jobs": arguments.threads}
    if arguments.decision_mode != "native":
        model_config["decision_mode"] = arguments.decision_mode
    return service.run(arguments.dataset, model_name=arguments.model,
                       algorithm="baseline" if action == "train" else arguments.algorithm,
                       data_path=arguments.data, seed=arguments.seed,
                       max_trials=getattr(arguments, "max_trials", 24),
                       timeout_seconds=getattr(arguments, "timeout", 300),
                       model_config=model_config,
                       progress_callback=progress)


def run_loop_command(arguments):
    from rac_hpo.application.loop_service import LoopService
    service = LoopService(arguments.root)
    action = arguments.loop_action
    if action == 'create':
        policy = json.loads(arguments.policy.read_text(encoding='utf-8')) if arguments.policy else None
        return service.create_session(arguments.data, arguments.authorize_training, policy)
    if action == 'list':
        return service.list_sessions()
    if action == 'show':
        return service.get_session(arguments.session_id)
    if action == 'export':
        return {'path': service.export_session(arguments.session_id, arguments.destination)}
    if action in ('promote', 'rollback', 'decline'):
        method = {'promote': service.promote, 'rollback': service.rollback, 'decline': service.reject_release}[action]
        return method(arguments.session_id, arguments.expected_revision, arguments.actor)
    def progress(event):
        print(json.dumps(event, ensure_ascii=False, allow_nan=False), file=sys.stderr, flush=True)
    return service.run_session(arguments.session_id, arguments.manual_demo, progress_callback=progress)


if __name__ == "__main__":
    raise SystemExit(main())
