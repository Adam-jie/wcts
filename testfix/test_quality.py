"""Concrete checks of generated HTML helper behavior before suite freezing.

This checks a recognized helper interface, not arbitrary test correctness. A
fresh model audit complements it. Business tests and independent acceptance
remain separate. Generated code runs in a deadline-bound subprocess only.
"""
from __future__ import annotations
import json
from pathlib import Path
import subprocess
import sys
import tempfile


AUDIT_SCHEMA = {'type':'object','properties':{
    'approved':{'type':'boolean'},
    'findings':{'type':'array','items':{'type':'object','properties':{
        'path':{'type':'string'}, 'problem':{'type':'string'}, 'counterexample':{'type':'string'}},
        'required':['path','problem','counterexample'],'additionalProperties':False}},
    'summary':{'type':'string'}},'required':['approved','findings','summary'],'additionalProperties':False}


def probe_html_helpers(files):
    worker = Path(__file__).with_name('test_quality_worker.py').resolve()
    from .project_eval import _environment
    with tempfile.TemporaryDirectory(prefix='testfix-test-quality-') as directory:
        try:
            completed = subprocess.run([sys.executable,'-I','-B',str(worker)], input=json.dumps(files),
                cwd=directory, env=_environment(), capture_output=True, text=True, encoding='utf-8', timeout=12)
        except subprocess.TimeoutExpired:
            return {'checked':0,'defects':[{'path':'generated helpers','problem':'helper probe exceeded deadline',
                                           'counterexample':'standard two-link HTML parsing did not complete'}]}
    if completed.returncode:
        return {'checked':0,'defects':[{'path':'generated helpers','problem':'helper probe failed',
                                       'counterexample':'inspect generated helper syntax and imports'}]}
    return json.loads(completed.stdout)
