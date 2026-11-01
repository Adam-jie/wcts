"""A native FEA-Bench pilot, deliberately separate from the web/CLI adapters.

Without --execute-model this command only validates public inputs and prepares
an integer plan. Models see selected public baseline source and their own tests;
the official judge runs once, after model work has ended. This is not the
benchmark's Docker harness, a WCTP employee run, or an OS security sandbox.
"""
from __future__ import annotations

import argparse
import ast
from copy import deepcopy
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from testfix.config import Settings
from testfix.package_tools import _run_bounded, run_package_suite
from testfix.project_eval import _diff, _functional_failure, _mapping_sha256, _paired_cases_match
from testfix.project_models import ProjectModelProvider
from testfix.project_tools import confined, files_under, relative_path
from testfix.project_workflow import CAPABILITY_NAMES, SCHEMAS, ExecutionBudget
from testfix.scheduling import solve_plan
from testfix.skills import load_catalog
from scripts.feabench_context import (build_stage_context, conservative_stage_reservation,
                                      serialized_bytes)

SCHEMA = 'testfix.feabench-native-pilot.v1'
SAFE_IMPORTS = {'pytest', 'sqlglot', 're', 'math', 'decimal', 'fractions',
                'itertools', 'functools', 'collections', 'datetime', 'operator',
                'typing', 'copy', 'enum', 'json', 'string'}
DANGEROUS_IMPORTS = {'os', 'sys', 'pathlib', 'subprocess', 'socket', 'requests',
                     'httpx', 'urllib', 'http', 'ctypes', 'importlib', 'runpy',
                     'shutil', 'tempfile', 'inspect', 'pickle', 'marshal',
                     'builtins', 'io', 'multiprocessing', 'threading'}
DANGEROUS_CALLS = {'open', 'exec', 'eval', 'compile', '__import__', 'globals',
                   'locals', 'vars', 'getattr', 'setattr', 'delattr', 'dir',
                   'breakpoint', 'input', 'help', 'memoryview'}
DANGEROUS_ATTRS = {'system', 'popen', 'spawn', 'exec', 'execve', 'fork',
                   'read_text', 'read_bytes', 'write_text', 'write_bytes',
                   'open', 'connect', 'urlopen', 'request', 'chdir', 'insert',
                   'load', 'dump', 'unlink', 'remove',
                   'rmdir', 'mkdir', 'makedirs', 'getenv', 'environ',
                   'getsource', 'getfile', 'getsourcelines', 'getmodule', 'getmembers'}
TIMEOUTS = {'plan-workflow':60, 'explore-repository':60, 'ground-code':90,
            'analyze-requirements':90, 'generate-tests':150,
            'diagnose-results':90, 'repair-code':150, 'review-patch':90}
CAPS = {'plan-workflow':900, 'explore-repository':1200, 'ground-code':1800,
        'analyze-requirements':2200, 'generate-tests':6500,
        'diagnose-results':2200, 'repair-code':12000, 'review-patch':2600}
PATCH_SCHEMA = {'type':'object', 'properties':{
    'baseline_sha256':{'type':'string'}, 'reason':{'type':'string'},
    'edits':{'type':'array','minItems':1,'maxItems':12,'items':{
        'type':'object','properties':{'path':{'type':'string'},
                                    'old':{'type':'string','minLength':1,'maxLength':50000},
                                    'new':{'type':'string','maxLength':100000}},
        'required':['path','old','new'],'additionalProperties':False}}},
    'required':['baseline_sha256','edits','reason'],'additionalProperties':False}


