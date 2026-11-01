"""Local WSGI API and a fixed set of static frontend files."""
import json
from pathlib import Path
import re
from urllib.parse import parse_qs

from .storage import Storage

WEB = Path(__file__).resolve().parents[1] / 'web'
STATIC_FILES = {'/': ('index.html', 'text/html; charset=utf-8'), '/app.js': ('app.js', 'text/javascript; charset=utf-8'), '/style.css': ('style.css', 'text/css; charset=utf-8')}


def create_application(database):
    store = Storage(database)

    def application(environ, start_response):
        def respond(status, data, content_type='application/json; charset=utf-8'):
            payload = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode('utf-8')
            start_response(status, [('Content-Type', content_type), ('Content-Length', str(len(payload))), ('Cache-Control', 'no-store'), ('X-Content-Type-Options', 'nosniff'), ('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'")])
            return [payload]

        def body():
            length = int(environ.get('CONTENT_LENGTH') or 0)
            if not 0 < length <= 8192:
                raise ValueError('请求正文不能为空且不能超过 8 KB')
            value = json.loads(environ['wsgi.input'].read(length))
            if not isinstance(value, dict):
                raise ValueError('请求应为 JSON 对象')
            return value

        method, path = environ['REQUEST_METHOD'], environ.get('PATH_INFO', '/')
        try:
            if method == 'GET' and path in STATIC_FILES:
                filename, content_type = STATIC_FILES[path]
                return respond('200 OK', (WEB / filename).read_bytes(), content_type)
            if method == 'GET' and path == '/api/health':
                return respond('200 OK', {'status': 'ok', 'product': 'TaskBoard'})
            if method == 'GET' and path == '/api/tasks':
                query = parse_qs(environ.get('QUERY_STRING', ''))
                page, page_size = int(query.get('page', ['1'])[0]), int(query.get('page_size', ['5'])[0])
                if page < 1 or not 1 <= page_size <= 50:
                    raise ValueError('页码应大于 0，每页数量应为 1–50')
                return respond('200 OK', store.list(query.get('q', [''])[0], page, page_size))
            if method == 'POST' and path == '/api/tasks':
                return respond('201 Created', store.create(body().get('title')))
            match = re.fullmatch(r'/api/tasks/([1-9][0-9]*)', path)
            if match:
                identifier = int(match[1])
                if method == 'GET':
                    task = store.get(identifier)
                    if task is None: raise KeyError(identifier)
                    return respond('200 OK', task)
                if method == 'PATCH':
                    return respond('200 OK', store.complete(identifier, body().get('completed')))
                if method == 'DELETE':
                    store.delete(identifier)
                    return respond('200 OK', {'deleted': identifier})
            return respond('404 Not Found', {'error': '接口或资源不存在'})
        except KeyError:
            return respond('404 Not Found', {'error': '任务不存在'})
        except (ValueError, UnicodeError) as error:
            return respond('400 Bad Request', {'error': str(error)})

    return application
