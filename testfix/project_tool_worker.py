"""Fixed tool worker; subprocess tree resource limits precede repository code."""
from __future__ import annotations

import json
from pathlib import Path
import sys

if __package__ in {None,''}:
    sys.path.insert(0,str(Path(__file__).resolve().parent.parent))

from testfix.resource_limits import apply_limits


def main():
    request_path = Path(sys.argv[1]).resolve()
    request = json.loads(request_path.read_text(encoding='utf-8'))
    guard = apply_limits(memory_bytes=1536*1024*1024,cpu_seconds=180)
    if request.get('adapter') == 'python_package':
        root = Path(request['root']).resolve()
        if not request_path.is_relative_to(root/'artifacts'):
            raise ValueError('Package tool request must stay within registered artifacts')
        from testfix.package_tools import run_package_operation
        run_package_operation(request,guard)
        return 0
    from testfix.config import Settings
    from testfix.project_eval import _run_suite,_runtime_files,_mapping_sha256,_sha256
    from testfix.project_delivery import verify_delivery
    root = Path(request['root']).resolve()
    paths = {key:Path(request[key]).resolve() for key in ('baseline','candidate','generated','acceptance','python','output')}
    if not all(path.is_relative_to(root) for path in paths.values()) or not request_path.is_relative_to(root/'artifacts'):
        raise ValueError('Tool paths must stay within the registered local workspace')
    output = paths['output']
    directory = output.parent
    candidate_hash = _mapping_sha256({name:_sha256(paths['candidate']/name) for name in sorted(_runtime_files(paths['candidate']))})
    operation = request['operation']
    result = {'operation':operation,'candidate_sha256':candidate_hash,'limits':{'backend':guard.backend,'memory_bytes':guard.memory_bytes,'cpu_seconds':guard.cpu_seconds}}
    if operation in {'baseline_public','candidate_public'}:
        target = paths['baseline'] if operation == 'baseline_public' else paths['candidate']
        result['regression'] = _run_suite(paths['python'],target,target/'tests',directory,operation+'-original')
        result['generated'] = _run_suite(paths['python'],target,paths['generated'],directory,operation+'-generated')
    elif operation == 'verify':
        # Independent source is never passed through a model context.
        result['baseline'] = _run_suite(paths['python'],paths['baseline'],paths['acceptance'],directory,'independent-baseline')
        result['acceptance'] = _run_suite(paths['python'],paths['candidate'],paths['acceptance'],directory,'independent-candidate')
        delivery_manifest = directory/'delivery-manifest.json'
        delivery_manifest.write_text(json.dumps({'id':'Project/flaskr','candidate':str(paths['candidate']),'python':str(paths['python'])}),encoding='utf-8')
        result['delivery'] = verify_delivery(delivery_manifest,Settings(root=root),
                                            {'id':request['run_id'],'delivery_attempt':directory.name})
    else:
        raise ValueError('Unknown fixed operation')
    output.write_text(json.dumps(result,ensure_ascii=False,allow_nan=False),encoding='utf-8')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
