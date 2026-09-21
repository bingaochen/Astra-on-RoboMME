"""Generate explicit, disjoint official benchmark episode lists."""
import argparse
import json
from pathlib import Path

from core import TASKS
from release_utils import validate_cases


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--dataset', choices=['test', 'val'], default='test')
    parser.add_argument('--tasks', nargs='+', choices=TASKS, default=TASKS)
    parser.add_argument('--episodes', nargs='+', type=int, default=list(range(50)))
    parser.add_argument('--shards', type=int, default=1)
    args = parser.parse_args()
    document = dict(dataset=args.dataset, cases=[dict(task=t, episode=e) for t in args.tasks for e in args.episodes])
    cases = validate_cases(document)
    if not 1 <= args.shards <= len(cases):
        parser.error('shards must be between 1 and the number of episodes')
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=False)
    (out/'all.json').write_text(json.dumps(document, indent=2)+'\n')
    for shard in range(args.shards):
        (out/f'shard_{shard:02d}.json').write_text(json.dumps(
            dict(dataset=args.dataset, cases=cases[shard::args.shards]), indent=2)+'\n')
    print(f'{len(cases)} distinct episodes; {args.shards} disjoint shards in {out}')


if __name__ == '__main__':
    main()
