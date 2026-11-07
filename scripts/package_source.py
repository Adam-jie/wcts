"""Package reviewed source and pinned fixtures; never package local credentials."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import zipfile


ROOT = Path(__file__).resolve().parent.parent
TREES = ('testfix', 'web', 'frontend', 'desktop', 'scripts', 'docs', 'agentarts', 'examples', 'tests', 'experiments', 'third_party')
TOP_FILES = ('README.md', 'pyproject.toml', 'uv.lock', '.env.example', '.gitignore', '.dockerignore',
             '.gitattributes', '.python-version', 'LICENSE', 'THIRD_PARTY_NOTICES.md', 'CONTRIBUTING.md', 'SECURITY.md')
EXCLUDED = {'__pycache__', '.pytest_cache', 'instance', 'build', 'dist', '.git', 'node_modules', '.runtime-cache'}


def source_files(root: Path = ROOT) -> dict[str, Path]:
    files = {}
    for name in TOP_FILES:
        path = root / name
        if path.is_file():
            files[name] = path
    for tree in TREES:
        for directory, directories, names in os.walk(root / tree):
            directories[:] = [name for name in directories if name not in EXCLUDED
                              and not name.startswith('.venv') and not name.endswith('.egg-info')]
            parent = Path(directory)
            if parent == root / 'experiments':
                directories[:] = [name for name in directories if name not in {'web-live', 'gpt56'}]
            for name in names:
                path = parent / name
                relative = path.relative_to(root)
                private_flaskr_capture = (relative.parts[:2] == ('experiments', 'flaskr')
                    and len(relative.parts) == 3 and path.suffix == '.json'
                    and name not in {'source.json', 'requirements.json'})
                richcli_capture = (relative.parts[:2] == ('experiments', 'rich-cli')
                    and ('baseline-evidence' in relative.parts or name.startswith('baseline-results.')
                         or name == 'upstream-source.zip'))
                if (name.startswith('.env') or name.startswith('.coverage') or name == '.DS_Store' or
                        name.startswith('credentials') or name == 'auth.json' or
                        path.suffix in {'.pyc', '.pyo', '.sqlite', '.sqlite3', '.db', '.log', '.pem', '.key'} or
                        private_flaskr_capture or richcli_capture or
                        relative.as_posix() == 'docs/prior-context.md' or
                        (tree == 'docs' and path.suffix in {'.docx', '.pdf'})):
                    continue
                if path.is_symlink():
                    raise ValueError('Source package refuses linked inputs: ' + str(relative))
                files[relative.as_posix()] = path
    return files


def package(destination: Path, *, root: Path = ROOT) -> dict:
    files = source_files(root)
    manifest = {name: hashlib.sha256(path.read_bytes()).hexdigest() for name,path in sorted(files.items())}
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        contents = {name: path.read_bytes() for name, path in files.items()}
        contents['SOURCE-MANIFEST.json'] = json.dumps(manifest,ensure_ascii=False,indent=2).encode('utf-8')
        for name, data in sorted(contents.items()):
            info = zipfile.ZipInfo(name)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data)
    with zipfile.ZipFile(destination) as archive:
        assert all(hashlib.sha256(archive.read(name)).hexdigest() == digest for name,digest in manifest.items())
    receipt = {'path':str(destination), 'files':len(files),
               'sha256':hashlib.sha256(destination.read_bytes()).hexdigest(), 'manifest':manifest}
    destination.with_suffix('.manifest.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf-8')
    return {key:value for key,value in receipt.items() if key!='manifest'}


if __name__ == '__main__':
    print(json.dumps(package(ROOT/'artifacts/release-v0.2.1/testfix-agent-v0.2.1-source.zip'),ensure_ascii=False,indent=2))
