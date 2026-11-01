"""Frozen host acceptance. Never included in a model's public snapshot."""
import io
import json
from urllib.parse import urlencode
import pytest
from taskboard.application import create_application
from taskboard.storage import Storage


@pytest.fixture
def product(tmp_path):
    database = tmp_path / 'acceptance.sqlite3'
    application = create_application(database)
    def request(path, method='GET', data=None):
        body = json.dumps(data).encode('utf-8') if data is not None else b''
        route, _, query = path.partition('?'); status = []
        response = b''.join(application({'REQUEST_METHOD': method, 'PATH_INFO': route, 'QUERY_STRING': query,
            'CONTENT_LENGTH': str(len(body)), 'wsgi.input': io.BytesIO(body)}, lambda code, headers: status.append(int(code.split()[0]))))
        return status[0], json.loads(response)
    return request, database


@pytest.mark.parametrize('needle', ['中文独立', 'English Verify', '包含_%_字面值'])
def test_literal_search_and_filtered_total(product, needle):
    request, _ = product
    request('/api/tasks', 'POST', {'title': needle})
    request('/api/tasks', 'POST', {'title': '无关数据'})
    result = request('/api/tasks?' + urlencode({'q': needle}))[1]
    assert result['total'] == 1 and [r['title'] for r in result['items']] == [needle]


def test_no_results_and_clear_search(product):
    request, _ = product
    assert request('/api/tasks?q=NO-SUCH-ITEM-1847')[1]['items'] == []
    assert request('/api/tasks?q=NO-SUCH-ITEM-1847')[1]['total'] == 0
    assert request('/api/tasks?q=')[1]['total'] == 10


@pytest.mark.parametrize('page_size', [1, 3, 5, 7])
def test_disjoint_and_complete_pages(product, page_size):
    request, _ = product
    rows = [request(f'/api/tasks?page={p}&page_size={page_size}')[1]['items'] for p in range(1, 12)]
    ids = [item['id'] for page in rows for item in page]
    assert len(ids) == len(set(ids)) == 10
    assert rows[-1] == []


def test_filtered_pages(product):
    request, _ = product
    for i in range(12): request('/api/tasks', 'POST', {'title': f'独立分页 {i}'})
    first = request('/api/tasks?' + urlencode({'q': '独立分页', 'page': 1, 'page_size': 5}))[1]
    last = request('/api/tasks?' + urlencode({'q': '独立分页', 'page': 3, 'page_size': 5}))[1]
    assert first['total'] == last['total'] == 12
    assert len(first['items']) == 5 and len(last['items']) == 2
    assert not ({x['id'] for x in first['items']} & {x['id'] for x in last['items']})


def test_true_and_false_survive_reopening(product):
    request, database = product
    task = request('/api/tasks', 'POST', {'title': '独立持久化'})[1]
    for completed in (True, False, True):
        assert request(f'/api/tasks/{task["id"]}', 'PATCH', {'completed': completed})[1]['completed'] is completed
        assert request(f'/api/tasks/{task["id"]}')[1]['completed'] is completed
        assert Storage(database).get(task['id'])['completed'] is completed


def test_repeat_twenty_times(product):
    request, database = product
    task = request('/api/tasks', 'POST', {'title': '循环独立关键词'})[1]
    for i in range(20):
        flag = bool(i % 2)
        request(f'/api/tasks/{task["id"]}', 'PATCH', {'completed': flag})
        assert Storage(database).get(task['id'])['completed'] is flag
        result = request('/api/tasks?' + urlencode({'q': '循环独立关键词'}))[1]
        assert result['total'] == 1 and result['items'][0]['id'] == task['id']


def test_preserve_create_delete_and_bad_inputs(product):
    request, database = product
    assert request('/api/tasks', 'POST', {'title': '  '})[0] == 400
    task = request('/api/tasks', 'POST', {'title': '独立增删'})[1]
    assert Storage(database).get(task['id'])['title'] == '独立增删'
    assert request(f'/api/tasks/{task["id"]}', 'DELETE')[0] == 200
    assert request(f'/api/tasks/{task["id"]}')[0] == 404
    assert request('/api/tasks?page=0')[0] == 400
