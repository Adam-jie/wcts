"""Public regression tests for features that already work."""
import json
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from pathlib import Path
from wsgiref.simple_server import make_server, WSGIRequestHandler

from taskboard.application import create_application
from taskboard.storage import Storage


class QuietHandler(WSGIRequestHandler):
    def log_message(self, *args):
        pass


class ProductCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / 'tasks.sqlite3'
        self.server = make_server('127.0.0.1', 0, create_application(self.database), handler_class=QuietHandler)
        self.origin = f'http://127.0.0.1:{self.server.server_port}'
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()

    def tearDown(self):
        self.server.shutdown(); self.worker.join(); self.server.server_close(); self.temporary.cleanup()

    def request(self, path, method='GET', data=None):
        body = json.dumps(data).encode() if data is not None else None
        request = Request(self.origin + path, method=method, data=body, headers={'Content-Type': 'application/json'})
        with urlopen(request, timeout=5) as response:
            return json.load(response)

    def test_health(self):
        self.assertEqual(self.request('/api/health')['status'], 'ok')

    def test_frontend_and_assets_load(self):
        for path in ['/', '/app.js', '/style.css']:
            with urlopen(self.origin + path) as response:
                self.assertEqual(response.status, 200)
                self.assertGreater(len(response.read()), 100)

    def test_seed_list(self):
        result = self.request('/api/tasks')
        self.assertEqual(result['total'], 10)
        self.assertEqual(len(result['items']), 5)

    def test_create_and_delete(self):
        task = self.request('/api/tasks', 'POST', {'title': '新增测试任务'})
        self.assertEqual(self.request(f'/api/tasks/{task["id"]}')['title'], '新增测试任务')
        self.request(f'/api/tasks/{task["id"]}', 'DELETE')
        with self.assertRaises(HTTPError) as error:
            self.request(f'/api/tasks/{task["id"]}')
        self.assertEqual(error.exception.code, 404)

    def test_created_task_survives_storage_reopen(self):
        task = self.request('/api/tasks', 'POST', {'title': '持久化测试'})
        self.assertEqual(Storage(self.database).get(task['id'])['title'], '持久化测试')

    def test_blank_title_is_rejected(self):
        with self.assertRaises(HTTPError) as error:
            self.request('/api/tasks', 'POST', {'title': '   '})
        self.assertEqual(error.exception.code, 400)
