import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from contextlib import closing
from unittest.mock import patch
from queue import Queue

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server import Workspace, token_hash


class WorkspaceTest(unittest.TestCase):
    def test_trial_progress_is_available_in_persisted_events(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd() / "rac-data") as directory:
            workspace = Workspace(Path(directory), "local")
            with closing(workspace.db()) as db, db:
                db.execute("INSERT INTO jobs VALUES ('JOB-test','local','industrial_run','running',NULL,NULL,NULL,0)")
            events = Queue()
            events.put(("progress", {"type": "trial_completed", "trial": {
                "number": 2, "status": "completed", "params": {"max_depth": 4},
                "objective": 0.25, "duration_seconds": 1.5}}))
            events.put(("result", {"status": "completed"}))

            class Process:
                def join(self):
                    pass

            class Events:
                def get(self, timeout):
                    return events.get(timeout=timeout)

                def close(self):
                    pass

            workspace.monitor('JOB-test', Process(), Events())
            with closing(workspace.db()) as db:
                import json
                payload = json.loads(db.execute("SELECT payload FROM job_events WHERE id='JOB-test'").fetchone()[0])
            self.assertEqual(payload['trial']['number'], 2)
            self.assertEqual(payload['trial']['params'], {'max_depth': 4})
            self.assertEqual(payload['trial']['objective'], 0.25)

    def test_restart_and_membership_revocation(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd() / "rac-data") as directory:
            root = Path(directory)
            with patch.dict(os.environ, {"LAB_ADMIN_OPENID": "configured-admin"}):
                workspace = Workspace(root, "cloud")
                with closing(workspace.db()) as db, db:
                    db.execute("INSERT INTO members VALUES ('test-member','member')")
                    db.execute("INSERT INTO tokens VALUES (?,?,?)",
                               (token_hash("fixture"), "test-member", time.time() + 60))
                    db.execute("INSERT INTO jobs VALUES ('JOB-fixture','test-member','train','running',NULL,NULL,NULL,0)")
                self.assertEqual(workspace.authenticate("Bearer fixture"), ("test-member", "member"))
                recovered = Workspace(root, "cloud")
                with closing(recovered.db()) as db, db:
                    self.assertEqual(db.execute("SELECT status FROM jobs").fetchone()[0], "interrupted")
                    db.execute("DELETE FROM members WHERE id='test-member'")
                with self.assertRaises(PermissionError):
                    recovered.authenticate("Bearer fixture")
