"""CLI request boundaries use a stub controller and never start a model."""
from __future__ import annotations

import sys

import pytest

from testfix import cli, project_tools, project_workflow
from testfix.config import Settings


@pytest.fixture
def request_cli(monkeypatch, tmp_path):
    calls = []
    settings = Settings(root=tmp_path)
    monkeypatch.setattr(cli, 'load_settings', lambda: settings)
    monkeypatch.setattr(project_tools, 'get_project', lambda current, identifier: {'adapter': 'python_package'})

    def execute(run, current):
        assert current is settings
        calls.append(run)
        return {**run, 'status': 'completed', 'summary': {'accepted': 1}}

    monkeypatch.setattr(project_workflow, 'execute_project_run', execute)

    def invoke(*arguments):
        monkeypatch.setattr(sys, 'argv', ['testfix', 'complete-project', '--project', 'Project/rich-cli', *arguments])
        with pytest.raises(SystemExit) as exit_info:
            cli.main()
        return exit_info.value.code

    return invoke, calls


def test_inline_request_is_passed_to_registered_project_team(request_cli):
    invoke, calls = request_cli
    assert invoke('--request', '  CSV 中的方括号要完整保留，并由测试和修复 Agent 协作。\n') == 0
    assert calls[0]['project_id'] == 'Project/rich-cli'
    assert calls[0]['collaboration_request'] == 'CSV 中的方括号要完整保留，并由测试和修复 Agent 协作。'
    assert calls[0]['provider'] == 'codex_cli'
    assert calls[0]['model'] == 'gpt-5.6-sol'
    assert calls[0]['budget']['wall_seconds'] == 7200


def test_utf8_file_request_is_read_without_modifying_file(request_cli, tmp_path):
    invoke, calls = request_cli
    path = tmp_path / 'request.txt'
    content = '\ufeff修复 CSV/TSV 字面量显示，保留 --print 的样式语义。\n'
    path.write_text(content, encoding='utf-8')
    before = path.read_bytes()
    assert invoke('--request-file', str(path)) == 0
    assert calls[0]['collaboration_request'] == content.lstrip('\ufeff').strip()
    assert path.read_bytes() == before


def test_no_request_preserves_original_controller_input(request_cli):
    invoke, calls = request_cli
    assert invoke() == 0
    assert 'collaboration_request' not in calls[0]
    assert calls[0]['budget']['wall_seconds'] == 5400


def test_explicit_wall_budget_is_kept_for_request_mode(request_cli):
    invoke, calls = request_cli
    assert invoke('--request', '修复 CSV 字面量', '--wall-seconds', '6100') == 0
    assert calls[0]['budget']['wall_seconds'] == 6100


@pytest.mark.parametrize('arguments', [
    ['--request', ''],
    ['--request', ' \n\t '],
    ['--request', '需' * 20_001],
    ['--request', 'repair', '--request-file', 'request.txt'],
])
def test_invalid_request_stops_before_loading_configuration_or_dispatch(monkeypatch, arguments):
    monkeypatch.setattr(sys, 'argv', ['testfix', 'complete-project', *arguments])

    def forbidden():
        pytest.fail('Invalid CLI input must not load configuration or start execution')

    monkeypatch.setattr(cli, 'load_settings', forbidden)
    with pytest.raises(SystemExit) as exit_info:
        cli.main()
    assert exit_info.value.code == 2


@pytest.mark.parametrize('content', [b'\xff\xfeinvalid utf8', b' \n\t ', b'x' * 20_001])
def test_invalid_file_request_never_dispatches(request_cli, tmp_path, content):
    invoke, calls = request_cli
    path = tmp_path / 'invalid.txt'
    path.write_bytes(content)
    assert invoke('--request-file', str(path)) == 2
    assert calls == []


def test_missing_request_file_never_dispatches(request_cli, tmp_path):
    invoke, calls = request_cli
    assert invoke('--request-file', str(tmp_path / 'missing.txt')) == 2
    assert calls == []


def test_exact_request_length_limit_is_accepted(request_cli):
    invoke, calls = request_cli
    assert invoke('--request', '需' * 20_000) == 0
    assert len(calls[0]['collaboration_request']) == 20_000
