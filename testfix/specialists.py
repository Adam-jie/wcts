"""Evidence-based specialist overlays for the existing, confined employee loop.

This is capability routing, not a repair-quality search or a device adapter.
Skill prose is advisory; existing host tools, ownership, budgets and gates retain
all execution authority. Unknown languages and absent adapters fail closed.
"""
from __future__ import annotations

import ast
from copy import deepcopy
import hashlib
import json
from pathlib import Path, PurePosixPath
import re


DEFAULT_LIBRARY = Path(__file__).with_name('specialist_library')
SPECIALIST_IDS = ('software', 'javascript', 'frontend', 'threejs', 'backend',
                  'database', 'mcu', 'mpu', 'protocol', 'integration')
PROFILE_CAPABILITIES = {
    'flaskr_project': {'source_tools', 'python_tests', 'wheel_build', 'http_smoke',
                       'rendered_templates', 'database_restart', 'integration_gates'},
    'python_package': {'source_tools', 'python_tests', 'wheel_build', 'cli_smoke', 'integration_gates'},
    'python_application': {'source_tools', 'python_tests', 'archive_build', 'http_smoke',
                           'rendered_templates', 'database_restart', 'integration_gates'},
}
_CODE_SUFFIXES = {'.py', '.js', '.mjs', '.cjs', '.ts', '.jsx', '.tsx', '.vue', '.svelte',
                  '.html', '.css', '.sql', '.c', '.h', '.cpp', '.hpp', '.cc', '.dts', '.dtsi'}
_DOC_SUFFIXES = {'.md', '.rst', '.txt', '.toml', '.json', '.yaml', '.yml', '.ini', '.cfg'}


class SpecialistCatalog:
    """Load only fixed, local Skill paths and bind their exact bytes to a snapshot."""

    def __init__(self, root=DEFAULT_LIBRARY):
        self.root = Path(root).resolve(strict=True)
        self._raw = {}
        for name in ('catalog.json', *(identity + '/SKILL.md' for identity in SPECIALIST_IDS)):
            path = (self.root / name).resolve(strict=True)
            if not path.is_relative_to(self.root) or path.is_symlink():
                raise ValueError('Specialist Skill escaped its trusted library')
            self._raw[name] = path.read_bytes()
        self.catalog = json.loads(self._raw['catalog.json'])
        if (self.catalog.get('schema') != 'testfix.specialist-catalog.v1'
                or [item['id'] for item in self.catalog.get('specialists', [])] != list(SPECIALIST_IDS)):
            raise ValueError('Invalid specialist catalog')
        self._snapshot = {'schema': 'testfix.specialist-snapshot.v1', 'version': self.catalog['version'],
                          'files': {name: hashlib.sha256(raw).hexdigest() for name, raw in self._raw.items()}}

    def snapshot(self):
        return deepcopy(self._snapshot)

    def verify(self):
        for name, digest in self._snapshot['files'].items():
            path = (self.root / name).resolve(strict=True)
            if not path.is_relative_to(self.root) or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise ValueError('Specialist Skill changed after loading')

    def render(self, identities):
        self.verify()
        if not set(identities) <= set(SPECIALIST_IDS):
            raise ValueError('Unknown specialist Skill')
        bodies = [self._raw[identity + '/SKILL.md'].decode('utf-8') for identity in SPECIALIST_IDS if identity in identities]
        return ('\n\n# Trusted specialist Skills (advice within the existing host contract)\n'
                + '\n\n'.join(bodies)) if bodies else ''


def _without_comments(text):
    # Evidence hints only, not a JavaScript/C parser or a code-safety validator.
    pattern = r'("(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|`(?:\\.|[^`\\])*`)|//[^\n]*|/\*[\s\S]*?\*/|<!--[^>]*-->'
    return re.sub(pattern, lambda match: match.group(1) or ' ', text)


