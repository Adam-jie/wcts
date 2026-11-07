"""Start TaskBoard on loopback. No third-party dependencies are required."""
import argparse
from pathlib import Path
from wsgiref.simple_server import make_server

from taskboard.application import create_application

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='TaskBoard 本地演示服务')
    parser.add_argument('--port', type=int, default=8878)
    parser.add_argument('--db', type=Path, default=Path(__file__).resolve().parent / 'var/tasks.sqlite3')
    args = parser.parse_args()
    with make_server('127.0.0.1', args.port, create_application(args.db)) as server:
        print(f'TaskBoard: http://127.0.0.1:{server.server_port}', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
