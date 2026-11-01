import { StrictMode, useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { Activity, ArrowDown, ArrowUpRight, Blocks, Check, ChevronRight, Circle, CircleAlert, Code2, Cpu, ExternalLink, Eye, EyeOff, FileCheck2, FileCode2, GitBranch, LayoutDashboard, LoaderCircle, Monitor, RefreshCw, ShieldCheck, Terminal, Workflow, X } from 'lucide-react';
import { embeddedPreviewTarget, modelEvidence, parseWebLive, previewTarget, PROJECT_LINKS, STATUS_LABEL, testCounts, type Evidence, type Project, type Status, type WebLive } from './lib/web-live';
import '../../web/theme.css';
import './web-testing.css';

const LOG_NAMES = { events: '任务事件', install: '依赖安装', build: '构建', tests: '测试', baseline: '原版服务', candidate: '候选服务' } as const;
type LogName = keyof typeof LOG_NAMES;
type View = 'preview' | 'tests' | 'logs';

function useEvidence(revision: number) {
  const [data, setData] = useState<WebLive | null>(null);
  const [error, setError] = useState('');
  const [receivedAt, setReceivedAt] = useState('');
  useEffect(() => {
    let active = true;
    let next: ReturnType<typeof setTimeout>;
    let controller: AbortController;
    async function poll() {
      controller = new AbortController();
      const deadline = setTimeout(() => controller.abort(), 8000);
      try {
        const response = await fetch('/api/web-live', { cache: 'no-store', signal: controller.signal });
        if (!response.ok) throw new Error(`状态服务返回 HTTP ${response.status}`);
        const value = parseWebLive(await response.json());
        if (active) { setData(value); setError(''); setReceivedAt(new Date().toLocaleTimeString('zh-CN')); }
      } catch (cause) {
        if (active) setError(cause instanceof Error && cause.name !== 'AbortError' ? cause.message : '状态读取超时');
      } finally {
        clearTimeout(deadline);
        if (active) next = setTimeout(() => void poll(), 2000);
      }
    }
    void poll();
    return () => { active = false; clearTimeout(next); controller?.abort(); };
  }, [revision]);
  return { data, error, receivedAt };
}

function useLog(projectId: string, name: LogName) {
  const [log, setLog] = useState({ text: '', truncated: false, error: '' });
  useEffect(() => {
    let active = true;
    let next: ReturnType<typeof setTimeout>;
    let controller: AbortController;
    setLog({ text: '', truncated: false, error: '' });
    async function poll() {
      controller = new AbortController();
      const deadline = setTimeout(() => controller.abort(), 6000);
      try {
        const response = await fetch(`/api/web-live/${encodeURIComponent(projectId)}/logs?name=${name}`, { cache: 'no-store', signal: controller.signal });
        if (!response.ok) throw new Error(`日志服务返回 HTTP ${response.status}`);
        const result = await response.json();
        if (typeof result?.text !== 'string' || typeof result?.truncated !== 'boolean' || result?.name !== name) throw new Error('日志格式不正确');
        if (active) setLog({ text: result.text, truncated: result.truncated, error: '' });
      } catch (cause) {
        if (active) setLog(previous => ({ ...previous, error: cause instanceof Error && cause.name !== 'AbortError' ? cause.message : '日志读取超时' }));
      } finally {
        clearTimeout(deadline);
        if (active) next = setTimeout(() => void poll(), 2000);
      }
    }
    void poll();
    return () => { active = false; clearTimeout(next); controller?.abort(); };
  }, [projectId, name]);
  return log;
}

function StatusIcon({ status, size = 14 }: { status: Status; size?: number }) {
  if (status === 'running') return <LoaderCircle size={size} className="wl-spin" />;
  if (status === 'passed' || status === 'completed') return <Check size={size} />;
  if (status === 'failed') return <X size={size} />;
  if (status === 'blocked') return <CircleAlert size={size} />;
  return <Circle size={size} />;
}
function Badge({ status }: { status: Status }) { return <span className={`wl-badge wl-status-${status}`}><StatusIcon status={status} size={12} />{STATUS_LABEL[status]}</span>; }

function LivePreview({ project }: { project: Project }) {
  const [enabled, setEnabled] = useState(true);
  const [revision, setRevision] = useState(0);
  const [mobileVersion, setMobileVersion] = useState<'reference' | 'candidate'>('candidate');
  return <section className="wl-live" aria-label="真实应用对比预览">
    <div className="wl-section-head"><div><h2><Monitor size={17} />真实应用对比</h2><p>在两个独立端口直接操作，核对补全前后的行为。</p></div><div className="wl-row"><button className="wl-icon-button" title="重新加载应用预览" aria-label="重新加载应用预览" disabled={!enabled} onClick={() => setRevision(value => value + 1)}><RefreshCw size={16} /></button><button className="wl-button" aria-pressed={enabled} onClick={() => setEnabled(!enabled)}>{enabled ? <EyeOff size={15} /> : <Eye size={15} />}{enabled ? '收起预览' : '打开预览'}</button></div></div>
    {enabled && <><div className="wl-mobile-versions" role="group" aria-label="选择预览版本"><button className={mobileVersion === 'reference' ? 'is-active' : ''} onClick={() => setMobileVersion('reference')}>原版基线</button><button className={mobileVersion === 'candidate' ? 'is-active' : ''} onClick={() => setMobileVersion('candidate')}>补全候选</button></div><div className="wl-preview-grid">
      {(['reference', 'candidate'] as const).map(kind => {
        const target = previewTarget(project, kind);
        const frameTarget = embeddedPreviewTarget(project, kind);
        return <article className={`wl-browser wl-browser-${kind} ${mobileVersion === kind ? 'is-mobile-active' : ''}`} key={kind}>
          <header><span className="wl-browser-dots"><i /><i /><i /></span><strong>{kind === 'reference' ? '原版基线' : '补全候选'}</strong><span className={`wl-ready ${target ? 'is-ready' : ''}`}><i />{target ? '页面可访问' : '等待启动'}</span>{target && <a href={target} target="_blank" rel="noopener noreferrer" title={`独立打开${kind === 'reference' ? '原版' : '候选'}应用`}><ExternalLink size={14} /><span className="wl-sr-only">独立打开应用</span></a>}</header>
          <div className="wl-browser-address"><ShieldCheck size={12} /><span>{PROJECT_LINKS[project.id][kind]}</span></div>
          {frameTarget ? <iframe key={`${project.id}-${kind}-${revision}`} title={`${project.title} ${kind === 'reference' ? '原版基线' : '补全候选'}实时应用`} src={frameTarget} sandbox="allow-scripts allow-same-origin allow-forms allow-modals allow-downloads" loading="eager" /> : target ? <div className="wl-preview-empty wl-open-separately"><div><ExternalLink size={25} /></div><h3>应用已启动</h3><p>安全策略要求独立打开。<br />在应用标签页查看和操作真实效果。</p><a className="wl-button wl-primary" href={target} target="_blank" rel="noopener noreferrer">打开{kind === 'reference' ? '原版应用' : '补全版应用'}<ExternalLink size={14} /></a></div> : <div className="wl-preview-empty"><div><Monitor size={25} /></div><h3>应用尚未就绪</h3><p>依赖、构建与启动状态会实时更新。<br />就绪后这里将显示实际运行的应用。</p></div>}
          <footer>{target && !frameTarget ? '保留项目原有安全策略，在独立标签页中运行。' : project.id === 'paperless-ngx' ? '若应用限制嵌入，请使用右上角独立打开。' : '可直接操作应用；刷新预览不会重启服务。'}</footer>
        </article>;
      })}
    </div></>}
    <p className="wl-preview-scope"><CircleAlert size={13} />页面可访问只代表服务已响应，功能是否满足需求以独立测试证据为准。</p>
  </section>;
}

function TestEvidence({ tests }: { tests: Evidence[] }) {
  const counts = testCounts(tests);
  return <section className="wl-test-evidence"><div className="wl-section-head"><div><h2><FileCheck2 size={17} />独立验证</h2><p>逐项保留执行结果，失败、阻塞和未执行不会计入通过。</p></div><span className="wl-test-tally">{counts.passed} / {counts.total} 项通过</span></div>
    {tests.length ? <div className="wl-test-list">{tests.map((item, index) => <details key={item.id}><summary><span className={`wl-test-icon wl-status-${item.status}`}><StatusIcon status={item.status} size={16} /></span><span><strong>{item.label}</strong><small>{item.id}</small></span><Badge status={item.status} /><ChevronRight className="wl-details-chevron" size={15} /></summary><div><span>执行证据</span><p>{item.detail || '尚未提供执行详情。'}</p><small>检查项 {index + 1}</small></div></details>)}</div> : <div className="wl-evidence-empty"><FileCheck2 size={25} /><h3>等待测试证据</h3><p>收到真实执行回执后显示结果。</p></div>}
  </section>;
}

function Logs({ projectId }: { projectId: string }) {
  const [name, setName] = useState<LogName>('events');
  const [follow, setFollow] = useState(true);
  const log = useLog(projectId, name);
  const pre = useRef<HTMLPreElement>(null);
  useEffect(() => { if (follow && pre.current) pre.current.scrollTop = pre.current.scrollHeight; }, [log.text, follow]);
  return <section className="wl-logs"><div className="wl-section-head"><div><h2><Terminal size={17} />实时执行日志</h2><p>每两秒读取实际日志。{log.truncated ? '当前显示末尾 16 KB。' : ''}</p></div><button className={`wl-button ${follow ? 'is-selected' : ''}`} aria-pressed={follow} onClick={() => setFollow(!follow)}><ArrowDown size={14} />跟随最新</button></div><div className="wl-log-tabs" role="group" aria-label="日志类型">{Object.entries(LOG_NAMES).map(([key, label]) => <button className={key === name ? 'is-active' : ''} key={key} onClick={() => setName(key as LogName)}>{label}</button>)}</div>{log.error && <p className="wl-log-error" role="alert"><CircleAlert size={14} />{log.error}；当前内容可能已过期。</p>}<pre ref={pre} tabIndex={0} aria-label={`${LOG_NAMES[name]}原始日志`}>{log.text || (log.error ? '暂时无法读取日志' : '读取日志中…')}</pre></section>;
}

function Workspace({ project, receivedAt, stale }: { project: Project; receivedAt: string; stale: boolean }) {
  const [view, setView] = useState<View>('preview');
  const counts = testCounts(project.tests);
  const reference = previewTarget(project, 'reference');
  const candidate = previewTarget(project, 'candidate');
  const model = modelEvidence(project.model);
  const currentStage = project.stages.find(stage => stage.status === 'running') || project.stages.find(stage => stage.status === 'blocked' || stage.status === 'failed');
  return <>
    <div className="wl-project-heading"><div><div className="wl-eyebrow"><GitBranch size={13} />真实开源工程实验</div><h1>{project.title}<Badge status={project.status} /></h1><div className="wl-source"><a href={project.source_url} target="_blank" rel="noopener noreferrer"><Code2 size={13} />源码<ArrowUpRight size={11} /></a><span />{project.source_commit ? <code title={project.source_commit}>{project.source_commit.slice(0, 12)}</code> : <span>版本待锁定</span>}</div></div><div className="wl-open-actions">{reference ? <a className="wl-button" href={reference} target="_blank" rel="noopener noreferrer">原版应用<ExternalLink size={14} /></a> : <button className="wl-button" disabled>原版待启动</button>}{candidate ? <a className="wl-button wl-primary" href={candidate} target="_blank" rel="noopener noreferrer">打开补全版<ExternalLink size={14} /></a> : <button className="wl-button wl-primary" disabled>补全版待启动</button>}</div></div>
    <div className="wl-overview"><section className="wl-controller"><div className="wl-controller-mark"><div><Cpu size={27} /></div><span /><span /></div><div><div className="wl-eyebrow">协作总控</div><h2>CodeAgent</h2><p title={project.phase}>{currentStage?.label || project.phase || '等待执行任务'}</p></div><div className="wl-model"><span>模型调用</span><strong>{model.calls}</strong>{(project.model.completed_calls > 0 || project.model.actual_model) && <small className="wl-model-identity">{model.identity}</small>}{project.model.requested_model && <small>请求：{project.model.requested_model}</small>}</div></section><section className="wl-goal"><div className="wl-eyebrow"><FileCode2 size={13} />本次补全目标</div><p>{project.feature || '等待总控载入本次需求与验收目标。'}</p><span><ShieldCheck size={13} />原版与候选独立运行 · 验收结果单独记录</span></section></div>
    <section className="wl-pipeline" aria-label="任务执行阶段"><div className="wl-section-head"><h2><Workflow size={16} />执行流程</h2><span className={`wl-sync ${stale ? 'is-stale' : ''}`}><i />{stale ? '连接中断，显示上次数据' : `已同步 ${receivedAt}`}</span></div>{project.stages.length ? <ol>{project.stages.map((stage, index) => <li className={`wl-stage wl-status-${stage.status}`} key={stage.id}><div className="wl-stage-top"><span className="wl-stage-number"><StatusIcon status={stage.status} size={15} /></span><small>{String(index + 1).padStart(2, '0')}</small></div><strong>{stage.label}</strong><span className="wl-stage-status">{STATUS_LABEL[stage.status]}</span><p>{stage.detail || '等待执行回执'}</p></li>)}</ol> : <div className="wl-stage-empty">等待实际执行阶段，不据此推断任务已完成。</div>}</section>
    {project.notes.length > 0 && <details className="wl-notes" open={project.status === 'blocked' || project.status === 'failed'}><summary><CircleAlert size={15} /><span>执行说明</span><small>{project.notes.length}</small><ChevronRight size={14} /></summary><ul>{project.notes.map((note, index) => <li key={index}>{note}</li>)}</ul></details>}
    <div className="wl-content-tabs" role="tablist" aria-label="实验观察"><button role="tab" aria-selected={view === 'preview'} className={view === 'preview' ? 'is-active' : ''} onClick={() => setView('preview')}><Monitor size={16} />实时应用</button><button role="tab" aria-selected={view === 'tests'} className={view === 'tests' ? 'is-active' : ''} onClick={() => setView('tests')}><FileCheck2 size={16} />验证证据<span>{counts.total}</span></button><button role="tab" aria-selected={view === 'logs'} className={view === 'logs' ? 'is-active' : ''} onClick={() => setView('logs')}><Terminal size={16} />执行日志</button><span className="wl-test-summary">{counts.failed > 0 && <b>{counts.failed} 项失败</b>}<span>{counts.passed} 项通过</span><span>{counts.unfinished} 项未完成</span></span></div>
    <div className="wl-content" role="tabpanel">{view === 'preview' ? <LivePreview project={project} /> : view === 'tests' ? <TestEvidence tests={project.tests} /> : <Logs projectId={project.id} />}</div>
  </>;
}

function App() {
  const [revision, setRevision] = useState(0);
  const [selected, setSelected] = useState('uptime-kuma');
  const { data, error, receivedAt } = useEvidence(revision);
  const project = data?.projects.find(item => item.id === selected) || data?.projects[0];
  return <div className="wl-app"><aside className="wl-sidebar"><a className="wl-brand" href="/web-testing"><span><Blocks size={23} /></span><div><strong>TestFix</strong><small>工程验证工作空间</small></div></a><div className="wl-space"><span>T</span><div>本地工作空间<small>产品验证</small></div><ChevronRight size={14} /></div><nav aria-label="工作空间导航"><a href="/web-testing" className="is-active" aria-current="page" title="网页产品验证"><LayoutDashboard size={18} /><span>网页产品验证</span></a><a href="/fea" title="CodeAgent 3D"><Cpu size={18} /><span>CodeAgent 3D</span><ArrowUpRight size={13} /></a><a href="/hardware" title="设备测试"><Workflow size={18} /><span>设备测试</span><ArrowUpRight size={13} /></a></nav><div className="wl-sidebar-label">实验项目</div><div className="wl-project-list">{(['uptime-kuma', 'paperless-ngx'] as const).map(id => { const item = data?.projects.find(value => value.id === id); return <button key={id} title={id === 'uptime-kuma' ? 'Uptime Kuma' : 'Paperless-ngx'} className={project?.id === id ? 'is-active' : ''} onClick={() => setSelected(id)}><span>{id === 'uptime-kuma' ? <Activity size={17} /> : <FileCode2 size={17} />}</span><div>{id === 'uptime-kuma' ? 'Uptime Kuma' : 'Paperless-ngx'}<small>{item ? STATUS_LABEL[item.status] : '读取状态中'}</small></div>{item && <i className={`wl-project-state wl-status-${item.status}`} />}</button>; })}</div><div className="wl-sidebar-foot"><span><i className={error ? 'has-error' : ''} />{error ? '状态连接异常' : data ? '本地状态已连接' : '连接本地服务'}</span><small>固定版本 · 独立验证 · 真实运行</small></div></aside><main className="wl-main"><header className="wl-topbar"><div><LayoutDashboard size={15} /><span>工作空间</span><ChevronRight size={12} /><strong>网页产品验证</strong></div><button className="wl-icon-button" aria-label="立即刷新实验状态" title="立即刷新实验状态" onClick={() => setRevision(value => value + 1)}><RefreshCw size={16} /></button></header><div className="wl-scroll">{error && <div className="wl-error" role="alert"><CircleAlert size={16} /><span>{error}{data ? '。以下保留最近一次状态，不能视为实时结果。' : '。正在自动重连。'}</span></div>}{project ? <Workspace key={project.id} project={project} receivedAt={receivedAt} stale={Boolean(error)} /> : <div className="wl-loading"><LoaderCircle size={30} className="wl-spin" /><h1>读取真实执行状态</h1><p>连接本地任务、构建和测试证据。</p></div>}<footer className="wl-page-foot"><span><ShieldCheck size={13} />证据来自本地执行记录</span><a href="/static/hardware-app/THIRD_PARTY_NOTICES.txt" target="_blank" rel="noopener noreferrer">开源许可<ArrowUpRight size={11} /></a></footer></div></main></div>;
}

createRoot(document.getElementById('root')!).render(<StrictMode><App /></StrictMode>);
