"""Download pinned benchmark evidence without installing or executing a project.

Raw upstream rows, reference fixes and grading tests are evaluator-only. Only
explicitly allowlisted task metadata is written to public/tasks.jsonl.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import re
import tempfile
from urllib.parse import urlencode
import urllib.request
import zipfile


ROOT = Path(__file__).resolve().parent.parent
SWE_REVISION = '6ec7bb89b9342f664a54a6e0a6ea6501d3437cc2'
SWE_REPOSITORY = 'princeton-nlp/SWE-bench_Lite'
SWE_HARNESS_REVISION = '02e7a74ffd0b707aab73d203fe87bdc7c76afc8e'
BUGSINPY_REVISION = '11c5f1eea954a42132cfd06bf257766a7963e0fd'
SWE_PARQUET_SHA256 = '7a21f37b8bc179c7db5beeb14e88ac538ba283455c776e6b2535bbfb6e3551b4'
BUGSINPY_ZIP_SHA256 = '88bc8fbaa2f347e318c5c585f89ec844ca27f7dd6761c3e66d63f4261a08fad1'
SWE_PUBLIC_FIELDS = ('instance_id', 'repo', 'base_commit', 'problem_statement',
                     'version', 'environment_setup_commit', 'created_at')
SWE_PRIVATE_FIELDS = ('instance_id', 'patch', 'test_patch', 'FAIL_TO_PASS', 'PASS_TO_PASS')


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def json_bytes(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8')


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(json_bytes(value))


def write_rows(path: Path, rows: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(''.join(json.dumps(row, ensure_ascii=False, sort_keys=True) + '\n'
                            for row in rows), encoding='utf-8')


def download(url: str, limit=32_000_000) -> tuple[bytes, dict]:
    request = urllib.request.Request(url, headers={'User-Agent': 'TestFix-benchmark-collector/1.0'})
    with urllib.request.urlopen(request, timeout=90) as response:
        data = response.read(limit + 1)
        if len(data) > limit:
            raise ValueError('Benchmark source exceeds bounded download size')
        return data, {key.lower(): value for key, value in response.headers.items()}


def split_swe_rows(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """Do not pass gold, grader names, test patches, or issue hints to agents."""
    public, private, seen = [], [], set()
    for row in rows:
        if any(key not in row for key in SWE_PUBLIC_FIELDS + SWE_PRIVATE_FIELDS):
            raise ValueError('SWE-bench source schema is incomplete')
        identifier = row['instance_id']
        if identifier in seen or not isinstance(identifier, str) or not identifier:
            raise ValueError('SWE-bench IDs must be nonempty and unique')
        if not re.fullmatch(r'[0-9a-f]{40}', row['base_commit']):
            raise ValueError('SWE-bench base commit must be pinned')
        seen.add(identifier)
        public.append({key: row[key] for key in SWE_PUBLIC_FIELDS})
        private.append({key: row[key] for key in SWE_PRIVATE_FIELDS})
    return public, private


def verify_collection(directory: Path) -> dict:
    manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
    for name, expected in manifest['files'].items():
        relative = Path(name)
        target = directory / relative
        if (relative.is_absolute() or '..' in relative.parts or
                not target.resolve().is_relative_to(directory.resolve()) or
                target.is_symlink() or not target.is_file()):
            raise ValueError('Invalid or missing benchmark evidence path')
        if digest(target.read_bytes()) != expected:
            raise ValueError('Benchmark evidence changed: ' + name)
    rows = [json.loads(line) for line in (directory / 'public/tasks.jsonl').read_text(encoding='utf-8').splitlines()]
    if len(rows) != manifest['task_count'] or len({row['instance_id'] for row in rows}) != len(rows):
        raise ValueError('Benchmark public task count/identity mismatch')
    if manifest['id'] == 'swe-bench-lite' and any(set(row) != set(SWE_PUBLIC_FIELDS) for row in rows):
        raise ValueError('SWE-bench public rows contain unapproved fields')
    return manifest


def _finish(directory: Path, metadata: dict) -> dict:
    metadata.update({
        'schema_version': 1,
        'collected_at': datetime.now(timezone.utc).isoformat(),
        'status': 'collected',
        'evaluation_status': 'not_run',
        'files': {path.relative_to(directory).as_posix(): digest(path.read_bytes())
                  for path in sorted(directory.rglob('*')) if path.is_file()},
    })
    write_json(directory / 'manifest.json', metadata)
    return verify_collection(directory)


def _collect_swe(directory: Path) -> dict:
    base = f'https://huggingface.co/datasets/{SWE_REPOSITORY}/resolve/{SWE_REVISION}'
    restricted = directory / 'evaluator-only'
    restricted.mkdir(parents=True)
    sources = {}
    for local, url in {
        'test.parquet': base + '/data/test-00000-of-00001.parquet',
        'dataset-card.md': base + '/README.md',
        'harness-LICENSE': f'https://raw.githubusercontent.com/SWE-bench/SWE-bench/{SWE_HARNESS_REVISION}/LICENSE',
    }.items():
        raw, _ = download(url)
        if local == 'test.parquet' and digest(raw) != SWE_PARQUET_SHA256:
            raise ValueError('Pinned SWE-bench Parquet checksum mismatch')
        (restricted / local).write_bytes(raw)
        sources['evaluator-only/' + local] = url
    rows = []
    for offset in range(0, 300, 100):
        url = 'https://datasets-server.huggingface.co/rows?' + urlencode({
            'dataset': SWE_REPOSITORY, 'config': 'default', 'split': 'test',
            'offset': offset, 'length': 100, 'revision': SWE_REVISION})
        raw, headers = download(url)
        if headers.get('x-revision') != SWE_REVISION:
            raise ValueError('Dataset viewer revision does not match pinned source')
        page = json.loads(raw)
        if page.get('partial') or page.get('num_rows_total') != 300:
            raise ValueError('SWE-bench page is partial or has unexpected total')
        page_rows = page['rows']
        if [item['row_idx'] for item in page_rows] != list(range(offset, offset + 100)):
            raise ValueError('SWE-bench page is not the expected contiguous 100 rows')
        if any(item.get('truncated_cells') for item in page_rows):
            raise ValueError('SWE-bench viewer truncated source cells')
        name = f'rows-{offset:03}.json'
        (restricted / name).write_bytes(raw)
        sources['evaluator-only/' + name] = url
        write_json(restricted / f'rows-{offset:03}.receipt.json', {'source_url': url,
                   'dataset_revision_header': headers['x-revision'], 'sha256': digest(raw)})
        rows.extend(item['row'] for item in page_rows)
    public, private = split_swe_rows(rows)
    write_rows(directory / 'public/tasks.jsonl', public)
    write_rows(restricted / 'grading.jsonl', private)
    # Selection is fixed before any model inference, across every represented repo.
    selected = []
    for repository in sorted({row['repo'] for row in public}):
        ordered = sorted((row for row in public if row['repo'] == repository),
                         key=lambda row: digest(('testfix-v1:' + row['instance_id']).encode()))
        selected.extend(row['instance_id'] for row in ordered[:2])
    write_json(directory / 'public/stratified-pilot.json', {
        'purpose': 'adaptation queue, not measured results',
        'selection_rule': 'up to 2 per repo, lowest SHA256(testfix-v1:<instance_id>)',
        'instance_ids': selected, 'task_count': len(selected),
        'evaluation_status': 'not_run'})
    return _finish(directory, {
        'id': 'swe-bench-lite', 'name': 'SWE-bench Lite (official test split)',
        'repository': 'https://github.com/SWE-bench/SWE-bench',
        'source_url': f'https://huggingface.co/datasets/{SWE_REPOSITORY}',
        'revision': SWE_REVISION, 'harness_revision': SWE_HARNESS_REVISION,
        'license': 'MIT (upstream harness declaration; source repository licenses retained)',
        'license_notes': 'The pinned HF card has no separate license tag. Harness LICENSE retained. Do not relicense upstream project code.',
        'task_count': len(public), 'repository_count': len({row['repo'] for row in public}),
        'repository_counts': dict(sorted(Counter(row['repo'] for row in public).items())),
        'scope': 'repository_issue_repair', 'ready_project_id': None,
        'public_fields': list(SWE_PUBLIC_FIELDS), 'sources': sources,
        'notes': ['Only metadata collected; no repository environment built or model repair run.',
                  'Docker/Linux per-instance adapter and official grading harness still required.',
                  'All upstream raw files, fixes, test patches and test names stay evaluator-only.'],
    })


def parse_info(text: str) -> dict[str, str]:
    values = {}
    for line in text.splitlines():
        match = re.fullmatch(r'([a-zA-Z_]+)="(.*)"\s*', line)
        if match:
            values[match[1]] = match[2]
    return values


def _collect_bugsinpy(directory: Path) -> dict:
    url = f'https://codeload.github.com/soarsmu/BugsInPy/zip/{BUGSINPY_REVISION}'
    raw, _ = download(url)
    if digest(raw) != BUGSINPY_ZIP_SHA256:
        raise ValueError('Pinned BugsInPy archive checksum mismatch')
    restricted = directory / 'evaluator-only'
    restricted.mkdir(parents=True)
    (restricted / 'upstream-metadata.zip').write_bytes(raw)
    public = []
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        # Read metadata only. Never extract or execute upstream shell/Python files.
        for name in sorted(archive.namelist()):
            match = re.fullmatch(r'[^/]+/projects/([^/]+)/bugs/(\d+)/bug.info', name)
            if not match:
                continue
            project, bug = match.groups()
            data = parse_info(archive.read(name).decode('utf-8'))
            project_name = name.split('/projects/')[0] + f'/projects/{project}/project.info'
            project_info = parse_info(archive.read(project_name).decode('utf-8'))
            commit = data.get('buggy_commit_id', '')
            if not re.fullmatch(r'[0-9a-f]{7,40}', commit):
                raise ValueError('BugsInPy has an invalid buggy revision')
            public.append({
                'instance_id': f'BugsInPy/{project}/{bug}', 'project': project,
                'bug_id': int(bug), 'repo': project_info['github_url'],
                'base_commit': commit, 'base_commit_is_full': len(commit) == 40,
                'python_version': data.get('python_version', ''),
                'project_status': project_info.get('status', 'unknown'),
                'problem_statement_available': False,
            })
        declared = [name for name in archive.namelist()
                    if name.rsplit('/', 1)[-1].lower() in {'license', 'license.txt', 'copying'}]
    if not public:
        raise ValueError('BugsInPy archive contains no bug metadata')
    write_rows(directory / 'public/tasks.jsonl', public)
    return _finish(directory, {
        'id': 'bugsinpy', 'name': 'BugsInPy official bug metadata',
        'repository': 'https://github.com/soarsmu/BugsInPy',
        'source_url': 'https://github.com/soarsmu/BugsInPy',
        'revision': BUGSINPY_REVISION, 'license': 'not declared in pinned upstream snapshot',
        'license_files_found': declared,
        'task_count': len(public), 'repository_count': len({row['repo'] for row in public}),
        'abbreviated_commit_count': sum(not row['base_commit_is_full'] for row in public),
        'repository_counts': dict(sorted(Counter(row['project'] for row in public).items())),
        'scope': 'repository_bug_repair', 'ready_project_id': None,
        'sources': {'evaluator-only/upstream-metadata.zip': url},
        'notes': ['Metadata only; no target project cloned, installed, executed or repaired.',
                  'Bug metadata lacks natural-language requirements; inspect original issue and prepare public task contract before model use.',
                  'Upstream historical Python environments and hidden fixed-version tests require a Linux/Docker adapter.',
                  'Some upstream buggy commit IDs are abbreviated; resolve and seal full 40-character commits before execution.',
                  'No upstream LICENSE found; local collection is not a redistribution license.'],
    })


def collect(destination: Path, dataset: str) -> dict:
    choices = {'swe-bench-lite': (SWE_REVISION, _collect_swe),
               'bugsinpy': (BUGSINPY_REVISION, _collect_bugsinpy)}
    revision, collector = choices[dataset]
    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / dataset / revision
    if target.exists():
        return verify_collection(target)  # Never replace earlier raw evidence.
    target.parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.collect-', dir=target.parent) as temporary:
        staging = Path(temporary) / 'collection'
        staging.mkdir()
        result = collector(staging)
        staging.rename(target)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', choices=['all', 'swe-bench-lite', 'bugsinpy'], default='all')
    parser.add_argument('--destination', type=Path, default=ROOT / 'data/benchmarks')
    args = parser.parse_args()
    selected = ['swe-bench-lite', 'bugsinpy'] if args.dataset == 'all' else [args.dataset]
    for dataset in selected:
        result = collect(args.destination, dataset)
        print(json.dumps({key: result[key] for key in ['id', 'revision', 'task_count', 'repository_count', 'status', 'evaluation_status']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
