// A read-only projection of host events. Planned dependencies are not executions.
export const CORE = [
  ['plan-workflow','CodeAgent 主控','理解需求、分配任务、汇总反馈',[0,1.1,-4.5]],
  ['explore-repository','仓库探索','读取源码，查找相关文件',[-7,.1,-3]],
  ['ground-code','代码定位','定位函数、接口与调用关系',[-7,.1,.4]],
  ['analyze-requirements','问题分析','对照需求识别缺失与错误',[-4,.1,3.5]],
  ['generate-tests','测试生成','生成步骤、数据和断言',[-.5,.1,3.5]],
  ['diagnose-results','失败诊断','根据真实失败证据定位原因',[3,.1,3.5]],
  ['repair-code','代码补全','受控应用补丁，汇总员工产物',[6.5,.1,.4]],
  ['review-patch','代码评审','检查修改、文件范围和风险',[6.5,.1,-3]],
  ['verify-delivery','独立验收','运行回归、验收和真实程序',[3.3,.1,-5.5]],
];
export const MODULES = [
  {id:'plan-workflow',title:'CodeAgent 主控',description:'需求理解、任务组织、约束调度、反馈汇总与重新派工',xyz:[0,2.3,-6],kind:'controller'},
  {id:'module-generation',title:'多 Agent 生成与补全',description:'工程分析 → 专业员工实现 → 集成与独立评审',xyz:[-6,.7,-3],kind:'module'},
  {id:'module-testing',title:'自动化测试',description:'测试设计 → WCTP 实际执行 → 回归、独立验收与运行检查',xyz:[6,.7,-3],kind:'module'},
];
export const WORKER_NAMES={software:'软件业务',frontend:'前端交互',javascript:'JavaScript / TypeScript',threejs:'Three.js 场景',backend:'后端接口',database:'数据库',integration:'集成协作',mcu:'MCU 固件',mpu:'MPU 系统',protocol:'驱动与协议'};
export const ownerOf=id=>id==='plan-workflow'||id==='constraint-scheduler'||id==='task-graph'?'plan-workflow':id==='generate-tests'||id==='verify-delivery'||id==='host'||id==='module-testing'?'module-testing':'module-generation';
export const STATUS = {pending:'待执行',running:'执行中',awaiting_manager:'等待总控检查',completed:'阶段完成',handed_off:'已交接',failed:'失败',stopped:'已停止',warning:'待补充',queued:'排队中'};
export const isRunning = run => ['queued','running'].includes(run?.status);
const list = value => Array.isArray(value) ? value : [];
const cut = (value,n=360) => String(value??'').slice(0,n);
const terminal = new Set(['completed','failed','rejected','interrupted','budget_exhausted']);
const employeeId = (round,id) => `employee:${round}:${id}`;

