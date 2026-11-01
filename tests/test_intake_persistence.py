import json
from pathlib import Path
import time

import pytest

from testfix.intake import IntakeStore, IntakeRequest


@pytest.mark.parametrize('permanent', [False, True])
def test_task_save_handles_windows_sharing_denial_without_losing_record(tmp_path, monkeypatch, permanent):
    store = IntakeStore(tmp_path)
    task = store.create(IntakeRequest(mode='idea', title='原任务', description='保留任务'))
    target = store.directory(task['id']) / 'task.json'
    old = target.read_bytes()
    attempts = []
    replace = Path.replace
    def denied(source, destination):
        if Path(destination) == target:
            attempts.append(source)
            if permanent or len(attempts) == 1:
                assert target.read_bytes() == old
                raise PermissionError('Windows sharing violation fixture')
        return replace(source, destination)
    monkeypatch.setattr(Path, 'replace', denied)
    monkeypatch.setattr(time, 'sleep', lambda _: None)
    updated = {**task, 'title': '更新后的任务'}
    if permanent:
        with pytest.raises(PermissionError): store.save(updated)
        assert target.read_bytes() == old
        assert 1 < len(attempts) <= 10
    else:
        store.save(updated)
        assert store.read(task['id'])['title'] == '更新后的任务'
        assert len(attempts) == 2
    assert not list(target.parent.glob('*.tmp'))