def save(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_case(case: dict) -> dict:
    """Validate trusted host paths without ever merging this object into prompts."""
    if case.get('schema') != 'testfix.feabench-pilot-case.v1':
        raise ValueError('Unsupported public case schema')
    case = dict(case)
    baseline = Path(case.get('baseline_root', case.get('baseline_dir', ''))).resolve()
    python = Path(case.get('python', case.get('package_interpreter', ''))).resolve()
    if not baseline.is_dir() or not python.is_file():
        raise ValueError('Pinned baseline directory or interpreter is unavailable')
    allowed = case.get('allowed_paths')
    if not isinstance(allowed, list) or not allowed or len(allowed) != len(set(allowed)):
        raise ValueError('Case requires a nonempty unique runtime allowlist')
    for name in allowed:
        if not isinstance(name, str) or not name.startswith('sqlglot/') or not name.endswith('.py'):
            raise ValueError('Pilot accepts existing sqlglot runtime Python files only')
        confined(baseline, name, exists=True)
    if case.get('package', 'sqlglot') != 'sqlglot':
        raise ValueError('This first native pilot is restricted to sqlglot')
    requirement = case.get('requirement')
    if not isinstance(requirement, dict):
        requirement = {'id':case['instance_id'], 'description':case['problem_statement']}
    if not all(isinstance(requirement.get(k), str) and requirement[k].strip() for k in ('id','description')):
        raise ValueError('A public requirement ID and description are required')
    if len(requirement['description']) > 30_000:
        raise ValueError('Public requirement exceeds the pilot input limit')
    problem_statement = case.get('problem_statement', requirement['description'])
    if not isinstance(problem_statement, str) or not problem_statement.strip() or len(problem_statement) > 30_000:
        raise ValueError('A bounded public problem statement is required')
    hints_text = case.get('hints_text', '')
    if not isinstance(hints_text, str) or len(hints_text) > 30_000:
        raise ValueError('Public task hints must be bounded text')
    context = case.get('context_paths', [])
    if not isinstance(context, list) or any(name not in allowed for name in context):
        raise ValueError('Context files must come from the public runtime allowlist')
    evaluation = case.get('evaluation', {})
    if (not isinstance(evaluation.get('command'), list) or not evaluation['command']
            or any(not isinstance(x, str) or '\x00' in x for x in evaluation['command'])
            or not any('{candidate}' in x for x in evaluation['command'])
            or not any('{output}' in x for x in evaluation['command'])):
        raise ValueError('A fixed trusted evaluator argv with candidate/output placeholders is required')
    case.update(baseline_root=str(baseline), python=str(python), requirement=requirement,
                problem_statement=problem_statement, hints_text=hints_text,
                package='sqlglot')
    return case


def selected_sources(case: dict, candidate: Path) -> dict[str, str]:
    """Compatibility accessor: public complete-line excerpts, never full files."""
    return build_stage_context(case, candidate, 'ground-code')['sources']


def public_context(case: dict, candidate: Path) -> dict:
    return build_stage_context(case, candidate, 'ground-code')


def _imports(tree: ast.AST) -> set[str]:
    result = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(alias.name.split('.')[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            result.add((node.module or '').split('.')[0])
    return result


def validate_tests(output: dict) -> dict[str, str]:
    files, result, total = output.get('files'), {}, 0
    if not isinstance(files, list) or not 1 <= len(files) <= 8:
        raise ValueError('Tester must return 1–8 source files')
    for item in files:
        name, text = item['path'], item['source']
        relative_path(name)
        if '/' in name or not name.endswith('.py') or not (name.startswith('test_') or name == 'conftest.py'):
            raise ValueError('Generated tests require bare test_*.py/conftest.py names')
        if not isinstance(text, str) or not text.strip():
            raise ValueError('Empty generated test: ' + name)
        if name in result:
            if result[name] == text:
                continue
            raise ValueError('Duplicate generated test has different contents: ' + name)
        tree = ast.parse(text)
        if not _imports(tree) <= SAFE_IMPORTS:
            raise ValueError('Generated tests imported a non-allowlisted module')
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import,ast.ImportFrom)):
                if isinstance(node,ast.ImportFrom) and node.level:
                    raise ValueError('Generated tests cannot import relative files')
                for alias in node.names:
                    if (any(p.startswith('_') or p in DANGEROUS_IMPORTS or p in DANGEROUS_CALLS for p in alias.name.split('.'))
                            or alias.name=='*' or alias.asname and alias.asname.startswith('__')):
                        raise ValueError('Generated tests cannot import private names or IO module aliases')
            if isinstance(node, ast.Attribute) and (node.attr.startswith('__') or node.attr in DANGEROUS_ATTRS or node.attr in DANGEROUS_IMPORTS):
                raise ValueError('Generated tests contain an IO/reflection attribute')
            if isinstance(node, ast.Name) and (node.id in DANGEROUS_CALLS or node.id in {'__builtins__','__loader__','__spec__'}):
                raise ValueError('Generated tests contain dynamic execution/reflection')
            if isinstance(node,ast.Call) and isinstance(node.func,(ast.Subscript,ast.Call,ast.Lambda)):
                raise ValueError('Generated tests cannot execute indirect/subscript callables')
        total += len(text.encode('utf-8'))
        if total > 100_000:
            raise ValueError('Generated tests exceed 100 KB')
        result[name] = text
    if not any(name.startswith('test_') for name in result):
        raise ValueError('No generated test module')
    return result


def _pure_data(node: ast.AST) -> bool:
    return all(isinstance(child,(ast.Constant,ast.Name,ast.Load,ast.Attribute,ast.Dict,
                                 ast.List,ast.Tuple,ast.Set,ast.UnaryOp,ast.USub,ast.UAdd))
               and not (isinstance(child,ast.Attribute) and child.attr.startswith('__'))
               for child in ast.walk(node))


def _data_assignment(node: ast.AST) -> bool:
    if isinstance(node,ast.Assign):
        return len(node.targets)==1 and isinstance(node.targets[0],ast.Name) and _pure_data(node.value)
    if isinstance(node,ast.AnnAssign):
        return (isinstance(node.target,ast.Name) and node.value is not None
                and _pure_data(node.value) and _pure_data(node.annotation))
    return False


def safe_registry_extension(old: ast.AST, new: ast.AST) -> bool:
    """Allow additive, delayed parser registrations, preserving old entries.

    No lambda defaults/annotations, eager execution, registry rewrites, dict
    expansion, or nested calls are accepted by this deliberately narrow rule.
    """
    if not all(isinstance(n,ast.Assign) and len(n.targets)==1
               and isinstance(n.targets[0],ast.Name) and isinstance(n.value,ast.Dict)
               for n in (old,new)):
        return False
    if old.targets[0].id != new.targets[0].id:
        return False
    old_items, new_items = old.value, new.value
    if any(k is None for k in [*old_items.keys,*new_items.keys]):
        return False
    old_keys, new_keys = [ast.dump(k) for k in old_items.keys], [ast.dump(k) for k in new_items.keys]
    if len(old_keys)!=len(set(old_keys)) or len(new_keys)!=len(set(new_keys)):
        return False
    previous = dict(zip(old_keys,old_items.values))
    current = dict(zip(new_keys,new_items.values))
    if [key for key in new_keys if key in previous] != old_keys:
        return False
    if not previous or not set(previous) < set(current):
        return False
    if any(ast.dump(value)!=ast.dump(current[key]) for key,value in previous.items()):
        return False
    # The existing registry must already contain delayed parser callbacks.
    if not all(isinstance(v,ast.Lambda) for v in previous.values()):
        return False
    def public_attribute(node, roots):
        return (isinstance(node,ast.Attribute) and isinstance(node.value,ast.Name)
                and node.value.id in roots and not node.attr.startswith('_'))
    def pure_argument(node):
        if isinstance(node,ast.Constant):
            return True
        if public_attribute(node, {'exp','TokenType'}):
            return True
        if isinstance(node,(ast.Tuple,ast.List,ast.Set)):
            return all(pure_argument(n) for n in node.elts)
        if isinstance(node,ast.Dict):
            return all(k is not None and pure_argument(k) and pure_argument(v)
                       for k,v in zip(node.keys,node.values))
        return False
    for key,value in zip(new_items.keys,new_items.values):
        if ast.dump(key) in previous:
            continue
        if not public_attribute(key, {'exp'}) or not isinstance(value,ast.Lambda):
            return False
        args=value.args
        if (len(args.args)!=1 or args.args[0].arg!='self' or args.args[0].annotation is not None
                or args.posonlyargs or args.kwonlyargs or args.vararg or args.kwarg
                or args.defaults or args.kw_defaults):
            return False
        body=value.body
        if not (isinstance(body,ast.Call) and isinstance(body.func,ast.Attribute)
                and isinstance(body.func.value,ast.Name) and body.func.value.id=='self'
                and body.func.attr.startswith('_parse_') and not body.func.attr.startswith('__')):
            return False
        if not all(pure_argument(n) for n in body.args):
            return False
        if not all(k.arg is not None and pure_argument(k.value) for k in body.keywords):
            return False
    return True


def validate_import_execution(old: ast.AST, new: ast.AST) -> None:
    """Keep existing import-time effects; permit ordinary new builder methods/data.

    This is a deliberately narrow pilot policy, not a proof that Python is safe.
    Function bodies remain editable; top-level/class statements cannot turn into
    new execution paths and existing headers cannot acquire new decorators.
    """
    functions = (ast.FunctionDef,ast.AsyncFunctionDef)
    def header(node):
        node = deepcopy(node)
        node.body = []
        return ast.dump(node)
    def check(before,after):
        old_defs = {n.name:n for n in before if isinstance(n,(*functions,ast.ClassDef))}
        new_defs = {n.name:n for n in after if isinstance(n,(*functions,ast.ClassDef))}
        if not set(old_defs) <= set(new_defs):
            raise ValueError('Patch cannot remove existing public classes/functions')
        for name,node in new_defs.items():
            previous = old_defs.get(name)
            if previous is not None and header(previous)!=header(node):
                raise ValueError('Patch changed an existing import-time definition header')
            if previous is None:
                signature = deepcopy(node)
                signature.body=[]
                if any(isinstance(x,(ast.Call,ast.Subscript,ast.Lambda,ast.NamedExpr)) for x in ast.walk(signature)):
                    raise ValueError('New definitions cannot execute expressions in headers/decorators')
            if isinstance(node,ast.ClassDef):
                check(previous.body if isinstance(previous,ast.ClassDef) else [],node.body)
        ordinary = lambda items:[n for n in items if not isinstance(n,(*functions,ast.ClassDef))]
        remaining = [ast.dump(n) for n in ordinary(after)]
        for node in ordinary(before):
            dumped=ast.dump(node)
            if dumped in remaining:
                remaining.remove(dumped)
                continue
            if isinstance(node,(ast.Assign,ast.AnnAssign)):
                target=ast.dump(node.targets[0] if isinstance(node,ast.Assign) and len(node.targets)==1 else
                                node.target if isinstance(node,ast.AnnAssign) else ast.Tuple(elts=node.targets,ctx=ast.Store()))
                matches=[n for n in ordinary(after) if isinstance(n,type(node)) and
                         ast.dump(n.targets[0] if isinstance(n,ast.Assign) and len(n.targets)==1 else
                                  n.target if isinstance(n,ast.AnnAssign) else ast.Tuple(elts=n.targets,ctx=ast.Store()))==target]
                if len(matches)==1 and ((_data_assignment(node) and _data_assignment(matches[0]))
                                        or safe_registry_extension(node,matches[0])):
                    remaining.remove(ast.dump(matches[0]))
                    continue
            raise ValueError('Patch changed an existing top-level/class execution statement')
        for node in ordinary(after):
            if ast.dump(node) not in remaining:
                continue
            if _data_assignment(node):
                continue
            if isinstance(node,ast.Expr) and isinstance(node.value,ast.Constant) and isinstance(node.value.value,str):
                continue
            raise ValueError('Patch introduced a top-level/class execution statement')
    check(old.body,new.body)


def patch_candidate(output: dict, candidate: Path, baseline: Path,
                    allowed: set[str], expected_hash: str, *, host_full_files=False) -> dict:
    current = files_under(candidate)
    if output.get('baseline_sha256') != expected_hash or _mapping_sha256(current) != expected_hash:
        raise ValueError('Stale candidate SHA in model patch')
    edits, texts, total = output.get('edits'), {}, 0
    if not isinstance(edits, list) or not 1 <= len(edits) <= 12:
        raise ValueError('Repair requires 1–12 unique-context source edits')
    for item in edits:
        name, old, new = item['path'], item['old'], item['new']
        if name not in allowed or not isinstance(old,str) or not old or not isinstance(new,str):
            raise ValueError('Unauthorized path or empty edit match')
        if len(old) > (800_000 if host_full_files else 50_000) or len(new) > (800_000 if host_full_files else 100_000):
            raise ValueError('Edit exceeds bounded text limits')
        target = confined(candidate,name,exists=True)
        text = texts.get(name,target.read_text(encoding='utf-8'))
        if text.count(old) != 1:
            raise ValueError('Edit old text must match exactly once in the current source')
        texts[name] = text.replace(old,new,1)
    pending = {}
    for name,text in texts.items():
        target = confined(candidate,name,exists=True)
        if not text.strip():
            raise ValueError('Patch cannot empty a runtime file')
        old_tree, tree = ast.parse(target.read_text(encoding='utf-8')), ast.parse(text)
        validate_import_execution(old_tree,tree)
        old_imports={ast.dump(n) for n in ast.walk(old_tree) if isinstance(n,(ast.Import,ast.ImportFrom))}
        old_names={ast.dump(n) for n in ast.walk(old_tree) if isinstance(n,ast.Name)}
        old_attrs={ast.dump(n) for n in ast.walk(old_tree) if isinstance(n,ast.Attribute)}
        old_calls={ast.dump(n) for n in ast.walk(old_tree) if isinstance(n,ast.Call)}
        dangerous_aliases=set(DANGEROUS_IMPORTS)
        for node in ast.walk(old_tree):
            if isinstance(node,ast.Import):
                dangerous_aliases.update(a.asname or a.name.split('.')[0] for a in node.names if a.name.split('.')[0] in DANGEROUS_IMPORTS)
            elif isinstance(node,ast.ImportFrom) and (node.module or '').split('.')[0] in DANGEROUS_IMPORTS:
                dangerous_aliases.update(a.asname or a.name for a in node.names)
        sensitive=dangerous_aliases|DANGEROUS_CALLS|{'__builtins__','__loader__','__spec__'}
        old_sensitive=Counter(n.id for n in ast.walk(old_tree) if isinstance(n,ast.Name) and n.id in sensitive)
        new_sensitive=Counter(n.id for n in ast.walk(tree) if isinstance(n,ast.Name) and n.id in sensitive)
        if new_sensitive-old_sensitive:
            raise ValueError('Patch introduced additional IO/reflection name use')
        for node in ast.walk(tree):
            if isinstance(node,(ast.Import,ast.ImportFrom)) and ast.dump(node) not in old_imports:
                raise ValueError('Pilot patches cannot introduce or alias new imports')
            if isinstance(node,ast.Attribute) and node.attr.startswith('__'):
                if ast.dump(node) not in old_attrs:
                    raise ValueError('Patch introduced a private reflection attribute')
            if isinstance(node,ast.Attribute):
                base=node.value
                while isinstance(base,ast.Attribute):
                    base=base.value
                if isinstance(base,ast.Name) and base.id in sensitive and ast.dump(node) not in old_attrs:
                    raise ValueError('Patch introduced an access through an existing IO/inspection import')
            if isinstance(node,ast.Name) and (node.id in DANGEROUS_CALLS or node.id in DANGEROUS_IMPORTS
                                             or node.id in {'__builtins__','__loader__','__spec__'}):
                if ast.dump(node) not in old_names:
                    raise ValueError('Patch introduced a dangerous IO/reflection name')
            if isinstance(node,ast.Call) and isinstance(node.func,(ast.Subscript,ast.Call,ast.Lambda)):
                if ast.dump(node) not in old_calls:
                    raise ValueError('Patch introduced an indirect/subscript callable')
        old_danger = {ast.dump(n) for n in ast.walk(old_tree) if isinstance(n, ast.Call)
                      and ((isinstance(n.func, ast.Name) and n.func.id in DANGEROUS_CALLS)
                           or (isinstance(n.func, ast.Attribute) and n.func.attr in DANGEROUS_ATTRS))}
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and (
                isinstance(node.func, ast.Name) and node.func.id in DANGEROUS_CALLS
                or isinstance(node.func, ast.Attribute) and node.func.attr in DANGEROUS_ATTRS
            ) and ast.dump(node) not in old_danger:
                raise ValueError('Patch introduced a dangerous IO/reflection call')
        total += len(text.encode('utf-8'))
        if total > 800_000:
            raise ValueError('Edited runtime files exceed 800 KB')
        pending[name] = (target, text)
    for target, text in pending.values():
        target.write_text(text, encoding='utf-8', newline='')
    after = files_under(candidate)
    original = files_under(baseline)
    changed = sorted(name for name in set(original) | set(after) if original.get(name) != after.get(name))
    if not set(changed) <= allowed:
        raise ValueError('Candidate changed beyond the runtime allowlist')
    return {'candidate_sha256':_mapping_sha256(after), 'changed_files':changed,
            'hashes':{name:after[name] for name in changed}}


def explore_with_correction(invoke, allowed_paths):
    extra = {}
    for attempt in (1, 2):
        output = invoke('explore-repository', extra)
        if output['candidates'] and all(item['path'] in allowed_paths for item in output['candidates']):
            return output
        if attempt == 2:
            raise ValueError('Explorer must locate public allowlisted files')
        extra = {'previous_output': output, 'validation_feedback': {
            'error': 'Each search candidate must name one existing file from source_index.',
            'instruction': 'Replace directory, combined or nonexistent paths with relevant exact file paths. '
                           'Retain useful queries and report uncertainty rather than inventing paths.'}}


def ground_with_correction(invoke, candidates, requirement_ids, allowed_paths):
    extra = {'search_candidates':candidates}
    for attempt in (1, 2):
        output = invoke('ground-code', extra)
        if output['locations'] and all(item['path'] in allowed_paths and item['requirement_id'] in requirement_ids
                                       for item in output['locations']):
            return output
        if attempt == 2:
            raise ValueError('Grounding returned invalid paths/requirements')
        extra = {'search_candidates':candidates, 'previous_output':output, 'validation_feedback':{
            'error':'Each location must contain exactly one allowed path and one registered requirement ID.',
            'instruction':'Return separate location entries for separate files; use exact allowed paths and IDs.'}}


def plan_team_with_correction(invoke, context, requirement_ids, allowed_paths):
    from testfix.team_planning import TeamPlanError, team_plan_schema, validate_team_plan
    extra = context
    for attempt in (1, 2):
        output = invoke('plan-workflow', extra, team_plan_schema())
        try:
            return validate_team_plan(output, requirement_ids, allowed_paths, allowed_paths)
        except TeamPlanError as error:
            if attempt == 2:
                raise
            extra = {**context, 'previous_output':output, 'validation_feedback':{'error':str(error),
                'instruction':'Correct the plan only if the request is supported. Independent tests, review, '
                'and delivery run after employee handoffs; do not claim they already passed. Keep genuinely '
                'uncovered requirements or questions when they cannot be resolved.'}}


def analyze_with_id_correction(invoke, code_locations: list, requirement_ids: set[str]) -> dict:
    """One model correction for an invalid requirement map; never rewrite IDs.

    The normal invoke function stores both full requests and raw stage outputs.
    A correction is a real second Skill invocation subject to the same budget.
    """
    extra={'code_locations':code_locations}
    for attempt in (1,2):
        output=invoke('analyze-requirements',extra)
        actual=[row['id'] for row in output['requirements']]
        if len(actual)==len(requirement_ids) and set(actual)==requirement_ids:
            return output
        if attempt==2:
            raise ValueError('Requirement analysis must preserve every original requirement ID exactly once after one correction')
        extra={'code_locations':code_locations,'previous_output':output,
               'validation_feedback':{
                   'code':'requirement_ids_mismatch','permitted_ids':sorted(requirement_ids),
                   'observed_ids':actual,
                   'instruction':(
                       'Return a corrected complete analysis using exactly one row per permitted original ID. '
                       'Do not create child/prefixed/suffixed IDs or duplicate rows. Combine subcriteria in '
                       'the behavior/evidence/criterion strings of the original row. The host will not '
                       'rename IDs or relax the ID contract. Preserve the required output schema.')}}
    raise AssertionError('Unreachable bounded analysis correction')


def generate_valid_tests(invoke, analysis, locations, requirement_ids, *, execution_feedback=None, previous_output=None):
    guidance = ('Use public requirements and observed existing behavior as the basis for each assertion. '
                'Do not invent exact formatting, dialect, input conversion, or error behavior that the request '
                'does not specify. In particular, do not assume a convenience method exists on a test input '
                'unless the available repository source or an existing example establishes it; use simple '
                'documented inputs if uncertain. Do not assert the relative ordering of existing and new '
                'output clauses unless the public specification or existing source establishes that order. '
                'Record uncertain expectations in assumptions rather than asserting them. '
                'Keep explicit requirement examples and existing regression checks.')
    extra = {'requirement_matrix':analysis, 'code_locations':locations, 'test_design_instructions':guidance}
    if execution_feedback is not None:
        extra.update(previous_output=previous_output, validation_feedback={
            'error':'Generated tests failed during collection on the baseline.',
            'instruction':'Fix test imports or collection using the observed public error. '
                          'Preserve requirement coverage and do not weaken assertions to pass.',
            'public_test_results':execution_feedback})
    for attempt in (1, 2):
        output = invoke('generate-tests', extra)
        try:
            if {x['requirement_id'] for x in output['coverage']} != requirement_ids or not output['steps']:
                raise ValueError('Generated tests require execution steps and requirement coverage')
            return output, validate_tests(output)
        except (ValueError, SyntaxError) as error:
            if attempt == 2:
                raise
            extra = {'requirement_matrix':analysis, 'code_locations':locations,
                     'test_design_instructions':guidance,
                     'previous_output':output, 'validation_feedback':{
                         'error':str(error), 'instruction':'Correct the test output. Put executable Python tests '
                         'only in bare test_*.py or conftest.py files; describe plans in steps, not extra files. '
                         'Preserve the requirement IDs and the required output schema.'}}


def diagnosis_paths(output, requirement_ids, allowed_paths):
    result = deepcopy(output)
    normalized = []
    for failure in result['failures']:
        if failure['requirement_id'] not in requirement_ids:
            raise ValueError('Diagnosis proposed an unknown requirement')
        paths = []
        for target in failure['target_paths']:
            match = re.fullmatch(r'(.+):(\d+)(?:-(\d+))?', target)
            path = match.group(1) if match and match.group(1) in allowed_paths else target
            if path not in allowed_paths:
                raise ValueError('Diagnosis proposed unauthorized targets')
            if path != target:
                normalized.append({'reported':target, 'path':path})
            if path not in paths:
                paths.append(path)
        failure['target_paths'] = paths
    return result, normalized


def make_plan(catalog, source_bytes: int) -> dict:
    options = []
    for skill_id in CAPABILITY_NAMES:
        llm = skill_id != 'verify-delivery'
        repeats = 2 if skill_id in {'analyze-requirements','diagnose-results','repair-code','review-patch'} else 1
        seconds = TIMEOUTS.get(skill_id, 240) * repeats
        if skill_id == 'generate-tests':
            seconds += 60
        if skill_id == 'repair-code':
            seconds += 120
        # These stages actually receive no full sources or first-30-line
        # summaries, respectively; do not charge them a full-source forecast.
        forecast = conservative_stage_reservation(skill_id, CAPS[skill_id]) * repeats if llm else 0
        options.append({'skill_id':skill_id, 'id':'native-pilot', 'duration_ms':seconds*1000,
                        'token_cap':forecast,
                        'resources':{'model_slots':int(llm), 'cpu_slots':int(not llm),
                                     'candidate_write':int(skill_id == 'repair-code')},
                        'evidence':{'basis':'Configured cumulative time limits and conservative serialized-context UTF-8 byte capacity + 12000 provider overhead + output cap, multiplied by invocation allowance; same byte basis as runtime reserve, not measured tokenization/performance'}})
    return solve_plan(options, {'max_makespan_ms':1_800_000, 'max_tokens':800_000,
                               'resources':{'model_slots':1,'cpu_slots':1,'candidate_write':1},
                               'solver_time_limit_seconds':5}, profile='python_package', catalog=catalog)


def suite_feedback(suite: dict) -> dict:
    """Only caller-generated tests are eligible to produce model feedback."""
    return {**{key:suite.get(key) for key in ('passed','total','successful','case_ids','collection_errors',
                                             'parse_error','binding_error','timeout','exit_code')},
            'output_tail':Path(suite['stdout_path']).read_text(encoding='utf-8', errors='replace')[-7000:]}


def run(case_path: Path, directory: Path, execute_model: bool, model: str = 'gpt-5.6-sol', *, team: bool = False) -> dict:
    if directory.exists():
        raise ValueError('Never overwrite a previous pilot directory')
    case = validate_case(json.loads(case_path.read_text(encoding='utf-8')))
    baseline = Path(case['baseline_root'])
    baseline_seal = files_under(baseline)
    sources = selected_sources(case, baseline)
    catalog = load_catalog()
    plan = make_plan(catalog, sum(len(x.encode('utf-8')) for x in sources.values()))
    directory.mkdir(parents=True)
    report = {'schema':SCHEMA, 'instance_id':case['instance_id'],
              'setting':case.get('setting','FEA-Bench public PR-text-only native Windows preexperiment; not official Standard/Docker evaluation'),
              'adapter_status':'Standalone native pilot; not connected to web project adapter',
              'orchestration':'Eight serial LLM Skills and deterministic final judge; no WCTP employee concurrency',
              'status':'preflight', 'model_requested':model, 'actual_models':[],
              'source_revision':_mapping_sha256(baseline_seal),
              'skill_catalog':catalog.snapshot(), 'plan':plan, 'stages':[],
              'model_calls':0, 'official_feedback_to_model':False,
              'snapshot_paths':list(sources), 'snapshot_bytes':sum(len(x.encode('utf-8')) for x in sources.values()),
              'security':'Snapshot-only model with disabled tools; local trusted Python subprocesses, not an OS sandbox'}
    if team:
        report['orchestration'] = 'WCTP employee graph, CP-SAT assignment and CodeAgent handoff review; native pilot'
    save(directory/'report.json', report)
    if plan['status'] not in {'OPTIMAL','FEASIBLE'}:
        raise ValueError('Pilot integer plan is not executable: ' + plan['status'])
    if not execute_model:
        return report

    work = directory/'workspace'
    candidate = work/'candidate'
    candidate.mkdir(parents=True)
    for name in baseline_seal:
        target = confined(candidate, name)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(confined(baseline, name, exists=True), target)
    generated = work/'generated_tests'
    generated.mkdir()
    budget = ExecutionBudget({'wall_seconds':1800,'token_limit':800_000,'model_slots':1,'cpu_slots':1})
    provider = ProjectModelProvider('codex_cli', Settings(root=ROOT), model=model,
                                    profile='python_package', artifact_dir=directory/'model_calls')
    completed, attempts, results = [], {}, {}
    report['status'] = 'running'
    start = time.monotonic()
    generated_seal = None
    expected_candidate_files = dict(baseline_seal)

    def persist():
        report.update(model_calls=len(provider.snapshot_calls()), budget=budget.report(),
                      duration_seconds=round(time.monotonic()-start, 3),
                      actual_models=sorted({c['actual_model'] for c in provider.snapshot_calls() if c.get('actual_model')}))
        save(directory/'report.json', report)

    def integrity():
        if files_under(baseline) != baseline_seal:
            raise ValueError('Pinned baseline changed during pilot')
        if generated_seal is not None and files_under(generated) != generated_seal:
            raise ValueError('Frozen model tests changed')
        current = files_under(candidate)
        if current != expected_candidate_files:
            raise ValueError('Candidate changed outside the last host-approved patch receipt')
        changed = {name for name in set(current)|set(baseline_seal) if current.get(name) != baseline_seal.get(name)}
        if not changed <= set(case['allowed_paths']):
            raise ValueError('Candidate changed outside runtime scope')

    def invoke(skill_id, extra=None):
        integrity()
        catalog.verify_snapshot(report['skill_catalog'])
        contract = catalog.get_contract(skill_id)
        attempt = attempts.get(skill_id, 0) + 1
        attempts[skill_id] = attempt
        timeout = min(TIMEOUTS[skill_id], max(1, int(budget.remaining())))
        declaration = catalog.validate_invocation(skill_id, 'python_package', contract['reads'],
                                                  contract['writes'], completed, attempt, timeout)
        context = build_stage_context(case, candidate, skill_id, extra)
        reservation = budget.reserve(context, CAPS[skill_id])
        stage = {'skill_id':skill_id, 'attempt':attempt, 'status':'running', 'declaration':declaration,
                 'context_bytes':serialized_bytes(context), 'source_selection':context['source_selection'],
                 'reservation':reservation}
        report['stages'].append(stage)
        persist()
        try:
            output = provider.invoke(skill_id, context, PATCH_SCHEMA if skill_id=='repair-code' else SCHEMAS[skill_id],
                                     timeout_s=timeout, token_cap=CAPS[skill_id])
        finally:
            trace = provider.last_trace
            if trace is not None:
                budget.reconcile(trace, reservation)
            persist()
        save(directory/'capabilities'/f'{skill_id}-{attempt}.json', output)
        stage.update(status='produced', artifact=f'capabilities/{skill_id}-{attempt}.json')
        results[skill_id] = output
        if skill_id not in completed:
            completed.append(skill_id)
        integrity()
        persist()
        return output

    def public_suite(label):
        integrity()
        budget.remaining()
        suite = run_package_suite(Path(case['python']), candidate if label.startswith('candidate') else baseline,
                                  generated, directory/'public-tests', label,
                                  {'source_root':'.','package':'sqlglot'})
        integrity()
        budget.remaining()
        return suite

    def prepare_generated_baseline(analysis, locations, requirement_ids):
        nonlocal generated_seal
        test_output, test_sources = generate_valid_tests(invoke, analysis, locations, requirement_ids)
        for retry in range(2):
            for name, text in test_sources.items():
                (generated/name).write_text(text, encoding='utf-8', newline='')
            generated_seal = files_under(generated)
            report['generated_tests'] = {'sha256':generated_seal, 'steps':test_output['steps'], 'coverage':test_output['coverage']}
            suite = public_suite(f'baseline-generated-{retry+1}')
            report['generated_baseline'] = suite
            persist()
            if _functional_failure(suite, require_assertion=False):
                return test_output, suite
            if retry or not suite.get('collection_errors') or suite.get('binding_error') or suite.get('timeout'):
                raise ValueError('Generated tests did not expose a collected baseline assertion failure')
            archive = directory/'rejected-generated-tests'/f'attempt-{attempts["generate-tests"]}'
            archive.parent.mkdir(parents=True, exist_ok=True)
            generated.rename(archive)
            generated.mkdir()
            report.setdefault('test_collection_retries', []).append({
                'source_directory':str(archive), 'sha256':generated_seal, 'baseline':suite})
            generated_seal = None
            test_output, test_sources = generate_valid_tests(invoke, analysis, locations, requirement_ids,
                execution_feedback=suite_feedback(suite), previous_output=test_output)

    try:
        requirement_ids = {case['requirement']['id']}
        planning = invoke('plan-workflow', {'required_skill_ids':list(CAPABILITY_NAMES)})
        if planning['skill_ids'] != list(CAPABILITY_NAMES) or {x['requirement_id'] for x in planning['goals']} != requirement_ids:
            raise ValueError('Planner omitted/reordered required Skills or requirements')
        exploration = explore_with_correction(invoke, set(case['allowed_paths']))
        if attempts.get('explore-repository', 0) > 1:
            report.setdefault('semantic_corrections', []).append({'skill_id': 'explore-repository',
                'reason': 'invalid_candidate_paths', 'attempts': attempts['explore-repository'], 'contract_relaxed': False})
        grounding = ground_with_correction(invoke, exploration['candidates'], requirement_ids, set(case['allowed_paths']))
        if attempts.get('ground-code',0)>1:
            report.setdefault('semantic_corrections',[]).append({'skill_id':'ground-code',
                'reason':'invalid_paths_or_requirement_ids', 'attempts':attempts['ground-code'], 'contract_relaxed':False})
        analysis = analyze_with_id_correction(invoke,grounding['locations'],requirement_ids)
        if attempts.get('analyze-requirements',0)>1:
            report.setdefault('semantic_corrections',[]).append({'skill_id':'analyze-requirements',
                                            'reason':'requirement_ids_mismatch',
                                            'attempts':attempts['analyze-requirements'],
                                            'contract_relaxed':False})
        test_output, baseline_suite = prepare_generated_baseline(analysis, grounding['locations'], requirement_ids)
        feedback = suite_feedback(baseline_suite)
        approved = False
        last_suite = None
        for round_number in (1, 2):
            diagnosis = invoke('diagnose-results', {'requirement_matrix':analysis, 'public_test_results':feedback,
                                                    'generated_tests':test_output['files'], 'business_round':round_number})
            diagnosis, normalized_paths = diagnosis_paths(diagnosis,requirement_ids,set(case['allowed_paths']))
            if normalized_paths:
                report.setdefault('diagnosis_path_normalizations',[]).append(
                    {'round':round_number,'paths':normalized_paths})
            if team:
                from scripts.feabench_team import PilotTeamWorkspace, pending_team_work
                from testfix.team_assignment import plan_team_execution
                from testfix.team_runtime import execute_team_round, MANAGER_GATE_SCHEMA, TeamReplanRequired

                def host_apply(proposal):
                    nonlocal expected_candidate_files
                    edits = [{'path':item['path'],
                              'old':confined(candidate,item['path'],exists=True).read_text(encoding='utf-8'),
                              'new':item['source']} for item in proposal['files']]
                    receipt = patch_candidate({'baseline_sha256':proposal['baseline_sha256'], 'edits':edits},
                        candidate,baseline,set(case['allowed_paths']),_mapping_sha256(expected_candidate_files),
                        host_full_files=True)
                    expected_candidate_files = files_under(candidate)
                    return receipt

                team_workspace = PilotTeamWorkspace(Settings(root=ROOT), directory, case, baseline, candidate,
                                                     integrity, host_apply)
                team_ledger = report.setdefault('collaboration', {'request':case['problem_statement'], 'rounds':[]})
                team_attempts = {}
                manager_evidence = {}

                def team_invoke(skill, extra, schema):
                    integrity()
                    task_id = extra.get('assigned_task',{}).get('id', extra.get('manager_gate',{}).get('task',{}).get('id','planner'))
                    scope = (skill, task_id, bool(extra.get('manager_gate')))
                    team_attempts[scope] = team_attempts.get(scope,0)+1
                    contract = catalog.get_contract(skill)
                    timeout = min(TIMEOUTS[skill], max(1,int(budget.remaining())))
                    catalog.validate_invocation(skill, 'python_package', contract['reads'], contract['writes'],
                                                completed, team_attempts[scope], timeout)
                    snapshot = team_workspace.snapshot()
                    assigned = extra.get('assigned_task')
                    if assigned:
                        snapshot['sources'] = {name:snapshot['sources'][name] for name in assigned['paths']
                                               if name in snapshot['sources']}
                        snapshot['scope_manifest']['allowed_changes'] = sorted(set(assigned['paths']) & team_workspace.allowed)
                    else:
                        relevant = {item['path'] for item in grounding['locations']}
                        relevant.update(path for failure in diagnosis['failures'] for path in failure['target_paths'])
                        gate_task = extra.get('manager_gate',{}).get('task',{})
                        relevant.update(gate_task.get('paths',[]))
                        snapshot['sources'] = {name:text for name,text in snapshot['sources'].items() if name in relevant}
                    context = {'instance_id':case['instance_id'], 'problem_statement':case['problem_statement'],
                               'hints_text':case['hints_text'],
                               'public_requirements':[case['requirement']], 'requirement_ids':list(requirement_ids),
                               'execution_profile':'python_package', 'code_locations':grounding['locations'],
                               'requirement_matrix':analysis, 'diagnosis_report':diagnosis,
                               'public_test_results':feedback, **snapshot, **extra}
                    if context.get('manager_request') == context['problem_statement']:
                        context.pop('manager_request')
                    if context.get('manager_gate',{}).get('post_change_evidence'):
                        context.pop('public_test_results')
                    cap = CAPS[skill]
                    reservation = budget.reserve(context,cap)
                    stage = {'skill_id':skill, 'team_scope':task_id, 'status':'running'}
                    report['stages'].append(stage)
                    try:
                        result = provider.invoke(skill,context,schema,timeout_s=timeout,token_cap=cap)
                        stage['status'] = 'produced'
                        return result
                    finally:
                        if provider.last_trace is not None:
                            budget.reconcile(provider.last_trace,reservation)
                        persist()

                def manager_check(assigned, handoff, observations):
                    evidence = None
                    if assigned['skill_id'] == 'repair-code':
                        key = (assigned['id'], team_workspace.candidate_hash())
                        if key not in manager_evidence:
                            current = files_under(candidate)
                            manager_evidence[key] = {
                                'candidate_sha256':key[1],
                                'file_sha256':{name:current[name] for name in assigned['paths'] if name in current},
                                'diff_against_baseline':_diff(baseline,candidate,set(assigned['paths']))[:16000],
                                'public_tests':suite_feedback(public_suite(
                                    f'candidate-generated-handoff-{len(team_ledger["rounds"])}-{len(manager_evidence)+1}')),
                            }
                        evidence = manager_evidence[key]
                    pending = pending_team_work(team_plan, team_ledger['rounds'][-1], assigned['id'])
                    return team_invoke('plan-workflow', {'manager_gate':{'task':assigned, 'handoff':handoff,
                        'observations':observations, 'post_change_evidence':evidence, 'pending_work':pending},
                        'control_instructions':'Review this employee handoff against its assigned task. '
                        'Unfinished work explicitly assigned to pending employees does not block this partial handoff. '
                        'Do not claim the overall requirement or tests passed. If ready is true, missing must be empty. '
                        'For repairs, post_change_evidence is read from the current candidate and its public tests. '
                        'A candidate tree hash and an individual file hash identify different objects. '
                        'Independent acceptance, regression, packaging and delivery gates run later; their absence '
                        'at an employee handoff is not itself a reason to reject the assigned work. '
                        'Do not mark those gates as passed.'}, MANAGER_GATE_SCHEMA)

                replan_feedback = None
                for revision_number in range(3):
                    team_attempts.clear()
                    team_plan = plan_team_with_correction(team_invoke, {'requirement_matrix':analysis, 'diagnosis_report':diagnosis,
                        'public_test_results':feedback, 'source_index':sorted(case['allowed_paths']),
                        'replan_feedback':replan_feedback,
                        'control_instructions':'Act as CodeAgent. Produce a dependency DAG of explore-repository and '
                        'repair-code work items covering the public requirement. Assign existing source paths; '
                        'use independent tasks only where the work is truly separable. If replan_feedback is present, '
                        'preserve existing candidate changes and use completed handoffs as prior evidence; '
                        'repeat completed work only if the evidence is stale or the feedback invalidates it. '
                        'Assign all missing work to suitable allowed files. '
                        'Independent verification and review occur after this employee plan; do not present them as already passed.'},
                        requirement_ids, team_workspace.allowed)
                    schedule = plan_team_execution(team_workspace,team_plan,wall_seconds=max(1,int(budget.remaining())),
                        token_limit=max(0,budget.values['token_limit']-budget.accounted_tokens),model_slots=1,max_steps=5)
                    team_ledger.setdefault('planning_attempts', []).append({
                        'business_round':round_number, 'revision':revision_number+1,
                        'plan':team_plan, 'schedule':schedule})
                    persist()
                    try:
                        _, receipt = execute_team_round(team_workspace,team_plan,schedule,call=team_invoke,
                            public_test=lambda:suite_feedback(public_suite('candidate-generated-team')),
                            emit=lambda *args:None,ledger=team_ledger,round_number=len(team_ledger['rounds'])+1,
                            model_slots=1,max_steps=5, remaining_budget=lambda:{'wall_seconds':max(1,int(budget.remaining())),
                                'token_limit':max(0,budget.values['token_limit']-budget.accounted_tokens)},
                            manager_check=manager_check, replan_on_rejected_handoff=True)
                        changed = sorted(name for name in case['allowed_paths']
                                         if expected_candidate_files.get(name) != baseline_seal.get(name))
                        receipt['changed_files'] = changed
                        receipt['hashes'] = {name:expected_candidate_files[name] for name in changed}
                        break
                    except TeamReplanRequired as error:
                        if revision_number == 2:
                            raise
                        feedback = suite_feedback(public_suite(f'candidate-generated-replan-{len(team_ledger["rounds"])}'))
                        replan_feedback = {'task':error.task, 'manager_review':error.review,
                                           'handoff':error.handoff, 'candidate_sha256':team_workspace.candidate_hash(),
                                           'completed_handoffs':team_ledger['rounds'][-1]['handoffs'],
                                           'recent_observations':error.observations[-3:]}
                        team_ledger.setdefault('replans', []).append(replan_feedback)
                        persist()
                patch = None
            else:
                patch = invoke('repair-code', {'requirement_matrix':analysis, 'diagnosis_report':diagnosis,
                                           'generated_tests':test_output['files'], 'public_test_results':feedback,
                                           'business_round':round_number,
                                           'patch_format_instructions':(
                                               'Use this native driver\'s bounded edits schema, not full-file sources. '
                                               'Return baseline_sha256 exactly equal to the current candidate hash, '
                                               'reason, and edits [{path,old,new}]. Each old must be nonempty exact '
                                               'current source text matching once; include sufficient surrounding '
                                               'context. Edits for the same file apply sequentially. Only existing '
                                               'allowlisted runtime files can be edited. Keep scope minimal.')})
            if not team:
                try:
                    receipt = patch_candidate(patch, candidate, baseline, set(case['allowed_paths']),
                                              _mapping_sha256(expected_candidate_files))
                except (ValueError,SyntaxError) as error:
                    report.setdefault('rejected_patches',[]).append({'round':round_number,
                                                                   'type':type(error).__name__,'reason':str(error)})
                    feedback['patch_rejection']={'reason':str(error),'instruction':'Correct the rejected edit proposal; no rejected file was applied.'}
                    integrity()
                    persist()
                    continue
            expected_candidate_files = files_under(candidate)
            if receipt['candidate_sha256'] != _mapping_sha256(expected_candidate_files):
                raise ValueError('Host patch receipt did not match candidate files')
            if team and 'repair-code' not in completed:
                completed.append('repair-code')
            integrity()
            report.setdefault('patches', []).append(receipt)
            (directory/f'round-{round_number}.patch').write_text(
                _diff(baseline, candidate, set(receipt['changed_files'])), encoding='utf-8')
            last_suite = public_suite(f'candidate-generated-{round_number}')
            report['generated_candidate'] = last_suite
            feedback = suite_feedback(last_suite)
            review_diff = _diff(baseline, candidate, set(receipt['changed_files']))
            review = invoke('review-patch', {'requirement_matrix':analysis,
                                             'generated_tests':test_output['files'], 'patch_artifact':receipt,
                                             'host_review_evidence':{
                                                 'candidate_sha256':receipt['candidate_sha256'],
                                                 'changed_file_sha256':receipt['hashes'],
                                                 'diff_against_baseline':review_diff[:16000],
                                                 'diff_truncated':len(review_diff)>16000},
                                             'public_test_results':feedback,
                                             'diagnosis_report':diagnosis,
                                             'business_round':round_number,
                                             'review_stage_instructions':(
                                                 'Review the candidate patch and available public test evidence. '
                                                 'Independent acceptance, regression and delivery execute after this '
                                                 'review; their absence is not itself a patch defect. Do not claim '
                                                 'those later checks have passed.')})
            if review['candidate_sha256'] != receipt['candidate_sha256']:
                raise ValueError('Reviewer approved a stale candidate')
            approved = review['approved'] and not any(x['severity']=='high' for x in review['findings'])
            report['review'] = review
            if approved and last_suite['successful'] and _paired_cases_match(baseline_suite, last_suite):
                break
            feedback['review_findings'] = review['findings']
            persist()

        # Model work ends here. Neither official test source nor official logs
        # are used as iterative repair feedback.
        integrity()
        candidate_hash = _mapping_sha256(files_under(candidate))
        if report.get('patches') and candidate_hash != report['patches'][-1]['candidate_sha256']:
            raise ValueError('Final candidate differs from the most recent host patch receipt')
        verify_contract = catalog.get_contract('verify-delivery')
        declaration = catalog.validate_invocation('verify-delivery','python_package', verify_contract['reads'],
                                                  verify_contract['writes'],completed,1,min(240,max(1,int(budget.remaining()))))
        evaluation = case['evaluation']
        output_dir = directory/'official-evaluation'
        command = [item.replace('{candidate}',str(candidate)).replace('{output}',str(output_dir)) for item in evaluation['command']]
        process = _run_bounded(command, cwd=ROOT, timeout_s=min(evaluation.get('timeout_seconds',240),budget.remaining()))
        save(directory/'judge-process.json', {**process, 'command':command})
        result_path = output_dir/'evaluation.json'
        official = json.loads(result_path.read_text(encoding='utf-8')) if result_path.is_file() else None
        report['official_evaluation'] = official
        report['official_process'] = {k:process[k] for k in ('exit_code','timeout','output_limit_exceeded','duration_s')}
        integrity()
        if _mapping_sha256(files_under(candidate)) != candidate_hash:
            raise ValueError('Native judge changed candidate runtime source')
        if official and official.get('candidate_tree_sha256') != candidate_hash:
            raise ValueError('Native judge result is not bound to this candidate tree')
        report['stages'].append({'skill_id':'verify-delivery','attempt':1,'status':'executed',
                                 'declaration':declaration,'artifact':'official-evaluation/evaluation.json'})
        report['verdict'] = {'generated_tests_pass':bool(last_suite and last_suite['successful'] and _paired_cases_match(baseline_suite,last_suite)),
                             'review_approved':bool(approved),
                             'official_resolved':bool(official and official.get('resolved') is True),
                             'official_target_stable':bool(official and official.get('target_stable') is True),
                             'official_baseline_exposes_gap':bool(official and official.get('baseline_official',{}).get('failed',0)>0
                                and official.get('baseline_official',{}).get('errors',1)==0),
                             'official_candidate_pass':bool(official and official.get('candidate_official',{}).get('all_passed') is True),
                             'regression_preserved':bool(official and official.get('baseline_regression',{}).get('all_passed') is True
                                and official.get('candidate_regression',{}).get('all_passed') is True
                                and official.get('baseline_regression',{}).get('test_case_ids_sha256')
                                and official.get('baseline_regression',{}).get('test_case_ids_sha256')==official.get('candidate_regression',{}).get('test_case_ids_sha256')),
                             'judge_completed':process['exit_code']==0 and not process['timeout'] and not process['output_limit_exceeded'],
                             'integrity':True, 'candidate_sha256':candidate_hash}
        report['accepted'] = all(v is True for k,v in report['verdict'].items() if k != 'candidate_sha256')
        report['status'] = 'completed' if report['accepted'] else 'failed'
        report['delivery_scope'] = ('Native library API and pytest evidence only. '
                                    'Wheel build, installation, CLI and web delivery were not assessed by this judge.')
        changed = [n for n in case['allowed_paths'] if sha(baseline/n) != sha(candidate/n)]
        (directory/'candidate.patch').write_bytes(_diff(baseline,candidate,set(changed)).encode('utf-8'))
    except Exception as error:
        report.update(status='failed', accepted=False, error={'type':type(error).__name__, 'message':str(error)})
    finally:
        if report['status'] == 'failed':
            try:
                integrity()
                changed = [n for n in case['allowed_paths'] if sha(baseline/n) != sha(candidate/n)]
                (directory/'candidate.patch').write_bytes(_diff(baseline,candidate,set(changed)).encode('utf-8'))
            except (ValueError, OSError) as error:
                report['prediction_error'] = {'type':type(error).__name__, 'message':str(error)}
        persist()
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case',required=True,type=Path)
    parser.add_argument('--run-dir',required=True,type=Path)
    parser.add_argument('--model',default='gpt-5.6-sol',help='Codex CLI model ID for this pilot')
    parser.add_argument('--team',action='store_true',help='Use the WCTP/CP-SAT employee controller for repair')
    parser.add_argument('--execute-model',action='store_true',help='Explicitly permit real charged model calls; omitted means preflight only')
    args = parser.parse_args()
    report = run(args.case.resolve(),args.run_dir.resolve(),args.execute_model,args.model,team=args.team)
    print(json.dumps({key:report.get(key) for key in ('instance_id','status','accepted','model_calls','actual_models','error')},ensure_ascii=False))
    return 0 if report['status'] in {'preflight','completed'} else 1


if __name__ == '__main__':
    raise SystemExit(main())
