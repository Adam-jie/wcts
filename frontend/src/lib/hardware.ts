import type { AllowedAction, Assertion, Device, HardwareDraft, HardwareOperation, HardwareReport,
  JsonValue, PlanningResponse, Requirement, TestPlan, TransportKind } from '../types';

const ID = /^[A-Za-z][A-Za-z0-9_.-]{0,63}$/;
const PLANNING_ID = /^[a-f0-9]{32}$/;
const KINDS = ['http', 'ssh', 'serial'];
const ASSERTIONS = ['status_code', 'exit_code', 'text_contains', 'text_equals', 'hex_equals', 'json_equals'];
const record = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === 'object' && !Array.isArray(value);
const clone = <T,>(value: T): T => structuredClone(value);

export function canonical(value: unknown): string {
  if (value === null) return 'null';
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`;
  if (record(value)) return `{${Object.keys(value).filter((key) => value[key] !== undefined).sort().map((key) => `${JSON.stringify(key)}:${canonical(value[key])}`).join(',')}}`;
  if (typeof value === 'number' && !Number.isFinite(value)) throw new Error('JSON 不能包含 NaN 或 Infinity。');
  const result = JSON.stringify(value);
  if (result === undefined) throw new Error('配置包含无法序列化的值。');
  return result;
}

function keys(value: Record<string, unknown>, allowed: string[], label: string) {
  if (Object.keys(value).some((key) => !allowed.includes(key))) throw new Error(`${label}包含未支持的字段，请检查格式。`);
}
function text(value: unknown, label: string): string {
  if (typeof value !== 'string') throw new Error(`${label}应为文本。`);
  return value;
}
function finite(value: unknown, fallback: number, label: string): number {
  if (value === undefined) return fallback;
  if (typeof value !== 'number' || !Number.isFinite(value)) throw new Error(`${label}应为有效数字。`);
  return value;
}
function bool(value: unknown, fallback: boolean, label: string): boolean {
  if (value === undefined) return fallback;
  if (typeof value !== 'boolean') throw new Error(`${label}应为 true 或 false。`);
  return value;
}
function headers(value: unknown): Record<string, string> {
  if (value === undefined) return {};
  if (!record(value) || Object.values(value).some((item) => typeof item !== 'string')) throw new Error('HTTP 请求头应为文本值对象。');
  return Object.fromEntries(Object.entries(value)) as Record<string, string>;
}
export function parseJson(textValue: string, label = '内容'): unknown {
  try { return JSON.parse(textValue); } catch { throw new Error(`${label}不是有效 JSON，请检查引号、逗号和括号。`); }
}
function deviceFrom(value: unknown): Device {
  if (!record(value) || !KINDS.includes(String(value.kind))) throw new Error('设备接口应为 http、ssh 或 serial。');
  const name = value.name === undefined ? (value.kind === 'serial' ? 'MCU' : 'MPU') : text(value.name, '设备名称');
  if (value.kind === 'http') {
    keys(value, ['kind', 'name', 'base_url', 'headers', 'verify_tls'], 'HTTP 设备');
    return { kind: 'http', name, base_url: text(value.base_url ?? '', '设备 URL'), headers: headers(value.headers), verify_tls: bool(value.verify_tls, true, 'TLS 校验') };
  }
  if (value.kind === 'serial') {
    keys(value, ['kind', 'name', 'port', 'baudrate', 'dtr', 'rts'], '串口设备');
    return { kind: 'serial', name, port: text(value.port ?? '', '串口'), baudrate: finite(value.baudrate, 115200, '波特率'), dtr: bool(value.dtr, false, 'DTR'), rts: bool(value.rts, false, 'RTS') };
  }
  keys(value, ['kind', 'name', 'host', 'port', 'username', 'private_key_path', 'known_hosts_path'], 'SSH 设备');
  return { kind: 'ssh', name, host: text(value.host ?? '', 'SSH IP'), port: finite(value.port, 22, 'SSH 端口'),
    username: text(value.username ?? '', 'SSH 用户'), private_key_path: text(value.private_key_path ?? '', '密钥路径'), known_hosts_path: text(value.known_hosts_path ?? '', 'known_hosts 路径') };
}
function assertionFrom(value: unknown): Assertion {
  if (!record(value) || !ASSERTIONS.includes(String(value.kind)) || !Object.hasOwn(value, 'expected')) throw new Error('每条断言需要合法 kind 和 expected。');
  keys(value, ['kind', 'expected', 'path'], '断言');
  canonical(value.expected);
  if (value.path !== undefined && (!Array.isArray(value.path) || value.path.some((part) => typeof part !== 'string' && !(typeof part === 'number' && Number.isInteger(part))))) throw new Error('JSON 路径应为文本或整数数组。');
  return { kind: value.kind as Assertion['kind'], expected: clone(value.expected as JsonValue), path: (value.path || []) as (string | number)[] };
}
function assertionsFrom(value: unknown): Assertion[] {
  if (!Array.isArray(value)) throw new Error('assertions 应为数组。');
  return value.map(assertionFrom);
}
function requirementsFrom(value: unknown): Requirement[] {
  if (!Array.isArray(value)) throw new Error('需求应为数组。');
  return value.map((item) => {
    if (!record(item)) throw new Error('每项需求应为对象。');
    keys(item, ['id', 'description', 'expected', 'category'], '需求');
    if (item.category !== undefined && !['functional', 'communication'].includes(String(item.category))) throw new Error('需求 category 应为 functional 或 communication。');
    return { id: text(item.id, '需求 ID'), description: text(item.description, '需求说明'), expected: text(item.expected, '预期行为'), category: (item.category || 'functional') as Requirement['category'] };
  });
}
function actionsFrom(value: unknown): AllowedAction[] {
  if (!Array.isArray(value)) throw new Error('允许动作应为数组。');
  return value.map((item) => {
    if (!record(item) || !KINDS.includes(String(item.kind))) throw new Error('动作接口应为 http、ssh 或 serial。');
    const common = ['kind', 'id', 'function_id', 'description', 'timeout_s', 'max_bytes', 'assertions'];
    const base = { id: text(item.id, '动作 ID'), function_id: text(item.function_id, '功能 ID'), description: text(item.description, '动作说明'), timeout_s: finite(item.timeout_s, 3, '动作超时'), max_bytes: finite(item.max_bytes, 4096, '回包字节数'), assertions: assertionsFrom(item.assertions ?? []) };
    if (item.kind === 'http') {
      keys(item, [...common, 'method', 'path', 'json_body', 'headers'], 'HTTP 动作');
      const method = item.method ?? 'GET';
      if (!['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD'].includes(String(method))) throw new Error('HTTP method 不受支持。');
      canonical(item.json_body ?? null);
      return { ...base, kind: 'http', method: method as 'GET', path: text(item.path, 'HTTP 路径'), json_body: (item.json_body ?? null) as JsonValue, headers: headers(item.headers) };
    }
    if (item.kind === 'ssh') {
      keys(item, [...common, 'command'], 'SSH 动作');
      return { ...base, kind: 'ssh', command: text(item.command, '固定 SSH 命令') };
    }
    keys(item, [...common, 'write_text', 'write_hex', 'read_until', 'read_until_hex'], '串口动作');
    const serial: Record<string, string | null | undefined> = {};
    for (const key of ['write_text', 'write_hex', 'read_until', 'read_until_hex']) {
      if (item[key] !== undefined && item[key] !== null) serial[key] = text(item[key], key);
    }
    return { ...base, kind: 'serial', ...serial };
  });
}
function planFrom(value: unknown): TestPlan | null {
  if (value === null || value === undefined) return null;
  if (!record(value) || !Array.isArray(value.steps)) throw new Error('计划需要 steps 数组。');
  keys(value, ['steps'], '计划');
  return { steps: value.steps.map((step) => {
    if (!record(step)) throw new Error('计划步骤应为对象。');
    keys(step, ['id', 'function_id', 'action_id', 'assertions'], '计划步骤');
    return { id: text(step.id, '步骤 ID'), function_id: text(step.function_id, '功能 ID'), action_id: text(step.action_id, '动作引用'), assertions: assertionsFrom(step.assertions) };
  }) };
}

export function parseRequirements(textValue: string): Requirement[] { return requirementsFrom(parseJson(textValue, '需求')); }
export function parseActions(textValue: string): AllowedAction[] { return actionsFrom(parseJson(textValue, '允许动作')); }
export function parsePlan(textValue: string): TestPlan | null { return planFrom(parseJson(textValue, '计划')); }
export function parseProfile(textValue: string): HardwareDraft {
  const value = parseJson(textValue, '设备档案');
  if (!record(value)) throw new Error('设备档案应为 JSON 对象。');
  keys(value, ['device', 'requirements', 'allowed_actions', 'plan', 'planning_id', 'max_duration_s'], '设备档案');
  if (value.planning_id !== undefined && (typeof value.planning_id !== 'string' || !PLANNING_ID.test(value.planning_id))) throw new Error('planning_id 应为 32 位小写十六进制记录 ID。');
  return { device: deviceFrom(value.device), requirements: requirementsFrom(value.requirements), allowed_actions: actionsFrom(value.allowed_actions), plan: planFrom(value.plan), max_duration_s: finite(value.max_duration_s, 60, '总超时'), ...(value.planning_id ? { planning_id: value.planning_id as string } : {}) };
}
export function exportProfile(draft: HardwareDraft): string { return JSON.stringify(draft, null, 2); }

function normalizedAssertions(assertions: Assertion[]) {
  return assertions.map((assertion) => ({ ...assertion, path: assertion.path ?? [], expected: assertion.kind === 'hex_equals' && typeof assertion.expected === 'string' ? assertion.expected.replace(/\s/g, '').toLowerCase() : assertion.expected }));
}
export function protocolFingerprint(draft: HardwareDraft): string {
  return canonical({ device: draft.device, requirements: draft.requirements, allowed_actions: draft.allowed_actions, max_duration_s: draft.max_duration_s });
}
export function draftFingerprint(draft: HardwareDraft): string { return canonical(draft); }
export function operationFingerprint(draft: HardwareDraft, revision: number): string {
  return canonical({ revision, draft });
}
export function transitionDraft(previous: HardwareDraft, proposed: HardwareDraft, preservePlanningId = false) {
  const next = clone(proposed);
  const protocolChanged = protocolFingerprint(previous) !== protocolFingerprint(next);
  const planChanged = canonical(previous.plan) !== canonical(next.plan);
  if (!preservePlanningId && (protocolChanged || planChanged || previous.planning_id !== next.planning_id)) delete next.planning_id;
  return { draft: next, protocolChanged, planChanged, changed: draftFingerprint(previous) !== draftFingerprint(next) };
}

export function validateDraft(draft: HardwareDraft, requirePlan = true): string[] {
  const errors: string[] = [];
  const { device, requirements, allowed_actions: actions, plan } = draft;
  if (device.kind === 'http') {
    try {
      const url = new URL(device.base_url);
      if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash || !['', '/'].includes(url.pathname)) errors.push('设备 URL 应为 http(s)://IP[:端口]，不含账号或路径。');
    } catch { errors.push('请填写设备 URL。'); }
  } else if (device.kind === 'serial') {
    if (!device.port || !(device.port === 'loop://' || /^(COM[1-9]\d{0,3}|\/dev\/(tty|cu)[A-Za-z0-9_.-]{1,80})$/i.test(device.port))) errors.push('请填写 COM/tty/cu 串口；模拟请使用 loop://。');
    if (!Number.isInteger(device.baudrate ?? 115200) || (device.baudrate ?? 115200) < 50 || (device.baudrate ?? 115200) > 4000000) errors.push('波特率应为 50～4000000 的整数。');
  } else if (!device.host || !device.username || !device.private_key_path || !device.known_hosts_path) errors.push('请填写 SSH IP、用户、私钥及 known_hosts 路径。');
  if (draft.max_duration_s < 1 || draft.max_duration_s > 120 || !Number.isFinite(draft.max_duration_s)) errors.push('总时间预算应为 1～120 秒。');
  if (!requirements.length || requirements.length > 32) errors.push('请声明 1～32 项需求。');
  if (!actions.length || actions.length > 32) errors.push('请声明 1～32 个允许动作。');
  const requirementIds = new Set<string>(); const actionMap = new Map<string, AllowedAction>();
  for (const item of requirements) {
    if (!ID.test(item.id) || requirementIds.has(item.id)) errors.push('需求 ID 应合法且唯一。');
    requirementIds.add(item.id);
    if (!item.description.trim() || !item.expected.trim()) errors.push('每项需求都需要说明及预期行为。');
  }
  for (const action of actions) {
    if (!ID.test(action.id) || actionMap.has(action.id)) errors.push('动作 ID 应合法且唯一。');
    actionMap.set(action.id, action);
    if (action.kind !== device.kind || !requirementIds.has(action.function_id)) errors.push('动作必须绑定当前接口和已声明功能。');
    if (!action.description.trim()) errors.push('每个动作需要说明。');
    if ((action.timeout_s ?? 3) < .1 || (action.timeout_s ?? 3) > 10) errors.push('单动作超时应为 0.1～10 秒。');
    if ((action.max_bytes ?? 4096) < 1 || (action.max_bytes ?? 4096) > 65536) errors.push('回包上限应为 1～65536 字节。');
    if (action.kind === 'http' && (!action.path.startsWith('/') || action.path.startsWith('//') || /[\\\r\n]/.test(action.path))) errors.push('HTTP 动作只能使用设备内相对路径。');
    if (action.kind === 'ssh' && !action.command.trim()) errors.push('SSH 动作需要用户明确声明的固定命令。');
    if (action.kind === 'serial' && ((action.write_text == null) === (action.write_hex == null))) errors.push('串口动作需要且只能选择 write_text 或 write_hex。');
  }
  if (requirePlan) {
    if (!plan?.steps.length || plan.steps.length > 32) errors.push('请先生成或编辑包含 1～32 个步骤的计划。');
    const ids = new Set<string>(); const covered = new Set<string>();
    for (const step of plan?.steps ?? []) {
      if (!ID.test(step.id) || ids.has(step.id)) errors.push('步骤 ID 应合法且唯一。');
      ids.add(step.id);
      const action = actionMap.get(step.action_id);
      if (!action || step.function_id !== action.function_id) { errors.push('步骤只能引用同一功能下的允许动作。'); continue; }
      covered.add(step.function_id);
      if (canonical(normalizedAssertions(step.assertions)) !== canonical(normalizedAssertions(action.assertions))) errors.push('步骤断言必须完整保留用户动作的协议合同，不能放宽预期。');
    }
    for (const item of requirements) if (!covered.has(item.id)) errors.push(`需求 ${item.id} 没有计划覆盖。`);
  }
  return [...new Set(errors)];
}

export function generatedDraft(draft: HardwareDraft, response: PlanningResponse): HardwareDraft {
  if (response.status !== 'ready' || response.model_used !== true || !response.planning_id || !PLANNING_ID.test(response.planning_id) || !response.plan) throw new Error(response.summary || '服务未返回有记录的完整模型计划，已有计划保留。');
  const next = { ...clone(draft), plan: planFrom(response.plan), planning_id: response.planning_id };
  const errors = validateDraft(next);
  if (errors.length) throw new Error(errors.join('；'));
  return next;
}

export interface OperationTicket { id: number; operation: HardwareOperation; snapshot: string; controller: AbortController; }
export class OperationGate {
  private current: OperationTicket | null = null;
  private sequence = 0;
  begin(operation: HardwareOperation, snapshot: string): OperationTicket {
    if (this.current) throw new Error('已有操作正在进行，请等待完成。');
    const ticket = { id: ++this.sequence, operation, snapshot, controller: new AbortController() };
    this.current = ticket; return ticket;
  }
  accepts(ticket: OperationTicket, snapshot: string): boolean { return this.current?.id === ticket.id && ticket.snapshot === snapshot; }
  release(ticket: OperationTicket): void { if (this.current?.id === ticket.id) this.current = null; }
  cancel(): void { this.current?.controller.abort(); }
  get busy(): boolean { return this.current !== null; }
}

function serviceError(value: unknown, status: number): string {
  if (!record(value)) return `本地服务返回 ${status}。`;
  const detail = value.detail ?? value.error;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) return detail.map((item: unknown) => record(item) ? `${Array.isArray(item.loc) ? item.loc.filter((part) => part !== 'body').join('.') : ''}：${String(item.msg ?? '输入无效')}` : '输入无效').join('；');
  if (record(detail) && typeof detail.message === 'string') return detail.message;
  return `本地服务返回 ${status}。`;
}
export function errorMessage(error: unknown): string {
  if (error instanceof DOMException && ['AbortError', 'TimeoutError'].includes(error.name)) return '已停止等待完整回执；服务端任务可能仍在执行，本页不会自动重试设备动作。';
  return error instanceof Error ? error.message : '本地服务暂时不可用。';
}
export async function requestHardware<T>(path: string, payload?: unknown, options: { signal?: AbortSignal; timeoutMs?: number; fetcher?: typeof fetch } = {}): Promise<T> {
  const controller = new AbortController();
  const relay = () => controller.abort(options.signal?.reason);
  options.signal?.addEventListener('abort', relay, { once: true });
  if (options.signal?.aborted) relay();
  const timer = setTimeout(() => controller.abort(new DOMException('请求等待超时', 'TimeoutError')), options.timeoutMs ?? 30000);
  try {
    const response = await (options.fetcher ?? fetch)(path, { method: payload === undefined ? 'GET' : 'POST', signal: controller.signal,
      headers: { Accept: 'application/json', ...(payload === undefined ? {} : { 'Content-Type': 'application/json' }) },
      ...(payload === undefined ? {} : { body: JSON.stringify(payload) }) });
    const textValue = await response.text();
    let value: unknown;
    try { value = JSON.parse(textValue); } catch { throw new Error(`本地服务未返回有效 JSON（${response.status}），请检查服务状态。`); }
    if (!response.ok) throw new Error(serviceError(value, response.status));
    return value as T;
  } finally { clearTimeout(timer); options.signal?.removeEventListener('abort', relay); }
}
export function isHardwareReport(value: unknown): value is HardwareReport {
  return record(value) && typeof value.run_id === 'string' && typeof value.status === 'string'
    && typeof value.success === 'boolean' && typeof value.model_used === 'boolean'
    && typeof value.is_simulation === 'boolean' && typeof value.functional_verified === 'boolean'
    && Array.isArray(value.steps) && Array.isArray(value.coverage);
}

export function makeExample(kind: TransportKind = 'http'): HardwareDraft {
  const requirement: Requirement = { id: 'feature-1', category: 'functional', description: '', expected: '' };
  let device: Device; let action: AllowedAction;
  if (kind === 'serial') {
    device = { kind, name: 'MCU', port: '', baudrate: 115200, dtr: false, rts: false };
    Object.assign(requirement, { description: '按固件协议读取版本，确认返回 1.0.0。', expected: '回包包含 VERSION:1.0.0' });
    action = { kind, id: 'read-version', function_id: requirement.id, description: '读取 MCU 固件版本', write_text: 'VERSION\n', read_until: '\n', timeout_s: 3, max_bytes: 4096, assertions: [{ kind: 'text_contains', expected: 'VERSION:1.0.0' }] };
  } else if (kind === 'ssh') {
    device = { kind, name: 'MPU', host: '', port: 22, username: '', private_key_path: '', known_hosts_path: '' };
    Object.assign(requirement, { description: '读取设备内核类型，确认返回 Linux。', expected: '输出包含 Linux' });
    action = { kind, id: 'read-system', function_id: requirement.id, description: '读取设备内核类型', command: 'uname -s', timeout_s: 3, max_bytes: 4096, assertions: [{ kind: 'exit_code', expected: 0 }, { kind: 'text_contains', expected: 'Linux' }] };
  } else {
    device = { kind, name: 'MPU', base_url: '', headers: {}, verify_tls: true };
    Object.assign(requirement, { description: '读取设备固件版本，确认返回 1.0.0。', expected: 'version = 1.0.0' });
    action = { kind, id: 'read-version', function_id: requirement.id, description: '读取固件版本接口', method: 'GET', path: '/api/version', timeout_s: 3, max_bytes: 4096, assertions: [{ kind: 'status_code', expected: 200 }, { kind: 'json_equals', path: ['version'], expected: '1.0.0' }] };
  }
  return { device, requirements: [requirement], allowed_actions: [action], plan: { steps: [{ id: 'step-1', function_id: requirement.id, action_id: action.id, assertions: clone(action.assertions) }] }, max_duration_s: 60 };
}
export function targetLabel(device: Device): string { return device.kind === 'http' ? device.base_url || '未填写设备 IP' : device.kind === 'ssh' ? device.host || '未填写 SSH IP' : device.port || '未填写串口'; }
export function actionLabel(action: AllowedAction): string { return action.kind === 'http' ? `${action.method || 'GET'} ${action.path}` : action.kind === 'ssh' ? action.command : action.write_text != null ? `发送 ${JSON.stringify(action.write_text)}` : `发送 HEX ${action.write_hex || ''}`; }
export function assertionLabel(assertion: Assertion): string {
  const value = JSON.stringify(assertion.expected), path = assertion.path?.length ? ` ${assertion.path.join('.')}` : '';
  return ({ status_code: `HTTP 状态码 = ${value}`, exit_code: `退出码 = ${value}`, text_contains: `回包包含 ${value}`, text_equals: `回包等于 ${value}`, hex_equals: `HEX 回包 = ${value}`, json_equals: `JSON${path} = ${value}` })[assertion.kind];
}
