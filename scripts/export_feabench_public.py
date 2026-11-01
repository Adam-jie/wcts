"""Export model-visible fields from a reconstructed FEA-Bench Standard dataset."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def public_task(row: dict) -> dict:
    fields = ('instance_id', 'repo', 'base_commit', 'problem_statement', 'hints_text')
    task = {name: row.get(name) for name in fields}
    if not all(isinstance(task[name], str) and task[name].strip() for name in fields[:-1]):
        raise ValueError('Task lacks a required public field')
    if task['hints_text'] is not None and not isinstance(task['hints_text'], str):
        raise ValueError('Task hints must be text or null')
    return task


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    from datasets import load_from_disk

    rows = load_from_disk(str(args.dataset))['test']
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8', newline='\n') as stream:
        for row in rows:
            stream.write(json.dumps(public_task(row), ensure_ascii=False) + '\n')
    print(json.dumps({'tasks': len(rows), 'output': str(args.output)}))


if __name__ == '__main__':
    main()