def _signals(name, text):
    suffix = PurePosixPath(name).suffix.lower()
    found = {'software'} if suffix in _CODE_SUFFIXES else set()
    code = _without_comments(text)
    imports = set()
    db_call = False
    if suffix == '.py':
        try:
            tree = ast.parse(text)
        except SyntaxError:
            # Broken code is expected in repair tasks; anchored imports remain hints.
            code = re.sub(r'(?m)#.*$', '', text)
            imports = set(re.findall(r'(?m)^\s*(?:from|import)\s+([\w.]+)', code))
            db_call = bool(re.search(r'\b(?:db|conn|connection|cursor|session)\.execute\s*\(', code))
        else:
            imports = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
            imports.update(node.module or '' for node in ast.walk(tree) if isinstance(node, ast.ImportFrom))
            db_call = any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                          and node.func.attr in {'execute', 'executemany', 'executescript'}
                          and isinstance(node.func.value, ast.Name)
                          and node.func.value.id in {'db', 'conn', 'connection', 'cursor', 'session'}
                          for node in ast.walk(tree))
            # Python comments and strings cannot activate JavaScript/hardware hints.
            code = '\n'.join(sorted(imports))
    if suffix in {'.js', '.mjs', '.cjs', '.ts', '.jsx', '.tsx', '.vue', '.svelte'}:
        found.add('javascript')
    if suffix in {'.html', '.css', '.jsx', '.tsx', '.vue', '.svelte'}:
        found.add('frontend')
    if suffix == '.html' and re.search(r'<script\b', code, re.IGNORECASE):
        found.add('javascript')
    if suffix != '.py' and re.search(r'\b(?:document\.|window\.|React\.|createApp\s*\()', code):
        found.add('frontend')
    if suffix != '.py' and re.search(r'''(?:\bfrom\s*|\brequire\s*\(|\bimport\s*\(?)['"]three(?:/[^'"]*)?['"]|\bnew\s+THREE\.''', code):
        found.update({'threejs', 'javascript', 'frontend'})
    if (any(module.split('.')[0] in {'flask', 'fastapi', 'django', 'starlette', 'wsgiref'} for module in imports)
            or re.search(r'''(?:from\s*|require\s*\()['"](?:express|fastify|koa)['"]''', code)):
        found.add('backend')
    if (suffix == '.sql' or db_call
            or any(module.split('.')[0] in {'sqlite3', 'sqlalchemy', 'psycopg', 'psycopg2', 'pymysql', 'redis'}
                   or module.startswith('django.db') for module in imports)):
        found.add('database')
    if (suffix in {'.c', '.h', '.cpp', '.hpp', '.cc'}
            and (re.search(r'(?i)\b(?:stm32\w*|freertos|cmsis|avr/io|HAL_\w+)\b', code)
                 or set(PurePosixPath(name).parts) & {'firmware', 'bsp'})):
        found.add('mcu')
    if suffix in {'.dts', '.dtsi'} or re.search(r'\b(?:buildroot|yocto|spidev|gpiod)\b', code):
        found.add('mpu')
    if (any(module.split('.')[0] in {'serial', 'smbus', 'smbus2', 'spidev', 'can'} for module in imports)
            or re.search(r'\b(?:HAL_(?:UART|I2C|SPI|CAN)_\w+|uart_(?:read|write)\w*)\b', code)):
        found.add('protocol')
    return found


