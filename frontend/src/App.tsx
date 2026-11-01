import { useEffect, useRef, useState } from 'react';
import * as Tabs from '@radix-ui/react-tabs';
import * as Tooltip from '@radix-ui/react-tooltip';
import { Cpu, Workflow, ListChecks, FileCheck2, Settings2, Plus, ArrowUpRight, PanelRightClose, PanelRightOpen, Play, Sparkles, ChevronRight, Upload, Braces, X, RefreshCw, FolderOpen, Clock3, ShieldCheck, LoaderCircle, Copy, Check, FileJson } from 'lucide-react';
import { useHardware } from './useHardware';
import { parseActions, parsePlan } from './lib/hardware';
import { Button, IconButton, Badge, Sheet, JsonEditor, JsonView } from './components/ui';
import { DeviceSettings } from './components/DeviceSettings';
import { WorkflowCanvas, type EvidenceKind } from './components/WorkflowCanvas';
import { PlanView, ResultsView } from './components/EvidenceViews';
import { targetOf, kindLabel, connectionSummary, functionSummary } from './components/helpers';

type AdvancedKind = 'profile' | 'draft' | 'actions' | 'plan' | 'report' | 'history';
const ADVANCED_LABELS: Record<AdvancedKind, string> = { profile: '设备档案 JSON', draft: '完整配置 JSON', actions: '协议动作 JSON', plan: '测试计划 JSON', report: '原始回执 JSON', history: '操作记录 JSON' };
const EVIDENCE_LABELS: Record<EvidenceKind, string> = { device: '设备参数', planner: 'CodeAgent 规划', actions: '允许的协议动作', assertions: '计划断言', result: '执行回执' };

function useWideLayout() {
  const [wide, setWide] = useState(() => window.matchMedia('(min-width: 1180px)').matches);
  useEffect(() => {
    const media = window.matchMedia('(min-width: 1180px)');
    const update = () => setWide(media.matches);
    media.addEventListener('change', update);
    return () => media.removeEventListener('change', update);
  }, []);
  return wide;
}

