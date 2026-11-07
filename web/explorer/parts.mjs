import {generationStages,nestedCapabilities} from './details.mjs';
// Architecture ownership only; these are not live workers or execution results.
export const groups = [
  {id:'controller',name:'CodeAgent 主控',short:'规划 · 分配 · 汇总',icon:'core',color:'#96e5ea'},
  {id:'generation',name:'多 Agent 生成与补全',short:'专业分工 · 协作实现',icon:'code',color:'#b8adf0'},
  {id:'tests',name:'自动化测试',short:'生成步骤 · 实测 · 验收',icon:'test',color:'#9ddcc3'},
];
const rootSpecs = [
  {id:'controller',english:'CODEAGENT CONTROL',base:[0,2.0,-.8],spread:[0,1.25,-.35],label:[-1,0],geometry:'controller',mark:'CA',
    description:'系统唯一的主控入口。理解需求、规划依赖和预算，把任务交给生成补全与自动化测试，汇总真实回执后决定交付或重新分配。',
    duties:['分析目标与现有工程，形成任务图和可核验的完成条件。','按专业、依赖、文件冲突及资源预算分配员工任务。','汇总补丁和公开测试结果，安排下一轮修改并输出最终结论。'],skills:['plan-workflow'],
    parameters:[['架构角色','主控；其余两大模块向其回报'],['内部能力','需求规划、人员路由、约束调度、候选搜索、模型接入、反馈汇总'],['调度机制','CP-SAT 硬约束 + 依赖就绪排序 + UCT 候选搜索'],['执行边界','实际能力由已注册工程适配器决定']],
    upstream:['用户需求与目标工程','功能描述、已有源码、预算和验收要求'],downstream:['生成补全 / 自动化测试','分配任务，接收产物与实际测试证据，再决定继续修改或交付。'],
    note:'ILP、搜索和模型接入是主控内部能力，不再作为与三大模块并列的系统。'},
  {id:'generation',english:'MULTI-AGENT GENERATION',base:[-2.1,.62,.85],spread:[-.5,.16,.25],label:[-1,0],geometry:'module',mark:'MA',
    description:'由主控按任务激活的专业 Agent 团队。一次展示工程分析、任务分配、专业实现、MCTS 候选搜索、诊断评审与集成交接；搜索扩展内部会重新派工和调用员工。',
    duties:['基于代码证据选择需要的专业；不需要的专业保持不激活并记录原因。','各 Agent 遵循 Skill、文件归属与工具契约，在隔离候选中协作修改。','整合候选补丁、诊断公开失败并评审，交给测试模块实际运行。'],skills:['explore-repository','ground-code','analyze-requirements','diagnose-results','repair-code','review-patch'],
    ordered:true,
    parameters:[['展开顺序','工程分析 → 任务分配 → 专业员工 → 候选搜索 → 诊断评审 → 集成交接'],['实际依赖','按任务图执行；专业员工在搜索扩展内反复调用，诊断按失败触发'],['专业 Skill','10 类专业；按源码信号与任务证据加载'],['协作方式','员工可并发推理，冲突文件由宿主串行提交'],['能力状态','已适配执行 / 不适用 / 缺少适配器']],
    upstream:['主控下发的实现任务','需求条目、相关源码、允许修改范围与公开失败证据'],downstream:['自动化测试','提交绑定源码版本的候选工程、补丁、生成理由和评审回执。'],
    links:[{id:'specialist-team',label:'展开专业 Agent 的职责与 Skill'},{id:'candidate-search',label:'展开 MCTS / UCT 候选空间树'}],
    note:'专业 Skill 的存在不代表该领域已实现通用自动构建；MCU/MPU 等仍须具备对应工程与设备适配器。'},
  {id:'tests',english:'AUTOMATED TESTING',base:[2.1,.62,.85],spread:[.5,.16,.25],label:[1,0],geometry:'module',mark:'QA',
    description:'将需求转为测试步骤、输入数据和断言，由框架真实执行。循环、多数据、产品质量检查和独立验收都属于这一模块，结果回报主控。',
    duties:['生成可执行步骤与数据，审核断言并冻结用例，保留补全前后的同套测试。','运行已有回归、循环多数据与适配器支持的产品检查，记录退出码和日志。','用补全 Agent 无权修改的独立验收核验最终候选，归档可复现的报告。'],skills:['generate-tests','verify-delivery'],
    parameters:[['内部划分','测试设计、WCTP 执行、循环多数据、产品核验、独立验收、报告交付'],['评判标准','可构建、可运行、功能符合需求、回归不被破坏'],['结果来源','进程、断言、真实 HTTP / CLI 或设备回包'],['设备范围','HTTP / SSH / 串口；需用户描述协议并具备连接条件']],
    upstream:['公开需求与候选工程','主控下发验收目标，生成模块提交固定版本候选'],downstream:['CodeAgent 主控','公开失败用于重新分配；独立验收仅输出最终结论，不向补全模型泄露测试内容。'],
    note:'自动化测试是整体模块，测试生成 Agent、WCTP 和独立验收是其中的不同职责；模型文本不作为通过证据。'},
];
export const roots=rootSpecs.map(spec=>({...groups.find(g=>g.id===spec.id),...spec,parent:null,group:spec.id}));
const childSpecs = {
  controller:[
    {id:'planning',name:'需求理解与任务规划',short:'需求 → 任务图',icon:'scan',skills:['plan-workflow'],
      description:'把用户目标与工程现状整理为任务、依赖、修改范围和可验证的交付条件。',duties:['理解目标功能并核对现有工程。','生成包含依赖与输出契约的员工任务图。'],files:['testfix/project_workflow.py','testfix/team_planning.py']},
    {id:'routing',name:'专业 Agent 分配',short:'按需激活 · 缺适配阻塞',icon:'graph',
      description:'结合任务与源码证据选择专业员工；不相关的专业记录为不适用，缺少执行能力时明确标记阻塞。',duties:['识别软件、网页、数据库与嵌入式任务信号。','核对执行适配器，避免把未执行任务标成完成。'],files:['testfix/specialists.py','testfix/team_runtime.py','testfix/specialist_library/catalog.json']},
    {id:'scheduler',name:'任务调度与优先级',short:'依赖 · 出度 · 资源预算',icon:'graph',
      description:'用 OR-Tools CP-SAT 处理依赖和预算等硬约束，就绪任务按出度、关键路径与后继数量排序。',duties:['核对时间、Token、模型槽位和 CPU 槽位。','在满足依赖的任务中按优先级派工。'],files:['testfix/scheduling.py','testfix/team_planning.py']},
    {id:'search',name:'补全方案探索与选择',short:'候选探索 · 公开反馈',icon:'orbit',
      description:'采用 UCT 单树或多树搜索，在隔离候选中探索不同补全路径，用公开检查反馈选择候选，最终独立验收不参与搜索奖励。',duties:['组织单树或多树候选的探索与评估。','记录候选来源、公开检查与最终选择。'],files:['testfix/repair_search.py','testfix/search_workflow.py']},
    {id:'gateway',name:'模型与平台接入',short:'模型调用 · Skill 契约',icon:'orbit',
      description:'向配置的模型服务发送受 Skill 约束的公开上下文，校验契约与版本，记录实际调用。',duties:['加载固定 Skill 契约和模型传输适配。','记录模型返回的用量、型号与调用错误。'],files:['testfix/project_models.py','testfix/agentarts_runtime.py']},
    {id:'feedback',name:'汇总、预算与二次分配',short:'回执 → 再规划 → 交付',icon:'layers',skills:['plan-workflow'],
      description:'汇总各员工产物、公开失败和剩余预算，决定继续补全、重新分配或停止并交付证据。',duties:['绑定当前候选和员工回执，保留返工原因。','把公开失败反馈转为下一轮任务；最终验收内容保持隔离。'],files:['testfix/project_workflow.py','testfix/team_runtime.py']},
  ],
  generation:[
    {id:'analysis',name:'仓库与功能缺口分析',short:'探索 · 定位 · 需求对照',icon:'scan',skills:['explore-repository','ground-code','analyze-requirements'],
      description:'探索仓库、接口和已有测试，定位未实现、部分实现或行为不一致的功能，为补全和测试提供共同依据。',duties:['按公开代码建立结构与功能证据。','将缺口绑定到文件、接口和可测试行为。'],files:['testfix/skill_library/explore-repository/SKILL.md','testfix/skill_library/ground-code/SKILL.md','testfix/skill_library/analyze-requirements/SKILL.md']},
    {id:'frontend',name:'网页与可视化 Agent',short:'前端 · JavaScript · Three.js',icon:'code',skills:['frontend','javascript','threejs'],
      description:'负责网页交互、JavaScript / TypeScript 和 Three.js 场景；是否可真实构建与浏览器测试取决于注册适配器。',duties:['按页面需求与交互契约修改允许的文件。','检查事件、响应式布局、资源加载与图形降级。'],files:['testfix/specialist_library/frontend/SKILL.md','testfix/specialist_library/javascript/SKILL.md','testfix/specialist_library/threejs/SKILL.md']},
    {id:'backend',name:'后端与数据库 Agent',short:'接口 · 事务 · 持久化',icon:'server',skills:['backend','database'],
      description:'将接口行为和数据库约束结合处理，补全业务逻辑、状态变更及持久化，并交给测试模块验证。',duties:['核对接口输入、错误处理与数据约束。','保留迁移与重启后的数据一致性证据。'],files:['testfix/specialist_library/backend/SKILL.md','testfix/specialist_library/database/SKILL.md']},
    {id:'software',name:'软件与集成 Agent',short:'业务逻辑 · 模块集成',icon:'layers',skills:['software','integration'],
      description:'补全软件业务模块与跨模块交接，整合专业员工的产物，处理接口和依赖不一致。',duties:['按任务契约完成业务逻辑与模块联调。','避免并发覆盖，交由宿主工具串行提交冲突修改。'],files:['testfix/specialist_library/software/SKILL.md','testfix/specialist_library/integration/SKILL.md','testfix/team_runtime.py']},
    {id:'embedded',name:'MCU / MPU 与协议 Agent',short:'固件 · 系统 · 驱动协议',icon:'core',skills:['mcu','mpu','protocol'],
      description:'根据硬件需求选择固件、嵌入式系统与协议专业。Skill 已提供约束，自动编译、烧录及真实板级测试仍需相应适配器。',duties:['依据用户提供的接口和协议定义工作范围。','缺工具链或设备连接时保留阻塞状态，不生成虚假实测结论。'],files:['testfix/specialist_library/mcu/SKILL.md','testfix/specialist_library/mpu/SKILL.md','testfix/specialist_library/protocol/SKILL.md']},
    {id:'repair',name:'诊断、补全与代码评审',short:'失败证据 → 补丁 → 评审',icon:'code',skills:['diagnose-results','repair-code','review-patch'],
      description:'针对公开失败诊断原因，在隔离候选中生成补丁，再由独立角色评审；测试是否通过交给自动化测试模块。',duties:['只修改允许范围内的源码，保护测试与参考答案。','评审意见绑定当前代码哈希，不代替真实验收。'],files:['testfix/skill_library/diagnose-results/SKILL.md','testfix/skill_library/repair-code/SKILL.md','testfix/skill_library/review-patch/SKILL.md']},
  ],
  tests:[
    {id:'test-design',name:'测试步骤与用例生成',short:'输入 · 步骤 · 断言',icon:'test',skills:['generate-tests'],
      description:'由模型根据公开需求与源码生成可执行步骤、数据和断言，审核测试是否有效，再冻结用例用于前后对照。',duties:['覆盖正常、边界和异常输入，明确每步预期行为。','审核断言并冻结测试，避免随补丁放宽标准。'],files:['testfix/skill_library/generate-tests/SKILL.md','testfix/test_quality.py','testfix/test_quality_worker.py']},
    {id:'executor',name:'测试流程执行',short:'图节点 · 进程 · 执行回执',icon:'graph',
      description:'复用校验后的 WCTP 图源码，连接任务节点与受控执行工具，运行测试进程并返回日志与退出码；图执行也被员工协作复用。',duties:['校验 WCTP 源文件哈希及节点契约。','在工程候选和允许的工具内执行，保留真实回执。'],files:['testfix/wctp_bridge.py','testfix/wctp_runtime.py','testfix/project_tools.py']},
    {id:'campaign',name:'循环与多数据测试',short:'多轮 · 多输入 · 固定计划',icon:'layers',
      description:'冻结输入与判定规则，按计划对同一产品重复、多数据运行，分别记录通过、失败、超时和未执行项。',duties:['保留每轮、每条数据和源码版本的对应关系。','预算耗尽或执行失败时明确记录未完成范围。'],files:['testfix/product_campaign.py','testfix/product_http.py']},
    {id:'product',name:'产品运行与质量核验',short:'构建 · HTTP / CLI · 设备',icon:'server',skills:['hardware-functional-testing'],
      description:'按适配器检查构建、启动、功能、回归、数据完整性、恢复与轻负载；设备用 HTTP、SSH 或串口回包核验。',duties:['核对程序能否启动及真实行为是否符合需求。','按实际支持的维度测试性能、并发、恢复和硬件协议；缺项明确记录。'],files:['testfix/project_tools.py','testfix/product_http_load.py','testfix/hardware.py','testfix/hardware_planner.py','testfix/hardware_io.py']},
    {id:'verify',name:'独立验收与回归保护',short:'固定候选 · 受保护测试',icon:'test',skills:['verify-delivery'],
      description:'对选定候选运行补全 Agent 无权改写的验收与原有回归，核对受保护文件和评审版本。验收内容不回流给补全模型。',duties:['以真实测试与源码哈希核验候选。','将最终结论交给主控，避免测试答案泄露。'],files:['testfix/project_workflow.py','testfix/skill_library/verify-delivery/SKILL.md']},
    {id:'delivery',name:'证据报告与工程交付',short:'补丁 · 日志 · 可运行工程',icon:'layers',
      description:'汇总实际完成范围、失败原因、补丁、测试报告和工程包，向主控提交可追溯的交付记录。',duties:['区分模型产物、公开检查和独立验收结果。','保留运行标识、日志、哈希与工程产物。'],files:['testfix/reports.py','testfix/project_workflow.py','testfix/project_tools.py']},
  ],
};
const formerGeneration=new Map(childSpecs.generation.map(part=>[part.id,part]));
childSpecs.generation=generationStages.map(spec=>spec.reuse?formerGeneration.get(spec.reuse):spec);
const branches={...childSpecs,...nestedCapabilities,'specialist-team':['frontend','backend','software','embedded'].map(id=>formerGeneration.get(id))};
const offsets=[[-1.27,-.32,-.90],[0,-.32,-1.42],[1.27,-.32,-.90],[-1.27,-.32,.95],[0,-.32,1.43],[1.27,-.32,.95]];
export const parts=[];
function appendPart(spec,root,parent=null,index=0){
  const depth=parent?parent.depth+1:0,offset=offsets[index%offsets.length];
  const node=parent?{
    ...spec,parent:parent.id,group:root.id,depth,sequence:index+1,color:root.color,
    english:'CAPABILITY / LEVEL '+depth+' / '+String(index+1).padStart(2,'0'),geometry:'submodule',mark:String(index+1).padStart(2,'0'),skills:spec.skills||[],
    base:[root.base[0]+offset[0],root.base[1]+(depth===1?-.32:.42+(depth-2)*.48),root.base[2]+offset[2]],
    spread:root.spread.map((v,i)=>v+(i===1?(depth-1)*.16:offset[i]*.15)),label:[offset[0]<0?-1:1,0],
    parameters:spec.parameters||[['所属主模块',root.name],['直接上级',parent.name],['功能职责',spec.short],['关联 Skill',spec.skills?.join(' / ')||'确定性工具或调度能力']],
    upstream:spec.upstream||[parent.name,'上级能力提供的任务、代码与允许读取的上下文'],
    downstream:spec.downstream||[parent.name,'返回产物、真实执行证据或未完成原因'],
    note:spec.note||(root.id==='generation'?'按现有代码展开职责与算法；导航编号不代表所有角色严格串行，实际执行由依赖图和模式决定。':root.note),
  }:{...spec,depth:0,sequence:groups.findIndex(g=>g.id===spec.id)+1};
  parts.push(node);
  (branches[node.id]||[]).forEach((child,position)=>appendPart(child,root,node,position));
}
roots.forEach(root=>appendPart(root,root));
export const primaryParts=parts.filter(part=>!part.parent);
export const childParts=id=>parts.filter(part=>part.parent===id);
export const ownerOf=id=>parts.find(part=>part.id===id)?.group;
export function pathToPart(id){
  const chain=[],seen=new Set();let current=parts.find(part=>part.id===id);
  while(current){if(seen.has(current.id))throw new Error('Circular architecture parent');seen.add(current.id);chain.unshift(current);current=parts.find(part=>part.id===current.parent);}
  return chain;
}
export const descendantsOf=id=>parts.filter(part=>pathToPart(part.id).slice(0,-1).some(ancestor=>ancestor.id===id));
export const treePartIds=(selected,expandedRoot)=>visiblePartIds(selected,expandedRoot);
export function matchingPartIds(query){
  const text=String(query).trim().toLocaleLowerCase();
  if(!text)return new Set(parts.map(part=>part.id));
  const matches=part=>[part.id,part.name,part.english,part.short,part.description,...part.skills].join(' ').toLocaleLowerCase().includes(text);
  const ids=new Set();
  for(const part of parts){
    if(!matches(part))continue;
    pathToPart(part.id).forEach(ancestor=>ids.add(ancestor.id));
    // Aggregated parent Skills must not reveal unrelated sibling Agents when a
    // more specific matching descendant exists.
    const descendants=descendantsOf(part.id);
    if(!descendants.some(matches))descendants.forEach(child=>ids.add(child.id));
  }
  return ids;
}
const fullBranches=new Map(primaryParts.map(root=>[root.id,[root.id,...descendantsOf(root.id).map(part=>part.id)]]));
export function visiblePartIds(_selected,expandedRoot){
  const root=ownerOf(expandedRoot);
  return new Set([...primaryParts.map(part=>part.id),...(fullBranches.get(root)||[])]);
}