export function timelineOf(run){
  const plans=list(run?.collaboration?.plans),rounds=list(run?.collaboration?.rounds);
  const hasHandoffGate=rounds.some(item=>item.handoff_gate==='codeagent_review')||list(run?.events).some(event=>event.stage==='handoff_checked');
  let round=1,branch='',previous='plan-workflow';const outputIndex=new Map();
  return list(run?.events).map((event,index)=>{
    const skill=event.agent||event.skill_id||event.capability||'plan-workflow';
    const stage=event.stage||'',text=cut(event.message,1200);
    let taskId=event.task_id||'',matched;
    if(stage==='search_expanding')branch=text.match(/搜索 ([^：:]+)/)?.[1]||'';
    if(stage==='employee_assigned'){
      // Old receipts have no task_id; recover only an exact title match.
      const title=text.replace(/^(?:总控按出度优先分配任务|主控联合分配任务)：/,'');
      const exact=plans.flatMap(entry=>list(entry.plan?.work_items).filter(item=>item.title===title).map(item=>({item,round:entry.round||1})));
      const bound=event.round?exact.find(m=>m.round===event.round):exact.length===1?exact[0]:null;
      if(bound){matched=bound.item;round=bound.round;}
      taskId=taskId||matched?.id||'';
    }else if(stage==='employee_tool')taskId=taskId||text.match(/^([^:]+): /)?.[1]||'';
    round=event.round||round;
    const record=rounds.find(item=>item.round===round);
    const item=matched||list(record?.plan?.work_items||plans.find(p=>(p.round||1)===round)?.plan?.work_items).find(t=>t.id===taskId);
    const employee=taskId?employeeId(round,taskId):'';
    let source=skill,target=skill,kind='阶段事件',inferred=false,status='running',summary=cut(event.summary||text,1200);
    if(stage==='allocation_solved'||stage==='solver'){
      source='plan-workflow';target='constraint-scheduler';kind='约束调度';status='completed';
    }else if(stage==='employee_assigned'){
      source='plan-workflow';target=employee||skill;kind='主控派工';
    }else if(stage==='handoff_checked'){
      source='plan-workflow';target=employee||skill;kind='总控检查';status=event.ok===false?'warning':'running';
    }else if(stage==='employee_tool'||stage==='employee_handoff'){
      const done=stage==='employee_handoff'||(!hasHandoffGate&&/(?:finish|apply_patch) 完成$/.test(text));
      source=employee||skill;target=done?'plan-workflow':'host';kind=done?'产物回传':'工具执行';
      status=done?'handed_off':text.endsWith('被拒绝')?'warning':hasHandoffGate&&/(?:finish|apply_patch) 完成$/.test(text)?'awaiting_manager':'running';
      const handoff=list(record?.messages).find(m=>m.kind==='handoff'&&m.sender?.endsWith('/'+taskId));
      if(done&&handoff?.content?.message)summary=cut(handoff.content.message,1200);
    }else if(stage==='completed'){
      source=skill;target='plan-workflow';kind='阶段产物回传';status='completed';
      const ordinal=outputIndex.get(skill)||0;outputIndex.set(skill,ordinal+1);
      const results=list(run?.model_calls).filter(c=>c.skill_id===skill&&c.result&&(c.result.summary||c.result.requirements));
      const output=results[ordinal]?.result;
      if(output?.summary)summary=cut(output.summary,1200);
      else if(output?.requirements)summary=cut(list(output.requirements).map(r=>`${r.id}：${r.evidence||r.behavior}`).join('\n'),1200);
    }else if(/failed|error|rejected/.test(stage))status='failed';
    else if(stage==='running'&&previous!==skill){source=previous;target=skill;kind='阶段切换（非通信记录）';inferred=true;}
    else if(stage==='public_validation'||stage==='baseline_running'){
      source=skill;target='host';kind='执行冻结测试';
    }
    if(stage==='running'||stage==='completed')previous=skill;
    return {id:`${run?.id||'empty'}:${index}`,index,time:event.time||'',skill,stage,text,summary,source,target,
      kind,inferred,status,employee,taskId,round,branch,item,workerId:event.worker_id||'',workerRole:event.worker_role||'',
      assignmentId:event.assignment_id||'',artifactIds:list(event.artifact_ids)};
  });
}

