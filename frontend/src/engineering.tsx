import { StrictMode, useEffect, useMemo, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { setNonce } from 'get-nonce';
import { ReactFlow, Background, Controls, MiniMap, Handle, Position, MarkerType, useNodesState, useNodesInitialized, useReactFlow, type NodeProps, type Node } from '@xyflow/react';
import { Play, GitBranch, Workflow, ListChecks, Blocks, ExternalLink, RefreshCw, Code2, Download, ChevronRight, Activity, Maximize2, Minimize2, Home } from 'lucide-react';
import { graphOf, type Row } from './lib/engineering-graph';
import '../../web/theme.css';
import '@xyflow/react/dist/style.css';
import './engineering.css';

const nonce = document.querySelector<HTMLMetaElement>('meta[name="style-nonce"]')?.content;
if (nonce && nonce !== '__TESTFIX_STYLE_NONCE__') setNonce(nonce);
const labels: Row = { pending: '待执行', running: '执行中', completed: '已完成', failed: '有失败', selected: '选定候选', root: '策略入口',
  handed_off: '已交接', awaiting_manager: '等待总控检查', warning: '待补充', passed: '通过', failure: '失败', defined: '已生成', budget_exhausted: '预算耗尽', queued: '排队中', rejected: '未通过验收', interrupted:'已中断', settled:'已结束' };
const views = [['workflow', '工程流程', Workflow], ['employees', 'Agent 分工', Blocks], ['search', '候选搜索树', GitBranch], ['tests', '自动化测试', ListChecks]] as const;

async function json(url: string, options?: RequestInit) {
  const response = await fetch(url, { cache: 'no-store', ...options });
  const value = await response.json();
  if (!response.ok) throw new Error(typeof value.detail === 'string' ? value.detail : JSON.stringify(value.detail));
  return value;
}

function EngineeringNode({ data, selected }: NodeProps<Node<Row>>) {
  return <div className={`eg-node ${selected ? 'is-selected' : ''} eg-${data.status}`}>
    <Handle type="target" position={(data.targetSide || Position.Left) as Position} isConnectable={false} />
    <div className="eg-node-top"><span className="eg-node-icon"><Code2 size={16}/></span><span>{labels[data.status] || data.status}</span><i/></div>
    <strong>{data.title}</strong><p>{data.subtitle}</p>
    <Handle type="source" position={(data.sourceSide || Position.Right) as Position} isConnectable={false} />
  </div>;
}
const nodeTypes = { engineering: EngineeringNode };

function FitMeasuredGraph({identity}:{identity:string}) {
  const initialized=useNodesInitialized();
  const {fitView}=useReactFlow();
  useEffect(() => {
    if (!initialized) return;
    let frame=requestAnimationFrame(() => { frame=requestAnimationFrame(() => { void fitView({padding:.14,maxZoom:1}); }); });
    return () => cancelAnimationFrame(frame);
  },[initialized,identity,fitView]);
  return null;
}

function EngineeringWorkbench() {
  const [project, setProject] = useState('Project/flaskr');
  const [projects, setProjects] = useState<Row[]>([]);
  const [runs, setRuns] = useState<Row[]>([]);
  const [runId, setRunId] = useState(new URLSearchParams(location.search).get('run') || '');
  const [run, setRun] = useState<Row | null>(null);
  const [view, setView] = useState('workflow');
  const [expanded,setExpanded]=useState(false);
  const [request, setRequest] = useState('完成已注册的功能缺口，保留原有行为；根据真实测试反馈重新分配与修正。');
  const [mode, setMode] = useState('forest');
  const [iterations, setIterations] = useState(6);
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [seenAt, setSeenAt] = useState('');
  useEffect(() => { json('/api/projects').then(data => setProjects(data.projects)).catch(error => setError(error.message)); }, []);
  useEffect(() => {
    let live = true, timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const listing = await json('/api/runs');
        if (!live) return;
        const values = listing.runs.filter((item: Row) => item.kind === 'autonomous_project');
        setRuns(values);
        const identity = runId || values[0]?.id;
        if (identity) {
          const data = await json('/api/runs/' + identity);
          if (!live) return;
          setRun(data);
        }
        setSeenAt(new Date().toLocaleTimeString('zh-CN'));
      } catch (error) { if (live) setError((error as Error).message); }
      if (live) timer = setTimeout(poll, 1600);
    }
    void poll();
    return () => { live = false; clearTimeout(timer); };
  }, [runId]);
  useEffect(() => { setSelected(null); }, [view, runId]);
  useEffect(() => { if (run?.id) {
    setProject(run.project_id); setRequest(run.collaboration_request || request);
    setMode(run.search?.mode || 'none'); setIterations(run.search?.iterations || 6);
  } }, [run?.id]);
  const graph = useMemo(() => graphOf(run, view), [run, view]);
  const [nodes,setNodes,onNodesChange] = useNodesState<Node<Row>>([]);
  useEffect(() => { setNodes(previous => graph.nodes.map(node => {
    const existing = previous.find(item => item.id === node.id);
    return {...existing,...node,position:existing?.position || node.position};
  })); },[graph,setNodes]);
  const edges = graph.edges.map(edge => ({ ...edge, type: 'smoothstep', markerEnd: { type: MarkerType.ArrowClosed, color: '#8d9bb0' },
    style: { stroke: edge.id === 'retry-loop' ? '#d5ad75' : '#668fa6', strokeWidth: 1.6 }, labelStyle: { fill: '#a9c2d6', fontSize: 11 }, labelBgStyle: { fill: '#111d2c', fillOpacity: .95 } }));
  const detail = nodes.find(node => node.id === selected)?.data;
  const task = run?.tasks?.[0];
  const generated=task?.generated || task?.generated_baseline;
  const regression=task?.regression || task?.regression_baseline;
  const searchNodes = run?.search_report?.nodes?.filter((node: Row) => node.parent_id) || [];
  const freshCalls = run?.model_calls?.filter((call: Row) => !['frozen_foundation_reuse','public_checkpoint_reuse'].includes(call.provider)).length || 0;
  const reusedCalls = (run?.model_calls?.length || 0) - freshCalls;
  async function start() {
    setError(''); setSubmitting(true);
    try {
      const data = await json('/api/project-runs', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({
        project_id: project, provider: 'codex_cli', model: 'gpt-5.6-luna', collaboration_request: request,
        ...(mode !== 'none' ? {search: {mode, trees: mode === 'mcts' ? 1 : 3, iterations, depth: 3, width: 2, min_successes: mode === 'mcts' ? 1 : 3}} : {}),
      }) });
      setRunId(data.id); history.replaceState(null,'', '/engineering?run=' + data.id);
    } catch (error) { setError((error as Error).message); }
    finally { setSubmitting(false); }
  }
  return <div className="engineering-app">
    <aside className="eg-sidebar"><a className="eg-brand" href="/"><span>无遁</span><b>TestFix<small>ENGINEERING WORKSPACE</small></b></a>
      <div className="eg-space"><span className="eg-live-dot"/> 本地工程实验室</div>
      <nav aria-label="工作台导航"><a href="/tasks"><Workflow size={17}/> 任务中心</a><a href="/intake"><Code2 size={17}/> 创建任务 / 导入工程</a><a href="/"><Home size={17}/> 返回首页</a><a className="active" href="/engineering" aria-current="page"><Workflow size={17}/> 工程协作工作台</a><a href="/fea"><Blocks size={17}/> CodeAgent 3D 工作台</a><a href="/lab"><GitBranch size={17}/> 3D 流程实验室</a><a href="/hardware"><Activity size={17}/> 硬件自动化测试</a><a href="/web-testing"><ExternalLink size={17}/> 网页产品验证</a><a href="/explorer"><Blocks size={17}/> 3D 系统拆解</a></nav>
      <div className="eg-section-label">运行记录 <span>{runs.length}</span></div><div className="eg-run-list">{runs.slice(0,16).map(item => <button key={item.id} className={(runId || run?.id) === item.id ? 'active' : ''} onClick={() => {setRunId(item.id); history.replaceState(null,'','/engineering?run=' + item.id);}}><i className={`eg-status-dot eg-${item.status}`}/><span>{item.id.slice(0,8)}<small>{labels[item.status] || item.status} · {new Date(item.created_at).toLocaleDateString('zh-CN')}</small></span><ChevronRight size={13}/></button>)}</div>
      <footer>WCTP 执行引擎<small>模型决策 · 宿主执行 · 证据验证</small></footer>
    </aside>
    <main className="eg-main"><header className="eg-topbar"><span><a href="/"><Home size={14}/> 返回首页</a><ChevronRight size={14}/> <b>功能补全与产品验证</b></span><span><i className="eg-live-dot"/>{seenAt ? `同步于 ${seenAt}` : '正在连接'}</span></header>
      <nav className="eg-mobile-nav" aria-label="切换工作台"><a href="/tasks">任务中心</a><a href="/intake">创建任务</a><a href="/engineering" aria-current="page">节点连线</a><a href="/fea">CodeAgent 3D</a><a href="/lab">3D 流程实验室</a><a href="/explorer">3D 系统拆解</a><a href="/hardware">硬件测试</a><a href="/web-testing">网页验证</a></nav>
      <section className="eg-title"><div><p>MULTI-AGENT ENGINEERING</p><h1>工程协作工作台 <span>{labels[run?.status] || '准备就绪'}</span></h1><div className="eg-project-line">{run?.project_id || project}<code>{run?.id?.slice(0,12) || '尚未启动任务'}</code></div></div><a href={run ? `/api/runs/${run.id}/report?format=md` : '/api/projects'} target="_blank"><ExternalLink size={15}/> 查看报告</a></section>
      <section className="eg-command"><div className="eg-request"><label htmlFor="request">告诉 CodeAgent 你要完成什么</label><textarea id="request" value={request} onChange={event => setRequest(event.target.value)}/></div><div className="eg-config"><label>工程<select value={project} onChange={event => setProject(event.target.value)}>{projects.map(item => <option key={item.id} value={item.id}>{item.title}</option>)}</select></label><div><label>搜索策略<select value={mode} onChange={event => setMode(event.target.value)}><option value="forest">多树 MCTS</option><option value="mcts">单树 MCTS</option><option value="none">线性协作基线</option></select></label><label>候选次数<input aria-label="候选次数" type="number" min={1} value={iterations} onChange={event => setIterations(Number(event.target.value))}/></label></div><button className="eg-start" disabled={!seenAt || submitting || ['queued','running'].includes(run?.status) || !Number.isInteger(iterations) || iterations < 1} onClick={start}><Play size={15}/>{submitting ? '正在提交' : '开始真实补全'}</button></div></section>
      {error && <div className="eg-error" role="alert">{error}</div>}
      <section className="eg-metrics"><div><span>新模型调用{reusedCalls ? ` · 复用 ${reusedCalls}` : ''}</span><b>{freshCalls}</b></div><div><span>独立候选</span><b>{searchNodes.length}</b></div><div><span>生成测试{!task?.generated && generated ? ' · 原始工程' : ''}</span><b>{generated ? `${generated.passed}/${generated.total}` : '待执行'}</b></div><div><span>原有回归{!task?.regression && regression ? ' · 原始工程' : ''}</span><b>{regression ? `${regression.passed}/${regression.total}` : '待执行'}</b></div><div><span>独立验收</span><b>{task?.acceptance ? `${task.acceptance.passed}/${task.acceptance.total}` : '待执行'}</b></div><div><span>构建与运行</span><b>{task?.delivery ? task.delivery.build?.passed && task.extended?.successful ? '已验证' : '未通过' : '待执行'}</b></div></section>
      <section className={`eg-workspace ${expanded ? 'eg-expanded' : ''}`}><div className="eg-tabs">{views.map(([id,label,Icon]) => <button key={id} className={view === id ? 'active' : ''} onClick={() => setView(id)}><Icon size={16}/>{label}{id === 'search' && <small>{searchNodes.length}</small>}</button>)}<span>拖拽节点 · 滚轮缩放 · 点击查看证据</span><button className="eg-expand" aria-label={expanded ? '收起画布' : '展开画布'} onClick={() => setExpanded(!expanded)}>{expanded ? <Minimize2 size={16}/> : <Maximize2 size={16}/>}</button></div>
        <div className={`eg-canvas-row ${detail ? 'has-detail' : ''}`}><div className="eg-canvas"><ReactFlow colorMode="dark" key={`${view}:${run?.id || 'idle'}`} nodes={nodes} edges={edges} nodeTypes={nodeTypes} minZoom={.15} maxZoom={1.6} nodesConnectable={false} edgesReconnectable={false} deleteKeyCode={null} onNodeClick={(_,node) => setSelected(node.id)} onPaneClick={() => setSelected(null)} onNodesChange={onNodesChange}><FitMeasuredGraph identity={`${view}:${run?.id}:${nodes.length}:${expanded}`}/><Background gap={22} size={1} color="#30475e"/><Controls showInteractive={false}/><MiniMap style={{width:130,height:90}} bgColor="#111d2c" maskColor="#050b14b0" pannable zoomable nodeColor={node => node.data.status === 'selected' ? '#91dcd8' : '#41687c'}/></ReactFlow>{!nodes.length && <div className="eg-empty"><GitBranch size={28}/><h2>{view === 'search' ? '等待候选分支' : '等待模型生成步骤'}</h2><p>开始任务后，这里显示实际执行记录。</p></div>}</div>
          {detail && <aside className="eg-inspector"><header><span>节点详情</span><button onClick={() => setSelected(null)}>关闭</button></header><h2>{detail.title}</h2><p>{detail.subtitle}</p><p>状态：{labels[detail.status] || detail.status}</p>{detail.detail?.evaluation && <p>公开测试：{detail.detail.evaluation.generated_passed}/{detail.detail.evaluation.generated_total}<br/>原有回归：{detail.detail.evaluation.regression_passed}/{detail.detail.evaluation.regression_total}<br/>访问次数：{detail.detail.visits}</p>}<details><summary>查看原始证据</summary><pre>{JSON.stringify(detail.detail,null,2)}</pre></details></aside>}</div>
      </section>
      <section className="eg-bottom"><div><h2><Activity size={16}/> 实时执行记录</h2><div className="eg-events">{(run?.events || []).slice(-14).reverse().map((item: Row,index: number) => <div key={index}><time>{item.time ? new Date(item.time).toLocaleTimeString('zh-CN',{timeZone:'Asia/Shanghai'}) : ''}</time><span>{item.message || item.detail || item.status}</span></div>)}{!run?.events?.length && <p>尚无执行记录。工程流程中的节点表示计划，不代表已运行。</p>}</div></div><div><h2><ListChecks size={16}/> 验证与交付</h2><p>{run?.error || '候选搜索只读取公开测试反馈。选定后执行独立验收、构建与真实运行检查。'}</p>{task?.extended && <p>真实运行检查：{task.extended.passed}/{task.extended.total}</p>}{run?.download && <a className="eg-download" href={`/api/project-runs/${run.id}/download`}><Download size={15}/> 下载已验收工程</a>}{run?.preview?.url ? <a href={run.preview.url} target="_blank">打开补全后的真实应用 <ExternalLink size={13}/></a> : <p>通过独立验收后，可启动对应候选的真实应用。</p>}</div></section>
    </main>
  </div>;
}
createRoot(document.getElementById('root')!).render(<StrictMode><EngineeringWorkbench/></StrictMode>);