export const icons = {
  core:'<rect x="6" y="6" width="12" height="12" rx="2"/><rect x="9" y="9" width="6" height="6" rx="1"/><path d="M9 3v3m6-3v3M9 18v3m6-3v3M3 9h3m-3 6h3m12-6h3m-3 6h3"/>',
  graph:'<rect x="3" y="3" width="5" height="5" rx="1"/><rect x="16" y="9" width="5" height="5" rx="1"/><rect x="3" y="16" width="5" height="5" rx="1"/><path d="M8 5.5h4v6h4M8 18.5h4v-7"/>',
  scan:'<path d="M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5M7 9h10M7 12h7M7 15h9"/>',
  test:'<path d="M9 3h6m-5 0v7L4 19c-.5 1 .1 2 1.2 2h13.6c1.1 0 1.7-1 1.2-2l-6-9V3M7 15h10"/><circle cx="11" cy="17.5" r=".6"/>',
  code:'<path d="m8 7-5 5 5 5m8-10 5 5-5 5m-3-13-2 16"/>',
  layers:'<path d="m12 3 10 5-10 5L2 8Zm-9 10 9 5 9-5M3 18l9 5 9-5"/>',
  server:'<rect x="3" y="3" width="18" height="7" rx="2"/><rect x="3" y="14" width="18" height="7" rx="2"/><path d="M7 6.5h.1m2.9 0h.1M7 17.5h.1m2.9 0h.1M16 6.5h2m-2 11h2"/>',
  orbit:'<circle cx="12" cy="12" r="3"/><ellipse cx="12" cy="12" rx="11" ry="5" transform="rotate(-35 12 12)"/><path d="M8 4c-1-3 2-3 4-1 5 4 8 14 5 18-2 2-6-2-8-6"/>',
};
export const iconMarkup = (id) => '<svg viewBox="0 0 24 24" aria-hidden="true">' + icons[id] + '</svg>';