export function frameOf(run,position,timeline=timelineOf(run)){
  const end=Math.max(0,Math.min(timeline.length,Number.isFinite(position)?position:timeline.length));
  const viewed=timeline.slice(0,end),current=viewed.at(-1),nodes=CORE.map(([id,title,description,xyz])=>({id,title:id==='repair-code'?'实现与集成交接':title,description,xyz,status:'pending',files:[],message:'尚无执行记录',parent:ownerOf(id),kind:'phase'}));
  for(const module of MODULES){const existing=nodes.find(n=>n.id===module.id);if(existing)Object.assign(existing,module,{parent:null});else nodes.push({...module,parent:null,status:'pending',files:[],message:'等待实际执行'});}
  nodes.push({id:'constraint-scheduler',title:'整数约束调度器',description:'联合选择任务、专业员工实例和执行策略；求解器属于主控工具',xyz:[0,0,0],status:'pending',files:[],message:'等待约束求解记录',parent:'plan-workflow',kind:'algorithm'},
    {id:'task-graph',title:'需求与任务 DAG',description:'需求覆盖、依赖、文件归属和输入输出契约；属于主控管理的数据',xyz:[0,0,0],status:'pending',files:[],message:'等待计划形成',parent:'plan-workflow',kind:'artifact'},
    {id:'host',title:'执行底座 · WCTP / 文件工具 / 测试环境',description:'宿主实际执行模型批次、受控文件工具与测试；执行器不是员工',xyz:[0,-.4,8],status:'pending',files:[],message:'等待工具请求',parent:null,kind:'infrastructure'});
  const map=new Map(nodes.map(n=>[n.id,n])),edges=[];
  const round=current?.round||run?.collaboration?.plans?.at(-1)?.round||1;
  const record=list(run?.collaboration?.rounds).find(r=>r.round===round);
  if(!record?.allocation_plans?.length)map.get('constraint-scheduler').description='该记录尚无任务到专业员工的联合求解证据；历史阶段/时序求解与联合分配分别记录。';
  let work=list(record?.plan?.work_items||list(run?.collaboration?.plans).find(p=>(p.round||1)===round)?.plan?.work_items);
  // Forest receipts expose an isolated candidate's employee summary separately.
  if(current?.branch){
    const branch=list(run?.search_report?.nodes).find(n=>n.id===current.branch);
    if(branch?.evidence?.work_items)work=branch.evidence.work_items;
  }
  work.forEach((item,index)=>{
    const id=employeeId(round,item.id),col=index%4,row=Math.floor(index/4),employee=list(record?.employees).find(e=>e.task_id===item.id);
    const observed=viewed.find(e=>e.employee===id&&e.stage==='employee_assigned'),role=observed?.workerRole||(observed?employee?.worker_role:'')||'';
    const special=list(employee?.specialist_ids),legacyRole=special.includes('database')?'database':special.includes('backend')?'backend':special.includes('frontend')?'frontend':special[0];
    const roleName=WORKER_NAMES[role||legacyRole]||'专业';
    const node={id,title:`${roleName} Agent · ${item.skill_id==='repair-code'?'实现':'分析'} ${index+1}`,description:cut(item.title,1200),
      xyz:[(col-(Math.min(4,work.length)-1)/2)*4,.1,7+row*3.3],status:'pending',files:list(item.paths),
      message:'计划已登记，等待实际派工',specialists:special,taskId:item.id,round,parent:'module-generation',kind:'employee',
      workerId:observed?(observed.workerId||employee?.worker_id||''):'',assignmentId:observed?(observed.assignmentId||employee?.assignment_id||''):'',
      strategy:observed?employee?.execution_strategy||'':'',requirementIds:list(item.requirement_ids),dependsOn:list(item.depends_on)};
    nodes.push(node);map.set(id,node);
    for(const dependency of list(item.depends_on))edges.push({source:employeeId(round,dependency),target:id,kind:'依赖输入（经主控）',planned:true});
  });
  for(const e of viewed){
    const node=map.get(e.stage==='allocation_solved'||e.stage==='solver'?'constraint-scheduler':e.employee||e.skill);
    if(node){node.message=e.summary;node.lastEvent=e;node.status=e.status;}
    if(e.stage==='employee_assigned'&&node){
      node.description=e.item?.title||e.text;
      // The host dispatches only ready tasks and passes dependency_outputs.
      // Draw this input transfer only after its source's observed handoff.
      for(const dependency of list(e.item?.depends_on)){
        const source=employeeId(e.round,dependency);
        if(map.get(source)?.status==='handed_off')edges.push({source,target:node.id,kind:'依赖产物（经主控）',planned:false,eventId:e.id});
      }
    }
    if(e.target==='host'){const host=map.get('host');host.message=e.text;host.status=e.stage==='employee_tool'?'completed':'running';}
    if(e.source!==e.target&&!e.inferred&&map.has(e.source)&&map.has(e.target))edges.push({source:e.source,target:e.target,kind:e.kind,planned:false,eventId:e.id,evidence:{taskId:e.taskId,assignmentId:e.assignmentId,artifactIds:e.artifactIds}});
  }
  const latest=end===timeline.length;
  if(latest&&terminal.has(run?.status)){
    for(const node of nodes){
      const capability=list(run?.workflow?.capabilities).find(c=>c.skill_id===node.id);
      // Use recorded status; a terminal run alone never proves every node passed.
      if(capability&&['completed','failed','pending'].includes(capability.status))node.status=capability.status;
      if(node.status==='running')node.status='stopped';
    }
  }
  const controller=map.get('plan-workflow');if(end)controller.status=latest&&terminal.has(run?.status)?run.status==='completed'?'completed':'stopped':'running';
  const graph=map.get('task-graph');if(work.length&&viewed.some(e=>e.skill==='plan-workflow'&&e.stage==='completed')){graph.status='completed';graph.message=`本轮计划 ${work.length} 项任务；所有注册需求保持覆盖，依赖与文件权限由宿主核验。`;}
  const allocationEvent=[...viewed].reverse().find(e=>e.assignmentId);
  const allocation=list(record?.allocation_plans).find(a=>a.id===allocationEvent?.assignmentId);
  if(allocation){const scheduler=map.get('constraint-scheduler');scheduler.message=`${allocation.status}；本次可派工：${allocation.dispatch.join('、')||'继续现有员工'}；目标：完成时间 → 就绪优先级 → 声明成本与专业匹配。`;
    scheduler.planSummary=`求解记录 ${allocation.id} · 声明剩余工期 ${allocation.estimates?.makespan_seconds??'—'} 秒 · 声明 Token ${allocation.estimates?.tokens??'—'}；这些数值不是实际性能。`;}
  for(const id of ['module-generation','module-testing']){
    const children=nodes.filter(n=>n.parent===id),seen=children.some(n=>n.status!=='pending');
    map.get(id).status=!seen?'pending':latest&&terminal.has(run?.status)?run.status==='completed'?'completed':'stopped':'running';
  }
  for(const node of nodes)if(node.parent)edges.push({source:node.parent,target:node.id,kind:'模块包含',planned:true});
  const unique=new Map();
  for(const edge of edges){const key=`${edge.source}>${edge.target}:${edge.planned?'plan':'observed'}`;unique.set(key,{...edge,id:key});}
  return {nodes,edges:[...unique.values()],current,position:end,total:timeline.length,latest,round,
    active:latest&&isRunning(run),runStatus:run?.status||'empty',allocation,
    inactiveSpecialists:Object.entries(WORKER_NAMES).filter(([id])=>!nodes.some(n=>n.kind==='employee'&&n.status!=='pending'&&n.specialists?.includes(id))).map(([id,name])=>({id,name,reason:'截至此事件尚未激活该专业员工'}))};
}

