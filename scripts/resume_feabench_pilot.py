"""Continue a failed native pilot with unchanged tests and a separate budget.

The parent is evidence, never a mutable checkpoint. Only public capability
outputs and generated-test feedback can reach the model. The independent judge
runs after the last model call. This is a Windows native preexperiment.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.feabench_context import build_stage_context, serialized_bytes
from scripts.run_feabench_pilot import (CAPS, PATCH_SCHEMA, TIMEOUTS, patch_candidate,
    save, sha, suite_feedback, validate_case, validate_tests)
from testfix.config import Settings
from testfix.package_tools import _run_bounded, run_package_suite
from testfix.project_eval import _diff, _mapping_sha256, _paired_cases_match
from testfix.project_models import ProjectModelProvider, _validate_schema
from testfix.project_tools import confined, files_under
from testfix.project_workflow import CAPABILITY_NAMES, SCHEMAS, ExecutionBudget
from testfix.skills import load_catalog

LIMITS = {'wall_seconds': 900, 'token_limit': 400_000, 'model_slots': 1, 'cpu_slots': 1}


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def validate_parent(case, parent_dir, catalog):
    """Return validated public inheritance; never load post-budget judge data."""
    parent_dir = Path(parent_dir).resolve()
    parent = read_json(parent_dir / 'report.json')
    if (parent.get('schema') != 'testfix.feabench-native-pilot.v1'
            or parent.get('instance_id') != case['instance_id'] or parent.get('status') != 'failed'):
        raise ValueError('Parent must be a failed native pilot of this exact case')
    catalog.verify_snapshot(parent['skill_catalog'])
    baseline = files_under(Path(case['baseline_root']))
    if _mapping_sha256(baseline) != parent['source_revision']:
        raise ValueError('Parent baseline seal does not match the pinned source')
    candidate = files_under(parent_dir / 'workspace/candidate')
    candidate_hash = _mapping_sha256(candidate)
    if (not parent.get('patches') or candidate_hash != parent['patches'][-1]['candidate_sha256']
            or candidate_hash != parent.get('review', {}).get('candidate_sha256')):
        raise ValueError('Parent candidate differs from its patch/review receipt')
    changed = {n for n in set(candidate) | set(baseline) if candidate.get(n) != baseline.get(n)}
    if not changed <= set(case['allowed_paths']) or set(candidate) != set(baseline):
        raise ValueError('Parent candidate changed outside the runtime allowlist')
    generated = files_under(parent_dir / 'workspace/generated_tests')
    if not generated or generated != parent['generated_tests']['sha256']:
        raise ValueError('Parent generated tests differ from their frozen seal')
    # Verify the original public input, not a new claim with the same case ID.
    requests = sorted((parent_dir / 'model_calls').glob('*/request.json'))
    if len(requests) != parent['model_calls']:
        raise ValueError('Parent model-call records are incomplete')
    original = read_json(requests[0])['context']
    for key in ('instance_id', 'repo_url', 'base_commit'):
        if original.get(key) != case.get(key):
            raise ValueError('Parent public provenance mismatch: ' + key)
    if (original.get('requirements') != [case['requirement']]
            or original.get('scope_manifest', {}).get('allowed_changes') != case['allowed_paths']):
        raise ValueError('Parent public requirement or editable scope changed')
    completed, attempts, outputs, inherited = [], {}, {}, []
    pinned = {str(parent_dir / 'report.json'): sha(parent_dir / 'report.json')}
    for path in requests:
        pinned[str(path)] = sha(path)
    for stage in parent['stages']:
        if stage.get('status') != 'produced':
            raise ValueError('Only produced parent model stages can be inherited')
        skill, attempt = stage['skill_id'], stage['attempt']
        if skill not in SCHEMAS or attempt != attempts.get(skill, 0) + 1:
            raise ValueError('Parent stage order or attempt counter is inconsistent')
        contract = catalog.get_contract(skill)
        declaration = stage['declaration']
        checked = catalog.validate_invocation(skill, 'python_package', contract['reads'],
            contract['writes'], completed, attempt, declaration['timeout_seconds'])
        if checked != declaration:
            raise ValueError('Parent Skill declaration changed')
        path = confined(parent_dir, stage['artifact'], exists=True)
        output = read_json(path)
        _validate_schema(output, PATCH_SCHEMA if skill == 'repair-code' else SCHEMAS[skill])
        outputs[skill] = output
        attempts[skill] = attempt
        if skill not in completed:
            completed.append(skill)
        pinned[str(path)] = sha(path)
        inherited.append({'skill_id': skill, 'attempt': attempt,
                          'artifact': str(path), 'sha256': sha(path), 'fresh_model_call': False})
    if completed != [s for s in CAPABILITY_NAMES if s != 'verify-delivery']:
        raise ValueError('Parent has not produced all eight required model capabilities')
    ids = {case['requirement']['id']}
    matrix = outputs['analyze-requirements']['requirements']
    if len(matrix) != len(ids) or {r['id'] for r in matrix} != ids:
        raise ValueError('Inherited analysis lost the original requirement IDs')
    test_output = outputs['generate-tests']
    if {r['requirement_id'] for r in test_output['coverage']} != ids or not test_output['steps']:
        raise ValueError('Inherited generated tests lost coverage or execution steps')
    texts = validate_tests(test_output)
    actual_texts = {n: confined(parent_dir / 'workspace/generated_tests', n, exists=True).read_text(encoding='utf-8')
                    for n in generated}
    if texts != actual_texts:
        raise ValueError('Generated test artifact no longer matches the frozen files')
    if (not _paired_cases_match(parent['generated_baseline'], parent['generated_candidate'])
            or parent['generated_baseline'].get('total') != 9):
        raise ValueError('Parent paired test identity is incomplete')
    for label in ('generated_baseline', 'generated_candidate'):
        log = Path(parent[label]['stdout_path']).resolve()
        try:
            relative = log.relative_to(parent_dir / 'public-tests').as_posix()
        except ValueError as error:
            raise ValueError('Inherited generated-test log escaped parent public evidence') from error
        confined(parent_dir / 'public-tests', relative, exists=True)
        pinned[str(log)] = sha(log)
    for failure in outputs['diagnose-results']['failures']:
        if failure['requirement_id'] not in ids or not set(failure['target_paths']) <= set(case['allowed_paths']):
            raise ValueError('Inherited diagnosis escaped public scope')
    return {'parent': parent, 'baseline': baseline, 'candidate': candidate,
            'generated': generated, 'completed': completed, 'attempts': attempts,
            'outputs': outputs, 'inherited': inherited, 'pinned': pinned}


def copy_sealed(source, destination, seal):
    destination.mkdir(parents=True)
    for name in seal:
        target = confined(destination, name)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(confined(source, name, exists=True), target)
    if files_under(destination) != seal:
        raise ValueError('Copied continuation snapshot does not match its inherited seal')


def run(case_path, parent_dir, directory, execute_model=False, prior_runs=()):
    case_path, parent_dir, directory = (Path(p).resolve() for p in (case_path, parent_dir, directory))
    if directory.exists():
        raise ValueError('Never overwrite an earlier continuation or pilot')
    case, catalog = validate_case(read_json(case_path)), load_catalog()
    inheritance = validate_parent(case, parent_dir, catalog)
    parent = inheritance['parent']
    history = []
    for path in [*(Path(p).resolve() for p in prior_runs), parent_dir]:
        if str(path) in {h['run'] for h in history}:
            raise ValueError('A historical run cannot be counted twice')
        previous = read_json(path / 'report.json')
        if previous.get('instance_id') != case['instance_id']:
            raise ValueError('Historical accounting is for another case')
        history.append({'run': str(path), 'report_sha256': sha(path / 'report.json'),
                        'model_calls': previous['model_calls'], 'status': previous['status'],
                        'reported_tokens': previous.get('budget', {}).get('reported_tokens'),
                        'duration_seconds': previous.get('duration_seconds')})
    directory.mkdir(parents=True)
    report = {'schema': 'testfix.feabench-native-continuation.v1', 'instance_id': case['instance_id'],
        'status': 'preflight', 'accepted': False, 'model_requested': 'gpt-5.6-sol', 'actual_models': [],
        'setting': parent['setting'], 'adapter_status': parent['adapter_status'],
        'orchestration': 'Resume verified serial Skills; no WCTP employee concurrency',
        'security': parent['security'], 'official_feedback_to_model': False,
        'source_revision': parent['source_revision'], 'skill_catalog': catalog.snapshot(),
        'parent_run': str(parent_dir), 'parent_report_sha256': sha(parent_dir / 'report.json'),
        'case_sha256': sha(case_path), 'inherited_stages': inheritance['inherited'],
        'inherited_attempts': dict(inheritance['attempts']), 'history': history,
        'starting_candidate_sha256': _mapping_sha256(inheritance['candidate']),
        'generated_tests': parent['generated_tests'], 'generated_baseline': parent['generated_baseline'],
        'new_phase_limits': LIMITS, 'max_additional_repair_rounds': 2,
        'budget_policy': 'New separately bounded continuation; earlier failures and usage retained, not pass@1',
        'stages': [], 'model_calls': 0}
    executor_paths = ['scripts/resume_feabench_pilot.py', 'scripts/run_feabench_pilot.py',
                      'scripts/feabench_context.py', 'scripts/prepare_feabench_case.py',
                      'testfix/project_models.py', 'testfix/package_tools.py']
    executor_seal = {str(ROOT / n): sha(ROOT / n) for n in executor_paths}
    for name in executor_paths:
        target = directory / 'executor-sources' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    report['executor_sha256'] = executor_seal
    save(directory / 'report.json', report)
    if not execute_model:
        return report
    candidate, generated = directory / 'workspace/candidate', directory / 'workspace/generated_tests'
    copy_sealed(parent_dir / 'workspace/candidate', candidate, inheritance['candidate'])
    copy_sealed(parent_dir / 'workspace/generated_tests', generated, inheritance['generated'])
    expected = dict(inheritance['candidate'])
    baseline = Path(case['baseline_root'])
    completed, attempts = list(inheritance['completed']), dict(inheritance['attempts'])
    budget = ExecutionBudget(LIMITS)
    provider = ProjectModelProvider('codex_cli', Settings(root=ROOT), model='gpt-5.6-sol',
                                     profile='python_package', artifact_dir=directory / 'model_calls')
    report['status'] = 'running'
    started = time.monotonic()

    def persist():
        calls = provider.snapshot_calls()
        report.update(model_calls=len(calls), budget=budget.report(),
            duration_seconds=round(time.monotonic() - started, 3),
            actual_models=sorted({c['actual_model'] for c in calls if c.get('actual_model')}))
        report['cumulative'] = {
            'model_calls': len(calls) + sum(h['model_calls'] for h in history),
            'reported_tokens': (report['budget']['reported_tokens'] + sum(h['reported_tokens'] for h in history))
                               if all(h['reported_tokens'] is not None for h in history) else None,
            'attempt_setting': 'Earlier failed trials plus this separately budgeted continuation, not pass@1'}
        save(directory / 'report.json', report)

    def integrity():
        if sha(case_path) != report['case_sha256']:
            raise ValueError('Public case changed during continuation')
        for path, digest in {**inheritance['pinned'], **executor_seal}.items():
            if sha(Path(path)) != digest:
                raise ValueError('Pinned parent artifact or executor changed: ' + path)
        if (files_under(baseline) != inheritance['baseline']
                or files_under(parent_dir / 'workspace/candidate') != inheritance['candidate']
                or files_under(parent_dir / 'workspace/generated_tests') != inheritance['generated']
                or files_under(generated) != inheritance['generated']):
            raise ValueError('Baseline, parent candidate, or frozen tests changed')
        if files_under(candidate) != expected:
            raise ValueError('Candidate changed outside the latest host patch receipt')
        catalog.verify_snapshot(report['skill_catalog'])

    def invoke(skill, extra):
        integrity()
        attempt = attempts.get(skill, 0) + 1
        timeout = min(TIMEOUTS[skill], max(1, int(budget.remaining())))
        contract = catalog.get_contract(skill)
        declaration = catalog.validate_invocation(skill, 'python_package', contract['reads'],
            contract['writes'], completed, attempt, timeout)
        context = build_stage_context(case, candidate, skill, extra)
        reservation = budget.reserve(context, CAPS[skill])
        attempts[skill] = attempt
        stage = {'skill_id': skill, 'attempt': attempt, 'status': 'running',
                 'declaration': declaration, 'context_bytes': serialized_bytes(context),
                 'source_selection': context['source_selection'], 'reservation': reservation}
        report['stages'].append(stage)
        persist()
        before = len(provider.snapshot_calls())
        try:
            output = provider.invoke(skill, context, PATCH_SCHEMA if skill == 'repair-code' else SCHEMAS[skill],
                                     timeout_s=timeout, token_cap=CAPS[skill])
        finally:
            if len(provider.snapshot_calls()) > before and provider.last_trace is not None:
                budget.reconcile(provider.last_trace, reservation)
            persist()
        path = directory / 'capabilities' / f'{skill}-{attempt}.json'
        save(path, output)
        stage.update(status='produced', artifact=str(path.relative_to(directory)), sha256=sha(path))
        if skill not in completed:
            completed.append(skill)
        integrity()
        persist()
        return output

    try:
        analysis = inheritance['outputs']['analyze-requirements']
        test_output = inheritance['outputs']['generate-tests']
        diagnosis = inheritance['outputs']['diagnose-results']
        # This is generated-test feedback only; do not load prior official logs.
        feedback = suite_feedback(parent['generated_candidate'])
        feedback['review_findings'] = parent['review']['findings']
        approved, last_suite = False, None
        for round_number in (1, 2):
            patch = invoke('repair-code', {'requirement_matrix': analysis, 'diagnosis_report': diagnosis,
                'generated_tests': test_output['files'], 'public_test_results': feedback,
                'business_round': round_number, 'patch_format_instructions': (
                    'Continue the failed current candidate using bounded edits, never full replacement files. '
                    'baseline_sha256 must equal the supplied candidate_sha256. Source texts are complete-line '
                    'excerpts with gaps, not complete files; old must be contiguous exact current text matching '
                    'once, without excerpt gap markers. Keep old API and all frozen tests unchanged. '
                    'Use only allowlisted runtime files. Parser registry changes may add deferred lambda '
                    'callbacks while preserving all existing entries; no eager import-time code is allowed.')})
            try:
                receipt = patch_candidate(patch, candidate, baseline, set(case['allowed_paths']), _mapping_sha256(expected))
            except (ValueError, SyntaxError) as error:
                report.setdefault('rejected_patches', []).append({'round': round_number, 'reason': str(error)})
                feedback['patch_rejection'] = {'reason': str(error), 'instruction': 'Correct this rejected proposal; it was not applied.'}
                integrity()
                persist()
                continue
            expected = files_under(candidate)
            if receipt['candidate_sha256'] != _mapping_sha256(expected):
                raise ValueError('New patch receipt is not bound to the exact candidate')
            report.setdefault('patches', []).append(receipt)
            (directory / f'continuation-round-{round_number}.patch').write_text(
                _diff(parent_dir / 'workspace/candidate', candidate, set(receipt['changed_files'])), encoding='utf-8')
            integrity()
            last_suite = run_package_suite(Path(case['python']), candidate, generated, directory / 'public-tests',
                f'candidate-generated-{round_number}', {'source_root': '.', 'package': 'sqlglot'})
            report['generated_candidate'] = last_suite
            integrity()
            feedback = suite_feedback(last_suite)
            review = invoke('review-patch', {'requirement_matrix': analysis, 'diagnosis_report': diagnosis,
                'generated_tests': test_output['files'], 'patch_artifact': receipt,
                'public_test_results': feedback, 'business_round': round_number})
            if review['candidate_sha256'] != receipt['candidate_sha256']:
                raise ValueError('Review refers to a stale candidate')
            approved = review['approved'] and not any(f['severity'] == 'high' for f in review['findings'])
            report['review'] = review
            persist()
            if approved and last_suite['successful'] and _paired_cases_match(parent['generated_baseline'], last_suite):
                break
            feedback['review_findings'] = review['findings']
            if round_number < 2:
                diagnosis = invoke('diagnose-results', {'requirement_matrix': analysis,
                    'generated_tests': test_output['files'], 'public_test_results': feedback})
                for f in diagnosis['failures']:
                    if f['requirement_id'] != case['requirement']['id'] or not set(f['target_paths']) <= set(case['allowed_paths']):
                        raise ValueError('New diagnosis escaped scope')
        # Freeze model activity here; official results cannot trigger repair.
        integrity()
        candidate_hash = _mapping_sha256(expected)
        report['model_phase_ended'] = True
        if not report.get('patches') or candidate_hash != report['patches'][-1]['candidate_sha256']:
            raise ValueError('Continuation produced no accepted host patch receipt')
        contract = catalog.get_contract('verify-delivery')
        declaration = catalog.validate_invocation('verify-delivery', 'python_package', contract['reads'],
            contract['writes'], completed, 1, min(240, max(1, int(budget.remaining()))))
        output = directory / 'official-evaluation'
        command = [arg.replace('{candidate}', str(candidate)).replace('{output}', str(output))
                   for arg in case['evaluation']['command']]
        process = _run_bounded(command, cwd=ROOT, timeout_s=min(240, budget.remaining()))
        save(directory / 'judge-process.json', {**process, 'command': command})
        official = read_json(output / 'evaluation.json') if (output / 'evaluation.json').is_file() else {}
        report['official_evaluation'] = official
        report['official_process'] = {k: process[k] for k in ('exit_code', 'timeout', 'output_limit_exceeded', 'duration_s')}
        integrity()
        if official.get('candidate_tree_sha256') != candidate_hash:
            raise ValueError('Independent judge is not bound to the final candidate')
        report['stages'].append({'skill_id': 'verify-delivery', 'attempt': 1, 'status': 'executed',
            'declaration': declaration, 'artifact': 'official-evaluation/evaluation.json'})
        before, after = official.get('baseline_regression', {}), official.get('candidate_regression', {})
        verdict = {
            'generated_tests_pass': bool(last_suite and last_suite['successful'] and _paired_cases_match(parent['generated_baseline'], last_suite)),
            'review_approved': bool(approved), 'official_resolved': official.get('resolved') is True,
            'official_target_stable': official.get('target_stable') is True,
            'official_baseline_exposes_gap': official.get('baseline_official', {}).get('failed', 0) > 0 and official.get('baseline_official', {}).get('errors', 1) == 0,
            'official_candidate_pass': official.get('candidate_official', {}).get('all_passed') is True,
            'regression_preserved': before.get('all_passed') is True and after.get('all_passed') is True
                 and bool(before.get('test_case_ids_sha256')) and before.get('test_case_ids_sha256') == after.get('test_case_ids_sha256'),
            'protected_paths_preserved': official.get('protected_file_violations') == [],
            'judge_completed': process['exit_code'] == 0 and not process['timeout'] and not process['output_limit_exceeded'],
            'integrity': True}
        report.update(verdict=verdict, accepted=all(v is True for v in verdict.values()), candidate_sha256=candidate_hash)
        report['status'] = 'completed' if report['accepted'] else 'failed'
        (directory / 'candidate.patch').write_bytes(_diff(baseline, candidate, set(case['allowed_paths'])).encode('utf-8'))
    except Exception as error:
        report.update(status='failed', accepted=False, error={'type': type(error).__name__, 'message': str(error)})
    finally:
        persist()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', required=True, type=Path)
    parser.add_argument('--parent-run', required=True, type=Path)
    parser.add_argument('--run-dir', required=True, type=Path)
    parser.add_argument('--prior-run', action='append', default=[], type=Path)
    parser.add_argument('--execute-model', action='store_true')
    args = parser.parse_args()
    report = run(args.case, args.parent_run, args.run_dir, args.execute_model, args.prior_run)
    print(json.dumps({k: report.get(k) for k in ('status', 'accepted', 'model_calls', 'actual_models', 'cumulative', 'error')}, ensure_ascii=False))
    return 0 if report['status'] in {'completed', 'preflight'} else 1


if __name__ == '__main__':
    raise SystemExit(main())
