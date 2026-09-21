"""Score the requested identities, retaining errors and missing episodes in the denominator."""
import argparse
import json
from pathlib import Path

from release_utils import summarize


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases', required=True)
    parser.add_argument('--results', required=True, nargs='+', help='One or more disjoint shard result directories')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    report = summarize(json.loads(Path(args.cases).read_text()), args.results)
    with Path(args.output).open('x') as stream:
        json.dump(report, stream, indent=2)
        stream.write('\n')
    print(json.dumps({k:v for k,v in report.items() if k != 'cases'}, indent=2))


if __name__ == '__main__':
    main()
