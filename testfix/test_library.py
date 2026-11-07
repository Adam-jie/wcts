"""Public test inputs and import into the existing task workflow."""
import hashlib
import json
from pathlib import Path
import shutil
from typing import Literal
import uuid
import zipfile

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .benchmark_data import SWE_REVISION, _public_collection, TASK_PUBLIC_FIELDS
from .datasets import load_tasks
from .intake import IntakeRequest, IntakeStore, inspect_file


def library_entries(settings):
    index = settings.root / 'data/test-library/index.json'
    entries = json.loads(index.read_text(encoding='utf-8'))['entries'] if index.is_file() else []
    registered = settings.root / 'data/projects/registered/rich-cli.json'
    if registered.is_file():
        project = json.loads(registered.read_text(encoding='utf-8'))
        for entry in entries:
            if (entry['id'] == 'rich-cli-modification' and project['source']['commit'] == entry['source']['commit']
                    and Path(project['python']).is_file()):
                entry['ready_run_url'] = '/implementation?project=registered:Project/rich-cli'
                entry['execution'] = '已登记CLI执行器、原有回归与固定验收；可使用原始基准任务运行。编辑后的需求请另行导入。'
    swe = settings.root / 'data/benchmarks/swe-bench-lite' / SWE_REVISION
    if (swe / 'manifest.json').is_file():
        _, rows = _public_collection(swe, 'swe-bench-lite', SWE_REVISION)
        for raw in rows:
            row = {key: raw[key] for key in TASK_PUBLIC_FIELDS['swe-bench-lite'] if key in raw}
            entries.append({'id': 'swe-' + row['instance_id'], 'dataset': 'swe-bench-lite', 'domain': 'software',
                            'operation': 'modification', 'title': row['instance_id'],
                            'description': row['problem_statement'], 'acceptance': '使用官方评测器运行该实例冻结的目标测试与原有回归。',
                            'environment': 'Linux / Docker；按实例固定环境安装依赖。', 'origin': 'official_benchmark',
                            'source': {'repository': 'https://github.com/' + row['repo'], 'commit': row['base_commit'],
                                       'license': '遵循目标仓库和官方数据许可'},
                            'status': 'input_ready', 'execution': '公开任务已采集；目标仓库和Docker评测环境需另外准备。'})
    if (settings.dataset / 'manifest.json').is_file():
        manifest = json.loads((settings.dataset / 'manifest.json').read_text(encoding='utf-8'))
        # load_tasks supplies stubs/partial code only; evaluator code is not exposed.
        from .datasets import read_rows, verify_dataset
        verify_dataset(settings.dataset)
        for identifier, row in read_rows(settings.dataset).items():
            entries.append({'id': 'humaneval-' + identifier.split('/')[-1], 'dataset': 'humaneval', 'domain': 'software',
                            'operation': 'completion', 'title': identifier + ' · ' + row['entry_point'],
                            'description': row['prompt'], 'acceptance': '固定官方验收与原有回归；函数级成绩单独统计。',
                            'environment': 'Python；函数级隔离执行器', 'origin': 'official_benchmark',
                            'source': {'repository': 'https://github.com/openai/human-eval', 'commit': manifest['commit'], 'license': 'MIT'},
                            'status': 'input_ready', 'execution': '函数级辅助集；不计为完整工程任务。'})
    return entries


class LibraryImport(BaseModel):
    model_config = ConfigDict(extra='forbid')
    title: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, min_length=1, max_length=20000)
    acceptance: str | None = Field(default=None, max_length=12000)


