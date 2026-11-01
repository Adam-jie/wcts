// Drill-down descriptions grounded in the repository, never synthetic run results.
export const generationStages=[
  {reuse:'analysis'},
  {id:'team-dispatch',name:'任务拆解与 Skill 分配',short:'任务图 · 专业匹配 · 文件归属',icon:'graph',skills:['plan-workflow'],
    description:'接收主控的任务图，核对依赖、专业 Skill、允许修改范围和资源槽位，为员工协作准备输入。',duties:['前置任务成功后才派发就绪任务。','不适用专业不激活；缺少适配器的任务明确阻塞。'],files:['testfix/team_planning.py','testfix/team_runtime.py','testfix/specialists.py']},
  {id:'specialist-team',name:'专业 Agent 协作实现',short:'前端 · 后端 · 软件 · 嵌入式',icon:'code',
    description:'按任务激活专业员工，在 Skill 和文件归属范围内读取、搜索、修改及交接。十种专业不表示十个模型常驻，模型请求可并发，宿主文件提交受冲突检查。',duties:['展开四个专业组，进一步查看十种 Agent 的职责与 Skill。','在 MCTS 模式下，本模块由每次候选扩展重新调用；线性模式直接调用员工协作。'],files:['testfix/team_runtime.py','testfix/specialists.py','testfix/specialist_library/catalog.json']},
  {id:'candidate-search',name:'MCTS / UCT 候选空间树',short:'选择 → 扩展 → 评估 → 回传',icon:'orbit',ordered:true,
    description:'把工程版本、补丁与公开失败反馈作为搜索状态。主控决定是否启用单树或多树搜索；每次扩展复制候选、重新规划员工并执行真实公开测试，再更新树统计。这里的空间是候选代码空间。',
    duties:['逐步查看状态建模、UCT 选择、候选扩展、公开评估、反向回传与候选选择。','每轮只激活一个选中分支，分支内的员工模型可以重叠执行。','独立验收、参考答案与最终验收成绩不进入搜索奖励或模型上下文。'],
    parameters:[['代码算法','search_forest：UCT 单树 / 多树'],['默认配置','3 树、6 次扩展、宽度 2、深度 3；以运行配置为准'],['探索常数','sqrt(2)，由 SearchConfig 配置'],['奖励来源','冻结的公开生成测试 + 原有回归'],['验收隔离','independent_acceptance_used = false']],
    files:['testfix/repair_search.py','testfix/search_workflow.py'],links:[{id:'search',label:'查看主控中的搜索配置职责'}]},
  {reuse:'repair'},
  {id:'integration-handoff',name:'集成交接与再次修改',short:'整合候选 → 测试 → 再分配',icon:'layers',
    description:'核对候选、补丁和回执版本，向自动化测试交接。公开失败时将证据送回主控重新分配，选定候选仍须经过评审、独立验收和交付检查。',duties:['保留来源、哈希和员工工具证据，避免覆盖冲突修改。','按实际结果继续修改或交付；尚未执行的检查保持未执行。'],files:['testfix/project_workflow.py','testfix/search_workflow.py','testfix/team_runtime.py']},
];

