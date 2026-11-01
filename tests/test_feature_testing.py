from copy import deepcopy
import json
import threading
from types import SimpleNamespace
import uuid

from fastapi.testclient import TestClient
import pytest

from testfix.api import create_app
from testfix import feature_testing as module
from testfix.project_tools import ProjectWorkspace, run_project_tool
from testfix.config import Settings
from test_application_execution import imported


ANALYSIS = {'requirements': [{'id': 'U1', 'behavior': '搜索不存在的标题', 'criterion': '返回零条任务'}], 'assumptions': []}
PLAN = {'files': [{'path': 'test_feature.py', 'source': 'from taskboard.storage import Storage\ndef test_absent(tmp_path):\n    db=Storage(tmp_path/"db")\n    db.create("现有任务")\n    assert db.list("没有这个标题")["total"] == 0\n'}],
        'steps': [{'title': '无匹配搜索', 'file': 'test_feature.py', 'expected': '没有匹配时返回零条'}],
        'coverage': [{'requirement_id': 'U1', 'tests': ['test_feature.py::test_absent']}], 'assumptions': []}


class Provider:
    def __init__(self, plan=PLAN): self.plan=plan; self.calls=[]; self.contexts=[]; self.last_trace=None
    def invoke(self, skill, context, schema, **kwargs):
        self.contexts.append(deepcopy(context))
        self.last_trace={'skill_id': skill, 'usage': {'total_tokens': 1}, 'status': 'completed'}
        self.calls.append(self.last_trace)
        return deepcopy({'approved':True,'findings':[]} if context.get('quality_review') else ANALYSIS if skill=='analyze-requirements' else self.plan)
    def snapshot_calls(self): return deepcopy(self.calls)


def run_input(project, **extra):
    return {'id': uuid.uuid4().hex, 'kind': 'feature_test', 'status': 'queued', 'phase': 'queued',
            'project_id': project['id'], 'created_at': '2026-10-27T00:00:00Z', 'model': 'fixture-explicit',
            'description': '搜索不存在的中文任务应为零条', 'provider': 'codex_cli', 'iterations': 2, **extra}


def suite(passed=True):
    return {'total': 1, 'passed': int(passed), 'successful': passed, 'case_ids': ['feature::test'],
            'duplicate_case_ids': [], 'cases': [{'id': 'feature::test', 'status': 'passed' if passed else 'failure'}]}


@pytest.fixture
def project(tmp_path, monkeypatch):
    settings, _, _, project=imported(tmp_path)
    monkeypatch.setattr(module, 'get_project', lambda *args: project)
    return settings, project


def test_description_drives_real_steps_wctp_loops_and_preserves_source(project):
    settings, project=project
    run=run_input(project); provider=Provider(); calls=[]
    store=SimpleNamespace(lock=threading.RLock(),runs={})
    def tool(settings, workspace, operation, **kwargs):
        workspace.assert_integrity();calls.append((operation,kwargs['test_file']))
        return {'suite': suite(), 'candidate_sha256': workspace.candidate_hash()}
    module.execute_feature_test(run,settings,store,provider=provider,tool_runner=tool)
    assert run['status']=='completed', run.get('error')
    assert len(calls)==4
    assert all(sorted(calls[i:i+2],key=lambda c:c[1] or '')==[('feature_tests',None),('feature_tests','test_feature.py')] for i in (0,2))
    assert run['summary']=={'total':2,'accepted':2,'tested':4,'passed':4}
    assert len(provider.calls)==3 and all(c['user_feature_description']==run['description'] for c in provider.contexts)
    assert 'test_acceptance.py' not in json.dumps(provider.contexts)
    assert provider.contexts[-1]['public_requirements']==ANALYSIS['requirements']
    report=json.loads((settings.artifacts/run['id']/'wctp-feature/report.json').read_text(encoding='utf-8'))
    assert report['native_step_execution'] and report['completed_iterations']==2
    assert report['source_copied'] is False and 'step_Feature01' in report['case']['graph']
    assert run['candidate_sha256']==ProjectWorkspace(settings,uuid.uuid4().hex,project).candidate_hash()


