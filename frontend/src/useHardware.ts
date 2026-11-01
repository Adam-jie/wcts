import { useCallback, useEffect, useRef, useState } from 'react';
import type { Dispatch, SetStateAction } from 'react';
import type { HardwareCapabilities, HardwareDraft, HardwareOperation, HardwareReport, HistoryEntry,
  PlanRecord, PlanningResponse, TransportKind, ValidationResponse } from './types';
import { draftFingerprint, errorMessage, exportProfile, generatedDraft, isHardwareReport, makeExample,
  OperationGate, operationFingerprint, parseProfile, requestHardware, transitionDraft, validateDraft } from './lib/hardware';
import type { OperationTicket } from './lib/hardware';

/** No credentials or receipts are persisted in browser storage. Only the
 * capabilities GET runs on mount. Planning and hardware writes are explicit. */
export function useHardware(initialDraft?: HardwareDraft) {
  const [draft, updateDraft] = useState<HardwareDraft>(() => structuredClone(initialDraft ?? makeExample()));
  const draftRef = useRef(draft);
  const [capabilities, setCapabilities] = useState<HardwareCapabilities | null>(null);
  const capabilitiesRef = useRef<HardwareCapabilities | null>(null);
  const [loadingCapabilities, setLoadingCapabilities] = useState(true);
  const [operation, setOperation] = useState<HardwareOperation | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [planRecord, setPlanRecord] = useState<PlanRecord | null>(() => initialDraft?.planning_id
    ? { origin: 'imported', planning_id: initialDraft.planning_id, serverConfirmed: false } : null);
  const [report, setReport] = useState<HardwareReport | null>(null);
  const [reportSnapshot, setReportSnapshot] = useState<string | null>(null);
  const [reportFresh, setReportFresh] = useState(false);
  const [history, setHistory] = useState<HistoryEntry[]>([]);
  const [validation, setValidation] = useState<ValidationResponse | null>(null);
  const gate = useRef(new OperationGate());
  const mounted = useRef(true);
  const capabilityRequest = useRef<AbortController | null>(null);
  const historySequence = useRef(0);
  const draftRevision = useRef(0);

  const addHistory = useCallback((entry: Omit<HistoryEntry, 'id' | 'at'>) => {
    if (!mounted.current) return;
    const value = { ...entry, id: `${Date.now()}-${++historySequence.current}`, at: new Date().toISOString() };
    // Retain failed/cancelled and superseded attempts, including older receipts.
    setHistory((previous) => [value, ...previous]);
  }, []);

  const commit = useCallback((next: HardwareDraft, preservePlanningId = false) => {
    const transition = transitionDraft(draftRef.current, next, preservePlanningId);
    if (transition.changed) draftRevision.current++;
    draftRef.current = transition.draft;
    updateDraft(transition.draft);
    return transition;
  }, []);

  const setDraft: Dispatch<SetStateAction<HardwareDraft>> = useCallback((proposed) => {
    try {
      const previous = draftRef.current;
      const candidate = typeof proposed === 'function' ? proposed(structuredClone(previous)) : proposed;
      const transition = commit(candidate);
      if (transition.changed) {
        setReportFresh(false);
        setValidation(null);
        setError(null);
        setPlanRecord((record) => record ? { ...record, origin: 'edited', planning_id: undefined,
          serverConfirmed: false, summary: transition.protocolChanged
            ? '设备、需求或协议已修改；原规划来源已过期，请重新生成或校验计划。'
            : '计划已编辑；执行时按手工或编辑后的计划核实。' } : null);
      }
    } catch (reason) { setError(errorMessage(reason)); }
  }, [commit]);

  const refreshCapabilities = useCallback(async () => {
    capabilityRequest.current?.abort();
    const controller = new AbortController(); capabilityRequest.current = controller;
    if (mounted.current) setLoadingCapabilities(true);
    try {
      const result = await requestHardware<HardwareCapabilities>('/api/hardware/capabilities', undefined,
        { signal: controller.signal, timeoutMs: 15000 });
      if (!result?.transports || !Array.isArray(result.serial_ports)
        || !['http', 'ssh', 'serial'].every((kind) => typeof result.transports[kind as TransportKind]?.available === 'boolean')) {
        throw new Error('服务能力响应不完整，尚未确认设备适配器状态。');
      }
      if (mounted.current && capabilityRequest.current === controller) {
        capabilitiesRef.current = result; setCapabilities(result);
      }
      return result;
    } catch (reason) {
      if (mounted.current && capabilityRequest.current === controller && !controller.signal.aborted) {
        capabilitiesRef.current = null; setCapabilities(null); setError(errorMessage(reason));
      }
      return undefined;
    } finally {
      if (mounted.current && capabilityRequest.current === controller) setLoadingCapabilities(false);
    }
  }, []);

  useEffect(() => {
    mounted.current = true;
    void refreshCapabilities();
    return () => { mounted.current = false; capabilityRequest.current?.abort(); gate.current.cancel(); };
  }, [refreshCapabilities]);

  const begin = useCallback((kind: HardwareOperation) => {
    if (gate.current.busy) return undefined;
    try {
      if (!capabilitiesRef.current) throw new Error('请先连接本地服务或刷新设备适配器状态。');
      const snapshot = structuredClone(draftRef.current);
      const errors = validateDraft(snapshot, kind !== 'planning');
      if (errors.length) throw new Error(errors.join('；'));
      if (kind === 'running' && capabilitiesRef.current.transports[snapshot.device.kind]?.available === false) {
        throw new Error('当前接口缺少本地执行依赖；本次没有向设备发送动作。');
      }
      const ticket = gate.current.begin(kind, operationFingerprint(snapshot, draftRevision.current));
      setOperation(kind); setError(null);
      if (kind === 'running') setReportFresh(false);
      return { ticket, snapshot };
    } catch (reason) { setError(errorMessage(reason)); return undefined; }
  }, []);

  const finish = useCallback((ticket: OperationTicket) => {
    const owns = gate.current.accepts(ticket, ticket.snapshot);
    gate.current.release(ticket);
    if (mounted.current && owns) setOperation(null);
  }, []);
  const fail = useCallback((ticket: OperationTicket, reason: unknown, extra: Pick<HistoryEntry, 'planning' | 'validation' | 'report'> = {}) => {
    const message = errorMessage(reason);
    if (mounted.current) setError(message);
    addHistory({ operation: ticket.operation, status: ticket.controller.signal.aborted ? 'cancelled' : 'failed', message, ...extra });
  }, [addHistory]);

  const generatePlan = useCallback(async () => {
    const started = begin('planning'); if (!started) return undefined;
    const { ticket, snapshot } = started;
    let response: PlanningResponse | undefined;
    try {
      const result = await requestHardware<PlanningResponse>('/api/hardware/plan', {
        device: snapshot.device, requirements: snapshot.requirements, allowed_actions: snapshot.allowed_actions,
        provider: 'codex_cli', model: 'gpt-5.6-luna', timeout_s: 120, max_duration_s: snapshot.max_duration_s,
      }, { signal: ticket.controller.signal, timeoutMs: 180000 });
      response = result;
      const current = gate.current.accepts(ticket, operationFingerprint(draftRef.current, draftRevision.current));
      if (!current) {
        addHistory({ operation: 'planning', status: 'stale', planning: result,
          message: '生成期间配置已变化；返回计划仅保留为历史，没有覆盖当前配置。' });
        return result;
      }
      if (result.status !== 'ready' || result.model_used !== true || !result.plan) {
        const summary = `${result.summary || '服务未返回完整模型计划。'} 本次未形成可执行的新计划，已有计划保留；请补充协议后重新生成或手工核对。`;
        if (mounted.current) {
          const retained = { ...snapshot }; delete retained.planning_id;
          commit(retained); setReportFresh(false); setValidation(null); setError(summary);
          setPlanRecord({ origin: 'edited', serverConfirmed: false, summary, uncovered: result.uncovered,
            model_requested: result.model_requested, model_actual: result.model_actual });
        }
        addHistory({ operation: 'planning', status: 'failed', planning: result, message: summary });
        return result;
      }
      const next = generatedDraft(snapshot, result);
      if (mounted.current) {
        commit(next, true); setValidation(null);
        setPlanRecord({ origin: 'generated', planning_id: result.planning_id, model_requested: result.model_requested,
          model_actual: result.model_actual, summary: result.summary, serverConfirmed: false });
      }
      addHistory({ operation: 'planning', status: 'completed', planning: result });
      return result;
    } catch (reason) { fail(ticket, reason, response ? { planning: response } : {}); return undefined; }
    finally { finish(ticket); }
  }, [addHistory, begin, commit, fail, finish]);

  const validatePlan = useCallback(async () => {
    const started = begin('validating'); if (!started) return undefined;
    const { ticket, snapshot } = started;
    try {
      const result = await requestHardware<ValidationResponse>('/api/hardware/validate-plan', snapshot,
        { signal: ticket.controller.signal, timeoutMs: 30000 });
      if (typeof result?.valid !== 'boolean') throw new Error('服务未返回有效的计划校验结果。');
      const current = gate.current.accepts(ticket, operationFingerprint(draftRef.current, draftRevision.current));
      if (mounted.current && current) setValidation(result);
      addHistory({ operation: 'validating', status: !current ? 'stale' : result.valid ? 'completed' : 'failed', validation: result });
      return result;
    } catch (reason) { fail(ticket, reason); return undefined; }
    finally { finish(ticket); }
  }, [addHistory, begin, fail, finish]);

  const run = useCallback(async () => {
    const started = begin('running'); if (!started) return undefined;
    const { ticket, snapshot } = started;
    try {
      const result = await requestHardware<unknown>('/api/hardware/run', snapshot,
        { signal: ticket.controller.signal, timeoutMs: snapshot.max_duration_s * 1000 + 40000 });
      if (!isHardwareReport(result)) throw new Error('本次没有取得完整设备回执，不能判定测试通过。');
      const current = gate.current.accepts(ticket, operationFingerprint(draftRef.current, draftRevision.current));
      if (mounted.current) {
        // A late receipt is useful evidence, but never a result for new inputs.
        setReport(result); setReportSnapshot(draftFingerprint(snapshot)); setReportFresh(current);
        if (current) setPlanRecord((record) => ({ origin: result.model_used === true ? 'generated' : record?.origin === 'imported' ? 'imported' : 'edited',
          planning_id: result.planning_id ?? snapshot.planning_id, model_requested: result.model_requested,
          model_actual: result.model_actual, summary: result.model_used ? '服务端已核对本次设备、需求、动作及完整计划的来源绑定。' : '本次执行来源由服务端标记为手工或已编辑计划。',
          serverConfirmed: result.model_used === true }));
      }
      addHistory({ operation: 'running', status: !current ? 'stale' : result.success ? 'completed' : 'failed', report: result });
      return result;
    } catch (reason) { fail(ticket, reason); return undefined; }
    finally { finish(ticket); }
  }, [addHistory, begin, fail, finish]);

  const importProfile = useCallback((text: string): boolean => {
    if (gate.current.busy) { setError('操作进行中，请等待后再导入档案。'); return false; }
    try {
      const next = parseProfile(text);
      commit(next, true); setValidation(null); setReportFresh(false); setError(null);
      setPlanRecord({ origin: 'imported', planning_id: next.planning_id, serverConfirmed: false,
        summary: next.planning_id ? '导入的规划来源待服务端核实；仅有 planning_id 不代表模型已参与。' : '已导入手工档案，没有连接设备或调用模型。' });
      return true;
    } catch (reason) { setError(errorMessage(reason)); return false; }
  }, [commit]);
  const loadExample = useCallback((kind: TransportKind) => {
    if (gate.current.busy) return;
    commit(makeExample(kind), true); setPlanRecord(null); setValidation(null); setReportFresh(false); setError(null);
  }, [commit]);
  const exportCurrentProfile = useCallback(() => exportProfile(draftRef.current), []);
  const clearError = useCallback(() => setError(null), []);
  const cancel = useCallback(() => gate.current.cancel(), []);

  return { draft, setDraft, capabilities, loadingCapabilities, serviceReady: capabilities !== null,
    busy: operation !== null, operation, error, planRecord, report, history, validation,
    stale: report !== null && (!reportFresh || reportSnapshot !== draftFingerprint(draft)),
    actions: { refreshCapabilities, importProfile, exportProfile: exportCurrentProfile, loadExample,
      generatePlan, validatePlan, run, clearError, cancel } };
}

export type HardwareController = ReturnType<typeof useHardware>;
