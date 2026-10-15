from datetime import datetime

import pytest
from werkzeug.security import generate_password_hash

from flaskr import create_app
from flaskr.db import get_db, init_db


@pytest.fixture
def app(tmp_path):
    """Create an application and a fresh SQLite database for every test."""
    database = tmp_path / "test.sqlite"
    application = create_app(
        {"TESTING": True, "DATABASE": str(database), "SECRET_KEY": "test-secret"}
    )

    with application.app_context():
        init_db()
        db = get_db()
        db.executemany(
            "INSERT INTO user (id, username, password) VALUES (?, ?, ?)",
            [
                (1, "alice", generate_password_hash("alice-pass")),
                (2, "bob", generate_password_hash("bob-pass")),
            ],
        )
        db.commit()

    return application


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def add_posts(app):
    """Insert posts through the public tutorial schema and return inserted IDs."""

    def insert(rows):
        ids = []
        with app.app_context():
            db = get_db()
            for row in rows:
                created = row.get("created", datetime(2026, 1, 1, 12, 0, 0))
                if isinstance(created, datetime):
                    created = created.isoformat(sep=" ")
                cursor = db.execute(
                    "INSERT INTO post (author_id, created, title, body) "
                    "VALUES (?, ?, ?, ?)",
                    (
                        row.get("author_id", 1),
                        created,
                        row["title"],
                        row.get("body", "body"),
                    ),
                )
                ids.append(cursor.lastrowid)
            db.commit()
        return ids

    return insert


@pytest.fixture
def login(client):
    def perform(username="alice", password="alice-pass"):
        return client.post(
            "/auth/login", data={"username": username, "password": password}
        )

    return perform