def test_generated_plan_can_wait_for_user_review_before_execution(project):
    settings, project = project
    run = run_input(project, review_before_execution=True)
    provider = Provider()
    def must_not_run(*args, **kwargs):
        raise AssertionError('用户确认前不能执行测试')
    module.execute_feature_test(run, settings, SimpleNamespace(lock=threading.RLock(), runs={}),
                                provider=provider, tool_runner=must_not_run)
    assert run['status'] == 'awaiting_review', run.get('error')
    assert run['phase'] == 'review'
    assert run['test_plan'] == PLAN
    assert run['requirements'] == ANALYSIS['requirements']
    assert len(provider.calls) == 3
    assert not (settings.artifacts / run['id'] / 'wctp-feature' / 'report.json').exists()


def test_user_edited_steps_execute_with_wctp_without_new_model_calls(project):
    settings, project = project
    edited = deepcopy(PLAN)
    edited['steps'][0]['title'] = '用户调整后的搜索测试'
    run = run_input(project, edited_plan=edited, edited_requirements=deepcopy(ANALYSIS['requirements']),
                    edited_assumptions=[], parent_feature_id='a' * 32)
    provider = Provider()
    calls = []
    def tool(settings, workspace, operation, **kwargs):
        calls.append(kwargs['test_file'])
        return {'suite': suite(), 'candidate_sha256': workspace.candidate_hash()}
    module.execute_feature_test(run, settings, SimpleNamespace(lock=threading.RLock(), runs={}),
                                provider=provider, tool_runner=tool)
    assert run['status'] == 'completed', run.get('error')
    assert provider.calls == []
    assert calls == [None, 'test_feature.py', None, 'test_feature.py']
    assert run['test_plan']['steps'][0]['title'] == '用户调整后的搜索测试'
    assert run['test_plan_source'] == 'user_edited'
    assert run['summary']['passed'] == 4


def test_user_edited_plan_does_not_require_model_configuration(project, monkeypatch):
    settings, project = project
    run = run_input(project, edited_plan=deepcopy(PLAN), edited_requirements=ANALYSIS['requirements'],
                    edited_assumptions=[], parent_feature_id='a' * 32)
    def unavailable(*args, **kwargs):
        raise AssertionError('重新执行人工计划不应初始化模型')
    monkeypatch.setattr(module, 'FeatureModelProvider', unavailable)
    module.execute_feature_test(run, settings, SimpleNamespace(lock=threading.RLock(), runs={}),
                                tool_runner=lambda *args, **kwargs: {'suite': suite()})
    assert run['status'] == 'completed', run.get('error')
    assert run['model_calls'] == []


def test_user_edited_plan_runs_real_tests_and_reports_failure(project):
    settings, project = project
    edited = deepcopy(PLAN)
    edited['steps'][0]['title'] = '人工复测搜索'
    run = run_input(project, iterations=1, edited_plan=edited,
                    edited_requirements=deepcopy(ANALYSIS['requirements']),
                    edited_assumptions=[], parent_feature_id='b' * 32)
    provider = Provider()
    module.execute_feature_test(run, settings, SimpleNamespace(lock=threading.RLock(), runs={}), provider=provider)
    assert provider.calls == []
    assert run['status'] == 'rejected', run.get('error')
    assert run['test_plan_source'] == 'user_edited'
    assert run['summary']['tested'] == 7 and run['summary']['passed'] == 6
    report = json.loads((settings.artifacts / run['id'] / 'wctp-feature/report.json').read_text(encoding='utf-8'))
    assert report['native_step_execution'] and report['status'] == 'failed'


def test_edited_plan_rejects_a_different_source_version(project):
    settings, project = project
    run = run_input(project, edited_plan=deepcopy(PLAN), edited_requirements=ANALYSIS['requirements'],
                    edited_assumptions=[], edited_candidate_sha256='0' * 64, parent_feature_id='a' * 32)
    provider = Provider()
    def must_not_run(*args, **kwargs):
        raise AssertionError('测试执行不应启动')
    module.execute_feature_test(run, settings, SimpleNamespace(lock=threading.RLock(), runs={}),
                                provider=provider, tool_runner=must_not_run)
    assert run['status'] == 'failed' and '工程版本已改变' in run['error']
    assert not provider.calls


