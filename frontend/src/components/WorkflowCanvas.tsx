import { useEffect, useMemo, useRef, useState } from 'react';
import { ReactFlow, Background, BackgroundVariant, Controls, Handle, MarkerType, Position, useNodesState, type Node, type NodeProps, type Edge, type ReactFlowInstance } from '@xyflow/react';
import { Cpu, Sparkles, TerminalSquare, ListChecks, FileCheck2, ChevronRight, SlidersHorizontal, ArrowUpRight } from 'lucide-react';
import type { HardwareController } from '../useHardware';
import { targetOf, kindLabel } from './helpers';
import { Button } from './ui';

export type EvidenceKind = 'device' | 'planner' | 'actions' | 'assertions' | 'result';
interface StageData extends Record<string, unknown> { title: string; subtitle: string; detail: string; kind: EvidenceKind; state: string; selected?: boolean; }
type Stage = Node<StageData, 'stage'>;
const ICONS = { device: Cpu, planner: Sparkles, actions: TerminalSquare, assertions: ListChecks, result: FileCheck2 };

function StageNode({ data, selected }: NodeProps<Stage>) {
  const Icon = ICONS[data.kind];
  return <div className={`stage-node stage-node-${data.kind} ${selected ? 'is-selected' : ''}`}>
    <Handle type="target" position={Position.Left} isConnectable={false} />
    <div className="stage-node-heading"><span className="stage-node-icon"><Icon size={19} /></span><span className="stage-node-type">{data.subtitle}</span><span className={`node-status node-status-${data.state}`} /></div>
    <strong>{data.title}</strong><p>{data.detail}</p>
    <Handle type="source" position={Position.Right} isConnectable={false} />
  </div>;
}
const nodeTypes = { stage: StageNode };

