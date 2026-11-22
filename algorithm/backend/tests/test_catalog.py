import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from contextlib import closing
from urllib.request import Request, urlopen
from http.server import ThreadingHTTPServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from catalog import datasets, preview
from server import Handler, Workspace


class CatalogTest(unittest.TestCase):
    def test_preview_uses_registered_file_and_bounds_rows(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd() / 'rac-data') as directory:
            root = Path(directory)
            path = root / 'datasets/raw/ai4i_2020/data.csv'
            path.parent.mkdir(parents=True)
            path.write_text('Type,Machine failure\nM,0\nL,1\n', encoding='utf-8')
            item = next(x for x in datasets(root) if x['id'] == 'ai4i_2020')
            self.assertTrue(item['available'])
            self.assertIn('合成', item['description'])
            self.assertEqual(preview(root, 'ai4i_2020', 1)['rows'], [['M', '0']])
            for key in ('../ai4i_2020', 'missing'):
                with self.assertRaises(ValueError):
                    preview(root, key)
            with self.assertRaises(ValueError):
                preview(root, 'ai4i_2020', 21)

    def test_dashboard_counts_real_tasks(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd() / 'rac-data') as directory:
            root = Path(directory)
            workspace = Workspace(root, 'local')
            with closing(workspace.db()) as db, db:
                db.execute("INSERT INTO jobs VALUES ('JOB-one','local','train','completed',NULL,NULL,NULL,0)")
                db.execute("INSERT INTO jobs VALUES ('JOB-two','local','train','running',NULL,NULL,NULL,1)")
            server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
            server.workspace = workspace
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = 'http://127.0.0.1:%d/api/dashboard' % server.server_port
                with urlopen(Request(url, headers={'X-Lab-Local': '1'}), timeout=5) as response:
                    data = json.load(response)
                self.assertEqual(data['completed_tasks'], 1)
                self.assertEqual(data['running_tasks'], 1)
                self.assertEqual(data['datasets_present'], 0)
            finally:
                server.shutdown()
                server.server_close()
                thread.join()


if __name__ == '__main__':
    unittest.main()
