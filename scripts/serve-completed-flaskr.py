"""Serve an accepted, hash-checked Flaskr candidate using a separate demo DB."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import re
import secrets
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from testfix.project_eval import _mapping_sha256, _runtime_files, _sha256
from testfix.reports import save_run


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', required=True)
    parser.add_argument('--port', type=int, default=8774)
    args = parser.parse_args()
    if not re.fullmatch('[0-9a-f]{32}', args.run) or not 1024 <= args.port <= 65535:
        parser.error('Use a valid accepted run ID and unprivileged port')
    directory = ROOT/'artifacts'/args.run
    report = json.loads((directory/'report.json').read_text(encoding='utf-8'))
    if report.get('status') != 'completed' or report.get('project_id') != 'Project/flaskr':
        parser.error('Only an accepted Flaskr run can be previewed')
    candidate = directory/'workspace/candidate'
    digest = _mapping_sha256({name:_sha256(candidate/name) for name in sorted(_runtime_files(candidate))})
    if digest != report['tasks'][0]['candidate_sha256']:
        parser.error('Candidate changed after acceptance')
    preview = directory/'preview'
    preview.mkdir(exist_ok=True)
    database = preview/'demo.sqlite'
    fresh = not database.exists()
    sys.path.insert(0, str(candidate))
    from flaskr import create_app
    app = create_app({'DATABASE':str(database), 'SECRET_KEY':secrets.token_hex(32), 'TESTING':False})
    if fresh:
        from flaskr.db import init_db
        with app.app_context():
            init_db()
        with app.test_client() as client:
            assert client.post('/auth/register', data={'username':'demo','password':'testfix-demo'}).status_code == 302
            assert client.post('/auth/login', data={'username':'demo','password':'testfix-demo'}).status_code == 302
            for number in range(1,14):
                assert client.post('/create', data={'title':f'TestFix engineering {number:02d}',
                    'body':f'Article {number}: detail, literal search and five-item pagination verified on this candidate.'}).status_code == 302
    from werkzeug.serving import make_server
    server = make_server('127.0.0.1', args.port, app)
    url = f'http://127.0.0.1:{args.port}/'
    report['preview'] = {'url':url, 'candidate_sha256':digest, 'database':'isolated demo database',
                         'demonstration_user':'demo', 'demonstration_password':'testfix-demo'}
    save_run(directory, report)
    print(json.dumps({'url':url,'candidate_sha256':digest,'run':args.run}),flush=True)
    server.serve_forever()


if __name__ == '__main__':
    main()