export function visibleFrame(frame,group='auto'){
  const byId=new Map(frame.nodes.map(n=>[n.id,n]));
  let selectedGroup=group==='auto'?byId.get(frame.current?.employee||frame.current?.target||frame.current?.skill)?.parent||ownerOf(frame.current?.skill||'plan-workflow'):group;
  if(!MODULES.some(m=>m.id===selectedGroup))selectedGroup='plan-workflow';
  const roots=new Set(MODULES.map(m=>m.id)),children=frame.nodes.filter(n=>n.parent===selectedGroup&&n.id!=='repair-code');
  const columns=children.length<=2?2:3;
  const positioned=children.map((node,index)=>({...node,xyz:[(index%columns-(Math.min(columns,children.length)-1)/2)*5,0,Math.floor(index/columns)*3.5]}));
  const bottom=Math.max(5,Math.ceil(children.length/columns)*3.5+1);
  const nodes=[...frame.nodes.filter(n=>roots.has(n.id)||n.kind==='infrastructure').map(n=>n.kind==='infrastructure'?{...n,xyz:[0,-.4,bottom]}:n),...positioned],shown=new Set(nodes.map(n=>n.id));
  function endpoint(id){return shown.has(id)?id:byId.get(id)?.parent||id;}
  const edges=[];for(const edge of frame.edges){const source=endpoint(edge.source),target=endpoint(edge.target);if(source!==target&&shown.has(source)&&shown.has(target))edges.push({...edge,source,target,id:`${source}>${target}:${edge.planned?edge.kind:'observed'}`});}
  return {...frame,nodes,edges:[...new Map(edges.map(e=>[e.id,e])).values()],selectedGroup,layout:{centerZ:(bottom-5)/2,spanZ:bottom+7}};
}

export function publicDetail(node){
  return {title:node.title,state:STATUS[node.status]||node.status,description:cut(node.description,1200),
    message:cut(node.message,1200),files:list(node.files).join('、'),specialists:list(node.specialists).join(' · '),
    kind:({controller:'主控',module:'主模块',employee:'员工实例',phase:'流程能力',algorithm:'算法工具',artifact:'任务数据',infrastructure:'执行底座'})[node.kind]||'',
    assignment:node.planSummary||(node.workerId?`员工：${node.workerId} · 策略：${node.strategy||'历史记录未报告'} · 分配：${node.assignmentId||'历史记录未报告'}`:''),
    contract:node.taskId?`任务 ${node.taskId} · 需求 ${list(node.requirementIds).join('、')||'见任务合同'} · 前置 ${list(node.dependsOn).join('、')||'无'}`:''};
}