def test_assertion_failure_is_not_test_success_and_stops_later_rounds(project):
    settings, project=project;run=run_input(project);calls=[]
    store=SimpleNamespace(lock=threading.RLock(),runs={})
    def tool(*args, **kwargs):calls.append(kwargs['test_file']);return {'suite':suite(kwargs['test_file'] is None)}
    module.execute_feature_test(run,settings,store,provider=Provider(),tool_runner=tool)
    assert run['status']=='rejected' and run['summary']['accepted']==0
    assert set(calls)=={None,'test_feature.py'} and len(calls)==2 and run['summary']['passed']==1


def test_changed_case_collection_cannot_pass_loop(project):
    settings, project=project;run=run_input(project);calls=[]
    def tool(*args,**kwargs):
        calls.append(1);value=suite()
        if len(calls)>2:value['case_ids']=['different::test']
        return {'suite':value}
    module.execute_feature_test(run,settings,SimpleNamespace(lock=threading.RLock(),runs={}),provider=Provider(),tool_runner=tool)
    assert run['status']!='completed' and run['summary']['accepted']==1
    report=json.loads((settings.artifacts/run['id']/'wctp-feature/report.json').read_text(encoding='utf-8'))
    assert report['status']=='failed' and '身份' in report['error']


@pytest.mark.parametrize('mutation', ['coverage','missing_step','duplicate_step','fake_test','escape','syntax','empty_assert'])
def test_invalid_generated_plan_is_rejected_before_execution(project,mutation):
    settings,project=project;plan=deepcopy(PLAN)
    if mutation=='coverage':plan['coverage']=[]
    elif mutation=='missing_step':plan['steps'][0]['file']='test_missing.py'
    elif mutation=='duplicate_step':plan['steps']*=2
    elif mutation=='fake_test':plan['coverage'][0]['tests']=['test_feature.py::test_fake']
    elif mutation=='escape':plan['files'][0]['path']='../test_escape.py'
    elif mutation=='syntax':plan['files'][0]['source']='this is invalid python @@'
    elif mutation=='empty_assert':plan['files'][0]['source']='def test_absent():\n    pass\n'
    workspace=ProjectWorkspace(settings,uuid.uuid4().hex,project)
    with pytest.raises((ValueError,SyntaxError)):module.validate_plan(plan,ANALYSIS['requirements'],workspace)
    assert workspace.generated_seal is None and not list(workspace.generated.iterdir())


def test_real_guarded_worker_reports_product_assertion_failures(project):
    settings,project=project;workspace=ProjectWorkspace(settings,uuid.uuid4().hex,project)
    workspace.write_tests(PLAN)
    result=run_project_tool(settings,workspace,'feature_tests',test_file='test_feature.py',timeout_s=60)
    assert not result['suite']['successful'] and result['suite']['total']==1
    assert result['suite']['cases'][0]['status']=='failure'
    assert 'assert' in result['suite']['cases'][0]['message']
    regression=run_project_tool(settings,workspace,'feature_tests',timeout_s=60)
    assert regression['suite']['successful'] and regression['suite']['total']==6
    workspace.assert_integrity()
    with pytest.raises(ValueError):run_project_tool(settings,workspace,'feature_tests',test_file='../secret.py',timeout_s=60)


@pytest.fixture
def client(project,monkeypatch):
    settings,project=project;(settings.root/'web').mkdir(exist_ok=True)
    app=create_app(settings);submitted=[]
    monkeypatch.setattr(app.state.store.pool,'submit',lambda *args:submitted.append(args))
    monkeypatch.setattr(module,'project_provider_status',lambda *_:[{'id':'codex_cli','configured':True}])
    with TestClient(app) as browser:yield browser,app,project,submitted


