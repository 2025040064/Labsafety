"""Read-only views over the existing dataset registry."""
import csv
from itertools import islice
import math
from pathlib import Path


def datasets(root):
    from rac_hpo.data.registry import DEFAULT_PATHS as regression_paths
    from rac_hpo.data.industrial import DEFAULT_PATHS as industrial_paths

    entries = []
    for kind, paths in (("regression", regression_paths), ("industrial", industrial_paths)):
        for key, relative in paths.items():
            target = Path(root) / relative
            entries.append({"id": key, "kind": kind, "available": target.exists(),
                            "preview_available": target.is_file() and target.suffix.lower() == ".csv",
                            "description": "公开合成示例数据" if key == "ai4i_2020" else "历史或公开数据；请查看审计结果确认来源"})
    return entries


def preview(root, dataset_id, limit=10):
    from rac_hpo.data.registry import DEFAULT_PATHS as regression_paths
    from rac_hpo.data.industrial import DEFAULT_PATHS as industrial_paths

    paths = {**regression_paths, **industrial_paths}
    if dataset_id not in paths:
        raise ValueError("Unknown dataset")
    if not 1 <= limit <= 20:
        raise ValueError("Preview limit must be between 1 and 20")
    path = Path(root) / paths[dataset_id]
    if not path.is_file() or path.suffix.lower() != ".csv":
        raise ValueError("CSV preview unavailable")
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle, delimiter=';' if dataset_id == 'cnc_milling_2025' else ',')
        rows = list(islice(reader, limit + 1))
    if not rows:
        raise ValueError("CSV is empty")
    return {"id": dataset_id, "columns": [cell[:200] for cell in rows[0][:32]],
            "rows": [[cell[:200] for cell in row[:32]] for row in rows[1:]], "columns_truncated": len(rows[0]) > 32,
            "note": "原始数据预览；未显示的字段可能仍参与数据审计。"}


def inspect_csv(path):
    path = Path(path)
    if not path.is_file() or path.suffix.lower() != '.csv' or path.stat().st_size > 32 * 1024 * 1024:
        raise ValueError('Choose one CSV file up to 32 MB')
    try:
        with path.open(encoding='utf-8-sig', newline='') as handle:
            reader = csv.reader(handle, strict=True)
            header = next(reader, [])
            if (not header or len(header) > 100 or any(not name.strip() for name in header)
                    or len(set(header)) != len(header)):
                raise ValueError('CSV must have 1–100 distinct nonempty column names')
            counts = [{'name': name, 'missing': 0, 'non_numeric': 0} for name in header]
            samples, total = [], 0
            for row in reader:
                total += 1
                if total > 100000 or len(row) != len(header):
                    raise ValueError('CSV exceeds 100000 rows or contains inconsistent columns')
                if len(samples) < 5:
                    samples.append([cell[:200] for cell in row[:32]])
                for cell, count in zip(row, counts):
                    if not cell.strip():
                        count['missing'] += 1
                        continue
                    try:
                        if not math.isfinite(float(cell)):
                            count['non_numeric'] += 1
                    except ValueError:
                        count['non_numeric'] += 1
            if not total:
                raise ValueError('CSV contains no data rows')
    except (csv.Error, UnicodeDecodeError) as error:
        raise ValueError('CSV must be valid UTF-8 with consistent columns') from error
    return {'columns': counts, 'rows': total, 'preview': samples,
            'note': '用户上传数据；请选择标签、分组和数值特征，并确认字段不会泄漏未来信息。'}
