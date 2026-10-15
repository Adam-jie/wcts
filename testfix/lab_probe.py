"""Bounded manual inputs against a pinned baseline and accepted wheel."""
from __future__ import annotations
import hashlib
import io
import json
from pathlib import Path
import shutil
import uuid
import zipfile
from .lab import IDENTIFIER
from .package_tools import _extract_wheel, _run_bounded
from .project_tools import files_under, get_project
from .reports import now


def _sealed_bytes(path, allowed, expected, *, limit=64*1024*1024):
    path = Path(path)
    resolved = path.resolve()
    if (not resolved.is_relative_to(allowed.resolve()) or path.is_symlink()
            or not resolved.is_file() or resolved.stat().st_size > limit):
        raise ValueError('Probe artifact is missing or outside the accepted run')
    content = resolved.read_bytes()
    if not expected or hashlib.sha256(content).hexdigest() != expected:
        raise ValueError('Probe artifact changed after acceptance')
    return content


def execute_probe(settings, run, text, mode='csv'):
    if not isinstance(text, str) or not 1 <= len(text) <= 5000 or mode not in {'csv', 'print', 'csv_head_tail'}:
        raise ValueError('Probe supports bounded CSV or print text only')
    identifier = run.get('id', '')
    task = next(iter(run.get('tasks', [])), {})
    if (not IDENTIFIER.fullmatch(identifier) or run.get('project_id') != 'Project/rich-cli'
            or run.get('status') != 'completed' or task.get('status') != 'accepted'
            or not all(task.get('verdict', {}).get(key) is True for key in
                       ('build_succeeds', 'review_bound_to_candidate', 'protected_files_unchanged', 'satisfies_tested_requirements'))):
        raise ValueError('Only an accepted rich-cli delivery can be probed')
    project = get_project(settings, 'Project/rich-cli')
    baseline = Path(project['baseline']).resolve()
    if not baseline.is_relative_to(settings.root.resolve()):
        raise ValueError('Pinned baseline leaves the workspace')
    hashes = files_under(baseline)
    if not project['source'].get('files') or hashes != project['source']['files']:
        raise ValueError('Pinned baseline source changed')
    if task.get('source', {}).get('commit') != project['source'].get('commit'):
        raise ValueError('This run uses a different registered source version')
    run_root = settings.artifacts / identifier
    accepted_zip = _sealed_bytes(run_root/'completed-project.zip', run_root, run.get('download', {}).get('sha256'))
    build = task.get('build', {})
    wheel_bytes = _sealed_bytes(build.get('wheel', ''), run_root/'tool_calls', build.get('sha256'))
    config = project['package_config']
    if config['package'] != 'rich_cli' or config['cli_module'] != 'rich_cli':
        raise ValueError('The interactive probe is bound to rich_cli')
    with zipfile.ZipFile(io.BytesIO(accepted_zip)) as archive, zipfile.ZipFile(io.BytesIO(wheel_bytes)) as wheel:
        for name in wheel.namelist():
            if name.startswith('rich_cli/') and not name.endswith('/'):
                source_name = 'project/' + config['source_root'].strip('/') + '/' + name
                if archive.read(source_name) != wheel.read(name):
                    raise ValueError('Accepted wheel does not match the delivered source')
    probe_id = uuid.uuid4().hex
    directory = run_root/'lab_probes'/probe_id
    directory.mkdir(parents=True, exist_ok=False)
    baseline_copy = directory/'baseline'
    for name in hashes:
        target = baseline_copy/name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(baseline/name, target)
    if files_under(baseline_copy) != hashes:
        raise ValueError('Baseline snapshot copy changed before probe')
    candidate_wheel = directory/'candidate.whl'
    candidate_wheel.write_bytes(wheel_bytes)
    candidate_install = directory/'candidate-install'
    _extract_wheel(candidate_wheel, candidate_install, config)
    python = str(Path(project['python']).resolve())
    baseline_dist = directory/'baseline-dist'
    built = _run_bounded([python, '-I', '-B', '-X', 'utf8', '-m', 'build', '--no-isolation',
        '--wheel', '--outdir', str(baseline_dist), str(baseline_copy)], cwd=directory, timeout_s=60)
    if built['exit_code'] != 0 or built['timeout'] or built['output_limit_exceeded']:
        raise ValueError('Pinned baseline wheel build failed: ' + built['stderr'][-1200:])
    wheels = list(baseline_dist.glob('*.whl'))
    if len(wheels) != 1:
        raise ValueError('Baseline did not build exactly one wheel')
    baseline_install = directory/'baseline-install'
    _extract_wheel(wheels[0], baseline_install, config)
    cli_args = ['--print' if mode == 'print' else '--csv', '--width', '120']
    if mode == 'csv_head_tail':
        cli_args.extend(['--head', '1', '--tail', '1'])
    cli_args.append('-')
    results = {}
    worker = Path(__file__).with_name('package_cli_worker.py').resolve()
    for label, installed in [('baseline', baseline_install), ('candidate', candidate_install)]:
        binding = directory/(label+'-binding.json')
        command = [python, '-I', '-B', '-X', 'utf8', str(worker), '--wheel-root', str(installed),
                   '--package', 'rich_cli', '--module', 'rich_cli', '--binding-json', str(binding), '--', *cli_args]
        result = _run_bounded(command, cwd=directory, timeout_s=15, stdin=text)
        recorded = json.loads(binding.read_text(encoding='utf-8')) if binding.is_file() else {}
        result['binding_ok'] = (recorded.get('success') is True and recorded.get('wheel_root') == str(installed.resolve())
                                and not recorded.get('escaped_modules'))
        results[label] = result
        for stream in ('stdout', 'stderr'):
            (directory/(label+'-'+stream+'.txt')).write_text(result[stream], encoding='utf-8')
    document = {'schema': 'testfix.interactive-probe.v1', 'id': probe_id, 'run_id': identifier,
        'created_at': now(), 'mode': mode, 'input': text,
        'input_sha256': hashlib.sha256(text.encode()).hexdigest(),
        'baseline_source_commit': project['source']['commit'],
        'baseline_wheel_sha256': hashlib.sha256(wheels[0].read_bytes()).hexdigest(),
        'candidate_wheel_sha256': build['sha256'], 'accepted_archive_sha256': run['download']['sha256'],
        'cli_args': cli_args, 'results': results, 'original_acceptance_scores_unchanged': True, 'model_calls': 0}
    receipt = directory/'receipt.json'
    receipt.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding='utf-8')
    return {'id': probe_id, 'mode': mode, 'input': text, **results,
        'receipt': {'id': probe_id, 'sha256': hashlib.sha256(receipt.read_bytes()).hexdigest()},
        'note': '真实子进程；原版和候选均从 wheel 执行；本次未调用 LLM，未改变原验收分数。'}
