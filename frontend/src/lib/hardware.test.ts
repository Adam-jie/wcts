import { afterEach, describe, expect, it, vi } from 'vitest';
import { canonical, draftFingerprint, exportProfile, generatedDraft, makeExample, OperationGate,
  operationFingerprint, parseActions, parsePlan, parseProfile, parseRequirements, requestHardware,
  transitionDraft, validateDraft } from './hardware';
import type { HardwareDraft, PlanningResponse } from '../types';

function configured(): HardwareDraft {
  const draft = makeExample('http');
  if (draft.device.kind === 'http') draft.device.base_url = 'http://127.0.0.1:9188';
  return draft;
}
function planning(draft: HardwareDraft): PlanningResponse {
  return { status: 'ready', model_used: true, planning_id: 'a'.repeat(32), plan: structuredClone(draft.plan!),
    model_requested: 'gpt-5.6-sol', model_actual: null };
}
afterEach(() => vi.useRealTimers());

describe('protocol editing and explicit source claims', () => {
  it('keeps authoring an empty target separate from executable validation', () => {
    const draft = makeExample();
    const imported = parseProfile(exportProfile(draft));
    expect(imported.device.kind).toBe('http');
    expect(validateDraft(imported)).toContain('请填写设备 URL。');
    expect(validateDraft(configured())).toEqual([]);
  });
  it.each(['{', '{"device":}', '[1,2,]', 'not json'])('reports malformed profile JSON: %s', (value) => {
    expect(() => parseProfile(value)).toThrow('有效 JSON');
  });
  it('does not import claimed model metadata as trusted provenance', () => {
    expect(() => parseProfile(JSON.stringify({ ...configured(), model_used: true }))).toThrow('未支持的字段');
    const imported = parseProfile(JSON.stringify({ ...configured(), planning_id: 'f'.repeat(32) }));
    expect(imported.planning_id).toBe('f'.repeat(32));
    expect(imported).not.toHaveProperty('model_used');
    expect(imported).not.toHaveProperty('serverConfirmed');
  });
  it('rejects invalid planning IDs instead of forging an executable source', () => {
    expect(() => parseProfile(JSON.stringify({ ...configured(), planning_id: '../planning.json' }))).toThrow('planning_id');
  });
  it('parses full requirements and controls step/action shapes', () => {
    expect(parseRequirements(JSON.stringify(configured().requirements))[0].category).toBe('functional');
    expect(parseActions(JSON.stringify(configured().allowed_actions))[0].id).toBe('read-version');
    expect(() => parsePlan('{"steps":[{"id":"s","function_id":"f","action_id":"a","assertions":[],"command":"rm"}]}')).toThrow('未支持的字段');
  });
  it('rejects model edits to frozen assertions, including nested bool/number substitutions', () => {
    const draft = configured();
    draft.allowed_actions[0].assertions = [{ kind: 'json_equals', expected: { values: [true] } }];
    draft.plan!.steps[0].assertions = [{ kind: 'json_equals', expected: { values: [1] } }];
    expect(validateDraft(draft).some((value) => value.includes('协议合同'))).toBe(true);
    draft.plan!.steps[0].assertions = structuredClone(draft.allowed_actions[0].assertions);
    expect(validateDraft(draft)).toEqual([]);
  });
  it('accepts equivalent backend defaults without weakening a contract', () => {
    const draft = configured();
    draft.plan!.steps[0].assertions = draft.allowed_actions[0].assertions.map((item) => ({ ...item, path: item.path ?? [] }));
    expect(validateDraft(draft)).toEqual([]);
  });
  it('rejects unlisted actions, uncovered requirements and cross-origin action paths', () => {
    const draft = configured();
    draft.plan!.steps[0].action_id = 'not-allowed';
    expect(validateDraft(draft).some((value) => value.includes('允许动作'))).toBe(true);
    draft.plan!.steps[0].action_id = draft.allowed_actions[0].id;
    draft.requirements.push({ id: 'missing', description: 'Other', expected: 'Works' });
    expect(validateDraft(draft)).toContain('需求 missing 没有计划覆盖。');
    if (draft.allowed_actions[0].kind === 'http') draft.allowed_actions[0].path = '//other-host/x';
    expect(validateDraft(draft).some((value) => value.includes('相对路径'))).toBe(true);
  });
  it('only accepts a complete server-ready plan and preserves the current draft on failure', () => {
    const draft = configured(), before = draftFingerprint(draft);
    expect(() => generatedDraft(draft, { status: 'needs_protocol', model_used: true, summary: '缺少协议' })).toThrow('缺少协议');
    expect(draftFingerprint(draft)).toBe(before);
    expect(() => generatedDraft(draft, { ...planning(draft), model_used: false })).toThrow();
    const generated = generatedDraft(draft, planning(draft));
    expect(generated.planning_id).toBe('a'.repeat(32));
    expect(draft.planning_id).toBeUndefined();
  });
});

