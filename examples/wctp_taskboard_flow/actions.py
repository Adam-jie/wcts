"""Concrete WCTP actions against an isolated copy of the TaskBoard application."""
import io
import json
from pathlib import Path
import tempfile
from urllib.parse import urlencode

from taskboard_demo.taskboard.application import create_application


def call(ctx, method, path, *, query=None, data=None):
    body = json.dumps(data, ensure_ascii=False).encode('utf-8') if data is not None else b''
    result = {}
    env = {'REQUEST_METHOD': method, 'PATH_INFO': path, 'QUERY_STRING': urlencode(query or {}),
           'CONTENT_LENGTH': str(len(body)), 'CONTENT_TYPE': 'application/json', 'wsgi.input': io.BytesIO(body)}
    payload = b''.join(create_application(ctx.get_variable('database_path'))(
        env, lambda status, headers: result.update(status=status)))
    result.update(request={'method': method, 'path': path, 'query': query or {}, 'body': data},
                  response=json.loads(payload.decode('utf-8')))
    return result


def prepare(ctx, **params):
    database = Path(tempfile.mkdtemp(prefix='wctp-taskboard-')) / 'tasks.sqlite3'
    create_application(database)
    return True, {'database_path': str(database), 'result': {'数据库': str(database), '状态': '已创建测试数据库'}}


def create_chinese(ctx, **params):
    result = call(ctx, 'POST', '/api/tasks', data={'title': params['chinese_title']})
    body = result['response']
    passed = result['status'] == '201 Created' and body.get('title') == params['chinese_title'] and isinstance(body.get('id'), int)
    return passed, {'task_id': body.get('id'), 'result': result}


def create_english(ctx, **params):
    result = call(ctx, 'POST', '/api/tasks', data={'title': params['english_title']})
    body = result['response']
    passed = result['status'] == '201 Created' and body.get('title') == params['english_title'] and isinstance(body.get('id'), int)
    return passed, {'second_task_id': body.get('id'), 'result': result}


def search_chinese(ctx, **params):
    result = call(ctx, 'GET', '/api/tasks', query={'q': params['search_word'], 'page_size': 50})
    body = result['response']
    passed = result['status'] == '200 OK' and body['total'] == 1 and body['items'][0]['id'] == ctx.get_variable('task_id')
    return passed, {'result': result}


def search_missing(ctx, **params):
    result = call(ctx, 'GET', '/api/tasks', query={'q': params['missing_word'], 'page_size': 50})
    body = result['response']
    return result['status'] == '200 OK' and body['total'] == 0 and body['items'] == [], {'result': result}


def first_page(ctx, **params):
    result = call(ctx, 'GET', '/api/tasks', query={'q': params['page_word'], 'page': 1, 'page_size': 1})
    body = result['response']
    passed = result['status'] == '200 OK' and body['total'] == 2 and len(body['items']) == 1
    return passed, {'first_page_id': body['items'][0]['id'] if body['items'] else None, 'result': result}


def second_page(ctx, **params):
    result = call(ctx, 'GET', '/api/tasks', query={'q': params['page_word'], 'page': 2, 'page_size': 1})
    body = result['response']
    passed = (result['status'] == '200 OK' and body['total'] == 2 and len(body['items']) == 1
              and body['items'][0]['id'] != ctx.get_variable('first_page_id'))
    return passed, {'result': result}


def complete_task(ctx, **params):
    result = call(ctx, 'PATCH', f"/api/tasks/{ctx.get_variable('task_id')}", data={'completed': True})
    return result['status'] == '200 OK' and result['response'].get('completed') is True, {'result': result}


def verify_persistence(ctx, **params):
    result = call(ctx, 'GET', f"/api/tasks/{ctx.get_variable('task_id')}")
    return result['status'] == '200 OK' and result['response'].get('completed') is True, {'result': result}


def delete_task(ctx, **params):
    identifier = ctx.get_variable('second_task_id')
    result = call(ctx, 'DELETE', f'/api/tasks/{identifier}')
    return result['status'] == '200 OK' and result['response'].get('deleted') == identifier, {'result': result}


def verify_delete(ctx, **params):
    result = call(ctx, 'GET', f"/api/tasks/{ctx.get_variable('second_task_id')}")
    return result['status'] == '404 Not Found', {'result': result}
