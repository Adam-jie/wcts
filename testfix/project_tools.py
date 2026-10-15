"""Registered-project tools with fixed paths, sealed inputs and patch-only writes.

Model outputs are data. No model-provided command is ever executed here. The
registered Flaskr adapter executes trusted local Python in bounded subprocesses;
this is not a general filesystem/network sandbox for hostile repositories.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import shutil
import subprocess
import sys
import time
import uuid

from .project_eval import _mapping_sha256, _runtime_files, _sha256


EXCLUDED = {'__pycache__', '.pytest_cache', '.git', '.venv', 'instance', 'node_modules', 'build', 'dist'}


def relative_path(value: str) -> str:
    if not isinstance(value, str) or not value or '\\' in value:
        raise ValueError('Path must use a nonempty repository-relative POSIX spelling')
    posix, windows = PurePosixPath(value), PureWindowsPath(value)
    if posix.is_absolute() or windows.drive or any(part in {'', '.', '..'} for part in value.split('/')):
        raise ValueError('Path escapes its declared root')
    if any(part in EXCLUDED or part.startswith('.env') for part in posix.parts):
        raise ValueError('Generated/environment/dependency paths are not readable project inputs')
    return value


def confined(root: Path, value: str, *, exists=False) -> Path:
    path = root / relative_path(value)
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('Path resolves outside its declared root')
    cursor = path
    while cursor != root:
        if cursor.is_symlink() or (hasattr(cursor, 'is_junction') and cursor.is_junction()):
            raise ValueError('Symlink/junction project paths are not accepted')
        cursor = cursor.parent
    if exists and not path.is_file():
        raise ValueError('Required project file is missing: ' + value)
    return path


def files_under(root: Path) -> dict[str, str]:
    result = {}
    for path in sorted(root.rglob('*')):
        name = path.relative_to(root).as_posix()
        if any(part in EXCLUDED or part.startswith('.env') for part in path.relative_to(root).parts):
            continue
        if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
            raise ValueError('Repository contains a symlink/junction: ' + name)
        if path.is_file():
            relative_path(name)
            result[name] = _sha256(path)
    if not result:
        raise ValueError('Empty project snapshot')
    return result


def public_sources(root: Path, *, byte_limit=180_000) -> dict[str, str]:
    result, size = {}, 0
    for name in files_under(root):
        if Path(name).suffix.lower() not in {'.py', '.sql', '.html', '.css', '.toml', '.rst', '.md', '.txt'}:
            continue
        text = confined(root, name, exists=True).read_text(encoding='utf-8')
        size += len(text.encode('utf-8'))
        if size > byte_limit:
            raise ValueError('Project exceeds the explicit public context byte budget')
        result[name] = text
    return result


def builtin_project(settings) -> dict:
    base = settings.root / 'experiments/flaskr'
    source = json.loads((base/'source.json').read_text(encoding='utf-8'))
    requirements = json.loads((base/'requirements.json').read_text(encoding='utf-8'))
    return {'id':'Project/flaskr', 'title':'Flaskr 完整博客：自主补全详情、搜索与分页',
            'adapter':'flaskr', 'source':source, 'baseline':str(base/'baseline'),
            'requirements':requirements['requirements'], 'clarifications':requirements['clarifications'][:2],
            'allowed_changes':requirements['allowed_changes'], 'acceptance':str(base/'acceptance_tests'),
            'python':str(settings.root/'.venv-flaskr/Scripts/python.exe') if os.name == 'nt' else str(settings.root/'.venv-flaskr/bin/python')}


def project_catalog(settings) -> list[dict]:
    # The API accepts registered IDs only. Importing a new executable repository
    # is an explicit local CLI operation, not a model or remote HTTP action.
    projects = [builtin_project(settings)]
    registry = settings.root/'data/projects/registered'
    if registry.is_dir():
        for path in sorted(registry.glob('*.json')):
            data = json.loads(path.read_text(encoding='utf-8'))
            if data['id'] in {item['id'] for item in projects}:
                raise ValueError('Duplicate registered project ID')
            projects.append(data)
    return projects


def get_project(settings, project_id: str) -> dict:
    for item in project_catalog(settings):
        if item['id'] == project_id:
            return item
    raise ValueError('Unknown registered project')


class ProjectWorkspace:
    def __init__(self, settings, run_id: str, project: dict):
        self.settings, self.project = settings, project
        if len(run_id) != 32 or any(c not in '0123456789abcdef' for c in run_id):
            raise ValueError('Run ID must be 32 lowercase hexadecimal characters')
        self.directory = settings.artifacts / run_id
        self.work = self.directory/'workspace'
        self.baseline, self.candidate = self.work/'baseline', self.work/'candidate'
        self.generated = self.work/'generated_tests'
        self.acceptance = self.work/'private/acceptance_tests'
        self.regression = self.work/'private/regression_tests'
        self.adapter = project.get('adapter','flaskr')
        if self.work.exists():
            raise ValueError('A run workspace already exists; never overwrite prior evidence')
        baseline = Path(project['baseline']).resolve()
        if not baseline.is_relative_to(settings.root.resolve()):
            raise ValueError('Registered source must stay within the local workspace')
        hashes = files_under(baseline)
        if project['source'].get('files') and hashes != project['source']['files']:
            raise ValueError('Pinned source snapshot changed')
        self.work.mkdir(parents=True)
        for destination in (self.baseline, self.candidate):
            for name in hashes:
                path = confined(destination, name)
                path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(confined(baseline,name,exists=True),path)
        acceptance = Path(project['acceptance']).resolve()
        if not acceptance.is_relative_to(settings.root.resolve()):
            raise ValueError('Acceptance must stay within the local workspace')
        hidden_hashes = files_under(acceptance)
        for name in hidden_hashes:
            target = confined(self.acceptance,name)
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(confined(acceptance,name,exists=True),target)
        self.regression_seal = None
        if self.adapter=='python_package':
            original = Path(project['package_config']['regression']).resolve()
            if not original.is_relative_to(settings.root.resolve()):
                raise ValueError('Registered behavioral regression must stay inside the workspace')
            self.regression_seal=files_under(original)
            for name in self.regression_seal:
                target=confined(self.regression,name)
                target.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(confined(original,name,exists=True),target)
        self.generated.mkdir()
        self.allowed = set(project['allowed_changes'])
        for name in self.allowed:
            relative_path(name)
            if self.adapter=='flaskr' and not name.startswith('flaskr/'):
                raise ValueError('Flaskr patch scope must be in the runtime package')
            if self.adapter=='python_package':
                config=project['package_config']
                prefix=config['source_root'].strip('/')+'/'+config['package'].replace('.','/')+'/'
                if not name.startswith(prefix):
                    raise ValueError('Package patch scope must stay inside the declared runtime package')
        self.source_seal = hashes
        self.hidden_seal = hidden_hashes
        self.generated_seal = None
        self.candidate_seal = hashes.copy()

    def assert_integrity(self):
        if files_under(self.baseline) != self.source_seal:
            raise ValueError('Baseline snapshot modified during execution')
        if files_under(self.acceptance) != self.hidden_seal:
            raise ValueError('Independent acceptance modified during execution')
        if self.regression_seal is not None and files_under(self.regression)!=self.regression_seal:
            raise ValueError('Frozen behavioral regression modified during execution')
        if self.generated_seal is not None and files_under(self.generated) != self.generated_seal:
            raise ValueError('Frozen generated tests modified during execution')
        if files_under(self.candidate) != self.candidate_seal:
            raise ValueError('Candidate changed outside the patch tool or after review')

    def snapshot(self):
        self.assert_integrity()
        return {'source_revision':_mapping_sha256(self.source_seal), 'candidate_sha256':self.candidate_hash(),
                'sources':public_sources(self.candidate), 'scope_manifest':{'allowed_changes':sorted(self.allowed),
                'frozen':['original_tests','generated_tests','independent_acceptance','evaluation_code']}}

    def runtime_files(self, root):
        return set(files_under(root)) if self.adapter=='python_package' else _runtime_files(root)

    def candidate_hash(self):
        return _mapping_sha256({name:_sha256(self.candidate/name) for name in sorted(self.runtime_files(self.candidate))})

    def write_tests(self, output: dict, *, dry_run=False):
        if self.generated_seal is not None:
            raise ValueError('Generated tests are already frozen')
        sources = output.get('files')
        if not isinstance(sources,list) or not 1 <= len(sources) <= 8:
            raise ValueError('Tester must produce 1–8 Python test/fixture files')
        total, seen, pending = 0, set(), {}
        for item in sources:
            name, source = item['path'], item['source']
            if name in seen or '/' in name or not name.endswith('.py') or not (name.startswith('test_') or name == 'conftest.py'):
                raise ValueError('Generated tests use only test_*.py/conftest.py in their own directory')
            if not isinstance(source,str) or not source.strip():
                raise ValueError('Empty generated test')
            tree = ast.parse(source)
            # A test is executable Python in the trusted adapter, but importing
            # a sibling answer tree or executing shell commands is never needed.
            for node in ast.walk(tree):
                if isinstance(node,(ast.Import,ast.ImportFrom)):
                    modules = [a.name.split('.')[0] for a in node.names] if isinstance(node,ast.Import) else [(node.module or '').split('.')[0]]
                    if any(m in {'subprocess','socket','requests','httpx','ctypes','importlib','runpy'} for m in modules):
                        raise ValueError('Generated tests import an execution/network escape module')
                if isinstance(node,ast.Call):
                    if isinstance(node.func,ast.Name) and node.func.id in {'exec','eval','compile','__import__'}:
                        raise ValueError('Dynamic execution is not allowed in generated tests')
                    if isinstance(node.func,ast.Attribute) and node.func.attr in {'system','popen','spawn','insert','chdir'}:
                        raise ValueError('Generated tests cannot launch commands or redirect source imports')
            total += len(source.encode('utf-8'))
            if total > 100_000:
                raise ValueError('Generated test size limit exceeded')
            pending[name] = (confined(self.generated,name),source)
            seen.add(name)
        if not any(name.startswith('test_') for name in seen):
            raise ValueError('No generated test module')
        if dry_run:
            return
        for path,source in pending.values():
            path.write_text(source,encoding='utf-8',newline='')
        self.generated_seal = files_under(self.generated)
        return self.generated_seal.copy()

    def apply_patch(self, output: dict, *, dry_run=False):
        self.assert_integrity()
        if output.get('baseline_sha256') != self.candidate_hash():
            raise ValueError('Patch proposal uses a stale candidate version')
        changes = output.get('files')
        if not isinstance(changes,list) or not 1 <= len(changes) <= len(self.allowed):
            raise ValueError('Repair must propose bounded changed files')
        pending, total = {}, 0
        for item in changes:
            name, source = item['path'], item['source']
            if name not in self.allowed or name in pending or not isinstance(source,str) or not source.strip():
                raise ValueError('Repair attempted an unauthorized/duplicate/empty file change')
            target = confined(self.candidate,name)
            if name.endswith('.py'):
                ast.parse(source)
            total += len(source.encode('utf-8'))
            if total > 120_000:
                raise ValueError('Patch size limit exceeded')
            pending[name] = (target,source)
        if dry_run:
            return
        for name,(target,source) in pending.items():
            target.parent.mkdir(parents=True,exist_ok=True)
            temporary = target.with_name(target.name+'.testfix-tmp')
            temporary.write_text(source,encoding='utf-8',newline='')
            temporary.replace(target)
        current = files_under(self.candidate)
        changed = {name for name in set(current)|set(self.source_seal) if current.get(name) != self.source_seal.get(name)}
        if not changed <= self.allowed:
            raise ValueError('Candidate changed outside the authorized file scope')
        self.candidate_seal = current
        self.assert_integrity()
        return {'candidate_sha256':self.candidate_hash(),'changed_files':sorted(changed),
                'hashes':{name:current[name] for name in sorted(changed)}}


def _kill_tree(process):
    if process.poll() is not None:
        return
    if os.name == 'nt':
        subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],capture_output=True,timeout=8,check=False)
    else:
        import signal
        os.killpg(process.pid,signal.SIGKILL)
    process.wait(timeout=8)


def run_project_tool(settings, workspace: ProjectWorkspace, operation: str, *, timeout_s: float, round_number=1) -> dict:
    if operation not in {'baseline_public','candidate_public','verify'}:
        raise ValueError('Unknown fixed project tool')
    workspace.assert_integrity()
    directory = workspace.directory/'tool_calls'/f'{operation}-{round_number}-{uuid.uuid4().hex[:8]}'
    directory.mkdir(parents=True)
    request = {'operation':operation,'root':str(settings.root.resolve()),'run_id':workspace.directory.name,
               'baseline':str(workspace.baseline),'candidate':str(workspace.candidate),'generated':str(workspace.generated),
               'acceptance':str(workspace.acceptance),'python':workspace.project['python'],'output':str(directory/'result.json')}
    if workspace.adapter=='python_package':
        request['adapter']='python_package'
        request['package_config']={**workspace.project['package_config'],'regression':str(workspace.regression)}
    request_path = directory/'request.json'
    request_path.write_text(json.dumps(request),encoding='utf-8')
    worker = Path(__file__).with_name('project_tool_worker.py').resolve()
    command = [sys.executable,str(worker),str(request_path)]
    from .project_eval import _environment
    started = time.monotonic()
    with (directory/'stdout.log').open('wb') as stdout,(directory/'stderr.log').open('wb') as stderr:
        process = subprocess.Popen(command,cwd=settings.root,env=_environment(),stdout=stdout,stderr=stderr,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
                                   start_new_session=os.name != 'nt')
        try:
            process.wait(timeout=max(.01,timeout_s))
        except subprocess.TimeoutExpired:
            _kill_tree(process)
            raise TimeoutError('Project tool exceeded its wall-time budget') from None
    workspace.assert_integrity()
    if process.returncode != 0 or not (directory/'result.json').is_file():
        raise RuntimeError(f'Project tool failed with exit {process.returncode}; see bounded tool logs')
    result = json.loads((directory/'result.json').read_text(encoding='utf-8'))
    if result.get('operation') != operation or result.get('candidate_sha256') != workspace.candidate_hash():
        raise ValueError('Tool evidence is not bound to the current candidate')
    result.update(duration_s=round(time.monotonic()-started,3),artifact=str((directory/'result.json').relative_to(settings.root)))
    return result