const skill=(id)=>'testfix/skill_library/'+id+'/SKILL.md';
const specialist=(id,name,short,description,duties)=>({id:'agent-'+id,name,short,description,duties,icon:'code',skills:[id],files:['testfix/specialist_library/'+id+'/SKILL.md','testfix/specialists.py']});
export const nestedCapabilities={
  analysis:[
    {id:'agent-explorer',name:'仓库探索 Agent',short:'输入仓库 → 输出相关文件',icon:'scan',skills:['explore-repository'],description:'探索公开仓库结构、接口和已有测试，给出与需求相关的候选文件。',duties:['输出实际存在的文件与定位依据。','不读取独立验收或参考答案。'],files:[skill('explore-repository'),'testfix/project_workflow.py']},
    {id:'agent-grounding',name:'代码定位 Agent',short:'候选文件 → 函数与调用位置',icon:'scan',skills:['ground-code'],description:'把需求绑定到具体路径、函数和代码位置，宿主记录对应源码哈希。',duties:['保留需求编号与代码证据。','避免无依据地假设目标函数已经存在。'],files:[skill('ground-code'),'testfix/project_workflow.py']},
    {id:'agent-requirements',name:'功能缺口分析 Agent',short:'需求对照 → 缺失 / 部分 / 已实现',icon:'scan',skills:['analyze-requirements'],description:'对照需求与定位代码识别功能状态，产出补全和测试共同使用的需求矩阵。',duties:['逐项覆盖公开需求，标记未知和假设。','给出可验证的行为标准。'],files:[skill('analyze-requirements'),'testfix/project_workflow.py']},
  ],
  'team-dispatch':[
    {id:'task-contract',name:'任务图与交接契约',short:'依赖 · 输入输出 · 修改范围',icon:'graph',description:'核对员工任务的 depends_on、Skill ID、需求编号与允许修改路径。',duties:['拒绝未知任务、循环依赖和越界路径。','把前置产物交给依赖员工。'],files:['testfix/team_planning.py','testfix/team_runtime.py']},
    {id:'skill-routing',name:'专业 Skill 按需匹配',short:'源码信号 → 专业与就绪状态',icon:'scan',description:'根据任务和源码信号路由专业 Skill，并核验实际执行能力。',duties:['记录所选专业、不适用项和缺失能力。','有 Skill 文本但无适配器时不宣称能够完成实测。'],files:['testfix/specialists.py','testfix/specialist_library/catalog.json']},
    {id:'file-ownership',name:'就绪优先级与文件归属',short:'出度优先 · 模型槽位 · 串行提交',icon:'graph',description:'先满足依赖，再按出度、声明关键路径和后继数量排序；运行时约束模型槽位、文件冲突与修改版本。',duties:['并发模型决策受实际槽位限制。','宿主在屏障处核验哈希并提交不冲突的补丁。'],files:['testfix/team_planning.py','testfix/team_runtime.py','testfix/wctp_runtime.py']},
  ],
  frontend:[
    specialist('frontend','网页交互 Agent','布局 · 交互 · 可访问性','负责页面结构、交互状态和响应式行为。',['核对表单、事件、错误状态和展示数据。','按注册适配器进行页面或模板验证。']),
    specialist('javascript','JavaScript / TypeScript Agent','语言逻辑 · 异步 · 构建','负责脚本逻辑、类型和异步处理，构建及浏览器测试需相应执行适配。',['检查输入输出、依赖和资源加载。','不把静态代码检查当成完整浏览器实测。']),
    specialist('threejs','Three.js 场景 Agent','几何 · 拾取 · 动画 · 图形降级','负责程序化几何体、材质、相机与交互。',['检查拾取、视角、动画和资源释放。','验证 WebGL 不可用时的降级与恢复。']),
  ],
  backend:[
    specialist('backend','后端接口 Agent','接口契约 · 业务状态 · 错误处理','补全服务接口和业务逻辑，交付可由真实 HTTP 请求验证的行为。',['核对请求、响应、权限与异常路径。','保留原有接口和回归约束。']),
    specialist('database','数据库 Agent','数据约束 · 事务 · 重启持久化','负责数据结构、查询及持久化语义。',['核对事务边界和数据一致性。','通过适配器验证重启后的数据状态。']),
  ],
  software:[
    specialist('software','软件业务 Agent','模块逻辑 · CLI · 工程构建','实现软件业务模块与命令行行为。',['依据公开需求完成允许范围内的修改。','把可运行性和原有回归交给真实执行验证。']),
    specialist('integration','集成协作 Agent','接口衔接 · 依赖 · 跨模块联调','整合其他员工的产物与跨模块接口。',['避免并发覆盖和重复实现。','交接当前源码版本与集成风险。']),
  ],
  embedded:[
    specialist('mcu','MCU 固件 Agent','固件逻辑 · 外设 · 串口协议','依据芯片、接口和用户协议约束固件修改；工具链与板级适配必须实际具备。',['核对寄存器或外设接口及响应预期。','缺编译、烧录或设备条件时明确阻塞。']),
    specialist('mpu','MPU 系统 Agent','嵌入式系统 · 服务 · 网络接口','处理嵌入式系统和应用服务需求，真实执行需目标系统与连接适配。',['核对部署、依赖和进程状态。','按实际支持的 SSH / HTTP 接口核验行为。']),
    specialist('protocol','驱动与协议 Agent','协议帧 · 编解码 · 超时','负责接口协议、数据编解码与异常恢复约束。',['使用用户明确提供的命令和回包定义。','区分连接成功、回包成功与功能满足。']),
  ],
  'candidate-search':[
    {id:'search-state',name:'01 候选状态与根节点',short:'工程快照 · 失败反馈 · 搜索边界',icon:'layers',description:'每个根节点引用初始候选工程和公开反馈。节点保存父节点、所属树、深度、访问次数及累积价值；实际工程对象由宿主保管。',duties:['冻结公开测试及其身份，先确认基线暴露真实功能失败。','设置树数、宽度、深度、迭代额度与预算。'],files:['testfix/repair_search.py','testfix/search_workflow.py'],parameters:[['节点状态','id / tree / parent_id / depth / visits / value_sum / mean_value'],['实际候选','隔离工作区 + candidate_sha256'],['公开前提','原有回归成功；生成测试可收集且暴露断言失败']]},
    {id:'uct-select',name:'02 UCT 选择分支',short:'平均价值 + 探索项',icon:'orbit',description:'在仍可扩展的根与子节点中，用 UCT 平衡已观察收益和未充分访问的分支。未访问节点的分数为正无穷，优先探索。',duties:['排除终止节点和达到最大深度的节点。','满宽节点向可扩展的孩子继续下降。'],files:['testfix/repair_search.py'],parameters:[['已访问节点','mean_value + c × sqrt(ln(parent_visits + 1) / visits)'],['mean_value','value_sum / visits'],['未访问节点','UCT = +∞'],['探索常数','SearchConfig.exploration；默认 sqrt(2)']]},
    {id:'branch-expand',name:'03 扩展并调用员工 Agent',short:'复制候选 → 再规划 → 专业实现',icon:'code',description:'从选中父节点复制隔离候选。主控根据父节点、兄弟分支反馈和策略重新派工，员工受 Skill 与文件范围约束完成修改。',duties:['每次扩展对应一次真实候选工作流，不生成虚构随机 rollout 成绩。','深度大于 1 时先调用诊断，再执行员工任务图。'],files:['testfix/search_workflow.py','testfix/team_runtime.py'],links:[{id:'specialist-team',label:'查看被搜索扩展调用的专业 Agent'}]},
    {id:'public-score',name:'04 真实公开测试评估',short:'冻结测试 · 回归门槛 · 奖励',icon:'test',description:'在候选上运行真实公开测试，核对与基线相同的测试身份。回归未保留、收集或绑定异常、超时等情况均不具备候选资格。',duties:['只有原有回归保留的有效候选获得奖励。','独立验收既不参与奖励，也不向员工提供内容。'],files:['testfix/repair_search.py','testfix/search_workflow.py'],parameters:[['合格奖励','0.25 + 0.75 × 生成测试通过比例'],['不合格奖励','0'],['评判来源','相同冻结公开用例、断言结果及运行日志'],['独立验收','始终在候选选择后单独执行']]},
    {id:'backpropagate',name:'05 沿祖先反向回传',short:'访问次数 + 1 · 更新均值',icon:'graph',description:'从刚评估的节点沿 parent_id 回到根，累加本次实际奖励并更新访问次数和均值，供下一轮 UCT 选择使用。',duties:['visits += 1；value_sum += reward。','mean_value = value_sum / visits；只统计实际扩展。'],files:['testfix/repair_search.py'],parameters:[['回传方向','当前节点 → 父节点 → 根节点'],['更新字段','visits / value_sum / mean_value'],['下一轮','根据更新后的统计重新选择可扩展分支']]},
    {id:'forest-choose',name:'06 多树比较与候选提升',short:'停止条件 · 排序 · 提升候选',icon:'layers',description:'达到公开成功候选数、扩展额度或耗尽前沿后停止。在合格候选中排序选择，核验员工证据后提升选中版本，再进入评审与独立验收。',duties:['先比较公开全部通过，再比较奖励及相同候选哈希票数。','继续以较浅深度、较早迭代打破平局；无合格候选明确失败。'],files:['testfix/repair_search.py','testfix/search_workflow.py']},
  ],
  'branch-expand':[
    {id:'fork-candidate',name:'隔离候选与版本绑定',short:'父快照 → 分支工作区',icon:'layers',description:'fork_candidate 复制受保护的父候选，记录父哈希，防止分支修改相互覆盖。',duties:['保护冻结测试与原始工程。','保留分支标识和候选哈希。'],files:['testfix/project_tools.py','testfix/search_workflow.py']},
    {id:'employee-execution',name:'员工工具循环与协作',short:'读取 / 搜索 → 修改 → 交接',icon:'code',skills:['repair-code'],description:'员工先读文件或搜索，再根据宿主回执选择受控动作；只有修复员工可请求补丁与公开测试，文件提交由宿主完成。',duties:['执行依赖图和文件归属约束。','保存每步工具回执及其哈希。'],files:['testfix/team_runtime.py','testfix/wctp_runtime.py'],links:[{id:'specialist-team',label:'展开十种专业 Agent 的功能'}]},
    {id:'public-cache',name:'相同候选公开结果复用',short:'源码哈希 + 冻结测试身份',icon:'server',description:'相同候选哈希可复用已缓存的公开测试结果，前提是冻结生成测试字节一致，并保留原始证据来源。',duties:['标记 cached_public_result，不能冒充新执行。','只允许受控前缀重定位，不改用例名称或断言。'],files:['testfix/search_workflow.py','testfix/repair_search.py']},
  ],
  'forest-choose':[
    {id:'forest-strategies',name:'多树策略与稀疏激活',short:'最小修改 / 接口契约 / 边界恢复',icon:'orbit',description:'不同根采用 minimal_change、interface_contracts、boundary_and_recovery 策略；每轮选一个分支扩展，分支内员工可以并发推理。',duties:['一棵树对应单树模式，多棵树对应 forest 模式。','不宣称复现论文结果或已有任务分配性能提升。'],files:['testfix/repair_search.py','testfix/search_workflow.py']},
    {id:'stop-rules',name:'停止与预算规则',short:'成功候选 / 额度 / 前沿耗尽',icon:'graph',description:'满足 min_successes、迭代额度用尽或可扩展前沿耗尽时停止；真实时间或 Token 预算耗尽应向上抛出，不能伪装成正常低分分支。',duties:['记录 stop_reason 和实际已执行范围。','运行配置决定额度，页面默认值不代表某次实测配置。'],files:['testfix/repair_search.py','testfix/search_workflow.py','testfix/project_workflow.py']},
    {id:'rank-promote',name:'候选排序、提升与后续验收',short:'公开通过 → 奖励 → 哈希票数',icon:'layers',description:'对合格候选按代码中的顺序排序，验证工具证据未被改动后提升所选工程。提升不代表最终验收通过。',duties:['校验来源与当前候选版本。','将评审、公开复核、独立验收和交付分别记录。'],files:['testfix/repair_search.py','testfix/search_workflow.py','testfix/project_workflow.py'],links:[{id:'verify',label:'查看自动化测试中的独立验收'}]},
  ],
  repair:[
    {id:'agent-diagnosis',name:'失败诊断 Agent',short:'公开失败 → 原因与目标文件',icon:'scan',skills:['diagnose-results'],description:'根据需求矩阵和公开测试反馈定位问题；诊断会在修复前和深层搜索扩展时触发，不仅在最终评审阶段执行。',duties:['使用已注册的需求编号。','目标路径必须属于允许修改范围。'],files:[skill('diagnose-results'),'testfix/project_workflow.py','testfix/search_workflow.py']},
    {id:'agent-developer',name:'代码补全 Agent',short:'受控读取 → 补丁 → 公开复核',icon:'code',skills:['repair-code'],description:'在宿主允许的范围内补全代码，可以加载专业 Skill，提交与当前候选哈希绑定的文件修改。',duties:['保护测试和参考答案。','使用真实工具回执，不自行宣判功能完成。'],files:[skill('repair-code'),'testfix/team_runtime.py']},
    {id:'agent-reviewer',name:'独立代码评审 Agent',short:'当前代码哈希 → 风险与返工',icon:'scan',skills:['review-patch'],description:'检查当前候选补丁和公开测试证据，给出明确问题与修改意见，评审意见必须绑定当前候选哈希。',duties:['代码改变后旧评审不能继续作为通过依据。','评审同意仍需经过真实独立验收。'],files:[skill('review-patch'),'testfix/project_workflow.py']},
  ],
  'integration-handoff':[
    {id:'merge-promote',name:'版本与补丁整合',short:'来源核验 · 串行提交 · 候选提升',icon:'layers',description:'核对员工工具证据与候选哈希，串行整合允许的修改，保留选中分支来源。',duties:['防止并发覆盖和过期补丁提交。','记录实际变更路径与版本。'],files:['testfix/team_runtime.py','testfix/search_workflow.py','testfix/project_tools.py']},
    {id:'testing-handoff',name:'交接自动化测试',short:'固定候选 → 回归与独立验收',icon:'test',description:'把固定候选与版本化回执交给自动化测试，继续执行所需回归、构建、实际程序运行及独立验收。',duties:['不把公开搜索分数当成验收结论。','记录未执行和不支持的检查。'],files:['testfix/project_workflow.py','testfix/project_tools.py'],links:[{id:'tests',label:'进入自动化测试主模块'}]},
    {id:'replanning',name:'公开反馈与再次分配',short:'失败证据 → 主控 → 新任务图',icon:'graph',description:'将公开失败与员工交接回报主控，依据剩余预算重新规划依赖、任务及专业。最终独立验收内容保持隔离。',duties:['保留失败原因与上一轮产物。','依照真实公开回执继续修改，避免无证据重复。'],files:['testfix/project_workflow.py','testfix/team_planning.py'],links:[{id:'feedback',label:'查看 CodeAgent 的汇总与二次分配'}]},
  ],
};
