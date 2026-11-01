"""Use an official FEA-Bench Standard task with the TestFix team pilot.

The official dataset and evaluator remain unchanged. This adapter is currently
limited to the existing native SQLGlot pilot and is not an official score.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.export_feabench_predictions import export
from scripts.run_feabench_pilot import run
from testfix.config import DEFAULT_CODEX_MODEL


def read_standard_task(dataset: Path, instance_id: str) -> dict:
    if dataset.is_dir():
        try:
            from datasets import load_from_disk
        except ImportError as error:
            raise RuntimeError('The official datasets package is required to read a saved Standard dataset') from error
        rows = load_from_disk(str(dataset))['test']
    else:
        with dataset.open(encoding='utf-8') as stream:
            rows = [json.loads(line) for line in stream if line.strip()]
    matches = [row for row in rows if row['instance_id'] == instance_id]
    if len(matches) != 1:
        raise ValueError('The selected Standard dataset must contain the task exactly once')
    return matches[0]


def make_public_case(row: dict, template: dict) -> dict:
    for name in ('instance_id', 'repo', 'base_commit'):
        if row.get(name) != template.get(name):
            raise ValueError('Official task and local pilot disagree on ' + name)
    statement = row.get('problem_statement')
    hints = row.get('hints_text') or ''
    if not isinstance(statement, str) or not statement.strip() or not isinstance(hints, str):
        raise ValueError('Standard task requires a public problem_statement and textual hints_text')
    public = dict(template)
    public['requirement'] = {'id':template['requirement']['id'], 'description':statement}
    public['problem_statement'] = statement
    public['hints_text'] = hints
    public['public_api'] = []
    public['interface_hints'] = []
    public['setting'] = ('Provided FEA-Bench Standard-format problem_statement and hints_text; '
                         'dataset provenance must be recorded separately; native judge, not official Docker score')
    return public


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--standard-dataset', type=Path, required=True,
                        help='Official reconstructed Standard dataset directory or exported JSONL')
    parser.add_argument('--case-template', type=Path, required=True)
    parser.add_argument('--case-output', type=Path, required=True)
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--model', default=DEFAULT_CODEX_MODEL)
    parser.add_argument('--execute-model', action='store_true')
    args = parser.parse_args()
    template = json.loads(args.case_template.read_text(encoding='utf-8'))
    row = read_standard_task(args.standard_dataset, template['instance_id'])
    case = make_public_case(row, template)
    args.case_output.parent.mkdir(parents=True, exist_ok=True)
    with args.case_output.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(case, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    report = run(args.case_output.resolve(), args.run_dir.resolve(), args.execute_model, args.model, team=True)
    prediction = args.run_dir / 'predictions-feabench.jsonl'
    if (args.run_dir / 'candidate.patch').is_file():
        export([args.run_dir], prediction)
    print(json.dumps({'instance_id':report['instance_id'], 'status':report['status'],
                      'accepted':report.get('accepted'), 'model_calls':report['model_calls'],
                      'prediction':str(prediction) if prediction.exists() else None,
                      'error':report.get('error')}, ensure_ascii=False))
    return 0 if report['status'] in {'preflight', 'completed'} else 1


if __name__ == '__main__':
    raise SystemExit(main())