export function WorkflowCanvas({ controller, selected, onSelect, onConfigure, onImport }: { controller: HardwareController; selected: EvidenceKind | null; onSelect(value: EvidenceKind | null): void; onConfigure(): void; onImport(): void }) {
  const container = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(800);
  const [flow, setFlow] = useState<ReactFlowInstance<Stage, Edge> | null>(null);
  const { draft, report, planRecord, stale, operation } = controller;
  useEffect(() => {
    if (!container.current) return;
    const observer = new ResizeObserver(entries => setWidth(entries[0]?.contentRect.width || 800));
    observer.observe(container.current);
    return () => observer.disconnect();
  }, []);
  const configured = Boolean(targetOf(draft.device));
  const usableReport = !stale ? report : null;
  const models = useMemo((): Stage[] => {
    const positions = width < 730
      ? [{ x: 0, y: 0 }, { x: 230, y: 0 }, { x: 230, y: 145 }, { x: 0, y: 145 }, { x: 0, y: 290 }]
      : width < 1100
        ? [{ x: 0, y: 0 }, { x: 230, y: 0 }, { x: 460, y: 0 }, { x: 460, y: 145 }, { x: 230, y: 145 }]
        : [{ x: 0, y: 80 }, { x: 260, y: 80 }, { x: 520, y: 80 }, { x: 780, y: 80 }, { x: 1040, y: 80 }];
    const assertions = draft.plan?.steps.reduce((sum, step) => sum + step.assertions.length, 0) || 0;
    const resultPassed = usableReport?.validation?.passed ?? usableReport?.steps.filter(item => item.status === 'passed').length;
    const data: StageData[] = [
      { kind: 'device', title: draft.device.name || '目标设备', subtitle: kindLabel(draft.device.kind), detail: targetOf(draft.device) || '等待配置连接地址', state: configured ? 'defined' : 'pending' },
      { kind: 'planner', title: 'CodeAgent', subtitle: '总控 · 步骤编排', detail: operation === 'planning' ? '正在处理本次规划请求' : planRecord?.origin === 'generated' ? '模型已生成受约束步骤' : planRecord?.origin === 'imported' ? '导入规划，来源待核实' : draft.plan?.steps.length ? '当前为手工测试计划' : '尚未调用模型', state: planRecord?.origin === 'generated' ? 'defined' : 'pending' },
      { kind: 'actions', title: '协议动作', subtitle: '用户允许的范围', detail: `${draft.allowed_actions.length} 个动作 · ${draft.plan?.steps.length || 0} 个计划步骤`, state: draft.allowed_actions.length ? 'defined' : 'pending' },
      { kind: 'assertions', title: '行为断言', subtitle: '预期结果检查', detail: assertions ? `${assertions} 条断言，执行后逐项比对` : '等待声明预期回包', state: assertions ? 'defined' : 'pending' },
      { kind: 'result', title: '执行证据', subtitle: '真实回包与记录', detail: usableReport ? `${resultPassed}/${usableReport.validation?.total ?? usableReport.steps.length} 步通过${!usableReport.success ? ' · 有失败' : usableReport.is_simulation ? ' · 软件模拟' : ''}` : stale ? '配置已改，等待重新执行' : '尚无执行记录', state: usableReport ? !usableReport.success ? 'failed' : usableReport.is_simulation ? 'simulation' : 'passed' : 'pending' },
    ];
    return data.map((item, index) => ({ id: item.kind, type: 'stage', position: positions[index], data: item, selected: selected === item.kind, draggable: true, deletable: false }));
  }, [width, draft, usableReport, planRecord, stale, operation, configured, selected]);
  const [nodes, setNodes, onNodesChange] = useNodesState<Stage>(models);
  useEffect(() => { setNodes(previous => models.map(item => ({ ...item, position: previous.find(existing => existing.id === item.id)?.position || item.position }))); }, [models, setNodes]);
  const layout = width < 730 ? 'narrow' : width < 1100 ? 'medium' : 'wide';
  useEffect(() => {
    setNodes(models);
    // ReactFlow measures the newly arranged nodes after rendering. Fit only on
    // initialization or a responsive layout change, never on evidence updates.
    let secondFrame = 0;
    const firstFrame = requestAnimationFrame(() => {
      secondFrame = requestAnimationFrame(() => {
        void flow?.fitView({ padding: 0.08, minZoom: 0.55, maxZoom: 1, duration: 0 });
      });
    });
    return () => { cancelAnimationFrame(firstFrame); cancelAnimationFrame(secondFrame); };
  }, [layout, flow, setNodes]);
  const edges = useMemo((): Edge[] => ['device', 'planner', 'actions', 'assertions'].map((source, index) => ({ id: `${source}-next`, source, target: ['planner', 'actions', 'assertions', 'result'][index], type: 'smoothstep', animated: false, deletable: false, markerEnd: { type: MarkerType.ArrowClosed, color: '#7098b0', width: 15, height: 15 }, style: { stroke: '#7098b0', strokeWidth: 1.5 } })), []);
  return <div className={`workflow-canvas ${!configured ? 'is-empty' : ''}`} ref={container}>
    <div className="canvas-caption"><span className="canvas-dot" />验证流程 <ChevronRight size={13} /><span>{configured ? '点选节点查看证据' : '等待添加设备'}</span></div>
    <ReactFlow colorMode="dark" nodes={nodes} edges={edges} onInit={setFlow} onNodesChange={onNodesChange} nodeTypes={nodeTypes} onNodeClick={(_, item) => onSelect(item.id as EvidenceKind)} onPaneClick={() => onSelect(null)} nodesConnectable={false} edgesReconnectable={false} deleteKeyCode={null} selectionOnDrag={false} panOnScroll zoomOnScroll={false} zoomOnPinch preventScrolling={false} minZoom={0.55} maxZoom={1.4} fitView fitViewOptions={{ padding: 0.08, minZoom: 0.55, maxZoom: 1 }} ariaLabelConfig={{ 'controls.zoomIn.ariaLabel': '放大流程', 'controls.zoomOut.ariaLabel': '缩小流程', 'controls.fitView.ariaLabel': '显示完整流程', 'controls.interactive.ariaLabel': '切换画布交互' }}>
      <Background variant={BackgroundVariant.Dots} gap={22} size={1} color="#30475e" /><Controls showInteractive={false} position="bottom-right" />
    </ReactFlow>
    {!configured && <div className="canvas-empty"><div className="empty-icon"><Cpu size={23} /><span><Sparkles size={11} /></span></div><h2>让设备的行为，成为可核对的证据。</h2><p>配置设备与协议，CodeAgent 会在允许范围内组织测试。</p><div><Button variant="primary" onClick={onConfigure}><SlidersHorizontal size={15} />配置设备</Button><Button onClick={onImport}>导入档案<ArrowUpRight size={14} /></Button></div></div>}
  </div>;
}