def route_task(task, sources, profile, *, required_specialists=(), allowed_new_paths=(), catalog=None):
    """Derive overlays from assigned public source, plus trusted registered metadata.

    ``required_specialists`` is host registry metadata for deleted/empty code,
    never model-selected prompt paths or free-form user instructions.
    """
    if profile not in PROFILE_CAPABILITIES or task.get('skill_id') not in {'repair-code', 'explore-repository'}:
        raise ValueError('Unknown specialist execution profile or employee Skill')
    if (not isinstance(required_specialists, (list, tuple))
            or any(identity not in SPECIALIST_IDS for identity in required_specialists)):
        raise ValueError('Unknown registered specialist requirement')
    catalog = catalog or SpecialistCatalog()
    catalog.verify()
    evidence, unknown = {}, []
    for name in task['paths']:
        path = PurePosixPath(name)
        if path.is_absolute() or '..' in path.parts or '\\' in name or ':' in name or str(path) != name:
            raise ValueError('Invalid assigned specialist source path')
        if name not in sources and name not in allowed_new_paths:
            raise ValueError('Assigned specialist source is absent')
        text = sources.get(name, '')
        if not isinstance(text, str):
            raise ValueError('Assigned specialist source must be text')
        suffix = path.suffix.lower()
        if suffix not in _CODE_SUFFIXES | _DOC_SUFFIXES:
            unknown.append(name)
        for identity in _signals(name, text):
            evidence.setdefault(identity, []).append({'path': name, 'basis': 'assigned_source_signal'
                                                     if name in sources else 'registered_new_file_path'})
    for identity in required_specialists:
        evidence.setdefault(identity, []).append({'basis': 'registered_requirement'})
    # Rendering or hardware capabilities are not implied by a general software Skill.
    if 'threejs' in evidence:
        for identity in ('javascript', 'frontend'):
            evidence.setdefault(identity, []).append({'basis': 'threejs_dependency'})
    discovery = task['skill_id'] == 'explore-repository'
    specialists, missing = [], set()
    for entry in catalog.catalog['specialists']:
        identity = entry['id']
        if identity not in evidence:
            continue
        needed = set(entry['execution_capabilities'])
        if identity == 'frontend' and 'javascript' in evidence:
            needed.add('browser_tests')
        if identity == 'database':
            scoped = '\n'.join(sources.get(name, '') for name in task['paths'])
            for marker, capability in [('psycopg', 'postgres_runtime'), ('pymysql', 'mysql_runtime'),
                                       ('sqlalchemy', 'orm_runtime'), ('redis', 'redis_runtime')]:
                if re.search(r'(?m)^\s*(?:from|import)\s+' + marker + r'\b', scoped):
                    needed.add(capability)
        if identity == 'software' and any(PurePosixPath(name).suffix.lower() in {'.c', '.h', '.cpp', '.hpp', '.cc'} for name in task['paths']):
            needed.add('native_build')
        absent = sorted(needed - PROFILE_CAPABILITIES[profile])
        missing.update(absent)
        specialists.append({'id': identity, 'name': entry['name'],
                            'status': 'ready' if discovery or not absent else 'blocked',
                            'evidence': evidence[identity], 'missing_capabilities': absent})
    status = 'ready' if discovery else 'needs_discovery' if unknown else 'blocked' if missing else 'ready'
    return {'schema': 'testfix.specialist-assignment.v1', 'version': catalog.catalog['version'],
            'task_id': task['id'], 'profile': profile, 'status': status,
            'agent_id': task['skill_id'] + '/' + ('+'.join(item['id'] for item in specialists) or 'general') + '/' + task['id'],
            'execution_scope': 'read_only_discovery' if discovery else 'registered_adapter_only',
            'specialists': specialists,
            'not_applicable': [identity for identity in SPECIALIST_IDS if identity not in evidence],
            'missing_capabilities': sorted(missing), 'unknown_source_types': sorted(unknown),
            'evaluation_basis': 'source_and_registered_capability_rules; not_execution_results'}


def registered_requirements(project, task):
    """Read the optional, registered per-requirement hints needed for erased code."""
    return sorted({identity for requirement in project.get('requirements', [])
                   if requirement['id'] in task['requirement_ids']
                   for identity in requirement.get('specialist_ids', [])})


def preview_project(settings, project_id):
    """Inspect a registered baseline only; no import, model, test or write."""
    from .project_tools import get_project, public_sources, project_profile
    project = get_project(settings, project_id)
    baseline = Path(project['baseline']).resolve(strict=True)
    if not baseline.is_relative_to(settings.root.resolve()):
        raise ValueError('Registered source must stay within the local workspace')
    task = {'id': 'preview', 'skill_id': 'repair-code', 'paths': project['allowed_changes'],
            'requirement_ids': [item['id'] for item in project['requirements']]}
    catalog = SpecialistCatalog()
    routing = route_task(task, public_sources(baseline),
                         project_profile(project),
                         required_specialists=registered_requirements(project, task),
                         allowed_new_paths=project['allowed_changes'], catalog=catalog)
    return {'project_id': project_id, 'scope': 'registered_allowed_changes_at_baseline',
            'execution_performed': False, 'routing': routing, 'catalog': catalog.snapshot()}
