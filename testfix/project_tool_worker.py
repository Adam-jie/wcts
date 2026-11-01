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
    if request.get('operation') == 'feature_tests':
        root = Path(request['root']).resolve()
        if not request_path.is_relative_to(root / 'artifacts' / request['run_id'] / 'tool_calls'):
            raise ValueError('Feature request must stay within its owned tool call')
        from testfix.feature_test_worker import run_step
        run_step(request, guard)
        return 0
    if request.get('adapter') == 'python_application':
        root = Path(request['root']).resolve()
        if not request_path.is_relative_to(root / 'artifacts'):
            raise ValueError('Application request must stay within run artifacts')
        from testfix.application_tools import run_application_operation
        run_application_operation(request, guard)
        return 0
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
    from testfix.wctp_case import run_test_actions, successful, functional_failure, delivery_success
    if operation in {'baseline_public','candidate_public'}:
        target = paths['baseline'] if operation == 'baseline_public' else paths['candidate']
        values, result['wctp'] = run_test_actions(directory, operation, {
            'step_Regression': ('原有回归', lambda: _run_suite(paths['python'],target,target/'tests',directory,operation+'-original'), successful, '原有回归全部通过'),
            'step_Generated': ('模型测试 · 原版对照' if operation == 'baseline_public' else '模型测试 · 修改版',
                lambda: _run_suite(paths['python'],target,paths['generated'],directory,operation+'-generated'),
                functional_failure if operation == 'baseline_public' else successful,
                '真实功能断言失败' if operation == 'baseline_public' else '冻结测试全部通过'),
        })
        result['regression'], result['generated'] = values['step_Regression'], values['step_Generated']
    elif operation == 'verify':
        def delivery():
            delivery_manifest = directory/'delivery-manifest.json'
            delivery_manifest.write_text(json.dumps({'id':'Project/flaskr','candidate':str(paths['candidate']),'python':str(paths['python'])}),encoding='utf-8')
            return verify_delivery(delivery_manifest,Settings(root=root), {'id':request['run_id'],'delivery_attempt':directory.name})
        values, result['wctp'] = run_test_actions(directory, operation, {
            'step_Baseline': ('原版独立验收对照', lambda: _run_suite(paths['python'],paths['baseline'],paths['acceptance'],directory,'independent-baseline'), functional_failure, '原版暴露功能缺口'),
            'step_Acceptance': ('修改版独立验收', lambda: _run_suite(paths['python'],paths['candidate'],paths['acceptance'],directory,'independent-candidate'), successful, '独立验收全部通过'),
            'step_Delivery': ('构建与实际运行', delivery, delivery_success, '构建与产品检查通过'),
        })
        result['baseline'], result['acceptance'], result['delivery'] = values['step_Baseline'], values['step_Acceptance'], values['step_Delivery']
    else:
        raise ValueError('Unknown fixed operation')
    output.write_text(json.dumps(result,ensure_ascii=False,allow_nan=False),encoding='utf-8')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
