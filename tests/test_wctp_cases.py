import json
from pathlib import Path
import pytest
import yaml

from testfix.wctp_case import execute_case, functional_failure
from testfix.wctp_runtime import SOURCE_SHA256
from testfix.wctp_runtime import WCTPGraphRunner


def test_native_original_steps_fork_join_loop_and_receipts(tmp_path):
    calls = []
    values, report = execute_case(tmp_path / 'case', {
        'step_Read': ('读取', lambda: calls.append('read') or {'successful': True}, lambda r: r['successful'], '真实读取成功'),
        'step_Write': ('写入', lambda: calls.append('write') or {'successful': True}, lambda r: r['successful'], '真实写入成功'),
    }, title='原框架循环验证', iterations=3)
    assert report['status'] == 'passed', report
    assert report['completed_iterations'] == 3 and len(calls) == 6
    assert report['source_sha256'] == SOURCE_SHA256 and report['native_step_execution'] is True
    assert len([r for r in report['results'] if r['step_id'] == 'step_Read']) == 3
    assert any(r['step_id'] == 'step_Loop' and r['status'] is False and r['control'] for r in report['results'])
    assert json.loads((tmp_path / 'case/report.json').read_text(encoding='utf-8')) == report
    assert 'step_Join --> step_Loop' in (tmp_path / 'case/case.yaml').read_text(encoding='utf-8')
    exported = yaml.safe_load((tmp_path / 'case/case.yaml').read_text(encoding='utf-8'))
    core = WCTPGraphRunner().core
    unbound = core['tcm.step_registry'].StepRegistry()
    unbound.register_inline(exported['inline_steps'])
    graph = core['tcm.case_parser'].parse_graph(exported['case_id'], exported['graph'])
    try:
        errors = core['tcm.case_parser'].validate_graph(graph, unbound)
    except core['tcm.step_registry'].ConfigError:
        errors = ['unregistered host action']
    assert errors, 'unbound export must never silently pass as no-op actions'


def test_native_failure_stops_repetition_and_keeps_failed_batch(tmp_path):
    calls = []
    _, report = execute_case(tmp_path / 'case', {
        'step_Fail': ('失败', lambda: {'successful': False}, lambda r: r['successful'], '全部通过'),
        'step_Peer': ('另一个分支', lambda: calls.append(1) or True, bool, '成功'),
    }, title='失败不能伪装成通过', iterations=3)
    assert report['status'] == 'failed' and report['completed_iterations'] == 0
    assert calls == [1]
    assert any(r['step_id'] == 'step_Fail' and not r['status'] for r in report['results'])
    assert not any(r['step_id'] == 'step_End' for r in report['results'])


def test_native_action_error_is_reported(tmp_path):
    def fail(): raise RuntimeError('transport unavailable')
    _, report = execute_case(tmp_path / 'case', {'step_Fail': ('故障', fail, bool, '完成')}, title='异常')
    assert report['status'] == 'failed' and 'transport unavailable' in report['error']


def test_native_sequential_action_edges_do_not_queue_workers_inside_step_timeout(tmp_path):
    calls=[]
    _,report=execute_case(tmp_path/'serial',{
        'step_First':('前置',lambda:calls.append('first') or True,bool,'完成'),
        'step_Second':('后续',lambda:calls.append('second') or True,bool,'完成'),
    },title='顺序动作',iterations=2,serial_actions=True)
    assert calls==['first','second','first','second'] and report['status']=='passed'
    assert 'step_First --> step_Second' in report['case']['graph']
    assert 'step_Dispatch --> step_Second' not in report['case']['graph']


@pytest.mark.parametrize('iterations', [0, 101, True, 1.5])
def test_native_loop_rejects_invalid_total_before_execution(tmp_path, iterations):
    with pytest.raises(ValueError):
        execute_case(tmp_path / 'case', {'step_Test': ('测试', lambda: True, bool, '成功')}, title='循环', iterations=iterations)
    assert not (tmp_path / 'case').exists()
