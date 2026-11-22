"""Explicitly mapped numeric CSV classification with whole-group splits."""
import csv
from pathlib import Path

import numpy as np
import pandas as pd

from .common import grouped_splits, make_dataset


def prepare_user_csv(path, mapping, manifest_dir=None):
    path = Path(path)
    if not path.is_file() or path.suffix.lower() != '.csv' or path.stat().st_size > 32 * 1024 * 1024:
        raise ValueError('One CSV file up to 32 MB is required')
    if not isinstance(mapping, dict):
        raise ValueError('Select target, group and feature columns')
    target, group, features = mapping.get('target'), mapping.get('group'), mapping.get('features')
    if not isinstance(target, str) or not isinstance(group, str) or not isinstance(features, list):
        raise ValueError('Select target, group and feature columns')
    if not features or len(features) > 32 or any(not isinstance(item, str) for item in features):
        raise ValueError('Select between 1 and 32 numeric feature columns')
    selected = [target, group] + features
    if len(set(selected)) != len(selected):
        raise ValueError('Target, group and features must be distinct')
    with path.open(encoding='utf-8-sig', newline='') as handle:
        header = next(csv.reader(handle), [])
    if not header or len(header) > 100 or any(not name.strip() for name in header) or len(set(header)) != len(header):
        raise ValueError('CSV must have 1–100 distinct nonempty column names')
    if not set(selected).issubset(header):
        raise ValueError('Selected column not found in CSV')
    frame = pd.read_csv(path, nrows=100001)
    if not 1 <= len(frame) <= 100000:
        raise ValueError('CSV must contain 1–100000 records')
    if frame[[target, group]].isna().any().any():
        raise ValueError('Target and group cannot contain missing values')
    X = frame[features].apply(pd.to_numeric, errors='raise')
    y = pd.to_numeric(frame[target], errors='raise')
    if not np.isfinite(y).all() or not np.equal(y, np.floor(y)).all():
        raise ValueError('Classification target must contain finite integer class IDs')
    if not 2 <= y.nunique() <= 20:
        raise ValueError('Classification target must contain 2–20 classes')
    y = y.astype(int).rename(target)
    groups = frame[group].astype(str)
    if groups.str.strip().eq('').any():
        raise ValueError('Group IDs cannot be blank')
    splits = grouped_splits(groups, y)
    metadata = {'data_origin': 'user_supplied', 'provenance_verified': False,
                'license': 'user_supplied_unknown', 'independent_unit_kind': 'user_declared_group',
                'group_definition': group, 'regime_definition': 'overall',
                'split_strategy': 'Whole user-declared groups, stratified by class',
                'classes': {str(value): str(value) for value in sorted(y.unique())},
                'excluded_fields': [target, group],
                'limitations': ['User must verify label meaning, group identity and feature availability at prediction time']}
    return make_dataset('user_csv', path, X, y, pd.Series('overall', index=frame.index),
                        groups, splits, metadata, [path], manifest_dir)