def create_test_library_router(settings):
    router, store = APIRouter(), IntakeStore(settings.root)

    def selected(identifier):
        entry = next((entry for entry in library_entries(settings) if entry['id'] == identifier), None)
        if entry is None: raise HTTPException(404, '测试任务不存在')
        return entry

    def bundle(entry):
        path = settings.root / 'data/test-library' / entry['bundle']
        if hashlib.sha256(path.read_bytes()).hexdigest() != entry['bundle_sha256']:
            raise HTTPException(409, '测试工程包校验失败，请重新采集')
        return path

    @router.get('/test-library')
    def page():
        return FileResponse(settings.root / 'web/test-library/index.html')

    @router.get('/api/test-library')
    def listing(q: str = Query(default='', max_length=200),
                domain: Literal['', 'web', 'software', 'mcu', 'mpu', 'rtl'] = '',
                operation: Literal['', 'completion', 'modification', 'development'] = '',
                dataset: str = Query(default='', max_length=80),
                offset: int = Query(default=0, ge=0), limit: int = Query(default=24, ge=1, le=100)):
        entries = library_entries(settings)
        counts = {key: sum(entry['dataset'] == key for entry in entries) for key in sorted({e['dataset'] for e in entries})}
        matches = [e for e in entries if (not domain or e['domain'] == domain)
                   and (not operation or e['operation'] == operation) and (not dataset or e['dataset'] == dataset)
                   and (not q or q.casefold() in (e['title'] + e['description'] + e['source'].get('repository', '')).casefold())]
        return {'total': len(matches), 'counts': counts, 'entries': [{key: value for key, value in e.items()
                if key not in {'bundle', 'bundle_sha256'}} | {'has_bundle': 'bundle' in e} for e in matches[offset:offset + limit]]}

    @router.get('/api/test-library/{identifier}/bundle')
    def download_bundle(identifier: str):
        entry = selected(identifier)
        if 'bundle' not in entry: raise HTTPException(404, '此任务只有公开需求，没有工程包')
        return FileResponse(bundle(entry), media_type='application/zip', filename=identifier + '.zip')

    @router.post('/api/test-library/{identifier}/import', status_code=201)
    def import_entry(identifier: str, body: LibraryImport):
        entry = selected(identifier)
        source = entry['source']
        url = source.get('repository', '') + '/archive/' + source.get('commit', '') + '.zip' if entry['dataset'] == 'swe-bench-lite' else ''
        mode = 'upload' if 'bundle' in entry or entry['dataset'] == 'humaneval' else 'url' if url else 'idea'
        # Validate overrides at the same boundary as manually created tasks.
        try:
            request = IntakeRequest(mode=mode, title=body.title or entry['title'],
                                    description=body.description or entry['description'],
                                    acceptance=entry['acceptance'] if body.acceptance is None else body.acceptance,
                                    environment=entry['environment'], url=url)
        except ValidationError as error:
            raise HTTPException(422, '任务名称、需求或验收标准超出限制，请编辑后重试') from error
        archive = bundle(entry) if 'bundle' in entry else None
        task = store.create(request)
        task['test_library'] = {'entry_id': identifier, 'dataset': entry['dataset'], 'domain': entry['domain'],
                                'operation': entry['operation'], 'origin': entry['origin'], 'source': source,
                                'execution': entry['execution']}
        if (entry.get('ready_run_url') and request.description == entry['description']
                and request.acceptance == entry['acceptance']):
            task['test_library']['execution_url'] = entry['ready_run_url']
        if mode == 'upload':
            file_id = uuid.uuid4().hex
            destination = store.directory(task['id']) / 'files' / file_id
            destination.parent.mkdir(parents=True, exist_ok=True)
            if archive:
                shutil.copyfile(archive, destination)
            else:
                task_id = 'HumanEval/' + identifier.removeprefix('humaneval-')
                code = load_tasks(settings.dataset, [task_id])[0].initial_code
                with zipfile.ZipFile(destination, 'w', compression=zipfile.ZIP_DEFLATED) as output:
                    output.writestr('solution.py', code)
            task['files'] = [{'id': file_id, 'name': identifier + '.zip', 'size': destination.stat().st_size,
                              'sha256': hashlib.sha256(destination.read_bytes()).hexdigest()}]
        store.save(task)
        if mode != 'url':
            store.prepare(task['id'])
            store.process(task['id'])
            task = store.read(task['id'])
            if task['status'] == 'prepared':
                task['analysis']['message'] = '测试集输入已整理。' + entry['execution']
                store.save(task)
        return {'task_id': task['id'], 'url': '/tasks?task=' + task['id'],
                'status': store.read(task['id'])['status'], 'execution': entry['execution']}

    return router
