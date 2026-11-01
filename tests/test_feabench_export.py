import json

import pytest

from scripts.export_feabench_predictions import export


def test_exports_real_candidate_in_official_shape_with_lf(tmp_path):
    run = tmp_path / 'run'
    run.mkdir()
    (run / 'report.json').write_text(json.dumps({'instance_id': 'owner__repo-1',
                                                   'model_requested': 'model-a'}), encoding='utf-8')
    (run / 'candidate.patch').write_bytes(b'--- a/file.py\r\n+++ b/file.py\r\n')
    output = tmp_path / 'predictions.jsonl'
    assert export([run], output) == 1
    record = json.loads(output.read_text(encoding='utf-8'))
    assert record == {'instance_id': 'owner__repo-1', 'model_name_or_path': 'model-a',
                      'full_output': '', 'model_patch': '--- a/file.py\n+++ b/file.py\n'}
    with pytest.raises(FileExistsError):
        export([run], output)
    with pytest.raises(ValueError, match='Duplicate instance'):
        export([run, run], tmp_path / 'duplicate.jsonl')
