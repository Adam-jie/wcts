import hashlib
import json
from zipfile import ZipFile

from scripts.package_source import package


def test_source_package_contains_app_components_and_omits_local_data(tmp_path):
    root = tmp_path / 'source'
    public = ['README.md', 'pyproject.toml', '.env.example', 'LICENSE',
              'frontend/package.json', 'desktop/src/main.cjs', 'web/implementation/app.js',
              'testfix/_vendor/wctp/src/engine/graph_executor.py']
    private = ['.env', 'docs/报名表.docx', 'docs/prior-context.md', 'desktop/node_modules/local.js',
               'frontend/node_modules/local.js', 'desktop/.runtime-cache/runtime.zip',
               'experiments/web-live/candidate/local.py', 'testfix/__pycache__/local.pyc']
    for name in public + private:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(name, encoding='utf-8')
    first = package(tmp_path / 'first.zip', root=root)
    second = package(tmp_path / 'second.zip', root=root)
    assert first['sha256'] == second['sha256']
    with ZipFile(first['path']) as archive:
        assert set(archive.namelist()) == set(public) | {'SOURCE-MANIFEST.json'}
        manifest = json.loads(archive.read('SOURCE-MANIFEST.json'))
        assert all(hashlib.sha256(archive.read(name)).hexdigest() == digest
                   for name, digest in manifest.items())
