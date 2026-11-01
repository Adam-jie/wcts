import type { Assertion, Device, HardwareReport, AllowedAction } from '../types';

export function targetOf(device: Device): string {
  return device.kind === 'http' ? device.base_url : device.kind === 'ssh' ? device.host : device.port;
}

export function kindLabel(kind: Device['kind']) { return { http: 'HTTP 接口', ssh: 'SSH 命令', serial: '串口协议' }[kind]; }

export function actionText(action: AllowedAction): string {
  return action.kind === 'http' ? `${action.method || 'GET'} ${action.path}` : action.kind === 'ssh' ? action.command : action.write_text !== undefined && action.write_text !== null ? `发送 ${JSON.stringify(action.write_text)}` : `HEX ${action.write_hex || ''}`;
}

export function assertionText(assertion: Assertion): string {
  const expected = typeof assertion.expected === 'string' ? assertion.expected : JSON.stringify(assertion.expected);
  return ({ status_code: `HTTP 状态码 = ${expected}`, exit_code: `退出码 = ${expected}`, text_contains: `回包包含「${expected}」`, text_equals: `回包等于「${expected}」`, hex_equals: `HEX = ${expected}`, json_equals: `${assertion.path?.join('.') || 'JSON'} = ${expected}` })[assertion.kind];
}

export function connectionSummary(report: HardwareReport | null, stale: boolean): { label: string; tone: 'neutral' | 'warning' | 'success' | 'danger' } {
  if (!report || stale) return { label: '设备未验证', tone: 'neutral' };
  const passed = report.steps.filter(step => step.communication_passed || step.actual?.communication_passed).length;
  if (report.is_simulation) return { label: passed ? '模拟链路已通' : '模拟链路未通', tone: 'warning' };
  return passed > 0 && passed === report.steps.length ? { label: '通信已验证', tone: 'success' } : { label: passed > 0 ? '部分通信失败' : '通信未通过', tone: 'danger' };
}

export function functionSummary(report: HardwareReport | null, stale: boolean): { label: string; tone: 'neutral' | 'warning' | 'success' | 'danger' } {
  if (!report || stale) return { label: stale ? '配置已变更，待重测' : '功能待执行', tone: 'neutral' };
  if (report.is_simulation) return { label: '模拟结果 · 非固件验收', tone: 'warning' };
  if (!report.coverage.some(item => item.category === 'functional')) return { label: '未覆盖功能行为', tone: 'neutral' };
  return report.functional_verified ? { label: '功能断言通过', tone: 'success' } : { label: '功能断言未通过', tone: 'danger' };
}