export default function App() {
  const controller = useHardware();
  const { draft, busy, operation, report, stale, planRecord, serviceReady } = controller;
  const wide = useWideLayout();
  const [settingsOpen, setSettingsOpen] = useState(() => window.matchMedia('(min-width: 1180px)').matches);
  const [tab, setTab] = useState('flow');
  const [selected, setSelected] = useState<EvidenceKind | null>(null);
  const [advanced, setAdvanced] = useState<AdvancedKind | null>(null);
  const [advancedText, setAdvancedText] = useState('');
  const [advancedError, setAdvancedError] = useState('');
  const [copied, setCopied] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const connection = connectionSummary(report, stale);
  const behavior = functionSummary(report, stale);
  const configured = Boolean(targetOf(draft.device));

  function openAdvanced(kind: string = 'draft', value?: unknown) {
    const type = kind as AdvancedKind;
    setAdvanced(type);
    setAdvancedError('');
    setCopied(false);
    setAdvancedText(value !== undefined ? JSON.stringify(value, null, 2) : type === 'actions' ? JSON.stringify(draft.allowed_actions, null, 2) : type === 'plan' ? JSON.stringify(draft.plan || { steps: [] }, null, 2) : type === 'report' ? JSON.stringify(report, null, 2) : controller.actions.exportProfile());
  }
  function applyAdvanced() {
    try {
      if (advanced === 'profile' || advanced === 'draft') {
        if (!controller.actions.importProfile(advancedText)) { setAdvancedError('档案未通过校验，请检查设备、需求和协议结构。'); return; }
      } else if (advanced === 'actions') {
        const actions = parseActions(advancedText);
        controller.setDraft(previous => ({ ...previous, allowed_actions: actions }));
      } else if (advanced === 'plan') {
        const plan = parsePlan(advancedText);
        controller.setDraft(previous => ({ ...previous, plan }));
      }
      setAdvanced(null);
    } catch (error) { setAdvancedError(error instanceof Error ? error.message : 'JSON 格式不正确。'); }
  }
  async function selectFile(file: File | undefined) {
    if (!file) return;
    if (file.size > 1_000_000) { setAdvancedError('设备档案应小于 1 MB。'); return; }
    setAdvancedText(await file.text());
    setAdvancedError('');
  }
  async function generate() {
    const result = await controller.actions.generatePlan();
    if (result) setTab('plan');
  }
  async function run() {
    const result = await controller.actions.run();
    if (result) setTab('results');
  }
  function selectionData() {
    if (selected === 'device') return draft.device;
    if (selected === 'planner') return planRecord || { status: '尚未调用模型', current_plan: draft.plan?.steps.length ? '手工计划' : '无计划' };
    if (selected === 'actions') return draft.allowed_actions;
    if (selected === 'assertions') return draft.plan?.steps.map(item => ({ step: item.id, action: item.action_id, assertions: item.assertions })) || { status: '尚未定义计划断言' };
    return report || { status: '尚无执行记录' };
  }

  return <Tooltip.Provider delayDuration={250}><div className={`app-frame ${wide && settingsOpen ? 'has-settings' : ''}`}>
    <aside className="sidebar"><a className="app-brand" href="/hardware" aria-label="TestFix 设备工作台"><span><Workflow size={20} /></span><b>TestFix<small>工程验证工作空间</small></b></a><div className="workspace-switch"><span className="workspace-avatar">T</span><span>本地工作空间<small>硬件功能验证</small></span><ChevronRight size={14} /></div><nav className="sidebar-nav" aria-label="工作空间导航"><button className="is-active" aria-label="设备工作台" onClick={() => setTab('flow')}><Workflow size={17} /><span>设备工作台</span></button><a href="/fea" aria-label="CodeAgent 3D 工作台"><Cpu size={17} /><span>CodeAgent 3D</span><ArrowUpRight size={13} /></a><button aria-label="运行记录" onClick={() => setHistoryOpen(true)}><Clock3 size={17} /><span>运行记录</span>{controller.history.length > 0 && <small>{controller.history.length}</small>}</button></nav><div className="sidebar-section-label"><span>设备库</span><IconButton label="新建设备草稿" disabled={busy} onClick={() => { controller.actions.loadExample('http'); setSettingsOpen(true); }}><Plus size={14} /></IconButton></div><button className="device-library-item" onClick={() => setSettingsOpen(true)}><span><Cpu size={16} /></span><span>{draft.device.name || '未命名设备'}<small>{kindLabel(draft.device.kind)}{configured ? '' : ' · 草稿'}</small></span><ChevronRight size={13} /></button><button className="sidebar-import" onClick={() => openAdvanced('profile')}><Plus size={14} /><span>导入设备档案</span></button><div className="sidebar-bottom"><button aria-label="工作台设置" onClick={() => setSettingsOpen(true)}><Settings2 size={16} /><span>工作台设置</span></button><div className="sidebar-service"><span className={serviceReady ? 'is-online' : ''} /><span>{serviceReady ? '本地服务已就绪' : controller.loadingCapabilities ? '连接服务中' : '本地服务未连接'}</span></div></div></aside>
    <main className="workspace-main"><header className="topbar"><div className="breadcrumb"><FolderOpen size={16} /><span>工作空间</span><ChevronRight size={13} /><strong>设备测试</strong></div><div className="topbar-tools"><IconButton label="刷新本地服务状态" disabled={busy || controller.loadingCapabilities} onClick={() => void controller.actions.refreshCapabilities()}><RefreshCw size={15} /></IconButton><button className="import-button" onClick={() => openAdvanced('profile')}><Upload size={14} /><span>导入档案</span></button><span className="topbar-divider" /><IconButton label={settingsOpen ? '收起设备设置' : '打开设备设置'} onClick={() => setSettingsOpen(!settingsOpen)}>{settingsOpen ? <PanelRightClose size={17} /> : <PanelRightOpen size={17} />}</IconButton></div></header>
      <section className="task-header"><div><p>硬件功能验证</p><h1>{configured ? draft.device.name || '设备测试任务' : '创建一个验证流程'}<span className="project-dot" /></h1><div className="task-target"><Cpu size={13} /><span>{targetOf(draft.device) || '先配置设备连接与协议'}</span></div></div><div className="task-actions"><Button className="configure-button" onClick={() => setSettingsOpen(true)}><Settings2 size={15} />配置设备</Button><Button disabled={busy || !serviceReady} onClick={() => void generate()}>{operation === 'planning' ? <LoaderCircle size={15} className="spin" /> : <Sparkles size={15} />}生成计划</Button><Button variant="primary" disabled={busy || !serviceReady || !draft.plan?.steps.length} onClick={() => void run()}>{operation === 'running' ? <LoaderCircle size={15} className="spin" /> : <Play size={15} />}执行测试</Button></div></section>
      <div className="status-line"><Badge tone={connection.tone}>{connection.label}</Badge><Badge tone={behavior.tone}>{behavior.label}</Badge>{planRecord?.origin === 'generated' && <span className="plan-source"><Sparkles size={12} />{planRecord.serverConfirmed ? '规划来源已核实' : '模型已生成计划'}</span>}</div>
      {controller.error && <div className="error-banner" role="alert"><span>{controller.error}</span><IconButton label="收起错误信息" onClick={controller.actions.clearError}><X size={15} /></IconButton></div>}
      {busy && <div className="operation-banner" role="status"><LoaderCircle size={14} className="spin" /><span>{operation === 'planning' ? '正在调用现有模型编排步骤，不连接设备。' : operation === 'validating' ? '正在检查计划与协议约束。' : '正在按允许动作执行设备测试。'}</span><button onClick={controller.actions.cancel} title="只停止本页等待，服务端可能继续；不会自动重试。">停止等待</button></div>}
      <Tabs.Root className="workspace-tabs" value={tab} onValueChange={setTab}><div className="workspace-tabbar"><Tabs.List className="workspace-tab-list" aria-label="设备测试工作区"><Tabs.Trigger value="flow"><Workflow size={15} />流程</Tabs.Trigger><Tabs.Trigger value="plan"><ListChecks size={15} />测试计划{draft.plan?.steps.length ? <span>{draft.plan.steps.length}</span> : null}</Tabs.Trigger><Tabs.Trigger value="results"><FileCheck2 size={15} />执行结果{report && <span>{stale ? '历史' : `${report.validation?.passed ?? 0}/${report.validation?.total ?? report.steps.length}`}</span>}</Tabs.Trigger></Tabs.List><button className="advanced-trigger" aria-label="高级配置" onClick={() => openAdvanced('draft')}><Braces size={14} /><span>高级</span></button></div>
        <Tabs.Content value="flow" className="workspace-tabpanel"><div className={`flow-workspace ${selected ? 'has-inspector' : ''}`}><WorkflowCanvas controller={controller} selected={selected} onSelect={setSelected} onConfigure={() => setSettingsOpen(true)} onImport={() => openAdvanced('profile')} />{selected && <section className="selection-inspector" aria-labelledby="selection-title"><header><div><span className="inspector-indicator" /><h2 id="selection-title">{EVIDENCE_LABELS[selected]}</h2><span>{selected === 'result' && stale ? '历史回执 · 当前配置未重测' : '当前实际数据'}</span></div><IconButton label="关闭节点详情" onClick={() => setSelected(null)}><X size={15} /></IconButton></header><JsonView value={selectionData()} /></section>}</div></Tabs.Content>
        <Tabs.Content value="plan" className="workspace-tabpanel scroll-panel"><PlanView controller={controller} onAdvanced={() => openAdvanced('plan')} /></Tabs.Content>
        <Tabs.Content value="results" className="workspace-tabpanel scroll-panel"><ResultsView controller={controller} onRun={() => void run()} onReceipt={() => openAdvanced('report')} /></Tabs.Content>
      </Tabs.Root><footer className="workspace-footer"><span><ShieldCheck size={12} />协议动作与断言受约束</span><span>{serviceReady ? '本地执行' : '等待本地服务'}</span></footer>
    </main>
    {wide && settingsOpen && <aside className="device-settings"><header><div><Settings2 size={15} /><h2>设备设置</h2></div><IconButton label="收起设备设置" onClick={() => setSettingsOpen(false)}><X size={16} /></IconButton></header><DeviceSettings controller={controller} onAdvanced={openAdvanced} /></aside>}
    <Sheet open={!wide && settingsOpen} onOpenChange={setSettingsOpen} title="设备设置" description="填写连接方式、功能要求与允许动作。" className="settings-sheet"><DeviceSettings controller={controller} onAdvanced={openAdvanced} /></Sheet>
    <Sheet open={advanced !== null} onOpenChange={open => { if (!open) setAdvanced(null); }} title={advanced ? ADVANCED_LABELS[advanced] : '高级配置'} description={advanced === 'report' || advanced === 'history' ? '来源于真实操作的记录；这里不执行新动作。' : '高级编辑不会连接设备或调用模型，应用后仍需校验与执行。'} className="advanced-sheet"><div className="advanced-toolbar"><span><FileJson size={15} />{advanced === 'profile' || advanced === 'draft' ? '设备、需求、协议与计划' : advanced === 'actions' ? '用户声明的动作与预期' : advanced === 'plan' ? '受约束的测试步骤' : '只读记录'}</span><div>{(advanced === 'profile' || advanced === 'draft') && <><input ref={fileInput} type="file" accept=".json,application/json" className="sr-only" disabled={busy} onChange={event => { void selectFile(event.target.files?.[0]); event.target.value = ''; }} /><Button disabled={busy} onClick={() => fileInput.current?.click()}><Upload size={14} />选择文件</Button></>}<IconButton label={copied ? '已复制 JSON' : '复制 JSON'} onClick={() => { void navigator.clipboard.writeText(advancedText).then(() => setCopied(true)).catch(() => setAdvancedError('复制未完成，可直接在编辑器中选择文本。')); }}>{copied ? <Check size={15} /> : <Copy size={15} />}</IconButton></div></div><JsonEditor label={advanced ? ADVANCED_LABELS[advanced] : 'JSON 编辑器'} testId={advanced === 'profile' || advanced === 'draft' ? 'profile-json' : advanced === 'actions' ? 'actions-json' : advanced === 'plan' ? 'plan-json' : 'receipt-json'} value={advancedText} onChange={setAdvancedText} readOnly={busy || advanced === 'report' || advanced === 'history'} />{(advancedError || (advanced && controller.error)) && <div className="advanced-error" role="alert">{advancedError || controller.error}</div>}<div className="dialog-footer"><span>{advanced === 'profile' || advanced === 'draft' ? '导入规划 ID 的来源由服务端重新核实。' : advanced === 'plan' ? '修改计划后，来源将重新判定。' : 'JSON 参数受服务端约束校验。'}</span><div><Button onClick={() => setAdvanced(null)}>关闭</Button>{advanced !== 'report' && advanced !== 'history' && <Button variant="primary" disabled={busy} onClick={applyAdvanced}>应用配置<ChevronRight size={14} /></Button>}</div></div></Sheet>
    <Sheet open={historyOpen} onOpenChange={setHistoryOpen} title="本页运行记录" description="记录来自当前工作台的实际操作。" className="history-sheet"><div className="history-list">{controller.history.length ? controller.history.map(item => <button key={item.id} onClick={() => { setHistoryOpen(false); openAdvanced('history', item); }}><span className={`history-state history-${item.status}`} /><span><strong>{item.operation === 'planning' ? '模型规划' : item.operation === 'validating' ? '约束校验' : '执行测试'}</strong><small>{new Date(item.at).toLocaleString('zh-CN')}</small></span><Badge tone={item.status === 'completed' ? 'neutral' : 'warning'}>{item.status === 'completed' ? '有回执' : item.status === 'cancelled' ? '停止等待' : item.status === 'stale' ? '历史配置' : '未完成'}</Badge><ChevronRight size={14} /></button>) : <div className="workspace-empty"><Clock3 size={28} /><h2>还没有操作记录</h2><p>生成计划、校验或执行后，会保留可查看的真实记录。</p></div>}</div></Sheet>
  </div></Tooltip.Provider>;
}
