"""Bind an inspected intake to a supported, immutable executable project."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sys
import tomllib
import uuid
import zipfile

from fastapi import HTTPException
from pydantic import BaseModel, Field

from .intake import member_name, inspect_file
from .project_tools import confined, files_under, get_project
from .reports import now

REQUIREMENTS = [
    {'id': 'R1', 'description': '修复任务标题的字面值子串搜索：支持中文与英文，% 和 _ 按普通字符匹配；搜索结果 total 反映匹配总数，无结果 items=[]、total=0，空搜索恢复全部数据。'},
    {'id': 'R2', 'description': '修复分页：按过滤后的稳定 id DESC 排序，使用 (page-1)*page_size 偏移；不同页不重复，末页返回剩余条目，超过末页返回空列表，保留原有 page/page_size 校验。'},
    {'id': 'R3', 'description': '修复完成状态持久化：True 和 False 均保存到 SQLite，重新读取、刷新及重启应用后状态保持；多次切换结果一致。'},
    {'id': 'R4', 'description': '保持原有新增、读取、删除、空标题拒绝、初始10条数据、JSON接口及静态网页可用；通过原有6项回归，并执行多数据、20轮循环与实际HTTP/SQLite重启验证。'}]


class RegisterIntake(BaseModel):
    revision: int = Field(ge=0)


def register_intake_project(settings, store, identifier, revision):
    with store.lock:
        task = store.read(identifier)
        if task.get('revision', 0) != revision: raise HTTPException(409, '任务资料已更新，请刷新后选择项目')
        if task['status'] != 'prepared': raise HTTPException(409, '请先完成材料整理')
        archive = next((f for f in task['files'] if f.get('inspection', {}).get('kind') == 'archive'), None)
        if not archive: raise HTTPException(422, '需要可修改的源码压缩包；安装包不能直接补全源码')
        path = store.directory(identifier) / 'files' / archive['id']
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''): digest.update(chunk)
        if digest.hexdigest() != archive['sha256']:
            raise HTTPException(409, '原始附件发生变化，未导入执行器')
        inspect_file(path, archive['name'])
        if not zipfile.is_zipfile(path): raise HTTPException(422, '本轮任务管理系统适配器需要 ZIP 源码包')
        with zipfile.ZipFile(path) as source:
            names = [member_name(e.filename) for e in source.infolist() if not e.is_dir()]
            if len(names) != len(set(names)): raise HTTPException(422, '压缩包包含重复文件名，未导入')
            roots = [n[:-len('taskboard/storage.py')] for n in names if n.endswith('taskboard/storage.py')]
            roots = [p for p in roots if all(p+n in names for n in ('taskboard/__init__.py', 'taskboard/application.py', 'server.py',
                'tests/test_core.py', 'pyproject.toml', 'web/index.html', 'web/app.js', 'web/style.css'))]
            if len(roots) != 1: raise HTTPException(422, '此源码未匹配任务管理系统适配器，请选择已登记工程')
            prefix = roots[0]
            try: metadata = tomllib.loads(source.read(prefix + 'pyproject.toml').decode('utf-8'))
            except (ValueError, UnicodeError): raise HTTPException(422, 'pyproject.toml 格式无效，请修正工程说明文件后重新上传') from None
            if metadata.get('project', {}).get('dependencies'):
                raise HTTPException(422, '此版本声明了额外依赖，需要先配置对应运行环境')
            registry = settings.root / 'data/projects/registered'; registry.mkdir(parents=True, exist_ok=True)
            project_id = f'Project/intake-{identifier}-v{revision}'
            record_path = registry / f'intake-{identifier}-v{revision}.json'
            if record_path.is_file():
                existing = json.loads(record_path.read_text(encoding='utf-8'))
                if existing['source']['archive_sha256'] != archive['sha256']: raise HTTPException(409, '项目来源冲突')
                return existing
            destination = settings.root / 'data/projects/imported' / f'{identifier}-v{revision}'
            if destination.exists(): raise HTTPException(409, '项目准备曾被中断，请重新保存任务资料后再选择')
            destination.mkdir(parents=True)
            for entry in source.infolist():
                if entry.is_dir() or not entry.filename.startswith(prefix): continue
                name = entry.filename[len(prefix):]
                if any(part in {'__pycache__', '.pytest_cache', '.git', '.venv', 'node_modules', 'var', 'dist', 'build'} or part.startswith('.env') for part in PurePosixPath(name).parts): continue
                target = confined(destination, name); target.parent.mkdir(parents=True, exist_ok=True)
                with source.open(entry) as incoming, target.open('xb') as output:
                    shutil.copyfileobj(incoming, output, length=1024 * 1024)
        record = {'id': project_id, 'title': task['title'], 'adapter': 'python_application', 'baseline': str(destination),
            'python': sys.executable,
            'requirements': REQUIREMENTS, 'clarifications': ['搜索按相同字符的字面值包含匹配，不额外要求忽略大小写。', '应用不依赖 Flask，使用标准库 WSGI 与 SQLite。'],
            'allowed_changes': ['taskboard/storage.py', 'taskboard/application.py'],
            'application_config': {'package': 'taskboard', 'source_root': '.'},
            'public_runtime': {'package': 'taskboard', 'factory': 'taskboard.application.create_application(database_path)',
                'protocol': 'WSGI', 'test_style': 'in_process_wsgi_or_Storage', 'dependencies': [], 'test_runner': 'pytest',
                'build': 'frozen source ZIP plus Python compilation', 'delivery': 'actual HTTP, SQLite restart and 20 cycles'},
            'intake_id': identifier, 'intake_revision': revision,
            'source': {'kind': 'user_upload', 'archive_sha256': archive['sha256'], 'files': files_under(destination),
                       'intake_id': identifier, 'prepared_at': now()}}
        frozen = Path(__file__).parent / 'application_contracts/taskboard'
        contract = settings.root / 'data/projects/contracts/taskboard-v1'
        contract.mkdir(parents=True, exist_ok=True)
        for name in files_under(frozen):
            target = confined(contract, name); target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() and target.read_bytes() != (frozen / name).read_bytes():
                raise HTTPException(409, '固定验收合同发生变化，未覆盖已有文件')
            if not target.exists(): shutil.copyfile(frozen / name, target)
        record['acceptance'] = str(contract)
        record['acceptance_files'] = files_under(contract)
        temporary = record_path.with_suffix('.' + uuid.uuid4().hex + '.tmp')
        temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(temporary, record_path)
        return record


def add_intake_project_routes(router, settings, store):
    @router.post('/api/intakes/{identifier}/project', status_code=201)
    def register(identifier: str, body: RegisterIntake):
        record = register_intake_project(settings, store, identifier, body.revision)
        return {key: record[key] for key in ('id', 'title', 'adapter', 'requirements', 'intake_id', 'intake_revision', 'public_runtime')}
