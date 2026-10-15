"""Build and real HTTP acceptance for the registered Flaskr engineering case."""
from __future__ import annotations

import argparse
import hashlib
import http.cookiejar
import json
import os
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from pathlib import Path

from .project_eval import _environment, _manifest_path


def _stop(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def verify_delivery(manifest_path: Path, settings, run: dict) -> dict:
    """Test a built wheel, not an editable import; keep all evidence in the run."""
    started = time.monotonic()
    manifest_path = manifest_path.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    base, root = manifest_path.parent, settings.root.resolve()
    if manifest.get("id") != "Project/flaskr":
        raise ValueError("Delivery checks currently support the registered Flaskr case only")
    candidate = _manifest_path(base, manifest["candidate"], root, "candidate", directory=True)
    python = _manifest_path(base, manifest["python"], root, "python")
    directory = settings.artifacts / run["id"] / "delivery"
    attempt = run.get('delivery_attempt')
    if attempt is not None:
        if not isinstance(attempt,str) or not attempt or len(attempt)>100 or any(character not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for character in attempt):
            raise ValueError('Invalid delivery attempt identifier')
        directory = settings.artifacts / run['id'] / 'delivery-attempts' / attempt
    if not directory.resolve().is_relative_to(settings.artifacts.resolve()):
        raise ValueError('Delivery evidence escapes the artifact directory')
    directory.mkdir(parents=True, exist_ok=True)
    output = directory / "build"
    command = [str(python), "-m", "build", "--no-isolation", "--wheel", "--outdir", str(output), str(candidate)]
    env = _environment()
    env.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    record = {"build": {"command": command, "exit_code": None, "passed": False},
              "http": {"passed": 0, "total": 0, "successful": False, "checks": [], "requests": []}}
    process = None
    log = None
    try:
        build = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        record["build"]["exit_code"] = build.returncode
        (directory / "build.stdout.txt").write_text(build.stdout, encoding="utf-8")
        (directory / "build.stderr.txt").write_text(build.stderr, encoding="utf-8")
        wheels = list(output.glob("*.whl"))
        if build.returncode or len(wheels) != 1:
            raise AssertionError("Wheel build failed or did not produce exactly one wheel")
        wheel = wheels[0]
        unpacked = directory / "installed-wheel"
        with zipfile.ZipFile(wheel) as archive:
            names = archive.namelist()
            assert "flaskr/templates/blog/detail.html" in names, "Detail template missing from wheel"
            for name in names:
                destination = unpacked / name
                if not destination.resolve().is_relative_to(unpacked.resolve()):
                    raise ValueError("Wheel member escapes installation directory")
            archive.extractall(unpacked)
        record["build"].update(passed=True, wheel=str(wheel), sha256=hashlib.sha256(wheel.read_bytes()).hexdigest(), members=names)
        database = directory / "http.sqlite"
        with socket.socket() as bound:
            bound.bind(("127.0.0.1", 0))
            port = bound.getsockname()[1]
        url = f"http://127.0.0.1:{port}"
        worker = root / "testfix" / "project_http_worker.py"
        server_command = [str(python), str(worker), "--project", str(unpacked), "--database", str(database), "--port", str(port)]
        log = (directory / "server.log").open("w", encoding="utf-8")

        def start():
            server = subprocess.Popen(server_command, cwd=directory, env=env, stdout=log, stderr=subprocess.STDOUT,
                                      creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if server.poll() is not None:
                    raise RuntimeError("HTTP application exited before startup")
                try:
                    with urllib.request.urlopen(url + "/hello", timeout=1) as response:
                        if response.status == 200:
                            return server
                except (urllib.error.URLError, TimeoutError):
                    time.sleep(0.05)
            _stop(server)
            raise TimeoutError("HTTP application did not become ready")

        process = start()
        record["http"].update(command=server_command, base_url=url, database=str(database), tested_artifact="built wheel")
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

        def request(path, data=None):
            payload = urllib.parse.urlencode(data).encode() if data is not None else None
            req = urllib.request.Request(url + path, data=payload)
            try:
                response = opener.open(req, timeout=5)
            except urllib.error.HTTPError as error:
                response = error
            with response:
                text = response.read().decode("utf-8")
                status = response.code
                final_path = urllib.parse.urlsplit(response.geturl()).path
            record["http"]["requests"].append({"method": "POST" if data is not None else "GET", "path": path, "status": status, "final_path": final_path})
            return status, text, final_path

        def check(name, condition):
            record["http"]["total"] += 1
            record["http"]["checks"].append({"name": name, "passed": bool(condition)})
            if not condition:
                raise AssertionError(name)
            record["http"]["passed"] += 1

        def rows():
            with sqlite3.connect(database) as db:
                return db.execute("SELECT id,title,body FROM post ORDER BY created DESC,id DESC").fetchall()

        check("application and static asset start from built wheel", request("/")[0] == 200 and request("/static/style.css")[0] == 200)
        check("registration form loads", request("/auth/register")[0] == 200)
        check("registration persists and redirects to login", request("/auth/register", {"username": "author", "password": "local-example-password"})[2] == "/auth/login")
        check("login establishes real cookie session", request("/auth/login", {"username": "author", "password": "local-example-password"})[2] == "/")
        for number in range(13):
            check(f"create article {number} via HTTP", request("/create", {"title": f"Agent notes {number}", "body": f"Persisted body {number}"})[2] == "/")
        check("create unrelated article", request("/create", {"title": "Other subject", "body": "Agent appears only in body"})[2] == "/")
        check("SQLite contains all created articles", len(rows()) == 14)
        first = request("/")[1]
        check("index limits display to five articles", first.count('<article') == 5)
        query = request("/?q=Agent&page=2")
        check("filtered page two renders five matching articles", query[0] == 200 and query[1].count('<article') == 5 and "Other subject" not in query[1])
        check("last filtered page contains remaining three articles", request("/?q=Agent&page=3")[1].count('<article') == 3)
        identifier = rows()[-1][0]
        detail = request(f"/{identifier}")
        check("public detail is linked from title and displays body", detail[0] == 200 and "Persisted body" in detail[1] and f'href="/{identifier}"' in request("/?q=Agent&page=3")[1])
        check("author updates article through HTTP", request(f"/{identifier}/update", {"title": "Agent revised", "body": "Updated persistent body"})[2] == "/")
        check("updated content is rendered", "Updated persistent body" in request(f"/{identifier}")[1])
        unicode_identifier = rows()[0][0]
        request(f"/{unicode_identifier}/update", {"title": "Écho Σ", "body": "Unicode literal search"})
        for literal in ("Écho", "Σ"):
            literal_result = request("/?" + urllib.parse.urlencode({"q": literal}))
            check(f"identical non-ASCII literal search {literal}", literal_result[0] == 200 and literal_result[1].count('<article') == 1)
        request("/auth/logout")
        check("logged-out readers retain public detail access", request(f"/{identifier}")[0] == 200)
        check("logged-out edit is redirected to login", request(f"/{identifier}/update")[2] == "/auth/login")
        request("/auth/register", {"username": "reader", "password": "local-reader-password"})
        request("/auth/login", {"username": "reader", "password": "local-reader-password"})
        check("different user cannot edit article", request(f"/{identifier}/update")[0] == 403)
        check("different user cannot delete article", request(f"/{identifier}/delete", {})[0] == 403)
        check("missing detail returns 404", request("/999999")[0] == 404)
        check("invalid page returns 400", request("/?page=0")[0] == 400)
        check("out-of-range filtered page returns 404", request("/?q=Agent&page=99")[0] == 404)
        check("very large positive page returns 404", request("/?page=" + "9" * 5000)[0] == 404)
        injection = request("/?" + urllib.parse.urlencode({"q": "' OR 1=1 --"}))
        check("SQL-looking query remains literal text", injection[0] == 200 and injection[1].count('<article') == 0 and len(rows()) == 14)
        malicious = '<script>alert("local")</script>'
        escaped = request("/?" + urllib.parse.urlencode({"q": malicious}))
        check("search input escapes HTML", malicious not in escaped[1] and "&lt;script&gt;" in escaped[1])
        _stop(process)
        process = start()
        check("database and changed article survive process restart", "Updated persistent body" in request(f"/{identifier}")[1] and len(rows()) == 14)
        request("/auth/logout")
        check("stored account survives restart", request("/auth/login", {"username": "author", "password": "local-example-password"})[2] == "/")
        request(f"/{identifier}/delete", {})
        check("author delete persists and removes detail", len(rows()) == 13 and request(f"/{identifier}")[0] == 404)
        record["http"]["successful"] = True
    except Exception as exc:
        record["error"] = f"{type(exc).__name__}: {exc}"
        record["execution_error"] = isinstance(exc, (OSError, subprocess.TimeoutExpired, TimeoutError, RuntimeError))
    finally:
        if process is not None:
            _stop(process)
        if log is not None:
            log.close()
    record["duration_s"] = round(time.monotonic() - started, 3)
    (directory / "result.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return record
