"""Pinned CNC offline protocol and immutable train/validation-only views."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

import numpy as np
import pandas as pd

from rac_hpo._io import atomic_text
from rac_hpo.domain.schemas import PreparedDataset

TOOLS = {'train': ['2', '6', '105', '10', '7', '11'], 'validation': ['4', '103'],
         'replay': ['5', '8'], 'gate': ['9', '101'], 'test': ['102', '3']}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False,
                                     ensure_ascii=False).encode('utf-8')).hexdigest()


def build_protocol(dataset):
    from .cnc_milling import OFFICIAL_SHA256
    if dataset.source_sha256 != OFFICIAL_SHA256:
        raise ValueError('Offline loop v1 requires the pinned author CNC dataset')
    roles = {name: np.flatnonzero(dataset.groups.isin(tools)).tolist()
             for name, tools in TOOLS.items()}
    if [len(roles[name]) for name in TOOLS] != [341, 101, 167, 233, 126]:
        raise ValueError('Pinned CNC protocol row counts changed')
    value = {'version': 'offline-tool-sequence-v1', 'source_path': dataset.source_path,
             'source_sha256': dataset.source_sha256, 'features': dataset.features,
             'target': dataset.target, 'tools': TOOLS, 'roles': roles,
             'data_mode': 'REPLAY', 'label_policy': 'release_at_tool_end',
             'limitations': ['Offline replay, not factory deployment.',
                            'Gate rows were visible to historical research.',
                            'One gate/validation tool per condition; q=0, no confidence interval.']}
    value['fingerprint'] = digest(value)
    # Gate ownership deliberately excludes path/session/policy so moving files cannot reset it.
    value['gate_fingerprint'] = digest({'source': dataset.source_sha256, 'rows': roles['gate']})
    return value


def replay_rows(dataset, tool):
    if str(tool) not in TOOLS['replay']:
        raise ValueError('Unknown replay tool')
    metadata = pd.read_csv(dataset.source_path, sep=';', header=1,
                           usecols=['TollIndex', 'NumberOfCycle', 'FileName'])
    metadata['TollIndex'] = pd.to_numeric(metadata['TollIndex'].astype(str).str.replace(',', '.'))
    metadata['NumberOfCycle'] = pd.to_numeric(metadata['NumberOfCycle'].astype(str).str.replace(',', '.'))
    metadata['source_row'] = np.arange(len(metadata))
    selected = metadata[metadata['TollIndex'] == int(tool)]
    return selected.sort_values(['NumberOfCycle', 'source_row'], kind='stable')['source_row'].tolist()


def sample_metadata(dataset, rows):
    metadata = pd.read_csv(dataset.source_path, sep=';', header=1,
                           usecols=['FileName', 'NumberOfCycle'])
    return [{'source_row': row, 'sample_id': str(metadata.iloc[row]['FileName']),
             'cycle': int(float(str(metadata.iloc[row]['NumberOfCycle']).replace(',', '.')))}
            for row in rows]


def simulate_features(frame, reference, scenario):
    result = frame.copy()
    if scenario == 'missing_sensor':
        result.iloc[:, 0] = np.nan
    elif scenario == 'feature_shift':
        for name in list(reference.columns[:30]):
            scale = max(float(reference[name].quantile(.75) - reference[name].quantile(.25)), 1e-8)
            result[name] += 3 * scale
    elif scenario != 'natural':
        raise ValueError('Unknown replay scenario')
    return result


def build_view(dataset, protocol, released_rows, scenario='natural'):
    if build_protocol(dataset) != protocol:
        raise ValueError('Protocol or source changed')
    released = set(released_rows)
    valid_released = set()
    # The single candidate must be fixed before the second tool is observed.
    first_tool = set(replay_rows(dataset, '5'))
    if released == first_tool:
        valid_released = first_tool
    elif released:
        raise ValueError('Only the complete released first replay tool may enter training')
    train = protocol['roles']['train'] + sorted(valid_released)
    validation = protocol['roles']['validation']
    selected = train + validation
    manifest = {key: deepcopy(value) for key, value in dataset.manifest.items()
                if key not in {'splits', 'split_groups', 'source_row_indices', 'split_summary'}}
    manifest.update(source_path=dataset.source_path,
                    protocol_version=protocol['version'], protocol_fingerprint=protocol['fingerprint'],
                    data_mode='REPLAY' if scenario == 'natural' else 'SIMULATED', scenario=scenario,
                    source_row_indices=selected,
                    released_rows=sorted(valid_released),
                    splits={'train': list(range(len(train))),
                            'validation': list(range(len(train), len(selected)))},
                    split_groups={'train': sorted(set(dataset.groups.iloc[train])),
                                  'validation': sorted(set(dataset.groups.iloc[validation]))},
                    split_strategy='Offline loop v1: restricted train/validation view; gate/test excluded')
    X = dataset.X.iloc[selected].reset_index(drop=True).copy()
    if valid_released:
        positions = list(range(len(protocol['roles']['train']), len(train)))
        X.iloc[positions] = simulate_features(X.iloc[positions],
                                             dataset.X.iloc[protocol['roles']['train']], scenario).to_numpy()
    return PreparedDataset(dataset.dataset_id, dataset.source_path, dataset.source_sha256,
                           dataset.features.copy(), dataset.target, dataset.target_unit,
                           X,
                           dataset.y.iloc[selected].reset_index(drop=True),
                           dataset.regimes.iloc[selected].reset_index(drop=True),
                           dataset.groups.iloc[selected].reset_index(drop=True),
                           manifest['splits'], manifest)


def save_view(root, view):
    identifier = digest(view.manifest)
    target = Path(root) / 'workspace/loop/views' / f'{identifier}.json'
    encoded = json.dumps(view.manifest, sort_keys=True, ensure_ascii=False, indent=2) + '\n'
    try:
        atomic_text(target, encoded, overwrite=False)
    except FileExistsError:
        if target.read_text(encoding='utf-8') != encoded:
            raise ValueError('Immutable dataset view changed')
    return identifier


def load_view(root, identifier):
    if not re.fullmatch('[a-f0-9]{64}', identifier):
        raise ValueError('Invalid dataset_view_id')
    from .cnc_milling import prepare_cnc_milling
    saved = json.loads((Path(root) / 'workspace/loop/views' / f'{identifier}.json').read_text(encoding='utf-8'))
    if digest(saved) != identifier:
        raise ValueError('Dataset view checksum changed')
    dataset = prepare_cnc_milling(saved['source_path'])
    view = build_view(dataset, build_protocol(dataset), saved['released_rows'], saved['scenario'])
    if view.manifest != saved:
        raise ValueError('Dataset view source or protocol changed')
    return view
