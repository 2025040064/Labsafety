from pathlib import Path
import sys
import tempfile
import unittest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / 'engine/src'))
from catalog import inspect_csv
from rac_hpo.data.industrial.user_csv import prepare_user_csv


class UserCsvTest(unittest.TestCase):
    def test_inspection_and_grouped_mapping(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd() / 'rac-data') as directory:
            path = Path(directory) / 'data.csv'
            lines = ['group,label,temperature,unused']
            for label in (0, 1):
                for group in range(3):
                    for row in range(2):
                        lines.append(f'{label}-{group},{label},{group + row},text')
            path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
            inspection = inspect_csv(path)
            self.assertEqual(inspection['rows'], 12)
            self.assertEqual(inspection['columns'][3]['non_numeric'], 12)
            data = prepare_user_csv(path, {'target': 'label', 'group': 'group', 'features': ['temperature']})
            sets = [set(data.groups.iloc[data.splits[name]]) for name in ('train', 'validation', 'test')]
            self.assertTrue(all(not sets[i] & sets[j] for i, j in ((0, 1), (0, 2), (1, 2))))
            self.assertEqual(data.manifest['data_origin'], 'user_supplied')
            with self.assertRaises(ValueError):
                prepare_user_csv(path, {'target': 'label', 'group': 'group', 'features': ['label']})
            with self.assertRaises(ValueError):
                prepare_user_csv(path, {'target': 'label', 'group': 'group', 'features': ['unused']})

    def test_invalid_or_duplicate_headers_are_rejected(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd() / 'rac-data') as directory:
            path = Path(directory) / 'data.csv'
            for content in ('a,a\n1,2\n', 'a,b\n1\n', ''):
                path.write_text(content, encoding='utf-8')
                with self.assertRaises(ValueError):
                    inspect_csv(path)


if __name__ == '__main__':
    unittest.main()
