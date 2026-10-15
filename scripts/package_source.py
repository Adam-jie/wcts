"""Package reviewed source and pinned fixtures; never package local credentials."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import zipfile


ROOT = Path(__file__).resolve().parent.parent
TREES = ('testfix', 'web', 'scripts', 'docs', 'agentarts', 'examples', 'tests', 'experiments')
TOP_FILES = ('README.md', 'pyproject.toml', 'uv.lock', '.env.example', '.gitignore', '.gitattributes',
             'LICENSE', 'THIRD_PARTY_NOTICES.md', 'CONTRIBUTING.md', 'SECURITY.md')
EXCLUDED = {'__pycache__', '.pytest_cache', 'instance', 'build', 'dist', '.git', 'node_modules'}


def package(destination: Path) -> dict:
    files = {}
    for name in TOP_FILES:
        path = ROOT / name
        if path.is_file():
            files[name] = path
    for tree in TREES:
        for path in sorted((ROOT / tree).rglob('*')):
            relative = path.relative_to(ROOT)
            if any(part in EXCLUDED or part.startswith('.venv') or
                   part.startswith('.env') or part.endswith('.egg-info') for part in relative.parts):
                continue
            if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
                raise ValueError('Source package refuses linked inputs: ' + str(relative))
            if path.is_file() and path.suffix not in {'.pyc', '.pyo', '.sqlite', '.db', '.log'} and not path.name.startswith('.coverage'):
                files[relative.as_posix()] = path
    manifest = {name: hashlib.sha256(path.read_bytes()).hexdigest() for name,path in sorted(files.items())}
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name,path in sorted(files.items()):
            archive.write(path, name)
        archive.writestr('SOURCE-MANIFEST.json', json.dumps(manifest,ensure_ascii=False,indent=2))
    with zipfile.ZipFile(destination) as archive:
        assert all(hashlib.sha256(archive.read(name)).hexdigest() == digest for name,digest in manifest.items())
    receipt = {'path':str(destination.relative_to(ROOT)), 'files':len(files),
               'sha256':hashlib.sha256(destination.read_bytes()).hexdigest(), 'manifest':manifest}
    destination.with_suffix('.manifest.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf-8')
    return {key:value for key,value in receipt.items() if key!='manifest'}


if __name__ == '__main__':
    print(json.dumps(package(ROOT/'artifacts/release-v0.2.0/testfix-agent-v0.2.0-source.zip'),ensure_ascii=False,indent=2))
