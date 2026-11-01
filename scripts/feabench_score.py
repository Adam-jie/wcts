"""Summarize an official FEA-Bench report against a frozen task manifest."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def summarize(manifest: dict, report: dict, subset: str) -> dict:
    entries = [row for row in manifest['entries'] if subset == 'full' or row['lite']]
    ids = [row['instance_id'] for row in entries]
    selected = set(ids)
    if not ids or len(ids) != len(selected):
        raise ValueError('Manifest has no tasks or duplicate task IDs')
    groups = {}
    for name in ('submitted', 'completed', 'resolved', 'unresolved', 'empty_patch', 'error'):
        values = report[name + '_ids']
        groups[name] = set(values)
        if len(groups[name]) != len(values) or not groups[name] <= selected:
            raise ValueError(f'Invalid or out-of-scope IDs in {name}_ids')
    if not groups['resolved'] <= groups['completed'] <= groups['submitted']:
        raise ValueError('Resolved/completed/submitted IDs are inconsistent')
    if not (groups['unresolved'] | groups['empty_patch'] | groups['error']) <= groups['submitted']:
        raise ValueError('Failure IDs must belong to submitted tasks')
    if (groups['resolved'] & groups['unresolved']
            or groups['completed'] != groups['resolved'] | groups['unresolved']
            or groups['completed'] & (groups['error'] | groups['empty_patch'])
            or groups['error'] & groups['empty_patch']):
        raise ValueError('Official result categories are inconsistent')
    missing = selected - groups['submitted']
    pending = groups['submitted'] - groups['completed'] - groups['error'] - groups['empty_patch']
    valid = not missing and not pending and not groups['error']
    return {
        'dataset_revision': manifest['dataset_revision'], 'subset': subset,
        'planned': len(ids), 'submitted': len(groups['submitted']),
        'completed': len(groups['completed']), 'resolved': len(groups['resolved']),
        'unresolved': len(groups['unresolved']), 'empty_patch': len(groups['empty_patch']),
        'environment_or_evaluation_errors': len(groups['error']),
        'not_submitted': len(missing), 'submitted_not_completed': len(pending),
        'resolved_ratio_percent': round(100 * len(groups['resolved']) / len(ids), 2) if valid else None,
        'full_scope_result_available': valid,
        'previously_debugged_tasks_in_scope': [row['instance_id'] for row in entries if row['previously_used_for_debugging']],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--subset', choices=('full', 'lite'), required=True)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding='utf-8'))
    report = json.loads(args.report.read_text(encoding='utf-8'))
    print(json.dumps(summarize(manifest, report, args.subset), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
