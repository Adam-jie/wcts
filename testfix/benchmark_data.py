"""Safe benchmark catalogue: collection does not imply runnable or evaluated."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


SWE_REVISION = '6ec7bb89b9342f664a54a6e0a6ea6501d3437cc2'
BUGSINPY_REVISION = '11c5f1eea954a42132cfd06bf257766a7963e0fd'
TASK_PUBLIC_FIELDS = {
    'swe-bench-lite': ('instance_id', 'repo', 'base_commit', 'problem_statement',
                       'version', 'environment_setup_commit', 'created_at'),
    'bugsinpy': ('instance_id', 'project', 'bug_id', 'repo', 'base_commit',
                 'base_commit_is_full', 'python_version', 'project_status',
                 'problem_statement_available'),
}


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding='utf-8'))


def _public_collection(directory: Path, identifier: str, revision: str) -> tuple[dict, list[dict]]:
    manifest = _read(directory / 'manifest.json')
    public_path = directory / 'public/tasks.jsonl'
    expected = manifest['files']['public/tasks.jsonl']
    if manifest['id'] != identifier or manifest['revision'] != revision:
        raise ValueError('Unexpected benchmark identity')
    raw = public_path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError('Benchmark public metadata checksum mismatch')
    rows = [json.loads(line) for line in raw.decode('utf-8').splitlines()]
    if len(rows) != manifest['task_count'] or len({r['instance_id'] for r in rows}) != len(rows):
        raise ValueError('Benchmark public metadata count mismatch')
    return manifest, rows


def _valid_public_collection(directory: Path, identifier: str, revision: str) -> dict:
    manifest, _ = _public_collection(directory, identifier, revision)
    return manifest


def list_benchmark_tasks(settings, identifier: str, limit: int = 24) -> dict:
    """Preview validated public task metadata; no execution or evaluator reads."""
    if not isinstance(identifier, str) or identifier not in TASK_PUBLIC_FIELDS:
        raise ValueError('Unknown collected benchmark dataset')
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError('Task preview limit must be an integer from 1 to 50')
    revision = SWE_REVISION if identifier == 'swe-bench-lite' else BUGSINPY_REVISION
    directory = Path(settings.root) / 'data/benchmarks' / identifier / revision
    try:
        _, rows = _public_collection(directory, identifier, revision)
        tasks = []
        for row in rows[:limit]:
            visible = {key: row[key] for key in TASK_PUBLIC_FIELDS[identifier] if key in row}
            if any(type(value) not in (str, int, bool) for value in visible.values()):
                raise ValueError('Public task metadata has unsupported field types')
            if 'problem_statement' in visible:
                statement = visible['problem_statement']
                if not isinstance(statement, str):
                    raise ValueError('Problem statement must be text')
                visible['problem_statement'] = statement[:4000]
                visible['problem_statement_truncated'] = len(statement) > 4000
            tasks.append(visible)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ValueError('Benchmark is not collected or its public index failed integrity validation') from exc
    notes = ['公开任务索引预览，不是修复结果；尚未接入这些任务的运行环境与评测适配器。',
             '仅列出公开元数据；未读取参考补丁、测试补丁或评测者目录。']
    if identifier == 'bugsinpy':
        notes.append('该集合暂缺自然语言问题描述，部分提交是缩写，运行前须补齐与封存。')
    return {'id': identifier, 'total': len(rows), 'status': 'collected_not_run',
            'tasks': tasks, 'has_more': len(rows) > len(tasks), 'notes': notes}


def list_benchmark_datasets(settings) -> list[dict]:
    """Return JSON-only summary; never read/return gold patches or grading tests.

    `ready` means a registered local adapter and runtime are present, not that a
    model has resolved a task. The existing run ledger remains the result source.
    """
    root = Path(settings.root)
    result = []
    for slug, title, project_id, scope, license_name in [
        ('rich-cli', 'rich-cli issue #147', 'Project/rich-cli', 'repository_issue_repair', 'MIT'),
        ('flaskr', 'Flaskr: 详情、搜索、分页', 'Project/flaskr', 'repository_feature_completion', 'BSD-3-Clause'),
    ]:
        directory = root / 'experiments' / slug
        source_path, requirements_path = directory / 'source.json', directory / 'requirements.json'
        source = _read(source_path) if source_path.is_file() else {}
        requirement_count = 0
        if requirements_path.is_file():
            requirement_count = len(_read(requirements_path).get('requirements', []))
        runtime = root / ('.venv-flaskr' if slug == 'flaskr' else '.venv-richcli')
        runtime_present = (runtime / 'Scripts/python.exe').is_file() or (runtime / 'bin/python').is_file()
        fixture_present = bool(source) and (directory / 'baseline').is_dir() and (directory / 'acceptance_tests').is_dir()
        registered = slug == 'flaskr' or (root / 'data/projects/registered/rich-cli.json').is_file()
        result.append({
            'id': slug, 'name': title, 'task_count': 1 if fixture_present else 0,
            'scope': scope, 'status': 'ready' if fixture_present and runtime_present and registered else 'environment_required',
            'license': source.get('license', license_name), 'repository': source.get('repository', ''),
            'source_url': source.get('issue_url') or source.get('requirements_url') or source.get('repository', ''),
            'revision': source.get('commit', ''),
            'ready_project_id': project_id if fixture_present and runtime_present and registered else None,
            'requirement_count': requirement_count,
            'notes': ['1 个完整工程任务；需求项数不算独立任务数。',
                      'rich-cli 是真实上游缺陷；Flaskr 是公开扩展建议形成的功能合同，不是上游 Bug。',
                      '历史实验成绩请查看实际运行记录；数据集可运行不代表本次已测试。'],
        })
    for identifier, name, revision, source_url, repository, scope in [
        ('swe-bench-lite', 'SWE-bench Lite · test', SWE_REVISION,
         'https://huggingface.co/datasets/princeton-nlp/SWE-bench_Lite',
         'https://github.com/SWE-bench/SWE-bench', 'repository_issue_repair'),
        ('bugsinpy', 'BugsInPy · 官方元数据', BUGSINPY_REVISION,
         'https://github.com/soarsmu/BugsInPy', 'https://github.com/soarsmu/BugsInPy', 'repository_bug_repair'),
    ]:
        directory = root / 'data/benchmarks' / identifier / revision
        summary = {'id': identifier, 'name': name, 'revision': revision, 'source_url': source_url,
                   'repository': repository, 'scope': scope, 'ready_project_id': None,
                   'task_count': 0, 'status': 'not_collected', 'license': '见上游与采集 manifest',
                   'notes': ['运行 scripts/collect_benchmarks.py 采集固定版本；尚未接入项目执行适配器。']}
        if (directory / 'manifest.json').is_file():
            try:
                manifest = _valid_public_collection(directory, identifier, revision)
                for key in ['task_count', 'license', 'repository_count', 'collected_at', 'evaluation_status']:
                    if key in manifest:
                        summary[key] = manifest[key]
                notes = ['已真实采集固定版本元数据，尚未安装目标仓库环境、接入项目适配器或运行模型修复。',
                         '参考补丁、验收测试和原始行仅供评测者，目录未开放给 Agent。']
                if identifier == 'bugsinpy':
                    notes += ['需补公开问题描述并适配历史 Python 环境；上游快照未发现许可证。']
                summary.update({'status': 'collected', 'notes': notes,
                                'public_sha256': manifest['files']['public/tasks.jsonl'],
                                'manifest_sha256': hashlib.sha256((directory / 'manifest.json').read_bytes()).hexdigest()})
            except (ValueError, KeyError, OSError):
                summary.update({'status': 'integrity_failed', 'notes': ['采集清单或公开任务索引校验失败，禁止计入有效数据。']})
        result.append(summary)
    humaneval = root / 'data/humaneval'
    human_summary = {'id': 'humaneval', 'name': 'HumanEval · 函数级辅助集', 'task_count': 0,
                     'scope': 'function_completion_auxiliary', 'status': 'not_collected', 'license': 'MIT',
                     'repository': 'https://github.com/openai/human-eval',
                     'source_url': 'https://github.com/openai/human-eval', 'revision': '',
                     'ready_project_id': None,
                     'notes': ['函数级辅助测试，不计入完整工程修复成功率。已有示例含人为构造的缺失实现，不能称上游真实 Bug。']}
    if (humaneval / 'manifest.json').is_file():
        try:
            manifest = _read(humaneval / 'manifest.json')
            if hashlib.sha256((humaneval / 'HumanEval.jsonl.gz').read_bytes()).hexdigest() != manifest['sha256']:
                raise ValueError('HumanEval checksum mismatch')
            human_summary.update({'task_count': manifest['records'], 'revision': manifest['commit'],
                                  'status': 'collected', 'public_sha256': manifest['sha256']})
        except (ValueError, KeyError, OSError):
            human_summary['status'] = 'integrity_failed'
    result.append(human_summary)
    return result
