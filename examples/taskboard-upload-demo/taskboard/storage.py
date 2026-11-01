"""SQLite persistence and task operations."""
from pathlib import Path
import sqlite3
from contextlib import contextmanager

SEED_TITLES = [
    '部署演示环境', '整理传感器数据', '检查串口回包', '实现中文搜索', '补充分页功能',
    '回归测试登录模块', '数据库备份', '编写接口文档', '核验异常输入', '准备演示视频',
]


class Storage:
    def __init__(self, filename):
        self.filename = str(filename)
        Path(filename).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.execute('CREATE TABLE IF NOT EXISTS tasks (id INTEGER PRIMARY KEY, title TEXT NOT NULL, completed INTEGER NOT NULL DEFAULT 0)')
            connection.execute('CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT)')
            if not connection.execute("SELECT 1 FROM metadata WHERE key='seeded'").fetchone():
                connection.executemany('INSERT INTO tasks (title) VALUES (?)', [(title,) for title in SEED_TITLES])
                connection.execute("INSERT INTO metadata VALUES ('seeded', '1')")

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.filename)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def record(row):
        if row is None:
            return None
        return {'id': row['id'], 'title': row['title'], 'completed': bool(row['completed'])}

    def get(self, identifier):
        with self.connect() as connection:
            return self.record(connection.execute('SELECT * FROM tasks WHERE id=?', (identifier,)).fetchone())

    def list(self, query='', page=1, page_size=5):
        with self.connect() as connection:
            total = connection.execute('SELECT count(*) FROM tasks').fetchone()[0]
            offset = 0
            rows = connection.execute('SELECT * FROM tasks ORDER BY id DESC LIMIT ? OFFSET ?', (page_size, offset)).fetchall()
        return {'items': [self.record(row) for row in rows], 'total': total, 'page': page, 'page_size': page_size}

    def create(self, title):
        if not isinstance(title, str) or not title.strip() or len(title.strip()) > 120:
            raise ValueError('任务标题应为 1–120 个字符')
        with self.connect() as connection:
            cursor = connection.execute('INSERT INTO tasks (title) VALUES (?)', (title.strip(),))
            identifier = cursor.lastrowid
        return self.get(identifier)

    def complete(self, identifier, completed):
        if type(completed) is not bool:
            raise ValueError('completed 必须为布尔值')
        task = self.get(identifier)
        if task is None:
            raise KeyError(identifier)
        task['completed'] = completed
        return task

    def delete(self, identifier):
        with self.connect() as connection:
            cursor = connection.execute('DELETE FROM tasks WHERE id=?', (identifier,))
            if not cursor.rowcount:
                raise KeyError(identifier)
