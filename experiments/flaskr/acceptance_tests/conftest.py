"""Independent acceptance fixtures, frozen before the developer sees failures."""
import pytest

from flaskr import create_app
from flaskr.db import get_db, init_db
from werkzeug.security import generate_password_hash


@pytest.fixture
def app(tmp_path):
    app = create_app({"TESTING": True, "DATABASE": str(tmp_path / "judge.sqlite"), "SECRET_KEY": "judge-only"})
    with app.app_context():
        init_db()
        db = get_db()
        db.execute("INSERT INTO user (username, password) VALUES (?, ?)", ("author", generate_password_hash("password")))
        db.execute("INSERT INTO user (username, password) VALUES (?, ?)", ("reader", generate_password_hash("password")))
        db.commit()
    yield app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def seed(app):
    def insert(titles, bodies=None, created="2026-10-14 08:00:00"):
        with app.app_context():
            db = get_db()
            ids = []
            for index, title in enumerate(titles):
                cursor = db.execute("INSERT INTO post (title, body, author_id, created) VALUES (?, ?, 1, ?)",
                                    (title, bodies[index] if bodies else f"body-{index}", created))
                ids.append(cursor.lastrowid)
            db.commit()
            return ids
    return insert
