import test from 'node:test';
import assert from 'node:assert/strict';
import {MODULES,timelineOf,frameOf,visibleFrame,publicDetail} from './flow-model.mjs';
const task=(id,title,depends_on=[])=>({id,title,skill_id:id==='repair'?'repair-code':'explore-repository',paths:['app/storage.py'],depends_on});
function fixture(){
  const work=[task('a','检查搜索'),task('b','检查持久化'),task('repair','修复存储',['a','b'])];
  const events=[['analyze-requirements','completed','分析完成'],['explore-repository','employee_assigned','总控按出度优先分配任务：检查搜索'],
    ['explore-repository','employee_assigned','总控按出度优先分配任务：检查持久化'],['explore-repository','employee_tool','a: read_file 完成'],
    ['explore-repository','employee_tool','a: finish 完成'],['explore-repository','employee_tool','b: finish 完成'],
    ['repair-code','employee_assigned','总控按出度优先分配任务：修复存储'],['repair-code','employee_tool','repair: finish 被拒绝'],
    ['repair-code','employee_tool','repair: apply_patch 完成'],['verify-delivery','running','执行验收'],['verify-delivery','completed','验收完成']]
    .map(([agent,stage,message],i)=>({agent,stage,message,time:new Date(1000*i).toISOString()}));
  return {id:'fixture',status:'running',events,collaboration:{plans:[{round:1,plan:{work_items:work}}],rounds:[{round:1,plan:{work_items:work},messages:[
    {sender:'explore-repository/software/a',kind:'handoff',content:{message:'搜索参数未被使用'}}]}]},
    workflow:{capabilities:[{skill_id:'verify-delivery',status:'completed'}]},model_calls:[{skill_id:'analyze-requirements',result:{requirements:[{id:'R1',evidence:'query 被忽略'}]}}]};
}
test('a registered plan alone creates no running employee or observed transfer',()=>{
  const run=fixture();run.events=[];const frame=frameOf(run,0);
  assert.equal(frame.nodes.find(n=>n.taskId==='a').status,'pending');assert.ok(frame.edges.every(e=>e.planned));assert.equal(frame.current,undefined);
});
test('concurrent employee assignments stay distinct and both active',()=>{
  const frame=frameOf(fixture(),3);
  assert.equal(frame.nodes.find(n=>n.taskId==='a').status,'running');assert.equal(frame.nodes.find(n=>n.taskId==='b').status,'running');
  assert.equal(frame.current.source,'plan-workflow');assert.equal(frame.current.target,'employee:1:b');
});
test('historical projection never displays future handoff as already complete',()=>{
  const frame=frameOf(fixture(),4),worker=frame.nodes.find(n=>n.taskId==='a');
  assert.equal(worker.status,'running');assert.doesNotMatch(worker.message,/搜索参数未被使用/);
  const later=frameOf(fixture(),5);assert.equal(later.current.kind,'产物回传');assert.equal(later.current.summary,'搜索参数未被使用');
});
test('a rejected tool is not a successful patch or a failed whole run',()=>{
  const frame=frameOf(fixture(),8);assert.equal(frame.current.status,'warning');assert.equal(frame.current.target,'host');
  assert.equal(frame.nodes.find(n=>n.taskId==='repair').status,'warning');
  assert.equal(frameOf(fixture(),9).nodes.find(n=>n.taskId==='repair').status,'handed_off');
});
test('new runs wait for controller approval and explicit handoff before completing a worker',()=>{
  const run=fixture();
  run.collaboration.rounds[0].handoff_gate='codeagent_review';
  run.events=[
    {agent:'explore-repository',stage:'employee_assigned',task_id:'a',round:1,message:'开始'},
    {agent:'explore-repository',stage:'employee_tool',task_id:'a',round:1,message:'a: finish 完成'},
    {agent:'plan-workflow',stage:'handoff_checked',task_id:'a',round:1,ok:false,message:'a：总控要求补充'},
    {agent:'plan-workflow',stage:'handoff_checked',task_id:'a',round:1,ok:true,message:'a：总控允许交接'},
    {agent:'explore-repository',stage:'employee_handoff',task_id:'a',round:1,message:'a：产物已交回主控'},
  ];
  assert.equal(frameOf(run,2).nodes.find(n=>n.taskId==='a').status,'awaiting_manager');
  assert.equal(frameOf(run,3).nodes.find(n=>n.taskId==='a').status,'warning');
  assert.equal(frameOf(run,4).nodes.find(n=>n.taskId==='a').status,'running');
  assert.equal(frameOf(run,5).nodes.find(n=>n.taskId==='a').status,'handed_off');
});
test('dependency edges are explicitly planned, never reported as direct agent messages',()=>{
  const frame=frameOf(fixture(),7);const edge=frame.edges.find(e=>e.source==='employee:1:a'&&e.target==='employee:1:repair');
  assert.equal(edge.planned,true);assert.match(edge.kind,/经主控/);
});
test('structured round and task identities override legacy text inference',()=>{
  const run=fixture();run.events=[{agent:'repair-code',stage:'employee_handoff',message:'交回',task_id:'repair',round:2,summary:'补丁已保存'}];
  const entry=timelineOf(run)[0];assert.equal(entry.employee,'employee:2:repair');assert.equal(entry.summary,'补丁已保存');assert.equal(entry.target,'plan-workflow');
});
test('ready employee receives dependency inputs only after observed predecessor handoffs',()=>{
  const before=frameOf(fixture(),6);assert.equal(before.edges.filter(e=>!e.planned&&e.kind==='依赖产物（经主控）').length,0);
  const after=frameOf(fixture(),7),inputs=after.edges.filter(e=>!e.planned&&e.kind==='依赖产物（经主控）');
  assert.equal(inputs.length,2);assert.ok(inputs.every(e=>e.target==='employee:1:repair'&&e.eventId===after.current.id));
});
test('repeated snapshots keep stable event identities; new run never shares them',()=>{
  const run=fixture();assert.deepEqual(timelineOf(run).map(e=>e.id),timelineOf(structuredClone(run)).map(e=>e.id));
  const other={...run,id:'other'};assert.notEqual(timelineOf(run)[0].id,timelineOf(other)[0].id);
});
test('a terminal failure stops activity without inventing acceptance',()=>{
  const run=fixture();run.events=run.events.slice(0,4);run.status='failed';run.workflow={};const frame=frameOf(run,4);
  assert.equal(frame.active,false);assert.equal(frame.nodes.find(n=>n.taskId==='a').status,'stopped');
  assert.equal(frame.nodes.find(n=>n.id==='verify-delivery').status,'pending');
});
test('only public analysis outputs are summarized; no raw model response is read',()=>{
  const run=fixture();run.model_calls[0].raw_response='not-displayable';const entry=timelineOf(run)[0];assert.match(entry.summary,/query 被忽略/);assert.doesNotMatch(entry.summary,/not-displayable/);
  const detail=publicDetail(frameOf(run,1).nodes.find(n=>n.id==='analyze-requirements'));assert.match(detail.message,/query/);
});
test('three main modules own stages, employees and tools at different levels',()=>{
  const frame=frameOf(fixture(),7);
  assert.equal(MODULES.length,3);
  assert.equal(frame.nodes.find(n=>n.id==='constraint-scheduler').parent,'plan-workflow');
  assert.equal(frame.nodes.find(n=>n.taskId==='repair').parent,'module-generation');
  assert.equal(frame.nodes.find(n=>n.id==='generate-tests').parent,'module-testing');
  assert.equal(frame.nodes.find(n=>n.id==='host').kind,'infrastructure');
  const view=visibleFrame(frame,'module-testing');assert.ok(view.nodes.filter(n=>n.parent).every(n=>n.parent==='module-testing'));
  assert.ok(MODULES.every(m=>view.nodes.some(n=>n.id===m.id)));
  assert.ok(!view.nodes.some(n=>n.taskId==='repair'));
});
test('generation scene prioritizes employee figures when assignments exist',()=>{
  const frame=frameOf(fixture(),3),view=visibleFrame(frame,'module-generation');
  assert.equal(view.nodes.filter(n=>n.kind==='employee').length,3);
  assert.ok(!view.nodes.some(n=>n.parent==='module-generation'&&n.kind==='phase'));
  assert.ok(!view.nodes.some(n=>n.kind==='module'));
  assert.ok(view.nodes.some(n=>n.kind==='controller'));
  assert.ok(frame.nodes.some(n=>n.id==='ground-code'&&n.kind==='phase'));
});
test('a chronological phase change never manufactures a communication edge',()=>{
  const run=fixture();run.events=[{agent:'ground-code',stage:'running',message:'定位',time:new Date().toISOString()}];
  const frame=frameOf(run,1);assert.equal(frame.current.inferred,true);
  assert.equal(frame.edges.filter(e=>!e.planned).length,0);
});
test('a future worker choice is not shown as an already dispatched assignment',()=>{
  const run=fixture();run.collaboration.rounds[0].employees=[{task_id:'a',worker_id:'database#1',worker_role:'database',execution_strategy:'standard',assignment_id:'allocation-1',specialist_ids:['database']}];
  assert.equal(frameOf(run,1).nodes.find(n=>n.taskId==='a').workerId,'');
  const later=frameOf(run,2);assert.equal(later.nodes.find(n=>n.taskId==='a').workerId,'database#1');
  assert.equal(frameOf(run,1).nodes.find(n=>n.taskId==='a').workerRole,'');
  assert.equal(later.nodes.find(n=>n.taskId==='a').workerRole,'database');
  assert.match(publicDetail(later.nodes.find(n=>n.taskId==='a')).assignment,/allocation-1/);
});
