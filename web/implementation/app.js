import {request,jsonRequest,el} from '/static/intake/details-assistant.js';
import {createRunFlow} from './run-flow.js';
import {createWctpTests} from './wctp-tests.js';
import {createFeatureTesting} from './feature-testing.js';
const $=id=>document.getElementById(id);
const runFlow=createRunFlow();
const wctpTests=createWctpTests();
const featureTesting=createFeatureTesting();
const query=new URLSearchParams(location.search),state={project:null,task:null,run:null,runId:query.get('run')||'',providers:[],timer:null,stream:null,serial:0,pollSerial:0,busy:false,tab:'log'};
const labels={queued:'排队中',running:'执行中',completed:'已完成并通过验收',failed:'执行失败',rejected:'验收未通过',budget_exhausted:'预算不足，已停止',interrupted:'已中断'};
const featureNames={R1:'搜索功能',R2:'分页功能',R3:'完成状态保存',R4:'原有功能与运行验证'};
const featureDescriptions={R1:'按中文或英文标题查找任务，无匹配时显示空结果，清空后恢复列表。',R2:'不同页显示不同的数据，末页和超出范围时处理正确。',R3:'勾选或取消完成后，刷新、重新打开或重启仍保留状态。',R4:'保留新增、读取、删除等功能，检查多数据、重复操作和程序启动。'};
const active=run=>['queued','running'].includes(run?.status);
let featureSignature='',eventsSignature='',stepsSignature='',agentsSignature='';
function error(message){$('error').textContent=message;$('error').hidden=!message;}
function controls(){const running=active(state.run);$('start').disabled=state.busy||!state.project||!$('goal').value.trim()||!state.providers.some(p=>p.id===$('provider').value&&p.configured)||running;$('start').textContent=running?'正在补全与修改…':'开始补全与修改';$('project').disabled=state.busy;$('run-history').disabled=state.busy;$('provider').disabled=state.busy||running;$('goal').disabled=state.busy||running;featureTesting.update(state);}
function runOption(run){let option=[...$('run-history').options].find(o=>o.value===run.id);if(!option){option=el('option');option.value=run.id;$('run-history').prepend(option);}option.textContent=new Date(run.created_at).toLocaleString('zh-CN',{hour12:false})+' · '+(labels[run.status]||run.status)+' · '+run.id.slice(0,8);return option;}
function features(){
  const signature=JSON.stringify([state.project?.id,state.project?.requirements,state.run?.status]);if(signature===featureSignature)return;featureSignature=signature;
  $('feature-list').replaceChildren();for(const item of state.project?.requirements||[]){const app=state.project.adapter==='python_application',card=el('article',undefined,'feature'+(state.run?.status==='completed'?' checked':'')),head=el('div');head.append(el('strong',app?(featureNames[item.id]||item.id):item.id),el('small',state.run?.status==='completed'?'已通过验收':active(state.run)?'执行中':'待执行'));card.append(head,el('p',app?(featureDescriptions[item.id]||item.description):item.description));if(app){const details=el('details');details.append(el('summary','验收条件'),el('p',item.description));card.append(details);}$('feature-list').append(card);}
}
async function catalog(){
  const [inputs,registered,models]=await Promise.all([request('/api/intakes?limit=100'),request('/api/projects'),request('/api/intake-models')]);
  const prior=$('project').value;$('project').replaceChildren();
  const uploadGroup=el('optgroup');uploadGroup.label='我的任务';
  for(const item of inputs.tasks.filter(t=>t.status==='prepared'&&t.execution_ready!==false)){const option=el('option',item.title);option.value='intake:'+item.id;uploadGroup.append(option);}
  if(uploadGroup.children.length)$('project').append(uploadGroup);
  const known=el('optgroup');known.label='已登记工程';
  for(const item of registered.projects.filter(p=>!p.id.startsWith('Project/intake-'))){const option=el('option',item.title);option.value='registered:'+item.id;known.append(option);}
  if(known.children.length)$('project').append(known);
  state.providers=models.providers;$('provider').replaceChildren();
  for(const provider of state.providers){const option=el('option',(provider.id==='codex_cli'?`Codex · ${provider.model}（请求）`:provider.label)+(provider.configured?'':'（未配置）'));option.value=provider.id;option.disabled=!provider.configured;$('provider').append(option);}
  const configured=state.providers.find(p=>p.configured);if(configured)$('provider').value=configured.id;
  const wanted=query.get('task')?'intake:'+query.get('task'):(query.get('project')||prior);
  if([...$('project').options].some(o=>o.value===wanted))$('project').value=wanted;
}
async function choose(){
  const serial=++state.serial;clearTimeout(state.timer);closeStream();state.busy=true;state.project=null;state.task=null;state.run=null;runFlow.update(null);error('');controls();$('project-note').textContent='正在准备项目源码…';
  if(!$('project').value){$('project-note').textContent='尚无项目，请先导入源码。';$('task-link').hidden=true;render(null);state.busy=false;controls();return;}
  try{
    const [kind,identifier]=[$('project').value.split(':')[0],$('project').value.slice($('project').value.indexOf(':')+1)];let project;
    if(kind==='intake'){
      const task=await request('/api/intakes/'+identifier);if(serial!==state.serial)return;
      project=await request('/api/intakes/'+identifier+'/project',jsonRequest('POST',{revision:task.revision||0}));
      if(serial!==state.serial)return;state.task=task;$('goal').value=task.description+(task.acceptance?'\n\n验收标准：\n'+task.acceptance:'');
      $('task-link').href='/tasks?task='+task.id;$('task-link').hidden=false;$('source-label').textContent='来自上传的源码与任务资料';
    }else{
      project=await request('/api/implementation-projects/'+identifier);if(serial!==state.serial)return;
      $('goal').value=project.requirements.map(r=>r.description).join('\n');$('task-link').hidden=true;$('source-label').textContent='来自已登记工程的验收合同';
    }
    state.project=project;features();$('project-note').textContent='源码已准备，原文件保留。';
    const listing=await request('/api/runs');if(serial!==state.serial)return;
    const matches=listing.runs.filter(r=>r.kind==='autonomous_project'&&r.project_id===project.id);
    $('run-history').replaceChildren();for(const item of [...matches].reverse())runOption(item);if(!matches.length){const option=el('option','尚无运行记录');option.value='';$('run-history').append(option);}
    const wanted=state.runId&&matches.find(r=>r.id===state.runId);state.runId=(wanted||matches[0])?.id||'';
    if(state.runId)await poll(serial);else render(null);
    window.history.replaceState(null,'',state.task?'/implementation?task='+state.task.id+(state.runId?'&run='+state.runId:''):'/implementation'+(state.runId?'?run='+state.runId:''));
  }catch(e){$('project-note').textContent='项目尚未准备完成';error(e.message);render(null);}
  finally{if(serial===state.serial){state.busy=false;controls();}}
}
function score(id,value){const node=$(id);node.className='';if(!value){node.textContent='待执行';return;}node.textContent=value.total?`${value.passed}/${value.total}`:'未成功收集';node.className=value.successful?'good':'bad';}
function render(run){
  state.run=run;runFlow.update(run);wctpTests.update(run);const task=run?.tasks?.[0];$('run-status').textContent=labels[run?.status]||'尚未开始';features();
  if(run){runOption(run);$('run-history').value=run.id;}
  const fresh=(run?.model_calls||[]).filter(c=>!['public_checkpoint_reuse','frozen_foundation_reuse'].includes(c.provider)).length,reused=(run?.model_calls?.length||0)-fresh;
  const latest=run?.events?.at(-1);$('run-summary').textContent=run?(run.error||latest?.message||labels[run.status])+` · 本轮模型调用 ${fresh} 次${reused?'，复用已保存分析 '+reused+' 项':''} · 运行 ${run.id.slice(0,8)}`:'选择项目并开始后，这里显示实际进度。';
  const stages=[['plan-workflow','explore-repository','ground-code','analyze-requirements'],['generate-tests'],['diagnose-results','repair-code','review-patch'],['verify-delivery']];
  [...$('progress').querySelectorAll('span')].forEach((node,index)=>{const values=(run?.workflow?.capabilities||[]).filter(c=>stages[index].includes(c.skill_id));node.className=values.length&&values.every(c=>c.status==='completed')?'done':values.some(c=>c.status==='running')?'active':'';});
  score('regression',task?.regression||task?.regression_baseline);score('generated',task?.generated||task?.generated_baseline);score('acceptance',task?.acceptance);score('runtime',task?.extended);
  const eventKey=JSON.stringify([run?.id,run?.events]);if(eventKey!==eventsSignature){eventsSignature=eventKey;$('events').replaceChildren();for(const item of run?.events||[]){const row=el('li'),time=el('time',new Date(item.time).toLocaleTimeString('zh-CN',{hour12:false}));row.append(time,el('span',item.message));$('events').append(row);}if(!run?.events?.length)$('events').append(el('li','尚无执行记录。'));}
  const stepKey=JSON.stringify([run?.id,task?.test_steps]);if(stepKey!==stepsSignature){stepsSignature=stepKey;$('test-steps').replaceChildren();for(const step of task?.test_steps||[])$('test-steps').append(el('li',step));if(!task?.test_steps?.length)$('test-steps').append(el('li','尚未生成测试步骤。'));}
  const plan=run?.collaboration?.plans?.at(-1)?.plan,agentKey=JSON.stringify([run?.id,plan]);if(agentKey!==agentsSignature){agentsSignature=agentKey;$('agents').replaceChildren();
  for(const item of plan?.work_items||[]){const article=el('article');article.append(el('strong',item.id+(item.skill_id==='repair-code'?' · 代码实现':' · 分析定位')),el('p',item.title),el('p','文件：'+item.paths.join('、')+' · 需求：'+item.requirement_ids.join('、')));$('agents').append(article);}
  if(!plan?.work_items?.length)$('agents').textContent='主控生成任务计划后显示实际分工。';
  }
  $('download').hidden=!run?.download;$('download').href=run?'/api/project-runs/'+run.id+'/download':'#';
  $('report').hidden=!run;$('report').href=run?'/api/runs/'+run.id+'/report?format=md':'#';
  $('preview').hidden=run?.status!=='completed'||state.project?.adapter!=='python_application';
  $('open-preview').hidden=true;controls();
}
async function poll(serial=state.serial){
  if(!state.runId)return;
  clearTimeout(state.timer);const pollSerial=++state.pollSerial;
  try{const run=await request('/api/runs/'+state.runId);if(serial!==state.serial||pollSerial!==state.pollSerial)return;render(run);if(state.tab==='diff')await loadDiff();
    if(active(run))watch(run.id,serial);
  }catch(e){if(serial===state.serial){runFlow.disconnected();error('读取进度失败：'+e.message);state.timer=setTimeout(()=>poll(serial),3000);}}
}
function closeStream(){if(state.stream){state.stream.close();state.stream=null;}}
function watch(id,serial){
  closeStream();const source=new EventSource('/api/runs/'+encodeURIComponent(id)+'/stream');state.stream=source;
  source.onmessage=event=>{
    if(state.stream!==source||serial!==state.serial||state.runId!==id)return;
    const value=JSON.parse(event.data);error('');render(value);if(state.tab==='diff')void loadDiff();
    if(!active(value))closeStream();
  };
  source.onerror=()=>{
    if(state.stream!==source||serial!==state.serial)return;
    closeStream();runFlow.disconnected();error('实时连接中断，正在重新连接…');state.timer=setTimeout(()=>poll(serial),1500);
  };
}
async function loadDiff(){if(!state.runId){$('diff').textContent='尚无代码修改。';return;}try{const data=await request('/api/project-runs/'+state.runId+'/patch');$('diff').textContent=data.diff||'尚无代码修改。';}catch(e){$('diff').textContent=e.message;}}
$('project').onchange=()=>{state.runId='';choose();};$('refresh').onclick=async()=>{error('');if(state.runId)await poll();else{await catalog();await choose();}};
$('run-history').onchange=async()=>{if(!$('run-history').value)return;clearTimeout(state.timer);closeStream();state.runId=$('run-history').value;state.busy=true;controls();try{await poll();window.history.replaceState(null,'','/implementation?'+new URLSearchParams({...(state.task?{task:state.task.id}:{}),run:state.runId}));}finally{state.busy=false;controls();}};
$('goal').oninput=controls;$('provider').onchange=controls;
$('start').onclick=async()=>{
  if($('start').disabled)return;state.busy=true;controls();error('');
  try{const provider=state.providers.find(p=>p.id===$('provider').value);const run=await request('/api/project-runs',jsonRequest('POST',{
    project_id:state.project.id,provider:provider.id,model:provider.model,max_rounds:2,strategy:'balanced',collaboration_request:$('goal').value,
    ...(state.task?{intake_id:state.task.id,intake_revision:state.task.revision||0}:{})}));
    state.runId=run.id;clearTimeout(state.timer);closeStream();await poll();$('run-flow').scrollIntoView({behavior:'smooth',block:'start'});window.history.replaceState(null,'','/implementation?'+new URLSearchParams({...(state.task?{task:state.task.id}:{}),run:run.id}));
  }catch(e){error(e.message);}finally{state.busy=false;controls();}
};
document.querySelectorAll('[data-tab]').forEach(button=>button.onclick=()=>{state.tab=button.dataset.tab;document.querySelectorAll('[data-tab]').forEach(b=>{b.setAttribute('aria-selected',String(b===button));$('panel-'+b.dataset.tab).hidden=b!==button;});if(state.tab==='diff')loadDiff();});
$('preview').onclick=async()=>{if(!state.run)return;$('preview').disabled=true;error('');try{const value=await request('/api/project-runs/'+state.run.id+'/preview',{method:'POST'});$('open-preview').href=value.url;$('open-preview').hidden=false;$('preview').textContent='程序已启动 ✓';}catch(e){error(e.message);}finally{$('preview').disabled=false;}};
window.addEventListener('pagehide',()=>{clearTimeout(state.timer);closeStream();},{once:true});
try{await catalog();if(!$('project').options.length){$('project-note').textContent='尚无项目，请先导入源码。';$('task-link').hidden=true;controls();}else await choose();}catch(e){error(e.message);}