def test_api_queues_durable_history_and_prevents_competing_jobs(client):
    browser,app,project,submitted=client
    body={'project_id':project['id'],'description':'搜索中文标题，不匹配时为零条'}
    response=browser.post('/api/feature-tests',json=body)
    assert response.status_code==202,response.text
    value=response.json();run=app.state.store.snapshot(value['id'])
    assert len(submitted)==1 and run['kind']=='feature_test' and run['model']=='gpt-5.6-luna'
    assert value['id'] in app.state.store._owned_run_ids
    assert browser.get('/api/feature-tests',params={'project_id':project['id']}).json()['runs'][0]['id']==value['id']
    assert browser.get('/api/feature-tests',params={'project_id':'other'}).json()['runs']==[]
    assert browser.post('/api/feature-tests',json=body).status_code==409
    assert browser.get('/api/runs/'+value['id']+'/report?format=md').status_code==200


@pytest.mark.parametrize('status', ['completed', 'awaiting_review'])
def test_api_saves_edited_plan_as_a_new_run(client, status):
    browser, app, project, submitted = client
    parent = run_input(project, status=status, test_plan=deepcopy(PLAN),
                       requirements=deepcopy(ANALYSIS['requirements']), analysis_assumptions=[],
                       candidate_sha256='c' * 64)
    app.state.store.runs[parent['id']] = deepcopy(parent)
    edited = deepcopy(PLAN)
    edited['steps'][0]['expected'] = '没有任务时为零条'
    response = browser.post('/api/feature-tests/' + parent['id'] + '/edited',
                            json={'plan': edited, 'iterations': 3})
    assert response.status_code == 202, response.text
    run = app.state.store.snapshot(response.json()['id'])
    assert run['parent_feature_id'] == parent['id']
    assert run['edited_plan'] == edited
    assert run['edited_candidate_sha256'] == parent['candidate_sha256']
    assert run['iterations'] == 3 and len(submitted) == 1
    assert app.state.store.snapshot(parent['id'])['test_plan'] == PLAN


@pytest.mark.parametrize('extra',[{'description':'   '},{'iterations':True},{'iterations':0},{'source_run_id':'../../escape'},{'provider':'replay'},{'execute_command':'anything'}])
def test_api_invalid_input_never_starts_model(client,extra):
    browser,app,project,submitted=client
    assert browser.post('/api/feature-tests',json={'project_id':project['id'],'description':'测试搜索',**extra}).status_code==422
    assert submitted==[]


def test_unavailable_provider_never_falls_back(client,monkeypatch):
    browser,app,project,submitted=client
    monkeypatch.setattr(module,'project_provider_status',lambda *_:[])
    response=browser.post('/api/feature-tests',json={'project_id':project['id'],'description':'测试搜索'})
    assert response.status_code==400 and submitted==[]


def test_test_only_skill_does_not_inherit_benchmark_gates_and_is_hash_bound(tmp_path,monkeypatch):
    path=tmp_path/'SKILL.md';path.write_text('Test only. User functionality defines assertions.',encoding='utf-8')
    monkeypatch.setattr(module,'SKILL_PATH',path)
    provider=module.FeatureModelProvider('openai',Settings(base_url='https://example.invalid',api_key='fixture'),profile='python_application')
    prompt=provider._instructions('generate-tests',module.PLAN_SCHEMA,1000)
    assert 'Test only.' in prompt and 'SQLite restart' not in prompt and 'all four' not in prompt
    assert provider.feature_skill_sha256 in prompt
    with pytest.raises(ValueError):provider._instructions('repair-code',{},1000)
    path.write_text('changed',encoding='utf-8')
    with pytest.raises(ValueError):provider._instructions('generate-tests',{},1000)


def test_wctp_ready_batch_obeys_single_test_worker_slot(project):
    import time
    settings,project=project;run=run_input(project,iterations=1)
    lock=threading.Lock();live=0;peak=0
    def tool(*args,**kwargs):
        nonlocal live,peak
        with lock:live+=1;peak=max(peak,live)
        time.sleep(.03)
        with lock:live-=1
        return {'suite':suite()}
    module.execute_feature_test(run,settings,SimpleNamespace(lock=threading.RLock(),runs={}),provider=Provider(),tool_runner=tool)
    assert run['status']=='completed' and peak==1


