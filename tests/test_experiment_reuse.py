from copy import deepcopy

import pytest

from testfix.experiment_reuse import FOUNDATION_SKILLS, FrozenFoundationProvider
from testfix.skills import load_catalog


class Upstream:
    def __init__(self): self.requests=[];self.last_trace=None
    def invoke_traced(self,skill,context,schema,**kwargs):
        self.requests.append((skill,context))
        self.last_trace={'skill_id':skill,'provider':'synthetic_real_transport_boundary','status':'completed','result':{'summary':'new'}}
        return {'summary':'new'},self.last_trace
    def metadata(self):return {'fixture':True}


def foundation():
    context={'source_revision':'source-hash','sources':{'app.py':'VALUE=1'},'public_requirements':[{'id':'R1'}]}
    return {'id':'a'*32,'kind':'autonomous_project','status':'completed',
        'provenance':{'skill_catalog':load_catalog().snapshot(),'source_revision':'source-hash'},
        'model_calls':[{'skill_id':skill,'status':'completed','invocation_id':skill,
                        'request':{'context':deepcopy(context)},'result':{'summary':'captured'}} for skill in FOUNDATION_SKILLS]}


def test_reuse_is_explicit_and_later_calls_are_fresh():
    upstream=Upstream();source=foundation();provider=FrozenFoundationProvider(upstream,source)
    context=source['model_calls'][0]['request']['context']
    schema={'properties':{'summary':{'type':'string'}}}
    assert provider.invoke('generate-tests',context,schema)=={'summary':'captured'}
    assert provider.last_trace['provider']=='frozen_foundation_reuse'
    assert provider.last_trace['usage']['total_tokens']==0
    assert provider.invoke('generate-tests',{**context,'test_quality_review':True},schema)=={'summary':'new'}
    assert len(upstream.requests)==1


def test_changed_source_cannot_reuse_foundation_output():
    source=foundation();provider=FrozenFoundationProvider(Upstream(),source)
    context=deepcopy(source['model_calls'][0]['request']['context']);context['sources']['app.py']='VALUE=2'
    with pytest.raises(ValueError,match='source or requirements changed'):
        provider.invoke('plan-workflow',context,{'properties':{'summary':{'type':'string'}}})


def test_rejected_foundation_is_not_accepted_as_experimental_control():
    source=foundation();source['status']='rejected'
    with pytest.raises(ValueError,match='accepted'):
        FrozenFoundationProvider(Upstream(),source)


def test_chained_foundation_without_original_context_is_rejected_up_front():
    source=foundation();provider=FrozenFoundationProvider(Upstream(),source)
    context=source['model_calls'][0]['request']['context']
    for skill in sorted(FOUNDATION_SKILLS):
        provider.invoke(skill,context,{'properties':{'summary':{'type':'string'}}})
    chained={**source,'model_calls':provider.snapshot_calls()}
    with pytest.raises(ValueError,match='original public context'):
        FrozenFoundationProvider(Upstream(),chained)


def test_public_quality_checkpoint_is_explicit_reuse_not_an_accepted_ablation():
    audits=[{'attempt':1,'model_audit':{'approved':False,'findings':['prior-public-issue']}}]
    source=foundation();source.update(status='failed',test_quality={'passed':False,'audits':audits},tasks=[{'history':[]}])
    with pytest.raises(ValueError,match='accepted'):
        FrozenFoundationProvider(Upstream(),source)
    provider=FrozenFoundationProvider(Upstream(),source,public_checkpoint=True)
    context=source['model_calls'][0]['request']['context']
    assert provider.invoke('generate-tests',context,{'properties':{'summary':{'type':'string'}}})=={'summary':'captured'}
    assert provider.last_trace['provider']=='public_checkpoint_reuse'
    assert provider.metadata()['reuse_purpose']=='public_phase_recovery'
    assert source['status']=='failed'
    assert provider.public_quality_history==audits
    provider.public_quality_history[0]['attempt']=99
    assert source['test_quality']['audits'][0]['attempt']==1


@pytest.mark.parametrize('evidence',[{'history':[{'candidate_sha256':'x'}]},{'acceptance':{'passed':1}},{'generated':{'passed':1}}])
def test_public_recovery_rejects_any_post_repair_or_acceptance_evidence(evidence):
    source=foundation();source.update(status='failed',test_quality={'passed':False},tasks=[evidence])
    with pytest.raises(ValueError,match='before repair'):
        FrozenFoundationProvider(Upstream(),source,public_checkpoint=True)


def test_public_checkpoint_cannot_change_the_user_request():
    source=foundation();source.update(status='failed',test_quality={'passed':False},tasks=[{'history':[]}])
    provider=FrozenFoundationProvider(Upstream(),source,public_checkpoint=True)
    context={**source['model_calls'][0]['request']['context'],'user_request':'different request'}
    with pytest.raises(ValueError,match='source or requirements changed'):
        provider.invoke('plan-workflow',context,{'properties':{'summary':{'type':'string'}}})


def employee_failure():
    source=foundation()
    source.update(status='failed',error='WCTPExecutionError: routing mismatch',tasks=[{'history':[],
        'regression_baseline':{'successful':True},'generated_baseline':{'runnable':True,'successful':False}}],
        collaboration={'rounds':[{'employees':[{'steps':[]}]}]})
    return source


def test_pre_tool_employee_checkpoint_reuses_only_public_foundation():
    source=employee_failure();upstream=Upstream()
    provider=FrozenFoundationProvider(upstream,source,employee_checkpoint=True)
    context=source['model_calls'][0]['request']['context']
    assert provider.invoke('generate-tests',context,{'properties':{'summary':{'type':'string'}}})=={'summary':'captured'}
    assert provider.last_trace['provider']=='public_checkpoint_reuse'
    provider.invoke('repair-code',context,{'properties':{'summary':{'type':'string'}}})
    assert len(upstream.requests)==1


@pytest.mark.parametrize('bad', ['post_tool','no_failure','post_repair','acceptance'])
def test_employee_checkpoint_cannot_reuse_post_tool_or_evaluated_result(bad):
    source=employee_failure()
    if bad=='post_tool': source['collaboration']['rounds'][0]['employees'][0]['steps']=[{'action':'read_file'}]
    elif bad=='no_failure': source['tasks'][0]['generated_baseline']['successful']=True
    elif bad=='post_repair': source['tasks'][0]['history']=[{'patched':True}]
    else: source['tasks'][0]['acceptance']={'passed':1}
    with pytest.raises(ValueError,match='pre-tool checkpoint'):
        FrozenFoundationProvider(Upstream(),source,employee_checkpoint=True)
