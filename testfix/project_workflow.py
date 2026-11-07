"""Nine-capability autonomous completion with integer scheduling and evidence gates."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import time
import threading
import zipfile

from .config import DEFAULT_CODEX_MODEL
from .project_eval import _diff, _functional_failure, _paired_cases_match, _runtime_files
from .project_tools import ProjectWorkspace, confined, get_project, run_project_tool, project_profile
from .reports import now, save_run
from .scheduling import solve_plan
from .skills import load_catalog


CAPABILITY_NAMES = {'plan-workflow':'任务与约束规划','explore-repository':'仓库探索','ground-code':'代码实体定位',
                    'analyze-requirements':'需求与缺口分析','generate-tests':'自动化测试生成',
                    'diagnose-results':'失败证据诊断','repair-code':'工程补丁生成','review-patch':'独立代码评审','verify-delivery':'独立验收与交付'}
DEFAULT_BUDGET = {'wall_seconds':4500,'token_limit':800_000,'model_slots':1,'cpu_slots':2}


def default_budget(adapter='flaskr'):
    return {**DEFAULT_BUDGET, **({'wall_seconds':7200} if adapter=='python_application' else {'wall_seconds':5400} if adapter=='python_package' else {})}


OUTPUT_CAPS = {'plan-workflow':700,'explore-repository':900,'ground-code':1400,'analyze-requirements':1800,
               'generate-tests':6500,'diagnose-results':1800,'repair-code':10000,'review-patch':2400,'verify-delivery':0}


def obj(properties):
    return {'type':'object','properties':properties,'required':list(properties),'additionalProperties':False}


STRING = {'type':'string'}
STRINGS = {'type':'array','items':STRING}
FILES = {'type':'array','items':obj({'path':STRING,'source':STRING})}
SCHEMAS = {
    'plan-workflow':obj({'goals':{'type':'array','items':obj({'requirement_id':STRING,'goal':STRING})},'skill_ids':STRINGS,'rationale':STRING}),
    'explore-repository':obj({'candidates':{'type':'array','items':obj({'path':STRING,'query':STRING,'reason':STRING})},'exhausted':{'type':'boolean'}}),
    'ground-code':obj({'locations':{'type':'array','items':obj({'requirement_id':STRING,'path':STRING,'symbol':STRING,'evidence':STRING,'status':{'type':'string','enum':['located','partial','not_found']}})}}),
    'analyze-requirements':obj({'requirements':{'type':'array','items':obj({'id':STRING,'behavior':STRING,'state':{'type':'string','enum':['missing','partial','implemented','unknown']},'evidence':STRING,'criterion':STRING})},'assumptions':STRINGS}),
    'generate-tests':obj({'files':FILES,'steps':STRINGS,'coverage':{'type':'array','items':obj({'requirement_id':STRING,'tests':STRINGS})},'assumptions':STRINGS}),
    'diagnose-results':obj({'failures':{'type':'array','items':obj({'requirement_id':STRING,'cause':STRING,'evidence':STRING,'target_paths':STRINGS})},'summary':STRING}),
    'repair-code':obj({'baseline_sha256':STRING,'files':FILES,'reason':STRING}),
    'review-patch':obj({'approved':{'type':'boolean'},'candidate_sha256':STRING,'findings':{'type':'array','items':obj({'path':STRING,'severity':{'type':'string','enum':['high','medium','low']},'problem':STRING,'fix':STRING})},'summary':STRING}),
}


class BudgetExceeded(RuntimeError):
    pass


class ExecutionBudget:
    """Reserve before calls and reconcile reported usage; never invent token use.

    The CLI reports tokens only after a turn and exposes no hard output cap.
    Its per-call reservation is conservative public UTF-8 bytes + output cap;
    excess reported usage stops subsequent work and cannot be labelled success.
    """
    def __init__(self, values: dict):
        if set(values) != set(DEFAULT_BUDGET) or any(type(value) is not int for value in values.values()):
            raise ValueError('Budget must contain integer wall_seconds/token_limit/model_slots/cpu_slots')
        if values['wall_seconds'] < 60 or values['token_limit'] < 5000:
            raise ValueError('Wall/token budget is outside the supported local bounds')
        if not 1 <= values['model_slots'] <= 8 or not 1 <= values['cpu_slots'] <= 8:
            raise ValueError('Resource capacities must be 1–8')
        self.values = values.copy()
        self.started = time.monotonic()
        self.tokens_used, self.accounted_tokens, self.calls = 0, 0, 0
        self.usage_complete = True

    def remaining(self):
        remaining = self.values['wall_seconds'] - (time.monotonic()-self.started)
        if remaining <= 0:
            raise BudgetExceeded('Overall wall-time budget exhausted')
        return remaining

    def reserve(self, context: dict, output_cap: int):
        self.remaining()
        reservation = len(json.dumps(context,ensure_ascii=False).encode('utf-8')) + output_cap
        if self.accounted_tokens + reservation > self.values['token_limit']:
            raise BudgetExceeded('Insufficient token budget for the next conservative request reservation')
        return reservation

    def reconcile(self, call: dict, reservation: int):
        usage = call.get('usage') or {}
        total = usage.get('total_tokens')
        if type(total) is not int:
            incoming = usage.get('input_tokens',usage.get('prompt_tokens'))
            outgoing = usage.get('output_tokens',usage.get('completion_tokens'))
            total = incoming+outgoing if type(incoming) is int and type(outgoing) is int else None
        if total is None:
            self.usage_complete = False
            self.accounted_tokens += reservation
        else:
            self.tokens_used += total
            self.accounted_tokens += total
        self.calls += 1
        self.remaining()
        if self.accounted_tokens > self.values['token_limit']:
            raise BudgetExceeded('Reported model usage exceeded the budget; subsequent calls stopped')

    def report(self):
        return {**self.values,'elapsed_s':round(time.monotonic()-self.started,3), 'tokens_used':self.tokens_used if self.usage_complete else None,
                'reported_tokens':self.tokens_used,'accounted_tokens':self.accounted_tokens,'token_usage_complete':self.usage_complete,
                'calls':self.calls,'cli_token_limit':'reservation_and_post_turn_accounting_not_server_hard_limit'}


def invocation_allowance(catalog,skill_id,max_rounds):
    rounds=max_rounds if skill_id in {'diagnose-results','repair-code','review-patch','verify-delivery'} else 1
    corrections=1 if skill_id=='verify-delivery' else 2
    return min(catalog.get_contract(skill_id)['budgets']['max_attempts'],rounds*corrections)


def _team_allowance(catalog, skill_id, max_rounds):
    maximum = catalog.get_contract(skill_id)['budgets']['max_attempts']
    if skill_id in {'explore-repository','repair-code'}:
        return maximum
    if skill_id == 'plan-workflow':
        return min(maximum, max_rounds + 1)
    return invocation_allowance(catalog, skill_id, max_rounds)


def scheduling_options(catalog, max_rounds=2, strategy='balanced', input_bytes=0, profile='flaskr_project', collaboration=False, search_iterations=0, quality_attempts=0):
    if strategy not in {'balanced','fast','thorough'}:
        raise ValueError('Unknown strategy preference')
    options = []
    for skill_id in catalog.catalog['profiles'][profile]['required_skills']:
        contract = catalog.get_contract(skill_id)
        repeats = invocation_allowance(catalog,skill_id,max_rounds)
        if collaboration:
            repeats = _team_allowance(catalog,skill_id,max_rounds)
        if search_iterations and skill_id in {'plan-workflow','explore-repository','repair-code','diagnose-results'}:
            repeats += search_iterations * (4 if skill_id in {'explore-repository','repair-code'} else 1)
        if search_iterations and skill_id == 'generate-tests':
            repeats = 2 * quality_attempts + 2
        resources = {'model_slots':int(contract['kind']=='llm'),'cpu_slots':int(contract['kind']=='deterministic'),'candidate_write':int(skill_id=='repair-code')}
        for name,factor in [('focused',1),('expanded',2)]:
            if contract['kind']=='deterministic' and name=='expanded':
                continue
            if strategy=='fast' and name!='focused' or strategy=='thorough' and name!='expanded' and contract['kind']=='llm':
                continue
            # Configured allowances, not fabricated latency measurements. Both
            # strategies retain every final gate; expanded supplies more output.
            # Total-token forecasts include public input and configured model
            # overhead; reported total usage is separately reconciled at runtime.
            incoming=(input_bytes+2)//3+12000 if contract['kind']=='llm' else 0
            output_allowance = max(1800,OUTPUT_CAPS[skill_id]) if collaboration and skill_id=='plan-workflow' else OUTPUT_CAPS[skill_id]
            cap = (incoming+output_allowance*factor)*repeats
            duration = contract['budgets']['timeout_seconds']*1000*repeats
            # Public baseline/candidate suites are part of these capabilities,
            # so their fixed process deadlines also consume the integer plan.
            public_ms=240_000 if profile=='python_package' else 150_000
            if skill_id=='generate-tests':
                duration += public_ms
            elif skill_id=='repair-code':
                duration += public_ms*max_rounds
                if collaboration:
                    duration += public_ms*2  # At most two employee-requested public test runs.
            options.append({'skill_id':skill_id,'id':name,'duration_ms':duration,'token_cap':cap,'resources':resources,
                            'evidence':{'basis':'duration is a configured cumulative upper bound including bounded validation corrections; tokens forecast public UTF-8 bytes/3 + configured 12000-token transport overhead + output allowance; not measured performance or billing'}})
    return options


def _public_feedback(result: dict) -> dict:
    feedback = {'candidate_sha256':result['candidate_sha256']}
    for key in ('regression','generated'):
        suite = result[key]
        text = Path(suite['stdout_path']).read_text(encoding='utf-8',errors='replace')
        feedback[key] = {name:suite.get(name) for name in ('passed','total','case_ids','collection_errors','parse_error','binding_error','successful','timeout','exit_code')}
        feedback[key]['output_tail'] = text[-7000:]
    return feedback


def _regression_preserved(before,after):
    return (before['successful'] and after['successful'] and _paired_cases_match(before,after)
            and {case['id'] for case in before['cases'] if case['status']=='passed'} == {case['id'] for case in after['cases'] if case['status']=='passed'})


REQUIRED_HTTP_CHECKS = {
    'application and static asset start from built wheel','registration form loads',
    'registration persists and redirects to login','login establishes real cookie session',
    *[f'create article {number} via HTTP' for number in range(13)],
    'create unrelated article','SQLite contains all created articles','index limits display to five articles',
    'filtered page two renders five matching articles','last filtered page contains remaining three articles',
    'public detail is linked from title and displays body','author updates article through HTTP','updated content is rendered',
    'identical non-ASCII literal search Écho','identical non-ASCII literal search Σ',
    'logged-out readers retain public detail access','logged-out edit is redirected to login',
    'different user cannot edit article','different user cannot delete article','missing detail returns 404',
    'invalid page returns 400','out-of-range filtered page returns 404','very large positive page returns 404',
    'SQL-looking query remains literal text','search input escapes HTML',
    'database and changed article survive process restart','stored account survives restart',
    'author delete persists and removes detail',
}


def delivery_passes(delivery: dict, *, adapter='flaskr', required_checks=None) -> bool:
    """Validate concrete required checks, not just an optimistic aggregate flag."""
    build, http = delivery.get('build',{}),delivery.get('cli' if adapter=='python_package' else 'http',{})
    checks = http.get('checks',[])
    if not isinstance(checks,list) or any(not isinstance(check,dict) for check in checks):
        return False
    names = [check.get('name') for check in checks]
    expected=set(required_checks) if adapter in {'python_package','python_application'} and required_checks else REQUIRED_HTTP_CHECKS
    if adapter not in {'flaskr','python_package','python_application'} or adapter in {'python_package','python_application'} and not required_checks:
        return False
    return (build.get('passed') is True and http.get('successful') is True
            and len(names)==len(set(names))==len(expected) and set(names)==expected
            and all(check.get('passed') is True for check in checks)
            and type(http.get('total')) is int and http['total']==len(checks) and http.get('passed')==len(checks))


def execute_project_run(run: dict, settings, persist=None, *, provider=None, tool_runner=run_project_tool) -> dict:
    started = time.monotonic()
    run.update(kind='autonomous_project',runner='project_process',status='running',execution_owner_pid=os.getpid(),events=[],tasks=[],model_calls=[],
               workflow={'capabilities':[{'skill_id':key,'name':label,'status':'pending','attempt':0} for key,label in CAPABILITY_NAMES.items()]},
               summary={'total':1,'accepted':0,'regressions':0,'duration_s':0})
    directory = settings.artifacts/run['id']
    state = {item['skill_id']:item for item in run['workflow']['capabilities']}
    completed, artifacts = [], {}
    workspace, budget = None,None
    profile='flaskr_project'
    collaboration_request = run.get('collaboration_request')
    team_plan, team_schedule = None, None
    team_public_calls = 0
    control_lock = threading.RLock()
    legacy_provider_lock = threading.Lock()
    capability_started, active_model_calls = {}, {}
    invocation_budget_failure = False
    scope_invocations = {}
    manager_gate_invocations = 0
    employee_charges = {}
    search_settings = run.get('search') or {}
    search_config = None

    def begin_capability_timing(skill):
        capability_started.setdefault(skill,time.monotonic())
        state[skill].setdefault('started_at',now())
        state[skill].pop('finished_at',None)
        state[skill]['timing_basis']='first_start_to_latest_completion; per_call_traces_are_authoritative'

    def complete_capability_timing(skill):
        state[skill]['duration_s']=round(time.monotonic()-capability_started[skill],3)
        if not active_model_calls.get(skill,0):
            state[skill]['finished_at']=now()

    def checkpoint():
        with control_lock:
            run['updated_at'] = now()
            if budget:
                run['workflow']['budget'] = budget.report()
            run['summary']['duration_s'] = round(time.monotonic()-started,3)
            save_run(directory,run)
            if persist:
                persist(run)

    def event(skill,stage,message,**details):
        with control_lock:
            run['events'].append({'time':now(),'agent':skill,'stage':stage,'message':message,
                                 **{key:value for key,value in details.items()
                                    if key in {'task_id','round','sender','recipient','operation','ok','summary',
                                               'worker_id','worker_role','strategy','requirement_ids','depends_on',
                                               'paths','assignment_id','artifact_ids'}}})
            checkpoint()

    def artifact(skill, content, inputs, attempt):
        path = directory/'capabilities'/skill/f'attempt-{attempt}.json'
        path.parent.mkdir(parents=True,exist_ok=True)
        document = {'schema':'testfix.capability-artifact.v1','skill_id':skill,'attempt':attempt,'created_at':now(),
                    'source_revision':_source_revision,'candidate_sha256':workspace.candidate_hash(),
                    'input_artifacts':{key:artifacts[key]['sha256'] for key in inputs if key in artifacts},'content':content}
        path.write_text(json.dumps(document,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
        record = {'path':str(path.relative_to(settings.root)),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
        artifacts[skill] = record
        state[skill]['artifact'] = record
        return record

    def validate_model_output(skill,output):
        from .project_models import _validate_schema
        _validate_schema(output,model_schema(skill))
        if skill=='plan-workflow':
            if output['skill_ids']!=list(CAPABILITY_NAMES) or {item['requirement_id'] for item in output['goals']}!=set(requirement_ids):
                raise ValueError('Planner omitted or reordered core capabilities/requirements')
            if collaboration_request:
                from .team_planning import validate_team_plan
                validate_team_plan(output['team_plan'], requirement_ids, sorted(workspace.allowed),
                                   list(workspace.snapshot()['sources']))
        elif skill=='explore-repository':
            if not output['candidates']:
                raise ValueError('Exploration requires at least one real existing file')
            for item in output['candidates']:
                confined(workspace.candidate,item['path'],exists=True)
        elif skill=='ground-code':
            if not output['locations']:
                raise ValueError('Grounding requires real code evidence')
            for item in output['locations']:
                path=confined(workspace.candidate,item['path'])
                if not path.is_file() and not (item['status']=='not_found' and item['path'] in workspace.allowed):
                    raise ValueError('Grounding may label an allowed missing file not_found, but cannot claim it exists')
                if item['requirement_id'] not in requirement_ids:
                    raise ValueError('Unknown requirement ID in grounded location')
        elif skill=='analyze-requirements':
            if len(output['requirements'])!=len(requirement_ids) or {item['id'] for item in output['requirements']}!=set(requirement_ids):
                raise ValueError('Account for each public requirement exactly once')
        elif skill=='generate-tests':
            if {item['requirement_id'] for item in output['coverage']}!=set(requirement_ids) or not output['steps']:
                raise ValueError('Tests need steps and a mapping for every public requirement')
            workspace.write_tests(output,dry_run=True)
        elif skill=='diagnose-results':
            for item in output['failures']:
                if item['requirement_id'] not in requirement_ids or not set(item['target_paths'])<=workspace.allowed:
                    raise ValueError('Diagnosis must use known requirements and allowed patch targets')
        elif skill=='repair-code':
            if output['baseline_sha256']!=workspace.candidate_hash() or not output['files']:
                raise ValueError('Patch must bind the exact current candidate_sha256 and contain changed files')
            if not {item['path'] for item in output['files']}<=workspace.allowed:
                raise ValueError('Patch contains a file outside scope_manifest.allowed_changes')
            workspace.apply_patch(output,dry_run=True)
        elif skill=='review-patch' and output['candidate_sha256']!=workspace.candidate_hash():
            raise ValueError('Review is not bound to the actual candidate')

    def model_schema(skill):
        if collaboration_request and skill == 'plan-workflow':
            from .team_planning import team_plan_schema
            return obj({**SCHEMAS[skill]['properties'], 'team_plan': team_plan_schema()})
        return SCHEMAS[skill]

    def invoke_once_impl(skill, extra=None, attempt=1, output_schema=None, source_workspace=None):
        nonlocal manager_gate_invocations
        with control_lock:
            active_workspace = source_workspace or workspace
            active_workspace.assert_integrity()
            if collaboration_request:
                from .team_runtime import verify_team_evidence
                verify_team_evidence(run['collaboration'],workspace)
            catalog.verify_snapshot(skill_snapshot)
            for item in artifacts.values():
                if hashlib.sha256((settings.root/item['path']).read_bytes()).hexdigest() != item['sha256']:
                    raise ValueError('A prior capability artifact changed')
            contract = catalog.get_contract(skill)
            option = selected[skill]
            output_allowance = max(1800,OUTPUT_CAPS[skill]) if collaboration_request and skill=='plan-workflow' else OUTPUT_CAPS[skill]
            cap = output_allowance*(2 if option['strategy']=='expanded' else 1)
            employee_scope = bool((extra or {}).get('assigned_task'))
            gate_scope = bool((extra or {}).get('manager_gate'))
            normal_attempts = state[skill]['attempt'] - (manager_gate_invocations if skill == 'plan-workflow' else 0)
            if not employee_scope and not gate_scope and normal_attempts>=option['max_invocations']:
                raise BudgetExceeded('Selected integer plan exhausted this capability invocation allowance')
            snapshot = active_workspace.snapshot()
            schema = output_schema or model_schema(skill)
            if output_schema is None and profile == 'python_application' and skill == 'repair-code':
                schema = obj({**schema['properties'],
                              'baseline_sha256': {**STRING, 'enum': [snapshot['candidate_sha256']]}})
            context = {'public_requirements':project['requirements'],'clarifications':project['clarifications'],
                       **snapshot,'run_plan':plan['selected'],'strategy':option['strategy'], 'requirement_ids':requirement_ids,
                       'control_instructions':(APPLICATION_INSTRUCTIONS if profile=='python_application' else PACKAGE_INSTRUCTIONS if profile=='python_package' else CONTROL_INSTRUCTIONS).get(skill,''),
                       'execution_profile':profile, 'public_runtime':project.get('public_runtime',{}), **(extra or {})}
            if collaboration_request:
                context['user_request'] = collaboration_request
                context['team_execution'] = {'backend':'wctp.GraphExecutor.execute',
                    'model_slots':budget.values['model_slots'], 'priority':'out_degree_then_critical_path_then_descendants',
                    'host_commits':'serialized_with_read_set_checks'}
                context['team_policy'] = ('The user request must be fully covered by the registered independent acceptance contract. '
                    'Report additional requests/ambiguities in uncovered_requests/questions; never silently narrow the request. '
                    'Independent acceptance and delivery checks are scheduled after code review; their not-yet-run '
                    'status alone is not an uncovered user request. Do not claim these checks have passed. '
                    'The manager preserves all nine final gates, decomposes implementation into a dependency DAG within the supplied schema task limit '
                    'using explore-repository or repair-code, assigns paths and dependency handoffs, and revises assignments '
                    'using current code, observed test results and review feedback. Each employee has its own Skill action allowance; '
                    'the whole run still obeys the actual global time and token budgets. '
                    'Each employee MUST use read_file/search as its first action even if sources are provided. '
                    'Independent investigations may run in parallel; use separate exploration employees for distinct '
                    'requirement groups when useful, followed by repair tasks with explicit handoff dependencies. '
                    'Tester, diagnosis, reviewer and independent verifier retain their existing separate roles. '
                    'Only registered public sources are readable. No hidden tests or arbitrary commands are available.')
                if search_config:
                    context['team_policy'] += (' Search mode: the five-action contract applies per employee in each isolated '
                        'candidate branch, replacing the legacy global five-call allowance. Host-wide time, tokens and model '
                        'slots remain enforced. Use the supplied search strategy and observed parent/sibling feedback to '
                        'explore a different implementation; never alter frozen tests or claim hidden acceptance results.')
                context['remaining_skill_invocations'] = {key:max(0,item['max_invocations']-state[key]['attempt']
                    +(manager_gate_invocations if key == 'plan-workflow' else 0)) for key,item in selected.items()}
                if extra and 'assigned_task' in extra:
                    # Employee contexts obey the same file ownership as their tools.
                    paths = set(extra['assigned_task']['paths'])
                    context['sources'] = {name:text for name,text in context['sources'].items() if name in paths}
                    context['scope_manifest']['allowed_changes'] = sorted(paths & active_workspace.allowed)
            timeout = min(contract['budgets']['timeout_seconds'],budget.remaining())
            attempt = state[skill]['attempt']+1
            quality_session = (extra or {}).get('test_quality_session') if search_config else None
            scope_id = (extra or {}).get('assigned_task', {}).get('id', quality_session or 'core')
            if gate_scope:
                scope_id = 'gate-' + extra['manager_gate']['task']['id']
            if employee_scope or gate_scope:
                scope_id += '@round-' + str((extra or {}).get('business_round', 1))
            scope = (active_workspace.directory.name, scope_id, skill)
            validation_attempt = scope_invocations.get(scope, 0) + 1 if source_workspace is not None or quality_session or employee_scope or gate_scope else normal_attempts + 1
            catalog.validate_invocation(skill,profile,contract['reads'],contract['writes'],completed,validation_attempt,max(1,int(timeout)))
            reservation = budget.reserve(context,cap)
            scope_invocations[scope] = validation_attempt
            if gate_scope:
                manager_gate_invocations += 1
            begin_capability_timing(skill)
            active_model_calls[skill]=active_model_calls.get(skill,0)+1
            state[skill].update(status='running',attempt=attempt,active_model_calls=active_model_calls[skill])
            event(skill,'running',CAPABILITY_NAMES[skill]+'：处理公开需求与工程资料')
        trace = None
        try:
            if hasattr(provider,'invoke_traced'):
                try:
                    output,trace = provider.invoke_traced(skill,context,schema,
                                                         timeout_s=timeout,token_cap=cap)
                finally:
                    trace = trace or provider.last_trace
            else:
                # Older injected providers have no per-call trace contract.
                # Serialize only these fixtures, never the production transport.
                with legacy_provider_lock:
                    prior_calls = len(provider.calls)
                    try:
                        output = provider.invoke(skill,context,schema,
                                                 timeout_s=timeout,token_cap=cap)
                    finally:
                        if len(provider.calls)==prior_calls+1:
                            trace=provider.calls[-1]
            if trace is None:
                raise ValueError('Model provider produced no trace for the invocation')
            return output
        finally:
            with control_lock:
                run['model_calls'] = provider.snapshot_calls() if hasattr(provider,'snapshot_calls') else list(provider.calls)
                active_model_calls[skill]-=1
                state[skill]['active_model_calls']=active_model_calls[skill]
                complete_capability_timing(skill)
                if trace is not None:
                    charged_before = budget.accounted_tokens
                    try:
                        budget.reconcile(trace,reservation)
                    finally:
                        if employee_scope:
                            key=(active_workspace.directory.name,(extra or {})['assigned_task']['id'],(extra or {}).get('business_round',1))
                            employee_charges[key]=employee_charges.get(key,0)+budget.accounted_tokens-charged_before
                else:
                    budget.cancel(reservation)

    def employee_budget_for(active_workspace, business_round):
        with control_lock:
            return {'wall_seconds':max(1,int(budget.remaining())),
                    'token_limit':max(0,budget.values['token_limit']-budget.accounted_tokens),
                    'task_accounted_tokens':{key[1]:value for key,value in employee_charges.items()
                        if key[0]==active_workspace.directory.name and key[2]==business_round}}

    def invoke_once(skill, extra=None, attempt=1, output_schema=None, source_workspace=None):
        # WCTP preserves callback failures as its own execution error. Remember
        # the trusted local exception type before that graph boundary, rather
        # than classifying a model/remote error by matching its text.
        nonlocal invocation_budget_failure
        try:
            from .project_models import ProjectModelError
            for service_attempt in range(2):
                try:
                    return invoke_once_impl(skill,extra,attempt,output_schema,source_workspace)
                except ProjectModelError as exc:
                    if exc.code != 'timeout' or service_attempt:
                        raise
                    # Each retry re-enters reservation, scope and global call
                    # accounting. The failed trace remains part of the report.
                    event(skill,'service_retry','模型服务超时：保留失败调用与预算记录，重试一次')
        except BudgetExceeded:
            with control_lock:
                invocation_budget_failure=True
            raise

    def invoke(skill, extra=None, attempt=1):
        extra=dict(extra or {})
        for correction in range(2):
            output=invoke_once(skill,extra,attempt)
            try:
                validate_model_output(skill,output)
                return output
            except (ValueError,SyntaxError) as exc:
                state[skill].setdefault('validation_failures',[]).append({'attempt':state[skill]['attempt'],'error':str(exc)})
                event(skill,'output_rejected','模型产物未通过实际位置/范围/版本检查，反馈原因后有限重试')
                if correction==1:
                    raise
                instruction = 'Correct the rejected output using only the provided current snapshot. Missing proposed new files are not existing exploration candidates.'
                if skill == 'plan-workflow' and collaboration_request:
                    instruction += (' Independent acceptance and delivery checks run after code review; their absence is not by itself '
                                    'an uncovered request. Preserve truly unsupported requests and unresolved questions.')
                extra['validation_feedback']={'error':str(exc),'instruction':instruction}
        raise AssertionError('Unreachable validation retry')

    def finish(skill,output,inputs=(),attempt=1):
        attempt=max(attempt,state[skill]['attempt'])
        artifact(skill,output,inputs,attempt)
        state[skill].update(status='completed')
        complete_capability_timing(skill)
        if skill not in completed:
            completed.append(skill)
        event(skill,'completed',CAPABILITY_NAMES[skill]+'：产物通过结构、范围和版本检查')

    try:
        if collaboration_request is not None and (not isinstance(collaboration_request,str)
                or not collaboration_request.strip() or len(collaboration_request)>20_000):
            raise ValueError('Collaboration request must contain 1–20000 characters')
        max_rounds = run.get('max_rounds',2)
        if type(max_rounds) is not int or not 1 <= max_rounds <= 5:
            raise ValueError('max_rounds must be 1–5')
        catalog = load_catalog()
        skill_snapshot = catalog.snapshot()
        project = get_project(settings,run['project_id'])
        if search_settings:
            from .repair_search import SearchConfig
            if search_settings.get('mode') not in {'mcts','forest'}:
                raise ValueError('Search mode must be mcts or forest')
            search_config = SearchConfig(**{key:value for key,value in search_settings.items() if key != 'mode'})
            if search_settings['mode'] == 'mcts' and search_config.trees != 1:
                raise ValueError('Single-tree MCTS requires trees=1')
            max_rounds = 1  # Search uses public feedback; independent acceptance is final, once.
            if not collaboration_request:
                collaboration_request = '\n'.join(item['id'] + ': ' + item['description'] for item in project['requirements'])
                run['collaboration_request'] = collaboration_request
        if project.get('adapter') not in {'flaskr','python_package','python_application'}:
            raise ValueError('No verified delivery adapter is registered for this project')
        profile=project_profile(project)
        run['execution_profile']=profile
        from .concurrency import ConcurrentBudget
        budget = ConcurrentBudget(ExecutionBudget(run.get('budget',default_budget(project['adapter']))),error_type=BudgetExceeded)
        public_seconds=240 if profile=='python_package' else 150
        required_delivery_checks=[item['name'] for item in project['package_config']['checks']] if profile=='python_package' else None
        if profile == 'python_application':
            from .application_tools import REQUIRED_TASKBOARD_CHECKS
            required_delivery_checks = list(REQUIRED_TASKBOARD_CHECKS)
        if required_delivery_checks is not None and (not required_delivery_checks or len(set(required_delivery_checks))!=len(required_delivery_checks)):
            raise ValueError('Registered CLI checks must be nonempty and uniquely named')
        requirement_ids = [item['id'] for item in project['requirements']]
        workspace = ProjectWorkspace(settings,run['id'],project)
        _source_revision = workspace.snapshot()['source_revision']
        task = {'id':project['id'],'title':project['title'],'kind':'project','source':project['source'],
                'requirements':[item['id']+': '+item['description'] for item in project['requirements']],
                'status':'running','attempts':0,'history':[]}
        run['tasks'].append(task)
        planning_bytes=len(json.dumps({'snapshot':workspace.snapshot(),'requirements':project['requirements'],
                                       'clarifications':project['clarifications']},ensure_ascii=False).encode('utf-8'))
        if collaboration_request:
            from .wctp_runtime import WCTPGraphRunner, SOURCE_SHA256
            wctp_backend = WCTPGraphRunner(max_workers=budget.values['model_slots'])
            planning_bytes += len(collaboration_request.encode('utf-8'))
            run['collaboration'] = {'schema':'testfix.manager-employees.v1','request':collaboration_request,
                                    'execution':'wctp_parallel_model_batches_serial_host_commits','plans':[],'rounds':[],
                                    'wctp_reference':{'source_root':str(wctp_backend.source_root),
                                        'source_sha256':dict(SOURCE_SHA256),'source_copied':False}}
        options = scheduling_options(catalog,max_rounds,run.get('strategy','balanced'),planning_bytes,profile,
                                     collaboration=bool(collaboration_request), search_iterations=search_config.iterations if search_config else 0,
                                     quality_attempts=search_config.quality_attempts if search_config else 0)
        weights = {'duration':1,'tokens':2 if run.get('strategy')!='thorough' else 0}
        solved = solve_plan(options,{'max_makespan_ms':budget.values['wall_seconds']*1000,
                                     'max_tokens':budget.values['token_limit'],
                                     'resources':{'model_slots':budget.values['model_slots'],'cpu_slots':budget.values['cpu_slots'],'candidate_write':1},
                                     'objective_weights':weights,'solver_time_limit_seconds':min(10,budget.remaining())},catalog=catalog,profile=profile)
        plan = {**solved,'solver_status':solved['status'],
                'selected':[{**item,'skill_id':item['id'],
                             'max_invocations':(_team_allowance(catalog,item['id'],max_rounds) if collaboration_request
                                                else invocation_allowance(catalog,item['id'],max_rounds)),
                             'output_token_cap_per_call':(max(1800,OUTPUT_CAPS[item['id']]) if collaboration_request and item['id']=='plan-workflow'
                                                          else OUTPUT_CAPS[item['id']])*(2 if item['strategy']=='expanded' else 1)}
                            for item in solved['stages']]}
        if collaboration_request:
            plan['employee_budget_scope'] = 'per_task_per_round_skill_allowance; live_global_budget_and_joint_assignments_enforced'
            for item in plan['selected']:
                item['invocation_scope'] = 'core_and_separate_employee_scopes' if item['skill_id'] in {'explore-repository','repair-code'} else 'core'
                item['evidence']['basis'] = ('Initial per-role strategy envelope. Employee allowances are compiled after the task DAG exists; '
                    'joint assignment and actual global reservations constrain execution. Not an aggregate employee runtime guarantee.')
        run['plan'] = plan
        if solved['status'] not in {'OPTIMAL','FEASIBLE'}:
            raise BudgetExceeded('Integer constraints produced no executable plan: '+solved['status'])
        selected = {item['skill_id']:item for item in plan['selected']}
        if search_config:
            selected['generate-tests']['max_invocations'] = 2 * search_config.quality_attempts + 2
            for skill in ('plan-workflow','explore-repository','repair-code','diagnose-results'):
                selected[skill]['max_invocations'] += search_config.iterations * (20 if skill in {'explore-repository','repair-code'} else 2)
        requested_model = run.get('model') or (DEFAULT_CODEX_MODEL if run['provider'] == 'codex_cli' else 'gpt-5.6-sol')
        if provider is None:
            from .project_models import ProjectModelProvider
            provider = ProjectModelProvider(run['provider'],settings,model=requested_model,artifact_dir=directory/'model_calls',profile=profile)
        run['provenance'] = {'execution_mode':'autonomous_nine_capability_workflow','model':requested_model,
                             'skill_catalog':skill_snapshot,'source_revision':_source_revision,'integer_plan':True,
                             'fresh_generation':getattr(provider,'name',None) in {'codex_cli','openai','ollama','agentarts'},
                             'provider':provider.metadata() if hasattr(provider,'metadata') else {'id':'injected_test_provider'}}
        if collaboration_request:
            run['provenance']['employee_assignment'] = 'task_worker_strategy_joint_cp_sat; reactive_tool_barriers'
        event('plan-workflow','solver',f"整数约束求解完成：{solved['status']}；九项能力及最终关卡均保留")
        output = invoke('plan-workflow',{'required_skill_ids':list(CAPABILITY_NAMES)})
        if output['skill_ids'] != list(CAPABILITY_NAMES) or {item['requirement_id'] for item in output['goals']} != set(requirement_ids):
            raise ValueError('Planner omitted or reordered core capabilities/requirements')
        finish('plan-workflow',output)
        if collaboration_request:
            from .team_assignment import plan_team_execution
            team_plan = output['team_plan']
            team_schedule = plan_team_execution(workspace,team_plan, wall_seconds=max(1,int(budget.remaining())),
                token_limit=max(0,budget.values['token_limit']-budget.accounted_tokens),
                model_slots=budget.values['model_slots'])
            run['collaboration']['plans'].append({'round':1,'plan':team_plan,'schedule':team_schedule})
            if team_schedule['status'] not in {'OPTIMAL','FEASIBLE'}:
                raise BudgetExceeded('Employee task graph has no executable integer schedule')

        output = invoke('explore-repository',{'source_index':list(workspace.source_seal),'skill_memory':[]})
        if not output['candidates']:
            raise ValueError('Explorer produced no actual code candidates')
        for item in output['candidates']:
            confined(workspace.candidate,item['path'],exists=True)
        finish('explore-repository',output,['plan-workflow'])
        output = invoke('ground-code',{'search_candidates':output['candidates']})
        if not output['locations']:
            raise ValueError('No grounded code location')
        for item in output['locations']:
            path = confined(workspace.candidate,item['path'])
            if item['requirement_id'] not in requirement_ids:
                raise ValueError('Grounding references an unknown requirement')
            item['source_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        locations = output['locations']
        finish('ground-code',output,['explore-repository'])
        output = invoke('analyze-requirements',{'code_locations':locations})
        if {item['id'] for item in output['requirements']} != set(requirement_ids):
            raise ValueError('Requirements analysis must account for every public requirement')
        requirements = output
        finish('analyze-requirements',output,['ground-code'])
        html_guidance = ('Use the registered BeautifulSoup test dependency with the stdlib html.parser backend for DOM assertions. '
            'Do not implement a new HTMLParser state machine. Compare decoded text and parsed attributes rather than raw HTML '
            'quoting, exact empty-state wording, tag nesting or optional whitespace. Bind title links to visible title text. '
            'Do not globally forbid script/img tags when testing one escaped injected payload.') if project.get('test_environment') else ''
        output = invoke('generate-tests',{'requirement_matrix':requirements,'test_environment':project.get('test_environment',{}),
                                         'html_test_guidance':html_guidance})
        if search_config:
            from .test_quality import AUDIT_SCHEMA, probe_html_helpers
            prior_quality_audits = getattr(provider,'public_quality_history',[])
            run['test_quality'] = {'passed':False,'audits':[],'prior_run_audits':prior_quality_audits,
                                   'basis':'host_standard_HTML_probe_and_fresh_model_audit'}
            for quality_attempt in range(1,search_config.quality_attempts+1):
                workspace.write_tests(output,dry_run=True)
                checks = probe_html_helpers(output['files'])
                audit = invoke_once('generate-tests', {
                    'test_quality_review':True,'test_quality_session':'quality-'+str(quality_attempt),
                    'requirement_matrix':requirements,'proposed_tests':output,
                    'prior_test_quality_audits':prior_quality_audits+run['test_quality']['audits'],
                    'host_helper_checks':checks,
                    'control_instructions':'Audit the proposed public tests before they are frozen. Return the supplied audit schema, not test code. '
                      'Look for incorrect HTMLParser helper state, assertions against unescaped raw text, over-specific layout/link labels, '
                      'and requirements accidentally narrowed or expanded. HTML entities may have equivalent encodings. '
                      'A correct implementation must not be rejected because the test helper collects author/body text as a link title. '
                      'This is an assertion-validity audit, not proof of exhaustive coverage. Findings must give a reproducible '
                      'wrong assertion under the actual supplied fixtures or a direct contradiction of a public requirement. '
                      'Coverage suggestions alone belong in the summary and do not veto freezing: independent acceptance and '
                      'final review remain mandatory. A nonempty empty-state content-region check is structural evidence; '
                      'qualitative message usefulness remains for final acceptance/review. Do not require a finite English '
                      'vocabulary to prove arbitrary message meaning. Keep prior resolved feedback consistent. '
                      'Approve only when findings are resolved. Do not change production code to satisfy an invalid test.'},
                    output_schema=AUDIT_SCHEMA)
                from .project_models import _validate_schema
                _validate_schema(audit,AUDIT_SCHEMA)
                record = {'attempt':quality_attempt,'host_checks':checks,'model_audit':audit}
                run['test_quality']['audits'].append(record)
                if audit['approved'] and not audit['findings'] and not checks['defects']:
                    run['test_quality']['passed'] = True
                    break
                if quality_attempt == search_config.quality_attempts:
                    raise ValueError('Generated test quality audit failed; suite was not frozen or used for repair')
                event('generate-tests','quality_correction','测试质量审查发现问题，保留反例并重新生成；不修改业务代码迁就错误测试')
                output = invoke_once('generate-tests', {'requirement_matrix':requirements,'previous_proposed_tests':output,
                    'test_quality_session':'quality-'+str(quality_attempt+1),'test_environment':project.get('test_environment',{}),
                    'html_test_guidance':html_guidance,
                    'test_quality_feedback':record,
                    'test_quality_history':prior_quality_audits+run['test_quality']['audits'],
                    'control_instructions':'Regenerate the complete test files and steps after fixing the host and audit counterexamples. '
                    'Preserve every registered behavior and boundary assertion. Fix helper semantics and use structural/decoded HTML checks; '
                    'match each expected fixture title within an anchor resolving to its expected post ID, never equate all anchor text '
                    'to the title. For arbitrary empty-state language, check content-region message presence and leave qualitative '
                    'usefulness to final acceptance/review rather than adding a fixed word whitelist. Honor all prior resolved feedback; '
                    'do not remove coverage or weaken expected behavior.'})
                validate_model_output('generate-tests',output)
        if {item['requirement_id'] for item in output['coverage']} != set(requirement_ids) or not output['steps']:
            raise ValueError('Generated tests require steps and complete public requirement mapping')
        workspace.write_tests(output)
        task.update(test_steps=output['steps'],requirement_coverage=output['coverage'],test_assumptions=output['assumptions'])
        event('generate-tests','baseline_running','模型测试已冻结，正在原工程执行回归与生成测试，确认真实失败证据')
        # This is a controller tool between capabilities, not a second model
        # role or a claim that final independent verification already passed.
        baseline = tool_runner(settings,workspace,'baseline_public',timeout_s=min(public_seconds,budget.remaining()))
        if not baseline['regression']['successful'] or not _functional_failure(baseline['generated']):
            raise ValueError('Generated tests must collect correctly and expose a real baseline assertion failure; original regression must pass')
        task.update(regression_baseline=baseline['regression'],generated_baseline=baseline['generated'])
        if profile=='python_package':
            task.update(upstream_regression_baseline=baseline['upstream_regression'],behavior_regression_baseline=baseline['behavior_regression'])
        finish('generate-tests',{**output,'frozen_hashes':workspace.generated_seal,'public_baseline_evidence':baseline},['analyze-requirements'])
        feedback = _public_feedback(baseline)
        final_verified = False
        for attempt in range(1,max_rounds+1):
            task['attempts'] = attempt
            if collaboration_request and attempt > 1:
                output = invoke('plan-workflow',{'required_skill_ids':list(CAPABILITY_NAMES),
                    'manager_feedback':feedback, 'previous_employee_handoffs':run['collaboration']['rounds'][-1]['handoffs'],
                    'previous_work_items':team_plan['work_items'], 'business_round':attempt})
                finish('plan-workflow',output, ['repair-code'], attempt)
                team_plan = output['team_plan']
                team_schedule = plan_team_execution(workspace,team_plan,wall_seconds=max(1,int(budget.remaining())),
                    token_limit=max(0,budget.values['token_limit']-budget.accounted_tokens),
                    model_slots=budget.values['model_slots'])
                run['collaboration']['plans'].append({'round':attempt,'plan':team_plan,'schedule':team_schedule})
                if team_schedule['status'] not in {'OPTIMAL','FEASIBLE'}:
                    raise BudgetExceeded('Reassigned employee task graph has no executable integer schedule')
            output = invoke('diagnose-results',{'requirement_matrix':requirements,'public_test_results':feedback},attempt)
            for failure in output['failures']:
                if failure['requirement_id'] not in requirement_ids or not set(failure['target_paths']) <= workspace.allowed:
                    raise ValueError('Diagnosis proposes an unknown requirement or out-of-scope patch target')
            task['diagnosis'] = output['summary']
            diagnosis = output
            finish('diagnose-results',output,['generate-tests'],attempt)
            repair_context = {'requirement_matrix':requirements,'diagnosis_report':diagnosis,
                              'generated_tests':{name:(workspace.generated/name).read_text(encoding='utf-8') for name in workspace.generated_seal},
                              'public_test_results':feedback}
            if search_config:
                from .search_workflow import run_repair_forest

                def publish_search(report):
                    run['search_report'] = report
                    target = directory/'search'/'forest.json'
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
                    checkpoint()

                def branch_call(branch, skill, extra, schema):
                    result = invoke_once(skill,extra,attempt,output_schema=schema,source_workspace=branch)
                    if extra.get('manager_gate'):
                        state['plan-workflow']['status'] = 'completed'
                    return result

                def branch_public(branch):
                    return tool_runner(settings,branch,'candidate_public',timeout_s=min(public_seconds,budget.remaining()),round_number=attempt)

                output,patch,search_report = run_repair_forest(workspace,team_plan,baseline,repair_context,
                    config=search_config,request=collaboration_request,call=branch_call,public_test=branch_public,
                    emit=event,remaining=budget.remaining,token_remaining=lambda:max(0,budget.values['token_limit']-budget.accounted_tokens),
                    model_slots=budget.values['model_slots'],publish=publish_search,round_number=attempt,
                    employee_budget=lambda branch:employee_budget_for(branch,1))
                run['collaboration']['rounds'].append({'round':attempt,'employees':[],'messages':[],
                    'handoffs':{},'execution':'selected_isolated_search_candidate_promoted',
                    'search_selected_node':search_report['selected_id'], 'candidate_after':workspace.candidate_hash()})
                state['explore-repository']['status']='completed'
            elif collaboration_request:
                from .team_runtime import execute_team_round, MANAGER_GATE_SCHEMA

                def employee_call(skill, extra, schema):
                    role_context = repair_context if skill == 'repair-code' else {}
                    return invoke_once(skill,{**role_context,**extra},attempt,output_schema=schema)

                def employee_public_test():
                    nonlocal team_public_calls
                    if team_public_calls >= 2:
                        raise ValueError('Employee public-test tool budget exhausted')
                    team_public_calls += 1
                    return _public_feedback(tool_runner(settings,workspace,'candidate_public',
                        timeout_s=min(public_seconds,budget.remaining()),round_number=attempt))

                def manager_check(assigned, handoff, observations):
                    result = invoke_once('plan-workflow', {
                        'manager_gate': {'task': assigned, 'handoff': handoff, 'observations': observations},
                        'business_round': attempt,
                        'control_instructions': 'Act as CodeAgent reviewing this employee handoff before dependent tasks start. '
                            'Check the assigned task and relevant public requirements against actual observations and current source. '
                            'Approve only if the handoff supplies enough evidence and work for its assigned scope; '
                            'do not require an individual partial task to finish all project requirements. '
                            'A no-patch handoff can be sufficient when observed code supports it; '
                            'verified_implementation=false means only that independent verification has not run. '
                            'The missing array contains only issues that block this particular handoff. '
                            'If ready is true, missing must be empty; mention later tests in reason only. '
                            'A handoff approval is not test or final acceptance success.'
                    }, attempt, output_schema=MANAGER_GATE_SCHEMA)
                    state['plan-workflow']['status'] = 'completed'
                    return result

                output,patch = execute_team_round(workspace,team_plan,team_schedule,call=employee_call,
                    public_test=employee_public_test,emit=event,ledger=run['collaboration'],round_number=attempt,
                    model_slots=budget.values['model_slots'],telemetry=event,
                    remaining_budget=lambda:employee_budget_for(workspace,attempt),manager_check=manager_check)
                state['explore-repository']['status']='completed'
            else:
                output = invoke('repair-code',repair_context,attempt)
                patch = workspace.apply_patch(output)
            finish('repair-code',{**output,**patch},['diagnose-results'],attempt)
            event('repair-code','public_validation','补丁已受控应用，正在候选工程执行冻结的生成测试与原有回归')
            public = tool_runner(settings,workspace,'candidate_public',timeout_s=min(public_seconds,budget.remaining()),round_number=attempt)
            task.update(generated=public['generated'],regression=public['regression'])
            if profile=='python_package':
                task.update(upstream_regression=public['upstream_regression'],behavior_regression=public['behavior_regression'])
            if (public['generated']['successful'] and public['regression']['successful']
                    and (not _paired_cases_match(baseline['generated'],public['generated'])
                         or not _paired_cases_match(baseline['regression'],public['regression']))):
                raise ValueError('Public test identities changed between baseline and candidate; '
                                 'stop for test-integrity or evaluator diagnosis instead of requesting a code repair')
            public_ok = (public['generated']['successful'] and _paired_cases_match(baseline['generated'],public['generated'])
                         and _regression_preserved(baseline['regression'],public['regression']))
            history = {'attempt':attempt,'candidate_sha256':workspace.candidate_hash(),'generated':{'passed':public['generated']['passed'],'total':public['generated']['total']},'public_tests_pass':public_ok}
            task['history'].append(history)
            feedback = _public_feedback(public)
            if collaboration_request:
                run['collaboration']['rounds'][-1].update(
                    status='awaiting_review' if public_ok else 'public_tests_failed',
                    public_test_result={'successful':public_ok,'candidate_sha256':workspace.candidate_hash()})
            if not public_ok:
                event('repair-code','test_failed','候选公开测试未通过，保存失败证据并进入有限修复循环')
                if attempt < max_rounds:
                    for key in ('diagnose-results','repair-code','review-patch','verify-delivery'):
                        if key in completed: completed.remove(key)
                    continue
                break
            review = invoke('review-patch',{'requirement_matrix':requirements,'patch_artifact':patch,'public_test_results':feedback,
                'review_stage_instructions': 'Review the candidate code and available public test evidence. Independent acceptance, '
                    'regression delivery checks, builds and real execution run after review. Their absence at this stage is not '
                    'itself a code defect; report them as unverified and do not claim that they passed.'},attempt)
            if review['candidate_sha256'] != workspace.candidate_hash():
                raise ValueError('Review is not bound to the actual candidate')
            task['review'] = review
            finish('review-patch',review,['repair-code'],attempt)
            if collaboration_request:
                run['collaboration']['rounds'][-1].update(review=review,
                    status='review_rejected' if not review['approved'] or any(item['severity']=='high' for item in review['findings'])
                    else 'awaiting_independent_verification')
            if not review['approved'] or any(item['severity']=='high' for item in review['findings']):
                feedback['review_findings'] = review['findings']
                event('review-patch','rejected','独立评审拒绝当前候选，修复后必须重新评审')
                if attempt < max_rounds:
                    for key in ('diagnose-results','repair-code','review-patch','verify-delivery'):
                        if key in completed: completed.remove(key)
                    continue
                break
            contract = catalog.get_contract('verify-delivery')
            catalog.validate_invocation('verify-delivery',profile,contract['reads'],contract['writes'],completed,attempt,
                                        max(1,int(min(contract['budgets']['timeout_seconds'],budget.remaining()))))
            begin_capability_timing('verify-delivery')
            state['verify-delivery'].update(status='running',attempt=attempt)
            event('verify-delivery','running','独立执行冻结验收、wheel构建与真实CLI验证' if profile=='python_package' else '独立执行冻结验收、构建产物、真实 HTTP 和数据库重启验证')
            verification = tool_runner(settings,workspace,'verify',timeout_s=min(240,budget.remaining()),round_number=attempt)
            independent_ok = (verification['acceptance']['successful'] and _paired_cases_match(verification['baseline'],verification['acceptance'])
                              and _functional_failure(verification['baseline']))
            delivery = verification['delivery']
            extended=delivery['cli' if profile=='python_package' else 'http']
            task.update(baseline=verification['baseline'],acceptance=verification['acceptance'],delivery=delivery,
                        build={**delivery['build'],'passed':int(delivery['build']['passed']),'total':1},extended=extended)
            task['delivery_kind']='cli' if profile=='python_package' else 'http'
            workspace.assert_integrity()
            final_verified = public_ok and independent_ok and delivery_passes(delivery,adapter=project['adapter'],required_checks=required_delivery_checks)
            if collaboration_request:
                run['collaboration']['rounds'][-1].update(status='accepted' if final_verified else 'verification_failed',
                    independent_result={'successful':independent_ok,'passed':verification['acceptance']['passed'],
                                        'total':verification['acceptance']['total']})
            task['verdict'] = {'baseline_demonstrates_missing_requirement':_functional_failure(baseline['generated']) and _functional_failure(verification['baseline']),
                               'generated_tests_pass':public['generated']['successful'],'independent_acceptance_pass':independent_ok,
                               'regression_preserved':_regression_preserved(baseline['regression'],public['regression']),
                               'review_bound_to_candidate':review['candidate_sha256']==workspace.candidate_hash(),
                               'protected_files_unchanged':True,'build_succeeds':bool(delivery['build']['passed']),
                               'satisfies_tested_requirements':bool(final_verified)}
            task['verdict']['application_runs_via_cli' if profile=='python_package' else 'application_runs_over_http']=bool(extended['successful'])
            task['verdict']['all_required_delivery_checks_present'] = delivery_passes(delivery,adapter=project['adapter'],required_checks=required_delivery_checks)
            finish('verify-delivery',{'verdict':task['verdict'],'evidence':verification},['review-patch'],attempt)
            if final_verified:
                break
            if run.get('independent_once'):
                break
            feedback['independent_result_summary'] = {'passed':verification['acceptance']['passed'],'total':verification['acceptance']['total'],'successful':False}
            feedback['delivery_result_summary'] = {'build':bool(delivery['build']['passed']),'kind':task['delivery_kind'],'checks_passed':extended['passed'],'checks_total':extended['total']}
            if attempt < max_rounds:
                for key in ('diagnose-results','repair-code','review-patch','verify-delivery'):
                    if key in completed: completed.remove(key)
        budget.remaining()
        workspace.assert_integrity()
        if collaboration_request:
            from .team_runtime import verify_team_evidence
            verify_team_evidence(run['collaboration'],workspace)
        if final_verified:
            if any(active_model_calls.values()):
                raise ValueError('Cannot finalize delivery while model calls are active')
            # Branch replanning/diagnosis can re-enter an already completed
            # core role. All branch calls have joined and final gates passed;
            # close its aggregate state without changing its original artifact.
            for identity,item in state.items():
                if identity in completed and item['status']=='running':
                    item.update(status='completed',status_basis='final_verified_with_no_active_model_calls')
        task.update(status='accepted' if final_verified else 'rejected',duration_s=round(time.monotonic()-started,3),
                    diff=_diff(workspace.baseline,workspace.candidate,workspace.allowed),
                    candidate_sha256=workspace.candidate_hash(),regression_breaks=0 if task.get('verdict',{}).get('regression_preserved') else len([case for case in task.get('regression',{}).get('cases',[]) if case['status']!='passed']))
        (directory/'patch.diff').write_text(task['diff'],encoding='utf-8')
        run['status'] = 'completed' if final_verified else 'rejected'
        run['summary'].update(accepted=int(final_verified),regressions=task['regression_breaks'])
        run['provenance']['provider'] = provider.metadata() if hasattr(provider,'metadata') else {'id':'injected_test_provider'}
        if final_verified:
            archive_path = directory/'unpublished-project.zip'
            with zipfile.ZipFile(archive_path,'w',compression=zipfile.ZIP_DEFLATED) as archive:
                for name in workspace.candidate_seal:
                    archive.write(workspace.candidate/name,'project/'+name)
                for name in workspace.generated_seal:
                    archive.write(workspace.generated/name,'validation/generated/'+name)
                for name in workspace.hidden_seal:
                    archive.write(workspace.acceptance/name,'validation/independent/'+name)
                if workspace.regression_seal is not None:
                    for name in workspace.regression_seal:
                        archive.write(workspace.regression/name,'validation/behavior-regression/'+name)
                archive.write(directory/'patch.diff','patch.diff')
                for skill,record in artifacts.items():
                    archive.write(settings.root/record['path'],'evidence/'+skill+'.json')
                archive.writestr('validation/requirements.json',json.dumps({'requirements':project['requirements'],
                    'clarifications':project['clarifications'],'source':project['source']},ensure_ascii=False,indent=2))
                lock = Path(project.get('requirements_lock',settings.root/'experiments/flaskr/requirements.lock'))
                if lock.is_file():
                    archive.write(lock,'validation/requirements.lock')
                # An immutable acceptance snapshot avoids a circular archive
                # hash: the final outer report subsequently binds this ZIP.
                archive.writestr('provenance/acceptance-report.json',json.dumps(run,ensure_ascii=False,indent=2))
                for path in sorted((directory/'model_calls').rglob('*')):
                    if path.is_file():
                        archive.write(path,'provenance/model_calls/'+path.relative_to(directory/'model_calls').as_posix())
                if collaboration_request:
                    archive.writestr('provenance/manager-employees.json',json.dumps(run['collaboration'],ensure_ascii=False,indent=2))
                    for path in sorted((directory/'team').rglob('*.json')):
                        archive.write(path,'evidence/team/'+path.relative_to(directory/'team').as_posix())
                if run.get('test_quality'):
                    archive.writestr('provenance/test-quality.json',json.dumps(run['test_quality'],ensure_ascii=False,indent=2))
                if run.get('search_report'):
                    from .project_tools import files_under
                    archive.writestr('provenance/search-forest.json',json.dumps(run['search_report'],ensure_ascii=False,indent=2))
                    for path in sorted((directory/'search').glob('*.json')):
                        archive.write(path,'evidence/search/'+path.name)
                    for node in run['search_report']['nodes']:
                        branch_id = node.get('evidence',{}).get('branch_run_id')
                        if not branch_id:
                            continue
                        branch_root = settings.artifacts/branch_id
                        candidate_root = branch_root/'workspace/candidate'
                        for name in files_under(candidate_root):
                            archive.write(candidate_root/name,'evidence/search/'+node['id']+'/candidate/'+name)
                        for path in sorted((branch_root/'team').rglob('*.json')):
                            archive.write(path,'evidence/search/'+node['id']+'/team/'+path.relative_to(branch_root/'team').as_posix())
                        for path in sorted((branch_root/'tool_calls').rglob('*')):
                            if path.is_file() and path.suffix in {'.json','.xml','.log','.txt'}:
                                archive.write(path,'evidence/search/'+node['id']+'/tool_calls/'+path.relative_to(branch_root/'tool_calls').as_posix())
                archive.writestr('REPRODUCE.md', application_reproduction(project) if profile=='python_application' else package_reproduction(project) if profile=='python_package' else
                    '# Reproduce the accepted Flaskr project\n\n'
                    'This archive contains actual candidate sources, frozen generated and independent tests, '
                    'the patch, nine capability artifacts and public model-call records. '
                    'The outer TestFix report binds the ZIP SHA-256.\n\n'
                    'Use Python 3.13 and a new virtual environment. Install the pinned validation dependencies '
                    'with `python -m pip install -r validation/requirements.lock`, then '
                    '`python -m pip install -e project`. Run each frozen suite separately:\n\n'
                    '```powershell\npython -m pytest project/tests -q\n'
                    'python -m pytest validation/generated -q\n'
                    'python -m pytest validation/independent -q\n'
                    'python -m build --wheel --outdir dist project\n```\n\n'
                    'Start a local instance with `python -m flask --app flaskr init-db` and '
                    '`python -m flask --app flaskr run --port 8768`. '
                    'Initialize only a new empty local instance database. '
                    'The saved TestFix acceptance report includes the separate built-wheel HTTP and '
                    'database-restart checks; running pytest alone does not reproduce those checks.\n')
            archive_hash=hashlib.sha256(archive_path.read_bytes()).hexdigest()
            workspace.assert_integrity()
            if collaboration_request:
                verify_team_evidence(run['collaboration'],workspace)
            budget.remaining()
            published_path=directory/'completed-project.zip'
            archive_path.replace(published_path)
            archive_path=published_path
            run['download'] = {'path':str(archive_path.relative_to(settings.root)),'sha256':archive_hash}
    except Exception as exc:
        run['status'] = 'budget_exhausted' if isinstance(exc,BudgetExceeded) or invocation_budget_failure else 'failed'
        run['summary']['accepted']=0
        run.pop('download',None)
        run['error'] = f'{type(exc).__name__}: {exc}'
        for capability in state.values():
            if capability['status']=='running':
                capability.update(status='failed',error=str(exc))
        for task in run['tasks']:
            task['status'] = run['status']
        if provider is not None:
            run['model_calls'] = list(provider.calls)
    finally:
        run['finished_at'] = now()
        checkpoint()
    return run


CONTROL_INSTRUCTIONS = {
    'plan-workflow':'Return the required nine skill IDs exactly in the provided order. Account for every requirement ID. ILP has already selected a legal resource/budget plan; do not remove final gates.',
    'explore-repository':'Search only the provided source_index/sources. Return actual existing file paths relevant to requirements. Repeated searches without new evidence are unnecessary.',
    'ground-code':'Bind every location to an existing provided file and real symbol or route. Use the public requirements and actual source only, never an imagined file. New missing routes belong to their existing module.',
    'analyze-requirements':'Cover each provided requirement ID exactly. Distinguish source facts from assumptions. The public requirement contract decides behaviors, not current implementation.',
    'generate-tests':'Return self-contained pytest tests plus fixtures. Each files[].path MUST be a BARE filename, for example conftest.py or test_features.py; never prefix generated_tests/ or any directory. The host places these files in its separate generated directory. Existing tests/conftest.py fixtures are NOT inherited: reproduce needed fixtures locally. Use Flask test client, tmp_path SQLite and the registered BeautifulSoup DOM dependency when available; generated tests must fail on the missing features. Use ISO timestamp strings when inserting database fixtures. Never redirect sys.path or hardcode baseline/candidate paths. Do not invoke external commands. Cover all four IDs, including regression preservation. Literal search must match identical non-ASCII characters; ASCII case folding is sufficient, and no negative assertion about additional Unicode variants is implied. Treat 5000-digit ASCII page as out-of-range 404, never a 500.',
    'diagnose-results':'Use only the provided public/generated failures and requirements. Identify bounded repair targets from scope_manifest.allowed_changes. Infrastructure or collection errors are distinct from behavior assertions.',
    'repair-code':'Return complete UTF-8 sources ONLY for changed allowed files. baseline_sha256 must exactly equal the provided candidate_sha256. Do not change tests, auth, database schema, evaluator or imports to another source tree. Read the public contract closely: ASCII case folding leaves non-ASCII characters intact, SQL wildcard characters are literal data, and huge numeric page values must be compared without unsafe int conversion. Preserve all original behavior.',
    'review-patch':'Independently inspect the current sources and public requirement contract. candidate_sha256 must exactly equal the provided current hash. Do not trust developer assertions. Check ASCII-only literal search, Unicode preservation, huge page handling, authorization, escaping, package templates, baseline imports and original behavior. State whether logs were supplied instead of personally executed.',
}


PACKAGE_INSTRUCTIONS={**CONTROL_INSTRUCTIONS,
    'generate-tests':'This is a complete Python CLI package, NOT Flask. Return self-contained pytest files using the actual rich_cli.__main__.main Click command and click.testing.CliRunner; dependencies are already installed and the host binds the selected src tree. Every files[].path MUST be a BARE test_*.py or conftest.py filename, no directory prefix. Derive expected behavior only from the four public requirements. Provide test steps and a coverage mapping for every ID. Exercise literal CSV headers/cells, malformed-looking markup as data, actual stdin/files, exports and existing intentional --print markup behavior. Use width 160 to avoid wrap artifacts, and decode XML/HTML entities before comparing visible export text. Do not launch commands or network requests in generated pytest; the independent host later builds a wheel and runs real CLI processes. Do not change tests, imports or sys.path to another source tree. Never claim tests have run.',
    'diagnose-results':'Use the supplied public requirement matrix and actual generated/upstream/behavior regression results. Distinguish behavior assertions from environment or collection errors. Only allowed src package files are repair targets; never change upstream tests, dependencies, metadata, hidden evaluation, or import routing.',
    'repair-code':'This is a Python CLI package. Return complete UTF-8 contents only for changed scope_manifest.allowed_changes paths; baseline_sha256 must exactly equal current candidate_sha256. Repair the actual implementation to satisfy public requirements and preserve all existing CLI modes, flags, errors and exports. Do not modify tests, dependencies, package metadata, or import bindings. The host applies the patch, reruns frozen tests, independently reviews the new hash, builds a wheel and exercises real CLI subprocesses.',
    'review-patch':'Independently inspect current Python package sources against all four public requirements. Bind candidate_sha256 exactly. Check CSV values remain literal in headers, cells and exports while intentional --print markup still works and upstream CLI behavior is preserved. Check patch scope and avoid requiring any particular implementation. Supplied public test results are observations; do not claim to have run commands. Hidden evaluation and real wheel CLI checks are pending host gates.'}


def package_reproduction(project):
    return ('# Reproduce the accepted Python package\n\n'
            'Install Python 3.13, create a fresh virtual environment, then run:\n\n'
            '```powershell\n$env:PYTHONUTF8 = "1"\n$env:COLUMNS = "200"\n$env:LINES = "60"\n'
            'python -X utf8 -m pip install -r validation/requirements.lock\n'
            'python -X utf8 -m pip install --no-build-isolation --no-deps -e project\n'
            'python -X utf8 -m pytest project/tests -q --rootdir project/tests --basetemp .testfix-temp/upstream\n'
            'python -X utf8 -m pytest validation/behavior-regression -q --rootdir validation/behavior-regression --basetemp .testfix-temp/regression\n'
            'python -X utf8 -m pytest validation/generated -q --rootdir validation/generated --basetemp .testfix-temp/generated\n'
            'python -X utf8 -m pytest validation/independent -q --rootdir validation/independent --basetemp .testfix-temp/independent\n'
            'python -X utf8 -m build --no-isolation --wheel --outdir dist project\n'
            f"python -X utf8 -m {project['package_config']['cli_module']} --help\n```\n\n"
            'The original upstream suite and pre-frozen additional behavior regression are reported separately. '
            'The saved host delivery records contain real CLI subprocess checks against the built wheel; '
            'pytest alone does not reproduce those process checks. '
            'This snapshot includes generated and independent tests, the patch, capability artifacts and public model-call traces.\n')


APPLICATION_INSTRUCTIONS = {**CONTROL_INSTRUCTIONS,
    'generate-tests': (
        'Use the supplied TaskBoard requirements and cover exactly their IDs. Return only executable Python '
        'test_*.py files or conftest.py in files, with bare filenames. Put the test plan in steps and coverage. '
        'Do not include a separate plan, JSON, or notes file. Use tmp_path and either Storage or a self-contained '
        'in-process WSGI helper. Create app = create_application(tmp_path / "tasks.sqlite3") and pass app to '
        'that helper. The helper calls app(environ, start_response) directly; create_application only accepts '
        'a database path. Do not import original test modules, open sockets, or run commands. '
        'When the baseline has only a bootstrap endpoint, assert the expected '
        'HTTP status before inspecting business fields so absent behavior produces a meaningful assertion failure. '
        'Decode JSON only for application/json. For search and pagination, account for the ten seed records, '
        'match percent and underscore literally, and distinguish the filtered total from the current page. '
        'For completion, check both boolean values after reopening Storage. Use small independent fixtures '
        'and compare actual behavior with the expected contract. The host freezes generated tests before repair.'
    ),
    'repair-code': (
        'Implement the supplied TaskBoard WSGI requirements in the allowed paths. Return full contents of changed '
        'files, with one entry per changed path. Copy the current candidate_sha256 verbatim into baseline_sha256. '
        'Preserve original tests and existing behavior outside the requested '
        'changes. For a bootstrap baseline, implement the requested business code and static assets in the declared '
        'paths. Keep the published factory and HTTP contract even if a generated test misuses it. Keep SQL '
        'parameters bound and treat search wildcards literally. The host runs frozen tests and '
        'actual HTTP and restart checks.'
    ),
    'review-patch': 'Independently review TaskBoard against all supplied requirements and public results. Bind the exact candidate hash. Check SQL parameterization, literal %/_ handling, correct filtered totals and pagination, committed True/False state, reopening databases and unchanged original behavior. No Flask or rich-cli assumptions apply. State that verification remains host-executed.',
    'diagnose-results': 'Diagnose TaskBoard using public requirement matrix and actual assertion failures. Use only allowed runtime paths as repair targets; distinguish broken behavior from collection/environment errors. Do not modify tests or import routing.'}


def application_reproduction(project):
    return ('# 运行已补全的任务管理系统\n\n使用 Python 3.11 或更新版本。应用本身无第三方依赖。\n\n'
        '```powershell\ncd project\npython -X utf8 server.py --port 8878\n```\n\n'
        '打开 http://127.0.0.1:8878。数据库默认保存在 project/var/tasks.sqlite3。\n'
        '验证时安装 pytest，分别执行 project/tests、validation/generated 和 validation/independent。'
        '交付记录另包含实际 HTTP、多数据、20 轮循环和 SQLite 重启检查；它们不等于仅运行 pytest。\n')