def test_accepted_target_copies_source_only_and_keeps_old_tests_unchanged(project,monkeypatch):
    settings,project=project;source=ProjectWorkspace(settings,uuid.uuid4().hex,project)
    monkeypatch.setattr('testfix.wctp_workbench.get_project',lambda *_:project)
    source.write_tests({'files':[{'path':'test_old.py','source':'def test_old():\n    assert True\n'}]})
    path=source.candidate/'taskboard/storage.py'
    source.apply_patch({'baseline_sha256':source.candidate_hash(),'files':[{'path':'taskboard/storage.py','source':path.read_text(encoding='utf-8')+'\n# accepted-version fixture\n'}]})
    old={'id':source.directory.name,'kind':'autonomous_project','status':'completed','project_id':project['id'],
         'summary':{'accepted':1},'tasks':[{'candidate_sha256':source.candidate_hash()}]}
    run=run_input(project,source_run_id=old['id'])
    store=SimpleNamespace(snapshot=lambda _:old)
    target=module.feature_workspace(settings,store,run,project)
    assert target.candidate_hash()==source.candidate_hash()
    assert target.generated_seal is None and not list(target.generated.iterdir())
    source.assert_integrity()


def test_invalid_plan_gets_one_evidence_based_correction_before_freezing(project):
    settings,project=project;run=run_input(project,iterations=1)
    class CorrectingProvider(Provider):
        def invoke(self,skill,context,schema,**kwargs):
            value=super().invoke(skill,context,schema,**kwargs)
            if skill=='generate-tests' and len(self.calls)==2:value['steps']*=2
            return value
    provider=CorrectingProvider()
    module.execute_feature_test(run,settings,SimpleNamespace(lock=threading.RLock(),runs={}),provider=provider,
        tool_runner=lambda *args,**kwargs:{'suite':suite()})
    assert run['status']=='completed' and len(provider.calls)==4
    assert provider.contexts[-2]['validation_error']
    assert provider.contexts[-2]['public_requirements']==ANALYSIS['requirements']
    assert len(run['generation_corrections'])==1


def test_rejected_quality_audit_never_freezes_or_executes_invalid_tests(project):
    settings,project=project;run=run_input(project)
    class RejectingProvider(Provider):
        def invoke(self,skill,context,schema,**kwargs):
            value=super().invoke(skill,context,schema,**kwargs)
            return {'approved':False,'findings':['counterexample: fixture has seeded rows']} if context.get('quality_review') else value
    calls=[]
    module.execute_feature_test(run,settings,SimpleNamespace(lock=threading.RLock(),runs={}),provider=RejectingProvider(),
        tool_runner=lambda *args,**kwargs:calls.append(1))
    assert run['status']=='failed' and calls==[] and len(run['test_quality_audits'])==3
    assert not (settings.artifacts/run['id']/'wctp-feature').exists()


def test_format_correction_and_quality_correction_both_precede_execution(project):
    settings, project = project
    run = run_input(project, iterations=1)
    class CorrectingProvider(Provider):
        def __init__(self):
            super().__init__()
            self.generation_count = 0
            self.audit_count = 0
        def invoke(self, skill, context, schema, **kwargs):
            value = super().invoke(skill, context, schema, **kwargs)
            if skill == 'generate-tests' and not context.get('quality_review'):
                self.generation_count += 1
                if self.generation_count == 1:
                    value['steps'] *= 2
            elif context.get('quality_review'):
                self.audit_count += 1
                if self.audit_count == 1:
                    return {'approved': False, 'findings': ['fixture must account for seeded records']}
            return value
    provider = CorrectingProvider()
    module.execute_feature_test(run, settings, SimpleNamespace(lock=threading.RLock(), runs={}),
        provider=provider, tool_runner=lambda *args, **kwargs: {'suite': suite()})
    assert run['status'] == 'completed'
    assert provider.generation_count == 3 and provider.audit_count == 2
    assert len(run['generation_corrections']) == 2
    assert 'seeded records' in run['generation_corrections'][1]['reason']
