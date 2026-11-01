"""Start the registered TaskBoard WSGI app from the exact selected build tree."""
import argparse
import importlib
import json
import os
from pathlib import Path
import sys
from wsgiref.simple_server import make_server, WSGIRequestHandler


class Quiet(WSGIRequestHandler):
    def log_message(self, *args): pass


def main():
    parser = argparse.ArgumentParser()
    for name in ('project', 'database', 'port', 'ready'): parser.add_argument('--' + name, required=True)
    options = parser.parse_args()
    project = Path(options.project).resolve()
    sys.path.insert(0, str(project))
    module = importlib.import_module('taskboard.application')
    location = Path(module.__file__).resolve()
    if not location.is_relative_to(project): raise ValueError('Application import escaped build tree')
    application = module.create_application(Path(options.database))
    with make_server('127.0.0.1', int(options.port), application, handler_class=Quiet) as server:
        ready = Path(options.ready); temporary = ready.with_suffix('.tmp')
        temporary.write_text(json.dumps({'port': server.server_port, 'module': str(location),
            'project': str(project)}), encoding='utf-8')
        os.replace(temporary, ready)
        server.serve_forever()


if __name__ == '__main__': main()
