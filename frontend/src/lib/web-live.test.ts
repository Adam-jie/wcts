import { describe, expect, it } from 'vitest';
import { embeddedPreviewTarget, modelEvidence, parseWebLive, previewTarget, testCounts } from './web-live';

const snapshot = () => ({ updated_at: '2026-10-16T00:00:00Z', projects: [{
  id: 'uptime-kuma', title: 'Uptime Kuma', source_url: 'https://github.com/louislam/uptime-kuma',
  reference_url: 'http://127.0.0.1:8770', candidate_url: 'http://127.0.0.1:8771', source_commit: 'abc',
  phase: '构建中', status: 'running', feature: 'test feature', updated_at: '',
  stages: [{ id: 'build', label: '构建', status: 'running', detail: '<script>untrusted</script>' }],
  tests: [{ id: 'regression', label: '回归', status: 'pending', detail: '' }], notes: [],
  model: { requested_model: 'requested', actual_model: null, completed_calls: 0 }, readiness: { reference: true, candidate: false },
  embedding: { reference: true, candidate: true },
}] });
describe('live web evidence boundaries', () => {
  it('keeps failed and incomplete tests out of passed counts', () => {
    expect(testCounts(['passed', 'failed', 'blocked', 'pending', 'running', 'skipped', 'completed'].map((status, i) => ({ id: String(i), label: '', detail: '', status })) as Parameters<typeof testCounts>[0])).toEqual({ passed: 1, failed: 1, unfinished: 5, total: 7 });
  });
  it('enables only probed fixed local preview addresses', () => {
    const project = parseWebLive(snapshot()).projects[0];
    expect(previewTarget(project, 'reference')).toBe('http://127.0.0.1:8770');
    expect(previewTarget(project, 'candidate')).toBeNull();
    expect(embeddedPreviewTarget(project, 'candidate')).toBeNull();
    expect(project.model.actual_model).toBeNull();
  });
  it.each(['https://attacker.example/', 'http://127.0.0.1:8770@attacker.example', 'javascript:alert(1)', 'http://127.0.0.1:8765/api/hardware/run'])('rejects untrusted iframe address %s', target => {
    const value = snapshot(); value.projects[0].candidate_url = target;
    expect(() => parseWebLive(value)).toThrow('本地实验范围');
  });
  it('rejects fabricated project IDs and statuses', () => {
    const value = snapshot(); value.projects[0].id = '__proto__';
    expect(() => parseWebLive(value)).toThrow('项目标识');
    const invalid = snapshot(); invalid.projects[0].tests[0].status = 'everything-pass';
    expect(() => parseWebLive(invalid)).toThrow('执行证据');
  });
  it('rejects duplicate projects and nonboolean readiness', () => {
    const value = snapshot(); value.projects.push(value.projects[0]);
    expect(() => parseWebLive(value)).toThrow('项目标识');
    expect(() => parseWebLive({ ...snapshot(), projects: [{ ...snapshot().projects[0], readiness: { reference: 'true', candidate: false } }] })).toThrow('项目状态');
  });
  it('preserves untrusted log-like detail as text for escaped React rendering', () => {
    expect(parseWebLive(snapshot()).projects[0].stages[0].detail).toBe('<script>untrusted</script>');
  });
  it('keeps completed model calls distinct from an unreported model identity', () => {
    const value = snapshot(); value.projects[0].model.completed_calls = 1;
    const model = parseWebLive(value).projects[0].model;
    expect(model.actual_model).toBeNull();
    expect(model.completed_calls).toBe(1);
    expect(modelEvidence(model)).toEqual({ calls: '已完成 1 次调用', identity: '服务端未报告型号' });
  });
  it('keeps a running application independently accessible when its security policy forbids embedding', () => {
    const value = snapshot(); value.projects[0].embedding.reference = false;
    const project = parseWebLive(value).projects[0];
    expect(previewTarget(project, 'reference')).toBe('http://127.0.0.1:8770');
    expect(embeddedPreviewTarget(project, 'reference')).toBeNull();
  });
  it.each([-1, .5, '1', null, true])('rejects invalid completed model call count %s', completed_calls => {
    expect(() => parseWebLive({ ...snapshot(), projects: [{ ...snapshot().projects[0], model: { completed_calls } }] })).toThrow('模型调用证据');
  });
});
