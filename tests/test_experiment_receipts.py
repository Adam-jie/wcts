"""Token receipt normalization: incomplete usage must remain unknown."""
import importlib.util
from pathlib import Path

import pytest

spec=importlib.util.spec_from_file_location('search_receipts',Path(__file__).parents[1]/'scripts/collect-search-experiment.py')
receipts=importlib.util.module_from_spec(spec)
spec.loader.exec_module(receipts)


@pytest.mark.parametrize('usage,expected',[
    ({'total_tokens':19},19),
    ({'prompt_tokens':12,'completion_tokens':7},19),
    ({'input_tokens':12,'output_tokens':7},19),
    ({'total_tokens':0},0),
    ({'input_tokens':12},None),
    ({'total_tokens':True},None),
    ({},None),(None,None),
])
def test_usage_variants_retain_known_and_unknown_distinction(usage,expected):
    assert receipts.tokens({'usage':usage})==expected
