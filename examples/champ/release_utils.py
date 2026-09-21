"""Validate release inputs without loading models or contacting inference APIs."""
import hashlib
import json
from pathlib import Path

from core import TASKS


def validate_cases(document):
    if document.get('dataset') not in ('test', 'val'):
        raise ValueError('Specify the official test or val split in the cases file')
    cases = document.get('cases', [])
    if not cases:
        raise ValueError('No episodes selected')
    seen = set()
    for case in cases:
        task, episode = case.get('task'), case.get('episode')
        if task not in TASKS or type(episode) is not int or not 0 <= episode < 50:
            raise ValueError('Expected a known task and official episode 0–49')
        identity = (task, episode)
        if identity in seen:
            raise ValueError('Duplicate task/episode identity')
        seen.add(identity)
    return cases


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def validate_checkpoints(vla_checkpoint, monitor_adapter):
    vla, adapter = Path(vla_checkpoint), Path(monitor_adapter)
    for path in (vla/'params', vla/'assets', vla.parent/'history_config.txt',
                 adapter/'adapter_config.json', adapter/'adapter_model.safetensors'):
        if not path.exists():
            raise ValueError(f'Missing checkpoint component: {path}')
    if (vla.parent/'history_config.txt').read_text().strip() != 'symbolic-grounded-subgoal.yaml':
        raise ValueError('VLA history_config.txt must select symbolic-grounded-subgoal.yaml')
    manifest = json.loads((Path(__file__).parent/'weights.json').read_text())
    for name, expected in manifest['monitor']['files'].items():
        if sha256(adapter/name) != expected:
            raise ValueError(f'Monitor file differs from the released checkpoint: {name}')


def summarize(document, results):
    cases = validate_cases(document)
    roots = [Path(p) for p in results] if isinstance(results, (list, tuple)) else [Path(results)]
    rows, counts = [], dict(success=0, fail=0, timeout=0, error=0, pending=0)
    for case in cases:
        paths = [root/case['task']/f"ep{case['episode']:03d}"/'result.json' for root in roots]
        found = [p for p in paths if p.exists()]
        if len(found) > 1:
            raise ValueError(f'Duplicate result for {case}; select one explicit attempt, not best-of runs')
        path = found[0] if found else None
        result = json.loads(path.read_text()) if path else {**case, 'dataset': document['dataset'], 'status': 'pending'}
        dataset = result.get('dataset')
        if path and dataset is None and (path.parent/'identity.json').exists():
            dataset = json.loads((path.parent/'identity.json').read_text()).get('dataset')
        if dataset != document['dataset']:
            raise ValueError(f'Result split is missing or differs from cases split: {path}')
        if (result.get('task'), result.get('episode')) != (case['task'], case['episode']):
            raise ValueError(f'Result identity mismatch: {path}')
        status = result['status']
        if status not in counts:
            raise ValueError(f'Unknown result status: {status}')
        counts[status] += 1
        rows.append(result)
    return dict(dataset=document['dataset'], expected=len(cases), counts=counts,
                complete=counts['error'] == counts['pending'] == 0,
                success_rate=counts['success']/len(cases), cases=rows)
