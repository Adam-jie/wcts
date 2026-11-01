"""Reuse a verified public foundation for controlled search ablations.

Reused outputs are explicitly labelled and incur zero *new* inference tokens.
New repair, branch planning, test audits and review calls use the real provider.
No reference implementation or independent acceptance enters this wrapper.
"""
from copy import deepcopy
import hashlib
import json
import threading
import uuid

from .skills import load_catalog


FOUNDATION_SKILLS = {'plan-workflow','explore-repository','ground-code','analyze-requirements','generate-tests'}


class FrozenFoundationProvider:
    name = 'frozen_foundation_with_real_search'

    def __init__(self, upstream, foundation, *, public_checkpoint=False, employee_checkpoint=False):
        if public_checkpoint and employee_checkpoint:
            raise ValueError('Choose one recovery checkpoint type')
        self.public_checkpoint=public_checkpoint or employee_checkpoint
        self.employee_checkpoint=employee_checkpoint
        if employee_checkpoint:
            tasks = foundation.get('tasks', [])
            if (foundation.get('kind') != 'autonomous_project' or foundation.get('status') != 'failed'
                    or foundation.get('search_report') or len(tasks) != 1
                    or not str(foundation.get('error', '')).startswith('WCTPExecutionError:')
                    or tasks[0].get('history') or tasks[0].get('acceptance') or tasks[0].get('generated')
                    or not tasks[0].get('regression_baseline', {}).get('successful')
                    or not tasks[0].get('generated_baseline', {}).get('runnable')
                    or tasks[0].get('generated_baseline', {}).get('successful') is not False
                    or any(e.get('steps') for r in foundation.get('collaboration', {}).get('rounds', []) for e in r.get('employees', []))):
                raise ValueError('Employee recovery requires a failed pre-tool checkpoint with unchanged source and valid failing public tests')
        elif public_checkpoint:
            if (foundation.get('kind') != 'autonomous_project' or foundation.get('status') != 'failed'
                    or foundation.get('test_quality',{}).get('passed') is not False
                    or foundation.get('search_report')
                    or any(task.get('history') or task.get('acceptance') or task.get('generated') for task in foundation.get('tasks',[]))):
                raise ValueError('Public recovery requires a failed test-quality checkpoint before repair or independent verification')
        elif foundation.get('status') != 'completed' or foundation.get('kind') != 'autonomous_project':
            raise ValueError('Ablation foundation must be an accepted engineering run')
        if foundation['provenance']['skill_catalog'] != load_catalog().snapshot():
            raise ValueError('Foundation Skill catalog differs from the current implementation')
        self.upstream, self.foundation = upstream, deepcopy(foundation)
        self.public_quality_history = deepcopy(foundation.get('test_quality',{}).get('prior_run_audits',[])
            + foundation.get('test_quality',{}).get('audits',[])) if public_checkpoint else []
        self._calls, self._used = [], set()
        self._lock, self._local = threading.RLock(), threading.local()
        self.outputs = {}
        for skill in FOUNDATION_SKILLS:
            choices = [call for call in foundation['model_calls'] if call['skill_id'] == skill
                       and call.get('status') == 'completed' and isinstance(call.get('result'),dict)
                       and not call.get('request',{}).get('context',{}).get('test_quality_review')
                       and not call.get('request',{}).get('context',{}).get('assigned_task')
                       and not call.get('request',{}).get('context',{}).get('search_context')]
            if not choices:
                raise ValueError('Foundation lacks a successful public capability: ' + skill)
            self.outputs[skill] = choices[-1]
            if not isinstance(choices[-1].get('request',{}).get('context'),dict):
                raise ValueError('Foundation requires original public context; chained reused foundations are not supported: ' + skill)

    def invoke(self, skill, context, schema, **kwargs):
        self._local.trace = None
        reusable = (skill in self.outputs and skill not in self._used
                    and not any(context.get(key) for key in ('assigned_task','search_context','test_quality_review')))
        if reusable:
            captured = self.outputs[skill]
            original = captured['request']['context']
            if (context.get('source_revision') != self.foundation['provenance']['source_revision']
                    or context.get('sources') != original.get('sources')
                    or context.get('public_requirements') != original.get('public_requirements')
                    or self.public_checkpoint and context.get('user_request') != original.get('user_request')):
                raise ValueError('Foundation public source or requirements changed')
            result = {key:deepcopy(captured['result'][key]) for key in schema['properties']}
            trace = {'invocation_id':uuid.uuid4().hex,'skill_id':skill,'provider':'public_checkpoint_reuse' if self.public_checkpoint else 'frozen_foundation_reuse',
                     'status':'completed','result':result,'usage':{'total_tokens':0},
                     'usage_source':'no_new_inference','source_run_id':self.foundation['id'],
                     'source_invocation_id':captured['invocation_id'],
                     'request':{'context_sha256':hashlib.sha256(json.dumps(context,sort_keys=True).encode()).hexdigest(),
                                'output_schema':schema,'contract_version':'testfix.foundation-reuse.v1'}}
            self._used.add(skill)
        else:
            try:
                result,trace = self.upstream.invoke_traced(skill,context,schema,**kwargs)
            finally:
                trace = self.upstream.last_trace
                if trace is not None and trace.get('status') != 'completed':
                    with self._lock: self._calls.append(trace)
                    self._local.trace = trace
        with self._lock: self._calls.append(trace)
        self._local.trace = deepcopy(trace)
        return deepcopy(result)

    def invoke_traced(self, *args, **kwargs):
        return self.invoke(*args, **kwargs), self.last_trace

    @property
    def last_trace(self):
        return deepcopy(getattr(self._local,'trace',None))

    @property
    def calls(self):
        return self.snapshot_calls()

    def snapshot_calls(self):
        with self._lock: return deepcopy(self._calls)

    def metadata(self):
        return {**self.upstream.metadata(),'foundation_run_id':self.foundation['id'],
                'reuse_purpose':'public_phase_recovery' if self.public_checkpoint else 'paired_experimental_foundation',
                'reused_capabilities':sorted(self._used),'fresh_generation_scope':'search_planning_repair_audit_diagnosis_and_final_review',
                'foundation_reuse_is_new_inference':False}
