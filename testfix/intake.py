"""Durable task intake. Imported files are evidence, never executable commands."""
from __future__ import annotations

import hashlib
import http.client
import ipaddress
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import socket
import ssl
import stat
import tarfile
import threading
import time
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit, quote
import uuid
import zipfile
from html.parser import HTMLParser

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator, ConfigDict

from .reports import now

MAX_BYTES = 1024 * 1024 * 1024
MAX_MEMBERS = 30000
MAX_EXPANDED = 4 * 1024 * 1024 * 1024
SOURCE_SUFFIXES = {'.py', '.js', '.ts', '.tsx', '.jsx', '.html', '.css', '.vue', '.java', '.go', '.rs', '.c', '.cpp', '.h', '.sql', '.cs', '.v', '.sv', '.ino'}
INSTALLERS = {'.exe', '.msi', '.msix', '.apk', '.aab', '.deb', '.rpm', '.dmg', '.pkg', '.appimage'}


class IntakeRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    mode: str
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=20000)
    acceptance: str = Field(default='', max_length=12000)
    environment: str = Field(default='', max_length=4000)
    url: str = Field(default='', max_length=2048)
    proposal_id: str | None = Field(default=None, pattern='^[a-f0-9]{32}$')

    @field_validator('title', 'description')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('请输入任务名称和功能描述')
        return value.strip()

    @field_validator('mode')
    @classmethod
    def supported_mode(cls, value):
        if value not in {'upload', 'url', 'idea'}:
            raise ValueError('请选择上传文件、提供网址或描述新功能')
        return value


class IntakeEdit(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=0)
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=20000)
    acceptance: str = Field(default='', max_length=12000)
    environment: str = Field(default='', max_length=4000)
    proposal_id: str | None = Field(default=None, pattern='^[a-f0-9]{32}$')

    @field_validator('title', 'description')
    @classmethod
    def nonblank(cls, value):
        return IntakeRequest.nonblank(value)


def safe_name(value):
    name = value.strip()
    if not name or len(name) > 180 or name in {'.', '..'} or re.search(r'[<>:"/\\|?*\x00-\x1f]', name) or name.endswith(('.', ' ')):
        raise ValueError('文件名无效，请使用不含路径的文件名')
    if name.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}:
        raise ValueError('文件名是系统保留名称')
    return name


def public_target(url):
    """Resolve once; the connection uses this exact address to prevent rebinding."""
    parsed = urlsplit(url)
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password or re.search(r'[\x00-\x20\x7f]', url):
        raise ValueError('请提供不含账号密码的 HTTP 或 HTTPS 网址')
    host = parsed.hostname.encode('idna').decode('ascii')
    port = parsed.port or (443 if parsed.scheme == 'https' else 80)
    addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(entry[4][0]).is_global for entry in addresses):
        raise ValueError('下载地址须为公网地址，不能指向本机、内网或云元数据服务')
    return parsed, host, port, addresses[0][4][0]


class PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, port, address):
        super().__init__(host, port, timeout=15, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        raw = socket.create_connection((self.address, self.port), self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except BaseException:
            raw.close()
            raise


def repository_url(url):
    parsed = urlsplit(url)
    parts = parsed.path.strip('/').split('/')
    if parsed.hostname == 'github.com' and len(parts) == 2 and all(re.fullmatch(r'[\w.-]+', part) for part in parts):
        owner, repo = parts
        return f'https://api.github.com/repos/{owner}/{repo.removesuffix(".git")}/zipball'
    return url


def download(url, target):
    current, started = repository_url(url), time.monotonic()
    for _ in range(6):
        parsed, host, port, address = public_target(current)
        connection = PinnedHTTPS(host, port, address) if parsed.scheme == 'https' else http.client.HTTPConnection(address, port, timeout=15)
        authority = host if port == (443 if parsed.scheme == 'https' else 80) else f'{host}:{port}'
        path = quote(parsed.path or '/', safe="/!$&'()*+,;=:@-._~%")
        try:
            connection.request('GET', urlunsplit(('', '', path, parsed.query, '')), headers={'Host': authority, 'User-Agent': 'TestFix-Intake/0.2', 'Accept-Encoding': 'identity'})
            response = connection.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                location = response.getheader('Location')
                if not location:
                    raise ValueError('下载站点未提供跳转地址')
                current = urljoin(current, location)
                continue
            if response.status != 200:
                raise ValueError(f'下载失败，远端返回 HTTP {response.status}')
            length = response.getheader('Content-Length')
            if length and int(length) > MAX_BYTES:
                raise ValueError('文件超过单文件 1 GB 接收上限')
            size, digest = 0, hashlib.sha256()
            with target.open('xb') as output:
                while chunk := response.read(256 * 1024):
                    size += len(chunk)
                    if size > MAX_BYTES or time.monotonic() - started > 120:
                        raise ValueError('下载超过大小或时间上限，请下载后直接上传文件')
                    output.write(chunk)
                    digest.update(chunk)
            if not size or length and size != int(length):
                raise ValueError('下载内容为空或不完整')
            disposition = response.getheader('Content-Disposition', '')
            match = re.search(r'filename\*?=(?:UTF-8\x27\x27)?["\x27]?([^";\r\n]+)', disposition, re.I)
            filename = unquote(match[1]).strip() if match else unquote(PurePosixPath(parsed.path).name) or 'download.bin'
            try:
                filename = safe_name(filename)
            except ValueError:
                filename = 'download.bin'
            if 'text/html' in response.getheader('Content-Type', '') and not filename.endswith(('.html', '.htm')):
                filename += '.html'
            return {'name': filename, 'size': size, 'sha256': digest.hexdigest(), 'url': current}
        finally:
            connection.close()
    raise ValueError('网址跳转次数过多')


class PageInfo(HTMLParser):
    def __init__(self):
        super().__init__(); self.in_title = False; self.title = ''; self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == 'title': self.in_title = True
        href = dict(attrs).get('href', '')
        if tag == 'a' and len(self.links) < 30 and re.search(r'\.(zip|gz|exe|msi|apk|deb)(?:\?|$)', href, re.I):
            self.links.append(href[:2048])

    def handle_endtag(self, tag):
        if tag == 'title': self.in_title = False

    def handle_data(self, text):
        if self.in_title: self.title = (self.title + text)[:300]


def member_name(name):
    value = name.rstrip('/')
    path = PurePosixPath(value)
    if not value or '\\' in value or path.is_absolute() or PureWindowsPath(value).drive or '..' in path.parts or ':' in value:
        raise ValueError('压缩包包含越界路径，未导入')
    return value


def inspect_file(path, filename, *, webpage=False):
    suffix = Path(filename).suffix.lower()
    if suffix in INSTALLERS:
        return {'kind': 'installer', 'source_available': False, 'message': '已接收安装包。代码补全还需要对应源码；运行验证需要匹配的系统与执行环境。'}
    names, total = [], 0
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            for entry in archive.infolist():
                name = member_name(entry.filename)
                if stat.S_ISLNK(entry.external_attr >> 16): raise ValueError('压缩包包含符号链接，未导入')
                total += entry.file_size
                names.append(name)
                if len(names) > MAX_MEMBERS or total > MAX_EXPANDED: raise ValueError('压缩包文件数量或展开体积超过接收上限')
        kind = 'archive'
    elif filename.lower().endswith(('.tar', '.tar.gz', '.tgz', '.tar.bz2', '.tar.xz')):
        with tarfile.open(path, 'r:*') as archive:
            for entry in archive:
                names.append(member_name(entry.name))
                if not entry.isfile() and not entry.isdir(): raise ValueError('压缩包包含链接或特殊设备文件，未导入')
                total += entry.size
                if len(names) > MAX_MEMBERS or total > MAX_EXPANDED: raise ValueError('压缩包文件数量或展开体积超过接收上限')
        kind = 'archive'
    elif suffix in {'.html', '.htm'} and webpage:
        info = PageInfo(); info.feed(path.read_bytes()[:2 * 1024 * 1024].decode('utf-8', errors='replace'))
        return {'kind': 'webpage', 'source_available': False, 'page_title': info.title.strip(), 'download_links': info.links,
                'message': '已读取网页。下方列出页面中的下载链接，可选择链接继续创建下载任务。'}
    elif suffix in {'.zip', '.tar', '.tgz'} or filename.lower().endswith(('.tar.gz', '.tar.xz', '.tar.bz2')):
        raise ValueError('压缩包格式无效或文件不完整')
    else:
        names = [filename]; kind = 'source_file' if suffix in SOURCE_SUFFIXES else 'attachment'
    sources = [name for name in names if Path(name).suffix.lower() in SOURCE_SUFFIXES and not any(part in {'node_modules', '.venv', 'vendor', '.git'} for part in PurePosixPath(name).parts)]
    manifests = [name for name in names if PurePosixPath(name).name in {'package.json', 'pyproject.toml', 'requirements.txt', 'Cargo.toml', 'go.mod', 'CMakeLists.txt', 'pom.xml'}]
    return {'kind': kind, 'source_available': bool(sources), 'file_count': len(names), 'source_count': len(sources),
            'extensions': sorted({Path(name).suffix.lower() for name in sources}), 'manifests': manifests[:40], 'sample_files': names[:80],
            'message': '已识别源码，可交接需求与工程结构进行后续规划。' if sources else '已留存文件，尚未识别可修改的源码。'}


class IntakeStore:
    def __init__(self, root):
        self.root = root / 'artifacts/intakes'
        self.lock = threading.RLock()
        self.uploading = set()

    def directory(self, identifier):
        if not re.fullmatch('[a-f0-9]{32}', identifier): raise HTTPException(404, '任务不存在')
        return self.root / identifier

    def read(self, identifier):
        try:
            with self.lock:
                return json.loads((self.directory(identifier) / 'task.json').read_text(encoding='utf-8'))
        except FileNotFoundError:
            raise HTTPException(404, '任务不存在') from None

    def save(self, task):
        directory = self.directory(task['id']); directory.mkdir(parents=True, exist_ok=True)
        from .reports import _publish_text
        _publish_text(directory / 'task.json', json.dumps(task, ensure_ascii=False, indent=2, allow_nan=False))

    def changed(self, task):
        task['revision'] = task.get('revision', 0) + 1
        task['updated_at'] = now()
        self.save(task)

    def create(self, body):
        if body.mode == 'url' and not body.url.strip(): raise HTTPException(422, '请输入网址')
        task = {'id': uuid.uuid4().hex, **body.model_dump(exclude={'proposal_id'}), 'created_at': now(), 'revision': 0,
                'details_source': 'manual', 'status': 'draft', 'files': [], 'events': [], 'analysis': None}
        with self.lock: self.save(task)
        return task

    def prepare(self, identifier):
        with self.lock:
            task = self.read(identifier)
            if task['status'] != 'draft' or identifier in self.uploading: raise HTTPException(409, '任务正在处理或已经整理，请勿重复提交')
            if task['mode'] == 'upload' and not task['files']: raise HTTPException(422, '请至少上传一个文件')
            task['status'] = 'reading'; task['events'].append({'at': now(), 'message': '开始读取输入材料'})
            self.changed(task)
        return task

    def process(self, identifier):
        task = self.read(identifier)
        temporary = self.directory(identifier) / 'download.part'
        try:
            if task['mode'] == 'url':
                metadata = download(task['url'].strip(), temporary)
                file_id = uuid.uuid4().hex
                destination = self.directory(identifier) / 'files' / file_id
                destination.parent.mkdir(exist_ok=True)
                temporary.replace(destination)
                task['files'].append({'id': file_id, **metadata})
                task['events'].append({'at': now(), 'message': f'下载完成：{metadata["name"]}'})
            for file in task['files']:
                file['inspection'] = inspect_file(self.directory(identifier) / 'files' / file['id'], file['name'], webpage=bool(file.get('url')))
            has_source = any(file['inspection']['source_available'] for file in task['files'])
            task['analysis'] = {
                'method': 'input_inspection', 'source_available': has_source,
                'next_stage': 'requirements_design' if task['mode'] == 'idea' else 'source_analysis' if has_source else 'source_required',
                'requirements': [line.strip(' -•\t') for line in task['description'].splitlines() if line.strip()],
                'acceptance_criteria': [line.strip(' -•\t') for line in task['acceptance'].splitlines() if line.strip()],
                'environment': task['environment'], 'planning_status': 'pending',
                'message': '需求已保存，待 CodeAgent 主控规划。' if task['mode'] == 'idea' or has_source else '材料已保存；需补充源码或明确产品测试入口。',
            }
            task['status'] = 'prepared'
            task['events'].append({'at': now(), 'message': '输入整理完成，尚未调用模型或执行程序'})
        except Exception as error:
            task['status'] = 'failed'
            task['error'] = str(error) if isinstance(error, (ValueError, tarfile.TarError, zipfile.BadZipFile)) else '读取材料失败，请检查网址、网络或文件格式后重新提交'
            task['events'].append({'at': now(), 'message': task['error']})
        finally:
            temporary.unlink(missing_ok=True)
            with self.lock: self.changed(task)


def create_intake_router(settings):
    router, store = APIRouter(), IntakeStore(settings.root)
    from .intake_details import IntakeDetails
    details = IntakeDetails(settings, store)

    @router.get('/intake')
    def page():
        return FileResponse(settings.root / 'web/intake/index.html')

    @router.get('/tasks')
    def tasks_page():
        return FileResponse(settings.root / 'web/tasks/index.html')

    @router.get('/api/intakes')
    def listing(q: str = Query(default='', max_length=200), status: str = '', mode: str = '',
                offset: int = Query(default=0, ge=0), limit: int = Query(default=100, ge=1, le=100)):
        with store.lock:
            values = []
            counts = {'all': 0, 'draft': 0, 'reading': 0, 'prepared': 0, 'failed': 0}
            for file in store.root.glob('*/task.json'):
                try:
                    item = json.loads(file.read_text(encoding='utf-8'))
                    summary = {key: item[key] for key in ('id', 'title', 'mode', 'status', 'created_at')}
                    summary.update(updated_at=item.get('updated_at', item['created_at']), file_count=len(item['files']),
                                   details_source=item.get('details_source', 'manual'), description=item['description'][:160])
                    if item.get('test_library'):
                        summary['execution_ready'] = False
                    counts['all'] += 1
                    if item['status'] in counts: counts[item['status']] += 1
                    if status and item['status'] != status or mode and item['mode'] != mode: continue
                    if q.strip() and q.strip().casefold() not in '\n'.join([item['id'], item['title'], item['description'],
                            item.get('url', ''), *(f['name'] for f in item['files'])]).casefold(): continue
                    values.append(summary)
                except (ValueError, KeyError, OSError): continue
            values.sort(key=lambda item: item['updated_at'], reverse=True)
            return {'tasks': values[offset:offset + limit], 'total': len(values), 'counts': counts, 'offset': offset, 'limit': limit}

    @router.post('/api/intakes', status_code=201)
    def create(body: IntakeRequest):
        with store.lock:
            provenance = details.provenance(body.proposal_id, None, None, body.model_dump())
            task = store.create(body)
            task.update(provenance)
            store.save(task)
            return task

    @router.patch('/api/intakes/{identifier}')
    def edit(identifier: str, body: IntakeEdit):
        with store.lock:
            task = store.read(identifier)
            if task['status'] == 'reading' or identifier in store.uploading:
                raise HTTPException(409, '材料正在读取或上传，请完成后再编辑资料')
            if task.get('revision', 0) != body.revision:
                raise HTTPException(409, '任务已更新，请刷新详情后再保存，当前输入未被覆盖')
            provenance = details.provenance(body.proposal_id, identifier, body.revision, body.model_dump())
            task.update(body.model_dump(exclude={'revision', 'proposal_id'}))
            task.update(provenance)
            if task.get('analysis'):
                task['analysis'].update(requirements=[s.strip(' -•\t') for s in task['description'].splitlines() if s.strip()],
                    acceptance_criteria=[s.strip(' -•\t') for s in task['acceptance'].splitlines() if s.strip()], environment=task['environment'])
            task['events'].append({'at': now(), 'message': '保存任务资料 · ' + {'manual': '自己填写', 'ai': '采用 AI 草稿', 'ai_edited': '编辑 AI 草稿后采用'}[task['details_source']]})
            store.changed(task)
            return task

    details.add_routes(router)
    from .intake_projects import add_intake_project_routes
    add_intake_project_routes(router, settings, store)

    @router.get('/api/intakes/{identifier}')
    def detail(identifier: str): return store.read(identifier)

    @router.put('/api/intakes/{identifier}/files', status_code=201)
    async def upload(identifier: str, request: Request, filename: str):
        try: filename = safe_name(filename)
        except ValueError as error: raise HTTPException(422, str(error)) from None
        with store.lock:
            task = store.read(identifier)
            if task['mode'] != 'upload' or task['status'] != 'draft' or identifier in store.uploading: raise HTTPException(409, '当前任务不能上传文件')
            if len(task['files']) >= 20: raise HTTPException(422, '每个任务最多接收 20 个附件')
            store.uploading.add(identifier)
        file_id, size, digest = uuid.uuid4().hex, 0, hashlib.sha256()
        directory = store.directory(identifier) / 'files'; directory.mkdir(exist_ok=True)
        temporary = directory / (file_id + '.part')
        try:
            with temporary.open('xb') as output:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > MAX_BYTES: raise HTTPException(413, '单文件不能超过 1 GB')
                    output.write(chunk); digest.update(chunk)
            if not size: raise HTTPException(422, '不能上传空文件')
            metadata = {'id': file_id, 'name': filename, 'size': size, 'sha256': digest.hexdigest()}
            with store.lock:
                task = store.read(identifier)
                temporary.replace(directory / file_id)
                task['files'].append(metadata); store.changed(task)
            return metadata
        finally:
            temporary.unlink(missing_ok=True)
            with store.lock: store.uploading.discard(identifier)

    @router.post('/api/intakes/{identifier}/prepare', status_code=202)
    def prepare(identifier: str, background: BackgroundTasks):
        task = store.prepare(identifier)
        background.add_task(store.process, identifier)
        return task

    @router.get('/api/intakes/{identifier}/handoff')
    def handoff(identifier: str):
        task = store.read(identifier)
        if task['status'] != 'prepared': raise HTTPException(409, '材料尚未整理完成')
        return FileResponse(store.directory(identifier) / 'task.json', filename=f'testfix-task-{identifier[:8]}.json', media_type='application/json')

    @router.get('/api/intakes/{identifier}/files/{file_id}')
    def original(identifier: str, file_id: str):
        task = store.read(identifier)
        metadata = next((file for file in task['files'] if file['id'] == file_id), None)
        if not metadata: raise HTTPException(404, '附件不存在')
        return FileResponse(store.directory(identifier) / 'files' / file_id, filename=metadata['name'], media_type='application/octet-stream')

    return router
