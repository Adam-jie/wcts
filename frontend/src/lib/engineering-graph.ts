export type Row = Record<string, any>;
export type GraphNode = { id: string; type: string; position: { x: number; y: number }; data: Row };
export type GraphEdge = { id: string; source: string; target: string; label?: string; animated?: boolean };
export const CORE = [
  ['plan-workflow', 'CodeAgent 总控', '需求拆解 · 约束调度'], ['explore-repository', '仓库探索', '读取与搜索公开源码'],
  ['ground-code', '代码定位', '文件 · 符号 · 路由'], ['analyze-requirements', '功能缺口分析', '需求与实现逐项比对'],
  ['generate-tests', '自动化测试生成', '步骤 · 数据 · 预期断言'], ['diagnose-results', '失败诊断', '真实测试反馈'],
  ['repair-code', '专业 Agent 协作', '隔离候选 · WCTP 派工'], ['review-patch', '代码评审', '绑定当前候选版本'],
  ['verify-delivery', '独立验收与运行', '回归 · 构建 · HTTP / CLI'],
];

export function graphOf(run: Row | null, view: string): { nodes: GraphNode[]; edges: GraphEdge[] } {
  const make = (id: string, title: string, subtitle: string, status: string, x: number, y: number, detail: any): GraphNode =>
    ({ id, type: 'engineering', position: { x, y }, data: { title, subtitle, status, detail } });
  if (view === 'search') {
    const raw: Row[] = run?.search_report?.nodes || [];
    const rows: Record<number, number> = {};
    const offsets: Record<number, number> = {};
    let offset = 0;
    for (const tree of [...new Set(raw.map(item => item.tree))].sort((a,b) => a-b)) {
      offsets[tree] = offset; offset += Math.max(196, (raw.filter(item => item.tree === tree).length - 1) * 145);
    }
    return {
      nodes: raw.map(item => {
        const lane = rows[item.tree] || 0; rows[item.tree] = lane + 1;
        return make(item.id, item.parent_id ? `候选 ${item.iteration}` : `策略树 ${item.tree + 1}`,
          item.parent_id ? `公开测试 ${item.evaluation?.generated_passed ?? '—'}/${item.evaluation?.generated_total ?? '—'} · 访问 ${item.visits}` : ['最小修改', '接口契约', '边界与恢复'][item.tree % 3],
          item.id === run?.search_report?.selected_id ? 'selected' : item.status === 'expanding' ? 'running' : item.evaluation?.public_pass ? 'completed' : item.parent_id ? 'failed' : 'root',
          item.depth * 310, offsets[item.tree] + (lane > 1 ? (lane - 1) * 140 : 0), item);
      }),
      edges: raw.filter(item => item.parent_id).map(item => ({ id: `${item.parent_id}-${item.id}`, source: item.parent_id,
        target: item.id, label: item.evaluation ? `r=${Number(item.evaluation.reward).toFixed(3)}` : '执行中' })),
    };
  }
  if (view === 'employees') {
    const active = [...(run?.search_report?.nodes || [])].reverse().find((item: Row) => item.status === 'expanding');
    const chosen = run?.search_report?.nodes?.find((item: Row) => item.id === run?.search_report?.selected_id)
      || active
      || [...(run?.search_report?.nodes || [])].reverse().find((item: Row) => item.evidence?.work_items);
    const managerCall = [...(run?.model_calls || [])].reverse().find((item: Row) => item.skill_id === 'plan-workflow'
      && item.request?.context?.search_context?.node_id === chosen?.id && item.result?.team_plan);
    const round = run?.collaboration?.rounds?.at(-1);
    const plan = chosen?.evidence?.work_items || managerCall?.result.team_plan.work_items || round?.plan?.work_items || run?.collaboration?.plans?.at(-1)?.plan?.work_items || [];
    const employees = chosen?.evidence?.employees || round?.employees || [];
    const start = [...(run?.events || [])].reverse().find((item: Row) => item.stage === 'search_expanding' && item.message?.includes(`搜索 ${chosen?.id}：`));
    const levels: Record<string, number> = {};
    function level(item: Row, seen = new Set<string>()): number {
      if (seen.has(item.id)) return 0; seen.add(item.id);
      return item.depends_on.length ? 1 + Math.max(...item.depends_on.map((key: string) => {
        const parent = plan.find((entry: Row) => entry.id === key); return parent ? level(parent, new Set(seen)) : 0;
      })) : 0;
    }
    const nodes = [make('manager', 'CodeAgent 总控', '分配 · 汇总 · 二次修改', active ? 'running' : 'root', 0, 120, { request: run?.collaboration_request })];
    const edges: GraphEdge[] = [];
    plan.forEach((item: Row) => {
      const depth = level(item), row = levels[depth] || 0; levels[depth] = row + 1;
      const employee = employees.find((entry: Row) => entry.task_id === item.id);
      const call = [...(run?.model_calls || [])].reverse().find((entry: Row) => entry.request?.context?.assigned_task?.id === item.id
        && entry.request?.context?.search_context?.node_id === chosen?.id);
      const observed = start && [...(run?.events || [])].reverse().find((entry: Row) => entry.time >= start.time
        && ['employee_tool', 'handoff_checked', 'employee_handoff'].includes(entry.stage)
        && (entry.task_id === item.id || entry.message?.startsWith(item.id + ': ')));
      const observedStatus = observed?.stage === 'employee_handoff' ? 'handed_off'
        : observed?.stage === 'handoff_checked' && observed.ok === false ? 'warning' : observed ? 'running' : 'pending';
      const specialistIds = employee?.specialist_ids || call?.specialist_assignment?.specialists?.map((entry: Row) => entry.id);
      nodes.push(make(item.id, item.title, specialistIds?.join(' · ') || item.skill_id,
        employee?.status || (active ? observedStatus : 'pending'), 310 * (depth + 1), row * 190,
        { task: item, employee, observed_host_event:observed, last_model_action:call && {invocation_id:call.invocation_id,status:call.status,action:call.result?.action} }));
      for (const source of item.depends_on.length ? item.depends_on : ['manager']) edges.push({ id: `${source}-${item.id}`, source, target: item.id, label: source === 'manager' ? '分配' : '依赖交接' });
    });
    return { nodes, edges };
  }
  if (view === 'tests') {
    const task = run?.tasks?.[0];
    const steps: string[] = task?.test_steps || [];
    const nodes = steps.map((step, i) => make(`step-${i}`, `步骤 ${i + 1}`, step,
      'defined', i % 3 * 310, Math.floor(i / 3) * 190,
      { step, coverage: task?.requirement_coverage, baseline: task?.generated_baseline, candidate: task?.generated,
        note: '步骤文本来自模型测试产物；单项执行结果以冻结测试用例记录为准。' }));
    const edges: GraphEdge[] = nodes.slice(1).map((item, i) => ({ id: `step-edge-${i}`, source: nodes[i].id, target: item.id, label: '测试说明顺序' }));
    const cases: Row[] = task?.generated?.cases || task?.generated_baseline?.cases || [];
    const y = Math.ceil(steps.length / 3) * 190 + 60;
    if (cases.length) {
      const suite=task?.generated || task?.generated_baseline;
      nodes.push(make('pytest-suite', '冻结测试执行结果', task?.generated ? '当前候选' : '原始基线', suite?.successful ? 'passed' : 'failed', 310, y, suite));
      cases.forEach((item,i) => {
        nodes.push(make('case-' + i, item.id.split('::').at(-1), item.id, item.status, i % 3 * 310, y + 190 + Math.floor(i/3) * 180, item));
        edges.push({id:'case-edge-' + i, source:'pytest-suite',target:'case-' + i,label:'实际 pytest 用例'});
      });
    }
    return { nodes, edges };
  }
  const capabilities = run?.workflow?.capabilities || [];
  const historicalSettlement=(id:string) => {
    const capability=capabilities.find((entry:Row)=>entry.skill_id===id);
    return run?.status==='completed' && run?.tasks?.[0]?.verdict?.satisfies_tested_requirements===true
      && capability?.status==='running' && capability?.active_model_calls===0 && !!capability?.artifact;
  };
  const nodes = CORE.map(([id, title, subtitle], index) => make(id, title, subtitle,
    historicalSettlement(id) ? 'settled' : capabilities.find((entry: Row) => entry.skill_id === id)?.status || 'pending', (Math.floor(index / 3) % 2 ? 2 - index % 3 : index % 3) * 320, Math.floor(index / 3) * 190,
    { capability: capabilities.find((entry: Row) => entry.skill_id === id), events: run?.events?.filter((entry: Row) => entry.skill_id === id || entry.capability === id || entry.agent === id),
      status_note:historicalSettlement(id) ? '历史阶段状态未回收；本次验收已完成且无活动调用。原始状态保留。' : undefined,
      artifact: run?.workflow?.capabilities?.find((entry: Row) => entry.skill_id === id)?.artifact,
      quality: id === 'generate-tests' ? run?.test_quality : undefined,
      output: run?.model_calls?.filter((entry: Row) => entry.skill_id === id).at(-1)?.result })).map((node,index) => ({...node,data:{...node.data,
        targetSide: index % 3 === 0 && index ? 'top' : Math.floor(index/3) % 2 ? 'right' : 'left',
        sourceSide: index % 3 === 2 && index < 8 ? 'bottom' : Math.floor(index/3) % 2 ? 'left' : 'right'}}));
  const edges: GraphEdge[] = CORE.slice(1).map(([target], index) => ({ id: `core-${index}`, source: CORE[index][0], target,
    label: index === 4 ? '失败证据' : index === 7 ? '批准后验收' : '产物交接', animated: capabilities.find((entry: Row) => entry.skill_id === target)?.status === 'running' }));
  edges.push({ id: 'retry-loop', source: 'repair-code', target: 'diagnose-results', label: '公开测试失败 → 诊断与重新分配' });
  return { nodes, edges };
}
