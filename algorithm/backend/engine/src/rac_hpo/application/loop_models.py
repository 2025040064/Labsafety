"""LightGBM artifact references and paired evaluation for the local simulation."""
import json
from pathlib import Path

import numpy as np

from rac_hpo.evaluation.evaluator import evaluate
from rac_hpo.infrastructure.artifacts import file_hash
from rac_hpo.training.model_export import load_model


def model_reference(experiment):
    path = Path(experiment['artifacts']['model'])
    manifest = json.loads((Path(experiment['directory'])/'manifest.json').read_text(encoding='utf-8'))
    return {'experiment_id': experiment['id'], 'path': str(path), 'sha256': file_hash(path),
            'dataset_view_id': experiment['dataset_view_id'], 'features': manifest['feature_order'],
            'metrics': experiment['metrics'], 'data_mode': experiment['data_mode']}


def predict(reference, X):
    if file_hash(reference['path']) != reference['sha256']:
        raise ValueError('Model artifact checksum changed')
    if list(X.columns) != reference['features']:
        raise ValueError('Model feature order changed')
    model = load_model(reference['path'])
    if model.feature_name() != [name.replace(' ', '_') for name in reference['features']]:
        raise ValueError('Saved model feature schema is incompatible')
    values = np.asarray(model.predict(X), dtype=float)
    if values.shape != (len(X),) or not np.isfinite(values).all():
        raise ValueError('Model predictions are invalid')
    return values


def metrics(dataset, rows, predictions):
    return evaluate(dataset.y.iloc[rows], predictions, dataset.regimes.iloc[rows],
                    dataset.groups.iloc[rows], group_kind='physical_tool', minimum_groups=5)