describe('draft invalidation and operation races', () => {
  it('invalidates a source on device or contract changes and leaves the old record intact', () => {
    const original = { ...configured(), planning_id: 'a'.repeat(32) };
    const proposed = structuredClone(original);
    if (proposed.device.kind === 'http') proposed.device.base_url = 'http://192.168.1.3';
    const changed = transitionDraft(original, proposed);
    expect(changed.protocolChanged).toBe(true);
    expect(changed.draft.planning_id).toBeUndefined();
    expect(original.planning_id).toBe('a'.repeat(32));
    expect(transitionDraft(original, structuredClone(original)).changed).toBe(false);
    expect(transitionDraft(original, structuredClone(original)).draft.planning_id).toBe(original.planning_id);
  });
  it('does not let a direct state update invent another planning ID', () => {
    const draft = configured();
    expect(transitionDraft(draft, { ...draft, planning_id: 'f'.repeat(32) }).draft.planning_id).toBeUndefined();
    expect(transitionDraft(draft, { ...draft, planning_id: 'f'.repeat(32) }, true).draft.planning_id).toBe('f'.repeat(32));
  });
  it('blocks double clicks synchronously before React can rerender', () => {
    const gate = new OperationGate(), snapshot = operationFingerprint(configured(), 0);
    const first = gate.begin('running', snapshot);
    expect(() => gate.begin('running', snapshot)).toThrow('已有操作');
    gate.release(first);
    expect(gate.begin('running', snapshot).id).not.toBe(first.id);
  });
  it('rejects late responses after edits, even when A → B → A restores identical JSON', () => {
    const gate = new OperationGate(), original = configured();
    const ticket = gate.begin('planning', operationFingerprint(original, 0));
    const changed = structuredClone(original); changed.requirements[0].expected = 'Other';
    expect(gate.accepts(ticket, operationFingerprint(changed, 1))).toBe(false);
    expect(gate.accepts(ticket, operationFingerprint(original, 2))).toBe(false);
    expect(gate.accepts(ticket, operationFingerprint(original, 0))).toBe(true);
  });
  it('keeps the request guard until cancellation actually settles', () => {
    const gate = new OperationGate(); const ticket = gate.begin('running', 'snapshot');
    gate.cancel();
    expect(ticket.controller.signal.aborted).toBe(true);
    expect(gate.busy).toBe(true);
    expect(() => gate.begin('planning', 'other')).toThrow();
    gate.release(ticket);
    const newer = gate.begin('validating', 'new');
    gate.release(ticket);
    expect(gate.accepts(newer, 'new')).toBe(true);
  });
  it('canonical comparison preserves JSON types and rejects non-finite values', () => {
    expect(canonical({ b: 2, a: 1 })).toBe(canonical({ a: 1, b: 2 }));
    expect(canonical({ nested: true })).not.toBe(canonical({ nested: 1 }));
    expect(() => canonical({ bad: Number.NaN })).toThrow('NaN');
  });
});

describe('bounded requests without silent retries', () => {
  it('surfaces server contract failures without retrying', async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify({ detail: [{ loc: ['body', 'plan'], msg: '合同被修改' }] }), { status: 422 }));
    await expect(requestHardware('/api/hardware/run', configured(), { fetcher })).rejects.toThrow('合同被修改');
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it('reports non-JSON failures rather than accepting an incomplete receipt', async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response('<h1>unavailable</h1>', { status: 503 }));
    await expect(requestHardware('/api/hardware/capabilities', undefined, { fetcher })).rejects.toThrow('有效 JSON');
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it('forwards external cancellation and does not issue another request', async () => {
    const controller = new AbortController();
    const fetcher = vi.fn<typeof fetch>().mockImplementation((_url, init) => new Promise((_resolve, reject) => {
      init!.signal!.addEventListener('abort', () => reject(init!.signal!.reason), { once: true });
    }));
    const pending = requestHardware('/api/hardware/run', configured(), { fetcher, signal: controller.signal });
    const rejected = expect(pending).rejects.toMatchObject({ name: 'AbortError' });
    controller.abort(); await rejected;
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it('uses an owned timeout and never reports a device success on timeout', async () => {
    vi.useFakeTimers();
    const fetcher = vi.fn<typeof fetch>().mockImplementation((_url, init) => new Promise((_resolve, reject) => {
      init!.signal!.addEventListener('abort', () => reject(init!.signal!.reason), { once: true });
    }));
    const pending = requestHardware('/api/hardware/run', configured(), { fetcher, timeoutMs: 1000 });
    const rejected = expect(pending).rejects.toMatchObject({ name: 'TimeoutError' });
    await vi.advanceTimersByTimeAsync(1000); await rejected;
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
});
