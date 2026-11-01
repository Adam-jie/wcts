"""Export native pilot candidates in FEA-Bench prediction JSONL format."""

import argparse
import json
from pathlib import Path


def export(run_dirs: list[Path], output: Path) -> int:
    rows = []
    seen = set()
    for directory in run_dirs:
        report = json.loads((directory / 'report.json').read_text(encoding='utf-8'))
        instance_id = report['instance_id']
        if instance_id in seen:
            raise ValueError(f'Duplicate instance: {instance_id}; export competing runs separately')
        seen.add(instance_id)
        patch = (directory / 'candidate.patch').read_text(encoding='utf-8')
        rows.append({'instance_id': instance_id,
                     'model_name_or_path': report['model_requested'],
                     'full_output': '',
                     'model_patch': patch})
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8', newline='\n') as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + '\n')
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, action='append', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(f'Exported {export(args.run_dir, args.output)} prediction(s) to {args.output}')


if __name__ == '__main__':
    main()
