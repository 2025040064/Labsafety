"""Shared RAC-HPO workspace HTTP API."""
from contextlib import closing
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import multiprocessing as mp
import os
from pathlib import Path
from queue import Empty
import re
import secrets
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
from urllib.parse import urlencode, urlsplit, parse_qs
from urllib.request import urlopen
from zipfile import ZipFile, ZIP_DEFLATED

sys.path.insert(0, str(Path(__file__).resolve().parent / 'engine/src'))


def _load_local_env():
    """Load the gitignored root .env for local secrets before any worker spawns."""
    path = Path(__file__).resolve().parents[1] / '.env'
    if not path.is_file():
        return
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_local_env()

from actions import ACTIONS, ADMIN_ACTIONS, identifier, services, upload_path, worker
from catalog import datasets, inspect_csv, preview
from views import (charts, compare_rows, event_rows, filter_rows, integer, optional_float,
                   paginate, run_summary, trial_rows)


def clean(value):
    if isinstance(value, dict):
        return {key: clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, Path):
        return str(value)
    return value


def encode(value):
    return json.dumps(clean(value), ensure_ascii=False, allow_nan=False)


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


class Workspace:
    def __init__(self, root, mode):
        self.root = root
        self.mode = mode
        self.db_path = root / "lab.sqlite3"
        self.guard = threading.Lock()
        self.active = None          # local tabular/industrial job
        self.llm_active = None      # PyTRIO job, runs in its own slot
        self.ctx = mp.get_context("spawn")
        with closing(self.db()) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, actor TEXT, action TEXT,
                    status TEXT, progress TEXT, result TEXT, error TEXT, created REAL);
                CREATE TABLE IF NOT EXISTS job_events (seq INTEGER PRIMARY KEY, id TEXT, payload TEXT);
                CREATE TABLE IF NOT EXISTS members (id TEXT PRIMARY KEY, role TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS tokens (hash TEXT PRIMARY KEY, member TEXT, expires REAL);
            """)
            db.execute("UPDATE jobs SET status='interrupted' WHERE status IN ('running','cancelling')")
            owner = os.getenv("LAB_ADMIN_OPENID")
            if owner:
                db.execute("INSERT INTO members VALUES (?, 'admin') ON CONFLICT(id) DO UPDATE SET role='admin'", (owner,))

    def db(self):
        db = sqlite3.connect(self.db_path, timeout=15)
        db.row_factory = sqlite3.Row
        return db

    def authenticate(self, header):
        scheme, _, token = header.partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise PermissionError("Authentication required")
        with closing(self.db()) as db:
            row = db.execute("""SELECT members.id, role FROM tokens JOIN members
                ON members.id=tokens.member WHERE hash=? AND expires>?""",
                (token_hash(token), time.time())).fetchone()
        if not row:
            raise PermissionError("Session expired or membership required")
        return tuple(row)

    def login(self, code):
        if self.mode != "cloud":
            raise PermissionError("Use the local development token")
        if not isinstance(code, str) or not 1 <= len(code) <= 256:
            raise ValueError("Invalid login code")
        query = urlencode({"appid": os.environ["LAB_WECHAT_APPID"], "secret": os.environ["LAB_WECHAT_SECRET"],
                           "js_code": code, "grant_type": "authorization_code"})
        try:
            with urlopen("https://api.weixin.qq.com/sns/jscode2session?" + query, timeout=8) as response:
                result = json.load(response)
        except (OSError, ValueError):
            raise PermissionError("WeChat authentication unavailable") from None
        member = result.get("openid")
        if not member or result.get("errcode"):
            raise PermissionError("WeChat authentication failed")
        with closing(self.db()) as db, db:
            role = db.execute("SELECT role FROM members WHERE id=?", (member,)).fetchone()
            if not role:
                return {"pending": True, "member_id": member}
            token = secrets.token_urlsafe(32)
            db.execute("DELETE FROM tokens WHERE expires<=?", (time.time(),))
            db.execute("INSERT INTO tokens VALUES (?,?,?)", (token_hash(token), member, time.time() + 28800))
        return {"token": token, "role": role[0]}

    def start(self, action, params, actor):
        with self.guard, closing(self.db()) as db:
            is_llm = action == "llm_train"
            slot = self.llm_active if is_llm else self.active
            if slot:
                raise RuntimeError("A PyTRIO task is already running" if is_llm
                                   else "A workspace task is already running")
            job = "JOB-" + secrets.token_hex(10)
            events, stop = self.ctx.Queue(), self.ctx.Event()
            process = self.ctx.Process(target=worker,
                args=(str(self.root), action, params, events, stop, actor, os.getpid()), daemon=True)
            with db:
                db.execute("INSERT INTO jobs VALUES (?,?,?,'running',NULL,NULL,NULL,?)",
                           (job, actor, action, time.time()))
            try:
                process.start()
            except Exception:
                with db:
                    db.execute("UPDATE jobs SET status='failed',error='Worker launch failed' WHERE id=?", (job,))
                raise
            if is_llm:
                self.llm_active = (job, stop)
            else:
                self.active = (job, stop)
            threading.Thread(target=self.monitor,
                             args=(job, process, events, is_llm), daemon=True).start()
        return {"id": job, "status": "running"}

    def monitor(self, job, process, events, is_llm=False):
        status, result, error = "interrupted", None, None
        try:
            with closing(self.db()) as db:
                while True:
                    try:
                        kind, payload = events.get(timeout=.3)
                    except Empty:
                        if not process.is_alive():
                            break
                        continue
                    if kind == "progress":
                        if isinstance(payload.get("trial"), dict):
                            trial = payload["trial"]
                            fields = ("number", "role", "status", "params", "objective",
                                      "duration_seconds", "failure_reason", "selection")
                            payload = {**payload, "trial": {key: trial[key] for key in fields if key in trial}}
                        with db:
                            db.execute("UPDATE jobs SET progress=? WHERE id=?", (encode(payload), job))
                            db.execute("INSERT INTO job_events(id,payload) VALUES (?,?)", (job, encode(payload)))
                    elif kind == "result":
                        result = encode(payload)
                        reported = str(payload.get("status") or payload.get("state") or "").lower() if isinstance(payload, dict) else ""
                        status = reported if reported in {"failed", "cancelled", "interrupted", "data_review", "budget_exhausted"} else "completed"
                        if status == "failed":
                            error = payload.get("failure_reason") or payload.get("error")
                        break
                    else:
                        status, error = "failed", str(payload)
                        break
        finally:
            process.join()
            events.close()
            with self.guard, closing(self.db()) as db, db:
                db.execute("UPDATE jobs SET status=?,result=?,error=? WHERE id=?", (status, result, error, job))
                if is_llm:
                    self.llm_active = None
                elif self.active and self.active[0] == job:
                    self.active = None

    def cancel(self, job, actor, role):
        with self.guard, closing(self.db()) as db, db:
            row = db.execute("SELECT actor FROM jobs WHERE id=?", (job,)).fetchone()
            if not row or (row[0] != actor and role != "admin"):
                raise PermissionError("Only the task creator or administrator can cancel")
            slot = self.llm_active if self.llm_active and self.llm_active[0] == job else self.active
            if not slot or slot[0] != job:
                raise RuntimeError("Task is no longer active")
            slot[1].set()
            db.execute("UPDATE jobs SET status='cancelling' WHERE id=?", (job,))
        return {"status": "cancelling"}


class Handler(BaseHTTPRequestHandler):
    server_version = "LabRAC"
    protocol_version = "HTTP/1.0"

    def log_message(self, _format, *args):
        pass

    def send_json(self, status, data):
        body = encode(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_text(self, body, filename, content_type="text/plain"):
        payload = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", content_type + "; charset=utf-8")
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def all_events(self, job):
        with closing(self.server.workspace.db()) as db:
            return [json.loads(item[0]) for item in db.execute(
                "SELECT payload FROM job_events WHERE id=? ORDER BY seq DESC", (job,)).fetchall()]

    def body(self, limit=32768):
        length = int(self.headers.get("Content-Length", "0"))
        if not 0 < length <= limit:
            raise ValueError("Invalid request size")
        return length

    def json_body(self):
        value = json.loads(self.rfile.read(self.body()))
        if not isinstance(value, dict):
            raise ValueError("Expected a JSON object")
        return value

    def do_GET(self):
        self.dispatch()

    def do_POST(self):
        self.dispatch()

    def dispatch(self):
        try:
            value = self.route()
            if value is not None:
                self.send_json(200, value)
        except PermissionError as error:
            self.send_json(403, {"error": str(error)})
        except (ValueError, KeyError, FileNotFoundError) as error:
            self.send_json(400, {"error": str(error)})
        except RuntimeError as error:
            self.send_json(409, {"error": str(error)})
        except (ConnectionError, TimeoutError):
            return
        except Exception as error:
            print(type(error).__name__, flush=True)
            self.send_json(500, {"error": "Internal workspace error"})

    def route(self):
        ws = self.server.workspace
        url = urlsplit(self.path)
        path = url.path
        query = parse_qs(url.query)
        post = self.command == "POST"
        if path == "/health" and not post:
            return {"status": "ok"}
        if path == "/api/login" and post:
            return ws.login(self.json_body().get("code"))
        if ws.mode == "local":
            host = self.headers.get("Host", "")
            expected = f"127.0.0.1:{self.server.server_port}"
            if (self.client_address[0] != "127.0.0.1" or host != expected
                    or self.headers.get("X-Lab-Local") != "1"):
                raise PermissionError("Local workspace requires a loopback client")
            actor, role = "local", "admin"
        else:
            actor, role = ws.authenticate(self.headers.get("Authorization", ""))
        if path == "/api/me" and not post:
            return {"role": role}
        if not post and ws.mode == "local" and path == "/api/datasets":
            return datasets(ws.root)
        if not post and ws.mode == "local" and path.startswith("/api/datasets/"):
            parts = path.split("/")
            if len(parts) == 5 and parts[4] == "preview":
                return preview(ws.root, parts[3], integer(query, "limit", 10, 1, 20))
            if len(parts) == 4:
                matches = [item for item in datasets(ws.root) if item["id"] == parts[3]]
                if matches:
                    return matches[0]
            raise ValueError("Unknown dataset")
        if not post and ws.mode == "local" and path == "/api/dashboard":
            with closing(ws.db()) as db:
                counts = dict(db.execute("SELECT status,COUNT(*) FROM jobs GROUP BY status").fetchall())
                recent = [dict(row) for row in db.execute(
                    "SELECT id,action,status,created FROM jobs ORDER BY created DESC LIMIT 5")]
            entries = datasets(ws.root)
            return {"datasets_present": sum(1 for item in entries if item["available"]),
                    "completed_tasks": counts.get("completed", 0),
                    "running_tasks": counts.get("running", 0) + counts.get("cancelling", 0),
                    "recent_tasks": recent}
        if path == "/api/members" and ws.mode == "cloud":
            if role != "admin":
                raise PermissionError("Administrator required")
            with closing(ws.db()) as db, db:
                if not post:
                    return [dict(row) for row in db.execute("SELECT id,role FROM members")]
                params = self.json_body()
                member = params.get("id", "")
                if not isinstance(member, str) or not 8 <= len(member) <= 128 or not all(c.isascii() and (c.isalnum() or c in "-_") for c in member):
                    raise ValueError("Invalid member identifier")
                if member == os.getenv("LAB_ADMIN_OPENID"):
                    raise PermissionError("Deployment administrator cannot be changed here")
                if params.get("enabled") is True:
                    db.execute("INSERT OR IGNORE INTO members VALUES (?, 'member')", (member,))
                else:
                    db.execute("DELETE FROM tokens WHERE member=?", (member,))
                    db.execute("DELETE FROM members WHERE id=?", (member,))
                return {"updated": True}
        if path == "/api/uploads" and post:
            extension = query.get("ext", [""])[0]
            if extension not in {"csv", "zip", "rar", "mat", "json", "txt"}:
                raise ValueError("Unsupported file extension")
            raw_name = query.get("name", [""])[0]
            safe_name = Path(raw_name).name
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,119}", safe_name):
                safe_name = "input." + extension
            existing = query.get("upload", [None])[0]
            if existing:
                upload_id = identifier(existing, "UP")
                directory = ws.root / "uploads" / upload_id
                if not directory.is_dir():
                    raise ValueError("Upload not found")
            else:
                upload_id = "UP-" + secrets.token_hex(10)
                directory = ws.root / "uploads" / upload_id
                directory.mkdir(parents=True)
            remaining = self.body(64 * 1024 * 1024)
            temporary = directory / (secrets.token_hex(6) + ".part")
            try:
                with temporary.open("wb") as output:
                    while remaining:
                        block = self.rfile.read(min(remaining, 65536))
                        if not block:
                            raise ValueError("Upload incomplete")
                        output.write(block)
                        remaining -= len(block)
                temporary.rename(directory / safe_name)
            finally:
                temporary.unlink(missing_ok=True)
            count = len([item for item in directory.iterdir() if item.is_file() and not item.name.endswith(".part")])
            return {"id": upload_id, "name": safe_name, "count": count}
        if not post and ws.mode == 'local' and path.startswith('/api/uploads/'):
            parts = path.split('/')
            if len(parts) == 5 and parts[4] == 'inspection':
                return inspect_csv(upload_path(ws.root, parts[3]))
            raise ValueError('Unknown upload endpoint')
        if path == "/api/llm/status" and post:
            from rac_hpo.application.llm_service import check_availability
            return check_availability(self.json_body().get("apiKey"))
        if path == "/api/jobs":
            if post:
                params = self.json_body()
                action = params.pop("action", None)
                if action not in ACTIONS:
                    raise ValueError("Unknown action")
                if action == "llm_status":
                    from rac_hpo.application.llm_service import check_availability
                    return check_availability(params.get("apiKey"))
                if action in ADMIN_ACTIONS and (role != "admin" or params.get("confirmed") is not True):
                    raise PermissionError("Administrator and explicit confirmation required")
                return ws.start(action, params, actor)
            with closing(ws.db()) as db:
                return [dict(row) for row in db.execute(
                    "SELECT id,action,status,created,error FROM jobs ORDER BY created DESC LIMIT 100")]
        if path.startswith("/api/jobs/"):
            parts = path.split("/")
            job = identifier(parts[3], "JOB")
            if post and path == f"/api/jobs/{job}/cancel":
                return ws.cancel(job, actor, role)
            if not post and len(parts) == 4:
                with closing(ws.db()) as db:
                    row = db.execute("SELECT * FROM jobs WHERE id=?", (job,)).fetchone()
                    if not row:
                        raise ValueError("Unknown task")
                    result = dict(row)
                    result.pop("actor")
                    for field in ("progress", "result"):
                        result[field] = json.loads(result[field]) if result[field] else None
                    result["events"] = self.all_events(job)[:50]
                    return result
            if not post and len(parts) == 5 and parts[4] in {"events", "export"}:
                events = self.all_events(job)
                if parts[4] == "events":
                    return event_rows(events, query.get("q", [""])[0],
                                      integer(query, "page", 1, 1, 100000),
                                      integer(query, "size", 50, 1, 100))
                payload = "".join(json.dumps(event, ensure_ascii=False, default=str) + "\n"
                                  for event in events)
                self.send_text(payload, job + "-events.jsonl", "application/x-ndjson")
                return None
        if post:
            raise ValueError("Unknown endpoint")
        experiment, industrial, loop = services(ws.root)
        if path == "/api/runs":
            from rac_hpo.application.llm_service import list_runs as list_llm_runs
            keys = ("id", "dataset_id", "dataset_key", "model_name", "algorithm", "status", "best_value", "target_unit", "created_at")
            rows = (experiment.repository.list_experiments() + industrial.list_runs()
                    + list_llm_runs(ws.root))
            rows.sort(key=lambda item: item.get("created_at") or "", reverse=True)
            return [{key: row.get(key) for key in keys} for row in rows]
        if path == "/api/loops":
            return [{key: session.get(key) for key in ("id", "state", "revision", "data_mode")}
                    for session in loop.list_sessions()]
        if path == "/api/compare":
            ids = query.get("id", [])
            if not 2 <= len(set(ids)) == len(ids) <= 8:
                raise ValueError("Select 2–8 distinct runs")
            if any(value.startswith("LLM-") for value in ids):
                raise ValueError("LLM 微调结果不与表格实验做公平比较")
            if all(value.startswith("IND-") for value in ids):
                verdict = industrial.compare([identifier(value, "IND") for value in ids])
                verdict["runs"] = compare_rows(verdict["runs"])
                verdict["ranking"] = False
                return verdict
            details = [experiment.get_experiment(identifier(value, "EXP")) for value in ids]
            return {"ranking": False, "runs": compare_rows(details)}
        if path.startswith("/api/runs/") or path.startswith("/api/loops/"):
            parts = path.split("/")
            run_id = parts[3]
            is_loop = run_id.startswith("LOOP-")
            is_llm = run_id.startswith("LLM-")
            if is_loop:
                detail = loop.get_session(identifier(run_id, "LOOP"))
                export = lambda: loop.export_session(run_id)
            elif is_llm:
                result_path = (ws.root / "workspace" / "llm_runs" /
                               identifier(run_id, "LLM") / "result.json")
                if not result_path.is_file():
                    raise FileNotFoundError("Unknown LLM run")
                detail = json.loads(result_path.read_text(encoding="utf-8"))
                export = lambda: result_path.parent
            elif run_id.startswith("IND-"):
                detail = industrial.get_run(identifier(run_id, "IND"))
                export = lambda: ws.root / "workspace/industrial_runs" / run_id
            else:
                detail = experiment.get_experiment(identifier(run_id, "EXP"))
                export = lambda: experiment.export_results(run_id)
            if len(parts) == 4:
                return detail if (is_loop or is_llm) else run_summary(detail)
            if len(parts) == 5 and parts[4] == "export":
                self.export_zip(Path(export()), run_id)
                return None
            if len(parts) == 5 and parts[4] in {"trials", "charts"} and not is_loop:
                rows = filter_rows(trial_rows(detail),
                                   query.get("q", [""])[0], query.get("status", [""])[0],
                                   query.get("param", [""])[0],
                                   optional_float(query, "low"), optional_float(query, "high"),
                                   query.get("sort", ["number"])[0], query.get("order", ["asc"])[0])
                if parts[4] == "charts":
                    return charts(detail, [row["number"] for row in rows])
                return paginate(rows, integer(query, "page", 1, 1, 100000),
                                integer(query, "size", 20, 1, 100))
        raise ValueError("Unknown endpoint")

    def export_zip(self, directory, run_id):
        with tempfile.TemporaryFile(dir=self.server.workspace.root) as file:
            with ZipFile(file, "w", ZIP_DEFLATED) as archive:
                for path in directory.rglob("*"):
                    if path.is_file() and not path.is_symlink():
                        archive.write(path, path.relative_to(directory))
            length = file.tell()
            file.seek(0)
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Disposition", f'attachment; filename="{run_id}.zip"')
            self.send_header("Content-Length", str(length))
            self.end_headers()
            shutil.copyfileobj(file, self.wfile)


def main():
    mode = os.getenv("LAB_MODE", "local")
    root = Path(os.getenv("LAB_ROOT", "rac-data")).resolve()
    if mode == "cloud":
        required = ("LAB_WECHAT_APPID", "LAB_WECHAT_SECRET", "LAB_ADMIN_OPENID")
        if not all(os.getenv(key) for key in required) or not os.path.ismount(root):
            raise SystemExit("Cloud requires a mounted LAB_ROOT and WeChat configuration")
    elif mode != "local":
        raise SystemExit("Invalid LAB_MODE")
    if not (root / "configs/default.yaml").is_file():
        raise SystemExit("Workspace has no configs/default.yaml")
    os.chdir(root)
    os.environ.setdefault("MPLBACKEND", "Agg")
    with (root / "lab.lock").open("a+b") as lock:
        if os.name == "nt":
            import msvcrt
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        host = "0.0.0.0" if mode == "cloud" else "127.0.0.1"
        port = int(os.getenv("PORT", "8765"))
        with ThreadingHTTPServer((host, port), Handler) as server:
            server.workspace = Workspace(root, mode)
            print(f"RAC-HPO listening on {host}:{port}", flush=True)
            server.serve_forever()


if __name__ == "__main__":
    main()
