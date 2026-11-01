"""Pin public GitHub refs; metadata collection never means runnable task data."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parent.parent


def pin_source(project: dict) -> dict:
    repository = project['repository']
    match = re.fullmatch(r'https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)', repository)
    ref = project['ref']
    if not match or not isinstance(ref, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._/-]{0,127}', ref):
        raise ValueError('Expected a public GitHub repository and an explicit ref')
    # Public Git ref advertisement avoids REST quotas and needs no credential.
    # This never clones, checks out, installs or executes the target repository.
    branch, tag, peeled = f'refs/heads/{ref}', f'refs/tags/{ref}', f'refs/tags/{ref}^{{}}'
    env = {**os.environ, 'GIT_TERMINAL_PROMPT': '0', 'GCM_INTERACTIVE': 'Never'}
    response = subprocess.run(['git', 'ls-remote', '--exit-code', repository, branch, tag, peeled],
                              capture_output=True, timeout=45, env=env, check=False)
    raw = response.stdout
    if response.returncode or len(raw) > 64_000:
        raise ValueError(f'Cannot resolve public ref for {project["id"]}; no source lock was published')
    rows = dict((name, sha) for sha, name in (line.split() for line in raw.decode('ascii').splitlines()))
    if branch in rows and tag in rows:
        raise ValueError('Ambiguous branch/tag; choose an unambiguous source ref')
    commit = rows.get(peeled) or rows.get(tag) or rows.get(branch)
    if not isinstance(commit, str) or not re.fullmatch('[a-f0-9]{40}', commit):
        raise ValueError('GitHub did not advertise a full commit SHA')
    return {'project_id': project['id'], 'domain': project['domain'], 'repository': repository,
            'requested_ref': ref, 'commit': commit, 'lookup_method': 'git_ls_remote',
            'commit_url': f'{repository}/commit/{commit}',
            'response_sha256': hashlib.sha256(raw).hexdigest(), 'status': 'metadata_only',
            'source_downloaded': False, 'submodules_locked': False, 'environment_ready': False,
            'preflight_passed': False, 'model_repair_executed': False}


def snapshot(catalog_path: Path, output: Path) -> dict:
    if output.exists():
        raise ValueError('Output already exists; keep the previous source lock')
    catalog_bytes = catalog_path.read_bytes()
    catalog = json.loads(catalog_bytes)
    if catalog.get('schema') != 'testfix.dataset-design.v1':
        raise ValueError('Unsupported dataset design schema')
    projects = catalog['projects']
    if not isinstance(projects, list) or not 1 <= len(projects) <= 32:
        raise ValueError('Expected 1-32 projects')
    if len({p['id'] for p in projects}) != len(projects):
        raise ValueError('Duplicate project ID')
    with ThreadPoolExecutor(max_workers=4) as executor:
        sources = list(executor.map(pin_source, projects))
    result = {'schema': 'testfix.dataset-source-lock.v1', 'suite_id': catalog['id'],
              'collected_at': datetime.now(timezone.utc).isoformat(),
              'catalog_sha256': hashlib.sha256(catalog_bytes).hexdigest(),
              'status': 'source_metadata_collected_not_code_or_tasks', 'sources': sources,
              'execution_performed': False, 'ready_case_count': 0,
              'notice': 'Only public refs were pinned. Full source, submodules, environments, faults and independent acceptance still require preparation.'}
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog', type=Path, default=ROOT/'examples/datasets/large-project-suite.json')
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    result = snapshot(args.catalog, args.output)
    print(json.dumps({'output': str(args.output.resolve()), 'source_refs_pinned': len(result['sources']),
                      'ready_case_count': 0, 'execution_performed': False}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
